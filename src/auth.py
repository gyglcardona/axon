"""
Lógica de negocio de autenticación y permisos -- capa entre `auth_store`
(guarda/lee filas tal cual, no sabe de roles) y `backend/app.py` (que nunca
debe tocar `auth_store` directamente). Acá viven las reglas de rol:
"superusuario ve todo", "un contador solo puede repartir empresas que él
mismo ve", "la delegación se detiene en un nivel" -- confirmadas con el
usuario, ver el plan de la sesión.
"""

from __future__ import annotations

import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

import auth_store
import correo

URL_BASE = "http://localhost:5000"  # TODO: configurable cuando exista un dominio propio


class AuthError(Exception):
    """Violación de una regla de permisos o de negocio de autenticación --
    mensaje listo para mostrar al usuario."""


def verificar_credenciales(conn: sqlite3.Connection, email: str, password: str) -> dict | None:
    """`None` sin distinguir "no existe" de "contraseña incorrecta" -- no le
    da pistas a quien intente adivinar cuentas."""
    usuario = auth_store.obtener_usuario_por_email(conn, email)
    if usuario is None or not usuario["activo"] or usuario["password_hash"] is None:
        return None
    if not check_password_hash(usuario["password_hash"], password):
        return None
    return usuario


def usuario_puede_ver_empresa(conn: sqlite3.Connection, usuario: dict, nit: str) -> bool:
    if usuario["rol"] == "superusuario":
        return True
    return nit in auth_store.listar_nits_de_usuario(conn, usuario["id"])


def filtrar_empresas_visibles(conn: sqlite3.Connection, usuario: dict, empresas: list[dict]) -> list[dict]:
    """`empresas` es cualquier lista de dicts con clave `"nit"` -- se usa
    tanto para `orquestador.listar_empresas()` como, más adelante, para
    `google_conexiones.listar_conexiones()`."""
    if usuario["rol"] == "superusuario":
        return list(empresas)
    nits_visibles = set(auth_store.listar_nits_de_usuario(conn, usuario["id"]))
    return [e for e in empresas if e["nit"] in nits_visibles]


def enviar_invitacion(conn: sqlite3.Connection, usuario_id: int, email: str, asunto: str, intro_html: str) -> None:
    """Pública (sin `_`) porque `orquestador.registrar_empresa_nueva` también
    la necesita para el correo de autorregistro -- mismo mecanismo de token
    que la invitación manual de un contador/superusuario."""
    token = auth_store.crear_token(conn, usuario_id, "invitacion")
    link = f"{URL_BASE}/?token={token}"
    correo.enviar_correo(
        email, asunto,
        f"<p>{intro_html} Entra a este enlace para crear tu contraseña "
        f"(válido por {auth_store.VIGENCIA_TOKEN_HORAS} horas):</p><p><a href='{link}'>{link}</a></p>",
    )


def invitar_usuario(
    conn: sqlite3.Connection, creador: dict, email: str, rol: str, nits: list[str],
    puede_crear_usuarios: bool = False,
) -> dict:
    """Crea un usuario sin contraseña y le envía la invitación por correo.

    Todo contador "raíz" (invitado directamente por un superusuario) debe
    poder administrar su propio equipo -- invitar auxiliares y repartirles
    subconjuntos de sus propias empresas -- así que `puede_crear_usuarios`
    se activa solo con crear el rol "contador", sin que el superusuario
    tenga que acordarse de marcarlo (`puede_crear_usuarios` como parámetro
    queda para el día en que existan planes que sí lo restrinjan --
    pendiente, ver docs/08-decisiones-pendientes/preguntas-abiertas.md).

    Un contador nunca puede fundar otro contador "raíz" capaz de delegar --
    la delegación se detiene en un nivel (confirmado con el usuario), así
    que si `creador` no es superusuario, `puede_crear_usuarios` siempre
    queda en False sin importar el rol del invitado ni lo que se pida."""
    if not (creador["rol"] == "superusuario" or creador["puede_crear_usuarios"]):
        raise AuthError("No tienes permiso para crear usuarios nuevos.")

    if creador["rol"] != "superusuario":
        if rol == "superusuario":
            raise AuthError("Solo un superusuario puede crear otro superusuario.")
        nits_visibles = set(auth_store.listar_nits_de_usuario(conn, creador["id"]))
        no_autorizados = [n for n in nits if n not in nits_visibles]
        if no_autorizados:
            raise AuthError(
                f"No puedes dar acceso a empresas que tú mismo no ves: {', '.join(no_autorizados)}."
            )
        puede_crear_usuarios = False
    elif rol == "contador":
        puede_crear_usuarios = True

    usuario_id = auth_store.crear_usuario(
        conn, email=email, rol=rol, puede_crear_usuarios=puede_crear_usuarios,
        creado_por_usuario_id=creador["id"],
    )
    for nit in nits:
        auth_store.asociar_empresa_a_usuario(conn, usuario_id, nit)

    enviar_invitacion(conn, usuario_id, email, "Te invitaron a AXON", "Te dieron acceso a AXON.")
    return auth_store.obtener_usuario_por_id(conn, usuario_id)


def solicitar_recuperacion(conn: sqlite3.Connection, email: str) -> None:
    """Nunca revela si el correo existe o no -- si no hay usuario activo con
    ese correo, simplemente no hace nada (ni error, ni éxito distinguible)."""
    usuario = auth_store.obtener_usuario_por_email(conn, email)
    if usuario is None or not usuario["activo"]:
        return
    token = auth_store.crear_token(conn, usuario["id"], "recuperacion")
    link = f"{URL_BASE}/?token={token}"
    correo.enviar_correo(
        email, "Recupera tu contraseña de AXON",
        "<p>Entra a este enlace para elegir una contraseña nueva (válido por "
        f"{auth_store.VIGENCIA_TOKEN_HORAS} horas). Si no lo pediste, ignora este correo.</p>"
        f"<p><a href='{link}'>{link}</a></p>",
    )


def fijar_password_con_token(conn: sqlite3.Connection, token: str, password: str) -> dict:
    validado = auth_store.obtener_token_valido(conn, token)
    if validado is None:
        raise AuthError("El enlace no es válido o ya expiró -- pide uno nuevo.")
    if len(password) < 8:
        raise AuthError("La contraseña debe tener al menos 8 caracteres.")
    auth_store.fijar_password(conn, validado["usuario_id"], generate_password_hash(password))
    auth_store.marcar_token_usado(conn, token)
    return auth_store.obtener_usuario_por_id(conn, validado["usuario_id"])


def listar_usuarios_gestionables(conn: sqlite3.Connection, actor: dict) -> list[dict]:
    """Los usuarios que `actor` puede administrar -- superusuario ve todos;
    un contador con `puede_crear_usuarios` solo ve a los que él mismo invitó
    (su propio equipo, no el de otro contador); cualquier otro rol no
    administra usuarios."""
    if actor["rol"] == "superusuario":
        return auth_store.listar_usuarios(conn)
    if actor["puede_crear_usuarios"]:
        return auth_store.listar_usuarios_creados_por(conn, actor["id"])
    raise AuthError("No tienes permiso para ver la lista de usuarios.")


def _validar_puede_gestionar(actor: dict, usuario_objetivo: dict) -> None:
    if actor["rol"] == "superusuario":
        return
    if usuario_objetivo["creado_por_usuario_id"] != actor["id"]:
        raise AuthError("No puedes gestionar un usuario que no creaste tú.")


def reenviar_invitacion(conn: sqlite3.Connection, actor: dict, usuario_id: int) -> None:
    usuario = auth_store.obtener_usuario_por_id(conn, usuario_id)
    if usuario is None:
        raise AuthError("Ese usuario no existe.")
    _validar_puede_gestionar(actor, usuario)
    if usuario["password_hash"] is not None:
        raise AuthError(
            "Ese usuario ya fijó su contraseña -- si necesita recuperarla, usa 'Olvidé mi contraseña' en el login."
        )
    enviar_invitacion(conn, usuario_id, usuario["email"], "Te invitaron a AXON", "Te dieron acceso a AXON.")


def actualizar_empresas_de_usuario(conn: sqlite3.Connection, actor: dict, usuario_id: int, nits: list[str]) -> None:
    usuario = auth_store.obtener_usuario_por_id(conn, usuario_id)
    if usuario is None:
        raise AuthError("Ese usuario no existe.")
    _validar_puede_gestionar(actor, usuario)
    if actor["rol"] != "superusuario":
        nits_visibles = set(auth_store.listar_nits_de_usuario(conn, actor["id"]))
        no_autorizados = [n for n in nits if n not in nits_visibles]
        if no_autorizados:
            raise AuthError(
                f"No puedes dar acceso a empresas que tú mismo no ves: {', '.join(no_autorizados)}."
            )
    auth_store.reemplazar_empresas_de_usuario(conn, usuario_id, nits)


def cambiar_estado_usuario(conn: sqlite3.Connection, actor: dict, usuario_id: int, activo: bool) -> None:
    usuario = auth_store.obtener_usuario_por_id(conn, usuario_id)
    if usuario is None:
        raise AuthError("Ese usuario no existe.")
    _validar_puede_gestionar(actor, usuario)
    auth_store.cambiar_activo(conn, usuario_id, activo)


def cambiar_rol_usuario(conn: sqlite3.Connection, actor: dict, usuario_id: int, nuevo_rol: str) -> dict:
    """Solo superusuario -- cambiar el rol de alguien es una escalación de
    privilegios en potencia (una "empresa" podría convertirse en
    "superusuario" y ver todo), así que no se delega ni siquiera a un
    contador con `puede_crear_usuarios` (ese solo reparte SUS empresas,
    nunca decide quién más administra el sistema)."""
    if actor["rol"] != "superusuario":
        raise AuthError("Solo un superusuario puede cambiar el rol de un usuario.")
    if nuevo_rol not in auth_store.ROLES_VALIDOS:
        raise AuthError(f"Rol inválido: '{nuevo_rol}'. Válidos: {', '.join(auth_store.ROLES_VALIDOS)}.")
    usuario = auth_store.obtener_usuario_por_id(conn, usuario_id)
    if usuario is None:
        raise AuthError("Ese usuario no existe.")
    if usuario_id == actor["id"]:
        raise AuthError("No puedes cambiar tu propio rol -- pídeselo a otro superusuario.")
    if usuario["rol"] == "superusuario" and nuevo_rol != "superusuario":
        if auth_store.contar_superusuarios_activos(conn) <= 1:
            raise AuthError("No puedes quitarle el rol al único superusuario activo.")

    # "empresa" nunca debería poder delegar -- si el rol nuevo es ese, se
    # apaga puede_crear_usuarios aunque lo tuviera de antes. Todo contador
    # sí debe poder administrar su propio equipo (ver invitar_usuario), así
    # que pasar a "contador" lo enciende aunque no lo tuviera.
    if nuevo_rol == "empresa":
        puede_crear_usuarios = False
    elif nuevo_rol == "contador":
        puede_crear_usuarios = True
    else:
        puede_crear_usuarios = usuario["puede_crear_usuarios"]
    auth_store.cambiar_rol(conn, usuario_id, nuevo_rol, puede_crear_usuarios)
    return auth_store.obtener_usuario_por_id(conn, usuario_id)


def eliminar_usuario(conn: sqlite3.Connection, actor: dict, usuario_id: int) -> None:
    """Solo superusuario -- ver `cambiar_rol_usuario` para el mismo criterio.
    Nunca se puede eliminar la propia cuenta desde acá (evita quedarse
    fuera por accidente) ni al único superusuario activo que quede."""
    if actor["rol"] != "superusuario":
        raise AuthError("Solo un superusuario puede eliminar usuarios.")
    usuario = auth_store.obtener_usuario_por_id(conn, usuario_id)
    if usuario is None:
        raise AuthError("Ese usuario no existe.")
    if usuario_id == actor["id"]:
        raise AuthError("No puedes eliminar tu propia cuenta -- pídeselo a otro superusuario.")
    if usuario["rol"] == "superusuario" and auth_store.contar_superusuarios_activos(conn) <= 1:
        raise AuthError("No puedes eliminar al único superusuario activo.")
    try:
        auth_store.eliminar_usuario(conn, usuario_id)
    except sqlite3.IntegrityError:
        raise AuthError(
            "Este usuario invitó a otros usuarios que todavía existen -- elimínalos "
            "o quítales el vínculo primero."
        )
