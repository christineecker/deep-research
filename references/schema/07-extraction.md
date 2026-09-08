# schema §7 — `extraction record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 7. `extraction record`

`workspace/extractions/pmid-<pmid>.json`. Stage 5, one subagent per paper (`SKILL.md` "Pipeline").

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
| `evidence_basis` | enum | yes | `fulltext` \| `abstract_only`. Must equal the corpus `fulltext.status` mapped (`missing` never reaches extraction). Abstract-only extractions may not be appraised as if full (`SKILL.md` "Invariants"). |
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
