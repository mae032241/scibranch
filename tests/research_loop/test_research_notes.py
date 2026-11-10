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
