---
title: "{{TITLE}}"
project: {{PROJECT}}
selection_mode: {{SELECTION_MODE}}
generated: { by: deep-research/0.1, at: {{GENERATED_AT}} }
status: {{SET_STATUS}}   # final | provisional (any verifier fail, or unresolved identifiers)
---

<!-- A set of single-paper summaries plus an optional bounded orientation layer. NOT a
     literature review: no PRISMA flow, no pooled effect, no GRADE table, no synthesis-style
     certainty verdict, unless the full review pipeline produced those artifacts separately. -->

# {{TITLE}}

> **This is a set of {{PAPER_COUNT}} individual paper summaries, not a synthesized body of
> evidence.** {{STATUS_NOTE}}

## 1. Set description

{{SET_DESCRIPTION}}

## 2. Selection basis and limits

{{SELECTION_BASIS}}

<!-- explicit: list the identifiers as given. question/topic: state the query, database,
     retrieval timestamp, and limit, and label it "bounded candidate discovery, not a
     systematic search" (references/search-strategy.md). -->

## 3. Papers included

| Evidence id | Title | Design | Appraisal |
|---|---|---|---|
| {{ROW_EVIDENCE_ID}} | {{ROW_TITLE}} | {{ROW_DESIGN}} | {{ROW_APPRAISAL}} |

{{FAILED_IDENTIFIERS_NOTE}}
<!-- Only when failed_identifiers is non-empty: which requested identifiers did not reach a
     verified summary, and at which stage. -->

## 4. Optional overview

{{OVERVIEW}}
<!-- "Not produced for this set." when overview.enabled is false. Otherwise: only descriptive
     comparisons already present in the per-paper summaries below (design, population,
     intervention, outcomes measured). Never a pooled estimate, certainty rating, or consensus
     statement. -->

## 5. Per-paper summaries

{{PER_PAPER_SUMMARIES}}
<!-- One `templates/single-paper-summary.md` §1-§13 body per included paper, in
     included_evidence_ids order. -->

## 6. Cross-paper cautions

{{CROSS_PAPER_CAUTIONS}}

<!-- Standing reminder, not a finding: these papers were not screened, appraised as a body, or
     synthesized against each other. Conflicting findings across papers are stated as
     conflicting, never resolved here. -->

## 7. Provenance

| | |
|---|---|
| Set id | {{SET_ID}} |
| Selection mode | {{SELECTION_MODE}} |
| Included | {{INCLUDED_COUNT}} |
| Failed | {{FAILED_COUNT}} |
| Generated | {{GENERATED_AT}} |
| Verification | {{VERIFICATION_STATUS}} |
