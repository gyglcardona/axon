"""
Pruebas de `src/backup_a_b2.py` -- el backup diario fuera del servidor (ver
docs/09-despliegue/plan-despliegue.md sección 5). No tocan la red real: los llamados
a `rclone` se interceptan y solo se verifica que se invoquen con las rutas y flags
correctos. `tar` sí corre de verdad (es rápido y así se prueba el paquete completo).
"""

import sqlite3
import subprocess
import sys
import tarfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import backup_a_b2  # noqa: E402


def _crear_db(ruta: Path, valor: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE t (v TEXT)")
    con.execute("INSERT INTO t VALUES (?)", (valor,))
    con.commit()
    con.close()


def test_respaldar_sqlite_copia_datos_correctamente(tmp_path):
    origen = tmp_path / "origen.db"
    destino = tmp_path / "sub" / "destino.db"
    _crear_db(origen, "hola")

    backup_a_b2._respaldar_sqlite(origen, destino)

    con = sqlite3.connect(destino)
    assert con.execute("SELECT v FROM t").fetchone() == ("hola",)
    con.close()


def test_armar_contenido_copia_dbs_y_archivos_planos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backup_a_b2, "DATA_DIR", Path("data"))
    monkeypatch.setattr(backup_a_b2, "CONFIG_DIR", Path("config"))

    _crear_db(Path("data/empresas/900111222.db"), "factura")
    Path("data/entrada-dian/hielo/2026/07").mkdir(parents=True)
    Path("data/entrada-dian/hielo/2026/07/algo.zip").write_bytes(b"contenido-zip")
    Path("config").mkdir()
    Path("config/registro.json").write_text('{"empresas": []}', encoding="utf-8")

    contenido = backup_a_b2._armar_contenido(tmp_path / "tmp_backup")

    con = sqlite3.connect(contenido / "data" / "empresas" / "900111222.db")
    assert con.execute("SELECT v FROM t").fetchone() == ("factura",)
    con.close()
    assert (contenido / "data" / "entrada-dian" / "hielo" / "2026" / "07" / "algo.zip").read_bytes() == b"contenido-zip"
    assert (contenido / "config" / "registro.json").is_file()


def test_empaquetar_genera_tar_gz_con_data_y_config(tmp_path):
    contenido = tmp_path / "contenido"
    (contenido / "data").mkdir(parents=True)
    (contenido / "data" / "x.txt").write_text("x")
    (contenido / "config").mkdir()
    (contenido / "config" / "y.txt").write_text("y")

    paquete = backup_a_b2._empaquetar(contenido, tmp_path, date(2026, 8, 12))

    assert paquete.name == "axon_backup_2026-08-12.tar.gz"
    with tarfile.open(paquete) as tar:
        nombres = tar.getnames()
    assert "data/x.txt" in nombres
    assert "config/y.txt" in nombres


def test_subir_copia_a_diario_y_a_mensual_solo_el_dia_1(monkeypatch, tmp_path):
    llamados = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, check: llamados.append(cmd) or subprocess.CompletedProcess(cmd, 0)
    )
    paquete = tmp_path / "axon_backup_2026-08-15.tar.gz"
    paquete.write_bytes(b"x")

    backup_a_b2._subir(paquete, date(2026, 8, 15))
    assert len(llamados) == 1
    assert llamados[0] == ["rclone", "copyto", str(paquete), "b2:axonweb-lat-backups/diario/axon_backup_2026-08-15.tar.gz"]

    llamados.clear()
    backup_a_b2._subir(paquete, date(2026, 8, 1))
    assert len(llamados) == 2
    assert llamados[1] == [
        "rclone", "copyto",
        "b2:axonweb-lat-backups/diario/axon_backup_2026-08-15.tar.gz",
        "b2:axonweb-lat-backups/mensual/axon_backup_2026-08-15.tar.gz",
    ]


def test_podar_antiguos_usa_la_retencion_configurada(monkeypatch):
    llamados = []
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, check: llamados.append(cmd) or subprocess.CompletedProcess(cmd, 0)
    )

    backup_a_b2._podar_antiguos()

    assert llamados == [
        ["rclone", "delete", "b2:axonweb-lat-backups/diario/", "--min-age", "30d"],
        ["rclone", "delete", "b2:axonweb-lat-backups/mensual/", "--min-age", "365d"],
    ]


def test_main_falla_claro_si_no_hay_data_o_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backup_a_b2, "DATA_DIR", Path("data"))
    monkeypatch.setattr(backup_a_b2, "CONFIG_DIR", Path("config"))

    import pytest
    with pytest.raises(SystemExit):
        backup_a_b2.main()


def test_main_end_to_end_arma_sube_y_limpia(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backup_a_b2, "DATA_DIR", Path("data"))
    monkeypatch.setattr(backup_a_b2, "CONFIG_DIR", Path("config"))

    _crear_db(Path("data/sistema.db"), "usuarios")
    Path("config").mkdir()
    Path("config/registro.json").write_text("{}", encoding="utf-8")

    llamados = []
    run_real = subprocess.run

    def run_falso(cmd, check):
        llamados.append(cmd)
        if cmd[0] == "tar":
            return run_real(cmd, check=check)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", run_falso)

    backup_a_b2.main()

    comandos = [c[0] for c in llamados]
    assert comandos.count("tar") == 1
    assert comandos.count("rclone") >= 2
    assert not list(Path("/tmp").glob(f"axon_backup_{date.today().isoformat()}"))
