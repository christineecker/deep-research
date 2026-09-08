# schema.md — JSON contracts

Authoritative data contracts for every deep-research script and subagent. Anything written to
`workspace/`, `outputs/`, `corpus.jsonl`, or `taskboard.jsonl` conforms to a record below.
Source of truth for design decisions: `PLAN.md` §5, §6, §7.

## 0. Shared rules

| Rule | Statement |
|---|---|
| S1 | Every record carries `schema_version: 1` (integer). Readers reject records without it. |
| S2 | All timestamps are ISO-8601 UTC with a literal `Z`: `2026-09-08T14:03:00Z`. No local time, no offsets. Dates without a time (e.g. `publication_date`) are `YYYY-MM-DD`; partial PubMed dates degrade to `YYYY-MM` or `YYYY`. |
| S3 | Unknown values are `null` (or `[]` for lists), never invented, never guessed, never `"N/A"`, never `""`. A field that PubMed did not supply is `null`. |
| S4 | Subagents NEVER return paper text, abstracts, quotes, or the full result JSON to the coordinator. They write their result file themselves and return exactly one `receipt` object (§1). |
| S5 | Malformed subagent JSON (unparseable, missing required field, enum violation) is retried **once** with the schema error text appended to the task prompt; a second failure marks the task `failed` and then `blocked`. `PLAN.md` §5 "Failure semantics". |
| S6 | Enums are closed. An unrecognized enum value is a schema error, not a passthrough. |
| S7 | All `*_path` fields are POSIX paths **relative to the run directory** (`<wiki>/outputs/deep-research/<slug>/`), except `sources[].resource` in OKF frontmatter and `local_path` for the shared PDF library, which are relative to the wiki root. No absolute paths are stored. |
| S8 | Files under `workspace/` are one JSON object per file, pretty-printed. `corpus.jsonl` and `taskboard.jsonl` are JSON Lines: one compact object per line, no trailing commas, append-only. |
| S9 | `evidence_id` is the primary evidence key: `pmid:<pmid>` when a PMID exists, else `doi:<doi>`, else `pmcid:<pmcid>`, else `url:<sha256-of-url-first16>`. Stable across resumes. |
| S10 | Records are immutable once their task is `completed`, unless the task's `inputs_hash` changes (`PLAN.md` §5). |

---

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

## 2. `taskboard record`

One line of `taskboard.jsonl`. Written **only** by `corpus.py task claim|complete|fail|block|list`
(`PLAN.md` §5). The coordinator never edits this file by hand.

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
| `retraction_status` | enum | yes | `none` \| `retracted` \| `expression_of_concern` \| `corrected`. Set at screening (`PLAN.md` §6). |
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
| `status` | enum | yes | `fulltext` \| `abstract_only` \| `missing`. A truncation-detected HTML route is `abstract_only`, never `fulltext` (`PLAN.md` §5 rung 5). |
| `source_tier` | int \| null | yes | `0`–`7`, the acquisition-ladder rung that produced the text: 0 local library, 1 PMC MCP full text, 2 PMC PDF, 3 Europe PMC fullTextXML, 4 Unpaywall location, 5 OA PDF/HTML fetch, 6 preprint twin, 7 quarantined. `null` before stage 4. |
| `access_route` | string \| null | yes | Short machine token for the concrete route, e.g. `library`, `pmc_mcp`, `pmc_pdf`, `epmc_xml`, `unpaywall_pdf`, `oa_html`, `preprint_twin`, `inbox_manual`, `quarantine`. |
| `local_path` | string \| null | yes | Wiki-root-relative path of the stored PDF/text, e.g. `assets/papers/pmid-12345678.pdf`. `null` when nothing was stored. |
| `sha256` | string \| null | yes | Hex sha256 of the stored file; the library dedupe key. |
| `truncation_detected` | bool | yes | `true` when the HTML truncation detector fired (body <1500 words or paywall markers). Forces `status: abstract_only`. |

Invariant: `status == "missing"` ⇒ `source_tier == 7` and the record appears in `missing.md`.
Invariant: `truncation_detected == true` ⇒ `status == "abstract_only"`.

---

## 5. `screening verdict`

`workspace/screening/<screener>/pmid-<pmid>.json`, where `<screener>` is `screener-a`,
`screener-b`, or `screener` (single-screen profiles).

```json
{
  "schema_version": 1,
  "pmid": "12345678",
  "decision": "exclude",
  "reason": "Adult sample (mean age 41); protocol requires 6-18y.",
  "criterion_failed": "E2",
  "retraction_flag": "none",
  "screener_id": "screener-a",
  "confidence": 0.92
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `pmid` | string | yes | PMID as string. For non-PMID records use `evidence_id` in this slot and name the file `<evidence_id-slug>.json`. |
| `decision` | enum | yes | `include` \| `exclude` \| `unclear`. `unclear` is legitimate and must not be coerced. |
| `reason` | string | yes | One or two sentences, ≤300 chars, referencing what in the title/abstract drove the call. No paper text quoted beyond a short phrase. |
| `criterion_failed` | string \| null | yes | Id of the protocol criterion that failed (e.g. `I1`, `E2`), drawn from `protocol.md`'s criteria ids. `null` for `include`, and `null` for `unclear` when no single criterion is decisive. Must resolve to a criterion id that exists in the protocol. |
| `retraction_flag` | enum | yes | `none` \| `retracted` \| `expression_of_concern` \| `corrected`. Propagates into the corpus record. |
| `screener_id` | string | yes | `screener-a` \| `screener-b` \| `screener`. Matches the directory segment. |
| `confidence` | float \| null | yes | `0.0`–`1.0` self-reported. `null` permitted; never used to override `unclear`. |

---

## 6. `adjudication record`

`workspace/screening/adjudication/pmid-<pmid>.json`. Written only when dual screening ran
(`systematic`, `max`) and the two screeners disagree.

```json
{
  "schema_version": 1,
  "pmid": "12345678",
  "screener_a_decision": "include",
  "screener_b_decision": "exclude",
  "final_decision": "include",
  "rationale": "B applied E2 to the pilot subsample; full sample age range 8-17 meets I1.",
  "adjudicator_id": "adjudicator-1"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `pmid` | string | yes | PMID as string (or `evidence_id` for non-PMID records). |
| `screener_a_decision` | enum | yes | `include` \| `exclude` \| `unclear` — verbatim from screener-a's verdict. |
| `screener_b_decision` | enum | yes | Same enum, from screener-b. |
| `final_decision` | enum | yes | `include` \| `exclude` \| `unclear`. This is what lands in the corpus record's `screening.decision`. |
| `rationale` | string | yes | ≤400 chars. Must state which criterion resolved the disagreement. |
| `adjudicator_id` | string | yes | Logical adjudicator worker id. |

The disagreement rate (adjudication records ÷ dual-screened records) is logged into the
PRISMA-style screening log (`PLAN.md` §1, §5).

---

## 7. `extraction record`

`workspace/extractions/pmid-<pmid>.json`. Stage 5, one subagent per paper (`PLAN.md` §5).

```json
{
  "schema_version": 1,
  "pmid": "12345678",
  "evidence_id": "pmid:12345678",
  "design": "parallel-group randomized controlled trial",
  "n_total": 240,
  "n_arms": [120, 120],
  "population": "Adolescents 12-17y with moderate MDD, outpatient, Germany, 62% female",
  "intervention": "Manualized group CBT, 12 weekly 90-min sessions",
  "comparator": "Waitlist control with treatment as usual",
  "outcomes": [
    {
      "name": "CDI-2 total score",
      "timepoint": "12 weeks",
      "effect_measure": "SMD",
      "effect": -0.41,
      "ci_low": -0.68,
      "ci_high": -0.14,
      "p_value": 0.003,
      "direction": "favors_intervention"
    },
    {
      "name": "Remission (CDRS-R <= 28)",
      "timepoint": "24 weeks",
      "effect_measure": "RR",
      "effect": 1.12,
      "ci_low": 0.88,
      "ci_high": 1.43,
      "p_value": 0.36,
      "direction": "null_effect"
    }
  ],
  "funding": "German Research Foundation, grant EX-1234",
  "coi": "Two authors report speaker fees from Example Pharma; others none declared.",
  "limitations": "No active comparator; 18% attrition at 24 weeks, complete-case analysis.",
  "evidence_basis": "fulltext",
  "extractor_notes": "24-week remission reported only in Table 3; text states 'no difference'.",
  "quotes": [
    {
      "text": "The intervention group showed a significant reduction in CDI-2 scores at 12 weeks.",
      "section": "Results",
      "page": 7
    }
  ]
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `pmid` | string \| null | yes | PMID as string; `null` for non-PubMed evidence. |
| `evidence_id` | string | yes | Links back to the corpus record. |
| `design` | string \| null | yes | Study design in the paper's own terms; drives appraisal-tool selection (`references/appraisal.md`). |
| `n_total` | int \| null | yes | Analyzed total N. `null` if not stated — never estimated. |
| `n_arms` | int[] | yes | Per-arm N in the paper's arm order. `[]` if not applicable/stated. |
| `population` | string \| null | yes | Who, where, key baseline characteristics. |
| `intervention` | string \| null | yes | What was delivered, dose/duration/format. |
| `comparator` | string \| null | yes | Control condition. `null` for single-arm designs. |
| `outcomes` | object[] | yes | One entry per reported outcome-timepoint pair. `[]` only when the paper reports no extractable outcome. |
| `funding` | string \| null | yes | Funders and grant ids as stated. `null` = not stated (distinct from "none"). |
| `coi` | string \| null | yes | COI statement as stated. |
| `limitations` | string \| null | yes | Limitations stated by the authors **plus** extractor-observed ones, marked as such. |
| `evidence_basis` | enum | yes | `fulltext` \| `abstract_only`. Must equal the corpus `fulltext.status` mapped (`missing` never reaches extraction). Abstract-only extractions may not be appraised as if full (`PLAN.md` §6). |
| `extractor_notes` | string \| null | yes | Ambiguities, discrepancies between text and tables, unit conversions performed. |
| `quotes` | object[] | yes | Verbatim anchors supporting the extraction. `[]` for `abstract_only` records is acceptable. |

### `outcomes[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `name` | string | yes | Outcome as named in the paper, plus instrument. |
| `timepoint` | string \| null | yes | e.g. `12 weeks`, `post-treatment`, `end of follow-up`. |
| `effect_measure` | string \| null | yes | `SMD`, `MD`, `RR`, `OR`, `HR`, `RD`, `beta`, `r`, … as reported. |
| `effect` | number \| null | yes | Point estimate in the reported measure's units. |
| `ci_low` | number \| null | yes | Lower bound of the reported interval (95% unless `extractor_notes` says otherwise). |
| `ci_high` | number \| null | yes | Upper bound. |
| `p_value` | number \| null | yes | Numeric p. Reported thresholds (`p<0.001`) go in `extractor_notes`, with `p_value: null`. |
| `direction` | enum | yes | `favors_intervention` \| `favors_comparator` \| `null_effect` \| `unclear`. `null_effect` = CI crosses the null / explicitly no difference. `unclear` when direction cannot be determined from what is reported. |

### `quotes[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `text` | string | yes | Verbatim, ≤300 chars. |
| `section` | string \| null | yes | Anchor: `Abstract`, `Methods`, `Results`, `Table 3`, `Discussion`. |
| `page` | int \| null | yes | Page number when the source was a PDF; `null` for XML/HTML routes. |

---

## 8. `appraisal record`

`workspace/appraisals/pmid-<pmid>.json`. Stage 6, one subagent per paper.

```json
{
  "schema_version": 1,
  "pmid": "12345678",
  "evidence_id": "pmid:12345678",
  "tool": "RoB2",
  "domains": [
    { "domain": "Randomization process", "judgement": "low", "rationale": "Computer-generated sequence, central allocation described." },
    { "domain": "Deviations from intended interventions", "judgement": "some_concerns", "rationale": "Open-label; ITT analysis reported but adherence not described." },
    { "domain": "Missing outcome data", "judgement": "high", "rationale": "18% attrition, complete-case analysis, no sensitivity analysis." }
  ],
  "overall_judgement": "high",
  "grade": {
    "risk_of_bias": "serious",
    "inconsistency": "not_serious",
    "indirectness": "not_serious",
    "imprecision": "serious",
    "publication_bias": "undetected",
    "certainty": "low"
  },
  "evidence_basis": "fulltext"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `pmid` | string \| null | yes | PMID as string; `null` for non-PubMed evidence. |
| `evidence_id` | string | yes | Links back to the corpus record. |
| `tool` | enum | yes | `RoB2` \| `ROBINS-I` \| `Newcastle-Ottawa` \| `AMSTAR-2` \| `none`. `none` = no in-scope instrument applies (narrative review, guideline, editorial, abstract-only record, cross-sectional/diagnostic/qualitative/animal/modelling design). |
| `domains` | object[] | yes | Tool-specific domains in the tool's canonical order. **Exactly `[]` when `tool == "none"`** — the reason then lives in `overall_judgement` and is recorded as a pipeline limitation, not a quality verdict. See `references/appraisal.md`. |
| `overall_judgement` | string | yes | Tool-appropriate overall rating: RoB2/ROBINS-I `low` \| `some_concerns` \| `moderate` \| `serious` \| `critical` \| `high` \| `unclear`; NOS a star count string (`"7/9"`); AMSTAR-2 `high` \| `moderate` \| `low` \| `critically_low`. |
| `grade` | object \| null | yes | GRADE domains for the body of evidence this study contributes to. `null` when GRADE is applied only at outcome level elsewhere. |
| `evidence_basis` | enum | yes | `fulltext` \| `abstract_only`. An `abstract_only` appraisal must set every domain not assessable from an abstract to `unclear` with rationale `"not assessable from abstract"`. |

### `domains[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `domain` | string | yes | Domain name as defined by the tool. |
| `judgement` | string | yes | `low` \| `some_concerns` \| `moderate` \| `serious` \| `critical` \| `high` \| `unclear` \| `yes` \| `no` \| `partial_yes` (AMSTAR-2 items) — the tool's own vocabulary; must be one of these tokens. |
| `rationale` | string | yes | ≤300 chars, states the evidence for the judgement. Never empty; `unclear` still needs a reason. |

### `grade` sub-object

| Field | Type | Req | Meaning |
|---|---|---|---|
| `risk_of_bias` | enum | yes | `not_serious` \| `serious` \| `very_serious`. |
| `inconsistency` | enum | yes | `not_serious` \| `serious` \| `very_serious`. |
| `indirectness` | enum | yes | `not_serious` \| `serious` \| `very_serious`. |
| `imprecision` | enum | yes | `not_serious` \| `serious` \| `very_serious`. |
| `publication_bias` | enum | yes | `undetected` \| `suspected` \| `strongly_suspected`. |
| `certainty` | enum | yes | `high` \| `moderate` \| `low` \| `very_low`. |

---

## 9. `verifier result`

`outputs/verification.json`, written by `scripts/verify.py` (stage 8).

```json
{
  "schema_version": 1,
  "checks": [
    { "check_id": "C-CITE-RESOLVE", "status": "pass", "detail": "48/48 report citations resolve to corpus evidence_ids." },
    { "check_id": "C-FULLTEXT", "status": "warn", "detail": "3 included studies are abstract_only." },
    { "check_id": "C-HYPOTHESIS-WALL", "status": "fail", "detail": "2 hypotheses phrased as established findings." }
  ],
  "unsupported_claims": [
    { "location": "report.md:L142", "claim": "CBT halves relapse risk at two years.", "reason": "No corpus outcome reports 24-month relapse." }
  ],
  "uncited_citations": ["pmid:34567890"],
  "missing_fulltext": ["pmid:45678901", "doi:10-1000-paywalled"],
  "abstract_only_claims": [
    { "location": "report.md:L88", "evidence_id": "pmid:23456789", "labelled": false }
  ],
  "okf_validation": "pass"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `checks` | object[] | yes | Every check run, in execution order. |
| `checks[].check_id` | string | yes | Stable id, `C-` prefix, SCREAMING-KEBAB. e.g. `C-CITE-RESOLVE`, `C-CORPUS-COMPLETE`, `C-SEARCH-LOG`, `C-RETRACTION`, `C-FULLTEXT`, `C-HYPOTHESIS-WALL`, `C-OKF`. |
| `checks[].status` | enum | yes | `pass` \| `fail` \| `warn`. Any `fail` makes the report provisional and blocks OKF promotion. |
| `checks[].detail` | string | yes | One line, machine-greppable, with counts. |
| `unsupported_claims` | object[] | yes | Claims in `report.md` with no backing corpus/extraction record. Each `{location, claim, reason}`. `[]` = clean. |
| `uncited_citations` | string[] | yes | `evidence_id`s present in `corpus.jsonl` as included but never cited in the report. |
| `missing_fulltext` | string[] | yes | `evidence_id`s with `fulltext.status == "missing"`; must match `missing.md`. |
| `abstract_only_claims` | object[] | yes | Report claims resting on `abstract_only` evidence. Each `{location, evidence_id, labelled}`; any `labelled: false` is a `C-FULLTEXT` fail (`PLAN.md` §6). |
| `okf_validation` | enum | yes | `pass` \| `fail` \| `skipped`. `skipped` when no wiki promotion was requested. `fail` keeps the report, blocks promotion, and writes `outputs/okf-validation.md` (`PLAN.md` §5). |

---

## Resolutions (contract addenda, 2026-09-08)

Ambiguities found while writing the subagent prompt blocks. These are binding.

| # | Question | Resolution |
|---|---|---|
| R1 | Receipts for batched screening tasks | One invocation = one `task_id` = **one** receipt. A batch task may cover many PMIDs and write many per-PMID files, but never returns a receipt array. Arrays are a schema error. |
| R2 | `output_path` for a batch task | For a `batch` key-kind task, `output_path` is the **directory** containing the per-item result files (e.g. `workspace/screening/screener-a/`). For every other key-kind it is a single file. |
| R3 | `adjudicator_id` allocation | Coordinator-supplied, same as `screener_id`. Convention: `adjudicator-01`, `adjudicator-02`, …; screeners are `screener-a` / `screener-b`. |
| R4 | GRADE `inconsistency` and `publication_bias` from a single-paper appraiser | These are body-level domains. The appraiser rates them **on the single-study view only** and says so in the rationale. **Stage 7 synthesis overrides both** with the across-study judgement. The synthesis value wins wherever the two differ. |
| R5 | `limitations` dual framing marker | One string, fixed convention: `Authors: <authors' own framing> \| Extractor: <extractor-observed>`. Either side may be empty but both labels are always present, so `verify.py` can grep them. |
| R6 | BibTeX citation-key derivation | Key = `evidence_id` with all non-alphanumeric characters stripped: `pmid:12345678` → `pmid12345678`, `doi:10.1000/ex-1` → `doi101000ex1`. `scripts/render.py` MUST emit exactly this; `report.qmd` cites `[@pmid12345678]`. Report footnote keys remain the `sources[].id` values, which are a separate namespace. |
| R7 | Provenance across a dedupe merge | `first_seen_query` (string) is joined by two fields on the corpus record, both defaulting to `[]`: `seen_in_queries: string[]` (union of every query id that surfaced this study) and `merged_from: string[]` (the `evidence_id`s absorbed by the merge). `first_seen_query` keeps the earliest. `corpus.py export --strict-schema` strips both. |
| R8 | `source_tier` before retrieval | The invariant `fulltext.status == "missing" ⇒ source_tier == 7` applies **only once `access_route` is set**, i.e. once stage 4 has actually attempted retrieval. A freshly added record is `status: missing` with `source_tier: null`; PRISMA counts it as `retrieval_not_yet_attempted`, never as `fulltext_unobtainable`. |
| R9 | Bibliographic extras on the corpus record | `volume`, `issue`, `pages`, `issn`, `epub_date`, `abstract` and `grants[]` are **optional** corpus-record fields. `eutils.py efetch` supplies them when PubMed has them; `render.py` and `okf.py` emit them only when present and never fetch or infer them. All of `volume`, `issue`, `pages`, `issn` are strings (R-per-V13), never numbers. |
