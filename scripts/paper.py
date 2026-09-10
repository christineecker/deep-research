#!/usr/bin/env python3
"""paper.py — single-paper and selected-paper summary profiles.

Implements `SINGLE_PAPER_SUMMARY_IMPLEMENTATION_PLAN.md` / `references/single-paper-summary.md`:
a lightweight run profile that reuses the standalone-repo pool (`references/pool-architecture.md`)
instead of the full Stage 0-8 review pipeline (`SKILL.md`). This script never dispatches a
subagent and never calls an LLM — like every other stage script in this skill, extraction (Stage
5), appraisal (Stage 6), and summary assembly are subagent work (`references/prompts/extract.md`,
`references/prompts/appraise.md`, `references/prompts/summarize-paper.md`), dispatched by the
orchestrating agent. This script does the deterministic parts: registry lookup/registration,
source acquisition, extraction/appraisal reuse, taskboard bookkeeping for what still needs a
subagent, summary rendering, and verification.

Subcommands
  summarize       one paper -> one §14 summary record + Markdown/HTML export
  summarize-set   several papers (explicit or bounded discovery) -> one §14 record per paper,
                  optionally one §15 set manifest with a bounded overview

`status` in every JSON payload tells the caller what to do next: `pending_extraction`,
`pending_appraisal`, `pending_summary`, `completed`, or `failed`. Re-running the same command
after the named subagent has written its output file resumes from where it left off.

Environment: python3, stdlib only. No pip installs.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import slugify, utcnow  # noqa: E402
import corpus as _corpus  # noqa: E402
import registry as _registry  # noqa: E402
import store as _store  # noqa: E402
import taskboard as _taskboard  # noqa: E402
import paper_summary as _ps  # noqa: E402

SCHEMA_VERSION = 1

DEFAULT_DISCOVERY_LIMIT = 10
HARD_DISCOVERY_LIMIT = 25


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


# --------------------------------------------------------------------- repo/run scaffolding


def require_repo(repo_arg: str) -> Path:
    repo_root = Path(repo_arg).expanduser().resolve()
    if not (repo_root / "data" / "papers").is_dir():
        raise SystemExit(
            f"paper.py: {repo_root} is not a standalone repo (no data/papers/). "
            "Run `python3 scripts/research.py init <path>` first."
        )
    return repo_root


def run_slug_for(evidence_id: str, purpose: str) -> str:
    return slugify(f"single-paper-{_ps.evidence_slug(evidence_id)}-{purpose}", maxlen=120)


def ensure_run_dir(repo_root: Path, slug: str, *, config_extra: dict) -> Path:
    run_dir = repo_root / "runs" / slug
    (run_dir / "workspace" / "extractions").mkdir(parents=True, exist_ok=True)
    (run_dir / "workspace" / "appraisals").mkdir(parents=True, exist_ok=True)
    (run_dir / "workspace" / "summaries").mkdir(parents=True, exist_ok=True)
    (run_dir / "outputs").mkdir(parents=True, exist_ok=True)
    config_path = run_dir / "config.json"
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}
    config.setdefault("schema_version", SCHEMA_VERSION)
    config.setdefault("profile", "single-paper-summary")
    config.setdefault("created_at", utcnow())
    config["repo_root"] = str(repo_root)
    config.update(config_extra)
    config["updated_at"] = utcnow()
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    protocol_path = run_dir / "protocol.md"
    if not protocol_path.exists():
        protocol_path.write_text(
            f"# {config.get('purpose', 'single-paper summary')} — {slug}\n\n"
            "Single-paper/selected-paper summary run. Not a full review protocol — "
            "see `references/single-paper-summary.md`.\n",
            encoding="utf-8")
    return run_dir


def write_corpus_record(run_dir: Path, registry_rec: dict) -> dict:
    corpus_path = run_dir / "corpus.jsonl"
    corpus = _corpus.Corpus(run_dir, corpus_path).load()
    raw = {k: v for k, v in registry_rec.items()
          if k in _corpus.CORPUS_FIELDS or k in _corpus.CORPUS_BIBLIO}
    raw.pop("extraction_path", None)
    raw.pop("appraisal_path", None)
    norm = _corpus.normalize_record(raw, allow_extra=True)
    with _corpus.advisory_lock(run_dir, "corpus"):
        corpus.upsert(norm)
        corpus.save()
    return corpus.records[norm["evidence_id"]]


# --------------------------------------------------------------------- identifier resolution


def resolve_paper(repo_root: Path, args) -> dict:
    """Register (or look up) exactly one paper. Returns the registry record."""
    registry = _registry.Registry(repo_root)
    if args.evidence_id:
        rec = registry.lookup(evidence_id=args.evidence_id)
        if rec is not None:
            return rec
        kind, _, key = args.evidence_id.partition(":")
        if kind not in ("pmid", "doi", "pmcid"):
            raise SystemExit(f"paper.py: no registry record for {args.evidence_id!r}; "
                             "register it first with registry.py add / add-pdf")
        args = argparse.Namespace(**{**vars(args), "evidence_id": None, kind: key})
        return resolve_paper(repo_root, args)
    if args.pdf:
        with _registry.advisory_lock(repo_root, "registry"):
            asset = _registry_add_pdf(registry, args)
        return asset
    if not (args.pmid or args.doi or args.pmcid):
        raise SystemExit("paper.py: one of --pmid/--doi/--pmcid/--pdf/--evidence-id is required")
    existing = registry.lookup(pmid=args.pmid, doi=args.doi, pmcid=args.pmcid)
    if existing is not None and not args.force_refresh_metadata:
        return existing
    raw: dict = {}
    pmid = _corpus.norm_pmid(args.pmid)
    if not pmid and args.doi:
        pmid = _registry._esearch_doi(args.doi)
    if pmid:
        raw = _registry._efetch_record(pmid)
    if args.doi and not raw.get("doi"):
        raw["doi"] = args.doi
    if args.pmcid and not raw.get("pmcid"):
        raw["pmcid"] = args.pmcid
    if not raw.get("title"):
        if existing is not None:
            return existing
        raise SystemExit(
            "paper.py: could not resolve a title for this identifier; register manually with "
            "`registry.py add --title ...` first")
    with _registry.advisory_lock(repo_root, "registry"):
        rec, _is_new = registry.register(raw)
        registry.save()
        registry.generate_pool()
    return rec


def _registry_add_pdf(registry: "_registry.Registry", args) -> dict:
    import hashlib
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"paper.py: --pdf not found: {pdf_path}")
    sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    assets_dir = registry.paths["sources"] / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    dest = assets_dir / f"sha256-{sha256}.pdf"
    if not dest.exists():
        dest.write_bytes(pdf_path.read_bytes())
    raw = {
        "pmid": args.pmid, "doi": args.doi, "pmcid": args.pmcid,
        "title": args.title or pdf_path.stem,
    }
    rec, _is_new = registry.register(raw)
    rec = registry.set_asset(rec["evidence_id"], {
        "sha256": sha256, "path": str(dest.relative_to(registry.repo_root)),
        "bytes": pdf_path.stat().st_size, "added_at": utcnow(),
    })
    registry.save()
    registry.generate_pool()
    return rec


# --------------------------------------------------------------------- extraction/appraisal reuse


def try_reuse(repo_root: Path, run_dir: Path, evidence_id: str, project: str | None) -> dict:
    entry = _registry.Registry(repo_root).lookup(evidence_id=evidence_id)
    result = {"extraction": None, "appraisal": None}
    if entry is None:
        return result
    if entry.get("extraction_path"):
        src = repo_root / entry["extraction_path"]
        if src.exists():
            dest_dir = run_dir / "workspace" / "extractions"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            result["extraction"] = str(dest)
    if project:
        appraisal_rel = (entry.get("appraisals") or {}).get(project)
        if appraisal_rel:
            src = repo_root / appraisal_rel
            if src.exists():
                dest_dir = run_dir / "workspace" / "appraisals"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / src.name
                dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                result["appraisal"] = str(dest)
    return result


def verified_extraction(run_dir: Path, repo_root: Path, evidence_id: str) -> dict | None:
    path = run_dir / "workspace" / "extractions" / f"{_ps.evidence_slug(evidence_id)}.json"
    if not path.exists():
        return None
    store = _store.Store(run_dir, repo_root=repo_root)
    extraction, reason = _registry._verify_extraction_for_promotion(evidence_id, path, store)
    if reason is not None:
        return None
    return extraction


def loaded_appraisal(run_dir: Path, evidence_id: str) -> dict | None:
    path = run_dir / "workspace" / "appraisals" / f"{_ps.evidence_slug(evidence_id)}.json"
    if not path.exists():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return rec if isinstance(rec, dict) else None


def queue_task(run_dir: Path, stage: str, evidence_id: str) -> str:
    kind, key = evidence_id.split(":", 1)
    task_id = f"{stage}:{kind}:{key}"
    board = _taskboard.TaskBoard(run_dir)
    with _corpus.advisory_lock(run_dir, "taskboard"):
        prev = board.get(task_id)
        if prev is None or prev.get("status") == "cancelled":
            board.write(_taskboard.make_task_record(
                task_id=task_id, stage=stage, status="pending", inputs_hash=None,
                attempts=0, worker=None, output_path=None, error=None,
                created_at=utcnow()))
    return task_id


# --------------------------------------------------------------------- summary assembly/render


def load_summary(run_dir: Path, evidence_id: str) -> dict | None:
    path = run_dir / "workspace" / "summaries" / f"{_ps.evidence_slug(evidence_id)}.json"
    if not path.exists():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return rec if isinstance(rec, dict) else None


def render_single_paper(summary: dict, extraction: dict, appraisal: dict | None,
                        registry_rec: dict, run_dir: Path, repo_root: Path,
                        verification_status: str) -> str:
    template_path = repo_root.parent / "templates" / "single-paper-summary.md"
    if not template_path.exists():
        script_dir = Path(__file__).resolve().parent
        template_path = script_dir.parent / "templates" / "single-paper-summary.md"
    template_text = template_path.read_text(encoding="utf-8")
    values = {
        "TITLE": registry_rec.get("title") or summary.get("evidence_id"),
        "EVIDENCE_ID": summary.get("evidence_id"),
        "PROJECT": summary.get("project") or "none",
        "PURPOSE": summary.get("purpose"),
        "AUDIENCE": summary.get("audience"),
        "SOURCE_BASIS": summary.get("source_basis"),
        "GENERATED_AT": summary.get("created_at"),
        "SUMMARY_STATUS": "final" if verification_status == "pass" else "provisional",
        "STATUS_NOTE": "" if verification_status == "pass"
                       else "One or more verification checks did not pass; see outputs/verification.json.",
        "CITATION": _ps.citation_line(registry_rec),
        "BOTTOM_LINE": _ps.render_section_body(summary, "bottom_line"),
        "WHY_SUMMARIZED": _ps.render_section_body(summary, "why_summarized"),
        "STUDY_DESIGN": _ps.render_section_body(summary, "study_design_and_basis"),
        "ABSTRACT_ONLY_NOTE": "Full text was not available for this summary." if
                              summary.get("source_basis") == "abstract_only" else "",
        "POPULATION": _ps.render_section_body(summary, "population_setting_sample"),
        "INTERVENTION": _ps.render_section_body(summary, "intervention_exposure_index_test_model"),
        "COMPARATOR": _ps.render_section_body(summary, "comparator_or_reference_standard"),
        "OUTCOMES": _ps.render_section_body(summary, "outcomes_and_results"),
        "APPRAISAL_SUMMARY": _ps.render_section_body(summary, "methods_quality_and_rob"),
        "LIMITATIONS": _ps.render_bullets(summary.get("limitations") or []),
        "TAKEAWAYS": _ps.render_section_body(summary, "practical_takeaways"),
        "DO_NOT_CONCLUDE": _ps.render_bullets(summary.get("do_not_conclude") or []),
        "EXTRACTION_PATH": summary.get("extraction_path"),
        "APPRAISAL_PATH_OR_SKIP_REASON": summary.get("appraisal_path")
                                          or f"skipped: {summary.get('appraisal_skipped_reason')}",
        "VERIFICATION_STATUS": verification_status,
    }
    return _ps.render_template(template_text, values)


# --------------------------------------------------------------------- pipeline: one paper


def summarize_one(repo_root: Path, args, *, evidence_hint: dict | None = None) -> dict:
    registry_rec = evidence_hint or resolve_paper(repo_root, args)
    evidence_id = registry_rec["evidence_id"]
    purpose = args.purpose or "background"
    audience = args.audience or "researcher"
    appraise_requested = not args.no_appraise

    slug = run_slug_for(evidence_id, purpose)
    run_dir = ensure_run_dir(repo_root, slug, config_extra={
        "evidence_id": evidence_id, "purpose": purpose, "audience": audience,
        "project": args.project, "appraise_requested": appraise_requested,
    })
    write_corpus_record(run_dir, registry_rec)

    if args.reuse and not args.force:
        try_reuse(repo_root, run_dir, evidence_id, args.project)

    extraction = None if args.force else verified_extraction(run_dir, repo_root, evidence_id)
    if extraction is None:
        if not args.offline:
            _acquire_source(run_dir, repo_root)
        corpus = _corpus.Corpus(run_dir).load()
        corpus_rec = corpus.records.get(evidence_id) or {}
        fulltext_status = (corpus_rec.get("fulltext") or {}).get("status", "missing")
        if fulltext_status == "missing":
            task_id = queue_task(run_dir, "retrieve", evidence_id)
            return _status_payload(run_dir, evidence_id, "pending_retrieval", task_id=task_id,
                                   detail="no full text or abstract available yet")
        task_id = queue_task(run_dir, "extract", evidence_id)
        return _status_payload(
            run_dir, evidence_id, "pending_extraction", task_id=task_id,
            detail="dispatch a Stage-5 extraction subagent (references/prompts/extract.md), "
                   f"output workspace/extractions/{_ps.evidence_slug(evidence_id)}.json")

    source_basis = extraction.get("evidence_basis", "fulltext")
    appraisal = loaded_appraisal(run_dir, evidence_id)
    appraisal_path = None
    appraisal_skipped_reason = None
    if source_basis == "abstract_only":
        appraisal_skipped_reason = "abstract_only"
    elif not appraise_requested:
        appraisal_skipped_reason = "no_appraise_flag"
    elif appraisal is not None:
        appraisal_path = f"workspace/appraisals/{_ps.evidence_slug(evidence_id)}.json"
        if appraisal.get("tool") == "none":
            appraisal_skipped_reason = None  # still a completed, on-purpose "none" appraisal
    else:
        task_id = queue_task(run_dir, "appraise", evidence_id)
        return _status_payload(
            run_dir, evidence_id, "pending_appraisal", task_id=task_id,
            detail="dispatch a Stage-6 appraisal subagent (references/prompts/appraise.md), "
                   f"output workspace/appraisals/{_ps.evidence_slug(evidence_id)}.json")

    summary = load_summary(run_dir, evidence_id)
    if summary is None:
        return _status_payload(
            run_dir, evidence_id, "pending_summary", detail=(
                "dispatch a summarize-paper subagent (references/prompts/summarize-paper.md) "
                f"with extraction_path=workspace/extractions/{_ps.evidence_slug(evidence_id)}.json"
                + (f", appraisal_path=workspace/appraisals/{_ps.evidence_slug(evidence_id)}.json"
                   if appraisal_path else f", appraisal_skipped_reason={appraisal_skipped_reason}")
                + f"; output workspace/summaries/{_ps.evidence_slug(evidence_id)}.json"))

    errors = _ps.validate_summary_shape(summary)
    for sec in summary.get("sections") or []:
        for claim in sec.get("claims") or []:
            for ref in claim.get("span_refs") or []:
                reason = _ps.resolve_span_ref(ref, extraction, appraisal)
                if reason:
                    errors.append(f"{sec.get('name')}: {reason}")
    verification_status = "pass" if not errors else "fail"

    out_md = render_single_paper(summary, extraction, appraisal, registry_rec, run_dir,
                                 repo_root, verification_status)
    md_path = run_dir / "outputs" / "single-paper-summary.md"
    md_path.write_text(out_md, encoding="utf-8")
    html_path = None
    if args.format in ("html", "both"):
        html_path = run_dir / "outputs" / "single-paper-summary.html"
        html_path.write_text(_wrap_html(registry_rec.get("title") or evidence_id, out_md),
                             encoding="utf-8")

    verification = {
        "schema_version": SCHEMA_VERSION, "evidence_id": evidence_id,
        "status": verification_status, "errors": errors, "checked_at": utcnow(),
    }
    (run_dir / "outputs" / "verification.json").write_text(
        json.dumps(verification, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.project:
        _copy_project_export(repo_root, args.project, "paper-summaries", evidence_id,
                             md_path, html_path)

    if not args.force:
        _promote(repo_root, run_dir, evidence_id, args.project)

    status = "completed" if verification_status == "pass" else "verification_failed"
    return _status_payload(run_dir, evidence_id, status, detail=", ".join(errors) or "ok",
                           summary_path=str(run_dir / "workspace" / "summaries"
                                            / f"{_ps.evidence_slug(evidence_id)}.json"),
                           output_path=str(md_path))


def _status_payload(run_dir: Path, evidence_id: str, status: str, *, task_id: str | None = None,
                    detail: str = "", summary_path: str | None = None,
                    output_path: str | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION, "status": status, "evidence_id": evidence_id,
        "run_dir": str(run_dir), "task_id": task_id, "detail": detail,
        "summary_path": summary_path, "output_path": output_path,
    }


def _call_module(module_name: str, argv: list[str]) -> dict | None:
    """Call `<module>.main(argv)` in-process and parse its printed JSON payload (every
    scripts/*.py CLI here prints exactly one JSON object via its own `emit`)."""
    module = sys.modules.get(module_name) or __import__(module_name)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        module.main(argv)
    try:
        return json.loads(buf.getvalue())
    except (json.JSONDecodeError, ValueError):
        return None


def _acquire_source(run_dir: Path, repo_root: Path) -> None:
    corpus_path = run_dir / "corpus.jsonl"
    _call_module("fulltext", ["acquire", "--run-dir", str(run_dir), "--corpus", str(corpus_path)])


def _promote(repo_root: Path, run_dir: Path, evidence_id: str, project: str | None) -> None:
    corpus = _corpus.Corpus(run_dir).load()
    rec = corpus.records.get(evidence_id)
    if rec is None:
        return
    slug = _ps.evidence_slug(evidence_id)
    extraction_src = run_dir / "workspace" / "extractions" / f"{slug}.json"
    if extraction_src.exists() and not rec.get("extraction_path"):
        rec["extraction_path"] = str(Path("workspace") / "extractions" / f"{slug}.json")
        with _corpus.advisory_lock(run_dir, "corpus"):
            corpus.save()
        registry = _registry.Registry(repo_root)
        store = _store.Store(run_dir, repo_root=repo_root)
        extraction, reason = _registry._verify_extraction_for_promotion(
            evidence_id, extraction_src, store)
        if reason is None:
            dest = registry.paths["extractions"] / f"{slug}.json"
            with _registry.advisory_lock(repo_root, "registry"):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(extraction, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
                registry.set_extraction(evidence_id, str(dest.relative_to(registry.repo_root)))
                registry.save()
                registry.generate_pool()
    if project:
        appraisal_src = run_dir / "workspace" / "appraisals" / f"{slug}.json"
        if appraisal_src.exists():
            registry = _registry.Registry(repo_root)
            dest = registry.paths["appraisals"] / project / f"{slug}.json"
            with _registry.advisory_lock(repo_root, "registry"):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(appraisal_src.read_text(encoding="utf-8"), encoding="utf-8")
                registry.set_appraisal(evidence_id, project,
                                       str(dest.relative_to(registry.repo_root)))
                registry.save()
                registry.generate_pool()


def _copy_project_export(repo_root: Path, project: str, subdir: str, evidence_id: str,
                         md_path: Path, html_path: Path | None) -> None:
    dest_dir = repo_root / "projects" / project / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    slug = _ps.evidence_slug(evidence_id)
    (dest_dir / f"{slug}.md").write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    if html_path is not None:
        (dest_dir / f"{slug}.html").write_text(html_path.read_text(encoding="utf-8"),
                                                encoding="utf-8")


def _wrap_html(title: str, markdown_text: str) -> str:
    import html as _html
    body = _html.escape(markdown_text)
    return (
        f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{_html.escape(title)}"
        f"</title></head><body><pre style=\"white-space:pre-wrap;font-family:system-ui,"
        f"sans-serif\">{body}</pre></body></html>\n"
    )


# --------------------------------------------------------------------- commands


def cmd_summarize(args) -> int:
    repo_root = require_repo(args.repo)
    payload = summarize_one(repo_root, args)
    emit(payload)
    return 0 if payload["status"] in ("completed",) else 1


def _normalize_bare_identifier(ident: str) -> str:
    """A line from `--ids-file` may already be `pmid:`/`doi:`/`pmcid:`-prefixed
    (an `evidence_id`), or bare — normalize the common bare forms only; anything else is
    passed through and resolved as a DOI (`resolve_paper`'s fallback)."""
    ident = ident.strip()
    if ident.split(":", 1)[0] in ("pmid", "doi", "pmcid"):
        return ident
    if ident.isdigit():
        return f"pmid:{ident}"
    if ident.upper().startswith("PMC"):
        return f"pmcid:{ident.upper()}"
    return f"doi:{ident}"


def _iter_ids_file(path: Path) -> list[str]:
    return [_normalize_bare_identifier(line) for line in path.read_text(encoding="utf-8").splitlines()
           if line.strip() and not line.strip().startswith("#")]


def _discover_candidates(query: str, limit: int) -> list[str]:
    import eutils as _eutils
    result = _eutils.esearch(query=query, retmax=limit)
    return [f"pmid:{p}" for p in (result.get("retrieved_pmids") or [])]


def cmd_summarize_set(args) -> int:
    repo_root = require_repo(args.repo)
    identifiers: list[str] = []
    selection_mode = "explicit"
    selection_basis: dict = {
        "topic": None, "question": None, "ids_file": None, "database": None,
        "retrieved_at": None, "limit": None,
    }

    for pmid in args.pmid or []:
        identifiers.append(f"pmid:{_corpus.norm_pmid(pmid)}")
    for doi in args.doi or []:
        identifiers.append(f"doi:{_corpus.norm_doi(doi)}")
    for pmcid in args.pmcid or []:
        identifiers.append(f"pmcid:{_corpus.norm_pmcid(pmcid)}")
    if args.ids_file:
        selection_basis["ids_file"] = args.ids_file
        for line in _iter_ids_file(Path(args.ids_file)):
            identifiers.append(line)
    if args.evidence_id:
        identifiers.extend(args.evidence_id)
    if args.bib:
        selection_basis["ids_file"] = args.bib
        result = _call_module("registry", ["import-bib", "--repo", str(repo_root),
                                           "--file", args.bib])
        identifiers.extend(r["evidence_id"] for r in (result or {}).get("records", [])
                           if r.get("status") == "ok")
    if args.folder:
        selection_basis["ids_file"] = args.folder
        folder_args = ["import-folder", "--repo", str(repo_root), "--dir", args.folder]
        if args.recursive:
            folder_args.append("--recursive")
        result = _call_module("registry", folder_args)
        identifiers.extend(r["evidence_id"] for r in (result or {}).get("records", []))

    if args.question or args.topic:
        selection_mode = "question" if args.question else "topic"
        limit = min(args.limit or DEFAULT_DISCOVERY_LIMIT, HARD_DISCOVERY_LIMIT)
        query = args.question or args.topic
        selection_basis.update({
            "topic": args.topic, "question": args.question, "database": "pubmed",
            "retrieved_at": utcnow(), "limit": limit,
        })
        identifiers = _discover_candidates(query, limit) if not args.offline else []

    if not identifiers:
        raise SystemExit("paper.py: summarize-set needs at least one identifier or "
                         "--question/--topic")

    label = args.topic or args.question or "explicit-set"
    set_id = f"paper-set:{selection_mode}:{slugify(label, maxlen=60)}:{utcnow()[:10]}"

    if args.select_only:
        emit({"schema_version": SCHEMA_VERSION, "status": "selected", "set_id": set_id,
             "selection_mode": selection_mode, "candidates": identifiers})
        return 0

    included, summary_paths, failed = [], [], []
    for ident in identifiers:
        ns = argparse.Namespace(**vars(args))
        ns.evidence_id = ns.pdf = None
        ns.pmid = ns.doi = ns.pmcid = None
        if ident.startswith("pmid:"):
            ns.pmid = ident.split(":", 1)[1]
        elif ident.startswith("doi:"):
            ns.doi = ident.split(":", 1)[1]
        elif ident.startswith("pmcid:"):
            ns.pmcid = ident.split(":", 1)[1]
        else:
            ns.evidence_id = ident
        ns.force_refresh_metadata = False
        try:
            payload = summarize_one(repo_root, ns)
        except SystemExit as exc:
            failed.append({"identifier": ident, "stage": "register", "reason": str(exc)})
            continue
        if payload["status"] == "completed":
            included.append(payload["evidence_id"])
            summary_paths.append(payload["summary_path"])
        else:
            stage = {"pending_retrieval": "retrieve", "pending_extraction": "extract",
                     "pending_appraisal": "appraise", "pending_summary": "assemble",
                     "verification_failed": "verify"}.get(payload["status"], "assemble")
            failed.append({"identifier": ident, "stage": stage, "reason": payload["detail"]})
            if args.strict:
                break

    overview = {"enabled": bool(args.overview) and len(included) >= 2, "claims": []}

    set_run_dir = repo_root / "runs" / slugify(f"paper-set-{label}", maxlen=120)
    set_run_dir.mkdir(parents=True, exist_ok=True)
    set_record = {
        "schema_version": SCHEMA_VERSION, "set_id": set_id, "project": args.project,
        "selection_mode": selection_mode, "selection_basis": selection_basis,
        "included_evidence_ids": included, "failed_identifiers": failed,
        "excluded_candidates": [], "summary_paths": summary_paths,
        "overview": overview, "created_at": utcnow(),
    }
    (set_run_dir / "summary-set.json").write_text(
        json.dumps(set_record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.project and included:
        dest_dir = repo_root / "projects" / args.project / "paper-summary-sets"
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / f"{slugify(label, maxlen=60)}.json").write_text(
            json.dumps(set_record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    status = "completed" if included and (not failed or not args.strict) else "partial"
    emit({"schema_version": SCHEMA_VERSION, "status": status, "set_id": set_id,
         "run_dir": str(set_run_dir), "included": len(included), "failed": len(failed),
         "record_path": str(set_run_dir / "summary-set.json")})
    return 0 if status == "completed" else 1


# --------------------------------------------------------------------- CLI wiring


def add_identifier_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pmid")
    p.add_argument("--doi")
    p.add_argument("--pmcid")
    p.add_argument("--pdf")
    p.add_argument("--evidence-id", dest="evidence_id")
    p.add_argument("--title", help="fallback title (used with --pdf/--doi when metadata "
                                   "cannot be resolved)")


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", required=True)
    p.add_argument("--project")
    p.add_argument("--purpose", choices=_ps.PURPOSES, default="background")
    p.add_argument("--audience", choices=_ps.AUDIENCES, default="researcher")
    p.add_argument("--appraise", dest="no_appraise", action="store_false", default=None,
                   help="explicit default: run/reuse an appraisal (on unless --no-appraise)")
    p.add_argument("--no-appraise", dest="no_appraise", action="store_true")
    p.add_argument("--reuse", action="store_true", default=True)
    p.add_argument("--no-reuse", dest="reuse", action="store_false")
    p.add_argument("--force", action="store_true",
                   help="regenerate extraction/summary even if reusable artifacts exist")
    p.add_argument("--format", choices=("md", "html", "both"), default="md")
    p.add_argument("--out")
    p.add_argument("--offline", action="store_true",
                   help="skip network acquisition/discovery (local rungs / reuse only)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paper.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("summarize", help="summarize exactly one paper")
    add_identifier_args(s)
    add_common_args(s)
    s.set_defaults(func=cmd_summarize, force_refresh_metadata=False)

    s = sub.add_parser("summarize-set", help="summarize a user-specified or discovered set")
    s.add_argument("--pmid", action="append")
    s.add_argument("--doi", action="append")
    s.add_argument("--pmcid", action="append")
    s.add_argument("--evidence-id", dest="evidence_id", action="append")
    s.add_argument("--pdf")
    s.add_argument("--title")
    s.add_argument("--ids-file", dest="ids_file")
    s.add_argument("--bib")
    s.add_argument("--folder")
    s.add_argument("--recursive", action="store_true")
    s.add_argument("--question")
    s.add_argument("--topic")
    add_common_args(s)
    s.add_argument("--limit", type=int)
    s.add_argument("--select-only", action="store_true")
    s.add_argument("--overview", dest="overview", action="store_true", default=False)
    s.add_argument("--no-overview", dest="overview", action="store_false")
    s.add_argument("--strict", action="store_true",
                   help="a single failed identifier fails the whole set (default: best-effort)")
    s.set_defaults(func=cmd_summarize_set)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("paper.py: interrupted", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - a crash must be exit 2, not a traceback-only exit 1
        print(f"paper.py: fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
