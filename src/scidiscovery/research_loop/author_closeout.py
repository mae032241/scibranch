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


def _context_sources(root, job, *, source_facts, base, head):
    from . import candidate_refs,lineage
    projection=lineage.load_source_records(root,job['step'],source_facts=source_facts,
        base=base,head=head,job=job)
    references=candidate_refs.references(projection['records'])
    texts={}
    for source in projection['sources']:
        step=source[0]['step']
        if not references:texts[step]=source_facts[step]['raw_text']
        else:
            public=c.scientific_projection(candidate_refs.public_science_fields(source[1],references),
                f'inputs/history/nodes/{step:03d}/scientific-original.json')
            texts[step]=json.dumps(public,ensure_ascii=False,allow_nan=False)
    return references,texts


def _load_context_inputs(root, job, *, input_policy=None):
    if input_policy is None:
        from . import accepted_budget
        base = c.settings(root)
        head = accepted_budget.load(root, base=base)
        input_policy = dict(base=base, head=head, policy=c.settings_for_job(root, job, base=base, head=head))
    inputs = c.load_validated_input_facts(root, c.workspace(root, job['step']), job, **input_policy)
    return input_policy, inputs


def _known_context_original(root, job, item, *, inputs):
    """Reuse a frozen direct edge only when its full source declaration agrees."""
    from . import originals
    sources=inputs['sources']
    for reference in job['direct_refs']:
        step = int(Path(reference['path']).parts[2])
        if step not in sources:continue
        facts = sources[step]
        expected = dict(reference, source=str(facts['raw_path']))
        if item['source'] != expected:continue
        path = originals.resolve_ref(facts['owner'], facts['seal']['raw_ref'])
        return path, facts['payload']
    for reference in job.get('direct_originals', []):
        step = int(Path(reference['path']).parts[2])
        if step not in sources:continue
        facts = sources[step]
        expected = dict(path=str(Path(reference['path']).parent/'scientific-original.json'),
            source=str(facts['raw_path']), size_bytes=reference['size_bytes'])
        if item['source'] != expected:continue
        path = originals.resolve_ref(facts['owner'], facts['seal']['raw_ref'])
        return path, facts['payload']
    for reference in [*job['direct_materials'],*job['global_materials']]:
        known=_creation_input(reference,job,inputs=inputs)
        if known is not None and item['source']==dict(reference,source=str(known[0])):
            return known
    return None


def _creation_input(row, job, *, inputs):
    """Project a complete frozen input row through its owner's accepted facts."""
    if row in job['direct_refs']:
        source=inputs['sources'][int(Path(row['path']).parts[2])]
        return source['raw_path'], source['payload']
    if row in job['direct_materials'] and row['path'] in inputs['inputs']['material_paths']:
        return inputs['inputs']['material_paths'][row['path']], None
    if row in job['global_materials'] and row['path'] in inputs.get('global_paths', {}):
        return inputs['global_paths'][row['path']], None
    return None


def _context_reference(root, path, size_bytes):
    """A scientific source descriptor, separate from root-relative control refs."""
    return dict(path=str(Path(path)), source=str(Path(path)), size_bytes=size_bytes)


def _context_source(root, reference):
    from . import originals
    return (originals.material_path(root, reference) if 'source' in reference
        else _check(root, reference))


def _context_compiler(root, job, item, *, controls_only=True, with_identity=False):
    """A permanent compiler identity never depends on an earlier export inode."""
    from . import execution_inputs
    if item['source'] != item['compiler']:
        raise ValueError('Author compiler source is not its exact frozen input')
    if item['material'] not in job['direct_materials']:
        raise ValueError('Author compiler source is not a frozen input')
    compiler = execution_inputs.verify_compilation(root, item['compiler'], material=item['material'],
        client_binding=item.get('client_binding'), controls_only=controls_only)
    if item.get('code_version') != compiler.get('code_version') or 'code_version' not in compiler:
        raise ValueError('Author compiler scientific code version changed')
    owner = c.read(_check(root, compiler['job']))
    call_id = execution_inputs._compiler_call_id(root, owner['step'], item['compiler'])
    return (compiler, owner['step'], call_id) if with_identity else compiler


def _context_code(root, job, item, *, controls_only=True, experiment_config=None):
    """Interpret only an existing compiler or exact retained accepted-call entry."""
    if item['kind'] == 'compiler_author':
        _, step, call_id = _context_compiler(root, job, item,
            controls_only=controls_only, with_identity=True)
        return step, call_id
    from . import code_versions, experiments as e
    source = _check(root, item['source'])
    accepted = c.read(source)
    call_id = accepted['call_id']
    config = (c.read(c.regular(Path(root) / 'native-evidence' / f'{job["step"]:03d}' / 'experiment-config.json'))
        if experiment_config is None else experiment_config)
    records = (Path(root) / 'host-runtime/experiment-control' / f'{job["step"]:03d}' / 'records'
        if type(job.get('worker_view_version')) is int and job['worker_view_version'] == 1
        else c.workspace(root, job['step']) / 'work/lineage')
    expected = records / 'calls' / call_id / 'accepted.json'
    if (source != expected or accepted['scope'] != config['scope']
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
    input_policy, inputs = _load_context_inputs(root, job)
    references,texts=_context_sources(root,job,
        source_facts=inputs['sources'],base=input_policy['base'],head=input_policy['head'])
    from . import accepted_prefix, candidate_refs, originals
    public=record/'author-science-context';public.mkdir()
    from . import shared_input_views
    shared_input_views.dynamic_root(root,public,'retained-implementations')
    original_task=work/'science-task.json'
    target=public/'science-task.json'
    task=candidate_refs.public_fields(inputs['task_view'],references)
    c.write(target,task);target.chmod(0o400)
    entries=[dict(kind='task',source=_ref(root,original_task),display=_ref(root,target))]
    history_inputs=accepted_prefix.input_rows(job)
    input_rows={item['path']: item for item in [*job['global_materials'], *job['direct_materials'],
        *job['direct_refs'], *history_inputs, *job['history'].get('execution_recovery_materials', [])]}
    paths={item['path']:'source' for item in [*job['global_materials'],*job['direct_materials']]}
    compiled={}
    from . import execution_inputs,qualification
    for material in job['direct_materials']:
        parts=Path(material['path']).parts
        source=(inputs['sources'].get(int(parts[2])) if len(parts)>=5 and parts[:2]==('inputs','parents')
            else None)
        if source is not None and 'seal' in source:
            seal=source['seal']
        else:
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
        for item in history_inputs)
    # _validate_inputs verified these copies against the same original source
    # receipts. They remain scientific originals, never JSON display pages.
    for original in job.get('direct_originals',[]):
        parent=int(Path(original['path']).parts[2]);source=inputs['sources'][parent]
        raw=originals.resolve_ref(source['owner'],source['seal']['raw_ref'])
        reported=candidate_refs.public_science_fields(source['payload'],candidate_refs.for_job(job))
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
        known=_creation_input(row,job,inputs=inputs)
        if known is None:
            known=_known_context_original(root,job,dict(source=bound),inputs=inputs)
        source=originals.material_path(root,bound) if known is None else known[0]
        original=dict(bound,source=str(source))
        if kind=='framework':
            reader=c.read_scientific_output if name in {row['path'] for row in job['direct_refs']} else c.read
            value=reader(source) if known is None else known[1]
            c.write(target,_context_projection(name,value,references,texts));target.chmod(0o400)
        entries.append(dict(kind=kind,source=original,display=(_ref(root,target) if kind=='framework' else
            dict(path=target.relative_to(root).as_posix(),size_bytes=row['size_bytes']))))
    if permit is not None:
        science_facts(root, job['step'], context_entries=entries, context_directory=public)
    manifest=record/'author-science-context.json'
    c.write(manifest,dict(version=1,step=job['step'],directory=public.relative_to(root).as_posix(),
        candidate_references=references,entries=entries,
        omissions=[dict(category='Private control receipts and recovery bindings',
            scope='Original retained task',reason='Retained and verified privately; available scientific inputs are listed by the task.')]))
    manifest.chmod(0o400)
    return _ref(root,manifest)


def _science_context_identity(facts):
    manifest, job = facts['manifest'], facts['job']
    if (manifest.get('version') != 1 or manifest.get('step') != job['step']
            or facts['directory'] != facts['manifest_path'].parent / 'author-science-context'):
        raise ValueError('Author scientific context has no exact retained task')


def load_science_context_facts(root, job, proof, *, input_policy=None):
    """Own this operation's context reads and exact-original acceptance.

    The returned values are local facts, not a reusable qualification or cache.
    Code exports remain with code_versions.materialize, which verifies their
    physical tree at its writer boundary.
    """
    root = Path(root)
    manifest_path = _check(root, proof)
    manifest = c.read(manifest_path)
    facts = dict(root=root, job=job, proof=proof, manifest_path=manifest_path, manifest=manifest,
        directory=root / manifest['directory'], entries=[])
    _science_context_identity(facts)
    input_policy, inputs = _load_context_inputs(root, job, input_policy=input_policy)
    facts['input_policy'] = input_policy
    facts['sources'] = inputs['sources']
    references, texts = _context_sources(root, job,
        source_facts=inputs['sources'],base=input_policy['base'],head=input_policy['head'])
    facts.update(references=references, texts=texts)
    if references != manifest['candidate_references']:
        raise ValueError('Author scientific references changed')
    experiment_config = None
    if any('code_version' in item and item['kind'] != 'compiler_author' for item in manifest['entries']):
        experiment_config = c.read(c.regular(root / 'native-evidence' / f'{job["step"]:03d}' / 'experiment-config.json'))
    for item in manifest['entries']:
        row = dict(item=item)
        if 'code_version' in item:
            display = root / item['display']['path']
            if (set(item['display']) != {'path'} or Path(item['display']['path']).is_absolute()
                    or '..' in Path(item['display']['path']).parts or not display.is_relative_to(facts['directory'])):
                raise ValueError('Author scientific display escaped its context')
            row.update(display=display, code_identity=_context_code(root, job, item,
                experiment_config=experiment_config))
        else:
            display = _declared_ref(root, item['display'])
            if not display.is_relative_to(facts['directory']):
                raise ValueError('Author scientific display escaped its context')
            known = _known_context_original(root, job, item, inputs=inputs)
            source = _context_source(root, item['source']) if known is None else known[0]
            row.update(source=source, display=display)
            if item['kind'] in {'source', 'compiler_author'}:
                row['same_original'] = ('source' in item['source']
                    or source.samefile(_check(root, item['display'])))
                if item['kind'] == 'compiler_author':
                    from . import execution_inputs
                    compiler = execution_inputs.verify_compilation(root, item['compiler'],
                        material=item['material'], client_binding=item['client_binding'])
                    row['compiler_source'] = _check(root, compiler['author'])
            else:
                display = _check(root, item['display'])
                name = display.relative_to(facts['directory']).as_posix()
                reader = (c.read_scientific_output if name in {row['path'] for row in job['direct_refs']}
                    else c.read)
                task_source=c.workspace(root,job['step'])/'science-task.json'
                row.update(source_value=(known[1] if known is not None else
                    inputs['task_view'] if item['kind']=='task' and source==task_source else
                    c.read(source) if item['kind'] == 'task' else reader(source)),
                    display_value=c.read(display), name=name)
        facts['entries'].append(row)
    validate_science_context_from_facts(facts)
    return facts


def validate_science_context_from_facts(facts):
    """Validate the loaded author view without reopening files or metadata."""
    _science_context_identity(facts)
    root, job, manifest = facts['root'], facts['job'], facts['manifest']
    directory, references, texts = facts['directory'], facts['references'], facts['texts']
    if references != manifest['candidate_references']:
        raise ValueError('Author scientific references changed')
    from . import candidate_refs
    for row in facts['entries']:
        item, display = row['item'], row['display']
        if 'code_version' in item:
            if (set(item['display']) != {'path'} or Path(item['display']['path']).is_absolute()
                    or '..' in Path(item['display']['path']).parts or not display.is_relative_to(directory)):
                raise ValueError('Author scientific display escaped its context')
            continue
        source = row['source']
        if not display.is_relative_to(directory):raise ValueError('Author scientific display escaped its context')
        if item['kind']=='compiler_author':
            if item['material'] not in job['direct_materials']:raise ValueError('Author compiler source is not a frozen input')
            if (row['compiler_source'] != source or not row['same_original']
                    or item['source']['size_bytes']!=item['display']['size_bytes']):
                raise ValueError('Author compiler scientific display changed')
        elif item['kind']=='source':
            if (item['source']['size_bytes']!=item['display']['size_bytes']
                    or not row['same_original']):
                raise ValueError('Author scientific original changed')
        else:
            expected=(candidate_refs.public_fields(row['source_value'],references) if item['kind']=='task' else
                _context_projection(row['name'],row['source_value'],references,texts))
            if row['display_value']!=expected:raise ValueError('Author scientific display differs from its exact original')
    return directory


def validate_science_context(root, job, proof):
    """Independent read/acceptance entry point for one retained author view."""
    return load_science_context_facts(root, job, proof)['directory']


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
    relative = Path(path).relative_to(root).as_posix()
    if not source._index.get((originals.identity_key(source.origin_identity), relative)):
        return False
    originals.resolve_ref(source, dict(path=relative))
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


def load_intent_eligibility(root, job, intent, *, verified=None, input_policy=None):
    """Produce the original permit and accepted author view in this operation."""
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
    context_facts = (load_science_context_facts(root,job,intent['author_closeout_science_context'],
        input_policy=input_policy) if intent.get('author_context_version')==1 else None)
    original_config=_check(root,original['original_experiment_config'])
    projection=c.regular(root/'native-evidence'/f'{job["step"]:03d}'/'continuations'/f'{attempt:03d}'/'experiment-config.json')
    if projection.read_bytes()!=original_config.read_bytes():
        raise ValueError('Author closeout retained execution projection changed')
    return value, context_facts


def require_intent_eligibility(root, job, intent, *, verified=None):
    """Independent eligibility entry point retaining its original permit result."""
    return load_intent_eligibility(root, job, intent, verified=verified)[0]


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
