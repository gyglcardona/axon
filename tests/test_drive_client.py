"""
Pruebas de src/drive_client.py: recorrido recursivo del árbol de una carpeta
de Drive y descarga de archivos. Nunca toca la red real -- se reemplaza
`_servicio()`/`_listar_hijos()` por versiones falsas y se inyecta un `creds`
de prueba (este módulo ya no se autentica solo, ver
src/google_conexiones.py); lo que se prueba es la lógica propia de este
módulo (recursión, filtrado por extensión, construcción de `ruta_relativa`),
no el cliente de Google (ya probado por la propia librería).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import drive_client  # noqa: E402

_CREDS_FALSAS = object()


class _FakeResp:
    status = 403
    reason = "Forbidden"


class _FakeServicio:
    def files(self):
        return self

    def get_media(self, fileId):
        return f"REQUEST-{fileId}"


def test_listar_arbol_carpeta_plana(monkeypatch):
    monkeypatch.setattr(drive_client, "_servicio", lambda creds: _FakeServicio())
    monkeypatch.setattr(drive_client, "_listar_hijos", lambda servicio, carpeta_id: [
        {"id": "f1", "name": "factura1.zip", "mimeType": "application/zip"},
        {"id": "f2", "name": "factura2.xml", "mimeType": "text/xml"},
    ])

    resultado = drive_client.listar_arbol("RAIZ", _CREDS_FALSAS)

    assert {a["ruta_relativa"] for a in resultado} == {"factura1.zip", "factura2.xml"}
    assert {a["id"] for a in resultado} == {"f1", "f2"}


def test_listar_arbol_recorre_subcarpetas_anidadas(monkeypatch):
    """Caso real: cada empresa organiza su carpeta compartida como quiera --
    acá con año/mes anidado y un archivo suelto en la raíz, para confirmar
    que ambos patrones conviven sin que se le exija una convención a nadie."""
    monkeypatch.setattr(drive_client, "_servicio", lambda creds: _FakeServicio())

    arbol = {
        "RAIZ": [
            {"id": "c2026", "name": "2026", "mimeType": drive_client.CARPETA_MIME},
            {"id": "suelto", "name": "suelto.zip", "mimeType": "application/zip"},
        ],
        "c2026": [
            {"id": "c07", "name": "07", "mimeType": drive_client.CARPETA_MIME},
        ],
        "c07": [
            {"id": "f1", "name": "julio.zip", "mimeType": "application/zip"},
        ],
    }
    monkeypatch.setattr(drive_client, "_listar_hijos", lambda servicio, carpeta_id: arbol.get(carpeta_id, []))

    resultado = drive_client.listar_arbol("RAIZ", _CREDS_FALSAS)
    por_ruta = {a["ruta_relativa"]: a["id"] for a in resultado}

    assert por_ruta == {"suelto.zip": "suelto", "2026/07/julio.zip": "f1"}


def test_listar_arbol_ignora_archivos_no_zip_ni_xml(monkeypatch):
    monkeypatch.setattr(drive_client, "_servicio", lambda creds: _FakeServicio())
    monkeypatch.setattr(drive_client, "_listar_hijos", lambda servicio, carpeta_id: [
        {"id": "p1", "name": "factura.pdf", "mimeType": "application/pdf"},
        {"id": "x1", "name": "vale.xml", "mimeType": "text/xml"},
    ])

    resultado = drive_client.listar_arbol("RAIZ", _CREDS_FALSAS)

    assert [a["name"] for a in resultado] == ["vale.xml"]


def test_descargar_archivo(monkeypatch):
    monkeypatch.setattr(drive_client, "_servicio", lambda creds: _FakeServicio())

    class _FakeDownloader:
        def __init__(self, buffer, request):
            self._buffer = buffer

        def next_chunk(self):
            self._buffer.write(b"contenido-zip-falso")
            return None, True

    monkeypatch.setattr(drive_client, "MediaIoBaseDownload", _FakeDownloader)

    resultado = drive_client.descargar_archivo("f1", _CREDS_FALSAS)

    assert resultado == b"contenido-zip-falso"


def test_listar_arbol_error_real_de_google_se_convierte_en_driveerror(monkeypatch):
    """Bug real confirmado en vivo: un HttpError crudo de googleapiclient
    (ej. permisos insuficientes, carpeta ya no compartida) no estaba
    envuelto en DriveError -- se colaba como un 500 sin mensaje útil hasta
    el endpoint de Flask."""
    monkeypatch.setattr(drive_client, "_servicio", lambda creds: _FakeServicio())

    def _falla(servicio, carpeta_id):
        raise drive_client.HttpError(_FakeResp(), b'{"error": "insufficient permissions"}')

    monkeypatch.setattr(drive_client, "_listar_hijos", _falla)

    with pytest.raises(drive_client.DriveError, match="No se pudo leer la carpeta de Drive"):
        drive_client.listar_arbol("RAIZ", _CREDS_FALSAS)


def test_descargar_archivo_error_real_de_google_se_convierte_en_driveerror(monkeypatch):
    monkeypatch.setattr(drive_client, "_servicio", lambda creds: _FakeServicio())

    class _FakeDownloaderFalla:
        def __init__(self, buffer, request):
            pass

        def next_chunk(self):
            raise drive_client.HttpError(_FakeResp(), b"error")

    monkeypatch.setattr(drive_client, "MediaIoBaseDownload", _FakeDownloaderFalla)

    with pytest.raises(drive_client.DriveError, match="No se pudo descargar"):
        drive_client.descargar_archivo("f1", _CREDS_FALSAS)
