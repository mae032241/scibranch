"""Optional nomination advice keeps originals, warnings and the full candidate pool."""
import copy
import json

import pytest

from scidiscovery.research_loop import (accepted_budget, candidate_nominations as nominations,
    contracts as c, history, lineage, ui_components, ui_view)
from execution_fixtures import execution_registry
from test_breadth_science import findings, h, prepare, worker as current_worker
from test_history_handoff import finish


@pytest.fixture
def retained_pool(tmp_path, execution_registry):
    root=prepare(tmp_path)
    h(root,labels=('P','Q'))
    _,config=current_worker(root,tmp_path,2,[1])
    work=c.workspace(root,2)
    directory=work/'experiments/models'
    directory.mkdir(parents=True)
    artifacts=[f'artifacts/model-{index}.bin' for index in range(25)]
    result=dict(version=1,metrics=[],outputs=[dict(path=path) for path in artifacts],
        candidates=[dict(artifact=path,label=f'Retained model {index}') for index,path in enumerate(artifacts)])
    script=work/'code/run.py'
    script.parent.mkdir(exist_ok=True)
    script.write_text('from pathlib import Path\n'
        'for path in '+repr(artifacts)+': Path(path).write_bytes(b"retained fixture model")\n'
        'Path("artifacts/result.json").write_text('+repr(json.dumps(result))+')\n')
    from breadth_execution_fixtures import run as qualified_run
    reply=qualified_run(root,config,script,'h001.m1',directory)
    assert reply['status']=='known_success'
    return root,reply['call_id'],artifacts


def seal_worker(root,value):
    return finish(root,2,'W',[1],candidate_nominations=value,
        mechanism_findings=findings('h001.m1',blocked=('h001.m2',)),preserve_native_intent=True)


def next_views(root):
    job=c.build_job(root,3,'A',[2])
    public=c.science_view(job,root)['lineage']['candidates']['nominations']
    projection=ui_view.candidate_projection(root,3,job=job,history_only=True)
    return public,projection
