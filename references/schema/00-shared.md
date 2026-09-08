# schema §0 — Shared rules

These rules bind every record in `references/schema/`. Index: `references/schema.md`.

## 0. Shared rules

| Rule | Statement |
|---|---|
| S1 | Every record carries `schema_version: 1` (integer). Readers reject records without it. |
| S2 | All timestamps are ISO-8601 UTC with a literal `Z`: `2026-09-08T14:03:00Z`. No local time, no offsets. Dates without a time (e.g. `publication_date`) are `YYYY-MM-DD`; partial PubMed dates degrade to `YYYY-MM` or `YYYY`. |
| S3 | Unknown values are `null` (or `[]` for lists), never invented, never guessed, never `"N/A"`, never `""`. A field that PubMed did not supply is `null`. |
| S4 | Subagents NEVER return paper text, abstracts, quotes, or the full result JSON to the coordinator. They write their result file themselves and return exactly one `receipt` object (§1). |
| S5 | Malformed subagent JSON (unparseable, missing required field, enum violation) is retried **once** with the schema error text appended to the task prompt; a second failure marks the task `failed` and then `blocked`. `SKILL.md` "Failure semantics" |
| S6 | Enums are closed. An unrecognized enum value is a schema error, not a passthrough. |
| S7 | All `*_path` fields are POSIX paths **relative to the run directory** (`<wiki>/outputs/deep-research/<slug>/`), except `sources[].resource` in OKF frontmatter and `local_path` for the shared PDF library, which are relative to the wiki root. No absolute paths are stored. |
| S8 | Files under `workspace/` are one JSON object per file, pretty-printed. `corpus.jsonl` and `taskboard.jsonl` are JSON Lines: one compact object per line, no trailing commas, append-only. |
| S9 | `evidence_id` is the primary evidence key: `pmid:<pmid>` when a PMID exists, else `doi:<doi>`, else `pmcid:<pmcid>`, else `url:<sha256-of-url-first16>`. Stable across resumes. |
| S10 | Records are immutable once their task is `completed`, unless the task's `inputs_hash` changes (`SKILL.md` "Pipeline"). |

---
