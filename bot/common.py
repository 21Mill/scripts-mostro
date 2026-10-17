"""
common.py — Módulo del relay de Mostro, compartido por los bots.

Solo lo específico de Nostr: conexión al relay, parseo de eventos kind 38383 y el texto de
una oferta. El envío por Telegram, el formateo de cifras, la persistencia en JSON y la
carga del .env viven en lib/, porque los usan también scripts que no tocan ningún relay.
"""

import hashlib
import html as html_mod
import json
import os
import sys
import threading
import time
import urllib.parse
import websocket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.entorno import cargar_env
from lib.formato import fecha_larga, formato_sats

# Falla al importar, no en caliente. El bot publica en un canal abierto lo que le cuenta
# un relay; arrancar sin poder comprobar quién firmó cada evento es justo lo que esto
# viene a impedir, así que es mejor no arrancar. En el nodo ya está instalada: pynostr,
# que usa bot-nostr.py, la trae como dependencia.
try:
    from coincurve import PublicKeyXOnly
except ImportError:
    raise SystemExit(
        "Falta coincurve, que verifica las firmas de los eventos: pip install coincurve"
    )

cargar_env()

# Hora peninsular española: los mensajes quedan fijos en el canal y los lee gente de aquí.
# ZoneInfo aplica el cambio invierno/verano solo, así que la etiqueta sale CET o CEST según
# la fecha de la oferta, no según cuándo se mire.
ZONA = ZoneInfo("Europe/Madrid")

MOSTRO_PUBKEY = os.getenv("MOSTRO_PUBKEY")

# En hexadecimal, que es como viene el autor en el evento. Con un npub ahí no fallaría
# nada visible: simplemente no se validaría ni un solo evento y el canal se quedaría
# mudo sin explicar por qué. Mejor decirlo al arrancar.
if MOSTRO_PUBKEY and len(MOSTRO_PUBKEY) != 64:
    raise SystemExit(
        f"MOSTRO_PUBKEY debe ser la clave en hexadecimal, 64 caracteres, no "
        f"{MOSTRO_PUBKEY[:12]}... de {len(MOSTRO_PUBKEY)}"
    )

# MOSTRO_RELAY admite varios relays separados por comas. Con uno solo, el día que ese relay
# dejó de contestar (aceptaba la conexión pero no respondía a nada) los bots se quedaron
# ciegos: ni veían las cancelaciones ni podían reconciliar, y el canal se llenó de ofertas
# muertas. Mostro publica en varios relays a la vez, así que escuchar en todos quita ese
# punto único de fallo.
RELAYS = [r.strip() for r in os.getenv("MOSTRO_RELAY", "").split(",") if r.strip()]

# Dónde se abre una oferta al pulsar su enlace. El cliente web resuelve el enlace de la
# orden por URL y deja tomarla sin instalar nada. El "#/" no es decorativo: el build de
# Flutter web no llama a usePathUrlStrategy, así que usa estrategia de hash y sin la
# almohadilla la ruta no llega a su router. Configurable porque la alternativa —una página
# propia que rebote al esquema mostro: para abrir la app nativa— solo cambiaría esta base.
MOSTRO_WEB_APP = os.getenv("MOSTRO_WEB_APP", "https://mostro.network/app/#/order_link")


def _id_calculado(evento):
    """El id que le corresponde a este evento según NIP-01.

    sha256 de [0, pubkey, created_at, kind, tags, content] serializado sin espacios y sin
    escapar lo que no sea ASCII. Las dos cosas importan: con ensure_ascii por defecto, un
    método de pago con un emoji o una tilde daría otro hash y el evento, siendo auténtico,
    se rechazaría.
    """
    datos = [
        0,
        evento["pubkey"],
        evento["created_at"],
        evento["kind"],
        evento["tags"],
        evento["content"],
    ]
    serializado = json.dumps(datos, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serializado.encode()).hexdigest()


def evento_autentico(evento):
    """True si el evento lo firmó de verdad MOSTRO_PUBKEY.

    Tres comprobaciones, y las tres hacen falta.

    El autor se mira aquí y no solo con el filtro 'authors' de la suscripción, porque ese
    lo aplica el relay, que es precisamente de quien se desconfía.

    El id se recalcula a partir de los datos. La firma va sobre el id, así que verificarla
    sola dejaría pasar un evento con un par (id, firma) auténtico y el contenido
    sustituido: nada ataría uno a lo otro.

    Y la firma se verifica contra ese id ya comprobado. verify() de coincurve no hashea su
    argumento, le pasa los 32 bytes a secp256k1_schnorrsig_verify, que es lo que pide
    NIP-01: la firma es sobre el id, que ya es un sha256.

    Cualquier evento malformado se rechaza sin más. A lo que llega de un relay no se le
    concede el beneficio de la duda.
    """
    try:
        if evento["pubkey"] != MOSTRO_PUBKEY:
            return False
        if evento["id"] != _id_calculado(evento):
            return False
        return PublicKeyXOnly(bytes.fromhex(evento["pubkey"])).verify(
            bytes.fromhex(evento["sig"]), bytes.fromhex(evento["id"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def _enlace_de(tags, order_id):
    """URL del cliente web para esta oferta, o '' si el evento no trae una creíble.

    El enlace sale tal cual de la etiqueta 'source' que escribe el demonio (mostro,
    src/nip33.rs, create_source_tag) en las ofertas pending; aquí no se construye nunca. Los
    relays y la clave del nodo que lleva dentro son suyos, y escribirlos a mano rompería
    todos los enlaces en silencio el día que cambien en settings.toml.

    Se valida porque el bot no verifica firmas: lee el JSON crudo del relay y se fía del
    filtro 'authors'. Un relay hostil puede contestar con un evento de autor falseado, así
    que solo se acepta un source que apunte a esta misma orden y a nuestro nodo. Lo que se
    publica en un canal abierto no puede acabar apuntando a donde quiera un relay.

    Devuelve '' cuando no hay etiqueta, que es lo normal en un nodo anterior a mostrod
    0.19.0 y en cualquier estado que no sea pending. Entonces el mensaje sale sin enlace.
    """
    source = tags.get("source", [""])[0]
    partes = urllib.parse.urlsplit(source)
    # El demonio escribe 'mostro:<id>?...', con el id en el path. Un 'mostro://<id>' lo
    # dejaría en el host: no es lo que él produce, así que no es nuestro.
    if partes.scheme != "mostro" or partes.netloc or partes.path != order_id:
        return ""
    # parse_qs y no una búsqueda de subcadena. El parser de la app recorre los pares de la
    # query y reasigna el nodo en cada 'mostro=', de modo que gana el último: comprobar que
    # nuestra clave aparece en algún sitio dejaba pasar un
    # 'mostro:<id>?mostro=<nuestra>&mostro=<ajena>' que en la app abre la ajena. Se exige
    # que haya exactamente un mostro= y que sea el nuestro.
    if not MOSTRO_PUBKEY or urllib.parse.parse_qs(partes.query).get("mostro") != [MOSTRO_PUBKEY]:
        return ""
    # Los relays del enlace no se comparan con MOSTRO_RELAY: son los del demonio, que
    # legítimamente publica en más de los que escucha el bot. Tampoco hace falta, porque la
    # app comprueba autor, kind y etiqueta d de lo que devuelva cada uno.
    return f"{MOSTRO_WEB_APP}?link={urllib.parse.quote(source, safe='')}"


def parsear_oferta(evento):
    """Extrae los datos de una oferta de un evento kind 38383."""
    todas_las_etiquetas = evento.get('tags', [])
    tags = {t[0]: t[1:] for t in todas_las_etiquetas if len(t) > 1}

    order_id = tags.get('d', [''])[0]
    estado = tags.get('s', [''])[0].lower()

    if not order_id:
        return None

    tipo = tags.get('k', [''])[0].upper()
    fiat = tags.get('f', [''])[0].upper()
    monto_sats = tags.get('amt', ['0'])[0]
    premium = tags.get('premium', ['0'])[0]
    created_at = evento.get('created_at', 0)
    bond = tags.get('bond', [''])[0] if 'bond' in tags else ''

    fa_datos = tags.get('fa', [])
    if len(fa_datos) > 1:
        monto_fiat = f"{fa_datos[0]} — {fa_datos[1]}"
    elif len(fa_datos) == 1:
        monto_fiat = fa_datos[0]
    else:
        monto_fiat = "Cualquier monto"

    lista_pm = [
        metodo.upper()
        for t in todas_las_etiquetas
        if t[0] == 'pm'
        for metodo in t[1:]
    ]
    metodos_texto = ", ".join(lista_pm) if lista_pm else "No especificado"

    return {
        "order_id": order_id,
        "estado": estado,
        "tipo": tipo,
        "fiat": fiat,
        "monto_sats": monto_sats,
        "premium": premium,
        "created_at": created_at,
        "bond": bond,
        "monto_fiat": monto_fiat,
        "metodos": metodos_texto,
        "enlace": _enlace_de(tags, order_id),
    }


def formato_texto(oferta, html=False):
    """Genera el texto de la oferta. html=True para Telegram, False para texto plano."""
    tipo = oferta["tipo"]
    b = lambda t: f"<b>{t}</b>" if html else t
    i = lambda t: f"<i>{t}</i>" if html else t
    # Todo lo que viene del evento pasa por aquí antes de entrar en el HTML de Telegram: un
    # vendedor puso un '<' en el método de pago y la API rechazó el mensaje entero
    # ("can't parse entities"), así que la oferta no llegó a publicarse. En texto plano
    # (Nostr) escapar convertiría ese mismo '<' en '&lt;' a la vista del lector.
    # str() porque formato_sats() devuelve el valor tal cual si no es un número, y
    # html.escape() reventaría con un None dentro de un mensaje ya casi enviado.
    esc = (lambda t: html_mod.escape(str(t))) if html else (lambda t: t)

    if tipo == "BUY":
        accion = "COMPRA"
        emoji = "🟢"
        desc = f"Alguien quiere {b('comprar')} Bitcoin"
    else:
        accion = "VENTA"
        emoji = "🔴"
        desc = f"Alguien quiere {b('vender')} Bitcoin"

    premium = oferta["premium"]
    try:
        p = float(premium)
        if p > 0:
            premium_txt = f"📈 {b('Premium:')}  +{esc(premium)}%"
        elif p < 0:
            premium_txt = f"📉 {b('Descuento:')}  {esc(premium)}%"
        else:
            sats_fijos = oferta["monto_sats"]
            fiat_fijo = oferta["monto_fiat"]
            if sats_fijos != "0" and fiat_fijo not in ("Cualquier monto",) and "—" not in fiat_fijo:
                try:
                    precio_btc = (float(fiat_fijo) / int(sats_fijos)) * 100_000_000
                    precio_fmt = f"{int(round(precio_btc)):,}".replace(",", ".")
                    premium_txt = f"💲 {b('Precio BTC:')}  {precio_fmt} {esc(oferta['fiat'])}"
                except (ValueError, ZeroDivisionError):
                    premium_txt = f"📊 {b('Premium:')}  Precio de mercado"
            else:
                premium_txt = f"📊 {b('Premium:')}  Precio de mercado"
    except ValueError:
        premium_txt = f"📊 {b('Premium:')}  {esc(premium)}%"

    # En una oferta a precio flotante (amt=0) esta línea decía "A precio de mercado", lo
    # mismo que el premium justo debajo. Se omite: el bloque de premium ya lo dice.
    sats = oferta["monto_sats"]
    sats_txt = f"⚡ {b('Sats:')}  {esc(formato_sats(sats))} sats" if sats != "0" else ""

    # Fecha y hora de la publicación, no un "hace N min": el mensaje se queda fijo en el
    # canal y el tiempo relativo, calculado una sola vez al publicar, envejece mintiendo.
    tiempo = ""
    if oferta["created_at"]:
        try:
            creado = datetime.fromtimestamp(oferta["created_at"], tz=ZONA)
            tiempo = f"{fecha_larga(creado)}, {creado.strftime('%H:%M %Z')}"
        except Exception:
            pass

    if html:
        code = lambda t: f"<code>{t}</code>"
    else:
        code = lambda t: t

    lineas = [
        f"{emoji} {b(f'Nueva oferta #{accion}')}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        desc,
        "",
        f"💰 {b('Fiat:')}  {esc(oferta['monto_fiat'])} {esc(oferta['fiat'])}",
    ]

    if sats_txt:
        lineas.append(sats_txt)

    lineas.append(premium_txt)
    lineas.append(f"🏦 {b('Método:')}  {esc(oferta['metodos'])}")

    if oferta["bond"]:
        lineas.append(f"🔒 {b('Fianza:')}  {esc(oferta['bond'])}%")

    if tiempo:
        lineas.append(f"\n🕐 {i(tiempo)}")

    # En Telegram el enlace tiene que ir en un ancla https. La etiqueta 'source' trae un URI
    # 'mostro:', que la API acepta pero el cliente descarta al pintar: el href desaparece y
    # queda un texto muerto. En Nostr va la URL pelada y en su propia línea, que es como los
    # clientes la enlazan. Se usa get() porque parsear_oferta no siempre ha devuelto la clave.
    enlace = oferta.get("enlace")
    if enlace:
        if html:
            lineas.append(f'\n📱 <a href="{enlace}">Abrir en Mostro</a>')
        else:
            lineas.append(f"\n📱 Abrir en Mostro:\n{enlace}")

    lineas.append(f"\n{code(esc(oferta['order_id']))}")
    lineas.append("━━━━━━━━━━━━━━━━━━━━━")
    lineas.append(f"🧌 {b('NostroMostro')} — Instancia española de Mostro 🇪🇸")

    return "\n".join(lineas)


def _expirada(evento, ahora):
    """True si el evento lleva una etiqueta NIP-40 'expiration' ya vencida."""
    for t in evento.get('tags', []):
        if len(t) > 1 and t[0] == 'expiration':
            try:
                return int(t[1]) <= ahora
            except ValueError:
                return False
    return False


def pending_de(eventos, ahora=None):
    """De una mezcla de eventos kind 38383, las ofertas cuyo estado actual es pending.

    Los eventos llegan de varios relays y cada uno puede ir con retraso, así que de cada
    orden manda la versión más reciente que haya visto cualquiera: si un relay dice pending
    y otro ya tiene el canceled posterior, la oferta está cancelada. También se descartan
    las vencidas por NIP-40, porque basta con que un relay no las purgue para resucitarlas.
    """
    ahora = int(time.time()) if ahora is None else ahora
    ultimo = {}
    for evento in eventos:
        oferta = parsear_oferta(evento)
        if not oferta:
            continue
        previa = ultimo.get(oferta["order_id"])
        if previa is None or oferta["created_at"] > previa[0]["created_at"]:
            ultimo[oferta["order_id"]] = (oferta, evento)
    return {
        order_id: oferta
        for order_id, (oferta, evento) in ultimo.items()
        if oferta["estado"] == "pending" and not _expirada(evento, ahora)
    }


def _solo_autenticos(relay, eventos):
    """Los eventos que el relay devolvió y además firmó nuestro nodo, o None si ninguno.

    Devolver None cuando contestó con eventos y ni uno es válido no es una exquisitez: es
    el camino que obtener_pending ya sabe tratar como "este relay no ha contestado". Si en
    vez de eso se devolviera la lista vacía, un MOSTRO_PUBKEY equivocado —o tres relays
    comprometidos a la vez— harían que obtener_pending viese cero ofertas pending y
    reconciliar borrase el canal entero. Una lista vacía de verdad, porque no había nada
    que devolver, sigue siendo una respuesta legítima y se respeta.
    """
    autenticos = [e for e in eventos if evento_autentico(e)]
    descartados = len(eventos) - len(autenticos)
    if descartados:
        print(f"⚠️ {relay}: {descartados} de {len(eventos)} eventos con firma inválida")
    if eventos and not autenticos:
        print(f"⚠️ {relay}: ninguno válido, lo trato como si no hubiera contestado")
        return None
    return autenticos


def _consultar(relay, filtro, timeout, max_eventos):
    """Eventos que devuelve un relay para un filtro, o None si no llega el EOSE."""
    ws = None
    try:
        ws = websocket.create_connection(relay, timeout=timeout)
        ws.send(json.dumps(["REQ", "scan", filtro]))
        eventos = []
        for _ in range(max_eventos):
            respuesta = json.loads(ws.recv())
            if respuesta[0] == "EVENT":
                eventos.append(respuesta[2])
            elif respuesta[0] == "EOSE":
                return _solo_autenticos(relay, eventos)
        print(f"⚠️ {relay}: demasiados eventos sin EOSE, no me fío del resultado")
        return None
    except Exception as e:
        print(f"⚠️ No se pudo consultar {relay}: {e}")
        return None
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _consultar_todos(relays, filtro, timeout, max_eventos):
    """{relay: eventos} de los relays que contestan, consultados en paralelo.

    En paralelo para que un relay colgado cueste un timeout en total y no uno por relay.
    """
    if not relays:
        return {}
    with ThreadPoolExecutor(max_workers=len(relays)) as pool:
        resultados = pool.map(lambda r: (r, _consultar(r, filtro, timeout, max_eventos)), relays)
        return {r: eventos for r, eventos in resultados if eventos is not None}


def obtener_pending(timeout=15, max_eventos=2000):
    """Ofertas pending según los relays: {order_id: oferta}, o None si no contestó ninguno.

    Sin filtro 'since' a propósito. Los kind 38383 son eventos reemplazables
    parametrizados: el relay solo conserva el último por (autor, kind, d), así que una
    consulta sin ventana temporal da el estado actual y nada más. Con la ventana de 24 h
    que se usaba antes, una oferta viva más antigua parecía no existir.

    Devolver None cuando no contesta ningún relay es deliberado: quien reconcilia tiene que
    poder distinguir "no queda ninguna" de "nadie ha contestado". Confundirlos vaciaría el
    canal entero durante una caída. Basta con que conteste uno.
    """
    # El filtro #s=pending no es un lujo: sin él el relay devuelve el histórico entero y
    # lo corta en su límite por defecto (300 eventos), casi todos ya canceled o success,
    # dejando fuera ofertas vivas. Reconciliar con esa lista truncada borraba del canal
    # ofertas que seguían abiertas. El limit explícito evita depender del que traiga el
    # relay de turno.
    filtro = {"kinds": [38383], "authors": [MOSTRO_PUBKEY], "#s": ["pending"], "limit": 1000}
    respuestas = _consultar_todos(RELAYS, filtro, timeout, max_eventos)
    if not respuestas:
        return None

    eventos = [e for lista in respuestas.values() for e in lista]
    candidatas = list(pending_de(eventos))

    # Un relay atrasado puede seguir dando pending una orden que otro ya tiene cancelada, y
    # el filtro #s=pending esconde justo esa versión más nueva. Se pregunta por las
    # candidatas sin filtrar por estado para que gane siempre la última.
    if candidatas:
        filtro = {"kinds": [38383], "authors": [MOSTRO_PUBKEY], "#d": candidatas}
        confirmacion = _consultar_todos(list(respuestas), filtro, timeout, max_eventos)
        eventos += [e for lista in confirmacion.values() for e in lista]

    return pending_de(eventos)


def reconciliar(publicadas, pending):
    """Compara lo publicado con la realidad del relay: (a_retirar, a_publicar).

    Es el arreglo de una fuga real: los bots solo retiraban una oferta al ver en directo su
    evento de cambio de estado, así que todo lo ocurrido mientras estaban parados se perdía
    para siempre. Y la expiración NIP-40 es silenciosa —el evento simplemente desaparece,
    sin avisar—, de modo que comparar contra el estado actual es la única forma de
    detectarla.
    """
    if pending is None:
        return [], []
    a_retirar = [oid for oid in publicadas if oid not in pending]
    a_publicar = [oid for oid in pending if oid not in publicadas]
    return a_retirar, a_publicar


def filtro_novedades():
    """Función que dice si un evento del directo es nuevo o ya está visto/superado.

    Con varios relays cada evento llega repetido, y uno atrasado puede entregar el pending
    de una orden después del canceled que ya llegó por otro: sin este filtro el bot
    volvería a publicar una oferta cancelada.
    """
    vistos = set()
    ultimo = {}

    def es_nuevo(evento):
        if evento.get("id") in vistos:
            return False
        oferta = parsear_oferta(evento)
        if not oferta:
            return False
        if oferta["created_at"] < ultimo.get(oferta["order_id"], 0):
            return False
        vistos.add(evento.get("id"))
        ultimo[oferta["order_id"]] = oferta["created_at"]
        return True

    return es_nuevo


def _escuchar(relay, on_message, on_open_extra):
    """Bucle de un relay: se suscribe y reconecta para siempre."""

    last_connected = [0]

    def al_abrir(ws):
        since = last_connected[0] if last_connected[0] > 0 else int(time.time()) - 300
        last_connected[0] = int(time.time())
        print(f"📡 Conectado a {relay}")
        suscripcion = [
            "REQ", "mostro_listener",
            {
                "kinds": [38383],
                "authors": [MOSTRO_PUBKEY],
                "since": since
            }
        ]
        ws.send(json.dumps(suscripcion))
        if on_open_extra:
            on_open_extra(ws)

    def al_cerrar(ws, code, msg):
        print(f"⚠️ Conexión con {relay} cerrada. Reconectando en 5s...")
        time.sleep(5)

    def al_error(ws, error):
        print(f"❌ Error WebSocket en {relay}: {error}")

    while True:
        try:
            ws = websocket.WebSocketApp(
                relay,
                on_message=on_message,
                on_open=al_abrir,
                on_close=al_cerrar,
                on_error=al_error
            )
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print(f"❌ Error fatal en {relay}: {e}. Reintentando en 10s...")
            time.sleep(10)


def conectar_relay(on_message, on_open_extra=None):
    """Escucha eventos en todos los relays de Mostro, cada uno en su hilo con keepalive.

    A on_message solo le llegan eventos nuevos (ver filtro_novedades) y de uno en uno: los
    bots modifican su estado y el fichero JSON al procesarlos, y no están hechos para que
    dos hilos lo hagan a la vez.
    """
    es_nuevo = filtro_novedades()
    cerrojo = threading.Lock()

    def al_recibir(ws, mensaje):
        try:
            datos = json.loads(mensaje)
        except ValueError:
            return
        if not datos or datos[0] != "EVENT" or len(datos) < 3:
            return
        # Antes de es_nuevo, no después: filtro_novedades apunta el id de lo que ve, así
        # que dejar entrar un evento falso le permitiría envenenar esa memoria y tapar al
        # auténtico que llegase luego con el mismo id.
        if not evento_autentico(datos[2]):
            print(f"⚠️ {getattr(ws, 'url', '?')}: evento con firma inválida, descartado")
            return
        with cerrojo:
            if es_nuevo(datos[2]):
                on_message(ws, mensaje)

    hilos = [
        threading.Thread(target=_escuchar, args=(relay, al_recibir, on_open_extra), daemon=True)
        for relay in RELAYS
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
