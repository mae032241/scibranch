#!/usr/bin/env bash
set -Eeuo pipefail

# The unused Root/MCP service deployment was retired in P5.
if [[ "${SCID_DEPLOY_MODE:-thin}" != thin ]]; then
    printf 'ERROR: legacy service deployment is retired; use the thin CLI.\n' >&2
    exit 2
fi
readonly SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly CLI_ROOT="${SCID_BIN_ROOT:-/usr/local/bin}"
readonly PYTHON="${SCID_PYTHON:-$(command -v python3 || true)}"
readonly PLUGIN_SPECIFICATION="${SCID_PLUGINS:-}"
readonly INSTALL_ROOT="${SCID_INSTALL_ROOT:-/opt/scidiscovery-thin}"
readonly SITE_ROOT="${INSTALL_ROOT}/site"
readonly SKILL_ROOT="${INSTALL_ROOT}/skills"
readonly BACKUP_ROOT="${SCID_BACKUP_ROOT:-/var/backups/scidiscovery-thin}"
PACKAGE_STAGE=""
SKILL_STAGE=""
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
    done
    [[ -d "$CLI_ROOT" && ! -L "$CLI_ROOT" ]] || die "SCID_BIN_ROOT must be an existing directory"
    [[ -n "$PYTHON" && "$PYTHON" = /* && -x "$PYTHON" ]] || die "base Python is unavailable: ${PYTHON}"
}

require_thin_sources() {
    local path plugin_ids plugin_distributions
    validate_thin_paths
    for path in pyproject.toml README.md deploy/plugin_selection.py deploy/install_transaction.py src/scidiscovery/research_loop/cli.py skills/sentaurus-tcad-code/SKILL.md skills/curve-analysis/SKILL.md skills/scientific-paper-evidence/SKILL.md; do
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
    local path
    # No inference about live writers: code is published only into a new prefix.
    for path in "$SITE_ROOT" "$SKILL_ROOT" "$CLI_ROOT/scid-research" "$CLI_ROOT/scid-experiment"; do
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
    PYTHONNOUSERSITE=1 PYTHONPATH="$pythonpath" "$PYTHON" -c 'import scidiscovery.research_loop.cli, scidiscovery.research_loop.experiments, scidiscovery.browser_access, scidiscovery.research_loop.instance_ui'
}

validate_base_python() {
    "$PYTHON" -m pip --version
    "$PYTHON" - "$SOURCE_ROOT/pyproject.toml" <<'PY'
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from packaging.requirements import Requirement
import sys
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        raise SystemExit("missing base Python dependency: tomli>=2; required for Python <3.11") from None

project = tomllib.loads(Path(sys.argv[1]).read_text())
requirements = [*project['project'].get('dependencies', ()), *project['build-system']['requires']]
for raw in requirements:
    requirement = Requirement(raw)
    if requirement.marker is not None and not requirement.marker.evaluate({'extra': ''}):
        continue
    try:
        observed = version(requirement.name)
    except PackageNotFoundError:
        raise SystemExit(f'missing base Python dependency: {requirement}; install it before --no-deps deployment') from None
    if requirement.specifier and observed not in requirement.specifier:
        raise SystemExit(f'incompatible base Python dependency: {requirement}; found {observed}')
    print(f'base dependency: {requirement.name}={observed}')
print('base Python dependency check: pass')
PY
}

require_root() {
    [[ "$(id -u)" -eq 0 ]] || die "install and uninstall require sudo"
}


preview() {
    printf 'Thin deployment: stage packages at %s; publish scid-research/scid-experiment at %s.\n' "$SITE_ROOT" "$CLI_ROOT"
    printf 'Source: %s\nPython: %s\nExecution adapters: %s\nSkill source: %s\n' "$SOURCE_ROOT" "$PYTHON" "${PLUGIN_SPECIFICATION:-none}" "$SKILL_ROOT"
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
    stage="${INSTALL_ROOT}/.site.new.$$"
    source_stage="$(mktemp -d)"
    source_root="${source_stage}/scidiscovery"
    if [[ ! -d "$INSTALL_ROOT" ]]; then
        install -d -o root -g root -m 0755 "$INSTALL_ROOT"
    fi
    rm -rf "$stage"
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
    selected_distributions="$(IFS=,; printf '%s' "${SELECTED_PLUGIN_DISTRIBUTIONS[*]}")"
    validate_distribution_cohort "$stage"
    probe_thin_package "$stage"
    PYTHONNOUSERSITE=1 PYTHONPATH="$stage" "$PYTHON" -m \
        scidiscovery.runtime_identity write \
        --manifest "$stage/runtime-identity.json"
    PYTHONNOUSERSITE=1 PYTHONPATH="$stage" "$PYTHON" -m \
        scidiscovery.runtime_identity verify \
        --manifest "$stage/runtime-identity.json"
    PACKAGE_STAGE="$stage"
    SKILL_STAGE="${INSTALL_ROOT}/.skills.new.$$"
    [[ ! -e "$SKILL_STAGE" && ! -L "$SKILL_STAGE" ]] || die "skill stage already exists"
    cp -a "$SOURCE_ROOT/skills" "$SKILL_STAGE"
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
if {entry.name for entry in scripts} != {'scid-research', 'scid-experiment'}:
    raise SystemExit('unexpected console entry point in thin package')
for name, target in (('scid-research', 'scidiscovery.research_loop.cli:main'),
                     ('scid-experiment', 'scidiscovery.research_loop.experiments:main')):
    matches = [entry for entry in scripts if entry.name == name]
    if len(matches) != 1 or matches[0].value != target or not callable(matches[0].load()):
        raise SystemExit('thin console entry point missing or ambiguous: ' + name)
print('thin package entry points: pass')
PY
}

thin_cli_wrapper() {
    local quoted_python quoted_site quoted_skills
    printf -v quoted_python '%q' "$PYTHON"
    printf -v quoted_site '%q' "$SITE_ROOT"
    printf -v quoted_skills '%q' "$SKILL_ROOT"
    cat <<EOF
#!/usr/bin/env bash
env PYTHONNOUSERSITE=1 PYTHONPATH=${quoted_site} SCID_SKILLS_ROOT=${quoted_skills} ${quoted_python} -m scidiscovery.runtime_identity verify --manifest ${quoted_site}/runtime-identity.json || exit
exec env PYTHONNOUSERSITE=1 PYTHONPATH=${quoted_site} SCID_SKILLS_ROOT=${quoted_skills} ${quoted_python} -m $1 "\$@"
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
    [[ -n "$PACKAGE_STAGE" && -d "$PACKAGE_STAGE" ]] || die "validated package stage is unavailable"
    validate_thin_targets
    [[ -n "$SKILL_STAGE" && -d "$SKILL_STAGE" ]] || die "validated skill stage is unavailable"
    mv -T -n -- "$SKILL_STAGE" "$SKILL_ROOT"
    [[ ! -e "$SKILL_STAGE" ]] || die "skill source appeared during publication; preserve it: ${SKILL_ROOT}"
    SKILL_STAGE=""
    mv -T -n -- "$PACKAGE_STAGE" "$SITE_ROOT"
    [[ ! -e "$PACKAGE_STAGE" ]] || die "thin prefix appeared during publication; preserve it: ${SITE_ROOT}"
    PACKAGE_STAGE=""
    chmod -R a-w "$SITE_ROOT"
    find "$SITE_ROOT" -type d -exec chmod a+rx {} +
    publish_thin_cli scid-research scidiscovery.research_loop.cli
    publish_thin_cli scid-experiment scidiscovery.research_loop.experiments
}

begin_install_transaction() {
    local timestamp
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    TRANSACTION_ROOT="${BACKUP_ROOT}/.transaction-${timestamp}-$$"
    [[ -d "$BACKUP_ROOT" ]] || install -d -m 0700 "$BACKUP_ROOT"
    "$PYTHON" "${SOURCE_ROOT}/deploy/install_transaction.py" begin --root "$TRANSACTION_ROOT" \
        --target "scid-research=${CLI_ROOT}/scid-research" \
        --target "scid-experiment=${CLI_ROOT}/scid-experiment"
    ROLLBACK_ARMED=1
    trap 'rollback_install $?' ERR
    trap 'rollback_install 130' INT TERM
}

rollback_install() {
    local status="${1:-1}"
    trap - ERR INT TERM
    set +e
    ROLLBACK_ARMED=0
    local name module safe=1
    for name in scid-research scid-experiment; do
        module=scidiscovery.research_loop.cli
        [[ "$name" != scid-experiment ]] || module=scidiscovery.research_loop.experiments
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
    [[ -z "$PACKAGE_STAGE" ]] || rm -rf "$PACKAGE_STAGE"
    [[ -z "$SKILL_STAGE" ]] || rm -rf "$SKILL_STAGE"
    printf 'Thin rollback retained the package prefix; instance data was untouched.\n' >&2
    exit "$status"
}

complete_install_transaction() {
    local destination
    destination="${BACKUP_ROOT}/transactions/${TRANSACTION_ROOT##*/.transaction-}"
    "$PYTHON" "${SOURCE_ROOT}/deploy/install_transaction.py" seal \
        --root "$TRANSACTION_ROOT" --destination "$destination"
    ROLLBACK_ARMED=0
    trap - ERR INT TERM
    printf 'Install transaction evidence: %s\n' "$destination"
}

install_all() {
    require_root
    thin_deployment_lock
    validate_thin_targets
    validate_base_python
    validate_source
    install_packages
    begin_install_transaction
    activate_packages
    probe_thin_package "$SITE_ROOT"
    complete_install_transaction
    printf 'Thin commands installed: %s/scid-research and %s/scid-experiment\nNo instance, budget, database or service was created.\nUse scid-research prepare with an explicit root, objective and budgets, then run/serve-ui for that root.\n' "$CLI_ROOT" "$CLI_ROOT"
}

status() {
    validate_thin_paths
    local name module
    for name in scid-research scid-experiment; do
        module=scidiscovery.research_loop.cli
        [[ "$name" != scid-experiment ]] || module=scidiscovery.research_loop.experiments
        if [[ -f "$CLI_ROOT/$name" && ! -L "$CLI_ROOT/$name" ]] && cmp -s <(thin_cli_wrapper "$module") "$CLI_ROOT/$name"; then
            printf '%s: wrapper matches configured prefix %s\n' "$name" "$SITE_ROOT"
        else
            printf '%s: absent or owned by another configuration\n' "$name"
        fi
    done
}

uninstall_services() {
    require_root
    validate_thin_paths
    thin_deployment_lock
    local name module
    for name in scid-research scid-experiment; do
        module=scidiscovery.research_loop.cli
        [[ "$name" != scid-experiment ]] || module=scidiscovery.research_loop.experiments
        if [[ -e "$CLI_ROOT/$name" || -L "$CLI_ROOT/$name" ]]; then
            [[ -f "$CLI_ROOT/$name" && ! -L "$CLI_ROOT/$name" ]] && cmp -s <(thin_cli_wrapper "$module") "$CLI_ROOT/$name" || die "refusing to remove another deployment's command: $name"
        fi
    done
    rm -f -- "$CLI_ROOT/scid-research" "$CLI_ROOT/scid-experiment"
    printf 'Thin command wrappers removed. Package prefix retained for existing processes; instance data was untouched.\n'
}

main() {
    [[ "$#" -le 1 ]] || die "usage: $0 [--dry-run|install|status|uninstall]"
    case "${1:---dry-run}" in
        --dry-run) require_sources; validate_base_python; validate_source; preview ;;
        install) require_sources; install_all ;;
        status) status ;;
        uninstall) uninstall_services ;;
        *) die "usage: $0 [--dry-run|install|status|uninstall]" ;;
    esac
}

[[ "${BASH_SOURCE[0]}" != "$0" ]] || main "$@"
