"""
test-reconciliacion.py — Comprueba la lógica de reconciliación de los bots.

Ejecutar: python3 test-reconciliacion.py

Sin dependencias ni framework: el repo no tiene infraestructura de tests y esto debe poder
lanzarse tal cual en el servidor.
"""

import sys

from common import reconciliar

fallos = []


def comprobar(descripcion, obtenido, esperado):
    if obtenido == esperado:
        print(f"  ✅ {descripcion}")
    else:
        print(f"  ❌ {descripcion}\n       esperado: {esperado}\n       obtenido: {obtenido}")
        fallos.append(descripcion)


print("reconciliar(publicadas, pending)\n")

# El caso que motivó todo esto: la instancia lleva meses acumulando entradas de ofertas
# que ya no existen, y sus mensajes siguen visibles en el canal.
comprobar(
    "retira las entradas cuya oferta ya no está pending",
    reconciliar({"viva": 1, "muerta": 2}, {"viva": {}}),
    (["muerta"], []),
)

comprobar(
    "publica las ofertas pending que aún no se han publicado",
    reconciliar({}, {"nueva": {}}),
    ([], ["nueva"]),
)

comprobar(
    "no toca nada cuando el estado ya coincide",
    reconciliar({"viva": 1}, {"viva": {}}),
    ([], []),
)

comprobar(
    "retira y publica en la misma pasada",
    reconciliar({"muerta": 1}, {"nueva": {}}),
    (["muerta"], ["nueva"]),
)

# La lección del fallo de p2p.band: un relay caído devuelve vacío, y confundir eso con
# "no hay ofertas" borraría el canal entero. None significa "no me han contestado".
comprobar(
    "NO retira nada si el relay no contestó (None), aunque haya entradas",
    reconciliar({"viva": 1, "otra": 2}, None),
    ([], []),
)

# Distinto de lo anterior: el relay sí contestó y de verdad no queda ninguna pending.
comprobar(
    "sí retira todo si el relay contestó y no queda ninguna pending",
    reconciliar({"muerta": 1, "otra": 2}, {}),
    (["muerta", "otra"], []),
)

print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallida(s)")
    sys.exit(1)
print("Todas las pruebas pasan")
