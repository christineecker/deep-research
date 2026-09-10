---
title: "Review protocol — {{QUESTION_SHORT}}"
run_slug: {{RUN_SLUG}}
profile: {{PROFILE}}
scope: {{SCOPE}}
rigor: {{RIGOR}}
generated: { by: deep-research/0.1, at: {{GENERATED_AT}} }
status: draft
---

# Protocol — {{QUESTION_SHORT}}

<!-- Written in stage 1, before any search runs. Gated at `protocol+strategy` and `both`.
     Criteria ids defined here are the ONLY ids screening may cite (schema.md §5
     `criterion_failed`). Never renumber after screening starts — append and log a deviation. -->

## 1. Question

{{RESEARCH_QUESTION}}

<!-- The user's question restated as an answerable one. One paragraph max. -->

## 2. {{QUESTION_FRAMEWORK}} <!-- PICO (default) / PECO / PEO / PCC / SPIDER / SPICE — pick per
     references/question-frameworks.md §1; swap the row labels below for the chosen framework's
     fields (§2 there). Shown here in its PICO/PECO shape, the two frameworks every prior run
     used. -->

| Element | Definition |
|---|---|
| Population | {{POPULATION}} |
| Intervention / Exposure | {{INTERVENTION}} |
| Comparator | {{COMPARATOR}} |
| Outcomes (primary) | {{OUTCOMES_PRIMARY}} |
| Outcomes (secondary) | {{OUTCOMES_SECONDARY}} |
| Setting / context | {{SETTING}} |
| Study designs eligible | {{DESIGNS}} |

<!-- If the question is aetiological/prognostic, use PECO and say so. If no comparator exists,
     write "none (single-arm / descriptive)" rather than inventing one. For PEO/PCC/SPIDER/SPICE,
     replace this table's rows with that framework's fields (references/question-frameworks.md
     §2) instead of leaving Intervention/Comparator blank. -->

## 3. Inclusion criteria

<!-- Numbered I1..In. Each must be decidable from a title+abstract wherever possible; mark ones
     that need full text with "(full text)". Screening cites the FIRST failing id. -->

| Id | Criterion | Decidable from abstract? |
|---|---|---|
| I1 | {{I1}} | {{I1_ABSTRACT_DECIDABLE}} |
| I2 | {{I2}} | {{I2_ABSTRACT_DECIDABLE}} |
| I3 | {{I3}} | {{I3_ABSTRACT_DECIDABLE}} |

## 4. Exclusion criteria

<!-- Numbered E1..En. Outcome positivity/significance is NEVER an exclusion criterion
     (`SKILL.md` "Invariants": null and negative findings are actively wanted). -->

| Id | Criterion | Decidable from abstract? |
|---|---|---|
| E1 | {{E1}} | {{E1_ABSTRACT_DECIDABLE}} |
| E2 | {{E2}} | {{E2_ABSTRACT_DECIDABLE}} |
| E3 | {{E3}} | {{E3_ABSTRACT_DECIDABLE}} |

## 5. Limits

| Limit | Value | Field tag |
|---|---|---|
| Years | {{YEARS}} | {{YEARS_TAG}} |
| Language | {{LANGUAGES}} | `[la]` |
| Species / age | {{SPECIES_AGE}} | `[mh]` + age filters |
| Article types | {{ARTICLE_TYPES}} | `[pt]` |
| Journals | {{JOURNALS}} | `[ta]` |
| Authors | {{AUTHORS}} | `[au]` |
| Open access only | {{OA_ONLY}} | `[sb]` |
| Preprints included | {{PREPRINTS}} | — |

<!-- Filters not expressible as field tags (sample size, funding source, setting) are applied at
     screening as numbered criteria, not in the query. -->

## 6. Search plan

Sources (scope = `{{SCOPE}}`): {{SOURCES}}

<!-- narrow: PubMed MCP. medium: + E-utilities. wide: + Europe PMC (preprints).
     max: + web/guidelines/grey literature + connector authorization check. -->

| Query id | Purpose / axis | Query string | Source |
|---|---|---|---|
| q1 | {{Q1_PURPOSE}} | `{{Q1_STRING}}` | {{Q1_SOURCE}} |
| q2 | {{Q2_PURPOSE}} | `{{Q2_STRING}}` | {{Q2_SOURCE}} |
| q3 | {{Q3_PURPOSE}} | `{{Q3_STRING}}` | {{Q3_SOURCE}} |
| q4 | {{Q4_PURPOSE}} | `{{Q4_STRING}}` | {{Q4_SOURCE}} |

<!-- 4-8 genuinely orthogonal queries, not 8 near-duplicates: vary MeSH vs free-text,
     intervention-led vs outcome-led vs population-led, design hedges, citation chaining.
     At least one query must be aimed at null/negative and non-significant results. -->

Citation chaining: {{CITATION_CHAINING}}
Grey literature / guidelines: {{GREY_LITERATURE}}

Budgets: `max_articles` = {{MAX_ARTICLES}}; `max_parallel` = {{MAX_PARALLEL}};
`max_fulltext_failures` = {{MAX_FULLTEXT_FAILURES}}.

## 7. Screening, extraction, appraisal plan

- Screening: {{SCREENING_MODE}} <!-- single screener, or dual (screener-a/screener-b) + adjudicator at systematic/max -->
- Full text: acquisition ladder rungs 0-6; unobtainable -> quarantine in `missing.md`, run continues, synthesis marked provisional. No paywall circumvention.
- Extraction: one subagent per paper, schema.md §7.
- Appraisal tools: {{APPRAISAL_TOOLS}} <!-- RoB2 / ROBINS-I / Newcastle-Ottawa / AMSTAR-2 / QUADAS-2 / PROBAST / CASP-qualitative / JBI-prevalence / JBI-cross-sectional by design; none for narrative or abstract-only -->
- Certainty: GRADE per outcome.

## 8. Analysis plan

{{ANALYSIS_PLAN}}

<!-- Narrative synthesis by outcome with effect-direction tabulation is the default. State here
     whether any quantitative pooling is intended and on what condition; if not, say so. State
     how heterogeneity and conflicting results will be handled — conflicts are explained, never
     averaged away. -->

## 9. Deviations log

<!-- Every departure from this protocol after it is locked. Append-only, newest last.
     A run with no deviations keeps this table with a single "none" row. -->

| Date | Section | Deviation | Reason |
|---|---|---|---|
| {{DEVIATION_DATE}} | {{DEVIATION_SECTION}} | {{DEVIATION_WHAT}} | {{DEVIATION_WHY}} |
