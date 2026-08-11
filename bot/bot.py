"""
bot.py — Publica ofertas de Mostro en un canal de Telegram.
Borra los mensajes cuando las ofertas son tomadas.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import (
    MOSTRO_PUBKEY, RELAY,
    parsear_oferta, formato_texto, conectar_relay,
    obtener_pending, reconciliar
)
from lib.entorno import cargar_env
from lib.estado import cargar_estado, guardar_estado
import lib.telegram as telegram

cargar_env()

# --- Configuración ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCRIPT_DIR = Path(__file__).parent
ORDERS_FILE = SCRIPT_DIR / "orders.json"

ordenes_publicadas = cargar_estado(ORDERS_FILE)


# --- Telegram ---

def enviar_telegram(mensaje):
    message_id = telegram.enviar(TOKEN, CHAT_ID, mensaje)
    if message_id:
        print("✅ Oferta publicada en Telegram")
    return message_id


def borrar_telegram(message_id):
    return telegram.borrar(TOKEN, CHAT_ID, message_id)


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
            guardar_estado(ORDERS_FILE, ordenes_publicadas)
            retiradas += 1
            time.sleep(0.5)

    nuevas = 0
    for order_id in a_publicar:
        message_id = enviar_telegram(formato_texto(pending[order_id], html=True))
        if message_id:
            ordenes_publicadas[order_id] = message_id
            guardar_estado(ORDERS_FILE, ordenes_publicadas)
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
            guardar_estado(ORDERS_FILE, ordenes_publicadas)
            return

        # Nueva oferta pending: publicar
        if estado != "pending" or order_id in ordenes_publicadas:
            return

        texto = formato_texto(oferta, html=True)
        message_id = enviar_telegram(texto)

        if message_id:
            ordenes_publicadas[order_id] = message_id
            guardar_estado(ORDERS_FILE, ordenes_publicadas)

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
