"""Retain an exact control cut and the selected node's current drafts for a fork.

Private snapshot bytes do not become selected inputs or scientific evidence.
Creating or launching the child belongs to fork_service and the controller.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import json
import fcntl
import os
import stat
from pathlib import Path

from scidiscovery import archive_io as files
from . import originals as o

MAX_ENTRIES = 65536
MAX_BYTES = 4 * 1024**3
MAX_ANCESTRY = 16
SOURCE_LOCATORS = frozenset({'source_paths', 'source_root', 'code_transfers'})
_guarded = ContextVar('fork_source_read_guards', default=frozenset())


def _json(path):
    path = files.check_path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Fork control original exceeds its finite JSON parse bound')
    return json.loads(path.read_bytes())


def _key(identity, path, proof):
    return o.identity_key(identity) + ':' + o.relative(path)




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
    state = budget.closeout_state(root)
    if state['unresolved_operations']:
        blocked.append(dict(code='unknown_delivery_effect', path='control-ledger'))
    actions = state['actions']
    if actions:
        row = actions[-1]
        outcome = row['outcome']
        if outcome is None or outcome['result'].get('status') == 'unknown' or outcome['result'].get('cleanup_confirmed') is not True:
            blocked.append(dict(code='unknown_delivery_effect', path='control-ledger'))
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
            snapshot = next(records.glob('.prepared-*'), None)
            if snapshot is not None:
                token = snapshot.name.removeprefix('.prepared-')
                blocked.append(dict(code='unknown_managed_preparation', path=snapshot.relative_to(root).as_posix(),
                    reason=f'Original preparation has not completed acceptance: --step {job["step"]} --snapshot-token {token}. '
                    'See scid-research execution-preparation-maintain --help. '
                    'Only for a proven built-in local unbound partial, use --unbound-local --action inspect; '
                    'bound or unknown preparations require the exact original call.'))
            from . import preparation_recovery
            if any(not row['resolved'] for row in preparation_recovery.retained_preparations(root, records)):
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
                    experiments.verify_retirement(c.read(c.regular(retirement)), original, interface=interface,verify_files=False)
                    status = 'not_submitted'
                elif observation.exists():
                    status = experiments.verify_observation(c.read(c.regular(observation)), original, interface=interface,verify_files=False)
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
    from . import execution, experiment_client, instance_owner, instance_archive
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
            for path in instance_archive.record_leases(records):
                acquire(path, required=True)
            if records.parent.parent.name == 'experiment-control':
                acquire(records.parent / 'accept.lock')
        acquire(root / 'execution-authority/writer.lock')
        acquire(root / 'control-ledger.lock')
        if (root / 'host-runtime/storage.lock').exists():
            from . import storage_policy as storage
            outer.enter_context(storage.writer_lock(root, context=storage.Deadline(65)))
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
        self.total_bytes = 0
        self.blocked = []
        self.limitations = []
        self.tasks = {}
        self.task_active = set()
        self.seals = {}
        self.directory_inventory = []
        self.code_transfers = {}
        self.control_active = set()
        self.control_done = set()

    def entry(self, identity, path, *, required=True):
        matches = [row for row in self.entries.values() if row['origin_identity'] == identity and row['path'] == path]
        visible = [row for row in matches if row['visibility'] != 'private_snapshot']
        if visible:
            matches = visible
        if not matches and not required:
            return None
        if len(matches) != 1:
            raise ValueError('Original has no unique exact closure binding: ' + path)
        return matches[0]

    def add(self, source, path, *, proof=None, visibility='host_proof'):
        path = o.relative(path)
        if visibility in {'scientific_input', 'historical_science'}:
            return self.reference(source, path, proof=proof, visibility=visibility)
        identity = source.origin_identity
        ref = dict(path=path, **({field: proof[field] for field in ('size_bytes','file_stat') if field in proof} if proof else {}))
        try:
            actual = o.resolve_ref(source, ref)
        except (OSError, ValueError):
            if proof is None or proof.get('retention') != 'archived' or not proof.get('snapshot'):
                raise
            actual = o.resolve_ref(source, dict(ref, path=o.relative(proof['snapshot'])))
        checked = files.stat_file(actual)
        if any(field in ref and checked[field] != ref[field] for field in ('size_bytes',)):
            raise ValueError('Original changed while collecting its exact proof')
        key = _key(identity, path, checked)
        entry = dict(origin_identity=identity, path=path,
            size_bytes=checked['size_bytes'],
            mode=checked['mode'] if visibility == 'private_snapshot' else 0o400,
            storage_path=f'inherited/{o.identity_key(identity)}/{path}',
            visibility=visibility)
        previous = self.entries.get(key)
        if previous is None:
            self.total_bytes += entry['size_bytes']
            if len(self.entries) >= MAX_ENTRIES or self.total_bytes > MAX_BYTES:
                raise ValueError('Fork source closure exceeds its finite copy bounds')
            self.entries[key] = entry
            self.paths[key] = str(actual)
        elif (previous['visibility'] == 'private_snapshot' and visibility != 'private_snapshot'
                or previous['visibility'] == 'host_proof' and visibility not in {'host_proof', 'private_snapshot'}):
            previous['visibility'] = visibility
        if previous is not None and previous['visibility'] in {'scientific_input','historical_science'}:
            previous.update(visibility=visibility,mode=entry['mode'])
            self.total_bytes += entry['size_bytes']
            if self.total_bytes > MAX_BYTES:
                raise ValueError('Fork source closure exceeds its finite copy bounds')
            self.paths[key] = str(actual)
        origin_key = o.identity_key(identity)
        origin = dict(identity=identity, original_root=source.original_root)
        if origin_key in self.origins and self.origins[origin_key] != origin:
            raise ValueError('Original instance has conflicting logical root identity')
        self.origins[origin_key] = origin
        return dict(origin_identity=identity, path=path, size_bytes=entry['size_bytes'])

    def reference(self, source, path, *, proof, visibility='historical_science'):
        """Keep a declared scientific locator without reading its original bytes."""
        path = o.relative(path)
        if proof is None:
            proof = files.stat_file(o.resolve_ref(source, dict(path=path)))
        if not isinstance(proof, dict) or type(proof.get('size_bytes')) is not int or proof['size_bytes'] < 0:
            raise ValueError('Scientific original lacks its declared exact size')
        key = _key(source.origin_identity, path, proof)
        entry = dict(origin_identity=source.origin_identity, path=path,
            size_bytes=proof['size_bytes'], mode=proof.get('mode', 0o400),
            storage_path=f'inherited/{o.identity_key(source.origin_identity)}/{path}', visibility=visibility)
        old = self.entries.get(key)
        if old is not None and old['size_bytes'] != entry['size_bytes']:
            raise ValueError('Scientific original has conflicting frozen sizes')
        if old is None:
            if len(self.entries) >= MAX_ENTRIES:
                raise ValueError('Fork source closure exceeds its finite entry bound')
            self.entries[key] = entry
        self.origins[o.identity_key(source.origin_identity)] = dict(
            identity=source.origin_identity, original_root=source.original_root)
        return dict(origin_identity=source.origin_identity, path=path, size_bytes=proof['size_bytes'])

    def snapshot(self, source):
        """Keep only this node's declared code directory and existing output draft."""
        from . import code_versions, contracts as c, execution, project_layout, storage_policy as storage
        if source.inherited:
            return
        root = source.storage_root
        job=o.source_job(source)
        layout=project_layout.code_roots(job.get('project'),c.workspace(root,source.step))
        code = files.check_path(layout['root'])
        context = storage.Deadline(65)
        inventory = storage.Inventory(code_versions._limits(None), context=context)
        pending = []
        if code.exists():
            for row in layout['editable']:
                directory=files.check_path(row['source'])
                if job.get('project') is not None:
                    inventory.entry()
                    if not directory.exists():raise ValueError('Declared current project draft directory is missing')
                pending.append(directory)
        while pending:
            directory = pending.pop()
            if not stat.S_ISDIR(directory.lstat().st_mode):
                raise ValueError('Current code draft requires ordinary directories')
            with os.scandir(directory) as items:
                for item in items:
                    inventory.entry()
                    path = files.check_path(directory / item.name)
                    code_versions._relative(path.relative_to(code).as_posix(), git_path=True)
                    info = path.lstat()
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif stat.S_ISREG(info.st_mode):
                        inventory.file(info.st_size)
                        self.add(source, path.relative_to(root).as_posix(), visibility='private_snapshot')
                    else:
                        raise ValueError('Current code draft contains a nonregular file')
        seal_path = execution.origin(root, source.step) / 'result-identity.json'
        seal = _json(seal_path) if seal_path.exists() else None
        record = execution.record(root, source.step)
        names = ['result.pending.json']
        if seal is None:
            names.append('science-output.json')
        for name in names:
            path = record / name
            if path.exists() or path.is_symlink():
                if path.lstat().st_size>32*1024*1024:
                    raise ValueError('Current output draft exceeds its finite JSON bound')
                self.add(source, path.relative_to(root).as_posix(), visibility='private_snapshot')

    def control(self, source, ref):
        """Follow control-record fields, never references guessed from science text."""
        source = o.source_for_ref(source, ref)
        raw = Path(ref.get('snapshot', ref['path']))
        if raw.is_absolute() and not raw.is_relative_to(source.original_root):
            # Original control refs may retain an ancestor's absolute spelling.
            # Only the existing bound origins can supply that owner.
            source,_ = o.material_owner(source,dict(source=str(raw)))
        path = o.relative(raw.relative_to(source.original_root).as_posix() if raw.is_absolute() else raw.as_posix())
        binding = self.add(source, path, proof=ref)
        key = _key(source.origin_identity, path, binding)
        value = _json(o.resolve_ref(source, binding)) if path.endswith('.json') else {}
        if key in self.control_done or key in self.control_active:
            return value
        self.control_active.add(key)

        def follow(proof):
            if isinstance(proof, dict) and isinstance(proof.get('path'), str):
                return self.control(source, proof)
            return None

        # These fields are control refs in job, seal, compiler,
        # provenance and recovery records. Raw/output/material fields below
        # remain scientific locators and are never read as control JSON.
        for name in ('job', 'job_ref', 'task_ref', 'configuration', 'native_intent',
                'root_intent_ref', 'source_provenance', 'observations', 'native_authorship',
                'formal_review', 'pending_executions',
                'contract', 'accepted', 'completed', 'submission', 'native_terminal',
                'observation', 'retirement', 'parent_ref', 'previous_ref', 'execution_authority',
                'request', 'intent', 'outcome', 'header', 'decision', 'admission',
                'allocation', 'pending_set', 'query', 'seal_ref', 'host_preparation',
                'original_permit', 'original_intent',
                'original_experiment_config', 'settings', 'breadth_snapshot'):
            follow(value.get(name))
        follow(value.get('history',{}).get('breadth_snapshot'))
        table=value.get('table')
        for mechanism in (table.get('mechanisms',{}) if isinstance(table,dict) else {}).values():
            terminal=mechanism.get('last_terminal')
            if terminal is not None:
                follow(dict(terminal['observation_ref'],
                    origin_identity=terminal['origin_identity']))
                follow(terminal.get('recovery_ref'))
        for name in ('execution_qualifications',):
            for proof in value.get(name, []):
                follow(proof)
        for name in ('request', 'unavailable', 'review'):
            follow((value.get('evidence_return') or {}).get(name))
        previous = value.get('previous')
        if isinstance(previous, dict):
            if 'path' in previous:follow(previous)
            else:
                for proof in previous.values():follow(proof)
        for name in ('job','job_ref','native_intent','configuration','accepted','prepared_ref'):
            follow((value.get('binding') or {}).get(name))
        follow((value.get('subject') or {}).get('producer_seal'))
        follow((value.get('requester') or {}).get('seal'))
        execution_ref = value.get('execution_ref') or {}
        for link in execution_ref.get('chain', []):
            for proof in link.values():
                follow(proof)
        for proof in value.get('control_cut', {}).get('correction_events', []):
            follow(proof)
        for group in [*value.get('material_refs', []), *value.get('relayed_material_refs', []),
                *[dict(list_ref=row['material_ref']['list_ref']) for row in value.get('selected_inputs', [])]]:
            follow(group['list_ref'])
        recoveries=value.get('history',{}).get('execution_recoveries',[])
        if (type(value.get('step')) is int and path==f'jobs/{value["step"]:03d}.json'
                and value.get('role') in {'H','W','A','E'}):
            recoveries=o.recovery_refs(o.at_step(source,value['step']),value)
        for proof in recoveries:
            follow(proof)
        for row in value.get('compiled_implementations', []):
            follow(row['compiler']); follow(row.get('client_binding'))
            self.science_ref(source,row['material'])
        for row in [*value.get('managed_calls', []), *value.get('evidence', []), *value.get('unknown', [])]:
            if isinstance(row, dict):
                self.call(source, row)
        for attempt in value.get('attempts', []):
            if path.endswith('/pending-execution-set.json'):
                for proof in attempt.values():follow(proof)
            else:
                for name in ('intent','outcome','header'):
                    follow(attempt.get(name))
        if path.endswith('/pending-execution-set.json'):
            for proof in value.get('host_preparations',[]):follow(proof)
            for allocation in value.get('allocations',[]):
                follow(allocation['allocation'])
                for proof in [*allocation['requests'],*allocation.get('records',[])]:follow(proof)
                # These are the existing pending inventory's mechanical refs;
                # opaque result field names do not change their control role.
                for proof in allocation.values():
                    if isinstance(proof,dict) and {'path','size_bytes'}<=set(proof):follow(proof)
        if isinstance(value.get('row'), dict):
            self.call(source, value['row'])
        if isinstance(value.get('old_row'), dict):
            self.call(source, value['old_row'])
        if path.endswith('/compiler.json'):
            follow(value.get('capability'))
            if 'code_version' in value:
                from . import execution_inputs
                step = _json(o.resolve_ref(source, value['job']))['step']
                call_id = execution_inputs._compiler_call_id(Path(source.original_root), step, dict(path=path))
                self.code(source, step, call_id, value['code_version'])
            else:
                self.science_ref(source,value['author'])
            self.science_ref(source,value['output'])
        if path.endswith('/accepted.json'):
            step = value['scope']['step']
            if value.get('call_id') != Path(path).parent.name:
                raise ValueError('Fork accepted control changed its exact original call')
            if 'code_version' in value:
                self.code(source, step, value['call_id'], value['code_version'])
            else:
                self.science_ref(source,value['script'])
            attempt = (value.get('native_attempt') or {}).get('number', 0)
            record = f'native-evidence/{step:03d}' + (f'/continuations/{attempt:03d}' if attempt else '')
            self.control(source, dict(path=record + '/experiment-config.json'))
            view = (value.get('input_sources') or {}).get('view')
            if isinstance(view, dict):
                for name in ('job', 'configuration', 'native_intent'):
                    follow(view.get(name))
                for proof in view.get('evidence',[]):
                    self.science_ref(source,proof)
            tcad=value.get('tcad') or {}
            for name in ('prepared_ref','authority'):
                follow(tcad.get(name))
            authority = value.get('execution_authority') or {}
            for name in ('request', 'decision', 'authorization', 'contract'):
                follow(authority.get(name))
        if path.endswith('/execution-observation.json'):
            from . import experiments
            accepted=follow(value['accepted']);completed=follow(value['completed'])
            _,controls=experiments._observation_file_roles(value,accepted,completed)
            for proof in value.get('proofs',[]):
                original=proof.get('source') if isinstance(proof.get('source'),dict) else proof
                if (str(Path(source.original_root)/proof['path']) in controls
                        or str(Path(source.original_root)/original['path']) in controls):
                    follow(proof)
                    if original is not proof:follow(original)
                else:
                    self.science_ref(source,proof)
        self.control_active.remove(key)
        self.control_done.add(key)
        return value

    def code(self, source, step, call_id, version):
        from . import code_versions, project_layout
        code_versions._version(version)
        holder = source.storage_root.parents[1] if source.inherited else source.storage_root
        key = (o.identity_key(source.origin_identity), step, call_id)
        transfer = dict(source_root=str(holder), step=step, call_id=call_id, code_version=copy.deepcopy(version))
        if key in self.code_transfers and self.code_transfers[key] != transfer:
            raise ValueError('Fork code call has conflicting exact versions')
        if key not in self.code_transfers:
            paths=code_versions.verify(holder,step=step,call_id=call_id,code_version=version)
            project=o.source_job(o.at_step(source,step)).get('project')
            project_layout.validate_code(project,version,
                purpose='compiler' if version['selection']=='file' else 'run',paths=paths)
        self.code_transfers[key] = transfer

    def science_ref(self, source, proof):
        if isinstance(proof.get('source'),dict):proof=proof['source']
        source=o.source_for_ref(source,proof)
        raw=Path(proof.get('snapshot',proof.get('source',proof['path'])))
        if raw.is_absolute():
            if not raw.is_relative_to(source.original_root):
                origins=(source.snapshot.origins if source.inherited else
                    (o._manifest(source.storage_root) or {}).get('origins',{}))
                if not any(raw.is_relative_to(origin['original_root']) for origin in origins.values()):
                    return  # The exact external locator stays in its typed owner record.
                source,path=o.material_owner(source,dict(source=str(raw)))
                raw=Path(path)
            else:
                raw=raw.relative_to(source.original_root)
        path=o.relative(raw.as_posix())
        if path.startswith('host-runtime/code-versions/exports/'):
            return  # An expired execution view is not a permanent original.
        if self.entry(source.origin_identity,path,required=False) is None:
            self.reference(source,path,proof=proof)

    def call(self, source, row):
        """Keep the exact accepted/terminal controls named by one frozen call row."""
        for name in ('accepted', 'completed', 'terminal', 'observation', 'retirement'):
            proof = row.get(name)
            if proof is not None:
                self.control(source, proof)
        for proof in [*row.get('originals', []), *row.get('outputs', [])]:
            self.science_ref(source,proof)

    def settings(self, source):
        self.add(source, 'settings.json')
        self.add(source, 'settings-identity.json')
        job = o.source_job(source)
        revision = job.get('configuration_revision')
        if revision is not None:
            for number in range(1, revision['revision'] + 1):
                self.add(source, f'host-control/configuration/revisions/{number:06d}.json')
                self.add(source, f'host-control/configuration/identities/{number:06d}.json')
            self.add(source, f'host-control/configuration/bindings/{source.step:03d}.json')
        settings = o.source_settings(source)
        if settings.get('source_provenance') is not None:
            self.control(source, settings['source_provenance'])
        for name in ('formal_review_policy', 'execution_authority_policy'):
            policy = settings.get(name) or {}
            if policy.get('contract') is not None:
                self.control(source, policy['contract'])
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
                    proof=marker if number == marker['seq'] else None)
                event = _json(o.resolve_ref(source, ref))
                if event.get('seq') != number or event.get('parent_ref') != predecessor:
                    raise ValueError('Final source phase ledger chain changed')
                predecessor = dict(path=str(Path(source.original_root) / ref['path']))
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
            boundary = dict(seq=event['seq'], path=f'control-ledger/{event["seq"]:06d}.json')
        if boundary is None:
            return None
        return self.add(source, f'control-ledger/{boundary["seq"]:06d}.json', proof=boundary)

    def task(self, source, *, selected=False):
        from . import contracts as c
        step = source.step
        job = o.source_job(source)
        if selected:
            from . import handoff_state
            job = handoff_state.material_inputs(o.context_root(source), job, sources={}, source=source)
        key = (o.identity_key(source.origin_identity), step)
        if key in self.tasks:
            return self.tasks[key]
        if key in self.task_active:
            raise ValueError('Fork source task ancestry cycle')
        self.task_active.add(key)
        self.settings(source)
        job_ref = self.add(source, f'jobs/{step:03d}.json')
        task_ref = self.add(source, f'project/nodes/{step:03d}/science-task.json')
        self.control(source, job_ref)
        boundaries = [job[field] for field in ('closeout_begin', 'normal_resume') if job.get(field) is not None]
        for boundary in boundaries:
            for number in range(1, boundary['seq'] + 1):
                self.add(source, f'control-ledger/{number:06d}.json',
                    proof=boundary if number == boundary['seq'] else None)
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
            if marker.get('normal_step'):
                directory += f'/nodes/{marker["normal_step"]:03d}'
            elif marker.get('repair_step'):
                directory += f'/repairs/{marker["repair_step"]:03d}'
            screen_ref = self.add(owner, directory + '/screen.json', proof=marker)
            screen = _json(o.resolve_ref(owner, screen_ref))
            self.add(owner, directory + '/shortlist.json', proof=screen['shortlist_ref'])
        if (o.source_settings(source).get('correction_policy') is not None or job.get('correction_request')) and 'control_cut' not in job:
            self.blocked.append(dict(code='fork_input_missing', input='control_cut.correction_events',
                reason='historical_correction_cut_unverifiable', path=f'jobs/{step:03d}.json'))
        # Only this task's declared input locators travel. Ancestor inputs do
        # not become an input-body closure or another retained copy.
        for row in ([*job.get('global_materials', []), *job.get('direct_materials', []),
                    *job.get('direct_refs', []), *job.get('history', {}).get('files', []),
                    *job.get('history', {}).get('execution_recovery_materials', [])] if selected else []):
            self.add(source, f'project/nodes/{step:03d}/' + o.relative(row['path']),
                proof=row, visibility='scientific_input' if selected else 'historical_science')
            if 'source' in row:
                self.science_ref(source,row)
        repair = job.get('engineering_repair')
        if repair is not None and selected:
            for name in ('source.py', 'artifact'):
                proof = repair['objects'].get(name)
                if proof is not None:
                    self.add(source, f'project/nodes/{step:03d}/inputs/repair/{name}',
                        proof=proof, visibility='scientific_input' if selected else 'historical_science')
        if job.get('fork_source') is not None:
            original_ref = job['fork_source']['job']
            owner = o.source_for_ref(source, original_ref)
            o.resolve_ref(owner, original_ref)
            self.task(owner)
        for row in job.get('history', {}).get('nodes', []):
            if not 0 < row['step'] < step:
                raise ValueError('Original task dependency crosses its exact input boundary')
            self.sealed(o.dependency(source, row['step'], seal_ref=row['seal_ref']), row['seal_ref'])
        if job.get('formal_review') is not None:
            request_source = o.source_for_ref(source, job['formal_review'])
            request = _json(o.resolve_ref(request_source, job['formal_review']))
            for producer_step, seal_ref in [(request['requester']['step'], request['requester']['seal']),
                    (request['subject']['producer_step'], request['subject']['producer_seal'])]:
                owner = o.dependency(request_source, producer_step, seal_ref=seal_ref)
                self.sealed(owner, seal_ref)
        if selected:
            from . import experiment_client, experiments
            if source.inherited:
                calls = [row for row in source.entries if row['origin_identity'] == source.origin_identity
                    and row['path'].endswith('/accepted.json')
                    and row['path'].startswith(f'host-runtime/experiment-control/{step:03d}/records/calls/')]
                for row in calls:
                    self.control(source, row)
            else:
                records = experiments.Records(dict(record_dir=str(experiment_client.record_directory(source.storage_root, step))))
                for directory in records.calls():
                    self.control(source, dict(path=directory.relative_to(source.storage_root).as_posix() + '/accepted.json'))
            self.compilations(source)
            seal=f'native-evidence/{step:03d}/result-identity.json'
            if o.has_original(source,seal):
                # This private original closes exact prior calls/compilers. It
                # is not a child seal or a selected scientific output.
                self.control(source,dict(path=seal))
        self.task_active.remove(key)
        self.tasks[key] = (job, job_ref, task_ref)
        return self.tasks[key]

    def sealed(self, source, seal_binding):
        key = (o.identity_key(source.origin_identity), source.step)
        if key in self.seals:
            return self.seals[key]
        job, job_ref, _ = self.task(source)
        step = source.step
        seal_path = f'native-evidence/{step:03d}/result-identity.json'
        seal_ref = self.add(source, seal_path, proof=seal_binding)
        seal = _json(o.resolve_ref(source, seal_ref))
        if seal.get('job_ref') != dict(path=str(Path(source.original_root) / job_ref['path'])):
            raise ValueError('Inherited seal lost its exact original job')
        raw = Path(seal['raw_ref']['path'])
        record = o.relative(raw.relative_to(source.original_root).parent.as_posix())
        if not (record == f'native-evidence/{step:03d}' or record.startswith(f'native-evidence/{step:03d}/continuations/')):
            raise ValueError('Inherited seal lost its exact native output record')
        self.records[f'{o.identity_key(source.origin_identity)}:{step}'] = record
        self.add(source, record + '/science-output.json', proof=seal['raw_ref'], visibility='historical_science')
        outcome = _json(o.resolve_ref(source, dict(path=record + '/native-outcome.json')))
        if outcome.get('stop_reason') == 'native_failure_event':
            self.add(source, record + '/native.jsonl', visibility='host_proof')
        self.add(source, f'native-evidence/{step:03d}/result.json', proof=seal['result_ref'], visibility='historical_science')
        self.add(source, f'project/nodes/{step:03d}/conclusion.json', proof=seal['raw_ref'], visibility='historical_science')
        for material in seal.get('materials', []):
            owner, path = o.material_owner(source, material)
            self.reference(owner, path, proof=material)
        self.control(source, seal_ref)
        self.attempts(source, selected=False)
        self.seals[key] = dict(step=step, origin_identity=source.origin_identity, job_ref=job_ref, seal_ref=seal_ref)
        return self.seals[key]

    def compilations(self, source):
        """Retain current selected-task compiler controls without a second index."""
        base = f'native-evidence/{source.step:03d}/compiled-implementations/'
        if source.inherited:
            paths = sorted(row['path'] for row in source.entries
                if row['origin_identity'] == source.origin_identity
                and row['path'].startswith(base) and len(Path(row['path']).parts) == 5
                and row['path'].endswith('/compiler.json'))
        else:
            paths = sorted(path.relative_to(source.storage_root).as_posix()
                for path in (source.storage_root / base).glob('*/compiler.json'))
        for path in paths:
            self.control(source, dict(path=path))
        # Current original author controls keep their exact presence in the cut;
        # they do not need a legacy compiler ordering/absence projection.
        path = f'host-recovery/author-closeout/{source.step:03d}-successor.json'
        present = (bool(source._index.get((o.identity_key(source.origin_identity), path)))
            if source.inherited else (source.storage_root / path).exists())
        if present:
            self.control(source, dict(path=path))

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
        task_ref = dict(path=str(Path(source.original_root) / f'jobs/{step:03d}.json'))
        for record, intent in paths:
            if intent.get('task_ref') != task_ref:
                raise ValueError('Original native intent changed its exact task')
            visibility = 'effect_only' if selected else 'host_proof'
            self.add(source, record + '/native-intent.json', visibility=visibility)
            if not selected:
                for name in ('experiment-config.json', 'checkpoint.json', 'stage-end.json', 'work-evidence.json'):
                    optional_path = record + '/' + name
                    exists = ((source.storage_root / optional_path).exists() if source.entries is None else
                        any(row['origin_identity'] == source.origin_identity and row['path'] == optional_path for row in source.entries))
                    if exists:
                        self.control(source,dict(path=optional_path))
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
    """Inspect an exact step or an exact {origin_identity,path,size_bytes} job ref."""
    from . import contracts as c
    root = files.check_path(Path(parent_root).absolute())
    initial = c.settings(root)
    from .protocol import CURRENT
    if type(initial.get('handoff_protocol')) is not int or initial['handoff_protocol'] != CURRENT:
        raise ValueError(f'Fork source requires frozen protocol {CURRENT}')
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
    from .protocol import require_job_format
    require_job_format(o.source_job(source))
    closure = _Closure()
    closure.blocked.extend(_parent_state(root))
    job, job_ref, task_ref = closure.task(source, selected=True)
    if isinstance(selected_job_ref, dict):
        if any(selected_job_ref.get(field) != job_ref[field] for field in ('origin_identity', 'path', 'size_bytes')):
            raise ValueError('Selected fork job identity changed')
    prefix = []
    visible_sources = {}
    science_nodes = []
    frozen_prefix=job.get('history',{}).get('prefix')
    if frozen_prefix is not None:
        from . import accepted_prefix
        namespace=accepted_prefix._view_key(frozen_prefix)
        owner=o.source_for_ref(source,dict(origin_identity=frozen_prefix['branch'],path='settings.json'))
        for ordinal in range(1,frozen_prefix['count']+1):
            ordinal_ref=closure.add(owner,f'{accepted_prefix.MEMBERS}/{namespace}/ordinals/{ordinal:09d}.json')
            binding=_json(o.resolve_ref(owner,ordinal_ref))
            member_ref=closure.add(owner,f'{accepted_prefix.MEMBERS}/{namespace}/'
                f'{o.identity_key(binding["origin_identity"])}/{binding["step"]:03d}.json')
            member=_json(o.resolve_ref(owner,member_ref))
            if (member['branch']!=frozen_prefix['branch'] or member['ordinal']!=ordinal
                    or member['step']>frozen_prefix['cutoff']):
                raise ValueError('Fork history member crosses its frozen input cut')
            nav_ref=closure.add(owner,frozen_prefix['visibility_root']['path']+'/'+member['navigation']['path'])
            event_owner=o.source_for_ref(owner,member['scientific_event'])
            event_path=Path(member['scientific_event']['path'])
            if event_path.is_absolute():event_path=event_path.relative_to(event_owner.original_root)
            event_ref=closure.add(event_owner,o.relative(event_path.as_posix()))
            science_nodes.append(dict(step=member['step'],seal_ref=dict(member['seal_ref'],
                origin_identity=member['origin_identity']),member_ref=member_ref,navigation_ref=nav_ref,
                scientific_event=event_ref))
    else:
        raise ValueError('Fork task lacks its exact accepted input prefix')
    if job.get('formal_review') is not None:
        # A reviewer sees its neutral subject binding. Host routing and science
        # investment retain the requester's exact accepted prefix separately.
        request_source = o.source_for_ref(source, job['formal_review'])
        request = _json(o.resolve_ref(request_source, job['formal_review']))
        requester = o.dependency(request_source, request['requester']['step'],
            seal_ref=request['requester']['seal'])
        requester_job = o.source_job(requester)
        science_nodes = [*requester_job.get('history', {}).get('nodes', []),
            dict(step=requester.step, seal_ref=request['requester']['seal'])]
    for row in science_nodes:
        if type(row.get('step')) is not int or row['step'] >= step:
            raise ValueError('Selected task history crosses its input boundary')
        ancestor = visible_sources.get(row['step']) or o.dependency(source, row['step'], seal_ref=row['seal_ref'])
        if ancestor is None:
            raise ValueError('Selected history has no exact visible sealed original')
        entry=dict(closure.sealed(ancestor, row['seal_ref']))
        for name in ('member_ref','navigation_ref','scientific_event'):
            if name in row:entry[name]=row[name]
        prefix.append(entry)
        visible_sources[row['step']] = ancestor
    if len({row['step'] for row in prefix}) != len(prefix):
        raise ValueError('Selected task history is ambiguous')
    native_intent = closure.attempts(source, selected=True)
    closure.snapshot(source)
    phase_ref = closure.phase(source, job)
    selected_settings = o.source_settings(source)
    public_snapshots = []
    declarations = dict(declarations=[], globals=[])
    declaration_ref = selected_settings.get('source_provenance')
    if declaration_ref is not None:
        declarations = _json(o.resolve_ref(source, declaration_ref))
    closure.limitations.append(dict(code='unfrozen_native_public_reads',
        detail='Scientific and public inputs remain direct originals; deleting them can make their consumers unavailable.'))
    # Source settings contracts retain root-relative public identities, while
    # input globals are the selected job's frozen files above.
    route_fields = ('role', 'instruction', 'evidence_return', 'correction_request', 'author_correction',
        'purpose', 'source_cutoff', 'delivery_sources', 'engineering_repair', 'final_report', 'supervision_request')
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
    result = dict(version=2, kind='scientific_fork', source=dict(origin_identity=source.origin_identity,
        step=step, job_ref=job_ref, science_task_ref=task_ref, native_intent_path=native_intent, intent_kind=_kind(job)),
        cut=dict(history=copy.deepcopy(job.get('history', {})),
            correction_events=[dict(ref, origin_identity=ref.get('origin_identity', source.origin_identity))
                for ref in job.get('control_cut', {}).get('correction_events', [])],
            phase_original_ref=phase_ref,
            execution_recoveries=[dict(ref, origin_identity=ref.get('origin_identity', history_owner.origin_identity))
                for ref in o.recovery_refs(source,job)]),
        prefix_entries=prefix, entries=list(closure.entries.values()), origins=closure.origins,
        native_records=closure.records,
        directory_inventory=closure.directory_inventory, bootstrap=boot, ancestry=ancestry,
        selected_settings=selected_settings, public_snapshots=public_snapshots,
        selected_globals=[dict(material=copy.deepcopy(material),
            original_ref=closure.add(source, f'project/nodes/{step:03d}/' + material['path'],
                proof=material, visibility='scientific_input')) for material in job['global_materials']],
        source_declarations=declarations,
        inherited_costs=_inherited_costs(closure, prefix),
        limitations=closure.limitations, blocked_reasons=closure.blocked,
        source_paths=closure.paths, source_root=str(root), code_transfers=list(closure.code_transfers.values()))
    return result


def _existing_directory(path):
    path = files.check_path(path)
    while not path.exists():
        path = path.parent
    if not path.is_dir():
        raise ValueError('Fork storage ancestor is not a directory')
    return path


def storage_blockers(plan, target_directory):
    """Inspect only files this operation copies; scientific inputs stay external."""
    _existing_directory(Path(target_directory).absolute())
    for entry in plan['entries']:
        if entry['visibility'] in {'scientific_input', 'historical_science'}:
            continue
        source = files.check_path(Path(plan['source_paths'][_key(entry['origin_identity'], entry['path'], entry)]))
        info = source.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Fork original is not a regular file')
    return []

def copy_source(plan, target_store):
    """Copy necessary controls and current drafts; retain scientific locators only."""
    if plan.get('blocked_reasons'):
        raise ValueError('Fork source has unresolved original effects')
    target_store = files.check_path(Path(target_store).absolute())
    blockers = storage_blockers(plan, target_store)
    if blockers:
        raise files.SharingUnsupported(blockers[0]['reason'])
    target_store.mkdir(parents=True, exist_ok=True, mode=0o700)
    from . import storage_policy as storage
    source_root = files.check_path(Path(plan['source_root']).absolute())
    context = storage.Deadline(65)
    policy = storage.for_root(source_root)
    inventory = storage.Inventory(policy, context=context)
    generations = {}
    for entry in plan['entries']:
        if entry['visibility'] in {'scientific_input', 'historical_science'}:
            continue
        inventory.entry(); inventory.file(entry['size_bytes'])
        key = _key(entry['origin_identity'], entry['path'], entry)
        source = Path(plan['source_paths'][key])
        proof = files.stat_file(source, context=context)
        if proof['size_bytes'] != entry['size_bytes']:
            raise ValueError('Fork source size changed after preview')
        generations[key] = proof
        target = target_store / entry['storage_path']
        files.copy_original(source, target, dict(entry, kind='file'), context=context)
    for entry in plan['entries']:
        if entry['visibility'] in {'scientific_input', 'historical_science'}:
            continue
        key = _key(entry['origin_identity'], entry['path'], entry)
        after = files.stat_file(Path(plan['source_paths'][key]), context=context)
        before = generations[key]
        if (after['size_bytes'] != before['size_bytes'] or after['mode'] != before['mode']
                or after['source_stat'][:3] != before['source_stat'][:3]):
            raise ValueError('Fork source changed during retention; preview is stale')
    manifest = {key: copy.deepcopy(value) for key, value in plan.items() if key not in SOURCE_LOCATORS}
    files.durable_json(target_store / 'origins.json', manifest['origins'])
    files.durable_json(target_store / 'bootstrap.json', manifest['bootstrap'])
    files.durable_json(target_store / 'manifest.json', manifest)
    return manifest


def validate_manifest(child_root, *, full=False):
    manifest = o._manifest(child_root)
    if manifest is None or manifest.get('version') != 2 or manifest.get('kind') != 'scientific_fork':
        raise ValueError('Fork manifest identity changed')
    if len(manifest['entries']) > MAX_ENTRIES or sum(row['size_bytes'] for row in manifest['entries']
            if row['visibility'] not in {'scientific_input', 'historical_science'}) > MAX_BYTES:
        raise ValueError('Fork source closure exceeds its finite bounds')
    seen = set()
    snapshot = o._snapshot(manifest) if full else None
    for entry in manifest['entries']:
        key = _key(entry['origin_identity'], entry['path'], entry)
        if key in seen:
            raise ValueError('Fork manifest repeats an original identity')
        seen.add(key)
        if full and entry['visibility'] not in {'scientific_input', 'historical_science'}:
            source = o._source(child_root, manifest, entry['origin_identity'], manifest['source']['step'], snapshot=snapshot)
            o.resolve_ref(source, entry)
    store = Path(child_root) / o.FORK_DIRECTORY
    if _json(store / 'origins.json') != manifest['origins'] or _json(store / 'bootstrap.json') != manifest['bootstrap']:
        raise ValueError('Fork private bindings changed')
    return manifest


def selected_task(child_root, *, full=False, material_sources=None):
    manifest = validate_manifest(child_root, full=True) if full else o._manifest(child_root)
    source = o.selected_source(child_root)
    from . import handoff_state, contracts as c
    material_sources = {} if material_sources is None else material_sources
    job = handoff_state.material_inputs(child_root, o.source_job(source),
        sources=material_sources, source=source)
    from .protocol import require_job_format
    require_job_format(job)
    task = _json(o.resolve_ref(source, manifest['source']['science_task_ref']))
    fields = ('global_materials', 'direct_refs', 'direct_originals', 'material_refs', 'selected_inputs', 'history')
    canonical = c.job_record(job)
    return dict(source=source, job=job, material_sources=material_sources,
        science_task=task, settings=o.source_settings(source),
        phase_original_ref=manifest['cut'].get('phase_original_ref'),
        input_binding={field: copy.deepcopy(canonical.get(field, {} if field == 'history' else []))
            for field in fields},
        source_job_ref=manifest['source']['job_ref'], source_task_ref=manifest['source']['science_task_ref'])


def bootstrap(child_root):
    return copy.deepcopy(validate_manifest(child_root)['bootstrap'])


def prefix_nodes(child_root):
    manifest = o._manifest(child_root)
    result = []
    snapshot = o._snapshot(manifest)
    for row in manifest['prefix_entries']:
        source = o._source(child_root, manifest, row['origin_identity'], row['step'], snapshot=snapshot)
        result.append(dict(step=row['step'], role=o.source_job(source)['role'],
            origin_identity=row['origin_identity'], job_ref=row['job_ref']))
    return result
