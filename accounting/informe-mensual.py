"""
informe-mensual.py — Informe financiero mensual de NostroMostro para Telegram.

Agrega accounting.db —una fila por operación liquidada, con el beneficio neto ya
calculado por accounting.py— y manda un único mensaje al chat privado del operador.

Solo lee: abre la base en modo ro para no poder interferir con mostro-accounting.service,
que le está escribiendo mientras tanto.

Lo lanza cron el día 1 de cada mes a las 00:30, informando del mes recién cerrado.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from lib.contabilidad import abrir, consultar, rango_mes
from lib.entorno import cargar_env
from lib.estado import cargar_estado, guardar_estado
from lib.formato import MESES, con_signo, formato_euros, formato_sats
from lib.telegram import enviar

ACCOUNTING_DB = SCRIPT_DIR / "accounting.db"
ESTADO_FILE = SCRIPT_DIR / "informe-mensual-enviado.json"

# Ruta absoluta a propósito: cron ejecuta desde $HOME, no desde aquí, y un load_dotenv()
# relativo no encontraría el fichero ni daría error al no encontrarlo.
cargar_env()

WEB_REPO = Path(
    os.getenv("NOSTROMOSTRO_WEB_REPO", str(Path.home() / "nostromostro.github.io"))
)
PREMIUMS_FILE = WEB_REPO / "data" / "premiums.json"

# Mismo destino que el resumen diario: el bot privado del operador. Sin respaldo a
# propósito — TELEGRAM_CHAT_ID es el canal público de ofertas, y caer ahí por omisión
# publicaría las cuentas de la instancia en abierto. Sin destino no se envía.
TOKEN = os.getenv("TELEGRAM_STATS_TOKEN") or os.getenv("TELEGRAM_MONITOR_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_STATS_CHAT_ID") or os.getenv("TELEGRAM_MONITOR_CHAT_ID")

SEPARADOR = "━━━━━━━━━━━━━━━━━━━━━"


# --- Datos ------------------------------------------------------------------


def consultar_mes(con, anio, mes):
    return consultar(con, *rango_mes(anio, mes))


def precio_btc():
    """BTC/EUR del JSON que ya genera premiums.sh. None si no está disponible."""
    try:
        with open(PREMIUMS_FILE) as f:
            return (json.load(f).get("stats") or {}).get("last_btc_price")
    except (IOError, OSError, json.JSONDecodeError):
        return None


def mes_anterior(anio, mes):
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


# --- Mensaje ----------------------------------------------------------------


def linea_comparativa(neto, neto_previo, anio_previo, mes_previo):
    etiqueta = f"{MESES[mes_previo - 1]}: {formato_sats(neto_previo)} sats"
    diff = neto - neto_previo

    if neto_previo == 0:
        # Sin porcentaje: dividir por cero no da "infinito por ciento", da un dato falso.
        variacion = f"{con_signo(diff)} sats"
    else:
        pct = diff / abs(neto_previo) * 100
        variacion = f"{con_signo(diff)} sats ({f'{pct:+.0f}'.replace('-', '−')} %)"

    emoji = "📈" if diff > 0 else ("📉" if diff < 0 else "➖")
    return f"{emoji} <b>vs. mes anterior:</b>  {variacion}\n<i>({etiqueta})</i>"


def construir_mensaje(anio, mes, datos, previo, precio):
    titulo = f"🧾 <b>Informe mensual · {MESES[mes - 1]} {anio}</b>"
    lineas = [titulo, SEPARADOR, ""]

    if datos["operaciones"] == 0:
        lineas.append("Sin operaciones este mes.")
    else:
        neto = f"✅ <b>Ganancia neta:</b>  {formato_sats(datos['neto'])} sats"
        if precio:
            euros = datos["neto"] / 100_000_000 * float(precio)
            neto += f"  (≈ {formato_euros(euros)} €)"
        lineas += [
            neto,
            f"🤝 <b>Operaciones:</b>  {datos['operaciones']}",
            f"💵 <b>Volumen:</b>  {formato_sats(datos['volumen'])} sats",
            "",
            "📊 <b>Desglose</b>",
            f"   Fee cobrado:  {formato_sats(datos['fee'])} sats",
            f"   Dev fee:  −{formato_sats(datos['dev_fee'])} sats",
            f"   Routing:  −{formato_sats(datos['routing'])} sats",
        ]

    prev_anio, prev_mes = mes_anterior(anio, mes)
    if previo is not None:
        lineas += ["", linea_comparativa(datos["neto"], previo["neto"], prev_anio, prev_mes)]

    lineas += [SEPARADOR, "🧌 <b>NostroMostro</b>"]
    return "\n".join(lineas)


# --- Envío ------------------------------------------------------------------


def enviar_telegram(mensaje):
    if enviar(TOKEN, CHAT_ID, mensaje) is None:
        return False
    print("✅ Informe mensual enviado")
    return True


# --- Principal --------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Informe financiero mensual de NostroMostro")
    parser.add_argument("--mes", help="mes a informar en formato YYYY-MM (por defecto, el anterior)")
    parser.add_argument("--dry-run", action="store_true", help="imprime el mensaje sin enviarlo")
    parser.add_argument("--force", action="store_true", help="envía aunque ya se enviara ese mes")
    args = parser.parse_args()

    if args.mes:
        try:
            objetivo = datetime.strptime(args.mes, "%Y-%m")
        except ValueError:
            print(f"❌ Mes inválido: {args.mes}. Formato esperado: YYYY-MM")
            return 1
        anio, mes = objetivo.year, objetivo.month
    else:
        hoy = datetime.now()
        anio, mes = mes_anterior(hoy.year, hoy.month)

    clave = f"{anio:04d}-{mes:02d}"

    if not ACCOUNTING_DB.exists():
        print(f"❌ No se encuentra {ACCOUNTING_DB}")
        return 1

    estado = cargar_estado(ESTADO_FILE)
    if not args.force and not args.dry_run and estado.get("ultimo_envio") == clave:
        print(f"El informe de {clave} ya se envió, no se repite")
        return 0

    con = abrir(ACCOUNTING_DB)
    try:
        datos = consultar_mes(con, anio, mes)
        prev_anio, prev_mes = mes_anterior(anio, mes)
        previo = consultar_mes(con, prev_anio, prev_mes)
        # Sin datos previos no hay comparativa que hacer: el primer mes de la contabilidad
        # tendría un mes anterior a cero que no significa "no se ganó nada", sino "no había
        # instancia". Distinguirlo es el motivo de mirar si existe alguna fila anterior.
        inicio, _ = rango_mes(anio, mes)
        hay_historial = con.execute(
            "SELECT COUNT(*) FROM earnings WHERE completed_at < ?", (inicio,)
        ).fetchone()[0] > 0
    finally:
        con.close()

    mensaje = construir_mensaje(anio, mes, datos, previo if hay_historial else None, precio_btc())

    if args.dry_run:
        print(mensaje)
        return 0

    if not CHAT_ID:
        print("TELEGRAM_STATS_CHAT_ID sin definir: no se envía.")
        return 0
    if not TOKEN:
        print("❌ Falta TELEGRAM_STATS_TOKEN")
        return 1

    if not enviar_telegram(mensaje):
        return 1

    # Una sola clave que se sobreescribe: el fichero no crece.
    guardar_estado(ESTADO_FILE, {"ultimo_envio": clave})
    return 0


if __name__ == "__main__":
    sys.exit(main())
