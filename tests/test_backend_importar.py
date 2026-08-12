"""
Pruebas de los endpoints del botón único de importación --
POST /importar-todo, POST /importar-archivos (multipart) y
POST /validar-completitud-archivo (multipart) -- con app.test_client() de
Flask. auth_store y las rutas de orquestador se redirigen a tmp_path; nunca
tocan data/sistema.db ni config/empresas/registro.json reales.
"""

import io
import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import auth_store  # noqa: E402
import orquestador  # noqa: E402
import state_store  # noqa: E402
import app as flask_app_module  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    original_conectar_sistema = auth_store.conectar
    monkeypatch.setattr(auth_store, "conectar", lambda base_dir=None: original_conectar_sistema(base_dir=tmp_path))

    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"empresa-test","nit":"900000000","nombre":"EMPRESA TEST"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")
    monkeypatch.setattr(orquestador, "ENTRADA_DIAN", tmp_path / "data" / "entrada-dian")
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", tmp_path / "data" / "empresas")

    original_conectar_empresa = state_store.conectar
    monkeypatch.setattr(
        state_store, "conectar",
        lambda nit, base_dir=None: original_conectar_empresa(nit, base_dir=tmp_path / "data" / "empresas"),
    )

    flask_app_module.app.config["TESTING"] = True
    with flask_app_module.app.test_client() as test_client:
        yield test_client


def _login_superusuario(client):
    conn = auth_store.conectar()
    try:
        auth_store.crear_usuario(
            conn, "super@axon.com", "superusuario", password_hash=generate_password_hash("ClaveSegura123"),
        )
    finally:
        conn.close()
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})


def _resultado_importar_fijo(nuevas=0):
    return {
        "empresa": "EMPRESA TEST", "nit": "900000000", "carpeta": "x",
        "nuevas": nuevas, "ya_existentes": 0, "duplicados": 0, "no_facturas": 0, "con_error": 0,
    }


# --- POST /api/empresas/<slug>/importar-todo ---

def test_importar_todo_endpoint_sin_sesion_da_401(client):
    r = client.post("/api/empresas/empresa-test/importar-todo")
    assert r.status_code == 401


def test_importar_todo_endpoint_ok(client, monkeypatch, tmp_path):
    _login_superusuario(client)
    (tmp_path / "data" / "entrada-dian" / "empresa-test").mkdir(parents=True)
    monkeypatch.setattr(orquestador, "ejecutar_importar", lambda s, c: _resultado_importar_fijo(3))

    r = client.post("/api/empresas/empresa-test/importar-todo")

    assert r.status_code == 200
    data = r.get_json()
    assert data["local"]["nuevas"] == 3
    assert data["drive"] is None


def test_importar_todo_endpoint_empresa_inexistente_da_404(client):
    _login_superusuario(client)
    r = client.post("/api/empresas/no-existe/importar-todo")
    assert r.status_code == 404


# --- POST /api/empresas/<slug>/importar-archivos ---

def test_importar_archivos_endpoint_sin_archivos_da_400(client):
    _login_superusuario(client)
    r = client.post("/api/empresas/empresa-test/importar-archivos", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_importar_archivos_endpoint_sube_y_luego_importa(client, monkeypatch, tmp_path):
    _login_superusuario(client)
    monkeypatch.setattr(orquestador, "ejecutar_importar", lambda s, c: _resultado_importar_fijo(1))

    r = client.post(
        "/api/empresas/empresa-test/importar-archivos",
        data={"archivos": (io.BytesIO(b"contenido-zip"), "factura.zip")},
        content_type="multipart/form-data",
    )

    assert r.status_code == 200
    data = r.get_json()
    assert data["subida"]["archivos"] == ["factura.zip"]
    assert data["local"]["nuevas"] == 1
    # el archivo de verdad quedó en disco, listo para una re-importación futura
    carpeta = Path(data["subida"]["carpeta"])
    assert (carpeta / "factura.zip").read_bytes() == b"contenido-zip"


# --- POST /api/empresas/<slug>/validar-completitud-archivo ---

def test_validar_completitud_archivo_endpoint_sin_archivo_da_400(client):
    _login_superusuario(client)
    r = client.post(
        "/api/empresas/empresa-test/validar-completitud-archivo",
        data={"desde": "2026-07-01", "hasta": "2026-07-31"}, content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_validar_completitud_archivo_endpoint_sin_rango_da_400(client):
    _login_superusuario(client)
    r = client.post(
        "/api/empresas/empresa-test/validar-completitud-archivo",
        data={"archivo": (io.BytesIO(b"no-es-un-excel-real"), "listado.xlsx")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


# --- POST /api/empresas/<slug>/reporte-faltantes-completitud ---

def test_reporte_faltantes_completitud_endpoint_sin_sesion_da_401(client):
    r = client.post("/api/empresas/empresa-test/reporte-faltantes-completitud", json={"faltantes": []})
    assert r.status_code == 401


def test_reporte_faltantes_completitud_endpoint_devuelve_xlsx_en_base64(client):
    import base64

    import openpyxl

    _login_superusuario(client)
    faltantes = [{"cufe": "CUFE-1", "fecha": "2026-07-15", "folio": "2", "prefijo": "FE",
                  "proveedor_nit": "900000000", "proveedor_nombre": "PROVEEDOR TEST", "total": 100000}]

    r = client.post("/api/empresas/empresa-test/reporte-faltantes-completitud", json={"faltantes": faltantes})

    assert r.status_code == 200
    data = r.get_json()
    contenido = base64.b64decode(data["reporte_xlsx_b64"])
    wb = openpyxl.load_workbook(io.BytesIO(contenido))
    filas = list(wb.active.iter_rows(values_only=True))
    assert filas[0] == ("Número de factura", "Fecha", "NIT emisor", "Nombre emisor", "Valor")
    assert filas[1][0] == "FE2"
