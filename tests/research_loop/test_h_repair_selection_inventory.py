"""Public nonrepair interpretation does not acquire material selection."""
from scidiscovery.research_loop import contracts as c, search
from test_search import focus, plan


def test_public_nonrepair_interpret_does_not_accept_material_selection(tmp_path, monkeypatch):
    state = dict(mechanisms={}, current_h=None, scope=None, plan=None, authorized=set(), focus=None,
        order=dict(remaining=[], seen_w=set(), allocation=None, diagnostics=[], discriminating_work=[]))
    monkeypatch.setattr(c, 'settings', lambda *args: dict(search_policy=dict(search.DEFAULT_POLICY)))

    def forbidden(*args, **kwargs):
        raise AssertionError('A nonrepair interpretation acquired material selection')

    monkeypatch.setattr(c, '_accept_selected_inputs', forbidden)
    result = search.interpret(tmp_path, dict(role='H', step=1),
        dict(next_action='experiment', route_reason='Exact scientific question', search_plan=plan()), state=state)
    assert result['diagnostics'] == []
    assert result['focus'] == focus(action='screen')
