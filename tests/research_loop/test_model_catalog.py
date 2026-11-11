"""Optional native metadata stays behind the Backend boundary and exact context."""
import json

from scidiscovery.research_loop import backends, configuration_form, model_catalog, pi_backend


def test_render_queries_only_selected_backend_with_declared_context(tmp_path, monkeypatch):
    calls = []
    def load(name, **context):
        calls.append((name, context))
        return model_catalog.unavailable('custom text')
    monkeypatch.setattr(model_catalog, 'load', load)
    draft = configuration_form.blank(base=tmp_path)
    draft['document']['agent'] = dict(backend='codex', launcher=['./chosen-codex'],
        native=dict(features=dict(fast_mode=False)))
    html = configuration_form.render(draft, edit=True)
    assert calls == [('codex', dict(launcher=['./chosen-codex'],
        native=dict(features=dict(fast_mode=False)), base=str(tmp_path)))]
    assert 'data-catalog-note="pi"' not in html


def test_optional_backend_catalog_uses_generic_boundary_and_context_cache(tmp_path, monkeypatch):
    calls = []
    class Custom(backends.Protocol):
        @classmethod
        def model_catalog(cls, context):
            calls.append(context)
            return dict(models=[dict(id='custom/exact:id[1]', label='exact', efforts=[], tiers=[])],
                source='custom native metadata', fetched_at=None, error=None)
    monkeypatch.setitem(backends._PROVIDERS, 'custom-catalog', Custom)
    monkeypatch.setattr(model_catalog, '_CACHE', {})
    context = dict(launcher=['./chosen', '--metadata-context'], native=dict(profile='one'), base=tmp_path)
    first = model_catalog.load('custom-catalog', **context)
    # Callers cannot mutate subsequent cached metadata.
    first['models'][0]['id'] = 'changed'
    second = model_catalog.load('custom-catalog', **context)
    assert second['models'][0]['id'] == 'custom/exact:id[1]' and len(calls) == 1
    model_catalog.load('custom-catalog', **dict(context, launcher=['./other']))
    model_catalog.load('custom-catalog', **dict(context, native=dict(profile='two')))
    model_catalog.load('custom-catalog', **dict(context, base=tmp_path / 'other'))
    assert len(calls) == 4
    assert calls[0] == dict(launcher=['./chosen', '--metadata-context'], native=dict(profile='one'), base=str(tmp_path))
    assert model_catalog.load('protocol')['models'] == []


def test_pi_catalog_native_context_and_declared_package_profile(tmp_path, monkeypatch):
    calls = []
    def listing(argv, **context):
        calls.append((argv, context))
        return 'provider model thinking\nacme exact:id[1] yes\n'
    monkeypatch.setattr(pi_backend, '_catalog_output', listing)
    monkeypatch.setattr(model_catalog, '_CACHE', {})
    agent = tmp_path / 'agent-one'
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent))
    context = dict(launcher=['./chosen-pi'], native=dict(resource_mode='closed'), base=tmp_path)
    assert model_catalog.load('pi', **context)['models'][0]['id'] == 'acme/exact:id[1]'
    assert model_catalog.load('pi', **context)['models'] and len(calls) == 1
    # A different inherited native directory cannot reuse the first catalog.
    agent_two = tmp_path / 'agent-two'
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent_two))
    assert model_catalog.load('pi', **context)['models'] and len(calls) == 2
    assert calls[-1][1]['env']['PI_CODING_AGENT_DIR'] == str(agent_two)
    # A configured profile admits its cached packages through the existing
    # native policy. The metadata command remains offline with no extensions.
    agent_two.mkdir()
    (agent_two / 'settings.json').write_text(json.dumps(dict(packages=['npm:cached-native-package'])))
    configured = model_catalog.load('pi', **dict(context, native=dict(resource_mode='configured')))
    assert configured['models'][0]['id'] == 'acme/exact:id[1]' and len(calls) == 3
    assert '--offline' in calls[-1][0] and '--no-extensions' in calls[-1][0]
    assert calls[-1][1]['env']['PI_OFFLINE'] == '1'
    # A different declared launcher triggers fresh observation; closed still
    # refuses those same package settings before any metadata process.
    unavailable = model_catalog.load('pi', **dict(context, launcher=['./other-pi']))
    assert unavailable['models'] == [] and unavailable['error'] and len(calls) == 3


def test_codex_catalog_does_not_borrow_unknown_launcher_context(tmp_path, monkeypatch):
    home = tmp_path / 'codex-home'
    home.mkdir()
    (home / 'models_cache.json').write_text(json.dumps(dict(models=[dict(slug='unrelated-model')])))
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setattr(model_catalog, '_CACHE', {})
    assert model_catalog.load('codex', launcher=['wrapper', '--different-home'])['models'] == []
    assert model_catalog.load('codex', launcher=['/chosen/codex'])['models'][0]['id'] == 'unrelated-model'
