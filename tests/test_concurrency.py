"""Concurrency invariants for `fulltext.py acquire --workers`.

Two things break silently when the acquisition ladder is parallelised, so both are pinned here:

  * R23 — `event_id`s must be unique within a run and allocated in append order. The `flock`
    in `store.append_event` makes the write atomic but not the read-allocate-check sequence
    that precedes it; without `store._EVENT_LOCK` two threads allocate the same `ev-000N`.
  * Politeness — the per-host interval must remain a real floor under N workers, or the pool
    turns a rate limit into a burst.

Neither test touches the network.
"""

from __future__ import annotations

import concurrent.futures as cf
import re
import time
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from helpers import load_script, make_run


store = load_script("store.py")
fulltext = load_script("fulltext.py")


def _event(etype: str, snapshot: dict) -> dict:
    """A structurally valid §11 event for `snapshot`. `fresh` is false (R22)."""
    return {
        "type": etype,
        "source_id": snapshot["source_id"],
        "url": snapshot["url"],
        "sha256": snapshot["content_hash"].split(":", 1)[-1],
        "fresh": False,
    }


class EventLogUnderConcurrencyTest(unittest.TestCase):
    """R23 holds when many threads register snapshots at once."""

    def test_concurrent_appends_keep_event_ids_unique_and_ordered(self):
        threads, per_thread = 8, 6
        with TemporaryDirectory() as tmp:
            _, run = make_run(Path(tmp))

            # One snapshot per event: `register` events must name an existing source_id.
            snaps = []
            for i in range(threads * per_thread):
                res = store.write_snapshot_result(
                    run, url=f"https://example.org/paper-{i}", text=f"Body of paper {i}.",
                    title=f"Paper {i}", access="full_text", origin="web", paper=None)
                snaps.append(res["snapshot"])

            # write_snapshot_result logs its own event per snapshot; the pooled appends
            # below are additional.
            baseline = len(store.read_events(run))

            def worker(chunk):
                for snap in chunk:
                    store.append_event(run, _event("register", snap))

            chunks = [snaps[i::threads] for i in range(threads)]
            with cf.ThreadPoolExecutor(max_workers=threads) as pool:
                for fut in [pool.submit(worker, c) for c in chunks]:
                    fut.result()

            events = store.read_events(run)
            ids = [e["event_id"] for e in events]

            self.assertEqual(len(ids), baseline + threads * per_thread)
            self.assertEqual(len(set(ids)), len(ids), "duplicate event_id — R23 violated")
            for eid in ids:
                self.assertRegex(eid, r"^ev-\d{4,}$")
            # File order is authoritative (R23): the counter increases down the file.
            nums = [int(re.match(r"^ev-(\d+)$", e).group(1)) for e in ids]
            self.assertEqual(nums, sorted(nums), "event_ids not allocated in append order")
            self.assertEqual(nums, list(range(1, len(nums) + 1)), "counter has gaps")

    def test_events_file_has_no_torn_lines(self):
        with TemporaryDirectory() as tmp:
            _, run = make_run(Path(tmp))
            res = store.write_snapshot_result(
                run, url="https://example.org/one", text="One.", title="One",
                access="full_text", origin="web", paper=None)
            snap = res["snapshot"]

            def worker(_):
                store.append_event(run, _event("read", snap))

            with cf.ThreadPoolExecutor(max_workers=8) as pool:
                for fut in [pool.submit(worker, i) for i in range(40)]:
                    fut.result()

            # read_events parses every line; a torn write raises or drops records.
            self.assertEqual(len(store.read_events(run)), 41)


class HostThrottleUnderConcurrencyTest(unittest.TestCase):
    """The per-host interval stays a floor when workers share one `Http`."""

    def test_same_host_requests_stay_spaced_across_threads(self):
        http = fulltext.Http(email=None)
        host_url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/pdf/"
        interval = fulltext.HOST_INTERVAL["pmc.ncbi.nlm.nih.gov"]
        stamps: list[float] = []
        guard = __import__("threading").Lock()

        def tick(_):
            http._wait(host_url)              # the throttle only; no request is made
            with guard:
                stamps.append(time.monotonic())

        n = 6
        with cf.ThreadPoolExecutor(max_workers=n) as pool:
            for fut in [pool.submit(tick, i) for i in range(n)]:
                fut.result()

        stamps.sort()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        slack = 0.05                          # scheduler jitter, not throttle slack
        for gap in gaps:
            self.assertGreaterEqual(
                gap, interval - slack,
                f"host interval collapsed under concurrency: {gap:.3f}s < {interval}s")

    def test_different_hosts_do_not_block_each_other(self):
        http = fulltext.Http(email=None)
        slow = "https://pmc.ncbi.nlm.nih.gov/x"        # 0.34s
        fast = "https://api.unpaywall.org/v2/x"        # 0.15s
        http._wait(slow)                                # prime the slow host

        started = time.monotonic()
        http._wait(fast)                                # must not wait on the slow host
        self.assertLess(time.monotonic() - started, 0.10)


class WorkerResolutionTest(unittest.TestCase):
    def test_offline_forces_serial(self):
        with TemporaryDirectory() as tmp:
            _, run = make_run(Path(tmp))
            args = _Args(offline=True, workers=8)
            self.assertEqual(fulltext.resolve_workers(args, run), 1)

    def test_explicit_flag_wins_over_config(self):
        with TemporaryDirectory() as tmp:
            _, run = make_run(Path(tmp))
            self.assertEqual(fulltext.resolve_workers(_Args(workers=3), run), 3)

    def test_falls_back_to_config_budget_then_default(self):
        with TemporaryDirectory() as tmp:
            _, run = make_run(Path(tmp))
            import json as _json
            cfg = _json.loads((run / "config.json").read_text())
            cfg["budgets"] = {"max_parallel": 6}
            (run / "config.json").write_text(_json.dumps(cfg))
            self.assertEqual(fulltext.resolve_workers(_Args(), run), 6)

            cfg.pop("budgets")
            (run / "config.json").write_text(_json.dumps(cfg))
            self.assertEqual(fulltext.resolve_workers(_Args(), run),
                             fulltext.DEFAULT_WORKERS)

    def test_clamped_to_sane_bounds(self):
        with TemporaryDirectory() as tmp:
            _, run = make_run(Path(tmp))
            self.assertEqual(fulltext.resolve_workers(_Args(workers=0), run), 1)
            self.assertEqual(fulltext.resolve_workers(_Args(workers=999), run),
                             fulltext.MAX_WORKERS)
            self.assertEqual(fulltext.resolve_workers(_Args(workers="nonsense"), run),
                             fulltext.DEFAULT_WORKERS)


class _Args:
    def __init__(self, *, offline: bool = False, workers=None):
        self.offline = offline
        self.workers = workers


if __name__ == "__main__":
    unittest.main()
