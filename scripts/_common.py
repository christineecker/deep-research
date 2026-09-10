"""Shared helpers for the deep-research scripts.

Imported as a sibling module, matching the existing pattern in `fulltext.py` and `verify.py`:

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import utcnow, read_json

The underscore prefix marks this as a library, not a CLI entry point; every other `scripts/*.py`
file has a `main()` and an argparse parser, and this one deliberately does not.

Scope: helpers that were **provably identical** across two or more scripts. That is a narrower
set than a glance at the duplicate function names suggests.


What is deliberately NOT here, and must not be "helpfully" consolidated later
----------------------------------------------------------------------------

Several helpers share a name across scripts but differ in behaviour, and the differences are
load-bearing. Merging them would be a silent behaviour change in evidence-integrity or
security-relevant code:

`read_jsonl` — four implementations, four **deliberate error policies**. `corpus.py` warns and
    skips a malformed line (or raises `UserError` under `strict`); `html_report.py` warns and
    skips, because a report should still render; `okf.py` raises `OkfError` and `verify.py`
    raises `FatalError`, because a publisher and a verifier must fail closed on corrupt input.
    One shared implementation would have to be parameterised by exception type and tolerance,
    which is more coupling than it removes.

`atomic_write` — three implementations, three durability/permission policies. `corpus.py`
    fsyncs before `os.replace` (append-only ledger); `render.py` chmods against the umask
    (files handed to Quarto); `verify.py` uses a plain sibling `.tmp`. These are different
    guarantees, not accidents.

`sha256_text` — `store.py` encodes UTF-8 **strictly**; `library.py` encodes with
    `errors="replace"`. `store.py`'s digest is the evidence contract (`content_hash`,
    `source_id`, schema §10-§11), and quietly swapping in a lenient encoder would change
    hashes for non-UTF-8 text. Never merge these two.

`slugify` — `okf.py` and `verify.py` share one (ASCII-fold, lowercase, `"untitled"` fallback,
    used for concept slugs) and that one lives here. `library.py` has a different one
    (case-preserving, allows `._~`, `"unknown"` fallback) because it names files in the shared
    PDF library. Two functions, one name, two jobs.

`warn` — `html_report.py` also accumulates into a module-level `WARNINGS` list that it renders
    into the report. `corpus.py` and `render.py` only print.

`log` — `library.py` swallows `OSError`, `fulltext.py` does not, and each stamps its own module
    name into `engine.log`.

`Http` — `fulltext.py` and `source.py` look like copy-paste but are not. `source.py` sets
    `session.trust_env = False`, so ambient proxies and `~/.netrc` are ignored; that enforces
    SKILL.md invariant 9 (no credentials, no institutional proxy access, no paywall
    circumvention). `fulltext.py`'s client also carries `offline` mode and `stream`/`params`
    support that `source.py` has no use for. Unifying them would either drop the hardening or
    silently widen `fulltext.py`'s behaviour.

`eutils.py`'s throttle stays where it is: it is a token bucket with an NCBI API-key rate switch
(3/s without a key, 10/s with one), a different politeness contract from the generic per-host
interval used elsewhere.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import unicodedata
from pathlib import Path

__all__ = ["utcnow", "now_iso", "read_json", "slugify", "emit_json", "wiki_root_for_run",
           "repo_root_for_run"]


def utcnow() -> str:
    """ISO-8601 UTC with a literal `Z`, per schema rule S2 (`references/schema/00-shared.md`)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: `corpus.py` and `render.py` spell it this way; identical function, kept as an alias so
#: their call sites do not have to change.
now_iso = utcnow


def wiki_root_for_run(run_dir) -> Path:
    """`<wiki>/outputs/deep-research/<slug>/` -> `<wiki>` (`SKILL.md` "Run directory").

    Provably identical in `library.py` and `store.py`; consolidated here.
    """
    run_dir = Path(run_dir).expanduser().resolve()
    parents = run_dir.parents
    if len(parents) >= 3 and parents[0].name == "deep-research" and parents[1].name == "outputs":
        return parents[2]
    return run_dir


def repo_root_for_run(run_dir) -> Path | None:
    """`<repo>/runs/<slug>/` -> `<repo>` (POOL_ARCHITECTURE_IMPLEMENTATION_PLAN.md
    "Target Repository Layout"). None when `run_dir` is not inside a standalone repo's
    `runs/` directory — callers fall back to `wiki_root_for_run` or run-local behavior.

    `repo_root` is the default storage root going forward; `wiki_root_for_run` remains for
    legacy wiki-backed runs (`<wiki>/outputs/deep-research/<slug>/`) only.
    """
    run_dir = Path(run_dir).expanduser().resolve()
    parents = run_dir.parents
    if len(parents) >= 1 and parents[0].name == "runs":
        return parents[1] if len(parents) >= 2 else None
    return None


def read_json(path: Path):
    """Parse one JSON file. Raises whatever `json` raises; callers own their error policy."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def slugify(text: str, maxlen: int = 80) -> str:
    """ASCII-fold to a lowercase hyphenated slug. Concept slugs (`okf.py`, `verify.py`).

    Not for filenames in the shared PDF library — `library.py` has its own, see the module
    docstring.
    """
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(text) > maxlen:
        text = text[:maxlen].rstrip("-")
    return text or "untitled"


def emit_json(payload: dict, *, code: int = 0) -> int:
    """Print a payload as pretty JSON and return an exit code. (`source.py`, `store.py`.)"""
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return code
