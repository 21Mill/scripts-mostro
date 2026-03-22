#!/bin/bash
# ============================================================================
# mostro-status.sh — Estado de todos los componentes Mostro
# Ubicación: ~/mostro-sources/scripts/mostro-status.sh
# ============================================================================

BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}${CYAN}"
echo "🧌 Mostro Status"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${NC}"

# --- Servicios ---
echo -e "${BOLD}Servicios:${NC}"
for svc in mostro.service mostro-watchdog.service mostrobot.service; do
    name=$(echo "$svc" | sed 's/.service//')
    if sudo systemctl is-active "$svc" &>/dev/null; then
        uptime=$(sudo systemctl show "$svc" --property=ActiveEnterTimestamp --value 2>/dev/null)
        echo -e "  ${GREEN}●${NC} $name  ${GREEN}activo${NC}  (desde $uptime)"
    else
        echo -e "  ${RED}●${NC} $name  ${RED}inactivo${NC}"
    fi
done

echo ""

# --- Versiones instaladas vs disponibles ---
echo -e "${BOLD}Versiones:${NC}"

declare -A SOURCES=(
    [mostrod]="/opt/mostro"
    [mostrix]="$HOME/mostro-sources/mostrix"
    [mostro-watchdog]="$HOME/mostro-sources/mostro-watchdog"
)

for comp in mostrod mostrix mostro-watchdog; do
    src="${SOURCES[$comp]}"
    local_ver=$(grep "^version" "$src/Cargo.toml" 2>/dev/null | head -1 | sed 's/.*"\(.*\)"/\1/')

    # Fetch silencioso
    remote_ver=""
    if [ -d "$src/.git" ]; then
        cd "$src"
        git fetch origin --quiet 2>/dev/null
        remote_ver=$(git show origin/main:Cargo.toml 2>/dev/null | grep "^version" | head -1 | sed 's/.*"\(.*\)"/\1/')
        pending=$(git log HEAD..origin/main --oneline 2>/dev/null | wc -l)
    fi

    if [ "$local_ver" = "$remote_ver" ]; then
        echo -e "  ${GREEN}✓${NC} $comp  ${BOLD}v$local_ver${NC}  ${GREEN}(actualizado)${NC}"
    elif [ -n "$remote_ver" ]; then
        echo -e "  ${YELLOW}↑${NC} $comp  ${BOLD}v$local_ver${NC}  → ${YELLOW}v$remote_ver${NC}  ($pending commits)"
    else
        echo -e "  ${BLUE}?${NC} $comp  ${BOLD}v$local_ver${NC}  (no se pudo comprobar remoto)"
    fi
done

echo ""

# --- Base de datos ---
echo -e "${BOLD}Base de datos:${NC}"
DB="/zfs_vault/mostro/mostro.db"
if [ -f "$DB" ]; then
    size=$(du -h "$DB" 2>/dev/null | cut -f1)
    mod=$(stat -c %y "$DB" 2>/dev/null | cut -d'.' -f1)
    trades=$(sudo sqlite3 "$DB" "SELECT COUNT(*) FROM orders WHERE status='success';" 2>/dev/null || echo "?")
    pending=$(sudo sqlite3 "$DB" "SELECT COUNT(*) FROM orders WHERE status='pending';" 2>/dev/null || echo "?")
    echo -e "  Tamaño: $size | Modificado: $mod"
    echo -e "  Trades completados: $trades | Pendientes: $pending"
fi

echo ""

# --- Backups ---
BACKUP_BASE="$HOME/mostro-sources/backups"
if [ -d "$BACKUP_BASE" ]; then
    backup_count=$(ls -d "$BACKUP_BASE"/*/ 2>/dev/null | wc -l)
    backup_size=$(du -sh "$BACKUP_BASE" 2>/dev/null | cut -f1)
    echo -e "${BOLD}Backups:${NC} $backup_count disponibles ($backup_size)"
fi

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "Actualizar: ${BOLD}./mostro-update.sh${NC} | Rollback: ${BOLD}./mostro-rollback.sh${NC}"
