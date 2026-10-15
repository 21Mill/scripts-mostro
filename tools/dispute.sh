#!/bin/bash
# ============================================================================
# dispute.sh — Lee la mediación que Serbero hizo de una disputa
# Uso: ./dispute.sh                  Lista las últimas mediaciones
#      ./dispute.sh <dispute_id>     Conversación completa (basta el prefijo)
#
# Serbero media con SU clave, no con la del solver, así que estas disputas no
# aparecen en mostrix y sus mensajes (brief, transcript, final_report) viajan
# por Nostr sin pasar por Telegram: el watchdog solo relaya el estado. Esta es
# la única forma de leer qué se dijeron las partes.
#
# Requiere sudo porque $SERBERO_DB es serbero:serbero 700 y no está en el
# snapshot de solo lectura (mostro-snapshot corre como mostro y no puede
# leerla). No se añade ninguna regla de sudoers: se pide la contraseña.
# La base se abre en modo lectura, así que es seguro con serbero escribiendo.
# ============================================================================

source "$(dirname "${BASH_SOURCE[0]}")/../admin/env.sh"

DISPUTE_ID="${1}"
LIMIT="${DISPUTE_LIMIT:-20}"
US=$'\x1f'   # separador de campos
RS=$'\x1e'   # marca de salto de línea dentro de un campo

if [ ! -f "$SERBERO_DB" ]; then
    echo -e "${RED}❌ No existe $SERBERO_DB${NC}"
    echo "Serbero no está instalado en esta máquina, o la ruta cambió (SERBERO_DB)."
    exit 1
fi

# sudo solo si no podemos leerla ya (permite probar con SERBERO_DB=fixture.db)
if [ -r "$SERBERO_DB" ]; then
    SQL=(sqlite3)
else
    SQL=(sudo sqlite3)
fi

# El contenido de la base lo escriben comprador y vendedor, así que se le quitan los
# caracteres de control antes de que lleguen al terminal: una secuencia ANSI en un mensaje
# de disputa podría repintar o manipular la sesión de quien lo lee. Se conservan tabulador,
# salto de línea y los dos separadores.
q() {
    "${SQL[@]}" -readonly -noheader -separator "$US" "$SERBERO_DB" "$1" \
        | tr -d '\000-\010\013-\035\177'
}

fecha() { [ -z "$1" ] || [ "$1" = "0" ] && { echo "-"; return; }; date -u -d "@$1" "+%Y-%m-%d %H:%M:%S"; }

# Un UUID solo tiene hex y guiones. La comprobación no es cosmética: estas consultas se
# interpolan en SQL y sqlite3 corre aquí como root, así que un argumento como
#   '; select writefile('/etc/cron.d/x','...'); --
# escribiría ficheros arbitrarios. El -readonly no lo evita: bloquea escrituras en la base,
# no en el sistema de ficheros. Se valida también lo que vuelve de la base (segundo orden).
id_valido() { [[ "$1" =~ ^[0-9a-fA-F-]{1,36}$ ]]; }

exige_id() {
    if ! id_valido "$1"; then
        echo -e "${RED}❌ Identificador no válido: '$1'${NC}" >&2
        echo "Se esperan solo dígitos hexadecimales y guiones (máximo 36 caracteres)." >&2
        exit 1
    fi
}

# ---------------------------------------------------------------- listado ---
if [ -z "$DISPUTE_ID" ]; then
    echo -e "${BOLD}${CYAN}Últimas mediaciones de Serbero${NC}\n"
    printf "%-20s %-38s %-24s %-11s %s\n" "ABIERTA (UTC)" "DISPUTA" "ESTADO" "IMPORTE" "ABRE / HANDOFF"
    printf '%.0s-' {1..120}; echo
    FILAS=$(q "select s.opened_at, s.dispute_id, coalesce(d.status, s.state, '-'),
                      coalesce(s.fiat_amount,'') , coalesce(s.fiat_code,''),
                      coalesce(d.initiator,'-'), coalesce(s.handoff_reason,'-')
                 from sessions s left join disputes d on d.dispute_id = s.dispute_id
                order by s.opened_at desc limit $LIMIT")
    if [ -z "$FILAS" ]; then
        echo -e "${YELLOW}Sin sesiones de mediación.${NC}"
        exit 0
    fi
    while IFS="$US" read -r abierta id estado importe divisa inicia handoff; do
        [ -n "$importe" ] && importe="$importe $divisa" || importe="-"
        printf "%-20s %-38s %-24s %-11s %s / %s\n" \
            "$(fecha "$abierta")" "$id" "$estado" "$importe" "$inicia" "$handoff"
    done <<< "$FILAS"
    echo -e "\nDetalle: ${BOLD}$0 <dispute_id>${NC}"
    exit 0
fi

# ------------------------------------------------------------- una disputa ---
exige_id "$DISPUTE_ID"

COINCIDENCIAS=$(q "select dispute_id from sessions where dispute_id like '${DISPUTE_ID}%' order by opened_at")
TOTAL=$(echo "$COINCIDENCIAS" | grep -c . || true)

if [ "$TOTAL" -eq 0 ]; then
    echo -e "${RED}❌ Serbero no medió ninguna disputa que empiece por '$DISPUTE_ID'${NC}"
    echo "Si la disputa existe, es que se resolvió sin mediación. Compruébalo con:"
    echo "  sqlite3 \"$MOSTRO_DISPUTES_DB_RO\" \"select dispute_id, status from dispute_messages;\""
    exit 1
elif [ "$TOTAL" -gt 1 ]; then
    echo -e "${YELLOW}⚠️  Varias coincidencias; concreta el id:${NC}"
    echo "$COINCIDENCIAS" | sed 's/^/  /'
    exit 1
fi

ID="$COINCIDENCIAS"
exige_id "$ID"
IFS="$US" read -r SESION ESTADO CICLO ESTADO_AT INICIA IMPORTE DIVISA METODO PUB ABIERTA CERRADA HANDOFF RONDAS RC RV < <(
    q "select s.session_id, coalesce(d.status,'-'), coalesce(d.lifecycle,'-'), coalesce(d.status_at,0),
              coalesce(d.initiator,'-'), coalesce(s.fiat_amount,'-'), coalesce(s.fiat_code,''),
              coalesce(s.payment_method,'-'), coalesce(s.order_published_at,0), s.opened_at,
              coalesce(s.updated_at,0), coalesce(s.handoff_reason,'-'),
              coalesce(s.rounds,0), coalesce(s.buyer_rounds,0), coalesce(s.seller_rounds,0)
         from sessions s left join disputes d on d.dispute_id = s.dispute_id
        where s.dispute_id = '$ID'")

echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}DISPUTA${NC}   $ID"
echo -e "sesión    $SESION"
echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════════════════════${NC}"
echo "  estado      $ESTADO ($CICLO) a las $(fecha "$ESTADO_AT")"
echo "  la abrió    $INICIA"
echo "  orden       $IMPORTE $DIVISA vía $METODO, publicada $(fecha "$PUB")"
echo "  mediación   $(fecha "$ABIERTA") → $(fecha "$CERRADA")"
echo "  handoff     $HANDOFF   rondas: $RONDAS (comprador $RC, vendedor $RV)"

exige_id "$SESION"

HANDOFF_AT=$(q "select created_at from events where session_id='$SESION' and kind='handoff' limit 1")

echo -e "\n${BOLD}${CYAN}── CONVERSACIÓN ──────────────────────────────────────────────────${NC}"
MARCADO=0
MENSAJES=$(q "select created_at, direction, coalesce(party,'?'), coalesce(template_id,''),
                     coalesce(attachments,0), replace(coalesce(content,''), char(10), '$RS')
                from messages where session_id='$SESION' order by created_at, id")
if [ -z "$MENSAJES" ]; then
    echo -e "${YELLOW}  (sin mensajes registrados)${NC}"
else
    while IFS="$US" read -r cuando dir quien plantilla adj texto; do
        if [ -n "$HANDOFF_AT" ] && [ "$MARCADO" -eq 0 ] && [ "$cuando" -gt "$HANDOFF_AT" ]; then
            echo -e "\n${YELLOW}  ──── HANDOFF $(fecha "$HANDOFF_AT") ($HANDOFF): lo que sigue llegó después ────${NC}"
            MARCADO=1
        fi
        if [ "$dir" = "out" ]; then
            etiqueta="${GREEN}Serbero → $quien${NC}"
            [ -n "$plantilla" ] && etiqueta="$etiqueta ${BLUE}[$plantilla]${NC}"
        else
            etiqueta="${BOLD}$quien${NC} →"
        fi
        echo -e "\n  $(fecha "$cuando")  $etiqueta"
        printf '%s\n' "$texto" | tr "$RS" '\n' | sed 's/^/      /'
        [ "$adj" != "0" ] && echo "      ($adj adjunto/s)"
    done <<< "$MENSAJES"
fi

VEREDICTOS=$(q "select judge_id, question_set_version, coalesce(input_tokens,0),
                       coalesce(last_message_id,0), action_json, answers_json
                  from evaluations where session_id='$SESION' order by created_at, id")
if [ -n "$VEREDICTOS" ]; then
    echo -e "\n${BOLD}${CYAN}── LECTURA DEL JUEZ ──────────────────────────────────────────────${NC}"
    while IFS="$US" read -r juez version tokens ultimo accion respuestas; do
        echo -e "\n  ${BOLD}$juez${NC}  ($version, $tokens tokens, hasta el mensaje $ultimo)"
        echo "  acción: $accion"
        echo "$respuestas" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
for key in sorted(data):
    a = data[key] if isinstance(data[key], dict) else {}
    p_yes = a.get("p_yes")
    probs = a.get("probabilities")
    if p_yes is not None:
        print("    %-24s %.2f" % (key, p_yes))
    elif isinstance(probs, dict) and probs:
        k, v = max(probs.items(), key=lambda kv: kv[1])
        print("    %-24s %s (%.2f)" % (key, k, v))
    else:
        print("    %-24s %s" % (key, probs))
'
    done <<< "$VEREDICTOS"
fi

echo -e "\n${BOLD}${CYAN}── CRONOLOGÍA ────────────────────────────────────────────────────${NC}"
q "select created_at, kind, coalesce(payload_json,'') from events
    where dispute_id='$ID' order by created_at, id" |
while IFS="$US" read -r cuando tipo carga; do
    printf "  %s  %-20s %s\n" "$(fecha "$cuando")" "$tipo" "$carga"
done
