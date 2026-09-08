# schema §1 — `receipt`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 1. `receipt`

The one-line object a subagent returns to the coordinator. This is the **only** thing that
crosses the subagent→main boundary.

```json
{
  "schema_version": 1,
  "task_id": "extract:pmid:12345678",
  "status": "completed",
  "output_path": "workspace/extractions/pmid-12345678.json",
  "summary": "RCT, n=240, CBT vs waitlist; primary CDI-2 at 12wk favors intervention (d=0.41)."
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `task_id` | string | yes | The task board `task_id` this receipt answers. Grammar in §2. |
| `status` | enum | yes | `completed` \| `blocked` \| `failed`. `blocked` = external precondition missing (no full text, unauthorized connector); `failed` = the work was attempted and errored. |
| `output_path` | string \| null | yes | Run-relative path to the result file the subagent wrote. `null` only when `status` is `failed` before any file was written. |
| `summary` | string | yes | ≤200 characters. Plain text, single line, no newlines, no markdown, no quotes from the paper. Truncation beyond 200 chars is a schema error, not silently trimmed. |

Prohibited in a receipt: abstract text, full-text excerpts, `quotes[]`, extracted tables, the
result JSON inlined, or any field not listed above.

---
