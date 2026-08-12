"""
Arma el plano de movimientos (asiento contable) y el plano de terceros
nuevos para Contai, a partir de una factura ya resuelta (misma forma que
devuelve `orquestador.listar_facturas`) -- funciones puras, sin I/O, mismo
espíritu que `siigo_payload.construir_payload`.

Contai no tiene API: en vez de un payload HTTP se arman filas que después
`orquestador.confirmar_exportacion_contai` vuelca a un `.xlsx` con el mismo
orden de columnas que los archivos reales de Contai (`contai_movimientos.xlsx`,
`contai_terceros.xlsx`) que el usuario compartió y se analizaron a mano:

  Cuenta(débito 620505)=37016, Cuenta(débito IVA 24081001)=7033 (base
  37015.79, ≈19%), Cuenta(crédito 110505 CAJA GENERAL)=160250 -- los tres
  débitos (7033+37016+116201) cuadran exacto contra el único crédito.

El GASTO (la BASE de cada línea) se agrupa por tarifa de IVA (normalizada,
ej. "19.0"; "0.0" si la línea no tiene IVA -- no una etiqueta especial: la
cuenta de tarifa 0 es una cuenta de base más, ej. 620515 "COMPRA NO
GRAVADAS", confirmado por el usuario agosto 2026), no por producto/ítem:
pedido explícito del contador de la empresa (agosto 2026), "los asientos se
hacen en base a los totales, no en base a cada ítem" -- mismo criterio que
ya tenía el IVA desde el principio (una fila por tarifa).

Los ítems inyectados por el motor de reglas que no son una compra real
(`origen` en `_ORIGENES_SIN_BASE_PROPIA`, ej. "OTROS IMPUESTOS" -- ver
motor_reglas._extraer_otros_impuestos) no aportan a esta base: su
`valor_unitario` ES un impuesto, no un costo, así que solo se procesa a
través de sus `impuestos` (ver más abajo), nunca sumado como base -- de lo
contrario el monto quedaría contado dos veces.

Si además la línea trae un impuesto especial que no es IVA ni retención
(Impuesto al Consumo, IBUA/ICUI, etc.), ese impuesto genera SU PROPIA fila
de débito aparte, a una cuenta configurable por tipo de impuesto
(`cuentas_impuesto_por_tipo`) -- no se mezcla con la base de la línea ni le
cambia la cuenta a la base. Confirmado explícitamente por el usuario
(agosto 2026): "va aparte, fila propia", el mismo espíritu que ya tenía la
retención (una cuenta configurable por tipo), pero de débito en vez de
crédito. Ver `_categoria_gasto_de_linea` (solo decide la BASE) y el bloque
de `impuesto_por_tipo` en `construir_movimientos` (el valor del impuesto).

Confirmado explícitamente con el usuario (no inventado):
  - Comprobante fijo "00010" = CAUSACIONES.
  - `Detalle` es una leyenda genérica por mes ("COMPRA <MES> <AÑO>"), no
    lleva el número de factura.
  - `Documento`/`Documento Ref.` es el número de factura del proveedor tal
    cual (no un consecutivo interno).
  - Retención (si la trae la factura) es una línea de crédito aparte a una
    cuenta de "retención por pagar" -- pero como el XML del proveedor no
    siempre declara el monto que retiene el COMPRADOR (esa retención la
    aplica quien recibe la factura, no quien la emite), acá solo se genera
    esa línea cuando el XML sí trae un monto declarado para ese tipo
    (`item.impuestos`) -- nunca se inventa un porcentaje para calcularla,
    a diferencia de Siigo (que sí puede calcularla porque tiene su propio
    catálogo de tarifas de retención).
"""

from __future__ import annotations

MESES_ES = [
    "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
    "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE",
]

COLUMNAS_MOVIMIENTO = (
    "Cuenta", "Comprobante", "Fecha(mm/dd/yyyy)", "Documento", "Documento Ref.",
    "NIT", "Detalle", "Tipo", "Valor", "Base",
    "Centro de Costo", "Trans. Ext", "Plazo", "Docto Electrónico",
)

COLUMNAS_TERCERO = (
    "NIT", "Tipo", "Nombre", "Direccion", "Ciudad", "Telefono", "Municipio",
    "Activo", "Tiene RUT", "Pais", "Email", "Celular", "Plazo",
    "Actividad Económica", "Indicativo", "Naturaleza",
)

_TIPOS_RETENCION = ("ReteFuente", "ReteICA", "ReteIVA", "Retencion_General")

DEBITO = 1
CREDITO = 2

# Separador del plano .txt -- confirmado explícitamente con el usuario
# (agosto 2026), no inventado: punto y coma, mismo criterio que usan la
# mayoría de "CSV" en configuración regional Latinoamericana.
SEPARADOR_TXT = ";"


def filas_a_txt(columnas: tuple[str, ...], filas: list[dict], separador: str = SEPARADOR_TXT) -> bytes:
    """Arma un archivo plano .txt a partir de las mismas filas que van al
    .xlsx (mismo orden de columnas, con encabezado). Los valores numéricos
    (float, ej. Valor/Base) se escriben con punto decimal fijo a 2 cifras --
    no se usa coma decimal aunque el separador de columnas sea ';', para no
    generar ambigüedad entre "separa columnas" y "separa decimales" en el
    mismo caracter. UTF-8 con BOM (utf-8-sig) para que tildes/ñ se vean bien
    si algo lo abre como texto plano en Windows; salto de línea \\r\\n."""
    lineas = [separador.join(columnas)]
    for fila in filas:
        valores = []
        for col in columnas:
            valor = fila[col]
            if isinstance(valor, float):
                valores.append(f"{valor:.2f}")
            else:
                valores.append("" if valor is None else str(valor))
        lineas.append(separador.join(valores))
    return ("\r\n".join(lineas) + "\r\n").encode("utf-8-sig")


def _normalizar_tarifa_iva(tarifa) -> str:
    """La tarifa de IVA que trae cada línea (`imp["porcentaje"]`) sale de
    `detalle_impuestos.porcentaje`, una columna REAL de SQLite -- sqlite3
    siempre la devuelve como float de Python, así que `str(...)` da "19.0",
    nunca "19". Si `cuentas_iva_por_tarifa` quedó guardado con la clave tal
    cual la tipeó el usuario en el formulario ("19", sin el ".0"), el cruce
    fallaba en silencio y bloqueaba la exportación con "Falta la cuenta de
    IVA" aunque sí estuviera configurada (bug real confirmado en producción,
    agosto 2026). Normaliza ambos lados al mismo formato antes de comparar."""
    try:
        return str(float(str(tarifa).strip()))
    except (TypeError, ValueError):
        return str(tarifa).strip()


def _detalle_del_mes(fecha_emision: str) -> str:
    anio, mes, _ = fecha_emision.split("-")
    return f"COMPRA {MESES_ES[int(mes) - 1]} {anio}"


def _fecha_mmddyyyy(fecha_emision: str) -> str:
    anio, mes, dia = fecha_emision.split("-")
    return f"{mes}/{dia}/{anio}"


# Ítems inyectados por el motor de reglas cuyo "valor_unitario" no es una
# base de compra real, sino un impuesto en sí mismo (ver
# motor_reglas._extraer_otros_impuestos: agrupa impuestos sin código Siigo
# propio -- IC, IBUA, ICUI, etc. -- en un ítem sintético "OTROS IMPUESTOS").
# Su base NUNCA debe sumarse a `cuentas_gasto_por_categoria` cuando SÍ trae
# el desglose en `impuestos` -- todo su valor ya viaja ahí (ver
# `impuesto_por_tipo` en `construir_movimientos`); sumar también la base
# duplicaría el monto.
_ORIGENES_SIN_BASE_PROPIA = {"otros_impuestos"}


def _tiene_base_propia(item: dict) -> bool:
    """Falso solo para un ítem "OTROS IMPUESTOS" que SÍ trae su desglose de
    impuestos -- ahí su valor va entero por `impuesto_por_tipo`, nunca como
    base. Si el ítem es de ese origen pero NO trae impuestos (facturas
    importadas antes de este fix -- ver motor_reglas._extraer_otros_impuestos,
    agosto 2026 -- que quedaron con `impuestos=[]` guardado permanentemente),
    se sigue tratando como base: es la única forma de no perder ese monto en
    silencio para datos ya importados que no se van a corregir a mano."""
    if item.get("origen") not in _ORIGENES_SIN_BASE_PROPIA:
        return True
    return not item.get("impuestos")


def _categoria_gasto_de_linea(item: dict) -> str:
    """A qué categoría (y por lo tanto a qué cuenta configurada en
    `cuentas_gasto_por_categoria`) va la BASE de esta línea -- agrupada por
    tarifa de IVA (normalizada, ej. "19.0"; "0.0" si no tiene IVA), no por
    producto (pedido explícito del contador de la empresa, agosto 2026:
    "los asientos se hacen en base a los totales, no en base a cada ítem").
    "0.0" y no una etiqueta como "SIN_IMPUESTO": la cuenta para tarifa 0 es
    una cuenta de BASE igual que cualquier otra tarifa (ej. 620515 "COMPRA
    NO GRAVADAS"), no un caso especial -- confirmado por el usuario (agosto
    2026: "si la tarifa iva es 0 esa debe ser la cuenta"). Un impuesto
    especial (Impuesto al Consumo, IBUA/ICUI, etc.) NO cambia esta
    categoría -- su valor va aparte, en su propia fila de débito (ver
    `cuentas_impuesto_por_tipo` en `construir_movimientos`), confirmado
    explícitamente por el usuario (agosto 2026: "va aparte, fila propia")."""
    for imp in item.get("impuestos", []):
        if (imp.get("tipo") or "").upper() == "IVA":
            return _normalizar_tarifa_iva(imp["porcentaje"])
    return _normalizar_tarifa_iva(0)


def _bases_por_categoria(factura: dict) -> dict[str, float]:
    """Suma la base de cada línea de la factura, agrupada por
    `_categoria_gasto_de_linea` -- una entrada por categoría fiscal
    presente, no una por ítem. Los ítems de `_ORIGENES_SIN_BASE_PROPIA`
    (ej. "OTROS IMPUESTOS") no aportan base -- todo su valor es impuesto."""
    bases: dict[str, float] = {}
    for item in factura["items"]:
        if not _tiene_base_propia(item):
            continue
        cantidad = float(item["cantidad"])
        precio = float(item["valor_unitario"])
        descuento = float(item.get("descuento_monto") or 0)
        base_linea = cantidad * precio - descuento
        categoria = _categoria_gasto_de_linea(item)
        bases[categoria] = bases.get(categoria, 0.0) + base_linea
    return bases


def _motivos_bloqueo(factura: dict, config_contai: dict) -> list[str]:
    """Nunca se arma un asiento a medias -- si falta una cuenta, se lista el
    motivo. No se inventa ninguna cuenta contable ni de IVA/retención/gasto.

    Ya NO exige que cada línea tenga su propia `cuenta_contable` (pedido
    explícito del contador, agosto 2026): el gasto se agrupa por categoría
    fiscal (ver `_categoria_gasto_de_linea`), así que lo que bloquea es que
    falte la cuenta configurada para alguna categoría presente en la
    factura -- no que falte en cada ítem por separado."""
    motivos = []

    cuentas_gasto = config_contai.get("cuentas_gasto_por_categoria") or {}
    for categoria in sorted(_bases_por_categoria(factura)):
        if not cuentas_gasto.get(categoria):
            motivos.append(f"Falta la cuenta de gasto configurada para la categoría \"{categoria}\".")

    cuentas_iva = {_normalizar_tarifa_iva(k): v for k, v in (config_contai.get("cuentas_iva_por_tarifa") or {}).items()}
    tarifas_iva_presentes = {
        _normalizar_tarifa_iva(imp["porcentaje"]) for it in factura["items"] for imp in it.get("impuestos", [])
        if (imp.get("tipo") or "").upper() == "IVA"
    }
    for tarifa in sorted(tarifas_iva_presentes):
        if not cuentas_iva.get(tarifa):
            motivos.append(f"Falta la cuenta de IVA configurada para la tarifa {tarifa}%.")

    cuentas_impuesto = config_contai.get("cuentas_impuesto_por_tipo") or {}
    tipos_impuesto_presentes = {
        imp["tipo"] for it in factura["items"] for imp in it.get("impuestos", [])
        if imp.get("tipo") and (imp.get("tipo") or "").upper() != "IVA" and imp.get("tipo") not in _TIPOS_RETENCION
    }
    for tipo in sorted(tipos_impuesto_presentes):
        if not cuentas_impuesto.get(tipo):
            motivos.append(f"Falta la cuenta configurada para el impuesto \"{tipo}\".")

    cuentas_retencion = config_contai.get("cuentas_retencion_por_tipo") or {}
    tipos_retencion_presentes = {
        imp["tipo"] for it in factura["items"] for imp in it.get("impuestos", [])
        if imp.get("tipo") in _TIPOS_RETENCION
    }
    for tipo in sorted(tipos_retencion_presentes):
        if not cuentas_retencion.get(tipo):
            motivos.append(f"Falta la cuenta de retención configurada para {tipo}.")

    modo = factura.get("modo_pago_contai") or config_contai.get("modo_pago_default") or "contado"
    cuenta_credito = config_contai.get(f"cuenta_credito_{modo}")
    if not cuenta_credito:
        motivos.append(f"Falta la cuenta de crédito configurada para el modo de pago \"{modo}\".")

    if not config_contai.get("comprobante"):
        motivos.append("Falta el comprobante configurado para Contai.")

    return motivos


def construir_movimientos(factura: dict, config_contai: dict, tipos_cuenta: dict[str, str] | None = None) -> dict:
    """Devuelve `{"filas": list[dict] | None, "motivos_bloqueo": list[str]}`.
    `filas` es `None` si hay motivos de bloqueo. Cada fila trae exactamente
    las claves de `COLUMNAS_MOVIMIENTO`, en ese orden.

    `tipos_cuenta` es el mapa código -> Tipo de Cuenta del plan de cuentas de
    Contai ya importado (ver docs/03-ingesta-dian, tipos N/S/C/B/A -- el
    usuario aportó la referencia oficial de Contai). Decide si CADA línea
    lleva NIT, no una regla fija por posición: "S" (Saldos Discriminados por
    Nit), "C" (por Nit y Docto) y "B" (cuentas de impuestos/retenciones)
    necesitan el NIT para que Contai discrimine el saldo por tercero; "N"
    (Saldo Global) y "A" (activos fijos) no. Confirmado contra un
    contai_movimientos.xlsx real generado por el propio Contai (agosto
    2026): la cuenta de IVA (tipo B en el plan de cuentas real del usuario)
    SÍ trae NIT en cada fila, igual que las cuentas de gasto -- solo la
    cuenta de caja (tipo N) queda sin NIT. También confirmado por el
    usuario: su cuenta de crédito 130505 es tipo C, así que SÍ debe llevar
    NIT -- antes esta función se lo quitaba a ciegas a la última línea (la
    de caja/proveedores) sin mirar su tipo. Si `tipos_cuenta` no trae la
    cuenta (plan de cuentas no importado, o cuenta no encontrada en él), esa
    línea queda sin NIT -- no se inventa.

    El GASTO ya no se arma una fila por ítem/producto -- se agrupa por
    tarifa de IVA (ver `_categoria_gasto_de_linea`), usando
    `cuentas_gasto_por_categoria`, exactamente con el mismo espíritu que ya
    tenía el IVA (una fila por tarifa, no por línea). Pedido explícito del
    contador de la empresa (agosto 2026): "los asientos los hace en base a
    los totales y no en base a cada ítem", para que el movimiento no quede
    demasiado extenso. `item["cuenta_contable"]` ya NO se usa acá -- sigue
    existiendo en la bandeja de revisión, pero no participa en la
    exportación a Contai.

    Si una línea trae un impuesto especial (no IVA, no retención -- ej.
    Impuesto al Consumo, IBUA/ICUI), su valor NO se suma a la base ni se
    pierde: genera su propia fila de débito aparte, agrupada por tipo de
    impuesto, usando `cuentas_impuesto_por_tipo` -- confirmado
    explícitamente por el usuario (agosto 2026: "va aparte, fila propia").
    Sin esto, el valor de ese impuesto no quedaría en ninguna fila y el
    asiento reflejaría un monto menor al real de la factura."""
    motivos = _motivos_bloqueo(factura, config_contai)
    if motivos:
        return {"filas": None, "motivos_bloqueo": motivos}

    tipos_cuenta = tipos_cuenta or {}
    comprobante = config_contai["comprobante"]
    fecha = _fecha_mmddyyyy(factura["fecha_emision"])
    documento = factura.get("numero_factura") or ""
    nit_proveedor = factura["proveedor_nit"]
    detalle = _detalle_del_mes(factura["fecha_emision"])
    cuentas_iva = {_normalizar_tarifa_iva(k): v for k, v in (config_contai.get("cuentas_iva_por_tarifa") or {}).items()}
    cuentas_gasto = config_contai.get("cuentas_gasto_por_categoria") or {}
    cuentas_impuesto = config_contai.get("cuentas_impuesto_por_tipo") or {}
    cuentas_retencion = config_contai.get("cuentas_retencion_por_tipo") or {}
    # Cada factura puede pagarse distinto aunque la empresa tenga un modo de
    # pago por defecto (ver orquestador.actualizar_factura) -- por eso se
    # mira primero la factura, y solo si no trae nada explícito se usa el
    # default de la empresa.
    modo = factura.get("modo_pago_contai") or config_contai.get("modo_pago_default") or "contado"
    cuenta_credito = config_contai[f"cuenta_credito_{modo}"]

    def _fila(cuenta, tipo, valor, base=0.0):
        lleva_nit = (tipos_cuenta.get(cuenta) or "").upper() in ("S", "C", "B")
        return {
            "Cuenta": cuenta, "Comprobante": comprobante, "Fecha(mm/dd/yyyy)": fecha,
            "Documento": documento, "Documento Ref.": documento,
            "NIT": nit_proveedor if lleva_nit else "",
            "Detalle": detalle, "Tipo": tipo, "Valor": round(valor, 2), "Base": round(base, 2),
            "Centro de Costo": "", "Trans. Ext": "", "Plazo": 0, "Docto Electrónico": "",
        }

    filas: list[dict] = []
    total_debitos = 0.0
    base_por_categoria: dict[str, float] = {}
    iva_por_tarifa: dict[str, float] = {}
    base_iva_por_tarifa: dict[str, float] = {}
    impuesto_por_tipo: dict[str, float] = {}
    base_impuesto_por_tipo: dict[str, float] = {}
    retencion_por_tipo: dict[str, float] = {}
    base_retencion_por_tipo: dict[str, float] = {}

    for item in factura["items"]:
        cantidad = float(item["cantidad"])
        precio = float(item["valor_unitario"])
        descuento = float(item.get("descuento_monto") or 0)
        base_linea = cantidad * precio - descuento

        if _tiene_base_propia(item):
            categoria = _categoria_gasto_de_linea(item)
            base_por_categoria[categoria] = base_por_categoria.get(categoria, 0.0) + base_linea

        for imp in item.get("impuestos", []):
            tipo_imp = imp.get("tipo") or ""
            if tipo_imp.upper() == "IVA":
                tarifa = _normalizar_tarifa_iva(imp["porcentaje"])
                iva_por_tarifa[tarifa] = iva_por_tarifa.get(tarifa, 0.0) + imp["valor"]
                base_iva_por_tarifa[tarifa] = base_iva_por_tarifa.get(tarifa, 0.0) + base_linea
            elif tipo_imp in _TIPOS_RETENCION:
                retencion_por_tipo[tipo_imp] = retencion_por_tipo.get(tipo_imp, 0.0) + imp["valor"]
                base_retencion_por_tipo[tipo_imp] = base_retencion_por_tipo.get(tipo_imp, 0.0) + base_linea
            elif tipo_imp:
                impuesto_por_tipo[tipo_imp] = impuesto_por_tipo.get(tipo_imp, 0.0) + imp["valor"]
                base_impuesto_por_tipo[tipo_imp] = base_impuesto_por_tipo.get(tipo_imp, 0.0) + base_linea

    for categoria in sorted(base_por_categoria):
        filas.append(_fila(cuentas_gasto[categoria], DEBITO, base_por_categoria[categoria]))
        total_debitos += base_por_categoria[categoria]

    for tarifa in sorted(iva_por_tarifa):
        filas.append(_fila(cuentas_iva[tarifa], DEBITO, iva_por_tarifa[tarifa], base_iva_por_tarifa[tarifa]))
        total_debitos += iva_por_tarifa[tarifa]

    for tipo in sorted(impuesto_por_tipo):
        filas.append(_fila(cuentas_impuesto[tipo], DEBITO, impuesto_por_tipo[tipo], base_impuesto_por_tipo[tipo]))
        total_debitos += impuesto_por_tipo[tipo]

    total_creditos_retencion = 0.0
    for tipo in sorted(retencion_por_tipo):
        filas.append(_fila(
            cuentas_retencion[tipo], CREDITO, retencion_por_tipo[tipo], base_retencion_por_tipo[tipo],
        ))
        total_creditos_retencion += retencion_por_tipo[tipo]

    valor_credito_final = round(total_debitos - total_creditos_retencion, 2)
    filas.append(_fila(cuenta_credito, CREDITO, valor_credito_final))

    suma_debitos = round(sum(f["Valor"] for f in filas if f["Tipo"] == DEBITO), 2)
    suma_creditos = round(sum(f["Valor"] for f in filas if f["Tipo"] == CREDITO), 2)
    assert suma_debitos == suma_creditos, (
        f"Asiento descuadrado para factura {documento}: débitos={suma_debitos} créditos={suma_creditos} "
        "-- esto es un bug de construir_movimientos, nunca del usuario."
    )

    return {"filas": filas, "motivos_bloqueo": []}


def construir_tercero_nuevo(tercero: dict) -> dict:
    """Arma una fila del maestro de terceros de Contai (16 columnas) desde
    los datos del emisor extraídos del XML (`dian_parser.extraer_tercero` /
    `orquestador._extraer_tercero_de_origen` -- mismo dict que ya usa la
    creación de terceros en Siigo). Lo que el XML no trae, va vacío o con un
    valor por defecto -- confirmado explícitamente con el usuario que así
    lo acepta Contai al importar.

    "Tipo" confirmado por el usuario contra su propio Contai: "A" para
    terceros identificados con NIT (jurídica), "C" para cédula (natural) --
    coincide con `id_type` (31=NIT, cualquier otro=cédula). "Ciudad" es el
    nombre literal (`cbc:CityName` del XML, junto al código DANE) y
    "Municipio" es el código DANE de esa ciudad (`ciudad_codigo`, ej.
    "05001" para Medellín) -- son dos columnas distintas, no confundir.
    "Pais" fijo en "169" (código interno de Colombia en Contai, confirmado
    por el usuario) -- todo lo que procesa este sistema es facturación DIAN
    colombiana."""
    es_juridica = tercero.get("id_type") == "31"
    return {
        "NIT": tercero.get("nit") or "",
        "Tipo": "A" if es_juridica else "C",
        "Nombre": (tercero.get("nombre") or "SIN NOMBRE").strip(),
        "Direccion": tercero.get("direccion") or "",
        "Ciudad": tercero.get("ciudad_nombre") or "",
        "Telefono": tercero.get("telefono") or "",
        "Municipio": tercero.get("ciudad_codigo") or "",
        "Activo": "S",
        "Tiene RUT": "N",
        "Pais": "169",
        "Email": tercero.get("correo") or "",
        "Celular": "",
        "Plazo": 0,
        "Actividad Económica": "",
        "Indicativo": "",
        "Naturaleza": "J" if es_juridica else "N",
    }
