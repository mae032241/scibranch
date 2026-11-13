"""Display snapshots do not re-hash or reorder recovered scientific sources."""
import json
import threading
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import instance_ui, ui_errors


def test_history_page_keeps_original_projection_when_missing_source_recovers(tmp_path, monkeypatch):
    page=instance_ui.InstanceUI.__new__(instance_ui.InstanceUI)
    page.root=tmp_path;page.view_memo={};page.history_pages={};page.cache_lock=threading.RLock()
    page._sealed_steps=lambda:[1,2]
    page._navigation=lambda:''
    page._page=lambda title,body,**kwargs:body
    missing={1}
    def node(step):
        if step in missing:raise ValueError('temporarily unavailable')
        return {}
    monkeypatch.setattr(instance_ui.ui_view,'Reader',lambda *args:SimpleNamespace(node=node,observation=lambda step:{}))
    page._candidates=lambda *args,job,**kwargs:dict(index=[dict(candidate_id=str(step)+'-'+str(i))
        for step in [row['step'] for row in job['history']['nodes']] for i in range(25)])
    monkeypatch.setattr(instance_ui.ui_view,'candidate_rows',lambda projection,**kwargs:projection)
    monkeypatch.setattr(instance_ui.ui,'candidate_table',lambda value:json.dumps(value,sort_keys=True))
    page._candidate_history()
    token=next(iter(page.history_pages))
    before=page._candidate_history(through=2,prefix=token,after=20)
    missing.clear()
    assert page._candidate_history(through=2,prefix=token,after=20)==before
    with pytest.raises(ui_errors.InputError):
        page._candidate_history(through=3,prefix=token,after=20)
    page.history_pages.clear()
    with pytest.raises(ui_errors.InputError):
        page._candidate_history(through=2,prefix=token,after=20)
