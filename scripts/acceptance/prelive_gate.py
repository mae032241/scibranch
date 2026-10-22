"""Pre-live production-config full-chain gate: fake native turns, zero model calls.

Re-prepares a fresh scratch scene from the exact frozen settings of a real run
(read-only source), rebinding only paths and code identities, and drives the
actual controller, rootless launcher/bwrap namespace, registered cov calls,
candidate audit, a side-effect-free candidate catalogue and isolated delivery leaf to
a confirmed delivery of the fixture author's explicit choice.
Run with the candidate interpreter, e.g. ``venv/bin/python -I prelive_gate.py``.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time

REPO = Path(__file__).resolve().parents[2]
LAUNCHER = REPO / 'scripts/acceptance/rootless_cov_launcher.py'
MAX_VERDICT_BYTES = 64 * 1024
CHAIN = [('H', None, 'experiment'), ('W', None, 'review'), ('A', None, 'audit'), ('A', None, 'continue_w'),
         ('W', None, 'review'), ('A', None, 'finish'), ('A', 'closeout', 'deliver')]

FAKE_NATIVE = r'''import json,os,pathlib,re,subprocess,sys,uuid
args=sys.argv[1:]
if args and args[0]=='sandbox':
 # Transport fixture only: outer bwrap is real; no provider sandbox qualification.
 assert len(args)==12 and args[:2]==['sandbox','-P'] and args[3]=='-C' and args[5]=='--'
 assert args[7:9]==['-B','-I'] and pathlib.Path(args[9]).name=='receipt.py'
 assert args[10:]==['--science-runner','/opt/exp/scid-invocation.json']
 invocation=json.loads(pathlib.Path(args[11]).read_text())
 assert set(invocation)=={'runner','source','output_line','output'}
 source=pathlib.Path(invocation['source']);cwd=pathlib.Path(args[4])
 assert source.is_relative_to(cwd) and source.is_file()
 assert type(invocation['output_line']) is int and invocation['output_line']>0
 assert invocation['output']=='/task-tmp/scid-receipt/row.json'
 os.chdir(args[4]);os.execv(args[6],args[6:])
sys.stdin.read()
work=pathlib.Path(args[args.index('-C')+1]);home=pathlib.Path(os.environ['CODEX_HOME'])
task=json.loads((work/'science-task.json').read_text());role=task['role']
output=pathlib.Path(args[args.index('--output-last-message')+1]);later=output.parent.parent.name=='continuations' or home.name.startswith('context-')
assert 'resume' not in args and pathlib.Path(args[args.index('--output-schema')+1]).name=='science-result.schema.json'
thread=str(uuid.uuid4());FAULT=None  # Replaced for an explicit negative control.
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
session=home/'sessions/current.jsonl';session.parent.mkdir(parents=True,exist_ok=True)
session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
if FAULT=='a_turn_failed' and role=='A':
 print(json.dumps(dict(type='turn.failed',error=dict(message='workspace routing discovery failed'))),flush=True);raise SystemExit(1)
TRAIN="""from sklearn.linear_model import Ridge
def train(train_df,val_df):
    features=[c for c in train_df.columns if c.startswith('y')]
    targets=[c for c in train_df.columns if c.startswith('x')]
    return Ridge(alpha=ALPHA).fit(train_df[features],train_df[targets])
"""
def exp(*items,expected=0):
 result=subprocess.run([str(work/'bin/exp'),*map(str,items)],capture_output=True,text=True)
 assert (result.returncode==0)==(expected==0),json.dumps(dict(arguments=list(map(str,items)),returncode=result.returncode,
  stderr=result.stderr[-384:],reply=reply_summary(result.stdout)))
 return result
def reply_summary(text):
 try:value=json.loads(text)
 except (ValueError,TypeError):return dict(unparsed=text[:512])
 if not isinstance(value,dict):return dict(unexpected_type=type(value).__name__)
 summary={key:str(value[key])[:256] for key in ('status','note','error','call_id','returncode','cleanup_confirmed','transport_complete') if key in value}
 for key in ('runner','engineering_check'):
  if isinstance(value.get(key),dict):summary[key]={field:str(value[key][field])[:192] for field in ('status','note','error','returncode','cleanup_confirmed') if field in value[key]}
 return summary
def run(name,alpha,*choice):
 script=work/'work'/(name+'.py');script.write_text(TRAIN.replace('ALPHA',repr(alpha)))
 reply=json.loads(exp('run','--script',script,*choice).stdout);assert reply['status']=='known_success',reply_summary(json.dumps(reply))
 return reply
def ids():
 path=task['lineage']['indexes']['candidates'];rows=[];seen=set()
 assert path is not None,dict(reason='No frozen public candidate index',candidates=task['lineage'].get('candidates'))
 while path is not None:
  logical=pathlib.Path(path)
  assert not logical.is_absolute() and '..' not in logical.parts and logical.parts[0]=='inputs' and path not in seen
  seen.add(path);page=json.loads((work/logical).read_text());rows.extend(page['candidates']);path=page.get('next_page')
 assert rows,'The frozen public candidate index declares no candidate'
 return [row['id'] for row in rows]
payload=dict(summary='Synthetic pre-live '+role+' contribution',scientific_result='No scientific efficacy was measured; fake native transport.',
 limitations=['Fake native transport, not a model; public validation only.'],implementation_selection=None)
unit=task.get('question','')
if role=='H':
 payload.update(next_action='experiment',route_reason='Screen two competing mechanisms',search_plan=dict(scope='Synthetic pre-live search',
  commitments=[dict(label=label,minimal_test='Does ridge strength '+label+' change public recovery? Bounded discriminating check.',existing_ref=None) for label in ('P','Q')],
  focus=dict(label=None,action='screen')))
elif role=='W' and 'Work unit: Screen' in unit:
 first=run('screen-p',1.0,'--new-plan','--mechanism','P')
 rejected=exp('run','--script',work/'work/screen-p.py','--new-plan','--mechanism','P',expected=1)
 assert 'Other H mechanisms' in rejected.stdout+rejected.stderr,(rejected.stdout,rejected.stderr)
 (work/'work/breadth-rejection.json').write_text(json.dumps(dict(returncode=rejected.returncode)))
 second=run('screen-q',10.0,'--new-plan','--mechanism','Q')
 (work/'work/screen-calls.json').write_text(json.dumps([first['call_id'],second['call_id']]))
 payload.update(summary='Synthetic pre-live screen contribution',stage_intent='review',mechanism_findings=[dict(mechanism=label,basis='observed',
  finding='One bounded registered call completed; fake transport, adequacy not asserted.') for label in ('P','Q')])
elif role=='W':
 if not later:
  parent=ids()[0]
  first=run('improve-1',0.5,'--parent-plan',parent,'--change-intent','Lower ridge strength','--mechanism','P')
  (work/'work/improve-first.json').write_text(json.dumps(dict(call_id=first['call_id'],candidate_id=first['candidate_id'])))
  payload.update(stage_intent='continue',mechanism_findings=None)
 else:
  assert home.name.startswith('context-') and (work/'work/science-resume.json').is_file()
  prior=json.loads((work/'work/improve-first.json').read_text())
  state=json.loads(exp('query','--call-id',prior['call_id']).stdout);assert state['state']=='succeeded',state
  run('improve-2',0.25,'--parent-plan',prior['candidate_id'],'--change-intent','Continue lowering ridge strength','--mechanism','P')
  payload.update(stage_intent='review',mechanism_findings=[dict(mechanism='P',basis='observed',
   finding='Two improve fragments completed bounded registered calls; fake transport.')])
 payload['summary']='Synthetic pre-live improve contribution'
else:
 audit=task.get('candidate_audit');closeout=task.get('closeout')
 parents=[json.loads(p.read_text()) for p in sorted((work/'inputs/parents').glob('*/conclusion.json'))]
 improved=any(row.get('summary')=='Synthetic pre-live improve contribution' for row in parents)
 none=dict(search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None)
 table=(closeout or {}).get('comparison')
 if closeout is not None and audit:
  assert audit['status']=='audited' and audit['qualification']=='passed',audit
 if table is not None:
  chosen=audit['candidate_id'] if audit else ids()[-1]
  rows=[dict(candidate_id=row['candidate_id'],key_risk='Public validation only; fake transport, no target evidence.',
   decision='keep' if row['candidate_id']==chosen else 'replace') for row in table['rows']]
  # The fixture author explicitly names a candidate before qualification and delivery.
  payload.update(none,next_action='deliver',candidate_recommendation=chosen,
   candidate_comparison=dict(rows=rows,rationale='The fixture author retains this exact observed candidate.',evidence_steps=[2]),
   route_reason='Authorize delivery of the explicit original choice; control must perform its host audit')
 elif closeout is not None:
  chosen=audit['candidate_id'] if audit else ids()[-1]
  payload.update(none,next_action='deliver',candidate_recommendation=chosen,
   route_reason='Authorize the explicit public fixture index choice within stated limitations; control must validate its exact originals and perform the host audit. No scientific efficacy is asserted.')
 elif improved:
  payload.update(none,next_action='finish',candidate_recommendation=ids()[-1],route_reason='Close the bounded synthetic search')
 elif audit:
  payload.update(next_action='continue_w',candidate_recommendation=audit['candidate_id'],route_reason='Improve P locally after the audit receipt',
   search_review=dict(dispositions=[],next_focus=dict(mechanism_ref='h001.m1',action='improve')),progress_assessment=dict(verdict='progress'),
   decision_request=dict(decision='candidate',question='Does lower ridge strength improve the audited P choice?',change_criterion='A better public recovery would replace run-1.'),portfolio_review='Local P improvement has greater decision value than rescreening Q.')
 else:
  payload.update(next_action='audit',candidate_recommendation=ids()[0],route_reason='Audit the exact provisional choice',
   search_review=dict(dispositions=[dict(mechanism_ref=ref,status='screened',reason='The exact final W screened this mechanism.') for ref in ('h001.m1','h001.m2')],next_focus=None),
   progress_assessment=dict(verdict='progress'),decision_request=None,portfolio_review=None)
if role=='W' and task.get('candidate_nomination_mechanisms'):
 payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
  reason='Synthetic transport observations establish no scientifically suitable representative; all retained candidates remain available to A.')
  for row in task['candidate_nomination_mechanisms'] if row['status']!='waived'])
if isinstance(payload.get('candidate_recommendation'),str):
 payload['selection_explanation']=dict(criteria='Compare exact public fixture candidates by their declared validation and limitations, preserving original checks; no scientific qualification is asserted.',
  criteria_change=dict(status='unchanged',prior_criteria=None,changes=None,evidence=None,accepted_tradeoffs=None))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=1000,cached_input_tokens=0,output_tokens=10))),flush=True)
output.write_text(json.dumps(payload))
'''


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):h.update(chunk)
    return h.hexdigest()


def _launcher():
    spec = importlib.util.spec_from_file_location('prelive_launcher', LAUNCHER)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _map(value, maps):
    if isinstance(value, dict):return {key: _map(item, maps) for key, item in value.items()}
    if isinstance(value, list):return [_map(item, maps) for item in value]
    if isinstance(value, str):
        for old, new in maps:
            if value == old or value.startswith(old + '/'):return new + value[len(old):]
    return value


# Reproduce policy opt-ins exactly; a gate must not silently enable a strategy.
ADOPTED = ()  # Replay the exact policy, including disabled/missing policies.


# Code/launcher identities and scratch paths are the only rebound settings.
REBOUND = (('goal_id',), ('public_root',), ('native_launcher_argv',), ('history_observer', 'adapter'),
           ('history_observer', 'adapter_sha256'), ('resume_policy', 'launcher_files'),
           ('delivery_policy', 'adapter', 'adapter'), ('delivery_policy', 'adapter', 'adapter_sha256'),
           ('delivery_policy', 'adapter', 'launcher_argv'), ('delivery_policy', 'adapter', 'launcher_files'))


def normalized(settings, maps=()):
    value = json.loads(json.dumps(_map(settings, maps)))
    for path in REBOUND:
        node = value
        for key in path[:-1]:node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict) and path[-1] in node:node[path[-1]] = '<rebound>'
    return value


def differences(left, right, prefix=''):
    if isinstance(left, dict) and isinstance(right, dict):
        return [item for key in sorted(set(left) | set(right))
                for item in differences(left.get(key, '<missing>'), right.get(key, '<missing>'), prefix + '/' + key)]
    return [] if left == right else [prefix or '/']


def source_files(settings_path, config, manifest):
    files = [settings_path, settings_path.parent / 'settings-identity.json', Path(config['public_manifest'])]
    files += [Path(config['public_root']) / name for name in manifest['public_files_sha256']]
    return {str(path): sha(path) for path in files if path.is_file()}


def _source_proof(root):
    """Read exact retained declarations and globals; paths alone establish no identity."""
    from scidiscovery.research_loop import contracts as c, qualification
    root = Path(root).absolute()
    settings = c.settings(root)
    if settings.get('source_provenance') is None:
        raise ValueError('Current prelive source requires its exact source proof')
    proof = qualification._sources(root)
    if (set(proof) != {'version', 'declarations', 'globals', 'scope'}
            or proof['version'] != 1 or proof['scope'] != qualification.SCOPE
            or [row['material'] for row in proof['globals']] != settings['global_materials']):
        raise ValueError('Prelive source proof differs from its frozen global mapping')
    def original(relative, digest, size):
        path = root / relative
        if Path(relative).is_absolute() or path.resolve() != path or not path.is_relative_to(root):
            raise ValueError('Prelive retained source escaped its exact instance')
        path = c.regular(path)
        if sha(path) != digest or path.stat().st_size != size:
            raise ValueError('Prelive retained source bytes differ from their declaration')
    for row in proof['declarations']:
        if set(row) != {'origin', 'restriction', 'retained'} or row['restriction'] not in {'clear', 'restricted'}:
            raise ValueError('Prelive source declaration is malformed')
        origin, retained = row['origin'], row['retained']
        if (not Path(origin['path']).is_absolute() or '..' in Path(origin['path']).parts
                or (retained['sha256'], retained['size_bytes']) != (origin['sha256'], origin['size_bytes'])):
            raise ValueError('Prelive source identity differs from its retained original')
        original(retained['path'], retained['sha256'], retained['size_bytes'])
    for row in proof['globals']:
        origin, material = row['origin'], row['material']
        if material['sha256'] != origin['sha256']:
            raise ValueError('Prelive global mapping changed original bytes')
        original(material['source'], origin['sha256'], origin['size_bytes'])
    return proof


def source_provenance_equal(source, target, maps):
    """Compare complete verified facts after only explicit controlled path rebinding."""
    return _map(_source_proof(source), maps) == _source_proof(target)


def rebind(settings_path, launcher_path, work, scid_python, fault=None):
    """Fresh scratch inputs; the source run is read, hashed and never written."""
    from cov_execution import delivery as cov_delivery, lineage as cov_lineage
    settings = json.loads(settings_path.read_bytes());config = json.loads(launcher_path.read_bytes())
    proof = _source_proof(settings_path.parent)
    manifest = json.loads(Path(config['public_manifest']).read_bytes())
    source_root, source_public = str(settings_path.parent.absolute()), settings['public_root']
    root, public = work / 'instance', work / 'public'
    for name, digest in manifest['public_files_sha256'].items():
        target = public / name;target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(source_public) / name, target)
        if sha(target) != digest:raise ValueError('frozen_public_input_changed')
    shutil.copyfile(config['public_manifest'], work / 'public-manifest.json')
    fake = work / 'fake-codex';fake.write_text('#!' + scid_python + '\n' + FAKE_NATIVE.replace('FAULT=None', 'FAULT=' + repr(fault), 1));fake.chmod(0o555)
    host = config.get('code_mode_host') or {}
    if not (host.get('path') and Path(host['path']).is_file() and sha(host['path']) == host.get('sha256')):
        path = Path('/usr/bin/true').resolve();host = dict(path=str(path), sha256=sha(path))
    venv = str(Path(scid_python).absolute().parent.parent)
    rebound = dict(config, scene_root=str(root), public_root=str(public), public_manifest=str(work / 'public-manifest.json'),
        runs_dir=str(root / 'runs'), submission_dir=str(root / 'submission'), native=str(fake),
        code_mode_host=host, scid_python=scid_python,
        runtime_paths=list(dict.fromkeys([p for p in config['runtime_paths'] if Path(p).exists()] + [sys.base_prefix, venv])))
    (work / 'launcher.json').write_text(json.dumps(rebound, indent=2) + '\n')
    maps = [(source_root, str(root)), (source_public, str(public)), (config['runs_dir'], str(root / 'runs')),
            (config['submission_dir'], str(root / 'submission'))]
    objective = work / 'objective.md';objective.write_text(settings['objective'], encoding='utf-8')
    origins = {row['material']['path']: row['origin']['path'] for row in proof['globals']}
    global_maps = [(origins['inputs/global/objective.md'], str(objective))]
    updates = []
    for index, item in enumerate(settings['global_materials']):
        if item['path'] != 'inputs/global/objective.md':
            target = work / 'updates' / f'{index:03d}.md';target.parent.mkdir(exist_ok=True)
            shutil.copyfile(settings_path.parent / item['source'], target);updates.append(target)
            global_maps.append((origins[item['path']], str(target)))
    maps = global_maps + maps
    kwargs = {key: settings[key] for key in ('max_tokens', 'max_seconds', 'max_steps', 'model', 'reasoning_effort', 'service_tier',
        'max_output_item_bytes', 'max_output_total_bytes', 'node_budget_mode') if key in settings}
    for key in ('search_policy', 'correction_policy', 'node_budget_policy', 'role_guidance', 'candidate_audit_policy',
                'portfolio_policy', 'worker_fragment_policy', 'lineage_projection_version', 'selection_protocol',
                'handoff_authority_version', 'decision_contract_version', 'role_budget_policy', 'selection_policy', 'role_profiles'):
        kwargs[key] = settings.get(key)
    adopted = []
    restrictions = []
    for row in proof['declarations']:
        target = Path(_map(row['origin']['path'], maps))
        if target not in [objective, *updates] and not target.is_relative_to(public):
            raise ValueError('Prelive source has no explicit controlled copy binding')
        if not target.is_file() or sha(target) != row['origin']['sha256'] or target.stat().st_size != row['origin']['size_bytes']:
            raise ValueError('Prelive rebound source differs from the exact declared bytes')
        restrictions.append(dict(source=str(target), sha256=row['origin']['sha256'], restriction=row['restriction']))
    kwargs['source_restrictions'] = restrictions
    if settings.get('history_observer') is not None:
        observer = settings['history_observer']
        kwargs['history_observer'] = dict(adapter=str(Path(cov_lineage.__file__).resolve()), config=observer['config'],
            **({'contract_version': observer['contract_version']} if 'contract_version' in observer else {}))
    if settings.get('resume_policy') is not None:
        kwargs['resume_policy'] = {k: v for k, v in settings['resume_policy'].items() if k != 'launcher_files'}
    if settings.get('delivery_policy') is not None:
        policy = dict(settings['delivery_policy']);adapter = policy['adapter']
        if adapter is not None:
            module = str(Path(cov_delivery.__file__).resolve())
            policy['adapter'] = dict(adapter=module, launcher_argv=[scid_python, module, *adapter['launcher_argv'][2:]],
                config=_map(adapter['config'], maps), target=_map(adapter['target'], maps),
                inputs=[dict(label=item['label'], path=_map(item['invoked_path'], maps)) for item in adapter['inputs']],
                source_roots=_map(adapter['source_roots'], maps))
        kwargs['delivery_policy'] = policy
    launcher = [scid_python, str(LAUNCHER), '--config', str(work / 'launcher.json'), '--']
    return settings, config, manifest, maps, adopted, dict(root=root, objective=objective, public_root=public, launcher=launcher,
                                                  user_updates=updates or None, **kwargs)


def _chain(root):
    from scidiscovery.research_loop import contracts as c, execution
    rows = []
    for path in sorted((root / 'jobs').glob('*.json')):
        job = c.read(path);step = job['step']
        if not (execution.origin(root, step) / 'result-identity.json').exists():
            rows.append(dict(step=step, role=job['role'], purpose=job.get('purpose'), sealed=False));continue
        result = c.validate(c.workspace(root, step))
        rows.append(dict(step=step, role=job['role'], purpose=job.get('purpose'), sealed=True,
            route=result.get('next_action') or result.get('stage_intent'), candidate=result.get('candidate_recommendation'),
            focus=(job.get('search') or {}).get('focus'), fragments=len(execution.records(root, step))))
    return rows


def _checks(root, chain, result):
    from scidiscovery.research_loop import budget, candidate_audit, contracts as c, delivery, execution, native, cli
    from scidiscovery.research_loop.experiment_client import record_directory
    checks = {}
    checks['delivery_confirmed'] = result.get('status') == 'delivery_confirmed'
    checks['chain_shape'] = [(row['role'], row['purpose'], row.get('route')) for row in chain] == CHAIN
    if not checks['chain_shape']:return checks
    screen, improve = chain[1]['step'], chain[4]['step']
    calls = [c.read(path) for path in sorted((record_directory(root, screen) / 'calls').glob('*/accepted.json'))]
    checks['screen_registered_calls_two_mechanisms'] = (len(calls) == 2 and
        sorted(row['mechanism_binding']['mechanism_ref'] for row in calls) == ['h001.m1', 'h001.m2'])
    checks['breadth_admission_rejected_repeat'] = (c.workspace(root, screen) / 'work/breadth-rejection.json').is_file()
    checks['screen_focus'] = chain[1]['focus'] == dict(mechanism_ref='h001.m1', action='screen')
    records = execution.records(root, improve)
    intents = [c.read(record / 'science-output.json').get('stage_intent') for record in records]
    threads = {c.read(record / 'native-outcome.json')['thread_id'] for record in records}
    checks['improve_two_new_context_fragments'] = (intents == ['continue', 'review'] and len(threads) == 2
        and c.read(records[1] / 'native-intent.json').get('context_mode') == 'new_context')
    checks['improve_focus'] = chain[4]['focus'] == dict(mechanism_ref='h001.m1', action='improve')
    receipts = [candidate_audit._outcome(root, chain[2]['step'])]
    checks['candidate_audits_audited'] = all(row is not None and row['result']['status'] == 'audited' for row in receipts)
    final = chain[6]
    job = c.read(root / 'jobs' / f'{final["step"]:03d}.json')
    checks['one_final_author'] = job.get('final_only') is True and candidate_audit._outcome(root, final['step']) is None
    actions = budget.closeout_state(root)['actions']
    checks['original_delivery_actions'] = (len(actions) == 2
        and [row['intent']['action_kind'] for row in actions] == ['submit', 'confirm']
        and len({row['intent']['operation_id'] for row in actions}) == 1
        and all(row['intent']['binding'] == actions[0]['intent']['binding']
            and row['intent']['decision']['step'] == final['step']
            and row['outcome'] is not None and row['outcome']['result']['status'] == 'confirmed' for row in actions))
    process_checks = []
    if checks['original_delivery_actions']:
        for action, phase in ((actions[0], 'audit'), (actions[0], 'promote'), (actions[1], 'confirm')):
            intent = action['intent']
            path = root / 'host-delivery/action-processes' / (intent['action_id'] + '-' + phase + '.json')
            process = c.read(path) if path.is_file() else {}
            process_checks.append((process.get('action_id'), process.get('operation_id'), process.get('phase')) == (
                intent['action_id'], intent['operation_id'], phase)
                and (process.get('identity') or {}).get('task_sha256') == intent['operation_id'])
        final_leaf = delivery.leaf_refs(root, actions[0]['intent']['operation_id'])
        final_phases = {c.read(root / item['path'])['phase'] for item in final_leaf}
        checks['final_host_leaf_output'] = {'audit', 'promote', 'confirm'} <= final_phases
    else:
        checks['final_host_leaf_output'] = False
    checks['final_host_audit_original'] = bool(process_checks) and all(process_checks)
    confirmed = result.get('last_confirmed') or {}
    screen = c.read(root / 'host-delivery/selection/screen.json') if (root / 'host-delivery/selection/screen.json').is_file() else None
    if c.settings(root).get('selection_policy') is not None:
        chosen = final['candidate']
        rows = (screen or {}).get('rows', [])
        checks['delivered_closeout_choice'] = confirmed.get('candidate_id') == chosen
        checks['selection_catalogue_frozen'] = bool(rows) and 'default' not in screen
        checks['selection_catalogue_no_domain_calls'] = not list((root / 'host-delivery/selection').rglob('*.intent.json'))
        comparison = c.read(execution.raw(root, final['step'])).get('candidate_comparison') or {}
        compared = comparison.get('rows', [])
        known = {row['candidate_id'] for row in rows}
        checks['selection_comparison_sealed'] = (bool(comparison.get('rationale')) and bool(comparison.get('evidence_steps'))
            and all(row['candidate_id'] in known for row in compared)
            and len({row['candidate_id'] for row in compared}) == len(compared)
            and [row['candidate_id'] for row in compared if row['decision'] == 'keep'] == [chosen])
    else:
        checks['selection_disabled_preserved'] = screen is None
        checks['delivered_closeout_choice'] = confirmed.get('candidate_id') == final['candidate']
    submitted = root / 'submission/model.pkl'
    checks['submission_bytes_match'] = submitted.is_file() and sha(submitted) == confirmed.get('artifact_sha256')
    checks['usage_known'] = native.usage(root)['unknown_steps'] == []
    leaf = root / 'host-delivery/leaf-output'
    phases = {path.name.split('-')[1] for path in leaf.glob('*.json')} if leaf.is_dir() else set()
    checks['leaf_output_preserved'] = {'resolve', 'audit', 'promote', 'confirm'} <= phases
    checks['audit_receipts_reference_leaf_output'] = all(row and row.get('leaf_output') for row in receipts)
    checks['leaf_output_not_in_workspaces'] = not any(b'leaf-output' in path.read_bytes()
        for path in (root / 'project/nodes').rglob('*') if path.is_file() and not path.is_symlink() and path.stat().st_size < 1 << 20)
    try:
        status = cli.status(root)
        checks['status_readable'] = status['closeout'] is not None and len(status['nodes']) == len(chain)
    except Exception:
        checks['status_readable'] = False
    return checks


FAULTS = ('a_turn_failed',)


def _fault_checks(root, chain, result, fault):
    """Failed native work is a retained stop, never a seal, success or replay."""
    from scidiscovery.research_loop import execution, native
    last = chain[-1] if chain else {}
    step = last.get('step')
    return dict(native_stopped=result.get('status') == 'native_stopped',
        failed_a_unsealed=last.get('role') == 'A' and last.get('sealed') is False,
        failed_usage_unknown=native.usage(root)['unknown_steps'] == [step],
        no_replay=step is not None and len(execution.records(root, step)) == 1 and len(list((root / 'jobs').glob('*.json'))) == step,
        no_delivery=not (root / 'submission/model.pkl').exists())


def run_gate(settings_path, launcher_path, work, *, scid_python=None, fault=None):
    """Return a bounded verdict; the caller supplies a fresh scratch directory."""
    started = time.monotonic()
    settings_path, launcher_path = Path(settings_path).absolute(), Path(launcher_path).absolute()
    work = Path(work).absolute()
    if work.exists():raise ValueError('Gate requires a fresh scratch directory; never reuse a scene')
    work.mkdir(parents=True);work = work.resolve()
    scid_python = str(Path(scid_python or sys.executable).absolute())
    from scidiscovery.research_loop import controller, native
    from scidiscovery.research_loop.protocol import CURRENT
    settings = json.loads(settings_path.read_bytes());config = json.loads(launcher_path.read_bytes())
    manifest = json.loads(Path(config['public_manifest']).read_bytes())
    before = source_files(settings_path, config, manifest)
    verdict = dict(passed=False, models_called=0, model_requests=0, settings=str(settings_path),
                   settings_sha256=sha(settings_path), source_protocol=settings.get('handoff_protocol'), current_protocol=CURRENT,
                   fault=fault, scope='Zero-model production-config chain; not real model behavior, target reward or remote TCAD.')
    from scidiscovery import research_loop
    installed = {path.name: sha(path) for path in sorted(Path(research_loop.__file__).parent.glob('*.py'))}
    # Binds the verdict to the exact installed code a live run would import.
    verdict['installed_modules_sha256'] = hashlib.sha256(json.dumps(installed, sort_keys=True).encode()).hexdigest()
    verdict['repo_source_mismatch'] = sorted(name for name, digest in installed.items()
        if not (REPO / 'src/scidiscovery/research_loop' / name).is_file() or sha(REPO / 'src/scidiscovery/research_loop' / name) != digest)[:32]
    checks = verdict['checks'] = {}
    identity = settings_path.parent / 'settings-identity.json'
    checks['source_settings_identity'] = identity.is_file() and json.loads(identity.read_bytes()).get('sha256') == verdict['settings_sha256']
    checks['frozen_protocol_current'] = settings.get('handoff_protocol') == CURRENT
    # Informational: the candidate launcher/adapters are what a new live run would use.
    verdict['launcher_matches_source_run'] = all(Path(p).is_file() and sha(p) == sha(LAUNCHER) for p in settings['native_launcher_argv']
                                                if Path(p).name.endswith('launcher.py'))
    try:
        _launcher().load(launcher_path)  # Read-only source preflight: whitelist, manifest, binaries.
        checks['source_launcher_preflight'] = True
    except (ValueError, OSError, KeyError) as error:
        checks['source_launcher_preflight'] = False;verdict['preflight_error'] = str(error)[:200]
    if not all(checks.values()):
        verdict['seconds'] = round(time.monotonic() - started, 1)
        return verdict  # Fail fast: a live run from this source could not start or would not be current.
    try:
        _, _, _, maps, adopted, kwargs = rebind(settings_path, launcher_path, work, scid_python, fault)
        controller.prepare(**kwargs)
        root = kwargs['root']
        rebound_settings = json.loads((root / 'settings.json').read_bytes())
        checks['source_provenance_equal_after_rebind'] = source_provenance_equal(settings_path.parent, root, maps)
        expected = normalized(settings, maps)
        if checks['source_provenance_equal_after_rebind']:
            # A proof hash includes its explicit source paths. Replace only after
            # independently verifying every original, restriction and mapping.
            expected['source_provenance'] = rebound_settings['source_provenance']
        diff = differences(expected, normalized(rebound_settings))
        verdict['adopted_defaults'] = ['/' + key for key in adopted]
        diff = [path for path in diff if path not in verdict['adopted_defaults']]
        checks['settings_equal_except_rebound'] = not diff
        verdict['settings_differences'] = diff[:32];verdict['rebound'] = ['/'.join(path) for path in REBOUND]
        if diff or not checks['source_provenance_equal_after_rebind']:
            raise ValueError('Prelive source rebinding changed frozen configuration or source facts; no native work launched')
        result = controller.run(root)
        chain = _chain(root)
        verdict.update(status=result.get('status'), reason=str(result.get('reason', ''))[:400], chain=chain[:16],
                       usage=native.usage(root))
        checks.update(_checks(root, chain, result) if fault is None else _fault_checks(root, chain, result, fault))
    except Exception as error:  # A gate failure is a verdict, never a retry.
        verdict['error'] = f'{type(error).__name__}: {str(error)[:400]}'
        checks['completed_without_exception'] = False
    checks['source_unchanged'] = source_files(settings_path, config, manifest) == before
    verdict['seconds'] = round(time.monotonic() - started, 1)
    verdict['passed'] = all(checks.values())  # With a fault: the negative control behaved as required.
    return verdict


def bounded(verdict):
    data = json.dumps(verdict, ensure_ascii=False, indent=1, default=str)
    while len(data.encode()) > MAX_VERDICT_BYTES and verdict.get('chain'):
        verdict['chain'] = verdict['chain'][:-1];verdict['chain_truncated'] = True
        data = json.dumps(verdict, ensure_ascii=False, indent=1, default=str)
    return data + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, required=True, help='Frozen settings.json of the latest real run (read-only)')
    parser.add_argument('--launcher-config', type=Path, help='Its launcher.json; defaults to the instance parent directory')
    parser.add_argument('--work', type=Path, required=True, help='Fresh scratch directory')
    parser.add_argument('--output', type=Path, required=True, help='Bounded JSON verdict path')
    parser.add_argument('--fault', choices=FAULTS, help='Negative control only; its pass never qualifies a live run')
    args = parser.parse_args()
    launcher = args.launcher_config or args.settings.absolute().parent.parent / 'launcher.json'
    verdict = run_gate(args.settings, launcher, args.work, fault=args.fault)
    args.output.write_text(bounded(verdict))
    print(json.dumps(dict(passed=verdict['passed'], fault=args.fault, status=verdict.get('status'), models_called=0,
                          failed=[name for name, ok in verdict['checks'].items() if not ok])))
    return 0 if verdict['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
