"""
Backup diario fuera del servidor -- pensado para correr por cron/systemd timer en el
VPS de producción. Empaqueta un respaldo completo de `data/` (todas las bases SQLite
por empresa + `sistema.db` + los XML/ZIP/PDF originales de la DIAN en
`entrada-dian/`) y `config/` (credenciales y configuración por empresa), y lo sube a
un bucket privado de Backblaze B2 vía `rclone` -- remoto "b2", configurado aparte en
`~/.config/rclone/rclone.conf` del usuario que corre el cron (nunca en este repo, ver
regla 4 de CLAUDE.md). Ver docs/09-despliegue/plan-despliegue.md sección 5.

Las bases SQLite se copian con la API de backup de sqlite3 (`Connection.backup`, con
reintentos si la base está momentáneamente ocupada) -- nunca una copia de archivo
directa, que podría capturar una escritura a medias mientras gunicorn sigue
atendiendo peticiones. https://www.sqlite.org/backup.html

Retención: 30 respaldos diarios + 12 mensuales (el que cae el día 1 de cada mes se
conserva en una carpeta aparte). Se poda lo vencido en cada corrida -- no hace falta
un cron separado para eso.

Uso: python src/backup_a_b2.py   (desde la raíz del repo, con rclone ya configurado)
"""

from __future__ import annotations

import datetime
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

RCLONE_REMOTE = "b2:axonweb-lat-backups"
DATA_DIR = Path("data")
CONFIG_DIR = Path("config")
RETENCION_DIARIA_DIAS = 30
RETENCION_MENSUAL_DIAS = 365


def _respaldar_sqlite(origen: Path, destino: Path, intentos: int = 5) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    ultimo_error: sqlite3.OperationalError | None = None
    for intento in range(intentos):
        con_origen = sqlite3.connect(f"file:{origen}?mode=ro", uri=True)
        con_destino = sqlite3.connect(destino)
        try:
            con_origen.backup(con_destino)
            return
        except sqlite3.OperationalError as e:
            ultimo_error = e
            time.sleep(2**intento)
        finally:
            con_destino.close()
            con_origen.close()
    raise RuntimeError(f"No se pudo respaldar '{origen}' tras {intentos} intentos: {ultimo_error}")


def _armar_contenido(carpeta_tmp: Path) -> Path:
    """Arma en `carpeta_tmp/contenido` una copia consistente de data/ (bases vía
    backup de sqlite3, el resto por copia directa) + config/."""
    contenido = carpeta_tmp / "contenido"
    destino_data = contenido / "data"
    for archivo in DATA_DIR.rglob("*"):
        if archivo.is_dir():
            continue
        relativa = archivo.relative_to(DATA_DIR)
        destino = destino_data / relativa
        if archivo.suffix == ".db":
            _respaldar_sqlite(archivo, destino)
        else:
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archivo, destino)

    shutil.copytree(CONFIG_DIR, contenido / "config")
    return contenido


def _empaquetar(contenido: Path, carpeta_tmp: Path, fecha: datetime.date) -> Path:
    paquete = carpeta_tmp / f"axon_backup_{fecha.isoformat()}.tar.gz"
    subprocess.run(
        ["tar", "-czf", str(paquete), "-C", str(contenido), "data", "config"],
        check=True,
    )
    return paquete


def _subir(paquete: Path, fecha: datetime.date) -> None:
    destino_diario = f"{RCLONE_REMOTE}/diario/{paquete.name}"
    subprocess.run(["rclone", "copyto", str(paquete), destino_diario], check=True)

    if fecha.day == 1:
        destino_mensual = f"{RCLONE_REMOTE}/mensual/{paquete.name}"
        subprocess.run(["rclone", "copyto", destino_diario, destino_mensual], check=True)


def _podar_antiguos() -> None:
    subprocess.run(
        ["rclone", "delete", f"{RCLONE_REMOTE}/diario/", "--min-age", f"{RETENCION_DIARIA_DIAS}d"],
        check=True,
    )
    subprocess.run(
        ["rclone", "delete", f"{RCLONE_REMOTE}/mensual/", "--min-age", f"{RETENCION_MENSUAL_DIAS}d"],
        check=True,
    )


def _log(mensaje: str) -> None:
    print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {mensaje}", flush=True)


def main() -> None:
    if not DATA_DIR.is_dir() or not CONFIG_DIR.is_dir():
        raise SystemExit(
            f"No existe '{DATA_DIR.resolve()}' o '{CONFIG_DIR.resolve()}' -- "
            "corre este script desde la raíz del repo (ver WorkingDirectory en axon.service)."
        )

    fecha = datetime.date.today()
    carpeta_tmp = Path(f"/tmp/axon_backup_{fecha.isoformat()}")
    if carpeta_tmp.exists():
        shutil.rmtree(carpeta_tmp)
    carpeta_tmp.mkdir(parents=True)

    try:
        contenido = _armar_contenido(carpeta_tmp)
        paquete = _empaquetar(contenido, carpeta_tmp, fecha)
        tamano_mb = paquete.stat().st_size / 1_000_000
        _log(f"Paquete armado: {paquete.name} ({tamano_mb:.1f} MB)")

        _subir(paquete, fecha)
        _log(f"Subido a {RCLONE_REMOTE}/diario/" + (" y a mensual/ (día 1)" if fecha.day == 1 else ""))

        _podar_antiguos()
        _log(f"Retención aplicada (diaria {RETENCION_DIARIA_DIAS}d, mensual {RETENCION_MENSUAL_DIAS}d)")
    finally:
        shutil.rmtree(carpeta_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
