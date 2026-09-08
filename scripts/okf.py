#!/usr/bin/env python3
"""okf.py — deep-research OKF v0.2-style bundle writer and validator.

Implements and enforces `references/okf-bundle.md`. The bundle lives at
`<wiki>/research/`; the shared PDF library at `<wiki>/assets/papers/`.
`<wiki>/wiki/` belongs to wiki-manager (OKF 0.1) and is NEVER touched.

Subcommands
-----------
  init      create <wiki>/research/ with index.md, log.md and every taxonomy dir
  write     write one concept from a JSON input file (corpus record or concept spec)
  promote   promote a completed run directory into the bundle; `--check` runs the
            publisher integrity preflight (snapshot hashes, re-sliced excerpts, paper
            identifiers, PDF asset digests) and every V-rule without writing anything
  validate  enforce V1..V25; non-zero exit on any violation
  selftest  round-trip the built-in YAML serializer/parser

Stdlib only. PyYAML is NOT installed on this machine, so the YAML frontmatter
subset used by the spec is emitted and parsed by this module (see `yaml_dump` /
`yaml_load_frontmatter`). No pip installs, ever.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

# `store.py` is the single owner of snapshot hashing, span checking and freshness
# (VALIDATION_ARCHITECTURE_PLAN.md decision D5). It is never reimplemented here.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
try:
    import store as _store
except Exception:  # pragma: no cover - store.py missing is itself reported
    _store = None

OKF_VERSION = "0.2"
BUNDLE = "deep-research"
TOOL_VERSION = "0.1"
GENERATED_BY = f"deep-research/{TOOL_VERSION}"
VERIFIED_BY = "process:deep-research-verifier"

STATUSES = ("draft", "stable", "provisional", "superseded")
RETRACTION_STATUSES = ("none", "retracted", "expression_of_concern", "corrected")

# taxonomy: type -> directory (okf-bundle.md §1)
TYPE_DIRS = {
    "Review": "reviews",
    "Protocol": "protocols",
    "Search Strategy": "searches",
    "Study": "studies",
    "Evidence Claim": "claims",
    "Outcome": "outcomes",
    "Population": "populations",
    "Intervention": "interventions",
    "Comparator": "comparators",
    "Appraisal": "appraisals",
    "Evidence Gap": "gaps",
    "Hypothesis": "hypotheses",
    "Source Document": "source-documents",
}
DIR_TYPES = {v: k for k, v in TYPE_DIRS.items()}
TYPE_ALIASES = {t.lower().replace(" ", "-"): t for t in TYPE_DIRS}
TYPE_ALIASES.update({t.lower(): t for t in TYPE_DIRS})

STALE_REQUIRED_TYPES = ("Review", "Search Strategy", "Evidence Claim")
PUBMED_TYPES = ("Study",)
RETRACTION_TYPES = ("Study", "Source Document")

REQUIRED_FIELDS = ("type", "title", "description", "resource", "tags",
                   "generated", "sources", "verified", "status")

# §3 bibliographic fields, in emission order
BIBLIO_FIELDS = ("pmid", "doi", "pmcid", "authors", "journal", "publication_date",
                 "epub_date", "volume", "issue", "pages", "abstract", "article_types",
                 "mesh_terms", "keywords", "grants", "publication_status",
                 "retraction_status")
# corpus-record key -> concept frontmatter key, for V12
CORPUS_BIBLIO_MAP = {
    "pmid": "pmid", "doi": "doi", "pmcid": "pmcid", "journal": "journal",
    "publication_date": "publication_date", "authors": "authors",
    "article_types": "article_types", "mesh_terms": "mesh_terms",
    "keywords": "keywords", "retraction_status": "retraction_status",
}

FRONTMATTER_ORDER = (
    "okf_version", "bundle", "type", "title", "description", "resource", "tags",
    "generated", "verified", "status", "stale_after",
    "evidence_id", "evidence_ids", "evidence_basis", "run_slug",
) + BIBLIO_FIELDS + (
    "is_preprint", "accessed_at", "publisher", "document_type",
    "tool", "overall_judgement", "certainty", "sources",
)

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PMID_RE = re.compile(r"^\d+$")
PMCID_RE = re.compile(r"^PMC\d+$")
DOI_RE = re.compile(r"^10\.\d{4,}/\S+$")
LOG_HEADING_RE = re.compile(r"^##\s+(.*)$")
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:", re.M)
FOOTNOTE_REF_RE = re.compile(r"(?<!\])\[\^([^\]\s]+)\]")
MD_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


# --------------------------------------------------------------- errors --------


class OkfError(Exception):
    """Fatal, user-facing error."""


class FenceError(OkfError):
    """A write was attempted outside the deep-research write fence."""


# ------------------------------------------------------------ yaml subset ------
#
# Supported subset (everything okf-bundle.md actually uses):
#   * block mappings, arbitrarily nested
#   * block sequences of scalars and of mappings
#   * flow sequences of scalars  [a, b, c]  and flow mappings  { k: v, k: v }
#   * scalars: plain / double-quoted / single-quoted strings, ints, floats,
#     booleans, null (`null` and `~`), ISO dates (YYYY-MM-DD)
# NOT supported: anchors/aliases, tags, multi-document streams, complex keys,
# block scalars on output (`|`, `>` are *parsed* but never emitted), flow
# collections nested inside flow collections beyond one level.

FLOW_MAP_KEYS = {"generated", "verified"}
FORCE_QUOTE_KEYS = {"pmid", "doi", "pmcid", "volume", "issue", "pages", "issn",
                    "sha256", "zip", "issn_linking"}
FORCE_PLAIN_KEYS = {"at", "publication_date", "epub_date", "stale_after",
                    "accessed_at", "created_at", "updated_at", "executed_at"}
_PLAIN_SAFE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+/-]*$")
_NUMBERISH_RE = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")
_RESERVED_PLAIN = {"null", "~", "true", "false", "yes", "no", "on", "off",
                   "True", "False", "Null", "NULL", "TRUE", "FALSE"}
_SPECIAL_LEAD = set("-?:,[]{}#&*!|>'\"%@`")


def _quote(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _needs_quote(s: str, flow: bool) -> bool:
    if s == "" or s != s.strip():
        return True
    if s in _RESERVED_PLAIN or _NUMBERISH_RE.match(s) or DATE_RE.match(s):
        return True
    if s[0] in _SPECIAL_LEAD:
        return True
    if "\n" in s or "\t" in s or ": " in s or " #" in s or s.endswith(":"):
        return True
    if flow and any(c in s for c in ",[]{}"):
        return True
    return False


def _scalar(value, key: str | None = None, flow: bool = False) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, _dt.datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, _dt.date):
        return value.isoformat()
    s = str(value)
    if key in FORCE_QUOTE_KEYS:
        return _quote(s)
    if key in FORCE_PLAIN_KEYS and _PLAIN_SAFE_RE.match(s) and not flow:
        return s
    if key in FORCE_PLAIN_KEYS and _PLAIN_SAFE_RE.match(s) and flow:
        return s
    return _quote(s) if _needs_quote(s, flow) else s


def _is_scalar(v) -> bool:
    return v is None or isinstance(v, (str, int, float, bool, _dt.date, _dt.datetime))


def _flow_map(d: dict) -> str:
    return "{ " + ", ".join(f"{k}: {_scalar(v, k, flow=True)}" for k, v in d.items()) + " }"


def _flow_seq(items: list, key: str | None = None) -> str:
    return "[" + ", ".join(_scalar(v, key, flow=True) for v in items) + "]"


def _emit_map(d: dict, indent: int, out: list[str]) -> None:
    sp = " " * indent
    for k, v in d.items():
        key = k if _PLAIN_SAFE_RE.match(str(k)) else _quote(str(k))
        if isinstance(v, dict):
            if not v:
                out.append(f"{sp}{key}: {{}}")
            elif k in FLOW_MAP_KEYS and all(_is_scalar(x) for x in v.values()):
                out.append(f"{sp}{key}: {_flow_map(v)}")
            else:
                out.append(f"{sp}{key}:")
                _emit_map(v, indent + 2, out)
        elif isinstance(v, list):
            if not v:
                out.append(f"{sp}{key}: []")
            elif all(_is_scalar(x) for x in v):
                out.append(f"{sp}{key}: {_flow_seq(v, k)}")
            else:
                out.append(f"{sp}{key}:")
                for item in v:
                    _emit_seq_item(item, indent + 2, out)
        else:
            out.append(f"{sp}{key}: {_scalar(v, k)}")


def _emit_seq_item(item, indent: int, out: list[str]) -> None:
    sp = " " * indent
    if isinstance(item, dict):
        if not item:
            out.append(f"{sp}- {{}}")
            return
        buf: list[str] = []
        _emit_map(item, indent + 2, buf)
        out.append(sp + "- " + buf[0].lstrip())
        out.extend(buf[1:])
    elif isinstance(item, list):
        if all(_is_scalar(x) for x in item):
            out.append(f"{sp}- {_flow_seq(item)}")
        else:
            out.append(f"{sp}-")
            for sub in item:
                _emit_seq_item(sub, indent + 2, out)
    else:
        out.append(f"{sp}- {_scalar(item)}")


def yaml_dump(data: dict) -> str:
    """Serialize a mapping to the YAML subset described above."""
    out: list[str] = []
    _emit_map(data, 0, out)
    return "\n".join(out) + ("\n" if out else "")


# --- parsing ---


def _unescape_double(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", "0": "\0",
                       '"': '"', "\\": "\\", "/": "/"}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
            if nxt == "u" and i + 5 < len(s) + 1:
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _read_quoted(text: str, i: int) -> tuple[str, int]:
    q = text[i]
    i += 1
    buf: list[str] = []
    while i < len(text):
        ch = text[i]
        if q == '"':
            if ch == "\\" and i + 1 < len(text):
                buf.append(ch)
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                return _unescape_double("".join(buf)), i + 1
        else:  # single quoted: '' is a literal '
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                return "".join(buf), i + 1
        buf.append(ch)
        i += 1
    raise OkfError(f"unterminated quoted scalar: {text[:60]!r}")


def _strip_comment(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch in "\"'":
            try:
                _, j = _read_quoted(s, i)
            except OkfError:
                return "".join(out) + s[i:]
            out.append(s[i:j])
            i = j
            continue
        if ch == "#" and (not out or out[-1] in (" ", "\t")):
            break
        out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _split_flow(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch in "\"'":
            _, j = _read_quoted(body, i)
            cur.append(body[i:j])
            i = j
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_scalar(text: str):
    text = text.strip()
    if not text:
        return None
    if text[0] in "\"'":
        value, j = _read_quoted(text, 0)
        rest = text[j:].strip()
        if rest and not rest.startswith("#"):
            raise OkfError(f"trailing content after quoted scalar: {text!r}")
        return value
    if text.startswith("["):
        if not text.endswith("]"):
            raise OkfError(f"unterminated flow sequence: {text!r}")
        return [_parse_scalar(p) for p in _split_flow(text[1:-1])]
    if text.startswith("{"):
        if not text.endswith("}"):
            raise OkfError(f"unterminated flow mapping: {text!r}")
        out = {}
        for part in _split_flow(text[1:-1]):
            if not part:
                continue
            k, v = _split_key_value(part)
            out[k] = _parse_scalar(v) if v is not None else None
        return out
    if text in ("null", "~", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if re.match(r"^[-+]?\d+$", text):
        return int(text)
    if _NUMBERISH_RE.match(text):
        return float(text)
    if DATE_RE.match(text):
        try:
            return _dt.date.fromisoformat(text)
        except ValueError:
            return text
    return text


def _split_key_value(content: str) -> tuple[str, str | None]:
    """Split `key: value` (or bare `key:`) honoring quoted keys."""
    if content[0] in "\"'":
        key, j = _read_quoted(content, 0)
        rest = content[j:].lstrip()
        if not rest.startswith(":"):
            raise OkfError(f"expected ':' after quoted key: {content!r}")
        value = rest[1:].strip()
        return key, (value or None)
    i = 0
    while i < len(content):
        if content[i] == ":" and (i + 1 == len(content) or content[i + 1] in " \t"):
            return content[:i].strip(), (content[i + 1:].strip() or None)
        i += 1
    raise OkfError(f"not a mapping entry: {content!r}")


def _is_seq_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _tokenize(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        stripped = raw.lstrip(" ")
        if stripped.startswith("#"):
            continue
        content = _strip_comment(stripped)
        if not content:
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), content))
    return lines


def _parse_node(lines, i, indent):
    if i < len(lines) and lines[i][0] == indent and _is_seq_item(lines[i][1]):
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_map(lines, i, indent):
    out: dict = {}
    while i < len(lines) and lines[i][0] == indent and not _is_seq_item(lines[i][1]):
        key, value = _split_key_value(lines[i][1])
        if value is None:
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if nxt and (nxt[0] > indent or (nxt[0] == indent and _is_seq_item(nxt[1]))):
                out[key], i = _parse_node(lines, i + 1, nxt[0])
            else:
                out[key] = None
                i += 1
        else:
            out[key] = _parse_scalar(value)
            i += 1
    return out, i


def _parse_seq(lines, i, indent):
    items: list = []
    while i < len(lines) and lines[i][0] == indent and _is_seq_item(lines[i][1]):
        text = lines[i][1]
        after = text[1:]
        rest = after.lstrip(" ")
        child_indent = indent + 1 + (len(after) - len(rest))
        if not rest:
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if nxt and nxt[0] > indent:
                value, i = _parse_node(lines, i + 1, nxt[0])
            else:
                value, i = None, i + 1
            items.append(value)
            continue
        try:
            _split_key_value(rest)
            is_map = True
        except OkfError:
            is_map = False
        if is_map:
            patched = list(lines)
            patched[i] = (child_indent, rest)
            value, i = _parse_map(patched, i, child_indent)
            items.append(value)
        else:
            items.append(_parse_scalar(rest))
            i += 1
    return items, i


def yaml_loads(text: str) -> dict:
    """Parse the YAML subset. Always returns a mapping (possibly empty)."""
    lines = _tokenize(text)
    if not lines:
        return {}
    value, _ = _parse_node(lines, 0, lines[0][0])
    if not isinstance(value, dict):
        raise OkfError("frontmatter must be a mapping")
    return value


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_text_or_None, body)."""
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None, text
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            return "\n".join(lines[1:idx]), "\n".join(lines[idx + 1:])
    return None, text


def render_document(front: dict, body: str) -> str:
    body = body.strip("\n")
    return "---\n" + yaml_dump(front) + "---\n\n" + body + "\n"


# ------------------------------------------------------------ write fence ------


def _realish(path: Path) -> Path:
    """realpath() that tolerates non-existent leaves (resolves existing ancestors)."""
    path = Path(os.path.abspath(str(path)))
    parts: list[str] = []
    cur = path
    while True:
        if cur.exists() or cur.parent == cur:
            base = Path(os.path.realpath(str(cur)))
            for part in reversed(parts):
                base = base / part
            return base
        parts.append(cur.name)
        cur = cur.parent


class Fence:
    """Hard write fence (okf-bundle.md B1/B2, rule V4).

    Every write in this module goes through `check()`. Allowed roots:
      * <wiki>/research/**
      * <wiki>/assets/papers/**
      * the current run directory, and only when explicitly registered via
        `allow_run_dir()` and only when it is <wiki>/outputs/deep-research/<slug>/
    <wiki>/wiki/ is forbidden absolutely.
    """

    def __init__(self, wiki: Path):
        self.wiki = _realish(Path(wiki))
        self.roots = [self.wiki / "research", self.wiki / "assets" / "papers"]
        self.forbidden = [self.wiki / "wiki"]

    def allow_run_dir(self, run_dir: Path) -> None:
        rd = _realish(Path(run_dir))
        expected_parent = self.wiki / "outputs" / "deep-research"
        if rd.parent != expected_parent:
            raise FenceError(
                f"refusing to register run directory outside "
                f"{expected_parent}/<slug>: {rd}")
        self.roots.append(rd)

    @staticmethod
    def _under(child: Path, parent: Path) -> bool:
        return child == parent or parent in child.parents

    def check(self, target: Path) -> Path:
        resolved = _realish(Path(target))
        for bad in self.forbidden:
            if self._under(resolved, bad):
                raise FenceError(
                    f"WRITE FENCE: {target} resolves inside {bad} which belongs to "
                    f"wiki-manager (OKF 0.1). deep-research never writes there.")
        for root in self.roots:
            if self._under(resolved, root) and resolved != root:
                return resolved
        allowed = ", ".join(str(r) for r in self.roots)
        raise FenceError(
            f"WRITE FENCE: refusing to write {target} (resolved: {resolved}). "
            f"Allowed roots: {allowed}")

    def write_text(self, target: Path, text: str) -> Path:
        resolved = self.check(target)
        self.check(resolved.parent / "_")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as fh:
            fh.write(text)
        return resolved

    def mkdir(self, target: Path) -> Path:
        resolved = self.check(target / "_").parent
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved


# --------------------------------------------------------------- helpers -------


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def slugify(text: str, maxlen: int = 80) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(text) > maxlen:
        text = text[:maxlen].rstrip("-")
    return text or "untitled"


def doi_slug(doi: str) -> str:
    return slugify(str(doi).replace("/", "-").replace(":", "-").replace(".", "-"))


def evidence_slug(record: dict) -> str:
    if record.get("pmid"):
        return f"pmid-{record['pmid']}"
    if record.get("doi"):
        return f"doi-{doi_slug(record['doi'])}"
    if record.get("pmcid"):
        return slugify(record["pmcid"])
    return slugify(record.get("evidence_id") or record.get("title") or "study")


def first_sentence(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    match = re.search(r"(?<=[.!?])\s", text)
    if match and match.start() <= limit:
        text = text[:match.start() + 1]
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def read_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise OkfError(f"{path}:{lineno}: malformed JSON line: {exc}") from exc
    return out


def relpath(target: Path, from_file: Path) -> str:
    return os.path.relpath(str(target), str(from_file.parent)).replace(os.sep, "/")


def load_document(path: Path) -> tuple[dict | None, str, str | None]:
    """Return (frontmatter, body, parse_error)."""
    text = path.read_text(encoding="utf-8")
    fm_text, body = split_frontmatter(text)
    if fm_text is None:
        return None, text, "no YAML frontmatter block"
    try:
        return yaml_loads(fm_text), body, None
    except OkfError as exc:
        return None, body, str(exc)


# ------------------------------------------------------------- concepts --------


class Concept:
    """One bundle concept document."""

    def __init__(self, ctype: str, slug: str, front: dict, body: str):
        self.type = ctype
        self.slug = slug
        self.front = front
        self.body = body

    def path(self, research: Path) -> Path:
        return research / TYPE_DIRS[self.type] / f"{self.slug}.md"

    def render(self) -> str:
        return render_document(self.front, self.body)


def order_front(front: dict) -> dict:
    known = [k for k in FRONTMATTER_ORDER if k in front]
    rest = [k for k in front if k not in FRONTMATTER_ORDER]
    return {k: front[k] for k in known + rest}


def base_front(ctype: str, title: str, description: str, resource: str,
               tags: list[str], generated_at: str, verified_at: str,
               status: str, sources: list[dict],
               stale_after: str | None = None, extra: dict | None = None) -> dict:
    tag_list = ["deep-research", slugify(ctype)]
    for tag in tags or []:
        tag = slugify(tag)
        if tag and tag not in tag_list:
            tag_list.append(tag)
    front = {
        "type": ctype,
        "title": title,
        "description": description,
        "resource": resource,
        "tags": tag_list,
        "generated": {"by": GENERATED_BY, "at": generated_at},
        "verified": {"by": VERIFIED_BY, "at": verified_at},
        "status": status,
    }
    if stale_after or ctype in STALE_REQUIRED_TYPES:
        front["stale_after"] = stale_after or default_stale_after()
    front.update(extra or {})
    front["sources"] = sources
    return order_front(front)


def default_stale_after(years: int = 2) -> str:
    now = _dt.datetime.now(_dt.timezone.utc).date()
    try:
        return now.replace(year=now.year + years).isoformat()
    except ValueError:  # 29 Feb
        return now.replace(year=now.year + years, day=28).isoformat()


def footnote_block(sources: list[dict], labels: dict[str, str] | None = None) -> str:
    labels = labels or {}
    lines = []
    for src in sources:
        sid = src["id"]
        label = labels.get(sid) or f"{src.get('title') or sid}. {src.get('resource')}"
        lines.append(f"[^{sid}]: {label}")
    return "\n".join(lines)


def pubmed_url(pmid: str) -> str:
    return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


def study_sources(record: dict, biblio: dict, concept_path: Path,
                  wiki: Path) -> tuple[list[dict], dict[str, str]]:
    """PubMed record + DOI landing page + local full-text PDF (when present)."""
    sources: list[dict] = []
    labels: dict[str, str] = {}
    pmid = biblio.get("pmid")
    doi = biblio.get("doi")
    key = pmid or (doi_slug(doi) if doi else slugify(record.get("evidence_id") or "source"))
    if pmid:
        sid = f"pubmed-{pmid}"
        sources.append({"id": sid, "resource": pubmed_url(pmid), "title": "PubMed record"})
        labels[sid] = citation_line(record, biblio) + f" {pubmed_url(pmid)}"
    if doi:
        sid = f"doi-{doi_slug(doi)}"
        sources.append({"id": sid, "resource": f"https://doi.org/{doi}",
                        "title": "DOI landing page"})
        labels[sid] = f"DOI landing page. https://doi.org/{doi}"
    local = ((record.get("fulltext") or {}).get("local_path")
             or record.get("local_path"))
    if local:
        abs_local = wiki / str(local).lstrip("/")
        if abs_local.exists():
            sid = f"fulltext-{key}"
            rel = relpath(abs_local, concept_path)
            sources.append({"id": sid, "resource": rel, "title": "Local full-text PDF"})
            labels[sid] = f"Local full-text PDF. {rel}"
    if not sources:
        url = record.get("resource") or record.get("url")
        sid = slugify(record.get("evidence_id") or "source")
        sources.append({"id": sid, "resource": url or "./", "title": record.get("title") or sid})
        labels[sid] = f"{record.get('title') or sid}. {url or ''}".strip()
    return sources, labels


def citation_line(record: dict, biblio: dict) -> str:
    authors = biblio.get("authors") or []
    if authors:
        first = authors[0]
        name = " ".join(x for x in [first.get("family"), first.get("initials")] if x)
        if not name:
            name = first.get("collective") or ""
        author = f"{name} et al." if len(authors) > 1 else name
    else:
        author = ""
    journal = (biblio.get("journal") or {}).get("iso_abbrev") or \
              (biblio.get("journal") or {}).get("title") or record.get("journal") or ""
    date = biblio.get("publication_date") or record.get("publication_date") or ""
    year = str(date)[:4]
    bits = [b for b in [author, record.get("title") or biblio.get("title") or "", journal] if b]
    line = ". ".join(bits)
    tail = year
    if biblio.get("volume"):
        tail += f";{biblio['volume']}"
        if biblio.get("issue"):
            tail += f"({biblio['issue']})"
        if biblio.get("pages"):
            tail += f":{biblio['pages']}"
    if tail:
        line += f". {tail}"
    if biblio.get("pmid"):
        line += f". PMID {biblio['pmid']}"
    return re.sub(r"\.\.+", ".", line).strip()


def split_author_string(name: str) -> dict:
    """'Smith JA' -> family/initials. Decomposition only; nothing invented."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    match = re.match(r"^(.+?)\s+([A-Z]{1,4})$", name)
    if match:
        return {"family": match.group(1), "given": None, "initials": match.group(2),
                "affiliation": None, "collective": None}
    return {"family": name or None, "given": None, "initials": None,
            "affiliation": None, "collective": None}


def normalize_authors(raw) -> list[dict] | None:
    if not raw:
        return None
    out = []
    for entry in raw:
        if isinstance(entry, dict):
            out.append({
                "family": entry.get("family"),
                "given": entry.get("given"),
                "initials": entry.get("initials"),
                "affiliation": entry.get("affiliation"),
                "collective": entry.get("collective"),
            })
        else:
            out.append(split_author_string(entry))
    return out


def build_biblio(record: dict, pubmed: dict | None = None) -> dict:
    """Bibliographic frontmatter block (§3). Unknown -> omitted; sub-parts -> null."""
    src: dict = {}
    src.update(record or {})
    src.update({k: v for k, v in (pubmed or {}).items() if v is not None})
    out: dict = {}

    for key in ("pmid", "pmcid"):
        if src.get(key):
            out[key] = str(src[key])
    if src.get("doi"):
        out["doi"] = str(src["doi"]).strip().lower()

    authors = normalize_authors(src.get("authors"))
    if authors:
        out["authors"] = authors

    journal = src.get("journal")
    if isinstance(journal, dict):
        out["journal"] = {"title": journal.get("title"),
                          "iso_abbrev": journal.get("iso_abbrev"),
                          "issn": journal.get("issn")}
    elif journal:
        out["journal"] = {"title": None, "iso_abbrev": str(journal), "issn": None}

    for key in ("publication_date", "epub_date"):
        if src.get(key):
            out[key] = str(src[key])
    for key in ("volume", "issue", "pages"):
        if src.get(key) not in (None, ""):
            out[key] = str(src[key])
    if src.get("abstract"):
        out["abstract"] = str(src["abstract"])
    for key in ("article_types", "mesh_terms", "keywords"):
        if src.get(key) is not None:
            out[key] = [str(x) for x in src[key]]
    grants = src.get("grants")
    if grants:
        out["grants"] = [
            ({"id": g.get("id"), "agency": g.get("agency")} if isinstance(g, dict)
             else {"id": str(g), "agency": None})
            for g in grants
        ]
    if src.get("publication_status"):
        out["publication_status"] = str(src["publication_status"])
    status = src.get("retraction_status") or "none"
    if status not in RETRACTION_STATUSES:
        raise OkfError(f"invalid retraction_status: {status!r}")
    out["retraction_status"] = status
    if src.get("is_preprint") is not None:
        out["is_preprint"] = bool(src["is_preprint"])
    return out


def study_concept(record: dict, wiki: Path, generated_at: str, verified_at: str,
                  status: str, pubmed: dict | None = None,
                  extraction: dict | None = None, body: str | None = None,
                  tags: list[str] | None = None,
                  description: str | None = None) -> Concept:
    biblio = build_biblio(record, pubmed)
    slug = evidence_slug(record)
    research = wiki / "research"
    concept_path = research / "studies" / f"{slug}.md"
    sources, labels = study_sources(record, biblio, concept_path, wiki)
    title = record.get("title") or (pubmed or {}).get("title") or slug
    resource = (pubmed_url(biblio["pmid"]) if biblio.get("pmid")
                else (f"https://doi.org/{biblio['doi']}" if biblio.get("doi")
                      else record.get("resource") or f"./{slug}.md"))
    basis = ((record.get("fulltext") or {}).get("status")
             or (extraction or {}).get("evidence_basis"))
    extra: dict = {}
    if record.get("evidence_id"):
        extra["evidence_id"] = record["evidence_id"]
    if basis in ("fulltext", "abstract_only"):
        extra["evidence_basis"] = basis
    extra.update(biblio)
    tag_list = list(tags or [])
    for atype in (biblio.get("article_types") or [])[:3]:
        tag_list.append(atype)
    if description is None:
        description = first_sentence(
            (extraction or {}).get("design")
            and f"{(extraction or {}).get('design')} reported in {title}."
            or record.get("description")
            or f"Study record for {title}."
        )
    front = base_front("Study", title, description, resource, tag_list,
                       generated_at, verified_at, status, sources, extra=extra)
    if body is None:
        body = study_body(record, biblio, extraction, sources, labels, basis)
    return Concept("Study", slug, front, body)


def study_body(record: dict, biblio: dict, extraction: dict | None,
               sources: list[dict], labels: dict[str, str], basis: str | None) -> str:
    primary = sources[0]["id"]
    abstract_note = " (abstract only)" if basis == "abstract_only" else ""
    lines = [f"# {record.get('title') or biblio.get('pmid') or 'Study'}", ""]
    lines.append(f"{citation_line(record, biblio)}[^{primary}]")
    lines.append("")
    lines.append("## Record")
    lines.append("")
    if biblio.get("pmid"):
        lines.append(f"- PMID: {biblio['pmid']}")
    if biblio.get("doi"):
        lines.append(f"- DOI: {biblio['doi']}")
    if biblio.get("pmcid"):
        lines.append(f"- PMCID: {biblio['pmcid']}")
    if biblio.get("article_types"):
        lines.append(f"- Article types: {', '.join(biblio['article_types'])}")
    lines.append(f"- Retraction status: {biblio.get('retraction_status', 'none')}")
    if basis:
        lines.append(f"- Evidence basis: {basis}")
    if extraction:
        lines.append("")
        lines.append("## Extracted")
        lines.append("")
        for key in ("design", "population", "intervention", "comparator"):
            if extraction.get(key):
                lines.append(f"- {key.capitalize()}: {extraction[key]}"
                             f"{abstract_note}[^{primary}]")
        if extraction.get("n_total") is not None:
            lines.append(f"- Analyzed N: {extraction['n_total']}"
                         f"{abstract_note}[^{primary}]")
        outcomes = extraction.get("outcomes") or []
        if outcomes:
            lines.append("")
            lines.append("## Outcomes")
            lines.append("")
            for out in outcomes:
                lines.append(f"- {outcome_sentence(out)}{abstract_note}[^{primary}]")
        for key in ("funding", "coi", "limitations"):
            if extraction.get(key):
                lines.append("")
                lines.append(f"**{key.upper() if key == 'coi' else key.capitalize()}:** "
                             f"{extraction[key]}[^{primary}]")
    lines.append("")
    lines.append(footnote_block(sources, labels))
    return "\n".join(lines)


def outcome_sentence(out: dict) -> str:
    bits = [out.get("name") or "outcome"]
    if out.get("timepoint"):
        bits.append(f"at {out['timepoint']}")
    effect = out.get("effect")
    if effect is not None:
        stat = f"{out.get('effect_measure') or 'effect'} {effect}"
        if out.get("ci_low") is not None and out.get("ci_high") is not None:
            stat += f", 95% CI {out['ci_low']} to {out['ci_high']}"
        if out.get("p_value") is not None:
            stat += f", p={out['p_value']}"
        bits.append(f"({stat})")
    if out.get("direction"):
        bits.append(f"— {str(out['direction']).replace('_', ' ')}")
    return " ".join(bits)


# --------------------------------------------------------- index / log ---------


def bundle_root_front(generated_at: str, verified_at: str) -> dict:
    return order_front({
        "okf_version": OKF_VERSION,
        "bundle": BUNDLE,
        "type": "Bundle",
        "title": "Deep Research Bundle",
        "description": ("Literature-research concepts generated and verified by the "
                        "deep-research skill."),
        "resource": "./index.md",
        "tags": ["deep-research", "bundle"],
        "generated": {"by": GENERATED_BY, "at": generated_at},
        "verified": {"by": VERIFIED_BY, "at": verified_at},
        "status": "stable",
    })


def dir_index_front(dirname: str, ctype: str, generated_at: str,
                    verified_at: str) -> dict:
    return order_front({
        "type": "Index",
        "title": f"{ctype} index",
        "description": f"Index of {ctype} concepts in the deep-research bundle.",
        "resource": "./index.md",
        "tags": ["deep-research", "index", slugify(ctype)],
        "generated": {"by": GENERATED_BY, "at": generated_at},
        "verified": {"by": VERIFIED_BY, "at": verified_at},
        "status": "stable",
    })


def write_root_index(fence: Fence, research: Path, generated_at: str,
                     verified_at: str) -> None:
    lines = ["# Deep Research Bundle", "",
             "Concepts generated by the `deep-research` skill. This bundle is "
             "deep-research's own OKF v0.2-style convention "
             "(`references/okf-bundle.md`); it is **not** wiki-manager's OKF 0.1 "
             "bundle at `../wiki/`, which is never written to here.", "",
             "## Concept types", ""]
    for ctype, dirname in TYPE_DIRS.items():
        count = 0
        d = research / dirname
        if d.is_dir():
            count = len([p for p in d.glob("*.md") if p.name != "index.md"])
        lines.append(f"- [{ctype}]({dirname}/index.md) — {count} concept(s)")
    lines += ["", "## Log", "", "- [Change log](log.md)"]
    fence.write_text(research / "index.md",
                     render_document(bundle_root_front(generated_at, verified_at),
                                     "\n".join(lines)))


def write_dir_index(fence: Fence, research: Path, dirname: str, generated_at: str,
                    verified_at: str) -> None:
    ctype = DIR_TYPES[dirname]
    d = research / dirname
    entries = []
    if d.is_dir():
        for path in sorted(d.glob("*.md")):
            if path.name == "index.md":
                continue
            front, _, _ = load_document(path)
            title = (front or {}).get("title") or path.stem
            entries.append(f"- [{title}]({path.name})")
    body = [f"# {ctype}", "", f"Concepts of type `{ctype}`.", ""]
    body += entries or ["_No concepts yet._"]
    body += ["", "[Bundle root](../index.md)"]
    fence.write_text(d / "index.md",
                     render_document(dir_index_front(dirname, ctype, generated_at,
                                                     verified_at),
                                     "\n".join(body)))


def append_log(fence: Fence, log_path: Path, entries: list[str],
               date: str | None = None) -> None:
    """Append bullets under today's ISO heading; newest last, never reorder."""
    if not entries:
        return
    date = date or today()
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8")
    else:
        text = "# Log\n"
    lines = text.rstrip("\n").split("\n")
    heading = f"## {date}"
    idx = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            idx = i
    if idx is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(heading)
        lines.extend(entries)
    else:
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        while end > idx + 1 and not lines[end - 1].strip():
            end -= 1
        existing = set(lines[idx + 1:end])
        fresh = [e for e in entries if e not in existing]
        lines[end:end] = fresh
    fence.write_text(log_path, "\n".join(lines).rstrip("\n") + "\n")


# ------------------------------------------------------------- validation ------


class Violation:
    __slots__ = ("rule", "path", "detail")

    def __init__(self, rule: str, path: str, detail: str):
        self.rule = rule
        self.path = path
        self.detail = detail

    def as_dict(self) -> dict:
        return {"rule": self.rule, "path": self.path, "detail": self.detail}


class Validator:
    def __init__(self, wiki: Path, corpus: list[dict] | None = None):
        self.wiki = _realish(Path(wiki))
        self.research = self.wiki / "research"
        self.papers = self.wiki / "assets" / "papers"
        self.corpus = corpus
        self.corpus_by_id = {}
        for rec in corpus or []:
            if rec.get("evidence_id"):
                self.corpus_by_id[rec["evidence_id"]] = rec
        self.violations: list[Violation] = []
        self.files_checked = 0
        self.skipped_rules: list[str] = []

    def add(self, rule: str, path: Path | str, detail: str) -> None:
        try:
            shown = os.path.relpath(str(path), str(self.wiki))
        except ValueError:
            shown = str(path)
        self.violations.append(Violation(rule, shown, detail))

    # --- entry points ---

    def run(self, only: Path | None = None) -> None:
        self.check_root()
        files = [only] if only else self.concept_files()
        for path in files:
            self.check_file(Path(path))
        if not only:
            self.check_indexes()
            self.check_logs()
        if self.corpus is None:
            self.skipped_rules += ["V12", "V24"]

    def concept_files(self) -> list[Path]:
        out = []
        if not self.research.is_dir():
            return out
        for dirname in TYPE_DIRS.values():
            d = self.research / dirname
            if not d.is_dir():
                continue
            for path in sorted(d.glob("*.md")):
                if path.name != "index.md":
                    out.append(path)
        return out

    # --- V5 ---

    def check_root(self) -> None:
        root = self.research / "index.md"
        if not root.exists():
            self.add("V5", root, "bundle root research/index.md is missing")
            return
        front, _, err = load_document(root)
        if err or front is None:
            self.add("V5", root, f"bundle root frontmatter unreadable: {err}")
            return
        if str(front.get("okf_version")) != OKF_VERSION:
            self.add("V5", root,
                     f"okf_version is {front.get('okf_version')!r}, expected {OKF_VERSION!r}")
        if front.get("bundle") != BUNDLE:
            self.add("V5", root,
                     f"bundle is {front.get('bundle')!r}, expected {BUNDLE!r}")
        if not str(front.get("type") or "").strip():
            self.add("V1", root, "bundle root has no non-empty `type`")

    # --- per concept file ---

    def check_file(self, path: Path) -> None:
        self.files_checked += 1
        # V4 (static half): a concept file must live inside the fence
        resolved = _realish(path)
        if (self.wiki / "wiki") in resolved.parents:
            self.add("V4", path, "concept file lives inside <wiki>/wiki/ (forbidden)")
        if not (self.research in resolved.parents):
            self.add("V4", path, "concept file lives outside <wiki>/research/")

        front, body, err = load_document(path)
        if front is None:
            self.add("V1", path, err or "no YAML frontmatter")
            return
        ctype = front.get("type")
        if not isinstance(ctype, str) or not ctype.strip():
            self.add("V1", path, "missing or empty `type`")
            ctype = None

        self.check_required(path, front)
        self.check_sources(path, front, body)
        self.check_timestamps(path, front)
        self.check_status(path, front)
        self.check_stale(path, front, ctype)
        self.check_taxonomy(path, front, ctype)
        self.check_pubmed(path, front, ctype)
        self.check_tags(path, front)
        self.check_links(path, front, body)
        self.check_wikilinks(path, body)
        self.check_footnote_usage(path, front, body)
        self.check_slug(path)
        self.check_okf01(path, front)
        self.check_corpus(path, front, body, ctype)

    # V6
    def check_required(self, path, front) -> None:
        for field in REQUIRED_FIELDS:
            if field not in front:
                self.add("V6", path, f"missing required field `{field}`")
                continue
            value = front[field]
            if value is None or (isinstance(value, (str, list, dict)) and not value) \
                    or (isinstance(value, str) and not value.strip()):
                self.add("V6", path, f"required field `{field}` is empty")

    # V2, V3
    def check_sources(self, path, front, body) -> None:
        sources = front.get("sources")
        if not isinstance(sources, list):
            return
        seen = set()
        ids = []
        for i, src in enumerate(sources):
            if not isinstance(src, dict):
                self.add("V2", path, f"sources[{i}] is not a mapping")
                continue
            sid = src.get("id")
            if not isinstance(sid, str) or not SOURCE_ID_RE.match(sid):
                self.add("V2", path, f"sources[{i}].id {sid!r} fails ^[a-z0-9][a-z0-9-]*$")
                continue
            if sid in seen:
                self.add("V2", path, f"duplicate sources[].id {sid!r}")
            seen.add(sid)
            ids.append(sid)
            for field in ("resource", "title"):
                if not str(src.get(field) or "").strip():
                    self.add("V6", path, f"sources[{i}].{field} is missing or empty")
        for key in sorted(set(FOOTNOTE_REF_RE.findall(body))
                          | set(FOOTNOTE_DEF_RE.findall(body))):
            if key not in seen:
                self.add("V3", path, f"footnote key [^{key}] does not resolve to a sources[].id")

    # V7
    def check_timestamps(self, path, front) -> None:
        for field in ("generated", "verified"):
            block = front.get(field)
            if not isinstance(block, dict):
                if field in front:
                    self.add("V7", path, f"`{field}` is not a mapping")
                continue
            if not str(block.get("by") or "").strip():
                self.add("V7", path, f"`{field}.by` is missing")
            at = block.get("at")
            if isinstance(at, (_dt.date, _dt.datetime)):
                at = _scalar(at)
            if not isinstance(at, str) or not TS_RE.match(at):
                self.add("V7", path,
                         f"`{field}.at` {at!r} is not ISO-8601 UTC (YYYY-MM-DDThh:mm:ssZ)")

    # V8
    def check_status(self, path, front) -> None:
        if "status" in front and front["status"] not in STATUSES:
            self.add("V8", path,
                     f"status {front['status']!r} not in {list(STATUSES)}")

    # V9
    def check_stale(self, path, front, ctype) -> None:
        if ctype not in STALE_REQUIRED_TYPES:
            return
        value = front.get("stale_after")
        if isinstance(value, _dt.date):
            return
        if not isinstance(value, str) or not DATE_RE.match(value):
            self.add("V9", path,
                     f"type {ctype!r} requires stale_after as YYYY-MM-DD (got {value!r})")

    # V10, V11
    def check_taxonomy(self, path, front, ctype) -> None:
        if ctype is None:
            return
        expected = TYPE_DIRS.get(ctype)
        if expected is None:
            self.add("V10", path, f"unknown concept type {ctype!r} for this bundle")
            return
        if path.parent.name != expected:
            self.add("V10", path,
                     f"type {ctype!r} must live in research/{expected}/, "
                     f"found research/{path.parent.name}/")
        if ctype == "Study":
            pmid = front.get("pmid")
            doi = front.get("doi")
            if pmid not in (None, ""):
                want = f"pmid-{pmid}"
            elif doi not in (None, ""):
                want = f"doi-{doi_slug(str(doi))}"
            else:
                self.add("V11", path, "Study has neither pmid nor doi in frontmatter")
                return
            if path.stem != want:
                self.add("V11", path, f"Study filename must be {want}.md")

    # V12 (needs corpus), V13, V14, V15
    def check_pubmed(self, path, front, ctype) -> None:
        is_pubmed = ctype in PUBMED_TYPES or str(
            front.get("resource") or "").startswith("https://pubmed.ncbi.nlm.nih.gov/")
        if ctype in RETRACTION_TYPES:
            value = front.get("retraction_status")
            if value not in RETRACTION_STATUSES:
                self.add("V15", path,
                         f"retraction_status {value!r} missing or not in "
                         f"{list(RETRACTION_STATUSES)}")
        if not is_pubmed:
            return
        for field in ("pmid", "doi", "pmcid", "volume", "issue", "pages"):
            if field not in front or front[field] is None:
                continue
            value = front[field]
            if isinstance(value, bool) or isinstance(value, (int, float)) \
                    or isinstance(value, (_dt.date, _dt.datetime)):
                self.add("V13", path,
                         f"`{field}` must be a YAML string, parsed as "
                         f"{type(value).__name__}: {value!r}")
        pmid = front.get("pmid")
        if isinstance(pmid, str) and not PMID_RE.match(pmid):
            self.add("V14", path, f"pmid {pmid!r} does not match ^\\d+$")
        pmcid = front.get("pmcid")
        if isinstance(pmcid, str) and not PMCID_RE.match(pmcid):
            self.add("V14", path, f"pmcid {pmcid!r} does not match ^PMC\\d+$")
        doi = front.get("doi")
        if isinstance(doi, str):
            if not DOI_RE.match(doi):
                self.add("V14", path, f"doi {doi!r} does not match ^10\\.\\d{{4,}}/\\S+$")
            elif doi != doi.lower():
                self.add("V14", path, f"doi {doi!r} is not lowercase")

    # V16
    def check_tags(self, path, front) -> None:
        tags = front.get("tags")
        if not isinstance(tags, list) or not tags:
            self.add("V16", path, "`tags` must be a non-empty list")
            return
        if "deep-research" not in tags:
            self.add("V16", path, "`tags` must contain `deep-research`")

    # V17
    def check_links(self, path, front, body) -> None:
        targets = []
        for src in front.get("sources") or []:
            if isinstance(src, dict) and isinstance(src.get("resource"), str):
                targets.append(("sources[].resource", src["resource"]))
        for dest in MD_LINK_RE.findall(body):
            targets.append(("markdown link", dest))
        for kind, dest in targets:
            if re.match(r"^(https?|mailto):", dest) or dest.startswith("#"):
                continue
            clean = dest.split("#", 1)[0].split("?", 1)[0]
            if not clean:
                continue
            resolved = _realish(path.parent / clean)
            inside = any(root == resolved or root in resolved.parents
                         for root in (self.research, self.papers))
            in_wiki_bundle = (self.wiki / "wiki") == resolved or \
                (self.wiki / "wiki") in resolved.parents
            in_runs = (self.wiki / "outputs") in resolved.parents
            if not resolved.exists():
                if in_wiki_bundle or in_runs:
                    continue  # permitted, non-load-bearing (§5); existence not required
                self.add("V17", path, f"dangling {kind}: {dest}")
            elif not (inside or in_wiki_bundle or in_runs):
                self.add("V17", path,
                         f"{kind} {dest} resolves outside research/ and assets/papers/")

    # V18
    def check_wikilinks(self, path, body) -> None:
        wikilinks = WIKILINK_RE.findall(body)
        if not wikilinks:
            return
        md_targets = set()
        for dest in MD_LINK_RE.findall(body):
            clean = dest.split("#", 1)[0]
            md_targets.add(clean)
            md_targets.add(Path(clean).stem)
            md_targets.add(Path(clean).name)
        for target in wikilinks:
            stem = Path(target.strip()).stem
            if target.strip() not in md_targets and stem not in md_targets:
                self.add("V18", path,
                         f"wikilink [[{target}]] has no corresponding markdown link")

    # V19
    def check_footnote_usage(self, path, front, body) -> None:
        sources = front.get("sources")
        if not isinstance(sources, list) or not sources:
            return
        if not FOOTNOTE_REF_RE.search(body):
            self.add("V19", path,
                     "concept declares sources[] but the body carries no [^key] footnote "
                     "reference (a body-only references list is not attribution)")

    # V22
    def check_slug(self, path) -> None:
        stem = path.stem
        for prefix in ("pmid-", "doi-"):
            if stem.startswith(prefix):
                stem = stem[len(prefix):]
                break
        if not SLUG_RE.match(stem):
            self.add("V22", path, f"filename slug {path.stem!r} fails ^[a-z0-9][a-z0-9-]{{0,79}}$")

    # V23
    def check_okf01(self, path, front) -> None:
        if "timestamp" in front:
            self.add("V23", path,
                     "frontmatter carries wiki-manager OKF 0.1 field `timestamp`")
        sources = front.get("sources")
        if isinstance(sources, str):
            self.add("V23", path, "`sources` is a string (wiki-manager OKF 0.1 shape)")
        elif isinstance(sources, list) and sources and \
                all(isinstance(s, str) for s in sources):
            self.add("V23", path, "`sources` is a list of strings (wiki-manager OKF 0.1 shape)")

    # V12, V24, V25
    def check_corpus(self, path, front, body, ctype) -> None:
        cited = []
        if isinstance(front.get("evidence_id"), str):
            cited.append(front["evidence_id"])
        for eid in front.get("evidence_ids") or []:
            if isinstance(eid, str):
                cited.append(eid)
        if self.corpus is not None:
            for eid in cited:
                if eid not in self.corpus_by_id:
                    self.add("V24", path,
                             f"evidence_id {eid!r} is not present in the run's corpus.jsonl")
            if ctype in PUBMED_TYPES:
                record = self.corpus_by_id.get(front.get("evidence_id")) or \
                    self.find_corpus_record(front)
                if record:
                    for ckey, fkey in CORPUS_BIBLIO_MAP.items():
                        value = record.get(ckey)
                        if value in (None, "", []):
                            continue
                        if fkey not in front or front[fkey] in (None, "", []):
                            self.add("V12", path,
                                     f"corpus supplies `{ckey}` but concept omits `{fkey}`")
        # V25
        declared = front.get("evidence_basis") == "abstract_only" or "(abstract only)" in body
        basis_records = []
        if self.corpus is not None:
            for eid in cited:
                rec = self.corpus_by_id.get(eid)
                if rec and (rec.get("fulltext") or {}).get("status") == "abstract_only":
                    basis_records.append(eid)
        if front.get("evidence_basis") == "abstract_only" and not declared:
            self.add("V25", path, "abstract-only basis is not declared")
        if basis_records and not declared:
            self.add("V25", path,
                     "rests on abstract_only evidence (" + ", ".join(basis_records) +
                     ") without `(abstract only)` in the body or "
                     "`evidence_basis: abstract_only` in frontmatter")

    def find_corpus_record(self, front) -> dict | None:
        pmid = front.get("pmid")
        doi = front.get("doi")
        for rec in self.corpus or []:
            if pmid and str(rec.get("pmid")) == str(pmid):
                return rec
            if doi and str(rec.get("doi") or "").lower() == str(doi).lower():
                return rec
        return None

    # V21
    def check_indexes(self) -> None:
        if not self.research.is_dir():
            return
        for d in sorted(p for p in self.research.iterdir() if p.is_dir()):
            concepts = [p for p in d.glob("*.md") if p.name != "index.md"]
            if concepts and not (d / "index.md").exists():
                self.add("V21", d / "index.md",
                         f"directory {d.name}/ holds {len(concepts)} concept file(s) "
                         f"but has no index.md")

    # V20
    def check_logs(self) -> None:
        if not self.research.is_dir():
            return
        for log_path in sorted(self.research.rglob("log.md")):
            text = log_path.read_text(encoding="utf-8")
            seen: set[str] = set()
            previous: str | None = None
            for line in text.split("\n"):
                match = LOG_HEADING_RE.match(line)
                if not match:
                    continue
                value = match.group(1).strip()
                if not DATE_RE.match(value):
                    self.add("V20", log_path, f"heading '## {value}' is not '## YYYY-MM-DD'")
                    continue
                if value in seen:
                    self.add("V20", log_path, f"duplicate heading '## {value}'")
                if previous and value < previous:
                    self.add("V20", log_path,
                             f"heading '## {value}' is out of ascending order "
                             f"(follows '## {previous}')")
                seen.add(value)
                previous = value

    # --- reporting ---

    def report_markdown(self, scope: str) -> str:
        ok = not self.violations
        lines = [
            "# OKF validation report",
            "",
            f"- generated: {utcnow()}",
            f"- validator: {GENERATED_BY} (`scripts/okf.py validate`)",
            f"- spec: `references/okf-bundle.md` (OKF-style v{OKF_VERSION}, bundle `{BUNDLE}`)",
            f"- wiki: `{self.wiki}`",
            f"- scope: {scope}",
            f"- files checked: {self.files_checked}",
            f"- violations: {len(self.violations)}",
            "",
            f"**Result: {'PASS' if ok else 'FAIL'}**",
            "",
        ]
        if self.skipped_rules:
            lines += [
                f"> Rules not evaluated (no `--corpus` supplied): "
                f"{', '.join(sorted(set(self.skipped_rules)))}.",
                "",
            ]
        if ok:
            lines.append("No violations found.")
        else:
            lines += ["## Violations", "", "| rule | path | detail |", "|---|---|---|"]
            for v in sorted(self.violations, key=lambda x: (x.rule.rjust(4), x.path)):
                detail = v.detail.replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {v.rule} | `{v.path}` | {detail} |")
            lines += ["", "Promotion into the wiki is blocked while any violation stands "
                          "(`PLAN.md` §5, failure semantics)."]
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------- commands ------


def resolve_type(name: str) -> str:
    if name in TYPE_DIRS:
        return name
    key = str(name).strip().lower().replace("_", "-").replace(" ", "-")
    if key in TYPE_ALIASES:
        return TYPE_ALIASES[key]
    raise OkfError(f"unknown concept type {name!r}; expected one of: "
                   + ", ".join(sorted(TYPE_DIRS)))


def cmd_init(args) -> int:
    wiki = Path(args.wiki).expanduser()
    if not wiki.exists():
        raise OkfError(f"wiki root does not exist: {wiki}")
    fence = Fence(wiki)
    research = fence.wiki / "research"
    now = utcnow()
    fence.mkdir(research)
    for dirname in TYPE_DIRS.values():
        fence.mkdir(research / dirname)
        write_dir_index(fence, research, dirname, now, now)
    write_root_index(fence, research, now, now)
    log_path = research / "log.md"
    if not log_path.exists():
        fence.write_text(log_path, "# Log\n")
    append_log(fence, log_path, [f"- initialized bundle root [index.md](index.md) "
                                 f"(okf_version {OKF_VERSION}, bundle {BUNDLE})"])
    print(json.dumps({"status": "ok", "bundle_root": str(research / "index.md"),
                      "directories": sorted(TYPE_DIRS.values())}, indent=2))
    return 0


def ensure_bundle(fence: Fence) -> Path:
    research = fence.wiki / "research"
    root = research / "index.md"
    if not root.exists():
        raise OkfError(
            f"no deep-research bundle at {research} — run `okf.py init --wiki {fence.wiki}` "
            f"first (B4: okf.py never writes into an unmarked research/ tree)")
    front, _, err = load_document(root)
    if err or not front or str(front.get("okf_version")) != OKF_VERSION \
            or front.get("bundle") != BUNDLE:
        raise OkfError(
            f"{root} is not a conforming deep-research bundle root "
            f"(need okf_version: \"{OKF_VERSION}\", bundle: {BUNDLE})")
    return research


def concept_from_spec(spec: dict, ctype: str, wiki: Path, generated_at: str,
                      verified_at: str, status: str, slug_override: str | None) -> Concept:
    """Build a Concept from a corpus record and/or a concept spec object."""
    record = spec.get("corpus") if isinstance(spec.get("corpus"), dict) else {}
    if not record and (spec.get("evidence_id") or spec.get("pmid") or spec.get("doi")):
        record = spec
    pubmed = spec.get("pubmed") if isinstance(spec.get("pubmed"), dict) else None
    extraction = spec.get("extraction") if isinstance(spec.get("extraction"), dict) else None
    status = spec.get("status") or status
    if status not in STATUSES:
        raise OkfError(f"status {status!r} not in {list(STATUSES)}")

    if ctype == "Study":
        concept = study_concept(record or spec, wiki, generated_at, verified_at, status,
                                pubmed=pubmed, extraction=extraction,
                                body=spec.get("body"), tags=spec.get("tags"),
                                description=spec.get("description"))
        if slug_override:
            raise OkfError("Study slugs are derived from pmid/doi (V11); "
                           "--slug is not accepted")
        for key in ("title", "description", "resource", "stale_after"):
            if spec.get(key) and key != "description":
                concept.front[key] = spec[key]
        if spec.get("evidence_ids"):
            concept.front["evidence_ids"] = list(spec["evidence_ids"])
        concept.front = order_front(concept.front)
        return concept

    title = spec.get("title")
    if not title:
        raise OkfError(f"{ctype} spec requires a `title`")
    slug = slug_override or spec.get("slug") or slugify(title)
    if not SLUG_RE.match(slug):
        raise OkfError(f"slug {slug!r} fails ^[a-z0-9][a-z0-9-]{{0,79}}$ (V22)")
    sources = spec.get("sources")
    if not sources:
        raise OkfError(f"{ctype} spec requires a non-empty `sources` list (V6)")
    for src in sources:
        if not isinstance(src, dict) or not src.get("id"):
            raise OkfError("each sources[] entry needs {id, resource, title}")
    description = spec.get("description") or first_sentence(title)
    resource = spec.get("resource") or f"./{slug}.md"
    extra = {k: v for k, v in (spec.get("extra") or {}).items()}
    for key in ("evidence_id", "evidence_ids", "evidence_basis", "run_slug",
                "publisher", "document_type", "accessed_at", "is_preprint",
                "tool", "overall_judgement", "certainty"):
        if spec.get(key) is not None:
            extra[key] = spec[key]
    if ctype in RETRACTION_TYPES:
        extra["retraction_status"] = spec.get("retraction_status") or "none"
    front = base_front(ctype, title, description, resource, spec.get("tags") or [],
                       generated_at, verified_at, status, sources,
                       stale_after=spec.get("stale_after"), extra=extra)
    body = spec.get("body")
    if not body:
        primary = sources[0]["id"]
        body = (f"# {title}\n\n{description}[^{primary}]\n\n"
                + footnote_block(sources))
    return Concept(ctype, slug, front, body)


def write_concepts(fence: Fence, research: Path, concepts: list[Concept],
                   now: str, run_slug: str | None, log: bool = True,
                   dry_run: bool = False,
                   log_notes: list[str] | None = None) -> list[Path]:
    written: list[Path] = []
    entries: list[str] = []
    for concept in concepts:
        path = concept.path(research)
        fence.check(path)
        existed = path.exists()
        if not dry_run:
            fence.write_text(path, concept.render())
        written.append(path)
        rel = relpath(path, research / "log.md")
        suffix = f" (run: {run_slug})" if run_slug else ""
        entries.append(f"- {'updated' if existed else 'created'} "
                       f"[{concept.type}: {concept.front.get('title')}]({rel}){suffix}")
    if not dry_run:
        dirs = sorted({c.path(research).parent.name for c in concepts})
        for dirname in dirs:
            write_dir_index(fence, research, dirname, now, now)
        write_root_index(fence, research, now, now)
        if log:
            append_log(fence, research / "log.md", list(log_notes or []) + entries)
    return written


def cmd_write(args) -> int:
    wiki = Path(args.wiki).expanduser()
    fence = Fence(wiki)
    research = ensure_bundle(fence)
    ctype = resolve_type(args.type)
    spec = read_json(Path(args.source))
    if not isinstance(spec, dict):
        raise OkfError("--from must contain a JSON object")
    now = utcnow()
    generated_at = args.generated_at or now
    verified_at = args.verified_at or now
    for label, value in (("--generated-at", generated_at), ("--verified-at", verified_at)):
        if not TS_RE.match(value):
            raise OkfError(f"{label} must be YYYY-MM-DDThh:mm:ssZ (got {value!r})")
    concept = concept_from_spec(spec, ctype, fence.wiki, generated_at, verified_at,
                                args.status, args.slug)
    written = write_concepts(fence, research, [concept], now, args.run_slug,
                             log=not args.no_log, dry_run=args.dry_run)
    print(json.dumps({"status": "ok" if not args.dry_run else "dry-run",
                      "type": ctype, "path": str(written[0])}, indent=2))
    return 0


# ------------------------------------------------------------- promote ---------


def load_run(run_dir: Path) -> dict:
    run: dict = {"dir": run_dir}
    config_path = run_dir / "config.json"
    run["config"] = read_json(config_path) if config_path.exists() else {}
    run["corpus"] = read_jsonl(run_dir / "corpus.jsonl")
    run["searches"] = [read_json(p) for p in
                       sorted((run_dir / "workspace" / "search").glob("*.json"))]
    run["extractions"] = {}
    for p in sorted((run_dir / "workspace" / "extractions").glob("*.json")):
        rec = read_json(p)
        if rec.get("evidence_id"):
            run["extractions"][rec["evidence_id"]] = rec
    run["appraisals"] = {}
    for p in sorted((run_dir / "workspace" / "appraisals").glob("*.json")):
        rec = read_json(p)
        if rec.get("evidence_id"):
            run["appraisals"][rec["evidence_id"]] = rec
    for candidate in (run_dir / "outputs" / "protocol.md", run_dir / "protocol.md"):
        if candidate.exists():
            run["protocol_md"] = candidate.read_text(encoding="utf-8")
            break
    report_path = run_dir / "outputs" / "report.md"
    run["report_md"] = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    ver_path = run_dir / "outputs" / "verification.json"
    run["verification"] = read_json(ver_path) if ver_path.exists() else None
    run["slug"] = run["config"].get("slug") or run_dir.name
    run["question"] = run["config"].get("question") or run["config"].get("title") \
        or run["slug"].replace("-", " ")
    return run


def report_section(report: str, *names: str) -> list[str]:
    """Return bullet lines under the first heading whose text matches any name."""
    lines = report.split("\n")
    wanted = [n.lower() for n in names]
    out: list[str] = []
    capturing = False
    level = 0
    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            text = heading.group(2).strip().lower()
            if capturing and len(heading.group(1)) <= level:
                capturing = False
            if any(w in text for w in wanted):
                capturing = True
                level = len(heading.group(1))
                continue
        if capturing:
            bullet = re.match(r"^\s*[-*]\s+(.*\S)\s*$", line)
            if bullet:
                out.append(bullet.group(1))
    return out


def strip_frontmatter_body(text: str) -> str:
    _, body = split_frontmatter(text)
    return body.strip()


def source_for_record(record: dict, concept_path: Path, wiki: Path) -> tuple[dict, str]:
    biblio = {"pmid": record.get("pmid"),
              "doi": str(record.get("doi")).lower() if record.get("doi") else None,
              "publication_date": record.get("publication_date"),
              "journal": {"iso_abbrev": record.get("journal"), "title": None, "issn": None},
              "authors": normalize_authors(record.get("authors"))}
    sources, labels = study_sources(record, biblio, concept_path, wiki)
    return sources[0], labels[sources[0]["id"]]


def collect_sources(records: list[dict], concept_path: Path,
                    wiki: Path) -> tuple[list[dict], dict[str, str]]:
    sources, labels = [], {}
    seen = set()
    for record in records:
        src, label = source_for_record(record, concept_path, wiki)
        if src["id"] in seen:
            continue
        seen.add(src["id"])
        sources.append(src)
        labels[src["id"]] = label
    return sources, labels


# ------------------------------------------- publisher integrity preflight -----
#
# VALIDATION_ARCHITECTURE_PLAN.md Phase 5 / Phase 6, schema.md §10-§13, R10-R24.
#
# Before a single byte is written into the bundle, every snapshot is re-read and both
# digests recomputed, every accepted claim's excerpt is re-sliced from `start:end`, the
# paper identifiers carried in the evidence are matched against the snapshot's `paper`
# metadata, and every user-supplied PDF is re-hashed against `asset.sha256`.
#
# All of that is delegated to `store.py` (D5); this module only decides what to do with
# the verdicts. Two gate policies apply:
#
#   * TAMPER_CODES block promotion unconditionally — gate or no gate (R20). A tampered
#     snapshot or a mismatched excerpt is never a rollout concern.
#   * everything else (unresolved artifacts, missing fresh fetches) blocks only when the
#     evidence-kernel gate is on. The gate is OFF by default (D7): a run with no
#     `outputs/result.json` at all is a pre-kernel run and still promotes, loudly labelled
#     as unverified, span-less evidence.

RESULT_FILENAME = "result.json"
RESULT_SCHEMA_VERSION = 1

# schema.md §13 reason codes that are integrity failures, not rollout state (R20).
TAMPER_CODES = (
    "RESULT_SCHEMA_ERROR",
    "UNKNOWN_SOURCE",
    "SNAPSHOT_HASH_MISMATCH",
    "SPAN_OUT_OF_RANGE",
    "SPAN_TOO_LONG",
    "EXCERPT_MISMATCH",
    "ASSET_HASH_MISMATCH",
    "NO_PAPER_ID",
)

UNVERIFIED_BANNER = (
    "PROMOTED UNVERIFIED: this run has no outputs/result.json, so no snapshot, span, "
    "excerpt or asset hash backs any promoted concept. Its evidence is agent-written and "
    "span-less (pre-kernel run; VALIDATION_ARCHITECTURE_PLAN.md D7).")


class IntegrityFinding:
    __slots__ = ("code", "artifact", "detail", "blocking")

    def __init__(self, code: str, artifact: str, detail: str, blocking: bool):
        self.code = code
        self.artifact = artifact
        self.detail = detail
        self.blocking = blocking

    def as_dict(self) -> dict:
        return {"reason_code": self.code, "artifact": self.artifact,
                "detail": self.detail, "blocking": self.blocking}


def _evidence_identifier(evidence_id: str) -> tuple[str | None, str | None]:
    """`pmid:12345678` -> ('pmid', '12345678'). Non-literature ids yield (None, None)."""
    if not isinstance(evidence_id, str) or ":" not in evidence_id:
        return None, None
    scheme, _, value = evidence_id.partition(":")
    scheme = scheme.strip().lower()
    if scheme not in ("pmid", "doi", "pmcid"):
        return None, None
    return scheme, value.strip()


class Preflight:
    """Publisher-side integrity checks over one run directory (Phase 5).

    Instantiates exactly one `store.Store` for the run and reuses it, so every snapshot is
    hashed once no matter how many spans point at it.
    """

    def __init__(self, run_dir: Path, wiki: Path, run: dict, gate: bool):
        self.run_dir = Path(run_dir)
        self.wiki = Path(wiki)
        self.run = run
        self.gate = bool(gate)
        self.result: dict | None = None
        self.result_path = self.run_dir / "outputs" / RESULT_FILENAME
        self.findings: list[IntegrityFinding] = []
        self.notes: list[str] = []
        self.store = None
        self.counts = {"snapshots_checked": 0, "spans_checked": 0, "assets_checked": 0,
                       "accepted": 0, "unresolved": 0, "artifacts_refused": 0}
        self.accepted_by_kind: dict[str, set] = {}
        self.refused: list[tuple[str, str, str]] = []   # (kind, evidence_id, reason)
        self.unverified = False

    # -- finding helpers --

    def fail(self, code: str, artifact: str, detail: str) -> None:
        self.findings.append(IntegrityFinding(code, artifact, detail, True))

    def note(self, code: str, artifact: str, detail: str) -> None:
        """Gate-controlled: blocking only when the evidence-kernel gate is on."""
        self.findings.append(IntegrityFinding(code, artifact, detail, self.gate))

    @property
    def blocking(self) -> list[IntegrityFinding]:
        return [f for f in self.findings if f.blocking]

    # -- entry point --

    def run_checks(self) -> None:
        if not self.result_path.exists():
            self.unverified = True
            self.notes.append(UNVERIFIED_BANNER)
            # Tamper is never a rollout concern: any snapshot present in a pre-kernel run
            # is still re-hashed, and a bad one still blocks.
            self._check_all_snapshots()
            return
        if _store is None:
            self.fail("RESULT_SCHEMA_ERROR", str(self.result_path),
                      "scripts/store.py could not be imported; the integrity preflight "
                      "cannot run and promotion fails closed")
            return
        try:
            result = read_json(self.result_path)
        except (OSError, json.JSONDecodeError) as exc:
            self.fail("RESULT_SCHEMA_ERROR", str(self.result_path),
                      f"outputs/{RESULT_FILENAME} is unreadable: {exc}")
            return
        if not isinstance(result, dict):
            self.fail("RESULT_SCHEMA_ERROR", str(self.result_path),
                      f"outputs/{RESULT_FILENAME} must be a JSON object (schema.md §13)")
            return
        self.result = result
        self._check_result_schema(result)
        if self.blocking:
            return
        self.store = _store.Store(self.run_dir, wiki_root=self.wiki)
        self._check_all_snapshots()
        self._check_result_sources(result)
        self._check_accepted(result)
        self._check_unresolved(result)

    # -- §13 structure --

    def _check_result_schema(self, result: dict) -> None:
        path = str(self.result_path)
        if result.get("schema_version") != RESULT_SCHEMA_VERSION:
            self.fail("RESULT_SCHEMA_ERROR", path,
                      f"schema_version must be {RESULT_SCHEMA_VERSION} "
                      f"(got {result.get('schema_version')!r})")
        for key, kind in (("accepted", list), ("sources", list),
                          ("diagnostics", dict), ("gate", dict)):
            if not isinstance(result.get(key), kind):
                self.fail("RESULT_SCHEMA_ERROR", path,
                          f"`{key}` is missing or is not a {kind.__name__} (schema.md §13)")
        slug = self.run.get("slug")
        if isinstance(result.get("run_slug"), str) and slug and result["run_slug"] != slug:
            self.fail("RESULT_SCHEMA_ERROR", path,
                      f"run_slug {result['run_slug']!r} does not belong to run {slug!r}")
        diagnostics = result.get("diagnostics")
        if isinstance(diagnostics, dict) and \
                not isinstance(diagnostics.get("unresolved"), list):
            self.fail("RESULT_SCHEMA_ERROR", path,
                      "diagnostics.unresolved is missing or is not a list "
                      "(an absent problem is an empty list, not a missing key)")

    # -- §10 snapshots --

    def _check_all_snapshots(self) -> None:
        if _store is None:
            return
        store = self.store or _store.Store(self.run_dir, wiki_root=self.wiki)
        self.store = store
        for source_id in store.list_snapshots():
            self.counts["snapshots_checked"] += 1
            verdict = store.verify_snapshot(source_id)
            if not verdict["ok"]:
                self.fail(verdict.get("reason_code") or "SNAPSHOT_HASH_MISMATCH",
                          source_id, verdict.get("detail") or "snapshot integrity failed")
                continue
            self._check_asset(source_id)

    def _check_asset(self, source_id: str) -> None:
        """User-supplied PDF bytes must still hash to the recorded asset digest."""
        try:
            snap = self.store.read_snapshot(source_id)
        except Exception as exc:                       # already reported by the caller
            self.fail("SNAPSHOT_HASH_MISMATCH", source_id, str(exc))
            return
        asset = snap.get("asset")
        if not asset:
            if snap.get("origin") == "user-supplied-pdf":
                self.fail("ASSET_HASH_MISMATCH", source_id,
                          "origin user-supplied-pdf with asset: null — there is no file to "
                          "hash, so the snapshot cannot be proven (schema.md §11)")
            return
        self.counts["assets_checked"] += 1
        path = self.wiki / asset["path"]
        if not path.is_file():
            self.fail("ASSET_HASH_MISMATCH", source_id,
                      f"asset file missing: {asset['path']}")
            return
        actual = _store.sha256_file(path)
        if actual != asset["sha256"]:
            self.fail("ASSET_HASH_MISMATCH", source_id,
                      f"{asset['path']} hashes to {actual}, snapshot records "
                      f"{asset['sha256']}")

    def _check_result_sources(self, result: dict) -> None:
        """`result.sources[]` is derived from snapshots; any drift is tampering."""
        for entry in result.get("sources") or []:
            if not isinstance(entry, dict):
                self.fail("RESULT_SCHEMA_ERROR", str(self.result_path),
                          "sources[] entry is not an object")
                continue
            source_id = entry.get("source_id")
            try:
                snap = self.store.read_snapshot(source_id)
            except Exception as exc:
                code = getattr(exc, "reason_code", None) or "UNKNOWN_SOURCE"
                self.fail(code, str(source_id), str(exc))
                continue
            for key in ("content_hash", "url", "access", "origin"):
                if entry.get(key) is not None and entry[key] != snap[key]:
                    self.fail("SNAPSHOT_HASH_MISMATCH", str(source_id),
                              f"result.json records {key}={entry[key]!r} but the snapshot "
                              f"says {snap[key]!r}; the snapshot wins (§13 derivation rule)")
            if entry.get("paper") is not None and entry["paper"] != snap["paper"]:
                self.fail("NO_PAPER_ID", str(source_id),
                          f"result.json records paper={entry['paper']!r} but the snapshot "
                          f"says {snap['paper']!r}")

    # -- §13 accepted artifacts --

    def _check_accepted(self, result: dict) -> None:
        for artifact in result.get("accepted") or []:
            if not isinstance(artifact, dict):
                self.fail("RESULT_SCHEMA_ERROR", str(self.result_path),
                          "accepted[] entry is not an object")
                continue
            self.counts["accepted"] += 1
            aid = str(artifact.get("artifact_id") or "?")
            kind = artifact.get("kind")
            evidence_id = artifact.get("evidence_id")
            claims = artifact.get("claims")
            if not isinstance(claims, list) or not claims:
                self.fail("RESULT_SCHEMA_ERROR", aid,
                          "accepted artifact carries no claims[]; an accepted artifact has "
                          "at least one resolved span (schema.md §13)")
                continue
            ok = self._check_claims(aid, claims)
            ok = self._check_paper(aid, artifact, evidence_id) and ok
            if ok and isinstance(kind, str) and isinstance(evidence_id, str):
                self.accepted_by_kind.setdefault(kind, set()).add(evidence_id)

    def _check_claims(self, aid: str, claims: list) -> bool:
        ok = True
        for i, claim in enumerate(claims):
            if not isinstance(claim, dict):
                self.fail("RESULT_SCHEMA_ERROR", aid, f"claims[{i}] is not an object")
                ok = False
                continue
            self.counts["spans_checked"] += 1
            # store.verify_span re-reads the snapshot, recomputes both digests, range- and
            # cap-checks the offsets and re-slices text[start:end], comparing it to the
            # excerpt carried in the record. Excerpts are re-sliced, never authored (P6).
            verdict = self.store.verify_span(claim, excerpt=claim.get("excerpt"))
            if not verdict["ok"]:
                self.fail(verdict.get("reason_code") or "EXCERPT_MISMATCH",
                          f"{aid} claims[{i}]",
                          verdict.get("detail") or "span did not resolve")
                ok = False
        return ok

    def _check_paper(self, aid: str, artifact: dict, evidence_id) -> bool:
        """P4/R14: identifiers in the evidence must match the snapshot's `paper` block."""
        scheme, value = _evidence_identifier(evidence_id)
        source_ids = artifact.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            source_ids = sorted({c.get("source_id") for c in artifact.get("claims") or []
                                 if isinstance(c, dict) and c.get("source_id")})
        merged: dict = {"pmid": None, "doi": None, "pmcid": None}
        ok = True
        for source_id in source_ids:
            try:
                snap = self.store.read_snapshot(source_id)
            except Exception as exc:
                code = getattr(exc, "reason_code", None) or "UNKNOWN_SOURCE"
                self.fail(code, aid, str(exc))
                ok = False
                continue
            paper = snap.get("paper") or {}
            if scheme and not any(paper.get(k) for k in ("pmid", "doi", "pmcid")):
                self.fail("NO_PAPER_ID", aid,
                          f"literature evidence_id {evidence_id!r} but snapshot "
                          f"{source_id} carries no pmid/doi/pmcid")
                ok = False
                continue
            if scheme:
                claimed = paper.get(scheme)
                if scheme == "doi":
                    same = str(claimed or "").lower() == str(value or "").lower()
                else:
                    same = str(claimed or "") == str(value or "")
                if not same:
                    self.fail("NO_PAPER_ID", aid,
                              f"evidence_id {evidence_id!r} disagrees with snapshot "
                              f"{source_id} paper.{scheme}={claimed!r}")
                    ok = False
            for key in merged:
                if paper.get(key):
                    if merged[key] and merged[key] != paper[key]:
                        self.fail("NO_PAPER_ID", aid,
                                  f"snapshots disagree on paper.{key}: "
                                  f"{merged[key]!r} vs {paper[key]!r}")
                        ok = False
                    merged[key] = merged[key] or paper[key]
        declared = artifact.get("paper")
        if isinstance(declared, dict):
            for key, want in merged.items():
                got = declared.get(key)
                if got and want and str(got) != str(want):
                    self.fail("NO_PAPER_ID", aid,
                              f"result.json paper.{key}={got!r} but the snapshots say "
                              f"{want!r}; paper is copied from the snapshot, never authored")
                    ok = False
        return ok

    # -- §13 diagnostics --

    def _check_unresolved(self, result: dict) -> None:
        unresolved = (result.get("diagnostics") or {}).get("unresolved") or []
        self.counts["unresolved"] = len(unresolved)
        for entry in unresolved:
            if not isinstance(entry, dict):
                continue
            code = str(entry.get("reason_code") or "NO_SPANS")
            aid = str(entry.get("artifact_id") or "?")
            detail = str(entry.get("detail") or "")
            if code in TAMPER_CODES:
                # The assembler already saw tampering. Never downgraded by the gate.
                self.fail(code, aid, detail or "assembler recorded an integrity failure")
            else:
                self.note(code, aid,
                          detail or "artifact was not accepted by the assembler")

    # -- what promotion may use --

    def filter_run(self) -> None:
        """Drop evidence the assembler did not accept (Phase 6).

        With `result.json` present, promotion is driven by accepted artifacts: an
        extraction or appraisal that is not in `accepted[]` may never back a promoted
        concept's evidence footnote, gate or no gate (R16). Study concepts are
        bibliographic records from `corpus.jsonl`, not claims, and are kept.
        """
        if self.result is None:
            return
        for key, kind in (("extractions", "extraction"), ("appraisals", "appraisal")):
            allowed = self.accepted_by_kind.get(kind, set())
            kept = {}
            for evidence_id, record in (self.run.get(key) or {}).items():
                if evidence_id in allowed:
                    kept[evidence_id] = record
                else:
                    self.refused.append((kind, evidence_id,
                                         f"not in result.json accepted[] as {kind}"))
            self.run[key] = kept
        self.counts["artifacts_refused"] = len(self.refused)

    # -- reporting --

    def report_markdown(self) -> str:
        lines = ["## Evidence integrity preflight", "",
                 f"- run: `{self.run_dir}`",
                 f"- result.json: "
                 + (f"`{self.result_path}`" if self.result is not None else "**absent**"),
                 f"- evidence-kernel gate: {'on' if self.gate else 'off (default, D7)'}",
                 f"- snapshots re-hashed: {self.counts['snapshots_checked']}",
                 f"- spans re-sliced: {self.counts['spans_checked']}",
                 f"- assets re-hashed: {self.counts['assets_checked']}",
                 f"- accepted artifacts: {self.counts['accepted']}",
                 f"- unresolved artifacts: {self.counts['unresolved']}",
                 f"- artifacts refused for promotion: {self.counts['artifacts_refused']}",
                 ""]
        if self.unverified:
            lines += [f"> **{UNVERIFIED_BANNER}**", ""]
        blocking = self.blocking
        lines.append(f"**Preflight: {'FAIL' if blocking else 'PASS'}**")
        lines.append("")
        if self.findings:
            lines += ["| reason_code | artifact | blocking | detail |",
                      "|---|---|---|---|"]
            for f in self.findings:
                detail = f.detail.replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {f.code} | `{f.artifact}` | "
                             f"{'yes' if f.blocking else 'no'} | {detail} |")
            lines.append("")
        else:
            lines += ["No integrity findings.", ""]
        if self.refused:
            lines += ["### Refused artifacts (not accepted by the assembler)", ""]
            for kind, evidence_id, reason in self.refused:
                lines.append(f"- `{kind}` `{evidence_id}` — {reason}")
            lines.append("")
        return "\n".join(lines)


# ------------------------------------------------- atomic bundle transaction ---


class TxFence(Fence):
    """A `Fence` that writes atomically and can undo the whole promotion.

    Every write goes to a sibling temp file created with `O_EXCL`, is fsynced, and is then
    moved into place with `os.replace` — so no reader ever sees a partial file. The prior
    bytes of every touched path (or the fact that it did not exist) are journaled, so a
    failure anywhere in the sequence restores the bundle to exactly what it was.

    The fence itself is unchanged: `check()` is inherited, so every write still has to be
    inside `<wiki>/research/**` or `<wiki>/assets/papers/**`, and `<wiki>/wiki/**` is still
    forbidden absolutely.
    """

    def __init__(self, wiki: Path):
        super().__init__(wiki)
        self._journal: list[tuple[Path, bytes | None]] = []
        self._seen: set[Path] = set()
        self._made_dirs: list[Path] = []

    def _record(self, resolved: Path) -> None:
        if resolved in self._seen:
            return
        self._seen.add(resolved)
        try:
            old = resolved.read_bytes()
        except FileNotFoundError:
            old = None
        self._journal.append((resolved, old))

    def mkdir(self, target: Path) -> Path:
        resolved = self.check(target / "_").parent
        self._track_dirs(resolved)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _track_dirs(self, directory: Path) -> None:
        missing = []
        cur = directory
        while not cur.exists() and cur.parent != cur:
            missing.append(cur)
            cur = cur.parent
        self._made_dirs.extend(reversed(missing))

    def write_text(self, target: Path, text: str) -> Path:
        resolved = self.check(target)
        self.check(resolved.parent / "_")
        self._record(resolved)
        self._track_dirs(resolved.parent)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        tmp = resolved.parent / f".{resolved.name}.okf-tmp-{os.getpid()}"
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(str(tmp), str(resolved))
        except BaseException:
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
            raise
        return resolved

    def rollback(self) -> int:
        """Restore every journaled path. Returns the number of paths restored."""
        n = 0
        for resolved, old in reversed(self._journal):
            try:
                if old is None:
                    if resolved.exists():
                        resolved.unlink()
                else:
                    resolved.write_bytes(old)
                n += 1
            except OSError:
                pass
        for directory in reversed(self._made_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        self._journal.clear()
        self._seen.clear()
        self._made_dirs.clear()
        return n


def build_shadow_wiki(wiki: Path, tmpdir: Path) -> Path:
    """A throwaway copy of the bundle used to validate a promotion before committing it.

    `research/` is copied for real; `assets/papers/` is mirrored as empty stub files,
    because the only thing V17 asks of a PDF is that it exists at the relative path a
    concept points at. Nothing here ever touches the real wiki.
    """
    shadow = Path(tmpdir) / "wiki"
    shadow.mkdir(parents=True, exist_ok=True)
    research = Path(wiki) / "research"
    if research.is_dir():
        shutil.copytree(str(research), str(shadow / "research"))
    papers = Path(wiki) / "assets" / "papers"
    if papers.is_dir():
        for src in papers.rglob("*"):
            dest = shadow / "assets" / "papers" / src.relative_to(papers)
            if src.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.touch()
    return shadow


def cmd_promote(args) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.is_dir():
        raise OkfError(f"run directory not found: {run_dir}")
    wiki = Path(args.wiki).expanduser()
    fence = Fence(wiki)
    research = ensure_bundle(fence)
    run = load_run(run_dir)
    now = utcnow()
    check_only = bool(getattr(args, "check", False))

    # --- Phase 5 preflight: nothing is written until this passes -----------------
    gate_flag = getattr(args, "gate", None)
    gate = bool(gate_flag) if gate_flag is not None else \
        bool((run["config"].get("gates") or {}).get("evidence_kernel"))
    preflight = Preflight(run_dir, fence.wiki, run, gate=gate)
    preflight.run_checks()
    if gate_flag is None and preflight.result is not None and not preflight.blocking \
            and bool((preflight.result.get("gate") or {}).get("enabled")) \
            and not preflight.gate:
        preflight.gate = True                 # result.json turns the gate on
        for f in preflight.findings:
            if not f.blocking and f.code not in TAMPER_CODES:
                f.blocking = True
    preflight.filter_run()
    if preflight.blocking:
        return finish_promote_failure(preflight, None, run_dir, fence, check_only,
                                      f"promote {run['slug']}")

    verification = run["verification"]
    if verification is None:
        if not args.allow_unverified:
            raise OkfError(
                f"no {run_dir / 'outputs' / 'verification.json'} — run scripts/verify.py "
                f"first, or pass --allow-unverified to promote as `provisional`")
        status = "provisional"
        verified_at = now
    else:
        failed = [c for c in verification.get("checks") or []
                  if c.get("status") == "fail"]
        if failed and not args.force:
            detail = "; ".join(f"{c.get('check_id')}: {c.get('detail')}" for c in failed)
            raise OkfError(
                "verifier reported failing checks — OKF promotion is blocked "
                f"(PLAN.md §5): {detail}")
        warned = any(c.get("status") == "warn" for c in verification.get("checks") or [])
        status = "provisional" if (failed or warned or verification.get("missing_fulltext")) \
            else "stable"
        verified_at = verification.get("verified_at") or now
        if not TS_RE.match(str(verified_at)):
            verified_at = now
    if args.status:
        status = args.status

    slug = run["slug"]
    if not SLUG_RE.match(slug):
        raise OkfError(f"run slug {slug!r} fails the slug rule (V22)")
    # Every concept is rendered in memory first: a build error here writes nothing.
    concepts = build_run_concepts(run, fence.wiki, research, now, verified_at, status)
    for concept in concepts:
        fence.check(concept.path(research))
        concept.render()

    validator_corpus = run["corpus"]
    log_notes = [f"- ⚠ {UNVERIFIED_BANNER}"] if preflight.unverified else []
    scope = f"promote {slug}"

    # --- dry validation against a throwaway shadow copy of the bundle ------------
    # The promotion is applied in full to a temp copy and validated there. Only a
    # completely clean prospective bundle is ever committed to the real wiki, so a
    # concept that is invalid deep in the sequence cannot leave a half-written bundle.
    shadow_dir = tempfile.mkdtemp(prefix="okf-promote-")
    try:
        shadow_wiki = build_shadow_wiki(fence.wiki, Path(shadow_dir))
        shadow_fence = Fence(shadow_wiki)
        shadow_research = shadow_fence.wiki / "research"
        write_concepts(shadow_fence, shadow_research, concepts, now, slug,
                       log=not args.no_log, log_notes=log_notes)
        validator = Validator(shadow_fence.wiki, corpus=validator_corpus)
        validator.run()
    finally:
        shutil.rmtree(shadow_dir, ignore_errors=True)

    if validator.violations:
        return finish_promote_failure(preflight, validator, run_dir, fence, check_only,
                                      scope)

    if check_only or args.dry_run:
        report = preflight.report_markdown() + "\n" + validator.report_markdown(scope=scope)
        if check_only:
            sys.stdout.write(report)          # --check writes nothing, anywhere
        else:
            write_report(Fence(wiki), run_dir / "outputs" / "okf-validation.md",
                         report, run_dir)
        print(json.dumps({
            "status": "check-ok" if check_only else "dry-run",
            "wrote": [],
            "concepts": len(concepts),
            "types": sorted({c.type for c in concepts}),
            "evidence": "unverified" if preflight.unverified else "verified",
            "gate": "on" if preflight.gate else "off",
            "preflight": {"findings": [f.as_dict() for f in preflight.findings],
                          **preflight.counts},
            "violations": 0,
        }, indent=2))
        if preflight.unverified:
            sys.stderr.write(f"WARNING: {UNVERIFIED_BANNER}\n")
        return 0

    # --- commit: atomic per file, journaled as a whole --------------------------
    tx = TxFence(fence.wiki)
    try:
        written = write_concepts(tx, research, concepts, now, slug,
                                 log=not args.no_log, log_notes=log_notes)
        post = Validator(fence.wiki, corpus=validator_corpus)
        post.run()
        if post.violations:                       # belt and braces; should not happen
            raise OkfError(
                f"post-write validation found {len(post.violations)} violation(s) the "
                f"shadow validation did not; the promotion was rolled back")
    except BaseException as exc:
        restored = tx.rollback()
        report = (preflight.report_markdown() + "\n"
                  + f"## Promotion aborted\n\n{exc}\n\n"
                    f"Rolled back {restored} path(s); the bundle is unchanged.\n")
        report_path = run_dir / "outputs" / "okf-validation.md"
        write_report(Fence(wiki), report_path, report, run_dir)
        sys.stderr.write(f"okf.py: promotion aborted and rolled back: {exc}\n")
        if isinstance(exc, (OkfError, OSError)):
            return 1
        raise

    report = preflight.report_markdown() + "\n" + post.report_markdown(scope=scope)
    report_path = run_dir / "outputs" / "okf-validation.md"
    write_report(Fence(wiki), report_path, report, run_dir)
    print(json.dumps({
        "status": "ok",
        "concepts": len(written),
        "types": sorted({c.type for c in concepts}),
        "validation_report": str(report_path),
        "evidence": "unverified" if preflight.unverified else "verified",
        "gate": "on" if preflight.gate else "off",
        "preflight": {"findings": [f.as_dict() for f in preflight.findings],
                      **preflight.counts},
        "violations": 0,
    }, indent=2))
    if preflight.unverified:
        sys.stderr.write(f"WARNING: {UNVERIFIED_BANNER}\n")
    return 0


def finish_promote_failure(preflight: "Preflight", validator: "Validator | None",
                           run_dir: Path, fence: Fence, check_only: bool,
                           scope: str) -> int:
    """Fail closed for promotion; preserve every run output.

    Nothing has been written into the bundle at this point, and nothing will be. In
    `--check` mode nothing at all is written — not even the report. Otherwise the
    diagnostics land in `<run>/outputs/okf-validation.md`, next to the untouched
    `outputs/report.md` and the rest of the run's artifacts.
    """
    report = preflight.report_markdown()
    if validator is not None:
        report += "\n" + validator.report_markdown(scope=scope)
    report_path = run_dir / "outputs" / "okf-validation.md"
    if not check_only:
        write_report(Fence(fence.wiki), report_path, report, run_dir)
    else:
        sys.stdout.write(report)
    blocking = preflight.blocking
    print(json.dumps({
        "status": "check-failed" if check_only else "validation-failed",
        "wrote": [],
        "bundle": "unchanged",
        "evidence": "unverified" if preflight.unverified else "verified",
        "gate": "on" if preflight.gate else "off",
        "preflight": {"findings": [f.as_dict() for f in preflight.findings],
                      **preflight.counts},
        "violations": len(validator.violations) if validator is not None else 0,
        "validation_report": None if check_only else str(report_path),
    }, indent=2))
    detail = "; ".join(f"{f.code} {f.artifact}" for f in blocking) or \
        (f"{len(validator.violations)} V-rule violation(s)" if validator else "")
    sys.stderr.write(
        f"OKF promotion BLOCKED: {detail}. Nothing was written into the bundle; "
        f"run outputs are untouched"
        + ("." if check_only else f" and diagnostics are at {report_path}.") + "\n")
    return 1


def write_report(fence: Fence, path: Path, text: str, run_dir: Path | None) -> None:
    try:
        if run_dir is not None:
            fence.allow_run_dir(run_dir)
        fence.write_text(path, text)
    except FenceError:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def build_run_concepts(run: dict, wiki: Path, research: Path, now: str,
                       verified_at: str, status: str) -> list[Concept]:
    slug = run["slug"]
    question = run["question"]
    corpus = run["corpus"]
    included = [r for r in corpus
                if (r.get("screening") or {}).get("decision") == "include"]
    if not included:
        included = corpus
    concepts: list[Concept] = []

    def mk(ctype, cslug, title, description, sources, body, extra=None, tags=None):
        front = base_front(ctype, title, description, f"./{cslug}.md", tags or [slug],
                           now, verified_at, status, sources, extra=extra)
        return Concept(ctype, cslug, front, body)

    # --- Study concepts -------------------------------------------------------
    study_slugs: dict[str, str] = {}
    for record in included:
        extraction = run["extractions"].get(record.get("evidence_id"))
        concept = study_concept(record, wiki, now, verified_at, status,
                                extraction=extraction, tags=[slug])
        concepts.append(concept)
        study_slugs[record.get("evidence_id") or evidence_slug(record)] = concept.slug

    def study_links(records: list[dict]) -> str:
        """Markdown links to Study concepts; abstract-only evidence is labelled (V25)."""
        bits = []
        for rec in records:
            eid = rec.get("evidence_id") or evidence_slug(rec)
            cslug = study_slugs.get(eid)
            if not cslug:
                continue
            note = (" (abstract only)"
                    if (rec.get("fulltext") or {}).get("status") == "abstract_only" else "")
            bits.append(f"- [{rec.get('title') or cslug}](../studies/{cslug}.md){note}")
        return "\n".join(bits)

    # --- Review ---------------------------------------------------------------
    review_path = research / "reviews" / f"{slug}.md"
    review_sources, review_labels = collect_sources(included, review_path, wiki)
    report_body = strip_frontmatter_body(run["report_md"])
    body_lines = [f"# Review: {question}", ""]
    body_lines.append(
        f"Evidence review covering {len(included)} included record(s) from run `{slug}`."
        f"[^{review_sources[0]['id']}]" if review_sources else "")
    body_lines += ["", "## Bundle concepts", "",
                   f"- [Protocol](../protocols/{slug}.md)",
                   f"- [Search Strategy](../searches/{slug}.md)", "",
                   "## Included studies", "", study_links(included), ""]
    if report_body:
        body_lines += ["## Report", "", report_body, ""]
    body_lines.append(footnote_block(review_sources, review_labels))
    review_extra = {"run_slug": slug,
                    "evidence_ids": [r.get("evidence_id") for r in included
                                     if r.get("evidence_id")]}
    concepts.append(mk("Review", slug, f"Review: {question}",
                       first_sentence(f"Evidence review of {question} produced by the "
                                      f"deep-research run {slug}."),
                       review_sources, "\n".join(body_lines), extra=review_extra))

    # --- Protocol -------------------------------------------------------------
    protocol_sources = [{"id": f"review-{slug}", "resource": f"../reviews/{slug}.md",
                         "title": f"Review: {question}"}]
    protocol_body = strip_frontmatter_body(run.get("protocol_md", ""))
    body = [f"# Protocol: {question}", "",
            f"Protocol for run `{slug}`.[^review-{slug}]", ""]
    if protocol_body:
        body += [protocol_body, ""]
    body += [f"See the [Review](../reviews/{slug}.md) and "
             f"[Search Strategy](../searches/{slug}.md).", "",
             footnote_block(protocol_sources)]
    concepts.append(mk("Protocol", slug, f"Protocol: {question}",
                       first_sentence(f"PICO/PECO, eligibility criteria and limits for "
                                      f"{question}."),
                       protocol_sources, "\n".join(body), extra={"run_slug": slug}))

    # --- Search Strategy ------------------------------------------------------
    search_sources = [{"id": "pubmed", "resource": "https://pubmed.ncbi.nlm.nih.gov/",
                       "title": "PubMed"},
                      {"id": f"review-{slug}", "resource": f"../reviews/{slug}.md",
                       "title": f"Review: {question}"}]
    body = [f"# Search Strategy: {question}", "",
            f"Queries executed for run `{slug}`.[^pubmed]", "",
            "| query id | source | hits | executed at |", "|---|---|---|---|"]
    for search in run["searches"]:
        body.append(f"| {search.get('query_id')} | {search.get('source')} | "
                    f"{search.get('count')} | {search.get('executed_at')} |")
    if not run["searches"]:
        body.append("| — | — | — | — |")
    body += ["", "### Queries", ""]
    for search in run["searches"]:
        body.append(f"- `{search.get('query_id')}`: `{search.get('query_string')}`")
        if search.get("translated_query"):
            body.append(f"  - translated: `{search['translated_query']}`")
    body += ["", f"See the [Protocol](../protocols/{slug}.md).", "",
             footnote_block(search_sources)]
    concepts.append(mk("Search Strategy", slug, f"Search strategy: {question}",
                       first_sentence(f"Databases, queries, translations and hit counts "
                                      f"for {question}."),
                       search_sources, "\n".join(body), extra={"run_slug": slug}))

    # --- PICO concepts --------------------------------------------------------
    for ctype, key in (("Population", "population"), ("Intervention", "intervention"),
                       ("Comparator", "comparator")):
        groups: dict[str, list[dict]] = {}
        for record in included:
            extraction = run["extractions"].get(record.get("evidence_id"))
            value = (extraction or {}).get(key)
            if not value:
                continue
            groups.setdefault(str(value), []).append(record)
        for value, records in groups.items():
            cslug = slugify(value, 72)[:72] or slugify(f"{key}-{slug}")
            cslug = f"{cslug}"
            path = research / TYPE_DIRS[ctype] / f"{cslug}.md"
            sources, labels = collect_sources(records, path, wiki)
            primary = sources[0]["id"]
            body = [f"# {ctype}: {first_sentence(value, 90)}", "",
                    f"{value}[^{primary}]", "", "## Reported by", "",
                    study_links(records), "", footnote_block(sources, labels)]
            extra = {"evidence_ids": [r.get("evidence_id") for r in records
                                      if r.get("evidence_id")]}
            concepts.append(mk(ctype, cslug, first_sentence(value, 100),
                               first_sentence(value), sources, "\n".join(body),
                               extra=extra))

    # --- Outcomes + Evidence Claims ------------------------------------------
    outcome_groups: dict[str, list[tuple[dict, dict]]] = {}
    for record in included:
        extraction = run["extractions"].get(record.get("evidence_id"))
        for out in (extraction or {}).get("outcomes") or []:
            if not out.get("name"):
                continue
            outcome_groups.setdefault(str(out["name"]), []).append((record, out))
    for name, pairs in outcome_groups.items():
        records = [p[0] for p in pairs]
        cslug = slugify(name, 72)
        opath = research / "outcomes" / f"{cslug}.md"
        sources, labels = collect_sources(records, opath, wiki)
        primary = sources[0]["id"]
        extra = {"evidence_ids": [r.get("evidence_id") for r in records
                                  if r.get("evidence_id")]}
        body = [f"# Outcome: {name}", "",
                f"Outcome construct `{name}` as reported across "
                f"{len(records)} study/studies.[^{primary}]", "",
                "## Reported by", "", study_links(records), "",
                f"See the [Evidence Claim](../claims/{cslug}.md).", "",
                footnote_block(sources, labels)]
        concepts.append(mk("Outcome", cslug, f"Outcome: {name}",
                           first_sentence(f"Outcome construct and instrument: {name}."),
                           sources, "\n".join(body), extra=extra))

        cpath = research / "claims" / f"{cslug}.md"
        csources, clabels = collect_sources(records, cpath, wiki)
        lines = [f"# Evidence claim: {name}", ""]
        for record, out in pairs:
            eid = record.get("evidence_id")
            basis = (record.get("fulltext") or {}).get("status")
            note = " (abstract only)" if basis == "abstract_only" else ""
            src_id = source_for_record(record, cpath, wiki)[0]["id"]
            lines.append(f"- {outcome_sentence(out)}{note}[^{src_id}]")
        lines += ["", "## Contributing studies", "", study_links(records), "",
                  f"See the [Outcome](../outcomes/{cslug}.md).", "",
                  footnote_block(csources, clabels)]
        abstract_only = any((r.get("fulltext") or {}).get("status") == "abstract_only"
                            for r in records)
        cextra = dict(extra)
        if abstract_only:
            cextra["evidence_basis"] = "abstract_only"
        concepts.append(mk("Evidence Claim", cslug, f"Evidence claim: {name}",
                           first_sentence(f"What the included evidence reports for {name}."),
                           csources, "\n".join(lines), extra=cextra))

    # --- Appraisals -----------------------------------------------------------
    for eid, appraisal in run["appraisals"].items():
        record = next((r for r in corpus if r.get("evidence_id") == eid), None)
        if record is None:
            continue
        cslug = f"{evidence_slug(record)}-{slugify(appraisal.get('tool') or 'appraisal')}"
        cslug = cslug[:80].strip("-")
        apath = research / "appraisals" / f"{cslug}.md"
        sources, labels = collect_sources([record], apath, wiki)
        primary = sources[0]["id"]
        lines = [f"# Appraisal: {record.get('title') or eid}", "",
                 f"{appraisal.get('tool')} appraisal; overall judgement "
                 f"**{appraisal.get('overall_judgement')}**.[^{primary}]", "",
                 "| domain | judgement | rationale |", "|---|---|---|"]
        for dom in appraisal.get("domains") or []:
            lines.append(f"| {dom.get('domain')} | {dom.get('judgement')} | "
                         f"{str(dom.get('rationale') or '').replace('|', '\\|')} |")
        grade = appraisal.get("grade")
        if grade:
            lines += ["", "### GRADE", ""]
            for key, value in grade.items():
                lines.append(f"- {key.replace('_', ' ')}: {value}")
        note = " (abstract only)" if appraisal.get("evidence_basis") == "abstract_only" else ""
        lines += ["", f"Appraised study: "
                      f"[{record.get('title') or eid}]"
                      f"(../studies/{study_slugs.get(eid, evidence_slug(record))}.md)"
                      f"{note}", "", footnote_block(sources, labels)]
        extra = {"evidence_id": eid, "tool": appraisal.get("tool"),
                 "overall_judgement": appraisal.get("overall_judgement")}
        if appraisal.get("evidence_basis"):
            extra["evidence_basis"] = appraisal["evidence_basis"]
        if (appraisal.get("grade") or {}).get("certainty"):
            extra["certainty"] = appraisal["grade"]["certainty"]
        concepts.append(mk("Appraisal", cslug,
                           f"Appraisal: {record.get('title') or eid}",
                           first_sentence(f"{appraisal.get('tool')} risk-of-bias and GRADE "
                                          f"judgement for {record.get('title') or eid}."),
                           sources, "\n".join(lines), extra=extra))

    # --- Evidence gaps --------------------------------------------------------
    gap_bullets = report_section(run["report_md"], "gap")
    missing = [r for r in corpus
               if (r.get("fulltext") or {}).get("status") == "missing"]
    if missing:
        gap_bullets.append(
            f"{len(missing)} included record(s) had no obtainable full text and were "
            f"quarantined; conclusions rest on the remainder.")
    for i, text in enumerate(gap_bullets, 1):
        cslug = f"{slug}-gap-{i:02d}"
        gpath = research / "gaps" / f"{cslug}.md"
        sources = [{"id": f"review-{slug}", "resource": relpath(review_path, gpath),
                    "title": f"Review: {question}"}]
        body = [f"# Evidence gap {i:02d}: {first_sentence(text, 80)}", "",
                f"{text}[^review-{slug}]", "",
                f"Identified during [{question}](../reviews/{slug}.md).", "",
                footnote_block(sources)]
        concepts.append(mk("Evidence Gap", cslug, first_sentence(text, 100),
                           first_sentence(text), sources, "\n".join(body),
                           extra={"run_slug": slug}))

    # --- Hypotheses -----------------------------------------------------------
    for i, text in enumerate(report_section(run["report_md"], "hypothes"), 1):
        cslug = f"{slug}-hypothesis-{i:02d}"
        hpath = research / "hypotheses" / f"{cslug}.md"
        hsources, hlabels = collect_sources(included, hpath, wiki)
        hsources = hsources[:8]
        body = [f"# Hypothesis {i:02d}", "",
                "**This is a generated hypothesis, not an established finding.**", "",
                f"{text}", "", "## Generated from", "",
                "Generated by the deep-research synthesis step from the evidence below; "
                "no included study tests this statement directly."
                f"[^{hsources[0]['id']}]" if hsources else "", "",
                study_links(included), "",
                f"See the [Review](../reviews/{slug}.md).", "",
                footnote_block(hsources, hlabels)]
        concepts.append(mk("Hypothesis", cslug, first_sentence(text, 100),
                           first_sentence(f"Generated hypothesis (not established "
                                          f"evidence): {text}"),
                           hsources, "\n".join(body), extra={"run_slug": slug}))

    # --- Source Documents -----------------------------------------------------
    for record in included:
        if record.get("source") in (None, "pubmed", "europepmc"):
            continue
        cslug = slugify(record.get("title") or record.get("evidence_id") or "source", 72)
        spath = research / "source-documents" / f"{cslug}.md"
        sources, labels = collect_sources([record], spath, wiki)
        primary = sources[0]["id"]
        extra = {"evidence_id": record.get("evidence_id"),
                 "accessed_at": now,
                 "publisher": record.get("publisher"),
                 "document_type": record.get("source"),
                 "retraction_status": record.get("retraction_status") or "none"}
        if record.get("is_preprint"):
            extra["is_preprint"] = True
        body = [f"# {record.get('title') or cslug}", "",
                f"{record.get('source')} source used as evidence in run "
                f"`{slug}`.[^{primary}]", "",
                f"See the [Review](../reviews/{slug}.md).", "",
                footnote_block(sources, labels)]
        concepts.append(mk("Source Document", cslug, record.get("title") or cslug,
                           first_sentence(f"{record.get('source')} source document used "
                                          f"in {question}."),
                           sources, "\n".join(body), extra=extra))

    return concepts


# ------------------------------------------------------------- validate --------


def cmd_validate(args) -> int:
    wiki = Path(args.wiki).expanduser()
    corpus = None
    if args.corpus:
        corpus = read_jsonl(Path(args.corpus).expanduser())
    elif args.run_dir:
        corpus = read_jsonl(Path(args.run_dir).expanduser() / "corpus.jsonl")
    validator = Validator(wiki, corpus=corpus)
    only = Path(args.path).expanduser().resolve() if args.path else None
    validator.run(only=only)
    scope = str(only) if only else "whole bundle"
    report = validator.report_markdown(scope=scope)
    if args.report:
        run_dir = Path(args.run_dir).expanduser() if args.run_dir else None
        write_report(Fence(wiki), Path(args.report).expanduser(), report, run_dir)
    if args.json:
        print(json.dumps({
            "okf_validation": "fail" if validator.violations else "pass",
            "files_checked": validator.files_checked,
            "violations": [v.as_dict() for v in validator.violations],
            "rules_skipped": sorted(set(validator.skipped_rules)),
        }, indent=2))
    else:
        sys.stdout.write(report)
    return 1 if validator.violations else 0


# ------------------------------------------------------------- selftest --------


SELFTEST_DOC = {
    "type": "Study",
    "title": 'A trial: "quoted", with #hash and colon: inside',
    "description": "One sentence.",
    "resource": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
    "tags": ["deep-research", "study", "rct"],
    "generated": {"by": GENERATED_BY, "at": "2026-09-08T00:00:00Z"},
    "verified": {"by": VERIFIED_BY, "at": "2026-09-08T00:00:00Z"},
    "status": "stable",
    "pmid": "0012345678",
    "doi": "10.1000/example",
    "pmcid": "PMC1234567",
    "authors": [{"family": "Smith", "given": "Jane A", "initials": "JA",
                 "affiliation": "Dept of Example, Example University",
                 "collective": None},
                {"family": None, "given": None, "initials": None,
                 "affiliation": None, "collective": "The Example Group"}],
    "journal": {"title": "Journal of Example Medicine", "iso_abbrev": "J Example Med",
                "issn": "1234-5678"},
    "publication_date": "2024-06-15",
    "epub_date": "2024-05-20",
    "volume": "07",
    "issue": "3",
    "pages": "101-115",
    "abstract": "Line one.\nLine two with a \"quote\" and a backslash \\ here.",
    "article_types": ["Randomized Controlled Trial", "Journal Article"],
    "mesh_terms": [],
    "keywords": ["adolescents", "remission"],
    "grants": [{"id": "R01-EXAMPLE", "agency": "National Institutes of Health"}],
    "publication_status": "ppublish",
    "retraction_status": "none",
    "is_preprint": False,
    "sources": [{"id": "pubmed-12345678",
                 "resource": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
                 "title": "PubMed record"}],
}


def cmd_selftest(args) -> int:
    failures = []
    text = yaml_dump(SELFTEST_DOC)
    parsed = yaml_loads(text)
    for key, expected in SELFTEST_DOC.items():
        got = parsed.get(key)
        if key in ("publication_date", "epub_date") and isinstance(got, _dt.date):
            got = got.isoformat()
        if got != expected:
            failures.append(f"{key}: expected {expected!r}, got {got!r}")
    for key in ("pmid", "doi", "pmcid", "volume", "issue", "pages"):
        if not isinstance(parsed.get(key), str):
            failures.append(f"{key} did not survive as a string (V13)")
    extra = set(parsed) - set(SELFTEST_DOC)
    if extra:
        failures.append(f"unexpected keys after round-trip: {sorted(extra)}")
    # nested empty collections and nulls
    edge = {"a": [], "b": {}, "c": None, "d": "null", "e": "2024-01-01", "f": "3.14",
            "g": ["x, y", "[z]"], "h": "  padded  ", "i": "-leading"}
    edge_parsed = yaml_loads(yaml_dump(edge))
    if edge_parsed != edge:
        failures.append(f"edge-case round-trip failed: {edge_parsed!r}")
    doc = render_document(SELFTEST_DOC, "# Body\n\ntext[^pubmed-12345678]")
    fm, _body = split_frontmatter(doc)
    if fm is None:
        failures.append("split_frontmatter failed on a rendered document")
    elif yaml_loads(fm).get("pmid") != "0012345678":
        failures.append("frontmatter round-trip through render_document failed")
    if failures:
        for line in failures:
            sys.stderr.write(f"selftest FAIL: {line}\n")
        return 1
    print("selftest OK: yaml subset round-trips (%d keys, %d edge cases)"
          % (len(SELFTEST_DOC), len(edge)))
    return 0


# ------------------------------------------------------------------ cli --------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="okf.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create <wiki>/research/ with index.md, log.md, taxonomy")
    s.add_argument("--wiki", required=True, help="wiki root directory")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("write", help="write one concept from a JSON input file")
    s.add_argument("--wiki", required=True)
    s.add_argument("--type", required=True,
                   help="concept type: " + ", ".join(sorted(TYPE_DIRS)))
    s.add_argument("--from", dest="source", required=True,
                   help="JSON file: a corpus record and/or a concept spec")
    s.add_argument("--slug", help="slug override (not allowed for Study, see V11)")
    s.add_argument("--status", default="stable", choices=list(STATUSES))
    s.add_argument("--generated-at", dest="generated_at", help="ISO-8601 UTC Z")
    s.add_argument("--verified-at", dest="verified_at", help="ISO-8601 UTC Z")
    s.add_argument("--run-slug", dest="run_slug", help="run slug recorded in log.md")
    s.add_argument("--no-log", action="store_true", help="do not append to log.md")
    s.add_argument("--dry-run", action="store_true", help="render but do not write")
    s.set_defaults(func=cmd_write)

    s = sub.add_parser("promote", help="promote a completed run directory into the bundle")
    s.add_argument("--run-dir", dest="run_dir", required=True)
    s.add_argument("--wiki", required=True)
    s.add_argument("--status", choices=list(STATUSES),
                   help="override the status derived from outputs/verification.json")
    s.add_argument("--allow-unverified", action="store_true",
                   help="promote without outputs/verification.json (status: provisional)")
    s.add_argument("--force", action="store_true",
                   help="promote despite failing verifier checks (not recommended)")
    s.add_argument("--check", action="store_true",
                   help="validation-only preflight: run every integrity and V-rule check "
                        "and write NOTHING, anywhere")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--gate", dest="gate", action="store_true", default=None,
                   help="evidence-kernel gate: unresolved artifacts block promotion "
                        "(default: off, see plan decision D7)")
    g.add_argument("--no-gate", dest="gate", action="store_false",
                   help="force the evidence-kernel gate off even if config/result.json "
                        "asks for it (tamper still blocks unconditionally)")
    s.add_argument("--no-log", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_promote)

    s = sub.add_parser("validate", help="enforce V1..V25; non-zero exit on any violation")
    s.add_argument("--wiki", required=True)
    s.add_argument("--path", help="validate a single concept file instead of the bundle")
    s.add_argument("--corpus", help="corpus.jsonl enabling V12 and V24")
    s.add_argument("--run-dir", dest="run_dir",
                   help="run directory; supplies corpus.jsonl and allows --report inside it")
    s.add_argument("--report", help="write the markdown report here "
                                    "(e.g. <run>/outputs/okf-validation.md)")
    s.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("selftest", help="round-trip the built-in YAML serializer/parser")
    s.set_defaults(func=cmd_selftest)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OkfError as exc:
        sys.stderr.write(f"okf.py: error: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
