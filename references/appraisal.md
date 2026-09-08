# appraisal.md — critical appraisal manual

Stage 6 of `PLAN.md` §5. One subagent per paper (opus). Output conforms to the
`appraisal record` in `references/schema.md` §8. This file is the human/agent-facing rationale
behind those fields: which tool, which domains, which vocabulary, and how to say "we don't know"
without laundering it into "low risk".

Governing invariants (`PLAN.md` §6):

- Abstract-only evidence is **never** appraised as if full text.
- Absence of reporting is **never** evidence of low risk.
- Every domain judgement carries a rationale, `unclear` included.

---

## 1. Tool selection

Driven by `extraction record.design` (schema §7) plus `corpus record.article_types`.
Pick exactly one tool. Record it in `appraisal record.tool`.

| Study design (as extracted) | Tool | `tool` enum | Judgement scale |
|---|---|---|---|
| Individually randomized parallel-group trial | RoB 2 | `RoB2` | low / some concerns / high |
| Cluster-randomized trial | RoB 2, cluster variant | `RoB2` | low / some concerns / high |
| Crossover trial | RoB 2, crossover variant | `RoB2` | low / some concerns / high |
| Quasi-randomized (alternation, date of birth, record number) | ROBINS-I — allocation is not random | `ROBINS-I` | low / moderate / serious / critical / no information |
| Non-randomized study of an intervention/exposure with a comparator (prospective or retrospective cohort of an intervention, controlled before-after, interrupted time series with comparator, database/claims study) | ROBINS-I | `ROBINS-I` | low / moderate / serious / critical / no information |
| Etiological / prognostic cohort study (no intervention framing) | Newcastle-Ottawa, cohort form | `Newcastle-Ottawa` | star count, max 9 |
| Case-control study | Newcastle-Ottawa, case-control form | `Newcastle-Ottawa` | star count, max 9 |
| Systematic review, with or without meta-analysis | AMSTAR-2 | `AMSTAR-2` | high / moderate / low / critically low |
| Cross-sectional study, case series, case report, qualitative study, diagnostic accuracy study, animal study, modelling study | `none` — no in-scope tool | `none` | — |
| Narrative review, editorial, commentary, letter, conference abstract with no methods, guideline document | `none` — do not appraise | `none` | — |
| Preprint with a full methods section | Tool by design, as above, **plus** the preprint flag | per design | per design |
| Preprint without a methods section, abstract-only record | `none` — do not appraise | `none` | — |

### The `none` case

`tool: "none"` is a legitimate, common outcome, not a failure. Schema §8 requires `domains: []`
and an `overall_judgement` — use `"unclear"` — and the reason must be stated. Because `domains`
must be empty for `tool: "none"`, put the reason in the record's rationale slot the prompt
provides; never leave it implicit.

```json
{
  "schema_version": 1,
  "pmid": "12345678",
  "evidence_id": "pmid:12345678",
  "tool": "none",
  "domains": [],
  "overall_judgement": "unclear",
  "grade": null,
  "evidence_basis": "abstract_only"
}
```

Do not:

- reach for RoB 2 because the abstract says "randomized" while no methods text exists;
- appraise a narrative review with AMSTAR-2 (AMSTAR-2 presupposes a systematic review; applying
  it to a narrative review manufactures a "critically low" that reads as a quality verdict on
  work that never claimed to be systematic — say "not a systematic review, not appraised");
- appraise a guideline with any of these four. If guideline quality matters to the question,
  say so as a gap; AGREE II is the recognised instrument and is out of scope for this pipeline.

Cross-sectional and diagnostic-accuracy designs are `none` here **because this pipeline carries
no instrument for them**, not because they are unappraisable. Recognised instruments exist
(e.g. QUADAS-2 for diagnostic accuracy). State the absence in the report as a limitation rather
than substituting a mismatched tool.

---

## 2. RoB 2 — randomized trials

Five domains, canonical order. Store them in `domains[]` in this order.

| # | Domain | Concerns |
|---|---|---|
| 1 | Bias arising from the randomization process | Sequence generation, allocation concealment, baseline imbalance suggesting a broken process |
| 2 | Bias due to deviations from intended interventions | Awareness of assignment, deviations arising from the trial context, appropriateness of the analysis for the effect of interest |
| 3 | Bias due to missing outcome data | Availability of outcome data, whether missingness could depend on the true value |
| 4 | Bias in measurement of the outcome | Method, assessor awareness of assignment, whether awareness could influence the measurement |
| 5 | Bias in selection of the reported result | Pre-specified analysis plan; selection from multiple eligible outcome measurements or analyses |

Domain judgements: `low` / `some_concerns` / `high` (schema tokens).

Overall judgement:

| Overall | Condition |
|---|---|
| `low` | Low risk in **all** five domains |
| `some_concerns` | Some concerns in at least one domain, high risk in none |
| `high` | High risk in at least one domain, **or** some concerns in multiple domains in a way that substantially lowers confidence in the result |

### Effect of interest

RoB 2 is assessed **per result**, for a stated effect of interest: the effect of *assignment* to
intervention (intention-to-treat) or the effect of *adhering* to intervention (per-protocol).
Domain 2 branches on this choice. Name the choice in the domain-2 rationale. Default to effect of
assignment unless the extraction shows the review question is about adherence.

RoB 2 is also assessed **per outcome**, not per trial. When a trial contributes several outcomes
with materially different risk (e.g. a blinded lab measure and an unblinded self-report), appraise
the outcome that carries the review's primary question and say in `extractor_notes`/rationale which
outcome was appraised. Do not average across outcomes.

### Signalling-question logic

Each domain is driven by signalling questions answered on:

```text
Y  = yes
PY = probably yes
PN = probably no
N  = no
NI = no information
```

Rules that must not be short-circuited:

1. Answer the signalling questions **first**, from the text; derive the domain judgement from the
   answers. Do not pick a domain judgement and reverse-engineer answers to it.
2. `PY` and `PN` carry the same algorithmic weight as `Y` and `N` — they encode inference from
   partial reporting, and the rationale must say what the inference rested on.
3. `NI` is not a synonym for `PN`. `NI` means the paper is silent. A domain resting on `NI` for a
   bias-critical question is at minimum `some_concerns`; it is never `low`.
4. Cluster-randomized trials add a domain concerning identification or recruitment of individual
   participants within clusters (recruitment bias). Crossover trials add domain content on period
   and carryover effects. Use the variant's domain names verbatim; if the variant's exact wording
   is not to hand, name the domain descriptively and say in the rationale that the cluster/crossover
   variant was applied.

---

## 3. ROBINS-I — non-randomized studies of interventions

Seven domains, grouped by when the bias is introduced. Canonical order:

| # | Stage | Domain |
|---|---|---|
| 1 | Pre-intervention | Bias due to confounding |
| 2 | Pre-intervention | Bias in selection of participants into the study |
| 3 | At intervention | Bias in classification of interventions |
| 4 | Post-intervention | Bias due to deviations from intended interventions |
| 5 | Post-intervention | Bias due to missing data |
| 6 | Post-intervention | Bias in measurement of outcomes |
| 7 | Post-intervention | Bias in selection of the reported result |

Judgements: `low` / `moderate` / `serious` / `critical` / `no information`
(schema tokens: `low`, `moderate`, `serious`, `critical`, `unclear` — use `unclear` for
"no information" and write `no information` explicitly in the rationale text).

Overall = the **worst** domain judgement. There is no averaging and no compensation: a single
`critical` domain makes the study `critical` overall regardless of the other six.

| Overall | Reading |
|---|---|
| `low` | Comparable to a well-performed randomized trial for this outcome |
| `moderate` | Sound for a non-randomized study but not comparable to a well-performed trial |
| `serious` | Important problems |
| `critical` | Too problematic to provide useful evidence; do not include in a synthesis of effects |
| `unclear` (no information) | Insufficient reporting to judge; not a pass |

### Target-trial framing (mandatory before domain 1)

ROBINS-I judges the study against the **hypothetical randomized trial it is trying to emulate**.
Write the target trial down before appraising, in the domain-1 rationale or extractor notes:

```text
Target trial
  Eligibility : who would have been randomized
  Treatment   : the intervention strategies being compared, with start time
  Assignment  : randomized at time zero, no blinding assumed
  Outcome     : the outcome and its measurement
  Follow-up   : starts at assignment; contrasts of interest (ITT-analogue)
```

Then appraise each domain as a deviation from that trial. Two corollaries:

- **Confounding domain requires a pre-specified confounder list.** Decide which confounders the
  target trial would have balanced, then ask whether the study measured and appropriately
  controlled them. "Adjusted for age and sex" is `serious` if the field's known confounders are
  baseline severity and socioeconomic status. State the list.
- **Time-zero alignment.** Misalignment of eligibility, treatment assignment, and start of
  follow-up (immortal time bias, prevalent-user bias) belongs to domains 1 and 2 and must be
  checked explicitly, not assumed absent.

A ROBINS-I V2 exists; this pipeline uses the seven-domain structure above as canonical. If a study
was appraised under a different version, say which in the rationale.

---

## 4. Newcastle-Ottawa Scale — cohort and case-control

Three groups. A maximum of one star per item except Comparability, which allows two. Maximum
9 stars. `overall_judgement` is a star-count string, e.g. `"7/9"` (schema §8).

### Cohort form

| Group | Items | Max stars |
|---|---|---|
| Selection | representativeness of the exposed cohort; selection of the non-exposed cohort; ascertainment of exposure; demonstration that the outcome of interest was not present at start of study | 4 |
| Comparability | comparability of cohorts on the basis of the design or analysis (one star for the study's principal confounder, a second for any additional confounder) | 2 |
| Outcome | assessment of outcome; was follow-up long enough for outcomes to occur; adequacy of follow-up of cohorts | 3 |

### Case-control form

| Group | Items | Max stars |
|---|---|---|
| Selection | is the case definition adequate; representativeness of the cases; selection of controls; definition of controls | 4 |
| Comparability | comparability of cases and controls on the basis of the design or analysis | 2 |
| Exposure | ascertainment of exposure; same method of ascertainment for cases and controls; non-response rate | 3 |

### Rules

1. Record each item as its own `domains[]` entry with `judgement` `yes` (star awarded) or `no`
   (no star), and the rationale saying what earned or lost it. Comparability gets two entries or
   one entry whose rationale states how many stars.
2. **Declare the Comparability confounders in the protocol or in the rationale.** The instrument
   leaves "principal confounder" to the reviewer; leaving it undeclared makes the score
   unreproducible.
3. **The NOS defines no good/fair/poor thresholds.** Any cutoff (AHRQ-style or otherwise) is the
   reviewer's, and must be declared in the report before it is used. Never present a threshold
   as if it came with the instrument.
4. A star count is not a risk-of-bias judgement. Do not translate "7/9" into "low risk of bias" in
   prose. Report the count, the group breakdown, and the specific losses.

---

## 5. AMSTAR-2 — systematic reviews

AMSTAR-2 has 16 items. Seven are **critical domains**; the confidence rating is driven by flaws
in those, weighted against non-critical weaknesses.

| Critical domain | Item |
|---|---|
| Protocol registered before commencement of the review | 2 |
| Adequacy of the literature search | 4 |
| Justification for excluding individual studies | 7 |
| Risk of bias from individual studies included in the review | 9 |
| Appropriateness of meta-analytical methods | 11 |
| Consideration of risk of bias when interpreting the results | 13 |
| Assessment of presence and likely impact of publication bias | 15 |

The remaining nine items are non-critical weaknesses. Item responses use `yes`, `partial_yes`,
`no` (schema tokens); some items additionally allow "no meta-analysis conducted", which is
recorded as `yes`-equivalent-not-applicable and must be stated in the rationale rather than scored
as a flaw.

Overall confidence in the results of the review:

| Rating | Condition |
|---|---|
| `high` | No critical flaw, and no more than one non-critical weakness |
| `moderate` | No critical flaw, more than one non-critical weakness |
| `low` | One critical flaw, with or without non-critical weaknesses |
| `critically_low` | More than one critical flaw, with or without non-critical weaknesses |

Notes:

- AMSTAR-2 rates **confidence in the review's results**, not the certainty of the underlying
  evidence and not the quality of the included primary studies. Do not restate a
  `critically_low` as "the evidence is very low certainty" — that is GRADE's job.
- AMSTAR-2 is for systematic reviews of healthcare interventions. Applying it outside that
  scope requires saying so.
- When an included systematic review overlaps the primary studies already in this corpus, note
  the double-counting risk in the synthesis (`references/synthesis.md`); appraisal does not
  resolve it.

---

## 6. Saying "unclear" honestly

The single largest failure mode of automated appraisal is converting silence into reassurance.

**Rule U1 — absence of reporting is never evidence of low risk.** A trial that does not describe
allocation concealment is not concealed; it is unreported. The judgement is `some_concerns` (RoB 2)
or `no information` / `serious` (ROBINS-I), never `low`.

**Rule U2 — name what is missing, and where you looked.** A rationale must let a reader reproduce
the judgement.

**Rule U3 — distinguish three different silences:**

| Silence | Meaning | Typical judgement |
|---|---|---|
| Not reported | The paper does not say | `some_concerns` / no information |
| Not assessable from this source | We only have the abstract | `unclear`, rationale `"not assessable from abstract"` |
| Reported as absent | The paper states it did not occur | judge on the statement's credibility |

**Rule U4 — never infer from journal, author, funder, sample size, citation count, or the fact
that a trial is registered.** Registration is evidence about domain 5 / item 2 only, and only if
the registration was checked and its date and pre-specified outcomes compared.

### Phrasings

```text
BAD  "Allocation concealment was likely adequate given the journal's standards."
GOOD "Allocation concealment not described (Methods, 'Randomisation' paragraph); no
      information on who held the sequence. Some concerns."

BAD  "No information on blinding, so low risk assumed."
GOOD "Outcome assessors' awareness of assignment not reported; the outcome is a
      clinician-rated scale, which is susceptible to assessor knowledge. Some concerns."

BAD  "Confounding adequately handled."
GOOD "Adjusted for age and sex only; baseline symptom severity and prior treatment —
      pre-specified as the principal confounders — were neither measured nor adjusted.
      Serious."

BAD  "Missing data unclear."            (unclear with no reason: schema violation)
GOOD "Attrition stated as 18% overall but not by arm, and no analysis of whether
      missingness relates to outcome. Some concerns."

BAD  "Study appears well conducted overall."   (global impression, no domain anchor)
GOOD Per-domain judgements only; the overall follows the tool's algorithm.

BAD  "Abstract does not mention randomisation problems, so domain 1 is low."
GOOD "Abstract-only record; randomisation process not assessable from abstract. Unclear."
```

---

## 7. Abstract-only evidence

Invariant (`PLAN.md` §6, schema §7/§8): `evidence_basis` is carried on both the extraction record
and the appraisal record and must agree with `corpus record.fulltext.status`.

| `fulltext.status` | `evidence_basis` | Appraisal |
|---|---|---|
| `fulltext` | `fulltext` | Normal appraisal |
| `abstract_only` (incl. HTML truncation detected) | `abstract_only` | Restricted, per below |
| `missing` | — | Never reaches extraction or appraisal; lives in `missing.md` |

Restricted appraisal rules for `evidence_basis: "abstract_only"`:

1. Every domain that cannot be assessed from an abstract is `unclear` with rationale
   `"not assessable from abstract"` (schema §8 requires exactly this).
2. `overall_judgement` is `unclear`. Never `low`, never a star count, never an AMSTAR-2 rating.
3. Prefer `tool: "none"` when *no* domain is assessable — which is the usual case. A record with
   five `unclear` domains and one weakly-supported judgement communicates less than an honest
   "not appraised: abstract only".
4. `grade` may still be filled at the body-of-evidence level, but the abstract-only studies must
   be reflected as risk-of-bias or indirectness limitations in that rating, not ignored.
5. The report must label the claim as abstract-only at the point of use. The verifier check
   `C-FULLTEXT` fails on any `abstract_only_claims[].labelled == false` (schema §9).

A truncation-detected HTML fetch is abstract-only. It is never "partial full text".

---

## 8. GRADE

GRADE rates **certainty of evidence per outcome**, across the body of studies contributing to that
outcome — not per study. The `grade` object on an individual appraisal record (schema §8) records
that study's contribution to the outcome-level rating; the outcome-level rating itself is produced
at synthesis (`references/synthesis.md`). Set `grade: null` when it is only being applied at
outcome level elsewhere.

### Starting certainty

| Body of evidence | Start |
|---|---|
| Randomized trials | High |
| Observational studies / NRSI | Low |

Option: when NRSI are appraised with ROBINS-I against a target trial, GRADE guidance permits
starting at High and downgrading for risk of bias from the ROBINS-I judgement. Choosing this path
must be declared in the protocol and stated in the report; it is not the default here.

### The five downgrade domains

| Domain | Downgrade when | Schema enum |
|---|---|---|
| Risk of bias | Studies contributing most of the weight carry serious/critical RoB2/ROBINS-I judgements | `not_serious` / `serious` / `very_serious` |
| Inconsistency | Unexplained heterogeneity in point estimates, non-overlapping CIs, high I² with no explanatory subgroup | same |
| Indirectness | Population, intervention, comparator, or outcome differs from the review question; indirect comparisons | same |
| Imprecision | Few events, wide CI spanning both appreciable benefit and appreciable harm, optimal information size not met | same |
| Publication bias | Small-study effects, industry-funded body, registered-but-unpublished trials, grey literature absent | `undetected` / `suspected` / `strongly_suspected` |

Each downgrade is by one level (serious) or two (very serious). Publication bias downgrades by one
when suspected, and may downgrade further when strongly suspected.

### Upgrade domains

GRADE defines **three** upgrade domains, applicable only to bodies of evidence not already
downgraded for risk of bias — in practice, observational evidence:

| Domain | Applies when |
|---|---|
| Large magnitude of effect | Large, consistent effect (conventionally RR > 2 or < 0.5); very large (RR > 5 or < 0.2) may upgrade two levels |
| Dose-response gradient | A monotone relation between exposure intensity and outcome |
| All plausible residual confounding would reduce the observed effect | Unadjusted confounding would have biased toward the null, yet an effect is still observed (or would have created a spurious effect, yet none is observed) |

Never upgrade randomized evidence that already starts High, and never upgrade a body of evidence
carrying serious risk of bias.

### Reaching the rating

```text
start (design)
  - risk of bias        (0 / -1 / -2)
  - inconsistency       (0 / -1 / -2)
  - indirectness        (0 / -1 / -2)
  - imprecision         (0 / -1 / -2)
  - publication bias    (0 / -1 / -2)
  + upgrades            (observational, unbiased bodies only)
= certainty : high | moderate | low | very_low        (floor at very_low)
```

Rules:

- One outcome, one rating. Rate the outcomes that matter to the question, including harms.
- Do not double-count: a problem downgraded under risk of bias is not also downgraded under
  imprecision for the same reason.
- Record the reason for every non-zero domain in the report. A certainty rating with no stated
  downgrade reasons is not a GRADE rating.
- Certainty is about the estimate, not about importance. "Low certainty" never means "no effect".

### Certainty wording (use verbatim shapes)

| Certainty | Wording |
|---|---|
| High | "X reduces Y" |
| Moderate | "X probably reduces Y" |
| Low | "X may reduce Y" |
| Very low | "The evidence is very uncertain about the effect of X on Y" |

These belong to the evidence side of the hard wall in `references/synthesis.md` §7. Hypotheses use
a different register entirely.
