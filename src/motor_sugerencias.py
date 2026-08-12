"""
Motor de sugerencias: a diferencia de `motor_reglas.py` (que resuelve por
política de empresa / perfil de proveedor, sin tocar la base de datos), este
módulo sugiere cuenta contable, IVA, retefuente, tipo de comprobante y medio
de pago a partir del histórico de compras ya causadas en Siigo
(`compras_siigo`, ver `src/orquestador.py:descargar_compras_siigo`) y de lo
que el usuario haya corregido a mano en importaciones anteriores
(`sugerencias_aprendidas`). Nunca son reglas de negocio confirmadas -- son
sugerencias que el usuario debe revisar (de ahí `resuelto_por = "historico"`
en `motor_reglas.py`, distinto de `"reglas"`).

Prioridad, de mayor a menor:
  1. Proveedor marcado autorretenedor (`config/proveedores/<nit>.json`,
     `comportamiento.autorretenedor`) -- solo aplica a retefuente, fuerza
     "sin retención" siempre, sin pasar por aprendizaje ni histórico.
  2. Preferencia aprendida (`sugerencias_aprendidas`) -- última corrección
     manual del usuario para ese proveedor(+ítem).
  3. Histórico de compras ya causadas en Siigo (`compras_siigo`) -- match
     exacto por proveedor + descripción normalizada, nunca difuso: mejor no
     sugerir nada que sugerir mal.
  4. Solo IVA: código "IVA 0%" autodetectado del catálogo `taxes` de esa
     empresa (si hay exactamente una tarifa en 0%). La retención NO tiene
     equivalente -- puede quedar sin código a propósito (ver
     docs/08-decisiones-pendientes u orquestador para el porqué: no todo
     catálogo real tiene una tarifa "Retefuente 0%").
  5. Nada -- el campo queda vacío/placeholder, el usuario elige.
"""

from __future__ import annotations

import difflib
import sqlite3

import state_store
from motor_reglas import cargar_config_proveedor

UMBRAL_SIMILITUD_DESCRIPCION = 0.55


def _normalizar(texto: str | None) -> str:
    return (texto or "").strip().upper()


def similitud_descripcion(a: str | None, b: str | None) -> float:
    """0.0 a 1.0 -- qué tan parecidas son dos descripciones de ítem, sin
    exigir igualdad exacta. Usado SOLO por "recalcular sin reimportar"
    (orquestador.buscar_candidatos_recalculo), donde el usuario revisa y
    confirma cada candidato antes de aplicar nada -- nunca por
    `sugerir_item`, que sigue exigiendo igualdad exacta porque ahí no hay
    confirmación humana de por medio (corre solo, al importar).

    Un umbral por similitud de caracteres puede dar falsos positivos (ej.
    "MASILLA GALON" vs "PINTURA GALON" también da ~0.62 solo por compartir
    "GALON") -- por diseño no se filtra más agresivo que eso: es preferible
    mostrar un candidato de más y que el usuario lo destilde, que esconder
    uno real solo porque la redacción varía (tallas, unidades, etc.)."""
    return difflib.SequenceMatcher(None, _normalizar(a), _normalizar(b)).ratio()


def descripciones_similares(a: str | None, b: str | None) -> bool:
    return similitud_descripcion(a, b) >= UMBRAL_SIMILITUD_DESCRIPCION


def es_autorretenedor(proveedor_nit: str) -> bool:
    config = cargar_config_proveedor(proveedor_nit)
    return bool(config.get("comportamiento", {}).get("autorretenedor"))


def _catalogo_taxes(conn: sqlite3.Connection) -> list[dict]:
    return state_store.listar_catalogo_siigo(conn, "taxes")


def resolver_iva_cero(conn: sqlite3.Connection) -> str | None:
    """id del catálogo `taxes` con `type == 'IVA'` y `percentage == 0` --
    `None` si no hay exactamente una (nunca se adivina entre varias)."""
    candidatos = [t for t in _catalogo_taxes(conn) if t.get("type") == "IVA" and t.get("percentage") == 0]
    return str(candidatos[0]["id"]) if len(candidatos) == 1 else None


def _formatear_porcentaje(p: float) -> str:
    return str(int(p)) if float(p) == int(p) else str(p)


def resolver_iva_por_tarifa(conn: sqlite3.Connection, porcentaje: float) -> str | None:
    """id del catálogo `taxes` cuyo `type == 'IVA'` coincida con la tarifa
    que declara el XML -- el eslabón que faltaba cuando ni lo aprendido ni
    el histórico resuelven: si la factura trae IVA del 19%, se asigna el
    código de 19% de los maestros de esa empresa.

    Si hay varias tarifas con el mismo porcentaje (caso real: Hielo tiene
    'IVA 19%', 'IVA Mayor valor de costo' e 'IVA mayor valor del gasto',
    todas al 19%), se prefiere la de nombre estándar 'IVA <p>%'; si ninguna
    se llama así, no se adivina entre variantes contablemente distintas --
    queda para el usuario."""
    candidatos = [
        t for t in _catalogo_taxes(conn)
        if t.get("type") == "IVA" and t.get("percentage") is not None
        and abs(float(t["percentage"]) - float(porcentaje)) < 0.01
    ]
    if len(candidatos) == 1:
        return str(candidatos[0]["id"])
    if len(candidatos) > 1:
        nombre_estandar = f"IVA {_formatear_porcentaje(porcentaje)}%"
        for t in candidatos:
            if _normalizar(t.get("name")) == nombre_estandar.upper():
                return str(t["id"])
    return None


def _resolver_tax_id_por_nombre(catalogo_taxes: list[dict], nombre_impuesto: str, tipo: str) -> str | None:
    """Cruza el nombre de un impuesto tal como viene en el histórico de
    `compras_siigo` (que solo guarda nombre/porcentaje, no `id` -- ver
    `orquestador._mapear_compra_siigo`) contra el catálogo `taxes` actual de
    esa empresa, para poder sugerir el `id` real."""
    for item in catalogo_taxes:
        if item.get("type") == tipo and item.get("name") == nombre_impuesto:
            return str(item["id"])
    return None


def _sugerir_item_por_historico(
    conn: sqlite3.Connection, proveedor_nit: str, descripcion_normalizada: str, catalogo_taxes: list[dict],
) -> dict:
    """Primer ítem de `compras_siigo` de ese proveedor cuya descripción
    coincida exacto (normalizada) -- como el histórico ya viene ordenado por
    fecha descendente, el primer match ya es el más reciente."""
    resultado: dict = {"cuenta_contable": None, "iva_tax_id": None, "retencion_tax_id": None}
    for compra in state_store.compras_siigo_por_proveedor(conn, proveedor_nit):
        for item in compra.get("items", []):
            if _normalizar(item.get("descripcion")) != descripcion_normalizada:
                continue
            resultado["cuenta_contable"] = item.get("cuenta_contable")
            for impuesto in item.get("impuestos", []):
                nombre = impuesto.get("tipo")
                if not nombre:
                    continue
                tax_id_iva = _resolver_tax_id_por_nombre(catalogo_taxes, nombre, "IVA")
                if tax_id_iva:
                    resultado["iva_tax_id"] = tax_id_iva
                    continue
                tax_id_ret = _resolver_tax_id_por_nombre(catalogo_taxes, nombre, "Retefuente")
                if tax_id_ret:
                    resultado["retencion_tax_id"] = tax_id_ret
            return resultado
    return resultado


def sugerir_item(
    conn: sqlite3.Connection, proveedor_nit: str, descripcion: str, porcentaje_iva_xml: float | None = None,
) -> dict:
    """{cuenta_contable, iva_tax_id, retencion_tax_id} sugeridos para una
    línea nueva, aplicando la prioridad de este módulo.

    `porcentaje_iva_xml` es la tarifa de IVA que declara el XML para esta
    línea (None si no declara IVA): cuando ni lo aprendido ni el histórico
    resuelven el código de IVA, se busca en el catálogo la tarifa que
    corresponda a ese porcentaje. El código 'IVA 0%' solo se asigna cuando
    el XML realmente no trae IVA (o lo trae en 0) -- nunca a una línea con
    tarifa mayor, eso enviaría un impuesto que no es."""
    descripcion_norm = _normalizar(descripcion)
    autorretenedor = es_autorretenedor(proveedor_nit)

    cuenta = state_store.obtener_preferencia_aprendida(conn, "cuenta_contable", proveedor_nit, descripcion_norm)
    iva = state_store.obtener_preferencia_aprendida(conn, "iva_tax_id", proveedor_nit, descripcion_norm)
    retencion = None if autorretenedor else state_store.obtener_preferencia_aprendida(
        conn, "retencion_tax_id", proveedor_nit, descripcion_norm
    )

    if cuenta is None or iva is None or (retencion is None and not autorretenedor):
        historico = _sugerir_item_por_historico(conn, proveedor_nit, descripcion_norm, _catalogo_taxes(conn))
        cuenta = cuenta or historico["cuenta_contable"]
        iva = iva or historico["iva_tax_id"]
        if not autorretenedor:
            retencion = retencion or historico["retencion_tax_id"]

    if iva is None:
        if porcentaje_iva_xml is not None and porcentaje_iva_xml > 0:
            iva = resolver_iva_por_tarifa(conn, porcentaje_iva_xml)
        else:
            iva = resolver_iva_cero(conn)

    return {
        "cuenta_contable": cuenta,
        "iva_tax_id": iva,
        "retencion_tax_id": None if autorretenedor else retencion,
    }


def resolver_cabecera_por_empresa(conn: sqlite3.Connection, campo: str) -> str | None:
    """Si dentro de esta empresa SIEMPRE se ha usado el mismo tipo de
    comprobante / medio de pago (un solo valor distinto entre todas las
    compras ya causadas), se usa ese como sugerencia por defecto -- caso
    real confirmado: Hielo Super-Cool usa el mismo id en el 100% de sus
    compras que ya tienen este campo resuelto, aunque el proveedor sea
    nuevo y no tenga preferencia aprendida propia todavía. El valor puede
    ser distinto para otra empresa -- esto nunca compara entre empresas,
    `conn` ya está scoped a una sola. Si hay más de un valor distinto, no se
    adivina cuál -- se deja vacío."""
    valores = state_store.valores_distintos_cabecera(conn, campo)
    return valores[0] if len(valores) == 1 else None


def _resolver_cabecera_por_historico_siigo(conn: sqlite3.Connection, proveedor_nit: str, campo: str) -> str | None:
    """Busca en el histórico real de compras ya causadas en Siigo para ESE
    proveedor (`compras_siigo`, de la más reciente a la más antigua) el
    último valor no nulo de `campo` -- caso real confirmado: Construcciones
    y Adecuaciones ET tiene 78 compras causadas por el aplicativo anterior
    del usuario, con tipo de comprobante y medio de pago reales por
    proveedor, pero sin ninguna preferencia aprendida localmente todavía
    (nunca se causó nada desde este sistema). Match exacto por NIT, nunca
    difuso -- mejor no sugerir nada que sugerir mal."""
    for compra in state_store.compras_siigo_por_proveedor(conn, proveedor_nit):
        valor = compra.get(campo)
        if valor:
            return str(valor)
    return None


def sugerir_cabecera(conn: sqlite3.Connection, proveedor_nit: str) -> dict:
    """{tipo_comprobante_id, medio_pago_id} sugeridos para la cabecera de una
    factura nueva de ese proveedor. Prioridad: preferencia aprendida para
    ESE proveedor primero; si no hay, el histórico real de compras ya
    causadas en Siigo para ese mismo proveedor; si tampoco hay, el valor
    único que ya usa toda la empresa (ver resolver_cabecera_por_empresa)."""
    tipo_comprobante = state_store.obtener_preferencia_aprendida(conn, "tipo_comprobante_id", proveedor_nit, None)
    medio_pago = state_store.obtener_preferencia_aprendida(conn, "medio_pago_id", proveedor_nit, None)
    if tipo_comprobante is None:
        tipo_comprobante = _resolver_cabecera_por_historico_siigo(conn, proveedor_nit, "tipo_comprobante_id")
    if medio_pago is None:
        medio_pago = _resolver_cabecera_por_historico_siigo(conn, proveedor_nit, "medio_pago_id")
    if tipo_comprobante is None:
        tipo_comprobante = resolver_cabecera_por_empresa(conn, "tipo_comprobante_id")
    if medio_pago is None:
        medio_pago = resolver_cabecera_por_empresa(conn, "medio_pago_id")
    return {"tipo_comprobante_id": tipo_comprobante, "medio_pago_id": medio_pago}


def aprender(
    conn: sqlite3.Connection, campo: str, proveedor_nit: str, item_descripcion: str | None, valor: str | None,
) -> None:
    """Recuerda una corrección manual del usuario para la próxima
    importación. No guarda nada si `valor` es `None` -- limpiar un campo no
    es una preferencia que valga la pena recordar."""
    if valor is None:
        return
    descripcion_norm = _normalizar(item_descripcion) if item_descripcion else None
    state_store.guardar_preferencia_aprendida(conn, campo, proveedor_nit, descripcion_norm, valor)
