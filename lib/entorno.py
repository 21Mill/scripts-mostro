"""
entorno.py — Localiza y carga el .env del repositorio.

Por ruta absoluta a propósito, y una sola vez para todos los scripts. Se ejecutan desde
directorios muy distintos —systemd con su WorkingDirectory, cron desde $HOME, a mano desde
cualquier sitio— y un load_dotenv() sin argumentos busca desde el directorio equivocado: la
configuración se queda vacía sin dar ningún error, que es la peor forma de fallar. En
bot-nostr.py llegó a costar una identidad Nostr nueva en cada arranque.
"""

from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"


def cargar_env():
    """Carga el .env del repositorio. Idempotente: repetirla no pisa lo ya cargado."""
    load_dotenv(ENV_FILE)
    return ENV_FILE
