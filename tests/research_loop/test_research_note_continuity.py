"""Optional authored notes cross real stopped and accepted public boundaries."""
from copy import deepcopy
import json

import pytest

from execution_fixtures import execution_registry, remote_registry
from test_delivery_review_guidance import reviewed
from test_direct_public_collect import stopped_attempt
from test_execution_fact_feedback import _server
from test_history_handoff import finish
from test_mutable_run_inputs import fixed_code, live_fragment, live_terminal_code
from test_worker_fragments import internal_fragment_instance, fragment_run, science
from scidiscovery.research_loop import (accepted_prefix, budget, checkpoint,
    contracts as c, controller, execution, experiments as e, history, native,
    research_notes, worker_execution_bridge)


def _note(root, job, record, row, *, note_id='reading-1', body='A scoped author interpretation.', supersedes=None):
    source=c.workspace(root,job['step'])/'work'/f'{note_id}.txt'
    source.write_text(body,encoding='utf-8')
    arguments=['note','--call-id',row['accepted']['call_id'],
        '--note-id',note_id,'--note-file',str(source)]
    if supersedes is not None:arguments+=['--supersedes',supersedes]
    reply=worker_execution_bridge._execute(_server(root,job,record,row['config']),arguments)
    assert reply['code']==0,reply
    assert reply['result']['status']=='research_note_recorded'
    return reply['result']


def _stopped(root, job, record, monkeypatch, *, memory=None, missing=False):
    c.write(record/'usage.json',dict(input_tokens=10,cached_input_tokens=3,output_tokens=4))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,
        native_started=True,terminal_observed=True,cleanup='observed_group_cleaned'))
    if not missing:
        science(record,stage_intent='handoff',research_memory=memory)
    stopped_attempt(root,job,record,monkeypatch)
    return c.accept_current_inventory(root,job['step'])


def _published(root, job, locator):
    assert locator.startswith('inputs/history/')
    return accepted_prefix.visibility(root,accepted_prefix.for_job(job))/locator.removeprefix('inputs/history/')


def test_real_notes_publish_into_memory_new_worker_and_protected_final(
        live_fragment,live_terminal_code,monkeypatch):
    root,job,record=live_fragment
    row=live_terminal_code()
    e.record_observation(row['config'],row['adapter'],row['accepted']['call_id'])
    body='研究解读：仅适用于此原始调用。未检验条件仍未知。'
    ack=_note(root,job,record,row,body=body)
    live_original=ack['note']['original']
    memory=dict(question='Which observation changes the next comparison?',clues=[dict(
        observed_fact='An author recorded a limited interpretation.',
        evidence=[dict(ref=live_original,relation='unknown',note='Author claim, not scientific qualification.')])])
    inventory=_stopped(root,job,record,monkeypatch,memory=memory)
    assert inventory.research_note_view['note_count']==1
    c.seal_result_from_inventory(root,1,inventory=inventory)

    def forbidden(*args,**kwargs):
        raise AssertionError('Successor notes must consume the accepted public prefix, without history loading')

    monkeypatch.setattr(history,'load_records_from_source_facts',forbidden)
    second=c.build_job(root,2,'W',[1])
    task_path=c.workspace(root,2)/'science-task.json'
    task=c.read(task_path)
    notes=task['research_notes']
    assert notes==second['history']['research_notes']
    assert notes['note_count']==1 and notes['source_cutoff']==1
    retained=notes['notes'][0]
    assert retained['preview']['text']==body
    assert not retained['preview']['truncated']
    assert 'After relevant observations' in task['reading_order']
    assert task['reading_order'].startswith('Original objective and constraints')
    assert retained['author']==dict(node='W001',attempt=0)
    assert retained['call_id']==row['accepted']['call_id']
    for locator in retained['navigation'].values():
        assert _published(root,second,locator).is_file()
        assert not locator.startswith('work/')
    original=retained['navigation']['original']
    assert _published(root,second,original).read_text()==body
    assert not (c.workspace(root,2)/live_original).exists()
    tip=accepted_prefix.navigation(root,accepted_prefix.for_job(second))
    clue=tip['research']['candidate_memory']['clues'][0]
    assert clue['evidence'][0]['ref']==original
    assert tip['research']['candidate_memory']['diagnostics']==[]
    historical=dict(research_memory=dict(question='Retain exact prior notes',clues=[dict(
        open_question='Does the author interpretation transfer?',
        evidence=[dict(ref=original,relation='unknown',note='Retained original author claim.')])]))
    facts=accepted_prefix._research_memory_facts(root,accepted_prefix.for_job(second),second,
        historical,dict(materials=[]),None,prior={})
    assert facts['references'][original]==original
    nonexistent=original.replace('/reading-1/','/missing-note/')
    historical['research_memory']['clues'][0]['evidence'][0]['ref']=nonexistent
    missing_facts=accepted_prefix._research_memory_facts(root,accepted_prefix.for_job(second),second,
        historical,dict(materials=[]),None,prior={})
    assert nonexistent not in missing_facts['references']
    public=json.dumps(notes,ensure_ascii=False)
    assert str(root) not in public and 'host-runtime' not in public

    frozen=task_path.read_bytes()
    finish(root,2,'W',[1],stage_intent='decision')
    final=reviewed(root,3,2,0)
    final_task=c.read(c.workspace(root,3)/'science-task.json')
    assert final_task['research_notes']['note_count']==1
    assert final_task['research_notes']['notes'][0]['navigation']['original']==original
    assert final_task['research_notes']['notes'][0]['preview']['text']==body
    assert 'At the relevant evidence and limitations' in final_task['reading_order']
    assert _published(root,final,original).read_text()==body
    assert task_path.read_bytes()==frozen


def test_missing_final_science_keeps_acknowledged_notes_and_abnormal_resume_discovery(
        live_fragment,live_terminal_code,monkeypatch):
    root,job,record=live_fragment
    row=live_terminal_code()
    e.record_observation(row['config'],row['adapter'],row['accepted']['call_id'])
    body='研究\\\"\n'*20000
    ack=_note(root,job,record,row,body=body)
    inventory=_stopped(root,job,record,monkeypatch,missing=True)
    saved=checkpoint._capture(root,job,record,inventory=inventory)
    frozen=deepcopy(saved['resume_brief'])
    assert saved['science']['status']=='missing'
    assert frozen['science_status']=='missing'
    assert frozen['research_notes']['note_count']==1
    assert frozen['research_notes']['notes'][0]['preview']['text']
    assert frozen['research_notes']['notes'][0]['preview']['truncated']
    assert len(checkpoint._bytes(frozen))<=checkpoint.MAX_BRIEF_BYTES
    assert (c.workspace(root,1)/ack['note']['original']).read_text()==body
    assert not (record/'science-output.json').exists()
    assert not (record/'result-identity.json').exists()
    task=c.read(c.workspace(root,1)/'science-task.json')
    assert task['research_note_recording']['index']=='work/execution-results/research-notes/index.json'
    for intent in (dict(continuation_kind='operator_resume',context_mode='new_context'),
            dict(continuation_kind='reentry',explicit_recovery=True,context_mode='new_context')):
        prompt,_,_=native.render_scientific_request(job,task,intent,
            backend_name='protocol',instruction_transport='prompt')
        assert 'science-task.json' in prompt
    live=c.read(c.workspace(root,1)/'work/execution-results/research-notes/index.json')
    assert live['notes'][0]['navigation']['original']==ack['note']['original']

    def forbidden(*args,**kwargs):raise AssertionError('Checkpoint consumers must not read note originals')

    with monkeypatch.context() as patch:
        patch.setattr(c,'read',forbidden)
        patch.setattr(research_notes,'load',forbidden)
        assert checkpoint.brief(checkpoint=saved)==frozen
        native.write_science_resume(c.workspace(root,1),intent=dict(science_resume=frozen))
    assert c.read(c.workspace(root,1)/'work/science-resume.json')==frozen

    many=deepcopy(inventory.research_note_records)*40
    for index,value in enumerate(many):value['note_id']=f'bounded-{index}'
    view=research_notes.render(many,scope=dict(kind='stopped_worker',node='W001',attempt=0),
        public_refs={value['note_id']:value['public'] for value in many},max_bytes=1024)
    view['navigation']=['work/execution-results/research-notes/index.json']
    compact=checkpoint._make_brief(job,saved,science=None,
        execution_view=inventory.execution_fact_view,research_note_view=view)
    assert compact['research_notes']['note_count']==40
    assert compact['research_notes']['omitted_rows']>0
    assert compact['research_notes']['navigation']==view['navigation']
    assert len(checkpoint._bytes(compact))<=checkpoint.MAX_BRIEF_BYTES


def test_optional_note_projection_failure_preserves_allowed_seal(
        live_fragment,live_terminal_code,monkeypatch):
    root,job,record=live_fragment
    row=live_terminal_code()
    e.record_observation(row['config'],row['adapter'],row['accepted']['call_id'])
    _note(root,job,record,row)
    def unavailable(*args,**kwargs):raise ValueError('Optional presentation cannot fit')
    monkeypatch.setattr(research_notes,'render',unavailable)
    inventory=_stopped(root,job,record,monkeypatch)
    assert inventory.research_note_view is None
    assert len(inventory.research_note_records)==1
    c.seal_result_from_inventory(root,1,inventory=inventory)
    assert (record/'result-identity.json').is_file()
    successor=c.build_job(root,2,'W',[1])
    node=accepted_prefix.navigation(root,accepted_prefix.for_job(successor))
    manifests=accepted_prefix.research_note_files(node)
    assert len(manifests)==2
    base=accepted_prefix.for_job(successor)['tip']['navigation']['path']
    original=next(value for value in manifests if value['path'].endswith('/original.raw'))
    from pathlib import Path
    locator='inputs/history/'+(Path(base).parent/original['path']).as_posix()
    assert _published(root,successor,locator).read_text()=='A scoped author interpretation.'


def test_stopped_note_access_composite_stays_bounded_through_checkpoint(
        live_fragment,live_terminal_code,monkeypatch):
    import test_mutable_run_inputs as inputs
    from scidiscovery.research_loop import execution_facts
    original_research=inputs._research
    def long_question(config):
        value=original_research(config)
        value['declarations'][0]['question']='研究"\\'*18
        value['experiment_question']['question']='研究"\\'*18
        value['experiment_question']['change_criterion']='条件"\\'*18
        return value
    monkeypatch.setattr(inputs,'_research',long_question)
    root,job,record=live_fragment
    row=live_terminal_code()
    e.record_observation(row['config'],row['adapter'],row['accepted']['call_id'])
    for number in range(12):
        _note(root,job,record,row,note_id=f'bounded-{number:02d}',body=f'Scoped original author claim {number}.',
            supersedes='bounded-00' if number==1 else None)
    inventory=_stopped(root,job,record,monkeypatch,missing=True)
    view=inventory.research_note_view
    assert view is not None and view['note_count']==12
    assert view['omitted_rows']>0
    assert view['navigation']==['work/execution-results/research-notes/index.json']
    assert len(execution_facts.serialized_bytes(view))<=research_notes.MAX_BYTES
    unbounded=research_notes.render(inventory.research_note_records,
        scope=view['scope'],public_refs={value['note_id']:value['public'] for value in inventory.research_note_records})
    unbounded['navigation']=view['navigation'];unbounded['access']=view['access']
    assert len(execution_facts.serialized_bytes(unbounded))>research_notes.MAX_BYTES
    saved=checkpoint._capture(root,job,record,inventory=inventory)
    notes=saved['resume_brief']['research_notes']
    assert notes['note_count']==12 and notes['omitted_rows']>0
    assert notes['navigation']==view['navigation']
    assert notes['notes'][0]['note_id']=='bounded-00'
    assert notes['notes'][0]['preview']['text']=='Scoped original author claim 0.'
    assert notes['notes'][0]['superseded_by']==['bounded-01']
    if len(notes['notes'])>1:assert notes['notes'][-1]['note_id']=='bounded-11'
    assert len(checkpoint._bytes(saved['resume_brief']))<=checkpoint.MAX_BRIEF_BYTES

    facts=[execution_facts.accepted_fact(call_id=f'unresolved-{number:03d}',status='unknown',
        accepted=dict(call_id=f'unresolved-{number:03d}'),observed=None,outputs=None,
        observation_basis='accepted_call') for number in range(60)]
    unresolved=execution_facts.render(facts=facts,scope=view['scope'],public_refs={},coverage='accepted_scope')
    def forbidden(*args,**kwargs):raise AssertionError('Final packet trimming acquired originals')
    with monkeypatch.context() as patch:
        patch.setattr(c,'read',forbidden)
        patch.setattr(research_notes,'load',forbidden)
        packet=checkpoint._make_brief(job,saved,science=None,
            execution_view=unresolved,research_note_view=view)
    shown=packet['execution_facts']
    assert shown['unresolved_calls']==60
    assert shown['unresolved_shown']==len(shown['calls'])
    assert shown['unresolved_omitted']==60-len(shown['calls'])>0
    assert shown['unresolved_id_access'] in {'limited','unavailable'}
    assert shown['omitted_rows']==60-len(shown['calls'])
    assert packet['research_notes']['note_count']==12
    assert len(checkpoint._bytes(packet))<=checkpoint.MAX_BRIEF_BYTES


def _instrument_fragments(root):
    launcher=root.parent/'fake-fragments.py'
    program=launcher.read_text()
    anchor="        (work/'work/original-call').write_text(result['call_id'])"
    assert program.count(anchor)==1
    program=program.replace(anchor,anchor+'''
        note=work/'work/reading-1.txt';note.write_text('First authored interpretation; target limits remain unknown.')
        assert exp(['note','--call-id',result['call_id'],'--note-id','reading-1','--note-file',str(note)])['status']=='research_note_recorded'
''')
    anchor="        call=(work/'work/original-call').read_text()"
    assert program.count(anchor)==1
    program=program.replace(anchor,anchor+'''
        assert 'science-task.json' in prompt
        task=json.loads((work/'science-task.json').read_text())
        assert task['research_note_recording']['index']=='work/execution-results/research-notes/index.json'
        notes=json.loads((work/'work/execution-results/research-notes/index.json').read_text())
        assert notes['notes'][0]['note_id']=='reading-1'
        assert notes['notes'][0]['preview']['text']=='First authored interpretation; target limits remain unknown.'
        note=work/'work/reading-2.txt';note.write_text('Revised authored interpretation with the same original call.')
        assert exp(['note','--call-id',call,'--note-id','reading-2','--note-file',str(note),'--supersedes','reading-1'])['status']=='research_note_recorded'
''')
    launcher.write_text(program)


@pytest.mark.process_e2e
def test_real_fragment_notes_freeze_then_publish_both_revisions(internal_fragment_instance):
    root=internal_fragment_instance(context='new_context')
    _instrument_fragments(root)
    result=fragment_run(root,'research-note-continuity')
    assert result['status']=='finish',result
    chain=execution.records(root,1)
    assert len(chain)==2
    saved=c.read(chain[0]/'checkpoint.json')
    frozen=saved['resume_brief']['research_notes']
    assert frozen['note_count']==1
    assert frozen['notes'][0]['note_id']=='reading-1'
    assert c.read(chain[1]/'native-intent.json')['science_resume']['research_notes']==frozen
    final=c.read(root/'jobs/002.json')
    notes=c.read(c.workspace(root,2)/'science-task.json')['research_notes']
    assert notes['note_count']==2
    by_id={row['note_id']:row for row in notes['notes']}
    assert set(by_id)=={'reading-1','reading-2'}
    assert by_id['reading-2']['supersedes']=='reading-1'
    assert by_id['reading-1']['superseded_by']==['reading-2']
    assert by_id['reading-2']['preview']['text']=='Revised authored interpretation with the same original call.'
    assert by_id['reading-2']['author']['attempt']==1
    assert by_id['reading-1']['call_id']==by_id['reading-2']['call_id']
    for row in notes['notes']:
        assert _published(root,final,row['navigation']['original']).is_file()
    assert c.read(chain[0]/'checkpoint.json')['resume_brief']['research_notes']==frozen


@pytest.mark.process_e2e
def test_explicit_abnormal_reentry_discovers_same_task_original_notes(
        internal_fragment_instance,monkeypatch):
    import time
    from scidiscovery.research_loop import continuation,runtime
    monkeypatch.setenv('SCID_U2_FAKE_CASE','failure')
    root=internal_fragment_instance(context='new_context')
    _instrument_fragments(root)
    result=controller.run(root)
    assert result['status']=='native_stopped',result
    original=execution.origin(root,1)
    saved=c.read(original/'checkpoint.json')
    assert saved['science']['status']=='missing'
    assert saved['resume_brief']['research_notes']['note_count']==1
    until=time.monotonic()+5
    while runtime.process_matches(c.read(original/'runner-process.json'),execution.identity(root,1,0)):
        assert time.monotonic()<until
        time.sleep(.02)
    recovery=continuation.reenter(root,1,explicit=True,expected_attempt=0,
        native_intent_ref=dict(path=str(original/'native-intent.json')),new_context=True)
    assert recovery['continuation_kind']=='reentry' and recovery['explicit_recovery']
    native.wait_native(root,1)
    result=controller.run(root)
    assert result['status']=='finish',result
    chain=execution.records(root,1)
    assert len(chain)==2
    assert c.read(chain[1]/'native-intent.json')['task_ref']==c.record_ref(root/'jobs/001.json')
    notes=c.read(c.workspace(root,2)/'science-task.json')['research_notes']
    assert notes['note_count']==2
    by_id={row['note_id']:row for row in notes['notes']}
    assert by_id['reading-2']['author']['attempt']==1
    assert by_id['reading-1']['call_id']==by_id['reading-2']['call_id']
    assert c.read(original/'checkpoint.json')['resume_brief']['research_notes']['note_count']==1


def test_protected_final_report_uses_real_accepted_note_original(
        live_fragment,live_terminal_code,monkeypatch):
    root,job,record=live_fragment
    row=live_terminal_code()
    e.record_observation(row['config'],row['adapter'],row['accepted']['call_id'])
    _note(root,job,record,row)
    inventory=_stopped(root,job,record,monkeypatch)
    c.seal_result_from_inventory(root,1,inventory=inventory)
    c.write(root/'host-started.json',budget.clock_stamp())
    begin=budget.begin_closeout(root,1,2,'Retain authored notes at protected FINAL',final_only=True)
    def stop_before_native(*args,**kwargs):raise RuntimeError('No native launch in this fixture')
    monkeypatch.setattr(controller,'_native_call',stop_before_native)
    assert controller._run_final(root,begin)['status']=='closeout_native_unsealed'
    final=c.read(root/'jobs/002.json')
    assert final['final_only'] and final['role']=='A'
    task=c.read(c.workspace(root,2)/'science-task.json')
    notes=task['research_notes']
    assert notes['note_count']==1 and notes['source_cutoff']==1
    assert notes['notes'][0]['preview']['text']=='A scoped author interpretation.'
    assert 'At the relevant evidence and limitations' in task['reading_order']
    assert _published(root,final,notes['notes'][0]['navigation']['original']).is_file()


def test_current_fork_retains_exact_published_notes_from_its_input_prefix(
        live_fragment,live_terminal_code,monkeypatch):
    from scidiscovery.research_loop import code_versions,fork_runtime,fork_service
    root,job,record=live_fragment
    row=live_terminal_code()
    e.record_observation(row['config'],row['adapter'],row['accepted']['call_id'])
    _note(root,job,record,row,body='An exact original retained through the selected fork input.')
    inventory=_stopped(root,job,record,monkeypatch)
    c.seal_result_from_inventory(root,1,inventory=inventory)
    selected=c.build_job(root,2,'W',[1])
    # The source fixture already replaces Git capture/verification. Keep that
    # same mocked Git boundary while exercising actual fork closure/publication.
    monkeypatch.setattr(code_versions,'transfer',lambda *args,**kwargs:None)
    (record/'runner.lock').touch(exist_ok=True)
    with controller._writer(root):pass
    child=root.parent/'child-note-continuity'
    limits=dict(max_tokens=1000,max_seconds=30,max_steps=2)
    preview=fork_service.preview(root,2,child,budget=limits)
    assert not preview['blockers'],preview
    fork_service.create(root,2,child,budget=limits,
        expected_preview_id=preview['preview_id'],operation_id='authored-notes-input-prefix')
    inherited=fork_runtime.build_selected_job(child,2)
    notes=c.read(c.workspace(child,2)/'science-task.json')['research_notes']
    assert notes==selected['history']['research_notes']
    original=notes['notes'][0]['navigation']['original']
    assert _published(child,inherited,original).read_text()=='An exact original retained through the selected fork input.'
