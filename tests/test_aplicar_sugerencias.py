"""
Pruebas de orquestador._aplicar_sugerencias: el enganche entre motor_reglas
(clasifica sin acceso a la base) y motor_sugerencias (sugiere con acceso al
histórico de compras Siigo / preferencias aprendidas) dentro de
ejecutar_importar -- y de cómo eso sube resuelto_por a "historico" sin nunca
tocar "reglas" (esa marca es solo para una regla de negocio confirmada).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orquestador  # noqa: E402
import state_store  # noqa: E402
from dian_parser import parsear_factura  # noqa: E402
from motor_reglas import clasificar_factura  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures-sinteticos"
CONFIG_DIR = Path(__file__).parent.parent / "config"


def _clasificacion_sin_cuenta():
    """NIT ficticio sin config/empresas/<nit>.json -- camino genérico, la
    única línea de este fixture queda con cuenta_contable=None (ver
    test_motor_reglas.py:test_empresa_sin_politica_usa_camino_generico)."""
    factura = parsear_factura((FIXTURES / "iva-no-discriminado-hielo-super-cool.xml").read_bytes())
    return clasificar_factura(factura, nit_empresa="000000000", config_dir=CONFIG_DIR)


def test_sin_historico_ni_aprendizaje_sigue_manual(tmp_path):
    conn = state_store.conectar("000000000", base_dir=tmp_path)
    resultado = _clasificacion_sin_cuenta()

    orquestador._aplicar_sugerencias(conn, resultado)

    assert resultado.items[0].cuenta_contable is None
    assert resultado.resuelto_por == "manual"


def test_historico_completa_la_cuenta_y_sube_a_historico(tmp_path):
    conn = state_store.conectar("000000000", base_dir=tmp_path)
    resultado = _clasificacion_sin_cuenta()
    descripcion = resultado.items[0].descripcion
    proveedor_nit = resultado.factura.proveedor_nit

    state_store.guardar_compras_siigo(conn, [{
        "siigo_id": "siigo-1", "numero": 1, "fecha": "2026-06-01",
        "proveedor_nit": proveedor_nit, "proveedor_nombre": "PROVEEDOR TEST",
        "factura_proveedor": "F1", "total": 1000, "subtotal": 1000,
        "items": [{"descripcion": descripcion, "cuenta_contable": "61302001", "impuestos": []}],
    }])

    orquestador._aplicar_sugerencias(conn, resultado)

    assert resultado.items[0].cuenta_contable == "61302001"
    assert resultado.resuelto_por == "historico"


def test_preferencia_aprendida_tambien_sube_a_historico_no_a_reglas(tmp_path):
    conn = state_store.conectar("000000000", base_dir=tmp_path)
    resultado = _clasificacion_sin_cuenta()
    descripcion = resultado.items[0].descripcion
    proveedor_nit = resultado.factura.proveedor_nit

    import motor_sugerencias
    motor_sugerencias.aprender(conn, "cuenta_contable", proveedor_nit, descripcion, "51950101")

    orquestador._aplicar_sugerencias(conn, resultado)

    assert resultado.items[0].cuenta_contable == "51950101"
    assert resultado.resuelto_por == "historico"  # nunca "reglas": es una sugerencia, no una regla de negocio


def test_asigna_iva_por_tarifa_del_xml_cuando_no_hay_aprendizaje_ni_historico(tmp_path):
    """Caso reportado por el usuario (Distribuidora El Manantial): la
    factura trae una tarifa de IVA (19%, ver dian_parser) pero ni el
    aprendizaje ni el histórico de compras_siigo la resuelven -- debe
    asignarse el código de esa tarifa desde los maestros ya descargados
    (catalogos_siigo), no dejarlo vacío ni caer al 0%."""
    conn = state_store.conectar("000000000", base_dir=tmp_path)
    state_store.guardar_catalogo_siigo(conn, "taxes", [
        {"id": 14139, "name": "IVA 0%", "type": "IVA", "percentage": 0},
        {"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19},
    ])
    resultado = _clasificacion_sin_cuenta()
    assert resultado.items[0].impuestos[0] == {"tipo": "IVA", "porcentaje": 19.0, "valor": 380000.0}

    orquestador._aplicar_sugerencias(conn, resultado)

    assert resultado.items[0].iva_tax_id == "19468"


def test_otros_impuestos_nunca_recibe_iva_ni_retencion_y_hereda_cuenta(tmp_path):
    """Ítem inyectado por motor_reglas para impuestos no mapeados (ver
    test_motor_reglas.test_impuestos_no_mapeados_se_agrupan_en_otros_impuestos):
    nunca debe recibir iva_tax_id/retencion_tax_id, ni siquiera si el
    histórico de compras_siigo lo sugeriría -- y si no hay histórico para su
    cuenta, debe heredar la de las demás líneas cuando todas comparten una."""
    conn = state_store.conectar("000000000", base_dir=tmp_path)
    factura = parsear_factura((FIXTURES / "impuestos-no-mapeados-adv-icl.xml").read_bytes())
    resultado = clasificar_factura(factura, nit_empresa="000000000", config_dir=CONFIG_DIR)
    proveedor_nit = resultado.factura.proveedor_nit

    # Las dos líneas "xml" ya resuelven a la misma cuenta vía histórico.
    state_store.guardar_compras_siigo(conn, [{
        "siigo_id": "siigo-1", "numero": 1, "fecha": "2026-06-01",
        "proveedor_nit": proveedor_nit, "proveedor_nombre": "PROVEEDOR TEST",
        "factura_proveedor": "F1", "total": 1000, "subtotal": 1000,
        "items": [
            {"descripcion": "Producto con ADV (fixture)", "cuenta_contable": "61352001", "impuestos": []},
            {"descripcion": "Producto con ICL (fixture)", "cuenta_contable": "61352001", "impuestos": []},
            # Histórico "malicioso" a propósito: si el motor lo respetara, el
            # ítem OTROS IMPUESTOS terminaría con IVA -- no debe pasar.
            {"descripcion": "OTROS IMPUESTOS", "cuenta_contable": "61352001",
             "impuestos": [{"tipo": "IVA 19%", "porcentaje": 19, "valor": 100}]},
        ],
    }])
    state_store.guardar_catalogo_siigo(conn, "taxes", [
        {"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19},
    ])

    orquestador._aplicar_sugerencias(conn, resultado)

    item_otros = next(i for i in resultado.items if i.origen == "otros_impuestos")
    assert item_otros.cuenta_contable == "61352001"
    assert item_otros.iva_tax_id is None
    assert item_otros.retencion_tax_id is None


def test_otros_impuestos_no_hereda_cuenta_si_las_demas_lineas_difieren(tmp_path):
    """Si las líneas normales de la factura NO comparten una única cuenta,
    no se adivina cuál asignarle a OTROS IMPUESTOS -- queda sin cuenta."""
    conn = state_store.conectar("000000000", base_dir=tmp_path)
    factura = parsear_factura((FIXTURES / "impuestos-no-mapeados-adv-icl.xml").read_bytes())
    resultado = clasificar_factura(factura, nit_empresa="000000000", config_dir=CONFIG_DIR)
    for item in resultado.items:
        if item.origen == "xml":
            item.cuenta_contable = "61352001" if "ADV" in item.descripcion else "61352002"

    orquestador._aplicar_sugerencias(conn, resultado)

    item_otros = next(i for i in resultado.items if i.origen == "otros_impuestos")
    assert item_otros.cuenta_contable is None


def test_iva_no_discriminado_hereda_cuenta_de_la_linea_de_gasto(tmp_path):
    """Caso reportado por el usuario (Hielo Super-Cool): el ítem 'IVA'
    inyectado por la política de empresa nunca debe llevar una cuenta fija
    propia -- debe heredar la misma cuenta que la línea de gasto real de ese
    mismo documento, una vez que esa línea la resuelve el histórico."""
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    factura = parsear_factura((FIXTURES / "iva-no-discriminado-hielo-super-cool.xml").read_bytes())
    resultado = clasificar_factura(factura, nit_empresa="901528790", config_dir=CONFIG_DIR)
    descripcion_linea = next(i for i in resultado.items if i.origen == "xml").descripcion
    proveedor_nit = resultado.factura.proveedor_nit

    state_store.guardar_compras_siigo(conn, [{
        "siigo_id": "siigo-1", "numero": 1, "fecha": "2026-06-01",
        "proveedor_nit": proveedor_nit, "proveedor_nombre": "PROVEEDOR TEST",
        "factura_proveedor": "F1", "total": 1000, "subtotal": 1000,
        "items": [{"descripcion": descripcion_linea, "cuenta_contable": "61201801", "impuestos": []}],
    }])

    orquestador._aplicar_sugerencias(conn, resultado)

    item_gasto = next(i for i in resultado.items if i.origen == "xml")
    item_iva = next(i for i in resultado.items if i.origen == "politica_empresa")
    assert item_gasto.cuenta_contable == "61201801"
    assert item_iva.cuenta_contable == "61201801"


def test_iva_no_discriminado_nunca_recibe_iva_ni_retencion(tmp_path):
    """La política dice literalmente 'sin impuestos asociados a este ítem'
    -- antes de este fix, sugerir_item le clavaba 'IVA 0%' porque
    item.impuestos venía vacío (así se diseñó la política a propósito)."""
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    state_store.guardar_catalogo_siigo(conn, "taxes", [{"id": 14139, "name": "IVA 0%", "type": "IVA", "percentage": 0}])
    factura = parsear_factura((FIXTURES / "iva-no-discriminado-hielo-super-cool.xml").read_bytes())
    resultado = clasificar_factura(factura, nit_empresa="901528790", config_dir=CONFIG_DIR)

    orquestador._aplicar_sugerencias(conn, resultado)

    item_iva = next(i for i in resultado.items if i.origen == "politica_empresa")
    assert item_iva.iva_tax_id is None
    assert item_iva.retencion_tax_id is None


def test_iva_no_discriminado_la_linea_xml_tampoco_recibe_iva_tax_id_del_historico(tmp_path):
    """Bug real confirmado en producción (Hielo Super-Cool, factura 2081 de
    S M BORDADOS Y ESTAMPADOS SAS, julio 2026): bajo la política, la línea
    'xml' ya no lleva IVA propio (se movió al ítem 'IVA' aparte) -- pero si
    el histórico de Siigo para ese proveedor+descripción trae un iva_tax_id
    de ANTES de que la política existiera (con tarifa > 0%, ej. 'IVA Mayor
    valor de costo' 19%), sugerir_item lo seguía proponiendo para la línea.
    Eso duplicaba el IVA: una vez en el ítem 'IVA', otra vez en esta línea.
    La cuenta contable sí debe seguir resolviéndose por histórico -- el fix
    es específico a iva_tax_id, no a todo el resto de la sugerencia."""
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    factura = parsear_factura((FIXTURES / "iva-no-discriminado-hielo-super-cool.xml").read_bytes())
    resultado = clasificar_factura(factura, nit_empresa="901528790", config_dir=CONFIG_DIR)
    descripcion_linea = next(i for i in resultado.items if i.origen == "xml").descripcion
    proveedor_nit = resultado.factura.proveedor_nit

    state_store.guardar_catalogo_siigo(conn, "taxes", [
        {"id": 19468, "name": "IVA Mayor valor de costo", "type": "IVA", "percentage": 19.0},
    ])
    state_store.guardar_compras_siigo(conn, [{
        "siigo_id": "siigo-1", "numero": 1, "fecha": "2026-06-01",
        "proveedor_nit": proveedor_nit, "proveedor_nombre": "PROVEEDOR TEST",
        "factura_proveedor": "F1", "total": 1000, "subtotal": 1000,
        "items": [{
            "descripcion": descripcion_linea, "cuenta_contable": "51055101",
            "impuestos": [{"tipo": "IVA Mayor valor de costo", "porcentaje": 19.0, "valor": 100}],
        }],
    }])

    orquestador._aplicar_sugerencias(conn, resultado)

    item_gasto = next(i for i in resultado.items if i.origen == "xml")
    assert item_gasto.cuenta_contable == "51055101"  # el resto del histórico sí se aplica
    assert item_gasto.iva_tax_id is None  # pero el IVA no, para no duplicarlo


def test_iva_no_discriminado_no_hereda_cuenta_si_la_linea_de_gasto_no_resuelve(tmp_path):
    """Caso real confirmado (K01218074, julio 2026): si la línea de gasto
    sigue sin cuenta, el ítem de IVA tampoco debe quedar con una cuenta --
    nunca cae de vuelta a un valor fijo genérico."""
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    factura = parsear_factura((FIXTURES / "iva-no-discriminado-hielo-super-cool.xml").read_bytes())
    resultado = clasificar_factura(factura, nit_empresa="901528790", config_dir=CONFIG_DIR)

    orquestador._aplicar_sugerencias(conn, resultado)

    item_iva = next(i for i in resultado.items if i.origen == "politica_empresa")
    assert item_iva.cuenta_contable is None


def test_ya_resuelto_por_reglas_no_se_degrada(tmp_path):
    """Si motor_reglas ya dejó todas las cuentas llenas (regla de negocio
    real), _aplicar_sugerencias no debe tocar resuelto_por -- debe seguir
    siendo "reglas", nunca bajar a "historico"."""
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    factura = parsear_factura((FIXTURES / "iva-no-discriminado-hielo-super-cool.xml").read_bytes())
    resultado = clasificar_factura(factura, nit_empresa="901528790", config_dir=CONFIG_DIR)
    for item in resultado.items:
        item.cuenta_contable = "CUENTA-YA-CONFIRMADA"
    resultado.resuelto_por = "reglas"

    orquestador._aplicar_sugerencias(conn, resultado)

    assert resultado.resuelto_por == "reglas"
