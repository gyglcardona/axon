"""
Registro de "conexiones de Google" reutilizables -- reemplaza el modelo
anterior de una sola cuenta de Google global (`config/google/token.json`,
usada por TODAS las empresas vía `drive_client.autenticar()`). Una conexión
es el token de UNA cuenta de Google (con permiso de solo lectura sobre Drive
y Gmail) que se puede **asociar** a una o varias empresas -- así se modela
tanto "el contador conecta una vez y la reutiliza en sus empresas" como
"esta empresa conecta la suya propia" (ver
docs/03-ingesta-dian/conexion-google.md).

La conexión de id especial `"legacy"` es la que ya existía antes de este
módulo (`config/google/client_secret.json` + `token.json`, creada a mano con
`python src/autorizar_drive.py`) -- se sigue leyendo de esos mismos paths,
ninguna empresa que ya la usa necesita volver a dar consentimiento. Solo
tiene el scope de Drive (nunca se autorizó para Gmail); por eso
`orquestador.guardar_conexion_gmail` bloquea activar Gmail mientras la
empresa siga en la conexión legacy.

Las conexiones NUEVAS se crean con un flujo OAuth basado en navegador
(`google_auth_oauthlib.flow.Flow`, no `InstalledAppFlow` -- esa clase es
para scripts de escritorio con `run_local_server`, aquí Flask necesita un
`redirect_uri` fijo). Requiere un cliente OAuth tipo "Aplicación web" en
Google Cloud Console (el cliente "App de escritorio" actual NO sirve para
esto, no admite un `redirect_uri` propio) -- ver
docs/03-ingesta-dian/conexion-google.md para los pasos manuales exactos.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

GOOGLE_DIR = Path("config/google")
CLIENT_SECRET_LEGACY_PATH = GOOGLE_DIR / "client_secret.json"
CLIENT_SECRET_WEB_PATH = GOOGLE_DIR / "client_secret_web.json"
TOKEN_LEGACY_PATH = GOOGLE_DIR / "token.json"
CONEXIONES_DIR = GOOGLE_DIR / "conexiones"
REGISTRO_PATH = CONEXIONES_DIR / "registro.json"
PENDIENTES_PATH = GOOGLE_DIR / "oauth_pendientes.json"

REDIRECT_URI = "http://localhost:5000/oauth/google/callback"
ESTADO_VIGENCIA_MINUTOS = 30

CONEXION_LEGACY = "legacy"


class GoogleConexionError(Exception):
    """Cualquier fallo autenticando o autorizando contra Google -- token
    vencido/revocado, state de OAuth inválido/expirado, falta configuración
    de Google Cloud Console. Mensaje listo para mostrar al usuario."""


def estado_client_secret_web() -> dict:
    """¿Ya está configurado el cliente OAuth tipo "Aplicación web" que hace
    falta para conectar cuentas de Google nuevas? Este archivo es la
    identidad de LA APLICACIÓN AXON ante Google (se sube una sola vez, quien
    la opera) -- no confundir con las conexiones por empresa, que sí se
    hacen 100% desde la interfaz sin tocar archivos (ver
    docs/03-ingesta-dian/conexion-google.md). Nunca devuelve el
    `client_secret` real, solo si está configurado y el `client_id` (dato
    público, sirve para confirmar visualmente que es el archivo correcto)."""
    if not CLIENT_SECRET_WEB_PATH.is_file():
        return {"configurado": False, "client_id": None}
    try:
        with open(CLIENT_SECRET_WEB_PATH, encoding="utf-8") as f:
            datos = json.load(f)
        client_id = datos.get("web", {}).get("client_id")
    except (json.JSONDecodeError, OSError):
        client_id = None
    return {"configurado": True, "client_id": client_id}


def guardar_client_secret_web(contenido: bytes) -> dict:
    """Guarda el archivo del cliente OAuth "Aplicación web" descargado de
    Google Cloud Console. Valida que sea el tipo correcto -- el error más
    común es subir por accidente el cliente "App de escritorio" (clave
    `"installed"` en vez de `"web"`), que no sirve para este flujo (no
    admite un `redirect_uri` fijo)."""
    try:
        datos = json.loads(contenido)
    except json.JSONDecodeError as e:
        raise GoogleConexionError(f"El archivo no es un JSON válido: {e}")

    if "installed" in datos and "web" not in datos:
        raise GoogleConexionError(
            "Este es un cliente OAuth tipo 'App de escritorio' (clave \"installed\") -- hace falta "
            "uno tipo 'Aplicación web' (clave \"web\"). Créalo de nuevo en Google Cloud Console "
            "eligiendo ese tipo. Ver docs/03-ingesta-dian/conexion-google.md."
        )
    if "web" not in datos:
        raise GoogleConexionError(
            "El archivo no tiene el formato esperado de un cliente OAuth de Google (falta la clave \"web\")."
        )

    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT_SECRET_WEB_PATH.write_bytes(contenido)
    return estado_client_secret_web()


def _leer_registro() -> list[dict]:
    if not REGISTRO_PATH.is_file():
        return []
    with open(REGISTRO_PATH, encoding="utf-8") as f:
        return json.load(f)


def _escribir_registro(conexiones: list[dict]) -> None:
    CONEXIONES_DIR.mkdir(parents=True, exist_ok=True)
    with open(REGISTRO_PATH, "w", encoding="utf-8") as f:
        json.dump(conexiones, f, ensure_ascii=False, indent=2)
        f.write("\n")


def listar_conexiones() -> list[dict]:
    """Todas las conexiones disponibles para asociar a una empresa --
    incluye la conexión "legacy" (sintetizada, no vive en `registro.json`)
    si `token.json` existe, siempre primero en la lista."""
    conexiones = _leer_registro()
    if TOKEN_LEGACY_PATH.is_file():
        conexiones = [{
            "id": CONEXION_LEGACY,
            "cuenta_email": None,
            "etiqueta": "Conexión compartida (actual)",
            "creado_por_usuario_id": None,
        }, *conexiones]
    return conexiones


def _cargar_y_refrescar(ruta_token: Path) -> Credentials:
    # scopes=None (no pasar SCOPES a la fuerza) -- cada archivo de token trae
    # sus propios scopes reales concedidos por Google. La conexión legacy
    # solo tiene drive.readonly (se creó antes de que existiera el scope de
    # Gmail); pedirle explícitamente el scope de Gmail al refrescar revienta
    # con "invalid_scope" -- bug real confirmado corriendo esto contra el
    # token.json real del usuario.
    creds = Credentials.from_authorized_user_file(str(ruta_token), scopes=None)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise GoogleConexionError(
                f"El token de esta conexión de Google ya no es válido (revocado o vencido): {e}. "
                "Hay que conectar la cuenta de nuevo desde 'Configuración'."
            )
        ruta_token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def obtener_credenciales(conexion_id: str) -> Credentials:
    """Credenciales listas para usar (refrescadas si hacía falta) de la
    conexión pedida. `""`/`None` se trata como la conexión legacy --
    compatibilidad total con las empresas que ya funcionaban antes de que
    existieran conexiones por empresa."""
    conexion_id = conexion_id or CONEXION_LEGACY
    if conexion_id == CONEXION_LEGACY:
        if not TOKEN_LEGACY_PATH.is_file():
            raise GoogleConexionError(
                "Todavía no se autorizó la conexión compartida de Google -- corre "
                "'python -m src.autorizar_drive' una vez, o conecta una cuenta nueva "
                "desde 'Configuración'."
            )
        return _cargar_y_refrescar(TOKEN_LEGACY_PATH)

    ruta = CONEXIONES_DIR / f"{conexion_id}.json"
    if not ruta.is_file():
        raise GoogleConexionError(
            f"La conexión de Google '{conexion_id}' no existe o fue eliminada -- "
            "elige otra conexión o conecta una cuenta nueva desde 'Configuración'."
        )
    return _cargar_y_refrescar(ruta)


def _leer_pendientes() -> dict:
    if not PENDIENTES_PATH.is_file():
        return {}
    with open(PENDIENTES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _escribir_pendientes(pendientes: dict) -> None:
    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    with open(PENDIENTES_PATH, "w", encoding="utf-8") as f:
        json.dump(pendientes, f, ensure_ascii=False, indent=2)
        f.write("\n")


def iniciar_autorizacion(slug: str, usuario_id: int | None = None) -> str:
    """Arma la URL de consentimiento de Google para conectar una cuenta
    nueva en nombre de `slug`, y guarda el `state` generado para poder
    identificar esa empresa cuando Google redirija de vuelta (Google no
    conoce el slug, solo el `state` que le pasamos). `usuario_id` (quién iba
    logueado cuando inició el flujo) queda registrado como dueño de la
    conexión nueva -- ver `procesar_callback` y
    `orquestador.listar_conexiones_google_visibles`."""
    if not CLIENT_SECRET_WEB_PATH.is_file():
        raise GoogleConexionError(
            f"No existe '{CLIENT_SECRET_WEB_PATH}' -- hace falta crear un cliente OAuth "
            "tipo 'Aplicación web' en Google Cloud Console (el cliente 'App de escritorio' "
            "actual no admite este flujo) y descargarlo ahí. Ver "
            "docs/03-ingesta-dian/conexion-google.md."
        )

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET_WEB_PATH), scopes=SCOPES, redirect_uri=REDIRECT_URI,
    )
    url_autorizacion, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent",
    )

    pendientes = _leer_pendientes()
    pendientes[state] = {
        "slug": slug,
        "usuario_id": usuario_id,
        "creado_en": datetime.now(timezone.utc).isoformat(),
        # google-auth-oauthlib genera un code_verifier (PKCE) distinto por
        # cada Flow() -- procesar_callback() crea un Flow nuevo para
        # intercambiar el code, así que sin guardar este valor acá, Google
        # responde "invalid_grant: Missing code verifier" al canjear el code.
        "code_verifier": flow.code_verifier,
    }
    _escribir_pendientes(pendientes)
    return url_autorizacion


def _obtener_email_cuenta(creds: Credentials) -> str | None:
    try:
        servicio = build("gmail", "v1", credentials=creds, cache_discovery=False)
        perfil = servicio.users().getProfile(userId="me").execute()
        return perfil.get("emailAddress")
    except Exception:
        return None


def procesar_callback(state: str, code: str) -> dict:
    """Completa el flujo iniciado por `iniciar_autorizacion`: intercambia el
    `code` por credenciales, registra una conexión nueva, y devuelve
    `{conexion_id, cuenta_email, slug}` para que el caller la asocie a la
    empresa que la inició."""
    pendientes = _leer_pendientes()
    pendiente = pendientes.pop(state, None)
    if pendiente is None:
        raise GoogleConexionError(
            "El enlace de autorización expiró o ya se usó -- intenta conectar la cuenta de nuevo."
        )
    _escribir_pendientes(pendientes)

    creado_en = datetime.fromisoformat(pendiente["creado_en"])
    if datetime.now(timezone.utc) - creado_en > timedelta(minutes=ESTADO_VIGENCIA_MINUTOS):
        raise GoogleConexionError(
            "El enlace de autorización expiró (pasaron más de "
            f"{ESTADO_VIGENCIA_MINUTOS} minutos) -- intenta conectar la cuenta de nuevo."
        )

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRET_WEB_PATH), scopes=SCOPES, redirect_uri=REDIRECT_URI,
        code_verifier=pendiente.get("code_verifier"),
    )
    flow.fetch_token(code=code)
    creds = flow.credentials

    conexion_id = uuid.uuid4().hex
    CONEXIONES_DIR.mkdir(parents=True, exist_ok=True)
    (CONEXIONES_DIR / f"{conexion_id}.json").write_text(creds.to_json(), encoding="utf-8")

    cuenta_email = _obtener_email_cuenta(creds)
    conexiones = _leer_registro()
    conexiones.append({
        "id": conexion_id,
        "cuenta_email": cuenta_email,
        "etiqueta": cuenta_email or f"Conexión {conexion_id[:8]}",
        "creado_en": datetime.now(timezone.utc).isoformat(),
        "creado_por_usuario_id": pendiente.get("usuario_id"),
    })
    _escribir_registro(conexiones)

    return {"conexion_id": conexion_id, "cuenta_email": cuenta_email, "slug": pendiente["slug"]}
