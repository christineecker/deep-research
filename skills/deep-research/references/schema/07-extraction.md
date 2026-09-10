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
  "diagnostic_accuracy": [],
  "prediction_model": [],
  "qualitative_evidence": null,
  "cross_sectional_evidence": null,
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
| `diagnostic_accuracy` | object[] | no | One entry per index test (or index-test/threshold pair) the paper reports accuracy for, against its reference standard. Optional; `[]` or omitted for every design that is not a diagnostic-accuracy study. Feeds `appraisal_target` and the domain judgements for QUADAS-2 (`references/appraisal.md` §5). See below. |
| `prediction_model` | object[] | no | One entry per prediction model the paper develops and/or validates. Optional; `[]` or omitted for every design that is not a prediction-model study. Feeds `appraisal_target` and the domain judgements for PROBAST (`references/appraisal.md` §6). See below. |
| `qualitative_evidence` | object \| null | no | Study-design and conduct metadata for a qualitative study — singular, not an array, because one paper reports one qualitative design (a mixed-methods paper with a qualitative and a quantitative arm still gets one `qualitative_evidence` object for the qualitative arm; the quantitative arm uses `outcomes`/other fields as normal). Optional; `null` or omitted for every design that has no qualitative component. Feeds the CASP qualitative checklist (`references/appraisal.md` §8). See below. |
| `cross_sectional_evidence` | object \| null | no | Sampling, measurement, and confounding metadata for a cross-sectional or prevalence study — singular, one per paper. Optional; `null` or omitted for every other design. Feeds the JBI prevalence / analytical cross-sectional checklists (`references/appraisal.md` §9). See below. |
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

### `diagnostic_accuracy[]` entry

One entry per index test (or index-test/threshold pair). A paper reporting two thresholds for the
same assay, or two different index tests, needs two entries — do not average or merge them; each
becomes its own QUADAS-2 appraisal target (`references/appraisal.md` §5).

| Field | Type | Req | Meaning |
|---|---|---|---|
| `index_test` | string \| null | yes | The test being evaluated, including assay/instrument and, if applicable, the threshold as a separate `threshold` field rather than folded into this name. |
| `reference_standard` | string \| null | yes | The standard the index test is compared against. |
| `target_condition` | string \| null | yes | The condition/diagnosis the index test is meant to detect. |
| `threshold` | string \| null | yes | The positivity cutoff as reported (e.g. `">=99th percentile"`). `null` if the test is not threshold-based or the paper does not state one. |
| `prespecified_threshold` | bool \| null | yes | Whether the paper states the threshold was set before seeing the data. `null` if not stated — do not infer `true` from silence. |
| `n_total` | int \| null | yes | Number of patients analysed for this index-test/reference-standard pair. |
| `tp` / `fp` / `fn` / `tn` | int \| null | yes | 2x2 cell counts as reported or as directly computable from a reported 2x2 table. Never back-calculated from sensitivity/specificity plus prevalence — if the paper gives only proportions, leave the counts `null` and record the proportions below. |
| `sensitivity` / `specificity` | number \| null | yes | As reported (proportion or percentage — state which in `extractor_notes` if ambiguous). |
| `sens_ci_low` / `sens_ci_high` / `spec_ci_low` / `spec_ci_high` | number \| null | yes | Reported interval bounds for sensitivity/specificity. `null` if not reported. |
| `interval_index_reference` | string \| null | yes | Time elapsed between the index test and the reference standard, as reported. Feeds QUADAS-2 "flow and timing". |
| `verification` | enum \| null | yes | `all_patients` \| `partial` \| `differential` \| `unclear`. Whether every patient received the same reference standard (`all_patients`), only some did (`partial`), or different reference standards were used depending on the index-test result (`differential`, a classic QUADAS-2 flow-and-timing flaw). `unclear` if not stated. |
| `blinding` | string \| null | yes | Whether index-test and reference-standard interpreters were blinded to each other's result, as stated. |
| `spans` | object[] | yes | Claim spans (§12) backing this entry. **Required whenever any of `index_test`, `reference_standard`, `target_condition`, `threshold`, `tp`, `fp`, `fn`, `tn`, `sensitivity`, or `specificity` is non-null.** An entry with all of those `null` may have `spans: []`. |

### `prediction_model[]` entry

One entry per model. A paper both developing and externally validating a model is two entries —
mark `study_type` accordingly — because PROBAST appraises development and validation separately
(`references/appraisal.md` §6). A paper validating two prior models against the same cohort is
also two entries.

| Field | Type | Req | Meaning |
|---|---|---|---|
| `model_name` | string \| null | yes | The model's name as given by the paper, or a short descriptive name if unnamed. |
| `study_type` | enum \| null | yes | `development` \| `validation` \| `development_and_validation` \| `unclear`. |
| `model_purpose` | string \| null | yes | What the model predicts and for whom (diagnostic vs. prognostic, target population, intended use point). |
| `outcome_definition` | string \| null | yes | How the predicted outcome is defined and ascertained. |
| `prediction_horizon` | string \| null | yes | The time window the model predicts over (e.g. `"30 days"`, `"5-year risk"`). `null` if not time-bound. |
| `candidate_predictors` | string \| null | yes | The full candidate predictor set considered, as reported. `null` if not stated (e.g. an external-validation-only paper testing a fixed published model). |
| `final_predictors` | string \| null | yes | The predictors retained in the final model. |
| `predictor_selection_method` | string \| null | yes | How predictors were chosen: e.g. `"univariable p<0.05 screening"`, `"all candidate predictors retained"`, `"LASSO"`, `"clinical consensus, no data-driven selection"`. `null` for a validation-only entry with no selection step of its own. |
| `n_participants` | int \| null | yes | Participants analysed for this model (development or validation sample, matching `study_type`). |
| `n_events` | int \| null | yes | Outcome events observed in that sample. `null` if not reported or not applicable (e.g. a continuous outcome). |
| `events_per_predictor` | number \| null | yes | As reported by the paper only. **Never back-calculated** from `n_events` / predictor count — if the paper does not state it, leave `null` and, if useful, note the raw counts in `extractor_notes`. |
| `validation_approach` | string \| null | yes | Internal validation (e.g. `"bootstrap, 500 resamples"`, `"10-fold cross-validation"`, `"none reported"`) or external validation description (cohort, setting). |
| `missing_data_handling` | string \| null | yes | e.g. `"complete-case analysis"`, `"multiple imputation, 20 datasets"`. `null` if not stated. |
| `discrimination` | string \| null | yes | Discrimination metric(s) as reported, e.g. `"C-statistic 0.81 (95% CI 0.77-0.85)"`. |
| `calibration` | string \| null | yes | Calibration metric(s)/assessment as reported, e.g. `"calibration slope 0.94, intercept 0.02"`, or `"calibration plot only, no slope/intercept reported"`. `null` if no calibration assessment is reported — record that absence in `extractor_notes` too, since PROBAST treats it as an analysis-domain concern. |
| `spans` | object[] | yes | Claim spans (§12) backing this entry. **Required whenever any of `model_name`, `outcome_definition`, `prediction_horizon`, `n_participants`, `n_events`, `events_per_predictor`, `discrimination`, or `calibration` is non-null.** An entry with all of those `null` may have `spans: []`. |

### `qualitative_evidence` object

A single object (not an array), present only for a paper with a qualitative component.

| Field | Type | Req | Meaning |
|---|---|---|---|
| `research_question` | string \| null | yes | The qualitative research question or aim, as stated. |
| `methodology` | string \| null | yes | The named qualitative approach, e.g. `"grounded theory"`, `"interpretive phenomenological analysis"`, `"reflexive thematic analysis"`, `"ethnography"`. `null` if the paper does not name one. |
| `theoretical_framework` | string \| null | yes | Any stated theoretical or conceptual framework informing the study. `null` if none stated. |
| `sampling_strategy` | string \| null | yes | How participants were sampled/recruited (e.g. `"purposive sampling to maximum variation"`). |
| `sample_size` | int \| null | yes | Number of participants (or interviews/focus groups, stated alongside participant count in `extractor_notes` if they differ). |
| `data_collection_method` | string \| null | yes | e.g. `"semi-structured interviews, 45-60 min, audio-recorded and transcribed verbatim"`. |
| `setting` | string \| null | yes | Where/when data collection took place. |
| `analysis_approach` | string \| null | yes | e.g. `"reflexive thematic analysis per Braun and Clarke"`, `"framework analysis"`. |
| `researcher_reflexivity` | string \| null | yes | What the paper states about the researcher-participant relationship, positionality, or reflexivity. `null` if not addressed — do not infer absence of reflexive practice from silence, only absence of *reporting* it. |
| `ethical_approval` | string \| null | yes | Ethics approval / consent statement as reported. |
| `key_themes` | string \| null | yes | The paper's reported themes/findings, named (not the full narrative — a list or short description sufficient for `key_themes` to be checked against the report). |
| `spans` | object[] | yes | Claim spans (§12) backing this object. **Required whenever any of `research_question`, `methodology`, `sampling_strategy`, `sample_size`, `data_collection_method`, `analysis_approach`, or `key_themes` is non-null.** `spans: []` is legal only when every one of those fields is `null`. |

### `cross_sectional_evidence` object

A single object (not an array), present only for a paper with a cross-sectional or prevalence
component. Covers both `JBI-prevalence` (standalone estimate) and `JBI-cross-sectional`
(exposure-outcome association) targets — fields not applicable to one still stay in the same
object; leave them `null`.

| Field | Type | Req | Meaning |
|---|---|---|---|
| `sample_frame` | string \| null | yes | The population/list the sample was drawn from (e.g. `"national health insurance claims database"`). |
| `sampling_method` | string \| null | yes | How the sample was selected (e.g. `"simple random sample from the sample frame"`, `"consecutive clinic attendees"`). |
| `sample_size` | int \| null | yes | Number of participants analysed. |
| `response_rate` | string \| null | yes | Response/participation rate as reported, and how non-response was handled if stated. |
| `condition_measurement_method` | string \| null | yes | How the condition/outcome of interest was measured or ascertained. |
| `exposure_measurement_method` | string \| null | yes | How the exposure was measured, for an analytical cross-sectional study. `null` for a pure prevalence study with no exposure. |
| `confounders_identified` | string \| null | yes | Confounders the study identified as relevant, as stated. `null` if the study identified none (state that explicitly, don't leave ambiguous with "not applicable"). |
| `confounders_handling` | string \| null | yes | How identified confounders were addressed (adjustment, stratification, matching, none). `null` only when `confounders_identified` is also `null`. |
| `prevalence_estimate` | string \| null | yes | The headline prevalence/burden estimate with its denominator and interval, as reported (e.g. `"12.3% (95% CI 10.1-14.8), n=1204"`). `null` for an analytical cross-sectional study reporting only an association, not a prevalence figure. |
| `spans` | object[] | yes | Claim spans (§12) backing this object. **Required whenever any of `sample_frame`, `sampling_method`, `sample_size`, `response_rate`, `condition_measurement_method`, `exposure_measurement_method`, or `prevalence_estimate` is non-null.** `spans: []` is legal only when every one of those fields is `null`. |

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
