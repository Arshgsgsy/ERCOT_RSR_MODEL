"""Public evaluation keeps the original diagnostic and handles optional inputs."""
import numpy as np
import pandas as pd
import pytest

import evaluation
from tests.test_models import fake_daily


def test_oos_r2_refits_without_heldout_outcomes():
    X = np.ones((4, 1))
    y = np.array([0., 2., 4., 10.])
    groups = np.array([0, 0, 1, 1])
    predictions = np.array([7., 7., 1., 1.])
    expected = 1 - np.sum((y - predictions) ** 2) / np.sum((y - y.mean()) ** 2)
    assert evaluation.oos_r2(X, y, groups, np.ones(4, bool)) == pytest.approx(expected)


def test_evaluation_skips_unavailable_optional_models(tmp_path, monkeypatch):
    daily = fake_daily(n=90)
    monkeypatch.setattr(evaluation, 'load_daily', lambda: daily)
    monkeypatch.setattr(evaluation, 'MODELS', {'vol_zone': {}, 'vol_zone_fe': {}})
    monkeypatch.setattr(evaluation, 'zones_for', lambda model: ['LZ_NORTH'])
    output = evaluation.write_evaluation(tmp_path)
    rows = pd.read_csv(output)
    assert set(rows.model) == {'vol_zone'}
    assert len(rows) == 12
    assert set(rows.folds) == {'day', 'week', 'month'}
    assert set(rows.term_set) == {'full', 'chosen', 'linear', 'block_only'}
