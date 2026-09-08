# schema §2 — `taskboard record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 2. `taskboard record`

One line of `taskboard.jsonl`. Written **only** by `corpus.py task claim|complete|fail|block|list`
(`SKILL.md` "Pipeline"). The coordinator never edits this file by hand.

```json
{
  "schema_version": 1,
  "task_id": "extract:pmid:12345678",
  "stage": "extract",
  "status": "pending",
  "inputs_hash": "sha256:1f0c9a...",
  "attempts": 1,
  "worker": "extractor-03",
  "output_path": "workspace/extractions/pmid-12345678.json",
  "error": null,
  "created_at": "2026-09-08T00:00:00Z",
  "updated_at": "2026-09-08T00:00:00Z"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `task_id` | string | yes | Unique task key. Grammar below. |
| `stage` | enum | yes | `protocol` \| `search` \| `screen` \| `adjudicate` \| `retrieve` \| `extract` \| `appraise` \| `synthesize` \| `verify` \| `report` \| `okf`. |
| `status` | enum | yes | `pending` \| `active` \| `completed` \| `blocked` \| `failed` \| `cancelled`. |
| `inputs_hash` | string | yes | `sha256:<hex>` over the canonicalized task inputs. Computed by `corpus.py`. A changed hash invalidates a `completed` output and permits a rewrite. |
| `attempts` | int | yes | Count of claims, starting at 1. The malformed-JSON retry (S5) increments it. |
| `worker` | string \| null | yes | Logical worker id, e.g. `extractor-03`, `screener-a`, `main`. `null` while `pending`. |
| `output_path` | string \| null | yes | Run-relative path of the expected/actual result file. |
| `error` | string \| null | yes | Diagnostic text for `failed`/`blocked`; `null` otherwise. Single line preferred. |
| `created_at` | string | yes | ISO-8601 UTC Z. |
| `updated_at` | string | yes | ISO-8601 UTC Z; stamped on every transition. |

### Status transitions

```text
pending -> active -> completed
                  -> failed  -> pending (retry, attempts+1)
                  -> blocked
pending -> cancelled          (budget/dedupe/no-progress guard)
active  -> cancelled          (max_wall_time)
```

`completed` is terminal unless `inputs_hash` changes. `blocked` is terminal within the run but
re-openable by a resume (e.g. user drops a PDF into `inbox/`).

### `task_id` grammar

```text
task_id   := <stage> ":" <key-kind> ":" <key>
stage     := protocol|search|screen|adjudicate|retrieve|extract|appraise|synthesize|verify|report|okf
key-kind  := pmid | doi | pmcid | query | url | slug | batch
key       := [A-Za-z0-9._~-]+          # DOIs are slugified: "/" -> "-", ":" -> "-"
```

Examples: `extract:pmid:12345678`, `search:query:q3`, `screen:batch:b02`,
`appraise:doi:10-1000-example`, `retrieve:pmcid:PMC1234567`, `okf:slug:cbt-adolescent-depression`.
Dual screening disambiguates by worker, not by `task_id` prefix collision:
`screen:pmid:12345678` exists once per screener id encoded in the batch task
(`screen:batch:b02` with `worker: screener-a` / `screener-b`).

---
