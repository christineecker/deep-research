Create a standalone deep-research repo (or import one from a wiki-manager wiki).

Do not read SKILL.md's staged pipeline narrative. This is a one-shot setup call.

Parse `$ARGUMENTS` for:
- `<path>` — required, positional. Where the new repo is created.
- `--from-wiki <wiki-root>` — optional. Legacy wiki root to seed the registry from
  (compatibility only).

If `<path>` is missing, ask for it rather than guessing.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/research.py" init <path> [--from-wiki <wiki-root>]
   ```
2. Report the repo path created and that `--repo <path>` is now usable with every other
   `/deep-research:*` command.

This repo is the prerequisite for `pool-add`, `summarize`, `summarize-set`,
`bib-export`, and `pdf-lookup` — all of them fail without a `data/papers/registry.jsonl`
in place first.
