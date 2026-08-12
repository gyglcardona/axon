"""
Pruebas de motor_sugerencias.py: sugerencia de cuenta/IVA/retefuente por
línea y de tipo de comprobante/medio de pago por cabecera, con la prioridad
autorretenedor > preferencia aprendida > histórico de compras_siigo > IVA 0%
autodetectado > nada. Nunca llama a Siigo ni a la red -- todo se arma sobre
un state_store.conectar() en tmp_path.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import motor_sugerencias  # noqa: E402
import state_store  # noqa: E402
from dian_parser import FacturaDian  # noqa: E402
from motor_reglas import ItemSiigo, ResultadoClasificacion  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    return state_store.conectar("900000000", base_dir=tmp_path)


def _sembrar_compra_con_cabecera(conn, cufe, tipo_comprobante_id, medio_pago_id, proveedor_nit="900999"):
    """Inserta una compra mínima ya causada con cabecera resuelta -- para
    probar resolver_cabecera_por_empresa/valores_distintos_cabecera, que leen
    directo de `compras`, no de sugerencias_aprendidas ni compras_siigo."""
    factura = FacturaDian(
        cufe=cufe, numero_factura=cufe, prefijo="F", numero_puro=cufe, fecha_emision="2026-06-01",
        proveedor_nombre="PROVEEDOR TEST", proveedor_nit=proveedor_nit,
        proveedor_correo=None, proveedor_direccion=None,
        subtotal_xml=1000, subtotal_fuente="TaxExclusiveAmount", total_pagar_xml=1190,
    )
    resultado = ResultadoClasificacion(
        factura=factura, items=[ItemSiigo(descripcion="X", cantidad=1, valor_unitario=1000, cuenta_contable="61")],
        resuelto_por="reglas", tipo_comprobante_id=tipo_comprobante_id, medio_pago_id=medio_pago_id,
    )
    state_store.guardar_resultado(conn, resultado, archivo_origen=Path("x.zip"))


def _guardar_catalogo_taxes(conn, items):
    state_store.guardar_catalogo_siigo(conn, "taxes", items)


def _guardar_historico(conn, proveedor_nit, items, numero=1, fecha="2026-06-01", reemplazar_todo=True):
    state_store.guardar_compras_siigo(conn, [{
        "siigo_id": f"siigo-{proveedor_nit}-{numero}", "numero": numero, "fecha": fecha,
        "proveedor_nit": proveedor_nit, "proveedor_nombre": "PROVEEDOR TEST",
        "factura_proveedor": "F1", "total": 1000, "subtotal": 1000,
        "items": items,
    }], reemplazar_todo=reemplazar_todo)


def test_sin_nada_no_sugiere_nada(conn):
    resultado = motor_sugerencias.sugerir_item(conn, "900111", "TORNILLOS")
    assert resultado == {"cuenta_contable": None, "iva_tax_id": None, "retencion_tax_id": None}


def test_resolver_iva_cero_encuentra_la_tarifa_en_0(conn):
    _guardar_catalogo_taxes(conn, [
        {"id": 14139, "name": "IVA 0%", "type": "IVA", "percentage": 0},
        {"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19},
    ])
    assert motor_sugerencias.resolver_iva_cero(conn) == "14139"


def test_resolver_iva_cero_ambiguo_no_adivina(conn):
    _guardar_catalogo_taxes(conn, [
        {"id": 1, "name": "IVA 0% A", "type": "IVA", "percentage": 0},
        {"id": 2, "name": "IVA 0% B", "type": "IVA", "percentage": 0},
    ])
    assert motor_sugerencias.resolver_iva_cero(conn) is None


def test_resolver_iva_cero_sin_ninguna_tarifa_en_0(conn):
    _guardar_catalogo_taxes(conn, [{"id": 1, "name": "IVA 19%", "type": "IVA", "percentage": 19}])
    assert motor_sugerencias.resolver_iva_cero(conn) is None


def test_resolver_iva_por_tarifa_encuentra_el_codigo_exacto(conn):
    _guardar_catalogo_taxes(conn, [
        {"id": 14139, "name": "IVA 0%", "type": "IVA", "percentage": 0},
        {"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19},
        {"id": 7823, "name": "IVA 5%", "type": "IVA", "percentage": 5},
    ])
    assert motor_sugerencias.resolver_iva_por_tarifa(conn, 19) == "19468"
    assert motor_sugerencias.resolver_iva_por_tarifa(conn, 5) == "7823"


def test_resolver_iva_por_tarifa_sin_coincidencia_no_adivina(conn):
    _guardar_catalogo_taxes(conn, [{"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19}])
    assert motor_sugerencias.resolver_iva_por_tarifa(conn, 5) is None


def test_resolver_iva_por_tarifa_con_varias_variantes_prefiere_el_nombre_estandar(conn):
    """Caso real de Hielo Super-Cool: tres tarifas al 19% ('IVA 19%', 'IVA
    Mayor valor de costo', 'IVA mayor valor del gasto') -- son contablemente
    distintas, así que solo se elige automáticamente la de nombre estándar
    'IVA 19%'; las otras dos exigen selección manual."""
    _guardar_catalogo_taxes(conn, [
        {"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19},
        {"id": 19469, "name": "IVA Mayor valor de costo", "type": "IVA", "percentage": 19},
        {"id": 19475, "name": "IVA mayor valor del gasto", "type": "IVA", "percentage": 19},
    ])
    assert motor_sugerencias.resolver_iva_por_tarifa(conn, 19) == "19468"


def test_resolver_iva_por_tarifa_ambiguo_sin_nombre_estandar_no_adivina(conn):
    _guardar_catalogo_taxes(conn, [
        {"id": 1, "name": "IVA raro A", "type": "IVA", "percentage": 19},
        {"id": 2, "name": "IVA raro B", "type": "IVA", "percentage": 19},
    ])
    assert motor_sugerencias.resolver_iva_por_tarifa(conn, 19) is None


def test_sugerir_item_asigna_iva_por_tarifa_del_xml(conn):
    """El caso reportado por el usuario (Distribuidora El Manantial): el XML
    trae una tarifa de IVA pero ni el aprendizaje ni el histórico la
    resuelven -- se debe asignar el código de esa tarifa de los maestros,
    no dejarla vacía ni caer al 0%."""
    _guardar_catalogo_taxes(conn, [
        {"id": 14139, "name": "IVA 0%", "type": "IVA", "percentage": 0},
        {"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19},
    ])
    resultado = motor_sugerencias.sugerir_item(conn, "900111", "PRODUCTO X", porcentaje_iva_xml=19)
    assert resultado["iva_tax_id"] == "19468"


def test_sugerir_item_con_iva_xml_en_cero_usa_iva_cero_no_por_tarifa(conn):
    _guardar_catalogo_taxes(conn, [{"id": 14139, "name": "IVA 0%", "type": "IVA", "percentage": 0}])
    resultado = motor_sugerencias.sugerir_item(conn, "900111", "PRODUCTO EXENTO", porcentaje_iva_xml=0)
    assert resultado["iva_tax_id"] == "14139"


def test_sugerir_item_preferencia_aprendida_gana_sobre_tarifa_del_xml(conn):
    _guardar_catalogo_taxes(conn, [{"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19}])
    motor_sugerencias.aprender(conn, "iva_tax_id", "900111", "PRODUCTO X", "OTRO-CODIGO")
    resultado = motor_sugerencias.sugerir_item(conn, "900111", "PRODUCTO X", porcentaje_iva_xml=19)
    assert resultado["iva_tax_id"] == "OTRO-CODIGO"


def test_sugerir_item_usa_iva_cero_cuando_no_hay_otra_fuente(conn):
    _guardar_catalogo_taxes(conn, [{"id": 14139, "name": "IVA 0%", "type": "IVA", "percentage": 0}])
    resultado = motor_sugerencias.sugerir_item(conn, "900111", "SERVICIO SIN IVA")
    assert resultado["iva_tax_id"] == "14139"
    # a diferencia del IVA, la retención no tiene un equivalente "0%" que se
    # autodetecte -- puede quedar sin código a propósito (ver decisión de
    # diseño: catálogo real de Hielo Super-Cool no tiene "Retefuente 0%").
    assert resultado["retencion_tax_id"] is None


def test_sugerir_item_por_historico_match_exacto_de_descripcion(conn):
    _guardar_catalogo_taxes(conn, [
        {"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19},
        {"id": 10065, "name": "Retefuente 3.5%", "type": "Retefuente", "percentage": 3.5},
    ])
    _guardar_historico(conn, "900111", [{
        "descripcion": "masilla galon", "cuenta_contable": "61302001",
        "impuestos": [
            {"tipo": "IVA 19%", "porcentaje": 19, "valor": 19000},
            {"tipo": "Retefuente 3.5%", "porcentaje": 3.5, "valor": 3500},
        ],
    }])

    resultado = motor_sugerencias.sugerir_item(conn, "900111", "  Masilla Galon  ")

    assert resultado["cuenta_contable"] == "61302001"
    assert resultado["iva_tax_id"] == "19468"
    assert resultado["retencion_tax_id"] == "10065"


def test_sugerir_item_por_historico_no_hace_match_difuso(conn):
    """Mejor no sugerir nada que sugerir mal -- sin match exacto de
    descripción (normalizada), no se adivina."""
    _guardar_historico(conn, "900111", [{
        "descripcion": "masilla galon", "cuenta_contable": "61302001", "impuestos": [],
    }])
    resultado = motor_sugerencias.sugerir_item(conn, "900111", "masilla medio galon")
    assert resultado["cuenta_contable"] is None


def test_historico_usa_la_compra_mas_reciente(conn):
    _guardar_historico(conn, "900111", [{
        "descripcion": "tornillos", "cuenta_contable": "CUENTA-VIEJA", "impuestos": [],
    }], numero=1, fecha="2026-01-01")
    _guardar_historico(conn, "900111", [{
        "descripcion": "tornillos", "cuenta_contable": "CUENTA-NUEVA", "impuestos": [],
    }], numero=2, fecha="2026-06-01", reemplazar_todo=False)

    resultado = motor_sugerencias.sugerir_item(conn, "900111", "tornillos")
    assert resultado["cuenta_contable"] == "CUENTA-NUEVA"


def test_preferencia_aprendida_tiene_prioridad_sobre_historico(conn):
    _guardar_historico(conn, "900111", [{
        "descripcion": "tornillos", "cuenta_contable": "CUENTA-HISTORICA", "impuestos": [],
    }])
    motor_sugerencias.aprender(conn, "cuenta_contable", "900111", "tornillos", "CUENTA-APRENDIDA")

    resultado = motor_sugerencias.sugerir_item(conn, "900111", "tornillos")
    assert resultado["cuenta_contable"] == "CUENTA-APRENDIDA"


def test_aprender_no_guarda_si_valor_es_none(conn):
    motor_sugerencias.aprender(conn, "cuenta_contable", "900111", "tornillos", None)
    assert state_store.obtener_preferencia_aprendida(conn, "cuenta_contable", "900111", "TORNILLOS") is None


def test_es_autorretenedor_lee_perfil_de_proveedor(monkeypatch):
    monkeypatch.setattr(motor_sugerencias, "cargar_config_proveedor", lambda nit: {"comportamiento": {"autorretenedor": True}})
    assert motor_sugerencias.es_autorretenedor("900111") is True

    monkeypatch.setattr(motor_sugerencias, "cargar_config_proveedor", lambda nit: {})
    assert motor_sugerencias.es_autorretenedor("900111") is False


def test_autorretenedor_nunca_sugiere_retencion_aunque_haya_historico_y_aprendizaje(conn, monkeypatch):
    monkeypatch.setattr(
        motor_sugerencias, "cargar_config_proveedor", lambda nit: {"comportamiento": {"autorretenedor": True}}
    )
    _guardar_catalogo_taxes(conn, [{"id": 10065, "name": "Retefuente 3.5%", "type": "Retefuente", "percentage": 3.5}])
    motor_sugerencias.aprender(conn, "retencion_tax_id", "900111", "tornillos", "10065")
    _guardar_historico(conn, "900111", [{
        "descripcion": "tornillos", "cuenta_contable": "CUENTA-X",
        "impuestos": [{"tipo": "Retefuente 3.5%", "porcentaje": 3.5, "valor": 100}],
    }])

    resultado = motor_sugerencias.sugerir_item(conn, "900111", "tornillos")

    assert resultado["retencion_tax_id"] is None
    assert resultado["cuenta_contable"] == "CUENTA-X"  # los demás campos siguen su flujo normal


def test_sugerir_cabecera_usa_preferencia_aprendida(conn):
    assert motor_sugerencias.sugerir_cabecera(conn, "900111") == {"tipo_comprobante_id": None, "medio_pago_id": None}

    motor_sugerencias.aprender(conn, "tipo_comprobante_id", "900111", None, "18679")
    motor_sugerencias.aprender(conn, "medio_pago_id", "900111", None, "8729")

    assert motor_sugerencias.sugerir_cabecera(conn, "900111") == {
        "tipo_comprobante_id": "18679", "medio_pago_id": "8729",
    }


def test_sugerir_cabecera_usa_historico_de_compras_siigo_del_proveedor(conn):
    """Bug real confirmado en producción (Construcciones y Adecuaciones ET):
    78 compras ya causadas en Siigo por el aplicativo anterior del usuario,
    con tipo de comprobante y medio de pago reales por proveedor, pero sin
    ninguna preferencia aprendida localmente (nunca se causó nada desde este
    sistema) -- antes de este fix, `_mapear_compra_siigo` descartaba esos
    campos al cachear y `sugerir_cabecera` no tenía de dónde sacarlos."""
    state_store.guardar_compras_siigo(conn, [{
        "siigo_id": "siigo-1", "numero": 1, "fecha": "2026-06-01",
        "proveedor_nit": "900111", "proveedor_nombre": "PROVEEDOR TEST",
        "factura_proveedor": "F1", "total": 1000, "subtotal": 1000, "items": [],
        "tipo_comprobante_id": "18484", "medio_pago_id": "4263",
    }])

    assert motor_sugerencias.sugerir_cabecera(conn, "900111") == {
        "tipo_comprobante_id": "18484", "medio_pago_id": "4263",
    }


def test_sugerir_cabecera_preferencia_aprendida_gana_al_historico_de_siigo(conn):
    state_store.guardar_compras_siigo(conn, [{
        "siigo_id": "siigo-1", "numero": 1, "fecha": "2026-06-01",
        "proveedor_nit": "900111", "proveedor_nombre": "PROVEEDOR TEST",
        "factura_proveedor": "F1", "total": 1000, "subtotal": 1000, "items": [],
        "tipo_comprobante_id": "18484", "medio_pago_id": "4263",
    }])
    motor_sugerencias.aprender(conn, "tipo_comprobante_id", "900111", None, "77777")

    assert motor_sugerencias.sugerir_cabecera(conn, "900111") == {
        "tipo_comprobante_id": "77777", "medio_pago_id": "4263",
    }


def test_sugerir_cabecera_historico_de_siigo_gana_al_valor_de_empresa(conn):
    """El histórico REAL de ese proveedor específico en Siigo es más
    confiable que el valor genérico "toda la empresa usa lo mismo" -- se
    prueba primero."""
    _sembrar_compra_con_cabecera(conn, "CUFE-1", "18679", "4308", proveedor_nit="900999")
    state_store.guardar_compras_siigo(conn, [{
        "siigo_id": "siigo-1", "numero": 1, "fecha": "2026-06-01",
        "proveedor_nit": "900111", "proveedor_nombre": "PROVEEDOR TEST",
        "factura_proveedor": "F1", "total": 1000, "subtotal": 1000, "items": [],
        "tipo_comprobante_id": "18484", "medio_pago_id": "4263",
    }])

    assert motor_sugerencias.sugerir_cabecera(conn, "900111") == {
        "tipo_comprobante_id": "18484", "medio_pago_id": "4263",
    }


def test_resolver_cabecera_por_empresa_con_un_solo_valor_distinto(conn):
    """Caso real: Hielo Super-Cool usa el mismo tipo de comprobante y medio
    de pago en el 100% de sus compras ya causadas."""
    _sembrar_compra_con_cabecera(conn, "CUFE-1", "18679", "4308")
    _sembrar_compra_con_cabecera(conn, "CUFE-2", "18679", "4308")

    assert motor_sugerencias.resolver_cabecera_por_empresa(conn, "tipo_comprobante_id") == "18679"
    assert motor_sugerencias.resolver_cabecera_por_empresa(conn, "medio_pago_id") == "4308"


def test_resolver_cabecera_por_empresa_no_adivina_con_varios_valores(conn):
    _sembrar_compra_con_cabecera(conn, "CUFE-1", "18679", "4308")
    _sembrar_compra_con_cabecera(conn, "CUFE-2", "99999", "4308")

    assert motor_sugerencias.resolver_cabecera_por_empresa(conn, "tipo_comprobante_id") is None
    assert motor_sugerencias.resolver_cabecera_por_empresa(conn, "medio_pago_id") == "4308"


def test_resolver_cabecera_por_empresa_sin_datos_da_none(conn):
    assert motor_sugerencias.resolver_cabecera_por_empresa(conn, "tipo_comprobante_id") is None


def test_sugerir_cabecera_cae_al_valor_de_empresa_si_no_hay_preferencia_del_proveedor(conn):
    """Proveedor nuevo, sin preferencia aprendida propia -- debe heredar el
    valor que ya usa el resto de la empresa, no quedar vacío."""
    _sembrar_compra_con_cabecera(conn, "CUFE-1", "18679", "4308", proveedor_nit="900999")

    assert motor_sugerencias.sugerir_cabecera(conn, "PROVEEDOR-NUEVO-SIN-HISTORIA") == {
        "tipo_comprobante_id": "18679", "medio_pago_id": "4308",
    }


def test_sugerir_cabecera_preferencia_aprendida_gana_al_valor_de_empresa(conn):
    _sembrar_compra_con_cabecera(conn, "CUFE-1", "18679", "4308", proveedor_nit="900999")
    motor_sugerencias.aprender(conn, "tipo_comprobante_id", "900111", None, "77777")

    assert motor_sugerencias.sugerir_cabecera(conn, "900111") == {
        "tipo_comprobante_id": "77777", "medio_pago_id": "4308",
    }
