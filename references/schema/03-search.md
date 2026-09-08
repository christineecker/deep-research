# schema §3 — `search result record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 3. `search result record`

`workspace/search/<query_id>.json`. One file per executed query. Every query and hit count is
logged for reproducibility (`PLAN.md` §5 stage 2).

```json
{
  "schema_version": 1,
  "query_id": "q3",
  "query_string": "(\"Cognitive Behavioral Therapy\"[mh] AND adolescent[mh]) AND (\"2015\"[dp] : \"2026\"[dp])",
  "translated_query": "\"cognitive behavioral therapy\"[MeSH Terms] AND \"adolescent\"[MeSH Terms] AND 2015:2026[dp]",
  "source": "pubmed",
  "count": 1284,
  "retrieved_ids": ["12345678", "23456789"],
  "retrieved_pmids": ["12345678", "23456789"],
  "retstart": 0,
  "retmax": 200,
  "executed_at": "2026-09-08T12:04:11Z",
  "hit_count_logged": true
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `query_id` | string | yes | Short stable id (`q1`..`qN`); matches the `search:query:<query_id>` task. |
| `query_string` | string | yes | The query exactly as submitted, verbatim, unescaped. |
| `translated_query` | string \| null | yes | `QueryTranslation` from esearch. `null` for non-PubMed sources or when unavailable. |
| `source` | enum | yes | `pubmed` \| `europepmc` \| `web`. |
| `count` | int \| null | yes | Total hits reported by the source (esearch `<Count>`). `null` when the source reports no total (some web endpoints). |
| `retrieved_ids` | string[] | yes | Ids actually returned in this page, in source order. Canonical field for all sources. |
| `retrieved_pmids` | string[] | yes | PMIDs only, order-preserved subset of `retrieved_ids`. `[]` for sources without PMIDs. |
| `retstart` | int | yes | Zero-based offset of this page. |
| `retmax` | int | yes | Page size requested. |
| `executed_at` | string | yes | ISO-8601 UTC Z. |
| `hit_count_logged` | bool | yes | `true` once `count` has been written into the search-strategy log used by the report/PRISMA flow. A `false` value blocks the verifier check `C-SEARCH-LOG`. |

Multi-page queries write one file per page: `<query_id>.json` for `retstart=0`,
`<query_id>-p<n>.json` thereafter; `count`, `query_string`, `translated_query` are identical
across pages.

---
