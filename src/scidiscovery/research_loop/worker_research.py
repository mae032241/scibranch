"""Live W declarations in the existing managed receipt namespace.

The operation owner loads a current fact once. Validation and projection are
pure; publication keeps immutable revisions and a single current pointer.
Scientific identity is declared by W, never inferred from wording or receipts.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re


def enabled(config):
    return type(config.get('research_strategy_version')) is int and config['research_strategy_version'] == 3


def applicable(job):
    """Select the frozen job phase that owns live W declarations; no I/O."""
    if not enabled(job) or job.get('role')!='W':return False
    from . import delivery_review
    return (delivery_review.scope(job) is None and not job.get('formal_review')
        and not job.get('engineering_repair') and not job.get('final_only') and not job.get('final_report')
        and job.get('purpose') not in {'closeout','formal_review','engineering_repair','worker_consultation'})


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Worker research requires ' + name)
    return value


def initial(config):
    """Seed already accepted identities from this exact task's frozen input."""
    mechanisms = {}
    for row in (config.get('breadth') or {}).get('mechanisms', []):
        if row.get('research_binding') is not None:
            retained=copy.deepcopy(row['research_binding']);retained.pop('experiment_question',None);retained.pop('version',None)
            mechanisms[row['mechanism_ref']]=retained
            continue
        mechanisms[row['mechanism_ref']] = dict(mechanism_ref=row['mechanism_ref'],
            question=row['minimal_test'], label=row['label'], status='retired' if row['status']=='waived' else 'active',
            exploration=False, revision=0, source_step=row['source_step'], source_role=row['source_role'],
            source_ref=copy.deepcopy(row['source_ref']), original_ref=copy.deepcopy(row['source_ref']),
            predecessors=[], catalog_binding={key:copy.deepcopy(row[key]) for key in ('ordinal','origin_ref')},
            source_goal_id=row['source_goal_id'],origin_ref=copy.deepcopy(row['origin_ref']))
    for retained in config.get('worker_research_seed',[]):
        row=copy.deepcopy(retained);row.pop('experiment_question',None);row.pop('version',None)
        mechanisms[row['mechanism_ref']]=row
    revision=max((row['revision'] for row in mechanisms.values()),default=0)
    goal_id=_text(config.get('worker_research_goal_id'),'exact host-bound research goal identity')
    return dict(version=1, scope=copy.deepcopy(config['scope']),goal_id=goal_id,revision=revision, mechanisms=mechanisms)


def validate_state(value, scope):
    if (not isinstance(value, dict) or set(value) != {'version','scope','goal_id','revision','mechanisms'}
            or type(value['version']) is not int or value['version'] != 1 or value['scope'] != scope
            or type(value['revision']) is not int or value['revision'] < 0
            or not isinstance(value['mechanisms'], dict)):
        raise ValueError('Exact current W research facts required')
    _text(value['goal_id'],'exact declaration goal identity')
    for ref, row in value['mechanisms'].items():
        if (not isinstance(row, dict) or row.get('mechanism_ref') != ref
                or row.get('status') not in {'active','retired'}
                or type(row.get('revision')) is not int or not 0 <= row['revision'] <= value['revision']
                or type(row.get('exploration')) is not bool):
            raise ValueError('Worker research mechanism identity changed')
        _text(ref, 'stable mechanism reference');_text(row.get('question'), 'mechanism question')
    return value


def load(config):
    """Owner-only acquisition; consumers receive this already loaded value."""
    from . import contracts as c
    path=Path(config['record_dir'])/'research.json'
    try:path.lstat()
    except FileNotFoundError:return initial(config)
    pointer=c.read(c.regular(path))
    if (not isinstance(pointer,dict) or set(pointer)!={'version','scope','revision','original_ref'}
            or pointer['version']!=1 or pointer['scope']!=config['scope']
            or type(pointer['revision']) is not int or pointer['revision']<1):
        raise ValueError('Exact current W declaration pointer required')
    original=Path(config['record_dir'])/'research'/f'{pointer["revision"]:06d}.json'
    if pointer['original_ref']!=dict(path=str(original)):
        raise ValueError('Current W declaration pointer escaped its immutable original')
    value=validate_state(c.read(c.regular(original)),config['scope'])
    if value['revision']!=pointer['revision']:raise ValueError('Current W declaration revision changed')
    if value['goal_id']!=config.get('worker_research_goal_id'):raise ValueError('Current W declaration goal identity changed')
    return value


def request(value, *, batch=True):
    """Validate ordinary authored JSON without touching its original file."""
    if not isinstance(value,dict) or set(value)-{'declarations','mechanism_ref','experiment_question'}:
        raise ValueError('Worker research declaration or experiment batch required')
    declarations=value.get('declarations',[])
    if not isinstance(declarations,list):raise ValueError('Explicit worker declarations required')
    seen=set()
    for row in declarations:
        if (not isinstance(row,dict) or set(row)-{'mechanism_ref','action','question','exploration','predecessors'}
                or row.get('action') not in {'declare','revise','merge','retire','resume'}):
            raise ValueError('Explicit worker declaration action required')
        _text(row.get('mechanism_ref'),'stable mechanism reference')
        if row['mechanism_ref'] in seen:raise ValueError('Each batch declaration requires a distinct mechanism_ref; split dependent changes into separate declarations')
        seen.add(row['mechanism_ref'])
        if row['action'] in {'declare','revise','merge'}:_text(row.get('question'),'mechanism question')
        if 'exploration' in row and type(row['exploration']) is not bool:
            raise ValueError('Exploration state must be explicit boolean')
        predecessors=row.get('predecessors',[])
        if (not isinstance(predecessors,list)
                or any(not isinstance(ref,str) or not ref for ref in predecessors)
                or len(set(predecessors))!=len(predecessors)):
            raise ValueError('Exact declared predecessor identities required')
        if row['action']=='merge' and not predecessors:raise ValueError('Merge requires its exact predecessors')
    if batch:
        from .research_memory import experiment_question
        _text(value.get('mechanism_ref'),'experiment mechanism reference')
        experiment_question(value.get('experiment_question'))
    return copy.deepcopy(value)


def evolve(state, declared, *, scope, original_ref):
    """Apply only explicit identity operations; revisions never reset call usage."""
    validate_state(state,scope);request(dict(declarations=declared),batch=False)
    result=copy.deepcopy(state);changed=False
    for declaration in declared:
        ref=declaration['mechanism_ref'];action=declaration['action'];rows=result['mechanisms']
        prior=rows.get(ref)
        if action in {'declare','merge'} and prior is None:
            if re.fullmatch(r'w'+f'{scope["step"]:03d}'+r'\.[A-Za-z][A-Za-z0-9_-]*',ref) is None:
                raise ValueError('New mechanism escaped the current W identity')
        elif prior is None:raise ValueError('Mechanism revision is outside this task declared scope')
        predecessors=declaration.get('predecessors',(prior or {}).get('predecessors',[]))
        if any(parent not in rows or parent==ref for parent in predecessors):
            raise ValueError('Mechanism predecessor is outside this task declared scope')
        if action=='declare' and prior is not None:
            if (prior['question']!=declaration['question']
                    or prior['exploration']!=declaration.get('exploration',False)):
                raise ValueError('Existing identity requires explicit revise, merge, retire or resume')
            continue
        if action=='retire' or action=='resume':
            row=copy.deepcopy(prior);row['status']='retired' if action=='retire' else 'active'
        else:
            row=dict(prior or {},mechanism_ref=ref,question=declaration['question'],label=declaration['question'],
                status='active',exploration=declaration.get('exploration',False),predecessors=list(predecessors))
        comparable={key:item for key,item in row.items() if key not in {'revision','source_ref','original_ref','source_step','source_role'}}
        old={key:item for key,item in (prior or {}).items() if key not in {'revision','source_ref','original_ref','source_step','source_role'}}
        retire_parents=action=='merge' and any(rows[parent]['status']!='retired' for parent in predecessors)
        if comparable==old and not retire_parents:continue
        changed=True
        row.update(revision=state['revision']+1,source_step=scope['step'],source_role='W',
            source_ref=dict(path=original_ref['path']),original_ref=copy.deepcopy(original_ref),
            source_scope=copy.deepcopy(scope),source_goal_id=state['goal_id'])
        row.setdefault('origin_ref',dict(goal_id=state['goal_id'],source_step=scope['step'],
            source_role='W',mechanism_ref=ref))
        rows[ref]=row
        if action=='merge':
            for parent in predecessors:
                rows[parent]=dict(rows[parent],status='retired',revision=state['revision']+1,
                    source_step=scope['step'],source_role='W',source_ref=dict(path=original_ref['path']),
                    original_ref=copy.deepcopy(original_ref),source_scope=copy.deepcopy(scope),source_goal_id=state['goal_id'])
    if changed:result['revision']+=1
    return validate_state(result,scope)


def prepare(config,state,value):
    """Pure admission: bind the batch to the precise declared revision."""
    from .breadth import OpportunityRequired
    try:
        request(value)
        source=dict(path=str(Path(config['record_dir'])/'research'/f'{state["revision"]+1:06d}.json'))
        updated=evolve(state,value.get('declarations',[]),scope=config['scope'],original_ref=source)
        ref=value['mechanism_ref'];row=updated['mechanisms'].get(ref)
        if row is None or row['status']!='active':raise ValueError('Choose an active exact declared mechanism')
        snapshot=dict(version=1,**copy.deepcopy(row),experiment_question=copy.deepcopy(value['experiment_question']))
        return updated,dict(mechanism_ref=ref,source_step=row['source_step'],source_ref=copy.deepcopy(row['source_ref']),
            source_role=row['source_role'],job_ref=copy.deepcopy(config['scope']['job_ref']),grant=None,
            catalog_binding=None,research_binding=snapshot)
    except (ValueError,TypeError,KeyError) as error:
        raise OpportunityRequired(str(error),reason='worker_research_declaration_required') from error


def publish(config,previous,current):
    """Writer-only publication under the existing experiment admission lock."""
    from . import contracts as c
    if current==previous:return current
    validate_state(current,config['scope'])
    path=Path(config['record_dir'])/'research'/f'{current["revision"]:06d}.json'
    if path.exists():
        if c.read(c.regular(path))!=current:raise ValueError('Immutable W declaration revision changed')
    else:c.write(path,current)
    path.chmod(0o400)
    c.write(Path(config['record_dir'])/'research.json',dict(version=1,scope=current['scope'],
        revision=current['revision'],original_ref=dict(path=str(path))))
    return current


def check_binding(binding,scope):
    """Validate saved call facts, never compare to a later mutable declaration."""
    if (not isinstance(binding,dict) or set(binding)!={'mechanism_ref','source_step','source_ref','source_role','job_ref','grant','catalog_binding','research_binding'}
            or binding['job_ref']!=scope['job_ref'] or binding['grant'] is not None):
        raise ValueError('Exact worker research call binding required')
    row=binding['research_binding']
    if (not isinstance(row,dict) or row.get('version')!=1 or row.get('status')!='active'
            or any(row.get(key)!=binding[key] for key in ('mechanism_ref','source_step','source_ref','source_role'))
            or type(row.get('source_step')) is not int or not 0<row['source_step']<=scope['step']
            or row.get('source_role') not in {'H','W'} or type(row.get('exploration')) is not bool
            or type(row.get('revision')) is not int or row['revision']<0
            or not isinstance(binding['source_ref'],dict) or set(binding['source_ref'])!={'path'}
            or not isinstance(binding['source_ref']['path'],str) or not binding['source_ref']['path']
            or not isinstance(row.get('original_ref'),dict) or row['original_ref'].get('path')!=binding['source_ref'].get('path')
            or not isinstance(row.get('predecessors'),list)
            or any(not isinstance(ref,str) or not ref or ref==row['mechanism_ref'] for ref in row['predecessors'])):
        raise ValueError('Saved worker declaration identity changed')
    from .research_memory import experiment_question
    _text(row.get('question'),'saved mechanism question');experiment_question(row.get('experiment_question'))
    return binding


def load_original(binding,scope):
    """Recovery owner acquires the exact saved source, never a current pointer."""
    from . import contracts as c
    check_binding(binding,scope)
    path=c.regular(Path(binding['source_ref']['path']))
    row=binding['research_binding']
    runtime=path.parent.name=='research' and row['revision']>0
    fact=dict(reference=copy.deepcopy(binding['source_ref']),runtime=runtime,
        value=c.read(path) if runtime else None)
    check_original(binding,scope,fact)
    return fact


def check_original(binding,scope,fact):
    """Validate an already loaded original; missing inputs cannot grant recovery."""
    check_binding(binding,scope)
    if (not isinstance(fact,dict) or fact.get('reference')!=binding['source_ref']
            or type(fact.get('runtime')) is not bool):
        raise ValueError('Exact saved worker declaration original required')
    if fact['runtime']:
        value=fact.get('value')
        source_scope=value.get('scope') if isinstance(value,dict) else None
        if (not isinstance(source_scope,dict) or source_scope.get('step')!=binding['source_step']
                or source_scope.get('role')!='W'):
            raise ValueError('Saved declaration original escaped its author scope')
        validate_state(value,source_scope)
        row=copy.deepcopy(binding['research_binding']);row.pop('version',None);row.pop('experiment_question',None)
        if ((row.get('source_scope') is not None and row['source_scope']!=source_scope)
                or (row.get('source_goal_id') is not None and row['source_goal_id']!=value['goal_id'])):
            raise ValueError('Saved declaration original changed its exact source owner')
        if (value['revision']!=row['revision'] or value['mechanisms'].get(binding['mechanism_ref'])!=row):
            raise ValueError('Saved declaration original changed its frozen revision')
    return fact


def public_text(value, limit):
    """Path-free bounded display text; exact originals remain unchanged."""
    if not isinstance(value,str):return None
    value=re.sub(r'''(?<![A-Za-z0-9_/])(?:/|[A-Za-z]:[\\/]|\\\\)[^\s<>"']+''', '[路径已省略]',value)
    value=re.sub(r'(?i)\b(?:token|api[_-]?key|authorization|password|secret)\s*[:=]\s*\S+', '[credential]',value)
    value=' '.join(value.split())
    try:raw=value.encode('utf-8')
    except UnicodeEncodeError:return None
    return (raw[:limit-3].decode('utf-8',errors='ignore')+'...' if len(raw)>limit else value) or None


def public_mechanism(row):
    """Public authored snapshot with stable identity and source stage only."""
    if not isinstance(row,dict):return None
    ref=row.get('mechanism_ref')
    if not isinstance(ref,str) or public_text(ref,128)!=ref:return None
    revision=row.get('revision')
    if type(revision) is not int or revision<0:return None
    return dict(mechanism_ref=ref,label=public_text(row.get('label') or row.get('question'),160),
        question=public_text(row.get('question'),240),
        question_truncated=row.get('question_truncated') is True or (isinstance(row.get('question'),str)
            and len(row['question'].encode('utf-8',errors='replace'))>240),status=row.get('status'),
        exploration=row.get('exploration') is True,revision=revision,
        source_step=row.get('source_step') if type(row.get('source_step')) is int else None,
        source_role=row.get('source_role') if row.get('source_role') in {'H','W'} else None)


def project(state):
    """Compact public navigation; originals retain full scientific statements."""
    rows=list(state['mechanisms'].values())
    shown=[value for row in rows[-16:] if (value:=public_mechanism(row)) is not None]
    return dict(version=1,revision=state['revision'],mechanisms=shown,
        omitted_count=max(0,len(rows)-len(shown)))


def call_fact(accepted,scope):
    """Extract one original investment from already loaded accepted metadata."""
    if (not isinstance(accepted,dict) or accepted.get('scope')!=scope
            or not isinstance(accepted.get('call_id'),str) or not accepted['call_id']):
        raise ValueError('Exact original W investment required')
    binding=check_binding(accepted.get('mechanism_binding'),scope)
    return dict(original_call_id=json.dumps(scope['job_ref'],sort_keys=True,separators=(',',':'))+':'+accepted['call_id'],
        mechanism_ref=binding['mechanism_ref'],predecessors=list(binding['research_binding'].get('predecessors',[])))


def _ancestry(ref,lineage):
    related=set();pending=[ref]
    while pending:
        current=pending.pop()
        if current in related:continue
        related.add(current);pending.extend(lineage.get(current,[]))
    return related


def investment(scope,calls,*,research_state=None):
    """A compact projection of exact admitted originals, never native tool traffic."""
    lineage={ref:list(row.get('predecessors',[])) for ref,row in (research_state or {}).get('mechanisms',{}).items()}
    facts=[call_fact(accepted,scope) for accepted in calls]
    for fact in facts:
        ref=fact['mechanism_ref'];lineage[ref]=sorted(set(lineage.get(ref,[]))|set(fact['predecessors']))
    originals={};counts={};direction=None;consecutive=0;predecessors=[]
    for fact in facts:
        original=fact['original_call_id'];ref=fact['mechanism_ref']
        if original in originals:
            if originals[original]!=ref:raise ValueError('Original investment changed its identity')
            continue
        originals[original]=ref;counts[ref]=counts.get(ref,0)+1
        related=_ancestry(ref,lineage)
        consecutive=consecutive+1 if direction in related else 1
        direction=ref;predecessors=sorted(related-{ref})
    return dict(version=1,scope=copy.deepcopy(scope),total_calls=len(originals),by_mechanism=counts,
        direction=direction,consecutive_calls=consecutive,predecessors=predecessors,
        last_original_call_id=next(reversed(originals),None))


def load_investment(config):
    """Native observation owner reads one optional projection, never call trees."""
    from . import contracts as c
    path=Path(config['record_dir'])/'live-status.json'
    try:path.lstat()
    except FileNotFoundError:return None
    value=c.read(c.regular(path,limit=65536))
    if not isinstance(value,dict) or value.get('scope')!=config['scope']:
        raise ValueError('Live investment view escaped its exact task')
    result=value.get('research_investment')
    if result is None:return None
    if (not isinstance(result,dict) or result.get('version')!=1 or result.get('scope')!=config['scope']
            or type(result.get('total_calls')) is not int or result['total_calls']<0
            or type(result.get('consecutive_calls')) is not int
            or not 0<=result['consecutive_calls']<=result['total_calls']
            or not isinstance(result.get('by_mechanism'),dict)
            or any(not isinstance(ref,str) or type(count) is not int or count<0 for ref,count in result['by_mechanism'].items())
            or sum(result['by_mechanism'].values())!=result['total_calls']):
        raise ValueError('Exact admitted investment projection required')
    return result
