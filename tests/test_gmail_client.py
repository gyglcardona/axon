"""
Pruebas de src/gmail_client.py: query de búsqueda, recorrido de adjuntos
anidados, y decodificación de un adjunto descargado. Nunca toca la red real
-- se reemplaza `_servicio()` por una versión falsa con las respuestas de
Gmail ya armadas a mano.
"""

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import gmail_client  # noqa: E402

_CREDS_FALSAS = object()


class _FakeResp:
    status = 403
    reason = "Forbidden"


class _FakeMessages:
    def __init__(self, lista_mensajes, detalles_por_id):
        self._lista_mensajes = lista_mensajes
        self._detalles_por_id = detalles_por_id
        self.queries_recibidas = []

    def list(self, userId, q, pageToken=None, maxResults=None):
        self.queries_recibidas.append(q)
        return self

    def get(self, userId, id, format):
        return _FakeEjecutable(self._detalles_por_id[id])

    def execute(self):
        return {"messages": self._lista_mensajes}


class _FakeEjecutable:
    def __init__(self, valor):
        self._valor = valor

    def execute(self):
        return self._valor


def test_query_incluye_spam_por_defecto():
    query = gmail_client._query("2026-01-15", buscar_en_spam=True)
    assert query == "after:2026/01/15 filename:zip (in:inbox OR in:spam)"


def test_query_sin_spam():
    query = gmail_client._query("2026-01-15", buscar_en_spam=False)
    assert query == "after:2026/01/15 filename:zip in:inbox"


def test_recorrer_partes_encuentra_zip_anidado():
    payload = {
        "parts": [
            {"mimeType": "text/plain", "body": {}},
            {
                "mimeType": "multipart/mixed",
                "parts": [
                    {"filename": "factura.zip", "body": {"attachmentId": "att-1"}},
                    {"filename": "logo.png", "body": {"attachmentId": "att-2"}},
                ],
            },
        ],
    }
    encontrados = list(gmail_client._recorrer_partes(payload))
    nombres = {p["filename"] for p in encontrados}
    assert nombres == {"factura.zip", "logo.png"}


def test_buscar_adjuntos_zip_ignora_adjuntos_no_zip(monkeypatch):
    lista_mensajes = [{"id": "m1"}]
    detalles = {
        "m1": {
            "internalDate": "1700000000000",
            "payload": {
                "parts": [
                    {"filename": "factura.zip", "body": {"attachmentId": "att-1"}},
                    {"filename": "logo.png", "body": {"attachmentId": "att-2"}},
                ],
            },
        },
    }

    class _Users:
        def messages(self_inner):
            return _FakeMessages(lista_mensajes, detalles)

    class _Servicio:
        def users(self_inner):
            return _Users()

    monkeypatch.setattr(gmail_client, "_servicio", lambda creds: _Servicio())

    adjuntos = gmail_client.buscar_adjuntos_zip(_CREDS_FALSAS, "2026-01-01", buscar_en_spam=True)

    assert len(adjuntos) == 1
    assert adjuntos[0] == {
        "message_id": "m1", "attachment_id": "att-1",
        "filename": "factura.zip", "fecha_interna": "1700000000000",
    }


def test_descargar_adjunto_decodifica_base64url(monkeypatch):
    contenido_real = b"contenido-zip-de-verdad"
    datos_b64 = base64.urlsafe_b64encode(contenido_real).decode("ascii").rstrip("=")

    class _Attachments:
        def get(self_inner, userId, messageId, id):
            assert messageId == "m1" and id == "att-1"
            return _FakeEjecutable({"data": datos_b64})

    class _Users:
        def messages(self_inner):
            return self_inner

        def attachments(self_inner):
            return _Attachments()

    class _Servicio:
        def users(self_inner):
            return _Users()

    monkeypatch.setattr(gmail_client, "_servicio", lambda creds: _Servicio())

    resultado = gmail_client.descargar_adjunto(_CREDS_FALSAS, "m1", "att-1")

    assert resultado == contenido_real


def test_buscar_adjuntos_zip_error_real_de_google_se_convierte_en_gmailerror(monkeypatch):
    """Bug real confirmado en vivo: una cuenta con el scope de Gmail
    insuficiente (o revocado) hacía que googleapiclient.errors.HttpError se
    colara crudo hasta el endpoint de Flask como un 500 sin mensaje útil."""
    class _MessagesFalla:
        def list(self_inner, userId, q, pageToken=None, maxResults=None):
            return self_inner

        def execute(self_inner):
            raise gmail_client.HttpError(_FakeResp(), b'{"error": "insufficient scope"}')

    class _Users:
        def messages(self_inner):
            return _MessagesFalla()

    class _Servicio:
        def users(self_inner):
            return _Users()

    monkeypatch.setattr(gmail_client, "_servicio", lambda creds: _Servicio())

    with pytest.raises(gmail_client.GmailError, match="No se pudo leer Gmail"):
        gmail_client.buscar_adjuntos_zip(_CREDS_FALSAS, "2026-01-01", buscar_en_spam=True)


def test_descargar_adjunto_error_real_de_google_se_convierte_en_gmailerror(monkeypatch):
    class _AttachmentsFalla:
        def get(self_inner, userId, messageId, id):
            return self_inner

        def execute(self_inner):
            raise gmail_client.HttpError(_FakeResp(), b"error")

    class _Users:
        def messages(self_inner):
            return self_inner

        def attachments(self_inner):
            return _AttachmentsFalla()

    class _Servicio:
        def users(self_inner):
            return _Users()

    monkeypatch.setattr(gmail_client, "_servicio", lambda creds: _Servicio())

    with pytest.raises(gmail_client.GmailError, match="No se pudo descargar"):
        gmail_client.descargar_adjunto(_CREDS_FALSAS, "m1", "att-1")
