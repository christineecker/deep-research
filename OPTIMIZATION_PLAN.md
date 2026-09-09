# Deep Research Optimization Plan

## Phase 1: Fix Correctness Drift

1. [Implemented] Wire Stage 7b digest into promotion.
   - Update `okf.py` run loading to read `outputs/digest.md`.
   - In `build_run_concepts`, use digest body for `research/reviews/<slug>.md`.
   - Check `taskboard.jsonl` for `digest:report` with `status: completed` and `output_path: outputs/digest.md`.
   - Block `okf.py promote` and `okf.py promote --check` if the digest is missing, stale, or uncompleted.
   - Add tests: promotion uses digest, missing digest blocks, failed digest blocks.

2. [Implemented] Fix preprint merge preservation.
   - Change `is_preprint` merge logic from `existing and incoming` to `existing or incoming`.
   - Add a regression test for published/preprint merge preserving `true`.

3. [Implemented] Resolve quarantine policy contradiction.
   - Choose one explicit rule in `SKILL.md` and `README.md`.
   - Recommended:
     - `fast` / `standard`: continue after quarantine, mark synthesis provisional.
     - `systematic` / `max`: hard halt before extraction until `missing.md` is cleared.
   - Add this policy to health alerts, failure semantics, and quarantine loop sections.

## Phase 2: Remove Avoidable Runtime Bottlenecks

4. [Implemented] Suppress stale MCP task alerts.
   - When computing pending rung-1 tasks, ignore records whose `fulltext.status == fulltext`.
   - Optionally mark stale task records as `superseded` or `resolved_elsewhere`.
   - Add a test where rung 1 emits `needs_mcp`, rung 2 succeeds, and summary reports `needs_mcp: 0`.

5. [Remaining] Optimize corpus dedupe.
   - Add in-memory indexes for PMID, DOI, and PMCID in `Corpus`.
   - For fuzzy title pass, precompute normalized titles once.
   - Bucket comparisons by publication year and first significant title token before `SequenceMatcher`.
   - Keep exact behavior as fallback for ambiguous cases.
   - Add a benchmark fixture with a few thousand synthetic records.

6. [Implemented] Make OCR cheaper.
   - Add configurable OCR page cap in `config.json` budgets, e.g. `max_ocr_pages`.
   - Default lower for `fast` / `standard`, higher for `systematic` / `max`.
   - Consider first-page DOI/title OCR before full-document OCR.
   - Report when OCR was skipped due to budget.

## Phase 3: Improve Maintainability

7. [Remaining] Split large scripts by responsibility.
   - First extraction target: shared JSONL, atomic write, path, schema helpers into a small module.
   - Then split:
     - `corpus.py`: store, dedupe, taskboard, PRISMA.
     - `okf.py`: YAML/frontmatter, validation, concept generation, promotion preflight.
     - `verify.py`: report parser, classic checks, kernel checks.
   - Preserve CLI entrypoints so user-facing commands do not change.

8. [Partially implemented] Add integration tests around stage boundaries.
   - Stage 4 quarantine behavior per profile.
   - Stage 7b digest receipt and promotion gate.
   - Stage 8 assemble -> verify -> promote order.
   - Resume with partially completed taskboard.

## Priority Order

1. Digest promotion mismatch.
2. Preprint merge bug.
3. Quarantine policy clarification.
4. Stale MCP task suppression.
5. Dedupe indexing.
6. OCR budget controls.
7. Script modularization.

This order fixes correctness and operator confusion before spending time on performance cleanup.
