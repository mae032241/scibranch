"""Installed Skill selection serializes only the existing declaration schema."""
import copy
import json

import pytest

from scidiscovery.research_loop import configuration_form as form, research_config, skill_supply
from test_configuration_form import Controls


@pytest.fixture
def installed(tmp_path, monkeypatch):
    source = tmp_path / 'skills'
    for name in ('alpha', 'beta'):
        directory = source / name
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(
            '---\nname: '+name+'\ndescription: '+name+' research guidance\n---\n')
    monkeypatch.setattr(skill_supply, 'source_root', lambda: source)
    # Keep the real native Skill-loading contract without invoking a process.
    executable = tmp_path / 'codex'
    executable.write_text('#!/bin/sh\nexit 99\n')
    executable.chmod(0o700)
    (tmp_path / 'public').mkdir()
    return form.imported(dict(version=1,
        research=dict(root='instance', public_root='public', objective_text='Compare exact alternatives.'),
        agent=dict(launcher=[str(executable)]),
        budget=dict(max_tokens=1000, max_seconds=120, max_steps=8)), base=tmp_path)


def submitted(draft, *, edit=False):
    return Controls(form.render(draft, edit=edit)).fields


def selected(document, role):
    name = document['agent']['roles'][role]['skill_set']
    return document['skills']['sets'][name]['items']


def test_catalog_is_collapsed_in_second_step_and_defaults_enable_no_roles(installed, tmp_path):
    html = form.render(installed)
    assert html.index('data-setup-step="2"') < html.index('id="research-skill-picker"') < html.index('class="advanced-settings"')
    assert '<details class="research-skill-settings"><summary>研究技能</summary>' in html
    assert 'alpha research guidance' in html
    assert 'beta research guidance' in html
    assert 'alpha 分配给 W · 实验与实现' in html
    fields = submitted(installed)
    assert not any(name.startswith('skill_picker.item.') for name in fields)
    parsed = form.parse(fields, base=tmp_path)
    assert form.declaration(parsed) == form.declaration(installed)
    assert 'skills' not in parsed['document']
    assert 'roles' not in parsed['document']['agent']


def test_no_script_selection_preview_export_import_keeps_role_binding(installed, tmp_path):
    fields = submitted(installed)
    # No JavaScript selects a mode; successful checkbox controls are enough.
    fields['skill_picker.item.W.0'] = 'alpha'
    fields['skill_picker.item.E.1'] = 'beta'
    parsed = form.parse(fields, base=tmp_path)
    assert selected(parsed['document'], 'W') == ['alpha']
    assert selected(parsed['document'], 'E') == ['beta']
    assert set(parsed['document']['agent']['roles']) == {'W', 'E'}
    checked = form.preview(parsed)
    assert checked['skills']['sets'] == parsed['document']['skills']['sets']
    exported = research_config.tomllib.loads(form.export_toml(parsed))
    rebound = form.imported(exported, base=tmp_path / 'elsewhere')
    assert rebound['document']['skills'] == parsed['document']['skills']
    assert rebound['document']['agent']['roles'] == parsed['document']['agent']['roles']
    assert form.declaration(form.parse(submitted(parsed), base=tmp_path)) == form.declaration(parsed)
    assert not (tmp_path / 'instance').exists()


def test_imported_sets_workers_unknown_fields_and_provider_references_are_lossless(installed, tmp_path):
    document = installed['document']
    document['skills'] = dict(sets={
        'original': dict(items=['alpha', 'missing'], future=dict(exact=[False, 3])),
        'unused': dict(items=['beta'])}, extension=dict(keep=[]))
    document['agent'].update(workers=dict(analyst=dict(model='worker-model', skill_set='original')),
        roles=dict(H=dict(worker='analyst'), W=dict(skill_set='provider:offered'),
            A=dict(skill_set=''), E=dict(extension=dict(exact=[]))))
    original = copy.deepcopy(document)
    html = form.render(installed)
    assert '暂不可勾选的技能：missing' in html
    assert submitted(installed)['skill_picker.item.H.0'] == 'alpha'
    assert form.declaration(form.parse(submitted(installed), base=tmp_path)) == original


def test_changed_selection_creates_unique_set_and_preserves_imported_sets(installed, tmp_path):
    document = installed['document']
    document['skills'] = dict(sets={'original': dict(items=['alpha']), 'web-h': dict(items=['beta'])})
    document['agent']['roles'] = dict(H=dict(skill_set='original'))
    fields = submitted(installed)
    fields['skill_picker.item.H.1'] = 'beta'
    parsed = form.parse(fields, base=tmp_path)['document']
    assert parsed['agent']['roles']['H']['skill_set'] == 'web-h-2'
    assert selected(parsed, 'H') == ['alpha', 'beta']
    assert parsed['skills']['sets']['original'] == dict(items=['alpha'])
    assert parsed['skills']['sets']['web-h'] == dict(items=['beta'])


def test_clear_overrides_worker_inheritance_and_inherit_removes_only_explicit_binding(installed, tmp_path):
    document = installed['document']
    document['skills'] = dict(sets=dict(original=dict(items=['alpha']), other=dict(items=['beta'])))
    document['agent'].update(workers=dict(analyst=dict(model='worker-model', skill_set='original')),
        roles=dict(H=dict(worker='analyst'), W=dict(worker='analyst', skill_set='other')))
    fields = submitted(installed)
    del fields['skill_picker.item.H.0']
    fields['skill_picker.choice.W'] = 'inherit'
    parsed = form.parse(fields, base=tmp_path)['document']
    assert selected(parsed, 'H') == []
    assert parsed['agent']['roles']['H']['worker'] == 'analyst'
    assert parsed['agent']['roles']['W'] == dict(worker='analyst')
    assert parsed['agent']['workers'] == document['agent']['workers']
    assert parsed['skills']['sets']['original'] == dict(items=['alpha'])


def test_explicit_choice_uses_existing_set_and_none_is_explicit_empty_set(installed, tmp_path):
    installed['document']['skills'] = dict(sets=dict(existing=dict(items=['beta'])))
    fields = submitted(installed)
    fields['skill_picker.choice.H'] = 'set:existing'
    fields['skill_picker.choice.W'] = 'none'
    parsed = form.parse(fields, base=tmp_path)['document']
    assert parsed['agent']['roles']['H']['skill_set'] == 'existing'
    assert selected(parsed, 'W') == []
    assert set(parsed['agent']['roles']) == {'H', 'W'}


def test_invalid_other_field_retains_successful_skill_selection(installed, tmp_path):
    fields = submitted(installed)
    fields['skill_picker.item.A.0'] = 'alpha'
    fields['cfg.budget.max_tokens'] = 'invalid'
    with pytest.raises(form.FormError) as caught:
        form.parse(fields, base=tmp_path)
    retry = submitted(caught.value.draft)
    assert retry['skill_picker.item.A.0'] == 'alpha'
    assert retry['cfg.budget.max_tokens'] == 'invalid'
    retry['cfg.budget.max_tokens'] = '1000'
    parsed = form.parse(retry, base=tmp_path)
    assert selected(parsed['document'], 'A') == ['alpha']


def test_picker_keeps_commented_inline_toml_and_accepts_explicit_set_edits(installed, tmp_path):
    fields = submitted(installed)
    original_set = 'original = { items = ["beta"], future = false }'
    advanced = '# Preserve my inline declaration.\nskills = { sets = { '+original_set+' } } # exact comment\n'
    fields['advanced_toml'] = advanced
    fields['skill_picker.item.W.0'] = 'alpha'
    parsed = form.parse(fields, base=tmp_path)
    retry = submitted(parsed)
    assert retry['advanced_toml'] == advanced
    retained = form.parse(retry, base=tmp_path)
    assert selected(retained['document'], 'W') == ['alpha']
    assert retained['document']['skills']['sets']['original'] == dict(items=['beta'], future=False)
    # An explicit expert edit replaces the generated set on the next submit.
    name = retained['document']['agent']['roles']['W']['skill_set']
    retry['advanced_toml'] = advanced.replace(original_set,
        original_set+', "'+name+'" = { items = ["beta"] }')
    edited = form.parse(retry, base=tmp_path)
    assert selected(edited['document'], 'W') == ['beta']
    assert submitted(edited)['advanced_toml'] == retry['advanced_toml']
    assert form.declaration(form.parse(submitted(edited), base=tmp_path)) == form.declaration(edited)


def test_invalid_advanced_toml_retry_keeps_new_skill_selection(installed, tmp_path):
    fields = submitted(installed)
    advanced = '# Keep this comment while correcting the table.\n[broken'
    fields['advanced_toml'] = advanced
    fields['skill_picker.item.W.0'] = 'alpha'
    with pytest.raises(form.FormError, match='高级设置（TOML）格式错误') as caught:
        form.parse(fields, base=tmp_path)
    retry = submitted(caught.value.draft)
    assert retry['advanced_toml'] == advanced
    assert retry['skill_picker.item.W.0'] == 'alpha'
    retry['advanced_toml'] = advanced.replace('[broken', '[extension]\nflag = false\n')
    parsed = form.parse(retry, base=tmp_path)
    assert selected(parsed['document'], 'W') == ['alpha']
    assert parsed['document']['extension'] == dict(flag=False)
    assert submitted(parsed)['advanced_toml'] == retry['advanced_toml']


def test_picker_can_roundtrip_at_retained_value_capacity(installed, tmp_path):
    fields = submitted(installed)
    fields['advanced_fixed'] = json.dumps([
        [['executor', 'config', 'configuration', 'nullable_' + str(index)], None]
        for index in range(252)])
    fields['skill_picker.choice.H'] = 'none'
    fields['skill_picker.item.W.0'] = 'alpha'
    fields['skill_picker.item.A.1'] = 'beta'
    fields['skill_picker.item.E.0'] = 'alpha'
    fields['skill_picker.item.E.1'] = 'beta'
    parsed = form.parse(fields, base=tmp_path)
    assert len(parsed['advanced_fixed']) == 256
    assert form.declaration(form.parse(submitted(parsed), base=tmp_path)) == form.declaration(parsed)


@pytest.mark.parametrize('limit', ['count', 'text'])
def test_picker_rejects_new_sets_that_exceed_retained_value_capacity(installed, tmp_path, limit):
    fields = submitted(installed)
    if limit == 'count':
        fixed = [[["executor", "config", "configuration", "nullable_" + str(index)], None]
            for index in range(256)]
    else:
        fixed = [[["executor", "config", "configuration", "nullable"], [None, '']]]
        fixed[0][1][1] = 'x' * (256_000 - len(json.dumps(fixed, ensure_ascii=False)))
    fields['advanced_fixed'] = json.dumps(fixed, ensure_ascii=False)
    assert len(fields['advanced_fixed']) <= 256_000
    fields['skill_picker.item.W.0'] = 'alpha'
    with pytest.raises(ValueError, match='新增技能集合.*保留容量'):
        form.parse(fields, base=tmp_path)


def test_existing_instance_uses_only_frozen_sets_and_never_catalogs(installed, tmp_path, monkeypatch):
    document = installed['document']
    document['skills'] = dict(sets=dict(existing=dict(items=['alpha']), off=dict(items=[])))
    document['agent'].update(workers=dict(analyst=dict(skill_set='existing')),
        roles=dict(H=dict(worker='analyst')))
    def forbidden():
        raise AssertionError('Existing instances must not expand installed inventory')
    monkeypatch.setattr(skill_supply, 'catalog', forbidden)
    html = form.render(installed, edit=True)
    assert '<details class="research-skill-settings"><summary>研究技能</summary>' in html
    assert 'skill_picker.item.' not in html
    assert '<option value="none">' not in html
    assert '已有集合：off（空集合，不启用技能）' in html
    fields = Controls(html).fields
    assert form.parse_edit(fields, base=tmp_path, configuration=document)['document'] == document
    fields['skill_picker.choice.H'] = 'set:off'
    parsed = form.parse_edit(fields, base=tmp_path, configuration=document)['document']
    assert parsed['agent']['roles']['H'] == dict(worker='analyst', skill_set='off')
    assert parsed['skills'] == document['skills']
    fields['skill_picker.choice.H'] = 'inherit'
    assert form.parse_edit(fields, base=tmp_path, configuration=document)['document']['agent']['roles']['H'] == dict(worker='analyst')
    fields['skill_picker.choice.H'] = 'none'
    with pytest.raises(ValueError, match='只能选择已冻结'):
        form.parse_edit(fields, base=tmp_path, configuration=document)
    fields['skill_picker.choice.H'] = 'custom'
    with pytest.raises(ValueError, match='只能选择已冻结'):
        form.parse_edit(fields, base=tmp_path, configuration=document)


def test_missing_catalog_keeps_imported_binding_and_shows_diagnostics(installed, tmp_path, monkeypatch):
    installed['document']['skills'] = dict(sets=dict(original=dict(items=['alpha'])))
    installed['document']['agent']['roles'] = dict(H=dict(skill_set='original'))
    monkeypatch.setattr(skill_supply, 'source_root', lambda: tmp_path / 'missing-installation')
    html = form.render(installed)
    assert 'data-skill-diagnostic="true"' in html
    assert '暂不可勾选的技能：alpha' in html
    assert form.declaration(form.parse(Controls(html).fields, base=tmp_path)) == form.declaration(installed)
    with pytest.raises(ValueError, match='alpha'):
        form.preview(installed)


def test_metadata_descriptions_and_diagnostics_are_html_escaped(installed, monkeypatch):
    monkeypatch.setattr(skill_supply, 'catalog', lambda: dict(
        items=[dict(name='alpha', description='<img src=x onerror="boom"> & details')],
        errors=[dict(name='bad<script>', message='<script>boom()</script>')]))
    html = form.render(installed)
    assert '&lt;img src=x onerror=&quot;boom&quot;&gt; &amp; details' in html
    assert 'bad&lt;script&gt;' in html
    assert '&lt;script&gt;boom()&lt;/script&gt;' in html
    assert '<img src=x' not in html
    assert '<script>boom()' not in html


def test_disappeared_checked_asset_rejects_without_rewriting_original(installed, tmp_path):
    fields = submitted(installed)
    fields['skill_picker.item.W.0'] = 'no-longer-installed'
    with pytest.raises(ValueError, match='已不可用'):
        form.parse(fields, base=tmp_path)
    assert 'skills' not in installed['document']


@pytest.mark.parametrize('reference', [[], {}])
def test_invalid_imported_worker_reference_does_not_crash_picker_or_repair_declaration(installed, reference):
    installed['document']['agent']['workers'] = dict(analyst=dict(skill_set='existing'))
    installed['document']['agent']['roles'] = dict(H=dict(worker=reference))
    original = copy.deepcopy(installed['document'])
    html = form.render(installed)
    assert 'id="research-skill-picker"' in html
    assert installed['document'] == original
    with pytest.raises(ValueError):
        form.preview(installed)


def test_deeply_nested_picker_baseline_reports_form_error(installed, tmp_path):
    fields = submitted(installed)
    fields['skill_picker.baseline.H'] = '[' * 2000 + ']' * 2000
    with pytest.raises(ValueError, match='勾选基线无效'):
        form.parse(fields, base=tmp_path)
