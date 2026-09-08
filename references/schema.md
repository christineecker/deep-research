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
      "direction": "null_effect",
      "spans": [
        { "claim": "24-week remission RR 1.12 (0.88-1.43), no group difference.", "evidence_id": "pmid:12345678", "source_id": "src-3f9a1c...", "start": 41022, "end": 41180, "access": "full_text" }
      ]
    }
  ],
  "spans": [
    { "claim": "Parallel-group randomized controlled trial, 240 adolescents randomized 1:1.", "evidence_id": "pmid:12345678", "source_id": "src-3f9a1c...", "start": 8104, "end": 8266, "access": "full_text" }
  ],
  "funding": "German Research Foundation, grant EX-1234",
  "coi": "Two authors report speaker fees from Example Pharma; others none declared.",
  "limitations": "No active comparator; 18% attrition at 24 weeks, complete-case analysis.",
  "evidence_basis": "fulltext",
  "extractor_notes": "24-week remission reported only in Table 3; text states 'no difference'.",
  "quotes": []
}
```

`quotes` above is `[]` because the **assembler** fills it. A record that arrives from a subagent
with a non-empty `quotes[]` is not a schema error, but the agent-written text is discarded and
re-derived from the snapshot (R17).

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
| `spans` | object[] | yes | Record-level claim spans (§12) backing the narrative factual fields (`design`, `n_total`, `n_arms`, `population`, `intervention`, `comparator`, `funding`, `coi`, `limitations`). One or more entries per field that makes a factual claim about the study; each entry's `claim` names the field it backs (R19). `[]` is legal only when every one of those fields is `null`. |
| `quotes` | object[] | yes | **DERIVED — not agent-authored.** Written by `scripts/assemble.py` by re-slicing `snapshot.text[start:end]` for each span in this record. A subagent MUST emit `quotes: []`; anything it writes here is discarded (R17). `[]` survives into `result.json` only for records with no spans, which are `unverified` (R16). |

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
| `spans` | object[] | yes | Claim spans (§12) backing this effect estimate. **Every outcome entry with a non-null `effect`, `ci_low`, `ci_high` or `p_value` requires at least one span.** An outcome whose numbers are all `null` may have `spans: []`. An estimate without a span cannot pass the gate (R16). |

### `quotes[]` entry — DERIVED

Produced by the assembler, never by an agent. One entry per span in the record, in span order.

| Field | Type | Req | Meaning |
|---|---|---|---|
| `text` | string | yes | `snapshot.text[start:end]`, verbatim, exactly as re-sliced. Not truncated to 300 chars — the 2000-char span cap (§12) is the only limit. |
| `section` | string \| null | yes | Anchor: `Abstract`, `Methods`, `Results`, `Table 3`, `Discussion`. Derived from the snapshot's section map when it has one, else `null`. Never guessed. |
| `page` | int \| null | yes | Page number when the snapshot came from a PDF and the extractor recorded page boundaries; `null` for XML/HTML routes. Never guessed. |
| `source_id` | string | yes | The snapshot the text was sliced from. |
| `start` / `end` | int | yes | Copied from the span; `end` exclusive. |

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
    {
      "domain": "Randomization process",
      "judgement": "low",
      "rationale": "Computer-generated sequence, central allocation described.",
      "spans": [
        { "claim": "Computer-generated randomisation sequence with central allocation.", "evidence_id": "pmid:12345678", "source_id": "src-3f9a1c...", "start": 9040, "end": 9188, "access": "full_text" }
      ]
    },
    {
      "domain": "Deviations from intended interventions",
      "judgement": "some_concerns",
      "rationale": "Open-label; ITT analysis reported but adherence not described.",
      "spans": [
        { "claim": "Open-label design; analysis by intention to treat.", "evidence_id": "pmid:12345678", "source_id": "src-3f9a1c...", "start": 9402, "end": 9510, "access": "full_text" }
      ]
    },
    {
      "domain": "Selection of the reported result",
      "judgement": "unclear",
      "rationale": "No information: no protocol or registration cited.",
      "spans": []
    }
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
| `spans` | object[] | yes | Claim spans (§12) locating, in the snapshot, the reported method the judgement rests on. **Required (≥1) for every judgement other than `unclear`.** `unclear` on the grounds of absent reporting takes `spans: []` — there is nothing to point at, and that is the honest record (R19). A non-`unclear` judgement with `spans: []` is `unverified` (R16). |

`quotes` is not a field of the appraisal record. The excerpts backing an appraisal are derived by
the assembler from `domains[].spans[]` into `result.json`, never written into the appraisal file.

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
| `checks[].check_id` | string | yes | Stable id, `C-` prefix, SCREAMING-KEBAB. e.g. `C-CITE-RESOLVE`, `C-CORPUS-COMPLETE`, `C-SEARCH-LOG`, `C-RETRACTION`, `C-FULLTEXT`, `C-HYPOTHESIS-WALL`, `C-OKF`, plus the evidence-kernel checks below. |
| `checks[].status` | enum | yes | `pass` \| `fail` \| `warn`. Any `fail` makes the report provisional and blocks OKF promotion. |
| `checks[].detail` | string | yes | One line, machine-greppable, with counts. |
| `unsupported_claims` | object[] | yes | Claims in `report.md` with no backing corpus/extraction record. Each `{location, claim, reason}`. `[]` = clean. |
| `uncited_citations` | string[] | yes | `evidence_id`s present in `corpus.jsonl` as included but never cited in the report. |
| `missing_fulltext` | string[] | yes | `evidence_id`s with `fulltext.status == "missing"`; must match `missing.md`. |
| `abstract_only_claims` | object[] | yes | Report claims resting on `abstract_only` evidence. Each `{location, evidence_id, labelled}`; any `labelled: false` is a `C-FULLTEXT` fail (`PLAN.md` §6). |
| `okf_validation` | enum | yes | `pass` \| `fail` \| `skipped`. `skipped` when no wiki promotion was requested. `fail` keeps the report, blocks promotion, and writes `outputs/okf-validation.md` (`PLAN.md` §5). |

### Evidence-kernel checks

`verify.py` runs these in addition to the checks above, reading `sources/`, `events.jsonl` and
`outputs/result.json`. They are **always executed and always reported**; whether a violation is
`fail` or `warn` depends on the gate flag (`--gate` / `config.json` `gates.evidence_kernel`,
default **off** — R20). While the gate is off no evidence-kernel check may block Stage 8 except
the tamper checks, which are never downgraded.

| `check_id` | Asserts | Gate off | Gate on |
|---|---|---|---|
| `C-SNAPSHOT` | Every `source_id` referenced by any span exists at `sources/src-<sha256>.json`, parses, and its recomputed `content_hash` and recomputed `source_id` equal the stored values. | `fail` on a missing, unparseable, or hash-mismatched snapshot (tampering is never a warning). `warn` — detail `no snapshot store` — when the run contains no `sources/` directory at all (pre-kernel run). | `fail`; a run with no snapshot store is also `fail`. |
| `C-SPAN` | For every span: `0 <= start < end <= len(snapshot.text)`, `end - start <= 2000`, and the derived excerpt equals `snapshot.text[start:end]` character-for-character. | `fail` on any span that exists but is out of range, over-length, or whose excerpt mismatches. `warn` when a record that should carry spans carries none (`unverified`, R16). | `fail` in both cases. |
| `C-FRESH-FETCH` | Every `source_id` backing a claim in an artifact the verifier would call `supported` has a fresh retrieval event in `events.jsonl` per §11's freshness rule, or a `local_pdf` event with a matching asset hash. | `warn` — the stale sources are listed in `detail` and the affected claims are downgraded to `unverified`, but the report is not blocked. | `fail`. |
| `C-ASSEMBLER` | `outputs/result.json` exists, is schema-valid, and lists the artifact in `accepted[]` rather than `diagnostics.unresolved[]`. | `warn`, including when the assembler was never run (detail `result.json absent`). | `fail`. |

A `C-SNAPSHOT` or `C-SPAN` `fail` blocks OKF promotion regardless of the gate flag: the publisher
preflight (`okf.py promote --check`, `references/evidence-kernel.md`) repeats both checks and
fails closed. `outputs/report.md` is still written and kept.

---

## 10. `snapshot record`

`<run>/sources/src-<sha256>.json`. One file per immutable source snapshot, written by
`scripts/source.py`. The evidence-proof kernel's ground truth: **the only text in the run that
counts as evidence.** Narrative reference: `references/evidence-kernel.md`.

```json
{
  "schema_version": 1,
  "source_id": "src-3f9a1cb84d02e77a5c1b0f9e2d6a4413c8b7e05f9a2d1c3e4b5a6978d0e1f2a3",
  "content_hash": "sha256:b7e05f9a2d1c3e4b5a6978d0e1f2a33f9a1cb84d02e77a5c1b0f9e2d6a4413c8",
  "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/",
  "title": "Cognitive behavioral therapy for adolescent depression: a randomized trial",
  "retrieved_at": "2026-09-08T12:07:44Z",
  "access": "full_text",
  "paper": { "pmid": "12345678", "doi": "10.1000/example", "pmcid": "PMC1234567" },
  "origin": "pmc",
  "asset": { "path": "assets/papers/pmid-12345678.pdf", "sha256": "9ab3c1...", "bytes": 1842991 },
  "text": "Cognitive behavioral therapy for adolescent depression\n\nAbstract\nBackground. ..."
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `source_id` | string | yes | `"src-"` + sha256 over the UTF-8 URL, one NUL byte, and the UTF-8 text; lowercase hex, 64 chars after the prefix. See "Source identity" below. Equals the file's basename without `.json`. |
| `content_hash` | string | yes | `"sha256:"` + sha256 of `text` **alone**, UTF-8 encoded, lowercase hex. Detects tampering with the body independently of the URL. |
| `url` | string | yes | The exact URL retrieved, byte-for-byte as requested — no normalization, no trailing-slash fixing. A redirect that changed the URL produces a snapshot for the final URL. For `user-supplied-pdf` this is the `file://` URL of the library path at ingest time. It participates in `source_id`, so it can never be edited. |
| `title` | string \| null | yes | Title as stated by the source (PubMed `ArticleTitle`, PMC title, HTML `<title>`, PDF metadata). `null` when the source states none. Never inferred. |
| `retrieved_at` | string | yes | ISO-8601 UTC Z (S2) at which the bytes were obtained. |
| `access` | enum | yes | `full_text` \| `abstract` \| `preprint` \| `guideline` \| `web`. What kind of evidence this text can support. Closed enum (S6). Mapping to `fulltext.status` in R21. |
| `paper` | object \| null | yes | `{pmid, doi, pmcid}`, each `string \| null`. The whole object is `null` only for sources with no bibliographic identity at all, i.e. `origin: web`. A literature claim must resolve to at least one non-null member (§13 `NO_PAPER_ID`). |
| `origin` | enum | yes | `pubmed` \| `pmc` \| `europepmc` \| `unpaywall` \| `oa-pdf` \| `user-supplied-pdf` \| `web`. The concrete acquisition channel. Closed enum. |
| `asset` | object \| null | yes | `{path, sha256, bytes}` for a snapshot derived from a stored file, else `null`. `path` is **wiki-root-relative** (`assets/papers/pmid-12345678.pdf`) — an explicit exception to S7, because the PDF library is shared across runs and PDFs are never duplicated per run (R13). `sha256` is lowercase hex of the file's bytes (no `sha256:` prefix, matching `corpus.fulltext.sha256`). `bytes` is the file size as an integer. |
| `text` | string | yes | The extracted plain text, decoded UTF-8. This is what spans (§12) index into. Never truncated to fit; never re-flowed, re-wrapped, normalized, or edited after writing. May be `""` only if the source genuinely yielded no text, in which case no span can reference it. |

### Source identity

```text
source_id = "src-" + sha256_hex( utf8(url) || NUL || utf8(text) )
```

- Both `url` and `text` are encoded as **UTF-8**; the separator is a **single literal NUL byte**
  (`0x00`, Python `b"\x00"`), not the two-character sequence backslash-zero.
- The digest is lowercase hexadecimal, 64 characters. `source_id` is the whole 68-character
  string including the `src-` prefix; the filename is `src-<64 hex>.json` (R11).
- `content_hash` covers `text` only and carries the `sha256:` prefix, matching `inputs_hash` in §2.

### Immutability and integrity

- A snapshot file is written **once**, with exclusive creation (`O_EXCL`). Rewriting an existing
  `src-*.json` is an error, not an overwrite — identical content produces the identical path, so
  a re-fetch of unchanged text is a no-op.
- **Every read** of a snapshot (by `source.py read`, `source.py spans`, `assemble.py`,
  `verify.py`, `okf.py promote`) recomputes `content_hash` and `source_id` from the file's own
  `url` and `text` and compares them to the stored values. A mismatch is a hard error: the
  snapshot is treated as tampered, no span resolves against it, and `C-SNAPSHOT` fails.
- Because `text` participates in `source_id`, corrected or re-fetched text is a **new** source,
  never an amendment. Offsets therefore never drift under a claim (R12).

---

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
(`VALIDATION_ARCHITECTURE_PLAN.md` Non-Goals). A `fetch` served entirely from an HTTP cache or
from an on-disk cache older than the run writes `fresh: false` and does not satisfy the rule.

**User-supplied-PDF exception.** For `origin: "user-supplied-pdf"` there is nothing to re-fetch:
the immutable, hash-checked local file *is* the source. Such a source is fresh iff a `local_pdf`
event exists for it in this run **and** the file at `snapshot.asset.path` (resolved against the
wiki root) still hashes to `snapshot.asset.sha256`. A missing file, a changed file, or a
`local_pdf` event on a snapshot with `asset: null` fails the rule
(`VALIDATION_ARCHITECTURE_PLAN.md` Phase 3).

---

## 12. `claim span record`

The atom of the kernel. Every material claim in the report, in an extraction, in an appraisal,
and in a promoted OKF concept resolves to one or more of these.

```json
{
  "claim": "The trial reported lower exacerbation rates in the intervention arm.",
  "evidence_id": "pmid:12345678",
  "source_id": "src-3f9a1cb84d02e77a5c1b0f9e2d6a4413c8b7e05f9a2d1c3e4b5a6978d0e1f2a3",
  "start": 10422,
  "end": 10610,
  "access": "full_text"
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `claim` | string | yes | The proposition the span supports, in the record author's own words, ≤300 chars, single line. It names what is being asserted; it is **not** a transcription of the source and is never used as evidence text. |
| `evidence_id` | string | yes | The corpus record this claim attaches to (S9). Must exist in `corpus.jsonl`. |
| `source_id` | string | yes | Snapshot the offsets index into. Must exist under `<run>/sources/` and pass integrity (§10). Must be one of the snapshots registered for `evidence_id` (R14). |
| `start` | int | yes | Character offset of the first supporting character, **inclusive**, `>= 0`. |
| `end` | int | yes | Character offset one past the last supporting character, **EXCLUSIVE**. `start < end <= len(text)`. |
| `access` | enum | yes | `full_text` \| `abstract` \| `preprint` \| `guideline` \| `web`. Must equal the snapshot's `access` — it is copied, not chosen. A mismatch is a schema error. |

### Offset semantics (R10)

- `start` and `end` are **character offsets into the snapshot's decoded `text` string** —
  precisely, Python `str` indices, such that the supporting text is exactly `text[start:end]`.
- They are **NOT** byte offsets, **NOT** UTF-8 byte counts, **NOT** UTF-16 code units, and not
  offsets into any rendered, re-wrapped, or markdown-converted view of the source.
- Python `str` indices are Unicode code points. A combining sequence or an emoji ZWJ cluster may
  therefore span several indices; slicing mid-cluster is legal and produces exactly what the
  re-slice produces, so it can never cause a false mismatch.
- `text` is stored and compared without normalization — no NFC/NFD pass, no whitespace collapsing,
  no newline rewriting. Any implementation that normalizes on read breaks every offset in the run.

### Span rules

| # | Rule |
|---|---|
| P1 | `end` is exclusive. `text[start:end]` is the excerpt, always. |
| P2 | `end - start <= 2000` characters. A longer span is a hard failure, never a silent truncation. |
| P3 | Every material report claim resolves to **one or more** span records. Multiple spans are ORed evidence for one claim; they may come from different `source_id`s, may overlap, and are never concatenated or merged by the assembler (R18). Two disjoint 1500-character spans are the correct answer to a claim needing 3000 characters of support. |
| P4 | Literature claims must still trace to a PMID, DOI or PMCID: the span's `evidence_id` and the snapshot's `paper` object must each supply at least one identifier, and they must agree (R14). |
| P5 | Abstract-only claims remain allowed, but only when `access` is `abstract` **and** the claim is labelled abstract-level in the report (existing `C-FULLTEXT` rule, §9). |
| P6 | The excerpt is **never** authored, transcribed, paraphrased, or re-typed by an agent. It is re-sliced from the snapshot at assembly time and again at publish time. An agent-supplied excerpt that differs from the re-slice fails the claim (`EXCERPT_MISMATCH`). |

### Backward compatibility (R16)

An extraction or appraisal record written before this layer — no `spans[]` at all, or `spans: []`
where §7/§8 requires entries — is **not** a schema error and is never deleted. It is marked
`unverified`:

- it is listed in `diagnostics.unresolved[]` of `result.json` with `reason_code: "NO_SPANS"`;
- it can never enter `accepted[]`, so it can never pass the gate;
- with the gate off it still reaches the report, and `C-SPAN` reports `warn` naming it;
- with the gate on it blocks Stage 8 for that artifact;
- it may never back a promoted OKF concept's evidence footnote, gate or no gate.

---

## 13. `assembler result`

`<run>/outputs/result.json`, written by `scripts/assemble.py` (`VALIDATION_ARCHITECTURE_PLAN.md`
Phase 4). Reads `workspace/extractions/*.json`, `workspace/appraisals/*.json`,
`outputs/report.md`, `sources/*.json`, `events.jsonl` and `corpus.jsonl`; writes one object.

```json
{
  "schema_version": 1,
  "run_slug": "cbt-adolescent-depression",
  "generated_at": "2026-09-08T15:22:07Z",
  "gate": { "enabled": false, "verdict": "warn", "flag": "--gate" },
  "counts": { "artifacts_seen": 42, "accepted": 39, "unresolved": 3, "spans_checked": 214, "sources": 37 },
  "accepted": [
    {
      "artifact_id": "extraction:pmid:12345678",
      "kind": "extraction",
      "evidence_id": "pmid:12345678",
      "source_ids": ["src-3f9a1c..."],
      "paper": { "pmid": "12345678", "doi": "10.1000/example", "pmcid": "PMC1234567" },
      "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/",
      "access": "full_text",
      "fresh": true,
      "claims": [
        {
          "claim": "Primary outcome CDI-2 at 12 weeks, SMD -0.41 (-0.68 to -0.14).",
          "field": "outcomes[0]",
          "source_id": "src-3f9a1c...",
          "start": 10422,
          "end": 10610,
          "access": "full_text",
          "excerpt": "At 12 weeks the intervention group showed a greater reduction in CDI-2 total score than controls (SMD -0.41, 95% CI -0.68 to -0.14; p=0.003).",
          "section": "Results",
          "page": 7
        }
      ]
    }
  ],
  "diagnostics": {
    "unresolved": [
      { "artifact_id": "appraisal:pmid:45678901", "kind": "appraisal", "evidence_id": "pmid:45678901", "field": "domains[2]", "reason_code": "NO_FRESH_FETCH", "detail": "src-8c22ef... last retrieved in run 2026-08-30-asthma; no fetch event in this run." },
      { "artifact_id": "extraction:doi:10-1000-old", "kind": "extraction", "evidence_id": "doi:10.1000/old", "field": null, "reason_code": "NO_SPANS", "detail": "pre-kernel record, unverified." }
    ],
    "counts_by_reason": { "NO_FRESH_FETCH": 1, "NO_SPANS": 2 }
  },
  "sources": [
    { "source_id": "src-3f9a1c...", "content_hash": "sha256:b7e05f...", "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/", "title": "Cognitive behavioral therapy for adolescent depression: a randomized trial", "access": "full_text", "origin": "pmc", "paper": { "pmid": "12345678", "doi": "10.1000/example", "pmcid": "PMC1234567" }, "asset": null, "fresh": true, "fresh_event_id": "ev-0007" }
  ]
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `run_slug` | string | yes | Run directory name. |
| `generated_at` | string | yes | ISO-8601 UTC Z. |
| `gate` | object | yes | `{enabled: bool, verdict: "pass"\|"fail"\|"warn", flag: string}`. `enabled` reflects `--gate` / `config.json` `gates.evidence_kernel`; default `false` (R20). `verdict` is `pass` when `diagnostics.unresolved` is empty, else `fail` if `enabled` else `warn`. The verdict is **always** written and always reported to the user, whether or not it blocks. |
| `counts` | object | yes | Integer tallies: `artifacts_seen`, `accepted`, `unresolved`, `spans_checked`, `sources`. |
| `accepted` | object[] | yes | Artifacts that passed every check. `[]` is legal. |
| `diagnostics` | object | yes | `{unresolved: object[], counts_by_reason: object}`. Never empty-by-omission: an absent problem is an empty list, not a missing key. |
| `sources` | object[] | yes | Snapshot metadata for every `source_id` referenced anywhere, derived from `sources/*.json`, plus `fresh` and the `fresh_event_id` that established it (`null` when not fresh). |

### `accepted[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `artifact_id` | string | yes | `<kind>:<evidence_id>` for record artifacts (`extraction:pmid:12345678`), `report:<anchor>` for a report claim (`report:L142`). Unique within `accepted[]`. |
| `kind` | enum | yes | `extraction` \| `appraisal` \| `report_claim` \| `synthesis_claim`. Closed enum. |
| `evidence_id` | string | yes | Corpus key (S9). |
| `source_ids` | string[] | yes | Every distinct snapshot backing this artifact, in first-use order. |
| `paper` | object | yes | `{pmid, doi, pmcid}` **copied from the snapshot**, not from the agent record. |
| `url` | string | yes | Copied from the snapshot. |
| `access` | enum | yes | Copied from the snapshot; where several snapshots back one artifact, the **weakest** wins, ordered `web` < `abstract` < `preprint` < `guideline` < `full_text`. |
| `fresh` | bool | yes | `true` iff every `source_id` is fresh for this run (§11). |
| `claims` | object[] | yes | One entry per resolved span; at least one entry. |

### `accepted[].claims[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `claim` | string | yes | Copied verbatim from the span record's `claim`. The only agent-written string that survives into `result.json`. |
| `field` | string \| null | yes | Path to the field the span backs inside the source record: `outcomes[0]`, `domains[2]`, `limitations`; `null` for a report claim. |
| `source_id` | string | yes | Copied from the span record after validation. |
| `start` | int | yes | Copied from the span record after validation. |
| `end` | int | yes | Copied from the span record after validation; exclusive. |
| `access` | enum | yes | Copied from the snapshot, not from the span record. |
| `excerpt` | string | yes | **DERIVED.** `snapshot.text[start:end]`, re-sliced at assembly time. Never copied from an agent record. |
| `section` | string \| null | yes | Derived from the snapshot's section map when it has one, else `null`. Never guessed. |
| `page` | int \| null | yes | Derived from recorded PDF page boundaries when the snapshot has them, else `null`. Never guessed. |

### `diagnostics.unresolved[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `artifact_id` | string | yes | Same grammar as `accepted[].artifact_id`. |
| `kind` | enum | yes | Same closed enum. |
| `evidence_id` | string \| null | yes | `null` when the artifact names an `evidence_id` absent from `corpus.jsonl`. |
| `field` | string \| null | yes | The offending field, when the failure is localised. |
| `reason_code` | enum | yes | Closed enum, table below. |
| `detail` | string | yes | One line, ≤300 chars, machine-greppable, naming the offending ids and numbers. |

### `reason_code` enum — the Phase 4 check list

| Code | Raised when |
|---|---|
| `UNKNOWN_SOURCE` | A span names a `source_id` with no snapshot under `sources/`. |
| `SNAPSHOT_HASH_MISMATCH` | The snapshot exists but its recomputed `content_hash` or `source_id` differs from the stored value. |
| `SPAN_OUT_OF_RANGE` | `start < 0`, `end > len(text)`, or `start >= end`. |
| `SPAN_TOO_LONG` | `end - start > 2000`. |
| `EXCERPT_MISMATCH` | An agent-written excerpt (a legacy `quotes[].text`, or an excerpt field on a report claim) differs from `text[start:end]`. |
| `NO_SPANS` | A record that §7/§8 requires to carry spans carries none — the `unverified` legacy case (R16). |
| `URL_NOT_RETRIEVED` | A citation URL in the artifact has no snapshot retrieved for that artifact in this run. |
| `NO_FRESH_FETCH` | A source backing a `supported` verdict has no fresh `fetch`/`local_pdf` event for this run (§11). |
| `UNSUPPORTED_VERDICT` | `outputs/verification.json` records an unsupported claim for this artifact. |
| `SOURCE_OUTSIDE_ACCEPTED` | A synthesis or report claim introduces a `source_id` outside the accepted evidence for its branch. |
| `NO_PAPER_ID` | A literature claim's snapshot `paper` object supplies no PMID, DOI or PMCID, or contradicts the claim's `evidence_id`. |
| `ASSET_HASH_MISMATCH` | A `user-supplied-pdf` snapshot's asset file is missing or no longer hashes to `asset.sha256`. |

### Derivation rule (binding)

> The assembler derives `url`, `excerpt`, `section`, `page`, `access`, `paper` and every other
> piece of source metadata **from the snapshots**, never from agent-written output.

Agent records contribute exactly three things to `result.json`: the `claim` string, the
`source_id`, and the `start`/`end` pair. Everything else is recomputed. Where an agent record and
a snapshot disagree about a URL, an identifier, an access level or a quote, the snapshot wins and
the artifact is **rejected** with the matching `reason_code` rather than silently corrected.

`result.json` is written atomically (temp file in `outputs/`, then `os.replace`). It is an input
to `verify.py` (check `C-ASSEMBLER`) and it is the only thing `okf.py promote` reads for
evidence — never raw agent output (`VALIDATION_ARCHITECTURE_PLAN.md` Phase 6). Ordering is fixed:
`assemble.py` runs **before** `verify.py`, which runs before render, HTML report and OKF
promotion (R24).

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

### Evidence-kernel addenda (R10–R24)

Ambiguities closed while writing the evidence-proof kernel contracts (§10–§13). Binding.

| # | Question | Resolution |
|---|---|---|
| R10 | What kind of offset are `start`/`end`? | **Character offsets into the snapshot's decoded `text`** — Python `str` indices, so the excerpt is exactly `text[start:end]`. Not byte offsets, not UTF-8 byte counts, not UTF-16 code units, not offsets into a rendered/markdown view. `text` is never normalized (no NFC/NFD, no whitespace collapsing, no newline rewriting) on write or on read; a normalizing reader would invalidate every span in the run. |
| R11 | Exact `source_id` computation and filename | `source_id = "src-" + sha256_hex(utf8(url) + NUL + utf8(text))` where NUL is one literal `0x00` byte, not the two characters backslash-zero. Lowercase hex, 64 chars, so `source_id` is 68 chars including the prefix. The snapshot filename is `<source_id>.json`, i.e. `src-<64 hex>.json`. `content_hash` is separately `"sha256:" + sha256_hex(utf8(text))` — the URL is deliberately excluded so body tampering is detectable on its own. |
| R12 | Can a snapshot be corrected in place? | No. Snapshots are write-once with `O_EXCL`; a rewrite is an error, not an overwrite. Corrected or re-fetched text produces a **new** `source_id` and a new file. The old snapshot stays. Consequence: offsets under an existing claim can never drift, and a claim pointing at superseded text stays pointed at exactly what it was written against. |
| R13 | Where do PDFs live? | **Not** duplicated per run. The plan's `runs/<slug>/assets/<sha256>.pdf` is superseded: PDFs stay in the shared library `<wiki>/assets/papers/` (rung 0, `PLAN.md` §5), and the snapshot's `asset.path` is **wiki-root-relative**, matching `corpus.fulltext.local_path`. This is an explicit, named exception to S7. `<run>/sources/` holds only `src-*.json`; there is no `<run>/assets/`. |
| R14 | Relationship between `source_id` and `evidence_id` | Many-to-one: one study (`evidence_id`) may have several snapshots — abstract, PMC full text, a user-supplied PDF. A span must name a `source_id` registered for its own `evidence_id`; the snapshot's `paper` triple and the `evidence_id` must supply and agree on at least one identifier, else `NO_PAPER_ID`. The corpus record gains one optional field, `source_ids: string[]` (default `[]`), listing the snapshots registered for that study; `corpus.py export --strict-schema` strips it, exactly as for R7's fields. |
| R15 | What exactly makes a retrieval "fresh"? | The five conjunctive conditions in §11: matching `source_id`; `type` is `fetch` or `local_pdf`; `fresh == true`; `at >=` this run's `config.json` `created_at`; and the recomputed digest still matches. Anything else — a `register`, a `read`, a cache-only fetch, a prior run's event copied in, a search snippet, an existing wiki note — is not fresh proof. |
| R16 | Old records with no spans | Not a schema error, never deleted, marked `unverified`: `NO_SPANS` in `diagnostics.unresolved[]`, never in `accepted[]`, therefore unable to pass the gate; `C-SPAN` `warn` with the gate off and `fail` with it on; and never permitted to back a promoted OKF concept's evidence footnote, gate or no gate. |
| R17 | Who writes `quotes[]`? | The assembler, at assembly time, by re-slicing `snapshot.text[start:end]`. `quotes[]` is a **derived** field in §7 and does not exist at all in §8. A subagent MUST emit `quotes: []`; agent-written quote text is discarded rather than trusted, and where it is present and differs from the re-slice the artifact is rejected with `EXCERPT_MISMATCH` rather than silently corrected. |
| R18 | Multiple spans for one claim | Allowed and expected. Spans for one claim are ORed evidence, may come from different `source_id`s, and may overlap. The assembler never concatenates or merges them, and never sums their lengths against the 2000-character cap — the cap is per span. A claim needing 3000 characters of support takes two disjoint spans, not one over-long one. |
| R19 | Which fields need spans? | Extraction: every `outcomes[]` entry with a non-null `effect`, `ci_low`, `ci_high` or `p_value`, plus record-level `spans[]` covering each non-null narrative factual field (`design`, `n_total`, `n_arms`, `population`, `intervention`, `comparator`, `funding`, `coi`, `limitations`). Appraisal: every `domains[]` entry whose `judgement` is not `unclear`. `extractor_notes` and an `unclear` domain rationale are the extractor's own reasoning, not claims about the source, and need no span — indeed an `unclear` grounded in absent reporting must take `spans: []`, because there is nothing to point at. |
| R20 | Is the gate on by default? | No. `assemble.py` and the `C-*` kernel checks are built, wired and **always reported**, but with `gates.evidence_kernel` (equivalently `--gate`) absent or `false` they do not block Stage 8. Default off until a live dry run passes (`VALIDATION_ARCHITECTURE_PLAN.md` migration step 9). Two things are never downgraded by the flag: `C-SNAPSHOT` and `C-SPAN` tamper failures, which block OKF promotion regardless. |
| R21 | `access` vs `evidence_basis` vs `fulltext.status` | `access` (§10) describes the *snapshot*; `evidence_basis` (§7, §8) describes the *record*. Mapping: `full_text` → `fulltext`; `abstract`, `web` → `abstract_only`; `preprint` and `guideline` → `fulltext` when the whole document's text was captured, `abstract_only` otherwise. `evidence_basis` must equal the corpus `fulltext.status` (unchanged rule); where the snapshot's `access` and the record's `evidence_basis` disagree, the snapshot wins and the artifact is rejected, not corrected. |
| R22 | Do `register` and `read` ever count as retrieval? | Never. Both are always `fresh: false`. `register` exists so that text obtained by `eutils.py`/`fulltext.py`/`library.py` becomes span-addressable without pretending it was retrieved in this run; `read` is a pure audit trail of which worker saw which window. A run whose sources are all `register`ed produces snapshots and spans that validate structurally but fail `C-FRESH-FETCH`. |
| R23 | `events.jsonl` ordering and `event_id` | File order is authoritative; `at` is informational and may tie or, under clock skew, go backwards. `event_id` is `ev-` plus a zero-padded counter of at least 4 digits, allocated in append order and unique within the run. Append is the only mutation. Writing the snapshot always precedes appending its event, so an event never names a `source_id` that does not yet exist. |
| R24 | Stage-8 ordering, and does `result.json` replace `verification.json`? | It does not. `assemble.py` runs **first**, writing `outputs/result.json`; `verify.py` runs second, reads it, and writes `outputs/verification.json` including `C-ASSEMBLER`. Then render, HTML report, and `okf.py promote --check` followed by `okf.py promote`. `verification.json` remains the verifier's own contract (§9); `result.json` is the evidence ledger and the only evidence input to promotion. |
| R25 | Two reason codes beyond §13's closed enum | `store.py` and `assemble.py` independently required these, so the §13 enum is extended by exactly two values. **`ACCESS_MISMATCH`** — the span's declared `access` disagrees with its snapshot's. `store.py verify_span` returns this in `warnings[]`; the assembler promotes any such warning to a rejection, because under the derivation rule the snapshot wins and an artifact is never corrected to fit. **`SCHEMA_ERROR`** — a structurally malformed record or span. Both appear in `diagnostics.unresolved[].reason_code`; neither can appear in `accepted[]`. |
| R26 | `accepted[].fresh` is always `true` | An accepted artifact is an assertion of support, and `NO_FRESH_FETCH` rejects anything lacking a fresh retrieval (or the hash-checked `user-supplied-pdf` exception). The field stays a boolean for forward compatibility and for readers that inspect `diagnostics`, but `false` cannot occur inside `accepted[]`. |
