"""
resumen.py — Publica en Telegram el resumen diario de la actividad de NostroMostro.

Tiene dos fuentes, y la diferencia entre ellas es lo que hay que tener presente al tocar
este fichero:

  - data/premiums.json, el fichero anonimizado que la web ya sirve. Todo lo que sale de
    ahí es público por definición, no puede revelar nada que no esté ya publicado, y no
    requiere acceso a mostro.db.
  - accounting.db, la contabilidad de la instancia: cuánto se ha ganado. Eso es privado, y
    solo se incluye si el destino se verifica privado (ver destino_privado). Reapuntar el
    resumen a un canal público hace que el bloque desaparezca, no que se publique.

Lo invoca premiums.sh al final del cron diario de las 00:00.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.contabilidad import abrir, consultar, rango_dia, rango_mes
from lib.entorno import cargar_env
from lib.estado import cargar_estado, guardar_estado
from lib.formato import MESES, fecha_larga, formato_euros, formato_sats
from lib.telegram import credenciales_de_toml, enviar

SCRIPT_DIR = Path(__file__).resolve().parent
ESTADO_FILE = SCRIPT_DIR / "resumen-enviado.json"

# Por ruta absoluta a propósito: premiums.sh hace 'cd' al repo web antes de invocarnos, así
# que un load_dotenv() sin argumentos buscaría el .env desde el directorio equivocado.
cargar_env()

WEB_REPO = Path(
    os.getenv("NOSTROMOSTRO_WEB_REPO", str(Path.home() / "nostromostro.github.io"))
)
PREMIUMS_FILE = WEB_REPO / "data" / "premiums.json"

# La contabilidad vive en accounting/, un directorio más allá. Sobreescribible por entorno
# para poder probar contra una base de juguete.
ACCOUNTING_DB = Path(
    os.getenv("ACCOUNTING_DB", SCRIPT_DIR.parent / "accounting" / "accounting.db")
)

URL_MERCADO = "https://nostromostro.github.io/#mercado"

# Con menos trades que esto, el ranking describiría las operaciones una por una en vez de
# agregarlas. Por debajo del umbral la línea se omite.
MIN_TRADES_METODOS = 5

# Origen de las credenciales. Con TELEGRAM_STATS_CONFIG apuntando al config.toml de otro
# servicio, se reutiliza su bot y su chat. Sin ella, valen las variables del .env.
#
# Nada de respaldos implícitos: TELEGRAM_TEST_CHAT_ID apunta al mismo canal público que
# TELEGRAM_CHAT_ID (@nostromostroofertas), así que usarlo de reserva publicaría en abierto
# por omisión. Si no hay destino, no se envía.
_CONFIG_TOML = os.getenv("TELEGRAM_STATS_CONFIG")
if _CONFIG_TOML:
    TOKEN, CHAT_ID = credenciales_de_toml(_CONFIG_TOML)
else:
    TOKEN = os.getenv("TELEGRAM_STATS_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    CHAT_ID = os.getenv("TELEGRAM_STATS_CHAT_ID")

SEPARADOR = "━━━━━━━━━━━━━━━━━━━━━"


# --- Destino ----------------------------------------------------------------


def destino_privado(chat_id):
    """¿Puede este chat recibir las cuentas de la instancia?

    Solo un chat privado, es decir: un ID numérico que no sea ninguno de los canales
    conocidos. Los canales se declaran normalmente con @nombre, pero también se pueden
    referir por su ID -100..., así que no basta con descartar el arroba: se comparan de
    forma explícita con TELEGRAM_CHAT_ID y TELEGRAM_TEST_CHAT_ID, que apuntan los dos al
    canal público de ofertas.

    Ante la duda, False. Un bloque de menos es un fallo visible que el operador reporta;
    un bloque de más son sus ingresos publicados en abierto.
    """
    if not chat_id:
        return False
    chat_id = str(chat_id).strip()
    if not chat_id or chat_id.startswith("@"):
        return False
    publicos = {
        str(os.getenv(v, "")).strip()
        for v in ("TELEGRAM_CHAT_ID", "TELEGRAM_TEST_CHAT_ID")
    }
    publicos.discard("")
    return chat_id not in publicos


# --- Formateo ---------------------------------------------------------------


def formato_numero(valor):
    """3.0 -> '3', 1.3 -> '1.3'. Evita el '.0' en los premios enteros."""
    if float(valor) == int(valor):
        return str(int(valor))
    return str(valor)


def linea_premium(etiqueta, valor):
    """Premium con signo y emoji explícitos, como hace formato_texto() en common.py."""
    if valor is None:
        return None
    v = float(valor)
    if v > 0:
        return f"📈 <b>{etiqueta}:</b>  +{formato_numero(valor)} %"
    if v < 0:
        return f"📉 <b>{etiqueta}:</b>  {formato_numero(valor)} %"
    return f"📊 <b>{etiqueta}:</b>  0 %"


def linea_metodos(payment_methods, trades_30d):
    if not payment_methods or trades_30d < MIN_TRADES_METODOS:
        return None
    total = sum(m.get("count", 0) for m in payment_methods)
    if total == 0:
        return None
    top = [
        f"{m['method']} {round(m['count'] / total * 100)} %"
        for m in payment_methods[:3]
    ]
    return f"🏦 <b>Métodos:</b>  {' · '.join(top)}"


# --- Cuentas (solo a destino privado) ---------------------------------------


def dia_informado(hoy=None):
    """El día natural anterior al momento de ejecución.

    Lanzado por el cron de las 00:00 es el día que acaba de cerrarse, que es lo que se
    quiere informar. Lanzado a mano a media tarde sigue siendo un día completo y bien
    definido, no un trozo del actual.
    """
    return (hoy or date.today()) - timedelta(days=1)


def leer_cuentas(fecha):
    """Agregados del día y del mes al que pertenece. None si la base no está disponible.

    Que la contabilidad falle no debe costarnos también el resumen: sin base, sin bloque,
    y el resto del mensaje sale igual.
    """
    if not ACCOUNTING_DB.exists():
        return None
    try:
        con = abrir(ACCOUNTING_DB)
    except sqlite3.Error:
        return None
    try:
        return {
            "dia": consultar(con, *rango_dia(fecha)),
            # El mes es el del día informado, no el de hoy: el día 1 a las 00:00 se
            # informa del 31 anterior, y un "acumulado del mes" a cero ahí sería falso.
            "mes": consultar(con, *rango_mes(fecha.year, fecha.month)),
        }
    except sqlite3.Error:
        return None
    finally:
        con.close()


def bloque_cuentas(fecha, cuentas, precio):
    dia = cuentas["dia"]
    mes = cuentas["mes"]

    lineas = ["", SEPARADOR, f"🔒 <b>Cuentas del {fecha_larga(fecha)}</b>"]

    if dia["operaciones"] == 0:
        lineas.append("Sin operaciones.")
    else:
        neto = f"✅ <b>Ganancia neta:</b>  {formato_sats(dia['neto'])} sats"
        if precio:
            euros = dia["neto"] / 100_000_000 * float(precio)
            neto += f"  (≈ {formato_euros(euros)} €)"
        lineas += [
            neto,
            f"🤝 <b>Operaciones:</b>  {dia['operaciones']}",
            f"💵 <b>Volumen:</b>  {formato_sats(dia['volumen'])} sats",
            f"   Fee cobrado:  {formato_sats(dia['fee'])} sats",
            f"   Dev fee:  −{formato_sats(dia['dev_fee'])} sats",
            f"   Routing:  −{formato_sats(dia['routing'])} sats",
        ]

    acumulado = f"{MESES[fecha.month - 1].capitalize()} hasta hoy"
    lineas.append(
        f"\n📆 <b>{acumulado}:</b>  {formato_sats(mes['neto'])} sats"
        f"  ({mes['operaciones']} ops)"
    )
    return lineas


def construir_mensaje(datos, fecha=None, cuentas=None):
    stats = datos.get("stats", {})
    trades_24h = stats.get("trades_24h") or 0
    trades_30d = stats.get("trades_30d") or 0
    precio = stats.get("last_btc_price")

    lineas = [
        "📊 <b>Resumen · NostroMostro</b>",
        SEPARADOR,
        "",
        f"🤝 <b>Trades (24 h):</b>  {trades_24h}",
    ]

    premium_24h = linea_premium("Premium medio", stats.get("avg_premium_24h"))
    if premium_24h:
        lineas.append(premium_24h)

    if precio:
        lineas.append(f"💲 <b>BTC/EUR:</b>  {formato_sats(precio)} €")

    lineas += ["", "📅 <b>Últimos 30 días</b>", f"🤝 <b>Trades:</b>  {trades_30d}"]

    premium_30d = linea_premium("Premium medio", stats.get("avg_premium_30d"))
    if premium_30d:
        lineas.append(premium_30d)

    metodos = linea_metodos(datos.get("payment_methods"), trades_30d)
    if metodos:
        lineas.append(metodos)

    if cuentas is not None:
        lineas += bloque_cuentas(fecha, cuentas, precio)

    lineas += [
        "",
        f"🌐 {URL_MERCADO}",
        SEPARADOR,
        "🧌 <b>NostroMostro</b> — P2P sin KYC vía ⚡",
    ]

    return "\n".join(lineas)


# --- Envío ------------------------------------------------------------------


def enviar_telegram(mensaje):
    if enviar(TOKEN, CHAT_ID, mensaje) is None:
        return False
    print("✅ Resumen publicado en Telegram")
    return True


# --- Principal --------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Resumen diario de NostroMostro para Telegram")
    parser.add_argument("--dry-run", action="store_true", help="imprime el mensaje sin enviarlo")
    parser.add_argument("--force", action="store_true", help="envía aunque ya se enviara hoy")
    parser.add_argument("--file", help="usa otro premiums.json (para pruebas)")
    args = parser.parse_args()

    ruta = Path(args.file) if args.file else PREMIUMS_FILE
    if not ruta.exists():
        print(f"❌ No se encuentra {ruta}")
        return 1

    try:
        with open(ruta) as f:
            datos = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"❌ No se pudo leer {ruta}: {e}")
        return 1

    # La instancia pasa semanas sin un solo trade: publicar "0 trades" cada día sería ruido.
    trades_24h = (datos.get("stats") or {}).get("trades_24h") or 0
    if trades_24h == 0:
        print("Sin trades en las últimas 24 h, no se envía resumen")
        return 0

    hoy = datetime.now().strftime("%Y-%m-%d")
    estado = cargar_estado(ESTADO_FILE)
    if not args.force and estado.get("ultimo_envio") == hoy:
        print(f"El resumen de {hoy} ya se envió, no se repite")
        return 0

    # Las cuentas solo si el destino es privado. Sin destino resuelto todavía no se puede
    # afirmar que lo sea, así que tampoco se incluyen: es el mismo criterio que abajo, donde
    # sin CHAT_ID no se envía nada.
    fecha = dia_informado()
    cuentas = None
    if destino_privado(CHAT_ID):
        cuentas = leer_cuentas(fecha)
        if cuentas is None:
            print(f"⚠️ Contabilidad no disponible en {ACCOUNTING_DB}: resumen sin cuentas")

    mensaje = construir_mensaje(datos, fecha, cuentas)

    if args.dry_run:
        print(mensaje)
        return 0

    if not CHAT_ID:
        print("TELEGRAM_STATS_CHAT_ID sin definir: no se envía. "
              "Fíjalo al chat donde deba publicarse el resumen.")
        return 0

    if not TOKEN:
        print("❌ Falta TELEGRAM_TOKEN")
        return 1

    if not enviar_telegram(mensaje):
        return 1

    # Una sola clave que se sobreescribe: el fichero no crece.
    guardar_estado(ESTADO_FILE, {"ultimo_envio": hoy})
    return 0


if __name__ == "__main__":
    sys.exit(main())
