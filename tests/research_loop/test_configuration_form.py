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
            self.select = attrs.get('name')
            self.fields[self.select] = ''
        if tag == 'option':
            self.option = attrs.get('value', '')
            if 'selected' in attrs:
                self.fields[self.select] = self.option

    def handle_endtag(self, tag):
        if tag == 'textarea': self.textarea = None
        if tag == 'select': self.select = None

    def handle_data(self, data):
        if self.textarea:
            self.fields[self.textarea] += data


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
    flag = next(name[:-4] for name,value in fields.items() if name.endswith('.key') and value=='flag')
    fields[flag+'.value'] = 'false'
    parsed = form.parse(fields,base=tmp_path)
    assert parsed['document']['executor']['config']['configuration']['flag'] is False
    del fields['enabled.executor.config.configuration']
    assert 'executor' not in form.parse(fields,base=tmp_path)['document']
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
    key = next(name[:-4] for name,value in fields.items() if name.endswith('.key') and value == 'number')
    fields[key+'.value'] = 'bad nested number'
    with pytest.raises(form.FormError) as caught:
        form.parse(fields,base=tmp_path)
    retained = submitted(caught.value.draft)
    assert retained['cfg.budget.max_tokens'] == 'oops'
    assert retained['cfg.agent.model'] == 'new-model'
    assert retained['task_text'] == 'New exact task'
    assert retained[key+'.kind'] == 'integer'
    assert retained[key+'.value'] == 'bad nested number'
    with pytest.raises(ValueError,match='类型错误'):
        form.create(caught.value.draft)
    assert not (tmp_path/'instance').exists()


def test_policy_forms_edit_real_schemas_and_preserve_extra_fields(tmp_path):
    draft = complete(tmp_path)
    draft['document']['budget']['role_shares'] = dict(version=1, min_w_share=.65,
        max_thinking_share=.35, thinking_node_target_tokens=250000,
        imported_extension=dict(keep=[]))
    draft['document']['policies'] = dict(resume=dict(max_attempts=2,
        protocol='codex_exec_resume', usage_scope='turn', output_repair=True,
        output_only=False))
    fields = submitted(draft)
    assert fields['cfg.budget.role_shares.min_w_share'] == '0.65'
    assert fields['cfg.policies.resume.output_only'] == 'false'
    fields['cfg.budget.role_shares.min_w_share'] = '0.6'
    fields['cfg.policies.resume.max_attempts'] = '1'
    parsed = form.parse(fields, base=tmp_path)
    assert parsed['document']['budget']['role_shares'] == dict(version=1, min_w_share=.6,
        max_thinking_share=.35, thinking_node_target_tokens=250000,
        imported_extension=dict(keep=[]))
    assert parsed['document']['policies']['resume']['max_attempts'] == 1
    assert parsed['document']['policies']['resume']['output_only'] is False
    assert research_config.tomllib.loads(form.export_toml(parsed))['budget'] == parsed['document']['budget']
    fields['cfg.policies.resume.max_attempts'] = 'invalid attempts'
    with pytest.raises(form.FormError) as caught:
        form.parse(fields, base=tmp_path)
    assert submitted(caught.value.draft)['cfg.policies.resume.max_attempts'] == 'invalid attempts'
    del fields['enabled.policies.resume']
    assert 'policies' not in form.parse(fields, base=tmp_path)['document']


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
    assert 'list="config-agent-reasoning_effort-suggestions"' in html
    assert 'E · 证据检索与提取' in html
    assert 'W · 实验与实现' in html
    assert 'data-config-group="agent.roles"' in html
    assert '已设置，折叠后仍保留' in html
    del fields['present.agent.roles.E.model']
    del fields['enabled.research.user_updates']
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
    assert len(matrix.controls) == 20
    assert len({item['name'] for item in matrix.controls}) == 20
    for item in matrix.controls:
        path = item['name'].removeprefix('cfg.')
        field = next(field for field in form.FIELDS if field.path == path)
        assert matrix.labels[item['id']] == field.label
        assert item['aria-describedby'] == 'role-settings-help'
        assert html.count('name="'+item['name']+'"') == 1
    assert 'class="role-settings-card"' not in html
    assert 'id="budget-settings"' in html
    assert '<th scope="col">用量（tokens）</th>' in html
    assert '<th scope="col">时间（秒）</th>' in html
    fields = Controls(html).fields
    parsed = (form.parse_edit(fields, base=tmp_path, configuration=draft['document'])
        if edit else form.parse(fields, base=tmp_path))
    assert (parsed['document'] if edit else form.declaration(parsed)) == draft['document']


def test_compact_numeric_groups_keep_units_and_collapsible_help(tmp_path):
    html = form.render(complete(tmp_path), edit=True)
    for group in ('探索切片', '补正阈值', '输出与候选'):
        assert '<legend>'+group+'</legend>' in html
    for label in ('模型用量（tokens）', '活动时间（秒）', '节点上限（步）',
                  '单项输出（字节）', 'W 时间目标（秒）', '暂停阈值（次）'):
        assert label in html
    assert '<details class="config-help">' in html
    assert 'aria-describedby="config-budget-max_seconds-help"' in html
    assert '<p id="config-budget-max_seconds-help">单位：秒。' in html
    assert 'name="enabled.budget.role_shares"' in html
    assert 'name="enabled.policies.resume"' in html


@pytest.mark.parametrize('edit', [False, True])
def test_advanced_standard_controls_keep_closed_category_disclosures_and_roundtrip(tmp_path, edit):
    draft = complete(tmp_path)
    draft['document']['budget'].update(candidate_extra_slots=0,
        role_shares=dict(version=1, min_w_share=0, max_thinking_share=.35,
            thinking_node_target_tokens=250, extension=dict(enabled=False, empty=[])))
    draft['document']['policies'] = dict(formal_review=False, resume=dict(max_attempts=1,
        protocol='codex_exec_resume', usage_scope='turn', output_only=False, native_home=''))
    draft['document']['executor'] = dict(config=dict(configuration=dict(
        enabled=False, count=0, text='', nested=dict(items=[]))))
    html = form.render(draft, edit=edit)

    class DisclosureControls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.disclosures = []
            self.categories = []
            self.controls = {}
            self.feed(html)

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'details':
                disclosure = (attrs.get('class', ''), attrs.get('data-config-group'), 'open' in attrs)
                self.disclosures.append(disclosure)
                if disclosure[0] == 'advanced-category' and disclosure[1]:
                    self.categories.append(disclosure)
            if tag in {'input', 'select', 'textarea'} and attrs.get('name', '').startswith('cfg.'):
                self.controls[attrs['name']] = tuple(self.disclosures)

        def handle_endtag(self, tag):
            if tag == 'details':
                self.disclosures.pop()

    parsed_controls = DisclosureControls()
    controls = parsed_controls.controls
    categories = ['agent', 'budget', 'executor', 'delivery', 'policies'] if edit else [
        'research', 'project', 'skills', 'agent', 'budget', 'executor', 'delivery', 'policies', 'ui', 'version']
    assert parsed_controls.categories == [('advanced-category', prefix, False) for prefix in categories]
    for path in ('agent.backend', 'agent.native.features.fast_mode', 'budget.breadth.initial_calls',
                 'budget.correction.pause_after', 'budget.max_output_item_bytes',
                 'budget.role_shares.min_w_share', 'executor.mode', 'executor.config.executor',
                 'policies.formal_review', 'policies.resume.output_only', 'policies.resume.native_home'):
        assert controls['cfg.'+path] == (('advanced-settings', None, False),
            ('advanced-category', path.split('.')[0], False))
        assert html.count('name="cfg.'+path+'"') == 1
    assert '<details class="advanced-category" data-config-group="budget"><summary>' in html
    assert '<fieldset class="structured-setting known-settings" data-config-path="budget.role_shares"' in html
    assert '<fieldset class="structured-setting known-settings" data-config-path="policies.resume"' in html
    assert '<label class="config-node-key">键名 ' in html
    assert '<label class="config-node-type">类型 ' in html
    fields = Controls(html).fields
    fields['cfg.budget.candidate_extra_slots'] = '1'
    fields['cfg.policies.resume.output_only'] = 'true'
    parsed = (form.parse_edit(fields, base=tmp_path, configuration=draft['document'])
        if edit else form.parse(fields, base=tmp_path))
    expected = form.declaration(draft)
    expected['budget']['candidate_extra_slots'] = 1
    expected['policies']['resume']['output_only'] = True
    assert (parsed['document'] if edit else form.declaration(parsed)) == expected


def test_list_entry_works_without_script_and_blank_entry_adds_no_value(tmp_path):
    draft = complete(tmp_path)
    fields = submitted(draft)
    omitted = form.parse(dict(fields, **{'enabled.agent.launcher': ''}), base=tmp_path)
    assert 'launcher' not in omitted['document'].get('agent', {})
    fields['cfg.research.user_updates'] = 'one update.md'
    parsed = form.parse(fields, base=tmp_path)
    assert parsed['document']['research']['user_updates'] == ['one update.md']
    fields['cfg.research.user_updates'] = ''
    assert 'user_updates' not in form.parse(fields, base=tmp_path)['document']['research']


def test_edit_form_roundtrips_operational_groups_and_keeps_instance_inputs_server_side(tmp_path):
    draft = complete(tmp_path)
    draft['document']['ui'] = dict(approval_secret_file='/private/operator.key')
    draft['document']['permissions'] = dict(workspace='workspace-write', network=False, web='live', subagents=False)
    draft['document']['executor'] = dict(mode='external', config=dict(configuration=dict(nested=[1, False, 'exact'])))
    draft['document']['delivery'] = dict(adapter=dict(config=dict(destination='exact-target')))
    fields = Controls(form.render(draft, edit=True)).fields
    assert not any(name.startswith(('cfg.research.', 'cfg.ui.', 'cfg.version', 'task_')) for name in fields)
    assert '/private/operator.key' not in form.render(draft, edit=True)
    assert fields['cfg.permissions.web'] == 'live'
    assert fields['cfg.executor.mode'] == 'external'
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
    fields['cfg.research.root'] = '/another-instance'
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
    assert retry['cfg.budget.breadth.max_w_seconds'] == str(seconds)
    assert retry['cfg.agent.model'] == 'model-after-correction'
    retry['cfg.budget.max_tokens'] = str(state['configuration']['budget']['max_tokens'])
    corrected = form.parse_edit(retry, base=root, configuration=state['configuration'])
    saved = configuration_updates.submit(root, corrected['document'], expected_revision=state['revision'])
    assert saved['pending']
    assert saved['settings']['model'] == 'model-after-correction'
    assert saved['settings']['search_policy']['max_w_seconds'] == seconds
    assert type(saved['settings']['search_policy']['max_w_observed_tokens']) is int
    retry['cfg.budget.breadth.max_w_observed_tokens'] = '1.5'
    with pytest.raises(form.FormError, match='W 软目标 tokens'):
        form.parse_edit(retry, base=root, configuration=state['configuration'])
