#!/usr/bin/env python3
"""tutorial.py - guided local tutorial runner and static tutorial-site builder.

Subcommands
  quickstart   [--repo <path>] [--project <slug>] [--force]
  build-site   --out <dir>

Environment: python3, stdlib only. No pip installs.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TUTORIALS = ROOT / "tutorials"
FIXTURES = TUTORIALS / "fixtures"
DOCS = (
    "README.md",
    "01-standalone-repo-quickstart.md",
    "02-add-papers-to-the-pool.md",
    "03-run-a-review-project.md",
    "04-appraise-with-frameworks.md",
    "05-export-a-manuscript-report.md",
    "06-troubleshooting.md",
)


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
        "tutorial_docs": str(TUTORIALS / "README.md"),
    })
    return 0


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)

    def repl(match: re.Match[str]) -> str:
        label, href = match.group(1), match.group(2)
        if href.endswith(".md"):
            href = href[:-3] + ".html"
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, escaped)


def markdown_to_html(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    in_code = False
    code_lines: list[str] = []
    in_ul = False
    in_ol = False
    para: list[str] = []
    table_rows: list[list[str]] = []

    def close_para() -> None:
        nonlocal para
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para = []

    def close_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        header, *body = table_rows
        out.append("<table>")
        out.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) +
                   "</tr></thead>")
        out.append("<tbody>")
        for row in body:
            out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
        out.append("</tbody></table>")
        table_rows = []

    def close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for line in lines:
        if line.startswith("```"):
            close_para()
            close_table()
            close_lists()
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            close_para()
            close_table()
            close_lists()
            continue
        if stripped.startswith("#"):
            close_para()
            close_table()
            close_lists()
            level = min(len(stripped) - len(stripped.lstrip("#")), 3)
            text = stripped[level:].strip()
            out.append(f"<h{level}>{_inline(text)}</h{level}>")
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            close_para()
            close_lists()
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} and "-" in c for c in cells):
                continue
            table_rows.append(cells)
            continue
        if stripped.startswith("- "):
            close_para()
            close_table()
            if not in_ul:
                close_lists()
                out.append("<ul>")
                in_ul = True
            out.append("<li>" + _inline(stripped[2:].strip()) + "</li>")
            continue
        ordered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if ordered:
            close_para()
            close_table()
            if not in_ol:
                close_lists()
                out.append("<ol>")
                in_ol = True
            out.append("<li>" + _inline(ordered.group(1).strip()) + "</li>")
            continue
        close_lists()
        close_table()
        para.append(stripped)

    close_para()
    close_table()
    close_lists()
    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    body = "\n".join(out)
    return HTML_TEMPLATE.format(title=html.escape(title), body=body, nav=_nav_html())


def _nav_html() -> str:
    links = []
    for doc in DOCS:
        label = "Home" if doc == "README.md" else doc[3:-3].replace("-", " ").title()
        href = "index.html" if doc == "README.md" else doc[:-3] + ".html"
        links.append(f'<a href="{href}">{html.escape(label)}</a>')
    return "\n".join(links)


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <aside>
    <h1>Deep Research Tutorials</h1>
    <nav>
{nav}
    </nav>
    <p><a href="fixtures/">Download fixtures</a></p>
  </aside>
  <main>
{body}
  </main>
</body>
</html>
"""

CSS = """
:root {
  color-scheme: light dark;
  --bg: #fafafa;
  --fg: #202124;
  --muted: #5f6368;
  --line: #dadce0;
  --code: #f1f3f4;
  --link: #0b57d0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #151515;
    --fg: #e8eaed;
    --muted: #bdc1c6;
    --line: #3c4043;
    --code: #252628;
    --link: #8ab4f8;
  }
}
body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
}
aside {
  border-right: 1px solid var(--line);
  min-height: 100vh;
  padding: 24px;
  position: sticky;
  top: 0;
  align-self: start;
}
aside h1 {
  font-size: 18px;
  margin: 0 0 20px;
}
nav a {
  display: block;
  color: var(--link);
  text-decoration: none;
  padding: 6px 0;
}
main {
  max-width: 920px;
  padding: 40px 48px 80px;
}
h1, h2, h3 {
  line-height: 1.2;
}
a {
  color: var(--link);
}
pre {
  background: var(--code);
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow-x: auto;
  padding: 14px;
}
code {
  background: var(--code);
  border-radius: 4px;
  padding: 1px 4px;
}
pre code {
  background: transparent;
  padding: 0;
}
table {
  border-collapse: collapse;
  width: 100%;
}
th, td {
  border: 1px solid var(--line);
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
}
blockquote {
  border-left: 3px solid var(--line);
  color: var(--muted);
  margin-left: 0;
  padding-left: 16px;
}
@media (max-width: 760px) {
  body {
    display: block;
  }
  aside {
    min-height: auto;
    position: static;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }
  main {
    padding: 24px;
  }
}
"""


def cmd_build_site(args) -> int:
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "style.css").write_text(CSS.strip() + "\n", encoding="utf-8")
    for doc in DOCS:
        src = TUTORIALS / doc
        title = "Deep Research Tutorials" if doc == "README.md" else src.stem
        html_out = "index.html" if doc == "README.md" else doc[:-3] + ".html"
        (out / html_out).write_text(
            markdown_to_html(src.read_text(encoding="utf-8"), title),
            encoding="utf-8",
        )
    if FIXTURES.exists():
        shutil.copytree(FIXTURES, out / "fixtures", dirs_exist_ok=True)
    emit({
        "schema_version": 1,
        "status": "ok",
        "command": "build-site",
        "out": str(out),
        "index": str(out / "index.html"),
        "pages": len(DOCS),
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

    s = sub.add_parser("build-site", help="build static HTML tutorials")
    s.add_argument("--out", required=True, help="output directory for static HTML")
    s.set_defaults(func=cmd_build_site)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
