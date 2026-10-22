"""One fresh native process per scientific role; no model scheduler.

An explicit launcher owns native permissions and functional MCP configuration.
The controller never constructs MCP agent calls. Only explicit qualified continuation
can continue the exact original native thread.
"""
from __future__ import annotations

import json
from contextlib import ExitStack
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from . import configuration_updates
from . import budget as control_budget, execution, runtime, search, checkpoint, profiles, backends
from .output_repair import OutputRepairRequired
from .contracts import (ACTIONS, actions, ROLE_INSTRUCTIONS, digest, host_record, read,
                        seal_result, workspace, write)


def usage(root: Path):
    """Sum distinct node meters, never re-add cumulative observations."""
    totals = dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    unknown = []
    from . import fork_runtime
    for step in fork_runtime.local_steps(root, include_native=True):
        for record in execution.records(root, step):
            if not (record / 'native-intent.json').exists():continue
            meter = record / 'usage.json'
            if not meter.exists():
                if step not in unknown:unknown.append(step)
                continue
            value = read(meter)
            if _usage_event(dict(type='turn.completed', usage=value)) != value:
                raise ValueError('Native usage observation changed')
            observation = record / 'usage-observation.json'
            outcome = record / 'native-outcome.json'
            metadata = read(outcome).get('metering') if outcome.exists() else None
            if metadata is None and observation.exists():metadata = read(observation)
            if metadata is not None and metadata.get('coverage') == 'unknown' and step not in unknown:
                unknown.append(step)
            for key in totals:totals[key] += value[key]
    result=dict(**totals, observed_tokens=totals['input_tokens'] + totals['output_tokens'], unknown_steps=unknown)
    result.update(control_budget.usage_accounting(root,result))
    return result


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
    job = read(job_path)
    profile_binding = profiles.binding(settings, job)
    node_anchor = control_budget.accept_node(root, job, final_report=final_report)
    if not control_budget.task_step_available(root, step) and not (node_anchor or {}).get('final_return'):
        raise control_budget.NodeBudgetUnavailable('step_limit')
    if node_anchor is not None and job['role'] == 'W' and search.launch_reason(root, job):
        raise ValueError('W dispatch violates its frozen search authorization')
    anchor = search.accept(root, job) if node_anchor is None else None
    with (record / "native-intent.json").open("x") as stream:
        json.dump(dict(step=step, task_sha256=digest(job_path), prepared_at=time.time(), **profile_binding,
                       **(dict(search_slice=anchor) if anchor is not None else {}),
                       **(dict(budget_slice=node_anchor) if node_anchor is not None else {})), stream)
        stream.flush()
        os.fsync(stream.fileno())
    runtime.start(root, step)
    return wait_native(root, step)


def wait_native(root: Path, step: int):
    """Completion polling is transport; only the original outcome can seal."""
    record = execution.record(root, step)
    startup_until = time.monotonic() + 5
    while True:
        from . import managed_wait
        if (record/managed_wait.NAME).exists() and not (execution.origin(root,step)/'result-identity.json').exists():
            managed_wait.wait_and_wake(root,step)
            record=execution.record(root,step);startup_until=time.monotonic()+5
            continue
        state = runtime.node_state(root, step)
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
                if job['role']=='W' and fragments and not (record/'checkpoint.json').exists():
                    raise continuation.ResumeUnavailable('worker_fragment_checkpoint_unavailable')
            try:result = continuation.seal_or_repair(root, step)
            except managed_wait.ExecutionWaiting:
                continue
            if result.get('fragment_continuation') is True:
                record = execution.record(root, step)
                startup_until = time.monotonic() + 5
                continue
            return result
        if state != "running":
            if state == 'unknown' and time.monotonic() < startup_until:
                # A probe can race original receipt publication. Re-probe only
                # this accepted execution in the existing startup window; a
                # second file read cannot turn the earlier unknown into proof
                # of a bad identity. Waiting never permits another submission.
                time.sleep(.05)
                continue
            raise RuntimeError("Original native execution is unknown; preserved without replay")
        time.sleep(.1)


def run_native(root: Path, step: int, attempt=0):
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    settings = configuration_updates.for_step(root, step)
    work = workspace(root, step)
    job_path = root / 'jobs' / f'{step:03d}.json'
    job = read(job_path)
    record = execution.record(root, step, attempt)
    record.mkdir(parents=True, exist_ok=True)
    intent = read(record / 'native-intent.json')
    final_return = control_budget.final_a_return(root, job, intent)
    startup = None
    if intent.get('continuation_kind') == 'startup_successor':
        from . import startup_recovery
        startup = startup_recovery.validate_intent(root,job,intent)
        startup_recovery.require_eligibility(root,step,startup)
    if intent.get('task_sha256') != digest(job_path):
        raise ValueError('Native execution task changed')
    profile = profiles.validate_intent(settings, job, intent)
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
    backend = backends.selected(settings)
    standard_native = backend.standard
    resume_position = None
    role_instruction = job.get('native_role_instruction', ROLE_INSTRUCTIONS[job['role']])
    if job['role']=='A' and not job.get('formal_review'):
        from .contracts import AUTHOR_EVIDENCE_GUIDANCE
        role_instruction += '\n' + AUTHOR_EVIDENCE_GUIDANCE
    if job.get('formal_review'):
        from . import formal_review
        role_instruction=formal_review.public(root,job)['review']['instruction']
    schema_path = execution.origin(root, step) / 'science-result.schema.json'
    args = backend.launch(profile, work, pending, schema_path, role_instruction)
    if settings.get('resume_policy') is not None:
        from . import continuation
        continuation.validate_launcher(root, step)
    if attempt and intent.get('context_mode') != 'new_context':
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
        import hashlib
        if hashlib.sha256(header).hexdigest() != intent['session']['header_sha256']:
            raise ValueError('Original native session header changed')
        args, resume_position = backend.resume(args, profile, work, pending, schema_path, session)
    (work / 'artifacts').mkdir(exist_ok=True)
    (work / 'work').mkdir(exist_ok=True)
    budget = usage(root)
    limits = control_budget.snapshot(root, settings)
    links_instruction = ('and limitations. Ordinary scientific citations and useful deliverable links are optional; unresolved links '
              'do not select files or acquire evidence qualification. Do not write handoff markers or mechanical '
              'evidence/repair paths. implementation_selection is a scientific choice: null keeps the already '
              'selected inputs, [] clears them, and listed object IDs replace them; do not restate unchanged choices. '
              if job.get('handoff_protocol', 1) >= 7 else
              'and Markdown links to the few needed deliverables, such as [comparison](artifacts/comparison.csv); '
              'the host resolves these links for the next role. ')
    completion = ("Complete your role's scientific work within the budget, preserving necessary validation. Report findings, uncertainties "
                  if job.get('handoff_authority_version') == 1 else
                  'Conclude once you have a decision-relevant scientific contribution. Report findings, uncertainties ')
    if job.get('scientific_work_unit'):
        completion=job['scientific_work_unit']+' Report findings, uncertainties '
    verification = '' if job.get('handoff_authority_version') == 1 else 'Leave further scientific verification requests to A. '
    prompt = (role_instruction + '\nRead science-task.json, then the global files and direct '
              'parent conclusions it names. Read particular parent artifacts only as scientifically needed. '
              'Use work/ for scratch code, logs, diagnostics and intermediate models; artifacts/ contains only '
              'necessary deliverables for the next role. Experiments may use experiments/<name>/work for native '
              'authoring scratch and experiments/<name>/artifacts for managed outputs, with an optional brief '
              'scientific conclusion.md. Those are directories, not extra role agents. '
              + completion + links_instruction + verification
              + 'The controller collects deliverables, records paths and hashes, validates and seals your final response. '
              'Do not build attachment indexes, manifests, provenance inventories, hash lists, packaging scripts, '
              'or schema validators. Do not duplicate final responses in files or copy every model for handoff. '
              'Read configured_delivery and any delivery_requirements in science-task.json. The effect scope is a configured '
              'capability description, not execution permission, scientific qualification or a promise to complete missing work. '
              'Historical cross-node delivery uses A next_action=deliver within that adapter scope; native exp submit '
              'uses declared run paths and does not resolve archived historical originals. '
              'Scientific checks required by the task remain your work. Return the small scientific JSON described '
              'by final_response, directly as your final answer. Routing, when needed, uses next_action with a '
              'separate route_reason. The controller dispatches the next role; functional tools retain their permissions.\n'
              + json.dumps(dict(
                  next_action_options=read(execution.origin(root,step) / 'science-result.schema.json')['properties']['next_action']['enum'] if job.get('final_only') else actions(job['role'], job.get('handoff_protocol', 1),
                      audit_enabled=settings.get('candidate_audit_policy') == {'version': 1},
                      review_enabled=job.get('formal_review_version')==1),
                  shared_seconds_remaining=max(0, int(limits['seconds_remaining'])),
                  shared_observed_tokens_remaining=max(0, limits['max_tokens']-budget.get('accounted_tokens',budget['observed_tokens'])),
                  metering='Native usage may be delayed; this is not a guaranteed billing cap.',
                  **(dict(node_budget=intent['budget_slice']['limits']) if intent.get('budget_slice') else {})), ensure_ascii=False))
    if job.get('formal_review'):
        prompt=(role_instruction+'\nRead science-task.json, its exact reviewed material and global inputs. '
            'Assess the evidence before requester explanations; no favorable verdict is presumed. '
            'Requester conclusions and full historical arguments are not default inputs. On-demand file reading is not completely observed; this is not blinded review or access isolation. '
            'Do not edit the subject. Return only the four scientific verdict/findings fields in final_response; '
            'the host binds subject, participants, qualification and return route. A pass grants no execution permission. '
            'No IDs, hashes, manifests or mechanical receipts are your work. '
            +json.dumps(dict(shared_seconds_remaining=max(0,int(limits['seconds_remaining'])),
                shared_observed_tokens_remaining=max(0,limits['max_tokens']-budget.get('accounted_tokens',budget['observed_tokens']))),ensure_ascii=False))
    if experiment_config is not None:
        selected=experiment_config['execution_interface']
        if job.get('worker_view_version') == 1:
            from .worker_execution_bridge import SUBMISSION_DIRECTORY
            managed_command=[sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge',
                '--submission-directory',SUBMISSION_DIRECTORY]
        else:
            managed_command=[sys.executable,'-m','scidiscovery.research_loop.experiments',
                '--config','work/experiment-config.json']
        prompt += ('\nManaged registered experiments: '+json.dumps(dict(
            command=managed_command,
            actions=['capabilities','describe --key <key> --output <new scratch file>','run --payload <original>','lineage-status'],
            lifecycle=selected['capabilities'],result_protocol=selected['result_protocol']),ensure_ascii=False)
            +'. First read capabilities and the relevant complete public input contract with describe; do not invent an opaque payload format. '
            'If no input contract is declared, use an already bound original payload or report unsupported. '
            'Use only declared lifecycle operations on original call IDs. Unavailable optional capabilities are unsupported, never a passing verdict. '
            'Native shell remains available for scratch; only managed calls are recorded. Never repeat an unknown submission.\n')
    if experiment_config is not None and experiment_config['execution_interface']['executor']=='local_execution:command':
        prompt += ('\nManaged local experiments: run '+json.dumps([*managed_command,
            'run','--experiment-dir','experiments/<name>','--script','experiments/<name>/run.py','--','<script args>'])
            +'. Use real paths/arguments. Only the chosen directory is snapshotted, excluding its artifacts/. '
            'The copied script runs with that read-only snapshot as cwd and writes the writable artifacts/. '
            'The default native backend also exposes the task public_root read-only at its declared absolute path; '
            'that live public tree is outside the input snapshot and does not prove all dynamic reads were frozen. '
            'Control retains exit/time/logs/inputs/outputs. Optional artifacts/result.json is version 1 JSON with metrics '
            '(name,value; optional unit,evaluation_key,direction) and outputs (path relative to the experiment root, label). '
            'unit, evaluation_key and direction retain your scientific explanation; control does not classify or infer their meaning. Read describe for the complete optional result contract and field diagnostics. '
            'An optional candidates array declares every retained model available for A to choose. Each member names one distinct exact declared artifact output; label and a metric reference are optional. A missing or unresolved metric does not discard a retained candidate. Control binds each identity to its original call and artifact, and delivers its retained inputs/results only after A explicitly chooses it. '
            'Preserve the actual task-required deliverables, including needed evaluated objects and native receipts, in these declared outputs before temporary files disappear. The adapter cannot reconstruct missing results from source code or metrics. '
            'Use finite metric values; control does not infer metric meaning, candidate ranking or qualification. '
            'Do not write hashes or identities. Native shell remains available for scratch. Query lineage-status for original calls; never repeat an unknown call.\n')
    output_only_wake = bool(intent.get('execution_wake') and (settings.get('resume_policy') or {}).get('output_only'))
    # External W can configure its managed call after receiving this prompt.
    # A wake may consume an external original without configuring a new run.
    if job['role'] == 'W' and ('managed_execution_wait' in job or experiment_config is not None or intent.get('execution_wake')):
        prompt += ('\nFollow the latest executor response\'s host-generated handoff.action and instruction. '
            'Polling instructions supersede older query-count advice only; frozen wake permission, original identity and budget remain binding. '
            'return_control: return scientific JSON now with pending limitations. '
            'poll_original: wait and query only that original call in this turn at poll_interval_seconds while pending and queryable, '
            'until handoff changes or an actual original budget, time or host-stop limit is reached; no query-count cap. '
            'consume_terminal: read original outputs and interpret the verified terminal result. '
            'reconcile_or_stop: reconcile the same call once or report the obstacle; never blindly poll or resubmit.\n')
    if job['role'] == 'W' and not output_only_wake:
        prompt += ('\nAn optional engineering_check may follow a confirmed managed call while you remain in this task. Read its compact feedback and repair source in this same session when useful. '
            'sample_replay_ok covers only its declared sample path, never full training or audit qualification. Small-sample or bounded-check failures may be inconclusive. '
            'An isolated replay sees only frozen submitted source and declared public inputs plus the task runtime dependencies; sibling scratch helpers and your home are absent. '
            'Do not package dependencies or write hashes/manifests; make the submitted implementation self-contained or use an explicitly supported dependency input.\n')
    if checkpoint.enabled(root, job) and not output_only_wake:
        prompt += ('\nA native turn can be a fragment of this same scientific stage. Give your current understanding, '
                   'unresolved questions and scientific next intent. stage_intent=continue requests more work in the assigned task; '
                   'review reports its completed scientific delivery, while decision requests A with partial work. The host can require review at the original investment boundary. '
                   'The host creates checkpoints and counts attempts; do not write mechanical checkpoint or budget fields.\n')
    if attempt and startup is None:
        remaining = dict(observed_tokens=max(0, intent['budget_slice']['limits']['max_observed_tokens'] - intent['budget_slice']['previous_tokens']),
                         active_seconds=max(0., intent['budget_slice']['limits']['max_seconds'] - control_budget._node_duration(root, job, intent, limits)))
        if control_budget.node_mode(root)=='soft':
            remaining.update(mode='soft_targets',previous_observed_tokens=intent['budget_slice']['previous_tokens'])
        label = 'Original soft-target gaps (zero is not a new grant): ' if control_budget.node_mode(root)=='soft' else 'Original node remaining budget: '
        context = 'new context for this same task/workspace' if intent.get('context_mode') == 'new_context' else 'same task/session/workspace'
        recovery = ('Read the retained original execution results and complete only your final response. '
                    if output_only_wake else
                    'collect or recover managed executions by their original IDs rather than resubmitting. '
                    'Use scientific judgment for ordinary native shell checkpoints; no new mechanical registry is required. ')
        prompt = (label + json.dumps(remaining) + '\nContinue this ' + context + '. Read existing checkpoints and confirmed results; '
                  + recovery + '\n' + prompt)
        if intent.get('output_repair'):
            prompt = ('Repair only your final response for this same completed task. Preserve completed experiments and '
                      'their results; do not rerun or resubmit them. Return corrected scientific JSON using the frozen '
                      'final_response contract. Controller diagnosis: '
                      + json.dumps({key: intent['output_repair'][key] for key in ('code', 'diagnostic')}, ensure_ascii=False)
                      + '\n' + prompt)
    if intent.get('execution_wake'):
        from . import managed_wait
        from . import continuation
        continuation.validate_wake(root,step,intent,execution.record(root,step,attempt-1))
        managed_wait.wake_packet(root,job,intent)
        wake_scope = ('This output-only wake permits interpreting and preserving the original terminal results and completing your final response only. '
            'Do not start new experiments or training, request a fragment continuation or WorkRepair. '
            if output_only_wake else '')
        prompt=('The original managed execution has finished. Read work/execution-results.json and its exact retained outputs. '
            + wake_scope +
            'Continue this same task/session from its checkpoint, interpret the results, and return your own scientific findings. '
            'Do not rerun or resubmit the completed execution.\n'+prompt)
    if intent.get('work_repair'):
        request=intent['work_repair']
        prompt=('Complete the missing scientific delivery for this same assigned task/session. Keep the original task action. '
            'Reuse confirmed observations and query existing original calls; never resubmit completed experiments. '
            'Add experiments only for scientifically unresolved assigned mechanisms within original authorization. '
            'Return your own mechanism findings, obstacles and limitations; require A decision if science cannot proceed. '
            'Controller diagnosis: '+json.dumps({key:request[key] for key in ('code','diagnostic')},ensure_ascii=False)+'\n'+prompt)
    if intent.get('continuation_kind') == 'fragment':
        prior = execution.record(root, step, attempt - 1)
        saved = intent['fragment_checkpoint']
        if saved['path'] != (prior / 'checkpoint.json').relative_to(root).as_posix() or saved['sha256'] != digest(prior / 'checkpoint.json'):
            raise ValueError('Native fragment checkpoint changed before launch')
        packet = checkpoint.brief(root, job, prior)
        write(work / 'work/science-resume.json', packet)
        prompt += ('\nRead work/science-resume.json for the bounded prior fragment preview. Continue within the original scientific scope; '
                   'the host retains exact execution and candidate identities. Return current understanding, remaining questions '
                   'and stage_intent (continue this mechanism or review by A). Do not write checkpoint IDs, counters or file manifests.\n')
    author_limits=None
    if intent.get('continuation_kind')=='author_closeout':
        from . import author_closeout
        permit=author_closeout.require_intent_eligibility(root,job,intent)
        author_limits=dict(max_observed_tokens=permit['max_tokens'],max_seconds=permit['max_seconds'])
        scientific_facts=intent.get('worker_view_version') == 1
        facts_name='author-closeout-science.json' if scientific_facts else 'author-closeout-facts.json'
        prompt=('This is one fresh, explicitly authorized author-closeout turn for the original stopped W. '
            f'Read work/{facts_name}, retained source/results and your existing scientific draft artifacts. '
            'Interpret only the completed original experiments and return your own final scientific JSON. '
            'No new experiment, training, submission, model refinement or replay is authorized. '
            'Managed tools and original execution destinations are read-only. Do not query a backend or collect into old records. '
            'Use existing facts directly; put only necessary scientific deliverables in artifacts/. '
            'Choose stage_intent=review or decision, never continue. The controller will hand your conclusion to A. '
            'Assess the exact original objective against the retained evidence yourself; state which requirements are supported, '
            'unsupported or unresolved and report actual limitations. '
            f'This author turn has a soft target of {permit["max_tokens"]} observed tokens and a finite limit of {permit["max_seconds"]} active seconds. Finish your original scientific response when the token target is reached.\n'
            +prompt)
        if attempt==2:
            # This explicitly authorized successor needs preserved facts, not the
            # normal experiment/engineering workflow or a broad directory dump.
            prompt=(role_instruction+'\nThis is the single explicitly authorized second author-only closeout for the exact original W. '
                +(f'First read work/{facts_name}, then science-task.json and the exact objective. '
                  if scientific_facts else f'First read {facts_name} beside the designated final-output file, then science-task.json and the exact objective. ')
                +('The scientific view includes every retained terminal call, available results and limitations. Control retains original machine receipts privately. '
                  if scientific_facts else 'The compact index includes every retained call and points to complete originals. ')
                +'Read particular source/results or draft artifacts only as scientifically needed. '
                'Do not dump the directory tree, full observation logs, all source files or repeated reports into context. '
                'This reduces transport overhead; it does not restrict consideration of failures, counterevidence or scientific uncertainty. '
                'No new experiment, training, refinement, submission, replay, backend query or collection into old records is authorized. '
                'Original calls/models and prior native attempts are read-only; write necessary scientific deliverables only in artifacts/. '
                'Interpret the completed experiments yourself and return the final scientific JSON described in science-task.json. '
                'Choose stage_intent=review or decision, never continue. The controller will give your own conclusion to A. '
                'Assess the exact original objective against the retained evidence yourself; state which requirements are supported, '
                'unsupported or unresolved and report actual limitations. '
                'Do not write mechanical inventories, source/model hashes, private control metadata or duplicate final responses. '
                f'This turn has a soft target of {permit["max_tokens"]} cumulative observed input plus output tokens (cached input included) and a finite limit of {permit["max_seconds"]} active seconds. Finish your original scientific response when the token target is reached. '
                'Each model request counts its entire input again; prioritize a bounded reading pass and your final scientific response.\n')
    mode=control_budget.node_mode(root)
    if (final_return is None and job.get('purpose') is None and not job.get('formal_review')
            and intent.get('continuation_kind')!='author_closeout'):
        # Ordinary execution_available reads frozen reserve configuration only;
        # reuse these observations without reentering current-node admission.
        available=control_budget.execution_available(root,job,limits,budget)
        prompt += ('\nOrdinary exploration budget advice: '+json.dumps(dict(
            observed_tokens_remaining_upper_bound=available['tokens'],active_seconds_available=available['seconds'],
            basis='shared_authorization_minus_existing_final_or_closeout_reserve',
            observed_usage_is_lower_bound=True,unknown_usage=bool(budget.get('unknown_steps')),
            inflight_usage_unknown=True),ensure_ascii=False)+'\n')
    prompt += ('\nBudget mode: '+mode+'. '+('Node/search limits are soft targets, not renewed grants. ' if mode=='soft' else
        'Node/search observed limits remain enforced. ')+
        'Between bounded actions read work/budget-status.json when available; some read-only author views do not expose dynamic updates. '
        'This small advisory is not a grant, execution permission or scientific evidence. '
        'Each model request counts its entire input again; cached input is already included in input, counted once. '
        'Unknown usage is not zero. In-flight requests and reporting delay are unmeasured; current-work remaining values are upper bounds. '
        'There is no per-request budget admission or guaranteed final-response allowance. '
        'Near limits, targets or the existing delivery reserve, finish bounded actions and return your own scientific JSON promptly, '
        'preserving necessary evidence, uncertainty and limitations. '
        + ('The original finite call duration and resource boundaries still apply; the exact final completion exception follows.\n' if final_return is not None else
           'Shared quotas are checked before the next node; finish this accepted node and report all usage, including overage. Original execution and resource limits still apply.\n' if mode=='soft' else
           'Shared hard limits still apply.\n'))
    if final_return is not None:
        prompt += ('\nThis is your final scientific return; no further scientific role or correction turn will be dispatched. '
            'Complete and submit your own final scientific JSON in this same execution. In summary, scientific_result and limitations, '
            'explain what you completed, what remains unfinished, the evidence-supported gap to the objective (if any), and why. '
            'Do not assume success or invent a gap. Reuse the available evidence; do not start new experiments or request another role. '
            'For this exact final return only, local token figures are targets; token usage exceeding the local or shared '
            'reference ceiling will be recorded in full and will not terminate this return. Shared time and step exhaustion do not revoke this return. Original per-call time, resources and '
            'execution permissions remain finite and enforced. Prioritize completing the response. '
            'An external deliver choice still requires the configured adapter and the original qualification and authorization; '
            'it does not assert that delivery or the scientific objective succeeded.\n')
    # Append after every branch that replaces the assignment, including formal
    # review and the bounded successor author turn.
    if job['role']=='W' and job.get('candidate_nomination_mechanisms'):
        prompt += ('\nReturn candidate_nominations as your complete scientific comparison preview: one representative '
            'or an explained gap for every unwaived H mechanism, including deferred and screened. '
            'Use each actual H mechanism_ref. Choose each model and explain its applicability yourself; a call binding '
            'does not classify its models. A model may explicitly represent multiple mechanisms when each reason '
            'explains that coverage. Use historical candidate references or {call_id,artifact} for one exact '
            'candidate in a current retained result.candidates declaration. Preserve the full candidate pool and '
            'original experiment materials independently. Add at most '+str(job['candidate_nomination_policy']['extra_slots'])+
            ' distinct extras beyond the representatives; do not truncate the retained pool to this preview size. '
            'No available model means an explicit scientific gap, never a fabricated candidate. '
            'Explain an unavailable or unsuitable old representative; temporary relative low score alone is '
            'insufficient to discard a mechanism. Only an explicit A waived disposition with its scientific '
            'negative or infeasibility reason removes mechanism coverage. Missing evidence and deferral retain it.\n')
    if job['role']=='A' and not job.get('formal_review'):
        if job.get('history',{}).get('candidate_nomination_ids') is not None:
            prompt += ('\nStart candidate comparison with the W scientific nominations and their reasons/gaps in '
                'lineage.indexes.nominations. They are a limited comparison preview, not a ranked or exhaustive '
                'candidate pool. Read lineage.indexes.candidates and next_page plus original experiment materials '
                'as needed. You may select any registered candidate, including an alternative outside the preview, '
                'using the existing candidate_recommendation and selection_explanation. Do not request another '
                'experiment or registration merely to bring in a retained model, and do not rewrite W nominations. '
                'Keep deferred and screened mechanisms in the comparison; only your explicit waived disposition '
                'with a scientific negative or infeasibility reason exempts mechanism coverage. Temporary relative '
                'low score alone is insufficient; preserve gaps and original limitations.\n')
        prompt += ('\nAny explicit candidate_recommendation requires selection_explanation: give criteria and '
            'criteria_change.status as unchanged or revised. For revised criteria, name prior_criteria, changes, '
            'evidence with locators explaining why, and accepted_tradeoffs. For unchanged criteria, use null '
            'for those change details. Compare with prior H/W design and report comparison conditions, thresholds '
            'and fallback rules: relaxing any such rule is revised even if a report already adopted it. '
            'If no prior criteria exist, say so honestly. No candidate choice requires '
            'no invented comparison: selection_explanation may be null. Control checks declaration completeness '
            'and returns missing details to this same A for output repair; it does not judge scientific truth '
            'or sufficiency, rank candidates or choose a default. This applies to the final A return too.\n')
    prompt += ('\nDo not independently compute, compare, or revalidate file/evidence hashes or checksums, '
        'generate hash inventories, or perform redundant mechanical integrity checks. '
        'Control automatically owns integrity verification; use scientific names/references and verified tool results. '
        'If a tool reports an integrity failure, retain and report it for control repair; '
        'do not run your own checksum verification or alter bindings. '
        'These restrictions cover extra mechanical integrity work, not hash algorithms required by the scientific task itself. '
        'Any helper receiving this assignment must follow the same restrictions.\n')
    home = execution.home(root, step, attempt)
    home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'THIN_HWA_ROLE': job['role'], 'THIN_HWA_WORKSPACE': str(work),
           'THIN_HWA_NATIVE_HOME': str(home), 'THIN_HWA_JOB_MANIFEST': str(job_path)}
    if settings.get('resume_policy') is not None and backend.name == 'codex':env['CODEX_HOME'] = str(home)
    if startup is not None:
        env['CODEX_HOME'] = startup['native_home']
        args[0] = startup['native_program']['path']
        prompt += ('\nThis is the only final A model submission for this exact scientific task. Give your final evidence-based conclusion and unmet objectives. '
            'Select an existing candidate with deliver for original host submission, or finish with the explicit reason submission is unsupported. '
            'No wait, new scientific role, dedicated audit, experiment or correction turn is authorized. The host supplies the actual submission receipt.\n')
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
    terminal_usage_observed = False
    terminal_usage_missing = False
    terminal_meter = dict(meter)
    live_meter = backend.meter(meter_home, work, session=intent.get('session') if attempt and intent.get('context_mode') != 'new_context' else None)
    cleanup = None
    thread_id = None
    scientific_lifetime = ExitStack()
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
            control_budget.write_node_hint(root,job,intent,limits,meter,observed=observed,author_limits=author_limits)
            next_hint=time.monotonic()+5
            hint_dirty=False
            if experiment_config is not None and job.get('worker_view_version') == 1:
                from . import native_execution, worker_execution_bridge
                actual_home=Path(env.get('CODEX_HOME',str(Path.home()/'.codex')))
                if not actual_home.is_absolute():actual_home=work/actual_home
                args,launch=native_execution.prepare(root,step,record,work,actual_home.resolve(),args,
                    standard=standard_native,resume_position=resume_position,native_options=settings.get('native_options'))
                scientific_lifetime.enter_context(worker_execution_bridge.serve(
                    work/worker_execution_bridge.SUBMISSION_DIRECTORY,work/'work/experiment-config.json',work,
                    launch=launch))
            process = subprocess.Popen(args, cwd=work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=err, env=env, start_new_session=True)
            process_id = runtime.process_identity(process.pid, execution.identity(root, step, attempt))
            write(record / "native-process.json", process_id)
            process.stdin.write(backend.request(prompt, profile=profile, work=work, schema=schema_path))
            process.stdin.close()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map() or process.poll() is None:
                    for key, _ in selector.select(1):
                        chunk = os.read(key.fileobj.fileno(), 65536)
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
                            if event.get('failed'):
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
                    projected = {key: max(terminal_meter[key], observation['tokens'][key]) for key in meter}
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
                    if final_return is None and control_budget.node_mode(root) == 'hard' and limits['seconds_remaining'] <= 0:
                        stop = 'wall_limit'
                    else:
                        shared=usage(root)
                        if final_return is None and control_budget.node_mode(root) == 'hard' and shared.get('accounted_tokens',shared['observed_tokens'])>=limits['max_tokens']:stop='token_limit'
                    if final_return is None:
                        stop = stop or control_budget.exploration_stop(root, job, limits)
                    stop = stop or control_budget.stop_node(root, job, intent, limits, meter)
                    if intent.get('continuation_kind')=='author_closeout':
                        elapsed=control_budget._node_duration(root,job,intent,limits)-intent['budget_slice']['previous_seconds']
                        if elapsed>=permit['max_seconds']:
                            stop=stop or 'author_closeout_wall_limit'
                    stop = stop or search.stop_reason(root, job, search_intent, limits, meter)
                    if stop or hint_dirty or time.monotonic()>=next_hint:
                        control_budget.write_node_hint(root,job,intent,limits,meter,observed=observed,author_limits=author_limits)
                        next_hint=time.monotonic()+5
                        hint_dirty=False
                    if stop:
                        break
    except BaseException as exc:
        error = exc
        stop = 'native_error_unknown'
    finally:
        try:
            scientific_lifetime.close()
        except (OSError,ValueError,RuntimeError) as bridge_error:
            error=error or bridge_error
            cleanup='cleanup_unknown';stop='cleanup_unknown'
        if process is not None:
            try:
                if process_id is not None and runtime.process_matches(process_id, process_id['task_sha256']):
                    os.killpg(process_id['pgid'], signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if process_id is not None and runtime.process_matches(process_id, process_id['task_sha256']):
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
                if managed_cleanup=='cleanup_unknown':
                    cleanup='cleanup_unknown';stop='cleanup_unknown'
            if process.stdout is not None:
                process.stdout.close()
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
    stop = stop or terminal_fault
    if process is not None:
        observation = live_meter.poll()
        observed = observed or observation['has_observation']
        for key in meter:
            meter[key] = max(meter[key], observation['tokens'][key], terminal_meter[key])
        observation.update(live_coverage=observation['coverage'], live_reason=observation['reason'],
                           live_tokens=dict(observation['tokens']))
        if terminal_observed:
            observation['source'] = backend.terminal_source
        if terminal_fault:
            observation.update(coverage='unknown', reason=terminal_fault)
        elif terminal_usage_observed and not terminal_usage_missing:
            mismatch = any(terminal_meter[key] < meter[key] for key in meter)
            if mismatch or live_meter.fault:
                observation.update(coverage='unknown', reason=live_meter.fault or 'terminal_usage_below_live_observation')
            else:
                observation.update(coverage='terminal', reason=None, source=backend.terminal_source,
                                   reconciliation=('matched' if observation['tokens'] == terminal_meter else 'terminal_covers_live')
                                       if observation['has_observation'] else 'terminal_fallback',
                                   unreported_usage=None)
        else:
            observation.update(coverage='unknown', reason=live_meter.fault or 'terminal_usage_unreported')
        observation['tokens'] = dict(meter)
        if observed:
            write(record / 'usage.json', meter)
        write(record / 'usage-observation.json', observation)
    outcome = dict(returncode=process.returncode if process else None,
          stop_reason=stop, usage=usage(root), thread_id=thread_id, cleanup=cleanup,
          native_identity=backend.identity(live_meter, thread_id),
          native_backend=backend.name,
          native_permissions=backends.permission_summary(settings),
          metering=live_meter.snapshot() if process is None else observation,
          limitation='Observed token metadata only; running requests and reporting delay are unmeasured.')
    if final_return is not None:
        outcome['final_a_completion'] = control_budget.final_a_receipt(final_return, intent, meter if observed else None, outcome['usage'])
    if attempt and intent.get('context_mode') != 'new_context' and thread_id != intent['session']['thread_id']:
        stop = outcome['stop_reason'] = 'native_thread_changed'
    if (settings.get('resume_policy') is not None or intent.get('continuation_kind') in {'author_closeout','startup_successor'}) and process is not None:
        outcome['execution_processes'] = {name: digest(record / name) for name in ('runner-process.json', 'native-process.json')}
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
        raise OutputRepairRequired('missing_output', 'Return the required final scientific JSON; the completed work and checkpoints are already retained.')
    os.replace(pending, raw)
    outcome['raw_sha256'] = digest(raw)
    write(record / 'native-outcome.json', outcome)
    return outcome
