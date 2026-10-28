"""Thin scientific packets and exact direct-node seals; no MCP dispatch protocol.

Only the controller supplies identities and lineage. Seals bind original paths,
producers and declared sizes, not content fingerprints or scientific validity.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from urllib.parse import unquote, urlsplit

MAX_RESULT_BYTES = 64_000
MAX_REUSED_INPUTS = 16
MAX_UNRESOLVED_LINKS = 16
REUSE_FRAGMENT = 'reuse-implementation'
from .protocol import CURRENT
from .instance_owner import effect as owner_effect
from . import atomic_files
HANDOFF_PROTOCOL = CURRENT
AUDIT_PROJECTION_VERSION = 1
ACTIONS = {
    'H': ('experiment', 'analyze', 'extract', 'wait'),
    'A': ('continue_w', 'revise_h', 'extract', 'wait', 'finish', 'deliver'),
}
MAX_FIXED_RESPONSE_BYTES = 3072
AUTHOR_EVIDENCE_GUIDANCE = (
    'Cite evidence/conditions; separate measurement/inference/proxy. State uncertainty/gaps/unmet goals and '
    'criteria changes/tradeoffs. Waive only supported negatives/infeasibility; else defer.')
NATIVE_TASK_INSTRUCTION = (
    'Read science-task.json/exact inputs. Honor goal/checks, scope/permissions and budget. '
    'Follow task rules; return JSON, no hashes/manifests/validators.')
PORTFOLIO_RESPONSE = (
    'After completed W except wait: portfolio_review={main_line:ref|null,reason,discriminating_work:null|{mechanism_refs,question,next_action}}. '
    'reason=strongest rival/cheapest test/decision/defer-revisit or infeasibility; next_action=continue_w|revise_h|extract. '
    'All active lineage.indexes.portfolio refs need nested work_updates[{work_ref,action:execute|defer|withdraw,reason}].')
FINAL_COVERAGE_RESPONSE = (
    'finish/deliver/FINAL: cover all refs in final_coverage[{mechanism_ref,status:deep|screened_pending|ruled_out|unexplored,reason}]. '
    'reason=depth/evidence/open questions; limitations=input/resource gaps.')


ROLE_INSTRUCTIONS = {
    'H': 'Own the scientific direction toward the original global goal. Propose or revise mechanisms, '
         'distinguishing hypotheses from implementation choices. Decide whether evidence extraction is needed, '
         'then choose experiment or independent analysis. Preserve original constraints and unmet questions.',
    'W': 'Own methods/candidates within H; A suggests methods. Honor checks; use --mechanism. Initial/grant calls protect screening opportunities, not trial totals. Route scope changes via A to H; reconcile unknown calls, never replay. Return to independent A, not global completion. No auxiliary agents.',
    'A': "A owns goal/evaluation-version judgment and counterevidence. No incumbent/unknown-optimality veto. continue_w=one finite stage; calls/audits aren't goal proof.",
    'E': 'Answer the exact evidence question from the requesting H or A. Retrieve or extract only needed materials '
         'using available functional tools. Preserve original sources, locators, conflicts and missing evidence. '
         'Return to the requester; do not choose the research direction or assert qualification. '
         'Mechanical candidate copying, packaging, path resolution and submission belong to the delivery adapter.',
}


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def decode_scientific_output(text: str):
    """Parse one authored object, allowing only an outer BOM or whole JSON fence."""
    text = text.strip()
    normalization = []
    if text.startswith('\ufeff'):
        text = text[1:].strip()
        normalization.append('bom')
    fenced = re.fullmatch(r'```(?:json)?\r?\n(.*?)\r?\n```', text, re.DOTALL)
    if fenced is not None:
        text = fenced[1]
        normalization.append('fenced')
    value = json.loads(text)
    if not isinstance(value, dict):
        raise json.JSONDecodeError('Scientific output must be one JSON object', text, 0)
    return value, '+'.join(normalization) or None


def read_scientific_output(path: Path):
    """Read syntax only; sealed conclusions retain their accepted interpretation."""
    return decode_scientific_output(path.read_text(encoding='utf-8'))[0]


def write(path: Path, value):
    payload = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    atomic_files.replace_bytes(path, payload)


_record_writers = ContextVar('scid_record_writers', default={})


@contextmanager
def record_writer(record_root, *, blocking=True, exclusive=False):
    """Cooperative managed-record write lease; lifecycle readers take exclusive.

    Callers first hold the instance effect gate. The shared lease covers the
    existing node/admission and adapter locks; snapshots use exclusive=True.
    """
    from scidiscovery import archive_io as files
    root = files.check_path(Path(record_root).absolute())
    key = str(root)
    held = _record_writers.get()
    if key in held:
        if exclusive and not held[key]:
            raise ValueError('A shared managed record lease cannot be upgraded')
        yield
        return
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'record-writer.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Managed record lease is not a regular file')
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(descriptor, mode | (0 if blocking else fcntl.LOCK_NB))
        token = _record_writers.set(dict(held, **{key: exclusive}))
        try:
            yield
        finally:
            _record_writers.reset(token)
    finally:
        os.close(descriptor)


def record_path(path):
    path = Path(path).absolute()
    if path.resolve() != path or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Exact original path without links required')
    regular(path)
    return str(path)


def record_ref(path):
    """Reference one exact control record; never read scientific payloads."""
    return dict(path=record_path(path))


def file_ref(path):
    """Locate a declared original; this is not a content fingerprint."""
    path = Path(record_path(path))
    return dict(path=str(path), size_bytes=path.stat().st_size)


def settings(root: Path, job=None):
    root = Path(root)
    if job is not None:
        from . import originals
        if (not job.get('fork_source') and type(job.get('step')) is int and job['step'] > 0
                and originals.inherited(root, job['step'])):
            return originals.source_settings(originals.node(root, job['step']))
        from . import configuration_updates
        return configuration_updates.for_job(root, job)
    path = regular(root / 'settings.json')
    identity = read(regular(root / 'settings-identity.json'))
    # Parse the exact frozen control locator. No content fingerprint is computed.
    data = path.read_bytes()
    if {key:value for key,value in identity.items() if key!='cold_owner'} != dict(path='settings.json'):
        raise ValueError('Frozen instance settings changed')
    return json.loads(data)


def workspace(root: Path, step: int):
    return root / 'project/nodes' / f'{step:03d}'


def host_record(work: Path):
    return work.parents[2] / 'native-evidence' / work.name


def regular(path: Path, *, limit=None):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected an original regular file')
    if limit is not None and path.stat().st_size > limit:
        raise ValueError('File exceeds its declared byte budget')
    return path


def output_quota_exceeded(policy, sizes):
    """Only an explicitly frozen storage quota can restrict complete materials."""
    item_limit,total_limit=policy.get('max_output_item_bytes'),policy.get('max_output_total_bytes')
    total=0
    for size in sizes:
        if type(size) is not int or size<0:raise ValueError('Original material size must be a nonnegative integer')
        if item_limit is not None and size>item_limit:return True
        total+=size
    return total_limit is not None and total>total_limit


def actions(role,protocol=CURRENT,*,audit_enabled=True,review_enabled=False):
    from .protocol import _current
    _current(protocol,'Scientific result schema')
    choices = (*ACTIONS[role],'audit') if role=='A' and audit_enabled else ACTIONS.get(role,())
    return (*choices, 'formal_review') if role=='A' and review_enabled else choices


def response_options(job):
    return dict(formal_review=bool(job.get('formal_review')), review_enabled=job.get('formal_review_version')==1 and job.get('formal_review_choices',{}).get('launcher_supported',True),
        final_only=job.get('final_only',False), final_delivery=bool((job.get('delivery_sources') or {}).get('items')),
        auxiliary_fields=job.get('scientific_response_fields'))


def response_fields(job, policy):
    """Actual optional consumers of this task, frozen with its existing job."""
    fields=[];ordinary=job.get('purpose') is None and not job.get('final_report')
    if job.get('search') is not None and job['role'] in {'H','A'}:
        fields.append('search_plan' if job['role']=='H' else 'search_review')
    if job['role']=='A' and policy.get('search_policy') is not None and not job.get('formal_review'):
        fields.append('portfolio_review')
        if job.get('final_coverage_mechanisms'):fields.append('final_coverage')
    if job['role']=='A' and ordinary:
        if policy.get('correction_policy') is not None:fields.append('progress_assessment')
        if policy.get('decision_contract_version')==1:fields.append('decision_request')
    if job['role']=='W':
        from . import worker_delivery
        if worker_delivery.applicable(job):fields.append('mechanism_findings')
        if job.get('candidate_nomination_mechanisms'):fields.append('candidate_nominations')
    return fields


def result_schema(role: str,protocol=CURRENT,comparison=False,*,formal_review=False,review_enabled=False,final_only=False,final_delivery=False,auxiliary_fields=None):
    """Required transport fields only; authored scientific content stays open."""
    from .protocol import _current
    _current(protocol,'Scientific result schema')
    if role not in ROLE_INSTRUCTIONS:raise ValueError('Unknown scientific role')
    if formal_review:
        from . import formal_review as review
        return review.schema()
    properties=dict(summary=dict(type='string'),scientific_result=dict(type='string'),
        limitations=dict(type='array',items=dict(type='string')))
    if role in ACTIONS:
        choices=list(actions(role,review_enabled=review_enabled))
        if final_only:choices=['deliver'] if final_delivery else ['finish']
        properties.update(next_action=dict(type='string',enum=choices),route_reason=dict(type='string'))
    if role=='W':properties['stage_intent']=dict(type='string',enum=['continue','review','decision'])
    return dict(type='object',properties=properties,required=list(properties),additionalProperties=True)


def response_instructions(job):
    """The actual fixed output instructions shown in the scientific task."""
    fields=dict(final_response=list(result_schema(job['role'],job['handoff_protocol'],
        'selection' in job,**response_options(job))['properties']),
        implementation_reuse='implementation_selection: null keeps; [] clears; IDs replace.')
    if job.get('search') is not None and job['role'] in {'H','A'}:
        fields['breadth_response']=(
            'search_plan: commitments[{label,minimal_test,existing_ref?}], focus?{label,action}; action=screen|improve|repair|validate; omissions retain.'
            if job['role']=='H' else
            'search_review: dispositions[{mechanism_ref,status,reason}],next_focus?{mechanism_ref,action}; status=pending|screened|deferred|waived; action=screen|improve|repair|validate; omitted=keep.')
    if job.get('portfolio_context') is not None and 'portfolio_review' in job.get('scientific_response_fields',[]):
        fields['portfolio_response']=PORTFOLIO_RESPONSE
    if job.get('final_coverage_mechanisms'):fields['final_coverage_response']=FINAL_COVERAGE_RESPONSE
    if job['role']=='A':
        fields['delivery_selection']='deliver: exact candidate_recommendation+rationale; output/receipt gaps; partial OK.'
        if job.get('purpose')=='closeout':
            fields['closeout_response']=('FINAL: follow action options; no new work/audit.' if job.get('final_only') else 'Closeout: deliver/audit/finish/wait; no exploration.')
    elif job['role']=='W':
        fields['delivery_requirements']='Managed originals persist; cite directly, no copies. Retain other outputs/receipts; report gaps.'
        fields['stage_response']='stage_intent: review=A-ready; decision=partial obstacle; continue=authorized same-stage work.'
        fields['stage_continuation']=dict(status='configured' if job.get('worker_fragment_policy') else 'unavailable',
            context=(job.get('worker_fragment_policy') or {}).get('context'),
            permission='Original terminal/session, known usage and remaining authorization required.')
    return fields


def scientific_field(job, payload, field):
    """Read only enabled consumer input; extra authored keys remain untouched."""
    enabled=job.get('scientific_response_fields')
    if enabled is not None and field not in enabled:return None
    value=payload.get(field)
    if value is None or not json_field_representable(field,value):return None
    if field=='progress_assessment':
        return dict(verdict=value['verdict']) if (isinstance(value,dict) and isinstance(value.get('verdict'),str)
            and value['verdict'] in {'progress','no_progress','unknown'}) else None
    if field=='decision_request':
        return value if (isinstance(value,dict) and isinstance(value.get('decision'),str)
            and value['decision'] in {'candidate','mechanism','compliance'}
            and all(isinstance(value.get(key),str) for key in ('question','change_criterion'))) else None
    if field in {'search_plan','search_review','candidate_nominations','candidate_comparison','portfolio_review','selection_explanation'}:
        return value if isinstance(value,dict) else None
    if field in {'mechanism_findings','final_coverage'}:
        return value if isinstance(value,list) else None
    return value


def scientific_diagnostics(job, payload):
    return [dict(code='scientific_field_unresolved',subject=field)
        for field in job.get('scientific_response_fields',[])
        if payload.get(field) is not None and scientific_field(job,payload,field) is None]


def science_payload(path: Path, role: str, protocol=CURRENT, comparison=False,*,formal_review=False,review_enabled=False,final_only=False,final_delivery=False,auxiliary_fields=None):
    import jsonschema
    from .output_repair import OutputRepairRequired
    original = regular(path)
    try:
        value = read_scientific_output(original)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise OutputRepairRequired('invalid_json', 'Return one valid JSON object matching final_response; completed experiments remain retained.') from error
    schema=result_schema(role,protocol,comparison,formal_review=formal_review,review_enabled=review_enabled,
        final_only=final_only,final_delivery=final_delivery,auxiliary_fields=auxiliary_fields)
    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError as error:
        location = '.'.join(map(str, error.absolute_path))[:128] or 'final response'
        raise OutputRepairRequired('invalid_fields', 'Correct required fields and types in ' + location + ' using final_response; do not repeat experiments.') from error
    if final_only and final_delivery and (not isinstance(value.get('candidate_recommendation'),str)
            or not value['candidate_recommendation'].strip()):
        raise OutputRepairRequired('candidate_recommendation_missing',
            'FINAL delivery requires an explicit existing candidate_recommendation. Keep the same task and retained evidence; do not run new work.')
    return value


def envelope(job, payload):
    return {**payload, 'role': job['role'], 'direct_refs': job['direct_refs'],
            'next_action': 'formal_review_return' if job.get('formal_review') else payload.get('next_action', ''),
            'route_reason': job['instruction'] if job.get('formal_review') else payload.get('route_reason', '')}


def json_field_representable(key, value):
    """Test the existing strict UTF-8 JSON representation, including its key."""
    try:json.dumps({key:value},ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,UnicodeError,OverflowError):return False
    return True


def scientific_projection(value, original):
    """Safe derived fields plus a locator; never rewrite the scientific original."""
    projected={};omitted=[]
    for index,(key,item) in enumerate(value.items()):
        if json_field_representable(key,item):projected[key]=item
        else:omitted.append(dict(field_index=index,code='unrepresentable_scientific_field'))
    if omitted:
        key='control_projection'
        while key in value:key+='_'+str(len(key))
        projected[key]=dict(kind='scientific_projection',original=original,
            diagnostics=omitted[:MAX_UNRESOLVED_LINKS],omitted_count=max(0,len(omitted)-MAX_UNRESOLVED_LINKS))
    return projected


def result_projection(job, payload):
    return scientific_projection(envelope(job,payload),'node:%03d/scientific-output' % job['step'])


def _whole_limitations(values, *, byte_limit=1024):
    """Keep whole authored entries; a long item cannot crowd out later caveats."""
    selected=[]
    for value in values:
        if len((json.dumps([*selected,value],ensure_ascii=False,indent=2)+'\n').encode())<=byte_limit:
            selected.append(value)
    return selected,len(values)-len(selected)


def _parent_view(job, ref, root=None, *, limitation_bytes=1024):
    directory = str(Path(ref['path']).parent / 'artifacts')
    entries = [row for row in job.get('direct_entry_points', [])
               if row['path'].startswith(directory + '/')]
    view = dict(conclusion=ref['path'], artifacts=directory)
    if entries:
        view['evidence'] = entries[:16]
        if len(entries) > 16:
            view['evidence_omitted_count'] = len(entries) - 16
    if root is not None:
        from . import originals, candidate_refs
        root=Path(root);parent_step=int(Path(ref['path']).parts[2])
        if ref not in job.get('direct_refs',[]):raise ValueError('Parent view has no exact task binding')
        owner=originals.node(root,parent_step)
        seal=read(originals.source_record(owner,'result-identity.json'))
        actual=originals.resolve_ref(owner,seal['raw_ref'])
        target=workspace(root,job['step'])/ref['path']
        if target.exists():
            path=originals.resolve_root_ref(root,dict(ref,path=str(target)))
        else:
            payload=read_scientific_output(actual)
            projected=scientific_projection(candidate_refs.public_science_fields(payload,
                candidate_refs.for_job(job)),str(Path(ref['path']).parent/'scientific-original.json'))
            if projected!=payload:raise ValueError('Bound public parent projection is unavailable')
            path=actual
        if path.stat().st_size!=ref['size_bytes']:
            raise ValueError('Direct scientific original changed')
        payload=read_scientific_output(path)
        view['role']=originals.source_job(owner)['role']
        view['limitations'],view['limitations_omitted_count']=_whole_limitations(payload['limitations'],byte_limit=limitation_bytes)
    return view


def material_name(work, row):
    """Use the producer's declared scientific name, independent of storage."""
    step = int(work.name)
    prefix = f'inputs/parents/{step:03d}/artifacts/'
    if not row['path'].startswith(prefix):
        raise ValueError('Scientific entry point has no exact producer output binding')
    local = row['path'][len(prefix):]
    normalized = PurePosixPath(local)
    if (not normalized.parts or normalized.is_absolute() or '..' in normalized.parts
            or '\\' in local or normalized.as_posix() != local):
        raise ValueError('Scientific entry point has no canonical public output path')
    from . import originals
    originals.material_path(work.parents[2], row)
    return local if local.startswith('experiments/') else 'artifacts/'+local


def artifact_entry_points(work, payload, materials, protocol=CURRENT, unresolved=None):
    """Resolve explicit inline scientific links, never infer paths from prose."""
    from .protocol import _current
    _current(protocol, 'Scientific artifact links')
    targets={}
    for row in materials:
        local=material_name(work,row)
        if local in targets and targets[local]!=row['path']:
            raise ValueError('Scientific entry point is ambiguous')
        targets[local]=row['path']
    entries, seen = [], set()
    def unavailable(label, target, reason):
        if unresolved is not None:
            if len(unresolved['items']) < MAX_UNRESOLVED_LINKS:
                clip = lambda value, capacity: value.encode()[:capacity].decode('utf-8', errors='ignore')
                unresolved['items'].append(dict(label=clip(label, 160), target=clip(target, 512), reason=reason))
            else:
                unresolved['omitted_count'] += 1
    links = re.finditer(r'\[([^\]\n]+)\]\((?:<([^>\n]+)>|([^\s()]+))\)', payload['scientific_result'])
    for link in links:
        label, angled, plain = link.groups()
        target = angled if angled is not None else plain
        try:
            url = urlsplit(target)
        except ValueError:
            unavailable(label, target, 'malformed_scientific_link')
            continue
        if url.scheme or url.netloc:
            continue
        decoded = unquote(url.path)
        normalized = PurePosixPath(decoded)
        if not normalized.parts or normalized.parts[0] not in {'artifacts', 'experiments'}:
            if (normalized.is_absolute() or '..' in normalized.parts
                    or '\\' in decoded or decoded.startswith('.') or url.fragment == REUSE_FRAGMENT):
                unavailable(label, target, 'unmanaged_or_legacy_link')
            continue
        if '..' in normalized.parts or url.query or '\\' in decoded:
            unavailable(label, target, 'Scientific artifact links require an exact local deliverable path')
            continue
        path = normalized.as_posix()
        if path not in targets:
            unavailable(label, target, 'Scientific artifact link does not name a selected deliverable')
            continue
        if path not in seen:
            entries.append(dict(label=label[:160], path=targets[path]))
            seen.add(path)
    return entries


def _public_materials(root, materials, *, source=None):
    """Verify only selected private descriptors, then expose their original fields."""
    from . import originals
    rows = []
    for material in materials:
        if source is None:
            originals.material_path(root, material)
        else:
            owner, relative = originals.material_owner(source, material)
            originals.resolve_ref(owner, dict(material, path=relative))
        rows.append({key: value for key, value in material.items() if key != 'file_stat'})
    return rows


def selected_inputs(work, payload, job, diagnostics=None):
    """Resolve explicit reuse intent against this role's already frozen inputs."""
    from .protocol import require_job_format
    require_job_format(job)
    root = work.parents[2]
    policy = settings(root, job)
    bound = {row['path']: row for row in job['direct_materials']}
    selected, seen, total = [], set(), 0
    choices = []
    choice = payload.get('implementation_selection')
    if choice is not None and (not isinstance(choice,list) or any(not isinstance(item,str) for item in choice)
            or not json_field_representable('implementation_selection',choice)):
        if diagnostics is not None:diagnostics.append(dict(code='implementation_unresolved',subject='unparseable explicit selection'))
        return []  # Invalid explicit choice never inherits or partly applies.
    if choice is None:
        if job.get('input_selection_status') == 'unresolved':
            if diagnostics is not None:
                diagnostics.append(dict(code='implementation_unresolved', subject='inherited unresolved choice'))
            return []
        choices = [(row['label'], row['material']) for row in job.get('selected_inputs', [])]
    else:
        from . import history
        unresolved = []
        for object_id in dict.fromkeys(choice):
            try:
                from . import candidate_refs
                canonical=candidate_refs.resolve_reference(root,job,object_id)
                if canonical in candidate_refs.available_ids(root, job):
                    matches=[item['material'] for item in history.candidate_materials(root,job) if item['candidate_id']==canonical]
                    if len(matches)!=1:raise history.UnresolvedImplementation('Candidate has no unique exact implementation')
                    row=matches[0]
                else:row = history.resolve_implementation(root, object_id, job['history']['nodes'])
            except ValueError as error:
                if not isinstance(error,history.UnresolvedImplementation) and str(error) not in {
                        'Candidate reference is outside this frozen scientific task','Use the listed scientific candidate reference'}:
                    raise
                unresolved.append(dict(code='implementation_unresolved', subject=object_id))
                continue
            choices.append((object_id, row))
        if unresolved:
            if diagnostics is not None:
                diagnostics.extend(unresolved)
            return []  # No partial selection or fallback to a former choice.
    material_seen = set()
    for label, row in choices:
        if row['path'] in material_seen:
            continue
        material_seen.add(row['path'])
        source = Path(row['source'])
        # The sealed public path identifies the producer even when a managed
        # output's retained bytes live in this instance's original archive.
        match = re.fullmatch(r'inputs/parents/([0-9]{3,})/artifacts/(.+)', row['path'])
        normalized = PurePosixPath(row['path'])
        if (match is None or '..' in normalized.parts or '\\' in row['path']
                or normalized.as_posix() != row['path']):
            raise ValueError('Implementation reuse requires an original instance node')
        origin = match[1]
        origin_step = int(origin)
        if origin != f'{origin_step:03d}' or not 0 < origin_step < job['step']:
            raise ValueError('Implementation reuse requires an exact preceding node')
        from . import originals
        owner = originals.node(root, origin_step)
        identity = read(originals.source_record(owner, 'result-identity.json'))
        actual = originals.material_path(root, row)
        if (not any({key: value for key, value in item.items() if key != 'file_stat'} == row
                for item in identity['materials']) or source.resolve() != source.absolute()
                or actual.stat().st_size != row['size_bytes']):
            raise ValueError('Selected original implementation changed or is not sealed')
        total += row['size_bytes']
        if output_quota_exceeded(policy,(entry['material']['size_bytes'] for entry in [*selected,dict(material=row)])):
            raise ValueError('Selected implementations exceed the frozen handoff bound')
        selected.append(dict(label=label[:160], origin_step=origin_step,
                             origin_result_ref=identity['result_ref'], material=row))
    return selected


def _decision_context(root, job):
    """Label the exact immediate scientific source; do not parse its prose."""
    if (job['role'] not in {'W', 'H'} or job.get('purpose') is not None
            or job.get('author_correction') or job.get('correction_request')):
        return None
    previous = job['step'] - 1
    ref = next((row for row in job['direct_refs'] if row['path'] == f'inputs/parents/{previous:03d}/conclusion.json'), None)
    if ref is None:
        return None
    from . import originals
    source_job = originals.source_job(originals.node(root, previous))
    source = science_payload(_execution_raw(root, previous), source_job['role'], source_job['handoff_protocol'], 'selection' in source_job,**response_options(source_job))
    action = 'continue_w' if job['role'] == 'W' else 'revise_h'
    if (source_job['role'] != 'A' or source.get('next_action') != action
            or job['instruction'] != source.get('route_reason')):
        return None  # H directives, E questions/returns and control corrections keep their question.
    return dict(source_role='A', source_action=action, source_original=ref['path'],
                original_instruction=job['instruction'],
                authority='A selects global direction and decision-changing evidence. Concrete execution methods are proposals. '
                'Original user constraints and H scope/discriminating commitments remain binding; W owns execution design and H owns mechanism revision.')


def _role_lineage(job):
    """Select role material from the same frozen view; indexes retain all facts."""
    import copy
    view=copy.deepcopy(job['history']['view'])
    breadth=(job.get('search') or {}).get('breadth')
    if job['role']=='W' and breadth is not None:
        rows=breadth['mechanisms']
        mechanisms=view['mechanisms']
        mechanisms['focus']=None
        mechanisms['authorized_count']=len(rows)
        mechanisms['overview']=[dict(mechanism_ref=row['mechanism_ref'],label=row['label'],
            status=row['status'],original=f'inputs/history/nodes/{row["source_h"]:03d}/0001.json') for row in rows[:4]]
        mechanisms['overview_omitted_count']=max(0,len(rows)-4)
        action=((job.get('search') or {}).get('focus') or {}).get('action')
        mechanisms['task_action']=action
        mechanisms['scope']=('Assigned shallow combination; each mechanism needs your own scientific finding or explicit obstacle.' if action=='screen' else
            'Assigned local '+str(action)+' task; findings and nominations cover only the assigned mechanisms. Do not repeat other mechanisms; their retained observations and gaps remain visible to A.')
        mechanisms['coverage_required_count']=len((job.get('search') or {}).get('coverage_obligation',[]))
        from .lineage import bounded_brief
        bounded_brief(view)
    return view


def science_view(job, root=None):
    from .protocol import require_job_format
    require_job_format(job)
    if job.get('fork_source') is not None:
        from . import fork_runtime
        return fork_runtime.selected_science_view(root, job)
    from . import formal_review, user_guidance
    if job.get('formal_review'):
        view = dict(formal_review.public(root,job), **user_guidance.public(job))
        view['review'] = {key: value for key, value in view['review'].items() if key != 'instruction'}
        return view
    protocol = job['handoff_protocol']
    context = job.get('decision_context')
    question = job['instruction']
    if context is not None:
        question = ("Choose observation-driven experiments within the bound H scientific questions and user constraints; use A's global direction and evidence gap."
                    if job['role'] == 'W' else "Revise the qualitative mechanism matrix and discriminating design in light of A's global assessment; preserve original constraints.")
    if job.get('scientific_work_unit'):
        question += '\nWork unit: ' + job['scientific_work_unit']
    if job.get('selection_protocol', 0) != job['history']['view']['candidates'].get('selection_protocol', 0):
        raise ValueError('Frozen selection projection differs from its task marker')
    request = None
    if job.get('author_correction'):
        from . import semantic
        request = semantic.public_request(root, job)
    audit = None
    if job.get('candidate_audit') is not None:
        from . import candidate_audit
        audit = candidate_audit.public_receipt(root, job['candidate_audit'])
    audit_capability = None
    if job['role'] == 'A' and root is not None:
        from . import candidate_audit
        available = candidate_audit.enabled(settings(Path(root), job))
        audit_capability = dict(available=available,
            unavailable_reason=None if available else 'candidate_audit_disabled')
    from . import shortlist
    closeout = None
    if job.get('purpose') == 'closeout' and job['role']=='A':
        from . import delivery
        closeout = dict(source_cutoff=job['source_cutoff'],
            sources=delivery.public_sources(job['delivery_sources'], job['history']['view']['candidates'].get('adapter_audits', []),
                nominated_ids=job['history'].get('candidate_nomination_ids')))
        if job['delivery_sources'].get('catalogue'):
            closeout['sources']['catalogue'] = 'inputs/delivery-candidates.json'
        if job.get('selection') is not None:
            from . import shortlist
            closeout['comparison']={key:value for key,value in shortlist.public_table(root,job['selection']).items() if key!='fields'}
    priorities={
        'H':('A','E','H','W'), 'W':('H','A','W','E'),
        'A':('W','E','H','A'), 'E':('A','H','W','E')}
    reading={
        'H':'Constraints, A judgments/counterevidence, then unresolved H questions.',
        'W':'Constraints and authorized H questions, then A direction and prior observations.',
        'A':'W observations, H questions and unresolved alternatives, then further investment.',
        'E':'Exact requester question and constraints, then requested original evidence.'}
    parents=[_parent_view(job,ref,root,limitation_bytes=1024//max(1,len(job['direct_refs']))) for ref in job['direct_refs']]
    order=priorities[job['role']]
    if job['role']=='E' and (job.get('evidence_return') or {}).get('role') in order:
        requester=job['evidence_return']['role']
        order=(requester,*[role for role in order if role!=requester])
    parents.sort(key=lambda row:order.index(row['role']) if row.get('role') in order else len(order))
    from . import engineering
    view = {key: value for key, value in {
        'role': job['role'], 'question': question,
        **user_guidance.public(job),
        **(dict(formal_review_capability=dict(enabled=True,
            choices=formal_review.public_choices(root,job)),
            formal_review_feedback=formal_review.feedback(root,job))
           if job.get('formal_review_version')==1 else {}),
        **(dict(source_restrictions=job['source_restrictions']) if 'source_restrictions' in job else {}),
        **(dict(execution_authority=dict(status='trusted_gate_required' if settings(Path(root), job).get('execution_resource_policy') else 'authority_only_production_gate_unavailable',
            qualified_materials=[read(Path(root) / ref['path'])['subject']['material']['path']
                                 for ref in job.get('execution_qualifications', [])[:3]],
            qualified_materials_omitted_count=max(0, len(job.get('execution_qualifications', [])) - 3)))
           if job.get('execution_authority') is not None else {}),
        **(dict(engineering_repair=engineering.public(job['engineering_repair'])) if job.get('engineering_repair') else {}),
        **(dict(repair_observation=dict(mechanism_ref=job['search']['focus']['mechanism_ref'],
            source_step=job['search']['repair_source']['step'],call_id=job['search']['repair_source']['call']['call_id'],
            authorization_step=job['search']['repair_authorization']['step'],
            scope='Restore this exact observation under the current H scope. Old findings retain their original H version and limitations.'))
           if (job.get('search') or {}).get('repair_source') is not None else {}),
        **(dict(candidate_nomination_policy=job['candidate_nomination_policy'],
            candidate_nomination_mechanisms=job['candidate_nomination_mechanisms'])
           if job.get('candidate_nomination_mechanisms') else {}),

        **(dict(decision_context=context) if context is not None else {}),
        'global': dict(objective='inputs/global/objective.md',
                       updates=[row['path'] for row in job['global_materials'][1:]]),
        'public_root': job['public_root'],
        **(dict(project=dict(base_root=job['project']['base_root'],
            editable=job['project']['editable'], scratch=job['project']['scratch'], outputs=job['project']['outputs'],
            base_view='Whole base directory is readonly, including untracked and ignored files; root .git is masked. Declared editable, scratch and output directories receive explicit task mounts.',
            code='Author only within declared editable directories. Managed runs use one retained code version.',
            seed={'selected_commit':'Draft initialized from the exact selected original code version.',
                  'frozen_base':'Draft initialized from frozen base editable files because no unique original code version was selected.',
                  'existing_draft':'Existing current draft was preserved; it is not asserted to match a parent commit.'}.get(
                      job.get('project_code_seed',{}).get('source')),
            writes='Use declared scratch for temporary files and outputs for retained results. Other project paths are readonly; move failed writes into the declared directories.'))
           if job.get('project') is not None else {}),
        **(dict(configured_delivery=job['configured_delivery']) if 'configured_delivery' in job else {}),
        'parents': parents,
        'reading_order':reading[job['role']]+' Preserve listed limitations. Parents are authored statements, not execution facts. Verify relevant originals as needed, delegating when native permissions allow; omissions use conclusion/index/next_page.',
        'reused_implementations':[dict(label=row['label'],path=row['material']['path']) for row in job.get('selected_inputs',[])],
        'lineage': _role_lineage(job),
        'input_selection_status': job['input_selection_status'], 'control_diagnostics': job['control_diagnostics'],
        **(dict(control_request=request)
           if job.get('author_correction') else {}),
        **(dict(closeout=closeout) if closeout is not None else {}),
        **(dict(candidate_audit=audit) if audit is not None else {}),
        **(dict(audit_capability=audit_capability) if audit_capability is not None else {}),
        **(dict(delivery_comparison={key:value for key,value in shortlist.public_table(root,job['selection']).items() if key!='fields'})
           if job['role']=='A' and job.get('purpose') is None and job.get('selection') is not None else {}),
        **(dict(portfolio_assessment={key:value for key,value in job['portfolio_context'].items() if key!='instruction'})
           if job.get('portfolio_context') is not None else {}),
        **(dict(final_coverage_mechanisms=[row['mechanism_ref'] for row in job['final_coverage_mechanisms']])
           if job.get('final_coverage_mechanisms') else {}),
        **response_instructions(job),
        **(dict(experiment_execution=('Host-configured registered W execution is available; only calls through the provided experiment command are recorded. Native scratch shell remains available.'
            if job['experiment_mode'] == 'local' else 'External launcher owns experiment configuration; native shell commands are not automatically recorded.'))
            if 'experiment_mode' in job else {}),
        **(dict(managed_execution_wait=job['managed_execution_wait'])
            if 'managed_execution_wait' in job else {}),
        'outputs': 'artifacts/', 'scratch': 'work/',
    }.items() if value}
    from . import candidate_refs
    return candidate_refs.public_fields(view,candidate_refs.for_job(job))


def material_inventory(root: Path, step: int):
    """Validate stopped-node originals and layout without changing permissions."""
    job=read(regular(root/'jobs'/f'{step:03d}.json'))
    policy = settings(root, job)
    work = workspace(root, step)
    rows, mutable, destinations = [], [], {}
    from . import execution
    record=execution.record(root,step)
    from . import worker_delivery
    worker_delivery.writers_stopped(root,job,record)
    if (record/'native-intent.json').exists():
        intent=read(regular(record/'native-intent.json'))
        if intent.get('author_context_version')==1:
            from . import author_closeout
            job=read(regular(root/'jobs'/f'{step:03d}.json'))
            proof=intent['author_closeout_science_context']
            author_closeout.validate_science_context(root,job,proof)

    def add(path, relative, *, source_kind='mutable'):
        path = regular(path)
        size = path.stat().st_size
        if policy.get('max_output_item_bytes') is not None and size > policy['max_output_item_bytes']:
            raise ValueError(f'Deliverable {relative!r} exceeds item byte budget '
                             f'({size} > {policy["max_output_item_bytes"]})')
        if source_kind=='mutable':
            if path.resolve()!=path or not path.is_relative_to(work):
                raise ValueError('Node deliverables must remain in their original owned workspace')
            if path.stat().st_nlink != 1:
                raise ValueError(f'Mutable deliverable {relative!r} must not have hard-link aliases')
        elif path.stat().st_mode & 0o777 != 0o400:
            raise ValueError('Managed output has not been sealed by its stopped writer')
        sources = destinations.setdefault(relative, [])
        for index, previous in enumerate(sources):
            if previous[0] != path:continue
            if source_kind == 'managed_frozen' and previous[1] == 'mutable':
                sources[index] = (path, source_kind)
                mutable.remove(path)
            return
        sources.append((path, source_kind))
        if source_kind=='mutable':mutable.append(path)
        rows.append(dict(source=str(path), original_source=str(path),
                         path=f'inputs/parents/{step:03d}/artifacts/{relative}', size_bytes=size))

    def artifacts(base, prefix=''):
        if not base.exists() and not base.is_symlink():
            return
        if base.is_symlink() or not base.is_dir():
            raise ValueError('Managed outputs must be an original directory')
        for directory, subdirs, files in os.walk(base, followlinks=False):
            for name in subdirs:
                if (Path(directory) / name).is_symlink():
                    raise ValueError('Managed output links are not sealed')
            for name in sorted(files):
                path = Path(directory) / name
                add(path, prefix + path.relative_to(base).as_posix())

    def experiments(base, prefix='experiments/'):
        if not base.exists() and not base.is_symlink():
            return
        if base.is_symlink() or not base.is_dir():
            raise ValueError('Experiments must be an original directory')
        for child in sorted(base.iterdir()):
            if child.is_symlink() or not child.is_dir():
                raise ValueError('Experiment nodes must be original directories')
            child_prefix = prefix + child.name + '/'
            artifacts(child / 'artifacts', child_prefix + 'artifacts/')
            for name in ('conclusion.json', 'conclusion.md'):
                path = child / name
                if path.exists() or path.is_symlink():
                    add(path, child_prefix + name)
            experiments(child / 'experiments', child_prefix + 'experiments/')

    artifacts(work / 'artifacts')
    experiments(work / 'experiments')
    job=read(root/'jobs'/f'{step:03d}.json')
    if job['role']=='W':
        from . import history
        for call in history.managed_calls(root,job):
            if call['status'] not in {'known_success','known_failed'}:continue
            outputs=history.scientific_outputs(root,job,call)
            for item in outputs:
                if item.get('retention')!='archived':raise ValueError('Managed deliverable original unavailable')
                add(root/item['path'],f'experiments/{call["call_id"]}/artifacts/{item["name"]}',source_kind='managed_frozen')
    collisions = [sources for sources in destinations.values() if len(sources) > 1]
    # Inspect every original at each destination, including managed originals
    # hidden behind an author's first entry. The author cannot resolve these.
    if any(sum(kind == 'managed_frozen' for _, kind in sources) > 1 for sources in collisions):
        raise ValueError('Different managed originals share a deliverable path')
    if collisions:
        from .output_repair import OutputRepairRequired
        managed = next((sources for sources in collisions if any(kind == 'managed_frozen' for _, kind in sources)), None)
        if managed is not None:
            authored = next(path for path, kind in managed if kind == 'mutable')
            diagnostic = (
                'Remove redundant copies; cite retained managed originals directly. Put distinct authored content at a separate path. '
                f'Preserve managed originals/call IDs; no rerun/resubmit. Owned path: {authored.relative_to(work).as_posix()}')
        else:
            paths = '; '.join(path.relative_to(work).as_posix() for path, _ in collisions[0])
            diagnostic = ('Separate owned deliverables at different publication paths; preserve distinct content. '
                f'No new work/rerun/resubmit. Owned paths: {paths}')
        raise OutputRepairRequired('deliverable_path_collision', diagnostic)
    total = sum(row['size_bytes'] for row in rows)
    if policy.get('max_output_total_bytes') is not None and total > policy['max_output_total_bytes']:
        raise ValueError(f'Deliverables exhaust total byte budget ({total} > {policy["max_output_total_bytes"]})')
    return sorted(rows, key=lambda row: row['path']), mutable


def collect_materials(root: Path, step: int):
    """Seal this stopped node's validated deliverables at their original paths."""
    rows, mutable = material_inventory(root, step)
    # Inventory, budgets and all original bindings must be valid before the
    # first permission change. IO failures in the sealing pass can still leave
    # partially changed modes; this is not a filesystem transaction.
    for path in mutable:
        if path.stat().st_mode & 0o777 != 0o400:path.chmod(0o400)
    return rows


def global_materials(root: Path):
    """Frozen original context shared by all nodes through read-only aliases."""
    rows = settings(root)['global_materials']
    for row in rows:
        source = regular(root / row['source'])
        if source.stat().st_size != row['size_bytes']:
            raise ValueError('Frozen global context changed')
    return rows


def _bind_original(source: Path, destination: Path, *, root, size_bytes,
                   source_kind, context=None, policy=None):
    """Publish/reuse a controller-owned slot for the exact logical original.

    The surrounding job/call owns the source/destination binding. File size is a
    declared bound, not a content identity. Existing managed sources still require
    exact inode aliases; existing mutable snapshots retain their first freeze.
    """
    from . import shared_originals
    source, destination, root = Path(source), Path(destination), Path(root)
    if source_kind not in {'mutable','managed_frozen'}:
        raise ValueError('Explicit mutable or managed_frozen source kind required')
    if not destination.absolute().is_relative_to(root.absolute()):
        raise ValueError('Original slot escaped its exact instance root')
    if destination.exists() or destination.is_symlink():
        shared_originals._path(destination)
        shared_originals._file(destination,size_bytes,frozen=True)
        if source_kind == 'managed_frozen' and not source.samefile(destination):
            raise ValueError('Existing original alias changed its exact inode')
        return destination
    frozen = shared_originals.retain(root,source,size_bytes=size_bytes,
        source_kind=source_kind,context=context,policy=policy)
    shared_originals.link(frozen,destination,size_bytes=size_bytes,
        root=root,context=context,policy=policy)
    return destination


def _selected_project_code(root, job):
    """Resolve only explicitly selected scientific objects to exact original code."""
    from . import execution_inputs, experiments as e, history, originals
    candidates={}
    for selected in job.get('selected_inputs',[]):
        material=selected['material']
        origin=execution_inputs._producer(root,material,controls_only=True)
        step=origin['producer_step'];owner=originals.node(root,step)
        parent=originals.source_job(owner)
        if parent.get('project')!=job['project']:
            raise ValueError('Selected code belongs to another frozen project')
        seal=read(originals.source_record(owner,'result-identity.json'))
        compiled=[item for item in seal.get('compiled_implementations',[]) if item['material']==material]
        version=None;call_id=None;kind=None
        if len(compiled)>1:raise ValueError('Selected project compiler is ambiguous')
        if compiled:
            item=compiled[0]
            value=execution_inputs.verify_compilation(root,item['compiler'],material=material,
                client_binding=item.get('client_binding'),source=owner if owner.inherited else None,controls_only=True)
            if 'code_version' in value:
                version=value['code_version'];kind='compiler'
                call_id=execution_inputs._compiler_call_id(Path(owner.original_root),step,item['compiler'])
        else:
            parts=Path(material['path']).parts
            prefix=('inputs','parents',f'{step:03d}','artifacts','experiments')
            if (len(parts)<8 or parts[:5]!=prefix or parts[6]!='artifacts'
                    or seal.get('observations') is None):continue
            call_id=parts[5];name=Path(*parts[7:]).as_posix()
            observed=history.validate_observation_original(owner,parent,seal,controls_only=True)
            matches=[row for row in observed.get('managed_calls',[]) if row['call_id']==call_id
                and len([item for item in row.get('outputs',[]) if item['name']==name
                    and item['size_bytes']==material['size_bytes']])==1]
            if len(matches)!=1:continue
            proof=matches[0]['accepted']
            accepted=read(originals.resolve_ref(owner,dict(proof,path=proof['snapshot'])))
            if 'code_version' in accepted:
                interface=accepted['interface']
                config=dict(interface['config_record'],execution_interface=interface)
                e._code_version_binding(config,accepted,source=owner)
                version=accepted['code_version'];kind='run'
        if version is None:continue
        binding=dict(source='selected_commit',kind=kind,step=step,call_id=call_id,
            original_job=originals.original_ref(owner,f'jobs/{step:03d}.json'),code_version=dict(version))
        holder=owner.storage_root.parents[1] if owner.inherited else owner.storage_root
        key=json.dumps(binding,sort_keys=True)
        candidates[key]=(holder,binding)
    if len(candidates)==1:return next(iter(candidates.values()))
    return None,dict(source='frozen_base',reason='No unique exact code version is bound by selected scientific inputs.')


@contextmanager
def _project_seed_export(holder, binding, *, project, context):
    """Release only an exact export first created for this seed's reads."""
    if holder is None:
        yield None
        return
    from . import code_versions, project_layout, storage_policy as storage
    version=binding['code_version'];step=binding['step'];call=binding['call_id']
    paths=code_versions.verify(holder,step=step,call_id=call,code_version=version,context=context)
    project_layout.validate_code(project,version,purpose=binding['kind'],paths=paths)
    exact=code_versions._private(holder,step,call,create=False,context=context).export
    existed=exact.exists() or exact.is_symlink()
    export=code_versions.materialize(holder,step=step,call_id=call,code_version=version,context=context)
    try:yield export
    finally:
        if not existed:
            code_versions.cleanup_export(holder,step=step,call_id=call,context=storage.Deadline(65))


def _seed_project_code(root, job, work, *, context):
    """Publish one bounded writable draft; an existing draft always wins."""
    from . import code_versions, project_layout, storage_policy as storage
    project=job['project'];layout=project_layout.code_roots(project,work);target=layout['root']
    if target.exists() or target.is_symlink():
        _,anchors=code_versions._directory(target)
        for row in layout['editable']:
            directory=target
            for component in Path(row['relative']).parts:
                directory=directory/component
                code_versions._mkdir(directory,anchors)
        return dict(source='existing_draft')
    policy=code_versions._limits(None)
    holder,binding=_selected_project_code(root,job)
    with _project_seed_export(holder,binding,project=project,context=context) as export:
        if holder is not None:
            version=binding['code_version']
            observed=code_versions._scan(export,version['entry'],'directory',policy,context)
            records=[dict(relative=name,source=export/name,signature=signature)
                for name,signature in observed[0].items()]
        else:
            records=project_layout.seed_files(project,context=context)
        directories=project_layout.seed_directories(project,records,context=context)
        actual=storage.Inventory(policy,context=context)
        stage=Path(tempfile.mkdtemp(prefix='.project-seed-',dir=work));created=[];created_directories=[]
        try:
            _,stage_anchors=code_versions._directory(stage)
            for name in sorted(directories,key=lambda name:(len(Path(name).parts),name)):
                code_versions._mkdir(stage/name,stage_anchors)
                created_directories.append(stage/name)
            for row in records:
                code_versions._unchanged(stage_anchors)
                if holder is not None:
                    code_versions._source_parents(export,row['relative'],observed[1])
                else:project_layout._unchanged(row['ancestors'])
                raw=code_versions._read_file(row['source'],row['signature'],actual)
                destination=stage/row['relative']
                with destination.open('xb') as stream:
                    created.append(destination);stream.write(raw)
                    os.fchmod(stream.fileno(),0o755 if row['signature'][2]&0o111 else 0o644)
            if holder is not None:
                if code_versions._scan(export,version['entry'],'directory',policy,context)!=observed:
                    raise ValueError('Selected original code changed during draft seed')
            else:
                for row in records:
                    project_layout._unchanged(row['ancestors'])
                    if code_versions._signature(row['source'].lstat())!=row['signature']:
                        raise ValueError('Frozen base editable code changed during draft seed')
            if target.exists() or target.is_symlink():raise ValueError('Current project draft appeared during seed')
            code_versions._unchanged(stage_anchors)
            stage.rename(target)
            return binding
        finally:
            if stage.exists():
                for path in reversed(created):path.unlink()
                for directory in reversed(created_directories):directory.rmdir()
                stage.rmdir()


@owner_effect
def build_job(root: Path, step: int, role: str, parents: list[int], instruction='', evidence_return=None,
              correction_request=None, author_correction=None, *, purpose=None, source_cutoff=None, delivery_sources=None, engineering_repair=None, final_report=None):
    if role not in ROLE_INSTRUCTIONS or type(step) is not int or step < 1:
        raise ValueError('Invalid scientific node')
    if len(parents) != len(set(parents)) or any(type(p) is not int or not 0 < p < step for p in parents):
        raise ValueError('Parents must be exact distinct preceding nodes')
    from .protocol import require_current
    require_current(root)
    from . import budget
    if budget.final_a_barrier(root, before_step=step) is not None:
        raise ValueError('Final A return permits no further scientific task')
    phase = budget.closeout_state(root)['begin']
    if phase is not None and phase['mode'] == 'final' and step >= phase['first_step'] and (
            role != 'A' or step != phase['first_step'] or purpose != 'closeout' and final_report is None):
        raise ValueError('FINAL permits only its explicit final A assignment')
    if final_report is not None:
        budget.validate_final_report_inputs(root, dict(step=step, role=role, purpose=purpose,
            final_only=phase is not None and phase['mode'] == 'final' and step == phase['first_step'],
            final_report=final_report, direct_refs=[dict(path=f'inputs/parents/{parent:03d}/conclusion.json') for parent in parents]))
    if purpose is None and any((root/'host-recovery/author-closeout'/f'{prior:03d}-output-acceptance.json').exists() for prior in range(1,step)):
        raise ValueError('Accepted completed author output permits closeout handoff only')
    if purpose is not None:
        from . import budget
        begin=budget.closeout_state(root)['begin']
        if (purpose!='closeout' or role not in {'A','W'} or role=='W' and engineering_repair is None or begin is None or begin['mode'] not in {'model','final'}
                or begin['mode']=='final' and (role!='A' or step!=begin['first_step'])
                or source_cutoff!=begin['source_cutoff'] or step<begin['first_step'] or delivery_sources is None):
            raise ValueError('Closeout task requires its immutable configured boundary')
    elif source_cutoff is not None or delivery_sources is not None:
        raise ValueError('Ordinary task cannot select a noncontiguous science prefix')
    from . import engineering
    if role=='W' and engineering_repair is None and parents:
        engineering_repair=engineering.request(root,max(parents))
    if engineering_repair is not None:
        if role!='W':raise ValueError('Engineering repair requires a new exact Worker task')
        engineering.admit(root,engineering_repair,step)
    job_path=root/'jobs'/f'{step:03d}.json'
    retained=read(regular(job_path)) if job_path.exists() else None
    if retained is not None:require_current(root,retained)
    from . import configuration_updates
    revision = configuration_updates.bind_new_job(root, step)
    policy = configuration_updates.for_new_job(root, step)
    protocol=CURRENT
    selection_protocol=1
    audit_projection_version=1 if policy.get('candidate_audit_policy') else 0
    review_binding=None
    if (evidence_return or {}).get('kind')=='formal_review':
        from . import formal_review
        if role!='A':raise ValueError('Formal review requires its native A node')
        review_binding=formal_review.input_binding(root,evidence_return['request'])
        audit_projection_version=0
    work = workspace(root, step)
    work.mkdir(parents=True, exist_ok=True)
    if (work/'inputs').is_symlink() or (work/'inputs').resolve()!=(work/'inputs').absolute():
        raise ValueError('Scientific inputs must remain in their original owned workspace')
    (work / 'work').mkdir(exist_ok=True)
    (work / 'artifacts').mkdir(exist_ok=True)
    globals_ = global_materials(root)
    from . import storage_policy as storage
    input_inventory=storage.Inventory(policy.get('storage_policy'),context=storage.Deadline(policy['max_seconds']))
    for item in globals_:
        input_inventory.entry();input_inventory.file((root/item['source']).stat().st_size)
        from . import originals
        originals.resolve_root_ref(root, dict(item, path=item['source']))
    refs, materials, entry_points, selected = [], [], [], []
    if review_binding is not None:
        materials=review_binding['materials'];selected=review_binding['selected']
    for parent in ([] if review_binding is not None else parents):
        source_work = workspace(root, parent)
        from . import history
        history._source(root,parent)
        # Pass the author's science, not the controller envelope and its parent
        # references. Historical lineage remains in the host-owned node tree.
        source = _execution_raw(root, parent)
        ref = dict(path=f'inputs/parents/{parent:03d}/conclusion.json', size_bytes=source.stat().st_size)
        (work / Path(ref['path']).parent / 'artifacts').mkdir(parents=True,exist_ok=True)
        refs.append(ref)
        from . import originals
        parent_seal = read(originals.source_record(originals.node(root, parent), 'result-identity.json'))
        materials.extend(_public_materials(root, parent_seal['materials']))
        entry_points.extend(parent_seal.get('entry_points', []))
        # Reuse is an instruction to the immediate next node, not persistent
        # baggage on a retained H ancestor in every later scientific cycle.
        if parent == step - 1:
            selected.extend(parent_seal.get('selected_inputs', []))
    unique = {}
    for item in [*materials, *[row['material'] for row in selected]]:
        if item['path'] in unique and unique[item['path']] != item:
            raise ValueError('Selected input paths conflict')
        unique[item['path']] = item
    materials = list(unique.values())
    if output_quota_exceeded(policy,(row['size_bytes'] for row in materials)):
        raise ValueError('Combined input materials exceed the frozen handoff bound')
    reused = {}
    for row in selected:
        reused.setdefault(row['material']['path'], row)
    selected = list(reused.values())
    if output_quota_exceeded(policy,(row['material']['size_bytes'] for row in selected)):
        raise ValueError('Selected implementations exceed the frozen handoff bound')
    for item in materials:
        input_inventory.entry();input_inventory.file(item['size_bytes'])
        from . import originals
        originals.material_path(root, item)
    # No recursive parent expansion; per-file identities stay in host storage.
    job = dict(step=step, role=role, goal_text=policy['objective'], public_root=policy['public_root'],
               instruction=instruction, direct_refs=refs, direct_materials=materials, direct_entry_points=entry_points, global_materials=globals_,
               evidence_return=evidence_return, **(dict(selected_inputs=selected) if selected else {}),
               handoff_protocol=protocol, worker_view_version=1, lineage_projection_version=1)
    if retained is not None:
        if retained.get('project') != policy.get('project'):
            raise ValueError('Frozen scientific project changed')
        if retained.get('project') is not None:job['project'] = retained['project']
    elif policy.get('project') is not None:
        job['project'] = json.loads(json.dumps(policy['project']))
    if role=='W' and job.get('project') is not None:
        if retained is None:
            job['project_code_seed']=_seed_project_code(root,job,work,context=input_inventory.context)
        elif 'project_code_seed' in retained:
            job['project_code_seed']=retained['project_code_seed']
    if revision is not None:
        job['configuration_revision'] = revision
    if retained is None:
        from . import delivery
        job['configured_delivery'] = delivery.public_configuration(policy.get('delivery_policy'))
    elif 'configured_delivery' in retained:
        job['configured_delivery'] = retained['configured_delivery']
    if final_report is not None:
        job['final_report'] = final_report
        if retained is None and phase is not None:
            job['closeout_begin'] = dict(seq=phase['seq'],
                record=record_ref(root / 'control-ledger' / f'{phase["seq"]:06d}.json'))
        elif retained is not None and 'closeout_begin' in retained:
            job['closeout_begin'] = retained['closeout_begin']
    resumed = budget.normal_resume(root,step)
    if resumed is not None and purpose is None and final_report is None:
        job['normal_resume'] = budget._event_ref(root,resumed)
    if (retained is None and ((purpose == 'closeout' and begin['mode'] == 'final') or final_report is not None)
            or retained is not None and retained.get('final_only')):
        job['final_only'] = True
    if policy.get('formal_review_policy') is not None:
        from . import formal_review
        job['formal_review_version']=1
        formal_review.bind_job(root,job)
    from . import profiles, skill_supply
    skill_binding = skill_supply.binding(policy, job)
    if skill_binding is not None:
        job['skill_binding'] = skill_binding
    if policy.get('role_profiles') is not None:
        job['native_profile'] = profiles.selected(policy, role)
    if retained is not None:
        profiles.for_job(policy, retained)
    if (retained.get('live_feedback_version') if retained is not None else policy.get('live_feedback_version')) == 1:
        job['live_feedback_version'] = 1
    if (retained.get('experiment_interface_version') if retained is not None else policy.get('experiment_interface_version')) == 1:
        job['experiment_interface_version'] = 1
    if (retained.get('managed_admission_version') if retained is not None else policy.get('managed_admission_version')) == 1:
        job['managed_admission_version'] = 1
    if policy.get('experiment_mode') is not None:
        job['experiment_mode'] = policy['experiment_mode']
    if policy.get('execution_config') is not None:
        job['execution_config_record']=policy['execution_config']
    advice = retained.get('role_guidance') if retained is not None else policy.get('role_guidance', {}).get(role)
    if advice is not None:
        job['role_guidance'] = advice
    job['handoff_authority_version'] = 1
    from . import guidance
    job['native_role_instruction'] = (retained['native_role_instruction'] if retained is not None
                                      else advice if advice is not None else guidance.OPPORTUNITY[role])
    if audit_projection_version == 1:
        job['audit_projection_version'] = 1
    job['selection_protocol'] = selection_protocol
    if engineering_repair is not None:
        job['engineering_repair']=engineering_repair
        engineering.bind_inputs(root,job)
    if purpose is not None:
        job.update(purpose=purpose, source_cutoff=source_cutoff, delivery_sources=delivery_sources,
                   closeout_begin=dict(seq=begin['seq'], record=record_ref(root / 'control-ledger' / f'{begin["seq"]:06d}.json')))
        if role=='A' and policy.get('selection_policy') is not None:
            from . import shortlist
            mark = shortlist.marker(root,step)
            if mark is not None:
                job['selection'] = mark
    previous = read(originals.source_record(originals.node(root, step - 1), 'result-identity.json')) if step - 1 in parents and review_binding is None else {}
    job['input_selection_status'] = previous.get('control_outcome', {}).get('selection_status', 'applied')
    job['control_diagnostics'] = previous.get('control_outcome', {}).get('diagnostics', [])
    if role=='W':job['worker_fragment_policy']=policy.get('worker_fragment_policy')
    if author_correction is not None:
        job['author_correction'] = author_correction
    if role == 'A' and step - 1 in parents and review_binding is None:
        from . import candidate_audit
        receipt = candidate_audit.receipt_ref(root, step - 1)
        if receipt is not None:
            job['candidate_audit'] = receipt
    state = None
    from . import history
    history_cutoff = final_report['source_cutoff'] if final_report else source_cutoff
    extra_steps = [parent for parent in parents if history_cutoff is not None and parent > history_cutoff]
    from . import lineage
    if retained is not None:
        job['history']=retained['history']
        history.validate(root,work,job['history'])
        options=retained.get('implementation_options',[])
    elif review_binding is not None:
        job['history']=review_binding['history'];options=[]
    else:
        job['history'],state=lineage.bind(root,step,role,protocol,source_cutoff=final_report['source_cutoff'] if final_report else source_cutoff,extra_steps=extra_steps,selection_protocol=selection_protocol,
            audit_projection_version=audit_projection_version,adjacent_audit=job.get('candidate_audit'),
            worker_view_version=1,assessment_job=job if role=='A' else None)
        options = [item for parent in parents for item in history.implementation_options(root, parent)]
    from . import candidate_refs
    job['direct_originals']=refs
    job['direct_refs']=[]
    for original in refs:
        parent=int(Path(original['path']).parts[2]);source=regular(_execution_raw(root,parent))
        if source.stat().st_size!=original['size_bytes']:raise ValueError('Direct producer original changed')
        reported=candidate_refs.public_science_fields(read_scientific_output(source),candidate_refs.for_job(job))
        original_path=str(Path(original['path']).parent/'scientific-original.json')
        public=scientific_projection(reported,original_path)
        # A logical raw link resolves to the sealed parent original.
        if public!=reported and not any(row['path']==original_path for row in job['history']['files']):
            job['history']['files'].append(dict(path=original_path,source=str(source),
                size_bytes=original['size_bytes'],kind='scientific_original'))
        target=work/original['path']
        if target.exists():
            if read_scientific_output(regular(target))!=public:raise ValueError('Frozen parent scientific view changed')
        elif public!=read_scientific_output(source):
            write(target,public);target.chmod(0o400)
        if source.stat().st_size!=original['size_bytes']:raise ValueError('Direct producer original changed')
        job['direct_refs'].append(dict(path=original['path'],
            size_bytes=target.stat().st_size if target.exists() else original['size_bytes']))
    job['implementation_options'] = options[:16]
    job['implementation_options_omitted_count'] = retained.get('implementation_options_omitted_count',0) if retained is not None else max(0, len(options) - 16)
    if review_binding is None and retained is None:
        job['history']['view']['implementation_options'] = [dict(id=row['id'], label=row['label'].encode()[:80].decode('utf-8',errors='ignore')) for row in options[:2]]
        job['history']['view']['implementation_options_omitted_count'] = max(0,len(options)-2)
        lineage.bounded_brief(job['history']['view'])
    if retained is not None:
        for field in ('search','portfolio_context','final_coverage_mechanisms','selection','candidate_nomination_policy','candidate_nomination_mechanisms'):
            if field in retained:job[field]=retained[field]
    elif purpose is None and not job.get('formal_review') and final_report is None and engineering_repair is None and policy.get('search_policy') is not None:
        from . import search
        job['search'] = search.bind(root, step, role, protocol, state=state, unified=True)
    if role=='A' and state is not None and not job.get('formal_review'):
        job['portfolio_context']=job['history']['portfolio_assessment']
        if policy.get('search_policy') is not None:
            job['final_coverage_mechanisms']=[dict(mechanism_ref=ref,label=item['label'],status=item['status']) for ref,item in state['mechanisms'].items()]
    if retained is None and role=='A' and purpose is None and review_binding is None and final_report is None and policy.get('selection_policy') is not None:
        from . import shortlist
        job['selection']=shortlist.bind_normal(root,job)
    if retained is None and role=='W' and job.get('search') is not None and policy.get('candidate_nomination_policy') is not None:
        job['candidate_nomination_policy']=policy['candidate_nomination_policy']
        if (job['search'].get('focus') or {}).get('action')=='screen':
            job['candidate_nomination_mechanisms']=[dict(mechanism_ref=ref,status=item['status'])
                for ref,item in state['mechanisms'].items()]
        else:
            job['candidate_nomination_mechanisms']=[dict(mechanism_ref=row['mechanism_ref'],status=row['status'])
                for row in (job['search'].get('breadth') or {}).get('mechanisms',[])]
    if retained is None:
        job['scientific_response_fields']=response_fields(job,policy)
    elif 'scientific_response_fields' in retained:
        job['scientific_response_fields']=retained['scientific_response_fields']
    if retained is not None:
        if 'scientific_work_unit' in retained:job['scientific_work_unit']=retained['scientific_work_unit']
    elif role=='W':
        from . import guidance
        action=((job.get('search') or {}).get('focus') or {}).get('action')
        if action in guidance.WORK_UNITS:job['scientific_work_unit']=guidance.WORK_UNITS[action]
    if correction_request is not None:
        job['correction_request'] = correction_request
        from . import correction
        correction.validate_job(root, job)
    context = _decision_context(root, job)
    if context is not None:
        job['decision_context'] = context
    if purpose == 'closeout' and delivery_sources.get('catalogue'):
        catalogue_path = work / 'inputs/delivery-candidates.json'
        catalogue = dict(items=delivery_sources['items'], omitted_count=delivery_sources['omitted_count'])
        from . import candidate_refs
        catalogue=candidate_refs.public_fields(catalogue,candidate_refs.for_job(job))
        if catalogue_path.exists():
            if read(regular(catalogue_path)) != catalogue:
                raise ValueError('Frozen candidate catalogue changed')
        else:
            write(catalogue_path, catalogue)
            catalogue_path.chmod(0o400)
    if role=='A' and job.get('formal_review_version')==1 and not job.get('formal_review') and (retained is None or 'formal_review_choices' in retained):
        from . import formal_review
        job['formal_review_choices']=formal_review.choice_constraints(root,job,
            retained['formal_review_choices']['unavailable'] if retained is not None else None)
    # Retained legacy jobs keep their exact identity and remain unqualified.
    if retained is None or 'source_provenance' in retained:
        from . import qualification
        job['source_provenance'] = qualification.bind_inputs(root, job)
        job['source_restrictions'] = qualification.status(root, job)
    from . import execution_authority
    execution_authority.bind_job(root, job)
    if retained is None and policy.get('correction_policy') is not None:
        from . import correction
        job['control_cut'] = dict(correction_events=correction.freeze_prefix(root))
    elif retained is not None and 'control_cut' in retained:
        job['control_cut'] = retained['control_cut']
    if retained is not None and 'managed_execution_wait' in retained:
        job['managed_execution_wait'] = retained['managed_execution_wait']
    elif retained is None and role=='W' and 'experiment_mode' in job:
        from . import managed_wait
        job['managed_execution_wait'] = managed_wait.capability(root,job)
    from . import user_guidance
    with user_guidance.binding(root, step, role, retained) as statements:
        if statements:
            job['user_guidance'] = statements
        for path, value in ((root / 'jobs' / f'{step:03d}.json', job),
                            (work / 'science-task.json', read(regular(work / 'science-task.json')) if retained is not None else science_view(job, root)),
                            (host_record(work) / 'science-result.schema.json', read(regular(host_record(work) / 'science-result.schema.json')) if retained is not None else result_schema(role, protocol, 'selection' in job,**response_options(job)))):
            if path.exists():
                if read(regular(path)) != value:
                    raise ValueError('Frozen scientific task changed')
            elif path.name == 'science-result.schema.json':
                from . import shared_originals
                payload=(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')
                original=shared_originals.publish_bytes(root,payload,policy=policy.get('storage_policy'),context=input_inventory.context)
                shared_originals.link(original,path,size_bytes=len(payload),root=root,
                    policy=policy.get('storage_policy'),context=input_inventory.context)
            else:
                write(path, value)
    skill_supply.project(root, policy, job)
    return job


def _native_output_identity(root, step, native_record, intent, outcome, *, source=None, seal=None, controls_only=False):
    from . import budget
    if source is None:
        job = read(regular(Path(root) / 'jobs' / f'{step:03d}.json'))
        budget.validate_final_a_receipt(root, job, intent, outcome)
    # The original stopped outcome remains byte-exact. Only a separate explicit
    # completed-return receipt can provide its exceptional final raw identity.
    if outcome.get('stop_reason')=='author_closeout_token_limit' and intent.get('attempt')==2:
        from . import author_closeout
        options={} if source is None else dict(source=source,seal=seal,controls_only=controls_only)
        accepted=author_closeout.accepted_output(root,step,native_record,intent,outcome,**options)
        if accepted is not None:return accepted['raw_ref'],accepted
    return outcome.get('raw_ref'),None


def _final_a_seal(native_record, outcome):
    if outcome.get('final_a_completion') is None:
        return None
    return dict(native_outcome_ref=record_ref(regular(native_record / 'native-outcome.json')),
                usage_ref=record_ref(regular(native_record / 'usage.json')) if (native_record / 'usage.json').exists() else None)


def _sealed_science(job, seal, raw, result, *, root=None, source=None):
    """Shared immutable science checks; no current control or authority reads."""
    from . import candidate_refs
    if regular(raw).stat().st_size != seal['raw_ref']['size_bytes'] or regular(result).stat().st_size != seal['result_ref']['size_bytes']:
        raise ValueError('Sealed scientific bytes changed')
    if 'file_stat' in seal['raw_ref']:
        from . import experiments
        if experiments.proof(raw)['file_stat']!=seal['raw_ref']['file_stat']:
            raise ValueError('Sealed scientific original changed')
    payload = candidate_refs.control_payload(job, science_payload(raw, job['role'],
        job['handoff_protocol'], 'selection' in job, **response_options(job)), root=root, source=source)
    if read(result) != result_projection(job, payload):
        raise ValueError('Result envelope differs from author science')
    return payload


def validate_original(source, *, _active=None, _memo=None, controls_only=False):
    """Validate retained source facts without renewing its execution grants."""
    from . import originals, qualification, formal_review, execution_inputs, history
    if _memo is None:
        return validate_original(source,_active=set(),_memo={},controls_only=controls_only)
    active = set() if _active is None else _active
    memo = {} if _memo is None else _memo
    key = (originals.identity_key(source.origin_identity), source.step)
    if key in active:
        raise ValueError('Scientific original dependency cycle')
    if key in memo:
        return memo[key]
    active = {*active, key}
    job = originals.source_job(source)
    policy = originals.source_settings(source)
    if policy.get('handoff_protocol') != CURRENT or job.get('handoff_protocol') != CURRENT:
        raise ValueError('Fork original requires the current frozen protocol')
    from .protocol import require_job_format
    require_job_format(job)
    job_path = originals.resolve_ref(source, dict(path=f'jobs/{source.step:03d}.json'))
    seal = read(originals.source_record(source, 'result-identity.json'))
    if seal['job_ref'] != dict(path=str(Path(source.original_root)/'jobs'/f'{source.step:03d}.json')):
        raise ValueError('Source seal changed its exact original job')
    if controls_only and not source.inherited and source.native_record is None:
        native=Path(seal['raw_ref']['path']).parent
        origin=Path(source.original_root)/'native-evidence'/f'{source.step:03d}'
        relative=native.relative_to(origin)
        if relative.parts and (len(relative.parts)!=2 or relative.parts[0]!='continuations'
                or not relative.parts[1].isdecimal() or relative.parts[1]!=f'{int(relative.parts[1]):03d}'):
            raise ValueError('Source seal lost its exact native attempt')
        source=originals.NodeSource(source.origin_identity,source.step,source.original_root,source.storage_root,
            native_record=native.relative_to(source.original_root).as_posix())
    intent = read(originals.source_record(source, 'native-intent.json'))
    outcome = read(originals.source_record(source, 'native-outcome.json'))
    raw = None if controls_only else originals.resolve_ref(source,seal['raw_ref'])
    result = originals.resolve_ref(source,seal['result_ref'])
    raw_ref,accepted=_native_output_identity(Path(source.original_root),source.step,
        Path(seal['raw_ref']['path']).parent,intent,outcome,source=source,seal=seal,controls_only=controls_only)
    identity=({key:raw_ref.get(key) for key in ('path','size_bytes')}
        if accepted is not None and isinstance(raw_ref,dict) else raw_ref)
    if (intent.get('task_ref') != seal['job_ref'] or outcome.get('returncode') != 0
            or not isinstance(raw_ref,dict)
            or identity != {key:seal['raw_ref'][key] for key in ('path','size_bytes')}
            or (seal.get('completed_output_acceptance') is not None and accepted is None)):
        raise ValueError('Source terminal lost its exact original task/output')
    payload = read(result) if controls_only else _sealed_science(job,seal,raw,result,source=source)
    if not controls_only:
        for ref in [*job.get('global_materials', []), *job.get('direct_refs', []),
                    *job.get('direct_materials', []), *job.get('history', {}).get('files', []),
                    *job.get('history', {}).get('execution_recovery_materials', [])]:
            originals.resolve_ref(source, dict(path=f'project/nodes/{source.step:03d}/' + ref['path'],
                **{field: ref[field] for field in ('size_bytes',) if field in ref}))
        for row in job.get('history', {}).get('nodes', []):
            parent = originals.dependency(source, row['step'], seal_ref=row['seal_ref'])
            validate_original(parent,_active=active,_memo=memo,controls_only=controls_only)
    for row in job.get('direct_originals', job.get('direct_refs', [])):
        step = int(Path(row['path']).parts[2])
        parent = originals.dependency(source, step)
        parent_seal = read(originals.source_record(parent, 'result-identity.json'))
        if row['size_bytes'] != parent_seal['raw_ref']['size_bytes']:
            raise ValueError('Source direct original changed its exact producer')
    qualification.validate_original(source,job,seal,controls_only=controls_only)
    history.validate_observation_original(source,job,seal,controls_only=controls_only)
    if ((seal.get('control_outcome') or {}).get('worker_delivery') or {}).get('facts_ref') is not None:
        from . import worker_delivery
        worker_delivery.validate_original(Path(source.storage_root),source,job,seal,controls_only=controls_only)
    execution_inputs.validate_original(source,job,seal,controls_only=controls_only)
    formal_review.validate_original(source,job,seal,payload,controls_only=controls_only)
    if not controls_only:
        for material in seal.get('materials', []):
            logical = Path(material['source'])
            if not logical.is_absolute() or not logical.is_relative_to(Path(source.original_root)):
                raise ValueError('Source material escaped its original producer')
            originals.resolve_ref(source, dict(material, path=logical.relative_to(source.original_root).as_posix()))
    memo[key] = read(result)
    return memo[key]


def validate(work: Path):
    root = work.parents[2]
    from . import originals
    if originals.inherited(root, int(work.name)):
        return validate_original(originals.node(root, int(work.name)))
    job_path = root / 'jobs' / f'{int(work.name):03d}.json'
    job = read(regular(job_path))
    from .protocol import require_current
    require_current(root, job)
    record = host_record(work)
    seal = read(regular(record / 'result-identity.json'))
    from . import execution
    native_record = execution.record(root, job['step'])
    intent = read(regular(native_record / 'native-intent.json'))
    outcome = read(regular(native_record / 'native-outcome.json'))
    raw = regular(native_record / 'science-output.json')
    result = regular(record / 'result.json')
    conclusion = work / 'conclusion.json'
    native_raw_ref,accepted=_native_output_identity(root,job['step'],native_record,intent,outcome)
    if seal.get('final_a_completion') != _final_a_seal(native_record, outcome):
        raise ValueError('Final A completion receipt identity changed')
    expected_acceptance=accepted['receipt'] if accepted is not None else None
    if (seal.get('completed_output_acceptance')!=expected_acceptance
            or seal.get('completed_output_limitation')!=(accepted['facts'] if accepted is not None else None)):
        raise ValueError('Completed-output acceptance seal identity changed')
    if (seal['job_ref'] != record_ref(job_path) or intent['task_ref'] != record_ref(job_path)
            or {key:seal['raw_ref'][key] for key in ('path','size_bytes')} != file_ref(raw) or seal['result_ref'] != file_ref(result)
            or native_raw_ref != {key:seal['raw_ref'][key] for key in ('path','size_bytes')}):
        raise ValueError('Scientific node identity changed')
    if (conclusion.exists() or conclusion.is_symlink()) and regular(conclusion).stat().st_size!=seal['raw_ref']['size_bytes']:
        raise ValueError('Saved scientific conclusion projection changed')
    from . import candidate_refs
    payload = _sealed_science(job, seal, raw, result, root=root)
    # The sealed interpretation belongs to this exact accepted attempt. New
    # optional-field readers cannot reinterpret already sealed scientific work.
    if not isinstance(seal.get('control_outcome'),dict):
        raise ValueError('Current scientific attempt has no control outcome')
    delivery_facts=(seal['control_outcome'].get('worker_delivery') or {}).get('facts_ref')
    if delivery_facts is not None:
        from . import worker_delivery
        if delivery_facts!=record_ref(regular(native_record/worker_delivery.FACT_NAME)):
            raise ValueError('Final W scientific evidence differs from its seal')
        worker_delivery.validate(root,job,native_record,source_attempt=intent.get('attempt',0))
    from . import correction
    correction.validate_payload(root, job, payload)
    if read(result) != result_projection(job, payload):
        raise ValueError('Result envelope differs from author science')
    unresolved = dict(items=[], omitted_count=0)
    if seal.get('entry_points', []) != artifact_entry_points(work, payload, seal['materials'], job['handoff_protocol'], unresolved):
        raise ValueError('Scientific deliverable entry points changed')
    if seal.get('unresolved_links') != unresolved:
        raise ValueError('Scientific unresolved link receipt changed')
    if seal.get('selected_inputs', []) != selected_inputs(work, payload, job):
        raise ValueError('Selected implementation receipts changed')
    _validate_inputs(root, work, job)
    from . import execution_inputs
    execution_inputs.validate_seal(root, job, seal)
    if 'pending_executions' in seal:
        from .execution_resume import verify_pending_set
        verify_pending_set(root, seal)
    from . import qualification
    qualification.validate_outputs(root, job, seal)
    from . import formal_review
    formal_review.validate_seal(root,job,payload,seal)
    from . import history
    history.validate_observation(root, job, seal)
    for ref in seal['materials']:
        path = regular(originals.material_path(root, ref))
        if path.stat().st_size != ref['size_bytes']:
            raise ValueError('Scientific original changed')
    return read(result)


def _validate_task_view(root, work, job):
    """Check frozen scientific input bindings, not a later prompt generator."""
    view=read(regular(work/'science-task.json'))
    if job.get('formal_review'):
        if view!=science_view(job,root):raise ValueError('Frozen formal review task changed')
        return
    expected_global=dict(objective='inputs/global/objective.md',
        updates=[row['path'] for row in job['global_materials'][1:]])
    parents=view.get('parents',[]) if isinstance(view,dict) else None
    if (not isinstance(view,dict) or view.get('role')!=job['role']
            or view.get('global')!=expected_global or view.get('public_root')!=job['public_root']
            or not isinstance(parents,list) or any(not isinstance(row,dict) for row in parents)):
        raise ValueError('Frozen scientific task changed its input binding')
    expected={row['path'] for row in job['direct_refs']}
    actual=[row.get('conclusion') for row in parents]
    if (any(not isinstance(path,str) for path in actual) or len(actual)!=len(expected)
            or set(actual)!=expected):
        raise ValueError('Frozen scientific task changed its exact parents')


def _validate_inputs(root, work, job):
    if job.get('fork_source') is not None:
        from . import fork_runtime, qualification, formal_review, engineering, execution_authority
        fork_runtime.validate_selected_job(root, work, job)
        qualification.validate_inputs(root, job)
        execution_authority.validate_job(root, job)
        formal_review.validate_job(root, job)
        engineering.validate_job(root, job)
        return
    from . import budget
    report = budget.validate_final_report_inputs(root, job)
    report_steps = ([*budget.scientific_steps(root,report['source_cutoff']+1), *sorted(int(Path(ref['path']).parts[2])
        for ref in job['direct_refs'] if int(Path(ref['path']).parts[2]) > report['source_cutoff'])] if report else [])
    if report is not None and ([row['step'] for row in job['history']['nodes']] != report_steps or 'search' in job):
        raise ValueError('Final report input history changed its exact sealed prefix')
    resumed = budget.normal_resume(root,job['step'])
    expected_resume = (budget._event_ref(root,resumed) if resumed is not None and job.get('purpose') in (None,'formal_review') and not job.get('final_report') else None)
    if job.get('normal_resume') != expected_resume:
        raise ValueError('Scientific task changed its exact normal resume boundary')
    if expected_resume is not None and not job.get('formal_review') and [row['step'] for row in job['history']['nodes']] != budget.scientific_steps(root,job['step']):
        raise ValueError('Resumed scientific history changed its journal-bound accepted sequence')
    from . import formal_review
    formal_review.validate_job(root,job)
    from . import execution_authority
    execution_authority.validate_job(root, job)
    from . import qualification
    qualification.validate_inputs(root, job)
    if 'source_provenance' in job and job.get('source_restrictions') != qualification.status(root, job):
        raise ValueError('Frozen source restriction view changed')
    if job.get('experiment_mode')!=settings(root, job).get('experiment_mode'):
        raise ValueError('Frozen experiment mode differs from its original settings')
    selected=settings(root, job).get('execution_config')
    if job.get('execution_config_record')!=selected:
        raise ValueError('Frozen execution selection differs from its original settings')
    if job['role']=='W' and job.get('worker_fragment_policy')!=settings(root, job).get('worker_fragment_policy'):
        raise ValueError('Frozen W continuation configuration differs from its original settings')
    if job.get('candidate_nomination_policy') is not None and job['candidate_nomination_policy']!=settings(root, job).get('candidate_nomination_policy'):
        raise ValueError('Frozen candidate nomination configuration changed')
    if job.get('audit_projection_version') == 1:
        if (job['history'].get('audit_projection_version') != 1
                or job['history'].get('adjacent_audit') != job.get('candidate_audit')):
            raise ValueError('Frozen audit projection differs from its task boundary')
    if job.get('handoff_authority_version') == 1:
        if job.get('decision_context') != _decision_context(root, job):
            raise ValueError('Scientific handoff source authority changed')
        if job.get('role_guidance') and job.get('native_role_instruction') != job['role_guidance']:
            raise ValueError('Native role instruction differs from its frozen scientific guidance')
    _validate_task_view(root,work,job)
    if job['global_materials'] != global_materials(root):
        raise ValueError('Bound global context changed')
    policy = settings(root, job)
    if job.get('purpose') == 'closeout':
        from . import budget, delivery
        boundary = job.get('closeout_begin') or {}
        events, _ = budget._events(root)
        begin = next((event for event in events if event['kind'] == 'closeout_begin' and event['seq'] == boundary.get('seq')), None)
        if (job['purpose'] != 'closeout' or job['role'] not in {'A','W'} or job['role']=='W' and job.get('engineering_repair') is None or begin is None
                or job.get('source_cutoff') != begin['source_cutoff'] or job['step'] < begin['first_step']
                or job.get('closeout_begin') != dict(seq=begin['seq'],record=record_ref(root/'control-ledger'/f'{begin["seq"]:06d}.json'))):
            raise ValueError('Closeout task boundary changed')
        from . import engineering
        if not job.get('final_only'):
            expected_sources = begin['delivery_sources']
            repaired = [number for number in engineering.steps(root,job['step']) if number > begin['source_cutoff']]
            if repaired:
                nodes = [*expected_sources['source_nodes'], *[dict(step=number,
                    seal_ref=record_ref(host_record(workspace(root,number))/'result-identity.json')) for number in repaired]]
                expected_sources = delivery.catalogue_sources(root,nodes)
            if job.get('delivery_sources') != expected_sources:
                raise ValueError('Closeout candidate originals differ from their immutable preparation')
        expected_steps = [*budget.scientific_steps(root,job['source_cutoff']+1), *sorted(int(ref['path'].split('/')[2])
            for ref in job['direct_refs'] if int(ref['path'].split('/')[2]) > job['source_cutoff'])]
        if [row['step'] for row in job['history']['nodes']] != expected_steps:
            raise ValueError('Closeout history differs from its frozen science prefix and direct authors')
        delivery.validate_sources(root, job['delivery_sources'], job=job)
        if job['delivery_sources'].get('catalogue'):
            from . import candidate_refs
            catalogue=candidate_refs.public_fields(dict(items=job['delivery_sources']['items'],
                omitted_count=job['delivery_sources']['omitted_count']),candidate_refs.for_job(job))
            if read(regular(work/'inputs/delivery-candidates.json'))!=catalogue:
                raise ValueError('Frozen candidate catalogue changed')
        from . import shortlist
        if job.get('selection') != (shortlist.marker(root,job['step']) if job['role']=='A' and policy.get('selection_policy') is not None else None):
            raise ValueError('Closeout comparison differs from its frozen control screening')
    from . import engineering
    engineering.validate_job(root,job)
    from . import correction
    correction.validate_job(root, job)
    from . import semantic
    semantic.validate_job(root, job)
    if job.get('purpose') is None and not job.get('final_report') and not job.get('engineering_repair') and policy.get('search_policy') is not None:
        from . import search
        if job.get('search') != search.bind(root, job['step'], job['role'], job['handoff_protocol'], unified=True):
            raise ValueError('Frozen scientific search binding changed')
    from . import history
    history.validate(root, work, job['history'])
    if output_quota_exceeded(policy,(row['size_bytes'] for row in job['direct_materials'])):
        raise ValueError('Combined input materials exceed the frozen handoff bound')
    expected = []
    previous = f'inputs/parents/{job["step"] - 1:03d}/conclusion.json'
    if job.get('formal_review'):
        expected=formal_review.input_binding(root,job['formal_review'])['selected']
    elif any(ref['path'] == previous for ref in job['direct_refs']):
        from . import originals
        expected = read(originals.source_record(originals.node(root, job['step'] - 1),
                                'result-identity.json')).get('selected_inputs', [])
    if job.get('selected_inputs', []) != expected:
        raise ValueError('Bound implementation selection differs from its predecessor')
    from . import originals
    previous_seal = read(originals.source_record(originals.node(root, job['step'] - 1), 'result-identity.json')) if any(
        ref['path'] == previous for ref in job['direct_refs']) else {}
    if job.get('input_selection_status') != previous_seal.get('control_outcome', {}).get('selection_status', 'applied'):
        raise ValueError('Bound implementation resolution differs from its predecessor')
    if job.get('control_diagnostics') != previous_seal.get('control_outcome', {}).get('diagnostics', []):
        raise ValueError('Bound control diagnostics differ from their predecessor')
    if any(row['material'] not in job['direct_materials'] for row in expected):
        raise ValueError('Selected implementation is absent from bound materials')
    for row in job['global_materials']:
        originals.resolve_root_ref(root, dict(row,path=row['source']))
        if (work/row['path']).exists() and regular(work/row['path']).stat().st_size!=row['size_bytes']:
            raise ValueError('The global context read by this role changed')
    for ref in job['direct_refs']:
        originals.resolve_root_ref(root,dict(ref,path=str(work/ref['path'])))
    from . import candidate_refs
    if [row['path'] for row in job.get('direct_originals',[])]!=[row['path'] for row in job['direct_refs']]:
        raise ValueError('Scientific parent view lost its exact original')
    for ref in job['direct_originals']:
        source=regular(_execution_raw(root,int(Path(ref['path']).parts[2])))
        if source.stat().st_size!=ref['size_bytes']:
            raise ValueError('Direct producer original changed')
        reported=candidate_refs.public_science_fields(read_scientific_output(source),candidate_refs.for_job(job))
        original_path=str(Path(ref['path']).parent/'scientific-original.json')
        public=scientific_projection(reported,original_path)
        if public!=reported:
            originals.resolve_ref(originals.node(root,int(Path(ref['path']).parts[2])),
                read(originals.source_record(originals.node(root,int(Path(ref['path']).parts[2])),
                    'result-identity.json'))['raw_ref'])
        if read_scientific_output(originals.resolve_root_ref(root,dict(next(row for row in job['direct_refs']
                if row['path']==ref['path']),path=str(work/ref['path']))))!=public:
            raise ValueError('Scientific parent view differs from its verified original')
    for ref in job['direct_materials']:
        path = regular(originals.material_path(root, ref))
        if path.stat().st_size != ref['size_bytes']:
            raise ValueError('Scientific original changed')
    for ref in job['direct_materials']:
        if (work/ref['path']).exists() and regular(work/ref['path']).stat().st_size!=ref['size_bytes']:
            raise ValueError('The direct material actually read by this role changed')


@owner_effect
def seal_result(root: Path, step: int):
    from .protocol import require_current
    require_current(root,read(regular(Path(root)/'jobs'/f'{step:03d}.json')))
    work = workspace(root, step)
    record = host_record(work)
    job_path = root / 'jobs' / f'{step:03d}.json'
    job = read(job_path)
    if (record / 'result-identity.json').exists():
        result = validate(work)
        from . import history
        history.refresh(root, step + 1)
        return result
    from . import execution
    native_record = execution.record(root, step)
    intent = read(regular(native_record / 'native-intent.json'))
    if intent['task_ref'] != record_ref(regular(job_path)):
        raise ValueError('Native launch task changed')
    _validate_inputs(root, work, job)
    outcome = read(regular(native_record / 'native-outcome.json'))
    from . import worker_delivery
    worker_delivery.writers_stopped(root,job,native_record)
    native_raw_ref,accepted=_native_output_identity(root,step,native_record,intent,outcome)
    from . import budget
    final = budget.final_a_return(root, job, intent)
    if (type(outcome.get('returncode')) is not int or outcome['returncode'] != 0
            or outcome.get('stop_reason') and accepted is None
            or budget.final_a_usage_unknown(final, outcome['usage']) and not budget.native_completion_known(outcome, settings(root, read(regular(root / 'jobs' / f'{step:03d}.json'))))):
        raise ValueError('Terminal scientific output is unverified or changed')
    if native_record!=execution.origin(root,step):
        from . import author_closeout
        if intent.get('continuation_kind')=='author_closeout':
            author_closeout.validate_intent(root,job,intent)
            from . import native
            current_usage=native.usage(root)
            if (outcome['usage'].get('blocking_unknown_steps')!=current_usage.get('blocking_unknown_steps')
                    or current_usage.get('blocking_unknown_steps',current_usage['unknown_steps'])):
                raise ValueError('Author closeout lacks verified current usage accounting')
    raw_path = native_record / 'science-output.json'
    pending = native_record / 'result.pending.json'
    if outcome.get('output_status') == 'missing':
        if (outcome.get('raw_ref') is not None or raw_path.exists() or raw_path.is_symlink()
                or pending.exists() or pending.is_symlink()):
            raise ValueError('Missing final output identity changed')
        from .output_repair import OutputRepairRequired
        raise OutputRepairRequired('missing_output', 'Return the required final scientific JSON; the completed work and checkpoints are already retained.')
    raw = regular(raw_path)
    if native_raw_ref != file_ref(raw):
        raise ValueError('Terminal scientific output is unverified or changed')
    from . import candidate_refs
    value = envelope(job, candidate_refs.control_payload(job,science_payload(raw, job['role'], job['handoff_protocol'], 'selection' in job,**response_options(job)),root=root))
    if job['role']=='W':
        from . import managed_wait
        managed_wait.before_seal(root,job,native_record)
        if worker_delivery.applicable(job):
            from . import checkpoint
            if not (native_record/'checkpoint.json').exists():
                checkpoint.capture(root,job,native_record)
            else:checkpoint.validate(root,job,native_record)
        worker_delivery.require_delivery(root,job,value,native_record)
        from . import candidate_nominations
        candidate_nominations.require(root,job,value)
    control = None
    from . import semantic
    control = semantic.outcome(root, job, value)
    from . import correction
    correction.validate_payload(root, job, value)
    materials = collect_materials(root, step)
    unresolved = dict(items=[], omitted_count=0)
    entry_points = artifact_entry_points(work, value, materials, job['handoff_protocol'], unresolved)
    selected = selected_inputs(work, value, job)
    observations = None
    from . import history
    history.validate_recommendation(job, value, root=root)
    from .execution_resume import capture_pending
    # Freeze the final attempt only after delivery/repair gates, and before
    # result snapshots or conclusions. A busy namespace leaves these untouched.
    pending_executions = capture_pending(root, job)
    observations = history.capture(root, job, value, materials)
    if raw.stat().st_mode & 0o777 != 0o400:raw.chmod(0o400)
    from . import experiments
    raw_ref=experiments.proof(raw)
    private_materials=[dict(row,file_stat=experiments.proof(
        Path(row['source']))['file_stat']) for row in materials]
    write(record / 'result.json', scientific_projection(value,'node:%03d/scientific-output' % job['step']))
    seal = dict(result_ref=file_ref(record / 'result.json'), raw_ref=raw_ref,
          job_ref=record_ref(job_path), materials=private_materials, entry_points=entry_points,
          scope='Exact bytes and direct lineage; not scientific qualification',
          **(dict(completed_output_acceptance=accepted['receipt'],completed_output_limitation=accepted['facts']) if accepted is not None else {}),
          **(dict(execution_ref=execution.reference(root, step)) if execution.reference(root, step) is not None else {}),
          **(dict(control_outcome=control) if control is not None else {}),
          unresolved_links=unresolved,
          **(dict(selected_inputs=selected) if selected else {}),
          **(dict(observations=observations) if observations else {}))
    final_completion = _final_a_seal(native_record, outcome)
    if final_completion is not None:
        seal['final_a_completion'] = final_completion
    from . import execution_inputs
    compiled = execution_inputs.capture(root, job, private_materials)
    if compiled:
        seal['compiled_implementations'] = compiled
    if pending_executions is not None:
        seal['pending_executions'] = pending_executions
    if 'source_provenance' in job:
        from . import qualification
        seal['source_provenance'] = qualification.seal_outputs(root, job, seal)
    if job.get('formal_review_version')==1:
        from . import formal_review
        seal['native_authorship']=formal_review.capture_participants(root,job)
        if job.get('formal_review'):
            seal['formal_review']=formal_review.seal_review(root,job,value,seal)
    write(record / 'result-identity.json', seal)
    result = validate(work)
    history.refresh(root, step + 1)
    return result


def _execution_raw(root, step):
    from . import execution, originals
    if originals.inherited(root, step):
        return originals.source_raw(originals.node(root, step))
    return execution.raw(root, step)
