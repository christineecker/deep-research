# schema §11 — `event record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 11. `event record`

`<run>/events.jsonl`, JSON Lines, **append-only** (S8). Written by `scripts/source.py`. Nothing
ever rewrites or deletes a line; the file is the run's retrieval history.

```json
{"schema_version":1,"event_id":"ev-0007","type":"fetch","source_id":"src-3f9a1c...","url":"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/","at":"2026-09-08T12:07:44Z","fresh":true,"sha256":"b7e05f...","actor":"main","detail":"pmc full text, 61042 chars, http 200"}
{"schema_version":1,"event_id":"ev-0008","type":"local_pdf","source_id":"src-8c22ef...","url":"file:///assets/papers/pmid-45678901.pdf","at":"2026-09-08T12:11:02Z","fresh":true,"sha256":"41d0be...","actor":"main","detail":"inbox ingest; asset bytes hashed and matched"}
{"schema_version":1,"event_id":"ev-0009","type":"register","source_id":"src-5b70aa...","url":"https://pubmed.ncbi.nlm.nih.gov/23456789/","at":"2026-09-08T12:12:19Z","fresh":false,"sha256":"7c9911...","actor":"main","detail":"abstract already held by corpus.py; folded into store, not re-retrieved"}
{"schema_version":1,"event_id":"ev-0010","type":"read","source_id":"src-3f9a1c...","url":"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/","at":"2026-09-08T12:14:50Z","fresh":false,"sha256":"b7e05f...","actor":"extractor-03","detail":"window 8000-12000"}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `event_id` | string | yes | `ev-` + a zero-padded 4-or-more digit counter, monotonically increasing in file order. Unique within the run. File order is the authoritative ordering; `at` is informational and may tie (R23). |
| `type` | enum | yes | `fetch` \| `local_pdf` \| `register` \| `read`. Closed enum. Meanings below. |
| `source_id` | string | yes | The snapshot this event concerns. For `fetch`/`local_pdf`/`register` the snapshot must already exist when the line is appended (write the snapshot, then the event). |
| `url` | string | yes | Byte-identical to the snapshot's `url`. Duplicated here so freshness can be answered from `events.jsonl` alone. |
| `at` | string | yes | ISO-8601 UTC Z (S2). |
| `fresh` | bool | yes | Whether this event constitutes a fresh retrieval. `true` only for `fetch` with a real network round-trip performed in this run, and for `local_pdf` with a verified asset hash. Always `false` for `register` and `read` (R22). |
| `sha256` | string | yes | Lowercase hex, no prefix. For `fetch`/`register`/`read`: the snapshot's `text` digest (the same digest as `content_hash`, prefix stripped). For `local_pdf`: the digest of the **PDF bytes on disk**, which must equal `snapshot.asset.sha256`. |
| `actor` | string | yes | Who caused the event: `main`, or a logical worker id (`extractor-03`, `appraiser-01`), sharing the taskboard's `worker` vocabulary (§2). |
| `detail` | string \| null | yes | One line, ≤300 chars, machine-greppable. Route, byte/char counts, HTTP status, window read. `null` permitted. |

### Event types

| Type | Emitted when | Counts as fresh |
|---|---|---|
| `fetch` | `source.py fetch` performed an actual network retrieval in this run and wrote (or matched) a snapshot. **This is the event that satisfies the fresh-fetch rule.** | yes, when `fresh: true` |
| `local_pdf` | `source.py local` ingested a user-supplied PDF from `inbox/` or the shared library, recomputed its bytes' sha256, and matched `snapshot.asset.sha256`. | yes, when `fresh: true` (the user-supplied-PDF exception) |
| `register` | Text already held elsewhere in the pipeline (an `eutils.py` abstract, a `fulltext.py` acquisition, a `library.py` cache hit from a *previous* run) was folded into the snapshot store without a new retrieval. | never |
| `read` | A subagent or script read a bounded window of a snapshot (`source.py read` / `source.py spans`). Audit only. | never |

### The fresh-fetch rule

> A `supported` verdict counts only if **every** source backing the claim has a fresh retrieval
> logged in this run's `events.jsonl`.

A source `S` is **fresh for this run** iff `events.jsonl` contains at least one line where all of:

1. `source_id == S`, and
2. `type == "fetch"` **or** `type == "local_pdf"`, and
3. `fresh == true`, and
4. `at >=` the run's `created_at` in `config.json` — the event was produced by *this* run, not
   copied in from another, and
5. `sha256` equals the digest recomputed **now**: the snapshot's `text` digest for `fetch`, the
   asset file's byte digest for `local_pdf`.

Condition 2 excludes `register` and `read`: text carried over from `corpus.jsonl`, from a previous
run's library cache, from a search snippet, or from an existing wiki note is **not** fresh proof
(`references/evidence-kernel.md`). A `fetch` served entirely from an HTTP cache or
from an on-disk cache older than the run writes `fresh: false` and does not satisfy the rule.

**User-supplied-PDF exception.** For `origin: "user-supplied-pdf"` there is nothing to re-fetch:
the immutable, hash-checked local file *is* the source. Such a source is fresh iff a `local_pdf`
event exists for it in this run **and** the file at `snapshot.asset.path` (resolved against the
wiki root) still hashes to `snapshot.asset.sha256`. A missing file, a changed file, or a
`local_pdf` event on a snapshot with `asset: null` fails the rule
(`references/evidence-kernel.md`).

---
