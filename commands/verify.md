Run consistency checks. `verify.py` is subcommand-based — pick the mode that matches
what exists.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for a mode, then that mode's flags:

**`run`** (default) — every check over a full run directory:
- `--run-dir <dir>` — required.
- `--repo <path>` — optional, standalone repo root for global source fallback
  (default: inferred from `--run-dir` under `<repo>/runs/<slug>`).
- `--wiki <wiki-root>` — optional, enables C-OKF via `okf.py validate`.
- `--report <path>` — optional (default `<run-dir>/outputs/report.md`).
- `--json` — print the verification JSON to stdout.
- `--markdown <file>` — also write a human-readable summary here.
- `--gate` / `--no-gate` — enforce evidence-kernel checks (C-SNAPSHOT, C-SPAN,
  C-FRESH-FETCH, C-ASSEMBLER) as hard failures blocking stage 8. Default off.
  Snapshot/span tamper failures are hard failures either way.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/verify.py" run --run-dir <dir> [--repo <p>] [--wiki <w>] [--json] [--markdown <f>] [--gate|--no-gate]
```

**`single-paper-summary`** — verify one §14 summary record. Still needs a run directory:
- `--summary <path>` — required, `workspace/summaries/<slug>.json`.
- `--run-dir <dir>` — required.
- `--repo <path>` — optional (default: inferred from `--run-dir`).
- `--json`.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/verify.py" single-paper-summary --summary <path> --run-dir <dir> [--repo <p>] [--json]
```

**`paper-summary-set`** — verify one §15 set manifest. Still needs a run directory:
- `--summary-set <path>` — required, `workspace/summary-set.json`.
- `--run-dir <dir>` — required.
- `--repo <path>` — optional.
- `--json`.

Call:
```
python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/verify.py" paper-summary-set --summary-set <path> --run-dir <dir> [--repo <p>] [--json]
```

Exit codes (report these, don't swallow them): `0` = no failures (warnings allowed),
`1` = a check failed, `2` = fatal error. Never modifies `report.md`.
