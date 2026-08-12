"""
Pruebas de descubrimiento/deduplicación de zip_handler.py contra escenarios
sintéticos que reproducen formas reales de entrega vistas en
data/entrada-dian/ (ver conversación de julio 2026):

- ZIP normal (un XML + un PDF).
- XML ya descomprimido, suelto en la carpeta.
- El mismo documento entregado dos veces: una vez en ZIP y otra vez ya
  descomprimido en una subcarpeta con su mismo nombre (patrón real visto en
  data/entrada-dian/hielo-super-cool/2026/06/).
- XML sin CUFE, o corrupto -- no debe deduplicarse por error, debe marcarse
  para revisión manual, nunca perderse en silencio.
"""

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from zip_handler import descubrir_documentos  # noqa: E402


def _xml_con_cufe(cufe: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:UUID>{cufe}</cbc:UUID>
  <cbc:ID>FACT-{cufe[:6]}</cbc:ID>
</Invoice>""".encode("utf-8")


def _xml_application_response(uuid: str) -> bytes:
    """Reproduce el caso real encontrado en data/entrada-dian/hielo-super-cool/2026/06/:
    la DIAN entrega acuses de recibo con el mismo formato de ZIP que las
    facturas -- no se pueden parsear como Invoice, deben descartarse explícitamente."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ApplicationResponse xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:UUID>{uuid}</cbc:UUID>
</ApplicationResponse>""".encode("utf-8")


def _xml_sin_cufe() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>FACT-SIN-CUFE</cbc:ID>
</Invoice>"""


def _crear_zip(ruta: Path, cufe: str) -> None:
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr(f"{cufe}.xml", _xml_con_cufe(cufe))
        z.writestr(f"{cufe}.pdf", b"%PDF-1.4 fake")


FIXTURES = Path(__file__).parent / "fixtures-sinteticos"


def test_procesa_zip_y_xml_suelto_sin_duplicarse(tmp_path):
    _crear_zip(tmp_path / "AAA111.zip", "AAA111")
    (tmp_path / "BBB222.xml").write_bytes(_xml_con_cufe("BBB222"))

    resultado = descubrir_documentos(tmp_path)

    cufes = {d.cufe for d in resultado.documentos}
    assert cufes == {"AAA111", "BBB222"}
    assert resultado.duplicados == []
    assert resultado.con_error == []


def test_mismo_cufe_en_zip_y_carpeta_extraida_es_duplicado(tmp_path):
    """Reproduce el caso real: <cufe>.zip y <cufe>/<cufe>.xml conviviendo en la
    misma carpeta porque alguien extrajo el ZIP a mano sin borrarlo."""
    _crear_zip(tmp_path / "CCC333.zip", "CCC333")
    subcarpeta = tmp_path / "CCC333"
    subcarpeta.mkdir()
    (subcarpeta / "CCC333.xml").write_bytes(_xml_con_cufe("CCC333"))

    resultado = descubrir_documentos(tmp_path)

    assert len(resultado.documentos) == 1
    assert resultado.documentos[0].cufe == "CCC333"
    assert len(resultado.duplicados) == 1
    assert resultado.duplicados[0].cufe == "CCC333"


def test_mismo_cufe_zip_y_suelto_es_duplicado(tmp_path):
    _crear_zip(tmp_path / "DDD444.zip", "DDD444")
    (tmp_path / "DDD444.xml").write_bytes(_xml_con_cufe("DDD444"))

    resultado = descubrir_documentos(tmp_path)

    assert len(resultado.documentos) == 1
    assert len(resultado.duplicados) == 1


def test_xml_sin_cufe_no_se_pierde_y_no_se_deduplica_por_error(tmp_path):
    (tmp_path / "sin-cufe.xml").write_bytes(_xml_sin_cufe())

    resultado = descubrir_documentos(tmp_path)

    assert resultado.documentos == []
    assert resultado.duplicados == []
    assert len(resultado.con_error) == 1
    assert "CUFE" in resultado.con_error[0].motivo


def test_application_response_se_descarta_como_no_factura_no_como_error(tmp_path):
    """Un acuse de recibo (ApplicationResponse) no debe intentar parsearse como
    factura, ni contar como error ni como duplicado -- va en su propia lista."""
    _crear_zip(tmp_path / "FFF666.zip", "FFF666")  # una factura normal, de control
    (tmp_path / "acuse.xml").write_bytes(_xml_application_response("GGG777"))

    resultado = descubrir_documentos(tmp_path)

    assert {d.cufe for d in resultado.documentos} == {"FFF666"}
    assert resultado.con_error == []
    assert resultado.duplicados == []


def test_zip_danado_no_tumba_el_resto_de_la_carpeta(tmp_path):
    """Bug real confirmado en vivo con la subida manual desde el navegador
    (orquestador.guardar_archivos_subidos): un .zip corrupto o que en
    realidad no es un ZIP (archivo equivocado, descarga truncada) hacía que
    zipfile.ZipFile reventara con BadZipFile y se perdiera TODO el
    descubrimiento de la carpeta, no solo ese archivo."""
    _crear_zip(tmp_path / "HHH888.zip", "HHH888")  # una factura normal, de control
    (tmp_path / "no-es-un-zip.zip").write_bytes(b"esto no es un zip valido")

    resultado = descubrir_documentos(tmp_path)

    assert {d.cufe for d in resultado.documentos} == {"HHH888"}
    assert len(resultado.con_error) == 1
    assert resultado.con_error[0].origen.name == "no-es-un-zip.zip"
    assert "dañado" in resultado.con_error[0].motivo or "válido" in resultado.con_error[0].motivo


def test_xml_corrupto_se_reporta_como_error_no_bloquea_el_resto(tmp_path):
    (tmp_path / "corrupto.xml").write_bytes(b"<Invoice><sin cerrar")
    (tmp_path / "EEE555.xml").write_bytes(_xml_con_cufe("EEE555"))

    resultado = descubrir_documentos(tmp_path)

    assert {d.cufe for d in resultado.documentos} == {"EEE555"}
    assert len(resultado.con_error) == 1
    assert "mal formado" in resultado.con_error[0].motivo


def test_invoice_envuelto_en_attached_document_se_descubre_igual(tmp_path):
    """Bug real confirmado con Agencia Exequiales del Ayer (agosto 2026):
    un proveedor entrega el Invoice envuelto en un AttachedDocument -- sin
    desenvolverlo, descubrir_documentos() lo clasificaba como "no factura" y
    TODAS las facturas de ese proveedor se perdían en silencio, nunca
    llegaban a la bandeja de revisión."""
    contenido = (FIXTURES / "invoice-envuelto-en-attached-document.xml").read_bytes()
    with zipfile.ZipFile(tmp_path / "envuelto.zip", "w") as z:
        z.writestr("envuelto.xml", contenido)

    resultado = descubrir_documentos(tmp_path)

    assert {d.cufe for d in resultado.documentos} == {"CUFE-SINTETICO-ENVUELTO-001"}
    assert resultado.con_error == []
    assert resultado.no_facturas == []
