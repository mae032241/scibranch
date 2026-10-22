"""Read-only replay of observed candidate facts and qualification receipts over one finished instance.

Public facts only: sealed jobs, A outputs, observer snapshots and A-requested public
audit receipts. Never reads reward/official/hidden files and never writes the
instance. ``--smoke`` copies each member's exact archived artifact and the public
harness into a fresh scratch stage and runs the cov adapter smoke code in a bwrap
namespace with the frozen task interpreter (no network, no instance mounts).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from scidiscovery.research_loop import selection
from cov_execution import delivery as cov_delivery

HIDDEN = ('official', 'hidden', 'private_score', 'reward')
FIELDS = ('id', 'label', 'evaluation_key', 'metric', 'direction', 'status')


def read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def public(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if any(word in key.lower() for word in HIDDEN):
                raise ValueError('Non-public field in replay source: ' + key)
            public(item)
    elif isinstance(value, list):
        for item in value:public(item)
    return value


def prefix(root):
    """Exploration prefix the closeout boundary froze (or every sealed node)."""
    cutoff = None
    for path in sorted((root / 'control-ledger').glob('*.json')):
        event = read(path) or {}
        if event.get('kind') == 'closeout_begin':cutoff = event['source_cutoff']
    steps = []
    for path in sorted((root / 'jobs').glob('*.json')):
        job = read(path);step = job['step']
        if job.get('purpose') == 'closeout' or (cutoff is not None and step > cutoff):continue
        if (root / 'native-evidence' / f'{step:03d}/result-identity.json').is_file():steps.append((step, job['role']))
    return cutoff, steps


def records(root, steps):
    rows, contexts = [], {}
    for step, role in steps:
        observation = public(read(root / 'host-history/observations' / f'{step:03d}.json') or {})
        candidates = [{key: item[key] for key in FIELDS} for item in observation.get('candidates', [])]
        for context in observation.get('contexts', []):
            model = context.get('model') or {}
            if context.get('status') == 'known_success' and model.get('retention') == 'archived':
                contexts.setdefault(context['candidate_id'], model)
        raw = read(root / 'native-evidence' / f'{step:03d}/science-output.json') or {}
        audit = role == 'A' and raw.get('next_action') == 'audit'
        rows.append(dict(step=step, role=role, recommendation=raw.get('candidate_recommendation') if role == 'A' and not audit else None,
            coverage=(observation.get('coverage') or {}).get('status') if observation else None, candidates=candidates,
            current_candidate=None, actual_submission=None, **(dict(audit_request=True) if audit else {})))
    return rows, contexts


def receipts(root, steps):
    """A-requested public audit adapter results within the prefix (artifact-bound)."""
    found = {}
    for step, role in steps:
        outcome = read(root / 'native-evidence' / f'{step:03d}/candidate-audit/outcome.json')
        if role != 'A' or outcome is None:continue
        result = public(outcome['result'])
        if result['status'] in ('audited', 'failed') and result.get('artifact_sha256'):
            found.setdefault(outcome['candidate_id'], []).append((result['status'], result['artifact_sha256']))
    return found


def smoke(root, launcher, member, model, work):
    """Copy-only stage; the instance and its runs are never mounted."""
    if model is None:
        return 'unavailable', 'artifact_not_archived'
    stage = work / member;(stage / 'inputs/harness').mkdir(parents=True);(stage / 'scratch').mkdir()
    artifact = root / model['snapshot']
    data = artifact.read_bytes()
    if hashlib.sha256(data).hexdigest() != model['sha256']:
        raise ValueError('Archived artifact changed')
    (stage / 'inputs/artifact').write_bytes(data)
    harness = Path(launcher['public_root']) / 'environment/harness'
    for name in ('runner.py', 'meta.json', 'val.csv'):shutil.copyfile(harness / name, stage / 'inputs/harness' / name)
    python = Path(launcher['task_python'])
    command = [shutil.which('bwrap'), '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc', '--unshare-uts',
               '--die-with-parent', '--new-session', '--clearenv', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
    base = python.resolve().parent.parent
    for path in dict.fromkeys(['/usr', '/bin', '/lib', '/lib64', str(python.parent.parent), str(base / 'bin'), str(base / 'lib')]):
        if Path(path).exists():command += ['--ro-bind', path, path]
    command += ['--ro-bind', str(stage / 'inputs'), '/inputs', '--bind', str(stage / 'scratch'), '/scratch',
                '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'OPENBLAS_NUM_THREADS', '1', '--chdir', '/scratch',
                '--', str(python), '-B', '-I', '-c', cov_delivery.SMOKE]
    run = subprocess.run(command, capture_output=True, timeout=120)
    value = read(stage / 'scratch/smoke.json') or {}
    status, note = cov_delivery.smoke_status(value)
    tail = run.stderr.decode(errors='replace').strip().splitlines()[-1:] if status != 'ok' else []
    return status, note + (': ' + tail[0][:160] if tail else '')


def replay(instance, *, work=None, launcher=None):
    root = Path(instance).absolute() / 'instance'
    cutoff, steps = prefix(root)
    rows, contexts = records(root, steps)
    value = selection.shortlist(rows)
    audits = receipts(root, steps)
    table = []
    for member in value['members']:
        cid = member['candidate_id'];model = contexts.get(cid)
        status, note = smoke(root, launcher, cid, model, work) if work is not None else ('not_replayed', 'smoke_not_requested')
        prior = [state for state, digest in audits.get(cid, []) if model and digest == model['sha256']]
        audit = 'failed' if 'failed' in prior else 'passed' if prior else 'not_run'
        table.append(dict(candidate_id=cid, metric=member['metric'], evaluation_key=member['evaluation_key'], direction=member['direction'],
                          first_step=member['first_step'], smoke=status, smoke_note=note, audit=audit))
    a_rec = value['a_recommended']
    return dict(instance=Path(instance).name, source_cutoff=cutoff, prefix_steps=[f'{role}{step:03d}' for step, role in steps],
                candidate_count=value['candidate_count'], latest_a_step=value['latest_a_step'],
                a_recommended=a_rec, candidates=table, scope='Public replay; audits only from existing A-requested receipts; no target or reward claim.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instances', nargs='+', type=Path, help='Trial directories containing instance/ and launcher.json')
    parser.add_argument('--smoke', type=Path, help='Fresh scratch directory for copy-only smoke stages')
    args = parser.parse_args()
    if args.smoke is not None and args.smoke.exists():
        raise SystemExit('Smoke replay requires a fresh scratch directory')
    results = []
    for instance in args.instances:
        launcher = json.loads((instance / 'launcher.json').read_text())
        work = args.smoke / instance.name if args.smoke is not None else None
        results.append(replay(instance, work=work, launcher=launcher))
    print(json.dumps(results, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
