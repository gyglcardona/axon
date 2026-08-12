"""
Envío de correo por SMTP de Gmail con contraseña de aplicación -- mecanismo
distinto y más simple que `google_conexiones.py` (que es OAuth para leer
Drive/Gmail de una empresa); este es solo para que AXON pueda ENVIAR correos
de invitación de usuario y recuperación de contraseña (ver `src/auth.py`).

Credenciales en `config/correo/smtp.json` (gitignored, mismo tratamiento que
`config/google/`). Se configuran desde la interfaz ("Configuración de correo
(sistema)"), nunca a mano.
"""

from __future__ import annotations

import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
CONFIG_PATH = Path("config/correo/smtp.json")


class CorreoError(Exception):
    """Fallo enviando o configurando el correo -- credenciales faltantes o
    incorrectas, sin red. Mensaje listo para mostrar al usuario."""


def obtener_config_smtp() -> dict:
    """Nunca devuelve la contraseña de aplicación real, solo si ya está
    configurado y con qué correo -- mismo criterio que
    `google_conexiones.estado_client_secret_web`."""
    if not CONFIG_PATH.is_file():
        return {"configurado": False, "email": ""}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        datos = json.load(f)
    email = datos.get("email", "")
    return {"configurado": bool(email and datos.get("password_app")), "email": email}


def guardar_config_smtp(email: str, password_app: str) -> dict:
    email = email.strip()
    password_app = password_app.strip()
    if not email or not password_app:
        raise CorreoError("El correo y la contraseña de aplicación son obligatorios.")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"email": email, "password_app": password_app}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return obtener_config_smtp()


def _leer_credenciales() -> tuple[str, str]:
    if not CONFIG_PATH.is_file():
        raise CorreoError(
            "Todavía no está configurado el envío de correo -- configúralo en "
            "'Configuración de correo (sistema)'."
        )
    with open(CONFIG_PATH, encoding="utf-8") as f:
        datos = json.load(f)
    email, password_app = datos.get("email"), datos.get("password_app")
    if not email or not password_app:
        raise CorreoError("La configuración de correo está incompleta -- vuelve a guardarla.")
    return email, password_app


def enviar_correo(destinatario: str, asunto: str, cuerpo_html: str) -> None:
    email, password_app = _leer_credenciales()

    mensaje = MIMEMultipart("alternative")
    mensaje["Subject"] = asunto
    mensaje["From"] = email
    mensaje["To"] = destinatario
    mensaje.attach(MIMEText(cuerpo_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as servidor:
            servidor.starttls()
            servidor.login(email, password_app)
            servidor.sendmail(email, [destinatario], mensaje.as_string())
    except smtplib.SMTPException as e:
        raise CorreoError(f"No se pudo enviar el correo: {e}")
    except OSError as e:
        raise CorreoError(f"No se pudo conectar con el servidor de correo: {e}")
