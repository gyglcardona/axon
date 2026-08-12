"""
Pruebas del motor de reglas -- en particular, la política de empresa de
Hielo Super-Cool (IVA no discriminado), que fue el caso que originó el
diseño de dos niveles de reglas (empresa vs. proveedor).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dian_parser import parsear_factura  # noqa: E402
from motor_reglas import clasificar_factura  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures-sinteticos"
CONFIG_DIR = Path(__file__).parent.parent / "config"


def _leer(nombre: str) -> bytes:
    return (FIXTURES / nombre).read_bytes()


def test_politica_iva_no_discriminado_mueve_el_iva_a_un_item():
    factura = parsear_factura(_leer("iva-no-discriminado-hielo-super-cool.xml"))

    resultado = clasificar_factura(factura, nit_empresa="901528790", config_dir=CONFIG_DIR)

    # La línea original no debe llevar impuestos.
    item_original = next(i for i in resultado.items if i.origen == "xml")
    assert item_original.impuestos == []

    # Debe existir un ítem adicional inyectado por la política, con el valor
    # exacto del IVA que traía el XML (380000, ver fixture).
    item_iva = next(i for i in resultado.items if i.origen == "politica_empresa")
    assert item_iva.valor_unitario == 380000
    assert item_iva.descripcion == "IVA"

    # motor_reglas nunca le asigna una cuenta fija propia -- la hereda de la
    # línea de gasto de este mismo documento, y eso lo resuelve
    # orquestador._aplicar_sugerencias, no motor_reglas (ver
    # test_aplicar_sugerencias.test_iva_no_discriminado_hereda_cuenta_de_la_linea_de_gasto).
    assert item_iva.cuenta_contable is None


def test_politica_iva_no_discriminado_no_duplica_el_iva_en_factura_multilinea():
    """Bug real confirmado en producción (GL30644, GLOBAL TOOLS S.A.S.,
    julio 2026): cuando el XML declara IVA por línea, el TaxTotal de
    documento es la SUMA de esas líneas, no un impuesto aparte -- sumar
    documento + líneas (como hacía antes vía FacturaDian.total_por_tipo)
    duplicaba el ítem de IVA inyectado (llegó a insertarse $13.251 en vez de
    $6.626, el doble exacto)."""
    factura = parsear_factura(_leer("iva-no-discriminado-multilinea-hielo.xml"))

    resultado = clasificar_factura(factura, nit_empresa="901528790", config_dir=CONFIG_DIR)

    item_iva = next(i for i in resultado.items if i.origen == "politica_empresa")
    # Suma exacta de líneas (958+1197+2698+942+830=6625) -- NO 13251 (el doble,
    # bug real) ni 6626 (el total de documento, que trae un redondeo de más).
    assert item_iva.valor_unitario == 6625


def test_politica_iva_no_discriminado_no_duplica_con_precio_con_iva_incluido():
    """Bug real confirmado en producción (TECNOMEDICA MD, TORACHE, julio
    2026): el proveedor trae Price con IVA incluido -- si motor_reglas usara
    ese precio tal cual, el total enviado a Siigo quedaría inflado por el
    IVA DOS veces (una en el precio del ítem, otra en el ítem 'IVA' que
    agrega la política)."""
    factura = parsear_factura(_leer("precio-con-iva-incluido.xml"))

    resultado = clasificar_factura(factura, nit_empresa="901528790", config_dir=CONFIG_DIR)

    item_gasto = next(i for i in resultado.items if i.origen == "xml")
    item_iva = next(i for i in resultado.items if i.origen == "politica_empresa")
    assert item_gasto.valor_unitario == 378.0
    assert item_iva.valor_unitario == 72.0
    # la suma de ambos ítems debe cuadrar con el total real de la factura (450), no con 522 (450+72)
    assert item_gasto.valor_unitario * item_gasto.cantidad + item_iva.valor_unitario == 450.0


def test_empresa_sin_politica_usa_camino_generico():
    """Una empresa sin la política activa debe mapear el IVA normalmente,
    dentro del ítem, no como línea aparte. En este fixture el IVA viene
    declarado a nivel de documento (no de línea) -- como la factura tiene
    una sola línea, debe atribuírsele completo."""
    factura = parsear_factura(_leer("iva-no-discriminado-hielo-super-cool.xml"))

    # NIT ficticio sin config/empresas/<nit>.json -- debe caer al camino genérico.
    resultado = clasificar_factura(factura, nit_empresa="000000000", config_dir=CONFIG_DIR)

    assert len(resultado.items) == 1  # una sola línea, no se inyectó ítem de IVA
    assert resultado.items[0].impuestos[0]["tipo"] == "IVA"
    assert resultado.items[0].impuestos[0]["valor"] == 380000
    assert any("nivel de documento" in n for n in resultado.notas)


def test_impuestos_no_mapeados_se_agrupan_en_otros_impuestos():
    """Caso reportado por el usuario con facturas reales de QUALA: códigos de
    esquema que dian_parser no reconoce (ADV=32, ICL=36) no deben perderse
    silenciosamente -- se sacan de sus líneas y se agrupan en un único ítem
    'OTROS IMPUESTOS', cantidad 1, sin IVA ni retención, pero CON el
    desglose por tipo conservado en sus impuestos (agosto 2026: antes se
    perdía por completo -- ver contai_export.cuentas_impuesto_por_tipo,
    que necesita saber cuánto es de cada tipo, no solo el total)."""
    factura = parsear_factura(_leer("impuestos-no-mapeados-adv-icl.xml"))

    resultado = clasificar_factura(factura, nit_empresa="000000000", config_dir=CONFIG_DIR)

    items_xml = [i for i in resultado.items if i.origen == "xml"]
    assert len(items_xml) == 2
    # El IVA de la línea 1 se conserva; el ADV/ICL no.
    assert [imp["tipo"] for imp in items_xml[0].impuestos] == ["IVA"]
    assert items_xml[1].impuestos == []

    item_otros = next(i for i in resultado.items if i.origen == "otros_impuestos")
    assert item_otros.descripcion == "OTROS IMPUESTOS"
    assert item_otros.cantidad == 1
    assert item_otros.valor_unitario == 8000  # 5000 (ADV) + 3000 (ICL)
    assert {imp["tipo"]: imp["valor"] for imp in item_otros.impuestos} == {
        "ESQUEMA_32": 5000.0, "ESQUEMA_36": 3000.0,
    }
    assert item_otros.iva_tax_id is None
    assert item_otros.retencion_tax_id is None
    assert any("no reconoce" in n for n in resultado.notas)


def test_impuesto_reconocido_pero_sin_codigo_siigo_se_agrupa_en_otros_impuestos():
    """Bug real confirmado en producción (SKY CORD S.A.S., factura FESC50872,
    tipo IC $70; COMCEL, factura E6083351704, tipo INC $673,62): estos
    impuestos SÍ tienen nombre reconocido por dian_parser (no son
    "ESQUEMA_X"), pero como no son IVA ni retención, no había ningún
    mecanismo que les resolviera un código de impuesto Siigo -- se perdían
    silenciosamente. Deben agruparse en 'OTROS IMPUESTOS' igual que los
    esquemas no reconocidos."""
    factura = parsear_factura(_leer("impuesto-ic-inc-no-mapeado-a-siigo.xml"))

    resultado = clasificar_factura(factura, nit_empresa="000000000", config_dir=CONFIG_DIR)

    item_linea = next(i for i in resultado.items if i.origen == "xml")
    assert [imp["tipo"] for imp in item_linea.impuestos] == ["IVA"]  # el IC se sacó, el IVA se conserva

    item_otros = next(i for i in resultado.items if i.origen == "otros_impuestos")
    assert item_otros.valor_unitario == 70.0
    assert item_otros.iva_tax_id is None
    assert item_otros.retencion_tax_id is None


def test_sin_impuestos_no_mapeados_no_agrega_item_otros():
    factura = parsear_factura(_leer("iva-multilinea.xml"))

    resultado = clasificar_factura(factura, nit_empresa="000000000", config_dir=CONFIG_DIR)

    assert all(i.origen != "otros_impuestos" for i in resultado.items)


def test_resuelto_por_es_manual_mientras_falte_cuenta_contable():
    """Todavía no existe el motor de predicción de cuentas (era
    predictor_manager.py en el prototipo anterior) -- hasta que exista,
    todo debe quedar honestamente marcado como 'manual', nunca 'reglas'."""
    factura = parsear_factura(_leer("iva-no-discriminado-hielo-super-cool.xml"))
    resultado = clasificar_factura(factura, nit_empresa="901528790", config_dir=CONFIG_DIR)

    assert resultado.resuelto_por == "manual"
