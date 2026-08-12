"""
Cliente HTTP real contra la API de Siigo (https://api.siigo.com).

Trae catálogos maestros de solo lectura (tipos de documento, medios de
pago, comprobantes, impuestos/retenciones) -- confirmado contra la API real
el 2026-07-21 con credenciales de Hielo Super-Cool. También puede CREAR una
compra real (`crear_purchase`, `POST /v1/purchases`) -- ese es el único
punto de todo el proyecto que escribe en la cuenta real de Siigo, y nunca se
llama sin que el usuario haya confirmado explícitamente qué se va a enviar
(ver CLAUDE.md, regla 3; la confirmación vive en
`orquestador.confirmar_envio_siigo`, no acá).

Formas de respuesta confirmadas contra la API real (no de la documentación,
que resultó inconsistente/genérica en varios puntos):
- document-types / payment-types / taxes: lista plana de objetos.
- journals: objeto paginado {"pagination", "results", "_links"} -- la lista
  real está en "results".

El payload de `POST /v1/purchases` está confirmado contra el aplicativo
anterior del usuario ("AXON" original,
`C:\\Users\\User\\Desktop\\Automatizar\\core\\enviar_siigo_individual.py`),
con 2212 compras reales ya sincronizadas -- ver `src/siigo_payload.py` para
cómo se arma.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE_URL = "https://api.siigo.com"


class SiigoError(Exception):
    """Cualquier fallo hablando con Siigo -- credenciales rechazadas, sin
    red, respuesta inesperada. El mensaje ya viene listo para mostrar al
    usuario, nunca expone la credencial usada."""


def _error_de(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode("utf-8", errors="replace")[:300]
    except Exception:
        return str(e)


def autenticar(usuario: str, access_key: str) -> str:
    """Devuelve un access_token (válido ~24h). No se persiste en ningún
    lado -- se pide de nuevo cada vez que el usuario aprieta "Actualizar
    datos", que no es una acción frecuente."""
    body = json.dumps({"username": usuario, "access_key": access_key}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/auth", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SiigoError(f"Siigo rechazó la autenticación (HTTP {e.code}): {_error_de(e)}")
    except urllib.error.URLError as e:
        raise SiigoError(f"No se pudo conectar con Siigo: {e.reason}")

    token = data.get("access_token")
    if not token:
        raise SiigoError("Siigo respondió sin access_token -- revisar usuario/access_key en Conexión Siigo.")
    return token


def _get(path: str, token: str, partner_id: str) -> dict | list:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Partner-Id": partner_id,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SiigoError(f"Siigo respondió HTTP {e.code} en {path}: {_error_de(e)}")
    except urllib.error.URLError as e:
        raise SiigoError(f"No se pudo conectar con Siigo: {e.reason}")


def obtener_document_types(token: str, partner_id: str, tipo: str = "FC") -> list[dict]:
    return _get(f"/v1/document-types?type={tipo}", token, partner_id)


def obtener_payment_types(token: str, partner_id: str, document_type: str = "FC") -> list[dict]:
    return _get(f"/v1/payment-types?document_type={document_type}", token, partner_id)


def obtener_journals(token: str, partner_id: str) -> list[dict]:
    data = _get("/v1/journals", token, partner_id)
    return data.get("results", []) if isinstance(data, dict) else data


def obtener_taxes(token: str, partner_id: str) -> list[dict]:
    return _get("/v1/taxes", token, partner_id)


def obtener_purchases_pagina(
    token: str, partner_id: str, page: int = 1, page_size: int = 100
) -> tuple[list[dict], dict]:
    """Una página de compras YA causadas en Siigo (GET /v1/purchases).

    Confirmado 2026-07-21 contra una cuenta real con 2212 compras: los
    filtros de fecha/proveedor documentados (`created_start`, `date_start`,
    `customer_identification`, etc.) **no filtran nada** en este endpoint --
    siempre devuelven el total completo, sea cual sea el valor. Lo único que
    sí funciona es la paginación, y los resultados vienen ordenados por
    consecutivo/fecha descendente (el más reciente primero) -- por eso el
    filtrado por fecha se hace en `orquestador.buscar_compras_siigo` cortando
    la paginación temprano, no pidiéndoselo a Siigo."""
    data = _get(f"/v1/purchases?page={page}&page_size={page_size}", token, partner_id)
    if isinstance(data, dict):
        return data.get("results", []), data.get("pagination", {})
    return data, {}


def obtener_payment_receipts_pagina(
    token: str, partner_id: str, created_start: str, created_end: str, page: int = 1, page_size: int = 100
) -> tuple[list[dict], dict]:
    """Una página de recibos de pago/egreso YA creados en Siigo (GET
    /v1/payment-receipts), filtrados por fecha de creación -- uso EXCLUSIVO
    de la herramienta de borrado masivo para desarrollo/pruebas
    (orquestador.eliminar_causaciones_periodo): Siigo genera un recibo
    automático al causar una compra con medio de pago 'Pagos por cuenta
    bancaria', y ese recibo hay que borrarlo ANTES de poder borrar la compra.

    `created_start`/`created_end` son los únicos filtros que trae este
    endpoint (no hay filtro por proveedor ni por factura) -- el cruce con la
    compra que se quiere borrar se hace del lado de acá, comparando
    `supplier.identification` y `items[].due.{prefix,consecutive}` contra
    los de la compra. Sin confirmar todavía si estos filtros de fecha
    realmente funcionan contra la API real (ver la advertencia ya
    documentada para /v1/purchases, donde varios filtros documentados no
    filtraban nada) -- por eso el primer uso real se hace con una sola
    factura antes de confiar en esto para un borrado masivo."""
    path = f"/v1/payment-receipts?created_start={created_start}&created_end={created_end}&page={page}&page_size={page_size}"
    data = _get(path, token, partner_id)
    if isinstance(data, dict):
        return data.get("results", []), data.get("pagination", {})
    return data, {}


def eliminar_payment_receipt(token: str, partner_id: str, receipt_id: str) -> None:
    """DELETE /v1/payment-receipts/{id} -- borra un recibo de pago/egreso ya
    creado en la cuenta real de Siigo. Paso obligatorio ANTES de poder
    borrar la compra que lo generó (ver eliminar_purchase) -- Siigo no deja
    borrar una compra mientras tenga un recibo de pago asociado."""
    req = urllib.request.Request(
        f"{BASE_URL}/v1/payment-receipts/{receipt_id}", method="DELETE",
        headers={"Authorization": f"Bearer {token}", "Partner-Id": partner_id},
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            return
    except urllib.error.HTTPError as e:
        raise SiigoError(f"Siigo respondió HTTP {e.code} al eliminar el recibo de pago {receipt_id}: {_error_de(e)}")
    except urllib.error.URLError as e:
        raise SiigoError(f"No se pudo conectar con Siigo: {e.reason}")


def obtener_nombre_proveedor(token: str, partner_id: str, identificacion: str) -> str | None:
    """El endpoint de compras solo trae el NIT del proveedor (`supplier.identification`),
    no el nombre -- hay que resolverlo aparte contra /v1/customers (los
    proveedores viven ahí como 'customers' en el modelo de datos de Siigo)."""
    data = _get(f"/v1/customers?identification={identificacion}", token, partner_id)
    resultados = data.get("results", []) if isinstance(data, dict) else []
    if not resultados:
        return None
    nombre = resultados[0].get("name")
    if isinstance(nombre, list):
        return " ".join(nombre)
    return nombre


def crear_customer(token: str, partner_id: str, payload: dict) -> dict:
    """POST /v1/customers -- crea un tercero (proveedor) en la cuenta real
    de Siigo. Se usa solo desde el flujo de envío confirmado por el usuario
    (orquestador.confirmar_envio_siigo), cuando el NIT del proveedor de una
    factura por enviar todavía no existe en Siigo -- una compra no se puede
    causar contra un tercero inexistente."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/v1/customers", data=data, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Partner-Id": partner_id,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SiigoError(f"Siigo respondió HTTP {e.code} al crear el tercero: {_error_de(e)}")
    except urllib.error.URLError as e:
        raise SiigoError(f"No se pudo conectar con Siigo: {e.reason}")


def _post_purchases(token: str, partner_id: str, payload: dict) -> tuple[int, dict | list | None, str]:
    """POST crudo a /v1/purchases -- devuelve (status, cuerpo_json_o_None,
    texto_crudo) en vez de lanzar en HTTP 4xx, porque `crear_purchase`
    necesita inspeccionar el cuerpo del error (`invalid_total_payments`)
    antes de decidir si reintenta."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/v1/purchases", data=data, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Partner-Id": partner_id,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            texto = resp.read().decode("utf-8")
            return resp.status, (json.loads(texto) if texto else None), texto
    except urllib.error.HTTPError as e:
        texto = e.read().decode("utf-8", errors="replace")
        try:
            cuerpo = json.loads(texto) if texto else None
        except json.JSONDecodeError:
            cuerpo = None
        return e.code, cuerpo, texto
    except urllib.error.URLError as e:
        raise SiigoError(f"No se pudo conectar con Siigo: {e.reason}")


def _total_esperado_por_siigo(cuerpo_error: dict | list | None) -> float | None:
    """Extrae el total que Siigo dice que calculó, del mensaje de error
    'invalid_total_payments' (confirmado contra el aplicativo anterior,
    C:\\...\\Automatizar\\core\\enviar_siigo_individual.py) -- ej.
    "...The total purchase calculated is '119000.00'..."."""
    if not isinstance(cuerpo_error, dict):
        return None
    try:
        mensaje = cuerpo_error.get("errors", [{}])[0].get("message", "")
        if "The total purchase calculated is" not in mensaje:
            return None
        parte = mensaje.split("The total purchase calculated is")[1]
        return float(parte.strip().strip("'\""))
    except (IndexError, KeyError, ValueError, AttributeError):
        return None


def eliminar_purchase(token: str, partner_id: str, purchase_id: str) -> None:
    """DELETE /v1/purchases/{id} -- borra una compra ya causada en la cuenta
    real de Siigo. Uso EXCLUSIVO de corrección de datos ya enviados con un
    valor incorrecto (ej. IVA duplicado por un bug ya corregido en el
    código, confirmado con el usuario antes de tocar nada) -- nunca se llama
    como parte del flujo normal de envío. Igual que `crear_purchase`, no
    pide ninguna confirmación adicional acá: quien llame esto ya debe tener
    la autorización explícita del usuario para ESTA compra puntual."""
    req = urllib.request.Request(
        f"{BASE_URL}/v1/purchases/{purchase_id}", method="DELETE",
        headers={"Authorization": f"Bearer {token}", "Partner-Id": partner_id},
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            return
    except urllib.error.HTTPError as e:
        raise SiigoError(f"Siigo respondió HTTP {e.code} al eliminar la compra {purchase_id}: {_error_de(e)}")
    except urllib.error.URLError as e:
        raise SiigoError(f"No se pudo conectar con Siigo: {e.reason}")


def crear_purchase(token: str, partner_id: str, payload: dict) -> dict:
    """POST /v1/purchases -- el único lugar de todo el proyecto que crea
    algo de verdad en la cuenta real de Siigo. Quien llame esta función es
    responsable de que el usuario ya haya confirmado explícitamente el
    payload exacto (CLAUDE.md regla 3) -- acá no se pide ninguna
    confirmación adicional.

    Reintento de autocorrección (confirmado necesario en producción por el
    aplicativo anterior): si Siigo responde 400 "invalid_total_payments" y
    la diferencia contra el total que dice haber calculado es chica (≤ 5
    pesos, redondeo), se reintenta una sola vez con `payments[0].value`
    ajustado a lo que Siigo espera -- nunca si la diferencia es grande, eso
    sí debe fallar y mostrarse como error real."""
    codigo, cuerpo, texto = _post_purchases(token, partner_id, payload)

    if codigo == 400 and "invalid_total_payments" in texto and payload.get("payments"):
        total_esperado = _total_esperado_por_siigo(cuerpo)
        actual = payload["payments"][0].get("value", 0)
        if total_esperado is not None and abs(total_esperado - actual) <= 5.0:
            payload_reintento = json.loads(json.dumps(payload))
            payload_reintento["payments"][0]["value"] = total_esperado
            codigo, cuerpo, texto = _post_purchases(token, partner_id, payload_reintento)

    if codigo not in (200, 201, 202):
        raise SiigoError(f"Siigo respondió HTTP {codigo} al crear la compra: {texto[:1000]}")
    if not isinstance(cuerpo, dict):
        raise SiigoError(f"Siigo respondió sin un JSON reconocible al crear la compra: {texto[:500]}")
    return cuerpo
