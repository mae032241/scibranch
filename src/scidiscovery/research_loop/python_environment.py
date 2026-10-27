"""Opt-in installed Python dependency checks during operator configuration only."""
from __future__ import annotations

import json
import io
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time
import tokenize

from scidiscovery.plugin_configuration import PluginConfigurationError

_FIELD = 'executor.config.configuration'
_MAX_OUTPUT = 32_768
_SECONDS = 20
_FLAGS = {'-B', '-I', '-E', '-s', '-u', '-S'}
_MODULE = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z', re.ASCII)
_PYTHON = re.compile(r'python(?:[0-9]+(?:\.[0-9]+)*)?\Z', re.ASCII)
_SCRIPT = r'''
import contextlib, importlib, importlib.metadata, json, os, platform, sys
request = json.loads(sys.argv[1])
if request['phase'] == 'roots':
    result = list(dict.fromkeys((sys.prefix, sys.base_prefix)))
elif request['phase'] == 'environment':
    version = sys.implementation.version
    implementation_version = '.'.join(str(x) for x in version[:3])
    if version.releaselevel != 'final':
        implementation_version += version.releaselevel[0] + str(version.serial)
    result = dict(implementation_name=sys.implementation.name,
        implementation_version=implementation_version, os_name=os.name, sys_platform=sys.platform,
        platform_machine=platform.machine(), platform_release=platform.release(),
        platform_system=platform.system(), platform_version=platform.version(),
        platform_python_implementation=platform.python_implementation(),
        python_full_version=platform.python_version(), python_version='.'.join(platform.python_version_tuple()[:2]))
else:
    result = []
    for row in request['requirements']:
        try:
            version = importlib.metadata.version(row['distribution'])
        except importlib.metadata.PackageNotFoundError:
            result.append(dict(status='missing'))
            continue
        try:
            with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                importlib.import_module(row['import_name'])
        except BaseException:
            result.append(dict(status='import_failed', version=version))
        else:
            result.append(dict(status='ok', version=version))
print(json.dumps(result, allow_nan=False))
'''


def _error(code, message, *, field='python_requirements', plugin_id):
    return PluginConfigurationError(code, message, field_path=f'{_FIELD}.{field}',
        hint='请检查 run_argv[0] 指定的实验 Python 及该字段的依赖声明；预检不会安装或更换环境。',
        plugin_id=plugin_id)


def _python_prefix(argv, *, allow_script, plugin_id):
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(value, str) or not value or '\0' in value for value in argv)
            or not Path(argv[0]).is_absolute() or not _PYTHON.fullmatch(Path(argv[0]).name)):
        raise _error('python_interpreter_required', '依赖预检需要 run_argv[0] 明确指定 Python 解释器的绝对路径。',
            field='run_argv', plugin_id=plugin_id)
    flags = argv[1:]
    if allow_script:
        # Match only the public receipt adapter's supported Python prefixes.
        # The callback checks declaration shape, never reads the runner/harness.
        if flags and not flags[-1].startswith('-'):
            if not flags[-1].endswith('.py') or not Path(flags[-1]).is_absolute():
                raise _error('python_invocation_unsupported', 'Cov 依赖预检需要受支持的绝对 Python 脚本路径。',
                    field='run_argv', plugin_id=plugin_id)
            flags = flags[:-1]
        if flags not in ([], ['-I'], ['-B', '-I']):
            raise _error('python_invocation_unsupported', 'Cov 依赖预检不支持该 Python 启动方式。',
                field='run_argv', plugin_id=plugin_id)
    else:
        if any(value not in _FLAGS for value in flags):
            raise _error('python_invocation_unsupported', '依赖预检不支持该 Python 启动方式。',
                field='run_argv', plugin_id=plugin_id)
    # Keep -S when explicitly declared: loading site here would falsely qualify
    # a package which the actual invocation cannot import. Never resolve a venv
    # symlink to its base Python; argv[0] remains the sole interpreter authority.
    return [argv[0], *flags, '-I', '-B']


def is_python_interpreter(value):
    """Only explicit conventional Python paths opt into interpreter probing."""
    return (isinstance(value, str) and '\0' not in value and Path(value).is_absolute()
        and _PYTHON.fullmatch(Path(value).name) is not None)


def runtime_roots(interpreter):
    """Capture only stdlib prefix locations for one new private native attempt."""
    prefix = _python_prefix([interpreter], allow_script=False, plugin_id='local_execution')
    roots = _probe(prefix, dict(phase='roots'), deadline=time.monotonic()+_SECONDS, plugin_id='local_execution')
    if (not isinstance(roots, list) or not 1 <= len(roots) <= 2
            or any(not isinstance(path, str) or not path or '\0' in path or len(path) > 4096
                or not Path(path).is_absolute() or Path(path).resolve() != Path(path)
                or not Path(path).is_dir() for path in roots)):
        raise _error('python_probe_invalid', '实验 Python 未返回有效的原始运行时目录。', plugin_id='local_execution')
    return roots


def _probe(prefix, request, *, deadline, plugin_id):
    """Bound both pipes and lifetime, and stop the probe's entire process group."""
    process = None
    try:
        process = subprocess.Popen([*prefix, '-c', _SCRIPT, json.dumps(request)],
            cwd='/', env={'LANG':'C.UTF-8', 'LC_ALL':'C.UTF-8',
                'OPENBLAS_NUM_THREADS':'1', 'OMP_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1'},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
        output = {process.stdout:bytearray(), process.stderr:bytearray()}
        with selectors.DefaultSelector() as selector:
            for stream in output:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _error('python_probe_timeout', '实验 Python 依赖预检超时。', plugin_id=plugin_id)
                for key, _ in selector.select(min(remaining, .1)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif len(output[key.fileobj]) + len(chunk) > _MAX_OUTPUT:
                        raise _error('python_probe_output_limit', '实验 Python 依赖预检输出超过限制。', plugin_id=plugin_id)
                    else:
                        output[key.fileobj].extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _error('python_probe_timeout', '实验 Python 依赖预检超时。', plugin_id=plugin_id)
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise _error('python_probe_timeout', '实验 Python 依赖预检超时。', plugin_id=plugin_id) from None
        if returncode:
            raise _error('python_probe_failed', '实验 Python 未能完成依赖预检。', plugin_id=plugin_id)
        try:
            return json.loads(output[process.stdout])
        except (UnicodeError, ValueError):
            raise _error('python_probe_invalid', '实验 Python 依赖预检没有返回有效结果。', plugin_id=plugin_id) from None
    except (OSError, ValueError) as error:
        if isinstance(error, PluginConfigurationError):
            raise
        raise _error('python_probe_failed', '无法启动或读取 run_argv[0] 指定的实验 Python。',
            field='run_argv', plugin_id=plugin_id) from None
    finally:
        if process is not None:
            try:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                raise _error('python_probe_cleanup_failed', '实验 Python 依赖预检进程清理未能确认。', plugin_id=plugin_id) from None
            finally:
                process.stdout.close()
                process.stderr.close()


def check_python_requirements(configuration, *, plugin_id, allow_script=False):
    """Validate declarations against argv[0]; do not mutate/freeze probe results."""
    if 'python_requirements' not in configuration:
        return configuration
    rows = configuration['python_requirements']
    if not isinstance(rows, list) or len(rows) > 32:
        raise _error('python_requirements_invalid', 'python_requirements 必须是最多 32 项的依赖声明数组。', plugin_id=plugin_id)
    if not rows:
        return configuration
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.version import InvalidVersion, Version
    parsed = []
    for index, row in enumerate(rows):
        field = f'python_requirements.{index}'
        if (not isinstance(row, dict) or set(row) != {'requirement', 'import_name'}
                or not isinstance(row.get('requirement'), str) or not 0 < len(row['requirement']) <= 512
                or not isinstance(row.get('import_name'), str) or len(row['import_name']) > 128
                or not _MODULE.fullmatch(row['import_name'])):
            raise _error('python_requirements_invalid', '每项依赖需要有界的 PEP 508 requirement 和明确 import_name。',
                field=field, plugin_id=plugin_id)
        try:
            requirement = Requirement(row['requirement'])
        except InvalidRequirement:
            raise _error('python_requirements_invalid', '依赖 requirement 不是有效的 PEP 508 声明。',
                field=field+'.requirement', plugin_id=plugin_id) from None
        # Extras have no single declared import or distribution contract. URLs
        # require origin validation rather than the installed-version check here.
        marker_names = {token.string for token in tokenize.generate_tokens(
            io.StringIO(str(requirement.marker)).readline) if token.type == tokenize.NAME}
        if (requirement.extras or requirement.url
                or marker_names & {'extra', 'extras', 'dependency_groups'}):
            raise _error('python_requirements_unsupported', '依赖预检不支持 extras、URL 或 extra marker。',
                field=field+'.requirement', plugin_id=plugin_id)
        parsed.append((index, requirement, row['import_name']))
    prefix = _python_prefix(configuration.get('run_argv'), allow_script=allow_script, plugin_id=plugin_id)
    deadline = time.monotonic() + _SECONDS
    environment = _probe(prefix, dict(phase='environment'), deadline=deadline, plugin_id=plugin_id)
    marker_fields = {'implementation_name', 'implementation_version', 'os_name', 'sys_platform', 'platform_machine',
        'platform_release', 'platform_system', 'platform_version', 'platform_python_implementation',
        'python_full_version', 'python_version'}
    if (not isinstance(environment, dict) or set(environment) != marker_fields
            or any(not isinstance(value, str) or len(value) > 1024 for value in environment.values())):
        raise _error('python_probe_invalid', '实验 Python 未返回完整的目标 marker 环境。', plugin_id=plugin_id)
    applicable = [(index, requirement, module) for index, requirement, module in parsed
        if requirement.marker is None or requirement.marker.evaluate(environment=environment)]
    if not applicable:
        return configuration
    results = _probe(prefix, dict(phase='requirements', requirements=[dict(
        distribution=requirement.name, import_name=module) for _, requirement, module in applicable]),
        deadline=deadline, plugin_id=plugin_id)
    if not isinstance(results, list) or len(results) != len(applicable):
        raise _error('python_probe_invalid', '实验 Python 未返回完整依赖预检结果。', plugin_id=plugin_id)
    for (index, requirement, _), result in zip(applicable, results):
        field = f'python_requirements.{index}'
        if not isinstance(result, dict) or result.get('status') not in {'missing', 'import_failed', 'ok'}:
            raise _error('python_probe_invalid', '实验 Python 返回了无效依赖预检结果。', field=field, plugin_id=plugin_id)
        if result['status'] == 'missing':
            raise _error('python_dependency_missing', 'run_argv[0] 指定的实验 Python 缺少该 distribution。',
                field=field+'.requirement', plugin_id=plugin_id)
        try:
            version = Version(result['version'])
        except (KeyError, TypeError, InvalidVersion):
            raise _error('python_dependency_version_invalid', '实验 Python 中该 distribution 的版本元数据无效。',
                field=field+'.requirement', plugin_id=plugin_id) from None
        if version not in requirement.specifier:
            raise _error('python_dependency_version_mismatch', '实验 Python 中该 distribution 版本不满足 requirement。',
                field=field+'.requirement', plugin_id=plugin_id)
        if result['status'] == 'import_failed':
            raise _error('python_dependency_import_failed', '实验 Python 无法导入该 import_name。',
                field=field+'.import_name', plugin_id=plugin_id)
    return configuration
