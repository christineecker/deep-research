---
title: "{{TITLE}}"
evidence_id: {{EVIDENCE_ID}}
project: {{PROJECT}}
purpose: {{PURPOSE}}
audience: {{AUDIENCE}}
source_basis: {{SOURCE_BASIS}}
generated: { by: deep-research/0.1, at: {{GENERATED_AT}} }
status: {{SUMMARY_STATUS}}   # final | provisional (any verifier fail)
---

<!-- One paper, one summary. This is NOT a literature review — do not add a synthesis-style
     "certainty of evidence" verdict or compare this paper against others here (that belongs in
     a set overview, `templates/paper-summary-set.md`, and only when the full review pipeline
     produced it). Every sentence must trace to §14 `sections[].claims[]` (a claim from the
     extraction/appraisal record), never to background knowledge. -->

# {{TITLE}}

> **This summarizes one paper, not a body of evidence.** {{STATUS_NOTE}}

## 1. Citation

{{CITATION}}

## 2. Bottom line

{{BOTTOM_LINE}}

<!-- <=120 words. What the paper found, in the paper's own terms, with its own uncertainty. -->

## 3. Why this paper was summarized

{{WHY_SUMMARIZED}}

## 4. Study design and evidence basis

{{STUDY_DESIGN}}

Evidence basis: **{{SOURCE_BASIS}}**. {{ABSTRACT_ONLY_NOTE}}
<!-- ABSTRACT_ONLY_NOTE only when source_basis is abstract_only: state plainly that full text
     was not available and that conduct could not be appraised. -->

## 5. Population, setting, and sample

{{POPULATION}}

## 6. Intervention / exposure / index test / model

{{INTERVENTION}}

<!-- Omit fields that do not apply to this paper's design (e.g. no index test for an RCT) —
     state "not applicable to this design" rather than leaving the section blank. -->

## 7. Comparator or reference standard

{{COMPARATOR}}

## 8. Outcomes and main results

{{OUTCOMES}}

<!-- One block per outcome/result the extraction carries, each with its effect estimate,
     interval, and direction exactly as extracted — never re-rounded, never re-interpreted. -->

## 9. Methods quality and risk of bias

{{APPRAISAL_SUMMARY}}

<!-- If appraisal was skipped: state which reason (`--no-appraise` | abstract-only | no
     supported tool) instead of silently omitting this section. -->

## 10. Limitations

{{LIMITATIONS}}

## 11. Practical takeaways

{{TAKEAWAYS}}

<!-- Only claims the paper itself supports, labeled as single-study evidence. No clinical
     recommendation beyond what the paper's own discussion/conclusion states. -->

## 12. What not to conclude from this paper alone

{{DO_NOT_CONCLUDE}}

<!-- At least one bullet for any fulltext, clinical/journal-club-purpose summary
     (references/schema/14-single-paper-summary.md). -->

## 13. Provenance

| | |
|---|---|
| Evidence id | {{EVIDENCE_ID}} |
| Extraction | {{EXTRACTION_PATH}} |
| Appraisal | {{APPRAISAL_PATH_OR_SKIP_REASON}} |
| Generated | {{GENERATED_AT}} |
| Verification | {{VERIFICATION_STATUS}} |
