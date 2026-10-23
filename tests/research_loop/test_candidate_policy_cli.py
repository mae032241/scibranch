"""Selection configuration fails closed through the supported prepare entry point."""
import sys

import pytest

from scidiscovery.research_loop import cli, contracts as c
from test_control_selection import OBSERVER
from test_delivery import delivery_policy


@pytest.mark.parametrize('policy,accepted',[
    ({'version':2},True),
    ({'version':1,'shortlist_k':3,'smoke_seconds':20},False),
    ({'version':2,'shortlist_k':1},False),
    ({'version':2,'evaluation_key':'public-v1'},False),
    ({'version':2,'smoke_seconds':20},False),
])
def test_prepare_cli_accepts_only_explicit_comparison_policy(tmp_path,policy,accepted):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain candidate facts for explicit scientific choice.')
    adapter=tmp_path/'adapter.py';adapter.write_text('raise AssertionError("Prepare cannot execute domain code")\n')
    descriptor=dict(adapter=str(adapter),launcher_argv=[sys.executable,str(adapter)],config={},target={},inputs=[],source_roots=[str(tmp_path)],readonly_inputs_version=1)
    files=dict(node={role:dict(max_observed_tokens=2000,max_seconds=60) for role in 'HWAE'},
        delivery=delivery_policy(descriptor),observer=dict(adapter=str(OBSERVER),config=dict(runs='runs',evaluation_key='v1')),
        selection=policy)
    for name,value in files.items():c.write(tmp_path/(name+'.json'),value)
    root=tmp_path/'instance'
    args=['prepare','--root',str(root),'--objective',str(objective),'--public-root',str(public),
        '--native-launcher-json','["never"]','--node-budget-policy',str(tmp_path/'node.json'),
        '--delivery-policy',str(tmp_path/'delivery.json'),'--history-observer-config',str(tmp_path/'observer.json'),
        '--selection-policy',str(tmp_path/'selection.json'),'--max-tokens','100000','--max-seconds','600','--max-steps','12']
    if accepted:
        assert cli.main(args)==0
        assert c.settings(root)['selection_policy']==policy
    else:
        assert cli.main(args)==1
        assert not root.exists()
