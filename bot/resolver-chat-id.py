"""
resolver-chat-id.py — Averigua el chat_id donde debe publicarse el resumen.

Un bot de Telegram no puede escribir en cualquier sitio: solo en chats donde alguien le
haya dado a «Iniciar», o en grupos y canales donde sea administrador. Este script consulta
qué chats ha visto el bot y muestra sus identificadores, para copiar el que corresponda a
TELEGRAM_STATS_CHAT_ID.

Uso:
    1. Pon el token en scripts/.env como TELEGRAM_STATS_TOKEN (o deja que use el de las
       ofertas, TELEGRAM_TOKEN, si es el mismo bot).
    2. Escribe algo al bot desde tu cuenta, o publica un mensaje en el canal destino.
    3. python3 resolver-chat-id.py

Nota: getUpdates solo devuelve lo reciente y no funciona si el bot tiene un webhook activo.
"""

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.entorno import cargar_env

cargar_env()

TOKEN = os.getenv("TELEGRAM_STATS_TOKEN") or os.getenv("TELEGRAM_TOKEN")


def main():
    if not TOKEN:
        print("❌ No hay token: define TELEGRAM_STATS_TOKEN o TELEGRAM_TOKEN en .env")
        return 1

    yo = requests.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=20).json()
    if not yo.get("ok"):
        print(f"❌ El token no es válido: {yo.get('description')}")
        return 1
    print(f"Bot: @{yo['result'].get('username')}\n")

    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=20).json()
    if not r.get("ok"):
        print(f"❌ getUpdates falló: {r.get('description')}")
        return 1

    vistos = {}
    for upd in r.get("result", []):
        for clave in ("message", "channel_post", "edited_message", "my_chat_member"):
            chat = (upd.get(clave) or {}).get("chat")
            if chat:
                vistos[chat["id"]] = chat

    if not vistos:
        print("No he visto ningún chat todavía.")
        print("Escríbele algo al bot (o publica en el canal donde es administrador)")
        print("y vuelve a ejecutar esto.")
        return 0

    print("Chats disponibles:\n")
    for cid, chat in vistos.items():
        nombre = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        usuario = f"  @{chat['username']}" if chat.get("username") else ""
        print(f"  chat_id = {cid}")
        print(f"      tipo: {chat.get('type')}   nombre: {nombre}{usuario}\n")

    print("Copia el que quieras a scripts/.env:")
    print("  TELEGRAM_STATS_CHAT_ID=<el id de arriba>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
