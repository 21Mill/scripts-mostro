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
for svc in "$MOSTROD_SERVICE" "$WATCHDOG_SERVICE" "$BOT_SERVICE" \
           "$BOT_NOSTR_SERVICE" "$ACCOUNTING_SERVICE" "$SNAPSHOT_TIMER"; do
    [ -z "$svc" ] && continue
    name=$(echo "$svc" | sed 's/\.service$//')
    # Sin sudo: consultar el estado de una unidad no requiere privilegios. Cuando lo
    # llevaba, un status.sh lanzado sin terminal (cron, ssh no interactivo) no podía pedir
    # la contraseña y daba por inactivos todos los servicios, que estaban corriendo.
    if systemctl is-active --quiet "$svc"; then
        uptime=$(systemctl show "$svc" --property=ActiveEnterTimestamp --value 2>/dev/null)
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

    if [ ! -d "$src/.git" ]; then
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
# Se informa sobre la instantánea, no sobre la base real: /data/mostro es 700 de mostro y
# admin no puede ni hacerle stat. La instantánea se refresca cada minuto solo si la base ha
# cambiado, así que su fecha es la del último cambio real; por eso se muestra su antigüedad,
# que delata un mostro-snapshot.timer parado.
if [ -f "$MOSTRO_DB_RO" ]; then
    size=$(du -h "$MOSTRO_DB_RO" 2>/dev/null | cut -f1)
    mod=$(stat -c %y "$MOSTRO_DB_RO" 2>/dev/null | cut -d'.' -f1)
    edad=$(( ($(date +%s) - $(stat -c %Y "$MOSTRO_DB_RO")) / 60 ))
    trades=$(sql_ro "$MOSTRO_DB_RO" "SELECT COUNT(*) FROM orders WHERE status='success';" || echo "?")
    pending=$(sql_ro "$MOSTRO_DB_RO" "SELECT COUNT(*) FROM orders WHERE status='pending';" || echo "?")
    echo -e "  Tamaño: ${size:-?} | Último cambio: ${mod:-?} (hace ${edad} min)"
    echo -e "  Trades completados: ${trades:-?} | Pendientes: ${pending:-?}"
    echo -e "  ${BLUE}Instantánea:${NC} $MOSTRO_DB_RO"
else
    echo -e "  ${YELLOW}No hay instantánea en $MOSTRO_DB_RO${NC}"
    echo -e "  ${YELLOW}Comprueba mostro-snapshot.timer${NC}"
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
