#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

usage() {
  cat <<EOF
Contributor helper. Users should install the CLI, then run setup:

  pipx install agent-keybank
  keybank setup

Usage:
  ./install.sh              editable install + keybank setup --agents all
  ./install.sh --interactive
  ./install.sh --uninstall
EOF
}

install_editable() {
  if command -v pipx >/dev/null 2>&1; then
    pipx install --force --editable "$ROOT"
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    uv tool install --force --editable "$ROOT"
    return
  fi
  python3 -m pip install --user --quiet --editable "$ROOT"
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
  --uninstall)
    if command -v pipx >/dev/null 2>&1; then
      pipx uninstall agent-keybank || true
    elif command -v uv >/dev/null 2>&1; then
      uv tool uninstall agent-keybank || true
    else
      python3 -m pip uninstall -y agent-keybank || true
    fi
    echo "Left \$HOME/.keybank in place (catalog and secrets were not deleted)."
    ;;
  --interactive)
    install_editable
    keybank setup
    ;;
  "")
    install_editable
    keybank setup --agents all
    ;;
  *)
    echo "Error: unknown argument $1" >&2
    usage >&2
    exit 1
    ;;
esac
