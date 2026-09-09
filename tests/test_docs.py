"""Docs-vs-CLI consistency check.

Imports each ``scripts/*.py`` module that exposes a ``build_parser()`` and
introspects the real ``argparse.ArgumentParser`` it returns (no subprocess
execution, no ``main()`` call — just constructing the parser object) to
collect the subcommand names that script actually accepts. Then greps
README.md, SKILL.md, and references/*.md for ``<script>.py <subcommand>``
references and fails if any referenced subcommand does not exist.

Import (rather than static AST parsing) is used deliberately: some scripts
delegate part of their subcommand tree to a sibling module (e.g. corpus.py's
`task` subcommand is built by `taskboard.py`'s `build_task_parser`), so the
only reliable source of truth is the assembled parser object itself.
"""

from __future__ import annotations

import argparse
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
DOC_FILES = [ROOT / "README.md", ROOT / "SKILL.md", *sorted((ROOT / "references").glob("*.md"))]

sys.path.insert(0, str(ROOT / "tests"))
from helpers import load_script  # noqa: E402  (sibling test module)

# <script>.py <word> [<word2>], word chars/hyphens only, as used throughout the docs.
DOC_REF_RE = re.compile(
    r"\b([a-z_]+\.py)\s+([a-z][a-z0-9_-]*)(?:\s+([a-z][a-z0-9_-]*))?"
)


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:  # noqa: SLF001 (argparse offers no public accessor)
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def parse_script_commands(name: str) -> tuple[set[str], set[str]]:
    """Return (top_level_names, nested_names) of a script's real argparse subcommands.

    top_level_names: subcommands accepted directly (e.g. `add`, `list`, `task`).
    nested_names: subcommands one level under any top-level command that itself
    has its own subparsers (e.g. `task`'s `create`/`claim`/.../`stats`).
    """
    module = load_script(name)
    if not hasattr(module, "build_parser"):
        return set(), set()

    parser = module.build_parser()
    action = _subparsers_action(parser)
    if action is None:
        return set(), set()

    top_level = set(action.choices.keys())
    nested: set[str] = set()
    for subparser in action.choices.values():
        sub_action = _subparsers_action(subparser)
        if sub_action is not None:
            nested.update(sub_action.choices.keys())

    return top_level, nested


def collect_script_commands() -> dict[str, tuple[set[str], set[str]]]:
    return {
        path.name: parse_script_commands(path.name)
        for path in sorted(SCRIPTS_DIR.glob("*.py"))
        if path.name != "_common.py"
    }


def collect_doc_references() -> list[tuple[str, str, str, str | None]]:
    """Return (doc_file_name, script_name, word1, word2) for every match."""
    refs = []
    for doc in DOC_FILES:
        text = doc.read_text(encoding="utf-8")
        for match in DOC_REF_RE.finditer(text):
            script, word1, word2 = match.group(1), match.group(2), match.group(3)
            refs.append((doc.name, script, word1, word2))
    return refs


class DocsReferenceRealCommandsTest(unittest.TestCase):
    def test_all_doc_referenced_subcommands_exist(self):
        script_commands = collect_script_commands()
        # Sanity: make sure introspection actually found something, so a build_parser
        # refactor that breaks this test's assumptions is loud. A few scripts (e.g.
        # eval.py, status.py, taskboard.py) have no build_parser / no add_subparsers
        # at all and legitimately parse to no subcommands.
        self.assertTrue(script_commands)
        scripts_with_subcommands = {n for n, (top, _) in script_commands.items() if top}
        self.assertTrue(scripts_with_subcommands)

        failures = []
        for doc_name, script, word1, word2 in collect_doc_references():
            if script not in script_commands:
                continue  # not a scripts/*.py reference (e.g. a stray "foo.py bar")
            top, nested = script_commands[script]
            if not top:
                continue  # flat CLI (no subcommands) -- word1 isn't a subcommand claim
            if word1 not in top:
                failures.append(
                    f"{doc_name}: '{script} {word1}' -- '{word1}' is not a real "
                    f"top-level subcommand of {script} (real: {sorted(top)})"
                )
                continue
            # The only two-word `<script>.py <cmd> <subcmd>` convention actually used in
            # the docs is corpus.py's `task <subcmd>`; a captured word2 elsewhere (e.g. a
            # shell pipeline's next token, as in "okf.py selftest && python3 ...") is not
            # a claim about a subcommand and would otherwise be a false positive.
            if word1 == "task" and word2 is not None and word2 not in nested:
                failures.append(
                    f"{doc_name}: '{script} {word1} {word2}' -- '{word2}' is not a real "
                    f"subcommand of {script}"
                )

        self.assertEqual(failures, [], "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
