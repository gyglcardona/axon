"""
Pruebas de src/auth.py: verificación de credenciales, visibilidad de
empresas por rol, invitación de usuarios (con la regla de "un solo nivel"
de delegación) y recuperación de contraseña. Nunca toca red real --
correo.enviar_correo se reemplaza por un grabador.
"""

import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import auth  # noqa: E402
import auth_store  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    conexion = auth_store.conectar(base_dir=tmp_path)
    yield conexion
    conexion.close()


@pytest.fixture(autouse=True)
def _correos_enviados(monkeypatch):
    enviados = []
    monkeypatch.setattr(auth.correo, "enviar_correo", lambda destinatario, asunto, cuerpo: enviados.append(
        {"destinatario": destinatario, "asunto": asunto, "cuerpo": cuerpo},
    ))
    return enviados


def _usuario(conn, email, rol, puede_crear_usuarios=False, password=None):
    password_hash = generate_password_hash(password) if password else None
    usuario_id = auth_store.crear_usuario(conn, email, rol, puede_crear_usuarios, password_hash=password_hash)
    return auth_store.obtener_usuario_por_id(conn, usuario_id)


# --- verificar_credenciales ---

def test_verificar_credenciales_correctas(conn):
    _usuario(conn, "user@empresa.com", "empresa", password="ClaveSegura123")
    usuario = auth.verificar_credenciales(conn, "user@empresa.com", "ClaveSegura123")
    assert usuario is not None
    assert usuario["email"] == "user@empresa.com"


def test_verificar_credenciales_password_incorrecta(conn):
    _usuario(conn, "user@empresa.com", "empresa", password="ClaveSegura123")
    assert auth.verificar_credenciales(conn, "user@empresa.com", "otra-clave") is None


def test_verificar_credenciales_usuario_inexistente(conn):
    assert auth.verificar_credenciales(conn, "nadie@empresa.com", "algo") is None


def test_verificar_credenciales_usuario_sin_password_fijada(conn):
    _usuario(conn, "invitado@empresa.com", "empresa")  # sin password (invitación pendiente)
    assert auth.verificar_credenciales(conn, "invitado@empresa.com", "algo") is None


# --- usuario_puede_ver_empresa / filtrar_empresas_visibles ---

def test_superusuario_ve_cualquier_empresa(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    assert auth.usuario_puede_ver_empresa(conn, super_, "900111222") is True


def test_contador_solo_ve_sus_empresas_asociadas(conn):
    contador = _usuario(conn, "contador@empresa.com", "contador")
    auth_store.asociar_empresa_a_usuario(conn, contador["id"], "900111222")

    assert auth.usuario_puede_ver_empresa(conn, contador, "900111222") is True
    assert auth.usuario_puede_ver_empresa(conn, contador, "900999888") is False


def test_filtrar_empresas_visibles_superusuario_ve_todas(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    empresas = [{"nit": "1"}, {"nit": "2"}, {"nit": "3"}]
    assert auth.filtrar_empresas_visibles(conn, super_, empresas) == empresas


def test_filtrar_empresas_visibles_contador_solo_las_suyas(conn):
    contador = _usuario(conn, "contador@empresa.com", "contador")
    auth_store.asociar_empresa_a_usuario(conn, contador["id"], "2")
    empresas = [{"nit": "1"}, {"nit": "2"}, {"nit": "3"}]

    assert auth.filtrar_empresas_visibles(conn, contador, empresas) == [{"nit": "2"}]


# --- invitar_usuario ---

def test_invitar_usuario_sin_permiso_da_error(conn):
    usuario_sin_permiso = _usuario(conn, "empresa@x.com", "empresa")
    with pytest.raises(auth.AuthError, match="permiso"):
        auth.invitar_usuario(conn, usuario_sin_permiso, "nuevo@x.com", "empresa", [])


def test_superusuario_invita_usuario_y_envia_correo(conn, _correos_enviados):
    super_ = _usuario(conn, "super@axon.com", "superusuario")

    nuevo = auth.invitar_usuario(conn, super_, "nuevo@empresa.com", "empresa", ["900111222"])

    assert nuevo["email"] == "nuevo@empresa.com"
    assert nuevo["password_hash"] is None  # todavía no la fija
    assert auth_store.listar_nits_de_usuario(conn, nuevo["id"]) == ["900111222"]
    assert len(_correos_enviados) == 1
    assert _correos_enviados[0]["destinatario"] == "nuevo@empresa.com"
    assert "token=" in _correos_enviados[0]["cuerpo"]


def test_superusuario_puede_otorgar_puede_crear_usuarios(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador_raiz = auth.invitar_usuario(
        conn, super_, "contador@firma.com", "contador", [], puede_crear_usuarios=True,
    )
    assert contador_raiz["puede_crear_usuarios"] is True


def test_todo_contador_invitado_por_superusuario_puede_gestionar_su_equipo_por_defecto(conn):
    """Un contador "raíz" siempre debe poder invitar a sus propios
    auxiliares y repartirles sus empresas -- no depende de que el
    superusuario se acuerde de marcar una casilla aparte. Las restricciones
    de cuántas empresas/auxiliares según el plan contratado quedan
    pendientes de diseñar, no son parte de este comportamiento."""
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador_raiz = auth.invitar_usuario(conn, super_, "contador@firma.com", "contador", [])
    assert contador_raiz["puede_crear_usuarios"] is True


def test_invitar_rol_empresa_no_otorga_puede_crear_usuarios_por_defecto(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    usuario_empresa = auth.invitar_usuario(conn, super_, "empresa@firma.com", "empresa", [])
    assert usuario_empresa["puede_crear_usuarios"] is False


def test_contador_con_permiso_invita_auxiliar_dentro_de_sus_empresas(conn):
    contador = _usuario(conn, "contador@firma.com", "contador", puede_crear_usuarios=True)
    auth_store.asociar_empresa_a_usuario(conn, contador["id"], "900111222")
    auth_store.asociar_empresa_a_usuario(conn, contador["id"], "900333444")

    auxiliar = auth.invitar_usuario(conn, contador, "aux@firma.com", "contador", ["900111222"])

    assert auth_store.listar_nits_de_usuario(conn, auxiliar["id"]) == ["900111222"]


def test_contador_no_puede_asignar_empresa_que_no_ve(conn):
    contador = _usuario(conn, "contador@firma.com", "contador", puede_crear_usuarios=True)
    auth_store.asociar_empresa_a_usuario(conn, contador["id"], "900111222")

    with pytest.raises(auth.AuthError, match="no ves"):
        auth.invitar_usuario(conn, contador, "aux@firma.com", "contador", ["900111222", "900999888"])


def test_auxiliar_creado_por_contador_nunca_puede_delegar_aunque_se_pida(conn):
    """La delegación se detiene en un nivel -- aunque el contador intente
    pasar puede_crear_usuarios=True para su auxiliar, se ignora."""
    contador = _usuario(conn, "contador@firma.com", "contador", puede_crear_usuarios=True)
    auth_store.asociar_empresa_a_usuario(conn, contador["id"], "900111222")

    auxiliar = auth.invitar_usuario(
        conn, contador, "aux@firma.com", "contador", ["900111222"], puede_crear_usuarios=True,
    )

    assert auxiliar["puede_crear_usuarios"] is False
    # y en efecto, ese auxiliar no puede invitar a nadie más
    with pytest.raises(auth.AuthError, match="permiso"):
        auth.invitar_usuario(conn, auxiliar, "otro@firma.com", "empresa", [])


# --- recuperación de contraseña ---

def test_solicitar_recuperacion_usuario_existente_envia_correo(conn, _correos_enviados):
    _usuario(conn, "user@empresa.com", "empresa", password="ClaveVieja123")

    auth.solicitar_recuperacion(conn, "user@empresa.com")

    assert len(_correos_enviados) == 1
    assert _correos_enviados[0]["destinatario"] == "user@empresa.com"


def test_solicitar_recuperacion_usuario_inexistente_no_hace_nada_ni_falla(conn, _correos_enviados):
    auth.solicitar_recuperacion(conn, "nadie@empresa.com")
    assert _correos_enviados == []


def test_fijar_password_con_token_valido(conn):
    usuario = _usuario(conn, "user@empresa.com", "empresa")
    token = auth_store.crear_token(conn, usuario["id"], "recuperacion")

    actualizado = auth.fijar_password_con_token(conn, token, "NuevaClave123")

    assert auth.verificar_credenciales(conn, "user@empresa.com", "NuevaClave123") is not None
    assert actualizado["id"] == usuario["id"]


def test_fijar_password_con_token_invalido_da_error(conn):
    with pytest.raises(auth.AuthError, match="no es válido"):
        auth.fijar_password_con_token(conn, "token-inventado", "NuevaClave123")


def test_fijar_password_muy_corta_da_error(conn):
    usuario = _usuario(conn, "user@empresa.com", "empresa")
    token = auth_store.crear_token(conn, usuario["id"], "invitacion")

    with pytest.raises(auth.AuthError, match="8 caracteres"):
        auth.fijar_password_con_token(conn, token, "corta")


def test_fijar_password_no_reutiliza_el_token(conn):
    usuario = _usuario(conn, "user@empresa.com", "empresa")
    token = auth_store.crear_token(conn, usuario["id"], "invitacion")
    auth.fijar_password_con_token(conn, token, "PrimeraClave123")

    with pytest.raises(auth.AuthError, match="no es válido"):
        auth.fijar_password_con_token(conn, token, "SegundaClave123")


# --- gestión de usuarios (Sesión 7) ---

def test_listar_usuarios_gestionables_superusuario_ve_todos(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    auth.invitar_usuario(conn, super_, "a@x.com", "empresa", [])
    auth.invitar_usuario(conn, super_, "b@x.com", "contador", [])

    usuarios = auth.listar_usuarios_gestionables(conn, super_)

    emails = {u["email"] for u in usuarios}
    assert emails == {"super@axon.com", "a@x.com", "b@x.com"}


def test_listar_usuarios_gestionables_contador_solo_ve_su_equipo(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador_a = auth.invitar_usuario(conn, super_, "contadorA@firma.com", "contador", [], puede_crear_usuarios=True)
    contador_b = auth.invitar_usuario(conn, super_, "contadorB@firma.com", "contador", [], puede_crear_usuarios=True)
    auth.invitar_usuario(conn, contador_a, "auxA@firma.com", "contador", [])
    auth.invitar_usuario(conn, contador_b, "auxB@firma.com", "contador", [])

    usuarios = auth.listar_usuarios_gestionables(conn, contador_a)

    assert {u["email"] for u in usuarios} == {"auxa@firma.com"}


def test_listar_usuarios_gestionables_sin_permiso_da_error(conn):
    empresa = _usuario(conn, "empresa@x.com", "empresa")
    with pytest.raises(auth.AuthError, match="permiso"):
        auth.listar_usuarios_gestionables(conn, empresa)


def test_reenviar_invitacion_a_usuario_sin_password(conn, _correos_enviados):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    nuevo = auth.invitar_usuario(conn, super_, "nuevo@x.com", "empresa", [])
    _correos_enviados.clear()

    auth.reenviar_invitacion(conn, super_, nuevo["id"])

    assert len(_correos_enviados) == 1
    assert _correos_enviados[0]["destinatario"] == "nuevo@x.com"


def test_reenviar_invitacion_a_usuario_con_password_da_error(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    con_password = _usuario(conn, "con-clave@x.com", "empresa", password="ClaveSegura123")

    with pytest.raises(auth.AuthError, match="ya fijó su contraseña"):
        auth.reenviar_invitacion(conn, super_, con_password["id"])


def test_no_puedo_reenviar_invitacion_de_usuario_que_no_cree(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador_a = auth.invitar_usuario(conn, super_, "contadorA@firma.com", "contador", [], puede_crear_usuarios=True)
    contador_b = auth.invitar_usuario(conn, super_, "contadorB@firma.com", "contador", [], puede_crear_usuarios=True)
    aux_de_a = auth.invitar_usuario(conn, contador_a, "auxA@firma.com", "contador", [])

    with pytest.raises(auth.AuthError, match="no creaste tú"):
        auth.reenviar_invitacion(conn, contador_b, aux_de_a["id"])


def test_actualizar_empresas_de_usuario(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    nuevo = auth.invitar_usuario(conn, super_, "nuevo@x.com", "empresa", ["900111222"])

    auth.actualizar_empresas_de_usuario(conn, super_, nuevo["id"], ["900333444", "900555666"])

    assert auth_store.listar_nits_de_usuario(conn, nuevo["id"]) == ["900333444", "900555666"]


def test_actualizar_empresas_contador_no_puede_dar_lo_que_no_ve(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador = auth.invitar_usuario(conn, super_, "contador@firma.com", "contador", ["900111222"], puede_crear_usuarios=True)
    aux = auth.invitar_usuario(conn, contador, "aux@firma.com", "contador", ["900111222"])

    with pytest.raises(auth.AuthError, match="no ves"):
        auth.actualizar_empresas_de_usuario(conn, contador, aux["id"], ["900111222", "900999888"])


def test_cambiar_estado_usuario_desactiva_y_bloquea_login(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    nuevo = _usuario(conn, "nuevo@x.com", "empresa", password="ClaveSegura123")

    auth.cambiar_estado_usuario(conn, super_, nuevo["id"], False)

    assert auth.verificar_credenciales(conn, "nuevo@x.com", "ClaveSegura123") is None


def test_no_puedo_gestionar_usuario_que_no_cree_ni_soy_superusuario(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador_a = auth.invitar_usuario(conn, super_, "contadorA@firma.com", "contador", [], puede_crear_usuarios=True)
    contador_b = auth.invitar_usuario(conn, super_, "contadorB@firma.com", "contador", [], puede_crear_usuarios=True)

    with pytest.raises(auth.AuthError, match="no creaste tú"):
        auth.cambiar_estado_usuario(conn, contador_b, contador_a["id"], False)


# --- invitar_usuario: un contador jamás puede fundar un superusuario ---

def test_contador_con_puede_crear_usuarios_no_puede_invitar_superusuario(conn):
    """Gap real encontrado y cerrado: antes `invitar_usuario` solo validaba
    los NITs para un creador no-superusuario, nunca el rol -- un contador
    con `puede_crear_usuarios=True` podía crear un superusuario nuevo."""
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador = auth.invitar_usuario(conn, super_, "contador@firma.com", "contador", [], puede_crear_usuarios=True)

    with pytest.raises(auth.AuthError, match="Solo un superusuario"):
        auth.invitar_usuario(conn, contador, "intruso@firma.com", "superusuario", [])

    assert auth_store.obtener_usuario_por_email(conn, "intruso@firma.com") is None


def test_superusuario_si_puede_invitar_otro_superusuario(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    nuevo = auth.invitar_usuario(conn, super_, "nuevo-super@axon.com", "superusuario", [])
    assert nuevo["rol"] == "superusuario"


# --- cambiar_rol_usuario: solo superusuario, con frenos de seguridad ---

def test_cambiar_rol_usuario_ok(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    usuario = _usuario(conn, "user@empresa.com", "empresa")

    actualizado = auth.cambiar_rol_usuario(conn, super_, usuario["id"], "contador")

    assert actualizado["rol"] == "contador"


def test_cambiar_rol_usuario_a_contador_enciende_puede_crear_usuarios(conn):
    """Igual que al invitar un contador nuevo -- pasar a "contador" siempre
    debe habilitar la gestión de su propio equipo, sin un paso aparte."""
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    usuario = _usuario(conn, "user@empresa.com", "empresa")

    actualizado = auth.cambiar_rol_usuario(conn, super_, usuario["id"], "contador")

    assert actualizado["puede_crear_usuarios"] is True


def test_cambiar_rol_usuario_lo_bloquea_un_contador_aunque_pueda_crear_usuarios(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador = auth.invitar_usuario(conn, super_, "contador@firma.com", "contador", [], puede_crear_usuarios=True)
    aux = auth.invitar_usuario(conn, contador, "aux@firma.com", "empresa", [])

    with pytest.raises(auth.AuthError, match="Solo un superusuario"):
        auth.cambiar_rol_usuario(conn, contador, aux["id"], "superusuario")


def test_cambiar_rol_usuario_no_puede_cambiarse_a_si_mismo(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    with pytest.raises(auth.AuthError, match="tu propio rol"):
        auth.cambiar_rol_usuario(conn, super_, super_["id"], "empresa")


def test_cambiar_rol_usuario_no_puede_quitarle_el_rol_al_unico_superusuario(conn):
    unico = _usuario(conn, "unico@axon.com", "superusuario")
    # actor sintético con rol superusuario, distinto del único que existe en
    # la base -- la función solo lee al actor del dict, no lo revalida contra
    # la BD (eso ya lo hace `requiere_login` antes de llegar acá)
    otro_actor = {"id": unico["id"] + 1000, "rol": "superusuario", "puede_crear_usuarios": True}

    with pytest.raises(auth.AuthError, match="único superusuario"):
        auth.cambiar_rol_usuario(conn, otro_actor, unico["id"], "contador")

    # con un segundo superusuario activo de verdad, sí se puede
    _usuario(conn, "otro@axon.com", "superusuario")
    actualizado = auth.cambiar_rol_usuario(conn, otro_actor, unico["id"], "contador")
    assert actualizado["rol"] == "contador"


def test_cambiar_rol_usuario_a_empresa_apaga_puede_crear_usuarios(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador = auth.invitar_usuario(conn, super_, "contador@firma.com", "contador", [], puede_crear_usuarios=True)

    actualizado = auth.cambiar_rol_usuario(conn, super_, contador["id"], "empresa")

    assert actualizado["rol"] == "empresa"
    assert actualizado["puede_crear_usuarios"] is False


# --- eliminar_usuario: solo superusuario, con los mismos frenos ---

def test_eliminar_usuario_ok(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    usuario = _usuario(conn, "user@empresa.com", "empresa")

    auth.eliminar_usuario(conn, super_, usuario["id"])

    assert auth_store.obtener_usuario_por_id(conn, usuario["id"]) is None


def test_eliminar_usuario_lo_bloquea_un_contador(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador = auth.invitar_usuario(conn, super_, "contador@firma.com", "contador", [], puede_crear_usuarios=True)
    aux = auth.invitar_usuario(conn, contador, "aux@firma.com", "empresa", [])

    with pytest.raises(auth.AuthError, match="Solo un superusuario"):
        auth.eliminar_usuario(conn, contador, aux["id"])


def test_eliminar_usuario_no_puede_eliminarse_a_si_mismo(conn):
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    with pytest.raises(auth.AuthError, match="propia cuenta"):
        auth.eliminar_usuario(conn, super_, super_["id"])


def test_eliminar_usuario_no_puede_eliminar_al_unico_superusuario(conn):
    unico = _usuario(conn, "unico@axon.com", "superusuario")
    otro_actor = {"id": unico["id"] + 1000, "rol": "superusuario", "puede_crear_usuarios": True}

    with pytest.raises(auth.AuthError, match="único superusuario"):
        auth.eliminar_usuario(conn, otro_actor, unico["id"])


def test_eliminar_usuario_que_invito_a_otros_da_error_claro(conn):
    """Antes de este mensaje, esto reventaba con un sqlite3.IntegrityError
    crudo -- la llave foránea de `creado_por_usuario_id` bloquea el borrado
    porque el auxiliar todavía existe y lo referencia."""
    super_ = _usuario(conn, "super@axon.com", "superusuario")
    contador = auth.invitar_usuario(conn, super_, "contador@firma.com", "contador", [], puede_crear_usuarios=True)
    auth.invitar_usuario(conn, contador, "aux@firma.com", "empresa", [])

    with pytest.raises(auth.AuthError, match="invitó a otros usuarios"):
        auth.eliminar_usuario(conn, super_, contador["id"])
