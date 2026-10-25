"""Public compile/seal/review/run contracts with injected local transport.

Native session files and scheduling eligibility are synthetic trusted fixture
inputs. This is not real platform independence, backend or solver qualification.
"""
import json
from pathlib import Path
import uuid

import pytest

from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import (budget, contracts as c, controller, execution_inputs as inputs,
    execution_authority as authority, experiments as e, formal_review as review, native)
from tcad_artifact import managed_execution as managed
from tcad_artifact.execution_policy import execution_admission
from tcad_artifact.project_packager import (materialize_execution_inputs, package_execution_package_json,
    validate_execution_package_json)
from tests.operations.tcad_policy_fixtures import policy_snapshot
from test_history_handoff import finish


class InputBackend:
    def __init__(self):
        self.prepared = []; self.submits = 0; self.policy_reads = 0
        self.state = 'queued'; self.submissions = {}

    def execution_admission(self, raw, *, preparation_profile):
        self.policy_reads += 1
        return execution_admission(policy_snapshot(), validate_execution_package_json(
            raw, preparation_profile=preparation_profile))

    def prepare(self, payload, *, preparation_profile, exchange_directory, input_metadata, open_input):
        raw = Path(payload.local_path).read_bytes()
        package = validate_execution_package_json(raw, preparation_profile=preparation_profile)
        resolved = materialize_execution_inputs(package, None, exchange_directory, 2,
            input_metadata=input_metadata, open_input=open_input)
        self.prepared.append({name: path.read_bytes() for name, path in resolved.items()})
        packed = package_execution_package_json(raw, preparation_profile=preparation_profile,
            input_payloads=resolved, output_root=exchange_directory / 'physical')
        self.output_root = exchange_directory / 'backend-output'
        return LocalFileDescriptor.model_validate(packed.job_spec_file.model_dump(mode='json'), strict=True)

    def lookup_submission(self, prepared):
        external = self.submissions.get(prepared.local_path)
        return (external, self.state) if external is not None else None

    def submit(self, prepared, *, authorization):
        self.submits += 1
        external = 'native-remote-id' if self.submits == 1 else f'native-remote-id-{self.submits}'
        self.submissions[prepared.local_path] = external
        return external, self.state

    def status_details(self, external):
        return dict(state=self.state, run_id=external)

    def collect_with_budget(self, external, *, context):
        assert external in self.submissions.values()
        context.remaining_seconds()
        self.output_root.mkdir(parents=True, exist_ok=True)
        manifest = dict(started_at=None, completed_at='2026-10-17T00:00:00Z',
            terminal_state=self.state, exit_code=0, error='', outputs=[])
        result = []
        for name, raw, media in [('tcad_log', b'synthetic input-chain terminal\n', 'text/plain'),
                ('tcad_manifest', e.encoded(manifest), 'application/json')]:
            path = self.output_root / name; path.write_bytes(raw)
            result.append(LocalFileDescriptor(name=name, local_path=str(path),
                 size_bytes=len(raw), media_type=media))
        return tuple(result)


@pytest.fixture
def chain(tmp_path, monkeypatch, request):
    old = json.loads((Path(__file__).parents[1] / 'fixtures/tcad_analysis_package.json').read_bytes())
    snapshot = tmp_path / 'operator-capability.json'; c.write(snapshot, old['capability'])
    launcher = tmp_path / 'codex'; launcher.write_text('# synthetic identity files only; never executed\n'); launcher.chmod(0o700)
    public = tmp_path / 'public'; public.mkdir(); objective = public / 'objective.md'; objective.write_text('Use one exact supplied mesh.')
    backend = InputBackend()
    monkeypatch.setattr(e, 'registration', lambda name: (managed.REGISTRATION, 'tcad'))
    monkeypatch.setattr(managed, 'build_adapter', lambda *args, **kwargs: backend)
    root = tmp_path / 'instance'
    options = dict(transport='socket', socket_path=str(tmp_path / 'unused.sock'),
        implementation_compilation=dict(capability_snapshot_path=str(snapshot)))
    plugin_config = tmp_path / 'tcad-plugin.json'; c.write(plugin_config, options)
    controller.prepare(root, objective, public, [str(launcher)], max_tokens=4000, max_seconds=1000,
        max_steps=12, model='fixture', reasoning_effort='high', search_policy=None, experiment_mode='local',
        node_budget_mode='soft', execution_config=dict(executor='tcad_execution:tcad', config_file=str(plugin_config)),
        formal_review_policy={'version': 1}, execution_authority_policy={'version': 1},
        execution_resource_policy=dict(version=1, allowance=dict(wall_time_seconds=100, max_storage_bytes=1000000)),
        source_restrictions=[] if getattr(request, 'param', None) == 'unknown_source' else [
            dict(source=str(objective), restriction='clear')])
    # Only the native scheduling/identity boundary is synthetic. Source binding,
    # compiler proofs, formal qualification rules, gate and resource ledger run
    # through their actual production functions if this test is later authorized.
    monkeypatch.setattr(authority, '_remaining', lambda *args, **kwargs: (budget.clock_stamp(), 100.))
    original_seal = c.seal_result

    def seal(root, step):
        work = c.workspace(root, step); record = c.host_record(work)
        thread = str(uuid.uuid5(uuid.NAMESPACE_URL, 'native-input-fixture-' + str(step)))
        home = tmp_path / 'native-home'; session = home / 'sessions' / (thread + '.jsonl'); session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text(json.dumps(dict(type='session_meta', payload=dict(id=thread, cwd=str(work)))) + '\n')
        (record / 'native.jsonl').write_text(json.dumps(dict(type='thread.started', thread_id=thread)) + '\n')
        outcome = c.read(record / 'native-outcome.json')
        outcome.update(thread_id=thread, native_identity=dict(participant_scope='codex_exec_single_agent_v1',
            home=str(home), path=str(session)))
        c.write(record / 'native-outcome.json', outcome)
        return original_seal(root, step)

    monkeypatch.setattr(c, 'seal_result', seal)
    finish(root, 1, 'H', [], action='experiment', files={'mesh.tdr': 'mesh'})
    job = c.build_job(root, 2, 'W', [1]); work = c.workspace(root, 2); record = c.host_record(work)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/002.json')))
    config_path = work / 'work/execution.json'; config = e.configure(root, 2, 'tcad_execution:tcad', options, config_path)
    author = work / 'work/implementation.json'
    c.write(author, dict(solver_kind='sdevice', files=[dict(relative_path='main.cmd', content='Solve {}\n')],
        input_slots=[dict(semantic_name='mesh', target_relative_path='inputs/mesh.tdr', media_type='application/octet-stream')],
        entrypoint='main.cmd', resource_limits=dict(wall_time_seconds=1, cpu_time_seconds=1,
            max_memory_bytes=1024, max_output_bytes=128, max_storage_bytes=100000)))
    aliases = work / 'work/scientific-files.json'; c.write(aliases, {'mesh': 'inputs/parents/001/artifacts/mesh.tdr'})
    return root, backend, options, config, config_path, author, aliases


def _reviewed_execution(chain, capsys, *, opaque=False):
    root, backend, options, config, config_path, author, aliases = chain
    output = c.workspace(root, 2) / 'artifacts/package.json'
    if opaque:
        from scidiscovery.artifact_agent.schema.common import canonical_json
        from tcad_artifact.project_packager import ExecutionPackage
        old = json.loads((Path(__file__).parents[1] / 'fixtures/tcad_analysis_package.json').read_bytes())
        old['project']['resource_limits'] = c.read(author)['resource_limits']
        package = ExecutionPackage.model_validate_json(json.dumps(dict(project=old['project'], capability=old['capability'])),strict=True)
        output.write_bytes(canonical_json(package.model_dump(mode='json')))
    else:
        assert e.main(['--config', str(config_path), 'compile', '--implementation', str(author),
            '--scientific-files', str(aliases), '--output', str(output)]) == 0
        compiled = json.loads(capsys.readouterr().out)
        assert compiled['status'] == 'compiled' and not backend.prepared and not backend.policy_reads
    before = output.read_bytes(); finish(root, 2, 'W', [1])
    seal = c.read(c.host_record(c.workspace(root, 2)) / 'result-identity.json')
    if opaque:
        assert 'compiled_implementations' not in seal
    else:
        assert len(seal['compiled_implementations']) == 1
        assert ('codex_exec', str(uuid.uuid5(uuid.NAMESPACE_URL, 'native-input-fixture-1'))) in {
            tuple(row) for row in review.authors(root, 2)['participants']}
    job, _, record = finish(root, 3, 'A', [2], action='formal_review', selection=['W002-I001'])
    role, parents, instruction, back = review.request_route(root, job, c.read(record / 'result.json'), [3])
    review_job = c.build_job(root, 4, role, parents, instruction, back)
    review_record = c.host_record(c.workspace(root, 4))
    c.write(review_record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/004.json')))
    c.write(review_record / 'science-output.json', dict(verdict='pass', summary='Synthetic exact review',
        scientific_result='Synthetic transport/input contract only.', limitations=['No real platform/solver qualification.']))
    c.write(review_record / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=1))
    c.write(review_record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        usage=native.usage(root), raw_ref=c.file_ref(review_record / 'science-output.json')))
    c.seal_result(root, 4)
    _, parents, instruction, back = review.return_route(root, review_job, [2, 3, 4])
    c.build_job(root, 5, 'A', parents, instruction, back)
    finish(root, 5, 'A', parents, selection=['W002-I001'])
    job = c.build_job(root, 6, 'W', [5]); work = c.workspace(root, 6); record = c.host_record(work)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/006.json')))
    active = work / 'work/execution.json'; current = e.configure(root, 6, 'tcad_execution:tcad', options, active)
    secret = root / 'operator.key'; secret.write_bytes(b'x' * 32); secret.chmod(0o600)
    authority.bind_secret(root, b'x' * 32, source=secret)
    return root,backend,output,active,current,before


def test_public_compile_exact_review_and_existing_run_keep_native_original(chain, capsys):
    root,backend,output,active,current,before=_reviewed_execution(chain,capsys)
    assert e.main(['--config', str(active), 'run', '--payload', str(output)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'queued' and backend.prepared == [{'mesh': b'mesh'}] and backend.submits == 1
    accepted = e.Records(current).accepted(result['call_id'])
    seal = c.read(c.host_record(c.workspace(root, 2)) / 'result-identity.json')
    entry = next(row for row in seal['compiled_implementations'] if row['material']['source'] == str(output))
    compiled = inputs.verify_compilation(root, entry['compiler'], material=entry['material'], client_binding=entry['client_binding'])
    original = compiled['output']
    assert original == e.proof(original['path'])
    assert Path(original['path']).is_relative_to(root / 'host-runtime')
    assert accepted['tcad']['payload']['local_path'] == original['path'] != str(output)
    assert all(accepted['tcad']['payload'][key] == original[key] for key in ('size_bytes',))
    assert c.read(output) == dict(worker_implementation_version=1, implementation_ref='I001')
    assert accepted['execution_reservation'] is not None
    assert e.main(['--config', str(active), 'run', '--payload', str(output)]) == 2
    assert json.loads(capsys.readouterr().out)['call_id'] == result['call_id'] and backend.submits == 1
    assert output.read_bytes() == before


def test_self_contained_v2_terminal_keeps_exact_inputs_and_next_subject_review(chain, capsys):
    from copy import deepcopy
    from scidiscovery.research_loop import history, qualification as q
    root, backend, output, active, current, _ = _reviewed_execution(chain, capsys, opaque=True)
    assert e.main(['--config', str(active), 'run', '--payload', str(output)]) == 2
    call = json.loads(capsys.readouterr().out)['call_id']
    job = c.read(root / 'jobs/006.json')
    accepted = e.Records(current).accepted(call)
    assert backend.submits == 1 and backend.prepared == [{}]
    assert 'config_record' not in accepted  # Local metadata is not a generic accepted field.
    assert accepted['interface']['config_record'] == current['execution_interface']['config_record']
    assert inputs.managed_source_contributions(root, job, accepted)['complete']
    assert history.managed_input_coverage(root, job, history.managed_calls(root, job))[0]['complete'] is False
    backend.state = 'succeeded'
    assert e.main(['--config', str(active), 'reconcile', '--call-id', call]) == 0
    capsys.readouterr()
    finish(root, 6, 'W', [5], files={'revised.txt':'A derivative of the exact opaque package.'})
    record = c.host_record(c.workspace(root, 6)); seal = c.read(record / 'result-identity.json')
    proof = q._read(root, seal['source_provenance'])
    covered = proof['managed_input_coverage'][0]
    assert covered['complete'] and covered['binding']['subject']['producer_step'] == 2
    assert 'compiler_ref' not in covered and 'compiled_implementations' not in seal
    assert q.output_status(root, 6)['restriction'] == 'clear' and review.authors(root, 6)['complete']
    assert set(map(tuple,review.authors(root,2)['participants'])) <= set(map(tuple,review.authors(root,6)['participants']))
    selecting = c.build_job(root, 7, 'A', [6])
    material = next(row for row in seal['materials'] if row['source'].endswith('/revised.txt'))
    choices = [row['id'] for row in history.implementation_options(root, 6) if row['label']=='revised.txt'
        and history.resolve_implementation(root,row['id'],selecting['history']['nodes'])==material]
    assert len(choices)==1
    requested,_,request_record=finish(root,7,'A',[6],action='formal_review',selection=choices)
    _,_,_,back=review.request_route(root,requested,c.read(request_record/'result.json'),[7])
    assert back['kind']=='formal_review' and q._read(root,back['request'])['subject']['material']==material
    changed=deepcopy(accepted);changed['input_sources']['view']['sources']['sources'][0]['original']['producer_step']=1
    with pytest.raises(q.IntegrityError,match='frozen compiler source'):
        inputs.managed_source_contributions(root,job,changed)
    changed=deepcopy(accepted);changed['input_sources']['view']['sources']['complete']=False
    assert inputs.managed_source_contributions(root,job,changed)['complete'] is False
    changed=deepcopy(accepted);changed['interface']['config_record']=dict(changed=True)
    with pytest.raises(q.IntegrityError,match='original native owner'):
        inputs.managed_source_contributions(root,job,changed)
    legacy={key:value for key,value in accepted.items() if key!='input_sources'}
    assert inputs.managed_source_contributions(root,job,legacy) is None
    archive=root/accepted['input_sources']['view']['evidence'][1]['path']
    archive.chmod(0o600);archive.write_bytes(b'changed actual prepared archive')
    with pytest.raises(q.IntegrityError,match='preparation original changed'):
        inputs.managed_source_contributions(root,job,accepted)
    assert backend.submits==1


def test_native_observation_new_subject_review_and_next_run_use_covered_inputs(chain, capsys, monkeypatch):
    from copy import deepcopy
    from scidiscovery.research_loop import history, qualification as q
    root, backend, output, active, current, _ = _reviewed_execution(chain, capsys)
    from scidiscovery.research_loop import atomic_files
    log=b'synthetic input-chain terminal\n'
    copies=[];copy=atomic_files.copy_original
    def counted_copy(source,target,**options):
        if Path(source)==backend.output_root/'tcad_log':copies.append(options['size_bytes'])
        return copy(source,target,**options)
    monkeypatch.setattr(atomic_files,'copy_original',counted_copy)
    assert e.main(['--config', str(active), 'run', '--payload', str(output)]) == 2
    call = json.loads(capsys.readouterr().out)['call_id']
    job_before = c.read(root / 'jobs/006.json')
    unknown = history.managed_input_coverage(root, job_before, history.managed_calls(root, job_before))
    assert len(unknown) == 1 and unknown[0]['complete'] is False
    backend.state = 'succeeded'
    assert e.main(['--config', str(active), 'reconcile', '--call-id', call]) == 0
    capsys.readouterr()
    assert backend.submits == 1 and backend.prepared == [{'mesh': b'mesh'}]
    observed=c.read(e.Records(current).directory(call)/'execution-observation.json')
    retained=next(item for item in observed['outputs'] if item['name']=='tcad_log')
    cas=Path(retained['path'])
    inode=(cas.stat().st_dev,cas.stat().st_ino)
    assert all((path.stat().st_dev,path.stat().st_ino)==inode
        for path in (Path(retained['path']),Path(retained['source']['path'])))
    assert (backend.output_root/'tcad_log').stat().st_ino!=cas.stat().st_ino
    assert copies==[len(log)], 'Native output, call original and core observation freeze bytes only once'
    work = c.workspace(root, 6); authored = work / 'work/revised.json'
    author = c.read(chain[5]); author['input_slots'] = []
    author['files'][0]['content'] = 'Solve { Coupled { Poisson } }\n'
    c.write(authored, author)
    revised = work / 'artifacts/revised-package.json'
    e.compile_implementation(current, e.load(current), authored, {}, revised)
    finish(root, 6, 'W', [5])
    record = c.host_record(work); seal = c.read(record / 'result-identity.json')
    proof = q._read(root, seal['source_provenance'])
    assert len(proof['managed_input_coverage']) == 1
    coverage = proof['managed_input_coverage'][0]
    assert coverage['call_id'] == call and coverage['complete'] is True
    assert proof['additional_input_restriction'] == 'clear'
    rows = history.read_observation(root, job_before, seal)['managed_calls']
    subject = coverage['binding']['subject']
    producer = q._read(root, subject['producer_seal'])
    entry = next(row for row in producer['compiled_implementations'] if row['material'] == subject['material'])
    assert coverage['compiler_ref'] == entry['compiler']
    assert coverage['client_binding'] == entry['client_binding']
    for mutation in ('changed', 'omitted'):
        altered = deepcopy(proof['managed_input_coverage'])
        if mutation == 'changed':
            altered[0]['client_binding']['path'] = '/wrong/compiler-client.json'
        else:
            altered[0].pop('client_binding')
        with pytest.raises(q.IntegrityError, match='no longer names its exact payload'):
            history.validate_input_coverage(root, job_before, rows, altered)
    for state in ('unknown', 'not_submitted'):
        with pytest.raises(q.IntegrityError, match='no complete coverage'):
            history.validate_input_coverage(root, job_before, [dict(rows[0], status=state)], proof['managed_input_coverage'])
    assert q.output_status(root, 6)['restriction'] == 'clear'
    assert review.authors(root, 6)['complete'] is True
    expected_authors = {tuple(row) for row in review.authors(root, 2)['participants']}
    assert expected_authors <= {tuple(row) for row in review.authors(root, 6)['participants']}
    # A changed ref cannot reuse the coverage fact; no extra backend action.
    changed = deepcopy(proof['managed_input_coverage'])
    changed[0]['binding']['request']['path'] = '/wrong/authorization-request.json'
    with pytest.raises(q.IntegrityError, match='binding changed'):
        q._output(root, c.read(root / 'jobs/006.json'), seal, managed_coverage=changed)
    original_job = c.read(root / 'jobs/006.json')
    accepted = e.Records(current).accepted(call)
    assert authority.accepted_input_subject(root, original_job,
        {key:value for key,value in accepted.items() if key != 'execution_authority'}) is None
    subject = coverage['binding']['subject']
    assert inputs.execution_input_coverage(root, dict(original_job, selected_inputs=[]), subject) is None
    wrong = dict(subject, producer_step=1)
    assert inputs.execution_input_coverage(root, original_job, wrong) is None
    # Legacy bytes without a projection keep the former conservative meaning.
    legacy = q._output(root, original_job, seal)
    assert 'managed_input_coverage' not in legacy
    assert legacy['additional_input_restriction'] == 'unknown'
    selecting = c.build_job(root, 7, 'A', [6])
    material = next(item for item in seal['materials'] if item['source'] == str(revised))
    choices = [item['id'] for item in history.implementation_options(root, 6)
        if item['label'] == revised.name
        and history.resolve_implementation(root, item['id'], selecting['history']['nodes']) == material]
    assert len(choices) == 1, choices
    job, _, routed = finish(root, 7, 'A', [6], action='formal_review', selection=choices)
    role, parents, instruction, back = review.request_route(root, job, c.read(routed / 'result.json'), [7])
    assert back['kind'] == 'formal_review'
    assert q._read(root, back['request'])['subject']['material'] == material
    reviewed = c.build_job(root, 8, role, parents, instruction, back)
    review_record = c.host_record(c.workspace(root, 8))
    c.write(review_record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/008.json')))
    c.write(review_record / 'science-output.json', dict(verdict='pass', summary='Exact revised fixture',
        scientific_result='Declared input/author coverage only.', limitations=['Synthetic transport and native identities.']))
    c.write(review_record / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=1))
    c.write(review_record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        usage=native.usage(root), raw_ref=c.file_ref(review_record / 'science-output.json')))
    c.seal_result(root, 8)
    qualification = q._read(root, c.read(review_record / 'result-identity.json')['formal_review'])
    assert qualification['status'] == 'passed' and qualification['independence'] == 'independent'
    assert qualification['subject']['material'] == material
    _, parents, instruction, back = review.return_route(root, reviewed, [6, 7, 8])
    returning = c.build_job(root, 9, 'A', parents, instruction, back)
    assert history.resolve_implementation(root, choices[0], returning['history']['nodes']) == material
    finish(root, 9, 'A', parents, selection=choices)
    c.build_job(root, 10, 'W', [9]); later = c.host_record(c.workspace(root, 10))
    c.write(later / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/010.json')))
    config_path = c.workspace(root, 10) / 'work/execution.json'
    e.configure(root, 10, 'tcad_execution:tcad', chain[2], config_path)
    backend.state = 'queued'
    assert e.main(['--config', str(config_path), 'run', '--payload', str(revised)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'queued', result
    assert backend.submits == 2
    assert backend.prepared == [{'mesh': b'mesh'}, {}]
    # Live qualification revalidation gains no dependency on the private key.
    original_bytes = (record / 'provenance.json').read_bytes(), (record / 'result-identity.json').read_bytes()
    (root / 'operator.key').unlink()
    assert q.output_status(root, 6)['restriction'] == 'clear'
    assert review.authors(root, 6)['complete'] is True
    history._source(root, 6)
    assert ((record / 'provenance.json').read_bytes(), (record / 'result-identity.json').read_bytes()) == original_bytes


@pytest.mark.parametrize('chain', ['unknown_source'], indirect=True)
def test_known_native_authors_with_unknown_restrictions_can_review_but_not_execute(chain, capsys):
    from scidiscovery.research_loop import qualification as q
    root, backend, output, active, _, _ = _reviewed_execution(chain, capsys)
    assert review.authors(root, 2)['complete'] is True
    assert q.output_status(root, 2)['restriction'] == 'unknown'
    assert e.main(['--config', str(active), 'run', '--payload', str(output)]) == 2
    assert 'unknown' in json.loads(capsys.readouterr().out)['note']
    assert backend.submits == 0 and backend.prepared == [] and backend.policy_reads == 0


def test_unbound_alias_and_author_read_race_reject_before_gate(chain, monkeypatch):
    root, backend, _, config, _, author, aliases = chain
    adapter = e.load(config); output = c.workspace(root, 2) / 'artifacts/package.json'
    with pytest.raises(ValueError, match='missing or ambiguous'):
        e.compile_implementation(config, adapter, author, {'mesh': 'mesh.tdr'}, output)
    original_identity = inputs.q._identity

    def changed(path):
        if Path(path) == author:
            document = c.read(author); document['files'][0]['content'] = 'Solve { changed }\n'; c.write(author, document)
        return original_identity(path)

    monkeypatch.setattr(inputs.q, '_identity', changed)
    with pytest.raises(ValueError, match='between reading'):
        e.compile_implementation(config, adapter, author, c.read(aliases), output)
    assert not output.exists() and not backend.prepared and not backend.policy_reads


def test_same_hash_wrong_origin_and_changed_package_cannot_reuse_compiler_proof(chain):
    root, backend, _, config, _, author, aliases = chain
    job = c.read(root / 'jobs/002.json'); original_material = job['direct_materials'][0]
    original = inputs._producer(root, original_material)
    originals = {path:path.read_bytes() for path in (root / 'jobs/002.json',
        Path(original_material['source']), root / original['producer_seal']['path'])}
    wrong = dict(original, producer_step=2)
    with pytest.raises(ValueError, match='frozen compiler source'):
        inputs.resolve_native_original(root, wrong, compiler_job=job)
    with pytest.raises(ValueError, match='Native input differs from its frozen compiler source'):
        inputs.resolve_native_original(root, dict(original, instance_id='another-instance'), compiler_job=job)
    assert all(path.read_bytes() == raw for path,raw in originals.items())
    output = c.workspace(root, 2) / 'artifacts/package.json'
    e.compile_implementation(config, e.load(config), author, c.read(aliases), output)
    from scidiscovery.research_loop import experiment_client
    before = output.read_bytes()
    package = experiment_client.payload_path(config, output)
    assert package != output and package.is_relative_to(root / 'host-runtime')
    packet = c.read(package); packet['resolved_inputs'][0]['original'] = wrong
    package.chmod(0o600); c.write(package, packet)
    with pytest.raises(ValueError):
        finish(root, 2, 'W', [1])
    assert not (c.host_record(c.workspace(root, 2)) / 'result-identity.json').exists()
    assert output.read_bytes() == before
    assert not backend.policy_reads and not backend.prepared


@pytest.fixture
def late_call(chain,capsys,monkeypatch):
    """Synthetic owner cleanup only; actual seal/gate/ledger/factory stay in path."""
    from scidiscovery.research_loop import execution,runtime
    root,backend,output,active,config,_=_reviewed_execution(chain,capsys)
    admission=backend.execution_admission
    monkeypatch.setattr(backend,'execution_admission',lambda *a,**kw:admission(*a,**kw).model_copy(
        update={'outcome':'require_human_approval'}))
    c.write(root/'host-started.json',budget.clock_stamp())
    assert e.main(['--config',str(active),'run','--payload',str(output)])==2
    pending=json.loads(capsys.readouterr().out)
    assert pending['status']=='authorization_required' and backend.submits==0 and not backend.prepared
    assert pending['authorization_request']==dict(status='execution_decision_required')
    call=pending['call_id'];original_seal=c.seal_result

    def cleaned(root,step):
        if step==6:
            record=execution.record(root,step)
            outcome=c.read(record/'native-outcome.json');outcome['cleanup']='observed_group_cleaned'
            c.write(record/'native-outcome.json',outcome)
            owner=dict(pid=999999999,start_ticks=1,pgid=999999999,sid=999999999,
                boot_id='synthetic-original-boot',task_ref=execution.identity(root,step))
            for name in ('runner-process.json','native-process.json'):c.write(record/name,owner)
        return original_seal(root,step)

    monkeypatch.setattr(c,'seal_result',cleaned)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    finish(root,6,'W',[5])
    seal_path=c.host_record(c.workspace(root,6))/'result-identity.json'
    pending_set=inputs.q._read(root,c.read(seal_path)['pending_executions'])
    assert pending_set['complete'] is True
    allocations=[row for row in pending_set['allocations'] if row['call_id']==call]
    assert len(allocations)==1 and len(allocations[0]['requests'])==1
    request=allocations[0]['requests'][0]
    original,canonical=authority.request(root,Path(request['path']).parent.name)
    assert canonical==request and original['call_id']==call
    assert original['job']==inputs.q._ref(root,root/'jobs/006.json')
    assert original['native_intent']==inputs.q._ref(root,c.host_record(c.workspace(root,6))/'native-intent.json')
    return root,backend,config,call,request,seal_path.read_bytes()


def _approve(root,ref):
    key=Path(ref['path']).parent.name
    tokens=authority.form_tokens(root,key,secret=b'x'*32,boot='fixture-ui',browser_token='fixture-browser')
    return authority.record_decision(root,key,secret=b'x'*32,boot='fixture-ui',browser_token='fixture-browser',
        **tokens,selected_option=authority.OPTIONS[0],rationale='Explicit synthetic exact decision')


def _resume(root,call,**options):
    return controller.execution_resume(root,6,call,max_seconds=20,authorization_note='Explicit finite host recovery',**options)


def test_late_decision_public_resume_preserves_call_pending_prefix_and_cancelled(late_call,monkeypatch,capsys):
    from scidiscovery.research_loop import cli,execution_recovery as recovery,history,search
    root,backend,config,call,request,seal_bytes=late_call
    _approve(root,request)
    records=e.Records(config);call_directory=records.root/'calls'/call
    assert not call_directory.exists()
    # An already frozen A continues to see the old zero-accepted prefix.
    old_job=c.build_job(root,7,'A',[6]);old_job_bytes=(root/'jobs/007.json').read_bytes()
    backend.state='queued'
    monkeypatch.setattr(backend,'status_details',lambda external:dict(state=backend.state,run_id=external))
    args=['execution-resume','--root',str(root),'--step','6','--call-id',call,
          '--max-seconds','20','--authorization-note','Explicit finite fixture window']
    assert cli.main(args)==0
    assert json.loads(capsys.readouterr().out)['call_id']==call and backend.submits==1
    assert records.directory(call)==call_directory
    accepted=e.Records(config).accepted(call);reservation=accepted['execution_reservation']
    assert accepted['execution_authority']['path'].endswith('/authorization.json')
    assert recovery.prefix(root,7)==[]
    assert 'execution_recoveries' not in old_job['history']
    refs=recovery.prefix(root,9);view=recovery.call_view(root,[],refs)
    assert view[(6,call)]['native_state']=='queued' and view[(6,call)]['row']['status']=='unknown'
    assert recovery.resolved(root,refs)=={} and recovery.pending(root) and not recovery.pending(root,unknown_only=True)
    count=len(refs);assert _resume(root,call)['status']=='execution_resume_recorded'
    assert len(recovery.prefix(root,9))==count and backend.submits==1
    backend.state='cancelling';_resume(root,call)
    assert recovery.call_view(root,[],recovery.prefix(root,9))[(6,call)]['native_state']=='cancelling'
    backend.state='cancelled'

    def collect(external,*,context):
        context.remaining_seconds();directory=root/'synthetic-terminal';directory.mkdir(exist_ok=True)
        rows=[]
        for name,data,media in [('tcad_log',b'cancelled\n','text/plain'),('tcad_manifest',json.dumps(dict(
                started_at=None,completed_at='2026-10-17T00:00:00Z',terminal_state='cancelled',
                exit_code=130,error='synthetic cancellation',outputs=[])).encode(),'application/json')]:
            path=directory/name;path.write_bytes(data)
            rows.append(LocalFileDescriptor(name=name,local_path=str(path),size_bytes=len(data),media_type=media))
        return tuple(rows)

    monkeypatch.setattr(backend,'collect_with_budget',collect,raising=False)
    _resume(root,call)
    refs=recovery.prefix(root,9);latest=recovery.call_view(root,[],refs)[(6,call)]
    assert latest['phase']=='terminal' and latest['row']['status']=='known_failed'
    assert latest['row']['execution']['native_state']=='cancelled' and not recovery.pending(root)
    sources=history.source_sequence(root,7)
    projected=recovery.project_records(root,history.records_from_sources(root,sources,write_cache=False),refs)
    assert len(projected[-1]['observation']['managed_calls'])==1
    assert projected[-1]['observation']['managed_calls'][0]['accepted']==latest['row']['accepted']
    assert search.fold(root,sources,recovery_refs=[])['opportunity_unknown'] is False
    assert budget.execution_reservation(root,reservation)['released'] is False
    assert (root/'jobs/007.json').read_bytes()==old_job_bytes
    assert (c.host_record(c.workspace(root,6))/'result-identity.json').read_bytes()==seal_bytes


def test_expired_request_requires_explicit_successor_without_retry_ttl_refresh(late_call,monkeypatch):
    from scidiscovery.research_loop import execution_recovery as recovery
    root,backend,_,call,original,_=late_call
    old_bytes=(root/original['path']).read_bytes();old=c.read(root/original['path'])
    monkeypatch.setattr(authority.time,'time',lambda:old['expires_at']+1)
    assert _resume(root,call)['status']=='authorization_renewal_required'
    first=_resume(root,call,request_valid_seconds=30)
    assert first['status']=='authorization_required' and not recovery.prefix(root,9)
    new=first['authorization_request'];new_bytes=(root/new['path']).read_bytes()
    assert c.read(root/new['path'])['supersedes']==original and authority.request_successor(root,original)==new
    again=_resume(root,call,request_valid_seconds=30)
    assert again['authorization_request']==new and (root/new['path']).read_bytes()==new_bytes
    with pytest.raises(ValueError,match='expired'):_approve(root,original)
    _approve(root,new);assert _resume(root,call)['status']=='execution_resume_recorded'
    assert backend.submits==1 and (root/original['path']).read_bytes()==old_bytes


def test_missing_positive_cleanup_and_contradictory_prepare_never_dispatch(late_call,monkeypatch):
    from scidiscovery.research_loop import execution_resume as resume,qualification as q,runtime
    root,backend,_,call,request,_=late_call
    _approve(root,request)
    seal=c.read(c.host_record(c.workspace(root,6))/'result-identity.json')
    allocation=q._ref(root,root/'execution-authority/calls'/call/'allocation.json')
    with pytest.raises(ValueError,match='no positive'):resume.verify_pending_set(root,dict(seal,pending_executions=None),allocation)
    proof=root/seal['pending_executions']['path'];before=proof.read_bytes();proof.chmod(0o600);proof.write_bytes(before+b' ')
    with pytest.raises(ValueError):_resume(root,call)
    proof.write_bytes(before);proof.chmod(0o400)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:True)
    with pytest.raises(ValueError,match='live or unknown'):_resume(root,call)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    c.write(root/'execution-authority/calls'/call/'prepare-intent.json',dict(note='uncertain original side effect'))
    with pytest.raises(ValueError,match='contradict'):_resume(root,call)
    assert backend.submits==0 and not backend.prepared


def test_interrupted_resume_intent_never_regains_fresh_dispatch(late_call,monkeypatch):
    from scidiscovery.research_loop import execution_recovery as recovery
    root,backend,config,call,request,_=late_call;_approve(root,request)
    original=backend.prepare
    monkeypatch.setattr(backend,'prepare',lambda *args,**kwargs:(_ for _ in ()).throw(TimeoutError('synthetic uncertain prepare')))
    with pytest.raises(TimeoutError):_resume(root,call)
    assert recovery.call_view(root,[],recovery.prefix(root,9))[(6,call)]['phase']=='resume_intent'
    assert not (e.Records(config).root/'calls'/call).exists()
    monkeypatch.setattr(backend,'prepare',original)
    assert _resume(root,call)['status']=='unknown' and backend.submits==0 and not backend.prepared


def test_reservation_gap_renews_authority_without_second_reservation_or_refund(late_call,monkeypatch):
    from scidiscovery.research_loop import execution_resume as resume,execution_recovery as recovery
    root,backend,config,call,request,_=late_call;_approve(root,request)
    begin=resume._RecoveryAdmission.begin
    monkeypatch.setattr(resume._RecoveryAdmission,'begin',lambda *args:(_ for _ in ()).throw(TimeoutError('after reserve before prepare boundary')))
    with pytest.raises(TimeoutError):_resume(root,call)
    held=budget.execution_accounting(root)['reservations']
    assert len(held)==1 and held[0]['bound'] is None and not recovery.prefix(root,9)
    reservation=held[0]['ref'];remaining=budget.execution_accounting(root)['remaining']
    monkeypatch.setattr(resume._RecoveryAdmission,'begin',begin)
    expires=c.read(root/request['path'])['expires_at']
    monkeypatch.setattr(authority.time,'time',lambda:expires+1)
    renewal=_resume(root,call,request_valid_seconds=30)
    assert renewal['status']=='authorization_required' and not recovery.prefix(root,9)
    _approve(root,renewal['authorization_request'])
    assert _resume(root,call)['status']=='execution_resume_recorded' and backend.submits==1
    assert e.Records(config).accepted(call)['execution_reservation']==reservation
    events=budget._events(root,c.settings(root))[0]
    assert sum(row['kind']=='resource_reserve' for row in events)==1
    assert sum(row['kind']=='resource_rebind' for row in events)==1
    assert not any(row['kind']=='resource_release' for row in events)
    assert budget.execution_accounting(root)['remaining']==remaining


def test_native_compile_retains_distinct_originals_for_equal_package_bytes(chain,capsys):
    root,backend,options,config,config_path,author,aliases=chain
    for name in ('first','second'):
        output=c.workspace(root,2)/f'artifacts/{name}.json'
        assert e.main(['--config',str(config_path),'compile','--implementation',str(author),
            '--scientific-files',str(aliases),'--output',str(output)])==0
        assert json.loads(capsys.readouterr().out)['status']=='compiled'
    compiled=[c.read(path) for path in inputs._directory(root,2).glob('*/compiler.json')]
    assert len(compiled)==2
    outputs=[row['output'] for row in compiled]
    first,second=(Path(row['path']) for row in outputs)
    assert first!=second and first.read_bytes()==second.read_bytes()
    assert (first.stat().st_dev,first.stat().st_ino)!=(second.stat().st_dev,second.stat().st_ino)
    descriptor=LocalFileDescriptor(name='storage-admission-only',local_path=str(first),
        size_bytes=outputs[0]['size_bytes'],media_type='application/json')
    accepted=e.Records(config).accept(descriptor,{},config['execution_interface'])
    retained=Path(accepted['script']['path'])
    assert accepted['source_script']==str(first)
    assert retained.read_bytes()==first.read_bytes()
    assert backend.submits==0


from test_mutable_run_inputs import fixed_code


@pytest.fixture
def compiler_case(tmp_path, monkeypatch):
    """Synthetic admission only; no CLI, native launcher or scientific backend."""
    root = tmp_path / 'instance'; work = c.workspace(root, 2)
    job = dict(step=2, role='W', worker_view_version=1, global_materials=[],
        direct_refs=[], direct_materials=[], selected_inputs=[])
    job_path = root / 'jobs/002.json'; c.write(job_path, job)
    intent_path = root / 'native-evidence/002/native-intent.json'
    c.write(intent_path, dict(task_ref=c.record_ref(job_path)))
    contract = dict(key='implementation_authoring')
    config = dict(worker_view_version=1,
        record_dir=str(root / 'host-runtime/experiment-control/002/records'),
        scope=dict(step=2, role='W', job_ref=c.record_ref(job_path)), native_attempt=dict(number=0),
        execution_interface=dict(public_capabilities=[contract]))
    c.write(root / 'host-runtime/experiment-control/002/interfaces/000.json', config)
    author = work / 'code/author.json'; author.parent.mkdir(parents=True)
    author.write_bytes(b'{"author":"v1"}\n')
    capability = root / 'operator-capability.json'; c.write(capability, dict(fixture=True))
    output = root / 'host-runtime/experiment-control/002/implementations/I001/package.json'
    output.parent.mkdir(parents=True)
    host = object.__new__(inputs.NativeInputHost)
    host.root, host.job, host.config = root, job, config
    host.intent = inputs.q._ref(root, intent_path)
    monkeypatch.setattr(authority, '_remaining', lambda *args: (None, 60.))
    monkeypatch.setattr(c, 'settings', lambda *args: {})
    receipts = []; save = inputs.q._save

    def saved(owner, path, value):
        ref = save(owner, path, value)
        if path.name == 'compiler.json':receipts.append((path, ref))
        return ref

    monkeypatch.setattr(inputs.q, '_save', saved)
    return dict(host=host, author=author, output=output, receipts=receipts,
        options=dict(slots=[], aliases={}, capability=inputs.q._identity(capability), contract=contract))


def test_compiler_git_builder_reads_commit_after_draft_edit_and_export_cleanup(compiler_case, fixed_code, monkeypatch):
    from scidiscovery.research_loop import code_versions
    case = compiler_case; host = case['host']; before = case['author'].read_bytes()
    calls = []; exports = []; materialize = code_versions.materialize
    capture = code_versions.capture; cleanup = code_versions.cleanup_export

    def captured(root, **options):
        calls.append(options['call_id'])
        return capture(root, **options)

    def exported(root, **options):
        result = materialize(root, **options); exports.append(result)
        case['author'].write_bytes(b'{"author":"v2"}\n')
        assert options['call_id'] == calls[0]
        return result

    def cleaned(root, **options):
        assert options['call_id'] == calls[0]
        return cleanup(root, **options)

    monkeypatch.setattr(code_versions, 'capture', captured)
    monkeypatch.setattr(code_versions, 'materialize', exported)
    monkeypatch.setattr(code_versions, 'cleanup_export', cleaned)
    received = []
    result = host.compile(case['author'], case['output'], **case['options'],
        builder=lambda raw, slots: received.append((raw, slots)) or b'{"package":"v1"}\n')
    receipt, proof = case['receipts'][0]; value = c.read(receipt)
    assert received == [(before, [])] and receipt.parent.name == calls[0]
    assert set(value['code_version']) == {'version','object_format','commit','tree_path','selection','entry','logical_root'}
    assert not {'author','author_origin'} & set(value) and value.get('author_input') is None
    assert not (receipt.parent / 'author.json').exists()
    assert all(set(value[key])=={'path','size_bytes'} for key in ('job','configuration','native_intent','capability','capability_source'))
    assert set(value['output'])=={'path','size_bytes','file_stat'}
    assert len(exports) == 1 and not exports[0].exists()
    assert 'no execution permission' in result['qualification']
    case['author'].unlink()
    assert inputs.verify_compilation(host.root, proof) == value
    assert case['output'].read_bytes() == b'{"package":"v1"}\n'


@pytest.mark.parametrize('damage', ['mixed_author', 'schema', 'pin', 'package'])
def test_compiler_git_verification_rejects_changed_binding_or_package(compiler_case, fixed_code, damage):
    case = compiler_case; host = case['host']
    host.compile(case['author'], case['output'], **case['options'], builder=lambda raw, slots: b'{}')
    receipt, proof = case['receipts'][0]; value = c.read(receipt)
    assert inputs.verify_compilation(host.root,proof)==value
    if damage == 'pin':
        fixed_code['bindings'].clear()
    elif damage == 'package':
        case['output'].chmod(0o600); case['output'].write_bytes(b'{"changed":true}')
    else:
        if damage == 'mixed_author':value['author'] = value['capability']
        elif damage == 'schema':value['code_version'].pop('tree_path')
        else:value['code_version']['selection'] = 'directory'
        receipt.chmod(0o600); c.write(receipt, value); proof = inputs.q._ref(host.root, receipt)
    with pytest.raises(ValueError):
        inputs.verify_compilation(host.root, proof)


def test_compiler_git_client_package_uses_execution_fingerprint_without_changing_controls(compiler_case,fixed_code):
    from scidiscovery.operations.execution import AdapterCapability
    from scidiscovery.research_loop import qualification as q
    case=compiler_case;host=case['host'];work=c.workspace(host.root,2)
    capability=AdapterCapability('implementation_authoring','authoring','fixture.compiler.v1',1,
        'application/json',b'{}',{})
    class Adapter:
        def capabilities(self):return (capability,)
        def compile_implementation(self,source,aliases,output):
            assert aliases=={}
            return host.compile(source,output,**dict(case['options'],contract=host.config['execution_interface']['public_capabilities'][0]),
                builder=lambda raw,slots:b'{"package":"fixed"}')
    adapter=Adapter();host.config['execution_interface']['public_capabilities']=e._public_capabilities(adapter)
    c.write(host.root/'host-runtime/experiment-control/002/interfaces/000.json',host.config)
    public=work/'artifacts/implementation.json';public.parent.mkdir(parents=True)
    result=e.compile_implementation.__wrapped__(host.config,adapter,case['author'],{},public)
    receipt,reference=case['receipts'][0];compiler=inputs.verify_compilation(host.root,reference)
    package=Path(compiler['output']['path']);client=c.read(package.parent/'client.json')
    assert result['status']=='compiled' and client['package']==compiler['output']==e.proof(package)
    assert set(client['package'])=={'path','size_bytes','file_stat'}
    assert all(set(compiler[key])=={'path','size_bytes'} for key in ('job','configuration','native_intent','capability'))
    material=dict(path='inputs/parents/002/artifacts/implementation.json',source=str(public),size_bytes=public.stat().st_size)
    assert inputs.verify_compilation(host.root,reference,material=material,client_binding=q._ref(host.root,package.parent/'client.json'))==compiler
