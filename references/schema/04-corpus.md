# schema §4 — `corpus record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 4. `corpus record`

One line of `corpus.jsonl`. The central object — everything else hangs off it. Written and
deduped by `scripts/corpus.py`.

```json
{
  "schema_version": 1,
  "evidence_id": "pmid:12345678",
  "pmid": "12345678",
  "doi": "10.1000/example",
  "pmcid": "PMC1234567",
  "title": "Cognitive behavioral therapy for adolescent depression: a randomized trial",
  "journal": "J Example Med",
  "publication_date": "2024-06-15",
  "authors": ["Smith JA", "Doe R", "Muller K"],
  "article_types": ["Randomized Controlled Trial"],
  "mesh_terms": ["Depression", "Cognitive Behavioral Therapy", "Adolescent"],
  "keywords": ["adolescents", "remission"],
  "retraction_status": "none",
  "source": "pubmed",
  "is_preprint": false,
  "screening": { "decision": "include", "reason": "RCT in target population, primary outcome reported" },
  "fulltext": {
    "status": "fulltext",
    "source_tier": 1,
    "access_route": "pmc_mcp",
    "local_path": "assets/papers/pmid-12345678.pdf",
    "sha256": "9ab3c1...",
    "truncation_detected": false
  },
  "extraction_path": "workspace/extractions/pmid-12345678.json",
  "appraisal_path": "workspace/appraisals/pmid-12345678.json",
  "first_seen_query": "q3"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `evidence_id` | string | yes | Primary key, per rule S9. Unique across `corpus.jsonl`. |
| `pmid` | string \| null | yes | PubMed id, digits as a **string** (never int — leading-zero and size safety). |
| `doi` | string \| null | yes | Lowercased DOI without `https://doi.org/` prefix. |
| `pmcid` | string \| null | yes | `PMC` + digits. |
| `title` | string | yes | Article title, unescaped, no trailing period normalization. |
| `journal` | string \| null | yes | ISO abbreviation preferred; full title if no abbreviation. |
| `publication_date` | string \| null | yes | `YYYY-MM-DD`, degrading to `YYYY-MM` / `YYYY`. |
| `authors` | string[] | yes | `"Family GI"` form, source order preserved. `[]` if none supplied. |
| `article_types` | string[] | yes | PubMed publication types verbatim. |
| `mesh_terms` | string[] | yes | MeSH descriptors verbatim. `[]` when not yet indexed. |
| `keywords` | string[] | yes | Author keywords. |
| `retraction_status` | enum | yes | `none` \| `retracted` \| `expression_of_concern` \| `corrected`. Set at screening (`SKILL.md` "Invariants"). |
| `source` | enum | yes | `pubmed` \| `europepmc` \| `preprint` \| `guideline` \| `web`. |
| `is_preprint` | bool | yes | `true` for bioRxiv/medRxiv/Research Square and any rung-6 preprint twin. Must be surfaced loudly in the report. |
| `screening` | object \| null | yes | `{decision, reason}` — the **final** decision (post-adjudication where dual screening ran). `decision` enum: `include` \| `exclude` \| `unclear`. `null` before screening. |
| `fulltext` | object | yes | See sub-table. |
| `extraction_path` | string \| null | yes | Run-relative path to the extraction record; `null` until stage 5 completes. |
| `appraisal_path` | string \| null | yes | Run-relative path to the appraisal record; `null` until stage 6 completes. |
| `first_seen_query` | string \| null | yes | `query_id` that first surfaced this record; preserved through dedupe (first writer wins). |

### `fulltext` sub-object

| Field | Type | Req | Meaning |
|---|---|---|---|
| `status` | enum | yes | `fulltext` \| `abstract_only` \| `missing`. A truncation-detected HTML route is `abstract_only`, never `fulltext` (`references/acquisition.md` rung 5). |
| `source_tier` | int \| null | yes | `0`–`7`, the acquisition-ladder rung that produced the text: 0 local library, 1 PMC MCP full text, 2 PMC PDF, 3 Europe PMC fullTextXML, 4 Unpaywall location, 5 OA PDF/HTML fetch, 6 preprint twin, 7 quarantined. `null` before stage 4. |
| `access_route` | string \| null | yes | Short machine token for the concrete route, e.g. `library`, `pmc_mcp`, `pmc_pdf`, `epmc_xml`, `unpaywall_pdf`, `oa_html`, `preprint_twin`, `inbox_manual`, `quarantine`. |
| `local_path` | string \| null | yes | Wiki-root-relative path of the stored PDF/text, e.g. `assets/papers/pmid-12345678.pdf`. `null` when nothing was stored. |
| `sha256` | string \| null | yes | Hex sha256 of the stored file; the library dedupe key. |
| `truncation_detected` | bool | yes | `true` when the HTML truncation detector fired (body <1500 words or paywall markers). Forces `status: abstract_only`. |

Invariant: `status == "missing"` ⇒ `source_tier == 7` and the record appears in `missing.md`.
Invariant: `truncation_detected == true` ⇒ `status == "abstract_only"`.

---
