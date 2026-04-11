#!/bin/bash
# ============================================================================
# check_channels.sh — Alerta por Telegram si hay más de 2 canales LND caídos
#
# Uso:
#   ./check_channels.sh              # Comprueba y alerta si > 2 canales caídos
#   ./check_channels.sh --status     # Muestra estado sin enviar alerta
#
# Cron recomendado (cada 10 minutos):
#   */10 * * * * /home/admin/mostro-sources/scripts/admin/check_channels.sh
# ============================================================================

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

THRESHOLD=2   # Alertar si los canales inactivos superan este número

# --- Leer configuración de Telegram desde el config del watchdog ---

WATCHDOG_CONFIG_FILE="${WATCHDOG_CONFIG:-/opt/mostro/config.toml}"

read_toml_value() {
    local key="$1"
    grep -oP "(?<=^${key} = ).*" "$WATCHDOG_CONFIG_FILE" 2>/dev/null \
        | tr -d '"' | head -1
}

BOT_TOKEN=$(read_toml_value "bot_token")
CHAT_ID=$(read_toml_value "chat_id")

if [ -z "$BOT_TOKEN" ] || [ -z "$CHAT_ID" ]; then
    echo "Error: no se pudo leer bot_token o chat_id de $WATCHDOG_CONFIG_FILE" >&2
    exit 1
fi

# --- Obtener estado de canales ---

channels_json=$(lncli listchannels 2>/dev/null)
if [ -z "$channels_json" ]; then
    echo "Error: no se pudo obtener la lista de canales de lncli" >&2
    exit 1
fi

total=$(echo "$channels_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('channels',[])))")
inactive=$(echo "$channels_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(1 for c in d.get('channels',[]) if not c['active']))")
active=$((total - inactive))

# --- Modo --status: solo mostrar, no alertar ---

if [[ "${1:-}" == "--status" ]]; then
    echo "Canales LND:"
    echo "  Total:    $total"
    echo "  Activos:  $active"
    echo "  Caídos:   $inactive"
    echo "  Umbral:   > $THRESHOLD"
    exit 0
fi

# --- Enviar alerta si se supera el umbral ---

if [ "$inactive" -gt "$THRESHOLD" ]; then
    # Construir lista de canales caídos (alias + pubkey abreviada)
    inactive_list=$(echo "$channels_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for c in d.get('channels', []):
    if not c['active']:
        alias = c.get('peer_alias', '')
        pubkey = c['remote_pubkey'][:12] + '...'
        capacity = int(c['capacity'])
        print(f'  • {alias or pubkey} — {capacity:,} sats')
")

    hostname=$(hostname)
    message="⚠️ *Alerta LND — ${hostname}*

🔴 *${inactive} canales caídos* (umbral: ${THRESHOLD})

Resumen:
• Total: ${total}
• Activos: ${active}
• Caídos: ${inactive}

Canales inactivos:
${inactive_list}"

    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${CHAT_ID}" \
        --data-urlencode "text=${message}" \
        --data-urlencode "parse_mode=Markdown" \
        -o /dev/null

    echo "Alerta enviada: $inactive canales caídos"
else
    echo "OK: $inactive canales caídos (umbral: > $THRESHOLD)"
fi
