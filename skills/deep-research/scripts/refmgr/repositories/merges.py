"""Paper merge service: preview, execute, and revert duplicate merges.

REFERENCE_MANAGER_V2_PLAN.md invariant #3 ("Ambiguous paper matches require
review") is why `execute_merge` is a distinct, explicit action rather than
something identifier/attachment import ever does implicitly -- a caller must
have already decided `survivor_id`/`absorbed_id` are the same paper.

The "Audit/merge" row ("Metadata changes, merge mappings and reversible
actions; never silently discard conflicting data") is why every merge is
recorded as a `merges` row plus a reversible `audit_log` entry with enough
detail (`mapping_json`) to undo it via `revert_merge`, and why nothing here
ever drops an absorbed paper's identifiers/attachments/collections/tags --
they are moved onto the survivor, never deleted.

Note on transactions: `execute_merge` and `revert_merge` each run their
entire mutation sequence inside a single `db.transaction(conn)` block,
calling the `_locked` core of each repository method (e.g. `_reassign_locked`,
`_add_to_collection_locked`, `_add_tag_locked`, `_soft_delete_locked`,
`_restore_locked`, `_reassign_paper_locked`, `_reassign_ids_locked`) instead
of the public wrapper, since the public wrappers each open their own
transaction and SQLite's `BEGIN IMMEDIATE` cannot nest. This makes the whole
merge (or revert) atomic: any exception partway through rolls back every
mutation made so far, including the `merges` row and the `audit_log` entry.

Every record type keyed on `paper_id` moves: identifiers, attachments,
collection membership, tags, chunks, terms, and figures, plus a reindex of
`papers_fts` for both survivor (identifiers/title changed) and absorbed (now
soft-deleted, so its row is dropped). `chunks`/`figures` move by exact row id
so a span or a crop's source-attachment provenance is never rederived or
approximated -- only its `paper_id` column changes. `revert_merge` checks
that every moved record is still owned by survivor before moving anything
back (`InterveningChangeError` otherwise): a later merge or a direct
reassignment may have moved the same record again, and blindly reclaiming it
would silently steal it from whatever operation owns it now.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .. import db, identity
from .attachments import AttachmentRepository
from .audit import AuditRepository
from .chunks import ChunkRepository
from .figures import FigureRepository
from .identifiers import IdentifierRepository
from .organization import OrganizationRepository
from .papers import PaperRepository
from .search import SearchRepository
from .terms import TermRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SelfMergeError(ValueError):
    """Raised when survivor_id and absorbed_id name the same paper."""


class DeletedSurvivorError(ValueError):
    """Raised when the requested survivor is already soft-deleted."""


class InterveningChangeError(ValueError):
    """Raised when revert_merge finds moved data no longer owned by survivor.

    A later merge or a direct reassignment can move a record this merge
    moved onto survivor somewhere else again. Reverting must never blindly
    move that record back -- it may belong to a different operation's
    bookkeeping now -- so the whole revert is refused with enough detail to
    reconcile by hand instead.
    """


class MergeService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._papers = PaperRepository(conn)
        self._identifiers = IdentifierRepository(conn)
        self._attachments = AttachmentRepository(conn)
        self._organization = OrganizationRepository(conn)
        self._audit = AuditRepository(conn)
        self._chunks = ChunkRepository(conn)
        self._terms = TermRepository(conn)
        self._figures = FigureRepository(conn)
        self._search = SearchRepository(conn)

    def _validate_pair(self, survivor_id: str, absorbed_id: str) -> None:
        if survivor_id == absorbed_id:
            raise SelfMergeError(
                f"cannot merge paper {survivor_id!r} into itself"
            )
        survivor = self._papers.get(survivor_id, include_deleted=True)
        if survivor is None:
            raise KeyError(survivor_id)
        if survivor["deleted_at"] is not None:
            raise DeletedSurvivorError(
                f"survivor {survivor_id!r} is already soft-deleted"
            )
        if self._papers.get(absorbed_id, include_deleted=True) is None:
            raise KeyError(absorbed_id)

    # -- preview ----------------------------------------------------------

    def preview_merge(self, survivor_id: str, absorbed_id: str) -> dict:
        # Soft-deleted *absorbed* papers are a valid input (e.g. previewing a
        # merge involving a paper already flagged for removal); a
        # soft-deleted or identical survivor is not, so this shares
        # `_validate_pair`'s guards with `execute_merge`.
        self._validate_pair(survivor_id, absorbed_id)

        survivor_identifiers = self._identifiers.list_for_paper(survivor_id)
        absorbed_identifiers = self._identifiers.list_for_paper(absorbed_id)
        survivor_by_scheme_value = {
            (row["scheme"], row["value"]): row["id"] for row in survivor_identifiers
        }

        identifiers_to_move = [
            {"id": row["id"], "scheme": row["scheme"], "value": row["value"]}
            for row in absorbed_identifiers
        ]

        # Defensive only: the schema's UNIQUE(scheme, value) constraint on
        # `identifiers` means no two identifier rows can ever share a
        # (scheme, value) pair. An absorbed identifier therefore can never
        # collide with a *different* identifier row already on survivor --
        # this loop should always produce an empty list in practice, but we
        # check for it rather than assume it, per "never silently discard
        # conflicting data".
        identifier_conflicts = []
        for row in absorbed_identifiers:
            key = (row["scheme"], row["value"])
            survivor_identifier_id = survivor_by_scheme_value.get(key)
            if survivor_identifier_id is not None and survivor_identifier_id != row["id"]:
                identifier_conflicts.append(
                    {
                        "scheme": row["scheme"],
                        "value": row["value"],
                        "survivor_identifier_id": survivor_identifier_id,
                    }
                )

        attachments_to_move = [
            {"id": row["id"], "role": row["role"], "original_filename": row["original_filename"]}
            for row in self._attachments.list_for_paper(absorbed_id)
        ]

        survivor_collection_ids = {
            row["id"] for row in self._organization.list_collections_for_paper(survivor_id)
        }
        collections_to_add = [
            row["id"]
            for row in self._organization.list_collections_for_paper(absorbed_id)
            if row["id"] not in survivor_collection_ids
        ]

        survivor_tags = set(self._organization.list_tags(survivor_id))
        tags_to_add = [
            tag for tag in self._organization.list_tags(absorbed_id) if tag not in survivor_tags
        ]

        return {
            "survivor_id": survivor_id,
            "absorbed_id": absorbed_id,
            "identifiers_to_move": identifiers_to_move,
            "attachments_to_move": attachments_to_move,
            "collections_to_add": collections_to_add,
            "tags_to_add": tags_to_add,
            "identifier_conflicts": identifier_conflicts,
        }

    # -- execute ------------------------------------------------------------

    def execute_merge(self, survivor_id: str, absorbed_id: str) -> str:
        # Step 1: validate before mutating anything -- rejects merging a
        # paper into itself and merging into an already soft-deleted survivor.
        self._validate_pair(survivor_id, absorbed_id)

        with db.transaction(self.conn):
            survivor_identifiers = self._identifiers.list_for_paper(survivor_id)
            survivor_by_scheme_value = {
                (row["scheme"], row["value"]): row["id"] for row in survivor_identifiers
            }

            # Step 2: reassign identifiers, skipping the no-op case where an
            # identical (scheme, value) row already points at survivor.
            identifiers_moved = []
            for row in self._identifiers.list_for_paper(absorbed_id):
                key = (row["scheme"], row["value"])
                existing_survivor_identifier_id = survivor_by_scheme_value.get(key)
                if existing_survivor_identifier_id == row["id"]:
                    continue
                self._identifiers._reassign_locked(row["id"], survivor_id)
                identifiers_moved.append(row["id"])

            # An absorbed identifier can arrive already flagged primary for its
            # scheme, which survivor may independently already have its own
            # primary for -- collapse to one per scheme rather than leave two.
            if identifiers_moved:
                self._identifiers._normalize_primaries_locked(survivor_id)

            # Step 3: reassign non-deleted attachments.
            attachments_moved = []
            for row in self._attachments.list_for_paper(absorbed_id):
                self._attachments._reassign_locked(row["id"], survivor_id)
                attachments_moved.append(row["id"])

            # Step 4: add survivor to any collection absorbed was in that
            # survivor wasn't already in.
            survivor_collection_ids = {
                row["id"] for row in self._organization.list_collections_for_paper(survivor_id)
            }
            collections_added = []
            for row in self._organization.list_collections_for_paper(absorbed_id):
                if row["id"] not in survivor_collection_ids:
                    self._organization._add_to_collection_locked(row["id"], survivor_id)
                    collections_added.append(row["id"])

            # Step 5: add any tag absorbed has that survivor doesn't.
            survivor_tags = set(self._organization.list_tags(survivor_id))
            tags_added = []
            for tag in self._organization.list_tags(absorbed_id):
                if tag not in survivor_tags:
                    self._organization._add_tag_locked(survivor_id, tag)
                    tags_added.append(tag)

            # Step 6: reassign chunks, terms, and figures -- the other three
            # record types keyed on paper_id besides identifiers/attachments.
            chunks_moved = self._chunks._reassign_paper_locked(absorbed_id, survivor_id)
            terms_result = self._terms._reassign_paper_locked(absorbed_id, survivor_id)
            figures_moved = self._figures._reassign_paper_locked(absorbed_id, survivor_id)

            # Step 7: soft-delete the absorbed paper.
            self._papers._soft_delete_locked(absorbed_id)

            # Step 8: bring metadata FTS in line with everything moved above
            # (survivor's identifiers/title changed; absorbed is now deleted
            # so its row is dropped) so a later reindex/mirror pass has
            # nothing left to "undo" the merge by resurrecting a stale row.
            self._search._reindex_paper_locked(survivor_id)
            self._search._reindex_paper_locked(absorbed_id)

            # Step 9: build the reversible mapping.
            mapping = {
                "survivor_paper_id": survivor_id,
                "absorbed_paper_id": absorbed_id,
                "identifiers_moved": identifiers_moved,
                "attachments_moved": attachments_moved,
                "collections_added": collections_added,
                "tags_added": tags_added,
                "chunks_moved": chunks_moved,
                "terms_moved": terms_result["moved"],
                "terms_dropped_duplicates": terms_result["dropped_duplicates"],
                "figures_moved": figures_moved,
            }

            # Step 10: record the merge row and audit entry in the same
            # transaction as every mutation above.
            merge_id = identity.new_id()
            now = _now()
            self.conn.execute(
                "INSERT INTO merges "
                "(id, survivor_paper_id, absorbed_paper_id, mapping_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (merge_id, survivor_id, absorbed_id, json.dumps(mapping), now),
            )
            self._audit.record(
                entity_type="paper",
                entity_id=survivor_id,
                action="merge",
                before=None,
                after=mapping,
                reversible=True,
            )

        return merge_id

    # -- revert ---------------------------------------------------------

    def revert_merge(self, merge_id: str) -> None:
        row = self.conn.execute(
            "SELECT * FROM merges WHERE id = ?", (merge_id,)
        ).fetchone()
        if row is None:
            raise KeyError(merge_id)
        if row["reverted_at"] is not None:
            raise ValueError(f"merge {merge_id!r} was already reverted")

        mapping = json.loads(row["mapping_json"])
        survivor_id = mapping["survivor_paper_id"]
        absorbed_id = mapping["absorbed_paper_id"]

        with db.transaction(self.conn):
            self._check_revert_ownership_locked(mapping, survivor_id)

            for identifier_id in mapping["identifiers_moved"]:
                self._identifiers._reassign_locked(identifier_id, absorbed_id)
            if mapping["identifiers_moved"]:
                self._identifiers._normalize_primaries_locked(absorbed_id)
                self._identifiers._normalize_primaries_locked(survivor_id)

            for attachment_id in mapping["attachments_moved"]:
                self._attachments._reassign_locked(attachment_id, absorbed_id)

            for collection_id in mapping["collections_added"]:
                self._organization._remove_from_collection_locked(collection_id, survivor_id)

            for tag in mapping["tags_added"]:
                self._organization._remove_tag_locked(survivor_id, tag)

            self._chunks._reassign_ids_locked(mapping.get("chunks_moved", []), absorbed_id)
            self._figures._reassign_ids_locked(mapping.get("figures_moved", []), absorbed_id)
            for term in mapping.get("terms_moved", []):
                self.conn.execute(
                    "DELETE FROM paper_terms WHERE paper_id = ? AND scheme = ? "
                    "AND value_norm = ?",
                    (survivor_id, term["scheme"], term["value_norm"]),
                )
                self.conn.execute(
                    "INSERT OR IGNORE INTO paper_terms "
                    "(paper_id, scheme, value, value_norm) VALUES (?, ?, ?, ?)",
                    (absorbed_id, term["scheme"], term["value"], term["value_norm"]),
                )

            self._papers._restore_locked(absorbed_id)

            self._search._reindex_paper_locked(survivor_id)
            self._search._reindex_paper_locked(absorbed_id)

            now = _now()
            self.conn.execute(
                "UPDATE merges SET reverted_at = ? WHERE id = ?", (now, merge_id)
            )
            self._audit.record(
                entity_type="paper",
                entity_id=survivor_id,
                action="revert_merge",
                before=mapping,
                after=None,
                reversible=False,
            )

    def _check_revert_ownership_locked(self, mapping: dict, survivor_id: str) -> None:
        conflicts = []

        for identifier_id in mapping["identifiers_moved"]:
            row = self.conn.execute(
                "SELECT paper_id FROM identifiers WHERE id = ?", (identifier_id,)
            ).fetchone()
            if row is None or row["paper_id"] != survivor_id:
                conflicts.append({"kind": "identifier", "id": identifier_id})

        for attachment_id in mapping["attachments_moved"]:
            row = self.conn.execute(
                "SELECT paper_id FROM attachments WHERE id = ?", (attachment_id,)
            ).fetchone()
            if row is None or row["paper_id"] != survivor_id:
                conflicts.append({"kind": "attachment", "id": attachment_id})

        chunk_owners = self._chunks.current_owners(mapping.get("chunks_moved", []))
        for chunk_id in mapping.get("chunks_moved", []):
            if chunk_owners.get(chunk_id) != survivor_id:
                conflicts.append({"kind": "chunk", "id": chunk_id})

        figure_owners = self._figures.current_owners(mapping.get("figures_moved", []))
        for figure_id in mapping.get("figures_moved", []):
            if figure_owners.get(figure_id) != survivor_id:
                conflicts.append({"kind": "figure", "id": figure_id})

        for term in mapping.get("terms_moved", []):
            exists = self.conn.execute(
                "SELECT 1 FROM paper_terms WHERE paper_id = ? AND scheme = ? "
                "AND value_norm = ?",
                (survivor_id, term["scheme"], term["value_norm"]),
            ).fetchone()
            if exists is None:
                conflicts.append(
                    {"kind": "term", "scheme": term["scheme"], "value_norm": term["value_norm"]}
                )

        if conflicts:
            raise InterveningChangeError(
                f"cannot revert: {len(conflicts)} record(s) moved by this merge are no "
                f"longer owned by survivor {survivor_id!r} -- a later merge or edit moved "
                f"them again: {conflicts}"
            )
