"""Facade composing the refmgr repositories into higher-level operations.

`ReferenceManagerService` is the entry point CLI/browser code is meant to
call instead of talking to individual repositories directly. It owns one
shared SQLite connection (via `db.open_and_migrate`) and one instance of
each repository built on top of it, and composes them into a handful of
multi-step operations (import a paper with identifiers, import an
attachment, find-or-create by identifier) while preserving the invariants
already enforced at the repository layer -- in particular idempotent
re-imports and "ambiguous paper matches require review"
(REFERENCE_MANAGER_V2_PLAN.md).
"""

from __future__ import annotations

from pathlib import Path

from . import db
from .repositories.assets import AssetRepository
from .repositories.attachments import AttachmentRepository
from .repositories.audit import AuditRepository
from .repositories.chunks import ChunkRepository
from .repositories.identifiers import IdentifierConflictError, IdentifierRepository
from .repositories.organization import OrganizationRepository
from .repositories.papers import PaperRepository
from .repositories.saved_searches import SavedSearchRepository
from .repositories.search import SearchRepository


class ReferenceManagerService:
    def __init__(self, library_root):
        self.library_root = Path(library_root)
        self.conn = db.open_and_migrate(self.library_root)
        self.papers = PaperRepository(self.conn)
        self.identifiers = IdentifierRepository(self.conn)
        self.assets = AssetRepository(self.conn, self.library_root)
        self.attachments = AttachmentRepository(self.conn)
        self.organization = OrganizationRepository(self.conn)
        self.audit = AuditRepository(self.conn)
        self.search = SearchRepository(self.conn)
        self.chunks = ChunkRepository(self.conn)
        self.saved_searches = SavedSearchRepository(self.conn)

    def reindex_paper(self, paper_id: str) -> None:
        """Bring the search index up to date for one paper.

        The service calls this automatically after `add_paper`/
        `find_or_create_paper_by_identifier` create or touch a paper's
        identifiers. Other paper mutations made directly through
        `self.papers` (e.g. `update_metadata`, `soft_delete`) do NOT
        auto-reindex -- the FTS5 index is a rebuildable derived artifact,
        not a live trigger-backed one (REFERENCE_MANAGER_V2_PLAN.md Phase
        4's "rebuildable" design). Call this explicitly after such a
        mutation, or `self.search.rebuild()` periodically; `self.search
        .coverage()` reports how stale the index currently is.
        """
        self.search.reindex_paper(paper_id)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ReferenceManagerService":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def add_paper(
        self,
        title: str,
        paper_type: str,
        metadata: dict | None = None,
        provenance: str | None = None,
        identifiers: list[tuple[str, str]] | None = None,
    ) -> str:
        """Create a paper and attach identifiers, idempotently.

        Safe to call twice with identical arguments: if any given
        identifier already resolves to an existing paper, no second paper
        is created -- the existing paper's id is returned, and any of the
        OTHER given identifiers not yet attached to it are added. Only
        raises `IdentifierConflictError` (propagated) when the given
        identifiers resolve to two different existing papers, which is
        genuinely ambiguous and requires manual review.
        """
        identifiers = identifiers or []

        existing_paper_id = None
        for scheme, raw_value in identifiers:
            found = self.identifiers.find_paper_by_identifier(scheme, raw_value)
            if found is None:
                continue
            if existing_paper_id is None:
                existing_paper_id = found
            elif found != existing_paper_id:
                raise IdentifierConflictError(scheme, raw_value, existing_paper_id, found)

        if existing_paper_id is not None:
            for scheme, raw_value in identifiers:
                self.identifiers.add(existing_paper_id, scheme, raw_value)
            self.reindex_paper(existing_paper_id)
            return existing_paper_id

        paper_id = self.papers.create(
            title=title, paper_type=paper_type, metadata=metadata, provenance=provenance
        )
        for scheme, raw_value in identifiers:
            self.identifiers.add(paper_id, scheme, raw_value)
        self.reindex_paper(paper_id)
        return paper_id

    def import_attachment(
        self,
        paper_id: str,
        source_path,
        role: str,
        mime_type: str | None = None,
        original_filename: str | None = None,
        provenance: str | None = None,
        version_label: str | None = None,
        page_count: int | None = None,
        preferred: bool = False,
    ) -> str:
        """Stage the file's bytes as an asset, then link a new attachment row.

        Calling this twice with the same `source_path` bytes for the same
        paper creates TWO attachment rows pointing at the SAME deduped
        asset -- attachments are never silently merged even when the
        underlying bytes are identical (e.g. deliberately re-uploading a
        supplement creates a second version-labeled attachment), per plan
        invariants #2/#3. This is intentional; do not dedupe attachments
        here.
        """
        asset_sha256 = self.assets.stage_and_commit(source_path, mime_type=mime_type)
        return self.attachments.link(
            paper_id=paper_id,
            asset_sha256=asset_sha256,
            role=role,
            original_filename=original_filename,
            provenance=provenance,
            version_label=version_label,
            page_count=page_count,
            preferred=preferred,
        )

    def find_or_create_paper_by_identifier(
        self,
        scheme: str,
        raw_value: str,
        title: str,
        paper_type: str,
        metadata: dict | None = None,
    ) -> str:
        """Look up a paper by identifier; create one if none is found.

        Does not overwrite title/metadata on an existing match -- metadata
        enrichment happens via a separate `PaperRepository.update_metadata`
        call, never implicitly here.
        """
        existing_paper_id = self.identifiers.find_paper_by_identifier(scheme, raw_value)
        if existing_paper_id is not None:
            return existing_paper_id
        return self.add_paper(
            title=title,
            paper_type=paper_type,
            metadata=metadata,
            identifiers=[(scheme, raw_value)],
        )
