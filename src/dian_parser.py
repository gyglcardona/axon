"""
Parser de facturas electrónicas de la DIAN (UBL 2.1).

Cero tokens de IA -- es código determinístico puro (ver docs/00-contexto/
decisiones-arquitectura.md, "Extracción de XML sin IA").

Corrige explícitamente los bugs confirmados en el parser anterior
(ver docs/01-hallazgos-fase0/bugs-parser-anterior.md):

1. Suma TODOS los nodos TaxTotal de la cabecera, no solo el primero.
2. Suma TODOS los TaxSubtotal de cada línea, no solo el primero.
3. Separa impuestos de cabecera de impuestos de línea (evita doble conteo).
4. Usa LineExtensionAmount como respaldo cuando TaxExclusiveAmount viene en 0.
5. Nunca asume que PayableAmount ya viene neto de retenciones -- se reportan
   por separado y quien decide cómo combinarlos es el motor de reglas, no el parser.

Salida: un diccionario con "impuestos por línea" (una entrada por impuesto
aplicado), siguiendo la decisión de arquitectura de no usar columnas fijas
por tipo de impuesto (ver docs/00-contexto/decisiones-arquitectura.md).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "sts": "dian:gov:co:facturaelectronica:Structures-2-1",
}

# Mapeo de código de esquema de impuesto (cac:TaxScheme/cbc:ID) a nombre.
# PENDIENTE DE CONFIRMAR contra XML reales de las 5 empresas -- estos son los
# códigos estándar más comunes en implementaciones DIAN, pero no están
# verificados contra una factura real todavía. Si un código no aparece aquí,
# el parser lo reporta igual bajo su ID crudo en vez de fallar.
TAX_SCHEME_MAP = {
    "01": "IVA",
    "02": "IC",       # Impuesto al Consumo
    "03": "ICA",
    "04": "INC",
    "05": "ReteIVA",
    "06": "ReteFuente",
    "07": "ReteICA",
    "20": "INC",
    "22": "ICUI",
    # Confirmado contra XML real (NABOR ABAD GARCIA HOYOS, factura
    # FVDM6616953, agosto 2026): cac:TaxScheme/cbc:ID=34 trae
    # cbc:Name="IBUA" en el propio XML del proveedor -- sin este mapeo,
    # _clasificar_esquema lo reportaba como "ESQUEMA_34".
    "34": "IBUA",
}


@dataclass
class ImpuestoLinea:
    tipo: str                  # "IVA", "IC", "ReteFuente", etc. (ver TAX_SCHEME_MAP)
    codigo_esquema: str        # código crudo del XML, por si TAX_SCHEME_MAP no lo cubre
    base: float
    porcentaje: float
    valor: float
    nivel: str                 # "documento" | "linea"
    linea_id: str | None = None    # None si es a nivel de documento


@dataclass
class LineaFactura:
    id_linea: str
    descripcion: str
    cantidad: float
    valor_unitario: float
    total_linea: float
    descuento_porcentaje: float
    descuento_monto: float
    impuestos: list[ImpuestoLinea] = field(default_factory=list)


@dataclass
class FacturaDian:
    cufe: str | None
    numero_factura: str
    prefijo: str
    numero_puro: str
    fecha_emision: str
    proveedor_nombre: str
    proveedor_nit: str
    proveedor_correo: str | None
    proveedor_direccion: str | None
    subtotal_xml: float                # TaxExclusiveAmount (o LineExtensionAmount si viene en 0)
    subtotal_fuente: str                # "TaxExclusiveAmount" | "LineExtensionAmount (respaldo)"
    total_pagar_xml: float              # PayableAmount, TAL COMO VIENE -- no se asume neto de nada
    impuestos_documento: list[ImpuestoLinea] = field(default_factory=list)
    lineas: list[LineaFactura] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)
    # Códigos DIAN de responsabilidad fiscal del PROVEEDOR (cac:PartyTaxScheme/
    # cbc:TaxLevelCode, ej. "O-13" = Gran Contribuyente, "O-15" = Autorretenedor
    # -- confirmado contra facturas reales de QUALA/KOPPS/COMMERK, que traen
    # varios códigos juntos separados por ";"). Sirve para marcar el perfil de
    # proveedor automáticamente al importar, ver orquestador._marcar_perfil_fiscal_automatico.
    responsabilidades_fiscales: list[str] = field(default_factory=list)
    # NIT del receptor/comprador (cac:AccountingCustomerParty) -- None si el
    # XML no trae ese bloque (pasa con algunos fixtures/proveedores viejos,
    # que no deben dejar de importarse por esto). Sirve para validar en
    # orquestador.ejecutar_importar que la factura sea realmente para la
    # empresa a la que se está importando, y no se cuele una factura de otra
    # empresa por una carpeta/subida equivocada (ver
    # docs/06-multiempresa-saas/aislamiento-datos.md).
    receptor_nit: str | None = None

    def total_por_tipo(self, tipo: str) -> float:
        """Suma un tipo de impuesto (ej. 'IVA') across documento + todas las líneas.

        ⚠ TRAMPA CONFIRMADA (bug real en producción, ver motor_reglas.
        _total_iva_sin_duplicar): en facturas DIAN reales con IVA declarado
        por línea, el TaxTotal de nivel documento casi siempre es la SUMA de
        esos mismos valores de línea, no un impuesto aparte -- sumar ambos
        (como hace este método) duplica el total. Úsalo solo cuando sepas
        que documento y líneas no se solapan (ej. fixtures sintéticos donde
        el IVA está a un solo nivel); para cualquier caso real con líneas,
        usa `_total_iva_sin_duplicar` como referencia en vez de este método."""
        total = sum(i.valor for i in self.impuestos_documento if i.tipo == tipo)
        for linea in self.lineas:
            total += sum(i.valor for i in linea.impuestos if i.tipo == tipo)
        return total


def _texto(nodo, xpath: str) -> str | None:
    encontrado = nodo.find(xpath, NS)
    return encontrado.text.strip() if encontrado is not None and encontrado.text else None


def _float(nodo, xpath: str, default: float = 0.0) -> float:
    txt = _texto(nodo, xpath)
    if txt is None:
        return default
    try:
        return float(txt)
    except ValueError:
        return default


def _desenvolver_si_es_attached_document(xml_bytes: bytes) -> bytes:
    """Varios proveedores (confirmado con Agencia Exequiales del Ayer, XML
    real de agosto 2026 -- proveedor factura vía edocnube.com) no entregan
    el Invoice/CreditNote directo: lo envuelven en un 'AttachedDocument' (el
    sobre firmado que exige la DIAN para la entrega), con el documento real
    completo embebido como CDATA en
    cac:Attachment/cac:ExternalReference/cbc:Description. Sin desenvolver
    esto antes de mirar el tipo de documento o el CUFE, `tipo_documento` ve
    'AttachedDocument' y TODAS las facturas de ese proveedor se pierden en
    silencio (se descartan como "no es una factura de compra", ver
    zip_handler.descubrir_documentos) -- no fallan, simplemente nunca
    llegan a la bandeja.

    Se llama al principio de cada función pública de este módulo (no solo
    en zip_handler) porque hay más de un lugar que relee el XML original
    desde el ZIP -- ver orquestador._extraer_tercero_de_origen. Si el XML no
    es un AttachedDocument (el caso normal), lo devuelve intacto."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return xml_bytes
    if root.tag.rsplit("}", 1)[-1] != "AttachedDocument":
        return xml_bytes
    descripcion = root.find(".//cac:Attachment/cac:ExternalReference/cbc:Description", NS)
    if descripcion is None or not descripcion.text:
        return xml_bytes
    return descripcion.text.strip().encode("utf-8")


def tipo_documento(xml_bytes: bytes) -> str:
    """Nombre del elemento raíz sin namespace (ej. 'Invoice', 'ApplicationResponse',
    'CreditNote'). La DIAN entrega en el mismo formato de ZIP/carpeta documentos
    que no son facturas de compra -- ej. acuses de recibo (`ApplicationResponse`),
    igual que el listado del portal trae 'Nomina Individual' u otros tipos que hay
    que filtrar (ver docs/03-ingesta-dian/validador-completitud.md). Solo 'Invoice'
    se puede pasar por `parsear_factura`; el resto debe descartarse explícitamente
    antes, nunca fallar a mitad del parseo."""
    xml_bytes = _desenvolver_si_es_attached_document(xml_bytes)
    root = ET.fromstring(xml_bytes)
    return root.tag.rsplit("}", 1)[-1]


def extraer_cufe(xml_bytes: bytes) -> str | None:
    """Lee el CUFE/CUDE (cbc:UUID) sin hacer el parseo completo de la factura.

    Es la llave de identificación única del documento ante la DIAN (ver
    docs/03-ingesta-dian/validador-completitud.md) -- se usa tanto para el
    cruce contra el listado de la DIAN como para deduplicar documentos
    entregados más de una vez (ver src/zip_handler.py).
    """
    xml_bytes = _desenvolver_si_es_attached_document(xml_bytes)
    root = ET.fromstring(xml_bytes)
    return _texto(root, ".//cbc:UUID")


def extraer_tercero(xml_bytes: bytes) -> dict | None:
    """Datos del emisor (proveedor) necesarios para CREARLO como tercero en
    Siigo cuando su NIT todavía no existe allá -- el XML de la DIAN trae
    todo lo que Siigo exige (dirección con códigos DANE de ciudad y
    departamento, responsabilidad fiscal, contacto), así que no hay que
    pedirle nada al usuario. Devuelve None si el XML no tiene el bloque de
    emisor (no debería pasar en una factura ya importada)."""
    xml_bytes = _desenvolver_si_es_attached_document(xml_bytes)
    root = ET.fromstring(xml_bytes)
    party = root.find(".//cac:AccountingSupplierParty/cac:Party", NS)
    if party is None:
        return None

    company_id = party.find(".//cac:PartyTaxScheme/cbc:CompanyID", NS)
    direccion_node = party.find(".//cac:PhysicalLocation/cac:Address", NS)
    if direccion_node is None:
        direccion_node = party.find(".//cac:PartyTaxScheme/cac:RegistrationAddress", NS)

    return {
        "nombre": _texto(party, ".//cac:PartyLegalEntity/cbc:RegistrationName") or "SIN NOMBRE",
        "nit": (company_id.text or "").strip() if company_id is not None else "",
        # schemeID = dígito de verificación, schemeName = tipo de documento
        # DIAN ("31" = NIT, "13" = cédula) -- coinciden con los id_type de Siigo.
        "digito_verificacion": company_id.get("schemeID") if company_id is not None else None,
        "id_type": company_id.get("schemeName") if company_id is not None else None,
        "direccion": _texto(direccion_node, ".//cac:AddressLine/cbc:Line") if direccion_node is not None else None,
        "ciudad_codigo": _texto(direccion_node, "cbc:ID") if direccion_node is not None else None,
        "ciudad_nombre": _texto(direccion_node, "cbc:CityName") if direccion_node is not None else None,
        "departamento_codigo": _texto(direccion_node, "cbc:CountrySubentityCode") if direccion_node is not None else None,
        "correo": _texto(party, ".//cac:Contact/cbc:ElectronicMail"),
        "telefono": _texto(party, ".//cac:Contact/cbc:Telephone"),
        "tax_level_code": _texto(party, ".//cac:PartyTaxScheme/cbc:TaxLevelCode"),
    }


def _clasificar_esquema(codigo: str | None) -> str:
    if codigo is None:
        return "DESCONOCIDO"
    return TAX_SCHEME_MAP.get(codigo.strip(), f"ESQUEMA_{codigo.strip()}")


def _parsear_tax_subtotals(nodo_padre, xpath: str, nivel: str, linea_id: str | None) -> list[ImpuestoLinea]:
    """Recorre TODOS los TaxSubtotal bajo nodo_padre (corrige el bug #1/#2 del parser anterior)."""
    resultado = []
    for subtotal in nodo_padre.findall(xpath, NS):
        codigo_esquema = _texto(subtotal, ".//cac:TaxScheme/cbc:ID")
        tipo = _clasificar_esquema(codigo_esquema)
        base = _float(subtotal, "cbc:TaxableAmount")
        valor = _float(subtotal, "cbc:TaxAmount")
        porcentaje = _float(subtotal, ".//cbc:Percent")
        resultado.append(ImpuestoLinea(
            tipo=tipo,
            codigo_esquema=codigo_esquema or "N/A",
            base=base,
            porcentaje=porcentaje,
            valor=valor,
            nivel=nivel,
            linea_id=linea_id,
        ))
    return resultado


def parsear_factura(xml_bytes: bytes) -> FacturaDian:
    """Parsea el XML de una factura electrónica DIAN (UBL 2.1) ya descomprimida."""
    xml_bytes = _desenvolver_si_es_attached_document(xml_bytes)
    root = ET.fromstring(xml_bytes)
    advertencias: list[str] = []

    cufe = _texto(root, ".//cbc:UUID")
    factura_id = _texto(root, ".//cbc:ID") or ""
    fecha = _texto(root, ".//cbc:IssueDate") or ""

    prov_node = root.find(".//cac:AccountingSupplierParty/cac:Party", NS)
    if prov_node is None:
        raise ValueError("No se encontró AccountingSupplierParty/Party en el XML")

    proveedor_nombre = _texto(prov_node, ".//cac:PartyLegalEntity/cbc:RegistrationName") or "SIN NOMBRE"
    proveedor_nit = _texto(prov_node, ".//cac:PartyTaxScheme/cbc:CompanyID") or ""
    proveedor_correo = _texto(prov_node, ".//cbc:ElectronicMail")
    proveedor_direccion = _texto(prov_node, ".//cac:RegistrationAddress/cac:AddressLine/cbc:Line")
    tax_level_code = _texto(prov_node, ".//cac:PartyTaxScheme/cbc:TaxLevelCode")
    responsabilidades_fiscales = [c.strip() for c in (tax_level_code or "").split(";") if c.strip()]

    receptor_node = root.find(".//cac:AccountingCustomerParty/cac:Party", NS)
    receptor_nit = _texto(receptor_node, ".//cac:PartyTaxScheme/cbc:CompanyID") if receptor_node is not None else None

    # --- Prefijo / número puro (misma heurística que el parser anterior, que
    #     ya funcionaba bien para esto) ---
    prefijo = _texto(root, ".//sts:AuthorizedInvoices/sts:Prefix") or ""
    if not prefijo:
        for i, char in enumerate(factura_id):
            if char.isdigit():
                prefijo = factura_id[:i]
                break
    numero_puro = factura_id[len(prefijo):] if prefijo and factura_id.startswith(prefijo) else factura_id

    # --- Totales de cabecera ---
    totales_node = root.find(".//cac:LegalMonetaryTotal", NS)
    if totales_node is None:
        raise ValueError("No se encontró LegalMonetaryTotal en el XML")

    total_pagar_xml = _float(totales_node, "cbc:PayableAmount")

    subtotal_xml = _float(totales_node, "cbc:TaxExclusiveAmount")
    subtotal_fuente = "TaxExclusiveAmount"
    if subtotal_xml == 0.0:
        # Hallazgo Fase 0: 2 proveedores traen TaxExclusiveAmount en cero.
        lineas_raw = root.findall(".//cac:InvoiceLine", NS)
        subtotal_xml = sum(_float(l, "cbc:LineExtensionAmount") for l in lineas_raw)
        subtotal_fuente = "LineExtensionAmount (respaldo, TaxExclusiveAmount venía en 0)"
        advertencias.append(
            "TaxExclusiveAmount venía en 0 -- se calculó el subtotal sumando "
            "LineExtensionAmount de todas las líneas (patrón conocido de Fase 0)."
        )

    # --- Impuestos de cabecera: TODOS los TaxTotal de la raíz, no solo el primero ---
    impuestos_documento = []
    nodos_tax_total_raiz = root.findall("./cac:TaxTotal", NS)
    if len(nodos_tax_total_raiz) > 1:
        advertencias.append(
            f"La factura trae {len(nodos_tax_total_raiz)} nodos TaxTotal a nivel de "
            "documento (patrón de IVA multilínea de Fase 0) -- se sumaron todos."
        )
    for tax_total in nodos_tax_total_raiz:
        impuestos_documento.extend(
            _parsear_tax_subtotals(tax_total, "cac:TaxSubtotal", nivel="documento", linea_id=None)
        )

    # --- Retención: se reporta tal cual, el parser NO la resta de nada ---
    total_retencion_xml = 0.0
    rete_node = root.find("./cac:WithholdingTaxTotal", NS)
    if rete_node is not None:
        total_retencion_xml = _float(rete_node, "cbc:TaxAmount")
        if total_retencion_xml > 0:
            advertencias.append(
                "La factura trae WithholdingTaxTotal informado. El parser lo reporta "
                "tal cual -- NO se resta de PayableAmount aquí (ver hallazgo Fase 0: "
                "la retención no siempre viene descontada del total). Esa decisión "
                "es del motor de reglas, no del parser."
            )
        impuestos_documento.append(ImpuestoLinea(
            tipo="Retencion_General",
            codigo_esquema="WithholdingTaxTotal",
            base=0.0,
            porcentaje=0.0,
            valor=total_retencion_xml,
            nivel="documento",
            linea_id=None,
        ))

    # --- Líneas ---
    lineas: list[LineaFactura] = []
    for idx, line in enumerate(root.findall(".//cac:InvoiceLine", NS), start=1):
        id_linea = _texto(line, "cbc:ID") or str(idx)
        desc = _texto(line, ".//cac:Item/cbc:Description") or "ITEM SIN DESCRIPCIÓN PROVEEDOR"
        cantidad = _float(line, "cbc:InvoicedQuantity", default=1.0)
        precio_unitario_xml = _float(line, ".//cac:Price/cbc:PriceAmount")
        total_linea = _float(line, "cbc:LineExtensionAmount")

        desc_porcentaje = 0.0
        desc_monto = 0.0
        allowance = line.find("./cac:AllowanceCharge", NS)
        if allowance is not None:
            indicator = _texto(allowance, "cbc:ChargeIndicator")
            if indicator is None or indicator.lower() == "false":
                desc_porcentaje = _float(allowance, "cbc:MultiplierFactorNumeric")
                desc_monto = _float(allowance, "cbc:Amount")

        # cac:Price/cbc:PriceAmount NO siempre es el precio unitario SIN
        # impuestos -- confirmado con facturas reales (TECNOMEDICA MD,
        # TORACHE, julio 2026): esos proveedores ponen ahí el precio CON IVA
        # incluido, mientras LineExtensionAmount sí es siempre el neto (antes
        # de impuestos, después de descuento). Usar Price tal cual inflaba el
        # ítem enviado a Siigo por el valor del IVA, ADEMÁS de la línea de
        # IVA aparte que agrega la política -- doble conteo, con un origen
        # distinto al ya corregido en motor_reglas. Se reconstruye el precio
        # unitario desde LineExtensionAmount (sumándole el descuento, que ya
        # viene neteado ahí, para no restarlo dos veces más adelante) en vez
        # de confiar en Price; Price solo se usa de respaldo si
        # LineExtensionAmount viene en 0.
        if total_linea != 0 and cantidad:
            precio_unitario = (total_linea + desc_monto) / cantidad
            if abs(precio_unitario - precio_unitario_xml) > 1:
                advertencias.append(
                    f"Línea {id_linea}: cac:Price/cbc:PriceAmount ({precio_unitario_xml:,.2f}) no "
                    f"coincide con el precio unitario neto calculado desde LineExtensionAmount "
                    f"({precio_unitario:,.2f}) -- se usó este último (patrón confirmado: el proveedor "
                    "puso el precio con IVA incluido en el campo Price)."
                )
        else:
            precio_unitario = precio_unitario_xml

        impuestos_linea = _parsear_tax_subtotals(
            line, ".//cac:TaxTotal/cac:TaxSubtotal", nivel="linea", linea_id=id_linea
        )
        if len([t for t in line.findall(".//cac:TaxTotal/cac:TaxSubtotal", NS)]) > 1:
            advertencias.append(
                f"Línea {id_linea} trae más de un TaxSubtotal (ej. IVA + IC en la "
                "misma línea) -- se sumaron/clasificaron todos por separado."
            )

        lineas.append(LineaFactura(
            id_linea=id_linea,
            descripcion=desc,
            cantidad=cantidad,
            valor_unitario=precio_unitario,
            total_linea=total_linea,
            descuento_porcentaje=desc_porcentaje,
            descuento_monto=desc_monto,
            impuestos=impuestos_linea,
        ))

    return FacturaDian(
        cufe=cufe,
        numero_factura=factura_id,
        prefijo=prefijo,
        numero_puro=numero_puro,
        fecha_emision=fecha,
        proveedor_nombre=proveedor_nombre,
        proveedor_nit=proveedor_nit,
        proveedor_correo=proveedor_correo,
        proveedor_direccion=proveedor_direccion,
        receptor_nit=receptor_nit,
        subtotal_xml=subtotal_xml,
        subtotal_fuente=subtotal_fuente,
        total_pagar_xml=total_pagar_xml,
        impuestos_documento=impuestos_documento,
        lineas=lineas,
        advertencias=advertencias,
        responsabilidades_fiscales=responsabilidades_fiscales,
    )
