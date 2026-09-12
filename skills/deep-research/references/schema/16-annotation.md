# schema §16 — `annotation record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 16. `annotation record`

`<repo>/data/papers/annotations.jsonl`, one JSON line per `evidence_id`. Written and read
by `scripts/annotations.py` only. Personal tags/rating/note, kept separate from
`registry.jsonl` (bibliographic + intake lifecycle) and from project-scoped appraisal
(`data/papers/appraisals/<project>/`, `references/schema/08-appraisal.md`) — see
`references/pool-architecture.md` "Appraisal is project-scoped, on purpose" for why a
personal opinion about a paper is neither of those things. An annotation never requires
its `evidence_id` to already exist in the registry.

```json
{
  "schema_version": 1,
  "evidence_id": "pmid:12345678",
  "tags": ["diagnostics", "to-read"],
  "rating": 4,
  "note": "Good discussion of index-test threshold selection.",
  "updated_at": "2026-09-12T00:00:00Z"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `evidence_id` | string | yes | Links to the corpus/registry record (schema.md S9 precedence: pmid > doi > pmcid > url). No referential check against `registry.jsonl` — annotating an unregistered evidence_id is valid. |
| `tags` | string[] | yes | Sorted, deduped, free-text tags. `[]` when none — never omitted, never `null`. |
| `rating` | int \| absent | no | 1-5 star rating. Omitted (never `null` on disk) when unset. |
| `note` | string \| absent | no | Free-text note. Omitted (never `null` on disk) when unset. |
| `updated_at` | string | yes | ISO-8601 UTC with a literal `Z` (schema rule S2), set on every write. |

### Invariants

- One record per `evidence_id`; `annotations.jsonl` is sorted by `evidence_id`, mirroring
  `registry.jsonl`'s own save-sorted convention.
- `rating`, when present, is an integer 1-5 inclusive; `annotations.py rate` rejects any
  other value before writing. Clearing a rating removes the field rather than writing
  `null`.
- `tags` is always a list, even when empty; adding an already-present tag or removing an
  absent one is a no-op, not an error.
- Written with the same atomic tmp-file + `Path.replace()` pattern as `registry.jsonl`
  (`registry.py Registry.save`), guarded by `advisory_lock(repo_root, "annotations")` — a
  lock distinct from `registry.py`'s own `"registry"` lock, so annotation writes never
  contend with registry writes.
- A corrupt or unparseable line is skipped on load, never raised — mirrors
  `registry.py Registry._load`'s tolerance policy.

---
