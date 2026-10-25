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
AUTHOR_EVIDENCE_GUIDANCE = (
    'Assess each original objective and authorized scope requirement against the available evidence; state what is supported, unsupported or unresolved. '
    'Tie key claims to locatable observations and their experiment conditions, distinguishing measurements, inferences and assumptions. '
    'Separate target metrics from proxies and sensitivity checks, and limit generalization to tested conditions. '
    'Compare candidates by the same goal criteria, addressing conflicting evidence, uncertainty and missing inputs without inventing facts. '
    'Compare plausible mechanisms before concentrating; justify any single-mechanism scope. Distinct parameter trials alone do not establish evidence across mechanisms. '
    'At each global judgment, compare relevant unexcluded routes, including screened, using the full mechanism index, original A reasons, observed investment and unresolved discriminating questions. Explain concentration, postponement and exclusion in scientific_result or route_reason. '
    'Budget for useful breadth before deep local work: read work/budget-status.json for current exploration availability, retain declared finite call and stage limits, and explain when resources or missing evidence prevent a comparison. Call and cycle counts are not scientific coverage or resource costs. '
    'For each waived mechanism, give an evidence-supported scientific negative or infeasibility reason tied to the original goal and constraints. '
    'Temporary relative underperformance or missing evidence calls for deferral rather than scientific rejection; explain concentration and remaining alternatives in the existing scientific result and disposition reasons. '
    'Scientific choice and conclusions belong to A; control supplies no candidate ranking or default. Explicit delivery requires your exact candidate and scientific rationale. '
    'When recommending a candidate, fill selection_explanation with the comparison criteria and an explicit unchanged/revised declaration against prior H/W design and report conditions, thresholds and fallback rules. Relaxing a predeclared condition or fallback is revised, even if a report already adopted it. If revised, acknowledge the prior criteria, what changed, locatable evidence explaining why, and accepted tradeoffs. A first selection may honestly state that no prior criteria were declared. '
    'Completed workflow, audit qualification, budget exhaustion, finish and delivery do not establish an unmeasured goal. State unmet objectives when stopping; no mechanical proof or hash work is required.')
ROLE_INSTRUCTIONS = {
    'H': 'Own the scientific direction toward the original global goal. Propose or revise mechanisms, '
         'distinguishing hypotheses from implementation choices. Decide whether evidence extraction is needed, '
         'then choose experiment or independent analysis. Preserve original constraints and unmet questions.',
    'W': 'Work within the bound hypothesis scope. Choose implementation and bounded experiments yourself. '
         'Preserve observations, failures, candidate files and limitations. A new mechanism belongs to H. '
         'Return your contribution for independent A; do not declare global completion.',
    'A': 'Independently judge the contribution to the original global goal, not only local improvement. '
         'Check evaluation blind spots, contrary evidence and unexplored mechanisms. Request further W experiments '
         'within H, return to H for a mechanism revision, request evidence, wait or support a scoped conclusion. '
         'A self-built proxy alone does not establish performance under unobserved target conditions. '
         'deliver explicitly authorizes adapter delivery; finish ends scientific exploration. Neither asserts global completion. '
         'extract requests scientific evidence only; control resolves, packages and submits exact originals.',
    'E': 'Answer the exact evidence question from the requesting H or A. Retrieve or extract only needed materials '
         'using available functional tools. Preserve original sources, locators, conflicts and missing evidence. '
         'Return to the requester; do not choose the research direction or assert qualification. '
         'Mechanical candidate copying, packaging, path resolution and submission belong to the delivery adapter.',
}


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


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
    if job['role']=='A' and ordinary:
        if policy.get('correction_policy') is not None:fields.append('progress_assessment')
        if policy.get('decision_contract_version')==1:fields.append('decision_request')
    if job['role']=='W':
        from . import worker_delivery
        if worker_delivery.applicable(job):fields.append('mechanism_findings')
        if job.get('candidate_nomination_mechanisms'):fields.append('candidate_nominations')
    return fields


def selection_explanation_schema():
    """Require an accountable declaration, never a machine scientific verdict."""
    text=dict(type='string',pattern=r'\S')
    variants=[]
    for status in ('unchanged','revised'):
        fields=dict(status=dict(type='string',enum=[status]))
        fields.update({field:dict(text) if status=='revised' else dict(type='null')
            for field in ('prior_criteria','changes','evidence','accepted_tradeoffs')})
        variants.append(dict(type='object',properties=fields,required=list(fields),additionalProperties=False))
    properties=dict(criteria=text,criteria_change=dict(anyOf=variants))
    return dict(type=['object','null'],properties=properties,required=list(properties),additionalProperties=False,
        description='Required for an explicit candidate recommendation, including final A. Compare adopted criteria with prior H/W design/report conditions, thresholds and fallback rules. Relaxing any such rule is revised even if the report already adopted it. Revised requires prior criteria, changes, locatable evidence explaining why, and accepted tradeoffs. Unchanged uses null change details. Control checks completeness, not scientific sufficiency or truth.')


def result_schema(role: str,protocol=CURRENT,comparison=False,*,formal_review=False,review_enabled=False,final_only=False,final_delivery=False,auxiliary_fields=None):
    """comparison: an A bound to a frozen candidate catalogue (job['selection'])."""
    from .protocol import _current
    _current(protocol,'Scientific result schema')
    if role not in ROLE_INSTRUCTIONS:raise ValueError('Unknown scientific role')
    if formal_review:
        from . import formal_review as review
        return review.schema()
    properties=dict(summary=dict(type='string'),scientific_result=dict(type='string'),
        limitations=dict(type='array',items=dict(type='string')),
        implementation_selection=dict(type=['array','null'],
            items=dict(type='string'),
            description='Null retains the selected implementation; [] clears it; listed scientific object IDs replace it. No paths, hashes or handoff markers.'))
    if role in ACTIONS:
        properties.update(next_action=dict(type='string',enum=list(actions(role,review_enabled=review_enabled))),route_reason=dict(type='string'))
        if role == 'A':
            properties['next_action']['description'] = ('deliver explicitly authorizes adapter delivery of candidate_recommendation; '
                'finish ends scientific exploration without granting delivery or asserting global completion. '
                'extract requests scientific evidence from E, never copying, packaging, path resolution or submission. '
                'audit is optional: check audit_capability.available; a disabled request returns unavailable without execution. '
                'formal_review, when enabled, is conditional: unchanged unavailable subject/identity is rejected before sealing for same-session output repair, not another A turn. A changed exact selection or newly verifiable identity may request it again. '
                'During closeout use deliver, audit, finish or wait; other actions receive bounded clarification because exploration is closed.')
    if final_only:
        properties['next_action']['enum'] = ['deliver'] if final_delivery else ['finish']
        properties['next_action']['description'] = ('Submit one explicit existing candidate through the host; no further scientific role, audit, wait or correction is authorized.' if final_delivery else
            'Return the final conclusion and explicit unsubmitted reason because candidate originals or delivery permission are unavailable.')
        properties['scientific_result']['description'] = 'Give your final evidence-based conclusion, exact selection or missing input, and explain unmet objectives. Never claim a host submission result; control supplies its actual receipt.'
    if role=='W':
        from . import worker_delivery, candidate_nominations
        properties['mechanism_findings']=worker_delivery.schema()
        properties['candidate_nominations']=candidate_nominations.schema()
        properties['stage_intent']=dict(type='string',enum=['continue','review','decision'],
        description='Continue the same scientific stage, report completed work for review, or request an A scientific decision with partial work. This does not grant budget or execution. Control may end the stage at a review or shared boundary.')
    if role in {'H','A'}:
        from . import search
        properties['search_plan' if role=='H' else 'search_review']=search.schema(role)
    if role=='A':
        from . import correction,decision
        properties.update(candidate_recommendation=dict(type=['string','null'],
                description='Choose an exact candidate reference from the frozen candidate index, or its listed implementation reference when both identify that same registered object. Null supplies no recommendation. No default or host selection. Explain scientific rationale and evidence in scientific_result.'),
            progress_assessment=correction.schema(),decision_request=decision.schema())
        properties['selection_explanation']=selection_explanation_schema()
        if final_only and final_delivery:
            properties['candidate_recommendation'].update(type='string',minLength=1)
        if comparison:
            from . import shortlist
            properties['candidate_comparison']=shortlist.schema()
    if auxiliary_fields is not None:
        for field in ('mechanism_findings','candidate_nominations','search_plan','search_review','progress_assessment',
                'decision_request','candidate_comparison'):
            if field not in auxiliary_fields:properties.pop(field,None)
    return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)


def scientific_field(job, payload, field):
    """Consume one optional field with the task's sole model-visible rule."""
    import jsonschema
    schema=result_schema(job['role'],job.get('handoff_protocol',CURRENT),'selection' in job,**response_options(job))
    rule=schema['properties'].get(field)
    if rule is None:return None
    value=payload.get(field)
    if not json_field_representable(field,value) or not jsonschema.Draft202012Validator(rule).is_valid(value):return None
    return value


def scientific_diagnostics(job, payload):
    schema=result_schema(job['role'],job.get('handoff_protocol',CURRENT),'selection' in job,**response_options(job))
    return [dict(code='scientific_field_unresolved',subject=field) for field,rule in schema['properties'].items()
        if isinstance(rule.get('type'),list) and 'null' in rule['type']
        and payload.get(field) is not None and scientific_field(job,payload,field) is None]


def science_payload(path: Path, role: str, protocol=CURRENT, comparison=False,*,formal_review=False,review_enabled=False,final_only=False,final_delivery=False,auxiliary_fields=None):
    import jsonschema
    from .output_repair import OutputRepairRequired
    original = regular(path)
    try:
        value = read(original)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise OutputRepairRequired('invalid_json', 'Return one valid JSON object matching final_response; completed experiments remain retained.') from error
    try:
        schema=result_schema(role,protocol,comparison,formal_review=formal_review,review_enabled=review_enabled,
            final_only=final_only,final_delivery=final_delivery,auxiliary_fields=auxiliary_fields)
        projection={key:item for key,item in value.items() if key in schema['properties']} if isinstance(value,dict) and not formal_review else value
        if isinstance(projection,dict) and not formal_review:
            if role=='A':
                explanation=projection.get('selection_explanation')
                rule=schema['properties']['selection_explanation']
                if (isinstance(projection.get('candidate_recommendation'),str) and explanation is None
                        or not jsonschema.Draft202012Validator(rule).is_valid(explanation)):
                    raise OutputRepairRequired('selection_explanation_incomplete',
                        'Fill selection_explanation: state criteria and unchanged/revised status; revised requires prior_criteria, changes, evidence with locators explaining why, and accepted_tradeoffs. Keep the same A task and completed evidence; do not repeat experiments.')
            for field,rule in schema['properties'].items():
                if isinstance(rule.get('type'),list) and 'null' in rule['type']:
                    if not jsonschema.Draft202012Validator(rule).is_valid(projection.get(field)):projection[field]=None
                    else:projection.setdefault(field,None)
        jsonschema.Draft202012Validator(schema).validate(projection)
    except jsonschema.ValidationError as error:
        location = '.'.join(map(str, error.absolute_path))[:128] or 'final response'
        raise OutputRepairRequired('invalid_fields', 'Correct required fields and types in ' + location + ' using final_response; do not repeat experiments.') from error
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
            payload=read(actual)
            projected=scientific_projection(candidate_refs.public_science_fields(payload,
                candidate_refs.for_job(job)),str(Path(ref['path']).parent/'scientific-original.json'))
            if projected!=payload:raise ValueError('Bound public parent projection is unavailable')
            path=actual
        if path.stat().st_size!=ref['size_bytes']:
            raise ValueError('Direct scientific original changed')
        payload=read(path)
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
            if protocol >= 7 and (normalized.is_absolute() or '..' in normalized.parts
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
        if job.get('handoff_protocol', 1) >= 8 and job.get('input_selection_status') == 'unresolved':
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
        if job.get('handoff_protocol', 1) >= 7 and row['path'] in material_seen:
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
    source = science_payload(_execution_raw(root, previous), source_job['role'], source_job.get('handoff_protocol', 1), 'selection' in source_job,**response_options(source_job))
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
    if job.get('fork_source') is not None:
        from . import fork_runtime
        return fork_runtime.selected_science_view(root, job)
    from . import formal_review, user_guidance
    if job.get('formal_review'):
        view = dict(formal_review.public(root,job), **user_guidance.public(job))
        view['review'] = {key: value for key, value in view['review'].items() if key != 'instruction'}
        return view
    protocol = job.get('handoff_protocol', 1)
    context = job.get('decision_context')
    question = job['instruction']
    if context is not None:
        question = ("Choose observation-driven experiments within the bound H scientific questions and user constraints; use A's global direction and evidence gap."
                    if job['role'] == 'W' else "Revise the qualitative mechanism matrix and discriminating design in light of A's global assessment; preserve original constraints.")
    if job.get('scientific_work_unit'):
        question += '\nWork unit: ' + job['scientific_work_unit']
    unified = job.get('lineage_projection_version') == 1
    if unified and job.get('selection_protocol', 0) != job['history']['view']['candidates'].get('selection_protocol', 0):
        raise ValueError('Frozen selection projection differs from its task marker')
    request = None
    if protocol >= 8 and job.get('author_correction'):
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
            selection='Choose a frozen candidate ID for delivery; null means no decision, not retention. Report scientific reasoning and limitations.',
            sources=delivery.public_sources(job['delivery_sources'], job['history']['view']['candidates'].get('adapter_audits', []),
                nominated_ids=job['history'].get('candidate_nomination_ids')),
            actions=('Select an existing candidate and authorize its original host submission. Give the final conclusion and unmet objectives; control supplies the actual submission receipt. No further scientific work.' if job.get('final_only') else
                'deliver authorizes delivery; audit obtains qualification; finish declines delivery; wait pauses. No new H/W/E work during closeout.'))
        if job['delivery_sources'].get('catalogue'):
            closeout['sources']['catalogue'] = 'inputs/delivery-candidates.json'
        if job.get('selection') is not None:
            from . import shortlist
            closeout.update(selection='Compare scientifically relevant alternatives by the same goal criteria. Explicitly name the exact candidate and give scientific rationale and evidence steps; null supplies no choice.',
                            comparison=shortlist.public_table(root, job['selection']))
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
            choices=formal_review.public_choices(root,job),
            instruction='formal_review requests an independent assessment of the single implementation selected through the existing scientific selection. Check formal_review_feedback for the current restriction and ordinary alternatives. The schema retains formal_review for a changed exact subject or newly verifiable identity; unchanged failure requires same-session output repair without sealing or dispatching another A. Control binds subject and identity; an unavailable response grants no qualification.'),
            formal_review_feedback=formal_review.feedback(root,job))
           if job.get('formal_review_version')==1 else {}),
        **(dict(source_restrictions=job['source_restrictions']) if 'source_restrictions' in job else {}),
        **(dict(execution_authority=dict(status='trusted_gate_required' if settings(Path(root), job).get('execution_resource_policy') else 'authority_only_production_gate_unavailable',
            qualified_materials=[read(Path(root) / ref['path'])['subject']['material']['path']
                                 for ref in job.get('execution_qualifications', [])[:3]],
            qualified_materials_omitted_count=max(0, len(job.get('execution_qualifications', [])) - 3),
            instruction='Choose scientific materials through the existing selection. Control binds exact review and execution identities; ordinary pass text grants nothing. Decisions alone do not dispatch or add budget.'))
           if job.get('execution_authority') is not None else {}),
        **(dict(engineering_repair=engineering.public(job['engineering_repair'])) if job.get('engineering_repair') else {}),
        **(dict(repair_observation=dict(mechanism_ref=job['search']['focus']['mechanism_ref'],
            source_step=job['search']['repair_source']['step'],call_id=job['search']['repair_source']['call']['call_id'],
            authorization_step=job['search']['repair_authorization']['step'],
            scope='Restore this exact observation under the current H scope. Old findings retain their original H version and limitations.'))
           if (job.get('search') or {}).get('repair_source') is not None else {}),
        **(dict(stage_continuation=dict(status='configured' if job.get('worker_fragment_policy') else 'unavailable',
            context=(job.get('worker_fragment_policy') or {}).get('context'),
            permission='Configured context is not permission: control verifies original terminal, cleanup, usage, session and remaining shared authorization before accepting another fragment.',
            intent='review reports completed assigned work; decision sends a scientific obstacle to A as partial; continue requests another fragment only when configured; otherwise control ends the stage with an explicit unavailable reason.')) if job['role']=='W' else {}),
        **(dict(candidate_nomination_policy=job['candidate_nomination_policy'],
            candidate_nomination_mechanisms=job['candidate_nomination_mechanisms'])
           if job.get('candidate_nomination_mechanisms') else {}),
        **(dict(decision_context=context) if context is not None else {}),
        'global': dict(objective='inputs/global/objective.md',
                       updates=[row['path'] for row in job['global_materials'][1:]]),
        'public_root': job['public_root'],
        **(dict(project=dict(base_root=job['project']['base_root'],
            editable=job['project']['editable'], scratch=job['project']['scratch'], outputs=job['project']['outputs'],
            base_view='Frozen Git HEAD tracked files only; untracked and ignored files are unavailable.',
            code='Author only within declared editable directories. Managed runs use one retained code version.',
            seed={'selected_commit':'Draft initialized from the exact selected original code version.',
                  'frozen_base':'Draft initialized from frozen base editable files because no unique original code version was selected.',
                  'existing_draft':'Existing current draft was preserved; it is not asserted to match a parent commit.'}.get(
                      job.get('project_code_seed',{}).get('source')),
            writes='Use declared scratch for temporary files and outputs for retained results. Other project paths are readonly; move failed writes into the declared directories.'))
           if job.get('project') is not None else {}),
        **(dict(configured_delivery=job['configured_delivery'],
            delivery_requirements={
                'W':'Identify the actual deliverables and native evidence required by the original task. Preserve the needed evaluated objects and original receipts in existing declared outputs before temporary work is removed; source code or a metric alone may not satisfy the task. Retain necessary results, not every intermediate, and report missing items without manufacturing them. Do not write hashes or mechanical inventories.',
                'A':'Compare the original task deliverables with the retained outputs and native receipts, and with configured_delivery.effect_scope. A configured adapter can perform only its declared effects; unknown scope provides no assurance. Do not expect directory copying to train or perform task-native submission or audit. Scientific completion is your judgment. You may deliver the best existing partial result, including in FINAL, while explicitly explaining missing deliverables, unsupported requirements and actual submission evidence. Scope descriptions grant no execution permission or scientific qualification.'
            }.get(job['role'])) if 'configured_delivery' in job else {}),
        'parents': parents,
        'reading_order':reading[job['role']]+' Preserve listed limitations. Parents are authored statements, not execution facts. Verify relevant originals as needed, delegating when native permissions allow; omissions use conclusion/index/next_page.',
        **(dict(reused_implementations=[dict(label=row['label'], path=row['material']['path'])
                                       for row in job.get('selected_inputs', [])],
                 implementation_reuse='Choose only listed scientific objects; control resolves their originals.')
           if job.get('handoff_protocol', 1) >= 2 else {}),
        **(dict(implementation_options=job.get('implementation_options', []),
                implementation_options_omitted_count=job.get('implementation_options_omitted_count', 0),
                scientific_links='Ordinary citations are scientific prose. Only listed bound files are available; '
                'unresolved links do not select inputs or acquire evidence qualification.') if protocol >= 7 and not unified else {}),
        **(dict(lineage=_role_lineage(job)) if unified else {}),
        **(dict(history=job['history']['view']) if job.get('handoff_protocol', 1) >= 3 and not unified else {}),
        **(dict(input_selection_status=job['input_selection_status'], control_diagnostics=job['control_diagnostics'])
           if job.get('handoff_protocol', 1) >= 8 else {}),
        **(dict(control_request=request)
           if job.get('handoff_protocol', 1) >= 8 and job.get('author_correction') else {}),
        **(dict(closeout=closeout) if closeout is not None else {}),
        **(dict(candidate_audit=audit) if audit is not None else {}),
        **(dict(audit_capability=audit_capability) if audit_capability is not None else {}),
        **(dict(delivery_selection='Read the frozen candidate catalogue and compare relevant alternatives by the original goal criteria. Your deliver authorizes only your exact candidate; finish declines delivery. Null supplies no delivery choice.')
           if job['role'] == 'A' and root is not None and settings(Path(root), job).get('selection_policy') is not None else {}),
        **(dict(delivery_comparison=shortlist.public_table(root,job['selection']))
           if job['role']=='A' and job.get('purpose') is None and job.get('selection') is not None else {}),
        **(dict(search=job['search']['view']) if job.get('search') and not unified else {}),
        **(dict(progress_assessment=('Judge the current assessed work as progress/no_progress/unknown; '
                'null means unknown. Explain scientific support and limitations in ordinary science. '
                'The host binds the exact assessed task for counting, not scientific verification. Do not write evidence paths.' if protocol >= 7 else
                'Judge global contribution as progress/no_progress/unknown with '
                'few exact bound prior conclusion/history-page paths; null means unknown. Never use '
                'your own new prose or unsealed files as progress evidence. Scientific explanations '
                'belong in scientific_result and limitations; the host counts sealed decisions.'))
           if 'progress_assessment' in result_schema(job['role'],protocol,'selection' in job,**response_options(job))['properties'] else {}),
        **(dict(decision_request='For scientific continuation, declare the decision, question and result that would change it. '
                'Exact completed requests without new bound evidence require clarification; this is not a scientific value judgment.')
           if 'decision_request' in result_schema(job['role'],protocol,'selection' in job,**response_options(job))['properties'] else {}),
        'final_response': list(result_schema(job['role'], job.get('handoff_protocol', 1), 'selection' in job,**response_options(job))['properties']),
        **(dict(experiment_execution=('Host-configured registered W execution is available; only calls through the provided experiment command are recorded. Native scratch shell remains available.'
            if job['experiment_mode'] == 'local' else 'External launcher owns experiment configuration; native shell commands are not automatically recorded.'))
            if 'experiment_mode' in job else {}),
        **(dict(managed_execution_wait=job['managed_execution_wait'])
            if 'managed_execution_wait' in job else {}),
        'outputs': 'artifacts/', 'scratch': 'work/',
    }.items() if value}
    from . import candidate_refs
    return candidate_refs.public_fields(view,candidate_refs.for_job(job))


def collect_materials(root: Path, step: int):
    """Seal this stopped node's own deliverables at their actual output paths."""
    job=read(regular(root/'jobs'/f'{step:03d}.json'))
    policy = settings(root, job)
    work = workspace(root, step)
    rows, total = [], 0
    private_packages=set()
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
            context=read(author_closeout._check(root,proof))
            private_packages={author_closeout._check(root,item['original']) for item in context.get('artifact_masks',[])}

    def add(path, relative, *, source_kind='mutable'):
        nonlocal total
        path = regular(path)
        if path in private_packages:return  # Explicit compiler/author grant; never infer from a filename.
        size = path.stat().st_size
        if policy.get('max_output_item_bytes') is not None and size > policy['max_output_item_bytes']:
            raise ValueError(f'Deliverable {relative!r} exceeds item byte budget '
                             f'({size} > {policy["max_output_item_bytes"]})')
        total += size
        if policy.get('max_output_total_bytes') is not None and total > policy['max_output_total_bytes']:
            raise ValueError(f'Deliverable {relative!r} exhausts total byte budget '
                             f'({total} > {policy["max_output_total_bytes"]})')
        if source_kind=='mutable':
            if path.resolve()!=path or not path.is_relative_to(work):
                raise ValueError('Node deliverables must remain in their original owned workspace')
            if path.stat().st_nlink != 1:
                raise ValueError(f'Mutable deliverable {relative!r} must not have hard-link aliases')
            if path.stat().st_mode & 0o777 != 0o400:path.chmod(0o400)
        elif path.stat().st_mode & 0o777 != 0o400:
            raise ValueError('Managed output has not been sealed by its stopped writer')
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
            outputs=history.scientific_outputs(root,job,call) if job.get('worker_view_version')==1 else call.get('outputs',[])
            for item in outputs:
                if item.get('retention')!='archived':raise ValueError('Managed deliverable original unavailable')
                add(root/item['path'],f'experiments/{call["call_id"]}/artifacts/{item["name"]}',source_kind='managed_frozen')
    if len({row['path'] for row in rows}) != len(rows):
        raise ValueError('Deliverable paths collide with nested experiment outputs')
    return sorted(rows, key=lambda row: row['path'])


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
            records=[row for row in project_layout.files(project,context=context)
                if any(row['relative'].startswith(name+'/') for name in project['editable'])]
        directories={parent.as_posix() for row in records for parent in Path(row['relative']).parents
            if str(parent)!='.'}
        for name in project['editable']:
            directories.add(name)
            directories.update(parent.as_posix() for parent in Path(name).parents if str(parent)!='.')
        inventory=storage.Inventory(policy,context=context)
        for _ in directories:inventory.entry()
        for row in records:
            inventory.entry();inventory.file(row['signature'][3])
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
    worker_view_version=1 if retained is None or retained.get('worker_view_version')==1 else 0
    protocol=CURRENT
    authority_version=projection_version=selection_protocol=1
    audit_projection_version=1 if policy.get('candidate_audit_policy') else 0
    review_binding=None
    if (evidence_return or {}).get('kind')=='formal_review':
        from . import formal_review
        if role!='A':raise ValueError('Formal review requires its native A node')
        review_binding=formal_review.input_binding(root,evidence_return['request'])
        audit_projection_version=0
    work = workspace(root, step)
    work.mkdir(parents=True, exist_ok=True)
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
        if worker_view_version!=1 and (work/ref['path']).exists():
            regular(work/ref['path'])
        (work / Path(ref['path']).parent / 'artifacts').mkdir(parents=True,exist_ok=True)
        refs.append(ref)
        from . import originals
        parent_seal = read(originals.source_record(originals.node(root, parent), 'result-identity.json'))
        materials.extend(_public_materials(root, parent_seal['materials']))
        entry_points.extend(parent_seal.get('entry_points', []))
        # Reuse is an instruction to the immediate next node, not persistent
        # baggage on a retained H ancestor in every later scientific cycle.
        if protocol >= 2 and parent == step - 1:
            selected.extend(parent_seal.get('selected_inputs', []))
    unique = {}
    for item in [*materials, *[row['material'] for row in selected]]:
        if item['path'] in unique and unique[item['path']] != item:
            raise ValueError('Selected input paths conflict')
        unique[item['path']] = item
    materials = list(unique.values())
    if protocol >= 2 and output_quota_exceeded(policy,(row['size_bytes'] for row in materials)):
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
               **(dict(handoff_protocol=protocol) if protocol >= 2 else {}))
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
    # New views are frozen with the task; rebuilding a legacy task must retain
    # its original bytes and projection rather than silently upgrade it.
    if worker_view_version == 1:
        job['worker_view_version'] = 1
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
    if authority_version == 1:
        job['handoff_authority_version'] = 1
        from . import guidance
        job['native_role_instruction'] = (retained['native_role_instruction'] if retained is not None
                                          else advice if advice is not None else (guidance.OPPORTUNITY if protocol >= 12 else guidance.DEFAULT)[role])
    if audit_projection_version == 1:
        job['audit_projection_version'] = 1
    if selection_protocol == 1:
        job['selection_protocol'] = 1
    if projection_version == 1:
        job['lineage_projection_version'] = 1
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
    if protocol >= 9 and role == 'A' and step - 1 in parents and review_binding is None:
        from . import candidate_audit
        receipt = candidate_audit.receipt_ref(root, step - 1)
        if receipt is not None:
            job['candidate_audit'] = receipt
    state = None
    from . import history
    history_cutoff = final_report['source_cutoff'] if final_report else source_cutoff
    extra_steps = [parent for parent in parents if history_cutoff is not None and parent > history_cutoff]
    from . import lineage
    if review_binding is not None:
        job['history']=review_binding['history'];options=[]
    else:
        job['history'],state=lineage.bind(root,step,role,protocol,source_cutoff=final_report['source_cutoff'] if final_report else source_cutoff,extra_steps=extra_steps,selection_protocol=selection_protocol,
            audit_projection_version=audit_projection_version,adjacent_audit=job.get('candidate_audit'),
            worker_view_version=job.get('worker_view_version',0))
        options = [item for parent in parents for item in history.implementation_options(root, parent)]
    if worker_view_version==1:
        from . import candidate_refs
        job['direct_originals']=refs
        job['direct_refs']=[]
        for original in refs:
            parent=int(Path(original['path']).parts[2]);source=regular(_execution_raw(root,parent))
            if source.stat().st_size!=original['size_bytes']:raise ValueError('Direct producer original changed')
            reported=candidate_refs.public_science_fields(read(source),candidate_refs.for_job(job))
            original_path=str(Path(original['path']).parent/'scientific-original.json')
            public=scientific_projection(reported,original_path)
            # A logical raw link resolves to the sealed parent original.
            if public!=reported and not any(row['path']==original_path for row in job['history']['files']):
                job['history']['files'].append(dict(path=original_path,source=str(source),
                    size_bytes=original['size_bytes'],kind='scientific_original'))
            target=work/original['path']
            if target.exists():
                if read(regular(target))!=public:raise ValueError('Frozen parent scientific view changed')
            elif public!=read(source):
                write(target,public);target.chmod(0o400)
            if source.stat().st_size!=original['size_bytes']:raise ValueError('Direct producer original changed')
            job['direct_refs'].append(dict(path=original['path'],
                size_bytes=target.stat().st_size if target.exists() else original['size_bytes']))
    job['implementation_options'] = options[:16]
    job['implementation_options_omitted_count'] = max(0, len(options) - 16)
    if projection_version == 1 and review_binding is None:
        job['history']['view']['implementation_options'] = [dict(id=row['id'], label=row['label'].encode()[:80].decode('utf-8',errors='ignore')) for row in options[:2]]
        job['history']['view']['implementation_options_omitted_count'] = max(0,len(options)-2)
        lineage.bounded_brief(job['history']['view'])
    if purpose is None and not job.get('formal_review') and final_report is None and engineering_repair is None and protocol >= 5 and policy.get('search_policy') is not None:
        from . import search
        job['search'] = search.bind(root, step, role, protocol, state=state, unified=projection_version == 1)
    if role=='A' and purpose is None and review_binding is None and final_report is None and policy.get('selection_policy') is not None:
        from . import shortlist
        job['selection']=shortlist.bind_normal(root,job)
    if role=='W' and job.get('search') is not None and policy.get('candidate_nomination_policy') is not None:
        job['candidate_nomination_policy']=policy['candidate_nomination_policy']
        if (job['search'].get('focus') or {}).get('action')=='screen':
            job['candidate_nomination_mechanisms']=[dict(mechanism_ref=ref,status=item['status'])
                for ref,item in state['mechanisms'].items()]
        else:
            job['candidate_nomination_mechanisms']=[dict(mechanism_ref=row['mechanism_ref'],status=row['status'])
                for row in (job['search'].get('breadth') or {}).get('mechanisms',[])]
    if retained is None or 'scientific_response_fields' in retained:
        job['scientific_response_fields']=response_fields(job,policy)
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
    if authority_version == 1:
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
                            (work / 'science-task.json', science_view(job, root)),
                            (host_record(work) / 'science-result.schema.json', result_schema(role, protocol, 'selection' in job,**response_options(job)))):
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
        if settings(root, job).get('final_a_completion_policy') is not None or outcome.get('final_a_completion') is not None or (intent.get('budget_slice') or {}).get('final_return') is not None:
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
        job.get('handoff_protocol', 1), 'selection' in job, **response_options(job)), root=root, source=source)
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
    if job.get('scientific_response_fields') != response_fields(job, policy):
        raise ValueError('Source response differs from its exact original settings')
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
            or native_raw_ref != {key:seal['raw_ref'][key] for key in ('path','size_bytes')}
            or read(regular(work / 'science-task.json')) != science_view(job, root)):
        raise ValueError('Scientific node identity changed')
    if (conclusion.exists() or conclusion.is_symlink()) and regular(conclusion).stat().st_size!=seal['raw_ref']['size_bytes']:
        raise ValueError('Saved scientific conclusion projection changed')
    from . import candidate_refs
    payload = _sealed_science(job, seal, raw, result, root=root)
    if seal.get('control_outcome') is not None:
        from . import semantic
        if seal['control_outcome'] != semantic.outcome(root, job, payload):
            raise ValueError('Control outcome differs from its original scientific attempt')
    else:
        raise ValueError('Current scientific attempt has no control outcome')
    from . import correction
    correction.validate_payload(root, job, payload)
    if read(result) != result_projection(job, payload):
        raise ValueError('Result envelope differs from author science')
    unresolved = dict(items=[], omitted_count=0)
    if seal.get('entry_points', []) != artifact_entry_points(work, payload, seal['materials'], job.get('handoff_protocol', 1), unresolved):
        raise ValueError('Scientific deliverable entry points changed')
    if job.get('handoff_protocol', 1) >= 7 and seal.get('unresolved_links') != unresolved:
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


def _validate_inputs(root, work, job):
    if job.get('fork_source') is not None:
        from . import fork_runtime, qualification, formal_review, engineering, execution_authority
        fork_runtime.validate_selected_job(root, work, job)
        if job.get('scientific_response_fields') != response_fields(job, settings(root, job)):
            raise ValueError('Frozen scientific response consumers changed')
        qualification.validate_inputs(root, job)
        execution_authority.validate_job(root, job)
        formal_review.validate_job(root, job)
        engineering.validate_job(root, job)
        return
    if 'scientific_response_fields' in job and job['scientific_response_fields']!=response_fields(job,settings(root, job)):
        raise ValueError('Frozen scientific response consumers changed')
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
    if read(regular(work / 'science-task.json')) != science_view(job, root):
        raise ValueError('Frozen scientific task changed')
    if job['global_materials'] != global_materials(root):
        raise ValueError('Bound global context changed')
    policy = settings(root, job)
    if job.get('purpose') == 'closeout':
        from . import budget, delivery
        boundary = job.get('closeout_begin') or {}
        events, _ = budget._events(root, settings(root))
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
    if job.get('purpose') is None and not job.get('final_report') and not job.get('engineering_repair') and job.get('handoff_protocol', 1) >= 5 and policy.get('search_policy') is not None:
        from . import search
        if job.get('search') != search.bind(root, job['step'], job['role'], job['handoff_protocol'], unified=job.get('lineage_projection_version') == 1):
            raise ValueError('Frozen scientific search binding changed')
    from . import history
    history.validate(root, work, job['history'])
    if job.get('handoff_protocol', 1) >= 2 and output_quota_exceeded(policy,(row['size_bytes'] for row in job['direct_materials'])):
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
    if job.get('worker_view_version')==1:
        from . import candidate_refs
        if [row['path'] for row in job.get('direct_originals',[])]!=[row['path'] for row in job['direct_refs']]:
            raise ValueError('Scientific parent view lost its exact original')
        for ref in job['direct_originals']:
            source=regular(_execution_raw(root,int(Path(ref['path']).parts[2])))
            if source.stat().st_size!=ref['size_bytes']:
                raise ValueError('Direct producer original changed')
            reported=candidate_refs.public_science_fields(read(source),candidate_refs.for_job(job))
            original_path=str(Path(ref['path']).parent/'scientific-original.json')
            public=scientific_projection(reported,original_path)
            if public!=reported:
                originals.resolve_ref(originals.node(root,int(Path(ref['path']).parts[2])),
                    read(originals.source_record(originals.node(root,int(Path(ref['path']).parts[2])),
                        'result-identity.json'))['raw_ref'])
            if read(originals.resolve_root_ref(root,dict(next(row for row in job['direct_refs']
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
        history.refresh(root, step + 1) if job.get('purpose') != 'closeout' and not job.get('final_report') else None
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
    if intent.get('continuation_kind') == 'startup_successor':
        from .startup_recovery import validate_intent
        validate_intent(root,job,intent)
    from . import candidate_refs
    value = envelope(job, candidate_refs.control_payload(job,science_payload(raw, job['role'], job.get('handoff_protocol', 1), 'selection' in job,**response_options(job)),root=root))
    if intent.get('continuation_kind') == 'startup_successor' and value.get('next_action') not in {'deliver','finish'}:
        from .output_repair import OutputRepairRequired
        raise OutputRepairRequired('invalid_final_action','Return the final conclusion with deliver or an explicit unsubmitted finish; no new scientific turn is authorized.')
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
    entry_points = artifact_entry_points(work, value, materials, job.get('handoff_protocol', 1), unresolved)
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
          **(dict(unresolved_links=unresolved) if job.get('handoff_protocol', 1) >= 7 else {}),
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
    history.refresh(root, step + 1) if job.get('purpose') != 'closeout' and not job.get('final_report') else None
    return result


def _execution_raw(root, step):
    from . import execution, originals
    if originals.inherited(root, step):
        return originals.source_raw(originals.node(root, step))
    return execution.raw(root, step)
