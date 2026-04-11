#!/bin/bash
# ============================================================================
# update.sh — Actualización segura de componentes Mostro
# Descarga binarios precompilados desde GitHub Releases y verifica
# la integridad con GPG (mostrod/mostrix) o SHA256 (watchdog).
#
# Uso:
#   ./update.sh              # Comprobar todos los componentes
#   ./update.sh mostrod      # Solo mostrod
#   ./update.sh mostrix      # Solo mostrix
#   ./update.sh watchdog     # Solo mostro-watchdog
#   ./update.sh --check      # Solo comprobar, no actualizar
# ============================================================================

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

BACKUP_TS="$BACKUP_DIR/$(date +%Y%m%d_%H%M%S)"
CHECK_ONLY=false
TARGET=""
TMPDIR_BASE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_BASE"' EXIT

for arg in "$@"; do
    case "$arg" in
        --check) CHECK_ONLY=true ;;
        mostrod)  TARGET="mostrod" ;;
        mostrix)  TARGET="mostrix" ;;
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

# --- Funciones de log ---

log_info()  { echo -e "${BLUE}ℹ ${NC}$1" >&2; }
log_ok()    { echo -e "${GREEN}✅ ${NC}$1" >&2; }
log_warn()  { echo -e "${YELLOW}⚠️  ${NC}$1" >&2; }
log_error() { echo -e "${RED}❌ ${NC}$1" >&2; }
log_header(){ echo -e "\n${BOLD}${CYAN}━━━ $1 ━━━${NC}\n"; }

# --- Arquitectura ---

get_arch() {
    case "$(uname -m)" in
        x86_64)  echo "x86_64" ;;
        aarch64) echo "aarch64" ;;
        armv7l)  echo "armv7" ;;
        *) echo "$(uname -m)" ;;
    esac
}

get_musl_triple() {
    case "$(uname -m)" in
        x86_64)  echo "x86_64-unknown-linux-musl" ;;
        aarch64) echo "aarch64-unknown-linux-musl" ;;
        armv7l)  echo "armv7-unknown-linux-gnueabi" ;;
        *) log_error "Arquitectura no soportada: $(uname -m)"; return 1 ;;
    esac
}

# --- Versiones ---

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

get_github_latest_tag() {
    local repo="$1"
    curl -s "https://api.github.com/repos/$repo/releases/latest" \
        | grep -oP '"tag_name": *"\K[^"]+' | head -1
}

# --- Configuración ---

check_config_changes() {
    local src_dir="$1"
    local config_example=""

    for f in "settings.tpl.toml" "settings.toml.example" "config.example.toml" "config.toml.example"; do
        if run_in_dir "$src_dir" "git show origin/main:$f" &>/dev/null; then
            config_example="$f"
            break
        fi
    done

    [ -z "$config_example" ] && return 1

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

# --- Backup ---

backup_component() {
    local name="$1"
    local bin="$2"
    local config="$3"

    mkdir -p "$BACKUP_TS/$name"

    if sudo test -f "$bin"; then
        sudo cp "$bin" "$BACKUP_TS/$name/" 2>/dev/null && \
            log_info "Binario respaldado: $BACKUP_TS/$name/$(basename "$bin")"
    fi

    if sudo test -f "$config"; then
        sudo cp "$config" "$BACKUP_TS/$name/" 2>/dev/null && \
            log_info "Config respaldada: $BACKUP_TS/$name/$(basename "$config")"
    fi

    # Backup de la BD (solo para mostrod, usando sqlite3 online backup API)
    if [ "$name" = "mostrod" ]; then
        local db_path="${MOSTRO_DB:-/opt/mostro/mostro.db}"
        local real_db
        real_db=$(readlink -f "$db_path" 2>/dev/null || echo "$db_path")
        if sudo -u mostro test -f "$real_db" 2>/dev/null; then
            sudo -u mostro sqlite3 "$real_db" ".backup '$BACKUP_TS/$name/mostro.db'" && \
                log_info "BD respaldada: $BACKUP_TS/$name/mostro.db"
        fi
    fi
}

# --- Detección de migraciones ---

check_migrations() {
    local src_dir="$1"
    local current_migrations new_migrations new_files

    current_migrations=$(run_in_dir "$src_dir" "git show HEAD:migrations/ 2>/dev/null | grep -E '^[0-9]{14}_' | sort" 2>/dev/null || true)
    new_migrations=$(run_in_dir "$src_dir" "git show origin/main:migrations/ 2>/dev/null | grep -E '^[0-9]{14}_' | sort" 2>/dev/null || true)

    new_files=$(comm -13 <(echo "$current_migrations") <(echo "$new_migrations") 2>/dev/null || true)

    if [ -n "$new_files" ]; then
        echo "$new_files"
        return 0
    fi
    return 1
}

# --- Verificación GPG + SHA256 (mostrod / mostrix) ---

import_gpg_keys() {
    local src_dir="$1"
    local keys_dir="$src_dir/keys"

    if [ -f "$keys_dir/negrunch.asc" ] && [ -f "$keys_dir/arkanoider.asc" ]; then
        gpg --import "$keys_dir/negrunch.asc" 2>/dev/null || true
        gpg --import "$keys_dir/arkanoider.asc" 2>/dev/null || true
    else
        log_warn "Claves GPG no encontradas en $keys_dir, importando desde GitHub..."
        curl -sL "https://raw.githubusercontent.com/MostroP2P/mostro/main/keys/negrunch.asc" | gpg --import 2>/dev/null || true
        curl -sL "https://raw.githubusercontent.com/MostroP2P/mostro/main/keys/arkanoider.asc" | gpg --import 2>/dev/null || true
    fi
}

download_and_verify_gpg() {
    local repo="$1"
    local version="$2"
    local bin_asset="$3"
    local src_dir="$4"
    local tmpdir="$5"

    local base_url="https://github.com/$repo/releases/download/v$version"

    log_info "Importando claves GPG de los firmantes..."
    import_gpg_keys "$src_dir"

    log_info "Descargando binario: $bin_asset"
    curl -sL "$base_url/$bin_asset" -o "$tmpdir/$bin_asset"

    log_info "Descargando manifest y firmas..."
    curl -sL "$base_url/manifest.txt"                -o "$tmpdir/manifest.txt"
    curl -sL "$base_url/manifest.txt.sig.negrunch"   -o "$tmpdir/manifest.txt.sig.negrunch"
    curl -sL "$base_url/manifest.txt.sig.arkanoider" -o "$tmpdir/manifest.txt.sig.arkanoider"

    log_info "Verificando firma GPG (negrunch)..."
    if ! gpg --verify "$tmpdir/manifest.txt.sig.negrunch" "$tmpdir/manifest.txt" 2>/dev/null; then
        log_error "Firma GPG de negrunch NO válida. Abortando."
        return 1
    fi
    log_ok "Firma negrunch válida"

    log_info "Verificando firma GPG (arkanoider)..."
    if ! gpg --verify "$tmpdir/manifest.txt.sig.arkanoider" "$tmpdir/manifest.txt" 2>/dev/null; then
        log_error "Firma GPG de arkanoider NO válida. Abortando."
        return 1
    fi
    log_ok "Firma arkanoider válida"

    log_info "Verificando integridad SHA256..."
    local expected_hash
    expected_hash=$(grep "$bin_asset" "$tmpdir/manifest.txt" | awk '{print $1}')
    if [ -z "$expected_hash" ]; then
        log_error "Hash no encontrado en manifest.txt para $bin_asset"
        return 1
    fi
    local actual_hash
    actual_hash=$(sha256sum "$tmpdir/$bin_asset" | awk '{print $1}')
    if [ "$expected_hash" != "$actual_hash" ]; then
        log_error "SHA256 no coincide. Esperado: $expected_hash | Obtenido: $actual_hash"
        return 1
    fi
    log_ok "Integridad SHA256 verificada"

    chmod +x "$tmpdir/$bin_asset"
    echo "$tmpdir/$bin_asset"
}

# --- Verificación SHA256 (mostro-watchdog) ---

download_and_verify_sha256() {
    local repo="$1"
    local version="$2"
    local bin_asset="$3"
    local tmpdir="$4"

    local base_url="https://github.com/$repo/releases/download/v$version"

    log_info "Descargando binario: $bin_asset"
    curl -sL "$base_url/$bin_asset" -o "$tmpdir/$bin_asset"

    log_info "Descargando SHA256..."
    curl -sL "$base_url/$bin_asset.sha256" -o "$tmpdir/$bin_asset.sha256"

    log_info "Verificando integridad SHA256..."
    local expected_hash
    expected_hash=$(awk '{print $1}' "$tmpdir/$bin_asset.sha256")
    local actual_hash
    actual_hash=$(sha256sum "$tmpdir/$bin_asset" | awk '{print $1}')
    if [ "$expected_hash" != "$actual_hash" ]; then
        log_error "SHA256 no coincide. Esperado: $expected_hash | Obtenido: $actual_hash"
        return 1
    fi
    log_ok "Integridad SHA256 verificada"

    chmod +x "$tmpdir/$bin_asset"
    echo "$tmpdir/$bin_asset"
}

# --- Instalación y reinicio ---

install_binary() {
    local src_bin="$1"
    local dest="$2"

    if [ ! -f "$src_bin" ]; then
        log_error "Binario no encontrado: $src_bin"
        return 1
    fi

    sudo install "$src_bin" "$dest"
    log_ok "Binario instalado: $dest"
}

restart_service() {
    local service="$1"

    if ! sudo systemctl is-enabled "$service" &>/dev/null; then
        log_warn "$service no está habilitado"
        return 0
    fi

    log_info "Reiniciando $service..."
    sudo systemctl restart "$service"

    # Esperar hasta 30s a que aparezcan los indicadores de arranque correcto
    local timeout=30 elapsed=0 ok=false
    while [ $elapsed -lt $timeout ]; do
        sleep 2
        elapsed=$((elapsed + 2))
        if sudo journalctl -u "$service" --since "1 minute ago" --no-pager -q 2>/dev/null \
            | grep -qE "Connected to (LND|relay)|Settings correctly loaded"; then
            ok=true
            break
        fi
        # Si el servicio ha muerto, no seguir esperando
        if ! sudo systemctl is-active "$service" &>/dev/null; then
            break
        fi
    done

    if $ok; then
        log_ok "$service arrancado y conectado correctamente"
    elif sudo systemctl is-active "$service" &>/dev/null; then
        log_warn "$service activo pero sin confirmar conexión en ${timeout}s — revisa los logs"
    else
        log_error "$service falló al arrancar"
        sudo journalctl -u "$service" --no-pager -n 15
        return 1
    fi
}

confirm() {
    local prompt="$1"
    echo -en "${BOLD}$prompt [s/N]: ${NC}"
    read -r response
    [[ "$response" =~ ^[sS]$ ]]
}

# --- Actualización de un componente ---

update_component() {
    local name="$1"
    local src_dir="$2"
    local bin_path="$3"
    local config_path="$4"
    local service="$5"
    local github_repo="$6"
    local verify_mode="$7"   # "gpg" o "sha256"

    log_header "$name"

    if ! sudo test -d "$src_dir/.git"; then
        log_error "Directorio de fuentes no encontrado: $src_dir"
        return 1
    fi

    local local_ver remote_ver latest_tag
    local_ver=$(get_local_version "$src_dir")
    remote_ver=$(get_remote_version "$src_dir")
    latest_tag=$(get_latest_tag "$src_dir")

    echo -e "  Versión instalada:  ${BOLD}$local_ver${NC}"
    echo -e "  Versión disponible: ${BOLD}$remote_ver${NC}"
    [ -n "$latest_tag" ] && echo -e "  Última tag:         ${BOLD}$latest_tag${NC}"

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

    echo ""
    log_warn "Actualización disponible: $local_ver → $remote_ver"

    local pending
    pending=$(get_pending_commits "$src_dir")
    if [ -n "$pending" ]; then
        echo -e "\n${CYAN}Cambios incluidos:${NC}"
        echo "$pending" | sed 's/^/    /'
    fi

    local changed_config=""
    if changed_config=$(check_config_changes "$src_dir"); then
        echo ""
        log_warn "La plantilla de configuración ha cambiado"
        show_config_diff "$src_dir" "$changed_config"
        log_warn "Revisa tu $config_path después de actualizar"
    fi

    $CHECK_ONLY && return 0

    echo ""
    if ! confirm "¿Actualizar $name a $remote_ver?"; then
        log_info "Actualización cancelada"
        return 0
    fi

    backup_component "$name" "$bin_path" "$config_path"

    # Detectar migraciones pendientes
    local pending_migrations=""
    if pending_migrations=$(check_migrations "$src_dir" 2>/dev/null); then
        echo ""
        log_warn "Esta actualización incluye migraciones de base de datos:"
        echo "$pending_migrations" | sed 's/^/    /'
        log_warn "Se aplicarán automáticamente al arrancar. Backup de BD ya realizado."
        echo ""
    fi

    # Determinar nombre del asset según arquitectura
    local arch triple bin_asset tmpdir
    arch=$(get_arch)
    tmpdir="$TMPDIR_BASE/$name"
    mkdir -p "$tmpdir"

    local verified_bin=""
    if [ "$verify_mode" = "gpg" ]; then
        triple=$(get_musl_triple)
        bin_asset="${name}-${triple}"
        verified_bin=$(download_and_verify_gpg "$github_repo" "$remote_ver" "$bin_asset" "$src_dir" "$tmpdir")
    else
        bin_asset="${name}-linux-${arch}"
        verified_bin=$(download_and_verify_sha256 "$github_repo" "$remote_ver" "$bin_asset" "$tmpdir")
    fi

    if [ -z "$verified_bin" ]; then
        log_error "Verificación fallida. Nada ha cambiado."
        return 1
    fi

    if [ -n "$service" ] && sudo systemctl is-active "$service" &>/dev/null; then
        log_info "Deteniendo $service..."
        sudo systemctl stop "$service"
    fi

    install_binary "$verified_bin" "$bin_path"

    # Actualizar repo fuente (para futuros git diff de config)
    run_in_dir "$src_dir" "git pull --quiet" || true

    local new_ver
    new_ver=$("$bin_path" --version 2>/dev/null || get_local_version "$src_dir")
    log_ok "Nueva versión: $new_ver"

    if [ -n "$service" ]; then
        if ! restart_service "$service"; then
            log_warn "Intentando rollback del binario..."
            local backup_bin="$BACKUP_TS/$name/$(basename "$bin_path")"
            if [ -f "$backup_bin" ]; then
                sudo install "$backup_bin" "$bin_path"
                log_info "Binario restaurado desde backup"
                restart_service "$service" \
                    && log_ok "Servicio restaurado a versión anterior" \
                    || log_error "El rollback también falló — intervención manual necesaria"
            else
                log_error "No hay backup disponible para rollback"
            fi
            return 1
        fi
    fi

    if [ -n "$changed_config" ]; then
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

$CHECK_ONLY && log_info "Modo solo comprobación (--check)"

if [ -z "$TARGET" ] || [ "$TARGET" = "mostrod" ]; then
    update_component "mostrod" "$MOSTROD_SRC" "$MOSTROD_BIN" "$MOSTROD_CONFIG" \
        "$MOSTROD_SERVICE" "MostroP2P/mostro" "gpg"
fi

if [ -z "$TARGET" ] || [ "$TARGET" = "mostrix" ]; then
    update_component "mostrix" "$MOSTRIX_SRC" "$MOSTRIX_BIN" "$MOSTRIX_CONFIG" \
        "" "MostroP2P/mostrix" "gpg"
fi

if [ -z "$TARGET" ] || [ "$TARGET" = "watchdog" ]; then
    update_component "mostro-watchdog" "$WATCHDOG_SRC" "$WATCHDOG_BIN" "$WATCHDOG_CONFIG" \
        "$WATCHDOG_SERVICE" "MostroP2P/mostro-watchdog" "sha256"
fi

echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if $CHECK_ONLY; then
    log_ok "Comprobación completada"
else
    log_ok "Proceso completado"
    [ -d "$BACKUP_TS" ] && log_info "Backups guardados en: $BACKUP_TS"
fi
