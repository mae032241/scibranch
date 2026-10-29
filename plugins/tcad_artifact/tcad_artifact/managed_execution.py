"""Opaque TCAD transport binding; trusted P3 authority is required for effects."""
from __future__ import annotations

import uuid
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from scidiscovery.artifact_agent.schema.execution import ExecutionAdmission
from scidiscovery.operations.execution import (
    AdapterCapability, ExecutionInterfaceRegistration, LifecycleCapabilities,
    LocalFileDescriptor, execution_status,
)
from scidiscovery.operations.runtime_plugins import (RuntimePluginContribution, RuntimePluginFactory,
    HOST_EXECUTION_GATE_KEY, HOST_EXECUTION_GATE_SCHEMA, HOST_EXECUTION_GATE_CONTRACT)
from scidiscovery.plugin_runtime.collection import CollectionContext
from scidiscovery.archive_io import SharingUnsupported
from scidiscovery.research_loop import experiments as e
from scidiscovery.research_loop import contracts as c

from .project_packager import (ExecutionPackage, NativeExecutionPackage, NativeResolvedProjectInput,
    TCADRuntimeManifest, validate_execution_package_json)
from .native_implementation import TCADCompilationConfig, TCADImplementation, compile_native_implementation
from .execution_control import SolverCapabilitySnapshot, TCADJobSpec
from .runtime_plugin import TCADRuntimeConfig, build_adapter


def _directory(path):
    path = Path(path).absolute()
    if path.resolve() != path or path.exists() and not path.is_dir():
        raise e.IntegrityError('Exact TCAD original directory required')
    path.mkdir(parents=True, exist_ok=True)
    return path


def _package_profile(raw):
    value = json.loads(raw)
    version = value.get('schema_version') if isinstance(value, dict) else None
    if type(version) is not int or version not in {2, 3}:
        raise ValueError('Explicit TCAD execution package version 2 or 3 required')
    return 'tcad.execution-package.v' + str(version)


def _proof(path, context=None):
    return e.proof(path,context=context)


def _verify_descriptor(descriptor, context=None):
    if Path(descriptor.local_path).stat().st_size != descriptor.size_bytes:
        raise e.IntegrityError('TCAD descriptor original size changed')
    original = _proof(descriptor.local_path, context)
    if original['size_bytes'] != descriptor.size_bytes:
        raise e.IntegrityError('TCAD descriptor original changed')
    return original


def _verify_proof(original, context=None):
    if (not isinstance(original, dict) or set(original) not in (
            {'path','size_bytes'}, {'path','size_bytes','file_stat'})
            or not isinstance(original.get('path'),str)
            or type(original.get('size_bytes')) is not int or original['size_bytes']<0):
        raise e.IntegrityError('TCAD original proof changed')
    current=_proof(original['path'], context)
    if any(current.get(key)!=value for key,value in original.items()):
        raise e.IntegrityError('TCAD original proof changed')


def _write_original(path, value):
    if Path(path).exists():
        if json.loads(e.read(path)) != value:
            raise e.IntegrityError('TCAD retained original changed')
        return
    try:
        e.write(path, value)
    except FileExistsError:
        if json.loads(e.read(path)) != value:
            raise e.IntegrityError('TCAD retained original changed')


class TCADManagedExecution:
    terminal_evidence_kind = 'adapter_result'
    result_protocol = None

    def __init__(self, config, adapter, *, gate=None, input_metadata=None, open_input=None, implementation_inputs=None, operation_context=None):
        self.config, self.adapter = config, adapter
        self.records = e.Records(config)
        self.gate = gate
        self.input_metadata, self.open_input = input_metadata, open_input
        self.implementation_inputs = implementation_inputs
        self.operation_context = operation_context
        self.compilation_capability = self.compilation_original = None
        self._lease = None
        transport = {key: config[key] for key in TCADRuntimeConfig.model_fields if key in config}
        self.transport = TCADRuntimeConfig.model_validate_json(json.dumps(transport), strict=True)
        assets = []
        if self.transport.command_config_path is not None:
            assets.append(self.transport.command_config_path)
            from .command_adapter import bundled_ssh_configuration
            nested = bundled_ssh_configuration(adapter.config)
            if nested is not None:
                assets.append(str(nested))
        if 'implementation_compilation' in config:
            compilation = TCADCompilationConfig.model_validate_json(json.dumps(config['implementation_compilation']), strict=True)
            self.compilation_original = _proof(compilation.capability_snapshot_path)
            self.compilation_capability = SolverCapabilitySnapshot.model_validate_json(
                Path(compilation.capability_snapshot_path).read_bytes(), strict=True)
            assets.append(compilation.capability_snapshot_path)
        self.target = dict(transport=self.transport.model_dump(mode='json'),
                           configuration_files=[e.proof(path) for path in assets])

    @staticmethod
    def lifecycle_capabilities():
        return LifecycleCapabilities(lookup_submission=True, query=True, cancel=True,
                                     collect=True, atomic_admission=True)

    def capabilities(self):
        from scidiscovery.research_loop import execution_inputs
        compilation = () if self.compilation_original is None else (AdapterCapability(
            'implementation_authoring', 'implementation', 'tcad.native-implementation.v1', 1,
            'application/schema+json', e.encoded(dict(implementation_schema=TCADImplementation.model_json_schema(),
                capability=self.compilation_capability.model_dump(mode='json'))),
            dict(action='Optional local compile; scientific slot names select exact bound input aliases; no execution permission')),)
        return compilation + (AdapterCapability(e.INPUT_SOURCES_KEY, 'source_provenance', execution_inputs.SOURCE_SCHEMA, 1,
            'application/json', e.encoded(execution_inputs.SOURCE_CONTRACT),
            dict(scope='Exact declared self-contained V2 package only; V3 uses compiler coverage; arbitrary solver reads are outside this proof')),
            AdapterCapability('execution_package', 'execution', 'tcad.execution-package.v2', 2,
            'application/schema+json', e.encoded(ExecutionPackage.model_json_schema(mode='validation')),
            dict(input='Exact TCAD execution package', result_protocol=None,
                 production='blocked_pending_trusted_gate' if self.gate is None else 'trusted_host_gate_required')),
            AdapterCapability('native_execution_package', 'execution', 'tcad.execution-package.v3', 3,
                'application/schema+json', e.encoded(NativeExecutionPackage.model_json_schema(mode='validation')),
                dict(input='Exact sealed host-compiled native package; formal review and trusted gate required')),
            AdapterCapability(e.RETIREMENT_KEY, 'execution_recovery', e.RETIREMENT_SCHEMA, 1,
                'application/json', e.encoded(dict(e.RETIREMENT_CONTRACT, backend_protocol={
                    'version': 2, 'submit': 'tcad_submit_retirable', 'retire': 'tcad_retire_submission'})),
                dict(action='Retire the exact original submission; no refund or new execution grant')),
            AdapterCapability('execution_resource_usage', 'resource_usage', 'scidiscovery.execution-resource-usage.v1', 1,
                'application/json', e.encoded(dict(version=1, dimensions=['wall_time_seconds', 'max_storage_bytes'],
                    basis='retained_native_terminal_manifest')),
                dict(action='Project known native resource facts; missing dimensions remain reserved')),
            AdapterCapability(HOST_EXECUTION_GATE_KEY, 'execution_authority', HOST_EXECUTION_GATE_SCHEMA, 1,
                'application/json', e.encoded(dict(HOST_EXECUTION_GATE_CONTRACT)),
                dict(action='Requires the trusted host factory gate; JSON cannot enable execution')))

    def worker_capabilities(self):
        """Scientific authoring contracts; complete package schemas stay host-side."""
        result = []
        for capability in self.capabilities():
            if capability.key in {'execution_package', 'native_execution_package'}:
                continue
            if capability.key == 'implementation_authoring':
                snapshot = self.compilation_capability.model_dump(mode='json')
                content = e.encoded(dict(implementation_schema=TCADImplementation.model_json_schema(),
                    capability={key: snapshot[key] for key in (
                        'schema_version', 'profile_id', 'solver_kind', 'launch_name',
                        'public_arguments', 'public_release_label')}))
                capability = AdapterCapability(capability.key, capability.kind, capability.schema_id,
                    capability.payload_schema_version, capability.media_type, content,
                    capability.public_summary)
            result.append(capability)
        return tuple(result)

    def compile_implementation(self, source, aliases, output):
        if self.compilation_original is None or self.implementation_inputs is None:
            raise e.UnsupportedExecution('Native compilation requires the explicit operator snapshot and trusted input host')
        _verify_proof(self.compilation_original)
        def author_document(raw):
            try:return TCADImplementation.model_validate_json(raw,strict=True)
            except ValidationError as error:
                rows=error.errors(include_input=False,include_context=False,include_url=False)
                details='; '.join(f'{".".join(str(item) for item in row["loc"]) or "document"}: {row["msg"]} [{row["type"]}]' for row in rows[:8])
                raise e.CallerError('TCAD implementation JSON is invalid: '+details[:1800]) from error
        authored = author_document(c.regular(Path(source).absolute()).read_bytes())
        contract = next(item for item in e._public_capabilities(self) if item['key'] == 'implementation_authoring')

        def build(raw, resolved):
            author = author_document(raw)
            inputs = tuple(NativeResolvedProjectInput.model_validate_json(e.encoded(row), strict=True) for row in resolved)
            return compile_native_implementation(author, capability=self.compilation_capability, resolved_inputs=inputs)

        return self.implementation_inputs.compile(source, output,
            slots=[slot.model_dump(mode='json') for slot in authored.input_slots], aliases=aliases,
            capability=self.compilation_original, contract=contract, builder=build)

    def _check(self, phase, call, payload, *, prepared=None, prior=None):
        if self.gate is None:
            raise e.UnsupportedExecution('TCAD production blocked: trusted P3 execution gate is unavailable')
        permit = self.gate(phase, scope=self.config['scope'], call_id=call, payload=payload,
                           target=self.target, prepared=prepared, prior_receipt=prior)
        if not isinstance(permit, dict) or 'receipt' not in permit:
            raise e.IntegrityError('Trusted TCAD gate returned no authority original')
        _verify_proof(permit['receipt'])
        if prior is not None and permit['receipt'] != prior:
            raise e.IntegrityError('TCAD gate changed the original authority binding')
        if phase in {'prepare', 'submit'} and not isinstance(permit.get('authorization'), ExecutionAdmission):
            raise e.IntegrityError('Trusted TCAD gate returned no execution admission')
        if phase in {'prepare', 'submit'} and permit['authorization'].outcome == 'deny':
            raise e.UnsupportedExecution('TCAD execution authority denied the original request')
        if phase == 'collect' and not isinstance(permit.get('context'), CollectionContext):
            raise e.UnsupportedExecution('Original TCAD collection remaining authority is unavailable')
        return permit

    def _stage(self, call):
        if not isinstance(call, str) or not e.ID.fullmatch(call):
            raise ValueError('Exact TCAD call identity required')
        return self.records.root / 'tcad-prepared' / call

    @e.record_effect
    def prepare(self, payload, *, preparation_profile, exchange_directory):
        self._original_interface()
        if preparation_profile != 'research_loop.v1' or Path(exchange_directory) != self.records.root:
            raise ValueError('Exact managed TCAD preparation context required')
        payload_proof=_verify_descriptor(payload,self.operation_context)
        binding_path=self._stage(payload.name)/'binding.json'
        if binding_path.exists():
            binding=json.loads(e.read(binding_path))
            if binding.get('payload')!=payload.model_dump(mode='json') or binding.get('target')!=self.target:
                raise e.IntegrityError('Original TCAD preparation binding changed')
            prepared=LocalFileDescriptor.model_validate({key:value for key,value in binding['prepared'].items()
                if key!='file_stat'})
            self._preparation(prepared)
            return prepared
        if self.config.get('transport')=='socket':
            raise e.UnsupportedExecution('TCAD socket cannot first-write new scientific outputs into the exact Node call directory; preparation and submission are unsupported')
        raw = Path(payload.local_path).read_bytes(); profile = _package_profile(raw)
        reviewed = validate_execution_package_json(raw, preparation_profile=profile)
        metadata, opener = self.input_metadata, self.open_input
        if isinstance(reviewed, NativeExecutionPackage):
            if self.implementation_inputs is None:
                raise e.UnsupportedExecution('Exact native compiler/input originals are unavailable')
            metadata, opener = self.implementation_inputs.for_payload(payload, reviewed.resolved_inputs)
        if reviewed.resolved_inputs and (metadata is None or opener is None):
            raise e.UnsupportedExecution('Exact frozen TCAD input originals are unavailable; no authority request or reservation created')
        # No capability/policy RPC or transport prepare is permitted before this gate.
        permit = self._check('prepare', payload.name, payload)
        stage = _directory(self._stage(payload.name))
        original = payload
        prepared = self.adapter.prepare(original, preparation_profile=profile,
            exchange_directory=stage, input_metadata=metadata, open_input=opener)
        prepared = prepared.model_copy(update={'name': payload.name})
        prepared_proof=_verify_descriptor(prepared,self.operation_context)
        if _verify_descriptor(payload,self.operation_context)!=payload_proof:
            raise e.IntegrityError('TCAD package original changed during preparation')
        binding = dict(payload=payload.model_dump(mode='json'),
            retained_payload=dict(original.model_dump(mode='json'),file_stat=payload_proof['file_stat']),
            prepared=dict(prepared.model_dump(mode='json'),file_stat=prepared_proof['file_stat']),
            target=self.target, authority=permit['receipt'],
            authorization=permit['authorization'].model_dump(mode='json'))
        if permit.get('reservation') is not None:
            binding['reservation'] = permit['reservation']
        e.write(stage / 'binding.json', binding)
        os.chmod(stage / 'binding.json', 0o440)
        return prepared

    def _preparation(self, descriptor, *, verify_inputs=True):
        value = json.loads(e.read(self._stage(descriptor.name) / 'binding.json'))
        saved_prepared=value.get('prepared')
        if (not isinstance(saved_prepared,dict)
                or {key:item for key,item in saved_prepared.items() if key!='file_stat'} != descriptor.model_dump(mode='json')
                or value.get('target') != self.target):
            raise e.IntegrityError('Original TCAD preparation target or descriptor changed')
        _verify_proof(dict(path=descriptor.local_path,size_bytes=descriptor.size_bytes,
            **({'file_stat':saved_prepared['file_stat']} if 'file_stat' in saved_prepared else {})),self.operation_context)
        payload = LocalFileDescriptor.model_validate(value['payload'])
        saved_payload=value['retained_payload']
        retained = LocalFileDescriptor.model_validate({key:item for key,item in saved_payload.items() if key!='file_stat'})
        if retained.model_copy(update={'local_path': payload.local_path}) != payload:
            raise e.IntegrityError('TCAD retained package differs from its original descriptor')
        if 'file_stat' in saved_payload and retained!=payload:
            raise e.IntegrityError('TCAD package original path changed')
        if verify_inputs:
            _verify_proof(dict(path=retained.local_path,size_bytes=retained.size_bytes,
                **({'file_stat':saved_payload['file_stat']} if 'file_stat' in saved_payload else {})),self.operation_context)
        _verify_proof(value['authority'])
        return value

    def acceptance_metadata(self, payload, *, parent=None, new=False, change=None):
        self._original_interface()
        if parent is not None or new or change is not None:
            raise e.UnsupportedExecution('TCAD opaque execution declares no candidate parent semantics')
        value = self._preparation(payload)
        return dict(parent=dict(candidate_id=None), change_intent=None, tcad=value,
            execution_authority=value['authority'], execution_target=value['target'],
            **(dict(execution_reservation=value['reservation']) if value.get('reservation') is not None else {}),
            submission_intent_path=str(self.records.root / 'calls' / payload.name / 'tcad-dispatch.json'))

    def input_sources(self, payload, metadata):
        """Actual package/physical inventory, never merely an empty slot label."""
        import tarfile
        from scidiscovery.research_loop import execution_inputs, execution_authority as authority, qualification as q
        from .execution_control import TCADJobSpec
        value = self._preparation(payload)
        raw = Path(value['retained_payload']['local_path']).read_bytes()
        package = validate_execution_package_json(raw, preparation_profile=_package_profile(raw))
        if not isinstance(package, ExecutionPackage) or package.resolved_inputs or package.project.input_slots:
            return None  # Native V3 retains its compiler; nonempty V2 has no native mapping.
        spec = TCADJobSpec.model_validate_json(Path(payload.local_path).read_bytes(), strict=True)
        expected = {item.relative_path:item.content.encode('utf-8') for item in package.project.files}
        inventory = {item.relative_path:item.size_bytes for item in spec.archive_entries}
        if (inventory != {name:len(raw) for name,raw in expected.items()}
                or spec.arguments != (package.project.entrypoint, *package.project.arguments)
                or spec.tool_profile != package.project.tool_profile or spec.solver_kind != package.project.solver_kind
                or spec.capability_sha256 != package.capability.capability_sha256):
            raise e.IntegrityError('TCAD declared sources differ from actual prepared package')
        _verify_descriptor(spec.input_archive,self.operation_context)
        seen = set()
        with tarfile.open(spec.input_archive.local_path, mode='r|') as archive:
            for member in archive:
                if not member.isfile() or member.name not in expected or member.name in seen:
                    raise e.IntegrityError('TCAD prepared archive contains undeclared scientific inputs')
                stream = archive.extractfile(member)
                size = 0
                for block in iter(lambda:stream.read(1024*1024), b''):
                    if self.operation_context is not None:
                        self.operation_context.remaining_seconds()
                    size += len(block)
                if size != inventory[member.name]:
                    raise e.IntegrityError('TCAD embedded source differs from prepared archive')
                seen.add(member.name)
        if seen != set(expected):
            raise e.IntegrityError('TCAD prepared archive omitted declared scientific inputs')
        archive_proof = _verify_descriptor(spec.input_archive,self.operation_context)
        package_proof = _verify_descriptor(LocalFileDescriptor.model_validate({key:item for key,item
            in value['retained_payload'].items() if key!='file_stat'}),self.operation_context)
        root, job, intent = authority._host_context(self.config)
        authorization = q._read(root, {**value['authority'], 'path':str(Path(value['authority']['path']).relative_to(root))})
        subject = q._read(root, authorization['request'])['subject']
        original = execution_inputs._producer(root, subject['material'])
        if original['producer_step'] != subject['producer_step'] or original['producer_seal'] != subject['producer_seal']:
            raise e.IntegrityError('TCAD input does not name its original reviewed producer')
        attempt = self.config['native_attempt']['number']
        projection = root / 'host-runtime/experiment-control' / f'{self.config["scope"]["step"]:03d}' / 'interfaces' / f'{attempt:03d}.json'
        return dict(job=q._ref(root,root/'jobs'/f'{job["step"]:03d}.json'),
            configuration=q._ref(root,projection), native_intent=intent, prepared=payload.model_dump(mode='json'),
            evidence=[dict(item,path=Path(item['path']).relative_to(root).as_posix()) for item in (package_proof,archive_proof)],
            sources=dict(version=1,complete=True,sources=[dict(kind='producer',original=original)]))

    def _bound(self, call, *, verify_inputs=True):
        accepted = self.records.accepted(call)
        descriptor = LocalFileDescriptor.model_validate(accepted['prepared'])
        if (accepted.get('native_attempt') != self.config.get('native_attempt')
                or accepted.get('interface') != self._original_interface()
                or descriptor.name != call
                or accepted['script'].get('path') not in {descriptor.local_path,str(self.records.directory(call) / 'source.py')}
                or accepted['script'].get('size_bytes') != descriptor.size_bytes
                or accepted.get('execution_authority') != accepted.get('tcad', {}).get('authority')
                or accepted.get('execution_reservation') != accepted.get('tcad', {}).get('reservation')
                or accepted.get('execution_target') != self.target
                or accepted.get('tcad') != self._preparation(descriptor,verify_inputs=verify_inputs)):
            raise e.IntegrityError('TCAD call differs from its original acceptance')
        _verify_proof(accepted['script'],self.operation_context)
        return accepted, descriptor

    def _original_interface(self):
        selected = self.config.get('execution_interface')
        required = {'version', 'executor', 'observation_version', 'terminal_evidence',
            'result_protocol', 'config_record', 'capabilities'}
        if (not isinstance(selected, dict) or type(selected.get('version')) is not int
                or selected['version'] != e.VERSION or not required.issubset(selected)
                or selected.get('executor') != 'tcad_execution:tcad'
                or type(selected.get('observation_version')) is not int
                or selected['observation_version'] != e.OBSERVATION_VERSION
                or selected.get('terminal_evidence') != self.terminal_evidence_kind
                or selected.get('result_protocol') != self.result_protocol
                or not isinstance(selected.get('config_record'), dict)
                or not isinstance(selected.get('capabilities'), dict)
                or any(type(value) is not bool for value in selected['capabilities'].values())
                or selected['capabilities'] != vars(self.lifecycle_capabilities())):
            raise e.IntegrityError('TCAD acceptance requires the complete original host interface; discovery is insufficient')
        # Core load verifies the frozen configuration and lifecycle; source proofs
        # are installation metadata, not evidence of a complete host interface.
        return selected

    def dispatch_lease(self, accepted):
        original, descriptor = self._bound(accepted['call_id'])
        if original != accepted:
            raise e.IntegrityError('TCAD dispatch acceptance changed')
        directory = self.records.directory(descriptor.name)
        if not (directory / 'tcad-dispatch.json').exists() and not (directory / 'retirement-native').exists():
            self._lease = (descriptor.name, descriptor.model_dump(mode='json'))

    def _binding(self, call, descriptor, external):
        if not isinstance(external, str) or not e.ID.fullmatch(external):
            raise e.IntegrityError('TCAD backend returned no exact execution identity')
        known = self.records.external(call)
        if known is not None and external != known:
            raise e.IntegrityError('TCAD backend changed the original external identity')
        value = dict(call_id=call, scope=self.config['scope'], prepared_ref=dict(path=descriptor.local_path,size_bytes=descriptor.size_bytes),
                     external_run_id=external, target=self.target)
        path = self.records.directory(call) / 'tcad-backend-binding.json'
        with self.records.lease('tcad-binding-' + call + '.lock'):
            if path.exists():
                if json.loads(e.read(path)) != value:
                    raise e.IntegrityError('TCAD backend acceptance binding changed')
            else:
                e.write(path, value)

    @e.record_effect
    def lookup_submission(self, submission):
        _, descriptor = self._bound(submission.name)
        if descriptor != submission:
            raise e.IntegrityError('TCAD lookup differs from the original prepared descriptor')
        found = self.adapter.lookup_submission(descriptor)
        if found is not None:
            self._binding(descriptor.name, descriptor, found[0])
        return found

    @e.record_effect
    def submit(self, submission):
        with self.records.lease('tcad-submission-' + submission.name + '.lock'):
            if e.retirement(self.config, submission.name) is not None:
                raise e.UnsupportedExecution('Original TCAD submission was permanently retired')
            return self._submit(submission)

    def _submit(self, submission):
        accepted, descriptor = self._bound(submission.name)
        if submission != descriptor:
            raise e.IntegrityError('TCAD submission differs from accepted preparation')
        found = self.lookup_submission(descriptor)
        if found is not None:
            return found
        directory = self.records.directory(descriptor.name)
        if ((directory / 'tcad-dispatch.json').exists()
                or (directory / 'retirement-native').exists()
                or self._lease != (descriptor.name, descriptor.model_dump(mode='json'))):
            raise e.UnsupportedExecution('TCAD original submission is unknown; lookup without resubmission')
        if self.config.get('transport')=='socket':
            raise e.UnsupportedExecution('TCAD socket cannot first-write new scientific outputs into the exact Node call directory; no submission was made')
        value = accepted['tcad']
        permit = self._check('submit', descriptor.name, LocalFileDescriptor.model_validate(value['payload']),
                             prepared=descriptor, prior=value['authority'])
        if permit['authorization'].model_dump(mode='json') != value['authorization']:
            raise e.IntegrityError('TCAD execution admission changed after preparation')
        # The exclusive original precedes the uncertain external side effect.
        e.write(directory / 'tcad-dispatch.json', dict(call_id=descriptor.name, scope=self.config['scope'],
            prepared=descriptor.model_dump(mode='json'), authority=permit['receipt']))
        self._lease = None
        try:
            external, state = self.adapter.submit(descriptor, authorization=permit['authorization'])
        except (OSError, RuntimeError) as error:
            raise e.UnsupportedExecution('TCAD submission response is unknown; preserve the original call') from error
        self._binding(descriptor.name, descriptor, external)
        return external, state

    def _retirement_packet(self, call, kind, value):
        raw = e.encoded(value)
        directory = _directory(self.records.directory(call) / 'retirement-native')
        path = directory / (kind + '-' + uuid.uuid4().hex + '.json')
        _write_original(path, value)
        return e.proof(path)

    @contextmanager
    def retire_original(self, call):
        # Core publishes its common receipt inside this context, under this lease.
        with e.instance_owner.lease_for_configuration(self.config), c.record_writer(self.records.root), self.records.lease('tcad-submission-' + call + '.lock'):
            existing = e.retirement(self.config, call)
            if existing is not None:
                yield dict(existing, status='not_submitted')
                return
            accepted, prepared = self._bound(call)
            value = accepted['tcad']
            directory = self.records.directory(call)
            request = dict(call_id=call, scope=accepted['scope'], accepted=e.proof(directory / 'accepted.json'),
                prepared=prepared.model_dump(mode='json'), authority=value['authority'], target=self.target)
            try:
                self._check('retire', call, LocalFileDescriptor.model_validate(value['payload']),
                    prepared=prepared, prior=value['authority'])
                self._lease = None
                request_proof = self._retirement_packet(call, 'request', request)
                protocol = self.adapter.validate_submission_protocol()
                protocol_proof = self._retirement_packet(call, 'protocol', protocol)
                response = self.adapter.retire_submission_details(prepared)
            except (e.UnsupportedExecution, OSError, RuntimeError):
                yield dict(call_id=call, status='unknown', note='Original retirement is unconfirmed; preserve its reservation')
                return
            response_proof = self._retirement_packet(call, 'response', response)
            if response.get('found') is True:
                external = response['run_id']
                self._binding(call, prepared, external)
                self.records.bind_external(call, external, response['state'])
                yield dict(call_id=call, status=response['state'], external_run_id=external,
                    note='Original job found; use its native terminal evidence, not a non-submission proof')
                return
            if (response.get('found') is not False or response.get('retired') is not True
                    or response.get('submission_id') != _prepared_submission_id(prepared.model_dump(mode='json'))):
                raise e.IntegrityError('TCAD retirement did not permanently fence the original submission')
            if self.records.external(call) is not None or any((directory / name).exists()
                    for name in ('completed.json', 'terminal.json', 'execution-observation.json')):
                raise e.IntegrityError('TCAD non-submission contradicts an original job or terminal fact')
            intent = directory / 'tcad-dispatch.json'
            native_originals=[request_proof,response_proof,protocol_proof]
            assertion=dict(version=1,kind='permanent_not_submitted',call_id=call,scope=accepted['scope'],
                accepted=e.proof(directory/'accepted.json'),prepared=prepared.model_dump(mode='json'),
                authority=value['authority'],native_originals=native_originals)
            yield dict(call_id=call,status='not_submitted',
                assertion=self._retirement_packet(call,'assertion',assertion),native_originals=native_originals,
                dispatch_intent=e.proof(intent) if intent.exists() else None)

    def _query(self, call):
        _, descriptor = self._bound(call,verify_inputs=False)
        external = self.records.external(call)
        if external is None:
            return dict(state='unknown', call_id=call)
        directory = self.records.directory(call)
        if (directory / 'tcad-terminal-binding.json').exists():
            receipt = self._retained_terminal(call,self.operation_context,verify_outputs=False)
            if (directory / 'collected.json').exists():
                self._collection(call,receipt['status']['state'],self.operation_context,verify_outputs=False)
            return receipt['status']
        backend=json.loads(e.read(directory/'tcad-backend-binding.json'))
        if backend != dict(call_id=call,scope=self.config['scope'],
                prepared_ref=dict(path=descriptor.local_path,size_bytes=descriptor.size_bytes),
                external_run_id=external,target=self.target):
            raise e.IntegrityError('TCAD retained backend binding changed')
        details = self.adapter.status_details(external)
        if 'run_id' in details and details['run_id'] != external:
            raise e.IntegrityError('TCAD status changed the original external identity')
        return dict(details, call_id=call, external_run_id=external)

    @e.record_effect
    def status_details(self, call):
        if e.retirement(self.config, call) is not None:
            return dict(state='unknown', call_id=call, submission_disposition='not_submitted')
        path = self.records.directory(call) / 'completed.json'
        if path.exists():
            value = self._completed(call,verify_outputs=False)
            return dict(state=value['native_state'], call_id=call,
                        external_run_id=self.records.external(call))
        return self._query(call)

    def _external_call(self, external):
        matches = [path.name for path in self.records.calls() if self.records.external(path.name) == external]
        if len(matches) != 1:
            raise e.IntegrityError('Exact original TCAD external binding required')
        return matches[0]

    def status(self, external):
        return self.status_details(self._external_call(external))['state']

    def _completed(self, call, *, verify_outputs=True):
        accepted, _ = self._bound(call,verify_inputs=False)
        value = json.loads(e.read(self.records.directory(call) / 'completed.json'))
        if (any(value.get(key) != accepted.get(key) for key in ('call_id', 'scope', 'mechanism_binding'))
                or value.get('native_state') not in {'succeeded', 'failed', 'cancelled'}
                or value.get('status') != execution_status(value['native_state'])):
            raise e.IntegrityError('TCAD completion differs from the original call')
        directory=self.records.directory(call)
        controls={directory/name for name in ('tcad-backend-binding.json','tcad-terminal-binding.json','collected.json')}
        if not controls.issubset({Path(original['path']) for original in value['proofs']}):
            raise e.IntegrityError('TCAD completion omitted original control proofs')
        for original in value['proofs']:
            if verify_outputs or Path(original['path']) in controls:
                _verify_proof(original,self.operation_context)
        collected=self._collection(call, value['native_state'],self.operation_context,verify_outputs=verify_outputs)
        outputs={item['local_path']:item for item in collected['outputs']}
        if any(Path(original['path']) not in controls and original['path'] not in outputs for original in value['proofs']):
            raise e.IntegrityError('TCAD completion proof escaped its original collection')
        for item in collected['outputs']:
            expected=dict(path=item['local_path'],size_bytes=item['size_bytes'],
                **({'file_stat':item['file_stat']} if 'file_stat' in item else {}))
            if expected not in value['proofs']:
                raise e.IntegrityError('TCAD completion omitted an original collected output')
        return value

    def _retained_terminal(self, call, context=None, *, verify_outputs=True):
        accepted, prepared = self._bound(call,verify_inputs=False)
        directory = self.records.directory(call)
        receipt = json.loads(e.read(directory / 'tcad-terminal-binding.json'))
        backend = json.loads(e.read(directory / 'tcad-backend-binding.json'))
        native = receipt.get('status', {}).get('state')
        if (receipt.get('call_id') != call or receipt.get('scope') != accepted['scope']
                or receipt.get('prepared_ref') != dict(path=prepared.local_path,size_bytes=prepared.size_bytes)
                or receipt.get('external_run_id') != self.records.external(call)
                or execution_status(native) not in {'known_success', 'known_failed'}):
            raise e.IntegrityError('TCAD retained terminal binding changed')
        if backend != dict(call_id=call, scope=accepted['scope'], prepared_ref=dict(path=prepared.local_path,size_bytes=prepared.size_bytes),
                           external_run_id=self.records.external(call), target=self.target):
            raise e.IntegrityError('TCAD retained backend binding changed')
        outputs = tuple(LocalFileDescriptor.model_validate({key:value for key,value in item.items() if key!='file_stat'})
            for item in receipt['outputs'])
        for saved,item in zip(receipt['outputs'],outputs):
            if context is not None:
                context.remaining_seconds()
            if 'file_stat' not in saved and Path(item.local_path) != directory / 'tcad-originals' / item.name:
                raise e.IntegrityError('TCAD retained output escaped its original call')
            if verify_outputs or item.name=='tcad_manifest':
                _verify_proof(dict(path=item.local_path,size_bytes=item.size_bytes,
                    **({'file_stat':saved['file_stat']} if 'file_stat' in saved else {})),context)
        self._inventory(outputs, native, context=context,verify_files=verify_outputs)
        return receipt

    def _collection(self, call, native, context=None, *, verify_outputs=True):
        accepted, _ = self._bound(call,verify_inputs=False)
        directory = self.records.directory(call)
        collected = json.loads(e.read(directory / 'collected.json'))
        receipt = self._retained_terminal(call,context,verify_outputs=verify_outputs)
        if (collected.get('call_id') != call or collected.get('scope') != accepted['scope']
                or collected.get('mechanism_binding') != accepted.get('mechanism_binding')
                or collected.get('native_state') != native
                or receipt.get('outputs') != collected.get('outputs')
                or receipt.get('status', {}).get('state') != native):
            raise e.IntegrityError('TCAD collection differs from the original terminal state')
        return collected

    @staticmethod
    def _inventory(outputs, native, *, context=None, verify_bytes=True, verify_files=True):
        names = {}
        for item in outputs:
            if context is not None:
                context.remaining_seconds()
            name = Path(item.name)
            if (item.name in names or name.is_absolute() or '..' in name.parts or not name.parts
                    or name.as_posix() != item.name or '\\' in item.name):
                raise e.IntegrityError('TCAD output names are ambiguous or noncanonical')
            if not verify_files:
                if not Path(item.local_path).is_absolute():
                    raise e.IntegrityError('Exact TCAD output path required')
            elif verify_bytes:
                _verify_descriptor(item, context)
            elif Path(item.local_path).resolve() != Path(item.local_path) or not Path(item.local_path).is_file():
                raise e.IntegrityError('Exact TCAD output regular file required')
            elif Path(item.local_path).stat().st_size != item.size_bytes:
                raise e.IntegrityError('TCAD output size differs from its declared identity')
            names[item.name] = item
        if 'tcad_manifest' not in names or 'tcad_log' not in names:
            raise e.IntegrityError('TCAD collection lacks native manifest or log')
        chunks = []
        with Path(names['tcad_manifest'].local_path).open('rb') as source:
            while True:
                if context is not None:
                    context.remaining_seconds()
                block = source.read(1024 * 1024)
                if not block:
                    break
                chunks.append(block)
        manifest = TCADRuntimeManifest.model_validate_json(b''.join(chunks), strict=True)
        if context is not None:
            context.remaining_seconds()
        if manifest.terminal_state != native:
            raise e.IntegrityError('TCAD manifest differs from observed native terminal state')
        if set(names) != {item.name for item in manifest.outputs} | {'tcad_manifest', 'tcad_log'}:
            raise e.IntegrityError('TCAD collection differs from the full native output inventory')
        for item in manifest.outputs:
            if context is not None:
                context.remaining_seconds()
            descriptor = names[item.name]
            if (descriptor.size_bytes, descriptor.media_type) != (item.size_bytes, item.media_type):
                raise e.IntegrityError('TCAD native output differs from its manifest')
        return manifest

    def _retain(self, call, outputs, details, context):
        from scidiscovery.research_loop import storage_policy as storage
        inventory=storage.Inventory(self.config.get('storage_policy'),context=context)
        for item in outputs:
            inventory.entry();inventory.file(item.size_bytes)
        manifest = self._inventory(outputs, details['state'], context=context, verify_bytes=False)
        directory = self.records.directory(call)
        from scidiscovery.research_loop.local_experiments import artifact_directory
        node_outputs=artifact_directory(self.config,call)
        originals = []
        for item in outputs:
            original=_verify_descriptor(item,context)
            source=Path(item.local_path)
            # Only this call's own stopped output namespace is ours to seal.
            # An adapter's external output descriptor is verified in place.
            if (source.is_relative_to(directory) or source.is_relative_to(self._stage(call))
                    or source.is_relative_to(node_outputs)):
                info=source.stat()
                if info.st_mode&0o777!=0o400 and info.st_nlink!=1:
                    raise e.IntegrityError('Mutable TCAD output must not have hard-link aliases')
                owned=True
            else:
                owned=False
            originals.append((item,original,owned))
        retained = []
        for item,original,owned in originals:
            source=Path(item.local_path)
            if owned:
                if source.stat().st_mode&0o777!=0o400:source.chmod(0o400)
                if _proof(source,context)!=original:
                    raise e.IntegrityError('Stopped TCAD output changed during sealing')
            retained.append(dict(item.model_dump(mode='json'),file_stat=original['file_stat']))
        receipt = directory / 'tcad-terminal-binding.json'
        context.remaining_seconds()
        _write_original(receipt, dict(call_id=call, scope=self.config['scope'],
            external_run_id=self.records.external(call), prepared_ref=dict(path=self.records.accepted(call)['prepared']['local_path'],size_bytes=self.records.accepted(call)['prepared']['size_bytes']),
            status=details, outputs=retained))
        self.records.publish(call, 'collected.json', dict(native_state=manifest.terminal_state,
            outputs=retained))

    @e.record_effect
    def result(self, call):
        directory = self.records.directory(call)
        if e.retirement(self.config, call) is not None:
            return dict(call_id=call, status='not_submitted', note='Permanent original non-submission; no scientific result')
        if (directory / 'completed.json').exists():
            return self._completed(call)
        unknown = dict(call_id=call, status='unknown', note='Original TCAD terminal evidence is not verified')
        accepted, prepared = self._bound(call,verify_inputs=False)
        try:
            details = self._query(call)
        except (OSError, RuntimeError):
            return unknown
        if execution_status(details.get('state')) not in {'known_success', 'known_failed'}:
            return unknown
        unknown['native_state'] = details['state']
        with self.records.lease('tcad-collect-' + call + '.lock'):
            if (directory / 'completed.json').exists():
                return self._completed(call)
            value = accepted['tcad']
            try:
                permit = self._check('collect', call, LocalFileDescriptor.model_validate(value['payload']),
                    prepared=prepared, prior=value['authority'])
                context = permit['context']
                context.remaining_seconds()
                if not (directory / 'collected.json').exists():
                    if (directory / 'tcad-terminal-binding.json').exists():
                        # The immutable receipt is published before collected.
                        # Resume that exact evidence without redownloading a
                        # manifest or replacing its inode/status observation.
                        receipt = self._retained_terminal(call,context)
                        details = receipt['status']
                        context.remaining_seconds()
                        self.records.publish(call, 'collected.json', dict(
                            native_state=details['state'], outputs=receipt['outputs']))
                    else:
                        options={}
                        if self.config.get('transport')=='command':
                            from scidiscovery.research_loop.local_experiments import artifact_directory
                            options['output_directory']=artifact_directory(self.config,call)
                        outputs = self.adapter.collect_with_budget(self.records.external(call), context=context,**options)
                        context.remaining_seconds()
                        self._retain(call, outputs, details, context)
                collected = self._collection(call, details['state'], context)
                context.remaining_seconds()
            except SharingUnsupported:
                raise
            except (e.UnsupportedExecution, OSError, RuntimeError):
                return unknown
            native = collected['native_state']
            try:
                proofs = [_proof(directory / name, context) for name in (
                    'tcad-backend-binding.json', 'tcad-terminal-binding.json', 'collected.json')]
                proofs += [dict(path=item['local_path'], size_bytes=item['size_bytes'],
                    **({'file_stat':item['file_stat']} if 'file_stat' in item else {}))
                           for item in collected['outputs']]
                context.remaining_seconds()
            except OSError:
                return unknown
            self.records.publish(call, 'completed.json', dict(status=execution_status(native), native_state=native,
                proofs=proofs, note='Retained native TCAD execution facts; no scientific qualification'))
            return json.loads(e.read(directory / 'completed.json'))

    @e.record_effect
    def collect_original(self, call):
        if self.result(call)['status'] not in {'known_success','known_failed'}:
            raise e.UnsupportedExecution('Original TCAD terminal collection is not verified')
        return tuple(LocalFileDescriptor.model_validate({key:value for key,value in item.items() if key!='file_stat'})
                     for item in json.loads(e.read(self.records.directory(call) / 'collected.json'))['outputs'])

    @staticmethod
    def scientific_outputs(descriptors, *, projection_directory, output_directory=None):
        """Project the verified producer manifest; solver data remain exact originals."""
        manifests = [item for item in descriptors if item.name == 'tcad_manifest']
        if len(manifests) != 1:
            raise e.IntegrityError('Scientific TCAD outputs require one native manifest')
        manifest = TCADRuntimeManifest.model_validate_json(
            Path(manifests[0].local_path).read_bytes(), strict=True)
        TCADManagedExecution._inventory(descriptors, manifest.terminal_state)
        records = {item.name: item for item in manifest.outputs}
        result = []
        scientific_paths, path_bindings, path_references = [], [], {}
        withheld = 0
        for descriptor in descriptors:
            if descriptor.name == 'tcad_manifest':
                continue
            record = records.get(descriptor.name)
            if record is not None and record.output_class == 'transport_derived':
                withheld += 1
                continue
            if re.fullmatch(r'generated_[0-9a-f]{64}', descriptor.name):
                descriptor = descriptor.model_copy(update={
                    'name': f'tcad_output_{len(result) + 1:03d}' + Path(record.relative_path).suffix})
            if record is not None:
                relative = Path(record.relative_path)
                if (relative.is_absolute() or relative.as_posix() != record.relative_path
                        or any(part in {'', '.', '..'} for part in record.relative_path.split('/'))
                        or '\\' in record.relative_path or '\x00' in record.relative_path):
                    raise e.IntegrityError('Scientific TCAD output requires a canonical relative scientific path')

                def readable_identity(match):
                    identity = match.group(0)
                    if identity not in path_references:
                        path_references[identity] = f'reference-{len(path_references) + 1:03d}'
                    return path_references[identity]

                display = '/'.join(re.sub(r'[0-9a-fA-F]{64}', readable_identity, part) for part in relative.parts)
                row = dict(name=descriptor.name, scientific_path=display,
                    path_view='exact_relative_path' if display == record.relative_path else 'display_projection')
                scientific_paths.append(row)
                path_bindings.append(dict(row, original_name=record.name, original_path=record.relative_path))
            result.append(descriptor)

        def error_code(message):
            exact = {'wall_time_exceeded', 'total_storage_exceeded', 'cancelled_before_launch', 'cancelled_by_request'}
            if message in exact:
                return message
            for prefix, code in (
                    ('required output missing:', 'required_output_missing'),
                    ('output exceeds declared bound', 'output_bound_exceeded'),
                    ('total output exceeds job limit', 'total_output_bound_exceeded'),
                    ('solver produced no generated output files', 'generated_output_missing'),
                    ('generated output', 'generated_output_collection_error')):
                if message.startswith(prefix):
                    return code
            return 'native_execution_error' if message else None

        facts = dict(view='tcad.scientific-execution-facts.v2', terminal_state=manifest.terminal_state,
            exit_code=manifest.exit_code, solver_exit_code=manifest.solver_exit_code,
            error_present=bool(manifest.error), error_code=error_code(manifest.error),
            collection_error_count=len(manifest.collection_errors),
            collection_error_codes=[error_code(item) for item in manifest.collection_errors],
            withheld_control_output_count=withheld, outputs=scientific_paths)
        projection_directory = Path(projection_directory)
        if (not projection_directory.is_absolute() or projection_directory.resolve() != projection_directory
                or not projection_directory.is_dir()):
            raise e.IntegrityError('Scientific TCAD projection requires its explicit canonical host directory')
        manifest_path=Path(manifests[0].local_path)
        if projection_directory.parent.name=='calls':
            call=projection_directory.name
            base=projection_directory.parent.parent
            if not e.ID.fullmatch(call) or base.name!='scientific-output-views':
                raise e.IntegrityError('Scientific TCAD projection lost its exact original call scope')
        else:
            # Existing retained manifests identify their historical destination.
            # External originals need the host's exact current call argument.
            call=next((parent.name for parent in manifest_path.parents if parent.parent.name=='calls'),None)
            if call is None or not e.ID.fullmatch(call):
                raise e.IntegrityError('Scientific TCAD projection lacks its exact original call scope')
            base=projection_directory
        directory = _directory(base / 'tcad/v3' / call)
        if output_directory is None:
            path = directory / 'tcad_execution_facts.json'
        else:
            output_directory=Path(output_directory)
            if (not output_directory.is_absolute() or output_directory.resolve()!=output_directory
                    or not output_directory.is_dir() or output_directory.name!='outputs'
                    or output_directory.parent.name!=call):
                raise e.IntegrityError('Scientific TCAD facts require the exact Node call output directory')
            path=output_directory/'tcad_execution_facts.json'
        if path.resolve() != path or path.is_symlink():
            raise e.IntegrityError('Scientific TCAD facts changed their host namespace')
        _write_original(path, facts)
        _write_original(directory / 'private-scientific-path-bindings.json',
            dict(manifest_ref=dict(path=manifests[0].local_path,size_bytes=manifests[0].size_bytes), outputs=path_bindings))
        mode=0o440 if output_directory is None else 0o400
        if path.stat().st_mode&0o777!=mode:path.chmod(mode)
        original = _proof(path)
        result.append(LocalFileDescriptor(name='tcad_execution_facts.json', local_path=str(path),
            size_bytes=original['size_bytes'], media_type='application/json'))
        return tuple(result)

    def collect(self, external):
        return self.collect_original(self._external_call(external))

    @e.record_effect
    def cancel_original(self, call):
        accepted, prepared = self._bound(call)
        details = self.status_details(call)
        if details.get('state') == 'cancelling' or execution_status(details.get('state')) in {'known_success', 'known_failed'}:
            return details['state']
        if details.get('state') == 'unknown':
            return 'unknown'
        value = accepted['tcad']
        self._check('cancel', call, LocalFileDescriptor.model_validate(value['payload']),
                    prepared=prepared, prior=value['authority'])
        return self.adapter.cancel(self.records.external(call))

    def cancel(self, external):
        return self.cancel_original(self._external_call(external))

    @e.record_effect
    def resource_consumption(self, call):
        """Trusted projection from the same verified native terminal originals."""
        from .execution_control import _consumed_budget
        completed = self._completed(call)
        collected = json.loads(e.read(self.records.directory(call) / 'collected.json'))
        outputs = tuple(LocalFileDescriptor.model_validate({key:value for key,value in item.items() if key!='file_stat'})
            for item in collected['outputs'])
        manifest = self._inventory(outputs, completed['native_state'],context=self.operation_context)
        return _consumed_budget(manifest.model_dump(mode='json')) or {}

    @staticmethod
    def scientific_source(accepted, *, implementation=None):
        return implementation

    @staticmethod
    def public_result(result):
        return {key: result[key] for key in ('call_id', 'status', 'native_state', 'note',
            'authorization_request', 'review_url', 'resource_accounting') if key in result}


def configure_managed_execution(config, source, *, binding):
    """Pure local preflight: parse declarations without building or contacting adapters."""
    from scidiscovery.plugin_configuration import PluginConfigurationError
    from .command_adapter import (CommandAdapterConfig, read_command_configuration,
        validate_command_configuration, bundled_ssh_configuration)
    from .ssh_transport import SSHTCADTransportConfig, read_transport_config
    from pydantic import ValidationError

    def invalid(field, hint):
        return PluginConfigurationError('tcad_configuration_invalid', 'TCAD 插件配置不完整或无效。',
            hint=f'请检查插件配置中的 {field}；{hint}', plugin_id='tcad_execution')

    def schema_field(error, model, fallback):
        while error is not None:
            if isinstance(error, ValidationError):
                for detail in error.errors(include_input=False, include_url=False):
                    location = detail.get('loc', ())
                    if location and location[0] in model.model_fields:
                        return str(location[0])
            error = error.__cause__
        return fallback

    if binding != 'tcad':
        raise PluginConfigurationError('plugin_binding_invalid', 'TCAD 执行器绑定无效。',
            field_path='executor.config.executor', hint='请使用 tcad_execution:tcad。', plugin_id='tcad_execution')
    if source is None:
        raise invalid('config_file', 'TCAD 必须使用独立 JSON 或 TOML 配置文件。')
    body = dict(config)
    allowed = set(TCADRuntimeConfig.model_fields) | {'max_seconds', 'implementation_compilation'}
    if set(body) - allowed:
        raise invalid('配置字段', '请仅保留 TCAD 支持的设置。')
    base = source.parent
    for field in ('socket_path', 'command_config_path'):
        if isinstance(body.get(field), str):
            if not body[field].strip():
                raise invalid(field, '请填写非空路径。')
            body[field] = os.path.abspath(base / Path(body[field]).expanduser())
    try:
        transport = TCADRuntimeConfig.model_validate_json(json.dumps({
            key: body[key] for key in TCADRuntimeConfig.model_fields if key in body}), strict=True)
    except (ValueError, TypeError) as error:
        raise invalid(schema_field(error, TCADRuntimeConfig, 'transport/socket_path/command_config_path'),
            '请选择 socket 或 command，并仅提供该传输需要的路径。') from None
    if 'max_seconds' in body and (type(body['max_seconds']) not in (int,float)
            or not 0 < body['max_seconds'] < float('inf')):
        raise invalid('max_seconds', '请填写有限正数。')
    if transport.command_config_path is not None:
        try:
            command = read_command_configuration(transport.command_config_path)
            executable = Path(command.executable)
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise ValueError()
        except (OSError, ValueError, TypeError) as error:
            raise invalid('command_config_path → ' + schema_field(error, CommandAdapterConfig, '命令配置文件'),
                '请确认命令配置文件存在、可读、权限安全，且 executable 可执行。') from None
        try:
            validate_command_configuration(command)
            nested = bundled_ssh_configuration(command)
        except (OSError, ValueError, TypeError) as error:
            raise invalid('command_config_path → SSH.' + schema_field(error, SSHTCADTransportConfig, 'arguments.--config'),
                '请修正内层 SSH 配置文件路径、读取权限或配置格式。') from None
        if nested is not None:
            ssh = read_transport_config(nested)
            for field in ('ssh_executable', 'identity_file', 'known_hosts_file'):
                value = getattr(ssh, field)
                if value is not None and (not Path(value).is_file() or not os.access(value, os.R_OK)):
                    raise invalid('command_config_path → SSH.' + field,
                        '请确认引用的本地文件存在且可读取。')
            if not os.access(ssh.ssh_executable, os.X_OK):
                raise invalid('command_config_path → SSH.ssh_executable', '请选择可执行的 SSH 程序。')
            if ssh.destination_resolver and (not Path(ssh.destination_resolver[0]).is_file()
                    or not os.access(ssh.destination_resolver[0], os.X_OK)):
                raise invalid('command_config_path → SSH.destination_resolver', '请选择可执行的本地解析程序。')
    if 'implementation_compilation' in body:
        try:
            compilation = dict(body['implementation_compilation'])
            if isinstance(compilation.get('capability_snapshot_path'), str):
                if not compilation['capability_snapshot_path'].strip():
                    raise ValueError()
                compilation['capability_snapshot_path'] = os.path.abspath(base / Path(
                    compilation['capability_snapshot_path']).expanduser())
            compilation = TCADCompilationConfig.model_validate_json(json.dumps(compilation), strict=True)
            snapshot_path = Path(compilation.capability_snapshot_path)
            if snapshot_path.is_symlink() or not snapshot_path.is_file():
                raise ValueError()
            with snapshot_path.open('rb') as stream:
                snapshot = stream.read(256_001)
            if len(snapshot) > 256_000:
                raise ValueError()
            SolverCapabilitySnapshot.model_validate_json(snapshot, strict=True)
        except (OSError, ValueError, TypeError):
            raise invalid('implementation_compilation.capability_snapshot_path',
                '请提供可读取且符合求解器能力 schema 的快照文件。') from None
        body['implementation_compilation'] = compilation.model_dump(mode='json')
    return body


def build_managed_runtime(context):
    if context.mode != 'local_worker':
        raise ValueError('TCAD managed registration requires the existing local_worker factory mode')
    body = json.loads(context.config_bytes)
    allowed = set(TCADRuntimeConfig.model_fields) | e.HOST_CONFIGURATION_FIELDS | {'max_seconds', 'implementation_compilation'}
    if not isinstance(body, dict) or set(body) - allowed or 'frozen_execution_interface' in body:
        raise ValueError('TCAD managed configuration contains undeclared settings')
    transport = TCADRuntimeConfig.model_validate_json(json.dumps({
        key: body[key] for key in TCADRuntimeConfig.model_fields if key in body}), strict=True)
    adapter = build_adapter(transport, state_root=context.state_root)
    services = getattr(context, 'execution_host_services', None)
    adapter.operation_context = getattr(services, 'operation_context', None)
    gate = services.gate(lambda raw: adapter.execution_admission(raw,
        preparation_profile=_package_profile(raw))) if services is not None else None
    # Gate and exact-input services are host objects; JSON cannot supply either.
    selected = getattr(context, 'frozen_execution_interface', None)
    if selected is not None:
        body = dict(body, execution_interface=dict(selected))
    return RuntimePluginContribution(execution_adapters={'tcad': TCADManagedExecution(body, adapter, gate=gate,
        implementation_inputs=getattr(services, 'implementation_inputs', None),
        operation_context=getattr(services, 'operation_context', None))})



REGISTRATION = ExecutionInterfaceRegistration('tcad_execution', '1', RuntimePluginFactory(build_managed_runtime,
    configure=configure_managed_execution, requires_config_file=True),
    scientific_outputs=TCADManagedExecution.scientific_outputs,
    scientific_source=TCADManagedExecution.scientific_source,
    projection_contract='tcad.public-projections.v2')


def _prepared_submission_id(descriptor):
    value=json.loads(Path(descriptor['local_path']).read_bytes())
    return value.get('request_id',value.get('submission_id'))
