"""
estado.py — Persistencia en JSON del estado de los scripts.

Lo usan los bots para recordar qué ofertas tienen publicadas y los informes para no
repetirse. Fallar en silencio al leer es deliberado: un fichero corrupto o inexistente
significa "no hay estado previo", que es exactamente lo que representa el diccionario
vacío. Al escribir sí se avisa, porque perder el estado sí tiene consecuencias.
"""

import json
from pathlib import Path


def cargar_estado(archivo):
    try:
        if Path(archivo).exists():
            with open(archivo, "r") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def guardar_estado(archivo, datos):
    try:
        with open(archivo, "w") as f:
            json.dump(datos, f)
    except IOError as e:
        print(f"⚠️ Error guardando {archivo}: {e}")
