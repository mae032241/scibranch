"""Verified permanent non-submission is retained, never a scientific observation.

Only the transport and gate dependencies are trusted stand-ins. Retirement,
archival, W sealing/assessment and A screening use their production validators.
No process, solver, actual authorization or independence qualification is claimed.
"""
import io
from pathlib import Path

import pytest

from scidiscovery.artifact_agent.schema.execution import ExecutionAdmission, LocalFileDescriptor
from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.research_loop import (contracts as c, controller, execution, experiment_client,
    experiments as e, history, search, worker_delivery)
from scidiscovery.research_loop.output_repair import WorkRepairRequired
from scidiscovery.research_loop.protocol import stage_scope
from tcad_artifact.managed_execution import TCADManagedExecution
from execution_fixtures import execution_registry
from tests.operations.test_runtime_plugin_configuration import (
    _ManagedTCADBackend, _small_resolved_execution_package)
from test_breadth_science import prepare, h, worker, findings, a
from test_history_handoff import finish
from test_worker_delivery import output, tree




from contextlib import nullcontext
from copy import deepcopy
from scidiscovery.research_loop import budget, qualification as q


@pytest.fixture
def retirement_control(tmp_path,monkeypatch):
    """Real control refs/retirement validation; only ledger storage is a stand-in."""
    root=tmp_path/'retirement-controls';root.mkdir()
    settings=dict(goal_id='source-fixture')
    monkeypatch.setattr(c,'settings',lambda selected:settings)
    monkeypatch.setattr(budget,'_ledger_writer',lambda selected:nullcontext())
    def create(*,authority_stat=False):
        call_id='0001-source-fixture';scope=dict(step=1,role='W')
        contract=dict(e.RETIREMENT_CONTRACT,backend_protocol=dict(fixture='never submitted'))
        capability=dict(key=e.RETIREMENT_KEY,kind='execution_recovery',schema_id=e.RETIREMENT_SCHEMA,
            payload_schema_version=1,media_type='application/json',size_bytes=len(e.encoded(contract)))
        interface=dict(executor='fixture:source_only',public_capabilities=[capability])
        authority_path=root/'authority.json';c.write(authority_path,dict(fixture='original control'))
        authority=(e.proof(authority_path) if authority_stat else
            dict(q._ref(root,authority_path),path=str(authority_path)))
        source=root/'source.py';source.write_bytes(b'never submitted source')
        prepared=LocalFileDescriptor(name=call_id,local_path=str(source),size_bytes=source.stat().st_size,
            media_type='application/octet-stream').model_dump(mode='json')
        reservation=q._save(root,root/'reservation.json',dict(fixture='held resources'))
        dispatch=root/'dispatch.json'
        accepted=dict(call_id=call_id,scope=scope,native_attempt=dict(number=0),interface=interface,
            script=c.file_ref(source),prepared=prepared,execution_authority=authority,
            execution_reservation=reservation,submission_intent_path=str(dispatch))
        accepted_path=root/'accepted.json';c.write(accepted_path,accepted)
        c.write(dispatch,dict(call_id=call_id,scope=scope,prepared=prepared,authority=authority))
        native=root/'native-fence.json';c.write(native,dict(status='permanent_not_submitted'))
        value=dict(version=2,kind='permanent_not_submitted',call_id=call_id,scope=scope,
            native_attempt=accepted['native_attempt'],interface=interface,accepted=e.proof(accepted_path),
            prepared=prepared,authority=authority,capability=capability,contract=contract,
            native_originals=[e.proof(native)],dispatch_intent=e.proof(dispatch))
        assertion=root/'assertion.json'
        def publish():
            c.write(assertion,dict(version=1,kind='permanent_not_submitted',
                **{key:value[key] for key in ('call_id','scope','accepted','prepared','authority','native_originals')}))
            value['assertion']=e.proof(assertion)
            c.write(root/'retirement.json',value)
            return q._ref(root,root/'retirement.json')
        retirement=publish()
        assert e.verify_retirement(value,lambda item:Path(item['path']))['accepted']==e.proof(accepted_path)
        row=dict(ref=reservation,owner=dict(fixture='one owner'),binding={key:accepted[key] for key in (
            'call_id','scope','native_attempt','interface')},actual={},released=False,
            bound=dict(accepted=q._ref(root,accepted_path),prepared=prepared))
        row['binding']['authority']=authority
        events=[]
        monkeypatch.setattr(budget,'_events',lambda selected,**options:(events,None,({}, {'a'*64:row})))
        def append(selected,settings,previous,parent,**fields):
            assert fields['kind']=='resource_release'
            events.append(fields);row['released']=True
        monkeypatch.setattr(budget,'_append',append)
        return dict(root=root,value=value,row=row,events=events,reservation=reservation,
            retirement=retirement,accepted=accepted,accepted_path=accepted_path,
            authority_path=authority_path,publish=publish)
    return create


@pytest.mark.parametrize('authority_stat',[False,True])
def test_retirement_releases_real_control_reference_shape_once(retirement_control,authority_stat):
    row=retirement_control(authority_stat=authority_stat)
    assert set(row['row']['bound']['accepted'])=={'path','size_bytes'}
    assert set(row['value']['authority'])==({'path','size_bytes','file_stat'} if authority_stat else {'path','size_bytes'})
    for _ in range(2):
        assert budget.release_execution(row['root'],reservation=row['reservation'],
            retirement=row['retirement'])==row['reservation']
    assert row['row']['released'] and len(row['events'])==1


@pytest.mark.parametrize('damage',['accepted','authority','unknown','actual'])
def test_retirement_release_preserves_resources_without_exact_proof(retirement_control,damage):
    row=retirement_control(authority_stat=damage=='authority')
    value=row['value'];root=row['root'];retirement=row['retirement']
    # The fixture first verifies the complete legal original with the real reader.
    assert e.verify_retirement(value,lambda item:Path(item['path']),verify_files=False)
    if damage=='accepted':
        other=root/'other-accepted.json';c.write(other,row['accepted'])
        value['accepted']=e.proof(other);retirement=row['publish']()
        assert e.verify_retirement(value,lambda item:Path(item['path']),verify_files=False)
    elif damage=='authority':
        path=row['authority_path'];replacement=path.with_name('replacement.json')
        replacement.write_bytes(path.read_bytes());replacement.replace(path)
    elif damage=='unknown':
        retirement=q._save(root,root/'unknown.json',dict(status='unknown',found=False))
    else:row['row']['actual']={'wall_time_seconds':1}
    with pytest.raises((ValueError,KeyError)):
        budget.release_execution(root,reservation=row['reservation'],retirement=retirement)
    assert not row['row']['released'] and row['events']==[]
    assert row['row']['actual']==({'wall_time_seconds':1} if damage=='actual' else {})


@pytest.mark.parametrize('key',['accepted','assertion'])
def test_retirement_saved_execution_fingerprint_rejects_equal_byte_replacement(retirement_control,key):
    row=retirement_control();value=row['value']
    assert set(value[key])=={'path','size_bytes','file_stat'}
    assert e.verify_retirement(value,lambda item:Path(item['path']))
    path=Path(value[key]['path']);replacement=path.with_name(path.name+'.replacement')
    replacement.write_bytes(path.read_bytes());replacement.replace(path)
    with pytest.raises(e.IntegrityError,match='Retirement original changed'):
        e.verify_retirement(value,lambda item:Path(item['path']))
    assert not row['row']['released'] and row['events']==[]
