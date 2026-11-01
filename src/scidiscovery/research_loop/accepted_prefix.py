"""Branch-owned accepted scientific navigation, advanced by one sealed node.

The descriptor is a fixed reference, not a serialized prefix. Membership is a
point lookup; historical science is opened only when its exact key is requested.
Public files contain science and logical navigation, never private proof rows.
"""
from pathlib import Path
import os
import re
from urllib.parse import quote

VERSION = 1
PUBLIC = 'host-history/accepted-public'
MEMBERS = 'host-history/accepted-members'
PENDING = 'host-history/accepted-pending.json'


def empty(root, settings):
    return dict(version=VERSION,branch=dict(goal_id=settings['goal_id'],
        settings_ref=dict(path=str(Path(root).absolute()/'settings.json'))),
        tip=None,visibility_root=dict(path=PUBLIC+'/'+settings['goal_id']),cutoff=0,count=0)


def _view_key(prefix):
    path=Path(_relative(prefix['visibility_root']['path']))
    if path.parent.as_posix()!=PUBLIC or not re.fullmatch(r'[A-Za-z0-9._-]+',path.name):
        raise ValueError('Exact branch-owned public namespace required')
    return path.name


def _relative(value):
    from . import originals
    return originals.relative(value)


def visibility(root, prefix):
    if (prefix.get('version')!=VERSION
            or type(prefix.get('count')) is not int or prefix['count']<0
            or type(prefix.get('cutoff')) is not int or prefix['cutoff']<0):
        raise ValueError('Exact accepted scientific prefix required')
    path=Path(root)/PUBLIC/_view_key(prefix)
    if path.is_symlink() or path.resolve()!=path.absolute() or not path.is_dir():
        raise ValueError('Accepted public visibility root is unavailable')
    return path


def initialize(root, *, search_seed, identity_root=None):
    from . import contracts as c
    prefix=empty(root,c.settings(root))
    path=Path(root)/prefix['visibility_root']['path']
    path.mkdir(parents=True,exist_ok=True)
    (Path(root)/MEMBERS/_view_key(prefix)/'logical').mkdir(parents=True,exist_ok=True)
    snapshot=Path(root)/'host-history/breadth-initial.json'
    _write_once(snapshot,search_seed)
    _publish_breadth(path,search_seed)
    origin=dict(goal_id=c.settings(root)['goal_id'],settings_ref=dict(path=str(
        Path(identity_root if identity_root is not None else root).absolute()/'settings.json')))
    return dict(origin_identity=origin,path='host-history/breadth-initial.json',size_bytes=snapshot.stat().st_size)


def _member_path(root, prefix, origin, step):
    from . import originals
    if type(step) is not int or step<=0:raise ValueError('Exact scientific member step required')
    return Path(root)/MEMBERS/_view_key(prefix)/originals.identity_key(origin)/f'{step:03d}.json'


def member(root, prefix, step, *, origin=None):
    """Read one already accepted membership record, including its branch cut."""
    from . import contracts as c
    visibility(root,prefix)
    if origin is None:
        binding_path=Path(root)/MEMBERS/_view_key(prefix)/'logical'/f'{step:03d}.json'
        if not binding_path.exists() and not binding_path.is_symlink():
            raise FileNotFoundError('Scientific member is absent from this branch')
        binding=c.read(c.regular(binding_path))
        origin=binding['origin_identity']
    member_path=_member_path(root,prefix,origin,step)
    if not member_path.exists() and not member_path.is_symlink():
        raise FileNotFoundError('Scientific member is absent from this branch')
    value=c.read(c.regular(member_path))
    if (value.get('branch')!=prefix['branch'] or value.get('origin_identity')!=origin
            or value.get('step')!=step or not 0<value.get('ordinal',0)<=prefix['count']
            or step>prefix['cutoff']):
        raise ValueError('Scientific member is outside its accepted branch prefix')
    return value


def current(root):
    from . import accepted_budget
    return accepted_budget.read(root)['prefix']


def for_job(job):
    prefix=job.get('history',{}).get('prefix')
    if not isinstance(prefix,dict) or prefix.get('version')!=VERSION:
        raise ValueError('Scientific job has no current accepted prefix descriptor')
    return prefix


def navigation(root, prefix, step=None, *, origin=None):
    from . import contracts as c
    if step is None:
        if prefix['tip'] is None:return None
        ref=prefix['tip']['navigation']
    else:
        ref=member(root,prefix,step,origin=origin)['navigation']
    return c.read(c.regular(visibility(root,prefix)/_relative(ref['path'])))


def _key_file(base, value):
    encoded='key-'+quote(value,safe='')
    return Path(base).joinpath(*[encoded[index:index+100] for index in range(0,len(encoded),100)])/'value.json'


def candidate(root, prefix, identity):
    """Read one exact accepted candidate and its saved material binding."""
    from . import accepted_budget,accepted_search,contracts as c
    alias=_key_file(Path(root)/MEMBERS/_view_key(prefix)/'candidate-aliases',identity)
    if alias.exists():identity=c.read(c.regular(alias))['candidate_id']
    row=accepted_budget.search_row(root,accepted_search.key('candidate',identity))
    if row is None:raise ValueError('Candidate reference is outside this frozen scientific task')
    member(root,prefix,row['first_step'],origin=row.get('origin_identity'))
    return row


def candidate_for_implementation(root,prefix,identity):
    from . import contracts as c
    path=_key_file(Path(root)/MEMBERS/_view_key(prefix)/'implementation-candidates',identity)
    if not path.exists():return None
    value=c.read(c.regular(path))
    return value['candidate_id'] if value['count']==1 else None


def _material_binding(root, job, seal, observation, candidate_value):
    from . import contracts as c,generic_results,history
    step=job['step'];owner=job;owner_seal=seal
    implementation=candidate_value.get('implementation')
    if implementation is not None:
        match=re.fullmatch(r'history/nodes/([0-9]{3,})/artifacts/(.+)',implementation)
        if match is None:return None
        owner_step=int(match[1]);path=f'inputs/parents/{owner_step:03d}/artifacts/{match[2]}'
        if owner_step!=step:owner,_,owner_seal,_=history._source(root,owner_step,resolve_choices=False)
    else:
        declarations=[(call,item) for call in observation.get('managed_calls',[])
            for item in generic_results.declarations(call) if isinstance(item,dict)
            and isinstance(item.get('artifact'),str) and generic_results.identity(call,item)==candidate_value['id']]
        if len(declarations)!=1:return None
        call,item=declarations[0]
        outputs=[output for output in call.get('outputs',[]) if 'artifacts/'+output['name']==item['artifact']]
        if len(outputs)!=1:return None
        path=f'inputs/parents/{step:03d}/artifacts/experiments/{call["call_id"]}/artifacts/{outputs[0]["name"]}'
    ordered=sorted(owner_seal['materials'],key=lambda item:item['path'])
    matches=[(index,item) for index,item in enumerate(ordered,1) if item['path']==path]
    if len(matches)!=1:return None
    index,item=matches[0]
    return dict(material={key:value for key,value in item.items() if key!='file_stat'},
        implementation_id=f'{owner["role"]}{owner["step"]:03d}-I{index:03d}')


def _write_once(path, value):
    from . import contracts as c
    if path.exists() or path.is_symlink():
        if c.read(c.regular(path))!=value:raise ValueError('Accepted public navigation changed')
    else:
        c.write(path,value);path.chmod(0o400)


def _public_entity(kind,row):
    fields={'mechanism':('mechanism_ref','ordinal','origin_ref','label','minimal_test','status',
        'source_h','original','planned_step','accepted_calls','remaining','next_remaining','participated_cycles'),
        'work':('work_ref','status','mechanism_refs','reason','source_a','selected_step','execution_step'),
        'candidate':('public_reference','first_step')}
    value={key:row[key] for key in fields[kind] if key in row}
    if kind=='candidate':
        value['observation']=dict(row['observation'],id=row.get('public_reference',row['observation']['id']))
    value['scope']='Accepted public navigation only; read original authored science for conclusions and limitations.'
    return value


def fork_seed(root,settings,*,search_seed,identity_root):
    """Freeze the selected task's declared public cut once, at child birth.

    The breadth table is already the selected input snapshot. Only previously
    accepted metadata from that same manifest's scientific events is copied;
    this does not interpret or replay the parent's scientific conclusions.
    """
    from . import contracts as c,originals,candidate_refs,accepted_search
    import copy
    root=Path(root)
    manifest=originals._manifest(root)
    frozen=manifest['cut']['history']['prefix']
    prefix=empty(identity_root,settings)
    prefix.update(tip=frozen['tip'],cutoff=frozen['cutoff'],count=frozen['count'])
    rows={}
    (root/prefix['visibility_root']['path']).mkdir(parents=True,exist_ok=True)
    public=visibility(root,prefix)
    source=originals.selected_source(root)
    for entry in manifest['prefix_entries']:
        owner=originals.for_origin(root,entry['origin_identity'],step=entry['step'])
        member_ref=entry['member_ref']
        member_owner=originals.source_for_ref(source,member_ref)
        old=c.read(originals.resolve_ref(member_owner,member_ref))
        event_ref=entry['scientific_event']
        event_owner=originals.source_for_ref(source,event_ref)
        event=c.read(originals.resolve_ref(event_owner,event_ref))
        if event.get('kind')!='scientific_seal' or event.get('step')!=entry['step']:
            raise ValueError('Fork member lost its exact accepted scientific event')
        for name,change in event['index_updates'].items():
            if name.startswith('search/'):
                rows[name]=change['current']
        nav_ref=entry['navigation_ref']
        nav_owner=originals.source_for_ref(source,nav_ref)
        nav=c.read(originals.resolve_ref(nav_owner,nav_ref))
        directory=public/Path(old['navigation']['path']).parent
        directory.mkdir(parents=True,exist_ok=True)
        seal=c.read(originals.source_record(owner,'result-identity.json'))
        for name,reference in (('science.json',seal['result_ref']),('scientific-original.json',seal['raw_ref'])):
            original=c.regular(originals.resolve_ref(owner,reference))
            target=directory/name
            if target.exists():
                if not target.samefile(original):raise ValueError('Fork scientific alias changed its original')
            else:os.link(original,target,follow_symlinks=False)
        _write_once(directory/'index.json',nav)
        value=dict(old,branch=prefix['branch'],storage_root=str(Path(identity_root)/originals.FORK_DIRECTORY),
            scientific_event=event_ref)
        _write_once(_member_path(root,prefix,value['origin_identity'],value['step']),value)
        _write_once(root/MEMBERS/_view_key(prefix)/'logical'/f'{value["step"]:03d}.json',
            dict(origin_identity=value['origin_identity']))
        _write_once(root/MEMBERS/_view_key(prefix)/'ordinals'/f'{value["ordinal"]:09d}.json',
            dict(origin_identity=value['origin_identity'],step=value['step']))
    mapping={}
    for name,row in rows.items():
        if name.startswith('search/candidate/'):
            raw=row['observation']['id']
            mapping[raw]=candidate_refs.origin_candidate(row['origin_identity'],raw)
    inherited={}
    for name,row in rows.items():
        row=copy.deepcopy(row)
        if name.startswith('search/candidate/'):
            canonical=mapping[row['observation']['id']]
            row['observation']['id']=canonical
            if row.get('parent') is not None:row['parent']=mapping.get(row['parent'],row['parent'])
            name=accepted_search.key('candidate',canonical)
            public=row.get('public_reference',canonical)
            _write_once(_key_file(root/MEMBERS/_view_key(prefix)/'candidate-aliases',public),dict(candidate_id=canonical))
        parts=name.split('/',2)
        if len(parts)==3 and parts[1] in {'candidate','work'}:
            public=row.get('public_reference',parts[2]) if parts[1]=='candidate' else parts[2]
            _write_once(_key_file(visibility(root,prefix)/'entities'/parts[1],public),
                _public_entity(parts[1],row))
        inherited[name]=row
    search_seed=copy.deepcopy(search_seed)
    for field in ('current_candidate','a_recommended','historical_a_recommendation','actual_submission'):
        item=search_seed.get(field)
        if isinstance(item,dict) and item.get('candidate_id') is not None:
            item['candidate_id']=mapping.get(item['candidate_id'],item['candidate_id'])
    return prefix,inherited,search_seed


def _publish_breadth(directory,summary,*,candidate_references=None):
    from . import contracts as c,candidate_nominations
    table=summary['table']
    rows=[]
    for ref in table['ordinals']:
        row=table['mechanisms'][ref]
        from . import originals
        source_key=originals.identity_key(row['source_origin_identity'])
        rows.append(_public_entity('mechanism',dict(row,mechanism_ref=ref,
            original=f'inputs/history/nodes/{source_key}/{row["source_h"]:03d}/scientific-original.json')))
    references={} if candidate_references is None else candidate_references
    c.write(Path(directory)/'breadth-table.json',dict(version=1,mechanisms=rows,
        nominations=candidate_nominations.source_table_projection(summary,
            lambda identity:references.get(identity,identity))))


def prepare_node(root, job, payload, seal, *, inventory):
    """Prepare this node's mechanical publication before its acceptance marker."""
    from . import accepted_budget, accepted_search, contracts as c, originals,history,candidate_refs
    root=Path(root)
    state=accepted_budget.read(root)
    prefix=state['prefix']
    slot=state.get('current_job')
    if (not isinstance(slot,dict) or slot.get('step')!=job['step']
            or slot.get('status')!='terminal'
            or slot.get('job_ref')!=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json')):
        raise ValueError('Public publication requires its exact admitted scientific owner')
    if for_job(job)!=prefix:
        raise ValueError('Scientific output lost its admitted accepted prefix')
    origin=prefix['branch']
    key=originals.identity_key(origin)
    base=f'nodes/{key}/{job["step"]:03d}'
    prior=navigation(root,prefix)
    recent=([*prior.get('recent',[])] if prior else [])[-1:]
    item=dict(step=job['step'],role=job['role'],summary=payload['summary'][:512],
        limitations=payload.get('limitations',[])[:4],original=f'inputs/history/{base}/science.json')
    recent.append(item)
    observation=inventory.observation_document if seal.get('observations') else None
    search_delta=accepted_search.prepare_node(root,job,payload,seal,summary=state['search'],
        lookup=lambda name:accepted_budget.search_row(root,name,head=state),
        usage_lookup=lambda number:accepted_budget.native_usage_row(root,number,head=state),
        observation=observation)
    snapshot=root/'native-evidence'/f'{job["step"]:03d}'/'breadth-snapshot.json'
    _write_once(snapshot,search_delta['summary'])
    snapshot_ref=dict(origin_identity=origin,path=snapshot.relative_to(root).as_posix(),size_bytes=snapshot.stat().st_size)
    seal['breadth_snapshot']=snapshot_ref
    aliases={};implementation_candidates={}
    if observation is not None:
        references=candidate_refs.references([dict(step=job['step'],role=job['role'],observation=observation)])
        for value in observation['candidates']:
            candidate_key=accepted_search.key('candidate',value['id'])
            row=search_delta['rows'].get(candidate_key)
            if row is None:continue
            public=references.get(value['id'],value['id'])
            row.setdefault('public_reference',public)
            row.setdefault('origin_identity',origin)
            binding=_material_binding(root,job,seal,observation,value)
            if binding is not None:
                row.setdefault('binding',binding)
                identity=binding['implementation_id']
                if row['first_step']==job['step']:
                    if identity not in implementation_candidates:
                        saved=_key_file(root/MEMBERS/_view_key(prefix)/'implementation-candidates',identity)
                        implementation_candidates[identity]=(c.read(c.regular(saved)) if saved.exists() else dict(count=0,candidate_id=None))
                    item=implementation_candidates[identity]
                    item['count']+=1
                    item['candidate_id']=value['id'] if item['count']==1 else None
            aliases[public]=value['id']
    node=dict(version=VERSION,step=job['step'],role=job['role'],science='science.json',scientific_original='scientific-original.json',
        recent=recent,node_count=prefix['count']+1,
        previous=prefix['tip']['navigation']['path'] if prefix['tip'] is not None else None,
        implementations=[dict(id=f'{job["role"]}{job["step"]:03d}-I{index:03d}',
            label=Path(row['path']).name) for index,row in enumerate(sorted(seal['materials'],key=lambda row:row['path']),1)],
        scope='Accepted authored science and exact implementation locators; no inherited qualification.')
    node['entities']=[]
    for name,row in search_delta['rows'].items():
        parts=name.split('/',2)
        if len(parts)!=3 or parts[1] not in {'mechanism','candidate','work'}:continue
        public=row.get('public_reference',parts[2])
        page=_key_file(Path('entities')/parts[1],public).as_posix()
        node['entities'].append(dict(kind=parts[1],reference=public,page='inputs/history/'+page))
    nav_ref=dict(path=base+'/index.json')
    # Size is the normal JSON publication size, not a new digest or authority.
    import json
    nav_ref['size_bytes']=len((json.dumps(node,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode())
    tip=dict(origin_identity=origin,step=job['step'],
        seal_ref=dict(path=str(root/'native-evidence'/f'{job["step"]:03d}'/'result-identity.json')),navigation=nav_ref)
    updated=dict(prefix,tip=tip,cutoff=max(prefix['cutoff'],job['step']),count=prefix['count']+1)
    membership=dict(branch=prefix['branch'],origin_identity=origin,step=job['step'],
        ordinal=updated['count'],original_root=str(root),storage_root=str(root),
        seal_ref=tip['seal_ref'],navigation=nav_ref)
    pending=dict(version=VERSION,step=job['step'],job_ref=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json'),
        previous=prefix,prefix=updated,node=node,member=membership,search_delta=search_delta,
        result_ref=seal['result_ref'],search_ref=snapshot_ref,
        candidate_aliases=aliases,implementation_candidates=implementation_candidates)
    c.write(root/PENDING,pending)
    return pending


def commit_node(root, pending):
    """Commit only the prepared current delta after the durable science marker."""
    from . import contracts as c, accepted_budget
    root=Path(root);step=pending['step']
    state=accepted_budget.read(root)
    if (state['prefix'] not in (pending['previous'],pending['prefix'])
            or state.get('current_job',{}).get('step')!=step):
        raise ValueError('Accepted public publication lost its active view owner')
    marker=c.regular(root/'native-evidence'/f'{step:03d}'/'result-identity.json')
    seal=c.read(marker)
    if seal['result_ref']!=pending['result_ref']:
        raise ValueError('Pending public node differs from its accepted scientific marker')
    prefix=pending['prefix'];member_value=pending['member']
    directory=visibility(root,prefix)/Path(prefix['tip']['navigation']['path']).parent
    directory.mkdir(parents=True,exist_ok=True)
    result=c.regular(Path(seal['result_ref']['path']))
    result.chmod(0o400)
    science=directory/'science.json'
    if science.exists():
        if not science.samefile(result):raise ValueError('Accepted public science alias changed')
    else:os.link(result,science,follow_symlinks=False)
    original=c.regular(Path(seal['raw_ref']['path']))
    raw_alias=directory/'scientific-original.json'
    if raw_alias.exists():
        if not raw_alias.samefile(original):raise ValueError('Accepted public scientific original changed')
    else:os.link(original,raw_alias,follow_symlinks=False)
    _write_once(directory/'index.json',pending['node'])
    for public,canonical in pending['candidate_aliases'].items():
        _write_once(_key_file(root/MEMBERS/_view_key(prefix)/'candidate-aliases',public),dict(candidate_id=canonical))
    for identity,value in pending['implementation_candidates'].items():
        c.write(_key_file(root/MEMBERS/_view_key(prefix)/'implementation-candidates',identity),value)
    for key,row in pending['search_delta']['rows'].items():
        parts=key.split('/',2)
        if len(parts)!=3 or parts[1] not in {'candidate','work'}:continue
        public=(row.get('public_reference') if parts[1]=='candidate' else None) or parts[2]
        value=_public_entity(parts[1],row)
        c.write(_key_file(visibility(root,prefix)/'entities'/parts[1],public),value)
    references={row['observation']['id']:row['public_reference']
        for name,row in pending['search_delta']['rows'].items()
        if name.startswith('search/candidate/') and row.get('public_reference') is not None}
    _publish_breadth(visibility(root,prefix),pending['search_delta']['summary'],candidate_references=references)
    committed=accepted_budget.publish_scientific_seal(root,step,prefix=prefix,role=pending['node']['role'],
        result_ref=seal['result_ref'],search_delta=pending['search_delta'],search_ref=pending['search_ref'])
    accepted=committed.row(f'job/{step:03d}')
    member_value=dict(member_value,scientific_event=accepted['output_event_ref'])
    _write_once(_member_path(root,prefix,member_value['origin_identity'],step),member_value)
    _write_once(root/MEMBERS/_view_key(prefix)/'logical'/f'{step:03d}.json',dict(origin_identity=member_value['origin_identity']))
    _write_once(root/MEMBERS/_view_key(prefix)/'ordinals'/f'{member_value["ordinal"]:09d}.json',
        dict(origin_identity=member_value['origin_identity'],step=step))
    from . import history
    history.refresh(root,step+1)
    (root/PENDING).unlink(missing_ok=True)
    return prefix


def reconcile(root):
    """A crash can leave one prepared current node, never an undiscovered prefix."""
    from . import contracts as c
    path=Path(root)/PENDING
    if not path.exists():return
    value=c.read(c.regular(path))
    marker=Path(root)/'native-evidence'/f'{value["step"]:03d}'/'result-identity.json'
    if marker.exists():commit_node(root,value)


def input_rows(job):
    return job.get('history',{}).get('inputs',[])
