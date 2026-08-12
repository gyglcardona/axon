"""
Pruebas de src/contai_export.py: arma el asiento contable (plano de
movimientos) y una fila de tercero nuevo para Contai. Función pura, sin
I/O -- nunca toca archivos ni red. La forma del asiento está confirmada
contra un ejemplo real de contai_movimientos.xlsx (ver docstring del
módulo): tres débitos (7033+37016+116201=160250) cuadran contra un solo
crédito a caja.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import contai_export  # noqa: E402


def _config(**overrides):
    base = {
        "comprobante": "00010",
        "modo_pago_default": "contado",
        "cuenta_credito_contado": "110505",
        "cuenta_credito_credito": "",
        "cuentas_iva_por_tarifa": {"19.0": "24081001"},
        "cuentas_retencion_por_tipo": {},
        "cuentas_gasto_por_categoria": {"0.0": "620505", "19.0": "620505"},
        "cuentas_impuesto_por_tipo": {},
    }
    base.update(overrides)
    return base


def _factura(items, **overrides):
    base = {
        "numero_factura": "FE1102", "proveedor_nit": "890920001", "fecha_emision": "2025-01-31",
        "items": items,
    }
    base.update(overrides)
    return base


def _item(**overrides):
    base = {
        "cuenta_contable": "620505", "cantidad": 1, "valor_unitario": 37015.79,
        "descuento_monto": 0, "impuestos": [{"tipo": "IVA", "porcentaje": 19.0, "valor": 7033.0}],
    }
    base.update(overrides)
    return base


def test_asiento_real_cuadra_debitos_contra_creditos():
    """Caso real decodificado a mano (documento 000486536): dos líneas de
    gasto, una con IVA -- tres débitos deben cuadrar contra el único
    crédito a la cuenta de contado."""
    factura = _factura([
        _item(cuenta_contable="620505", valor_unitario=37015.79,
              impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 7033.0}]),
        _item(cuenta_contable="620515", valor_unitario=116201, impuestos=[]),
    ])
    config = _config(cuentas_gasto_por_categoria={"19.0": "620505", "0.0": "620515"})
    r = contai_export.construir_movimientos(factura, config)

    assert r["motivos_bloqueo"] == []
    filas = r["filas"]
    debitos = [f for f in filas if f["Tipo"] == 1]
    creditos = [f for f in filas if f["Tipo"] == 2]
    assert len(debitos) == 3  # 2 gastos + 1 IVA
    assert len(creditos) == 1
    assert round(sum(f["Valor"] for f in debitos), 2) == round(sum(f["Valor"] for f in creditos), 2)
    assert creditos[0]["Cuenta"] == "110505"
    assert creditos[0]["NIT"] == ""  # confirmado contra el ejemplo real


def test_comprobante_fecha_y_detalle():
    factura = _factura([_item(impuestos=[])], fecha_emision="2025-01-31")
    r = contai_export.construir_movimientos(factura, _config())

    fila = r["filas"][0]
    assert fila["Comprobante"] == "00010"
    assert fila["Fecha(mm/dd/yyyy)"] == "01/31/2025"
    assert fila["Detalle"] == "COMPRA ENERO 2025"
    assert fila["Documento"] == "FE1102"
    assert fila["Documento Ref."] == "FE1102"


def test_dos_tarifas_de_iva_generan_dos_lineas_de_debito():
    factura = _factura([
        _item(cuenta_contable="620505", valor_unitario=100000,
              impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.0}]),
        _item(cuenta_contable="620510", valor_unitario=50000,
              impuestos=[{"tipo": "IVA", "porcentaje": 5.0, "valor": 2500.0}]),
    ])
    config = _config(
        cuentas_iva_por_tarifa={"19.0": "24081001", "5.0": "24081002"},
        cuentas_gasto_por_categoria={"19.0": "620505", "5.0": "620510"},
    )
    r = contai_export.construir_movimientos(factura, config)

    lineas_iva = [f for f in r["filas"] if f["Cuenta"] in ("24081001", "24081002")]
    assert len(lineas_iva) == 2
    assert {f["Valor"] for f in lineas_iva} == {19000.0, 2500.0}


def test_retencion_genera_linea_de_credito_aparte():
    factura = _factura([
        _item(cuenta_contable="620505", valor_unitario=100000, impuestos=[
            {"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.0},
            {"tipo": "ReteFuente", "porcentaje": 2.5, "valor": 2500.0},
        ]),
    ])
    config = _config(cuentas_retencion_por_tipo={"ReteFuente": "236540"})
    r = contai_export.construir_movimientos(factura, config)

    assert r["motivos_bloqueo"] == []
    linea_retencion = next(f for f in r["filas"] if f["Cuenta"] == "236540")
    assert linea_retencion["Tipo"] == 2
    assert linea_retencion["Valor"] == 2500.0

    debitos = sum(f["Valor"] for f in r["filas"] if f["Tipo"] == 1)
    creditos = sum(f["Valor"] for f in r["filas"] if f["Tipo"] == 2)
    assert debitos == creditos


def test_factura_con_modo_pago_propio_usa_esa_cuenta_no_el_default():
    """La empresa tiene modo_pago_default='contado' (cuenta 110505), pero
    esta factura puntual se marcó como 'credito' -- debe usar la cuenta de
    crédito de la factura, no la de contado de la empresa."""
    factura = _factura([_item(impuestos=[])], modo_pago_contai="credito")
    config = _config(cuenta_credito_contado="110505", cuenta_credito_credito="220501")

    r = contai_export.construir_movimientos(factura, config)

    assert r["motivos_bloqueo"] == []
    credito = next(f for f in r["filas"] if f["Tipo"] == 2)
    assert credito["Cuenta"] == "220501"


def test_factura_sin_modo_pago_propio_usa_el_default_de_la_empresa():
    factura = _factura([_item(impuestos=[])])  # sin modo_pago_contai
    config = _config(cuenta_credito_contado="110505", cuenta_credito_credito="220501")

    r = contai_export.construir_movimientos(factura, config)

    credito = next(f for f in r["filas"] if f["Tipo"] == 2)
    assert credito["Cuenta"] == "110505"


def test_bloquea_si_falta_cuenta_de_credito_del_modo_de_pago_de_la_factura():
    """La empresa sí tiene configurada la cuenta de contado, pero esta
    factura pide crédito y esa cuenta no está configurada -- debe bloquear
    mencionando "credito", no fallar en silencio usando la de contado."""
    factura = _factura([_item(impuestos=[])], modo_pago_contai="credito")
    config = _config(cuenta_credito_contado="110505", cuenta_credito_credito="")

    r = contai_export.construir_movimientos(factura, config)

    assert r["filas"] is None
    assert any("credito" in m for m in r["motivos_bloqueo"])


def test_cuenta_contable_del_item_ya_no_se_usa_ni_bloquea():
    """El campo cuenta_contable del ítem quedó vestigial para Contai (pedido
    del contador, agosto 2026) -- aunque esté vacío o ausente, la
    exportación no debe bloquear ni usarlo: la fila de gasto sale de la
    cuenta configurada para la categoría fiscal de la línea, no de
    item['cuenta_contable']."""
    factura = _factura([_item(cuenta_contable=None, impuestos=[])])
    r = contai_export.construir_movimientos(factura, _config())

    assert r["motivos_bloqueo"] == []
    gasto = next(f for f in r["filas"] if f["Tipo"] == 1)
    assert gasto["Cuenta"] == "620505"  # cuenta configurada para tarifa "0.0", no la del ítem


def test_bloquea_si_falta_cuenta_de_gasto_para_la_categoria_presente():
    """Ya no exige cuenta_contable por ítem -- lo que bloquea ahora es que
    falte la cuenta de GASTO configurada para la categoría fiscal (tarifa de
    IVA, o "0.0" si no tiene) presente en la factura."""
    factura = _factura([_item(impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.0}])])
    config = _config(cuentas_gasto_por_categoria={})

    r = contai_export.construir_movimientos(factura, config)

    assert r["filas"] is None
    assert any("categoría" in m and "19.0" in m for m in r["motivos_bloqueo"])


def test_dos_items_misma_tarifa_generan_una_sola_fila_de_gasto():
    """Pedido explícito del contador (agosto 2026): 'los asientos se hacen
    en base a los totales, no en base a cada ítem' -- dos líneas con la
    misma tarifa de IVA deben sumar en UNA sola fila de gasto, no dos."""
    factura = _factura([
        _item(valor_unitario=100000, impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.0}]),
        _item(valor_unitario=50000, impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 9500.0}]),
    ])
    r = contai_export.construir_movimientos(factura, _config())

    gastos = [f for f in r["filas"] if f["Cuenta"] == "620505" and f["Tipo"] == 1]
    assert len(gastos) == 1
    assert gastos[0]["Valor"] == 150000


# --- Impuesto especial (no IVA, no retención -- ej. Impuesto al Consumo,
# IBUA/ICUI): confirmado explícitamente por el usuario (agosto 2026) que va
# en su propia fila de débito, aparte de la base -- no se suma a la cuenta
# de gasto de la línea ni cambia su categoría. ---

def test_impuesto_especial_genera_su_propia_fila_de_debito():
    factura = _factura([
        _item(valor_unitario=100000, impuestos=[
            {"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.0},
            {"tipo": "INC", "porcentaje": 8.0, "valor": 8000.0},
        ]),
    ])
    config = _config(cuentas_impuesto_por_tipo={"INC": "620520"})

    r = contai_export.construir_movimientos(factura, config)

    assert r["motivos_bloqueo"] == []
    # La base sigue cayendo en la cuenta de gasto por tarifa de IVA (19.0),
    # SIN el valor del INC mezclado -- el INC no le cambia la categoría.
    gasto = next(f for f in r["filas"] if f["Cuenta"] == "620505")
    assert gasto["Valor"] == 100000
    # El IVA sigue yendo aparte, sin chocar con el INC.
    iva = next(f for f in r["filas"] if f["Cuenta"] == "24081001")
    assert iva["Valor"] == 19000.0
    # El INC va en su propia fila de débito, a su propia cuenta.
    inc = next(f for f in r["filas"] if f["Cuenta"] == "620520")
    assert inc["Tipo"] == 1
    assert inc["Valor"] == 8000.0

    debitos = sum(f["Valor"] for f in r["filas"] if f["Tipo"] == 1)
    creditos = sum(f["Valor"] for f in r["filas"] if f["Tipo"] == 2)
    assert debitos == creditos == 127000  # 100000 base + 19000 IVA + 8000 INC


def test_bloquea_si_falta_cuenta_de_impuesto_especial_para_el_tipo_presente():
    factura = _factura([_item(impuestos=[{"tipo": "ICUI", "porcentaje": 8.0, "valor": 3000.0}])])
    r = contai_export.construir_movimientos(factura, _config())

    assert r["filas"] is None
    assert any("ICUI" in m for m in r["motivos_bloqueo"])


def test_item_otros_impuestos_no_duplica_su_valor_como_base():
    """Caso real (agosto 2026, NABOR ABAD GARCIA HOYOS, factura FVDM6616953):
    motor_reglas agrupa impuestos sin código Siigo propio (Impuesto al
    Consumo, IBUA) en un ítem sintético "OTROS IMPUESTOS" (origen=
    "otros_impuestos"), cuyo valor_unitario ES la suma de esos impuestos,
    no una base de compra real. Si ese valor también se sumara como base
    de la categoría "0.0", el monto quedaría contado dos veces (una como
    base, otra como impuesto especial) y el asiento no cuadraría contra el
    total real de la factura."""
    factura = _factura([
        _item(valor_unitario=100000, impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.0}]),
        _item(
            descripcion="OTROS IMPUESTOS", cantidad=1, valor_unitario=29760.0, descuento_monto=0,
            cuenta_contable=None, origen="otros_impuestos",
            impuestos=[{"tipo": "IC", "porcentaje": 0.0, "valor": 3360.0},
                       {"tipo": "IBUA", "porcentaje": 0.0, "valor": 26400.0}],
        ),
    ])
    config = _config(cuentas_impuesto_por_tipo={"IC": "620520", "IBUA": "620525"})

    r = contai_export.construir_movimientos(factura, config)

    assert r["motivos_bloqueo"] == []
    # La base del ítem "OTROS IMPUESTOS" NO debe aparecer en ninguna cuenta
    # de gasto -- solo la base real del primer ítem (100000).
    gasto = next(f for f in r["filas"] if f["Cuenta"] == "620505")
    assert gasto["Valor"] == 100000
    ic = next(f for f in r["filas"] if f["Cuenta"] == "620520")
    assert ic["Valor"] == 3360.0
    ibua = next(f for f in r["filas"] if f["Cuenta"] == "620525")
    assert ibua["Valor"] == 26400.0

    debitos = sum(f["Valor"] for f in r["filas"] if f["Tipo"] == 1)
    creditos = sum(f["Valor"] for f in r["filas"] if f["Tipo"] == 2)
    # 100000 base + 19000 IVA + 3360 IC + 26400 IBUA = 148760 (NO 178520,
    # que sería el resultado si el ítem "OTROS IMPUESTOS" también aportara
    # sus 29760 como base).
    assert debitos == creditos == 148760


def test_item_otros_impuestos_sin_desglose_legado_sigue_sumando_como_base():
    """Resguardo para datos importados ANTES de este fix (agosto 2026): un
    ítem "OTROS IMPUESTOS" que quedó guardado con impuestos=[] (el
    desglose nunca se persistió) no debe perder su valor en silencio --
    ya que no hay a dónde redirigirlo como impuesto especial, se sigue
    sumando como base (mismo comportamiento que antes de este fix), para
    que el total de la factura no quede corto. Caso real: NABOR ABAD
    GARCIA HOYOS, facturas FVDM6616953/FVDM6658173, que el usuario decidió
    no corregir en la base de datos."""
    factura = _factura([
        _item(valor_unitario=100000, impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.0}]),
        _item(
            descripcion="OTROS IMPUESTOS", cantidad=1, valor_unitario=29760.0, descuento_monto=0,
            cuenta_contable=None, origen="otros_impuestos", impuestos=[],
        ),
    ])
    config = _config(cuentas_gasto_por_categoria={"19.0": "620505", "0.0": "620515"})
    r = contai_export.construir_movimientos(factura, config)

    assert r["motivos_bloqueo"] == []
    gasto_19 = next(f for f in r["filas"] if f["Cuenta"] == "620505")
    assert gasto_19["Valor"] == 100000
    gasto_otros = next(f for f in r["filas"] if f["Cuenta"] == "620515")
    assert gasto_otros["Valor"] == 29760  # nada se pierde, aunque vaya a la cuenta "0.0" en vez de a IC/IBUA

    debitos = sum(f["Valor"] for f in r["filas"] if f["Tipo"] == 1)
    creditos = sum(f["Valor"] for f in r["filas"] if f["Tipo"] == 2)
    assert debitos == creditos == 148760  # 100000 + 29760 base + 19000 IVA


def test_tarifa_configurada_sin_decimal_igual_hace_match():
    """Bug real confirmado en producción (agosto 2026, NABOR ABAD GARCIA
    HOYOS): el usuario tipeó "19" en el campo Tarifa % de "Cuentas y
    tarifas" (sin ".0") -- la tarifa de la línea sale de
    detalle_impuestos.porcentaje, una columna REAL de SQLite que sqlite3
    siempre devuelve como float, así que str(...) da "19.0". "19" != "19.0"
    bloqueaba la exportación con "Falta la cuenta de IVA" aunque sí
    estuviera configurada. No debe bloquear."""
    factura = _factura([_item(impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.0}])])
    config = _config(cuentas_iva_por_tarifa={"19": "24081001"})

    r = contai_export.construir_movimientos(factura, config)

    assert r["motivos_bloqueo"] == []
    linea_iva = next(f for f in r["filas"] if f["Cuenta"] == "24081001")
    assert linea_iva["Valor"] == 19000.0


def test_tarifa_configurada_con_espacios_igual_hace_match():
    factura = _factura([_item(impuestos=[{"tipo": "IVA", "porcentaje": 5.0, "valor": 5000.0}])])
    config = _config(cuentas_iva_por_tarifa={" 5 ": "24081002"}, cuentas_gasto_por_categoria={"5.0": "620510"})

    r = contai_export.construir_movimientos(factura, config)

    assert r["motivos_bloqueo"] == []


def test_bloquea_si_falta_cuenta_de_iva_para_la_tarifa_presente():
    factura = _factura([_item(impuestos=[{"tipo": "IVA", "porcentaje": 5.0, "valor": 5000.0}])])
    config = _config(cuentas_iva_por_tarifa={"19.0": "24081001"}, cuentas_gasto_por_categoria={"5.0": "620510"})
    r = contai_export.construir_movimientos(factura, config)

    assert r["filas"] is None
    assert any("tarifa 5.0" in m for m in r["motivos_bloqueo"])


def test_bloquea_si_falta_cuenta_de_retencion_para_el_tipo_presente():
    factura = _factura([_item(impuestos=[{"tipo": "ReteICA", "porcentaje": 1.0, "valor": 1000.0}])])
    r = contai_export.construir_movimientos(factura, _config())

    assert r["filas"] is None
    assert any("ReteICA" in m for m in r["motivos_bloqueo"])


def test_bloquea_si_falta_cuenta_de_credito_del_modo_de_pago():
    factura = _factura([_item(impuestos=[])])
    r = contai_export.construir_movimientos(factura, _config(cuenta_credito_contado=""))

    assert r["filas"] is None
    assert any("contado" in m for m in r["motivos_bloqueo"])


# --- NIT por línea según el Tipo de Cuenta real (N/S/C/B/A) del plan de
# cuentas de Contai -- ver docs/03-ingesta-dian, referencia aportada por el
# usuario. Antes esto era una regla fija por posición (todo débito lleva
# NIT, el crédito final no); ahora depende del tipo real de cada cuenta. ---

def test_cuenta_tipo_s_lleva_nit():
    factura = _factura([_item(cuenta_contable="620505", impuestos=[])])
    r = contai_export.construir_movimientos(factura, _config(), tipos_cuenta={"620505": "S", "110505": "N"})

    gasto = next(f for f in r["filas"] if f["Cuenta"] == "620505")
    assert gasto["NIT"] == "890920001"  # nit_proveedor de _factura()


def test_cuenta_tipo_c_lleva_nit():
    factura = _factura([_item(cuenta_contable="620505", impuestos=[])])
    r = contai_export.construir_movimientos(factura, _config(), tipos_cuenta={"620505": "C"})

    gasto = next(f for f in r["filas"] if f["Cuenta"] == "620505")
    assert gasto["NIT"] == "890920001"


def test_cuenta_tipo_n_no_lleva_nit():
    factura = _factura([_item(cuenta_contable="620505", impuestos=[])])
    r = contai_export.construir_movimientos(factura, _config(), tipos_cuenta={"620505": "N"})

    gasto = next(f for f in r["filas"] if f["Cuenta"] == "620505")
    assert gasto["NIT"] == ""


def test_cuenta_sin_tipo_conocido_no_lleva_nit():
    """Ni tipos_cuenta=None ni una cuenta que no aparece en el plan de
    cuentas importado deben inventar un NIT -- si no se sabe el tipo, se
    deja vacío, no se asume."""
    factura = _factura([_item(cuenta_contable="620505", impuestos=[])])

    r_sin_plan = contai_export.construir_movimientos(factura, _config())
    r_cuenta_no_encontrada = contai_export.construir_movimientos(factura, _config(), tipos_cuenta={"999999": "S"})

    assert next(f for f in r_sin_plan["filas"] if f["Cuenta"] == "620505")["NIT"] == ""
    assert next(f for f in r_cuenta_no_encontrada["filas"] if f["Cuenta"] == "620505")["NIT"] == ""


def test_cuenta_tipo_b_lleva_nit():
    """Caso real confirmado contra un contai_movimientos.xlsx generado por
    el propio Contai (agosto 2026): la cuenta de IVA (tipo B -- "cuentas de
    impuestos" según la referencia oficial de Contai) trae NIT en cada
    fila, igual que las cuentas de gasto. La cuenta real del usuario
    (24080501, IVA GENERADO 19%) es tipo B, no S/C."""
    factura = _factura([_item(cuenta_contable="620505",
                               impuestos=[{"tipo": "IVA", "porcentaje": 19.0, "valor": 19000.0}])])
    config = _config(cuentas_iva_por_tarifa={"19.0": "24080501"})

    r = contai_export.construir_movimientos(factura, config, tipos_cuenta={"620505": "S", "24080501": "B"})

    iva = next(f for f in r["filas"] if f["Cuenta"] == "24080501")
    assert iva["NIT"] == "890920001"


def test_cuenta_tipo_a_no_lleva_nit():
    factura = _factura([_item(cuenta_contable="620505", impuestos=[])])
    r = contai_export.construir_movimientos(factura, _config(), tipos_cuenta={"620505": "A"})

    gasto = next(f for f in r["filas"] if f["Cuenta"] == "620505")
    assert gasto["NIT"] == ""


def test_cuenta_de_credito_tipo_s_si_lleva_nit():
    """Caso real reportado por el usuario: la cuenta de crédito 130505 es
    tipo S en su plan de cuentas -- antes esta función se lo quitaba a
    ciegas a la línea de crédito sin mirar su tipo real; ahora depende del
    tipo, igual que cualquier otra línea."""
    factura = _factura([_item(cuenta_contable="620505", impuestos=[])])
    config = _config(cuenta_credito_contado="130505")

    r = contai_export.construir_movimientos(factura, config, tipos_cuenta={"620505": "N", "130505": "S"})

    credito = next(f for f in r["filas"] if f["Cuenta"] == "130505")
    assert credito["NIT"] == "890920001"


def test_construir_tercero_nuevo_juridica():
    tercero = {"nit": "900123456", "nombre": "PROVEEDOR SAS", "id_type": "31",
               "direccion": "CALLE 1", "correo": "a@b.com", "telefono": "3001234567",
               "ciudad_nombre": "Medellín", "ciudad_codigo": "05001"}
    fila = contai_export.construir_tercero_nuevo(tercero)

    assert fila["NIT"] == "900123456"
    assert fila["Nombre"] == "PROVEEDOR SAS"
    assert fila["Naturaleza"] == "J"
    assert fila["Tipo"] == "A"
    assert fila["Direccion"] == "CALLE 1"
    assert fila["Email"] == "a@b.com"
    assert fila["Ciudad"] == "Medellín"
    assert fila["Municipio"] == "05001"
    assert fila["Pais"] == "169"
    assert set(fila.keys()) == set(contai_export.COLUMNAS_TERCERO)


def test_construir_tercero_nuevo_persona_natural():
    tercero = {"nit": "123456789", "nombre": "JUAN PEREZ", "id_type": "13"}
    fila = contai_export.construir_tercero_nuevo(tercero)

    assert fila["Naturaleza"] == "N"
    assert fila["Tipo"] == "C"


def test_construir_tercero_nuevo_sin_datos_opcionales_no_revienta():
    fila = contai_export.construir_tercero_nuevo({"nit": "1", "nombre": "X"})
    assert fila["Direccion"] == ""
    assert fila["Email"] == ""
    assert fila["Ciudad"] == ""
    assert fila["Municipio"] == ""
    assert fila["Pais"] == "169"


# --- filas_a_txt: plano .txt separado por ';' (confirmado con el usuario,
# agosto 2026) -- mismas filas que ya van al .xlsx, dos formatos por archivo. ---

def test_filas_a_txt_separa_por_punto_y_coma_con_encabezado():
    columnas = ("A", "B")
    filas = [{"A": "x", "B": "y"}]

    contenido = contai_export.filas_a_txt(columnas, filas)
    texto = contenido.decode("utf-8-sig")

    lineas = texto.strip("\r\n").split("\r\n")
    assert lineas[0] == "A;B"
    assert lineas[1] == "x;y"


def test_filas_a_txt_numeros_con_punto_decimal_dos_cifras():
    """El separador de columnas es ';', así que los decimales SIEMPRE van
    con punto (nunca coma) -- si no, ';' separaría columnas y ',' separaría
    decimales dentro del mismo archivo, ambigüedad real que hay que evitar."""
    columnas = ("Valor",)
    filas = [{"Valor": 7033.0}, {"Valor": 100000.5}, {"Valor": 1234.567}]

    contenido = contai_export.filas_a_txt(columnas, filas)
    lineas = contenido.decode("utf-8-sig").strip("\r\n").split("\r\n")

    assert lineas[1:] == ["7033.00", "100000.50", "1234.57"]


def test_filas_a_txt_valor_none_queda_vacio():
    columnas = ("NIT",)
    filas = [{"NIT": None}, {"NIT": "900123456"}]

    contenido = contai_export.filas_a_txt(columnas, filas)
    lineas = contenido.decode("utf-8-sig").strip("\r\n").split("\r\n")

    assert lineas[1:] == ["", "900123456"]


def test_filas_a_txt_sin_filas_da_solo_encabezado():
    contenido = contai_export.filas_a_txt(("A", "B"), [])
    lineas = contenido.decode("utf-8-sig").strip("\r\n").split("\r\n")
    assert lineas == ["A;B"]
