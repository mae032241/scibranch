"""A retained failed-turn meter does not close later authorized exploration."""
import sys

import pytest

from scidiscovery.research_loop import accepted_budget as accepted, budget
from scidiscovery.research_loop import contracts as c, controller, execution, native
from test_accepted_budget_head import prepare
from test_automatic_recovery import AUTOMATIC_NATIVE
import test_default_cli_output_repair as default_cli


def test_current_unknown_original_blocks_execution_without_a_metering_block(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    c.build_job(root,1,'H',[])
    record = execution.origin(root,1)
    accepted.accept_native(root,1,record,dict(step=1,task_ref=c.record_ref(root/'jobs/001.json')))
    meter = dict(input_tokens=17,cached_input_tokens=2,output_tokens=3)
    c.write(record/'usage.json',meter)
    c.write(record/'usage-observation.json',dict(coverage='unknown',reason='terminal_usage_unreported',tokens=meter))
    originals = {path:path.read_bytes() for path in (root/'jobs/001.json',record/'native-intent.json',record/'usage.json')}
    usage = native.usage(root)
    assert usage['unknown_steps'] == [1] and usage['observed_tokens'] == 20
    assert usage['blocking_unknown_count'] == 0 and usage['blocking_unknown_steps'] == []
    assert budget.execution_blocked(root)
    with pytest.raises(RuntimeError,match='running or unknown original execution'):
        budget.amend(root,max_tokens=1200,authorization_note='An unresolved original cannot be replaced.')
    monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**k:pytest.fail('An unknown original must not be submitted again'))
    result = controller.run(root)
    assert result['status'] == 'native_outcome_unsealed', result
    assert not (record/'continuations').exists()
    assert not (root/'jobs/002.json').exists()
    assert all(path.read_bytes() == data for path,data in originals.items())
    assert native.usage(root) == usage
