#!/bin/bash
# ============================================================================
# mostro-env.sh — Carga configuración común para los scripts de Mostro
# ============================================================================

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)"

if [ -f "$_SCRIPT_DIR/.env" ]; then
    set -a
    source "$_SCRIPT_DIR/.env"
    set +a
fi

# Home del propietario del directorio de scripts (no del usuario que ejecuta)
_OWNER=$(stat -c '%U' "$_SCRIPT_DIR" 2>/dev/null)
_OWNER_HOME=$(getent passwd "$_OWNER" 2>/dev/null | cut -d: -f6)
_OWNER_HOME="${_OWNER_HOME:-$HOME}"

# --- Defaults (según guía oficial mostro.community) ---
MOSTROD_SRC="${MOSTROD_SRC:-/opt/mostro}"
MOSTROD_CONFIG="${MOSTROD_CONFIG:-$MOSTROD_SRC/settings.toml}"
MOSTROD_BIN="${MOSTROD_BIN:-/usr/local/bin/mostrod}"
MOSTROD_SERVICE="${MOSTROD_SERVICE:-mostro.service}"

MOSTRIX_SRC="${MOSTRIX_SRC:-$_OWNER_HOME/mostro-sources/mostrix}"
MOSTRIX_CONFIG="${MOSTRIX_CONFIG:-$_OWNER_HOME/.mostrix/settings.toml}"
MOSTRIX_BIN="${MOSTRIX_BIN:-/usr/local/bin/mostrix}"

WATCHDOG_SRC="${WATCHDOG_SRC:-$_OWNER_HOME/mostro-sources/mostro-watchdog}"
WATCHDOG_CONFIG="${WATCHDOG_CONFIG:-$MOSTROD_SRC/config.toml}"
WATCHDOG_BIN="${WATCHDOG_BIN:-/usr/local/bin/mostro-watchdog}"
WATCHDOG_SERVICE="${WATCHDOG_SERVICE:-mostro-watchdog.service}"

BOT_SERVICE="${BOT_SERVICE:-mostrobot.service}"
BACKUP_DIR="${BACKUP_DIR:-$_OWNER_HOME/mostro-sources/backups}"
MOSTRO_DB="${MOSTRO_DB:-$MOSTROD_SRC/mostro.db}"
MOSTRO_LOG="${MOSTRO_LOG:-}"

# --- Colores ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# --- Helper: ejecutar en directorio como su propietario ---
run_in_dir() {
    local dir="$1"
    shift
    local owner
    owner=$(stat -c '%U' "$dir" 2>/dev/null)
    if [ "$owner" != "$(whoami)" ]; then
        sudo -u "$owner" bash -c "cd '$dir' && $*"
    else
        ( cd "$dir" && eval "$*" )
    fi
}
