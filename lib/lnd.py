"""
lnd.py — Localiza en LND los pagos que hizo Mostro y reparte su coste de enrutamiento.

Mostro paga desde lnd2 y lnd2 enruta a través de lnd1, que es el que tiene la liquidez. Hay
que mirar los dos nodos, y separar lo que cobra la red de lo que cobra uno de tus propios
nodos: esa parte se queda en casa y hoy es 0, pero puede dejar de serlo.

Tres reglas que vienen de haber visto este cálculo fallar:

- Un pago que no se encuentra es desconocido, nunca 0. Un 0 inventado es indistinguible de
  un 0 real y envenena todos los totales; un desconocido se reintenta.
- Los costes se llevan en milisatoshis. fee_sat de LND está truncado (28,975 sats salen
  como 28), y con comisiones de pocos sats el error es del orden del propio dato.
- Si la factura del comprador es una Lightning Address no hay hash que buscar. Mostro no
  guarda el del pago, pero el importe pagado es exactamente amount - fee (comprobado en
  60 de 60 órdenes), así que se identifica por importe y fecha, y si hay dos candidatas no
  se adivina.

La parte que habla con lncli está aislada en _lncli(); el resto son funciones puras.
"""

import json
import re
import subprocess

HEX64 = re.compile(r"^[0-9a-f]{64}$")
BOLT11 = re.compile(r"^ln(bc|tb|bcrt)[0-9a-z]+$", re.IGNORECASE)

# El pago al comprador se lanza poco después de tomarse la orden, pero un reintento o un
# comprador lento pueden retrasarlo. Tres días cubre todo lo visto hasta ahora.
VENTANA_PAGO_SEGUNDOS = 3 * 86400
MARGEN_ANTES_SEGUNDOS = 600


class ErrorLnd(RuntimeError):
    """Un lncli que falla o devuelve algo ilegible. Siempre significa 'no sé', nunca '0'."""


class Nodo:
    def __init__(self, nombre, args):
        self.nombre = nombre
        self.args = list(args)


def cargar_nodos(entorno):
    """lnd1 siempre; lnd2 solo si su certificado y su macaroon de lectura están configurados.

    Sin lnd2 los pagos hechos desde él quedan pendientes en vez de contarse como 0, que es
    justo lo que pasaba al seguir consultando solo a lnd1 después de migrar Mostro.
    """
    nodos = [Nodo("lnd1", ["lncli"])]
    cert = entorno.get("LND2_TLSCERT")
    macaroon = entorno.get("LND2_MACAROON")
    if cert and macaroon:
        servidor = entorno.get("LND2_RPCSERVER") or "127.0.0.1:10010"
        nodos.append(Nodo("lnd2", [
            "lncli",
            f"--rpcserver={servidor}",
            f"--tlscertpath={cert}",
            f"--macaroonpath={macaroon}",
        ]))
    return nodos


# --- Acceso a lncli ----------------------------------------------------------------


def _lncli(nodo, *args, timeout=120):
    try:
        r = subprocess.run([*nodo.args, *args], capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise ErrorLnd(f"{nodo.nombre}: {e}") from e
    if r.returncode != 0:
        raise ErrorLnd(f"{nodo.nombre}: {r.stderr.strip()[:200]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise ErrorLnd(f"{nodo.nombre}: respuesta ilegible ({e})") from e


def pubkey_de(nodo):
    return _lncli(nodo, "getinfo", timeout=30)["identity_pubkey"]


def listar_pagos(nodo, desde, hasta):
    """Pagos completados creados entre dos epochs. Una consulta por ventana, sin caché.

    La caché incremental anterior pedía solo lo creado desde la última consulta, y un pago
    que seguía en vuelo en ese momento no se volvía a pedir cuando terminaba.
    """
    datos = _lncli(
        nodo, "listpayments",
        f"--creation_date_start={int(desde)}",
        f"--creation_date_end={int(hasta)}",
        "--max_payments=100000",
        timeout=180,
    )
    return [p for p in datos.get("payments", []) if p.get("status") == "SUCCEEDED"]


def hash_de_factura(nodo, bolt11):
    """payment_hash de un bolt11. Falla con ErrorLnd si lncli no puede decodificarlo."""
    return _lncli(nodo, "decodepayreq", bolt11, timeout=20)["payment_hash"].lower()


# --- Funciones puras ---------------------------------------------------------------


def es_hash(valor):
    return isinstance(valor, str) and HEX64.match(valor.lower()) is not None


def destino_de(buyer_invoice):
    """'bolt11' | 'direccion' | 'desconocido', según lo que guardó Mostro del comprador."""
    if not buyer_invoice:
        return "desconocido"
    if BOLT11.match(buyer_invoice):
        return "bolt11"
    if "@" in buyer_invoice:
        return "direccion"
    return "desconocido"


def desglosar_coste(pago, propias):
    """(total_msat, interno_msat) de un pago de listpayments.

    total_msat es lo que LND dice que costó. interno_msat es la parte que cobraron nodos
    tuyos (los de `propias`, un conjunto de pubkeys). En la ruta de LND, la fee de cada
    salto la cobra el propio nodo de ese salto (comprobado contra las políticas de los
    canales), de modo que sumar las de los saltos que son tuyos da lo que se queda en casa.
    """
    suma_saltos = 0
    interno = 0
    for htlc in pago.get("htlcs", []):
        if htlc.get("status") != "SUCCEEDED":
            continue
        for salto in htlc.get("route", {}).get("hops", []):
            fee = int(salto.get("fee_msat") or 0)
            suma_saltos += fee
            if salto.get("pub_key") in propias:
                interno += fee

    declarado = pago.get("fee_msat")
    total = int(declarado) if declarado not in (None, "") else suma_saltos
    return total, min(interno, total)


def pago_por_importe(pagos, importe_sats, desde, hasta, excluidos=(), coste=None):
    """Busca el pago de `importe_sats` creado entre dos epochs.

    Devuelve (pago, 'ok') | (None, 'ninguno') | (None, 'ambiguo'). Con varias candidatas no
    se elige: asignar el pago equivocado falsea el coste de dos órdenes a la vez.

    Salvo que todas cuesten exactamente lo mismo. `coste` es una función pago -> valor
    comparable; si da el mismo valor para todas las candidatas, da igual cuál sea la de esta
    orden, porque cambia qué pago se asigna pero no el coste. Pasa con órdenes pequeñas
    repetidas hacia el mismo destino. Se toma la más temprana.
    """
    candidatas = sorted(
        (
            p for p in pagos
            if int(p.get("value_sat") or 0) == importe_sats
            and desde <= int(p.get("creation_date") or 0) <= hasta
            and p.get("payment_hash") not in excluidos
        ),
        key=lambda p: int(p.get("creation_date") or 0),
    )
    if len(candidatas) == 1:
        return candidatas[0], "ok"
    if candidatas and coste is not None and len({coste(p) for p in candidatas}) == 1:
        return candidatas[0], "ok"
    return None, ("ambiguo" if candidatas else "ninguno")


def resolver_orden(orden, por_hash, lista, propias, usados=()):
    """Localiza los dos pagos de una orden y reparte su coste.

    `orden` trae: amount, fee, dev_fee, dev_fee_hash, taken_at, destino y hash_comprador
    (este último solo si destino == 'bolt11'). `por_hash` indexa los pagos por payment_hash,
    `lista` es la misma colección como lista, y `usados` los hashes ya asignados a otra orden.

    Devuelve un dict con comprador y devs como (total_msat, interno_msat) o None si no se
    conocen, los hashes de los pagos encontrados, un estado ('ok', 'pendiente' o
    'ambiguo') y el motivo de lo que falta.
    """
    motivos = []
    ambiguo = False

    comprador = None
    hash_comprador = None
    if orden["destino"] == "bolt11":
        pago = por_hash.get(orden.get("hash_comprador"))
        if pago:
            comprador = desglosar_coste(pago, propias)
            hash_comprador = pago["payment_hash"]
        else:
            motivos.append("pago al comprador no localizado por su factura")
    elif orden["destino"] == "direccion":
        importe = orden["amount"] - orden["fee"]
        pago, resultado = pago_por_importe(
            lista, importe,
            orden["taken_at"] - MARGEN_ANTES_SEGUNDOS,
            orden["taken_at"] + VENTANA_PAGO_SEGUNDOS,
            usados,
            coste=lambda p: desglosar_coste(p, propias),
        )
        if pago:
            comprador = desglosar_coste(pago, propias)
            hash_comprador = pago["payment_hash"]
        elif resultado == "ambiguo":
            ambiguo = True
            motivos.append("varios pagos con el importe del comprador")
        else:
            motivos.append("pago al comprador (Lightning Address) no localizado por importe")
    else:
        motivos.append("destino del pago al comprador desconocido")

    devs = None
    hash_devs = None
    if orden["dev_fee"] == 0:
        devs = (0, 0)
    else:
        candidato = (orden.get("dev_fee_hash") or "").lower()
        if es_hash(candidato):
            pago = por_hash.get(candidato)
            if pago:
                devs = desglosar_coste(pago, propias)
                hash_devs = candidato
            else:
                motivos.append("pago de la comision de desarrollo no localizado")
        else:
            motivos.append("comision de desarrollo aun sin pagar")

    if comprador is not None and devs is not None:
        estado = "ok"
    else:
        estado = "ambiguo" if ambiguo else "pendiente"

    return {
        "comprador": comprador,
        "devs": devs,
        "hash_comprador": hash_comprador,
        "hash_devs": hash_devs,
        "estado": estado,
        "motivo": "; ".join(motivos),
    }
