"""
Pruebas de "Compras en Siigo": `descargar_compras_siigo` trae TODO de Siigo
(GET /v1/purchases, paginado completo) y reemplaza el caché local;
`listar_compras_siigo` solo lee ese caché -- nunca llama a Siigo. Nunca toca
la red real -- siigo_client se reemplaza por paginación falsa, con formas de
respuesta confirmadas contra la API real (ver src/siigo_client.py).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orquestador  # noqa: E402
import siigo_client  # noqa: E402
import state_store  # noqa: E402


def _compra(numero, fecha, nit, prefijo, num_prov, total, items=None):
    return {
        "id": f"siigo-{numero}",
        "number": numero,
        "date": fecha,
        "supplier": {"identification": nit},
        "provider_invoice": {"prefix": prefijo, "number": num_prov},
        "total": total,
        "items": items or [{
            "description": "Item de prueba", "code": "51950101", "type": "Account",
            "quantity": 1.0, "price": total, "total": total,
            "taxes": [{"name": "IVA 19%", "type": "IVA", "percentage": 19.0, "value": total * 0.19}],
        }],
    }


@pytest.fixture
def empresa_configurada(tmp_path, monkeypatch):
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"empresa-test","nit":"900000000","nombre":"EMPRESA TEST"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", tmp_path / "data" / "empresas")

    original_conectar = state_store.conectar

    def _conectar_en_tmp(nit_empresa, base_dir=None):
        return original_conectar(nit_empresa, base_dir=tmp_path / "data" / "empresas")

    monkeypatch.setattr(state_store, "conectar", _conectar_en_tmp)
    orquestador.guardar_conexion_siigo("empresa-test", "correo@empresa.com", "ACCESS-KEY", "Axon")
    return "empresa-test"


def _preparar_paginas_falsas(monkeypatch, paginas: list[list[dict]], total_results: int):
    monkeypatch.setattr(siigo_client, "autenticar", lambda u, k: "TOKEN")

    def _pagina_falsa(token, partner_id, page=1, page_size=100):
        if page > len(paginas):
            return [], {}
        return paginas[page - 1], {"page": page, "page_size": page_size, "total_results": total_results}

    monkeypatch.setattr(siigo_client, "obtener_purchases_pagina", _pagina_falsa)
    monkeypatch.setattr(siigo_client, "obtener_nombre_proveedor", lambda t, p, nit: f"Proveedor {nit}")


def test_descargar_trae_todas_las_paginas_y_las_guarda(empresa_configurada, monkeypatch):
    # la página 1 debe venir COMPLETA (100 items, como pediría orquestador con
    # PAGE_SIZE=100) para que el corte de paginación siga a la página 2 --
    # una página corta se interpreta como la última, sin importar total_results.
    pagina1 = [_compra(200 - i, "2026-06-30", "900111", "FE", str(i), 1000) for i in range(100)]
    pagina2 = [_compra(99, "2026-05-10", "900222", "FE", "999", 30000)]
    _preparar_paginas_falsas(monkeypatch, [pagina1, pagina2], total_results=101)

    resumen = orquestador.descargar_compras_siigo(empresa_configurada)
    assert resumen == {"total": 101}

    todas = orquestador.listar_compras_siigo(empresa_configurada)
    assert len(todas) == 101
    assert 99 in {f["numero"] for f in todas}


def test_listar_local_no_llama_a_siigo(empresa_configurada, monkeypatch):
    pagina1 = [_compra(1, "2026-06-01", "900111", "FE", "1", 100000)]
    _preparar_paginas_falsas(monkeypatch, [pagina1], total_results=1)
    orquestador.descargar_compras_siigo(empresa_configurada)

    def _fallar_si_se_llama(*a, **k):
        raise AssertionError("listar_compras_siigo no debería llamar a Siigo")
    monkeypatch.setattr(siigo_client, "autenticar", _fallar_si_se_llama)
    monkeypatch.setattr(siigo_client, "obtener_purchases_pagina", _fallar_si_se_llama)

    resultado = orquestador.listar_compras_siigo(empresa_configurada)
    assert len(resultado) == 1
    assert resultado[0]["proveedor_nombre"] == "Proveedor 900111"


def test_listar_local_sin_descarga_previa_devuelve_vacio(empresa_configurada):
    assert orquestador.listar_compras_siigo(empresa_configurada) == []


def test_descargar_reemplaza_no_acumula(empresa_configurada, monkeypatch):
    _preparar_paginas_falsas(monkeypatch, [[_compra(1, "2026-06-01", "900111", "FE", "1", 1000)]], total_results=1)
    orquestador.descargar_compras_siigo(empresa_configurada)
    assert len(orquestador.listar_compras_siigo(empresa_configurada)) == 1

    _preparar_paginas_falsas(monkeypatch, [[
        _compra(1, "2026-06-01", "900111", "FE", "1", 1000),
        _compra(2, "2026-06-02", "900111", "FE", "2", 2000),
    ]], total_results=2)
    orquestador.descargar_compras_siigo(empresa_configurada)
    assert len(orquestador.listar_compras_siigo(empresa_configurada)) == 2


def test_filtro_por_fecha_y_texto_es_local(empresa_configurada, monkeypatch):
    pagina1 = [
        _compra(1, "2026-06-01", "900111", "FE", "100", 10000),
        _compra(2, "2026-06-15", "900222", "FC", "200", 20000),
        _compra(3, "2026-05-01", "900222", "FC", "300", 30000),
    ]
    _preparar_paginas_falsas(monkeypatch, [pagina1], total_results=3)
    orquestador.descargar_compras_siigo(empresa_configurada)

    por_fecha = orquestador.listar_compras_siigo(empresa_configurada, desde="2026-06-01", hasta="2026-06-30")
    assert {f["numero"] for f in por_fecha} == {1, 2}

    por_texto = orquestador.listar_compras_siigo(empresa_configurada, texto="900222")
    assert {f["numero"] for f in por_texto} == {2, 3}


def test_resuelve_y_cachea_nombre_de_proveedor(empresa_configurada, monkeypatch):
    pagina1 = [_compra(1, "2026-06-01", "900111", "FE", "1", 100000)]
    _preparar_paginas_falsas(monkeypatch, [pagina1], total_results=1)
    orquestador.descargar_compras_siigo(empresa_configurada)

    def _fallar_si_se_llama(t, p, nit):
        raise AssertionError("no debería resolver de nuevo un nombre ya cacheado")
    monkeypatch.setattr(siigo_client, "obtener_nombre_proveedor", _fallar_si_se_llama)

    # segunda descarga: el nombre ya está en proveedores_siigo, no debe re-resolverse
    orquestador.descargar_compras_siigo(empresa_configurada)
    assert orquestador.listar_compras_siigo(empresa_configurada)[0]["proveedor_nombre"] == "Proveedor 900111"


def test_mapea_items_e_impuestos_al_formato_de_detalle(empresa_configurada, monkeypatch):
    pagina1 = [_compra(1, "2026-06-01", "900111", "FE", "1", 119000, items=[{
        "description": "MASILLA GALON", "code": "61302001", "type": "Account",
        "quantity": 2.0, "price": 50000, "total": 100000,
        "taxes": [{"name": "IVA 19%", "type": "IVA", "percentage": 19.0, "value": 19000}],
    }])]
    _preparar_paginas_falsas(monkeypatch, [pagina1], total_results=1)
    orquestador.descargar_compras_siigo(empresa_configurada)

    item = orquestador.listar_compras_siigo(empresa_configurada)[0]["items"][0]
    assert item["descripcion"] == "MASILLA GALON"
    assert item["cuenta_contable"] == "61302001"
    assert item["cantidad"] == 2.0
    assert item["impuestos"][0] == {"tipo": "IVA 19%", "porcentaje": 19.0, "valor": 19000}


def test_sin_conexion_configurada_da_error_claro(tmp_path, monkeypatch):
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"vacia","nit":"800000000","nombre":"SIN CONFIG"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", tmp_path / "config" / "empresas")

    with pytest.raises(ValueError, match="Conexión Siigo"):
        orquestador.descargar_compras_siigo("vacia")
