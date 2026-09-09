# Deep Research Simplification Plan

## Goal

Reduce the amount of behavior the coordinator has to remember, shrink the largest scripts into
clearer units, and lower future change risk without changing the existing user-facing commands.

Non-goals:

- Do not weaken citation, evidence-kernel, quarantine, or OKF validation rules.
- Do not remove the current CLI entrypoints.
- Do not move run data out of `<wiki>/outputs/deep-research/<slug>/`.
- Do not duplicate PDFs per run; they stay in `<wiki>/assets/papers/`.
- Do not merge helpers that `scripts/_common.py` documents as deliberately divergent
  (`read_jsonl`, `atomic_write`, `sha256_text`, `slugify`, `warn`, `log`, `Http`).

## Current State

- `scripts/_common.py` exists (commit 78dd372) and is imported by all ten CLI scripts. It holds
  the helpers that were provably identical. Its docstring records why the remaining same-name
  helpers must stay separate: different error policies, durability guarantees, and hash
  contracts. That decision stands.
- `fulltext.py:353-450` has a literal copy-paste pair of handoff queues (MCP rung 1, browser
  rung 7) differing only in path and status string.
- `corpus.py` (1832 lines) combines corpus storage, dedupe, screening ingestion, PRISMA, query
  guards, no-progress guards, and `TaskBoard`.
- `okf.py` (3047 lines) covers frontmatter parsing, write fences, validation, preflight,
  transaction handling, and concept generation.
- Test net is thin: 34 tests, ~1k lines, over ~23k lines of scripts. `test_corpus.py` is 56
  lines. No direct tests for `okf.py` `Validator` beyond the digest gate.
- `html_report.py` (2472), `verify.py` (1853), and `watch.py` (1603) are larger than
  `corpus.py` and are out of scope here; they have single responsibilities and no known
  duplication.
- `SKILL.md`, `README.md`, and `references/` repeat policy text.

## Phase A: Unify Full-Text Handoff Queues

1. Add a small `HandoffQueue` class inside `fulltext.py`.
   - Constructed with `(path, pending_status)`.
   - `read()`: JSONL, skip malformed lines silently (existing behavior; keep local, do not
     route through `_common`).
   - `upsert(task)`: replace by `task_id`, write via sibling `.tmp` then `replace`.
   - `pending(records)`: filter by status, skip if `result_path` exists, skip if the record's
     `fulltext.status == "fulltext"`.

2. Replace the six `*_mcp_task*` / `*_browser_task*` functions with two module-level instances
   or thin wrappers, keeping the existing function names as aliases so call sites and tests do
   not change.

3. Keep rung-specific payloads where they are.

Exit criteria:

- One implementation of read/upsert/pending.
- Existing stale-suppression and acquisition-status tests pass unchanged.

## Phase B: Docs Consistency Check

1. Add `tests/test_docs.py`.
   - Collect argparse subcommand names from each `scripts/*.py`.
   - Grep `README.md`, `SKILL.md`, and `references/*.md` for `<script>.py <subcommand>`
     patterns.
   - Fail on any command that does not exist.

2. Define source-of-truth ownership.
   - `SKILL.md`: operational coordinator instructions.
   - `README.md`: short operator manual.
   - `references/`: detailed policies and schemas.

3. Remove repeated policy prose.
   - Quarantine behavior detailed once in `references/acquisition.md`.
   - Stage overview concise in `SKILL.md`.
   - README links to details instead of restating edge cases.

Exit criteria:

- Docs mention only commands that exist, enforced by test.
- Fewer repeated copies of quarantine, pool, connector, and promotion policy.

## Phase C: Split Taskboard Out Of `corpus.py`

1. Create `scripts/taskboard.py`.
   - Move `TaskBoard` (`corpus.py:618`), task id validation, transition graph, input hashing,
     and task CLI command functions.

2. Keep `corpus.py task ...` working by delegation.

3. Add direct tests for `taskboard.py` transition behavior.

Exit criteria:

- `corpus.py` no longer owns taskboard state-machine logic.
- Existing `corpus.py task` commands remain compatible.

## Phase D: Characterization Tests Before Any Further Split

No structural change in this phase. Purpose: build the net the later splits need.

1. `okf.py`
   - `Validator`: one test per V-rule family using fixture bundles that pass and fail.
   - `Preflight` and transaction flow: promote a fixture run, assert file set and rollback on
     injected failure.
   - Frontmatter parser: round-trip and malformed-input cases.

2. `corpus.py`
   - `Corpus` merge and evidence id derivation.
   - `build_prisma` counts on a fixture corpus.
   - Query guard register/check.

3. Record a coverage baseline.

```bash
python -m coverage run -m unittest discover -s tests
python -m coverage report --include='scripts/*'
```

Exit criteria:

- Coverage number recorded in this file.
- Every function slated for Phase E or F is exercised by at least one test.

### Coverage baseline (recorded 2026-09-09)

Added `tests/test_okf_validator.py` (41 tests: one per V1..V25 rule family plus a
zero-violation baseline, frontmatter round-trip/malformed-input cases, and a promote
transaction suite — success file set, `--check` writes nothing, and rollback on an
injected post-write validation failure with byte-identical `research/` before/after) and
extended `tests/test_corpus.py` (23 new tests: `derive_evidence_id` precedence and
fallback, `can_merge` blocking guards, `Corpus.upsert`/`save`/`load` round trip,
`build_prisma` counts and `prisma_markdown` rendering on a fixture corpus, and the
query-register/query-check duplicate guard via the CLI). Full suite: 117 tests, OK
(58 pre-existing + 59 new).

```
python3 -m coverage run -m unittest discover -s tests
python3 -m coverage report --include='scripts/*'
```

| file | stmts | miss | cover |
|---|---:|---:|---:|
| scripts/_common.py | 28 | 4 | 86% |
| scripts/assemble.py | 614 | 192 | 69% |
| scripts/corpus.py | 985 | 460 | 53% |
| scripts/eutils.py | 458 | 351 | 23% |
| scripts/eval.py | 117 | 90 | 23% |
| scripts/fulltext.py | 926 | 684 | 26% |
| scripts/html_report.py | 1075 | 926 | 14% |
| scripts/library.py | 536 | 425 | 21% |
| scripts/okf.py | 2024 | 681 | 66% |
| scripts/pool.py | 366 | 278 | 24% |
| scripts/render.py | 717 | 603 | 16% |
| scripts/source.py | 384 | 274 | 29% |
| scripts/status.py | 225 | 202 | 10% |
| scripts/store.py | 661 | 195 | 70% |
| scripts/taskboard.py | 224 | 69 | 69% |
| scripts/verify.py | 1167 | 1047 | 10% |
| scripts/watch.py | 1127 | 1032 | 8% |
| **TOTAL** | **11634** | **7513** | **35%** |

Notes: `okf.py` and `corpus.py` — the two files in scope for Phase E/F — sit at 66% and
53% respectively, up from a thin pre-Phase-D net (`test_corpus.py` was 56 lines with two
tests; `okf.py`'s `Validator` had no direct test beyond the digest gate). Coverage is
measured only for code that runs in-process; CLI-level characterization tests that shell
out via `run_py` (subprocess) exercise their target scripts but do not attribute lines to
this report — actual behavior coverage of `corpus.py`'s CLI commands (`query-check`,
`query-register`, `dedupe`, `screen-ingest`, etc.) is higher than the raw percentage
suggests. `coverage` is not installed in the project's system Python; this run used a
throwaway venv (`python3 -m venv` + `pip install coverage requests`).

Phase E/F checklist — every function/class named in those sections is now exercised by
at least one Phase D test:
- Corpus, `build_prisma`, `prisma_markdown`, query guard (`norm_query`/`query_hash`
  plus CLI register/check) — `tests/test_corpus.py`.
- Frontmatter parser (`split_frontmatter`/`yaml_dump`/`yaml_loads`/`render_document`/
  `load_document`), `Fence`/`TxFence` (via the promote transaction suite), `Validator`
  (all 25 V-rules), `load_run`/`cmd_promote` (promote)/concept generation
  (`study_concept`, `base_front`) — `tests/test_okf_validator.py`.

## Phase E: Split Corpus Responsibilities (conditional)

Proceed only if Phase D is done and there is a concrete need to use PRISMA or the corpus store
without the CLI. Otherwise skip; splitting adds import surface without removing logic.

1. `scripts/corpus_store.py`: `Corpus`, record normalization, evidence id derivation, merge,
   strict schema export.
2. `scripts/prisma.py`: `build_prisma`, `prisma_markdown`.
3. `scripts/query_guard.py`: query normalization, hash, register/check.
4. `corpus.py` remains the CLI facade; command lines unchanged.

## Phase F: Split `okf.py` (conditional)

Proceed only after Phase D. Highest-risk phase.

1. `scripts/frontmatter.py`: YAML subset parser/serializer, `split_frontmatter`,
   `render_document`.
2. `scripts/fence.py`: `Fence`, `TxFence`, path checks, write helpers.
3. `scripts/okf_validate.py`: `Validator`, `Violation`, V-rule checks.
4. `scripts/okf_promote.py`: `load_run`, digest gate, preflight, concept generation,
   transaction flow.
5. `okf.py` remains the CLI facade.

Exit criteria:

- `okf.py selftest`, `okf.py validate`, `okf.py promote` remain compatible.
- Phase D tests pass without modification.

## Dropped

- **Shared primitives phase.** Already shipped as `_common.py`. The remaining same-name
  helpers are documented as intentionally different. Only `wiki_root_for_run`
  (`library.py:524`, `store.py:224`) is a genuine duplicate; fold it into `_common.py`
  opportunistically during Phase A or C, no separate phase.
- **`pipeline.py` facade.** Adds a second orchestration source next to `SKILL.md` prose and
  mutates `config.json` stage pointers. Two sources of truth is the opposite of the goal.
  Revisit only as a read-only `next --run-dir` advisor, and only if the corresponding prose is
  deleted from `SKILL.md` in the same change.

## Order

1. Phase A: handoff queue unification.
2. Phase B: docs consistency check and prose dedupe.
3. Phase C: taskboard split.
4. Phase D: characterization tests and coverage baseline.
5. Reassess. Phases E and F only with a stated need.

## Verification

After each phase:

```bash
python -m unittest discover -s tests
```

For phases touching promotion or corpus:

```bash
python scripts/eval.py
python scripts/okf.py selftest
python scripts/corpus.py validate --run-dir <fixture-run>
python scripts/verify.py run --run-dir <fixture-run> --wiki <fixture-wiki> --json
```

## Open Questions

- Should `pool.py seed` scoring remain lexical only, or should a later phase add a richer
  local embedding/index option?
- Should `README.md` be aggressively shortened once Phase B links replace restated policy?
