#!/bin/bash
# ============================================================================
# monitor.sh — Monitoriza una transacción Bitcoin y notifica por Telegram
# Uso: ./monitor.sh <txid>
# ============================================================================

source "$(dirname "${BASH_SOURCE[0]}")/../admin/env.sh"

TELEGRAM_BOT_TOKEN="${TELEGRAM_MONITOR_TOKEN}"
CHAT_ID="${TELEGRAM_MONITOR_CHAT_ID}"
CHECK_INTERVAL=${CHECK_INTERVAL:-60}

if [ -z "$1" ]; then
    echo "Uso: $0 <txid>"
    exit 1
fi

if ! command -v curl &> /dev/null || ! command -v jq &> /dev/null; then
    echo "Error: Se requiere 'curl' y 'jq'. Instálalos primero."
    exit 1
fi

TXID=$1

# Validación de formato: txid debe ser exactamente 64 caracteres hexadecimales
if ! [[ "$TXID" =~ ^[0-9a-fA-F]{64}$ ]]; then
    echo "Error: '$TXID' no es una txid válida (debe ser 64 caracteres hexadecimales)."
    exit 1
fi

# Validación de existencia: la tx debe ser visible en mempool o confirmada
check_tx_exists() {
    local RESPONSE
    RESPONSE=$(curl -s --max-time 10 "https://mempool.space/api/tx/$TXID")
    if echo "$RESPONSE" | jq -e '.txid' &>/dev/null; then
        return 0
    fi
    RESPONSE=$(curl -s --max-time 10 "https://blockstream.info/api/tx/$TXID")
    if echo "$RESPONSE" | jq -e '.txid' &>/dev/null; then
        return 0
    fi
    return 1
}

if ! check_tx_exists; then
    echo "Error: La transacción '$TXID' no se encuentra en la mempool ni confirmada."
    echo "Comprueba que la txid es correcta y que la transacción ha sido broadcast."
    exit 1
fi

# Auto-lanzarse en background si no lo está ya
if [ -z "$MONITOR_BG" ]; then
    LOG="$HOME/monitor-${TXID:0:8}.log"
    export MONITOR_BG=1
    nohup "$0" "$TXID" >> "$LOG" 2>&1 &
    echo "Lanzado en background (PID: $!)"
    echo "Log: $LOG"
    exit 0
fi

API_URL="https://mempool.space/api/tx/$TXID"
FALLBACK_URL="https://blockstream.info/api/tx/$TXID"

send_telegram() {
    local MESSAGE="$1"
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${CHAT_ID}" \
            -d "text=${MESSAGE}" \
            -d "parse_mode=HTML" > /dev/null
    fi
}

check_tx() {
    local RESPONSE
    RESPONSE=$(curl -s --max-time 10 "$API_URL")

    if [ -z "$RESPONSE" ] || echo "$RESPONSE" | grep -q "error"; then
        RESPONSE=$(curl -s --max-time 10 "$FALLBACK_URL")
    fi

    local CONFIRMED
    CONFIRMED=$(echo "$RESPONSE" | jq -r '.status.confirmed' 2>/dev/null)

    if [ "$CONFIRMED" == "true" ]; then
        BLOCK_HEIGHT=$(echo "$RESPONSE" | jq -r '.status.block_height' 2>/dev/null)
        BLOCK_HASH=$(echo "$RESPONSE" | jq -r '.status.block_hash' 2>/dev/null)
        echo "$BLOCK_HEIGHT $BLOCK_HASH"
        return 0
    fi

    return 1
}

echo "🔎 Monitorizando: $TXID"
echo "🌐 Fuente: mempool.space (fallback: blockstream.info)"
echo "⏱️  Intervalo de comprobación: ${CHECK_INTERVAL}s"
echo ""

while true; do
    RESULT=$(check_tx)
    if [ $? -eq 0 ]; then
        BLOCK_HEIGHT=$(echo "$RESULT" | awk '{print $1}')
        BLOCK_HASH=$(echo "$RESULT" | awk '{print $2}')

        MSG="✅ <b>Transacción confirmada!</b>%0A%0A📦 Bloque: <code>${BLOCK_HEIGHT}</code>%0A🔗 TX: <code>${TXID}</code>%0A🔍 <a href=\"https://mempool.space/tx/${TXID}\">Ver en mempool.space</a>"

        echo "✅ ¡Confirmada en bloque $BLOCK_HEIGHT!"
        send_telegram "$MSG"
        rm -f "$HOME/monitor-${TXID:0:8}.log"
        exit 0
    fi

    echo "⏳ $(date '+%H:%M:%S') - Sin confirmar. Próxima comprobación en ${CHECK_INTERVAL}s..."
    sleep "$CHECK_INTERVAL"
done
