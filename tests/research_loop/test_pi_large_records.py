"""Mechanical native-size regressions; no model calls or scientific conclusions."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import pi_backend as pi, pi_sessions, pi_transport
from test_pi_backend_v2 import (FAKE_RPC, FINAL, PROFILE, STAMP, USAGE,
    bind_engine, collect_separately, message, prepare_instance, require_namespace)


def wire(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')


def test_large_individual_record_selector_split_utf8_and_exact_final(tmp_path):
    text = ' \n' + '原始🙂' * 120000 + '\n\t'
    value = dict(type='message_end', message=message(text))
    raw = wire(value)
    assert len(raw) > 1024 * 1024
    assert pi_transport.record(raw) == value
    split = raw.index('🙂'.encode()) + 1
    selector = pi.PiEventSelector()
    assert selector.feed(raw[:split]) == []
    assert selector.feed(raw[split:-1]) == []
    assert selector.feed(raw[-1:]) == [value]
    assert selector.feed(b'', final=True) == []
    backend = bind_engine(tmp_path)
    assert backend.event(value) == dict(activity=True)
    assert backend.event(dict(type='turn_end', message=value['message'])) == dict(activity=True)
    assert backend.event(dict(type='agent_end', messages=[value['message']])) == dict(activity=True)
    assert backend.event(dict(type='agent_settled', aborted=False)) == dict(completed=True, result_text=text)


@pytest.mark.parametrize('raw', [b'{broken}\n', b'{"type":"agent_end","type":"agent_settled"}\n',
    b'{"type":"agent_end","number":NaN}\n', b'{"type":"agent_end"}'])
def test_large_record_path_keeps_strict_json_and_lf(raw):
    selector = pi.PiEventSelector()
    assert selector.feed(raw, final=True) == [dict(type='pi_transport_fault')]
    with pytest.raises(ValueError): pi_transport.record(raw)


def test_large_aggregate_does_not_allow_work_after_settlement():
    binding = pi_transport.Binding('fixture', 'exact-model', 'high', '/private/session', 'Original prompt')
    binding.prompt_sent = True
    aggregate = dict(type='agent_end', messages=[message('x'*(384*1024), stamp=i+2) for i in range(3)])
    assert len(wire(aggregate)) > 1024 * 1024
    binding.event(aggregate)
    binding.event(dict(type='agent_settled', aborted=False))
    with pytest.raises(pi_transport.TransportFault, match='work_after_settlement'):
        binding.event(aggregate)


def test_transport_fault_has_only_allowlisted_internal_reason(tmp_path):
    safe = pi_transport.fault_event(pi_transport.TransportFault('work_after_settlement'))
    assert safe == dict(type='pi_transport_fault', code='work_after_settlement')
    for error in (ValueError('work_after_settlement'),
            pi_transport.TransportFault('private-provider-key/path'),
            pi_transport.TransportFault('work_after_settlement', 'private-provider-key/path')):
        assert pi_transport.fault_event(error) == dict(type='pi_transport_fault')
    backend = bind_engine(tmp_path)
    result = backend.event(safe)
    assert result['failure']['category'] == 'unknown'
    assert result['failure']['not_accepted'] is False
    assert backend.engine.usage.fault == 'pi_transport_fault:work_after_settlement'
    # Provider-native text cannot add an arbitrary reason to outcome diagnostics.
    backend = bind_engine(tmp_path)
    backend.event(dict(type='pi_transport_fault', code='private-provider-key/path'))
    assert backend.engine.usage.fault == 'pi_transport_fault'


def test_large_session_record_meter_resume_and_exact_original(tmp_path):
    home = tmp_path/'home'; work = tmp_path/'work'
    path = home/'sessions/pi-session.jsonl'; path.parent.mkdir(parents=True); work.mkdir()
    text = ' \n' + '原始🙂' * 120000 + '\n\t'
    rows = [dict(type='session', version=3, id='exact-session', timestamp=STAMP, cwd=str(work)),
        dict(type='message', id='user', parentId=None, timestamp=STAMP,
            message=dict(role='user', timestamp=0, content='Original prompt')),
        dict(type='message', id='final', parentId='user', timestamp=STAMP, message=message(text))]
    original = b''.join(map(wire, rows)); path.write_bytes(original)
    assert len(wire(rows[-1])) > 1024 * 1024
    meter = pi_sessions.SessionUsage(home, work); meter.profile = dict(PROFILE)
    meter.bind_thread('exact-session')
    # One poll is a work chunk, never a quota on the eventual original.
    for _ in range(8):
        snapshot = meter.poll()
        if snapshot['offset'] == len(original): break
    assert snapshot['reason'] is None and snapshot['tokens'] == pi_sessions.usage_tokens(USAGE)
    terminal = meter.terminal()
    assert terminal['coverage'] == 'terminal'
    binding = terminal['session_binding']; assert binding is not None
    acquired = pi_sessions.load_resume_original(binding, work, home)
    pi_sessions.recheck_prefix(acquired)
    resumed = pi_sessions.SessionUsage(home, work, binding, resume_original=acquired)
    assert resumed.snapshot()['tokens'] == dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    assert path.read_bytes() == original
    assert pi_sessions.decode(wire(rows[-1]))['message']['content'][-1]['text'] == text
    appended = wire(dict(type='message', id='next', parentId='final', timestamp=STAMP,
        message=message('Next native final', stamp=2)))
    with path.open('ab') as stream: stream.write(appended)
    assert resumed.terminal()['coverage'] == 'terminal'
    assert resumed.snapshot()['tokens'] == pi_sessions.usage_tokens(USAGE)
    assert path.read_bytes() == original + appended
    # The SDK owns mutable context; the frozen session identity remains exact.
    path.write_bytes(original.replace(b'exact-session', b'other-session', 1))
    with pytest.raises(ValueError, match='changed'): pi_sessions.recheck_prefix(acquired)


def test_large_total_size_is_valid_binding_and_streamed_proof(tmp_path, monkeypatch):
    """A virtual ordinary original tests >64 MiB without retaining a huge fixture."""
    size = 65 * 1024 * 1024 + 37
    home = tmp_path/'home'; work = tmp_path/'work'; path = home/'sessions/pi-session.jsonl'
    header = wire(dict(type='session', version=3, id='exact-session', timestamp=STAMP, cwd=str(work)))
    session = dict(backend='pi', home=str(home), workspace=str(work), path=str(path),
        thread_id='exact-session', profile=dict(PROFILE), header=header.decode(), identity=[1, 2],
        size_bytes=size, sha256='a'*64)
    assert pi_sessions.validate_resume_binding(session, work, home) == path
    info = SimpleNamespace(st_dev=1, st_ino=2, st_size=size, st_mtime_ns=3,
        st_ctime_ns=4, st_mode=0o100600, st_nlink=1)
    acquired = []
    class Digest:
        def __init__(self): self.count = 0
        def update(self, chunk): self.count += len(chunk)
        def digest(self): return b'fixture-digest'
    class Stream:
        def __init__(self): self.remaining = size; self.read_sizes = []
        def fileno(self): return 42
        def readline(self, count): return header
        def read(self, count):
            self.read_sizes.append(count)
            data = b'x' * min(count, self.remaining); self.remaining -= len(data)
            return data
    @contextmanager
    def open_original(*args):
        stream = Stream(); acquired.append(stream); yield stream
    monkeypatch.setattr(pi_sessions, 'open_original', open_original)
    monkeypatch.setattr(pi_sessions.os, 'fstat', lambda fd: info)
    monkeypatch.setattr(Path, 'lstat', lambda path: info)
    monkeypatch.setattr(pi_sessions.hashlib, 'sha256', Digest)
    meter = pi_sessions.SessionUsage(home, work)
    meter.offset = size; meter.identity = (1, 2); meter.digest = Digest()
    meter._verify_stopped_original()
    assert meter.verified_offset == size and meter.fault is None
    pi_sessions.recheck_prefix(dict(session=session, info=info, digest=Digest()))
    assert len(acquired) == 2
    assert acquired[0].remaining == 0
    assert acquired[1].remaining == size  # Resume checks identity, without rehashing SDK context.
    assert all(0 < count <= 1024 * 1024 for stream in acquired for count in stream.read_sizes)


@pytest.mark.process_e2e
def test_offline_rpc_large_agent_end_settles_without_reprompt(tmp_path, monkeypatch):
    require_namespace()
    monkeypatch.delenv('PI_FIXTURE_FAULT', raising=False)
    agent = tmp_path/'fixture-agent'; agent.mkdir()
    monkeypatch.setenv('PI_CODING_AGENT_DIR', str(agent))
    monkeypatch.setenv('PI_FIXTURE_FINAL', FINAL)
    monkeypatch.setenv('PI_FIXTURE_ORIGINAL_HOME', os.environ['HOME'])
    marker = "emit(dict(type='message_end',message=msg));emit(dict(type='agent_end'))"
    assert FAKE_RPC.count(marker) == 1
    aggregate = [message('x'*(384*1024), stamp=i+2) for i in range(3)]
    aggregate.append(message())
    assert len(wire(dict(type='agent_end', messages=aggregate))) > 1024 * 1024
    # Alter only this peer's SDK aggregate event; established fixture bytes and
    # its existing consumers remain unchanged.
    large_peer = FAKE_RPC.replace(marker,
        "emit(dict(type='message_end',message=msg));emit(dict(type='agent_end',messages="
        "[dict(msg,timestamp=i+2,content=[dict(type='text',text='x'*(384*1024))]) for i in range(3)]+[msg]))")
    launcher = tmp_path/'fake-pi'; launcher.write_text(large_peer); launcher.chmod(0o700)
    root, _ = prepare_instance(tmp_path, launcher)
    result = collect_separately(root)
    from scidiscovery.research_loop import contracts as c, execution
    record = execution.origin(root, 1); outcome = c.read(record/'native-outcome.json')
    assert result.returncode == 0
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['terminal_observed'] and outcome['cleanup'] == 'observed_group_cleaned'
    assert outcome['metering']['coverage'] == 'terminal'
    assert (record/'science-output.json').read_bytes() == FINAL.encode('utf-8')
    assert c.read(agent/'commands.json').count('prompt') == 1
    stream = [pi_sessions.decode(row) for row in (record/'native.jsonl').read_bytes().splitlines()]
    assert not any(row['type'] == 'pi_transport_fault' for row in stream)
    assert [row for row in stream if row['type'] == 'agent_end'] == [dict(type='agent_end')]
    assert stream[-1] == dict(type='agent_settled', aborted=False)
    # The child itself writes this retained byte-for-byte native original at EOF.
    assert (root/'native-homes/001/sessions/pi-session.jsonl').read_bytes() == (agent/'session-original.bin').read_bytes()
