"""Registered scientific execution; one owner of the original call receipts.

No model execution, domain parser, subprocess launch or Artifact service lives here.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import contextmanager, ExitStack
from dataclasses import asdict
import fcntl
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
from functools import wraps

from . import breadth, configuration_updates
from . import instance_owner

from scidiscovery.operations.execution import (AdapterCapability, ExecutionInterfaceRegistration,
    LifecycleCapabilities, LocalFileDescriptor, MANAGED_METHODS, lifecycle_capabilities, LOCAL_RESULT_PROTOCOL,
    PENDING_EXECUTION_STATES, execution_status)
from scidiscovery.operations.runtime_plugins import (RuntimePluginContext, RuntimePluginContribution,
    HOST_EXECUTION_GATE_KEY, HOST_EXECUTION_GATE_SCHEMA, HOST_EXECUTION_GATE_CONTRACT)

GROUP = 'scidiscovery.execution_interfaces'
VERSION = 1
OBSERVATION_VERSION = 1
ADMISSION_VERSION = 1
MAX_BYTES = 256_000
MAX_ASSET = 32 * 1024 * 1024
ID = re.compile(r'[A-Za-z0-9._-]{1,128}\Z')
RETIREMENT_KEY = 'original_submission_retirement'
RETIREMENT_SCHEMA = 'scidiscovery.permanent-submission-retirement.v1'
RETIREMENT_CONTRACT = dict(version=1, identity='original_call_and_prepared_path',
    absent='permanent_not_submitted', invocation='context_manager', refunds=False)
INPUT_SOURCES_KEY = 'managed_input_sources'
INPUT_SOURCES_SCHEMA = 'scidiscovery.managed-input-sources.v1'
INPUT_SOURCES_CONTRACT = dict(version=1, view='exact_snapshot_sources',
    authoring='current_job_experiment_directory', inputs='frozen_job_originals_and_read_copies',
    scope='framework_declared_inputs_only')


class IntegrityError(ValueError):
    pass


class UnsupportedExecution(ValueError):
    pass


class CommandOptionsError(ValueError):
    """Unsupported caller options rejected before preparation or admission."""


class ReviewRequired(ValueError):
    pass


class _AdmissionReviewRequired(ReviewRequired):
    """A control check proved this dispatch was not accepted."""


class _BudgetAdmissionUnavailable(ReviewRequired):
    """A trusted host gate refused new work; preparation may still be unresolved."""
    _REASONS = {
        'review_deadline_reached': ('budget_exhausted', 'shared_time_exhausted'),
        'reserve_reached': ('budget_exhausted', 'shared_budget_exhausted'),
        'shared_usage_unknown': ('budget_unavailable', 'shared_usage_unknown'),
    }

    def __init__(self, reason):
        self.status, self.reason = self._REASONS[reason]
        self._review_reason = reason
        super().__init__(self.reason)


class _AdmissionBudgetRefused(_BudgetAdmissionUnavailable):
    """The budget gate refused this dispatch before any external side effect."""


class _AdmissionOpportunityRequired(breadth.OpportunityRequired):
    """A control check proved this dispatch was not accepted."""


def _raise_admission_refusal(error):
    if isinstance(error, _BudgetAdmissionUnavailable):
        raise _AdmissionBudgetRefused(error._review_reason) from error
    refusal = _AdmissionReviewRequired if isinstance(error, ReviewRequired) else _AdmissionOpportunityRequired
    raise refusal(str(error)) from error


class ConcurrentExecution(ValueError):
    pass


class LocalPreparationCleanupError(ValueError):
    """The current unsubmitted local preparation could not be removed."""


def _configuration_effect(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        first = args[0] if args else kwargs['config']
        config = first.config if isinstance(first, Records) else first
        if config.get('scope') is not None:
            from .protocol import require_scope
            require_scope(config['scope'])
        from . import contracts as c, storage_policy as storage, experiment_client
        root = experiment_client.root_for_configuration(config, allow_standalone=True)
        if root is not None:
            from . import shared_input_views
            shared_input_views.dynamic_root(root,c.workspace(root,config['scope']['step']),'retained-experiments')
        with instance_owner.lease_for_configuration(config), c.record_writer(Path(config['record_dir'])), storage.bind(root,config.get('storage_policy')):
            return function(*args, **kwargs)
    return guarded


def record_effect(function):
    @wraps(function)
    def guarded(self,*args,**kwargs):
        if self.config.get('scope') is not None:
            from .protocol import require_scope
            require_scope(self.config['scope'])
        from . import contracts as c, storage_policy as storage, experiment_client
        root = experiment_client.root_for_configuration(self.config, allow_standalone=True)
        if root is not None:
            from . import shared_input_views
            shared_input_views.dynamic_root(root,c.workspace(root,self.config['scope']['step']),'retained-experiments')
        with instance_owner.lease_for_configuration(self.config), c.record_writer(self.records.root), storage.bind(root,self.config.get('storage_policy')):
            return function(self,*args,**kwargs)
    return guarded


def _require_new_execution(config):
    """Narrow result repair cannot grant new scientific work through a client."""
    from . import contracts as c, execution, experiment_client
    root=experiment_client.root_for_configuration(config,allow_standalone=True)
    if root is None:return
    step=config['scope']['step']
    intents=list(execution.accepted_intents(root,step))
    attempt=config.get('native_attempt')
    if (not isinstance(attempt,dict) or set(attempt)!={'number','execution_key'}
            or type(attempt.get('number')) is not int or not 0<=attempt['number']<len(intents)):
        raise IntegrityError('New scientific work requires its exact accepted native attempt')
    number=attempt['number'];record,intent=intents[number]
    if attempt['execution_key']!=c.record_ref(root/'jobs'/f'{step:03d}.json' if number==0 else record/'native-intent.json'):
        raise IntegrityError('New scientific work differs from its exact accepted native intent')
    # The current bridge is bound to its own attempt. An old retained client
    # cannot lend its earlier full-task grant to the latest narrow repair.
    if intent.get('output_repair') is not None or intents[-1][1].get('output_repair') is not None:
        raise UnsupportedExecution('Output repair permits original results only; no new scientific execution or compilation')
    job=c.read(c.regular(root/'jobs'/f'{step:03d}.json'))
    if not (configuration_updates.for_job(root,job).get('resume_policy') or {}).get('output_only'):return
    paths=execution.records(root,step)
    for path in paths[1:]:
        intent=c.read(c.regular(path/'native-intent.json'))
        if intent.get('continuation_kind')=='execution_wake':
            raise UnsupportedExecution('Output-only execution wake permits original results only; no new managed execution')


def _review_observation(value, stamp):
    """Interpret the same live or archived host observation without new grants."""
    reason=('host_review_observation_unavailable' if value is None else
        'review_deadline_reached' if stamp['at_boottime']>=value['deadline_boottime'] else
        'host_review_observation_expired' if stamp['at_boottime']>=value['valid_until_boottime'] else
        value['reason'] if value['pending'] else None)
    return dict(requested=bool(reason),reason=reason,
        enforced=reason not in {'target_tokens_reached','target_time_reached',
            'host_review_observation_expired','host_review_observation_unavailable'},inflight_usage_unknown=True)


def review_status(config):
    """Read host facts; a missing lease or deadline never becomes a new grant."""
    admission=config.get('admission')
    if not isinstance(admission,dict) or type(admission.get('version')) is not int or admission['version']!=ADMISSION_VERSION:
        raise UnsupportedExecution('Preserved admission contract is read-only; prepare a new instance')
    if admission.get('mode')=='hard':return dict(requested=False,reason=None,enforced=False,inflight_usage_unknown=True)
    if admission.get('mode')!='soft':raise UnsupportedExecution('Frozen review applicability is unavailable; prepare an applicable admission contract')
    from . import budget
    stamp=budget.clock_stamp()
    try:
        path=Path(admission['directory'])/'review.json'
        if path.resolve()!=path:raise IntegrityError('Host review path changed')
        path.lstat()  # Missing advice is distinct from a damaged existing original.
        value=json.loads(read(path,16_000))
        if (value['scope']!=config['scope'] or value['stage_anchor']!=admission['stage_anchor']
                or value['native_attempt']!=config['native_attempt'] or value['boot_id']!=stamp['boot_id']):
            raise IntegrityError('Host review facts differ from the frozen stage or native attempt')
        deadline,lease=value['deadline_boottime'],value['valid_until_boottime']
        if (type(deadline) not in (int,float) or not 0<deadline<float('inf')
                or type(lease) not in (int,float) or not 0<lease<float('inf')):
            raise IntegrityError('Host review facts have no finite original deadline')
        return _review_observation(value,stamp)
    except FileNotFoundError:
        return _review_observation(None,stamp)
    except (KeyError,TypeError) as exc:
        raise IntegrityError('Host review facts are malformed') from exc


def review_feedback(config):
    """Optional current advice cannot erase an original operation's confirmed result."""
    try:return review_status(config)
    except (OSError,ValueError,KeyError,TypeError):
        return dict(requested=None,reason='current_review_observation_unavailable',enforced=False,inflight_usage_unknown=True)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode()


def read(path, limit=None):
    path = Path(path)
    if path.resolve() != path or not path.is_file() or limit is not None and path.stat().st_size > limit:
        raise IntegrityError('Exact bounded execution original required')
    return path.read_bytes()


def proof(path, limit=None, *, context=None):
    from . import contracts as c
    if context is not None:context.remaining_seconds()
    path=c.regular(Path(path),limit=limit)
    from scidiscovery import archive_io
    value=archive_io.stat_file(path,context=context)
    device,inode,modified,_=value['source_stat']
    value=dict(path=str(path),size_bytes=value['size_bytes'],
        file_stat=[device,inode,value['size_bytes'],modified])
    if context is not None:context.remaining_seconds()
    return value


def write(path, value, *, root=None, policy=None, context=None):
    from . import atomic_files
    from . import storage_policy as storage
    path=Path(path);data=encoded(value)
    with storage.mutation(root,0 if path.exists() else len(data),policy=policy,context=context or storage.Deadline(65)):
        atomic_files.publish_bytes(path, data)


def registration(executor):
    if not isinstance(executor,str):raise ValueError('Exact registered executor key required')
    plugin, separator, binding = executor.partition(':')
    if not separator or not ID.fullmatch(plugin) or not ID.fullmatch(binding):
        raise ValueError('Exact registered executor key required')
    matches = list(importlib.metadata.entry_points().select(group=GROUP, name=plugin))
    if len(matches) != 1:
        raise UnsupportedExecution('Registered execution implementation unavailable')
    try:value = matches[0].load()
    except (ImportError,AttributeError) as exc:
        raise UnsupportedExecution('Registered execution implementation cannot be loaded') from exc
    if not isinstance(value, ExecutionInterfaceRegistration) or value.plugin_id != plugin:
        raise ValueError('Execution registration identity differs from its entry point')
    return value, binding


HOST_CONFIGURATION_FIELDS = frozenset({'scope','record_dir','native_attempt','admission','breadth',
    'engineering_repair','execution_interface','registered_implementation', 'host_execution_authority',
    'execution_host_services', 'frozen_execution_interface', 'worker_view_version', 'host_launch_implementation',
    'storage_policy'})


def operator_configuration(value, base=None):
    """Freeze public operator settings without constructing an adapter or fake task."""
    from scidiscovery.plugin_configuration import (PluginConfigurationError, configuration_path, read_configuration)
    if not isinstance(value,dict) or set(value) not in ({'executor','configuration'}, {'executor','config_file'}):
        raise PluginConfigurationError('plugin_config_selection_invalid', '请选择执行插件及一种配置来源。',
            field_path='executor.config.executor', hint='填写 plugin:binding 执行器及 config_file，或插件支持的内联 configuration。')
    try:
        declared, binding = registration(value['executor'])
    except (ValueError, UnsupportedExecution):
        candidate = value.get('executor')
        plugin_id = candidate.partition(':')[0] if isinstance(candidate,str) and ID.fullmatch(candidate.partition(':')[0]) else None
        raise PluginConfigurationError('plugin_executor_unavailable', '执行插件不可用或执行器标识无效。',
            field_path='executor.config.executor', hint='请检查 plugin:binding 标识并安装对应插件。', plugin_id=plugin_id) from None
    source = None
    if 'config_file' in value:
        source = configuration_path(value['config_file'], base=base, plugin_id=declared.plugin_id)
        configuration = read_configuration(source, plugin_id=declared.plugin_id)
    else:
        if declared.factory.requires_config_file:
            raise PluginConfigurationError('plugin_config_file_required', '所选执行插件要求独立配置文件。',
                hint='请填写 config_file，指向插件的 JSON 或 TOML 配置文件。', plugin_id=declared.plugin_id)
        configuration = value['configuration']
    if not isinstance(configuration,dict) or HOST_CONFIGURATION_FIELDS.intersection(configuration):
        raise PluginConfigurationError('plugin_config_fields_invalid', 'Operator execution configuration must be an object and cannot supply host identity or admission fields.',
            hint='请移除宿主身份或执行授权字段。', plugin_id=declared.plugin_id)
    if declared.factory.configure is not None:
        try:
            configuration = declared.factory.configure(configuration, source, binding=binding)
        except PluginConfigurationError:
            raise
        except (OSError, ValueError, TypeError):
            raise PluginConfigurationError('plugin_config_invalid', '插件配置未通过本地检查。',
                hint='请依据插件文档修正配置字段和引用文件。', plugin_id=declared.plugin_id) from None
    if not isinstance(configuration,dict) or HOST_CONFIGURATION_FIELDS.intersection(configuration):
        raise ValueError('Plugin configuration callback returned invalid operator settings')
    # Round-trip also rejects non-JSON/nonfinite settings before creating an instance.
    try:
        body=json.loads(encoded(dict(executor=value['executor'],configuration=configuration)))
    except (ValueError, TypeError):
        raise PluginConfigurationError('plugin_config_invalid', '插件配置包含不支持的值。',
            hint='请使用有限数字及可序列化的 JSON 配置值。', plugin_id=declared.plugin_id) from None
    return body


def validate_operator_configuration(value):
    if not isinstance(value,dict) or set(value) not in ({'executor','configuration'},{'executor','configuration','implementation'}):
        raise IntegrityError('Frozen operator execution configuration is malformed')
    if not isinstance(value['configuration'],dict) or HOST_CONFIGURATION_FIELDS.intersection(value['configuration']):
        raise IntegrityError('Frozen operator execution configuration is malformed')
    encoded(value)
    registration(value['executor'])
    return value


def _interface_contract(value):
    """Installed source/version metadata does not amend an execution contract."""
    return {key:item for key,item in value.items() if key not in {'code','plugin_version'}}


def _adapter(config, path, *, host_services=None):
    selected = config['execution_interface']
    declared, binding = registration(selected['executor'])
    body = {key: value for key, value in config.items() if key != 'execution_interface'}
    from . import execution_authority
    services = host_services if host_services is not None else execution_authority.host_services(config)
    contribution = declared.factory.build(RuntimePluginContext(declared.plugin_id, 'local_worker',
        Path(path), encoded(body), Path(config['record_dir']), execution_host_services=services,
        frozen_execution_interface=selected))
    if not isinstance(contribution, RuntimePluginContribution) or binding not in contribution.execution_adapters:
        raise UnsupportedExecution('Registered execution binding unavailable')
    adapter = contribution.execution_adapters[binding]
    if any(not callable(getattr(adapter,name,None)) for name in MANAGED_METHODS):
        raise UnsupportedExecution('Implementation does not provide the managed execution interface')
    if services is not None:
        gates=[item for item in adapter.capabilities() if item.key==HOST_EXECUTION_GATE_KEY]
        if (len(gates)!=1 or gates[0].schema_id!=HOST_EXECUTION_GATE_SCHEMA
                or gates[0].payload_schema_version!=1 or gates[0].kind!='execution_authority'
                or gates[0].content!=encoded(dict(HOST_EXECUTION_GATE_CONTRACT))):
            raise UnsupportedExecution('Registered implementation does not declare the trusted host execution gate')
    return declared, adapter, body


def _public_capabilities(adapter):
    result=[]
    for item in adapter.capabilities():
        if (not isinstance(item,AdapterCapability) or not isinstance(item.key,str) or not ID.fullmatch(item.key)
                or any(not isinstance(value,str) or not value
                       for value in (item.kind,item.schema_id,item.media_type))
                or type(item.payload_schema_version) is not int or item.payload_schema_version<=0
                or not isinstance(item.content,bytes) or not isinstance(item.public_summary,Mapping)
                or any(previous['key']==item.key for previous in result)):
            raise IntegrityError('Registered public capability contract is malformed')
        result.append(dict(key=item.key,kind=item.kind,schema_id=item.schema_id,
            payload_schema_version=item.payload_schema_version,media_type=item.media_type,
            public_summary=dict(item.public_summary),size_bytes=len(item.content)))
    try:encoded(result)
    except (ValueError,TypeError) as exc:
        raise IntegrityError('Registered public capability metadata is not finite JSON') from exc
    return result


def _worker_capabilities(adapter):
    """Producer-owned science contracts, separate from frozen host metadata."""
    method = getattr(adapter, 'worker_capabilities', None)
    items = tuple(method() if callable(method) else adapter.capabilities())
    return tuple(item for item in items if item.key not in {
        HOST_EXECUTION_GATE_KEY, RETIREMENT_KEY, INPUT_SOURCES_KEY})


def _capability_view(item):
    return dict(key=item.key, kind=item.kind, schema_id=item.schema_id,
        payload_schema_version=item.payload_schema_version, media_type=item.media_type,
        public_summary=dict(item.public_summary))


def _status_view(value):
    """Status metadata is a control object; scientific metrics retain their names."""
    result = {key: value[key] for key in ('state', 'call_id', 'candidate_id', 'evaluation_key',
        'metric', 'direction', 'wall_seconds', 'parent_candidate', 'returncode', 'result',
        'submission_disposition') if key in value}
    if isinstance(result.get('result'),dict):result['result']=_run_view(result['result'])
    return result


def _scientific_result(value):
    # These fields belong to the declared local scientific-result protocol.
    # Metric names and author-owned output labels are preserved verbatim.
    result = {key:value[key] for key in ('status','metrics','outputs','candidates','diagnostic',
        'metrics_omitted_count','outputs_omitted_count','diagnostics','details_ref',
        'candidates_omitted_count','diagnostics_omitted_count') if key in value}
    # The optional-result parser emits fixed field diagnostics, never exception
    # text or control objects. Preserve that bounded explanation at every view.
    if value.get('status') == 'invalid' and value.get('diagnostic') in {'result_json_invalid','result_io'} and isinstance(value.get('detail'),str):
        result['detail'] = value['detail'].encode('utf-8')[:512].decode('utf-8',errors='ignore')
    return result


def _accounting_view(value):
    return {key:value[key] for key in ('state','call_id','actual','retained_dimensions') if key in value}


def _run_view(value):
    """The adapter owns its public science shape; control enforces finite JSON."""
    if not isinstance(value, dict):
        raise IntegrityError('Registered public result is not an object')
    result=json.loads(encoded(value))
    if len(encoded(result)) > MAX_BYTES:
        raise IntegrityError('Registered public result exceeds the public response bound')
    if isinstance(result.get('authorization_request'),dict):
        result['authorization_request']=dict(status='execution_decision_required')
    if isinstance(result.get('resource_accounting'),dict):
        result['resource_accounting']=_accounting_view(result['resource_accounting'])
    return result


def _source_projection(config, accepted):
    """Project a declared file or its permanent code identity, without exporting code."""
    declared,_=registration(config['execution_interface']['executor'])
    return _source_projection_from_provider(config,accepted,declared.scientific_source)


def _source_projection_from_provider(config, accepted, scientific_source):
    """Use an already selected provider with the same original-source checks."""
    implementation=None;version=None;code_entry=None
    if 'code_version' in accepted:
        root=_code_version_binding(config,accepted)
        version=dict(accepted['code_version']);code_entry=_code_export_entry(root,accepted)
    if accepted.get('execution_authority') is not None:
        from . import experiment_client,contracts as c,execution_authority,execution_inputs,originals
        root=experiment_client.root_for_configuration(config)
        job=c.read(c.regular(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
        metadata=_source_control_metadata(config,accepted,verify_signatures=True)
        if metadata is not None and metadata['compiler'] is not None:
            subject=metadata['subject'];compiled=metadata['compiled'];compiler=metadata['compiler']
            if metadata['code_version'] is not None:
                compiler_version=metadata['code_version']
                if version is not None and version!=compiler_version:
                    raise IntegrityError('Scientific source has conflicting accepted and compiler code versions')
                version=compiler_version
            elif scientific_source is not None:
                # Legacy author-file projections keep their original scientific
                # validation. New Git metadata never takes this consumption path.
                binding=execution_authority.accepted_input_subject(root,job,accepted)
                if binding is not None:
                    owner=(originals.for_origin(root,subject['producer_seal']['origin_identity'])
                        if 'origin_identity' in subject['producer_seal'] else originals.node(root,subject['producer_step']))
                    compiler=execution_inputs.verify_compilation(root,compiled['compiler'],
                        material=subject['material'],client_binding=compiled.get('client_binding'),
                        **(dict(source=owner) if owner.inherited else {}))
                    author=compiler['author']
                    actual=(originals.resolve_ref(originals.source_for_ref(owner,author),author)
                        if owner.inherited else originals.resolve_root_ref(root,author))
                    implementation=dict(author,path=str(actual))
    source=(scientific_source(accepted,implementation=implementation)
        if scientific_source is not None else None)
    if (source is not None and code_entry is not None
            and {key:source.get(key) for key in ('path','size_bytes','file_stat')}==code_entry):source=None
    if source is not None and any(proof(source['path']).get(key)!=item for key,item in source.items()
            if key in {'path','size_bytes','file_stat'}):
        raise IntegrityError('Scientific source differs from its retained original')
    return source,version


def scientific_result(config, accepted, completed, observation):
    """Read the frozen public projection; legacy records gain no new meaning."""
    if 'public_result' in observation:
        return _run_view(observation['public_result'])
    result={key:completed[key] for key in ('status','native_state','returncode','wall_seconds') if key in completed}
    if config['execution_interface'].get('result_protocol')==LOCAL_RESULT_PROTOCOL and isinstance(completed.get('result'),dict):
        result['result']=_scientific_result(completed['result'])
    return result


def _diagnostic(error, fallback, *, config=None):
    # Provider/validator exception text can contain arbitrary private paths and
    # credentials. Keep the original only in the host diagnostic namespace.
    if isinstance(config,dict) and isinstance(config.get('record_dir'),str):
        try:
            from . import contracts as c
            directory=Path(config['record_dir']).parent/'execution-diagnostics'
            if directory.resolve()!=directory:raise ValueError('Diagnostic namespace changed')
            c.write(directory/(uuid.uuid4().hex+'.json'),dict(error=type(error).__name__,note=str(error)))
        except (OSError,ValueError,TypeError):pass
    return fallback


def _candidate_view(config, value):
    """Only typed candidate choices use the host's exact readable reference map."""
    if instance_owner.configuration_root(config) is None:return value
    from . import candidate_refs, contracts as c,experiment_client
    root=experiment_client.root_for_configuration(config)
    job=c.read(c.regular(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
    def row(item):
        result=dict(item)
        for key in ('candidate_id','parent_candidate'):
            if key in result:result[key]=candidate_refs.public_reference(root,job,result[key])
        if isinstance(result.get('parent'),dict):
            result['parent']=dict(result['parent'])
            if 'candidate_id' in result['parent']:
                result['parent']['candidate_id']=candidate_refs.public_reference(root,job,result['parent']['candidate_id'])
        return result
    result=row(value)
    if isinstance(result.get('calls'),list):result['calls']=[row(item) for item in result['calls']]
    return result


def _scientific_projection_directory(config):
    from . import contracts as c,experiment_client
    root=experiment_client.root_for_configuration(config,allow_standalone=True)
    if root is not None:
        from . import shared_input_views
        shared_input_views.dynamic_root(root,c.workspace(root,config['scope']['step']),'retained-experiments')
    record_directory=Path(config['record_dir'])
    if root is None:
        parts=record_directory.parts
        if ('host-runtime' in parts or any(parts[index:index+2]==('project','nodes') for index in range(len(parts)-1))
                or not record_directory.is_absolute() or record_directory.resolve()!=record_directory
                or not record_directory.is_dir()):
            raise IntegrityError('Scientific projection has no canonical standalone host directory')
        # Standalone Records owns this whole explicit storage namespace. Views
        # stay beside calls/ so its CAS, lock, quotas and aliases share one root.
        projection_directory=record_directory/'scientific-output-views'
    else:
        task=c.record_ref(c.regular(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
        if config['scope'].get('job_ref')!=task:
            raise IntegrityError('Scientific projection differs from its exact original task')
        projection_directory=root/'host-runtime/experiment-control'/f'{config["scope"]["step"]:03d}'/'scientific-output-views'
    if (projection_directory.resolve()!=projection_directory
            or root is not None and projection_directory.is_relative_to(record_directory)
            or projection_directory.is_relative_to(record_directory/'calls')):
        raise IntegrityError('Scientific projection must remain outside original managed calls')
    projection_directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not projection_directory.is_dir():raise IntegrityError('Scientific projection directory is unavailable')
    return projection_directory


def _validated_scientific_outputs(selected, *, operation_context=None):
    names=set()
    for descriptor in selected:
        if not isinstance(descriptor,LocalFileDescriptor):raise IntegrityError('Scientific output descriptor is malformed')
        name=Path(descriptor.name)
        if (name.is_absolute() or '..' in name.parts or not name.parts or name.as_posix()!=descriptor.name
                or '\\' in descriptor.name or descriptor.name in names):
            raise IntegrityError('Scientific output requires distinct canonical names')
        names.add(descriptor.name)
        item=proof(descriptor.local_path,context=operation_context)
        if item['size_bytes']!=descriptor.size_bytes:
            raise IntegrityError('Scientific output original changed')
    return selected


def _local_original_observation(config, observation):
    """The Local producer's saved paths already name their actual originals."""
    if config['execution_interface'].get('executor')!='local_execution:command':return False
    rows=[item for name in ('proofs','outputs','scientific_outputs') for item in observation.get(name,[])]
    if observation.get('scientific_source') is not None:rows.append(observation['scientific_source'])
    if all(isinstance(item.get('path'),str) and item['path']==(item.get('source') or {}).get('path') for item in rows):
        return True
    # Existing observations have exact saved private slots. A changed new
    # descriptor must not select that compatibility branch and publish an alias.
    record_dir=config.get('record_dir') or config['execution_interface'].get('config_record',{}).get('record_dir')
    if not isinstance(record_dir,str):raise IntegrityError('Local saved observation lost its exact record directory')
    directory=Records(dict(config,record_dir=record_dir)).directory(observation['call_id'])/'observed-originals'
    if any(Path(item.get('path','')).parent!=directory
            or re.fullmatch(r'[0-9]{6}',Path(item.get('path','')).name) is None for item in rows):
        raise IntegrityError('Local observation lost its actual or saved original path')
    return False


def scientific_outputs(config, observation, *, adapter=None, include_logs=False, operation_context=None):
    """Producer-owned science files from an already verified full observation."""
    from . import storage_policy as storage
    context=operation_context or storage.Deadline(config.get('max_seconds',65))
    storage.check(context)
    if 'scientific_outputs' in observation:
        for item in observation['scientific_outputs']:
            current=proof(item['path'],context=context)
            if any(current[key]!=item[key] for key in ('size_bytes','file_stat') if key in item):
                raise IntegrityError('Saved scientific output original changed')
        return _validated_scientific_outputs(tuple(LocalFileDescriptor.model_validate(item['descriptor']).model_copy(update={'local_path':item['path']})
            for item in observation['scientific_outputs']),operation_context=context)
    descriptors=tuple(LocalFileDescriptor.model_validate(item['descriptor']).model_copy(update={'local_path':item['path']})
        for item in observation['outputs'])
    if adapter is None:
        # Old seals remain readable but cannot acquire a newly installed domain
        # projection. The explicit built-in local protocol has a stable reader.
        if config['execution_interface'].get('result_protocol')!=LOCAL_RESULT_PROTOCOL:
            raise UnsupportedExecution('Legacy scientific projection is not frozen; retain its sealed deliverables')
        method=None
    else:
        declared,_=registration(config['execution_interface']['executor'])
        method=declared.scientific_outputs or getattr(adapter,'scientific_outputs',None)
    if callable(method):
        call=observation.get('call_id')
        if not isinstance(call,str) or not ID.fullmatch(call):raise IntegrityError('Scientific projection has no exact original call')
        projection=_scientific_projection_directory(config)/'calls'/call
        if projection.resolve()!=projection:raise IntegrityError('Scientific projection call namespace changed')
        projection.mkdir(parents=True,exist_ok=True,mode=0o700)
        options=dict(projection_directory=projection)
        try:parameters=inspect.signature(method).parameters
        except (TypeError,ValueError):parameters={}
        if 'output_directory' in parameters:
            from . import local_experiments
            options['output_directory']=local_experiments.artifact_directory(config,call)
        selected=tuple(method(descriptors,**options))
    if config['execution_interface'].get('result_protocol')==LOCAL_RESULT_PROTOCOL:
        def original(reference):
            actual=proof(reference['path'],context=context)
            if any(actual[key]!=reference[key] for key in ('size_bytes','file_stat') if key in reference):
                raise IntegrityError('Scientific output completion original changed')
            storage.check(context)
            value=json.loads(read(Path(reference['path'])))
            storage.check(context)
            return value
        completed=original(observation['completed'])
        if any(completed.get(key)!=observation.get(key) for key in ('call_id','scope','mechanism_binding','status')):
            raise IntegrityError('Scientific outputs differ from their exact completed call')
        def collected(source):
            matches=[]
            for item,descriptor in zip(observation['outputs'],descriptors):
                saved=item['source']
                if (any(saved.get(key)!=source.get(key) for key in ('path','size_bytes'))
                        or descriptor.size_bytes!=source['size_bytes']):continue
                for reference in (saved,source):
                    if 'file_stat' in reference and proof(reference['path'],context=context)['file_stat']!=reference['file_stat']:
                        raise IntegrityError('Collected scientific original changed')
                matches.append(descriptor)
            if len(matches)!=1:raise IntegrityError('Scientific output has no exact collected original')
            return matches[0]
        selected=(list(selected) if callable(method) else [collected({key:output[key]
            for key in ('path','size_bytes','file_stat') if key in output}).model_copy(
                update={'name':output['name']}) for output in completed.get('outputs',[])])
        if include_logs:
            from . import shared_originals, scientific_log_projection, experiment_client
            limits=config.get('storage_policy')
            inventory=storage.Inventory(limits,context=context)
            for item in selected:
                inventory.entry();inventory.file(item.size_bytes)
            accepted=original(observation['accepted'])
            if any(accepted.get(key)!=observation.get(key) for key in ('call_id','scope','mechanism_binding')):
                raise IntegrityError('Scientific logs differ from their exact accepted call')
            directory=Path(config['record_dir'])/'calls'/observation['call_id']
            replacements={str(directory):f'[retained execution {observation["call_id"]}]',
                str(Path(config['record_dir'])):'[retained executions]',
                accepted['script']['path']:accepted['source_script']}
            if 'entry' in accepted:
                replacements[str(directory/'inputs')]=accepted['experiment_directory']
                for item in accepted['snapshot_files']:
                    replacements[str(directory/'inputs'/item['name'])]=item['path']
                replacements[str(directory/'outputs')]=str(Path(accepted['experiment_directory'])/'artifacts')
            replacements=scientific_log_projection.encode_replacements(replacements)
            storage.check(context)
            direct=_local_original_observation(config,observation)
            references=[item for key in ('proofs','outputs','scientific_outputs') for item in observation.get(key,[])]
            if observation.get('scientific_source') is not None:references.append(observation['scientific_source'])
            direct=direct or all(item.get('path')==(item.get('source') or {}).get('path') for item in references)
            root=experiment_client.root_for_configuration(config,allow_standalone=True) or Path(config['record_dir'])
            if direct:
                from . import local_experiments
                target_directory=local_experiments.artifact_directory(config,observation['call_id'])
            else:
                target_directory=_scientific_projection_directory(config)/'local-logs'/observation['call_id']
            if target_directory.resolve()!=target_directory:
                raise IntegrityError('Scientific log projection changed its host namespace')
            target_directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            log_name='execution-logs';suffix=1
            while any(item.name in {log_name+'/stdout.txt',log_name+'/stderr.txt'} for item in selected):
                suffix+=1;log_name=f'execution-logs-{suffix}'
            if direct:
                target_directory=target_directory/log_name
                target_directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            for stream in ('stdout','stderr'):
                matches=[item for item in completed['proofs'] if item['path']==str(directory/(stream+'.raw'))]
                if len(matches)!=1:raise IntegrityError('Scientific log has no exact retained transport original')
                descriptor=collected(matches[0])
                def blocks():
                    return scientific_log_projection.project(descriptor.local_path,size_bytes=descriptor.size_bytes,replacements=replacements,context=context)
                inventory.entry()
                target=target_directory/(stream+'.txt')
                if target.is_symlink():raise IntegrityError('Scientific log projection must be an original file')
                if not target.exists():
                    if direct:
                        from . import atomic_files
                        def projected_blocks():
                            for block in blocks():
                                with storage.mutation(root,len(block),policy=limits,context=context):
                                    yield block
                        atomic_files.publish_stream(target,projected_blocks(),context=context,inventory=inventory)
                    else:
                        original=shared_originals.publish_stream(root,blocks,policy=limits,context=context)
                        shared_originals.link(original,target,size_bytes=original.stat().st_size,
                            root=root,policy=limits,context=context)
                item=proof(target,context=context)
                selected.append(LocalFileDescriptor(name=log_name+'/'+stream+'.txt',local_path=str(target),
                    size_bytes=item['size_bytes'],media_type='text/plain'))
        selected=tuple(selected)
    elif not callable(method):selected=descriptors
    return _validated_scientific_outputs(selected,operation_context=context)


def _collected_view(config, call_id, observation, adapter, *, operation_context=None):
    """Give retained scientific outputs stable names without object-store addresses."""
    from . import contracts as c,experiment_client
    root=experiment_client.root_for_configuration(config)
    work=c.workspace(root,config['scope']['step'])
    from . import shared_input_views
    shared_input_views.dynamic_root(root,work,'retained-experiments')
    result = []
    from . import storage_policy as storage
    context=operation_context or storage.Deadline(config['max_seconds'])
    inventory=storage.Inventory(config.get('storage_policy'),context=context)
    directory=Records(config).directory(call_id)
    if (observation.get('call_id')!=call_id
            or observation['accepted']['path']!=str(directory/'accepted.json')
            or observation['completed']['path']!=str(directory/'completed.json')):
        raise IntegrityError('Collected scientific output lost its exact call controls')
    verify_observation(observation,lambda item:Path(item['path']),
        interface=config['execution_interface'],context=context,verify_files=False)
    accepted=json.loads(read(directory/'accepted.json'));completed=json.loads(read(directory/'completed.json'))
    direct=_local_original_observation(config,observation)
    output_root=shared_input_views.dynamic_root(root,work,'retained-experiments')/call_id/'outputs'
    for descriptor in scientific_outputs(config,observation,adapter=adapter,include_logs=True,operation_context=context):
        inventory.entry();inventory.file(descriptor.size_bytes)
        target=output_root/descriptor.name
        if direct:
            if Path(descriptor.local_path)!=target or target.resolve()!=target:
                raise IntegrityError('Local scientific output is outside its declared actual public original')
        else:
            actual=Path(descriptor.local_path)
            if actual.resolve()!=actual:raise IntegrityError('Collected scientific output original changed')
            public_run=False
            model=completed.get('model');run_id=completed.get('run_id');runs=accepted.get('runs_root')
            if (isinstance(model,dict) and isinstance(runs,str) and type(run_id) is int and run_id>0
                    and actual==Path(model['path'])==Path(runs)/f'model_run{run_id}.pkl'):
                public=Path(runs)
                row=json.loads(read(directory/'row.json'))
                saved=proof(actual,context=context)
                if (not public.is_absolute() or public.resolve()!=public or not public.is_relative_to(root)
                        or type(run_id) is not int or run_id<1
                        or actual!=public/f'model_run{run_id}.pkl' or completed['status']!='known_success'
                        or row.get('run_id')!=run_id or row.get('model_path')!=str(actual)
                        or row.get('script')!=accepted['source_script'] or row.get('ok') is not True
                        or any(saved[key]!=model[key] for key in ('size_bytes','file_stat') if key in model)):
                    raise IntegrityError('Collected public run model differs from its exact receipt call')
                public_run=True
            if actual.is_relative_to(output_root) or public_run:
                target=actual
            else:
                raise UnsupportedExecution('Scientific output has no readable original in its exact call public output directory')
        result.append(dict(name=descriptor.name,path=str(target),media_type=descriptor.media_type,
            size_bytes=descriptor.size_bytes,retention='retained_original'))
    return dict(status='collected', call_id=call_id, outputs=result)


def freeze(config, executor, *, path=None):
    """Host preparation only: freeze selected executor, configuration and flags."""
    from .protocol import require_scope
    require_scope(config.get('scope'))
    value = dict(config)
    from . import storage_policy as storage
    value['storage_policy'] = storage.configuration(value.get('storage_policy'))
    from . import breadth
    breadth.validate(value.get('breadth'))
    value.setdefault('admission',dict(version=ADMISSION_VERSION,mode='unavailable'))
    scope=value.get('scope')
    if (not isinstance(scope,dict) or set(scope)!={'step','role','job_ref','execution_key','mechanism_ref','handoff_protocol'}
            or type(scope.get('step')) is not int or scope['step']<=0 or scope['role'] not in {'H','W','A','E'}
            or not isinstance(scope.get('job_ref'),dict) or set(scope['job_ref'])!={'path'}
            or not isinstance(scope.get('execution_key'),dict) or set(scope['execution_key'])!={'path'}):
        raise IntegrityError('Exact host-bound scientific execution scope required')
    value.setdefault('max_seconds',65)
    if type(value['max_seconds']) not in (int,float) or not 0<value['max_seconds']<float('inf'):
        raise ValueError('Finite frozen execution time limit required')
    value['execution_interface'] = dict(executor=executor)
    declared, adapter, body = _adapter(value, path or Path(config['record_dir']).parent / 'config.json')
    result_protocol=getattr(adapter,'result_protocol',None)
    if result_protocol is not None and (not isinstance(result_protocol,str) or not 0<len(result_protocol.encode())<=160):
        raise ValueError('An execution result protocol requires an explicit bounded identifier')
    if declared.projection_contract is not None and (not isinstance(declared.projection_contract,str)
            or not 0<len(declared.projection_contract.encode())<=160):
        raise ValueError('Execution projection requires an explicit bounded contract identifier')
    value['execution_interface'] = dict(version=VERSION, executor=executor, plugin_version=declared.version,
        observation_version=OBSERVATION_VERSION,
        **(dict(projection_contract=declared.projection_contract) if declared.projection_contract is not None else {}),
        terminal_evidence=getattr(adapter,'terminal_evidence_kind','adapter_result'),
        result_protocol=result_protocol,
        config_record=body,
        capabilities=asdict(lifecycle_capabilities(adapter)))
    if 'registered_implementation' in value or adapter.capabilities():
        value['execution_interface']['public_capabilities']=_public_capabilities(adapter)
    return value


@instance_owner.effect
def configure(root,step,executor,config,output):
    """Freeze a host-bound interface before the Worker enters its existing namespace."""
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    from . import contracts as c, execution, budget
    root=Path(root).resolve();job=c.read(c.regular(root/'jobs'/f'{step:03d}.json'))
    versions=(configuration_updates.for_job(root, job).get('experiment_interface_version'),job.get('experiment_interface_version'))
    if any(type(value) is not int or value!=VERSION for value in versions):
        raise UnsupportedExecution('Preserved scientific instance is read-only; prepare a new registered instance')
    versions=(configuration_updates.for_job(root, job).get('managed_admission_version'),job.get('managed_admission_version'))
    if any(type(value) is not int or value!=ADMISSION_VERSION for value in versions):
        raise UnsupportedExecution('Preserved scientific admission is read-only; prepare a new instance')
    c._validate_inputs(root,c.workspace(root,step),job)
    record=execution.record(root,step)
    if c.read(c.regular(record/'native-intent.json')).get('task_ref')!=c.record_ref(root/'jobs'/f'{step:03d}.json'):
        raise IntegrityError('Host configuration has no exact accepted scientific task')
    native_attempt=dict(number=len(execution.records(root,step))-1,execution_key=execution.identity(root,step))
    selected=configuration_updates.for_job(root, job).get('execution_config')
    implementation={}
    if selected is not None:
        expected=dict(selected['configuration'])
        if executor=='local_execution:command':
            expected.setdefault('cwd',job['project']['base_root'] if job.get('project') is not None
                else str(c.workspace(root,step)))
        if executor!=selected['executor'] or config!=selected['configuration']:
            raise IntegrityError('Host execution configuration differs from the frozen operator selection')
        config=expected
        if 'implementation' in selected:implementation=dict(registered_implementation=selected['implementation'])
    elif isinstance(config,dict) and set(config)=={'config_file'}:
        config=operator_configuration(dict(executor=executor,config_file=config['config_file']))['configuration']
    # Other mappings are trusted launcher settings, which may already contain
    # host bindings. Rebind them to this accepted task below; operator settings
    # are checked separately before preparation or explicit file selection.
    admission=budget.review_configuration(root,job)
    from .protocol import stage_scope
    from . import candidate_refs, experiment_client
    value=dict(config,**implementation,record_dir=str(experiment_client.record_directory(root,step)),native_attempt=native_attempt,admission=admission,
        scope=stage_scope(root,job),breadth=(job.get('search') or {}).get('breadth'),
        **(dict(engineering_repair=dict(candidate_id=candidate_refs.resolve_reference(root,job,
            job['engineering_repair']['candidate_id']))) if job.get('engineering_repair') else {}))
    value['storage_policy'] = c.settings(root, job).get('storage_policy')
    client = None
    if job.get('worker_view_version') == 1:
        from . import experiment_client
        value['worker_view_version'] = 1
        original=record/'experiment-config.json'
        if original.exists():
            retained=c.read(c.regular(original))
            if 'host_launch_implementation' in retained:value['host_launch_implementation']=retained['host_launch_implementation']
        if Path(output).absolute().parent==c.workspace(root,step)/'work':
            if experiment_client.location(output) != (root, step):
                raise IntegrityError('Execution client differs from its canonical task workspace')
            client = experiment_client.stub(job, native_attempt['number'])
            if Path(output).exists():
                old=c.read(c.regular(Path(output)))
                if (not isinstance(old,dict) or set(old)!=set(client)
                        or old.get('step')!=step or old.get('worker_view_version')!=1):
                    raise IntegrityError('Frozen execution client changed')
    if configuration_updates.for_job(root, job).get('execution_resource_policy') is not None and job['role'] == 'W':
        from . import execution_authority
        value['host_execution_authority'] = execution_authority.contract(root, job)
    value=freeze(value,executor,path=output)
    updates=[]
    # Complete originals remain in the existing host-control namespace.
    projection=root/'host-runtime/experiment-control'/f'{value["scope"]["step"]:03d}'/'interfaces'/f'{native_attempt["number"]:03d}.json'
    for path in ((record/'experiment-config.json',projection) if client is not None else (Path(output),record/'experiment-config.json',projection)):
        if path.exists():
            old=c.read(c.regular(path))
            if (old==value or
                    {key:item for key,item in old.items() if key!='execution_interface'}==
                    {key:item for key,item in value.items() if key!='execution_interface'}
                    and _interface_contract(old['execution_interface'])==_interface_contract(value['execution_interface'])):
                value=old
                continue
            if path in (record/'experiment-config.json',projection) or old.get('scope')!=value['scope']:
                raise IntegrityError('Frozen experiment configuration changed')
        updates.append(path)
    # Validate every immutable conflict before replacing the current pointer.
    for path in updates:
        c.write(path,value);path.chmod(0o400)
    if client is not None:
        output=Path(output)
        c.write(experiment_client.binding_path(root,job,output),
            dict(client=client,configuration=proof(record/'experiment-config.json')))
        c.write(output,client);output.chmod(0o400)
    return value


def load(config, *, path=None, host_services=None):
    from .protocol import require_scope
    require_scope(config.get('scope') if isinstance(config,dict) else None)
    if not isinstance(config,dict):
        raise IntegrityError('Frozen execution configuration is malformed')
    if 'admission' not in config:
        raise UnsupportedExecution('Preserved admission contract is read-only; prepare a new instance')
    selected = config.get('execution_interface')
    if not isinstance(selected, dict) or type(selected.get('version')) is not int or selected['version'] != VERSION:
        raise UnsupportedExecution('Preserved execution configuration is read-only; prepare a new registered instance')
    if config.get('host_execution_authority') is not None:
        from . import execution_authority
        execution_authority.host_services(config)
    current=freeze({key: value for key, value in config.items() if key != 'execution_interface'}, selected['executor'], path=path)
    if ({key:value for key,value in current.items() if key!='execution_interface'} !=
            {key:value for key,value in config.items() if key!='execution_interface'}
            or _interface_contract(current['execution_interface']) != _interface_contract(selected)):
        raise IntegrityError('Frozen execution contract or configuration changed')
    _, adapter, _ = _adapter(config, path or Path(config['record_dir']).parent / 'config.json',host_services=host_services)
    return adapter


def original_configuration(config,call_id):
    """Resolve one accepted call's exact configuration, never the latest attempt."""
    accepted=Records(config).accepted(call_id)
    if (accepted.get('native_attempt')==config.get('native_attempt')
            and accepted.get('interface')==config['execution_interface']):
        return config
    attempt=accepted.get('native_attempt')
    if (not isinstance(attempt,dict) or set(attempt)!={'number','execution_key'}
            or type(attempt.get('number')) is not int or not 0<=attempt['number']<10000):
        raise IntegrityError('Original execution native attempt is unavailable')
    from . import experiment_client, execution, contracts as c
    root=experiment_client.root_for_configuration(config)
    step=config['scope']['step']
    intents=list(execution.accepted_intents(root,step))
    number=attempt['number']
    if (number>=len(intents) or attempt['execution_key']!=c.record_ref(
            root/'jobs'/f'{step:03d}.json' if number==0 else intents[number][0]/'native-intent.json')):
        raise IntegrityError('Original execution native attempt differs from its exact accepted intent')
    path=root/'host-runtime/experiment-control'/f'{config["scope"]["step"]:03d}'/'interfaces'/f'{attempt["number"]:03d}.json'
    original=json.loads(read(path))
    if (original.get('scope')!=config['scope'] or original.get('record_dir')!=config['record_dir']
            or original.get('native_attempt')!=attempt or original.get('execution_interface')!=accepted.get('interface')):
        raise IntegrityError('Original execution configuration differs from accepted binding')
    return original


def original_adapter(config,call_id,capability):
    """Both the current permission and the original interface must allow an action."""
    current=load(config)
    def allowed(settings,adapter):
        if capability=='retire':
            _retirement_capability(settings['execution_interface'])
            return callable(getattr(adapter,'retire_original',None))
        return getattr(lifecycle_capabilities(adapter),capability)
    if not allowed(config,current):
        raise UnsupportedExecution('Requested original execution operation unavailable')
    original=original_configuration(config,call_id)
    adapter=current if original is config else load(original)
    if not allowed(original,adapter):
        raise UnsupportedExecution('Original execution operation unavailable')
    return original,adapter


def _direct_execution_input(config, metadata, prepared, original):
    """Use the accepted code or exact original-package binding, without copying it."""
    if 'code_version' in metadata:return True
    if config.get('worker_view_version')!=1:return False
    tcad=metadata.get('tcad')
    if isinstance(tcad,dict) and 'prepared' in tcad:
        bound=tcad['prepared']
        if (not isinstance(bound,dict)
                or set(bound) not in (set(prepared),set(prepared)|{'file_stat'})):
            raise IntegrityError('Prepared execution binding fields differ')
        if 'file_stat' in bound:
            saved=bound['file_stat']
            if (not isinstance(saved,list) or len(saved)!=4
                    or any(type(value) is not int for value in saved)
                    or saved[2]!=prepared['size_bytes'] or saved!=original.get('file_stat')):
                raise IntegrityError('Prepared execution input original changed')
        if {key:value for key,value in bound.items() if key!='file_stat'}!=prepared:
            raise IntegrityError('Prepared execution binding differs')
        return True
    if (isinstance(metadata.get('source_script'),str)
            and Path(metadata['source_script']).absolute()!=Path(prepared['local_path'])):
        from . import experiment_client
        selected=experiment_client.payload_path(config,metadata['source_script'])
        return (selected==Path(prepared['local_path'])
            and selected!=Path(metadata['source_script']).absolute())
    return False


class Records:
    """One canonical accepted/process/terminal/completed owner, shared by drivers."""
    def __init__(self, config):
        self.config = config
        self.root = Path(config['record_dir'])
        self._preparation_node_held = False
        if self.root.resolve() != self.root:
            raise IntegrityError('Execution record namespace changed')

    def storage_root(self):
        from . import experiment_client
        return experiment_client.root_for_configuration(self.config,allow_standalone=True) or self.root

    def calls(self):
        root = self.root / 'calls'
        if root.resolve() != root:
            raise IntegrityError('Execution call namespace changed')
        paths = sorted(root.iterdir()) if root.exists() else []
        if any(path.resolve() != path or not path.is_dir() for path in paths):
            raise IntegrityError('Execution call directory changed')
        return paths

    def directory(self, call_id):
        if not isinstance(call_id, str) or not ID.fullmatch(call_id):
            raise ValueError('Exact original execution identity required')
        path = self.root / 'calls' / call_id
        if path.resolve() != path or not path.is_dir():
            raise IntegrityError('Original execution call unavailable')
        return path

    @contextmanager
    def lease(self, name='node.lock', *, context=None):
        if name=='accept.lock' and self.config.get('admission',{}).get('mode')=='soft':
            path=Path(self.config['admission']['directory'])/'accept.lock'
            fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
            try:
                if context is None:fcntl.flock(fd,fcntl.LOCK_EX)
                else:
                    while True:
                        context.remaining_seconds()
                        try:
                            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                        except BlockingIOError:time.sleep(min(.05,context.remaining_seconds()))
                yield
            finally:os.close(fd)
            return
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.root / name, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    @contextmanager
    def _preparation_lease(self):
        """One dispatch owns the existing node lock through preparation."""
        with self.lease():
            self._preparation_node_held = True
            try:yield
            finally:self._preparation_node_held = False

    @contextmanager
    def admission_lease(self, *, context=None):
        """Use the same short lock order for preflight and durable acceptance."""
        with ExitStack() as locks:
            try:
                if not self._preparation_node_held:locks.enter_context(self.lease())
                locks.enter_context(self.lease('accept.lock',context=context))
            except BlockingIOError as error:
                raise ConcurrentExecution('Another admission still owns its lease; no new call was accepted') from error
            yield

    def admission_binding(self, mechanism, *, expected_calls=None, recovery_admission=None):
        calls=self.calls()
        if expected_calls is not None and [path.name for path in calls]!=expected_calls:
            raise ConcurrentExecution('Another original execution was accepted; query its identity')
        if recovery_admission is None:
            review=review_status(self.config)
            if review['requested'] and review['enforced']:
                reason=review['reason']
                if reason in _BudgetAdmissionUnavailable._REASONS:
                    raise _BudgetAdmissionUnavailable(reason)
                raise ReviewRequired(reason)
        else:
            recovery_admission()
        from . import breadth
        return breadth.admit(self.config.get('breadth'),mechanism,self.config['scope'],
            [self.accepted(path.name) for path in calls])

    @_configuration_effect
    def preflight(self, *, expected_calls, mechanism=None):
        from . import storage_policy as storage
        with self.admission_lease(context=storage.Deadline(self.config['max_seconds'])):
            self.admission_binding(mechanism,expected_calls=expected_calls)

    @_configuration_effect
    def accept(self, descriptor, metadata, interface, *, expected_calls=None, mechanism=None, recovery_admission=None, operation_context=None, _local_preparation=False):
        """Snapshot inputs before the short accept.lock publication section."""
        _require_new_execution(self.config)
        from .protocol import require_scope
        require_scope(self.config['scope'])
        from . import contracts as c, storage_policy as storage
        from . import preparation_recovery, experiment_client
        operation_context = storage.Deadline(self.config['max_seconds'],operation_context)
        instance=experiment_client.root_for_configuration(self.config,allow_standalone=True)
        if instance is not None:
            unresolved=[row for row in preparation_recovery.retained_preparations(instance,self.root,context=operation_context)
                if not row['resolved']]
            if unresolved:raise ConcurrentExecution('Original host preparation is unresolved; preserve its exact call without replay')
        inventory = storage.Inventory(self.config.get('storage_policy'),context=operation_context)
        original=proof(descriptor.local_path,context=operation_context)
        if original['size_bytes']!=descriptor.size_bytes:
            raise IntegrityError('Prepared execution input changed')
        destination=self.root/'calls'/descriptor.name
        direct_input=_direct_execution_input(self.config,metadata,descriptor.model_dump(mode='json'),original)
        source_proof=original if direct_input else dict(path=str(destination/'source.py'),size_bytes=descriptor.size_bytes)
        accepted = dict(metadata, call_id=descriptor.name, scope=self.config['scope'],
            source_script=metadata.get('source_script',descriptor.local_path) if direct_input else descriptor.local_path, script=source_proof,native_attempt=self.config.get('native_attempt'),
            prepared=descriptor.model_dump(mode='json'), interface=interface)
        if instance is not None:
            job=c.read(c.regular(instance/'jobs'/f'{self.config["scope"]["step"]:03d}.json'))
            if job.get('project') is not None:
                if 'base_identity' in accepted:_validate_base_identity(job['project'],accepted)
                accepted['base_identity']=dict(job['project']['base_identity'])
            else:_validate_base_identity(None,accepted)
        elif 'base_identity' in accepted:
            raise IntegrityError('Standalone execution cannot supply a project identity')
        import shutil,tempfile
        self.root.mkdir(parents=True,exist_ok=True)
        directory=Path(tempfile.mkdtemp(prefix='.prepared-',dir=self.root))
        source = directory / 'source.py'
        host_preparation=None
        try:
            if direct_input:verify_scientific_inputs(accepted,config=self.config,context=operation_context)
            # Only the live built-in local dispatch knows prepare had no external
            # effect. A missing accepted/process file is never recovery evidence.
            if not _local_preparation:
                host_preparation=preparation_recovery.bind_snapshot(self.config,directory,descriptor,accepted,context=operation_context)
            if host_preparation is not None:accepted['host_preparation']=host_preparation
            files = metadata.get('snapshot_files', [])
            if not isinstance(files,list):raise ValueError('Managed input inventory required')
            if not files:inventory.file(descriptor.size_bytes)
            for item in files:
                inventory.entry();inventory.file(item['size_bytes'])
            if not direct_input:
                c._bind_original(Path(descriptor.local_path), source, context=operation_context, root=self.storage_root(), policy=self.config.get('storage_policy'), size_bytes=descriptor.size_bytes, source_kind='mutable')
            seen = set()
            for item in files:
                name = item['name']; relative = Path(name)
                if (not isinstance(name, str) or relative.is_absolute() or '..' in relative.parts
                        or relative.as_posix() != name or not relative.parts or name in seen):
                    raise ValueError('Managed input path is not canonical')
                seen.add(name)
                original=proof(item['path'],context=operation_context)
                if any(original[key]!=item[key] for key in ('size_bytes','file_stat') if key in item):
                    raise IntegrityError('Managed input changed before acceptance')
                target=directory/'inputs'/relative
                if direct_input:
                    saved=experiment_client.input_reference(self.config,item['path'])
                    if any(saved.get(key)!=item.get(key) for key in ('path','size_bytes','file_stat')):
                        raise IntegrityError('Code data inventory differs from its frozen scientific input')
                else:
                    c._bind_original(Path(item['path']), target, context=operation_context, root=self.storage_root(), policy=self.config.get('storage_policy'), size_bytes=item['size_bytes'], source_kind='mutable')
            with self.admission_lease(context=operation_context):
                binding=self.admission_binding(mechanism,expected_calls=expected_calls,
                    recovery_admission=recovery_admission)
                if binding is not None:accepted['mechanism_binding']=binding
                if direct_input:
                    verify_scientific_inputs(accepted,config=self.config,context=operation_context)
                    if proof(descriptor.local_path,context=operation_context)!=accepted['script']:
                        raise IntegrityError('Accepted code export changed during preparation')
                write(directory/'accepted.json',accepted,root=self.storage_root(),
                    policy=self.config.get('storage_policy'),context=operation_context)
                destination.parent.mkdir(exist_ok=True)
                from . import instance_archive
                instance_archive._publish(directory,destination)
        finally:
            if directory.exists():
                if _local_preparation:
                    try:shutil.rmtree(directory)
                    except OSError as error:
                        raise LocalPreparationCleanupError('Local temporary preparation cleanup failed before submission') from error
                elif host_preparation is None:shutil.rmtree(directory)
                elif not (directory/'accepted.json').exists():
                    preparation_recovery.discard_owned_snapshot(self.config,host_preparation,
                        reason='Original writer cleanup after incomplete acceptance',context=storage.Deadline(65))
        return accepted

    @_configuration_effect
    def publish(self, call_id, filename, value):
        from .protocol import require_scope
        require_scope(self.config['scope'])
        if filename not in {'process.json', 'terminal.json', 'completed.json','submission.json','collected.json','submission-retirement.json'}:
            raise ValueError('Unknown execution receipt')
        directory = self.directory(call_id)
        accepted = json.loads(read(directory / 'accepted.json'))
        if accepted['scope'] != self.config['scope'] or accepted['call_id'] != call_id:
            raise IntegrityError('Execution receipt escaped its accepted scope')
        value=dict(value,call_id=call_id,scope=self.config['scope'])
        if 'mechanism_binding' in accepted:
            if 'mechanism_binding' in value and value['mechanism_binding']!=accepted['mechanism_binding']:
                raise IntegrityError('Execution receipt changed scientific mechanism')
            value=dict(value,call_id=call_id,scope=self.config['scope'],mechanism_binding=accepted['mechanism_binding'])
        # These receipts retain dispatched identities, cleanup and terminal or
        # permanent-retirement facts. Content quota exhaustion must not erase
        # recovery information after a real side effect. Admission and retained
        # payloads/logs remain quota-bound; receipt bytes count in the next scan.
        write(directory / filename, value)

    def accepted(self,call_id):
        """Read accepted control identity without reopening scientific payloads."""
        value=json.loads(read(self.directory(call_id)/'accepted.json'))
        if (not isinstance(value,dict) or value.get('call_id')!=call_id
                or not isinstance(value.get('scope'),dict) or type(value['scope'].get('step')) is not int
                or value['scope']!=self.config['scope']):
            raise IntegrityError('Accepted execution binding changed')
        from . import breadth
        try:breadth.check_binding(self.config.get('breadth'),value.get('mechanism_binding'),self.config['scope'])
        except ValueError as error:raise IntegrityError('Accepted scientific mechanism binding changed') from error
        parent=value.get('parent')
        if not isinstance(parent,dict) or 'candidate_id' not in parent:
            raise IntegrityError('Published execution parent is malformed')
        candidate=parent['candidate_id']
        from . import candidate_refs
        if candidate is not None and not candidate_refs.valid_parent_id(candidate):
            raise IntegrityError('Published execution parent is malformed')
        if 'code_version' in value:_code_version_binding(self.config,value)
        else:
            from . import contracts as c, experiment_client
            instance=experiment_client.root_for_configuration(self.config,allow_standalone=True)
            if instance is not None:
                job=c.read(c.regular(instance/'jobs'/f'{self.config["scope"]["step"]:03d}.json'))
                _validate_base_identity(job.get('project'),value)
            elif 'base_identity' in value:
                raise IntegrityError('Standalone execution cannot supply a project identity')
        return value

    @_configuration_effect
    def bind_external(self,call_id,external_id,state):
        if not isinstance(external_id,str) or not ID.fullmatch(external_id):
            raise IntegrityError('Submitted external execution identity is invalid')
        accepted=self.accepted(call_id)
        value=dict(call_id=call_id,external_run_id=external_id,scope=self.config['scope'])
        if 'mechanism_binding' in accepted:value['mechanism_binding']=accepted['mechanism_binding']
        path=self.directory(call_id)/'submission.json'
        if path.exists():
            if json.loads(read(path))!=value:raise IntegrityError('Original external execution identity changed')
        else:self.publish(call_id,'submission.json',value)
        return external_id,state

    def external(self,call_id):
        accepted=self.accepted(call_id)
        path=self.directory(call_id)/'submission.json'
        if not path.exists():return None
        value=json.loads(read(path))
        if (value.get('call_id')!=call_id or value.get('scope')!=self.config['scope']
                or value.get('mechanism_binding')!=accepted.get('mechanism_binding')):
            raise IntegrityError('External execution binding changed')
        return value['external_run_id']

    def state(self,call_id):
        """Canonical completion only; process observation belongs to an implementation."""
        self.accepted(call_id)
        path=self.directory(call_id)/'completed.json'
        if not path.exists():return 'unknown'
        completed=json.loads(read(path))
        if not isinstance(completed,dict) or completed.get('call_id')!=call_id or completed.get('scope')!=self.config['scope']:
            raise IntegrityError('Completed execution binding changed')
        if 'native_state' in completed:
            state=completed['native_state']
            if (not isinstance(state,str) or state not in {'succeeded','failed','cancelled'}
                    or execution_status(state)!=completed.get('status')):
                raise IntegrityError('Completed execution native terminal state changed')
            return state
        return {'known_success':'succeeded','known_failed':'failed','unknown':'unknown'}.get(completed.get('status'),'unknown')


def dispatch(config, script, *, parent=None, new=False, change=None, mechanism=None, command_options=None):
    """Convenience input path; neutral callers may provide a descriptor directly."""
    from . import experiment_client
    with instance_owner.lease_for_configuration(config):
        logical=experiment_client.code_path(config,script)
        reference=experiment_client.input_reference(config,logical)
        source=experiment_client.payload_path(config,logical);item=proof(source)
        if str(source)==reference['path'] and item!=reference:
            raise IntegrityError('Scientific payload changed after its saved input binding was checked')
        payload=LocalFileDescriptor(name='input',local_path=str(source),size_bytes=item['size_bytes'],media_type='application/octet-stream')
        return dispatch_descriptor(config,payload,parent=parent,new=new,change=change,mechanism=mechanism,
            command_options=command_options,_source_script=str(logical))


def local_terminal_status(accepted, completed, terminal):
    """Legacy local originals only; no fabricated POSIX receipts for other backends."""
    if not isinstance(completed,dict) or not isinstance(terminal,dict):return 'unknown'
    if any(value.get(key)!=accepted.get(key) for value in (completed,terminal)
           for key in ('call_id','scope','mechanism_binding')):return 'unknown'
    if (terminal.get('cleanup_confirmed') is not True or terminal.get('transport_complete') is not True
            or type(terminal.get('returncode')) is not int):return 'unknown'
    expected='known_success' if terminal['returncode']==0 else 'known_failed'
    return expected if completed.get('status')==expected else 'unknown'


def verify_observation(value, original, *, interface=None, context=None, verify_files=True, source=None):
    """Verify frozen control facts using exact bytes, never a live backend query."""
    if type(verify_files) is not bool:raise ValueError('Explicit observation file verification required')
    def reference(item):
        if (not isinstance(item,dict) or not isinstance(item.get('path'),str) or not item['path']
                or type(item.get('size_bytes')) is not int or item['size_bytes']<0
                or 'file_stat' in item and (not isinstance(item['file_stat'],list) or len(item['file_stat'])!=4
                    or any(type(part) is not int for part in item['file_stat'])
                    or item['file_stat'][2]!=item['size_bytes'])):
            raise IntegrityError('Execution observation original fingerprint is unavailable')
    def bound(item):
        reference(item)
        data=original(item)
        if isinstance(data,Path):
            valid=proof(data,context=context)
            changed=any(valid[key]!=item[key] for key in ('size_bytes','file_stat') if key in item)
        else:changed=len(data)!=item['size_bytes']
        if changed:raise IntegrityError('Execution observation original changed')
        return data
    def decoded(item):
        data=bound(item)
        if isinstance(data,Path):
            with data.open(encoding='utf-8') as stream:return json.load(stream)
        return json.loads(data)
    if not isinstance(value,dict) or value.get('version')!=OBSERVATION_VERSION:
        raise IntegrityError('Execution observation version unavailable')
    if (value.get('interface',{}).get('projection_contract') is not None
            and not {'public_result','scientific_outputs','scientific_source'} <= set(value)):
        raise IntegrityError('Frozen scientific projection contract is incomplete')
    accepted=decoded(value['accepted']);completed=decoded(value['completed'])
    submission=decoded(value['submission'])
    if (value.get('interface')!=accepted.get('interface') or interface is not None and value.get('interface')!=interface
            or value.get('status') not in {'known_success','known_failed'}
            or value['status']!=completed.get('status')
            or value.get('external_run_id')!=submission.get('external_run_id')
            or not isinstance(value.get('external_run_id'),str)):
        raise IntegrityError('Execution observation differs from accepted interface or terminal result')
    if ('native_state' in completed or 'native_state' in value):
        state=completed.get('native_state')
        if (not isinstance(state,str) or state not in {'succeeded','failed','cancelled'} or value.get('native_state')!=state
                or execution_status(state)!=value['status']):
            raise IntegrityError('Execution observation differs from the native terminal state')
    for row in (accepted,completed,submission):
        if any(row.get(key)!=value.get(key) for key in ('call_id','scope','mechanism_binding')):
            raise IntegrityError('Execution observation escaped its accepted owner')
    source_version=value.get('source_code_version');metadata=None
    if 'source_code_version' in value:
        from . import code_versions
        try:code_versions._version(source_version)
        except (KeyError,TypeError,ValueError) as error:
            raise IntegrityError('Scientific source code version is malformed') from error
        body=value['interface'].get('config_record')
        if not isinstance(body,dict) or body.get('scope')!=accepted['scope']:
            raise IntegrityError('Scientific source code version has no exact frozen configuration')
        config=dict(body,execution_interface=value['interface'])
        metadata=(_source_control_metadata(config,accepted,source=source)
            if accepted.get('execution_authority') is not None else None)
        if 'code_version' in accepted:
            _code_version_binding(config,accepted,source=source)
            expected_version=accepted['code_version']
            if metadata is not None and metadata['code_version'] not in (None,expected_version):
                raise IntegrityError('Scientific source has conflicting accepted and compiler code versions')
        elif accepted.get('execution_authority') is not None:
            expected_version=metadata['code_version']
            if expected_version is None:raise IntegrityError('Scientific source code version has no exact compiler')
        else:raise IntegrityError('Scientific source code version has no accepted original binding')
        if source_version!=expected_version:
            raise IntegrityError('Scientific source code version differs from its exact original')
    science,controls=_observation_file_roles(value,accepted,completed,
        source_controls=metadata['control_paths'] if metadata is not None else ())
    if accepted.get('execution_authority') is not None:bound(accepted['execution_authority'])
    if isinstance(accepted.get('tcad'),dict):bound(accepted['script'])
    for item in [*value['proofs'],*value['outputs']]:
        reference(item);reference(item['source'])
        if verify_files or item['path'] in controls or item['source']['path'] in controls or item['source'] not in science:bound(item)
        if verify_files or item['source']['path'] in controls:bound(item['source'])
        if item['source']['size_bytes']!=item['size_bytes']:
            raise IntegrityError('Collected observation differs from its declared descriptor')
    if [item['source'] for item in value['proofs']]!=completed.get('proofs'):
        raise IntegrityError('Retained backend proofs differ from the original completion descriptors')
    for item in value['outputs']:
        descriptor=LocalFileDescriptor.model_validate(item['descriptor'])
        if (descriptor.name!=item['name'] or descriptor.local_path!=item['source']['path']
                or descriptor.size_bytes!=item['size_bytes']):
            raise IntegrityError('Retained collection differs from its original descriptor')
    scientific_source=value.get('scientific_source')
    if scientific_source is not None:
        reference(scientific_source);reference(scientific_source['source'])
        if ('source_code_version' in value and 'code_version' in accepted
                and {key:scientific_source['source'].get(key) for key in ('path','size_bytes','file_stat')}
                    ==_code_export_entry(_code_version_binding(config,accepted,source=source),accepted)):
            raise IntegrityError('Scientific code source retained its temporary export instead of its code version')
        if verify_files or scientific_source['path'] in controls or scientific_source['source']['path'] in controls:bound(scientific_source)
        if verify_files or scientific_source['source']['path'] in controls:bound(scientific_source['source'])
        if scientific_source['size_bytes']!=scientific_source['source']['size_bytes']:
            raise IntegrityError('Scientific source lost its retained original')
    if 'public_result' in value:
        public=_run_view(value['public_result'])
        if public.get('status')!=value['status'] or 'call_id' in public and public['call_id']!=value['call_id']:
            raise IntegrityError('Frozen public result differs from the original call')
    names=set()
    for item in value.get('scientific_outputs',[]):
        reference(item);reference(item['source'])
        if verify_files or item['path'] in controls or item['source']['path'] in controls:bound(item)
        if verify_files or item['source']['path'] in controls:bound(item['source'])
        descriptor=LocalFileDescriptor.model_validate(item['descriptor'])
        name=Path(descriptor.name)
        if (descriptor.name in names or name.is_absolute() or '..' in name.parts or not name.parts
                or name.as_posix()!=descriptor.name or '\\' in descriptor.name
                or descriptor.name!=item['name'] or descriptor.local_path!=item['source']['path']
                or item['size_bytes']!=descriptor.size_bytes
                or item['source']['size_bytes']!=item['size_bytes']):
            raise IntegrityError('Frozen scientific output lost its exact descriptor')
        names.add(descriptor.name)
    if value['interface'].get('terminal_evidence')=='local_process':
        terminal=decoded(value['native_terminal'])
        if local_terminal_status(accepted,completed,terminal)!=value['status']:
            raise IntegrityError('Local observation lacks confirmed cleanup, transport or exit status')
    elif value['interface'].get('terminal_evidence')!='adapter_result':
        raise IntegrityError('Execution terminal evidence contract unavailable')
    return value['status']


def _retirement_capability(interface):
    rows=[row for row in interface.get('public_capabilities',[]) if row.get('key')==RETIREMENT_KEY]
    if (len(rows)!=1 or rows[0].get('schema_id')!=RETIREMENT_SCHEMA
            or type(rows[0].get('payload_schema_version')) is not int or rows[0]['payload_schema_version']!=1
            or rows[0].get('kind')!='execution_recovery' or rows[0].get('media_type')!='application/json'):
        raise UnsupportedExecution('Original permanent retirement capability is unavailable')
    return rows[0]


def _retirement_contract(contract,capability):
    if (not isinstance(contract,dict) or set(contract)!=set(RETIREMENT_CONTRACT)|{'backend_protocol'}
            or encoded({key:contract.get(key) for key in RETIREMENT_CONTRACT})!=encoded(RETIREMENT_CONTRACT)
            or not isinstance(contract.get('backend_protocol'),dict) or not contract['backend_protocol']):
        raise IntegrityError('Permanent retirement capability contract changed')
    raw=encoded(contract)
    if capability.get('size_bytes')!=len(raw):
        raise IntegrityError('Permanent retirement capability bytes differ from the frozen interface')
    return contract


def verify_retirement(value, original, *, interface=None, context=None, verify_files=True):
    """Verify an independent never-submitted fact, without a backend or budget IO."""
    if type(verify_files) is not bool:raise ValueError('Explicit retirement file verification required')
    def bound(item, *, authority=False):
        data=original(item)
        if isinstance(data,Path):
            if data.resolve()!=data:raise IntegrityError('Retirement original path changed')
            actual=proof(data,context=context)
            fields=tuple(key for key in ('size_bytes','file_stat') if key in item)
            if any(actual[key]!=item.get(key) for key in fields):
                raise IntegrityError('Retirement original changed')
            data=data.read_bytes()
        if len(data)!=item['size_bytes']:
            raise IntegrityError('Retirement original changed')
        return data
    def decoded(item, *, authority=False):return json.loads(bound(item,authority=authority))
    if (not isinstance(value,dict) or type(value.get('version')) is not int or value['version'] not in {1,2}
            or value.get('kind')!='permanent_not_submitted'):
        raise IntegrityError('Permanent retirement contract is unavailable')
    accepted=decoded(value['accepted'])
    selected=accepted.get('interface')
    if (value.get('interface')!=selected or interface is not None and selected!=interface
            or value.get('capability')!=_retirement_capability(selected)
            or any(value.get(key)!=accepted.get(key) for key in ('call_id','scope','native_attempt','mechanism_binding'))
            or value.get('prepared')!=accepted.get('prepared')
            or value.get('authority')!=accepted.get('execution_authority')):
        raise IntegrityError('Retirement differs from the original accepted call')
    contract=_retirement_contract(value['contract'],value['capability'])
    prepared=value['prepared']
    script_path=(prepared.get('local_path') if 'code_version' in accepted else str(Path(value['accepted']['path']).parent/'source.py'))
    if (accepted['script'].get('path')!=script_path
            or prepared.get('name')!=value['call_id']
            or accepted['script'].get('size_bytes')!=prepared['size_bytes']):
        raise IntegrityError('Retirement prepared source differs from its original descriptor')
    science,controls=_observation_file_roles(None,accepted,{},directory=Path(value['accepted']['path']).parent)
    script={key:accepted['script'][key] for key in ('path','size_bytes','file_stat') if key in accepted['script']}
    if verify_files or script['path'] in controls or script not in science:
        bound(accepted['script'])
        if 'code_version' not in accepted:bound(dict(path=prepared['local_path'],size_bytes=prepared['size_bytes']))
    if isinstance(accepted.get('tcad'),dict):
        for key in ('prepared_ref','authority'):
            item=accepted['tcad'].get(key)
            if isinstance(item,dict) and 'path' in item:bound(item)
    decoded(value['authority'],authority=True)
    if value['version']==1:
        declared,_=registration(selected['executor'])
        if declared.legacy_retirement is None:
            raise UnsupportedExecution('Legacy retirement requires its original compatible evidence reader')
        declared.legacy_retirement(value,decoded,contract)
    else:
        assertion=decoded(value['assertion'])
        expected=dict(version=1,kind='permanent_not_submitted',
            **{key:value[key] for key in ('call_id','scope','accepted','prepared','authority','native_originals')})
        if assertion!=expected or not isinstance(value['native_originals'],list) or not value['native_originals']:
            raise IntegrityError('Normalized retirement differs from the exact original call')
        for item in value['native_originals']:bound(item)
    dispatch=value.get('dispatch_intent')
    if dispatch is not None:
        intent=decoded(dispatch)
        if (dispatch.get('path')!=accepted.get('submission_intent_path')
                or intent.get('call_id')!=value['call_id'] or intent.get('scope')!=value['scope']
                or intent.get('prepared')!=value['prepared'] or intent.get('authority')!=value['authority']):
            raise IntegrityError('Retirement changed the original dispatch intent')
    return dict(call_id=value['call_id'],scope=value['scope'],prepared=value['prepared'],
                accepted=value['accepted'],authority=value['authority'])


def retirement_originals(value,accepted):
    prepared=value['prepared']
    result=([value[key] for key in ('accepted','authority','request','response','protocol')]
        if value['version']==1 else [value['accepted'],value['authority'],value['assertion'],*value['native_originals']])
    result.extend([accepted['script'],dict(path=prepared['local_path'],size_bytes=prepared['size_bytes'])])
    if value.get('dispatch_intent') is not None:result.append(value['dispatch_intent'])
    return result


def retirement(config,call_id, *, verify_files=True):
    records=Records(config);directory=records.directory(call_id)
    path=directory/'submission-retirement.json'
    if not path.exists() and not path.is_symlink():return None
    value=json.loads(read(path));accepted=records.accepted(call_id)
    if (any(proof(directory/'accepted.json').get(key)!=item for key,item in value.get('accepted',{}).items()) or value.get('prepared')!=accepted['prepared']
            or records.external(call_id) is not None
            or any((directory/name).exists() or (directory/name).is_symlink()
                   for name in ('completed.json','terminal.json','execution-observation.json'))):
        raise IntegrityError('Permanent non-submission contradicts the original call')
    dispatch=Path(accepted['submission_intent_path'])
    if (dispatch.parent!=directory or dispatch.resolve()!=dispatch
            or (value.get('dispatch_intent') is None if dispatch.exists() else value.get('dispatch_intent') is not None)
            or dispatch.exists() and any(proof(dispatch).get(key)!=item for key,item in value['dispatch_intent'].items())):
        raise IntegrityError('Retired call dispatch identity changed')
    verify_retirement(value,lambda item:Path(item['path']),interface=config['execution_interface'],verify_files=verify_files)
    return value


@_configuration_effect
def retire_original(config,adapter,call_id):
    capability=_retirement_capability(config['execution_interface'])
    matches=[item for item in adapter.capabilities() if item.key==RETIREMENT_KEY]
    if len(matches)!=1:raise UnsupportedExecution('Original retirement declaration is unavailable')
    contract=_retirement_contract(json.loads(matches[0].content),capability)
    existing=retirement(config,call_id)
    if existing is not None:
        from . import execution_authority
        accounting=execution_authority.reconcile_resources(config,adapter,call_id)
        return dict(call_id=call_id,status='not_submitted',receipt=proof(Records(config).directory(call_id)/'submission-retirement.json'),
                    **(dict(resource_accounting=accounting) if accounting is not None else {}))
    method=getattr(adapter,'retire_original',None)
    if not callable(method):raise UnsupportedExecution('Original retirement method is unavailable')
    records=Records(config);directory=records.directory(call_id)
    try:
        with method(call_id) as evidence:
            if evidence.get('status')!='not_submitted':return evidence
            accepted=records.accepted(call_id)
            value=dict(version=2,kind='permanent_not_submitted',call_id=call_id,scope=accepted['scope'],
                native_attempt=accepted.get('native_attempt'),mechanism_binding=accepted.get('mechanism_binding'),
                interface=accepted['interface'],accepted=proof(directory/'accepted.json'),prepared=accepted['prepared'],
                authority=accepted['execution_authority'],capability=_retirement_capability(accepted['interface']),contract=contract,
                **{key:evidence[key] for key in ('assertion','native_originals','dispatch_intent')})
            verify_retirement(value,lambda item:Path(item['path']),interface=config['execution_interface'])
            path=directory/'submission-retirement.json'
            if path.exists():
                if json.loads(read(path))!=value:raise IntegrityError('Retirement publication changed')
            else:records.publish(call_id,'submission-retirement.json',value)
            retirement(config,call_id)
            from . import execution_authority
            accounting=execution_authority.reconcile_resources(config,adapter,call_id)
            return dict(call_id=call_id,status='not_submitted',receipt=proof(path),
                        **(dict(resource_accounting=accounting) if accounting is not None else {}))
    except BlockingIOError:
        return dict(call_id=call_id,status='unknown',note='Original submission or retirement still owns its call lease')


def _saved_observation(config, adapter, call_id, path, *, operation_context, verify_files):
    if not path.exists():return None
    value=json.loads(read(path));verify_observation(value,lambda item:Path(item['path']),
        interface=config['execution_interface'],context=operation_context,verify_files=verify_files)
    from . import execution_authority
    execution_authority.reconcile_resources(config,adapter,call_id,observation=value,operation_context=operation_context)
    _cleanup_terminal_code_export(config,call_id,value)
    return value


@_configuration_effect
def reconcile_saved_observation(config, adapter, call_id, *, operation_context=None):
    """Verify saved terminal controls and reconcile accounting and export cleanup.

    Missing observations remain missing; this operation never queries terminal
    status or collects and publishes terminal evidence.
    """
    from . import storage_policy as storage
    operation_context=storage.Deadline(config['max_seconds'],operation_context)
    path=Records(config).directory(call_id)/'execution-observation.json'
    return _saved_observation(config,adapter,call_id,path,
        operation_context=operation_context,verify_files=False)


@_configuration_effect
def record_observation(config, adapter, call_id, *, collected=None, operation_context=None):
    """Freeze one backend-verified terminal projection while executing its protocol.

    The adapter owns its native terminal evidence. In particular local adapters
    validate cleanup, transport and exit code in status_details/result. Scientific
    readers consume only this immutable projection and its retained originals.
    """
    from . import shared_originals, storage_policy as storage, experiment_client
    operation_context=storage.Deadline(config['max_seconds'],operation_context)
    records=Records(config);directory=records.directory(call_id)
    storage_root=experiment_client.root_for_configuration(config,allow_standalone=True) or records.root
    inventory=storage.Inventory(config.get('storage_policy'),context=operation_context)
    retained={}
    path=directory/'execution-observation.json'
    saved=_saved_observation(config,adapter,call_id,path,
        operation_context=operation_context,verify_files=True)
    if saved is not None:return saved
    accepted=records.accepted(call_id)
    if accepted['interface']!=config['execution_interface']:
        raise IntegrityError('Observation interface differs from accepted execution')
    direct_local=(config['execution_interface'].get('executor')=='local_execution:command'
        and 'code_version' in accepted)
    external=records.external(call_id)
    if external is None:return None
    details=adapter.status_details(call_id);state=details.get('state')
    status=execution_status(state)
    if status not in {'known_success','known_failed'}:return None
    result=adapter.result(call_id)
    completion=directory/'completed.json'
    if result.get('status')=='unknown' and not completion.exists() and not completion.is_symlink():return None
    completed=json.loads(read(directory/'completed.json'))
    if (result!=completed or completed.get('status')!=status
            or any(completed.get(key)!=accepted.get(key) for key in ('call_id','scope','mechanism_binding'))):
        raise IntegrityError('Adapter terminal projection differs from accepted completion')
    if (state=='cancelled' or 'native_state' in completed) and completed.get('native_state')!=state:
        raise IntegrityError('Adapter completion must retain its native terminal state')
    proofs=result.get('proofs')
    if not isinstance(proofs,list) or not proofs:
        raise IntegrityError('Adapter terminal result requires bounded original proofs')
    for item in proofs:
        if any(proof(item['path'],context=operation_context).get(key)!=saved for key,saved in item.items()):raise IntegrityError('Adapter terminal original changed')
    def retain(item):
        from . import contracts as c
        source=c.regular(Path(item['path']));info=source.stat()
        key=(str(source),info.st_dev,info.st_ino,info.st_mtime_ns,item['size_bytes'])
        if key in retained:return dict(retained[key],source=item)
        original=proof(source,context=operation_context)
        if any(original[key]!=item[key] for key in ('size_bytes','file_stat') if key in item):
            raise IntegrityError('Execution observation original changed during collection')
        if direct_local and source.stat().st_mode & 0o777 != 0o400:
            raise IntegrityError('Local observation original has not been sealed by its stopped writer')
        retained[key]=original
        return dict(original,source=item)
    proofs=[retain(item) for item in proofs]
    outputs=[];names=set()
    if lifecycle_capabilities(adapter).collect:
        descriptors=adapter.collect_original(call_id) if collected is None else collected
        for descriptor in descriptors:
            inventory.entry();inventory.file(descriptor.size_bytes)
            name=Path(descriptor.name)
            if (name.is_absolute() or '..' in name.parts or not name.parts or name.as_posix()!=descriptor.name
                    or '\\' in descriptor.name or descriptor.name in names):
                raise IntegrityError('Collected observation requires distinct canonical names')
            names.add(descriptor.name)
            item=proof(descriptor.local_path,context=operation_context)
            if item['size_bytes']!=descriptor.size_bytes:
                raise IntegrityError('Collected observation original changed')
            outputs.append(dict(retain(item),name=descriptor.name,descriptor=descriptor.model_dump(mode='json')))
    value=dict(version=OBSERVATION_VERSION,call_id=call_id,scope=accepted['scope'],
        mechanism_binding=accepted.get('mechanism_binding'),interface=accepted['interface'],
        external_run_id=external,status=status,accepted=proof(directory/'accepted.json'),
        completed=proof(directory/'completed.json'),submission=proof(directory/'submission.json'),proofs=proofs,outputs=outputs)
    if 'native_state' in completed:value['native_state']=completed['native_state']
    if config['execution_interface'].get('terminal_evidence')=='local_process':
        value['native_terminal']=proof(directory/'terminal.json')
    # Validate the original terminal before any optional diagnostic or science
    # projection. Diagnostics share that original call's deadline and never replay.
    verify_observation(dict(value,public_result=dict(status=status,call_id=call_id),
        scientific_source=None,scientific_outputs=[]),lambda item:Path(item['path']),context=operation_context)
    from . import replay_check
    replay_check.complete(config,adapter,call_id,value)
    source,source_version=_source_projection(config,accepted)
    value['scientific_source']=retain(source) if source is not None else None
    if source_version is not None:value['source_code_version']=source_version
    value['public_result']=_run_view(adapter.public_result(result))
    selected=scientific_outputs(config,value,adapter=adapter,include_logs=True,operation_context=operation_context)
    value['scientific_outputs']=[dict(retain(proof(item.local_path,context=operation_context)),
        name=item.name,descriptor=item.model_dump(mode='json')) for item in selected]
    verify_observation(value,lambda item:Path(item['path']),context=operation_context)
    with records.lease('observation-'+call_id+'.lock'):
        if path.exists():
            if json.loads(read(path))!=value:raise IntegrityError('Frozen execution observation changed')
        else:write(path,value,root=storage_root,policy=config.get('storage_policy'),context=operation_context)
    from . import execution_authority
    execution_authority.reconcile_resources(config,adapter,call_id,observation=value,operation_context=operation_context)
    _cleanup_terminal_code_export(config,call_id,value)
    return value


def dispatch_descriptor(config, payload, *, parent=None, new=False, change=None, mechanism=None, command_options=None,_source_script=None):
    from .protocol import require_scope
    require_scope(config.get('scope'))
    from . import execution_authority, experiment_client, storage_policy as storage
    root=experiment_client.root_for_configuration(config,allow_standalone=True)
    with instance_owner.lease_for_configuration(config), storage.bind(root,config.get('storage_policy')):
        try:return _dispatch_descriptor(config,payload,parent=parent,new=new,change=change,mechanism=mechanism,
            command_options=command_options,_source_script=_source_script)
        except execution_authority.AuthorityRequired as error:return error.feedback
        except LocalPreparationCleanupError:
            return dict(status='known_not_started',accepted=False,error='local_cleanup_failed',
                note='Local temporary preparation could not be cleaned; this dispatch did not submit an execution.')
        except BlockingIOError:return dict(status='unknown',note='Original accepted execution still owns its lease')
        except ConcurrentExecution as error:return dict(status='unknown',note=str(error))


def _admission_conflict(config, expected_calls):
    """Only called for control admission failures before any new submission."""
    records=Records(config)
    changed=[path for path in records.calls() if path.name not in expected_calls]
    if changed:
        call_id=changed[-1].name
        try:
            _,adapter=original_adapter(config,call_id,'query')
            state=execution_status(adapter.status_details(call_id).get('state'))
        except (OSError,ConcurrentExecution):state='unknown'
        return dict(status=state,call_id=call_id,
            note='Another original execution was accepted; preserve and query this exact call.')
    return dict(status='known_not_started',accepted=False,
        note='Execution admission did not accept this dispatch; no new call was submitted.')


def _dispatch_descriptor(config, payload, *, parent=None, new=False, change=None, mechanism=None, command_options=None,_source_script=None):
    from . import contracts as c, experiment_client
    records=Records(config)
    with ExitStack() as originals:
        try:originals.enter_context(c.record_writer(records.root,blocking=False))
        except BlockingIOError:
            return dict(status='known_not_started',accepted=False,
                note='Managed execution records are busy; this dispatch did not prepare or accept a call.')
        instance=experiment_client.root_for_configuration(config,allow_standalone=True)
        if instance is not None:
            from . import shared_input_views
            shared_input_views.dynamic_root(instance,c.workspace(instance,config['scope']['step']),'retained-experiments')
        original=_original_dispatch_state(config,payload,parent=parent,new=new,change=change,command_options=command_options)
    if isinstance(original,dict):return original
    adapter,capabilities,expected_calls=original
    with ExitStack() as preparation:
        try:
            preparation.enter_context(c.record_writer(records.root,blocking=False))
            preparation.enter_context(records._preparation_lease())
        except BlockingIOError:
            return dict(status='known_not_started',accepted=False,
                note='Managed execution records are busy; this dispatch did not prepare or accept a call.')
        prepared=_prepare_descriptor(config,payload,adapter,records,expected_calls,
            parent=parent,new=new,change=change,mechanism=mechanism,command_options=command_options,
            _source_script=_source_script)
    if isinstance(prepared,dict):return prepared
    with c.record_writer(records.root):
        if prepared is None:return _admission_conflict(config,expected_calls)
        descriptor,accepted=prepared
        return _submit_descriptor(config,adapter,capabilities,descriptor,accepted)


def _pending_dispatch(config, instance):
    from . import preparation_recovery, execution_recovery, storage_policy as storage
    if instance is None:return None
    unresolved=[row for row in preparation_recovery.retained_preparations(instance,Path(config['record_dir']),context=storage.Deadline(65)) if not row['resolved']]
    if unresolved:
        row=unresolved[0]
        return dict(status='unknown',call_id=row['call_id'],
            note='Original host preparation is unresolved; no new preparation or submission is permitted.')
    if execution_recovery.pending(instance):
        return dict(status='unknown',note='Original host execution recovery is unresolved; preserve its identity without a new call.')
    return None


def _original_dispatch_state(config, payload, *, parent=None, new=False, change=None, command_options=None):
    from .protocol import require_scope
    require_scope(config.get('scope'))
    _require_new_execution(config)
    from . import experiment_client, experiments as common
    instance=experiment_client.root_for_configuration(config,allow_standalone=True)
    pending=_pending_dispatch(config,instance)
    if pending is not None:return pending
    repair=config.get('engineering_repair')
    if repair is not None and (parent!=repair['candidate_id'] or new or not change or Records(config).calls()):
        raise ValueError('Engineering repair allows one exact parent candidate call with a declared change')
    adapter = load(config)
    if command_options is not None:
        signature = inspect.signature(adapter.acceptance_metadata)
        signature.bind(payload, parent=parent, new=new, change=change)
        try:
            signature.bind(payload, parent=parent, new=new, change=change, command_options=command_options)
        except TypeError as error:
            raise CommandOptionsError('Selected executor does not support --experiment-dir or script arguments; read its public capabilities and use the declared invocation') from error
    capabilities = lifecycle_capabilities(adapter)
    if not capabilities.atomic_admission:
        raise UnsupportedExecution('Implementation does not support managed admission')
    records = Records(config)
    before=records.calls()
    for path in before:
        if retirement(original_configuration(config,path.name),path.name,verify_files=False) is not None:continue
        try:
            original,prior=original_adapter(config,path.name,'query')
            details=prior.status_details(path.name)
            state=execution_status(details.get('state'))
            if state not in {'known_success','known_failed'} or records.external(path.name) is None:
                return dict(status=state if state in PENDING_EXECUTION_STATES and records.external(path.name) is not None else 'unknown',
                    call_id=path.name,note='Preserve original execution; query its identity instead of replay')
            observed=reconcile_saved_observation(original,prior,path.name)
        except (ReviewRequired,common.ReviewRequired,breadth.OpportunityRequired):
            return dict(status='unknown',call_id=path.name,
                note='Original accepted execution outcome is unknown; reconcile this exact call without resubmission.')
        if observed is None:
            return dict(status='unknown',call_id=path.name,
                note='Original terminal execution has no verified observation; preserve its identity.')
        if details.get('state')=='cancelled' and observed.get('native_state')!='cancelled':
            raise IntegrityError('Original cancellation has no bound native terminal observation')
    expected_calls=[path.name for path in before]
    return adapter,capabilities,expected_calls


def _prepare_descriptor(config, payload, adapter, records, expected_calls, *, parent=None, new=False, change=None, mechanism=None, command_options=None,_source_script=None):
    from . import code_versions, experiment_client, preparation_recovery, storage_policy as storage
    instance=experiment_client.root_for_configuration(config,allow_standalone=True)
    pending=_pending_dispatch(config,instance)
    if pending is not None:return pending
    if [path.name for path in records.calls()]!=expected_calls:return None
    logical=Path(_source_script or payload.local_path).absolute()
    selection=experiment_client.code_selection(config,logical,resolved=payload.local_path,
        experiment_directory=(command_options or {}).get('experiment_directory'))
    if (selection is not None and selection['logical_root']=='project'
            and config['execution_interface']['executor']!='local_execution:command'):
        raise UnsupportedExecution('Project directory execution requires the managed Local runtime; compile a self-contained implementation JSON for this executor.')
    _require_new_execution(config)
    try:records.preflight(expected_calls=expected_calls,mechanism=mechanism)
    except (ReviewRequired,breadth.OpportunityRequired) as error:_raise_admission_refusal(error)
    except ConcurrentExecution:return None
    call_id = f'{len(expected_calls)+1:04d}-{uuid.uuid4().hex}'
    if config.get('host_execution_authority') is not None:
        from . import execution_authority
        try:call_id=execution_authority.host_services(config).call_identity(payload,len(expected_calls)+1,mechanism)
        except breadth.OpportunityRequired as error:_raise_admission_refusal(error)
    descriptor = payload.model_copy(update={'name':call_id})
    options = dict(command_options=command_options) if command_options is not None else {}
    from .local_experiments import LocalExecution
    local_preparation=(type(adapter) is LocalExecution
        and config['execution_interface']['executor']=='local_execution:command')
    operation_context=storage.Deadline(config['max_seconds'])
    # Legacy direct Local input selection is host-owned and effect-free. New W
    # code reaches every adapter through its exact Git export first.
    local_metadata=(adapter.acceptance_metadata(descriptor,parent=parent,new=new,change=change,**options)
        if local_preparation and selection is None else None)
    code_version=None;preparation_started=False;published=False
    try:
        if selection is not None:
            try:
                logical=selection['code_root']/selection['entry']
                descriptor=descriptor.model_copy(update={'local_path':str(logical)})
                if proof(descriptor.local_path,context=operation_context)['size_bytes']!=descriptor.size_bytes:
                    raise IntegrityError('Selected code changed before capture')
                code_version=code_versions.capture(instance,step=config['scope']['step'],call_id=call_id,
                    **selection,context=operation_context)
                tree=code_versions.materialize(instance,step=config['scope']['step'],call_id=call_id,
                    code_version=code_version,context=operation_context)
                original=proof(tree/code_version['entry'],context=operation_context)
                descriptor=descriptor.model_copy(update=dict(local_path=original['path'],size_bytes=original['size_bytes']))
            except (OSError,ValueError,storage.StorageQuotaExceeded) as error:
                raise CommandOptionsError('Selected code could not be fixed before preparation; check the dedicated code scope, supported file types and capture limits.') from error
            if local_preparation:
                local_metadata=adapter.acceptance_metadata(descriptor,parent=parent,new=new,change=change,
                    command_options=dict(command_options or {},source_script=str(logical)),
                    _code_version=dict(code_version))
        preparation_started=True
        prepared = adapter.prepare(descriptor, preparation_profile='research_loop.v1', exchange_directory=records.root)
        try:
            metadata=(local_metadata if local_preparation else
                adapter.acceptance_metadata(prepared,parent=parent,new=new,change=change,**options))
        except TypeError as error:
            if options:raise UnsupportedExecution('Selected executor does not support local command options') from error
            raise
        metadata=dict(metadata)
        if 'code_version' in metadata:
            raise IntegrityError('Adapters cannot supply host-owned code versions')
        if 'base_identity' in metadata:
            raise IntegrityError('Adapters cannot supply host-owned project identity')
        if instance is not None:
            from . import contracts as c
            job=c.read(c.regular(instance/'jobs'/f'{config["scope"]["step"]:03d}.json'))
            if job.get('project') is not None:metadata['base_identity']=dict(job['project']['base_identity'])
        if code_version is not None:
            metadata['code_version']=dict(code_version)
            metadata['source_script']=str(logical)
            if 'command' in metadata and 'script_index' in metadata:
                metadata['command'][metadata['script_index']]=str(logical)
            metadata.setdefault('snapshot_files',[])
            for key in ('entry','source_reference','source_input'):metadata.pop(key,None)
        elif _source_script is not None:
            metadata['source_script']=_source_script
            if not local_preparation and 'command' in metadata and 'script_index' in metadata:
                metadata['command'][metadata['script_index']]=_source_script
        source_contracts=[item for item in adapter.capabilities() if item.key==INPUT_SOURCES_KEY]
        if len(source_contracts)>1:raise IntegrityError('Ambiguous managed input source declaration')
        if source_contracts:
            from . import execution_inputs
            item=source_contracts[0]
            actual=[row for row in _public_capabilities(adapter) if row['key']==INPUT_SOURCES_KEY]
            frozen=[row for row in config['execution_interface'].get('public_capabilities',[]) if row['key']==INPUT_SOURCES_KEY]
            if frozen!=actual:raise IntegrityError('Managed input source capability differs from frozen interface')
            local_contract = (item.schema_id==INPUT_SOURCES_SCHEMA and item.content==encoded(INPUT_SOURCES_CONTRACT))
            exact_contract = (item.schema_id==execution_inputs.SOURCE_SCHEMA and item.content==encoded(execution_inputs.SOURCE_CONTRACT))
            if (item.kind=='source_provenance' and (local_contract or exact_contract)
                    and item.payload_schema_version==1 and item.media_type=='application/json'):
                method=getattr(adapter,'input_sources',None)
                metadata['input_sources']=dict(contract=dict(key=item.key,kind=item.kind,schema_id=item.schema_id,
                    payload_schema_version=item.payload_schema_version,media_type=item.media_type,
                    content=item.content.decode('utf-8'),size_bytes=len(item.content)),
                    view=method(prepared,metadata) if callable(method) else None)
        from . import replay_check
        metadata['engineering_check'] = replay_check.acceptance(config, adapter)
        if (metadata.get('code_version')!=code_version
                or code_version is None and 'code_version' in metadata):
            raise IntegrityError('Adapters cannot change host-owned code versions')
        try:
            accepted = records.accept(prepared, metadata, config['execution_interface'],
                expected_calls=expected_calls,mechanism=mechanism,operation_context=operation_context,
                _local_preparation=local_preparation)
        except (ReviewRequired,breadth.OpportunityRequired,ConcurrentExecution) as error:
            if local_preparation:
                if isinstance(error,ConcurrentExecution):return None
                _raise_admission_refusal(error)
            # prepare may already have affected an external implementation. Local
            # snapshot removal cannot establish that the original preparation ended.
            if instance is not None:
                pending=[row for row in preparation_recovery.retained_preparations(instance,records.root,
                    context=storage.Deadline(65)) if row['call_id']==call_id and not row['resolved']]
                if pending:
                    return dict(status='unknown',call_id=call_id,
                        note='Original host preparation preceded admission refusal and remains unresolved; preserve this exact call without a new preparation or submission.')
            return dict(status='unknown',
                note='Execution preparation preceded admission refusal and may have external effects; preserve the original preparation without a new preparation or submission.')
        published=True
        return prepared,accepted
    finally:
        if code_version is not None and not published and (not preparation_started or local_preparation):
            try:code_versions.cleanup_export(instance,step=config['scope']['step'],call_id=call_id,
                context=storage.Deadline(65))
            except (OSError,ValueError) as error:
                raise LocalPreparationCleanupError('Unsubmitted code export cleanup failed before execution') from error


def _submit_descriptor(config, adapter, capabilities, prepared, accepted):
    from . import experiments as common
    records=Records(config);call_id=accepted['call_id']
    try:
        verify_scientific_inputs(accepted,config=config)
        if 'code_version' in accepted:
            from . import code_versions, experiment_client, storage_policy as storage, worker_execution_bridge
            root=experiment_client.root_for_configuration(config)
            if _code_export_entry(root,accepted) is not None:
                context=storage.Deadline(config['max_seconds'],upstream=worker_execution_bridge.operation_context())
                try:code_versions.materialize(root,step=config['scope']['step'],call_id=call_id,
                    code_version=accepted['code_version'],context=context)
                except (OSError,ValueError) as error:
                    raise IntegrityError('Accepted prepared code export differs from its original pin') from error
            try:current=proof(accepted['script']['path'])
            except (OSError,ValueError) as error:
                raise IntegrityError('Accepted prepared execution original changed or is unavailable') from error
            if current!=accepted['script']:
                raise IntegrityError('Accepted prepared execution original changed or is unavailable')
        adapter.dispatch_lease(accepted)
        external_id,state=adapter.submit(prepared)
        records.bind_external(call_id,external_id,state)
        observed=record_observation(config,adapter,call_id)
        result=json.loads(read(observed['completed']['path'])) if observed is not None else adapter.result(call_id)
        if observed is None and capabilities.query and records.external(call_id) is not None:
            state=execution_status(adapter.status_details(call_id).get('state'))
            if state in PENDING_EXECUTION_STATES:
                result=dict(result,status=state,note='Original execution is '+state+'; preserve its identity without resubmission.')
    except (BlockingIOError,ConcurrentExecution,common.ConcurrentExecution,
            ReviewRequired,common.ReviewRequired,breadth.OpportunityRequired):
        return dict(status='unknown',call_id=call_id,
            note='Original accepted execution outcome is unknown; reconcile this exact call without resubmission.')
    return result


@_configuration_effect
def progress(config):
    adapter = load(config)
    if not lifecycle_capabilities(adapter).query:
        raise UnsupportedExecution('Execution query unavailable')
    rows = []
    counts = dict.fromkeys(('accepted', 'submitted', 'queued', 'running', 'cancelling', 'known_success', 'known_failed', 'not_submitted', 'unknown'), 0)
    for path in Records(config).calls():
        if retirement(original_configuration(config,path.name),path.name,verify_files=False) is not None:
            counts['not_submitted']+=1
            rows.append(dict(call_id=path.name,status='not_submitted',candidate_id=None))
            continue
        binding=None
        try:
            _,prior=original_adapter(config,path.name,'query')
            details = prior.status_details(path.name)
            binding=Records(config).accepted(path.name).get('mechanism_binding')
        except (FileNotFoundError,json.JSONDecodeError,UnicodeDecodeError):
            details=dict(state='unknown')
        except IntegrityError:
            raise
        except (OSError,KeyError,TypeError,AttributeError,RecursionError) as error:
            raise IntegrityError('Published execution metadata is malformed or unavailable') from error
        state = execution_status(details.get('state'))
        counts[state] += 1
        rows.append(dict(call_id=path.name, **(dict(mechanism_ref=binding['mechanism_ref']) if binding else {}), status=state, candidate_id=details.get('candidate_id'),
            evaluation_key=details.get('evaluation_key'), metric=details.get('metric'), direction=details.get('direction'),
            wall_seconds=details.get('wall_seconds'), parent_candidate=details.get('parent_candidate'),
            **(dict(native_state='cancelled') if details.get('state')=='cancelled' else {}),
            **(dict(returncode=details['returncode'],result=_run_view(details['result'])) if details.get('result') is not None else {})))
    return dict(version=1, calls=rows[-16:], counts=counts, omitted_count=max(0, len(rows)-16),
        coverage='partial' if counts['unknown'] else 'complete', observed_at=time.time())


def preserved_progress(config):
    records=Records(config);rows=[];counts=dict.fromkeys(('accepted','running','known_success','known_failed','unknown'),0)
    for directory in records.calls():
        records.accepted(directory.name)
        path=directory/'completed.json'
        value=json.loads(read(path)) if path.exists() else {}
        status=value.get('status','unknown')
        if status not in counts:status='unknown'
        counts[status]+=1
        rows.append(dict(call_id=directory.name,status=status,qualification='Preserved receipt; no current scientific interpretation'))
    return dict(calls=rows[-16:],counts=counts,omitted_count=max(0,len(rows)-16),coverage='preserved_originals_only')


@_configuration_effect
def compile_implementation(config, adapter, source, aliases, output):
    """Optional registered authoring; this does not dispatch or grant execution."""
    _require_new_execution(config)
    capabilities = [item for item in _public_capabilities(adapter) if item['key'] == 'implementation_authoring']
    if (len(capabilities) != 1 or capabilities[0] not in config['execution_interface'].get('public_capabilities', [])
            or not callable(getattr(adapter, 'compile_implementation', None))):
        raise UnsupportedExecution('Selected implementation does not declare optional scientific compilation')
    if not isinstance(aliases, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in aliases.items()):
        raise ValueError('Scientific file choices must map slot names to exact bound input aliases')
    if config.get('worker_view_version')!=1:return adapter.compile_implementation(source, aliases, output)
    from . import contracts as c,experiment_client
    root=experiment_client.root_for_configuration(config);work=c.workspace(root,config['scope']['step'])
    source=experiment_client.code_path(config,source)
    output=Path(output).absolute()
    if output.resolve()!=output or not output.is_relative_to(work/'artifacts') or output.exists():
        raise ValueError('Choose a new scientific implementation output under this task artifacts')
    private=Path(config['record_dir']).parent/'implementations'
    private.mkdir(parents=True,exist_ok=True)
    entries=list(private.iterdir())
    if any(path.resolve()!=path or not path.is_dir() or re.fullmatch(r'I[0-9]{3,}',path.name) is None for path in entries):
        raise IntegrityError('Compiled implementation namespace changed')
    reference=f'I{len(entries)+1:03d}';directory=private/reference;directory.mkdir()
    package=directory/'package.json'
    result=adapter.compile_implementation(source,aliases,package)
    if result.get('status')!='compiled' or Path(result.get('path',''))!=package:
        raise IntegrityError('Compiler did not retain the exact private implementation')
    original=proof(package)
    client=dict(worker_implementation_version=1,implementation_ref=reference)
    c.write(directory/'client.json',dict(client=client,public_path=str(output),package=original))
    c.write(output,client);output.chmod(0o400)
    return dict(status='compiled',path=str(output.relative_to(work)),qualification=result.get('qualification'))


class CallerError(ValueError):
    """A rejected public invocation, before an accepted execution exists."""
    def __init__(self,note,*,error='invalid_arguments'):
        super().__init__(note);self.error=error


class _CallerArgumentParser(argparse.ArgumentParser):
    def error(self,message):
        # argparse may embed user paths/values in message; expose declared usage
        # only, never control paths or a checksum provided as an invalid value.
        raise CallerError(f'Check required options with {self.prog} --help. Use the assigned task science files.')


def caller_feedback(error):
    return dict(status='caller_error',accepted=False,error=error.error,note=str(error))


def _cli_parser(*,configuration=True):
    parser=_CallerArgumentParser(prog='exp',description=__doc__)
    if configuration:parser.add_argument('--config',type=Path,required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run');payload=run.add_mutually_exclusive_group(required=True)
    payload.add_argument('--script',type=Path);payload.add_argument('--payload',type=Path)
    run.add_argument('--experiment-dir',type=Path,help='When declared by the selected executor, snapshot this directory excluding artifacts/; unavailable for Cov single-source execution')
    choice=run.add_mutually_exclusive_group();choice.add_argument('--parent-plan');choice.add_argument('--new-plan',action='store_true')
    run.add_argument('--change-intent');run.add_argument('--mechanism')
    run.add_argument('arguments',nargs=argparse.REMAINDER,help='When declared by the selected executor, script arguments after --; passed as argv, never shell text')
    sub.add_parser('lineage-status')
    compile_command = sub.add_parser('compile', help='Optional local compilation of scientific slots; grants no execution')
    compile_command.add_argument('--implementation', type=Path, required=True)
    compile_command.add_argument('--scientific-files', type=Path, required=True, help='JSON slot-name to listed scientific input alias map; {} for self-contained sources')
    compile_command.add_argument('--output', type=Path, required=True, help='New package under this task artifacts/')
    capabilities=sub.add_parser('capabilities',help='Read compact declared public input contracts and lifecycle support')
    capabilities.add_argument('--start',type=int,default=0)
    describe=sub.add_parser('describe',help='Copy one complete declared public contract to an explicit new file')
    describe.add_argument('--key',required=True);describe.add_argument('--output',type=Path,required=True)
    describe.add_argument('--metadata',action='store_true',help='Copy complete public contract metadata instead of its content')
    for name in ('query','lookup','collect','cancel','reconcile','retire'):
        item=sub.add_parser(name,**(dict(help='Freeze the same original terminal facts using query and declared collection; never submit a new execution.') if name=='reconcile' else {}))
        item.add_argument('--call-id',required=True)
    return parser,sub


def cli_help(command=None):
    parser,commands=_cli_parser(configuration=False)
    if command is not None:
        if command not in commands.choices:raise CallerError('Choose an existing command with exp help.')
        parser=commands.choices[command]
    return dict(status='help',usage=parser.format_help(),
        note='Write ordinary science files, then use the declared command. Control retains originals; never repeat an unknown call.')


def main(argv=None):
    # -m runs this file as __main__; registered factories import its canonical name.
    from . import breadth, feedback, experiments as common
    parser,_=_cli_parser()
    try:args=parser.parse_args(argv)
    except CallerError as error:
        print(json.dumps(caller_feedback(error),allow_nan=False));return 2
    args.config=args.config.absolute()
    try:args.config.lstat()
    except FileNotFoundError:
        result=caller_feedback(CallerError('Configuration client is missing. From the assigned task use --config work/experiment-config.json or exp help; do not recreate control configuration.',error='config_not_found'))
        print(json.dumps(result,allow_nan=False));return 2
    except OSError:
        print(json.dumps(dict(status='integrity_error',note='Configuration client cannot be read; preserve its original and report the unavailable input.'),allow_nan=False));return 3
    owner_lease = ExitStack();feedback_config=None;accepted_call_id=None
    try:
        config=json.loads(read(args.config.absolute()))
        if isinstance(config,dict) and 'worker_view_version' in config and 'scope' not in config:
            from . import experiment_client
            config=experiment_client.resolve(args.config,config)
        feedback_config=config
        from . import protocol
        scope=config.get('scope',{})
        if args.command=='lineage-status' and (type(scope.get('handoff_protocol')) is not int or scope['handoff_protocol']!=protocol.CURRENT):
            print(json.dumps(preserved_progress(config),allow_nan=False));return 0
        owner_lease.enter_context(instance_owner.lease_for_configuration(config))
        if args.command in {'run','compile','lookup','collect','cancel','reconcile','retire'}:
            from .protocol import require_scope
            require_scope(config.get('scope'))
        adapter=load(config,path=args.config)
        if args.command in {'capabilities','describe'}:
            frozen_declared=_public_capabilities(adapter)
            frozen=config['execution_interface'].get('public_capabilities')
            if frozen is not None and frozen_declared!=frozen:raise IntegrityError('Frozen public execution contract changed')
            worker_contracts=_worker_capabilities(adapter)
            declared=[_capability_view(item) for item in worker_contracts]
            if args.command=='capabilities':
                if not 0<=args.start<=len(declared):raise ValueError('Exact capability continuation position required')
                result=dict(executor=config['execution_interface']['executor'],lifecycle=config['execution_interface']['capabilities'],contracts=[])
                for item in declared[args.start:]:
                    compact=dict(item)
                    if len(encoded(compact))>8000:compact['public_summary']=dict(view_status='details_omitted')
                    if len(encoded(compact))>8000:
                        compact={key:item[key] for key in ('key','kind','schema_id','media_type','payload_schema_version')}
                        compact['metadata_view_status']='details_omitted; describe --metadata'
                    if len(encoded(dict(result,contracts=[*result['contracts'],compact])))>14_000:break
                    result['contracts'].append(compact)
                position=args.start+len(result['contracts'])
                result.update(start=args.start,omitted_count=len(declared)-len(result['contracts']),
                    next_start=position if position<len(declared) else None)
                result['read_contract']='describe --key <key> --output <new scratch file>; add --metadata for full public metadata'
            else:
                items=[item for item in worker_contracts if item.key==args.key]
                if len(items)!=1:raise UnsupportedExecution('Exact declared public capability key unavailable')
                item=items[0];expected=next(row for row in declared if row['key']==args.key)
                # Recheck the producer's public bytes without changing the frozen
                # complete host-capability equality above.
                if items != [row for row in _worker_capabilities(adapter) if row.key==args.key]:
                    raise IntegrityError('Public execution contract changed while reading')
                output=args.output.absolute()
                if output.resolve()!=output or not output.parent.is_dir():raise IntegrityError('Exact new public contract output path required')
                with output.open('xb') as stream:
                    stream.write(encoded(expected) if args.metadata else item.content);stream.flush();os.fsync(stream.fileno())
                result=dict(key=item.key,contract=dict(path=str(output),size_bytes=output.stat().st_size),kind='public_metadata' if args.metadata else 'public_contract')
            code=0
        elif args.command=='compile':
            from . import experiment_client
            aliases=experiment_client.input_reference(config,args.scientific_files)
            result=compile_implementation(config,adapter,args.implementation,json.loads(read(aliases['path'])),args.output)
            result={key:result[key] for key in ('status','path','qualification') if key in result}
            code=0
        elif args.command=='run':
            arguments=args.arguments[1:] if args.arguments[:1]==['--'] else args.arguments
            options=dict(arguments=arguments,experiment_directory=str(args.experiment_dir.absolute()) if args.experiment_dir else None)
            parent=args.parent_plan
            if parent is not None and instance_owner.configuration_root(config) is not None:
                from . import candidate_refs,contracts as c
                root=instance_owner.configuration_root(config)
                job=c.read(c.regular(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
                parent=candidate_refs.resolve_reference(root,job,parent)
            result=dispatch(config,args.script or args.payload,parent=parent,new=args.new_plan,change=args.change_intent,mechanism=args.mechanism,
                command_options=options if arguments or args.experiment_dir else None)
            observation=None
            if isinstance(result.get('call_id'),str):
                call_id=result['call_id'];records=Records(config)
                if not ID.fullmatch(call_id) or call_id in {'.','..'}:
                    raise IntegrityError('Execution response changed its exact original call identity')
                approval=result.get('status')=='authorization_required'
                if approval:
                    from . import execution_authority,qualification as q
                    root,job,intent=execution_authority._host_context(config)
                    request_ref=result.get('authorization_request')
                    if not isinstance(request_ref,dict) or not isinstance(request_ref.get('path'),str):
                        raise IntegrityError('Execution approval response has no original request')
                    request,actual=execution_authority.request(root,Path(request_ref['path']).parent.name)
                    if (request_ref!=actual or request.get('call_id')!=call_id
                            or request.get('job')!=q._ref(root,root/'jobs'/f'{job["step"]:03d}.json')
                            or request.get('native_intent')!=intent):
                        raise IntegrityError('Execution approval response escaped its original task and call')
                directory=records.root/'calls'/call_id
                if directory.resolve()!=directory:
                    raise IntegrityError('Execution response call namespace changed')
                preaccept_unknown=False
                if result.get('status')=='unknown' and not directory.exists():
                    from . import preparation_recovery,experiment_client,storage_policy as storage
                    root=experiment_client.root_for_configuration(config,allow_standalone=True)
                    if root is not None:
                        preaccept_unknown=any(row['call_id']==call_id and not row['resolved']
                            for row in preparation_recovery.retained_preparations(root,records.root,context=storage.Deadline(65)))
                if not (approval or preaccept_unknown) or directory.exists():
                    records.accepted(call_id)
                    accepted_call_id=call_id
                    observation=records.directory(call_id)/'execution-observation.json'
            if observation is not None and observation.exists():
                observed=json.loads(read(observation))
                verify_observation(observed,lambda item:Path(item['path']),interface=config['execution_interface'])
                result=scientific_result(config,Records(config).accepted(result['call_id']),result,observed)
            elif result.get('status')=='known_not_started' and result.get('accepted') is False:
                result={key:result[key] for key in ('status','accepted','error','note') if key in result}
            else:result=adapter.public_result(result)
            result=_run_view(result)
            code={'known_success':0,'known_failed':1}.get(result['status'],2)
        elif args.command=='lineage-status':result=progress(config);code=0
        elif args.command=='retire':
            config,adapter=original_adapter(config,args.call_id,'retire')
            accepted_call_id=args.call_id
            result=retire_original(config,adapter,args.call_id)
            result={key:result[key] for key in ('call_id','status','note','resource_accounting') if key in result}
            if isinstance(result.get('resource_accounting'),dict):result['resource_accounting']=_accounting_view(result['resource_accounting'])
            code=0 if result.get('status')=='not_submitted' else 2
        else:
            capability='lookup_submission' if args.command=='lookup' else 'query' if args.command in {'query','reconcile'} else args.command
            if not getattr(lifecycle_capabilities(adapter),capability):
                raise UnsupportedExecution('Requested original execution operation unavailable')
            config,adapter=original_adapter(config,args.call_id,capability)
            accepted_call_id=args.call_id
            if args.command=='collect':
                from . import storage_policy as storage
                operation_context=storage.Deadline(config['max_seconds'])
                storage.check(operation_context)
            if args.command=='query':result=adapter.status_details(args.call_id)
            elif args.command=='reconcile':
                observed=record_observation(config,adapter,args.call_id)
                from . import replay_check
                replay_check.complete(config, adapter, args.call_id, observed)
                _cleanup_terminal_code_export(config,args.call_id,observed)
                state=execution_status(adapter.status_details(args.call_id).get('state')) if observed is None and Records(config).external(args.call_id) is not None else 'unknown'
                pending=state in PENDING_EXECUTION_STATES
                result=dict(call_id=args.call_id,status=observed['status'] if observed is not None else state if pending else 'unknown',
                    **(dict(native_state=observed['native_state']) if observed is not None and 'native_state' in observed else {}),
                    note='Original terminal observation frozen; no new submission.' if observed is not None else 'Original accepted execution is '+state+'.' if pending else 'Original execution has no verified terminal observation.')
            elif args.command=='lookup':
                records=Records(config);accepted=records.accepted(args.call_id)
                descriptor=LocalFileDescriptor.model_validate(accepted['prepared'])
                found=adapter.lookup_submission(descriptor)
                if found is None:result=dict(state='unknown',call_id=args.call_id)
                else:
                    records.bind_external(args.call_id,*found)
                    result=adapter.status_details(args.call_id)
            elif args.command=='collect':
                descriptors=adapter.collect_original(args.call_id)
                storage.check(operation_context)
                result=dict(status='collected',outputs=[item.model_dump(mode='json') for item in descriptors])
            else:result=dict(status=adapter.cancel_original(args.call_id))
            if args.command=='collect':
                cached=(Records(config).directory(args.call_id)/'execution-observation.json').exists()
                if cached:
                    observed=reconcile_saved_observation(config,adapter,args.call_id,operation_context=operation_context)
                else:
                    observed=record_observation(config,adapter,args.call_id,collected=descriptors,
                        operation_context=operation_context)
                if observed is None:raise IntegrityError('Collected execution has no verified terminal observation')
                if instance_owner.configuration_root(config) is not None:
                    result=_collected_view(config,args.call_id,observed,adapter,operation_context=operation_context)
            elif args.command in {'query','lookup'} and execution_status(result.get('state')) in {'known_success','known_failed'}:
                if (Records(config).directory(args.call_id)/'execution-observation.json').exists():
                    reconcile_saved_observation(config,adapter,args.call_id)
            if args.command in {'query','lookup'}:result=_status_view(result)
            code=2 if args.command=='reconcile' and result['status'] not in {'known_success','known_failed'} else 0
        result=_candidate_view(config,result)
        call_id = result.get('call_id', getattr(args, 'call_id', None))
        if call_id is not None and 'engineering_check' not in result:
            from . import replay_check
            result['engineering_check'] = replay_check.public(config, call_id)
        from . import experiment_client,contracts as c
        root=experiment_client.root_for_configuration(feedback_config,allow_standalone=True)
        work=c.workspace(root,feedback_config['scope']['step']) if root is not None else Path(feedback_config['record_dir']).parent.parent
        result['feedback']=dict(version=1,budget=feedback.budget_hint(work),
            review=review_feedback(feedback_config),
            qualification='Execution metadata does not establish scientific or candidate qualification.')
    except (_AdmissionBudgetRefused,common._AdmissionBudgetRefused) as error:
        if args.command=='run' and accepted_call_id is None:
            result=dict(status=error.status,reason=error.reason,accepted=False,
                note=('The shared budget refused this new execution.' if error.status=='budget_exhausted'
                    else 'Shared usage is unknown; new execution is unavailable.'))
            code=2
        else:
            result=dict(status='unknown',**(dict(call_id=accepted_call_id) if accepted_call_id is not None else {}),
                note=_diagnostic(error,'Execution provider outcome is unknown; reconcile the original call and never repeat submission',config=feedback_config));code=3
    except (_AdmissionReviewRequired,common._AdmissionReviewRequired) as error:result=dict(status='review_required',reason=_diagnostic(error,'Original execution review required; request the declared review before continuing',config=feedback_config),accepted=False);code=2
    except (_AdmissionOpportunityRequired,common._AdmissionOpportunityRequired) as error:result=dict(status='unresolved',accepted=False,note=_diagnostic(error,'Choose an available authorized H mechanism before another request',config=feedback_config));code=2
    except (ReviewRequired,common.ReviewRequired,breadth.OpportunityRequired) as error:
        result=dict(status='unknown',**(dict(call_id=accepted_call_id) if accepted_call_id is not None else {}),
            note=_diagnostic(error,'Execution provider outcome is unknown; reconcile the original call and never repeat submission',config=feedback_config));code=3
    except (ConcurrentExecution,common.ConcurrentExecution) as error:result=dict(status='unknown',note=_diagnostic(error,'Execution preparation outcome is unknown; preserve the original preparation without replay',config=feedback_config));code=2
    except (IntegrityError,common.IntegrityError,feedback.history.ObserverIntegrityError) as error:result=dict(status='integrity_error',note=_diagnostic(error,'Execution original integrity check failed; preserve originals and report this call',config=feedback_config));code=3
    except OSError as error:result=dict(status='integrity_error',note=_diagnostic(error,'Execution original cannot be read; preserve originals and report the unavailable input',config=feedback_config));code=3
    except (CommandOptionsError,common.CommandOptionsError) as error:result=dict(status='known_not_started',accepted=False,error='caller_error',note=_diagnostic(error,'Requested command options are unavailable; check exp help for this command',config=feedback_config));code=2
    except (CallerError,common.CallerError) as error:result=caller_feedback(error);code=2
    except ValueError as error:result=dict(status='unavailable' if isinstance(error,(UnsupportedExecution,common.UnsupportedExecution)) else 'unresolved',note=_diagnostic(error,'Execution request could not be resolved; check declared inputs and preserve any original call',config=feedback_config));code=2
    except Exception as error:result=dict(status='unknown',note=_diagnostic(error,'Execution provider outcome is unknown; reconcile the original call and never repeat submission',config=feedback_config));code=3
    finally:owner_lease.close()
    if args.command in {'run','query','lookup','collect','cancel','reconcile'}:
        from . import managed_wait
        result['handoff']=managed_wait.handoff(feedback_config,result,args.command,getattr(args,'call_id',None))
    # CLI is a compact model view; the complete collection is already durable.
    if isinstance(result.get('outputs'),list):
        original=result['outputs'];result=dict(result,outputs=[])
        for item in original:
            proposed=dict(result,outputs=[*result['outputs'],item])
            if len(encoded(proposed))>16_000:break
            result['outputs'].append(item)
        if len(result['outputs'])<len(original):result['outputs_omitted_count']=len(original)-len(result['outputs'])
    print(json.dumps(result,allow_nan=False));return code


def _code_export_entry(root, accepted):
    """Recognize the saved code entry without requiring its temporary export."""
    version=accepted.get('code_version');script=accepted.get('script');prepared=accepted.get('prepared')
    if not isinstance(version,dict) or not isinstance(script,dict) or not isinstance(prepared,dict):return None
    expected=Path(root)/'host-runtime/code-versions/exports'/f'{accepted["scope"]["step"]:03d}'/accepted['call_id']/'tree'/version['entry']
    if (script.get('path')!=str(expected) or prepared.get('local_path')!=str(expected)
            or prepared.get('size_bytes')!=script.get('size_bytes')):return None
    return {key:script.get(key) for key in ('path','size_bytes','file_stat')}


def _compiler_source_version(compiler, reference, subject, *, project=None):
    """Check only the compiler's saved code role and original producer identity."""
    from . import project_layout
    try:version=project_layout.validate_code(project,compiler['code_version'],purpose='compiler')
    except (KeyError,TypeError,ValueError) as error:
        raise IntegrityError('Compiler source code version is malformed') from error
    path=Path(reference['path']);identity=path.parent.name;step=subject['producer_step']
    if (type(step) is not int or step<=0 or re.fullmatch(r'[0-9a-f]{32}',identity) is None
            or path!=Path(f'native-evidence/{step:03d}/compiled-implementations')/identity/'compiler.json'
            or type(compiler.get('version')) is not int or compiler['version']!=1
            or compiler.get('job',{}).get('path')!=f'jobs/{step:03d}.json'
            or 'author' in compiler or 'author_origin' in compiler or compiler.get('author_input') is not None
            or version['selection']!='file'
            or project is None and Path(version['entry']).name!=version['entry']):
        raise IntegrityError('Compiler source code version changed its exact producer binding')
    _validate_base_identity(project,compiler)
    return dict(version)


def _source_control_metadata(config, accepted, *, verify_signatures=False, source=None):
    """Read the accepted source's existing controls without consuming its science."""
    from . import experiment_client,contracts as c,execution_authority as authority,qualification as q,originals
    if accepted.get('execution_authority') is None:return None
    if source is not None and not source.inherited:source=None
    if source is None:
        root=experiment_client.root_for_configuration(config)
    else:
        source=originals.at_step(source,config['scope']['step'])
        root=Path(source.original_root);job=originals.source_job(source)
        records=(root/'host-runtime/experiment-control'/f'{job["step"]:03d}'/'records'
            if job.get('worker_view_version')==experiment_client.VERSION else c.workspace(root,job['step'])/'work/lineage')
        if config.get('record_dir')!=str(records):raise IntegrityError('Scientific control record namespace changed')
    if verify_signatures and not (root/'execution-authority/host-key-source.json').exists():return None
    def entry_owner(path,reference=None):
        owner=originals.source_for_ref(source,reference or {})
        path=Path(path)
        if path.is_absolute():
            if not path.is_relative_to(owner.original_root):
                owner,_=originals.material_owner(owner,dict(source=str(path)))
            path=path.relative_to(owner.original_root)
        relative=originals.relative(path.as_posix())
        key=(originals.identity_key(owner.origin_identity),relative)
        entries=owner._index.get(key,())
        if not entries:
            parts=path.parts;step=None
            if len(parts)>1 and parts[0] in {'jobs','native-evidence'}:
                number=parts[1].split('.')[0]
                if number.isdecimal():step=int(number)
            if step is not None:
                owner=originals.dependency(owner,step)
                entries=owner._index.get((originals.identity_key(owner.origin_identity),relative),())
        if len(entries)!=1 or entries[0].get('visibility') not in {'host_proof','effect_only'}:
            raise IntegrityError('Scientific control lacks an exact transported control entry')
        return owner,relative,entries[0]
    def control_ref(path):
        if source is None:return q._ref(root,path)
        owner,relative,entry=entry_owner(path)
        result=dict(path=relative,size_bytes=entry['size_bytes'])
        if owner.origin_identity!=source.origin_identity:result['origin_identity']=owner.origin_identity
        return result
    def read_control(reference):
        if source is None:return q._read(root,reference)
        if set(reference)-{'origin_identity'} not in ({'path','size_bytes'},{'path','size_bytes','file_stat'}):
            raise IntegrityError('Scientific control original fields changed')
        owner,relative,entry=entry_owner(reference['path'],reference)
        if 'size_bytes' in reference and reference['size_bytes']!=entry['size_bytes']:
            raise IntegrityError('Scientific control differs from its original size')
        actual=originals.resolve_ref(owner,dict(reference,path=relative))
        data=read(actual,32*1024*1024)
        if len(data)!=entry['size_bytes']:raise IntegrityError('Scientific control bytes changed')
        return json.loads(data)
    def control_address(reference):
        if source is None:return q.control_reference(root,reference)['path']
        owner,relative,_=entry_owner(reference['path'],reference)
        return str(Path(owner.original_root)/relative)
    def control(reference):
        if source is not None:return read_control(reference)
        path=Path(reference['path'])
        actual=path if path.is_absolute() else root/path
        if not actual.is_relative_to(root):raise IntegrityError('Scientific control escaped its instance')
        current=proof(actual)
        if (set(reference) not in ({'path','size_bytes'},{'path','size_bytes','file_stat'})
                or any(current[key]!=reference[key] for key in ('size_bytes','file_stat') if key in reference)):
            raise IntegrityError('Scientific control original changed')
        # q owns path/size controls; the execution fingerprint is checked above.
        return q._read(root,dict(path=actual.relative_to(root).as_posix(),size_bytes=reference['size_bytes']))
    try:
        job=read_control(control_ref(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
        _validate_base_identity(job.get('project'),accepted)
        attempt=accepted.get('native_attempt') or {};number=attempt.get('number')
        if type(number) is not int or not 0<=number<10000:
            raise IntegrityError('Scientific source has no exact native attempt')
        native=root/'native-evidence'/f'{job["step"]:03d}'
        if number:native=native/'continuations'/f'{number:03d}'
        native_config=(c.read(c.regular(native/'experiment-config.json')) if source is None else
            read_control(control_ref(native/'experiment-config.json')))
        authorization=control(accepted['execution_authority'])
        request_ref=authorization['request'];request=read_control(request_ref)
        request_id=Path(request_ref['path']).parent.name
        if not authority.ID.fullmatch(request_id):raise IntegrityError('Scientific source request identity changed')
        directory=root/'execution-authority/requests'/request_id
        body={key:item for key,item in request.items() if key!='nonce_hash'}
        if (request_ref!=control_ref(directory/'request.json')
                or Path(accepted['execution_authority']['path'])!=directory/'authorization.json'
                or authority.security.request_digest(body)!=request_id):
            raise IntegrityError('Scientific source request original changed')
        allocation_ref=authorization['allocation'];allocation=read_control(allocation_ref)
        decision_ref=authorization['decision'];decision=read_control(decision_ref)
        binding=allocation['binding'];subject=request['subject'];call=accepted['call_id']
        alias=request.get('payload_alias',request['payload']);target=accepted.get('execution_target')
        if (native_config.get('scope')!=accepted['scope'] or native_config.get('native_attempt')!=attempt
                or native_config.get('execution_interface')!=accepted['interface']
                or config.get('scope')!=accepted['scope'] or config.get('native_attempt')!=attempt
                or config.get('execution_interface')!=accepted['interface']
                or accepted['scope'].get('job_ref')!=dict(path=str(root/'jobs'/f'{job["step"]:03d}.json'))
                or job.get('role')!='W' or request['job']!=control_ref(root/'jobs'/f'{job["step"]:03d}.json')
                or allocation_ref!=control_ref(root/'execution-authority/calls'/call/'allocation.json')
                or allocation['call_id']!=call or binding['scope']!=accepted['scope']
                or binding['native_attempt']!=attempt or binding['interface']!=accepted['interface']
                or binding['subject']!=subject or binding['review']!=request['review']
                or binding['payload_alias']!=alias or request['call_id']!=call
                or authorization['scope']!=accepted['scope'] or authorization['native_attempt']!=attempt
                or authorization['interface']!=accepted['interface'] or authorization['call_id']!=call
                or authorization['target']!=target or request['target']!=target
                or authorization['payload_alias']!=alias
                or allocation['native_intent']!=request['native_intent']
                or request['native_intent']!=control_ref(native/'native-intent.json')
                or read_control(request['native_intent'])['task_ref']!=q.control_reference(root,request['job'])
                or decision_ref!=control_ref(directory/'decision.json')
                or decision['request']!=request_ref or decision['nonce_hash']!=request['nonce_hash']
                or not authority.security.decision_authorizes(request['kind'],decision['selected_option'],request['options'])):
            raise IntegrityError('Scientific source differs from its exact accepted authority controls')
        if verify_signatures:
            secret=authority.host_secret(root)
            for signed in (authorization,decision):
                signed_body={key:item for key,item in signed.items() if key!='receipt_hmac'}
                if not authority.hmac.compare_digest(signed.get('receipt_hmac',''),
                        authority.security.receipt_hmac(secret,authority.security.canonical(signed_body))):
                    raise IntegrityError('Scientific source host signature changed')
        seal=read_control(subject['producer_seal']);material=subject['material'];step=subject['producer_step']
        if (type(step) is not int or not 0<step<job['step']
                or subject['producer_seal']!=control_ref(c.host_record(c.workspace(root,step))/'result-identity.json')
                or not any({key:value for key,value in row.items() if key!='file_stat'}==material
                    for row in seal.get('materials',[]))
                or material not in [*job.get('direct_materials',[]),*[row['material'] for row in job.get('selected_inputs',[])]]
                or alias['path'] not in {request['payload']['path'],material['source'],str(c.workspace(root,job['step'])/material['path'])}
                or alias['size_bytes']!=request['payload']['size_bytes']
                or accepted['script']['size_bytes']!=accepted['prepared']['size_bytes']):
            raise IntegrityError('Scientific source lost its exact accepted producer material')
        control_paths={str(root/'jobs'/f'{job["step"]:03d}.json'),str(native/'experiment-config.json'),
            accepted['execution_authority']['path'],str(directory/'request.json'),
            str(root/'execution-authority/calls'/call/'allocation.json'),str(directory/'decision.json'),
            str(native/'native-intent.json'),control_address(subject['producer_seal'])}
        compiled=[item for item in seal.get('compiled_implementations',[]) if item['material']==material]
        if len(compiled)>1:raise IntegrityError('Scientific source has ambiguous compiler provenance')
        if not compiled:return dict(subject=subject,compiler=None,compiled=None,code_version=None,control_paths=control_paths)
        item=compiled[0];reference=item['compiler'];compiler=read_control(reference)
        if source is not None:
            owner,_,_=entry_owner(reference['path'],reference)
            source=originals.at_step(owner,step);root=Path(source.original_root)
        control_paths.add(control_address(reference))
        version=None
        if 'code_version' in compiler:
            compiler_job=read_control(compiler['job']);compiler_config=read_control(compiler['configuration'])
            version=_compiler_source_version(compiler,reference,subject,project=compiler_job.get('project'))
            compiler_attempt=compiler_config['native_attempt']['number']
            if type(compiler_attempt) is not int or not 0<=compiler_attempt<10000:
                raise IntegrityError('Scientific compiler native attempt changed')
            compiler_native=root/'native-evidence'/f'{step:03d}'
            if compiler_attempt:compiler_native=compiler_native/'continuations'/f'{compiler_attempt:03d}'
            declarations=[row for row in compiler['implementation'].get('public_capabilities',[])
                if row['key']=='implementation_authoring']
            if (compiler_job.get('step')!=step or compiler_job.get('role')!='W'
                    or compiler['job']!=control_ref(root/'jobs'/f'{step:03d}.json')
                    or compiler['configuration']!=control_ref(root/'host-runtime/experiment-control'/f'{step:03d}'/'interfaces'/f'{compiler_attempt:03d}.json')
                    or compiler['native_intent']!=control_ref(compiler_native/'native-intent.json')
                    or read_control(compiler['native_intent'])['task_ref']!=q.control_reference(root,compiler['job'])
                    or compiler_config['scope'].get('step')!=step
                    or compiler_config['scope'].get('job_ref')!=q.control_reference(root,compiler['job'])
                    or compiler['implementation']!=compiler_config['execution_interface']
                    or len(declarations)!=1 or compiler['contract']!=declarations[0]
                    or any(compiler['capability'].get(key)!=compiler['capability_source'].get(key) for key in ('size_bytes',))
                    or any(request['payload'].get(key)!=item for key,item in compiler['output'].items())):
                raise IntegrityError('Scientific compiler lost its frozen control binding')
            read_control(compiler['capability'])
            control_paths.update(control_address(compiler[key])
                for key in ('job','configuration','native_intent','capability'))
            from . import originals
            seen=set();frozen=[*compiler_job.get('direct_materials',[]),
                *[row['material'] for row in compiler_job.get('selected_inputs',[])]]
            for slot in compiler['inputs']:
                name=slot['semantic_name'];original=slot['original']
                if name in seen:raise IntegrityError('Scientific compiler input slots are ambiguous')
                seen.add(name)
                matches={json.dumps(row,sort_keys=True):row for row in frozen
                    if row['path']==original.get('material_path')}
                if len(matches)!=1:raise IntegrityError('Scientific compiler input has no exact frozen material')
                input_material=next(iter(matches.values()));parts=Path(input_material['path']).parts
                input_step=original.get('producer_step')
                if (type(input_step) is not int or not 0<input_step<step
                        or type(original.get('size_bytes')) is not int
                        or len(parts)<5 or parts[:2]!=('inputs','parents')
                        or parts[2]!=f'{input_step:03d}' or parts[3]!='artifacts'):
                    raise IntegrityError('Scientific compiler input changed its exact producer')
                producer=(originals.node(root,input_step) if source is None else
                    originals.dependency(source,input_step,seal_ref=original['producer_seal']))
                relative=f'native-evidence/{input_step:03d}/result-identity.json'
                producer_seal=(control_ref(Path(producer.original_root)/relative) if source is not None else
                    originals.original_ref(producer,relative) if producer.inherited else
                    q._ref(root,c.host_record(c.workspace(root,input_step))/'result-identity.json'))
                expected=dict(instance_id=producer.origin_identity['goal_id'],producer_step=input_step,
                    producer_seal=producer_seal,material_path=input_material['path'],size_bytes=input_material['size_bytes'])
                seal=read_control(producer_seal)
                if (json.dumps(original,sort_keys=True)!=json.dumps(expected,sort_keys=True)
                        or [{key:value for key,value in row.items() if key!='file_stat'}
                            for row in seal.get('materials',[]) if row['path']==input_material['path']]!=[input_material]
                        or (not producer.inherited and seal.get('job_ref')!=c.record_ref(
                            Path(producer.original_root)/'jobs'/f'{input_step:03d}.json'))
                        or slot['alias']!=input_material['path'] or type(slot['size_bytes']) is not int
                        or slot['size_bytes']!=input_material['size_bytes'] or not isinstance(slot['media_type'],str)):
                    raise IntegrityError('Scientific compiler input alias or original metadata changed')
                control_paths.update((control_address(producer_seal),
                    str(Path(producer.original_root)/relative)))
            if type(compiler_job.get('worker_view_version')) is int and compiler_job['worker_view_version']==1:
                output=Path(compiler['output']['path']);client_ref=item.get('client_binding')
                if (type(compiler_config.get('worker_view_version')) is not int or compiler_config['worker_view_version']!=1
                        or compiler_config.get('record_dir')!=str(root/'host-runtime/experiment-control'/f'{step:03d}'/'records')
                        or output.parent.parent!=Path(compiler_config['record_dir']).parent/'implementations'
                        or re.fullmatch(r'I[0-9]{3,}',output.parent.name) is None or output.name!='package.json'
                        or client_ref!=control_ref(output.parent/'client.json')):
                    raise IntegrityError('Scientific compiler client lost its sealed control binding')
                client=read_control(client_ref)
                control_paths.add(control_address(client_ref))
                expected=dict(worker_implementation_version=1,implementation_ref=output.parent.name)
                if (set(client)!={'client','public_path','package'} or client['client']!=expected
                        or type(client['client'].get('worker_implementation_version')) is not int
                        or client['package']!=compiler['output']
                        or client['public_path']!=material.get('original_source',material['source'])
                        or not Path(client['public_path']).is_relative_to(c.workspace(root,step)/'artifacts')):
                    raise IntegrityError('Scientific compiler client differs from its sealed material')
        return dict(subject=subject,compiler=compiler,compiled=item,code_version=version,control_paths=control_paths)
    except (KeyError,TypeError,AttributeError) as error:
        raise IntegrityError('Scientific source control metadata is malformed') from error


def _validate_base_identity(project, value):
    """Bind frozen metadata without consuming the current base or draft bytes."""
    expected=None if project is None else project['base_identity']
    if (value.get('base_identity')!=expected
            or project is None and 'base_identity' in value):
        raise IntegrityError('Accepted project base identity differs from its original task')


def _code_version_binding(config, accepted, *, source=None):
    """Check permanent code metadata without opening code, drafts or exports."""
    from . import code_versions, contracts as c, experiment_client, project_layout
    if source is not None and not source.inherited:source=None
    try:version=code_versions._version(accepted['code_version'])
    except (KeyError,TypeError,ValueError) as error:
        raise IntegrityError('Accepted code version binding is malformed') from error
    if (config is None or accepted.get('scope')!=config.get('scope')
            or config.get('scope',{}).get('role')!='W'):
        raise IntegrityError('Accepted code version requires its exact W configuration')
    if source is None:
        root=experiment_client.root_for_configuration(config)
        job=c.read(c.regular(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
    else:
        from . import originals
        source=originals.at_step(source,config['scope']['step'])
        root=Path(source.original_root);job=originals.source_job(source)
        records=(root/'host-runtime/experiment-control'/f'{job["step"]:03d}'/'records'
            if job.get('worker_view_version')==experiment_client.VERSION else c.workspace(root,job['step'])/'work/lineage')
        if (config.get('record_dir')!=str(records) or job.get('role')!='W'
                or accepted['scope'].get('job_ref')!=dict(path=str(root/'jobs'/f'{job["step"]:03d}.json'))):
            raise IntegrityError('Accepted code version lost its original task configuration')
        attempt=(accepted.get('native_attempt') or {}).get('number',0)
        if type(attempt) is not int or not 0<=attempt<10000:
            raise IntegrityError('Accepted code version lost its original native attempt')
        native=f'native-evidence/{job["step"]:03d}' + (f'/continuations/{attempt:03d}' if attempt else '')
        frozen=c.read(originals.resolve_ref(source,dict(path=native+'/experiment-config.json')))
        if (frozen.get('scope')!=accepted['scope'] or frozen.get('native_attempt')!=accepted.get('native_attempt')
                or frozen.get('execution_interface')!=config.get('execution_interface')):
            raise IntegrityError('Accepted code version differs from its original native configuration')
    logical=Path(version['logical_root']);entry=Path(version['entry'])
    project=job.get('project')
    try:project_layout.validate_code(project,version,purpose='run')
    except (ValueError,KeyError,TypeError) as error:
        raise IntegrityError('Accepted code version differs from its original project') from error
    _validate_base_identity(project,accepted)
    if version['selection']=='directory':
        valid=(logical==Path('project' if project is not None else 'code')
            and config.get('execution_interface',{}).get('executor')=='local_execution:command')
    else:valid=logical.parts[0]=='code' and len(entry.parts)==1
    if (not valid or accepted.get('source_script')!=str(c.workspace(root,config['scope']['step'])/logical/entry)
            or any(key in accepted for key in ('entry','source_reference','source_input'))):
        raise IntegrityError('Accepted code version differs from its selected logical source')
    return root


def verify_scientific_inputs(accepted, *, config=None, context=None):
    """Consume retained code versions and saved data, never current W drafts."""
    items=[*accepted.get('snapshot_files',[])]
    if 'code_version' in accepted:
        from . import code_versions, contracts as c, project_layout
        root=_code_version_binding(config,accepted)
        try:
            paths=code_versions.verify(root,step=config['scope']['step'],call_id=accepted['call_id'],
                code_version=accepted['code_version'],context=context)
            job=c.read(c.regular(root/'jobs'/f'{config["scope"]["step"]:03d}.json'))
            project_layout.validate_code(job.get('project'),accepted['code_version'],purpose='run',paths=paths)
        except (OSError,ValueError) as error:
            raise IntegrityError('Accepted code version original changed or is unavailable') from error
    elif (accepted.get('script',{}).get('path')==accepted.get('prepared',{}).get('local_path')
            and 'file_stat' in accepted['script']):
        items=[accepted['script'],*items]
    else:
        # The original non-Git owner consumes its frozen call slots, retaining
        # HEAD's independent-copy identity rather than reopening mutable inputs.
        record=Path(accepted['script']['path']).parent
        items=[accepted['script'],*[dict(path=str(record/'inputs'/item['name']),
            size_bytes=item['size_bytes']) for item in items]]
    for item in items:
        current=proof(item['path'],context=context)
        if any(current[key]!=item[key] for key in ('size_bytes','file_stat') if key in item):
            raise IntegrityError('Accepted scientific input original changed')
    return accepted


def _observation_file_roles(value, accepted, completed, *, directory=None, source_controls=()):
    """Declared science membership never overrides a same-call control role."""
    observed=value if value is not None else {}
    interface=observed.get('interface',accepted.get('interface',{}))
    fields=('path','size_bytes','file_stat')
    science=[item['source'] for key in ('outputs','scientific_outputs') for item in observed.get(key,[])]
    if observed.get('scientific_source') is not None:science.append(observed['scientific_source']['source'])
    science.extend({key:item.get(key) for key in fields} for item in accepted.get('snapshot_files',[]))
    if isinstance(completed.get('model'),dict):science.append(completed['model'])
    science.extend({key:item.get(key) for key in fields} for item in completed.get('outputs',[]))
    body=interface.get('config_record',accepted.get('config_record',{}))
    record=Path(body.get('record_dir',''))
    if ('code_version' in accepted and record.is_absolute() and len(record.parents)>=4
            and record==record.parents[3]/'host-runtime/experiment-control'/f'{accepted["scope"]["step"]:03d}'/'records'):
        entry=_code_export_entry(record.parents[3],accepted)
        if entry is not None:science.append(entry)
    elif ('code_version' not in accepted and accepted.get('tcad') is None
            and interface.get('terminal_evidence')=='local_process'):
        science.append(accepted['script'])
        if isinstance(accepted.get('source_input'),dict):science.append(accepted['source_input'])
    directory=(Path(directory) if directory is not None else Path(observed['accepted']['path']).parent)
    controls=set(source_controls)|{observed[key]['path'] for key in ('accepted','completed','submission','native_terminal') if key in observed}
    controls.update(str(directory/name) for name in ('accepted.json','completed.json','submission.json'))
    if interface.get('terminal_evidence')=='local_process':
        controls.update(str(directory/name) for name in ('process.json','terminal.json'))
        if 'runs_root' in accepted:controls.update(str(directory/name) for name in ('receipt.json','row.json'))
        science.extend(item for item in completed.get('proofs',[])
            if item.get('path') in {str(directory/'stdout.raw'),str(directory/'stderr.raw')})
    authority=accepted.get('execution_authority')
    if isinstance(authority,dict):controls.add(authority['path'])
    if isinstance(accepted.get('tcad'),dict):
        controls.update(str(directory/name) for name in ('tcad-backend-binding.json','tcad-terminal-binding.json','collected.json'))
        controls.add(accepted['script']['path'])
        for key in ('prepared_ref','authority'):
            item=accepted['tcad'].get(key)
            if isinstance(item,dict) and 'path' in item:controls.add(item['path'])
        for item in observed.get('outputs',[]):
            if item.get('descriptor',{}).get('name')=='tcad_manifest':controls.add(item['source']['path'])
        for item in completed.get('outputs',[]):
            if item.get('name')=='tcad_manifest':controls.add(item.get('path',item.get('local_path')))
    return science,controls


def _cleanup_terminal_code_export(config, call_id, observation):
    """Retire only this call's export after saved terminal and replay cleanup."""
    if (not isinstance(observation,dict) or observation.get('status') not in {'known_success','known_failed'}
            or observation.get('interface',{}).get('terminal_evidence')!='local_process'):
        return
    from . import code_versions, replay_check, storage_policy as storage
    try:
        records=Records(config);directory=records.directory(call_id)
        accepted=records.accepted(call_id)
        if 'code_version' not in accepted:return
        root=_code_version_binding(config,accepted)
        if json.loads(read(directory/'execution-observation.json'))!=observation:return
        verify_observation(observation,lambda item:Path(item['path']),
            interface=config['execution_interface'],verify_files=False)
        if not replay_check.cleanup_confirmed(config,call_id,observation):return
    except Exception:
        return  # An unavailable cleanup proof leaves the exact export intact.
    try:
        code_versions.cleanup_export(root,step=config['scope']['step'],call_id=call_id,
            context=storage.Deadline(65))
    except Exception as error:
        export=root/'host-runtime/code-versions/exports'/f'{config["scope"]["step"]:03d}'/call_id/'tree'
        _diagnostic(RuntimeError(f'Code export cleanup failed for call {call_id} at {export}: '
            f'{type(error).__name__}: {error}'),'Original code export cleanup remains unconfirmed',config=config)


if __name__=='__main__':raise SystemExit(main())
