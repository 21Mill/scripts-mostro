"""
test-firma.py — Comprueba que solo pasan los eventos que firmó de verdad nuestro nodo.

Ejecutar: python3 test-firma.py

Sin dependencias ni framework, igual que los demás, salvo coincurve, que es la que verifica
y sin la cual common.py no arranca.

La semilla es un kind 38383 **real** de nuestra instancia, capturado del relay y escrito
aquí entero. Tiene que ser auténtico: es la única forma de comprobar que la serialización
del id es la que manda NIP-01, cosa que ningún evento inventado demuestra. Y se eligió uno
con emoji y acentos en un método de pago a propósito, porque ahí es donde se nota si el
ensure_ascii=False falta. La oferta hace tiempo que caducó; la fixtura no.

Cada caso parte de esa semilla y le cambia una cosa. Ninguno debe pasar.
"""

import copy
import json
import os
import sys

os.environ["MOSTRO_PUBKEY"] = "0000cc02101ec29eea9ce623258752b9d7da66c27845ed26846dd0b0fc736b40"

from common import _solo_autenticos, evento_autentico

SEMILLA = json.loads('''{
  "content": "",
  "created_at": 1791394637,
  "id": "0663f92d4d046ca24c64214e6450d8f18a9f80d9ac92bbfe7af4d22222463c86",
  "kind": 38383,
  "pubkey": "0000cc02101ec29eea9ce623258752b9d7da66c27845ed26846dd0b0fc736b40",
  "sig": "a3773c5463023ea96afbb54487866054982e45883d998e1b28a1ea84117c81ead73412ae8bb8d90b7d7d490b37b19a631a31494316c5511610a94f50c07d7a51",
  "tags": [
    [
      "d",
      "d4d56230-2bfa-45d4-9209-fbe9cb7e7ad3"
    ],
    [
      "k",
      "sell"
    ],
    [
      "f",
      "EUR"
    ],
    [
      "s",
      "canceled"
    ],
    [
      "amt",
      "0"
    ],
    [
      "fa",
      "0"
    ],
    [
      "pm",
      "(yonij🎯) Halcash (Banco Popular Bankinter Cajamar Caja Laboral ABANCA y Evo) o equivalentes Efectivo a Cajero de cualquier otro banco (Santander Caixabank BBVA Sabadell...) al número del comprador o al MÍO"
    ],
    [
      "premium",
      "3"
    ],
    [
      "network",
      "mainnet"
    ],
    [
      "layer",
      "lightning"
    ],
    [
      "published_at",
      "1791380353"
    ],
    [
      "expires_at",
      "1791466753"
    ],
    [
      "expiration",
      "1793986637"
    ],
    [
      "y",
      "mostro",
      "NostroMostro 🇪🇸"
    ],
    [
      "z",
      "order"
    ]
  ]
}''')

fallos = []


def comprobar(descripcion, obtenido, esperado):
    if obtenido == esperado:
        print(f"  ✅ {descripcion}")
    else:
        print(f"  ❌ {descripcion}\n       esperado: {esperado}\n       obtenido: {obtenido}")
        fallos.append(descripcion)


def mutado(**cambios):
    ev = copy.deepcopy(SEMILLA)
    ev.update(cambios)
    return ev


def sin(clave):
    ev = copy.deepcopy(SEMILLA)
    del ev[clave]
    return ev


print("evento_autentico\n")

comprobar(
    "un evento real de nuestro nodo se acepta",
    evento_autentico(copy.deepcopy(SEMILLA)),
    True,
)


print("\nel id se recalcula: cambiar los datos invalida la firma\n")

comprobar(
    "un carácter de más en el content",
    evento_autentico(mutado(content=SEMILLA["content"] + "x")),
    False,
)

etiquetas = copy.deepcopy(SEMILLA["tags"])
for t in etiquetas:
    if t[0] == "premium":
        t[1] = "99"
comprobar(
    "el premium cambiado en una etiqueta",
    evento_autentico(mutado(tags=etiquetas)),
    False,
)

# La etiqueta con emoji y acentos: si el id se calculase escapando el no-ASCII, este evento
# no se distinguiría del auténtico porque ninguno de los dos cuadraría.
etiquetas = copy.deepcopy(SEMILLA["tags"])
for t in etiquetas:
    if t[0] == "pm":
        t[1] = t[1].replace("🎯", "🎲")
comprobar(
    "un emoji distinto en el método de pago",
    evento_autentico(mutado(tags=etiquetas)),
    False,
)

comprobar(
    "la fecha del evento cambiada",
    evento_autentico(mutado(created_at=SEMILLA["created_at"] + 1)),
    False,
)

# Si el kind llega como cadena, la serialización difiere y el id ya no cuadra. Rechazarlo
# es lo correcto: un evento con los tipos cambiados no es el que se firmó.
comprobar(
    "el kind como cadena en vez de entero",
    evento_autentico(mutado(kind=str(SEMILLA["kind"]))),
    False,
)

# Lo que la comprobación del id existe para parar: id y firma auténticos, datos cambiados.
comprobar(
    "un par id/firma legítimo con el contenido cambiado",
    evento_autentico(mutado(content="oferta falsa")),
    False,
)


print("\nel autor y la firma\n")

otro = "82fa8cb978b43c79b2156585bac2c011176a21d2aead6d9f7c575c005be88390"
comprobar(
    "firmado por otro nodo",
    evento_autentico(mutado(pubkey=otro)),
    False,
)

sig_tocada = ("f" if SEMILLA["sig"][0] != "f" else "0") + SEMILLA["sig"][1:]
comprobar(
    "la firma con un carácter cambiado",
    evento_autentico(mutado(sig=sig_tocada)),
    False,
)

comprobar(
    "una firma de longitud imposible",
    evento_autentico(mutado(sig="00")),
    False,
)


print("\neventos malformados: sin beneficio de la duda\n")

for clave in ["sig", "id", "pubkey", "tags", "content", "created_at", "kind"]:
    comprobar(f"sin la clave {clave}", evento_autentico(sin(clave)), False)

comprobar("un id que no es hexadecimal", evento_autentico(mutado(id="zz" * 32)), False)
comprobar("un evento vacío", evento_autentico({}), False)
comprobar("algo que no es un evento", evento_autentico(None), False)


print("\nun relay que solo manda basura no cuenta como que ha contestado\n")

bueno = copy.deepcopy(SEMILLA)
malo = mutado(content="oferta falsa")

# Lo que protege de vaciar el canal: si esto devolviera [], obtener_pending vería cero
# ofertas pending y reconciliar retiraría todos los mensajes publicados.
comprobar(
    "todos inválidos: se trata como si el relay no hubiera contestado",
    _solo_autenticos("wss://relay.falso", [malo, malo]),
    None,
)

comprobar(
    "algunos inválidos: se queda con los buenos",
    _solo_autenticos("wss://relay.regular", [bueno, malo]),
    [bueno],
)

comprobar(
    "todos válidos: pasan todos",
    _solo_autenticos("wss://relay.bueno", [bueno, bueno]),
    [bueno, bueno],
)

# Un relay sin nada que decir es una respuesta legítima, no un relay sospechoso: es el caso
# normal cuando no hay ninguna oferta viva.
comprobar(
    "sin eventos: lista vacía, no None",
    _solo_autenticos("wss://relay.vacio", []),
    [],
)


print()
if fallos:
    print(f"❌ {len(fallos)} comprobación(es) fallida(s)")
    sys.exit(1)
print("✅ Todo en verde")
