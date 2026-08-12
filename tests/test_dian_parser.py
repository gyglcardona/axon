"""
Pruebas del parser contra los fixtures sintéticos (tests/fixtures-sinteticos/).

Estos NO son casos reales -- ver tests/casos-reales/README.md para la
diferencia. Cuando lleguen XML reales, sus pruebas van en tests/casos-reales/.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dian_parser import extraer_cufe, parsear_factura, tipo_documento  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures-sinteticos"


def _leer(nombre: str) -> bytes:
    return (FIXTURES / nombre).read_bytes()


def test_iva_multilinea_suma_todos_los_taxtotal():
    """Corrige el bug #1 confirmado en xml_processor.py (parser anterior)."""
    factura = parsear_factura(_leer("iva-multilinea.xml"))

    total_iva = factura.total_por_tipo("IVA")

    # 63333 + 63333 + 63334 = 190000 -- si el parser solo leyera el primer
    # TaxTotal (el bug del parser anterior), esto daría 63333.
    assert total_iva == 190000
    assert len(factura.impuestos_documento) == 3
    assert any("IVA multilínea" in a or "TaxTotal a nivel de documento" in a for a in factura.advertencias)


def test_tax_exclusive_en_cero_usa_respaldo():
    """Corrige el bug #4: TaxExclusiveAmount en 0 debe usar LineExtensionAmount."""
    factura = parsear_factura(_leer("tax-exclusive-en-cero.xml"))

    assert factura.subtotal_xml == 500000
    assert "respaldo" in factura.subtotal_fuente.lower()
    assert any("TaxExclusiveAmount venía en 0" in a for a in factura.advertencias)


def test_total_pagar_se_reporta_tal_cual_sin_asumir_neto():
    """El parser nunca debe restar la retención por su cuenta -- eso es
    decisión del motor de reglas, no del parser (ver docs/00-contexto/
    decisiones-arquitectura.md)."""
    factura = parsear_factura(_leer("iva-no-discriminado-hielo-super-cool.xml"))

    assert factura.total_pagar_xml == 2380000
    assert factura.subtotal_xml == 2000000
    assert factura.total_por_tipo("IVA") == 380000


def test_precio_con_iva_incluido_usa_line_extension_amount():
    """Bug real confirmado en producción (TECNOMEDICA MD, TORACHE, julio
    2026): cac:Price/cbc:PriceAmount venía con el IVA incluido (450) en vez
    del precio neto (378, que sí coincide con LineExtensionAmount) -- usar
    Price tal cual inflaba el ítem enviado a Siigo por el valor del IVA."""
    factura = parsear_factura(_leer("precio-con-iva-incluido.xml"))

    linea = factura.lineas[0]
    assert linea.valor_unitario == 378.0  # NO 450 (el precio con IVA que trae el XML)
    assert any("no coincide con el precio unitario neto" in a for a in factura.advertencias)


def test_responsabilidades_fiscales_se_separan_por_punto_y_coma():
    """Caso real (QUALA/KOPPS/COMMERK): TaxLevelCode trae varios códigos DIAN
    juntos separados por ';' -- el parser debe devolverlos como lista, no
    como un solo string opaco (eso es lo que rompía orquestador._payload_tercero,
    ver test_payload_tercero_multiples_codigos)."""
    factura = parsear_factura(_leer("responsabilidades-fiscales-multiples.xml"))

    assert factura.responsabilidades_fiscales == ["O-13", "O-15", "O-23"]


def test_sin_tax_level_code_da_lista_vacia():
    factura = parsear_factura(_leer("iva-multilinea.xml"))

    assert factura.responsabilidades_fiscales == []


def test_lineas_se_parsean_correctamente():
    factura = parsear_factura(_leer("iva-no-discriminado-hielo-super-cool.xml"))

    assert len(factura.lineas) == 1
    linea = factura.lineas[0]
    assert linea.descripcion == "Insumos de refrigeración (fixture)"
    assert linea.cantidad == 10
    assert linea.total_linea == 2000000


# --- Invoice envuelto en AttachedDocument (bug real, agosto 2026) ---
#
# Caso real confirmado con Agencia Exequiales del Ayer: su proveedor (vía
# edocnube.com) no entrega el Invoice directo, lo envuelve en un
# AttachedDocument con el Invoice real embebido como CDATA. Sin desenvolver
# esto, tipo_documento() veía "AttachedDocument" y TODAS las facturas de ese
# proveedor se descartaban en silencio como "no es una factura de compra" --
# nunca llegaban a la bandeja, sin ningún error visible.

def test_tipo_documento_desenvuelve_attached_document():
    xml_bytes = _leer("invoice-envuelto-en-attached-document.xml")
    assert tipo_documento(xml_bytes) == "Invoice"


def test_extraer_cufe_desenvuelve_attached_document():
    xml_bytes = _leer("invoice-envuelto-en-attached-document.xml")
    assert extraer_cufe(xml_bytes) == "CUFE-SINTETICO-ENVUELTO-001"


def test_parsear_factura_desenvuelve_attached_document():
    factura = parsear_factura(_leer("invoice-envuelto-en-attached-document.xml"))

    assert factura.cufe == "CUFE-SINTETICO-ENVUELTO-001"
    assert factura.proveedor_nombre == "PROVEEDOR SINTETICO ENVUELTO S.A.S."
    assert factura.proveedor_nit == "900000099"
    assert factura.total_pagar_xml == 119000
    assert factura.subtotal_xml == 100000
    assert factura.total_por_tipo("IVA") == 19000


def test_tipo_documento_invoice_normal_no_se_toca():
    """Un Invoice normal (no envuelto) debe seguir funcionando exactamente
    igual -- el desenvolvimiento es un no-op cuando no hace falta."""
    assert tipo_documento(_leer("iva-multilinea.xml")) == "Invoice"


# --- receptor_nit (validación de que la factura sea para la empresa correcta) ---


def test_sin_accounting_customer_party_receptor_nit_es_none():
    """Ninguno de los fixtures sintéticos actuales trae AccountingCustomerParty
    -- debe dar None, no reventar, para no romper la importación de XML que no
    traigan ese bloque (ver orquestador.ejecutar_importar, que solo valida
    cuando sí viene el dato)."""
    factura = parsear_factura(_leer("iva-multilinea.xml"))
    assert factura.receptor_nit is None


def test_receptor_nit_se_extrae_de_accounting_customer_party():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:UUID>CUFE-RECEPTOR-001</cbc:UUID>
  <cbc:ID>SINT-REC-001</cbc:ID>
  <cbc:IssueDate>2026-08-01</cbc:IssueDate>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyLegalEntity><cbc:RegistrationName>PROVEEDOR X</cbc:RegistrationName></cac:PartyLegalEntity>
      <cac:PartyTaxScheme><cbc:CompanyID>900000001</cbc:CompanyID></cac:PartyTaxScheme>
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty>
    <cac:Party>
      <cac:PartyLegalEntity><cbc:RegistrationName>EMPRESA RECEPTORA</cbc:RegistrationName></cac:PartyLegalEntity>
      <cac:PartyTaxScheme><cbc:CompanyID>901518066</cbc:CompanyID></cac:PartyTaxScheme>
    </cac:Party>
  </cac:AccountingCustomerParty>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount>100000</cbc:TaxExclusiveAmount>
    <cbc:PayableAmount>119000</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:TaxTotal><cbc:TaxAmount>19000</cbc:TaxAmount></cac:TaxTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity>1</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount>100000</cbc:LineExtensionAmount>
    <cac:Item><cbc:Description>Item</cbc:Description></cac:Item>
    <cac:Price><cbc:PriceAmount>100000</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
</Invoice>"""
    factura = parsear_factura(xml)
    assert factura.receptor_nit == "901518066"
