#!/usr/bin/env python3
"""export_select.py — selection + asset-resolution layer for the ReadCube export command.

Implements Phase 2.2 of `REFERENCE_MANAGER_V2_PLAN.md` ("New Phase 2 — ReadCube export
command" > "Command" > "Selection semantics", and "Metadata and PDF handling" >
"Asset resolution"). Does real file I/O (reads the registry, reads run directories, hashes
files) but never writes anything — copying/publishing is a later task (2.3).

Selection
---------
`select_all`, `select_by_evidence_ids`, `select_by_run_dir` each resolve a selection source
down to a list of raw registry records (`registry.py Registry` records — see
`references/schema/04-corpus.md` for the field shapes; `fulltext.local_path`/`fulltext.sha256`
is "the registry's one primary asset pointer" per the plan's "Asset-resolution bridging").
Every failure mode the plan calls out as export-blocking ("fail before publication if a
requested record is unresolved", "Reject runs whose inclusion selection is unavailable or
ambiguous") raises `ExportError` rather than silently narrowing the selection.

`select_by_run_dir` reads a run's `corpus.jsonl` directly (a plain JSON-Lines read, `_read_jsonl`
below) rather than `okf.py load_run`: `load_run` also reads `config.json`, every search/
extraction/appraisal workspace file, `taskboard.jsonl`, `outputs/report.md`, `outputs/digest.md`
and `outputs/verification.json` — none of which selection needs, and requiring their presence
would make this module needlessly coupled to a run's full directory layout (a minimal test run
with just a `corpus.jsonl` should be selectable). Screening/inclusion is exactly the
`corpus.py` `included_screen` computation ("screened = records with a `screening` block;
included = `screening.decision == 'include'`"), reproduced narrowly here.

Asset resolution
----------------
`AssetSource` is the abstract bridge the plan asks for so the export command's core does not
hardcode the legacy registry's "one primary asset pointer" limitation. `LegacyRegistryAssetSource`
is the first (and, for this phase, only) implementation, reading `fulltext.local_path`/
`fulltext.sha256` off a registry record. A second implementation (`RefmgrAssetSource`, reading
`refmgr`'s attachment/asset repositories) is deferred to Phase 2.6 per the plan.
"""

from __future__ import annotations

import abc
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registry as _registry  # noqa: E402  (sibling module, stdlib-only)


class ExportError(Exception):
    """A selection failure that must block the whole export before publication."""


# --------------------------------------------------------------------------- selection


def _sorted_records(records: dict[str, dict]) -> list[dict]:
    return [records[eid] for eid in sorted(records)]


def select_all(repo_root) -> list[dict]:
    """Every registered reference, including metadata-only records, ordered by evidence_id."""
    reg = _registry.Registry(repo_root)
    return _sorted_records(reg.records)


def select_by_evidence_ids(repo_root, evidence_ids: list[str]) -> list[dict]:
    """Resolve each id against the registry (identifier-normalized alias lookup via
    `Registry.lookup`). Raises `ExportError` naming every unresolved id at once — never
    partial, never fail-fast on the first miss."""
    reg = _registry.Registry(repo_root)
    resolved: list[dict] = []
    unresolved: list[str] = []
    for raw_id in evidence_ids:
        rec = _resolve_one(reg, raw_id)
        if rec is None:
            unresolved.append(raw_id)
        else:
            resolved.append(rec)
    if unresolved:
        raise ExportError(
            "unresolved evidence id(s): " + ", ".join(repr(x) for x in unresolved)
        )
    return resolved


def _resolve_one(reg: "_registry.Registry", raw_id: str) -> dict | None:
    """Resolve one requested id against the registry: exact evidence_id key first, then
    `Registry.lookup`'s normalized pmid/doi/pmcid alias resolution (an evidence_id string
    such as `"doi:10.1/x"` is split into its scheme/value so `lookup` can normalize it the
    same way it normalizes an explicit `--doi` argument)."""
    if raw_id in reg.records:
        return reg.records[raw_id]
    scheme, _, value = raw_id.partition(":")
    kwargs: dict[str, str] = {}
    if scheme == "pmid":
        kwargs["pmid"] = value
    elif scheme == "doi":
        kwargs["doi"] = value
    elif scheme == "pmcid":
        kwargs["pmcid"] = value
    else:
        kwargs["evidence_id"] = raw_id
    return reg.lookup(**kwargs)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def select_by_run_dir(repo_root, run_dir) -> list[dict]:
    """Select a run's actually-included records (screening.decision == "include"),
    resolved against the main registry.

    Raises `ExportError` if the run has no resolvable screening/inclusion state at all
    (no corpus records carry a `screening` block), and raises `ExportError` (via
    `select_by_evidence_ids`) if any included evidence_id doesn't resolve in the registry
    — an included run record absent from the registry is a hard error, not a silent skip.
    """
    run_dir = Path(run_dir)
    corpus_path = run_dir / "corpus.jsonl"
    records = _read_jsonl(corpus_path)
    screened = [r for r in records if isinstance(r.get("screening"), dict)]
    if not screened:
        raise ExportError(
            f"run {run_dir} has no resolvable screening/inclusion state: "
            f"{corpus_path} contains no corpus record with a `screening` block "
            f"(is this the right run, or has it not reached the screening stage?)"
        )
    included = [r for r in screened if r["screening"].get("decision") == "include"]
    evidence_ids = [r["evidence_id"] for r in included if r.get("evidence_id")]
    if not evidence_ids:
        # Screening happened but nothing was included — a real (if empty) selection,
        # not an ambiguous one; return no records rather than raising.
        return []
    return select_by_evidence_ids(repo_root, evidence_ids)


# ----------------------------------------------------------------------- asset resolution


class AssetResolution(NamedTuple):
    available: bool
    local_path: Path | None
    sha256_expected: str | None
    role: str  # "primary" | "supplement" | "version" | "unknown"
    problem: str | None


class AssetSource(abc.ABC):
    """Bridge interface so the export core is not hardcoded to the legacy registry's
    single-asset-pointer limitation (plan "Asset-resolution bridging")."""

    @abc.abstractmethod
    def resolve(self, record: dict) -> AssetResolution:
        raise NotImplementedError


def _icloud_placeholder_problem(path: Path) -> str | None:
    """Best-effort macOS iCloud placeholder detection: a 0-byte file with a sibling
    `.<name>.icloud` marker. Never raises — inconclusive on this OS/filesystem is treated
    as "not a placeholder", not an error."""
    try:
        if path.stat().st_size != 0:
            return None
        sibling = path.with_name("." + path.name + ".icloud")
        if sibling.exists():
            return "iCloud placeholder not downloaded"
    except OSError:
        return None
    return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LegacyRegistryAssetSource(AssetSource):
    """Reads a registry record's single `fulltext.local_path`/`fulltext.sha256` pointer
    (plan: "The registry currently exposes one primary asset pointer")."""

    def __init__(self, repo_root):
        self.repo_root = Path(repo_root).expanduser().resolve()

    def resolve(self, record: dict) -> AssetResolution:
        fulltext = record.get("fulltext")
        if not isinstance(fulltext, dict) or not fulltext.get("local_path"):
            return AssetResolution(False, None, None, "primary", "no fulltext recorded")

        raw_path = fulltext["local_path"]
        path = Path(raw_path)
        if not path.is_absolute():
            # Same convention as `registry.py add-pdf`/`fulltext.py finalize`: local_path
            # is stored repo-root-relative.
            path = self.repo_root / path

        if not path.exists():
            return AssetResolution(False, None, None, "primary", "missing file")

        icloud_problem = _icloud_placeholder_problem(path)
        if icloud_problem:
            return AssetResolution(False, None, None, "primary", icloud_problem)

        if not os.access(path, os.R_OK):
            return AssetResolution(False, None, None, "primary", "unreadable")

        try:
            digest = _sha256_file(path)
        except OSError:
            return AssetResolution(False, None, None, "primary", "unreadable")

        expected = fulltext.get("sha256")
        if expected and digest != expected:
            return AssetResolution(False, None, None, "primary", "checksum mismatch")

        return AssetResolution(True, path, expected or digest, "primary", None)
