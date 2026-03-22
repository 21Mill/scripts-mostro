#!/bin/bash
# ============================================================================
# mostro_log_search.sh — Busca y formatea logs de Mostro por order ID
# Uso: ./mostro_log_search.sh <order_id_parcial_o_completo>
# ============================================================================

source "$(dirname "${BASH_SOURCE[0]}")/mostro-env.sh"

ORDER_ID="${1}"

if [ -z "$ORDER_ID" ]; then
    echo "Uso: $0 <order_id>"
    echo "Ejemplo: $0 a179dca3"
    exit 1
fi

# Determinar fuente de logs
if [ -n "$MOSTRO_LOG" ] && [ -f "$MOSTRO_LOG" ]; then
    LOG_SOURCE="$MOSTRO_LOG"
    RESULTS=$(grep "$ORDER_ID" "$MOSTRO_LOG" 2>/dev/null || true)
else
    LOG_SOURCE="journalctl -u $MOSTROD_SERVICE"
    RESULTS=$(sudo journalctl -u "$MOSTROD_SERVICE" --no-pager 2>/dev/null | grep "$ORDER_ID" || true)
fi

if [ -z "$RESULTS" ]; then
    TOTAL=0
else
    TOTAL=$(echo "$RESULTS" | wc -l)
fi

if [ "$TOTAL" -eq 0 ]; then
    echo "❌ No se encontraron entradas para: $ORDER_ID"
    echo ""
    echo "Buscando IDs similares..."
    SHORT=$(echo "$ORDER_ID" | cut -d'-' -f1)
    if [ -n "$MOSTRO_LOG" ] && [ -f "$MOSTRO_LOG" ]; then
        grep -oP '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' "$MOSTRO_LOG" \
            | grep "$SHORT" | sort -u | head -5
    else
        sudo journalctl -u "$MOSTROD_SERVICE" --no-pager 2>/dev/null \
            | grep -oP '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' \
            | grep "$SHORT" | sort -u | head -5
    fi
    exit 1
fi

echo "🧌 Mostro Log Search"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Orden:    $ORDER_ID"
echo "📄 Fuente:   $LOG_SOURCE"
echo "🔍 Entradas: $TOTAL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

echo "$RESULTS" | sort | while IFS= read -r line; do
    if echo "$line" | grep -qiE "ERROR|FATAL"; then
        echo -e "\033[31m❌ $line\033[0m"
    elif echo "$line" | grep -qi "WARN"; then
        echo -e "\033[33m⚠️  $line\033[0m"
    elif echo "$line" | grep -qiE "DEBUG|TRACE"; then
        echo -e "\033[90m🔍 $line\033[0m"
    else
        echo "   $line"
    fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo "📊 Resumen:"
for level in ERROR WARN INFO DEBUG; do
    count=$(echo "$RESULTS" | grep -ci "$level" || true)
    [ "$count" -gt 0 ] && echo "   $level: $count"
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
