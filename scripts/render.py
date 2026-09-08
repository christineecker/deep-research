#!/usr/bin/env python3
"""render.py — report.md -> refs.bib + report.qmd -> `quarto render` (pdf/docx).

Stage 8 output path of `PLAN.md` §5 / §8 Phase 5. Implements the contracts in
`references/schema.md` §4 (corpus record) and its Resolution **R6** (BibTeX citation
key = `evidence_id` with every non-alphanumeric character stripped:
`pmid:12345678` -> `pmid12345678`), plus the citation rules of
`references/reporting.md` §2.

Environment: python3 3.14, stdlib only. No third-party imports, no pip, no network.
External binaries used through subprocess: `quarto` (optional; a missing binary is a
clean error, never a traceback).

Subcommands
  bib   --corpus corpus.jsonl --out refs.bib
        BibTeX from corpus records. Deterministic order (by citation key), correct
        escaping of & % $ # _ { } ~ ^ \\, brace-protected capitals in titles.

  qmd   --report report.md --corpus corpus.jsonl --out report.qmd [--template t.qmd]
        Builds the Quarto YAML header and rewrites markdown footnote attributions
        (keyed to `sources[].id`, per reporting.md §2) into `[@<R6-key>]` citations.
        A footnote key that does not resolve to a corpus record is left **in place,
        untouched**, and reported as a warning. Never guessed, never dropped.

  pdf | docx  --qmd report.qmd [--out-dir outputs/]
        Shells out to `quarto render --to <fmt>`. On failure: `outputs/report.md` is
        preserved, nothing is deleted, the failure is appended to the run's
        `engine.log`, a JSON error object goes to stdout and the exit code is non-zero
        (`PLAN.md` §5 "Failure semantics").

  all   --run-dir <dir> [--formats pdf,docx]
        One-shot bib -> qmd -> render over a run directory. Resumable and idempotent:
        a step whose output is newer than all of its inputs is skipped unless --force.

Exit codes
  0 ok (warnings may still be present)   2 user error      3 state error
  4 external render failure              5 required binary missing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import now_iso  # noqa: E402  (sibling module, stdlib-only)

SCHEMA_VERSION = 1
TOOL = "render.py/0.1"
DEFAULT_BIB_NAME = "refs.bib"


class UserError(Exception):
    """Bad invocation or bad input file."""


class StateError(Exception):
    """The data violates a contract that must hold by construction."""


class RenderError(Exception):
    """An external renderer failed. Carries the captured output."""

    def __init__(self, message: str, *, detail: str = "", returncode: int | None = None):
        super().__init__(message)
        self.detail = detail
        self.returncode = returncode


# --------------------------------------------------------------------------- io

def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def _umask() -> int:
    cur = os.umask(0o022)
    os.umask(cur)
    return cur


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o666 & ~_umask())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_corpus(path: Path) -> list[dict]:
    """Read corpus.jsonl. Malformed lines are a user error, not a silent skip."""
    if not path.exists():
        raise UserError(f"corpus not found: {path}")
    records: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise UserError(f"{path}:{lineno}: invalid JSON: {exc}") from None
            if not isinstance(obj, dict):
                raise UserError(f"{path}:{lineno}: expected a JSON object")
            if obj.get("schema_version") != SCHEMA_VERSION:
                raise UserError(
                    f"{path}:{lineno}: schema_version {obj.get('schema_version')!r} "
                    f"!= {SCHEMA_VERSION} (schema.md S1)"
                )
            if not obj.get("evidence_id"):
                raise UserError(f"{path}:{lineno}: record has no evidence_id (schema.md S9)")
            records.append(obj)
    return records


def find_run_dir(start: Path) -> Path | None:
    """Best-effort location of the run directory that owns `start`.

    Run layout (PLAN.md §7): <run>/outputs/report.{md,qmd}. A file sitting directly in
    an `outputs/` directory belongs to that directory's parent.
    """
    p = start if start.is_dir() else start.parent
    p = p.resolve()
    if p.name == "outputs":
        return p.parent
    for cand in (p, *p.parents):
        if (cand / "outputs").is_dir() and (
            (cand / "config.json").exists() or (cand / "corpus.jsonl").exists()
        ):
            return cand
    return None


def engine_log(run_dir: Path | None, message: str) -> None:
    """Append one diagnostic line to the run's engine.log (PLAN.md §5)."""
    if run_dir is None:
        return
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        with (run_dir / "engine.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {TOOL} {message}\n")
    except OSError as exc:  # logging must never mask the original failure
        warn(f"could not write engine.log: {exc}")


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


# ------------------------------------------------------------------- R6 bib keys

def bib_key(evidence_id: str) -> str:
    """schema.md R6: evidence_id with all non-alphanumeric characters stripped.

    pmid:12345678       -> pmid12345678
    doi:10.1000/ex-1    -> doi101000ex1
    pmcid:PMC1234567    -> pmcidPMC1234567
    Case is preserved; only ASCII alphanumerics survive.
    """
    key = re.sub(r"[^0-9A-Za-z]+", "", evidence_id or "")
    if not key:
        raise StateError(f"evidence_id {evidence_id!r} yields an empty citation key (R6)")
    return key


# ------------------------------------------------------------------- bibtex text

_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def bib_escape(text: str) -> str:
    """Escape the ten TeX-special characters. Backslash first, hence the char loop."""
    out = []
    for ch in text:
        out.append(_ESCAPES.get(ch, ch))
    return "".join(out)


_WORD_CHARS = re.compile(r"[0-9A-Za-z]")


def _needs_brace(token: str, *, first: bool) -> bool:
    """True when a title token's capitalisation must be protected from a style's
    title-casing: acronyms, proper nouns, mixed-case words (mRNA, PubMed), and any
    capital that is not simply the leading letter of the very first word."""
    core = token.strip("([{\"'“‘")
    if not core:
        return False
    if not any(c.isupper() for c in core):
        return False
    if first:
        # "Cognitive behavioral ..." — the leading capital is expected. Protect only
        # when there is a further capital (an acronym or mixed case).
        rest = core[1:]
        return any(c.isupper() for c in rest)
    return True


def bib_title(title: str) -> str:
    """Escape a title and brace-protect its capitalised words.

    Protection wraps the escaped token, keeping leading/trailing punctuation outside the
    braces so that `(CBT),` becomes `({CBT}),`.
    """
    parts: list[str] = []
    first_word_seen = False
    for token in re.split(r"(\s+)", title):
        if not token or token.isspace():
            parts.append(bib_escape(token) if token else token)
            continue
        m = re.match(r"^(\W*)(.*?)(\W*)$", token, flags=re.DOTALL)
        lead, core, trail = m.group(1), m.group(2), m.group(3)
        is_first = not first_word_seen
        if _WORD_CHARS.search(core):
            first_word_seen = True
        if core and _needs_brace(core, first=is_first):
            parts.append(bib_escape(lead) + "{" + bib_escape(core) + "}" + bib_escape(trail))
        else:
            parts.append(bib_escape(token))
    return "".join(parts)


_INITIALS = re.compile(r"^[A-Z][A-Z-]{0,4}$")


def bib_author_name(raw) -> str | None:
    """Render one corpus author as BibTeX `Family, Given`.

    Accepts the corpus `"Family GI"` string form (schema.md §4) and the structured
    `{family, given, initials, collective}` form used by the OKF bibliographic block.
    Collective/corporate authors are emitted as a single brace-protected literal so
    BibTeX does not split them into given/family.
    """
    if isinstance(raw, dict):
        collective = raw.get("collective")
        if collective:
            return "{" + bib_escape(str(collective).strip()) + "}"
        family = (raw.get("family") or "").strip()
        given = (raw.get("given") or raw.get("initials") or "").strip()
        if not family and not given:
            return None
        if not family:
            return "{" + bib_escape(given) + "}"
        return f"{bib_escape(family)}, {bib_escape(given)}".rstrip(", ").rstrip()

    name = str(raw or "").strip()
    if not name:
        return None
    tokens = name.split()
    if len(tokens) >= 2 and _INITIALS.fullmatch(tokens[-1]):
        family = " ".join(tokens[:-1])
        initials = tokens[-1]
        given = " ".join(f"{c}." for c in initials if c.isalpha())
        return f"{bib_escape(family)}, {bib_escape(given)}"
    # No trailing initials: a collective/corporate author ("WHO Study Group") or a
    # single-token name. Brace it rather than guessing a family/given split.
    return "{" + bib_escape(name) + "}"


def bib_authors(record: dict) -> str | None:
    authors = record.get("authors_structured") or record.get("authors") or []
    names = [n for n in (bib_author_name(a) for a in authors) if n]
    return " and ".join(names) if names else None


# ------------------------------------------------------------------ bib entries

def _year_month(record: dict) -> tuple[str | None, str | None]:
    pub = record.get("publication_date")
    if not pub:
        return None, None
    m = re.match(r"^(\d{4})(?:-(\d{2}))?", str(pub))
    if not m:
        return None, None
    return m.group(1), (str(int(m.group(2))) if m.group(2) else None)


def _pages(record: dict) -> str | None:
    pages = record.get("pages")
    if not pages:
        return None
    pages = str(pages).strip()
    # BibTeX page ranges use an en-dash: 101-115 -> 101--115.
    return re.sub(r"\s*[-–—]+\s*", "--", pages) if re.search(r"[-–—]", pages) else pages


def _canonical_url(record: dict) -> str | None:
    if record.get("url"):
        return str(record["url"])
    if record.get("pmid"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{record['pmid']}/"
    if record.get("doi"):
        return f"https://doi.org/{record['doi']}"
    if record.get("pmcid"):
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{record['pmcid']}/"
    return None


def _has_type(record: dict, *needles: str) -> bool:
    types = " | ".join(str(t) for t in (record.get("article_types") or [])).lower()
    return any(n in types for n in needles)


def entry_type_for(record: dict) -> str:
    """@article default; preprints and grey literature get the honest container."""
    if record.get("is_preprint"):
        return "misc"
    if _has_type(record, "book chapter", "chapter"):
        return "incollection"
    if _has_type(record, "book") or (record.get("source") == "book"):
        return "book"
    if record.get("source") in ("guideline", "web"):
        return "misc"
    if _has_type(record, "guideline", "practice guideline") and not record.get("journal"):
        return "misc"
    if not record.get("journal"):
        # @article without a journal is malformed BibTeX; grey literature it is.
        return "misc"
    return "article"


def _preprint_server(record: dict) -> str | None:
    for candidate in (record.get("journal"), record.get("publisher"), record.get("server")):
        if candidate:
            return str(candidate)
    doi = (record.get("doi") or "").lower()
    url = (record.get("url") or "").lower()
    for needle, label in (
        ("biorxiv", "bioRxiv"),
        ("medrxiv", "medRxiv"),
        ("researchsquare", "Research Square"),
        ("research-square", "Research Square"),
        ("arxiv", "arXiv"),
        ("10.1101", "bioRxiv / medRxiv"),
        ("10.21203", "Research Square"),
    ):
        if needle in doi or needle in url:
            return label
    return None


_TIER_LABELS = {
    0: "local library",
    1: "PMC full text",
    2: "PMC PDF",
    3: "Europe PMC full-text XML",
    4: "Unpaywall location",
    5: "open-access fetch",
    6: "preprint twin",
    7: "quarantined; full text not obtained",
}


def build_note(record: dict) -> str | None:
    """Notes that must survive into the rendered bibliography (reporting.md §4)."""
    bits: list[str] = []
    status = record.get("retraction_status") or "none"
    if status == "retracted":
        bits.append("RETRACTED.")
    elif status == "expression_of_concern":
        bits.append("Expression of Concern.")
    elif status == "corrected":
        bits.append("A correction to this article exists.")
    if record.get("is_preprint"):
        bits.append("Preprint. Not peer reviewed.")
    ft = record.get("fulltext") or {}
    ft_status = ft.get("status")
    if ft_status == "abstract_only":
        bits.append("Abstract only; full text not obtained.")
    elif ft_status == "missing":
        bits.append("Full text not obtained (quarantined).")
    tier = ft.get("source_tier")
    if ft_status == "fulltext" and isinstance(tier, int) and tier in _TIER_LABELS:
        bits.append(f"Full text via {_TIER_LABELS[tier]} (source tier {tier}).")
    if record.get("note"):
        bits.append(str(record["note"]))
    return " ".join(bits) if bits else None


def build_entry(record: dict) -> str:
    """One BibTeX entry. Absent values are omitted, never invented (schema.md S3)."""
    key = bib_key(record["evidence_id"])
    etype = entry_type_for(record)
    year, month = _year_month(record)
    fields: list[tuple[str, str]] = []

    def put(name: str, value, *, escaped: bool = False) -> None:
        if value is None:
            return
        text = value if escaped else bib_escape(str(value))
        if not str(text).strip():
            return
        fields.append((name, str(text)))

    put("author", bib_authors(record), escaped=True)
    title = record.get("title")
    if title:
        put("title", bib_title(str(title)), escaped=True)
    if etype == "article":
        put("journal", record.get("journal"))
    elif etype == "incollection":
        put("booktitle", record.get("booktitle") or record.get("journal"))
        put("publisher", record.get("publisher"))
    elif etype == "book":
        put("publisher", record.get("publisher") or record.get("journal"))
    else:  # misc
        if record.get("is_preprint"):
            put("howpublished", _preprint_server(record))
        else:
            put("howpublished", record.get("publisher") or record.get("journal"))
    put("year", year)
    put("month", month)
    put("volume", record.get("volume"))
    put("number", record.get("number") or record.get("issue"))
    put("pages", _pages(record))
    put("doi", record.get("doi"))
    put("pmid", record.get("pmid"))
    put("pmcid", record.get("pmcid"))
    put("issn", record.get("issn"))
    url = _canonical_url(record)
    put("url", url)
    if etype == "misc" and url and record.get("accessed"):
        put("urldate", record.get("accessed"))
    keywords = record.get("keywords") or []
    if keywords:
        put("keywords", ", ".join(str(k) for k in keywords))
    put("note", build_note(record))

    width = max((len(name) for name, _ in fields), default=0)
    body = ",\n".join(f"  {name.ljust(width)} = {{{value}}}" for name, value in fields)
    return f"@{etype}{{{key},\n{body}\n}}\n"


def select_records(records: list[dict], mode: str) -> list[dict]:
    """Which corpus records earn a bibliography entry.

    included  (default) — screening.decision == "include", plus any record carrying a
                          retraction flag (reporting.md §4 requires those to be cited
                          with a marker, so they must be citable).
    all       — every retrieved record.
    """
    if mode == "all":
        return list(records)
    out = []
    for rec in records:
        screening = rec.get("screening") or {}
        if screening.get("decision") == "include":
            out.append(rec)
        elif (rec.get("retraction_status") or "none") != "none":
            out.append(rec)
    return out


def cmd_bib(args) -> int:
    corpus_path = Path(args.corpus)
    records = read_corpus(corpus_path)
    selected = select_records(records, args.select)

    by_key: dict[str, dict] = {}
    for rec in selected:
        key = bib_key(rec["evidence_id"])
        if key in by_key:
            # Impossible by construction under R6 (evidence_ids are unique, S9), so a
            # collision means the corpus itself is broken. Assert loudly.
            raise StateError(
                f"citation-key collision {key!r}: {by_key[key]['evidence_id']!r} and "
                f"{rec['evidence_id']!r} (schema.md S9/R6 violated)"
            )
        by_key[key] = rec

    header = (
        "% refs.bib — GENERATED by scripts/render.py. Do not hand-edit; regenerating overwrites.\n"
        f"% source: {corpus_path.name}  entries: {len(by_key)}  selection: {args.select}\n"
        "% citation key = corpus evidence_id with all non-alphanumerics stripped "
        "(references/schema.md R6)\n\n"
    )
    entries = [build_entry(by_key[key]) for key in sorted(by_key)]
    out_path = Path(args.out)
    atomic_write(out_path, header + "\n".join(entries))

    emit({
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "command": "bib",
        "out": str(out_path),
        "records_read": len(records),
        "entries_written": len(entries),
        "selection": args.select,
        "keys": sorted(by_key),
    })
    return 0


# ------------------------------------------------------------ footnote resolution

def slug_doi(doi: str) -> str:
    return re.sub(r"[^0-9a-z]+", "-", str(doi).lower()).strip("-")


def alias_map(records: list[dict]) -> tuple[dict[str, dict], set[str]]:
    """Map every plausible `sources[].id` shape onto its corpus record.

    Shapes come from reporting.md §2 (`pubmed-<pmid>`, `doi-<slug>`, `pmc-<pmcid>`,
    `fulltext-<pmid>`, `url-<slug>`) plus the evidence_id and the R6 key themselves.
    An alias claimed by two different records is ambiguous and is deliberately made
    unresolvable — a wrong citation is worse than an untouched footnote.
    """
    aliases: dict[str, dict] = {}
    ambiguous: set[str] = set()

    def add(alias: str | None, rec: dict) -> None:
        if not alias:
            return
        norm = alias.strip().lower()
        if not norm:
            return
        prev = aliases.get(norm)
        if prev is not None and prev["evidence_id"] != rec["evidence_id"]:
            ambiguous.add(norm)
            return
        aliases[norm] = rec

    for rec in records:
        eid = str(rec["evidence_id"])
        add(eid, rec)
        add(bib_key(eid), rec)
        add(re.sub(r"[^0-9A-Za-z]+", "-", eid).strip("-"), rec)
        pmid = rec.get("pmid")
        if pmid:
            for pref in ("pubmed-", "pmid-", "fulltext-", "pubmed:", "pmid:"):
                add(f"{pref}{pmid}", rec)
            add(str(pmid), rec)
        doi = rec.get("doi")
        if doi:
            s = slug_doi(doi)
            add(f"doi-{s}", rec)
            add(f"doi:{doi}", rec)
            add(s, rec)
        pmcid = rec.get("pmcid")
        if pmcid:
            for pref in ("pmc-", "pmcid-", "pmc:", "pmcid:", "fulltext-"):
                add(f"{pref}{pmcid}", rec)
            add(str(pmcid), rec)
        if eid.startswith("url:"):
            add(f"url-{eid[4:]}", rec)

    for norm in ambiguous:
        aliases.pop(norm, None)
    return aliases, ambiguous


# ----------------------------------------------------------------- qmd assembly

_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_FN_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:")
_FN_RUN_RE = re.compile(r"(?:\[\^[^\]\s]+\])+(?!:)")
_FN_ONE_RE = re.compile(r"\[\^([^\]\s]+)\]")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Pull scalar `key: value` pairs out of a leading YAML block. Deliberately shallow —
    the report's frontmatter (templates/report.md) is flat scalars."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if line.startswith((" ", "\t", "#")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if not value.startswith(('"', "'")) and " #" in value:
            value = value.split(" #", 1)[0].strip()   # trailing YAML comment
        value = value.strip('"').strip("'").strip()
        if value:
            meta[key.strip()] = value
    return meta, text[m.end():]


def yaml_quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


DEFAULT_HEADER = """title: {title}
author: {author}
date: {date}
format:
  pdf:
    toc: true
    toc-depth: 3
    number-sections: true
    papersize: a4
    geometry: margin=25mm
    colorlinks: true
  docx:
    toc: true
    toc-depth: 3
    number-sections: true
bibliography: {bib}
link-citations: true
toc: true
"""


def build_header(args, meta: dict[str, str], bib_name: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    subs = {
        "TITLE": args.title or meta.get("title") or "Evidence review",
        "AUTHOR": args.author or meta.get("author") or "deep-research",
        "GENERATED_DATE": args.date or meta.get("generated_date") or date.today().isoformat(),
        "GENERATED_AT": meta.get("generated") or now_iso(),
        "RUN_SLUG": args.run_slug or meta.get("run_slug") or "",
        "PROFILE": meta.get("profile") or "",
        "REPORT_STATUS": meta.get("status") or "",
        "STATUS_NOTE": meta.get("status_note") or "",
        "SEARCH_DATE": meta.get("search_date") or "",
    }

    if not args.template:
        header = DEFAULT_HEADER.format(
            title=yaml_quote(subs["TITLE"]),
            author=yaml_quote(subs["AUTHOR"]),
            date=yaml_quote(subs["GENERATED_DATE"]),
            bib=bib_name,
        )
        return header, notes

    tpl_path = Path(args.template)
    if not tpl_path.exists():
        raise UserError(f"template not found: {tpl_path}")
    tpl = tpl_path.read_text(encoding="utf-8")
    m = _FM_RE.match(tpl)
    if not m:
        raise UserError(f"template has no YAML header: {tpl_path}")
    body = m.group(1)
    for name, value in subs.items():
        body = body.replace("{{" + name + "}}", str(value))

    kept: list[str] = []
    for line in body.splitlines():
        if "{{" in line:
            notes.append(f"template header line dropped (unresolved placeholder): {line.strip()}")
            continue
        kept.append(line)

    text = "\n".join(kept).rstrip() + "\n"
    if not re.search(r"^bibliography\s*:", text, flags=re.M):
        text += f"bibliography: {bib_name}\n"
    else:
        text = re.sub(r"^bibliography\s*:.*$", f"bibliography: {bib_name}", text, flags=re.M)
    if not re.search(r"^format\s*:", text, flags=re.M):
        text += "format:\n  pdf:\n    toc: true\n  docx:\n    toc: true\n"
    if not re.search(r"^\s*toc\s*:", text, flags=re.M):
        text += "toc: true\n"
    if not re.search(r"^title\s*:", text, flags=re.M):
        text = f"title: {yaml_quote(subs['TITLE'])}\n" + text
    if not re.search(r"^author\s*:", text, flags=re.M):
        text += f"author: {yaml_quote(subs['AUTHOR'])}\n"
    if not re.search(r"^date\s*:", text, flags=re.M):
        text += f"date: {yaml_quote(subs['GENERATED_DATE'])}\n"
    return text, notes


def convert_body(
    body: str,
    aliases: dict[str, dict],
    ambiguous: set[str],
    *,
    keep_footnotes: bool,
) -> tuple[str, dict]:
    """Rewrite footnote attributions into Quarto citations.

    Resolved inline refs become `[@key]` (adjacent refs merge into `[@a; @b]`).
    Unresolved refs and their definitions are left exactly as they were, and reported.
    """
    lines = body.splitlines()
    resolved_keys: dict[str, str] = {}   # footnote key -> bib key
    unresolved: dict[str, int] = {}
    used_bib_keys: set[str] = set()

    def resolve(fn_key: str) -> str | None:
        norm = fn_key.strip().lower()
        if norm in ambiguous:
            return None
        rec = aliases.get(norm)
        if rec is None:
            return None
        return bib_key(rec["evidence_id"])

    def replace_run(match: re.Match) -> str:
        run = match.group(0)
        keys = _FN_ONE_RE.findall(run)
        cited: list[str] = []
        leftover: list[str] = []
        for fn_key in keys:
            bkey = resolve(fn_key)
            if bkey is None:
                unresolved[fn_key] = unresolved.get(fn_key, 0) + 1
                leftover.append(f"[^{fn_key}]")
            else:
                resolved_keys[fn_key] = bkey
                used_bib_keys.add(bkey)
                if bkey not in cited:
                    cited.append(bkey)
        out = ""
        if cited:
            out += "[" + "; ".join(f"@{k}" for k in cited) + "]"
        out += "".join(leftover)
        return out

    # Pass 1 — inline references, skipping fenced code blocks and definition blocks.
    out_lines: list[str] = []
    in_fence = False
    i = 0
    def_blocks: list[tuple[int, int, str]] = []  # (start, end_exclusive, key)
    while i < len(lines):
        line = lines[i]
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            i += 1
            continue
        if in_fence:
            out_lines.append(line)
            i += 1
            continue
        m = _FN_DEF_RE.match(line)
        if m:
            start = i
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() == "":
                    # blank line belongs to the block only if an indented line follows
                    if i + 1 < len(lines) and lines[i + 1][:1] in (" ", "\t"):
                        i += 2
                        continue
                    break
                if nxt[:1] in (" ", "\t"):
                    i += 1
                    continue
                break
            def_blocks.append((start, i, m.group(1)))
            out_lines.extend(lines[start:i])
            continue
        out_lines.append(_FN_RUN_RE.sub(replace_run, line))
        i += 1

    # Pass 2 — definitions. Drop only those whose key resolved (the bibliography now
    # carries them); an unresolved definition stays in place, untouched.
    dropped = 0
    kept_defs: list[str] = []
    drop_ranges: list[tuple[int, int]] = []
    for start, end, key in def_blocks:
        bkey = resolve(key)
        if bkey is None:
            unresolved.setdefault(key, unresolved.get(key, 0))
            kept_defs.append(key)
            continue
        resolved_keys.setdefault(key, bkey)
        if not keep_footnotes:
            drop_ranges.append((start, end))
            dropped += 1

    if drop_ranges:
        drop = set()
        for start, end in drop_ranges:
            drop.update(range(start, end))
        out_lines = [ln for idx, ln in enumerate(out_lines) if idx not in drop]

    text = "\n".join(out_lines)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    stats = {
        "footnote_keys_resolved": sorted(set(resolved_keys)),
        "citations_emitted": sorted(used_bib_keys),
        "unresolved_footnotes": sorted(unresolved),
        "unresolved_definitions_kept": sorted(set(kept_defs)),
        "definitions_dropped": dropped,
    }
    return text, stats


def cmd_qmd(args) -> int:
    report_path = Path(args.report)
    if not report_path.exists():
        raise UserError(f"report not found: {report_path}")
    records = read_corpus(Path(args.corpus))
    aliases, ambiguous = alias_map(records)

    raw = report_path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)

    out_path = Path(args.out)
    bib_name = args.bibliography or DEFAULT_BIB_NAME
    header, notes = build_header(args, meta, bib_name)
    converted, stats = convert_body(
        body, aliases, ambiguous, keep_footnotes=args.keep_footnotes
    )

    if "{#refs}" not in converted:
        converted = converted.rstrip() + (
            "\n\n# References {.unnumbered}\n\n::: {#refs}\n:::\n"
        )

    atomic_write(out_path, f"---\n{header.rstrip()}\n---\n\n{converted.lstrip()}\n")

    bib_path = out_path.parent / bib_name
    warnings = list(notes)
    for key in stats["unresolved_footnotes"]:
        warnings.append(
            f"footnote [^{key}] does not resolve to a corpus record; left untouched in "
            f"{out_path.name} (reporting.md R2 / verify.py C-CITE-RESOLVE)"
        )
    if ambiguous:
        warnings.append(
            "ambiguous source ids (claimed by >1 record, treated as unresolvable): "
            + ", ".join(sorted(ambiguous))
        )
    # A citation whose record would not appear in the default refs.bib selection.
    selected_keys = {bib_key(r["evidence_id"]) for r in select_records(records, "included")}
    orphan = [k for k in stats["citations_emitted"] if k not in selected_keys]
    if orphan:
        warnings.append(
            "cited records are not in the default refs.bib selection (run `bib --select all` "
            "or check screening decisions): " + ", ".join(orphan)
        )
    if not bib_path.exists():
        warnings.append(f"{bib_path} does not exist yet — run `render.py bib` before rendering")

    for w in warnings:
        warn(w)
    run_dir = find_run_dir(out_path)
    for w in warnings:
        engine_log(run_dir, f"qmd warning: {w}")

    emit({
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "command": "qmd",
        "out": str(out_path),
        "bibliography": str(bib_path),
        "template": str(args.template) if args.template else None,
        **stats,
        "warnings": warnings,
    })
    return 0


# --------------------------------------------------------------------- rendering

def require_quarto(explicit: str | None = None) -> str:
    exe = explicit or os.environ.get("QUARTO_BIN") or shutil.which("quarto")
    if not exe or not Path(exe).exists():
        raise RenderError(
            "the `quarto` binary was not found on PATH",
            detail=(
                "Install Quarto (https://quarto.org/docs/get-started/) or set QUARTO_BIN / "
                "pass --quarto-bin. outputs/report.md is unaffected; no files were changed."
            ),
        )
    return exe


def run_quarto(qmd: Path, fmt: str, out_dir: Path | None, quarto_bin: str,
               extra: list[str] | None = None) -> dict:
    exe = require_quarto(quarto_bin)
    cmd = [exe, "render", qmd.name, "--to", fmt]
    if out_dir is not None:
        cmd += ["--output-dir", str(out_dir.resolve())]
    cmd += list(extra or [])
    try:
        proc = subprocess.run(
            cmd, cwd=str(qmd.parent), capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise RenderError(f"could not execute quarto: {exc}", detail=" ".join(cmd)) from None
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        raise RenderError(
            f"quarto render --to {fmt} failed (exit {proc.returncode})",
            detail=tail[-4000:],
            returncode=proc.returncode,
        )
    return {"command": cmd, "stdout": proc.stdout, "stderr": proc.stderr}


def expected_output(qmd: Path, fmt: str, out_dir: Path | None) -> Path:
    ext = {"pdf": ".pdf", "docx": ".docx", "html": ".html"}.get(fmt, f".{fmt}")
    target_dir = out_dir if out_dir is not None else qmd.parent
    return target_dir / (qmd.stem + ext)


def failure_payload(run_dir: Path | None, command: str, exc: RenderError, *,
                    qmd: Path | None = None, fmt: str | None = None) -> dict:
    preserved = []
    if run_dir is not None:
        md = run_dir / "outputs" / "report.md"
        if md.exists():
            preserved.append(str(md))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "failed",
        "stage": "report",
        "command": command,
        "format": fmt,
        "qmd": str(qmd) if qmd else None,
        "error": str(exc),
        "detail": exc.detail,
        "returncode": exc.returncode,
        "preserved": preserved,
        "deleted": [],
        "engine_log": str(run_dir / "engine.log") if run_dir else None,
        "timestamp": now_iso(),
    }


def render_one(qmd: Path, fmt: str, out_dir: Path | None, args) -> dict:
    """Render one format. Raises RenderError; the caller owns failure semantics."""
    result = run_quarto(qmd, fmt, out_dir, getattr(args, "quarto_bin", None))
    produced = expected_output(qmd, fmt, out_dir)
    if not produced.exists():
        raise RenderError(
            f"quarto reported success but {produced} was not produced",
            detail=(result.get("stdout", "") + result.get("stderr", ""))[-4000:],
        )
    return {"format": fmt, "output": str(produced), "stderr_tail": (result.get("stderr") or "")[-800:]}


def cmd_render(args) -> int:
    qmd = Path(args.qmd)
    if not qmd.exists():
        raise UserError(f"qmd not found: {qmd}")
    fmt = args.format
    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(args.run_dir) if getattr(args, "run_dir", None) else find_run_dir(qmd)

    bib = qmd.parent / DEFAULT_BIB_NAME
    if not bib.exists():
        warn(f"{bib} is missing; citations will render unresolved")
        engine_log(run_dir, f"render warning: {bib} missing before `quarto render --to {fmt}`")

    try:
        info = render_one(qmd, fmt, out_dir, args)
    except RenderError as exc:
        payload = failure_payload(run_dir, fmt, exc, qmd=qmd, fmt=fmt)
        engine_log(run_dir, f"render failure ({fmt}): {exc} :: {exc.detail.splitlines()[-1] if exc.detail else ''}")
        emit(payload)
        return 5 if "not found on PATH" in str(exc) else 4

    engine_log(run_dir, f"rendered {fmt}: {info['output']}")
    emit({
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "command": fmt,
        "qmd": str(qmd),
        **info,
    })
    return 0


# --------------------------------------------------------------------- one-shot

def _newer(target: Path, *inputs: Path) -> bool:
    """True when `target` exists and is at least as new as every input."""
    if not target.exists():
        return False
    t = target.stat().st_mtime
    return all((not p.exists()) or p.stat().st_mtime <= t for p in inputs)


def cmd_all(args) -> int:
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        raise UserError(f"run directory not found: {run_dir}")
    outputs = run_dir / "outputs"
    corpus = Path(args.corpus) if args.corpus else run_dir / "corpus.jsonl"
    report = Path(args.report) if args.report else outputs / "report.md"
    if not report.exists():
        raise UserError(f"report not found: {report} (stage 7 must run first)")
    if not corpus.exists():
        raise UserError(f"corpus not found: {corpus}")
    outputs.mkdir(parents=True, exist_ok=True)

    bib_out = outputs / DEFAULT_BIB_NAME
    qmd_out = outputs / (report.stem + ".qmd")
    steps: list[dict] = []
    warnings: list[str] = []

    # --- bib (idempotent)
    if not args.force and _newer(bib_out, corpus):
        steps.append({"step": "bib", "status": "skipped", "reason": "up to date", "out": str(bib_out)})
    else:
        bib_args = argparse.Namespace(corpus=str(corpus), out=str(bib_out), select=args.select)
        buf = _capture(cmd_bib, bib_args)
        steps.append({"step": "bib", "status": "ok", "out": str(bib_out),
                      "entries": buf.get("entries_written")})

    # --- qmd (idempotent)
    tpl = Path(args.template) if args.template else None
    if not args.force and _newer(qmd_out, report, corpus, *( [tpl] if tpl else [] )):
        steps.append({"step": "qmd", "status": "skipped", "reason": "up to date", "out": str(qmd_out)})
    else:
        qmd_args = argparse.Namespace(
            report=str(report), corpus=str(corpus), out=str(qmd_out),
            template=str(tpl) if tpl else None, bibliography=DEFAULT_BIB_NAME,
            title=args.title, author=args.author, date=args.date, run_slug=args.run_slug,
            keep_footnotes=args.keep_footnotes,
        )
        buf = _capture(cmd_qmd, qmd_args)
        warnings.extend(buf.get("warnings") or [])
        steps.append({"step": "qmd", "status": "ok", "out": str(qmd_out),
                      "unresolved_footnotes": buf.get("unresolved_footnotes")})

    # --- render
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    failures = 0
    for fmt in formats:
        produced = expected_output(qmd_out, fmt, outputs)
        if not args.force and _newer(produced, qmd_out, bib_out):
            steps.append({"step": fmt, "status": "skipped", "reason": "up to date",
                          "output": str(produced)})
            continue
        try:
            info = render_one(qmd_out, fmt, outputs, args)
        except RenderError as exc:
            failures += 1
            payload = failure_payload(run_dir, "all", exc, qmd=qmd_out, fmt=fmt)
            engine_log(run_dir, f"render failure ({fmt}): {exc}")
            steps.append({"step": fmt, "status": "failed", "error": str(exc),
                          "detail": exc.detail[-1500:] if exc.detail else ""})
            if "not found on PATH" in str(exc):
                emit({**payload, "steps": steps, "warnings": warnings})
                return 5
            continue
        engine_log(run_dir, f"rendered {fmt}: {info['output']}")
        steps.append({"step": fmt, "status": "ok", "output": info["output"]})

    for w in warnings:
        warn(w)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "failed" if failures else "ok",
        "command": "all",
        "run_dir": str(run_dir),
        "report_md": str(report),
        "preserved": [str(report)],
        "deleted": [],
        "steps": steps,
        "warnings": warnings,
        "timestamp": now_iso(),
    }
    emit(payload)
    return 4 if failures else 0


def _capture(func, args) -> dict:
    """Run a subcommand that prints a JSON payload, returning the payload instead."""
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        func(args)
    try:
        return json.loads(buf.getvalue())
    except json.JSONDecodeError:
        return {}


# ------------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="render.py",
        description="report.md -> refs.bib + report.qmd -> quarto render (pdf/docx).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Citation keys follow references/schema.md R6: evidence_id with every\n"
            "non-alphanumeric character stripped (pmid:12345678 -> pmid12345678).\n"
            "Render failures preserve outputs/report.md, delete nothing, log to engine.log."
        ),
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bib", help="generate refs.bib from corpus.jsonl")
    p.add_argument("--corpus", required=True, help="path to corpus.jsonl")
    p.add_argument("--out", required=True, help="path to write refs.bib")
    p.add_argument("--select", choices=("included", "all"), default="included",
                   help="included (default): screening include + retraction-flagged records")
    p.set_defaults(func=cmd_bib)

    p = sub.add_parser("qmd", help="convert report.md into report.qmd with [@key] citations")
    p.add_argument("--report", required=True, help="path to outputs/report.md")
    p.add_argument("--corpus", required=True, help="path to corpus.jsonl")
    p.add_argument("--out", required=True, help="path to write report.qmd")
    p.add_argument("--template", help="Quarto template whose YAML header is reused "
                                      "(templates/report.qmd)")
    p.add_argument("--bibliography", default=DEFAULT_BIB_NAME,
                   help=f"bibliography filename referenced from the header (default {DEFAULT_BIB_NAME})")
    p.add_argument("--title", help="override the document title")
    p.add_argument("--author", help="override the document author")
    p.add_argument("--date", help="override the document date (YYYY-MM-DD)")
    p.add_argument("--run-slug", help="run slug used by the template subtitle")
    p.add_argument("--keep-footnotes", action="store_true",
                   help="keep resolved footnote definitions alongside the citations")
    p.set_defaults(func=cmd_qmd)

    for fmt in ("pdf", "docx", "html"):
        p = sub.add_parser(fmt, help=f"quarto render --to {fmt}")
        p.add_argument("--qmd", required=True, help="path to report.qmd")
        p.add_argument("--out-dir", help="output directory (default: the .qmd's directory)")
        p.add_argument("--run-dir", help="run directory owning engine.log (default: inferred)")
        p.add_argument("--quarto-bin", help="path to the quarto binary (default: PATH/QUARTO_BIN)")
        p.set_defaults(func=cmd_render, format=fmt)

    p = sub.add_parser("all", help="bib -> qmd -> render, resumable and idempotent")
    p.add_argument("--run-dir", required=True, help="run directory (<wiki>/outputs/deep-research/<slug>/)")
    p.add_argument("--formats", default="pdf", help="comma-separated: pdf,docx,html")
    p.add_argument("--corpus", help="override <run-dir>/corpus.jsonl")
    p.add_argument("--report", help="override <run-dir>/outputs/report.md")
    p.add_argument("--template", help="Quarto template (templates/report.qmd)")
    p.add_argument("--select", choices=("included", "all"), default="included")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--date")
    p.add_argument("--run-slug")
    p.add_argument("--keep-footnotes", action="store_true")
    p.add_argument("--quarto-bin", help="path to the quarto binary (default: PATH/QUARTO_BIN)")
    p.add_argument("--force", action="store_true", help="regenerate every step, ignoring mtimes")
    p.set_defaults(func=cmd_all)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except StateError as exc:
        print(f"state-error: {exc}", file=sys.stderr)
        return 3
    except RenderError as exc:
        emit(failure_payload(None, getattr(args, "command", "render"), exc))
        return 5 if "not found on PATH" in str(exc) else 4
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
