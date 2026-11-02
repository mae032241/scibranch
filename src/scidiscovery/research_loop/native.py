"""One fresh native process per scientific role; no model scheduler.

An explicit launcher owns native permissions and functional MCP configuration.
The controller never constructs MCP agent calls. Only explicit qualified continuation
can continue the exact original native thread.
"""
from __future__ import annotations

import json
import errno
from contextlib import ExitStack
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time

from . import configuration_updates
from . import budget as control_budget, execution, runtime, search, checkpoint, profiles, backends, native_recovery
from .output_repair import OutputRepairRequired
from .native_usage import terminal_attempt_tokens
from .guidance import STAGE_PLANNING
from .contracts import (ACTIONS, actions, ROLE_INSTRUCTIONS, record_ref, file_ref, host_record, read,
                        seal_result, workspace, write, NATIVE_TASK_INSTRUCTION, decode_scientific_output,
                        native_outcome)


RESUME_INSTRUCTION = 'Resume same task/scope/permissions/budget; no accepted unknown-call replay.'
OUTPUT_REPAIR_INSTRUCTION = 'Fix diagnosed JSON/layout only; return full JSON preserving other science/plans/limits. No work/rerun/resubmit this turn; future W authorization unchanged. No prior experiment implied: '
WORK_REPAIR_INSTRUCTION = 'Complete missing assigned work under original authorization; reuse results; never replay accepted unknown calls. Report obstacles/limits. Diagnosis: '
FRAGMENT_INSTRUCTION = 'Read work/science-resume.json; continue per stage_response.'


def render_scientific_request(job, task, intent, *, backend_name, instruction_transport,
        budget_status=None, remaining=None, execution_interface=None, managed_command=None,
        output_only_wake=False, author=None, final_return=False):
    """Pure rendering of the complete request; no later response-format appends."""
    from . import contracts as c
    formal=bool(job.get('formal_review'))
    schema=c.result_schema(job['role'],job.get('handoff_protocol',c.HANDOFF_PROTOCOL),
        'selection' in job,**c.response_options(job))
    instruction=job.get('native_role_instruction',ROLE_INSTRUCTIONS[job['role']])
    if formal:
        instruction=task['review_instruction']
        prompt=('Read science-task.json and exact reviewed inputs. Assess evidence independently before requester explanations. '
            'Do not edit the subject. Return one JSON object with the four final_response verdict/findings fields. '
            'A pass grants no execution permission; reading is not blinded or isolated.')
    else:
        if job['role']=='A':instruction+='\n'+c.AUTHOR_EVIDENCE_GUIDANCE
        rules=c.response_instructions(job)
        if all(task.get(key)==value for key,value in rules.items()):
            format_rule=''
        else:
            format_rule=('Current response rules; scope binds: '
                +json.dumps(rules,ensure_ascii=False,separators=(',',':')))
        if backend_name=='codex':
            properties=schema['properties']
            text_fields=[name for name,rule in properties.items() if rule.get('type')=='string' and 'enum' not in rule]
            menu=['/'.join(text_fields)+':text','limitations:text[]']
            menu.extend(name+':'+ '|'.join(rule['enum']) for name,rule in properties.items() if 'enum' in rule)
            format_rule+='\nRequired: '+'; '.join(menu)
        # The same developer layer follows the complete frozen scientific literal.
        instruction+='\n'+format_rule
        prompt=('Read science-task.json/inputs; JSON only. '
            'Use declared writable paths; no control wrappers/hashes. '
            'work/budget-status.json: stale/partial, no grant; unknown tokens!=0; input has cache.')
        if final_return:
            prompt+=(' Protected FINAL: conclude now; no new role/audit/experiment. '
                'Reference token/time/step ceilings do not revoke this accepted return; original call limits, '
                'permissions and delivery qualification still apply.')
        else:
            prompt+=' Soft targets; call limits/permissions bind.'
            if (job['role'] in {'A','H','W'} and not author
                    and not output_only_wake and not intent.get('output_repair') and not intent.get('work_repair')
                    and job.get('purpose')!='closeout' and not job.get('final_report') and not job.get('final_only')):
                prompt+=' Close with unmet goals if short. '+STAGE_PLANNING[job['role']]
                if job['role']=='W':prompt+=' Prefer 10-30s waits on pending originals within deadlines.'
    if budget_status:prompt+='\n'+json.dumps(budget_status,ensure_ascii=False,separators=(',',':'))
    if execution_interface is not None and not author and not output_only_wake:
        prompt+='\nManaged execution: '+json.dumps(dict(command=managed_command,
            lifecycle=execution_interface['capabilities'],result_protocol=execution_interface['result_protocol']),
            ensure_ascii=False,separators=(',',':'))
        prompt+=(' Read capabilities/describe; original IDs; never replay accepted unknown calls. '
            'Freeze code/ on capture; outputs/logs elsewhere.')
    if job['role']=='W' and ('managed_execution_wait' in job or execution_interface is not None or intent.get('execution_wake')):
        prompt+=' handoff.action: finalize_response=JSON; return_control=wait.'
    if job['role']=='W' and not output_only_wake and not author:
        prompt+=' engineering_check: sample, no training/audit.'
    if remaining is not None:
        prompt+='\n'+json.dumps(remaining,separators=(',',':'))
        if not intent.get('output_repair') and not intent.get('work_repair'):
            prompt+='\n'+RESUME_INSTRUCTION
    if intent.get('output_repair') or intent.get('work_repair'):
        request=intent.get('work_repair') or intent['output_repair']
        prompt+='\n'+(WORK_REPAIR_INSTRUCTION if intent.get('work_repair') else OUTPUT_REPAIR_INSTRUCTION)
        prompt+=json.dumps({key:request[key] for key in ('code','diagnostic')},ensure_ascii=False,separators=(',',':'))
    if intent.get('execution_wake'):
        prompt+=' Read work/execution-results.json and original outputs; interpret without rerun/resubmission.'
        if output_only_wake:prompt+=' Output-only: no experiment/training/fragment/work repair.'
    if intent.get('continuation_kind')=='fragment':prompt+='\n'+FRAGMENT_INSTRUCTION
    if author:
        prompt+=(' Author-only closeout: read '+author['facts']+'. Completed originals only; '
            'no experiment/training/refinement/submission/replay/backend query/collection. '
            'Original calls are read-only; stage_intent=review/decision, never continue. '
            'Assess original goals, evidence and limitations; write only needed artifacts/. ')
        prompt+=json.dumps(author['limits'],separators=(',',':'))
    if instruction_transport=='prompt':prompt=instruction+'\n'+prompt
    return prompt,instruction,schema



def usage(root: Path, *, budget_events=None):
    """Sum distinct node meters, never re-add cumulative observations."""
    from . import accepted_budget
    return accepted_budget.usage(root, head=budget_events.state if budget_events is not None else None)

def _complete_reported_tokens(root,step):
    """Strict advisory sample: missing endpoint/coverage never contributes zero."""
    from . import contracts as c
    total=0;accepted=0
    for record in execution.records(root,step):
        if not (record/'native-intent.json').exists():continue
        accepted+=1
        intent=c.read(c.regular(record/'native-intent.json'))
        if intent.get('task_ref')!=c.record_ref(c.regular(Path(root)/'jobs'/f'{step:03d}.json')):
            raise ValueError('Advisory sample task identity changed')
        if not (record/'native-outcome.json').exists():return None,'current_usage_inflight'
        outcome=c.native_outcome(record)
        if type(outcome.get('returncode')) is not int:return None,'terminal_endpoint_unavailable'
        if not (record/'usage.json').exists():return None,'meter_unavailable'
        value=c.read(c.regular(record/'usage.json'))
        if _usage_event(dict(type='turn.completed',usage=value))!=value:return None,'meter_unavailable'
        metadata=outcome.get('metering')
        if metadata is None and (record/'usage-observation.json').exists():metadata=c.read(c.regular(record/'usage-observation.json'))
        if not isinstance(metadata,dict) or metadata.get('coverage')!='terminal':return None,'terminal_coverage_unavailable'
        # Present native session counters explicitly leave delay unmeasured.
        # Do not upgrade that lower bound to a complete sample for a number.
        if metadata.get('reporting_delay_unmeasured') is not False or metadata.get('inflight_usage_unknown') is not False:
            return None,'reporting_delay_unmeasured'
        total+=value['input_tokens']+value['output_tokens']
    return (total,None) if accepted else (None,'accepted_attempts_unavailable')


def cycle_token_advice(root,state,*,usage_snapshot=None,budget_snapshot=None):
    """Reported-token reference only, independent of admission and shared clocks."""
    from . import accepted_budget
    head = accepted_budget.read(root)
    advice = head['search']['portfolio']['token_advice']
    samples = [dict(w_step=row['w_step'], a_steps=[row['a_step']],
        science_refs=[dict(source_step=step,pointer='/scientific_result') for step in (row['w_step'],row['a_step'])],
        reported_tokens=row['reported_tokens']) for row in advice['samples']]
    reasons = []
    if advice['complete_sample_count'] < 2:reasons.append('insufficient_complete_local_cycles')
    current=usage(root) if usage_snapshot is None else usage_snapshot
    limits=control_budget.snapshot(root) if budget_snapshot is None else budget_snapshot
    if current.get('blocking_unknown_count', bool(current.get('unknown_steps'))) or current.get('reserved_unknown_steps'):
        reasons.append('usage_unknown')
    spent=current.get('accounted_tokens',current.get('observed_tokens'));ceiling=limits.get('max_tokens')
    remaining=max(0,ceiling-spent) if type(ceiling) is int and type(spent) is int and spent>=0 else None
    if remaining is None:reasons.append('current_balance_unavailable')
    mean=advice['reported_tokens_sum']/advice['complete_sample_count'] if advice['complete_sample_count'] else None
    if mean is not None and mean<=0:reasons.append('nonpositive_sample_mean')
    reasons=list(dict.fromkeys(reasons));available=not reasons
    return dict(accounted_tokens=spent,max_tokens=ceiling,accepted_attempt_count=head['dispatch_count'],status='available' if available else 'unavailable',reference_cycles=remaining/mean if available else None,
        minimum_samples=2,complete_sample_count=advice['complete_sample_count'],excluded_sample_count=advice['excluded_sample_count'],reasons=reasons,
        mean_reported_tokens=mean,remaining_reported_tokens=remaining,samples=samples,excluded_samples=[],
        scope='Reported native W/A tokens in complete child-local cycles only; inherited costs, H/E/audit/external calls and shared time are excluded from sample averages. Remaining balance includes current local usage. Not an execution grant or guarantee; existing time, steps and FINAL limits apply.')

def _usage_event(event):
    if event.get('type') != 'turn.completed':
        return None
    value = event.get('usage')
    if not isinstance(value, dict) or any(type(value.get(k)) is not int or value[k] < 0
                                          for k in ('input_tokens', 'output_tokens')):
        return None
    cached = value.get('cached_input_tokens', 0)
    if type(cached) is not int or not 0 <= cached <= value['input_tokens']:
        return None
    return dict(input_tokens=value['input_tokens'], cached_input_tokens=cached, output_tokens=value['output_tokens'])


def execute_native(root: Path, step: int, *, final_report=None):
    """Submit once, then wait for the same durable local runner."""
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    settings = configuration_updates.for_step(root, step)
    record = execution.record(root, step)
    record.mkdir(parents=True, exist_ok=True)
    job_path = root / "jobs" / f"{step:03d}.json"
    producer_sources={}
    job = _contracts.read_job(job_path,sources=producer_sources)
    profile_binding = profiles.binding(settings, job)
    from . import skill_supply
    skill_supply.project(root, settings, job)
    node_anchor = control_budget.accept_node(root, job, final_report=final_report)
    if not control_budget.task_step_available(root, step) and not (node_anchor or {}).get('final_return'):
        raise control_budget.NodeBudgetUnavailable('step_limit')
    if node_anchor is not None and job['role'] == 'W' and search.launch_reason(root, job):
        raise ValueError('W dispatch violates its frozen search authorization')
    anchor = search.accept(root, job) if node_anchor is None else None
    with (record / "native-intent.json").open("x") as stream:
        json.dump(dict(step=step, task_ref=record_ref(job_path), prepared_at=time.time(), **profile_binding,
                       **(dict(search_slice=anchor) if anchor is not None else {}),
                       **(dict(budget_slice=node_anchor) if node_anchor is not None else {})), stream)
        stream.flush()
        os.fsync(stream.fileno())
    from . import accepted_budget
    accepted_budget.accept_native(root,step,record,read(record/'native-intent.json'))
    runtime.start(root, step)
    return wait_native(root, step)


def wait_native(root: Path, step: int, *, _reentry_started=False, inventory=None):
    """Completion polling is transport; only the original outcome can seal."""
    record = execution.record(root, step)
    startup_until = time.monotonic() + 5
    reentry_started = _reentry_started
    while True:
        from . import managed_wait
        if (record/managed_wait.NAME).exists() and not (execution.origin(root,step)/'result-identity.json').exists():
            managed_wait.wait_and_wake(root,step)
            record=execution.record(root,step);startup_until=time.monotonic()+5
            inventory=None
            continue
        state = runtime.node_state(root, step)
        if state == 'unstarted' and read(record / 'native-intent.json').get('continuation_kind') in {'reentry','automatic_recovery'} and not reentry_started:
            runtime.start(root, step, read(record / 'native-intent.json')['attempt'])
            reentry_started = True
            startup_until = time.monotonic() + 5
            continue
        if state == 'interrupted':
            from . import continuation
            continuation.reenter(root, step)
            record = execution.record(root, step)
            inventory=None
            reentry_started = True
            startup_until = time.monotonic() + 5
            continue
        if state in {"terminal", "sealed"}:
            from . import continuation, worker_delivery
            job = read(root / 'jobs' / f'{step:03d}.json')
            fragments = checkpoint.enabled(root, job)
            if state == 'terminal':
                # Outcome publication precedes the runner's final writes. Wait
                # for this exact owner before sealing or returning control.
                runner = record / 'runner-process.json'
                if runner.exists() and runtime.process_matches(read(runner), execution.identity(root, step)):
                    time.sleep(.1)
                    continue
                refused = control_budget.prelaunch_budget_reason(root, step, record)
                if refused is not None:
                    raise control_budget.NodeBudgetUnavailable(refused)
                from . import contracts as c
                if inventory is None:inventory=c.accept_current_inventory(root,step)
                elif inventory.root!=root or c.job_record(inventory.job)!=job or inventory.record!=record:
                    raise ValueError('Current inventory belongs to another task or attempt')
                decision = continuation.automatic_decision(root, step,inventory=inventory)
                if decision['action'] in {'continue','retry'}:
                    raise continuation.AutomaticRecoveryRequired(decision,inventory=inventory)
            try:result = continuation.seal_or_repair(root, step,inventory=inventory)
            except managed_wait.ExecutionWaiting:
                continue
            if result.get('fragment_continuation') is True:
                record = execution.record(root, step)
                inventory=None
                startup_until = time.monotonic() + 5
                continue
            return result
        if state != "running":
            if state in {'unknown','unstarted'} and time.monotonic() < startup_until:
                # A probe can race original receipt publication. Re-probe only
                # this accepted execution in the existing startup window; a
                # second file read cannot turn the earlier unknown into proof
                # of a bad identity. Waiting never permits another submission.
                time.sleep(.05)
                continue
            # The exact owner can publish while node_state checks receipts.
            # Collect that same outcome through the normal terminal checks;
            # this late publication never authorizes another launch.
            if (record / 'native-outcome.json').exists():
                continue
            raise RuntimeError("Original native execution is unknown; preserved without replay")
        time.sleep(.1)


def _shared_view_context(root, job, intent, limits, *, final_return=None, author_permit=None):
    """Finite mechanical preparation, bounded only by existing hard clocks.

    Shared balance and ordinary node targets are soft for accepted work; they
    must not become a fresh readonly preparation deadline.
    """
    from . import shared_input_views, storage_policy
    seconds=shared_input_views.ALIAS_SECONDS+10
    if final_return is not None:
        policy=control_budget.node_limits(root,job)
        seconds=min(seconds,policy['max_seconds']-control_budget._node_duration(root,job,intent,limits))
    if author_permit is not None:
        elapsed=control_budget._node_duration(root,job,intent,limits)-intent['budget_slice']['previous_seconds']
        seconds=min(seconds,author_permit['max_seconds']-elapsed)
    if seconds<=0:raise TimeoutError('Original native hard deadline reached before readonly preparation')
    return storage_policy.Deadline(seconds)


def _cleanup_code_exports(root, exports, *, launch_started, process, cleanup, bridge_stopped):
    """Only this runner's selected exports, after every actual reader stopped."""
    if not bridge_stopped or (launch_started and (process is None or cleanup != 'observed_group_cleaned')):
        return
    from . import code_versions, experiments as e
    for export_step, call_id in dict.fromkeys(exports):
        try:
            code_versions.cleanup_export(root, step=export_step, call_id=call_id)
        except (OSError, ValueError) as error:
            e._diagnostic(error, 'Native context code export cleanup failed', config=None)


def _budget_notice_launch(backend, args, work, attempt, env, view_facts, context, *, diagnostic=None):
    """Qualify only this internal hook in the exact final native namespace."""
    from . import budget_notice, shared_input_views
    channel=dict(status='unavailable',reason='hook_preflight_unavailable')
    def report(error):
        if diagnostic is not None:
            message=(type(error).__name__+': '+str(error)).encode('utf-8',errors='replace')[:1024]
            diagnostic(b'Budget notice preflight: '+message+b'\n')
    try:
        helper=Path(work)/'work'/budget_notice.HELPER
        view_facts['mounts']['mappings'][str(helper)]=helper
        check=shared_input_views.wrap_from_facts(dict(view_facts,worker_policy=None),[sys.executable,str(helper),
            '--work',str(work),'--attempt',str(attempt),'--check'])
        with shared_input_views.popen_arguments(check) as (argv,pass_fds):
            visible=subprocess.run(argv,cwd=work,env=env,stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,pass_fds=pass_fds,
                timeout=min(3,context.remaining_seconds()))
        if visible.returncode or visible.stdout!=budget_notice.VISIBLE.encode()+b'\n':
            channel['reason']='helper_unavailable'
            report(ValueError('Helper visibility check failed; returncode='+str(visible.returncode)))
            return shared_input_views.wrap_from_facts(view_facts,args),channel
        declared=budget_notice.definition(work,attempt)
        candidate=backend.budget_hook_args(args,declared)
        metadata_facts=dict(view_facts,mounts=dict(view_facts['mounts'],
            mappings=dict(view_facts['mounts']['mappings'])))
        if '--ignore-user-config' in args:
            native_home=Path(env.get('CODEX_HOME',str(Path.home()/'.codex')))
            if not native_home.is_absolute():native_home=Path(work)/native_home
            native_config=native_home/'config.toml'
            # A missing config already has the same semantics. Do not create
            # a mountpoint in the inherited native home for an absent file.
            try:config_info=native_config.lstat()
            except FileNotFoundError:config_info=None
            if config_info is not None and not stat.S_ISREG(config_info.st_mode):
                report(ValueError('Metadata projection requires a directly ordinary native config file'))
                return shared_input_views.wrap_from_facts(view_facts,args),channel
            if config_info is not None:
                metadata_facts['mounts']['mappings'][str(native_config)]=Path(work)/'work'/budget_notice.EMPTY_CONFIG
        inventories=[]
        for invocation in (args,candidate):
            metadata=shared_input_views.wrap_from_facts(metadata_facts,backend.hook_inventory_args(invocation,work))
            with shared_input_views.popen_arguments(metadata) as (argv,pass_fds):
                inventories.append(budget_notice.list_hooks(argv,work,env,pass_fds=pass_fds,
                    timeout=min(5,context.remaining_seconds())))
        try:budget_notice.validate_hook_trust(*inventories,declared,work)
        except ValueError as error:
            channel['reason']='hook_trust_unqualified'
            report(error)
            return shared_input_views.wrap_from_facts(view_facts,args),channel
        context.remaining_seconds()
        return shared_input_views.wrap_from_facts(view_facts,backend.budget_hook_args(args,declared,trusted=True)),dict(
            status='available',mechanism='codex_post_tool_use')
    except (OSError,ValueError,TypeError,KeyError,RecursionError,subprocess.TimeoutExpired) as error:
        report(error)
        return shared_input_views.wrap_from_facts(view_facts,args),channel


def run_native(root: Path, step: int, attempt=0):
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    settings = configuration_updates.for_step(root, step)
    work = workspace(root, step)
    job_path = root / 'jobs' / f'{step:03d}.json'
    producer_sources={}
    job = _contracts.read_job(job_path,sources=producer_sources)
    record = execution.record(root, step, attempt)
    record.mkdir(parents=True, exist_ok=True)
    intent = read(record / 'native-intent.json')
    final_return = control_budget.final_a_return(root, job, intent)
    if intent.get('task_ref') != record_ref(job_path):
        raise ValueError('Native execution task changed')
    profile = profiles.validate_intent(settings, job, intent)
    from . import skill_supply
    skill_supply.project(root, settings, job)
    namespace_launcher=skill_supply.namespace_launcher(settings)
    if job.get('experiment_mode') != settings.get('experiment_mode'):
        raise ValueError('Frozen experiment mode changed')
    experiment_config=None
    if job['role']=='W' and job.get('experiment_mode')=='local':
        from . import experiments
        selected=settings.get('execution_config')
        if selected is None:  # Exact pre-selection instances retain the original default.
            executor='local_execution:command'
            configuration=dict(run_argv=[sys.executable],max_seconds=min(65,settings['max_seconds']),cwd=str(work))
        else:
            experiments.validate_operator_configuration(selected)
            executor=selected['executor'];configuration=selected['configuration']
        experiment_config=experiments.configure(root,step,executor,configuration,work/'work/experiment-config.json')
    if attempt:
        profiles.validate_intent(settings, job, read(execution.origin(root, step) / 'native-intent.json'))
    # W statistics reuse this exact node anchor, never a second acceptance clock.
    search_intent = dict(intent, search_slice=intent['budget_slice']) if intent.get('budget_slice') is not None and job.get('search') and job['role'] == 'W' else intent
    pending = record / 'result.pending.json'
    schema_path = execution.origin(root, step) / 'science-result.schema.json'
    backend = backends.selected(settings, schema_transport=execution.schema_transport(root, step, attempt))
    if backend.standard and not job.get('formal_review'):
        backend = backends.selected(settings, schema_transport='prompt')
    standard_native = backend.standard
    notice_channel=dict(status='unavailable',reason=('unsupported_backend' if not standard_native else
        'undeclared_native_permissions' if settings.get('native_options') is None else
        'unsupported_namespace' if namespace_launcher is not None else 'not_preflighted'))
    notice_helper=None
    resume_position = None
    resume_session=None
    if settings.get('resume_policy') is not None:
        from . import continuation
        continuation.validate_launcher(root, step)
    startup_retry = bool((intent.get('automatic_recovery') or {}).get('startup_retry'))
    if intent.get('continuation_kind') == 'automatic_recovery':
        from . import continuation
        continuation.validate_automatic(root, step, intent)
    if attempt and intent.get('context_mode') != 'new_context' and not startup_retry:
        if not backend.supports_resume:
            from .continuation import ResumeUnavailable
            raise ResumeUnavailable('worker_resume_unsupported_backend')
        from .contracts import regular
        continuation.validate_repair(root, step, intent)
        session = intent['session']
        if (session.get('home') != str(execution.home(root, step, attempt))
                or session.get('workspace') != str(work)
                or Path(session['path']).resolve() != Path(session['path'])
                or not Path(session['path']).is_relative_to(Path(session['home']) / 'sessions')):
            raise ValueError('Original native session binding changed')
        with regular(Path(session['path'])).open('rb') as stream:
            header = stream.readline()
        continuation.validate_session_header(header, session['thread_id'], work)
        resume_session=session
    (work / 'artifacts').mkdir(exist_ok=True)
    (work / 'work').mkdir(exist_ok=True)
    budget = usage(root)
    limits = control_budget.snapshot(root, settings)
    task=read(work/'science-task.json')
    if job.get('formal_review'):
        from . import formal_review
        task=dict(task,review_instruction=formal_review.public(root,job)['review']['instruction'])
    status=dict(shared_seconds_remaining=max(0,int(limits['seconds_remaining'])),
        shared_observed_tokens_remaining=max(0,limits['max_tokens']-budget.get('accounted_tokens',budget['observed_tokens'])))
    if intent.get('budget_slice'):status['node_budget']=intent['budget_slice']['limits']
    remaining=None
    if attempt:
        remaining=dict(remaining_observed_tokens=max(0,intent['budget_slice']['limits']['max_observed_tokens']-intent['budget_slice']['previous_tokens']),
            remaining_active_seconds=max(0.,intent['budget_slice']['limits']['max_seconds']-control_budget._node_duration(root,job,intent,limits)),
            mode='soft_targets',previous_consumed_observed_tokens=intent['budget_slice']['previous_tokens'])
    command=None;interface=None
    if experiment_config is not None:
        interface=experiment_config['execution_interface']
        if job.get('worker_view_version')==1:
            from .worker_execution_bridge import SUBMISSION_DIRECTORY
            command=[sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge','--submission-directory',SUBMISSION_DIRECTORY]
        else:command=[sys.executable,'-m','scidiscovery.research_loop.experiments','--config','work/experiment-config.json']
    output_only_wake=bool(intent.get('execution_wake') and (settings.get('resume_policy') or {}).get('output_only'))
    if intent.get('execution_wake'):
        from . import managed_wait,continuation
        continuation.validate_wake(root,step,intent,execution.record(root,step,attempt-1))
        managed_wait.wake_packet(root,job,intent)
    if intent.get('continuation_kind')=='fragment':
        prior=execution.record(root,step,attempt-1)
        if intent['fragment_checkpoint']['path']!=(prior/'checkpoint.json').relative_to(root).as_posix():
            raise ValueError('Native fragment checkpoint changed before launch')
        write(work/'work/science-resume.json',checkpoint.brief(root,job,prior))
    author_limits=None;author=None
    author_context_facts=None
    if intent.get('continuation_kind')=='author_closeout':
        from . import author_closeout
        permit,author_context_facts=author_closeout.load_intent_eligibility(root,job,intent)
        author_limits=dict(max_observed_tokens=permit['max_tokens'],max_seconds=permit['max_seconds'])
        author=dict(facts='work/author-closeout-science.json' if intent.get('worker_view_version')==1 else ('author-closeout-facts.json' if attempt==2 else 'work/author-closeout-facts.json'),limits=author_limits)
    mode=control_budget.node_mode(root)
    if (final_return is None and job.get('purpose') is None and not job.get('formal_review')
            and intent.get('continuation_kind')!='author_closeout'):
        available=control_budget.execution_available(root,job,limits,budget)
        status['exploration']=dict(observed_tokens_remaining_upper_bound=available['tokens'],
            active_seconds_available=available['seconds'],token_usage_unknown=bool(budget.get('unknown_steps')))
    prompt,role_instruction,response_schema=render_scientific_request(job,task,intent,backend_name=backend.name,
        instruction_transport=backend.instruction_transport,budget_status=status,remaining=remaining,
        execution_interface=interface,managed_command=command,output_only_wake=output_only_wake,
        author=author,final_return=final_return is not None)
    args=backend.launch(profile,work,pending,schema_path,role_instruction)
    if resume_session is not None:
        args,resume_position=backend.resume(args,profile,work,pending,schema_path,resume_session)
    home = execution.home(root, step, attempt)
    home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'THIN_HWA_ROLE': job['role'], 'THIN_HWA_WORKSPACE': str(work),
           'THIN_HWA_NATIVE_HOME': str(home), 'THIN_HWA_JOB_MANIFEST': str(job_path)}
    if settings.get('resume_policy') is not None and backend.name == 'codex':env['CODEX_HOME'] = str(home)
    meter_home = home
    if settings.get('resume_policy') is None and standard_native:
        # Raw Codex keeps its inherited credentials/configuration and writes its
        # rollout into that same actual home, not the stage's launcher namespace.
        meter_home = Path(env['CODEX_HOME']) if env.get('CODEX_HOME') else Path.home() / '.codex'
        if not meter_home.is_absolute():meter_home = work / meter_home
        meter_home = meter_home.resolve()
    process = None
    process_id = None
    stop = None
    error = None
    pending_line = b''
    meter = dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    observed = False
    terminal_observed = False
    terminal_fault = None
    completed_before = None
    terminal_usage_observed = False
    terminal_usage_missing = False
    terminal_meter = dict(meter)
    live_meter = backend.meter(meter_home, work, session=intent.get('session') if attempt and intent.get('context_mode') != 'new_context' and not startup_retry else None)
    cleanup = None
    thread_id = (intent.get('session') or {}).get('thread_id') if attempt and not startup_retry else None
    native_failures = []
    native_activity_observed = False
    scientific_lifetime = ExitStack()
    code_exports = []
    launch_started = False
    bridge_stopped = False
    try:
        with (record / 'native.jsonl').open('wb') as log, (record / 'native.stderr').open('wb') as err, (record / 'native-public.jsonl').open('w') as public_log:
            # Submission happens here, after runner/watchdog startup. Recheck
            # all shared limits; this fresh intent alone is not missing usage.
            latest = usage(root)
            limits = control_budget.snapshot(root, settings)
            stop = ('wall_limit' if final_return is None and limits['seconds_remaining'] <= 0 else
                    'usage_unknown' if control_budget.final_a_usage_unknown(final_return, latest, ignore_step=step) else
                    'token_limit' if final_return is None and latest.get('accounted_tokens',latest['observed_tokens']) >= limits['max_tokens'] else None)
            stop = stop or control_budget.node_admission(root, job, ignore_step=step, intent=intent)
            stop = stop or control_budget.stop_node(root, job, intent, limits, meter)
            stop = stop or search.stop_reason(root, job, search_intent, limits, meter)
            if stop:
                write(record / 'usage.json', dict(input_tokens=0, cached_input_tokens=0, output_tokens=0))
                write(record / 'native-outcome.json', dict(returncode=None, stop_reason=stop,
                    native_started=False, usage=usage(root), thread_id=None,
                    limitation='Budget stopped the original intent before native submission.'))
                return
            if settings.get('resume_policy') is not None:
                continuation.validate_task(root, step)
            control_budget.write_node_hint(root,job,intent,limits,meter,observed=observed,
                author_limits=author_limits,notice_channel=notice_channel)
            next_hint=time.monotonic()+5
            hint_dirty=False
            if experiment_config is not None and job.get('worker_view_version') == 1:
                from . import native_execution, worker_execution_bridge
                actual_home=Path(env.get('CODEX_HOME',str(Path.home()/'.codex')))
                if not actual_home.is_absolute():actual_home=work/actual_home
                args,launch=native_execution.prepare(root,step,record,work,actual_home.resolve(),args,
                    standard=standard_native,resume_position=resume_position,native_options=settings.get('native_options'),
                    job=job,intent=intent,settings=settings)
                scientific_lifetime.enter_context(worker_execution_bridge.serve(
                    work/worker_execution_bridge.SUBMISSION_DIRECTORY,work/'work/experiment-config.json',work,
                    launch=launch))
            from . import shared_input_views
            if notice_channel['reason']=='not_preflighted':
                from . import budget_notice
                try:notice_helper=budget_notice.stage_helper(work)
                except (OSError,ValueError):notice_channel['reason']='helper_unavailable'
            limits=control_budget.snapshot(root,settings)
            view_context=_shared_view_context(root,job,intent,limits,final_return=final_return,
                author_permit=permit if intent.get('continuation_kind')=='author_closeout' else None)
            view_sources=shared_input_views.load_source_facts(root,job,work,
                native=dict(record=record,intent=intent),policy=settings,
                include_wake=namespace_launcher is None,author_facts=author_context_facts,
                material_sources=producer_sources,
                input_policy=author_context_facts['input_policy'] if author_context_facts is not None else None)
            selected_code=shared_input_views.materialize_selected_code(root,
                shared_input_views.selected_code_from_facts(view_sources),context=view_context,exports=code_exports)
            if namespace_launcher is None:
                view_facts=shared_input_views.load_wrap_facts(root,job,work,context=view_context,
                    selected_code=selected_code,source_facts=view_sources,policy=settings)
                if notice_helper is not None:
                    def notice_diagnostic(message):
                        err.write(message);err.flush()
                    args,notice_channel=_budget_notice_launch(backend,args,work,attempt,env,view_facts,view_context,
                        diagnostic=notice_diagnostic)
                    view_context.remaining_seconds()
                else:args=shared_input_views.wrap_from_facts(view_facts,args)
            else:
                # Registered host preparation/bridge stays in its host view.
                # Its frozen module owns the final scientific namespace seam.
                shared_input_views.ensure_capability(context=view_context)
            if notice_helper is not None:
                limits=control_budget.snapshot(root,settings)
                control_budget.write_node_hint(root,job,intent,limits,meter,observed=observed,
                    author_limits=author_limits,notice_channel=notice_channel)
            launch_started = True
            with shared_input_views.popen_arguments(args) as (launch_args, pass_fds):
                process = subprocess.Popen(launch_args, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=err, env=env, start_new_session=True, pass_fds=pass_fds)
            process_id = runtime.process_identity(process.pid, execution.identity(root, step, attempt))
            write(record / "native-process.json", process_id)
            request = memoryview(backend.request(prompt, profile=profile, work=work, schema=schema_path,formal_review=bool(job.get('formal_review')),response_schema=response_schema))
            # Startup events can fill stdout before the launcher consumes its
            # request. Keep both directions in the same interruptible loop so
            # the existing finite clocks still run while stdin is backpressured.
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if request:
                    selector.register(process.stdin, selectors.EVENT_WRITE)
                else:
                    process.stdin.close()
                while selector.get_map() or process.poll() is None:
                    for key, _ in selector.select(1):
                        if key.fileobj is process.stdin:
                            try:
                                sent = os.write(key.fileobj.fileno(), request[:65536])
                                request = request[sent:]
                            except BlockingIOError:
                                continue
                            except BrokenPipeError:
                                # A launcher may close its request pipe while
                                # still publishing its exact failure/outcome.
                                request = request[:0]
                            if not request:
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                            continue
                        try:
                            chunk = os.read(key.fileobj.fileno(), 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            if not pending_line:
                                continue
                            # A final JSON event need not end in a newline.
                            pending_line += b'\n'
                        else:
                            log.write(chunk)
                            log.flush()
                            pending_line += chunk
                        if len(pending_line) > 64 * 1024 * 1024:
                            raise ValueError('Native event exceeds transport byte budget')
                        while b'\n' in pending_line:
                            line, pending_line = pending_line.split(b'\n', 1)
                            try:
                                event = json.loads(line)
                            except ValueError:
                                continue
                            if not isinstance(event, dict):
                                continue
                            for public_event in backend.public_events(event):
                                # Text/tool publication needs the shared public
                                # cleaner; never expose arbitrary native data.
                                if public_event['type'] in {'session.started','turn.completed','usage'}:
                                    public_log.write(json.dumps(public_event,ensure_ascii=False)+'\n')
                                    public_log.flush()
                            event = backend.event(event)
                            if event.get('failure') is not None:
                                native_failures.append(event['failure'])
                            native_activity_observed = native_activity_observed or bool(event.get('activity'))
                            if event.get('failed') and (terminal_observed
                                    or (event.get('failure') or {}).get('event_type') != 'error'):
                                terminal_fault = terminal_fault or 'native_failure_event'
                            if terminal_observed and ('session' in event or event.get('activity')):
                                terminal_fault = terminal_fault or 'native_activity_after_completion'
                            if terminal_observed and event.get('completed'):
                                terminal_fault = terminal_fault or 'native_completion_duplicated'
                            if 'session' in event:
                                thread_id = event['session']
                                live_meter.bind_thread(thread_id)
                            if 'live_usage' in event:
                                live_meter.observe(event['live_usage'])
                            if 'result' in event:
                                if not isinstance(event['result'], dict) or pending.exists() or pending.is_symlink():
                                    raise ValueError('Native protocol result is invalid or duplicated')
                                write(pending,event['result'])
                            if event.get('completed'):
                                if not terminal_observed:completed_before = len(native_failures)
                                terminal_observed = True
                            current = event.get('usage')
                            if current is not None:
                                # Only one completion can verify this invocation.
                                # Ambiguous duplicates retain an observed lower
                                # bound without counting the same report twice.
                                for key in terminal_meter:
                                    terminal_meter[key] = max(terminal_meter[key], current[key])
                                terminal_usage_observed = True
                                observed = True
                                hint_dirty=True
                            elif event.get('completed'):
                                terminal_usage_missing = True
                    observation = live_meter.poll()
                    attempt_terminal = terminal_attempt_tokens(terminal_meter, observation)
                    projected = {key: max(attempt_terminal[key], observation['tokens'][key]) for key in meter}
                    if projected != meter:
                        hint_dirty = True
                    meter = projected
                    observed = observed or observation['has_observation']
                    if observed:
                        write(record / 'usage.json', meter)
                    write(record / 'usage-observation.json', observation)
                    # Pause is a next-node barrier. Accepted work can finish and
                    # seal; it must not become an unknown submission on resume.
                    limits = control_budget.snapshot(root, settings)
                    if final_return is None:
                        stop = stop or control_budget.exploration_stop(root, job, limits)
                    stop = stop or control_budget.stop_node(root, job, intent, limits, meter)
                    if intent.get('continuation_kind')=='author_closeout':
                        elapsed=control_budget._node_duration(root,job,intent,limits)-intent['budget_slice']['previous_seconds']
                        if elapsed>=permit['max_seconds']:
                            stop=stop or 'author_closeout_wall_limit'
                    stop = stop or search.stop_reason(root, job, search_intent, limits, meter)
                    if stop or hint_dirty or time.monotonic()>=next_hint:
                        control_budget.write_node_hint(root,job,intent,limits,meter,observed=observed,
                            author_limits=author_limits,notice_channel=notice_channel)
                        next_hint=time.monotonic()+5
                        hint_dirty=False
                    if stop:
                        break
    except BaseException as exc:
        error = exc
        from .experiments import UnsupportedExecution
        if process is None and isinstance(exc,OSError) and exc.errno==errno.E2BIG:
            error=UnsupportedExecution('Shared input readonly mount plan exceeds the operating system launch capability')
        stop = 'shared_input_view_unsupported' if isinstance(error,UnsupportedExecution) and process is None else 'native_error_unknown'
    finally:
        try:
            scientific_lifetime.close()
            bridge_stopped = True
        except (OSError,ValueError,RuntimeError) as bridge_error:
            error=error or bridge_error
            cleanup='cleanup_unknown';stop='cleanup_unknown'
        if process is not None:
            try:
                if process_id is not None and runtime.process_matches(process_id, process_id['task_ref']):
                    os.killpg(process_id['pgid'], signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if process_id is not None and runtime.process_matches(process_id, process_id['task_ref']):
                    os.killpg(process_id['pgid'], signal.SIGKILL)
                process.wait(timeout=5)
            if process_id is not None:
                native_cleanup = runtime.cleanup_owned_group(process_id)
                cleanup = 'cleanup_unknown' if cleanup=='cleanup_unknown' else native_cleanup
                if cleanup == 'cleanup_unknown':
                    stop = 'cleanup_unknown'
            if job.get('experiment_mode')=='local' and job['role']=='W':
                from . import local_experiments
                try:managed_cleanup=local_experiments.cleanup(root,job)
                except (OSError,ValueError,KeyError,TypeError):managed_cleanup='cleanup_unknown'
                configured_local=(experiment_config is not None
                    and experiment_config['execution_interface']['executor']=='local_execution:command')
                managed_stopped=(managed_cleanup=='observed_group_cleaned'
                    or not configured_local and managed_cleanup=='not_applicable')
                if not managed_stopped:
                    cleanup='cleanup_unknown';stop='cleanup_unknown'
            if process.stdout is not None:
                process.stdout.close()
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        _cleanup_code_exports(root, code_exports, launch_started=launch_started,
            process=process, cleanup=cleanup, bridge_stopped=bridge_stopped)
    native_failure = native_recovery.combine(native_failures, completed_before=completed_before
        if terminal_observed and process is not None and process.returncode == 0
            and cleanup == 'observed_group_cleaned' else None)
    if native_failure is not None:terminal_fault = terminal_fault or 'native_failure_event'
    stop = stop or terminal_fault
    if process is not None:
        observation = live_meter.poll()
        observed = observed or observation['has_observation']
        attempt_terminal = terminal_attempt_tokens(terminal_meter, observation)
        meter = {key: max(observation['tokens'][key], attempt_terminal[key]) for key in meter}
        observation.update(live_coverage=observation['coverage'], live_reason=observation['reason'],
                           live_tokens=dict(observation['tokens']))
        if terminal_observed:
            observation['source'] = backend.terminal_source
        if terminal_fault:
            observation.update(coverage='unknown', reason=terminal_fault)
        elif terminal_usage_observed and not terminal_usage_missing:
            mismatch = any(attempt_terminal[key] < meter[key] for key in meter)
            if mismatch or live_meter.fault:
                observation.update(coverage='unknown', reason=live_meter.fault or 'terminal_usage_below_live_observation')
            else:
                observation.update(coverage='terminal', reason=None, source=backend.terminal_source,
                                   reconciliation=('matched' if observation['tokens'] == attempt_terminal else 'terminal_covers_live')
                                       if observation['has_observation'] else 'terminal_fallback',
                                   unreported_usage=None)
        else:
            observation.update(coverage='unknown', reason=live_meter.fault or 'terminal_usage_unreported')
        observation['tokens'] = dict(meter)
        if observed:
            write(record / 'usage.json', meter)
        write(record / 'usage-observation.json', observation)
    outcome = dict(returncode=process.returncode if process else None, native_started=process is not None,
          stop_reason=stop, usage=usage(root), thread_id=thread_id, cleanup=cleanup,
          terminal_observed=terminal_observed, native_failure=native_failure,
          native_activity_observed=native_activity_observed,
          native_identity=backend.identity(live_meter, thread_id),
          native_backend=backend.name,
          native_schema_transport=backend.active_schema_transport,
          native_permissions=backends.permission_summary(settings),
          budget_notification=notice_channel,
          metering=live_meter.snapshot() if process is None else observation,
          limitation='Observed token metadata only; running requests and reporting delay are unmeasured.')
    outcome['shared_inputs']=dict(enforcement=('registered_final_namespace' if namespace_launcher is not None
        else 'kernel_readonly_mounts') if process is not None else 'not_started',
        limitation='Same-user host processes and pre-existing writable descriptors remain trusted.')
    if error is not None and process is None:
        outcome['limitation']=str(error)
    if final_return is not None:
        outcome['final_a_completion'] = control_budget.final_a_receipt(final_return, intent, meter if observed else None, outcome['usage'])
    if attempt and intent.get('context_mode') != 'new_context' and not startup_retry and thread_id != intent['session']['thread_id']:
        stop = outcome['stop_reason'] = 'native_thread_changed'
    if (settings.get('resume_policy') is not None or intent.get('continuation_kind') == 'author_closeout') and process is not None:
        outcome['execution_processes'] = {name: record_ref(record / name) for name in ('runner-process.json', 'native-process.json')}
    if intent.get('budget_slice') is not None:
        try:
            outcome['budget_slice'] = control_budget.node_report(root, job, intent, meter)
            outcome['budget_slice']['usage_observed'] = observed
        except ValueError:
            stop = outcome['stop_reason'] = 'node_slice_meter_unknown'
            outcome['budget_slice'] = dict(status='unknown')
    if search_intent.get('search_slice') is not None:
        try:
            outcome['search_slice'] = search.report(root, job, search_intent, meter)
            if intent.get('budget_slice') is not None:outcome['search_slice'] = outcome['budget_slice']
        except ValueError:
            stop = outcome['stop_reason'] = 'w_slice_meter_unknown'
            outcome['search_slice'] = dict(status='unknown')
    if error is not None:
        write(record / 'native-outcome.json', outcome)
        raise error
    if stop or process.returncode != 0 or not terminal_observed:
        write(record / 'native-outcome.json', outcome)
        raise RuntimeError('Native work is not a verified terminal result; preserved without replay')
    raw = record / 'science-output.json'
    if pending.is_symlink() or (pending.exists() and not pending.is_file()):
        outcome['output_status'] = 'unsafe'
        write(record / 'native-outcome.json', outcome)
        raise ValueError('Native science output is missing or not a regular file')
    if not pending.exists():
        outcome['output_status'] = 'missing'
        write(record / 'native-outcome.json', outcome)
        raise OutputRepairRequired('missing_output', 'Return complete scientific JSON; preserve original facts/plans/limits. No work this repair turn; reuse prior results/checkpoints only if they exist.')
    os.replace(pending, raw)
    outcome['raw_ref'] = file_ref(raw)
    try:
        _, normalization = decode_scientific_output(raw.read_text(encoding='utf-8'))
        if normalization is not None:
            outcome['scientific_output_normalization'] = normalization
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass  # Preserve the original for the existing bounded output-repair gate.
    finally:
        write(record / 'native-outcome.json', outcome)
    return outcome
