"""Public comparison is independent of normalization and filesystem access."""
from scidiscovery.research_loop.configuration_changes import rows
from scidiscovery.research_loop.configuration_form import Field


def test_exact_declarations_distinguish_false_zero_null_and_omission():
    fields = [Field('budget.max_tokens', 'tokens', 'integer'),
        Field('permissions.network', 'network', 'boolean'), Field('agent.model', 'model'),
        Field('agent.roles.W.model', 'W model')]
    active = dict(budget=dict(max_tokens=False), permissions=dict(network=0), agent=dict(model=None, roles=dict(W=dict(model='old'))))
    draft = dict(budget=dict(max_tokens=0), permissions=dict(network=False), agent={})
    result = {item['path']: item for item in rows(active, draft, fields=fields)}
    assert len(result) == 4
    assert result['budget.max_tokens']['draft'] == '0'
    assert result['permissions.network']['draft'].startswith('false')
    assert result['agent.model']['active'] == '空值（null）'
    assert result['agent.model']['draft'] == '未声明（默认值待校验）'
    assert result['agent.roles.W.model']['draft'] == '继承全局或命名研究者（待校验）'
    assert result['agent.roles.W.model']['scope'] == 'W 角色的后续新节点'


def test_no_changes_and_known_recovery_context_and_native_changes():
    fields = [Field('policies.resume.automatic_recovery.max_retries', 'retries', 'integer'),
        Field('policies.worker_context_windows', 'windows', 'integer'),
        Field('policies.supervision.mode', 'auto A'), Field('agent.native.fast_mode', 'fast', 'boolean')]
    old = dict(policies=dict(resume=dict(automatic_recovery=dict(max_retries=1)), worker_context_windows=1,
        supervision=dict(mode='off')), agent=dict(native=dict(fast_mode=False)))
    assert rows(old, old, fields=fields) == []
    new = dict(policies=dict(resume=dict(automatic_recovery=dict(max_retries=2)), worker_context_windows=3,
        supervision=dict(mode='automatic')), agent=dict(native=dict(fast_mode=True)))
    result = rows(old, new, fields=fields)
    assert [item['draft'] for item in result] == ['2', '3', 'automatic', 'true（允许 / 开启）']
    assert result[0]['scope'] == '后续新节点；原调用恢复不变'


def test_unknown_extensions_private_values_and_invalid_objects_are_not_expanded():
    class Private:
        def __str__(self):
            raise AssertionError('Private values must never be formatted')
        __repr__ = __str__
    fields = [Field('agent.model', 'model'), Field('agent.native.api_key', 'key'),
        Field('agent.native.custom', 'custom', 'table'), Field('policies.resume.native_home', 'home'),
        Field('research.objective_text', 'task'), Field('executor.config.configuration', 'plugin', 'table')]
    new = dict(agent=dict(model=Private(), native=dict(api_key='secret-value', custom=dict(secret='nested-secret'))),
        policies=dict(resume=dict(native_home='/private/home')), research=dict(objective_text='scientific task'),
        executor=dict(config=dict(configuration=dict(token='plugin-secret'))))
    result = rows({}, new, fields=fields)
    assert [item['path'] for item in result] == ['agent.model', 'agent.native.api_key']
    assert result[0]['draft'] == '待修正（尚未验证）'
    assert result[1]['draft'] == '已声明（内容不在摘要展示）'


def test_changed_launcher_values_are_explicit_without_private_paths_or_arguments():
    fields = [Field('agent.launcher', 'launcher', 'strings')]
    result = rows(dict(agent=dict(launcher=['/private/old/codex', '--token', 'old-secret'])),
        dict(agent=dict(launcher=['/private/new/codex', '--token', 'new-secret'])), fields=fields)
    assert len(result) == 1
    assert result[0]['active'] == 'codex · 3 项（路径与参数值省略）'
    assert '路径或参数已变化，见启动命令' in result[0]['draft']
    assert 'private' not in str(result) and 'secret' not in str(result)


def test_native_free_text_is_opaque_even_with_innocuous_known_field_names():
    fields = [Field('agent.native.endpoint', 'endpoint'), Field('agent.native.note', 'note'),
        Field('agent.native.resource_mode', 'mode', choices=('closed', 'configured'))]
    old = dict(agent=dict(native=dict(endpoint='/home/private/old-auth-token', note='Old research prompt', resource_mode='closed')))
    new = dict(agent=dict(native=dict(endpoint='/home/private/auth-token?key=secret', note='Ordinary scientific prompt about samples', resource_mode='configured')))
    result = rows(old, new, fields=fields)
    assert [item['path'] for item in result] == [field.path for field in fields]
    for item in result[:2]:
        assert item['active'] == '已声明（内容不在摘要展示）'
        assert item['draft'] == '已声明（内容不在摘要展示）（声明内容已变化，见对应设置）'
    assert result[2]['active'] == 'closed' and result[2]['draft'] == 'configured'
    assert all(private not in str(result) for private in ('/home/private', 'auth-token', 'secret', 'research prompt', 'scientific prompt'))


def test_missing_current_budget_is_unknown_not_the_frozen_node_limit():
    fields = [Field('budget.max_tokens', 'tokens', 'integer')]
    active = dict(budget=dict(max_tokens=4000))
    result = rows(active, active, fields=fields, budget_available=False)
    assert result[0]['active'] == '未知（当前值不可用）'
    assert result[0]['draft'] == '4000'


def test_number_fields_compare_finite_numeric_values_without_coercing_other_types():
    fields = [Field('budget.max_seconds', 'seconds', 'number'),
        Field('budget.max_steps', 'steps', 'integer'), Field('budget.breadth.max_w_seconds', 'W seconds', 'number')]
    old = dict(budget=dict(max_seconds=60, max_steps=1, breadth=dict(max_w_seconds=False)))
    new = dict(budget=dict(max_seconds=60.0, max_steps=1.0, breadth=dict(max_w_seconds=0)))
    result = rows(old, new, fields=fields)
    assert [item['path'] for item in result] == ['budget.max_steps', 'budget.breadth.max_w_seconds']
    assert result[0]['active'] == '1' and result[0]['draft'] == '1.0'
    assert result[1]['active'].startswith('false') and result[1]['draft'] == '0'
