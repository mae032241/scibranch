"""Optional W-authored interpretations of exact original calls.

The IO owner publishes originals. The projection functions below consume values
only: an authored claim is neither an execution fact nor scientific approval.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import re
import shutil
import tempfile

_PUBLICATION_REQUEST=ContextVar('research_note_publication_request',default=None)


@contextmanager
def publication_request(binding,acknowledge):
    token=_PUBLICATION_REQUEST.set(dict(binding=binding,acknowledge=acknowledge))
    try:yield
    finally:_PUBLICATION_REQUEST.reset(token)


VERSION = 1
MAX_BYTES = 4096
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
INTERPRETATION = ('W-authored notes are claims, not execution truth, scientific qualification, '
                  'review verdicts or execution permission. Superseded originals remain available.')


# Pure identity and public projection helpers. No filesystem operations here.
def identity(value):
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError('Choose an ordinary stable note identity using letters, numbers, dot, dash or underscore.')
    return value


def validate(value, *, scope, accepted):
    from .experiments import IntegrityError
    if (not isinstance(value, dict) or value.get('version') != VERSION
            or value.get('scope') != scope or value.get('call_id') != accepted.get('call_id')
            or value.get('native_attempt') != accepted.get('native_attempt')
            or value.get('mechanism_binding') != accepted.get('mechanism_binding')
            or accepted.get('scope') != scope):
        raise IntegrityError('Authored note differs from its exact original call binding')
    if type(value.get('sequence')) is not int or value['sequence']<1:
        raise IntegrityError('Authored note publication order changed')
    author=value.get('author_attempt')
    if (not isinstance(author,dict) or set(author)!={'number','execution_key'}
            or type(author.get('number')) is not int or author['number']<0
            or not isinstance(author.get('execution_key'),dict)):
        raise IntegrityError('Authored note lost its exact author attempt')
    identity(value.get('note_id'))
    if value.get('supersedes') is not None:identity(value['supersedes'])
    return value


def public_record(value):
    """Allowlist frozen scientific identity; private original references stay private."""
    from .worker_research import public_mechanism, public_text
    scope=value['scope'];binding=value.get('mechanism_binding') or {}
    research=binding.get('research_binding') or {}
    question=research.get('experiment_question') or {}
    result=dict(version=VERSION,note_id=value['note_id'],call_id=value['call_id'],
        author=dict(node=f'W{scope["step"]:03d}',attempt=value['author_attempt']['number']),
        supersedes=value.get('supersedes'),original='original.raw',interpretation=INTERPRETATION)
    mechanism=public_mechanism(research)
    if mechanism is not None:result['mechanism']=mechanism
    elif isinstance(binding.get('mechanism_ref'),str):
        result['mechanism_ref']=public_text(binding['mechanism_ref'],128)
    if question:result['experiment_question']={key:public_text(question.get(key),240)
        for key in ('question','change_criterion')}
    return result


def _scope(value):
    from .execution_facts import _public_scope
    if isinstance(value,dict) and value.get('kind')=='current_worker':
        checked=_public_scope(dict(value,kind='stopped_worker'))
        return dict(checked,kind='current_worker')
    return _public_scope(value)


def _locator(value):
    from .execution_facts import _locator as locator
    return locator(value)


def render(records, *, scope, public_refs, unavailable_notes=(), max_bytes=MAX_BYTES):
    """Bound navigation only; the exact authored body is never preview-truncated."""
    from .execution_facts import _public_scope, serialized_bytes
    scope=_scope(scope)
    if (not isinstance(records,(list,tuple)) or not isinstance(public_refs,dict)
            or type(max_bytes) is not int or not 768<=max_bytes<=MAX_BYTES):
        raise ValueError('Authored notes need explicit values, public references and a bounded view')
    rows=[]
    for record in records:
        value=public_record(record)
        refs=public_refs.get(record['note_id'],{})
        value['navigation']={key:path for key in ('record','original')
            if (path:=_locator(refs.get(key))) is not None}
        rows.append(value)
    diagnostics=[dict(note_id=item.get('note_id') if isinstance(item.get('note_id'),str) and _ID.fullmatch(item['note_id']) else None,
        diagnostic='research_note_original_unavailable') for item in unavailable_notes]
    value=dict(version=VERSION,scope=scope,note_count=len(rows)+len(diagnostics),
        omitted_rows=len(diagnostics),notes=rows,unavailable_notes=diagnostics,
        unavailable_note_count=len(diagnostics),coverage='partial' if diagnostics else 'accepted_scope',
        navigation=[],interpretation=INTERPRETATION)
    while len(serialized_bytes(value))>max_bytes and value['notes']:
        value['notes'].pop(0);value['omitted_rows']+=1
    while len(serialized_bytes(value))>max_bytes and value['unavailable_notes']:
        value['unavailable_notes'].pop()
    if len(serialized_bytes(value))>max_bytes:raise ValueError('Authored note view exceeds its bound')
    return value


def _public_row(row):
    from .worker_research import public_mechanism, public_text
    author=row.get('author') or {}
    if (not isinstance(author.get('node'),str) or re.fullmatch(r'W[0-9]{3,}',author['node']) is None
            or type(author.get('attempt')) is not int or author['attempt']<0):
        raise ValueError('Authored note view requires exact public author identity')
    if (not isinstance(row.get('call_id'),str) or re.fullmatch(r'[A-Za-z0-9._-]{1,128}',row['call_id']) is None):
        raise ValueError('Authored note view requires an exact original call identity')
    result=dict(version=VERSION,note_id=identity(row.get('note_id')),
        call_id=row.get('call_id'),author=dict(node=author['node'],attempt=author['attempt']),
        supersedes=identity(row['supersedes']) if row.get('supersedes') is not None else None,
        original='original.raw',interpretation=INTERPRETATION)
    mechanism=public_mechanism(row.get('mechanism'))
    if mechanism is not None:result['mechanism']=mechanism
    if row.get('mechanism_ref') is not None:result['mechanism_ref']=public_text(row['mechanism_ref'],128)
    if isinstance(row.get('experiment_question'),dict):
        result['experiment_question']={key:public_text(row['experiment_question'].get(key),240)
            for key in ('question','change_criterion')}
    result['navigation']={key:path for key,item in row.get('navigation',{}).items()
        if key in {'record','original'} and (path:=_locator(item)) is not None}
    return result


def combine(previous,current,*,scope,source_cutoff,node_count,navigation,max_bytes=MAX_BYTES):
    """Carry accepted projections across a prefix, never reopen note originals."""
    from .execution_facts import _public_scope, serialized_bytes
    value=dict(version=VERSION,scope=_scope(scope),source_cutoff=source_cutoff,
        node_count=node_count,note_count=0,omitted_rows=0,notes=[],unavailable_notes=[],
        unavailable_note_count=0,coverage='accepted_scope',
        navigation=list(dict.fromkeys(path for item in navigation if (path:=_locator(item)) is not None)),
        interpretation=INTERPRETATION)
    for document in (previous,current):
        if not isinstance(document,dict) or document.get('version')!=VERSION:continue
        value['note_count']+=document.get('note_count',0)
        value['omitted_rows']+=document.get('omitted_rows',0)
        if document.get('coverage','accepted_scope')!='accepted_scope':value['coverage']='partial'
        value['unavailable_note_count']+=document.get('unavailable_note_count',0)
        for item in document.get('unavailable_notes',[]):
            ref=item.get('note_id')
            value['unavailable_notes'].append(dict(note_id=ref if isinstance(ref,str) and _ID.fullmatch(ref) else None,
                diagnostic='research_note_original_unavailable'))
        for row in document.get('notes',[]):
            # These are already public projections; retain only their public schema.
            result=_public_row(row)
            value['notes'].append(result)
    while len(serialized_bytes(value))>max_bytes and value['notes']:
        value['notes'].pop(0);value['omitted_rows']+=1
    while len(serialized_bytes(value))>max_bytes and value['navigation']:value['navigation'].pop(0)
    while len(serialized_bytes(value))>max_bytes and value['unavailable_notes']:
        value['unavailable_notes'].pop()
    if len(serialized_bytes(value))>max_bytes:raise ValueError('Authored note view exceeds its bound')
    return value


# IO producers. Internal consumers must receive their already loaded values.
def _same_bytes(left,right):
    with Path(left).open('rb') as first,Path(right).open('rb') as second:
        while True:
            a=first.read(1024*1024);b=second.read(1024*1024)
            if a!=b:return False
            if not a:return True


def _paths(config,note_id):
    identity(note_id)
    return Path(config['record_dir'])/'research-notes'/note_id


def _acquire(config,directory,*,accepted,value):
    from . import contracts as c
    from .experiments import IntegrityError
    if directory.resolve()!=directory or directory.is_symlink():
        raise IntegrityError('Authored note namespace changed')
    value=validate(value,scope=config['scope'],accepted=accepted)
    if value['note_id']!=directory.name:raise IntegrityError('Authored note identity changed')
    from .experiments import proof
    original=proof(c.regular(directory/'original.raw'))
    if original!=value.get('raw_proof'):
        raise IntegrityError('Authored note original bytes changed')
    return dict(value,record=dict(path=str(directory/'note.json')),
        original=dict(path=str(directory/'original.raw')),
        public=dict(record=f'work/execution-results/research-notes/{directory.name}/note.json',
            original=f'work/execution-results/research-notes/{directory.name}/original.raw'))


def _loaded(config,directory,*,accepted):
    from . import contracts as c
    return _acquire(config,directory,accepted=accepted,value=c.read(c.regular(directory/'note.json')))


def load(config, *, accepted_calls, accepted_attempts):
    """Stopped owner repairs complete notes, preserving valid siblings on failure.

    The accepted call values are acquired by the inventory owner before this
    optional producer. Missing or invalid notes do not qualify their claims.
    """
    from . import contracts as c, experiment_client
    from .experiments import IntegrityError
    root=Path(config['record_dir'])/'research-notes'
    empty=dict(records=[],unavailable_notes=[],coverage='accepted_scope',public_index=None)
    if not root.exists():return empty
    if root.resolve()!=root:raise IntegrityError('Authored note namespace changed')
    result=[];diagnostics=[]
    for directory in sorted(root.iterdir()):
        if directory.name.startswith('.'):continue
        try:
            value=c.read(c.regular(directory/'note.json'))
            accepted=accepted_calls.get(value.get('call_id'))
            if not isinstance(accepted,dict):raise IntegrityError('Authored note lost its accepted original call')
            if value.get('author_attempt') not in accepted_attempts:
                raise IntegrityError('Authored note lost its exact accepted author attempt')
            loaded=_acquire(config,directory,accepted=accepted,value=value)
            instance=experiment_client._configuration_root(config,allow_standalone=False)
            work=c.workspace(instance,config['scope']['step'])
            result.append(_publish_public(config,loaded,work=work))
        except (OSError,ValueError,TypeError,KeyError,UnicodeError):
            diagnostics.append(dict(note_id=directory.name if _ID.fullmatch(directory.name) else None,
                diagnostic='research_note_original_unavailable'))
    public_index=None
    if result:
        try:
            _write_live_index(config,work,result,unavailable_notes=diagnostics)
            public_index='work/execution-results/research-notes/index.json'
        except (OSError,ValueError,TypeError,KeyError):pass  # Optional index failure preserves acquired originals.
    return dict(records=sorted(result,key=lambda row:row['sequence']),unavailable_notes=diagnostics,
        coverage='partial' if diagnostics else 'accepted_scope',public_index=public_index)


def _publish_public(config,value,*,work):
    from . import contracts as c, shared_input_views
    from .experiments import IntegrityError, proof
    shared_input_views.dynamic_root(work.parents[2],work,'execution-results')
    directory=work/'work/execution-results/research-notes'/value['note_id']
    directory.mkdir(parents=True,exist_ok=True)
    if directory.resolve()!=directory:raise IntegrityError('Public authored note namespace changed')
    metadata=public_record(value);target=directory/'note.json'
    if target.exists():
        if c.read(c.regular(target))!=metadata:raise IntegrityError('Public authored note metadata changed')
    else:c.write(target,metadata)
    target.chmod(0o400)
    target=directory/'original.raw';source=Path(value['original']['path'])
    c._bind_original(source,target,root=work.parents[2],
        size_bytes=source.stat().st_size,source_kind='managed_frozen',policy=config.get('storage_policy'))
    target.chmod(0o400)
    return dict(value,publication=dict(record=proof(directory/'note.json'),
        original=proof(target)))


def _write_live_index(config,work,records,*,unavailable_notes=()):
    from . import contracts as c
    from .execution_facts import serialized_bytes
    records=sorted(records,key=lambda row:row['sequence'])
    refs={row['note_id']:dict(record=f'work/execution-results/research-notes/{row["note_id"]}/note.json',
        original=f'work/execution-results/research-notes/{row["note_id"]}/original.raw') for row in records}
    view=render(records,scope=dict(kind='current_worker',node=f'W{config["scope"]["step"]:03d}'),
        public_refs=refs,unavailable_notes=unavailable_notes)
    view['navigation']=['work/execution-results/research-notes/index.json']
    view['directory']=dict(kind='directory',path='work/execution-results/research-notes',
        layout='<note-id>/note.json and <note-id>/original.raw',
        read_hint='List this one directory to discover omitted note IDs, then read selected note.json and original.raw files.',
        scope='Live same-task directory; it may include later native attempts and is not a frozen inventory snapshot.')
    while len(serialized_bytes(view))>MAX_BYTES and view['notes']:
        view['notes'].pop(0);view['omitted_rows']+=1
    while len(serialized_bytes(view))>MAX_BYTES and view['unavailable_notes']:
        view['unavailable_notes'].pop()
    c.write(work/'work/execution-results/research-notes/index.json',view)


def _rebuild_live_index(config,work,*,current_record):
    """Observe only same-task note metadata and public slot availability.

    Complete private siblings whose publication was interrupted remain explicit
    omissions until their owner repairs them. An index is never a membership
    source, and no scientific body or earlier task is read here.
    """
    from . import contracts as c, experiments as e
    root=Path(config['record_dir'])/'research-notes'
    records=[];diagnostics=[]
    try:
        for directory in sorted(root.iterdir()):
            if directory.name.startswith('.'):continue
            try:
                if directory.resolve()!=directory:
                    raise e.IntegrityError('Authored note navigation namespace changed')
                metadata=(current_record if directory.name==current_record['note_id']
                    else c.read(c.regular(directory/'note.json')))
                if (metadata.get('scope')!=config['scope'] or metadata.get('note_id')!=directory.name
                        or type(metadata.get('sequence')) is not int or metadata['sequence']<1):
                    raise e.IntegrityError('Authored note navigation lost its exact task identity')
                public=work/'work/execution-results/research-notes'/directory.name
                if public.resolve()!=public:raise e.IntegrityError('Authored note public namespace changed')
                if directory.name!=current_record['note_id']:
                    if c.read(c.regular(public/'note.json'))!=public_record(metadata):
                        raise e.IntegrityError('Authored note public metadata changed')
                    original=c.regular(directory/'original.raw')
                    if e.proof(original)!=metadata.get('raw_proof') or not c.regular(public/'original.raw').samefile(original):
                        raise e.IntegrityError('Authored note public original unavailable')
                records.append(metadata)
            except (OSError,ValueError,TypeError,KeyError,UnicodeError):
                diagnostics.append(dict(note_id=directory.name if _ID.fullmatch(directory.name) else None,
                    diagnostic='research_note_original_unavailable'))
        _write_live_index(config,work,records,unavailable_notes=diagnostics)
    except (OSError,ValueError,TypeError,KeyError):pass  # Optional navigation cannot erase a durable acknowledgement.


def refresh_navigation(config,*,note_id):
    """Receipt owner refreshes navigation after verifying exact note proofs."""
    from . import contracts as c, experiment_client
    try:
        directory=_paths(config,note_id)
        value=c.read(c.regular(directory/'note.json'))
        root=experiment_client._configuration_root(config,allow_standalone=False)
        _rebuild_live_index(config,c.workspace(root,config['scope']['step']),current_record=value)
    except (OSError,ValueError,TypeError,KeyError):pass  # Original receipt proofs were verified by the owner.


def _acknowledgement(value):
    return dict(status='research_note_recorded',note_id=value['note_id'],
        call_id=value['call_id'],note=value['public'],interpretation=INTERPRETATION)


def recover(config,*,note_id,call_id,binding):
    """Confirm publication by this claimed request, without source rereads."""
    from . import contracts as c, experiments as e, experiment_client
    directory=_paths(config,note_id)
    if not directory.exists():return None
    value=c.read(c.regular(directory/'note.json'))
    if value.get('publication_binding')!=binding:return None
    accepted=e.Records(config).accepted(call_id)
    loaded=_acquire(config,directory,accepted=accepted,value=value)
    root=experiment_client._configuration_root(config,allow_standalone=False)
    work=c.workspace(root,config['scope']['step'])
    published=_publish_public(config,loaded,work=work)
    _rebuild_live_index(config,work,current_record=published)
    return _acknowledgement(published)


def publish(config, *, accepted, note_id, source, supersedes=None, facts):
    """Publish one optional note; retries compare exact bytes without body hashes."""
    from . import contracts as c, experiments as e
    try:
        identity(note_id)
        if supersedes is not None:identity(supersedes)
    except ValueError as error:raise e.CallerError(str(error),error='invalid_note') from error
    if (config.get('scope',{}).get('role')!='W' or config.get('native_attempt') is None):
        raise e.CallerError('Authored notes require an exact W task and native attempt.',error='invalid_note')
    work=facts['work']
    if work is None:raise e.CallerError('Authored notes require the assigned W workspace.',error='invalid_note')
    records=e.Records(config)
    with records.lease():
        destination=_paths(config,note_id)
        if destination.exists():
            metadata=c.read(c.regular(destination/'note.json'))
            original_accepted=(accepted if metadata.get('call_id')==accepted['call_id']
                else records.accepted(metadata.get('call_id')))
            value=_acquire(config,destination,accepted=original_accepted,value=metadata)
            if (value['call_id']!=accepted['call_id'] or value['supersedes']!=supersedes
                    or not _same_bytes(value['original']['path'],source)):
                raise e.CallerError('This note-id already belongs to different original bytes. Use a new note-id and --supersedes the exact prior note-id.',error='note_identity_conflict')
        else:
            if supersedes is not None:
                prior_path=_paths(config,supersedes)
                if not prior_path.exists():raise e.CallerError('The superseded note-id must name an exact existing current-task note.',error='invalid_note_revision')
                prior=c.read(c.regular(prior_path/'note.json'))
                if prior.get('call_id')!=accepted['call_id']:
                    raise e.CallerError('A note revision must retain its exact original call.',error='invalid_note_revision')
                _acquire(config,prior_path,accepted=accepted,value=prior)
            destination.parent.mkdir(parents=True,exist_ok=True)
            temporary=Path(tempfile.mkdtemp(prefix='.',dir=destination.parent))
            try:
                before=e.proof(source)
                c._bind_original(Path(source),temporary/'original.raw',root=work.parents[2],
                    size_bytes=before['size_bytes'],source_kind='mutable',policy=config.get('storage_policy'))
                if e.proof(source)!=before:
                    raise e.CallerError('The optional note file changed during publication. Retain its bytes and retry the note request.',error='note_file_changed')
                sequence=1+sum(1 for path in destination.parent.iterdir() if not path.name.startswith('.'))
                value=dict(version=VERSION,note_id=note_id,call_id=accepted['call_id'],sequence=sequence,
                    scope=deepcopy(accepted['scope']),native_attempt=deepcopy(accepted['native_attempt']),
                    author_attempt=deepcopy(config['native_attempt']),
                    publication_binding=deepcopy((_PUBLICATION_REQUEST.get() or {}).get('binding')),
                    mechanism_binding=deepcopy(accepted.get('mechanism_binding')),supersedes=supersedes)
                raw_proof=e.proof(temporary/'original.raw')
                value['raw_proof']=dict(raw_proof,path=str(destination/'original.raw'))
                c.write(temporary/'note.json',value)
                for name in ('note.json','original.raw'):(temporary/name).chmod(0o400)
                os.rename(temporary,destination)
            finally:
                if temporary.exists():shutil.rmtree(temporary)
            value=_loaded(config,destination,accepted=accepted)
        value=_publish_public(config,value,work=work)
        acknowledgement=_acknowledgement(value)
        request=_PUBLICATION_REQUEST.get()
        if request is not None:request['acknowledge'](acknowledgement)
        _rebuild_live_index(config,work,current_record=value)
        return acknowledgement
