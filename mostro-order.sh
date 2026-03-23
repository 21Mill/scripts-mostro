#!/usr/bin/env bash
# mostro-order.sh — Consulta todos los datos de una orden en la base de datos de Mostro.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/mostro-env.sh"

# --- Uso ---
if [[ -z "$1" ]]; then
    echo -e "${CYAN}Uso:${NC} $(basename "$0") <order_id>"
    echo -e "  Busca una orden por su UUID completo o parcial."
    echo ""
    echo -e "${CYAN}Ejemplos:${NC}"
    echo "  $(basename "$0") 7361b8fe-9999-44e8-bb66-63074ed6a941"
    echo "  $(basename "$0") 7361b8fe"
    echo "  $(basename "$0") --recent        (últimas 10 órdenes)"
    echo "  $(basename "$0") --pending       (órdenes pendientes)"
    exit 1
fi

DB_PATH="${MOSTRO_DB:-/opt/mostro/mostro.db}"
DISPUTES_DB="${MOSTRO_DISPUTES_DB:-/opt/mostro/disputes.db}"
DB_USER="${MOSTRO_USER:-mostro}"

run_sql() {
    local db="$1"
    shift
    # Intentar directamente, si falla usar sudo -u $DB_USER
    if sqlite3 "$db" "$@" 2>/dev/null; then
        return 0
    else
        sudo -u "$DB_USER" sqlite3 "$db" "$@" 2>/dev/null
    fi
}

# Verificar acceso a la DB
if ! run_sql "$DB_PATH" "SELECT 1" >/dev/null 2>&1; then
    echo -e "${RED}Error:${NC} No se pudo acceder a la base de datos en $DB_PATH"
    echo -e "  Comprueba que el archivo existe y tienes permisos (o sudo) para acceder."
    exit 1
fi

# --- Funciones de formato ---
format_timestamp() {
    local ts="$1"
    if [[ -n "$ts" && "$ts" != "0" ]]; then
        date -d "@$ts" "+%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "$ts"
    else
        echo "—"
    fi
}

format_sats() {
    local sats="$1"
    if [[ -n "$sats" && "$sats" != "0" ]]; then
        printf "%'d sats" "$sats" 2>/dev/null | sed 's/,/./g'
    else
        echo "—"
    fi
}

format_pubkey() {
    local pk="$1"
    if [[ -n "$pk" && "$pk" != "" ]]; then
        echo "${pk:0:16}...${pk: -8}"
    else
        echo "—"
    fi
}

format_status() {
    local status="$1"
    case "$status" in
        pending)                  echo -e "${YELLOW}⏳ pending${NC}" ;;
        active)                   echo -e "${GREEN}✅ active${NC}" ;;
        success)                  echo -e "${GREEN}✅ success${NC}" ;;
        canceled|"canceled-by-admin") echo -e "${RED}❌ $status${NC}" ;;
        dispute)                  echo -e "${RED}⚠️  dispute${NC}" ;;
        expired)                  echo -e "${DIM}⏰ expired${NC}" ;;
        waiting-buyer-invoice)    echo -e "${CYAN}🔄 waiting-buyer-invoice${NC}" ;;
        waiting-payment)          echo -e "${CYAN}🔄 waiting-payment${NC}" ;;
        settled-hold-invoice)     echo -e "${CYAN}🔄 settled-hold-invoice${NC}" ;;
        fiat-sent)                echo -e "${CYAN}💸 fiat-sent${NC}" ;;
        *)                        echo -e "$status" ;;
    esac
}

# --- Modos especiales ---
if [[ "$1" == "--recent" ]]; then
    echo -e "${BOLD}${CYAN}═══ Últimas 10 órdenes ═══${NC}"
    echo ""
    results=$(run_sql "$DB_PATH" -separator '|' "
        SELECT
            LOWER(
                SUBSTR(HEX(id),1,8) || '-' ||
                SUBSTR(HEX(id),9,4) || '-' ||
                SUBSTR(HEX(id),13,4) || '-' ||
                SUBSTR(HEX(id),17,4) || '-' ||
                SUBSTR(HEX(id),21,12)
            ) as uuid,
            kind, status, fiat_code, fiat_amount, amount, created_at
        FROM orders ORDER BY created_at DESC LIMIT 10
    ")
    printf "  ${DIM}%-38s %-6s %-24s %5s %-5s %10s   %-19s${NC}\n" "ORDER ID" "TYPE" "STATUS" "FIAT" "CODE" "SATS" "CREATED"
    echo -e "  ${DIM}$(printf '─%.0s' {1..120})${NC}"
    while IFS='|' read -r uuid kind status fiat_code fiat_amount amount created_at; do
        ts=$(format_timestamp "$created_at")
        sats="—"
        [[ "$amount" != "0" ]] && sats=$(printf "%'d" "$amount" 2>/dev/null | sed 's/,/./g')
        fiat_display="$fiat_amount"
        [[ "$fiat_amount" == "0" ]] && fiat_display="rango"
        case "$status" in
            pending)    status_color="${YELLOW}$status${NC}" ;;
            success)    status_color="${GREEN}$status${NC}" ;;
            canceled*)  status_color="${RED}$status${NC}" ;;
            expired)    status_color="${DIM}$status${NC}" ;;
            *)          status_color="${CYAN}$status${NC}" ;;
        esac
        printf "  %-38s %-6s %-24b %5s %-5s %10s   %-19s\n" "$uuid" "$kind" "$status_color" "$fiat_display" "$fiat_code" "$sats" "$ts"
    done <<< "$results"
    echo ""
    exit 0
fi

if [[ "$1" == "--pending" ]]; then
    echo -e "${BOLD}${CYAN}═══ Órdenes pendientes ═══${NC}"
    echo ""
    results=$(run_sql "$DB_PATH" -separator '|' "
        SELECT
            LOWER(
                SUBSTR(HEX(id),1,8) || '-' ||
                SUBSTR(HEX(id),9,4) || '-' ||
                SUBSTR(HEX(id),13,4) || '-' ||
                SUBSTR(HEX(id),17,4) || '-' ||
                SUBSTR(HEX(id),21,12)
            ) as uuid,
            kind, fiat_code, fiat_amount, min_amount, max_amount, amount, premium, payment_method, created_at, expires_at
        FROM orders WHERE status='pending' ORDER BY created_at DESC
    ")
    if [[ -z "$results" ]]; then
        echo -e "  ${DIM}No hay órdenes pendientes.${NC}"
        exit 0
    fi
    while IFS='|' read -r uuid kind fiat_code fiat_amount min_amount max_amount amount premium payment_method created_at expires_at; do
        tipo_emoji="🟢 BUY"
        [[ "$kind" == "sell" ]] && tipo_emoji="🔴 SELL"
        echo -e "  ${BOLD}$tipo_emoji${NC}  $uuid"
        if [[ "$min_amount" != "0" && "$max_amount" != "0" ]]; then
            echo "       Fiat: $min_amount — $max_amount $fiat_code"
        else
            echo "       Fiat: $fiat_amount $fiat_code"
        fi
        [[ "$amount" != "0" ]] && echo "       Sats: $(format_sats "$amount")"
        echo "       Premium: ${premium}%  |  Método: $payment_method"
        echo "       Creada: $(format_timestamp "$created_at")  |  Expira: $(format_timestamp "$expires_at")"
        echo ""
    done <<< "$results"
    exit 0
fi

# --- Búsqueda de orden específica ---
ORDER_INPUT=$(echo "$1" | tr -d '-' | tr '[:lower:]' '[:upper:]')

# Buscar por UUID exacto o parcial
if [[ ${#ORDER_INPUT} -eq 32 ]]; then
    WHERE_CLAUSE="HEX(id) = '$ORDER_INPUT'"
else
    WHERE_CLAUSE="HEX(id) LIKE '${ORDER_INPUT}%'"
fi

result=$(run_sql "$DB_PATH" -separator '|' "
    SELECT
        LOWER(
            SUBSTR(HEX(id),1,8) || '-' ||
            SUBSTR(HEX(id),9,4) || '-' ||
            SUBSTR(HEX(id),13,4) || '-' ||
            SUBSTR(HEX(id),17,4) || '-' ||
            SUBSTR(HEX(id),21,12)
        ) as uuid,
        kind, status, event_id, hash, preimage,
        creator_pubkey, buyer_pubkey, master_buyer_pubkey,
        seller_pubkey, master_seller_pubkey,
        cancel_initiator_pubkey, dispute_initiator_pubkey,
        price_from_api, premium, payment_method,
        amount, min_amount, max_amount,
        fiat_code, fiat_amount, buyer_invoice,
        fee, routing_fee, dev_fee, dev_fee_paid, dev_fee_payment_hash,
        buyer_dispute, seller_dispute,
        buyer_cooperativecancel, seller_cooperativecancel,
        buyer_sent_rate, seller_sent_rate,
        payment_attempts, failed_payment,
        invoice_held_at, taken_at, created_at, expires_at,
        range_parent_id, trade_index_seller, trade_index_buyer,
        next_trade_pubkey, next_trade_index
    FROM orders WHERE $WHERE_CLAUSE
")

if [[ -z "$result" ]]; then
    echo -e "${RED}No se encontró ninguna orden que coincida con:${NC} $1"
    exit 1
fi

# Contar resultados
num_results=$(echo "$result" | wc -l)
if [[ "$num_results" -gt 1 ]]; then
    echo -e "${YELLOW}Se encontraron $num_results órdenes. Mostrando todas:${NC}"
    echo ""
fi

while IFS='|' read -r uuid kind status event_id hash preimage \
    creator_pubkey buyer_pubkey master_buyer_pubkey \
    seller_pubkey master_seller_pubkey \
    cancel_initiator_pubkey dispute_initiator_pubkey \
    price_from_api premium payment_method \
    amount min_amount max_amount \
    fiat_code fiat_amount buyer_invoice \
    fee routing_fee dev_fee dev_fee_paid dev_fee_payment_hash \
    buyer_dispute seller_dispute \
    buyer_cooperativecancel seller_cooperativecancel \
    buyer_sent_rate seller_sent_rate \
    payment_attempts failed_payment \
    invoice_held_at taken_at created_at expires_at \
    range_parent_id trade_index_seller trade_index_buyer \
    next_trade_pubkey next_trade_index; do

    # Tipo
    if [[ "$kind" == "buy" ]]; then
        tipo_display="${GREEN}${BOLD}🟢 COMPRA (buy)${NC}"
    else
        tipo_display="${RED}${BOLD}🔴 VENTA (sell)${NC}"
    fi

    echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${CYAN}║${NC}  📋 Orden: ${BOLD}$uuid${NC}"
    echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # --- Info general ---
    echo -e "${BOLD}  ─── General ───${NC}"
    echo -e "  Tipo:        $tipo_display"
    echo -e "  Estado:      $(format_status "$status")"
    echo -e "  Event ID:    ${DIM}$event_id${NC}"
    echo ""

    # --- Montos ---
    echo -e "${BOLD}  ─── Montos ───${NC}"
    if [[ "$min_amount" != "0" && "$max_amount" != "0" ]]; then
        echo -e "  Fiat:        $min_amount — $max_amount $fiat_code (rango)"
    else
        echo -e "  Fiat:        $fiat_amount $fiat_code"
    fi
    if [[ "$amount" != "0" ]]; then
        echo -e "  Sats:        $(format_sats "$amount")"
    else
        echo -e "  Sats:        ${DIM}A definir por mercado${NC}"
    fi
    echo -e "  Premium:     ${premium}%"
    [[ "$price_from_api" != "0" ]] && echo -e "  Precio API:  $price_from_api"
    echo -e "  Método:      $payment_method"
    echo ""

    # --- Comisiones ---
    if [[ "$fee" != "0" || "$routing_fee" != "0" || "$dev_fee" != "0" ]]; then
        echo -e "${BOLD}  ─── Comisiones ───${NC}"
        [[ "$fee" != "0" ]] && echo -e "  Fee:         $(format_sats "$fee")"
        [[ "$routing_fee" != "0" ]] && echo -e "  Routing:     $(format_sats "$routing_fee")"
        [[ "$dev_fee" != "0" ]] && echo -e "  Dev fee:     $(format_sats "$dev_fee") (pagada: $dev_fee_paid)"
        [[ -n "$dev_fee_payment_hash" && "$dev_fee_payment_hash" != "" ]] && echo -e "  Dev hash:    ${DIM}$dev_fee_payment_hash${NC}"
        echo ""
    fi

    # --- Participantes ---
    echo -e "${BOLD}  ─── Participantes ───${NC}"
    echo -e "  Creador:     $(format_pubkey "$creator_pubkey")"
    if [[ -n "$buyer_pubkey" && "$buyer_pubkey" != "" ]]; then
        echo -e "  Comprador:   $(format_pubkey "$buyer_pubkey")"
        [[ -n "$master_buyer_pubkey" && "$master_buyer_pubkey" != "" ]] && \
            echo -e "  Master buyer:$(format_pubkey "$master_buyer_pubkey")"
    else
        echo -e "  Comprador:   ${DIM}—${NC}"
    fi
    if [[ -n "$seller_pubkey" && "$seller_pubkey" != "" ]]; then
        echo -e "  Vendedor:    $(format_pubkey "$seller_pubkey")"
        [[ -n "$master_seller_pubkey" && "$master_seller_pubkey" != "" ]] && \
            echo -e "  Master seller:$(format_pubkey "$master_seller_pubkey")"
    else
        echo -e "  Vendedor:    ${DIM}—${NC}"
    fi
    echo ""

    # --- Lightning ---
    if [[ -n "$hash" && "$hash" != "" ]] || [[ -n "$preimage" && "$preimage" != "" ]] || [[ -n "$buyer_invoice" && "$buyer_invoice" != "" ]]; then
        echo -e "${BOLD}  ─── Lightning ───${NC}"
        [[ -n "$hash" && "$hash" != "" ]] && echo -e "  Hash:        ${DIM}$hash${NC}"
        [[ -n "$preimage" && "$preimage" != "" ]] && echo -e "  Preimage:    ${DIM}$preimage${NC}"
        if [[ -n "$buyer_invoice" && "$buyer_invoice" != "" ]]; then
            echo -e "  Invoice:     ${DIM}${buyer_invoice:0:40}...${NC}"
        fi
        [[ "$payment_attempts" != "0" ]] && echo -e "  Intentos:    $payment_attempts"
        [[ "$failed_payment" != "0" ]] && echo -e "  Fallidos:    ${RED}$failed_payment${NC}"
        echo ""
    fi

    # --- Disputas y cancelaciones ---
    if [[ "$buyer_dispute" != "0" || "$seller_dispute" != "0" || "$buyer_cooperativecancel" != "0" || "$seller_cooperativecancel" != "0" ]]; then
        echo -e "${BOLD}  ─── Disputas / Cancelaciones ───${NC}"
        [[ "$buyer_dispute" != "0" ]] && echo -e "  Disputa comprador:  ${RED}Sí${NC}"
        [[ "$seller_dispute" != "0" ]] && echo -e "  Disputa vendedor:   ${RED}Sí${NC}"
        [[ "$buyer_cooperativecancel" != "0" ]] && echo -e "  Cancel comprador:   ${YELLOW}Sí${NC}"
        [[ "$seller_cooperativecancel" != "0" ]] && echo -e "  Cancel vendedor:    ${YELLOW}Sí${NC}"
        [[ -n "$cancel_initiator_pubkey" && "$cancel_initiator_pubkey" != "" ]] && \
            echo -e "  Iniciador cancel:   $(format_pubkey "$cancel_initiator_pubkey")"
        [[ -n "$dispute_initiator_pubkey" && "$dispute_initiator_pubkey" != "" ]] && \
            echo -e "  Iniciador disputa:  $(format_pubkey "$dispute_initiator_pubkey")"
        echo ""
    fi

    # --- Ratings ---
    if [[ "$buyer_sent_rate" != "0" || "$seller_sent_rate" != "0" ]]; then
        echo -e "${BOLD}  ─── Valoraciones ───${NC}"
        [[ "$buyer_sent_rate" != "0" ]] && echo -e "  Rating comprador:   ⭐ $buyer_sent_rate"
        [[ "$seller_sent_rate" != "0" ]] && echo -e "  Rating vendedor:    ⭐ $seller_sent_rate"
        echo ""
    fi

    # --- Tiempos ---
    echo -e "${BOLD}  ─── Tiempos ───${NC}"
    echo -e "  Creada:      $(format_timestamp "$created_at")"
    echo -e "  Expira:      $(format_timestamp "$expires_at")"
    [[ "$taken_at" != "0" ]] && echo -e "  Tomada:      $(format_timestamp "$taken_at")"
    [[ "$invoice_held_at" != "0" ]] && echo -e "  Hold at:     $(format_timestamp "$invoice_held_at")"
    echo ""

    # --- Trade index ---
    if [[ "$trade_index_seller" != "0" || "$trade_index_buyer" != "0" ]]; then
        echo -e "${BOLD}  ─── Trade Index ───${NC}"
        [[ "$trade_index_seller" != "0" ]] && echo -e "  Seller idx:  $trade_index_seller"
        [[ "$trade_index_buyer" != "0" ]] && echo -e "  Buyer idx:   $trade_index_buyer"
        [[ -n "$next_trade_pubkey" && "$next_trade_pubkey" != "" ]] && echo -e "  Next pubkey: $(format_pubkey "$next_trade_pubkey")"
        [[ "$next_trade_index" != "0" ]] && echo -e "  Next idx:    $next_trade_index"
        echo ""
    fi

    # --- Orden padre (rangos) ---
    if [[ -n "$range_parent_id" && "$range_parent_id" != "" ]]; then
        echo -e "${BOLD}  ─── Rango ───${NC}"
        echo -e "  Orden padre: ${DIM}$range_parent_id${NC}"
        echo ""
    fi

    # --- Disputa en disputes.db ---
    if [[ -f "$DISPUTES_DB" ]]; then
        dispute=$(run_sql "$DISPUTES_DB" -separator '|' "
            SELECT
                LOWER(
                    SUBSTR(HEX(id),1,8) || '-' ||
                    SUBSTR(HEX(id),9,4) || '-' ||
                    SUBSTR(HEX(id),13,4) || '-' ||
                    SUBSTR(HEX(id),17,4) || '-' ||
                    SUBSTR(HEX(id),21,12)
                ),
                status, order_previous_status, solver_pubkey, created_at, taken_at
            FROM disputes WHERE LOWER(
                SUBSTR(HEX(order_id),1,8) || '-' ||
                SUBSTR(HEX(order_id),9,4) || '-' ||
                SUBSTR(HEX(order_id),13,4) || '-' ||
                SUBSTR(HEX(order_id),17,4) || '-' ||
                SUBSTR(HEX(order_id),21,12)
            ) = '$uuid'
        " 2>/dev/null)
        if [[ -n "$dispute" ]]; then
            IFS='|' read -r d_id d_status d_prev_status d_solver d_created d_taken <<< "$dispute"
            echo -e "${RED}${BOLD}  ─── ⚠️ DISPUTA ───${NC}"
            echo -e "  Disputa ID:  $d_id"
            echo -e "  Estado:      $d_status"
            echo -e "  Estado prev: $d_prev_status"
            [[ -n "$d_solver" && "$d_solver" != "" ]] && echo -e "  Solver:      $(format_pubkey "$d_solver")"
            echo -e "  Creada:      $(format_timestamp "$d_created")"
            [[ "$d_taken" != "0" ]] && echo -e "  Tomada:      $(format_timestamp "$d_taken")"
            echo ""
        fi
    fi

done <<< "$result"
