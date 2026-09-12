Answer a scientific question from the papers already in a standalone repo. No run needed.

Do not read SKILL.md's staged pipeline narrative — this is not a pipeline run. It searches
what the repo already holds, and answers only from evidence that verifies right now.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required.
- the question itself — everything that isn't a flag.
- `--k N` — how many papers to consider (default 8).
- `--passages-per-paper N` — verified passages per paper (default 3).
- `--project <slug>` — report appraisal status scoped to this project.
- `--no-semantic` — lexical retrieval only; skips loading the embedding model.

Steps:

1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/ask.py" retrieve --repo <path> --question "<question>" [--k N] [--passages-per-paper N] [--project <slug>] [--no-semantic]
   ```

2. Write the answer **only** from what the script returned. The rules are not stylistic:

   - Every factual sentence must rest on a `claims[]` entry or a `passages[]` entry from
     the output. Anything not in the bundle does not go in the answer — not from your own
     knowledge of the literature, not from the paper titles, not from inference across
     papers that no returned span supports.
   - Cite each one by its `evidence_id` (and `source_id` when quoting a passage
     verbatim). Quote exactly; the `excerpt`/`text` fields are the verified bytes.
   - Never cite anything from `unverified[]`. Those spans failed re-verification against
     the snapshot store — the source has changed, moved, or been tampered with since it
     was extracted. Say that such evidence exists and was excluded, if it's material.
   - If `papers[]` is empty, or no paper carries a claim or passage, say the library has
     nothing on this question. That is the answer. Do not fall back to answering from
     memory, and do not soften it into a general statement about the topic.

3. Close with what the answer rests on: how many papers, whether they are extracted and
   appraised (`extraction_status`, `appraised`), and anything in `notes[]` that bears on
   coverage — a missing chunk index or embeddings file means retrieval saw less than the
   repo actually holds, and the user should know before trusting the sweep.

This command answers *from the library*. It is not a literature search: it cannot find a
paper the repo has never seen. When the answer is thin because the repo is thin, say so
and offer a full `deep-research` run, which does search PubMed.
