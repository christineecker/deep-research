# Report optimization plan

This plan proposes structural and content improvements for `templates/report.md` without
implementing them yet. The goal is to improve readability, auditability, and usefulness for
follow-on grant proposals, papers, and secondary analyses while preserving the verifier-required
section discipline.

## Goals

- Make the report easier to scan without weakening its evidence trail.
- Preserve all required numbered sections.
- Keep evidence claims, appraisal judgements, limitations, and hypotheses clearly separated.
- Make the "New insights & hypotheses" section more actionable for future research planning.
- Keep `report.md`, `report.qmd`, `references/reporting.md`, and verifier expectations aligned
  before any template changes are made.

## Proposed target structure

```text
0. Title block / status
1. Short answer and plain-language summary
2. Evidence at a glance
3. Question and protocol
4. Methods
5. PRISMA flow and screening log
6. Characteristics of included studies
7. Risk of bias and appraisal
8. Results by outcome
9. Certainty of evidence (GRADE)
10. Conflicts and inconsistencies
11. Evidence gaps
12. Limitations of this review
13. New insights & hypotheses — NOT EVIDENCE
14. Unobtainable / quarantined evidence
15. References
16. Provenance
```

## Recommended changes

### 1. Put the short answer first

Move the current `Short answer` line to the top of section 1, before the plain-language summary.
Readers should see the bottom line before the supporting summary.

### 2. Add an evidence-at-a-glance section

Add a compact table before the detailed protocol and results.

Suggested columns:

```text
Outcome | Studies / participants | Direction of effect | Best estimate / range | Certainty | Main limitation
```

This section should summarize only findings that are fully supported later in the report.

### 3. Consolidate methods into subsections

Keep one methods section, but divide it internally:

```text
4.1 Search
4.2 Screening
4.3 Retrieval and quarantine
4.4 Extraction
4.5 Appraisal and GRADE
```

This reduces scattering while keeping the audit trail explicit.

### 4. Strengthen appraisal reporting

Rename `Risk of bias / quality` to `Risk of bias and appraisal`.

Suggested table columns:

```text
Study | Design | Tool | Key domain concerns | Overall judgement | Evidence basis
```

For `tool: "none"` records, state why no in-scope tool applies. Do not force those records into
a pseudo-domain or quality grade.

### 5. Add a protocol deviation table

Replace or supplement the current free-text deviation placeholder with:

```text
Deviation | Reason | Affected records/outcomes | Likely impact
```

This makes resumed runs and protocol departures easier to audit.

### 6. Add funding and COI synthesis

Add a required subsection or table summarizing funding and conflicts of interest across included
studies.

Suggested columns:

```text
Funding / COI pattern | Studies affected | Possible interpretive impact
```

This should feed into publication-bias and certainty judgements where relevant.

### 7. Make harms explicit

Require either a harms/adverse-events outcome subsection or a stated reason harms were not
assessed.

Suggested rule:

```text
8.x Harms and adverse events
```

or:

```text
Harms were not assessed because ...
```

### 8. Keep and improve new insights & hypotheses

Do not remove this section. It is especially valuable for grant proposals, new papers, and
secondary analyses. Instead, make it more structured and actionable while preserving the hard
wall that says it is not evidence.

Suggested opening subsection:

```text
### Opportunity map

| Opportunity | Evidence trigger | Gap addressed | Potential study/analysis | Feasibility | Priority |
```

Suggested hypothesis shape:

```text
### H1 — Hypothesis title

- Idea, explicitly speculative:
- What prompted it:
- Mechanism or rationale:
- Testable prediction:
- Proposed analysis or study design:
- Minimum data needed:
- What would falsify it:
- Grant/paper angle:
```

Hypotheses should remain conditional, falsifiable, and clearly separated from the evidence
sections.

### 9. Move hypotheses after limitations

Place `New insights & hypotheses — NOT EVIDENCE` after limitations. This makes the section read
as a deliberate forward-looking interpretation after the evidence, certainty, gaps, and
limitations have been stated.

### 10. Expand provenance links

Add direct links to generated artifacts:

```text
protocol.md
search logs
evidence-table.md
prisma.json / prisma.md
missing.md
verification.json
result.json
OKF bundle path
```

This makes the final report easier to audit from a single document.

## Verification considerations

Before implementing this plan, update the relevant verifier/reporting assumptions together:

- `templates/report.md`
- `templates/report.qmd`
- `references/reporting.md`
- `scripts/verify.py` section checks, if section numbering or headings are enforced
- Any tests that assert section names, ordering, or required content

Preserve these constraints:

- Every required section is present.
- Every evidence claim is cited.
- Abstract-only claims are labelled at point of use.
- GRADE wording matches certainty.
- Hypotheses are conditional and testable.
- No hypothesis is phrased as an established finding.
- Quarantined evidence is excluded from synthesis.

## Implementation sequence

1. Update the Markdown report template.
2. Mirror the structure in the Quarto template.
3. Update `references/reporting.md` so the report contract matches the templates.
4. Update verifier checks only where the current section ordering or headings are hard-coded.
5. Add or adjust tests for section presence, hypothesis-wall wording, and artifact links.
6. Run the full test suite.

