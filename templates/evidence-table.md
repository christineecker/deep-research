# Evidence table — {{QUESTION_SHORT}}

<!-- Generated from corpus.jsonl + workspace/extractions/*.json + workspace/appraisals/*.json.
     One row per study x outcome. A study with three outcome-timepoint pairs gets three rows;
     repeat the study id, leave design/N/population blank on continuation rows or repeat them —
     be consistent within a run. Never leave a cell empty because the value is unknown: write
     `not stated`. Do not invent values. -->

Included studies: {{N_INCLUDED}} | full text: {{N_FULLTEXT}} | abstract-only: {{N_ABSTRACT_ONLY}} | quarantined (not in this table): {{N_MISSING}}

| Study | Design | N | Population | Intervention / Comparator | Outcome (timepoint) | Effect [95% CI] | p | Direction | RoB (tool) | Evidence basis | Tier |
|---|---|---|---|---|---|---|---|---|---|---|---|
| {{STUDY_LABEL}}[^{{FOOTNOTE_KEY}}] | {{DESIGN}} | {{N_TOTAL}} ({{N_ARMS}}) | {{POPULATION}} | {{INTERVENTION}} vs {{COMPARATOR}} | {{OUTCOME_NAME}} ({{TIMEPOINT}}) | {{EFFECT_MEASURE}} {{EFFECT}} [{{CI_LOW}}, {{CI_HIGH}}] | {{P_VALUE}} | {{DIRECTION}} | {{OVERALL_JUDGEMENT}} ({{TOOL}}) | {{EVIDENCE_BASIS}} | {{SOURCE_TIER}} |

<!-- Column rules:
     Study            — "First-author Year" + footnote key resolving to the PMID/DOI citation.
     N                — analysed total; arms in parentheses. `not stated` if absent (never estimated).
     Effect [95% CI]  — as reported, in the reported measure's units. Missing bound -> `not reported`.
     p                — numeric only; a threshold (p<0.001) renders as reported with a dagger and
                        a note, matching extraction `extractor_notes`.
     Direction        — favors_intervention | favors_comparator | null_effect | unclear.
                        Null and negative rows are first-class; never omit them.
     RoB              — appraisal `overall_judgement` + `tool`. `none` for narrative/guideline.
     Evidence basis   — fulltext | abstract_only. abstract_only rows are visually marked and
                        must never carry a favourable RoB rating (schema.md §8).
     Tier             — acquisition ladder rung 0-7 (source_tier). 6 = preprint twin: flag loudly.
                        Rows sourced from a preprint carry a `preprint` marker next to the study label. -->

## Notes

- Abstract-only rows ({{N_ABSTRACT_ONLY}}): {{ABSTRACT_ONLY_LIST}} — appraised as `unclear` on all domains not assessable from an abstract.
- Preprints ({{N_PREPRINTS}}): {{PREPRINT_LIST}} — not peer reviewed; content may differ from any published version.
- Retraction flags: {{RETRACTION_LIST}} <!-- retracted / expression_of_concern / corrected, or "none" -->
- Manually supplied full text (inbox resume): {{INBOX_SUPPLIED_LIST}}

## Footnotes

[^{{FOOTNOTE_KEY}}]: {{AUTHORS_ETAL}} {{TITLE}}. *{{JOURNAL}}* {{YEAR}}. PMID {{PMID}}. DOI [{{DOI}}](https://doi.org/{{DOI}}). [PubMed](https://pubmed.ncbi.nlm.nih.gov/{{PMID}}/)

<!-- One footnote per study, keyed to `sources[].id` in the OKF bundle (PLAN.md §6).
     Every footnote must resolve to an evidence_id actually retrieved in this run. -->
