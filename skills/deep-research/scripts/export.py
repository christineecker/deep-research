#!/usr/bin/env python3
"""export.py — `/deep-research:export readcube`: publish a ReadCube-importable bundle.

Implements Phase 2.2 (export core + fixture validation) and the CLI-facing part of
2.4 from `REFERENCE_MANAGER_V2_PLAN.md`'s "New Phase 2 — ReadCube export command"
section. Selection and asset resolution are delegated to `export_select.py`
(`select_all`/`select_by_evidence_ids`/`select_by_run_dir`, `LegacyRegistryAssetSource`);
RIS serialization is delegated to `ris.py` (`build_ris_record`/`render_ris_bundle`);
citation keys reuse `render.py`'s `bib_key` (schema.md R6), and `--bib` reuses
`render.py`'s `build_entry` directly (no BibTeX logic is duplicated here).

Implements Phase 2.3's incremental-export ledger: `--incremental` diffs the current
selection against `export_ledger.py`'s per-destination ledger and publishes only
`changed_or_new` records (unchanged ones are omitted from the batch and reported in
`unchanged_count`); an all-unchanged selection is a structured no-op (`status: "no_op"`,
no batch directory created). See `export_ledger.py`'s module docstring for the locking
design.

Implements Phase 2.6: `--source legacy|refmgr` selects between `LegacyRegistryAssetSource`
(default, reads `--repo`'s registry.jsonl) and `export_select_refmgr.py`'s
`RefmgrExportSource` (reads a SQLite refmgr library, `--library <path>`). `--source
refmgr` supports `--all` and `--evidence-id` (bare paper id, or "scheme:value" identifier
lookup via `_resolve_refmgr_evidence_id`) selection; `--run-dir` is deliberately rejected
for it (see `_select_records_refmgr`'s docstring for why). Every attachment a refmgr
paper has is exported, not just the preferred one: `_plan_export` calls
`resolve_all(paper_id)` when the asset source supports it, routing the primary/unknown
attachment into `PDFs/` and every other role into `Supplements/` (created lazily), with
each record's manifest `attachments[]` listing every association.

Bundle contract (`<destination>/<name>-<unique-id>/`):
    references.ris
    references.bib          # only with --bib
    PDFs/<safe-label>--<hash-prefix>.pdf
    Supplements/            # present only if a future AssetSource populates it
    manifest.json
    import-report.md
    README.md
    COMPLETE.json            # written last, after everything else is verified written

Exit codes (mirrors render.py's convention)
  0 ok (published export, dry run, or no-op)
  2 user error (bad CLI usage, unresolved selection)
  3 state error (a selected reference fails --require-pdfs, or publication could
    not be completed — e.g. a copy's hash did not match its source)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_select as _select  # noqa: E402  (sibling module, stdlib-only)
import export_select_refmgr as _select_refmgr  # noqa: E402  (Phase 2.6: RefmgrAssetSource)
import export_ledger as _ledger  # noqa: E402  (sibling module: ExportLedger, Phase 2.3)
import ris as _ris  # noqa: E402  (sibling module, stdlib-only)
import render as _render  # noqa: E402  (sibling module: bib_key, build_entry)

# Key `paper_to_export_record` embeds so a refmgr-sourced record can be traced back to
# its paper id for `resolve_all()` — see export_select_refmgr.py's module docstring.
_PAPER_ID_KEY = "_refmgr_paper_id"

SCHEMA_VERSION = 1
TOOL = "export.py/0.1"


class PublicationError(Exception):
    """A failure while staging/copying/writing a bundle. Always a state error (exit 3);
    the caller is responsible for cleaning up any partial staging directory."""


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------- metadata

def export_metadata(record: dict, citation_key: str) -> dict:
    """The exported metadata fields for one record — the *only* thing the metadata
    hash covers, never raw registry internals (plan: "hash the exported metadata
    fields, not raw registry internals")."""
    rec = _ris.build_ris_record(record, citation_key=citation_key)
    return asdict(rec)


def metadata_hash(metadata: dict) -> str:
    """Deterministic sha256 over a canonical (sorted-key, no-whitespace) JSON
    serialization of the exported metadata — stable and reusable for Phase 2.3's
    incremental fingerprinting."""
    canonical = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_FULLTEXT_STATUS_VALUES = ("fulltext", "abstract_only", "missing")  # corpus.py FULLTEXT_STATUS


def fulltext_status_label(record: dict, resolution: "_select.AssetResolution") -> str:
    """A per-record fulltext/abstract-only/missing status for the import report and
    manifest (plan Phase 5: "display abstract-only/full-text status clearly"). Legacy
    registry records carry `fulltext.status` (corpus.py `FULLTEXT_STATUS`) directly;
    refmgr has no abstract-only concept (attachments are pass/fail only), so a refmgr
    record falls back to whether an attachment actually resolved."""
    fulltext = record.get("fulltext")
    if isinstance(fulltext, dict) and fulltext.get("status") in _FULLTEXT_STATUS_VALUES:
        return fulltext["status"]
    return "fulltext" if resolution.available else "missing"


def completeness_warnings(record: dict, resolution: "_select.AssetResolution") -> list[str]:
    warnings: list[str] = []
    if not (record.get("doi") or record.get("pmid") or record.get("pmcid")):
        warnings.append("no external identifier (DOI/PMID/PMCID)")
    if not (record.get("authors_structured") or record.get("authors")):
        warnings.append("no authors on file")
    if not record.get("publication_date"):
        warnings.append("no publication date on file")
    if not record.get("title"):
        warnings.append("no title on file")
    if not resolution.available:
        warnings.append(f"primary PDF unavailable: {resolution.problem}")
    return warnings


# ---------------------------------------------------------------- filename safety

_UNSAFE = re.compile(r"[^A-Za-z0-9 _-]+")
_WHITESPACE = re.compile(r"\s+")


def safe_label(text: str | None, *, max_len: int = 80) -> str:
    """Sanitize a title (preferred) or citation key to a filesystem-safe label:
    strip unsafe characters, collapse whitespace to single spaces->hyphens, bound
    length. Never identity-bearing — see the manifest for that."""
    text = (text or "").strip()
    text = _UNSAFE.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    text = text.replace(" ", "-")
    text = text.strip("-")
    if not text:
        text = "untitled"
    return text[:max_len].rstrip("-") or "untitled"


def unique_bundle_filename(label: str, sha256_hex: str, extension: str,
                            used: dict[str, str]) -> str:
    """`<safe-label>--<hash-prefix>.<extension>`, extending the hash-prefix length
    until the (label, prefix) pair is unique within this batch (plan: "extend hash
    prefixes if collisions occur"). `used` maps an already-claimed filename to the
    full sha256 that claimed it, and is mutated in place."""
    prefix_len = 8
    while True:
        candidate = f"{label}--{sha256_hex[:prefix_len]}.{extension}"
        owner = used.get(candidate)
        if owner is None or owner == sha256_hex:
            used[candidate] = sha256_hex
            return candidate
        if prefix_len >= len(sha256_hex):
            # Exhausted the full hash and still colliding: truly impossible (two
            # distinct files can't share a full sha256) but never loop forever.
            candidate = f"{label}--{sha256_hex}-{uuid.uuid4().hex[:6]}.{extension}"
            used[candidate] = sha256_hex
            return candidate
        prefix_len += 1


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------- selection

def _resolve_refmgr_evidence_id(service, raw_id: str) -> str | None:
    """A refmgr "--evidence-id" is either a bare paper id (service.papers.get) or a
    "scheme:value" identifier (e.g. "doi:10.1000/x") resolved via find_paper_by_identifier.
    Returns the paper id, or None if unresolved."""
    paper = service.papers.get(raw_id, include_deleted=False)
    if paper:
        return raw_id
    if ":" in raw_id:
        scheme, _, value = raw_id.partition(":")
        return service.identifiers.find_paper_by_identifier(scheme, value)
    return None


def _select_records_refmgr(library_root: Path, args) -> tuple[list[dict], dict]:
    """Phase 2.6: refmgr-backed selection. `--all` and `--evidence-id` (bare paper id,
    or "scheme:value" identifier lookup). `--run-dir` is not supported against refmgr:
    a run's corpus.jsonl evidence_ids use the legacy `scheme:value` scheme keyed to the
    *legacy registry*, and there is no defined operation yet for cross-resolving those
    against refmgr identifiers/papers -- raising here is deliberate, not an oversight."""
    service = _select_refmgr.ReferenceManagerService(library_root)
    try:
        if args.all:
            paper_ids = _select_refmgr.RefmgrExportSource(library_root).list_papers_for_export()
            description = {"source": "all"}
        elif args.evidence_id:
            paper_ids = []
            unresolved = []
            for raw_id in args.evidence_id:
                pid = _resolve_refmgr_evidence_id(service, raw_id)
                if pid is None:
                    unresolved.append(raw_id)
                else:
                    paper_ids.append(pid)
            if unresolved:
                raise _select.ExportError(
                    "unresolved --evidence-id value(s) against refmgr library "
                    f"{library_root}: {', '.join(unresolved)}"
                )
            description = {"source": "evidence_id", "evidence_ids": list(args.evidence_id)}
        else:
            raise _select.ExportError(
                "--run-dir is not supported with --source refmgr: a run's evidence_ids "
                "use the legacy registry's scheme, with no defined resolution against "
                "refmgr identifiers yet -- use --source legacy for --run-dir selection"
            )
        records = [_select_refmgr.paper_to_export_record(service, pid) for pid in paper_ids]
    finally:
        service.close()
    records = sorted(records, key=lambda r: r["evidence_id"])
    return records, description


def _select_records(repo_root: Path, args) -> tuple[list[dict], dict]:
    if getattr(args, "source", "legacy") == "refmgr":
        return _select_records_refmgr(Path(args.library).expanduser().resolve(), args)
    if args.all:
        records = _select.select_all(repo_root)
        description = {"source": "all"}
    elif args.evidence_id:
        records = _select.select_by_evidence_ids(repo_root, args.evidence_id)
        description = {"source": "evidence_id", "evidence_ids": list(args.evidence_id)}
    else:
        records = _select.select_by_run_dir(repo_root, args.run_dir)
        description = {"source": "run_dir", "run_dir": str(args.run_dir)}
    records = sorted(records, key=lambda r: r["evidence_id"])
    return records, description


# ------------------------------------------------------------------------ plan

def _plan_export(repo_root: Path, args) -> dict:
    """Selection + resolution + manifest-shaped bookkeeping shared verbatim by
    dry-run and real publication (plan 2.2: "dry-run: run the exact same selection
    + resolution + manifest-building logic")."""
    records, selection_description = _select_records(repo_root, args)

    if getattr(args, "source", "legacy") == "refmgr":
        library_root = Path(args.library).expanduser().resolve()
        asset_source = _select_refmgr.RefmgrExportSource(library_root)
    else:
        asset_source = _select.LegacyRegistryAssetSource(repo_root)
    plans: list[dict] = []
    warnings: list[str] = []
    blocking_errors: list[dict] = []

    try:
        for record in records:
            eid = record["evidence_id"]
            # refmgr papers can have multiple attachments (roles/versions); resolve_all
            # returns every non-deleted one, preferred/primary first. Legacy records have
            # at most one asset pointer, so extra_resolutions is always [] for them.
            resolve_all = getattr(asset_source, "resolve_all", None)
            if callable(resolve_all) and _PAPER_ID_KEY in record:
                all_resolutions = resolve_all(record[_PAPER_ID_KEY])
                resolution = all_resolutions[0] if all_resolutions else asset_source.resolve(record)
                extra_resolutions = all_resolutions[1:]
            else:
                resolution = asset_source.resolve(record)
                extra_resolutions = []
            key = _render.bib_key(eid)
            metadata = export_metadata(record, key)
            md_hash = metadata_hash(metadata)
            rec_warnings = completeness_warnings(record, resolution)
            for w in rec_warnings:
                warnings.append(f"{eid}: {w}")
            if not resolution.available and args.require_pdfs:
                blocking_errors.append({"evidence_id": eid, "reason": resolution.problem})
            # Fingerprint the ordered (role, sha256) pairs of every resolved asset for this
            # record (primary + supplements/versions; [] for a metadata-only record) --
            # combined with metadata_hash, this is what Phase 2.3's incremental diff
            # compares against the ledger's last published baseline for this destination
            # (export_ledger.py `_diff_against`).
            all_avail = [resolution] + [r for r in extra_resolutions if r.available]
            assets = [(r.role, r.sha256_expected) for r in all_avail if r.available]
            attachment_fp = _ledger.attachment_fingerprint(assets)
            plans.append({
                "record": record,
                "evidence_id": eid,
                "citation_key": key,
                "metadata": metadata,
                "metadata_hash": md_hash,
                "attachment_fingerprint": attachment_fp,
                "warnings": rec_warnings,
                "resolution": resolution,
                "extra_resolutions": extra_resolutions,
            })
    finally:
        close = getattr(asset_source, "close", None)
        if callable(close):
            close()

    return {
        "records": plans,
        "selection_description": selection_description,
        "warnings": warnings,
        "blocking_errors": blocking_errors,
    }


def _dedupe_attachments(plans: list[dict]) -> dict[str, dict]:
    """Group available assets by full sha256 so a file shared by multiple records is
    copied once but keeps every association (plan bundle contract). Covers each
    record's primary resolution plus any extra_resolutions (refmgr supplements/versions)."""
    by_sha: dict[str, dict] = {}
    for entry in plans:
        resolutions = [entry["resolution"]] + entry.get("extra_resolutions", [])
        for resolution in resolutions:
            if not resolution.available:
                continue
            sha = resolution.sha256_expected or _sha256_file(resolution.local_path)
            bucket = by_sha.setdefault(sha, {
                "sha256": sha,
                "source_path": resolution.local_path,
                "role": resolution.role,
                "label_source": entry["record"].get("title") or entry["citation_key"],
                "associations": [],
            })
            bucket["associations"].append({"evidence_id": entry["evidence_id"], "role": resolution.role})
    return by_sha


def _counts(plans: list[dict]) -> dict:
    primary_available = sum(1 for e in plans if e["resolution"].available)
    supplement_available = sum(
        1 for e in plans for r in e.get("extra_resolutions", []) if r.available
    )
    return {
        "selected": len(plans),
        "primary_pdf_count": primary_available,
        "metadata_only_count": len(plans) - primary_available,
        "supplement_count": supplement_available,
    }


def build_manifest(*, batch_id: str, created_at: str, selection_description: dict,
                    destination: Path, plans: list[dict], attachments: dict[str, dict],
                    attachment_paths: dict[str, str]) -> dict:
    counts = _counts(plans)
    records_out = []
    for entry in plans:
        resolution = entry["resolution"]
        assoc = []
        for r in [resolution] + entry.get("extra_resolutions", []):
            if not r.available:
                continue
            sha = r.sha256_expected or _sha256_file(r.local_path)
            path = attachment_paths.get(sha)
            if path:
                assoc.append({"path": path, "role": r.role})
        records_out.append({
            "evidence_id": entry["evidence_id"],
            "doi": entry["record"].get("doi"),
            "pmid": entry["record"].get("pmid"),
            "pmcid": entry["record"].get("pmcid"),
            "type": entry["metadata"].get("ris_type"),
            "citation_key": entry["citation_key"],
            "metadata_hash": entry["metadata_hash"],
            "warnings": entry["warnings"],
            "primary_pdf_available": resolution.available,
            "fulltext_status": fulltext_status_label(entry["record"], resolution),
            "attachments": assoc,
        })

    attachments_out = []
    for sha, bucket in attachments.items():
        attachments_out.append({
            "path": attachment_paths[sha],
            "role": bucket["role"],
            "sha256": sha,
            "bytes": bucket.get("bytes"),
            "page_count": None,
            "provenance": {"source_path": str(bucket["source_path"])},
            "associations": bucket["associations"],
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "created_at": created_at,
        "exporter_version": TOOL,
        "selection": selection_description,
        "destination": str(destination),
        "counts": {
            "records": counts["selected"],
            "attachments": len(attachments_out),
            "primary_pdf": counts["primary_pdf_count"],
            "metadata_only": counts["metadata_only_count"],
            "supplements": counts["supplement_count"],
        },
        "records": records_out,
        "attachments": attachments_out,
    }


def render_import_report(manifest: dict, warnings: list[str], blocking_errors: list[dict]) -> str:
    lines = [
        f"# Import report — batch `{manifest['batch_id']}`",
        "",
        f"- Records: {manifest['counts']['records']}",
        f"- Primary PDFs: {manifest['counts']['primary_pdf']}",
        f"- Metadata-only references: {manifest['counts']['metadata_only']}",
        f"- Supplements: {manifest['counts']['supplements']}",
        f"- Attachment files: {manifest['counts']['attachments']}",
        "",
        "## Records",
        "",
        "| Evidence id | Citation key | Fulltext status |",
        "|---|---|---|",
    ]
    for rec in manifest["records"]:
        lines.append(f"| {rec['evidence_id']} | {rec['citation_key']} | "
                     f"{rec['fulltext_status']} |")
    lines.append("")
    if blocking_errors:
        lines.append("## Blocking errors")
        lines.append("")
        for err in blocking_errors:
            lines.append(f"- {err['evidence_id']}: {err['reason']}")
        lines.append("")
    lines.append("## Warnings")
    lines.append("")
    if warnings:
        for w in warnings:
            lines.append(f"- {w}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


README_TEXT = """\
# ReadCube import

This bundle was produced by `deep-research export.py readcube`. To import it:

1. Import `references.ris` first.
2. Import the primary PDFs next, from `PDFs/`.
3. Review matching between the imported PDFs and their bibliographic records.
4. Attach any files in `Supplements/`, or any ambiguous files, to the intended
   reference by hand.
5. Inspect `import-report.md` for warnings and anything that could not be fully
   exported.

`COMPLETE.json` confirms this bundle was fully and correctly written to local
disk. It does not confirm iCloud synchronization or a successful ReadCube import.
"""


# --------------------------------------------------------------------- publish

def _publish(*, repo_root: Path, destination: Path, name: str, plan: dict, args) -> dict:
    plans = plan["records"]
    batch_name = f"{name}-{uuid.uuid4().hex[:8]}"
    staging = destination / f".staging-{uuid.uuid4().hex}"
    final_dir = destination / batch_name

    try:
        destination.mkdir(parents=True, exist_ok=True)
        pdf_dir = staging / "PDFs"
        pdf_dir.mkdir(parents=True)

        attachments = _dedupe_attachments(plans)
        attachment_paths: dict[str, str] = {}
        used_filenames: dict[str, str] = {}
        total_bytes = 0
        supplements_dir = None

        for sha, bucket in attachments.items():
            # Primary/unknown-role assets go in PDFs/ (matches the single-asset legacy
            # source's existing layout); anything else (refmgr supplement/version roles)
            # goes in Supplements/, created lazily so it's absent when unused.
            if bucket["role"] in ("primary", "unknown"):
                bundle_subdir, subdir_name = pdf_dir, "PDFs"
            else:
                if supplements_dir is None:
                    supplements_dir = staging / "Supplements"
                    supplements_dir.mkdir(parents=True)
                bundle_subdir, subdir_name = supplements_dir, "Supplements"
            label = safe_label(bucket["label_source"])
            filename = unique_bundle_filename(label, sha, "pdf", used_filenames)
            dest_path = bundle_subdir / filename
            shutil.copyfile(bucket["source_path"], dest_path)
            copy_hash = _sha256_file(dest_path)
            if copy_hash != sha:
                raise PublicationError(
                    f"hash mismatch after copying {bucket['source_path']} "
                    f"(expected {sha}, got {copy_hash})"
                )
            size = dest_path.stat().st_size
            bucket["bytes"] = size
            total_bytes += size
            attachment_paths[sha] = f"{subdir_name}/{filename}"

        created_at = now_iso()
        manifest = build_manifest(
            batch_id=batch_name, created_at=created_at,
            selection_description=plan["selection_description"], destination=destination,
            plans=plans, attachments=attachments, attachment_paths=attachment_paths,
        )

        ris_records = [
            _ris.build_ris_record(e["record"], citation_key=e["citation_key"]) for e in plans
        ]
        (staging / "references.ris").write_text(
            _ris.render_ris_bundle(ris_records) + "\n", encoding="utf-8"
        )

        if args.bib:
            entries = [_render.build_entry(e["record"]) for e in plans]
            (staging / "references.bib").write_text("\n".join(entries), encoding="utf-8")

        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / "import-report.md").write_text(
            render_import_report(manifest, plan["warnings"], plan["blocking_errors"]),
            encoding="utf-8",
        )
        (staging / "README.md").write_text(README_TEXT, encoding="utf-8")

        # COMPLETE.json last, after everything else is verified written.
        for required in ("references.ris", "manifest.json", "import-report.md", "README.md"):
            if not (staging / required).exists():
                raise PublicationError(f"{required} was not written before COMPLETE.json")
        (staging / "COMPLETE.json").write_text(
            json.dumps({
                "schema_version": SCHEMA_VERSION,
                "batch_id": batch_name,
                "completed_at": now_iso(),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        os.replace(staging, final_dir)
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, PublicationError):
            raise
        raise PublicationError(str(exc)) from exc

    return {
        "batch_id": batch_name,
        "bundle_path": str(final_dir),
        "manifest": manifest,
        "total_bytes": total_bytes,
        "attachment_count": len(attachments),
    }


# ----------------------------------------------------------------------- CLI

def _result_shell(*, status: str, dry_run: bool) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "command": "readcube",
        "dry_run": dry_run,
        "batch_id": None,
        "bundle_path": None,
        "selection": None,
        "exported_count": 0,
        "unchanged_count": 0,
        "metadata_only_count": 0,
        "primary_pdf_count": 0,
        "supplement_count": 0,
        "total_bytes": 0,
        "warnings": [],
        "blocking_errors": [],
        "local_publication_state": "not_published",
        "ledger_state": "not_tracked",
        "readcube_import_status": "unknown",
    }


def _ledger_candidates(plans: list[dict]) -> list[dict]:
    return [
        {
            "evidence_id": e["evidence_id"],
            "metadata_hash": e["metadata_hash"],
            "attachment_fingerprint": e["attachment_fingerprint"],
        }
        for e in plans
    ]


def cmd_readcube(args) -> int:
    repo_root = Path(args.repo).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()
    destination_identity = str(destination)

    if getattr(args, "source", "legacy") == "refmgr" and not args.library:
        result = _result_shell(status="error", dry_run=bool(args.dry_run))
        result["blocking_errors"] = [{"reason": "--library is required when --source refmgr"}]
        result["message"] = "--library is required when --source refmgr"
        emit(result)
        return 2

    try:
        plan = _plan_export(repo_root, args)
    except _select.ExportError as exc:
        result = _result_shell(status="error", dry_run=bool(args.dry_run))
        result["blocking_errors"] = [{"reason": str(exc)}]
        result["message"] = str(exc)
        emit(result)
        return 2

    counts = _counts(plan["records"])
    result = _result_shell(status="ok", dry_run=bool(args.dry_run))
    result["ledger_state"] = "tracked" if args.incremental else "not_tracked"
    result["selection"] = {**plan["selection_description"], "count": counts["selected"]}
    result["metadata_only_count"] = counts["metadata_only_count"]
    result["primary_pdf_count"] = counts["primary_pdf_count"]
    result["supplement_count"] = counts["supplement_count"]
    result["warnings"] = plan["warnings"]
    result["blocking_errors"] = plan["blocking_errors"]

    if plan["blocking_errors"] and args.require_pdfs:
        result["status"] = "error"
        result["local_publication_state"] = "blocked"
        emit(result)
        return 3

    # --incremental + --dry-run: report the diff without touching the ledger or
    # publishing anything (dry-run must never mutate state).
    if args.incremental and args.dry_run:
        ledger = _ledger.ExportLedger(repo_root)
        changed, unchanged = ledger.diff(destination_identity, _ledger_candidates(plan["records"]))
        result["unchanged_count"] = len(unchanged)
        result["local_publication_state"] = "dry_run"
        result["exported_count"] = len(changed)
        if not changed:
            result["status"] = "no_op"
        emit(result)
        return 0

    if args.dry_run:
        result["local_publication_state"] = "dry_run"
        result["exported_count"] = counts["selected"]
        emit(result)
        return 0

    if args.incremental:
        ledger = _ledger.ExportLedger(repo_root)
        published_at = now_iso()
        # One lock acquisition spans diff -> publish -> record so no concurrent export
        # process can publish conflicting ledger state in between (plan: "no
        # read-then-later-write-without-holding-the-lock-continuously window").
        with ledger.locked(destination_identity) as handle:
            changed, unchanged = handle.diff(_ledger_candidates(plan["records"]))
            result["unchanged_count"] = len(unchanged)

            if not changed:
                # "A no-change incremental export returns a structured no-op result and
                # does not create an empty batch."
                result["status"] = "no_op"
                result["exported_count"] = 0
                result["local_publication_state"] = "no_op"
                emit(result)
                return 0

            changed_eids = {c["evidence_id"] for c in changed}
            publish_plan = {**plan, "records": [e for e in plan["records"] if e["evidence_id"] in changed_eids]}

            try:
                published = _publish(
                    repo_root=repo_root, destination=destination,
                    name=args.name or "export", plan=publish_plan, args=args,
                )
            except PublicationError as exc:
                for cand in changed:
                    handle.record_failed(cand["evidence_id"], batch_id=None,
                                         published_at=published_at, error=str(exc))
                result["status"] = "error"
                result["local_publication_state"] = "not_published"
                result["blocking_errors"].append({"reason": str(exc)})
                emit(result)
                return 3

            for cand in changed:
                handle.record_published(
                    cand["evidence_id"], metadata_hash=cand["metadata_hash"],
                    attachment_fingerprint=cand["attachment_fingerprint"],
                    batch_id=published["batch_id"], published_at=published_at,
                )

        result["batch_id"] = published["batch_id"]
        result["bundle_path"] = published["bundle_path"]
        result["exported_count"] = len(changed)
        result["total_bytes"] = published["total_bytes"]
        result["local_publication_state"] = "published"
        emit(result)
        return 0

    # Non-incremental: unchanged from prior behavior — every selected record is
    # published every time, the ledger is never consulted.
    try:
        published = _publish(
            repo_root=repo_root, destination=destination,
            name=args.name or "export", plan=plan, args=args,
        )
    except PublicationError as exc:
        result["status"] = "error"
        result["local_publication_state"] = "not_published"
        result["blocking_errors"].append({"reason": str(exc)})
        emit(result)
        return 3

    result["batch_id"] = published["batch_id"]
    result["bundle_path"] = published["bundle_path"]
    result["exported_count"] = counts["selected"]
    result["total_bytes"] = published["total_bytes"]
    result["local_publication_state"] = "published"
    emit(result)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="export.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("readcube", help="publish a ReadCube-importable bundle")
    p.add_argument("--repo", required=True)
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--all", action="store_true")
    sel.add_argument("--evidence-id", action="append", default=None)
    sel.add_argument("--run-dir")
    p.add_argument("--destination", required=True)
    p.add_argument("--name", default="export")
    p.add_argument("--incremental", action="store_true")
    p.add_argument("--bib", action="store_true")
    p.add_argument("--require-pdfs", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    # Phase 2.6: which AssetSource resolves attachments. "legacy" (default) reads
    # data/papers/registry.jsonl (--repo) -- where real data lives today. "refmgr"
    # reads the newer SQLite refmgr library (--library) and supports multiple
    # attachments/roles/versions per paper; only --all selection against it for now
    # (see _select_records_refmgr). The export ledger (--incremental) is always
    # keyed off --repo regardless of --source, since it's a research-repo artifact.
    p.add_argument("--source", choices=["legacy", "refmgr"], default="legacy")
    p.add_argument("--library", help="refmgr library root; required when --source refmgr")
    p.set_defaults(func=cmd_readcube)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
