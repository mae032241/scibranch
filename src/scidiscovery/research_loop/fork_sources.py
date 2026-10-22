"""Compile a bounded, exact source closure for a new scientific fork.

Only immutable input originals and their declared control proofs are retained.
Creating or launching the child belongs to fork_service and the controller.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import errno
import hashlib
import json
import fcntl
import os
import stat
import tempfile
from pathlib import Path

from scidiscovery import archive_io as files
from . import originals as o

MAX_ENTRIES = 65536
MAX_BYTES = 4 * 1024**3
MAX_ANCESTRY = 16
SOURCE_LOCATORS = frozenset({'source_paths', 'source_root'})
_guarded = ContextVar('fork_source_read_guards', default=frozenset())
REFERENCE_MEMBERS = frozenset({
    'files', 'execution_recoveries', 'execution_recovery_materials', 'audit_receipts', 'adjacent_audit',
    'materials', 'selected_inputs', 'material', 'retained', 'globals', 'declarations', 'edges', 'seal',
    'source_provenance', 'input_provenance', 'observations', 'native_authorship', 'attempts', 'intent',
    'outcome', 'header', 'producer_seal', 'subject', 'requester', 'contract', 'request', 'review',
    'unavailable', 'source', 'fault', 'audit', 'receipt', 'proofs', 'original', 'originals', 'inputs',
    'compiler', 'job', 'task', 'author', 'compiled_implementations', 'input_sources', 'accepted_ref',
    'snapshot_files', 'managed_calls', 'stopped_original', 'delivery_sources',
    'source_nodes', 'input_selection', 'implementation', 'accepted', 'operation', 'preparation',
    'execution_authority', 'configuration', 'original_permit', 'completed_output_acceptance',
    'correction_events', 'sources', 'evidence', 'native_intent', 'capability', 'author_input', 'output', 'client_binding',
})


def _json(path):
    path = files.check_path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Fork control original exceeds its finite JSON parse bound')
    return json.loads(path.read_bytes())


def _key(identity, path, proof):
    return o.identity_key(identity) + ':' + o.relative(path) + ':' + proof['sha256'] + ':' + str(proof['size_bytes'])


def _control_document(path, *, context=None, document=None):
    """Recognize host document types; a JSON suffix never types scientific bytes."""
    parts = Path(path).parts
    name = parts[-1]
    if document is not None and Path(document).name == 'compiler.json' and context in {
            'author', 'capability', 'capability_source', 'output'}:
        return False
    if parts[0] == 'native-evidence':
        if len(parts) == 5 and parts[2] == 'compiled-implementations':
            return name == 'compiler.json' or (name.startswith('author-client-') and name.endswith('.json'))
        record = len(parts) == 3 or (len(parts) == 5 and parts[2] == 'continuations' and parts[3].isdecimal())
        return record and name in {'result-identity.json', 'native-authorship.json',
            'input-provenance.json', 'provenance.json', 'observation.json', 'formal-review.json',
            'experiment-config.json', 'pending-execution-set.json'}
    if name == 'accepted.json' and context == 'accepted' and document is not None:
        return (document.startswith(('host-history/observations/', 'host-history/execution-recoveries/'))
            or document.endswith('/pending-execution-set.json'))
    return name.endswith('.json') and path.startswith(('source-provenance/',
        'host-history/observations/', 'host-history/execution-recoveries/',
        'host-recovery/author-closeout/', 'host-recovery/author-context/',
        'host-delivery/resolved/', 'host-delivery/preparation/', 'host-delivery/selection/',
        'execution-authority/', 'host-control/configuration/', 'host-control/guidance/'))


def _kind(job):
    if job.get('formal_review'):
        return 'formal_review'
    if job.get('engineering_repair'):
        return 'engineering_repair'
    if job.get('final_only') or job.get('final_report'):
        return 'final'
    return 'normal'


def _accept_lock(root, job, records):
    from . import execution
    for record, _ in execution.accepted_intents(root, job['step']):
        config_path = record / 'experiment-config.json'
        if config_path.exists():
            admission = _json(config_path).get('admission') or {}
            if admission.get('mode') == 'soft':
                directory = files.check_path(Path(admission['directory']))
                if not directory.is_relative_to(root):
                    raise ValueError('Managed admission lock escaped its original instance')
                return directory / 'accept.lock'
    return records / 'accept.lock'


def _parent_state(root):
    """Read retained completion/effects; never create a source-side lock."""
    from . import budget, contracts as c, execution, experiments, experiment_client
    blocked = []
    if not (root / 'host-run.lock').exists() and not (root / 'instance-owner.json').exists():
        blocked.append(dict(code='source_guard_unavailable', path='host-run.lock'))
    jobs = sorted((root / 'jobs').glob('*.json'), key=lambda path: int(path.stem))
    complete = False
    if jobs:
        last = c.read(c.regular(jobs[-1]))
        seal = root / 'native-evidence' / f'{last["step"]:03d}' / 'result-identity.json'
        if seal.exists():
            value = c.validate(c.workspace(root, last['step']))
            complete = last.get('role') == 'A' and value.get('next_action') == 'finish'
    state = budget.closeout_state(root)
    if state['unresolved_operations']:
        blocked.append(dict(code='unknown_delivery_effect', path='control-ledger'))
    actions = state['actions']
    if actions:
        row = actions[-1]
        outcome = row['outcome']
        if outcome is None or outcome['result'].get('status') == 'unknown' or outcome['result'].get('cleanup_confirmed') is not True:
            blocked.append(dict(code='unknown_delivery_effect', path='control-ledger'))
        elif row['intent']['action_kind'] == 'confirm' and outcome['result']['status'] == 'confirmed':
            complete = True
    if not complete:
        blocked.append(dict(code='source_not_completed', path='jobs'))
    for path in jobs:
        job = c.read(c.regular(path))
        for record, intent in execution.accepted_intents(root, job['step']):
            outcome_path = record / 'native-outcome.json'
            if not outcome_path.exists():
                blocked.append(dict(code='unknown_native_effect', path=record.relative_to(root).as_posix()))
                continue
            outcome = c.read(c.regular(outcome_path))
            if (outcome.get('native_started') is not False and not (record / 'runner.lock').exists()
                    and not (root / 'instance-owner.json').exists()):
                blocked.append(dict(code='source_guard_unavailable', path=(record / 'runner.lock').relative_to(root).as_posix()))
            if outcome.get('native_started') is not False and (type(outcome.get('returncode')) is not int or outcome.get('cleanup') != 'observed_group_cleaned'):
                blocked.append(dict(code='unknown_native_effect', path=record.relative_to(root).as_posix()))
        if job.get('experiment_mode') is not None:
            records = experiment_client.record_directory(root, job['step'])
            if any(records.glob('.prepared-*')):
                blocked.append(dict(code='unknown_managed_preparation', path=records.relative_to(root).as_posix()))
            for directory in sorted((records / 'calls').glob('*')):
                for lock in (records / 'node.lock', _accept_lock(root, job, records)):
                    if not lock.exists() and not (root / 'instance-owner.json').exists():
                        blocked.append(dict(code='source_guard_unavailable', path=lock.relative_to(root).as_posix()))
                accepted = c.read(c.regular(directory / 'accepted.json'))
                interface = accepted['interface']
                def original(proof):
                    path = Path(proof['path'])
                    return files.check_path(path if path.is_absolute() else root / path)
                retirement = directory / 'submission-retirement.json'
                observation = directory / 'execution-observation.json'
                completion = directory / 'completed.json'
                status = 'unknown'
                if retirement.exists():
                    experiments.verify_retirement(c.read(c.regular(retirement)), original, interface=interface)
                    status = 'not_submitted'
                elif observation.exists():
                    status = experiments.verify_observation(c.read(c.regular(observation)), original, interface=interface)
                elif completion.exists() and interface.get('observation_version') is None:
                    terminal_path = directory / 'terminal.json'
                    terminal = c.read(c.regular(terminal_path)) if terminal_path.exists() else None
                    status = experiments.local_terminal_status(accepted, c.read(c.regular(completion)), terminal)
                if status not in {'known_success', 'known_failed', 'not_submitted'}:
                    blocked.append(dict(code='unknown_managed_effect', path=(directory / 'accepted.json').relative_to(root).as_posix()))
    if str(root) not in _guarded.get():
        try:
            with source_guard(root, create_gate=False):
                pass
        except (OSError, RuntimeError, ValueError) as error:
            blocked.append(dict(code='source_guard_unavailable', path='source_owner_locks', reason=str(error)))
    return blocked


@contextmanager
def source_guard(parent_root, *, create_gate=True):
    """Hold existing cooperative source-owner locks without creating a file."""
    from . import execution, experiment_client, instance_owner
    root = files.check_path(Path(parent_root).absolute())
    descriptors = []
    outer = ExitStack()
    locked = set()
    def acquire(path, *, required=False):
        path = files.check_path(path)
        if not path.exists():
            if required:
                raise ValueError('source_guard_unavailable: ' + path.name)
            return
        if path in locked:
            return
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Source owner lock is not a regular file')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        descriptors.append(fd)
        locked.add(path)
    try:
        outer.enter_context(instance_owner.source_lease(root, create=create_gate))
        binding = instance_owner._binding(root)
        if binding is not None:
            anchor, _ = instance_owner._anchor(binding)
            acquire(anchor / 'writer.lock', required=True)
            instance_owner._active(root, binding)
        acquire(root / 'host-run.lock', required=binding is None)
        acquire(root / 'configuration.lock')
        acquire(root / 'execution-authority/ui.lock')
        for path in sorted((root / 'jobs').glob('*.json')):
            job = _json(path)
            for record, _ in execution.accepted_intents(root, job['step']):
                outcome = _json(record / 'native-outcome.json')
                acquire(record / 'runner.lock', required=binding is None and outcome.get('native_started') is not False)
            records = experiment_client.record_directory(root, job['step'])
            calls = sorted((records / 'calls').glob('*'))
            for path in (records / 'node.lock', _accept_lock(root, job, records)):
                acquire(path, required=bool(calls) and binding is None)
            for call in calls:
                for prefix in ('tcad-submission-', 'tcad-binding-', 'tcad-collect-', 'observation-', 'engineering-'):
                    acquire(records / (prefix + call.name + '.lock'))
            if records.parent.parent.name == 'experiment-control':
                acquire(records.parent / 'accept.lock')
        acquire(root / 'execution-authority/writer.lock')
        acquire(root / 'control-ledger.lock')
        token = _guarded.set(_guarded.get() | {str(root)})
        try:
            yield
        finally:
            _guarded.reset(token)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        outer.close()


def _inherited_costs(closure, prefix):
    total = 0
    complete = True
    for node in prefix:
        identity = node['origin_identity']
        base = f'native-evidence/{node["step"]:03d}/'
        intents = [row for row in closure.entries.values() if row['origin_identity'] == identity
            and row['path'].startswith(base) and row['path'].endswith('/native-intent.json')]
        for entry in intents:
            meter = entry['path'].rsplit('/', 1)[0] + '/usage.json'
            usage_entry = closure.entry(identity, meter, required=False)
            if usage_entry is None:
                complete = False
                continue
            usage = _json(Path(closure.paths[_key(identity, meter, usage_entry)]))
            if any(type(usage.get(field)) is not int or usage[field] < 0 for field in ('input_tokens', 'output_tokens', 'cached_input_tokens')):
                raise ValueError('Original usage meter changed its declared unit')
            total += usage['input_tokens'] + usage['output_tokens']
            outcome_path = entry['path'].rsplit('/', 1)[0] + '/native-outcome.json'
            outcome_key = _key(identity, outcome_path, closure.entry(identity, outcome_path))
            outcome = _json(Path(closure.paths[outcome_key]))
            if (outcome.get('metering') or {}).get('coverage') == 'unknown':
                complete = False
    return dict(tokens=dict(status='known' if complete else 'unknown', value=total if complete else None,
            known_lower_bound=total, reason=None if complete else 'At least one original attempt has incomplete metering'),
        active_seconds=dict(status='unknown', value=None, reason='No exact active-time ledger cut was reconstructed'),
        control_actions=dict(status='unknown', value=None, known_scientific_nodes=len(prefix),
            reason='Scientific prefix count does not account for all original control actions'))


class _Closure:
    def __init__(self):
        self.entries = {}
        self.paths = {}
        self.origins = {}
        self.records = {}
        self.visited = set()
        self.total_bytes = 0
        self.blocked = []
        self.limitations = []
        self.tasks = {}
        self.task_active = set()
        self.seals = {}
        self.legacy_compilations = {}

    def entry(self, identity, path, *, required=True):
        matches = [row for row in self.entries.values() if row['origin_identity'] == identity and row['path'] == path]
        if not matches and not required:
            return None
        if len(matches) != 1:
            raise ValueError('Original has no unique exact closure binding: ' + path)
        return matches[0]

    def add(self, source, path, *, proof=None, visibility='host_proof', parse=False, work_step=None):
        path = o.relative(path)
        identity = source.origin_identity
        ref = dict(path=path, **({field: proof[field] for field in ('sha256', 'size_bytes') if field in proof} if proof else {}))
        try:
            actual = o.resolve_ref(source, ref)
        except (OSError, ValueError):
            if proof is None or proof.get('retention') != 'archived' or not proof.get('snapshot'):
                raise
            actual = o.resolve_ref(source, dict(ref, path=o.relative(proof['snapshot'])))
        checked = files.hash_file(actual)
        if any(field in ref and checked[field] != ref[field] for field in ('sha256', 'size_bytes')):
            raise ValueError('Original changed while collecting its exact proof')
        key = _key(identity, path, checked)
        entry = dict(origin_identity=identity, path=path, sha256=checked['sha256'],
            size_bytes=checked['size_bytes'], mode=0o400,
            storage_path=f'inherited/{o.identity_key(identity)}/versions/{checked["sha256"]}/{checked["size_bytes"]}/{path}',
            visibility=visibility)
        previous = self.entries.get(key)
        if previous is None:
            self.total_bytes += entry['size_bytes']
            if len(self.entries) >= MAX_ENTRIES or self.total_bytes > MAX_BYTES:
                raise ValueError('Fork source closure exceeds its finite copy bounds')
            self.entries[key] = entry
            self.paths[key] = str(actual)
        elif previous['visibility'] == 'host_proof' and visibility != 'host_proof':
            previous['visibility'] = visibility
        origin_key = o.identity_key(identity)
        origin = dict(identity=identity, original_root=source.original_root)
        if origin_key in self.origins and self.origins[origin_key] != origin:
            raise ValueError('Original instance has conflicting logical root identity')
        self.origins[origin_key] = origin
        if parse and key not in self.visited:
            self.visited.add(key)
            self.refs(source, _json(actual), work_step=work_step, context=Path(path).name, document=path)
        return dict(origin_identity=identity, path=path, sha256=entry['sha256'], size_bytes=entry['size_bytes'])

    def refs(self, source, value, *, work_step=None, context=None, document=None):
        """Follow only declared hash/path original records, never prose strings."""
        if isinstance(value, list):
            for item in value:
                self.refs(source, item, work_step=work_step, context=context, document=document)
            return
        if not isinstance(value, dict):
            return
        if context == 'engineering_repair':
            self.refs(source, value.get('failure'), work_step=work_step, context='repair_failure', document=document)
            for proof in value.get('objects', {}).values():
                self.refs(source, proof, work_step=work_step, context='repair_object', document=document)
        if (document is not None and Path(document).name == 'accepted.json' and context == 'prepared'
                and isinstance(value.get('local_path'), str) and isinstance(value.get('sha256'), str)):
            prepared = dict(path=value['local_path'], sha256=value['sha256'], size_bytes=value['size_bytes'])
            logical = Path(prepared['path'])
            if logical.is_absolute():
                root = Path(source.original_root)
                if not logical.is_relative_to(root):
                    raise ValueError('Prepared original is outside its original instance')
                prepared['path'] = logical.relative_to(root).as_posix()
            try:
                o.resolve_ref(source, prepared)
            except (OSError, ValueError):
                # The accepted digest binds these actual retained bytes. Do not
                # claim archived retention, or require it, before checking them.
                archive = dict(prepared, path='host-history/objects/' + prepared['sha256'])
                o.resolve_ref(source, archive)
                self.add(source, archive['path'], proof=archive)
                prepared.update(retention='archived', snapshot=archive['path'])
            self.add(source, prepared['path'], proof=prepared)
        if isinstance(value.get('sha256'), str) and ('source' in value or 'path' in value):
            raw = value.get('source', value.get('path'))
            if isinstance(raw, str):
                owner = o.source_for_ref(source, value)
                logical = Path(raw)
                if logical.is_absolute():
                    if 'source' in value:
                        owner, path = o.material_owner(owner, value)
                    else:
                        root = Path(owner.original_root)
                        if not logical.is_relative_to(root):
                            raise ValueError('Declared original is outside its original instance: ' + raw)
                        path = logical.relative_to(root).as_posix()
                else:
                    path = raw
                    if path.startswith('inputs/'):
                        if work_step is None:
                            raise ValueError('Workspace input reference lacks its exact original job')
                        path = f'project/nodes/{work_step:03d}/' + path
                parse = _control_document(path, context=context, document=document)
                self.add(owner, path, proof=value, parse=parse, work_step=work_step)
                if value.get('retention') == 'archived' and value.get('snapshot'):
                    self.add(owner, o.relative(value['snapshot']), proof=value)
        for key, item in value.items():
            if document is not None and context == 'accepted.json' and key == 'snapshot_files':
                # Records.accept copies these bytes under the call's inputs
                # namespace. item.path is the mutable pre-acceptance locator.
                for row in item:
                    path = (Path(document).parent / 'inputs' / o.relative(row['name'])).as_posix()
                    self.add(source, path, proof=row)
                continue
            # A retained native session descriptor can point to credentials/home.
            # Its separately sealed header is the portable authorship evidence.
            declared = key in REFERENCE_MEMBERS
            declared |= key in {'view', 'raw'} and context == 'input_sources'
            declared |= key == 'outputs' and context in {'managed_calls', 'execution-observation.json'}
            declared |= key == 'items' and context == 'delivery_sources'
            # These are observer host-contract fields, never keys in authored
            # scientific output. Their archived proofs are required independently
            # of the producer's live artifact path.
            if document is not None and document.startswith('host-history/observations/'):
                declared |= key in {'candidates', 'current_candidate', 'actual_submission', 'contexts',
                    'artifact', 'model', 'completed', 'terminal', 'retirement', 'observation'}
            if document is not None and document.startswith('host-history/execution-recoveries/'):
                declared |= key in {'row', 'previous', 'allocation', 'pending_set', 'decision',
                    'admission', 'query', 'submission', 'completed', 'terminal', 'retirement', 'observation'}
            if document is not None and document.endswith('/pending-execution-set.json'):
                declared |= key in {'allocations', 'allocation', 'requests', 'prepare_intent',
                    'submission.json', 'tcad-dispatch.json', 'tcad-backend-binding.json',
                    'completed.json', 'submission-retirement.json'}
                declared |= context == 'attempts' and key in {'native-intent.json', 'native-outcome.json',
                    'runner-process.json', 'native-process.json'}
            if document is not None and Path(document).name == 'accepted.json':
                declared |= key in {'script', 'prepared', 'execution_authority', 'input_sources'}
            if document is not None and document.startswith('execution-authority/'):
                declared |= key in {'allocation', 'decision', 'admission', 'pending_set', 'grant_revision', 'payload'}
            if document is not None and (Path(document).name.startswith('author-client-')
                    or document.startswith('host-recovery/author-closeout/')):
                declared |= key in {'legacy_authorization', 'permit', 'package', 'settings', 'original_intent',
                    'original_experiment_config', 'source_originals', 'successor', 'upgrade'}
                declared |= context == 'source_originals' and key in {'native-intent.json', 'native-outcome.json',
                    'usage.json', 'usage-observation.json', 'native.jsonl', 'runner-process.json', 'native-process.json'}
            if declared and not (
                    key == 'origin' and ('retained' in value or 'material' in value)):
                self.refs(source, item, work_step=work_step, context=key, document=document)

    def settings(self, source):
        self.add(source, 'settings.json', proof=dict(sha256=source.origin_identity['settings_sha256']))
        self.add(source, 'settings-identity.json')
        job = o.source_job(source)
        revision = job.get('configuration_revision')
        if revision is not None:
            for number in range(1, revision['revision'] + 1):
                self.add(source, f'host-control/configuration/revisions/{number:06d}.json', parse=True)
                self.add(source, f'host-control/configuration/identities/{number:06d}.json')
            self.add(source, f'host-control/configuration/bindings/{source.step:03d}.json')
        settings = o.source_settings(source)
        for field in ('source_provenance', 'formal_review_policy', 'execution_authority_policy'):
            self.refs(source, settings.get(field), work_step=source.step, context=field)
        return settings

    def phase(self, source, job):
        boundary = job.get('closeout_begin')
        if boundary is None and (job.get('final_only') or job.get('final_report')):
            intent = _json(o.resolve_ref(source, dict(path=f'native-evidence/{source.step:03d}/native-intent.json')))
            marker = (intent.get('budget_slice') or {}).get('final_return')
            if not isinstance(marker, dict) or type(marker.get('seq')) is not int:
                raise ValueError('Final source lacks its exact accepted phase reference')
            events = []
            predecessor = None
            for number in range(1, marker['seq'] + 1):
                ref = self.add(source, f'control-ledger/{number:06d}.json',
                    proof=dict(sha256=marker['sha256']) if number == marker['seq'] else None)
                event = _json(o.resolve_ref(source, ref))
                if event.get('seq') != number or event.get('parent_sha256') != predecessor:
                    raise ValueError('Final source phase ledger chain changed')
                predecessor = ref['sha256']
                events.append(event)
            accepted = events[-1]
            if accepted.get('kind') != 'final_a_return' or accepted.get('step') != job['step']:
                raise ValueError('Final source accepted return belongs to another task')
            candidates = [event for event in events[:-1] if event.get('kind') == 'closeout_begin']
            if not candidates:
                raise ValueError('Final source accepted return has no original phase')
            event = candidates[-1]
            cutoff = job.get('source_cutoff', (job.get('final_report') or {}).get('source_cutoff'))
            if event.get('mode') != 'final' or event.get('first_step') != job['step'] or event.get('source_cutoff') != cutoff:
                raise ValueError('Final source accepted phase changed its exact cutoff')
            boundary = dict(seq=event['seq'], sha256=self.entry(source.origin_identity,
                f'control-ledger/{event["seq"]:06d}.json')['sha256'])
        if boundary is None:
            return None
        return self.add(source, f'control-ledger/{boundary["seq"]:06d}.json', proof=dict(sha256=boundary['sha256']))

    def task(self, source, *, selected=False):
        from . import contracts as c
        step = source.step
        job = o.source_job(source)
        key = (o.identity_key(source.origin_identity), step)
        if key in self.tasks:
            return self.tasks[key]
        if key in self.task_active:
            raise ValueError('Fork source task ancestry cycle')
        self.task_active.add(key)
        if source.entries is None:
            c._validate_inputs(source.storage_root, c.workspace(source.storage_root, step), job)
        elif selected:
            c.validate_original(source)
        self.settings(source)
        job_ref = self.add(source, f'jobs/{step:03d}.json')
        task_ref = self.add(source, f'project/nodes/{step:03d}/science-task.json')
        boundaries = [job[field] for field in ('closeout_begin', 'normal_resume') if job.get(field) is not None]
        for boundary in boundaries:
            for number in range(1, boundary['seq'] + 1):
                self.add(source, f'control-ledger/{number:06d}.json',
                    proof=dict(sha256=boundary['sha256']) if number == boundary['seq'] else None, parse=True)
        if job.get('delivery_sources', {}).get('catalogue'):
            self.add(source, f'project/nodes/{step:03d}/inputs/delivery-candidates.json',
                visibility='scientific_input' if selected else 'historical_science')
        marker = job.get('selection')
        if marker is not None:
            owner, owner_job, seen = source, job, set()
            while owner_job.get('fork_source') is not None:
                identity = (o.identity_key(owner.origin_identity), owner.step)
                if identity in seen:
                    raise ValueError('Selection original task chain contains a cycle')
                seen.add(identity)
                ref = owner_job['fork_source']['job']
                owner = o.source_for_ref(owner, ref)
                o.resolve_ref(owner, ref)
                owner_job = o.source_job(owner)
                if owner_job.get('selection') != marker:
                    raise ValueError('Selection marker differs from its exact original task')
            directory = 'host-delivery/selection'
            if marker.get('repair_step'):
                directory += f'/repairs/{marker["repair_step"]:03d}'
            screen_ref = self.add(owner, directory + '/screen.json', proof=dict(sha256=marker['sha256']))
            screen = _json(o.resolve_ref(owner, screen_ref))
            self.add(owner, directory + '/shortlist.json', proof=dict(sha256=screen['shortlist_sha256']))
        for field in ('global_materials', 'direct_materials', 'direct_refs', 'selected_inputs',
                      'history', 'source_provenance', 'formal_review', 'formal_review_return', 'candidate_audit',
                      'engineering_repair', 'correction_request', 'author_correction', 'delivery_sources',
                      'final_report', 'normal_resume', 'execution_authority', 'user_guidance'):
            value = job.get(field)
            if field == 'history' and value is not None:
                # The view contains authored scientific strings and arbitrary
                # payload projections; only these typed descriptor members own
                # original-file dependencies. Never recurse into history.view.
                value = {key: value[key] for key in ('files', 'execution_recoveries',
                    'execution_recovery_materials', 'audit_receipts', 'adjacent_audit') if key in value}
                if 'execution_recoveries' in value:
                    value['execution_recoveries']=o.recovery_refs(source,job)
            self.refs(source, value, work_step=step, context=field)
        self.refs(source, job.get('control_cut'), work_step=step)
        if (o.source_settings(source).get('correction_policy') is not None or job.get('correction_request')) and 'control_cut' not in job:
            self.blocked.append(dict(code='fork_input_missing', input='control_cut.correction_events',
                reason='historical_correction_cut_unverifiable', path=f'jobs/{step:03d}.json'))
        # The task's exact files are a separate namespace from their producers.
        for row in [*job.get('global_materials', []), *job.get('direct_materials', []),
                    *job.get('direct_refs', []), *job.get('history', {}).get('files', []),
                    *job.get('history', {}).get('execution_recovery_materials', [])]:
            self.add(source, f'project/nodes/{step:03d}/' + o.relative(row['path']),
                proof=row, visibility='scientific_input' if selected else 'historical_science')
        repair = job.get('engineering_repair')
        if repair is not None:
            for name in ('source.py', 'artifact'):
                proof = repair['objects'].get(name)
                if proof is not None:
                    self.add(source, f'project/nodes/{step:03d}/inputs/repair/{name}',
                        proof=proof, visibility='scientific_input' if selected else 'historical_science')
        for ref in job.get('direct_originals', []):
            original = str(Path(ref['path']).parent / 'scientific-original.json')
            if source.entries is not None:
                exists = any(row['origin_identity'] == source.origin_identity
                    and row['path'] == f'project/nodes/{step:03d}/{original}' for row in source.entries)
            else:
                exists = (source.storage_root / f'project/nodes/{step:03d}/{original}').exists()
            if exists:
                self.add(source, f'project/nodes/{step:03d}/{original}', proof=ref,
                    visibility='scientific_input' if selected else 'historical_science')
        if job.get('fork_source') is not None:
            original_ref = job['fork_source']['job']
            owner = o.source_for_ref(source, original_ref)
            o.resolve_ref(owner, original_ref)
            self.task(owner)
        for row in job.get('history', {}).get('nodes', []):
            if not 0 < row['step'] < step:
                raise ValueError('Original task dependency crosses its exact input boundary')
            self.sealed(o.dependency(source, row['step'], seal_sha256=row['seal_sha256']), row['seal_sha256'])
        if job.get('formal_review') is not None:
            request_source = o.source_for_ref(source, job['formal_review'])
            request = _json(o.resolve_ref(request_source, job['formal_review']))
            for producer_step, seal_ref in [(request['requester']['step'], request['requester']['seal']),
                    (request['subject']['producer_step'], request['subject']['producer_seal'])]:
                owner = o.dependency(request_source, producer_step, seal_sha256=seal_ref['sha256'])
                self.sealed(owner, seal_ref['sha256'])
        self.task_active.remove(key)
        self.tasks[key] = (job, job_ref, task_ref)
        return self.tasks[key]

    def sealed(self, source, seal_sha):
        key = (o.identity_key(source.origin_identity), source.step)
        if key in self.seals:
            if self.seals[key]['seal_ref']['sha256'] != seal_sha:
                raise ValueError('One original node has conflicting sealed identities')
            return self.seals[key]
        job, job_ref, _ = self.task(source)
        step = source.step
        seal_path = f'native-evidence/{step:03d}/result-identity.json'
        seal_ref = self.add(source, seal_path, proof=dict(sha256=seal_sha), parse=True, work_step=step)
        seal = _json(o.resolve_ref(source, seal_ref))
        if seal.get('job_sha256') != job_ref['sha256']:
            raise ValueError('Inherited seal lost its exact original job')
        raw = o.source_raw(source)
        record = self._logical_path(source, raw).rsplit('/', 1)[0]
        self.records[f'{o.identity_key(source.origin_identity)}:{step}'] = record
        self.add(source, record + '/science-output.json', proof=dict(sha256=seal['raw_sha256']), visibility='historical_science')
        self.add(source, f'native-evidence/{step:03d}/result.json', proof=dict(sha256=seal['sha256']), visibility='historical_science')
        self.add(source, f'project/nodes/{step:03d}/conclusion.json', proof=dict(sha256=seal['raw_sha256']), visibility='historical_science')
        self.attempts(source, selected=False)
        if seal.get('compiled_implementations'):
            self.compilation_inventory(source)
        self.seals[key] = dict(step=step, origin_identity=source.origin_identity, job_ref=job_ref, seal_ref=seal_ref)
        return self.seals[key]

    def compilation_inventory(self, source):
        """Freeze the complete legacy index and exact optional author chain."""
        key = f'{o.identity_key(source.origin_identity)}:{source.step}'
        if key in self.legacy_compilations:
            return
        if source.inherited:
            inventory = source.snapshot.legacy_compilations.get(key)
            if inventory is None:
                # Old snapshots cannot prove absence or directory completeness.
                return
            inventory = copy.deepcopy(inventory)
        else:
            base = f'native-evidence/{source.step:03d}/compiled-implementations'
            paths = sorted(path.relative_to(source.storage_root).as_posix()
                for path in (source.storage_root / base).glob('*/compiler.json'))
            def optional(path):
                return o.original_ref(source, path) if (source.storage_root / path).exists() else None
            inventory = dict(compiler_paths=paths,
                author_successor=optional(f'host-recovery/author-closeout/{source.step:03d}-successor.json'),
                output_upgrade=optional(f'host-recovery/author-closeout/{source.step:03d}-output-upgrade.json'))
        self.legacy_compilations[key] = inventory
        for path in inventory['compiler_paths']:
            self.add(source, path, parse=True, work_step=source.step)
        for name in ('author_successor', 'output_upgrade'):
            if inventory[name] is not None:
                self.refs(source, inventory[name], work_step=source.step)

    def _logical_path(self, source, actual):
        if source.entries is None:
            return Path(actual).relative_to(source.storage_root).as_posix()
        matches = [row['path'] for row in source.entries
            if row['origin_identity'] == source.origin_identity and source.storage_root / row['storage_path'] == actual]
        if len(matches) != 1:
            raise ValueError('Copied original has no unique logical path')
        return matches[0]

    def attempts(self, source, *, selected):
        step = source.step
        if source.entries is None:
            from . import execution
            chain = list(execution.accepted_intents(source.storage_root, step))
            paths = [(record.relative_to(source.storage_root).as_posix(), intent) for record, intent in chain]
        else:
            first = f'native-evidence/{step:03d}'
            names = [row['path'] for row in source.entries if row['origin_identity'] == source.origin_identity
                and row['path'].endswith('/native-intent.json')
                and (row['path'] == first + '/native-intent.json' or row['path'].startswith(first + '/continuations/'))]
            paths = [(name.rsplit('/', 1)[0], _json(o.resolve_ref(source, dict(path=name)))) for name in sorted(names)]
        if not paths and selected:
            return None  # A frozen, never-submitted logical task has no call to resume.
        if not paths:
            raise ValueError('Selected task lacks its original accepted native intent')
        task_sha = self.entry(source.origin_identity, f'jobs/{step:03d}.json')['sha256']
        for record, intent in paths:
            if intent.get('task_sha256') != task_sha:
                raise ValueError('Original native intent changed its exact task')
            visibility = 'effect_only' if selected else 'host_proof'
            self.add(source, record + '/native-intent.json', visibility=visibility)
            if not selected:
                for name in ('experiment-config.json', 'checkpoint.json', 'stage-end.json', 'work-evidence.json'):
                    optional_path = record + '/' + name
                    exists = ((source.storage_root / optional_path).exists() if source.entries is None else
                        any(row['origin_identity'] == source.origin_identity and row['path'] == optional_path for row in source.entries))
                    if exists:
                        self.add(source, optional_path, parse=name == 'experiment-config.json', work_step=step)
            usage_path = record + '/usage.json'
            exists = ((source.storage_root / usage_path).exists() if source.entries is None else
                any(row['origin_identity'] == source.origin_identity and row['path'] == usage_path for row in source.entries))
            if exists and not selected:
                self.add(source, usage_path)
            try:
                outcome_ref = self.add(source, record + '/native-outcome.json', visibility=visibility)
                outcome = _json(o.resolve_ref(source, outcome_ref))
            except (OSError, ValueError):
                self.blocked.append(dict(code='unknown_native_effect', path=record))
                continue
            if (outcome.get('native_started') is not False and (type(outcome.get('returncode')) is not int
                    or outcome.get('cleanup') != 'observed_group_cleaned')):
                self.blocked.append(dict(code='unknown_native_effect', path=record))
            # Do not recursively expose selected N outcome science or tool feedback.
        return paths[0][0] + '/native-intent.json'


def inspect_source(parent_root, selected_job_ref):
    """Inspect an exact step or an exact {origin_identity,path,sha256} job ref."""
    from . import contracts as c
    root = files.check_path(Path(parent_root).absolute())
    initial = c.settings(root)
    if initial.get('handoff_protocol') != 23:
        raise ValueError('Fork source requires frozen protocol 23')
    if isinstance(selected_job_ref, int) and not isinstance(selected_job_ref, bool):
        step = selected_job_ref
    elif isinstance(selected_job_ref, dict):
        parts = Path(o.relative(selected_job_ref['path'])).parts
        if len(parts) != 2 or parts[0] != 'jobs' or not parts[1].endswith('.json'):
            raise ValueError('Fork selection requires an exact original job')
        step = int(Path(parts[1]).stem)
    else:
        raise ValueError('Explicit scientific step or job original required')
    # Source N must belong to the selected parent; nested prefix N must be forked
    # through its original producer rather than silently selecting a different task.
    source = o.node(root, step)
    closure = _Closure()
    closure.blocked.extend(_parent_state(root))
    job, job_ref, task_ref = closure.task(source, selected=True)
    if isinstance(selected_job_ref, dict):
        if any(selected_job_ref.get(field) != job_ref[field] for field in ('origin_identity', 'path', 'sha256')):
            raise ValueError('Selected fork job identity changed')
    prefix = []
    visible_sources = {item.step: item for item in o.source_sequence(root, step)}
    science_nodes = list(job.get('history', {}).get('nodes', []))
    if job.get('formal_review') is not None:
        # A reviewer sees its neutral subject binding. Host routing and science
        # investment retain the requester's exact accepted prefix separately.
        request_source = o.source_for_ref(source, job['formal_review'])
        request = _json(o.resolve_ref(request_source, job['formal_review']))
        requester = o.dependency(request_source, request['requester']['step'],
            seal_sha256=request['requester']['seal']['sha256'])
        requester_job = o.source_job(requester)
        science_nodes = [*requester_job.get('history', {}).get('nodes', []),
            dict(step=requester.step, seal_sha256=request['requester']['seal']['sha256'])]
    for row in science_nodes:
        if type(row.get('step')) is not int or row['step'] >= step:
            raise ValueError('Selected task history crosses its input boundary')
        ancestor = visible_sources.get(row['step']) or o.dependency(source, row['step'], seal_sha256=row['seal_sha256'])
        if ancestor is None:
            raise ValueError('Selected history has no exact visible sealed original')
        prefix.append(closure.sealed(ancestor, row['seal_sha256']))
        visible_sources[row['step']] = ancestor
    if len({row['step'] for row in prefix}) != len(prefix):
        raise ValueError('Selected task history is ambiguous')
    native_intent = closure.attempts(source, selected=True)
    phase_ref = closure.phase(source, job)
    selected_settings = o.source_settings(source)
    public_snapshots = []
    declarations = dict(declarations=[], globals=[])
    declaration_ref = selected_settings.get('source_provenance')
    if declaration_ref is not None:
        declarations = _json(o.resolve_ref(source, declaration_ref))
        public_root = Path(selected_settings['public_root'])
        for declaration in declarations.get('declarations', []):
            original = Path(declaration['origin']['path'])
            if original.is_relative_to(public_root):
                retained = declaration['retained']
                public_snapshots.append(dict(path=o.relative(original.relative_to(public_root).as_posix()),
                    original_ref=closure.add(source, retained['path'], proof=retained)))
    closure.limitations.append(dict(code='unfrozen_native_public_reads',
        detail='Only explicitly retained public originals are portable; arbitrary native reads were not frozen.'))
    # Source settings contracts retain root-relative public identities, while
    # input globals are the selected job's frozen files above.
    route_fields = ('role', 'instruction', 'evidence_return', 'correction_request', 'author_correction',
        'purpose', 'source_cutoff', 'delivery_sources', 'engineering_repair', 'final_report')
    route = {field: copy.deepcopy(job.get(field)) for field in route_fields}
    route['parents'] = [int(Path(row['path']).parts[2]) for row in job.get('direct_refs', [])]
    def latest(role):
        for row in reversed(prefix):
            candidate = visible_sources[row['step']]
            if o.source_job(candidate)['role'] == role:
                return row['job_ref']
        return None
    boot = dict(version=1, next_step=step, selected_task_ref=job_ref, intent_kind=_kind(job), route=route,
        phase_original_ref=phase_ref,
        current_h=latest('H'), current_w=latest('W'), completed_source_refs=[row['job_ref'] for row in prefix])
    inherited_manifest = o._manifest(root)
    ancestry = ([] if inherited_manifest is None else list(inherited_manifest.get('ancestry', [])))
    direct = o.local_identity(root)
    if direct in ancestry or len({o.identity_key(identity) for identity in ancestry}) != len(ancestry):
        raise ValueError('Fork ancestry contains a source identity cycle')
    ancestry.append(direct)
    if len(ancestry) > MAX_ANCESTRY:
        raise ValueError('Fork ancestry exceeds its finite depth bound')
    history_owner=o.history_source(source,job)
    result = dict(version=1, kind='scientific_fork', source=dict(origin_identity=source.origin_identity,
        step=step, job_ref=job_ref, science_task_ref=task_ref, native_intent_path=native_intent, intent_kind=_kind(job)),
        cut=dict(history=copy.deepcopy(job.get('history', {})),
            correction_events=[dict(ref, origin_identity=ref.get('origin_identity', source.origin_identity))
                for ref in job.get('control_cut', {}).get('correction_events', [])],
            phase_original_ref=phase_ref,
            execution_recoveries=[dict(ref, origin_identity=ref.get('origin_identity', history_owner.origin_identity))
                for ref in o.recovery_refs(source,job)]),
        prefix_entries=prefix, entries=list(closure.entries.values()), origins=closure.origins,
        native_records=closure.records, legacy_compilations=closure.legacy_compilations, bootstrap=boot, ancestry=ancestry,
        selected_settings=selected_settings, public_snapshots=public_snapshots,
        selected_globals=[dict(material=copy.deepcopy(material),
            original_ref=closure.add(source, material['source'], proof=material)) for material in job['global_materials']],
        source_declarations=declarations,
        inherited_costs=_inherited_costs(closure, prefix),
        limitations=closure.limitations, blocked_reasons=closure.blocked,
        source_paths=closure.paths, source_root=str(root))
    result['source_fingerprint'] = fingerprint(result)
    return result


def fingerprint(plan):
    return hashlib.sha256(files.canonical({key: value for key, value in plan.items()
        if key not in SOURCE_LOCATORS | {'source_fingerprint'}})).hexdigest()


def _existing_directory(path):
    path = files.check_path(path)
    while not path.exists():
        path = path.parent
    if not path.is_dir():
        raise ValueError('Fork storage ancestor is not a directory')
    return path


def storage_blockers(plan, target_directory):
    """Inspect link devices and cache availability without writing either root."""
    device = _existing_directory(Path(target_directory).absolute()).stat().st_dev
    for entry in plan['entries']:
        source = files.check_path(Path(plan['source_paths'][_key(entry['origin_identity'], entry['path'], entry)]))
        info = source.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Fork original is not a regular file')
        if info.st_dev != device:
            return [dict(code='fork_storage_unsupported', reason='Fork originals and target require the same filesystem')]
        if stat.S_IMODE(info.st_mode) != 0o400:
            root = files.check_path(Path(plan['source_root']).absolute())
            directory = _existing_directory(root / 'host-history/objects')
            if directory.stat().st_dev != device:
                return [dict(code='fork_storage_unsupported', reason='Fork snapshot cache and target require the same filesystem')]
            cache = files.check_path(root / 'host-history/objects' / entry['sha256'])
            if cache.exists() and cache.stat().st_dev != device:
                return [dict(code='fork_storage_unsupported', reason='Fork snapshot cache and target require the same filesystem')]
            if not cache.exists() and not stat.S_IMODE(directory.stat().st_mode) & 0o222:
                return [dict(code='fork_storage_unsupported', reason='Fork needs a writable private source snapshot cache')]
    return []


def _shared_original(source, entry, source_root):
    """Normalize writable legacy originals once, preserving their original inode."""
    if stat.S_IMODE(source.stat().st_mode) == 0o400:
        return source
    directory = files.check_path(Path(source_root) / 'host-history/objects')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = files.check_path(directory / entry['sha256'])
    if destination.exists():
        checked = files.hash_file(destination)
        if any(checked[key] != entry[key] for key in ('sha256', 'size_bytes', 'mode')):
            raise ValueError('Fork snapshot cache original changed')
        return destination
    # A crashed import may leave a disposable temporary, but never poison the
    # next import by reusing/truncating a readonly temporary or published inode.
    descriptor, name = tempfile.mkstemp(prefix='.fork-snapshot-', dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            with os.fdopen(source_fd, 'rb') as original:
                if not stat.S_ISREG(os.fstat(original.fileno()).st_mode):
                    raise ValueError('Fork original is not a regular file')
                copied = 0
                for block in iter(lambda: original.read(1024 * 1024), b''):
                    copied += len(block)
                    if copied > entry['size_bytes']:
                        raise ValueError('Fork original changed during snapshot import')
                    output.write(block)
            output.flush()
            os.fchmod(output.fileno(), 0o400)
            os.fsync(output.fileno())
        files.link_verified(temporary, destination, entry)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def copy_source(plan, target_store):
    """Retain shared originals in the exact child/host-runtime/fork directory."""
    if plan.get('blocked_reasons'):
        raise ValueError('Fork source has unresolved original effects')
    if fingerprint(plan) != plan.get('source_fingerprint'):
        raise ValueError('Fork source plan changed after inspection')
    target_store = files.check_path(Path(target_store).absolute())
    blockers = storage_blockers(plan, target_store)
    if blockers:
        raise files.SharingUnsupported(blockers[0]['reason'])
    target_store.mkdir(parents=True, exist_ok=True, mode=0o700)
    retained = {}
    for entry in plan['entries']:
        key = _key(entry['origin_identity'], entry['path'], entry)
        source = Path(plan['source_paths'][key])
        try:
            source = _shared_original(source, entry, plan['source_root'])
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EROFS}:
                raise files.SharingUnsupported('Fork needs a writable private source snapshot cache') from error
            raise
        retained[key] = target_store / entry['storage_path']
        files.link_verified(source, retained[key], entry)
    (target_store / 'public').mkdir(exist_ok=True, mode=0o700)
    for snapshot in plan.get('public_snapshots', []):
        ref = snapshot['original_ref']
        source = retained[_key(ref['origin_identity'], ref['path'], ref)]
        files.link_verified(source, target_store / 'public' / o.relative(snapshot['path']),
            dict(kind='file', mode=0o400, sha256=ref['sha256'], size_bytes=ref['size_bytes']))
    # Recheck every original after all links/imports, not just each local read.
    for entry in plan['entries']:
        proof = files.hash_file(Path(plan['source_paths'][_key(entry['origin_identity'], entry['path'], entry)]))
        if any(proof[field] != entry[field] for field in ('sha256', 'size_bytes')):
            raise ValueError('Fork source changed during retention; preview is stale')
    manifest = {key: copy.deepcopy(value) for key, value in plan.items() if key not in SOURCE_LOCATORS}
    files.durable_json(target_store / 'origins.json', manifest['origins'])
    files.durable_json(target_store / 'bootstrap.json', manifest['bootstrap'])
    files.durable_json(target_store / 'manifest.json', manifest)
    return manifest


def validate_manifest(child_root, *, full=False):
    manifest = o._manifest(child_root)
    if manifest is None or fingerprint(manifest) != manifest.get('source_fingerprint'):
        raise ValueError('Fork manifest identity changed')
    if len(manifest['entries']) > MAX_ENTRIES or sum(row['size_bytes'] for row in manifest['entries']) > MAX_BYTES:
        raise ValueError('Fork source closure exceeds its finite bounds')
    seen = set()
    snapshot = o._snapshot(manifest) if full else None
    for entry in manifest['entries']:
        key = _key(entry['origin_identity'], entry['path'], entry)
        if key in seen:
            raise ValueError('Fork manifest repeats an original identity')
        seen.add(key)
        if full:
            source = o._source(child_root, manifest, entry['origin_identity'], manifest['source']['step'], snapshot=snapshot)
            o.resolve_ref(source, entry)
    store = Path(child_root) / o.FORK_DIRECTORY
    if _json(store / 'origins.json') != manifest['origins'] or _json(store / 'bootstrap.json') != manifest['bootstrap']:
        raise ValueError('Fork private bindings changed')
    return manifest


def selected_task(child_root, *, full=False):
    manifest = validate_manifest(child_root, full=full)
    source = o.selected_source(child_root)
    job = o.source_job(source)
    task = _json(o.resolve_ref(source, manifest['source']['science_task_ref']))
    fields = ('global_materials', 'direct_refs', 'direct_originals', 'direct_materials', 'selected_inputs', 'history')
    return dict(source=source, job=job, science_task=task, settings=o.source_settings(source),
        phase_original_ref=manifest['cut'].get('phase_original_ref'),
        input_binding={field: copy.deepcopy(job.get(field, {} if field == 'history' else [])) for field in fields},
        source_job_ref=manifest['source']['job_ref'], source_task_ref=manifest['source']['science_task_ref'])


def bootstrap(child_root):
    return copy.deepcopy(validate_manifest(child_root)['bootstrap'])


def prefix_nodes(child_root):
    manifest = validate_manifest(child_root)
    result = []
    snapshot = o._snapshot(manifest)
    for row in manifest['prefix_entries']:
        source = o._source(child_root, manifest, row['origin_identity'], row['step'], snapshot=snapshot)
        result.append(dict(step=row['step'], role=o.source_job(source)['role'],
            origin_identity=row['origin_identity'], job_ref=row['job_ref']))
    return result
