"""
mostro_bot_nostr.py — Publica ofertas de Mostro como notas en Nostr.
Borra las notas cuando las ofertas son tomadas (NIP-09).
"""

import json
import os
import time
import websocket as ws_client
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from mostro_common import (
    parsear_oferta, formato_texto, cargar_ordenes, guardar_ordenes, conectar_relay
)

# --- Configuración ---
NOSTR_NSEC = os.getenv("NOSTR_BOT_NSEC", "")
NOSTR_RELAYS = os.getenv("NOSTR_BOT_RELAYS", "wss://relay.damus.io,wss://nos.lol,wss://relay.mostro.network")

SCRIPT_DIR = Path(__file__).parent
ORDERS_FILE = SCRIPT_DIR / "orders_nostr.json"

from pynostr.key import PrivateKey
from pynostr.event import Event


def init_keys():
    global NOSTR_NSEC
    if not NOSTR_NSEC:
        print("🔑 No se encontró NOSTR_BOT_NSEC, generando claves nuevas...")
        pk = PrivateKey()
        NOSTR_NSEC = pk.bech32()
        env_file = SCRIPT_DIR / ".env"
        with open(env_file, "a") as f:
            f.write(f"\n# --- Nostr Bot (generado automáticamente) ---\n")
            f.write(f"NOSTR_BOT_NSEC={NOSTR_NSEC}\n")
        print(f"✅ Claves generadas y guardadas en .env")
        print(f"   npub: {pk.public_key.bech32()}")
        return pk
    else:
        return PrivateKey.from_nsec(NOSTR_NSEC)


private_key = init_keys()
ordenes_publicadas = cargar_ordenes(ORDERS_FILE)
relay_list = [r.strip() for r in NOSTR_RELAYS.split(",") if r.strip()]


def publicar_evento(evento_dict):
    """Publica un evento en todos los relays configurados."""
    event_id = evento_dict.get("id")
    for relay_url in relay_list:
        try:
            ws = ws_client.create_connection(relay_url, timeout=10)
            ws.send(json.dumps(["EVENT", evento_dict]))
            resp = ws.recv()
            ws.close()
            data = json.loads(resp)
            if data[0] == "OK" and data[2]:
                print(f"  ✅ Publicado en {relay_url}")
            else:
                print(f"  ⚠️ Rechazado por {relay_url}: {data}")
        except Exception as e:
            print(f"  ❌ Error en {relay_url}: {e}")
    return event_id


def crear_evento(kind, content, tags=None):
    """Crea y firma un evento Nostr."""
    event = Event(
        pubkey=private_key.public_key.hex(),
        created_at=int(time.time()),
        kind=kind,
        tags=tags or [],
        content=content,
    )
    event.sign(private_key.hex())
    return event.to_dict()


def publicar_nota(texto):
    """Publica una nota kind 1."""
    evento = crear_evento(1, texto)
    print(f"📝 Publicando nota en {len(relay_list)} relays...")
    event_id = publicar_evento(evento)
    return event_id


def borrar_nota(event_id):
    """Publica un evento kind 5 (NIP-09) para borrar una nota."""
    tags = [["e", event_id]]
    evento = crear_evento(5, "Oferta tomada", tags)
    print(f"🗑️ Borrando nota {event_id[:12]}... en {len(relay_list)} relays...")
    publicar_evento(evento)


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

        if estado != "pending" and order_id in ordenes_publicadas:
            event_id = ordenes_publicadas[order_id]
            print(f"📡 Orden {order_id[:8]}... cambió a '{estado}'")
            borrar_nota(event_id)
            del ordenes_publicadas[order_id]
            guardar_ordenes(ORDERS_FILE, ordenes_publicadas)
            return

        if estado != "pending" or order_id in ordenes_publicadas:
            return

        texto = formato_texto(oferta, html=False)
        event_id = publicar_nota(texto)

        if event_id:
            ordenes_publicadas[order_id] = event_id
            guardar_ordenes(ORDERS_FILE, ordenes_publicadas)

    except Exception as e:
        print(f"⚠️ Error procesando mensaje: {e}")


if __name__ == "__main__":
    print("🧌 Mostro Bot Nostr iniciado")
    print(f"🔑 npub: {private_key.public_key.bech32()}")
    print(f"📡 Relays: {', '.join(relay_list)}")
    print(f"📋 Ofertas cargadas: {len(ordenes_publicadas)}")
    conectar_relay(procesar_mensaje)
