"""Structured declarations retain originals and use the CLI preparation path."""
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scidiscovery.research_loop import configuration_form as form, contracts, research_config


class Controls(HTMLParser):
    """Submit successful HTML controls without needing a browser or framework."""
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.textarea = None
        self.select = None
        self.option = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name'):
            if attrs.get('type') != 'checkbox' or 'checked' in attrs:
                self.fields[attrs['name']] = attrs.get('value', '')
        if tag == 'textarea':
            self.textarea = attrs.get('name')
            self.fields[self.textarea] = ''
        if tag == 'select':
            # Browsers do not submit unnamed presentation-only selects.
            self.select = attrs.get('name')
            if self.select:
                self.fields[self.select] = ''
        if tag == 'option':
            self.option = attrs.get('value', '')
            if 'selected' in attrs and self.select:
                self.fields[self.select] = self.option

    def handle_endtag(self, tag):
        if tag == 'textarea': self.textarea = None
        if tag == 'select': self.select = None

    def handle_data(self, data):
        if self.textarea:
            self.fields[self.textarea] += data


def set_setting(fields, path, value):
    """Edit a setting the way a user would: form control if shown, else TOML."""
    if 'cfg.' + path in fields:
        fields['cfg.' + path] = str(value).lower() if type(value) is bool else str(value)
        return fields
    advanced = form.tomllib.loads(fields.get('advanced_toml', ''))
    target = advanced
    *parents, leaf = path.split('.')
    for key in parents:
        target = target.setdefault(key, {})
    target[leaf] = value
    fields['advanced_toml'] = form.advanced_toml(advanced)[0]
    return fields


def submitted(draft):
    return Controls(form.render(draft)).fields


def complete(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    return form.imported(dict(version=1,
        research=dict(root='instance', public_root='public', objective_text='Compare exact alternatives.\nRetain limitations.'),
        agent=dict(launcher=['never-launched']),
        budget=dict(max_tokens=1000, max_seconds=120, max_steps=8)), base=tmp_path)


def test_plugin_fields_roundtrip_and_export_preserves_file_base(tmp_path):
    draft = complete(tmp_path)
    draft['document']['executor'] = dict(config=dict(executor='third_party:binding', config_file='./plugin.toml'))
    fields = submitted(draft)
    assert fields['cfg.executor.config.executor'] == 'third_party:binding'
    assert fields['cfg.executor.config.config_file'] == './plugin.toml'
    parsed = form.parse(fields, base=tmp_path)
    assert form.declaration(parsed) == form.declaration(draft)
    exported = research_config.tomllib.loads(form.export_toml(parsed))
    assert exported['executor']['config']['config_file'] == str(tmp_path / 'plugin.toml')
    assert research_config.from_document(exported, base=tmp_path/'elsewhere')['execution_config'] == {
        'executor': 'third_party:binding', 'config_file': str(tmp_path/'plugin.toml')}


def test_blank_page_has_no_implicit_inputs_or_budgets(tmp_path):
    (tmp_path / 'research.toml').write_text('must not be read')
    draft = form.blank(base=tmp_path)
    fields = submitted(draft)
    assert fields['task_text'] == ''
    for key in ('research.root', 'research.public_root', 'budget.max_tokens', 'budget.max_seconds', 'budget.max_steps', 'agent.model'):
        assert fields['cfg.'+key] == ''
    assert form.parse(fields, base=tmp_path)['document'] == {}
    with pytest.raises(ValueError, match='新实例目录：必填'):
        form.preview(draft)
    draft['document'] = dict(research=dict(root='new', public_root='public'))
    with pytest.raises(ValueError, match='总 tokens 预算：必填'):
        form.preview(draft)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['research.toml']


def test_retired_byte_quota_controls_preserve_imported_legacy_declaration(tmp_path):
    draft=complete(tmp_path)
    draft['document']['budget'].update(max_output_item_bytes=8,max_output_total_bytes=1)
    assert not any(field.path in {'budget.max_output_item_bytes','budget.max_output_total_bytes'}
        for field in form.FIELDS)
    html=form.render(draft)
    assert '单项输出字节上限' not in html and '单节点输出合计字节上限' not in html
    parsed=form.parse(submitted(draft),base=tmp_path)
    assert form.declaration(parsed)==form.declaration(draft)
    checked=form.preview(parsed)
    assert checked['max_output_item_bytes']==8 and checked['max_output_total_bytes']==1


def test_complex_cov_declaration_roundtrips_all_typed_controls_and_export(tmp_path):
    draft = complete(tmp_path)
    document = draft['document']
    document['research']['source_restrictions'] = [dict(source='public/dataset.bin', restriction='restricted')]
    document['research']['user_updates'] = []
    document['agent'].update(backend='codex', model='global', reasoning_effort='high', service_tier='default',
        roles={role:dict(model='model-'+role, reasoning_effort='high', service_tier='priority') for role in 'HWAE'},
        native=dict(sandbox_workspace_write=dict(writable_roots=['runs'], network_access=False,
            exclude_tmpdir_env_var=True, exclude_slash_tmp=True), features=dict(fast_mode=False)))
    document['permissions'] = dict(workspace='workspace-write', network=False, web='cached', subagents=False)
    document['budget'].update(node_targets={role:dict(max_observed_tokens=400,max_seconds=30) for role in 'HWAE'},
        role_shares=dict(version=1, shares=dict(H=0.1,W=0.5,A=0.3,E=0.1)),
        breadth=dict(max_w_seconds=60,max_w_observed_tokens=500,max_requested_repairs=2,initial_calls=1),
        correction=dict(review_after=3,pause_after=2),candidate_extra_slots=2,portfolio_review_after_cycles=3,
        max_output_item_bytes=10000,max_output_total_bytes=20000)
    document['executor'] = dict(mode='local', config=dict(executor='cov_execution:CovExecution',
        configuration=dict(runs_root='runs', nested=dict(enabled=True, groups=[1, 'two', dict(key='value')]))),
        resource_policy=dict(version=1,allowance=dict(wall_time_seconds=120,max_storage_bytes=1024)))
    document['delivery'] = dict(closeout_a=dict(max_observed_tokens=100,max_seconds=20), inflight_tokens=100,
        cleanup_seconds=1,submit_seconds=5,confirm_seconds=5,
        adapter=dict(adapter='adapter.py',launcher_argv=['./harness','literal-relative-arg'],config=dict(evaluation_key='v1'),
            target=dict(destination='original'),inputs=[dict(label='dataset',path='data/file')], source_roots=['public'],readonly_inputs_version=1,
            capabilities=['audit','submit'],effect_scope=dict(kind='declared')))
    document['policies'] = dict(formal_review=True,execution_authority=True,candidate_audit=False,decision_contract=True,
        worker_context='new_context',selection=dict(version=1,extra=dict(keep=[])),
        resume=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',native_home='/private/native'),
        history_observer=dict(adapter='/host/observer.py',config=dict(runs='runs',evaluation_key='v1')))
    document['ui'] = dict(port=0,open_browser=False,approval_secret_file='private/operator.key')
    fields = submitted(draft)
    parsed = form.parse(fields, base=tmp_path)
    assert form.declaration(parsed) == document
    exported = research_config.tomllib.loads(form.export_toml(parsed))
    # Canonical declared paths keep their meaning when the optional file moves.
    assert research_config.from_document(exported, base=tmp_path/'elsewhere') == research_config.from_document(document,base=tmp_path)
    assert exported['executor']['config'] == document['executor']['config']
    assert exported['delivery']['adapter']['launcher_argv'][1] == 'literal-relative-arg'


def test_extensions_are_editable_and_optional_groups_can_be_omitted(tmp_path):
    draft = complete(tmp_path)
    draft['document']['executor'] = dict(config=dict(configuration=dict(flag=True,deep=[dict(value=5)])))
    fields = submitted(draft)
    assert 'flag = true' in fields['advanced_toml']
    fields['advanced_toml'] = fields['advanced_toml'].replace('flag = true', 'flag = false')
    parsed = form.parse(fields,base=tmp_path)
    assert parsed['document']['executor']['config']['configuration'] == dict(flag=False,deep=[dict(value=5)])
    assert 'executor' not in form.parse(dict(fields, advanced_toml=''),base=tmp_path)['document']
    with pytest.raises(ValueError,match='有效的整数'):
        form.parse(dict(fields, **{'cfg.budget.max_tokens':'not-integer'}),base=tmp_path)
    with pytest.raises(ValueError,match='不支持的表单字段'):
        form.parse(dict(fields, surprise='ignored?'),base=tmp_path)


def test_unknown_imported_values_are_retained_then_rejected_not_dropped(tmp_path):
    draft = complete(tmp_path)
    draft['document']['agent']['misspelled'] = dict(exact=['a',2,True])
    parsed = form.parse(submitted(draft),base=tmp_path)
    assert parsed['document']['agent']['misspelled'] == dict(exact=['a',2,True])
    with pytest.raises(ValueError,match='unknown misspelled'):
        form.preview(parsed)
    assert not (tmp_path/'instance').exists()


def test_inline_preview_has_no_writes_create_freezes_exact_original_and_update_mapping(tmp_path):
    draft = complete(tmp_path)
    update = tmp_path/'update.md'; update.write_text('Exact user update.')
    draft['document']['research']['user_updates'] = ['update.md']
    original = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*'))
    preview = form.preview(draft)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*')) == original
    created = form.create(draft)
    root = tmp_path/'instance'
    assert created['objective'] == preview['objective'] == draft['task_text']
    objective = root/'project/global/objective.md'
    assert objective.read_bytes() == draft['task_text'].encode()
    assert objective.stat().st_mode & 0o222 == 0
    provenance = contracts.read(root/created['source_provenance']['path'])
    assert [row['origin']['path'] for row in provenance['globals']] == [str(objective),str(update)]
    assert [row['material']['source'] for row in provenance['globals']] == ['project/global/objective.md',str(update)]
    with pytest.raises(ValueError,match='fresh directory'):
        form.create(draft)


def test_file_and_inline_are_exclusive_and_protocol_needs_explicit_model(tmp_path):
    draft = complete(tmp_path)
    draft['document']['research']['objective'] = 'other.md'
    with pytest.raises(ValueError,match='只能选择一项'):
        form.imported(draft['document'],base=tmp_path)
    with pytest.raises(ValueError,match='exactly one'):
        research_config.from_document(draft['document'],base=tmp_path)
    del draft['document']['research']['objective']
    draft['document']['agent']['backend'] = 'protocol'
    with pytest.raises(ValueError,match='explicit model'):
        form.preview(draft)
    draft['document']['agent']['model'] = 'protocol-model'
    checked = form.preview(draft)
    assert checked['model'] == 'protocol-model'
    assert checked['reasoning_effort'] is None
    assert checked['service_tier'] is None


def test_shared_parser_file_and_document_path_meaning_agree(tmp_path):
    draft = complete(tmp_path)
    path = tmp_path/'optional.toml'
    path.write_text(form.export_toml(draft))
    assert research_config.load(path) == research_config.from_document(form.declaration(draft),base=tmp_path)
    assert form.declaration(form.load(path)) == research_config.read_declaration(path)


def test_invalid_edit_retains_all_controls_for_correction(tmp_path):
    draft = complete(tmp_path)
    draft['document']['executor'] = dict(config=dict(nested=dict(number=3)))
    fields = submitted(draft)
    fields['task_text'] = 'New exact task'
    fields['cfg.agent.model'] = 'new-model'
    fields['cfg.budget.max_tokens'] = 'oops'
    broken = fields['advanced_toml'].replace('number = 3', 'number = = 3')
    fields['advanced_toml'] = broken
    with pytest.raises(form.FormError, match='高级设置（TOML）格式错误') as caught:
        form.parse(fields,base=tmp_path)
    retained = submitted(caught.value.draft)
    assert retained['cfg.budget.max_tokens'] == 'oops'
    assert retained['cfg.agent.model'] == 'new-model'
    assert retained['task_text'] == 'New exact task'
    assert retained['advanced_toml'] == broken
    with pytest.raises(ValueError,match='类型错误'):
        form.create(caught.value.draft)
    assert not (tmp_path/'instance').exists()


def test_configuration_edit_preserves_and_edits_explicit_null_plugin_limits(tmp_path):
    draft = complete(tmp_path)
    limits = dict.fromkeys(('max_file_bytes', 'max_log_bytes', 'max_result_bytes', 'max_total_bytes'))
    draft['document']['executor'] = dict(config=dict(configuration=limits))
    html = form.render(draft, edit=True)
    assert '包含空值（null）' in html and 'executor.config.configuration.max_file_bytes' in html
    fields = Controls(html).fields
    parsed = form.parse_edit(fields, base=tmp_path, configuration=draft['document'])
    assert parsed['document'] == draft['document']
    # Writing the same key in TOML replaces the retained null.
    fields['advanced_toml'] = fields['advanced_toml'].replace('[executor.config.configuration]', '[executor.config.configuration]\nmax_file_bytes = 1024')
    parsed = form.parse_edit(fields, base=tmp_path, configuration=draft['document'])
    assert parsed['document']['executor']['config']['configuration'] == dict(limits, max_file_bytes=1024)
    fields = Controls(form.render(form.imported(parsed['document'], base=tmp_path), edit=True)).fields
    assert 'max_file_bytes = 1024' in fields['advanced_toml']
    assert form.parse_edit(fields, base=tmp_path, configuration=draft['document'])['document'] == parsed['document']


def test_retained_null_records_reject_tampering(tmp_path):
    draft = complete(tmp_path)
    draft['document']['executor'] = dict(config=dict(configuration=dict(max_file_bytes=None)))
    fields = Controls(form.render(draft, edit=True)).fields
    assert fields['advanced_fixed'] == '[[["executor", "config", "configuration", "max_file_bytes"], null]]'
    with pytest.raises(ValueError, match='保留的空值记录无效'):
        form.parse_edit(dict(fields, advanced_fixed='[["not a path"]]'), base=tmp_path, configuration=draft['document'])


@pytest.mark.parametrize('plugin_value', [None, dict(nested=None), [1, None]])
def test_toml_export_rejects_null_without_changing_draft(tmp_path, plugin_value):
    draft = complete(tmp_path)
    draft['document']['executor'] = dict(config=dict(configuration=dict(limit=plugin_value)))
    before = form.declaration(draft)
    with pytest.raises(ValueError, match='TOML 不支持空值'):
        form.export_toml(draft)
    assert form.declaration(draft) == before
    fields = Controls(form.render(draft, edit=True)).fields
    assert form.parse_edit(fields, base=tmp_path, configuration=before)['document'] == before


def test_toml_export_without_null_keeps_typed_plugin_values(tmp_path):
    draft = complete(tmp_path)
    configuration = dict(limit=1024, disabled=False, empty='', labels=['None', 0])
    draft['document']['executor'] = dict(config=dict(configuration=configuration))
    exported = research_config.tomllib.loads(form.export_toml(draft))
    assert exported['executor']['config']['configuration'] == configuration
    imported = form.imported(exported, base=tmp_path)
    assert research_config.tomllib.loads(form.export_toml(imported)) == exported


def test_policy_forms_edit_real_schemas_and_preserve_extra_fields(tmp_path):
    draft = complete(tmp_path)
    draft['document']['budget']['role_shares'] = dict(version=1, min_w_share=.65,
        max_thinking_share=.35, thinking_node_target_tokens=250000,
        imported_extension=dict(keep=[]))
    draft['document']['policies'] = dict(resume=dict(max_attempts=2,
        protocol='codex_exec_resume', usage_scope='turn', output_repair=True,
        output_only=False))
    fields = submitted(draft)
    text = fields['advanced_toml']
    assert '[budget.role_shares]' in text and 'min_w_share = 0.65' in text
    assert '[policies.resume]' in text and 'output_only = false' in text
    fields['advanced_toml'] = text.replace('min_w_share = 0.65', 'min_w_share = 0.6').replace('max_attempts = 2', 'max_attempts = 1')
    parsed = form.parse(fields, base=tmp_path)
    assert parsed['document']['budget']['role_shares'] == dict(version=1, min_w_share=.6,
        max_thinking_share=.35, thinking_node_target_tokens=250000,
        imported_extension=dict(keep=[]))
    assert parsed['document']['policies']['resume']['max_attempts'] == 1
    assert parsed['document']['policies']['resume']['output_only'] is False
    assert research_config.tomllib.loads(form.export_toml(parsed))['budget'] == parsed['document']['budget']


@pytest.mark.parametrize('edit', [False, True])
def test_automatic_recovery_controls_retain_omission_false_and_finite_limits(tmp_path, edit):
    from scidiscovery.research_loop import native_recovery
    draft = complete(tmp_path)
    def parse(fields):
        return (form.parse_edit(fields, base=tmp_path, configuration=draft['document'])
            if edit else form.parse(fields, base=tmp_path))
    legacy = dict(max_attempts=2, protocol='codex_exec_resume', usage_scope='turn',
        output_repair=True, output_only=True)
    automatic = dict(native_recovery.policy({}), enabled=False,
        initial_backoff_seconds=.25, extension=dict(keep=[]))
    for policy in (legacy, dict(legacy, automatic_recovery={}),
                   dict(legacy, automatic_recovery=automatic)):
        draft['document']['policies'] = dict(resume=policy)
        html = form.render(draft, edit=edit)
        fields = Controls(html).fields
        assert (parse(fields)['document'] if edit else form.declaration(parse(fields))) == draft['document']
    assert '[policies.resume.automatic_recovery]' in fields['advanced_toml']
    assert 'id="advanced-settings"' in html
    fields['advanced_toml'] = fields['advanced_toml'].replace(
        'max_retries = '+str(automatic['max_retries']), 'max_retries = 4').replace(
        'max_attempts = '+str(automatic['max_attempts']), 'max_attempts = 6')
    parsed = parse(fields)
    assert parsed['document']['policies']['resume']['automatic_recovery'] == dict(automatic, max_retries=4, max_attempts=6)
    exported = research_config.tomllib.loads(form.export_toml(parsed))
    assert exported['policies']['resume'] == parsed['document']['policies']['resume']


@pytest.mark.parametrize('path', ['agent.model', 'budget.max_tokens'])
def test_advanced_toml_rejects_keys_also_set_by_form(tmp_path, path):
    draft = complete(tmp_path)
    draft['document']['agent']['model'] = 'form-model'
    fields = submitted(draft)
    section, key = path.split('.')
    line = key+' = '+('"other"' if key == 'model' else '5')
    text = fields['advanced_toml']
    fields['advanced_toml'] = (text.replace('['+section+']', '['+section+']\n'+line) if '['+section+']' in text
        else text+'\n['+section+']\n'+line+'\n')
    with pytest.raises(form.FormError, match='高级设置与表单字段重复'):
        form.parse(fields, base=tmp_path)


def test_collapsed_controls_keep_custom_values_false_empty_and_omission(tmp_path):
    draft = complete(tmp_path)
    draft['document']['agent'].update(reasoning_effort='backend-specific-effort',
        service_tier='backend-specific-tier', roles=dict(E=dict(model='')))
    draft['document']['permissions'] = dict(network=False)
    draft['document']['research']['user_updates'] = []
    html = form.render(draft)
    fields = Controls(html).fields
    parsed = form.parse(fields, base=tmp_path)
    assert form.declaration(parsed) == form.declaration(draft)
    assert 'data-picker="effort"' in html and '>推理力度<' in html
    assert 'E · 证据检索与提取' in html
    assert 'data-config-group="agent.roles" open' in html
    del fields['present.agent.roles.E.model']
    fields['advanced_toml'] = ''
    omitted = form.parse(fields, base=tmp_path)['document']
    assert 'roles' not in omitted['agent']
    assert 'user_updates' not in omitted['research']
    assert omitted['permissions']['network'] is False
    exported = research_config.tomllib.loads(form.export_toml(parsed))
    assert exported['agent']['roles']['E']['model'] == ''
    assert exported['research']['user_updates'] == []


@pytest.mark.parametrize('edit', [False, True])
def test_compact_role_matrix_shares_rows_and_keeps_accessible_labels(tmp_path, edit):
    draft = complete(tmp_path)
    draft['document']['agent']['roles'] = dict(H=dict(reasoning_effort='custom-effort'), E=dict(model=''))
    draft['document']['budget']['node_targets'] = {
        role: dict(max_observed_tokens=400, max_seconds=30.5) for role in 'HWAE'}
    html = form.render(draft, edit=edit)

    class Matrix(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inside = False
            self.body = False
            self.rows = 0
            self.controls = []
            self.labels = {}
            self.label = None
            self.feed(html)

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'table' and 'config-role-matrix' in attrs.get('class', '').split():
                self.inside = True
            if not self.inside:
                return
            if tag == 'tbody':
                self.body = True
            if tag == 'tr' and self.body:
                self.rows += 1
            if tag == 'input' and attrs.get('name', '').startswith('cfg.'):
                self.controls.append(attrs)
            if tag == 'label':
                self.label = attrs.get('for')

        def handle_endtag(self, tag):
            if tag == 'table':
                self.inside = False
            if tag == 'label':
                self.label = None

        def handle_data(self, data):
            if self.label:
                self.labels[self.label] = self.labels.get(self.label, '')+data

    matrix = Matrix()
    assert matrix.rows == 4
    assert len(matrix.controls) == 8
    assert len({item['name'] for item in matrix.controls}) == 8
    for item in matrix.controls:
        path = item['name'].removeprefix('cfg.')
        field = next(field for field in form.FIELDS if field.path == path)
        assert matrix.labels[item['id']] == field.label
        assert item['aria-describedby'] == 'role-settings-help'
        assert html.count('name="'+item['name']+'"') == 1
    assert 'id="budget-settings"' in html
    fields = Controls(html).fields
    assert '[budget.node_targets.H]' in fields['advanced_toml']
    parsed = (form.parse_edit(fields, base=tmp_path, configuration=draft['document'])
        if edit else form.parse(fields, base=tmp_path))
    assert (parsed['document'] if edit else form.declaration(parsed)) == draft['document']


def test_form_shows_only_everyday_fields_with_inline_help(tmp_path):
    html = form.render(complete(tmp_path), edit=True)
    for label in ('模型用量（tokens）', '活动时间（秒）', '节点上限（步）', '推理力度', '自动方向复核'):
        assert label in html
    assert 'name="cfg.budget.max_output_item_bytes"' not in html
    assert 'name="cfg.budget.breadth.max_w_seconds"' not in html
    assert 'name="enabled.policies.resume"' not in html
    assert 'config-node-key' not in html and '<details class="config-help">' not in html
    assert '<small class="field-help" id="config-budget-max_seconds-help">' in html
    assert 'name="advanced_toml"' in html
    names = set(Controls(html).fields)
    assert len([name for name in names if name.startswith('cfg.')]) <= 26


@pytest.mark.parametrize('edit', [False, True])
def test_advanced_settings_roundtrip_through_toml(tmp_path, edit):
    draft = complete(tmp_path)
    draft['document']['budget'].update(candidate_extra_slots=0,
        role_shares=dict(version=1, min_w_share=0, max_thinking_share=.35,
            thinking_node_target_tokens=250, extension=dict(enabled=False, empty=[])))
    draft['document']['policies'] = dict(formal_review=False, resume=dict(max_attempts=1,
        protocol='codex_exec_resume', usage_scope='turn', output_only=False, native_home=''))
    draft['document']['executor'] = dict(config=dict(configuration=dict(
        enabled=False, count=0, text='', nested=dict(items=[]))))
    html = form.render(draft, edit=edit)
    assert '<details class="advanced-settings" id="advanced-settings"><summary>高级设置（TOML）' in html
    fields = Controls(html).fields
    fields['advanced_toml'] = fields['advanced_toml'].replace('candidate_extra_slots = 0', 'candidate_extra_slots = 1').replace(
        'output_only = false', 'output_only = true')
    parsed = (form.parse_edit(fields, base=tmp_path, configuration=draft['document'])
        if edit else form.parse(fields, base=tmp_path))
    expected = form.declaration(draft)
    expected['budget']['candidate_extra_slots'] = 1
    expected['policies']['resume']['output_only'] = True
    assert (parsed['document'] if edit else form.declaration(parsed)) == expected


def test_list_settings_are_edited_as_toml_arrays(tmp_path):
    draft = complete(tmp_path)
    fields = submitted(draft)
    assert 'launcher = ["never-launched"]' in fields['advanced_toml']
    omitted = form.parse(dict(fields, advanced_toml=fields['advanced_toml'].replace('launcher = ["never-launched"]', '')), base=tmp_path)
    assert 'launcher' not in omitted['document'].get('agent', {})
    fields['advanced_toml'] += '\n[research]\nuser_updates = ["one update.md"]\n'
    parsed = form.parse(fields, base=tmp_path)
    assert parsed['document']['research']['user_updates'] == ['one update.md']


def test_edit_form_roundtrips_operational_groups_and_keeps_instance_inputs_server_side(tmp_path):
    draft = complete(tmp_path)
    draft['document']['ui'] = dict(approval_secret_file='/private/operator.key')
    draft['document']['permissions'] = dict(workspace='workspace-write', network=False, web='live', subagents=False)
    draft['document']['executor'] = dict(mode='external', config=dict(configuration=dict(nested=[1, False, 'exact'])))
    draft['document']['delivery'] = dict(adapter=dict(config=dict(destination='exact-target')))
    fields = Controls(form.render(draft, edit=True)).fields
    assert not any(name.startswith(('cfg.research.', 'cfg.ui.', 'cfg.version', 'task_')) for name in fields)
    assert '/private/operator.key' not in form.render(draft, edit=True)
    assert '[research]' not in fields['advanced_toml'] and '[ui]' not in fields['advanced_toml']
    assert fields['cfg.permissions.web'] == 'live'
    assert 'mode = "external"' in fields['advanced_toml']
    parsed = form.parse_edit(fields, base=tmp_path, configuration=draft['document'])
    assert parsed['document'] == draft['document']
    fields['cfg.agent.model'] = 'next-model'
    fields['cfg.budget.max_tokens'] = 'invalid budget'
    with pytest.raises(form.FormError) as caught:
        form.parse_edit(fields, base=tmp_path, configuration=draft['document'])
    retry = Controls(form.render(caught.value.draft, edit=True)).fields
    assert retry['cfg.budget.max_tokens'] == 'invalid budget'
    assert retry['cfg.agent.model'] == 'next-model'
    assert caught.value.draft['document']['research'] == draft['document']['research']
    fields['advanced_toml'] += '\n[research]\nroot = "/another-instance"\n'
    with pytest.raises(ValueError, match='不能在研究配置中修改'):
        form.parse_edit(fields, base=tmp_path, configuration=draft['document'])


@pytest.mark.parametrize('seconds', [900.0, 0.25])
def test_normalized_breadth_seconds_survive_invalid_edit_correction_and_save(tmp_path, seconds):
    from scidiscovery.research_loop import configuration_updates, search
    draft = complete(tmp_path)
    # The ordinary creation path supplies a float-valued default; sub-second
    # ceilings are valid too. Counts and token ceilings remain integers.
    if seconds != search.DEFAULT_POLICY['max_w_seconds']:
        draft['document']['budget']['breadth'] = dict(search.DEFAULT_POLICY, max_w_seconds=seconds)
    form.create(draft)
    root = tmp_path / 'instance'
    state = configuration_updates.status(root)
    assert state['configuration']['budget']['breadth']['max_w_seconds'] == seconds
    editable = form.imported(state['configuration'], base=root)
    fields = Controls(form.render(editable, edit=True)).fields
    fields['cfg.agent.model'] = 'model-after-correction'
    fields['cfg.budget.max_tokens'] = 'invalid total'
    with pytest.raises(form.FormError) as caught:
        form.parse_edit(fields, base=root, configuration=state['configuration'])
    assert 'W 软目标秒数' not in str(caught.value)
    retry = Controls(form.render(caught.value.draft, edit=True)).fields
    assert 'max_w_seconds = '+str(seconds) in retry['advanced_toml']
    assert retry['cfg.agent.model'] == 'model-after-correction'
    retry['cfg.budget.max_tokens'] = str(state['configuration']['budget']['max_tokens'])
    corrected = form.parse_edit(retry, base=root, configuration=state['configuration'])
    saved = configuration_updates.submit(root, corrected['document'], expected_revision=state['revision'])
    assert saved['pending']
    assert saved['settings']['model'] == 'model-after-correction'
    assert saved['settings']['search_policy']['max_w_seconds'] == seconds
    assert type(saved['settings']['search_policy']['max_w_observed_tokens']) is int
    assert type(corrected['document']['budget']['breadth']['max_w_observed_tokens']) is int


def test_registered_provider_describes_native_controls_and_keeps_typed_roundtrip(tmp_path, monkeypatch):
    from scidiscovery.research_loop import backends
    class Independent(backends.Protocol):
        name='native_fields_fixture'
        @staticmethod
        def configuration_fields():
            return (dict(path='agent.native.resource',label='Exact provider resource',
                description='Use the selected provider resource.'),
                dict(path='agent.native.enabled',label='Provider feature enabled',kind='boolean'))
    monkeypatch.setitem(backends._PROVIDERS,Independent.name,Independent)
    def forbidden():
        raise AssertionError('Independent form requested the default provider schema')
    monkeypatch.setattr(backends.Codex,'configuration_fields',forbidden)
    draft=complete(tmp_path)
    draft['document']['agent'].update(backend=Independent.name,
        native=dict(resource='resource.toml',enabled=False,extension=dict(keep=[1,'two'])))
    html=form.render(draft)
    fields=Controls(html).fields
    assert 'resource = "resource.toml"' in fields['advanced_toml']
    assert 'Use the selected provider resource.' in html
    assert 'fast_mode' not in html
    assert form.declaration(form.parse(fields,base=tmp_path))==form.declaration(draft)


def test_backend_switch_consumes_rendered_controls_and_preserves_each_provider_draft(tmp_path, monkeypatch):
    from scidiscovery.research_loop import backends
    class Independent(backends.Protocol):
        name='switch_fields_fixture'
        @staticmethod
        def configuration_fields():
            return (dict(path='agent.native.resource',label='Provider resource'),
                dict(path='agent.native.enabled',label='Provider feature',kind='boolean'))
        @staticmethod
        def declaration_options(options,*,base): return dict(options)
    monkeypatch.setitem(backends._PROVIDERS,Independent.name,Independent)
    draft=complete(tmp_path)
    original_native=dict(features=dict(fast_mode=True),
        sandbox_workspace_write=dict(writable_roots=['original-native-root']))
    draft['document']['agent'].update(native=original_native,model='retained-model')
    draft['document']['permissions']=dict(network=False,subagents=False)
    original_permissions=dict(draft['document']['permissions'])
    def switch(current, backend_name, native_toml=None):
        fields=submitted(current)
        fields['cfg.agent.backend']=backend_name
        if native_toml is not None:
            fields['advanced_toml']+=native_toml
        with pytest.raises(form.FormError,match='后端已切换') as caught:
            form.parse(fields,base=tmp_path)
        changed=caught.value.draft
        assert changed['document']['agent']['backend']==backend_name
        assert changed['document']['agent']['model']=='retained-model'
        assert changed['document']['permissions']==original_permissions
        assert not (tmp_path/'instance').exists()
        return changed
    draft=switch(draft,'protocol')
    assert 'native' not in draft['document']['agent']
    assert draft['native_drafts']['codex']==original_native
    assert 'fast_mode' not in submitted(draft)['advanced_toml']
    draft=switch(draft,Independent.name)
    assert draft['native_drafts']['protocol'] is None
    draft=switch(draft,'codex','\n[agent.native]\nresource = "independent-resource.toml"\nenabled = false\n')
    assert draft['document']['agent']['native']==original_native
    independent_native=dict(resource='independent-resource.toml',enabled=False)
    assert draft['native_drafts'][Independent.name]==independent_native
    assert 'original-native-root' in submitted(draft)['advanced_toml']
    draft=switch(draft,Independent.name)
    assert draft['document']['agent']['native']==independent_native
    stable=form.parse(submitted(draft),base=tmp_path)
    assert form.declaration(stable)==form.declaration(draft)
    assert stable['native_drafts']['codex']==original_native
    exported=form.export_toml(stable)
    assert 'rendered_backend' not in exported and 'native_drafts' not in exported
    assert 'independent-resource.toml' in exported
    assert 'original-native-root' not in exported


def test_model_catalog_reads_codex_cache_and_pi_listing(tmp_path, monkeypatch):
    import json
    from scidiscovery.research_loop import model_catalog, pi_backend
    home = tmp_path / 'codex'
    home.mkdir()
    (home / 'models_cache.json').write_text(json.dumps(dict(fetched_at='2026-11-11T00:00:00Z', models=[
        dict(slug='visible-model', display_name='Visible', visibility='list', default_reasoning_level='low',
             supported_reasoning_levels=[dict(effort='low'), dict(effort='high')], service_tiers=[dict(id='priority')]),
        dict(slug='hidden-model', visibility='hide')])))
    calls = []
    def listing(argv, **context):
        calls.append((argv, context))
        return 'provider model context max-out thinking images\nacme fast-1 1M 8K yes no\nacme plain 1M 8K no no\n'
    monkeypatch.setattr(pi_backend, '_catalog_output', listing)
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(tmp_path / 'pi-agent'))
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setattr(model_catalog, '_CACHE', {})
    codex = model_catalog.load('codex')
    assert [model['id'] for model in codex['models']] == ['visible-model']
    assert codex['models'][0]['efforts'] == ['low', 'high'] and codex['models'][0]['tiers'] == ['default', 'priority']
    pi = model_catalog.load('pi', launcher=['./custom-pi'], base=tmp_path)
    assert [model['id'] for model in pi['models']] == ['acme/fast-1', 'acme/plain']
    assert pi['models'][0]['efforts'][-1] == 'max' and pi['models'][1]['efforts'] == ['off']
    assert calls[0][0][0] == str(tmp_path / 'custom-pi')
    assert calls[0][0][-1] == '--list-models' and '--offline' in calls[0][0] and '--no-extensions' in calls[0][0]
    assert calls[0][1]['env']['PI_CODING_AGENT_DIR'] == str(tmp_path / 'pi-agent')
    assert model_catalog.load('protocol')['models'] == []
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'missing'))
    assert model_catalog.load('codex')['error'].startswith('未找到 Codex 模型目录')


def test_model_pickers_offer_catalog_choices_and_keep_exact_typed_values(tmp_path):
    draft = complete(tmp_path)
    draft['document']['agent'].update(model='not-in-catalog', reasoning_effort='high')
    draft['document']['agent']['roles'] = dict(W=dict(model='acme/custom:model[exact]', reasoning_effort='low'))
    catalogs = dict(codex=dict(models=[dict(id='listed', label='Listed', efforts=['low', 'high'], tiers=['default'])],
                               source='fixture', fetched_at=None, error=None),
                    pi=dict(models=[], source=None, fetched_at=None, error='no pi'))
    html = form.render(draft, catalogs=catalogs)
    assert 'id="model-catalog"' in html and '"listed"' in html
    assert 'data-picker="model" data-scope="global"' in html and 'data-picker="effort" data-scope="H"' in html
    assert html.index('name="cfg.agent.backend"') < html.index('name="cfg.agent.model"')
    fields = Controls(html).fields
    assert fields['cfg.agent.model'] == 'not-in-catalog'
    assert fields['cfg.agent.roles.W.model'] == 'acme/custom:model[exact]'
    assert form.declaration(form.parse(fields, base=tmp_path)) == form.declaration(draft)


def test_supervision_mode_select_writes_a_complete_policy(tmp_path):
    from scidiscovery.research_loop import supervision
    draft = complete(tmp_path)
    fields = submitted(draft)
    assert fields['cfg.policies.supervision.mode'] == ''
    assert 'supervision' not in form.parse(fields, base=tmp_path)['document'].get('policies', {})
    fields['cfg.policies.supervision.mode'] = 'notice'
    parsed = form.parse(fields, base=tmp_path)
    selected = parsed['document']['policies']['supervision']
    assert selected['mode'] == 'notice'
    assert supervision.policy(selected)['mode'] == 'notice'
    checked = form.preview(parsed)
    assert checked['supervision_policy']['mode'] == 'notice'
    # An existing default policy shows only the select, not a TOML copy.
    html = form.render(form.imported(form.declaration(parsed), base=tmp_path), edit=True)
    assert '[policies.supervision]' not in html and '<option value="notice" selected>' in html


@pytest.mark.parametrize('advanced_mode', ['off', 'notice'])
def test_supervision_mode_rejects_duplicate_advanced_declaration(tmp_path, advanced_mode):
    draft = complete(tmp_path)
    fields = submitted(draft)
    fields['cfg.policies.supervision.mode'] = 'notice'
    fields['advanced_toml'] += '\n[policies.supervision]\nmode = "' + advanced_mode + '"\n'
    with pytest.raises(form.FormError, match='policies.supervision.mode.*重复') as caught:
        form.parse(fields, base=tmp_path)
    # Both edited controls remain available for correction after the error.
    assert caught.value.draft['document']['policies']['supervision']['mode'] == 'notice'
    assert 'mode = "' + advanced_mode + '"' in caught.value.draft['advanced_text']
    assert not (tmp_path / 'instance').exists()


def test_backend_refresh_uses_selected_catalog_and_retains_unfinished_draft(tmp_path, monkeypatch):
    from scidiscovery.research_loop import model_catalog
    calls=[]
    def catalog(name, **context):
        calls.append((name,context))
        return dict(models=[dict(id=name+'/listed',label='Listed',efforts=[],tiers=[])],
            source='selected provider fixture',fetched_at=None,error=None)
    monkeypatch.setattr(model_catalog,'load',catalog)
    draft=form.blank(base=tmp_path)
    draft['task_mode']='file'
    draft['document']=dict(research=dict(objective='not-yet-present.md'),
        agent=dict(model='exact/custom:model[1]',native=dict(features=dict(fast_mode=False)),
            roles=dict(W=dict(model='exact/worker:model[2]'))))
    fields=submitted(draft)
    fields['cfg.agent.backend']='protocol'
    changed=form.parse(fields,base=tmp_path,refresh_backend=True)
    assert changed['task_mode']=='file' and changed['document']['research']==draft['document']['research']
    assert changed['document']['agent']['model']=='exact/custom:model[1]'
    assert changed['document']['agent']['roles']==draft['document']['agent']['roles']
    assert changed['native_drafts']['codex']==draft['document']['agent']['native']
    fields=submitted(changed)
    fields['cfg.agent.backend']='codex'
    restored=form.parse(fields,base=tmp_path,refresh_backend=True)
    html=form.render(restored)
    assert [name for name,_ in calls]==['codex','protocol','codex']
    assert calls[-1][1]==dict(launcher=None,native=draft['document']['agent']['native'],base=str(tmp_path))
    assert restored['document']['agent']['native']==draft['document']['agent']['native']
    assert 'data-resume-protocol="codex_exec_resume"' in html
    assert 'form.requestSubmit(refresh)' in form.SCRIPT_SOURCE
    assert 'refresh.formNoValidate = true' in form.SCRIPT_SOURCE
    assert sorted(path.name for path in tmp_path.iterdir())==[]


def test_resume_protocol_defaults_are_displayed_without_resolving_a_launcher(tmp_path, monkeypatch):
    from scidiscovery.research_loop import backends, model_catalog
    class Independent(backends.Protocol):
        @staticmethod
        def resume_policy_defaults():return dict(protocol='fixture_resume',usage_scope='turn')
        def default_resume_policy(self):raise AssertionError('Rendering must not qualify a launcher')
    monkeypatch.setitem(backends._PROVIDERS,'fixture-resume',Independent)
    monkeypatch.setattr(model_catalog,'load',lambda *a,**kw:model_catalog.unavailable('fixture'))
    draft=complete(tmp_path)
    draft['document']['agent']['backend']='fixture-resume'
    before=form.declaration(draft)
    html=form.render(draft)
    assert 'data-resume-protocol="fixture_resume"' in html
    assert form.declaration(form.parse(Controls(html).fields,base=tmp_path))==before


@pytest.mark.parametrize('backend_name,protocol_name',[('codex','codex_exec_resume'),('pi','pi_rpc_v1')])
def test_partial_resume_policy_inherits_only_backend_protocol_and_usage_scope(tmp_path,backend_name,protocol_name):
    import sys
    from scidiscovery.research_loop import continuation
    options=dict(root=tmp_path/'instance',objective=tmp_path/'objective',public_root=tmp_path,
        launcher=[sys.executable],native_backend=backend_name,model='acme/exact',reasoning_effort='low',
        max_tokens=1000,max_seconds=120,max_steps=8,experiment_mode='external',
        native_options=dict(workspace='workspace-write',network=True,web='disabled',subagents=False),
        resume_policy=dict(max_attempts=1,automatic_recovery=dict(enabled=False)))
    expected=dict(options['resume_policy'],protocol=protocol_name,usage_scope='turn')
    normalized=research_config.normalize(options)
    assert normalized['resume_policy']==expected
    assert options['resume_policy']==dict(max_attempts=1,automatic_recovery=dict(enabled=False))
    continuation.policy(expected,[sys.executable],backend=backend_name)
    explicit=dict(options, resume_policy=dict(max_attempts=1,protocol='wrong',usage_scope='turn'))
    with pytest.raises(ValueError,match='protocol|Resume'):
        normalized=research_config.normalize(explicit)
        continuation.policy(normalized['resume_policy'],[sys.executable],backend=backend_name)
    incomplete=dict(options,resume_policy={})
    with pytest.raises(ValueError,match='finite attempts'):
        normalized=research_config.normalize(incomplete)
        continuation.policy(normalized['resume_policy'],[sys.executable],backend=backend_name)


@pytest.mark.parametrize('launcher,expected',[(['pi'],None),(['./chosen-pi'],['./chosen-pi'])])
def test_backend_switch_adapts_only_exact_provider_default_launcher(tmp_path,monkeypatch,launcher,expected):
    from scidiscovery.research_loop import model_catalog
    calls=[]
    def load(name,**context):
        calls.append((name,context))
        return model_catalog.unavailable('Exact launcher context fixture')
    monkeypatch.setattr(model_catalog,'load',load)
    draft=complete(tmp_path)
    draft['document']['agent'].update(backend='pi',launcher=launcher,model='acme/exact',reasoning_effort='low')
    fields=submitted(draft)
    fields['cfg.agent.backend']='codex'
    changed=form.parse(fields,base=tmp_path,refresh_backend=True)
    assert changed['document']['agent'].get('launcher')==expected
    form.render(changed)
    assert calls[-1]==('codex',dict(launcher=expected,native=None,base=str(tmp_path)))
    assert draft['document']['agent']['launcher']==launcher


def test_configuration_refresh_owns_one_state_and_renderer_uses_supplied_facts(tmp_path,monkeypatch):
    from urllib.parse import urlencode
    from scidiscovery.research_loop import budget,configuration_updates,instance_ui,model_catalog
    from test_execution_authority import prepare
    root,key=prepare(tmp_path,enabled=False)
    ui=instance_ui.InstanceUI(root,secret_file=key,approval_host=False);ui.host='127.0.0.1:18777'
    _,_,headers=ui.get('/manage?'+urlencode(dict(token=ui.management_token)),host=ui.host)
    cookie=headers['Set-Cookie'];token=ui._browser(cookie)
    _,page,_=ui.get('/configuration',host=ui.host,cookie=cookie)
    fields=Controls(page.decode()).fields
    fields.update({'mode':'refresh','cfg.agent.backend':'protocol'})
    original_status=configuration_updates.status
    observations=[]
    def status(root):
        observations.append(True)
        return original_status(root)
    monkeypatch.setattr(configuration_updates,'status',status)
    assert ui._control_post(token,'configuration-update',fields)[0]==200
    assert observations==[True]
    state=original_status(root)
    originals=instance_ui.display.settings(root)
    ceilings=budget.ceilings(root)
    draft=form.imported(state['configuration'],base=root)
    catalogs=form.load_catalogs(draft)
    def reread(*args,**kwargs):raise AssertionError('Renderer must consume supplied configuration facts')
    monkeypatch.setattr(configuration_updates,'status',reread)
    monkeypatch.setattr(instance_ui.display,'settings',reread)
    monkeypatch.setattr(budget,'ceilings',reread)
    monkeypatch.setattr(model_catalog,'load',reread)
    assert ui._render_configuration_page(token,state=state,original_settings=originals,current_budget=ceilings,
        catalogs=catalogs,draft=draft)[0]==200
