# search-strategy.md — MeSH, hedges, field tags, orthogonal query design

Reference for PLAN.md §5 stage 2 (Search). Consumed by the coordinator when it *designs*
4–8 queries and by `scripts/eutils.py` when it *executes* them. Every query in this document
is written in PubMed/E-utilities syntax and is pasteable as-is.

Hard rule for this stage: **every executed query logs its hit count and NCBI-translated
query** into a `search result record` (`references/schema.md` §3). A query with
`hit_count_logged: false` fails verifier check `C-SEARCH-LOG` and blocks the PRISMA flow.

---

## 1. Controlled vocabulary vs free text

| Mechanism | Syntax | Behaviour | When |
|---|---|---|---|
| MeSH, exploded (default) | `"Depressive Disorder"[mh]` | Includes all narrower descriptors in the tree | Default for any indexed concept |
| MeSH, not exploded | `"Depressive Disorder"[mh:noexp]` | This descriptor only | Parent term is far broader than the concept |
| Major topic | `"Depressive Disorder"[majr]` | Only records where the term is a *major* concept (starred) | Precision arm; never the only arm |
| Major topic, unexploded | `"Depressive Disorder"[majr:noexp]` | Both restrictions | Rarely; very high precision |
| Attached subheading | `"Depressive Disorder/therapy"[mh]` | Descriptor + qualifier pair | Narrow a facet without a second concept |
| Floating subheading | `"drug therapy"[sh]` | Qualifier anywhere on the record | Hedges (Cochrane HSSS uses `"drug therapy"[sh]`) |
| Title/abstract | `adolescent*[tiab]` | Free text in title + abstract | Mandatory companion arm to every MeSH arm |
| Title only | `trial[ti]` | Title words | Precision-maximizing hedges |
| Author keyword | `remission[ot]` | Publisher-supplied "other term" keywords | Catches concepts MeSH has no descriptor for |
| Text word (broad) | `mindfulness[tw]` | MeSH + tiab + other terms + substances | Scoping only; too blunt for a logged arm |
| Supplementary concept | `"tirzepatide"[nm]` | Substance name records | Drugs newer than their MeSH descriptor |

### Why MeSH alone is always wrong

1. **Indexing lag.** Records enter PubMed unindexed and stay that way for weeks to months.
   `[mh]` cannot retrieve them. The most recent 6–12 months of literature — usually the part
   the user cares about — is reachable **only** by text words.
   Check the size of the blind spot:
   ```text
   esearch.fcgi?db=pubmed&term=<concept>[tiab] AND publisher[sb]
   esearch.fcgi?db=pubmed&term=<concept>[tiab] NOT medline[sb]
   ```
2. **Descriptor drift.** Terms are introduced, renamed and re-scoped; older records keep the
   older descriptor. Check `Entrez Date` vs `MeSH Date` when a concept is younger than ~5 years.
3. **No descriptor exists.** New interventions, scales, and constructs have no MeSH term until
   someone requests one. `[tiab]` + `[ot]` + `[nm]` are the only routes.
4. **Indexer disagreement.** MeSH is applied by humans; recall for any single descriptor is
   well below 100%.

Consequence: **every concept facet is `(MeSH OR text-word) `**, e.g.

```text
("Cognitive Behavioral Therapy"[mh] OR "cognitive behavio*al therapy"[tiab]
 OR CBT[tiab] OR "cognitive therapy"[tiab] OR "behavio*al activation"[tiab])
```

Truncation notes: `*` needs ≥4 leading characters, expands to at most 600 terms, and
**disables automatic term mapping** — a truncated term is never mapped to MeSH. A phrase in
double quotes is also never mapped. Both facts show up in `translated_query`; always read it.

---

## 2. Filters → E-utilities field tags (PLAN.md §4)

| Filter | Example value | Tag / syntax |
|---|---|---|
| Years | 2000–2026 | `("2000"[dp] : "2026"[dp])` |
| Years, entry date | added since 2024-01-01 | `("2024/01/01"[edat] : "3000"[edat])` |
| Years, MeSH-completion date | — | `("2024"[mhda] : "3000"[mhda])` |
| Relative window | last 5 years | `"last 5 years"[dp]` |
| Author | Kaufmann J | `Kaufmann J[au]` |
| First / last author | — | `Kaufmann J[1au]` / `Kaufmann J[lastau]` |
| Author identifier | ORCID | `0000-0002-1825-0097[auid]` |
| Collaborator / investigator | — | `Smith J[ir]` |
| Affiliation | — | `"University Hospital Frankfurt"[ad]` |
| Journal | JAMA Psychiatry | `"JAMA Psychiatry"[ta]` |
| Article type | RCT, meta-analysis, guideline | `"Randomized Controlled Trial"[pt]`, `"Meta-Analysis"[pt]`, `"Practice Guideline"[pt]` |
| Species | human | `"Humans"[mh]` (never `NOT "Animals"[mh]` alone — see §3) |
| Age, child 6–12 | — | `"Child"[mh]` (6–12y) — see age table below |
| Language | English, German | `english[la] OR german[la]` |
| Free full text | OA only | `"free full text"[sb]` |
| PMC subset | — | `"pubmed pmc"[sb]` |
| MEDLINE-indexed only | — | `medline[sb]` |
| Not-yet-indexed | — | `publisher[sb]`, `pubmednotmedline[sb]` |
| Has abstract | — | `hasabstract` |
| Sex | — | `"Female"[mh]` / `"Male"[mh]` |
| Grant / funding id | — | `R01MH123456[gr]` |
| Retraction status | — | `"Retracted Publication"[pt]`, `"Retraction of Publication"[pt]`, `"Expression of Concern"[pt]` |
| PMID list | — | `12345678[pmid] OR 23456789[pmid]` |
| Sample size, funding source, setting | — | **not queryable — applied at screening** |

PubMed age groups (MeSH, use the descriptor, not a numeric range):

| Descriptor | Range | Descriptor | Range |
|---|---|---|---|
| `"Infant, Newborn"[mh]` | 0–1 mo | `"Adolescent"[mh]` | 13–18 y |
| `"Infant"[mh]` | 1–23 mo | `"Young Adult"[mh]` | 19–24 y |
| `"Child, Preschool"[mh]` | 2–5 y | `"Adult"[mh]` | 19–44 y |
| `"Child"[mh]` | 6–12 y | `"Aged"[mh]` | 65+ y |

Age descriptors are MeSH → they inherit the indexing lag. Pair with
`(adolescen*[tiab] OR youth[tiab] OR teenager*[tiab])`.

Date-type choice matters for reproducibility: `[dp]` (publication date) is unstable for
ahead-of-print records; `[edat]` (Entrez date) is stable and is what makes a rerun of the same
strategy reproducible. Log which one was used in `query_string`.

---

## 3. Validated design hedges

**Provenance.** The strings below are PubMed renderings of published filters. Before a
`systematic` or `max` run, re-verify them against the canonical sources rather than trusting
this file: Cochrane Handbook ch. 4 + its Technical Supplement (`training.cochrane.org/handbook`);
SIGN search filters (`www.sign.ac.uk/what-we-do/methodology/search-filters/`); PubMed Clinical
Queries (`pubmed.ncbi.nlm.nih.gov/clinical/`); InterTASC ISSG Search Filter Resource. Record
the source and retrieval date in `protocol.md`. Never present an adapted filter as the
verbatim published one.

### 3.1 Cochrane Highly Sensitive Search Strategy — RCTs, **sensitivity-maximizing**

Use when recall dominates (systematic / max profiles). Very low precision by design.

```text
(randomized controlled trial[pt]
 OR controlled clinical trial[pt]
 OR randomized[tiab]
 OR placebo[tiab]
 OR "drug therapy"[sh]
 OR randomly[tiab]
 OR trial[tiab]
 OR groups[tiab])
NOT ("Animals"[mh] NOT "Humans"[mh])
```

### 3.2 Cochrane HSSS — RCTs, **sensitivity- and precision-maximizing**

Default RCT hedge for `standard` rigor.

```text
(randomized controlled trial[pt]
 OR controlled clinical trial[pt]
 OR randomized[tiab]
 OR placebo[tiab]
 OR "Clinical Trials as Topic"[mh:noexp]
 OR randomly[tiab]
 OR trial[ti])
NOT ("Animals"[mh] NOT "Humans"[mh])
```

The animal exclusion **must** be written `NOT (Animals[mh] NOT Humans[mh])`, never
`NOT Animals[mh]`: the latter drops every mixed human/animal record.

### 3.3 SIGN systematic-review / meta-analysis filter (PubMed adaptation)

```text
("Meta-Analysis"[pt]
 OR "Meta-Analysis as Topic"[mh]
 OR "Systematic Review"[pt]
 OR "Systematic Reviews as Topic"[mh]
 OR metaanaly*[tiab] OR meta-analy*[tiab] OR metanaly*[tiab]
 OR "systematic review"[tiab] OR "systematic overview"[tiab]
 OR "Review Literature as Topic"[mh]
 OR ((cochrane[tiab] OR embase[tiab] OR cinahl[tiab] OR psycinfo[tiab]
      OR psychinfo[tiab] OR "science citation index"[tiab] OR medline[tiab])
     AND (review[pt] OR review[tiab]))
 OR (("reference list*"[tiab] OR bibliograph*[tiab] OR "hand-search*"[tiab]
      OR "manual search*"[tiab] OR "relevant journals"[tiab])
     AND (review[pt] OR review[tiab]))
 OR (("selection criteria"[tiab] OR "data extraction"[tiab]) AND review[pt]))
NOT (comment[pt] OR letter[pt] OR editorial[pt]
     OR ("Animals"[mh] NOT "Humans"[mh]))
```

Umbrella/overview arm for `max`: add `OR "umbrella review"[tiab] OR "overview of reviews"[tiab] OR "network meta-analysis"[tiab] OR "Network Meta-Analysis"[pt]`.

### 3.4 Observational designs — sensitivity-maximizing

```text
("Observational Study"[pt]
 OR "Observational Studies as Topic"[mh]
 OR "Epidemiologic Studies"[mh]
 OR "Cohort Studies"[mh]
 OR "Case-Control Studies"[mh]
 OR "Cross-Sectional Studies"[mh]
 OR "Follow-Up Studies"[mh]
 OR "Longitudinal Studies"[mh]
 OR "Prospective Studies"[mh]
 OR "Retrospective Studies"[mh]
 OR cohort*[tiab] OR "case-control"[tiab] OR "case control"[tiab]
 OR "cross-sectional"[tiab] OR "cross sectional"[tiab]
 OR longitudinal[tiab] OR prospective[tiab] OR retrospective[tiab]
 OR "register-based"[tiab] OR "registry-based"[tiab]
 OR "population-based"[tiab])
NOT ("Animals"[mh] NOT "Humans"[mh])
```

### 3.5 Observational designs — precision-maximizing

```text
("Observational Study"[pt]
 OR "Cohort Studies"[majr] OR "Case-Control Studies"[majr]
 OR "Cross-Sectional Studies"[majr]
 OR "cohort study"[ti] OR "cohort of"[ti] OR "case-control study"[ti]
 OR "cross-sectional study"[ti] OR "prospective cohort"[ti])
NOT ("Animals"[mh] NOT "Humans"[mh])
```

### 3.6 Other design hedges

| Purpose | Hedge core |
|---|---|
| Diagnostic accuracy | `("Sensitivity and Specificity"[mh] OR sensitivity[tiab] OR specificity[tiab] OR "predictive value*"[tiab] OR "likelihood ratio*"[tiab] OR "ROC curve"[mh] OR "diagnostic accuracy"[tiab])` |
| Prognosis | `("Prognosis"[mh] OR "Disease Progression"[mh] OR "Survival Analysis"[mh] OR predict*[tiab] OR prognos*[tiab] OR "risk factor*"[tiab])` |
| Qualitative | `("Qualitative Research"[mh] OR "Interviews as Topic"[mh] OR "Focus Groups"[mh] OR qualitative[tiab] OR "thematic analysis"[tiab] OR "grounded theory"[tiab])` |
| Guidelines / grey | `("Practice Guideline"[pt] OR "Guideline"[pt] OR "Consensus Development Conference"[pt] OR "Guidelines as Topic"[mh] OR guideline*[ti] OR "consensus statement"[ti])` |
| Economic | `("Costs and Cost Analysis"[mh] OR "Cost-Benefit Analysis"[mh] OR cost*[tiab] OR "QALY"[tiab] OR "cost-effectiveness"[tiab])` |
| Harms / adverse effects | `("adverse effects"[sh] OR "Drug-Related Side Effects and Adverse Reactions"[mh] OR "adverse event*"[tiab] OR safety[tiab] OR tolerability[tiab] OR harm*[tiab])` |
| **Null / negative findings** (PLAN.md §6 invariant) | `("no significant difference*"[tiab] OR "no difference"[tiab] OR "not superior"[tiab] OR "failed to"[tiab] OR nonsignificant[tiab] OR "non-significant"[tiab] OR "null result*"[tiab] OR "negative trial"[tiab] OR "equivalence trial"[tiab] OR "noninferiority"[tiab] OR "Equivalence Trials as Topic"[mh])` |
| Retraction surveillance | `("Retracted Publication"[pt] OR "Retraction of Publication"[pt] OR "Expression of Concern"[pt])` — run over the *included* PMID set, not the topic |

The null-findings hedge is not optional: it is the operationalization of "null/negative
findings actively searched, not just whatever surfaced".

---

## 4. Citation chaining (elink)

Backward (references), forward (cited-by) and sideways (related) chaining are a **different
entry point**, not another boolean query. They find papers whose wording your vocabulary
missed.

```text
BASE=https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi

# forward — who cites this (PMC-derived)
$BASE?dbfrom=pubmed&db=pubmed&linkname=pubmed_pubmed_citedin&id=12345678&retmode=json

# backward — what this cites (PMC-derived reference list)
$BASE?dbfrom=pubmed&db=pubmed&linkname=pubmed_pubmed_refs&id=12345678&retmode=json

# sideways — PubMed "similar articles" (term-vector neighbours)
$BASE?dbfrom=pubmed&db=pubmed&linkname=pubmed_pubmed&id=12345678&retmode=json

# reviews only among the neighbours
$BASE?dbfrom=pubmed&db=pubmed&linkname=pubmed_pubmed_reviews&id=12345678&retmode=json

# PMC full-text records citing this one
$BASE?dbfrom=pubmed&db=pmc&linkname=pubmed_pmc_refs&id=12345678&retmode=json
```

Multiple seeds: repeat `&id=` per PMID (`&id=1&id=2`) to keep per-seed link sets separate;
a comma-joined list merges them and loses provenance.

| Caveat | Handling |
|---|---|
| `citedin` / `refs` cover only PMC-deposited citing/cited articles → substantial undercount | Cross-check with Europe PMC (below); state the limitation in the search-strategy log |
| `pubmed_pubmed` (related) is unranked-by-relevance beyond the first page and unbounded | Cap at the top 20–50 per seed; record the cap |
| Chaining amplifies the citation bias of the seed set | Seed from ≥3 *independent* records (different groups/countries/designs), including one null-result paper |

Europe PMC citation endpoints (scope `wide`/`max`, better coverage than elink):

```text
https://www.ebi.ac.uk/europepmc/webservices/rest/MED/12345678/citations?format=json&pageSize=100
https://www.ebi.ac.uk/europepmc/webservices/rest/MED/12345678/references?format=json&pageSize=100
```

Every chaining pass is logged as its own `search result record` with
`query_string: "elink:pubmed_pubmed_citedin:seed=12345678,23456789"`, `translated_query: null`,
`count` = number of linked ids returned. Chaining that is not logged does not exist.

---

## 5. Designing 4–8 **orthogonal** queries

The failure mode this section exists to prevent: eight queries that are the same query with
synonyms shuffled, producing one hit set, ~0% marginal yield, and a search log that *looks*
thorough. Orthogonality is a property of the **hit sets**, not of the strings.

### 5.1 The four axes

| Axis | Values | What varying it buys |
|---|---|---|
| **A — concept axis dropped** | drop P / drop I / drop C / drop O / drop setting / drop nothing | A 4-facet AND is massively over-specified; each dropped facet recovers records that facet's vocabulary failed on |
| **B — vocabulary** | MeSH-only `[mh]`/`[majr]` · text-word `[tiab]`/`[ti]` · author keyword `[ot]`/`[nm]` · phrase-free (single broad `[tw]`) | Recovers records lost to indexing lag, descriptor drift, and non-standard terminology |
| **C — design filter** | none · RCT hedge (3.1/3.2) · SR hedge (3.3) · observational hedge (3.4/3.5) · null-findings hedge · harms hedge | Different literatures index the same question under different designs |
| **D — entry point** | de novo boolean · forward chaining · backward chaining · related-articles · journal/author-targeted · registry & grey | Escapes the vocabulary entirely |

**Orthogonality rule.** Two queries count as orthogonal when they differ on **≥2 axes** *and*
their hit sets satisfy Jaccard `J < 0.6` with unique yield `≥ 15%` each (§5.4). A set of 4–8
queries must cover **at least three values of axis A**, **at least three of axis B**, **at
least two of axis C**, and **at least two of axis D**.

### 5.2 Worked example

PICO — P: adolescents 12–18 with major depression · I: group CBT · C: waitlist or TAU ·
O: depressive symptom severity. Facet blocks defined once:

```text
P = ("Adolescent"[mh] OR "Child"[mh] OR adolescen*[tiab] OR youth[tiab]
     OR teenager*[tiab] OR "young people"[tiab])
D = ("Depressive Disorder"[mh] OR "Depression"[mh] OR depress*[tiab]
     OR "MDD"[tiab] OR dysthymi*[tiab])
I = ("Cognitive Behavioral Therapy"[mh] OR "cognitive behavio*al therapy"[tiab]
     OR CBT[tiab] OR "cognitive therapy"[tiab] OR "behavio*al activation"[tiab])
O = ("Treatment Outcome"[mh] OR "Psychiatric Status Rating Scales"[mh]
     OR "symptom severity"[tiab] OR remission[tiab] OR response[tiab]
     OR BDI[tiab] OR CDI[tiab] OR "CDRS-R"[tiab] OR PHQ-9[tiab])
```

| id | Query | A (dropped) | B | C | D | Role |
|---|---|---|---|---|---|---|
| q1 | `P AND D AND I AND RCT-hedge-3.2 AND ("2000"[dp]:"2026"[dp])` | O | mixed | RCT precision | de novo | Anchor; highest precision |
| q2 | `("Adolescent"[mh] AND "Depressive Disorder"[mh] AND "Cognitive Behavioral Therapy"[majr])` | O, C | MeSH-only, major topic | none | de novo | Pure controlled-vocabulary arm; catches badly-titled records |
| q3 | `(adolescen*[tiab] OR teenager*[tiab]) AND depress*[tiab] AND (CBT[tiab] OR "cognitive behavio*al"[tiab]) AND ("2023"[edat]:"3000"[edat])` | O, C | text-word only | none | de novo | Indexing-lag arm; the only arm that sees the last ~12 months |
| q4 | `P AND D AND I AND SR-hedge-3.3` | O, C | mixed | SR | de novo | Prior syntheses → also a source of backward-chaining seeds |
| q5 | `P AND D AND O AND null-findings-hedge NOT I` | **I** | mixed | null/negative | de novo | Drops the intervention facet entirely; surfaces null trials of *other* therapies that report CBT arms, and reduces positive-result bias |
| q6 | `elink pubmed_pubmed_citedin` over the 3 largest RCTs found by q1 + the 1 null trial found by q5 | n/a | none | none | forward chaining | Finds papers whose vocabulary none of q1–q5 matched |
| q7 | `elink pubmed_pubmed_refs` over the 2 most recent SRs from q4 | n/a | none | none | backward chaining | Recovers pre-2000 and non-MEDLINE-indexed trials |
| q8 | `I AND ("Waiting Lists"[mh] OR waitlist*[tiab] OR "treatment as usual"[tiab] OR TAU[tiab]) AND D` | P | mixed | none | de novo | Drops the population facet; catches mixed-age samples that report an adolescent subgroup |

Note what is *not* here: no two queries differ only by synonym substitution.
q1/q2/q3 share facets but differ on axis B *and* C; q5 and q8 drop different facets; q6/q7
are a different entry point altogether.

### 5.3 Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Same facets, synonyms permuted | Automatic term mapping already ORs most synonyms; `J ≈ 0.95` | Change axis A or C, not the wordlist |
| Adding one more `AND` facet per query | Each query is a subset of the previous one — nested, not orthogonal | Drop a facet instead of adding one |
| Eight design hedges over one facet block | All hedges heavily overlap on `[pt]` terms | Max two design hedges plus one no-hedge arm |
| Only `[mh]` arms | Misses everything unindexed | ≥1 pure `[tiab]` arm restricted by `[edat]` |
| Only forward chaining | Inherits the seed set's bias | Pair `citedin` with `refs` and independent seeds |
| Date-slicing the same query | Partitions one hit set; `J = 0` but zero new *concepts* | Date slicing is pagination, not a query |

Date slicing has `J = 0` yet adds nothing: **Jaccard alone is not sufficient**; the axis
profile must differ too. Both tests must pass.

### 5.4 Near-duplicate detection by hit-set overlap

Run after execution, before screening. Inputs are the `retrieved_ids` of every
`workspace/search/*.json` record (`references/schema.md` §3).

```python
#!/usr/bin/env python3
# overlap.py — orthogonality report over workspace/search/*.json
import json, sys, itertools, pathlib

files = sorted(pathlib.Path(sys.argv[1], "workspace/search").glob("*.json"))
sets = {}
for f in files:
    if f.name.startswith("."):
        continue
    s = json.loads(f.read_text())
    sets.setdefault(s["query_id"], set()).update(s.get("retrieved_ids") or [])

print("pair\tJaccard\tshared")
for a, b in itertools.combinations(sorted(sets), 2):
    A, B = sets[a], sets[b]
    j = len(A & B) / len(A | B) if (A | B) else 0.0
    flag = "  <-- NEAR-DUPLICATE" if j >= 0.6 else ""
    print(f"{a}/{b}\t{j:.3f}\t{len(A & B)}{flag}")

print("\nquery\thits\tunique\tunique_yield")
for q, A in sorted(sets.items()):
    others = set().union(*[v for k, v in sets.items() if k != q]) if len(sets) > 1 else set()
    u = A - others
    y = len(u) / len(A) if A else 0.0
    flag = "  <-- LOW YIELD" if (len(u) < 5 and y < 0.15) else ""
    print(f"{q}\t{len(A)}\t{len(u)}\t{y:.3f}{flag}")
```

Checklist — a query set ships only when all of these hold:

- [ ] 4–8 queries, each with a `search result record` carrying non-null `count` and
      `translated_query`, and `hit_count_logged: true`.
- [ ] No pair with `J ≥ 0.6` (or: the pair is justified in `protocol.md` as a deliberate
      sensitivity/precision pair).
- [ ] Every query has unique yield `≥ 15%` **or** `≥ 5` unique records.
- [ ] Axis coverage: ≥3 values of A, ≥3 of B, ≥2 of C, ≥2 of D.
- [ ] ≥1 pure text-word arm bounded by `[edat]` (indexing-lag arm).
- [ ] ≥1 null/negative-findings arm.
- [ ] ≥1 citation-chaining arm with ≥3 independent seeds.
- [ ] No query's `translated_query` is a substring of another's (nesting test).
- [ ] `corpus.py query-check --run-dir <dir> --query '<q>'` returns `duplicate: false` for
      each query **before** execution.
- [ ] Marginal contribution after dedupe recorded per query (`first_seen_query` /
      `seen_in_queries` in `corpus.jsonl`).

Remediation when a pair fails: do **not** delete a query. Replace it by changing its axis-A
value (drop a different facet) or its axis-D value (make it a chaining pass), then re-execute
and re-run the overlap report. Deleting shrinks the recorded strategy without improving it.

---

## 6. Execution and logging contract

```text
# 1. guard against re-running a query already executed this run
corpus.py query-check  --run-dir <run> --query '<query>' --fail-on-duplicate
corpus.py query-register --run-dir <run> --query-id q5 --query '<query>' --source pubmed

# 2. count first — never fetch before you know the size
esearch.fcgi?db=pubmed&term=<urlencoded>&retmax=0&usehistory=y
    -> <Count>, <QueryTranslation>, WebEnv/QueryKey

# 3. page the ids, 200 at a time, honoring the 3 req/s throttle (10 with NCBI_API_KEY)
esearch.fcgi?...&retstart=0&retmax=200

# 4. write workspace/search/q5.json (schema.md §3); pages beyond the first are q5-p2.json ...
# 5. corpus.py add --run-dir <run> --query-id q5 --file <normalized-records.jsonl>
# 6. corpus.py dedupe --run-dir <run>
# 7. python3 overlap.py <run>          # §5.4 orthogonality report
# 8. corpus.py prisma --run-dir <run> --out
```

| Rule | Statement |
|---|---|
| L1 | `query_string` is stored **verbatim as submitted**, unescaped — not the URL-encoded form. |
| L2 | `translated_query` is `<QueryTranslation>` from esearch, copied verbatim. A `null` here is permitted only for non-PubMed sources and chaining passes. |
| L3 | `count` is esearch `<Count>`, the *total*, not the page size. |
| L4 | Reading `translated_query` is mandatory before accepting a result: it reveals truncation that suppressed term mapping, a mistyped `[mh]` that silently mapped to `[tw]`, and unintended automatic explosion. Any surprise there is a query bug, not a finding. |
| L5 | A query returning `count: 0` is still logged. Zero hits is a result and belongs in the PRISMA identification row. |
| L6 | A query with `count` above the run's budget is not silently truncated: log the full `count`, page to the budget, and record the truncation in `protocol.md`. |
| L7 | The final report reproduces every query, its date of execution, its date filter, and its hit count — this table *is* the reproducibility claim. |
