"""Author closeout leaves task-specific scientific judgments to the Worker."""
import io
import json
import os
from pathlib import Path
import re

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
        monkeypatch.setattr(formal_review,'public',lambda *args:dict(review=dict(
            instruction='Independently assess the exact scientific subject.')))
    work = c.workspace(root, 1)
    (work / 'artifacts/retained-evidence.txt').write_text(evidence)
    task = c.read(work / 'science-task.json')
    assert (work / task['global']['objective']).read_text() == objective
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
    monkeypatch.setattr(author_closeout, 'require_intent_eligibility', lambda *args: dict(max_tokens=1000, max_seconds=60))

    prompts = []

    class CaptureInput(io.BytesIO):
        def write(self, data):
            prompts.append(data.decode())
            return super().write(data)

    class CaptureProcess:
        pid = 999999999
        returncode = 0

        def __init__(self, args, **kwargs):
            self.stdin = CaptureInput()
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

    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert len(prompts) == 1
    prompt = prompts[0]
    assert not re.search(r'twenty[- ]five|\b25\b|last[- ]eight|audited-score plateau', prompt, re.IGNORECASE)
    assert 'auditing and delivery remain pending' not in prompt.lower()
    assert 'audit and delivery remain pending' not in prompt.lower()
    assert 'rather than asserting task completion' not in prompt
    assert 'Do not independently compute, compare, or revalidate file/evidence hashes or checksums' in prompt
    assert 'generate hash inventories, or perform redundant mechanical integrity checks' in prompt
    assert 'Control automatically owns integrity verification' in prompt
    assert 'do not run your own checksum verification or alter bindings' in prompt
    assert 'not hash algorithms required by the scientific task itself' in prompt
    assert 'Any helper receiving this assignment must follow the same restrictions' in prompt
    if attempt:
        assert 'No new experiment, training,' in prompt
        assert 'read-only' in prompt
        assert 'Choose stage_intent=review or decision, never continue.' in prompt
        assert '1000' in prompt and '60' in prompt
    if review:assert 'Do not edit the subject' in prompt
    assert (work / 'artifacts/retained-evidence.txt').read_text() == evidence


@pytest.mark.process_e2e
@pytest.mark.parametrize('attempt', [1, 2])
def test_author_science_context_reads_framed_parent_originals(tmp_path, monkeypatch, attempt):
    import test_default_cli_output_repair as fixture

    # The real CLI seals H and binds its exact original into a normal W job.
    # Only the native wire is simulated; context and input validation stay real.
    sink = "pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))"
    framed_sink = ("pathlib.Path(args[args.index('--output-last-message')+1]).write_text("
        "'\\ufeff ```json\\n'+json.dumps(payload,ensure_ascii=False)+'\\n``` \\n')")
    assert fixture.NATIVE.count(sink) == 1
    program = fixture.NATIVE.replace("next_action='analyze'", "next_action='experiment'")
    contexts = []
    for framed in (False, True):
        case = tmp_path / ('framed' if framed else 'clean')
        case.mkdir()
        monkeypatch.setattr(fixture, 'NATIVE', program.replace(sink, framed_sink) if framed else program)
        root, _, env = fixture.prepare_cli(case, fault='valid')
        result = fixture.cli(root, 'run', env=env)
        assert result['status'] == 'finish', result
        job = c.read(root / 'jobs/002.json')
        assert job['role'] == 'W'
        parent = execution.origin(root, 1)
        raw = parent / 'science-output.json'
        assert any(row['path'] == 'inputs/parents/001/conclusion.json'
            and row['size_bytes'] == raw.stat().st_size for row in job['direct_originals'])
        if framed:
            with pytest.raises(json.JSONDecodeError):
                c.read(raw)  # The control-record reader remains strict.
        paths = [raw, parent / 'result.json', parent / 'result-identity.json',
            parent / 'native-intent.json', parent / 'native-outcome.json',
            root / 'jobs/001.json', root / 'jobs/002.json',
            c.workspace(root, 2) / 'science-task.json']
        originals = {path: path.read_bytes() for path in paths}
        reference = c.file_ref(raw)
        record = root / f'author-context-fixture-{attempt:03d}'
        record.mkdir()
        proof = author_closeout.science_context(root, job, record, attempt=attempt)
        directory = author_closeout.validate_science_context(root, job, proof)
        manifest = c.read(root / proof['path'])
        conclusion = c.read(directory / 'inputs/parents/001/conclusion.json')
        assert conclusion['summary'] == 'Deterministic CLI transport'
        assert conclusion['scientific_result'] == c.read_scientific_output(raw)['scientific_result']
        contexts.append((conclusion, manifest['candidate_references'],
            [(row['kind'], Path(row['display']['path']).relative_to(directory.relative_to(root)).as_posix())
                for row in manifest['entries']]))
        assert c.validate(c.workspace(root, 1))['summary'] == conclusion['summary']
        assert c.file_ref(raw) == reference
        assert all(path.read_bytes() == value for path, value in originals.items())
    assert contexts[0] == contexts[1]
