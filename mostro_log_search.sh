#!/bin/bash
# mostro_log_search.sh — Busca y formatea logs de Mostro por order ID
# Uso: ./mostro_log_search.sh <order_id_parcial_o_completo>
# Ejemplo: ./mostro_log_search.sh a179dca3
#          ./mostro_log_search.sh a179dca3-ce49-4d59-a47b-5627439b41a5

LOG_FILE="/var/log/mostro.log"
ORDER_ID="${1}"

if [ -z "$ORDER_ID" ]; then
    echo "Uso: $0 <order_id>"
    echo "Ejemplo: $0 a179dca3"
    exit 1
fi

if [ ! -f "$LOG_FILE" ]; then
    echo "❌ No se encuentra $LOG_FILE"
    exit 1
fi

# Contar coincidencias
TOTAL=$(grep -c "$ORDER_ID" "$LOG_FILE" 2>/dev/null)

if [ "$TOTAL" -eq 0 ]; then
    echo "❌ No se encontraron entradas para: $ORDER_ID"
    echo ""
    echo "Buscando IDs similares..."
    # Intentar con la primera parte del UUID
    SHORT=$(echo "$ORDER_ID" | cut -d'-' -f1)
    grep -oP '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}' "$LOG_FILE" \
        | grep "$SHORT" | sort -u | head -5
    exit 1
fi

echo "🧌 Mostro Log Search"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Orden:    $ORDER_ID"
echo "📄 Archivo:  $LOG_FILE"
echo "🔍 Entradas: $TOTAL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Extraer y mostrar líneas relevantes, ordenadas cronológicamente
# Colorear niveles: ERROR en rojo, WARN en amarillo, INFO en verde
grep "$ORDER_ID" "$LOG_FILE" | sort | while IFS= read -r line; do
    # Intentar extraer timestamp y nivel
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

# Resumen de niveles
echo "📊 Resumen:"
for level in ERROR WARN INFO DEBUG; do
    count=$(grep "$ORDER_ID" "$LOG_FILE" | grep -ci "$level")
    [ "$count" -gt 0 ] && echo "   $level: $count"
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
