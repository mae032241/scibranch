"""Ordinary W completion wakes retain one bounded exploration, without models."""
import pytest

from scidiscovery.research_loop import continuation, contracts as c, managed_wait


@pytest.mark.parametrize('changed',[dict(role='A'),dict(formal_review={'requested':True}),
    dict(final_only=True),dict(final_report={'source_cutoff':0}),dict(purpose='closeout')])
def test_completion_wake_cannot_grant_ordinary_work_to_special_task(tmp_path,monkeypatch,changed):
    c.write(tmp_path/'jobs/001.json',dict(dict(step=1,role='W'),**changed))
    monkeypatch.setattr(continuation,'validate_wake_policy',lambda *_:None)
    monkeypatch.setattr(managed_wait,'validate',lambda *_:pytest.fail('Special task cannot acquire a waiting grant'))
    with pytest.raises(ValueError,match='original ordinary W task'):
        continuation.validate_wake(tmp_path,1,{},tmp_path/'prior')


@pytest.mark.parametrize('source',[dict(continuation_kind='repair',output_repair={}),dict(continuation_kind='author_closeout')])
def test_completion_wake_cannot_expand_json_repair_or_author_closeout(tmp_path,monkeypatch,source):
    c.write(tmp_path/'jobs/001.json',dict(step=1,role='W'))
    c.write(tmp_path/'prior/native-intent.json',source)
    monkeypatch.setattr(continuation,'validate_wake_policy',lambda *_:None)
    monkeypatch.setattr(managed_wait,'validate',lambda *_:pytest.fail('Narrow source cannot acquire a waiting grant'))
    with pytest.raises(ValueError,match='special continuation scope'):
        continuation.validate_wake(tmp_path,1,{},tmp_path/'prior')
