"""
test-auditoria.py — Comprueba la auditoría del pago de Mostro al comprador.

Ejecutar: python3 lib/test-auditoria.py

Sin dependencias ni framework, como test-lib.py. Nada aquí toca LND: los pagos son diccionarios
con la forma de `lncli listpayments`. El caso central es una orden real: el primer intento al
comprador falló por timeout y el segundo salió por cuatro saltos.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lib.lnd as lnd
from lib.auditoria import auditar_pago, candidatos, texto_auditoria
from lib.contabilidad import fila_de_orden
from lib.ganancias import migrar_esquema

fallos = []


def comprobar(descripcion, obtenido, esperado):
    if obtenido == esperado:
        print(f"  ✅ {descripcion}")
    else:
        print(f"  ❌ {descripcion}\n       esperado: {esperado}\n       obtenido: {obtenido}")
        fallos.append(descripcion)


LND1 = "02" + "a1" * 32
FLASH = "02" + "e4" * 32
DESCONOCIDO = "02" + "e9" * 32
DESTINO = "03" + "d5" * 32
PROPIAS = {LND1, "03" + "b2" * 32}
ALIAS = {LND1: "NostroMostro", FLASH: "1sats.com", DESCONOCIDO: "", DESTINO: "Destino"}
H_FALLO = "64" * 32
H_EXITO = "93" * 32
PREIMAGE = "4e" * 32
T = 1_000_000


def alias_de(pub):
    return ALIAS.get(pub)


def pago(hash_, creado, fee_msat, saltos=(), estado="SUCCEEDED", motivo="FAILURE_REASON_NONE", value=150004,
         preimage=None, rechazos=()):
    htlcs = []
    if saltos:
        htlcs.append({"status": "SUCCEEDED" if estado == "SUCCEEDED" else estado,
                      "attempt_time_ns": "1000000000", "resolve_time_ns": "4200000000",
                      "route": {"hops": [{"pub_key": k, "fee_msat": str(f)} for k, f in saltos]}})
    for codigo in rechazos:
        htlcs.append({"status": "FAILED", "failure": {"code": codigo}, "route": {"hops": []}})
    return {"payment_hash": hash_, "value_sat": str(value), "creation_date": str(creado), "status": estado,
            "failure_reason": motivo, "fee_msat": str(fee_msat),
            "payment_preimage": preimage or ("0" * 64), "htlcs": htlcs}


ORDEN = {"amount": 150455, "fee": 451, "taken_at": T, "destino": "direccion",
         "payment_attempts": 1, "failed_payment": 1}

intento_fallido = pago(H_FALLO, T + 800, 0, estado="FAILED", motivo="FAILURE_REASON_TIMEOUT")
intento_exitoso = pago(H_EXITO, T + 1000, 150005, [(LND1, 0), (FLASH, 1), (DESCONOCIDO, 150004), (DESTINO, 0)],
                       preimage=PREIMAGE)
PAGOS = {"lnd2": [intento_exitoso, intento_fallido], "lnd1": []}


def auditar(**cambios):
    args = {"orden": ORDEN, "pagos_por_nodo": PAGOS, "propias": PROPIAS, "alias_de": alias_de}
    args.update(cambios)
    return auditar_pago(**args)


print("localización\n")

aud = auditar()
comprobar("encuentra los dos intentos, del más antiguo al más nuevo",
          [i["estado"] for i in aud["intentos"]], ["FAILED", "SUCCEEDED"])
comprobar("y sabe en qué nodo estaba cada uno", [i["nodo"] for i in aud["intentos"]], ["lnd2", "lnd2"])
comprobar("esperado = amount - fee", aud["esperado_sats"], 150004)
comprobar("sin hash de factura se localiza por importe", aud["metodo"], "importe")
comprobar("el pago de otro importe no entra",
          len(auditar(pagos_por_nodo={"lnd2": [pago("77" * 32, T + 900, 5, value=99999)]})["intentos"]), 0)
comprobar("ni el de ese importe fuera de la ventana de la orden",
          len(auditar(pagos_por_nodo={"lnd2": [pago("77" * 32, T + 10 * 86400, 5)]})["intentos"]), 0)

fuera_de_ventana = pago("55" * 32, T + 10 * 86400, 777, [(LND1, 0), (DESTINO, 0)])
aud_hash = auditar(pagos_por_nodo={"lnd2": [fuera_de_ventana]}, hash_factura="55" * 32)
comprobar("con la factura del comprador, el hash manda aunque caiga fuera de la ventana",
          (aud_hash["metodo"], len(aud_hash["intentos"])), ("hash", 1))
comprobar("busca en lnd1 y en lnd2",
          len(auditar(pagos_por_nodo={"lnd1": [intento_fallido], "lnd2": [intento_exitoso]})["intentos"]), 2)
comprobar("candidatos() devuelve (nodo, pago, por_hash)",
          [(n, p["payment_hash"], h) for n, p, h in candidatos(ORDEN, PAGOS)],
          [("lnd2", H_FALLO, False), ("lnd2", H_EXITO, False)])


print("\nel intento completado\n")

ok = aud["exito"]
comprobar("hay un único pago completado", ok["hash"], H_EXITO)
comprobar("comisión total en msat exactos", ok["fee_msat"], 150005)
comprobar("de la que tus nodos no cobran nada", (ok["interno_msat"], ok["externo_msat"]), (0, 150005))
comprobar("la ruta tiene cuatro saltos", len(ok["rutas"][0]), 4)
comprobar("el primero es tuyo y el último es el destino",
          (ok["rutas"][0][0]["propio"], ok["rutas"][0][-1]["destino"]), (True, True))
comprobar("quien cobró casi todo es el tercer salto", ok["rutas"][0][2]["fee_msat"], 150004)
comprobar("con el alias cuando el grafo lo tiene y vacío si no",
          [s["alias"] for s in ok["rutas"][0]], ["NostroMostro", "1sats.com", "", "Destino"])
comprobar("el preimage solo se enseña en el que salió", (ok["preimage"], aud["intentos"][0]["preimage"]), (PREIMAGE, None))
comprobar("el motivo del fallo, en el que falló", aud["intentos"][0]["motivo"], "FAILURE_REASON_TIMEOUT")
comprobar("y ninguno en el que salió", ok["motivo"], None)
comprobar("cuánto tardó, a partir de los tiempos del HTLC", ok["duracion_s"], 3.2)

con_interno = pago(H_EXITO, T + 1000, 1300, [(LND1, 300), (DESCONOCIDO, 1000), (DESTINO, 0)], preimage=PREIMAGE)
ok2 = auditar(pagos_por_nodo={"lnd2": [con_interno]})["exito"]
comprobar("si lnd1 cobrara, esa parte sale como coste interno",
          (ok2["interno_msat"], ok2["externo_msat"]), (300, 1000))

con_rechazos = pago(H_FALLO, T + 800, 0, estado="FAILED", motivo="FAILURE_REASON_NO_ROUTE",
                    rechazos=["TEMPORARY_CHANNEL_FAILURE"] * 3)
comprobar("los rechazos se agrupan por causa",
          auditar(pagos_por_nodo={"lnd2": [con_rechazos]})["intentos"][0]["fallos"], {"TEMPORARY_CHANNEL_FAILURE": 3})


print("\navisos\n")

comprobar("un caso limpio no avisa de nada", aud["avisos"], [])
comprobar("sin ningún pago, lo dice",
          any("ningún pago" in a for a in auditar(pagos_por_nodo={"lnd1": [], "lnd2": []})["avisos"]), True)
comprobar("si otras órdenes tienen el mismo importe, avisa de que pueden mezclarse",
          any("mismo importe" in a for a in auditar(homonimas=2)["avisos"]), True)
comprobar("pero no lo dice cuando se localizó por el hash de la factura",
          any("mismo importe" in a for a in auditar(pagos_por_nodo={"lnd2": [fuera_de_ventana]},
                                                    hash_factura="55" * 32, homonimas=2)["avisos"]), False)
comprobar("sin lnd2 configurado, lo avisa", any("lnd2 no está configurado" in a for a in auditar(lnd2_configurado=False)["avisos"]), True)
comprobar("con un nodo caído, lo avisa", any("lnd2 no respondió" in a for a in auditar(nodos_caidos=["lnd2"])["avisos"]), True)

segundo_exito = pago("88" * 32, T + 2000, 150005, [(LND1, 0), (DESTINO, 0)], preimage="9a" * 32)
doble = auditar(pagos_por_nodo={"lnd2": [intento_exitoso, segundo_exito]})
comprobar("dos pagos completados para una orden: posible doble pago",
          any("doble pago" in a for a in doble["avisos"]), True)
comprobar("y entonces no se da ninguno por bueno", doble["exito"], None)
comprobar("salvo que otra orden pueda explicarlo: entonces avisa de la mezcla, no de doble pago",
          any("doble pago" in a for a in auditar(pagos_por_nodo={"lnd2": [intento_exitoso, segundo_exito]}, homonimas=1)["avisos"]), False)
comprobar("un pago en vuelo se avisa",
          any("en vuelo" in a for a in auditar(pagos_por_nodo={"lnd2": [pago(H_FALLO, T + 5, 0, estado="IN_FLIGHT")]})["avisos"]), True)

coincide = {"estado": "ok", "comprador_msat": 150005, "comprador_interno_msat": 0, "motivo": None}
distinta = dict(coincide, comprador_msat=150000)
comprobar("si la contabilidad coincide con LND, no avisa", auditar(contabilidad=coincide)["avisos"], [])
comprobar("si no coincide, lo dice con las dos cifras",
          [a for a in auditar(contabilidad=distinta)["avisos"] if "contabilidad" in a],
          ["La contabilidad guardó 150 sats de routing y LND dice 150,005."])


print("\ntexto\n")

t = texto_auditoria(aud)
comprobar("enseña los dos intentos", ("Intento 1 de 2" in t, "Intento 2 de 2" in t), (True, True))
comprobar("con el hash y el preimage completos, para poder copiarlos", (H_EXITO in t, PREIMAGE in t), (True, True))
comprobar("el estado de cada uno", ("❌ FAILED" in t, "✅ SUCCEEDED" in t), (True, True))
comprobar("cuenta que se localizó por importe y por qué", ("Lightning Address" in t, "por importe y fecha" in t), (True, True))
comprobar("la comisión repartida entre red y tus nodos", "red 150,005 · tus nodos 0" in t, True)
comprobar("la ruta marca el nodo tuyo y el destino", ("(tuyo)" in t, "(destino)" in t), (True, True))
comprobar("sin alias, el principio de la clave", (DESCONOCIDO[:16] + "…") in t, True)
# Los contadores de la orden no son el número real de intentos (aquí marcan 1 y hubo 2
# pagos), así que no se repiten junto a la lista que sí lo es.
comprobar("no repite los contadores de Mostro", ("Según Mostro" in t, "fallidos 1" in t), (False, False))
comprobar("pero siguen en el JSON", (aud["segun_mostro"]["intentos"], aud["segun_mostro"]["fallidos"]), (1, 1))
# La columna de la ruta está en msat: 1 msat es "1", no "0,001".
comprobar("la comisión de cada salto, en msat enteros", ("150.004 msat" in t, "0,001" in t), (True, False))
comprobar("incluso la de un solo msat", "         1 msat" in texto_auditoria(auditar(pagos_por_nodo={"lnd2": [intento_exitoso]})), True)
comprobar("sin contabilidad lo dice", "sin fila" in t, True)
comprobar("sin color no lleva secuencias de escape", "\033[" in t, False)
comprobar("con color sí", "\033[" in texto_auditoria(aud, color=True), True)
t_cont = texto_auditoria(auditar(contabilidad=coincide))
comprobar("con contabilidad, enseña su cifra y su estado", ("150,005 sats" in t_cont, "estado ok" in t_cont), (True, True))
comprobar("los avisos salen al final", texto_auditoria(auditar(homonimas=1)).rstrip().splitlines()[-1].startswith("  ⚠"), True)
comprobar("sin ningún intento no revienta", "Intento 1" in texto_auditoria(auditar(pagos_por_nodo={})), False)


print("\nlectura de LND\n")

llamadas = []


def falso_lncli(nodo, *args, timeout=120):
    llamadas.append(args)
    if args[0] == "listpayments":
        return {"payments": [{"payment_hash": "a", "status": "SUCCEEDED"}, {"payment_hash": "b", "status": "FAILED"}]}
    if args[0] == "getnodeinfo":
        if args[1] == "roto":
            raise lnd.ErrorLnd("sin grafo")
        return {"node": {"alias": "Mi nodo"}}


real_lncli = lnd._lncli
lnd._lncli = falso_lncli
nodo = lnd.Nodo("lnd1", ["lncli"])
comprobar("por defecto solo los completados (lo que necesita la contabilidad)",
          [p["payment_hash"] for p in lnd.listar_pagos(nodo, 1, 2)], ["a"])
comprobar("sin pedir los incompletos a LND", "--include_incomplete" in llamadas[-1], False)
comprobar("con incompletos, todos",
          [p["payment_hash"] for p in lnd.listar_pagos(nodo, 1, 2, incompletos=True)], ["a", "b"])
comprobar("y se lo pide a LND", "--include_incomplete" in llamadas[-1], True)
comprobar("el alias de un nodo del grafo", lnd.alias_de(nodo, "abc"), "Mi nodo")
comprobar("y None si el grafo no lo conoce", lnd.alias_de(nodo, "roto"), None)
lnd._lncli = real_lncli


print("\nfila de contabilidad\n")

ESQUEMA = """CREATE TABLE earnings (order_id TEXT PRIMARY KEY, completed_at INTEGER, amount INTEGER, fiat_code TEXT,
    fiat_amount INTEGER, fee INTEGER, dev_fee INTEGER, routing_buyer INTEGER, routing_devs INTEGER,
    net_profit INTEGER, notified_at INTEGER)"""
ID_HEX = "97341899" "7cda" "4cfe" "aef9" "a82cbaf256ab"
ID_UUID = "97341899-7cda-4cfe-aef9-a82cbaf256ab"

antigua = sqlite3.connect(":memory:")
antigua.execute(ESQUEMA)
antigua.execute("INSERT INTO earnings VALUES (?, 1, 1, 'EUR', 1, 902, 271, 150, 1, 480, 1)", (ID_HEX,))
f = fila_de_orden(antigua, ID_UUID)
comprobar("una base sin migrar da sus sats enteros en msat y sin estado",
          (f["estado"], f["comprador_msat"], f["devs_msat"]), (None, 150_000, 1_000))
comprobar("una orden que no está devuelve None", fila_de_orden(antigua, "0" * 32), None)

migrar_esquema(antigua)
antigua.execute("UPDATE earnings SET routing_estado='ok', routing_buyer_msat=150005, routing_buyer_interno_msat=0, "
                "routing_devs_msat=1013, routing_devs_interno_msat=0, net_profit_msat=480000")
f = fila_de_orden(antigua, ID_UUID)
comprobar("una migrada da los msat exactos y su estado",
          (f["estado"], f["comprador_msat"], f["comprador_interno_msat"], f["devs_msat"]), ("ok", 150005, 0, 1013))
comprobar("acepta el UUID con guiones o sin ellos", fila_de_orden(antigua, ID_HEX), f)


print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallida(s)")
    sys.exit(1)
print("Todas las pruebas pasan")
