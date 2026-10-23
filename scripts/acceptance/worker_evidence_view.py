"""Export a new science view of one operator-selected, selected public index.

Operator entry point (no model, solver, replay, or scientific qualification)::

    python scripts/acceptance/worker_evidence_view.py --index /exact/public/index.json \
        --output /exclusive/new-view

Only ``science/`` is the reader entry point. ``control/`` retains the exact input
index, every indexed original, original omission rows, and complete view bindings.
Root must mount or grant only ``science/`` to a Worker, never the output parent or
``control/``. This preparatory export does not establish an OS secrecy boundary.
The default index summarizes omissions. Its category ``details_ref`` opens small
pages of scientific missing-material facts when candidate or experiment coverage
needs checking; these pages do not expose the private mechanical inventory.
Only omission metadata has an 8 KiB default byte limit; the whole evidence index
is not size-limited by that rule. Omission detail/text pages are at most 16 KiB
of final JSON bytes, including escapes and navigation. Long strings keep a string
preview plus sibling ``*_details_ref``/``*_truncated`` fields; linked text chunks
reconstruct the complete scientific text, not the unavailable omitted original.
This is a new view, never a replacement for a frozen packet or an exact-original
claim. Scientific originals, including source code implementing hash algorithms
and scientist prose, are copied without string scrubbing. Mechanical receipts use
the fixed projections below; unsupported mechanical source shapes fail closed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re


VIEW = 'scidiscovery.worker-evidence-view.v2'
EXACT_KINDS = {'original_task', 'public_data', 'source', 'metrics',
               'confirmed_artifact', 'limitation', 'sealed_scientist'}
PROJECTED_KINDS = {'accepted_projection', 'audit_projection', 'terminal_projection',
                   'completion_projection', 'metrics_projection', 'publication_inventory'}
METRICS = ('run_id', 'started_at', 'metric', 'ok', 'val_acc', 'val_score',
           'audit_score', 'audit_gap', 'leakage_flag', 'audit_note', 'wall_seconds')
MECHANICAL_FIELDS = {'source_ref', 'source_original_ref',
                     'artifact_ref', 'model_ref', 'job_ref', 'execution_key',
                     'candidate_id', 'call_id', 'operation_id', 'prepared', 'snapshot_files',
                     'public_manifest_ref', 'task_python_ref', 'known_terminal_experiments',
                     'candidate_recommendation', 'candidate_comparison'}
OMISSIONS = {
    'binary_model_private_filenames': (
        'trained binary models', 'Original trained binary models',
        'Binary originals embed private host filenames and are withheld from this view.'),
    'backend_control_intent': (
        'backend control records', 'Original backend submission intents',
        'Execution intents contain host control bindings and are withheld from this view.'),
    'stdout_contains_private_host_paths': (
        'raw execution logs', 'Original unprojected execution logs',
        'Raw logs contain private host paths and are withheld from this view.'),
}
OMISSION_PAGE_SIZE = 12
OMISSIONS_SUMMARY_MAX_BYTES = 8 * 1024
OMISSION_PAGE_MAX_BYTES = 16 * 1024
OMISSION_PREVIEW_MAX_BYTES = 512


def _json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON field in selected evidence')
            value[key] = item
        return value

    def invalid(value):
        raise ValueError('Non-finite JSON number in selected evidence')

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def _encoded(value):
    return (json.dumps(value, indent=2, allow_nan=False) + '\n').encode()


def _text_prefix(text, render, byte_limit):
    """Largest code-point prefix whose final encoded wrapper fits the limit."""
    low, high = 0, min(len(text), byte_limit)
    while low < high:
        middle = (low + high + 1) // 2
        if len(_encoded(render(text[:middle]))) <= byte_limit:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _omission_page(rows, start, key, prefix, number, byte_limit, *, next_key='next'):
    stop, value = start, None
    while stop < len(rows) and stop - start < OMISSION_PAGE_SIZE:
        candidate = {key: rows[start:stop + 1]}
        if stop + 1 < len(rows):
            candidate[next_key] = f'{prefix}/{number + 1:03d}.json'
        if len(_encoded(candidate)) > byte_limit:
            break
        stop, value = stop + 1, candidate
    if value is None:
        raise ValueError('Bounded omission fields cannot fit their encoded page')
    return value, stop


def _alias(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\x00' in value
            or PurePosixPath(value).is_absolute() or PurePosixPath(value).as_posix() != value
            or any(part in {'.', '..'} for part in PurePosixPath(value).parts)
            or PurePosixPath(value).parts[0] == 'index.json'):
        raise ValueError('Canonical relative evidence alias required')
    return value


def _fields(value, names):
    if not isinstance(value, dict):
        raise ValueError('Typed object evidence required')
    return {key: item for key, item in value.items() if key in names}


def _metrics(value):
    result = _fields(value, METRICS)
    if not result or any(isinstance(item, (dict, list)) for item in result.values()):
        raise ValueError('Scalar scientific metric fields required')
    return result


def _status(value):
    if (not isinstance(value, str) or not re.fullmatch('[a-z][a-z0-9_]{0,95}', value)
            or re.search('[0-9a-f]{64}', value)):
        raise ValueError('Bounded public status enum required')
    return value


class _Projection:
    def __init__(self):
        self.references = {}
        self.omission_details = []
        self.omission_inventory_count = 0
        self.omission_text_count = 0

    def reference(self, kind, original):
        if not isinstance(original, (str, int)) or isinstance(original, bool):
            raise ValueError('Typed original reference required')
        key = kind, str(original)
        if key not in self.references:
            count = sum(item[0] == kind for item in self.references) + 1
            self.references[key] = f'{kind} {count}'
        return self.references[key]

    def identities(self, original):
        return {name.removesuffix('_id') + '_ref': self.reference(kind, original[name])
                for name, kind in (('call_id', 'Call'), ('candidate_id', 'Candidate'),
                                   ('operation_id', 'Operation')) if name in original}

    def facts(self, kind, original):
        if kind == 'metrics_projection':
            return [_metrics(row) for row in original]
        if kind == 'audit_projection':
            return _metrics(original)
        if kind == 'accepted_projection':
            value = dict(self.identities(original), status=_status(original.get('status', 'accepted_original_retained')))
            materials = [original['prepared']] if isinstance(original.get('prepared'), dict) else []
            materials += original.get('snapshot_files', [])
            value['materials'] = [dict(material_ref=self.reference('Material', row.get('path', row.get('name'))))
                                  for row in materials]
            return value
        if kind == 'terminal_projection':
            if original.get('status') not in {'known_success', 'known_failed', 'unknown'}:
                raise ValueError('An original terminal status is required')
            value = dict(self.identities(original), **_fields(original, ('status', 'returncode', 'wall_seconds')))
            value['outputs'] = [dict(material_ref=self.reference('Material', row.get('path', row.get('name'))),
                                     **_fields(row, ('media_type',))) for row in original.get('outputs', [])]
            return value
        if kind == 'completion_projection':
            value = _fields(original, ('status', 'goal_completion', 'observation_scope'))
            if not value:
                raise ValueError('Typed completion observation required')
            for item in value.values():
                _status(item)
            for name in ('last_confirmed', 'current_actual'):
                row = original.get(name)
                value[name] = None if row is None else dict(self.identities(row),
                    **_fields(row, ('status', 'cleanup_confirmed')))
                if row is not None:
                    if 'status' in row:
                        _status(row['status'])
                    if 'cleanup_confirmed' in row and type(row['cleanup_confirmed']) is not bool:
                        raise ValueError('Typed cleanup flag required')
            return value
        if kind == 'sealed_scientist':
            allowed = {'summary', 'scientific_result', 'limitations', 'implementation_selection',
                'next_action', 'route_reason', 'search_plan', 'mechanism_findings', 'stage_intent',
                'search_review', 'candidate_recommendation', 'progress_assessment', 'decision_request',
                'portfolio_review', 'candidate_comparison'}
            if set(original) - allowed:
                raise ValueError('Unsupported mechanical scientist producer')
            value = _fields(original, allowed - {'candidate_recommendation', 'candidate_comparison'})
            recommendation = original.get('candidate_recommendation')
            value['candidate_recommendation_ref'] = None if recommendation is None else self.reference('Candidate', recommendation)
            comparison = original.get('candidate_comparison')
            if comparison is not None:
                if (not isinstance(comparison, dict) or set(comparison) != {'rows', 'rationale', 'evidence_steps'}
                        or comparison['rationale'] is not None and not isinstance(comparison['rationale'], str)
                        or not isinstance(comparison['evidence_steps'], list)
                        or any(type(step) is not int or step < 1 for step in comparison['evidence_steps'])):
                    raise ValueError('Unsupported candidate comparison producer')
                value['candidate_comparison'] = dict(rows=[dict(candidate_ref=self.reference('Candidate', row['candidate_id']),
                    **_fields(row, ('key_risk', 'decision'))) for row in comparison['rows']])
                value['candidate_comparison'].update(_fields(comparison, ('rationale', 'evidence_steps')))
            return value
        if kind == 'limitation' and 'known_terminal_experiments' in original:
            allowed = {'version', 'known_terminal_experiments', 'known_terminal_record_count',
                'original_unresolved_submission_count', 'unresolved_submission_is_completed',
                'unresolved_submission_replay', 'original_unsealed_W_conclusion_included',
                'old_model_pickles_included', 'scientifically_distinct_count', 'stopping_condition'}
            if set(original) - allowed:
                raise ValueError('Unsupported carried experiment inventory producer')
            value = _fields(original, allowed - {'known_terminal_experiments'})
            value['known_terminal_experiments'] = [_fields(row, ('alias', 'source', 'facts'))
                                                  for row in original['known_terminal_experiments']]
            return value
        if kind == 'limitation' and 'source_ref' in original:
            allowed = {'source_ref', 'known_terminal', 'public_run', 'public_audit',
                       'scientific_distinctness', 'qualification'}
            if set(original) - allowed:
                raise ValueError('Unsupported mechanical limitation producer')
            value = _fields(original, ('known_terminal', 'scientific_distinctness', 'qualification'))
            for name in ('public_run', 'public_audit'):
                if name in original:
                    value[name] = _metrics(original[name])
            return value
        if kind == 'metrics' and set(original) == {
                'source_ref', 'public_manifest_ref', 'task_python_ref', 'qualification'}:
            return _fields(original, ('qualification',))
        if kind == 'metrics' and 'candidate_id' in original:
            # Fixed numerical-reproduction report; do not infer a generic JSON scrub.
            allowed = {'candidate_id', 'passed', 'exact_bundle_objects_verified',
                'source_public_metric_reproduction', 'metric_abs_tolerance', 'model_public_reproduction',
                'timestamped_receipt_and_retrained_pickle_byte_identity_required', 'qualification'}
            if set(original) - allowed or 'model_public_reproduction' not in original:
                raise ValueError('Unsupported mechanical metrics producer')
            model = original['model_public_reproduction']
            model_fields = {'passed', 'public_validation_scores', 'max_absolute_prediction_difference',
                'prediction_abs_tolerance', 'prediction_rel_tolerance', 'rows', 'hidden_scoring', 'qualification'}
            source = original.get('source_public_metric_reproduction', {})
            if (not isinstance(model, dict) or set(model) - model_fields or not isinstance(source, dict)
                    or set(source) - {'val_score', 'audit_score', 'leakage_flag_false'}):
                raise ValueError('Unsupported numerical-reproduction producer fields')
            return dict(self.identities(original), **_fields(original, allowed - {'candidate_id'}))
        raise ValueError('Unsupported mechanical evidence producer')

    def omission_text(self, key, text, prefix):
        if not isinstance(text, str):
            raise ValueError('Known omission text fields must be strings')
        if len(_encoded(text)) <= OMISSION_PREVIEW_MAX_BYTES:
            return {key: text}
        self.omission_text_count += 1
        prefix = f'{prefix}/text-{self.omission_text_count:03d}'
        remaining, number = text, 1
        while remaining:
            final = dict(text=remaining) if len(remaining) <= OMISSION_PAGE_MAX_BYTES else None
            if final is not None and len(_encoded(final)) <= OMISSION_PAGE_MAX_BYTES:
                value = final
                chunk = remaining
            else:
                next_ref = f'{prefix}/{number + 1:03d}.json'
                chunk = _text_prefix(remaining, lambda part: dict(text=part, next=next_ref), OMISSION_PAGE_MAX_BYTES)
                if not chunk:
                    raise ValueError('Omission text cannot fit its encoded page')
                value = dict(text=chunk, next=next_ref)
            self.omission_details.append((f'{prefix}/{number:03d}.json', _encoded(value)))
            remaining, number = remaining[len(chunk):], number + 1
        return {key: _text_prefix(text, lambda part: part, OMISSION_PREVIEW_MAX_BYTES),
                key + '_details_ref': f'{prefix}/001.json', key + '_truncated': True}

    def omission_index(self, summaries):
        """Keep the entire default omission block bounded, including navigation."""
        value = dict(omissions=summaries)
        if len(_encoded(value)) <= OMISSIONS_SUMMARY_MAX_BYTES:
            return value
        prefix = 'omissions/summaries'
        value, stop = _omission_page(summaries, 0, 'omissions', prefix, 0,
                                    OMISSIONS_SUMMARY_MAX_BYTES, next_key='omissions_next')
        number = 1
        while stop < len(summaries):
            page, stop = _omission_page(summaries, stop, 'omissions', prefix, number, OMISSION_PAGE_MAX_BYTES)
            self.omission_details.append((f'{prefix}/{number:03d}.json', _encoded(page)))
            number += 1
        return value

    def omissions(self, original):
        if not isinstance(original, dict) or not isinstance(original.get('withheld'), list):
            raise ValueError('Typed original omission inventory required')
        self.omission_inventory_count += 1
        inventory_prefix = f'omissions/inventory-{self.omission_inventory_count:03d}'
        text = {}
        for key in ('task_completion', 'limitation'):
            if key in original:
                text.update(self.omission_text(key, original[key], inventory_prefix))
        grouped = {}
        for row in original['withheld']:
            if (not isinstance(row, dict) or not isinstance(row.get('reason'), str)
                    or row['reason'] not in OMISSIONS):
                raise ValueError('Unsupported omission category')
            _alias(row['public_alias'])
            if type(row.get('size_bytes')) is not int or row['size_bytes'] < 0:
                raise ValueError('Exact original omission binding required')
            # public_alias is the producer's scientific name, never its private path.
            human_alias = '/'.join(self.reference('Material', part) if re.search('[0-9a-fA-F]{64}', part)
                                   else part for part in PurePosixPath(row['public_alias']).parts)
            scope = str(PurePosixPath(human_alias).parent)
            material = dict(category=OMISSIONS[row['reason']][0], reason=OMISSIONS[row['reason']][2],
                            availability='not_available_in_this_view')
            material.update(self.omission_text('material_label', human_alias, inventory_prefix))
            material.update(self.omission_text('scope', scope if scope != '.' else 'Retained scientific materials', inventory_prefix))
            grouped.setdefault(row['reason'], []).append(material)
        facts = original.get('reproduction_facts', {})
        allowed_facts = {'numerical_reproduction_passed', 'first_overall_postcheck_passed',
                         'subsequent_exact_cache_restoration_verified', 'science_replay_repeated'}
        if not isinstance(facts, dict) or set(facts) - allowed_facts or any(type(value) is not bool for value in facts.values()):
            raise ValueError('Unsupported omission reproduction facts')
        categories = []
        for reason, rows in sorted(grouped.items()):
            prefix = f'{inventory_prefix}/{reason}'
            start, number = 0, 1
            while start < len(rows):
                page, start = _omission_page(rows, start, 'materials', prefix, number, OMISSION_PAGE_MAX_BYTES)
                self.omission_details.append((f'{prefix}/{number:03d}.json', _encoded(page)))
                number += 1
            categories.append(dict(category=OMISSIONS[reason][0],
                scope='Retained scientific materials and execution evidence',
                reason=OMISSIONS[reason][2], missing_material=OMISSIONS[reason][1],
                details_ref=f'{prefix}/001.json',
                when_to_open='Open details when missing-material coverage for a candidate or experiment could affect your assessment.'))
        return dict(categories=categories, **_fields(original, ('reproduction_facts',)), **text)


def export_view(index_path, output_dir):
    """Read one explicit index and sized regular originals into a fresh view."""
    index_path, output_dir = Path(index_path).absolute(), Path(output_dir).absolute()
    if index_path.resolve() != index_path or not index_path.is_file():
        raise ValueError('Exact original index required')
    index_raw = index_path.read_bytes()
    index = _json(index_raw)
    if index.get('version') != 2 or not isinstance(index.get('evidence'), list) or not index['evidence']:
        raise ValueError('Explicit original public evidence index version 2 required')
    if (output_dir.resolve() != output_dir or output_dir.exists()
            or output_dir.is_relative_to(index_path.parent)):
        raise ValueError('Exclusive new view outside the original packet required')
    originals, aliases = [], set()
    for row in index['evidence']:
        alias = _alias(row['alias'])
        if alias in aliases or any(alias.startswith(old + '/') or old.startswith(alias + '/') for old in aliases):
            raise ValueError('Distinct nonoverlapping evidence aliases required')
        aliases.add(alias)
        if row['kind'] not in EXACT_KINDS | PROJECTED_KINDS:
            raise ValueError('Unsupported evidence kind; no implicit original-byte qualification')
        source = index_path.parent / alias
        if source.resolve() != source or not source.is_file():
            raise ValueError('Indexed original is missing or redirected')
        raw = source.read_bytes()
        if type(row['size_bytes']) is not int or len(raw) != row['size_bytes']:
            raise ValueError('Indexed original bytes differ')
        originals.append((row, raw))

    projection, science, bindings, omissions = _Projection(), [], [], []
    for number, (row, raw) in enumerate(originals, 1):
        alias, kind = row['alias'], row['kind']
        detail_start = len(projection.omission_details)
        value, summary = None, 'Exact scientific original bytes; no new scientific qualification.'
        if kind in PROJECTED_KINDS or kind in {'metrics', 'limitation', 'sealed_scientist'}:
            if kind == 'metrics_projection':
                original = _json(raw) if raw.lstrip().startswith(b'[') else [
                    _json(line) for line in raw.splitlines() if line.strip()]
            else:
                original = _json(raw) if kind in PROJECTED_KINDS or raw.lstrip().startswith(b'{') else None
            if kind == 'publication_inventory':
                omissions.append(projection.omissions(original))
            elif kind in PROJECTED_KINDS:
                value = projection.facts(kind, original)
            elif isinstance(original, dict) and set(original) & MECHANICAL_FIELDS:
                value = projection.facts(kind, original)
        # An indexed receipt cannot become raw scientific bytes through relabeling.
        fixed = {'accepted.json': 'accepted_projection', 'completed.json': 'terminal_projection',
                 'submitted.json': 'audit_projection', 'results.jsonl': 'metrics_projection',
                 'final-result.json': 'completion_projection', 'closeout-outcome.json': 'completion_projection'}
        if PurePosixPath(alias).name in fixed and fixed[PurePosixPath(alias).name] != kind:
            raise ValueError('Mechanical receipt requires its typed projection kind')
        binding = dict(original_alias=alias, kind=kind, source_ref=dict(path=str(index_path.parent / alias),size_bytes=len(raw)),
                       source_original_ref=row.get('source_original_ref'))
        if kind != 'publication_inventory':
            # Preserve source directory relationships unless the alias itself exposes a digest.
            public_alias = alias if not re.search('[0-9a-fA-F]{64}', alias) else f'evidence/E{number:04d}.json'
            public_raw = raw if value is None else _encoded(value)
            if value is not None:
                summary = 'Scientific view of a selected original; original bytes retained privately.'
            science.append((public_alias, public_raw, dict(alias=public_alias, kind=kind, summary=summary)))
            binding.update(science_alias=public_alias, size_bytes=len(public_raw),
                           relation='exact_bytes' if value is None else 'typed_scientific_projection')
        else:
            binding.update(relation='omission_summary_and_scientific_detail_pages', omitted_originals=original['withheld'],
                science_details=[dict(alias=alias, size_bytes=len(raw))
                                 for alias, raw in projection.omission_details[detail_start:]])
        bindings.append(binding)
    navigation_start = len(projection.omission_details)
    omission_index = projection.omission_index(omissions)
    science_aliases = []
    for alias in [item[0] for item in science] + [item[0] for item in projection.omission_details]:
        if any(alias == old or alias.startswith(old + '/') or old.startswith(alias + '/') for old in science_aliases):
            raise ValueError('Projected science aliases collide')
        science_aliases.append(alias)
    public_index = dict(view=VIEW, evidence=[item[2] for item in science], **omission_index)
    control_index = dict(view=VIEW, original_index=dict(path=str(index_path), size_bytes=len(index_raw)),
        bindings=bindings, references=[dict(kind=kind, original=original, reference=reference)
            for (kind, original), reference in projection.references.items()],
        omission_navigation=[dict(alias=alias, size_bytes=len(raw))
                             for alias, raw in projection.omission_details[navigation_start:]])

    output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    control, public = output_dir / 'control', output_dir / 'science'
    control.mkdir(mode=0o700)
    public.mkdir()

    def write(directory, alias, raw, mode):
        target = directory / alias
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
        target.chmod(mode)

    write(control, 'index.json', index_raw, 0o600)
    for row, raw in originals:
        write(control / 'originals', row['alias'], raw, 0o600)
    write(control, 'bindings.json', _encoded(control_index), 0o600)
    for alias, raw, _ in science:
        write(public, alias, raw, 0o444)
    for alias, raw in projection.omission_details:
        write(public, alias, raw, 0o444)
    write(public, 'index.json', _encoded(public_index), 0o444)
    return public / 'index.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = export_view(args.index, args.output)
    print(json.dumps(dict(status='new_science_view_exported_no_scientific_verdict', science_index=str(result))))


if __name__ == '__main__':
    main()
