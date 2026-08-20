"""
Pruebas de reglas_propuestas.py (persistencia pura en JSON por NIT) y de las
funciones de orquestador.py que lo envuelven con el permiso de
auth.puede_gestionar_reglas -- ver frontend "Reglas por empresa" (menú
Maestros, solo visible para superusuario/contador).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import auth  # noqa: E402
import orquestador  # noqa: E402
import reglas_propuestas  # noqa: E402
import state_store  # noqa: E402
from dian_parser import FacturaDian  # noqa: E402
from motor_reglas import ItemSiigo, ResultadoClasificacion  # noqa: E402


# --- reglas_propuestas.py (módulo puro) ---

def test_listar_sin_archivo_da_lista_vacia(tmp_path):
    assert reglas_propuestas.listar("900000000", tmp_path) == []


def test_crear_agrega_una_regla_pendiente(tmp_path):
    regla = reglas_propuestas.crear("900000000", "IVA no discriminado en Kopps", "contador@firma.com", tmp_path)

    assert regla["id"] == 1
    assert regla["texto"] == "IVA no discriminado en Kopps"
    assert regla["estado"] == "pendiente"
    assert regla["creado_por"] == "contador@firma.com"
    assert regla["respuesta"] is None
    assert regla["aplicada_en"] is None
    assert reglas_propuestas.listar("900000000", tmp_path) == [regla]


def test_crear_ids_consecutivos_por_empresa(tmp_path):
    reglas_propuestas.crear("900000000", "Regla uno", "a@x.com", tmp_path)
    r2 = reglas_propuestas.crear("900000000", "Regla dos", "a@x.com", tmp_path)
    assert r2["id"] == 2


def test_crear_no_mezcla_reglas_entre_empresas(tmp_path):
    reglas_propuestas.crear("900000000", "Regla de la empresa 1", "a@x.com", tmp_path)
    reglas_propuestas.crear("900111111", "Regla de la empresa 2", "b@x.com", tmp_path)

    assert len(reglas_propuestas.listar("900000000", tmp_path)) == 1
    assert len(reglas_propuestas.listar("900111111", tmp_path)) == 1


def test_crear_texto_vacio_da_error(tmp_path):
    with pytest.raises(ValueError, match="vacía"):
        reglas_propuestas.crear("900000000", "   ", "a@x.com", tmp_path)


def test_crear_texto_demasiado_largo_da_error(tmp_path):
    with pytest.raises(ValueError, match="larga"):
        reglas_propuestas.crear("900000000", "x" * 4001, "a@x.com", tmp_path)


def test_cambiar_estado_a_respondida_guarda_respuesta(tmp_path):
    regla = reglas_propuestas.crear("900000000", "¿Cómo se causa el flete?", "a@x.com", tmp_path)

    actualizada = reglas_propuestas.cambiar_estado(
        "900000000", regla["id"], "respondida",
        "El flete va a la cuenta 517005 según la política ya existente.", "claude-review@axon.com",
        tmp_path,
    )

    assert actualizada["estado"] == "respondida"
    assert actualizada["respuesta"] == "El flete va a la cuenta 517005 según la política ya existente."
    assert actualizada["respondida_por"] == "claude-review@axon.com"
    assert actualizada["respondida_en"] is not None
    assert actualizada["aplicada_en"] is None


def test_cambiar_estado_a_aplicada_registra_fecha(tmp_path):
    regla = reglas_propuestas.crear("900000000", "Regla nueva", "a@x.com", tmp_path)

    actualizada = reglas_propuestas.cambiar_estado(
        "900000000", regla["id"], "aplicada", "Ya se ajustó el motor de reglas.", "claude-review@axon.com", tmp_path,
    )

    assert actualizada["estado"] == "aplicada"
    assert actualizada["aplicada_en"] is not None


def test_cambiar_estado_invalido_da_error(tmp_path):
    regla = reglas_propuestas.crear("900000000", "Regla nueva", "a@x.com", tmp_path)
    with pytest.raises(ValueError, match="inválido"):
        reglas_propuestas.cambiar_estado("900000000", regla["id"], "que-no-existe", None, "a@x.com", tmp_path)


def test_cambiar_estado_id_inexistente_da_error(tmp_path):
    with pytest.raises(ValueError, match="No existe"):
        reglas_propuestas.cambiar_estado("900000000", 999, "aplicada", None, "a@x.com", tmp_path)


def test_eliminar_regla_pendiente_ok(tmp_path):
    regla = reglas_propuestas.crear("900000000", "prueba", "a@x.com", tmp_path)

    reglas_propuestas.eliminar("900000000", regla["id"], tmp_path)

    assert reglas_propuestas.listar("900000000", tmp_path) == []


def test_eliminar_regla_respondida_da_error(tmp_path):
    regla = reglas_propuestas.crear("900000000", "prueba", "a@x.com", tmp_path)
    reglas_propuestas.cambiar_estado("900000000", regla["id"], "respondida", "x", "claude@axon.com", tmp_path)

    with pytest.raises(ValueError, match="pendiente"):
        reglas_propuestas.eliminar("900000000", regla["id"], tmp_path)
    assert len(reglas_propuestas.listar("900000000", tmp_path)) == 1


def test_eliminar_regla_id_inexistente_da_error(tmp_path):
    with pytest.raises(ValueError, match="No existe"):
        reglas_propuestas.eliminar("900000000", 999, tmp_path)


def test_eliminar_no_afecta_otras_reglas(tmp_path):
    r1 = reglas_propuestas.crear("900000000", "Regla uno", "a@x.com", tmp_path)
    r2 = reglas_propuestas.crear("900000000", "Regla dos", "a@x.com", tmp_path)

    reglas_propuestas.eliminar("900000000", r1["id"], tmp_path)

    restantes = reglas_propuestas.listar("900000000", tmp_path)
    assert len(restantes) == 1
    assert restantes[0]["id"] == r2["id"]


# --- orquestador.py (envoltorio con permiso) ---

@pytest.fixture
def empresa_configurada(tmp_path, monkeypatch):
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"empresa-test","nit":"900000000","nombre":"EMPRESA TEST"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "REGLAS_PROPUESTAS_DIR", tmp_path / "data" / "reglas-propuestas")
    return "empresa-test"


def _actor(rol, email="user@x.com", puede_crear_usuarios=False):
    return {"id": 1, "email": email, "rol": rol, "puede_crear_usuarios": puede_crear_usuarios}


def test_crear_regla_propuesta_superusuario_ok(empresa_configurada):
    regla = orquestador.crear_regla_propuesta(empresa_configurada, "Duda sobre retención", _actor("superusuario"))
    assert regla["texto"] == "Duda sobre retención"


def test_crear_regla_propuesta_contador_sin_flag_ok(empresa_configurada):
    """A diferencia de crear_empresa_administrada, cualquier contador puede
    proponer reglas -- no depende de puede_crear_usuarios (ver
    auth.puede_gestionar_reglas)."""
    regla = orquestador.crear_regla_propuesta(
        empresa_configurada, "Duda", _actor("contador", puede_crear_usuarios=False),
    )
    assert regla["estado"] == "pendiente"


def test_crear_regla_propuesta_rol_empresa_da_error(empresa_configurada):
    with pytest.raises(auth.AuthError, match="permiso"):
        orquestador.crear_regla_propuesta(empresa_configurada, "Duda", _actor("empresa"))


def test_crear_regla_propuesta_empresa_inexistente_da_error(empresa_configurada):
    with pytest.raises(orquestador.EmpresaNoEncontrada):
        orquestador.crear_regla_propuesta("no-existe", "Duda", _actor("superusuario"))


def test_listar_reglas_propuestas_rol_empresa_da_error(empresa_configurada):
    orquestador.crear_regla_propuesta(empresa_configurada, "Duda", _actor("superusuario"))
    with pytest.raises(auth.AuthError):
        orquestador.listar_reglas_propuestas(empresa_configurada, _actor("empresa"))


def test_listar_reglas_propuestas_ok(empresa_configurada):
    orquestador.crear_regla_propuesta(empresa_configurada, "Duda uno", _actor("superusuario"))
    orquestador.crear_regla_propuesta(empresa_configurada, "Duda dos", _actor("contador"))

    reglas = orquestador.listar_reglas_propuestas(empresa_configurada, _actor("superusuario"))

    assert len(reglas) == 2


def test_cambiar_estado_regla_propuesta_ok(empresa_configurada):
    regla = orquestador.crear_regla_propuesta(empresa_configurada, "Duda", _actor("contador"))

    actualizada = orquestador.cambiar_estado_regla_propuesta(
        empresa_configurada, regla["id"], "aplicada", "Se ajustó el código.", _actor("superusuario"),
    )

    assert actualizada["estado"] == "aplicada"
    assert actualizada["aplicada_en"] is not None


def test_cambiar_estado_regla_propuesta_rol_empresa_da_error(empresa_configurada):
    regla = orquestador.crear_regla_propuesta(empresa_configurada, "Duda", _actor("contador"))
    with pytest.raises(auth.AuthError):
        orquestador.cambiar_estado_regla_propuesta(
            empresa_configurada, regla["id"], "aplicada", "x", _actor("empresa"),
        )


def test_eliminar_regla_propuesta_ok(empresa_configurada):
    regla = orquestador.crear_regla_propuesta(empresa_configurada, "prueba", _actor("contador"))

    orquestador.eliminar_regla_propuesta(empresa_configurada, regla["id"], _actor("superusuario"))

    assert orquestador.listar_reglas_propuestas(empresa_configurada, _actor("superusuario")) == []


def test_eliminar_regla_propuesta_rol_empresa_da_error(empresa_configurada):
    regla = orquestador.crear_regla_propuesta(empresa_configurada, "prueba", _actor("contador"))
    with pytest.raises(auth.AuthError):
        orquestador.eliminar_regla_propuesta(empresa_configurada, regla["id"], _actor("empresa"))


# --- orquestador.reglas_confirmadas (solo lectura, política + perfiles de proveedor) ---

@pytest.fixture
def empresa_con_reglas_confirmadas(tmp_path, monkeypatch):
    registro = tmp_path / "registro.json"
    registro.write_text(
        '{"empresas":[{"slug":"empresa-test","nit":"900000000","nombre":"EMPRESA TEST"}]}',
        encoding="utf-8",
    )
    config_empresas = tmp_path / "config" / "empresas"
    config_proveedores = tmp_path / "config" / "proveedores"
    base_datos_empresas = tmp_path / "data" / "empresas"
    docs_dir = tmp_path / "docs" / "02-reglas-negocio"
    config_empresas.mkdir(parents=True)
    config_proveedores.mkdir(parents=True)

    monkeypatch.setattr(orquestador, "REGISTRO", registro)
    monkeypatch.setattr(orquestador, "CONFIG_EMPRESAS_DIR", config_empresas)
    monkeypatch.setattr(orquestador, "CONFIG_PROVEEDORES_DIR", config_proveedores)
    monkeypatch.setattr(orquestador, "BASE_DATOS_EMPRESAS", base_datos_empresas)
    monkeypatch.setattr(orquestador, "DOCS_REGLAS_NEGOCIO_DIR", docs_dir)

    original_conectar = state_store.conectar

    def _conectar_en_tmp(nit_empresa, base_dir=None):
        return original_conectar(nit_empresa, base_dir=base_datos_empresas)

    monkeypatch.setattr(state_store, "conectar", _conectar_en_tmp)
    return "empresa-test"


def _sembrar_factura_de_proveedor(nit_empresa, proveedor_nit, proveedor_nombre, cufe):
    conn = state_store.conectar(nit_empresa)
    factura = FacturaDian(
        cufe=cufe, numero_factura="FE1", prefijo="FE", numero_puro="1", fecha_emision="2025-01-31",
        proveedor_nombre=proveedor_nombre, proveedor_nit=proveedor_nit,
        proveedor_correo=None, proveedor_direccion=None,
        subtotal_xml=100000, subtotal_fuente="TaxExclusiveAmount", total_pagar_xml=119000,
    )
    items = [ItemSiigo(descripcion="ITEM", cantidad=1, valor_unitario=100000, cuenta_contable="620505")]
    resultado = ResultadoClasificacion(factura=factura, items=items, resuelto_por="manual")
    state_store.guardar_resultado(conn, resultado, archivo_origen=Path("x.zip"))
    conn.close()


def test_reglas_confirmadas_sin_nada_configurado(empresa_con_reglas_confirmadas):
    r = orquestador.reglas_confirmadas(empresa_con_reglas_confirmadas, _actor("superusuario"))
    assert r == {"politicas_empresa": [], "perfiles_proveedor": []}


def test_reglas_confirmadas_rol_empresa_da_error(empresa_con_reglas_confirmadas):
    with pytest.raises(auth.AuthError):
        orquestador.reglas_confirmadas(empresa_con_reglas_confirmadas, _actor("empresa"))


def test_reglas_confirmadas_incluye_politica_activa_de_la_empresa(empresa_con_reglas_confirmadas):
    ruta = orquestador.CONFIG_EMPRESAS_DIR / "900000000.json"
    ruta.write_text(json.dumps({
        "politicas": {
            "iva_no_discriminado": {
                "activa": True,
                "comportamiento": {"accion": "mover_iva_a_item", "cuenta_contable": "519595"},
            },
        },
    }), encoding="utf-8")

    r = orquestador.reglas_confirmadas(empresa_con_reglas_confirmadas, _actor("superusuario"))

    assert len(r["politicas_empresa"]) == 1
    assert r["politicas_empresa"][0]["clave"] == "iva_no_discriminado"
    assert r["politicas_empresa"][0]["comportamiento"]["cuenta_contable"] == "519595"


def test_reglas_confirmadas_ignora_politica_inactiva(empresa_con_reglas_confirmadas):
    ruta = orquestador.CONFIG_EMPRESAS_DIR / "900000000.json"
    ruta.write_text(json.dumps({
        "politicas": {"iva_no_discriminado": {"activa": False, "comportamiento": {}}},
    }), encoding="utf-8")

    r = orquestador.reglas_confirmadas(empresa_con_reglas_confirmadas, _actor("superusuario"))

    assert r["politicas_empresa"] == []


def test_reglas_confirmadas_nunca_expone_credenciales_siigo(empresa_con_reglas_confirmadas):
    ruta = orquestador.CONFIG_EMPRESAS_DIR / "900000000.json"
    ruta.write_text(json.dumps({
        "politicas": {"iva_no_discriminado": {"activa": True, "comportamiento": {}}},
        "credenciales_siigo": {"usuario": "secreto@empresa.com", "access_key": "clave-super-secreta"},
    }), encoding="utf-8")

    r = orquestador.reglas_confirmadas(empresa_con_reglas_confirmadas, _actor("superusuario"))

    assert "secreto@empresa.com" not in json.dumps(r)
    assert "clave-super-secreta" not in json.dumps(r)


def test_reglas_confirmadas_solo_incluye_proveedores_que_ya_facturaron(empresa_con_reglas_confirmadas):
    (orquestador.CONFIG_PROVEEDORES_DIR / "800000001.json").write_text(json.dumps({
        "nit": "800000001", "nombre": "COMMERK S.A.S", "comportamiento": {"gran_contribuyente": True},
    }), encoding="utf-8")
    # perfil de un proveedor que NUNCA le ha facturado a esta empresa -- no debe aparecer
    (orquestador.CONFIG_PROVEEDORES_DIR / "800000002.json").write_text(json.dumps({
        "nit": "800000002", "nombre": "OTRO PROVEEDOR", "comportamiento": {},
    }), encoding="utf-8")
    _sembrar_factura_de_proveedor("900000000", "800000001", "COMMERK S.A.S", "CUFE-A")

    r = orquestador.reglas_confirmadas(empresa_con_reglas_confirmadas, _actor("superusuario"))

    nits = [p["nit"] for p in r["perfiles_proveedor"]]
    assert nits == ["800000001"]


def test_reglas_confirmadas_proveedor_sin_perfil_no_aparece(empresa_con_reglas_confirmadas):
    """El proveedor le facturó, pero nunca se le creó un archivo en
    config/proveedores/ -- no hay nada que mostrar, no es un error."""
    _sembrar_factura_de_proveedor("900000000", "800000009", "PROVEEDOR SIN PERFIL", "CUFE-B")

    r = orquestador.reglas_confirmadas(empresa_con_reglas_confirmadas, _actor("superusuario"))

    assert r["perfiles_proveedor"] == []


def test_reglas_confirmadas_incluye_texto_del_doc_md(empresa_con_reglas_confirmadas):
    carpeta_docs = orquestador.DOCS_REGLAS_NEGOCIO_DIR / "politicas-empresa"
    carpeta_docs.mkdir(parents=True)
    (carpeta_docs / "900000000-iva-no-discriminado.md").write_text(
        "# Política de empresa\n\nEsta empresa no discrimina el IVA.", encoding="utf-8",
    )
    ruta = orquestador.CONFIG_EMPRESAS_DIR / "900000000.json"
    ruta.write_text(json.dumps({
        "politicas": {"iva_no_discriminado": {"activa": True, "comportamiento": {}}},
    }), encoding="utf-8")

    r = orquestador.reglas_confirmadas(empresa_con_reglas_confirmadas, _actor("superusuario"))

    assert "no discrimina el IVA" in r["politicas_empresa"][0]["detalle_md"]
