"""Thin scientific packets and exact direct-node seals; no MCP dispatch protocol.

Only the controller supplies identities and lineage. Seals bind original paths,
producers and declared sizes, not content fingerprints or scientific validity.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
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
from . import guidance, delivery_review
HANDOFF_PROTOCOL = CURRENT
AUDIT_PROJECTION_VERSION = 1
ACTIONS = {
    'H': ('experiment', 'analyze', 'extract', 'wait'),
    'A': ('continue_w', 'revise_h', 'extract', 'wait', 'finish', 'deliver'),
}
MAX_FIXED_RESPONSE_BYTES = 3072
AUTHOR_EVIDENCE_GUIDANCE = guidance.ASSESSMENT_SCOPE
NATIVE_TASK_INSTRUCTION = (
    'Read science-task.json/exact inputs; honor goal/checks/scope/permissions/budget. '
    'Return JSON; no hashes/manifests/validators.')
PORTFOLIO_RESPONSE = (
    'After W except wait: portfolio_review={main_line:ref|null,reason,discriminating_work:null|{mechanism_refs,question,next_action:continue_w|revise_h|extract}}. '
    'Optional work_updates[{work_ref,action:execute|defer|withdraw,reason}]; omitted=keep. '
    'Execute on work.next_action; continue_w focus in work refs.')
FINAL_COVERAGE_RESPONSE = (
    'finish/deliver: final_coverage[{mechanism_ref,status:deep|screened_pending|ruled_out|unexplored,reason}] for all refs.')
IMPLEMENTATION_REUSE = (
    'implementation_selection: null=keep; []=clear; current/history IDs=next-node inputs.')


ROLE_INSTRUCTIONS = {
    'H': guidance.DEFAULT['H'],
    'W': guidance.DEFAULT['W'],
    'A': guidance.DEFAULT['A'],
    'E': 'Answer the exact evidence question from the requesting H or A. Retrieve or extract only needed materials '
         'using available functional tools. Preserve original sources, locators, conflicts and missing evidence. '
         'Return to the requester; do not choose the research direction or assert qualification. '
         'Mechanical candidate copying, packaging, path resolution and submission belong to the delivery adapter.',
}


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def job_record(job):
    """Canonical private job form; expanded material facts stay operation local."""
    from . import material_references
    return material_references.job_record(job)


def read_job(path, *, sources=None, base=None, source=None):
    """Own one current job read and explicitly resolve its selected material lists."""
    from . import handoff_state, originals
    path = Path(path)
    root = originals.context_root(source) if source is not None else path.parent.parent
    return handoff_state.material_inputs(root, read(regular(path)),
        sources={} if sources is None else sources, base=base, source=source)


def native_outcome(record, *, original_record=None, output_path=None):
    """Acquire this exact owner's compact facts once; never rewrite originals."""
    from . import backends
    record=Path(record)
    outcome=read(regular(record/'native-outcome.json'))
    if not _native_outcome_candidate(outcome):return outcome
    try:
        meter=read(regular(record/'usage.json'))
        evidence=backends.outcome_provider(outcome).acquire_terminal_evidence(record,outcome,output_path=output_path)
    except (OSError,ValueError,TypeError,KeyError,UnicodeError,RecursionError):return outcome
    return _native_outcome(record,outcome,meter=meter,terminal_evidence=evidence,
        original_record=original_record,output_path=output_path)


def _native_outcome_candidate(outcome):
    from . import backends
    try:return backends.outcome_provider(outcome).terminal_reconciliation_candidate(outcome)
    except (ValueError,TypeError,KeyError):return False


def _native_outcome(record,outcome,*,meter,original_record=None,output_path=None,terminal_evidence=None):
    """Pure interpretation of already-produced facts; absent facts stay unknown.

    The producer owns exact output acquisition. Consumers must pass that small
    evidence value explicitly and never reopen the native stream or final bytes.
    ``output_path`` remains a compatibility argument and confers no evidence.
    """
    from . import backends
    if not _native_outcome_candidate(outcome):return outcome
    try:
        return backends.outcome_provider(outcome).reconcile_terminal(outcome,meter=meter,
            evidence=terminal_evidence,original_record=Path(original_record or record))
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):return outcome


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


def read_scientific_source(path: Path):
    """Read one original once, retaining its exact text and parsed author view."""
    text = path.read_text(encoding='utf-8')
    payload, normalization = decode_scientific_output(text)
    return dict(raw_text=text, payload=payload, normalization=normalization)


def read_scientific_output(path: Path):
    """Read syntax only; sealed conclusions retain their accepted interpretation."""
    return read_scientific_source(path)['payload']


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
        from . import accepted_budget
        base = settings(root)
        head = accepted_budget.load(root, base=base) if job.get('configuration_revision') is not None else None
        return settings_for_job(root, job, base=base, head=head)
    path = regular(root / 'settings.json')
    identity = read(regular(root / 'settings-identity.json'))
    # Parse the exact frozen control locator. No content fingerprint is computed.
    data = path.read_bytes()
    if {key:value for key,value in identity.items() if key!='cold_owner'} != dict(path='settings.json'):
        raise ValueError('Frozen instance settings changed')
    return json.loads(data)


def settings_for_job(root, job, *, base, head):
    """Select one exact policy using the operation owner's existing facts."""
    from . import originals, configuration_updates
    if (not job.get('fork_source') and type(job.get('step')) is int and job['step'] > 0
            and originals.inherited(root, job['step'])):
        return originals.source_settings(originals.node(root, job['step']))
    return configuration_updates.policy_for_job(root, job, base=base, head=head)


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
    """Legacy byte-quota compatibility; complete original files have no byte cap."""
    for size in sizes:
        if type(size) is not int or size<0:raise ValueError('Original material size must be a nonnegative integer')
    return False


def actions(role,protocol=CURRENT,*,audit_enabled=True,review_enabled=False):
    from .protocol import _current
    _current(protocol,'Scientific result schema')
    choices = (*ACTIONS[role],'audit') if role=='A' and audit_enabled else ACTIONS.get(role,())
    return (*choices, 'formal_review') if role=='A' and review_enabled else choices


def response_options(job):
    return dict(formal_review=bool(job.get('formal_review')), review_enabled=job.get('purpose')!='worker_consultation' and job.get('formal_review_version')==1 and job.get('formal_review_choices',{}).get('launcher_supported',True),
        final_only=job.get('final_only',False), final_delivery=bool((job.get('delivery_sources') or {}).get('items')),
        auxiliary_fields=job.get('scientific_response_fields'),
        final_decline=job.get('research_strategy_version') in {2,3} and bool(job.get('final_only')),
        worker_consultation_question=job.get('research_strategy_version')==3,
        delivery_review=(delivery_review.binding(job) or {}).get('corrections_used') if delivery_review.scope(job)=='delivery_review' else None,
        delivery_correction=delivery_review.scope(job)=='delivery_correction')


def response_fields(job, policy):
    """Actual optional consumers of this task, frozen with its existing job."""
    fields=[];ordinary=guidance.purpose(job)=='assessment'
    if job.get('search') is not None and job['role'] in {'H','W'}:
        fields.append('search_plan')
    if job['role']=='A' and (ordinary or delivery_review.scope(job)=='delivery_review'):
        if policy.get('correction_policy') is not None:fields.append('progress_assessment')
        fields.append('decision_request')
        if job.get('supervision_request') is not None:fields.append('supervision_result')
    if job['role']=='W':
        if job.get('research_strategy_version')!=3:fields.append('exploration_rounds')
        if job.get('research_strategy_version')==3:fields.append('research_memory')
        from . import worker_delivery
        if worker_delivery.applicable(job):fields.append('mechanism_findings')
        if job.get('candidate_nomination_mechanisms'):fields.append('candidate_nominations')
    return fields


def supervision_binding(value, *, step, research_strategy_version=None):
    """Copy the public host-frozen direction scope; consume no external facts."""
    keys={'version','cutoff','due_reasons','budget_fraction','automatic','final'}
    if isinstance(value,dict) and 'investment_cutoff' in value and research_strategy_version==3:
        keys.add('investment_cutoff')
    if not isinstance(value,dict) or set(value)!=keys or type(value['version']) is not int or value['version']!=1:
        raise ValueError('Direction supervision request is invalid')
    cutoff=value['cutoff']
    if (not isinstance(cutoff,dict) or set(cutoff)!={'total_rounds','source_step','round_id'}
            or type(cutoff['total_rounds']) is not int or cutoff['total_rounds']<0
            or cutoff['source_step'] is not None and (type(cutoff['source_step']) is not int or not 0<=cutoff['source_step']<step)
            or cutoff['round_id'] is not None and (not isinstance(cutoff['round_id'],str) or not cutoff['round_id'].strip())
            or not isinstance(value['due_reasons'],list)
            or any(not isinstance(reason,str) or not reason.strip() for reason in value['due_reasons'])
            or value['budget_fraction'] is not None and (type(value['budget_fraction']) not in (int,float)
                or not 0<=value['budget_fraction']<float('inf'))
            or any(type(value[key]) is not bool for key in ('automatic','final'))
            or 'investment_cutoff' in value and (type(value['investment_cutoff']) is not int or value['investment_cutoff']<0)
            or not json_field_representable('supervision_request',value)):
        raise ValueError('Direction supervision request is invalid')
    return dict(value,cutoff=dict(cutoff),due_reasons=list(value['due_reasons']))


def result_schema(role: str,protocol=CURRENT,comparison=False,*,formal_review=False,review_enabled=False,final_only=False,final_delivery=False,auxiliary_fields=None,final_decline=False,delivery_review=None,delivery_correction=False,worker_consultation_question=False):
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
        if final_only:choices=(['deliver','finish'] if final_delivery else ['finish']) if final_decline else ['deliver'] if final_delivery else ['finish']
        elif delivery_review is not None:choices=['deliver','finish']+(['continue_w'] if delivery_review==0 else [])
        properties.update(next_action=dict(type='string',enum=choices),route_reason=dict(type='string'))
    if role=='W':
        properties['stage_intent']=dict(type='string',enum=['continue','handoff','decision'] if delivery_correction else ['continue','handoff','revise_h','review','decision'])
        if delivery_correction:properties['search_plan']=dict(type='null')
    required=[key for key in properties if not (delivery_correction and key=='search_plan')]
    return dict(type='object',properties=properties,required=required,additionalProperties=True)


def response_instructions(job):
    """The actual fixed output instructions shown in the scientific task."""
    # Ordinary required fields/types are already carried by the Codex renderer
    # menu or the protocol response_schema. Keep one transport declaration.
    fields=dict(implementation_reuse=IMPLEMENTATION_REUSE)
    contract=job.get('role_purpose_instruction',guidance.role_contract(job))
    if contract:fields['role_contract']=contract
    if job.get('formal_review'):
        fields['final_response']=list(result_schema(job['role'],job['handoff_protocol'],
            'selection' in job,**response_options(job))['properties'])
    if job.get('search') is not None and job['role'] in {'H','W'}:
        fields['breadth_response']=(
            'search_plan:commitments[{label,existing_ref?}],focus?{index:1-based|null,action:screen|improve}; null=breadth; omissions retain.'
            +' index:null+screen=breadth; focus:null=keep.'
            +(' Experiment needs top-level search_plan commitments+focus if none retained.' if job['role']=='H' else ''))
    if job.get('purpose')=='worker_consultation':
        fields['implementation_reuse']=('The requesting W input materials and implementation selection carry forward unchanged. '
            'Consultation implementation_selection is retained as authored text only; it cannot clear or replace those inputs.')
    if job['role']=='A' and job.get('purpose')!='worker_consultation':
        fields['delivery_selection']='deliver: candidate_recommendation+rationale; output/receipt gaps; partial OK.'
        if job.get('research_strategy_version') in (1,2,3) and guidance.purpose(job)=='assessment':
            fields['delivery_selection']='deliver/finish requests final A assessment; candidate_recommendation+rationale if available; retain output/receipt gaps.'
        if job.get('purpose')=='closeout':
            fields['closeout_response']=('FINAL: follow action options; no new work/audit.' if job.get('final_only') else 'Closeout: deliver/audit/finish/wait; no exploration.')
    elif job['role']=='W':
        fields.pop('implementation_reuse')
        context=(job.get('worker_fragment_policy') or {}).get('context')
        # A fragment continues this logical W job; configured context controls
        # native thread reuse; the authored intent chooses the next serial job.
        fields['stage_response']=('stage_intent:continue=same W;handoff=seal partial/revisions/new W;revise_h=H/review=A advice to W;decision=A assessment. '
            'implementation_selection:null=keep;[]=clear;IDs=next inputs. '
            'mechanism_findings?:[{mechanism,basis:observed|blocked,finding,call_id?}];'
            'exploration_rounds?:[{mechanism,question,call_ids:[],finding}];'
            'candidate_nominations?:{representatives:[{mechanism_ref,candidate}]}. '
            'Rounds:nonblank question/finding;current W managed IDs;never infer from calls/turns. '
            'Resume:'+(context if isinstance(context,str) else json.dumps(context,ensure_ascii=False,separators=(',',':'))
                if job.get('worker_fragment_policy') else 'unavailable')+' needs terminal/session/known usage/authorization.')
        if job.get('research_strategy_version') in (1,2,3):
            fields['stage_response']=('stage_intent:continue=W;handoff=seal/revise W;revise_h=H;'
                'review=ordinary A reflection;decision=ready to close: protected Final A required before delivery. Advice returns W; no grant. '
                'implementation_selection:null=keep;[]=clear;IDs=inputs. ')
            enabled=job.get('scientific_response_fields')
            for name,rule in (
                ('mechanism_findings','mechanism_findings?:[{mechanism,basis:observed|blocked,finding,call_id?}]. '),
                ('exploration_rounds','exploration_rounds?:[{mechanism,question,call_ids:[],finding}]; nonblank question/finding, current W managed IDs; never infer rounds. '),
                ('candidate_nominations','candidate_nominations?:{representatives:[{mechanism_ref,candidate}]}. ')):
                if enabled is None or name in enabled:fields['stage_response']+=rule
            fields['stage_response']+=('Continuation:'+(context if isinstance(context,str) else
                json.dumps(context,ensure_ascii=False,separators=(',',':')) if job.get('worker_fragment_policy')
                else 'unavailable')+'; session/usage/authorization bind.')
        if job.get('research_strategy_version')==3:
            # Live --research admission carries v3 mechanisms and batch purpose.
            # The frozen H-first search_plan hint and legacy round report are
            # not this strategy's managed execution/measurement interface.
            fields.pop('breadth_response',None)
            fields['stage_response']=('stage_intent:continue=W;handoff=new W;revise_h=H advice;review=A advice;decision=pre-delivery Final A. Advice returns W; no grant. '
                'revise_h/review require nonblank consultation_question. '
                'implementation_selection:null=keep;[]=clear;IDs=inputs. '
                'research_memory?:{question,candidates:[{candidate,data,evaluation,evidence:[{ref,relation:supports|opposes|unknown,note}],failure_condition,open_question,limitations:[]}]}; exact candidate/ref originals; [] may be exploratory. '
                'Continuation:'+(context if isinstance(context,str) else json.dumps(context,ensure_ascii=False,separators=(',',':'))
                    if job.get('worker_fragment_policy') else 'unavailable')+'; session/usage/authorization bind.')
    scoped=delivery_review.binding(job)
    if scoped is not None and scoped['kind']=='delivery_review':
        fields['delivery_selection']=('deliver=accept exact existing candidate_recommendation with rationale; finish=decline all with no recommendation and truthful limits. '
            +('continue_w=one bounded decision-changing gap; concrete decision_request required; W chooses methods.' if scoped['corrections_used']==0 else
              'Correction consumed: no further work route.'))
    elif scoped is not None:
        fields.pop('breadth_response',None)
        fields['stage_response']=('stage_intent:continue=same bounded correction W;handoff=retain this exact gap in a new W context;decision=one final A reassessment. '
            'Resolve only the requesting A decision_request using existing authorization; W chooses methods. No H/A consultation or new search; search_plan:null/omitted. '
            'implementation_selection:null=keep;[]=clear;IDs=inputs. exploration_rounds?:[{mechanism,question,call_ids:[],finding}]; current W managed IDs only. Original budgets and unknown-call reconciliation hold.')
        if job.get('research_strategy_version')==3:
            fields['stage_response']=fields['stage_response'].replace(
                'exploration_rounds?:[{mechanism,question,call_ids:[],finding}]; current W managed IDs only. ','')
    elif job.get('research_strategy_version') in {2,3} and job['role']=='W':
        fields['stage_response']=fields['stage_response'].replace('decision=ready to close: protected Final A required before delivery.',
            'decision=one reversible pre-delivery Final A: accept/decline/one bounded correction within original budgets.')
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
    if field=='supervision_result':
        return value if isinstance(value,str) and value in {'continue_existing','request_evidence','finalize'} else None
    if field=='exploration_rounds':
        return value if isinstance(value,list) else None
    if field=='candidate_nominations':return value
    if field=='research_memory':return value if isinstance(value,dict) else None
    if field in {'search_plan','search_review','candidate_comparison','portfolio_review','selection_explanation'}:
        return value if isinstance(value,dict) else None
    if field in {'mechanism_findings','final_coverage'}:
        return value if isinstance(value,list) else None
    return value


def scientific_diagnostics(job, payload):
    return [dict(code='scientific_field_unresolved',subject=field)
        for field in job.get('scientific_response_fields',[])
        if field!='exploration_rounds'
        if payload.get(field) is not None and scientific_field(job,payload,field) is None]


def science_payload(path: Path, role: str, protocol=CURRENT, comparison=False,*,formal_review=False,review_enabled=False,final_only=False,final_delivery=False,auxiliary_fields=None,final_decline=False,delivery_review=None,delivery_correction=False,worker_consultation_question=False):
    import jsonschema
    from .output_repair import OutputRepairRequired
    original = regular(path)
    try:
        value = read_scientific_output(original)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise OutputRepairRequired('invalid_json', 'Return one complete valid JSON object matching final_response; repair syntax only, preserving original scientific facts/plans/limits. Reuse prior results only if they exist; no work this repair turn.') from error
    schema=result_schema(role,protocol,comparison,formal_review=formal_review,review_enabled=review_enabled,
        final_only=final_only,final_delivery=final_delivery,auxiliary_fields=auxiliary_fields,
        final_decline=final_decline,delivery_review=delivery_review,delivery_correction=delivery_correction)
    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError as error:
        location = '.'.join(map(str, error.absolute_path))[:128] or 'final response'
        if error.validator=='required':
            missing=[key for key in error.validator_value if key not in error.instance]
            detail='missing required field '+', '.join(missing)
        elif error.validator=='type':detail='expected '+str(error.validator_value)
        elif error.validator=='enum':detail='expected one of '+', '.join(map(str,error.validator_value))
        else:detail='must satisfy '+str(error.validator)
        raise OutputRepairRequired('invalid_fields', 'Correct '+location+': '+detail+'. Return corrected JSON; do not repeat experiments.') from error
    if (final_only and final_delivery or delivery_review is not None) and value.get('next_action')=='deliver' and (not isinstance(value.get('candidate_recommendation'),str)
            or not value['candidate_recommendation'].strip()):
        raise OutputRepairRequired('candidate_recommendation_missing',
            'FINAL delivery requires an explicit existing candidate_recommendation. Keep the same task and retained evidence; do not run new work.')
    if worker_consultation_question and role=='W' and value.get('stage_intent') in {'revise_h','review'}:
        question=value.get('consultation_question')
        if not isinstance(question,str) or not question.strip():
            raise OutputRepairRequired('consultation_question_missing',
                'Correct consultation_question: give the concrete nonblank question for H/A using retained candidates, originals and unresolved gaps; no new experiments.')
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


def _parent_view(job, ref, *, source=None, limitation_bytes=1024):
    directory = str(Path(ref['path']).parent / 'artifacts')
    entries = [row for row in job.get('direct_entry_points', [])
               if row['path'].startswith(directory + '/')]
    view = dict(conclusion=ref['path'], artifacts=directory)
    if entries:
        view['evidence'] = entries[:16]
        if len(entries) > 16:
            view['evidence_omitted_count'] = len(entries) - 16
    if source is not None:
        if ref not in job.get('direct_refs',[]):raise ValueError('Parent view has no exact task binding')
        payload=source['public']
        view['role']=source['facts']['job']['role']
        view['limitations'],view['limitations_omitted_count']=_whole_limitations(payload['limitations'],byte_limit=limitation_bytes)
        member=source['member']
        view['history_alias']='inputs/history/'+str(Path(member['navigation']['path']).parent/'science.json')
    return view


def _load_parent_source(root, step, *, base, sources, members, prefix, resolve_choices=False):
    from . import handoff_state, accepted_prefix
    if step not in sources:
        sources[step]=handoff_state.source_facts(root,step,base=base,resolve_choices=resolve_choices)
    elif 'payload' not in sources[step] or resolve_choices and 'control' not in sources[step]:
        handoff_state.source_facts_from_sealed(root,sources[step],resolve_choices=resolve_choices)
    if step not in members:
        members[step]=accepted_prefix.member(root,prefix,step)
    if members[step]['seal_ref'] != sources[step]['seal_ref']:
        raise ValueError('Assessment source is outside its exact frozen history')
    return sources[step]


def _parent_public(job, ref, *, source):
    from . import candidate_refs
    reported=candidate_refs.public_science_fields(source['payload'],candidate_refs.for_job(job))
    return reported, scientific_projection(reported,str(Path(ref['path']).parent/'scientific-original.json'))


def _accept_parent_public(root, job, ref, *, source):
    """Accept the selected public file once, using the already parsed original."""
    _, public=_parent_public(job,ref,source=source)
    target=workspace(root,job['step'])/ref['path']
    if target.exists():
        path=regular(target)
        if path.stat().st_size != ref['size_bytes']:
            raise ValueError('Direct scientific original changed')
        if read_scientific_output(path) != public:
            raise ValueError('Scientific parent view differs from its verified original')
    elif public != source['payload']:
        raise ValueError('Bound public parent projection is unavailable')
    elif ref['size_bytes'] != source['seal']['raw_ref']['size_bytes']:
        raise ValueError('Direct scientific original changed')
    return public


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
    return local if local.startswith('experiments/') else 'artifacts/'+local


def artifact_entry_points(work, payload, materials, protocol=CURRENT, unresolved=None):
    """Resolve explicit inline scientific links, never infer paths from prose."""
    from .protocol import _current
    _current(protocol, 'Scientific artifact links')
    targets={}
    for row in materials:
        local=material_name(work,row)
        names=(local,'artifacts/'+local) if local.startswith('experiments/') else (local,)
        for name in names:
            if name in targets and targets[name]!=row['path']:
                raise ValueError('Scientific entry point is ambiguous')
            targets[name]=row['path']
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
        if targets[path] not in seen:
            entries.append(dict(label=label[:160], path=targets[path]))
            seen.add(targets[path])
    return entries


def _public_materials(materials):
    """Project descriptors whose exact membership was accepted by their owner."""
    return [{key:value for key,value in material.items() if key!='file_stat'} for material in materials]


def _accept_selected_inputs(work, payload, job, diagnostics=None, *, _policy=None, _sources=None):
    """Resolve explicit reuse intent against this role's already frozen inputs."""
    from .protocol import require_job_format
    require_job_format(job)
    root = work.parents[2]
    bound = {row['path']: row for row in job['direct_materials']}
    selected, seen = [], set()
    sources = {} if _sources is None else _sources
    choices = []
    choice = None if job.get('purpose')=='worker_consultation' else payload.get('implementation_selection')
    if choice is not None and (not isinstance(choice,list) or any(not isinstance(item,str) for item in choice)
            or not json_field_representable('implementation_selection',choice)):
        if diagnostics is not None:diagnostics.append(dict(code='implementation_unresolved',subject='unparseable explicit selection'))
        return []  # Invalid explicit choice never inherits or partly applies.
    if choice is None:
        if job.get('input_selection_status') == 'unresolved':
            if diagnostics is not None:
                diagnostics.append(dict(code='implementation_unresolved', subject='inherited unresolved choice'))
            return []
        return job.get('selected_inputs', [])
    else:
        from . import history
        unresolved = []
        for object_id in dict.fromkeys(choice):
            try:
                from . import candidate_refs
                canonical=candidate_refs.resolve_reference(root,job,object_id)
                if candidate_refs.is_available(root,job,canonical):
                    matches=[item['material'] for item in history.candidate_materials(root,job,candidate_id=canonical)]
                    if len(matches)!=1:raise history.UnresolvedImplementation('Candidate has no unique exact implementation')
                    row=matches[0]
                else:row = history.resolve_implementation(root, object_id, job['history']['prefix'])
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
        from . import handoff_state
        if origin_step not in sources:
            owner = originals.node(root, origin_step)
            sources[origin_step] = handoff_state.sealed_facts(root, origin_step, owner=owner)
        facts = sources[origin_step]
        identity = facts['seal']
        originals.bound_material_path(root, row, source=facts['owner'], seal=identity)
        if source.resolve() != source.absolute():
            raise ValueError('Selected original implementation changed or is not sealed')
        selected.append(dict(label=label[:160], origin_step=origin_step,
            origin_result_ref=identity['result_ref'], material=row))
    from . import material_references
    return material_references.bind_selected(selected, sources=sources)


def selected_inputs(inventory):
    """Read the current owner's accepted selection; never inspect materials."""
    return inventory.selected_inputs


def _relayed_materials(job, payload, selected):
    """Keep current inputs through direction, assessment and exact E returns."""
    requester = job.get('evidence_return')
    extraction_return = (job['role'] == 'E' and isinstance(requester, dict)
        and requester.get('role') in {'A', 'H'} and type(requester.get('step')) is int
        and 0 < requester['step'] < job['step']
        and any(ref['path'] == f'inputs/parents/{requester["step"]:03d}/conclusion.json'
            for ref in job['direct_refs']))
    if ((job['role'] not in {'A','H','W'} and not extraction_return) or job.get('purpose') not in {None,'worker_consultation'}
            or any(job.get(key) for key in ('final_report', 'final_only', 'formal_review',
                'author_correction', 'correction_request'))):
        return None
    if job.get('purpose')=='worker_consultation' or payload.get('implementation_selection') is None:
        return [] if job.get('input_selection_status') == 'unresolved' else job['material_refs']
    groups = []
    for row in selected:
        ref = row['material_ref']
        group = next((item for item in groups if item['list_ref'] == ref['list_ref']), None)
        if group is None:
            group = dict(list_ref=ref['list_ref'], ids=[])
            groups.append(group)
        if ref['id'] not in group['ids']:
            group['ids'].append(ref['id'])
    return groups


def _material_options(materials, *, sources):
    """Expose current originals under their producer's unchanged object IDs."""
    indexes, options = {}, []
    for material in materials:
        step = int(PurePosixPath(material['path']).parts[2])
        if step not in indexes:
            facts = sources[step]
            indexes[step] = {row['path']: (index, row) for index, row in enumerate(
                sorted(_public_materials(facts['seal']['materials']), key=lambda row: row['path']), 1)}
        index, original = indexes[step][material['path']]
        if original != material:
            raise ValueError('Implementation option differs from its exact producer')
        options.append(dict(id=f'{sources[step]["job"]["role"]}{step:03d}-I{index:03d}',
            label=Path(material['path']).name.encode()[:160].decode('utf-8', errors='ignore'),
            path=material['path']))
    return options


def _decision_context(job, *, sources):
    """Label the exact immediate scientific source; do not parse its prose."""
    if (job['role'] not in {'W', 'H'} or job.get('purpose') is not None
            or job.get('author_correction') or job.get('correction_request')):
        return None
    previous = job['step'] - 1
    ref = next((row for row in job['direct_refs'] if row['path'] == f'inputs/parents/{previous:03d}/conclusion.json'), None)
    if ref is None:
        return None
    source_job, source = sources[previous]['job'], sources[previous]['payload']
    action = 'continue_w' if job['role'] == 'W' else 'revise_h'
    if (source_job['role'] != 'A' or source.get('next_action') != action
            or job['instruction'] != source.get('route_reason')):
        return None  # H directives, E questions/returns and control corrections keep their question.
    return dict(source_role='A', source_action=action, source_original=ref['path'],
                original_instruction=job['instruction'],
                authority='H offers revisable hypotheses/directions; A independently assesses evidence/claims. '
                'Concrete execution methods are proposals. Original user/task constraints, the frozen dispatch/catalog, '
                'explicitly assigned focused evidence gaps and execution permissions remain binding. W owns research direction/design/implementation/adaptation '
                'and may revise mechanisms; seal before a new W freezes revisions for execution.')


def _assessment_handoff(job, *, sources, members, policy):
    """Locate the prior decision through the exact W/H route, never recency."""
    if (job['role'] != 'A' or job.get('purpose') is not None or job.get('final_report')
            or job.get('final_only') or job.get('formal_review') or job.get('fork_source')
            or job.get('author_correction') or job.get('correction_request')):
        return None
    def source(step):
        facts = sources[step]
        if members[step]['seal_ref'] != facts['seal_ref']:
            raise ValueError('Assessment source is outside its exact frozen history')
        return facts['job']

    def payload(step):
        if 'payload' not in sources[step]:
            raise KeyError(step)
        return sources[step]['payload']

    parents = [(ref, source(int(Path(ref['path']).parts[2]))) for ref in job['direct_refs']]
    current = next((parent for _, parent in parents if parent['step'] == job['step'] - 1), None)
    if (current is None or current.get('engineering_repair')
            or current.get('author_correction') or current.get('correction_request')):
        return None
    view = {}
    for role in ('W', 'H'):
        refs = [ref['path'] for ref, parent in parents if parent['role'] == role]
        if len(refs) > 1:
            return None  # No implicit choice between scientific scopes.
        if refs:
            view['current_' + role.lower()] = refs[0]
    context = current.get('decision_context')
    previous_a = None
    relation = 'context_only'
    if current['role'] == 'E':
        requester = current.get('evidence_return') or {}
        if requester.get('role') == 'A':
            previous_a = next((parent for _, parent in parents if parent['step'] == requester.get('step')), None)
            if previous_a is not None:
                science = payload(previous_a['step'])
                if (science.get('next_action') != 'extract' or job['instruction'] != science.get('route_reason')
                        or not any(ref['path'] == f'inputs/parents/{previous_a["step"]:03d}/conclusion.json'
                                   for ref in current['direct_refs'])):
                    previous_a = None
    elif current['role'] == 'A':
        science = payload(current['step'])
        if science.get('next_action') == 'audit' and job['instruction'] == science.get('route_reason'):
            previous_a = current
    elif current['role'] not in {'W', 'H'}:
        return None
    if current['role'] in {'A', 'E'} and previous_a is None:
        return None
    if context is None and current['role'] == 'W':
        predecessor = current['step'] - 1
        if any(ref['path'] == f'inputs/parents/{predecessor:03d}/conclusion.json'
               for ref in current['direct_refs']):
            parent = source(predecessor)
            if parent['role'] == 'H':
                science = payload(predecessor)
                if science.get('next_action') == 'experiment' and current['instruction'] == science.get('route_reason'):
                    context = parent.get('decision_context')
    if context is not None:
        step = int(Path(context['source_original']).parts[2])
        previous_a = source(step)
        relation = 'scope_revision_context' if context['source_action'] == 'revise_h' else 'context_only'
    if previous_a is not None:
        prior = previous_a
        step = prior['step']
        if (prior['role'] == 'A' and not prior.get('purpose') and not prior.get('formal_review')
                and not prior.get('author_correction') and not prior.get('correction_request')):
            seal = sources[step]['seal']
            view['previous_a'] = dict(step=step,
                original=f'inputs/context/{step:03d}/scientific-original.json',
                relation=relation, materials=[row['path'] for row in seal['materials']])
            view['previous_a']['implementations']='inputs/history/'+members[step]['navigation']['path']
            # An accepted old job may have omitted these inputs under its then
            # active byte quota. Preserve that exact historical omission; new
            # jobs retain the complete material list without a byte cap.
            retained = (job.get('assessment_handoff') or {}).get('previous_a')
            if (isinstance(retained,dict) and retained.get('materials')==[]
                    and retained.get('materials_unavailable_reason')=='input_material_bound'
                    and {key:value for key,value in retained.items()
                         if key not in {'materials','materials_unavailable_reason'}}
                        == {key:value for key,value in view['previous_a'].items() if key!='materials'}):
                view['previous_a'].update(materials=[],materials_unavailable_reason='input_material_bound')
    return view


def _load_assessment_handoff(root, job, *, base, sources, members, policy):
    """Load only a source explicitly reached by this task's existing route."""
    from . import accepted_prefix, handoff_state
    while True:
        try:
            return _assessment_handoff(job,sources=sources,members=members,policy=policy)
        except KeyError as error:
            step=error.args[0]
            if type(step) is not int or not 0 < step < job['step']:
                raise
            if step not in sources:
                sources[step]=handoff_state.sealed_facts(root,step,base=base)
            elif step in members:
                if 'payload' in sources[step]:
                    raise
                handoff_state.source_facts_from_sealed(root,sources[step],resolve_choices=False)
            if step not in members:
                members[step]=accepted_prefix.member(root,accepted_prefix.for_job(job),step)


def _assessment_original(handoff, *, sources):
    prior = handoff.get('previous_a')
    if prior is None:
        return None
    source = sources[prior['step']]
    seal, raw = source['seal'], source['raw_path']
    return dict(path=prior['original'], source=str(raw), size_bytes=seal['raw_ref']['size_bytes'], kind='scientific_original')


def _role_lineage(job, parents=()):
    """Select role material from the same frozen view; indexes retain all facts."""
    import copy
    view=copy.deepcopy(job['history']['view'])
    breadth=(job.get('search') or {}).get('breadth')
    if job['role']=='W' and breadth is not None:
        rows=breadth['mechanisms']
        mechanisms=view['mechanisms']
        mechanisms['focus']=None
        mechanisms['authorized_count']=len(rows)
        mechanisms['overview']=[dict(number=str(row['ordinal']),mechanism_ref=row['mechanism_ref'],label=row['label'],
            status=row['status'],original='inputs/history/nodes/'+row['source_goal_id']+
                f'/{row["source_step"]:03d}/science.json') for row in rows[:4]]
        mechanisms['overview_omitted_count']=max(0,len(rows)-4)
        action=((job.get('search') or {}).get('focus') or {}).get('action')
        mechanisms['task_action']=action
        mechanisms['scope']=('Assigned shallow combination; each mechanism needs your own scientific finding or explicit obstacle.' if action=='screen' else
            'Assigned local '+str(action)+' task; findings and nominations cover only the assigned mechanisms. Do not repeat other mechanisms; their retained observations and gaps remain visible to A.')
        mechanisms['coverage_required_count']=len((job.get('search') or {}).get('coverage_obligation',[]))
        if 'admission_advice' in job['search']:
            mechanisms['admission']=job['search']['admission_advice']
        from .lineage import bounded_brief
        bounded_brief(view)
    # These routes share an exact sealed scientific producer, not necessarily
    # byte representation: history wraps the public conclusion in text pages.
    aliases={row['history_alias']:row['conclusion'] for row in parents if row.get('history_alias')}
    mechanisms=view['mechanisms']
    for rows in (view['recent'], mechanisms.get('overview',[]),
                 (mechanisms.get('unfinished_plan') or {}).get('remaining',[])):
        for row in rows:
            if row.get('original') in aliases:
                row['original']=aliases[row['original']]
    return view


def science_view(job, root=None):
    """Independent presentation entrypoint; build passes its existing facts."""
    parents={}
    qualified=[]
    if root is None or job.get('fork_source') is not None:
        policy={}
    else:
        root=Path(root)
        base=settings(root)
        from . import accepted_budget
        head=accepted_budget.load(root,base=base)
        policy=settings_for_job(root,job,base=base,head=head)
        if not job.get('formal_review'):
            from . import accepted_prefix
            sources,members={},{}
            for ref in job['direct_refs']:
                step=int(Path(ref['path']).parts[2])
                source=_load_parent_source(root,step,base=base,sources=sources,members=members,
                    prefix=accepted_prefix.for_job(job))
                parents[step]=dict(facts=source,member=members[step],
                    public=_accept_parent_public(root,job,ref,source=source))
        from . import formal_review
        owners=[row['facts']['owner'] for row in parents.values()]
        for reference in job.get('execution_qualifications', [])[:3]:
            _, proof=formal_review._receipt_original(root,reference,base=base,owners=owners)
            qualified.append(proof['subject']['material']['path'])
    return _science_view(job,root,parent_sources=parents,policy=policy,qualified_materials=qualified)


def consultation_view(job):
    """Project the existing purpose and exact return binding, without new state."""
    if job.get('purpose')!='worker_consultation':return None
    return dict(purpose='worker_consultation',
        **(dict(question=job['instruction']) if job.get('research_strategy_version')==3 else {}),
        requester=f'inputs/parents/{job["evidence_return"]["step"]:03d}/conclusion.json',
        return_role='W',scope='High-level advice on the exact W question; returned to a new W. No formal review verdict or execution grant.')


def _science_view(job, root, *, parent_sources, policy, qualified_materials):
    from .protocol import require_job_format
    require_job_format(job)
    if job.get('fork_source') is not None:
        from . import fork_runtime
        return fork_runtime.selected_science_view(root, job)
    from . import formal_review, user_guidance
    if job.get('formal_review'):
        view = dict(formal_review.public(root,job), **user_guidance.public(job))
        view['review'] = {key: value for key, value in view['review'].items() if key != 'instruction'}
        view['role_contract']=job.get('role_purpose_instruction',guidance.role_contract(job))
        return view
    protocol = job['handoff_protocol']
    context = job.get('decision_context')
    question = job['instruction']
    ordinary = (delivery_review.scope(job) is None and job.get('purpose') is None and not any(job.get(key) for key in
        ('final_report', 'final_only', 'engineering_repair', 'author_correction', 'correction_request')))
    action = ((job.get('search') or {}).get('focus') or {}).get('action')
    if context is not None and ordinary:
        if job['role'] == 'W':
            question = {
                'improve': 'Own sustained experiment design, algorithms and adaptation under original user/task constraints and this frozen dispatch/catalog; explore toward unmet goals using observations, residuals and local relations. Propose revisions; seal before a new W freezes them for execution.',
                'validate': 'Own validation design under original user/task constraints and this frozen dispatch/catalog; resolve the focused evidence gap without a new search. Propose revisions; seal before a new W freezes them for execution.',
                'screen': 'Own fair comparison of mechanisms relevant to current uncertainty under original user/task constraints and this frozen dispatch/catalog; reuse sufficient evidence. Propose revisions; seal before a new W freezes them for execution.',
            }.get(action, job['instruction'])
        else:
            question = ('Revise high-level scientific methods/directions using direct W observations/counterevidence and A assessment; '
                'separate provisional H judgments from original constraints, required counts and commitments.')
    elif ordinary and job['role'] == 'W' and action == 'improve':
        question += '\nW owns sustained research design/adaptation under original user/task constraints and this frozen dispatch/catalog; use observations/residuals toward unmet goals. Propose revisions; seal before a new W freezes them for execution.'
    if ordinary and job['role']=='W' and job.get('research_strategy_version')==3:
        question=job['instruction']+'\nOwn research and adapt in this W session. Declare/revise stable mechanisms and experiment_question before managed experiments; use retained originals and candidate memory. Initial exploration may have no candidate or mature hypothesis.'
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
        available = candidate_audit.enabled(policy)
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
        'H':('W','A','E','H'), 'W':('H','A','W','E'),
        'A':('W','E','H','A'), 'E':('A','H','W','E')}
    reading={
        'H':'Original constraints/counts/commitments, direct W observations/counterevidence and A judgments, then unresolved H questions. Check prior directions against subsequent W work; revise H scientific judgments from observations.',
        'W':'Constraints and useful H hypotheses, then prior observations and A judgments.',
        'A':'The concrete assessment question and latest W changes, then relevant prior A/H evidence and unresolved alternatives. Judge what can change this decision; retain material limitations.',
        'E':'Exact requester question and constraints, then requested original evidence.'}
    if job.get('research_strategy_version')==3:
        priorities['W']=('W','H','A','E')
        reading['W']='Original objective and constraints, retained W candidate memory and unresolved questions, then relevant observations and H/A advice.'
    parents=[_parent_view(job,ref,source=parent_sources[int(Path(ref['path']).parts[2])] if root is not None else None,
        limitation_bytes=1024//max(1,len(job['direct_refs']))) for ref in job['direct_refs']]
    order=priorities[job['role']]
    if job['role']=='E' and (job.get('evidence_return') or {}).get('role') in order:
        requester=job['evidence_return']['role']
        order=(requester,*[role for role in order if role!=requester])
    parents.sort(key=lambda row:order.index(row['role']) if row.get('role') in order else len(order))
    from . import engineering
    view = {key: value for key, value in {
        'role': job['role'], 'question': question,
        **(dict(consultation=consultation_view(job)) if job.get('purpose')=='worker_consultation' else {}),
        **(dict(supervision_request=supervision_binding(job['supervision_request'],step=job['step'],
            research_strategy_version=job.get('research_strategy_version')))
           if job.get('supervision_request') is not None else {}),
        **user_guidance.public(job),
        **(dict(formal_review_capability=dict(enabled=True,
            choices=formal_review.public_choices(root,job)),
            formal_review_feedback=formal_review.feedback(root,job))
           if job.get('formal_review_version')==1 and job.get('purpose')!='worker_consultation' else {}),
        **(dict(source_restrictions=job['source_restrictions']) if 'source_restrictions' in job else {}),
        **(dict(execution_authority=dict(status='trusted_gate_required' if policy.get('execution_resource_policy') else 'authority_only_production_gate_unavailable',
            qualified_materials=qualified_materials[:3],
            qualified_materials_omitted_count=max(0, len(job.get('execution_qualifications', [])) - 3)))
           if job.get('execution_authority') is not None else {}),
        **(dict(engineering_repair=engineering.public(job['engineering_repair'])) if job.get('engineering_repair') else {}),
        **(dict(repair_observation=dict(mechanism_ref=job['search']['focus']['mechanism_ref'],
            source_step=job['search']['repair_source']['step'],call_id=job['search']['repair_source']['call']['call_id'],
            status=job['search']['repair_source']['call']['status'],
            authorization_step=job['search']['repair_authorization']['step'],
            scope='Scientific repair under current H; known_success/known_failed are terminal history, not lifecycle restoration. Old findings retain their original H version and limitations.'))
           if (job.get('search') or {}).get('repair_source') is not None else {}),
        **(dict(candidate_nomination_policy=job['candidate_nomination_policy'],
            candidate_nomination_mechanisms=job['candidate_nomination_mechanisms'])
           if job.get('candidate_nomination_mechanisms') else {}),

        **(dict(decision_context=context) if context is not None else {}),
        **(dict(assessment_handoff=job['assessment_handoff']) if 'assessment_handoff' in job else {}),
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
        'reading_order':reading[job['role']]+' Read each parent conclusion first; its history_alias and matching history-index pages expose the same scientific source, not independent evidence. lineage.indexes.research locates the latest shared H/W/A authored understanding, limitations and direction supervision. Use its exact original links and lineage indexes on demand for decision-relevant gaps; a new role turn does not require rereading the full history. Use existing reports/artifact_index for exact entries to local observations, counterevidence, untested combinations and open questions; do not manufacture clues. Use native file/shell tools: inspect JSON keys/shapes, then needed fields or line ranges; size combined batch returns for every inner/outer tool limit. Analyze large numeric arrays with your own local code and report needed results with exact source locators; originals remain available. Reuse unchanged originals fully read this session; fill gaps/conflicts/truncation, including later pages and limitations.'
            +(' A judges independently; no routine redo of old observations. Parent claims are not qualification.'
              if job['role']=='A' else ' A verifies independently. Parents are claims.')
            +' Check execution facts only at calls addressable by the current experiment client. For history use assigned originals or select exact frozen-history I IDs for next-node inputs after return. Retained identity alone implies neither readable material nor queryable calls. Prose/requested_evidence/evidence_requests do not bind inputs; do not search private control records.'
            +' Omissions: conclusion/index/next_page.',
        'reused_implementations':[dict(label=row['label'],path=row['material']['path']) for row in job.get('selected_inputs',[])],
        'lineage': _role_lineage(job,parents),
        'input_selection_status': job['input_selection_status'], 'control_diagnostics': job['control_diagnostics'],
        **(dict(control_request=request)
           if job.get('author_correction') else {}),
        **(dict(closeout=closeout) if closeout is not None else {}),
        **(dict(candidate_audit=audit) if audit is not None else {}),
        **(dict(audit_capability=audit_capability) if audit_capability is not None else {}),
        **(dict(delivery_comparison={key:value for key,value in shortlist.public_table(root,job['selection']).items() if key!='fields'})
           if job['role']=='A' and job.get('purpose') is None and job.get('selection') is not None else {}),
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


@dataclass
class CurrentInventory:
    """Accepted facts for this stopped attempt within one control operation."""
    root: Path
    job: dict
    record: Path
    attempt: int
    managed_calls: list
    scientific_outputs: dict
    input_coverage: list | None = None
    additional_inputs: bool = False
    delivery: dict | None = None
    checkpoint: dict | None = None
    materials: list = field(default_factory=list)
    mutable_materials: list = field(default_factory=list)
    observation: dict | None = None
    science_available: bool = False
    selected_inputs: list = field(default_factory=list)
    selection_diagnostics: list = field(default_factory=list)
    output_error: Exception | None = None
    policy: dict | None = None
    native_intent: dict | None = None
    native_outcome: dict | None = None
    base: dict | None = None
    head: object | None = None
    task_ref: dict | None = None
    configuration_revision: dict | None = None
    chain: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    process_receipts: dict = field(default_factory=dict)
    payload: dict | None = None
    science_error: Exception | None = None
    interpretation: dict | None = None
    caller_correction: dict | None = None
    file_correction: dict | None = None
    observation_document: dict | None = None
    observation_inputs: dict | None = None
    native_meter: dict | None = None
    material_sources: dict = field(default_factory=dict)
    redundant_materials: list = field(default_factory=list)
    research_state: dict | None = None


def accept_current_inventory(root: Path, step: int):
    """The sole current-node observation/acceptance owner after writers stop."""
    from . import execution, worker_delivery, history, checkpoint
    from .output_repair import OutputRepairRequired
    root=Path(root)
    material_sources = {}
    job=read_job(root/'jobs'/f'{step:03d}.json', sources=material_sources)
    if (root/'native-evidence'/f'{step:03d}'/'result-identity.json').exists():
        raise ValueError('Sealed scientific facts must be read from their accepted result')
    from . import accepted_budget
    base=settings(root)
    head=accepted_budget.load(root,base=base)
    policy=settings_for_job(root,job,base=base,head=head)
    from .protocol import require_current_job
    require_current_job(root,base=base,job=job,retained=job,policy=policy)
    task_ref=record_ref(root/'jobs'/f'{step:03d}.json')
    accepted=list(execution.accepted_intents_from_facts(root,step,job=job,base=base,
        policy=policy,head=head,task_ref=task_ref))
    if not accepted:regular(execution.origin(root,step)/'native-intent.json')
    chain=[path for path,_ in accepted]
    intents=[value for _,value in accepted]
    record=execution.record_from_chain(chain)
    intent=intents[-1]
    original_outcome=read(regular(record/'native-outcome.json'))
    receipts=worker_delivery._writers_stopped(root,job,record,chain=chain,
        intent=intent,outcome=original_outcome)
    meter=read(regular(record/'usage.json')) if (record/'usage.json').exists() else None
    terminal_evidence=None
    if _native_outcome_candidate(original_outcome):
        from . import backends
        terminal_evidence=backends.outcome_provider(original_outcome).acquire_terminal_evidence(record,original_outcome)
    outcome=_native_outcome(record,original_outcome,meter=meter,terminal_evidence=terminal_evidence)
    head=accepted_budget.accept_terminal_from_base(root,step,record,base=base,meter=meter,
        unknown=meter is None or step in outcome.get('usage',{}).get('unknown_steps',[]),
        metering=outcome.get('metering'))
    raw=record/'science-output.json';pending=record/'result.pending.json'
    if (not raw.exists() and not raw.is_symlink() and pending.exists() and not pending.is_symlink()
            and outcome.get('returncode')==0 and outcome.get('stop_reason') is None
            and outcome.get('cleanup')=='observed_group_cleaned'
            and outcome.get('raw_ref')==dict(path=str(raw),size_bytes=regular(pending).stat().st_size)):
        os.link(regular(pending),raw,follow_symlinks=False)
    inventory=CurrentInventory(root,job,record,len(chain)-1,[],{},policy=policy,
        native_intent=intent,native_outcome=outcome,native_meter=meter,base=base,head=head,task_ref=task_ref,
        configuration_revision=job.get('configuration_revision'),chain=chain,intents=intents,
        process_receipts=receipts,material_sources=material_sources)
    calls=(history._observe_managed_calls_for_inventory(root,job,inventory=inventory)
        if history._managed_config_for_inventory(job,inventory) else [])
    history._validate_managed_for_inventory(root,job,calls,inventory=inventory)
    inventory.managed_calls=calls
    from . import worker_research
    if worker_research.applicable(job):
        from . import experiment_client
        from .protocol import stage_scope_for_job
        inventory.research_state=worker_research.load(dict(research_strategy_version=3,
            worker_research_goal_id=policy['goal_id'],
            scope=stage_scope_for_job(root,job,task_ref=task_ref),
            record_dir=str(experiment_client._record_directory_from_facts(root,step,job)),
            breadth=(job.get('search') or {}).get('breadth'),
            worker_research_seed=((job.get('search') or {}).get('worker_research') or {}).get('seed',[])))
    inventory.scientific_outputs={row['call_id']:history._observe_scientific_outputs_for_inventory(root,job,row,inventory=inventory)
        for row in calls if row['status'] in {'known_success','known_failed'}}
    needs_checkpoint=(job['role']=='W' and (checkpoint.enabled_for_policy(job,settings=policy)
        or worker_delivery.applicable(job) or job.get('managed_execution_wait')))
    if not raw.exists():
        if needs_checkpoint:inventory.checkpoint=checkpoint._capture(root,job,record,inventory=inventory)
        return inventory
    inventory.science_available=True
    load_validated_input_facts(root,workspace(root,step),job,base=base,policy=policy,head=head,
        sources=material_sources)
    authored=None
    try:
        from . import candidate_refs
        authored=science_payload(raw,job['role'],job['handoff_protocol'],
            'selection' in job,**response_options(job))
        payload=candidate_refs.control_payload(job,authored,root=root,inventory=inventory)
        inventory.payload=payload
        inventory.selected_inputs=_accept_selected_inputs(workspace(root,step),payload,job,inventory.selection_diagnostics,
            _policy=inventory.policy,_sources=material_sources)
    except OutputRepairRequired as error:
        # The existing bounded output-repair owner diagnoses malformed science;
        # no selection or scientific qualification is inferred from it here.
        inventory.output_error=error
        if authored is None:inventory.science_error=error
    if inventory.payload is not None:
        from . import search
        inventory.interpretation=(search._interpret_from_facts(root,job,inventory.payload,
            policy=policy,state=search.current_state(root,job,inventory.payload),
            selected_inputs=inventory.selected_inputs)
            if policy.get('search_policy') is not None and not job.get('formal_review') and not job.get('final_report')
            else dict(focus=None,allocation=None,diagnostics=[]))
    if len(chain)>1 and outcome.get('returncode')==0 and not outcome.get('stop_reason'):
        execution.accept_current_terminal_for_inventory(root,job,inventory,
            original_outcome=original_outcome,meter=meter)
    inventory.input_coverage=history._accept_managed_input_coverage(root,job,calls) if calls else None
    if worker_delivery.repair_applicable(job) and (record/'science-output.json').exists():
        inventory.caller_correction=worker_delivery._caller_correction(root,job,record)
        inventory.file_correction=worker_delivery._file_correction(root,job,inventory=inventory)
        inventory.delivery=worker_delivery._capture(root,job,record,inventory=inventory)
    if needs_checkpoint:
        inventory.checkpoint=checkpoint._capture(root,job,record,inventory=inventory)
    try:
        inventory.materials,inventory.mutable_materials=_material_inventory(root,step,inventory=inventory)
    except OutputRepairRequired as error:
        if inventory.output_error is None:inventory.output_error=error
    return inventory


def accept_recovery_call(root, job, call_id, *, context=None):
    """Accept only this operation's newly changed original execution facts."""
    from . import history
    rows=history._observe_managed_calls(root,job,call_ids=[call_id],context=context)
    if len(rows)!=1 or rows[0]['call_id']!=call_id:
        raise ValueError('Recovered original accepted record is unavailable')
    history._validate_managed(root,job,rows,context=context)
    return rows[0]


def material_inventory(root: Path, step: int, *, inventory):
    """Validate stopped-node originals and layout without changing permissions."""
    job=read(regular(root/'jobs'/f'{step:03d}.json'))
    if inventory.root!=root or job_record(inventory.job)!=job:
        raise ValueError('Current inventory belongs to another task or attempt')
    _inventory_identity(root,step,inventory)
    if inventory.policy is None:
        raise ValueError('Current inventory requires its produced policy')
    _current_inventory_head(root,step,inventory)
    return _material_inventory(root,step,inventory=inventory)


def _inventory_identity(root,step,inventory):
    """Check the owner's explicit binding without loading another chain."""
    root=Path(root)
    origin=host_record(workspace(root,step))
    expected=origin if inventory.attempt==0 else origin/'continuations'/f'{inventory.attempt:03d}'
    if (inventory.root!=root or inventory.job.get('step')!=step or inventory.record!=expected
            or inventory.job.get('configuration_revision')!=inventory.configuration_revision
            or inventory.chain and (inventory.chain[-1]!=inventory.record
                or len(inventory.chain)-1!=inventory.attempt)):
        raise ValueError('Current inventory belongs to another task or attempt')


def _current_inventory_head(root,step,inventory):
    """Admit this exact stopped attempt at an independent public boundary."""
    from . import accepted_budget
    _inventory_identity(root,step,inventory)
    base=inventory.base if inventory.base is not None else settings(root)
    head=accepted_budget.load(root,base=base)
    current=head.current_job
    relative=inventory.record.relative_to(root).as_posix()
    point=head.row('attempt/'+relative.replace('/',':'))
    if (current is None or current['step']!=step or current['native_record']!=relative
            or current['job_ref']!=inventory.task_ref or point is None or point['step']!=step
            or point['intent']!=inventory.native_intent or point['status']!='closed'):
        raise ValueError('Current inventory belongs to another task or attempt')
    inventory.head=head
    return head


def _validate_current_inventory_owner(root,step,inventory,*,head):
    """Consume the terminal writer's exact current owner without opening rows."""
    _inventory_identity(root,step,inventory)
    if head is None:
        raise ValueError('Current inventory requires its produced accepted head')
    current=head.current_job
    relative=inventory.record.relative_to(root).as_posix()
    if (current is None or current['step']!=step or current['native_record']!=relative
            or current['job_ref']!=inventory.task_ref or current['status']!='terminal'
            or head['current_native'] is not None):
        raise ValueError('Current inventory belongs to another task or attempt')


def _material_inventory(root: Path, step: int, *, inventory):
    """Consume the current owner's facts while checking owned deliverables."""
    _inventory_identity(root,step,inventory)
    job=inventory.job
    work = workspace(root, step)
    rows, mutable, destinations = [], [], {}
    record=inventory.record
    if inventory.policy is None:
        raise ValueError('Current inventory requires its produced policy')
    from . import worker_delivery
    calls=[]
    if job['role']=='W':
        from . import history
        calls=history.managed_calls(inventory)
    managed_namespaces={call['call_id'] for call in calls}

    def managed_namespace(relative):
        parts=PurePosixPath(relative).parts
        return len(parts)>=2 and parts[0]=='experiments' and parts[1] in managed_namespaces

    if (record/'native-intent.json').exists():
        intent=inventory.native_intent
        if intent.get('author_context_version')==1:
            from . import author_closeout
            proof=intent['author_closeout_science_context']
            author_closeout.validate_science_context(root,job,proof)

    def add(path, relative, *, source_kind='mutable'):
        path = regular(path)
        size = path.stat().st_size
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
        rows.append(dict(source=str(path),
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
    if job['role']=='W':
        for call in calls:
            if call['status'] not in {'known_success','known_failed'}:continue
            outputs=history.scientific_outputs(inventory,call)
            for item in outputs:
                if item.get('retention')!='archived':raise ValueError('Managed deliverable original unavailable')
                add(root/item['path'],f'experiments/{call["call_id"]}/artifacts/{item["name"]}',source_kind='managed_frozen')
    collisions = [sources for sources in destinations.values() if len(sources) > 1]
    # Inspect every original at each destination, including managed originals
    # hidden behind an author's first entry. The author cannot resolve these.
    if any(sum(kind == 'managed_frozen' for _, kind in sources) > 1 for sources in collisions):
        raise ValueError('Different managed originals share a deliverable path')
    # Only a copy at the original's exact publication path can be redundant.
    # Compare at those collisions after validating every retained original.
    redundant = set()
    sizes = {row['source']: row['size_bytes'] for row in rows}
    for relative, sources in destinations.items():
        managed = [path for path, kind in sources if kind == 'managed_frozen']
        if not managed:continue
        original = managed[0]
        for path, kind in sources:
            if kind != 'mutable' or sizes[str(path)] != sizes[str(original)]:continue
            with path.open('rb') as authored, original.open('rb') as retained:
                while True:
                    block = authored.read(65536)
                    if block != retained.read(65536):break
                    if not block:
                        redundant.add(path)
                        break
        destinations[relative] = [(path, kind) for path, kind in sources if path not in redundant]
    rows = [row for row in rows if Path(row['source']) not in redundant]
    mutable = [path for path in mutable if path not in redundant]
    collisions = [sources for sources in destinations.values() if len(sources) > 1]
    reserved = next((path for relative, sources in destinations.items() if managed_namespace(relative)
        for path, kind in sources if kind=='mutable'),None)
    if reserved is not None:
        from .output_repair import OutputRepairRequired
        diagnostic = (
            'Remove redundant copies; cite retained managed originals directly. '
            'Managed call publication directories belong to their original producers; move distinct authored reports outside them. '
            f'Preserve managed originals/call IDs; no rerun/resubmit. Owned path: {reserved.relative_to(work).as_posix()}')
        raise OutputRepairRequired('deliverable_path_collision', diagnostic)
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
    inventory.redundant_materials = sorted(redundant)
    return sorted(rows, key=lambda row: row['path']), mutable


def collect_materials(root: Path, step: int, *, inventory):
    """Seal this stopped node's validated deliverables at their original paths."""
    rows, mutable = inventory.materials, inventory.mutable_materials
    # Inventory, budgets and all original bindings must be valid before the
    # first permission change. IO failures in the sealing pass can still leave
    # partially changed modes; this is not a filesystem transaction.
    for path in mutable:
        if path.stat().st_mode & 0o777 != 0o400:path.chmod(0o400)
    for path in inventory.redundant_materials:
        try:path.unlink(missing_ok=True)
        except OSError:pass  # The retained original already owns publication.
    return rows


def global_materials(root: Path):
    """Frozen original context shared by all nodes through read-only aliases."""
    return _global_materials(root, base=settings(root))


def _global_materials(root, *, base):
    """Check original material locations using the already loaded root base."""
    rows = base['global_materials']
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
              correction_request=None, author_correction=None, *, purpose=None, source_cutoff=None, delivery_sources=None, engineering_repair=None, final_report=None, supervision_request=None):
    if role not in ROLE_INSTRUCTIONS or type(step) is not int or step < 1:
        raise ValueError('Invalid scientific node')
    if len(parents) != len(set(parents)) or any(type(p) is not int or not 0 < p < step for p in parents):
        raise ValueError('Parents must be exact distinct preceding nodes')
    if supervision_request is not None and (role!='A' or purpose is not None or evidence_return is not None
            or final_report is not None or author_correction is not None or correction_request is not None
            or engineering_repair is not None):
        raise ValueError('Direction supervision requires an ordinary A assessment')
    from .protocol import require_current_base
    base = settings(root)
    require_current_base(root, base=base)
    from . import accepted_prefix
    retained_path=Path(root)/'jobs'/f'{step:03d}.json'
    retained=None
    if retained_path.exists():
        retained=read(regular(retained_path))
        from .protocol import require_job_format
        require_job_format(retained)
    accepted_prefix.reconcile(root)
    if retained is not None:
        from . import handoff_state
        retained=handoff_state.material_inputs(Path(root),retained,sources={},base=base,source=None)
        requested=dict(role=role,instruction=instruction,evidence_return=evidence_return,
            correction_request=correction_request,author_correction=author_correction)
        expected_purpose=('formal_review' if (evidence_return or {}).get('kind')=='formal_review' else
            'worker_consultation' if (evidence_return or {}).get('role')=='W' else purpose)
        if (any(retained.get(key)!=value for key,value in requested.items())
                or [int(Path(ref['path']).parts[2]) for ref in retained['direct_refs']]!=parents
                or retained.get('purpose')!=expected_purpose or retained.get('source_cutoff')!=source_cutoff
                or retained.get('delivery_sources')!=delivery_sources
                or supervision_request is not None and retained.get('supervision_request')!=supervision_request
                or retained.get('final_report')!=final_report):
            raise ValueError('Frozen scientific task changed')
        from . import accepted_budget
        accepted_budget.accept_job_from_base(root,step,retained,base=base)
        return retained
    from . import handoff_state
    handoff_state.begin_job(root,step)
    from . import budget
    from . import accepted_budget
    head = accepted_budget.load(root, base=base)
    events, _ = budget._events_from_state(head)
    if budget.final_a_barrier(root, before_step=step, events=events) is not None:
        raise ValueError('Final A return permits no further scientific task')
    phase = budget.closeout_state(root, events=events)['begin']
    if phase is not None and phase['mode'] == 'final' and step >= phase['first_step'] and (
            role != 'A' or step != phase['first_step'] or purpose != 'closeout' and final_report is None):
        raise ValueError('FINAL permits only its explicit final A assignment')
    if final_report is not None:
        budget.validate_final_report_inputs(root, dict(step=step, role=role, purpose=purpose,
            final_only=phase is not None and phase['mode'] == 'final' and step == phase['first_step'],
            final_report=final_report, direct_refs=[dict(path=f'inputs/parents/{parent:03d}/conclusion.json') for parent in parents]))
    consultation=(evidence_return or {}).get('role')=='W'
    if supervision_request is not None:
        supervision_request=supervision_binding(supervision_request,step=step)
    if consultation:
        if (purpose not in {None,'worker_consultation'} or role not in {'H','A'}
                or set(evidence_return)!={'role','step'} or type(evidence_return.get('step')) is not int
                or evidence_return['step'] not in parents or not 0<evidence_return['step']<step
                or source_cutoff is not None or delivery_sources is not None or final_report is not None):
            raise ValueError('Worker consultation requires its exact preceding W requester')
        purpose='worker_consultation'
    if purpose is not None and not consultation:
        from . import budget
        begin=phase
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
    retained=read_job(job_path, base=base) if job_path.exists() else None
    from . import configuration_updates
    revision, policy, head = configuration_updates.bind_new_job_facts(root, step, base=base)
    events, _ = budget._events_from_state(head)
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
    globals_ = _global_materials(root, base=base)
    from . import storage_policy as storage
    input_inventory=storage.Inventory(policy.get('storage_policy'),
        context=storage.Deadline(65 if policy['max_seconds'] is None else policy['max_seconds']))
    for item in globals_:
        input_inventory.entry();input_inventory.file((root/item['source']).stat().st_size)
        from . import originals
        originals.resolve_root_ref(root, dict(item, path=item['source']))
    refs, materials, entry_points, selected = [], [], [], []
    parent_sources=[]
    parent_facts,parent_members,parent_views,material_paths={},{},{},{}
    if review_binding is not None:
        parent_facts.update(review_binding['material_sources'])
        materials=review_binding['materials'];selected=review_binding['selected']
    relay_predecessor = None
    if review_binding is None and step - 1 in parents:
        predecessor = _load_parent_source(root, step - 1, base=base,
            sources=parent_facts, members=parent_members, prefix=head['prefix'])
        if 'relayed_material_refs' in predecessor['seal']:
            relay_predecessor = step - 1
    for parent in ([] if review_binding is not None else parents):
        facts=_load_parent_source(root,parent,base=base,sources=parent_facts,members=parent_members,
            prefix=head['prefix'],resolve_choices=True)
        parent_source=handoff_state.source_from_facts(facts)
        parent_sources.append(parent_source)
        # Pass the author's science, not the controller envelope and its parent
        # references. Historical lineage remains in the host-owned node tree.
        ref = dict(path=f'inputs/parents/{parent:03d}/conclusion.json', size_bytes=facts['seal']['raw_ref']['size_bytes'])
        (work / Path(ref['path']).parent / 'artifacts').mkdir(parents=True,exist_ok=True)
        refs.append(ref)
        from . import originals
        parent_seal = parent_source[2]
        if relay_predecessor is not None and parent != relay_predecessor:
            continue  # Older conclusions stay visible; the accepted relay owns current materials.
        for material in parent_seal['materials']:
            material_paths[material['path']]=originals.bound_material_path(root,material,
                source=facts['owner'],seal=parent_seal)
        materials.extend(_public_materials(parent_seal['materials']))
        entry_points.extend(parent_seal.get('entry_points', []))
        # Reuse is an instruction to the immediate next node, not persistent
        # baggage on a retained H ancestor in every later scientific cycle.
        if parent == step - 1:
            selected.extend(handoff_state.selected_materials(root,parent_seal,sources=parent_facts,
                base=base,source=facts['owner']))
            if 'relayed_material_refs' in parent_seal:
                materials.extend(handoff_state.relayed_materials(root,parent_seal,sources=parent_facts,
                    base=base,source=facts['owner']))
    if consultation:
        requester=parent_facts[evidence_return['step']]
        expected='revise_h' if role=='H' else 'review'
        if requester['job']['role']!='W' or requester['payload'].get('stage_intent')!=expected:
            raise ValueError('Worker consultation differs from its exact authored request')
    delivery_review.validate_parent(dict(step=step,role=role,evidence_return=evidence_return,
        research_strategy_version=policy.get('research_strategy_version'),purpose=purpose,
        author_correction=author_correction,correction_request=correction_request,engineering_repair=engineering_repair),parent_facts)
    unique = {}
    for item in [*materials, *[row['material'] for row in selected]]:
        if item['path'] in unique and unique[item['path']] != item:
            raise ValueError('Selected input paths conflict')
        unique[item['path']] = item
    materials = list(unique.values())
    reused = {}
    for row in selected:
        reused.setdefault(row['material']['path'], row)
    selected = list(reused.values())
    for item in materials:
        input_inventory.entry();input_inventory.file(item['size_bytes'])
        from . import originals
        if item['path'] in material_paths:
            continue
        if review_binding is not None and item in review_binding.get('compiler_scientific_materials',[]):
            material_paths[item['path']]=originals.material_path(root,item)
            continue
        origin=int(Path(item['path']).parts[2])
        if origin not in parent_facts:
            parent_facts[origin]=handoff_state.sealed_facts(root,origin,base=base)
        producer=parent_facts[origin]
        material_paths[item['path']]=originals.bound_material_path(root,item,
            source=producer['owner'],seal=producer['seal'])
    # No recursive parent expansion; per-file identities stay in host storage.
    job = dict(step=step, role=role, goal_text=policy['objective'], public_root=policy['public_root'],
               instruction=instruction, direct_refs=refs, direct_materials=materials, direct_entry_points=entry_points, global_materials=globals_,
               evidence_return=evidence_return, **(dict(selected_inputs=selected) if selected else {}),
               handoff_protocol=protocol, worker_view_version=1, lineage_projection_version=1)
    if consultation:job['purpose']='worker_consultation'
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
    resumed = budget.normal_resume(root,step,events=events)
    if resumed is not None and purpose is None and final_report is None:
        job['normal_resume'] = budget._event_ref(root,resumed)
    if (retained is None and ((purpose == 'closeout' and begin['mode'] == 'final') or final_report is not None)
            or retained is not None and retained.get('final_only')):
        job['final_only'] = True
    if policy.get('formal_review_policy') is not None:
        from . import formal_review
        job['formal_review_version']=1
        formal_review.bind_job(root,job,sources=parent_facts)
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
    strategy=retained if retained is not None else policy
    if 'research_strategy_version' in strategy:
        job['research_strategy_version']=strategy['research_strategy_version']
    advice = retained.get('role_guidance') if retained is not None else policy.get('role_guidance', {}).get(role)
    if advice is not None:
        job['role_guidance'] = advice
    job['handoff_authority_version'] = 1
    from . import guidance
    job['native_role_instruction'] = (retained['native_role_instruction'] if retained is not None
                                      else advice if advice is not None else
                                      (guidance.WORKER_FIRST if job.get('research_strategy_version')==3 else
                                       guidance.WORKER_LED if job.get('research_strategy_version') in (1,2) else guidance.OPPORTUNITY)[role])
    if audit_projection_version == 1:
        job['audit_projection_version'] = 1
    job['selection_protocol'] = selection_protocol
    if engineering_repair is not None:
        job['engineering_repair']=engineering_repair
        engineering.bind_inputs(root,job)
    if purpose is not None and not consultation:
        job.update(purpose=purpose, source_cutoff=source_cutoff, delivery_sources=delivery_sources,
                   closeout_begin=dict(seq=begin['seq'], record=record_ref(root / 'control-ledger' / f'{begin["seq"]:06d}.json')))
        if role=='A' and policy.get('selection_policy') is not None:
            from . import shortlist
            mark = shortlist.marker(root,step)
            if mark is not None:
                job['selection'] = mark
    previous = parent_facts[step-1]['seal'] if step - 1 in parents and review_binding is None else {}
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
        job['history'],state=lineage.bind_from_facts(root,step,role,protocol,base=base,head=head,policy=policy,
            source_cutoff=final_report['source_cutoff'] if final_report else source_cutoff,extra_steps=extra_steps,selection_protocol=selection_protocol,
            audit_projection_version=audit_projection_version,adjacent_audit=job.get('candidate_audit'),
            worker_view_version=1,assessment_job=job if role=='A' else None,source_rows=parent_sources)
        options = []
    if retained is not None:
        if 'assessment_handoff' in retained:
            job['assessment_handoff'] = retained['assessment_handoff']
    elif correction_request is None:
        handoff = _load_assessment_handoff(root,job,base=base,sources=parent_facts,
            members=parent_members,policy=policy)
        if handoff is not None:
            job['assessment_handoff'] = handoff
            original = _assessment_original(handoff,sources=parent_facts)
            if original is not None:
                bound = [row for row in job['history']['inputs'] if row['path'] == original['path']]
                if not bound:
                    input_inventory.entry(); input_inventory.file(original['size_bytes'])
                    job['history']['inputs'].append(original)
                elif bound != [original]:
                    raise ValueError('Assessment original differs from its exact producer')
    prior = job.get('assessment_handoff', {}).get('previous_a')
    if prior is not None:
        producer=parent_facts[prior['step']]
        seal = producer['seal']
        selected_materials=[row for row in seal['materials'] if row['path'] in prior['materials']]
        for material in selected_materials:
            if material['path'] not in material_paths:
                material_paths[material['path']]=originals.bound_material_path(root,material,
                    source=producer['owner'],seal=seal)
        for material in _public_materials(selected_materials):
            bound = [row for row in job['direct_materials'] if row['path'] == material['path']]
            if bound and bound != [material]:
                raise ValueError('Assessment material conflicts with its exact producer')
            if not bound:
                input_inventory.entry(); input_inventory.file(material['size_bytes'])
                job['direct_materials'].append(material)
    if review_binding is None and retained is None:
        options = _material_options(job['direct_materials'], sources=parent_facts)
    from . import candidate_refs
    job['direct_originals']=refs
    job['direct_refs']=[]
    for original in refs:
        parent=int(Path(original['path']).parts[2]);facts=parent_facts[parent]
        source=facts['raw_path']
        if source.stat().st_size!=original['size_bytes']:raise ValueError('Direct producer original changed')
        reported,public=_parent_public(job,original,source=facts)
        original_path=str(Path(original['path']).parent/'scientific-original.json')
        # A logical raw link resolves to the sealed parent original.
        if public!=reported and not any(row['path']==original_path for row in job['history']['inputs']):
            job['history']['inputs'].append(dict(path=original_path,source=str(source),
                size_bytes=original['size_bytes'],kind='scientific_original'))
        target=work/original['path']
        if target.exists():
            if read_scientific_output(regular(target))!=public:raise ValueError('Frozen parent scientific view changed')
        elif public!=facts['payload']:
            write(target,public);target.chmod(0o400)
        if source.stat().st_size!=original['size_bytes']:raise ValueError('Direct producer original changed')
        job['direct_refs'].append(dict(path=original['path'],
            size_bytes=target.stat().st_size if target.exists() else original['size_bytes']))
        parent_views[parent]=dict(facts=facts,member=parent_members[parent],public=public)
    job['implementation_options'] = options[:16]
    job['implementation_options_omitted_count'] = retained.get('implementation_options_omitted_count',0) if retained is not None else max(0, len(options) - 16)
    if review_binding is None and retained is None:
        job['history']['view']['implementation_options'] = [dict(id=row['id'],
            label=row['label'].encode()[:80].decode('utf-8',errors='ignore'), path=row['path']) for row in options[:2]]
        job['history']['view']['implementation_options_omitted_count'] = max(0,len(options)-2)
        lineage.bounded_brief(job['history']['view'])
    if retained is not None:
        for field in ('search','portfolio_context','final_coverage_mechanisms','selection','candidate_nomination_policy','candidate_nomination_mechanisms','supervision_request'):
            if field in retained:job[field]=retained[field]
    elif (purpose is None or consultation and role=='H') and not job.get('formal_review') and final_report is None and engineering_repair is None and policy.get('search_policy') is not None:
        from . import search
        job['search'] = search.bind(root, step, role, protocol, state=state, unified=True,
            job=job,parent_jobs={number:facts['job'] for number,facts in parent_facts.items()})
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
        if supervision_request is not None:job['supervision_request']=supervision_request
        job['scientific_response_fields']=response_fields(job,policy)
    elif 'scientific_response_fields' in retained:
        job['scientific_response_fields']=retained['scientific_response_fields']
    if retained is not None:
        if 'scientific_work_unit' in retained:job['scientific_work_unit']=retained['scientific_work_unit']
    elif role=='W':
        from . import guidance
        action='validate' if delivery_review.scope(job)=='delivery_correction' else ((job.get('search') or {}).get('focus') or {}).get('action')
        if action in guidance.WORK_UNITS:job['scientific_work_unit']=guidance.WORK_UNITS[action]
    if correction_request is not None:
        job['correction_request'] = correction_request
        from . import correction
        correction.validate_job(root, job)
    context = _decision_context(job,sources=parent_facts)
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
    if role=='A' and purpose!='worker_consultation' and job.get('formal_review_version')==1 and not job.get('formal_review') and (retained is None or 'formal_review_choices' in retained):
        from . import formal_review
        job['formal_review_choices']=formal_review.choice_constraints(root,job,
            retained['formal_review_choices']['unavailable'] if retained is not None else None)
    from . import material_references
    scientific = review_binding.get('compiler_scientific_materials', []) if review_binding is not None else []
    job['material_refs'] = material_references.references(
        [row for row in job['direct_materials'] if row not in scientific], sources=parent_facts)
    if review_binding is None:
        job['direct_materials'] = material_references.materials(job['material_refs'], sources=parent_facts)
    if 'selected_inputs' in job:
        job['selected_inputs'] = material_references.bind_selected(job['selected_inputs'], sources=parent_facts)
    # Retained legacy jobs keep their exact identity and remain unqualified.
    if retained is None or 'source_provenance' in retained:
        from . import qualification
        inputs=qualification.input_facts(root,job,base=base,head=head,sources=parent_facts,
            material_paths=material_paths,review_binding=review_binding)
        job['source_provenance'],document=qualification.bind_inputs_from_facts(root,job,facts=inputs)
        job['source_restrictions']=qualification.status_from_facts(job,document,facts=inputs)
    from . import execution_authority
    authority_facts=execution_authority.job_facts(root,job,base=base,policy=policy,sources=parent_facts)
    execution_authority.bind_job(root,job,facts=authority_facts)
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
    if retained is None:
        job['role_purpose_instruction']=guidance.role_contract(job)
    elif 'role_purpose_instruction' in retained:
        job['role_purpose_instruction']=retained['role_purpose_instruction']
    with user_guidance.binding_from_base(root,step,role,retained,base=base) as (statements,guidance_current):
        if statements:
            job['user_guidance'] = statements
        for path, value in ((root / 'jobs' / f'{step:03d}.json', job_record(job)),
                            (work / 'science-task.json', read(regular(work / 'science-task.json')) if retained is not None else _science_view(job,root,parent_sources=parent_views,policy=policy,qualified_materials=authority_facts['qualified_materials'])),
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
        acceptance=accepted_budget.job_acceptance_facts(root,step,base=base)
        head=accepted_budget.accept_job_from_facts(root,step,job,facts=acceptance,
            guidance_current=guidance_current)
    skill_supply.project(root, policy, job)
    return job


def _native_output_identity(root, step, intent, outcome, *, source=None, job=None):
    from . import budget
    if source is None:
        if job is None:job = read(regular(Path(root) / 'jobs' / f'{step:03d}.json'))
        budget.validate_final_a_receipt(root, job, intent, outcome)
    return outcome.get('raw_ref')


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
    from . import handoff_state
    material_sources = {}
    job = handoff_state.material_inputs(originals.context_root(source), originals.source_job(source),
        sources=material_sources, source=source)
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
    outcome_record = originals.source_record(source, 'native-outcome.json').parent
    saved_outcome = read(regular(outcome_record / 'native-outcome.json'))
    outcome = native_outcome(outcome_record,
        original_record=Path(seal['raw_ref']['path']).parent,
        output_path=(originals.resolve_ref(source, seal['raw_ref'])
            if saved_outcome.get('stop_reason') == 'native_failure_event' else None))
    raw = None if controls_only else originals.resolve_ref(source,seal['raw_ref'])
    result = originals.resolve_ref(source,seal['result_ref'])
    raw_ref=_native_output_identity(Path(source.original_root),source.step,intent,outcome,source=source)
    if (intent.get('task_ref') != seal['job_ref'] or outcome.get('returncode') != 0
            or outcome.get('stop_reason')
            or not isinstance(raw_ref,dict)
            or raw_ref != {key:seal['raw_ref'][key] for key in ('path','size_bytes')}):
        raise ValueError('Source terminal lost its exact original task/output')
    payload = read(result) if controls_only else _sealed_science(job,seal,raw,result,source=source)
    if ('relayed_material_refs' in seal and seal['relayed_material_refs'] !=
            _relayed_materials(job, payload, seal.get('selected_inputs', []))):
        raise ValueError('Relayed material receipts changed')
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
    from . import originals, execution
    if originals.inherited(root, int(work.name)):
        return validate_original(originals.node(root, int(work.name)))
    job_path = root / 'jobs' / f'{int(work.name):03d}.json'
    material_sources = {}
    job = read_job(job_path, sources=material_sources)
    from .protocol import require_current
    require_current(root, job)
    record = host_record(work)
    seal = read(regular(record / 'result-identity.json'))
    from . import history
    observed=(read(root/seal['observations']['path']) if seal.get('observations') else {})
    calls=observed.get('managed_calls',[])
    history._validate_managed(root,job,calls)
    inventory=CurrentInventory(root,job,execution.record(root,job['step']),
        len(execution.records(root,job['step']))-1,calls,{},policy=settings(root,job),
        task_ref=record_ref(job_path),configuration_revision=job.get('configuration_revision'),
        material_sources=material_sources)
    if (seal.get('control_outcome') or {}).get('worker_delivery',{}).get('facts_ref'):
        from . import worker_delivery
        inventory.delivery=worker_delivery._validate(root,job,inventory.record,inventory=inventory)

    from . import execution
    native_record = execution.record(root, job['step'])
    intent = read(regular(native_record / 'native-intent.json'))
    outcome = native_outcome(native_record)
    raw = regular(native_record / 'science-output.json')
    result = regular(record / 'result.json')
    conclusion = work / 'conclusion.json'
    native_raw_ref=_native_output_identity(root,job['step'],intent,outcome,job=job)
    if seal.get('final_a_completion') != _final_a_seal(native_record, outcome):
        raise ValueError('Final A completion receipt identity changed')
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
        worker_delivery.validate(root,job,native_record,inventory=inventory,source_attempt=intent.get('attempt',0))
    from . import correction
    correction.validate_payload(root, job, payload)
    if read(result) != result_projection(job, payload):
        raise ValueError('Result envelope differs from author science')
    unresolved = dict(items=[], omitted_count=0)
    if seal.get('entry_points', []) != artifact_entry_points(work, payload, seal['materials'], job['handoff_protocol'], unresolved):
        raise ValueError('Scientific deliverable entry points changed')
    if seal.get('unresolved_links') != unresolved:
        raise ValueError('Scientific unresolved link receipt changed')
    from . import material_references
    if seal.get('selected_inputs', []) != material_references.selected_record(
            _accept_selected_inputs(work, payload, job,_policy=inventory.policy,_sources=material_sources)):
        raise ValueError('Selected implementation receipts changed')
    if ('relayed_material_refs' in seal and seal['relayed_material_refs'] !=
            _relayed_materials(job, payload, seal.get('selected_inputs', []))):
        raise ValueError('Relayed material receipts changed')
    _validate_inputs(root, work, job,_policy=inventory.policy,_sources=material_sources)
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
    history.validate_observation(root, job, seal,inventory=inventory)
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
        return view
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
    if view.get('consultation')!=consultation_view(job):
        raise ValueError('Frozen scientific task changed its exact consultation requester')
    if 'assessment_handoff' in job and view.get('assessment_handoff') != job['assessment_handoff']:
        raise ValueError('Frozen scientific task changed its assessment sources')
    return view


def _validate_inputs(root, work, job, *, _policy=None, _sources=None):
    if read(regular(Path(root)/'jobs'/f'{job["step"]:03d}.json'))!=job_record(job):
        raise ValueError('Scientific inputs differ from their accepted canonical job')
    from . import accepted_budget
    base=settings(root)
    head=accepted_budget.load(root,base=base)
    policy=settings_for_job(root,job,base=base,head=head) if _policy is None else _policy
    return load_validated_input_facts(root,work,job,base=base,policy=policy,head=head,
        sources=_sources)['document']


def load_validated_input_facts(root,work,job,*,base,policy,head,sources=None):
    sources = {} if sources is None else sources
    if job.get('fork_source') is not None:
        from . import fork_runtime, fork_sources, formal_review, engineering
        selected=fork_sources.selected_task(root, material_sources=sources)
        facts=fork_runtime._load_selected_job_facts(root,work,job,base=base,policy=policy,head=head,
            selected=selected)
        document=facts['inputs']['document']
        formal_review.validate_job(root, job, sources=facts['sources'])
        engineering.validate_job(root, job)
        task_view=fork_runtime._selected_science_view(job,selected=selected,policy=policy,
            qualified=facts['authority']['qualified_materials'])
        return dict(document=document, sources=facts['sources'], inputs=facts['inputs'],task_view=task_view)
    from . import budget,accepted_budget,accepted_prefix
    events=budget._events_from_state(head)[0]
    report = budget._validate_final_report_inputs_from_facts(root,job,policy=policy,
        events=events)
    accepted=(head.current_job if head.current_job is not None and head.current_job['step']==job['step']
        else head.row(f'job/{job["step"]:03d}'))
    if accepted is None or accepted['prefix']!=accepted_prefix.for_job(job):
        raise ValueError('Scientific input prefix differs from its accepted job')
    if accepted['input_search_ref']!=job['history']['breadth_snapshot']:
        raise ValueError('Scientific breadth input differs from its accepted snapshot')
    if report is not None and 'search' in job:
        raise ValueError('Final report input history changed its exact sealed prefix')
    resumed = budget._normal_resume_from_facts(root,job['step'],events=events)
    expected_resume = (budget._event_ref(root,resumed) if resumed is not None and job.get('purpose') in (None,'formal_review') and not job.get('final_report') else None)
    if job.get('normal_resume') != expected_resume:
        raise ValueError('Scientific task changed its exact normal resume boundary')
    from . import formal_review
    review_binding=formal_review.validate_job(root,job,sources=sources)
    from . import qualification
    members={}
    for ref in job['direct_refs']:
        step=int(Path(ref['path']).parts[2])
        _load_parent_source(root,step,base=base,sources=sources,members=members,
            prefix=accepted_prefix.for_job(job))
    delivery_review.validate_parent(job,sources)
    inputs=qualification.input_facts(root,job,base=base,head=head,sources=sources,
        review_binding=review_binding)
    document=qualification.validate_inputs_from_facts(root,job,facts=inputs)
    from . import execution_authority
    authority_facts=execution_authority.job_facts(root,job,base=base,policy=policy,sources=sources)
    execution_authority.validate_job(root,job,facts=authority_facts)
    if 'source_provenance' in job and job.get('source_restrictions') != qualification.status_from_facts(job,document,facts=inputs):
        raise ValueError('Frozen source restriction view changed')
    if job.get('experiment_mode')!=policy.get('experiment_mode'):
        raise ValueError('Frozen experiment mode differs from its original settings')
    selected=policy.get('execution_config')
    if job.get('execution_config_record')!=selected:
        raise ValueError('Frozen execution selection differs from its original settings')
    if job['role']=='W' and job.get('worker_fragment_policy')!=policy.get('worker_fragment_policy'):
        raise ValueError('Frozen W continuation configuration differs from its original settings')
    if job.get('candidate_nomination_policy') is not None and job['candidate_nomination_policy']!=policy.get('candidate_nomination_policy'):
        raise ValueError('Frozen candidate nomination configuration changed')
    if job.get('audit_projection_version') == 1:
        if (job['history'].get('audit_projection_version') != 1
                or job['history'].get('adjacent_audit') != job.get('candidate_audit')):
            raise ValueError('Frozen audit projection differs from its task boundary')
    if job.get('handoff_authority_version') == 1:
        if job.get('role_guidance') and job.get('native_role_instruction') != job['role_guidance']:
            raise ValueError('Native role instruction differs from its frozen scientific guidance')
    task_view=_validate_task_view(root,work,job)
    if job['global_materials'] != _global_materials(root,base=base):
        raise ValueError('Bound global context changed')
    if job.get('purpose')=='worker_consultation':
        target=job.get('evidence_return') or {}
        requested=sources.get(target.get('step'))
        expected='revise_h' if job['role']=='H' else 'review'
        if (job['role'] not in {'H','A'} or set(target)!={'role','step'} or target.get('role')!='W'
                or requested is None or requested['job']['role']!='W'
                or requested['payload'].get('stage_intent')!=expected):
            raise ValueError('Worker consultation lost its exact authored requester')
    if job.get('purpose') == 'closeout':
        from . import budget, delivery
        boundary = job.get('closeout_begin') or {}
        events, _ = budget._events_from_state(head)
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
        for ref in job['direct_refs']:
            accepted_prefix.member(root,accepted_prefix.for_job(job),int(Path(ref['path']).parts[2]))
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
    from . import history
    history.validate(root, work, job['history'])
    if 'assessment_handoff' in job:
        handoff=_load_assessment_handoff(root,job,base=base,sources=sources,members=members,policy=policy)
        if job['assessment_handoff'] != handoff:
            raise ValueError('Assessment sources changed their exact W/H decision lineage')
        original = _assessment_original(job['assessment_handoff'],sources=sources)
        if original is not None and [row for row in job['history']['inputs'] if row['path'] == original['path']] != [original]:
            raise ValueError('Assessment original differs from its exact producer')
        prior = job['assessment_handoff'].get('previous_a')
        if prior is not None and not set(prior['materials']) <= {row['path'] for row in job['direct_materials']}:
            raise ValueError('Assessment materials are absent from its exact inputs')
    expected = []
    previous = f'inputs/parents/{job["step"] - 1:03d}/conclusion.json'
    if job.get('formal_review'):
        expected=formal_review.input_binding(root,job['formal_review'],sources=sources)['selected']
    elif any(ref['path'] == previous for ref in job['direct_refs']):
        from . import handoff_state
        predecessor = sources[job['step']-1]
        expected = handoff_state.selected_materials(root,predecessor['seal'],sources=sources,
            base=base,source=predecessor['owner'])
    if job.get('selected_inputs', []) != expected:
        raise ValueError('Bound implementation selection differs from its predecessor')
    from . import originals
    previous_seal = sources[job['step']-1]['seal'] if any(
        ref['path'] == previous for ref in job['direct_refs']) else {}
    if job.get('input_selection_status') != previous_seal.get('control_outcome', {}).get('selection_status', 'applied'):
        raise ValueError('Bound implementation resolution differs from its predecessor')
    if job.get('control_diagnostics') != previous_seal.get('control_outcome', {}).get('diagnostics', []):
        raise ValueError('Bound control diagnostics differ from their predecessor')
    if any(row['material'] not in job['direct_materials'] for row in expected):
        raise ValueError('Selected implementation is absent from bound materials')
    global_paths={}
    for row in job['global_materials']:
        global_paths[row['path']]=originals.resolve_root_ref(root, dict(row,path=row['source']))
        if (work/row['path']).exists() and regular(work/row['path']).stat().st_size!=row['size_bytes']:
            raise ValueError('The global context read by this role changed')
    if [row['path'] for row in job.get('direct_originals',[])]!=[row['path'] for row in job['direct_refs']]:
        raise ValueError('Scientific parent view lost its exact original')
    for ref in job['direct_refs']:
        _accept_parent_public(root,job,ref,source=sources[int(Path(ref['path']).parts[2])])
    for ref in job['direct_materials']:
        path = regular(inputs['material_paths'][ref['path']])
        if path.stat().st_size != ref['size_bytes']:
            raise ValueError('Scientific original changed')
    for ref in job['direct_materials']:
        if (work/ref['path']).exists() and regular(work/ref['path']).stat().st_size!=ref['size_bytes']:
            raise ValueError('The direct material actually read by this role changed')
    return dict(document=document, sources=sources, inputs=inputs,task_view=task_view,global_paths=global_paths)


def _validate_inputs_from_facts(root,work,job,*,base,policy,head):
    return load_validated_input_facts(root,work,job,base=base,policy=policy,head=head)['document']


@owner_effect
def seal_result(root: Path, step: int, *, inventory=None):
    root=Path(root)
    work = workspace(root, step)
    record = host_record(work)
    if (record / 'result-identity.json').exists():
        from .protocol import require_current
        require_current(root,read(regular(root/'jobs'/f'{step:03d}.json')))
        from . import handoff_state,accepted_prefix
        accepted_prefix.reconcile(root)
        return handoff_state.read_sealed(root, step)
    if inventory is None:
        return seal_result_from_inventory(root,step,inventory=accept_current_inventory(root,step))
    _inventory_identity(root,step,inventory)
    if inventory.policy is None:
        raise ValueError('Current inventory requires its produced policy')
    head=_current_inventory_head(root,step,inventory)
    from .protocol import require_current_job
    require_current_job(root,base=head.base,job=inventory.job,retained=inventory.job,policy=inventory.policy)
    return _seal_result(root,step,inventory=inventory)


@owner_effect
def seal_result_from_inventory(root: Path, step: int, *, inventory):
    """Seal the inventory produced and terminally accepted by this operation."""
    root=Path(root)
    if inventory.base is None or inventory.policy is None:
        raise ValueError('Current inventory requires its produced base and policy')
    _validate_current_inventory_owner(root,step,inventory,head=inventory.head)
    from .protocol import require_current_job
    require_current_job(root,base=inventory.base,job=inventory.job,retained=inventory.job,policy=inventory.policy)
    return _seal_result(root,step,inventory=inventory)


def _seal_result(root,step,*,inventory):
    """Publish this operation's accepted science without rebuilding its facts."""
    _inventory_identity(root,step,inventory)
    from . import execution
    work=workspace(root,step);record=host_record(work)
    job_path=root/'jobs'/f'{step:03d}.json'
    job=inventory.job
    native_record = inventory.record
    intent=inventory.native_intent
    if intent['task_ref'] != inventory.task_ref:
        raise ValueError('Native launch task changed')
    outcome=inventory.native_outcome
    if inventory.policy is None:
        raise ValueError('Current inventory requires its produced policy')
    policy=inventory.policy
    from . import worker_delivery
    from . import budget
    final = budget._final_a_return_from_facts(root,job,intent,policy=policy,
        events=budget._events_from_state(inventory.head)[0])
    budget._validate_final_a_receipt_from_facts(intent,outcome,
        policy=policy,meter=inventory.native_meter,accepted=final)
    native_raw_ref=outcome.get('raw_ref')
    if (type(outcome.get('returncode')) is not int or outcome['returncode'] != 0
            or outcome.get('stop_reason')
            or budget.final_a_usage_unknown(final, outcome['usage']) and not budget.native_completion_known(outcome, policy)):
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
        raise OutputRepairRequired('missing_output', 'Return complete scientific JSON; preserve original facts/plans/limits. No work this repair turn; reuse prior results/checkpoints only if they exist.')
    raw = regular(raw_path)
    if native_raw_ref != file_ref(raw):
        raise ValueError('Terminal scientific output is unverified or changed')
    if inventory.output_error is not None:
        raise inventory.output_error
    value = envelope(job,inventory.payload)
    if job['role']=='W':
        from . import managed_wait
        managed_wait.before_seal(root,job,native_record,inventory=inventory)
        if (value.get('stage_intent') in {'handoff','revise_h','review'} and (
                any(call['status']=='unknown' for call in inventory.managed_calls)
                or ((job.get('search') or {}).get('breadth') or {}).get('prior_unknown'))):
            from .continuation import ResumeUnavailable
            raise ResumeUnavailable('worker_handoff_original_execution_unknown')
        if worker_delivery.applicable(job):
            from . import checkpoint
            if not (native_record/'checkpoint.json').exists():
                inventory.checkpoint
            else:checkpoint.validate(root,job,native_record,inventory=inventory)
        from . import candidate_nominations
        candidate_nominations.require(root,job,value,inventory=inventory)
    control = None
    from . import semantic
    from .output_repair import WorkRepairRequired, OutputRepairRequired
    try:
        control = semantic.outcome(root, job, value,inventory=inventory)
    except WorkRepairRequired as error:
        error.inventory=inventory
        raise
    except OutputRepairRequired as error:
        error.inventory=inventory
        inventory.output_error=error
        raise
    from . import correction
    correction.validate_payload(root, job, value)
    materials = collect_materials(root, step,inventory=inventory)
    unresolved = dict(items=[], omitted_count=0)
    entry_points = artifact_entry_points(work, value, materials, job['handoff_protocol'], unresolved)
    selected = selected_inputs(inventory)
    relayed = _relayed_materials(job, inventory.payload, selected)
    from . import material_references
    observations = None
    from . import history
    history.validate_recommendation(job, value, root=root)
    from .execution_resume import capture_pending
    # Freeze the final attempt only after delivery/repair gates, and before
    # result snapshots or conclusions. A busy namespace leaves these untouched.
    pending_executions = capture_pending(root, job)
    observations = history.capture(root, job, value, materials,inventory=inventory)
    if raw.stat().st_mode & 0o777 != 0o400:raw.chmod(0o400)
    from . import experiments
    raw_ref=experiments.proof(raw)
    private_materials=[dict(row,file_stat=experiments.proof(
        Path(row['source']))['file_stat']) for row in materials]
    projected=scientific_projection(value,'node:%03d/scientific-output' % job['step'])
    write(record / 'result.json',projected)
    execution_ref=execution._reference(inventory.chain)
    seal = dict(result_ref=file_ref(record / 'result.json'), raw_ref=raw_ref,
          job_ref=inventory.task_ref, materials=private_materials, entry_points=entry_points,
          scope='Exact bytes and direct lineage; not scientific qualification',
          **(dict(execution_ref=execution_ref) if execution_ref is not None else {}),
          **(dict(control_outcome=control) if control is not None else {}),
          unresolved_links=unresolved,
          **(dict(selected_inputs=material_references.selected_record(selected)) if selected else {}),
          **(dict(relayed_material_refs=relayed) if relayed is not None else {}),
          **(dict(observations=observations) if observations else {}))
    observed_inputs=history.validate_current_observation(root,job,seal,inventory=inventory)
    inventory.observation=observations
    if observations is not None:
        observer_unknown=(policy.get('history_observer') is not None and
            (observed_inputs is None or not observed_inputs['complete']))
        inventory.additional_inputs=((bool(inventory.managed_calls) if inventory.input_coverage is None else
            not all(row['complete'] for row in inventory.input_coverage)) or observer_unknown)
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
        seal['source_provenance'] = qualification.seal_outputs(root, job, seal,inventory=inventory)
    if job.get('formal_review_version')==1:
        from . import formal_review
        seal['native_authorship']=formal_review.capture_participants(root,job)
        if job.get('formal_review'):
            seal['formal_review']=formal_review.seal_review(root,job,value,seal)
    # These are the remaining publication gates. Captures above already checked
    # the exact current inputs, execution, selection, material and compilation
    # originals; publication must not reopen that same complete acceptance.
    conclusion=work/'conclusion.json'
    if (conclusion.exists() or conclusion.is_symlink()) and regular(conclusion).stat().st_size!=raw_ref['size_bytes']:
        raise ValueError('Saved scientific conclusion projection changed')
    if not isinstance(control,dict):
        raise ValueError('Current scientific attempt has no control outcome')
    delivery_facts=(control.get('worker_delivery') or {}).get('facts_ref')
    if delivery_facts is not None:
        if delivery_facts!=record_ref(regular(native_record/worker_delivery.FACT_NAME)):
            raise ValueError('Final W scientific evidence differs from its seal')
        worker_delivery.validate(root,job,native_record,inventory=inventory,source_attempt=intent.get('attempt',0))
    if pending_executions is not None:
        from .execution_resume import verify_pending_set
        verify_pending_set(root,seal)
    from . import formal_review
    formal_review.validate_seal(root,job,value,seal)
    from . import accepted_prefix
    pending_public=accepted_prefix.prepare_node(root,job,value,seal,inventory=inventory)
    write(record / 'result-identity.json', seal)
    accepted_prefix.commit_node_from_facts(root,pending_public,base=inventory.base,head=inventory.head,seal=seal)
    return projected


def _execution_raw(root, step):
    from . import execution, originals
    if originals.inherited(root, step):
        return originals.source_raw(originals.node(root, step))
    return execution.raw(root, step)
