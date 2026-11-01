"""Portable, instrumented handoff scaling probe; synthetic science, no launch.

Explicit check_ entry, outside default collection. Wall times include counting
overhead and carry no machine-dependent threshold. Growing payloads or old IO
are reported as scaling gaps even when the verification-boundary checks pass.
"""
from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, handoff_state
from scidiscovery.research_loop import history, native, shared_input_views


TEXT = 'Synthetic finding: the scientific question remains unanswered.'
LIMITATIONS = ['Authored fixture only; no scientific evidence or qualification.']
ARTIFACT = 'Exact small synthetic artifact.\n'


def _producer(root, path):
    try:
        parts = Path(path).absolute().relative_to(root).parts
    except (TypeError, ValueError):
        return None
    if len(parts) > 1 and parts[0] == 'native-evidence' and parts[1].isdecimal():
        return int(parts[1])
    if len(parts) > 2 and parts[:2] == ('project', 'nodes') and parts[2].isdecimal():
        return int(parts[2])
    if len(parts) == 2 and parts[0] == 'jobs' and Path(parts[1]).stem.isdecimal():
        return int(Path(parts[1]).stem)
    if len(parts) == 3 and parts[:2] == ('host-history', 'observations') and Path(parts[2]).stem.isdecimal():
        return int(Path(parts[2]).stem)
    if len(parts) > 2 and parts[:2] == ('host-history', 'execution-recoveries') and parts[2].isdecimal():
        return int(parts[2])
    return None


@contextmanager
def _counts(monkeypatch, root, current):
    counts = Counter()
    root = Path(root).absolute()
    original_stat = os.stat
    def relative_parts(path):
        try:
            return Path(path).absolute().relative_to(root).parts
        except (TypeError, ValueError):
            return None
    def category(step):
        if step is None:
            return 'other'
        return ('ancient' if step < current-1 else 'direct' if step == current-1
            else 'current' if step == current else 'next')
    def file_category(path):
        parts = relative_parts(path)
        if parts and parts[0] == 'control-ledger':
            return 'ledger'
        if parts and parts[:2] == ('host-history', 'objects'):
            return 'history_context'
        return category(_producer(root, path))
    def area(parts):
        if parts and parts[0] in ('jobs', 'native-evidence'):
            return parts[0].replace('-', '_')
        if parts[:2] == ('host-history', 'execution-recoveries'):
            return 'execution_recovery'
        if parts[:2] == ('host-delivery', 'audit-recovery'):
            return 'audit_recovery'
        if 'candidate-audit' in parts:
            return 'candidate_audit'
        return 'other'
    def filesystem(operation, path):
        parts = relative_parts(path)
        if parts is not None:
            owner = file_category(path)
            counts[f'{operation}_{owner}'] += 1
            counts[f'{operation}_area_{area(parts)}_{owner}'] += 1
    class Entry:
        """Preserve DirEntry behavior while separating metadata requests."""
        def __init__(self, entry):
            self.entry = entry
        def __getattr__(self, name):
            return getattr(self.entry, name)
        def __fspath__(self):
            return os.fspath(self.entry)
        def stat(self, *, follow_symlinks=True):
            filesystem('direntry_stat' if follow_symlinks else 'direntry_lstat', self.entry.path)
            return self.entry.stat(follow_symlinks=follow_symlinks)
        def is_dir(self, *, follow_symlinks=True):
            filesystem('direntry_is_dir', self.entry.path)
            return self.entry.is_dir(follow_symlinks=follow_symlinks)
        def is_file(self, *, follow_symlinks=True):
            filesystem('direntry_is_file', self.entry.path)
            return self.entry.is_file(follow_symlinks=follow_symlinks)
        def is_symlink(self):
            filesystem('direntry_is_symlink', self.entry.path)
            return self.entry.is_symlink()
    class Scan:
        def __init__(self, iterator):
            self.iterator = iterator
        def __iter__(self):
            return self
        def __next__(self):
            entry = next(self.iterator)
            filesystem('scandir_entries', entry.path)
            return Entry(entry)
        def __enter__(self):
            self.iterator.__enter__()
            return self
        def __exit__(self, *args):
            return self.iterator.__exit__(*args)
        def close(self):
            self.iterator.close()
    with monkeypatch.context() as patch:
        for name in ('stat', 'lstat'):
            original = getattr(os, name)
            def stat(path, *args, _original=original, _name=name, **kwargs):
                operation = 'lstat' if _name == 'stat' and kwargs.get('follow_symlinks') is False else _name
                filesystem(operation, path)
                return _original(path, *args, **kwargs)
            patch.setattr(os, name, stat)
        original_scandir = os.scandir
        def scandir(path='.'):
            filesystem('scandir_calls', path)
            return Scan(original_scandir(path))
        patch.setattr(os, 'scandir', scandir)
        original_read_bytes = Path.read_bytes
        def read_bytes(path, *args, **kwargs):
            parts = relative_parts(path)
            if parts and parts[-1].endswith('.json'):
                counts[f'physical::Path.read_bytes::{"/".join(parts)}'] += 1
            if parts == ('settings.json',):
                counts['path::Path.read_bytes::settings.json'] += 1
            return original_read_bytes(path, *args, **kwargs)
        patch.setattr(Path, 'read_bytes', read_bytes)
        original_read_text = Path.read_text
        def read_text(path, *args, **kwargs):
            parts = relative_parts(path)
            if parts and parts[-1].endswith('.json'):
                counts[f'physical::Path.read_text::{"/".join(parts)}'] += 1
            return original_read_text(path, *args, **kwargs)
        patch.setattr(Path, 'read_text', read_text)
        scientific_reader = ('read_scientific_source' if hasattr(c, 'read_scientific_source')
            else 'read_scientific_output')
        for name, label in (('read', 'read'), (scientific_reader, 'read_scientific_output')):
            original = getattr(c, name)
            def read(path, *args, _original=original, _name=label, **kwargs):
                owner = file_category(path)
                counts[f'{_name}_{owner}'] += 1
                parts = relative_parts(path)
                if parts is not None:
                    counts[f'path::c.{_name}::{"/".join(parts)}'] += 1
                # The spy's byte measurement must not inflate production stat counts.
                counts[f'{_name}_bytes_{owner}'] += original_stat(path).st_size
                if owner == 'ancient':
                    frame = sys._getframe(1)
                    counts[f'ancient_read_caller::{frame.f_code.co_filename}::{frame.f_lineno}::{frame.f_code.co_name}'] += 1
                return _original(path, *args, **kwargs)
            patch.setattr(c, name, read)
        for name in ('validate', 'validate_original'):
            original = getattr(c, name)
            def validate(value, *args, _original=original, _name=name, **kwargs):
                step = int(value.name) if _name == 'validate' else value.step
                counts[f'deep_{_name}_{category(step)}'] += 1
                return _original(value, *args, **kwargs)
            patch.setattr(c, name, validate)
        original_inputs = c._validate_inputs
        def inputs(root, work, job, *args, **kwargs):
            counts[f'deep_inputs_{category(job["step"])}'] += 1
            return original_inputs(root, work, job, *args, **kwargs)
        patch.setattr(c, '_validate_inputs', inputs)
        for name in ('_validate_managed', '_observe_managed_calls', '_observe_scientific_outputs',
                '_accept_managed_input_coverage', 'validate_observation', 'validate_managed_original'):
            original = getattr(history, name)
            def gate(root_or_source, job, *args, _original=original, _name=name, **kwargs):
                counts[f'deep_{_name}_{category(job["step"])}'] += 1
                return _original(root_or_source, job, *args, **kwargs)
            patch.setattr(history, name, gate)
        accept = c.accept_current_inventory
        def owner(root, step, *args, **kwargs):
            counts[f'accept_owner_{category(step)}'] += 1
            return accept(root, step, *args, **kwargs)
        patch.setattr(c, 'accept_current_inventory', owner)
        read_events = budget._read_events
        def ledger(*args, **kwargs):
            counts['ledger_reads'] += 1
            observed = read_events(*args, **kwargs)
            counts['ledger_events_loaded'] += len(observed[0])
            return observed
        patch.setattr(budget, '_read_events', ledger)
        sequence = history.source_sequence
        def sources(*args, **kwargs):
            result = sequence(*args, **kwargs)
            counts['source_sequence_calls'] += 1
            counts['source_sequence_nodes_returned'] += len(result)
            return result
        patch.setattr(history, 'source_sequence', sources)
        yield counts


def _terminal(root, step, job):
    """The same inert terminal receipts as finish, without a second job build."""
    work = c.workspace(root, step)
    record = c.host_record(work)
    (work / 'artifacts').mkdir(parents=True, exist_ok=True)
    (work / 'artifacts/subject.txt').write_text(ARTIFACT)
    intent = dict(task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json'))
    c.write(record / 'native-intent.json', intent)
    accepted_budget.accept_native(root, step, record, intent)
    payload = dict(summary='A contribution', scientific_result=TEXT, limitations=LIMITATIONS)
    if job.get('handoff_protocol', 1) >= 7:
        payload['implementation_selection'] = None
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False,
        usage=native.usage(root), raw_ref=c.file_ref(record / 'science-output.json')))


def _sample(root, step, mode, monkeypatch):
    stages = {}
    prepared = (root / 'jobs' / f'{step:03d}.json').exists()
    with _counts(monkeypatch, root, step) as counts:
        def measured(name, operation):
            before = counts.copy()
            started = time.perf_counter()
            result = operation()
            stages[name] = dict(seconds=time.perf_counter()-started,
                counts={key: counts[key]-before[key] for key in counts if counts[key] != before[key]})
            return result
        started = time.perf_counter()
        job = measured('build', lambda: c.build_job(root, step, 'E', [step-1]))
        measured('terminal_fixture', lambda: _terminal(root, step, job))
        measured('seal', lambda: c.seal_result(root, step))
        next_job = measured('next_build', lambda: c.build_job(root, step+1, 'E', [step]))
        arguments = measured('next_mount_argv', lambda: shared_input_views.mount_arguments(
            root, next_job, c.workspace(root, step+1), include_skills=False))
        elapsed = time.perf_counter()-started
    state = accepted_budget.read(root)
    accepted_job = state.row(f'job/{step:03d}')
    assert accepted_job['input_search_ref'] == job['history']['breadth_snapshot']
    assert accepted_job['output_search_ref'] == state['search_ref']
    assert next_job['history']['breadth_snapshot'] == state['search_ref']
    return dict(mode=mode, accepted_prefix_nodes=step-1, step=step,
        current_job_was_prepared=prepared, seconds=elapsed, stages=stages, counts=dict(counts),
        job_bytes=(root / 'jobs' / f'{step:03d}.json').stat().st_size,
        next_job_bytes=(root / 'jobs' / f'{step+1:03d}.json').stat().st_size,
        direct_parent_count=len(job['direct_refs']), direct_material_count=len(job['direct_materials']),
        direct_material_bytes=sum(row['size_bytes'] for row in job['direct_materials']),
        direct_original_bytes=sum(row['size_bytes'] for row in job['direct_originals']),
        history_nodes=job['history']['prefix']['count'], history_files=len(job['history']['inputs']),
        prefix_count=job['history']['prefix']['count'], history_input_count=len(job['history']['inputs']),
        accepted_head_bytes=(root/accepted_budget.HEAD).stat().st_size,
        mechanism_count=len(state['search']['table']['mechanisms']),
        input_breadth_snapshot_bytes=job['history']['breadth_snapshot']['size_bytes'],
        output_breadth_snapshot_bytes=state['search_ref']['size_bytes'],
        breadth_snapshot_binding_checked=True,
        next_mount_argument_count=len(arguments),
        next_mount_argument_bytes=sum(len(os.fsencode(argument))+1 for argument in arguments))


def test_synthetic_handoff_scaling(tmp_path, monkeypatch):
    output = Path(os.environ.get('SCID_TEST_LOG_DIR', str(tmp_path))) / 'handoff-scaling.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(scope='Synthetic E handoffs at bounded prefix lengths 2/8/16/32; no scientific performance claim.',
        instrumentation='c.read and the lowest available canonical scientific reader family (c.read_scientific_source when present, otherwise c.read_scientific_output) with stable read_scientific_output/path::c.read_scientific_output labels and exact instance-relative paths; logical counts overlap physical Path.read_text/read_bytes counts and must not be added; instance .json Path.read_text/read_bytes calls, settings.json Path.read_bytes, explicit deep gates, current inventory owner, ledger reader, os.scandir calls/yielded entries and os.stat/lstat; counting overhead included in wall times.',
        limitations=['Prefix setup excluded from per-handoff timing but remains inside the supervised process budget.',
            'Cold samples complete the next prepared job after dropping the warm scope, so their prefix has one extra sealed node.',
            'All current findings/artifacts and direct-parent counts/bytes are fixed; generated history/context growth is reported without exemption.',
            'Exact-path Counter keys use path::<entrypoint>::<instance-relative path>; c.read and c.read_scientific_output path counts duplicate their owner-category counts and must not be added to those totals.',
            'Scientific reader family and settings.json Path.read_bytes counts are additional to c.read counts; the observed entrypoints do not cover every underlying IO operation.',
            'The scientific reader selection is probe instrumentation for applying the same overlay to the old baseline, not a product compatibility mechanism; it spies only the lowest available family member to avoid parent/delegate double counting.',
            'physical::Path.read_text:: and physical::Path.read_bytes:: keys count .json reads inside this instance; they overlap c.read/path dimensions and must not be added to them. JSONL and streamed reads are not covered.',
            'Filesystem counts cover paths inside this instance; stat/lstat count Python calls, and DirEntry metadata requests are separate because they may use cached metadata.',
            'Directory calls and entries are grouped by jobs, native-evidence, candidate audit, audit recovery, execution recovery and other paths; entries retain ancient/direct/current/next attribution when their path carries a step.',
            'This handoff sample does not enter controller._run; startup and pending discovery require their own observation.',
            'One instrumented sample per scale and mode; no wall-time threshold or overall O(1) conclusion.'],
        samples=[])
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    save()
    for length in (2, 8, 16, 32):
        base = tmp_path / f'prefix-{length:03d}'
        public = base / 'public'
        public.mkdir(parents=True)
        objective = public / 'objective.md'
        objective.write_text('Retain bounded synthetic findings; the scientific question is unanswered.')
        root = base / 'instance'
        controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=100000,
            max_seconds=240, max_steps=length+4, model='fixture', reasoning_effort='high',
            search_policy=None, experiment_mode='external')
        c.write(root / 'host-started.json', budget.clock_stamp())
        budget.transition(root, 'paused', 'synthetic_prefix_start')
        with handoff_state.scope(root):
            for step in range(1, length+1):
                job = c.build_job(root, step, 'E', [])
                _terminal(root, step, job)
                c.seal_result(root, step)
                budget.transition(root, 'waiting' if step % 2 else 'paused', 'synthetic_prefix_tick')
            report['samples'].append(_sample(root, length+1, 'warm_continuous', monkeypatch))
            save()
        assert handoff_state.current(root) is None
        with handoff_state.scope(root):
            report['samples'].append(_sample(root, length+2, 'cold_fresh_scope', monkeypatch))
            save()
    report['growth'] = {}
    for mode in ('warm_continuous', 'cold_fresh_scope'):
        rows = [row for row in report['samples'] if row['mode'] == mode]
        old_reads = [sum(value for key, value in row['counts'].items()
            if key in ('read_ancient', 'read_scientific_output_ancient')) for row in rows]
        filesystem_keys = sorted({key for row in rows for key in row['counts']
            if key.startswith(('scandir_', 'stat_', 'lstat_', 'direntry_'))})
        filesystem_counts = {key: [row['counts'].get(key, 0) for row in rows]
            for key in filesystem_keys}
        report['growth'][mode] = dict(prefix_nodes=[row['accepted_prefix_nodes'] for row in rows],
            wall_seconds=[row['seconds'] for row in rows],
            job_bytes=[row['job_bytes'] for row in rows], ancient_json_reads=old_reads,
            accepted_head_bytes=[row['accepted_head_bytes'] for row in rows],
            mechanism_count=[row['mechanism_count'] for row in rows],
            input_breadth_snapshot_bytes=[row['input_breadth_snapshot_bytes'] for row in rows],
            output_breadth_snapshot_bytes=[row['output_breadth_snapshot_bytes'] for row in rows],
            next_mount_argument_bytes=[row['next_mount_argument_bytes'] for row in rows],
            ancient_json_bytes=[sum(value for key,value in row['counts'].items()
                if key in ('read_bytes_ancient','read_scientific_output_bytes_ancient')) for row in rows],
            ledger_reads=[row['counts'].get('ledger_reads', 0) for row in rows],
            ledger_events_loaded=[row['counts'].get('ledger_events_loaded', 0) for row in rows],
            source_sequence_calls=[row['counts'].get('source_sequence_calls', 0) for row in rows],
            source_sequence_nodes_returned=[row['counts'].get('source_sequence_nodes_returned', 0) for row in rows],
            filesystem_counts=filesystem_counts,
            generated_context_grows=rows[-1]['job_bytes'] > rows[0]['job_bytes'],
            ancient_json_io_grows=old_reads[-1] > old_reads[0],
            ancient_filesystem_work_grows=any(values[-1] > values[0]
                for key, values in filesystem_counts.items() if key.endswith('_ancient')),
            requirement='Overall constant handoff cost is not established; any context/old-IO growth remains a scaling gap.')
    save()
    direct_shapes = {(row['direct_parent_count'], row['direct_material_count'],
        row['direct_material_bytes'], row['direct_original_bytes']) for row in report['samples']}
    assert len(direct_shapes) == 1 and next(iter(direct_shapes))[0] == 1, direct_shapes
    for row in report['samples']:
        old = {key: value for key, value in row['counts'].items()
            if key.startswith('deep_') and key.endswith(('_ancient', '_direct')) and value}
        assert not old, dict(sample=row, old_scientific_checks=old)
        assert row['counts'].get('accept_owner_current') == 1, row
    assert len({row['mechanism_count'] for row in report['samples']}) == 1
