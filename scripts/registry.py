#!/usr/bin/env python3
"""registry.py — canonical paper registry at `<repo>/data/papers/registry.jsonl`.

Standalone-repo counterpart to the legacy wiki-level `pool.py` pool: one record per
`evidence_id` (schema.md S9 precedence: pmid > doi > pmcid > url), holding bibliographic
metadata plus intake lifecycle status (POOL_ARCHITECTURE_IMPLEMENTATION_PLAN.md
"Registry And Pool Model"). `pool.jsonl` is never hand-edited here — `generate_pool()`
regenerates it as a search-optimized projection so there is exactly one durable source of
truth (Open Decision 1: generated view, not a second persisted store).

Keyed and normalized with the same helpers as everywhere else in this skill
(`corpus.py derive_evidence_id`, `norm_pmid`/`norm_doi`/`norm_pmcid`) so a registry record
and a run's corpus record agree on identity without translation.

Subcommands
  add      --repo (--pmid|--doi) [--extract]     register a paper by identifier (efetch metadata)
  lookup   --repo (--pmid|--doi|--pmcid|--evidence-id)   find a registry record
  list     --repo                                dump registry entries
  pool     --repo                                regenerate data/papers/pool.jsonl from the registry
  promote  --repo --run-dir [--strict|--no-verify]  promote a run's extractions into the canonical store
  bib      --repo --out [--select all|extracted|appraised] [--project]   export BibTeX

Environment: python3, stdlib only. No pip installs.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import utcnow  # noqa: E402  (sibling module, stdlib-only)
import corpus as _corpus  # noqa: E402  (derive_evidence_id, norm_pmid/doi/pmcid, normalize_record)
import store as _store  # noqa: E402  (Store, StoreError: promotion span verification)
import render as _render  # noqa: E402  (build_entry, bib_key, atomic_write: repo-mode bib export)

SCHEMA_VERSION = 1

STATUS_VALUES = ("registered", "screening", "included", "excluded")
METADATA_STATUS_VALUES = ("pending", "partial", "complete")
ASSET_STATUS_VALUES = ("missing", "available")
EXTRACTION_STATUS_VALUES = ("not_started", "in_progress", "extracted")
APPRAISAL_STATUS_VALUES = ("not_appraised", "in_progress", "appraised")

# Registry records are bibliographic + lifecycle, never run-local (schema.md §4 fields that
# only mean something inside the run that produced them — screening verdict, first_seen_query —
# are intentionally excluded; extraction_path here means the *canonical* global path, not a
# run-workspace path).
REGISTRY_FIELDS = tuple(
    f for f in _corpus.CORPUS_FIELDS
    if f not in ("screening", "extraction_path", "appraisal_path", "first_seen_query", "fulltext")
) + _corpus.CORPUS_BIBLIO


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def repo_paths(repo_root: Path) -> dict[str, Path]:
    repo_root = Path(repo_root).expanduser().resolve()
    papers = repo_root / "data" / "papers"
    return {
        "repo_root": repo_root,
        "papers": papers,
        "registry": papers / "registry.jsonl",
        "pool": papers / "pool.jsonl",
        "extractions": papers / "extractions",
        "appraisals": papers / "appraisals",
        "sources": repo_root / "data" / "sources",
        "locks": repo_root / ".locks",
    }


@contextlib.contextmanager
def advisory_lock(repo_root: Path, name: str):
    """Cross-process exclusive lock, repo-scoped (`corpus.py advisory_lock` is run-scoped)."""
    import fcntl
    lock_dir = repo_paths(repo_root)["locks"]
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{name}.lock"
    with open(lock_path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class Registry:
    """The `<repo>/data/papers/registry.jsonl` store. One record per evidence_id."""

    def __init__(self, repo_root: Path):
        self.paths = repo_paths(repo_root)
        self.repo_root = self.paths["repo_root"]
        self.path = self.paths["registry"]
        self.records: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a corrupt registry line must never block add/lookup
                eid = rec.get("evidence_id")
                if eid:
                    self.records[eid] = rec

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for eid in sorted(self.records):
                fh.write(json.dumps(self.records[eid], ensure_ascii=False, sort_keys=False))
                fh.write("\n")
        tmp.replace(self.path)

    def lookup(self, *, evidence_id: str | None = None, pmid: str | None = None,
               doi: str | None = None, pmcid: str | None = None) -> dict | None:
        if evidence_id and evidence_id in self.records:
            return self.records[evidence_id]
        npmid = _corpus.norm_pmid(pmid)
        if npmid and f"pmid:{npmid}" in self.records:
            return self.records[f"pmid:{npmid}"]
        ndoi = _corpus.norm_doi(doi)
        if ndoi and f"doi:{ndoi}" in self.records:
            return self.records[f"doi:{ndoi}"]
        npmcid = _corpus.norm_pmcid(pmcid)
        if npmcid and f"pmcid:{npmcid}" in self.records:
            return self.records[f"pmcid:{npmcid}"]
        # Primary-key lookup misses a record keyed by a *different* identifier that
        # also carries this one (e.g. keyed pmid:123, looked up by its DOI): scan the
        # normalized identifier fields instead of giving up.
        if npmid or ndoi or npmcid:
            for rec in self.records.values():
                if npmid and _corpus.norm_pmid(rec.get("pmid")) == npmid:
                    return rec
                if ndoi and _corpus.norm_doi(rec.get("doi")) == ndoi:
                    return rec
                if npmcid and _corpus.norm_pmcid(rec.get("pmcid")) == npmcid:
                    return rec
        return None

    def register(self, raw: dict, *, allow_extra: bool = True) -> tuple[dict, bool]:
        """Normalize + upsert one bibliographic record. Never forces extraction (plan
        "Manual adds should register papers without forcing extraction")."""
        norm = _corpus.normalize_record(raw, allow_extra=allow_extra)
        eid = norm["evidence_id"]
        existing = self.records.get(eid)
        is_new = existing is None
        target = existing or {
            "schema_version": SCHEMA_VERSION,
            "evidence_id": eid,
            "status": "registered",
            "metadata_status": "pending",
            "asset_status": "missing",
            "extraction_status": "not_started",
            "appraisal_status": "not_appraised",
            "sources": [],
            "created_at": utcnow(),
        }
        for field in REGISTRY_FIELDS:
            if field in ("schema_version", "evidence_id"):
                continue
            val = norm.get(field)
            if val in (None, "", [], {}):
                continue
            target[field] = val  # a fresh register/re-register may correct stale metadata
        has_title = bool(target.get("title"))
        has_journal_or_date = bool(target.get("journal") or target.get("publication_date"))
        target["metadata_status"] = (
            "complete" if has_title and has_journal_or_date
            else "partial" if has_title else "pending"
        )
        target["updated_at"] = utcnow()
        self.records[eid] = target
        return target, is_new

    def set_asset(self, evidence_id: str, asset: dict) -> dict:
        rec = self.records.setdefault(evidence_id, {
            "schema_version": SCHEMA_VERSION, "evidence_id": evidence_id,
            "status": "registered", "metadata_status": "pending", "asset_status": "missing",
            "extraction_status": "not_started", "appraisal_status": "not_appraised",
            "sources": [], "created_at": utcnow(),
        })
        rec["asset"] = asset
        rec["asset_status"] = "available"
        rec["updated_at"] = utcnow()
        return rec

    def set_extraction(self, evidence_id: str, extraction_path: str) -> dict:
        rec = self.records.setdefault(evidence_id, {
            "schema_version": SCHEMA_VERSION, "evidence_id": evidence_id,
            "status": "registered", "metadata_status": "pending", "asset_status": "missing",
            "extraction_status": "not_started", "appraisal_status": "not_appraised",
            "sources": [], "created_at": utcnow(),
        })
        rec["extraction_path"] = extraction_path
        rec["extraction_status"] = "extracted"
        rec["updated_at"] = utcnow()
        return rec

    def set_appraisal(self, evidence_id: str, project: str, appraisal_path: str) -> dict:
        """Appraisal storage is project-scoped, never a universal paper property (plan
        "Appraisal Storage": a paper can be strong evidence for one question and weak for
        another) — `appraisals` is a `{project: path}` map, not a single field."""
        rec = self.records.setdefault(evidence_id, {
            "schema_version": SCHEMA_VERSION, "evidence_id": evidence_id,
            "status": "registered", "metadata_status": "pending", "asset_status": "missing",
            "extraction_status": "not_started", "appraisal_status": "not_appraised",
            "sources": [], "created_at": utcnow(),
        })
        appraisals = rec.setdefault("appraisals", {})
        appraisals[project] = appraisal_path
        rec["appraisal_status"] = "appraised"
        rec["updated_at"] = utcnow()
        return rec

    def generate_pool(self) -> list[dict]:
        """Regenerate `pool.jsonl` as a search-optimized projection of the registry.

        A pure function of `self.records`; never accumulates state pool.jsonl itself
        would not otherwise have (Open Decision 1: generated view, not persisted truth).
        """
        pool_path = self.paths["pool"]
        pool_path.parent.mkdir(parents=True, exist_ok=True)
        entries = [self.records[eid] for eid in sorted(self.records)]
        tmp = pool_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for rec in entries:
                fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=False))
                fh.write("\n")
        tmp.replace(pool_path)
        return entries


def _author_str(entry) -> str:
    if not isinstance(entry, dict):
        return str(entry)
    if entry.get("collective"):
        return str(entry["collective"])
    return " ".join(x for x in (entry.get("family"), entry.get("initials")) if x) or ""


def _efetch_to_registry_raw(rec: dict) -> dict:
    """`eutils.py efetch` record -> corpus-shaped raw dict (`corpus.py normalize_record`
    expects `journal` as a string and `authors` as a list of strings; efetch's richer
    structured `journal`/`authors` are `okf.py build_biblio`'s job, not the registry's)."""
    journal = rec.get("journal") or {}
    journal_str = (journal.get("iso_abbrev") or journal.get("title")
                  if isinstance(journal, dict) else journal)
    authors = [_author_str(a) for a in (rec.get("authors") or [])]
    out = dict(rec)
    out["journal"] = journal_str or None
    out["authors"] = [a for a in authors if a]
    if isinstance(journal, dict) and journal.get("issn"):
        out["issn"] = journal["issn"]
    return out


def _efetch_record(pmid: str) -> dict:
    """Fetch one PMID's normalized bibliographic JSON via `eutils.py efetch` (in-process,
    no subprocess — reuses its NCBI throttle/retry policy)."""
    import eutils as _eutils
    result = _eutils.efetch(pmids=[pmid])
    records = result.get("records") or []
    if not records:
        raise SystemExit(f"efetch returned no record for pmid {pmid}")
    return _efetch_to_registry_raw(records[0])


def _esearch_doi(doi: str) -> str | None:
    """DOI -> PMID via PubMed's own DOI field search, then efetch by PMID."""
    import eutils as _eutils
    query = f'{doi}[AID]'
    result = _eutils.esearch(query=query, retmax=1)
    pmids = result.get("retrieved_pmids") or []
    return pmids[0] if pmids else None


def cmd_add_pdf(args) -> int:
    """`data/sources/assets/sha256-<hash>.pdf` (plan "Target Repository Layout") — flat,
    content-addressed, distinct from `library.py`'s wiki-shaped `<wiki>/assets/papers/`
    (pmid/doi-named files + index.json manifest). Reuses `library.py`'s pure PDF helpers
    (`sha256_file`, `pdf_pages`, `doi_from_pdf`) without its wiki-coupled `Library` class."""
    import library as _library_mod
    pdf = Path(args.file).expanduser().resolve()
    if not pdf.exists():
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "add-pdf",
              "error": f"no such file: {pdf}"})
        return 1
    registry = Registry(args.repo)
    digest = _library_mod.sha256_file(pdf)
    assets_dir = registry.paths["sources"] / "assets"
    dest = assets_dir / f"sha256-{digest}.pdf"

    doi = args.doi or _library_mod.doi_from_pdf(pdf)
    raw = {
        "pmid": args.pmid, "doi": doi, "pmcid": args.pmcid,
        "title": args.title or pdf.stem,
    }
    with advisory_lock(args.repo, "registry"):
        rec, is_new = registry.register(raw)
        eid = rec["evidence_id"]
        if not dest.exists():
            assets_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            if args.move:
                shutil.move(str(pdf), str(dest))
            else:
                shutil.copy2(str(pdf), str(dest))
        elif args.move:
            pdf.unlink(missing_ok=True)
        asset = {
            "sha256": digest, "path": str(dest.relative_to(registry.repo_root)),
            "bytes": dest.stat().st_size, "pages": _library_mod.pdf_pages(dest),
            "added_at": utcnow(),
        }
        rec = registry.set_asset(eid, asset)
        registry.save()
        registry.generate_pool()
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "add-pdf",
          "evidence_id": eid, "is_new": is_new, "asset": asset, "record": rec})
    return 0


_BIB_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,]*),", re.IGNORECASE)
_BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*", re.IGNORECASE)


def parse_bibtex(text: str) -> list[dict]:
    """Minimal stdlib-only `.bib` reader: `{@type{key, field = {value}, ...}` entries with
    brace- or quote-delimited values. No external bibtex library (this skill installs
    nothing — see module docstring); good enough for Zotero/EndNote/PubMed `.bib` exports,
    not a full BibTeX grammar (no `@string` macros, no `#` concatenation)."""
    entries = []
    pos = 0
    for m in _BIB_ENTRY_RE.finditer(text):
        entrytype = m.group(1).lower()
        if entrytype in ("comment", "string", "preamble"):
            continue
        citekey = m.group(2).strip()
        body_start = m.end()
        depth = 1
        i = body_start
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[body_start:i - 1]
        fields: dict[str, str] = {}
        fm_iter = list(_BIB_FIELD_RE.finditer(body))
        for idx, fm in enumerate(fm_iter):
            name = fm.group(1).lower()
            vstart = fm.end()
            vend = fm_iter[idx + 1].start() if idx + 1 < len(fm_iter) else len(body)
            raw_val = body[vstart:vend].strip().rstrip(",").strip()
            raw_val = raw_val.strip('{}"').strip()
            raw_val = re.sub(r"\s+", " ", raw_val)
            fields[name] = raw_val
        entries.append({"entrytype": entrytype, "citekey": citekey, "fields": fields})
        pos = i
    return entries


def _bibtex_to_registry_raw(fields: dict) -> dict:
    authors = []
    author_field = fields.get("author")
    if author_field:
        authors = [a.strip() for a in re.split(r"\s+and\s+", author_field) if a.strip()]
    pmid = fields.get("pmid")
    if not pmid:
        note = fields.get("note") or ""
        pm = re.search(r"PMID:?\s*(\d+)", note, re.IGNORECASE)
        pmid = pm.group(1) if pm else None
    return {
        "pmid": pmid, "doi": fields.get("doi"), "title": fields.get("title"),
        "journal": fields.get("journal") or fields.get("journaltitle"),
        "publication_date": fields.get("year"), "authors": authors,
        "volume": fields.get("volume"), "issue": fields.get("number"),
        "pages": fields.get("pages"), "issn": fields.get("issn"),
        "abstract": fields.get("abstract"),
    }


def cmd_import_bib(args) -> int:
    path = Path(args.file).expanduser().resolve()
    text = path.read_text(encoding="utf-8")
    parsed = parse_bibtex(text)
    registry = Registry(args.repo)
    results = []
    with advisory_lock(args.repo, "registry"):
        for entry in parsed:
            raw = _bibtex_to_registry_raw(entry["fields"])
            if not raw.get("title"):
                results.append({"citekey": entry["citekey"], "status": "skipped",
                                "reason": "no title field"})
                continue
            rec, is_new = registry.register(raw)
            results.append({"citekey": entry["citekey"], "status": "ok",
                            "evidence_id": rec["evidence_id"], "is_new": is_new})
        registry.save()
        registry.generate_pool()
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "import-bib",
          "repo": str(registry.repo_root), "file": str(path), "entries_seen": len(parsed),
          "registered": sum(1 for r in results if r["status"] == "ok"),
          "new": sum(1 for r in results if r.get("is_new")),
          "skipped": sum(1 for r in results if r["status"] == "skipped"),
          "records": results})
    return 0


def cmd_import_folder(args) -> int:
    import library as _library_mod
    import shutil
    folder = Path(args.dir).expanduser().resolve()
    pdfs = sorted(folder.rglob("*.pdf")) if args.recursive else sorted(folder.glob("*.pdf"))
    registry = Registry(args.repo)
    assets_dir = registry.paths["sources"] / "assets"
    results = []
    with advisory_lock(args.repo, "registry"):
        for pdf in pdfs:
            digest = _library_mod.sha256_file(pdf)
            dest = assets_dir / f"sha256-{digest}.pdf"
            doi = _library_mod.doi_from_pdf(pdf)
            raw = {"doi": doi, "title": pdf.stem}
            rec, is_new = registry.register(raw)
            eid = rec["evidence_id"]
            if not dest.exists():
                assets_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(pdf), str(dest))
            asset = {
                "sha256": digest, "path": str(dest.relative_to(registry.repo_root)),
                "bytes": dest.stat().st_size, "pages": _library_mod.pdf_pages(dest),
                "added_at": utcnow(),
            }
            registry.set_asset(eid, asset)
            results.append({"file": str(pdf), "evidence_id": eid, "is_new": is_new})
        registry.save()
        registry.generate_pool()
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "import-folder",
          "repo": str(registry.repo_root), "dir": str(folder), "files_seen": len(pdfs),
          "registered": len(results), "new": sum(1 for r in results if r["is_new"]),
          "records": results})
    return 0


def cmd_add(args) -> int:
    if not args.pmid and not args.doi:
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "add",
              "error": "one of --pmid/--doi is required"})
        return 2
    registry = Registry(args.repo)
    pmid = _corpus.norm_pmid(args.pmid)
    if not pmid and args.doi:
        pmid = _esearch_doi(args.doi)
    raw: dict = {}
    if pmid:
        raw = _efetch_record(pmid)
    elif args.doi:
        raw = {"doi": args.doi, "title": args.title}
    if args.doi and not raw.get("doi"):
        raw["doi"] = args.doi
    if not raw.get("title"):
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "add",
              "error": "could not resolve a title for this identifier "
                       "(pass --title to register with partial metadata)"})
        return 1
    with advisory_lock(args.repo, "registry"):
        rec, is_new = registry.register(raw)
        registry.save()
        registry.generate_pool()
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "add",
          "evidence_id": rec["evidence_id"], "is_new": is_new, "record": rec})
    return 0


def cmd_lookup(args) -> int:
    registry = Registry(args.repo)
    rec = registry.lookup(evidence_id=args.evidence_id, pmid=args.pmid, doi=args.doi,
                          pmcid=args.pmcid)
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "lookup",
          "matched": rec is not None, "record": rec})
    return 0 if rec is not None else 1


def cmd_list(args) -> int:
    registry = Registry(args.repo)
    entries = sorted(registry.records.values(), key=lambda r: r.get("evidence_id") or "")
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "list",
          "count": len(entries), "entries": entries[: args.limit]})
    return 0


def extraction_slug(evidence_id: str) -> str:
    """`pmid:12345678` -> `pmid-12345678`; `doi:10.1000/example` -> `doi-10.1000-example`
    (plan "Target Repository Layout" example filenames — literal, not `render.py bib_key`,
    which strips punctuation `pool.py`/bib-key style)."""
    return (evidence_id or "").replace(":", "-", 1).replace("/", "-")


def _extraction_spans(rec: dict) -> list[dict]:
    """Every claim-span record in an extraction (schema.md §7): top-level `spans` and
    each `outcomes[].spans`. Mirrors `assemble.py _spans_of`, scoped to what promotion
    needs to verify — not a full re-implementation of the assembler's artifact model."""
    spans: list[dict] = []
    top = rec.get("spans")
    if isinstance(top, list):
        spans.extend(s for s in top if isinstance(s, dict))
    for outcome in rec.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        osp = outcome.get("spans")
        if isinstance(osp, list):
            spans.extend(s for s in osp if isinstance(s, dict))
    return spans


def _verify_extraction_for_promotion(
        eid: str, src_path: Path, span_store: "_store.Store") -> tuple[dict | None, str | None]:
    """Priority 6 (POOL_ARCHITECTURE_OPTIMIZATION_PLAN.md): before promotion, confirm the
    extraction's own `evidence_id` matches the corpus record and every span verifies.

    Returns `(extraction, None)` on success — `extraction` has a missing `evidence_id`
    filled in (the only correction ever made) — or `(None, reason)` to skip promotion.
    """
    try:
        extraction = json.loads(src_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable extraction JSON: {exc}"
    if not isinstance(extraction, dict):
        return None, "extraction is not a JSON object"
    rec_eid = extraction.get("evidence_id")
    if rec_eid is None:
        extraction["evidence_id"] = eid
    elif rec_eid != eid:
        return None, f"extraction evidence_id {rec_eid!r} disagrees with corpus evidence_id {eid!r}"
    for i, span in enumerate(_extraction_spans(extraction)):
        result = span_store.verify_span(span)
        if not result["ok"]:
            return None, f"span[{i}] {result['reason_code']}: {result['detail']}"
    return extraction, None


def cmd_promote(args) -> int:
    """Stage 5 completion: copy a run's workspace extractions into the canonical
    `data/papers/extractions/` store and update the registry (plan "Stage 5 Promotion").

    The run keeps its workspace copy for auditability; only the corpus record's
    `extraction_path` is repointed at the canonical, repo-relative path so later reuse
    (this run or any other) prefers the canonical copy over a run-local pointer.

    By default (Priority 6, POOL_ARCHITECTURE_OPTIMIZATION_PLAN.md), each extraction is
    verified before promotion: its `evidence_id` must match the corpus record, and every
    span it carries must verify against the run-local or global source store. A record
    that fails either check is skipped, never silently promoted. `--no-verify` restores
    the old copy-only behavior (migration/debugging). `--strict` makes any skip a hard
    failure (exit 1) instead of a best-effort partial promotion.
    """
    run_dir = Path(args.run_dir).expanduser().resolve()
    corpus_path = Path(args.corpus) if args.corpus else run_dir / "corpus.jsonl"
    repo_root = Path(args.repo).expanduser().resolve()
    registry = Registry(repo_root)
    extractions_dir = registry.paths["extractions"]
    verify = not args.no_verify
    span_store = _store.Store(run_dir, repo_root=repo_root) if verify else None

    promoted, skipped = [], []
    with advisory_lock(args.repo, "registry"), _corpus.advisory_lock(run_dir, "corpus"):
        corpus = _corpus.Corpus(run_dir, corpus_path).load()
        for eid in list(corpus.order):
            rec = corpus.records.get(eid)
            if rec is None:
                continue
            rel = rec.get("extraction_path")
            if not rel:
                continue
            src_path = (run_dir / rel) if not Path(rel).is_absolute() else Path(rel)
            if not src_path.exists():
                skipped.append({"evidence_id": eid, "reason": f"missing file: {src_path}"})
                continue

            extraction = None
            if verify:
                extraction, reason = _verify_extraction_for_promotion(eid, src_path, span_store)
                if reason is not None:
                    skipped.append({"evidence_id": eid, "reason": reason})
                    continue

            dest_path = extractions_dir / f"{extraction_slug(eid)}.json"
            if not args.dry_run:
                extractions_dir.mkdir(parents=True, exist_ok=True)
                payload = (json.dumps(extraction, indent=2, ensure_ascii=False) + "\n"
                          if extraction is not None else src_path.read_text(encoding="utf-8"))
                dest_path.write_text(payload, encoding="utf-8")
                canonical_rel = str(dest_path.relative_to(registry.repo_root))
                registry.register({k: rec.get(k) for k in REGISTRY_FIELDS if rec.get(k)}
                                  | {"evidence_id": eid})
                registry.set_extraction(eid, canonical_rel)
                rec["extraction_path"] = canonical_rel
            promoted.append({"evidence_id": eid, "canonical_path": str(dest_path)})
        if not args.dry_run:
            registry.save()
            registry.generate_pool()
            corpus.save()

    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "promote",
          "dry_run": args.dry_run, "verify": verify, "strict": bool(args.strict),
          "repo": str(registry.repo_root), "run_dir": str(run_dir),
          "promoted": len(promoted), "skipped": len(skipped),
          "records": promoted, "skipped_records": skipped})
    return 1 if (args.strict and skipped) else 0


def cmd_appraise_promote(args) -> int:
    """Promote a run's workspace appraisals into `data/papers/appraisals/<project>/`
    (plan "Stage 5 Promotion" pattern, applied to appraisal instead of extraction — see
    `set_appraisal` for why this is project-scoped, not global)."""
    run_dir = Path(args.run_dir).expanduser().resolve()
    corpus_path = Path(args.corpus) if args.corpus else run_dir / "corpus.jsonl"
    registry = Registry(args.repo)
    project_dir = registry.paths["appraisals"] / args.project

    promoted, skipped = [], []
    with advisory_lock(args.repo, "registry"), _corpus.advisory_lock(run_dir, "corpus"):
        corpus = _corpus.Corpus(run_dir, corpus_path).load()
        for eid in list(corpus.order):
            rec = corpus.records.get(eid)
            if rec is None:
                continue
            rel = rec.get("appraisal_path")
            if not rel:
                continue
            src_path = (run_dir / rel) if not Path(rel).is_absolute() else Path(rel)
            if not src_path.exists():
                skipped.append({"evidence_id": eid, "reason": f"missing file: {src_path}"})
                continue
            dest_path = project_dir / f"{extraction_slug(eid)}.json"
            if not args.dry_run:
                project_dir.mkdir(parents=True, exist_ok=True)
                dest_path.write_text(src_path.read_text(encoding="utf-8"), encoding="utf-8")
                canonical_rel = str(dest_path.relative_to(registry.repo_root))
                registry.set_appraisal(eid, args.project, canonical_rel)
                rec["appraisal_path"] = canonical_rel
            promoted.append({"evidence_id": eid, "canonical_path": str(dest_path)})
        if not args.dry_run:
            registry.save()
            registry.generate_pool()
            corpus.save()

    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "appraise-promote",
          "dry_run": args.dry_run, "repo": str(registry.repo_root), "run_dir": str(run_dir),
          "project": args.project, "promoted": len(promoted), "skipped": len(skipped),
          "records": promoted, "skipped_records": skipped})
    return 0


def cmd_pool(args) -> int:
    registry = Registry(args.repo)
    with advisory_lock(args.repo, "registry"):
        entries = registry.generate_pool()
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "pool",
          "repo": str(registry.repo_root), "pool": str(registry.paths["pool"]),
          "count": len(entries)})
    return 0


def cmd_bib(args) -> int:
    """Priority 7 (POOL_ARCHITECTURE_OPTIMIZATION_PLAN.md): repo-mode BibTeX export,
    parallel to the legacy wiki-mode `pool.py bib` but reading the registry instead of the
    wiki pool. `--select appraised` requires `--project` — appraisal is project-scoped
    (`set_appraisal`), never a universal paper property."""
    registry = Registry(args.repo)
    records = list(registry.records.values())
    if args.select == "extracted":
        records = [r for r in records if r.get("extraction_path")]
    elif args.select == "appraised":
        if not args.project:
            emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "bib",
                  "error": "--select appraised requires --project"})
            return 2
        records = [r for r in records if (r.get("appraisals") or {}).get(args.project)]
    elif args.project:
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "bib",
              "error": "--project only applies to --select appraised"})
        return 2

    by_key = {}
    for rec in records:
        by_key[_render.bib_key(rec["evidence_id"])] = rec
    header = (
        "% refs.bib — GENERATED by scripts/registry.py bib. Do not hand-edit; "
        "regenerating overwrites.\n"
        f"% source: {registry.path}  entries: {len(by_key)}  selection: {args.select}"
        f"{' project=' + args.project if args.project else ''}\n"
        "% citation key = evidence_id with all non-alphanumerics stripped "
        "(references/schema.md R6)\n\n"
    )
    entries = [_render.build_entry(by_key[key]) for key in sorted(by_key)]
    out_path = Path(args.out)
    _render.atomic_write(out_path, header + "\n".join(entries))
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "bib",
          "repo": str(registry.repo_root), "out": str(out_path),
          "registry_size": len(registry.records), "entries_written": len(entries),
          "selection": args.select, "project": args.project})
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="registry.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add", help="register a paper by PMID/DOI")
    s.add_argument("--repo", required=True)
    s.add_argument("--pmid")
    s.add_argument("--doi")
    s.add_argument("--title", help="fallback title when metadata cannot be resolved (DOI only)")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("add-pdf", help="register a paper from a local PDF (sha256 dedupe)")
    s.add_argument("--repo", required=True)
    s.add_argument("--file", required=True, help="path to the PDF")
    s.add_argument("--pmid")
    s.add_argument("--doi", help="default: extracted from the PDF if present")
    s.add_argument("--pmcid")
    s.add_argument("--title", help="default: the PDF's filename stem")
    s.add_argument("--move", action="store_true", help="move instead of copy into the asset store")
    s.set_defaults(func=cmd_add_pdf)

    s = sub.add_parser("import-bib", help="bulk-register papers from a .bib file")
    s.add_argument("--repo", required=True)
    s.add_argument("--file", required=True, help="path to the .bib file")
    s.set_defaults(func=cmd_import_bib)

    s = sub.add_parser("import-folder", help="bulk-register PDFs from a folder (sha256 dedupe)")
    s.add_argument("--repo", required=True)
    s.add_argument("--dir", required=True, help="folder of PDFs")
    s.add_argument("--recursive", action="store_true")
    s.set_defaults(func=cmd_import_folder)

    s = sub.add_parser("lookup", help="find a registered paper")
    s.add_argument("--repo", required=True)
    s.add_argument("--evidence-id", dest="evidence_id")
    s.add_argument("--pmid")
    s.add_argument("--doi")
    s.add_argument("--pmcid")
    s.set_defaults(func=cmd_lookup)

    s = sub.add_parser("list", help="dump registry entries")
    s.add_argument("--repo", required=True)
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("pool", help="regenerate data/papers/pool.jsonl from the registry")
    s.add_argument("--repo", required=True)
    s.set_defaults(func=cmd_pool)

    s = sub.add_parser("bib", help="export repo-wide or scoped BibTeX from the registry")
    s.add_argument("--repo", required=True)
    s.add_argument("--out", required=True, help="output .bib path")
    s.add_argument("--select", choices=("all", "extracted", "appraised"), default="all")
    s.add_argument("--project", help="restrict to this project's appraised records "
                                     "(required with --select appraised)")
    s.set_defaults(func=cmd_bib)

    s = sub.add_parser("promote", help="promote a run's extractions into the canonical store")
    s.add_argument("--repo", required=True)
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--corpus", help="corpus.jsonl (default: <run-dir>/corpus.jsonl)")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--strict", action="store_true",
                   help="exit 1 if any extraction is skipped (missing file, evidence_id "
                        "mismatch, or an unverifiable span); default is best-effort")
    s.add_argument("--no-verify", action="store_true",
                   help="skip evidence_id/span verification and copy extractions as-is "
                        "(the pre-priority-6 behavior; for migration/debugging)")
    s.set_defaults(func=cmd_promote)

    s = sub.add_parser("appraise-promote",
                       help="promote a run's appraisals into a project's appraisal store")
    s.add_argument("--repo", required=True)
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--project", required=True,
                   help="project/protocol id (data/papers/appraisals/<project>/)")
    s.add_argument("--corpus", help="corpus.jsonl (default: <run-dir>/corpus.jsonl)")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_appraise_promote)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
