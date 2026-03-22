#!/bin/bash
# monitor_tx.sh - Monitoriza una transacción Bitcoin y notifica por Telegram
#
# Uso: ./monitor_tx.sh <txid>
#

# --- Cargar variables de entorno ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

TELEGRAM_BOT_TOKEN="${TELEGRAM_TOKEN}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID}"
CHECK_INTERVAL=${CHECK_INTERVAL:-60}

# --- Validaciones ---
if [ -z "$1" ]; then
  echo "Uso: $0 <txid>"
  exit 1
fi

if ! command -v curl &> /dev/null || ! command -v jq &> /dev/null; then
  echo "Error: Se requiere 'curl' y 'jq'. Instálalos primero."
  exit 1
fi

TXID=$1
API_URL="https://mempool.space/api/tx/$TXID"
FALLBACK_URL="https://blockstream.info/api/tx/$TXID"

# --- Función: enviar notificación Telegram ---
send_telegram() {
  local MESSAGE="$1"
  if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d "chat_id=${TELEGRAM_CHAT_ID}" \
      -d "text=${MESSAGE}" \
      -d "parse_mode=HTML" > /dev/null
  fi
}

# --- Función: comprobar estado ---
check_tx() {
  local RESPONSE
  RESPONSE=$(curl -s --max-time 10 "$API_URL")

  # Fallback a blockstream si mempool falla
  if [ -z "$RESPONSE" ] || echo "$RESPONSE" | grep -q "error"; then
    RESPONSE=$(curl -s --max-time 10 "$FALLBACK_URL")
  fi

  local CONFIRMED
  CONFIRMED=$(echo "$RESPONSE" | jq -r '\.status.confirmed' 2>/dev/null)

  if [ "$CONFIRMED" == "true" ]; then
    BLOCK_HEIGHT=$(echo "$RESPONSE" | jq -r '.status.block_height' 2>/dev/null)
    BLOCK_HASH=$(echo "$RESPONSE" | jq -r '.status.block_hash' 2>/dev/null)
    echo "$BLOCK_HEIGHT $BLOCK_HASH"
    return 0
  fi

  return 1
}

# --- Main ---
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
    exit 0
  fi

  echo "⏳ $(date '+%H:%M:%S') - Sin confirmar. Próxima comprobación en ${CHECK_INTERVAL}s..."
  sleep "$CHECK_INTERVAL"
done
