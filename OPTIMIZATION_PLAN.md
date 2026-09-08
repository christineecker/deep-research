# Optimization Implementation Plan

> **Status, 2026-09-08: O1-O5 implemented, O6 partially declined, O7 still blocked.**
> What was actually built differs from what was planned in three places, each because the
> codebase turned out not to match an assumption in the plan. The record is at the bottom,
> under "Implementation record". Read that before trusting the phrasing above it.

Seven changes to the deep-research skill, ordered by payoff-per-risk. Each is independently
shippable; nothing here changes the pipeline's semantics except O7, which is a rollout flip
already scheduled by `VALIDATION_ARCHITECTURE_UPDATE_PLAN.md` migration step 9.

Baseline measured 2026-09-08 at commit `960a868`:

```text
SKILL.md                 255 lines   14.0K
references/*.md         3033 lines  ~150K   (schema.md alone: 905 lines / 64.3K)
references/prompts/*.md  613 lines
scripts/*.py           18231 lines
```

Two categories, kept separate because they fail differently:

- **Context cost** (O1-O3) — what the agent reads. Wrong here means slow, expensive runs.
- **Code health and wall-clock** (O4-O6) — what the scripts do. Wrong here means broken runs.

O7 is neither; it is the last open item of the validation plan.

---

## O1 — Route `evidence-kernel.md` from SKILL.md

**Defect, not an optimization.** `references/evidence-kernel.md` (292 lines) is absent from the
SKILL.md reading table at `SKILL.md:14-22`. Its only inbound links are two cross-references
buried in `schema.md:539` and `schema.md:548`. An agent working Stage 8 has no route to the
narrative explanation of the layer it is operating.

Change: one row in the SKILL.md table.

```markdown
| Snapshots, spans, freshness, the assembler gate | `references/evidence-kernel.md` |
```

Place it after the `okf-bundle.md` row, before the prompts row.

Risk: none. Verify: `grep -c evidence-kernel.md SKILL.md` returns 1.

---

## O2 — Declare the prompt skeletons authoritative

`references/prompts/extract.md` and `appraise.md` open with a coordinator-facing header naming
`references/schema.md §7`/`§8` `+ §1 + §12`. The header sits above the `---` on line 7, so a
correctly-substituted subagent prompt never contains it — but nothing states that the inline
JSON skeleton is sufficient, and an agent that follows the pointer reads all 905 lines
(~16k tokens) to confirm a shape it was already given.

Field parity was checked before writing this plan:

```text
schema §7 fields not in the extract.md skeleton:  page, section, text
```

All three are `quotes[]` sub-fields, and R17 requires the subagent to emit `quotes: []`. Their
absence from the skeleton is correct, not lossy. The skeleton is complete for its consumer.

Changes, in each of the four prompt files:

1. Move the `Contract:` line under a `<!-- coordinator notes -->` marker so its audience is
   unambiguous.
2. Add one line inside the prompt body, immediately above the `## Output` skeleton:

   > The JSON skeleton below is the complete and authoritative contract for your output. Do not
   > read `references/schema.md`; it contains nothing you need and costs you your context.

3. Re-run the parity check for `appraise.md` against §8 and `screen.md` against §5 before
   claiming completeness for those two. Only §7 was verified.

Saving: ~16k tokens per extraction and appraisal subagent that would otherwise follow the
pointer. At `systematic` (60 papers × 2 stages) that is up to 1.9M tokens of avoided reads.

Risk: low. If a skeleton turns out to be lossy, output fails assembler validation loudly rather
than silently — `assemble.py` rejects on missing required fields.

Verify: the parity check above, run for §5 and §8. Both must come back empty or explained.

---

## O3 — Split `schema.md`, keep `schema.md` as the index

905 lines / 64.3K / ~16k tokens is the single largest read in the skill, and every consumer
needs one or two of its fourteen sections.

**Constraint discovered while planning:** `schema.md` is referenced ~90 times across 24 files,
and most references are `§N`-anchored inside python docstrings:

```text
scripts/corpus.py §4  ×5      scripts/okf.py §13    ×4
scripts/fulltext.py §10 ×4    scripts/assemble.py §13 ×4
scripts/verify.py §9  ×3      scripts/store.py §12  ×3   ... and 20 more files
```

A naive split invalidates all of them. The design that avoids that:

**`references/schema.md` stays, and becomes a ~40-line index.** It keeps the filename every
existing reference already uses, and maps each `§N` to its file. A docstring saying
`references/schema.md §10` remains correct: the reader opens schema.md, sees `§10 → snapshot`,
and opens one 60-line file. **No script or reference file is edited.**

New layout:

```text
references/schema.md                  index: shared rules + §N -> file map (~40 lines)
references/schema/00-shared.md        §0   shared rules (S1-S7)
references/schema/01-receipt.md       §1
references/schema/02-taskboard.md     §2
references/schema/03-search.md        §3
references/schema/04-corpus.md        §4
references/schema/05-screening.md     §5
references/schema/06-adjudication.md  §6
references/schema/07-extraction.md    §7
references/schema/08-appraisal.md     §8
references/schema/09-verifier.md      §9
references/schema/10-snapshot.md      §10
references/schema/11-event.md         §11
references/schema/12-span.md          §12
references/schema/13-assembler.md     §13
references/schema/99-resolutions.md   R1-R24 addenda
```

Rules for the split:

- Section boundaries are the existing `## N.` headings; content is moved verbatim, never
  rewritten in the same commit as the move.
- `§0` shared rules (S1-S7) are duplicated **by reference only** — each per-record file opens
  with `Shared rules: references/schema/00-shared.md`. Do not inline them fourteen times.
- The R-numbered resolutions (`schema.md:867-905`) are cross-cutting; they go in one file, and
  the index lists which R-numbers bind which sections.
- Each new file carries its `§N` in an H1 so a reader landing there knows what they have.

Saving: extraction subagent context drops from ~16k to ~2k tokens. Main thread at Stage 0 reads
the 40-line index instead of the whole contract.

Risk: medium — it is a large mechanical move, and a section silently lost in the move would not
surface until a validation failure. Mitigate by asserting the move is lossless:

```bash
# every non-index line must survive somewhere under references/schema/
diff <(sed -n '7,905p' references/schema.md.orig | grep -v '^\s*$' | sort) \
     <(cat references/schema/*.md          | grep -v '^\s*$' | sort)
```

Do this split in its own commit, with no content edits, so the diff is reviewable as a move.

---

## O4 — `_common.py` and `_http.py`

Measured duplication across `scripts/`:

| Helper | Defined in |
|---|---|
| `read_json` | corpus, okf, html_report, verify |
| `read_jsonl` | corpus, html_report, okf, verify |
| `slugify` | corpus, library, okf, verify |
| `_emit` | eutils, okf, store, source |
| `atomic_write` | corpus, verify, render |
| `warn` | corpus, html_report, render |
| `utcnow` | okf, library, store |
| `now_iso` | corpus, render |
| `log` | fulltext, library |
| `sha256_text` | library, store |

Worse, the HTTP layer is a straight copy-paste: `fulltext.py:112-160` and `source.py` carry the
same `Http` class with identical backoff constants (`1.5 * (attempt + 1)` on network error,
`2.0 * (attempt + 1)` on 429/5xx) and the same per-host interval throttle. A retry-policy fix
today must be made twice, and `eutils.py` has a third, better implementation (token bucket).

Changes:

1. Add `scripts/_common.py`: `read_json`, `read_jsonl`, `atomic_write`, `utcnow`, `slugify`,
   `warn`, `log`, `sha256_text`, `_emit`. Underscore prefix so it never looks like a CLI entry
   point. Reconcile `utcnow` vs `now_iso` to one name (`utcnow`, matching S2's ISO-8601-Z rule)
   and keep `now_iso = utcnow` as an alias inside the module rather than editing every call site.
2. Add `scripts/_http.py`: the `Http` class from `fulltext.py`, verbatim, plus the per-host
   `HOST_INTERVAL` table. `fulltext.py` and `source.py` import it.
3. Leave `eutils.py`'s token bucket alone. It is NCBI-specific (API-key rate switch, 3/s vs
   10/s) and merging it into the generic client would couple two different politeness contracts.
   Note the deliberate split in `_http.py`'s docstring.

Saving: ~250 lines, and one place to fix a retry bug.

Risk: low-moderate. Every import in the tree is `import store`-style sibling import
(`sys.path` manipulation at the top of each script) — follow that existing pattern exactly
rather than introducing a package. `tests/` and `eval/` must still run: they import siblings the
same way.

Verify: `python3 scripts/eval.py` (fixture mode, no network) plus the four `tests/test_*.py`
before and after. Byte-identical output required.

---

## O5 — Parallelize the acquisition ladder

`fulltext.py:1032` is a plain serial loop over the corpus. At `systematic` (60 papers) and `max`
(100), each record walks up to seven network rungs one at a time. This is the largest
wall-clock cost in the pipeline.

The work is already shaped for it: `acquire_record(rec, ctx, from_tier=...)` mutates its own
record in place and returns a result dict; `write_corpus` runs once after the loop. Records are
independent.

The throttle is what makes this delicate. `Http._wait` at `fulltext.py:130-138` keeps
`self._last[host]` unlocked, and the intervals are small (0.15-0.34s) — meaning serial cost is
dominated by round-trip latency and PDF work, not by the politeness floor. Parallelism converts
latency-bound time into throttle-bound time, which is the real win, but only if per-host
serialization survives.

Changes:

1. Guard `Http._wait` with a `threading.Lock`, and make the wait per host: hold a
   `dict[str, Lock]` so two workers hitting `pmc.ncbi.nlm.nih.gov` still serialize against each
   other at 0.34s while a third worker hitting `api.unpaywall.org` proceeds.
2. Replace the loop body with a `ThreadPoolExecutor`, `max_workers` from a new
   `--workers` flag defaulting to **4**, and from `config.json` `budgets.max_parallel` when the
   caller passes it. Preserve submission order in `results` by collecting futures in order.
3. Serialize the side-channels: `log()` appends to `engine.log`, and `register_acquisition`
   writes snapshots and appends to `events.jsonl`. `events.jsonl` append ordering is
   **contract** (R23: file order is authoritative, `event_id` is a counter unique within the
   run). Take a module-level lock around event append + `event_id` allocation, or the counter
   races and R23 breaks.
4. Keep `--limit` semantics: with a pool, "first N selectable" must be decided **before**
   submission, not by breaking out of a loop.
5. Leave `--offline` and fixture-mode single-threaded so tests stay deterministic.

Risk: **highest in this plan.** Concurrency plus an append-ordered event log plus in-place
record mutation. R23 is the thing most likely to break, and it breaks silently. Requires:

- A test that runs `acquire` with `--workers 4` against fixtures and asserts `event_id`s are
  contiguous, unique, and in file order.
- A test asserting per-host spacing is never violated under 4 workers.

Both are new. Do not ship this without them.

---

## O6 — Repo hygiene

1. Two deletions are staged-but-uncommitted in the working tree:
   `deep-research-pipeline.html`, `deep-research.workflow.json`. Commit or restore them; a dirty
   tree of this age obscures every subsequent diff.
2. Move design records out of the skill root into `docs/`: `PLAN.md` (428 lines),
   `VALIDATION_ARCHITECTURE_UPDATE_PLAN.md`, and this file. They carry no token cost (nothing
   auto-loads them), but the root should read as `SKILL.md` + `README.md` + the four working
   directories. Update `README.md:3`, which points at `PLAN.md` as the design record.

Risk: none beyond the link update.

---

## O7 — Flip the evidence-kernel gate

Not a code change; the last open item of the validation plan (D7/R20, migration step 9). The
gate is built, wired, and always reported, but `gates.evidence_kernel` defaults false, so kernel
violations warn rather than block.

Precondition, unchanged from D7: **one live end-to-end run must pass with the gate on.** Until
that exists, flipping the default converts warnings into blocked promotions on real runs.

Sequence:

1. Run a small real question end to end at `standard` with `verify.py --gate` and
   `assemble.py --strict`.
2. Read `outputs/verification.json`'s `evidence_kernel` block. `C-SNAPSHOT`, `C-SPAN`,
   `C-FRESH-FETCH`, `C-ASSEMBLER` must all pass, not merely fail to block.
3. Only then flip the default and update `SKILL.md:141-145`, which currently documents the gate
   as off.

Note the interaction with R22: a run whose sources were all `register`ed rather than fetched
validates structurally but fails `C-FRESH-FETCH` by design. The dry run must include genuinely
fresh retrievals or it proves nothing.

---

## Order and commits

| # | Change | Commit shape | Depends on |
|---|---|---|---|
| O1 | Route `evidence-kernel.md` | 1 line | — |
| O2 | Prompt skeletons authoritative | 4 files, doc-only | parity check for §5, §8 |
| O3 | Split schema, keep index | move-only, no content edits | O2 (so prompts stop pointing at it) |
| O4 | `_common.py` + `_http.py` | one commit per module | eval + tests green first |
| O5 | Parallel acquire | code + 2 new tests | O4 (`_http.py` is where the lock lands) |
| O6 | Hygiene | separate, mechanical | — |
| O7 | Gate default | 1 line + doc | a passing live dry run |

O1 and O2 are ~20 minutes together and capture most of the token saving. O3 is the largest
context win and the largest mechanical diff. O5 is the only wall-clock win and the only change
that can corrupt a run — it goes last among the code changes, behind its own tests.

## Non-goals

- No rewrite of `okf.py` (3020 lines) or `html_report.py` (2473 lines). They are executed, never
  read by the model, so their size is a maintenance question and not a context one. Splitting
  them buys nothing this plan is about.
- No caching layer over `store.Store`. It already caches snapshots per process and each of
  `assemble`/`verify`/`okf` instantiates exactly one; re-hashing across the three stage-8
  processes costs milliseconds at run scale.
- No merge of `eutils.py`'s token bucket into the generic HTTP client (see O4.3).
- No change to any record contract. O3 moves schema text between files; it does not edit it.

---

## Implementation record (2026-09-08)

Baseline before the work: `eval.py` 7/7, `unittest` 17/17. After: **eval 7/7, unittest 25/25**,
all twelve script CLIs load and `--help`.

| # | Planned | Done | Deviation |
|---|---|---|---|
| O1 | Route `evidence-kernel.md` | yes | none |
| O2 | Prompt skeletons authoritative | yes, all four prompts | none; parity verified for §5, §6, §7, §8 |
| O3 | Split schema, keep index | yes | none; 905 → 57-line index, move proven lossless |
| O4 | `_common.py` + `_http.py` | `_common.py` only | **narrowed — see below** |
| O5 | Parallel acquire | yes, with 8 new tests | none in substance; the race was real |
| O6 | Hygiene | declined | **see below** |
| O7 | Gate default | not attempted | still blocked on a live dry run |

### O4 was narrowed, and the reason matters

The plan asserted the duplicated helpers were copy-paste and that `source.py`'s `Http` was
`fulltext.py`'s verbatim. Neither held. The divergences are load-bearing:

- `source.py`'s `Http` sets `session.trust_env = False`, so ambient proxies and `~/.netrc` are
  ignored. That enforces **SKILL.md invariant 9** — no credentials, no institutional proxy
  access, no paywall circumvention. `fulltext.py`'s client does not set it. Merging them
  "verbatim" as planned would have silently deleted a security guard.
- `read_jsonl` has four different error policies (warn-and-skip in `corpus.py` and
  `html_report.py`; `OkfError` in `okf.py`; `FatalError` in `verify.py`) because a report
  renderer should tolerate a bad line and a publisher must not.
- `atomic_write` has three different durability/permission policies (fsync; chmod-vs-umask;
  plain `.tmp`).
- `sha256_text` is strict UTF-8 in `store.py` and `errors="replace"` in `library.py`.
  `store.py`'s is the **evidence contract** (`content_hash`, `source_id`); swapping in the
  lenient encoder would change digests for non-UTF-8 text.
- `slugify` in `library.py` is case-preserving and filename-safe; `okf.py`/`verify.py`'s is an
  ASCII-fold concept slug. Same name, two jobs.

So `_http.py` was **not** created, and `_common.py` holds only the provably identical helpers:
`utcnow`/`now_iso`, `read_json`, `slugify` (the `okf`≡`verify` one), `emit_json` (the
`source`≡`store` one). Thirteen definitions became four. Every divergence above is documented
in `_common.py`'s module docstring so a later reader does not "finish the job" and break one.

Consolidating the rest would mean parameterising each by error policy, durability and
encoding — more coupling than it removes, in exactly the code paths where a silent behaviour
change is least acceptable.

### O5: the race was real, and both tests were proven to catch it

`store.append_event` read the event log, allocated `ev-000N`, checked for duplicates and only
then took an `flock`. The lock made the *write* atomic, not the allocation. Verified by
disabling `store._EVENT_LOCK` and re-running: **96 appends collapsed to 73 unique `event_id`s**
— 23 duplicates, R23 violated. With the lock, 96/96 unique, contiguous, in file order.

Same check for the throttle: with `Http._host_lock` disabled, six threads hit one host with a
gap of **0.0001s** against a required 0.34s — a burst against NCBI. With it, spacing holds.

A test that passes with and without the fix would have been worthless here, so both were run
against the broken code before being kept. `tests/test_concurrency.py`, 8 tests.

Also changed as part of O5: `engine.log` appends are lock-serialised, `--limit` selects before
submission (so it means "first N selectable", not "first N to finish"), and `--offline` forces
serial so fixture runs stay deterministic. Snapshot writes needed nothing: they were already
`O_EXCL` with `EEXIST` tolerated.

### O6 was declined, not forgotten

Moving `PLAN.md`, `VALIDATION_ARCHITECTURE_UPDATE_PLAN.md` and this file into `docs/` would
break **125 inbound references** (104 to `PLAN.md` alone), most of them `§`-anchored inside the
docstrings of evidence-critical scripts. The gain is a tidier root; the cost is a 25-file
mechanical diff that would bury the O5 concurrency change under noise in review. Declined as
poor value. Reversible at any time with a careful `sed` if the root layout matters more later.

The two uncommitted deletions (`deep-research-pipeline.html`, `deep-research.workflow.json`)
were left as they were found: deleting them is someone's uncommitted decision, and committing
on its behalf was not asked for.
