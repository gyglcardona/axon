"""
Pruebas del menú "Conexión Siigo": leer/guardar usuario, access_key y
partner_id en config/empresas/<nit>.json -- el mismo archivo que ya usan las
5 empresas locales, y con el que una empresa nueva del SaaS (sin archivo
todavía) lo configuraría por primera vez desde el formulario.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orquestador  # noqa: E402


@pytest.fixture
def empresa_slug(tmp_path, monkeypatch):
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"empresa-test","nit":"900000000","nombre":"EMPRESA TEST"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")
    return "empresa-test"


def test_empresa_sin_config_previa_devuelve_vacio(empresa_slug):
    datos = orquestador.obtener_conexion_siigo(empresa_slug)
    assert datos == {"usuario": "", "access_key": "", "partner_id": "", "configurado": False}


def test_guardar_crea_el_archivo_si_no_existia(tmp_path, empresa_slug):
    resultado = orquestador.guardar_conexion_siigo(
        empresa_slug, "correo@empresa.com", "ACCESS-KEY-XYZ", "Axon"
    )
    assert resultado == {"guardado": True}

    ruta = tmp_path / "config" / "empresas" / "900000000.json"
    assert ruta.exists()
    config = json.loads(ruta.read_text(encoding="utf-8"))
    assert config["credenciales_siigo"]["usuario"] == "correo@empresa.com"
    assert config["credenciales_siigo"]["access_key"] == "ACCESS-KEY-XYZ"
    assert config["credenciales_siigo"]["partner_id"] == "Axon"
    assert config["nit"] == "900000000"

    datos = orquestador.obtener_conexion_siigo(empresa_slug)
    assert datos["configurado"] is True
    assert datos["usuario"] == "correo@empresa.com"


def test_guardar_no_borra_politicas_ni_clave_portal_existentes(tmp_path, empresa_slug):
    ruta = tmp_path / "config" / "empresas"
    ruta.mkdir(parents=True)
    (ruta / "900000000.json").write_text(json.dumps({
        "nit": "900000000", "nombre": "EMPRESA TEST", "slug": "empresa-test",
        "credenciales_siigo": {
            "usuario": "viejo@empresa.com", "access_key": "VIEJA",
            "clave_portal_siigo": "clave-humana-no-tocar", "partner_id": "Axon",
        },
        "politicas": {"iva_no_discriminado": {"activa": True}},
    }), encoding="utf-8")

    orquestador.guardar_conexion_siigo(empresa_slug, "nuevo@empresa.com", "NUEVA-KEY", "Axon")

    config = json.loads((ruta / "900000000.json").read_text(encoding="utf-8"))
    assert config["credenciales_siigo"]["usuario"] == "nuevo@empresa.com"
    assert config["credenciales_siigo"]["access_key"] == "NUEVA-KEY"
    assert config["credenciales_siigo"]["clave_portal_siigo"] == "clave-humana-no-tocar"
    assert config["politicas"]["iva_no_discriminado"]["activa"] is True


def test_empresa_no_encontrada_lanza_error(empresa_slug):
    with pytest.raises(orquestador.EmpresaNoEncontrada):
        orquestador.obtener_conexion_siigo("no-existe")
