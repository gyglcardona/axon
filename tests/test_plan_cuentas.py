"""
Pruebas de importación del plan de cuentas (Excel exportado de Siigo).

El formato viene confirmado en docs/05-esquema-datos/plan-cuentas-hielo-super-cool.md
a partir de un archivo real: 6 filas de metadatos, encabezados en la fila 7,
9 columnas fijas. Aquí se reproduce ese formato con un Excel sintético (no se
versiona un Excel real de un cliente).
"""

import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orquestador  # noqa: E402
import state_store  # noqa: E402

ENCABEZADOS = [
    "Código", "Nombre", "Categoría", "Clase", "Relación con",
    "Maneja vencimientos", "Diferencia fiscal", "Activo", "Nivel agrupación",
]


def _crear_excel_plan_cuentas(ruta: Path, filas_cuentas: list[tuple]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Plan de cuentas"])
    ws.append(["Empresa ejemplo S.A.S."])
    ws.append(["NIT 900000000-1"])
    ws.append([])
    ws.append([])
    ws.append([])
    ws.append(ENCABEZADOS)
    for fila in filas_cuentas:
        ws.append(list(fila))
    wb.save(ruta)


@pytest.fixture
def empresa_slug(tmp_path, monkeypatch):
    """Aísla el registro y la BD en tmp_path para no tocar config/data reales."""
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"empresa-test","nit":"900000000","nombre":"EMPRESA TEST"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", tmp_path / "data" / "empresas")

    original_conectar = state_store.conectar

    def _conectar_en_tmp(nit_empresa, base_dir=None):
        return original_conectar(nit_empresa, base_dir=tmp_path / "data" / "empresas")

    monkeypatch.setattr(state_store, "conectar", _conectar_en_tmp)
    return "empresa-test"


def test_importa_plan_cuentas_y_cuenta_transaccionales(tmp_path, empresa_slug):
    excel = tmp_path / "plan-cuentas.xlsx"
    _crear_excel_plan_cuentas(excel, [
        ("1", "ACTIVO", "Activo", "Activo", "", "No", "No", "Si", "Agrupador"),
        ("1105", "Caja general", "Activo", "Activo", "", "No", "No", "Si", "Transaccional"),
        ("5195", "Gastos de transporte", "Gasto", "Gastos", "", "No", "No", "Si", "Transaccional"),
        ("229999", "Causación automática compras (sistema)", "Pasivo", "Pasivo", "", "No", "No", "Si", "Transaccional"),
    ])

    resumen = orquestador.importar_plan_cuentas(empresa_slug, str(excel))

    assert resumen == {"total": 4, "transaccionales": 3}

    todas = orquestador.listar_plan_cuentas(empresa_slug)
    assert len(todas) == 4

    transaccionales = orquestador.listar_plan_cuentas(empresa_slug, solo_transaccionales=True)
    assert {c["codigo"] for c in transaccionales} == {"1105", "5195", "229999"}
    assert all(c["nivel_agrupacion"] == "Transaccional" for c in transaccionales)


def test_reimportar_reemplaza_el_plan_anterior(tmp_path, empresa_slug):
    excel1 = tmp_path / "v1.xlsx"
    _crear_excel_plan_cuentas(excel1, [("1105", "Caja", "Activo", "Activo", "", "No", "No", "Si", "Transaccional")])
    orquestador.importar_plan_cuentas(empresa_slug, str(excel1))
    assert len(orquestador.listar_plan_cuentas(empresa_slug)) == 1

    excel2 = tmp_path / "v2.xlsx"
    _crear_excel_plan_cuentas(excel2, [
        ("1105", "Caja general", "Activo", "Activo", "", "No", "No", "Si", "Transaccional"),
        ("1110", "Bancos", "Activo", "Activo", "", "No", "No", "Si", "Transaccional"),
    ])
    orquestador.importar_plan_cuentas(empresa_slug, str(excel2))

    cuentas = orquestador.listar_plan_cuentas(empresa_slug)
    assert len(cuentas) == 2
    assert {c["codigo"] for c in cuentas} == {"1105", "1110"}


def test_archivo_sin_filas_da_error_claro(tmp_path, empresa_slug):
    excel = tmp_path / "vacio.xlsx"
    _crear_excel_plan_cuentas(excel, [])

    with pytest.raises(ValueError, match="No se encontraron cuentas"):
        orquestador.importar_plan_cuentas(empresa_slug, str(excel))


def test_archivo_inexistente_da_error_claro(empresa_slug):
    with pytest.raises(FileNotFoundError):
        orquestador.importar_plan_cuentas(empresa_slug, "no-existe.xlsx")
