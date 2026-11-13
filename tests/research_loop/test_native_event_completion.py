"""Original clean endpoints resolve notices, preserving later failures."""
import pytest

from scidiscovery.research_loop import backends, native_recovery


def _failure(event):
    return backends.selected(dict(native_launcher_argv=['codex'])).event(event)['failure']


@pytest.mark.parametrize('event',[
    dict(type='error',message='An arbitrary intermediate stream notice.'),
    dict(type='error',error=dict(code='stream_disconnected')),
    dict(type='error',error=dict(code='invalid_api_key')),
    dict(type='error',error=dict(code='connection_error',accepted=False)),
    dict(type='error',message='Reconnecting... 2/5 (workspace routing discovery timed out)'),
])
def test_clean_original_completion_resolves_top_level_stream_errors(event):
    failure=_failure(event)
    assert failure['event_type']=='error'
    assert native_recovery.combine([failure])==failure
    assert native_recovery.combine([failure],completed_before=1) is None


@pytest.mark.parametrize('event',[
    dict(type='turn.failed',error=dict(code='stream_disconnected')),
    dict(type='turn.incomplete'),
    dict(type='turn.interrupted'),
])
def test_failed_turns_survive_later_completion(event):
    failure=_failure(event)
    assert native_recovery.combine([failure],completed_before=1)==failure


def test_completion_never_resolves_a_later_error_or_changes_its_classification():
    before=_failure(dict(type='error',error=dict(code='connection_error')))
    after=_failure(dict(type='error',error=dict(code='insufficient_quota')))
    assert native_recovery.combine([before,after],completed_before=1)==after
    assert native_recovery.combine([after],completed_before=0)==after
