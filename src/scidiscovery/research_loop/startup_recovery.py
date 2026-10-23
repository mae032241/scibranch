"""One explicit first model submission after independently reconciled startup failure.

This is host engineering evidence, not scientific qualification or a retry policy.
Original native receipts, missing meters and consumed budgets are never rewritten.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import os
from pathlib import Path
import shutil

from . import configuration_updates
from . import contracts as c, profiles

_review_preflight = ContextVar('native_startup_review_preflight',default=None)
_collection_preflight = ContextVar('native_startup_collection_preflight',default=None)


def path(root, step):
    return Path(root)/'host-recovery/native-startup'/f'{step:03d}.json'


def _ref(file):
    file = c.regular(Path(file).absolute())
    return dict(path=str(file),size_bytes=file.stat().st_size)


def _check(ref):
    file = c.regular(Path(ref['path']))
    if file.resolve() != file or file.stat().st_size != ref['size_bytes'] or c.file_ref(file) != ref:
        raise ValueError('Native startup recovery original or implementation changed')
    return file


def _origin(root, step):
    return c.host_record(c.workspace(root,step))


def _original_paths(root, step):
    record = _origin(root,step)
    job_path = c.regular(Path(root)/'jobs'/f'{step:03d}.json')
    job = c.read(job_path)
    files = dict(settings=configuration_updates.settings_path(root, job),job=job_path,
        intent=record/'native-intent.json',outcome=record/'native-outcome.json',
        stream=record/'native.jsonl',stderr=record/'native.stderr',observation=record/'usage-observation.json',
        runner=record/'runner-process.json',native=record/'native-process.json',
        schema=record/'science-result.schema.json',assignment=c.workspace(root,step)/'science-task.json')
    return files


def _originals(root, step):
    return {name:_ref(file) for name,file in _original_paths(root,step).items()}


def _bound_settings(originals):
    # Revision originals contain the policy under settings; their whole-file
    # proof stays bound in the permit, exactly like the initial settings file.
    value = c.read(Path(originals['settings']['path']))
    job = c.read(Path(originals['job']['path']))
    return value['settings'] if job.get('configuration_revision') is not None else value


def _native_attribution(report):
    """Retained evidence about the original source flow, not the current tool."""
    value=report.get('native_implementation_attribution')
    if (not isinstance(value,dict) or not isinstance(value.get('postcall_ref'),dict) or set(value['postcall_ref'])!={'path'}
            or not isinstance(value.get('limitations'),list) or not value['limitations']
            or any(not isinstance(item,str) or not item.strip() for item in value['limitations'])):
        raise ValueError('Original native source attribution and its limitations are required')
    return value


def _evidence(root, step, report, originals, *, initial=False):
    _native_attribution(report)
    settings,job = _bound_settings(originals),c.read(Path(originals['job']['path']))
    subject = report.get('subject') or {}
    judgment = report.get('engineering_judgment') or {}
    sources = (report.get('positive_source_flow_evidence') or {}).get('sources') or []
    if (report.get('kind') != 'native_startup_engineering_reconciliation'
            or subject.get('goal_id') != settings['goal_id'] or subject.get('step') != step
            or subject.get('attempt') != 0 or subject.get('role') != 'A'
            or subject.get('task_ref') != dict(path=originals['job']['path'])
            or judgment.get('provider_submission') != 'provider_not_submitted'
            or type(judgment.get('provider_model_tokens')) is not int or judgment['provider_model_tokens'] != 0
            or not judgment.get('product_meter_remains_unknown') or not sources
            or not isinstance(report.get('author'),str) or not report['author'].strip()):
        raise ValueError('Positive independent engineering non-submission evidence is required')
    for name in ('settings','job','intent','outcome','stream','stderr','usage_observation'):
        own = 'observation' if name == 'usage_observation' else name
        if report.get('originals',{}).get(name) != originals[own]:
            raise ValueError('Engineering reconciliation does not bind the exact startup originals')
    for source in sources:
        if (not isinstance(source,dict) or not isinstance(source.get('url'),str) or not source['url']
                or not isinstance(source.get('finding'),str) or not source['finding'].strip()):
            raise ValueError('Positive source-flow evidence and its limitations must be retained')
    outcome = c.read(Path(originals['outcome']['path']))
    intent = c.read(Path(originals['intent']['path']))
    record = _origin(root,step)
    if (job['role'] != 'A' or job.get('formal_review') or 'attempt' in intent
            or type(outcome.get('returncode')) is not int or outcome['returncode'] == 0
            or outcome.get('stop_reason') or outcome.get('cleanup') != 'observed_group_cleaned'
            or outcome.get('thread_id') is not None or outcome.get('metering',{}).get('has_observation')
            or any((record/name).exists() or (record/name).is_symlink() for name in
                ('usage.json','science-output.json','result.pending.json'))
            or initial and (record/'result-identity.json').exists()):
        raise ValueError('Recovery requires the exact cleaned startup failure without submitted science')
    marker = (intent.get('budget_slice') or {}).get('final_return')
    if not isinstance(marker,dict) or marker != subject.get('final_return'):
        raise ValueError('Startup recovery requires the original single final-A claim')
    claim_path = Path(root)/'control-ledger'/f'{marker["seq"]:06d}.json'
    claim = c.read(c.regular(claim_path))
    if (c.record_ref(claim_path) != marker['record'] or claim.get('kind') != 'final_a_return'
            or claim.get('step') != step or claim['anchor']['task_ref'] != dict(path=originals['job']['path'])
            or intent['budget_slice'] != dict(claim['anchor'],final_return=marker)):
        raise ValueError('Startup recovery changed the exact final-A authorization')
    profiles.validate_intent(settings,job,intent)
    seconds = outcome.get('budget_slice',{}).get('advance_seconds')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Startup recovery must retain consumed native time')
    return marker


def validate(root, step, value=None):
    """Validate permission identity and scope without reading execution results."""
    root = Path(root).resolve()
    value = c.read(c.regular(path(root,step))) if value is None else value
    if (value.get('version') != 1 or value.get('scope') != 'first_model_submission_after_reconciled_startup'
            or value.get('root') != str(root) or value.get('step') != step or value.get('successor_attempt') != 1
            or not isinstance(value.get('authorization_statement'),str) or not value['authorization_statement'].strip()):
        raise ValueError('One explicit exact native startup successor authorization is required')
    originals = value['originals']
    expected = _original_paths(root,step)
    if set(originals) != set(expected):raise ValueError('Startup successor original bindings changed')
    for name,ref in originals.items():
        if (not isinstance(ref,dict) or set(ref) != {'path','size_bytes'}
                or ref['path'] != str(expected[name]) or type(ref['size_bytes']) is not int or ref['size_bytes'] < 0):
            raise ValueError('Startup successor original declaration changed')
        if name in {'settings','job','intent','schema','assignment'}:_check(ref)
    report = c.read(_check(value['reconciliation']))
    if value['native_program']!=_native_attribution(report)['postcall_ref']:
        raise ValueError('Native source attribution differs from its accepted engineering evidence')
    intent = c.read(Path(originals['intent']['path']))
    marker = (intent.get('budget_slice') or {}).get('final_return')
    if (not isinstance(marker,dict) or set(marker) != {'seq','record'} or type(marker['seq']) is not int
            or marker['seq'] <= 0):raise ValueError('Startup successor lost its original final claim')
    claim_path = root/'control-ledger'/f'{marker["seq"]:06d}.json'
    claim = c.read(c.regular(claim_path))
    job = c.read(Path(originals['job']['path']))
    if (c.record_ref(claim_path) != marker['record'] or claim.get('kind') != 'final_a_return'
            or claim.get('step') != step or job['role'] != 'A' or job.get('formal_review') is not None
            or 'attempt' in intent or intent.get('task_ref') != dict(path=originals['job']['path'])
            or claim['anchor']['task_ref'] != dict(path=originals['job']['path'])
            or intent['budget_slice'] != dict(claim['anchor'],final_return=marker)):
        raise ValueError('Startup successor changed its original final claim')
    profiles.validate_intent(_bound_settings(originals),job,intent)
    if value.get('skill_binding') != job.get('skill_binding'):
        raise ValueError('Startup successor changed the frozen Skill binding')
    if value.get('final_return') != marker:
        raise ValueError('Startup successor changed its original final claim')
    if value['native_profile'] != profiles.for_job(_bound_settings(originals),job):
        raise ValueError('Startup successor changed the frozen A profile')
    _review_binding(root,value)
    home = Path(value['native_home'])
    if home.resolve() != home or not home.is_dir():raise ValueError('Exact startup successor native home is unavailable')
    config = home/'config.toml'
    current_config = _ref(config) if config.exists() else None
    if value.get('native_home_config') != current_config:
        raise ValueError('Original native home configuration changed')
    return value


def reconciled_original(root, step, value=None, *, initial=False):
    """Prove the original non-submission; a permission alone is not usage evidence."""
    value = validate(root,step,value)
    originals = value['originals']
    for ref in originals.values():_check(ref)
    if originals != _originals(root,step):raise ValueError('Startup successor original bindings changed')
    marker = _evidence(Path(root),step,c.read(_check(value['reconciliation'])),originals,initial=initial)
    if marker != value['final_return']:raise ValueError('Reconciliation changed its original final claim')
    return value


def require_eligibility(root, step, value=None, *, initial=False):
    """Check retained evidence and inputs only before a new scientific dispatch."""
    root = Path(root)
    value = reconciled_original(root,step,value,initial=initial)
    verify_program(value)
    with _review_scope(root,value):
        job = c.read(root/'jobs'/f'{step:03d}.json')
        c._validate_inputs(root,c.workspace(root,step),job)
    return value


def _review_binding(root, value, current=None):
    """Exact historical review semantics; no verdict or new review is granted."""
    from . import formal_review
    policy = _bound_settings(value['originals']).get('formal_review_policy')
    if policy is None:
        if value.get('review_contract') is not None:raise ValueError('Startup recovery invented a review contract')
        return None
    ref = value.get('review_contract')
    expected = {**policy['contract'],'path':str(Path(root)/policy['contract']['path'])}
    if ref != expected:raise ValueError('Startup recovery changed its original review contract reference')
    frozen = c.read(_check(ref))
    current = formal_review._contract() if current is None else current
    if {key:item for key,item in frozen.items() if key!='implementation'} != {key:item for key,item in current.items() if key!='implementation'}:
        raise ValueError('Startup recovery cannot change scientific review semantics')
    return policy['contract']


@contextmanager
def _review_scope(root, value):
    validate(root,value['step'],value)
    token = _review_preflight.set((str(Path(root).resolve()),value))
    try:yield
    finally:_review_preflight.reset(token)


def review_contract(root, policy, current):
    """Only an exact validated startup authorization may read the old contract."""
    root = Path(root).resolve()
    active = _review_preflight.get()
    if active is not None and active[0] == str(root):
        value = validate(root,active[1]['step'],active[1])
        return _review_binding(root,value,current) == policy['contract']
    for permit in (root/'host-recovery/native-startup').glob('[0-9]*.json'):
        if not permit.stem.isdigit():continue
        value = validate(root,int(permit.stem))
        if _review_binding(root,value,current) == policy['contract']:return True
    return False


def original_proof(root, step, value=None):
    value = validate(root,step,value)
    return {'native-intent.json':dict(path=value['originals']['intent']['path']),
            'native-outcome.json':dict(path=value['originals']['outcome']['path']), 'usage.json':None,
            'startup_reconciliation':c.record_ref(path(root,step))}


def validate_intent(root, job, intent):
    value = validate(root,job['step'])
    original = c.read(Path(value['originals']['intent']['path']))
    if (intent.get('attempt') != 1 or intent.get('continuation_kind') != 'startup_successor'
            or intent.get('context_mode') != 'new_context' or 'session' in intent
            or intent.get('startup_recovery') != _ref(path(root,job['step']))
            or intent.get('step') != job['step'] or intent.get('task_ref') != dict(path=value['originals']['job']['path'])
            or intent.get('root_intent_ref') != dict(path=value['originals']['intent']['path'])
            or intent.get('previous') != original_proof(root,job['step'],value)
            or intent.get('budget_slice') != original['budget_slice']
            or intent.get('native_profile') != value['native_profile']
            or intent.get('skill_binding') != value.get('skill_binding')):
        raise ValueError('Startup successor changed its one exact fresh-context execution')
    return value


def verify_program(value):
    c.regular(Path(value['native_program']['path']))
    for ref in value['launcher_files']:c.regular(Path(ref['path']))


def _admission(root, step, value):
    """Recheck original permissions and finite caps at the new intent boundary."""
    from . import budget,correction
    with _review_scope(root,value):
        if (root/'pause.request').exists():raise ValueError('Original pause remains effective')
        if correction.gate(root,step-1) is not None:raise ValueError('Original correction pause requires its exact release')
        if budget.execution_blocked(root):raise ValueError('Original live or unknown external effects block startup recovery')
        limits = budget.snapshot(root)
        original = c.read(Path(value['originals']['intent']['path']))
        elapsed = limits['advance_seconds']-original['budget_slice']['advance_seconds']
        if elapsed >= original['budget_slice']['limits']['max_seconds'] or budget.used_steps(root)+1 > budget.effective_max_steps(root):
            raise ValueError('Original finite final-A time or control-step authorization is exhausted')


def start(root, step, *, reconciliation, reconciliation_ref, native_home, authorization_statement):
    """Explicit supported entry; subsequent calls collect only the same attempt."""
    from . import budget,execution,native,runtime
    from .controller import _writer,_inactive_on_error,_run
    root = Path(root).resolve()
    with _writer(root),_inactive_on_error(root,'native_startup_successor_stopped'):
        configuration_updates.for_step(root, step)
        permit_path = path(root,step)
        if permit_path.exists():
            value = validate(root,step)
            if (value['reconciliation_input'] != reconciliation_ref or value['native_home'] != str(Path(native_home).resolve())
                    or value['authorization_statement'] != authorization_statement):
                raise ValueError('The accepted startup successor authorization cannot be replaced')
        else:
            source = c.regular(Path(reconciliation).resolve())
            if c.file_ref(source) != reconciliation_ref:raise ValueError('Exact engineering reconciliation reference is required')
            originals = _originals(root,step)
            report = c.read(source)
            marker = _evidence(root,step,report,originals,initial=True)
            for name in ('runner','native'):
                identity = c.read(Path(originals[name]['path']))
                if runtime.process_matches(identity,dict(path=originals['job']['path'])):
                    raise ValueError('Original startup writer is still live')
            settings = configuration_updates.for_step(root, step)
            program = shutil.which(settings['native_launcher_argv'][0])
            if program is None:raise ValueError('Exact native launcher is unavailable')
            if Path(program).resolve().name == 'codex.js':
                from .native_execution import _native_program
                program = _native_program(settings['native_launcher_argv'])
            program_ref = dict(path=str(Path(program).resolve()))
            home = Path(native_home).resolve()
            if not home.is_dir() or not os.access(home,os.W_OK):raise ValueError('Explicit original writable native home is required')
            evidence = permit_path.parent/f'{step:03d}-reconciliation.json'
            evidence.parent.mkdir(parents=True,exist_ok=True)
            if evidence.exists() and evidence.read_bytes() != source.read_bytes():raise ValueError('Original recovery evidence cannot be replaced')
            if not evidence.exists():
                with evidence.open('xb') as stream:stream.write(source.read_bytes())
                evidence.chmod(0o400)
            original_job = c.read(Path(originals['job']['path']))
            value = dict(version=1,scope='first_model_submission_after_reconciled_startup',root=str(root),step=step,
                successor_attempt=1,originals=originals,reconciliation=_ref(evidence),reconciliation_input=reconciliation_ref,final_return=marker,
                native_profile=profiles.for_job(settings,original_job),
                **({'skill_binding':original_job['skill_binding']} if 'skill_binding' in original_job else {}),
                native_home=str(home),
                review_contract=({**settings['formal_review_policy']['contract'],
                    'path':str(root/settings['formal_review_policy']['contract']['path'])} if settings.get('formal_review_policy') is not None else None),
                native_home_config=_ref(home/'config.toml') if (home/'config.toml').exists() else None,
                native_program=program_ref,launcher_files=[dict(path=str(Path(arg).resolve())) for arg in settings['native_launcher_argv'][1:] if Path(arg).is_file()],
                authorization_statement=authorization_statement,
                trust='Explicit acceptance of exact independent engineering evidence and its disclosed limitations; no scientific qualification.')
            require_eligibility(root,step,value,initial=True)
            _admission(root,step,value)
            c.write(permit_path,value);permit_path.chmod(0o400)
        job = c.read(root/'jobs'/f'{step:03d}.json')
        record = _origin(root,step)/'continuations/001'
        if not (record/'native-intent.json').exists():
            if record.exists() and any(record.iterdir()):raise ValueError('Unaccepted startup successor contains effects; preserved without replay')
            require_eligibility(root,step,value)
            _admission(root,step,value)
            original = c.read(Path(value['originals']['intent']['path']))
            intent = dict(step=step,attempt=1,continuation_kind='startup_successor',context_mode='new_context',
                task_ref=dict(path=value['originals']['job']['path']),native_profile=value['native_profile'],
                **({'skill_binding':value['skill_binding']} if 'skill_binding' in value else {}),
                root_intent_ref=dict(path=value['originals']['intent']['path']),previous=original_proof(root,step,value),
                startup_recovery=_ref(permit_path),budget_slice=original['budget_slice'])
            validate_intent(root,job,intent)
            budget.transition(root,'advance','explicit_native_startup_successor')
            record.mkdir(parents=True,exist_ok=True)
            with (record/'native-intent.json').open('x') as stream:
                json.dump(intent,stream);stream.flush();os.fsync(stream.fileno())
            runtime.start(root,step,1)
        else:
            validate_intent(root,job,c.read(record/'native-intent.json'))
        return _run(root)


def collection_path(root, step):
    return path(root,step).with_name(f'{step:03d}-collection.json')


def _collection_report(root, original, report):
    """Independent positive source-order evidence, never missing-files inference."""
    subject = report.get('subject') or {}
    judgment = report.get('engineering_judgment') or {}
    flow = report.get('positive_source_flow_evidence') or {}
    step = original['step']
    if (report.get('kind') != 'delivery_pre_dispatch_engineering_reconciliation'
            or not isinstance(report.get('author'),str) or not report['author'].strip()
            or judgment.get('delivery_dispatch') != 'not_dispatched'
            or not flow.get('mandatory_failure_chain') or not flow.get('first_external_dispatch')
            or not flow.get('conclusion') or subject.get('step') != step or subject.get('native_attempt') != 1
            or subject.get('goal_id') != _bound_settings(original['originals'])['goal_id']
            or subject.get('final_return') != original['final_return']):
        raise ValueError('Exact positive independent delivery non-dispatch evidence is required')
    refs = report['originals']
    for ref in refs.values():_check(ref)
    action = c.read(_check(refs['action_intent']))
    record = _origin(root,step);current = record/'continuations/001'
    expected = dict(settings=Path(root)/'settings.json',settings_identity=Path(root)/'settings-identity.json',
        job=Path(root)/'jobs'/f'{step:03d}.json',startup_permit=path(root,step),
        final_claim=Path(root)/'control-ledger'/f'{original["final_return"]["seq"]:06d}.json',
        action_intent=Path(root)/'control-ledger'/f'{action["seq"]:06d}.json',
        origin_scientific_seal=record/'result-identity.json',successor_intent=current/'native-intent.json',
        successor_outcome=current/'native-outcome.json',successor_usage=current/'usage.json',
        successor_scientific_raw=current/'science-output.json')
    if any(refs.get(name) != _ref(file) for name,file in expected.items()):
        raise ValueError('Delivery reconciliation changed its exact original receipts')
    if report.get('accepted_implementation',[]) != original.get('implementation',[]):
        raise ValueError('Delivery reconciliation changed the accepted source-order implementation')
    prefix = report.get('control_ledger_prefix') or []
    if len(prefix) != action['seq'] or any(ref != _ref(Path(root)/'control-ledger'/f'{number:06d}.json')
            for number,ref in enumerate(prefix,1)):
        raise ValueError('Delivery reconciliation changed its exact accepted ledger prefix')
    decision = dict(step=step,job_ref=dict(path=refs['job']['path']),raw_ref=refs['successor_scientific_raw'],
        seal_ref=dict(path=refs['origin_scientific_seal']['path']))
    if (action.get('kind') != 'action_intent' or action.get('action_kind') != 'submit'
            or action.get('final_return') != original['final_return'] or action.get('decision') != decision
            or action['binding']['decision'] != decision or action['binding']['operation_id'] != action['operation_id']
            or any(subject.get(key) != action[key] for key in ('action_id','action_kind','operation_id','decision'))
            or subject.get('action_seq') != action['seq']):
        raise ValueError('Only the exact accepted original delivery operation can be collected')
    outcome,meter = c.read(current/'native-outcome.json'),c.read(current/'usage.json')
    if (outcome.get('returncode') != 0 or outcome.get('stop_reason') or outcome.get('cleanup') != 'observed_group_cleaned'
            or outcome.get('raw_ref') != decision['raw_ref']
            or outcome.get('metering',{}).get('coverage') != 'terminal'
            or any(type(meter.get(key)) is not int or meter[key] < 0 for key in ('input_tokens','cached_input_tokens','output_tokens'))):
        raise ValueError('Collection requires the existing sealed, known-terminal final A')
    return action


def _collection_binding(root, original):
    """Direct compatibility reads only; this must never call validate()."""
    root = Path(root).resolve()
    pending = _collection_preflight.get()
    receipt = (pending[1] if pending is not None and pending[0] == str(root) and pending[1]['step'] == original['step']
        else c.read(c.regular(collection_path(root,original['step']))))
    if (receipt.get('version') != 1 or receipt.get('scope') != 'sealed_final_a_original_delivery_only'
            or receipt.get('root') != str(root) or receipt.get('step') != original['step']
            or receipt.get('startup_permit') != _ref(path(root,original['step']))
            or not isinstance(receipt.get('authorization_statement'),str) or not receipt['authorization_statement'].strip()):
        raise ValueError('Exact collector-only authorization is required')
    # Authorization binds exact objects and limits only. Positive recovery and
    # scientific/result predicates are checked separately before publication.
    report = c.read(_check(receipt['reconciliation']))
    # These are grant/declaration objects, not execution-result predicates.
    for name in ('settings','settings_identity','job','startup_permit','final_claim','action_intent'):
        _check(report['originals'][name])
    if report.get('accepted_implementation',[]) != original.get('implementation',[]):
        raise ValueError('Collector changed its accepted implementation identity')
    action = c.read(_check(report['originals']['action_intent']))
    if report['originals']['action_intent'] != _ref(root/'control-ledger'/f'{action["seq"]:06d}.json'):
        raise ValueError('Collector changed its exact original action reference')
    if (action.get('kind') != 'action_intent' or action.get('action_kind') != 'submit'
            or action.get('final_return') != original['final_return']
            or action.get('decision',{}).get('step') != original['step']
            or action['decision'].get('job_ref') != dict(path=original['originals']['job']['path'])
            or action['binding'].get('decision') != action['decision'] or action['binding'].get('operation_id') != action['operation_id']
            or receipt.get('limits') != dict(max_seconds=action['max_seconds'],max_invocations=1,native_dispatches=0)
            or receipt.get('action_id') != action['action_id'] or receipt.get('operation_id') != action['operation_id']
            or receipt.get('boot_id') != c.read(Path(root)/'control-ledger'/f'{original["final_return"]["seq"]:06d}.json')['anchor']['boot_id']
            or type(receipt.get('advance_seconds')) not in (int,float) or not math.isfinite(receipt['advance_seconds'])
            or receipt['advance_seconds'] < action['advance_seconds']):
        raise ValueError('Collector changed its one original first-invocation identity or finite time anchor')
    return receipt


def unstarted_action(root, intent):
    """Only the exact temporary preflight may exclude its proven unstarted action."""
    pending = _collection_preflight.get()
    if pending is None or pending[0] != str(Path(root).resolve()) or pending[1]['action_id'] != intent['action_id']:
        raise ValueError('Original delivery is not in its exact non-dispatch preflight')
    original = c.read(path(root,pending[1]['step']))
    receipt = _collection_binding(root,original)
    if receipt['operation_id'] != intent['operation_id']:raise ValueError('Original delivery operation changed')


def collection_dispatch(root, intent):
    """The single first actual invocation keeps the original declared call cap."""
    step = intent['decision']['step']
    original = c.read(c.regular(path(root,step)))
    receipt = _collection_binding(root,original)
    if receipt['action_id'] != intent['action_id'] or receipt['operation_id'] != intent['operation_id']:
        raise ValueError('Collection cannot renew another delivery invocation')
    return receipt


def collect_delivery(root, step, *, reconciliation, reconciliation_ref, authorization_statement):
    """Host delivery only. Never enter generic run or any native dispatch path."""
    from . import budget,delivery,correction,controller
    root = Path(root).resolve()
    with controller._writer(root),controller._inactive_on_error(root,'final_delivery_collection_stopped'):
        file = collection_path(root,step)
        original = c.read(c.regular(path(root,step)))
        existed = file.exists()
        if existed:
            value = _collection_binding(root,original)
            if value['reconciliation_input'] != reconciliation_ref or value['authorization_statement'] != authorization_statement:
                raise ValueError('The collector-only authorization cannot be replaced')
        else:
            source = c.regular(Path(reconciliation).resolve())
            if c.file_ref(source) != reconciliation_ref:raise ValueError('Exact delivery reconciliation reference is required')
            action = _collection_report(root,original,c.read(source))
            evidence = file.with_name(f'{step:03d}-collection-reconciliation.json')
            if evidence.exists() and evidence.read_bytes() != source.read_bytes():raise ValueError('Original delivery evidence cannot be replaced')
            if not evidence.exists():
                with evidence.open('xb') as stream:stream.write(source.read_bytes())
                evidence.chmod(0o400)
            value = dict(version=1,scope='sealed_final_a_original_delivery_only',root=str(root),step=step,
                startup_permit=_ref(path(root,step)),reconciliation=_ref(evidence),reconciliation_input=reconciliation_ref,
                action_id=action['action_id'],operation_id=action['operation_id'],
                limits=dict(max_seconds=action['max_seconds'],max_invocations=1,native_dispatches=0),
                boot_id=c.read(root/'control-ledger'/f'{original["final_return"]["seq"]:06d}.json')['anchor']['boot_id'],
                advance_seconds=action['advance_seconds'],authorization_statement=authorization_statement,
                safety='One first actual invocation with the unchanged original finite call cap; all original global time and usage retained. No native dispatch, review qualification or new scientific allowance.')
            token = _collection_preflight.set((str(root),value))
            try:
                reconciled_original(root,step)
                job = c.read(root/'jobs'/f'{step:03d}.json')
                result = c.validate(c.workspace(root,step))
                state = budget.closeout_state(root)
                row = next(row for row in state['actions'] if row['intent']['action_id'] == action['action_id'])
                phases = root/'host-delivery/action-processes'
                if (row['outcome'] is not None or any(phases.glob(action['action_id']+'-*.json'))
                        or (root/'pause.request').exists() or correction.gate(root,step-1) is not None
                        or budget.execution_blocked(root,ignore_operation=action['operation_id'],ignore_unstarted_action=action['action_id'])
                        or delivery.binding(root,job,result) != action['binding']):
                    raise ValueError('Original delivery is no longer a known unstarted, qualified operation')
                value['advance_seconds'] = budget.snapshot(root)['advance_seconds']
                _collection_binding(root,original)
                c.write(file,value);file.chmod(0o400)
            finally:_collection_preflight.reset(token)
        reconciled_original(root,step)
        job = c.read(root/'jobs'/f'{step:03d}.json')
        result = c.validate(c.workspace(root,step))
        if not existed:
            action = _collection_report(root,original,c.read(_check(value['reconciliation'])))
            budget.transition(root,'advance','original_delivery_first_dispatch')
            reply = delivery.invoke(root,action,action['max_seconds'],
                on_process=lambda phase,identity:budget.action_process(root,action,phase,identity),collection=value)
            budget.action_outcome(root,action,reply)
        return controller._run_delivery_actions(root,job,result)


def validate_collection(root, step):
    """Validate collector authorization refs/scope/limits, separately from results."""
    return _collection_binding(Path(root).resolve(),c.read(c.regular(path(root,step))))
