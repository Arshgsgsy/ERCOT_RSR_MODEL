"""Tests for run_all.py helpers."""

from run_all import decision_ids, git_state


def test_decision_ids_collects_every_decision_key():
    cfg = {"a": {"decision": "D4"}, "b": {"x": {"decision": "D16, D17 (departure), D31"}}, "decision": "D2"}
    assert decision_ids(cfg) == ["D2", "D4", "D16", "D17", "D31"]


def test_git_state_keeps_full_file_names():
    state = git_state()
    assert "commit" in state and len(state["commit"]) == 40
    assert all(not f.startswith(" ") and len(f) > 1 for f in state["dirty_files"])
