"""Mechanical evidence export contracts; no scientist or external execution."""
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/acceptance/worker_evidence_view.py'
SPEC = importlib.util.spec_from_file_location('worker_evidence_view', SCRIPT)
view = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(view)


def encoded(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def packet(tmp_path, entries):
    public = tmp_path / 'original/public'
    public.mkdir(parents=True)
    evidence = []
    originals = {}
    for alias, kind, value in entries:
        raw = value if isinstance(value, bytes) else encoded(value)
        source = public / alias
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(raw)
        evidence.append(dict(alias=alias, kind=kind, size_bytes=len(raw), projection='retained original'))
        originals[alias] = raw
    index = public / 'index.json'
    index.write_bytes(encoded(dict(version=2, evidence=evidence)))
    return index, originals


def read(path):
    return json.loads(path.read_text())


def test_new_science_view_retains_exact_private_bindings_and_small_omission_summary(tmp_path, capsys):
    digest = 'a' * 64
    candidate = 'local-' + digest
    alternative = 'local-' + 'b' * 64
    source = f"import hashlib\nscientific_digest = '{digest}'\n# sha256 is the actual studied algorithm\n".encode()
    scientist = dict(scientific_result=f'The SHA-256 test vector is {digest}.',
                     limitations=['The negative control failed; the unresolved call stays unknown.'])
    inventory = dict(version=1, withheld=[dict(public_alias=f'models/run-{i}.pkl',
        sha256=digest, size_bytes=i, reason='binary_model_private_filenames') for i in range(48)],
        reproduction_facts=dict(numerical_reproduction_passed=True, first_overall_postcheck_passed=False,
            subsequent_exact_cache_restoration_verified=True, science_replay_repeated=False),
        task_completion='Original stopping conditions remain unfinished.',
        limitation='Unknown submissions are not completed experiments. Full original-byte reproduction is unavailable.')
    index, originals = packet(tmp_path, [
        ('sources/train.py', 'source', source),
        ('instruction.md', 'original_task', b'User original: retain sha256 as a scientific metric name.\n'),
        ('scientist.json', 'sealed_scientist', scientist),
        ('scientist-a.json', 'sealed_scientist', dict(scientific_result='The alternative has worse recovery.',
            limitations=['The unresolved submission stays unknown.'], candidate_recommendation=candidate,
            candidate_comparison=dict(rows=[dict(candidate_id=candidate, key_risk='The negative control failed.',
                decision='keep'), dict(candidate_id=alternative, key_risk='The alternative has stronger shift sensitivity.',
                decision='replace')], rationale='Prefer observed recovery while retaining the stronger-perturbation loss.', evidence_steps=[2]))),
        ('carry/index.json', 'limitation', dict(version=1, known_terminal_experiments=[dict(alias='experiment-1',
            source='experiment-1/train.py', facts='experiment-1/facts.json', source_ref=digest)],
            original_unresolved_submission_count=1, unresolved_submission_is_completed=False,
            stopping_condition='Original stopping conditions remain unresolved.')),
        ('metrics/identity.json', 'metrics', dict(source_ref=digest, public_manifest_ref=digest,
            task_python_ref=digest, qualification='Public observations only; no scientific verdict.')),
        ('metrics/scientific-data.json', 'metrics', dict(sha256='A named scientific variable', values=[1, 2])),
        ('calls/accepted.json', 'accepted_projection', dict(call_id=digest, candidate_id=candidate,
            prepared=dict(name=digest, sha256=digest, size_bytes=7), scope=dict(job_ref=digest))),
        ('calls/completed.json', 'terminal_projection', dict(call_id=digest, candidate_id=candidate,
            status='unknown', scope=dict(job_ref=digest), outputs=[dict(name=digest, sha256=digest,
                size_bytes=7, media_type='application/json')])),
        ('completion/closeout-outcome.json', 'completion_projection', dict(status='delivery_confirmed',
            goal_completion='not_asserted', last_confirmed=dict(candidate_id=candidate,
                operation_id=digest, artifact_ref=digest, status='confirmed', cleanup_confirmed=True))),
        ('audit/submitted.json', 'audit_projection', dict(run_id=1, val_score=0.7, audit_score=0.6,
            leakage_flag=False, artifact_ref=digest)),
        ('run/results.jsonl', 'metrics_projection', [dict(run_id=1, ok=False, metric=0.2, sha256=digest)]),
        ('withheld.json', 'publication_inventory', inventory),
    ])
    output = tmp_path / 'new-view'
    # Exercise the actual operator entry point consumed by future packet preparation.
    view.main(['--index', str(index), '--output', str(output)])
    assert json.loads(capsys.readouterr().out)['science_index'] == str(output / 'science/index.json')
    assert (output / 'control/index.json').read_bytes() == index.read_bytes()
    for alias, raw in originals.items():
        assert (output / 'control/originals' / alias).read_bytes() == raw
    bindings = read(output / 'control/bindings.json')
    assert bindings['original_index'] == dict(path=str(index), size_bytes=index.stat().st_size)
    assert len(bindings['bindings']) == len(originals)
    omitted = next(row for row in bindings['bindings'] if row['kind'] == 'publication_inventory')
    assert omitted['omitted_originals'] == inventory['withheld']
    assert (output / 'control').stat().st_mode & 0o777 == 0o700
    assert (output / 'science/sources/train.py').read_bytes() == source
    assert (output / 'science/scientist.json').read_bytes() == originals['scientist.json']
    public = read(output / 'science/index.json')
    assert all(set(row) == {'alias', 'kind', 'summary'} for row in public['evidence'])
    assert all(row['kind'] != 'publication_inventory' for row in public['evidence'])
    assert not (output / 'science/withheld.json').exists()
    assert len(public['omissions'][0]['categories']) == 1
    assert public['omissions'][0]['reproduction_facts']['first_overall_postcheck_passed'] is False
    assert 'unfinished' in public['omissions'][0]['task_completion']
    assert 'run-47' not in json.dumps(public)
    assert digest not in json.dumps(public)
    assert str(index.parent) not in json.dumps(public)
    assert 'control/' not in json.dumps(public)
    assert 'originals/' not in json.dumps(public)
    accepted = read(output / 'science/calls/accepted.json')
    terminal = read(output / 'science/calls/completed.json')
    completion = read(output / 'science/completion/closeout-outcome.json')
    assert accepted['candidate_ref'] == terminal['candidate_ref'] == completion['last_confirmed']['candidate_ref']
    assert accepted['materials'][0]['material_ref'] == terminal['outputs'][0]['material_ref']
    assert terminal['status'] == 'unknown'
    scientist_a = read(output / 'science/scientist-a.json')
    assert scientist_a['candidate_recommendation_ref'] == accepted['candidate_ref']
    assert scientist_a['scientific_result'] == 'The alternative has worse recovery.'
    assert scientist_a['candidate_comparison']['rows'][0]['key_risk'] == 'The negative control failed.'
    comparison = scientist_a['candidate_comparison']
    assert comparison['rows'][0]['candidate_ref'] == accepted['candidate_ref']
    assert comparison['rows'][1]['candidate_ref'] != accepted['candidate_ref']
    assert comparison['rationale'] == 'Prefer observed recovery while retaining the stronger-perturbation loss.'
    assert comparison['evidence_steps'] == [2]
    assert 'b' * 64 not in json.dumps(scientist_a)
    assert read(output / 'science/carry/index.json')['unresolved_submission_is_completed'] is False
    assert (output / 'science/metrics/scientific-data.json').read_bytes() == originals['metrics/scientific-data.json']
    assert read(output / 'science/run/results.jsonl')[0]['ok'] is False
    for alias in ['calls/accepted.json', 'calls/completed.json', 'completion/closeout-outcome.json',
                  'audit/submitted.json', 'run/results.jsonl', 'scientist-a.json',
                  'carry/index.json', 'metrics/identity.json']:
        text = (output / 'science' / alias).read_text()
        assert digest not in text
        assert 'sha256' not in text
        assert 'scope' not in text
    assert originals['withheld.json'] == (index.parent / 'withheld.json').read_bytes()


@pytest.mark.parametrize('mutation', ['index', 'missing', 'changed', 'redirected'])
def test_exact_index_and_every_indexed_original_are_mandatory(tmp_path, mutation):
    index, _ = packet(tmp_path, [('source.py', 'source', b'x = 1\n')])
    source = index.parent / 'source.py'
    if mutation == 'index':
        index.write_bytes(b'{invalid index')
    elif mutation == 'missing':
        source.unlink()
    elif mutation == 'changed':
        source.write_bytes(b'x = 22\n')
    else:
        target = tmp_path / 'redirect.py'
        source.rename(target)
        source.symlink_to(target)
    with pytest.raises(ValueError):
        view.export_view(index, tmp_path / 'new-view')
    assert not (tmp_path / 'new-view').exists()


def test_unsupported_mechanical_producer_fails_without_scrubbing_scientific_data(tmp_path):
    original = dict(candidate_id='b' * 64, custom_metric=dict(sha256='a legitimate scientific variable'))
    index, originals = packet(tmp_path, [('metrics.json', 'metrics', original)])
    with pytest.raises(ValueError, match='Unsupported mechanical metrics producer'):
        view.export_view(index, tmp_path / 'new-view')
    assert not (tmp_path / 'new-view').exists()
    assert (index.parent / 'metrics.json').read_bytes() == originals['metrics.json']


def test_exclusive_output_and_indexed_receipt_kind(tmp_path):
    index, _ = packet(tmp_path, [('completed.json', 'limitation', dict(status='unknown'))])
    with pytest.raises(ValueError, match='Mechanical receipt requires'):
        view.export_view(index, tmp_path / 'new-view')
    output = tmp_path / 'already-used'
    output.mkdir()
    (output / 'retained').write_bytes(b'existing failed preparation')
    with pytest.raises(ValueError, match='Exclusive new view'):
        view.export_view(index, output)
    assert (output / 'retained').read_bytes() == b'existing failed preparation'


def test_omission_unknown_categories_fail_closed(tmp_path):
    inventory = dict(withheld=[dict(public_alias='missing/result.json', sha256='a' * 64,
                                   size_bytes=1, reason='new_unreviewed_reason')])
    index, _ = packet(tmp_path, [('withheld.json', 'publication_inventory', inventory)])
    with pytest.raises(ValueError, match='Unsupported omission category'):
        view.export_view(index, tmp_path / 'new-view')
    assert not (tmp_path / 'new-view').exists()


def test_omission_details_are_bounded_readable_and_retain_each_missing_material(tmp_path):
    digest = 'c' * 64
    withheld = [dict(public_alias=f'experiments/run-{i:02d}/model.pkl', sha256=digest,
                     size_bytes=i, reason='binary_model_private_filenames') for i in range(29)]
    withheld.extend([
        dict(public_alias='experiments/run-28/submission-intent.json', sha256=digest,
             size_bytes=10, reason='backend_control_intent'),
        dict(public_alias=f'experiments/run-28/local-{digest}/raw.stdout', sha256=digest,
             size_bytes=20, reason='stdout_contains_private_host_paths'),
    ])
    inventory = dict(version=1, withheld=withheld, task_completion='Unknown calls remain unresolved.')
    index, originals = packet(tmp_path, [('withheld.json', 'publication_inventory', inventory)])
    output = tmp_path / 'new-view'
    view.export_view(index, output)
    science = output / 'science'
    public = read(science / 'index.json')
    assert public['evidence'] == []
    assert len(json.dumps(public)) < 2500
    assert 'run-00' not in json.dumps(public)
    assert 'withheld.json' not in json.dumps(public)
    assert digest not in json.dumps(public)
    categories = public['omissions'][0]['categories']
    assert len(categories) == 3
    details = []
    page_aliases = []
    for category in categories:
        assert 'candidate or experiment' in category['when_to_open']
        alias = category['details_ref']
        while alias is not None:
            assert alias.startswith('omissions/')
            assert alias not in page_aliases
            page_aliases.append(alias)
            page = read(science / alias)
            assert 1 <= len(page['materials']) <= view.OMISSION_PAGE_SIZE
            assert set(page) <= {'materials', 'next'}
            for row in page['materials']:
                assert set(row) == {'material_label', 'category', 'scope', 'reason', 'availability'}
                assert row['availability'] == 'not_available_in_this_view'
                assert row['category'] == category['category']
                assert row['reason'] == category['reason']
                assert not row['material_label'].startswith('/')
                assert 'control/' not in json.dumps(row)
            assert digest not in json.dumps(page)
            assert 'sha256' not in json.dumps(page)
            assert 'size_bytes' not in json.dumps(page)
            details.extend(page['materials'])
            alias = page.get('next')
    assert len(details) == len(withheld)
    for row in withheld[:-1]:
        assert any(detail['material_label'] == row['public_alias'] for detail in details)
    assert any(row['material_label'].endswith('/raw.stdout') and 'run-28' in row['scope'] for row in details)
    bindings = read(output / 'control/bindings.json')['bindings'][0]
    assert bindings['omitted_originals'] == withheld
    assert {row['alias'] for row in bindings['science_details']} == set(page_aliases)
    for row in bindings['science_details']:
        assert (science / row['alias']).stat().st_size == row['size_bytes']
    assert (output / 'control/originals/withheld.json').read_bytes() == originals['withheld.json']


def test_candidate_comparison_with_unrecognized_mechanical_fields_fails_closed(tmp_path):
    candidate = 'local-' + 'a' * 64
    original = dict(scientific_result='Scientific explanation remains exact.',
        candidate_comparison=dict(rows=[dict(candidate_id=candidate, key_risk='Observed loss.', decision='keep')],
            rationale='Scientific reason.', evidence_steps=[2], artifact_ref='b' * 64))
    index, originals = packet(tmp_path, [('scientist-a.json', 'sealed_scientist', original)])
    with pytest.raises(ValueError, match='Unsupported candidate comparison producer'):
        view.export_view(index, tmp_path / 'new-view')
    assert not (tmp_path / 'new-view').exists()
    assert (index.parent / 'scientist-a.json').read_bytes() == originals['scientist-a.json']


def omission_summaries(science):
    public = read(science / 'index.json')
    block = {key: public[key] for key in ('omissions', 'omissions_next') if key in public}
    assert len(encoded(block)) <= view.OMISSIONS_SUMMARY_MAX_BYTES
    summaries = list(public['omissions'])
    alias = public.get('omissions_next')
    seen = set()
    while alias is not None:
        assert alias.startswith('omissions/') and alias not in seen
        seen.add(alias)
        path = science / alias
        page = read(path)
        assert path.stat().st_size <= view.OMISSION_PAGE_MAX_BYTES
        assert 1 <= len(page['omissions']) <= view.OMISSION_PAGE_SIZE
        assert set(page) <= {'omissions', 'next'}
        summaries.extend(page['omissions'])
        alias = page.get('next')
    return summaries


def complete_omission_text(science, row, key):
    assert isinstance(row[key], str)
    alias = row.get(key + '_details_ref')
    if alias is None:
        assert key + '_truncated' not in row
        return row[key]
    assert row[key + '_truncated'] is True
    parts, seen = [], set()
    while alias is not None:
        assert alias.startswith('omissions/') and alias not in seen
        seen.add(alias)
        path = science / alias
        assert path.stat().st_size <= view.OMISSION_PAGE_MAX_BYTES
        page = read(path)
        assert set(page) <= {'text', 'next'}
        assert isinstance(page['text'], str) and page['text']
        parts.append(page['text'])
        alias = page.get('next')
    text = ''.join(parts)
    assert text.startswith(row[key]) and len(row[key]) < len(text)
    return text


def test_omission_long_text_has_byte_bounded_pages_and_exact_reconstruction(tmp_path):
    # JSON escapes, multibyte characters and surrogate-pair encoding all count.
    text = ('Unresolved counterevidence: 中文实验😀 "quoted" \\path\n\t' * 1600) + ' final loss.'
    inventory = dict(withheld=[], task_completion=text, limitation='')
    index, originals = packet(tmp_path, [('withheld.json', 'publication_inventory', inventory)])
    output = tmp_path / 'new-view'
    view.export_view(index, output)
    science = output / 'science'
    summary, = omission_summaries(science)
    assert complete_omission_text(science, summary, 'task_completion') == text
    assert summary['limitation'] == ''
    assert 'limitation_details_ref' not in summary
    assert len(encoded(summary['task_completion'])) <= view.OMISSION_PREVIEW_MAX_BYTES
    assert text not in (science / 'index.json').read_text()
    assert (output / 'control/originals/withheld.json').read_bytes() == originals['withheld.json']
    for path in (science / 'omissions').rglob('*.json'):
        assert path.stat().st_size <= view.OMISSION_PAGE_MAX_BYTES


def test_omission_long_material_names_and_scopes_remain_complete_and_pages_fit_bytes(tmp_path):
    digest = 'd' * 64
    scope = 'experiment-中文😀' * 900
    withheld = [dict(public_alias=f'{scope}/case-{i}/model-{"模型😀" * 900}.pkl',
        sha256=digest, size_bytes=i, reason='binary_model_private_filenames') for i in range(24)]
    inventory = dict(withheld=withheld)
    index, _ = packet(tmp_path, [('withheld.json', 'publication_inventory', inventory)])
    output = tmp_path / 'new-view'
    view.export_view(index, output)
    science = output / 'science'
    summary, = omission_summaries(science)
    assert scope not in json.dumps(summary)
    alias = summary['categories'][0]['details_ref']
    materials, sizes = [], []
    while alias is not None:
        path = science / alias
        assert path.stat().st_size <= view.OMISSION_PAGE_MAX_BYTES
        page = read(path)
        sizes.append(len(page['materials']))
        assert 1 <= sizes[-1] <= view.OMISSION_PAGE_SIZE
        for material in page['materials']:
            materials.append((complete_omission_text(science, material, 'material_label'),
                              complete_omission_text(science, material, 'scope')))
            assert material['availability'] == 'not_available_in_this_view'
        alias = page.get('next')
    assert any(size < view.OMISSION_PAGE_SIZE for size in sizes[:-1])
    assert materials == [(row['public_alias'], str(Path(row['public_alias']).parent)) for row in withheld]
    bindings = read(output / 'control/bindings.json')['bindings'][0]
    assert bindings['omitted_originals'] == withheld
    assert {row['alias'] for row in bindings['science_details']} == {
        path.relative_to(science).as_posix() for path in (science / 'omissions').rglob('*.json')}
    for path in (science / 'omissions').rglob('*.json'):
        assert path.stat().st_size <= view.OMISSION_PAGE_MAX_BYTES
        raw = path.read_text()
        assert digest not in raw and 'size_bytes' not in raw and 'control/' not in raw


def test_omission_multiple_inventory_summary_navigation_is_byte_bounded_and_complete(tmp_path):
    inventories = [dict(withheld=[dict(public_alias=f'experiment-{i}/model.pkl', sha256='e' * 64,
        size_bytes=i, reason='binary_model_private_filenames')],
        task_completion=f'Inventory {i}: ' + ('未完成😀 "failure"\n' * 180),
        limitation=f'Counterevidence {i}: ' + ('Loss remains unresolved.\t' * 180)) for i in range(24)]
    index, originals = packet(tmp_path, [(f'withheld-{i}.json', 'publication_inventory', value)
                                                 for i, value in enumerate(inventories)])
    output = tmp_path / 'new-view'
    view.export_view(index, output)
    science = output / 'science'
    public = read(science / 'index.json')
    assert public['evidence'] == [] and public['omissions_next'].startswith('omissions/')
    summaries = omission_summaries(science)
    assert len(summaries) == len(inventories)
    for summary, original in zip(summaries, inventories):
        for key in ('task_completion', 'limitation'):
            assert complete_omission_text(science, summary, key) == original[key]
        category, = summary['categories']
        material, = read(science / category['details_ref'])['materials']
        assert material['material_label'] == original['withheld'][0]['public_alias']
        assert material['availability'] == 'not_available_in_this_view'
    bindings = read(output / 'control/bindings.json')
    private_pages = bindings['omission_navigation'] + [page for binding in bindings['bindings']
                                                      for page in binding['science_details']]
    assert {row['alias'] for row in private_pages} == {
        path.relative_to(science).as_posix() for path in (science / 'omissions').rglob('*.json')}
    for row in private_pages:
        path = science / row['alias']
        assert path.stat().st_size <= view.OMISSION_PAGE_MAX_BYTES
        assert path.stat().st_size == row['size_bytes']
    for alias, raw in originals.items():
        assert (output / 'control/originals' / alias).read_bytes() == raw


@pytest.mark.parametrize('field', ['task_completion', 'limitation'])
@pytest.mark.parametrize('value', [None, 7, [], {}])
def test_omission_text_bad_types_fail_closed(tmp_path, field, value):
    inventory = dict(withheld=[], **{field: value})
    index, _ = packet(tmp_path, [('withheld.json', 'publication_inventory', inventory)])
    with pytest.raises(ValueError, match='Known omission text fields must be strings'):
        view.export_view(index, tmp_path / 'new-view')
    assert not (tmp_path / 'new-view').exists()


def test_selected_same_size_source_is_copied_without_content_fingerprinting(tmp_path):
    index, _ = packet(tmp_path, [('source.py', 'source', b'x = 1\n')])
    source=index.parent/'source.py';source.write_bytes(b'x = 2\n')
    output=tmp_path/'new-view'
    view.export_view(index,output)
    assert (output/'science/source.py').read_bytes()==b'x = 2\n'
    binding=read(output/'control/bindings.json')['bindings'][0]
    assert binding['source_ref']==dict(path=str(source),size_bytes=source.stat().st_size)
    assert 'sha256' not in json.dumps(binding)
