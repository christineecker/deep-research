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
`_restore_locked`) instead of the public wrapper, since the public wrappers
each open their own transaction and SQLite's `BEGIN IMMEDIATE` cannot nest.
This makes the whole merge (or revert) atomic: any exception partway through
rolls back every mutation made so far, including the `merges` row and the
`audit_log` entry.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .. import db, identity
from .attachments import AttachmentRepository
from .audit import AuditRepository
from .identifiers import IdentifierRepository
from .organization import OrganizationRepository
from .papers import PaperRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MergeService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._papers = PaperRepository(conn)
        self._identifiers = IdentifierRepository(conn)
        self._attachments = AttachmentRepository(conn)
        self._organization = OrganizationRepository(conn)
        self._audit = AuditRepository(conn)

    # -- preview ----------------------------------------------------------

    def preview_merge(self, survivor_id: str, absorbed_id: str) -> dict:
        # Soft-deleted papers are valid inputs here (e.g. previewing a merge
        # involving a paper already flagged for removal), so existence
        # checks include deleted rows.
        if self._papers.get(survivor_id, include_deleted=True) is None:
            raise KeyError(survivor_id)
        if self._papers.get(absorbed_id, include_deleted=True) is None:
            raise KeyError(absorbed_id)

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
        # Step 1: validate before mutating anything.
        if self._papers.get(survivor_id, include_deleted=True) is None:
            raise KeyError(survivor_id)
        if self._papers.get(absorbed_id, include_deleted=True) is None:
            raise KeyError(absorbed_id)

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

            # Step 6: soft-delete the absorbed paper.
            self._papers._soft_delete_locked(absorbed_id)

            # Step 7: build the reversible mapping.
            mapping = {
                "survivor_paper_id": survivor_id,
                "absorbed_paper_id": absorbed_id,
                "identifiers_moved": identifiers_moved,
                "attachments_moved": attachments_moved,
                "collections_added": collections_added,
                "tags_added": tags_added,
            }

            # Step 8: record the merge row and audit entry in the same
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
            for identifier_id in mapping["identifiers_moved"]:
                self._identifiers._reassign_locked(identifier_id, absorbed_id)

            for attachment_id in mapping["attachments_moved"]:
                self._attachments._reassign_locked(attachment_id, absorbed_id)

            for collection_id in mapping["collections_added"]:
                self._organization._remove_from_collection_locked(collection_id, survivor_id)

            for tag in mapping["tags_added"]:
                self._organization._remove_tag_locked(survivor_id, tag)

            self._papers._restore_locked(absorbed_id)

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
