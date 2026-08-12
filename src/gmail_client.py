"""
Cliente real contra la API de Gmail (solo lectura) -- busca adjuntos .zip en
bandeja de entrada y spam desde una fecha dada, y descarga los bytes de un
adjunto puntual. Espejo de `drive_client.py`: recibe credenciales ya
resueltas (`google_conexiones.obtener_credenciales`), no se autentica solo.

Búsqueda deliberadamente amplia (confirmado con el usuario): cualquier
adjunto `.zip`, sin filtrar por remitente ni asunto -- las facturas DIAN no
siempre llegan del mismo remitente. Lo que no sea una factura real ya lo
descarta el pipeline existente (`orquestador.ejecutar_importar` cuenta
`no_facturas`), igual que hoy con Drive.
"""

from __future__ import annotations

import base64

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class GmailError(Exception):
    """Cualquier fallo hablando con Gmail -- sin red, adjunto ya no
    disponible, etc. Mensaje listo para mostrar al usuario."""


def _servicio(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _query(desde_fecha: str, buscar_en_spam: bool) -> str:
    fecha_gmail = desde_fecha.replace("-", "/")
    partes = [f"after:{fecha_gmail}", "filename:zip"]
    partes.append("(in:inbox OR in:spam)" if buscar_en_spam else "in:inbox")
    return " ".join(partes)


def _recorrer_partes(payload: dict):
    """Un adjunto puede estar en cualquier nivel de anidamiento
    (multipart/mixed dentro de multipart/alternative, etc.) -- recorre todo
    el árbol de `parts` y entrega cualquier parte con `filename` (las partes
    de texto/html no lo traen)."""
    if payload.get("filename"):
        yield payload
    for parte in payload.get("parts") or []:
        yield from _recorrer_partes(parte)


def buscar_adjuntos_zip(creds: Credentials, desde_fecha: str, buscar_en_spam: bool = True) -> list[dict]:
    """Devuelve un dict por cada adjunto `.zip` encontrado en mensajes desde
    `desde_fecha` (formato "YYYY-MM-DD"): `{"message_id", "attachment_id",
    "filename", "fecha_interna"}`. `fecha_interna` es el epoch en
    milisegundos que Gmail asigna al mensaje (`internalDate`), útil para
    actualizar `ultima_sincronizacion` al terminar."""
    servicio = _servicio(creds)
    query = _query(desde_fecha, buscar_en_spam)

    try:
        mensajes: list[dict] = []
        page_token = None
        while True:
            respuesta = servicio.users().messages().list(
                userId="me", q=query, pageToken=page_token, maxResults=500,
            ).execute()
            mensajes.extend(respuesta.get("messages", []))
            page_token = respuesta.get("nextPageToken")
            if not page_token:
                break

        adjuntos: list[dict] = []
        for mensaje in mensajes:
            detalle = servicio.users().messages().get(
                userId="me", id=mensaje["id"], format="full",
            ).execute()
            fecha_interna = detalle.get("internalDate")
            for parte in _recorrer_partes(detalle.get("payload", {})):
                nombre = parte.get("filename") or ""
                attachment_id = parte.get("body", {}).get("attachmentId")
                if nombre.lower().endswith(".zip") and attachment_id:
                    adjuntos.append({
                        "message_id": mensaje["id"],
                        "attachment_id": attachment_id,
                        "filename": nombre,
                        "fecha_interna": fecha_interna,
                    })
    except HttpError as e:
        raise GmailError(
            f"No se pudo leer Gmail: {e}. Puede que falte el permiso de Gmail en esta conexión o que se haya "
            "revocado -- conecta la cuenta de nuevo desde 'Configuración'."
        )
    return adjuntos


def descargar_adjunto(creds: Credentials, message_id: str, attachment_id: str) -> bytes:
    """Descarga los bytes de un adjunto puntual de un mensaje."""
    servicio = _servicio(creds)
    try:
        adjunto = servicio.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id,
        ).execute()
    except HttpError as e:
        raise GmailError(f"No se pudo descargar un adjunto de Gmail: {e}.")
    datos = adjunto["data"]
    return base64.urlsafe_b64decode(datos + "=" * (-len(datos) % 4))
