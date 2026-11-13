"""Author closeout leaves task-specific scientific judgments to the Worker."""
import json
import os
from pathlib import Path
import re
import threading

import pytest

from scidiscovery.research_loop import author_closeout, budget, contracts as c, controller, execution, native, runtime


@pytest.mark.parametrize('attempt,role,review,objective,evidence', [
    *[pytest.param(0,role,False,'Compare retained scientific observations.','Retained observations.',
        id='ordinary-'+role) for role in 'HWAE'],
    pytest.param(0,'A',True,'Independently review the retained scientific implementation.',
        'Retained scientific implementation.',id='independent-formal-review'),
    pytest.param(1,'W',False,
        'Compare ionic conductivity measurements; no audit or delivery is required.',
        'The retained conductivity measurements include uncertainty intervals.',
        id='first-author-no-audit-or-delivery'),
    pytest.param(2,'W',False,
        'Estimate the orbital period from retained observations. Independent audit and report delivery are complete.',
        'The observations, completed independent audit and delivered report are retained.',
        id='successor-author-completed-audit-and-delivery'),
])
def test_author_closeout_prompt_defers_requirement_judgment_to_worker(tmp_path, monkeypatch, attempt, role, review, objective, evidence):
    public = tmp_path / 'public'
    public.mkdir()
    goal = public / 'objective.md'
    goal.write_text(objective)
    root = tmp_path / 'instance'
    nodes = {role: dict(max_observed_tokens=10000, max_seconds=120) for role in 'HWAE'}
    controller.prepare(root, goal, public, ['captured-native'],
        max_tokens=100000, max_seconds=600, max_steps=4,
        model='fixture-no-model', reasoning_effort='high', search_policy=None,
        node_budget_policy=nodes, node_budget_mode='soft')
    job=c.build_job(root, 1, role, [])
    if review:
        # Only capture the real review prompt branch; qualification and author
        # independence are verified by the separate formal-review tests.
        from scidiscovery.research_loop import formal_review
        job['formal_review']=dict(fixture='prompt capture only; no scientific verdict')
        c.write(root/'jobs/001.json',job)
        monkeypatch.setattr(formal_review,'selected_compiler',lambda *args,**kwargs:None)
        monkeypatch.setattr(formal_review,'input_binding',lambda *args,**kwargs:dict(materials=[]))
        monkeypatch.setattr(formal_review,'public',lambda *args:dict(review=dict(
            instruction='Independently assess the exact scientific subject.')))
    work = c.workspace(root, 1)
    (work / 'artifacts/retained-evidence.txt').write_text(evidence)
    task = c.read(work / 'science-task.json')
    # The objective is a framed original referenced by the task, not copied into the workspace.
    objective_row = next(row for row in c.read(root / 'jobs/001.json')['global_materials']
        if row['path'] == task['global']['objective'])
    assert Path(objective_row['source']).read_text() == objective
    assert 'candidate_audit_policy' not in c.settings(root)
    assert 'delivery_policy' not in c.settings(root)

    # Capture prompts after the eligibility boundary; authorization/chain
    # validation has its own tests. Keep the real task, profile, budgets,
    # prompt assembly and stdin path without constructing a synthetic grant.
    origin = execution.origin(root, 1)
    chain = [origin, *[origin / 'continuations' / f'{number:03d}' for number in range(1, attempt + 1)]]
    anchor = dict(task_ref=c.record_ref(root / 'jobs/001.json'), role=role,
        boot_id=budget.clock_stamp()['boot_id'], advance_seconds=0., limits=nodes[role],
        shared_limits=dict(max_tokens=100000, max_seconds=600),
        previous_tokens=0, previous_seconds=0.)
    meter = dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    for number, record in enumerate(chain):
        intent = dict(task_ref=anchor['task_ref'], budget_slice=anchor)
        if number:
            intent.update(attempt=number, continuation_kind='author_closeout', context_mode='new_context',
                root_intent_ref=c.record_ref(origin / 'native-intent.json'), previous=execution.proof(chain[number - 1]))
        c.write(record / 'native-intent.json', intent)
        c.write(record / 'runner-process.json', dict(fixture='no runner process'))
        if number < attempt:
            c.write(record / 'usage.json', meter)
            c.write(record / 'native-outcome.json', dict(budget_slice=dict(role=role,
                advance_seconds=0., **meter, observed_tokens=0, dispatches=number + 1,
                limits=nodes[role], usage_observed=True)))
    monkeypatch.setattr(execution, 'records', lambda root, step: chain if step == 1 else [execution.origin(root, step)])
    monkeypatch.setattr(author_closeout, 'load_intent_eligibility',
        lambda *args, **kwargs: (dict(max_tokens=1000, max_seconds=60), None))

    prompts = []

    readers = []

    def capture_stdin():
        # The launcher writes through a real nonblocking pipe; collect the whole request.
        read_fd, write_fd = os.pipe()

        def drain():
            with os.fdopen(read_fd, 'rb') as stream:
                prompts.append(stream.read().decode())

        reader = threading.Thread(target=drain)
        reader.start()
        readers.append(reader)
        return os.fdopen(write_fd, 'wb')

    real_popen = native.subprocess.Popen

    class CaptureProcess:
        pid = 999999999
        returncode = 0

        def __new__(cls, args, **kwargs):
            # The shared subprocess module also serves the real readonly-mount capability probe.
            if '--output-last-message' not in args:
                return real_popen(args, **kwargs)
            return super().__new__(cls)

        def __init__(self, args, **kwargs):
            self.stdin = capture_stdin()
            read_fd, write_fd = os.pipe()
            self.stdout = os.fdopen(read_fd, 'rb')
            try:
                os.write(write_fd, (json.dumps(dict(type='turn.completed',
                    usage=dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))) + '\n').encode())
            finally:
                os.close(write_fd)
            # Native completion is transport only; this test claims no science
            # verdict and does not seal or route a synthetic response.
            c.write(Path(args[args.index('--output-last-message') + 1]), dict(fixture='captured prompt'))

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(native.subprocess, 'Popen', CaptureProcess)
    monkeypatch.setattr(runtime, 'process_identity', lambda pid, binding: dict(task_ref=binding))
    monkeypatch.setattr(runtime, 'process_matches', lambda *args: False)
    monkeypatch.setattr(runtime, 'cleanup_owned_group', lambda *args: 'observed_group_cleaned')
    outcome = native.run_native(root, 1, attempt)
    for reader in readers:
        reader.join(10)

    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert len(prompts) == 1
    prompt = prompts[0]
    assert not re.search(r'twenty[- ]five|\b25\b|last[- ]eight|audited-score plateau', prompt, re.IGNORECASE)
    assert 'auditing and delivery remain pending' not in prompt.lower()
    assert 'audit and delivery remain pending' not in prompt.lower()
    assert 'rather than asserting task completion' not in prompt
    if attempt:
        assert 'no experiment/training/refinement/submission/replay/backend query/collection' in prompt
        assert 'read-only' in prompt
        assert 'stage_intent=decision; no consultation, handoff or continued work' in prompt
        assert '1000' in prompt and '60' in prompt
    if review:assert 'Do not edit the subject' in prompt
    assert (work / 'artifacts/retained-evidence.txt').read_text() == evidence
