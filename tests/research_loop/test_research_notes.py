"""Pure authored-note navigation is separate from execution facts."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import execution_facts, research_notes as notes


def _record(number=1):
    return dict(version=1,note_id=f'note-{number}',call_id=f'call-{number}',
        scope=dict(step=1,role='W',job_ref=dict(path='/private/job.json')),
        native_attempt=dict(number=0,execution_key=dict(path='/private/intent.json')),
        author_attempt=dict(number=1,execution_key=dict(path='/private/later-intent.json')),
        mechanism_binding=dict(mechanism_ref='w001.test',research_binding=dict(
            mechanism_ref='w001.test',label='Read output',question='Check /private/secret result',
            status='active',exploration=False,revision=2,source_step=1,source_role='W',
            original_ref=dict(path='/private/declarations.json'),
            experiment_question=dict(question='Measure outcome',change_criterion='Change if wrong'))),
        supersedes=None,original=dict(path='/private/note.raw'),record=dict(path='/private/note.json'))


def test_projection_is_pure_bounded_and_allowlists_frozen_identity(monkeypatch):
    records=[_record(number) for number in range(20)]
    before=deepcopy(records)
    def forbidden(*args,**kwargs):pytest.fail('Projection acquired filesystem state')
    for name in ('open','stat','resolve'):monkeypatch.setattr(Path,name,forbidden)
    view=notes.render(records,scope=dict(kind='stopped_worker',node='W001',attempt=1),
        public_refs={row['note_id']:dict(record=f'work/execution-results/research-notes/{row["note_id"]}/note.json',
            original=f'work/execution-results/research-notes/{row["note_id"]}/original.raw') for row in records})
    assert records==before and view['note_count']==20 and view['omitted_rows']>0
    assert len(execution_facts.serialized_bytes(view))<=4096
    assert '/private' not in json.dumps(view) and 'execution_key' not in json.dumps(view)
    assert view['notes'][-1]['author']==dict(node='W001',attempt=1)
    assert 'claims' in view['interpretation']


def test_prefix_combination_sanitizes_nested_values_and_uses_explicit_navigation():
    row=notes.public_record(_record())
    row['author']['private']='hidden'
    row['mechanism']['private_path']='/private/control'
    row['navigation']=dict(record='/private/fabricated',original='history/nodes/001/research-notes/note-1/original.raw')
    current=dict(version=1,note_count=1,omitted_rows=0,notes=[row],
        directory=dict(kind='directory',path='work/execution-results/research-notes'))
    value=notes.combine(None,current,scope=dict(kind='accepted_sources',node='current'),
        source_cutoff=1,node_count=1,navigation=['inputs/history/nodes/001/index.json','/private/navigation'])
    assert '/private' not in json.dumps(value) and 'hidden' not in json.dumps(value)
    assert value['note_count']==1 and value['notes'][0]['navigation']==dict(
        original='history/nodes/001/research-notes/note-1/original.raw')
    assert value['navigation']==['inputs/history/nodes/001/index.json']
    assert 'directory' not in value


@pytest.mark.parametrize('raw,status,text,truncated',[
    ('中文'*100,'available','中文'*33,True),
    ('\\\"'*150,'available',('\\\"'*150).encode()[:200].decode(),True),
    ('','available','',False),
    (b'bad\xffprefix','unavailable',None,None),
    (b'binary\0prefix','unavailable',None,None),
    ('a'*203+'中','available','a'*200,True),
])
def test_preview_snapshot_is_a_bounded_valid_prefix(tmp_path,raw,status,text,truncated):
    source=tmp_path/'original.raw'
    source.write_bytes(raw.encode() if isinstance(raw,str) else raw)
    preview=notes._preview(source)
    assert preview['status']==status
    if status=='available':
        assert preview['text']==text and preview['truncated']==truncated
        assert len(preview['text'].encode())<=200
    else:assert 'reason' in preview and 'text' not in preview
    assert source.read_bytes()==(raw.encode() if isinstance(raw,str) else raw)
    assert notes._preview(tmp_path/'missing')['reason']=='prefix_read_failed'


def test_legacy_public_record_and_preview_snapshot_are_separate_from_compact_rows():
    record=_record();before=notes.public_record(record)
    assert 'preview' not in before and 'mechanism' in before
    assert 'superseded_by' not in before
    rendered=notes.render([record],scope=dict(kind='stopped_worker',node='W001'),public_refs={})
    assert notes.public_record(record)==before
    assert rendered['notes'][0]['preview']==dict(status='not_generated')
    assert 'mechanism' not in rendered['notes'][0]
    assert 'experiment_question' not in rendered['notes'][0]
    folded=notes.combine(None,rendered,scope=dict(kind='accepted_sources',node='current'),
        source_cutoff=1,node_count=1,navigation=[])
    assert folded['notes'][0]['preview']==dict(status='not_generated')


def test_revision_flags_precede_fitting_and_survive_omitted_successor_and_legacy_fold():
    records=[_record(number) for number in range(8)]
    for row in records[1:5]:row['supersedes']='note-0'
    records[5]['note_id']='note-0';records[5]['scope']['step']=2
    records[6]['supersedes']='note-0';records[6]['call_id']='different-call'
    # Only exact current-task/call successors belong to the direct reverse edge.
    for row in records[1:5]:row['call_id']=records[0]['call_id']
    view=notes.render(records,scope=dict(kind='stopped_worker',node='W001'),public_refs={},max_bytes=1400)
    first=view['notes'][0]
    assert first['note_id']=='note-0'
    assert first['superseded_by']==['note-1','note-2','note-3']
    assert first['superseded_by_omitted']==1
    assert 'note-1' not in {row['note_id'] for row in view['notes']}
    assert view['revision_coverage']=='complete'
    legacy=notes.public_record(_record(20))
    combined=notes.combine(view,dict(version=1,note_count=1,omitted_rows=0,notes=[legacy]),
        scope=dict(kind='accepted_sources',node='current'),source_cutoff=2,node_count=2,navigation=[])
    assert combined['revision_coverage']=='partial'
    old=next(row for row in combined['notes'] if row['note_id']=='note-20')
    assert 'superseded_by' not in old
    assert combined['notes'][0]['superseded_by']==first['superseded_by']
    full=notes.render(records,scope=dict(kind='stopped_worker',node='W001'),public_refs={})
    other=next(row for row in full['notes'] if row['author']['node']=='W002')
    assert other['superseded_by']==[]


def test_repeated_note_fold_preserves_available_endpoints_and_exact_omissions():
    value=None
    for number in range(25):
        record=_record(number);record['preview']=dict(version=1,status='available',text='中文\\\"'*20,truncated=True)
        current=notes.render([record],scope=dict(kind='stopped_worker',node='W001'),public_refs={})
        value=notes.combine(value,current,scope=dict(kind='accepted_sources',node='current'),
            source_cutoff=number+1,node_count=number+1,navigation=[])
        assert value['notes'][0]['note_id']=='note-0'
        assert value['notes'][-1]['note_id']==f'note-{number}'
        assert value['note_count']==value['omitted_rows']+len(value['notes'])==number+1
        assert len(execution_facts.serialized_bytes(value))<=4096
