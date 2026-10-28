"""Read-only public covshift observer for the generic rootless history protocol.

Reads only the configured public runs directory. Does not load models, infer a
scientific recommendation, inspect hidden scores or submit anything.
"""
from pathlib import Path
import json
import math
import sys

MAX_INPUT = 256_000
MAX_ROWS = 128
MAX_SCAN_BYTES = 4 * 1024 * 1024


def read(path):
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ValueError('Exact bounded public run original required')
    return path.read_text()


def submission_original(root, runs, model, fact):
    """Use the promotion's existing source binding only for its exact generation.

    Custody is verified by the host against its retained originals; this public
    marker alone grants no immutability to an arbitrary source path.
    """
    marker=runs/'rootless-delivery-operation.json'
    if marker.exists():
        try:
            value=json.loads(read(marker))
            info=model.stat()
            generation=[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]
            origin=value.get('artifact_origin')
            if (value.get('candidate_id')==f'run-{fact.get("run_id")}'
                    and isinstance(value.get('operation_id'),str) and value['operation_id']
                    and value.get('audit')==fact and value.get('published',{}).get('run_model')==generation
                    and isinstance(origin,dict) and isinstance(origin.get('path'),str)
                    and origin.get('size_bytes')==info.st_size):
                path=Path(origin['path'])
                # External originals need their private inherited binding; the
                # public observer cannot establish that custody on its own.
                if path.is_absolute() and path.is_relative_to(root) and path.resolve()==path:
                    return path.relative_to(root).as_posix()
        except (OSError,ValueError,TypeError):
            pass
    return model.relative_to(root).as_posix()


def observe(request):
    root = Path(request['root'])
    config = request['config']
    if set(config) != {'runs', 'evaluation_key'}:
        raise ValueError('Cov observer requires runs and an explicit evaluation_key')
    relative = Path(config['runs'])
    if relative.is_absolute() or '..' in relative.parts or (root / relative).resolve() != root / relative:
        raise ValueError('Public runs must be an original instance directory')
    runs = root / relative
    results = runs / 'results.jsonl'
    rows, scanned, partial = [], 0, False
    known = set(request.get('known_candidates', []))
    if results.exists():
        if results.is_symlink() or results.resolve() != results:
            raise ValueError('Public run ledger must be an original')
        with results.open('rb') as stream:
            for line in stream:
                if not line.strip():continue
                row=json.loads(line)
                if f'run-{row["run_id"]}' not in known:rows.append(row)
    ids, candidates = set(), []
    known = set(request.get('known_candidates', []))
    for row in rows:
        run_id = row['run_id']
        if type(run_id) is not int or run_id < 1 or run_id in ids:
            raise ValueError('Run IDs must be exact and unique')
        ids.add(run_id)
        candidate_id = f'run-{run_id}'
        if candidate_id in known:
            continue
        metric = row.get('val_score')
        if metric is not None and (type(metric) not in (int, float) or not math.isfinite(metric)):
            raise ValueError('Public metric is not finite')
        # Bind a code handle only when the harness actually names an exported
        # original. Unexported scratch is unknown, even if filenames match.
        matches = [item for item in request['materials'] if item['source'] == row.get('script')]
        implementation = None
        if len(matches) == 1:
            implementation = 'history/nodes/' + '/'.join(Path(matches[0]['path']).parts[2:])
        candidates.append(dict(id=candidate_id, label=f'Public experiment {run_id}',
            implementation=implementation, evaluation_key=config['evaluation_key'],
            metric=metric, direction='max', status='ok' if row.get('ok') is True else 'failed',
            evidence=[results.relative_to(root).as_posix()]))
    actual = None
    submitted = runs / 'submitted.json'
    if submitted.exists():
        fact = json.loads(read(submitted))
        if type(fact.get('run_id')) is not int:
            raise ValueError('Submitted experiment does not name an observed exact run')
        model = runs / 'submitted_model.pkl'
        if model.is_symlink() or model.resolve() != model or not model.is_file():
            raise ValueError('Submitted model original missing')
        # Evidence refers to the public harness's actual exp submit. It does not
        # claim an external evaluator accepted this artifact or read its score.
        candidate_id = f'run-{fact["run_id"]}'
        actual = dict(candidate_id=candidate_id if fact['run_id'] in ids or candidate_id in known else None, artifact=submission_original(root,runs,model,fact),
                      evidence=[submitted.relative_to(root).as_posix()])
    return dict(candidates=candidates, current_candidate=None, actual_submission=actual,
                coverage=dict(status='partial' if partial else 'complete', note='Highest means observed public proxy only; scan/new-record limits may omit facts.' if partial else 'Complete public ledger scan at this observation boundary.'))


if __name__ == '__main__':
    json.dump(observe(json.load(sys.stdin)),sys.stdout,allow_nan=False)
