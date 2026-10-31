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
    return None


@contextmanager
def _counts(monkeypatch, root, current):
    counts = Counter()
    def category(step):
        if step is None:
            return 'other'
        return ('ancient' if step < current-1 else 'direct' if step == current-1
            else 'current' if step == current else 'next')
    def file_category(path):
        try:
            parts = Path(path).absolute().relative_to(root).parts
            if parts[0] == 'control-ledger':
                return 'ledger'
            if parts[:2] == ('host-history', 'objects'):
                return 'history_context'
        except (TypeError, ValueError, IndexError):
            pass
        return category(_producer(root, path))
    with monkeypatch.context() as patch:
        for name in ('read', 'read_scientific_output'):
            original = getattr(c, name)
            def read(path, *args, _original=original, _name=name, **kwargs):
                owner = file_category(path)
                counts[f'{_name}_{owner}'] += 1
                counts[f'{_name}_bytes_{owner}'] += Path(path).stat().st_size
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
        instrumentation='c.read/read_scientific_output, explicit deep gates, current inventory owner and ledger reader only; counting overhead included in wall times.',
        limitations=['Prefix setup excluded from per-handoff timing but remains inside the supervised process budget.',
            'Cold samples complete the next prepared job after dropping the warm scope, so their prefix has one extra sealed node.',
            'All current findings/artifacts and direct-parent counts/bytes are fixed; generated history/context growth is reported without exemption.',
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
            generated_context_grows=rows[-1]['job_bytes'] > rows[0]['job_bytes'],
            ancient_json_io_grows=old_reads[-1] > old_reads[0],
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
