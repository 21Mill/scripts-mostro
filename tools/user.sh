#!/usr/bin/env bash
# user.sh — Consulta el historial de ordenes de un usuario por su clave publica.
#
# La clave que identifica a una persona entre operaciones es la MASTER pubkey
# (master_buyer_pubkey / master_seller_pubkey). Las columnas buyer_pubkey y
# seller_pubkey son claves efimeras, distintas en cada orden, asi que buscar por
# ellas solo encuentra una. Se aceptan las dos: si la clave dada es efimera se
# resuelve primero a su master y se avisa.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../admin/env.sh"

# --- Uso ---
if [[ -z "$1" ]]; then
    echo -e "${CYAN}Uso:${NC} $(basename "$0") <pubkey> [--status <estados>] [--json]"
    echo -e "  Historial de ordenes de un usuario (hex de 64 caracteres o npub)."
    echo ""
    echo -e "${CYAN}Ejemplos:${NC}"
    echo "  $(basename "$0") d91a8edba5c6526d7591e405cbb8fc8f931f3234afb98c8a8206fd1e47a4ee99"
    echo "  $(basename "$0") d91a8edb --status success          (solo completadas)"
    echo "  $(basename "$0") d91a8edb --status dispute,canceled (varios estados)"
    echo "  $(basename "$0") d91a8edb --status canceled*        (y canceled-by-admin)"
    echo "  $(basename "$0") d91a8edb --json     (salida JSON para otros scripts)"
    echo "  $(basename "$0") --top              (usuarios con mas ordenes)"
    exit 1
fi

DB_PATH="$MOSTRO_DB_RO"

if ! sql_ro "$DB_PATH" "SELECT 1" >/dev/null 2>&1; then
    echo -e "${RED}Error:${NC} No se pudo acceder a la base de datos en $DB_PATH"
    exit 1
fi

UUID_SQL="LOWER(SUBSTR(HEX(id),1,8)||'-'||SUBSTR(HEX(id),9,4)||'-'||SUBSTR(HEX(id),13,4)||'-'||SUBSTR(HEX(id),17,4)||'-'||SUBSTR(HEX(id),21,12))"

format_timestamp() {
    local ts="$1"
    if [[ -n "$ts" && "$ts" != "0" ]]; then
        date -d "@$ts" "+%Y-%m-%d %H:%M" 2>/dev/null || echo "$ts"
    else
        echo "—"
    fi
}

format_sats() {
    local sats="$1"
    if [[ -n "$sats" && "$sats" != "0" ]]; then
        printf "%'d" "$sats" 2>/dev/null | sed 's/,/./g'
    else
        echo "—"
    fi
}

# --- --top: quien opera mas ---
if [[ "$1" == "--top" ]]; then
    echo -e "${BOLD}${CYAN}═══ Usuarios con mas ordenes ═══${NC}"
    echo ""
    printf "  ${DIM}%-66s %6s %8s %8s${NC}\n" "MASTER PUBKEY" "TOTAL" "SUCCESS" "SATS"
    echo -e "  ${DIM}$(printf '─%.0s' {1..92})${NC}"
    sql_ro "$DB_PATH" -separator '|' "
        SELECT pk, COUNT(*) n, SUM(status='success') ok, SUM(CASE WHEN status='success' THEN amount ELSE 0 END) sats
        FROM (
            SELECT master_buyer_pubkey pk, status, amount FROM orders WHERE master_buyer_pubkey IS NOT NULL
            UNION ALL
            SELECT master_seller_pubkey, status, amount FROM orders WHERE master_seller_pubkey IS NOT NULL
        )
        GROUP BY pk ORDER BY n DESC LIMIT 15
    " | while IFS='|' read -r pk n ok sats; do
        printf "  %-66s %6s %8s %8s\n" "$pk" "$n" "$ok" "$(format_sats "$sats")"
    done
    echo ""
    exit 0
fi

PUBKEY=""
JSON=0
STATUS_FILTER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --json)   JSON=1; shift ;;
        --status) STATUS_FILTER="$2"; shift 2 ;;
        --status=*) STATUS_FILTER="${1#--status=}"; shift ;;
        -*)       echo -e "${RED}Error:${NC} opcion desconocida $1"; exit 1 ;;
        *)        PUBKEY="$1"; shift ;;
    esac
done

if [[ -z "$PUBKEY" ]]; then
    echo -e "${RED}Error:${NC} falta la clave publica"
    exit 1
fi

if [[ -n "$STATUS_FILTER" && -z "${STATUS_FILTER//[[:space:]]/}" ]]; then
    echo -e "${RED}Error:${NC} --status necesita al menos un estado"
    exit 1
fi

# npub -> hex, si hace falta y hay con que convertir
if [[ "$PUBKEY" == npub1* ]]; then
    if command -v nak >/dev/null 2>&1; then
        PUBKEY=$(nak decode "$PUBKEY" | grep -oE '[0-9a-f]{64}' | head -1)
    else
        echo -e "${RED}Error:${NC} para usar npub hace falta 'nak'. Pasa la clave en hex."
        exit 1
    fi
fi

# Prefijo: completar a partir de la propia base
if [[ ${#PUBKEY} -lt 64 ]]; then
    match=$(sql_ro "$DB_PATH" "
        SELECT DISTINCT pk FROM (
            SELECT master_buyer_pubkey pk FROM orders UNION SELECT master_seller_pubkey FROM orders
            UNION SELECT buyer_pubkey FROM orders UNION SELECT seller_pubkey FROM orders
        ) WHERE pk LIKE '${PUBKEY}%' LIMIT 5")
    n_match=$(echo "$match" | grep -c .)
    if [[ "$n_match" -eq 0 ]]; then
        echo -e "${RED}Sin resultados${NC} para el prefijo $PUBKEY"
        exit 1
    elif [[ "$n_match" -gt 1 ]]; then
        echo -e "${YELLOW}Prefijo ambiguo${NC}, coincide con:"
        echo "$match" | sed 's/^/  /'
        exit 1
    fi
    PUBKEY="$match"
fi

# Si es una clave efimera, subir a su master: si no, solo se veria una orden.
master=$(sql_ro "$DB_PATH" "
    SELECT COALESCE(
      (SELECT master_buyer_pubkey FROM orders WHERE buyer_pubkey='$PUBKEY' LIMIT 1),
      (SELECT master_seller_pubkey FROM orders WHERE seller_pubkey='$PUBKEY' LIMIT 1))
    WHERE NOT EXISTS (SELECT 1 FROM orders WHERE master_buyer_pubkey='$PUBKEY' OR master_seller_pubkey='$PUBKEY')")
if [[ -n "$master" ]]; then
    echo -e "${YELLOW}Nota:${NC} $PUBKEY es una clave de operacion; se usa su master ${BOLD}$master${NC}"
    echo ""
    PUBKEY="$master"
fi

WHERE="(master_buyer_pubkey='$PUBKEY' OR master_seller_pubkey='$PUBKEY')"

# --status: lista separada por comas. Un '*' final pasa a LIKE, para que
# "canceled*" recoja tambien canceled-by-admin y cooperatively-canceled no.
if [[ -n "$STATUS_FILTER" ]]; then
    cond=""
    IFS=',' read -ra estados <<< "$STATUS_FILTER"
    for e in "${estados[@]}"; do
        e="${e//[[:space:]]/}"
        [[ -z "$e" ]] && continue
        e="${e//\'/}"
        [[ -n "$cond" ]] && cond="$cond OR "
        if [[ "$e" == *\* ]]; then
            cond="${cond}status LIKE '${e%\*}%'"
        else
            cond="${cond}status='$e'"
        fi
    done
    WHERE="$WHERE AND ($cond)"

    # Un filtro que no casa con nada suele ser un estado mal escrito: mejor
    # decir cuales existen que devolver una tabla vacia sin explicacion.
    if [[ "$(sql_ro "$DB_PATH" "SELECT COUNT(*) FROM orders WHERE $WHERE")" == "0" ]]; then
        echo -e "${YELLOW}Sin ordenes${NC} con estado '$STATUS_FILTER' para este usuario."
        echo -e "  ${DIM}Estados presentes en sus ordenes:${NC}"
        sql_ro "$DB_PATH" "SELECT DISTINCT status FROM orders
            WHERE master_buyer_pubkey='$PUBKEY' OR master_seller_pubkey='$PUBKEY' ORDER BY 1" | sed 's/^/    /'
        exit 1
    fi
fi
# Rol y lado: el creador se identifica comparando creator_pubkey con la clave
# efimera de cada lado, porque creator_pubkey nunca es la master.
# Los alias importan: con --json son las claves del objeto, y sin ellos la del
# UUID sale siendo la expresion SQL entera.
SELECT_ORDERS="
    SELECT $UUID_SQL AS order_id,
           status, kind,
           CASE WHEN master_buyer_pubkey='$PUBKEY' THEN 'comprador' ELSE 'vendedor' END AS rol,
           CASE WHEN (master_buyer_pubkey='$PUBKEY' AND creator_pubkey=buyer_pubkey)
                  OR (master_seller_pubkey='$PUBKEY' AND creator_pubkey=seller_pubkey)
                THEN 'maker' ELSE 'taker' END AS lado,
           fiat_code, fiat_amount, min_amount, max_amount, amount AS sats, premium,
           created_at, taken_at,
           COALESCE((SELECT status FROM disputes d WHERE d.order_id = orders.id), '') AS disputa
    FROM orders WHERE $WHERE ORDER BY created_at DESC"

if [[ "$JSON" -eq 1 ]]; then
    sql_ro "$DB_PATH" -json "$SELECT_ORDERS"
    exit 0
fi

total=$(sql_ro "$DB_PATH" "SELECT COUNT(*) FROM orders WHERE $WHERE")
if [[ "$total" == "0" ]]; then
    echo -e "${RED}Sin ordenes${NC} para $PUBKEY"
    exit 1
fi

echo -e "${BOLD}${CYAN}═══ Usuario $PUBKEY ═══${NC}"
[[ -n "$STATUS_FILTER" ]] && echo -e "${DIM}  filtro de estado: $STATUS_FILTER${NC}"
echo ""

# Reputacion (tabla users; puede no existir si nunca completo una operacion)
sql_ro "$DB_PATH" -separator '|' "
    SELECT total_reviews, ROUND(total_rating,2), last_rating, last_trade_index, is_banned, created_at
    FROM users WHERE pubkey='$PUBKEY'" | while IFS='|' read -r rev rating last idx banned alta; do
    baneado=""
    [[ "$banned" == "1" ]] && baneado=" ${RED}[BANEADO]${NC}"
    echo -e "  Alta: $(format_timestamp "$alta")  |  Valoraciones: $rev (media $rating, ultima $last)  |  Trade index: $idx$baneado"
    echo ""
done

printf "  ${DIM}%-38s %-18s %-5s %-10s %-6s %10s %10s   %-16s  %s${NC}\n" \
    "ORDER ID" "STATUS" "TYPE" "ROL" "LADO" "FIAT" "SATS" "CREADA" "DISPUTA"
echo -e "  ${DIM}$(printf '─%.0s' {1..118})${NC}"

sql_ro "$DB_PATH" -separator '|' "$SELECT_ORDERS" |
while IFS='|' read -r uuid status kind rol lado fiat_code fiat_amount min_amount max_amount amount premium created_at taken_at disputa; do
    case "$status" in
        success)    status_color="${GREEN}$status${NC}" ;;
        pending)    status_color="${YELLOW}$status${NC}" ;;
        canceled*)  status_color="${RED}$status${NC}" ;;
        dispute)    status_color="${YELLOW}$status${NC}" ;;
        expired)    status_color="${DIM}$status${NC}" ;;
        *)          status_color="${CYAN}$status${NC}" ;;
    esac
    # Rango sin tomar: el importe concreto aun no existe
    if [[ "$min_amount" != "0" && "$max_amount" != "0" && ( "$fiat_amount" == "0" || "$status" == "pending" ) ]]; then
        fiat_display="${min_amount}-${max_amount}"
    else
        fiat_display="$fiat_amount"
    fi
    # El estado de la orden no dice a favor de quien se resolvio una disputa:
    # canceled-by-admin sale tanto si gano el vendedor como si gano el comprador.
    # Eso vive en disputes.status, asi que se muestra al lado.
    case "$disputa" in
        seller-refunded) disputa_txt="${RED}⚖ sats al vendedor${NC}" ;;
        settled)         disputa_txt="${GREEN}⚖ sats al comprador${NC}" ;;
        in-progress)     disputa_txt="${YELLOW}⚖ sin resolver${NC}" ;;
        "")              disputa_txt="" ;;
        *)               disputa_txt="${CYAN}⚖ $disputa${NC}" ;;
    esac
    printf "  %-38s %-31b %-5s %-10s %-6s %6s %-3s %10s   %-16s  %b\n" \
        "$uuid" "$status_color" "$kind" "$rol" "$lado" \
        "$fiat_display" "$fiat_code" "$(format_sats "$amount")" "$(format_timestamp "$created_at")" "$disputa_txt"
done

echo ""

# Recuento de disputas: util para juzgar a una contraparte de un vistazo.
disputas=$(sql_ro "$DB_PATH" -separator '|' "
    SELECT d.status, COUNT(*) FROM disputes d JOIN orders o ON o.id = d.order_id
    WHERE $WHERE GROUP BY d.status ORDER BY COUNT(*) DESC")
if [[ -n "$disputas" ]]; then
    echo -e "  ${BOLD}Disputas${NC}"
    while IFS='|' read -r dstatus n; do
        case "$dstatus" in
            seller-refunded) txt="resueltas a favor del vendedor" ;;
            settled)         txt="resueltas a favor del comprador" ;;
            in-progress)     txt="abiertas, sin resolver" ;;
            *)               txt="$dstatus" ;;
        esac
        printf "    %3s %s\n" "$n" "$txt"
    done <<< "$disputas"
    echo ""
fi

echo -e "  ${BOLD}Totales${NC}"
sql_ro "$DB_PATH" -separator '|' "
    SELECT status, COUNT(*), SUM(amount), SUM(fiat_amount)
    FROM orders WHERE $WHERE GROUP BY status ORDER BY COUNT(*) DESC" |
while IFS='|' read -r status n sats fiat; do
    printf "    %-18s %3s ordenes   %10s sats   %6s EUR\n" "$status" "$n" "$(format_sats "$sats")" "$fiat"
done
echo ""
