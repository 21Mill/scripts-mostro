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
    echo "  $(basename "$0") --recent        (ultimas 10 ordenes)"
    echo "  $(basename "$0") --pending       (ordenes pendientes)"
    echo "  $(basename "$0") --active        (ordenes en curso)"
    echo "  $(basename "$0") --stats         (estadisticas generales)"
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

# SQL helper para formatear UUIDs
UUID_SQL="LOWER(SUBSTR(HEX(id),1,8)||'-'||SUBSTR(HEX(id),9,4)||'-'||SUBSTR(HEX(id),13,4)||'-'||SUBSTR(HEX(id),17,4)||'-'||SUBSTR(HEX(id),21,12))"

# --- Funciones de formato ---
format_timestamp() {
    local ts="$1"
    if [[ -n "$ts" && "$ts" != "0" && "$ts" != "" ]]; then
        date -d "@$ts" "+%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "$ts"
    else
        echo -e "${DIM}—${NC}"
    fi
}

format_sats() {
    local sats="$1"
    if [[ -n "$sats" && "$sats" != "0" && "$sats" != "" ]]; then
        printf "%'d sats" "$sats" 2>/dev/null | sed 's/,/./g'
    else
        echo -e "${DIM}—${NC}"
    fi
}

format_pubkey() {
    local pk="$1"
    if [[ -n "$pk" && "$pk" != "" ]]; then
        echo "${pk:0:16}...${pk: -8}"
    else
        echo -e "${DIM}—${NC}"
    fi
}

format_duration() {
    local start="$1" end="$2"
    if [[ -z "$start" || "$start" == "0" || -z "$end" || "$end" == "0" ]]; then
        return
    fi
    local diff=$(( end - start ))
    if [[ $diff -lt 0 ]]; then return; fi
    if [[ $diff -lt 60 ]]; then
        echo "${diff}s"
    elif [[ $diff -lt 3600 ]]; then
        echo "$(( diff / 60 ))min $(( diff % 60 ))s"
    else
        echo "$(( diff / 3600 ))h $(( (diff % 3600) / 60 ))min"
    fi
}

format_fiat() {
    local fiat_amount="$1" min_amount="$2" max_amount="$3" fiat_code="$4" status="$5"
    if [[ "$min_amount" != "0" && "$min_amount" != "" && "$max_amount" != "0" && "$max_amount" != "" ]]; then
        # Orden con rango
        if [[ "$fiat_amount" != "0" && "$fiat_amount" != "" && "$status" != "pending" ]]; then
            echo "$fiat_amount $fiat_code (rango: $min_amount — $max_amount)"
        else
            echo "$min_amount — $max_amount $fiat_code"
        fi
    else
        echo "$fiat_amount $fiat_code"
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

# --stats
if [[ "$1" == "--stats" ]]; then
    echo -e "${BOLD}${CYAN}═══ Estadisticas de Mostro ═══${NC}"
    echo ""
    stats=$(run_sql "$DB_PATH" -separator '|' "
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as completadas,
            SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pendientes,
            SUM(CASE WHEN status LIKE 'canceled%' THEN 1 ELSE 0 END) as canceladas,
            SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END) as expiradas,
            SUM(CASE WHEN status='dispute' THEN 1 ELSE 0 END) as disputas,
            SUM(CASE WHEN status NOT IN ('success','pending','canceled','canceled-by-admin','expired','dispute') THEN 1 ELSE 0 END) as en_curso,
            SUM(CASE WHEN status='success' THEN amount ELSE 0 END) as total_sats,
            SUM(CASE WHEN status='success' THEN fee ELSE 0 END) as total_fees,
            SUM(CASE WHEN status='success' THEN routing_fee ELSE 0 END) as total_routing,
            SUM(CASE WHEN status='success' THEN dev_fee ELSE 0 END) as total_dev_fee,
            SUM(CASE WHEN status='success' AND kind='buy' THEN 1 ELSE 0 END) as buys_ok,
            SUM(CASE WHEN status='success' AND kind='sell' THEN 1 ELSE 0 END) as sells_ok
        FROM orders
    ")
    IFS='|' read -r total completadas pendientes canceladas expiradas disputas en_curso total_sats total_fees total_routing total_dev_fee buys_ok sells_ok <<< "$stats"

    echo -e "  ${BOLD}Ordenes${NC}"
    echo -e "  Total:         $total"
    echo -e "  Completadas:   ${GREEN}$completadas${NC} (compras: $buys_ok, ventas: $sells_ok)"
    echo -e "  Pendientes:    ${YELLOW}$pendientes${NC}"
    echo -e "  En curso:      ${CYAN}$en_curso${NC}"
    echo -e "  Canceladas:    ${RED}$canceladas${NC}"
    echo -e "  Expiradas:     ${DIM}$expiradas${NC}"
    [[ "$disputas" != "0" ]] && echo -e "  Disputas:      ${RED}$disputas${NC}"
    echo ""
    echo -e "  ${BOLD}Volumen (ordenes completadas)${NC}"
    echo -e "  Total sats:    $(format_sats "$total_sats")"
    echo -e "  Fees cobradas: $(format_sats "$total_fees")"
    [[ "$total_routing" != "0" ]] && echo -e "  Routing fees:  $(format_sats "$total_routing")"
    [[ "$total_dev_fee" != "0" ]] && echo -e "  Dev fees:      $(format_sats "$total_dev_fee")"
    echo ""

    # Monedas usadas
    monedas=$(run_sql "$DB_PATH" -separator '|' "
        SELECT fiat_code, COUNT(*) as n, SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as ok
        FROM orders GROUP BY fiat_code ORDER BY n DESC
    ")
    echo -e "  ${BOLD}Monedas${NC}"
    while IFS='|' read -r code n ok; do
        echo -e "  $code: $n ordenes ($ok completadas)"
    done <<< "$monedas"
    echo ""

    # Usuarios activos
    users=$(run_sql "$DB_PATH" "SELECT COUNT(*) FROM users")
    echo -e "  ${BOLD}Usuarios registrados:${NC} $users"
    echo ""
    exit 0
fi

# --recent
if [[ "$1" == "--recent" ]]; then
    echo -e "${BOLD}${CYAN}═══ Ultimas 10 ordenes ═══${NC}"
    echo ""
    results=$(run_sql "$DB_PATH" -separator '|' "
        SELECT $UUID_SQL, kind, status, fiat_code, fiat_amount, min_amount, max_amount, amount, created_at
        FROM orders ORDER BY created_at DESC LIMIT 10
    ")
    printf "  ${DIM}%-38s %-6s %-24s %8s %-5s %10s   %-19s${NC}\n" "ORDER ID" "TYPE" "STATUS" "FIAT" "CODE" "SATS" "CREATED"
    echo -e "  ${DIM}$(printf '─%.0s' {1..120})${NC}"
    while IFS='|' read -r uuid kind status fiat_code fiat_amount min_amount max_amount amount created_at; do
        ts=$(format_timestamp "$created_at")
        sats="${DIM}—${NC}"
        [[ "$amount" != "0" && "$amount" != "" ]] && sats=$(printf "%'d" "$amount" 2>/dev/null | sed 's/,/./g')
        if [[ "$min_amount" != "0" && "$min_amount" != "" && "$max_amount" != "0" && "$max_amount" != "" ]]; then
            if [[ "$fiat_amount" != "0" && "$fiat_amount" != "" && "$status" != "pending" ]]; then
                fiat_display="$fiat_amount"
            else
                fiat_display="${min_amount}-${max_amount}"
            fi
        else
            fiat_display="$fiat_amount"
        fi
        case "$status" in
            pending)    status_color="${YELLOW}$status${NC}" ;;
            success)    status_color="${GREEN}$status${NC}" ;;
            canceled*)  status_color="${RED}$status${NC}" ;;
            expired)    status_color="${DIM}$status${NC}" ;;
            *)          status_color="${CYAN}$status${NC}" ;;
        esac
        printf "  %-38s %-6s %-24b %8s %-5s %10s   %-19s\n" "$uuid" "$kind" "$status_color" "$fiat_display" "$fiat_code" "$sats" "$ts"
    done <<< "$results"
    echo ""
    exit 0
fi

# --pending
if [[ "$1" == "--pending" ]]; then
    echo -e "${BOLD}${CYAN}═══ Ordenes pendientes ═══${NC}"
    echo ""
    results=$(run_sql "$DB_PATH" -separator '|' "
        SELECT $UUID_SQL, kind, fiat_code, fiat_amount, min_amount, max_amount, amount, premium, payment_method, created_at, expires_at
        FROM orders WHERE status='pending' ORDER BY created_at DESC
    ")
    if [[ -z "$results" ]]; then
        echo -e "  ${DIM}No hay ordenes pendientes.${NC}"
        exit 0
    fi
    while IFS='|' read -r uuid kind fiat_code fiat_amount min_amount max_amount amount premium payment_method created_at expires_at; do
        tipo_emoji="🟢 BUY"
        [[ "$kind" == "sell" ]] && tipo_emoji="🔴 SELL"
        echo -e "  ${BOLD}$tipo_emoji${NC}  $uuid"
        echo "       Fiat: $(format_fiat "$fiat_amount" "$min_amount" "$max_amount" "$fiat_code" "pending")"
        [[ "$amount" != "0" && "$amount" != "" ]] && echo "       Sats: $(format_sats "$amount")"
        echo "       Premium: ${premium}%  |  Metodo: $payment_method"
        echo "       Creada: $(format_timestamp "$created_at")  |  Expira: $(format_timestamp "$expires_at")"
        echo ""
    done <<< "$results"
    exit 0
fi

# --active (ordenes en curso, no pending ni finalizadas)
if [[ "$1" == "--active" ]]; then
    echo -e "${BOLD}${CYAN}═══ Ordenes en curso ═══${NC}"
    echo ""
    results=$(run_sql "$DB_PATH" -separator '|' "
        SELECT $UUID_SQL, kind, status, fiat_code, fiat_amount, min_amount, max_amount, amount, payment_method, taken_at, created_at
        FROM orders
        WHERE status NOT IN ('pending','success','canceled','canceled-by-admin','expired')
        ORDER BY created_at DESC
    ")
    if [[ -z "$results" ]]; then
        echo -e "  ${DIM}No hay ordenes en curso.${NC}"
        exit 0
    fi
    while IFS='|' read -r uuid kind status fiat_code fiat_amount min_amount max_amount amount payment_method taken_at created_at; do
        tipo_emoji="🟢 BUY"
        [[ "$kind" == "sell" ]] && tipo_emoji="🔴 SELL"
        echo -e "  ${BOLD}$tipo_emoji${NC}  $uuid"
        echo -e "       Estado: $(format_status "$status")"
        echo "       Fiat: $(format_fiat "$fiat_amount" "$min_amount" "$max_amount" "$fiat_code" "$status")"
        [[ "$amount" != "0" && "$amount" != "" ]] && echo "       Sats: $(format_sats "$amount")"
        echo "       Metodo: $payment_method"
        if [[ "$taken_at" != "0" && "$taken_at" != "" ]]; then
            dur=$(format_duration "$taken_at" "$(date +%s)")
            echo "       Tomada: $(format_timestamp "$taken_at")  (hace $dur)"
        fi
        echo ""
    done <<< "$results"
    exit 0
fi

# --- Busqueda de orden especifica ---
ORDER_INPUT=$(echo "$1" | tr -d '-' | tr '[:lower:]' '[:upper:]')

# Buscar por UUID exacto o parcial
if [[ ${#ORDER_INPUT} -eq 32 ]]; then
    WHERE_CLAUSE="HEX(id) = '$ORDER_INPUT'"
else
    WHERE_CLAUSE="HEX(id) LIKE '${ORDER_INPUT}%'"
fi

result=$(run_sql "$DB_PATH" -separator '|' "
    SELECT
        $UUID_SQL,
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
    echo -e "${RED}No se encontro ninguna orden que coincida con:${NC} $1"
    exit 1
fi

# Contar resultados
num_results=$(echo "$result" | wc -l)
if [[ "$num_results" -gt 1 ]]; then
    echo -e "${YELLOW}Se encontraron $num_results ordenes. Mostrando todas:${NC}"
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
    echo -e "  Fiat:        $(format_fiat "$fiat_amount" "$min_amount" "$max_amount" "$fiat_code" "$status")"
    if [[ "$amount" != "0" && "$amount" != "" ]]; then
        echo -e "  Sats:        $(format_sats "$amount")"
    else
        echo -e "  Sats:        ${DIM}A definir por mercado${NC}"
    fi
    echo -e "  Premium:     ${premium}%"
    if [[ "$price_from_api" == "1" ]]; then
        echo -e "  Precio API:  ${GREEN}Si${NC} (precio obtenido de API externa)"
    elif [[ "$price_from_api" != "0" && "$price_from_api" != "" ]]; then
        echo -e "  Precio API:  $price_from_api"
    fi
    echo -e "  Metodo:      $payment_method"
    echo ""

    # --- Comisiones ---
    if [[ ("$fee" != "0" && "$fee" != "") || ("$routing_fee" != "0" && "$routing_fee" != "") || ("$dev_fee" != "0" && "$dev_fee" != "") ]]; then
        total_fees=$(( ${fee:-0} + ${routing_fee:-0} + ${dev_fee:-0} ))
        echo -e "${BOLD}  ─── Comisiones ───${NC}"
        [[ "$fee" != "0" && "$fee" != "" ]] && echo -e "  Fee:         $(format_sats "$fee")"
        [[ "$routing_fee" != "0" && "$routing_fee" != "" ]] && echo -e "  Routing:     $(format_sats "$routing_fee")"
        if [[ "$dev_fee" != "0" && "$dev_fee" != "" ]]; then
            dev_paid_txt="${RED}No${NC}"
            [[ "$dev_fee_paid" == "1" ]] && dev_paid_txt="${GREEN}Si${NC}"
            echo -e "  Dev fee:     $(format_sats "$dev_fee") (pagada: $dev_paid_txt)"
        fi
        echo -e "  ${DIM}Total:       $(format_sats "$total_fees")${NC}"
        [[ -n "$dev_fee_payment_hash" && "$dev_fee_payment_hash" != "" ]] && echo -e "  Dev hash:    ${DIM}$dev_fee_payment_hash${NC}"
        echo ""
    fi

    # --- Participantes ---
    echo -e "${BOLD}  ─── Participantes ───${NC}"
    echo -e "  Creador:      $(format_pubkey "$creator_pubkey")"
    if [[ -n "$buyer_pubkey" && "$buyer_pubkey" != "" ]]; then
        echo -e "  Comprador:    $(format_pubkey "$buyer_pubkey")"
        [[ -n "$master_buyer_pubkey" && "$master_buyer_pubkey" != "" ]] && \
            echo -e "  Master buyer: $(format_pubkey "$master_buyer_pubkey")"
    else
        echo -e "  Comprador:    ${DIM}—${NC}"
    fi
    if [[ -n "$seller_pubkey" && "$seller_pubkey" != "" ]]; then
        echo -e "  Vendedor:     $(format_pubkey "$seller_pubkey")"
        [[ -n "$master_seller_pubkey" && "$master_seller_pubkey" != "" ]] && \
            echo -e "  Master seller:$(format_pubkey "$master_seller_pubkey")"
    else
        echo -e "  Vendedor:     ${DIM}—${NC}"
    fi
    echo ""

    # --- Lightning ---
    if [[ (-n "$hash" && "$hash" != "") || (-n "$preimage" && "$preimage" != "") || (-n "$buyer_invoice" && "$buyer_invoice" != "") ]]; then
        echo -e "${BOLD}  ─── Lightning ───${NC}"
        [[ -n "$hash" && "$hash" != "" ]] && echo -e "  Hash:        ${DIM}$hash${NC}"
        [[ -n "$preimage" && "$preimage" != "" ]] && echo -e "  Preimage:    ${DIM}$preimage${NC}"
        if [[ -n "$buyer_invoice" && "$buyer_invoice" != "" ]]; then
            echo -e "  Invoice:     ${DIM}${buyer_invoice:0:50}...${NC}"
        fi
        if [[ "$payment_attempts" != "0" && "$payment_attempts" != "" ]]; then
            echo -e "  Intentos:    $payment_attempts"
        fi
        if [[ "$failed_payment" != "0" && "$failed_payment" != "" ]]; then
            echo -e "  Fallidos:    ${RED}$failed_payment${NC}"
        fi
        echo ""
    fi

    # --- Disputas y cancelaciones ---
    if [[ "$buyer_dispute" != "0" || "$seller_dispute" != "0" || "$buyer_cooperativecancel" != "0" || "$seller_cooperativecancel" != "0" || (-n "$cancel_initiator_pubkey" && "$cancel_initiator_pubkey" != "") || (-n "$dispute_initiator_pubkey" && "$dispute_initiator_pubkey" != "") ]]; then
        echo -e "${BOLD}  ─── Disputas / Cancelaciones ───${NC}"
        [[ "$buyer_dispute" != "0" ]] && echo -e "  Disputa comprador:  ${RED}Si${NC}"
        [[ "$seller_dispute" != "0" ]] && echo -e "  Disputa vendedor:   ${RED}Si${NC}"
        [[ "$buyer_cooperativecancel" != "0" ]] && echo -e "  Cancel comprador:   ${YELLOW}Si${NC}"
        [[ "$seller_cooperativecancel" != "0" ]] && echo -e "  Cancel vendedor:    ${YELLOW}Si${NC}"
        [[ -n "$cancel_initiator_pubkey" && "$cancel_initiator_pubkey" != "" ]] && \
            echo -e "  Iniciador cancel:   $(format_pubkey "$cancel_initiator_pubkey")"
        [[ -n "$dispute_initiator_pubkey" && "$dispute_initiator_pubkey" != "" ]] && \
            echo -e "  Iniciador disputa:  $(format_pubkey "$dispute_initiator_pubkey")"
        echo ""
    fi

    # --- Ratings ---
    if [[ ("$buyer_sent_rate" != "0" && "$buyer_sent_rate" != "") || ("$seller_sent_rate" != "0" && "$seller_sent_rate" != "") ]]; then
        echo -e "${BOLD}  ─── Valoraciones ───${NC}"
        [[ "$buyer_sent_rate" != "0" && "$buyer_sent_rate" != "" ]] && echo -e "  Rating comprador:   ⭐ $buyer_sent_rate"
        [[ "$seller_sent_rate" != "0" && "$seller_sent_rate" != "" ]] && echo -e "  Rating vendedor:    ⭐ $seller_sent_rate"
        echo ""
    fi

    # --- Tiempos ---
    echo -e "${BOLD}  ─── Tiempos ───${NC}"
    echo -e "  Creada:      $(format_timestamp "$created_at")"
    echo -e "  Expira:      $(format_timestamp "$expires_at")"
    if [[ "$taken_at" != "0" && "$taken_at" != "" ]]; then
        echo -e "  Tomada:      $(format_timestamp "$taken_at")"
        wait_dur=$(format_duration "$created_at" "$taken_at")
        [[ -n "$wait_dur" ]] && echo -e "  ${DIM}Espera hasta toma: $wait_dur${NC}"
    fi
    if [[ "$invoice_held_at" != "0" && "$invoice_held_at" != "" ]]; then
        echo -e "  Hold at:     $(format_timestamp "$invoice_held_at")"
    fi
    if [[ "$status" == "success" && "$taken_at" != "0" && "$taken_at" != "" ]]; then
        # Calcular duracion total del trade (desde toma hasta ultimo timestamp conocido)
        end_ts="$invoice_held_at"
        [[ "$end_ts" == "0" || "$end_ts" == "" ]] && end_ts="$expires_at"
        trade_dur=$(format_duration "$taken_at" "$end_ts")
        [[ -n "$trade_dur" ]] && echo -e "  ${DIM}Duracion del trade: ~$trade_dur${NC}"
    fi
    echo ""

    # --- Trade index ---
    if [[ ("$trade_index_seller" != "0" && "$trade_index_seller" != "") || ("$trade_index_buyer" != "0" && "$trade_index_buyer" != "") ]]; then
        echo -e "${BOLD}  ─── Trade Index ───${NC}"
        [[ "$trade_index_seller" != "0" && "$trade_index_seller" != "" ]] && echo -e "  Seller idx:  $trade_index_seller"
        [[ "$trade_index_buyer" != "0" && "$trade_index_buyer" != "" ]] && echo -e "  Buyer idx:   $trade_index_buyer"
        [[ -n "$next_trade_pubkey" && "$next_trade_pubkey" != "" ]] && echo -e "  Next pubkey: $(format_pubkey "$next_trade_pubkey")"
        [[ "$next_trade_index" != "0" && "$next_trade_index" != "" ]] && echo -e "  Next idx:    $next_trade_index"
        echo ""
    fi

    # --- Orden padre (rangos) ---
    if [[ -n "$range_parent_id" && "$range_parent_id" != "" ]]; then
        echo -e "${BOLD}  ─── Rango ───${NC}"
        echo -e "  Orden padre: ${DIM}$range_parent_id${NC}"
        echo ""
    fi

    # --- Disputa en disputes.db ---
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
        [[ "$d_taken" != "0" && "$d_taken" != "" ]] && echo -e "  Tomada:      $(format_timestamp "$d_taken")"
        echo ""
    fi

done <<< "$result"
