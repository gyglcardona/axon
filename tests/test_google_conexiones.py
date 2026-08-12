"""
Pruebas de src/google_conexiones.py: registro de conexiones reutilizables +
flujo OAuth basado en navegador. Nunca toca la red real ni Google Cloud --
`Flow` se reemplaza por una versión falsa y todos los paths de
config/google/ se redirigen a tmp_path.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import google_conexiones  # noqa: E402


class _FakeCreds:
    def to_json(self):
        return json.dumps({
            "refresh_token": "r", "client_id": "c", "client_secret": "s",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": google_conexiones.SCOPES,
        })


class _FakeFlow:
    ultimo_code = None
    ultimo_code_verifier_recibido = "SIN LLAMAR TODAVIA"

    def __init__(self, *args, **kwargs):
        # google-auth-oauthlib genera un code_verifier (PKCE) distinto por
        # cada Flow() de verdad -- lo simulamos acá para poder probar que
        # iniciar_autorizacion lo guarda y procesar_callback lo reenvía
        # (ver el bug real: "invalid_grant: Missing code verifier").
        self.code_verifier = "FAKE-CODE-VERIFIER"
        _FakeFlow.ultimo_code_verifier_recibido = kwargs.get("code_verifier")

    def authorization_url(self, **kwargs):
        return "https://accounts.google.com/o/oauth2/auth?fake=1", "FAKE-STATE-123"

    def fetch_token(self, code):
        _FakeFlow.ultimo_code = code
        self.credentials = _FakeCreds()

    @classmethod
    def from_client_secrets_file(cls, *args, **kwargs):
        return cls(**kwargs)


@pytest.fixture
def entorno_google(tmp_path, monkeypatch):
    google_dir = tmp_path / "config" / "google"
    monkeypatch.setattr(google_conexiones, "GOOGLE_DIR", google_dir)
    monkeypatch.setattr(google_conexiones, "CLIENT_SECRET_WEB_PATH", google_dir / "client_secret_web.json")
    monkeypatch.setattr(google_conexiones, "TOKEN_LEGACY_PATH", google_dir / "token.json")
    monkeypatch.setattr(google_conexiones, "CONEXIONES_DIR", google_dir / "conexiones")
    monkeypatch.setattr(google_conexiones, "REGISTRO_PATH", google_dir / "conexiones" / "registro.json")
    monkeypatch.setattr(google_conexiones, "PENDIENTES_PATH", google_dir / "oauth_pendientes.json")
    monkeypatch.setattr(google_conexiones, "Flow", _FakeFlow)
    monkeypatch.setattr(google_conexiones, "_obtener_email_cuenta", lambda creds: "empresa@gmail.com")
    return google_dir


def test_listar_conexiones_vacio_sin_legacy_ni_registro(entorno_google):
    assert google_conexiones.listar_conexiones() == []


def test_listar_conexiones_sintetiza_legacy_si_token_existe(entorno_google):
    entorno_google.mkdir(parents=True)
    (entorno_google / "token.json").write_text("{}", encoding="utf-8")

    conexiones = google_conexiones.listar_conexiones()

    assert len(conexiones) == 1
    assert conexiones[0]["id"] == "legacy"


def test_iniciar_autorizacion_sin_client_secret_web_da_error_claro(entorno_google):
    with pytest.raises(google_conexiones.GoogleConexionError, match="Aplicación web"):
        google_conexiones.iniciar_autorizacion("empresa-test")


def test_iniciar_autorizacion_guarda_state_pendiente(entorno_google):
    entorno_google.mkdir(parents=True)
    (entorno_google / "client_secret_web.json").write_text("{}", encoding="utf-8")

    url = google_conexiones.iniciar_autorizacion("empresa-test")

    assert url == "https://accounts.google.com/o/oauth2/auth?fake=1"
    pendientes = json.loads((entorno_google / "oauth_pendientes.json").read_text(encoding="utf-8"))
    assert pendientes["FAKE-STATE-123"]["slug"] == "empresa-test"


def test_iniciar_autorizacion_guarda_el_code_verifier_de_pkce(entorno_google):
    """Bug real: google-auth-oauthlib genera un code_verifier (PKCE) por
    cada Flow() -- si no se guarda acá y se reenvía en procesar_callback,
    Google responde "invalid_grant: Missing code verifier" al canjear el
    code, porque procesar_callback arma un Flow() nuevo y distinto."""
    entorno_google.mkdir(parents=True)
    (entorno_google / "client_secret_web.json").write_text("{}", encoding="utf-8")

    google_conexiones.iniciar_autorizacion("empresa-test")

    pendientes = json.loads((entorno_google / "oauth_pendientes.json").read_text(encoding="utf-8"))
    assert pendientes["FAKE-STATE-123"]["code_verifier"] == "FAKE-CODE-VERIFIER"


def test_procesar_callback_reenvia_el_code_verifier_guardado(entorno_google):
    entorno_google.mkdir(parents=True)
    (entorno_google / "client_secret_web.json").write_text("{}", encoding="utf-8")
    google_conexiones.iniciar_autorizacion("empresa-test")
    pendientes = json.loads((entorno_google / "oauth_pendientes.json").read_text(encoding="utf-8"))
    state = next(iter(pendientes))

    google_conexiones.procesar_callback(state, "CODE-123")

    assert _FakeFlow.ultimo_code_verifier_recibido == "FAKE-CODE-VERIFIER"


def test_procesar_callback_state_invalido_da_error_claro(entorno_google):
    with pytest.raises(google_conexiones.GoogleConexionError, match="expiró"):
        google_conexiones.procesar_callback("STATE-QUE-NO-EXISTE", "CODE-123")


def test_procesar_callback_state_vencido_da_error_claro(entorno_google):
    entorno_google.mkdir(parents=True)
    vencido = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
    (entorno_google / "oauth_pendientes.json").write_text(
        json.dumps({"STATE-VIEJO": {"slug": "empresa-test", "creado_en": vencido}}), encoding="utf-8",
    )

    with pytest.raises(google_conexiones.GoogleConexionError, match="expiró"):
        google_conexiones.procesar_callback("STATE-VIEJO", "CODE-123")


def test_procesar_callback_valido_registra_conexion(entorno_google):
    entorno_google.mkdir(parents=True)
    (entorno_google / "client_secret_web.json").write_text("{}", encoding="utf-8")
    google_conexiones.iniciar_autorizacion("empresa-test")
    pendientes = json.loads((entorno_google / "oauth_pendientes.json").read_text(encoding="utf-8"))
    state = next(iter(pendientes))

    resultado = google_conexiones.procesar_callback(state, "CODE-123")

    assert resultado["slug"] == "empresa-test"
    assert resultado["cuenta_email"] == "empresa@gmail.com"
    assert _FakeFlow.ultimo_code == "CODE-123"

    conexiones = google_conexiones.listar_conexiones()
    assert any(c["id"] == resultado["conexion_id"] for c in conexiones)
    assert (entorno_google / "conexiones" / f"{resultado['conexion_id']}.json").is_file()

    # el state se consume -- no se puede reusar
    with pytest.raises(google_conexiones.GoogleConexionError, match="expiró"):
        google_conexiones.procesar_callback(state, "CODE-123")


def test_obtener_credenciales_legacy_sin_token_da_error_claro(entorno_google):
    with pytest.raises(google_conexiones.GoogleConexionError, match="autorizar_drive"):
        google_conexiones.obtener_credenciales("")


def test_obtener_credenciales_conexion_inexistente_da_error_claro(entorno_google):
    with pytest.raises(google_conexiones.GoogleConexionError, match="no existe"):
        google_conexiones.obtener_credenciales("no-existe-este-id")


def test_estado_client_secret_web_sin_configurar(entorno_google):
    assert google_conexiones.estado_client_secret_web() == {"configurado": False, "client_id": None}


def test_guardar_client_secret_web_valido(entorno_google):
    contenido = json.dumps({"web": {"client_id": "abc123.apps.googleusercontent.com", "client_secret": "s"}}).encode()

    resultado = google_conexiones.guardar_client_secret_web(contenido)

    assert resultado == {"configurado": True, "client_id": "abc123.apps.googleusercontent.com"}
    assert google_conexiones.estado_client_secret_web() == resultado


def test_guardar_client_secret_web_rechaza_cliente_de_escritorio(entorno_google):
    contenido = json.dumps({"installed": {"client_id": "abc123", "client_secret": "s"}}).encode()

    with pytest.raises(google_conexiones.GoogleConexionError, match="App de escritorio"):
        google_conexiones.guardar_client_secret_web(contenido)


def test_guardar_client_secret_web_rechaza_json_invalido(entorno_google):
    with pytest.raises(google_conexiones.GoogleConexionError, match="JSON"):
        google_conexiones.guardar_client_secret_web(b"esto no es json")
