"""
test-formato.py — Comprueba el parseo de un evento de oferta y el texto que se publica.

Ejecutar: python3 test-formato.py

Sin dependencias ni framework, igual que test-reconciliacion.py: el repo no tiene
infraestructura de tests y esto debe poder lanzarse tal cual en el servidor.

Lo que más se vigila aquí es el enlace a la orden. Sale de la etiqueta 'source' que escribe
el demonio, y el bot no verifica firmas —lee JSON crudo del relay y se fía del filtro
'authors'—, así que un relay hostil podría colar un evento con el autor falseado. De ahí los
casos de enlace ajeno: lo que se publica en un canal público no puede apuntar a donde
quiera el relay.
"""

import os
import sys

# Antes del import de common, que las lee al cargarse. load_dotenv no pisa lo que ya esté
# en el entorno, así que esto vale igual aquí que en el nodo, donde sí hay .env.
NODO = "0000cc02101ec29eea9ce623258752b9d7da66c27845ed26846dd0b0fc736b40"
OTRO_NODO = "82fa8cb978b43c79b2156585bac2c011176a21d2aead6d9f7c575c005be88390"
os.environ["MOSTRO_PUBKEY"] = NODO
os.environ["MOSTRO_WEB_APP"] = "https://mostro.network/app/#/order_link"

from common import formato_texto, parsear_oferta

ORDEN = "1d957da0-bb7c-4700-b91c-fafaeff250ea"
OTRA_ORDEN = "96097d10-d6e3-409c-89bf-2d417db26cee"
RELAYS = "wss://relay.mostro.network,wss://nos.lol"

SOURCE = f"mostro:{ORDEN}?relays={RELAYS}&mostro={NODO}"

# Escrito entero y a mano a propósito: si se calculase con quote() el test repetiría la
# implementación en vez de comprobarla.
ENLACE = (
    "https://mostro.network/app/#/order_link?link="
    "mostro%3A1d957da0-bb7c-4700-b91c-fafaeff250ea"
    "%3Frelays%3Dwss%3A%2F%2Frelay.mostro.network%2Cwss%3A%2F%2Fnos.lol"
    "%26mostro%3D0000cc02101ec29eea9ce623258752b9d7da66c27845ed26846dd0b0fc736b40"
)

fallos = []


def comprobar(descripcion, obtenido, esperado):
    if obtenido == esperado:
        print(f"  ✅ {descripcion}")
    else:
        print(f"  ❌ {descripcion}\n       esperado: {esperado}\n       obtenido: {obtenido}")
        fallos.append(descripcion)


def evento(source=SOURCE, order_id=ORDEN, metodo="BIZUM"):
    """Un kind 38383 mínimo pero realista. source=None lo deja fuera."""
    tags = [
        ["d", order_id],
        ["s", "pending"],
        ["k", "buy"],
        ["f", "EUR"],
        ["amt", "13755"],
        ["fa", "10"],
        ["pm", metodo],
        ["premium", "0"],
    ]
    if source is not None:
        tags.append(["source", source])
    return {"created_at": 1760659200, "tags": tags}


print("parsear_oferta: el enlace de la orden\n")

comprobar(
    "un source válido se convierte en la URL del cliente web",
    parsear_oferta(evento())["enlace"],
    ENLACE,
)

comprobar(
    "sin etiqueta source no hay enlace (nodo anterior a mostrod 0.19.0)",
    parsear_oferta(evento(source=None))["enlace"],
    "",
)

# Los dos casos que justifican validar en vez de confiar.
comprobar(
    "un source de otro nodo se descarta",
    parsear_oferta(evento(source=f"mostro:{ORDEN}?relays={RELAYS}&mostro={OTRO_NODO}"))["enlace"],
    "",
)

comprobar(
    "un source de otra orden se descarta",
    parsear_oferta(evento(source=f"mostro:{OTRA_ORDEN}?relays={RELAYS}&mostro={NODO}"))["enlace"],
    "",
)

comprobar(
    "un source sin mostro= se descarta: no se da por supuesto que sea nuestro",
    parsear_oferta(evento(source=f"mostro:{ORDEN}?relays={RELAYS}"))["enlace"],
    "",
)

comprobar(
    "un source que no es un enlace de orden se descarta",
    parsear_oferta(evento(source="https://ejemplo.invalido/phishing"))["enlace"],
    "",
)

# El parser de la app recorre los pares de la query y reasigna el nodo en cada 'mostro=',
# así que gana el último. Comprobar que nuestra clave aparece "en algún sitio" dejaba pasar
# esto, y el enlace publicado habría llevado al lector al nodo del atacante.
comprobar(
    "un source con dos mostro= se descarta: en la app ganaría el último",
    parsear_oferta(evento(source=f"mostro:{ORDEN}?mostro={NODO}&mostro={OTRO_NODO}"))["enlace"],
    "",
)

comprobar(
    "un source con el id en el host (mostro://) se descarta: no es lo que escribe el demonio",
    parsear_oferta(evento(source=f"mostro://{ORDEN}?relays={RELAYS}&mostro={NODO}"))["enlace"],
    "",
)


print("\nformato_texto: cómo sale el enlace en cada canal\n")

oferta = parsear_oferta(evento())
sin_enlace = parsear_oferta(evento(source=None))

comprobar(
    "en Telegram el enlace va dentro de un ancla HTML",
    f'<a href="{ENLACE}">' in formato_texto(oferta, html=True),
    True,
)

comprobar(
    "en Nostr va la URL pelada, que los clientes enlazan solos",
    ENLACE in formato_texto(oferta, html=False),
    True,
)

comprobar(
    "en Nostr no se cuela nada de HTML",
    "<a href=" in formato_texto(oferta, html=False),
    False,
)

comprobar(
    "sin enlace, el mensaje de Telegram no lleva ninguna línea de enlace",
    "Abrir en Mostro" in formato_texto(sin_enlace, html=True),
    False,
)

comprobar(
    "sin enlace, el mensaje de Nostr tampoco",
    "order_link" in formato_texto(sin_enlace, html=False),
    False,
)


print("\nformato_texto: lo que ya funcionaba y no debe romperse\n")

# Un vendedor puso un '<' en el método de pago y la API de Telegram rechazó el mensaje
# entero ("can't parse entities"), así que la oferta no llegó a publicarse.
con_signo = parsear_oferta(evento(metodo="PAYPAL <FAMILY>"))

comprobar(
    "un '<' en el método de pago se escapa en HTML",
    "&lt;FAMILY&gt;" in formato_texto(con_signo, html=True),
    True,
)

comprobar(
    "pero en texto plano se deja tal cual, para que el lector lo vea",
    "<FAMILY>" in formato_texto(con_signo, html=False),
    True,
)

comprobar(
    "el id de la orden sigue apareciendo en el mensaje",
    ORDEN in formato_texto(oferta, html=True),
    True,
)


print()
if fallos:
    print(f"❌ {len(fallos)} comprobación(es) fallida(s)")
    sys.exit(1)
print("✅ Todo en verde")
