"""
API + servidor de la interfaz web. Capa delgada de HTTP sobre
src/orquestador.py -- toda la lógica real vive ahí, compartida con main.py
(CLI). Ver docs/00-contexto/decisiones-arquitectura.md, "Backend separado
del frontend": correr esto local hoy con `python backend/app.py` es el mismo
código que correría en un servidor cuando esto sea SaaS, solo cambia cómo se
despliega, no la lógica de negocio.

Arrancar: python backend/app.py  ->  http://localhost:5000
"""

from __future__ import annotations

import base64
import secrets
import sys
import tempfile
from functools import wraps
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory, session  # noqa: E402

import auth  # noqa: E402
import auth_store  # noqa: E402
import correo  # noqa: E402
import drive_client  # noqa: E402
import gmail_client  # noqa: E402
import google_conexiones  # noqa: E402
import orquestador  # noqa: E402
import siigo_client  # noqa: E402

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
SECRET_KEY_PATH = Path("config/sistema/secret_key.txt")


def _obtener_o_crear_secret_key() -> str:
    """Clave para firmar la cookie de sesión de Flask -- se genera una sola
    vez y se guarda fuera de git (mismo tratamiento que config/google/ y
    config/correo/); si se pierde o cambia, todas las sesiones activas
    quedan invalidadas (nadie queda "medio logueado", es seguro)."""
    if SECRET_KEY_PATH.is_file():
        return SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
    SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    clave = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(clave, encoding="utf-8")
    return clave


app = Flask(__name__)
app.secret_key = _obtener_o_crear_secret_key()


def _usuario_publico(usuario: dict) -> dict:
    """Nunca se manda `password_hash` al frontend."""
    return {
        "id": usuario["id"], "email": usuario["email"], "rol": usuario["rol"],
        "puede_crear_usuarios": usuario["puede_crear_usuarios"], "activo": usuario["activo"],
    }


def _usuario_con_empresas(conn, usuario: dict) -> dict:
    return {
        **_usuario_publico(usuario),
        "nits": auth_store.listar_nits_de_usuario(conn, usuario["id"]),
        "tiene_password": usuario["password_hash"] is not None,
    }


def requiere_login(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        usuario_id = session.get("usuario_id")
        if usuario_id is None:
            return jsonify({"error": "No has iniciado sesión."}), 401
        conn = auth_store.conectar()
        try:
            usuario = auth_store.obtener_usuario_por_id(conn, usuario_id)
        finally:
            conn.close()
        if usuario is None or not usuario["activo"]:
            session.clear()
            return jsonify({"error": "No has iniciado sesión."}), 401
        g.usuario = usuario
        return vista(*args, **kwargs)
    return envoltura


def requiere_acceso_empresa(vista):
    """Va DESPUÉS de `@requiere_login` en la pila de decoradores (más cerca
    de `@app.get(...)`) -- depende de que `g.usuario` ya esté puesto."""
    @wraps(vista)
    def envoltura(*args, **kwargs):
        slug = kwargs.get("slug")
        try:
            nit = orquestador.resolver_empresa(slug)["nit"]
        except orquestador.EmpresaNoEncontrada as e:
            return jsonify({"error": str(e)}), 404
        conn = auth_store.conectar()
        try:
            permitido = auth.usuario_puede_ver_empresa(conn, g.usuario, nit)
        finally:
            conn.close()
        if not permitido:
            return jsonify({"error": "No tienes acceso a esta empresa."}), 403
        return vista(*args, **kwargs)
    return envoltura


def requiere_superusuario(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if g.usuario["rol"] != "superusuario":
            return jsonify({"error": "Esta acción requiere ser superusuario."}), 403
        return vista(*args, **kwargs)
    return envoltura


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "bandeja-revision.html")


@app.post("/api/auth/login")
def api_login():
    data = request.get_json(silent=True) or {}
    conn = auth_store.conectar()
    try:
        usuario = auth.verificar_credenciales(conn, data.get("email", ""), data.get("password", ""))
    finally:
        conn.close()
    if usuario is None:
        return jsonify({"error": "Correo o contraseña incorrectos."}), 401
    session["usuario_id"] = usuario["id"]
    return jsonify(_usuario_publico(usuario))


@app.post("/api/auth/logout")
def api_logout():
    session.clear()
    return jsonify({"cerrada": True})


@app.get("/api/auth/yo")
def api_yo():
    usuario_id = session.get("usuario_id")
    if usuario_id is None:
        return jsonify({"error": "No has iniciado sesión."}), 401
    conn = auth_store.conectar()
    try:
        usuario = auth_store.obtener_usuario_por_id(conn, usuario_id)
    finally:
        conn.close()
    if usuario is None or not usuario["activo"]:
        session.clear()
        return jsonify({"error": "No has iniciado sesión."}), 401
    return jsonify(_usuario_publico(usuario))


@app.post("/api/auth/invitar")
@requiere_login
def api_invitar():
    data = request.get_json(silent=True) or {}
    conn = auth_store.conectar()
    try:
        nuevo = auth.invitar_usuario(
            conn, g.usuario, data.get("email", ""), data.get("rol", ""), data.get("nits") or [],
            bool(data.get("puede_crear_usuarios", False)),
        )
    except (auth.AuthError, ValueError, correo.CorreoError) as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
    return jsonify(_usuario_publico(nuevo))


@app.post("/api/auth/crear-empresa")
@requiere_login
def api_crear_empresa():
    data = request.get_json(silent=True) or {}
    try:
        resultado = orquestador.crear_empresa_administrada(
            data.get("nit", ""), data.get("razon_social", ""), data.get("email", ""), g.usuario,
        )
    except (auth.AuthError, ValueError, correo.CorreoError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(resultado)


@app.get("/api/auth/usuarios")
@requiere_login
def api_listar_usuarios():
    conn = auth_store.conectar()
    try:
        usuarios = auth.listar_usuarios_gestionables(conn, g.usuario)
        return jsonify([_usuario_con_empresas(conn, u) for u in usuarios])
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 403
    finally:
        conn.close()


@app.post("/api/auth/usuarios/<int:usuario_id>/reenviar-invitacion")
@requiere_login
def api_reenviar_invitacion(usuario_id):
    conn = auth_store.conectar()
    try:
        auth.reenviar_invitacion(conn, g.usuario, usuario_id)
    except (auth.AuthError, correo.CorreoError) as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
    return jsonify({"enviado": True})


@app.post("/api/auth/usuarios/<int:usuario_id>/empresas")
@requiere_login
def api_actualizar_empresas_usuario(usuario_id):
    nits = (request.get_json(silent=True) or {}).get("nits") or []
    conn = auth_store.conectar()
    try:
        auth.actualizar_empresas_de_usuario(conn, g.usuario, usuario_id, nits)
        usuario = auth_store.obtener_usuario_por_id(conn, usuario_id)
        return jsonify(_usuario_con_empresas(conn, usuario))
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()


@app.post("/api/auth/usuarios/<int:usuario_id>/estado")
@requiere_login
def api_cambiar_estado_usuario(usuario_id):
    activo = bool((request.get_json(silent=True) or {}).get("activo", True))
    conn = auth_store.conectar()
    try:
        auth.cambiar_estado_usuario(conn, g.usuario, usuario_id, activo)
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
    return jsonify({"guardado": True})


@app.post("/api/auth/usuarios/<int:usuario_id>/rol")
@requiere_login
@requiere_superusuario
def api_cambiar_rol_usuario(usuario_id):
    nuevo_rol = (request.get_json(silent=True) or {}).get("rol", "")
    conn = auth_store.conectar()
    try:
        actualizado = auth.cambiar_rol_usuario(conn, g.usuario, usuario_id, nuevo_rol)
        return jsonify(_usuario_con_empresas(conn, actualizado))
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()


@app.delete("/api/auth/usuarios/<int:usuario_id>")
@requiere_login
@requiere_superusuario
def api_eliminar_usuario(usuario_id):
    conn = auth_store.conectar()
    try:
        auth.eliminar_usuario(conn, g.usuario, usuario_id)
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
    return jsonify({"eliminado": True})


@app.get("/api/auth/token/<token>")
def api_token(token):
    conn = auth_store.conectar()
    try:
        validado = auth_store.obtener_token_valido(conn, token)
        if validado is None:
            return jsonify({"valido": False})
        usuario = auth_store.obtener_usuario_por_id(conn, validado["usuario_id"])
    finally:
        conn.close()
    return jsonify({"valido": True, "tipo": validado["tipo"], "email": usuario["email"] if usuario else None})


@app.post("/api/auth/fijar-password")
def api_fijar_password():
    data = request.get_json(silent=True) or {}
    conn = auth_store.conectar()
    try:
        auth.fijar_password_con_token(conn, data.get("token", ""), data.get("password", ""))
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
    # Si el navegador ya tenía una sesión activa de OTRA cuenta (ej. alguien
    # invitado que ya usa AXON para otra empresa, o quien está probando con
    # su propia cuenta de superusuario en la misma pestaña), sin esto el
    # usuario sigue viendo la app como esa cuenta vieja después de fijar su
    # contraseña nueva -- bug real confirmado 2026-08 (una empresa recién
    # autorregistrada veía la conexión de Google de otra empresa, porque en
    # realidad seguía logueado como el superusuario que la había creado).
    session.clear()
    return jsonify({"guardado": True})


@app.post("/api/auth/olvide-password")
def api_olvide_password():
    email = (request.get_json(silent=True) or {}).get("email", "")
    conn = auth_store.conectar()
    try:
        try:
            auth.solicitar_recuperacion(conn, email)
        except correo.CorreoError:
            pass  # el correo no configurado es un problema del sistema, no algo que este endpoint deba revelar
    finally:
        conn.close()
    return jsonify({"enviado": True})  # siempre "éxito" -- nunca revela si el correo existe


@app.post("/api/auth/registrar-empresa")
def api_registrar_empresa():
    """Público (sin @requiere_login) -- es la puerta de entrada para una
    empresa que nunca ha usado AXON. Ver orquestador.registrar_empresa_nueva:
    el rol siempre es "empresa" y nunca viene del cuerpo del request."""
    data = request.get_json(silent=True) or {}
    try:
        resultado = orquestador.registrar_empresa_nueva(
            data.get("nit", ""), data.get("razon_social", ""), data.get("email", ""),
        )
    except (ValueError, correo.CorreoError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(resultado)


@app.get("/api/empresas")
@requiere_login
def api_empresas():
    conn = auth_store.conectar()
    try:
        empresas = auth.filtrar_empresas_visibles(conn, g.usuario, orquestador.listar_empresas())
    finally:
        conn.close()
    return jsonify(empresas)


@app.delete("/api/empresas/<slug>")
@requiere_login
@requiere_superusuario
def api_eliminar_empresa(slug):
    """Solo superusuario -- borra la empresa por completo (ver
    orquestador.eliminar_empresa). No usa @requiere_acceso_empresa porque un
    superusuario ya tiene acceso a cualquier empresa de por sí."""
    try:
        return jsonify(orquestador.eliminar_empresa(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/empresas/<slug>/facturas")
@requiere_login
@requiere_acceso_empresa
def api_facturas(slug):
    try:
        return jsonify(orquestador.listar_facturas(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/empresas/<slug>/carpetas")
@requiere_login
@requiere_acceso_empresa
def api_carpetas(slug):
    try:
        return jsonify(orquestador.listar_carpetas_disponibles(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/empresas/<slug>/listados")
@requiere_login
@requiere_acceso_empresa
def api_listados(slug):
    carpeta = request.args.get("carpeta") or ""
    if not carpeta:
        return jsonify({"error": "Falta 'carpeta' (ej. '2026/07') en la petición."}), 400
    try:
        return jsonify(orquestador.listar_archivos_listado(slug, carpeta))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/empresas/<slug>/facturas/<cufe>/pdf")
@requiere_login
@requiere_acceso_empresa
def api_pdf(slug, cufe):
    try:
        pdf_bytes = orquestador.obtener_pdf(slug, cufe)
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    if pdf_bytes is None:
        return jsonify({"error": "No se encontró un PDF adjunto para esta factura."}), 404
    return Response(pdf_bytes, mimetype="application/pdf")


@app.get("/api/empresas/<slug>/conexion-siigo")
@requiere_login
@requiere_acceso_empresa
def api_conexion_siigo(slug):
    try:
        return jsonify(orquestador.obtener_conexion_siigo(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/conexion-siigo")
@requiere_login
@requiere_acceso_empresa
def api_guardar_conexion_siigo(slug):
    data = request.get_json(silent=True) or {}
    usuario = (data.get("usuario") or "").strip()
    access_key = (data.get("access_key") or "").strip()
    partner_id = (data.get("partner_id") or "").strip()
    if not usuario or not access_key or not partner_id:
        return jsonify({"error": "Correo, Access Key y Partner ID son obligatorios."}), 400
    try:
        return jsonify(orquestador.guardar_conexion_siigo(slug, usuario, access_key, partner_id))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/empresas/<slug>/conexion-drive")
@requiere_login
@requiere_acceso_empresa
def api_conexion_drive(slug):
    try:
        return jsonify(orquestador.obtener_conexion_drive(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/conexion-drive")
@requiere_login
@requiere_acceso_empresa
def api_guardar_conexion_drive(slug):
    data = request.get_json(silent=True) or {}
    carpeta_id = (data.get("carpeta_id") or "").strip()
    if not carpeta_id:
        return jsonify({"error": "El id de la carpeta de Drive es obligatorio."}), 400
    try:
        return jsonify(orquestador.guardar_conexion_drive(slug, carpeta_id))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/sistema/conexion-google-app")
@requiere_login
@requiere_superusuario
def api_estado_client_secret_web():
    return jsonify(google_conexiones.estado_client_secret_web())


@app.post("/api/sistema/conexion-google-app")
@requiere_login
@requiere_superusuario
def api_guardar_client_secret_web():
    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"error": "Falta el archivo JSON ('archivo' en el form-data)."}), 400
    try:
        return jsonify(google_conexiones.guardar_client_secret_web(archivo.read()))
    except google_conexiones.GoogleConexionError as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/sistema/config-correo")
@requiere_login
@requiere_superusuario
def api_config_correo():
    return jsonify(correo.obtener_config_smtp())


@app.post("/api/sistema/config-correo")
@requiere_login
@requiere_superusuario
def api_guardar_config_correo():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(correo.guardar_config_smtp(data.get("email", ""), data.get("password_app", "")))
    except correo.CorreoError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/sistema/config-correo/prueba")
@requiere_login
@requiere_superusuario
def api_probar_config_correo():
    destinatario = (request.get_json(silent=True) or {}).get("destinatario", "").strip()
    if not destinatario:
        return jsonify({"error": "Falta 'destinatario' en el cuerpo de la petición."}), 400
    try:
        correo.enviar_correo(
            destinatario, "Correo de prueba -- AXON",
            "<p>Si ves este mensaje, el envío de correo desde AXON está funcionando correctamente.</p>",
        )
        return jsonify({"enviado": True})
    except correo.CorreoError as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/conexiones-google")
@requiere_login
def api_conexiones_google():
    return jsonify(orquestador.listar_conexiones_google_visibles(g.usuario))


@app.get("/api/empresas/<slug>/conexion-google/autorizar")
@requiere_login
@requiere_acceso_empresa
def api_autorizar_conexion_google(slug):
    try:
        url = orquestador.iniciar_autorizacion_google(slug, g.usuario["id"])
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except google_conexiones.GoogleConexionError as e:
        return jsonify({"error": str(e)}), 400
    return redirect(url)


@app.get("/oauth/google/callback")
def api_oauth_google_callback():
    state = request.args.get("state", "")
    code = request.args.get("code", "")
    error = request.args.get("error")
    if error:
        return f"<p>Google rechazó la conexión: {error}. Cierra esta pestaña e intenta de nuevo.</p>", 400
    try:
        resultado = orquestador.completar_autorizacion_google(state, code)
    except google_conexiones.GoogleConexionError as e:
        return f"<p>{e}</p>", 400
    cuenta_email = resultado.get("cuenta_email")
    sufijo = f" ({cuenta_email})" if cuenta_email else ""
    return f"<p>Cuenta conectada correctamente{sufijo}. Puedes cerrar esta pestaña y volver a AXON.</p>"


@app.post("/api/empresas/<slug>/conexion-google")
@requiere_login
@requiere_acceso_empresa
def api_asociar_conexion_google(slug):
    data = request.get_json(silent=True) or {}
    conexion_id = data.get("conexion_id", "")
    try:
        return jsonify(orquestador.asociar_conexion_google_para_usuario(slug, conexion_id, g.usuario))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 403


@app.get("/api/empresas/<slug>/conexion-gmail")
@requiere_login
@requiere_acceso_empresa
def api_conexion_gmail(slug):
    try:
        return jsonify(orquestador.obtener_conexion_gmail(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/conexion-gmail")
@requiere_login
@requiere_acceso_empresa
def api_guardar_conexion_gmail(slug):
    data = request.get_json(silent=True) or {}
    campos = {k: data[k] for k in ("activo", "buscar_en_spam", "desde_fecha") if k in data}
    try:
        return jsonify(orquestador.guardar_conexion_gmail(slug, campos))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/empresas/<slug>/destino-causacion")
@requiere_login
@requiere_acceso_empresa
def api_destino_causacion(slug):
    try:
        return jsonify(orquestador.obtener_destino_causacion(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/destino-causacion")
@requiere_login
@requiere_acceso_empresa
def api_guardar_destino_causacion(slug):
    data = request.get_json(silent=True) or {}
    destino = (data.get("destino") or "").strip()
    try:
        return jsonify(orquestador.guardar_destino_causacion(slug, destino))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/empresas/<slug>/compras-siigo")
@requiere_login
@requiere_acceso_empresa
def api_compras_siigo(slug):
    desde = request.args.get("desde") or None
    hasta = request.args.get("hasta") or None
    texto = request.args.get("q") or None
    try:
        return jsonify(orquestador.listar_compras_siigo(slug, desde, hasta, texto))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/compras-siigo/descargar")
@requiere_login
@requiere_acceso_empresa
def api_descargar_compras_siigo(slug):
    data = request.get_json(silent=True) or {}
    desde = (data.get("desde") or "").strip() or None
    hasta = (data.get("hasta") or "").strip() or None
    try:
        return jsonify(orquestador.descargar_compras_siigo(slug, desde, hasta))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (ValueError, siigo_client.SiigoError) as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/empresas/<slug>/catalogos-siigo/<tipo>")
@requiere_login
@requiere_acceso_empresa
def api_catalogo_siigo(slug, tipo):
    try:
        return jsonify(orquestador.listar_catalogo_siigo(slug, tipo))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/catalogos-siigo/actualizar")
@requiere_login
@requiere_acceso_empresa
def api_actualizar_catalogos_siigo(slug):
    try:
        return jsonify(orquestador.actualizar_catalogos_siigo(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (ValueError, siigo_client.SiigoError) as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/empresas/<slug>/plan-cuentas")
@requiere_login
@requiere_acceso_empresa
def api_plan_cuentas(slug):
    solo_transaccionales = request.args.get("transaccionales") == "1"
    try:
        return jsonify(orquestador.listar_plan_cuentas(slug, solo_transaccionales))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/plan-cuentas/importar")
@requiere_login
@requiere_acceso_empresa
def api_importar_plan_cuentas(slug):
    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"error": "Falta el archivo Excel ('archivo' en el form-data)."}), 400
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        archivo.save(tmp.name)
        ruta_temp = tmp.name
    try:
        return jsonify(orquestador.importar_plan_cuentas(slug, ruta_temp))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"No se pudo leer el Excel: {e}"}), 400
    finally:
        Path(ruta_temp).unlink(missing_ok=True)


@app.get("/api/empresas/<slug>/config-contai")
@requiere_login
@requiere_acceso_empresa
def api_config_contai(slug):
    try:
        return jsonify(orquestador.obtener_config_contai(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/config-contai")
@requiere_login
@requiere_acceso_empresa
def api_guardar_config_contai(slug):
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(orquestador.guardar_config_contai(slug, data))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/empresas/<slug>/plan-cuentas-contai")
@requiere_login
@requiere_acceso_empresa
def api_plan_cuentas_contai(slug):
    solo_transaccionales = request.args.get("transaccionales") == "1"
    try:
        return jsonify(orquestador.listar_plan_cuentas_contai(slug, solo_transaccionales))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/plan-cuentas-contai/importar")
@requiere_login
@requiere_acceso_empresa
def api_importar_plan_cuentas_contai(slug):
    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"error": "Falta el archivo Excel ('archivo' en el form-data)."}), 400
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        archivo.save(tmp.name)
        ruta_temp = tmp.name
    try:
        return jsonify(orquestador.importar_plan_cuentas_contai(slug, ruta_temp))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"No se pudo leer el Excel: {e}"}), 400
    finally:
        Path(ruta_temp).unlink(missing_ok=True)


@app.get("/api/empresas/<slug>/comprobantes-contai")
@requiere_login
@requiere_acceso_empresa
def api_comprobantes_contai(slug):
    try:
        return jsonify(orquestador.listar_comprobantes_contai(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/comprobantes-contai/importar")
@requiere_login
@requiere_acceso_empresa
def api_importar_comprobantes_contai(slug):
    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"error": "Falta el archivo Excel ('archivo' en el form-data)."}), 400
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        archivo.save(tmp.name)
        ruta_temp = tmp.name
    try:
        return jsonify(orquestador.importar_comprobantes_contai(slug, ruta_temp))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"No se pudo leer el Excel: {e}"}), 400
    finally:
        Path(ruta_temp).unlink(missing_ok=True)


@app.get("/api/empresas/<slug>/movimientos-contai")
@requiere_login
@requiere_acceso_empresa
def api_movimientos_contai(slug):
    try:
        return jsonify(orquestador.obtener_resumen_movimientos_contai(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/empresas/<slug>/movimientos-contai/lineas")
@requiere_login
@requiere_acceso_empresa
def api_listar_movimientos_contai(slug):
    """Consulta del histórico ya importado (no el resumen) -- filtra por
    NIT, nombre de proveedor o número de documento vía ?q=, y por rango de
    fechas vía ?desde=&hasta= (ISO), mismo patrón que /compras-siigo."""
    try:
        return jsonify(orquestador.listar_movimientos_contai(
            slug, texto=request.args.get("q"),
            desde=request.args.get("desde"), hasta=request.args.get("hasta"),
        ))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/movimientos-contai/importar")
@requiere_login
@requiere_acceso_empresa
def api_importar_movimientos_contai(slug):
    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"error": "Falta el archivo Excel ('archivo' en el form-data)."}), 400
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        archivo.save(tmp.name)
        ruta_temp = tmp.name
    try:
        return jsonify(orquestador.importar_movimientos_contai(slug, ruta_temp))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"No se pudo leer el Excel: {e}"}), 400
    finally:
        Path(ruta_temp).unlink(missing_ok=True)


@app.get("/api/empresas/<slug>/terceros-contai")
@requiere_login
@requiere_acceso_empresa
def api_terceros_contai(slug):
    try:
        empresa = orquestador.resolver_empresa(slug)
        conn = orquestador.state_store.conectar(empresa["nit"])
        try:
            return jsonify({"total": orquestador.state_store.contar_terceros_contai(conn)})
        finally:
            conn.close()
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/terceros-contai/importar")
@requiere_login
@requiere_acceso_empresa
def api_importar_terceros_contai(slug):
    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"error": "Falta el archivo Excel ('archivo' en el form-data)."}), 400
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        archivo.save(tmp.name)
        ruta_temp = tmp.name
    try:
        return jsonify(orquestador.importar_terceros_contai(slug, ruta_temp))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"No se pudo leer el Excel: {e}"}), 400
    finally:
        Path(ruta_temp).unlink(missing_ok=True)


@app.post("/api/empresas/<slug>/contai/previsualizar")
@requiere_login
@requiere_acceso_empresa
def api_previsualizar_exportacion_contai(slug):
    cufes = (request.get_json(silent=True) or {}).get("cufes") or []
    if not cufes:
        return jsonify({"error": "Falta 'cufes' (lista) en el cuerpo de la petición."}), 400
    try:
        return jsonify(orquestador.previsualizar_exportacion_contai(slug, cufes))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/contai/confirmar")
@requiere_login
@requiere_acceso_empresa
def api_confirmar_exportacion_contai(slug):
    cufes = (request.get_json(silent=True) or {}).get("cufes") or []
    if not cufes:
        return jsonify({"error": "Falta 'cufes' (lista) en el cuerpo de la petición."}), 400
    try:
        resultado = orquestador.confirmar_exportacion_contai(slug, cufes)
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404

    # Dos formatos por cada uno de los dos contenidos (pedido explícito del
    # usuario): movimientos y terceros, cada uno en .xlsx y en .txt.
    return jsonify({
        "exportadas": resultado["exportadas"], "con_error": resultado["con_error"], "detalle": resultado["detalle"],
        "movimientos_xlsx_b64": base64.b64encode(resultado["movimientos_xlsx"]).decode("ascii"),
        "movimientos_txt_b64": base64.b64encode(resultado["movimientos_txt"]).decode("ascii"),
        "terceros_xlsx_b64": base64.b64encode(resultado["terceros_xlsx"]).decode("ascii"),
        "terceros_txt_b64": base64.b64encode(resultado["terceros_txt"]).decode("ascii"),
    })


@app.post("/api/empresas/<slug>/importar")
@requiere_login
@requiere_acceso_empresa
def api_importar(slug):
    carpeta = (request.get_json(silent=True) or {}).get("carpeta")
    if not carpeta:
        return jsonify({"error": "Falta 'carpeta' en el cuerpo de la petición."}), 400
    try:
        return jsonify(orquestador.ejecutar_importar(slug, carpeta))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/importar-google")
@requiere_login
@requiere_acceso_empresa
def api_importar_google(slug):
    try:
        return jsonify(orquestador.importar_desde_google(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (ValueError, drive_client.DriveError, gmail_client.GmailError, google_conexiones.GoogleConexionError) as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/importar-todo")
@requiere_login
@requiere_acceso_empresa
def api_importar_todo(slug):
    """El botón único de importación -- trae Drive/Gmail si están
    configurados y además importa lo que ya haya en la carpeta local
    (incluye lo que llegue por /importar-archivos). Nunca falla solo porque
    Google no esté configurado (ver orquestador.importar_todo)."""
    try:
        return jsonify(orquestador.importar_todo(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/importar-archivos")
@requiere_login
@requiere_acceso_empresa
def api_importar_archivos_subidos(slug):
    """Para el modo SaaS: quien importa no tiene ni sabe de una ruta en el
    servidor, así que sube el/los ZIP directamente desde el navegador."""
    archivos = request.files.getlist("archivos")
    if not archivos:
        return jsonify({"error": "Selecciona al menos un archivo para subir."}), 400
    try:
        subida = orquestador.guardar_archivos_subidos(slug, [(a.filename, a.read()) for a in archivos])
        resultado = orquestador.importar_todo(slug)
        return jsonify({"subida": subida, **resultado})
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/clasificar")
@requiere_login
@requiere_acceso_empresa
def api_no_implementado(slug):
    comando = request.path.rsplit("/", 1)[-1]
    return jsonify({"error": f"El comando '{comando}' todavía no está implementado."}), 501


@app.get("/api/empresas/<slug>/facturas/<cufe>/compra-siigo")
@requiere_login
@requiere_acceso_empresa
def api_compra_siigo_de_factura(slug, cufe):
    try:
        compra = orquestador.obtener_compra_siigo_de_factura(slug, cufe)
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    if compra is None:
        return jsonify({"error": (
            "Esta factura no aparece en el caché de 'Compras en Siigo'. Si crees que ya está "
            "causada, descarga primero ese periodo en la pantalla Compras en Siigo."
        )}), 404
    return jsonify(compra)


@app.post("/api/empresas/<slug>/enviar-siigo/previsualizar")
@requiere_login
@requiere_acceso_empresa
def api_previsualizar_envio_siigo(slug):
    cufes = (request.get_json(silent=True) or {}).get("cufes") or []
    if not cufes:
        return jsonify({"error": "Falta 'cufes' (lista) en el cuerpo de la petición."}), 400
    try:
        return jsonify(orquestador.previsualizar_envio_siigo(slug, cufes))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/enviar-siigo/confirmar")
@requiere_login
@requiere_acceso_empresa
def api_confirmar_envio_siigo(slug):
    cufes = (request.get_json(silent=True) or {}).get("cufes") or []
    if not cufes:
        return jsonify({"error": "Falta 'cufes' (lista) en el cuerpo de la petición."}), 400
    try:
        return jsonify(orquestador.confirmar_envio_siigo(slug, cufes))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (ValueError, siigo_client.SiigoError) as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/eliminar-siigo/previsualizar")
@requiere_login
@requiere_acceso_empresa
def api_previsualizar_eliminacion_siigo(slug):
    data = request.get_json(silent=True) or {}
    desde = (data.get("desde") or "").strip()
    hasta = (data.get("hasta") or "").strip()
    cufes = data.get("cufes") or None
    try:
        return jsonify(orquestador.previsualizar_eliminacion_siigo(slug, desde, hasta, cufes))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/eliminar-siigo/confirmar")
@requiere_login
@requiere_acceso_empresa
def api_confirmar_eliminacion_siigo(slug):
    cufes = (request.get_json(silent=True) or {}).get("cufes") or []
    if not cufes:
        return jsonify({"error": "Falta 'cufes' (lista) en el cuerpo de la petición."}), 400
    try:
        return jsonify(orquestador.confirmar_eliminacion_siigo(slug, cufes))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (ValueError, siigo_client.SiigoError) as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/validar-completitud")
@requiere_login
@requiere_acceso_empresa
def api_validar_completitud(slug):
    data = request.get_json(silent=True) or {}
    carpeta = (data.get("carpeta") or "").strip()
    archivo = (data.get("archivo") or "").strip()
    desde = (data.get("desde") or "").strip()
    hasta = (data.get("hasta") or "").strip()
    if not carpeta or not archivo:
        return jsonify({"error": "Faltan 'carpeta' y/o 'archivo' en la petición."}), 400
    try:
        return jsonify(orquestador.validar_completitud(slug, carpeta, archivo, desde, hasta))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/validar-completitud-archivo")
@requiere_login
@requiere_acceso_empresa
def api_validar_completitud_archivo(slug):
    """Como /validar-completitud, pero para cuando el listado se sube desde
    el navegador en vez de ya estar en el servidor (modo SaaS)."""
    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"error": "Falta el archivo del listado ('archivo' en el form-data)."}), 400
    desde = (request.form.get("desde") or "").strip()
    hasta = (request.form.get("hasta") or "").strip()
    try:
        return jsonify(orquestador.validar_completitud_archivo_subido(
            slug, archivo.filename, archivo.read(), desde, hasta,
        ))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/reporte-faltantes-completitud")
@requiere_login
@requiere_acceso_empresa
def api_reporte_faltantes_completitud(slug):
    """Arma el .xlsx descargable de las facturas faltantes que ya se
    mostraron en la tabla de /validar-completitud -- recibe esa misma lista
    tal cual (no vuelve a comparar nada), así que el reporte siempre
    coincide con lo que el usuario tiene en pantalla."""
    data = request.get_json(silent=True) or {}
    faltantes = data.get("faltantes") or []
    contenido = orquestador.reporte_faltantes_completitud_xlsx(faltantes)
    return jsonify({"reporte_xlsx_b64": base64.b64encode(contenido).decode("ascii")})


_CAMPOS_FACTURA = {"tipo_comprobante_id", "medio_pago_id", "modo_pago_contai"}
_CAMPOS_ITEM = {"cuenta_contable", "iva_tax_id", "retencion_tax_id"}


@app.patch("/api/empresas/<slug>/facturas/<cufe>")
@requiere_login
@requiere_acceso_empresa
def api_actualizar_factura(slug, cufe):
    data = request.get_json(silent=True) or {}
    campos = {k: v for k, v in data.items() if k in _CAMPOS_FACTURA}
    try:
        return jsonify(orquestador.actualizar_factura(slug, cufe, campos))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.patch("/api/empresas/<slug>/facturas/<cufe>/items/<int:item_id>")
@requiere_login
@requiere_acceso_empresa
def api_actualizar_item(slug, cufe, item_id):
    data = request.get_json(silent=True) or {}
    campos = {k: v for k, v in data.items() if k in _CAMPOS_ITEM}
    try:
        return jsonify(orquestador.actualizar_item(slug, cufe, item_id, campos))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/facturas/<cufe>/items/<int:item_id>/replicar")
@requiere_login
@requiere_acceso_empresa
def api_replicar_item(slug, cufe, item_id):
    campo = (request.get_json(silent=True) or {}).get("campo")
    try:
        return jsonify(orquestador.replicar_campo_item(slug, cufe, item_id, campo))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/facturas/<cufe>/items/<int:item_id>/recalcular-candidatos")
@requiere_login
@requiere_acceso_empresa
def api_recalcular_candidatos(slug, cufe, item_id):
    data = request.get_json(silent=True) or {}
    campo = data.get("campo")
    desde = (data.get("desde") or "").strip()
    hasta = (data.get("hasta") or "").strip()
    try:
        return jsonify(orquestador.buscar_candidatos_recalculo(slug, cufe, item_id, campo, desde, hasta))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/recalcular-aplicar")
@requiere_login
@requiere_acceso_empresa
def api_recalcular_aplicar(slug):
    data = request.get_json(silent=True) or {}
    campo = data.get("campo")
    valor = data.get("valor")
    item_ids = data.get("item_ids") or []
    if not item_ids:
        return jsonify({"error": "Falta 'item_ids' (lista) en el cuerpo de la petición."}), 400
    try:
        return jsonify(orquestador.aplicar_recalculo_masivo(slug, campo, valor, item_ids))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/completar-cabecera")
@requiere_login
@requiere_acceso_empresa
def api_completar_cabecera(slug):
    try:
        return jsonify(orquestador.completar_cabecera_faltante_por_empresa(slug))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/facturas/eliminar")
@requiere_login
@requiere_acceso_empresa
def api_eliminar_facturas(slug):
    cufes = (request.get_json(silent=True) or {}).get("cufes") or []
    if not cufes:
        return jsonify({"error": "Falta 'cufes' (lista) en el cuerpo de la petición."}), 400
    try:
        return jsonify(orquestador.eliminar_facturas(slug, cufes))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/empresas/<slug>/proveedores/<nit>/autorretenedor")
@requiere_login
@requiere_acceso_empresa
def api_marcar_autorretenedor(slug, nit):
    data = request.get_json(silent=True) or {}
    autorretenedor = bool(data.get("autorretenedor"))
    nombre = data.get("nombre") or ""
    try:
        orquestador.resolver_empresa(slug)  # valida que la empresa exista antes de tocar config/proveedores/
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(orquestador.marcar_proveedor_autorretenedor(nit, nombre, autorretenedor))


@app.delete("/api/empresas/<slug>/reglas-propuestas/<int:regla_id>")
@requiere_login
@requiere_acceso_empresa
def api_eliminar_regla_propuesta(slug, regla_id):
    try:
        orquestador.eliminar_regla_propuesta(slug, regla_id, g.usuario)
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (auth.AuthError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"eliminada": True})


@app.get("/api/empresas/<slug>/reglas-confirmadas")
@requiere_login
@requiere_acceso_empresa
def api_reglas_confirmadas(slug):
    try:
        return jsonify(orquestador.reglas_confirmadas(slug, g.usuario))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/empresas/<slug>/reglas-propuestas")
@requiere_login
@requiere_acceso_empresa
def api_listar_reglas_propuestas(slug):
    try:
        return jsonify(orquestador.listar_reglas_propuestas(slug, g.usuario))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except auth.AuthError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/empresas/<slug>/reglas-propuestas")
@requiere_login
@requiere_acceso_empresa
def api_crear_regla_propuesta(slug):
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(orquestador.crear_regla_propuesta(slug, data.get("texto", ""), g.usuario))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (auth.AuthError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@app.patch("/api/empresas/<slug>/reglas-propuestas/<int:regla_id>")
@requiere_login
@requiere_acceso_empresa
def api_cambiar_estado_regla_propuesta(slug, regla_id):
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(orquestador.cambiar_estado_regla_propuesta(
            slug, regla_id, data.get("estado", ""), data.get("respuesta"), g.usuario,
        ))
    except orquestador.EmpresaNoEncontrada as e:
        return jsonify({"error": str(e)}), 404
    except (auth.AuthError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    app.run(port=5000, debug=True)
