"""Missing optional Pi facts preserve native work and honest lower bounds.

Only the marked Node VM cases start a process; they run the real generated
observer with mocked native SDK/helper calls, never Pi or a model provider.
"""
import json
import subprocess

import pytest

from scidiscovery.research_loop import native_usage, pi_observation, pi_sessions
from test_pi_backend_v2 import FINAL, USAGE, bind_engine, message
from test_pi_observation import PROMPT, append, bound, entry, event, pending


@pytest.mark.parametrize('usage', [None, {'input': 'unavailable'}], ids=['absent', 'unusable'])
def test_missing_assistant_and_compaction_billing_keeps_context_and_known_lower_bound(tmp_path, usage):
    backend, meter, observer = bound(tmp_path)
    pending(observer, event(meter)); observer.poll()
    known = message(stamp=1)
    unknown = message(stamp=2)
    unknown.pop('usage')
    fields = {} if usage is None else dict(usage=usage)
    unknown.update(fields)
    append(meter.path, entry('known-assistant', 'native-user', message=known),
        entry('unknown-assistant', 'known-assistant', message=unknown))
    pending(observer, event(meter, kind='turn_end', leaf='unknown-assistant',
        assistant_id='unknown-assistant', status='completed'))
    view = meter.poll()
    lower_bound = pi_sessions.usage_tokens(USAGE)
    assert view['tokens'] == lower_bound and view['has_observation'] is True
    assert view['coverage'] == 'unknown' and view['reason'] == 'pi_session_usage_unreported'
    assert view['context']['current_window'] is True
    assert not meter.fault and not observer.fault and observer.acknowledged == 2
    assert meter.persisted_messages[pi_sessions.message_identity(unknown)] is None
    pending(observer, event(meter, kind='compact_start', leaf='unknown-assistant', context=None))
    observer.poll()
    append(meter.path, entry('unknown-compact', 'unknown-assistant', kind='compaction',
        summary='Native summary', tokensBefore=85, firstKeptEntryId='native-user', **fields))
    pending(observer, event(meter, kind='compact', leaf='unknown-compact', context=None,
        compaction_id='unknown-compact', pre_cursor='unknown-assistant'))
    meter.poll()
    pending(observer, event(meter, leaf='unknown-compact'))
    view = meter.poll()
    assert view['tokens'] == lower_bound and view['coverage'] == 'unknown'
    assert view['context']['current_window'] is True
    assert view['context_windows']['windows_started'] == 2
    assert native_usage.metadata_stop(view, required=True) is None
    assert not meter.fault and not observer.fault and observer.acknowledged == 5
    pending(observer, event(meter, kind='terminal', leaf='unknown-compact'))
    observer.poll()
    terminal = backend.finish_observation(meter, required=True)
    assert not observer.fault and native_usage.metadata_stop(terminal, required=True, terminal=True) is None
    assert terminal['tokens'] == lower_bound and terminal['coverage'] == 'unknown'
    binding = terminal['session_binding']
    assert binding['thread_id'] == 'exact-session' and binding['path'] == str(meter.path)
    assert binding['workspace'] == str(meter.workspace)
    assert 'sha256' not in binding  # Missing billing does not invent a full session digest.


def test_later_known_usage_never_fills_or_rebills_prior_unknown_billing(tmp_path):
    _, meter, observer = bound(tmp_path)
    unknown = message()
    unknown.pop('usage')
    append(meter.path, entry('unknown-assistant', 'native-user', message=unknown))
    view = meter.poll()
    assert not meter.fault and view['coverage'] == 'unknown'
    assert view['has_observation'] is False  # Numeric lower-bound zeros are not measured usage.
    assert meter.counts['assistant'] == 1
    known = message(stamp=2)
    meter.events.add(USAGE, identity=pi_sessions.message_identity(known))
    append(meter.path, entry('known-assistant', 'unknown-assistant', message=known))
    expected = pi_sessions.usage_tokens(USAGE)
    for _ in range(3):
        view = meter.poll()
        assert view['tokens'] == expected and view['has_observation'] is True
        assert view['coverage'] == 'unknown' and view['reason'] == 'pi_session_usage_unreported'
    assert meter.counts['assistant'] == 2
    assert meter.persisted_messages[pi_sessions.message_identity(unknown)] is None
    assert meter.session_binding() is None and not observer.fault


@pytest.mark.parametrize('duplicate', ['entry', 'message'])
def test_unknown_billing_does_not_relax_original_identity_duplicates(tmp_path, duplicate):
    _, meter, observer = bound(tmp_path)
    unknown = message()
    unknown.pop('usage')
    original = entry('unknown-assistant', 'native-user', message=unknown)
    append(meter.path, original)
    meter.poll()
    assert not meter.fault
    append(meter.path, original if duplicate == 'entry' else
        entry('different-entry', 'unknown-assistant', message=unknown))
    pending(observer, event(meter, leaf='unknown-assistant'))
    view = meter.poll()
    assert meter.fault == 'pi_session_usage_invalid' and observer.fault
    assert native_usage.metadata_stop(view, required=True) == 'native_metadata_unavailable'


def test_optional_delivery_loss_keeps_lower_bound_but_cannot_supply_complete_authorship(tmp_path):
    _, meter, observer = bound(tmp_path)
    observer.required = False
    pending(observer, event(meter)); observer.poll()
    append(meter.path, entry('assistant', 'native-user', message=message()))
    meter.poll()
    observer.path.unlink()
    observer.poll()
    view = meter.snapshot()
    assert view['tokens'] == pi_sessions.usage_tokens(USAGE)
    assert view['metadata_delivery']['fault'] and view['context'] == dict(status='unavailable')
    assert native_usage.metadata_stop(view, required=False) is None
    with pytest.raises(ValueError, match='^pi_authorship_delivery_incomplete$'):
        observer.controlled_facts()


FAILURE_HARNESS = r'''
const vm = require("node:vm"), fs = require("node:fs");
const { createHash } = require("node:crypto");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
let source = input.source;
for (const line of [
  'import { execFileSync } from "node:child_process";\n',
  'import { createHash } from "node:crypto";\n',
  'import { performance } from "node:perf_hooks";\n'
]) source = source.replace(line, "");
source = source.replace("export default function (pi)", "function install(pi)");
const handlers = new Map(), entries = [], errors = [];
let leaf = null, aborted = 0, deliveries = 0;
const pi = { registerCommand() {}, on: (name, fn) => handlers.set(name, fn) };
const ctx = {
  cwd: input.workspace,
  sessionManager: {
    getSessionId: () => "exact-session", getSessionFile: () => input.path,
    getLeafId: () => leaf, getEntries: () => entries, getBranch: () => entries
  },
  getContextUsage: () => ({ tokens: 85, contextWindow: 100 }),
  abort: () => { aborted++; }
};
vm.runInNewContext(source + "\ninstall(pi);", {
  pi, createHash, performance: { timeOrigin: 1800000000000, now: () => 1000 },
  execFileSync: () => { deliveries++; throw new Error("private helper failure"); }
}, { timeout: 1000 });
function step(kind, value) {
  try { handlers.get(kind)(value, ctx); }
  catch { errors.push({ type: "extension_error" }); }
}
step("before_agent_start", {});
if (input.damage !== "identity") {
  entries.push({ id: "native-user", type: "message", message: { role: "user", content: input.prompt } });
  leaf = "native-user";
}
step("context", {});
entries.push({ id: "native-assistant", type: "message", message: { role: "assistant" } });
leaf = "native-assistant";
step("turn_end", { messageEntryId: "native-assistant", outcome: "completed" });
step("agent_settled", {});
process.stdout.write(JSON.stringify({ aborted, deliveries, errors }));
'''


@pytest.mark.process_e2e
@pytest.mark.parametrize('required', [False, True], ids=['optional', 'required'])
@pytest.mark.parametrize('damage', ['helper', 'identity'])
def test_generated_observer_failure_honors_requirement_and_preserves_optional_native_final(tmp_path, required, damage):
    proof = dict(required=required, metadata='/private/metadata.json', nonce='a' * 64)
    result = subprocess.run(['node', '--input-type=commonjs', '-e', FAILURE_HARNESS],
        input=json.dumps(dict(source=pi_observation.extension_source(tmp_path, proof),
            workspace=str(tmp_path), path=str(tmp_path/'session.jsonl'), prompt=PROMPT, damage=damage)),
        text=True, capture_output=True, timeout=5, check=True)
    observed = json.loads(result.stdout)
    assert observed['deliveries'] == (1 if damage == 'helper' else 0)  # Never retry a failed observer.
    assert observed['aborted'] == int(required)
    assert observed['errors'] == ([dict(type='extension_error')] if required else [])
    backend = bind_engine(tmp_path)
    for error in observed['errors']: backend.event(error)
    backend.event(dict(type='message_end', message=message()))
    terminal = backend.event(dict(type='agent_settled', aborted=required))
    if required:
        assert not terminal.get('completed')
    else:
        assert terminal == dict(completed=True, result_text=FINAL)
