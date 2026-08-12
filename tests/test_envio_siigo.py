"""
Pruebas de orquestador.previsualizar_envio_siigo / confirmar_envio_siigo.
previsualizar NUNCA debe tocar la red (CLAUDE.md regla 3: solo se autentica
y se llama a Siigo después de que el usuario confirma explícitamente lo que
va a mandar). confirmar sí llama a siigo_client, pero acá siempre con un
siigo_client falso -- nunca a la red real.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orquestador  # noqa: E402
import siigo_client  # noqa: E402
import state_store  # noqa: E402
from dian_parser import FacturaDian  # noqa: E402
from motor_reglas import ItemSiigo, ResultadoClasificacion  # noqa: E402


@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    """Ningún test de este archivo debe tocar la red real -- si algún camino
    llega a urllib sin mock, debe fallar fuerte y claro, no colgarse ni
    pegarle a Siigo de verdad."""
    import urllib.request

    def _bloquear(*a, **k):
        raise AssertionError("test intentó salir a la red real -- falta un monkeypatch")

    monkeypatch.setattr(urllib.request, "urlopen", _bloquear)


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


def _sembrar_factura_enviable(cufe="CUFE-1"):
    conn = state_store.conectar("900000000")
    state_store.guardar_catalogo_siigo(conn, "taxes", [
        {"id": 19468, "name": "IVA 19%", "type": "IVA", "percentage": 19.0},
    ])
    factura = FacturaDian(
        cufe=cufe, numero_factura="F1", prefijo="F", numero_puro="1", fecha_emision="2026-07-01",
        proveedor_nombre="PROVEEDOR TEST", proveedor_nit="900111222",
        proveedor_correo=None, proveedor_direccion=None,
        subtotal_xml=1000, subtotal_fuente="TaxExclusiveAmount", total_pagar_xml=1190,
    )
    items = [ItemSiigo(descripcion="ITEM", cantidad=1, valor_unitario=1000, cuenta_contable="51950101")]
    resultado = ResultadoClasificacion(
        factura=factura, items=items, resuelto_por="manual",
        tipo_comprobante_id="18679", medio_pago_id="8729",
    )
    state_store.guardar_resultado(conn, resultado, archivo_origen=Path("x.zip"))
    conn.close()


def _sembrar_factura_incompleta(cufe="CUFE-2"):
    conn = state_store.conectar("900000000")
    factura = FacturaDian(
        cufe=cufe, numero_factura="F2", prefijo="F", numero_puro="2", fecha_emision="2026-07-02",
        proveedor_nombre="PROVEEDOR TEST", proveedor_nit="900111222",
        proveedor_correo=None, proveedor_direccion=None,
        subtotal_xml=1000, subtotal_fuente="TaxExclusiveAmount", total_pagar_xml=1190,
    )
    items = [ItemSiigo(descripcion="ITEM SIN CUENTA", cantidad=1, valor_unitario=1000, cuenta_contable=None)]
    resultado = ResultadoClasificacion(factura=factura, items=items, resuelto_por="manual")
    state_store.guardar_resultado(conn, resultado, archivo_origen=Path("x.zip"))
    conn.close()


def test_previsualizar_nunca_llama_a_la_red(empresa_configurada, monkeypatch):
    _sembrar_factura_enviable()

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("previsualizar_envio_siigo no debería tocar la red")

    monkeypatch.setattr(siigo_client, "autenticar", _fallar_si_se_llama)
    monkeypatch.setattr(siigo_client, "crear_purchase", _fallar_si_se_llama)

    resultado = orquestador.previsualizar_envio_siigo(empresa_configurada, ["CUFE-1"])

    assert len(resultado) == 1
    assert resultado[0]["enviable"] is True
    assert resultado[0]["payload"]["document"] == {"id": 18679}


def test_previsualizar_marca_no_enviable_con_motivo(empresa_configurada):
    _sembrar_factura_incompleta()

    resultado = orquestador.previsualizar_envio_siigo(empresa_configurada, ["CUFE-2"])

    assert resultado[0]["enviable"] is False
    assert resultado[0]["payload"] is None
    assert any("cuenta contable" in m for m in resultado[0]["motivos_bloqueo"])


def test_previsualizar_cufe_inexistente_no_revienta(empresa_configurada):
    _sembrar_factura_enviable()
    resultado = orquestador.previsualizar_envio_siigo(empresa_configurada, ["CUFE-NO-EXISTE"])
    assert resultado[0]["enviable"] is False
    assert "No existe" in resultado[0]["motivos_bloqueo"][0]


def test_confirmar_sin_conexion_configurada_da_error_claro(empresa_configurada):
    _sembrar_factura_enviable()
    with pytest.raises(ValueError, match="Conexión Siigo"):
        orquestador.confirmar_envio_siigo(empresa_configurada, ["CUFE-1"])


def test_confirmar_envio_exitoso_persiste_estado_y_siigo_id(empresa_configurada, monkeypatch):
    _sembrar_factura_enviable()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(siigo_client, "obtener_nombre_proveedor", lambda t, p, nit: "PROVEEDOR TEST")
    monkeypatch.setattr(siigo_client, "crear_purchase", lambda t, p, payload: {"id": "siigo-abc-123"})

    resumen = orquestador.confirmar_envio_siigo(empresa_configurada, ["CUFE-1"])

    assert resumen == {
        "enviadas": 1, "con_error": 0,
        "detalle": [{"cufe": "CUFE-1", "ok": True, "siigo_id": "siigo-abc-123"}],
    }
    f = next(x for x in orquestador.listar_facturas(empresa_configurada) if x["cufe"] == "CUFE-1")
    assert f["estado_siigo"] == "enviado"
    assert f["siigo_id"] == "siigo-abc-123"
    assert f["siigo_error"] is None


def test_confirmar_envio_con_error_guarda_payload_y_respuesta(empresa_configurada, monkeypatch):
    _sembrar_factura_enviable()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(siigo_client, "obtener_nombre_proveedor", lambda t, p, nit: "PROVEEDOR TEST")

    def _fallar(t, p, payload):
        raise siigo_client.SiigoError("Siigo respondió HTTP 400: NIT del proveedor no existe")

    monkeypatch.setattr(siigo_client, "crear_purchase", _fallar)

    resumen = orquestador.confirmar_envio_siigo(empresa_configurada, ["CUFE-1"])

    assert resumen["enviadas"] == 0
    assert resumen["con_error"] == 1
    f = next(x for x in orquestador.listar_facturas(empresa_configurada) if x["cufe"] == "CUFE-1")
    assert f["estado_siigo"] == "error"
    assert f["siigo_id"] is None
    error = json.loads(f["siigo_error"])
    assert "NIT del proveedor no existe" in error["error"]
    assert error["payload_enviado"]["document"] == {"id": 18679}


def _sembrar_cache_compras_siigo(proveedor_nit, factura_proveedor):
    conn = state_store.conectar("900000000")
    state_store.guardar_compras_siigo(conn, [{
        "siigo_id": f"siigo-{factura_proveedor}", "numero": 99, "fecha": "2026-06-01",
        "proveedor_nit": proveedor_nit, "proveedor_nombre": "PROVEEDOR TEST",
        "factura_proveedor": factura_proveedor, "total": 1190, "subtotal": 1000, "items": [],
    }], reemplazar_todo=False)
    conn.close()


def test_previsualizar_bloquea_factura_ya_enviada_desde_aqui(empresa_configurada):
    _sembrar_factura_enviable()
    conn = state_store.conectar("900000000")
    state_store.registrar_resultado_envio_siigo(conn, "CUFE-1", "enviado", siigo_id="siigo-previo")
    conn.close()

    r = orquestador.previsualizar_envio_siigo(empresa_configurada, ["CUFE-1"])

    assert r[0]["enviable"] is False
    assert any("Ya fue enviada" in m and "siigo-previo" in m for m in r[0]["motivos_bloqueo"])


def test_previsualizar_bloquea_si_ya_existe_en_el_cache_de_siigo(empresa_configurada):
    """La factura sembrada tiene prefijo 'F' + número '1' -> 'F1', el mismo
    formato de factura_proveedor del caché compras_siigo -- si el mismo
    proveedor ya la tiene causada (por el app anterior o a mano), se
    bloquea: enviarla la duplicaría."""
    _sembrar_factura_enviable()
    _sembrar_cache_compras_siigo("900111222", "F1")

    r = orquestador.previsualizar_envio_siigo(empresa_configurada, ["CUFE-1"])

    assert r[0]["enviable"] is False
    assert any("duplicaría" in m for m in r[0]["motivos_bloqueo"])


def test_mismo_numero_de_otro_proveedor_no_bloquea(empresa_configurada):
    _sembrar_factura_enviable()
    _sembrar_cache_compras_siigo("800999999", "F1")  # otro NIT -- no es la misma factura

    r = orquestador.previsualizar_envio_siigo(empresa_configurada, ["CUFE-1"])

    assert r[0]["enviable"] is True


def test_confirmar_bloquea_duplicado_sin_llamar_a_crear_purchase(empresa_configurada, monkeypatch):
    _sembrar_factura_enviable()
    _sembrar_cache_compras_siigo("900111222", "F1")
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("no debería enviar una factura que ya existe en Siigo")

    monkeypatch.setattr(siigo_client, "crear_purchase", _fallar_si_se_llama)

    resumen = orquestador.confirmar_envio_siigo(empresa_configurada, ["CUFE-1"])

    assert resumen["con_error"] == 1
    assert "duplicaría" in resumen["detalle"][0]["error"]


def test_envio_exitoso_alimenta_el_cache_y_un_reintento_queda_bloqueado(empresa_configurada, monkeypatch):
    """El caché antidúplicados se alimenta también de nuestros propios
    envíos exitosos (la respuesta de POST tiene la misma forma que GET
    /v1/purchases) -- un segundo intento de la misma factura queda
    bloqueado por partida doble (estado 'enviado' + caché)."""
    _sembrar_factura_enviable()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(siigo_client, "obtener_nombre_proveedor", lambda t, p, nit: "PROVEEDOR TEST")
    monkeypatch.setattr(siigo_client, "crear_purchase", lambda t, p, payload: {
        "id": "siigo-abc", "number": 258, "date": "2026-07-01",
        "supplier": {"identification": "900111222"},
        "provider_invoice": {"prefix": "F", "number": "1"},
        "total": 1190,
        "items": [{"code": "51950101", "description": "ITEM", "quantity": 1, "price": 1000, "total": 1000}],
    })

    resumen = orquestador.confirmar_envio_siigo(empresa_configurada, ["CUFE-1"])
    assert resumen["enviadas"] == 1

    conn = state_store.conectar("900000000")
    assert state_store.existe_compra_siigo(conn, "900111222", "F1") is True
    conn.close()

    resumen2 = orquestador.confirmar_envio_siigo(empresa_configurada, ["CUFE-1"])
    assert resumen2["con_error"] == 1
    assert "Ya fue enviada" in resumen2["detalle"][0]["error"]


def test_confirmar_crea_el_tercero_si_no_existe_en_siigo(empresa_configurada, monkeypatch, tmp_path):
    """'si algún nit no existe en siigo, créalo antes' -- el tercero se crea
    con los datos del emisor releídos del XML original de la factura."""
    _sembrar_factura_enviable()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(siigo_client, "obtener_nombre_proveedor", lambda t, p, nit: None)  # no existe
    monkeypatch.setattr(orquestador, "_extraer_tercero_de_origen", lambda archivo, cufe: {
        "nombre": "PROVEEDOR TEST", "nit": "900111222", "digito_verificacion": "1", "id_type": "31",
        "direccion": "CALLE 1 # 2-3", "ciudad_codigo": "05631", "departamento_codigo": "05",
        "correo": "prov@test.com", "telefono": "3000000000", "tax_level_code": "R-99-PN",
    })
    # el SELECT de archivo_origen igual corre -- la factura sembrada tiene 'x.zip'
    creados = []
    monkeypatch.setattr(siigo_client, "crear_customer", lambda t, p, payload: creados.append(payload) or {"id": "tercero-nuevo"})
    monkeypatch.setattr(siigo_client, "crear_purchase", lambda t, p, payload: {"id": "compra-ok"})

    resumen = orquestador.confirmar_envio_siigo(empresa_configurada, ["CUFE-1"])

    assert resumen["enviadas"] == 1
    assert len(creados) == 1
    tercero = creados[0]
    assert tercero["type"] == "Supplier"
    assert tercero["person_type"] == "Company"
    assert tercero["id_type"] == "31"
    assert tercero["identification"] == "900111222"
    assert tercero["name"] == ["PROVEEDOR TEST"]
    assert tercero["check_digit"] == "1"
    assert tercero["fiscal_responsibilities"] == [{"code": "R-99-PN"}]
    assert tercero["address"]["city"] == {"country_code": "Co", "state_code": "05", "city_code": "05631"}
    assert tercero["contacts"][0]["email"] == "prov@test.com"


def test_confirmar_no_crea_tercero_si_ya_existe(empresa_configurada, monkeypatch):
    _sembrar_factura_enviable()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(siigo_client, "obtener_nombre_proveedor", lambda t, p, nit: "PROVEEDOR TEST")  # ya existe

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("no debería crear un tercero que ya existe")

    monkeypatch.setattr(siigo_client, "crear_customer", _fallar_si_se_llama)
    monkeypatch.setattr(siigo_client, "crear_purchase", lambda t, p, payload: {"id": "compra-ok"})

    resumen = orquestador.confirmar_envio_siigo(empresa_configurada, ["CUFE-1"])
    assert resumen["enviadas"] == 1


def test_confirmar_bloquea_si_no_hay_datos_para_crear_el_tercero(empresa_configurada, monkeypatch):
    """Si el XML no trae los códigos DANE que Siigo exige, la factura queda
    con error claro en vez de crear un tercero a medias."""
    _sembrar_factura_enviable()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(siigo_client, "obtener_nombre_proveedor", lambda t, p, nit: None)
    monkeypatch.setattr(orquestador, "_extraer_tercero_de_origen", lambda archivo, cufe: {
        "nombre": "PROVEEDOR TEST", "nit": "900111222", "ciudad_codigo": None, "departamento_codigo": None,
    })

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("no debería intentar crear ni enviar")

    monkeypatch.setattr(siigo_client, "crear_customer", _fallar_si_se_llama)
    monkeypatch.setattr(siigo_client, "crear_purchase", _fallar_si_se_llama)

    resumen = orquestador.confirmar_envio_siigo(empresa_configurada, ["CUFE-1"])

    assert resumen["con_error"] == 1
    assert "códigos DANE" in resumen["detalle"][0]["error"]


def test_payload_tercero_persona_natural_parte_el_nombre():
    payload = orquestador._payload_tercero({
        "nombre": "JUAN FERNANDO ACEVEDO MEJIA", "nit": "71000000", "id_type": "13",
        "ciudad_codigo": "05001", "departamento_codigo": "05",
    })
    assert payload["person_type"] == "Person"
    assert payload["name"] == ["JUAN", "FERNANDO ACEVEDO MEJIA"]


def test_payload_tercero_tax_level_desconocido_cae_a_r99pn():
    payload = orquestador._payload_tercero({
        "nombre": "X", "nit": "900", "id_type": "31",
        "ciudad_codigo": "05001", "departamento_codigo": "05", "tax_level_code": "ZZ-99",
    })
    assert payload["fiscal_responsibilities"] == [{"code": "R-99-PN"}]


def test_payload_tercero_multiples_codigos_de_responsabilidad():
    """Caso real: QUALA trae TaxLevelCode='O-13;O-15;O-23' -- antes del fix,
    comparar el string completo contra _RESPONSABILIDADES_FISCALES_SIIGO
    nunca hacía match y siempre caía a R-99-PN, aunque los 3 códigos fueran
    válidos para Siigo."""
    payload = orquestador._payload_tercero({
        "nombre": "QUALA S.A.", "nit": "860074450", "id_type": "31",
        "ciudad_codigo": "11001", "departamento_codigo": "11", "tax_level_code": "O-13;O-15;O-23",
    })
    assert payload["fiscal_responsibilities"] == [{"code": "O-13"}, {"code": "O-15"}, {"code": "O-23"}]


def test_payload_tercero_multiples_codigos_descarta_los_invalidos():
    payload = orquestador._payload_tercero({
        "nombre": "X", "nit": "900", "id_type": "31",
        "ciudad_codigo": "05001", "departamento_codigo": "05", "tax_level_code": "O-13;ZZ-99",
    })
    assert payload["fiscal_responsibilities"] == [{"code": "O-13"}]


def test_payload_tercero_limpia_el_telefono_de_caracteres_no_numericos():
    """Caso real: Siigo rechazó la creación del tercero de COMMERK (NIT
    800007955) con 'Invalid data type: number' en phones[0].number porque el
    XML DIAN trae el teléfono con guion ('322-3677140') -- hay que enviar
    solo dígitos."""
    payload = orquestador._payload_tercero({
        "nombre": "COMMERK S.A.S", "nit": "800007955", "id_type": "31",
        "ciudad_codigo": "76001", "departamento_codigo": "76", "telefono": "322-3677140",
    })
    assert payload["phones"] == [{"number": "3223677140"}]


def test_payload_tercero_recorta_el_indicativo_de_pais_del_telefono():
    """Caso real: Siigo rechazó la creación del tercero de SYS FMQ (NIT
    901079686) con 'length_max' porque el XML trae el teléfono con
    indicativo de país ('+573223047049', 12 dígitos) y Siigo exige máximo
    10 -- se toman los últimos 10 dígitos, no los primeros, para no cortar
    el número real y dejar el indicativo."""
    payload = orquestador._payload_tercero({
        "nombre": "SYS FMQ S.A.S.", "nit": "901079686", "id_type": "31",
        "ciudad_codigo": "05001", "departamento_codigo": "05", "telefono": "+573223047049",
    })
    assert payload["phones"] == [{"number": "3223047049"}]


def test_payload_tercero_sin_telefono_no_agrega_el_campo():
    payload = orquestador._payload_tercero({
        "nombre": "X", "nit": "900", "id_type": "31",
        "ciudad_codigo": "05001", "departamento_codigo": "05", "telefono": "---",
    })
    assert "phones" not in payload


def test_ver_en_siigo_encuentra_la_causacion_por_nit_y_numero(empresa_configurada):
    _sembrar_factura_enviable()  # prefijo 'F' + numero '1' -> factura_proveedor 'F1'
    _sembrar_cache_compras_siigo("900111222", "F1")

    compra = orquestador.obtener_compra_siigo_de_factura(empresa_configurada, "CUFE-1")

    assert compra is not None
    assert compra["factura_proveedor"] == "F1"
    assert compra["proveedor_nit"] == "900111222"


def test_ver_en_siigo_devuelve_none_si_no_esta_en_cache(empresa_configurada):
    _sembrar_factura_enviable()
    assert orquestador.obtener_compra_siigo_de_factura(empresa_configurada, "CUFE-1") is None


def test_ver_en_siigo_cufe_inexistente_da_error_claro(empresa_configurada):
    _sembrar_factura_enviable()
    with pytest.raises(ValueError, match="No existe una factura"):
        orquestador.obtener_compra_siigo_de_factura(empresa_configurada, "CUFE-FANTASMA")


def test_ver_en_siigo_folio_sin_prefijo_usa_el_mismo_respaldo_que_el_envio(empresa_configurada):
    """Bug real confirmado (factura 2081 de S M BORDADOS Y ESTAMPADOS SAS,
    Hielo Super-Cool): un folio DIAN puramente numérico (prefijo vacío) se
    envía a Siigo con provider_invoice.prefix="FC" (ver
    siigo_payload.PREFIJO_RESPALDO) -- si la llave de cruce se reconstruía
    con prefijo vacío en vez de ese mismo respaldo, "Ver en Siigo" decía
    'no aparece en el caché' para una factura que sí estaba causada."""
    conn = state_store.conectar("900000000")
    factura = FacturaDian(
        cufe="CUFE-SIN-PREFIJO", numero_factura="2081", prefijo="", numero_puro="2081",
        fecha_emision="2026-07-25", proveedor_nombre="PROVEEDOR TEST", proveedor_nit="900111222",
        proveedor_correo=None, proveedor_direccion=None,
        subtotal_xml=1000, subtotal_fuente="TaxExclusiveAmount", total_pagar_xml=1190,
    )
    items = [ItemSiigo(descripcion="ITEM", cantidad=1, valor_unitario=1000, cuenta_contable="51950101")]
    resultado = ResultadoClasificacion(
        factura=factura, items=items, resuelto_por="manual",
        tipo_comprobante_id="18679", medio_pago_id="8729",
    )
    state_store.guardar_resultado(conn, resultado, archivo_origen=Path("x.zip"))
    conn.close()

    _sembrar_cache_compras_siigo("900111222", "FC2081")

    compra = orquestador.obtener_compra_siigo_de_factura(empresa_configurada, "CUFE-SIN-PREFIJO")

    assert compra is not None
    assert compra["factura_proveedor"] == "FC2081"


def test_antiduplicados_folio_sin_prefijo_detecta_lo_que_ya_esta_en_siigo(empresa_configurada):
    """Mismo bug que el de 'Ver en Siigo' pero del lado del antidúplicados:
    sin este fix, una factura con folio sin prefijo que YA está en Siigo
    (bajo la llave real 'FC<numero>') no se detectaba como duplicada."""
    conn = state_store.conectar("900000000")
    factura = FacturaDian(
        cufe="CUFE-SIN-PREFIJO", numero_factura="2081", prefijo="", numero_puro="2081",
        fecha_emision="2026-07-25", proveedor_nombre="PROVEEDOR TEST", proveedor_nit="900111222",
        proveedor_correo=None, proveedor_direccion=None,
        subtotal_xml=1000, subtotal_fuente="TaxExclusiveAmount", total_pagar_xml=1190,
    )
    items = [ItemSiigo(descripcion="ITEM", cantidad=1, valor_unitario=1000, cuenta_contable="51950101")]
    resultado = ResultadoClasificacion(
        factura=factura, items=items, resuelto_por="manual",
        tipo_comprobante_id="18679", medio_pago_id="8729",
    )
    state_store.guardar_resultado(conn, resultado, archivo_origen=Path("x.zip"))
    conn.close()
    _sembrar_cache_compras_siigo("900111222", "FC2081")

    r = orquestador.previsualizar_envio_siigo(empresa_configurada, ["CUFE-SIN-PREFIJO"])

    assert r[0]["enviable"] is False
    assert any("Ya existe en Siigo" in m for m in r[0]["motivos_bloqueo"])


def test_confirmar_no_envia_facturas_bloqueadas(empresa_configurada, monkeypatch):
    _sembrar_factura_incompleta()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("no debería intentar enviar una factura bloqueada")

    monkeypatch.setattr(siigo_client, "crear_purchase", _fallar_si_se_llama)

    resumen = orquestador.confirmar_envio_siigo(empresa_configurada, ["CUFE-2"])

    assert resumen["con_error"] == 1
    assert "cuenta contable" in resumen["detalle"][0]["error"]


def _sembrar_factura_ya_enviada(cufe="CUFE-1", siigo_id="siigo-viejo-123"):
    _sembrar_factura_enviable(cufe)
    conn = state_store.conectar("900000000")
    state_store.registrar_resultado_envio_siigo(conn, cufe, "enviado", siigo_id=siigo_id)
    conn.close()


def test_corregir_iva_duplicado_borra_y_recrea(empresa_configurada, monkeypatch):
    """Caso real: Hielo Super-Cool tenía 58 compras ya causadas en Siigo con
    el ítem de IVA duplicado por un bug ya corregido -- la corrección borra
    la compra vieja y crea una nueva con el payload ya corregido."""
    _sembrar_factura_ya_enviada()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    llamadas = []
    monkeypatch.setattr(siigo_client, "eliminar_purchase", lambda t, p, pid: llamadas.append(("eliminar", pid)))
    monkeypatch.setattr(siigo_client, "crear_purchase", lambda t, p, payload: llamadas.append(("crear", payload)) or {"id": "siigo-nuevo-456"})

    resumen = orquestador.corregir_iva_duplicado_enviadas(empresa_configurada, ["CUFE-1"])

    assert resumen == {
        "corregidas": 1, "con_error": 0,
        "detalle": [{"cufe": "CUFE-1", "ok": True, "siigo_id_viejo": "siigo-viejo-123", "siigo_id_nuevo": "siigo-nuevo-456"}],
    }
    assert llamadas[0] == ("eliminar", "siigo-viejo-123")
    assert llamadas[1][0] == "crear"
    f = next(x for x in orquestador.listar_facturas(empresa_configurada) if x["cufe"] == "CUFE-1")
    assert f["estado_siigo"] == "enviado"
    assert f["siigo_id"] == "siigo-nuevo-456"


def test_corregir_iva_duplicado_rechaza_factura_no_enviada(empresa_configurada, monkeypatch):
    _sembrar_factura_enviable()  # queda 'pendiente', nunca se marcó 'enviado'
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("no debería tocar Siigo para una factura que nunca se envió")

    monkeypatch.setattr(siigo_client, "eliminar_purchase", _fallar_si_se_llama)
    monkeypatch.setattr(siigo_client, "crear_purchase", _fallar_si_se_llama)

    resumen = orquestador.corregir_iva_duplicado_enviadas(empresa_configurada, ["CUFE-1"])

    assert resumen["con_error"] == 1
    assert "no está marcada como enviada" in resumen["detalle"][0]["error"]


def test_corregir_iva_duplicado_no_borra_si_falla_el_borrado(empresa_configurada, monkeypatch):
    """Si eliminar_purchase falla, no se debe intentar crear nada -- la
    compra vieja (incorrecta pero real) sigue intacta, segura de reintentar."""
    _sembrar_factura_ya_enviada()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")

    def _fallar_eliminar(t, p, pid):
        raise siigo_client.SiigoError("Siigo respondió HTTP 404: la compra no existe")

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("no debería intentar crear si el borrado falló")

    monkeypatch.setattr(siigo_client, "eliminar_purchase", _fallar_eliminar)
    monkeypatch.setattr(siigo_client, "crear_purchase", _fallar_si_se_llama)

    resumen = orquestador.corregir_iva_duplicado_enviadas(empresa_configurada, ["CUFE-1"])

    assert resumen["con_error"] == 1
    assert "No se pudo borrar la compra vieja" in resumen["detalle"][0]["error"]
    f = next(x for x in orquestador.listar_facturas(empresa_configurada) if x["cufe"] == "CUFE-1")
    assert f["estado_siigo"] == "enviado"
    assert f["siigo_id"] == "siigo-viejo-123"  # intacta


def test_corregir_iva_duplicado_marca_error_especial_si_falla_la_recreacion(empresa_configurada, monkeypatch):
    """Caso más delicado: se borró la compra vieja pero la nueva no se pudo
    crear -- debe quedar clarísimo en el error que hay que atenderlo a mano,
    no reintentarlo solo (la factura ya no existe en Siigo en este punto)."""
    _sembrar_factura_ya_enviada()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(siigo_client, "eliminar_purchase", lambda t, p, pid: None)

    def _fallar_crear(t, p, payload):
        raise siigo_client.SiigoError("Siigo respondió HTTP 500: error interno")

    monkeypatch.setattr(siigo_client, "crear_purchase", _fallar_crear)

    resumen = orquestador.corregir_iva_duplicado_enviadas(empresa_configurada, ["CUFE-1"])

    assert resumen["con_error"] == 1
    assert "BORRADA" in resumen["detalle"][0]["error"]
    f = next(x for x in orquestador.listar_facturas(empresa_configurada) if x["cufe"] == "CUFE-1")
    assert f["estado_siigo"] == "error"
    assert f["siigo_id"] is None
    assert "BORRADA" in f["siigo_error"]


# --- Borrado por período en Siigo (herramienta de desarrollo/pruebas) ---
# _sembrar_factura_ya_enviada() deja: prefijo="F", numero_puro="1" (factura del
# proveedor "F1"), proveedor_nit="900111222", fecha_emision="2026-07-01".

def _receipt(nit="900111222", prefix="F", consecutive=1, receipt_id="recibo-abc"):
    return {
        "id": receipt_id,
        "supplier": {"identification": nit},
        "items": [{"due": {"prefix": prefix, "consecutive": consecutive}, "value": 1190}],
    }


def test_previsualizar_eliminacion_requiere_rango_de_fechas(empresa_configurada):
    with pytest.raises(ValueError, match="rango de fechas"):
        orquestador.previsualizar_eliminacion_siigo(empresa_configurada, "", "")


def test_previsualizar_eliminacion_solo_incluye_enviadas_en_rango(empresa_configurada):
    _sembrar_factura_ya_enviada(cufe="CUFE-1")  # enviado, fecha_emision 2026-07-01
    _sembrar_factura_enviable(cufe="CUFE-2")    # pendiente -- no debe aparecer

    r = orquestador.previsualizar_eliminacion_siigo(empresa_configurada, "2026-07-01", "2026-07-31")

    assert [x["cufe"] for x in r] == ["CUFE-1"]
    assert r[0]["siigo_id"] == "siigo-viejo-123"


def test_previsualizar_eliminacion_con_cufes_solo_incluye_las_seleccionadas(empresa_configurada):
    """Caso real pedido por el usuario: dentro del rango de fechas, el
    borrado NUNCA debe incluir facturas que el usuario no marcó -- aunque
    estén 'enviado' y dentro del rango, si no vienen en `cufes` se excluyen."""
    _sembrar_factura_ya_enviada(cufe="CUFE-1", siigo_id="siigo-1")
    _sembrar_factura_ya_enviada(cufe="CUFE-2", siigo_id="siigo-2")

    r = orquestador.previsualizar_eliminacion_siigo(empresa_configurada, "2026-07-01", "2026-07-31", cufes=["CUFE-1"])

    assert [x["cufe"] for x in r] == ["CUFE-1"]


def test_previsualizar_eliminacion_sin_cufes_incluye_todas_las_del_rango(empresa_configurada):
    """Sin lista de cufes (compatibilidad hacia atrás / uso interno), se
    comporta como antes: todas las 'enviado' del rango."""
    _sembrar_factura_ya_enviada(cufe="CUFE-1", siigo_id="siigo-1")
    _sembrar_factura_ya_enviada(cufe="CUFE-2", siigo_id="siigo-2")

    r = orquestador.previsualizar_eliminacion_siigo(empresa_configurada, "2026-07-01", "2026-07-31")

    assert {x["cufe"] for x in r} == {"CUFE-1", "CUFE-2"}


def test_previsualizar_eliminacion_respeta_el_rango(empresa_configurada):
    _sembrar_factura_ya_enviada(cufe="CUFE-1")  # fecha_emision 2026-07-01

    r = orquestador.previsualizar_eliminacion_siigo(empresa_configurada, "2026-08-01", "2026-08-31")

    assert r == []


def test_confirmar_eliminacion_borra_recibo_y_compra_y_resetea_estado(empresa_configurada, monkeypatch):
    _sembrar_factura_ya_enviada()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(
        siigo_client, "obtener_payment_receipts_pagina",
        lambda t, p, ci, ce, page=1, page_size=100: ([_receipt()] if page == 1 else [], {}),
    )
    llamadas = []
    monkeypatch.setattr(siigo_client, "eliminar_payment_receipt", lambda t, p, rid: llamadas.append(("recibo", rid)))
    monkeypatch.setattr(siigo_client, "eliminar_purchase", lambda t, p, pid: llamadas.append(("compra", pid)))

    resumen = orquestador.confirmar_eliminacion_siigo(empresa_configurada, ["CUFE-1"])

    assert resumen == {
        "eliminadas": 1, "con_error": 0,
        "detalle": [{"cufe": "CUFE-1", "ok": True, "recibo_borrado": True}],
    }
    assert llamadas == [("recibo", "recibo-abc"), ("compra", "siigo-viejo-123")]
    f = next(x for x in orquestador.listar_facturas(empresa_configurada) if x["cufe"] == "CUFE-1")
    assert f["estado_siigo"] == "pendiente"
    assert f["siigo_id"] is None


def test_confirmar_eliminacion_sin_recibo_encontrado_igual_borra_la_compra(empresa_configurada, monkeypatch):
    """Si el proveedor autorretenedor no genera recibo automático (u otro
    medio de pago que no lo cree), no debe fallar -- se borra la compra
    directamente."""
    _sembrar_factura_ya_enviada()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(siigo_client, "obtener_payment_receipts_pagina", lambda *a, **k: ([], {}))

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("no debería intentar borrar un recibo que no existe")

    monkeypatch.setattr(siigo_client, "eliminar_payment_receipt", _fallar_si_se_llama)
    monkeypatch.setattr(siigo_client, "eliminar_purchase", lambda t, p, pid: None)

    resumen = orquestador.confirmar_eliminacion_siigo(empresa_configurada, ["CUFE-1"])

    assert resumen["eliminadas"] == 1
    assert resumen["detalle"][0]["recibo_borrado"] is False


def test_confirmar_eliminacion_rechaza_factura_no_enviada(empresa_configurada, monkeypatch):
    _sembrar_factura_enviable()  # queda 'pendiente'
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(siigo_client, "obtener_payment_receipts_pagina", lambda *a, **k: ([], {}))

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("no debería tocar Siigo para una factura que nunca se envió")

    monkeypatch.setattr(siigo_client, "eliminar_payment_receipt", _fallar_si_se_llama)
    monkeypatch.setattr(siigo_client, "eliminar_purchase", _fallar_si_se_llama)

    resumen = orquestador.confirmar_eliminacion_siigo(empresa_configurada, ["CUFE-1"])

    assert resumen["con_error"] == 1
    assert "no está marcada como enviada" in resumen["detalle"][0]["error"]


def test_confirmar_eliminacion_no_borra_la_compra_si_falla_el_recibo(empresa_configurada, monkeypatch):
    _sembrar_factura_ya_enviada()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(
        siigo_client, "obtener_payment_receipts_pagina",
        lambda t, p, ci, ce, page=1, page_size=100: ([_receipt()] if page == 1 else [], {}),
    )

    def _fallar_recibo(t, p, rid):
        raise siigo_client.SiigoError("Siigo respondió HTTP 404: el recibo no existe")

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("no debería intentar borrar la compra si el recibo falló")

    monkeypatch.setattr(siigo_client, "eliminar_payment_receipt", _fallar_recibo)
    monkeypatch.setattr(siigo_client, "eliminar_purchase", _fallar_si_se_llama)

    resumen = orquestador.confirmar_eliminacion_siigo(empresa_configurada, ["CUFE-1"])

    assert resumen["con_error"] == 1
    assert "no se pudo borrar el recibo de pago" in resumen["detalle"][0]["error"].lower()
    f = next(x for x in orquestador.listar_facturas(empresa_configurada) if x["cufe"] == "CUFE-1")
    assert f["estado_siigo"] == "enviado"  # intacta
    assert f["siigo_id"] == "siigo-viejo-123"


def test_confirmar_eliminacion_marca_error_si_borra_recibo_pero_no_la_compra(empresa_configurada, monkeypatch):
    _sembrar_factura_ya_enviada()
    orquestador.guardar_conexion_siigo(empresa_configurada, "correo@empresa.com", "ACCESS-KEY", "Axon")
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")
    monkeypatch.setattr(
        siigo_client, "obtener_payment_receipts_pagina",
        lambda t, p, ci, ce, page=1, page_size=100: ([_receipt()] if page == 1 else [], {}),
    )
    monkeypatch.setattr(siigo_client, "eliminar_payment_receipt", lambda t, p, rid: None)

    def _fallar_compra(t, p, pid):
        raise siigo_client.SiigoError("Siigo respondió HTTP 500: error interno")

    monkeypatch.setattr(siigo_client, "eliminar_purchase", _fallar_compra)

    resumen = orquestador.confirmar_eliminacion_siigo(empresa_configurada, ["CUFE-1"])

    assert resumen["con_error"] == 1
    assert "requiere atención manual" in resumen["detalle"][0]["error"]
    f = next(x for x in orquestador.listar_facturas(empresa_configurada) if x["cufe"] == "CUFE-1")
    assert f["estado_siigo"] == "enviado"  # no se marcó pendiente -- la compra sigue en Siigo
