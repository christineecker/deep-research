# question-frameworks.md — question-framework router

Stage 0/1 of `SKILL.md` ("Ask, if not already known" step 1; Stage 1 "Protocol"). This file is
the routing table and per-framework field contract behind `question_framework` /
`framework_fields` in `config.json`. It generalizes the PICO/PECO-only version of Stage 0 step 1
to the six frameworks the plan (`SCIENTIFIC_FRAMEWORKS_OPTIMIZATION_PLAN.md`, "Question Framework
Roadmap") tracks, without breaking any run that only ever used PICO/PECO.

This is a doc-and-prompt-discipline mechanism, not a code-enforced schema: this pipeline has no
`config.json` schema doc and no script validates protocol content today (the same is true of the
pre-existing PICO/PECO fields). "Validation" here means the coordinator applies the checklist
below and asks the user rather than guessing, exactly as it already does for PICO/PECO's
population/intervention/comparator/outcome.

---

## 1. Pick the framework from the question type

| Question type | Framework | Required fields |
|---|---|---|
| Intervention effectiveness/efficacy, clinical or health-systems | **PICO** | Population, Intervention, Comparator, Outcome |
| Etiology, risk factors, environmental/occupational exposure, prognosis with an implicit comparator | **PECO** | Population, Exposure, Comparator, Outcome |
| Prevalence, incidence, association, or any exposure-outcome question with no meaningful comparator | **PEO** | Population, Exposure, Outcome |
| Scoping review: mapping the extent/nature of evidence rather than answering an effect question | **PCC** | Population, Concept, Context |
| Qualitative or mixed-methods question about experience, meaning, or process | **SPIDER** | Sample, Phenomenon of Interest, Design, Evaluation, Research type |
| Service delivery, implementation, or evaluation question | **SPICE** | Setting, Perspective, Intervention, Comparison, Evaluation |

Default to **PICO** for a comparator-bearing intervention question and **PECO** for an
etiological/risk-factor question with no intervention — this preserves every existing run's
behavior unchanged, since those two were the only frameworks this pipeline previously supported.
Pick a different framework only when the question genuinely does not fit PICO/PECO's shape (no
comparator at all, a scoping aim, a qualitative aim, or a service-evaluation aim) — do not force a
PEO/PCC/SPIDER/SPICE question into PICO/PECO columns just because that is the older path, and do
not reach for one of the newer frameworks when PICO/PECO already fits.

If the question is ambiguous between two frameworks (e.g. an intervention question with no
comparator could be PICO with `Comparator: none` or PEO), prefer keeping the comparator column and
writing `"none (single-arm / descriptive)"` — matching `templates/protocol.md`'s existing
guidance — over switching frameworks, unless the review is explicitly scoping or qualitative.

## 2. Per-framework field definitions

### PICO (default, intervention effectiveness)

| Field | Definition |
|---|---|
| Population | Who — condition, setting, demographics |
| Intervention | What is being evaluated |
| Comparator | Control condition; `"none (single-arm / descriptive)"` if there truly is none |
| Outcome | What is measured, primary and secondary |

### PECO (default, etiology/exposure/prognosis)

| Field | Definition |
|---|---|
| Population | Who |
| Exposure | The exposure, risk factor, or prognostic factor |
| Comparator | Unexposed/reference group; `"none"` only if genuinely absent |
| Outcome | What is measured |

### PEO (exposure-outcome, no comparator)

| Field | Definition |
|---|---|
| Population | Who |
| Exposure | The exposure or characteristic of interest |
| Outcome | What is measured |

Use PEO instead of PECO when the review question is genuinely comparator-free (e.g. "what is the
prevalence of X among people exposed to Y") rather than because a comparator group is merely hard
to find in the literature — the latter is a limitation to state in the protocol, not a reason to
drop the field.

### PCC (scoping reviews)

| Field | Definition |
|---|---|
| Population | Who |
| Concept | The core concept/practice/phenomenon being mapped |
| Context | Setting, geography, timeframe, cultural context |

A PCC protocol does not carry inclusion/exclusion criteria framed as an effect estimate — scoping
reviews map the extent and nature of evidence, and `templates/protocol.md` §8 (analysis plan)
should say so explicitly rather than defaulting to effect-direction tabulation language.

### SPIDER (qualitative/mixed-methods)

| Field | Definition |
|---|---|
| Sample | Who/what is sampled (not a random population sample — a deliberately chosen one) |
| Phenomenon of Interest | The experience, belief, or behavior under study |
| Design | The study design(s) eligible (interviews, focus groups, ethnography, survey, ...) |
| Evaluation | What outcome/evaluation is of interest |
| Research type | Qualitative, quantitative, or mixed |

A SPIDER protocol routes appraisal toward the CASP qualitative checklist (`references/appraisal.md`
§8) for the qualitative component, not RoB2/ROBINS-I/NOS.

### SPICE (service delivery/implementation/evaluation)

| Field | Definition |
|---|---|
| Setting | Where the service/intervention takes place |
| Perspective | Whose perspective (patient, provider, system) |
| Intervention | The service/intervention/policy being evaluated |
| Comparison | Alternative service model, or `"none"` |
| Evaluation | The outcome/evaluation measure |

## 3. Recording the framework

Record in `config.json`, alongside the existing `filters` object (`SKILL.md` step 5):

```json
{
  "question_framework": "PICO",
  "framework_fields": {
    "population": "Adolescents 12-17y with moderate MDD, outpatient",
    "intervention": "Manualized group CBT",
    "comparator": "Waitlist control",
    "outcome": "Depressive symptom severity"
  }
}
```

`question_framework` is one of `PICO` \| `PECO` \| `PEO` \| `PCC` \| `SPIDER` \| `SPICE`.
`framework_fields` keys are the lower-cased field names from §2 above for the chosen framework —
no more, no fewer. A run that never sets `question_framework` is read as `PICO` if it has a
`comparator`, otherwise `PECO`, preserving every run written before this router existed.

`templates/protocol.md` §2's table header and rows should use the chosen framework's field names
(the template as shipped shows PICO/PECO columns; swap the row labels for a PEO/PCC/SPIDER/SPICE
protocol using the field lists above — do not invent columns not listed in §2).

## 4. Validation checklist (ask, don't guess)

Applied once, at Stage 0 step 1 (`SKILL.md`), same moment PICO/PECO was previously confirmed:

- Every required field for the chosen framework (§2) has either a concrete answer or an explicit
  `"none"`/`"not applicable"` with a one-line reason — never leave a required field blank in
  `framework_fields` and never invent a value the user did not give or the paper's abstract did
  not already establish for a **rescoping**, not an initial framing.
- If the user's phrasing does not obviously map to one framework, restate the question back using
  the framework you picked and ask them to confirm or redirect — the same "restate as PICO/PECO
  before proceeding" discipline `SKILL.md` step 1 already requires, generalized to all six.
- A framework switch mid-run (the protocol was written as PICO, then the user redirects to a
  qualitative sub-question) is a deviation: log it in `templates/protocol.md` §9 the same as any
  other protocol change, naming the old and new framework.
- Missing-field warnings are not a hard stop outside `systematic`/`max` gates (`SKILL.md`
  "Profiles") — at `fast` (`gates: none`) there is no gate to catch it later, so get it right at
  Stage 0, same rule that already applies to PICO/PECO's inclusion/exclusion criteria.

## 5. Search-string construction

`references/search-strategy.md` documents axis-based query construction (population-led,
intervention/exposure-led, outcome-led, design hedges). The axis set is generated from
`framework_fields`, not hardcoded to PICO's four elements:

| Framework | Axes to cover across the 4-8 queries |
|---|---|
| PICO | population, intervention, comparator (if not "none"), outcome |
| PECO | population, exposure, comparator (if not "none"), outcome |
| PEO | population, exposure, outcome |
| PCC | population/context, concept — scoping searches skew broader and less outcome-constrained; do not add an outcome axis PCC does not have |
| SPIDER | sample, phenomenon of interest, design (qualitative method terms: interview*, "focus group*", qualitative, ethnograph*), evaluation |
| SPICE | setting, perspective, intervention, comparison (if not "none"), evaluation |

The existing PICO/PECO rule — at least one query aimed at null/negative and non-significant
results (`SKILL.md` "Invariants") — still applies to every framework where an effect or
association is in question (PICO, PECO, PEO, SPICE); it does not apply to PCC, which maps
evidence rather than testing an effect.
