"""
Pruebas de la conexión de Google Drive por empresa y de
orquestador.importar_desde_drive -- nunca tocan la red real, `drive_client`
se reemplaza por una versión falsa (mismo patrón que tests/test_catalogos_siigo.py
para siigo_client).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import drive_client  # noqa: E402
import google_conexiones  # noqa: E402
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


def test_conexion_drive_sin_configurar(empresa_configurada):
    slug, _ = empresa_configurada
    assert orquestador.obtener_conexion_drive(slug) == {"carpeta_id": "", "configurado": False, "conexion_id": ""}


def test_guardar_y_obtener_conexion_drive(empresa_configurada):
    slug, _ = empresa_configurada
    orquestador.guardar_conexion_drive(slug, "CARPETA-ID-123")

    assert orquestador.obtener_conexion_drive(slug) == {
        "carpeta_id": "CARPETA-ID-123", "configurado": True, "conexion_id": "",
    }


def test_guardar_conexion_drive_no_pisa_credenciales_siigo(empresa_configurada):
    """La conexión de Drive y la de Siigo viven en el mismo archivo de
    config -- guardar una no debe borrar la otra."""
    slug, _ = empresa_configurada
    orquestador.guardar_conexion_siigo(slug, "correo@empresa.com", "ACCESS-KEY", "Axon")

    orquestador.guardar_conexion_drive(slug, "CARPETA-ID-123")

    assert orquestador.obtener_conexion_siigo(slug)["usuario"] == "correo@empresa.com"
    assert orquestador.obtener_conexion_drive(slug)["carpeta_id"] == "CARPETA-ID-123"


def test_importar_desde_drive_sin_configurar_da_error_claro(empresa_configurada):
    slug, _ = empresa_configurada
    with pytest.raises(ValueError, match="Drive"):
        orquestador.importar_desde_drive(slug)


def test_importar_desde_drive_descarga_solo_lo_nuevo_y_preserva_subcarpetas(empresa_configurada, monkeypatch):
    slug, tmp_path = empresa_configurada
    orquestador.guardar_conexion_drive(slug, "CARPETA-ID")
    monkeypatch.setattr(google_conexiones, "obtener_credenciales", lambda conexion_id: object())

    ya_local = tmp_path / "data" / "entrada-dian" / slug / "2026" / "07" / "existente.zip"
    ya_local.parent.mkdir(parents=True)
    ya_local.write_bytes(b"contenido-viejo-no-se-toca")

    arbol_drive = [
        {"id": "f-existente", "name": "existente.zip", "ruta_relativa": "2026/07/existente.zip"},
        {"id": "f-nuevo", "name": "nuevo.zip", "ruta_relativa": "2026/07/nuevo.zip"},
        {"id": "f-suelto", "name": "suelto.xml", "ruta_relativa": "suelto.xml"},
    ]
    descargas = {"f-nuevo": b"contenido-nuevo-zip", "f-suelto": b"<xml/>"}
    carpetas_pedidas = []

    def _listar_arbol_falso(carpeta_id, creds):
        carpetas_pedidas.append(carpeta_id)
        return arbol_drive

    monkeypatch.setattr(drive_client, "listar_arbol", _listar_arbol_falso)
    monkeypatch.setattr(drive_client, "descargar_archivo", lambda file_id, creds: descargas[file_id])

    llamada_importar = {}

    def _ejecutar_importar_falso(slug_recibido, carpeta_relativa):
        llamada_importar["args"] = (slug_recibido, carpeta_relativa)
        return {"empresa": "EMPRESA TEST", "nit": "900000000", "carpeta": "x",
                "nuevas": 1, "ya_existentes": 0, "duplicados": 0, "no_facturas": 0, "con_error": 0}

    monkeypatch.setattr(orquestador, "ejecutar_importar", _ejecutar_importar_falso)

    resumen = orquestador.importar_desde_drive(slug)

    assert carpetas_pedidas == ["CARPETA-ID"]
    assert resumen["descargados"] == 2
    assert resumen["ya_estaban_localmente"] == 1
    assert resumen["nuevas"] == 1
    assert llamada_importar["args"] == (slug, ".")

    # el archivo que ya existía no se sobreescribió
    assert ya_local.read_bytes() == b"contenido-viejo-no-se-toca"
    # los nuevos quedaron con la misma estructura de subcarpetas que en Drive
    assert (tmp_path / "data" / "entrada-dian" / slug / "2026" / "07" / "nuevo.zip").read_bytes() == b"contenido-nuevo-zip"
    assert (tmp_path / "data" / "entrada-dian" / slug / "suelto.xml").read_bytes() == b"<xml/>"


def test_importar_desde_drive_sin_archivos_nuevos_no_descarga_nada(empresa_configurada, monkeypatch):
    slug, tmp_path = empresa_configurada
    orquestador.guardar_conexion_drive(slug, "CARPETA-ID")
    monkeypatch.setattr(google_conexiones, "obtener_credenciales", lambda conexion_id: object())

    ya_local = tmp_path / "data" / "entrada-dian" / slug / "existente.zip"
    ya_local.parent.mkdir(parents=True, exist_ok=True)
    ya_local.write_bytes(b"contenido")

    monkeypatch.setattr(drive_client, "listar_arbol", lambda carpeta_id, creds: [
        {"id": "f-existente", "name": "existente.zip", "ruta_relativa": "existente.zip"},
    ])

    def _descargar_no_deberia_llamarse(file_id, creds):
        raise AssertionError("no debía descargar nada -- el archivo ya existe localmente")

    monkeypatch.setattr(drive_client, "descargar_archivo", _descargar_no_deberia_llamarse)
    monkeypatch.setattr(orquestador, "ejecutar_importar", lambda slug, carpeta: {
        "empresa": "x", "nit": "x", "carpeta": "x",
        "nuevas": 0, "ya_existentes": 0, "duplicados": 0, "no_facturas": 0, "con_error": 0,
    })

    resumen = orquestador.importar_desde_drive(slug)

    assert resumen["descargados"] == 0
    assert resumen["ya_estaban_localmente"] == 1
