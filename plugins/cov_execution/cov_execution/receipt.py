"""Cov's independent receipt transport; ordinary stdio remains execution logs.

This file is also invoked by the task Python and therefore uses only stdlib.
The public runner profile captures its actual rec object at one confirmed output
point. Candidate prints never select or manufacture that output point.
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

MAX_BYTES = 256_000
MAX_SOURCE_BYTES = 32 * 1024 * 1024  # Existing managed payload allowance.
ENV_KEYS = ('SCID_COV_RECEIPT_PATH','SCID_COV_CALL_ID','SCID_COV_ACCEPTED_SHA256','SCID_COV_SOURCE_SHA256')


def encoded(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n').encode()


def original(path,limit=None):
    path=Path(path)
    if not path.is_absolute() or path.resolve()!=path or not path.is_file():
        raise ValueError('Cov receipt requires an exact original executable source')
    size=path.stat().st_size
    if limit is not None and size>limit:raise ValueError('Cov receipt original exceeds its existing read bound')
    digest=hashlib.sha256();observed=0
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            observed+=len(chunk)
            if limit is not None and observed>limit:raise ValueError('Cov receipt original exceeds its existing read bound')
            digest.update(chunk)
    if observed!=size:raise ValueError('Cov receipt original changed while being read')
    return dict(path=str(path),sha256=digest.hexdigest(),size_bytes=size)


def _shape(nodes):
    return [ast.dump(node,include_attributes=False) for node in nodes]


def runner_profile(path):
    proof=original(path);tree=ast.parse(Path(path).read_bytes(),filename=str(path))
    main=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='main']
    expected=ast.parse("with open(RESULTS_LOG, 'a') as f:\n f.write(json.dumps(rec) + '\\n')\nprint(json.dumps(rec, indent=2))\nsys.exit(0 if rec.get('ok') else 1)\n").body
    if len(main)!=1 or _shape(main[0].body[-3:])!=_shape(expected):
        raise ValueError('Public runner has no qualified independent receipt output point')
    return dict(proof,output_line=main[0].body[-2].lineno)


def profile(argv,channel):
    # A declared file backend can implement the receipt env interface directly.
    # Only the exact public cmd_run shape opts into the frozen runner adapter.
    if channel=='backend_file':return dict(version=1,channel=channel)
    if channel!='public_runner_main':raise ValueError('Explicit independent cov receipt channel required')
    sources=[Path(arg) for arg in argv[1:] if arg.endswith('.py') and Path(arg).is_file()]
    if len(sources)!=1:raise ValueError('Public receipt profile requires one exact CLI source')
    if argv not in ([argv[0],str(sources[0])],[argv[0],'-I',str(sources[0])],
            [argv[0],'-B','-I',str(sources[0])]):
        raise ValueError('Public receipt profile does not qualify additional interpreter or CLI flags')
    backend=original(sources[0]);tree=ast.parse(sources[0].read_bytes(),filename=str(sources[0]))
    cmd=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='cmd_run']
    expected=ast.parse("RUN_DIR.mkdir(parents=True, exist_ok=True)\nrc = subprocess.call([sys.executable, str(RUNNER), str(script)])\nsys.exit(rc)\n").body
    if len(cmd)!=1 or _shape(cmd[0].body[-3:])!=_shape(expected):
        raise ValueError('Public exp run has no qualified runner dispatch')
    runner=Path(os.environ.get('EXP_RUNNER','/opt/exp/runner.py'))
    return dict(version=1,channel='public_runner_main',backend=backend,runner=runner_profile(runner))


def envelope(accepted,row):
    return dict(version=1,call_id=accepted['call_id'],accepted_sha256=hashlib.sha256(encoded(accepted)).hexdigest(),
        source_sha256=accepted['script']['sha256'],row=row)


def environment(accepted,directory):
    value=envelope(accepted,None)
    return dict(zip(ENV_KEYS,(str(directory/'receipt.json'),accepted['call_id'],value['accepted_sha256'],value['source_sha256'])))


def _runner(accepted,directory):
    transport=accepted['receipt_transport'];runner=transport['runner']
    if original(transport['backend']['path'])!=transport['backend'] or runner_profile(runner['path'])!=runner:
        raise ValueError('Frozen public receipt executable changed')
    spec=importlib.util.spec_from_file_location('_scid_public_cov_runner',runner['path'])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    owner=module.main.__code__;published=False
    def receipt_print(*args,**kwargs):
        nonlocal published
        frame=sys._getframe(1)
        if frame.f_code is owner and frame.f_globals is module.__dict__ and frame.f_lineno==runner['output_line']:
            if published:raise ValueError('Public runner repeated its exact receipt output')
            row=frame.f_locals.get('rec')
            if not isinstance(row,dict):raise ValueError('Public runner output has no actual record object')
            data=encoded(envelope(accepted,row))
            pending=directory/'receipt.pending'
            with pending.open('xb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            pending.chmod(0o400)
            os.link(pending,directory/'receipt.json')  # Atomic, exclusive publication.
            pending.unlink();published=True
        builtins.print(*args,**kwargs)
    module.print=receipt_print
    sys.argv=[runner['path'],accepted['source_script']]
    # Preserve the public script interpreter/import context; no scoring changes.
    sys.path.insert(0,str(Path(runner['path']).parent))
    module.main()


def science_runner(path):
    """Trusted row capture with scientific inputs only; host adds identities.

    The launcher verifies the frozen runner and source before entering this
    namespace. No accepted call, authority, or checksum is supplied here.
    """
    path=Path(path)
    if path.resolve()!=path or not path.is_file() or path.stat().st_size>16_000:
        raise ValueError('Exact bounded scientific invocation required')
    invocation=json.loads(path.read_bytes())
    if (not isinstance(invocation,dict) or set(invocation)!={'runner','source','output_line','output'}
            or type(invocation['output_line']) is not int or invocation['output_line']<=0):
        raise ValueError('Scientific runner invocation is malformed')
    spec=importlib.util.spec_from_file_location('_scid_public_cov_runner',invocation['runner'])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    owner=module.main.__code__;published=False
    def receipt_print(*args,**kwargs):
        nonlocal published
        frame=sys._getframe(1)
        if frame.f_code is owner and frame.f_globals is module.__dict__ and frame.f_lineno==invocation['output_line']:
            if published:raise ValueError('Public runner repeated its scientific result output')
            row=frame.f_locals.get('rec')
            if not isinstance(row,dict):raise ValueError('Public runner has no actual result row')
            data=encoded(row)
            if len(data)>MAX_BYTES:raise ValueError('Public runner result exceeds its declared bound')
            with Path(invocation['output']).open('xb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            published=True
        builtins.print(*args,**kwargs)
    module.print=receipt_print
    sys.argv=[invocation['runner'],invocation['source']]
    sys.path.insert(0,str(Path(invocation['runner']).parent))
    module.main()


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--science-runner':return science_runner(sys.argv[2])
    if len(sys.argv)!=2:raise ValueError('One exact accepted call original is required')
    path=Path(sys.argv[1]);directory=path.parent
    proof=original(path);accepted=json.loads(path.read_bytes())
    if path.name!='accepted.json' or directory.name!=accepted['call_id']:
        raise ValueError('Receipt transport changed its accepted call')
    if proof['sha256']!=hashlib.sha256(encoded(accepted)).hexdigest():
        raise ValueError('Receipt transport acceptance is not canonical')
    source=original(accepted['source_script'])
    if (source['sha256']!=accepted['prepared']['sha256'] or source['sha256']!=accepted['script']['sha256']
            or source['size_bytes']!=accepted['prepared']['size_bytes'] or source['size_bytes']!=accepted['script']['size_bytes']):
        raise ValueError('Receipt transport source changed before execution')
    transport=accepted['receipt_transport']
    if transport.get('version')!=1:raise ValueError('Unsupported independent receipt transport')
    if (directory/'receipt.json').exists():raise ValueError('Accepted call already has an independent receipt')
    if transport['channel']=='public_runner_main':return _runner(accepted,directory)
    if transport['channel']!='backend_file':raise ValueError('Unknown independent receipt transport')
    env=dict(os.environ,**environment(accepted,directory))
    return subprocess.call(accepted['command'],cwd=accepted.get('cwd'),env=env)


if __name__=='__main__':
    raise SystemExit(main())
