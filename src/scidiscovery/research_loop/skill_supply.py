"""Instance-owned immutable Skill bundles and exact, rebuildable Codex projections.

This selects only framework-managed Skills. Native user/system/plugin sources
remain inherited; readonly modes are a soft boundary for trusted same-user code.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from . import contracts as c

DEFAULT_ROOT = '/opt/scidiscovery-thin/skills'
DIRECTORY = 'host-control/skills'
MANIFEST = DIRECTORY + '/manifest.json'
_NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z')
_SHARED_STORE_HINT = ('Set SCID_SHARED_STORE to an absolute private directory owned by this user '
    'on the instance filesystem; the filesystem must support hardlinks. No copy fallback.')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _manifest_digest(value):
    payload = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    return hashlib.sha256(payload).hexdigest()


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError('Skill and set names must be simple directory names')
    return value


def configuration(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'root', 'sets'}:
        raise ValueError('skills must contain only root and sets')
    root = value.get('root', os.environ.get('SCID_SKILLS_ROOT', DEFAULT_ROOT))
    if not isinstance(root, str) or not root or not Path(root).is_absolute():
        raise ValueError('Skill source root must be an absolute path')
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
    return dict(root=str(Path(root).absolute()), sets=result)


def _tree(directory, *, inventory=None, context=None):
    """Inspect only a selected tree; links and special files are never imported."""
    rows, directories = [], []
    def walk(path):
        info = path.lstat()
        if inventory is not None:
            inventory.entry()
        if stat.S_ISREG(info.st_mode):
            if inventory is not None:
                inventory.file(info.st_size)
            relative = path.relative_to(directory).as_posix()
            if '\\' in relative:
                raise ValueError('Skill resource paths must be portable relative paths')
            from scidiscovery import archive_io as files
            proof = files.hash_file(path, context=context)
            rows.append(dict(path=relative, sha256=proof['sha256'], size_bytes=info.st_size))
        elif stat.S_ISDIR(info.st_mode):
            if path != directory:
                directories.append(path.relative_to(directory).as_posix())
            for child in sorted(path.iterdir(), key=lambda p: p.name):
                walk(child)
        else:
            raise ValueError('Skill bundles require ordinary files and directories, without links')
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Selected Skill directory is missing or linked')
    walk(directory)
    manual = c.regular(directory / 'SKILL.md', limit=256_000).read_text(encoding='utf-8')
    lines = manual.splitlines()
    if not lines or lines[0] != '---' or '---' not in lines[1:]:
        raise ValueError('Selected Skill requires SKILL.md name and description frontmatter')
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
    _name(metadata['name'])
    return dict(files=rows, directories=directories, metadata=metadata)


def _launcher_module(launcher):
    args = list(launcher[1:])
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
    proof = files.hash_file(path)
    return dict(name=module, path=str(path), **{key: proof[key] for key in ('sha256', 'size_bytes')})


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


def prepare(value, *, backend, launcher, existing=None, policy=None, context=None):
    declared = configuration(value)
    items = sorted({item for row in declared['sets'].values() for item in row['items']})
    old = existing.get('skills') if existing is not None else None
    loader = _launcher_contract(backend, launcher, old.get('launcher') if old else None) if items else None
    if existing is not None:
        if declared != ({key: old[key] for key in ('root', 'sets')} if old is not None else configuration()):
            raise ValueError('Skill source, sets and versions are frozen; create a new instance to change them')
        return (dict(old), None) if old is not None else (None, None)
    if not declared['sets'] and value is None:
        return None, None
    source = Path(declared['root'])
    if items and (source.is_symlink() or source.resolve() != source):
        raise ValueError('Skill source root must be an exact ordinary directory')
    from . import storage_policy as storage
    inventory = storage.Inventory(policy, context=context)
    objects = {}
    for item in items:
        tree = _tree(source / item, inventory=inventory, context=context)
        if tree['metadata']['name'] != item:
            raise ValueError('Selected Skill directory and native name must match')
        objects[item] = dict(tree_sha256=_hash(tree), **tree)
    manifest = dict(version=1, sets=declared['sets'], objects=objects)
    result = dict(declared, manifest=dict(path=MANIFEST, sha256=_manifest_digest(manifest)))
    if loader is not None:
        result['launcher'] = loader
    return result, manifest


def _shared_store():
    """Private mechanical cache; it confers neither scientific identity nor permission."""
    from scidiscovery import archive_io as files
    configured = os.environ.get('SCID_SHARED_STORE')
    cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    path = Path(configured) if configured is not None else cache / 'scidiscovery' / 'shared-store'
    if not path.is_absolute():
        raise ValueError('SCID_SHARED_STORE requires an exact absolute directory. ' + _SHARED_STORE_HINT)
    try:
        files.check_path(path)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = path.stat()
    except PermissionError:
        raise PermissionError('Skill shared store is inaccessible. ' + _SHARED_STORE_HINT) from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('Skill shared store must be a private directory owned by this user. ' + _SHARED_STORE_HINT)
    return path


def _readonly_directories(base):
    for path in sorted((p for p in base.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        path.chmod(0o500)
    base.chmod(0o500)


def freeze(root, settings, manifest, *, context=None):
    if manifest is None:
        return
    if not manifest['objects']:
        path = Path(root) / MANIFEST
        c.write(path, manifest)
        path.chmod(0o400)
        validate(root, settings)
        return
    from scidiscovery import archive_io as files
    from . import atomic_files, storage_policy as storage
    root = Path(root)
    declared = settings['skills']
    policy = settings.get('storage_policy')
    context = context or storage.Deadline(65)
    inventory = storage.Inventory(policy, context=context)
    # Apply the frozen logical inventory before any content hashing or importing.
    unique = {}
    for tree in manifest['objects'].values():
        inventory.entry()
        for relative in tree['directories']:
            inventory.entry()
        for row in tree['files']:
            inventory.entry(); inventory.file(row['size_bytes'])
            unique[row['sha256']] = row['size_bytes']
    store = _shared_store()
    if store.stat().st_dev != root.stat().st_dev:
        raise files.SharingUnsupported('Skill sharing requires supported same-filesystem hardlinks. ' + _SHARED_STORE_HINT)
    # One common cache lock covers all trees: competing trees never independently
    # copy the same missing payload before publishing a winning object.
    with storage.writer_lock(root, context=context), storage.writer_lock(store, context=context):
        present = set()
        objects = root / DIRECTORY / 'objects'
        if objects.exists():
            for tree in manifest['objects'].values():
                for row in tree['files']:
                    if (objects / tree['tree_sha256'] / row['path']).exists():
                        present.add(row['sha256'])
        growth = sum(size for digest, size in unique.items() if digest not in present)
        with storage.mutation(root, growth, policy=policy, context=context):
            for item, tree in manifest['objects'].items():
                base = files.check_path(objects / tree['tree_sha256'])
                base.mkdir(parents=True, exist_ok=True)
                for relative in tree['directories']:
                    (base / relative).mkdir(parents=True, exist_ok=True)
                for row in tree['files']:
                    source = Path(declared['root']) / item / row['path']
                    blob = files.check_path(store / 'skills/objects' / row['sha256'])
                    # Recheck the configured installation even when a cache hit exists.
                    proof = files.hash_file(source, context=context)
                    if any(proof[key] != row[key] for key in ('sha256', 'size_bytes')):
                        raise ValueError('Skill source changed during preparation')
                    if not blob.exists() and not blob.is_symlink():
                        atomic_files.copy_original(source, blob, sha256=row['sha256'],
                            size_bytes=row['size_bytes'], context=context)
                    try:
                        files.link_verified(blob, base / row['path'], dict(kind='file', mode=0o400, **row), context=context)
                    except files.SharingUnsupported as error:
                        raise files.SharingUnsupported('Skill sharing requires supported same-filesystem hardlinks. ' + _SHARED_STORE_HINT) from error
                tree_path = store / 'skills/trees' / (tree['tree_sha256'] + '.json')
                payload = (json.dumps(tree, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
                files.check_path(tree_path)
                if tree_path.exists():
                    if stat.S_IMODE(tree_path.stat().st_mode) != 0o400 or tree_path.read_bytes() != payload:
                        raise ValueError('Shared Skill tree manifest changed')
                else:
                    atomic_files.publish_bytes(tree_path, payload)
                _readonly_directories(base)
    path = root / MANIFEST
    c.write(path, manifest)
    path.chmod(0o400)
    validate(root, settings)


def validate(root, settings):
    declared = settings.get('skills')
    if declared is None:
        return None
    if declared.get('launcher') is not None:
        _launcher_contract(settings.get('native_backend', 'codex'), settings['native_launcher_argv'], declared['launcher'])
    from scidiscovery import archive_io as files
    path = files.check_path(Path(root) / MANIFEST)
    manifest = c.read(c.regular(path))
    expected = declared.get('manifest')
    if (expected != dict(path=MANIFEST, sha256=c.digest(path)) or manifest.get('version') != 1
            or manifest.get('sets') != declared['sets'] or stat.S_IMODE(path.stat().st_mode) != 0o400):
        raise ValueError('Frozen Skill manifest changed')
    names = {item for row in declared['sets'].values() for item in row['items']}
    if set(manifest['objects']) != names:
        raise ValueError('Frozen Skill object inventory changed')
    for tree in manifest['objects'].values():
        if tree['tree_sha256'] != _hash({key: tree[key] for key in ('files', 'directories', 'metadata')}):
            raise ValueError('Frozen Skill tree identity changed')
        directory = Path(root) / DIRECTORY / 'objects' / tree['tree_sha256']
        if any(stat.S_IMODE(files.check_path(path).stat().st_mode) != 0o500
                for path in [directory, *[directory / relative for relative in tree['directories']]]):
            raise ValueError('Frozen Skill directory permissions changed')
        for row in tree['files']:
            relative = PurePosixPath(row['path'])
            if (relative.is_absolute() or '..' in relative.parts or relative.as_posix() != row['path']
                    or row['path'] == '.'):
                raise ValueError('Frozen Skill file path changed')
            path = files.check_path(directory / row['path'])
            if stat.S_IMODE(c.regular(path).stat().st_mode) != 0o400:
                raise ValueError('Frozen Skill object permissions changed')
        if _tree(directory) != {key: tree[key] for key in ('files', 'directories', 'metadata')}:
            raise ValueError('Frozen Skill tree gained or lost files')
    return manifest


def binding(settings, job):
    declared = settings.get('skills')
    if declared is None:
        return None
    from . import profiles
    profile = profiles.normalize(settings.get('role_profiles', {}), settings,
        settings.get('worker_profiles'), declared['sets'])[job['role']]
    name = profile.get('skill_set')
    items = declared['sets'][name]['items'] if name is not None else []
    return dict(version=1, role=job['role'], worker=profile.get('worker'), skill_set=name,
        configuration_revision=job.get('configuration_revision'),
        selection_sha256=_hash(dict(manifest=declared['manifest'], items=items)))


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
    return {item: Path(root) / DIRECTORY / 'objects' / manifest['objects'][item]['tree_sha256']
            for item in (manifest['sets'][name]['items'] if name is not None else [])}


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
    """Link child-owned immutable objects from its already retained fork snapshot."""
    if settings.get('skills') is None:
        return
    from scidiscovery import archive_io as files
    from . import originals
    source = originals.selected_source(root)
    manifest_path = originals.resolve_ref(source, dict(path=MANIFEST))
    if c.digest(manifest_path) != settings['skills']['manifest']['sha256']:
        raise ValueError('Inherited Skill manifest changed')
    manifest = c.read(manifest_path)
    for tree in manifest['objects'].values():
        base = Path(root) / DIRECTORY / 'objects' / tree['tree_sha256']
        for relative in tree['directories']:
            (base / relative).mkdir(parents=True, exist_ok=True)
    paths = [MANIFEST, *[DIRECTORY + '/objects/' + tree['tree_sha256'] + '/' + row['path']
        for tree in manifest['objects'].values() for row in tree['files']]]
    for relative in dict.fromkeys(paths):
        source_path = originals.resolve_ref(source, dict(path=relative))
        files.link_verified(source_path, Path(root) / relative, dict(kind='file', **files.hash_file(source_path)))
    for tree in manifest['objects'].values():
        _readonly_directories(Path(root) / DIRECTORY / 'objects' / tree['tree_sha256'])
    validate(root, settings)


def native_mounts(root, job):
    """Exact readonly inputs for a registered native namespace only.

    The caller must not pass this list to scientific executor subprocesses.
    Neither settings/manifest nor the whole private object store is supplied.
    """
    from . import configuration_updates
    settings = configuration_updates.for_job(root, job)
    targets = project(root, settings, job, rebuild=False)
    if targets is None:
        return []
    projection = c.workspace(Path(root), job['step']) / '.agents/skills'
    return [(str(path), str(path)) for path in dict.fromkeys([projection, *targets.values()])]
