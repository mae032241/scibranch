"""One explicitly authorized fresh author turn over stopped W originals.

This is an operator closeout, not a retroactive resume policy or experiment retry.
The old outcome, incomplete meter and every accepted experiment remain original.
"""
import json
from pathlib import Path

from . import configuration_updates
from . import contracts as c


def _context_projection(path, value, references, source_text):
    """Only known framework documents receive a scientific display projection."""
    from . import candidate_refs,history
    parts=Path(path).parts
    if parts[:2]==('inputs','parents') and parts[-1]=='conclusion.json':
        return candidate_refs.public_science_fields(value,references)
    if len(parts)==5 and parts[:3]==('inputs','history','nodes'):
        step=int(parts[3]);part=int(Path(parts[4]).stem)
        text=source_text[step]
        return dict(value,text=text[(part-1)*history.TEXT_CHARS:part*history.TEXT_CHARS])
    if parts[:3]==('inputs','history','experiments'):
        value=dict(value,experiments=[{key:item for key,item in row.items()
            if key not in {'retirement_proof','native_originals','native_originals_index'}} for row in value['experiments']])
    return candidate_refs.public_fields(value,references)


def _context_sources(root, job):
    from . import candidate_refs,lineage
    projection=lineage.project(root,job['step'])
    references=candidate_refs.references(projection['records'])
    texts={}
    for source in projection['sources']:
        step=source[0]['step']
        if not references:texts[step]=c.regular(c._execution_raw(root,step)).read_text(encoding='utf-8')
        else:
            public=c.scientific_projection(candidate_refs.public_science_fields(source[1],references),
                f'inputs/history/nodes/{step:03d}/scientific-original.json')
            texts[step]=json.dumps(public,ensure_ascii=False,allow_nan=False)
    return references,texts


def _context_reference(root, path, size_bytes):
    """A scientific source descriptor, separate from root-relative control refs."""
    return dict(path=str(Path(path)), source=str(Path(path)), size_bytes=size_bytes)


def _context_source(root, reference):
    from . import originals
    return (originals.material_path(root, reference) if 'source' in reference
        else _check(root, reference))


def _context_compiler(root, job, item, *, controls_only=True):
    """A permanent compiler identity never depends on an earlier export inode."""
    from . import execution_inputs
    if item['source'] != item['compiler']:
        raise ValueError('Author compiler source is not its exact frozen input')
    if item['material'] in job['direct_materials']:
        compiler = execution_inputs.verify_compilation(root, item['compiler'], material=item['material'],
            client_binding=item.get('client_binding'), controls_only=controls_only)
    else:
        binding, compiler = execution_inputs.verify_legacy_author_client(root, job, item['client_binding'])
        grant = binding['legacy_authorization']
        if grant['compiler'] != item['compiler'] or grant['material'] != item['material']:
            raise ValueError('Author compiler source changed its existing authorization')
    if item.get('code_version') != compiler.get('code_version') or 'code_version' not in compiler:
        raise ValueError('Author compiler scientific code version changed')
    owner = c.read(_check(root, compiler['job']))
    execution_inputs._compiler_call_id(root, owner['step'], item['compiler'])
    return compiler


def _context_code(root, job, item, *, controls_only=True):
    """Interpret only an existing compiler or exact retained accepted-call entry."""
    if item['kind'] == 'compiler_author':
        compiler = _context_compiler(root, job, item, controls_only=controls_only)
        owner = c.read(_check(root, compiler['job']))
        from . import execution_inputs
        return owner['step'], execution_inputs._compiler_call_id(root, owner['step'], item['compiler'])
    from . import code_versions, experiments as e
    from .experiment_client import record_directory
    accepted = c.read(_check(root, item['source']))
    call_id = accepted['call_id']
    config = c.read(c.regular(Path(root) / 'native-evidence' / f'{job["step"]:03d}' / 'experiment-config.json'))
    expected = record_directory(root, job['step']) / 'calls' / call_id / 'accepted.json'
    if (_check(root, item['source']) != expected or accepted['scope'] != config['scope']
            or accepted['interface'] != config['execution_interface']
            or accepted.get('code_version') != item['code_version']
            or e._code_version_binding(config, accepted) != Path(root)):
        raise ValueError('Author scientific code lost its exact accepted call')
    code_versions._version(item['code_version'])
    if not controls_only:
        code_versions.verify(root, step=job['step'], call_id=call_id, code_version=item['code_version'])
    return job['step'], call_id


def science_context(root, job, record, *, permit=None, attempt=None):
    """A new author reads independent displays; retained task inputs stay frozen."""
    root=Path(root);work=c.workspace(root,job['step']);record=Path(record)
    c._validate_inputs(root,work,job)
    references,texts=_context_sources(root,job)
    from . import candidate_refs, originals
    public=record/'author-science-context';public.mkdir()
    from . import shared_input_views
    shared_input_views.dynamic_root(root,public,'retained-implementations')
    original_task=work/'science-task.json'
    target=public/'science-task.json'
    task=candidate_refs.public_fields(c.read(original_task),references)
    c.write(target,task);target.chmod(0o400)
    entries=[dict(kind='task',source=_ref(root,original_task),display=_ref(root,target))]
    input_rows={item['path']: item for item in [*job['global_materials'], *job['direct_materials'],
        *job['direct_refs'], *job['history']['files'], *job['history'].get('execution_recovery_materials', [])]}
    paths={item['path']:'source' for item in [*job['global_materials'],*job['direct_materials']]}
    compiled={}
    from . import execution_inputs,qualification
    for material in job['direct_materials']:
        producer=execution_inputs._producer(root,material)
        seal=qualification._read(root,producer['producer_seal'])
        matches=[item for item in seal.get('compiled_implementations',[]) if item['material']==material]
        if not matches:continue
        if len(matches)!=1:raise ValueError('Author scientific implementation is ambiguous')
        binding=matches[0]
        compiler=execution_inputs.verify_compilation(root,binding['compiler'],material=material,
            client_binding=binding.get('client_binding'))
        compiled[material['path']]=dict(material=material,binding=binding,compiler=compiler)
        paths[material['path']]='compiler_author'
        owner=qualification._read(root,compiler['job'])
        for item in compiler['inputs']:
            original=execution_inputs.resolve_native_original(root,item['original'],compiler_job=owner)
            destination=public/item['alias']
            source=originals.material_path(root, original)
            entries.append(dict(kind='source',source=dict(original, source=str(source)),
                display=dict(path=destination.relative_to(root).as_posix(), size_bytes=original['size_bytes'])))
    paths.update((item['path'],'framework') for item in job['direct_refs'])
    paths.update((item['path'],'source' if item.get('kind')=='scientific_original' else 'framework')
        for item in job['history']['files'])
    # _validate_inputs verified these copies against the same original source
    # receipts. They remain scientific originals, never JSON display pages.
    for original in job.get('direct_originals',[]):
        parent=int(Path(original['path']).parts[2]);raw=c.regular(c._execution_raw(root,parent))
        reported=candidate_refs.public_science_fields(c.read(raw),candidate_refs.for_job(job))
        name=str(Path(original['path']).parent/'scientific-original.json')
        if c.scientific_projection(reported,name)!=reported:
            paths[name]='source'
            input_rows[name]=dict(path=name,source=str(raw),size_bytes=original['size_bytes'])
    paths.update((item['path'],'source') for item in job['history'].get('execution_recovery_materials',[]))
    for name,kind in sorted(paths.items()):
        parts=Path(name).parts
        if (parts[:3]==('inputs','history','recoveries') or parts[:2]==('inputs','recovered')
                and ('originals' in parts or parts[-1]=='retirement.json')):
            continue  # Control originals stay retained; no public inventory link.
        target=public/name
        if kind=='compiler_author':
            item=compiled[name]
            if 'code_version' in item['compiler']:
                entries.append(dict(kind=kind,source=item['binding']['compiler'],
                    display=dict(path=target.relative_to(root).as_posix()), material=item['material'],
                    compiler=item['binding']['compiler'], client_binding=item['binding'].get('client_binding'),
                    code_version=item['compiler']['code_version']))
            else:
                author=item['compiler']['author'];source=_check(root,author)
                entries.append(dict(kind=kind,source=_context_reference(root,source,author['size_bytes']),
                    display=dict(path=target.relative_to(root).as_posix(),size_bytes=author['size_bytes']),
                    material=item['material'],compiler=item['binding']['compiler'],client_binding=item['binding'].get('client_binding')))
            continue
        row=input_rows[name]
        bound=dict(row,source=row.get('source',str(work/name)))
        source=originals.material_path(root,bound)
        original=dict(bound,source=str(source))
        if kind=='framework':
            c.write(target,_context_projection(name,c.read(source),references,texts));target.chmod(0o400)
        entries.append(dict(kind=kind,source=original,display=(_ref(root,target) if kind=='framework' else
            dict(path=target.relative_to(root).as_posix(),size_bytes=row['size_bytes']))))
    own=[];masks=[];implementations=[]
    if job.get('worker_view_version')!=1 and permit is not None:
        for number,compiler_path in enumerate(sorted(execution_inputs._directory(root,job['step']).glob('*/compiler.json')),1):
            compiler_ref=_ref(root,compiler_path);compiler=qualification._read(root,compiler_ref)
            output=compiler['output'];source=Path(output['path'])
            if not source.is_relative_to(work/'artifacts'):raise ValueError('Legacy compiler output is not an original task artifact')
            relative=source.relative_to(work/'artifacts')
            material=dict(source=str(source),path=f'inputs/parents/{job["step"]:03d}/artifacts/{relative.as_posix()}',size_bytes=output['size_bytes'])
            client=work/'artifacts/scientific-implementations'/f'I{number:03d}.json'
            binding_ref=execution_inputs.create_legacy_author_client(root,job,compiler_ref,material,permit,attempt,client)
            binding,compiler=execution_inputs.verify_legacy_author_client(root,job,binding_ref,require_intent=False)
            own.append(binding_ref)
            source_name=f'work/retained-implementations/I{number:03d}/implementation.json'
            scientific=public/source_name
            if 'code_version' in compiler:
                entries.append(dict(kind='compiler_author',source=compiler_ref,
                    display=dict(path=scientific.relative_to(root).as_posix()),material=material,
                    compiler=compiler_ref,client_binding=binding_ref,code_version=compiler['code_version']))
            else:
                author=_check(root,compiler['author'])
                entries.append(dict(kind='source',source=_context_reference(root,author,compiler['author']['size_bytes']),
                    display=dict(path=scientific.relative_to(root).as_posix(),size_bytes=compiler['author']['size_bytes'])))
            owner=qualification._read(root,compiler['job']);aliases=[]
            for item in compiler['inputs']:
                original=execution_inputs.resolve_native_original(root,item['original'],compiler_job=owner)
                input_source=originals.material_path(root,original);display=public/item['alias'];aliases.append(item['alias'])
                entries.append(dict(kind='source',source=dict(original,source=str(input_source)),
                    display=dict(path=display.relative_to(root).as_posix(),size_bytes=original['size_bytes'])))
            note=public/'artifact-notes'/f'I{number:03d}.txt'
            note.parent.mkdir(parents=True,exist_ok=True)
            note.write_text('The native execution package is retained privately. Read '+source_name+
                ' and use '+client.relative_to(work).as_posix()+' as the scientific implementation reference.\n')
            note.chmod(0o400)
            masks.append(dict(original=_ref(root,source),display=_ref(root,note),client=str(client)))
            implementations.append(dict(reference=f'I{number:03d}',source=source_name,
                scientific_inputs=aliases,implementation=client.relative_to(work).as_posix(),
                qualification='Unreviewed scientific implementation; no execution permission'))
    if implementations:
        task['retained_implementations']=implementations
        target=public/'science-task.json';target.chmod(0o600);c.write(target,task);target.chmod(0o400)
        entries[0]['display']=_ref(root,target)
    if permit is not None:
        science_facts(root, job['step'], context_entries=entries, context_directory=public)
    manifest=record/'author-science-context.json'
    c.write(manifest,dict(version=1,step=job['step'],directory=public.relative_to(root).as_posix(),
        candidate_references=references,entries=entries,own_compiled_clients=own,artifact_masks=masks,
        retained_implementations=implementations,
        omissions=[dict(category='Legacy control receipts and private recovery bindings',
            scope='Original retained task',reason='Retained and verified privately; available scientific inputs are listed by the task.')]))
    manifest.chmod(0o400)
    return _ref(root,manifest)


def validate_science_context(root, job, proof):
    root=Path(root);manifest=c.read(_check(root,proof));directory=root/manifest['directory']
    if manifest.get('version')!=1 or manifest.get('step')!=job['step'] or directory!=_check(root,proof).parent/'author-science-context':
        raise ValueError('Author scientific context has no exact retained task')
    c._validate_inputs(root,c.workspace(root,job['step']),job)
    references,texts=_context_sources(root,job)
    if references!=manifest['candidate_references']:raise ValueError('Author scientific references changed')
    from . import candidate_refs
    from . import execution_inputs
    for binding in manifest.get('own_compiled_clients',[]):
        execution_inputs.verify_legacy_author_client(root,job,binding)
    for item in manifest.get('artifact_masks',[]):
        _check(root,item['original']);_check(root,item['display'])
    for item in manifest['entries']:
        if 'code_version' in item:
            display=Path(root)/item['display']['path']
            if (set(item['display']) != {'path'} or Path(item['display']['path']).is_absolute()
                    or '..' in Path(item['display']['path']).parts or not display.is_relative_to(directory)):
                raise ValueError('Author scientific display escaped its context')
            _context_code(root, job, item)
            continue
        source=_context_source(root,item['source']);display=_declared_ref(root,item['display'])
        if not display.is_relative_to(directory):raise ValueError('Author scientific display escaped its context')
        if item['kind']=='compiler_author':
            from . import execution_inputs
            if item['material'] not in job['direct_materials']:raise ValueError('Author compiler source is not a frozen input')
            compiler=execution_inputs.verify_compilation(root,item['compiler'],material=item['material'],
                client_binding=item['client_binding'])
            if (_check(root,compiler['author'])!=source
                    or 'source' not in item['source'] and not source.samefile(_check(root,item['display']))
                    or item['source']['size_bytes']!=item['display']['size_bytes']):
                raise ValueError('Author compiler scientific display changed')
        elif item['kind']=='source':
            if (item['source']['size_bytes']!=item['display']['size_bytes']
                    or 'source' not in item['source'] and not source.samefile(_check(root,item['display']))):
                raise ValueError('Author scientific original changed')
        else:
            display=_check(root,item['display'])
            name=display.relative_to(directory).as_posix()
            expected=(candidate_refs.public_fields(c.read(source),references) if item['kind']=='task' else
                _context_projection(name,c.read(source),references,texts))
            if item['kind']=='task' and manifest.get('retained_implementations'):
                expected=dict(expected,retained_implementations=manifest['retained_implementations'])
            if c.read(display)!=expected:raise ValueError('Author scientific display differs from its exact original')
    return directory


def _original_reference(path):
    path=c.regular(Path(path))
    if path.resolve()!=path:raise ValueError('Author closeout requires exact original files')
    return c.record_ref(path), path.stat().st_size


def _control(root, reference):
    return dict(path=str(Path(root)/reference['path']))


def _ref(root, path, *, source=None):
    if source is not None:
        from . import originals
        proof=originals.original_ref(source,Path(path).relative_to(root).as_posix())
        return {key:proof[key] for key in ('path','size_bytes')}
    path=Path(path);digest,size=_original_reference(path)
    return dict(path=path.relative_to(root).as_posix(),size_bytes=size)


def _check(root, ref, *, source=None):
    path=root/ref['path']
    if Path(ref['path']).is_absolute() or '..' in Path(ref['path']).parts or _ref(root,path,source=source)!=ref:
        raise ValueError('Author closeout original changed')
    if source is not None:
        from . import originals
        return originals.resolve_ref(source,ref)
    return path


def _read_original(root, path, source):
    return c.read(_check(root,_ref(root,path,source=source),source=source))


def _optional_original(root, path, source):
    if source is None or not source.inherited:
        return path.exists()
    from . import originals
    retained=originals.legacy_compilation(source)
    field=('author_successor' if path==successor_path(root,source.step) else 'output_upgrade')
    proof=retained[field]
    if proof is None:
        return False
    if proof.get('origin_identity',source.origin_identity)!=source.origin_identity:
        raise ValueError('Author closeout optional original changed its origin')
    proof={key:proof[key] for key in ('path','size_bytes')}
    _declared_ref(root,proof,path)
    _check(root,proof,source=source)
    return True


def _calls(root, step):
    from .experiment_client import record_directory
    directory=record_directory(root,step)/'calls'
    if not directory.is_dir() or directory.resolve()!=directory:
        raise ValueError('Author closeout requires retained managed experiments')
    return [_ref(root,p) for p in sorted(directory.rglob('*')) if p.is_file()]


def _declared_ref(root, ref, expected=None):
    """Check a declared object address/hash without opening its result bytes."""
    if (not isinstance(ref,dict) or set(ref) != {'path','size_bytes'}
            or not isinstance(ref['path'],str) or Path(ref['path']).is_absolute() or '..' in Path(ref['path']).parts
            or type(ref['size_bytes']) is not int or ref['size_bytes'] < 0
):
        raise ValueError('Author closeout declared original identity changed')
    path = Path(root)/ref['path']
    if expected is not None and path != expected:
        raise ValueError('Author closeout declared original scope changed')
    return path


def _validate_original(root, value, *, source=None):
    """Validate permission objects and scope independently of execution results."""
    root=Path(root)
    if value.get('version')!=1 or value.get('context')!='new_context' or value.get('scope')!='author_closeout_only':
        raise ValueError('Explicit finite author closeout authorization required')
    step=value['step'];origin=root/'native-evidence'/f'{step:03d}'
    job=_read_original(root,root/'jobs'/f'{step:03d}.json',source)
    if source is None:
        settings_path=configuration_updates.settings_path(root,job)
    else:
        from . import originals
        originals.source_settings(source)
        settings_path=root/(job.get('configuration_revision') or {}).get('path','settings.json')
    expected=dict(job=root/'jobs'/f'{step:03d}.json',settings=settings_path,
        original_intent=origin/'native-intent.json',original_outcome=origin/'native-outcome.json',
        original_usage=origin/'usage.json',original_experiment_config=origin/'experiment-config.json')
    for key,path in expected.items():
        _declared_ref(root,value[key],path)
        if key not in {'original_outcome','original_usage'}:_check(root,value[key],source=source)
    job=c.read(_check(root,value['job'],source=source))
    if type(step) is not int or job['step']!=step or job['role']!='W' or value['source_attempt']!=0:
        raise ValueError('Author closeout must retain the original stopped W')
    for ref in value['calls']:_declared_ref(root,ref)
    return value


def _require_original_facts(root, value):
    """Verify the exact stopped original and retained call tree, not a grant."""
    root=Path(root);_validate_original(root,value)
    for key in ('original_outcome','original_usage'):_check(root,value[key])
    old=c.read(root/value['original_outcome']['path'])
    if old.get('stop_reason')!='token_limit' or old.get('cleanup')!='observed_group_cleaned' or type(old.get('returncode')) is not int:
        raise ValueError('Author closeout original is not a cleaned token stop')
    if _calls(root,value['step'])!=value['calls']:
        raise ValueError('Author closeout cannot change or add managed calls')
    return value


def require_original_eligibility(root, value):
    """Require known retained science before creating a new author context."""
    value=validate(root,value)
    _require_original_facts(root,value)
    job=c.read(Path(root)/value['job']['path'])
    c._validate_inputs(Path(root),c.workspace(root,value['step']),job)
    terminal_facts(root,value['step'])
    return value


def validate(root, value, *, source=None):
    """Validate old bytes or their exact explicitly authorized retained upgrade."""
    root=Path(root)
    successor=successor_path(root,value['step'])
    if _optional_original(root,successor,source):
        # The successor verifier includes the complete original validation.
        validate_successor(root,_read_original(root,successor,source),original=value,source=source)
    else:
        _validate_original(root,value,source=source)
    return value


def _validate_verified_intent(root, job, intent, verified):
    """Bind one intent inside a caller's freshly verified authorization chain.

    Only records() and the full public validator construct these local tuples;
    they are neither persisted verdicts nor a public option to skip validation.
    """
    proof,value,original=verified
    if (intent.get('continuation_kind')!='author_closeout' or intent.get('context_mode')!='new_context'
            or intent.get('author_closeout')!=proof or c.read(_check(root,proof))!=value):
        raise ValueError('Author closeout accepted intent required')
    attempt=intent.get('attempt')
    if attempt==1:
        if value!=original:raise ValueError('Author closeout original permit changed')
    elif attempt==2:
        if (_check(root,proof)!=successor_path(root,job['step'])
                or c.read(_check(root,value['original_permit']))!=original):
            raise ValueError('Author successor changed its exact authorization')
        facts=intent.get('author_closeout_facts')
        expected=root/'native-evidence'/f'{job["step"]:03d}'/'continuations/002/author-closeout-facts.json'
        if not isinstance(facts,dict) or _declared_ref(root,facts)!=expected:
            raise ValueError('Author successor compact facts changed')
    else:raise ValueError('Author closeout accepts only the exact first or explicit second author turn')
    if intent.get('worker_view_version') == 1:
        expected=(c.workspace(root,job['step'])/'work/author-closeout-science.json' if attempt==1 else
            root/'native-evidence'/f'{job["step"]:03d}'/'continuations/002/author-closeout-science.json')
        facts=intent.get('author_closeout_science_facts')
        if not isinstance(facts,dict) or _declared_ref(root,facts)!=expected:
            raise ValueError('Author closeout scientific facts changed')
    if intent.get('author_context_version')==1:
        context=intent.get('author_closeout_science_context')
        if not isinstance(context,dict):raise ValueError('Author scientific context is missing')
        _declared_ref(root,context)
    if value['step']!=job['step'] or intent.get('task_ref')!=_control(root,original['job']):
        raise ValueError('Author closeout accepted a different scientific task')
    return value


def validate_intent(root, job, intent):
    """Read the exact author permission without execution-result qualification."""
    root=Path(root);proof=intent.get('author_closeout')
    if proof is None or intent.get('continuation_kind')!='author_closeout' or intent.get('context_mode')!='new_context':
        raise ValueError('Author closeout accepted intent required')
    value=c.read(_check(root,proof));attempt=intent.get('attempt')
    if attempt==1:
        value=validate(root,value);original=value
    elif attempt==2:
        value=validate_successor(root,value)
        original=c.read(_check(root,value['original_permit']))
    else:raise ValueError('Author closeout accepts only the exact first or explicit second author turn')
    return _validate_verified_intent(root,job,intent,(proof,value,original))


def require_intent_eligibility(root, job, intent, *, verified=None):
    """Verify author inputs/results; records may supply its freshly checked chain."""
    root=Path(root)
    if verified is None:
        value=validate_intent(root,job,intent)
        original=value if intent['attempt']==1 else c.read(_check(root,value['original_permit']))
    else:
        _,value,original=verified
        _validate_verified_intent(root,job,intent,verified)
    if verified is None:
        if intent['attempt']==1:require_original_eligibility(root,original)
        else:require_successor_eligibility(root,value)
    attempt=intent['attempt']
    if attempt==2:_check(root,intent['author_closeout_facts'])
    if intent.get('worker_view_version')==1:_check(root,intent['author_closeout_science_facts'])
    if intent.get('author_context_version')==1:
        validate_science_context(root,job,intent['author_closeout_science_context'])
    original_config=_check(root,original['original_experiment_config'])
    projection=c.regular(root/'native-evidence'/f'{job["step"]:03d}'/'continuations'/f'{attempt:03d}'/'experiment-config.json')
    if projection.read_bytes()!=original_config.read_bytes():
        raise ValueError('Author closeout retained execution projection changed')
    return value


def terminal_facts(root, step):
    """Verify retained transport facts without loading or querying an adapter."""
    from . import experiments as e
    root=Path(root);work=c.workspace(root,step);rows=[]
    config=c.read(root/'native-evidence'/f'{step:03d}'/'experiment-config.json')
    from .experiment_client import record_directory
    for directory in sorted((record_directory(root,step)/'calls').iterdir()):
        accepted=c.read(c.regular(directory/'accepted.json'))
        observation=c.read(c.regular(directory/'execution-observation.json'))
        completed=c.read(c.regular(directory/'completed.json'))
        if (accepted.get('interface')!=config['execution_interface'] or accepted.get('scope')!=config['scope']
                or accepted.get('call_id')!=directory.name
                or observation.get('accepted')!=e.proof(directory/'accepted.json')
                or observation.get('completed')!=e.proof(directory/'completed.json')
                or observation.get('submission')!=e.proof(directory/'submission.json')):
            raise ValueError('Retained call belongs to another frozen execution interface')
        status=e.verify_observation(observation,lambda proof:Path(proof['path']),interface=accepted['interface'])
        if status not in {'known_success','known_failed'}:
            raise ValueError('Author closeout cannot recover unknown experiments')
        rows.append(dict(call_id=directory.name,status=status,candidate_id=completed.get('candidate_id'),
            mechanism=accepted.get('mechanism_binding'),
            originals=directory.relative_to(work).as_posix() if directory.is_relative_to(work) else str(directory),
            qualification='Retained execution observations, not public audit qualification'))
    if not rows:raise ValueError('Author closeout has no completed experiments')
    return dict(version=1,scope='Preserved terminal experiment facts only; no backend query or new experiment',calls=rows)


def science_facts(root, step, *, context_entries=None, context_directory=None):
    """Scientific projection of freshly verified originals, never their directory index."""
    from . import experiments as e, originals
    facts=terminal_facts(root,step);work=c.workspace(Path(root),step);calls=[]
    config=c.read(Path(root)/'native-evidence'/f'{step:03d}'/'experiment-config.json')
    def expose(reference, logical):
        actual=originals.resolve_root_ref(root,reference)
        if context_entries is not None:
            context_entries.append(dict(kind='source',source=dict(reference,source=str(actual)),
                display=dict(path=(Path(context_directory)/logical).relative_to(root).as_posix(),
                    size_bytes=reference['size_bytes'])))
        return work/logical
    for row in facts['calls']:
        directory=work/row['originals'];completed=c.read(directory/'completed.json')
        observation=c.read(directory/'execution-observation.json')
        accepted=c.read(directory/'accepted.json')
        mechanism=row['mechanism'] or {}
        result=completed.get('result') or {}
        source=None
        original=observation.get('scientific_source')
        if 'code_version' in accepted:
            version=accepted['code_version']
            if (observation.get('source_code_version') != version
                    or e._code_version_binding(config,accepted) != Path(root)):
                raise ValueError('Author scientific source lost its original code binding')
            logical=Path('work/retained-experiments')/row['call_id']/'code'
            if version['selection']=='file':logical=logical/version['entry']
            source=work/logical/(version['entry'] if version['selection']=='directory' else '')
            if context_entries is not None:
                context_entries.append(dict(kind='source',source=_ref(root,directory/'accepted.json'),
                    display=dict(path=(Path(context_directory)/logical).relative_to(root).as_posix()),
                    code_version=version))
        elif original is None and 'scientific_source' not in observation and config['execution_interface'].get('result_protocol')==e.LOCAL_RESULT_PROTOCOL:
            # Existing local source bytes already have a generic accepted binding.
            original=accepted['script']
        if original is not None and 'code_version' not in accepted:
            reference=original.get('source',original)
            if not isinstance(reference,dict):reference=original
            source=expose(reference,Path('work/retained-experiments')/row['call_id']/Path(reference['path']).name)
        value=dict(call_id=row['call_id'],candidate_id=row['candidate_id'],status=row['status'],
            mechanism={key:mechanism[key] for key in ('mechanism_ref',) if key in mechanism},
            source=source.relative_to(work).as_posix() if source is not None else None,
            source_status='retained_scientific_original' if source is not None else 'scientific_source_unavailable',
            result=e.scientific_result(config,accepted,completed,observation),
            outputs=[],qualification=row['qualification'])
        scientific=e.scientific_outputs(config,observation)
        for output in scientific:
            reference=next((item.get('source',item) for item in observation.get('scientific_outputs',[])
                if item.get('name')==output.name),None)
            if not isinstance(reference,dict):reference=dict(path=output.local_path,size_bytes=output.size_bytes)
            path=expose(reference,Path('work/retained-experiments')/row['call_id']/'outputs'/output.name)
            value['outputs'].append(dict(name=output.name,path=path.relative_to(work).as_posix()))
        if config['execution_interface'].get('result_protocol')==e.LOCAL_RESULT_PROTOCOL:
            names={item.name for item in scientific};logs=[]
            for output in e.scientific_outputs(config,observation,include_logs=True):
                if output.name in names:continue
                reference=next((item.get('source',item) for item in observation.get('scientific_outputs',[])
                    if item.get('name')==output.name),None)
                if not isinstance(reference,dict):reference=dict(path=output.local_path,size_bytes=output.size_bytes)
                path=expose(reference,Path('work/retained-experiments')/row['call_id']/'outputs'/output.name)
                logs.append(dict(stream=Path(output.name).stem,path=path.relative_to(work).as_posix()))
            if logs:
                value['logs_ref']=dict(note='Read stdout/stderr only as scientifically needed; known execution snapshot paths use scientific source names.',
                    streams=logs)
        calls.append(value)
    return dict(version=1,view='scientific facts from verified originals',scope=facts['scope'],calls=calls,
        qualifications=['Terminal execution is not scientific validity or public-audit qualification. No new experiment or backend query is authorized.'],
        omitted_details=[dict(category='Control receipts and machine identity bindings',scope='Retained calls',
            reason='Verified and retained privately; unnecessary for scientific interpretation.'),
            dict(category='Uncollected original outputs',scope='Only verified retained result assets are provided',
            reason='Not provided by this view; complete original-byte reproduction may require additional retained material.')])


def start(root, step, *, max_tokens, max_seconds, authorization_note):
    from . import budget, execution, native, profiles, runtime
    root=Path(root);settings=configuration_updates.for_step(root, step);job=c.read(c.regular(root/'jobs'/f'{step:03d}.json'))
    origin=execution.origin(root,step)
    if (type(max_tokens) is not int or max_tokens<=0 or type(max_seconds) not in (int,float)
            or not 0<max_seconds<float('inf') or not authorization_note.strip()):
        raise ValueError('Finite explicit author closeout budget and authorization required')
    if (origin/'result-identity.json').exists() or (origin/'continuations').exists():
        raise ValueError('Author closeout is single-use; preserve its original attempt')
    if any(p.name>f'{step:03d}.json' for p in (root/'jobs').glob('*.json')):
        raise ValueError('Author closeout cannot replace a scientific successor')
    if (root/'pause.request').exists() or budget.closeout_state(root)['begin'] is not None or budget.execution_blocked(root):
        raise ValueError('Author closeout requires stopped original execution and no closeout or pause')
    old=c.read(c.regular(origin/'native-outcome.json'))
    if old.get('stop_reason')!='token_limit' or old.get('cleanup')!='observed_group_cleaned':
        raise ValueError('Only an original cleaned token-limit W is eligible')
    binding=c.record_ref(root/'jobs'/f'{step:03d}.json')
    for name in ('runner-process.json','native-process.json'):
        process=c.read(c.regular(origin/name))
        if process.get('task_ref')!=binding or process.get('boot_id')!=budget.clock_stamp()['boot_id'] or runtime.process_matches(process,binding):
            raise ValueError('Original author writer is live or its identity is unavailable')
    meter=native.usage(root);limits=budget.snapshot(root)
    reserve=budget.delivery_reserve(settings) or dict(tokens=0,seconds=0,steps=0)
    if (limits['max_tokens']-meter.get('accounted_tokens',meter['observed_tokens'])<=reserve['tokens']
            or limits['seconds_remaining']<max_seconds+reserve['seconds']
            or budget.effective_max_steps(root)-budget.used_steps(root)<1+reserve['steps']):
        raise ValueError('Finite author closeout and delivery reserve exceed remaining authorization')
    facts=terminal_facts(root,step)
    permit=dict(version=1,scope='author_closeout_only',context='new_context',step=step,source_attempt=0,
        max_tokens=max_tokens,max_seconds=max_seconds,authorization_note=authorization_note.strip(),
        job=_ref(root,root/'jobs'/f'{step:03d}.json'),settings=_ref(root,configuration_updates.settings_path(root, job)),
        original_intent=_ref(root,origin/'native-intent.json'),original_outcome=_ref(root,origin/'native-outcome.json'),
        original_usage=_ref(root,origin/'usage.json'),
        original_experiment_config=_ref(root,origin/'experiment-config.json'),calls=_calls(root,step))
    require_original_eligibility(root,permit)
    path=root/'host-recovery/author-closeout'/f'{step:03d}.json'
    if path.exists():raise ValueError('Original author closeout authorization already exists; no automatic replay')
    c.write(path,permit);path.chmod(0o400)
    facts_path=origin/'author-closeout-original-facts.json'
    if facts_path.exists():raise ValueError('Original terminal facts already exist; inspect without replay')
    c.write(facts_path,facts);facts_path.chmod(0o400)
    science_path=c.workspace(root,step)/'work/author-closeout-science.json'
    c.write(science_path,science_facts(root,step));science_path.chmod(0o400)
    tokens,seconds=budget._attempt_consumption(root,job,[origin])
    budget.transition(root,'advance','explicit_author_closeout')
    limits=budget.snapshot(root)
    anchor=dict(task_ref=binding,role='W',boot_id=budget.clock_stamp()['boot_id'],advance_seconds=limits['advance_seconds'],
        limits=budget.node_limits(root,job),shared_limits={key:limits[key] for key in ('max_tokens','max_seconds')},
        previous_tokens=tokens,previous_seconds=seconds)
    context_record=root/'host-recovery/author-context'/f'{step:03d}/001'
    context_record.mkdir(parents=True)
    context=science_context(root,job,context_record,permit=_ref(root,path),attempt=1)
    record=origin/'continuations/001';record.mkdir(parents=True)
    projection=record/'experiment-config.json'
    projection.write_bytes(_check(root,permit['original_experiment_config']).read_bytes())
    projection.chmod(0o400)
    intent=dict(step=step,attempt=1,continuation_kind='author_closeout',context_mode='new_context',
        task_ref=binding,root_intent_ref=c.record_ref(origin/'native-intent.json'),previous=execution.proof(origin),
        budget_slice=anchor,author_closeout=_ref(root,path),
        worker_view_version=1,author_closeout_science_facts=_ref(root,science_path),
        author_context_version=1,author_closeout_science_context=context,
        native_home=str(root/'native-homes'/f'{step:03d}'/'context-001'),**profiles.binding(settings,job))
    c.write(record/'native-intent.json',intent)
    runtime.start(root,step,1)
    result=native.wait_native(root,step)
    validate(root,permit)
    return result


def successor_path(root, step):
    return Path(root)/'host-recovery/author-closeout'/f'{step:03d}-successor.json'


def validate_successor(root, value, *, original=None, source=None):
    """Pure exact-original checks, never usage/records/snapshot or a new grant."""
    root=Path(root)
    if (value.get('version')!=1 or value.get('scope')!='author_closeout_successor_only'
            or value.get('source_attempt')!=1 or value.get('attempt')!=2
            or value.get('context')!='new_context' or type(value.get('step')) is not int
            or type(value.get('max_tokens')) is not int or value['max_tokens']<=0
            or type(value.get('max_seconds')) not in (int,float) or not 0<value['max_seconds']<float('inf')
            or any(not isinstance(value.get(key),str) or not value[key].strip()
                   for key in ('authorization_note','authorization_statement'))):
        raise ValueError('One explicit finite author successor authorization required')
    step=value['step'];permit=root/'host-recovery/author-closeout'/f'{step:03d}.json'
    if _declared_ref(root,value['original_permit'])!=permit:
        raise ValueError('Author successor changed its original permit')
    old=c.read(_check(root,value['original_permit'],source=source))
    if old.get('step')!=step:raise ValueError('Author successor original step changed')
    if original is not None and old!=original:raise ValueError('Author successor original permit changed')
    _validate_original(root,old,source=source)
    output_upgrade=_output_record(root,step,'upgrade')
    if _optional_original(root,output_upgrade,source):
        _validate_output_upgrade(root,_read_original(root,output_upgrade,source),successor=value,source=source)
    prior=root/'native-evidence'/f'{step:03d}'/'continuations/001'
    if set(value.get('source_originals',{}))!={'native-intent.json','native-outcome.json','usage.json','usage-observation.json','native.jsonl','runner-process.json','native-process.json'}:
        raise ValueError('Author successor needs the exact stopped source originals')
    for name,ref in value['source_originals'].items():
        if _declared_ref(root,ref)!=prior/name:raise ValueError('Author successor source original changed')
    intent=c.read(_check(root,value['source_originals']['native-intent.json'],source=source))
    if (intent.get('attempt')!=1 or intent.get('continuation_kind')!='author_closeout'
            or intent.get('context_mode')!='new_context'
            or intent.get('root_intent_ref')!=_control(root,old['original_intent'])
            or intent.get('author_closeout')!=value['original_permit']
            or intent.get('task_ref')!=_control(root,old['job'])):
        raise ValueError('Author successor source intent identity changed')
    return value


def require_successor_eligibility(root, value):
    """Verify original results and the cleaned first author before a new turn."""
    root=Path(root);value=validate_successor(root,value)
    old=c.read(_check(root,value['original_permit']))
    _require_original_facts(root,old)
    prior=root/'native-evidence'/f'{value["step"]:03d}'/'continuations/001'
    for name,ref in value['source_originals'].items():
        if _check(root,ref)!=prior/name:raise ValueError('Author successor source original changed')
    intent=c.read(prior/'native-intent.json');outcome=c.read(prior/'native-outcome.json')
    if (outcome.get('stop_reason')!='author_closeout_wall_limit'
            or outcome.get('cleanup')!='observed_group_cleaned' or type(outcome.get('returncode')) is not int):
        raise ValueError('Author successor requires the exact cleaned first author finite time stop')
    for name in ('runner-process.json','native-process.json'):
        process=c.read(prior/name)
        if (_control(root,value['source_originals'][name])!=outcome.get('execution_processes',{}).get(name)
                or process.get('task_ref')!=_control(root,value['source_originals']['native-intent.json'])
                or process.get('boot_id') is None or process.get('boot_id')!=intent.get('budget_slice',{}).get('boot_id')):
            raise ValueError('Author successor source process identity changed')
    job=c.read(root/old['job']['path'])
    c._validate_inputs(root,c.workspace(root,value['step']),job)
    terminal_facts(root,value['step'])
    return value


def _successor_facts(root, step):
    facts=terminal_facts(root,step)
    # Exact call identities and original entry points only. Ranking, metrics,
    # mechanism interpretation and efficacy remain the scientific author's work.
    calls=[];work=c.workspace(root,step)
    for row in facts['calls']:
        directory=work/row['originals'];accepted=c.read(directory/'accepted.json');completed=c.read(directory/'completed.json')
        calls.append(dict(call_id=row['call_id'],status=row['status'],candidate_id=row['candidate_id'],
            originals=row['originals'],backend_run_id=c.read(directory/'submission.json').get('external_run_id'),
            source_ref=accepted.get('script'),
            artifacts=[{key:item[key] for key in ('size_bytes',)} for item in completed.get('outputs',[])]))
    return dict(version=1,scope=facts['scope'],call_count=len(calls),omitted_calls=0,calls=calls,
        ordering='Rows are listed by call directory, not scientific chronology. backend_run_id is copied from the retained submission binding; null means unavailable. Infer no last-eight ordering from this list.',
        detailed_facts=(str(Path(root)/'native-evidence'/f'{step:03d}'/'author-closeout-original-facts.json')
            if (Path(root)/'native-evidence'/f'{step:03d}'/'author-closeout-original-facts.json').exists()
            else 'work/author-closeout-facts.json'),
        omitted_details=['Reported metrics, evaluation keys, mechanism bindings and full proofs remain in the exact per-call originals. No scientific ranking or distinct-approach verdict is supplied.'],
        qualifications=['Terminal execution is not public-audit qualification; audit remains unknown. No new experiment or backend query is authorized.'])


def start_successor(root, step, *, max_tokens, max_seconds, authorization_note,
                    authorization_statement):
    """One new operator action, never an automatic retry or a resume-policy edit."""
    from . import budget,execution,native,profiles,runtime
    root=Path(root);origin=execution.origin(root,step)
    if (type(max_tokens) is not int or max_tokens<=0 or type(max_seconds) not in (int,float)
            or not 0<max_seconds<float('inf') or any(not isinstance(text,str) or not text.strip()
                for text in (authorization_note,authorization_statement))):
        raise ValueError('Finite author successor budget and original authorization are required')
    path=successor_path(root,step)
    if path.exists():
        permit=validate_successor(root,c.read(c.regular(path)))
        if (permit['max_tokens']!=max_tokens or permit['max_seconds']!=max_seconds
                or permit['authorization_note']!=authorization_note.strip()
                or permit['authorization_statement']!=authorization_statement.strip()):
            raise ValueError('Original author successor authorization cannot be replaced')
    else:
        if (origin/'result-identity.json').exists() or (origin/'continuations/002').exists():
            raise ValueError('Author successor cannot replace an accepted or sealed author')
        permit=dict(version=1,scope='author_closeout_successor_only',context='new_context',step=step,
            source_attempt=1,attempt=2,max_tokens=max_tokens,max_seconds=max_seconds,
            authorization_note=authorization_note.strip(),authorization_statement=authorization_statement.strip(),
            original_permit=_ref(root,root/'host-recovery/author-closeout'/f'{step:03d}.json'),
            source_originals={name:_ref(root,origin/'continuations/001'/name) for name in
                ('native-intent.json','native-outcome.json','usage.json','usage-observation.json','native.jsonl','runner-process.json','native-process.json')})
        validate_successor(root,permit)
        # Publishing an authorization is not dispatch.
        c.write(path,permit);path.chmod(0o400)
    paths=execution.records(root,step)
    if len(paths)==3:return native.wait_native(root,step)  # Collect exact accepted 2, never create 3.
    if len(paths)!=2:raise ValueError('Author successor requires exactly the original and first author attempts')
    job=c.read(c.regular(root/'jobs'/f'{step:03d}.json'));settings=configuration_updates.for_step(root, step)
    if ((origin/'result-identity.json').exists() or (root/'pause.request').exists()
            or budget.closeout_state(root)['begin'] is not None
            or any(p.name>f'{step:03d}.json' for p in (root/'jobs').glob('*.json'))):
        raise ValueError('Author successor cannot replace a seal, pause, closeout or scientific successor')
    binding=c.record_ref(root/'jobs'/f'{step:03d}.json');prior=paths[-1]
    previous_binding=c.record_ref(prior/'native-intent.json')
    for name in ('runner-process.json','native-process.json'):
        process=c.read(c.regular(prior/name))
        if (process.get('task_ref')!=previous_binding or process.get('boot_id')!=budget.clock_stamp()['boot_id']
                or runtime.process_matches(process,previous_binding)):
            raise ValueError('Original author writer is live or its identity is unavailable')
    if budget.execution_blocked(root):raise ValueError('Author successor requires stopped original execution')
    meter=native.usage(root);limits=budget.snapshot(root)
    reserve=budget.delivery_reserve(settings) or dict(tokens=0,seconds=0,steps=0)
    if (limits['max_tokens']-meter.get('accounted_tokens',meter['observed_tokens'])<=reserve['tokens']
            or limits['seconds_remaining']<max_seconds+reserve['seconds']
            or budget.effective_max_steps(root)-budget.used_steps(root)<1+reserve['steps']):
        raise ValueError('Finite author successor and delivery reserve exceed remaining authorization')
    require_successor_eligibility(root,permit)
    facts=_successor_facts(root,step)
    tokens,seconds=budget._attempt_consumption(root,job,paths)
    budget.transition(root,'advance','explicit_author_closeout_successor')
    limits=budget.snapshot(root)
    anchor=dict(task_ref=binding,role='W',boot_id=budget.clock_stamp()['boot_id'],advance_seconds=limits['advance_seconds'],
        limits=budget.node_limits(root,job),shared_limits={key:limits[key] for key in ('max_tokens','max_seconds')},
        previous_tokens=tokens,previous_seconds=seconds)
    context_record=root/'host-recovery/author-context'/f'{step:03d}/002'
    context_record.mkdir(parents=True)
    context=science_context(root,job,context_record,permit=_ref(root,path),attempt=2)
    record=origin/'continuations/002';record.mkdir()
    projection=record/'experiment-config.json'
    projection.write_bytes(_check(root,c.read(root/permit['original_permit']['path'])['original_experiment_config']).read_bytes())
    projection.chmod(0o400)
    c.write(record/'author-closeout-facts.json',facts);(record/'author-closeout-facts.json').chmod(0o400)
    science_path=record/'author-closeout-science.json'
    c.write(science_path,science_facts(root,step));science_path.chmod(0o400)
    intent=dict(step=step,attempt=2,continuation_kind='author_closeout',context_mode='new_context',
        task_ref=binding,root_intent_ref=c.record_ref(origin/'native-intent.json'),previous=execution.proof(prior),
        budget_slice=anchor,author_closeout=_ref(root,path),author_closeout_facts=_ref(root,record/'author-closeout-facts.json'),
        worker_view_version=1,author_closeout_science_facts=_ref(root,science_path),
        author_context_version=1,author_closeout_science_context=context,
        native_home=str(root/'native-homes'/f'{step:03d}'/'context-002'),**profiles.binding(settings,job))
    c.write(record/'native-intent.json',intent)
    runtime.start(root,step,2)
    result=native.wait_native(root,step)
    validate_successor(root,permit)
    return result


def _output_record(root, step, kind):
    return Path(root)/'host-recovery/author-closeout'/f'{step:03d}-output-{kind}.json'


def _validate_output_upgrade(root, value, *, successor=None, source=None):
    """Bind output authorization to its exact successor without executing work."""
    root=Path(root)
    if (value.get('version')!=1 or value.get('scope')!='completed_author_output_code_upgrade'
            or type(value.get('step')) is not int or value['step']<=0 or value.get('attempt')!=2
            or any(not isinstance(value.get(key),str) or not value[key].strip()
                   for key in ('authorization_note','authorization_statement'))):
        raise ValueError('Explicit completed-output implementation authorization required')
    path=successor_path(root,value['step'])
    if _declared_ref(root,value['successor'])!=path:raise ValueError('Output upgrade changed its author successor')
    original=c.read(_check(root,value['successor'],source=source))
    if successor is not None and original!=successor:raise ValueError('Output upgrade successor changed')
    return value


def _completed_output_facts(root, step, record, session):
    """Pure complete-return checks; no records(), usage(), clock or budget sampling."""
    import json
    from . import native
    root=Path(root);record=Path(record);work=c.workspace(root,step)
    if record!=root/'native-evidence'/f'{step:03d}'/'continuations/002':
        raise ValueError('Completed-output acceptance is limited to author attempt 2')
    intent=c.read(c.regular(record/'native-intent.json'));outcome=c.read(c.regular(record/'native-outcome.json'))
    usage=c.read(c.regular(record/'usage.json')) if (record/'usage.json').exists() else None
    observation=c.read(c.regular(record/'usage-observation.json'))
    if (intent.get('attempt')!=2 or intent.get('continuation_kind')!='author_closeout' or intent.get('context_mode')!='new_context'
            or intent.get('task_ref')!=c.record_ref(c.regular(root/'jobs'/f'{step:03d}.json'))
            or intent.get('author_closeout')!=_ref(root,successor_path(root,step))
            or type(outcome.get('returncode')) is not int or outcome['returncode']!=0
            or outcome.get('stop_reason')!='author_closeout_token_limit' or outcome.get('cleanup')!='observed_group_cleaned'
            or outcome.get('raw_ref') is not None or outcome.get('output_status') is not None):
        raise ValueError('Only an exact completely returned cleaned author token stop is eligible')
    if (usage is not None and (native._usage_event(dict(type='turn.completed',usage=usage))!=usage
            or observation.get('tokens')!=usage) or observation!=outcome.get('metering')
            or observation.get('source')!='turn.completed'):
        raise ValueError('Completed output usage observation changed its original completion')
    binding=c.record_ref(record/'native-intent.json')
    for name in ('runner-process.json','native-process.json'):
        path=c.regular(record/name);process=c.read(path)
        if (c.record_ref(path)!=outcome.get('execution_processes',{}).get(name) or process.get('task_ref')!=binding
                or process.get('boot_id')!=intent.get('budget_slice',{}).get('boot_id')
                or process.get('boot_id') is None
                or any(type(process.get(key)) is not int or process[key]<=0 for key in ('pid','start_ticks','pgid','sid','ppid'))):
            raise ValueError('Completed author process identity changed')
    pending=c.regular(record/'result.pending.json');data=pending.read_bytes()
    events=[]
    with c.regular(record/'native.jsonl').open() as stream:
        for line in stream:events.append(json.loads(line))
    completed=[event for event in events if event.get('type')=='turn.completed']
    completed_indexes=[number for number,event in enumerate(events) if event.get('type')=='turn.completed']
    message_indexes=[number for number,event in enumerate(events)
        if event.get('type')=='item.completed' and event.get('item',{}).get('type')=='agent_message']
    messages=[events[number]['item'].get('text') for number in message_indexes]
    if (len(completed)!=1 or not message_indexes or message_indexes[-1]>=completed_indexes[0]
            or any(event.get('type','').startswith(('turn.','item.','thread.')) for event in events[completed_indexes[0]+1:])
            or sum(event.get('type')=='turn.started' for event in events)!=1
            or any(event.get('type') in {'turn.failed','error'} for event in events)
            or not messages or not isinstance(messages[-1],str) or messages[-1].encode()!=data
            or [event.get('thread_id') for event in events if event.get('type')=='thread.started']!=[outcome.get('thread_id')]):
        raise ValueError('Pending output differs from the unique completed native return')
    terminal_usage=native._usage_event(completed[0])
    if (terminal_usage is not None and (usage is None or any(usage[key]<terminal_usage[key] for key in terminal_usage))
            or observation.get('coverage')=='terminal' and terminal_usage!=usage):
        raise ValueError('Completed output changed its reported usage lower bound')
    session=Path(session)
    home=Path(intent.get('native_home',''))
    if (home!=root/'native-homes'/f'{step:03d}'/'context-002' or session.resolve()!=session
            or not session.is_relative_to(home/'sessions')):
        raise ValueError('Completed output changed its exact native session')
    with c.regular(session).open('rb') as stream:
        header=json.loads(stream.readline())
    if (header.get('type')!='session_meta' or header.get('payload',{}).get('id')!=outcome.get('thread_id')
            or observation.get('thread_id')!=outcome.get('thread_id')
            or header.get('payload',{}).get('cwd')!=str(work)):
        raise ValueError('Completed session or its terminal counter changed')
    from . import budget
    job=c.read(root/'jobs'/f'{step:03d}.json')
    origin=root/'native-evidence'/f'{step:03d}'
    budget._attempt_consumption(root,job,[origin,origin/'continuations/001',record])
    report=outcome['budget_slice']
    if budget.node_mode(root)=='hard' and (report['observed_tokens']>report['limits']['max_observed_tokens']
            or report['advance_seconds']>report['limits']['max_seconds']):
        raise ValueError('Completed output exceeded its original hard node allowance')
    permit=c.read(_check(root,intent['author_closeout']))
    elapsed=report['advance_seconds']-intent['budget_slice']['previous_seconds']
    if elapsed<0 or elapsed>=permit['max_seconds']:
        raise ValueError('Completed output also exceeded its original author wall allowance')
    tokens=usage['input_tokens']+usage['output_tokens'] if usage is not None else None
    complete=observation.get('coverage')=='terminal' and usage is not None
    return dict(observed_tokens=tokens,local_limit=permit['max_tokens'],
        local_excess=max(0,tokens-permit['max_tokens']) if complete else None,
        usage_coverage=observation.get('coverage','unknown'),
        local_elapsed_seconds=elapsed,local_wall_seconds=permit['max_seconds'],
        original_stop_reason=outcome['stop_reason'],scope='Completed returned bytes only; scientific qualification remains pending')


def _admission_usage_refs(root, step):
    # This is a retained meter inventory, never permission for another attempt.
    paths=[]
    for number in range(1,step+1):
        origin=Path(root)/'native-evidence'/f'{number:03d}'
        if (origin/'native-intent.json').exists() and (origin/'usage.json').exists():paths.append(origin/'usage.json')
        directory=origin/'continuations'
        if directory.exists():
            paths.extend(path/'usage.json' for path in sorted(directory.iterdir())
                if (path/'native-intent.json').exists() and (path/'usage.json').exists())
    return [_ref(Path(root),path) for path in paths]


def _validate_output_acceptance(root, value, *, source=None):
    """Read the exact acceptance declaration without rechecking returned results."""
    root=Path(root);step=value.get('step')
    if (value.get('version')!=1 or value.get('scope')!='explicit_completed_author_output'
            or type(step) is not int or step<=0 or value.get('attempt')!=2):
        raise ValueError('Explicit exact completed-output acceptance required')
    upgrade_path=_output_record(root,step,'upgrade')
    _declared_ref(root,value['upgrade'],upgrade_path)
    upgrade=_check(root,value['upgrade'],source=source)
    _validate_output_upgrade(root,c.read(upgrade),source=source)
    record=root/'native-evidence'/f'{step:03d}'/'continuations/002'
    names={'native-intent.json','native-outcome.json','usage.json','usage-observation.json','native.jsonl',
        'runner-process.json','native-process.json','result.pending.json','experiment-config.json','author-closeout-facts.json'}
    missing=set(value.get('missing_originals',[]))
    if missing not in (set(),{'usage.json'}):raise ValueError('Completed-output missing original declaration changed')
    names-=missing
    if set(value.get('originals',{}))!=names:raise ValueError('Completed-output original set changed')
    for name,ref in value['originals'].items():
        _declared_ref(root,ref,record/name)
        if name in {'native-intent.json','experiment-config.json'}:_check(root,ref,source=source)
    _declared_ref(root,value['session'])
    _declared_ref(root,value['job'],root/'jobs'/f'{step:03d}.json')
    _check(root,value['job'],source=source)
    for ref in value['usage_originals']:_declared_ref(root,ref)
    admission=value.get('admission',{})
    import math
    if (not isinstance(admission,dict)
            or any(type(admission.get(key)) is not int or admission[key]<0
                for key in ('accounted_tokens','max_tokens','required_tokens','available_steps','required_steps'))
            or any(type(admission.get(key)) not in (int,float) or not math.isfinite(admission[key]) or admission[key]<0
                for key in ('seconds_remaining','required_seconds'))):
        raise ValueError('Completed-output original admission numbers are invalid')
    _check(root,admission['ledger'],source=source)
    return value


def require_output_eligibility(root, value):
    """Verify all returned bytes, terminal facts and retained original accounting."""
    _validate_output_acceptance(root,value)
    root=Path(root);step=value.get('step')
    if (value.get('version')!=1 or value.get('scope')!='explicit_completed_author_output'
            or type(step) is not int or step<=0 or value.get('attempt')!=2):
        raise ValueError('Explicit exact completed-output acceptance required')
    upgrade_path=_output_record(root,step,'upgrade')
    if _check(root,value['upgrade'])!=upgrade_path:raise ValueError('Completed-output upgrade identity changed')
    _validate_output_upgrade(root,c.read(upgrade_path))
    record=root/'native-evidence'/f'{step:03d}'/'continuations/002'
    names={'native-intent.json','native-outcome.json','usage.json','usage-observation.json','native.jsonl',
        'runner-process.json','native-process.json','result.pending.json','experiment-config.json','author-closeout-facts.json'}
    missing=set(value.get('missing_originals',[]))
    if missing not in (set(),{'usage.json'}) or any((record/name).exists() for name in missing):
        raise ValueError('Completed-output missing original declaration changed')
    names-=missing
    if set(value.get('originals',{}))!=names:raise ValueError('Completed-output original set changed')
    for name,ref in value['originals'].items():
        if _check(root,ref)!=record/name:raise ValueError('Completed-output original identity changed')
    session=_check(root,value['session'])
    if value.get('facts')!=_completed_output_facts(root,step,record,session):
        raise ValueError('Completed-output terminal facts changed')
    if _check(root,value['job'])!=root/'jobs'/f'{step:03d}.json':raise ValueError('Completed-output job changed')
    admission=value.get('admission',{})
    import math
    if (not isinstance(admission,dict)
            or any(type(admission.get(key)) is not int or admission[key]<0
                for key in ('accounted_tokens','max_tokens','required_tokens','available_steps','required_steps'))
            or any(type(admission.get(key)) not in (int,float) or not math.isfinite(admission[key]) or admission[key]<0
                for key in ('seconds_remaining','required_seconds'))):
        raise ValueError('Completed-output original admission numbers are invalid')
    if admission.get('blocking_unknown_steps')!=[]:
        raise ValueError('Completed-output original admission is unavailable')
    from . import budget,native
    settings=c.settings(root);events,_=budget._events(root,settings)
    ledger=_check(root,admission['ledger']);seq=c.read(ledger).get('seq')
    if (type(seq) is not int or not 1<=seq<=len(events)
            or ledger!=root/'control-ledger'/f'{seq:06d}.json'):
        raise ValueError('Completed-output budget prefix changed')
    prefix=events[:seq];limits,max_steps,_=budget._ceiling_limits(settings,prefix)
    if (admission['max_tokens']!=limits['max_tokens']
            or admission['required_tokens']!=0 or admission['required_seconds']!=0 or admission['required_steps']!=0
            or admission['available_steps']>max_steps
            or admission['seconds_remaining']>limits['max_seconds']
            or value.get('usage_originals')!=_admission_usage_refs(root,step)):
        raise ValueError('Completed-output allowance or meter originals changed')
    accounted=0
    for proof in value['usage_originals']:
        path=_check(root,proof);meter=c.read(path)
        if native._usage_event(dict(type='turn.completed',usage=meter))!=meter:
            raise ValueError('Completed-output original meter is invalid')
        accounted+=meter['input_tokens']+meter['output_tokens']
    if accounted!=admission['accounted_tokens']:
        raise ValueError('Completed-output original accounting changed')
    return value


def accepted_output(root, step, record, intent, outcome, *, source=None, seal=None, controls_only=False):
    """Revalidate bound result evidence for scientific sealing, not permission reading."""
    if source is not None:
        from . import originals
        logical=Path(source.original_root);receipt=(seal or {}).get('completed_output_acceptance')
        if receipt is None:return None
        if receipt.get('origin_identity',source.origin_identity)!=source.origin_identity:
            raise ValueError('Completed-output acceptance differs from its original source')
        _declared_ref(logical,{key:receipt[key] for key in ('path','size_bytes')},_output_record(logical,step,'acceptance'))
        value=c.read(originals.resolve_ref(source,receipt))
        _validate_output_acceptance(logical,value,source=source)
        expected=logical/'native-evidence'/f'{step:03d}'/'continuations/002'
        if (value['step']!=step or Path(record)!=expected or intent.get('attempt')!=2
                or intent.get('continuation_kind')!='author_closeout' or outcome.get('returncode')!=0
                or outcome.get('stop_reason')!='author_closeout_token_limit'):
            raise ValueError('Original completed-output acceptance changed its attempt')
        upgrade=c.read(_check(logical,value['upgrade'],source=source))
        successor=c.read(_check(logical,upgrade['successor'],source=source))
        permit=c.read(_check(logical,successor['original_permit'],source=source))
        _validate_original(logical,permit,source=source)
        if upgrade['step']!=step or permit['step']!=step:
            raise ValueError('Original completed-output acceptance changed its producer')
        for name,payload in (('native-intent.json',intent),('native-outcome.json',outcome)):
            if c.read(_check(logical,value['originals'][name],source=source))!=payload:
                raise ValueError('Completed-output acceptance belongs to another original attempt')
        if (Path(seal['raw_ref']['path'])!=expected/'science-output.json'
                or seal.get('completed_output_limitation')!=value['facts']):
            raise ValueError('Accepted original completed output changed')
        if not controls_only:
            pending=_check(logical,value['originals']['result.pending.json'],source=source)
            raw=originals.resolve_ref(source,seal['raw_ref'])
            if not raw.samefile(pending):raise ValueError('Accepted completed output alias changed')
        return dict(receipt=receipt,raw_ref=dict(seal['raw_ref']),facts=value['facts'])
    root=Path(root);path=_output_record(root,step,'acceptance')
    if not path.exists():return None
    value=require_output_eligibility(root,c.read(c.regular(path)))
    if (Path(record)!=root/'native-evidence'/f'{step:03d}'/'continuations/002'
            or intent!=c.read(root/value['originals']['native-intent.json']['path'])
            or outcome!=c.read(root/value['originals']['native-outcome.json']['path'])):
        raise ValueError('Completed-output acceptance belongs to another native attempt')
    raw=Path(record)/'science-output.json'
    pending=_check(root,value['originals']['result.pending.json'])
    if (c.regular(raw).resolve()!=raw or not raw.samefile(pending)
            or raw.stat().st_size!=value['originals']['result.pending.json']['size_bytes']):
        raise ValueError('Accepted completed output alias changed')
    return dict(receipt=_ref(root,path),raw_ref=c.file_ref(raw),facts=value['facts'])


def accept_completed_output(root, step, *, attempt, pending_ref, session, authorization_note, authorization_statement):
    """Publish already returned author bytes; never launch native or executor work."""
    from . import budget,execution,native,runtime
    root=Path(root)
    if (type(step) is not int or step<=0 or attempt!=2 or type(attempt) is not int
            or any(not isinstance(text,str) or not text.strip() for text in (authorization_note,authorization_statement))):
        raise ValueError('Exact author attempt 2 and explicit output acceptance are required')
    record=root/'native-evidence'/f'{step:03d}'/'continuations/002'
    pending=_ref(root,record/'result.pending.json')
    if pending!=pending_ref:raise ValueError('Explicit pending output digest changed')
    upgrade_path=_output_record(root,step,'upgrade')
    if upgrade_path.exists():
        upgrade=_validate_output_upgrade(root,c.read(c.regular(upgrade_path)))
        if (upgrade['authorization_note']!=authorization_note.strip()
                or upgrade['authorization_statement']!=authorization_statement.strip()):
            raise ValueError('Original completed-output authorization cannot be replaced')
    else:
        upgrade=dict(version=1,scope='completed_author_output_code_upgrade',step=step,attempt=2,
            successor=_ref(root,successor_path(root,step)),authorization_note=authorization_note.strip(),
            authorization_statement=authorization_statement.strip())
        _validate_output_upgrade(root,upgrade)
        c.write(upgrade_path,upgrade);upgrade_path.chmod(0o400)
    acceptance_path=_output_record(root,step,'acceptance')
    if acceptance_path.exists():
        value=require_output_eligibility(root,c.read(c.regular(acceptance_path)))
        if (value['originals']['result.pending.json']!=pending or _check(root,value['session'])!=Path(session).absolute()):
            raise ValueError('Original completed-output acceptance cannot be replaced')
        c._bind_original(record/'result.pending.json', record/'science-output.json', policy=c.settings(root).get('storage_policy'), root=root, size_bytes=(record/'result.pending.json').stat().st_size, source_kind='managed_frozen')
        return c.seal_result(root,step)
    paths=execution.records(root,step);job=c.read(c.regular(root/'jobs'/f'{step:03d}.json'))
    if (len(paths)!=3 or paths[-1]!=record or job['role']!='W'
            or (execution.origin(root,step)/'result-identity.json').exists()
            or (root/'pause.request').exists() or budget.closeout_state(root)['begin'] is not None
            or any(p.name>f'{step:03d}.json' for p in (root/'jobs').glob('*.json'))):
        raise ValueError('Completed output cannot replace a seal, pause, successor or closeout')
    facts=_completed_output_facts(root,step,record,Path(session).absolute())
    science=c.science_payload(record/'result.pending.json','W',job['handoff_protocol'],'selection' in job,**c.response_options(job))
    if science.get('stage_intent') not in {'review','decision'}:raise ValueError('Completed output cannot reopen exploration')
    binding=c.record_ref(record/'native-intent.json')
    for name in ('runner-process.json','native-process.json'):
        process=c.read(record/name)
        if process['boot_id']!=budget.clock_stamp()['boot_id'] or runtime.process_matches(process,binding):
            raise ValueError('Completed author still has a live or unavailable original writer')
    budget.transition(root,'advance','explicit_completed_author_output_acceptance')
    if budget.execution_blocked(root):
        raise ValueError('Completed output has unresolved original execution')
    meter=native.usage(root);limits=budget.snapshot(root)
    ledger=sorted((root/'control-ledger').glob('*.json'))[-1]
    admission=dict(accounted_tokens=meter['accounted_tokens'],blocking_unknown_steps=meter['blocking_unknown_steps'],
        max_tokens=limits['max_tokens'],seconds_remaining=limits['seconds_remaining'],
        available_steps=max(0,budget.effective_max_steps(root)-budget.used_steps(root)),
        required_tokens=0,required_seconds=0.,required_steps=0,ledger=_ref(root,ledger))
    names=('native-intent.json','native-outcome.json','usage.json','usage-observation.json','native.jsonl',
        'runner-process.json','native-process.json','result.pending.json','experiment-config.json','author-closeout-facts.json')
    missing=['usage.json'] if not (record/'usage.json').exists() else []
    value=dict(version=1,scope='explicit_completed_author_output',step=step,attempt=2,upgrade=_ref(root,upgrade_path),
        originals={name:_ref(root,record/name) for name in names if name not in missing},missing_originals=missing,
        job=_ref(root,root/'jobs'/f'{step:03d}.json'),
        session=_ref(root,Path(session).absolute()),facts=facts,admission=admission,usage_originals=_admission_usage_refs(root,step))
    require_output_eligibility(root,value)
    c.write(acceptance_path,value);acceptance_path.chmod(0o400)
    c._bind_original(record/'result.pending.json', record/'science-output.json', policy=c.settings(root).get('storage_policy'), root=root, size_bytes=(record/'result.pending.json').stat().st_size, source_kind='managed_frozen')
    return c.seal_result(root,step)
