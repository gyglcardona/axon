"""
Pruebas de la ingesta desde Gmail en orquestador.py: configuración
(conexion_gmail), y importar_desde_gmail (descarga solo adjuntos nuevos,
actualiza ultima_sincronizacion). Nunca toca la red real -- gmail_client y
google_conexiones se reemplazan por versiones falsas.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import auth_store  # noqa: E402
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
    monkeypatch.setattr(google_conexiones, "obtener_credenciales", lambda conexion_id: object())
    return "empresa-test", tmp_path


def test_guardar_conexion_gmail_bloquea_activar_con_conexion_legacy(empresa_configurada):
    slug, _ = empresa_configurada
    with pytest.raises(ValueError, match="conexión compartida"):
        orquestador.guardar_conexion_gmail(slug, {"activo": True})


def test_guardar_conexion_gmail_permite_activar_con_conexion_propia(empresa_configurada):
    slug, _ = empresa_configurada
    orquestador.asociar_conexion_google(slug, "conexion-propia-123")

    orquestador.guardar_conexion_gmail(slug, {"activo": True, "desde_fecha": "2026-01-01"})

    config = orquestador.obtener_conexion_gmail(slug)
    assert config["activo"] is True
    assert config["desde_fecha"] == "2026-01-01"


def test_guardar_conexion_gmail_no_sobreescribe_desde_fecha_ya_fijada(empresa_configurada):
    slug, _ = empresa_configurada
    orquestador.asociar_conexion_google(slug, "conexion-propia-123")
    orquestador.guardar_conexion_gmail(slug, {"desde_fecha": "2026-01-01"})

    orquestador.guardar_conexion_gmail(slug, {"desde_fecha": "2026-06-01"})

    assert orquestador.obtener_conexion_gmail(slug)["desde_fecha"] == "2026-01-01"


def test_importar_desde_gmail_sin_activar_da_error_claro(empresa_configurada):
    slug, _ = empresa_configurada
    with pytest.raises(ValueError, match="Gmail"):
        orquestador.importar_desde_gmail(slug)


def _configurar_gmail_activo(slug, desde_fecha="2026-01-01"):
    orquestador.asociar_conexion_google(slug, "conexion-propia-123")
    orquestador.guardar_conexion_gmail(slug, {"activo": True, "desde_fecha": desde_fecha})


def test_importar_desde_gmail_descarga_y_actualiza_ultima_sincronizacion(empresa_configurada, monkeypatch):
    slug, tmp_path = empresa_configurada
    _configurar_gmail_activo(slug)

    adjuntos = [
        {"message_id": "m1", "attachment_id": "a1", "filename": "factura1.zip", "fecha_interna": "1700000000000"},
        {"message_id": "m2", "attachment_id": "a2", "filename": "factura2.zip", "fecha_interna": "1700086400000"},
    ]
    contenidos = {("m1", "a1"): b"contenido-1", ("m2", "a2"): b"contenido-2"}

    monkeypatch.setattr("gmail_client.buscar_adjuntos_zip", lambda creds, desde, spam: adjuntos)
    monkeypatch.setattr("gmail_client.descargar_adjunto", lambda creds, mid, aid: contenidos[(mid, aid)])
    monkeypatch.setattr(orquestador, "ejecutar_importar", lambda slug_recibido, carpeta: {
        "empresa": "EMPRESA TEST", "nit": "900000000", "carpeta": "x",
        "nuevas": 2, "ya_existentes": 0, "duplicados": 0, "no_facturas": 0, "con_error": 0,
    })

    resumen = orquestador.importar_desde_gmail(slug)

    assert resumen["descargados"] == 2
    assert resumen["ya_estaban_localmente"] == 0
    assert resumen["nuevas"] == 2
    # el mensaje más reciente (fecha_interna mayor) fija la nueva ultima_sincronizacion
    assert orquestador.obtener_conexion_gmail(slug)["ultima_sincronizacion"] == "2023-11-15"


def test_importar_desde_gmail_no_descarga_dos_veces_el_mismo_adjunto(empresa_configurada, monkeypatch):
    """El nombre de archivo incluye el message_id -- si ya existe en disco de
    una corrida anterior, una corrida nueva no debe volver a descargarlo."""
    slug, tmp_path = empresa_configurada
    _configurar_gmail_activo(slug)

    ya_local = tmp_path / "data" / "entrada-dian" / slug / "gmail" / "2023" / "11" / "m1_factura1.zip"
    ya_local.parent.mkdir(parents=True)
    ya_local.write_bytes(b"contenido-viejo-no-se-toca")

    adjuntos = [
        {"message_id": "m1", "attachment_id": "a1", "filename": "factura1.zip", "fecha_interna": "1700000000000"},
    ]

    def _descargar_no_deberia_llamarse(creds, mid, aid):
        raise AssertionError("no debía descargar nada -- el adjunto ya existe localmente")

    monkeypatch.setattr("gmail_client.buscar_adjuntos_zip", lambda creds, desde, spam: adjuntos)
    monkeypatch.setattr("gmail_client.descargar_adjunto", _descargar_no_deberia_llamarse)
    monkeypatch.setattr(orquestador, "ejecutar_importar", lambda slug_recibido, carpeta: {
        "empresa": "EMPRESA TEST", "nit": "900000000", "carpeta": "x",
        "nuevas": 0, "ya_existentes": 1, "duplicados": 0, "no_facturas": 0, "con_error": 0,
    })

    resumen = orquestador.importar_desde_gmail(slug)

    assert resumen["descargados"] == 0
    assert resumen["ya_estaban_localmente"] == 1
    assert ya_local.read_bytes() == b"contenido-viejo-no-se-toca"


def test_importar_desde_google_sin_nada_configurado_da_error_claro(empresa_configurada):
    slug, _ = empresa_configurada
    with pytest.raises(ValueError, match="Google"):
        orquestador.importar_desde_google(slug)


def test_importar_desde_google_corre_drive_y_gmail(empresa_configurada, monkeypatch):
    slug, _ = empresa_configurada
    orquestador.guardar_conexion_drive(slug, "CARPETA-ID")
    _configurar_gmail_activo(slug)

    llamadas = []
    monkeypatch.setattr(orquestador, "importar_desde_drive", lambda s: llamadas.append("drive") or {"descargados": 0})
    monkeypatch.setattr(orquestador, "importar_desde_gmail", lambda s: llamadas.append("gmail") or {"descargados": 0})

    resultado = orquestador.importar_desde_google(slug)

    assert llamadas == ["drive", "gmail"]
    assert resultado["drive"] == {"descargados": 0}
    assert resultado["gmail"] == {"descargados": 0}


# --- Sesión 8 del plan de login: conexiones de Google filtradas por dueño ---

@pytest.fixture
def dos_empresas_con_usuarios(tmp_path, monkeypatch):
    """Dos empresas independientes (A y B), cada una con su propio usuario
    rol 'empresa', más un superusuario -- para probar que ninguna ve ni
    puede asociar la conexión de Google de la otra."""
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":['
        '{"slug":"empresa-a","nit":"900000001","nombre":"EMPRESA A"},'
        '{"slug":"empresa-b","nit":"900000002","nombre":"EMPRESA B"}'
        ']}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")
    monkeypatch.setattr(orquestador, "ENTRADA_DIAN", tmp_path / "data" / "entrada-dian")
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", tmp_path / "data" / "empresas")

    google_dir = tmp_path / "config" / "google"
    monkeypatch.setattr(google_conexiones, "GOOGLE_DIR", google_dir)
    monkeypatch.setattr(google_conexiones, "CLIENT_SECRET_WEB_PATH", google_dir / "client_secret_web.json")
    monkeypatch.setattr(google_conexiones, "TOKEN_LEGACY_PATH", google_dir / "token.json")
    monkeypatch.setattr(google_conexiones, "CONEXIONES_DIR", google_dir / "conexiones")
    monkeypatch.setattr(google_conexiones, "REGISTRO_PATH", google_dir / "conexiones" / "registro.json")
    monkeypatch.setattr(google_conexiones, "PENDIENTES_PATH", google_dir / "oauth_pendientes.json")

    _conectar_original = auth_store.conectar
    monkeypatch.setattr(orquestador.auth_store, "conectar", lambda base_dir=None: _conectar_original(base_dir=tmp_path))

    conn = auth_store.conectar(base_dir=tmp_path)
    superusuario_id = auth_store.crear_usuario(conn, "super@axon.com", "superusuario", puede_crear_usuarios=True)
    usuario_a_id = auth_store.crear_usuario(conn, "a@empresa-a.com", "empresa", puede_crear_usuarios=False)
    auth_store.asociar_empresa_a_usuario(conn, usuario_a_id, "900000001")
    usuario_b_id = auth_store.crear_usuario(conn, "b@empresa-b.com", "empresa", puede_crear_usuarios=False)
    auth_store.asociar_empresa_a_usuario(conn, usuario_b_id, "900000002")
    conn.close()

    def _usuario(usuario_id, rol, puede_crear_usuarios=False):
        return {"id": usuario_id, "rol": rol, "puede_crear_usuarios": puede_crear_usuarios}

    usuario_super = _usuario(superusuario_id, "superusuario", True)
    usuario_a = _usuario(usuario_a_id, "empresa")
    usuario_b = _usuario(usuario_b_id, "empresa")

    # Conexión de Google creada por el usuario de la empresa A, y asociada a
    # ella -- como si hubiera pasado por el flujo OAuth real.
    conexiones = [{
        "id": "conexion-de-a", "cuenta_email": "a@gmail.com", "etiqueta": "a@gmail.com",
        "creado_en": "2026-01-01T00:00:00+00:00", "creado_por_usuario_id": usuario_a_id,
    }]
    (google_dir / "conexiones").mkdir(parents=True)
    (google_dir / "conexiones" / "registro.json").write_text(
        json.dumps(conexiones), encoding="utf-8",
    )
    orquestador.asociar_conexion_google("empresa-a", "conexion-de-a")

    return {"super": usuario_super, "a": usuario_a, "b": usuario_b}


def test_listar_conexiones_google_visibles_superusuario_ve_todas(dos_empresas_con_usuarios):
    usuarios = dos_empresas_con_usuarios
    ids = {c["id"] for c in orquestador.listar_conexiones_google_visibles(usuarios["super"])}
    assert ids == {"conexion-de-a"}


def test_listar_conexiones_google_visibles_dueno_ve_la_suya(dos_empresas_con_usuarios):
    usuarios = dos_empresas_con_usuarios
    ids = {c["id"] for c in orquestador.listar_conexiones_google_visibles(usuarios["a"])}
    assert ids == {"conexion-de-a"}


def test_listar_conexiones_google_visibles_otra_empresa_no_la_ve(dos_empresas_con_usuarios):
    """El gap real que motivó la Sesión 8: antes de este cambio, el usuario
    de la empresa B veía (y podía asociar) la conexión de Google de la
    empresa A con solo estar logueado."""
    usuarios = dos_empresas_con_usuarios
    ids = {c["id"] for c in orquestador.listar_conexiones_google_visibles(usuarios["b"])}
    assert ids == set()


def test_usuario_puede_usar_conexion_google_solo_el_dueno_o_su_empresa(dos_empresas_con_usuarios):
    usuarios = dos_empresas_con_usuarios
    assert orquestador.usuario_puede_usar_conexion_google(usuarios["a"], "conexion-de-a") is True
    assert orquestador.usuario_puede_usar_conexion_google(usuarios["b"], "conexion-de-a") is False
    assert orquestador.usuario_puede_usar_conexion_google(usuarios["super"], "conexion-de-a") is True


def test_asociar_conexion_google_para_usuario_bloquea_conexion_ajena(dos_empresas_con_usuarios):
    usuarios = dos_empresas_con_usuarios
    with pytest.raises(ValueError, match="No tienes acceso"):
        orquestador.asociar_conexion_google_para_usuario("empresa-b", "conexion-de-a", usuarios["b"])

    # nunca se guardó -- la empresa B sigue sin conexión propia
    assert orquestador.obtener_conexion_drive("empresa-b")["conexion_id"] == ""


def test_asociar_conexion_google_para_usuario_permite_al_dueno(dos_empresas_con_usuarios):
    usuarios = dos_empresas_con_usuarios
    orquestador.asociar_conexion_google_para_usuario("empresa-a", "conexion-de-a", usuarios["a"])
    assert orquestador.obtener_conexion_drive("empresa-a")["conexion_id"] == "conexion-de-a"
