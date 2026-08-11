"""
bot.py — Publica ofertas de Mostro en un canal de Telegram.
Borra los mensajes cuando las ofertas son tomadas.
"""

import json
import os
import time
import requests
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from common import (
    MOSTRO_PUBKEY, RELAY,
    parsear_oferta, formato_texto, cargar_ordenes, guardar_ordenes, conectar_relay,
    obtener_pending, reconciliar
)

# --- Configuración ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCRIPT_DIR = Path(__file__).parent
ORDERS_FILE = SCRIPT_DIR / "orders.json"

ordenes_publicadas = cargar_ordenes(ORDERS_FILE)


# --- Telegram ---

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    datos = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        respuesta = requests.post(url, data=datos)
        if respuesta.status_code == 200:
            result = respuesta.json()
            message_id = result.get("result", {}).get("message_id")
            print("✅ Oferta publicada en Telegram")
            return message_id
        else:
            print(f"❌ Error de Telegram: {respuesta.text}")
    except Exception as e:
        print(f"❌ Error de conexión: {e}")
    return None


def borrar_telegram(message_id):
    """True si la oferta puede darse por retirada; False si conviene reintentarlo.

    Que Telegram responda con un error (mensaje inexistente, demasiado antiguo para
    borrarlo) también cuenta como retirada: reintentarlo en cada arranque no lo va a
    arreglar y la entrada se quedaría atascada para siempre. Solo un fallo de conexión
    justifica conservarla.
    """
    url = f"https://api.telegram.org/bot{TOKEN}/deleteMessage"
    datos = {
        "chat_id": CHAT_ID,
        "message_id": message_id
    }
    try:
        respuesta = requests.post(url, data=datos, timeout=30)
    except requests.RequestException as e:
        print(f"❌ Error de conexión borrando mensaje {message_id}: {e}")
        return False

    if respuesta.status_code == 200:
        print(f"🗑️ Oferta eliminada del canal (msg_id: {message_id})")
    else:
        print(f"⚠️ Telegram no pudo borrar {message_id}, se descarta igualmente: {respuesta.text}")
    return True


# --- Scan inicial ---

def scan_inicial(solo_simular=False):
    """Pone el canal al día con el relay: publica lo que falta y retira lo que sobra.

    Retirar es la mitad que faltaba. El bot solo borraba un mensaje al ver en directo el
    evento de cambio de estado, así que todo lo ocurrido mientras estaba parado no se
    borraba nunca; y una oferta que caduca por NIP-40 no emite ningún evento, de modo que
    en directo es indetectable por definición.
    """
    global ordenes_publicadas
    print("🔍 Reconciliando con el relay...")

    pending = obtener_pending()
    a_retirar, a_publicar = reconciliar(ordenes_publicadas, pending)

    if pending is None:
        print("⚠️ El relay no ha contestado: no se reconcilia (no se borra nada por si acaso)")
        return

    if solo_simular:
        print(f"[simulación] retiraría {len(a_retirar)} y publicaría {len(a_publicar)}")
        for order_id in a_retirar:
            print(f"  - retirar {order_id} (msg_id: {ordenes_publicadas[order_id]})")
        for order_id in a_publicar:
            print(f"  + publicar {order_id}")
        return

    retiradas = 0
    for order_id in a_retirar:
        if borrar_telegram(ordenes_publicadas[order_id]):
            del ordenes_publicadas[order_id]
            guardar_ordenes(ORDERS_FILE, ordenes_publicadas)
            retiradas += 1
            time.sleep(0.5)

    nuevas = 0
    for order_id in a_publicar:
        message_id = enviar_telegram(formato_texto(pending[order_id], html=True))
        if message_id:
            ordenes_publicadas[order_id] = message_id
            guardar_ordenes(ORDERS_FILE, ordenes_publicadas)
            nuevas += 1
            time.sleep(1)

    print(f"✅ Reconciliado: {len(pending)} pending, {nuevas} publicadas, {retiradas} retiradas")


# --- Procesar ofertas ---

def procesar_mensaje(ws, mensaje):
    global ordenes_publicadas
    try:
        datos = json.loads(mensaje)
        if datos[0] != "EVENT":
            return

        oferta = parsear_oferta(datos[2])
        if not oferta:
            return

        order_id = oferta["order_id"]
        estado = oferta["estado"]

        # Oferta tomada/cancelada: borrar del canal
        if estado != "pending" and order_id in ordenes_publicadas:
            message_id = ordenes_publicadas[order_id]
            print(f"📡 Orden {order_id[:8]}... cambió a '{estado}'")
            borrar_telegram(message_id)
            del ordenes_publicadas[order_id]
            guardar_ordenes(ORDERS_FILE, ordenes_publicadas)
            return

        # Nueva oferta pending: publicar
        if estado != "pending" or order_id in ordenes_publicadas:
            return

        texto = formato_texto(oferta, html=True)
        message_id = enviar_telegram(texto)

        if message_id:
            ordenes_publicadas[order_id] = message_id
            guardar_ordenes(ORDERS_FILE, ordenes_publicadas)

    except Exception as e:
        print(f"⚠️ Error procesando mensaje: {e}")


if __name__ == "__main__":
    # --dry-run enseña qué retiraría y qué publicaría, sin tocar el canal ni el fichero.
    simular = "--dry-run" in sys.argv
    print("🧌 Mostro Bot Telegram iniciado")
    print(f"📋 Ofertas cargadas: {len(ordenes_publicadas)}")
    scan_inicial(solo_simular=simular)
    if not simular:
        conectar_relay(procesar_mensaje)
