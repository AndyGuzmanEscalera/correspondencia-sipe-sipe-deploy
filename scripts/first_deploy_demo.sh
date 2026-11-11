#!/usr/bin/env bash
set -Eeuo pipefail
trap 'printf "\nDETENIDO: primer deploy incompleto; no se ejecutará reversión automática.\n" >&2' ERR
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf "\nCORRESPONDENCIA DEMO\n"
if [[ "$(uname -s)" != Linux ]]; then
    printf 'DETENIDO: este script se ejecuta únicamente en Linux/Debian.\n' >&2
    exit 1
fi
for tool in docker git python3; do
    command -v "$tool" >/dev/null || { printf 'Falta herramienta: %s\n' "$tool" >&2; exit 1; }
done
exec python3 -B "$SCRIPT_DIR/first_deploy_demo.py" "$@"
