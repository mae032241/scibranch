"""Installation-owned Skill bundles and exact, rebuildable Codex projections.

This selects only framework-managed Skills. Native user/system/plugin sources
remain inherited; readonly modes are a soft boundary for trusted same-user code.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

from . import contracts as c

DIRECTORY = 'host-control/skills'
MANIFEST = DIRECTORY + '/manifest.json'
_NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z')
_CATALOG_LIMIT = 256
def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError('Skill and set names must be simple directory names')
    return value


def source_root():
    """One asset tree beside the actual source/flat install or wheel prefix."""
    module = Path(__file__).resolve()
    site = module.parents[2]
    prefix = Path(sys.prefix).resolve()
    if (site.name in {'site-packages', 'dist-packages'}
            and site.parent.name == f'python{sys.version_info.major}.{sys.version_info.minor}'
            and site.parent.parent.name in {'lib', 'lib64'}
            and site.parent.parent.parent == prefix):
        return prefix / 'skills'
    return module.parents[3] / 'skills'


def configuration(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'sets'}:
        raise ValueError('skills must contain only sets; assets belong to the CLI installation')
    sets = value.get('sets', {})
    if not isinstance(sets, dict):
        raise ValueError('Skill sets must be a table')
    result = {}
    for name, row in sets.items():
        _name(name)
        if (not isinstance(row, dict) or set(row) != {'items'} or not isinstance(row['items'], list)
                or any(not isinstance(item, str) for item in row['items'])):
            raise ValueError('Each Skill set requires an items array')
        items = [_name(item) for item in row['items']]
        if len(items) != len(set(items)):
            raise ValueError('Skill set repeats an item')
        result[name] = dict(items=sorted(items))
    return dict(sets=result)


def _metadata(directory):
    """Read only the selected directory's native manual, never its resource tree."""
    item = _name(directory.name)
    diagnostic = (f'Selected Skill {item!r} requires an ordinary directory and SKILL.md at {directory}; '
        f'install or restore that bundle in the CLI installation skills directory {directory.parent}')
    if directory.is_symlink() or not directory.is_dir() or directory.resolve() != directory:
        raise ValueError(diagnostic)
    manual_path = directory / 'SKILL.md'
    if manual_path.is_symlink() or manual_path.resolve() != manual_path:
        raise ValueError(diagnostic)
    try:
        manual = c.regular(manual_path, limit=256_000).read_text(encoding='utf-8')
    except (OSError, ValueError) as error:
        raise ValueError(diagnostic) from error
    lines = manual.splitlines()
    if not lines or lines[0] != '---' or '---' not in lines[1:]:
        raise ValueError(f'Selected Skill {item!r} at {directory} requires SKILL.md name and description frontmatter')
    front = lines[1:lines[1:].index('---') + 1]
    metadata = {}
    for key in ('name', 'description'):
        matches = [line.partition(':')[2].strip() for line in front if line.startswith(key + ':')]
        if len(matches) != 1 or not matches[0].strip('\'" '):
            raise ValueError('Selected Skill requires unambiguous name and description frontmatter')
        value = matches[0]
        if value[0] in {'"', "'"}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError('Selected Skill frontmatter quote is incomplete')
            value = value[1:-1]
        metadata[key] = value
    if _name(metadata['name']) != item:
        raise ValueError(f'Selected Skill directory and native name must match at {directory}')
    return metadata


def catalog():
    """List installed metadata for an explicit configuration view, without selection.

    Only this installation's immediate entries and native SKILL.md files are
    inspected. Resources, user Skill sources and instance state are untouched.
    """
    items, errors = [], []
    source = None
    try:
        source = source_root()
        if source.is_symlink() or source.resolve() != source or not source.is_dir():
            raise ValueError('requires an ordinary installation skills directory')
        with os.scandir(source) as entries:
            for index, entry in enumerate(entries):
                if index >= _CATALOG_LIMIT:
                    errors.append(dict(name='skills', message=(
                        f'Installed Skill catalog at {source} is limited to {_CATALOG_LIMIT} '
                        'directory entries; additional entries were not inspected')))
                    break
                try:
                    if entry.is_symlink():
                        raise ValueError(f'Installed Skill {entry.name!r} at {source / entry.name} '
                            'requires an ordinary directory; restore the installed bundle')
                    if entry.is_dir(follow_symlinks=False):
                        items.append(_metadata(source / entry.name))
                except (OSError, ValueError, RuntimeError) as error:
                    errors.append(dict(name=entry.name, message=str(error)))
    except (OSError, ValueError, RuntimeError) as error:
        items = []
        location = f' at {source}' if source is not None else ''
        errors.append(dict(name='skills', message=f'Installed Skill catalog unavailable{location}: '
            f'{error}; install or restore the CLI installation skills directory'))
    return dict(items=sorted(items, key=lambda row: row['name']),
        errors=sorted(errors, key=lambda row: row['name']))


def _selected(sets, role_profiles=None, worker_profiles=None):
    from . import profiles
    rows = profiles.normalize(role_profiles or {}, dict(model='selection', reasoning_effort=None,
        service_tier=None), worker_profiles, sets)
    return sorted({item for row in rows.values() if row.get('skill_set') is not None
        for item in sets[row['skill_set']]['items']})


def _launcher_module(launcher):
    args = list(launcher[1:])
    if args and args[-1]=='--':args.pop()  # Optional exact argparse boundary.
    while args and args[0] in {'-B', '-I'}:
        args.pop(0)
    if (re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?', Path(launcher[0]).name) is None
            or len(args) != 4 or args[0] != '-m' or args[2] != '--config'
            or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+', args[1]) or not args[3]):
        raise ValueError('Custom native launcher has no Skill loading contract')
    return args[1]


def _module_identity(module):
    from scidiscovery import archive_io as files
    spec = importlib.util.find_spec(module)
    if spec is None or spec.origin is None:
        raise ValueError('Declared native Skill launcher module is unavailable')
    path = files.check_path(Path(spec.origin).absolute())
    c.regular(path)
    return dict(name=module, path=str(path))


def _provider(entry, module):
    from scidiscovery.operations.execution import ExecutionInterfaceRegistration
    declared = entry.load()
    if not isinstance(declared, ExecutionInterfaceRegistration) or declared.plugin_id != entry.name:
        raise ValueError('Native Skill launcher registration identity changed')
    if declared.native_launcher_module != module:
        return None
    return dict(plugin_id=declared.plugin_id, plugin_version=declared.version,
        entry_point=entry.value, native_launcher_module=module, contract_version=1)


def _launcher_contract(backend, launcher, retained=None):
    if backend != 'codex':
        raise ValueError('Custom native backend has no Skill loading contract')
    standard = len(launcher) == 1 and Path(launcher[0]).name == 'codex'
    if standard:
        value = dict(kind='codex_node_cwd', version=1)
    else:
        module = _launcher_module(launcher)
        group = 'scidiscovery.execution_interfaces'
        if retained is None:
            matches = []
            for entry in importlib.metadata.entry_points().select(group=group):
                provider = _provider(entry, module)
                if provider is not None:
                    matches.append(provider)
            if len(matches) != 1:
                raise ValueError('Native Skill launcher requires one exact registered provider declaration')
            provider = matches[0]
        else:
            provider = retained.get('provider', {})
            entries = list(importlib.metadata.entry_points().select(group=group, name=provider.get('plugin_id')))
            if len(entries) != 1 or _provider(entries[0], module) != provider:
                raise ValueError('Frozen native Skill launcher provider changed')
        value = dict(kind='registered_native_module', version=1, provider=provider, module=_module_identity(module))
    if retained is not None and value != retained:
        raise ValueError('Frozen native Skill launcher identity changed; create a new instance')
    return value


def native_launcher_contract(backend, launcher, *, retained=...):
    """Freeze the existing registered final scientific namespace owner.

    Other local argv remain eligible for the common outer. An existing exact
    declaration is verified even when the selected Skill inventory is empty.
    """
    value=None
    if backend=='codex' and not (len(launcher)==1 and Path(launcher[0]).name=='codex'):
        try:module=_launcher_module(launcher)
        except ValueError:module=None
        if module is not None:
            entries=importlib.metadata.entry_points().select(group='scidiscovery.execution_interfaces')
            matches=[entry for entry in entries if _provider(entry,module) is not None]
            if len(matches)>1:raise ValueError('Native namespace launcher requires one exact registered provider')
            if matches:
                value=_launcher_contract(backend,launcher,retained if retained is not ... else None)
    if retained is not ... and value!=retained:
        raise ValueError('Frozen native namespace launcher identity changed; create a new instance')
    return value


def namespace_launcher(settings):
    """Validate the frozen owner before choosing its final namespace seam."""
    capabilities=settings.get('native_capabilities',{})
    if 'native_launcher_contract' not in capabilities:
        raise ValueError('Frozen native namespace launcher contract is missing')
    return native_launcher_contract(settings.get('native_backend','codex'),settings['native_launcher_argv'],
        retained=capabilities['native_launcher_contract'])


def prepare(value, *, backend, launcher, existing=None, policy=None, context=None,
            role_profiles=None, worker_profiles=None):
    declared = configuration(value)
    old = existing.get('skills') if existing is not None else None
    if existing is not None:
        if declared['sets'] != (old['sets'] if old is not None else {}):
            raise ValueError('Skill sets and source bindings are frozen; create a new instance to change them')
        return (dict(old), None) if old is not None else (None, None)
    if not declared['sets'] and value is None:
        return None, None
    items = _selected(declared['sets'], role_profiles, worker_profiles)
    source = source_root()
    result = dict(version=3, root=str(source), sets=declared['sets'])
    if items:
        result['launcher'] = _launcher_contract(backend, launcher)
    for item in items:
        _metadata(source / item)
    return result, None


def freeze(root, settings, manifest, *, context=None):
    """New instances retain source bindings only; there are no Skill bytes to freeze."""
    if manifest is not None:
        raise ValueError('Per-instance Skill freezing is retired')


def _legacy_root(root, declared):
    """Resolve retained v2 bytes through exact registered original sources only."""
    from . import originals
    from .fork_sources import MAX_ANCESTRY
    current = Path(root)
    seen = set()
    for _ in range(MAX_ANCESTRY + 1):
        expected = current / MANIFEST
        if expected.exists() or expected.is_symlink():
            return current
        if current in seen:
            raise ValueError('Legacy Skill source ancestry contains a cycle')
        seen.add(current)
        parent_root = current
        try:
            selected = originals.selected_source(current)
            if originals.source_settings(selected).get('skills') != declared:
                raise ValueError('Legacy Skill source settings changed')
            parent_root = Path(selected.original_root)
            # Prove actual disappearance before the node reader's missing-job
            # diagnostic; an existing but changed identity still fails below.
            parent_root.lstat()
            parent = originals.node(parent_root, selected.step)
            if (parent.origin_identity != selected.origin_identity
                    or parent.original_root != selected.original_root):
                raise ValueError('Legacy Skill source identity changed at ' + str(parent_root))
            if originals.source_settings(parent).get('skills') != declared:
                raise ValueError('Legacy Skill source settings changed at ' + str(parent_root))
            candidate = parent_root / MANIFEST
            if candidate.exists() or candidate.is_symlink():
                resolved = originals.resolve_ref(parent, dict(path=MANIFEST))
                if resolved != candidate:
                    raise ValueError('Legacy Skill source manifest location changed')
                return parent_root
            current = parent_root
        except OSError as error:
            missing = parent_root / MANIFEST
            raise ValueError(f'Legacy Skill source unavailable at {missing}; '
                'keep the exact selected parent instance and its retained Skill assets available') from error
        except ValueError as error:
            if str(error) == 'Instance has no frozen fork source':
                raise ValueError(f'Legacy Skill source unavailable at {expected}; '
                    'keep the exact selected parent instance and its retained Skill assets available') from error
            raise
    raise ValueError('Legacy Skill source ancestry exceeds its finite depth bound')


def _legacy_validate(root, declared):
    """Read retained v2 inventories only; never create or migrate their objects."""
    from scidiscovery import archive_io as files
    root = _legacy_root(root, declared)
    path = files.check_path(root / MANIFEST)
    manifest = c.read(c.regular(path))
    if (declared.get('manifest') != dict(path=MANIFEST, version=manifest.get('freeze_id'))
            or manifest.get('version') != 2 or manifest.get('sets') != declared['sets']
            or stat.S_IMODE(path.stat().st_mode) != 0o400):
        raise ValueError('Frozen Skill manifest changed')
    names = {item for row in declared['sets'].values() for item in row['items']}
    if set(manifest['objects']) != names:
        raise ValueError('Frozen Skill object inventory changed')
    for item, tree in manifest['objects'].items():
        directory = Path(root) / DIRECTORY / 'objects' / _name(item)
        expected = {}
        for relative in ['', *tree['directories']]:
            if relative and (PurePosixPath(relative).is_absolute() or '..' in PurePosixPath(relative).parts
                    or PurePosixPath(relative).as_posix() != relative or relative == '.'):
                raise ValueError('Frozen Skill directory path changed')
            base = files.check_path(directory / relative)
            if not base.is_dir() or stat.S_IMODE(base.stat().st_mode) != 0o500:
                raise ValueError('Frozen Skill directory permissions changed')
            expected[base] = set()
        for row in tree['files']:
            relative = PurePosixPath(row['path'])
            if (relative.is_absolute() or '..' in relative.parts or relative.as_posix() != row['path']
                    or row['path'] == '.'):
                raise ValueError('Frozen Skill file path changed')
            path = files.check_path(directory / row['path'])
            if (stat.S_IMODE(c.regular(path).stat().st_mode) != 0o400
                    or path.stat().st_size != row['size_bytes']):
                raise ValueError('Frozen Skill object permissions or size changed')
            expected[path.parent].add(path.name)
        for base in expected:
            if base != directory:
                expected[base.parent].add(base.name)
        for base, children in expected.items():
            if {child.name for child in base.iterdir()} != children:
                raise ValueError('Frozen Skill tree gained or lost files')
        if _metadata(directory) != tree['metadata']:
            raise ValueError('Frozen Skill metadata changed')
    return manifest


def validate(root, settings):
    declared = settings.get('skills')
    if declared is None:
        return None
    if declared.get('launcher') is not None:
        _launcher_contract(settings.get('native_backend', 'codex'), settings['native_launcher_argv'], declared['launcher'])
    if 'manifest' in declared:
        return _legacy_validate(root, declared)
    if (declared.get('version') != 3 or set(declared) - {'version', 'root', 'sets', 'launcher'}
            or configuration(dict(sets=declared['sets']))['sets'] != declared['sets']
            or not isinstance(declared.get('root'), str) or not Path(declared['root']).is_absolute()):
        raise ValueError('Frozen Skill source binding changed')
    items = _selected(declared['sets'], settings.get('role_profiles'), settings.get('worker_profiles'))
    if items and declared.get('launcher') is None:
        # A profile edit may select a previously unused set, but cannot evade the
        # existing backend/launcher contract established for native discovery.
        _launcher_contract(settings.get('native_backend', 'codex'), settings['native_launcher_argv'])
    for item in items:
        _metadata(Path(declared['root']) / item)
    return declared


def binding(settings, job):
    declared = settings.get('skills')
    if declared is None:
        return None
    from . import profiles
    profile = profiles.normalize(settings.get('role_profiles', {}), settings,
        settings.get('worker_profiles'), declared['sets'])[job['role']]
    name = profile.get('skill_set')
    items = declared['sets'][name]['items'] if name is not None else []
    result = dict(version=2 if 'manifest' in declared else 3, role=job['role'],
        worker=profile.get('worker'), skill_set=name, configuration_revision=job.get('configuration_revision'), items=items)
    result.update(dict(manifest=declared['manifest']) if 'manifest' in declared else dict(root=declared['root']))
    return result


def for_job(settings, job):
    expected = binding(settings, job)
    if job.get('skill_binding') != expected:
        raise ValueError('Frozen native job Skill binding changed')
    return expected


def _targets(root, settings, job):
    manifest = validate(root, settings)
    for_job(settings, job)
    if manifest is None:
        return None
    name = job['skill_binding']['skill_set']
    source = (_legacy_root(root, settings['skills']) / DIRECTORY / 'objects'
        if 'manifest' in settings['skills'] else Path(settings['skills']['root']))
    return {item: source / item for item in (manifest['sets'][name]['items'] if name is not None else [])}


def project(root, settings, job, *, rebuild=True):
    """Only missing derived entries may be rebuilt; unexpected entries fail closed."""
    targets = _targets(root, settings, job)
    if targets is None:
        return
    from scidiscovery import archive_io as files
    work = c.workspace(Path(root), job['step'])
    parent = work / '.agents'
    directory = parent / 'skills'
    files.check_path(parent)
    if parent.exists() and not parent.is_dir():
        raise ValueError('Native Skill projection parent changed')
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise ValueError('Native Skill projection changed')
    if not directory.exists():
        if not rebuild:
            raise ValueError('Native Skill projection is missing')
        directory.mkdir(parents=True)
    entries = {path.name: path for path in directory.iterdir()}
    if set(entries) - set(targets):
        raise ValueError('Native Skill projection contains unselected entries')
    for item, target in targets.items():
        path = directory / item
        relative = os.path.relpath(target, directory)
        if item in entries:
            if not path.is_symlink() or os.readlink(path) != relative or path.resolve() != target.resolve():
                raise ValueError('Native Skill projection target changed')
        elif rebuild:
            path.symlink_to(relative, target_is_directory=True)
        else:
            raise ValueError('Native Skill projection is incomplete')
    return targets


def archive_projection(root, step):
    from . import configuration_updates
    job = c.read(c.regular(Path(root) / 'jobs' / f'{step:03d}.json'))
    settings = configuration_updates.for_job(root, job)
    if settings.get('skills') is None:
        raise ValueError('Unbound native Skill projection cannot be archived')
    project(root, settings, job, rebuild=False)


def rebuild(root):
    from . import configuration_updates
    for path in sorted((Path(root) / 'jobs').glob('*.json')):
        job = c.read(c.regular(path))
        project(root, configuration_updates.for_job(root, job), job)


def inherit(root, settings):
    """Direct installation bindings need no instance-owned Skill objects."""
    if settings.get('skills') is not None:
        validate(root, settings)


def native_mounts(root, job):
    """Exact readonly inputs for a registered native namespace only.

    The caller must not pass this list to scientific executor subprocesses.
    Neither settings/manifest nor the whole installation library is supplied.
    """
    from . import configuration_updates
    settings = configuration_updates.for_job(root, job)
    targets = project(root, settings, job, rebuild=False)
    if targets is None:
        return []
    projection = c.workspace(Path(root), job['step']) / '.agents/skills'
    return [(str(path), str(path)) for path in dict.fromkeys([projection, *targets.values()])]
