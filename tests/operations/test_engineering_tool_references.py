"""Engineering readers consume explicit control paths without source fingerprints."""
import json
import runpy
from pathlib import Path


def test_behavior_card_reads_review_from_exact_step_namespace_without_job(tmp_path):
    script=Path(__file__).resolve().parents[2]/'scripts/acceptance/behavior_card.py'
    values=runpy.run_path(str(script))
    review=tmp_path/'host-runtime/experiment-control/007/review.json'
    review.parent.mkdir(parents=True)
    review.write_text(json.dumps(dict(reason='Retained independent review.')))
    outcome=tmp_path/'native-evidence/007/result-identity.json'
    outcome.parent.mkdir(parents=True)
    outcome.write_text(json.dumps(dict(control_outcome=dict(status='refused'))))
    assert not (tmp_path/'jobs/007.json').exists()
    assert values['review_reason'](tmp_path,7)==('Retained independent review.',dict(status='refused'))
