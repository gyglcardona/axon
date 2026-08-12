"""
Pruebas del destino de causación por empresa (Siigo / Contai) -- solo la
organización/visibilidad de la configuración, no la generación de archivos
planos para Contai (todavía no existe, ver docs/08-decisiones-pendientes).
"""

import sys
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
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", tmp_path / "data" / "empresas")

    original_conectar = state_store.conectar

    def _conectar_en_tmp(nit_empresa, base_dir=None):
        return original_conectar(nit_empresa, base_dir=tmp_path / "data" / "empresas")

    monkeypatch.setattr(state_store, "conectar", _conectar_en_tmp)
    return "empresa-test", tmp_path


def test_sin_config_previa_el_default_es_siigo(empresa_configurada):
    """Ninguna empresa ya configurada debe cambiar de comportamiento por
    este cambio -- si el archivo no existe, el destino es "siigo"."""
    slug, _ = empresa_configurada
    assert orquestador.obtener_destino_causacion(slug) == {"destino_causacion": "siigo"}


def test_guardar_y_obtener_destino_causacion(empresa_configurada):
    slug, _ = empresa_configurada
    orquestador.guardar_destino_causacion(slug, "contai")

    assert orquestador.obtener_destino_causacion(slug) == {"destino_causacion": "contai"}


def test_destino_invalido_da_error_claro(empresa_configurada):
    slug, _ = empresa_configurada
    with pytest.raises(ValueError, match="Destino de causación inválido"):
        orquestador.guardar_destino_causacion(slug, "algo-que-no-existe")


def test_guardar_destino_no_pisa_credenciales_siigo_ni_conexion_drive(empresa_configurada):
    """Los tres campos viven en el mismo archivo de config -- guardar uno no
    debe borrar los otros dos."""
    slug, _ = empresa_configurada
    orquestador.guardar_conexion_siigo(slug, "correo@empresa.com", "ACCESS-KEY", "Axon")
    orquestador.guardar_conexion_drive(slug, "CARPETA-ID-123")

    orquestador.guardar_destino_causacion(slug, "contai")

    assert orquestador.obtener_conexion_siigo(slug)["usuario"] == "correo@empresa.com"
    assert orquestador.obtener_conexion_drive(slug)["carpeta_id"] == "CARPETA-ID-123"
    assert orquestador.obtener_destino_causacion(slug)["destino_causacion"] == "contai"


def test_guardar_conexion_siigo_no_pisa_destino_ya_elegido(empresa_configurada):
    slug, _ = empresa_configurada
    orquestador.guardar_destino_causacion(slug, "contai")

    orquestador.guardar_conexion_siigo(slug, "correo@empresa.com", "ACCESS-KEY", "Axon")

    assert orquestador.obtener_destino_causacion(slug)["destino_causacion"] == "contai"


def test_listar_empresas_incluye_destino_causacion(empresa_configurada):
    slug, _ = empresa_configurada
    orquestador.guardar_destino_causacion(slug, "contai")

    empresas = orquestador.listar_empresas()

    assert empresas[0]["destino_causacion"] == "contai"


def test_listar_empresas_sin_config_da_siigo_por_defecto(empresa_configurada):
    empresas = orquestador.listar_empresas()

    assert empresas[0]["destino_causacion"] == "siigo"
