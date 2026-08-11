"""
lib — Piezas compartidas por los scripts de Python del repositorio.

Aquí vive lo que no pertenece a ningún script en concreto: la carga del .env, el envío por
Telegram, el formateo de cifras y la persistencia en JSON. Antes estaba copiado en cada
fichero, con las copias divergiendo entre sí.

Deliberadamente sin nada de Nostr: bot/common.py sigue siendo el módulo del relay. Los
scripts de contabilidad se ejecutan desde cron y no deben arrastrar websocket ni pynostr
solo para poder formatear un número.

Los scripts de los subdirectorios lo importan añadiendo la raíz del repo a sys.path:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lib.formato import formato_sats
"""
