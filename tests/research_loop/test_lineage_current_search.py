"""Current sealed H/search + registered cov execution + A, without models."""
import json

import pytest

from scidiscovery.research_loop import contracts as c, history, lineage
from cov_execution import lineage as cov_lineage
from execution_fixtures import execution_registry
from test_lineage import scene, synthetic_host_launch
from test_nonlocal_execution_observation import research


@pytest.mark.process_e2e
@pytest.mark.parametrize('damage',['missing_protocol','different_owner'])
def test_context_requires_current_stable_stage_scope(tmp_path,execution_registry,damage):
    root,config,digest=scene(tmp_path)
    script=c.workspace(root,2)/'code/train.py';script.write_text('success')
    cov_lineage.run(config,script,new=True,mechanism=research(root,'1'))
    request=dict(root=str(root),step=2,config=c.settings(root)['history_observer']['config'],known_candidates=[],known_contexts=[])
    contexts=cov_lineage.observe(request)['contexts']
    wrong=json.loads(json.dumps(contexts))
    if damage=='missing_protocol':wrong[0]['scope'].pop('handoff_protocol')
    else:wrong[0]['scope']['execution_key']=c.record_ref(root/'jobs/001.json')
    def forbidden_archive(*args,**kwargs):raise AssertionError('Scope must be checked before asset archive')
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(history.ObserverIntegrityError,match='Execution context differs'):
        lineage.contexts(root,c.read(root/'jobs/002.json'),wrong,forbidden_archive)
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
