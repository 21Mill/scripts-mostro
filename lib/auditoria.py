"""
auditoria.py — Reconstruye el pago de Mostro al comprador de una orden, con todos sus intentos.

Lo que guarda la orden no basta para auditarlo: el `hash` es el de la hold invoice del
vendedor (lo que se cobró), no el del pago de salida, y `payment_attempts` y `failed_payment`
son contadores. El pago al comprador está en LND, y cada reintento es un pago distinto con
su propio hash. Aquí se juntan todos, en lnd1 y lnd2, y se enseña por saltos qué costó y a
quién.

Cómo se localiza:

- Si el comprador dio una factura, por el hash de esa factura.
- Si dio una Lightning Address no hay hash que buscar: se busca por importe (amount - fee,
  exacto) y fecha, y se dice así. Si otras órdenes tienen el mismo importe en esa ventana, se
  avisa de que los intentos pueden mezclarse en vez de afirmar que son de esta.

Las funciones son puras: los pagos llegan ya leídos y el alias de un nodo se pide a una
función que se pasa, de modo que se prueba sin LND. La parte que habla con él está en
tools/pago.py.
"""

import time
from collections import Counter

from lib.formato import formato_msat, formato_sats
from lib.lnd import MARGEN_ANTES_SEGUNDOS, VENTANA_PAGO_SEGUNDOS, desglosar_coste


def _hex_real(valor):
    """Un preimage de verdad: LND rellena con ceros el de los pagos que no se completaron."""
    return bool(valor) and set(valor) != {"0"}


def describir_intento(nodo, pago, propias, alias_de):
    """Resume un pago de listpayments: estado, coste repartido, ruta y causas de fallo."""
    total, interno = desglosar_coste(pago, propias)
    htlcs = pago.get("htlcs", [])
    exitosos = [h for h in htlcs if h.get("status") == "SUCCEEDED"]
    fallos = Counter(
        (h.get("failure") or {}).get("code", "SIN_CODIGO") for h in htlcs if h.get("status") == "FAILED"
    )

    rutas = []
    for htlc in exitosos:
        saltos = htlc.get("route", {}).get("hops", [])
        rutas.append([
            {
                "pub": s.get("pub_key", ""),
                "alias": alias_de(s.get("pub_key", "")) or "",
                "fee_msat": int(s.get("fee_msat") or 0),
                "propio": s.get("pub_key") in propias,
                "destino": i == len(saltos) - 1,
            }
            for i, s in enumerate(saltos)
        ])

    duracion = None
    if exitosos:
        inicio = int(exitosos[0].get("attempt_time_ns") or 0)
        fin = int(exitosos[0].get("resolve_time_ns") or 0)
        if inicio and fin:
            duracion = (fin - inicio) / 1e9

    estado = pago.get("status")
    motivo = pago.get("failure_reason")
    return {
        "nodo": nodo,
        "hash": pago["payment_hash"],
        "estado": estado,
        "motivo": None if motivo in (None, "", "FAILURE_REASON_NONE") else motivo,
        "creado": int(pago.get("creation_date") or 0),
        "valor_sat": int(pago.get("value_sat") or 0),
        "fee_msat": total,
        "interno_msat": interno,
        "externo_msat": total - interno,
        "preimage": pago.get("payment_preimage") if estado == "SUCCEEDED" and _hex_real(pago.get("payment_preimage")) else None,
        "rutas": rutas,
        "fallos": dict(fallos),
        "n_htlcs": len(htlcs),
        "duracion_s": duracion,
    }


def candidatos(orden, pagos_por_nodo, hash_factura=None):
    """[(nodo, pago, por_hash)] de los pagos que pueden ser del comprador, del más antiguo al más nuevo."""
    neto = orden["amount"] - orden["fee"]
    desde = orden["taken_at"] - MARGEN_ANTES_SEGUNDOS
    hasta = orden["taken_at"] + VENTANA_PAGO_SEGUNDOS
    encontrados = []
    for nodo, pagos in pagos_por_nodo.items():
        for pago in pagos:
            creado = int(pago.get("creation_date") or 0)
            por_hash = hash_factura is not None and pago.get("payment_hash") == hash_factura
            por_importe = int(pago.get("value_sat") or 0) == neto and desde <= creado <= hasta
            if por_hash or por_importe:
                encontrados.append((nodo, pago, por_hash))
    encontrados.sort(key=lambda x: int(x[1].get("creation_date") or 0))
    return encontrados


def auditar_pago(orden, pagos_por_nodo, propias, alias_de, hash_factura=None, homonimas=0,
                 contabilidad=None, nodos_caidos=(), lnd2_configurado=True):
    """La auditoría completa del pago al comprador.

    `orden`: amount, fee, taken_at, destino ('bolt11' | 'direccion' | ...), payment_attempts y
    failed_payment (los contadores de Mostro). `pagos_por_nodo`: {'lnd1': [...], 'lnd2': [...]}
    con pagos de cualquier estado. `homonimas`: otras órdenes con el mismo importe neto cerca
    en el tiempo. `contabilidad`: lo que devuelve contabilidad.fila_de_orden.
    """
    encontrados = candidatos(orden, pagos_por_nodo, hash_factura)
    intentos = [describir_intento(n, p, propias, alias_de) for n, p, _ in encontrados]
    metodo = "hash" if any(por_hash for _, _, por_hash in encontrados) else "importe"
    exitos = [i for i in intentos if i["estado"] == "SUCCEEDED"]

    avisos = []
    for nodo in nodos_caidos:
        avisos.append(f"{nodo} no respondió: puede faltar algún intento.")
    if not lnd2_configurado:
        avisos.append("lnd2 no está configurado (LND2_TLSCERT y LND2_MACAROON): solo se ha mirado lnd1, "
                      "y Mostro paga desde lnd2.")
    if not intentos:
        avisos.append("No hay ningún pago de ese importe en la ventana de la orden.")
    if metodo == "importe" and homonimas and intentos:
        avisos.append(f"Hay {homonimas} orden(es) más con el mismo importe en esos días: "
                      "los intentos de arriba podrían mezclarse con los suyos.")
    if len(exitos) > 1 and not homonimas:
        avisos.append(f"Hay {len(exitos)} pagos completados de ese importe para una sola orden: "
                      "posible doble pago.")
    if any(i["estado"] == "IN_FLIGHT" for i in intentos):
        avisos.append("Hay un pago en vuelo: todavía no se sabe si saldrá.")
    if len(exitos) == 1 and contabilidad and contabilidad.get("comprador_msat") is not None:
        if contabilidad["comprador_msat"] != exitos[0]["fee_msat"]:
            avisos.append(
                f"La contabilidad guardó {formato_msat(contabilidad['comprador_msat'])} sats de routing "
                f"y LND dice {formato_msat(exitos[0]['fee_msat'])}."
            )

    return {
        "esperado_sats": orden["amount"] - orden["fee"],
        "amount": orden["amount"],
        "fee": orden["fee"],
        "destino": orden.get("destino"),
        "metodo": metodo,
        # Solo para --json. En el texto no se enseñan: `failed_payment` es un 0 o un 1 y
        # `payment_attempts` no es el número real de intentos, y la lista de intentos de LND
        # ya cuenta lo que pasó de verdad.
        "segun_mostro": {"intentos": orden.get("payment_attempts"), "fallidos": orden.get("failed_payment")},
        "intentos": intentos,
        "exito": exitos[0] if len(exitos) == 1 else None,
        "contabilidad": contabilidad,
        "avisos": avisos,
    }


# --- Presentación ------------------------------------------------------------------


def _paleta(color):
    if not color:
        return {k: "" for k in ("verde", "rojo", "amarillo", "tenue", "negrita", "fin")}
    return {"verde": "\033[0;32m", "rojo": "\033[0;31m", "amarillo": "\033[1;33m",
            "tenue": "\033[2m", "negrita": "\033[1m", "fin": "\033[0m"}


def _hora(epoch):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def texto_auditoria(aud, color=False):
    """La auditoría como texto para terminal. Con color=True usa los colores de order.sh."""
    c = _paleta(color)
    out = [f"{c['negrita']}  ─── Auditoría del pago al comprador ───{c['fin']}", ""]

    out.append(f"  Esperado:      {formato_sats(aud['esperado_sats'])} sats"
               f"  {c['tenue']}({formato_sats(aud['amount'])} − {formato_sats(aud['fee'])} de fee){c['fin']}")
    if aud["metodo"] == "hash":
        out.append(f"  Localizado:    por el hash de la factura del comprador")
    elif aud["destino"] == "direccion":
        out.append(f"  Localizado:    por importe y fecha {c['tenue']}(el comprador dio una Lightning Address, "
                   f"no hay hash de factura){c['fin']}")
    else:
        out.append("  Localizado:    por importe y fecha")
    out.append("")

    total = len(aud["intentos"])
    for n, i in enumerate(aud["intentos"], 1):
        if i["estado"] == "SUCCEEDED":
            marca = f"{c['verde']}✅ SUCCEEDED{c['fin']}"
        elif i["estado"] == "FAILED":
            marca = f"{c['rojo']}❌ FAILED{c['fin']}"
        elif i["estado"] == "IN_FLIGHT":
            marca = f"{c['amarillo']}⏳ IN_FLIGHT{c['fin']}"
        else:
            marca = str(i["estado"])
        out.append(f"  {c['negrita']}Intento {n} de {total}{c['fin']} · {i['nodo']} · {_hora(i['creado'])} · {marca}")
        if i["motivo"]:
            out.append(f"    Motivo:     {c['rojo']}{i['motivo']}{c['fin']}")
        for codigo, veces in sorted(i["fallos"].items()):
            out.append(f"    Rechazo:    {veces} × {codigo}")
        out.append(f"    Hash:       {i['hash']}")
        if i["preimage"]:
            out.append(f"    Preimage:   {i['preimage']}")
        if i["estado"] == "SUCCEEDED":
            fee = formato_msat(i["fee_msat"])
            reparto = (f"{c['tenue']}(red {formato_msat(i['externo_msat'])} · "
                       f"tus nodos {formato_msat(i['interno_msat'])}){c['fin']}")
            dur = f" · tardó {i['duracion_s']:.1f} s".replace(".", ",") if i["duracion_s"] is not None else ""
            out.append(f"    Comisión:   {fee} sats {reparto}{dur}")
            for r, ruta in enumerate(i["rutas"], 1):
                if len(i["rutas"]) > 1:
                    out.append(f"    Ruta {r}:")
                else:
                    out.append("    Ruta:")
                for k, s in enumerate(ruta, 1):
                    nombre = s["alias"] or (s["pub"][:16] + "…")
                    marcas = []
                    if s["propio"]:
                        marcas.append("tuyo")
                    if s["destino"]:
                        marcas.append("destino")
                    etiqueta = f" {c['tenue']}({', '.join(marcas)}){c['fin']}" if marcas else ""
                    out.append(f"      {k}. {nombre[:34]:<34} {formato_sats(s['fee_msat']):>10} msat{etiqueta}")
        out.append("")

    cont = aud["contabilidad"]
    if cont is None:
        out.append(f"  Contabilidad:  {c['tenue']}sin fila: la orden aún no se ha contabilizado{c['fin']}")
    elif cont.get("comprador_msat") is None:
        out.append(f"  Contabilidad:  pendiente {c['tenue']}({cont.get('motivo') or 'sin detalle'}){c['fin']}")
    else:
        externo = cont["comprador_msat"] - (cont.get("comprador_interno_msat") or 0)
        out.append(f"  Contabilidad:  routing del comprador {formato_msat(cont['comprador_msat'])} sats"
                   f" {c['tenue']}(red {formato_msat(externo)} · tus nodos {formato_msat(cont.get('comprador_interno_msat') or 0)}"
                   f" · estado {cont.get('estado') or 'sin migrar'}){c['fin']}")

    for aviso in aud["avisos"]:
        out.append(f"  {c['amarillo']}⚠{c['fin']}  {aviso}")
    out.append("")
    return "\n".join(out)
