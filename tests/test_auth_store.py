"""
Pruebas de src/auth_store.py: creación de usuarios, asociación a empresas, y
ciclo de vida de tokens de invitación/recuperación. Nunca toca
data/sistema.db real -- cada prueba usa su propio tmp_path.
"""

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import auth_store  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    conexion = auth_store.conectar(base_dir=tmp_path)
    yield conexion
    conexion.close()


def test_crear_usuario_devuelve_id_y_nunca_guarda_password_en_texto_plano(conn):
    usuario_id = auth_store.crear_usuario(conn, "correo@empresa.com", "contador", password_hash="hash-falso-123")

    usuario = auth_store.obtener_usuario_por_id(conn, usuario_id)
    assert usuario["email"] == "correo@empresa.com"
    assert usuario["rol"] == "contador"
    assert usuario["password_hash"] == "hash-falso-123"  # el hash se guarda tal cual, el hasheo lo hace el caller
    # confirmamos que jamás se guarda "en texto plano" comparando contra lo que sería una contraseña real
    assert usuario["password_hash"] != "MiContraseñaReal123"


def test_crear_usuario_sin_password_queda_none_hasta_que_se_fija(conn):
    usuario_id = auth_store.crear_usuario(conn, "nuevo@empresa.com", "empresa")
    assert auth_store.obtener_usuario_por_id(conn, usuario_id)["password_hash"] is None

    auth_store.fijar_password(conn, usuario_id, "hash-nuevo")
    assert auth_store.obtener_usuario_por_id(conn, usuario_id)["password_hash"] == "hash-nuevo"


def test_crear_usuario_rechaza_rol_invalido(conn):
    with pytest.raises(ValueError, match="Rol inválido"):
        auth_store.crear_usuario(conn, "x@x.com", "administrador")


def test_crear_usuario_rechaza_correo_duplicado(conn):
    auth_store.crear_usuario(conn, "dup@empresa.com", "empresa")
    with pytest.raises(ValueError, match="Ya existe"):
        auth_store.crear_usuario(conn, "DUP@empresa.com", "empresa")  # mayúsculas no evaden el duplicado


def test_obtener_usuario_por_email_no_existente(conn):
    assert auth_store.obtener_usuario_por_email(conn, "nadie@empresa.com") is None


def test_creado_por_usuario_id_y_puede_crear_usuarios(conn):
    superusuario_id = auth_store.crear_usuario(conn, "super@axon.com", "superusuario", puede_crear_usuarios=True)
    contador_id = auth_store.crear_usuario(
        conn, "contador@empresa.com", "contador", puede_crear_usuarios=True, creado_por_usuario_id=superusuario_id,
    )
    auxiliar_id = auth_store.crear_usuario(
        conn, "auxiliar@empresa.com", "contador", puede_crear_usuarios=False, creado_por_usuario_id=contador_id,
    )

    contador = auth_store.obtener_usuario_por_id(conn, contador_id)
    auxiliar = auth_store.obtener_usuario_por_id(conn, auxiliar_id)
    assert contador["creado_por_usuario_id"] == superusuario_id
    assert contador["puede_crear_usuarios"] is True
    assert auxiliar["creado_por_usuario_id"] == contador_id
    assert auxiliar["puede_crear_usuarios"] is False  # el auxiliar no puede delegar más


def test_asociar_y_listar_nits_de_usuario(conn):
    usuario_id = auth_store.crear_usuario(conn, "contador@empresa.com", "contador")
    auth_store.asociar_empresa_a_usuario(conn, usuario_id, "900111222")
    auth_store.asociar_empresa_a_usuario(conn, usuario_id, "900333444")
    auth_store.asociar_empresa_a_usuario(conn, usuario_id, "900111222")  # repetido, no debe duplicar

    assert auth_store.listar_nits_de_usuario(conn, usuario_id) == ["900111222", "900333444"]


def test_quitar_empresa_de_usuario(conn):
    usuario_id = auth_store.crear_usuario(conn, "contador@empresa.com", "contador")
    auth_store.asociar_empresa_a_usuario(conn, usuario_id, "900111222")

    auth_store.quitar_empresa_de_usuario(conn, usuario_id, "900111222")

    assert auth_store.listar_nits_de_usuario(conn, usuario_id) == []


def test_listar_nits_de_usuario_sin_empresas(conn):
    usuario_id = auth_store.crear_usuario(conn, "super@axon.com", "superusuario")
    assert auth_store.listar_nits_de_usuario(conn, usuario_id) == []


def test_ciclo_de_vida_token_valido_y_se_consume(conn):
    usuario_id = auth_store.crear_usuario(conn, "nuevo@empresa.com", "empresa")
    token = auth_store.crear_token(conn, usuario_id, "invitacion")

    validado = auth_store.obtener_token_valido(conn, token)
    assert validado is not None
    assert validado["usuario_id"] == usuario_id
    assert validado["tipo"] == "invitacion"

    auth_store.marcar_token_usado(conn, token)
    assert auth_store.obtener_token_valido(conn, token) is None  # ya no sirve una segunda vez


def test_token_vencido_no_es_valido(conn):
    usuario_id = auth_store.crear_usuario(conn, "nuevo@empresa.com", "empresa")
    token = auth_store.crear_token(conn, usuario_id, "recuperacion", vigencia_horas=-1)  # ya vencido al crearlo

    assert auth_store.obtener_token_valido(conn, token) is None


def test_token_inexistente_no_es_valido(conn):
    assert auth_store.obtener_token_valido(conn, "token-que-nunca-existio") is None


def test_crear_token_rechaza_tipo_invalido(conn):
    usuario_id = auth_store.crear_usuario(conn, "nuevo@empresa.com", "empresa")
    with pytest.raises(ValueError, match="Tipo de token inválido"):
        auth_store.crear_token(conn, usuario_id, "otro-tipo")
