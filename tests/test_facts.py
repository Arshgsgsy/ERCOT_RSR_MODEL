"""Historical facts are explicit inputs, independent of local experiment state."""
import json

import pytest

import facts


def test_reference_facts_do_not_read_generated_experiments(tmp_path, monkeypatch):
    historical = tmp_path / 'hac_size'
    historical.mkdir()
    (historical / 'size.csv').write_text('deliberately invalid historical data')
    monkeypatch.setitem(facts.CONFIG['paths'], 'outputs', tmp_path)
    result = facts.Facts()
    facts.add_validation_reference_facts(result)
    reference = json.loads(facts.INFERENCE_REFERENCE.read_text())
    assert [(r['id'], r['value']) for r in result.rows] == [
        (r['id'], r['value']) for r in reference['facts']]
    assert all(r['file'] == 'reference/inference_validation.json' for r in result.rows)


def test_missing_reference_fails_instead_of_silently_dropping_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(facts, 'INFERENCE_REFERENCE', tmp_path / 'missing.json')
    with pytest.raises(FileNotFoundError):
        facts.add_validation_reference_facts(facts.Facts())
