"""
Pruebas de orquestador.importar_todo (el botón único de importación: local +
Drive + Gmail en una sola corrida) y orquestador.guardar_archivos_subidos
(subida de ZIP desde el navegador para el modo SaaS, donde quien importa no
tiene ni sabe de una ruta en el servidor). `ejecutar_importar` e
`importar_desde_google` se reemplazan por versiones falsas -- el pipeline
real de parseo ya está cubierto por tests/test_zip_handler.py y
tests/test_validar_completitud.py; acá solo se prueba la orquestación.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import google_conexiones  # noqa: E402
import orquestador  # noqa: E402
import state_store  # noqa: E402


@pytest.fixture
def empresa_configurada(tmp_path, monkeypatch):
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"empresa-test","nit":"900000000","nombre":"EMPRESA TEST"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")
    monkeypatch.setattr(orquestador, "ENTRADA_DIAN", tmp_path / "data" / "entrada-dian")
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", tmp_path / "data" / "empresas")

    original_conectar = state_store.conectar

    def _conectar_en_tmp(nit_empresa, base_dir=None):
        return original_conectar(nit_empresa, base_dir=tmp_path / "data" / "empresas")

    monkeypatch.setattr(state_store, "conectar", _conectar_en_tmp)
    return "empresa-test", tmp_path


def _resultado_importar_fijo(nuevas=0):
    return {
        "empresa": "EMPRESA TEST", "nit": "900000000", "carpeta": "x",
        "nuevas": nuevas, "ya_existentes": 0, "duplicados": 0, "no_facturas": 0, "con_error": 0,
    }


# --- importar_todo ---

def test_importar_todo_sin_nada_da_todo_en_cero_sin_reventar(empresa_configurada):
    slug, _ = empresa_configurada
    resultado = orquestador.importar_todo(slug)

    assert resultado["drive"] is None
    assert resultado["gmail"] is None
    assert resultado["error_google"] is None
    assert resultado["local"]["nuevas"] == 0
    assert resultado["local"]["ya_existentes"] == 0


def test_importar_todo_corre_pasada_local_completa_del_arbol(empresa_configurada, monkeypatch):
    slug, tmp_path = empresa_configurada
    (tmp_path / "data" / "entrada-dian" / slug).mkdir(parents=True)
    llamadas = []
    monkeypatch.setattr(orquestador, "ejecutar_importar", lambda s, c: llamadas.append((s, c)) or _resultado_importar_fijo(5))

    resultado = orquestador.importar_todo(slug)

    assert llamadas == [(slug, ".")]
    assert resultado["local"]["nuevas"] == 5
    assert resultado["drive"] is None
    assert resultado["gmail"] is None


def test_importar_todo_con_drive_configurado_lo_sincroniza_y_tambien_hace_pasada_local(empresa_configurada, monkeypatch):
    slug, tmp_path = empresa_configurada
    orquestador.guardar_conexion_drive(slug, "CARPETA-ID")
    (tmp_path / "data" / "entrada-dian" / slug).mkdir(parents=True)

    monkeypatch.setattr(orquestador, "importar_desde_google", lambda s: {
        "drive": {**_resultado_importar_fijo(2), "descargados": 2, "ya_estaban_localmente": 0}, "gmail": None,
    })
    monkeypatch.setattr(orquestador, "ejecutar_importar", lambda s, c: _resultado_importar_fijo(0))

    resultado = orquestador.importar_todo(slug)

    assert resultado["drive"]["nuevas"] == 2
    assert resultado["gmail"] is None
    assert resultado["error_google"] is None
    assert resultado["local"]["nuevas"] == 0  # ya las contó la pasada de Drive, no se duplican


def test_importar_todo_error_de_google_no_bloquea_la_pasada_local(empresa_configurada, monkeypatch):
    """Si el token de Google está vencido (o cualquier otro fallo), el botón
    único igual debe importar lo que ya esté localmente en vez de fallar
    todo el intento."""
    slug, tmp_path = empresa_configurada
    orquestador.guardar_conexion_drive(slug, "CARPETA-ID")
    (tmp_path / "data" / "entrada-dian" / slug).mkdir(parents=True)

    def _falla(s):
        raise google_conexiones.GoogleConexionError("El token ya no es válido.")

    monkeypatch.setattr(orquestador, "importar_desde_google", _falla)
    monkeypatch.setattr(orquestador, "ejecutar_importar", lambda s, c: _resultado_importar_fijo(1))

    resultado = orquestador.importar_todo(slug)

    assert resultado["error_google"] == "El token ya no es válido."
    assert resultado["drive"] is None
    assert resultado["local"]["nuevas"] == 1  # la pasada local igual corrió


def test_importar_todo_empresa_inexistente_da_error_claro(empresa_configurada):
    with pytest.raises(orquestador.EmpresaNoEncontrada):
        orquestador.importar_todo("no-existe")


# --- guardar_archivos_subidos ---

def test_guardar_archivos_subidos_los_deja_en_una_carpeta_propia(empresa_configurada):
    slug, tmp_path = empresa_configurada

    resultado = orquestador.guardar_archivos_subidos(slug, [
        ("factura1.zip", b"contenido-1"), ("factura2.zip", b"contenido-2"),
    ])

    carpeta = Path(resultado["carpeta"])
    assert carpeta.parent == tmp_path / "data" / "entrada-dian" / slug / "subidos"
    assert sorted(resultado["archivos"]) == ["factura1.zip", "factura2.zip"]
    assert (carpeta / "factura1.zip").read_bytes() == b"contenido-1"
    assert (carpeta / "factura2.zip").read_bytes() == b"contenido-2"


def test_guardar_archivos_subidos_sanea_nombres_con_ruta(empresa_configurada):
    """El navegador no debería mandar nunca un '..' en el nombre de archivo,
    pero si algo lo manda (o alguien lo fuerza a mano contra la API), nunca
    se debe poder escribir fuera de la carpeta de subidos -- los demás
    segmentos de la ruta sí se conservan (ver _ruta_relativa_segura): desde
    que existe el selector de carpeta, esos segmentos son subcarpetas
    legítimas del árbol que el usuario subió, no basura a descartar."""
    slug, tmp_path = empresa_configurada

    resultado = orquestador.guardar_archivos_subidos(slug, [
        ("../../../etc/malicioso.zip", b"contenido"),
        ("..\\..\\config\\sistema\\malicioso2.zip", b"contenido"),
    ])

    carpeta = Path(resultado["carpeta"])
    rutas = {Path(r) for r in resultado["archivos"]}
    assert rutas == {Path("etc", "malicioso.zip"), Path("config", "sistema", "malicioso2.zip")}
    assert (carpeta / "etc" / "malicioso.zip").is_file()
    assert (carpeta / "config" / "sistema" / "malicioso2.zip").is_file()
    # nada se escribió fuera de la carpeta de subidos
    assert not (tmp_path / "etc").exists()
    assert not (tmp_path / "config" / "sistema" / "malicioso2.zip").exists()


def test_guardar_archivos_subidos_ruta_solo_con_puntos_se_descarta(empresa_configurada):
    slug, _ = empresa_configurada

    resultado = orquestador.guardar_archivos_subidos(slug, [("../../..", b"contenido")])

    assert resultado["archivos"] == []


def test_guardar_archivos_subidos_preserva_subcarpetas_del_picker_de_carpeta(empresa_configurada):
    """El selector de carpeta (<input webkitdirectory>) manda
    webkitRelativePath con subcarpetas, ej. 'MiCarpeta/2026/07/factura.zip'
    -- deben preservarse (no aplanarse a solo el nombre) para no chocar
    archivos con el mismo nombre que vivían en meses distintos."""
    slug, tmp_path = empresa_configurada

    resultado = orquestador.guardar_archivos_subidos(slug, [
        ("MiCarpeta/2026/07/factura.zip", b"contenido-julio"),
        ("MiCarpeta/2026/08/factura.zip", b"contenido-agosto"),
    ])

    carpeta = Path(resultado["carpeta"])
    assert (carpeta / "MiCarpeta" / "2026" / "07" / "factura.zip").read_bytes() == b"contenido-julio"
    assert (carpeta / "MiCarpeta" / "2026" / "08" / "factura.zip").read_bytes() == b"contenido-agosto"


def test_guardar_archivos_subidos_empresa_inexistente_da_error_claro(empresa_configurada):
    with pytest.raises(orquestador.EmpresaNoEncontrada):
        orquestador.guardar_archivos_subidos("no-existe", [("a.zip", b"x")])
