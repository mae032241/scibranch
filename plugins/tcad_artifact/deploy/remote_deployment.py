"""Standalone Python 3.6 remote half of the local installation transaction."""
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def binding(path):
    if path.is_symlink():
        raise ValueError("deployment target must not be a symlink")
    if not path.exists():return None
    value=path.stat()
    return [value.st_dev,value.st_ino,value.st_mode,value.st_size,value.st_mtime_ns]


def sync_directory(path):
    descriptor=os.open(str(path),os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def prepare_publication(path, candidate, token, mode):
    temporary=path.with_name(path.name+".deploy-"+token)
    descriptor=os.open(str(temporary),os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),mode)
    with os.fdopen(descriptor,"wb") as output, candidate.open("rb") as source:
        shutil.copyfileobj(source,output)
        output.flush()
        os.fchmod(output.fileno(),mode)
        os.fsync(output.fileno())
    sync_directory(path.parent)
    return temporary


def write(path, raw, mode=0o600):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))
    sync_directory(path.parent)


def probe(runner, config):
    result = {}
    for name in ("tcad_capabilities", "tcad_execution_policy", "tcad_submission_protocol"):
        request = {"schema_version": 2, "operation": "rpc", "payload": {"request": {
            "jsonrpc": "2.0", "id": "deployment", "method": "tools/call",
            "params": {"name": name, "arguments": {}}}}}
        completed = subprocess.run([sys.executable, str(runner), "--config", str(config)],
            input=json.dumps(request).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if completed.returncode:
            raise RuntimeError("Remote runner/config validation failed: " + completed.stderr.decode(errors="replace")[-1000:])
        value = json.loads(completed.stdout)
        response = value.get("payload", {}).get("response", {})
        if value.get("ok") is not True or "error" in response:
            raise RuntimeError("Remote capability/policy/submission protocol discovery failed during deployment")
        result[name] = response["result"]["structuredContent"]
    if not result["tcad_capabilities"].get("capabilities"):
        raise RuntimeError("Remote runner has no capabilities")
    if not {"agent_execution_policy", "runner", "debug"}.issubset(result["tcad_execution_policy"]):
        raise RuntimeError("Remote policy discovery is incomplete")
    if result["tcad_submission_protocol"] != {"version": 2, "submit": "tcad_submit_retirable", "retire": "tcad_retire_submission"}:
        raise RuntimeError("Remote submission/retirement protocol is incompatible")
    return {"capabilities": len(result["tcad_capabilities"]["capabilities"]), "policy_available": True}


def require_idle(config):
    value = json.loads(config.read_bytes())
    root = Path(value["result_root"])
    if root.exists() and any((p / "submitted_at").exists() and not (p / "done").exists()
                             for p in root.iterdir() if p.is_dir()):
        raise RuntimeError("Remote TCAD jobs may still be active; deployment stopped")


def deploy(request):
    runner, config = Path(request["runner"]), Path(request["config"])
    root = runner.parent.parent
    if (not runner.is_absolute() or runner != root / "bin/scidiscovery-tcad-ssh-runner"
            or config != root / "config/runner.json"):
        raise ValueError("Remote deployment requires canonical runner/config paths")
    token = request["transaction"]
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise ValueError("Invalid deployment transaction identity")
    directory = root / ".deployments"
    if directory.is_symlink():
        raise ValueError("Remote deployment directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stage = directory / token
    phase = request["phase"]
    with (directory / "lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if phase == "prepare":
            stage.mkdir(mode=0o700)
            originals = {"runner": binding(runner), "config": binding(config)}
            if config.exists():
                require_idle(config)
            raw = (base64.b64decode(request["configuration"], validate=True)
                   if request.get("configuration") is not None else config.read_bytes())
            value = json.loads(raw)
            policy = value.get("agent_execution_policy", {})
            if "max_total_wall_time_seconds" not in policy and request.get("policy_defaults"):
                policy["max_total_wall_time_seconds"] = request["policy_defaults"]["max_total_wall_time_seconds"]
                raw = json.dumps(value, sort_keys=True).encode()
            debug = value.get("debug", {})
            if "budget_scope" not in debug and request.get("debug_defaults"):
                debug["budget_scope"] = request["debug_defaults"]["budget_scope"]
                raw = json.dumps(value, sort_keys=True).encode()
            write(stage / "runner.candidate", base64.b64decode(request["source"], validate=True), 0o750)
            write(stage / "config.candidate", raw)
            # Probe both protocols before replacing either existing file.
            health = probe(stage / "runner.candidate", stage / "config.candidate")
            for key, path in (("runner", runner), ("config", config)):
                if originals[key] is not None:
                    shutil.copy2(str(path), str(stage / (key + ".before")))
            manifest = {"state": "prepared", "originals": originals,
                "candidates": {key: binding(stage / (key + ".candidate")) for key in originals},
                "modes": {"runner": runner.stat().st_mode & 0o777 if runner.exists() else 0o750,
                    "config": config.stat().st_mode & 0o777 if config.exists() else 0o640}}
            write(stage / "manifest.json", json.dumps(manifest).encode())
            return dict(health, state="prepared")
        if not (stage / "manifest.json").exists() and phase == "rollback":
            return {"state": "not_activated"}
        manifest = json.loads((stage / "manifest.json").read_bytes())
        if phase == "activate":
            if manifest["state"] != "prepared":
                raise RuntimeError("Remote deployment is not prepared")
            for key, path in (("runner", runner), ("config", config)):
                if binding(path) != manifest["originals"][key]:
                    raise RuntimeError("Remote deployment target changed after preparation")
            if config.exists():
                require_idle(config)
            manifest["state"] = "activating"
            write(stage / "manifest.json", json.dumps(manifest).encode())
            manifest["published"]={}
            manifest["publication_paths"]={}
            for key, path in (("config", config), ("runner", runner)):
                path.parent.mkdir(parents=True, exist_ok=True)
                candidate = stage / (key + ".candidate")
                if binding(candidate) != manifest["candidates"][key]:
                    raise RuntimeError("Remote staged candidate changed")
                temporary=prepare_publication(path,candidate,token,manifest["modes"][key])
                manifest["published"][key]=binding(temporary)
                manifest["publication_paths"][key]=str(temporary)
                # The new inode is durably owned before it can replace the target.
                write(stage/"manifest.json",json.dumps(manifest).encode())
                os.replace(str(temporary),str(path))
                sync_directory(path.parent)
            health = probe(runner, config)
            manifest["state"] = "activated"
        elif phase == "rollback":
            if manifest["state"] in ("activating", "activated", "complete"):
                for key, path in (("runner", runner), ("config", config)):
                    if binding(path) not in (manifest["originals"][key], manifest.get("published",{}).get(key)):
                        raise RuntimeError("Remote rollback conflicts with a later change")
                for key, path in (("config", config), ("runner", runner)):
                    if manifest["originals"][key] is None:
                        if path.exists(): path.unlink()
                    else:
                        write(path, (stage / (key + ".before")).read_bytes(), manifest["modes"][key])
            for key, name in manifest.get("publication_paths",{}).items():
                path=Path(name)
                target=runner if key=="runner" else config
                if path!=target.with_name(target.name+".deploy-"+token):
                    raise RuntimeError("Remote publication path changed its transaction owner")
                if path.exists() or path.is_symlink():
                    if binding(path)!=manifest["published"][key]:
                        raise RuntimeError("Remote staged publication inode changed")
                    path.unlink()
                    sync_directory(path.parent)
            manifest["state"] = "rolled_back"
            health = {}
        elif phase == "finish":
            if manifest["state"] != "activated":
                raise RuntimeError("Remote deployment was not activated")
            for key, path in (("runner", runner), ("config", config)):
                if binding(path) != manifest["published"][key]:
                    raise RuntimeError("Remote deployed file differs from this installation candidate")
            health = probe(runner, config)
            manifest["state"] = "complete"
        else:
            raise ValueError("Unknown remote deployment phase")
        write(stage / "manifest.json", json.dumps(manifest).encode())
        return dict(health, state=manifest["state"])


if __name__ == "__main__":
    print(json.dumps(deploy(json.load(sys.stdin))))
