"""Current material selections into existing producer lists; no file reads."""
from pathlib import Path, PurePosixPath


def _public(row):
    return {key: value for key, value in row.items() if key != 'file_stat'}


def list_ref(facts):
    owner = facts['owner']
    return dict(origin_identity=owner.origin_identity, step=owner.step,
        path=str(Path(owner.original_root) / f'native-evidence/{owner.step:03d}/result-identity.json'))


def _index(facts):
    return {f'{facts["job"]["role"]}{facts["owner"].step:03d}-I{number:03d}': _public(row)
        for number, row in enumerate(sorted(facts['seal']['materials'], key=lambda row: row['path']), 1)}


def references(materials, *, sources):
    """Bind selected rows to their producer's unchanged scientific object IDs."""
    return _references(materials, sources=sources)[0]


def _references(materials, *, sources):
    groups, indexes, by_step, bindings = [], {}, {}, {}
    for material in materials:
        step = int(PurePosixPath(material['path']).parts[2])
        facts = sources[step]
        if step not in indexes:
            indexes[step] = {row['path']: (key, row) for key, row in _index(facts).items()}
        match = indexes[step].get(material['path'])
        if match is None or match[1] != material:
            raise ValueError('Scientific material differs from its exact producer list')
        ref = list_ref(facts)
        group = by_step.get(step)
        if group is None:
            group = dict(list_ref=ref, ids=[])
            groups.append(group)
            by_step[step] = group
        if match[0] not in group['ids']:
            group['ids'].append(match[0])
        bindings[material['path']] = dict(list_ref=ref, id=match[0])
    return groups, bindings


def materials(refs, *, sources):
    """Resolve only explicit current IDs using facts already loaded by the owner."""
    result, seen, indexes = [], set(), {}
    for group in refs:
        if (not isinstance(group, dict) or set(group) != {'list_ref', 'ids'}
                or not isinstance(group['ids'], list) or not group['ids']
                or any(not isinstance(item, str) for item in group['ids'])
                or len(set(group['ids'])) != len(group['ids'])):
            raise ValueError('Current material selection has no exact producer list and IDs')
        ref = group['list_ref']
        if not isinstance(ref, dict) or set(ref) != {'origin_identity', 'step', 'path'}:
            raise ValueError('Current material list lost its exact original producer')
        step = ref['step']
        if type(step) is not int or step < 1:
            raise ValueError('Current material list lost its exact original producer')
        facts = sources[step]
        if ref != list_ref(facts):
            raise ValueError('Current material list differs from its exact producer')
        if step not in indexes:
            indexes[step] = _index(facts)
        index = indexes[step]
        for object_id in group['ids']:
            if object_id not in index:
                raise ValueError('Current material ID is outside its exact producer list')
            material = index[object_id]
            if material['path'] in seen:
                raise ValueError('Current material selection repeats a scientific path')
            seen.add(material['path'])
            result.append(material)
    return result


def selected_record(rows, *, refs=None):
    """Keep selection labels and provenance while replacing copied descriptors."""
    bindings = {}
    for group in refs or []:
        for object_id in group['ids']:
            bindings[object_id] = dict(list_ref=group['list_ref'], id=object_id)
    result = []
    for row in rows:
        if 'material' not in row:
            result.append(dict(row))
            continue
        ref = row.get('material_ref')
        if ref is None:
            raise ValueError('Selected material requires its owner-produced original reference')
        if refs is not None and (ref.get('id') not in bindings or bindings[ref['id']] != ref):
            raise ValueError('Selected material is outside the current original selection')
        result.append({key: value for key, value in row.items() if key != 'material'})
    return result


def selected_materials(rows, *, sources):
    groups, by_step = [], {}
    for row in rows:
        ref = row['material_ref']
        step = ref['list_ref']['step']
        group = by_step.get(step)
        if group is None:
            group = dict(list_ref=ref['list_ref'], ids=[])
            groups.append(group)
            by_step[step] = group
        if group['list_ref'] != ref['list_ref']:
            raise ValueError('Selected material has conflicting exact producers')
        if ref['id'] not in group['ids']:
            group['ids'].append(ref['id'])
    resolved = iter(materials(groups, sources=sources))
    originals = {object_id: next(resolved) for group in groups for object_id in group['ids']}
    return [dict(row, material=originals[row['material_ref']['id']]) for row in rows]


def bind_selected(rows, *, sources):
    """Attach selectors once while the selection owner has producer facts."""
    _, bindings = _references([row['material'] for row in rows], sources=sources)
    return [dict(row, material_ref=bindings[row['material']['path']]) for row in rows]


def selected_refs(rows):
    return [dict(list_ref=row['material_ref']['list_ref'], ids=[row['material_ref']['id']]) for row in rows]


def job_record(job):
    """Serialize a loaded current job without repeating producer descriptors."""
    record = {key: value for key, value in job.items() if key != 'direct_materials'}
    if 'direct_materials' in job and 'material_refs' not in job:
        raise ValueError('Current job requires owner-produced material references')
    if 'selected_inputs' in job:
        record['selected_inputs'] = selected_record(job['selected_inputs'],
            refs=job['material_refs'])
    return record
