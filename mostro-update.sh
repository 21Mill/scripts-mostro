#!/bin/bash
# ============================================================================
# mostro-update.sh — Actualización segura de componentes Mostro
# Ubicación: ~/mostro-sources/scripts/mostro-update.sh
#
# Uso:
#   ./mostro-update.sh              # Comprobar todos los componentes
#   ./mostro-update.sh mostrod      # Solo mostrod
#   ./mostro-update.sh mostrix      # Solo mostrix
#   ./mostro-update.sh watchdog     # Solo mostro-watchdog
#   ./mostro-update.sh --check      # Solo comprobar, no actualizar
# ============================================================================
 
set -euo pipefail
 
# --- Colores ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'
 
# --- Rutas ---
MOSTROD_SRC="/opt/mostro"
MOSTROD_CONFIG="/opt/mostro/settings.toml"
MOSTROD_BIN="/usr/local/bin/mostrod"
MOSTROD_SERVICE="mostro.service"
 
MOSTRIX_SRC="/home/admin/mostro-sources/mostrix"
MOSTRIX_CONFIG="/home/admin/.mostrix/settings.toml"
MOSTRIX_BIN="/usr/local/bin/mostrix"
 
WATCHDOG_SRC="/home/admin/mostro-sources/mostro-watchdog"
WATCHDOG_CONFIG="/opt/mostro/config.toml"
WATCHDOG_BIN="/usr/local/bin/mostro-watchdog"
WATCHDOG_SERVICE="mostro-watchdog.service"
 
BACKUP_DIR="/home/admin/mostro-sources/backups/$(date +%Y%m%d_%H%M%S)"
CHECK_ONLY=false
TARGET=""
 
# --- Parsear argumentos ---
for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=true ;;
        mostrod) TARGET="mostrod" ;;
        mostrix) TARGET="mostrix" ;;
        watchdog) TARGET="watchdog" ;;
        --help|-h)
            echo "Uso: $0 [componente] [--check]"
            echo ""
            echo "Componentes: mostrod, mostrix, watchdog"
            echo "Sin argumentos: comprueba todos"
            echo "--check: solo muestra versiones, no actualiza"
            exit 0
            ;;
    esac
done
 
# --- Funciones ---
 
log_info()  { echo -e "${BLUE}ℹ ${NC}$1"; }
log_ok()    { echo -e "${GREEN}✅ ${NC}$1"; }
log_warn()  { echo -e "${YELLOW}⚠️  ${NC}$1"; }
log_error() { echo -e "${RED}❌ ${NC}$1"; }
log_header(){ echo -e "\n${BOLD}${CYAN}━━━ $1 ━━━${NC}\n"; }

# Helper: ejecutar comando en un directorio como su propietario
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
 
get_local_version() {
    local src_dir="$1"
    run_in_dir "$src_dir" "grep '^version' Cargo.toml 2>/dev/null | head -1 | sed 's/.*\"\(.*\)\"/\1/'"
}
 
get_remote_version() {
    local src_dir="$1"
    run_in_dir "$src_dir" "git fetch origin --tags --quiet 2>/dev/null; git show origin/main:Cargo.toml 2>/dev/null | grep '^version' | head -1 | sed 's/.*\"\(.*\)\"/\1/'"
}
 
get_latest_tag() {
    local src_dir="$1"
    run_in_dir "$src_dir" "git tag --sort=-v:refname 2>/dev/null | head -1"
}
 
get_pending_commits() {
    local src_dir="$1"
    run_in_dir "$src_dir" "git log HEAD..origin/main --oneline 2>/dev/null"
}
 
check_config_changes() {
    local src_dir="$1"
    local config_example=""
 
    for f in "settings.tpl.toml" "settings.toml.example" "config.example.toml" "config.toml.example"; do
        if run_in_dir "$src_dir" "git show origin/main:$f" &>/dev/null; then
            config_example="$f"
            break
        fi
    done
 
    if [ -z "$config_example" ]; then
        return 1
    fi
 
    local local_hash remote_hash
    local_hash=$(run_in_dir "$src_dir" "git show HEAD:$config_example 2>/dev/null | md5sum | cut -d' ' -f1")
    remote_hash=$(run_in_dir "$src_dir" "git show origin/main:$config_example 2>/dev/null | md5sum | cut -d' ' -f1")
 
    if [ "$local_hash" != "$remote_hash" ]; then
        echo "$config_example"
        return 0
    fi
    return 1
}
 
show_config_diff() {
    local src_dir="$1"
    local config_file="$2"
 
    echo -e "${YELLOW}Cambios en la plantilla de configuración ($config_file):${NC}"
    run_in_dir "$src_dir" "git diff HEAD..origin/main -- $config_file 2>/dev/null | head -60"
    echo ""
}
 
backup_component() {
    local name="$1"
    local bin="$2"
    local config="$3"
 
    mkdir -p "$BACKUP_DIR/$name"
 
    if sudo test -f "$bin"; then
        sudo cp "$bin" "$BACKUP_DIR/$name/" 2>/dev/null && \
            log_info "Binario respaldado: $BACKUP_DIR/$name/$(basename $bin)"
    fi
 
    if sudo test -f "$config"; then
        sudo cp "$config" "$BACKUP_DIR/$name/" 2>/dev/null && \
            log_info "Config respaldada: $BACKUP_DIR/$name/$(basename $config)"
    fi
}
 
build_rust_component() {
    local src_dir="$1"
    local name="$2"
 
    log_info "Descargando cambios..."
    run_in_dir "$src_dir" "git pull --quiet"
 
    log_info "Compilando $name (esto puede tardar unos minutos)..."
    if run_in_dir "$src_dir" "cargo build --release 2>&1 | tail -5"; then
        log_ok "Compilación exitosa"
        return 0
    else
        log_error "Error de compilación"
        return 1
    fi
}
 
install_binary() {
    local src_dir="$1"
    local bin_name="$2"
    local dest="$3"
 
    local src_bin="$src_dir/target/release/$bin_name"
    if ! sudo test -f "$src_bin"; then
        log_error "Binario no encontrado: $src_bin"
        return 1
    fi
 
    sudo install "$src_bin" "$dest"
    log_ok "Binario instalado: $dest"
}
 
restart_service() {
    local service="$1"
 
    if sudo systemctl is-enabled "$service" &>/dev/null; then
        log_info "Reiniciando $service..."
        sudo systemctl restart "$service"
        sleep 2
        if sudo systemctl is-active "$service" &>/dev/null; then
            log_ok "$service arrancado correctamente"
        else
            log_error "$service falló al arrancar"
            sudo journalctl -u "$service" --no-pager -n 10
            return 1
        fi
    else
        log_warn "$service no está habilitado"
    fi
}
 
confirm() {
    local prompt="$1"
    echo -en "${BOLD}$prompt [s/N]: ${NC}"
    read -r response
    [[ "$response" =~ ^[sS]$ ]]
}
 
# --- Actualizar un componente ---
 
update_component() {
    local name="$1"
    local src_dir="$2"
    local bin_path="$3"
    local bin_name="$4"
    local config_path="$5"
    local service="$6"
 
    log_header "$name"
 
    if ! sudo test -d "$src_dir/.git"; then
        log_error "Directorio de fuentes no encontrado: $src_dir"
        return 1
    fi
 
    # Versiones
    local local_ver remote_ver
    local_ver=$(get_local_version "$src_dir")
    remote_ver=$(get_remote_version "$src_dir")
    local latest_tag
    latest_tag=$(get_latest_tag "$src_dir")
 
    echo -e "  Versión instalada:  ${BOLD}$local_ver${NC}"
    echo -e "  Versión disponible: ${BOLD}$remote_ver${NC}"
    [ -n "$latest_tag" ] && echo -e "  Última tag:         ${BOLD}$latest_tag${NC}"
 
    # Comparar versiones
    if [ "$local_ver" = "$remote_ver" ]; then
        log_ok "$name está actualizado"
 
        local pending
        pending=$(get_pending_commits "$src_dir")
        if [ -n "$pending" ]; then
            log_warn "Hay commits sin release:"
            echo "$pending" | head -5 | sed 's/^/    /'
            local count
            count=$(echo "$pending" | wc -l)
            [ "$count" -gt 5 ] && echo "    ... y $((count - 5)) más"
        fi
        return 0
    fi
 
    # Hay actualización disponible
    echo ""
    log_warn "Actualización disponible: $local_ver → $remote_ver"
 
    local pending
    pending=$(get_pending_commits "$src_dir")
    if [ -n "$pending" ]; then
        echo -e "\n${CYAN}Cambios incluidos:${NC}"
        echo "$pending" | sed 's/^/    /'
    fi
 
    # Comprobar cambios en config
    local changed_config
    if changed_config=$(check_config_changes "$src_dir"); then
        echo ""
        log_warn "La plantilla de configuración ha cambiado"
        show_config_diff "$src_dir" "$changed_config"
        log_warn "Revisa tu $config_path después de actualizar"
    fi
 
    if $CHECK_ONLY; then
        return 0
    fi
 
    echo ""
    if ! confirm "¿Actualizar $name a $remote_ver?"; then
        log_info "Actualización cancelada"
        return 0
    fi
 
    # Backup
    backup_component "$name" "$bin_path" "$config_path"
 
    # Compilar
    if ! build_rust_component "$src_dir" "$name"; then
        log_error "Compilación fallida. Nada ha cambiado."
        return 1
    fi
 
    # Parar servicio si existe
    if [ -n "$service" ] && sudo systemctl is-active "$service" &>/dev/null; then
        log_info "Deteniendo $service..."
        sudo systemctl stop "$service"
    fi
 
    # Instalar
    install_binary "$src_dir" "$bin_name" "$bin_path"
 
    # Verificar versión
    local new_ver
    new_ver=$("$bin_path" --version 2>/dev/null || get_local_version "$src_dir")
    log_ok "Nueva versión: $new_ver"
 
    # Reiniciar servicio si existe
    if [ -n "$service" ]; then
        restart_service "$service"
    fi
 
    # Recordar cambios de config si los hubo
    if [ -n "${changed_config:-}" ]; then
        echo ""
        log_warn "RECUERDA: La config de ejemplo cambió."
        log_warn "Compara con: diff $config_path $src_dir/$changed_config"
    fi
}
 
# --- Main ---
 
echo -e "${BOLD}${CYAN}"
echo "🧌 Mostro Update Manager"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${NC}"
 
if $CHECK_ONLY; then
    log_info "Modo solo comprobación (--check)"
fi
 
# Ejecutar según el target
if [ -z "$TARGET" ] || [ "$TARGET" = "mostrod" ]; then
    update_component "mostrod" "$MOSTROD_SRC" "$MOSTROD_BIN" "mostrod" "$MOSTROD_CONFIG" "$MOSTROD_SERVICE"
fi
 
if [ -z "$TARGET" ] || [ "$TARGET" = "mostrix" ]; then
    update_component "mostrix" "$MOSTRIX_SRC" "$MOSTRIX_BIN" "mostrix" "$MOSTRIX_CONFIG" ""
fi
 
if [ -z "$TARGET" ] || [ "$TARGET" = "watchdog" ]; then
    update_component "mostro-watchdog" "$WATCHDOG_SRC" "$WATCHDOG_BIN" "mostro-watchdog" "$WATCHDOG_CONFIG" "$WATCHDOG_SERVICE"
fi
 
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if $CHECK_ONLY; then
    log_ok "Comprobación completada"
else
    log_ok "Proceso completado"
    if [ -d "$BACKUP_DIR" ]; then
        log_info "Backups guardados en: $BACKUP_DIR"
    fi
fi
