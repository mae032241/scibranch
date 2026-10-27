"""Same-prefix update boundaries without pip, model, solver, or scientific state."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "deploy/install.sh"


def shell(environment, body):
    return subprocess.run(
        ["bash", "-c", 'source "$1"\n' + body, "bash", str(SCRIPT)],
        env=environment, capture_output=True, text=True, timeout=10, check=False,
    )


@pytest.fixture
def original_install(tmp_path):
    home = tmp_path / "home"
    prefix = home / ".local/share/scidiscovery"
    binary = home / ".local/bin"
    binary.mkdir(parents=True)
    original_files = {}
    for relative, raw in (("site/scidiscovery/module.py", b"old package\n"),
                          ("site/runtime-identity.json", b"old identity fixture\n"),
                          ("skills/skill/SKILL.md", b"old skill\n")):
        path = prefix / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        original_files[path] = (raw, 0o444)
    for path in original_files:
        path.chmod(0o444)
    for relative in ("site/scidiscovery", "site", "skills/skill", "skills"):
        (prefix / relative).chmod(0o555)
    (prefix / "operator-notes.txt").write_bytes(b"unmanaged prefix content\n")
    environment = {**os.environ, "HOME": str(home), "SCID_PYTHON": sys.executable}
    for name in ("SCID_BIN_ROOT", "SCID_INSTALL_ROOT", "SCID_BACKUP_ROOT",
                 "SCID_DEPLOY_MODE", "SCID_PLUGINS"):
        environment.pop(name, None)
    result = shell(environment, '''thin_cli_wrapper scidiscovery.research_loop.cli > "$CLI_ROOT/scid-research"
thin_cli_wrapper scidiscovery.research_loop.experiments > "$CLI_ROOT/scid-experiment"
chmod 755 "$CLI_ROOT/scid-research" "$CLI_ROOT/scid-experiment"
''')
    assert result.returncode == 0, result.stderr
    for name in ("scid-research", "scid-experiment"):
        path = binary / name
        original_files[path] = (path.read_bytes(), 0o755)
    unrelated = home / "science-instance/original.csv"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"scientific original must remain untouched\n")
    environment["UNCHANGED_SCIENCE"] = str(unrelated)
    return environment, prefix, original_files


STAGED_UPDATE = '''validate_base_python() { :; }
validate_source() { :; }
install_packages() {
    PACKAGE_STAGE="$(mktemp -d "$INSTALL_ROOT/.site.new.XXXXXX")"
    SKILL_STAGE="$(mktemp -d "$INSTALL_ROOT/.skills.new.XXXXXX")"
    SOURCE_STAGE="$(mktemp -d "$HOME/.source.new.XXXXXX")"
    mkdir "$PACKAGE_STAGE/scidiscovery" "$SKILL_STAGE/skill"
    printf '%s\\n' "${REVISION:-new package}" > "$PACKAGE_STAGE/scidiscovery/module.py"
    printf 'new identity fixture\\n' > "$PACKAGE_STAGE/runtime-identity.json"
    printf '%s\\n' "${REVISION:-new skill}" > "$SKILL_STAGE/skill/SKILL.md"
    if [[ "${FAIL_AT:-}" == build ]]; then return 41; fi
    probe_thin_package "$PACKAGE_STAGE"
    rm -r "$SOURCE_STAGE"
    SOURCE_STAGE=""
    chmod -R a-w "$PACKAGE_STAGE" "$SKILL_STAGE"
}
probe_thin_package() {
    if [[ "${FAIL_AT:-}" == staged-probe && "$1" != "$SITE_ROOT" ]]; then return 42; fi
    if [[ "${FAIL_AT:-}" == installed-probe && "$1" == "$SITE_ROOT" ]]; then return 43; fi
}
'''


def assert_original(original_files):
    for path, (raw, mode) in original_files.items():
        assert path.read_bytes() == raw
        assert path.stat().st_mode & 0o777 == mode
    for path in original_files:
        if path.name in ("module.py", "SKILL.md"):
            assert path.parent.stat().st_mode & 0o777 == 0o555
            assert path.parent.parent.stat().st_mode & 0o777 == 0o555


def assert_clean(environment, prefix):
    assert {path.name for path in prefix.iterdir()} == {"site", "skills", "operator-notes.txt"}
    assert (prefix / "operator-notes.txt").read_bytes() == b"unmanaged prefix content\n"
    home = Path(environment["HOME"])
    assert not [path for path in home.iterdir() if path.name.startswith(".source.new.")]
    assert Path(environment["UNCHANGED_SCIENCE"]).read_bytes() == (
        b"scientific original must remain untouched\n"
    )


@pytest.mark.process_e2e
def test_reinstall_repeats_at_original_home_prefix_without_package_backups(original_install):
    environment, prefix, original_files = original_install
    for revision in ("revision one", "revision two"):
        result = shell({**environment, "REVISION": revision}, STAGED_UPDATE + "main reinstall\n")
        assert result.returncode == 0, result.stderr
        for relative in ("site/scidiscovery/module.py", "skills/skill/SKILL.md"):
            assert (prefix / relative).read_text() == revision + "\n"
        for path, (raw, mode) in original_files.items():
            if path.name.startswith("scid-"):
                assert path.read_bytes() == raw
                assert path.stat().st_mode & 0o777 == mode
        assert_clean(environment, prefix)


@pytest.mark.process_e2e
@pytest.mark.parametrize("failure, status", [("build", 41), ("staged-probe", 42)])
def test_reinstall_build_and_staged_probe_failure_leave_originals_unchanged(
    original_install, failure, status,
):
    environment, prefix, original_files = original_install
    result = shell({**environment, "FAIL_AT": failure}, STAGED_UPDATE + "main reinstall\n")
    assert result.returncode == status, result.stderr
    assert_original(original_files)
    assert_clean(environment, prefix)
    assert not (Path(environment["HOME"]) / ".local/state/scidiscovery/install").exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize("failure, status", [("publication", 44), ("installed-probe", 43)])
def test_reinstall_publication_and_installed_probe_failure_restore_exact_originals(
    original_install, failure, status,
):
    environment, prefix, original_files = original_install
    publication_failure = '''eval "$(declare -f publish_thin_cli | sed '1s/publish_thin_cli/publish_original_cli/')"
publish_thin_cli() {
    [[ "$1" != scid-experiment ]] || return 44
    publish_original_cli "$@"
}
''' if failure == "publication" else ""
    result = shell({**environment, "FAIL_AT": failure},
                   STAGED_UPDATE + publication_failure + "main reinstall\n")
    assert result.returncode == status, result.stderr
    assert_original(original_files)
    assert_clean(environment, prefix)
    backup = Path(environment["HOME"]) / ".local/state/scidiscovery/install"
    transactions = [path for path in backup.iterdir() if path.name.startswith(".transaction-")]
    assert len(transactions) == 1
    assert json.loads((transactions[0] / "manifest.json").read_bytes())["state"] == "rolled_back"


@pytest.mark.process_e2e
def test_reinstall_cleanup_failure_reports_committed_update_without_rollback(original_install):
    environment, prefix, original_files = original_install
    cleanup_failure = '''eval "$(declare -f remove_install_tree | sed '1s/remove_install_tree/remove_original_tree/')"
remove_install_tree() {
    [[ "$1" != "$SITE_ROLLBACK" && "$1" != "$SKILL_ROLLBACK" ]] || return 45
    remove_original_tree "$@"
}
'''
    result = shell(environment, STAGED_UPDATE + cleanup_failure + "main reinstall\n")
    assert result.returncode == 1, result.stderr
    assert "Update committed; old package cleanup is pending" in result.stderr
    assert "Thin rollback" not in result.stderr
    assert (prefix / "site/scidiscovery/module.py").read_bytes() == b"new package\n"
    assert (prefix / "skills/skill/SKILL.md").read_bytes() == b"new skill\n"
    site_holders = [path for path in prefix.iterdir() if path.name.startswith(".site.old.")]
    skill_holders = [path for path in prefix.iterdir() if path.name.startswith(".skills.old.")]
    assert len(site_holders) == len(skill_holders) == 1
    assert (site_holders[0] / "scidiscovery/module.py").read_bytes() == b"old package\n"
    assert (skill_holders[0] / "skill/SKILL.md").read_bytes() == b"old skill\n"
    assert site_holders[0].stat().st_mode & 0o777 == 0o555
    assert skill_holders[0].stat().st_mode & 0o777 == 0o555
    backup = Path(environment["HOME"]) / ".local/state/scidiscovery/install/transactions"
    completed = list(backup.iterdir())
    assert len(completed) == 1
    assert json.loads((completed[0] / "manifest.json").read_bytes())["state"] == "complete"
    for path, (raw, mode) in original_files.items():
        if path.name.startswith("scid-"):
            assert path.read_bytes() == raw
            assert path.stat().st_mode & 0o777 == mode


@pytest.mark.process_e2e
@pytest.mark.parametrize("failure, status", [("second-old-move", 46), ("site-publish-move", 47)])
def test_reinstall_directory_move_failure_restores_exact_originals(original_install, failure, status):
    environment, prefix, original_files = original_install
    move_failure = '''mv() {
    local argument source="" destination=""
    for argument in "$@"; do
        [[ "$argument" == -* ]] && continue
        source="$destination"
        destination="$argument"
    done
    if [[ "$FAIL_AT" == second-old-move && "$source" == "$SKILL_ROOT" && "$destination" == "$SKILL_ROLLBACK" ]]; then
        return 46
    fi
    if [[ "$FAIL_AT" == site-publish-move && "$source" == "$PACKAGE_STAGE" && "$destination" == "$SITE_ROOT" ]]; then
        return 47
    fi
    command mv "$@"
}
'''
    result = shell({**environment, "FAIL_AT": failure}, STAGED_UPDATE + move_failure + "main reinstall\n")
    assert result.returncode == status, result.stderr
    assert_original(original_files)
    assert_clean(environment, prefix)
    backup = Path(environment["HOME"]) / ".local/state/scidiscovery/install"
    transactions = [path for path in backup.iterdir() if path.name.startswith(".transaction-")]
    assert len(transactions) == 1
    assert json.loads((transactions[0] / "manifest.json").read_bytes())["state"] == "rolled_back"


@pytest.mark.process_e2e
@pytest.mark.parametrize("mutation", ["foreign-wrapper", "missing-wrapper", "linked-wrapper",
                                    "missing-site", "linked-site", "missing-skills", "linked-skills",
                                    "missing-identity", "linked-identity", "missing-core", "linked-core"])
def test_reinstall_refuses_foreign_or_incomplete_originals_before_staging(original_install, mutation):
    environment, prefix, original_files = original_install
    binary = Path(environment["HOME"]) / ".local/bin"
    if mutation.endswith("wrapper"):
        path = binary / "scid-experiment"
        if mutation == "foreign-wrapper":
            path.write_bytes(b"foreign owner\n")
        elif mutation == "missing-wrapper":
            path.unlink()
        else:
            old = binary / "saved-wrapper"
            path.rename(old)
            path.symlink_to(old)
    elif mutation.endswith(("identity", "core")):
        path = prefix / "site" / ("runtime-identity.json" if mutation.endswith("identity") else "scidiscovery")
        path.parent.chmod(0o755)
        saved = prefix / ("saved-" + path.name)
        if path.is_dir():
            path.chmod(0o755)
        path.rename(saved)
        if saved.is_dir():
            saved.chmod(0o555)
        if mutation.startswith("linked"):
            path.symlink_to(saved, target_is_directory=mutation.endswith("core"))
        path.parent.chmod(0o555)
    else:
        path = prefix / ("site" if mutation.endswith("site") else "skills")
        saved = prefix / ("saved-" + path.name)
        path.rename(saved)
        if mutation.startswith("linked"):
            path.symlink_to(saved, target_is_directory=True)
    before = {path: path.read_bytes() for path in original_files if path.is_file()}
    if mutation.endswith("core"):
        before[saved / "module.py"] = (saved / "module.py").read_bytes()
        saved_core_mode = saved.stat().st_mode & 0o777
        saved_module_mode = (saved / "module.py").stat().st_mode & 0o777
    result = shell(environment, '''install_packages() { exit 97; }
main reinstall
''')
    assert result.returncode == 1, result.stderr
    assert "reinstall" in result.stderr
    assert all(path.read_bytes() == raw for path, raw in before.items())
    if mutation.endswith("core"):
        assert saved.stat().st_mode & 0o777 == saved_core_mode == 0o555
        assert (saved / "module.py").stat().st_mode & 0o777 == saved_module_mode == 0o444
    assert not [path for path in prefix.iterdir() if path.name.startswith(".")]
    assert not (Path(environment["HOME"]) / ".local/state/scidiscovery/install").exists()


def test_seal_rename_failure_keeps_transaction_rollbackable(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("same_prefix_transaction", SCRIPT.with_name("install_transaction.py"))
    transaction_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transaction_module)
    wrapper = tmp_path / "scid-research"
    wrapper.write_bytes(b"old exact wrapper\n")
    transaction = tmp_path / "transaction"
    transaction_module.begin_transaction(transaction, targets=(("scid-research", wrapper),))
    wrapper.write_bytes(b"new wrapper\n")
    original_replace = os.replace

    def fail_seal(source, destination):
        if Path(source) == transaction:
            raise OSError("injected seal rename failure")
        return original_replace(source, destination)

    monkeypatch.setattr(transaction_module.os, "replace", fail_seal)
    with pytest.raises(OSError, match="seal rename failure"):
        transaction_module.seal_transaction(transaction, tmp_path / "completed")
    assert json.loads((transaction / "manifest.json").read_bytes())["state"] == "prepared"
    transaction_module.rollback_transaction(transaction)
    assert wrapper.read_bytes() == b"old exact wrapper\n"
