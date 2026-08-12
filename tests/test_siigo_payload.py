"""
Pruebas de src/siigo_payload.py: cómo se arma el JSON real de
POST /v1/purchases a partir de una factura ya resuelta. La forma está
confirmada contra el aplicativo anterior del usuario ("AXON" original,
C:\\Users\\User\\Desktop\\Automatizar\\core\\enviar_siigo_individual.py),
con 2212 compras reales ya sincronizadas -- no contra la documentación
oficial de Siigo, que ni siquiera aclara si items.taxes acepta retenciones
(sí las acepta, confirmado).

Nunca toca la red -- construir_payload es una función pura.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import siigo_payload  # noqa: E402

CATALOGO_TAXES = [
    {"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19.0},
    {"id": 14139, "name": "IVA 0%", "type": "IVA", "percentage": 0.0},
    {"id": 10065, "name": "Retefuente 3.5%", "type": "Retefuente", "percentage": 3.5},
]


def _item(**overrides):
    base = {
        "id": 1, "descripcion": "TORNILLOS", "cantidad": 2, "valor_unitario": 1000,
        "cuenta_contable": "51950101", "iva_tax_id": None, "retencion_tax_id": None,
        "descuento_monto": 0, "tipo_item": "Account", "origen": "xml", "impuestos": [],
    }
    base.update(overrides)
    return base


def _factura(items, **overrides):
    base = {
        "cufe": "CUFE-1", "numero_factura": "F1", "prefijo": "F", "numero_puro": "1",
        "fecha_emision": "2026-07-01", "proveedor_nit": "900111222", "proveedor_nombre": "PROVEEDOR TEST",
        "total_pagar_xml": 2000, "tipo_comprobante_id": "18679", "medio_pago_id": "8729",
        "items": items,
    }
    base.update(overrides)
    return base


def test_iva_se_toma_del_monto_declarado_en_el_xml_no_se_recalcula():
    item = _item(
        cantidad=1, valor_unitario=100000, iva_tax_id="19468",
        impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.5}],  # valor "raro" a propósito
    )
    r = siigo_payload.construir_payload(_factura([item]), CATALOGO_TAXES)

    assert r["motivos_bloqueo"] == []
    taxes = r["payload"]["items"][0]["taxes"]
    assert taxes == [{"id": 19468, "value": 19000.5}]  # el valor exacto del XML, no 100000*0.19


def test_iva_se_calcula_por_porcentaje_si_no_viene_declarado_en_el_xml():
    item = _item(cantidad=1, valor_unitario=100000, iva_tax_id="19468", impuestos=[])
    r = siigo_payload.construir_payload(_factura([item]), CATALOGO_TAXES)

    taxes = r["payload"]["items"][0]["taxes"]
    assert taxes == [{"id": 19468, "value": 19000.0}]  # 100000 * 19%


def test_retencion_siempre_se_calcula_por_porcentaje():
    item = _item(cantidad=1, valor_unitario=100000, retencion_tax_id="10065")
    r = siigo_payload.construir_payload(_factura([item]), CATALOGO_TAXES)

    taxes = r["payload"]["items"][0]["taxes"]
    assert taxes == [{"id": 10065, "value": 3500.0}]  # 100000 * 3.5%


def test_iva_y_retencion_conviven_en_el_mismo_item_taxes():
    item = _item(
        cantidad=1, valor_unitario=100000, iva_tax_id="19468", retencion_tax_id="10065",
        impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.0}],
    )
    r = siigo_payload.construir_payload(_factura([item]), CATALOGO_TAXES)

    taxes = r["payload"]["items"][0]["taxes"]
    assert {"id": 19468, "value": 19000.0} in taxes
    assert {"id": 10065, "value": 3500.0} in taxes
    assert len(taxes) == 2


def test_dos_lineas_con_retenciones_distintas_no_generan_conflicto():
    """Confirmado con el app anterior: la retención va por línea, dentro de
    items[].taxes -- no hay ningún campo a nivel de documento que obligue a
    unificarla entre líneas."""
    item_a = _item(descripcion="A", retencion_tax_id="10065")
    item_b = _item(descripcion="B", retencion_tax_id=None)
    r = siigo_payload.construir_payload(_factura([item_a, item_b]), CATALOGO_TAXES)

    assert r["payload"]["items"][0]["taxes"] == [{"id": 10065, "value": 70.0}]  # 2*1000*3.5%
    assert r["payload"]["items"][1]["taxes"] == []


def test_descuento_de_linea_se_refleja_en_discount_y_en_el_total():
    item = _item(cantidad=1, valor_unitario=1000, descuento_monto=200)
    r = siigo_payload.construir_payload(_factura([item]), CATALOGO_TAXES)

    item_payload = r["payload"]["items"][0]
    assert item_payload["price"] == 1000  # precio bruto, no se toca
    assert item_payload["discount"] == 200
    assert r["payload"]["payments"][0]["value"] == 800  # 1000 - 200, sin iva/retencion


def test_sin_descuento_no_incluye_la_llave_discount():
    item = _item(descuento_monto=0)
    r = siigo_payload.construir_payload(_factura([item]), CATALOGO_TAXES)
    assert "discount" not in r["payload"]["items"][0]


def test_payment_value_neto_de_retencion_no_es_total_pagar_xml():
    item = _item(cantidad=1, valor_unitario=100000, retencion_tax_id="10065")
    r = siigo_payload.construir_payload(_factura([item], total_pagar_xml=100000), CATALOGO_TAXES)

    # 100000 (base) - 3500 (retencion) = 96500 -- no 100000 (total_pagar_xml, que
    # docs/05-esquema-datos/modelo-datos.md documenta como nunca neteado de retenciones)
    assert r["payload"]["payments"][0]["value"] == 96500.0


def test_bloquea_si_falta_cuenta_contable():
    item = _item(cuenta_contable=None)
    r = siigo_payload.construir_payload(_factura([item]), CATALOGO_TAXES)

    assert r["payload"] is None
    assert any("cuenta contable" in m for m in r["motivos_bloqueo"])


def test_bloquea_si_falta_tipo_comprobante():
    r = siigo_payload.construir_payload(_factura([_item()], tipo_comprobante_id=None), CATALOGO_TAXES)
    assert r["payload"] is None
    assert any("tipo de comprobante" in m for m in r["motivos_bloqueo"])


def test_bloquea_si_falta_medio_pago():
    r = siigo_payload.construir_payload(_factura([_item()], medio_pago_id=None), CATALOGO_TAXES)
    assert r["payload"] is None
    assert any("medio de pago" in m for m in r["motivos_bloqueo"])


def test_prefijo_vacio_usa_fc_como_respaldo():
    """Bug real confirmado contra la API de Siigo: un folio DIAN puramente
    numérico (sin prefijo, caso real: factura 47357 de FERRECANTOS S.A.S)
    hace que `prefijo` llegue vacío -- Siigo rechaza provider_invoice.prefix
    vacío con 'parameter_required'. Mismo respaldo "FC" que ya usaba el
    aplicativo anterior del usuario (2212 compras reales sincronizadas)."""
    r = siigo_payload.construir_payload(_factura([_item()], prefijo="", numero_puro="47357"), CATALOGO_TAXES)
    assert r["payload"]["provider_invoice"] == {"prefix": "FC", "number": "47357"}


def test_forma_general_del_payload():
    r = siigo_payload.construir_payload(_factura([_item()]), CATALOGO_TAXES)
    payload = r["payload"]

    assert payload["document"] == {"id": 18679}
    assert payload["supplier"] == {"identification": "900111222", "branch_office": 0}
    assert payload["provider_invoice"] == {"prefix": "F", "number": "1"}
    assert "PROVEEDOR TEST" in payload["observations"]
    assert payload["discount_type"] == "Value"
    assert payload["payments"][0]["id"] == 8729
    assert payload["payments"][0]["due_date"] == "2026-07-01"
    assert payload["items"][0]["type"] == "Account"
    assert payload["items"][0]["code"] == "51950101"


def test_descripcion_se_trunca_a_50_caracteres():
    item = _item(descripcion="X" * 80)
    r = siigo_payload.construir_payload(_factura([item]), CATALOGO_TAXES)
    assert len(r["payload"]["items"][0]["description"]) == 50


def test_cantidad_fraccionaria_ajusta_el_precio_para_conservar_el_total():
    """Caso real (factura D2295739 de Hielo Super-Cool): cantidad 1.228 a
    $16.286,65 = $20.000. Siigo solo acepta 2 decimales en quantity -- si se
    redondea a 1.23 sin ajustar el precio, el total queda en $20.032,58
    (fuera de la tolerancia de reintento de ±5 pesos). Igual que el
    aplicativo anterior: el precio se deriva del total real / cantidad
    redondeada."""
    item = _item(cantidad=1.228, valor_unitario=16286.65)
    r = siigo_payload.construir_payload(_factura([item]), CATALOGO_TAXES)

    item_payload = r["payload"]["items"][0]
    assert item_payload["quantity"] == 1.23
    total_reconstruido = item_payload["quantity"] * item_payload["price"]
    assert abs(total_reconstruido - 20000.0) < 0.05  # conserva el total real del XML
    assert abs(r["payload"]["payments"][0]["value"] - 20000.0) < 0.05


def test_cantidad_entera_no_altera_el_precio():
    item = _item(cantidad=3, valor_unitario=1000)
    r = siigo_payload.construir_payload(_factura([item]), CATALOGO_TAXES)
    assert r["payload"]["items"][0]["price"] == 1000
    assert r["payload"]["items"][0]["quantity"] == 3
