"""Shared research navigation reuses accepted originals without grading science."""
import json

import pytest

from scidiscovery.research_loop import accepted_prefix, contracts as c, history, lineage, shared_input_views
from test_history_handoff import finish
from test_lineage_view import scene


def test_authored_research_navigation_separates_consultation_and_assessment():
    prior={}
    private_value='host-private-sentinel-do-not-publish'
    for step,role,purpose in [(1,'H',None),(2,'W',None),(3,'A',None),(4,'A','worker_consultation')]:
        prior=lineage.research_navigation(prior,dict(step=step,role=role,purpose=purpose,
                budget=dict(tokens=private_value),cursor=private_value,private_path='/tmp/'+private_value),
            dict(summary='作者原文'*200,scientific_result='Unresolved question: source evidence is missing.',
                limitations=['局限'*200,'Original missing input','Third limitation'],next_action='wait'),
            node_path=f'nodes/original/{step:03d}')
    assert prior['latest_h']['source_step']==1
    assert prior['latest_w']['source_step']==2
    assert prior['latest_assessment']['source_step']==3
    assert prior['latest_consultation_a']['source_step']==4
    authored=prior['latest_consultation_a']
    assert authored['summary_truncated'] and authored['limitations_truncated']
    assert authored['limitations_omitted_count']==1
    assert authored['scientific_result']==dict(path='inputs/history/nodes/original/004/science.json',pointer='/scientific_result')
    assert authored['original']=='inputs/history/nodes/original/004/scientific-original.json'
    assert len((json.dumps(prior,ensure_ascii=False,indent=2)+'\n').encode())<=history.MAX_PAGE_BYTES
    assert private_value not in json.dumps(prior)
    assert set(authored)=={'source_step','source_role','summary','summary_truncated',
        'limitations','limitations_truncated','limitations_omitted_count',
        'science','original','scientific_result'}
    assert set(prior)=={'version','latest_w','latest_h','latest_assessment',
        'latest_consultation_a','current_author','candidates','unresolved_questions','previous','scope'}


def test_formal_review_does_not_replace_assessment():
    prior=lineage.research_navigation({},dict(step=1,role='A'),
        dict(summary='Assessment',scientific_result='Evidence judgment',limitations=[],next_action='finish'),node_path='nodes/o/001')
    following=lineage.research_navigation(prior,dict(step=2,role='A',purpose='formal_review'),
        dict(summary='Exact-subject review',scientific_result='Subject only',limitations=[]),node_path='nodes/o/002')
    assert following['latest_assessment']==prior['latest_assessment']


def test_current_research_index_links_complete_accepted_original(tmp_path):
    root=scene(tmp_path)
    original='Complete authored scientific understanding '*200
    finish(root,1,'H',[],text=original,summary='Competing explanations',action='experiment')
    job=c.build_job(root,2,'W',[1])
    work=c.workspace(root,2)
    public=accepted_prefix.visibility(root,accepted_prefix.for_job(job))
    # These are logical readonly input paths; the final native mount maps the
    # existing accepted-public directory, without copying it into the workspace.
    logical_history=work/'inputs/history'
    argv=shared_input_views.mount_arguments(root,job,work,include_skills=False)
    assert any(argv[index:index+3]==['--ro-bind',str(public),str(logical_history)]
        for index in range(len(argv)-2))
    research_path=public/(work/job['history']['view']['indexes']['research']).relative_to(logical_history)
    research=c.read(research_path)
    source=research['latest_h']
    assert source['source_step']==1 and source['source_role']=='H'
    assert c.read(public/(work/source['original']).relative_to(logical_history))['scientific_result']==original
    assert c.read(public/(work/source['science']).relative_to(logical_history))['scientific_result']==original
    assert research['latest_w'] is None and research['latest_assessment'] is None
    assert (public/(work/source['original']).relative_to(logical_history)).stat().st_mode & 0o222 == 0


@pytest.mark.parametrize('role',['H','W','A','E'])
def test_accepted_recent_excerpt_is_explicit_and_complete_original_is_readable_for_every_role(tmp_path,role):
    root=scene(tmp_path)
    summary='a'+'科学视图🙂'*120
    limitations=['a'+'约束🙂'*70,'a'+'来源🙂'*70,'Third limitation','Fourth limitation','Fifth limitation']
    original='Complete authored scientific result with unresolved evidence.'
    finish(root,1,'H',[],text=original,summary=summary,limitations=limitations,action='experiment')
    job=c.build_job(root,2,role,[1])
    work=c.workspace(root,2)
    view=c.read(work/'science-task.json')
    assert len((json.dumps(view['lineage'],ensure_ascii=False,indent=2)+'\n').encode())<=history.MAX_BRIEF_BYTES
    recent=view['lineage']['recent']
    assert len(recent)==1
    excerpt=recent[0]
    assert excerpt['summary']=='a'+'科学视图🙂'*23+'科学视图'
    assert len(excerpt['summary'].encode())<=384 and excerpt['summary_truncated']
    assert all(len(text.encode())<=160 for text in excerpt['limitations'])
    assert excerpt['limitations']==['a'+'约束🙂'*15+'约束','a'+'来源🙂'*15+'来源']
    assert excerpt['limitations_truncated'] and excerpt['limitations_omitted_count']==3
    assert 'lineage.indexes.research' in view['reading_order']
    assert 'on demand' in view['reading_order']
    assert 'does not require rereading the full history' in view['reading_order']
    public=accepted_prefix.visibility(root,accepted_prefix.for_job(job))
    logical_history=work/'inputs/history'
    research=c.read(public/(work/view['lineage']['indexes']['research']).relative_to(logical_history))
    # The same producer publishes the compact default and research excerpts.
    source=research['latest_h']
    for field in ('summary','summary_truncated','limitations','limitations_truncated','limitations_omitted_count'):
        assert excerpt[field]==source[field]
    for field in ('science','original'):
        complete=c.read(public/(work/source[field]).relative_to(logical_history))
        assert complete['summary']==summary and complete['limitations']==limitations
        assert complete['scientific_result']==original


@pytest.mark.process_e2e
def test_research_candidate_shortcut_reaches_unnominated_candidate_through_previous_index(tmp_path):
    observer=tmp_path/'observer.py'
    observer.write_text('''import json,sys
request=json.load(sys.stdin)
rows=[dict(id='retained',label='Unnominated retained candidate',implementation=None,evaluation_key='fixture',metric=.5,direction='max',status='ok',evidence=['fixture-observation.json'])] if request['step']==1 else []
print(json.dumps(dict(candidates=rows,current_candidate=None,actual_submission=None,coverage=dict(status='complete',note='Fixture observation'))))
''')
    root=scene(tmp_path,observer=dict(adapter=str(observer),config={}))
    # Observer proofs name exact instance originals, rather than workspace aliases.
    c.write(root/'fixture-observation.json',dict(observable='Fixture retained measurement',value=.5))
    finish(root,1,'H',[],action='experiment')
    observed=c.read(root/'host-history/observations/001.json')
    assert [row['id'] for row in observed['candidates']]==['retained'], observed
    assert not observed.get('diagnostic') and not observed.get('candidate_diagnostics'), observed
    finish(root,2,'W',[1])
    job=c.build_job(root,3,'A',[2])
    public=accepted_prefix.visibility(root,accepted_prefix.for_job(job))
    view=c.read(c.workspace(root,3)/'science-task.json')['lineage']
    assert view['candidates']['count']==1, observed
    def read_logical(path):
        assert path.startswith('inputs/history/')
        return c.read(public/path.removeprefix('inputs/history/'))
    research=read_logical(view['indexes']['research'])
    assert research['candidates']==view['indexes']['candidates']
    current=read_logical(research['candidates'])
    assert current['step']==2 and not any(row['kind']=='candidate' for row in current['entities'])
    previous=c.read(public/current['previous'])
    candidates=[row for row in previous['entities'] if row['kind']=='candidate']
    assert len(candidates)==1, dict(entities=previous['entities'],observation=observed)
    candidate=candidates[0]
    retained=read_logical(candidate['page'])['observation']
    assert retained['label']=='Unnominated retained candidate'
    assert not {'evidence','proofs','snapshot','binding','origin_identity','source_ref'} & retained.keys()
    assert str(root) not in json.dumps(retained)
    nominations=read_logical('inputs/history/breadth-table.json')['nominations']
    assert not nominations['representatives'] and not nominations['extras']
    assert 'previous links' in research['scope']


def test_consultation_recommendation_text_does_not_enter_candidate_admission(monkeypatch):
    from scidiscovery.research_loop import candidate_refs
    def forbidden(*args,**kwargs):
        raise AssertionError('Consultation must not resolve recommendation qualification')
    monkeypatch.setattr(candidate_refs,'is_available',forbidden)
    assert history.validate_recommendation(
        dict(role='A',purpose='worker_consultation',handoff_protocol=30),
        dict(candidate_recommendation='unavailable-author-text'))==[]
