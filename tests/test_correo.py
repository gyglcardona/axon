"""
Pruebas de src/correo.py: configuración SMTP y envío de correo. Nunca toca
la red real -- `smtplib.SMTP` se reemplaza por una versión falsa.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import correo  # noqa: E402


class _FakeSMTP:
    instancias = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.starttls_llamado = False
        self.login_args = None
        self.sendmail_args = None
        _FakeSMTP.instancias.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        self.starttls_llamado = True

    def login(self, email, password):
        self.login_args = (email, password)

    def sendmail(self, remitente, destinatarios, mensaje):
        self.sendmail_args = (remitente, destinatarios, mensaje)


@pytest.fixture(autouse=True)
def _config_en_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(correo, "CONFIG_PATH", tmp_path / "config" / "correo" / "smtp.json")
    _FakeSMTP.instancias = []
    yield


def test_config_sin_archivo(_config_en_tmp):
    assert correo.obtener_config_smtp() == {"configurado": False, "email": ""}


def test_guardar_y_obtener_config(_config_en_tmp):
    resultado = correo.guardar_config_smtp("cuenta@gmail.com", "abcd efgh ijkl mnop")

    assert resultado == {"configurado": True, "email": "cuenta@gmail.com"}
    # nunca se expone la contraseña de aplicación al leer el estado
    assert "password_app" not in correo.obtener_config_smtp()


def test_guardar_config_rechaza_campos_vacios(_config_en_tmp):
    with pytest.raises(correo.CorreoError, match="obligatorios"):
        correo.guardar_config_smtp("", "algo")
    with pytest.raises(correo.CorreoError, match="obligatorios"):
        correo.guardar_config_smtp("cuenta@gmail.com", "")


def test_enviar_correo_sin_configurar_da_error_claro(_config_en_tmp):
    with pytest.raises(correo.CorreoError, match="Todavía no está configurado"):
        correo.enviar_correo("destino@empresa.com", "Asunto", "<p>Cuerpo</p>")


def test_enviar_correo_usa_smtp_gmail_con_starttls_y_login(_config_en_tmp, monkeypatch):
    correo.guardar_config_smtp("cuenta@gmail.com", "clave-app")
    monkeypatch.setattr(correo.smtplib, "SMTP", _FakeSMTP)

    correo.enviar_correo("destino@empresa.com", "Bienvenido a AXON", "<p>Hola</p>")

    instancia = _FakeSMTP.instancias[0]
    assert instancia.host == "smtp.gmail.com"
    assert instancia.port == 587
    assert instancia.starttls_llamado is True
    assert instancia.login_args == ("cuenta@gmail.com", "clave-app")
    remitente, destinatarios, mensaje = instancia.sendmail_args
    assert remitente == "cuenta@gmail.com"
    assert destinatarios == ["destino@empresa.com"]
    assert "Bienvenido a AXON" in mensaje


def test_enviar_correo_error_smtp_da_mensaje_claro(_config_en_tmp, monkeypatch):
    correo.guardar_config_smtp("cuenta@gmail.com", "clave-mala")

    class _SMTPFalla(_FakeSMTP):
        def login(self, email, password):
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"credenciales invalidas")

    monkeypatch.setattr(correo.smtplib, "SMTP", _SMTPFalla)

    with pytest.raises(correo.CorreoError, match="No se pudo enviar"):
        correo.enviar_correo("destino@empresa.com", "Asunto", "<p>Cuerpo</p>")
