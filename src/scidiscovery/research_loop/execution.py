"""One immutable execution-reference seam for initial and continued native work."""
from pathlib import Path

from . import configuration_updates
from . import contracts as c


def origin(root, step):
    return c.host_record(c.workspace(root, step))


def proof(record):
    names = ['native-intent.json', 'native-outcome.json', 'usage.json']
    if not (record / 'native-outcome.json').exists() and (record / 'interruption.json').exists():
        names = ['native-intent.json', 'interruption.json', 'runner-process.json',
                 'native-process.json', 'usage.json']
    if (record / 'checkpoint.json').exists():
        names.append('checkpoint.json')
    if (record / 'stage-end.json').exists():
        names.append('stage-end.json')
    return {name: c.record_ref(c.regular(record / name)) for name in names
            if name != 'usage.json' or (record/name).exists()}


def _skill_intent(root, step, intent):
    if ((intent.get('automatic_recovery') or {}).get('format_fallback') is not None
            and intent.get('continuation_kind') != 'automatic_recovery'):
        raise ValueError('Native format fallback requires a validated recovery intent')
    if 'skill_binding' not in intent and c.settings(Path(root)).get('skills') is None:
        return
    settings = configuration_updates.for_step(root, step)
    if settings.get('skills') is not None or 'skill_binding' in intent:
        from . import profiles
        profiles.validate_intent(settings, c.read(c.regular(Path(root) / 'jobs' / f'{step:03d}.json')), intent)


def accepted_intents(root, step):
    """Enumerate accepted identities without traversing their execution results."""
    root = Path(root)
    first = origin(root,step)
    task = c.record_ref(c.regular(root/'jobs'/f'{step:03d}.json'))
    previous = None
    if (first/'native-intent.json').exists():
        from . import accepted_budget
        if accepted_budget.native_attempt(root,step,first) is None:
            raise ValueError('Original native intent has not been accepted')
        value = c.read(c.regular(first/'native-intent.json'))
        if not isinstance(value, dict):
            raise ValueError('Original accepted native intent must be an object')
        _skill_intent(root, step, value)
        if (value.get('task_ref') != task or 'attempt' in value
                or 'step' in value and (type(value['step']) is not int or value['step'] != step)):
            raise ValueError('Original accepted native intent identity changed')
        previous = first
        yield first,value
    directory = first/'continuations'
    if not directory.exists():return
    if directory.resolve() != directory:raise ValueError('Native continuation directory changed')
    accepted = 0
    for number,path in enumerate(sorted(directory.iterdir()),1):
        if path.name != f'{number:03d}' or path.resolve() != path or not path.is_dir():
            raise ValueError('Accepted native continuation namespace changed')
        file = path/'native-intent.json'
        if not file.exists():
            if any(path.iterdir()):raise ValueError('Unaccepted continuation has unexpected execution facts')
            continue
        from . import accepted_budget
        if accepted_budget.native_attempt(root,step,path) is None:
            raise ValueError('Original native intent has not been accepted')
        value = c.read(c.regular(file))
        if not isinstance(value, dict):
            raise ValueError('Accepted native continuation intent must be an object')
        _skill_intent(root, step, value)
        if (previous is None or number != accepted+1 or type(value.get('attempt')) is not int
                or value['attempt'] != number or value.get('task_ref') != task
                or 'step' in value and (type(value['step']) is not int or value['step'] != step)
                or value.get('root_intent_ref') != c.record_ref(c.regular(first/'native-intent.json'))
                or (value.get('previous') or {}).get('native-intent.json') != c.record_ref(c.regular(previous/'native-intent.json'))):
            raise ValueError('Accepted native continuation identity changed')
        accepted += 1
        previous = path
        yield path,value


def records(root, step):
    """Read accepted attempt identities; acceptance does not recur on a read."""
    first=origin(root,step)
    accepted=[path for path,_ in accepted_intents(root,step)]
    return accepted or [first]


def validate_records(root, step):
    """Full original-chain acceptance, owned by current inventory admission."""
    root = Path(root)
    first = origin(root, step)
    result = [first]
    directory = first / 'continuations'
    if (first / 'native-intent.json').exists():
        _skill_intent(root, step, c.read(c.regular(first / 'native-intent.json')))
    if not directory.exists():return result
    if directory.resolve() != directory:raise ValueError('Native continuation directory changed')
    settings = configuration_updates.for_step(root, step)
    repair_limit = (settings.get('resume_policy') or {}).get('max_attempts', 0)
    automatic = (settings.get('resume_policy') or {}).get('automatic_recovery')
    if automatic is not None:
        from . import native_recovery
        automatic = native_recovery.policy(automatic)
    automatic_limit = automatic['max_continues'] + automatic['max_retries'] if automatic and automatic['enabled'] else 0
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    from . import checkpoint
    fragments = checkpoint.enabled(root, job)
    from . import budget
    author_permit=root/'host-recovery/author-closeout'/f'{step:03d}.json'
    limit = budget.historical_step_limit(root) - 1 if fragments else max(repair_limit,int(author_permit.exists()))
    limit += automatic_limit
    # Reattaching an interrupted author does not spend a scientific retry.
    limit += sum(1 for path in directory.iterdir() if path.is_dir()
        and (path/'native-intent.json').exists()
        and c.read(c.regular(path/'native-intent.json')).get('continuation_kind') == 'reentry')
    from . import author_closeout
    successor=author_closeout.successor_path(root,step)
    verified_authors=None
    if successor.exists():
        successor_ref=author_closeout._ref(root,successor)
        value=c.read(author_closeout._check(root,successor_ref))
        author_closeout.require_successor_eligibility(root,value)
        original=c.read(author_closeout._check(root,value['original_permit']))
        # One full validation per invocation; each intent still checks its exact
        # accepted proof, job, compact facts and retained execution projection.
        verified_authors={1:(value['original_permit'],original,original),2:(successor_ref,value,original)}
        limit=max(limit,2)  # Only the exact operator-authorized author successor.
    elif author_permit.exists():
        original=c.read(c.regular(author_permit))
        author_closeout.require_original_eligibility(root,original)
        verified_authors={1:(author_closeout._ref(root,author_permit),original,original)}
    repairs = 0
    task = c.record_ref(c.regular(root / 'jobs' / f'{step:03d}.json'))
    original_intent = c.read(c.regular(first / 'native-intent.json'))
    if original_intent.get('task_ref') != task:
        raise ValueError('Original native task identity changed')
    if (original_intent.get('budget_slice') or {}).get('final_return') is not None:
        budget.final_a_return(root, job, original_intent)
    for number, path in enumerate(sorted(directory.iterdir()), 1):
        if path.name != f'{number:03d}' or path.resolve() != path or not path.is_dir() or number > limit:
            raise ValueError('Native continuation identity or sequence changed')
        if not (path / 'native-intent.json').exists():
            if any(path.iterdir()):raise ValueError('Unaccepted continuation has unexpected execution facts')
            continue
        intent = c.read(c.regular(path / 'native-intent.json'))
        _skill_intent(root, step, intent)
        output_repair=intent.get('continuation_kind')=='repair' and isinstance(intent.get('output_repair'),dict) and 'work_repair' not in intent
        format_retry=(intent.get('continuation_kind')=='automatic_recovery'
            and (intent.get('automatic_recovery') or {}).get('format_fallback') is True)
        if (original_intent.get('budget_slice') or {}).get('final_return') is not None and intent.get('continuation_kind') != 'reentry' and not (output_repair or format_retry):
            raise ValueError('Final A return cannot acquire another native attempt')
        if (output_repair or format_retry) and (original_intent.get('budget_slice') or {}).get('final_return') is not None and (intent.get('budget_slice') or {}).get('final_return') != original_intent['budget_slice']['final_return']:
            raise ValueError('Final A output repair must retain its original completion authorization')
        if (intent.get('budget_slice') or {}).get('final_return') is not None:
            budget.final_a_return(root, job, intent)
        if (number != len(result) or intent.get('attempt') != number or intent.get('task_ref') != task
                or intent.get('previous') != proof(result[-1])
                or intent.get('root_intent_ref') != c.record_ref(c.regular(first / 'native-intent.json'))):
            raise ValueError('Native continuation chain changed')
        kind = intent.get('continuation_kind', 'repair')
        from . import continuation
        wake_authorized = kind == 'execution_wake' and continuation.wake_allowed(settings.get('resume_policy'))
        if kind == 'execution_wake' and not wake_authorized:
            raise ValueError('Execution wake differs from its frozen authorization')
        if ((settings.get('resume_policy') or {}).get('output_only') and kind in {'repair','fragment','execution_wake'}
                and not output_repair and not wake_authorized):
            raise ValueError('Output-only policy cannot authorize scientific continuation')
        if kind not in {'repair', 'fragment', 'execution_wake','author_closeout','reentry','automatic_recovery'} or kind == 'fragment' and not fragments:
            raise ValueError('Native continuation kind differs from its frozen policy')
        if kind == 'automatic_recovery':
            continuation.validate_automatic(root, step, intent, prior_record=result[-1],
                prior_intents=[c.read(c.regular(item / 'native-intent.json')) for item in result])
        if kind == 'reentry':
            continuation.validate_reentry(root, job, intent, result[-1])
        if kind=='author_closeout':
            if verified_authors is not None:
                author_closeout.require_intent_eligibility(root,job,intent,verified=verified_authors[number])
            else:
                author_closeout.require_intent_eligibility(root,job,intent)
        if kind in {'repair','execution_wake'}:
            repairs += 1
            if repairs > repair_limit:
                raise ValueError('Native repair attempts exceed their original finite authorization')
        if kind == 'fragment':
            prior = result[-1] / 'checkpoint.json'
            expected = dict(path=prior.relative_to(root).as_posix())
            if intent.get('fragment_checkpoint') != expected:
                raise ValueError('Fragment continuation changed its exact checkpoint')
        result.append(path)
    return result


def _reference(chain):
    """Build a reference from this call's fully verified chain, not a cache."""
    if len(chain) == 1 and not (chain[0] / 'stage-end.json').exists():return None
    return dict(attempt=len(chain) - 1, chain=[proof(path) for path in chain])


def reference(root, step):
    return _reference(records(root, step))


def record(root, step, attempt=None):
    chain = records(root, step)
    if attempt is not None:
        if type(attempt) is not int or not 0 <= attempt < len(chain):
            raise ValueError('Unknown exact native attempt')
        return chain[attempt]
    seal = origin(root, step) / 'result-identity.json'
    if seal.exists() and c.read(c.regular(seal)).get('execution_ref') != _reference(chain):
        raise ValueError('Sealed native execution reference changed')
    current = chain[-1]
    return current


def raw(root, step):
    from . import originals
    if originals.inherited(root, step):
        return originals.source_raw(originals.node(root, step))
    return record(root, step) / 'science-output.json'


def identity(root, step, attempt=None):
    path = record(root, step, attempt)
    return c.record_ref(path / 'native-intent.json') if path != origin(root, step) else c.record_ref(root / 'jobs' / f'{step:03d}.json')


def home(root, step, attempt=0):
    """Read the accepted native home; rotation never changes the scientific job."""
    root = Path(root)
    current = record(root, step, attempt)
    intent = c.read(c.regular(current / 'native-intent.json'))
    base = root / 'native-homes' / f'{step:03d}'
    policy=configuration_updates.for_step(root, step).get('resume_policy') or {}
    inherited=Path(policy['native_home']) if policy.get('output_only') and policy.get('native_home') else None
    selected = Path(intent.get('native_home', intent.get('session', {}).get('home', str(inherited or base))))
    if (selected.resolve() != selected or selected != base and selected != inherited and
            (selected.parent != base or not selected.name.startswith('context-')
             or not selected.name[8:].isdigit())):
        raise ValueError('Native home differs from its exact stage namespace')
    return selected


def schema_transport(root, step, attempt=0):
    """One accepted negotiation stays with this exact scientific task lineage."""
    chain=records(root,step)
    if type(attempt) is not int or not 0 <= attempt < len(chain):
        raise ValueError('Unknown exact native schema transport attempt')
    return 'prompt' if any((c.read(c.regular(path/'native-intent.json')).get('automatic_recovery') or {}).get('format_fallback') is True
        for path in chain[:attempt+1]) else None


def _terminal(root, step, chain, outcome):
    """Explicit independent audit of the complete retained chain."""
    accept_current_terminal(root,step,chain,outcome)
    from . import continuation
    continuation.validate_repair_chain(root,step,chain)
    continuation.validate_fragment_chain(root,step,chain)
    for number,path in enumerate(chain[1:],1):
        intent=c.read(path/'native-intent.json')
        if intent.get('continuation_kind')=='execution_wake':
            continuation.validate_wake(root,step,intent,chain[number-1])


def accept_current_terminal(root, step, chain, outcome):
    """A terminal continuation cannot rebind its accepted intent before seal."""
    current = chain[-1]
    binding = c.record_ref(c.regular(current / 'native-intent.json'))
    intent = c.read(current / 'native-intent.json')
    process_boot = None
    for name in ('runner-process.json', 'native-process.json'):
        path = c.regular(current / name)
        receipt = c.read(path)
        if (c.record_ref(path) != outcome.get('execution_processes', {}).get(name)
                or receipt.get('task_ref') != binding
                or not isinstance(receipt.get('boot_id'), str) or not receipt['boot_id']
                or process_boot is not None and receipt['boot_id'] != process_boot
                or any(type(receipt.get(key)) is not int or receipt[key] <= 0
                       for key in ('pid', 'start_ticks', 'pgid', 'sid', 'ppid'))):
            raise ValueError('Terminal continuation differs from its accepted process identity')
        process_boot = receipt['boot_id']
    from . import budget
    budget._attempt_consumption(root, c.read(root / 'jobs' / f'{step:03d}.json'), chain)
    author_turn=False
    for number, path in enumerate(chain[1:],1):
        intent=c.read(path/'native-intent.json')
        if intent.get('continuation_kind')=='author_closeout':author_turn=True
    if author_turn:
        # records() already validated every author intent, permit, projection,
        # call inventory and code digest in this very invocation. Check the one
        # current final response once; no verdict survives this record() call.
        science=c.science_payload(current/'science-output.json','W',configuration_updates.for_step(root, step)['handoff_protocol'])
        if science.get('stage_intent')=='continue':raise ValueError('Author closeout cannot reopen exploration')
