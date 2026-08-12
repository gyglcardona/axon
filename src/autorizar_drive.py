"""
Paso manual de configuración inicial -- se corre UNA sola vez, a mano, con
la cuenta de Gmail donde el usuario recibe las carpetas compartidas de cada
empresa (ver docs/03-ingesta-dian/importar-desde-drive.md para los pasos
previos en Google Cloud Console). Nunca lo dispara el backend web ni ningún
flujo automático -- abre el navegador para que el usuario inicie sesión y
acepte el permiso de solo lectura sobre Drive, y guarda el token de
refresco en `config/google/token.json` (gitignored). Ese token sirve para
todas las empresas -- no hay que repetir esto por cada una.

Uso: python src/autorizar_drive.py
Requiere: config/google/client_secret.json (descargado de Google Cloud
Console, tipo de credencial "App de escritorio").
"""

from __future__ import annotations

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
CLIENT_SECRET_PATH = Path("config/google/client_secret.json")
TOKEN_PATH = Path("config/google/token.json")


def main() -> None:
    if not CLIENT_SECRET_PATH.is_file():
        raise SystemExit(
            f"No existe '{CLIENT_SECRET_PATH}' -- descárgalo desde Google Cloud "
            "Console (Credenciales > ID de cliente de OAuth > App de escritorio) "
            "antes de correr este script. Ver docs/03-ingesta-dian/"
            "importar-desde-drive.md."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"Listo -- token guardado en '{TOKEN_PATH}'. Ya puedes usar 'Importar desde Drive' en AXON.")


if __name__ == "__main__":
    main()
