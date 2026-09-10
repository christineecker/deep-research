#!/usr/bin/env python3
"""tutorial.py - guided local tutorial runner for the standalone-repo quickstart demo.

Subcommands
  quickstart   [--repo <path>] [--project <slug>] [--force]

Human-facing walkthrough docs live in docs/ (see docs/quickstart.html).

Environment: python3, stdlib only. No pip installs.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "scripts" / "fixtures"


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def _run(args: list[str]) -> subprocess.CompletedProcess:
    print("$ " + " ".join(args))
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=120)
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)
    return proc


def _copy_fixture(name: str, dest: Path) -> None:
    src = FIXTURES / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    print(f"copied {src.relative_to(ROOT)} -> {dest}")


def cmd_quickstart(args) -> int:
    repo = Path(args.repo).expanduser().resolve()
    project = args.project
    if repo.exists() and any(repo.iterdir()) and not args.force:
        emit({
            "schema_version": 1,
            "status": "error",
            "command": "quickstart",
            "error": f"{repo} already exists and is not empty; pass --force to reuse it",
        })
        return 1

    _run([sys.executable, "scripts/research.py", "init", str(repo)])
    project_cmd = [
        sys.executable, "scripts/research.py", "project", "create", project,
        "--repo", str(repo), "--title", args.title,
    ]
    if args.force:
        project_cmd.append("--force")
    _run(project_cmd)
    _run([
        sys.executable, "scripts/registry.py", "import-bib", "--repo", str(repo),
        "--file", str(FIXTURES / "sample-refs.bib"),
    ])

    run_dir = repo / "runs" / "tutorial-run"
    _copy_fixture("sample-corpus.jsonl", run_dir / "corpus.jsonl")
    _copy_fixture(
        "sample-extraction-quadas2.json",
        run_dir / "workspace" / "extractions" / "pmid-12345678.json",
    )
    _copy_fixture(
        "sample-appraisal-quadas2.json",
        run_dir / "workspace" / "appraisals" / "pmid-12345678.json",
    )
    _copy_fixture("sample-protocol.md", repo / "projects" / project / "protocol.md")

    _run([
        sys.executable, "scripts/registry.py", "promote", "--repo", str(repo),
        "--run-dir", str(run_dir), "--no-verify",
    ])
    _run([
        sys.executable, "scripts/registry.py", "appraise-promote", "--repo", str(repo),
        "--run-dir", str(run_dir), "--project", project,
    ])
    _run([
        sys.executable, "scripts/registry.py", "bib", "--repo", str(repo),
        "--out", str(repo / "projects" / project / "refs.bib"),
        "--select", "appraised", "--project", project,
    ])

    emit({
        "schema_version": 1,
        "status": "ok",
        "command": "quickstart",
        "repo": str(repo),
        "project": project,
        "project_dir": str(repo / "projects" / project),
        "tutorial_docs": str(ROOT / "docs" / "quickstart.html"),
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tutorial.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("quickstart", help="run the local standalone-repo tutorial")
    s.add_argument("--repo", default="/tmp/deep-research-tutorial-demo")
    s.add_argument("--project", default="diagnostic-demo")
    s.add_argument("--title", default="Diagnostic Demo Manuscript")
    s.add_argument("--force", action="store_true", help="reuse an existing non-empty repo")
    s.set_defaults(func=cmd_quickstart)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
