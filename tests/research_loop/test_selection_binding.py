"""New selection layout is job-frozen; old science and guidance stay byte exact."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, controller, execution, guidance
from test_history_handoff import finish


def prepare(tmp_path, *, selection_protocol=1, advice=None):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Use only public comparisons and sealed scientific judgments.')
    adapter=tmp_path/'observer.py';adapter.write_text('''import json,sys
r=json.load(sys.stdin)
c=[dict(id='P',label='Observed candidate P',implementation=None,evaluation_key='public-val-v1',metric=.8,direction='max',status='ok',evidence=['project/global/objective.md'])] if r['step']==1 else []
print(json.dumps(dict(candidates=c,current_candidate=None,actual_submission=None,coverage=dict(status='complete',note='Public fixture boundary'))))
''')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fake'],max_tokens=100000,max_seconds=3600,max_steps=8,
        model='fake',reasoning_effort='high',search_policy=None,selection_protocol=selection_protocol,
        role_guidance=guidance.DEFAULT if advice is None else advice,history_observer=dict(adapter=str(adapter),config={}))
    return root




def test_current_selection_literal_stays_frozen_when_future_default_changes(tmp_path,monkeypatch):
    old_advice={**guidance.DEFAULT,'A':'Previously frozen scientific advice, unchanged by new rules.'}
    root=prepare(tmp_path,selection_protocol=None,advice=old_advice)
    finish(root,1,'H',[]);finish(root,2,'W',[1]);job,work,record=finish(root,3,'A',[2],recommendation='P')
    # A null compatibility argument still freezes the current settings/job marker.
    assert c.settings(root)['selection_protocol']==job['selection_protocol']==1
    assert job['role_guidance']==old_advice['A']
    originals={p:p.read_bytes() for p in (root/'settings.json',root/'jobs/003.json',work/'science-task.json',record/'science-output.json',record/'result-identity.json')}
    monkeypatch.setitem(guidance.DEFAULT,'A','Changed future default')
    assert c.build_job(root,3,'A',[2])==job
    c.validate(work)
    assert c.science_view(job,root)['lineage']['candidates']['selection_protocol']==1
    assert all(p.read_bytes()==data for p,data in originals.items())




@pytest.mark.parametrize('bad',[True,0,2,'1'])
def test_invalid_selection_marker_cannot_prepare(tmp_path,bad):
    with pytest.raises(ValueError):prepare(tmp_path,selection_protocol=bad)
    assert not (tmp_path/'instance').exists()
