---
title: "{{TITLE}}"
run_slug: {{RUN_SLUG}}
profile: {{PROFILE}}
generated: { by: deep-research/0.1, at: {{GENERATED_AT}} }
status: {{REPORT_STATUS}}   # final | provisional (any verifier fail, or quarantined evidence)
---

# {{TITLE}}

<!-- The deliverable. Every claim about evidence carries a footnote keyed to an evidence_id that
     was actually retrieved this run (PLAN.md §6). No footnote -> the claim does not belong in
     sections 1-8; if it is your own inference it belongs in section 9, and nowhere else. -->

> **Status: {{REPORT_STATUS}}.** {{STATUS_NOTE}}
> <!-- e.g. "Provisional: 3 of 28 included studies could not be obtained in full text; see §8." -->

## 1. Question

{{RESEARCH_QUESTION}}

**Short answer.** {{BOTTOM_LINE}}

<!-- 3-6 sentences. State the direction, the strength, the certainty, and what would change the
     answer. Hedge honestly: if the evidence does not answer the question, say that first. -->

## 2. Protocol summary

| | |
|---|---|
| Population | {{POPULATION}} |
| Intervention / Exposure | {{INTERVENTION}} |
| Comparator | {{COMPARATOR}} |
| Primary outcomes | {{OUTCOMES_PRIMARY}} |
| Designs eligible | {{DESIGNS}} |
| Limits | {{LIMITS_SUMMARY}} |
| Sources searched | {{SOURCES}} |
| Screening | {{SCREENING_MODE}} |
| Protocol | [`protocol.md`](../protocol.md) |

Deviations from protocol: {{DEVIATIONS_SUMMARY}} <!-- "none" or a short list pointing at protocol §9 -->

## 3. Search and selection (PRISMA flow)

| Step | n |
|---|---|
| Records identified — PubMed | {{N_PUBMED}} |
| Records identified — other sources | {{N_OTHER}} |
| Duplicates removed | {{N_DUPES}} |
| Records screened (title/abstract) | {{N_SCREENED}} |
| Records excluded at screening | {{N_EXCLUDED_SCREEN}} |
| Full texts sought | {{N_FULLTEXT_SOUGHT}} |
| Full texts not obtainable (quarantined) | {{N_QUARANTINED}} |
| Studies included | {{N_INCLUDED}} |

Queries executed, with hit counts: {{QUERY_LOG_REF}} <!-- link to the search-strategy log -->
Dual screening: {{DUAL_SCREEN_NOTE}} <!-- disagreement rate = adjudications / dual-screened; "n/a" for single-screen profiles -->

Exclusion reasons at full-text stage: {{FULLTEXT_EXCLUSION_REASONS}}

## 4. Evidence table

{{EVIDENCE_TABLE}}

<!-- Insert templates/evidence-table.md rendered for this run, or link to it if long. -->

## 5. Synthesis by outcome

<!-- One subsection per outcome, primary outcomes first. Narrative synthesis with effect-
     direction tabulation. Do not pool numerically unless the protocol's analysis plan allowed
     it and the studies are combinable — if you did pool, state the method and the heterogeneity. -->

### 5.1 {{OUTCOME_1_NAME}}

Studies: {{OUTCOME_1_N_STUDIES}} ({{OUTCOME_1_N_PARTICIPANTS}} participants).
Direction: {{OUTCOME_1_DIRECTION_TALLY}} <!-- e.g. "4 favor intervention, 2 null, 1 favors comparator" -->

{{OUTCOME_1_NARRATIVE}}[^{{KEY_A}}][^{{KEY_B}}]

<!-- Include null and negative studies in the narrative with the same weight as positive ones.
     Report effect sizes with CIs, not just significance. -->

### 5.2 {{OUTCOME_2_NAME}}

{{OUTCOME_2_NARRATIVE}}

## 6. Agreement and conflict

{{CONFLICT_NARRATIVE}}

<!-- Where studies disagree, name the disagreeing studies and explain WHY: population, dose,
     comparator, outcome instrument, follow-up length, analysis model, risk of bias, funding.
     Never average a conflict away and never present a mean of contradictory findings as the
     answer (PLAN.md §6). If the cause of the disagreement is unknown, say "unexplained". -->

| Outcome | Concordant studies | Discordant studies | Most plausible explanation |
|---|---|---|---|
| {{CONFLICT_OUTCOME}} | {{CONFLICT_CONCORDANT}} | {{CONFLICT_DISCORDANT}} | {{CONFLICT_EXPLANATION}} |

## 7. Certainty of evidence (GRADE)

| Outcome | Studies (n) | RoB | Inconsistency | Indirectness | Imprecision | Publication bias | Certainty |
|---|---|---|---|---|---|---|---|
| {{GRADE_OUTCOME}} | {{GRADE_N_STUDIES}} | {{GRADE_ROB}} | {{GRADE_INCONSISTENCY}} | {{GRADE_INDIRECTNESS}} | {{GRADE_IMPRECISION}} | {{GRADE_PUBBIAS}} | {{GRADE_CERTAINTY}} |

Reasons for downgrading: {{GRADE_FOOTNOTES}}

## 8. What is missing, quarantined, or uncertain

<!-- Stated plainly. This section is what separates an honest review from a confident one. -->

- **Full text not obtained ({{N_QUARANTINED}}):** {{QUARANTINED_LIST}} — listed with links in [`missing.md`](../missing.md). These studies are excluded from the synthesis; their absence may bias it in the direction of {{QUARANTINE_BIAS_DIRECTION}} <!-- or "an unknown direction" -->.
- **Abstract-only evidence ({{N_ABSTRACT_ONLY}}):** {{ABSTRACT_ONLY_LIST}} — conduct could not be appraised; every claim resting on these is marked *(abstract only)* in the text above.
- **Retracted / expression of concern:** {{RETRACTION_LIST}}
- **Preprints (not peer reviewed):** {{PREPRINT_LIST}}
- **Searched but not found:** {{SEARCHED_NOT_FOUND}} <!-- outcomes, populations or comparisons for which the search returned nothing; state that this is absence of evidence, not evidence of absence -->
- **Known limitations of this review:** {{REVIEW_LIMITATIONS}} <!-- search date, language limits, single screener, no grey literature, no meta-analysis, etc. -->

## 9. Evidence gaps

| Gap | Why it matters | What would close it |
|---|---|---|
| {{GAP}} | {{GAP_WHY}} | {{GAP_STUDY_NEEDED}} |

<!-- Gaps are derived from the evidence above (under-studied populations, missing comparators,
     short follow-up, absent outcomes) and are still evidence-anchored: footnote them. -->

---

# 10. New insights & hypotheses — NOT EVIDENCE

> **Hard wall.** Everything below this line is generated inference, not a finding of the
> literature. Nothing here is supported by the studies reviewed above; it is what the reviewer
> thinks *might* be true and how it could be tested. Do not cite this section as evidence, do
> not quote it as a conclusion, and do not carry any sentence from it back into sections 1-9.

<!-- Enforced by verifier check C-HYPOTHESIS-WALL. Every item MUST be phrased conditionally
     ("may", "could", "if X then Y would be expected") and MUST carry a testable prediction.
     A hypothesis phrased as an established finding is a verifier failure. Footnotes here point
     to the evidence that PROMPTED the idea; they never make the idea itself evidenced. -->

### H1 — {{HYPOTHESIS_1_TITLE}}

- **Idea (speculative):** {{HYPOTHESIS_1_STATEMENT}}
- **What prompted it:** {{HYPOTHESIS_1_TRIGGER}}[^{{KEY_C}}]
- **Testable prediction:** {{HYPOTHESIS_1_PREDICTION}}
- **How to test it:** {{HYPOTHESIS_1_TEST}}
- **What would falsify it:** {{HYPOTHESIS_1_FALSIFIER}}

### H2 — {{HYPOTHESIS_2_TITLE}}

- **Idea (speculative):** {{HYPOTHESIS_2_STATEMENT}}
- **What prompted it:** {{HYPOTHESIS_2_TRIGGER}}
- **Testable prediction:** {{HYPOTHESIS_2_PREDICTION}}
- **How to test it:** {{HYPOTHESIS_2_TEST}}
- **What would falsify it:** {{HYPOTHESIS_2_FALSIFIER}}

---

## References

<!-- Markdown footnotes are the per-claim attribution layer (PLAN.md §6). Keys match
     `sources[].id` in the OKF bundle. Every key used above appears here; every entry here is a
     record actually retrieved this run. Uncited included studies are reported by verify.py. -->

[^{{KEY_A}}]: {{AUTHORS_ETAL_A}} {{TITLE_A}}. *{{JOURNAL_A}}* {{YEAR_A}}. PMID {{PMID_A}}. DOI [{{DOI_A}}](https://doi.org/{{DOI_A}}). [PubMed](https://pubmed.ncbi.nlm.nih.gov/{{PMID_A}}/)
[^{{KEY_B}}]: {{AUTHORS_ETAL_B}} {{TITLE_B}}. *{{JOURNAL_B}}* {{YEAR_B}}. PMID {{PMID_B}}. DOI [{{DOI_B}}](https://doi.org/{{DOI_B}}). *(abstract only)*
[^{{KEY_C}}]: {{AUTHORS_ETAL_C}} {{TITLE_C}}. *{{JOURNAL_C}}* {{YEAR_C}}. PMID {{PMID_C}}. DOI [{{DOI_C}}](https://doi.org/{{DOI_C}}).

<!-- Data source attribution (required): literature identified via PubMed
     (https://pubmed.ncbi.nlm.nih.gov/); full text via PMC / Europe PMC / Unpaywall as recorded
     per study in corpus.jsonl. -->

Searched {{SEARCH_DATE}}. Generated by deep-research; run `{{RUN_SLUG}}`.
