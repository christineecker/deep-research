from __future__ import annotations

import unittest
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, make_run


taskboard = load_script("taskboard.py")


def _ns(run_dir: Path, task_id: str, **kw) -> Namespace:
    defaults = dict(run_dir=str(run_dir), task_id=task_id, inputs=None, inputs_file=None,
                     output_path=None, worker=None, error=None, summary=None)
    defaults.update(kw)
    return Namespace(**defaults)


class TaskIdValidationTest(unittest.TestCase):
    def test_valid_task_id_parses_stage_kind_key(self):
        stage, kind, key = taskboard.validate_task_id("search:query:q1")
        self.assertEqual((stage, kind, key), ("search", "query", "q1"))

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(taskboard.UserError):
            taskboard.validate_task_id("bogus:query:q1")

    def test_unknown_key_kind_is_rejected(self):
        with self.assertRaises(taskboard.UserError):
            taskboard.validate_task_id("search:nope:q1")

    def test_malformed_grammar_is_rejected(self):
        with self.assertRaises(taskboard.UserError):
            taskboard.validate_task_id("search:query")  # missing key segment

    def test_empty_task_id_is_rejected(self):
        with self.assertRaises(taskboard.UserError):
            taskboard.validate_task_id("")


class TransitionGraphTest(unittest.TestCase):
    def test_new_task_may_only_start_pending(self):
        taskboard.check_transition(None, "pending", inputs_hash=None)
        with self.assertRaises(taskboard.StateError):
            taskboard.check_transition(None, "active", inputs_hash=None)

    def test_pending_to_active_allowed(self):
        prev = {"task_id": "search:query:q1", "status": "pending", "inputs_hash": "h1"}
        taskboard.check_transition(prev, "active", inputs_hash="h1")

    def test_pending_to_completed_directly_is_rejected(self):
        prev = {"task_id": "search:query:q1", "status": "pending", "inputs_hash": "h1"}
        with self.assertRaises(taskboard.StateError):
            taskboard.check_transition(prev, "completed", inputs_hash="h1")

    def test_active_can_reach_completed_failed_blocked_cancelled(self):
        prev = {"task_id": "search:query:q1", "status": "active", "inputs_hash": "h1"}
        for status in ("completed", "failed", "blocked", "cancelled"):
            taskboard.check_transition(prev, status, inputs_hash="h1")

    def test_completed_is_terminal_unless_inputs_hash_changes(self):
        prev = {"task_id": "search:query:q1", "status": "completed", "inputs_hash": "h1"}
        with self.assertRaises(taskboard.StateError):
            taskboard.check_transition(prev, "pending", inputs_hash="h1")
        with self.assertRaises(taskboard.StateError):
            taskboard.check_transition(prev, "pending", inputs_hash=None)
        # a genuinely different inputs_hash reopens it (any new_status accepted here since
        # check_transition returns early once it detects a change).
        taskboard.check_transition(prev, "pending", inputs_hash="h2")

    def test_blocked_is_reopenable_but_not_terminally_closeable_directly(self):
        prev = {"task_id": "search:query:q1", "status": "blocked", "inputs_hash": "h1"}
        taskboard.check_transition(prev, "pending", inputs_hash="h1")
        taskboard.check_transition(prev, "active", inputs_hash="h1")
        with self.assertRaises(taskboard.StateError):
            taskboard.check_transition(prev, "completed", inputs_hash="h1")

    def test_cancelled_can_only_be_reopened_to_pending(self):
        prev = {"task_id": "search:query:q1", "status": "cancelled", "inputs_hash": "h1"}
        taskboard.check_transition(prev, "pending", inputs_hash="h1")
        with self.assertRaises(taskboard.StateError):
            taskboard.check_transition(prev, "active", inputs_hash="h1")

    def test_failed_can_retry_or_cancel(self):
        prev = {"task_id": "search:query:q1", "status": "failed", "inputs_hash": "h1"}
        for status in ("pending", "active", "cancelled"):
            taskboard.check_transition(prev, status, inputs_hash="h1")
        with self.assertRaises(taskboard.StateError):
            taskboard.check_transition(prev, "blocked", inputs_hash="h1")


class InputsHashDeterminismTest(unittest.TestCase):
    def test_sha256_of_is_deterministic_and_order_independent(self):
        a = taskboard.sha256_of({"a": 1, "b": 2})
        b = taskboard.sha256_of({"b": 2, "a": 1})
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("sha256:"))

    def test_sha256_of_differs_for_different_payloads(self):
        a = taskboard.sha256_of({"a": 1})
        b = taskboard.sha256_of({"a": 2})
        self.assertNotEqual(a, b)

    def test_compute_inputs_hash_none_when_no_inputs_supplied(self):
        args = Namespace(inputs=None, inputs_file=None)
        self.assertIsNone(taskboard.compute_inputs_hash(args, prev=None))

    def test_compute_inputs_hash_matches_sha256_of_for_inline_json(self):
        args = Namespace(inputs='{"x": 1}', inputs_file=None)
        self.assertEqual(taskboard.compute_inputs_hash(args, prev=None),
                         taskboard.sha256_of({"x": 1}))

    def test_compute_inputs_hash_is_deterministic_across_calls(self):
        args = Namespace(inputs='{"x": 1, "y": 2}', inputs_file=None)
        h1 = taskboard.compute_inputs_hash(args, prev=None)
        h2 = taskboard.compute_inputs_hash(args, prev=None)
        self.assertEqual(h1, h2)


class TaskBoardStateTest(unittest.TestCase):
    def test_last_record_per_task_id_wins_and_history_keeps_all(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            board = taskboard.TaskBoard(run)
            board.write({"task_id": "search:query:q1", "status": "pending"})
            board.write({"task_id": "search:query:q1", "status": "active"})
            board.write({"task_id": "search:query:q2", "status": "pending"})

            state = board.state()
            self.assertEqual(state["search:query:q1"]["status"], "active")
            self.assertEqual(state["search:query:q2"]["status"], "pending")
            self.assertEqual(len(board.history()), 3)
            self.assertEqual(board.get("search:query:q1")["status"], "active")
            self.assertIsNone(board.get("search:query:missing"))

    def test_record_without_task_id_is_skipped_not_raised(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            board = taskboard.TaskBoard(run)
            board.path.parent.mkdir(parents=True, exist_ok=True)
            board.path.write_text('{"status": "pending"}\n', encoding="utf-8")
            self.assertEqual(board.state(), {})


class TaskCliTransitionTest(unittest.TestCase):
    """End-to-end through the `task` CLI command functions themselves."""

    def test_create_claim_complete_then_completed_is_immutable(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            task_id = "search:query:q1"

            created = taskboard.cmd_task_create(_ns(run, task_id))
            self.assertEqual(created, 0)

            claimed = taskboard.cmd_task_claim(_ns(run, task_id, worker="w1"))
            self.assertEqual(claimed, 0)
            board = taskboard.TaskBoard(run)
            self.assertEqual(board.get(task_id)["status"], "active")
            self.assertEqual(board.get(task_id)["attempts"], 1)

            completed = taskboard.cmd_task_complete(
                _ns(run, task_id, worker="w1", summary="done"))
            self.assertEqual(completed, 0)
            self.assertEqual(board.get(task_id)["status"], "completed")

            with self.assertRaises(taskboard.StateError):
                taskboard.cmd_task_claim(_ns(run, task_id, worker="w2"))

    def test_fail_then_reopen_to_pending(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            task_id = "extract:pmid:1"
            taskboard.cmd_task_create(_ns(run, task_id))
            taskboard.cmd_task_claim(_ns(run, task_id, worker="w1"))
            taskboard.cmd_task_fail(_ns(run, task_id, worker="w1", error="boom"))
            board = taskboard.TaskBoard(run)
            self.assertEqual(board.get(task_id)["status"], "failed")
            self.assertEqual(board.get(task_id)["error"], "boom")

            taskboard.cmd_task_reopen(_ns(run, task_id))
            self.assertEqual(board.get(task_id)["status"], "pending")

    def test_claim_with_no_prior_task_auto_creates_it(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            task_id = "screen:pmid:2"
            taskboard.cmd_task_claim(_ns(run, task_id, worker="w1"))
            board = taskboard.TaskBoard(run)
            rec = board.get(task_id)
            self.assertEqual(rec["status"], "active")
            self.assertEqual(rec["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
