"""Repair retains the exact old observation and binds current H authority."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, execution, experiments, history, search, semantic
from execution_fixtures import execution_registry
from test_breadth_science import a, findings, h, prepare, run, worker
from test_history_handoff import finish

pytestmark = pytest.mark.process_e2e


def revision(*, label='P', minimal_test='A corrected discriminating prediction', scope='Revised scientific scope'):
    return dict(scope=scope, commitments=[dict(label=label, minimal_test=minimal_test, existing_ref='h001.m1')],
                focus=dict(index=1, action='repair'))


def original(tmp_path, *, cov=False, failed=True):
    root=prepare(tmp_path,cov=cov)
    h(root,labels=('P','Q','R','S'))
    _,config=worker(root,tmp_path,2,[1],cov=cov)
    observed=run(root,config,'h001.m1',failed=failed)
    for ref in ('h001.m2','h001.m3','h001.m4'):run(root,config,ref)
    finish(root,2,'W',[1],mechanism_findings=findings('h001.m1','h001.m2','h001.m3','h001.m4'))
    return root,config,observed


def revise(root, *, choice=None, parents=(1,2,3), selection=None):
    a(root,3,[1,2],action='revise_h',dispositions=[dict(mechanism_ref='h001.m2',status='screened',
        reason='The exact original W supplies this unchanged mechanism observation')])
    return finish(root,4,'H',list(parents),action='experiment',search_choice=choice or revision(),selection=selection)


def receipt(root,step):
    return c.read(execution.record(root,step)/'result-identity.json')['control_outcome']
