"""A continuing shallow task keeps its original soft target advisory."""
import sys

import pytest

from scidiscovery.research_loop import (contracts as c, controller, budget, native,
    continuation, checkpoint, execution, runtime, search, experiments, worker_delivery)
from execution_fixtures import execution_registry
from test_breadth_science import h, run, findings
from test_worker_delivery import output, stopped_native


def continuing_stage(tmp_path, *, max_tokens=100000):
    public=tmp_path/'public';public.mkdir()
    objective=public/'goal.md';objective.write_text('Compare the original mechanisms within finite investment.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable],model='no-model',reasoning_effort='high',
        max_tokens=max_tokens,max_seconds=600,max_steps=16,search_policy=search.DEFAULT_POLICY,node_budget_mode='soft',
        node_budget_policy={role:dict(max_observed_tokens=10 if role=='W' else 100,max_seconds=60)
                            for role in ('H','W','A','E')},
        resume_policy=dict(max_attempts=1,protocol='codex_exec_resume',usage_scope='turn',output_repair=False),
        worker_fragment_policy=dict(context='new_context'))
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','Known zero-model host start for original soft-target probe')
    h(root,labels=('P','Q'))
    job=c.build_job(root,2,'W',[1])
    anchor=budget.accept_node(root,job)
    record=execution.record(root,2)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json'),budget_slice=anchor))
    from scidiscovery.research_loop import accepted_budget
    accepted_budget.accept_native(root,2,record,c.read(record/'native-intent.json'))
    config=experiments.configure(root,2,'local_execution:command',dict(run_argv=[sys.executable]),c.workspace(root,2)/'work/experiment-config.json')
    run(root,config,'h001.m1')
    record,_=output(root,findings('h001.m1'),intent='continue');stopped_native(root,record)
    meter=c.read(record/'usage.json');outcome=c.read(record/'native-outcome.json')
    outcome['budget_slice']=dict(role='W',limits=budget.node_limits(root,job),usage_observed=True,
        dispatches=1,advance_seconds=0.,observed_tokens=meter['input_tokens']+meter['output_tokens'],**meter)
    c.write(record/'native-outcome.json',outcome)
    checkpoint.capture(root,job,record)
    return root,job,config,record
