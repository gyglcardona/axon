"""
Pruebas del panel de detalle editable: actualizar cabecera/línea de una
factura ya importada, replicar un campo de línea a las demás del mismo
documento, eliminar facturas, y marcar un proveedor como autorretenedor.
Todo contra bases/archivos en tmp_path -- nunca toca config/proveedores/ ni
data/empresas/ reales del repo.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orquestador  # noqa: E402
import state_store  # noqa: E402
from dian_parser import FacturaDian  # noqa: E402
from motor_reglas import ItemSiigo, ResultadoClasificacion  # noqa: E402


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


def _sembrar_factura(
    cufe="CUFE-1", proveedor_nit="900111", n_items=2, tipo_comprobante_id=None, medio_pago_id=None,
):
    conn = state_store.conectar("900000000")
    factura = FacturaDian(
        cufe=cufe, numero_factura="F1", prefijo="F", numero_puro="1", fecha_emision="2026-06-01",
        proveedor_nombre="PROVEEDOR TEST", proveedor_nit=proveedor_nit,
        proveedor_correo=None, proveedor_direccion=None,
        subtotal_xml=1000, subtotal_fuente="TaxExclusiveAmount", total_pagar_xml=1190,
    )
    items = [
        ItemSiigo(descripcion=f"ITEM {i}", cantidad=1, valor_unitario=100, cuenta_contable=None)
        for i in range(n_items)
    ]
    resultado = ResultadoClasificacion(
        factura=factura, items=items, resuelto_por="manual",
        tipo_comprobante_id=tipo_comprobante_id, medio_pago_id=medio_pago_id,
    )
    state_store.guardar_resultado(conn, resultado, archivo_origen=Path("x.zip"))
    conn.close()


def _items_de(slug, cufe):
    f = next(x for x in orquestador.listar_facturas(slug) if x["cufe"] == cufe)
    return f["items"]


def test_actualizar_factura_guarda_y_aprende(empresa_configurada):
    _sembrar_factura()

    r = orquestador.actualizar_factura(empresa_configurada, "CUFE-1", {"tipo_comprobante_id": "18679"})

    assert r == {"cufe": "CUFE-1", "tipo_comprobante_id": "18679"}
    f = next(x for x in orquestador.listar_facturas(empresa_configurada) if x["cufe"] == "CUFE-1")
    assert f["tipo_comprobante_id"] == "18679"

    # queda aprendido para la próxima importación de ese proveedor
    import motor_sugerencias
    assert motor_sugerencias.sugerir_cabecera(state_store.conectar("900000000"), "900111")["tipo_comprobante_id"] == "18679"


def test_actualizar_factura_modo_pago_contai_se_guarda_sin_aprenderse(empresa_configurada):
    """modo_pago_contai es una decisión por factura, no un patrón del
    proveedor (dos facturas del mismo proveedor pueden pagarse distinto) --
    a diferencia de tipo_comprobante_id/medio_pago_id, no debe quedar
    aprendido para la próxima importación de ese proveedor."""
    _sembrar_factura()

    r = orquestador.actualizar_factura(empresa_configurada, "CUFE-1", {"modo_pago_contai": "credito"})

    assert r == {"cufe": "CUFE-1", "modo_pago_contai": "credito"}
    f = next(x for x in orquestador.listar_facturas(empresa_configurada) if x["cufe"] == "CUFE-1")
    assert f["modo_pago_contai"] == "credito"

    assert state_store.obtener_preferencia_aprendida(
        state_store.conectar("900000000"), "modo_pago_contai", "900111", None,
    ) is None


def test_actualizar_factura_cufe_inexistente_da_error_claro(empresa_configurada):
    _sembrar_factura()
    with pytest.raises(ValueError, match="No existe una factura"):
        orquestador.actualizar_factura(empresa_configurada, "CUFE-QUE-NO-EXISTE", {"tipo_comprobante_id": "1"})


def test_actualizar_item_guarda_y_aprende_por_proveedor_y_descripcion(empresa_configurada):
    _sembrar_factura()
    item_id = _items_de(empresa_configurada, "CUFE-1")[0]["id"]

    r = orquestador.actualizar_item(empresa_configurada, "CUFE-1", item_id, {"cuenta_contable": "51950101"})

    assert r == {"id": item_id, "cuenta_contable": "51950101"}
    assert _items_de(empresa_configurada, "CUFE-1")[0]["cuenta_contable"] == "51950101"

    import motor_sugerencias
    sugerido = motor_sugerencias.sugerir_item(state_store.conectar("900000000"), "900111", "ITEM 0")
    assert sugerido["cuenta_contable"] == "51950101"


def test_actualizar_item_de_otra_factura_da_error(empresa_configurada):
    _sembrar_factura(cufe="CUFE-1")
    _sembrar_factura(cufe="CUFE-2", proveedor_nit="900222")
    item_id_de_cufe2 = _items_de(empresa_configurada, "CUFE-2")[0]["id"]

    with pytest.raises(ValueError, match="no pertenece"):
        orquestador.actualizar_item(empresa_configurada, "CUFE-1", item_id_de_cufe2, {"cuenta_contable": "X"})


def test_replicar_campo_actualiza_las_demas_lineas_no_la_origen_dos_veces(empresa_configurada):
    _sembrar_factura(n_items=3)
    items = _items_de(empresa_configurada, "CUFE-1")
    origen_id = items[0]["id"]
    orquestador.actualizar_item(empresa_configurada, "CUFE-1", origen_id, {"cuenta_contable": "51950101"})

    r = orquestador.replicar_campo_item(empresa_configurada, "CUFE-1", origen_id, "cuenta_contable")

    assert r == {"campo": "cuenta_contable", "valor": "51950101", "lineas_actualizadas": 2}
    cuentas = {it["cuenta_contable"] for it in _items_de(empresa_configurada, "CUFE-1")}
    assert cuentas == {"51950101"}


def test_replicar_campo_invalido_da_error(empresa_configurada):
    _sembrar_factura()
    item_id = _items_de(empresa_configurada, "CUFE-1")[0]["id"]
    with pytest.raises(ValueError, match="no replicable"):
        orquestador.replicar_campo_item(empresa_configurada, "CUFE-1", item_id, "campo_inventado")


def test_eliminar_facturas_borra_y_no_afecta_las_demas(empresa_configurada):
    _sembrar_factura(cufe="CUFE-1")
    _sembrar_factura(cufe="CUFE-2")

    r = orquestador.eliminar_facturas(empresa_configurada, ["CUFE-1"])

    assert r == {"eliminadas": 1}
    cufes = {f["cufe"] for f in orquestador.listar_facturas(empresa_configurada)}
    assert cufes == {"CUFE-2"}


def test_completar_cabecera_faltante_usa_el_valor_unico_de_la_empresa(empresa_configurada):
    """Caso real: Hielo Super-Cool usa siempre el mismo tipo de comprobante y
    medio de pago -- las facturas sin cabecera (proveedor nuevo, sin
    preferencia aprendida propia) deben completarse con ese valor único."""
    _sembrar_factura(cufe="CUFE-1", tipo_comprobante_id="18679", medio_pago_id="4308")
    _sembrar_factura(cufe="CUFE-2", proveedor_nit="900222")  # sin cabecera todavía
    _sembrar_factura(cufe="CUFE-3", proveedor_nit="900333")  # sin cabecera todavía

    r = orquestador.completar_cabecera_faltante_por_empresa(empresa_configurada)

    assert r["tipo_comprobante_id"] == {"valor_usado": "18679", "actualizadas": 2, "motivo": None}
    assert r["medio_pago_id"] == {"valor_usado": "4308", "actualizadas": 2, "motivo": None}
    facturas = {f["cufe"]: f for f in orquestador.listar_facturas(empresa_configurada)}
    assert facturas["CUFE-2"]["tipo_comprobante_id"] == "18679"
    assert facturas["CUFE-3"]["medio_pago_id"] == "4308"


def test_completar_cabecera_nunca_pisa_un_valor_ya_puesto(empresa_configurada):
    _sembrar_factura(cufe="CUFE-1", tipo_comprobante_id="18679", medio_pago_id="4308")
    _sembrar_factura(cufe="CUFE-2", proveedor_nit="900222", tipo_comprobante_id="OTRO-VALOR")

    orquestador.completar_cabecera_faltante_por_empresa(empresa_configurada)

    facturas = {f["cufe"]: f for f in orquestador.listar_facturas(empresa_configurada)}
    assert facturas["CUFE-2"]["tipo_comprobante_id"] == "OTRO-VALOR"


def test_completar_cabecera_no_adivina_si_hay_mas_de_un_valor(empresa_configurada):
    _sembrar_factura(cufe="CUFE-1", tipo_comprobante_id="18679", medio_pago_id="4308")
    _sembrar_factura(cufe="CUFE-2", proveedor_nit="900222", tipo_comprobante_id="99999", medio_pago_id="4308")
    _sembrar_factura(cufe="CUFE-3", proveedor_nit="900333")  # sin cabecera

    r = orquestador.completar_cabecera_faltante_por_empresa(empresa_configurada)

    assert r["tipo_comprobante_id"] == {"valor_usado": None, "actualizadas": 0, "motivo": "no_unico"}
    assert r["medio_pago_id"]["valor_usado"] == "4308"  # este sí es único, no se bloquea por el otro campo
    facturas = {f["cufe"]: f for f in orquestador.listar_facturas(empresa_configurada)}
    assert facturas["CUFE-3"]["tipo_comprobante_id"] is None


def test_completar_cabecera_sin_ninguna_resuelta_da_sin_datos(empresa_configurada):
    _sembrar_factura(cufe="CUFE-1")

    r = orquestador.completar_cabecera_faltante_por_empresa(empresa_configurada)

    assert r["tipo_comprobante_id"] == {"valor_usado": None, "actualizadas": 0, "motivo": "sin_datos"}


def test_marcar_proveedor_autorretenedor_escribe_config_proveedores(empresa_configurada, tmp_path):
    r = orquestador.marcar_proveedor_autorretenedor("900111", "PROVEEDOR TEST", True)

    assert r == {"nit": "900111", "autorretenedor": True}
    ruta = tmp_path / "config" / "proveedores" / "900111.json"
    assert ruta.exists()
    config = json.loads(ruta.read_text(encoding="utf-8"))
    assert config["comportamiento"]["autorretenedor"] is True
    assert config["nombre"] == "PROVEEDOR TEST"
    # motor_sugerencias.es_autorretenedor lee config/proveedores/<nit>.json vía
    # motor_reglas.cargar_config_proveedor con su propio base_dir por defecto
    # (no el CONFIG_PROVEEDORES_DIR de orquestador) -- eso ya se prueba
    # aparte, con ese base_dir sí monkeypatcheado, en test_motor_sugerencias.py.


def test_marca_automatica_por_codigo_o15_autorretenedor(empresa_configurada, tmp_path):
    """Caso real: QUALA/KOPPS declaran O-15 (autorretenedor) en su propio XML
    -- no hay que esperar a que alguien lo marque a mano desde la bandeja."""
    orquestador._marcar_perfil_fiscal_automatico("860074450", "QUALA S.A.", ["O-13", "O-15", "O-23"])

    ruta = tmp_path / "config" / "proveedores" / "860074450.json"
    config = json.loads(ruta.read_text(encoding="utf-8"))
    assert config["comportamiento"]["autorretenedor"] is True
    assert config["comportamiento"]["gran_contribuyente"] is True


def test_marca_automatica_gran_contribuyente_sin_autorretencion(empresa_configurada, tmp_path):
    """Caso real: COMMERK trae O-13 (gran contribuyente) pero NO O-15 -- no
    hay que inferir autorretención de la sola condición de gran contribuyente."""
    orquestador._marcar_perfil_fiscal_automatico("800007955", "COMMERK S.A.S", ["O-13"])

    ruta = tmp_path / "config" / "proveedores" / "800007955.json"
    config = json.loads(ruta.read_text(encoding="utf-8"))
    assert config["comportamiento"]["gran_contribuyente"] is True
    assert "autorretenedor" not in config["comportamiento"]


def test_marca_automatica_sin_codigos_no_crea_archivo(empresa_configurada, tmp_path):
    orquestador._marcar_perfil_fiscal_automatico("900111", "PROVEEDOR SIN RESPONSABILIDADES", [])

    ruta = tmp_path / "config" / "proveedores" / "900111.json"
    assert not ruta.exists()


def test_marca_automatica_nunca_desmarca(empresa_configurada, tmp_path):
    """Una factura puntual sin O-15 no prueba que el proveedor dejó de ser
    autorretenedor -- no debe pisar un True ya guardado con False."""
    orquestador.marcar_proveedor_autorretenedor("860074450", "QUALA S.A.", True)

    orquestador._marcar_perfil_fiscal_automatico("860074450", "QUALA S.A.", [])

    ruta = tmp_path / "config" / "proveedores" / "860074450.json"
    config = json.loads(ruta.read_text(encoding="utf-8"))
    assert config["comportamiento"]["autorretenedor"] is True


def test_desmarcar_proveedor_autorretenedor_preserva_otros_campos(empresa_configurada, tmp_path):
    ruta = tmp_path / "config" / "proveedores" / "900111.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({
        "nit": "900111", "nombre": "PROVEEDOR TEST", "comportamiento": {"impuestos_adicionales": ["Impoconsumo"]},
    }), encoding="utf-8")

    orquestador.marcar_proveedor_autorretenedor("900111", "PROVEEDOR TEST", True)

    config = json.loads(ruta.read_text(encoding="utf-8"))
    assert config["comportamiento"]["autorretenedor"] is True
    assert config["comportamiento"]["impuestos_adicionales"] == ["Impoconsumo"]
