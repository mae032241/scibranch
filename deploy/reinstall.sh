#!/usr/bin/env bash
set -Eeuo pipefail

readonly SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly MODE="${1:-reinstall}"
[[ "$#" -le 1 ]] || { printf 'Usage: %s [--dry-run|install|reinstall|status]\n' "${0##*/}" >&2; exit 2; }
if [[ "${SCID_DEPLOY_MODE:-thin}" != thin ]]; then
    printf 'ERROR: legacy service deployment is retired; use the thin CLI.\n' >&2
    exit 2
fi
case "$MODE" in
    status|--dry-run|install|reinstall) exec "$SOURCE_ROOT/deploy/install.sh" "$MODE" ;;
    *) printf 'Usage: %s [--dry-run|install|reinstall|status]\n' "${0##*/}" >&2; exit 2 ;;
esac
