"""
Pruebas de `orquestador._ruta_desde_archivo_origen`: bug real confirmado en
producción (agosto 2026) -- las bases de datos se poblaron en Windows, donde
`compras.archivo_origen` queda guardado con "\\" como separador (ej.
"data\\entrada-dian\\hielo-super-cool\\...\\algo.zip"). Al migrar esas mismas
bases a un servidor Linux, `Path("data\\entrada-dian\\...")` interpreta todo
el string como UN SOLO nombre de archivo (en POSIX "\\" no separa carpetas),
así que `obtener_pdf`/`_extraer_tercero_de_origen` nunca encontraban el
archivo aunque sí existiera en disco -- "No se encontró un PDF adjunto para
esta factura" incluso teniendo el PDF ahí mismo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orquestador  # noqa: E402


def test_normaliza_separadores_de_windows():
    ruta = orquestador._ruta_desde_archivo_origen(
        "data\\entrada-dian\\hielo-super-cool\\2026\\05\\algo.zip"
    )
    assert ruta == Path("data/entrada-dian/hielo-super-cool/2026/05/algo.zip")


def test_no_afecta_rutas_ya_con_separador_posix():
    ruta = orquestador._ruta_desde_archivo_origen(
        "data/entrada-dian/hielo-super-cool/2026/05/algo.zip"
    )
    assert ruta == Path("data/entrada-dian/hielo-super-cool/2026/05/algo.zip")


def test_encuentra_el_archivo_real_con_ruta_estilo_windows(tmp_path, monkeypatch):
    """Reproduce el bug de punta a punta: un archivo real en disco, referido
    con separadores de Windows, debe encontrarse igual en Linux."""
    carpeta = tmp_path / "entrada-dian" / "empresa-test"
    carpeta.mkdir(parents=True)
    archivo = carpeta / "factura.xml"
    archivo.write_bytes(b"contenido")

    ruta_estilo_windows = str(archivo).replace("/", "\\")
    ruta = orquestador._ruta_desde_archivo_origen(ruta_estilo_windows)

    assert ruta.is_file()
    assert ruta.read_bytes() == b"contenido"
