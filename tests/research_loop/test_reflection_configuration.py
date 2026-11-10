"""Reflection defaults and opt-outs use the common preparation/configuration path."""
from scidiscovery.research_loop import accepted_budget, contracts as c, controller, research_config, supervision


def prepare(tmp_path, **changes):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain evidence and continue bounded research.')
    root=tmp_path/'instance'
    options=dict(root=root,objective=objective,public_root=public,launcher=['never-launched'],
        native_backend='protocol',model='explicit-provider-model',reasoning_effort='high',experiment_mode='external',
        max_tokens=10000,max_seconds=120,max_steps=8)
    options.update(changes)
    controller.prepare(**options)
    return root,options


def test_new_common_prepare_freezes_reflection_without_a_codex_default(tmp_path):
    root,_=prepare(tmp_path)
    selected=c.settings(root)
    assert selected['native_backend']=='protocol'
    assert selected['supervision_policy']==supervision.DEFAULT_WORKER_FIRST_REFLECTION_POLICY
    job=c.build_job(root,1,'W',[])
    assert job['worker_continuity_version']==1
    head=accepted_budget.read(root)
    assert c.settings_for_job(root,job,base=selected,head=head)['native_backend']=='protocol'


def test_toml_configuration_explicitly_disables_new_automatic_reviews(tmp_path):
    (tmp_path/'public').mkdir()
    off=dict(supervision.DEFAULT_WORKER_FIRST_REFLECTION_POLICY,mode='off',max_automatic_reviews=0)
    document=dict(version=1,research=dict(root='instance',public_root='public',
        objective_text='Keep scientific work autonomous with this explicit opt-out.'),
        agent=dict(backend='protocol',launcher=['never-launched'],model='explicit-provider-model',reasoning_effort='high'),
        executor=dict(mode='external'),budget=dict(max_tokens=10000,max_seconds=120,max_steps=8),
        policies=dict(supervision=off))
    options=research_config.from_document(document,base=tmp_path)
    selected=research_config.prepare(**options,validate_only=True)
    assert selected['native_backend']=='protocol' and selected['supervision_policy']==off
