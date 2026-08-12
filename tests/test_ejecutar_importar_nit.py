"""
Pruebas de orquestador.ejecutar_importar validando el NIT del receptor
(comprador) contra el NIT de la empresa a la que se está importando.

Bug de riesgo real desde que existe la subida manual de carpetas en modo SaaS
(ver guardar_archivos_subidos): alguien podría subir por error la carpeta de
OTRA empresa, mezclando facturas ajenas en la bandeja de revisión (rompe el
aislamiento de datos de docs/06-multiempresa-saas/aislamiento-datos.md). Se
prueba contra el pipeline real (sin mockear ejecutar_importar), a diferencia
de tests/test_importar_todo.py que sí lo mockea.
"""

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orquestador  # noqa: E402
import state_store  # noqa: E402


@pytest.fixture
def empresa_configurada(tmp_path, monkeypatch):
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"empresa-test","nit":"900000000","nombre":"EMPRESA TEST"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")
    monkeypatch.setattr(orquestador, "ENTRADA_DIAN", tmp_path / "data" / "entrada-dian")
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", tmp_path / "data" / "empresas")

    original_conectar = state_store.conectar

    def _conectar_en_tmp(nit_empresa, base_dir=None):
        return original_conectar(nit_empresa, base_dir=tmp_path / "data" / "empresas")

    monkeypatch.setattr(state_store, "conectar", _conectar_en_tmp)
    return "empresa-test", tmp_path


def _xml_factura(cufe: str, receptor_nit: str | None) -> bytes:
    bloque_receptor = ""
    if receptor_nit is not None:
        bloque_receptor = f"""
  <cac:AccountingCustomerParty>
    <cac:Party>
      <cac:PartyLegalEntity><cbc:RegistrationName>RECEPTOR</cbc:RegistrationName></cac:PartyLegalEntity>
      <cac:PartyTaxScheme><cbc:CompanyID>{receptor_nit}</cbc:CompanyID></cac:PartyTaxScheme>
    </cac:Party>
  </cac:AccountingCustomerParty>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:UUID>{cufe}</cbc:UUID>
  <cbc:ID>FACT-{cufe[:6]}</cbc:ID>
  <cbc:IssueDate>2026-08-01</cbc:IssueDate>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyLegalEntity><cbc:RegistrationName>PROVEEDOR X</cbc:RegistrationName></cac:PartyLegalEntity>
      <cac:PartyTaxScheme><cbc:CompanyID>900000099</cbc:CompanyID></cac:PartyTaxScheme>
    </cac:Party>
  </cac:AccountingSupplierParty>{bloque_receptor}
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
</Invoice>""".encode("utf-8")


def _crear_zip(ruta: Path, cufe: str, receptor_nit: str | None) -> None:
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr(f"{cufe}.xml", _xml_factura(cufe, receptor_nit))


def _contar_compras(nit: str, tmp_path: Path) -> int:
    conn = state_store.conectar(nit)
    try:
        return conn.execute("SELECT COUNT(*) FROM compras").fetchone()[0]
    finally:
        conn.close()


def test_factura_de_otra_empresa_se_descarta_y_no_se_importa(empresa_configurada):
    slug, tmp_path = empresa_configurada
    carpeta = tmp_path / "data" / "entrada-dian" / slug / "subidos"
    carpeta.mkdir(parents=True)
    _crear_zip(carpeta / "AAA111.zip", "AAA111", receptor_nit="811111111")  # OTRA empresa

    resultado = orquestador.ejecutar_importar(slug, "subidos")

    assert resultado["nuevas"] == 0
    assert resultado["nit_no_corresponde"] == 1
    assert _contar_compras("900000000", tmp_path) == 0


def test_factura_con_nit_receptor_correcto_se_importa(empresa_configurada):
    slug, tmp_path = empresa_configurada
    carpeta = tmp_path / "data" / "entrada-dian" / slug / "subidos"
    carpeta.mkdir(parents=True)
    _crear_zip(carpeta / "BBB222.zip", "BBB222", receptor_nit="900000000")  # esta empresa

    resultado = orquestador.ejecutar_importar(slug, "subidos")

    assert resultado["nuevas"] == 1
    assert resultado["nit_no_corresponde"] == 0
    assert _contar_compras("900000000", tmp_path) == 1


def test_factura_sin_receptor_en_el_xml_se_importa_igual(empresa_configurada):
    """No se puede validar lo que el XML no trae -- no debe romper la
    importación de proveedores/formatos que no incluyan AccountingCustomerParty."""
    slug, tmp_path = empresa_configurada
    carpeta = tmp_path / "data" / "entrada-dian" / slug / "subidos"
    carpeta.mkdir(parents=True)
    _crear_zip(carpeta / "CCC333.zip", "CCC333", receptor_nit=None)

    resultado = orquestador.ejecutar_importar(slug, "subidos")

    assert resultado["nuevas"] == 1
    assert resultado["nit_no_corresponde"] == 0
