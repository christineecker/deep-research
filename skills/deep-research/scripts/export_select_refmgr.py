#!/usr/bin/env python3
"""export_select_refmgr.py — second `AssetSource` implementation, reading the newer
SQLite-backed `refmgr` layer instead of the legacy JSONL registry.

Implements Phase 2.6 of `REFERENCE_MANAGER_V2_PLAN.md` ("New Phase 2 — ReadCube export
command" > "2.6 — `RefmgrAssetSource`"), per the "Asset-resolution bridging" decision above
it: the export command's core is not hardcoded to either storage layer, it talks to the
`AssetSource` ABC defined in `export_select.py` (`resolve(record) -> AssetResolution`).
`LegacyRegistryAssetSource` (in `export_select.py`) is the first implementation, reading the
registry's single `fulltext` pointer per record. `RefmgrExportSource` (this module) is the
second: it reads `AttachmentRepository`/`AssetRepository`, which already solve the "one
primary asset pointer" limitation the legacy source has — a `refmgr` paper can have multiple
attachments (different roles, versions), with a `preferred` flag and checksum-verified
content-addressed storage.

This module is standalone: it does not modify, import from, or get imported by `export.py`
or `export_select.py`. Wiring CLI selection between the two sources is a separate task.

Multi-attachment vs. the single-resolution `AssetSource` contract
-------------------------------------------------------------------
`AssetSource.resolve(record) -> AssetResolution` is singular by design (one caller, one
answer). A `refmgr` paper doesn't fit that shape directly — it may have zero, one, or many
attachments. This module keeps the singular contract for interface compatibility (so code
that only wants "the" primary asset for a record still works via `resolve`), and adds a
second, richer method, `resolve_all(paper_id) -> list[AssetResolution]`, for callers (a
future `export.py` update, not part of this task) that want the full multi-attachment
picture. `resolve` is defined in terms of `resolve_all`: it returns the first item (the
attachment marked `is_preferred_reader`, if any, else the earliest-created one), or a single
"no attachments" `AssetResolution` if the paper has none.

Adapter: legacy-shaped export records vs. refmgr papers
--------------------------------------------------------
`ris.build_ris_record` and `export.py`'s manifest-building code consume legacy registry
records (see `corpus.py` `CORPUS_FIELDS`/`CORPUS_BIBLIO` for the field list). A `refmgr`
paper isn't shaped like that — its bibliographic detail lives in a flexible `metadata` JSON
blob, and its identifiers live in a separate table rather than top-level `pmid`/`doi`/`pmcid`
keys. `paper_to_export_record` adapts a refmgr paper into a dict shaped as closely as
reasonably possible to the legacy shape, so a future caller need not branch on which store a
record came from for every field. Notably:

- `evidence_id` is the refmgr paper's own immutable id (`identity.new_id()`-generated),
  NOT a legacy `scheme:value` evidence_id. Legacy and refmgr evidence_ids are NOT
  interchangeable or comparable without an explicit lookup — this is expected for this
  phase (Phase 1 intentionally did not migrate legacy data into `refmgr`; see "Asset-
  resolution bridging" in the plan).
- `fulltext` is omitted entirely (the multi-attachment reality doesn't fit the legacy
  single-fulltext-dict shape). A caller wanting assets must use `RefmgrExportSource.resolve`
  / `.resolve_all` directly, never look for `record["fulltext"]`.
- Legacy fields with no refmgr equivalent yet (`mesh_terms`, `keywords`,
  `retraction_status`, ...) are omitted, not set to `None` — matching `ris.py`'s "absent
  means omitted" convention.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_select import AssetResolution, AssetSource  # noqa: E402  (sibling module)
from refmgr.service import ReferenceManagerService  # noqa: E402  (sibling package)

# Legacy `corpus.py` field names that a refmgr paper's `metadata` dict may also use.
# Anything else in `metadata` is simply not passed through (a straightforward key-name
# passthrough, per the plan, not an attempt to invent a full mapping table).
_METADATA_PASSTHROUGH_FIELDS = (
    "journal",
    "publication_date",
    "authors",
    "article_types",
    "is_preprint",
    "source",
    "volume",
    "issue",
    "pages",
    "abstract",
)

# Internal (by-convention-private) key `paper_to_export_record` embeds in its output so
# `RefmgrExportSource.resolve` can recover the source paper id from a plain dict record.
_PAPER_ID_KEY = "_refmgr_paper_id"

_NO_ATTACHMENTS = AssetResolution(
    available=False,
    local_path=None,
    sha256_expected=None,
    role="unknown",
    problem="no attachments",
)


def paper_to_export_record(service: "ReferenceManagerService", paper_id: str) -> dict:
    """Adapt a refmgr paper into a dict shaped like a legacy registry export record.

    See module docstring "Adapter" section for the mapping rules and their limitations.
    Raises `KeyError` if `paper_id` does not resolve to a non-deleted paper (matching
    `PaperRepository`'s own `KeyError` convention for missing ids).
    """
    paper = service.papers.get(paper_id)
    if paper is None:
        raise KeyError(paper_id)

    record: dict = {
        "evidence_id": paper["id"],
        "title": paper["title"],
        _PAPER_ID_KEY: paper["id"],
    }

    metadata = paper.get("metadata") or {}
    for field in _METADATA_PASSTHROUGH_FIELDS:
        if field in metadata:
            record[field] = metadata[field]

    # Identifiers: `identifiers` table has UNIQUE(scheme, value) but does not prevent
    # multiple DIFFERENT values for the same scheme on one paper (a preprint DOI
    # alongside a published one, say). `primary_for_scheme` is the one place that
    # policy is decided (refmgr/repositories/identifiers.py) -- export uses it rather
    # than picking its own "first by created_at" here, so the two never disagree.
    for scheme in ("doi", "pmid", "pmcid"):
        identifier = service.identifiers.primary_for_scheme(paper_id, scheme)
        if identifier is not None:
            record[scheme] = identifier["value"]

    return record


def _attachment_sort_key(attachment: dict) -> tuple[int, str]:
    # Preferred attachment first (0 sorts before 1), then by created_at.
    preferred_rank = 0 if attachment.get("is_preferred_reader") else 1
    return (preferred_rank, attachment.get("created_at") or "")


class RefmgrExportSource(AssetSource):
    """`AssetSource` reading `refmgr`'s `AttachmentRepository`/`AssetRepository` instead of
    the legacy registry's single `fulltext` pointer (plan Phase 2.6).

    Owns its own `ReferenceManagerService` (opened in `__init__`); call `close()` or use as
    a context manager to release the underlying SQLite connection.
    """

    def __init__(self, library_root):
        self.library_root = Path(library_root)
        self.service = ReferenceManagerService(self.library_root)

    def close(self) -> None:
        self.service.close()

    def __enter__(self) -> "RefmgrExportSource":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def resolve(self, record: dict) -> AssetResolution:
        """Satisfy the `AssetSource` ABC: return just the first (preferred/primary)
        resolution for the paper `record` (produced by `paper_to_export_record`) points at.
        """
        paper_id = record.get(_PAPER_ID_KEY)
        if not paper_id:
            raise ValueError(
                f"record missing {_PAPER_ID_KEY!r} — was it produced by "
                "paper_to_export_record()?"
            )
        resolutions = self.resolve_all(paper_id)
        return resolutions[0] if resolutions else _NO_ATTACHMENTS

    def resolve_all(self, paper_id: str) -> list[AssetResolution]:
        """One `AssetResolution` per non-deleted attachment for `paper_id`, ordered with
        the `is_preferred_reader` attachment first (if any), otherwise by `created_at`.
        """
        attachments = sorted(
            self.service.attachments.list_for_paper(paper_id),
            key=_attachment_sort_key,
        )
        return [self._resolve_attachment(attachment) for attachment in attachments]

    def _resolve_attachment(self, attachment: dict) -> AssetResolution:
        role = attachment["role"]
        asset_sha256 = attachment["asset_sha256"]
        asset = self.service.assets.get(asset_sha256)
        if asset is None:
            # Shouldn't normally happen given the FK, but per the plan's "visible
            # diagnostics over silent failure" invariant, check rather than assume.
            return AssetResolution(False, None, None, role, "asset record missing")

        if not self.service.assets.verify_integrity(asset_sha256):
            return AssetResolution(
                False, None, asset_sha256, role, "checksum mismatch or missing file"
            )

        local_path = self.library_root / asset["storage_path"]
        return AssetResolution(True, local_path, asset_sha256, role, None)

    def list_papers_for_export(self) -> list[str]:
        """Every non-deleted paper id in the library (a refmgr-shaped counterpart to
        `export_select.select_all`, for a future `--all`-equivalent selection)."""
        paper_ids: list[str] = []
        offset = 0
        limit = 200
        while True:
            page = self.service.papers.list(limit=limit, offset=offset, include_deleted=False)
            if not page:
                break
            paper_ids.extend(paper["id"] for paper in page)
            if len(page) < limit:
                break
            offset += limit
        return paper_ids
