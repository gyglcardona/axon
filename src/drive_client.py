"""
Cliente real contra la API de Google Drive (solo lectura) -- trae el árbol de
archivos de una carpeta compartida y descarga bytes de un archivo puntual.

Recibe las credenciales ya resueltas (`google_conexiones.obtener_credenciales`)
en vez de autenticarse por su cuenta -- así una empresa puede usar una
conexión de Google distinta a otra sin que este módulo tenga que saber nada
de conexiones ni de qué empresa está pidiendo el árbol (ver
`orquestador.importar_desde_drive`).
"""

from __future__ import annotations

import io

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

CARPETA_MIME = "application/vnd.google-apps.folder"
EXTENSIONES_VALIDAS = (".zip", ".xml")


class DriveError(Exception):
    """Cualquier fallo hablando con Google Drive -- carpeta_id inválido, sin
    red, etc. Mensaje listo para mostrar al usuario."""


def _servicio(creds: Credentials):
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _listar_hijos(servicio, carpeta_id: str) -> list[dict]:
    """Una carpeta puede tener más de 1000 archivos -- pagina con
    `pageToken` hasta agotar los resultados."""
    hijos: list[dict] = []
    page_token = None
    while True:
        respuesta = servicio.files().list(
            q=f"'{carpeta_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        hijos.extend(respuesta.get("files", []))
        page_token = respuesta.get("nextPageToken")
        if not page_token:
            break
    return hijos


def listar_arbol(carpeta_id: str, creds: Credentials) -> list[dict]:
    """Recorre `carpeta_id` recursivamente (la estructura de subcarpetas de
    cada empresa no se puede asumir -- ver docs/03-ingesta-dian/importar-
    desde-drive.md) y devuelve un archivo por cada `.zip`/`.xml` encontrado a
    cualquier profundidad: `{"id", "name", "ruta_relativa"}`, donde
    `ruta_relativa` refleja las subcarpetas tal como están en Drive (ej.
    "2026/07/archivo.zip", o solo "archivo.zip" si está suelto)."""
    servicio = _servicio(creds)

    archivos: list[dict] = []

    def _recorrer(id_actual: str, prefijo: str) -> None:
        for item in _listar_hijos(servicio, id_actual):
            nombre = item["name"]
            if item["mimeType"] == CARPETA_MIME:
                _recorrer(item["id"], f"{prefijo}{nombre}/")
            elif nombre.lower().endswith(EXTENSIONES_VALIDAS):
                archivos.append({
                    "id": item["id"],
                    "name": nombre,
                    "ruta_relativa": f"{prefijo}{nombre}",
                })

    try:
        _recorrer(carpeta_id, "")
    except HttpError as e:
        raise DriveError(
            f"No se pudo leer la carpeta de Drive: {e}. Puede que el id de la carpeta esté mal, que ya "
            "no se comparta con esta cuenta, o que la conexión haya perdido permisos -- revísala en 'Configuración'."
        )
    return archivos


def descargar_archivo(file_id: str, creds: Credentials) -> bytes:
    """Descarga el contenido de un archivo por su id de Drive."""
    servicio = _servicio(creds)
    buffer = io.BytesIO()
    request = servicio.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(buffer, request)
    try:
        listo = False
        while not listo:
            _, listo = downloader.next_chunk()
    except HttpError as e:
        raise DriveError(f"No se pudo descargar un archivo de Drive: {e}.")
    return buffer.getvalue()
