#!/bin/bash
# ============================================================================
# env.sh — Carga configuración común para los scripts de Mostro
# ============================================================================

# Directorio del script que llama (para _OWNER_HOME)
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)"
# Raíz del repo: admin/ está un nivel dentro de scripts/
_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$_REPO_ROOT/.env" ]; then
    set -a
    source "$_REPO_ROOT/.env"
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
BOT_NOSTR_SERVICE="${BOT_NOSTR_SERVICE:-mostrobot-nostr.service}"
ACCOUNTING_SERVICE="${ACCOUNTING_SERVICE:-mostro-accounting.service}"
SNAPSHOT_TIMER="${SNAPSHOT_TIMER:-mostro-snapshot.timer}"
BACKUP_DIR="${BACKUP_DIR:-$_OWNER_HOME/mostro-sources/backups}"
MOSTRO_DB="${MOSTRO_DB:-$MOSTROD_SRC/mostro.db}"

# Instantáneas de solo lectura publicadas por mostro-snapshot.timer. Todo lo que solo
# consulta lee de aquí SIN sudo. MOSTRO_DB sigue apuntando a la base real porque update.sh
# necesita el original para el respaldo previo a una actualización, y eso sí va con sudo.
MOSTRO_DB_RO="${MOSTRO_DB_RO:-/var/lib/mostro-snapshot/mostro.db}"
MOSTRO_DISPUTES_DB_RO="${MOSTRO_DISPUTES_DB_RO:-/var/lib/mostro-snapshot/disputes.db}"
MOSTRO_LOG="${MOSTRO_LOG:-}"

# --- Colores ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# --- Helper: consultar una base de datos en solo lectura ---
# Uso: sql_ro "$MOSTRO_DB_RO" "SELECT ..."   |   sql_ro "$db" -json "SELECT ..."
#
# Lee las instantáneas que publica mostro-snapshot.timer. Antes cada script llamaba a
# 'sudo -u mostro sqlite3', una regla NOPASSWD que en la práctica daba shell completa como
# mostro: sqlite3 ejecuta órdenes del sistema con .shell y, aun con -readonly, un
# SELECT writefile() escribe ficheros desde SQL puro.
sql_ro() {
    local db="$1"
    shift
    sqlite3 -readonly "$db" "$@" 2>/dev/null
}

# --- Helper: enviar un mensaje por Telegram ---
# Uso: telegram_enviar <token> <chat_id> <texto> [parse_mode]
#
# Devuelve por stdout la respuesta de la API, para quien necesite comprobarla. El token y
# el chat van como argumentos porque cada script los saca de un sitio distinto: el
# config.toml del watchdog, las variables TELEGRAM_MONITOR_* o el propio .env.
telegram_enviar() {
    local token="$1" chat="$2" texto="$3" parse_mode="${4:-}"
    local args=(--data-urlencode "chat_id=${chat}" --data-urlencode "text=${texto}")
    [ -n "$parse_mode" ] && args+=(--data-urlencode "parse_mode=${parse_mode}")
    curl -s --max-time 30 -X POST \
        "https://api.telegram.org/bot${token}/sendMessage" "${args[@]}"
}

# --- Helper: ejecutar en un directorio, con sudo solo si hace falta ---
#
# Se intenta primero directamente. Que el directorio sea de otro usuario no significa que
# no podamos leerlo: /opt/mostro es 750 de mostro y admin pertenece a ese grupo, así que
# consultar la versión en su Cargo.toml no necesita privilegio ninguno. Solo se recurre a
# sudo cuando el intento directo falla de verdad —git, por ejemplo, se niega a operar en un
# repositorio de otro dueño— y con -n para que falle en el acto en vez de quedarse pidiendo
# una contraseña que en cron o por ssh no va a llegar nunca.
run_in_dir() {
    local dir="$1"
    shift
    if ( cd "$dir" && eval "$*" ) 2>/dev/null; then
        return 0
    fi
    local owner
    owner=$(stat -c '%U' "$dir" 2>/dev/null)
    if [ -z "$owner" ] || [ "$owner" = "$(whoami)" ]; then
        return 1
    fi
    sudo -n -u "$owner" env PATH="$PATH:$HOME/.cargo/bin" \
        bash -c "cd '$dir' && $*" 2>/dev/null
}
