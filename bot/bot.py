"""
bot.py — Publica ofertas de Mostro en un canal de Telegram.
Borra los mensajes cuando las ofertas son tomadas.
"""

import json
import os
import time
import requests
import websocket as ws_client
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from common import (
    MOSTRO_PUBKEY, RELAY,
    parsear_oferta, formato_texto, cargar_ordenes, guardar_ordenes, conectar_relay
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
    url = f"https://api.telegram.org/bot{TOKEN}/deleteMessage"
    datos = {
        "chat_id": CHAT_ID,
        "message_id": message_id
    }
    try:
        respuesta = requests.post(url, data=datos)
        if respuesta.status_code == 200:
            print(f"🗑️ Oferta eliminada del canal (msg_id: {message_id})")
            return True
        else:
            print(f"⚠️ No se pudo borrar mensaje {message_id}: {respuesta.text}")
    except Exception as e:
        print(f"❌ Error borrando mensaje: {e}")
    return False


# --- Scan inicial ---

def scan_inicial():
    """Escanea el relay para publicar órdenes pending que el bot no haya visto."""
    global ordenes_publicadas
    print("🔍 Escaneando órdenes pending existentes...")
    try:
        ws = ws_client.create_connection(RELAY, timeout=15)
        ws.send(json.dumps(["REQ", "scan", {
            "kinds": [38383],
            "authors": [MOSTRO_PUBKEY],
            "since": int(time.time()) - 86400
        }]))

        pending = []
        for _ in range(500):
            resp = json.loads(ws.recv())
            if resp[0] == "EVENT":
                oferta = parsear_oferta(resp[2])
                if oferta and oferta["estado"] == "pending":
                    pending.append(oferta)
            if resp[0] == "EOSE":
                break
        ws.close()

        nuevas = 0
        for oferta in pending:
            order_id = oferta["order_id"]
            if order_id not in ordenes_publicadas:
                texto = formato_texto(oferta, html=True)
                message_id = enviar_telegram(texto)
                if message_id:
                    ordenes_publicadas[order_id] = message_id
                    guardar_ordenes(ORDERS_FILE, ordenes_publicadas)
                    nuevas += 1
                    time.sleep(1)

        print(f"✅ Scan completado: {len(pending)} pending, {nuevas} nuevas publicadas")
    except Exception as e:
        print(f"⚠️ Error en scan inicial: {e}")


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
    print("🧌 Mostro Bot Telegram iniciado")
    print(f"📋 Ofertas cargadas: {len(ordenes_publicadas)}")
    scan_inicial()
    conectar_relay(procesar_mensaje)
