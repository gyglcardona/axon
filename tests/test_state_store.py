"""
Pruebas de state_store.py: que lo que guarda una corrida de importación se
pueda leer de vuelta tal como se esperaría, y que no se puedan colar CUFE
duplicados en la base (la deduplicación de zip_handler es dentro de una
corrida; esta es la protección a nivel de base de datos, entre corridas).
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dian_parser import parsear_factura  # noqa: E402
from motor_reglas import clasificar_factura  # noqa: E402
from zip_handler import DocumentoDuplicado, DocumentoConError, DocumentoNoFactura  # noqa: E402
import state_store  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures-sinteticos"
CONFIG_DIR = Path(__file__).parent.parent / "config"


def _resultado_clasificado(cufe: str):
    factura = parsear_factura((FIXTURES / "iva-no-discriminado-hielo-super-cool.xml").read_bytes())
    factura.cufe = cufe  # el fixture sintético no trae CUFE real
    return clasificar_factura(factura, nit_empresa="901528790", config_dir=CONFIG_DIR)


def test_guardar_y_leer_una_compra(tmp_path):
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    resultado = _resultado_clasificado("CUFE-TEST-001")

    compra_id = state_store.guardar_resultado(conn, resultado, archivo_origen=Path("data/entrada-dian/x.zip"))

    fila = conn.execute("SELECT cufe, resuelto_por, total_pagar_xml FROM compras WHERE id = ?", (compra_id,)).fetchone()
    assert fila == ("CUFE-TEST-001", "manual", 2380000)

    items = conn.execute(
        "SELECT descripcion, origen FROM detalle_compras WHERE compra_id = ? ORDER BY orden", (compra_id,)
    ).fetchall()
    assert len(items) == 2  # línea original + ítem de IVA no discriminado inyectado por la política
    assert items[1][1] == "politica_empresa"

    impuestos = conn.execute(
        """SELECT ti.valor FROM detalle_impuestos ti
           JOIN detalle_compras dc ON dc.id = ti.detalle_compra_id
           WHERE dc.compra_id = ?""",
        (compra_id,),
    ).fetchall()
    assert impuestos == []  # la política mueve el IVA a un ítem, sin impuestos asociados


def test_no_permite_cufe_duplicado_entre_corridas(tmp_path):
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    state_store.guardar_resultado(conn, _resultado_clasificado("CUFE-REPETIDO"), Path("uno.zip"))

    assert state_store.ya_existe_cufe(conn, "CUFE-REPETIDO") is True

    with pytest.raises(sqlite3.IntegrityError):
        state_store.guardar_resultado(conn, _resultado_clasificado("CUFE-REPETIDO"), Path("dos.zip"))


def test_bases_de_datos_separadas_por_empresa(tmp_path):
    """No debe existir ninguna forma de que una empresa vea datos de otra --
    ver docs/06-multiempresa-saas/aislamiento-datos.md."""
    conn_a = state_store.conectar("111111111", base_dir=tmp_path)
    conn_b = state_store.conectar("222222222", base_dir=tmp_path)

    state_store.guardar_resultado(conn_a, _resultado_clasificado("SOLO-EMPRESA-A"), Path("a.zip"))

    assert state_store.ya_existe_cufe(conn_a, "SOLO-EMPRESA-A") is True
    assert state_store.ya_existe_cufe(conn_b, "SOLO-EMPRESA-A") is False


def test_registrar_descartado_duplicado_y_error(tmp_path):
    conn = state_store.conectar("901528790", base_dir=tmp_path)

    state_store.registrar_descartado(conn, DocumentoDuplicado(
        cufe="CUFE-X", origen=Path("dos.zip"), origen_primera_aparicion=Path("uno.zip"),
    ))
    state_store.registrar_descartado(conn, DocumentoConError(
        origen=Path("raro.xml"), motivo="No se encontró CUFE",
    ))
    state_store.registrar_descartado(conn, DocumentoNoFactura(
        origen=Path("acuse.xml"), tipo="ApplicationResponse",
    ))

    filas = conn.execute("SELECT tipo, cufe FROM documentos_descartados ORDER BY id").fetchall()
    assert filas == [("duplicado", "CUFE-X"), ("error", None), ("no_es_factura", None)]


def test_migracion_agrega_columnas_a_base_ya_existente(tmp_path):
    """Simula una base creada antes de esta feature (sin las columnas nuevas
    de tipo_comprobante_id/medio_pago_id/iva_tax_id/retencion_tax_id) --
    conectar() debe agregarlas sin error y sin perder los datos ya guardados
    (ver state_store._migrar)."""
    db_path = tmp_path / "999999999.db"
    conn_vieja = sqlite3.connect(db_path)
    conn_vieja.execute("""
        CREATE TABLE compras (
            id INTEGER PRIMARY KEY, cufe TEXT NOT NULL UNIQUE, numero_factura TEXT,
            resuelto_por TEXT NOT NULL, estado_siigo TEXT NOT NULL DEFAULT 'pendiente', creado_en TEXT NOT NULL
        )
    """)
    conn_vieja.execute("""
        CREATE TABLE detalle_compras (
            id INTEGER PRIMARY KEY, compra_id INTEGER NOT NULL, orden INTEGER NOT NULL,
            descripcion TEXT, cuenta_contable TEXT
        )
    """)
    conn_vieja.execute(
        "INSERT INTO compras (cufe, resuelto_por, creado_en) VALUES ('CUFE-VIEJO', 'manual', '2026-01-01')"
    )
    conn_vieja.commit()
    conn_vieja.close()

    conn = state_store.conectar("999999999", base_dir=tmp_path)

    columnas_compras = {f[1] for f in conn.execute("PRAGMA table_info(compras)")}
    assert {"tipo_comprobante_id", "medio_pago_id"} <= columnas_compras
    columnas_detalle = {f[1] for f in conn.execute("PRAGMA table_info(detalle_compras)")}
    assert {"iva_tax_id", "retencion_tax_id"} <= columnas_detalle

    fila = conn.execute("SELECT cufe, tipo_comprobante_id FROM compras WHERE cufe = 'CUFE-VIEJO'").fetchone()
    assert fila == ("CUFE-VIEJO", None)

    # conectar() otra vez (ej. otra petición) no debe fallar -- idempotente.
    state_store.conectar("999999999", base_dir=tmp_path)


def test_eliminar_compras_borra_factura_detalle_e_impuestos(tmp_path):
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    compra_id = state_store.guardar_resultado(conn, _resultado_clasificado("CUFE-BORRAR"), Path("x.zip"))

    total = state_store.eliminar_compras(conn, ["CUFE-BORRAR"])

    assert total == 1
    assert conn.execute("SELECT 1 FROM compras WHERE id = ?", (compra_id,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM detalle_compras WHERE compra_id = ?", (compra_id,)).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM detalle_impuestos WHERE detalle_compra_id IN "
        "(SELECT id FROM detalle_compras WHERE compra_id = ?)", (compra_id,)
    ).fetchone() is None


def test_eliminar_compras_no_afecta_otras_facturas(tmp_path):
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    state_store.guardar_resultado(conn, _resultado_clasificado("CUFE-A"), Path("a.zip"))
    state_store.guardar_resultado(conn, _resultado_clasificado("CUFE-B"), Path("b.zip"))

    total = state_store.eliminar_compras(conn, ["CUFE-A"])

    assert total == 1
    assert state_store.ya_existe_cufe(conn, "CUFE-A") is False
    assert state_store.ya_existe_cufe(conn, "CUFE-B") is True


def test_eliminar_compras_lista_vacia_no_hace_nada(tmp_path):
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    assert state_store.eliminar_compras(conn, []) == 0


def test_preferencia_aprendida_upsert_y_lectura(tmp_path):
    conn = state_store.conectar("901528790", base_dir=tmp_path)

    assert state_store.obtener_preferencia_aprendida(conn, "cuenta_contable", "900111", "TORNILLOS") is None

    state_store.guardar_preferencia_aprendida(conn, "cuenta_contable", "900111", "TORNILLOS", "51950101")
    assert state_store.obtener_preferencia_aprendida(conn, "cuenta_contable", "900111", "TORNILLOS") == "51950101"

    # una segunda corrección del usuario reemplaza la anterior (upsert, no acumula)
    state_store.guardar_preferencia_aprendida(conn, "cuenta_contable", "900111", "TORNILLOS", "51950202")
    assert state_store.obtener_preferencia_aprendida(conn, "cuenta_contable", "900111", "TORNILLOS") == "51950202"


def test_preferencia_aprendida_cabecera_con_none_no_se_duplica(tmp_path):
    """item_descripcion=None (campos de cabecera) se normaliza a '' -- si no,
    SQLite no considera iguales dos NULL para el UNIQUE y cada llamada
    insertaría una fila nueva en vez de actualizar (ver el docstring de
    guardar_preferencia_aprendida)."""
    conn = state_store.conectar("901528790", base_dir=tmp_path)

    state_store.guardar_preferencia_aprendida(conn, "medio_pago_id", "900111", None, "10")
    state_store.guardar_preferencia_aprendida(conn, "medio_pago_id", "900111", None, "20")

    assert state_store.obtener_preferencia_aprendida(conn, "medio_pago_id", "900111", None) == "20"
    total_filas = conn.execute(
        "SELECT COUNT(*) FROM sugerencias_aprendidas WHERE campo = 'medio_pago_id' AND proveedor_nit = '900111'"
    ).fetchone()[0]
    assert total_filas == 1


def test_registrar_descartado_no_se_duplica_al_reimportar_la_misma_carpeta(tmp_path):
    """Si se corre `importar` dos veces sobre la misma carpeta (ej. el usuario
    la vuelve a correr por error), el log de auditoría no debe duplicarse --
    solo `compras` necesita bloquear duro, esto es solo trazabilidad."""
    conn = state_store.conectar("901528790", base_dir=tmp_path)
    descarte = DocumentoDuplicado(cufe="CUFE-X", origen=Path("dos.zip"), origen_primera_aparicion=Path("uno.zip"))

    state_store.registrar_descartado(conn, descarte)
    state_store.registrar_descartado(conn, descarte)

    filas = conn.execute("SELECT COUNT(*) FROM documentos_descartados").fetchone()[0]
    assert filas == 1
