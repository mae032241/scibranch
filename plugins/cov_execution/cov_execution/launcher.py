"""Isolated cov launcher: host binds lineage; native receives public work only."""
from pathlib import Path
import argparse, hashlib, json, os, shlex, shutil, sys
from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY

def engineering_reserve(s):
    # New launcher copies opt in; preserved launcher/config originals are untouched.
    seconds=s.get('cov_engineering_check_reserve_seconds',15)
    if type(seconds) not in (int,float) or not (seconds==0 or 2<seconds<65):
        raise ValueError('cov_check_reserve_requires_zero_or_seconds_between_2_and_65')
    return seconds

def engineering_notice(s):
    reserve=engineering_reserve(s)
    prefix=f'Cov original call window: 65s total; requested engineering reserve {reserve:g}s. '
    if s.get('cov_receipt_channel','public_runner_main')!='public_runner_main':
        return prefix+'This receipt channel has no isolated check capability; no partition is promised.'
    if not reserve:
        return prefix+'No reserved check window; checking is best effort within the original deadline.'
    return prefix+(f'Experiment at most {65-reserve:g}s; check at most {min(10,reserve-2):g}s; feedback 2s. '
        'Remaining task time may shorten these windows. Actual reservation is in engineering_check.time_allocation; no extra time is granted.')

def regular(p, bound=None):
    p=Path(p).absolute()
    if p.is_symlink() or not p.is_file() or p.resolve()!=p or bound is not None and p.stat().st_size>bound:
        raise ValueError('regular_bounded_file_required')
    return p.read_bytes()

def file_digest(p):
    p=Path(p).absolute()
    if p.is_symlink() or not p.is_file() or p.resolve()!=p:
        raise ValueError('bounded_native_binary_required')
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def load(p):
    s=json.loads(regular(p));public=Path(s['public_root'])
    engineering_reserve(s)
    if s.get('cov_receipt_channel','public_runner_main') not in {'public_runner_main','backend_file'}:
        raise ValueError('explicit_cov_receipt_channel_required')
    if not public.is_absolute() or public.resolve()!=public:
        raise ValueError('absolute_unsymlinked_public_root_required')
    m=json.loads(regular(s['public_manifest']))
    if m['task_revision']!='d78f5eb52ad754c5ee9154741af73130a85a65b8':
        raise ValueError('exact_cov_revision_required')
    expected_files={'task.toml','instruction.md','environment/Dockerfile',*(f'environment/harness/{n}' for n in ['exp_cli.py','runner.py','train.csv','val.csv','meta.json'])}
    if set(m['public_files_sha256'])!=expected_files:raise ValueError('public_whitelist_mismatch')
    expected={}
    for name in expected_files:
        q=Path(name)
        for member in (q,*q.parents[:-1]):expected.setdefault(member.parent,set()).add(member.name)
    for directory,names in expected.items():
        q=public/directory
        if q.is_symlink() or not q.is_dir() or {v.name for v in q.iterdir()}!=names:raise ValueError('public_directory_whitelist_changed')
    for name,h in m['public_files_sha256'].items():
        n=Path(name)
        if n.is_absolute() or '..' in n.parts or hashlib.sha256(regular(public/n)).hexdigest()!=h:
            raise ValueError('frozen_public_input_changed')
    for p in (s['native'],s['code_mode_host']['path']):
        regular(p)
    return s

def controlled(s, workspace, home):
    scene=Path(s['scene_root']).absolute();w=Path(workspace).absolute();h=Path(home).absolute()
    if not scene.is_dir() or scene.resolve()!=scene or not w.is_dir() or w.resolve()!=w:
        raise ValueError('fresh_scene_and_workspace_required')
    if not w.is_relative_to(scene) or not h.is_relative_to(scene) or h==scene or h.is_relative_to(w) or w.is_relative_to(h):
        raise ValueError('separate_scene_home_workspace_required')
    if h.exists() and (not h.is_dir() or h.resolve()!=h):
        raise ValueError('unsymlinked_home_required')
    return w,h

def direct_materials(s,w,job_path):
    if not job_path:raise ValueError('trusted_job_manifest_required')
    p=Path(job_path).absolute();root=Path(s['scene_root'])
    if p.parent!=root/'jobs' or p.is_relative_to(w):raise ValueError('operator_job_root_required')
    job=json.loads(regular(p));items=job.get('direct_materials',[])
    if job.get('worker_view_version')==1 or s.get('_author_science_context'):
        from scidiscovery.research_loop import contracts as c
        c._validate_inputs(root,w,job)
        return []  # The exact scientific read copies are already inside w/inputs.
    if not isinstance(items,list):raise ValueError('direct_material_list_required')
    result=[];totals={};targets=set();managed={}
    for item in items:
        if not isinstance(item,dict) or set(item)!= {'source','path','sha256','size_bytes'}:
            raise ValueError('exact_direct_material_fields_required')
        source=Path(item['source']).absolute();dest=Path(item['path'])
        nodes=root/'project/nodes'
        if not source.is_relative_to(nodes) or source.is_relative_to(w):
            raise ValueError('exact_parent_artifact_path_required')
        relative=source.relative_to(nodes)
        if len(relative.parts)<3 or relative.parts[0]==w.name:
            raise ValueError('exact_parent_artifact_path_required')
        node=relative.parts[0]; inner=Path(*relative.parts[1:])
        if inner.parts[0]=='artifacts':
            exported=Path(*inner.parts[1:])
        elif inner.parts[0]=='experiments':
            parts=inner.parts
            while len(parts)>=3 and parts[0]=='experiments':parts=parts[2:]
            if not (parts[0]=='artifacts' and len(parts)>1 or len(parts)==1 and parts[0] in ('conclusion.json','conclusion.md')):
                raise ValueError('intermediate_material_not_exportable')
            exported=inner
        elif (len(inner.parts)==6 and inner.parts[:3]==('work','lineage','calls')
                and inner.parts[4]=='observed-originals' and inner.name==item['sha256']):
            # Common execution outputs are content-addressed retained originals,
            # not arbitrary work files. Reuse the sealed control observation and
            # its descriptor validation; never rediscover a live backend here.
            from scidiscovery.research_loop import contracts as c
            if node not in managed:
                parent=nodes/node;c.validate(parent)
                seal=c.read(c.regular(c.host_record(parent)/'result-identity.json'))
                receipt=seal.get('observations')
                if receipt is None:raise ValueError('managed_material_observation_required')
                observed=c.read(c.regular(root/receipt['path']))
                managed[node]=(seal['materials'],observed['managed_calls'])
            materials,calls=managed[node]
            matching=[(call,output) for call in calls for output in call.get('outputs',[])
                if call['call_id']==inner.parts[3] and root/output['path']==source
                and output['sha256']==item['sha256'] and output['size_bytes']==item['size_bytes']
                and dest==Path('inputs/parents')/node/'artifacts/experiments'/call['call_id']/'artifacts'/output['name']]
            if item not in materials or len(matching)!=1:
                raise ValueError('exact_managed_material_required')
            call,output=matching[0]
            exported=Path('experiments')/call['call_id']/'artifacts'/output['name']
        else:raise ValueError('intermediate_material_not_exportable')
        expected=Path('inputs/parents')/node/'artifacts'/exported
        if dest!=expected or dest.is_absolute() or '..' in dest.parts:
            raise ValueError('exact_parent_artifact_path_required')
        if type(item['size_bytes']) is not int or item['size_bytes']<0:
            raise ValueError('direct_material_file_bound')
        if source.is_symlink() or not source.is_file() or source.resolve()!=source:
            raise ValueError('regular_original_file_required')
        if source.stat().st_size!=item['size_bytes'] or file_digest(source)!=item['sha256']:
            raise ValueError('direct_material_digest_mismatch')
        target=w/dest
        if str(target) in targets:raise ValueError('duplicate_direct_material_target')
        targets.add(str(target));target.parent.mkdir(parents=True,exist_ok=True)
        if target.parent.resolve()!=target.parent or target.is_symlink():raise ValueError('material_target_symlink')
        if not target.exists():target.touch(mode=0o444)
        elif not target.is_file():raise ValueError('material_target_not_file')
        result.append((str(source),str(target)))
    return result

def environment(s,w):
    return {'PATH':str(Path(s['scene_root'])/'host-runtime/native-bin')+':'+s.get('_closeout_bin',str(w/'bin'))+':'+str(Path(s['task_python']).parent)+':/usr/bin:/bin',
        'EXP_HARNESS':'/opt/exp','EXP_RUN_DIR':s['runs_dir'],'EXP_TRAIN':'/opt/exp/train.csv',
        'EXP_VAL':'/opt/exp/val.csv','EXP_META':'/opt/exp/meta.json','EXP_RUNNER':'/opt/exp/runner.py',
        'EXP_DEFAULT_SCRIPT':str(w/'train.py'),'EXP_WALL_CAP_S':'60','EXP_AUDIT_BUDGET_S':'45',
        'EXP_AUDIT_LEAKAGE_THRESHOLD':'0.10','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1',
        'MKL_NUM_THREADS':'1','TMPDIR':'/task-tmp','HOME':'/task-tmp','LOOPX_USAGE_PING':'0',
        'PYTHONDONTWRITEBYTECODE':'1'}

def exp_wrapper(s,w):
    if s.get('_read_only_closeout'):
        facts=Path(s.get('_closeout_facts',str(w/'work/author-closeout-facts.json')))
        view=('exec /bin/cat '+shlex.quote(str(facts))) if s.get('_author_closeout') else 'printf \'%s\\n\' \'{"status":"closeout_read_only","note":"Read science-task.json and bound original candidate evidence"}\''
        return ('#!/bin/sh\ncase "$1" in\nlineage-status) '+view+';;\n'
            '*) printf \'%s\\n\' \'{"status":"known_not_started","accepted":false,"note":"Operator closeout permits preserved evidence only; no native execution or submission"}\'; exit 2;;\nesac\n')
    warning = ('Historical cross-node delivery: use A next_action=deliver and the controller adapter. '
        'Native exp submit uses the paths declared in the run and does not resolve archived historical originals.')
    public = 'exec ' + shlex.quote(s['task_python']) + ' -B -I /opt/exp/exp_cli.py "$@"'
    notice='if [ "$1" = run ]; then printf \'%s\\n\' '+shlex.quote(engineering_notice(s))+' >&2; fi\n'
    if s.get('_worker_science_view'):
        return ('#!/bin/sh\n'+notice+'exec '+shlex.quote(s['scid_python'])+
            ' -B -I -m scidiscovery.research_loop.worker_execution_bridge --submission-directory '+shlex.quote(str(w/SUBMISSION_DIRECTORY))+' "$@"\n')
    return ('#!/bin/sh\n'+notice+'case "$1" in\nrun|lineage-status|query|lookup|collect|cancel|reconcile|retire|compile|capabilities|describe) exec '
        + shlex.quote(s['scid_python']) + ' -B -I -m scidiscovery.research_loop.experiments --config '
        + shlex.quote(str(w/'work/lineage-config.json')) + ' "$@";;\nsubmit|--help|-h|help) printf \'%s\\n\' '
        + shlex.quote(warning) + ' >&2; ' + public + ';;\n*) ' + public + ';;\nesac\n')


def native_profile(s):
    from scidiscovery.research_loop import profiles, contracts as c, configuration_updates
    declared = {'service_tier':'default', **s}
    manifest = os.environ.get('THIN_HWA_JOB_MANIFEST')
    if manifest is not None:
        root = Path(s['scene_root']).absolute()
        path = Path(manifest).absolute()
        job = c.read(c.regular(path))
        if path != root/'jobs'/f'{job["step"]:03d}.json':
            raise ValueError('exact_job_manifest_required')
        base = c.settings(root)
        def models(settings):
            return {role: profiles.selected(settings,role) for role in profiles.ROLES}
        # Operator launch JSON need not repeat private Skill manifests.
        declared_profiles = dict(base, **declared)
        if models(declared_profiles) != models(base):
            raise ValueError('launcher_role_profiles_mismatch')
        return profiles.for_job(configuration_updates.for_job(root, job), job)
    return profiles.selected(declared, os.environ['THIN_HWA_ROLE'])

def native_skill_mounts(s,w):
    """Return only verified current-role snapshots; keep manifests host-private."""
    if s.get('_backend'):
        return []
    from scidiscovery.research_loop import contracts as c, skill_supply
    root=Path(s['scene_root']).absolute()
    path=Path(os.environ.get('THIN_HWA_JOB_MANIFEST',str(root/'jobs'/(w.name+'.json')))).absolute()
    if path != root/'jobs'/(w.name+'.json'):
        raise ValueError('exact_skill_job_manifest_required')
    job=c.read(c.regular(path))
    return skill_supply.native_mounts(root,job)

def owner_metadata(s,w):
    """Share two exact read-only originals; cold-owner anchors stay host-only.

    This launcher cannot carry an enrolled owner's external lease into its
    namespace. Reject that case on the host, rather than interpreting hidden
    owner files as an unenrolled instance inside the namespace.
    """
    from scidiscovery.research_loop import contracts as c, instance_owner
    root=Path(s['scene_root'])
    try:
        c.settings(root)  # Missing/corrupt settings identity is never unenrolled.
        owner=instance_owner.require_owner(root)
    except (KeyError,TypeError,AttributeError) as error:
        raise ValueError('cov_namespace_owner_unavailable') from error
    if owner is not None:
        raise ValueError('cov_namespace_cold_enrolled_owner_unsupported')
    paths=(root/'jobs'/(w.name+'.json'),root/'settings-identity.json')
    for path in paths:regular(path)
    return paths

def prepare_home(s,w,h, auth_source=None):
    metadata=owner_metadata(s,w)  # Refuse before creating native homes or wrappers.
    profile=native_profile(s)
    h.mkdir(parents=True,exist_ok=True);(h/'tmp/arg0').mkdir(parents=True,exist_ok=True)
    for p in [Path(s['runs_dir']),Path(s['submission_dir'])]:
        if not p.is_relative_to(Path(s['scene_root'])):
            raise ValueError('scene_output_root_required')
        p.mkdir(parents=True,exist_ok=True)
        if p.resolve()!=p:raise ValueError('unsymlinked_scene_output_required')
    evidence=Path(s['scene_root'])/'native-evidence'/w.name;evidence.mkdir(parents=True,exist_ok=True)
    if evidence.resolve()!=evidence:raise ValueError('unsymlinked_native_evidence_required')
    helper=Path(s['scene_root'])/'host-runtime/native-bin';helper.mkdir(parents=True,exist_ok=True)
    if helper.resolve()!=helper:raise ValueError('unsymlinked_native_helper_root_required')
    for name,target in [('codex-linux-sandbox',s['native']),('apply_patch',s['native']),('codex-code-mode-host',s['code_mode_host']['path'])]:
        alias=helper/name
        if alias.is_symlink():
            if str(alias.readlink())!=target:raise ValueError('native_helper_alias_changed')
        elif alias.exists():raise ValueError('native_helper_alias_not_symlink')
        else:alias.symlink_to(target)
    bin_directory=h/'scid-bin' if s.get('_read_only_closeout') else w/'bin'
    bin_directory.mkdir(exist_ok=True)
    if s.get('_read_only_closeout'):s['_closeout_bin']=str(bin_directory)
    wrapper = exp_wrapper(s,w)
    p=bin_directory/'exp'
    if p.exists() and regular(p).decode()!=wrapper:raise ValueError('exp_wrapper_changed')
    if not p.exists():p.write_text(wrapper);p.chmod(0o555)
    grants={':root':'deny',':minimal':'read',str(w):'write',str(w/'inputs'):'read',str(h/'tmp/arg0'):'read',
        s['public_root']:'read','/instruction.md':'read','/opt/exp':'read','/opt/exp/runs':'write',
        '/submission':'write','/task-tmp':'write',s['runs_dir']:'write',s['submission_dir']:'write',
        s['native']:'read',s['code_mode_host']['path']:'read',str(helper):'read'}
    grants.update({str(target):'read' for source,target in native_skill_mounts(s,w)})
    control=Path(s['scene_root'])/'host-runtime/experiment-control'/hashlib.sha256(regular(os.environ['THIN_HWA_JOB_MANIFEST'])).hexdigest()
    if s.get('_worker_science_view'):
        grants['/task-result']='write'
    else:
        grants[str(control)]='read'
        grants.update({str(path):'read' for path in metadata})
    if s.get('_read_only_closeout'):
        grants.update({str(w):'read',str(w/'artifacts'):'write',str(bin_directory):'read',
            '/opt/exp/runs':'read','/submission':'read',s['runs_dir']:'read',s['submission_dir']:'read',
            **({} if s.get('_worker_science_view') else {s['_closeout_record']:'write'})})
        if s.get('_author_closeout'):
            if not s.get('_worker_science_view'):
                grants[str(Path(s['_closeout_record'])/'experiment-config.json')]='read'
            facts=Path(s.get('_closeout_facts',str(Path(s['_closeout_record'])/'author-closeout-facts.json')))
            if facts.exists():grants[str(facts)]='read'
    for p in ['/usr','/bin','/lib','/lib64',*s['runtime_paths']]:grants[p]='read'
    lines=['model='+json.dumps(profile['model']),'model_reasoning_effort='+json.dumps(profile['reasoning_effort']),
        'service_tier='+json.dumps(profile['service_tier']),'default_permissions='+json.dumps(s['profile']),
        'approval_policy="never"','web_search="disabled"','[permissions.'+s['profile']+'.filesystem]']
    lines.extend(json.dumps(k)+'='+json.dumps(v) for k,v in grants.items())
    lines.extend(['[permissions.'+s['profile']+'.network]','enabled=false','[shell_environment_policy]',
        'inherit="none"','[shell_environment_policy.set]'])
    lines.extend(json.dumps(k)+'='+json.dumps(v) for k,v in environment(s,w).items())
    lines.extend(['[features]','fast_mode=false','apps=false','multi_agent=false'])
    content='\n'.join(lines)+'\n';p=h/'config.toml'
    if p.exists() and regular(p).decode()!=content:raise ValueError('frozen_home_config_changed')
    if not p.exists():p.write_text(content);p.chmod(0o600)
    if auth_source:
        auth=h/'auth.json'
        if not auth.exists():auth.write_bytes(regular(auth_source));auth.chmod(0o600)
    # This metadata is operator-owned outside the scientific workspace.
    if not s.get('_worker_science_view'):
        (h/'launcher-config-sha256.txt').write_text(hashlib.sha256(content.encode()).hexdigest()+'\n')

def outer(s,w,h,command,materials=()):
    metadata=owner_metadata(s,w)
    output_bind='--ro-bind' if s.get('_read_only_closeout') else '--bind'
    c=[shutil.which('bwrap') or 'bwrap','--unshare-user','--unshare-pid','--unshare-ipc','--unshare-uts',
       '--die-with-parent','--new-session','--clearenv','--proc','/proc','--dev','/dev','--tmpfs','/tmp']
    for p in dict.fromkeys(['/usr','/bin','/lib','/lib64',*s['runtime_paths'],s['native'],s['code_mode_host']['path']]):
        if Path(p).exists():c.extend(['--ro-bind',p,p])
    for p in ['/etc/resolv.conf','/etc/hosts','/etc/ssl/certs']:
        if Path(p).exists():c.extend(['--ro-bind',p,p])
    c.extend(['--ro-bind',str(Path(s['scene_root'])/'host-runtime/native-bin'),str(Path(s['scene_root'])/'host-runtime/native-bin'),'--ro-bind',s['public_root'],s['public_root'],'--ro-bind',s['public_root']+'/instruction.md','/instruction.md',
        '--dir','/opt/exp','--dir','/opt/exp/runs',output_bind,str(w),str(w),
        output_bind,s['runs_dir'],'/opt/exp/runs',output_bind,s['submission_dir'],'/submission',
        output_bind,s['runs_dir'],s['runs_dir'],output_bind,s['submission_dir'],s['submission_dir'],
        '--tmpfs','/task-tmp','--chdir',str(w)])
    if not s.get('_backend'):
        c.extend(['--bind',str(h),str(h)])
    else:
        c.extend(['--ro-bind',str(h/'config.toml'),str(h/'config.toml')])
        if (w/'inputs').is_dir():c.extend(['--ro-bind',str(w/'inputs'),str(w/'inputs')])
        # Same profile paths exist, but scientific code cannot submit Agent
        # requests or inspect its native result transport.
        c.extend(['--tmpfs',str(w/SUBMISSION_DIRECTORY),'--dir','/task-result'])
    if s.get('_worker_science_view'):
        if s.get('_public_result') and not s.get('_backend'):
            c.extend(['--bind',s['_public_result'],'/task-result'])
    else:
        c.extend([output_bind,str(Path(s['scene_root'])/'native-evidence'/w.name),str(Path(s['scene_root'])/'native-evidence'/w.name)])
    if s.get('_legacy_science_overlay') and not s.get('_backend'):
        # Newly authorized public author views never expose frozen legacy
        # control files. Preserve their host bytes beneath a science-only view.
        c.extend(['--tmpfs',str(w/'work')])
        for name in ('retained-experiments','author-closeout-science.json','budget-status.json'):
            science=w/'work'/name
            if science.exists():c.extend(['--ro-bind',str(science),str(science)])
    if s.get('_author_science_context') and not s.get('_backend'):
        context=Path(s['_author_science_context'])
        c.extend(['--ro-bind',str(context/'science-task.json'),str(w/'science-task.json'),
            '--tmpfs',str(w/'inputs')])
        if (context/'inputs').is_dir():c.extend(['--ro-bind',str(context/'inputs'),str(w/'inputs')])
        if (context/'work/retained-implementations').is_dir():
            c.extend(['--ro-bind',str(context/'work/retained-implementations'),str(w/'work/retained-implementations')])
    if s.get('_read_only_closeout'):
        c.extend(['--bind',str(w/'artifacts'),str(w/'artifacts')])
        if not s.get('_worker_science_view'):
            c.extend(['--bind',s['_closeout_record'],s['_closeout_record']])
        if s.get('_author_closeout'):
            if not s.get('_worker_science_view'):
                projection=str(Path(s['_closeout_record'])/'experiment-config.json')
                c.extend(['--ro-bind',projection,projection])
            facts=Path(s.get('_closeout_facts',str(Path(s['_closeout_record'])/'author-closeout-facts.json')))
            if facts.exists():c.extend(['--ro-bind',str(facts),str(facts)])
    for name in ['exp_cli.py','runner.py','train.csv','val.csv','meta.json']:
        c.extend(['--ro-bind',str(Path(s['scene_root'])/'host-runtime/native-bin'),str(Path(s['scene_root'])/'host-runtime/native-bin'),'--ro-bind',s['public_root']+'/environment/harness/'+name,'/opt/exp/'+name])
    for source,target in materials:c.extend(['--ro-bind',source,target])
    for source,target in native_skill_mounts(s,w):c.extend(['--ro-bind',str(source),str(target)])
    if not s.get('_backend'):
        for source,target in s.get('_author_artifact_masks',()):c.extend(['--ro-bind',source,target])
    if not s.get('_worker_science_view'):
        for path in metadata:c.extend(['--ro-bind',str(path),str(path)])
    if not s.get('_worker_science_view'):
        c.extend(['--ro-bind', str(Path(s['scene_root'])/'host-runtime'/('lineage-'+w.name+'.json')), str(w/'work/lineage-config.json')])
    manifest=Path(s['scene_root'])/'jobs'/(w.name+'.json')
    control=Path(s['scene_root'])/'host-runtime/experiment-control'/hashlib.sha256(regular(manifest)).hexdigest()
    if control.exists() and not s.get('_worker_science_view'):c.extend(['--ro-bind',str(control),str(control)])
    for source,target,mode in s.get('_backend_materials',()):c.extend([mode,source,target])
    for k,v in {**environment(s,w),'CODEX_HOME':str(h),'LANG':'C.UTF-8'}.items():c.extend(['--setenv',k,v])
    return c+command


def backend_launcher(s,w,h,materials):
    """Host-selected scientific child mounts; callers cannot supply this plan."""
    def launch(config,accepted,command,cwd):
        from scidiscovery.research_loop import contracts as c, experiments as e
        from cov_execution import receipt as cov_receipt
        directory=e.Records(config).directory(accepted['call_id'])
        if e.Records(config).accepted(accepted['call_id'])!=accepted:
            raise e.IntegrityError('Scientific launch differs from its original accepted call')
        settings=dict(s,_backend=True,_read_only_closeout=False,_author_closeout=False)
        bindings=[];finish=None
        source=Path(accepted['source_script'])
        if source.resolve()!=source or not source.is_relative_to(w):
            raise e.UnsupportedExecution('Scientific source is outside the declared task namespace')
        if config['execution_interface']['executor']=='cov_execution:experiment':
            transport=accepted['receipt_transport']
            if transport.get('channel')!='public_runner_main':
                raise e.UnsupportedExecution('This retained receipt channel has no science-only backend transport')
            if cov_receipt.runner_profile(transport['runner']['path'])!=transport['runner']:
                raise e.IntegrityError('Scientific runner differs from its frozen original')
            stage=directory/'backend-science';inputs=stage/'inputs';outputs=stage/'outputs'
            inputs.mkdir(parents=True);outputs.mkdir()
            invocation=inputs/'invocation.json'
            e.write(invocation,dict(runner=transport['runner']['path'],source=str(source),
                output_line=transport['runner']['output_line'],output='/task-tmp/scid-receipt/row.json'))
            bindings.extend([(str(invocation),'/opt/exp/scid-invocation.json','--ro-bind'),
                (str(outputs),'/task-tmp/scid-receipt','--bind'),
                (str(directory/'source.py'),str(source),'--ro-bind')])
            command=[config['run_argv'][0],'-B','-I',str(Path(cov_receipt.__file__).resolve()),'--science-runner','/opt/exp/scid-invocation.json']
            cwd=str(w)
            def finish(returncode,cleanup,transport_complete):
                row_path=outputs/'row.json'
                if not row_path.exists():return
                if (e.proof(directory/'source.py')!=accepted['script']
                        or e.proof(source)['sha256']!=accepted['script']['sha256']):
                    raise e.IntegrityError('Scientific source changed during original execution')
                row=json.loads(e.read(row_path,cov_receipt.MAX_BYTES))
                # The existing completion owner still checks aggregate row,
                # actual returncode, transport and cleanup before any success.
                if not isinstance(row,dict) or row.get('script')!=str(source):
                    raise e.IntegrityError('Scientific runner row differs from its original source')
                e.write(directory/'receipt.json',cov_receipt.envelope(accepted,row))
        elif config['execution_interface']['executor']=='local_execution:command':
            command=list(command)
            if 'entry' in accepted:
                target=Path(accepted['experiment_directory'])
                if target.resolve()!=target or not target.is_relative_to(w):
                    raise e.UnsupportedExecution('Scientific experiment is outside its task namespace')
                bindings.extend([(str(directory/'inputs'),str(target),'--ro-bind'),
                    (str(directory/'inputs/artifacts'),str(target/'artifacts'),'--bind')])
                command[accepted['script_index']]=str(target/accepted['entry']);cwd=str(target)
            else:
                bindings.append((str(directory/'source.py'),str(source),'--ro-bind'))
                command[accepted['script_index']]=str(source)
                if cwd is not None and (Path(cwd).resolve()!=Path(cwd) or not Path(cwd).is_relative_to(w)):
                    raise e.UnsupportedExecution('Scientific working directory is outside its task namespace')
        else:
            raise e.UnsupportedExecution('Selected local backend has no qualified scientific namespace launcher')
        settings['_backend_materials']=bindings
        helper=Path(s['scene_root'])/'host-runtime/native-bin/codex-linux-sandbox'
        native=helper.resolve(strict=True)
        # The same named permissions profile enforces read grants and protected
        # metadata for scientific children; this trusted bootstrap never runs
        # caller source before the actual no-provider sandbox helper.
        bootstrap=('umask 077; mkdir -p /task-tmp/science-runtime; '
            'chmod 700 /task-tmp /task-tmp/science-runtime; '
            'export TMPDIR=/task-tmp/science-runtime XDG_RUNTIME_DIR=/task-tmp/science-runtime; exec "$@"')
        scientific=['/bin/sh','-c',bootstrap,'scid-scientific-sandbox',str(native),
            'sandbox','-P',s['profile'],'-C',cwd or str(w),'--',*command]
        wrapped=outer(settings,w,h,scientific,materials)
        position=wrapped.index('--chdir');wrapped[position+1]=cwd or str(w)
        return wrapped,None,finish
    return launch

def accepted_attempt(s,w,output,*,resume_thread=None,schema=None):
    """Bind fresh/resumed exec to this exact already accepted native dispatch."""
    from scidiscovery.research_loop import backends,contracts as c,execution,continuation,runtime,profiles,configuration_updates
    from scidiscovery.research_loop.protocol import require_current
    import time
    root=Path(s['scene_root']).absolute();step=int(w.name)
    job_path=Path(os.environ['THIN_HWA_JOB_MANIFEST']).absolute()
    if job_path!=root/'jobs'/f'{step:03d}.json' or job_path.resolve()!=job_path:
        raise ValueError('exact_job_manifest_required')
    job=c.read(c.regular(job_path));require_current(root,job)
    if w!=c.workspace(root,step) or os.environ['THIN_HWA_ROLE']!=job['role']:
        raise ValueError('accepted_role_workspace_mismatch')
    base=c.settings(root)
    frozen=configuration_updates.for_job(root,job)
    declared={'service_tier':'default', **s}
    def models(settings):
        return {role: profiles.selected(settings,role) for role in profiles.ROLES}
    # Skill/Worker context comes from the original instance, not operator JSON.
    if models(dict(base, **declared)) != models(base):
        raise ValueError('launcher_role_profiles_mismatch')
    profiles.for_job(frozen,job)
    continuation.validate_task(root,step)
    if resume_thread is not None or frozen.get('resume_policy') is not None:
        continuation.validate_launcher(root,step)
    paths=execution.records(root,step);number=len(paths)-1;record=paths[number]
    if output!=record/'result.pending.json' or output.resolve()!=output:
        raise ValueError('output_path_outside_accepted_attempt')
    if (record/'native-outcome.json').exists() or output.exists():
        raise ValueError('original_dispatch_already_terminal_or_returned')
    intent=c.read(c.regular(record/'native-intent.json'))
    if intent.get('task_sha256')!=c.digest(job_path):raise ValueError('accepted_native_task_changed')
    profiles.validate_intent(frozen,job,intent)
    profiles.validate_intent(frozen,job,c.read(execution.origin(root,step)/'native-intent.json'))
    if Path(os.environ['THIN_HWA_NATIVE_HOME']).absolute()!=execution.home(root,step,number):
        raise ValueError('accepted_native_home_mismatch')
    expected_schema=execution.origin(root,step)/'science-result.schema.json'
    if resume_thread is not None:
        if not number or intent.get('session') is None:
            raise ValueError('accepted_resume_session_required')
        if intent.get('continuation_kind')=='fragment':
            if (intent.get('context_mode')!='same_session'
                    or (frozen.get('worker_fragment_policy') or {}).get('context')!='same_session'):
                raise ValueError('accepted_resume_context_mismatch')
        elif intent.get('continuation_kind')=='repair':
            continuation.validate_repair(root,step,intent)
        else:raise ValueError('accepted_resume_kind_required')
        original=continuation.session(root,step,paths[number-1])
        if intent['session']!=original or resume_thread!=original['thread_id']:
            raise ValueError('accepted_resume_session_mismatch')
        if schema is None:
            if (frozen.get('native_capabilities',{}).get('response_schema_transport')!='scientific_task'
                    or backends.selected(frozen).schema_transport!='scientific_task'):
                raise ValueError('accepted_resume_schema_mismatch')
            schema=expected_schema
        if schema!=expected_schema or schema.resolve()!=schema:
            raise ValueError('accepted_resume_schema_mismatch')
    elif number and intent.get('continuation_kind')=='author_closeout':
        from scidiscovery.research_loop import author_closeout
        author_closeout.validate_intent(root,job,intent)
    elif number and (intent.get('continuation_kind')!='fragment' or intent.get('context_mode')!='new_context'
            or (frozen.get('worker_fragment_policy') or {}).get('context')!='new_context'):
        raise ValueError('accepted_resume_command_required')
    binding=execution.identity(root,step,number)
    runner=c.read(c.regular(record/'runner-process.json'))
    if runner['pid']!=os.getppid() or not runtime.process_matches(runner,binding):
        raise ValueError('original_runner_dispatch_required')
    # Popen precedes publication of this exact child receipt; wait only for that receipt.
    deadline=time.monotonic()+2
    while not (record/'native-process.json').exists() and time.monotonic()<deadline:time.sleep(.01)
    child=c.read(c.regular(record/'native-process.json'))
    if child['pid']!=os.getpid() or not runtime.process_matches(child,binding):
        raise ValueError('unknown_native_dispatch_cannot_be_replayed')
    return expected_schema

def native_configs(s,w,profile):
    """Exact backend-owned argv values; this namespace supports offline defaults."""
    from scidiscovery.research_loop import backends,contracts as c,configuration_updates
    root=Path(s['scene_root']);job=c.read(c.regular(Path(os.environ['THIN_HWA_JOB_MANIFEST'])))
    frozen=configuration_updates.for_job(root,job)
    backend=backends.selected(frozen)
    if backend.name!='codex':raise ValueError('cov_launcher_requires_codex_backend')
    defaults=backends.configuration('codex',frozen['native_launcher_argv'],{})['native_options']
    if frozen.get('native_options') is not None and frozen['native_options']!=defaults:
        raise ValueError('native_permissions_not_supported_by_cov_launcher')
    instruction=job.get('native_role_instruction',c.ROLE_INSTRUCTIONS[job['role']])
    if job['role']=='A' and not job.get('formal_review'):
        instruction+='\n'+c.AUTHOR_EVIDENCE_GUIDANCE
    if job.get('formal_review'):
        from scidiscovery.research_loop import formal_review
        instruction=formal_review.public(root,job)['review']['instruction']
    declared=backend.launch(profile,w,w/'unused-output',w/'unused-schema',instruction)[len(backend.launcher):]
    configs={declared[i+1].split('=',1)[0]:declared[i+1] for i,value in enumerate(declared) if value=='-c'}
    return configs,'--ignore-user-config' in declared

def native_args(s,w,args):
    # Resume is an exact accepted session, never selection by name or recency.
    if not args or args[0]!='exec':raise ValueError('fresh_native_exec_required')
    profile=native_profile(s)
    expected,ignore_user_config=native_configs(s,w,profile)
    result=['exec'];i=1;seen=set();configs=set();output=None;schema=None;resuming=False;thread=None
    while i<len(args):
        x=args[i]
        if x=='resume':
            if resuming or any(key in seen for key in ('--model','--output-last-message','--output-schema')):
                raise ValueError('accepted_resume_command_shape_required')
            resuming=True;result.append(x);i+=1;continue
        if x in ['--json','--skip-git-repo-check']:
            result.append(x);i+=1;continue
        if x=='--ignore-user-config':
            if not ignore_user_config or x in seen or resuming:raise ValueError('native_config_override_rejected')
            seen.add(x);i+=1
            continue  # The exact generated home owns the named isolation profile.
        if x=='-' and i==len(args)-1:result.append(x);i+=1;continue
        if resuming and not x.startswith('-'):
            import uuid
            if thread is not None:
                raise ValueError('one_exact_resume_thread_required')
            try:
                if str(uuid.UUID(x))!=x:raise ValueError
            except ValueError:raise ValueError('exact_resume_uuid_required') from None
            thread=x;result.append(x);i+=1;continue
        allowed=['--output-last-message','--model','-c','--sandbox','-C']+(['--output-schema'] if resuming else [])
        if x not in allowed or i+1>=len(args) or resuming and x in ['-c','--sandbox','-C']:
            raise ValueError('undeclared_native_argument')
        v=args[i+1];i+=2
        if x in seen and x!='-c':raise ValueError('duplicate_native_argument')
        seen.add(x)
        if x=='--sandbox':
            if v!='workspace-write':raise ValueError('unexpected_legacy_sandbox')
            continue  # Exact legacy flag would override the pinned named deny profile.
        if x=='-C' and Path(v).absolute()!=w:raise ValueError('workspace_argument_mismatch')
        if x=='--model' and v!=profile['model']:raise ValueError('model_profile_mismatch')
        if x=='-c':
            key=v.split('=',1)[0]
            if expected.get(key)!=v or key in configs:
                raise ValueError('native_config_override_rejected')
            configs.add(key)
            if key=='sandbox_workspace_write':continue  # Replaced by the exact rootless profile, like --sandbox.
        if x=='--output-last-message':
            p=Path(v).absolute()
            output=p
        if x=='--output-schema':schema=Path(v).absolute()
        result.extend([x,v])
    if not {'--output-last-message','--model','-c','-C'}.issubset(seen) or result[-1]!='-':
        raise ValueError('complete_stdin_role_command_required')
    if configs!=set(expected) or ('--ignore-user-config' in seen)!=ignore_user_config:
        raise ValueError('complete_role_profile_config_required')
    if '--skip-git-repo-check' not in result:result.insert(1,'--skip-git-repo-check')
    if resuming and thread is None:
        raise ValueError('complete_resume_session_schema_required')
    explicit_schema=schema is not None
    schema=accepted_attempt(s,w,output,resume_thread=thread if resuming else None,schema=schema)
    regular(schema)  # The frozen origin schema is mandatory for every context.
    if not resuming:result[-1:-1]=['--output-schema',str(schema)]
    elif not explicit_schema:result[-2:-2]=['--output-schema',str(schema)]
    return result

def main():
    a=argparse.ArgumentParser();a.add_argument('--config',required=True);a.add_argument('command',nargs=argparse.REMAINDER);q=a.parse_args()
    s=load(q.config);w,h=controlled(s,os.environ['THIN_HWA_WORKSPACE'],os.environ['THIN_HWA_NATIVE_HOME'])
    if os.environ.get('THIN_HWA_ROLE') not in ['H','W','A','E']:raise ValueError('declared_scientific_role_required')
    owner_metadata(s,w)
    args=q.command[1:] if q.command and q.command[0]=='--' else q.command
    import subprocess
    checked=subprocess.run([s['scid_python'],'-B','-I','-c',
        'import sys;from pathlib import Path;from scidiscovery.research_loop import contracts as c;from scidiscovery.research_loop.protocol import require_current;require_current(Path(sys.argv[1]),c.read(c.regular(Path(sys.argv[2]))))',
        s['scene_root'],os.environ['THIN_HWA_JOB_MANIFEST']],capture_output=True,text=True)
    if checked.returncode:raise ValueError('Current scientific contract required before launcher preparation')
    args=native_args(s,w,args)
    from scidiscovery.research_loop import execution,contracts
    job=json.loads(regular(os.environ['THIN_HWA_JOB_MANIFEST']))
    record=execution.record(Path(s['scene_root']),job['step'])
    intent=contracts.read(record/'native-intent.json')
    s['_author_closeout']=intent.get('continuation_kind')=='author_closeout'
    s['_worker_science_view']=job.get('worker_view_version')==1 or intent.get('worker_view_version')==1
    s['_legacy_science_overlay']=s['_author_closeout'] and intent.get('worker_view_version')==1 and job.get('worker_view_version')!=1
    s['_read_only_closeout']=s['_author_closeout'] or job.get('purpose')=='closeout'
    if s['_read_only_closeout']:s['_closeout_record']=str(record)
    if s['_author_closeout'] and intent.get('author_context_version')==1:
        from scidiscovery.research_loop import author_closeout
        s['_author_science_context']=str(author_closeout.validate_science_context(
            Path(s['scene_root']),job,intent['author_closeout_science_context']))
        context=contracts.read(Path(s['scene_root'])/intent['author_closeout_science_context']['path'])
        s['_author_artifact_masks']=[(str(Path(s['scene_root'])/item['display']['path']),
            str(Path(s['scene_root'])/item['original']['path'])) for item in context.get('artifact_masks',[])]
    facts=record/('author-closeout-science.json' if intent.get('worker_view_version')==1 else 'author-closeout-facts.json')
    if s['_author_closeout'] and intent.get('worker_view_version')==1:
        facts=Path(s['scene_root'])/intent['author_closeout_science_facts']['path']
    if s['_author_closeout'] and facts.exists():
        if s['_worker_science_view']:
            target=w/'work/author-closeout-science.json'
            contracts._copy_bound(facts,target,contracts.digest(facts))
            s['_closeout_facts']=str(target)
        else:s['_closeout_facts']=str(facts)
    materials=direct_materials(s,w,os.environ.get('THIN_HWA_JOB_MANIFEST'));prepare_home(s,w,h,os.environ.get('THIN_HWA_AUTH_SOURCE'))
    # Native shares its current job and settings identity read-only; other host
    # jobs, settings, owner anchors and ledgers are not mounted.
    from cov_execution.lineage import configure
    job=json.loads(regular(os.environ['THIN_HWA_JOB_MANIFEST']))
    configured=(w/'work/lineage-config.json' if job.get('worker_view_version')==1 else
        Path(s['scene_root'])/'host-runtime'/('lineage-'+w.name+'.json'))
    if s['_author_closeout']:
        regular(w/'work/lineage-config.json')
        if job.get('worker_view_version')==1:
            from scidiscovery.research_loop import experiment_client
            experiment_client.resolve(configured,json.loads(regular(configured)))
        elif regular(configured)!=regular(execution.origin(Path(s['scene_root']),job['step'])/'experiment-config.json'):
            raise ValueError('author_closeout_original_projection_changed')
    if not s['_author_closeout']:
        backend=str(Path(s['public_root'])/'environment/harness/exp_cli.py') if s['_worker_science_view'] else '/opt/exp/exp_cli.py'
        configure(Path(s['scene_root']),job['step'],[s['task_python'],'-B','-I',backend],Path(s['runs_dir']),configured,
            receipt_channel=s.get('cov_receipt_channel','public_runner_main'),
            engineering_check_reserve_seconds=engineering_reserve(s))
    print(engineering_notice(s),file=sys.stderr,flush=True)
    if not s['_author_closeout']:(w/'work/lineage-config.json').touch(exist_ok=True)
    if not s['_worker_science_view']:
        command=outer(s,w,h,[s['native'],*args],materials);os.execv(command[0],command)
    # Public native transport files contain scientific schema/result bytes only.
    # Full dispatch/usage/session/config originals remain outside this namespace.
    public_result=Path(s['scene_root'])/'host-runtime/science-native-results'/w.name/f'{intent.get("attempt",0):03d}'
    public_result.mkdir(parents=True,exist_ok=True)
    schema=execution.origin(Path(s['scene_root']),job['step'])/'science-result.schema.json'
    contracts._copy_bound(schema,public_result/'science-result.schema.json',contracts.digest(schema))
    pending=record/'result.pending.json'
    args=list(args)
    args[args.index('--output-last-message')+1]='/task-result/result.pending.json'
    args[args.index('--output-schema')+1]='/task-result/science-result.schema.json'
    s['_public_result']=str(public_result)
    from scidiscovery.research_loop import worker_execution_bridge
    from contextlib import nullcontext
    endpoint=w/SUBMISSION_DIRECTORY
    harness=Path(s['public_root'])/'environment/harness'
    host_environment={key:str(harness/name) for key,name in
        [('EXP_RUNNER','runner.py'),('EXP_TRAIN','train.csv'),('EXP_VAL','val.csv'),('EXP_META','meta.json')]}
    service=(nullcontext() if s['_read_only_closeout'] else worker_execution_bridge.serve(endpoint,configured,w,
        launch=backend_launcher(s,w,h,materials),environment=host_environment))
    with service:
        command=outer(s,w,h,[s['native'],*args],materials)
        child=subprocess.Popen(command)
        import signal
        def stop(signum,frame):raise SystemExit(128+signum)
        previous={sig:signal.signal(sig,stop) for sig in (signal.SIGTERM,signal.SIGINT)}
        try:code=child.wait()
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
            for sig,handler in previous.items():signal.signal(sig,handler)
        returned=public_result/'result.pending.json'
        if returned.exists():contracts._copy_bound(returned,pending,contracts.digest(returned))
    raise SystemExit(code)

if __name__=='__main__':main()
