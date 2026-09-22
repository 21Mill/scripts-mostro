"""
test-reconciliacion.py — Comprueba la lógica de reconciliación de los bots.

Ejecutar: python3 test-reconciliacion.py

Sin dependencias ni framework: el repo no tiene infraestructura de tests y esto debe poder
lanzarse tal cual en el servidor.
"""

import sys

from common import filtro_novedades, pending_de, reconciliar

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



def evento(order_id, estado, created_at, event_id=None, expiration=None):
    tags = [["d", order_id], ["s", estado]]
    if expiration is not None:
        tags.append(["expiration", str(expiration)])
    return {"id": event_id or f"{order_id}-{estado}-{created_at}", "created_at": created_at, "tags": tags}


print("\npending_de(eventos) — mezcla de varios relays\n")

comprobar(
    "se queda con las pending",
    sorted(pending_de([evento("a", "pending", 10), evento("b", "canceled", 10)], ahora=0)),
    ["a"],
)

# Un relay atrasado sigue dando pending lo que otro ya tiene cancelado.
comprobar(
    "gana la versión más reciente aunque venga de otro relay",
    pending_de([evento("a", "pending", 10), evento("a", "canceled", 20)], ahora=0),
    {},
)

comprobar(
    "el orden de llegada no importa",
    pending_de([evento("a", "canceled", 20), evento("a", "pending", 10)], ahora=0),
    {},
)

comprobar(
    "descarta las vencidas por NIP-40 aunque un relay no las haya purgado",
    sorted(pending_de([evento("a", "pending", 10, expiration=100),
                       evento("b", "pending", 10, expiration=300)], ahora=200)),
    ["b"],
)

print("\nfiltro_novedades() — eventos del directo\n")

es_nuevo = filtro_novedades()
comprobar("deja pasar un evento nuevo", es_nuevo(evento("a", "pending", 10)), True)
comprobar("descarta el mismo evento llegado por otro relay", es_nuevo(evento("a", "pending", 10)), False)
comprobar("deja pasar el cambio de estado posterior", es_nuevo(evento("a", "canceled", 20)), True)
# Sin esto, el pending atrasado de un relay lento volvería a publicar la oferta cancelada.
comprobar("descarta un evento más viejo que el último visto", es_nuevo(evento("a", "pending", 15)), False)
comprobar("no mezcla órdenes distintas", es_nuevo(evento("b", "pending", 5)), True)

print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallida(s)")
    sys.exit(1)
print("Todas las pruebas pasan")
