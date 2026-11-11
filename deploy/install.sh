#!/usr/bin/env bash
set -Eeuo pipefail

# The unused Root/MCP service deployment was retired in P5.
if [[ "${SCID_DEPLOY_MODE:-thin}" != thin ]]; then
    printf 'ERROR: legacy service deployment is retired; use the thin CLI.\n' >&2
    exit 2
fi
readonly SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly CLI_ROOT="${SCID_BIN_ROOT:-${HOME}/.local/bin}"
readonly PYTHON="${SCID_PYTHON:-$(command -v python3 || true)}"
readonly PLUGIN_SPECIFICATION="${SCID_PLUGINS:-}"
readonly INSTALL_ROOT="${SCID_INSTALL_ROOT:-${HOME}/.local/share/scidiscovery}"
readonly SITE_ROOT="${INSTALL_ROOT}/site"
readonly SKILL_ROOT="${INSTALL_ROOT}/skills"
readonly BACKUP_ROOT="${SCID_BACKUP_ROOT:-${HOME}/.local/state/scidiscovery/install}"
PACKAGE_STAGE=""
SKILL_STAGE=""
SOURCE_STAGE=""
SITE_ROLLBACK=""
SKILL_ROLLBACK=""
INSTALL_MODE=install
REINSTALL_SITE_PUBLISHED=0
REINSTALL_SKILLS_PUBLISHED=0
TRANSACTION_ROOT=""
ROLLBACK_ARMED=0
declare -a SELECTED_PLUGINS=()
declare -a SELECTED_PLUGIN_DISTRIBUTIONS=()

die() {
    printf 'ERROR: %s\n' "$*" >&2
    if [[ "$ROLLBACK_ARMED" -eq 1 ]]; then
        rollback_install 1
    fi
    exit 1
}

validate_thin_paths() {
    local path
    for path in "$INSTALL_ROOT" "$CLI_ROOT" "$BACKUP_ROOT"; do
        [[ "$path" = /* && "$(realpath -m -- "$path")" == "$path" ]] || die "thin deployment paths must be absolute and canonical"
        [[ ! -L "$path" && ( ! -e "$path" || -d "$path" ) ]] || die "thin deployment paths must be directories, not symlinks or other file types: ${path}"
    done
    [[ -n "$PYTHON" && "$PYTHON" = /* && -x "$PYTHON" ]] || die "base Python is unavailable: ${PYTHON}"
}

require_thin_sources() {
    local path plugin_ids plugin_distributions
    validate_thin_paths
    for path in pyproject.toml README.md deploy/plugin_selection.py deploy/python_dependencies.py deploy/install_transaction.py src/scidiscovery/app_cli.py src/scidiscovery/research_loop/cli.py skills/sentaurus-tcad-code/SKILL.md skills/curve-analysis/SKILL.md skills/scientific-paper-evidence/SKILL.md; do
        [[ -f "$SOURCE_ROOT/$path" ]] || die "missing thin source: ${path}"
    done
    if [[ -n "$PLUGIN_SPECIFICATION" ]]; then
        plugin_ids="$("$PYTHON" "$SOURCE_ROOT/deploy/plugin_selection.py" --source-root "$SOURCE_ROOT" --plugins "$PLUGIN_SPECIFICATION" --field id)" || die "SCID_PLUGINS validation failed"
        plugin_distributions="$("$PYTHON" "$SOURCE_ROOT/deploy/plugin_selection.py" --source-root "$SOURCE_ROOT" --plugins "$PLUGIN_SPECIFICATION" --field distribution)" || die "SCID_PLUGINS distribution resolution failed"
        mapfile -t SELECTED_PLUGINS <<<"$plugin_ids"
        mapfile -t SELECTED_PLUGIN_DISTRIBUTIONS <<<"$plugin_distributions"
    fi
}

validate_thin_targets() {
    local path name module
    if [[ "$INSTALL_MODE" == reinstall ]]; then
        for path in "$SITE_ROOT" "$SKILL_ROOT"; do
            [[ -d "$path" && ! -L "$path" ]] || die "reinstall requires the original ordinary package and skill directories: ${path}"
        done
        [[ -f "$SITE_ROOT/runtime-identity.json" && ! -L "$SITE_ROOT/runtime-identity.json" && -d "$SITE_ROOT/scidiscovery" && ! -L "$SITE_ROOT/scidiscovery" ]] || die "reinstall requires the original package layout"
        for name in scid-research scid-experiment scid; do
            module=scidiscovery.research_loop.cli
            [[ "$name" != scid-experiment ]] || module=scidiscovery.research_loop.experiments
            [[ "$name" != scid ]] || module=scidiscovery.app_cli
            # Older owned thin installs have the original two wrappers only.
            if [[ "$name" == scid && ! -e "$CLI_ROOT/$name" && ! -L "$CLI_ROOT/$name" ]]; then
                continue
            fi
            [[ -f "$CLI_ROOT/$name" && ! -L "$CLI_ROOT/$name" ]] && cmp -s <(thin_cli_wrapper "$module") "$CLI_ROOT/$name" || die "reinstall refuses a missing or foreign command: ${name}"
        done
        return
    fi
    for path in "$SITE_ROOT" "$SKILL_ROOT" "$CLI_ROOT/scid-research" "$CLI_ROOT/scid-experiment" "$CLI_ROOT/scid"; do
        [[ ! -e "$path" && ! -L "$path" ]] || die "thin target already exists; preserve the existing deployment: ${path}"
    done
}

thin_deployment_lock() {
    local path="$CLI_ROOT/.scidiscovery-thin-install.lock"
    [[ ! -L "$path" && ( ! -e "$path" || -f "$path" ) ]] || die "unsafe thin deployment lock"
    exec 9>"$path"
    flock -n 9 || die "another thin deployment owns these command paths"
}

require_sources() {
    require_thin_sources
}

validate_source() {
    local plugin pythonpath="${SOURCE_ROOT}/src"
    for plugin in "${SELECTED_PLUGINS[@]}"; do
        pythonpath+=":${SOURCE_ROOT}/plugins/${plugin}"
    done
    PYTHONNOUSERSITE=1 PYTHONPATH="$pythonpath" "$PYTHON" -c 'import scidiscovery.app_cli, scidiscovery.research_loop.cli, scidiscovery.research_loop.experiments, scidiscovery.browser_access, scidiscovery.research_loop.instance_ui'
}

validate_base_python() {
    "$PYTHON" -m pip --version
    "$PYTHON" "$SOURCE_ROOT/deploy/python_dependencies.py" \
        --source-root "$SOURCE_ROOT" --plugins "$PLUGIN_SPECIFICATION"
}

preview() {
    printf 'Thin deployment: stage packages at %s; publish scid/scid-research/scid-experiment at %s.\n' "$SITE_ROOT" "$CLI_ROOT"
    printf 'Source: %s\nPython: %s\nExecution adapters: %s\nSkill source: %s\nInstall evidence: %s\n' "$SOURCE_ROOT" "$PYTHON" "${PLUGIN_SPECIFICATION:-none}" "$SKILL_ROOT" "$BACKUP_ROOT"
    printf 'No instance, budget, database, platform profile or service is created.\n'
}

validate_distribution_cohort() {
    local stage="$1" selected_distributions
    [[ -d "$stage" ]] || die "package cohort stage is unavailable: ${stage}"
    selected_distributions="$(IFS=,; printf '%s' "${SELECTED_PLUGIN_DISTRIBUTIONS[*]}")"
    SCID_SELECTED_DISTRIBUTIONS="$selected_distributions" \
        PYTHONNOUSERSITE=1 PYTHONPATH="$stage" "$PYTHON" - "$stage" <<'PY'
import os
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

stage = Path(__import__('sys').argv[1]).resolve()
names = ('scidiscovery', *(name for name in os.environ[
    'SCID_SELECTED_DISTRIBUTIONS'].split(',') if name))
cohort = {canonicalize_name(name) for name in names}
packages = {}
for name in names:
    try:
        package = distribution(name)
    except PackageNotFoundError as error:
        raise SystemExit(f'missing staged distribution: {name}') from error
    location = Path(package.locate_file('')).resolve()
    if not location.is_relative_to(stage):
        raise SystemExit(f'staged distribution resolved outside package stage: {name}={location}')
    packages[canonicalize_name(name)] = package

for source_name, package in packages.items():
    for raw in package.requires or ():
        requirement = Requirement(raw)
        target = canonicalize_name(requirement.name)
        if target not in cohort or (
            requirement.marker is not None and not requirement.marker.evaluate()
        ):
            continue
        observed = packages[target].version
        if requirement.specifier and observed not in requirement.specifier:
            raise SystemExit(
                'incompatible staged distribution cohort: '
                f'{package.metadata["Name"]}=={package.version} requires {requirement}; '
                f'found {packages[target].metadata["Name"]}=={observed}'
            )
print('staged distribution cohort: pass')
PY
}

install_packages() {
    local stage source_stage source_root plugin plugin_root selected_distributions
    local -a package_roots
    if [[ ! -d "$INSTALL_ROOT" ]]; then
        install -d -m 0755 "$INSTALL_ROOT"
    fi
    PACKAGE_STAGE="$(mktemp -d "${INSTALL_ROOT}/.site.new.XXXXXX")"
    stage="$PACKAGE_STAGE"
    SOURCE_STAGE="$(mktemp -d)"
    source_stage="$SOURCE_STAGE"
    source_root="${source_stage}/scidiscovery"
    install -d "$source_root/src"
    cp -a "$SOURCE_ROOT/pyproject.toml" "$SOURCE_ROOT/README.md" \
        "$SOURCE_ROOT/deploy" "$SOURCE_ROOT/skills" \
        "$source_root/"
    [[ ! -f "$SOURCE_ROOT/README.zh-CN.md" ]] || \
        cp -a "$SOURCE_ROOT/README.zh-CN.md" "$source_root/"
    cp -a "$SOURCE_ROOT/src/scidiscovery" "$source_root/src/"
    package_roots=("$source_root")
    for plugin in "${SELECTED_PLUGINS[@]}"; do
        plugin_root="${source_stage}/plugin-${plugin}"
        install -d "$plugin_root"
        cp -a "$SOURCE_ROOT/plugins/${plugin}/." "$plugin_root/"
        package_roots+=("$plugin_root")
    done
    find "$source_stage" -type d \( -name __pycache__ -o -name '*.egg-info' -o -name build \) \
        -prune -exec rm -rf {} +
    printf 'Building local application packages (offline, without dependencies)...\n'
    "$PYTHON" -m pip install --disable-pip-version-check --no-input --no-index \
        --root-user-action=ignore --no-deps --no-build-isolation \
        --progress-bar off --upgrade \
        --target "$stage" "${package_roots[@]}"
    rm -rf "$source_stage"
    SOURCE_STAGE=""
    selected_distributions="$(IFS=,; printf '%s' "${SELECTED_PLUGIN_DISTRIBUTIONS[*]}")"
    validate_distribution_cohort "$stage"
    probe_thin_package "$stage"
    PYTHONNOUSERSITE=1 PYTHONPATH="$stage" "$PYTHON" -m \
        scidiscovery.runtime_identity write \
        --manifest "$stage/runtime-identity.json"
    PYTHONNOUSERSITE=1 PYTHONPATH="$stage" "$PYTHON" -m \
        scidiscovery.runtime_identity verify \
        --manifest "$stage/runtime-identity.json"
    SKILL_STAGE="$(mktemp -d "${INSTALL_ROOT}/.skills.new.XXXXXX")"
    cp -a "$SOURCE_ROOT/skills/." "$SKILL_STAGE/"
    find "$SKILL_STAGE" -type d -name __pycache__ -prune -exec rm -rf {} +
    find "$SKILL_STAGE" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
    [[ -z "$(find "$SKILL_STAGE" ! -type d ! -type f -print -quit)" ]] || die "skill assets must be ordinary files and directories"
    chmod -R a-w "$SKILL_STAGE"
    find "$SKILL_STAGE" -type d -exec chmod a+rx {} +
}

probe_thin_package() {
    PYTHONNOUSERSITE=1 PYTHONPATH="$1" "$PYTHON" - <<'PY'
from importlib.metadata import distribution
import scidiscovery.browser_access
import scidiscovery.research_loop.instance_ui
scripts = [entry for entry in distribution('scidiscovery').entry_points if entry.group == 'console_scripts']
if {entry.name for entry in scripts} != {'scid', 'scid-research', 'scid-experiment'}:
    raise SystemExit('unexpected console entry point in thin package')
for name, target in (('scid', 'scidiscovery.app_cli:main'),
                     ('scid-research', 'scidiscovery.research_loop.cli:main'),
                     ('scid-experiment', 'scidiscovery.research_loop.experiments:main')):
    matches = [entry for entry in scripts if entry.name == name]
    if len(matches) != 1 or matches[0].value != target or not callable(matches[0].load()):
        raise SystemExit('thin console entry point missing or ambiguous: ' + name)
print('thin package entry points: pass')
PY
}

thin_cli_wrapper() {
    local quoted_python quoted_site
    printf -v quoted_python '%q' "$PYTHON"
    printf -v quoted_site '%q' "$SITE_ROOT"
    cat <<EOF
#!/usr/bin/env bash
env PYTHONNOUSERSITE=1 PYTHONPATH=${quoted_site} ${quoted_python} -m scidiscovery.runtime_identity verify --manifest ${quoted_site}/runtime-identity.json || exit
exec env PYTHONNOUSERSITE=1 PYTHONPATH=${quoted_site} ${quoted_python} -m $1 "\$@"
EOF
}

publish_thin_cli() {
    local temporary
    temporary="$(mktemp "$CLI_ROOT/.thin-cli.XXXXXX")"
    thin_cli_wrapper "$2" > "$temporary"
    chmod 0755 "$temporary"
    if ! ln -- "$temporary" "$CLI_ROOT/$1"; then
        rm -f -- "$temporary"
        die "thin command appeared during publication: $1"
    fi
    rm -- "$temporary"
}

activate_packages() {
    local app_wrapper_existed=0
    [[ -n "$PACKAGE_STAGE" && -d "$PACKAGE_STAGE" ]] || die "validated package stage is unavailable"
    validate_thin_targets
    if [[ -e "$CLI_ROOT/scid" || -L "$CLI_ROOT/scid" ]]; then
        app_wrapper_existed=1
    fi
    [[ -n "$SKILL_STAGE" && -d "$SKILL_STAGE" ]] || die "validated skill stage is unavailable"
    if [[ "$INSTALL_MODE" == reinstall ]]; then
        # Same-parent renames preserve even read-only directory roots unchanged.
        SITE_ROLLBACK="$(mktemp -d "${INSTALL_ROOT}/.site.old.XXXXXX")"
        SKILL_ROLLBACK="$(mktemp -d "${INSTALL_ROOT}/.skills.old.XXXXXX")"
        mv -T -- "$SITE_ROOT" "$SITE_ROLLBACK"
        mv -T -- "$SKILL_ROOT" "$SKILL_ROLLBACK"
        rm -- "$CLI_ROOT/scid-research" "$CLI_ROOT/scid-experiment"
        if [[ "$app_wrapper_existed" -eq 1 ]]; then
            [[ -f "$CLI_ROOT/scid" && ! -L "$CLI_ROOT/scid" ]] && cmp -s <(thin_cli_wrapper scidiscovery.app_cli) "$CLI_ROOT/scid" || die "reinstall command owner changed: scid"
            rm -- "$CLI_ROOT/scid"
        fi
    fi
    mv -T -n -- "$SKILL_STAGE" "$SKILL_ROOT"
    [[ ! -e "$SKILL_STAGE" ]] || die "skill source appeared during publication; preserve it: ${SKILL_ROOT}"
    REINSTALL_SKILLS_PUBLISHED=1
    SKILL_STAGE=""
    mv -T -n -- "$PACKAGE_STAGE" "$SITE_ROOT"
    [[ ! -e "$PACKAGE_STAGE" ]] || die "thin prefix appeared during publication; preserve it: ${SITE_ROOT}"
    REINSTALL_SITE_PUBLISHED=1
    PACKAGE_STAGE=""
    chmod -R a+rX,a-w -- "$SITE_ROOT"
    publish_thin_cli scid-research scidiscovery.research_loop.cli
    publish_thin_cli scid-experiment scidiscovery.research_loop.experiments
    publish_thin_cli scid scidiscovery.app_cli
}

begin_install_transaction() {
    local timestamp
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    TRANSACTION_ROOT="${BACKUP_ROOT}/.transaction-${timestamp}-$$"
    [[ -d "$BACKUP_ROOT" ]] || install -d -m 0700 "$BACKUP_ROOT"
    "$PYTHON" "${SOURCE_ROOT}/deploy/install_transaction.py" begin --root "$TRANSACTION_ROOT" \
        --target "scid-research=${CLI_ROOT}/scid-research" \
        --target "scid-experiment=${CLI_ROOT}/scid-experiment" \
        --target "scid=${CLI_ROOT}/scid"
    arm_install_rollback
}

arm_install_rollback() {
    ROLLBACK_ARMED=1
    trap 'rollback_install $?' ERR
    trap 'rollback_install 130' INT TERM
}

remove_install_tree() {
    local path="$1"
    [[ ! -e "$path" && ! -L "$path" ]] && return 0
    [[ -d "$path" && ! -L "$path" ]] || return 1
    chmod -R u+rwX -- "$path" || return
    rm -rf -- "$path"
}

restore_install_tree() {
    local holder="$1" target="$2" published="$3" stage="$4"
    [[ -n "$holder" ]] || return 0
    if [[ "$published" -eq 1 || ( -n "$stage" && ! -e "$stage" && ! -L "$stage" ) ]]; then
        remove_install_tree "$target" || return
    fi
    if [[ ! -e "$target" && ! -L "$target" ]]; then
        mv -T -- "$holder" "$target"
    else
        # A failed old-tree move leaves only the reserved empty sibling.
        rmdir -- "$holder"
    fi
}

rollback_install() {
    local status="${1:-1}"
    trap - ERR INT TERM
    set +e
    ROLLBACK_ARMED=0
    local name module safe=1
    restore_install_tree "$SITE_ROLLBACK" "$SITE_ROOT" "$REINSTALL_SITE_PUBLISHED" "$PACKAGE_STAGE" || printf 'Package rollback needs inspection: %s\n' "$SITE_ROLLBACK" >&2
    restore_install_tree "$SKILL_ROLLBACK" "$SKILL_ROOT" "$REINSTALL_SKILLS_PUBLISHED" "$SKILL_STAGE" || printf 'Skill rollback needs inspection: %s\n' "$SKILL_ROLLBACK" >&2
    for name in scid-research scid-experiment scid; do
        module=scidiscovery.research_loop.cli
        [[ "$name" != scid-experiment ]] || module=scidiscovery.research_loop.experiments
        [[ "$name" != scid ]] || module=scidiscovery.app_cli
        if [[ -e "$CLI_ROOT/$name" || -L "$CLI_ROOT/$name" ]]; then
            [[ -f "$CLI_ROOT/$name" && ! -L "$CLI_ROOT/$name" ]] && cmp -s <(thin_cli_wrapper "$module") "$CLI_ROOT/$name" || safe=0
        fi
    done
    if [[ -n "$TRANSACTION_ROOT" && -f "${TRANSACTION_ROOT}/manifest.json" ]]; then
        if [[ "$safe" -eq 1 ]]; then
            "$PYTHON" "${SOURCE_ROOT}/deploy/install_transaction.py" rollback --root "$TRANSACTION_ROOT"
        else
            printf 'Thin rollback incomplete: an unexpected command owner was preserved; inspect transaction %s.\n' "$TRANSACTION_ROOT" >&2
        fi
    fi
    [[ -z "$PACKAGE_STAGE" ]] || remove_install_tree "$PACKAGE_STAGE"
    [[ -z "$SKILL_STAGE" ]] || remove_install_tree "$SKILL_STAGE"
    [[ -z "$SOURCE_STAGE" ]] || remove_install_tree "$SOURCE_STAGE"
    printf 'Thin rollback retained the package prefix; instance data was untouched.\n' >&2
    exit "$status"
}

complete_install_transaction() {
    local destination holder cleanup_pending=0
    destination="${BACKUP_ROOT}/transactions/${TRANSACTION_ROOT##*/.transaction-}"
    "$PYTHON" "${SOURCE_ROOT}/deploy/install_transaction.py" seal \
        --root "$TRANSACTION_ROOT" --destination "$destination"
    ROLLBACK_ARMED=0
    trap - ERR INT TERM
    printf 'Install transaction evidence: %s\n' "$destination"
    for holder in "$SITE_ROLLBACK" "$SKILL_ROLLBACK"; do
        [[ -n "$holder" ]] || continue
        if ! remove_install_tree "$holder"; then
            printf 'Update committed; old package cleanup is pending at %s.\n' "$holder" >&2
            cleanup_pending=1
        fi
    done
    [[ "$cleanup_pending" -eq 0 ]] || return 1
    SITE_ROLLBACK=""
    SKILL_ROLLBACK=""
}

install_all() {
    [[ -d "$CLI_ROOT" ]] || install -d -m 0755 "$CLI_ROOT"
    thin_deployment_lock
    validate_thin_targets
    arm_install_rollback
    validate_base_python
    validate_source
    install_packages
    begin_install_transaction
    activate_packages
    probe_thin_package "$SITE_ROOT"
    complete_install_transaction
    printf 'Thin commands installed: %s/scid, %s/scid-research and %s/scid-experiment\nNo instance, budget, database or service was created.\nUse scid web to open workspace management, or scid-research prepare with an explicit root, objective and budgets.\n' "$CLI_ROOT" "$CLI_ROOT" "$CLI_ROOT"
}

status() {
    validate_thin_paths
    local name module
    for name in scid-research scid-experiment scid; do
        module=scidiscovery.research_loop.cli
        [[ "$name" != scid-experiment ]] || module=scidiscovery.research_loop.experiments
        [[ "$name" != scid ]] || module=scidiscovery.app_cli
        if [[ -f "$CLI_ROOT/$name" && ! -L "$CLI_ROOT/$name" ]] && cmp -s <(thin_cli_wrapper "$module") "$CLI_ROOT/$name"; then
            printf '%s: wrapper matches configured prefix %s\n' "$name" "$SITE_ROOT"
        else
            printf '%s: absent or owned by another configuration\n' "$name"
        fi
    done
}

uninstall_services() {
    validate_thin_paths
    if [[ ! -d "$CLI_ROOT" ]]; then
        printf 'Thin command wrappers are absent. Package prefix retained; instance data was untouched.\n'
        return
    fi
    thin_deployment_lock
    local name module
    for name in scid-research scid-experiment scid; do
        module=scidiscovery.research_loop.cli
        [[ "$name" != scid-experiment ]] || module=scidiscovery.research_loop.experiments
        [[ "$name" != scid ]] || module=scidiscovery.app_cli
        if [[ -e "$CLI_ROOT/$name" || -L "$CLI_ROOT/$name" ]]; then
            [[ -f "$CLI_ROOT/$name" && ! -L "$CLI_ROOT/$name" ]] && cmp -s <(thin_cli_wrapper "$module") "$CLI_ROOT/$name" || die "refusing to remove another deployment's command: $name"
        fi
    done
    rm -f -- "$CLI_ROOT/scid-research" "$CLI_ROOT/scid-experiment" "$CLI_ROOT/scid"
    printf 'Thin command wrappers removed. Package prefix retained for existing processes; instance data was untouched.\n'
}

main() {
    [[ "$#" -le 1 ]] || die "usage: $0 [--dry-run|install|reinstall|status|uninstall]"
    case "${1:---dry-run}" in
        --dry-run) require_sources; validate_base_python; validate_source; preview ;;
        install) require_sources; install_all ;;
        reinstall) INSTALL_MODE=reinstall; require_sources; install_all ;;
        status) status ;;
        uninstall) uninstall_services ;;
        *) die "usage: $0 [--dry-run|install|reinstall|status|uninstall]" ;;
    esac
}

[[ "${BASH_SOURCE[0]}" != "$0" ]] || main "$@"
