"""Cov public experiment implementation of the registered lifecycle contract."""
from contextlib import ExitStack
import json
import os
import sys
from pathlib import Path
import time

from scidiscovery.operations.execution import AdapterCapability, ExecutionInterfaceRegistration, LocalFileDescriptor
from scidiscovery.operations.runtime_plugins import RuntimePluginContribution, RuntimePluginFactory
from scidiscovery.research_loop.local_experiments import LocalExecution
from scidiscovery.research_loop import experiments as e
from . import lineage as cov, receipt as cov_receipt


class CovExecution(LocalExecution):
    def __init__(self, config):
        super().__init__(config)
        from scidiscovery.research_loop import replay_check
        replay_check.reserve_seconds(config)

    def capabilities(self):
        original = tuple(item for item in super().capabilities() if item.key != e.INPUT_SOURCES_KEY)
        command = dict(version=1, invocation='run --script <single source file>',
            accepted_source='exact accepted source original', experiment_directory=False, script_arguments=False,
            dependencies='Declared public harness inputs and installed runtime only; sibling modules and arbitrary local dependencies are not retained.',
            candidate_options=['--parent-plan', '--new-plan', '--change-intent', '--mechanism'])
        original = (*original, AdapterCapability('cov_single_source', 'execution', 'scidiscovery.cov-single-source.v1', 1,
            'application/json', e.encoded(command), command))
        if self.config.get('receipt_channel') != 'public_runner_main':
            return original
        from scidiscovery.research_loop import replay_check
        policy = dict(max_seconds=10, feedback_seconds=2, supports_time_reservation=True,
            check_scope=dict(source='exact accepted source original',
                inputs='exact accepted public runner/train.csv/val.csv/meta.json and task runtime',
                selection='first rows in frozen CSV order', train_rows_max=32, validation_rows_max=8,
                coverage='source import, one sampled train() call and sampled predict; installed runtime dependencies only',
                limitation='Unexecuted paths, full-data training and public audit are untested. Installed libraries are shared, not individually frozen. Pre-acceptance input preparation costs belong to the original task, outside this diagnostic ceiling; no scientific qualification.'))
        summary = dict(policy['check_scope'], total_call_seconds=self.config['max_seconds'],
            requested_reserve_seconds=replay_check.reserve_seconds(self.config), feedback_seconds=policy['feedback_seconds'],
            time_rule='Positive reserve includes check and feedback and shortens the experiment before dispatch; zero is best effort. Accepted originals retain their frozen deadlines.')
        return (*original, AdapterCapability('isolated_replay', 'engineering', 'scidiscovery.engineering-replay.v1', 1,
            'application/json', e.encoded(policy), summary))

    def _execution_deadline(self, accepted):
        bound = accepted.get('engineering_check') or {}
        allocation = bound.get('time_allocation') or {}
        if allocation.get('status') != 'reserved':
            return super()._execution_deadline(accepted)
        from scidiscovery.research_loop import budget
        monotonic = time.monotonic(); stamp = budget.clock_stamp()
        remaining = allocation['experiment_deadline_boottime'] - stamp['at_boottime']
        if bound['boot_id'] != stamp['boot_id'] or remaining <= 0:
            raise TimeoutError('Original reserved experiment window unavailable; no new time grant')
        return monotonic + remaining

    def _replay_inputs(self, transport):
        """Bind only the known public harness originals before the original call."""
        from scidiscovery.research_loop import protocol
        parent = Path(transport['runner']['path']).parent
        files = []
        for name in ('runner.py', 'train.csv', 'val.csv', 'meta.json'):
            path = parent / name
            environment = {'runner.py':'EXP_RUNNER', 'train.csv':'EXP_TRAIN',
                'val.csv':'EXP_VAL', 'meta.json':'EXP_META'}[name]
            if Path(os.environ.get(environment, str(path))).absolute() != path:
                raise ValueError('Public replay input mapping is not the declared harness layout')
            item = e.proof(path)
            if name == 'runner.py' and any(item[key] != transport['runner'][key] for key in ('path','size_bytes')):
                raise e.IntegrityError('Public replay runner differs from accepted receipt producer')
            if name != 'runner.py':
                files.append(dict(item, name='replay/harness/' + name))
        # Optional engineering inputs must not erase the science data binding.
        try:files.append(dict(e.proof(Path(protocol.__file__).resolve()),name='replay/scid-current-protocol'))
        except (OSError,ValueError):pass
        runtime=dict(inputs=files)
        invoked = Path(self.config['run_argv'][0]).absolute()
        try:runtime.update(e.proof(invoked.resolve()),invoked_path=str(invoked))
        except (OSError,ValueError):pass
        return [], runtime

    def acceptance_metadata(self,payload,*,parent=None,new=False,change=None):
        channel=self.config.get('receipt_channel')
        if self.config.get('worker_view_version')==1 and channel!='public_runner_main':
            raise e.UnsupportedExecution('Retained receipt channel has no qualified scientific execution namespace')
        if channel not in {'public_runner_main','backend_file'}:
            raise e.UnsupportedExecution('Cov execution needs an explicit independent receipt channel; preserved calls remain read-only')
        selected,binding=cov._parent(self.config,parent,new)
        transport = cov_receipt.profile(self.config['run_argv'],channel)
        metadata = dict(parent=selected,parent_binding=binding,change_intent=change,runs_root=self.config['runs_root'],
            evaluation_key=self.config['evaluation_key'],source_script=payload.local_path,
            command=[*self.config['run_argv'],'run','--script',payload.local_path],
            script_index=len(self.config['run_argv'])+2,cwd=None,
            receipt_transport=transport, config_record=self.config, snapshot_files=[])
        if channel == 'public_runner_main':
            try:
                metadata['snapshot_files'], metadata['replay_runtime'] = self._replay_inputs(transport)
                runtime=metadata['replay_runtime']
                if (not {'path','size_bytes','file_stat','invoked_path'}<=set(runtime)
                        or len(runtime['inputs'])!=4):
                    metadata['replay_inputs_unavailable']=True
            except (OSError, ValueError):
                metadata['replay_inputs_unavailable'] = True
        return metadata

    @e.record_effect
    def replay_check(self, call_id, *, deadline_boottime, on_process):
        """No new time grant: preparation and execution share one absolute bound."""
        from scidiscovery.research_loop import contracts as c, delivery, storage_policy as storage
        from . import delivery as cov_delivery
        directory = self.records.directory(call_id)
        accepted = self.records.accepted(call_id)
        if accepted.get('replay_inputs_unavailable') or 'replay_runtime' not in accepted:
            return dict(status='unsupported', note='exact_public_replay_inputs_unavailable', cleanup_confirmed=True)

        def check():
            if time.clock_gettime(time.CLOCK_BOOTTIME) >= deadline_boottime:
                raise TimeoutError('Original engineering preparation deadline reached')

        def reference(item):
            check()
            bound = {key:item[key] for key in ('path','size_bytes','file_stat') if key in item}
            actual=e.proof(bound['path'])
            if any(actual.get(key)!=value for key,value in bound.items()):
                raise e.IntegrityError('Accepted replay original changed')
            check()
            return actual

        code_root = None

        def cleanup_export():
            if code_root is None:return
            from scidiscovery.research_loop import code_versions
            try:
                code_versions.cleanup_export(code_root, step=accepted['scope']['step'], call_id=call_id,
                    context=storage.Deadline(65))
            except Exception as error:
                e._diagnostic(error, 'Replay code export cleanup remains unconfirmed', config=self.config)

        def mountpoint(original, destination):
            check()
            if e.proof(original['path'])!=original:
                raise e.IntegrityError('Accepted replay original changed before its view')
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.touch(mode=0o400,exist_ok=False)
            check()

        def wire(original):
            return {key:original[key] for key in ('path','size_bytes')}

        try:
            check()
            # complete() already verified the original observation and these
            # small bindings; do not hash every model/log a second time here.
            completed = json.loads(e.read(directory / 'completed.json'))
            if cov.output_unavailable(completed):
                return dict(status='unsupported',note='cov_output_unavailable',cleanup_confirmed=True)
            runtime = accepted['replay_runtime']
            reference(runtime)
            if Path(runtime['invoked_path']).resolve() != Path(runtime['path']):
                raise e.IntegrityError('Accepted replay runtime invocation changed')
            operation = call_id
            spec = dict(adapter=str(Path(cov_delivery.__file__).resolve()),
                launcher_argv=[runtime['invoked_path'], str(Path(cov_delivery.__file__).resolve()), '--isolate'])
            stage = directory / 'engineering-replay'
            stage.mkdir()
            (stage / 'scratch').mkdir()
            if 'code_version' in accepted:
                from scidiscovery.research_loop import code_versions
                root = e._code_version_binding(self.config, accepted)
                check()
                preparation = storage.Deadline(deadline_boottime - time.clock_gettime(time.CLOCK_BOOTTIME))
                try:
                    code_versions.verify(root, step=accepted['scope']['step'], call_id=call_id,
                        code_version=accepted['code_version'], context=preparation)
                    tree = code_versions.materialize(root, step=accepted['scope']['step'], call_id=call_id,
                        code_version=accepted['code_version'], context=preparation)
                    code_root = root
                except TimeoutError:
                    raise
                except (OSError, ValueError) as error:
                    raise e.IntegrityError('Accepted replay code version changed or is unavailable') from error
                source = e.proof(tree / accepted['code_version']['entry'], context=preparation)
                check()
            else:
                source = reference(accepted['script'])
            # Reuse the existing delivery view and wire fields. Pin and ordinary
            # fingerprints are consumed here, before the isolated leaf sees it.
            mountpoint(source,stage/'inputs/source.py')
            objects = {'source.py': wire(source)}
            inputs = [dict(wire(runtime),invoked_path=runtime['invoked_path'],label='runtime-python')]
            for item in _replay_roles(accepted):
                label = item['name'].removeprefix('replay/')
                current=isinstance(accepted.get('replay_runtime'),dict) and 'inputs' in accepted['replay_runtime']
                if current:original=reference(item)
                else:
                    # Legacy metadata names the public source; HEAD consumed an
                    # independent frozen call slot, whose contract stores size.
                    original=reference(dict(path=str(directory/'inputs'/item['name']),size_bytes=item['size_bytes']))
                mountpoint(original,stage/'inputs'/label)
                objects[label] = wire(original)
                inputs.append(dict(wire(original),name=item['name'],label=label))
            request = dict(phase='replay', scope=accepted['scope'], operation_id=operation,
                candidate_id=completed['candidate_id'], artifact_ref=wire(completed['model']) if completed['model'] else None,
                deadline_boottime=deadline_boottime, object_refs=objects, inputs=inputs,
                config=dict(python=runtime['invoked_path']), stage=str(stage))
            check()
        except TimeoutError:
            cleanup_export()  # Preparation never started an external consumer.
            return dict(status='not_run', note='original_engineering_preparation_deadline_reached', cleanup_confirmed=True)
        except BaseException:
            cleanup_export()
            raise
        started = False
        def process(phase, identity):
            nonlocal started
            started = True
            on_process(phase, identity)
        try:
            value = delivery._call(spec, request, deadline_boottime - time.clock_gettime(time.CLOCK_BOOTTIME),
                on_process=process, evidence=directory / 'engineering-diagnostics')
        except delivery.DeliveryUnresolved as error:
            return dict(status='unknown', note=error.code, cleanup_confirmed=False)
        except BaseException:
            if not started:cleanup_export()
            raise
        # _call returns only after its actual leaf group is confirmed stopped.
        cleanup_export()
        # Raw diagnostics stay in the persistent call stage. A deadline never
        # licenses another complete copy/hash just to produce the small view.
        leaf_record = {}
        leaf = stage / 'scratch/replay.json'
        if time.clock_gettime(time.CLOCK_BOOTTIME) < deadline_boottime:
            try:
                if c.regular(leaf).stat().st_size <= 16_000:
                    leaf_record = json.loads(e.read(leaf))
            except (OSError, ValueError):
                pass
        diagnostics = []
        for name in ('replay.stdout', 'replay.stderr', 'replay.json'):
            path = stage / 'scratch' / name
            if path.exists():
                c.regular(path).chmod(0o400)
                diagnostics.append(e.proof(path))
        if value is None:
            return dict(status='inconclusive' if started else 'not_run', note='check_did_not_complete', cleanup_confirmed=True,
                samples=leaf_record.get('samples'), stage=leaf_record.get('stage'), diagnostic_originals=diagnostics,
                completion='Timeout or failed leaf; bounded execution did not produce a verified result.')
        if (not isinstance(value, dict) or any(value.get(key) != request[key]
                for key in ('operation_id', 'candidate_id', 'artifact_ref'))
                or value.get('status') not in {'sample_replay_ok', 'dependency_error', 'inconclusive'}):
            return dict(status='unknown', note='invalid_sample_replay_result', cleanup_confirmed=True)
        return {key: value[key] for key in ('status', 'note', 'diagnostic', 'diagnostic_omitted_bytes', 'samples', 'stage')} | dict(
            cleanup_confirmed=True, diagnostic_originals=diagnostics)

    def completion(self,directory,accepted,returncode,cleanup,transport_complete, *, context=None):
        terminal=json.loads(e.read(directory/'terminal.json'))
        if any(terminal.get(key)!=accepted.get(key) for key in ('call_id','scope','mechanism_binding')):
            raise e.IntegrityError('Cov terminal differs from its original accepted call')
        if (terminal.get('returncode')!=returncode or terminal.get('cleanup_confirmed') is not cleanup
                or terminal.get('transport_complete') is not transport_complete):
            raise e.IntegrityError('Cov completion differs from its original terminal facts')
        try:
            rows=cov._rows(Path(self.config['runs_root'])/'results.jsonl')
            cov_receipt.recover_pending(directory,accepted,terminal,rows)
            result=cov._receipt(directory,accepted,rows,returncode,cleanup,transport_complete=transport_complete,
                storage_root=self.records.storage_root(), policy=self.config.get('storage_policy'), context=context)
        except (OSError,json.JSONDecodeError,cov._LedgerShapeError):
            result=None
        if result is None:
            result=super().completion(directory,accepted,returncode,cleanup,transport_complete,context=context)
            source=[accepted['script']]
            if 'code_version' in accepted:
                root=e._code_version_binding(self.config,accepted)
                if e._code_export_entry(root,accepted) is not None:source=[]
            result.update(change_intent=accepted['change_intent'],
                proofs=[e.proof(directory/name) for name in ('accepted.json','stdout.raw','stderr.raw','terminal.json')]
                    +source,
                result=dict(status='unavailable',metrics=[],outputs=[],diagnostic='cov_receipt_unavailable'),
                returncode=returncode,wall_seconds=terminal.get('wall_seconds'),
                note='Original execution terminal retained; Cov output unavailable without an exact completed public receipt; no replay')
        return result

    def command(self,accepted):
        # exp records the source's original path. Preserve that contract and
        # verify exact prepared bytes both before execution and in its receipt.
        if 'code_version' in accepted:
            e._code_version_binding(self.config,accepted)
            if (accepted['script']['path']!=accepted['prepared']['local_path']
                    or accepted['script']['size_bytes']!=accepted['prepared']['size_bytes']):
                raise e.IntegrityError('Original cov execution source changed')
        elif any(e.proof(accepted['source_script'])[key]!=value for key,value in
                dict(path=accepted['prepared']['local_path'],size_bytes=accepted['prepared']['size_bytes']).items()):
            raise e.IntegrityError('Original cov execution source changed')
        # The task interpreter retains its scientific libraries and permissions.
        # Only cov's stdout/receipt separation changes; generic execution is shared.
        return [self.config['run_argv'][0],'-B','-I',str(Path(cov_receipt.__file__).resolve()),
            str(self.records.directory(accepted['call_id'])/'accepted.json')]

    @e.record_effect
    def result(self,call_id):
        directory=self.records.directory(call_id);accepted=self.records.accepted(call_id)
        result=cov._completed(directory,verify_source='code_version' not in accepted)
        if result is None and (directory/'terminal.json').exists():
            with ExitStack() as writer:
                try:writer.enter_context(self._execution_writer_lease(call_id))
                except BlockingIOError:
                    return dict(status='unknown',call_id=call_id,note='Original call has no completion')
                # The original writer or another recovery reader may have
                # completed since our first read. Only its saved result wins.
                result=cov._completed(directory,verify_source='code_version' not in accepted)
                if result is None:
                    terminal=json.loads(e.read(directory/'terminal.json'))
                    result=self.completion(directory,accepted,terminal.get('returncode'),
                        terminal.get('cleanup_confirmed'),terminal.get('transport_complete'))
                    self.records.publish(call_id,'completed.json',result)
                    result=cov._completed(directory,verify_source='code_version' not in accepted)
        return result if result is not None else dict(status='unknown',call_id=call_id,note='Original call has no completion')

    @e.record_effect
    def collect_original(self,call_id):
        outputs = super().collect_original(call_id)
        result = self.result(call_id)
        model = result.get('model')
        if model is None:return outputs
        # result() consumes the saved same-call model proof, including legacy
        # call/model.pkl records; the public name does not create a new file.
        outputs=tuple(item for item in outputs if item.local_path!=model['path'])
        return (*outputs,LocalFileDescriptor(name='model.pkl',local_path=model['path'],
            size_bytes=model['size_bytes'],media_type='application/octet-stream'))

    @staticmethod
    def scientific_outputs(descriptors, *, projection_directory):
        return tuple(item for item in descriptors if item.name == 'model.pkl')

    @e.record_effect
    def status_details(self,call_id):
        directory=self.records.directory(call_id)
        self.result(call_id)  # Reconcile retained terminal/output facts, never execute.
        state=self._state(call_id)
        result=cov._completed(directory,verify_assets=False)
        accepted=json.loads(e.read(directory/'accepted.json'))
        row=json.loads(e.read(directory/'row.json')) if result and result.get('candidate_id') is not None else {}
        if result and result['status']=='unknown':state='unknown'
        return dict(state=state,call_id=call_id,candidate_id=result['candidate_id'] if result else None,
            evaluation_key=self.config['evaluation_key'],metric=row.get('val_score') if cov._finite(row.get('val_score')) else None,
            direction='max',wall_seconds=row.get('wall_seconds') if cov._finite(row.get('wall_seconds')) else None,
            parent_candidate=accepted['parent']['candidate_id'],
            **(dict(result=result['result'],returncode=result.get('returncode')) if result and cov.output_unavailable(result) else {}))

    def public_result(self,result):
        return cov.public_feedback(self.config,result,include_model=True) if 'proofs' in result else super().public_result(result)


def build(context):
    return RuntimePluginContribution(execution_adapters={'experiment':CovExecution(json.loads(context.config_bytes))})


def validate_context(item, accepted, parent):
    """Cov's native run/model and derivation contract, separate from host identity."""
    if cov.output_unavailable(item):
        if (item['status'] not in {'known_success','known_failed','unknown'}
                or any(item.get(key) is not None for key in ('candidate_id','run_id','model'))):
            raise e.IntegrityError('Cov unavailable output acquired scientific qualification')
    elif ((item['status']=='known_success') != (item.get('model') is not None)
            or item['status']!='unknown' and (type(item.get('run_id')) is not int
                or item['run_id']<1 or not isinstance(item['candidate_id'],str))):
        raise ValueError('Cov failed/unknown context cannot acquire model qualification')
    if item['parent']['status']=='declared':
        if parent is None or parent.get('model') is None:
            raise e.IntegrityError('Cov parent has no earlier model original')
        completion=next((proof for proof in parent['proofs'] if Path(proof['path']).name=='completed.json'),None)
        expected=cov._binding(parent,completion)
        if accepted.get('parent_binding')!=expected:
            raise e.IntegrityError('Cov parent model differs from the accepted original')


def engineering_feedback(original):
    result={}
    if 'stage' in original:
        result['stage']=original['stage'] if original['stage'] in {'harness','source_import','sample_train','sample_predict'} else 'unknown'
    if isinstance(original.get('samples'),dict):
        result['samples']={key:original['samples'][key] for key in
            ('train_rows','validation_rows','available_train_rows','available_validation_rows')
            if key in original['samples'] and (original['samples'][key] is None or type(original['samples'][key]) is int)}
        result['samples']['selection']='first rows in frozen CSV order'
    if 'completion' in original:result['completion']='Bounded replay did not produce a verified result.'
    return result


def research_defaults(configuration, *, root):
    """Declarations only; caller policies and execution permission remain explicit."""
    root=Path(root).resolve();runs=Path(configuration['runs_root'])
    if not runs.is_absolute() or runs.resolve()!=runs or not runs.is_relative_to(root):
        raise ValueError('Cov observer requires the declared runs_root inside the exact new instance')
    observer=dict(adapter=str(Path(cov.__file__).resolve()),contract_version=2,
        config=dict(runs=runs.relative_to(root).as_posix(),calls='project/nodes',evaluation_key=configuration['evaluation_key']))
    from . import delivery
    return dict(history_observer=observer,delivery_adapter=dict(adapter=str(Path(delivery.__file__).resolve()),
        launcher_argv=[sys.executable,str(Path(delivery.__file__).resolve()),'--isolate']))


REGISTRATION=ExecutionInterfaceRegistration('cov_execution','1',RuntimePluginFactory(build),
    scientific_outputs=CovExecution.scientific_outputs,
    scientific_source=LocalExecution.scientific_source,
    context_validator=validate_context,research_defaults=research_defaults,engineering_feedback=engineering_feedback,
    projection_contract='cov.public-projections.v1')


def _replay_roles(accepted):
    """Decode only Cov's existing runner and four public replay roles."""
    names = ('replay/harness/runner.py', 'replay/harness/train.csv',
        'replay/harness/val.csv', 'replay/harness/meta.json', 'replay/scid-current-protocol')
    runtime = accepted.get('replay_runtime')
    current = isinstance(runtime, dict) and 'inputs' in runtime
    files = runtime['inputs'] if current else accepted.get('snapshot_files')
    expected = names[1:] if current else names
    fields=[{'name','path','size_bytes','file_stat'}]
    if not current:fields.append({'name','path','size_bytes'})
    partial=current and accepted.get('replay_inputs_unavailable') is True
    if (not isinstance(files, list) or not partial and len(files) != len(expected)
            or any(not isinstance(item, dict) or set(item) not in fields
                or item.get('name') not in expected for item in files)
            or len({item['name'] for item in files})!=len(files)
            or not partial and {item['name'] for item in files} != set(expected)):
        raise e.IntegrityError('Accepted public replay roles are malformed')
    by_name = {item['name']: item for item in files}
    if current:
        runner = accepted['receipt_transport']['runner']
        by_name[names[0]] = dict({key: runner[key] for key in ('path','size_bytes','file_stat') if key in runner}, name=names[0])
    return [by_name[name] for name in names if name in by_name]
