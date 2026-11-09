"""
ganancias.py — Lo que accounting.db guarda de cada operación: esquema, neto y guardado.

lib/contabilidad.py solo lee; todo lo que escribe en earnings está aquí, para que el
servicio que contabiliza y el script que recalcula el histórico compartan una única manera
de hacerlo.

Las columnas originales (routing_buyer, routing_devs, net_profit) son sats enteros y las
leen los informes de siempre. Se conservan y se rellenan con el valor redondeado de las
nuevas, en milisatoshis, que son las que mandan: sumar millones de sats redondeados uno a
uno acumularía hasta medio sat de error por operación.

Qué se resta como coste: lo que cobra la red (total - interno). Lo que cobran tus propios
nodos entre sí no sale de tu bolsillo, y se guarda aparte para poder verlo. Si algún día
quieres tratarlo como coste, el único sitio que cambia es calcular_neto_msat().
"""

COLUMNAS_NUEVAS = {
    "routing_buyer_msat": "INTEGER",
    "routing_buyer_interno_msat": "INTEGER",
    "routing_devs_msat": "INTEGER",
    "routing_devs_interno_msat": "INTEGER",
    "net_profit_msat": "INTEGER",
    # NULL: fila anterior a esta versión, sin verificar contra LND.
    # 'ok' | 'pendiente' | 'ambiguo' | 'sin_datos' (pendiente que ya no se reintenta)
    "routing_estado": "TEXT",
    "routing_motivo": "TEXT",
    "routing_revisado_at": "INTEGER",
    "pago_comprador_hash": "TEXT",
    "pago_devs_hash": "TEXT",
}

ESTADOS_SIN_RESOLVER = ("pendiente", "ambiguo", "sin_datos")

# Neto de una fila, en msat, para sumar en los informes.
#
# Una operación 'pendiente' cuyo comprador ya se conoce solo espera el coste del pago de la
# comisión de desarrollo, de en torno a 1 sat. Dejarla fuera del total haría desaparecer su
# beneficio entero (cientos de sats) durante los días que tarde, que es mucho más engañoso
# que contarla con ese sat por conocer. Se cuenta con lo que se sabe, y los informes avisan
# de cuántas son así. Si falta el coste del comprador no se calcula nada: ahí el error
# podría ser grande.
SQL_NETO_MSAT = """COALESCE(
    net_profit_msat,
    CASE WHEN routing_estado = 'pendiente' AND routing_buyer_msat IS NOT NULL
         THEN (fee - dev_fee) * 1000
              - (routing_buyer_msat - routing_buyer_interno_msat)
              - COALESCE(routing_devs_msat - routing_devs_interno_msat, 0)
    END,
    net_profit * 1000
)"""


def redondear_sats(msat):
    """Redondeo normal (0,5 sube) de milisatoshis a sats. Válido también para negativos."""
    return (msat + 500) // 1000


def migrar_esquema(con):
    """Añade las columnas nuevas si faltan. Idempotente: se llama en cada arranque."""
    existentes = {fila[1] for fila in con.execute("PRAGMA table_info(earnings)")}
    for nombre, tipo in COLUMNAS_NUEVAS.items():
        if nombre not in existentes:
            con.execute(f"ALTER TABLE earnings ADD COLUMN {nombre} {tipo}")
    con.commit()


def _externo(par):
    return None if par is None else par[0] - par[1]


def calcular_neto_msat(fee_real, dev_fee, comprador, devs):
    """fee_real - dev_fee - coste externo de los dos pagos, en msat. None si falta alguno.

    Es None y no una cifra con 0 en lo que falta: un neto a medias parece un neto.
    """
    if comprador is None or devs is None:
        return None
    return (fee_real - dev_fee) * 1000 - _externo(comprador) - _externo(devs)


def neto_provisional_msat(fee_real, dev_fee, comprador, devs):
    """Lo mismo, contando 0 en lo que aún no se conoce. Solo para avisar al operador."""
    conocido = (_externo(comprador) or 0) + (_externo(devs) or 0)
    return (fee_real - dev_fee) * 1000 - conocido


def guardar_resolucion(con, orden, res, ahora):
    """Inserta la operación o actualiza lo relativo a routing si ya existía.

    `orden` es la fila de Mostro (id, amount, fee, dev_fee, fiat_code, fiat_amount,
    taken_at) y `res` lo que devuelve lnd.resolver_orden. Devuelve el neto en msat o None.
    Si la fila ya existía no se toca ni su fecha de aviso ni su fee: solo el coste.
    """
    fee_real = orden["fee"] * 2  # Mostro registra solo el 50% de la fee real
    comprador, devs = res["comprador"], res["devs"]
    neto_msat = calcular_neto_msat(fee_real, orden["dev_fee"], comprador, devs)

    def sats(valor):
        return None if valor is None else redondear_sats(valor)

    valores = {
        "routing_buyer": sats(_externo(comprador)),
        "routing_devs": sats(_externo(devs)),
        "net_profit": sats(neto_msat),
        "routing_buyer_msat": None if comprador is None else comprador[0],
        "routing_buyer_interno_msat": None if comprador is None else comprador[1],
        "routing_devs_msat": None if devs is None else devs[0],
        "routing_devs_interno_msat": None if devs is None else devs[1],
        "net_profit_msat": neto_msat,
        "routing_estado": res["estado"],
        "routing_motivo": res["motivo"] or None,
        "routing_revisado_at": ahora,
        "pago_comprador_hash": res["hash_comprador"],
        "pago_devs_hash": res["hash_devs"],
    }
    columnas = ", ".join(valores)
    marcas = ", ".join("?" for _ in valores)
    actualizar = ", ".join(f"{c} = excluded.{c}" for c in valores)
    con.execute(
        f"""
        INSERT INTO earnings
          (order_id, completed_at, amount, fiat_code, fiat_amount, fee, dev_fee,
           notified_at, {columnas})
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, {marcas})
        ON CONFLICT(order_id) DO UPDATE SET {actualizar}
        """,
        (orden["id"], orden["taken_at"], orden["amount"], orden["fiat_code"],
         orden["fiat_amount"], fee_real, orden["dev_fee"], ahora, *valores.values()),
    )
    con.commit()
    return neto_msat


def ids_procesados(con):
    return {fila[0] for fila in con.execute("SELECT order_id FROM earnings")}


def hashes_usados(con):
    """Pagos ya asignados a una orden: se excluyen al buscar por importe."""
    return {
        fila[0]
        for fila in con.execute(
            "SELECT pago_comprador_hash FROM earnings WHERE pago_comprador_hash IS NOT NULL"
        )
    }


def hash_asignado(con, order_id):
    """El pago al comprador que esta misma orden ya tenía asignado, si lo tenía."""
    fila = con.execute(
        "SELECT pago_comprador_hash FROM earnings WHERE order_id = ?", (order_id,)
    ).fetchone()
    return fila[0] if fila else None


def caducar_pendientes(con, ahora, max_dias):
    """Las pendientes de hace más de max_dias pasan a 'sin_datos' y dejan de reintentarse."""
    limite = ahora - max_dias * 86400
    con.execute(
        "UPDATE earnings SET routing_estado = 'sin_datos' "
        "WHERE routing_estado IN ('pendiente', 'ambiguo') AND completed_at < ?",
        (limite,),
    )
    con.commit()


def ids_a_reintentar(con):
    return [
        fila[0]
        for fila in con.execute(
            "SELECT order_id FROM earnings WHERE routing_estado IN ('pendiente', 'ambiguo')"
        )
    ]


def totales(con):
    """(neto acumulado en msat, nº de operaciones con neto conocido o provisional)."""
    return con.execute(
        f"SELECT COALESCE(SUM({SQL_NETO_MSAT}), 0), COUNT({SQL_NETO_MSAT}) FROM earnings"
    ).fetchone()
