"""Tests for run_all.py helpers."""

from types import SimpleNamespace

import run_all
from run_all import decision_ids, git_state, source_files


def test_decision_ids_collects_every_decision_key():
    cfg = {"a": {"decision": "D4"}, "b": {"x": {"decision": "D16, D17 (departure), D31"}}, "decision": "D2"}
    assert decision_ids(cfg) == ["D2", "D4", "D16", "D17", "D31"]


def test_git_state_keeps_full_file_names(monkeypatch):
    responses = iter(['a' * 40 + '\n', 'main\n', ' M build.py\n?? evaluation.py\n'])
    monkeypatch.setattr(run_all.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=next(responses)))
    state = git_state()
    assert "commit" in state and len(state["commit"]) == 40
    assert state['dirty_files'] == ['build.py', 'evaluation.py']


def test_source_snapshot_excludes_local_work_and_includes_reference(tmp_path, monkeypatch):
    for name in ('run_all.py', 'evaluation.py', 'tests/test_build.py', '.env.example',
                 'reference/inference_validation.json', 'docs/methodology.md',
                 'scratch.py', 'local_work/package_report.py', 'notebooks/scratch.py',
                 'docs/editorial_notes.md', 'outputs/run/source_snapshot/scratch.py', '.env'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(run_all, 'ROOT', tmp_path)
    assert {p.relative_to(tmp_path).as_posix() for p in source_files()} == {
        'run_all.py', 'evaluation.py', 'tests/test_build.py', '.env.example',
        'reference/inference_validation.json', 'docs/methodology.md',
    }
