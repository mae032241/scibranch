"""Instance-oriented CLI: no interactive scheduler session or MCP dispatch."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
import sys
from scidiscovery.plugin_configuration import read_execution_selection

from . import budget, contracts, controller, search, research_config, instance_control
from .instance_control import status, request_pause, continue_instance


def main(argv=None):
    parser = argparse.ArgumentParser(prog='scid-research', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='Create a fresh instance with explicit finite budgets')
    prepare.add_argument('--config', type=Path, help='Single research TOML declaration; cannot mix with preparation flags')
    prepare.add_argument('--root', type=Path)
    prepare.add_argument('--objective', type=Path)
    prepare.add_argument('--user-update', type=Path, action='append', default=[], help='Freeze an additional user statement in global inputs; repeat for more')
    prepare.add_argument('--public-root', type=Path)
    prepare.add_argument('--source-restrictions', type=Path, help='Operator source declarations JSON array: exact absolute source, sha256, restriction (clear/restricted); source restrictions only, not review or execution permission')
    prepare.add_argument('--enable-formal-review', action='store_true', help='Allow an explicit A formal_review request for one selected scientific object; requires complete real native participant identities and grants no execution permission')
    prepare.add_argument('--enable-execution-authority', action='store_true', help='Freeze the optional execution request/decision contract; requires formal review and grants no execution without explicit resources and the trusted gate')
    prepare.add_argument('--execution-resource-policy', type=Path, help='Explicit version1 cumulative allowance JSON: wall_time_seconds/max_storage_bytes; no default grant')
    prepare.add_argument('--native-launcher-json', help='An argv JSON array, e.g. ["codex"]')
    prepare.add_argument('--native-backend', choices=['codex', 'protocol'], default='codex')
    prepare.add_argument('--native-options-json', help='Explicit inline backend permission/options JSON; omitted uses the same frozen defaults as TOML')
    prepare.add_argument('--history-observer-config', type=Path, help='Optional trusted host adapter/config JSON; frozen at preparation')
    prepare.add_argument('--experiment-mode', choices=['local', 'external'], help='Host-configured W execution (local) or launcher-owned execution (external); standard codex defaults to local, custom launchers to external')
    prepare.add_argument('--execution-config',type=Path,help='JSON/TOML executor selection with config_file or supported inline configuration; relative config_file uses this file directory; conflicts with external mode')
    prepare.add_argument('--search-policy', type=Path, help='Frozen W time/token targets, hard requested-repair count and optional positive initial_calls per mechanism')
    prepare.add_argument('--correction-policy', type=Path, help='Explicitly enable N4 with review_after/pause_after JSON thresholds (defaults 3/2)')
    prepare.add_argument('--node-budget-policy', type=Path, help='Explicit H/W/A/E soft time/token targets; no delivery guarantee')
    prepare.add_argument('--node-budget-mode', choices=['soft'], default='soft', help='All H/W/A/E node token targets are soft; shared admission and finite execution limits remain enforced')
    prepare.add_argument('--role-budget-policy', type=Path, help='Frozen W/H+A token shares and H/A node target JSON (null disables); disabled unless explicitly configured')
    prepare.add_argument('--selection-policy', type=Path, help='Frozen explicit closeout comparison contract JSON ({"version":2}); disabled unless explicitly configured')
    prepare.add_argument('--candidate-extra-slots', type=int, default=2, help='Freeze W nomination extras beyond one representative per unwaived H mechanism (default: 2); never caps the retained candidate pool or chooses a model')
    prepare.add_argument('--portfolio-review-after-cycles', type=int, help='Freeze an optional reminder interval in unserved completed W+A cycles; the full mechanism portfolio is always visible and reminders never gate scientific results')
    prepare.add_argument('--decision-contract', action='store_true', help='Freeze protocol10 scientific A decision requests and exact repeat admission')
    prepare.add_argument('--enable-candidate-audit', action='store_true', help='Opt into protocol9 A audit requests; requires an isolated delivery adapter')
    prepare.add_argument('--delivery-policy', type=Path, help='Explicit frozen closeout reserve and isolated delivery adapter JSON')
    prepare.add_argument('--worker-context',choices=['same_session','new_context'],help='Qualified W fragments within the same stage; requires explicit resume policy and node budgets')
    prepare.add_argument('--worker-resume-policy', type=Path, help='Explicit qualified finite continuation policy JSON; omitted standard codex uses at most two output-only repairs in its original home; custom launchers have no default')
    prepare.add_argument('--max-output-item-bytes', type=int, help='Optional explicit per-file storage quota; default has no storage size cap')
    prepare.add_argument('--max-output-total-bytes', type=int, help='Optional explicit total deliverable storage quota; default has no storage size cap')
    prepare.add_argument('--max-tokens', type=int)
    prepare.add_argument('--max-seconds', type=float, help='Maximum authorized runtime: advancement and accepted execution waiting; idle pauses do not consume this ceiling')
    prepare.add_argument('--max-steps', type=int)
    prepare.add_argument('--model')
    prepare.add_argument('--reasoning-effort')
    prepare.add_argument('--service-tier')
    prepare.add_argument('--role-profiles', type=Path, help='Optional H/W/A/E model, reasoning_effort and service_tier overrides; omitted roles/fields inherit global defaults')
    config_check = commands.add_parser('config-check', help='Read-only validation of the effective research TOML configuration')
    config_check.add_argument('--config', type=Path, required=True)
    config_inspect = commands.add_parser('config-inspect', help='Inspect effective TOML or exact frozen instance settings; host-private output')
    for name in ('fork-preview', 'fork-create'):
        command = commands.add_parser(name, help='Preview or create a paused branch from an exact historical logical task; never starts native work')
        command.add_argument('--root', type=Path, required=True)
        command.add_argument('--step', type=int, required=True)
        command.add_argument('--child-root', type=Path, required=True)
        command.add_argument('--max-tokens', type=int, required=True)
        command.add_argument('--max-seconds', type=float, required=True)
        command.add_argument('--max-steps', type=int, required=True)
        command.add_argument('--guidance-file', type=Path)
        if name == 'fork-create':
            command.add_argument('--expected-preview-id', required=True)
            command.add_argument('--operation-id', required=True)
    config_source = config_inspect.add_mutually_exclusive_group(required=True)
    config_source.add_argument('--config', type=Path)
    config_source.add_argument('--root', type=Path)
    progress_parser = commands.add_parser('progress', help='Read bounded safe public draft messages and tool status for an exact native attempt')
    progress_parser.add_argument('--root', type=Path, required=True)
    progress_parser.add_argument('--step', type=int, required=True)
    progress_parser.add_argument('--attempt', type=int, default=0)
    progress_parser.add_argument('--cursor')
    progress_parser.add_argument('--limit-bytes', type=int, default=65536)
    for name in ('result', 'materials', 'artifact'):
        command = commands.add_parser(name, help='Read exact sealed scientific originals; no advancement or qualification')
        command.add_argument('--root', type=Path, required=True)
        command.add_argument('--step', type=int, required=True)
        if name == 'materials':
            command.add_argument('--after', type=int, default=0)
            command.add_argument('--limit', type=int, default=20)
        if name == 'artifact':
            command.add_argument('--index', type=int, required=True)
            command.add_argument('--output', type=Path, required=True)
    start = commands.add_parser('start', help='Start or reconnect to an instance controller independently of the CLI/UI process')
    start.add_argument('--root', type=Path, required=True)
    start.add_argument('--continue', dest='continue_run', action='store_true')
    start.add_argument('--resume-step', type=int, help='Resume one exact known failed node through its existing frozen continuation gates')
    start.add_argument('--ack-correction')
    start.add_argument('--authorization-note')
    operations = commands.add_parser('operations', help='Read host control operation receipts')
    operations.add_argument('--root', type=Path, required=True)
    operations.add_argument('--operation-id')
    for name in ('guidance-submit', 'guidance-list', 'guidance-edit', 'guidance-revoke'):
        command = commands.add_parser(name, help='Manage immutable applied and revisable pending user guidance')
        command.add_argument('--root', type=Path, required=True)
        if name in {'guidance-submit', 'guidance-edit'}:
            command.add_argument('--text', required=True)
        if name == 'guidance-submit':
            command.add_argument('--target', choices=['global', 'H', 'W', 'A', 'E'], default='global')
            command.add_argument('--kind', choices=['advice', 'goal_change'], default='advice')
            command.add_argument('--scope', choices=['next', 'research'], default='next')
            command.add_argument('--effective-step', type=int)
        if name in {'guidance-edit', 'guidance-revoke'}:
            command.add_argument('--guidance-id', required=True)
            command.add_argument('--expected-revision', type=int, required=True)
    for name, help_text in (
            ('run', 'Run or recover only completed sealed scientific handoffs'),
            ('status', 'Read instance state without changing or starting work'),
            ('pause', 'Let accepted work finish; prevent the next scientific node'),
            ('continue', 'Clear the next-node pause and run; never replay unknown work')):
        command = commands.add_parser(name, help=help_text)
        command.add_argument('--root', type=Path, required=True)
        if name == 'continue':
            command.add_argument('--ack-correction', help='Exact current published pause trigger ID; permits one nondeferred A cycle')
            command.add_argument('--authorization-note', help='External operator decision; not authentication or a budget amendment')
    amend = commands.add_parser('budget-amend', help='Record an external operator ceiling revision without resetting consumption')
    amend.add_argument('--root', type=Path, required=True)
    amend.add_argument('--max-tokens', type=int)
    amend.add_argument('--max-seconds', type=float)
    amend.add_argument('--max-steps', type=int, help='Explicit finite cumulative control-step ceiling; preserves consumed continuations/actions')
    amend.add_argument('--authorization-statement', help='Original operator statement authorizing this revision')
    amend.add_argument('--execution-wall-seconds', type=int, help='Explicit cumulative execution ceiling; preserves prior reservations/usage and grants no exact-call permission')
    amend.add_argument('--execution-storage-bytes', type=int, help='Explicit cumulative execution storage ceiling; must accompany --execution-wall-seconds')
    amend.add_argument('--authorization-note', required=True, help='Operator decision for the audit; this text is not authentication')
    startup = commands.add_parser('native-startup-successor', help='Explicitly authorize the sole first model submission after exact independent startup non-submission reconciliation')
    startup.add_argument('--root',type=Path,required=True)
    startup.add_argument('--step',type=int,required=True)
    startup.add_argument('--reconciliation',type=Path,required=True)
    startup.add_argument('--reconciliation-sha256',required=True)
    startup.add_argument('--native-home',type=Path,required=True)
    startup.add_argument('--authorization-statement',required=True)
    collector = commands.add_parser('native-startup-collect', help='Collect an existing sealed final A and its exact proven unstarted delivery; never launch native work')
    collector.add_argument('--root',type=Path,required=True)
    collector.add_argument('--step',type=int,required=True)
    collector.add_argument('--reconciliation',type=Path,required=True)
    collector.add_argument('--reconciliation-sha256',required=True)
    collector.add_argument('--authorization-statement',required=True)
    author = commands.add_parser('worker-closeout', help='Explicit finite fresh-context author closeout using the exact retained W work')
    author.add_argument('--root', type=Path, required=True)
    author.add_argument('--step', type=int, required=True)
    author.add_argument('--max-tokens', type=int, required=True)
    author.add_argument('--max-seconds', type=float, required=True)
    author.add_argument('--authorization-note', required=True)
    author.add_argument('--successor', action='store_true', help='Explicitly authorize the single second author-only turn; never automatic retry')
    author.add_argument('--authorization-statement', help='Original operator statement authorizing this one successor')
    accept = commands.add_parser('worker-output-accept', help='Explicitly accept exact already returned author2 bytes; never another native turn')
    accept.add_argument('--root', type=Path, required=True)
    accept.add_argument('--step', type=int, required=True)
    accept.add_argument('--attempt', type=int, choices=[2], required=True)
    accept.add_argument('--pending-sha256', required=True)
    accept.add_argument('--session', type=Path, required=True)
    accept.add_argument('--authorization-note', required=True)
    accept.add_argument('--authorization-statement', required=True)
    closeout = commands.add_parser('closeout', help='Close exploration within the original remaining authorization')
    closeout.add_argument('--root', type=Path, required=True)
    closeout.add_argument('--export-only', action='store_true', help='Export existing verified results without sealing, launching science or delivering')
    resume = commands.add_parser('worker-resume', help='Continue one known local failed node in its exact original thread')
    resume.add_argument('--root', type=Path, required=True)
    resume.add_argument('--step', type=int, required=True)
    recover=commands.add_parser('execution-recover',help='Append same-call terminal evidence for a sealed unknown; new nodes freeze its recovery prefix')
    recover.add_argument('--root',type=Path,required=True)
    recover.add_argument('--step',type=int,required=True)
    execution_resume=commands.add_parser('execution-resume',help='Explicit finite host progress of one original sealed W call; never replay uncertain preparation')
    execution_resume.add_argument('--root',type=Path,required=True)
    execution_resume.add_argument('--step',type=int,required=True)
    execution_resume.add_argument('--call-id',required=True)
    execution_resume.add_argument('--max-seconds',type=float,required=True,help='Current finite control operation window; does not extend solver or shared authorization')
    execution_resume.add_argument('--authorization-note',required=True,help='Operator audit note only; not an execution decision')
    execution_resume.add_argument('--request-valid-seconds',type=float,help='Explicit finite replacement request validity when old request expired; no new execution allowance')
    execution_resume.add_argument('--maintenance',choices=['query','collect','cancel'],help='Maintain one existing accepted call within a current finite operation window; never prepare, submit or renew')
    serve = commands.add_parser('serve-ui', help='Serve an explicit instance or a workspace for configuration and opening instances')
    ui_source = serve.add_mutually_exclusive_group(required=True)
    ui_source.add_argument('--root', type=Path)
    ui_source.add_argument('--workspace', type=Path, help='Existing explicit directory; opens management before research preparation')
    serve.add_argument('--instance-root', type=Path, action='append', default=[], help='Additional explicit instance choice; repeatable, no directory discovery')
    serve.add_argument('--approval-secret-file', type=Path, help='Operator-owned regular 0600 browser host key; defaults to frozen ui.approval_secret_file')
    serve.add_argument('--translation-config', type=Path, help='Optional application translation TOML; independent of frozen scientific profiles')
    serve.add_argument('--port', type=int, help='Loopback port; defaults to frozen ui.port or an available port')
    export = commands.add_parser('archive-export', help='Copy quiescent framework originals to a new inert read-only directory package; no writable restore')
    export.add_argument('--root', type=Path, required=True)
    export.add_argument('--output', type=Path, required=True)
    inspect = commands.add_parser('archive-inspect', help='Verify a read-only package and page its complete manifest without running code or importing plugins')
    inspect.add_argument('--package', type=Path, required=True)
    inspect.add_argument('--after', type=int, default=0, help='Exact prior entry index; default starts at the first entry')
    inspect.add_argument('--limit', type=int, default=20, help='Returned page size; all package bytes are still verified')
    inspect.add_argument('--section', choices=['manifest', 'dependencies'], default='manifest')
    restore = commands.add_parser('archive-restore-material', help='Restore one missing ordinary sealed file to its original instance; never restore control/call state or continue research')
    restore.add_argument('--root', type=Path, required=True)
    restore.add_argument('--package', type=Path, required=True)
    restore.add_argument('--step', type=int, required=True)
    restore.add_argument('--index', type=int, required=True, help='Exact member index from the sealed scientific materials page')
    enroll = commands.add_parser('checkpoint-enroll', help='Explicitly bind a quiescent instance to an external same-host cold-transfer anchor')
    enroll.add_argument('--root', type=Path, required=True)
    enroll.add_argument('--anchor-directory', type=Path, required=True)
    cold = commands.add_parser('checkpoint-export', help='Freeze this enrolled generation and export its complete cold state; never delete the source root')
    cold.add_argument('--root', type=Path, required=True)
    cold.add_argument('--output', type=Path, required=True)
    resume_cold = commands.add_parser('checkpoint-restore', help='Restore a registered cold checkpoint only to its absent original same-host root; no automatic continuation')
    resume_cold.add_argument('--root', type=Path, required=True)
    resume_cold.add_argument('--package', type=Path, required=True)
    arguments = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(arguments)
    root_arg = getattr(args, 'root', None)
    root = ((root_arg.expanduser().absolute() if args.command in {'archive-export', 'archive-restore-material','checkpoint-enroll','checkpoint-export','checkpoint-restore'} else root_arg.expanduser().resolve())
            if root_arg is not None else None)
    try:
        workspace_ui = args.command == 'serve-ui' and args.workspace is not None
        if args.command not in {'prepare','config-check','config-inspect','status','archive-inspect','checkpoint-enroll','checkpoint-export','checkpoint-restore','fork-preview','fork-create'} and not workspace_ui:
            from .protocol import require_current
            require_current(root)
        if args.command in {'fork-preview', 'fork-create'}:
            from . import fork_service
            options = dict(budget=dict(max_tokens=args.max_tokens, max_seconds=args.max_seconds, max_steps=args.max_steps),
                guidance_text=contracts.regular(args.guidance_file, limit=256000).read_text(encoding='utf-8') if args.guidance_file is not None else None)
            if args.command == 'fork-preview':
                result = fork_service.preview(root, args.step, args.child_root, **options)
            else:
                result = fork_service.create(root, args.step, args.child_root, **options,
                    expected_preview_id=args.expected_preview_id, operation_id=args.operation_id)
        elif args.command in {'config-check', 'config-inspect'}:
            result = research_config.inspect(config=args.config, root=root)
        elif args.command == 'prepare':
            if args.config is not None:
                supplied = [token.split('=', 1)[0] for token in arguments[1:] if token.startswith('--')]
                if any(token != '--config' for token in supplied):
                    raise ValueError('--config cannot be combined with preparation flags')
                options = research_config.load(args.config)
                root = options['root']
                settings = research_config.prepare(**options)
            else:
                required = ('root', 'objective', 'public_root', 'native_launcher_json', 'max_tokens', 'max_seconds', 'max_steps')
                missing = [name for name in required if getattr(args, name) is None]
                if missing:
                    raise ValueError('Preparation requires --config or explicit ' + ', '.join('--' + name.replace('_', '-') for name in missing))
                role_profiles = None
                if args.role_profiles is not None:
                    role_profiles = contracts.read(contracts.regular(args.role_profiles, limit=4096))
                    if not isinstance(role_profiles, dict):
                        raise ValueError('Role profiles file must contain a JSON object')
                launcher=json.loads(args.native_launcher_json)
                resume_policy=contracts.read(contracts.regular(args.worker_resume_policy,limit=4096)) if args.worker_resume_policy is not None else None
                node_policy=contracts.read(contracts.regular(args.node_budget_policy,limit=4096)) if args.node_budget_policy is not None else None
                settings = research_config.prepare(root=root, objective=args.objective, public_root=args.public_root,
                    launcher=launcher, max_tokens=args.max_tokens, max_seconds=args.max_seconds,
                    native_backend=args.native_backend,
                    native_options=json.loads(args.native_options_json) if args.native_options_json is not None else None,
                    max_steps=args.max_steps, max_output_item_bytes=args.max_output_item_bytes, max_output_total_bytes=args.max_output_total_bytes, model=args.model, reasoning_effort=args.reasoning_effort, node_budget_mode=args.node_budget_mode,
                    service_tier=args.service_tier, user_updates=args.user_update,
                    role_profiles=role_profiles,
                    experiment_mode=args.experiment_mode,
                    execution_config=read_execution_selection(args.execution_config) if args.execution_config is not None else None,
                    source_restrictions=contracts.read(contracts.regular(args.source_restrictions)) if args.source_restrictions is not None else None,
                    formal_review_policy=dict(version=1) if args.enable_formal_review else None,
                    execution_authority_policy=dict(version=1) if args.enable_execution_authority else None,
                    execution_resource_policy=contracts.read(contracts.regular(args.execution_resource_policy)) if args.execution_resource_policy is not None else None,
                    candidate_audit_policy={'version':1} if args.enable_candidate_audit else None,
                    decision_contract_version=1 if args.decision_contract else None,
                    portfolio_policy=dict(review_after_cycles=args.portfolio_review_after_cycles) if args.portfolio_review_after_cycles is not None else None,
                    search_policy=contracts.read(contracts.regular(args.search_policy, limit=4096))
                        if args.search_policy is not None else search.DEFAULT_POLICY,
                    correction_policy=contracts.read(contracts.regular(args.correction_policy, limit=4096))
                        if args.correction_policy is not None else None,
                    node_budget_policy=node_policy,
                    delivery_policy=contracts.read(contracts.regular(args.delivery_policy, limit=64_000))
                        if args.delivery_policy is not None else None,
                    worker_fragment_policy=dict(context=args.worker_context) if args.worker_context else None,
                    role_budget_policy=contracts.read(contracts.regular(args.role_budget_policy, limit=4096))
                        if args.role_budget_policy is not None else None,
                    selection_policy=contracts.read(contracts.regular(args.selection_policy, limit=4096))
                        if args.selection_policy is not None else None,
                    candidate_extra_slots=args.candidate_extra_slots,
                    resume_policy=resume_policy,
                    history_observer=contracts.read(contracts.regular(args.history_observer_config, limit=64_000))
                        if args.history_observer_config is not None else None)
            result = dict(status='prepared', instance=str(root), goal_id=settings['goal_id'])
            if settings.get('execution_config') is not None:
                result['execution']=dict(executor=settings['execution_config']['executor'],
                    capabilities_status='awaiting_host_task_configuration')
        elif args.command == 'serve-ui':
            ui = {} if workspace_ui else contracts.settings(root).get('ui', {})
            port = args.port if args.port is not None else ui.get('port', 0)
            secret_file = args.approval_secret_file or ui.get('approval_secret_file')
            if secret_file is None:
                raise ValueError('serve-ui requires --approval-secret-file or frozen ui.approval_secret_file')
            if not 0 <= port <= 65535:
                raise ValueError('UI port must be between 0 and 65535')
            from . import instance_ui
            options = dict(workspace=True) if workspace_ui else {}
            if args.translation_config is not None:
                from . import ui_translation
                options["translation_config"] = ui_translation.load_config(args.translation_config)
            result = instance_ui.serve(args.workspace if workspace_ui else root,
                secret_file=Path(secret_file), port=port, instance_roots=args.instance_root, **options)
        elif args.command == 'progress':
            from . import progress
            result = progress.read(root, args.step, attempt=args.attempt, cursor=args.cursor, limit_bytes=args.limit_bytes)
        elif args.command == 'start':
            result = instance_control.start_background(root, continue_run=args.continue_run,
                ack_correction=args.ack_correction, authorization_note=args.authorization_note, resume_step=args.resume_step)
        elif args.command in {'result', 'materials', 'artifact'}:
            from . import instance_science
            if args.command == 'result':
                with instance_science.science(root, args.step) as original:
                    original.copy_to(sys.stdout.buffer)
                return 0
            if args.command == 'materials':
                result = instance_science.materials(root, args.step, after=args.after, limit=args.limit)
            else:
                output = args.output.expanduser().absolute()
                original, name = instance_science.artifact(root, args.step, args.index)
                with original, tempfile.NamedTemporaryFile(dir=output.parent) as temporary:
                    original.copy_to(temporary)
                    temporary.flush(); os.fsync(temporary.fileno())
                    os.link(temporary.name, output, follow_symlinks=False)
                result = dict(status='artifact_downloaded', step=args.step, index=args.index, name=name, size_bytes=original.size)
        elif args.command == 'operations':
            result = (instance_control.operation(root, args.operation_id) if args.operation_id is not None
                      else dict(status='operations', operations=instance_control.operations(root)))
        elif args.command.startswith('guidance-'):
            from . import user_guidance
            if args.command == 'guidance-submit':
                result = user_guidance.submit(root, args.text, target=args.target, kind=args.kind,
                    scope=args.scope, effective_step=args.effective_step)
            elif args.command == 'guidance-edit':
                result = user_guidance.edit(root, args.guidance_id, args.text, expected_revision=args.expected_revision)
            elif args.command == 'guidance-revoke':
                result = user_guidance.revoke(root, args.guidance_id, expected_revision=args.expected_revision)
            else:
                result = dict(status='guidance_listed', guidance=user_guidance.list_guidance(root))
        elif args.command == 'archive-export':
            from .instance_archive import archive_export
            result = archive_export(root, args.output)
        elif args.command == 'archive-inspect':
            from .instance_archive import archive_inspect
            result = archive_inspect(args.package, after=args.after, limit=args.limit, section=args.section)
        elif args.command == 'archive-restore-material':
            from .instance_archive import archive_restore_material
            result = archive_restore_material(root, args.package, step=args.step, index=args.index)
        elif args.command == 'checkpoint-enroll':
            from .instance_archive import checkpoint_enroll
            result = checkpoint_enroll(root,args.anchor_directory)
        elif args.command == 'checkpoint-export':
            from .instance_archive import checkpoint_export
            result = checkpoint_export(root,args.output)
        elif args.command == 'checkpoint-restore':
            from .instance_archive import checkpoint_restore
            result = checkpoint_restore(root,args.package)
        elif args.command == 'execution-recover':
            result=controller.execution_recover(root,args.step)
        elif args.command == 'execution-resume':
            result=controller.execution_resume(root,args.step,args.call_id,max_seconds=args.max_seconds,
                authorization_note=args.authorization_note,request_valid_seconds=args.request_valid_seconds,maintenance=args.maintenance)
        elif args.command == 'native-startup-collect':
            from . import startup_recovery
            result = startup_recovery.collect_delivery(root,args.step,reconciliation=args.reconciliation,
                reconciliation_sha256=args.reconciliation_sha256,authorization_statement=args.authorization_statement)
        elif args.command == 'native-startup-successor':
            from . import startup_recovery
            result = startup_recovery.start(root,args.step,reconciliation=args.reconciliation,
                reconciliation_sha256=args.reconciliation_sha256,
                native_home=args.native_home,authorization_statement=args.authorization_statement)
        elif args.command == 'worker-closeout':
            result = controller.worker_closeout(root, args.step, max_tokens=args.max_tokens, max_seconds=args.max_seconds,
                authorization_note=args.authorization_note,successor=args.successor,
                authorization_statement=args.authorization_statement)
        elif args.command == 'worker-output-accept':
            result=controller.worker_output_accept(root,args.step,attempt=args.attempt,pending_sha256=args.pending_sha256,
                session=args.session,
                authorization_note=args.authorization_note,authorization_statement=args.authorization_statement)
        elif args.command == 'worker-resume':
            result = controller.worker_resume(root, args.step)
        elif args.command == 'status':
            result = status(root)
        elif args.command == 'closeout':
            result = controller.closeout(root, export_only=args.export_only)
        elif args.command == 'budget-amend':
            resource_change = args.execution_wall_seconds is not None or args.execution_storage_bytes is not None
            if resource_change and (args.execution_wall_seconds is None or args.execution_storage_bytes is None):
                raise ValueError('Execution resource amendment requires both explicit dimensions')
            if resource_change and (args.max_tokens is not None or args.max_seconds is not None or args.max_steps is not None or args.authorization_statement is not None):
                raise ValueError('Amend scientific or execution ceilings in one explicit operation at a time')
            if resource_change:
                grant = budget.amend_execution_grant(root, allowance=dict(wall_time_seconds=args.execution_wall_seconds,
                    max_storage_bytes=args.execution_storage_bytes), authorization_note=args.authorization_note)
                result = dict(status='budget_amended', execution_resources=grant,
                              permission='Ceiling configuration only; no exact-call execution decision')
            else:
                result = instance_control.request_budget_amendment(root, max_tokens=args.max_tokens, max_seconds=args.max_seconds, max_steps=args.max_steps,
                                      authorization_note=args.authorization_note, authorization_statement=args.authorization_statement)
        elif args.command == 'pause':
            result = request_pause(root)
        elif args.command == 'continue':
            result = continue_instance(root, ack_correction=args.ack_correction,
                                       authorization_note=args.authorization_note)
        else:
            result = controller.run(root)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps(dict(status='error',error=str(error),**(dict(code=error.code) if hasattr(error, 'code') else {})), ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    if args.command == 'execution-resume' and args.maintenance is not None:
        from scidiscovery.operations.execution import PENDING_EXECUTION_STATES, execution_status
        # Remote failure is a successfully maintained original, not a failed
        # control operation. Unknown facts still require reconciliation.
        raw = result.get('state') if args.maintenance == 'query' else result.get('status')
        state = raw if raw in ('known_success', 'known_failed') else execution_status(raw)
        recognized = {'known_success', 'known_failed'}
        if args.maintenance in {'query', 'cancel'}:
            recognized.update(PENDING_EXECUTION_STATES)
        return 0 if state in recognized else 2
    return 0 if result.get('status') in {None, 'fork_preview_ready', 'fork_created', 'preserved_read_only', 'prepared', 'finish', 'final_a_completed', 'wait', 'paused', 'pause_requested', 'budget_amended', 'delivery_confirmed', 'execution_recovered', 'execution_resume_recorded', 'exported_unsubmitted', 'ui_stopped', 'archive_exported', 'archive_inspected', 'material_restored', 'material_already_present','checkpoint_enrolled','checkpoint_exported','checkpoint_restored','checkpoint_restore_already_completed','execution_maintained','config_checked','config_inspected','accepted','running','applied','operations','guidance_listed','pending','revoked','progress_read','progress_unavailable','artifact_downloaded'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
