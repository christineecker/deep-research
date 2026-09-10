# single-paper-summary.md — the one-paper and selected-paper summary profiles

Implements `SINGLE_PAPER_SUMMARY_IMPLEMENTATION_PLAN.md`. Two lightweight run profiles that reuse
the standalone-repo pool (`references/pool-architecture.md`) instead of the full Stage 0-8
literature-review pipeline (`SKILL.md`). Neither profile is a review: no PRISMA flow, no
effect-direction synthesis, no GRADE summary-of-findings table, no meta-analysis.

## When to use which

| Ask | Use |
|---|---|
| "summarize this paper", "what does this study say", one PMID/DOI/PMCID/PDF | `scripts/paper.py summarize` |
| "summarize these three papers", a small user-supplied set, or a bounded topic/question scan for candidate papers | `scripts/paper.py summarize-set` |
| "literature review", "systematic review", "what does the evidence say about X" | `SKILL.md` full pipeline — never these profiles |

## Commands

```bash
python3 scripts/paper.py summarize --repo <repo> --pmid <pmid>
python3 scripts/paper.py summarize --repo <repo> --doi <doi>
python3 scripts/paper.py summarize --repo <repo> --pmcid <pmcid>
python3 scripts/paper.py summarize --repo <repo> --pdf <file.pdf> [--doi <doi>]
python3 scripts/paper.py summarize --repo <repo> --evidence-id <evidence-id>

python3 scripts/paper.py summarize-set --repo <repo> --pmid 123 --pmid 456 --pmid 789
python3 scripts/paper.py summarize-set --repo <repo> --ids-file papers.txt
python3 scripts/paper.py summarize-set --repo <repo> --bib refs.bib
python3 scripts/paper.py summarize-set --repo <repo> --folder papers/ --recursive
python3 scripts/paper.py summarize-set --repo <repo> --question "..."
python3 scripts/paper.py summarize-set --repo <repo> --topic "..."
```

Full option reference, storage layout, data model, and phased build plan:
`SINGLE_PAPER_SUMMARY_IMPLEMENTATION_PLAN.md`. Schema: §14/§15
(`references/schema/14-single-paper-summary.md`, `references/schema/15-paper-summary-set.md`).

## Workflow (single-paper)

```text
Input paper
  -> registry lookup or registration (registry.py Registry, shared with the full pipeline)
  -> source retrieval / PDF import (fulltext.py acquire, same acquisition ladder)
  -> extraction reuse (pool.py reuse --repo) or a new extraction task, one subagent,
     references/prompts/extract.md — unchanged from Stage 5
  -> appraisal reuse or a new appraisal task, one subagent, references/prompts/appraise.md —
     unchanged from Stage 6, project-scoped, on by default for fulltext summaries
  -> summary assembly, one subagent, references/prompts/summarize-paper.md (new — §14 contract)
  -> verification: scripts/verify.py single-paper-summary
  -> Markdown/HTML export
  -> optional promotion of the extraction/appraisal back to the canonical pool
```

`scripts/paper.py` never calls an extraction, appraisal, or summarization subagent itself — like
every other stage in this skill, that dispatch is the orchestrating agent's job. The CLI does the
deterministic parts (registry, acquisition, reuse, taskboard bookkeeping, assembly-input
plumbing, rendering, verification) and reports back a `status` telling the agent what to dispatch
next: `pending_extraction`, `pending_appraisal`, `pending_summary`, or `completed`. Re-running the
same command after the subagent finishes its file picks up where it left off — this mirrors how
Stage 5/6 already work in the full pipeline (`SKILL.md` "Pipeline"), just for one paper instead of
a corpus.

## Workflow (selected-paper)

Same per-paper path, run once per identifier in the set (partial failures are recorded, not
fatal — `references/schema/15-paper-summary-set.md` `failed_identifiers`), plus:

- `explicit` mode: identifiers normalized and registered one by one, or via `registry.py
  import-bib` / `import-folder` for `--bib`/`--folder` inputs.
- `question`/`topic` mode: one bounded PubMed search (`eutils.py esearch`, `--limit`-capped, hard
  default cap when `--limit` is omitted) produces a candidate list; `selection_basis` in the set
  manifest records the query, database, timestamp, and limit. `--select-only` stops here.
- optional `--overview`: a bounded cross-paper orientation section built only from the already-
  verified §14 summaries' descriptive fields (design, population, intervention, outcomes
  measured). Never infers pooled effect, certainty, or consensus.

## Verification

`scripts/verify.py single-paper-summary --summary <workspace/summaries/<slug>.json> --run-dir <run>`
and `scripts/verify.py paper-summary-set --summary-set <workspace/summary-set.json> --run-dir <run>`
implement the plan's "Verification" checks: every claim resolves to the target paper's own
`evidence_id`, every `span_refs` pointer resolves against the named extraction/appraisal file,
appraisal is present or its skip reason is recorded, abstract-only summaries are labeled, and no
cross-study/pooled/consensus wording appears outside a full-pipeline run. Output:
`<run>/outputs/verification.json`.

## Pool integration

Both profiles read and write the same canonical stores the full pipeline uses
(`data/papers/registry.jsonl`, `data/papers/extractions/`, `data/papers/appraisals/<project>/`) —
there is no separate summary-only store. A paper summarized once and later pulled into a full
review starts from an already-registered, possibly already-extracted/appraised record; a paper
extracted during a full review and later asked about individually reuses that extraction instead
of re-running Stage 5.
