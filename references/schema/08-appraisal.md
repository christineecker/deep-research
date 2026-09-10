# schema §8 — `appraisal record`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

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
| `tool` | enum | yes | `RoB2` \| `ROBINS-I` \| `Newcastle-Ottawa` \| `AMSTAR-2` \| `QUADAS-2` \| `PROBAST` \| `CASP-qualitative` \| `JBI-prevalence` \| `JBI-cross-sectional` \| `none`. `none` = no in-scope instrument applies (narrative review, guideline, editorial, abstract-only record, case series/report, animal/modelling design that is not a prediction model — see `references/appraisal.md`). This enum is closed until a new framework has shipped prompt, schema, verifier, and tests support (`SCIENTIFIC_FRAMEWORKS_OPTIMIZATION_PLAN.md`). |
| `tool_variant` | string \| null | no | Optional free-text variant of `tool`, e.g. `"cluster"` or `"crossover"` for `RoB2`, `"V2"` for `ROBINS-I`. `null`/omitted means the canonical variant. Does not widen the `tool` enum. |
| `appraisal_target` | object \| null | no\* | Structured description of what within the paper is being appraised, for tools that judge a specific result, index test, or model rather than the paper as a whole. See below. `null`/omitted is equivalent to appraising the paper's primary result under the tool's usual scope. \*Required for `QUADAS-2` and `PROBAST`; optional and preferred for `RoB2`/`ROBINS-I` when the appraised outcome, result, comparator, follow-up window, or effect of interest is known; not used (stays `null`) for `Newcastle-Ottawa`, `AMSTAR-2`, `CASP-qualitative`, `JBI-prevalence`, `JBI-cross-sectional`, or `tool: "none"`. |
| `domains` | object[] | yes | Tool-specific domains in the tool's canonical order. **Exactly `[]` when `tool == "none"`** — the reason is recorded in the appraiser receipt summary and report limitations, not as a domain or quality verdict. See `references/appraisal.md`. |
| `overall_judgement` | string | yes | Tool-appropriate overall rating: RoB2/ROBINS-I `low` \| `some_concerns` \| `moderate` \| `serious` \| `critical` \| `high` \| `unclear`; NOS a star count string (`"7/9"`); AMSTAR-2 `high` \| `moderate` \| `low` \| `critically_low`; QUADAS-2/PROBAST `low` \| `high` \| `unclear` — the worst risk-of-bias domain judgement (applicability is reported separately per domain, not folded into this field; `references/appraisal.md` §5/§6); CASP-qualitative a `yes`-count string (`"7/10"`); JBI-prevalence a `yes`-count string (`"7/9"`); JBI-cross-sectional a `yes`-count string (`"6/8"`) — all three count strings share the same declared-threshold caution as NOS (`references/appraisal.md` §8/§9) — never translate a count into a risk-of-bias label. |
| `grade` | object \| null | yes | GRADE domains for the body of evidence this study contributes to. `null` when GRADE is applied only at outcome level elsewhere. |
| `evidence_basis` | enum | yes | `fulltext` \| `abstract_only`. An `abstract_only` appraisal record REQUIRES `tool: "none"`, `domains: []`, and `overall_judgement: "unclear"` — an abstract cannot support a conduct appraisal against any tool's domains, so no domain-level judgement is ever recorded against `abstract_only` evidence. `grade` may still be filled at the body-of-evidence level, reflecting the abstract-only status as a risk-of-bias/indirectness limitation. |

### `appraisal_target` object (optional)

Not used by Newcastle-Ottawa, AMSTAR-2, CASP-qualitative, JBI-prevalence, or JBI-cross-sectional —
every field stays `null` and the whole object may be omitted or `null` for those five (they all
appraise the paper/study as a whole, not a specific result within it). `QUADAS-2` requires it
(`target_type: "index_test"`, plus `index_test`, `reference_standard`, and `population`;
`references/appraisal.md` §5). `PROBAST` requires it too (`target_type: "prediction_model"`, plus
`target_id` naming the model and whether development or validation is being appraised,
`population`, `outcome`, and `prediction_horizon`; `references/appraisal.md` §6). `RoB2` and
`ROBINS-I` may carry it — prefer setting it when the appraised outcome, result, comparator,
follow-up window, or effect of interest is known (e.g. one appraisal record per outcome for a
trial with divergent risk of bias across outcomes), and leave it `null` when the appraisal covers
the study's overall/primary conduct. It exists so a framework that judges a specific result, index
test, or model — rather than the paper as a whole — can record what, specifically, was appraised
without inventing a new top-level shape each time such a framework is added.

| Field | Type | Req | Meaning |
|---|---|---|---|
| `target_type` | enum \| null | no | `outcome` \| `result` \| `index_test` \| `prediction_model` \| `review` \| `study`. |
| `target_id` | string \| null | no | Reviewer-assigned identifier for the target, stable within the project. |
| `population` | string \| null | no | Population the target applies to. |
| `intervention_or_exposure` | string \| null | no | Intervention or exposure defining the target. |
| `comparator` | string \| null | no | Comparator, when applicable. |
| `outcome` | string \| null | no | Outcome name, matching the extraction record's outcome naming. |
| `index_test` | string \| null | no | Index test name, for diagnostic-accuracy targets. |
| `reference_standard` | string \| null | no | Reference standard, for diagnostic-accuracy targets. |
| `prediction_horizon` | string \| null | no | Prediction horizon, for prediction-model targets. |

### `domains[]` entry

| Field | Type | Req | Meaning |
|---|---|---|---|
| `domain` | string | yes | Domain name as defined by the tool. |
| `domain_group` | enum \| null | no | Grouping for tools whose domains split across kinds of judgement: `risk_of_bias` \| `applicability` \| `reporting` \| `certainty`. RoB2/ROBINS-I/Newcastle-Ottawa/AMSTAR-2/CASP-qualitative/JBI-prevalence/JBI-cross-sectional domains are all a single undifferentiated quality judgement and may leave this `null`. `QUADAS-2` and `PROBAST` domains require it — `risk_of_bias` on all four domains, plus `applicability` on the three that carry an applicability judgement (`references/appraisal.md` §5/§6). |
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
