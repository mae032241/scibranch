"""Public progress refreshes exact original owner liveness on every read."""
import errno

import pytest

from scidiscovery.research_loop import budget, contracts as c, progress, runtime
from test_immediate_interrupt import accepted_scene


def owner(pid,binding):
    return dict(pid=pid,boot_id=budget.clock_stamp()['boot_id'],start_ticks=10,
        pgid=pid,sid=pid,ppid=1,state='S',task_ref=binding)


@pytest.mark.parametrize('stopped',['absent','reused','Z','X','x'])
def test_progress_saved_running_refreshes_without_a_control_file_change(tmp_path,monkeypatch,stopped):
    root,record,target=accepted_scene(tmp_path,monkeypatch)
    binding=dict(path=str(root/'jobs/001.json'))
    original=owner(81201,binding)
    c.write(record/'runner-process.json',original)
    receipt=(record/'runner-process.json').read_bytes()
    state=['S']
    def actual(pid,expected):
        if state[0]=='absent':raise ProcessLookupError(errno.ESRCH,'fixture original exited')
        if state[0]=='reused':return dict(original,start_ticks=11,task_ref=expected)
        return dict(original,state=state[0],task_ref=expected)
    monkeypatch.setattr(runtime,'process_identity',actual)
    assert progress.read(root,1)['state']=='running'
    state[0]=stopped
    assert progress.read(root,1)['state']=='unknown'
    assert (record/'runner-process.json').read_bytes()==receipt


@pytest.mark.parametrize('attempt',[0,1])
def test_progress_collects_live_native_when_runner_receipt_is_missing(tmp_path,monkeypatch,attempt):
    root,record,target=accepted_scene(tmp_path,monkeypatch)
    if attempt:
        # The reader's explicit attempt namespace determines the expected
        # owner binding; a receipt never supplies its own authority.
        intent=c.read(record/'native-intent.json')
        record=record/'continuations/001';record.mkdir(parents=True)
        c.write(record/'native-intent.json',dict(intent,attempt=attempt))
    binding=dict(path=str(record/'native-intent.json')) if attempt else dict(path=str(root/'jobs/001.json'))
    original=owner(81202,binding)
    c.write(record/'native-process.json',original)
    monkeypatch.setattr(runtime,'process_identity',lambda pid,expected:dict(original,task_ref=expected))
    assert progress.read(root,1,attempt=attempt)['state']=='running'
    c.write(record/'native-process.json',dict(original,task_ref=dict(path='/different/original')))
    assert progress.read(root,1,attempt=attempt)['state']=='unknown'


def test_progress_bad_runner_does_not_hide_live_native_and_preserves_terminal_overrides(tmp_path,monkeypatch):
    root,record,target=accepted_scene(tmp_path,monkeypatch)
    (record/'runner-process.json').write_bytes(b'{bad receipt')
    binding=dict(path=str(root/'jobs/001.json'));original=owner(81203,binding)
    c.write(record/'native-process.json',original)
    monkeypatch.setattr(runtime,'process_identity',lambda pid,expected:dict(original,task_ref=expected))
    assert progress.read(root,1)['state']=='running'
    c.write(record/'native-outcome.json',dict(returncode=0))
    assert progress.read(root,1)['state']=='native_outcome_present'
    c.write(record/'result-identity.json',dict(status='fixture_terminal_identity'))
    assert progress.read(root,1)['state']=='seal_present_unverified'
