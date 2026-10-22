"""Complete scientific records survive sizes beyond compact model previews."""
import json
from pathlib import Path
import sys

import pytest

from execution_fixtures import execution_registry
from test_lineage import scene as cov_scene
from test_history_handoff import finish
from test_worker_fragments import fragment, science
from scidiscovery.research_loop import checkpoint, contracts as c, controller, execution, history, lineage, selection
from cov_execution import lineage as cov


@pytest.mark.process_e2e
def test_twenty_five_real_calls_keep_large_snapshot_contexts_and_restart_bindings(tmp_path,execution_registry):
    outer=tmp_path/('long-real-instance-owner-'+'q'*100);outer.mkdir()
    root,config,digest=cov_scene(outer)
    script=c.workspace(root,2)/'work/train.py';script.write_text('success')
    results=[cov.run(config,digest,script,new=True) for _ in range(25)]
    assert all(row['status']=='known_success' for row in results)
    finish(root,2,'W',[1],text='A complete authored record. '+('科学 evidence \\"' * 12000))
    seal_path=execution.origin(root,2)/'result-identity.json';sealed=seal_path.read_bytes()
    seal=c.read(seal_path);path=root/seal['observations']['path'];frozen=path.read_bytes();value=c.read(path)
    assert len(frozen)>256000
    assert len(value['contexts'])==len(value['managed_calls'])==len(value['candidates'])==25
    assert value['coverage']['status']=='complete' and 'diagnostic' not in value
    expected={item['candidate_id']:item['call_id'] for item in value['contexts']}
    assert all(item['status']=='known_success' and item['parent']['status']=='declared_root' for item in value['contexts'])
    # A receives all associations, not rediscovered candidates without contexts.
    job=c.build_job(root,3,'A',[1,2]);assert len(job['history']['candidate_ids'])==25
    graph=lineage.project(root,3)['graph']
    assert {key:item['context']['call_id'] for key,item in graph['candidates'].items()}==expected
    assert len(history._bytes(job['history']['view']))<=history.MAX_BRIEF_BYTES
    assert all(row['size_bytes']<=history.MAX_PAGE_BYTES for row in job['history']['files'])
    finish(root,3,'A',[1,2],recommendation=results[0]['candidate_id'])
    assert history._source(root,2)[2]['observations']==seal['observations']
    # Disposable projections can disappear across process restart.
    for cached in (root/'host-history/nodes').glob('*.json'):cached.unlink()
    restored=lineage.project(root,4)
    assert {key:item['context']['call_id'] for key,item in restored['graph']['candidates'].items()}==expected
    c.validate(c.workspace(root,2));c.validate(c.workspace(root,3))
    assert path.read_bytes()==frozen and seal_path.read_bytes()==sealed
    # The archived accepted identity remains authoritative after size caps go.
    original=root/value['contexts'][0]['proofs'][0]['snapshot']
    original.chmod(0o600)
    with original.open('ab') as stream:stream.write(b'changed')
    with pytest.raises(history.ObserverIntegrityError):history._source(root,2)


def test_checkpoint_full_material_list_and_large_science_keep_small_brief(fragment):
    root,job,record=fragment
    science(record,scientific_result='Full scientific original: '+('\\\"物理 ' * 30000))
    assert (record/'science-output.json').stat().st_size>64000
    artifacts=c.workspace(root,1)/'artifacts';artifacts.mkdir(exist_ok=True)
    for number in range(140):(artifacts/(f'{number:03d}-'+'m'*170)).write_text(str(number))
    saved=checkpoint.capture(root,job,record)
    assert len(saved['materials'])==140 and saved['omitted_materials']==0
    assert (record/'checkpoint.json').stat().st_size>64000
    assert checkpoint.validate(root,job,record)==saved
    view=checkpoint.brief(root,job,record)
    assert len(checkpoint._bytes(view))<=checkpoint.MAX_BRIEF_BYTES
    assert len(view['direct_files'])+view['omitted_files']==140
    assert view['truncated']


def test_archive_streams_complete_large_file_and_never_relaxes_path_checks(fragment,monkeypatch):
    root,_,_=fragment;source=root/'large-original.bin'
    with source.open('wb') as stream:
        for _ in range(34):stream.write(b'x'*(1024*1024))
    source_size=source.stat().st_size
    original_read=Path.read_bytes
    def no_full_bytes(self):
        if self==source or self.is_relative_to(root/'host-history/objects'):
            raise AssertionError('Full binary original was loaded into memory')
        return original_read(self)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'read_bytes',no_full_bytes)
        proof=history._proof(root,source.relative_to(root).as_posix())
        history._validate_proof(root,proof)
    assert proof['retention']=='archived' and proof['size_bytes']==source_size
    assert c.digest(root/proof['snapshot'])==c.digest(source)
    alias=root/'alias';alias.symlink_to(source)
    for name in [str(source),'../large-original.bin','alias']:
        with pytest.raises(history.ObserverIntegrityError):history._proof(root,name)


@pytest.mark.process_e2e
def test_internal_observer_transport_has_no_compact_request_or_response_cap(tmp_path):
    script=tmp_path/'observer.py'
    script.write_text('import json,sys\nq=json.load(sys.stdin)\nprint(json.dumps(dict(original=q["original"])))\n')
    config=history.observer_config(dict(adapter=str(script),config={}))
    original='完整原件'*100000
    assert history.read_adapter(config,dict(original=original))==dict(original=original)


def test_oversized_navigation_row_is_omitted_without_limiting_complete_pages(fragment):
    root,_,_=fragment;work=c.workspace(root,1);_,paged,files=history.page_writer(work)
    rows=[dict(id='retained-id',label='x'*100000)]
    pages=paged(rows,8,'records','inputs/history/large')
    page=c.read(work/pages[0])
    assert page['records'][0]['id']=='retained-id' and page['records'][0]['view_omitted']
    assert rows[0]['label']=='x'*100000
    assert all(item['size_bytes']<=history.MAX_PAGE_BYTES for item in files)


@pytest.mark.parametrize('quotas',[{},dict(max_output_item_bytes=100000,max_output_total_bytes=200000)])
def test_new_default_has_no_storage_cap_and_explicit_quota_is_still_enforced(tmp_path,quotas):
    public=tmp_path/'public';public.mkdir();objective=public/'goal';objective.write_text('Exact original storage.')
    root=tmp_path/'instance'
    settings=controller.prepare(root,objective,public,['never-launched'],max_tokens=1000,max_seconds=60,max_steps=5,
        model='no-model',reasoning_effort='high',search_policy=None,**quotas)
    assert (settings['max_output_item_bytes'],settings['max_output_total_bytes'])==((100000,200000) if quotas else (None,None))
    c.build_job(root,1,'W',[]);output=c.workspace(root,1)/'artifacts/data';output.parent.mkdir(exist_ok=True)
    output.write_bytes(b'x'*100001)
    if quotas:
        with pytest.raises(ValueError,match='item byte budget'):c.collect_materials(root,1)
    else:assert c.collect_materials(root,1)[0]['size_bytes']==100001


def test_full_candidate_accumulation_is_not_a_compact_count_limit(fragment):
    rows=[dict(id=f'candidate-{n}',label='row',implementation=None,evaluation_key='one-public-version',metric=float(n),direction='min',status='ok',evidence=[]) for n in range(4100)]
    records=[dict(step=1,role='W',observation=dict(candidates=rows,current_candidate=None,actual_submission=None,coverage=dict(status='complete',note='Fixture')),recommendation=None)]
    view=lineage.candidate_view(records)
    expected=[row['id'] for row in rows]
    assert [row['id'] for row in view['candidate_index']]==expected
    assert [row['id'] for row in view['candidate_facts']]==expected[:16]
    assert view['candidates_omitted_count']==4084
    compact=selection.project(selection.public_inputs(records))
    assert len(selection._bytes(compact))<=history.MAX_BRIEF_BYTES
    assert 0<len(compact['candidate_facts'])<=16
    assert [row['id'] for row in compact['candidate_facts']]==expected[:len(compact['candidate_facts'])]
    assert len(compact['candidate_facts'])+compact['candidate_facts_omitted_count']==4100
    root,_,_=fragment;work=c.workspace(root,1)
    _,paged,files=history.page_writer(work)
    pages=paged(view['candidate_index'],16,'candidates','inputs/history/complete-candidates')
    restored=[];page=pages[0]
    while page:
        value=c.read(work/page);restored.extend(value['candidates']);page=value['next_page']
    assert [row['id'] for row in restored]==expected
    assert restored==view['candidate_index']
    assert all(row['size_bytes']<=history.MAX_PAGE_BYTES for row in files)


def test_complete_implementation_selection_is_not_capped_at_sixteen(tmp_path):
    public=tmp_path/'public';public.mkdir();objective=public/'goal';objective.write_text('Retain exact implementation identities.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launched'],max_tokens=1000,max_seconds=60,max_steps=6,
        model='no-model',reasoning_effort='high',search_policy=None)
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1],files={f'file-{n:03d}':'original' for n in range(17)})
    finish(root,3,'A',[1,2],selection=[f'W002-I{n:03d}' for n in range(1,18)])
    seal=c.read(execution.origin(root,3)/'result-identity.json')
    assert len(seal['selected_inputs'])==17
    job=c.build_job(root,4,'H',[3])
    assert len(job['selected_inputs'])==len(job['direct_materials'])==17
    c._validate_inputs(root,c.workspace(root,4),job)


@pytest.mark.process_e2e
def test_large_coverage_record_does_not_fail_scientific_seal_or_overview(tmp_path):
    public=tmp_path/'public';public.mkdir();objective=public/'goal';objective.write_text('Retain complete provenance.')
    adapter=tmp_path/'observer.py';adapter.write_text('import json\nprint(json.dumps(dict(candidates=[],current_candidate=None,actual_submission=None,coverage=dict(status="partial",note="original "*20000))))\n')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launched'],max_tokens=1000,max_seconds=60,max_steps=3,
        model='no-model',reasoning_effort='high',search_policy=None,history_observer=dict(adapter=str(adapter),config={}))
    finish(root,1,'H',[])
    seal=c.read(execution.origin(root,1)/'result-identity.json');full=c.read(root/seal['observations']['path'])
    assert full['coverage']['note']=='original '*20000
    assert (root/'host-history/overview.json').stat().st_size<=history.MAX_PAGE_BYTES
    assert c.read(root/'host-history/overview.json')['view_omitted']
    c.validate(c.workspace(root,1))
