"""
Pruebas de "recalcular sin reimportar": tomar una corrección manual y
ofrecerla para otras facturas ya importadas del mismo proveedor, dentro del
rango de fechas activo, con ítems de descripción parecida (no
necesariamente idéntica). El emparejamiento es aproximado a propósito (ver
motor_sugerencias.descripciones_similares) -- por eso el flujo real siempre
pasa primero por una previsualización que el usuario confirma
(buscar_candidatos_recalculo) antes de aplicar nada (aplicar_recalculo_masivo).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import motor_sugerencias  # noqa: E402
import orquestador  # noqa: E402
import state_store  # noqa: E402
from dian_parser import FacturaDian  # noqa: E402
from motor_reglas import ItemSiigo, ResultadoClasificacion  # noqa: E402


def test_similitud_detecta_variaciones_minimas():
    # mismo producto, tallas/unidades distintas -- debe considerarse parecido
    assert motor_sugerencias.descripciones_similares("TORNILLO 13 PULGADAS", 'TORNILLO 14"')
    assert motor_sugerencias.descripciones_similares("MASILLA GALON", "MASILLA MEDIO GALON")


def test_similitud_no_confunde_productos_distintos():
    assert not motor_sugerencias.descripciones_similares("TORNILLO 13 PULGADAS", "CEMENTO GRIS 50KG")


@pytest.fixture
def empresa_configurada(tmp_path, monkeypatch):
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"empresa-test","nit":"900000000","nombre":"EMPRESA TEST"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")
    monkeypatch.setattr(orquestador, "CONFIG_PROVEEDORES_DIR", tmp_path / "config" / "proveedores")
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", tmp_path / "data" / "empresas")

    original_conectar = state_store.conectar

    def _conectar_en_tmp(nit_empresa, base_dir=None):
        return original_conectar(nit_empresa, base_dir=tmp_path / "data" / "empresas")

    monkeypatch.setattr(state_store, "conectar", _conectar_en_tmp)
    return "empresa-test"


def _sembrar_factura(cufe, fecha_emision, descripciones, proveedor_nit="900111222"):
    conn = state_store.conectar("900000000")
    factura = FacturaDian(
        cufe=cufe, numero_factura=cufe, prefijo="F", numero_puro="1", fecha_emision=fecha_emision,
        proveedor_nombre="FERRETERIA TEST", proveedor_nit=proveedor_nit,
        proveedor_correo=None, proveedor_direccion=None,
        subtotal_xml=1000, subtotal_fuente="TaxExclusiveAmount", total_pagar_xml=1190,
    )
    items = [
        ItemSiigo(descripcion=d, cantidad=1, valor_unitario=100, cuenta_contable=None)
        for d in descripciones
    ]
    resultado = ResultadoClasificacion(factura=factura, items=items, resuelto_por="manual")
    state_store.guardar_resultado(conn, resultado, archivo_origen=Path("x.zip"))
    conn.close()


def _items_de(slug, cufe):
    f = next(x for x in orquestador.listar_facturas(slug) if x["cufe"] == cufe)
    return f["items"]


def test_encuentra_candidatos_similares_de_otras_facturas(empresa_configurada):
    slug = empresa_configurada
    _sembrar_factura("CUFE-ORIGEN", "2026-07-05", ["TORNILLO 13 PULGADAS"])
    _sembrar_factura("CUFE-B", "2026-07-10", ['TORNILLO 14"'])  # parecido, otra factura
    _sembrar_factura("CUFE-C", "2026-07-12", ["CEMENTO GRIS 50KG"])  # no parecido
    origen_id = _items_de(slug, "CUFE-ORIGEN")[0]["id"]
    orquestador.actualizar_item(slug, "CUFE-ORIGEN", origen_id, {"cuenta_contable": "51950101"})

    r = orquestador.buscar_candidatos_recalculo(
        slug, "CUFE-ORIGEN", origen_id, "cuenta_contable", "2026-07-01", "2026-07-31",
    )

    assert r["valor"] == "51950101"
    descripciones = {c["descripcion"] for c in r["candidatos"]}
    assert descripciones == {'TORNILLO 14"'}


def test_no_incluye_items_que_ya_tienen_el_mismo_valor(empresa_configurada):
    slug = empresa_configurada
    _sembrar_factura("CUFE-ORIGEN", "2026-07-05", ["TORNILLO 13 PULGADAS"])
    _sembrar_factura("CUFE-B", "2026-07-10", ['TORNILLO 14"'])
    origen_id = _items_de(slug, "CUFE-ORIGEN")[0]["id"]
    otro_id = _items_de(slug, "CUFE-B")[0]["id"]
    orquestador.actualizar_item(slug, "CUFE-ORIGEN", origen_id, {"cuenta_contable": "51950101"})
    orquestador.actualizar_item(slug, "CUFE-B", otro_id, {"cuenta_contable": "51950101"})  # ya tiene el mismo valor

    r = orquestador.buscar_candidatos_recalculo(
        slug, "CUFE-ORIGEN", origen_id, "cuenta_contable", "2026-07-01", "2026-07-31",
    )

    assert r["candidatos"] == []


def test_respeta_el_rango_de_fechas(empresa_configurada):
    slug = empresa_configurada
    _sembrar_factura("CUFE-ORIGEN", "2026-07-05", ["TORNILLO 13 PULGADAS"])
    _sembrar_factura("CUFE-FUERA-DE-RANGO", "2026-08-01", ['TORNILLO 14"'])
    origen_id = _items_de(slug, "CUFE-ORIGEN")[0]["id"]
    orquestador.actualizar_item(slug, "CUFE-ORIGEN", origen_id, {"cuenta_contable": "51950101"})

    r = orquestador.buscar_candidatos_recalculo(
        slug, "CUFE-ORIGEN", origen_id, "cuenta_contable", "2026-07-01", "2026-07-31",
    )

    assert r["candidatos"] == []


def test_sin_rango_da_error(empresa_configurada):
    slug = empresa_configurada
    _sembrar_factura("CUFE-ORIGEN", "2026-07-05", ["TORNILLO 13 PULGADAS"])
    origen_id = _items_de(slug, "CUFE-ORIGEN")[0]["id"]

    with pytest.raises(ValueError, match="nunca se corre contra toda la tabla"):
        orquestador.buscar_candidatos_recalculo(slug, "CUFE-ORIGEN", origen_id, "cuenta_contable", "", "")


def test_campo_vacio_en_origen_da_error(empresa_configurada):
    slug = empresa_configurada
    _sembrar_factura("CUFE-ORIGEN", "2026-07-05", ["TORNILLO 13 PULGADAS"])
    origen_id = _items_de(slug, "CUFE-ORIGEN")[0]["id"]

    with pytest.raises(ValueError, match="no hay nada que propagar"):
        orquestador.buscar_candidatos_recalculo(
            slug, "CUFE-ORIGEN", origen_id, "cuenta_contable", "2026-07-01", "2026-07-31",
        )


def test_aplicar_recalculo_solo_toca_los_ids_confirmados(empresa_configurada):
    slug = empresa_configurada
    _sembrar_factura("CUFE-ORIGEN", "2026-07-05", ["TORNILLO 13 PULGADAS"])
    _sembrar_factura("CUFE-B", "2026-07-10", ['TORNILLO 14"'])
    _sembrar_factura("CUFE-C", "2026-07-12", ['TORNILLO 15"'])
    id_b = _items_de(slug, "CUFE-B")[0]["id"]
    id_c = _items_de(slug, "CUFE-C")[0]["id"]

    r = orquestador.aplicar_recalculo_masivo(slug, "cuenta_contable", "51950101", [id_b])

    assert r == {"actualizados": 1}
    assert _items_de(slug, "CUFE-B")[0]["cuenta_contable"] == "51950101"
    assert _items_de(slug, "CUFE-C")[0]["cuenta_contable"] is None  # no estaba en la lista confirmada -- no se toca
    assert id_c  # sembrada para que exista un ítem "distractor" en la corrida
    # y queda aprendido para la próxima importación de ese ítem puntual
    conn = state_store.conectar("900000000")
    assert motor_sugerencias.sugerir_item(conn, "900111222", 'TORNILLO 14"')["cuenta_contable"] == "51950101"
