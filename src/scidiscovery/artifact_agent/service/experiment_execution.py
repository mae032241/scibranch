"""Control-owned execution commands scoped to one running experiment."""
from __future__ import annotations

import hashlib
import json

from ...operation_contract import contract_diagnostic, DiagnosticError, ToolInputError

from ...operations.invoke import (ApprovalProjectorContext, ApprovalSubjectSnapshot,
    effect_operation_plan)
from ..execution_bridge import ExecutionAuthorizationRequired
from ..schema.approval import (ApprovalOption, ApprovalRequest, CompiledApprovalIdentity,
    HumanDecision, execution_approval_option_id, execution_decision_authorizes)
from ..schema.common import canonical_json
from ..schema.execution import ExecutionResultManifest
from ..schema.artifact import ArtifactRegistration


class ExperimentExecution:
    def __init__(self, *, runs, executions, bridge, collection, approval_base_url):
        self.runs, self.executions = runs, executions
        self.bridge, self.collection = bridge, collection
        self.approval_base_url = approval_base_url
        if runs is not None and executions is not None and executions.approvals is not None:
            runs.approval_database_path = executions.approvals.database_path
        self.services_for = None
        self.worker_services = {}

    def task_origin(self, run):
        return self.task_lineage(run)[-1].run_id

    def publish_diagnostic_files(self, context, *, implementation, name, workspace, outputs):
        """Deliver verified control-owned diagnostic files through common streamed publication."""
        import time
        from pathlib import Path
        from ...attachment_tools import AttachmentFile, PublishFiles
        from ...agent_execution_settings import AttachmentSettings
        from ...plugin_runtime.workspace import write_control_workspace_stream
        settings = AttachmentSettings.model_validate((self.runs.status(context.run_id).recovery_policy or {}).get('attachments', {}))
        deadline = time.monotonic() + context.remaining_seconds
        files, delivered = [], []
        for item in outputs:
            if 'relative_path' not in item:
                continue
            source = Path(workspace) / item['relative_path']
            if source.is_symlink() or not source.resolve().is_relative_to(Path(workspace).resolve()):
                raise ValueError('Diagnostic output is outside its controlled workspace')
            filename = Path(item.get('source_path') or item['name']).name
            target = Path('scratch/debug') / name / item['name'] / filename
            digest, size = hashlib.sha256(), 0
            def chunks():
                nonlocal size
                with source.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(settings.chunk_bytes), b''):
                        size += len(chunk)
                        if size > min(settings.max_item_bytes, item['size_bytes']) or time.monotonic() >= deadline:
                            raise ValueError('Diagnostic output exceeded its delivery budget')
                        digest.update(chunk)
                        yield chunk
                if size != item['size_bytes'] or digest.hexdigest() != item['sha256']:
                    raise ValueError('Diagnostic output differs from its controlled identity')
            write_control_workspace_stream(context.workspace, target, chunks())
            source_name = item.get('source_path') or item['name']
            caption = source_name if len(source_name) <= 512 else source_name[:511] + '…'
            files.append(AttachmentFile(path=target.as_posix(), purpose=f'Native diagnostic output {caption} ({item["name"]}); requires scientific assessment, not a formal execution result.'))
            delivered.append({'name': item['name'], 'file_name': source_name, 'path': target.as_posix(),
                'size_bytes': size, 'sha256': item['sha256']})
        if files:
            published = context.publish_files(PublishFiles(files=tuple(files), source_aliases=(implementation,)))
            for item, retained in zip(delivered, published['files'], strict=True):
                item['reference'] = retained['reference']
        return delivered

    def task_lineage(self, run):
        return self.runs.task_lineage(run.run_id)

    def _domain_active(self, run):
        if self.services_for is not None:
            self.worker_services.update(self.services_for(run.operation_id))
        ancestors = self.task_lineage(run)
        lineage = {item.run_id for item in ancestors}
        inputs = tuple(binding for item in ancestors for binding in item.inputs)
        details = []
        for service in {id(item): item for item in self.worker_services.values()}.values():
            if callable(getattr(service, "experiment_pending_diagnostics", None)):
                details.extend(service.experiment_pending_diagnostics(lineage, inputs))
            elif callable(getattr(service, "experiment_activity", None)) and service.experiment_activity(lineage, inputs, cancel=False):
                details.append(contract_diagnostic("experiment_diagnostics_pending", phase="protocol",
                    affected_action="submit", repairable=True,
                    message="An experiment diagnostic is still active. Finish or cancel the existing diagnostic, then resubmit the unchanged report."))
        return details

    def require_idle(self, run):
        from .run_outputs import RunOutputError
        origin = self.task_origin(run)
        active = self._domain_active(run)
        blockers = []
        incomplete = []
        for binding in self.runs.scheduler_bindings.list(instance=run.instance_id, namespace="execution",
                name_prefix="experiment-" + origin + "-"):
            if self._execution_owner(binding.object_id) != origin:
                continue
            self._reconcile_authorization(binding.object_id)
            status = self.executions.status(binding.object_id)
            if status.external_run_id is not None and status.state in {"submitted", "running", "cancelling"}:
                # Final delivery must not depend on the next background observation.
                self.bridge.sync(execution_id=binding.object_id, diagnostic_scope="instance:" + run.instance_id)
                status = self.executions.status(binding.object_id)
            summary = getattr(self.collection, 'summary', None)
            collection = summary(binding.object_id) if callable(summary) and status.state in {
                'succeeded', 'failed', 'cancelled'} else {}
            # A collector may finish while its summary is being observed.
            status = self.executions.status(binding.object_id)
            collection_failed = (collection.get('state') in {'failed', 'timed_out', 'interrupted'}
                and not collection.get('recovery_pending', False))
            if status.state in {'succeeded', 'failed', 'cancelled'} and collection_failed:
                incomplete.append(binding.name.removeprefix('experiment-' + origin + '-'))
            if status.state == 'collected' and run.accepted_candidate_digest is None:
                # Background collection can finish without another Worker call.
                # Reconcile its receipt before the finalizer snapshots materials.
                from types import SimpleNamespace
                try:
                    implementation = self._execution_implementation_record(run, binding.object_id)
                except DiagnosticError as error:
                    if not error.details or any(item['code'] not in {
                            'experiment_implementation_unavailable', 'experiment_implementation_ambiguous'} for item in error.details):
                        raise  # Proof/integrity failures must reach the submission policy.
                    # No exact receipt can be synthesized. Keep the collected files,
                    # and permit only an explicitly limited scientific conclusion.
                    incomplete.append(binding.name.removeprefix('experiment-' + origin + '-'))
                    implementation = None
                if implementation is not None:
                    context = SimpleNamespace(run_id=run.run_id, accept_evidence=lambda **values:
                        self.runs.accept_tool_evidence(run.run_id, tool_name='worker_experiment_execute',
                            allowed_ports=('tool_evidence', 'recovery_manifest_output'), **values))
                    name = binding.name.removeprefix('experiment-' + origin + '-')
                    self._execution_status(context, SimpleNamespace(name=name), binding.object_id, implementation, seal=True)
            if ((status.state not in {"failed", "cancelled", "collected", "abandoned"}
                    and not (status.state == 'succeeded' and collection_failed))
                    or collection.get('recovery_pending', False)
                    or (status.state != 'collected' and collection.get('state') in {
                        'pending', 'running', 'stopping', 'stop_pending', 'unknown'})):
                name = binding.name.removeprefix("experiment-" + origin + "-")
                blockers.append(contract_diagnostic("experiment_outstanding_work", phase="protocol",
                    affected_action="submit", repairable=True,
                    message=(f"Execution {name!r} has undelivered results (collection={collection.get('state', 'not_started')}). "
                        f"Use worker_experiment_execute(action='collect', name={name!r}) if collection has not started; "
                        "if it is already active, read status until collected, then resubmit the report. Do not rerun the solver."
                        if status.state in {'succeeded', 'failed', 'cancelled'} else
                        f"Execution {name!r} is {status.state}. Read worker_experiment_execute(action='status', name={name!r}); finish it or call action='cancel' with the same name, then resubmit the unchanged report.")))
        for diagnostic in active:
            if diagnostic["code"] == "experiment_diagnostic_submission_unknown":
                name = diagnostic.get("path", "").removeprefix("$.diagnostics.")
                incomplete.append("diagnostic:" + name)
                self.runs.record_attention(run.run_id, scope="diagnostic:" + name,
                    code="experiment_diagnostic_submission_unknown")
            else:
                blockers.append(diagnostic)
        if blockers:
            from ...operation_contract import bounded_diagnostics
            raise RunOutputError("Outstanding experiment work must finish before final delivery.",
                category="execution_pending", details=bounded_diagnostics(blockers, phase="protocol", action="submit"))
        return tuple(incomplete)

    @staticmethod
    def validate_incomplete_delivery(content, names):
        from .run_outputs import RunOutputError
        report = json.loads(content)
        if report.get('outcome') in {'blocked', 'inconclusive'} and any(
                isinstance(item, str) and item.strip() for item in report.get('limitations', ())):
            return
        raise RunOutputError('Execution results were not delivered.', details=(contract_diagnostic(
            'experiment_collection_incomplete', phase='protocol', affected_action='submit', repairable=True,
            message=f"Result collection or exact implementation binding is incomplete for {list(names)[:8]!r}. Submit outcome blocked/inconclusive with limitations explaining the missing delivery, binding or unresolved diagnostic. This does not release diagnostic reservations. Root can use run_diagnostic on the owning task and exact diagnostic name for status/cancel after the report is sealed; retry collection only if collection actually failed. Do not rerun the solver or claim completed delivery."),))

    def diagnostic_control(self, run, *, name, action, file_name=None, offset=0, max_bytes=8192):
        """Observe/cancel existing diagnostic work, never start replacement work."""
        if self.services_for is not None:
            self.worker_services.update(self.services_for(run.operation_id))
        ancestors = self.task_lineage(run)
        run_ids = {item.run_id for item in ancestors}
        inputs = tuple(binding for item in ancestors for binding in item.inputs)
        matches = []
        for service in {id(item): item for item in self.worker_services.values()}.values():
            reader = getattr(service, "experiment_diagnostic_status", None)
            if callable(reader):
                result = reader(run_ids, inputs, name=name,
                    recovery_route=lambda owner, diagnostic: self.diagnostic_recovery_route(run, owner, diagnostic))
                if result.get("state") != "not_created":
                    matches.append((service, result))
        if not matches:
            return {"name": name, "state": "not_created", "submission": "not_submitted"}
        if len(matches) != 1:
            raise ToolInputError("experiment_diagnostic_ambiguous", "This diagnostic name identifies multiple transports; retain the original identities and ask the administrator to repair the binding.")
        service, result = matches[0]
        if action == "cancel":
            service.experiment_activity(run_ids, inputs, cancel=True, name=name)
            result = service.experiment_diagnostic_status(run_ids, inputs, name=name,
                recovery_route=lambda owner, diagnostic: self.diagnostic_recovery_route(run, owner, diagnostic))
        if action == "collect":
            from ...agent_execution_settings import ExecutionIOSettings
            limits = ExecutionIOSettings.model_validate((run.recovery_policy or {}).get('execution_io', {}))
            result = service.experiment_diagnostic_collect(run_ids, inputs, name=name,
                run_id=run.run_id, operation_id=run.operation_id, seconds=limits.file_timeout_seconds,
                publish=lambda receipt, files: self._publish_diagnostic_delivery(run, name, receipt, files))
        if action == "read":
            return self._read_diagnostic_delivery(run, result, file_name=file_name, offset=offset, max_bytes=max_bytes)
        unresolved = result.get("submission") == "unknown"
        active = result.get("state") in {"accepted", "running", "cancelling"}
        pending_delivery = (result.get('delivery_pending') or
            not result.get('delivery') and result.get('state') in {'succeeded', 'failed', 'cancelled'}
            and result.get('submission') == 'submitted')
        # Preserve attention after scientific completion; exact recovery clears
        # only this diagnostic's issue in its lineage.
        for owner in ancestors:
            self.runs.record_attention(owner.run_id, scope="diagnostic:" + name,
                code=("experiment_diagnostic_submission_unknown" if unresolved else
                    "experiment_diagnostic_running" if active else
                    "experiment_diagnostic_results_pending" if pending_delivery else None))
        return self._public_diagnostic_projection(result)

    def diagnostic_recovery_route(self, run, owner_id, name):
        """Resolve a control identity only within the caller's exact instance."""
        owner = self.runs.status(owner_id)
        if owner.instance_id != run.instance_id:
            return {'code': 'experiment_diagnostic_owner_unavailable'}
        owner_name = self.runs.scheduler_bindings.find_name(instance=run.instance_id,
            namespace='run', object_id=owner_id)
        if owner_name is None:
            return {'code': 'experiment_diagnostic_owner_unavailable'}
        instance = self.runs.scheduler_bindings.get_instance(instance_id=run.instance_id)
        return {'owner_name': owner_name, 'diagnostic_name': name,
            'request': {'name': 'run_diagnostic', 'surface': 'execution',
                'arguments': {'name': owner_name, 'diagnostic_name': name, 'action': 'cancel'}},
            'administrator': {'module': 'scidiscovery.artifact_agent.interfaces.submission_recovery',
                'arguments': ['--instance', instance.name, '--run-name', owner_name,
                    '--diagnostic-name', name],
                'instruction': 'An administrator must stop all old local and remote submitters, then supply the configured --state-root, --confirm-old-submitters-stopped and an audited --reason. This only enables exact remote retirement; retry the Root cancel request afterward.'}}

    @staticmethod
    def _public_diagnostic_projection(value):
        """Withhold unsafe fields explicitly; private diagnostic originals stay exact."""
        from ...plugin_runtime.workspace import validate_publication_content, WorkspaceOutputError
        result, restricted = {}, []
        for field, content in value.items():
            try:
                validate_publication_content(field, canonical_json(content), "application/json")
            except WorkspaceOutputError:
                restricted.append(field)
                if field == "summary":
                    result[field] = "Diagnostic summary withheld by the public-content boundary; original retained privately."
            else:
                result[field] = content
        if restricted:
            result["restricted_fields"] = restricted
        return result

    def _publish_diagnostic_delivery(self, run, name, receipt, files):
        """Seal takeover output independently of an already final scientific report."""
        from ..schema.refs import ActorRef, ArtifactRef
        from ...plugin_runtime.workspace import validate_publication_content, WorkspaceOutputError
        proof = receipt['proof']
        implementation = ArtifactRef.model_validate(proof['implementation_ref'])
        self.runs.artifacts.verify(implementation)
        owner = self.runs.status(proof['run_id'])
        parents = (implementation,)
        labels = {'scientific_claim_admissible': 'false', 'source_origin': 'development_diagnostic'}
        key = hashlib.sha256(canonical_json({'owner': proof['run_id'], 'name': name,
            'submission': receipt['submission']['sha256']})).hexdigest()
        base = 'diagnostic.' + key
        creator = ActorRef(actor_type='service', actor_id='experiment_execution')
        entries = []
        for ordinal, item in enumerate(files):
            if item['relative_path'] == f'reports/diagnostic-{name}.json':
                continue  # Private control proof: public receipt retains scientific diagnostics only.
            raw = item['path'].read_bytes()
            if len(raw) != item['size_bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Retained diagnostic differs from its verified receipt')
            entry = {'file_name': item['relative_path'], 'media_type': item['media_type'],
                'size_bytes': item['size_bytes'], 'sha256': item['sha256']}
            try:
                validate_publication_content('file_identity', canonical_json(entry), 'application/json')
            except WorkspaceOutputError:
                entry = self._public_diagnostic_projection(entry)
                entry.setdefault('file_name', 'restricted_file_' + str(ordinal))
                entry.update(publication='restricted', reason='public_content_boundary',
                    original_retained_privately=True)
                entries.append(entry)
                continue
            try:
                validate_publication_content(entry['file_name'], raw, item['media_type'])
            except WorkspaceOutputError:
                entry.update(publication='restricted', reason='public_content_boundary',
                    original_retained_privately=True)
                entries.append(entry)
                continue
            registration = ArtifactRegistration(kind='diagnostic_file', schema_id='scidiscovery.opaque.v1',
                payload_schema_version=1, media_type=item['media_type'], creator=creator, parent_refs=parents, labels=labels)
            envelope = self.runs.artifacts.register_file(item['path'], registration,
                expected_sha256=item['sha256'], expected_size=item['size_bytes'],
                idempotency_key='diagnostic-delivery:' + key + ':file:' + str(ordinal))
            artifact_name = base + '.file.' + str(ordinal)
            self.runs.scheduler_bindings.bind(instance=run.instance_id, namespace='artifact',
                name=artifact_name, object_id=envelope.artifact_id)
            entries.append({**entry, 'artifact_name': artifact_name, 'publication': 'original'})
        response = receipt['response']
        manifest = self._public_diagnostic_projection({'schema_version': 1, 'diagnostic_name': name,
            'owning_task': owner.output_binding_name.removesuffix('.output'),
            'development_only': True, 'scientific_claim_admissible': False,
            **{key: response[key] for key in
                ('mode', 'state', 'diagnostic_layer', 'summary', 'exit_code', 'missing_outputs') if key in response}})
        manifest.setdefault('diagnostic_name', 'diagnostic-' + key[:16])
        manifest.setdefault('owning_task', 'task-' + hashlib.sha256(owner.run_id.encode()).hexdigest()[:16])
        manifest['files'] = entries
        manifest_bytes = canonical_json(manifest)
        validate_publication_content('diagnostic_receipt', manifest_bytes, 'application/json')
        envelope = self.runs.artifacts.register(manifest_bytes,
            ArtifactRegistration(kind='diagnostic_delivery', schema_id='scidiscovery.diagnostic-delivery.v1',
                payload_schema_version=1, media_type='application/json', creator=creator, labels=labels,
                parent_refs=tuple(dict.fromkeys((*parents, *(self.runs.artifacts.get_by_id(
                    self.runs.scheduler_bindings.resolve(instance=run.instance_id, namespace='artifact', name=item['artifact_name'])).ref for item in entries if 'artifact_name' in item))))),
            idempotency_key='diagnostic-delivery:' + key + ':receipt')
        self.runs.scheduler_bindings.bind(instance=run.instance_id, namespace='artifact',
            name=base + '.receipt', object_id=envelope.artifact_id)
        return {'artifact_name': base + '.receipt', 'file_count': len(entries),
            'restricted_file_count': sum(item['publication'] == 'restricted' for item in entries)}

    def _read_diagnostic_delivery(self, run, result, *, file_name, offset, max_bytes):
        import base64
        from ...plugin_runtime.workspace import validate_publication_content, WorkspaceOutputError
        delivery = result.get('delivery')
        if not delivery:
            raise ToolInputError('experiment_diagnostic_not_delivered',
                'This diagnostic has no sealed takeover delivery; collect the exact diagnostic first.')
        receipt_id = self.runs.scheduler_bindings.resolve(instance=run.instance_id,
            namespace='artifact', name=delivery['artifact_name'])
        receipt_envelope = self.runs.artifacts.get_by_id(receipt_id)
        manifest_raw = self.runs.artifacts.read(receipt_envelope.ref)
        manifest = json.loads(manifest_raw)
        public_manifest = self._public_diagnostic_projection({key: value for key, value in manifest.items() if key != 'files'})
        public_entries = []
        for index, original in enumerate(manifest.get('files', ())):
            entry = self._public_diagnostic_projection(original)
            if 'file_name' not in entry or ('artifact_name' in original and 'artifact_name' not in entry):
                entry.setdefault('file_name', 'restricted_file_' + str(index))
                entry.update(publication='restricted', reason='public_content_boundary', original_retained_privately=True)
            public_entries.append(entry)
        public_manifest['files'] = public_entries
        envelope = receipt_envelope
        public_name = file_name
        restricted = False
        projected = False
        if file_name is not None:
            pair = next(((original, public) for original, public in zip(manifest.get('files', ()), public_entries)
                if file_name in {original.get('file_name'), public.get('file_name')}), None)
            if pair is None:
                raise ToolInputError('experiment_diagnostic_file_unknown', 'Choose an exact file_name from the sealed receipt.')
            selected, visible = pair
            public_name = visible['file_name']
            restricted = visible.get('publication') == 'restricted'
            if not restricted:
                envelope = self.runs.artifacts.get_by_id(self.runs.scheduler_bindings.resolve(
                    instance=run.instance_id, namespace='artifact', name=selected['artifact_name']))
                content = self.runs.artifacts.read(envelope.ref)
                try:
                    # Check the complete immutable object, never a page fragment.
                    validate_publication_content('diagnostic_file', content, envelope.media_type)
                except WorkspaceOutputError:
                    restricted = True
        else:
            try:
                validate_publication_content('diagnostic_receipt', manifest_raw, 'application/json')
            except WorkspaceOutputError:
                projected = True
                public_manifest.update(publication='restricted_projection', original_retained_privately=True)
                content = canonical_json(public_manifest)
                validate_publication_content('diagnostic_receipt_projection', content, 'application/json')
            else:
                content = manifest_raw
        metadata = self._public_diagnostic_projection({'artifact_name': delivery['artifact_name'],
            'receipt_sha256': receipt_envelope.sha256, 'file_name': public_name, 'media_type': envelope.media_type})
        if restricted:
            return {**metadata, 'publication': 'restricted', 'reason': 'public_content_boundary',
                'original_retained_privately': True}
        raw = content[offset:offset + max_bytes]
        textual = envelope.media_type.startswith('text/') or envelope.media_type == 'application/json'
        return {**metadata, 'offset': offset,
            'next_offset': offset + len(raw) if offset + len(raw) < len(content) else None,
            **({'publication': 'restricted_projection', 'original_retained_privately': True} if projected else {}),
            'encoding': 'utf-8' if textual else 'base64',
            'content': raw.decode('utf-8', errors='replace') if textual else base64.b64encode(raw).decode('ascii')}

    def _execution_owner(self, execution_id):
        envelope = self.executions.artifacts.get_by_id(self.executions.request_artifact_id(execution_id))
        return envelope.labels.get("experiment_run")

    def implementation(self, context, alias):
        return self._implementation_payload(self._selected_implementation(context, alias))

    def _selected_implementation(self, context, alias):
        from .sealed_materials import material_error
        record = self.runs.sealed_material_record(self.runs.status(context.run_id), alias)
        if record is None or record.get("metadata", {}).get("kind") != "experiment_implementation":
            raise material_error("experiment_implementation_unavailable",
                "Choose a sealed implementation created in or explicitly bound to this task.")
        return record

    def _implementation_payload(self, record):
        from ..schema.refs import ArtifactRef
        from .sealed_materials import material_error
        material = ArtifactRef.model_validate(record["artifact_ref"])
        envelope = self.executions.artifacts.verify(material)
        parents = envelope.parent_refs
        if 'control_parents' in record:
            direct = tuple(ArtifactRef.model_validate(ref) for ref in record['control_parents'])
            if any(ref not in parents for ref in direct):
                raise material_error('experiment_implementation_package_invalid', 'Controlled package is not an exact implementation parent.')
            parents = direct
        packages = [ref for ref in parents
            if self.executions.artifacts.verify(ref).kind == "experiment_execution_package"]
        if len(packages) > 1 and 'control_parents' not in record:
            # Historical tool receipts inherited all frozen inputs. Only those
            # exact inputs may be excluded; never select by recency or filename.
            producer_id = envelope.labels.get('tool_producer_run')
            if producer_id is not None:
                inherited = {item.artifact_ref for item in self.runs.status(producer_id).inputs}
                packages = [ref for ref in packages if ref not in inherited]
        if len(packages) != 1:
            raise material_error("experiment_implementation_package_invalid",
                "The sealed implementation has no unique controlled execution package.")
        return packages[0]

    def _contract(self, context):
        run = self.runs.running_task(context.run_id)
        return self._contract_for_run(run)

    def _contract_for_run(self, run):
        compiled = self.runs.compiled_operation(run)
        ref = compiled.spec.executor.capability
        if ref is None:
            raise ValueError("No execution method is configured for this experiment.")
        capability = compiled.implementations[f"{ref.plugin_id or compiled.plugin_id}:{ref.component_id}"]
        effect = self.runs.operation_catalog.operation(capability.execution_operation)
        identity = effect.approval_identity
        if identity is None:
            raise ValueError("The configured execution method has no authorization contract.")
        return run, effect, CompiledApprovalIdentity(operation_id=identity.operation_id,
            operation_version=identity.version, operation_digest=identity.operation_digest,
            approval_contract_digest=identity.approval_contract_digest), capability

    def capabilities(self, context):
        _, effect, _, _ = self._contract(context)
        return self.bridge.capabilities(executor=effect_operation_plan(effect).executor)

    def seal_implementation(self, context, *, payload, scientific_material, sources, private_outputs=()):
        run, effect, _, capability = self._contract(context)
        plan = effect_operation_plan(effect)
        port = next(item for item in effect.spec.inputs if item.name == plan.payload_port)
        # Check current adapter capability before sealing/reviewing an unusable
        # implementation. This neither authorizes execution nor reserves budget;
        # start repeats the check against the then-current policy.
        self.bridge.validate_request(executor=plan.executor, preparation_profile=plan.preparation_profile,
            payload=payload, allow_denial=True)
        parents = tuple(context.source_descriptor(alias).artifact_ref for alias in sources)
        private = self.executions.artifacts.register(payload, ArtifactRegistration(
            kind="experiment_execution_package", schema_id=port.schema_id,
            payload_schema_version=capability.execution_package_schema_version,
            media_type="application/json", creator=self.executions.service_actor, parent_refs=parents,
            confidentiality="approval_only", labels={"experiment_run": run.run_id, "experiment_task": self.task_origin(run)}),
            idempotency_key="experiment-package:" + hashlib.sha256(canonical_json({
                "run": run.run_id, "payload": hashlib.sha256(payload).hexdigest(), "parents": parents})).hexdigest()).ref
        record = context.accept_evidence(raw=scientific_material, media_type="application/json",
            derived_from=sources, trusted_parent_refs=(private,), metadata={"kind": "experiment_implementation",
                "private_outputs":list(private_outputs),
                **({"output_inventory_name": capability.output_inventory_name}
                   if getattr(capability, "output_inventory_name", None) else {})})
        if (run.recovery_policy or {}).get("require_execution_review"):
            record["review"] = self.review_handoff(run, record)
        return record

    def review_handoff(self, run, implementation):
        """Give the scheduler an exact public request, without starting a reviewer."""
        name = self.runs.publish_sealed_material(run, implementation["alias"])
        request_name = name + ".review"
        if len(request_name) > 256:
            request_name = name[:230] + ".review-" + hashlib.sha256(name.encode()).hexdigest()[:16]
        return {"required": bool((run.recovery_policy or {}).get("require_execution_review")),
            "read_guidance": "After the review completes, call worker_experiment_execute(action='review', implementation=<the same reference>) again. It returns review_result.verdict and a read_request for worker_reference_read, including revise/blocked findings, without ending this Run.",
            "subject_artifact_name": name,
            "request": {"name": request_name, "operation_id": "science.object.review.v1",
                "inputs": [{"port": "subject", "artifact_names": [name]}],
                "instruction": "Independently review this exact sealed implementation against its bound research objective, scientific constraints and required observables. Report missing evidence and limitations explicitly; execution permission does not establish scientific validity."}}

    def _execution_implementation_record(self, run, execution_id):
        envelope = self.executions.artifacts.get_by_id(self.executions.request_artifact_id(execution_id))
        from ..schema.refs import ArtifactRef
        raw = envelope.labels.get("implementation_receipt")
        receipt = ArtifactRef.model_validate_json(raw, strict=True) if raw is not None else None
        from .run_attention import BINDING_CODES
        scope = 'implementation:' + execution_id
        try:
            payload = self.executions.request(execution_id).payload_ref
            if receipt is None:
                # Request scope identifies the recovery root, not the package author.
                # A successor may have sealed the package after that root ended.
                author = self.executions.artifacts.verify(payload).labels.get("experiment_run")
                if author:
                    owner = self.runs.status(author)
                    if owner.instance_id != run.instance_id:
                        from .sealed_materials import material_error
                        raise material_error("experiment_implementation_unavailable",
                            "The historical package author belongs to another instance.")
                    original = self._run_implementation_record(owner, payload)
                else:
                    # Older packages without author metadata require uniqueness over
                    # the whole original task family, never the consumer's subset.
                    origin = envelope.labels.get("experiment_run")
                    if not origin:
                        from .sealed_materials import material_error
                        raise material_error("experiment_implementation_unavailable",
                            "The historical execution has no authoritative implementation owner. Preserve its results and request control repair.")
                    _, family = self.runs.task_family(origin)
                    if any(owner.instance_id != run.instance_id for owner in family):
                        from .sealed_materials import material_error
                        raise material_error("experiment_implementation_unavailable",
                            "The historical execution belongs to another instance.")
                    original = self._matching_implementation_record(
                        [item for owner in family for item in self.runs.available_material_records(owner)], payload)
                receipt = ArtifactRef.model_validate(original['artifact_ref'])
            record = self._run_implementation_record(run, payload, receipt_ref=receipt)
        except DiagnosticError as error:
            code = next((item['code'] for item in error.details if item['code'] in BINDING_CODES), None)
            if code:
                self.runs.record_attention(run.run_id, scope=scope, code=code)
            raise
        self.runs.record_attention(run.run_id, scope=scope, code=None)
        return record

    def _run_implementation_record(self, run, payload_ref, *, receipt_ref=None):
        return self._matching_implementation_record(self.runs.available_material_records(run), payload_ref,
            receipt_ref=receipt_ref)

    def _matching_implementation_record(self, materials, payload_ref, *, receipt_ref=None):
        records = [item for item in materials
            if item.get("metadata", {}).get("kind") == "experiment_implementation"
            and (receipt_ref is None or canonical_json(item["artifact_ref"]) == receipt_ref.canonical_json())
            and self._implementation_payload(item) == payload_ref]
        if not records:
            from .sealed_materials import material_error
            raise material_error("experiment_implementation_unavailable",
                "Bind the exact sealed implementation used by this execution.")
        if len({canonical_json(item["artifact_ref"]) for item in records}) != 1:
            from .sealed_materials import material_error
            raise material_error("experiment_implementation_ambiguous",
                "This historical execution did not retain its exact implementation receipt. "
                "Preserve the execution and its collected results. Root must inspect the retained records; "
                "the Worker cannot repair a historical execution binding. Once work is idle, submit blocked "
                "or inconclusive with limitations describing this missing exact binding. Do not rerun the solver.")
        return records[0]

    def _cancel(self, execution_id):
        if self.bridge.submissions.summary(execution_id).get("state") == "unknown":
            self.bridge.cancel(execution_id=execution_id)
            return
        self.bridge.submissions.require_idle(execution_id)
        status = self.executions.status(execution_id)
        if status.state == "abandoned":
            self.executions.abandon(execution_id)  # Complete any interrupted approval cleanup.
            return
        if status.state in {"created", "authorized"}:
            prepared = self.executions.prepared_submission(execution_id)
            if prepared is None:
                self.bridge.abandon(execution_id)
                return
            found = self.bridge._adapter(status.executor).lookup_submission(prepared)
            if found is None:
                message = "Cancellation could not be confirmed: the exact prepared submission is not yet visible and an earlier submit may still be processing. Its identity and budget remain reserved. Recover the same execution after service recovery, then cancel the confirmed job; do not create a replacement."
                raise DiagnosticError(message, details=(contract_diagnostic("experiment_execution_submission_unknown",
                    phase="tool_execution", affected_action="tool_call", repairable=False, message=message),))
            self.bridge._record_started(execution_id, *found)
        if self.executions.status(execution_id).state != "abandoned":
            self.bridge.cancel(execution_id=execution_id)

    def _io_settings(self, context):
        from ...agent_execution_settings import ExecutionIOSettings
        run = self.runs.running_task(context.run_id)
        return ExecutionIOSettings.model_validate((run.recovery_policy or {}).get("execution_io", {}))

    def _export(self, context, request, envelope):
        import os
        import time
        import uuid
        from pathlib import Path
        name = request.output_name
        if name in {".", ".."} or any(value in name for value in ("/", "\\", "\0")):
            raise ValueError("The collected output has no safe scientific filename.")
        if envelope.size_bytes > self._io_settings(context).max_export_bytes:
            raise ToolInputError("experiment_export_limit", "The collected output exceeds this workspace's file limit.", path="$.output_name")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        handles, temporary = [], ".export-" + uuid.uuid4().hex
        deadline = time.monotonic() + min(context.remaining_seconds, self._io_settings(context).file_timeout_seconds)
        try:
            parent = os.open(context.workspace, flags)
            handles.append(parent)
            for part in ("scratch", "collected", request.name):
                try:
                    os.mkdir(part, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                parent = os.open(part, flags, dir_fd=parent)
                handles.append(parent)
            target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=parent)
            with os.fdopen(target, "wb") as destination, self.executions.artifacts.open_original(envelope.ref) as original:
                while chunk := original.read(1024*1024):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Scientific output export exceeded the remaining task time.")
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            if handles:
                try:
                    os.unlink(temporary, dir_fd=handles[-1])
                except FileNotFoundError:
                    pass
            for descriptor in reversed(handles):
                os.close(descriptor)
        return {"name":name, "path":str(Path("scratch/collected") / request.name / name),
            "media_type":envelope.media_type, "size_bytes":envelope.size_bytes}

    def _implementation_review(self, run, implementation):
        from ..schema.refs import ArtifactRef
        from .sealed_materials import material_error
        from ...general_science_experiment_review import ExperimentReview
        subject = ArtifactRef.model_validate(implementation["artifact_ref"])
        review = self.runs.latest_subject_review(run.instance_id, subject)
        if review is None or review.output_ref is None:
            return None
        compiled = self.runs.operation_catalog.operation("science.object.review.v1")
        if review.operation_version != compiled.spec.version or "subject" not in compiled.spec.independent_review_ports:
            raise material_error("experiment_execution_review_incompatible",
                "The exact implementation review uses a different installed review contract. Call worker_experiment_execute(action='review', implementation=<the same reference>) for a compatible review request; dispatch a new independent review.")
        envelope = self.runs.artifacts.catalog(review.output_ref)
        if envelope.schema_id != "scidiscovery.experiment-review.v1" or envelope.size_bytes > compiled.spec.limits.max_output_bytes:
            raise material_error("experiment_execution_review_invalid", "The exact implementation review has no valid sealed review result. Use worker_experiment_execute(action='review', implementation=<the same reference>) to obtain the exact review subject for repair.")
        raw = self.runs.artifacts.read(review.output_ref)
        result = ExperimentReview.model_validate_json(raw, strict=True)
        return review.output_ref, raw, result

    def _required_review(self, run, implementation):
        if not (run.recovery_policy or {}).get("require_execution_review"):
            return None
        from .sealed_materials import material_error
        review = self._implementation_review(run, implementation)
        if review is None:
            raise material_error("experiment_execution_review_required",
                "Independent implementation review is required. Call worker_experiment_execute(action='review', implementation=<the same reference>) for the exact public subject and operation_invoke request. Ask the scheduler to dispatch an independent reviewer, then retry start with the same implementation after a sealed pass. No execution was created.")
        ref, _, result = review
        if result.verdict != "pass":
            raise material_error("experiment_execution_review_not_passed",
                "The latest independent review of this exact implementation did not pass. Resolve its findings and review any revised implementation before execution; budget approval does not override this requirement. Call worker_experiment_execute(action='review', implementation=<the chosen reference>) for the exact review request.")
        return ref

    def command(self, context, request):
        run = self.runs.running_task(context.run_id)
        execution_id = "exe_" + hashlib.sha256(canonical_json({
            "task": self.task_origin(run), "name": request.name})).hexdigest()
        if request.action in {"status", "cancel"}:
            from .executions import ExecutionNotFound
            try:
                self.executions.status(execution_id)
            except ExecutionNotFound:
                return {"name": request.name, "state": "not_created", "submission": "not_submitted"}
            if self._execution_owner(execution_id) != self.task_origin(run):
                raise ToolInputError("experiment_execution_owner_mismatch", "This execution does not belong to the current task.")
            if request.action == "cancel":
                self._cancel(execution_id)
            else:
                self.bridge.submissions.refresh(execution_id, scope="instance:" + run.instance_id)
                self._reconcile_authorization(execution_id)
                approval = self._approval_status(execution_id)
                status = self.executions.status(execution_id)
                if approval is not None and status.state in {"created", "abandoned"}:
                    self._bind_approval(run, request.name, execution_id)
                    return self._authorization_response(run, request.name, execution_id, approval)
                if status.external_run_id is not None and status.state in {"submitted", "running", "cancelling"}:
                    self.bridge.sync(execution_id=execution_id, diagnostic_scope="instance:" + run.instance_id)
            implementation = None
            if request.action == "status" and self.executions.status(execution_id).state == "collected":
                try:
                    implementation = self._execution_implementation_record(run, execution_id)
                except DiagnosticError:
                    pass  # State remains readable even when scientific originals need rebinding.
            return self._execution_status(context, request, execution_id, implementation, seal=request.action == 'status')
        if request.action in {"capabilities", "review", "start", "advance"}:
            run, effect, identity, _ = self._contract(context)
            plan = effect_operation_plan(effect)
        if request.action == "capabilities":
            return {"methods": [{"method": item.key,
                **{k: v for k, v in item.public_summary.items()
                   if k in {"solver_kind", "release", "description", "limitations", "capabilities"}}}
                for item in self.capabilities(context)]}
        if request.action == "review":
            implementation = self._selected_implementation(context, request.implementation)
            result = {"implementation": request.implementation,
                "review": self.review_handoff(run, implementation)}
            review = self._implementation_review(run, implementation)
            if review is not None:
                ref, raw, assessment = review
                # Deliver unchanged reviewer bytes through the existing immutable,
                # bounded material channel. This copy never grants review authority:
                # execution admission continues to check the original exact review.
                record = context.accept_evidence(raw=raw, media_type="application/json",
                    derived_from=(request.implementation,), trusted_parent_refs=(ref,),
                    metadata={"kind": "experiment_review", "review_ref": ref.model_dump(mode="json")})
                result["review_result"] = {"state": "completed", "verdict": assessment.verdict,
                    "reference": record["alias"], "read_request": {"action": "read",
                        "source": record["alias"], "reference": "content"}}
            else:
                result["review_result"] = {"state": "not_available"}
            return result
        if request.action == "start":
            if request.implementation is None:
                raise ToolInputError("experiment_implementation_required", "Choose a sealed implementation before starting execution.", path="$.implementation")
            implementation = self._selected_implementation(context, request.implementation)
            payload = self._implementation_payload(implementation)
            reviewed = self._required_review(run, implementation)
            from .executions import ExecutionNotFound
            try:
                self.executions.status(execution_id)
            except ExecutionNotFound:
                existing = False
            else:
                existing = True
                original = self._execution_implementation_record(run, execution_id)
                if original["artifact_ref"] != implementation["artifact_ref"]:
                    raise ToolInputError("experiment_execution_implementation_changed",
                        "This execution name is bound to another exact implementation. Use status for the original execution; "
                        "use a new name only for an intentionally new execution.", path="$.implementation")
            if not existing:
                self.executions.create(executor=plan.executor, preparation_profile=plan.preparation_profile,
                    payload_ref=payload, execution_id=execution_id, compiled_identity=identity,
                    labels={"operation_id": identity.operation_id, "operation_version": identity.operation_version,
                        "operation_digest": identity.operation_digest, "approval_contract_digest": identity.approval_contract_digest,
                        "experiment_run": self.task_origin(run),
                        "implementation_receipt": canonical_json(implementation["artifact_ref"]).decode("utf-8"),
                        **({"implementation_review": reviewed.model_dump_json()} if reviewed is not None else {})})
            self.runs.scheduler_bindings.bind(instance=run.instance_id, namespace="execution",
                name="experiment-" + self.task_origin(run) + "-" + request.name, object_id=execution_id)
        from .executions import ExecutionNotFound
        try:
            current = self.executions.request(execution_id)
        except ExecutionNotFound as error:
            raise ToolInputError("experiment_execution_not_created",
                "No execution exists under this name. Use status with the original name; start only after confirming no earlier submission.", path="$.name") from error
        if self._execution_owner(execution_id) != self.task_origin(run):
            raise ToolInputError("experiment_execution_owner_mismatch", "This execution does not belong to the current task.")
        if request.action in {"start", "advance"} and current.compiled_identity != identity:
            raise ToolInputError("experiment_execution_contract_changed", "Execution configuration changed; the existing execution cannot be restarted.", path="$.action")
        if request.action == "logs":
            if request.offset:
                from .execution_log_reader import invalid
                raise invalid("For logs use direction and its returned cursor; offset is only for collected output read.")
            return self.executions.cached_log(execution_id, log_index=request.log_index,
                direction=request.direction, cursor=request.cursor, max_bytes=self._io_settings(context).read_page_bytes)
        if request.action == "collect":
            # Transport is bound to the immutable execution request. A missing
            # scientific receipt must not strand already-computed remote files.
            if self.collection is None:
                raise ValueError("The controlled collection service is unavailable.")
            self.collection.collect(execution_id, scope="instance:" + run.instance_id,
                total_seconds=min(context.remaining_seconds, self._io_settings(context).collection_timeout_seconds),
                file_timeout_seconds=self._io_settings(context).file_timeout_seconds,
                idle_timeout_seconds=self._io_settings(context).idle_timeout_seconds)
            details = ()
            try:
                implementation = self._execution_implementation_record(run, execution_id)
            except DiagnosticError as error:
                if not error.details or any(item['code'] not in {
                        'experiment_implementation_unavailable', 'experiment_implementation_ambiguous'} for item in error.details):
                    raise
                implementation, details = None, error.details
                self.runs.record_error_observation(run.run_id, 'tool_failed', diagnostic={
                    'category':'tool_failed', 'tool_name':'worker_experiment_execute', 'details':details})
            result = self._execution_status(context, request, execution_id, implementation, seal=True)
            if details:
                result['delivery_diagnostics'] = list(details)
            return result
        implementation = self._execution_implementation_record(run, execution_id)
        private_outputs = implementation["metadata"].get("private_outputs", ())
        if request.action in {"read", "export"}:
            if self.executions.status(execution_id).state != 'collected':
                raise ToolInputError('experiment_collection_pending',
                    'Outputs are not collected yet. Read status for this same execution and wait for collection state completed; do not repeat the solver.', path='$.action')
            outputs = [item for item in self.executions.outputs(execution_id) if item.logical_name not in private_outputs]
            names, _ = self._output_file_names(context, execution_id, implementation['metadata'].get('output_inventory_name'))
            matches = [item for item in outputs if request.output_name is not None
                and request.output_name in {item.logical_name, names.get(item.logical_name)}]
            if len(matches) != 1:
                raise ToolInputError("experiment_output_unknown", "Select an exact name or file_name returned by status for this collected execution."
                    + ' Available examples: ' + repr([names.get(item.logical_name, item.logical_name) for item in outputs[:6]])
                    + (f' ({len(outputs)-6} more; read status).' if len(outputs)>6 else '.'), path="$.output_name")
            item = matches[0]
            envelope = self.executions.artifacts.get_by_id(item.artifact_id)
            if request.action == "export":
                return self._export(context, request.model_copy(update={'output_name': item.logical_name}), envelope)
            if not (item.media_type.startswith("text/") or item.media_type == "application/json"):
                return {"name": item.logical_name, "media_type": item.media_type, "size_bytes": item.size_bytes,
                    "availability": "retained_binary", "instruction": "Use export to obtain the exact file for local domain analysis."}
            with self.executions.artifacts.open_original(envelope.ref) as stream:
                stream.seek(request.offset)
                page_bytes = self._io_settings(context).read_page_bytes
                raw = stream.read(page_bytes + 1)
            return {"name": item.logical_name, "text": raw[:page_bytes].decode("utf-8", errors="replace"),
                "next_offset": request.offset + page_bytes if len(raw) > page_bytes else None}
        if request.action in {"start", "advance"}:
            approval = self._approval_status(execution_id)
            if approval is not None:
                self._bind_approval(run, request.name, execution_id)
                response = self._authorization_response(run, request.name, execution_id, approval)
                if self.executions.status(execution_id).state in {"created", "abandoned"} and (
                        response["state"] not in {"created", "awaiting_authorization"}):
                    return response
        if request.action in {"start", "advance"}:
            if self.executions.status(execution_id).external_run_id is None:
                self._required_review(run, implementation)
            try:
                self._start_request(execution_id, effect, identity, approval)
            except ExecutionAuthorizationRequired:
                self._bind_approval(run, request.name, execution_id)
                return self._authorization_response(run, request.name, execution_id, self._approval_status(execution_id))
            if request.action == "advance" and self.executions.status(execution_id).external_run_id is not None:
                self.bridge.sync(execution_id=execution_id, diagnostic_scope="instance:" + run.instance_id)
        return self._execution_status(context, request, execution_id, implementation, seal=True)

    def _execution_status(self, context, request, execution_id, implementation, *, seal):
        status = self.executions.status(execution_id)
        result = {"name": request.name, "state": status.state,
            "submission": self.bridge.submissions.summary(execution_id)}
        observation = self.executions.observation(execution_id)
        result["observation"] = {
            "state": "refresh_failed" if observation.get("observation_error") else
                "recorded" if observation.get("status_observed_at") else "unobserved",
            "observed_at": observation.get("status_observed_at"),
            "progress_observed_at": observation.get("progress_observed_at"),
        }
        if isinstance(observation.get("progress"), dict):
            from ...plugin_runtime.experiment import progress_summary
            result["progress"] = progress_summary(observation["progress"])
        if status.result_ref is not None:
            manifest = ExecutionResultManifest.model_validate_json(self.executions.artifacts.read(status.result_ref), strict=True)
            result["terminal_state"] = manifest.terminal_state
            if implementation is None:
                result["outputs_status"] = "Collected files are retained, but their exact implementation receipt is unavailable. Root must inspect the binding; once work is idle, submit blocked/inconclusive with limitations. Do not rerun the solver."
                return result
            private_outputs = implementation["metadata"].get("private_outputs", ())
            names, inventory_note = self._output_file_names(context, execution_id,
                implementation["metadata"].get("output_inventory_name"))
            if inventory_note:
                result["output_inventory_note"] = inventory_note
            result["outputs"] = [{"name": item.logical_name, "media_type": item.media_type,
                **({"file_name": names[item.logical_name]} if item.logical_name in names else {}),
                "size_bytes": item.size_bytes} for item in self.executions.outputs(execution_id)
                if item.logical_name not in private_outputs]
            # A control observation is sealed without exposing control identities.
            if seal:
                # Operational progress is mutable; the scientific receipt stays stable.
                sealed = {key: value for key, value in result.items() if key not in {"progress", "observation", "submission"}}
                record = context.accept_evidence(raw=canonical_json(sealed), media_type="application/json",
                    derived_from=("research_objective", implementation["alias"]), trusted_parent_refs=(status.result_ref,),
                    metadata={"kind": "experiment_execution", "name": request.name, "implementation": implementation["alias"]})
                result["reference"] = record["alias"]
                result["artifact_name"] = record["artifact_name"]
        elif status.state in {'succeeded', 'failed', 'cancelled'}:
            summary = getattr(self.collection, 'summary', None)
            collection = summary(execution_id) if callable(summary) else {}
            from .engineering_diagnostics import diagnostic_locator
            result['collection'] = {'state': collection.get('state', 'unknown')}
            if diagnostic := diagnostic_locator(collection.get('error')):
                result['collection']['diagnostic'] = diagnostic
            result['outputs_status'] = 'not_collected'
            result['next_action'] = {'tool': 'worker_experiment_execute', 'arguments': {'action': 'status', 'name': request.name}}
            result['instruction'] = 'Solver termination is not collection completion. After collect is accepted, poll status until state=collected; then use its reference and exact output names. Never resubmit the solver to recover collection.'
        return result

    def _output_file_names(self, context, execution_id, inventory_name):
        """Project exact solver-relative filenames, never transport paths or guesses."""
        import json
        from pathlib import PurePosixPath
        from ...agent_execution_settings import MaterialInputSettings
        if inventory_name is None:
            return {}, None
        # A status query must remain readable after deployment changes. This
        # field was frozen with the implementation; do not recompile its owner.
        run = self.runs.status(context.run_id)
        outputs = {item.logical_name: self.executions.artifacts.get_by_id(item.artifact_id)
            for item in self.executions.outputs(execution_id)}
        inventory = outputs.get(inventory_name)
        if inventory is None:
            return {}, "Original output filenames are unavailable: the collection has no declared inventory. Retain this limitation; do not infer case or time from opaque labels."
        limits = MaterialInputSettings.model_validate((run.recovery_policy or {}).get("input_materials", {}))
        if inventory.size_bytes > limits.inline_max_bytes:
            return {}, "The output inventory exceeds the configured inline read limit. Output labels remain usable, but original filenames require bounded analysis of the collected inventory."
        try:
            document = json.loads(self.executions.artifacts.read(inventory.ref))
            names = {}
            for item in document['outputs']:
                path = item['relative_path']
                name = item['name']
                original = outputs[name]
                if (not isinstance(path, str) or not path or '\\' in path
                        or PurePosixPath(path).is_absolute() or '..' in path.split('/')
                        or path == '.' or PurePosixPath(path).as_posix() != path
                        or any(ord(char) < 32 for char in path) or name in names
                        or (original.ref.sha256, original.size_bytes, original.media_type) !=
                            (item['sha256'], item['size_bytes'], item['media_type'])):
                    raise ValueError('inventory differs from exact collected files')
                names[name] = path
            return names, None
        except (KeyError, TypeError, ValueError):
            # A broken display mapping cannot make a terminal lifecycle unreadable.
            # Keep the exact output identities and withhold every unverified name.
            return {}, "The collected output inventory does not match its exact files; original filenames are withheld. Preserve the collected execution and ask for collection repair; do not resubmit the solver."

    def recover_start(self, execution_id, *, instance_id):
        """Recover the original request after all scientific owners have closed."""
        from .executions import ExecutionServiceError
        owner = self._execution_owner(execution_id)
        if owner is None or not self.runs.task_recovery_available(owner, instance_id=instance_id):
            raise ExecutionServiceError("Execution recovery requires a closed failed or limited task lineage")
        run = self.runs.status(owner)
        state = self.executions.status(execution_id)
        if state.external_run_id is not None:
            return  # Already submitted: recovery must not create another solver.
        if not self.runs.task_recovery_available(owner, instance_id=instance_id, for_start=True):
            raise ExecutionServiceError("A completed limited task permits result recovery only; a new execution requires a new scientific task.")
        if state.state not in {"created", "authorized"}:
            raise ExecutionServiceError("This execution cannot be started; inspect its terminal state")
        _, effect, identity, _ = self._contract_for_run(run)
        request = self.executions.request(execution_id)
        plan = effect_operation_plan(effect)
        if (request.compiled_identity != identity or request.executor != plan.executor
                or request.preparation_profile != plan.preparation_profile):
            raise ExecutionServiceError("Execution recovery contract differs from the original request")
        implementation = self._execution_implementation_record(run, execution_id)
        self._required_review(run, implementation)
        approval = self._approval_status(execution_id)
        if approval is not None and (approval.status != "decided" or self._authorization_state(approval) != "created"):
            raise ExecutionServiceError("The original execution approval has not authorized this request")
        try:
            self._start_request(execution_id, effect, identity, approval)
        except ExecutionAuthorizationRequired:
            pass  # Background handoff already published the exact approval.

    def _start_request(self, execution_id, effect, identity, approval):
        # Worker continuation and explicit Root recovery have identical authorization
        # and scientific budget ownership; neither uses standalone Effect bindings.
        run = self.runs.status(self._execution_owner(execution_id))
        from ...agent_execution_settings import ExecutionIOSettings
        io = ExecutionIOSettings.model_validate((run.recovery_policy or {}).get("execution_io", {}))
        def request_approval():
            self._approval(execution_id, effect, identity)
            # Register from the creator, even when the caller already received
            # pending and never polls the Worker again.
            self.runs.record_approval_wait(run.run_id, "apr_" + execution_id)
            name = self.runs.scheduler_bindings.find_name(instance=run.instance_id,
                namespace="execution", object_id=execution_id)
            if name is not None:
                self._bind_approval(run, name.removeprefix("experiment-" + self.task_origin(run) + "-"), execution_id)
        self.bridge.submissions.start(execution_id=execution_id,
            scope="instance:" + run.instance_id,
            wait_seconds=io.submission_response_wait_seconds,
            on_authorization_required=request_approval,
            approval_id="apr_" + execution_id if approval is not None and approval.status == "decided" else None,
            compiled_identity=identity,
            allow_policy_authorization=effect.spec.review.approval.allow_policy_authorization,
            budget_subject_schemas=("scidiscovery.research-objective.v1",))

    def recovery_actions(self, execution_id):
        state = self.executions.status(execution_id)
        actions = ["execution_status", "execution_sync", "execution_logs", "execution_collect", "execution_outputs"]
        if state.state in {"created", "authorized"} and state.external_run_id is None:
            actions.append("execution_abandon")
            approval = self._approval_status(execution_id)
            owner = self._execution_owner(execution_id)
            run = self.runs.status(owner) if owner is not None else None
            if (run is not None and self.runs.task_recovery_available(owner, instance_id=run.instance_id, for_start=True)
                    and (approval is None or (approval.status == "decided" and self._authorization_state(approval) == "created"))):
                actions.append("execution_start")
        elif state.state in {"submitted", "running", "cancelling"}:
            actions.append("execution_cancel")
        return actions

    def _approval_status(self, execution_id):
        from .approvals import ApprovalAccessDenied
        if self.executions.approvals is not None:
            try:
                return self.executions.approvals.status("apr_" + execution_id)
            except ApprovalAccessDenied:
                pass
        return None

    def _bind_approval(self, run, name, execution_id):
        bindings = self.runs.scheduler_bindings
        approval_id = "apr_" + execution_id
        if bindings.find_name(instance=run.instance_id, namespace="approval", object_id=approval_id) is None:
            task = bindings.find_name(instance=run.instance_id, namespace="run", object_id=self.task_origin(run))
            if task is None:
                raise ValueError("Execution approval requires its owning task's instance name.")
            semantic_name = task + ".execution." + name + ".approval"
            if len(semantic_name) > 256:
                semantic_name = semantic_name[:231] + "." + hashlib.sha256(semantic_name.encode()).hexdigest()[:24]
            bindings.bind(instance=run.instance_id, namespace="approval", name=semantic_name,
                object_id=approval_id)

    def _authorization_response(self, run, name, execution_id, approval):
        if approval.status == "pending":
            self.runs.record_approval_wait(run.run_id, "apr_" + execution_id)
        state = self._authorization_state(approval)
        self._reconcile_authorization(execution_id, approval)
        authorization = self.executions.authorization(execution_id)
        from datetime import datetime, timezone
        current = self.runs.status(run.run_id)
        remaining = max(0, int((datetime.fromisoformat(current.deadline_at.replace('Z', '+00:00')) - datetime.now(timezone.utc)).total_seconds()))
        result = {"name": name, "state": state,
            "task_budget": {"deadline_at": current.deadline_at, "remaining_seconds": remaining,
                "instruction": "Use this current task budget or worker_heartbeat, not the assignment's initial deadline, while awaiting approval."},
            "execution_state": self.executions.status(execution_id).state, "authorization": {"status": approval.status,
            **{key: authorization[key] for key in ("reason", "budget", "allowance", "per_execution_allowance", "budget_usage", "accounting") if key in authorization}}}
        bound = self.runs.scheduler_bindings.find_name(instance=run.instance_id, namespace="approval", object_id="apr_" + execution_id)
        if bound is not None:
            result["approval_name"] = bound
        if approval.status == "pending" and approval.review_path and self.approval_base_url:
            result["review_url"] = self.approval_base_url.rstrip("/") + approval.review_path
        return result

    def _authorization_state(self, approval):
        state = {"pending": "awaiting_authorization", "expired": "authorization_expired",
            "cancelled_by_human": "authorization_cancelled"}.get(approval.status, "created")
        if approval.status == "decided":
            stored = ApprovalRequest.model_validate_json(self.executions.artifacts.read(approval.approval_request_ref), strict=True)
            decision = HumanDecision.model_validate_json(self.executions.artifacts.read(approval.decision_ref), strict=True)
            if not execution_decision_authorizes(stored, decision):
                state = "authorization_rejected"
        return state

    def _reconcile_authorization(self, execution_id, approval=None):
        approval = approval or self._approval_status(execution_id)
        if approval is None or self._authorization_state(approval) not in {
                "authorization_expired", "authorization_cancelled", "authorization_rejected"}:
            return
        status = self.executions.status(execution_id)
        # No prepared submission means there is no uncertain external side effect.
        # Prepared/started requests require explicit status/cancel reconciliation.
        if (status.state == "created" and status.external_run_id is None
                and self.executions.prepared_submission(execution_id) is None):
            self.executions.abandon(execution_id)

    def _approval(self, execution_id, effect, identity):
        if self.executions.approvals is None or self.approval_base_url is None:
            raise ValueError("This execution needs the configured approval service; no execution has been submitted.")
        contract = effect.spec.review.approval
        refs = self.executions.approval_subject_refs(execution_id)
        existing = self._approval_status(execution_id)
        if existing is not None:
            stored = ApprovalRequest.model_validate_json(self.executions.artifacts.read(existing.approval_request_ref), strict=True)
            if stored.kind != "execution_authorization" or stored.subject_refs != refs or stored.compiled_identity != identity:
                raise ValueError("Execution approval does not bind the exact request and contract.")
            if existing.status == "pending":
                self.executions.approvals.refresh_access("apr_" + execution_id)
            return
        snapshots = []
        for index, (port, ref) in enumerate(zip(contract.subject_ports, refs, strict=True)):
            envelope = self.executions.artifacts.verify(ref)
            snapshots.append(ApprovalSubjectSnapshot(index, port, 0, ref, envelope.schema_id,
                envelope.media_type, envelope.size_bytes, envelope.parent_refs,
                tuple(envelope.labels.items()), None, self.executions.artifacts.read(ref)))
        projector = contract.projector
        document = effect.implementations[f"{projector.plugin_id or effect.plugin_id}:{projector.component_id}"](
            ApprovalProjectorContext(effect.spec.operation_id, effect.spec.version, effect.digest, tuple(snapshots)))
        self.executions.approvals.create_request(approval_id="apr_" + execution_id,
            kind="execution_authorization", subject_refs=refs, question=contract.question,
            options=tuple(ApprovalOption(option_id=execution_approval_option_id(item.decision),
                label=item.label, description=item.description or "Record the declared execution authorization decision.",
                requires_rationale=item.requires_reason) for item in contract.options),
            requested_by=self.executions.service_actor, idempotency_key="execution-approval:" + execution_id,
            review_document=document, compiled_identity=identity)


class _TaskExperimentTools:
    """Control adapter bound by the tool host; a template never mutates its scope."""
    __slots__ = ("__coordinator", "__operation_id", "__run_id")

    def __init__(self, coordinator, *, operation_id, run_id=None):
        self.__coordinator = coordinator
        self.__operation_id = operation_id
        self.__run_id = run_id

    def _for_run(self, run_id):
        if self.__run_id is not None and self.__run_id != run_id:
            raise ValueError("Experiment capability cannot be rebound to another Run.")
        return self if self.__run_id is not None else _TaskExperimentTools(
            self.__coordinator, operation_id=self.__operation_id, run_id=run_id)

    def __scope(self, context):
        run = self.__coordinator.runs.running_task(context.run_id)
        if run.operation_id != self.__operation_id:
            raise ValueError("Experiment capability belongs to another operation.")
        if run.run_id != self.__run_id:
            raise ValueError("Experiment capability belongs to another Run.")
        return run

    def capabilities(self, context):
        self.__scope(context)
        return self.__coordinator.capabilities(context)

    def seal_implementation(self, context, *, payload, scientific_material, sources, private_outputs=()):
        self.__scope(context)
        return self.__coordinator.seal_implementation(context, payload=payload,
            scientific_material=scientific_material, sources=sources, private_outputs=private_outputs)

    def read_implementation(self, context, alias):
        from ...plugin_runtime.experiment import SealedImplementation
        self.__scope(context)
        ref = self.__coordinator.implementation(context, alias)
        return SealedImplementation(ref, self.__coordinator.executions.artifacts.read(ref))

    def source_alias(self, context, ref):
        from .sealed_materials import material_error
        run = self.__scope(context)
        alias = self.__coordinator.runs.material_source_aliases(run).get(ref)
        if alias is None:
            raise material_error("experiment_source_not_bound",
                "Bind the exact original scientific file required by this sealed implementation.",
                path="$.implementation")
        return alias

    def diagnostic_context(self, context, *, service_name):
        from dataclasses import replace
        run = self.__scope(context)
        service = context.require_service(service_name)
        context.state['diagnostic_recovery_route'] = lambda owner, name: self.__coordinator.diagnostic_recovery_route(run, owner, name)
        private = service.exchange_root / "experiment-workspaces" / self.__coordinator.task_origin(run)
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        return replace(context, workspace=private, output_directory=private / "output")

    def publish_diagnostic_files(self, context, *, implementation, name, workspace, outputs):
        self.__scope(context)
        return self.__coordinator.publish_diagnostic_files(context, implementation=implementation,
            name=name, workspace=workspace, outputs=outputs)

    def cancel_diagnostics(self, context, *, service_name, name):
        run = self.__scope(context)
        service = context.require_service(service_name)
        ancestors = self.__coordinator.task_lineage(run)
        lineage = {item.run_id for item in ancestors}
        inputs = tuple(binding for item in ancestors for binding in item.inputs)
        return bool(service.experiment_activity(lineage, inputs, cancel=True, name=name))

    def command(self, context, request):
        self.__scope(context)
        return self.__coordinator.command(context, request)


def scope_experiment_services(services, *, run_id):
    """Scope only the compiled tool's selected services, without mutating cached bindings."""
    return {key: value._for_run(run_id) if isinstance(value, _TaskExperimentTools) else value
            for key, value in services.items()}


def bind_experiment_services(runs, compiled, services, *, coordinator=None, run_id=None):
    """Shared tool-service binding for unified and standalone Worker hosts."""
    if compiled.spec.executor.capability is None:
        return services
    coordinator = coordinator or getattr(runs, "experiment_executions", None)
    if coordinator is None:
        raise ValueError("The experiment execution service is not configured.")
    coordinator.worker_services.update(services)
    capability = _TaskExperimentTools(coordinator, operation_id=compiled.spec.operation_id, run_id=run_id)
    for tool in compiled.worker_tools:
        for key in (*tool.required_services, *tool.optional_services):
            if key.partition(":")[2] == "experiment.execution":
                services[key] = capability
    return services


def open_experiment_services(runs, catalog, *, state_root, plugin_configs,
                             approval_secret_file=None, approval_base_url=None):
    from ..runtime_plugin_bindings import load_runtime_plugin_contributions
    from ..execution_bridge import ExecutionBridge
    from .execution_collection import ExecutionCollection, open_collection_executions
    executions = open_collection_executions(state_root)
    if approval_secret_file is not None:
        from ..runtime import read_secret_file
        from .approvals import ApprovalService
        executions.approvals = ApprovalService(artifacts=executions.artifacts,
            database_path=state_root / "database/approvals.sqlite3", service_actor=executions.service_actor,
            receipt_secret=read_secret_file(approval_secret_file, label="approval receipt"))
    adapters = load_runtime_plugin_contributions(catalog, plugin_configs,
        mode="control", state_root=state_root).execution_adapters
    coordinator = ExperimentExecution(runs=runs, executions=executions,
        bridge=ExecutionBridge(executions, adapters=adapters, lifecycle_settings=runs.agent_settings.lifecycle),
        collection=ExecutionCollection(executions, plugin_configs={key: str(path) for key, path in plugin_configs.items()}),
        approval_base_url=approval_base_url)
    from ..worker_services import load_operation_services
    runs.experiment_executions = coordinator
    coordinator.services_for = lambda operation_id: load_operation_services(catalog, operation_id, plugin_configs, state_root)
    return coordinator
