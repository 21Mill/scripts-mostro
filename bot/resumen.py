"""
resumen.py — Publica en Telegram el resumen agregado de la actividad de NostroMostro.

Se alimenta de data/premiums.json, el mismo fichero anonimizado que ya sirve la web, no de
la base de datos: así nada de lo que salga por aquí puede revelar algo que no esté ya
publicado, y el script no necesita acceso a mostro.db.

Lo invoca premiums.sh al final del cron diario de las 00:00.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from common import cargar_ordenes, guardar_ordenes, formato_sats

SCRIPT_DIR = Path(__file__).parent
ESTADO_FILE = SCRIPT_DIR / "resumen-enviado.json"

# Por ruta absoluta a propósito: premiums.sh hace 'cd' al repo web antes de invocarnos, así
# que un load_dotenv() sin argumentos buscaría el .env desde el directorio equivocado.
load_dotenv(SCRIPT_DIR.parent / ".env")

WEB_REPO = Path(
    os.getenv("NOSTROMOSTRO_WEB_REPO", str(Path.home() / "nostromostro.github.io"))
)
PREMIUMS_FILE = WEB_REPO / "data" / "premiums.json"

URL_MERCADO = "https://nostromostro.github.io/#mercado"

# Con menos trades que esto, el ranking describiría las operaciones una por una en vez de
# agregarlas. Por debajo del umbral la línea se omite.
MIN_TRADES_METODOS = 5

# El resumen puede ir por un bot distinto al de las ofertas. Si no se define uno propio,
# reutiliza el existente.
TOKEN = os.getenv("TELEGRAM_STATS_TOKEN") or os.getenv("TELEGRAM_TOKEN")

# Destino explícito y sin respaldo a propósito. TELEGRAM_TEST_CHAT_ID no es un chat de
# pruebas: apunta al mismo canal público que TELEGRAM_CHAT_ID (@nostromostroofertas), así
# que usarlo como respaldo publicaría en abierto por omisión. Sin esta variable no se envía.
CHAT_ID = os.getenv("TELEGRAM_STATS_CHAT_ID")

SEPARADOR = "━━━━━━━━━━━━━━━━━━━━━"


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


def construir_mensaje(datos):
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

    lineas += [
        "",
        f"🌐 {URL_MERCADO}",
        SEPARADOR,
        "🧌 <b>NostroMostro</b> — P2P sin KYC vía ⚡",
    ]

    return "\n".join(lineas)


# --- Envío ------------------------------------------------------------------


def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    datos = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        respuesta = requests.post(url, data=datos, timeout=30)
        if respuesta.status_code == 200:
            print("✅ Resumen publicado en Telegram")
            return True
        print(f"❌ Error de Telegram: {respuesta.text}")
    except requests.RequestException as e:
        print(f"❌ Error de conexión con Telegram: {e}")
    return False


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
    estado = cargar_ordenes(ESTADO_FILE)
    if not args.force and estado.get("ultimo_envio") == hoy:
        print(f"El resumen de {hoy} ya se envió, no se repite")
        return 0

    mensaje = construir_mensaje(datos)

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
    guardar_ordenes(ESTADO_FILE, {"ultimo_envio": hoy})
    return 0


if __name__ == "__main__":
    sys.exit(main())
