# synthesis.md — synthesis manual

Stage 7 of `SKILL.md` "Pipeline". Runs in **main only**, on the main model, from `corpus.jsonl`,
`workspace/extractions/*.json`, and `workspace/appraisals/*.json`. Produces the synthesis sections
of `outputs/report.md` and the inputs to stage 8 verification.

Governing invariants (`SKILL.md` "Invariants"):

- Conflicts are surfaced **with an explanation**, never averaged away.
- "New insights / hypotheses" are strictly separated from "what the evidence shows".
- Abstract-only vs full-text is tagged on every claim.
- Null and negative findings are actively sought, not just whatever surfaced.

Hard constraint: **this pipeline does not compute pooled effect estimates.** No meta-analysis is
run, no random-effects model is fitted, no I² is calculated by us. Where a pooled estimate or an
I² appears in the report it is *extracted from a published meta-analysis in the corpus and
attributed to it*, never produced here. Structured synthesis without meta-analysis is the method;
SWiM (Synthesis Without Meta-analysis) is the reporting frame.

---

## 1. Unit of synthesis

Synthesise per **outcome × timepoint × comparison**, matching the `outcomes[]` entries of the
extraction record (schema §7). Never per study, never "overall".

```text
synthesis unit := <population slice> × <intervention vs comparator> × <outcome construct> × <timepoint band>
```

Timepoint bands are declared in the protocol before synthesis (e.g. post-treatment ≤ 3 months;
short-term 3–6; medium 6–12; long > 12). A study contributing two timepoints contributes to two
units. Declare the bands in the report; do not invent them per outcome to make results line up.

---

## 2. Effect-direction tabulation

The default synthesis method. This is vote counting **done properly**: the tabulated unit is
direction *plus* magnitude *plus* precision *plus* study weight-proxy *plus* risk of bias — never
a bare count of "positive studies".

### The table

One row per study within a synthesis unit:

| Column | Source | Notes |
|---|---|---|
| Study | `corpus record` | first author, year, `evidence_id` |
| Design | `extraction.design` | drives the appraisal tool |
| N | `extraction.n_total`, `n_arms` | `null` if not stated — never estimated |
| Population slice | `extraction.population` | flags indirectness |
| Comparator | `extraction.comparator` | active vs inactive is a conflict axis (§5) |
| Outcome + instrument | `outcomes[].name` | different instruments are noted, not merged |
| Timepoint | `outcomes[].timepoint` | mapped to a declared band |
| Measure | `outcomes[].effect_measure` | SMD / MD / RR / OR / HR / … |
| Estimate | `outcomes[].effect` | in the reported measure's units |
| Interval | `ci_low`, `ci_high` | 95% unless `extractor_notes` says otherwise |
| Direction | `outcomes[].direction` | `favors_intervention` / `favors_comparator` / `null_effect` / `unclear` |
| Precision | derived | see below |
| Risk of bias | `appraisal.overall_judgement` | tool named |
| Basis | `extraction.evidence_basis` | `fulltext` / `abstract_only` — printed, always |

Derived precision, computed from the reported interval only:

| Precision | Rule |
|---|---|
| `precise` | Interval excludes the null **and** both bounds fall on the same side of the pre-declared minimal important difference (MID), where a MID is declared |
| `imprecise` | Interval excludes the null but spans the MID, or the interval is wide relative to the effect |
| `null-compatible` | Interval includes the null |
| `unreported` | No interval reported — the row still appears; it is not dropped and not treated as null |

Rules:

1. **Never convert a p-value into a direction on its own.** `p = 0.06` with a point estimate
   favouring intervention is `favors_intervention` + `null-compatible`, not "no effect".
2. **Never count a study without an interval as evidence of no effect.** `unreported` is its own
   category and is reported as such.
3. **Weight the reading by risk of bias and N, in prose.** "Three of five trials favour the
   intervention" is meaningless if the three are `high` risk with n≈30 and the two null trials are
   `low` risk with n≈600. Say that instead of the count.
4. **Do not sum across different effect measures.** An OR and an SMD share a direction column and
   nothing else.
5. **Report the count only alongside the qualifiers.** Acceptable:
   "4/6 trials show point estimates favouring X; the two largest and least biased are
   null-compatible." Unacceptable: "the majority of studies were positive."

### Presentation choices

| Presentation | Use when | Do not use when |
|---|---|---|
| Effect-direction plot (direction arrows sized/annotated by N, grouped by outcome and by risk of bias) | Heterogeneous measures, few studies, mixed designs — the default here | You have homogeneous effect sizes and a defensible pooled estimate (you don't — see §3) |
| Harvest plot | Studies vary along an explicit moderator (dose, setting, population) and the point is the distribution across that moderator | The moderator was chosen post hoc to make a pattern appear |
| Albatross-style plot (p-value against sample size, contours by effect size) | Studies report only p-values and N, effect sizes not extractable or not comparable | Effect sizes and intervals *are* available — plot them |
| Forest plot **without** a pooled diamond | Effect sizes share a measure and are visually comparable | Do not add a diamond. A diamond asserts a pooled estimate this pipeline did not compute |
| Pooled estimate | Never, from this pipeline | Always |

A published meta-analysis's forest plot or pooled estimate may be **quoted and cited**. Label it as
the review's result, with its AMSTAR-2 rating alongside.

---

## 3. When not to pool

Even where pooling would be technically possible, it is out of scope. Where it would be *wrong*,
state why — the reasons themselves are synthesis findings.

| Reason not to pool | Signal |
|---|---|
| Clinical heterogeneity | Populations differ on effect-modifying characteristics (age band, severity, comorbidity, care setting, country/health-system) |
| Different comparators | Waitlist vs treatment-as-usual vs active comparator. Pooling these estimates a quantity nobody wants |
| Different outcome constructs | "Depression" measured by CDI-2, PHQ-9, and clinician-rated CDRS-R remission are not one outcome; symptom severity and remission are not one outcome |
| Different timepoints | Post-treatment and 24-month follow-up answer different questions |
| Different intervention content or intensity | 6 vs 20 sessions; delivered by specialists vs lay providers |
| Different analysis targets | ITT vs per-protocol; adjusted vs unadjusted; cluster trials not accounting for clustering |
| Overlapping samples | Multiple reports of one trial, or systematic reviews sharing primary studies — double-counting |
| Serious/critical risk of bias dominating | ROBINS-I `critical` studies are excluded from effect synthesis entirely |
| Too few studies | With 2–3 studies a pooled estimate mostly reports its own model assumptions |

Standard sentence for the report:

```text
Results were not pooled. The included studies differ in comparator (waitlist, treatment as
usual, active therapy) and in outcome construct (continuous symptom scales vs dichotomous
remission), so a single pooled estimate would not correspond to an answerable question.
Findings are tabulated by direction, magnitude, and precision instead.
```

---

## 4. Heterogeneity

Three kinds. Name which kind you mean, every time.

| Kind | What varies | Where it comes from |
|---|---|---|
| Clinical | Participants, interventions, comparators, outcomes, settings | Extraction records |
| Methodological | Design, risk of bias, analysis model, follow-up length, attrition handling | Appraisal records + extraction |
| Statistical | Variation in observed effects beyond chance | Only ever quoted from a published meta-analysis |

### Statistical heterogeneity metrics — quoting rules

| Metric | Meaning | Rule here |
|---|---|---|
| I² | Percentage of variability across studies due to heterogeneity rather than chance | Quote with its CI when the source gives one; **I² is not an absolute measure of heterogeneity** and depends on study precision |
| tau² | Between-study variance on the effect scale | More informative than I² for magnitude; quote when reported |
| Prediction interval | Range within which the effect in a *new* setting is expected to lie | The most decision-relevant statistic; quote whenever the source reports it, and prefer it to the pooled CI when describing what the evidence implies for a new population |

Cochrane's rough I² bands, to be used as bands and never as thresholds:
0–40% may not be important; 30–60% moderate; 50–90% substantial; 75–100% considerable.
Overlapping on purpose. Do not write "I² = 62%, therefore substantial heterogeneity" as if it were
a decision rule; write what varies clinically and methodologically alongside it.

### Describing heterogeneity in prose

```text
BAD  "Studies were heterogeneous (I2 = 71%)."
GOOD "The five trials vary clinically (three in specialist clinics, two in schools) and
      methodologically (two are cluster-randomized without clustering adjustment). The
      published meta-analysis of the same trials reports I2 = 71% (95% CI 42-85) with a
      prediction interval of SMD -0.91 to +0.14, i.e. a new trial could plausibly show
      benefit or no effect."

BAD  "Heterogeneity was explored by subgroup analysis and disappeared in adults."
GOOD "The one moderator declared in the protocol was age band. Effects favour the
      intervention in adult samples and are null-compatible in adolescent samples; this
      is based on 3 vs 2 studies and is a hypothesis, not a demonstrated subgroup effect
      (see 'New insights')."
```

Subgroup differences observed in the tabulation are **hypotheses** unless the source itself
performed a formal test of interaction. They cross the wall in §7.

---

## 5. Conflict handling

`SKILL.md` "Invariants": conflicts are surfaced with an explanation, never averaged away. A conflict is not a
defect in the report; an unexplained conflict is.

### Definition

Two studies within one synthesis unit conflict when their intervals are largely non-overlapping
and their directions differ, **or** when one is `precise` and excludes an effect the other
`precise` result asserts. Same-direction studies with different magnitudes are not a conflict;
they are a gradient (see §5 taxonomy, dose/intensity).

### Taxonomy of why studies disagree

| # | Cause | Evidence you would see |
|---|---|---|
| C1 | Population | Different age band, severity, comorbidity, baseline risk; effect modification plausible on clinical grounds |
| C2 | Dose / intensity / fidelity | Sessions, duration, provider training, adherence; effects track intensity monotonically |
| C3 | Comparator | Waitlist inflates effects relative to active comparators; "usual care" differs across health systems |
| C4 | Outcome definition | Different instruments, different thresholds for a dichotomous outcome, self-report vs observer-rated |
| C5 | Timepoint | Effect present post-treatment, absent at follow-up (or vice versa) |
| C6 | Risk of bias | The divergent studies cluster by RoB2/ROBINS-I judgement; unblinded subjective outcomes vs blinded assessment |
| C7 | Analysis choices | ITT vs per-protocol, complete-case vs imputation, adjusted vs unadjusted, unaccounted clustering, post-hoc covariates |
| C8 | Publication / selective reporting | The favourable results are small, industry-funded, or from unregistered trials; registered outcomes missing from the report |
| C9 | Chance / imprecision | Small studies, wide intervals; the "conflict" is compatible with a single underlying effect |

### Attribution procedure

Work the list in order and stop at the first cause the data actually support. Record the step at
which you stopped.

```text
1. Restate both results with estimate + interval + N + risk of bias + evidence_basis.
2. C9 first: do the intervals overlap materially? If yes -> imprecision, not conflict.
   Say so and stop. Do not manufacture an explanation for noise.
3. C4/C5: are the outcomes and timepoints actually the same construct and band?
   If not -> not a conflict; they are different synthesis units. Re-file them.
4. C3: are the comparators the same? Tabulate effect by comparator type.
5. C6/C7: do the divergent results cluster by risk-of-bias judgement or by analysis
   choice? Check whether the pattern holds across all studies in the unit, not just
   the two in question.
6. C1/C2: is there a clinical moderator, declared in the protocol, that separates them?
   An undeclared moderator that fits is a hypothesis, not an explanation.
7. C8: check registry entries and protocols (see section 6).
8. If none of the above is supported: say the conflict is unexplained. That is a
   legitimate, reportable finding and feeds GRADE inconsistency.
```

### Reporting a conflict

```text
BAD  "Findings were mixed, with an overall trend toward benefit."
BAD  "Averaging across studies suggests a small positive effect."
BAD  "The negative trial was likely underpowered."   (asserted, not shown)

GOOD "Smith 2024 (n=240, RoB2 some concerns, waitlist comparator) reports SMD -0.41
      (95% CI -0.68 to -0.14) at 12 weeks; Lee 2023 (n=612, RoB2 low, active-therapy
      comparator) reports SMD -0.05 (95% CI -0.21 to 0.11) at the same timepoint. The
      intervals barely overlap. Comparator type separates them: all three waitlist-
      controlled trials favour the intervention, both active-comparator trials are
      null-compatible (attribution: C3). No trial compares the two comparators directly,
      so this remains an observed pattern across trials, not a tested interaction."

GOOD "The two results conflict and none of the examined causes (population, comparator,
      outcome, timepoint, risk of bias, analysis) accounts for the divergence. The
      inconsistency is unexplained and the outcome is downgraded one level for
      inconsistency in GRADE."
```

---

## 6. Publication-bias signals without a meta-analysis

Funnel plots and Egger-type tests need roughly 10 or more studies and a pooled analysis; neither
is available here. Use these instead, and record the result in
`appraisal record.grade.publication_bias` (`undetected` / `suspected` / `strongly_suspected`).

| Signal | How to check with what this pipeline has | Reads as |
|---|---|---|
| Small-study effects | Sort the effect-direction table by N. Do the largest effects sit with the smallest studies? | `suspected` when the pattern is clean and monotone |
| Registry vs publication | Search ClinicalTrials.gov / ICTRP / PROSPERO for trials matching the protocol's PICO; compare against what was found. Completed trials with no publication years after completion | `suspected`; `strongly_suspected` when several large ones are unpublished |
| Protocol vs report outcome switching | For each included trial with a registration or published protocol, compare the pre-specified primary outcome with the reported primary outcome. Promotion, demotion, omission, or a changed timepoint | Downgrade for risk of bias (RoB 2 domain 5) **and** flag selective reporting |
| Grey literature absence | Did the search reach preprints, theses, conference proceedings, regulatory documents? At `narrow`/`medium` scope it did not | State the limitation; `suspected` is not automatic, but "not assessable" must be said |
| Funding / sponsorship asymmetry | Cluster `extraction.funding` and `extraction.coi` against direction | Reportable pattern; name it, do not accuse |
| Language and database restriction | Protocol filters (`[la]`, single-database search) | Limitation on completeness; report as such |
| Duplicate/salami publication | Same trial registration or overlapping author-site-period across records | Correct the corpus; do not count twice |

When none of these can be assessed, write "publication bias could not be assessed" — not
"no evidence of publication bias".

---

## 7. The hard wall

Two sections, never mixed, never adjacent in the same paragraph, never in the same sentence.

```text
======================================================================
  WHAT THE EVIDENCE SHOWS          |  NEW INSIGHTS / HYPOTHESES
  every sentence cites an          |  every sentence is labelled as a
  evidence_id retrieved this run   |  hypothesis and states what would
                                   |  test it. Citations here point at
                                   |  what motivates the idea, and must
                                   |  not be phrased as supporting it.
======================================================================
```

### Permitted verb forms

| Side | Permitted | Forbidden |
|---|---|---|
| Evidence | *reduces / probably reduces / may reduce / the evidence is very uncertain about* (GRADE wordings, `references/appraisal.md` §8); *was reported*, *was measured*, *X trials found*, *the interval excludes/includes*, *no study reported* | *proves*, *confirms*, *establishes*, *clearly shows*, *it is well known*, *suggests that X works* (unanchored), *likely* without a certainty rating |
| Hypotheses | *we hypothesise*, *a plausible mechanism would be*, *this pattern would be consistent with*, *an untested explanation is*, *this could be tested by* | any GRADE wording, *evidence indicates*, *studies show*, *data support*, *is associated with* (asserted), any bare present-tense factual claim |

Additional rules:

1. Every hypothesis sentence carries an explicit marker (a `Hypothesis:` prefix, or the section
   heading plus a hedged verb). The verifier check `C-HYPOTHESIS-WALL` fails a report where a
   hypothesis is phrased as an established finding (schema §9).
2. Every hypothesis states **what would test it** — a design, a population, an outcome, a
   comparison. A hypothesis with no test is speculation and is dropped.
3. A hypothesis may not be cited later in the report as if it were a finding.
4. Gaps belong on the evidence side ("no study reported X") — they are observations about the
   corpus. Explanations for gaps belong on the hypothesis side.

### Worked examples

```text
BAD   "The evidence suggests CBT works better in adolescents, probably because
       cognitive flexibility is higher at that age."
       (fuses an under-supported claim with an untested mechanism)

GOOD  Evidence: "Three trials in adolescent samples report point estimates favouring
       CBT (SMD -0.41, -0.33, -0.29); the two adult trials are null-compatible.
       No trial tested an age-by-treatment interaction. Low certainty (downgraded for
       risk of bias and imprecision)."
      Hypotheses: "Hypothesis: the age pattern above reflects effect modification by
       developmental stage rather than chance or comparator differences. Testable by a
       trial stratified by age band with a pre-specified interaction test, or by an
       individual-participant-data meta-analysis of the five trials."

BAD   "Given the mechanism, longer courses are likely more effective."
GOOD  "Hypothesis: effect size increases with number of sessions. Motivated by the
       ordering of the four trials by session count (6, 8, 12, 16) in the effect-direction
       table; this is an across-trial observation with no within-trial dose comparison
       and is confounded with year and setting. Testable by a dose-comparison trial."

BAD   "No study examined long-term outcomes, which likely means effects fade."
GOOD  Evidence: "No included study reported outcomes beyond 24 weeks."
      Hypotheses: "Hypothesis: post-treatment gains attenuate by 12 months. Untested;
       no included study measured beyond 24 weeks."
```

---

## 8. Provisional syntheses

A synthesis is **PROVISIONAL** whenever any of the following holds (`SKILL.md` "Pipeline" failure
semantics, §6):

| Trigger | Source |
|---|---|
| Any included record has `fulltext.status == "missing"` (quarantined) | `corpus.jsonl` / `missing.md` |
| Any included record is `abstract_only`, including truncation-detected HTML | `corpus record.fulltext` |
| Any task ended `failed` or `blocked` in a stage that feeds synthesis | `taskboard.jsonl` |
| The no-progress guard fired and the run moved to verification early | `engine.log` |
| A required connector was unauthorized at `max` scope | `engine.log` |
| Any verifier check has `status: "fail"` | `outputs/verification.json` |

Requirements when provisional:

1. The word **PROVISIONAL** appears in the report title block, not only in a footnote.
2. A dedicated subsection lists every quarantined record — PMID, DOI, PMCID, title, journal, and
   the ladder rung reached — mirroring `missing.md`, and states the resume path (drop PDFs into
   the run's `inbox/` and rerun).
3. Each affected synthesis unit states the shortfall *locally*, at the point of the claim, not
   only in a global caveat.
4. The direction of the possible bias is stated where it can be reasoned about, and stated as
   unknown where it cannot:
   ```text
   GOOD "Two of seven trials for this outcome could not be obtained (PMID 11111111,
         22222222; both paywalled, quarantined). Both are registered as industry-funded
         and larger than the included trials, so the tabulated direction may overstate
         benefit. Certainty is not raised above low while they are outstanding."
   BAD  "Two studies were unavailable but this is unlikely to change the conclusions."
   ```
5. Quarantined and abstract-only evidence never contributes to an upgrade in GRADE and never
   supports a `high` certainty rating.
6. Hypotheses generated from a provisional corpus say so.

A provisional synthesis is still delivered. It is never silently completed, and never withheld.
