"""One immutable execution-reference seam for initial and continued native work."""
from pathlib import Path

from . import configuration_updates
from . import contracts as c


def origin(root, step):
    return c.host_record(c.workspace(root, step))


def proof(record):
    names = ['native-intent.json', 'native-outcome.json', 'usage.json']
    if (record / 'checkpoint.json').exists():
        names.append('checkpoint.json')
    if (record / 'stage-end.json').exists():
        names.append('stage-end.json')
    if not (record/'usage.json').exists() and record.parent.name == 'native-evidence':
        from . import startup_recovery
        if startup_recovery.path(record.parents[1],int(record.name)).exists():
            return startup_recovery.original_proof(record.parents[1],int(record.name))
    return {name: c.digest(c.regular(record / name)) for name in names
            if name != 'usage.json' or (record/name).exists()}


def accepted_intents(root, step):
    """Enumerate accepted identities without traversing their execution results."""
    root = Path(root)
    first = origin(root,step)
    task = c.digest(c.regular(root/'jobs'/f'{step:03d}.json'))
    previous = None
    if (first/'native-intent.json').exists():
        value = c.read(c.regular(first/'native-intent.json'))
        if (value.get('task_sha256') != task or 'attempt' in value
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
        value = c.read(c.regular(file))
        if (previous is None or number != accepted+1 or type(value.get('attempt')) is not int
                or value['attempt'] != number or value.get('task_sha256') != task
                or 'step' in value and (type(value['step']) is not int or value['step'] != step)
                or value.get('root_intent_sha256') != c.digest(c.regular(first/'native-intent.json'))
                or (value.get('previous') or {}).get('native-intent.json') != c.digest(c.regular(previous/'native-intent.json'))):
            raise ValueError('Accepted native continuation identity changed')
        accepted += 1
        previous = path
        yield path,value


def records(root, step):
    root = Path(root)
    first = origin(root, step)
    result = [first]
    directory = first / 'continuations'
    if not directory.exists():return result
    if directory.resolve() != directory:raise ValueError('Native continuation directory changed')
    settings = configuration_updates.for_step(root, step)
    repair_limit = (settings.get('resume_policy') or {}).get('max_attempts', 0)
    job = c.read(c.regular(root / 'jobs' / f'{step:03d}.json'))
    from . import checkpoint
    fragments = checkpoint.enabled(root, job)
    from . import budget
    author_permit=root/'host-recovery/author-closeout'/f'{step:03d}.json'
    limit = budget.historical_step_limit(root) - 1 if fragments else max(repair_limit,int(author_permit.exists()))
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
    from . import startup_recovery
    startup = startup_recovery.path(root,step).exists()
    if startup:
        startup_recovery.reconciled_original(root,step)
        limit=max(limit,1)
    repairs = 0
    task = c.digest(c.regular(root / 'jobs' / f'{step:03d}.json'))
    original_intent = c.read(c.regular(first / 'native-intent.json'))
    if original_intent.get('task_sha256') != task:
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
        output_repair=intent.get('continuation_kind')=='repair' and isinstance(intent.get('output_repair'),dict) and 'work_repair' not in intent
        if (original_intent.get('budget_slice') or {}).get('final_return') is not None and intent.get('continuation_kind') != 'startup_successor' and not output_repair:
            raise ValueError('Final A return cannot acquire another native attempt')
        if output_repair and (original_intent.get('budget_slice') or {}).get('final_return') is not None and (intent.get('budget_slice') or {}).get('final_return') != original_intent['budget_slice']['final_return']:
            raise ValueError('Final A output repair must retain its original completion authorization')
        if (intent.get('budget_slice') or {}).get('final_return') is not None:
            budget.final_a_return(root, job, intent)
        if (number != len(result) or intent.get('attempt') != number or intent.get('task_sha256') != task
                or intent.get('previous') != proof(result[-1])
                or intent.get('root_intent_sha256') != c.digest(c.regular(first / 'native-intent.json'))):
            raise ValueError('Native continuation chain changed')
        kind = intent.get('continuation_kind', 'repair')
        if ((settings.get('resume_policy') or {}).get('output_only') and kind in {'repair','fragment','execution_wake'}
                and not output_repair):
            raise ValueError('Output-only policy cannot authorize scientific continuation')
        if kind not in {'repair', 'fragment', 'execution_wake','author_closeout','startup_successor'} or kind == 'fragment' and not fragments:
            raise ValueError('Native continuation kind differs from its frozen policy')
        if kind == 'startup_successor':
            if not startup or number != 1:raise ValueError('Only one exact startup successor is authorized')
            startup_recovery.validate_intent(root,job,intent)
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
            expected = dict(path=prior.relative_to(root).as_posix(), sha256=c.digest(c.regular(prior)))
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
    if len(chain) > 1 and (current / 'native-outcome.json').exists():
        outcome = c.read(c.regular(current / 'native-outcome.json'))
        accepted=None
        if outcome.get('returncode') == 0 and outcome.get('stop_reason')=='author_closeout_token_limit':
            from . import author_closeout
            accepted=author_closeout.accepted_output(root,step,current,c.read(current/'native-intent.json'),outcome)
        if outcome.get('returncode') == 0 and (not outcome.get('stop_reason') or accepted is not None):
            _terminal(root, step, chain, outcome)
    return current


def raw(root, step):
    from . import originals
    if originals.inherited(root, step):
        return originals.source_raw(originals.node(root, step))
    return record(root, step) / 'science-output.json'


def identity(root, step, attempt=None):
    path = record(root, step, attempt)
    return c.digest(path / 'native-intent.json') if path != origin(root, step) else c.digest(root / 'jobs' / f'{step:03d}.json')


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


def _terminal(root, step, chain, outcome):
    """A terminal continuation cannot rebind its accepted intent before seal."""
    current = chain[-1]
    binding = c.digest(c.regular(current / 'native-intent.json'))
    intent = c.read(current / 'native-intent.json')
    for name in ('runner-process.json', 'native-process.json'):
        path = c.regular(current / name)
        receipt = c.read(path)
        if (c.digest(path) != outcome.get('execution_processes', {}).get(name)
                or receipt.get('task_sha256') != binding
                or receipt.get('boot_id') != intent['budget_slice']['boot_id']
                or any(type(receipt.get(key)) is not int or receipt[key] <= 0
                       for key in ('pid', 'start_ticks', 'pgid', 'sid', 'ppid'))):
            raise ValueError('Terminal continuation differs from its accepted process identity')
    from . import budget
    budget._attempt_consumption(root, c.read(root / 'jobs' / f'{step:03d}.json'), chain)
    from . import continuation
    continuation.validate_repair_chain(root, step, chain)
    continuation.validate_fragment_chain(root, step, chain)
    author_turn=False
    for number, path in enumerate(chain[1:],1):
        intent=c.read(path/'native-intent.json')
        if intent.get('continuation_kind')=='execution_wake':continuation.validate_wake(root,step,intent,chain[number-1])
        if intent.get('continuation_kind')=='author_closeout':author_turn=True
    if author_turn:
        # records() already validated every author intent, permit, projection,
        # call inventory and code digest in this very invocation. Check the one
        # current final response once; no verdict survives this record() call.
        science=c.science_payload(current/'science-output.json','W',configuration_updates.for_step(root, step)['handoff_protocol'])
        if science.get('stage_intent')=='continue':raise ValueError('Author closeout cannot reopen exploration')
