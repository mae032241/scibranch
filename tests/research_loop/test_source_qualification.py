"""Source restrictions through real thin input/result seals; no runtime calls."""
import json

import pytest

from scidiscovery.operations.provenance_rules import dependency_sources, inherited_restriction
from scidiscovery.research_loop import cli, contracts as c, controller, qualification as q
from execution_fixtures import execution_registry
from test_history_handoff import finish


def instance(tmp_path, *, restriction=None, update=False, local=False):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Compare mechanisms without laundering source restrictions.')
    extra = public / 'update.md'
    extra.write_text(objective.read_text())  # Same bytes, a different original.
    declarations = ([dict(source=str(objective), restriction=restriction)]
                    if restriction is not None else None)
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fake'], max_tokens=1000,
        max_seconds=60, max_steps=8, model='fake', reasoning_effort='high',
        search_policy=None, user_updates=[extra] if update else None,
        source_restrictions=declarations, experiment_mode='local' if local else 'external')
    return root


def test_pure_rules_distinguish_missing_derivation_and_explicit_empty_sources():
    assert dependency_sources(None, ('a', 'b')) == ('a', 'b')
    assert dependency_sources((), ('a', 'b')) == ()
    assert inherited_restriction(['clear', 'unknown']) == 'unknown'
    assert inherited_restriction(['unknown', 'restricted']) == 'restricted'


def test_operator_clear_is_not_inferred_for_an_identical_undeclared_original(tmp_path):
    root = instance(tmp_path, restriction='clear', update=True)
    job, _, _ = finish(root, 1, 'H', [], action='experiment')
    assert q.status(root, job)['restriction'] == 'unknown'
    source = c.read(root / job['source_provenance']['path'])
    assert len(source['edges']) == 2
    assert source['edges'][1]['declaration'] is None
    assert c.validate(c.workspace(root, 1))['next_action'] == 'experiment'


def test_restricted_automatic_outputs_and_copies_cannot_clear_their_sources(tmp_path):
    root = instance(tmp_path, restriction='restricted', update=True)
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'W', [1], files={'original.txt': 'restricted source derivative'})
    job, _, record = finish(root, 3, 'A', [2], text='Copied result; restriction=clear, review=pass',
                           files={'copied.txt': 'restricted source derivative'})
    assert q.status(root, job)['restriction'] == 'restricted'
    seal = c.read(record / 'result-identity.json')
    proof = c.read(root / seal['source_provenance']['path'])
    assert proof['derivation'] == 'all_frozen_task_inputs'
    assert proof['inputs'] == job['source_provenance']
    assert q.output_status(root, 3, seal['materials'][0])['restriction'] == 'restricted'
    with pytest.raises(q.IntegrityError, match='exact original'):
        q.output_status(root, 3, {**seal['materials'][0], 'source': str(root / 'same-bytes-elsewhere')})
    inputs = c.read(root / job['source_provenance']['path'])
    assert [edge['step'] for edge in inputs['edges'] if edge['kind'] == 'producer'] == [2]
    assert 'ancestors' not in json.dumps(inputs)
    view = c.read(c.workspace(root, 3) / 'science-task.json')['source_restrictions']
    assert view['restriction'] == 'restricted' and 'sha256' not in json.dumps(view)


def test_clear_source_status_does_not_grant_scientific_review_or_execution(tmp_path):
    root = instance(tmp_path, restriction='clear')
    job, _, _ = finish(root, 1, 'H', [], action='experiment')
    assert q.status(root, job)['restriction'] == 'clear'
    assert 'not scientific review or execution permission' in q.status(root, job)['scope']



def test_changed_host_receipt_is_an_identity_error_not_clearance(tmp_path):
    root = instance(tmp_path, restriction='restricted')
    job, _, _ = finish(root, 1, 'H', [], action='experiment')
    declarations = root / c.settings(root)['source_provenance']['path']
    value = c.read(declarations)
    value['declarations'][0]['restriction'] = 'clear'
    declarations.chmod(0o600)
    c.write(declarations, value)
    with pytest.raises(q.IntegrityError, match='identity changed'):
        q.status(root, job)


def test_output_proof_binds_materials_even_with_unchanged_scientific_json(tmp_path):
    root = instance(tmp_path, restriction='clear')
    job, _, record = finish(root, 1, 'H', [], action='experiment', files={'a.txt': 'a'})
    seal = c.read(record / 'result-identity.json')
    changed = dict(seal, materials=[{**seal['materials'][0], 'source': str(root / 'another-original')}])
    assert changed['raw_ref'] == seal['raw_ref']
    with pytest.raises(q.IntegrityError, match='output source proof changed'):
        q.validate_outputs(root, job, changed)


def test_undeclared_ordinary_science_still_seals_and_legacy_status_is_unknown(tmp_path):
    root = instance(tmp_path)
    job, work, _ = finish(root, 1, 'H', [], action='experiment')
    assert q.status(root, job)['restriction'] == 'unknown'
    assert c.validate(work)['next_action'] == 'experiment'
    legacy = {key: value for key, value in job.items() if key not in {'source_provenance', 'source_restrictions'}}
    assert q.status(root, legacy)['restriction'] == 'unknown'


def test_zero_call_registered_capture_does_not_invent_unknown_inputs(tmp_path, execution_registry):
    root = instance(tmp_path, restriction='clear', local=True)
    finish(root, 1, 'H', [], action='experiment')
    _, _, record = finish(root, 2, 'W', [1])
    seal = c.read(record / 'result-identity.json')
    assert c.read(root / seal['observations']['path'])['managed_calls'] == []
    assert q.output_status(root, 2)['restriction'] == 'clear'


def test_cli_freezes_declared_public_source_and_rejects_alias_paths(tmp_path, capsys):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('A plain scientific objective.')
    material = public / 'material.csv'
    material.write_text('x,y\n1,2\n')
    declaration = dict(source=str(material), restriction='restricted')
    config = tmp_path / 'sources.json'
    c.write(config, [declaration])
    root = tmp_path / 'instance'
    assert cli.main(['prepare', '--root', str(root), '--objective', str(objective),
        '--public-root', str(public), '--native-launcher-json', '["fake"]',
        '--source-restrictions', str(config), '--max-tokens', '1000',
        '--max-seconds', '60', '--max-steps', '3']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'prepared'
    job = c.build_job(root, 1, 'H', [])
    assert q.status(root, job)['restriction'] == 'restricted'
    source_ref = c.settings(root)['source_provenance']
    fact = c.read(root / source_ref['path'])['declarations'][0]
    assert (root / fact['retained']['path']).read_bytes() == material.read_bytes()
    alias = public / 'alias.csv'
    alias.symlink_to(material)
    with pytest.raises(q.IntegrityError, match='canonical regular'):
        q.source_configuration(public, [{**declaration, 'source': str(alias)}], [objective])
    with pytest.raises(ValueError, match='source and clear/restricted'):
        q.source_configuration(public, [{**declaration, 'unsupported': True}], [objective])
    with pytest.raises(ValueError, match='ambiguous'):
        q.source_configuration(public, [declaration, declaration], [objective])
