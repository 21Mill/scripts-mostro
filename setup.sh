#!/bin/bash
# ============================================================================
# setup.sh — Configuración interactiva de scripts Mostro
# Genera el archivo .env con las rutas y credenciales del usuario.
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

echo -e "${BOLD}${CYAN}"
echo "🧌 Mostro Scripts — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${NC}"
echo "Este asistente te guiará para configurar los scripts."
echo "Pulsa Enter para aceptar el valor por defecto [entre corchetes]."
echo ""

# --- Helpers ---
ask() {
    local var_name="$1"
    local prompt="$2"
    local default="$3"
    local value

    echo -en "${BOLD}$prompt${NC} [${CYAN}$default${NC}]: "
    read -r value
    value="${value:-$default}"

    # Expandir ~ a $HOME
    value="${value/#\~/$HOME}"

    eval "$var_name='$value'"
}

validate_dir() {
    local path="$1"
    local label="$2"
    if sudo test -d "$path" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $label: $path"
        return 0
    else
        echo -e "  ${YELLOW}⚠${NC} $label: $path ${YELLOW}(no encontrado, se usará igualmente)${NC}"
        return 0
    fi
}

validate_file() {
    local path="$1"
    local label="$2"
    if sudo test -f "$path" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $label: $path"
        return 0
    else
        echo -e "  ${YELLOW}⚠${NC} $label: $path ${YELLOW}(no encontrado)${NC}"
        return 0
    fi
}

validate_bin() {
    local path="$1"
    local label="$2"
    if command -v "$path" &>/dev/null || sudo test -f "$path" 2>/dev/null; then
        local ver
        ver=$(timeout 3 "$path" --version 2>/dev/null || echo "instalado")
        echo -e "  ${GREEN}✓${NC} $label: $path ($ver)"
        return 0
    else
        echo -e "  ${YELLOW}⚠${NC} $label: $path ${YELLOW}(no encontrado)${NC}"
        return 0
    fi
}

# --- Cargar valores existentes si hay .env ---
if [ -f "$ENV_FILE" ]; then
    echo -e "${BLUE}ℹ${NC} Se encontró un .env existente. Los valores actuales se usarán como defaults.\n"
    set -a
    source "$ENV_FILE"
    set +a
fi

# ============================================================
# SECCIÓN 1: Rutas de mostrod
# ============================================================
echo -e "${BOLD}${CYAN}━━━ 1/5: mostrod (daemon principal) ━━━${NC}\n"

ask MOSTROD_SRC     "Directorio de fuentes de mostrod" "${MOSTROD_SRC:-/opt/mostro}"
ask MOSTROD_CONFIG  "Archivo de configuración"         "${MOSTROD_CONFIG:-$MOSTROD_SRC/settings.toml}"
ask MOSTROD_BIN     "Binario de mostrod"               "${MOSTROD_BIN:-/usr/local/bin/mostrod}"
ask MOSTROD_SERVICE "Servicio systemd"                 "${MOSTROD_SERVICE:-mostro.service}"

echo ""
validate_dir  "$MOSTROD_SRC"    "Fuentes"
validate_file "$MOSTROD_CONFIG" "Config"
validate_bin  "$MOSTROD_BIN"    "Binario"
echo ""

# ============================================================
# SECCIÓN 2: Rutas de mostrix
# ============================================================
echo -e "${BOLD}${CYAN}━━━ 2/5: mostrix (admin TUI) ━━━${NC}\n"

ask MOSTRIX_SRC    "Directorio de fuentes de mostrix" "${MOSTRIX_SRC:-$HOME/mostro-sources/mostrix}"
ask MOSTRIX_CONFIG "Archivo de configuración"         "${MOSTRIX_CONFIG:-$HOME/.mostrix/settings.toml}"
ask MOSTRIX_BIN    "Binario de mostrix"               "${MOSTRIX_BIN:-/usr/local/bin/mostrix}"

echo ""
validate_dir  "$MOSTRIX_SRC"    "Fuentes"
validate_file "$MOSTRIX_CONFIG" "Config"
validate_bin  "$MOSTRIX_BIN"    "Binario"
echo ""

# ============================================================
# SECCIÓN 3: Rutas de mostro-watchdog
# ============================================================
echo -e "${BOLD}${CYAN}━━━ 3/5: mostro-watchdog ━━━${NC}\n"

ask WATCHDOG_SRC     "Directorio de fuentes de watchdog" "${WATCHDOG_SRC:-$HOME/mostro-sources/mostro-watchdog}"
ask WATCHDOG_CONFIG  "Archivo de configuración"          "${WATCHDOG_CONFIG:-$MOSTROD_SRC/config.toml}"
ask WATCHDOG_BIN     "Binario de mostro-watchdog"        "${WATCHDOG_BIN:-/usr/local/bin/mostro-watchdog}"
ask WATCHDOG_SERVICE "Servicio systemd"                  "${WATCHDOG_SERVICE:-mostro-watchdog.service}"

echo ""
validate_dir  "$WATCHDOG_SRC"    "Fuentes"
validate_file "$WATCHDOG_CONFIG" "Config"
validate_bin  "$WATCHDOG_BIN"    "Binario"
echo ""

# ============================================================
# SECCIÓN 4: Rutas generales
# ============================================================
echo -e "${BOLD}${CYAN}━━━ 4/5: Rutas generales ━━━${NC}\n"

ask BACKUP_DIR   "Directorio de backups"     "${BACKUP_DIR:-$HOME/mostro-sources/backups}"
ask MOSTRO_DB    "Base de datos SQLite"       "${MOSTRO_DB:-$MOSTROD_SRC/mostro.db}"
ask MOSTRO_LOG   "Archivo de log (vacío = journalctl)" "${MOSTRO_LOG:-}"
ask BOT_SERVICE  "Servicio systemd del bot"   "${BOT_SERVICE:-mostrobot.service}"

echo ""
validate_file "$MOSTRO_DB" "Base de datos"
if [ -n "$MOSTRO_LOG" ]; then
    validate_file "$MOSTRO_LOG" "Log"
else
    echo -e "  ${GREEN}✓${NC} Logs: journalctl -u $MOSTROD_SERVICE"
fi
echo ""

# ============================================================
# SECCIÓN 5: Telegram
# ============================================================
echo -e "${BOLD}${CYAN}━━━ 5/5: Telegram (opcional) ━━━${NC}\n"
echo "Si no usas el bot de Telegram, deja los campos vacíos."
echo ""

ask TELEGRAM_TOKEN       "Token del bot de Telegram"       "${TELEGRAM_TOKEN:-}"
ask TELEGRAM_CHAT_ID     "Chat ID para ofertas"            "${TELEGRAM_CHAT_ID:-}"
ask TELEGRAM_TEST_CHAT_ID "Chat ID para test (vacío = mismo que ofertas)" "${TELEGRAM_TEST_CHAT_ID:-$TELEGRAM_CHAT_ID}"
ask MOSTRO_PUBKEY        "Clave pública de tu Mostro"      "${MOSTRO_PUBKEY:-}"
ask MOSTRO_RELAY         "URL del relay Nostr"             "${MOSTRO_RELAY:-wss://relay.mostro.network}"

# --- Test de Telegram ---
if [ -n "$TELEGRAM_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
    echo ""
    echo -en "${BOLD}¿Enviar mensaje de prueba a Telegram? [s/N]: ${NC}"
    read -r test_tg
    if [[ "$test_tg" =~ ^[sS]$ ]]; then
        response=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=🧌 Mostro Scripts configurado correctamente!" 2>&1)
        if echo "$response" | grep -q '"ok":true'; then
            echo -e "${GREEN}✅ Mensaje enviado correctamente${NC}"
        else
            echo -e "${RED}❌ Error: $response${NC}"
        fi
    fi
fi

# ============================================================
# GENERAR .env
# ============================================================
echo ""
echo -e "${BOLD}${CYAN}━━━ Generando .env ━━━${NC}\n"

# Backup del .env anterior si existe
if [ -f "$ENV_FILE" ]; then
    cp "$ENV_FILE" "${ENV_FILE}.bak"
    echo -e "${BLUE}ℹ${NC} Backup del .env anterior: ${ENV_FILE}.bak"
fi

cat > "$ENV_FILE" << ENVEOF
# ============================================================================
# Mostro Scripts — Configuración
# Generado por setup.sh el $(date '+%Y-%m-%d %H:%M:%S')
# ============================================================================

# --- mostrod ---
MOSTROD_SRC=$MOSTROD_SRC
MOSTROD_CONFIG=$MOSTROD_CONFIG
MOSTROD_BIN=$MOSTROD_BIN
MOSTROD_SERVICE=$MOSTROD_SERVICE

# --- mostrix ---
MOSTRIX_SRC=$MOSTRIX_SRC
MOSTRIX_CONFIG=$MOSTRIX_CONFIG
MOSTRIX_BIN=$MOSTRIX_BIN

# --- mostro-watchdog ---
WATCHDOG_SRC=$WATCHDOG_SRC
WATCHDOG_CONFIG=$WATCHDOG_CONFIG
WATCHDOG_BIN=$WATCHDOG_BIN
WATCHDOG_SERVICE=$WATCHDOG_SERVICE

# --- General ---
BACKUP_DIR=$BACKUP_DIR
MOSTRO_DB=$MOSTRO_DB
MOSTRO_LOG=$MOSTRO_LOG
BOT_SERVICE=$BOT_SERVICE

# --- Telegram ---
TELEGRAM_TOKEN=$TELEGRAM_TOKEN
TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID
TELEGRAM_TEST_CHAT_ID=$TELEGRAM_TEST_CHAT_ID

# --- Mostro Nostr ---
MOSTRO_PUBKEY=$MOSTRO_PUBKEY
MOSTRO_RELAY=$MOSTRO_RELAY
ENVEOF

echo -e "${GREEN}✅ Archivo .env generado: $ENV_FILE${NC}"
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Configuración completada${NC}"
echo ""
echo "Puedes editar $ENV_FILE manualmente o volver a ejecutar este script."
echo "Para reconfigurar: ${BOLD}./setup.sh${NC}"
