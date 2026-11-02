"""A-authored diagnostics use the existing artifact handoff; no model calls.

Only stopped native transport is supplied by the fixture. Publication, acceptance,
seals, explicit selections and readonly mount plans remain real. These checks
establish that published originals can be consumed, not that an LLM publishes them
or that the authored diagnostic establishes a scientific result.
"""
import json
from pathlib import Path

from scidiscovery.research_loop import (
    atomic_files, contracts as c, controller, handoff_state, native, originals,
    shared_input_views,
)
from test_direct_input_bindings import stopped_seals
from test_history_handoff import finish


MISSING = 'No public dataset or model was supplied; no scientific comparison was run.'
GAP = 'The A diagnostic exists only as a work draft and was not published for reuse.'
# These are fixture-authored choices, not selections inferred by control.
V1_CHOICE = ['A003-I002', 'A003-I003', 'A003-I001', 'A003-I004']
V2_CHOICE = ['A006-I002', 'A006-I003', 'A006-I001', 'A006-I004']
NAMES = {
    3: {'A003-I001': 'config.json', 'A003-I002': 'diagnostic.py',
        'A003-I003': 'helper.py', 'A003-I004': 'report.json'},
    6: {'A006-I001': 'config.json', 'A006-I002': 'diagnostic.py',
        'A006-I003': 'helper.py', 'A006-I004': 'report.json'},
}


def prepare(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain exact authored diagnostic originals and their limitations.\n')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'],
        max_tokens=10000, max_seconds=300, max_steps=9, model='fixture',
        reasoning_effort='high', search_policy=None, experiment_mode='external')
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment', limitations=[MISSING])
    finish(root, 2, 'W', [1], limitations=[MISSING])
    c.build_job(root, 3, 'A', [1, 2])
    return root


def publish(root, step, version):
    """Author a tiny self-contained public bundle in its sealed readonly mode."""
    files = {
        'diagnostic.py': (
            f'"""Public arithmetic diagnostic fixture {version}; no model or dataset."""\n'
            'from helper import difference\n\n'
            'def diagnose(left, right, config):\n'
            "    return {'difference': difference(left, right), 'condition': config['condition']}\n"
        ),
        'helper.py': f'"""Arithmetic helper {version}."""\ndef difference(left, right):\n    return left - right\n',
        'config.json': json.dumps(dict(condition=f'public-arithmetic-{version}')) + '\n',
        'report.json': json.dumps(dict(version=version, script='diagnostic.py',
            helper='helper.py', config='config.json', results=None,
            limitations=[MISSING])) + '\n',
    }
    published = {}
    for object_id, name in NAMES[step].items():
        source = c.workspace(root, step) / 'artifacts' / name
        source.write_text(files[name])
        source.chmod(0o400)
        info = source.stat()
        published[object_id] = dict(source=source, body=source.read_bytes(),
            mode=info.st_mode,
            file_stat=[info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns],
            path=f'inputs/parents/{step:03d}/artifacts/{name}')
    return published


def seal_assessment(root, step, parents, *, selection=None):
    return finish(root, step, 'A', parents, action='revise_h', selection=selection,
        text='Published [diagnostic](artifacts/diagnostic.py), '
            '[helper](artifacts/helper.py), [config](artifacts/config.json), '
            'and [report](artifacts/report.json). ' + MISSING,
        limitations=[MISSING])


def watch_material_copies(monkeypatch, published):
    """Observe existing publishers without replacing their production effects."""
    sources = {row['source'] for row in published.values()}
    bodies = {row['body'] for row in published.values()}
    copies = []
    copy = atomic_files.copy_original
    publish_bytes = atomic_files.publish_bytes

    def copying(source, target, **options):
        if Path(source) in sources:
            copies.append(('copy', Path(target)))
        return copy(source, target, **options)

    def publishing(target, body, **options):
        if body in bodies:
            copies.append(('publish', Path(target)))
        return publish_bytes(target, body, **options)

    monkeypatch.setattr(atomic_files, 'copy_original', copying)
    monkeypatch.setattr(atomic_files, 'publish_bytes', publishing)
    return copies


def assert_originals(root, job, published, *, choice=None):
    """Follow the published task and native mount plan to exact author files."""
    work = c.workspace(root, job['step'])
    task = c.read(work / 'science-task.json')
    prompt, _, _ = native.render_scientific_request(job, task, {},
        backend_name='codex', instruction_transport='prompt')
    assert 'science-task.json' in prompt
    options = {row['id']: row['path'] for row in job['implementation_options']}
    materials = {row['path']: row for row in job['direct_materials']}
    mounts = shared_input_views.mount_arguments(root, job, work, include_skills=False)
    for object_id, saved in published.items():
        assert options[object_id] == saved['path']
        material = materials[saved['path']]
        assert 'sha256' not in material and 'file_stat' not in material
        source = originals.material_path(root, material)
        assert source == Path(material['source']) == saved['source']
        assert source.read_bytes() == saved['body']
        info = source.stat()
        assert info.st_mode == saved['mode']
        assert [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns] == saved['file_stat']
        assert any(mounts[index:index + 3] == ['--ro-bind', str(source), str(work / saved['path'])]
            for index in range(len(mounts) - 2))
    assert 'sha256' not in json.dumps(task)
    if choice is not None:
        selected = {row['label']: row for row in job['selected_inputs']}
        assert set(selected) == set(choice)
        assert {row['label']: row['path'] for row in task['reused_implementations']} == {
            object_id: published[object_id]['path'] for object_id in choice}
        for object_id in choice:
            row = selected[object_id]
            saved = published[object_id]
            assert row['material'] == materials[saved['path']]
            assert row['material_ref']['id'] == object_id
            assert row['material_ref']['list_ref']['step'] == int(object_id[1:4])
    return task


def test_published_a_diagnostic_bundle_is_explicitly_selected_and_relayed_to_w_and_a(
        tmp_path, monkeypatch):
    root = prepare(tmp_path, monkeypatch)
    published = publish(root, 3, 'v1')
    copies = watch_material_copies(monkeypatch, published)
    with handoff_state.scope(root):
        _, _, record = seal_assessment(root, 3, [1, 2])
        seal = c.read(record / 'result-identity.json')
        assert {row['path'] for row in seal['materials']} == {row['path'] for row in published.values()}
        hjob = c.build_job(root, 4, 'H', [1, 2, 3])
        assert_originals(root, hjob, published)
        assert not hjob.get('selected_inputs')  # Publication makes originals available, never chosen.
        finish(root, 4, 'H', [1, 2, 3], action='experiment', selection=V1_CHOICE,
            limitations=[MISSING])
        wjob = c.build_job(root, 5, 'W', [4])
        assert_originals(root, wjob, published, choice=V1_CHOICE)
        finish(root, 5, 'W', [4], limitations=[MISSING])
        ajob = c.build_job(root, 6, 'A', [4, 5])
        assert_originals(root, ajob, published, choice=V1_CHOICE)
        finish(root, 6, 'A', [4, 5], action='finish', limitations=[MISSING])
        assert c.read(c.host_record(c.workspace(root, 6)) / 'result-identity.json')[
            'control_outcome']['diagnostics'] == []
    report = json.loads(published['A003-I004']['body'])
    assert report['results'] is None and report['limitations'] == [MISSING]
    assert copies == []


def test_new_a_diagnostic_version_keeps_old_selection_until_fixture_selects_exact_new_bundle(
        tmp_path, monkeypatch):
    root = prepare(tmp_path, monkeypatch)
    first = publish(root, 3, 'v1')
    first_copies = watch_material_copies(monkeypatch, first)
    with handoff_state.scope(root):
        seal_assessment(root, 3, [1, 2])
        finish(root, 4, 'H', [1, 2, 3], action='experiment', selection=V1_CHOICE,
            limitations=[MISSING])
        finish(root, 5, 'W', [4], limitations=[MISSING])
        c.build_job(root, 6, 'A', [4, 5])
        second = publish(root, 6, 'v2')
        second_copies = watch_material_copies(monkeypatch, second)
        seal_assessment(root, 6, [4, 5])
        hjob = c.build_job(root, 7, 'H', [4, 5, 6])
        assert_originals(root, hjob, {**first, **second}, choice=V1_CHOICE)
        # A new same-named report/script does not change the inherited choice.
        for name in ('diagnostic.py', 'report.json'):
            old = next(row for row in first.values() if row['source'].name == name)
            new = next(row for row in second.values() if row['source'].name == name)
            assert old['source'] != new['source'] and old['body'] != new['body']
        finish(root, 7, 'H', [4, 5, 6], action='experiment', selection=V2_CHOICE,
            limitations=[MISSING])
        wjob = c.build_job(root, 8, 'W', [7])
        assert_originals(root, wjob, second, choice=V2_CHOICE)
        assert {row['path'] for row in wjob['direct_materials']} == {
            row['path'] for row in second.values()}
        assert not set(V1_CHOICE) & {row['id'] for row in wjob['implementation_options']}
        assert json.loads(originals.material_path(root, next(row['material']
            for row in wjob['selected_inputs'] if row['label'] == 'A006-I004')).read_text())['version'] == 'v2'
    assert first_copies == second_copies == []


def test_unpublished_work_diagnostic_allows_bounded_a_seal_and_retains_the_gap(
        tmp_path, monkeypatch):
    root = prepare(tmp_path, monkeypatch)
    work = c.workspace(root, 3)
    draft = work / 'work' / 'diagnostic.py'
    draft.write_text('# Unpublished arithmetic draft; no scientific inputs.\n')
    (work / 'work' / 'report.json').write_text(json.dumps(dict(results=None, limitations=[GAP, MISSING])))
    _, _, record = finish(root, 3, 'A', [1, 2], action='revise_h',
        text='Only work/diagnostic.py and work/report.json drafts exist. ' + GAP,
        limitations=[GAP, MISSING])
    seal = c.read(record / 'result-identity.json')
    assert seal['materials'] == seal['entry_points'] == []
    assert seal['control_outcome']['diagnostics'] == []
    assert c.read(record / 'science-output.json')['limitations'] == [GAP, MISSING]
    hjob = c.build_job(root, 4, 'H', [1, 2, 3])
    assert hjob['direct_materials'] == hjob['implementation_options'] == []
    assert not hjob.get('selected_inputs')
    assert draft.read_text() == '# Unpublished arithmetic draft; no scientific inputs.\n'
