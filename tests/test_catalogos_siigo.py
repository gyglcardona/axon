"""
Pruebas de los catálogos maestros de Siigo (tipos de FC, medios de pago,
comprobantes, retenciones). Nunca llaman a la red real -- `siigo_client` se
reemplaza por una versión falsa; las formas de respuesta usadas aquí vienen
confirmadas contra la API real (ver src/siigo_client.py, docstring).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orquestador  # noqa: E402
import siigo_client  # noqa: E402
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

    orquestador.guardar_conexion_siigo("empresa-test", "correo@empresa.com", "ACCESS-KEY", "Axon")
    return "empresa-test"


def test_sin_conexion_configurada_da_error_claro(tmp_path, monkeypatch):
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"empresa-vacia","nit":"800000000","nombre":"SIN CONFIG"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")

    with pytest.raises(ValueError, match="Conexión Siigo"):
        orquestador.actualizar_catalogos_siigo("empresa-vacia")


def test_actualizar_trae_los_4_catalogos_y_los_guarda(empresa_configurada, monkeypatch):
    llamadas = []

    def _autenticar_falso(usuario, access_key):
        llamadas.append(("auth", usuario, access_key))
        return "TOKEN-FALSO"

    monkeypatch.setattr(siigo_client, "autenticar", _autenticar_falso)
    monkeypatch.setattr(siigo_client, "obtener_document_types", lambda t, p: [
        {"id": 18679, "code": "1", "name": "Compra", "type": "FC"},
    ])
    monkeypatch.setattr(siigo_client, "obtener_payment_types", lambda t, p: [
        {"id": 8729, "name": "Causación Automática de compras", "type": "Proveedor"},
        {"id": 4258, "name": "Efectivo", "type": "CarteraProveedor"},
    ])
    monkeypatch.setattr(siigo_client, "obtener_journals", lambda t, p: [
        {"id": "822d696a", "name": "CC-997-1149", "date": "2026-07-01"},
    ])
    monkeypatch.setattr(siigo_client, "obtener_taxes", lambda t, p: [
        {"id": 10048, "name": "IVA 19%", "type": "IVA", "percentage": 19.0},
        {"id": 10049, "name": "Retefuente 11%", "type": "Retefuente", "percentage": 11.0},
    ])

    resumen = orquestador.actualizar_catalogos_siigo(empresa_configurada)

    assert resumen == {"document_types": 1, "payment_types": 2, "journals": 1, "taxes": 2}
    assert llamadas == [("auth", "correo@empresa.com", "ACCESS-KEY")]

    taxes = orquestador.listar_catalogo_siigo(empresa_configurada, "taxes")
    assert {t["name"] for t in taxes} == {"IVA 19%", "Retefuente 11%"}

    payment_types = orquestador.listar_catalogo_siigo(empresa_configurada, "payment_types")
    assert len(payment_types) == 2


def test_actualizar_reemplaza_no_acumula(empresa_configurada, monkeypatch):
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(siigo_client, "obtener_document_types", lambda t, p: [{"id": 1, "name": "A"}])
    monkeypatch.setattr(siigo_client, "obtener_payment_types", lambda t, p: [])
    monkeypatch.setattr(siigo_client, "obtener_journals", lambda t, p: [])
    monkeypatch.setattr(siigo_client, "obtener_taxes", lambda t, p: [])

    orquestador.actualizar_catalogos_siigo(empresa_configurada)
    orquestador.actualizar_catalogos_siigo(empresa_configurada)

    assert len(orquestador.listar_catalogo_siigo(empresa_configurada, "document_types")) == 1


def test_falla_de_autenticacion_se_propaga_como_siigo_error(empresa_configurada, monkeypatch):
    def _autenticar_falla(usuario, access_key):
        raise siigo_client.SiigoError("Siigo rechazó la autenticación (HTTP 401)")

    monkeypatch.setattr(siigo_client, "autenticar", _autenticar_falla)

    with pytest.raises(siigo_client.SiigoError):
        orquestador.actualizar_catalogos_siigo(empresa_configurada)


def test_tipo_de_catalogo_invalido_da_error_claro(empresa_configurada):
    with pytest.raises(ValueError, match="Tipo de catálogo desconocido"):
        orquestador.listar_catalogo_siigo(empresa_configurada, "algo-que-no-existe")
