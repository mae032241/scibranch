"""Pi defaults through common owners; no scientific qualification is implied.

Only process_e2e nodes start installed Pi, an inert Local child and a finite
loopback provider. The existing separate collector owns durable process cleanup.
"""
import ast
import copy
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import threading

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import (backends, budget, configuration_form,
    contracts as c, controller, execution, experiments, formal_review, native,
    pi_backend as pi, pi_namespace, research_config, worker_research)
from test_pi_backend_v2 import (OPTIONS, adapter,
    collect_separately, configure_installed_pi_fixture, installed_pi_fixture,
    options, settings)
from test_pi_observation import FAULT_WRAPPER
from test_pi_backend_interfaces import retain_installed_repair_diagnostics


COMMON_POLICIES = ('execution_resource_policy', 'formal_review_policy',
    'execution_authority_policy', 'candidate_audit_policy', 'delivery_policy',
    'selection_policy', 'history_observer')


def default_options(tmp_path, **changes):
    value = options(tmp_path, native_options=dict(OPTIONS, workspace='workspace-write'), **changes)
    # None is meaningful to generic setdefault: actually omit fixture scopes.
    for key in ('experiment_mode', 'resume_policy', 'worker_fragment_policy', 'node_budget_policy'):
        value.pop(key, None)
    return value


def test_source_release_manifest_includes_both_backend_examples():
    source = Path(__file__).resolve().parents[2]
    script = source/'scripts/build_git_release.py'
    if not script.is_file():
        pytest.skip('Source release manifest is absent in this partial/installed tree')
    # Read only the known literal: never import or run the recursive builder.
    tree = ast.parse(script.read_text(encoding='utf-8'), filename=str(script))
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'ROOT_FILES'
            for target in node.targets)]
    assert len(values) == 1 and isinstance(values[0], ast.Tuple)
    root_files = ast.literal_eval(values[0])
    assert all(isinstance(item, str) for item in root_files)
    for name in ('research.example.toml', 'research.pi.example.toml'):
        assert root_files.count(name) == 1
        assert (source/name).is_file()


def test_source_pi_example_normalizes_default_local_rpc_cadence_without_native_io(monkeypatch):
    source = Path(__file__).resolve().parents[2]
    if not (source/'scripts/build_git_release.py').is_file():
        pytest.skip('Source release manifest is absent in this partial/installed tree')
    selected = []
    def which(executable):
        selected.append(executable)
        return sys.executable
    def forbidden(*a, **kw):
        pytest.fail('Source example normalization accessed native home or started a process')
    monkeypatch.setattr(shutil, 'which', which)
    monkeypatch.setattr(Path, 'home', forbidden)
    monkeypatch.setattr(pi_namespace, 'agent_directory', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    declared = research_config.load(source/'research.pi.example.toml')
    original = copy.deepcopy(declared)
    value = research_config.normalize(declared)
    assert declared == original and selected == ['pi']
    assert value['native_backend'] == 'pi'
    assert value['model'] == 'provider/exact-model-id'
    assert value['reasoning_effort'] == 'high' and value['service_tier'] is None
    assert declared['native_options'] == value['native_options'] == dict(
        workspace='workspace-write', network=True, web='disabled', subagents=False)
    # Normalization retains the omission; the common controller materializes Local.
    assert value.get('experiment_mode') is None and value.get('execution_config') is None
    backend = backends.selected(dict(value, native_launcher_argv=value['launcher']))
    assert backend.default_experiment_mode() == 'local'
    assert value['resume_policy'] == dict(max_attempts=2, protocol='pi_rpc_v1',
        usage_scope='turn', output_repair=True, output_only=True, execution_wake=True)
    assert value['worker_fragment_policy'] == dict(context='same_session', max_context_windows=1)
    ceilings = dict(max_tokens=2000000, max_seconds=3600, max_steps=32)
    assert {key: value[key] for key in ceilings} == ceilings
    assert all(type(value[key]) is int and value[key] > 0 for key in ceilings)
    assert value['node_budget_policy'] == {role: dict(max_observed_tokens=ceilings['max_tokens'],
        max_seconds=ceilings['max_seconds']) for role in 'HWAE'}
    assert backend.complete_authorship is True
    assert all(key not in value for key in COMMON_POLICIES)


def test_default_pi_controller_starts_v3_w_without_an_initial_h_or_native_process(
        tmp_path, monkeypatch, execution_registry):
    value = default_options(tmp_path, launcher=[sys.executable])
    assert 'research_strategy_version' not in value
    value['public_root'].mkdir(); value['objective'].write_text('Mechanical W-first scheduling only.')
    def forbidden(*a, **kw): pytest.fail('Source scheduling regression launched a native process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    prepared = research_config.prepare(**value)
    assert prepared['research_strategy_version'] == 3 and prepared['experiment_mode'] == 'local'
    called = []
    def stop(root, step, **kwargs):
        called.append(c.read(root/'jobs'/f'{step:03d}.json'))
        raise RuntimeError('Stop before original native execution')
    monkeypatch.setattr(controller, '_native_call', stop)
    controller.run(value['root'])
    assert len(called) == 1
    assert (called[0]['step'], called[0]['role'], called[0]['direct_refs']) == (1, 'W', [])
    assert called[0]['research_strategy_version'] == 3
    assert not (value['root']/'jobs/002.json').exists()
    assert not (execution.origin(value['root'], 1)/'native-process.json').exists()


def test_default_resume_uses_selected_executable_without_auth_or_home_redirection(monkeypatch):
    backend = adapter(native_launcher_argv=['operator-pi'])
    selected = []
    def which(executable):
        selected.append(executable)
        return '/operator/bin/pi'
    monkeypatch.setattr(shutil, 'which', which)
    def forbidden(*a, **kw): pytest.fail('Default policy read native auth/config or acquired another owner')
    for name in ('stat', 'open', 'resolve', 'home'):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(pi_namespace, 'agent_directory', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    value = backend.default_resume_policy()
    assert selected == ['operator-pi']
    assert value == dict(launcher=['/operator/bin/pi'], policy=dict(max_attempts=2,
        protocol='pi_rpc_v1', usage_scope='turn', output_repair=True, output_only=True,
        execution_wake=True))
    value['policy']['max_attempts'] = 1
    assert pi.DEFAULT_RESUME_POLICY['max_attempts'] == 2
    assert backend.default_experiment_mode() == 'local'


def test_default_resume_missing_selected_executable_refuses_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda executable: None)
    with pytest.raises(ValueError, match='Pi native launcher is unavailable'):
        adapter().default_resume_policy()


def test_default_normalization_keeps_generic_cadence_and_control_policy_omissions(tmp_path):
    value = default_options(tmp_path, launcher=[sys.executable])
    original = copy.deepcopy(value)
    normalized = research_config.normalize(value)
    assert value == original
    # The controller, not normalization, materializes the implied mode.
    assert normalized.get('experiment_mode') is None
    assert normalized['resume_policy'] == pi.DEFAULT_RESUME_POLICY
    assert normalized['launcher'] == [str(Path(sys.executable).absolute())]
    assert 'native_home' not in normalized['resume_policy']
    assert normalized['worker_fragment_policy'] == dict(context='same_session', max_context_windows=1)
    assert normalized['node_budget_policy'] == {role: dict(max_observed_tokens=1000,
        max_seconds=25) for role in 'HWAE'}
    for key in COMMON_POLICIES:
        assert key not in normalized and key not in original
    assert normalized['search_policy'] is original['search_policy'] is None
    assert adapter().complete_authorship is True


@pytest.mark.parametrize('entry', ['normalize', 'prepare', 'controller'])
@pytest.mark.parametrize('mode', ['omitted', None])
def test_implied_readonly_local_refuses_before_git_plugins_paths_or_publication(tmp_path, monkeypatch, entry, mode):
    value = default_options(tmp_path)
    value['native_options'] = dict(OPTIONS)
    value['launcher'] = None  # Public static launcher default is a pure datum.
    if mode is None:
        value['experiment_mode'] = None
    original = copy.deepcopy(value)
    def forbidden(*a, **kw): pytest.fail('Implied read-only Local reached preparation effects')
    from scidiscovery.research_loop import project_layout
    with monkeypatch.context() as guarded:
        for name in ('resolve', 'stat', 'open', 'mkdir', 'home', 'read_text'):
            guarded.setattr(Path, name, forbidden)
        guarded.setattr(os, 'open', forbidden)
        guarded.setattr(shutil, 'which', forbidden)
        guarded.setattr(subprocess, 'Popen', forbidden)
        guarded.setattr(project_layout, 'prepare', forbidden)
        guarded.setattr(experiments, 'registration', forbidden)
        guarded.setattr(experiments, 'operator_configuration', forbidden)
        guarded.setattr(c, 'write', forbidden)
        guarded.setattr(c.atomic_files, 'publish_bytes', forbidden)
        with pytest.raises(ValueError, match='Pi Local requires.*workspace-write'):
            if entry == 'controller': controller.prepare(**value)
            elif entry == 'prepare': research_config.prepare(**value)
            else: research_config.normalize(value)
    assert value == original and not value['root'].exists()


def test_static_launcher_and_explicit_external_readonly_admit_without_io(monkeypatch):
    value = settings()
    value.pop('native_launcher_argv')
    before = copy.deepcopy(value)
    def forbidden(*a, **kw): pytest.fail('Pure declaration discovered native runtime or launcher')
    for name in ('resolve', 'stat', 'open', 'home'):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(shutil, 'which', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    pi.Pi.validate_declaration(value)
    assert value == before and pi.Pi.default_launcher() == ['pi']
    # Minimal capability constructors remain valid; no preparation defaults run.
    assert backends.selected(dict(native_backend='pi', native_launcher_argv=['pi'])).complete_authorship is False


@pytest.mark.parametrize('target', ['base', 'H', 'W', 'A', 'E', 'unused_worker'])
def test_default_path_validates_all_profiles_before_native_or_plugin_io(tmp_path, monkeypatch, target):
    value = default_options(tmp_path)
    if target == 'base': value['model'] = 'bare'
    elif target == 'unused_worker': value['worker_profiles'] = {'unused': {'service_tier': 'default'}}
    else: value['role_profiles'] = {target: {'reasoning_effort': 'unsupported'}}
    def forbidden(*a, **kw): pytest.fail('Invalid default-path profile reached an effect')
    for name in ('stat', 'open', 'resolve', 'mkdir', 'home'):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(shutil, 'which', forbidden)
    monkeypatch.setattr(experiments, 'registration', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError): research_config.normalize(value)


@pytest.mark.parametrize('policy', COMMON_POLICIES)
def test_common_policy_declarations_are_purely_delegated_not_provider_validated(monkeypatch, policy):
    # Deliberately opaque to the provider; common owners alone validate grammar.
    value = settings(**{policy: {'owner_must_validate_this': True}})
    original = copy.deepcopy(value)
    backend = adapter()
    def forbidden(*a, **kw): pytest.fail('Provider attempted common scientific policy acceptance')
    for name in ('resolve', 'stat', 'open', 'home'):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(c, 'read', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    pi.Pi.validate_declaration(value)
    backend.validate_preparation(value)
    assert value == original and backend.complete_authorship is True
    assert backend.observation_capability('native_calls')['status'] == 'unsupported'
    assert 'retry_and_cache_warming' in backend.observation_capability('native_calls')['reason']


def test_common_formal_and_execution_policies_admit_but_do_not_attest_authorship(tmp_path):
    value = options(tmp_path, launcher=[sys.executable],
        formal_review_policy={'version': 1}, execution_authority_policy={'version': 1})
    value['public_root'].mkdir(); value['objective'].write_text('Unqualified mechanical declaration')
    prepared = research_config.prepare(**value, validate_only=True)
    assert prepared['formal_review_policy'] == prepared['execution_authority_policy'] == {'version': 1}
    assert prepared['experiment_mode'] == 'external'
    assert prepared['native_capabilities']['complete_native_authorship'] is True
    assert not value['root'].exists()
    value['formal_review_policy'] = None
    with pytest.raises(ValueError, match='Execution authority requires.*formal review'):
        research_config.prepare(**value, validate_only=True)
    value['formal_review_policy'] = {'version': 2}
    with pytest.raises(ValueError, match='Formal review requires'):
        research_config.prepare(**value, validate_only=True)


def test_formal_request_refuses_retained_unsupported_pi_reviewer_after_policy_admission(tmp_path, monkeypatch):
    subject = dict(producer_step=1, material={'path': 'artifacts/subject'})
    monkeypatch.setattr(formal_review, 'contract', lambda *a: {'path': 'exact-contract'})
    monkeypatch.setattr(formal_review, '_subject', lambda *a: subject)
    monkeypatch.setattr(formal_review, 'authors', lambda *a: dict(complete=True, participants=[['independent-author']]))
    monkeypatch.setattr(formal_review.configuration_updates, 'for_job', lambda *a: settings(formal_review_policy={'version': 1},
        native_capabilities=dict(complete_native_authorship=False)))
    result = formal_review._request_value(tmp_path, [subject], {'role': 'A'})
    assert result['reason'] == 'reviewer_identity_unsupported'
    auxiliary = pi.Pi.readonly_capability(settings())
    assert auxiliary['status'] == 'unsupported' and 'auxiliary readonly' in auxiliary['reason']
    assert adapter().supports_native_schema_flag is False


def test_pi_ui_describes_local_and_explicit_permissions_without_filling_them(tmp_path, monkeypatch):
    fields = {field['path']: field for field in pi.Pi.configuration_fields()}
    assert fields['permissions.workspace']['placeholder'] == 'workspace-write'
    assert fields['permissions.workspace']['required']
    assert 'Local' in fields['permissions.workspace']['description']
    assert fields['agent.model']['placeholder'] == 'provider/model-id'
    assert set(fields['agent.reasoning_effort']['choices']) == pi.THINKING_LEVELS
    assert all(fields['permissions.' + name]['required'] for name in ('workspace', 'network', 'web', 'subagents'))
    monkeypatch.setattr(configuration_form.skill_supply, 'catalog', lambda: dict(items=[], errors=[]))
    draft = configuration_form.imported(dict(agent={'backend': 'pi'}), base=tmp_path)
    before = copy.deepcopy(draft)
    rendered = configuration_form.render(draft)
    assert '默认采用 Local' in rendered and 'provider/model-id' in rendered
    assert draft == before and 'permissions' not in configuration_form.declaration(draft)


# This is the same bounded public submission-file protocol exercised by Group 2,
# executed by the real native Bash tool, not a fake transport or host-side call.
# run --script uses the existing Local compile/source-capture path before submit.
LOCAL_SCRIPT = '''import json,os,pathlib
here=pathlib.Path.cwd()
work=here.parents[1]
root=work.parents[2]
assert os.environ['HOME']=='/task-tmp'
assert not list(pathlib.Path('/task-tmp').iterdir())
for key in ('PI_CODING_AGENT_DIR','CODEX_HOME','THIN_HWA_NATIVE_HOME','PI_ROOT'):
 assert key not in os.environ,key
for path in (root/'settings.json',root/'jobs',root/'native-homes',root/'native-evidence',pathlib.Path(__AUTH__)):
 assert not path.exists(),str(path)
source=work/'code/run.py'
try:source.write_text('ILLEGAL')
except OSError:pass
else:raise AssertionError('captured source writable')
(here/'artifacts/inert.txt').write_text('inert-local-output\\n')
(here/'artifacts/result.json').write_text(json.dumps(dict(version=1,metrics=[],outputs=[dict(path='artifacts/inert.txt')])) )
print('default-pi-local-once')
'''


DEFAULT_RESEARCH = dict(declarations=[dict(action='declare', mechanism_ref='w001.inert',
    question='Does the captured inert command retain its declared output?', exploration=True)],
    mechanism_ref='w001.inert', experiment_question=dict(
        question='Does this exact captured command write the inert output once?',
        change_criterion='Retain the original output if present; report the missing output otherwise.'))
CONSULTATION_QUESTION = 'Does this retained inert output justify anything beyond a mechanical transport observation?'


def managed_bash(auth):
    script = LOCAL_SCRIPT.replace('__AUTH__', repr(str(auth)))
    client = '''import json,pathlib,time
work=pathlib.Path.cwd()
source=work/'code/run.py'
source.parent.mkdir(exist_ok=True)
source.write_text(__SCRIPT__)
runtime=work/'experiments/default-run'
runtime.mkdir(parents=True,exist_ok=True)
slot=work/'work/experiment'
def request(number,arguments,code=0):
 value=dict(submission_ref='S%03d'%number,arguments=arguments)
 temporary=slot/'native.pending'
 temporary.write_text(json.dumps(value));temporary.replace(slot/'submit.json')
 until=time.monotonic()+20
 while time.monotonic()<until:
  try:response=json.loads((slot/'response.json').read_text())
  except (FileNotFoundError,json.JSONDecodeError):time.sleep(.02);continue
  if response.get('request')==value:
   assert response['code']==code,response
   return response
  time.sleep(.02)
 raise AssertionError('existing managed execution owner did not reply')
assert request(1,['capabilities'])['code']==0
arguments=['run','--script',str(source),'--experiment-dir',str(runtime)]
refused=request(2,arguments,code=2)
assert refused['result']['accepted'] is False,refused
assert refused['result']['reason']=='worker_research_declaration_required',refused
research=work/'work/research.json'
research.write_text(json.dumps(__RESEARCH__))
arguments+=['--research',str(research)]
response=request(3,arguments)
assert response['result']['status']=='known_success',response
call=response['result']['call_id']
assert request(4,['query','--call-id',call])['result']['state']=='succeeded'
assert request(3,arguments)==response
collected=request(5,['collect','--call-id',call])
assert collected['result']['status']=='collected',collected
assert request(6,['collect','--call-id',call])['result']==collected['result']
print('native-bash-managed-local-collected-once')
'''.replace('__SCRIPT__', repr(script)).replace('__RESEARCH__', repr(DEFAULT_RESEARCH))
    return shlex.join([sys.executable, '-c', client])


@contextmanager
def managed_peer(command, final, *, repair):
    requests, errors = [], []
    stop = threading.Event()
    limit = 3 if repair else 2
    class Handler(BaseHTTPRequestHandler):
        def setup(self): self.request.settimeout(2); super().setup()
        def log_message(self, *args): pass
        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                assert 0 < size <= 256 * 1024 and self.path == '/v1/chat/completions'
                body = json.loads(self.rfile.read(size)); requests.append(body)
                assert len(requests) <= limit and body['model'] == 'exact-model'
                assert body['scid_fixture_thinking'] == 'high'
                assert self.headers.get('Authorization') == 'Bearer synthetic-not-a-credential'
                if len(requests) == 1:
                    delta = dict(role='assistant', tool_calls=[dict(index=0, id='native-managed-bash',
                        type='function', function=dict(name='bash', arguments=json.dumps(dict(command=command, timeout=25))))])
                    finish = 'tool_calls'
                else:
                    delta = dict(role='assistant', content='{malformed' if repair and len(requests) == 2 else final)
                    finish = 'stop'
                chunks = [dict(choices=[dict(index=0, delta=delta, finish_reason=None)]),
                    dict(choices=[dict(index=0, delta={}, finish_reason=finish)]),
                    dict(choices=[], usage=dict(prompt_tokens=17, completion_tokens=5, total_tokens=22,
                        prompt_tokens_details=dict(cached_tokens=7)))]
                wire = b''.join(b'data: ' + json.dumps(dict(id='default-' + str(len(requests)),
                    object='chat.completion.chunk', created=1, model='exact-model', **chunk)).encode() + b'\n\n'
                    for chunk in chunks) + b'data: [DONE]\n\n'
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(wire))); self.send_header('Connection', 'close'); self.end_headers()
                self.wfile.write(wire); self.wfile.flush(); self.close_connection = True
            except Exception as error:
                errors.append(type(error).__name__); self.close_connection = True
    server = HTTPServer(('127.0.0.1', 0), Handler); server.timeout = .05
    def serve():
        while not stop.is_set(): server.handle_request()
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    try: yield 'http://127.0.0.1:' + str(server.server_port) + '/v1', requests, errors
    finally:
        stop.set(); thread.join(timeout=3); server.server_close()
        assert not thread.is_alive()


def wire_text(content):
    return content if isinstance(content, str) else ''.join(block['text'] for block in content if block['type'] == 'text')


@pytest.mark.process_e2e
@pytest.mark.parametrize('repair', [False, True], ids=['native-bash-local', 'native-bash-local-default-repair'])
def test_installed_pi_defaults_use_actual_managed_local_and_current_collector(
        tmp_path, monkeypatch, record_property, execution_registry, repair):
    diagnostic_log_dir = os.environ.get('SCID_TEST_LOG_DIR')
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    final = ' \n' + json.dumps(dict(summary='Mechanical default activation — 原件',
        scientific_result='Inert Local process only; no scientific qualification.',
        limitations=['Finite loopback provider; no solver or real credential.'],
        implementation_selection=None, stage_intent='review', consultation_question=CONSULTATION_QUESTION,
        mechanism_findings=[dict(
            mechanism='Inert fixture', basis='observed', finding='One captured Local command completed.')]),
        ensure_ascii=False) + '\n\t'
    value = default_options(tmp_path, max_tokens=10000, max_seconds=45, max_steps=3)
    value.pop('launcher')  # Also exercise the public static default executable.
    value['public_root'].mkdir(); value['objective'].write_text('Finite inert default Local check; no scientific verdict.')
    with managed_peer(managed_bash(agent), final, repair=repair) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        prepared = research_config.prepare(**value)
        root = value['root']; work = c.workspace(root, 1)
        assert 'research_strategy_version' not in value
        assert prepared['research_strategy_version'] == 3 and prepared['experiment_mode'] == 'local'
        assert Path(prepared['native_launcher_argv'][0]).resolve() == Path(launcher)
        assert {k: prepared['resume_policy'][k] for k in pi.DEFAULT_RESUME_POLICY} == pi.DEFAULT_RESUME_POLICY
        assert 'native_home' not in prepared['resume_policy']
        assert prepared['worker_fragment_policy'] == dict(context='same_session', max_context_windows=1)
        assert prepared['node_budget_policy'] == {role: dict(max_observed_tokens=10000,
            max_seconds=45) for role in 'HWAE'}
        assert prepared['native_capabilities']['complete_native_authorship'] is True
        for key in COMMON_POLICIES:
            assert key not in prepared
        job = c.build_job(root, 1, 'W', [])
        assert job['research_strategy_version'] == 3 and job['direct_refs'] == []
        (work/'code').mkdir(exist_ok=True)  # Empty fixture slot; native Bash writes the actual source.
        originals = {path: path.read_bytes() for path in (root/'settings.json', root/'jobs/001.json', work/'science-task.json')}
        c.write(root/'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'pi_default_activation_fixture')
        result = collect_separately(root, timeout=65)
        if diagnostic_log_dir:
            retain_installed_repair_diagnostics(root,
                Path(diagnostic_log_dir) / ('default-repair' if repair else 'default-local'), result.stderr)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        assert len(requests) == (3 if repair else 2) and errors == []
    assert len(requests) == (3 if repair else 2) and errors == []
    first = execution.origin(root, 1)
    records = execution.records(root, 1)
    assert records == ([first, first/'continuations/001'] if repair else [first])
    assert not (first/'continuations/002/native-intent.json').exists()
    assert (records[-1]/'science-output.json').read_bytes() == final.encode('utf-8')
    if repair:
        assert (first/'science-output.json').read_bytes() == b'{malformed'
    expected = [dict(input_tokens=34, cached_input_tokens=14, output_tokens=10)]
    if repair: expected.append(dict(input_tokens=17, cached_input_tokens=7, output_tokens=5))
    receipts = [c.read(record/'native-outcome.json') for record in records]
    user_ids = []
    for record, receipt, debit in zip(records, receipts, expected):
        assert receipt['native_started'] and receipt['returncode'] == 0 and receipt['stop_reason'] is None
        assert receipt['terminal_observed'] and receipt['cleanup'] == 'observed_group_cleaned'
        meter = receipt['metering']
        assert meter['coverage'] == 'terminal' and meter['tokens'] == c.read(record/'usage.json') == debit
        assert {key: meter['context_windows'][key] for key in ('coverage', 'windows_started', 'reason')} == dict(
            coverage='live', windows_started=1, reason=None)
        assert meter['context_windows']['cursor']['thread_id'] == receipt['thread_id']
        assert meter['metadata_delivery']['acknowledged'] > 0 and not meter['metadata_delivery']['fault']
        assert meter['observation_capabilities']['native_calls']['status'] == 'unsupported'
        assert receipt['native_identity']['participant_scope'] == pi.PARTICIPANT_SCOPE
        assert receipt['native_identity']['controlled']['kind'] == pi.PARTICIPANT_PROOF
        assert c.file_ref(record/'science-output.json') == receipt['raw_ref']
        assert (record/'runner-process.json').is_file() and (record/'native-process.json').is_file()
        user_ids.append(meter['turn_id'])
    assert len(set(user_ids)) == len(records)
    session = Path(receipts[-1]['native_identity']['session']['path'])
    data = session.read_bytes()
    proof = receipts[-1]['native_identity']['session']
    assert proof['size_bytes'] == len(data) and proof['sha256'] == hashlib.sha256(data).hexdigest()
    rows = [json.loads(row) for row in data.splitlines()]
    users = [row for row in rows if row.get('type') == 'message' and row['message']['role'] == 'user']
    assert [row['id'] for row in users] == user_ids
    assert sum(row.get('type') == 'session' for row in rows) == 1
    assert sum(row.get('type') == 'message' and row['message']['role'] == 'toolResult' for row in rows) == 1
    if repair:
        previous = receipts[0]['native_identity']['session']
        assert hashlib.sha256(data[:previous['size_bytes']]).hexdigest() == previous['sha256']
        intent = c.read(records[1]/'native-intent.json')
        assert intent['continuation_kind'] == 'repair' and intent['session'] == previous
        assert intent['budget_slice']['previous_tokens'] == 44
        assert intent['output_repair']['source_attempt'] == 0
        assert not any(key in intent for key in ('work_repair', 'execution_wake', 'automatic_recovery'))
        assert native.OUTPUT_REPAIR_INSTRUCTION in wire_text(requests[2]['messages'][-1]['content'])
    usage = native.usage(root)
    assert usage['observed_tokens'] == usage['accounted_tokens'] == (66 if repair else 44)
    assert usage['unknown_steps'] == []
    assert [len([m for m in body['messages'] if m['role'] == 'user']) for body in requests] == ([1, 1, 2] if repair else [1, 1])
    tools = [m for m in requests[1]['messages'] if m['role'] == 'tool']
    assert len(tools) == 1 and 'native-bash-managed-local-collected-once' in wire_text(tools[0]['content'])
    config = c.read(first/'experiment-config.json'); managed = experiments.Records(config)
    calls = managed.calls(); assert len(calls) == 1
    call = calls[0]; accepted = managed.accepted(call.name)
    assert accepted['code_version']['logical_root'] == 'code'
    assert config['research_strategy_version'] == 3
    binding = worker_research.check_binding(accepted['mechanism_binding'], config['scope'])
    assert binding['mechanism_ref'] == DEFAULT_RESEARCH['mechanism_ref']
    assert binding['research_binding']['question'] == DEFAULT_RESEARCH['declarations'][0]['question']
    assert binding['research_binding']['experiment_question'] == DEFAULT_RESEARCH['experiment_question']
    assert binding['research_binding']['exploration'] is True
    declaration = Path(binding['source_ref']['path'])
    assert declaration == Path(config['record_dir'])/'research/000001.json'
    assert c.read(declaration)['revision'] == 1 and not declaration.stat().st_mode & 0o222
    assert c.read(work/'work/research.json') == DEFAULT_RESEARCH
    assert c.read(call/'completed.json')['status'] == 'known_success'
    assert (call/'stdout.raw').read_bytes() == b'default-pi-local-once\n'
    assert not (first/'formal-review.json').exists()
    assert (first/'result-identity.json').is_file()
    c.validate(work)
    for name in ('auth.json', 'settings.json', 'models.json'):
        assert not (root/'native-homes/001'/name).exists()
    for path, original in originals.items(): assert path.read_bytes() == original
    assert session.read_bytes() == data


@pytest.mark.process_e2e
def test_installed_pi_default_required_metadata_loss_never_repairs_or_replays(
        tmp_path, monkeypatch, record_property, execution_registry):
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    wrapper = tmp_path/'pi-default-metadata-loss'
    wrapper.write_text(FAULT_WRAPPER.replace('__LAUNCHER__', repr(launcher)).replace('__FAULT__', repr('metadata-loss')))
    wrapper.chmod(0o700)
    final = json.dumps(dict(summary='Mechanical metadata failure',
        scientific_result='No scientific qualification.', limitations=['Required terminal metadata was lost.'],
        implementation_selection=None, stage_intent='review', consultation_question=CONSULTATION_QUESTION,
        mechanism_findings=[dict(
            mechanism='Inert fixture', basis='observed', finding='Original Local execution retained.')]))
    value = default_options(tmp_path, launcher=[str(wrapper)], max_tokens=10000, max_seconds=45, max_steps=3)
    value['public_root'].mkdir(); value['objective'].write_text('No replay after required default metadata failure.')
    with managed_peer(managed_bash(agent), final, repair=False) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        prepared = research_config.prepare(**value)
        assert 'research_strategy_version' not in value
        assert prepared['research_strategy_version'] == 3 and prepared['experiment_mode'] == 'local'
        assert prepared['resume_policy']['output_repair'] is True
        assert prepared['worker_fragment_policy']['max_context_windows'] == 1
        root = value['root']; c.build_job(root, 1, 'W', [])
        (c.workspace(root, 1)/'code').mkdir(exist_ok=True)
        original_job = (root/'jobs/001.json').read_bytes()
        c.write(root/'host-started.json', budget.clock_stamp()); budget.transition(root, 'advance', 'pi_default_metadata_fault_fixture')
        result = collect_separately(root, timeout=65)
        assert result.returncode != 0 and len(requests) == 2 and errors == []
    assert len(requests) == 2 and errors == []
    record = execution.origin(root, 1); outcome = c.read(record/'native-outcome.json')
    assert outcome['cleanup'] == 'observed_group_cleaned'
    assert outcome['metering']['metadata_delivery']['fault']
    assert c.read(record/'usage.json') == dict(input_tokens=34, cached_input_tokens=14, output_tokens=10)
    assert len(execution.records(root, 1)) == 1
    assert not (record/'output-repair.json').exists() and not (record/'result-identity.json').exists()
    assert not (record/'continuations/001/native-intent.json').exists()
    session = root/'native-homes/001/sessions/pi-session.jsonl'
    rows = [json.loads(row) for row in session.read_bytes().splitlines()]
    users = [row for row in rows if row.get('type') == 'message' and row['message']['role'] == 'user']
    assert len(users) == 1
    # Control diagnoses the public observation snapshot, not a Pi collector's
    # private .turn layout. A bound turn after submission never grants replay.
    assert outcome['metering']['turn_id'] == users[0]['id']
    assert outcome['technical_diagnostic'] == dict(phase='metadata_delivery',
        code='native_metadata_unavailable',
        original_call_state='terminal' if outcome['terminal_observed'] else 'unknown',
        thread_bound=True, turn_bound=True,
        next_action='inspect_terminal_original' if outcome['terminal_observed'] else 'reconcile_original_without_replay')
    assert sum(row.get('type') == 'message' and row['message']['role'] == 'toolResult' for row in rows) == 1
    assert any(row.get('type') == 'message' and row['message']['role'] == 'assistant'
        and wire_text(row['message']['content']) == final for row in rows)
    config = c.read(record/'experiment-config.json')
    assert config['research_strategy_version'] == 3
    managed = experiments.Records(config)
    calls = managed.calls(); assert len(calls) == 1
    binding = worker_research.check_binding(managed.accepted(calls[0].name)['mechanism_binding'], config['scope'])
    assert binding['mechanism_ref'] == DEFAULT_RESEARCH['mechanism_ref']
    assert binding['research_binding']['experiment_question'] == DEFAULT_RESEARCH['experiment_question']
    assert c.read(calls[0]/'completed.json')['status'] == 'known_success'
    assert (calls[0]/'stdout.raw').read_bytes() == b'default-pi-local-once\n'
    assert (root/'jobs/001.json').read_bytes() == original_job
