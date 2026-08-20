"""
Pruebas de los endpoints /api/auth/* y de GET /api/empresas ya protegido --
con app.test_client() de Flask. auth_store se redirige a tmp_path (nunca
toca data/sistema.db real) y correo.enviar_correo se reemplaza por un
grabador (nunca red real).
"""

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
    original_conectar = auth_store.conectar

    def _conectar_tmp(base_dir=None):
        return original_conectar(base_dir=tmp_path)

    monkeypatch.setattr(auth_store, "conectar", _conectar_tmp)

    correos_enviados = []
    monkeypatch.setattr(
        flask_app_module.auth.correo, "enviar_correo",
        lambda destinatario, asunto, cuerpo: correos_enviados.append(
            {"destinatario": destinatario, "asunto": asunto, "cuerpo": cuerpo},
        ),
    )

    flask_app_module.app.config["TESTING"] = True
    with flask_app_module.app.test_client() as test_client:
        test_client.correos_enviados = correos_enviados
        yield test_client


def _crear_usuario(email, rol, password=None, puede_crear_usuarios=False):
    conn = auth_store.conectar()
    try:
        password_hash = generate_password_hash(password) if password else None
        usuario_id = auth_store.crear_usuario(conn, email, rol, puede_crear_usuarios, password_hash=password_hash)
        return usuario_id
    finally:
        conn.close()


# --- login / logout / yo ---

def test_yo_sin_sesion_da_401(client):
    r = client.get("/api/auth/yo")
    assert r.status_code == 401


def test_login_credenciales_correctas(client):
    _crear_usuario("user@empresa.com", "empresa", password="ClaveSegura123")

    r = client.post("/api/auth/login", json={"email": "user@empresa.com", "password": "ClaveSegura123"})

    assert r.status_code == 200
    assert r.get_json()["email"] == "user@empresa.com"
    assert "password_hash" not in r.get_json()


def test_login_credenciales_incorrectas_da_401(client):
    _crear_usuario("user@empresa.com", "empresa", password="ClaveSegura123")
    r = client.post("/api/auth/login", json={"email": "user@empresa.com", "password": "mala"})
    assert r.status_code == 401


def test_login_luego_yo_devuelve_el_usuario(client):
    _crear_usuario("user@empresa.com", "contador", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "user@empresa.com", "password": "ClaveSegura123"})

    r = client.get("/api/auth/yo")

    assert r.status_code == 200
    assert r.get_json()["rol"] == "contador"


def test_logout_invalida_la_sesion(client):
    _crear_usuario("user@empresa.com", "empresa", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "user@empresa.com", "password": "ClaveSegura123"})

    client.post("/api/auth/logout")

    assert client.get("/api/auth/yo").status_code == 401


# --- /api/empresas protegido ---

def test_empresas_sin_login_da_401(client):
    assert client.get("/api/empresas").status_code == 401


def test_empresas_superusuario_ve_todas(client, monkeypatch):
    monkeypatch.setattr(
        flask_app_module.orquestador, "listar_empresas",
        lambda: [{"nit": "1", "nombre": "A"}, {"nit": "2", "nombre": "B"}],
    )
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.get("/api/empresas")

    assert r.status_code == 200
    assert len(r.get_json()) == 2


def test_empresas_contador_solo_ve_las_suyas(client, monkeypatch):
    monkeypatch.setattr(
        flask_app_module.orquestador, "listar_empresas",
        lambda: [{"nit": "1", "nombre": "A"}, {"nit": "2", "nombre": "B"}],
    )
    usuario_id = _crear_usuario("contador@empresa.com", "contador", password="ClaveSegura123")
    conn = auth_store.conectar()
    auth_store.asociar_empresa_a_usuario(conn, usuario_id, "2")
    conn.close()
    client.post("/api/auth/login", json={"email": "contador@empresa.com", "password": "ClaveSegura123"})

    r = client.get("/api/empresas")

    assert [e["nit"] for e in r.get_json()] == ["2"]


# --- invitar ---

def test_invitar_sin_login_da_401(client):
    r = client.post("/api/auth/invitar", json={"email": "nuevo@x.com", "rol": "empresa", "nits": []})
    assert r.status_code == 401


def test_invitar_sin_permiso_da_400(client):
    _crear_usuario("empresa@x.com", "empresa", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "empresa@x.com", "password": "ClaveSegura123"})

    r = client.post("/api/auth/invitar", json={"email": "nuevo@x.com", "rol": "empresa", "nits": []})

    assert r.status_code == 400


def test_superusuario_invita_y_se_envia_correo(client):
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.post("/api/auth/invitar", json={"email": "nuevo@empresa.com", "rol": "empresa", "nits": ["900111222"]})

    assert r.status_code == 200
    assert r.get_json()["email"] == "nuevo@empresa.com"
    assert len(client.correos_enviados) == 1


# --- token / fijar-password / olvide-password ---

def test_token_inexistente_no_es_valido(client):
    r = client.get("/api/auth/token/no-existe")
    assert r.get_json() == {"valido": False}


def test_fijar_password_con_token_de_invitacion(client):
    usuario_id = _crear_usuario("nuevo@empresa.com", "empresa")
    conn = auth_store.conectar()
    token = auth_store.crear_token(conn, usuario_id, "invitacion")
    conn.close()

    r_token = client.get(f"/api/auth/token/{token}")
    assert r_token.get_json()["valido"] is True
    assert r_token.get_json()["email"] == "nuevo@empresa.com"

    r_fijar = client.post("/api/auth/fijar-password", json={"token": token, "password": "NuevaClave123"})
    assert r_fijar.status_code == 200

    r_login = client.post("/api/auth/login", json={"email": "nuevo@empresa.com", "password": "NuevaClave123"})
    assert r_login.status_code == 200


def test_fijar_password_limpia_una_sesion_previa_de_otra_cuenta(client):
    """Bug real confirmado 2026-08: una empresa recién autorregistrada fijó
    su contraseña desde el enlace del correo, pero el navegador ya tenía una
    sesión activa de OTRA cuenta (ej. alguien probando con su propia cuenta
    de superusuario en la misma pestaña) -- sin limpiar la sesión vieja, la
    app seguía mostrando todo como esa cuenta anterior (llegó a ver la
    conexión de Google de una empresa ajena). `fijar-password` debe dejar al
    navegador sin sesión, para que solo quede autenticado quien haga login
    explícito con las credenciales nuevas."""
    _crear_usuario("viejo@otra-empresa.com", "empresa", password="ClaveVieja123")
    client.post("/api/auth/login", json={"email": "viejo@otra-empresa.com", "password": "ClaveVieja123"})
    assert client.get("/api/auth/yo").status_code == 200  # sesión vieja activa

    usuario_id = _crear_usuario("nuevo@empresa.com", "empresa")
    conn = auth_store.conectar()
    token = auth_store.crear_token(conn, usuario_id, "invitacion")
    conn.close()

    r_fijar = client.post("/api/auth/fijar-password", json={"token": token, "password": "NuevaClave123"})
    assert r_fijar.status_code == 200

    assert client.get("/api/auth/yo").status_code == 401  # la sesión vieja ya no sirve


def test_olvide_password_siempre_responde_200_exista_o_no_el_correo(client):
    assert client.post("/api/auth/olvide-password", json={"email": "nadie@x.com"}).status_code == 200
    _crear_usuario("si-existe@x.com", "empresa", password="ClaveSegura123")
    assert client.post("/api/auth/olvide-password", json={"email": "si-existe@x.com"}).status_code == 200


# --- endpoints /api/empresas/<slug>/... (Sesión 5) ---

def _mockear_empresa(monkeypatch, slug="empresa-test", nit="900111222"):
    monkeypatch.setattr(
        flask_app_module.orquestador, "resolver_empresa",
        lambda s: {"slug": slug, "nit": nit, "nombre": "EMPRESA TEST"} if s == slug else (_ for _ in ()).throw(
            flask_app_module.orquestador.EmpresaNoEncontrada(f"No existe '{s}'"),
        ),
    )
    monkeypatch.setattr(flask_app_module.orquestador, "listar_facturas", lambda s: [])


def test_endpoint_de_empresa_sin_login_da_401(client, monkeypatch):
    _mockear_empresa(monkeypatch)
    r = client.get("/api/empresas/empresa-test/facturas")
    assert r.status_code == 401


def test_endpoint_de_empresa_sin_acceso_da_403(client, monkeypatch):
    _mockear_empresa(monkeypatch)
    _crear_usuario("otra-empresa@x.com", "empresa", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "otra-empresa@x.com", "password": "ClaveSegura123"})

    r = client.get("/api/empresas/empresa-test/facturas")

    assert r.status_code == 403


def test_endpoint_de_empresa_con_acceso_funciona(client, monkeypatch):
    _mockear_empresa(monkeypatch)
    usuario_id = _crear_usuario("dueño@x.com", "empresa", password="ClaveSegura123")
    conn = auth_store.conectar()
    auth_store.asociar_empresa_a_usuario(conn, usuario_id, "900111222")
    conn.close()
    client.post("/api/auth/login", json={"email": "dueño@x.com", "password": "ClaveSegura123"})

    r = client.get("/api/empresas/empresa-test/facturas")

    assert r.status_code == 200


def test_endpoint_de_empresa_superusuario_siempre_tiene_acceso(client, monkeypatch):
    _mockear_empresa(monkeypatch)
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.get("/api/empresas/empresa-test/facturas")

    assert r.status_code == 200


# --- endpoints /api/sistema/... (solo superusuario) ---

def test_endpoint_sistema_sin_login_da_401(client):
    assert client.get("/api/sistema/config-correo").status_code == 401


def test_endpoint_sistema_usuario_normal_da_403(client):
    _crear_usuario("empresa@x.com", "empresa", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "empresa@x.com", "password": "ClaveSegura123"})

    r = client.get("/api/sistema/config-correo")

    assert r.status_code == 403


def test_endpoint_sistema_superusuario_funciona(client):
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.get("/api/sistema/config-correo")

    assert r.status_code == 200


# --- /api/conexiones-google (requiere login, sin filtrar por dueño todavía -- Sesión 8) ---

def test_conexiones_google_sin_login_da_401(client):
    assert client.get("/api/conexiones-google").status_code == 401


def test_conexiones_google_con_login_funciona(client, monkeypatch):
    monkeypatch.setattr(flask_app_module.orquestador, "listar_conexiones_google", lambda: [])
    _crear_usuario("empresa@x.com", "empresa", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "empresa@x.com", "password": "ClaveSegura123"})

    assert client.get("/api/conexiones-google").status_code == 200


# --- gestión de usuarios (Sesión 7) ---

def test_listar_usuarios_superusuario_ve_todos(client):
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    _crear_usuario("otro@x.com", "empresa")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.get("/api/auth/usuarios")

    assert r.status_code == 200
    assert {u["email"] for u in r.get_json()} == {"super@axon.com", "otro@x.com"}


def test_listar_usuarios_sin_permiso_da_403(client):
    _crear_usuario("empresa@x.com", "empresa", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "empresa@x.com", "password": "ClaveSegura123"})

    assert client.get("/api/auth/usuarios").status_code == 403


def test_reenviar_invitacion_endpoint(client):
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})
    r_invitar = client.post("/api/auth/invitar", json={"email": "nuevo@x.com", "rol": "empresa", "nits": []})
    usuario_id = r_invitar.get_json()["id"]
    client.correos_enviados.clear()

    r = client.post(f"/api/auth/usuarios/{usuario_id}/reenviar-invitacion")

    assert r.status_code == 200
    assert len(client.correos_enviados) == 1


def test_actualizar_empresas_usuario_endpoint(client):
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})
    r_invitar = client.post("/api/auth/invitar", json={"email": "nuevo@x.com", "rol": "empresa", "nits": []})
    usuario_id = r_invitar.get_json()["id"]

    r = client.post(f"/api/auth/usuarios/{usuario_id}/empresas", json={"nits": ["900111222"]})

    assert r.status_code == 200
    assert r.get_json()["nits"] == ["900111222"]


def test_cambiar_estado_usuario_endpoint(client):
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})
    usuario_id = _crear_usuario("nuevo@x.com", "empresa", password="ClaveSegura123")

    r = client.post(f"/api/auth/usuarios/{usuario_id}/estado", json={"activo": False})

    assert r.status_code == 200
    assert client.post("/api/auth/login", json={"email": "nuevo@x.com", "password": "ClaveSegura123"}).status_code == 401


# --- POST /api/auth/registrar-empresa (autorregistro público, sin sesión) ---

@pytest.fixture
def client_registro(client, tmp_path, monkeypatch):
    """`client` ya redirige auth_store a tmp_path -- acá además se redirige
    el registro de empresas y la base aislada, para que la prueba del
    endpoint público nunca toque config/empresas/registro.json real."""
    registro = tmp_path / "registro.json"
    registro.write_text('{"empresas": []}', encoding="utf-8")
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", tmp_path / "data" / "empresas")

    original_conectar_empresa = state_store.conectar
    monkeypatch.setattr(
        state_store, "conectar",
        lambda nit, base_dir=None: original_conectar_empresa(nit, base_dir=tmp_path / "data" / "empresas"),
    )
    return client


def test_registrar_empresa_no_requiere_sesion(client_registro):
    r = client_registro.post("/api/auth/registrar-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva S.A.S.", "email": "dueno@empresa.com",
    })
    assert r.status_code == 200
    assert r.get_json()["registrado"] is True
    assert len(client_registro.correos_enviados) == 1


def test_registrar_empresa_nit_duplicado_da_400(client_registro):
    client_registro.post("/api/auth/registrar-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Uno", "email": "uno@empresa.com",
    })
    r = client_registro.post("/api/auth/registrar-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Dos", "email": "dos@empresa.com",
    })
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_registrar_empresa_no_acepta_rol_desde_el_body(client_registro):
    """Aunque alguien mande "rol":"superusuario" en el body, el endpoint
    nunca lo lee -- orquestador.registrar_empresa_nueva ni siquiera tiene
    ese parámetro (ver tests/test_registro_empresa.py)."""
    r = client_registro.post("/api/auth/registrar-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva", "email": "dueno@empresa.com",
        "rol": "superusuario", "puede_crear_usuarios": True,
    })
    assert r.status_code == 200

    conn = auth_store.conectar()
    try:
        usuario = auth_store.obtener_usuario_por_email(conn, "dueno@empresa.com")
        assert usuario["rol"] == "empresa"
        assert usuario["puede_crear_usuarios"] is False
    finally:
        conn.close()


def test_registrar_empresa_usuario_creado_no_puede_iniciar_sesion_sin_confirmar(client_registro):
    client_registro.post("/api/auth/registrar-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva", "email": "dueno@empresa.com",
    })
    # todavía no fijó contraseña (no hizo clic en el correo) -- cualquier
    # intento de login debe fallar, nunca quedar "medio adentro"
    r = client_registro.post("/api/auth/login", json={"email": "dueno@empresa.com", "password": "cualquiera"})
    assert r.status_code == 401


# --- POST /api/auth/crear-empresa (superusuario o contador con permiso) ---

def test_crear_empresa_sin_login_da_401(client_registro):
    r = client_registro.post("/api/auth/crear-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva", "email": "dueno@empresa.com",
    })
    assert r.status_code == 401


def test_crear_empresa_sin_permiso_da_400(client_registro):
    _crear_usuario("empresa@x.com", "empresa", password="ClaveSegura123")
    client_registro.post("/api/auth/login", json={"email": "empresa@x.com", "password": "ClaveSegura123"})

    r = client_registro.post("/api/auth/crear-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva", "email": "dueno@empresa.com",
    })

    assert r.status_code == 400


def test_crear_empresa_contador_sin_permiso_da_400(client_registro):
    _crear_usuario("aux@firma.com", "contador", password="ClaveSegura123", puede_crear_usuarios=False)
    client_registro.post("/api/auth/login", json={"email": "aux@firma.com", "password": "ClaveSegura123"})

    r = client_registro.post("/api/auth/crear-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva", "email": "dueno@empresa.com",
    })

    assert r.status_code == 400


def test_crear_empresa_superusuario_ok(client_registro):
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client_registro.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client_registro.post("/api/auth/crear-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva S.A.S.", "email": "dueno@empresa.com",
    })

    assert r.status_code == 200
    assert r.get_json()["creado"] is True
    assert len(client_registro.correos_enviados) == 1

    conn = auth_store.conectar()
    try:
        dueno = auth_store.obtener_usuario_por_email(conn, "dueno@empresa.com")
        assert dueno["rol"] == "empresa"
        assert dueno["password_hash"] is None  # no puede iniciar sesión hasta fijarla
    finally:
        conn.close()


def test_crear_empresa_contador_con_permiso_queda_asociada_a_el(client_registro):
    _crear_usuario("contador@firma.com", "contador", password="ClaveSegura123", puede_crear_usuarios=True)
    client_registro.post("/api/auth/login", json={"email": "contador@firma.com", "password": "ClaveSegura123"})

    r = client_registro.post("/api/auth/crear-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva", "email": "dueno@empresa.com",
    })
    assert r.status_code == 200
    nit_creado = r.get_json()["nit"]

    # el contador que la creó ya puede verla, sin que nadie más se la asigne
    r_empresas = client_registro.get("/api/empresas")
    assert nit_creado in [e["nit"] for e in r_empresas.get_json()]


def test_crear_empresa_no_es_visible_para_otro_contador_no_asociado(client_registro):
    _crear_usuario("contador1@firma.com", "contador", password="ClaveSegura123", puede_crear_usuarios=True)
    client_registro.post("/api/auth/login", json={"email": "contador1@firma.com", "password": "ClaveSegura123"})
    r = client_registro.post("/api/auth/crear-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva", "email": "dueno@empresa.com",
    })
    nit_creado = r.get_json()["nit"]
    client_registro.post("/api/auth/logout")

    _crear_usuario("contador2@firma.com", "contador", password="ClaveSegura123", puede_crear_usuarios=True)
    client_registro.post("/api/auth/login", json={"email": "contador2@firma.com", "password": "ClaveSegura123"})

    r_empresas = client_registro.get("/api/empresas")
    assert nit_creado not in [e["nit"] for e in r_empresas.get_json()]


def test_crear_empresa_nit_duplicado_da_400(client_registro):
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client_registro.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})
    client_registro.post("/api/auth/crear-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Uno", "email": "uno@empresa.com",
    })

    r = client_registro.post("/api/auth/crear-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Dos", "email": "dos@empresa.com",
    })

    assert r.status_code == 400


# --- POST /api/auth/usuarios/<id>/rol (solo superusuario) ---

def test_cambiar_rol_usuario_endpoint_requiere_superusuario(client):
    _crear_usuario("contador@firma.com", "contador", password="ClaveSegura123", puede_crear_usuarios=True)
    usuario_id = _crear_usuario("nuevo@x.com", "empresa", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "contador@firma.com", "password": "ClaveSegura123"})

    r = client.post(f"/api/auth/usuarios/{usuario_id}/rol", json={"rol": "superusuario"})

    assert r.status_code == 403


def test_cambiar_rol_usuario_endpoint_ok(client):
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    usuario_id = _crear_usuario("nuevo@x.com", "empresa", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.post(f"/api/auth/usuarios/{usuario_id}/rol", json={"rol": "contador"})

    assert r.status_code == 200
    assert r.get_json()["rol"] == "contador"


def test_cambiar_rol_usuario_endpoint_sin_sesion_da_401(client):
    usuario_id = _crear_usuario("nuevo@x.com", "empresa", password="ClaveSegura123")
    r = client.post(f"/api/auth/usuarios/{usuario_id}/rol", json={"rol": "superusuario"})
    assert r.status_code == 401


# --- DELETE /api/auth/usuarios/<id> (solo superusuario) ---

def test_eliminar_usuario_endpoint_requiere_superusuario(client):
    _crear_usuario("contador@firma.com", "contador", password="ClaveSegura123", puede_crear_usuarios=True)
    usuario_id = _crear_usuario("nuevo@x.com", "empresa", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "contador@firma.com", "password": "ClaveSegura123"})

    r = client.delete(f"/api/auth/usuarios/{usuario_id}")

    assert r.status_code == 403
    assert auth_store.obtener_usuario_por_id(auth_store.conectar(), usuario_id) is not None


def test_eliminar_usuario_endpoint_ok(client):
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    usuario_id = _crear_usuario("nuevo@x.com", "empresa", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.delete(f"/api/auth/usuarios/{usuario_id}")

    assert r.status_code == 200
    conn = auth_store.conectar()
    try:
        assert auth_store.obtener_usuario_por_id(conn, usuario_id) is None
    finally:
        conn.close()


def test_eliminar_usuario_endpoint_no_puede_eliminarse_a_si_mismo(client):
    super_id = _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.delete(f"/api/auth/usuarios/{super_id}")

    assert r.status_code == 400


# --- DELETE /api/empresas/<slug> (solo superusuario) ---

def test_eliminar_empresa_endpoint_requiere_superusuario(client_registro):
    client_registro.post("/api/auth/registrar-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva", "email": "dueno@empresa.com",
    })
    _crear_usuario("contador@firma.com", "contador", password="ClaveSegura123", puede_crear_usuarios=True)
    client_registro.post("/api/auth/login", json={"email": "contador@firma.com", "password": "ClaveSegura123"})

    r = client_registro.delete("/api/empresas/empresa-nueva")

    assert r.status_code == 403


def test_eliminar_empresa_endpoint_ok(client_registro):
    resultado = client_registro.post("/api/auth/registrar-empresa", json={
        "nit": "900123456", "razon_social": "Empresa Nueva", "email": "dueno@empresa.com",
    }).get_json()
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client_registro.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client_registro.delete(f"/api/empresas/{resultado['slug']}")

    assert r.status_code == 200
    assert r.get_json()["eliminada"] is True

    # ya no aparece ni para el superusuario
    r_empresas = client_registro.get("/api/empresas")
    assert resultado["nit"] not in [e["nit"] for e in r_empresas.get_json()]


def test_eliminar_empresa_endpoint_sin_sesion_da_401(client_registro):
    r = client_registro.delete("/api/empresas/lo-que-sea")
    assert r.status_code == 401


# --- endpoints /api/empresas/<slug>/reglas-propuestas ---

@pytest.fixture
def reglas_dir(tmp_path, monkeypatch):
    ruta = tmp_path / "data" / "reglas-propuestas"
    monkeypatch.setattr(flask_app_module.orquestador, "REGLAS_PROPUESTAS_DIR", ruta)
    return ruta


def test_reglas_propuestas_sin_login_da_401(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    r = client.get("/api/empresas/empresa-test/reglas-propuestas")
    assert r.status_code == 401


def test_crear_regla_propuesta_rol_empresa_da_400(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    usuario_id = _crear_usuario("dueño@x.com", "empresa", password="ClaveSegura123")
    conn = auth_store.conectar()
    auth_store.asociar_empresa_a_usuario(conn, usuario_id, "900111222")
    conn.close()
    client.post("/api/auth/login", json={"email": "dueño@x.com", "password": "ClaveSegura123"})

    r = client.post("/api/empresas/empresa-test/reglas-propuestas", json={"texto": "Una duda"})

    assert r.status_code == 400


def test_crear_regla_propuesta_contador_ok(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    usuario_id = _crear_usuario("contador@firma.com", "contador", password="ClaveSegura123")
    conn = auth_store.conectar()
    auth_store.asociar_empresa_a_usuario(conn, usuario_id, "900111222")
    conn.close()
    client.post("/api/auth/login", json={"email": "contador@firma.com", "password": "ClaveSegura123"})

    r = client.post("/api/empresas/empresa-test/reglas-propuestas", json={"texto": "IVA no discriminado"})

    assert r.status_code == 200
    assert r.get_json()["texto"] == "IVA no discriminado"
    assert r.get_json()["estado"] == "pendiente"


def test_crear_regla_propuesta_texto_vacio_da_400(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.post("/api/empresas/empresa-test/reglas-propuestas", json={"texto": "   "})

    assert r.status_code == 400


def test_listar_reglas_propuestas_superusuario_ve_las_de_cualquier_empresa(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})
    client.post("/api/empresas/empresa-test/reglas-propuestas", json={"texto": "Regla uno"})
    client.post("/api/empresas/empresa-test/reglas-propuestas", json={"texto": "Regla dos"})

    r = client.get("/api/empresas/empresa-test/reglas-propuestas")

    assert r.status_code == 200
    assert len(r.get_json()) == 2


def test_cambiar_estado_regla_propuesta_ok(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})
    regla_id = client.post(
        "/api/empresas/empresa-test/reglas-propuestas", json={"texto": "Regla"},
    ).get_json()["id"]

    r = client.patch(
        f"/api/empresas/empresa-test/reglas-propuestas/{regla_id}",
        json={"estado": "aplicada", "respuesta": "Se ajustó el motor de reglas."},
    )

    assert r.status_code == 200
    assert r.get_json()["estado"] == "aplicada"
    assert r.get_json()["aplicada_en"] is not None


def test_cambiar_estado_regla_propuesta_estado_invalido_da_400(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})
    regla_id = client.post(
        "/api/empresas/empresa-test/reglas-propuestas", json={"texto": "Regla"},
    ).get_json()["id"]

    r = client.patch(
        f"/api/empresas/empresa-test/reglas-propuestas/{regla_id}", json={"estado": "no-existe"},
    )

    assert r.status_code == 400


# --- endpoint /api/empresas/<slug>/reglas-confirmadas (solo lectura) ---

@pytest.fixture
def reglas_confirmadas_dirs(tmp_path, monkeypatch):
    config_empresas = tmp_path / "config" / "empresas"
    config_proveedores = tmp_path / "config" / "proveedores"
    base_datos_empresas = tmp_path / "data" / "empresas"
    config_empresas.mkdir(parents=True)
    config_proveedores.mkdir(parents=True)
    monkeypatch.setattr(flask_app_module.orquestador, "CONFIG_EMPRESAS_DIR", config_empresas)
    monkeypatch.setattr(flask_app_module.orquestador, "CONFIG_PROVEEDORES_DIR", config_proveedores)
    monkeypatch.setattr(flask_app_module.orquestador, "BASE_DATOS_EMPRESAS", base_datos_empresas)
    return config_empresas, config_proveedores, base_datos_empresas


def test_reglas_confirmadas_sin_login_da_401(client, monkeypatch, reglas_confirmadas_dirs):
    _mockear_empresa(monkeypatch)
    r = client.get("/api/empresas/empresa-test/reglas-confirmadas")
    assert r.status_code == 401


def test_reglas_confirmadas_rol_empresa_da_400(client, monkeypatch, reglas_confirmadas_dirs):
    _mockear_empresa(monkeypatch)
    usuario_id = _crear_usuario("dueño@x.com", "empresa", password="ClaveSegura123")
    conn = auth_store.conectar()
    auth_store.asociar_empresa_a_usuario(conn, usuario_id, "900111222")
    conn.close()
    client.post("/api/auth/login", json={"email": "dueño@x.com", "password": "ClaveSegura123"})

    r = client.get("/api/empresas/empresa-test/reglas-confirmadas")

    assert r.status_code == 400


def test_reglas_confirmadas_superusuario_ok(client, monkeypatch, reglas_confirmadas_dirs):
    _mockear_empresa(monkeypatch)
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.get("/api/empresas/empresa-test/reglas-confirmadas")

    assert r.status_code == 200
    assert r.get_json() == {"politicas_empresa": [], "perfiles_proveedor": []}


def test_reglas_confirmadas_incluye_politica_activa(client, monkeypatch, reglas_confirmadas_dirs):
    config_empresas, _, _ = reglas_confirmadas_dirs
    _mockear_empresa(monkeypatch)
    (config_empresas / "900111222.json").write_text(
        '{"politicas": {"iva_no_discriminado": {"activa": true, "comportamiento": {"cuenta_contable": "519595"}}}}',
        encoding="utf-8",
    )
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})

    r = client.get("/api/empresas/empresa-test/reglas-confirmadas")

    assert r.status_code == 200
    assert r.get_json()["politicas_empresa"][0]["clave"] == "iva_no_discriminado"


# --- endpoint DELETE /api/empresas/<slug>/reglas-propuestas/<id> ---

def test_eliminar_regla_propuesta_endpoint_ok(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})
    regla_id = client.post(
        "/api/empresas/empresa-test/reglas-propuestas", json={"texto": "prueba"},
    ).get_json()["id"]

    r = client.delete(f"/api/empresas/empresa-test/reglas-propuestas/{regla_id}")

    assert r.status_code == 200
    assert r.get_json()["eliminada"] is True
    assert client.get("/api/empresas/empresa-test/reglas-propuestas").get_json() == []


def test_eliminar_regla_propuesta_endpoint_ya_respondida_da_400(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})
    regla_id = client.post(
        "/api/empresas/empresa-test/reglas-propuestas", json={"texto": "prueba"},
    ).get_json()["id"]
    client.patch(
        f"/api/empresas/empresa-test/reglas-propuestas/{regla_id}",
        json={"estado": "aplicada", "respuesta": "x"},
    )

    r = client.delete(f"/api/empresas/empresa-test/reglas-propuestas/{regla_id}")

    assert r.status_code == 400


def test_eliminar_regla_propuesta_endpoint_rol_empresa_da_400(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    usuario_id = _crear_usuario("dueño@x.com", "empresa", password="ClaveSegura123")
    conn = auth_store.conectar()
    auth_store.asociar_empresa_a_usuario(conn, usuario_id, "900111222")
    conn.close()
    _crear_usuario("super@axon.com", "superusuario", password="ClaveSegura123")
    client.post("/api/auth/login", json={"email": "super@axon.com", "password": "ClaveSegura123"})
    regla_id = client.post(
        "/api/empresas/empresa-test/reglas-propuestas", json={"texto": "prueba"},
    ).get_json()["id"]
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "dueño@x.com", "password": "ClaveSegura123"})

    r = client.delete(f"/api/empresas/empresa-test/reglas-propuestas/{regla_id}")

    assert r.status_code == 400


def test_eliminar_regla_propuesta_endpoint_sin_login_da_401(client, monkeypatch, reglas_dir):
    _mockear_empresa(monkeypatch)
    r = client.delete("/api/empresas/empresa-test/reglas-propuestas/1")
    assert r.status_code == 401
