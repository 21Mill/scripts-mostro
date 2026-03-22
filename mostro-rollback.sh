#!/bin/bash
# ============================================================================
# mostro-rollback.sh — Restaurar versión anterior de un componente Mostro
# Ubicación: ~/mostro-sources/scripts/mostro-rollback.sh
#
# Uso:
#   ./mostro-rollback.sh              # Lista backups disponibles
#   ./mostro-rollback.sh mostrod      # Restaurar mostrod del último backup
#   ./mostro-rollback.sh watchdog     # Restaurar watchdog del último backup
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

BACKUP_BASE="$HOME/mostro-sources/backups"
TARGET="${1:-}"

declare -A BINS=(
    [mostrod]="/usr/local/bin/mostrod"
    [mostrix]="/usr/local/bin/mostrix"
    [mostro-watchdog]="/usr/local/bin/mostro-watchdog"
)

declare -A CONFIGS=(
    [mostrod]="/opt/mostro/settings.toml"
    [mostrix]="$HOME/.mostrix/settings.toml"
    [mostro-watchdog]="/opt/mostro/config.toml"
)

declare -A SERVICES=(
    [mostrod]="mostro.service"
    [mostrix]=""
    [mostro-watchdog]="mostro-watchdog.service"
)

# Lista backups
if [ -z "$TARGET" ]; then
    echo -e "${BOLD}${CYAN}🧌 Mostro Rollback — Backups disponibles${NC}\n"

    if [ ! -d "$BACKUP_BASE" ]; then
        echo -e "${RED}No hay backups en $BACKUP_BASE${NC}"
        exit 1
    fi

    for backup in $(ls -rd "$BACKUP_BASE"/*/ 2>/dev/null); do
        ts=$(basename "$backup")
        echo -e "${BOLD}📦 $ts${NC}"
        for comp in "$backup"*/; do
            [ -d "$comp" ] || continue
            name=$(basename "$comp")
            files=$(ls "$comp" 2>/dev/null | tr '\n' ', ' | sed 's/,$//')
            echo "   └─ $name: $files"
        done
        echo ""
    done

    echo "Uso: $0 <mostrod|mostrix|watchdog>"
    exit 0
fi

# Mapear nombre corto
case "$TARGET" in
    mostrod) COMP="mostrod" ;;
    mostrix) COMP="mostrix" ;;
    watchdog) COMP="mostro-watchdog" ;;
    *)
        echo -e "${RED}Componente no reconocido: $TARGET${NC}"
        echo "Opciones: mostrod, mostrix, watchdog"
        exit 1
        ;;
esac

# Buscar último backup
LATEST=$(ls -rd "$BACKUP_BASE"/*/"$COMP" 2>/dev/null | head -1)

if [ -z "$LATEST" ] || [ ! -d "$LATEST" ]; then
    echo -e "${RED}No hay backup de $COMP${NC}"
    exit 1
fi

echo -e "${BOLD}${CYAN}🧌 Restaurar $COMP${NC}\n"
echo -e "Backup: ${BOLD}$LATEST${NC}"
echo "Contenido:"
ls -la "$LATEST/"
echo ""

echo -en "${BOLD}¿Restaurar $COMP desde este backup? [s/N]: ${NC}"
read -r response
if [[ ! "$response" =~ ^[sS]$ ]]; then
    echo "Cancelado."
    exit 0
fi

BIN_NAME=$(basename "${BINS[$COMP]}")
SERVICE="${SERVICES[$COMP]}"

# Parar servicio
if [ -n "$SERVICE" ] && sudo systemctl is-active "$SERVICE" &>/dev/null; then
    echo -e "${BLUE}Deteniendo $SERVICE...${NC}"
    sudo systemctl stop "$SERVICE"
fi

# Restaurar binario
if [ -f "$LATEST/$BIN_NAME" ]; then
    sudo install "$LATEST/$BIN_NAME" "${BINS[$COMP]}"
    echo -e "${GREEN}✅ Binario restaurado: ${BINS[$COMP]}${NC}"
fi

# Restaurar config si existe y el usuario lo quiere
CONFIG_NAME=$(basename "${CONFIGS[$COMP]}")
if [ -f "$LATEST/$CONFIG_NAME" ]; then
    echo -en "${BOLD}¿Restaurar también la configuración? [s/N]: ${NC}"
    read -r resp_config
    if [[ "$resp_config" =~ ^[sS]$ ]]; then
        sudo cp "$LATEST/$CONFIG_NAME" "${CONFIGS[$COMP]}"
        echo -e "${GREEN}✅ Config restaurada: ${CONFIGS[$COMP]}${NC}"
    fi
fi

# Reiniciar servicio
if [ -n "$SERVICE" ]; then
    echo -e "${BLUE}Reiniciando $SERVICE...${NC}"
    sudo systemctl start "$SERVICE"
    sleep 2
    if sudo systemctl is-active "$SERVICE" &>/dev/null; then
        echo -e "${GREEN}✅ $SERVICE arrancado correctamente${NC}"
    else
        echo -e "${RED}❌ $SERVICE falló al arrancar${NC}"
        sudo journalctl -u "$SERVICE" --no-pager -n 10
    fi
fi

# Verificar versión
echo ""
echo -e "${BOLD}Versión restaurada:${NC}"
"${BINS[$COMP]}" --version 2>/dev/null || echo "(no se pudo obtener versión)"
