#!/usr/bin/env python3
"""corpus.py — corpus.jsonl store, dedupe, PRISMA counters, and the taskboard CLI.

Enforcement point for `references/schema.md` §2 (taskboard record), §3 (search result
record), §4 (corpus record), §5 (screening verdict), §6 (adjudication record).

Environment: python3 3.14, stdlib only. No third-party imports, no pip.

Files this script owns inside a run directory
  corpus.jsonl                       state file; JSON Lines, one corpus record per line
  taskboard.jsonl                    append-only log; last record per task_id wins
  .locks/<name>.lock                 advisory flock files (parallel subagents)
  .guard.json                        no-progress guard probe history
  workspace/search/.query-index.json normalized-query registry (duplicate-query guard)

Schema extensions (documented deviations from references/schema.md §4)
  Two optional list fields are added to the corpus record so that dedupe can union
  provenance without losing information, as required by `SKILL.md` stage 3:
    seen_in_queries : string[]  every query_id that surfaced this study (first_seen_query
                                remains the single earliest one, per schema §4)
    merged_from     : string[]  evidence_ids absorbed into this record by dedupe
    source_ids      : string[]  evidence-kernel snapshot ids backing this record
                                (schema R14; written by fulltext.py/library.py when text
                                is registered into the store, unioned on merge)
  All three default to [] and are dropped by `corpus.py export --strict-schema`.

Dedupe policy (see `dedupe`)
  pass 1  exact PMID
  pass 2  normalized DOI (NFKC, casefold, strip doi:/dx.doi.org/doi.org prefixes,
          strip trailing punctuation)
  pass 3  normalized title (NFKD, combining marks stripped, casefold, punctuation ->
          space, whitespace collapsed) compared with difflib.SequenceMatcher;
          default ratio threshold 0.93 (--title-threshold).
  Blocking guards — a pair is NEVER merged when any of these hold, so a distinct study
  can never be silently dropped:
    * both records have a PMID and the PMIDs differ
    * both records have a DOI and the normalized DOIs differ
    * both records have a publication year and the years differ
    * one is a preprint and the other is not, unless DOI or PMID matched exactly
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import now_iso, read_json  # noqa: E402  (sibling module, stdlib-only)
from taskboard import (  # noqa: E402  (sibling module, stdlib-only)
    STAGES,
    TASK_STATUSES,
    StateError,
    TaskBoard,
    UserError,
    cmd_task_block,
    cmd_task_cancel,
    cmd_task_claim,
    cmd_task_complete,
    cmd_task_create,
    cmd_task_fail,
    cmd_task_list,
    cmd_task_next,
    cmd_task_reopen,
    cmd_task_show,
    cmd_task_stats,
    validate_task_id,
)

SCHEMA_VERSION = 1
DEFAULT_TITLE_THRESHOLD = 0.93

# --------------------------------------------------------------------------- enums

DECISIONS = ("include", "exclude", "unclear")
RETRACTION = ("none", "retracted", "expression_of_concern", "corrected")
CORPUS_SOURCES = ("pubmed", "europepmc", "preprint", "guideline", "web", "pool")
FULLTEXT_STATUS = ("fulltext", "abstract_only", "missing")

CORPUS_FIELDS = (
    "schema_version", "evidence_id", "pmid", "doi", "pmcid", "title", "journal",
    "publication_date", "authors", "article_types", "mesh_terms", "keywords",
    "retraction_status", "source", "is_preprint", "screening", "fulltext",
    "extraction_path", "appraisal_path", "first_seen_query",
)
CORPUS_EXTENSIONS = ("seen_in_queries", "merged_from", "source_ids")
# schema.md R9: optional bibliographic extras supplied by `eutils.py efetch` and
# consumed by `render.py` / `okf.py`. Carried through verbatim when present, never
# fetched or invented here. volume/issue/pages/issn are strings, never numbers.
CORPUS_BIBLIO = ("volume", "issue", "pages", "issn", "epub_date", "abstract", "grants")
FULLTEXT_FIELDS = (
    "status", "source_tier", "access_route", "local_path", "sha256",
    "truncation_detected",
)

# ----------------------------------------------------------------------- utilities

def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def atomic_write(path: Path, text: str) -> None:
    """Temp file in the same directory + os.replace. Never leaves a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_line(path: Path, obj: dict) -> None:
    """Append exactly one complete JSONL line. Caller must hold the advisory lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_json(obj) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: Path, *, strict: bool = False) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                msg = f"{path}:{n}: malformed JSONL ({exc})"
                if strict:
                    raise UserError(msg)
                warn(msg + " — line skipped")
                continue
            if not isinstance(obj, dict):
                warn(f"{path}:{n}: not a JSON object — line skipped")
                continue
            out.append(obj)
    return out

def warn(msg: str) -> None:
    print(f"warn: {msg}", file=sys.stderr)


@contextmanager
def advisory_lock(run_dir: Path, name: str, timeout_note: str = ""):
    """Cross-process exclusive lock. Subagents run in parallel; every writer takes it."""
    lock_dir = run_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{name}.lock"
    with open(lock_path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ------------------------------------------------------------------ normalization


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def norm_title(title: str | None) -> str:
    if not title:
        return ""
    t = strip_diacritics(title)
    t = t.casefold()
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                 "http://dx.doi.org/", "doi.org/", "dx.doi.org/", "doi:")


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = unicodedata.normalize("NFKC", doi).strip().casefold()
    for pref in _DOI_PREFIXES:
        if d.startswith(pref):
            d = d[len(pref):]
    d = d.strip().rstrip(".,;)")
    return d or None


def norm_pmid(pmid) -> str | None:
    if pmid is None:
        return None
    p = str(pmid).strip()
    p = re.sub(r"^pmid:", "", p, flags=re.I)
    return p or None


def norm_pmcid(pmcid) -> str | None:
    if not pmcid:
        return None
    p = str(pmcid).strip().upper()
    if p.startswith("PMC:"):
        p = p[4:]
    if p.isdigit():
        p = "PMC" + p
    return p or None


_QUOTES = {"“": '"', "”": '"', "‘": "'", "’": "'", "«": '"',
           "»": '"', "′": "'", "″": '"'}
_BOOL_RE = re.compile(r"\b(and|or|not)\b", re.I)


def norm_query(query: str) -> str:
    """Normalized form used for the duplicate-query guard (`SKILL.md` "Pipeline")."""
    q = unicodedata.normalize("NFKC", query)
    for src, dst in _QUOTES.items():
        q = q.replace(src, dst)
    q = q.casefold()
    q = _BOOL_RE.sub(lambda m: m.group(0).upper(), q)
    q = re.sub(r"\s*([()])\s*", r"\1", q)      # parens only: [] tags keep their spacing
    q = re.sub(r"\s*:\s*", ":", q)
    q = _WS_RE.sub(" ", q).strip()
    return q


def query_hash(query: str) -> str:
    return "sha256:" + hashlib.sha256(norm_query(query).encode("utf-8")).hexdigest()


def year_of(date: str | None) -> str | None:
    if not date:
        return None
    m = re.match(r"^(\d{4})", str(date))
    return m.group(1) if m else None


def slugify_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._~-]+", "-", value).strip("-")


def query_sort_key(query_id: str | None):
    """Earliest-query ordering: q1 < q2 < q10 < everything else, then lexicographic."""
    if not query_id:
        return (2, "", 0)
    if query_id == "pool-seed":
        return (-1, "", 0)
    m = re.match(r"^q(\d+)$", query_id.strip(), re.I)
    if m:
        return (0, "", int(m.group(1)))
    return (1, query_id, 0)


# ------------------------------------------------------------ corpus record shaping


def blank_fulltext() -> dict:
    return {
        "status": "missing", "source_tier": None, "access_route": None,
        "local_path": None, "sha256": None, "truncation_detected": False,
    }


def derive_evidence_id(rec: dict) -> str:
    """schema.md S9 precedence: pmid > doi > pmcid > url."""
    pmid = norm_pmid(rec.get("pmid"))
    if pmid:
        return f"pmid:{pmid}"
    doi = norm_doi(rec.get("doi"))
    if doi:
        return f"doi:{doi}"
    pmcid = norm_pmcid(rec.get("pmcid"))
    if pmcid:
        return f"pmcid:{pmcid}"
    url = rec.get("url") or rec.get("resource") or rec.get("evidence_id") or rec.get("title")
    if not url:
        raise UserError("cannot derive evidence_id: record has no pmid/doi/pmcid/url/title")
    digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:16]
    return f"url:{digest}"


def id_precedence(evidence_id: str) -> int:
    kind = evidence_id.split(":", 1)[0]
    return {"pmid": 0, "doi": 1, "pmcid": 2, "url": 3}.get(kind, 4)


def normalize_record(raw: dict, *, allow_extra: bool = False) -> dict:
    """Coerce an input dict into a schema-complete corpus record. Validates enums."""
    if not isinstance(raw, dict):
        raise UserError("corpus record must be a JSON object")
    unknown = (set(raw) - set(CORPUS_FIELDS) - set(CORPUS_EXTENSIONS)
               - set(CORPUS_BIBLIO) - {"url", "resource"})
    if unknown and not allow_extra:
        raise UserError(
            "unknown corpus field(s): " + ", ".join(sorted(unknown))
            + " (schema.md §4; pass --allow-extra to ignore)"
        )

    rec: dict = {"schema_version": SCHEMA_VERSION}
    rec["pmid"] = norm_pmid(raw.get("pmid"))
    rec["doi"] = norm_doi(raw.get("doi"))
    rec["pmcid"] = norm_pmcid(raw.get("pmcid"))
    rec["evidence_id"] = raw.get("evidence_id") or derive_evidence_id(raw)

    title = raw.get("title")
    if not title:
        raise UserError(f"{rec['evidence_id']}: 'title' is required (schema.md §4)")
    rec["title"] = str(title)
    rec["journal"] = raw.get("journal") or None
    rec["publication_date"] = raw.get("publication_date") or None

    for field in ("authors", "article_types", "mesh_terms", "keywords"):
        value = raw.get(field) or []
        if not isinstance(value, list):
            raise UserError(f"{rec['evidence_id']}: '{field}' must be a list")
        rec[field] = [str(v) for v in value]

    rec["retraction_status"] = raw.get("retraction_status") or "none"
    if rec["retraction_status"] not in RETRACTION:
        raise UserError(f"{rec['evidence_id']}: retraction_status must be one of {RETRACTION}")

    rec["source"] = raw.get("source") or "pubmed"
    if rec["source"] not in CORPUS_SOURCES:
        raise UserError(f"{rec['evidence_id']}: source must be one of {CORPUS_SOURCES}")

    rec["is_preprint"] = bool(raw.get("is_preprint", False))

    screening = raw.get("screening")
    if screening is not None:
        if not isinstance(screening, dict):
            raise UserError(f"{rec['evidence_id']}: screening must be an object or null")
        decision = screening.get("decision")
        if decision not in DECISIONS:
            raise UserError(
                f"{rec['evidence_id']}: screening.decision must be one of {DECISIONS}"
            )
        screening = {"decision": decision, "reason": screening.get("reason") or ""}
    rec["screening"] = screening

    ft = dict(blank_fulltext())
    given = raw.get("fulltext") or {}
    if not isinstance(given, dict):
        raise UserError(f"{rec['evidence_id']}: fulltext must be an object")
    bad = set(given) - set(FULLTEXT_FIELDS)
    if bad and not allow_extra:
        raise UserError(f"{rec['evidence_id']}: unknown fulltext field(s): {sorted(bad)}")
    ft.update({k: v for k, v in given.items() if k in FULLTEXT_FIELDS})
    if ft["status"] not in FULLTEXT_STATUS:
        raise UserError(f"{rec['evidence_id']}: fulltext.status must be one of {FULLTEXT_STATUS}")
    ft["truncation_detected"] = bool(ft.get("truncation_detected", False))
    if ft["truncation_detected"] and ft["status"] == "fulltext":
        raise UserError(
            f"{rec['evidence_id']}: truncation_detected=true forces status=abstract_only "
            "(schema.md §4 invariant)"
        )
    # schema.md §4: source_tier is null before stage 4. The `missing => tier 8`
    # invariant applies only once retrieval was actually attempted (access_route set).
    if ft["status"] == "missing" and ft["access_route"] and ft["source_tier"] is None:
        ft["source_tier"] = 8
    if ft["source_tier"] is not None and not (0 <= int(ft["source_tier"]) <= 8):
        raise UserError(f"{rec['evidence_id']}: fulltext.source_tier must be 0-8 or null")
    rec["fulltext"] = ft

    rec["extraction_path"] = raw.get("extraction_path") or None
    rec["appraisal_path"] = raw.get("appraisal_path") or None
    rec["first_seen_query"] = raw.get("first_seen_query") or None

    seen = raw.get("seen_in_queries") or []
    if rec["first_seen_query"] and rec["first_seen_query"] not in seen:
        seen = [rec["first_seen_query"]] + list(seen)
    rec["seen_in_queries"] = sorted(set(seen), key=query_sort_key)
    rec["merged_from"] = sorted(set(raw.get("merged_from") or []))
    rec["source_ids"] = sorted(set(raw.get("source_ids") or []))
    for field in CORPUS_BIBLIO:                       # R9 — present-only passthrough
        if raw.get(field) not in (None, "", []):
            value = raw[field]
            if field in ("volume", "issue", "pages", "issn") and not isinstance(value, str):
                value = str(value)
            rec[field] = value
    return rec


def strict_schema_view(rec: dict) -> dict:
    # R7/R14 extensions are stripped; R9's optional bibliographic fields are §4
    # fields and survive when present.
    return {k: rec[k] for k in CORPUS_FIELDS + CORPUS_BIBLIO if k in rec}


# ------------------------------------------------------------------ corpus store


class Corpus:
    """corpus.jsonl as a materialized state file: read -> merge -> atomic rewrite."""

    def __init__(self, run_dir: Path, path: Path | None = None):
        self.run_dir = run_dir
        self.path = path or (run_dir / "corpus.jsonl")
        self.records: dict[str, dict] = {}
        self.order: list[str] = []

    def load(self) -> "Corpus":
        for obj in read_jsonl(self.path):
            if obj.get("schema_version") != SCHEMA_VERSION:
                warn(f"{self.path}: record without schema_version {SCHEMA_VERSION} — kept as-is")
            rec = normalize_record(obj, allow_extra=True)
            eid = rec["evidence_id"]
            if eid not in self.records:
                self.order.append(eid)
            self.records[eid] = rec
        return self

    def save(self) -> None:
        lines = [canonical_json(self.records[e]) for e in self.order if e in self.records]
        atomic_write(self.path, "".join(l + "\n" for l in lines))

    def list(self) -> list[dict]:
        return [self.records[e] for e in self.order if e in self.records]

    # ---- insertion ---------------------------------------------------------

    def upsert(self, incoming: dict) -> tuple[str, str]:
        """Insert or merge on exact id keys. Returns (action, evidence_id)."""
        target = self._find_exact(incoming)
        if target is None:
            eid = incoming["evidence_id"]
            if eid in self.records:
                merged = merge_records(self.records[eid], incoming)
                self.records[eid] = merged
                return ("merged", eid)
            self.records[eid] = incoming
            self.order.append(eid)
            return ("added", eid)
        merged = merge_records(self.records[target], incoming)
        self._rekey(target, merged)
        return ("merged", merged["evidence_id"])

    def _find_exact(self, rec: dict) -> str | None:
        pmid, doi, pmcid = rec.get("pmid"), rec.get("doi"), rec.get("pmcid")
        for eid in self.order:
            existing = self.records.get(eid)
            if existing is None:
                continue
            if pmid and existing.get("pmid") == pmid:
                return eid
            if doi and existing.get("doi") == doi:
                return eid
            if pmcid and existing.get("pmcid") == pmcid and not (
                pmid and existing.get("pmid") and existing["pmid"] != pmid
            ):
                return eid
        return None

    def _rekey(self, old_eid: str, merged: dict) -> None:
        new_eid = merged["evidence_id"]
        if new_eid == old_eid:
            self.records[old_eid] = merged
            return
        idx = self.order.index(old_eid)
        self.order[idx] = new_eid
        del self.records[old_eid]
        self.records[new_eid] = merged


def _prefer(a, b, a_wins: bool):
    """Non-null wins; on conflict the higher-precedence record wins."""
    if a in (None, "", [], {}):
        return b
    if b in (None, "", [], {}):
        return a
    if a == b:
        return a
    return a if a_wins else b


def _prefer_source(existing_source: str | None, incoming_source: str | None,
                   keep_existing: bool):
    if existing_source == "pool" and incoming_source not in (None, "", "pool"):
        return incoming_source
    if incoming_source == "pool" and existing_source not in (None, "", "pool"):
        return existing_source
    return _prefer(existing_source, incoming_source, keep_existing)


def union_list(a, b) -> list:
    out = list(a or [])
    for item in (b or []):
        if item not in out:
            out.append(item)
    return out


def merge_records(existing: dict, incoming: dict) -> dict:
    """Merge two corpus records. Preserves the earliest first_seen_query, unions
    provenance, and never drops an id."""
    keep_existing = id_precedence(existing["evidence_id"]) <= id_precedence(
        incoming["evidence_id"]
    )
    out: dict = {"schema_version": SCHEMA_VERSION}

    for field in ("pmid", "doi", "pmcid", "title", "journal", "publication_date",
                  "journal", "extraction_path", "appraisal_path"):
        out[field] = _prefer(existing.get(field), incoming.get(field), keep_existing)

    for field in ("authors", "article_types", "mesh_terms", "keywords"):
        out[field] = union_list(existing.get(field), incoming.get(field))

    # retraction: any non-`none` flag survives
    flags = [existing.get("retraction_status", "none"), incoming.get("retraction_status", "none")]
    non_none = [f for f in flags if f and f != "none"]
    out["retraction_status"] = non_none[0] if non_none else "none"

    out["source"] = _prefer_source(existing.get("source"), incoming.get("source"),
                                   keep_existing)
    out["is_preprint"] = bool(existing.get("is_preprint")) or bool(incoming.get("is_preprint"))

    out["screening"] = existing.get("screening") or incoming.get("screening")

    ef, inf = existing.get("fulltext") or blank_fulltext(), incoming.get("fulltext") or blank_fulltext()
    rank = {"fulltext": 0, "abstract_only": 1, "missing": 2}
    best = ef if rank[ef["status"]] <= rank[inf["status"]] else inf
    other = inf if best is ef else ef
    ft = dict(best)
    for k in FULLTEXT_FIELDS:
        if ft.get(k) in (None, ""):
            ft[k] = other.get(k)
    ft["truncation_detected"] = bool(ef.get("truncation_detected")) or bool(
        inf.get("truncation_detected")
    )
    if ft["truncation_detected"] and ft["status"] == "fulltext":
        ft["truncation_detected"] = False  # a real full text overrides a truncated route
    out["fulltext"] = ft

    queries = union_list(existing.get("seen_in_queries"), incoming.get("seen_in_queries"))
    for q in (existing.get("first_seen_query"), incoming.get("first_seen_query")):
        if q and q not in queries:
            queries.append(q)
    out["seen_in_queries"] = sorted(set(queries), key=query_sort_key)
    out["first_seen_query"] = out["seen_in_queries"][0] if out["seen_in_queries"] else None

    out["evidence_id"] = derive_evidence_id(out)
    absorbed = set(existing.get("merged_from") or []) | set(incoming.get("merged_from") or [])
    for eid in (existing["evidence_id"], incoming["evidence_id"]):
        if eid != out["evidence_id"]:
            absorbed.add(eid)
    out["merged_from"] = sorted(absorbed)
    out["source_ids"] = sorted(
        set(existing.get("source_ids") or []) | set(incoming.get("source_ids") or [])
    )
    for field in CORPUS_BIBLIO:                       # R9 — never dropped by a merge
        value = _prefer(existing.get(field), incoming.get(field), keep_existing)
        if value not in (None, "", []):
            out[field] = value
    return out


def can_merge(a: dict, b: dict, *, exact_id_match: bool) -> tuple[bool, str]:
    """Blocking guards — never silently merge two distinct studies."""
    if a.get("pmid") and b.get("pmid") and a["pmid"] != b["pmid"]:
        return False, "distinct PMIDs"
    if a.get("doi") and b.get("doi") and a["doi"] != b["doi"]:
        return False, "distinct DOIs"
    ya, yb = year_of(a.get("publication_date")), year_of(b.get("publication_date"))
    if ya and yb and ya != yb:
        return False, f"different publication years ({ya} vs {yb})"
    if not exact_id_match and bool(a.get("is_preprint")) != bool(b.get("is_preprint")):
        return False, "preprint vs published, no id match"
    return True, ""


# TaskBoard, task id validation, the transition graph, input hashing, and the task-record
# shape all live in `taskboard.py` now (imported above) — SIMPLIFICATION_PLAN.md Phase C.


# --------------------------------------------------------------- screening intake


def screening_dir(run_dir: Path) -> Path:
    return run_dir / "workspace" / "screening"


def load_verdicts(run_dir: Path) -> dict[str, dict[str, dict]]:
    """-> {evidence_id: {screener_id: verdict}}"""
    out: dict[str, dict[str, dict]] = {}
    base = screening_dir(run_dir)
    if not base.is_dir():
        return out
    for sub in sorted(base.iterdir()):
        if not sub.is_dir() or sub.name == "adjudication":
            continue
        for f in sorted(sub.glob("*.json")):
            try:
                v = read_json(f)
            except json.JSONDecodeError as exc:
                warn(f"{f}: malformed screening verdict ({exc}) — skipped")
                continue
            if not isinstance(v, dict):
                warn(f"{f}: expected a screening verdict object, found "
                     f"{type(v).__name__} — skipped. workspace/screening/<screener>/ "
                     f"must contain only per-record verdict files; keep batch inputs "
                     f"and other working files elsewhere (e.g. workspace/batches/)")
                continue
            eid = verdict_evidence_id(v)
            sid = v.get("screener_id") or sub.name
            if v.get("decision") not in DECISIONS:
                warn(f"{f}: decision must be one of {DECISIONS} — skipped")
                continue
            out.setdefault(eid, {})[sid] = v
    return out


def load_adjudications(run_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    base = screening_dir(run_dir) / "adjudication"
    if not base.is_dir():
        return out
    for f in sorted(base.glob("*.json")):
        try:
            a = read_json(f)
        except json.JSONDecodeError as exc:
            warn(f"{f}: malformed adjudication record ({exc}) — skipped")
            continue
        if not isinstance(a, dict):
            warn(f"{f}: expected an adjudication record object, found "
                 f"{type(a).__name__} — skipped")
            continue
        if a.get("final_decision") not in DECISIONS:
            warn(f"{f}: final_decision must be one of {DECISIONS} — skipped")
            continue
        out[verdict_evidence_id(a)] = a
    return out


def verdict_evidence_id(obj: dict) -> str:
    raw = str(obj.get("pmid") or obj.get("evidence_id") or "").strip()
    if not raw:
        raise UserError("screening record has neither pmid nor evidence_id (schema.md §5)")
    if raw.isdigit():
        return f"pmid:{raw}"
    return raw


def resolve_final(verdicts: dict[str, dict], adj: dict | None) -> tuple[str | None, str, str | None]:
    """-> (decision, reason, criterion_failed). decision None = needs adjudication."""
    if adj:
        crit = None
        for v in verdicts.values():
            if v.get("decision") == adj["final_decision"]:
                crit = v.get("criterion_failed")
                break
        return adj["final_decision"], adj.get("rationale") or "", crit
    ids = sorted(verdicts)
    if len(ids) == 1:
        v = verdicts[ids[0]]
        return v["decision"], v.get("reason") or "", v.get("criterion_failed")
    decisions = {verdicts[i]["decision"] for i in ids}
    if len(decisions) == 1:
        v = verdicts[ids[0]]
        return v["decision"], v.get("reason") or "", v.get("criterion_failed")
    return None, "", None


def retraction_from(verdicts: dict[str, dict]) -> str:
    for v in verdicts.values():
        flag = v.get("retraction_flag")
        if flag and flag != "none":
            if flag not in RETRACTION:
                warn(f"unknown retraction_flag {flag!r} — ignored")
                continue
            return flag
    return "none"


def cohens_kappa(pairs: list[tuple[str, str]]) -> tuple[float | None, float | None]:
    """-> (raw_agreement, kappa). kappa is None when it is undefined (pe == 1)."""
    n = len(pairs)
    if n == 0:
        return None, None
    agree = sum(1 for a, b in pairs if a == b)
    po = agree / n
    cats = set(DECISIONS) | {a for a, _ in pairs} | {b for _, b in pairs}
    pe = 0.0
    for c in cats:
        pa = sum(1 for a, _ in pairs if a == c) / n
        pb = sum(1 for _, b in pairs if b == c) / n
        pe += pa * pb
    if abs(1.0 - pe) < 1e-12:
        return round(po, 4), None
    return round(po, 4), round((po - pe) / (1.0 - pe), 4)


# --------------------------------------------------------------------- commands


def cmd_init(args) -> int:
    run_dir = Path(args.run_dir)
    for sub in ("workspace/search", "workspace/screening", "workspace/extractions",
                "workspace/appraisals", "outputs", "inputs", "inbox", ".locks"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    for f in ("corpus.jsonl", "taskboard.jsonl", "engine.log"):
        p = run_dir / f
        if not p.exists():
            p.touch()
    print(json.dumps({"run_dir": str(run_dir), "status": "initialized"}, indent=2))
    return 0


def _load_input_records(args) -> list[dict]:
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.json:
        text = args.json
    else:
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        raise UserError("no input records supplied (--json / --file / stdin)")
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, list) else [obj]
    except json.JSONDecodeError:
        pass
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise UserError(f"input line {n}: {exc}")
    return out


def cmd_add(args) -> int:
    run_dir = Path(args.run_dir)
    incoming = _load_input_records(args)
    results = []
    with advisory_lock(run_dir, "corpus"):
        corpus = Corpus(run_dir, Path(args.corpus) if args.corpus else None).load()
        for raw in incoming:
            if args.query_id and not raw.get("first_seen_query"):
                raw = dict(raw, first_seen_query=args.query_id)
            rec = normalize_record(raw, allow_extra=args.allow_extra)
            action, eid = corpus.upsert(rec)
            results.append({"action": action, "evidence_id": eid})
        corpus.save()
    print(json.dumps({
        "added": sum(1 for r in results if r["action"] == "added"),
        "merged": sum(1 for r in results if r["action"] == "merged"),
        "records": results,
        "total": len(corpus.records),
    }, indent=2))
    return 0


def cmd_list(args) -> int:
    corpus = Corpus(Path(args.run_dir), Path(args.corpus) if args.corpus else None).load()
    recs = corpus.list()
    if args.decision:
        recs = [r for r in recs if (r.get("screening") or {}).get("decision") == args.decision]
    if args.fulltext_status:
        recs = [r for r in recs if r["fulltext"]["status"] == args.fulltext_status]
    if args.format == "json":
        print(json.dumps(recs, indent=2, ensure_ascii=False))
    elif args.format == "jsonl":
        for r in recs:
            print(canonical_json(r))
    else:
        for r in recs:
            dec = (r.get("screening") or {}).get("decision") or "-"
            print(f"{r['evidence_id']}\t{dec}\t{r['fulltext']['status']}\t{r['title'][:70]}")
    return 0


def cmd_get(args) -> int:
    corpus = Corpus(Path(args.run_dir)).load()
    rec = corpus.records.get(args.evidence_id)
    if rec is None:
        raise UserError(f"no corpus record with evidence_id {args.evidence_id!r}")
    print(json.dumps(rec, indent=2, ensure_ascii=False))
    return 0


def cmd_export(args) -> int:
    corpus = Corpus(Path(args.run_dir)).load()
    for rec in corpus.list():
        print(canonical_json(strict_schema_view(rec) if args.strict_schema else rec))
    return 0


def cmd_dedupe(args) -> int:
    run_dir = Path(args.run_dir)
    threshold = args.title_threshold
    merges: list[dict] = []
    blocked: list[dict] = []

    with advisory_lock(run_dir, "corpus"):
        corpus = Corpus(run_dir, Path(args.corpus) if args.corpus else None).load()

        def pass_exact(field: str, label: str) -> bool:
            seen: dict[str, str] = {}
            for eid in list(corpus.order):
                rec = corpus.records.get(eid)
                if rec is None:
                    continue
                key = rec.get(field)
                if not key:
                    continue
                if key in seen and seen[key] != eid:
                    a_eid = seen[key]
                    a, b = corpus.records[a_eid], rec
                    ok, why = can_merge(a, b, exact_id_match=True)
                    if not ok:
                        blocked.append({"a": a_eid, "b": eid, "match": label, "reason": why})
                        continue
                    merged = merge_records(a, b)
                    corpus.order.remove(eid)
                    del corpus.records[eid]
                    corpus._rekey(a_eid, merged)
                    merges.append({"kept": merged["evidence_id"], "absorbed": eid,
                                   "match": label, "key": key})
                    return True
                seen[key] = eid
            return False

        while pass_exact("pmid", "pmid"):
            pass
        while pass_exact("doi", "doi"):
            pass

        # pass 3: fuzzy normalized title
        changed = True
        while changed:
            changed = False
            eids = [e for e in corpus.order if e in corpus.records]
            for i in range(len(eids)):
                for j in range(i + 1, len(eids)):
                    a, b = corpus.records[eids[i]], corpus.records[eids[j]]
                    ta, tb = norm_title(a["title"]), norm_title(b["title"])
                    if not ta or not tb:
                        continue
                    ratio = difflib.SequenceMatcher(None, ta, tb).ratio()
                    if ratio < threshold:
                        continue
                    ok, why = can_merge(a, b, exact_id_match=False)
                    if not ok:
                        blocked.append({"a": eids[i], "b": eids[j], "match": "title",
                                        "ratio": round(ratio, 4), "reason": why})
                        continue
                    merged = merge_records(a, b)
                    corpus.order.remove(eids[j])
                    del corpus.records[eids[j]]
                    corpus._rekey(eids[i], merged)
                    merges.append({"kept": merged["evidence_id"], "absorbed": eids[j],
                                   "match": "title", "ratio": round(ratio, 4)})
                    changed = True
                    break
                if changed:
                    break

        if not args.dry_run:
            corpus.save()

    print(json.dumps({
        "dry_run": args.dry_run,
        "title_threshold": threshold,
        "duplicates_removed": len(merges),
        "merges": merges,
        "blocked_merges": blocked,
        "records_remaining": len(corpus.records),
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_validate(args) -> int:
    run_dir = Path(args.run_dir)
    errors: list[str] = []
    corpus = Corpus(run_dir).load()
    for rec in corpus.list():
        eid = rec["evidence_id"]
        ft = rec["fulltext"]
        if ft["status"] == "missing" and ft["access_route"] and ft["source_tier"] != 8:
            errors.append(f"{eid}: attempted-but-missing requires source_tier=8")
        if ft["truncation_detected"] and ft["status"] != "abstract_only":
            errors.append(f"{eid}: truncation_detected requires status=abstract_only")
        if eid != derive_evidence_id(rec):
            errors.append(f"{eid}: evidence_id violates S9 precedence "
                          f"(expected {derive_evidence_id(rec)})")
    board = TaskBoard(run_dir)
    for tid in board.state():
        try:
            validate_task_id(tid)
        except UserError as exc:
            errors.append(str(exc))
    print(json.dumps({"records": len(corpus.records), "errors": errors,
                      "status": "pass" if not errors else "fail"}, indent=2))
    return 0 if not errors else 1


# ---- PRISMA ---------------------------------------------------------------


def build_prisma(run_dir: Path) -> dict:
    corpus = Corpus(run_dir).load()
    recs = corpus.list()
    verdicts = load_verdicts(run_dir)
    adjudications = load_adjudications(run_dir)

    duplicates_removed = sum(len(r.get("merged_from") or []) for r in recs)
    identified = len(recs) + duplicates_removed

    by_source: dict[str, int] = {}
    for r in recs:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1

    # cross-check against the search-strategy log (schema.md §3)
    search_ids: set[str] = set()
    queries_logged = 0
    hit_counts: dict[str, int | None] = {}
    sdir = run_dir / "workspace" / "search"
    if sdir.is_dir():
        for f in sorted(sdir.glob("*.json")):
            if f.name.startswith("."):
                continue
            try:
                s = read_json(f)
            except json.JSONDecodeError as exc:
                warn(f"{f}: malformed search result record ({exc})")
                continue
            if not isinstance(s, dict):
                warn(f"{f}: expected a search result record object, found "
                     f"{type(s).__name__} — skipped. workspace/search/ must contain "
                     f"only search result records; keep efetch output and other "
                     f"working files elsewhere (e.g. workspace/fetch/)")
                continue
            queries_logged += 1
            if s.get("hit_count_logged") is not True:
                warn(f"{f}: hit_count_logged is not true — verifier check C-SEARCH-LOG "
                     f"will fail and the PRISMA flow will be blocked")
            search_ids.update(s.get("retrieved_ids") or [])
            hit_counts[s.get("query_id") or f.stem] = s.get("count")

    screened = [r for r in recs if r.get("screening")]
    included_screen = [r for r in screened if r["screening"]["decision"] == "include"]
    excluded = [r for r in screened if r["screening"]["decision"] == "exclude"]
    unclear = [r for r in screened if r["screening"]["decision"] == "unclear"]

    reasons: dict[str, int] = {}
    for r in excluded:
        eid = r["evidence_id"]
        crit = None
        adj = adjudications.get(eid)
        vs = verdicts.get(eid, {})
        _, _, crit = resolve_final(vs, adj) if vs else (None, "", None)
        key = crit or "unspecified"
        reasons[key] = reasons.get(key, 0) + 1

    sought = included_screen
    missing = [r for r in sought if r["fulltext"]["status"] == "missing"]
    unobtainable = [r for r in missing if r["fulltext"]["access_route"]]
    not_attempted = [r for r in missing if not r["fulltext"]["access_route"]]
    obtained = [r for r in sought if r["fulltext"]["status"] != "missing"]
    abstract_only = [r for r in obtained if r["fulltext"]["status"] == "abstract_only"]
    included = obtained
    extracted = [r for r in included if r.get("extraction_path")]
    appraised = [r for r in included if r.get("appraisal_path")]

    # dual screening agreement
    pairs: list[tuple[str, str]] = []
    for eid, vs in verdicts.items():
        a, b = vs.get("screener-a"), vs.get("screener-b")
        if a and b:
            pairs.append((a["decision"], b["decision"]))
    raw_agreement, kappa = cohens_kappa(pairs)
    disagreements = [
        eid for eid, vs in verdicts.items()
        if vs.get("screener-a") and vs.get("screener-b")
        and vs["screener-a"]["decision"] != vs["screener-b"]["decision"]
    ]
    dual = {
        "dual_screened": len(pairs),
        "disagreements": len(disagreements),
        "disagreement_rate": round(len(disagreements) / len(pairs), 4) if pairs else None,
        "raw_agreement": raw_agreement,
        "cohens_kappa": kappa,
        "adjudications_recorded": len(adjudications),
        "awaiting_adjudication": sorted(
            e for e in disagreements if e not in adjudications
        ),
    } if pairs else None

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "identification": {
            "records_identified": identified,
            "records_identified_by_source": by_source,
            "queries_logged": queries_logged,
            "hit_counts": hit_counts,
            "unique_ids_in_search_logs": len(search_ids),
            "duplicates_removed": duplicates_removed,
            "records_after_dedupe": len(recs),
        },
        "screening": {
            "records_screened": len(screened),
            "not_yet_screened": len(recs) - len(screened),
            "excluded": len(excluded),
            "excluded_by_reason": dict(sorted(reasons.items())),
            "unclear": len(unclear),
            "included_after_screening": len(included_screen),
        },
        "retrieval": {
            "fulltext_sought": len(sought),
            "fulltext_obtained": len(obtained),
            "fulltext_unobtainable": len(unobtainable),
            "unobtainable_ids": [r["evidence_id"] for r in unobtainable],
            "retrieval_not_yet_attempted": len(not_attempted),
            "abstract_only": len(abstract_only),
        },
        "included": {
            "studies_included": len(included),
            "with_extraction": len(extracted),
            "with_appraisal": len(appraised),
            "retracted_or_eoc": sum(
                1 for r in included if r["retraction_status"] != "none"
            ),
            "preprints": sum(1 for r in included if r["is_preprint"]),
        },
        "dual_screening": dual,
    }


def prisma_markdown(p: dict) -> str:
    i, s, r, inc = p["identification"], p["screening"], p["retrieval"], p["included"]
    lines = [
        "## PRISMA flow",
        "",
        f"_Generated {p['generated_at']} by `corpus.py prisma`._",
        "",
        "| Phase | Count |",
        "|---|---|",
        f"| Records identified (all sources) | {i['records_identified']} |",
        f"| Duplicates removed | {i['duplicates_removed']} |",
        f"| Records screened (title/abstract) | {s['records_screened']} |",
        f"| Records excluded | {s['excluded']} |",
        f"| Records unclear at screening | {s['unclear']} |",
        f"| Reports sought for retrieval | {r['fulltext_sought']} |",
        f"| Reports not retrieved (unobtainable) | {r['fulltext_unobtainable']} |",
        f"| Retrieval not yet attempted | {r['retrieval_not_yet_attempted']} |",
        f"| Reports assessed (full text or abstract-only) | {r['fulltext_obtained']} |",
        f"| — of which abstract-only | {r['abstract_only']} |",
        f"| Studies included in synthesis | {inc['studies_included']} |",
        f"| — with extraction record | {inc['with_extraction']} |",
        f"| — with appraisal record | {inc['with_appraisal']} |",
        f"| — preprints (flagged) | {inc['preprints']} |",
        f"| — retracted / expression of concern | {inc['retracted_or_eoc']} |",
        "",
        "### Records identified by source",
        "",
        "| Source | Records |",
        "|---|---|",
    ]
    for src, n in sorted(i["records_identified_by_source"].items()):
        lines.append(f"| {src} | {n} |")
    lines += ["", "### Query hit counts (schema.md §3)", "",
              "| query_id | hits |", "|---|---|"]
    if i["hit_counts"]:
        for qid, n in sorted(i["hit_counts"].items(), key=lambda kv: query_sort_key(kv[0])):
            lines.append(f"| {qid} | {'—' if n is None else n} |")
    else:
        lines.append("| — | no search result records found |")

    lines += ["", "### Exclusions with reasons", "",
              "| criterion_failed | Records excluded |", "|---|---|"]
    if s["excluded_by_reason"]:
        for crit, n in s["excluded_by_reason"].items():
            lines.append(f"| {crit} | {n} |")
    else:
        lines.append("| — | 0 |")

    if r["unobtainable_ids"]:
        lines += ["", "### Full text unobtainable (see `missing.md`)", ""]
        lines += [f"- `{eid}`" for eid in r["unobtainable_ids"]]

    d = p["dual_screening"]
    lines += ["", "### Dual screening agreement", ""]
    if d is None:
        lines.append("Single-screen profile: no dual-screening agreement statistics.")
    else:
        kappa = "undefined" if d["cohens_kappa"] is None else f"{d['cohens_kappa']:.4f}"
        lines += [
            "| Metric | Value |", "|---|---|",
            f"| Records dual-screened | {d['dual_screened']} |",
            f"| Disagreements | {d['disagreements']} |",
            f"| Disagreement rate | {d['disagreement_rate']} |",
            f"| Raw agreement | {d['raw_agreement']} |",
            f"| Cohen's kappa | {kappa} |",
            f"| Adjudication records | {d['adjudications_recorded']} |",
            f"| Awaiting adjudication | {len(d['awaiting_adjudication'])} |",
        ]
    return "\n".join(lines) + "\n"


def cmd_prisma(args) -> int:
    run_dir = Path(args.run_dir)
    p = build_prisma(run_dir)
    md = prisma_markdown(p)
    if args.out:
        outdir = run_dir / "outputs"
        atomic_write(outdir / "prisma.json", json.dumps(p, indent=2, ensure_ascii=False) + "\n")
        atomic_write(outdir / "prisma.md", md)
    if args.format in ("json", "both"):
        print(json.dumps(p, indent=2, ensure_ascii=False))
    if args.format in ("md", "both"):
        if args.format == "both":
            print()
        print(md, end="")
    return 0


# ---- screening intake -----------------------------------------------------


def cmd_screen_ingest(args) -> int:
    run_dir = Path(args.run_dir)
    verdicts = load_verdicts(run_dir)
    adjudications = load_adjudications(run_dir)
    updated, pending, unknown = [], [], []
    with advisory_lock(run_dir, "corpus"):
        corpus = Corpus(run_dir).load()
        for eid, vs in sorted(verdicts.items()):
            decision, reason, _ = resolve_final(vs, adjudications.get(eid))
            if decision is None:
                pending.append(eid)
                continue
            rec = corpus.records.get(eid)
            if rec is None:
                unknown.append(eid)
                continue
            rec["screening"] = {"decision": decision, "reason": reason[:300]}
            flag = retraction_from(vs)
            if flag != "none":
                rec["retraction_status"] = flag
            updated.append({"evidence_id": eid, "decision": decision,
                            "retraction_status": rec["retraction_status"]})
        if not args.dry_run:
            corpus.save()
    result = {
        "dry_run": args.dry_run,
        "verdict_files": sum(len(v) for v in verdicts.values()),
        "updated": len(updated),
        "records": updated,
        "awaiting_adjudication": sorted(pending),
        "verdicts_without_corpus_record": sorted(unknown),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if unknown and args.strict:
        return 1
    return 0


def cmd_disagreements(args) -> int:
    run_dir = Path(args.run_dir)
    verdicts = load_verdicts(run_dir)
    adjudications = load_adjudications(run_dir)
    corpus = Corpus(run_dir).load()
    rows = []
    for eid, vs in sorted(verdicts.items()):
        a, b = vs.get("screener-a"), vs.get("screener-b")
        if not (a and b) or a["decision"] == b["decision"]:
            continue
        rec = corpus.records.get(eid) or {}
        rows.append({
            "evidence_id": eid,
            "pmid": rec.get("pmid") or (eid.split(":", 1)[1] if eid.startswith("pmid:") else None),
            "screener_a_decision": a["decision"],
            "screener_b_decision": b["decision"],
            "screener_a_criterion": a.get("criterion_failed"),
            "screener_b_criterion": b.get("criterion_failed"),
            "adjudicated": eid in adjudications,
            "final_decision": (adjudications.get(eid) or {}).get("final_decision"),
            "title": rec.get("title"),
            "task_id": f"adjudicate:pmid:{rec.get('pmid')}" if rec.get("pmid")
            else f"adjudicate:slug:{slugify_key(eid)}",
        })
    if args.pending_only:
        rows = [r for r in rows if not r["adjudicated"]]
    if args.format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for r in rows:
            mark = "adjudicated" if r["adjudicated"] else "PENDING"
            print(f"{r['evidence_id']}\t{r['screener_a_decision']}/"
                  f"{r['screener_b_decision']}\t{mark}\t{(r['title'] or '')[:60]}")
    return 0


# ---- guardrails -----------------------------------------------------------


def cmd_query_check(args) -> int:
    run_dir = Path(args.run_dir)
    qhash = query_hash(args.query)
    index_path = run_dir / "workspace" / "search" / ".query-index.json"
    index = read_json(index_path) if index_path.exists() else {}

    executed: dict[str, dict] = {}
    sdir = run_dir / "workspace" / "search"
    if sdir.is_dir():
        for f in sorted(sdir.glob("*.json")):
            if f.name.startswith("."):
                continue
            try:
                s = read_json(f)
            except json.JSONDecodeError:
                continue
            qs = s.get("query_string")
            if not qs:
                continue
            executed[query_hash(qs)] = {
                "query_id": s.get("query_id"), "query_string": qs,
                "count": s.get("count"), "executed_at": s.get("executed_at"),
                "source": s.get("source"), "file": str(f.relative_to(run_dir)),
            }

    hit = executed.get(qhash) or index.get(qhash)
    result = {
        "query": args.query,
        "normalized_query": norm_query(args.query),
        "query_hash": qhash,
        "duplicate": hit is not None,
        "existing": hit,
        "action": "skip-or-merge-into-existing-result" if hit else "execute",
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if hit and args.fail_on_duplicate else 0


def cmd_query_register(args) -> int:
    run_dir = Path(args.run_dir)
    index_path = run_dir / "workspace" / "search" / ".query-index.json"
    with advisory_lock(run_dir, "query-index"):
        index = read_json(index_path) if index_path.exists() else {}
        qhash = query_hash(args.query)
        existing = index.get(qhash)
        if existing and existing.get("query_id") != args.query_id:
            print(json.dumps({"duplicate": True, "existing": existing}, indent=2))
            return 1
        index[qhash] = {
            "query_id": args.query_id, "query_string": args.query,
            "source": args.source, "registered_at": now_iso(),
        }
        atomic_write(index_path, json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"registered": args.query_id, "query_hash": qhash}, indent=2))
    return 0


def cmd_guard(args) -> int:
    """No-progress guard (`SKILL.md` "Pipeline" execution guardrails)."""
    run_dir = Path(args.run_dir)
    board = TaskBoard(run_dir)
    state = board.state()
    # progress fingerprint: task_id=status pairs, so blocked -> completed counts as progress
    done = sorted(f"{t}={r.get('status')}" for t, r in state.items()
                  if r.get("status") in ("completed", "blocked", "cancelled"))
    attempts_total = sum(int(r.get("attempts") or 0) for r in state.values())
    active = sorted(t for t, r in state.items() if r.get("status") == "active")
    fingerprint = hashlib.sha256("\n".join(done).encode("utf-8")).hexdigest()[:16]

    probe = {
        "at": now_iso(),
        "fingerprint": fingerprint,
        "done_count": len(done),
        "attempts_total": attempts_total,
        "records_total": len(board.history()),
        "active": len(active),
    }
    guard_path = run_dir / ".guard.json"
    with advisory_lock(run_dir, "guard"):
        hist = read_json(guard_path) if guard_path.exists() else {"probes": []}
        probes = hist.get("probes", [])
        if not args.no_record:
            probes.append(probe)
            probes = probes[-50:]
            atomic_write(guard_path, json.dumps({"probes": probes}, indent=2) + "\n")

    window = probes[-args.window:] if len(probes) >= args.window else probes
    stalled = (
        len(window) >= args.window
        and len({p["fingerprint"] for p in window}) == 1
        and window[-1]["attempts_total"] > window[0]["attempts_total"]
    )
    frozen = (
        len(window) >= args.window
        and len({(p["fingerprint"], p["attempts_total"], p["records_total"])
                 for p in window}) == 1
        and not active            # work in flight is not a stall
    )
    pending = [t for t, r in state.items() if r.get("status") in ("pending", "failed")]
    # A board with nothing queued and nothing in flight is idle, not stalled: the stage
    # finished and the next one has not been dispatched. Calling it a stall would send a
    # healthy run to a provisional report.
    idle = not pending and not active
    no_progress = bool(stalled or frozen) and not idle
    result = {
        "run_dir": str(run_dir),
        "window": args.window,
        "probes_recorded": len(probes),
        "probe": probe,
        "no_progress": no_progress,
        "reason": (None if idle else
                   "repeated task assignment with no newly completed/blocked work"
                   if stalled else
                   "no taskboard activity at all across the window" if frozen else None),
        "idle": idle,
        "pending_or_failed": sorted(pending),
        "active": active,
        "recommendation": ("bail-to-verification-with-provisional-report" if no_progress
                           else "dispatch-next-stage-or-finish" if idle else "continue"),
    }
    print(json.dumps(result, indent=2))
    return 1 if result["no_progress"] else 0


# The task CLI command functions (`_task_transition`, `cmd_task_*`, `_task_rows`) moved to
# `taskboard.py` — SIMPLIFICATION_PLAN.md Phase C. `build_task_parser` (imported above)
# wires the `task` subcommand below so `corpus.py task ...` behaves identically.


# ------------------------------------------------------------------------ cli


def add_run_dir(p: argparse.ArgumentParser) -> None:
    p.add_argument("--run-dir", required=True,
                   help="run directory <wiki>/outputs/deep-research/<slug>/")


def add_inputs(p: argparse.ArgumentParser) -> None:
    p.add_argument("--inputs", help="JSON object of canonical task inputs (hashed to inputs_hash)")
    p.add_argument("--inputs-file", help="path to a JSON file of canonical task inputs")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="corpus.py",
        description="corpus.jsonl store, dedupe, PRISMA counters, and the taskboard CLI "
                    "(the only writer of taskboard.jsonl).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create the run directory skeleton")
    add_run_dir(p)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("add", help="append/merge corpus records (exact-id merge)")
    add_run_dir(p)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--json", help="inline JSON object or array")
    g.add_argument("--file", help="path to a JSON / JSONL file")
    p.add_argument("--corpus", help="override corpus.jsonl path")
    p.add_argument("--query-id", help="stamp first_seen_query on records that lack it")
    p.add_argument("--allow-extra", action="store_true",
                   help="tolerate unknown fields instead of erroring")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="list corpus records")
    add_run_dir(p)
    p.add_argument("--corpus")
    p.add_argument("--decision", choices=DECISIONS)
    p.add_argument("--fulltext-status", choices=FULLTEXT_STATUS)
    p.add_argument("--format", choices=("table", "json", "jsonl"), default="table")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("get", help="print one corpus record")
    add_run_dir(p)
    p.add_argument("--evidence-id", required=True)
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("export", help="emit corpus.jsonl to stdout")
    add_run_dir(p)
    p.add_argument("--strict-schema", action="store_true",
                   help="drop the seen_in_queries/merged_from extensions")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("dedupe", help="merge duplicates: PMID -> DOI -> normalized title")
    add_run_dir(p)
    p.add_argument("--corpus")
    p.add_argument("--title-threshold", type=float, default=DEFAULT_TITLE_THRESHOLD,
                   help=f"difflib ratio threshold for title matching (default {DEFAULT_TITLE_THRESHOLD})")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_dedupe)

    p = sub.add_parser("validate", help="check corpus/taskboard invariants")
    add_run_dir(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("prisma", help="PRISMA counters + flow block")
    add_run_dir(p)
    p.add_argument("--format", choices=("json", "md", "both"), default="both")
    p.add_argument("--out", action="store_true",
                   help="also write outputs/prisma.json and outputs/prisma.md")
    p.set_defaults(func=cmd_prisma)

    p = sub.add_parser("screen-ingest",
                       help="fold screening verdicts/adjudications into corpus records")
    add_run_dir(p)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 when a verdict has no corpus record")
    p.set_defaults(func=cmd_screen_ingest)

    p = sub.add_parser("disagreements", help="list records needing adjudication")
    add_run_dir(p)
    p.add_argument("--pending-only", action="store_true")
    p.add_argument("--format", choices=("table", "json"), default="table")
    p.set_defaults(func=cmd_disagreements)

    p = sub.add_parser("query-check", help="duplicate-query guard (normalized query hash)")
    add_run_dir(p)
    p.add_argument("--query", required=True)
    p.add_argument("--fail-on-duplicate", action="store_true")
    p.set_defaults(func=cmd_query_check)

    p = sub.add_parser("query-register", help="register a query before execution")
    add_run_dir(p)
    p.add_argument("--query", required=True)
    p.add_argument("--query-id", required=True)
    p.add_argument("--source", choices=("pubmed", "europepmc", "web", "pool"),
                   default="pubmed")
    p.set_defaults(func=cmd_query_register)

    p = sub.add_parser("guard", help="no-progress guard; exit 1 when stalled")
    add_run_dir(p)
    p.add_argument("--window", type=int, default=3, help="probes compared (default 3)")
    p.add_argument("--no-record", action="store_true", help="inspect without recording a probe")
    p.set_defaults(func=cmd_guard)

    # ---- task -------------------------------------------------------------
    # taskboard.jsonl state machine (only writer). Command implementations live in
    # taskboard.py (SIMPLIFICATION_PLAN.md Phase C); this parser wiring stays here so
    # corpus.py remains the CLI facade.
    tp = sub.add_parser("task", help="taskboard.jsonl state machine (only writer)")
    tsub = tp.add_subparsers(dest="task_cmd", required=True)

    def task_cmd(name, help_, func, *, worker=False, output=False, error=False,
                 summary=False, inputs=True):
        q = tsub.add_parser(name, help=help_)
        add_run_dir(q)
        q.add_argument("--task-id", required=True, help="<stage>:<key-kind>:<key>")
        if worker:
            q.add_argument("--worker", help="logical worker id, e.g. extractor-03")
        if output:
            q.add_argument("--output-path", help="run-relative result file path")
        if error:
            q.add_argument("--error", help="single-line diagnostic")
        if summary:
            q.add_argument("--summary",
                           help="the subagent receipt's one-line summary, stored on the task")
        if inputs:
            add_inputs(q)
        q.set_defaults(func=func)
        return q

    task_cmd("create", "enqueue a pending task", cmd_task_create, output=True)
    task_cmd("claim", "pending|failed|blocked -> active (attempts+1)", cmd_task_claim,
             worker=True, output=True)
    task_cmd("complete", "active -> completed (output becomes immutable)",
             cmd_task_complete, worker=True, output=True, summary=True)
    task_cmd("fail", "active -> failed with diagnostics", cmd_task_fail,
             worker=True, output=True, error=True)
    task_cmd("block", "active -> blocked (external precondition missing)",
             cmd_task_block, worker=True, output=True, error=True)
    task_cmd("cancel", "-> cancelled (budget / dedupe / no-progress guard)",
             cmd_task_cancel, worker=True, error=True)
    task_cmd("reopen", "failed|blocked|cancelled -> pending", cmd_task_reopen)

    q = tsub.add_parser("list", help="list tasks")
    add_run_dir(q)
    q.add_argument("--stage", choices=STAGES)
    q.add_argument("--status", nargs="+", choices=TASK_STATUSES)
    q.add_argument("--format", choices=("table", "json", "ids"), default="table")
    q.set_defaults(func=cmd_task_list)

    q = tsub.add_parser("next", help="next claimable tasks (pending|failed), oldest first")
    add_run_dir(q)
    q.add_argument("--stage", choices=STAGES, required=True)
    q.add_argument("--limit", type=int, default=1)
    q.add_argument("--format", choices=("ids", "json"), default="ids")
    q.set_defaults(func=cmd_task_next)

    q = tsub.add_parser("show", help="show one task, optionally its full history")
    add_run_dir(q)
    q.add_argument("--task-id", required=True)
    q.add_argument("--history", action="store_true")
    q.set_defaults(func=cmd_task_show)

    q = tsub.add_parser("stats", help="task counts by stage and status")
    add_run_dir(q)
    q.set_defaults(func=cmd_task_stats)

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
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
