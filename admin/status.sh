#!/bin/bash
# ============================================================================
# mostro-status.sh — Estado de todos los componentes Mostro
# ============================================================================

source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

echo -e "${BOLD}${CYAN}"
echo "🧌 Mostro Status"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${NC}"

# --- Servicios ---
echo -e "${BOLD}Servicios:${NC}"
for svc in "$MOSTROD_SERVICE" "$WATCHDOG_SERVICE" "$BOT_SERVICE"; do
    [ -z "$svc" ] && continue
    name=$(echo "$svc" | sed 's/.service//')
    if sudo systemctl is-active "$svc" &>/dev/null; then
        uptime=$(sudo systemctl show "$svc" --property=ActiveEnterTimestamp --value 2>/dev/null)
        echo -e "  ${GREEN}●${NC} $name  ${GREEN}activo${NC}  (desde $uptime)"
    else
        echo -e "  ${RED}●${NC} $name  ${RED}inactivo${NC}"
    fi
done

echo ""

# --- Versiones ---
echo -e "${BOLD}Versiones:${NC}"

declare -A SOURCES=(
    [mostrod]="$MOSTROD_SRC"
    [mostrix]="$MOSTRIX_SRC"
    [mostro-watchdog]="$WATCHDOG_SRC"
)

for comp in mostrod mostrix mostro-watchdog; do
    src="${SOURCES[$comp]}"

    if ! sudo test -d "$src/.git" 2>/dev/null; then
        echo -e "  ${BLUE}?${NC} $comp  ${YELLOW}(fuentes no encontradas: $src)${NC}"
        continue
    fi

    local_ver=$(run_in_dir "$src" "grep '^version' Cargo.toml 2>/dev/null | head -1 | sed 's/.*\"\(.*\)\"/\1/'")

    remote_ver=""
    pending=0
    run_in_dir "$src" "git fetch origin --quiet" 2>/dev/null
    remote_ver=$(run_in_dir "$src" "git show origin/main:Cargo.toml 2>/dev/null | grep '^version' | head -1 | sed 's/.*\"\(.*\)\"/\1/'")
    pending=$(run_in_dir "$src" "git log HEAD..origin/main --oneline 2>/dev/null | wc -l")

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
if sudo test -f "$MOSTRO_DB" 2>/dev/null; then
    size=$(sudo du -h "$MOSTRO_DB" 2>/dev/null | cut -f1)
    mod=$(sudo stat -c %y "$MOSTRO_DB" 2>/dev/null | cut -d'.' -f1)
    trades=$(sudo sqlite3 "$MOSTRO_DB" "SELECT COUNT(*) FROM orders WHERE status='success';" 2>/dev/null || echo "?")
    pending=$(sudo sqlite3 "$MOSTRO_DB" "SELECT COUNT(*) FROM orders WHERE status='pending';" 2>/dev/null || echo "?")
    echo -e "  Tamaño: $size | Modificado: $mod"
    echo -e "  Trades completados: $trades | Pendientes: $pending"
else
    echo -e "  ${YELLOW}No encontrada: $MOSTRO_DB${NC}"
fi

echo ""

# --- Backups ---
if [ -d "$BACKUP_DIR" ]; then
    backup_count=$(ls -d "$BACKUP_DIR"/*/ 2>/dev/null | wc -l)
    backup_size=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
    echo -e "${BOLD}Backups:${NC} $backup_count disponibles ($backup_size)"
fi

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "Actualizar: ${BOLD}./update.sh${NC} | Rollback: ${BOLD}./rollback.sh${NC}"
