"""
Arma el payload real de `POST /v1/purchases` a partir de una factura ya
resuelta (tal como la devuelve `orquestador.listar_facturas`) -- función
pura, sin I/O, para que sea fácil de probar y de mostrar en el modal de
confirmación antes de enviar nada de verdad (CLAUDE.md regla 3: nunca se
envía a Siigo sin confirmación explícita).

Estructura confirmada contra el aplicativo anterior del usuario ("AXON"
original, `C:\\Users\\User\\Desktop\\Automatizar\\core\\enviar_siigo.py` /
`enviar_siigo_individual.py`), que ya tiene 2212 compras reales
sincronizadas en la cuenta de Hielo Super-Cool -- más confiable que
developers.siigo.com, que ni siquiera aclara si `items.taxes` acepta
retenciones. Confirmado empíricamente: sí las acepta, en el mismo array que
el IVA, cada una como `{"id", "value"}`. No existe ni hace falta un campo
`retentions` a nivel de documento -- cada línea puede llevar su propia
retención (o ninguna), de forma independiente.
"""

from __future__ import annotations

# Respaldo cuando el folio DIAN viene sin prefijo (puramente numérico) --
# Siigo exige provider_invoice.prefix no vacío ("parameter_required",
# confirmado real: factura 47357 de FERRECANTOS S.A.S). Mismo valor que ya
# usaba el aplicativo anterior del usuario (enviar_siigo.py/
# enviar_siigo_individual.py, 2212 compras reales), lo que además significa
# que las facturas históricas sin prefijo que causó ese app viejo también
# quedaron con este mismo prefijo en Siigo -- orquestador._factura_proveedor_de
# debe usar esta misma constante para que el antidúplicados y "Ver en Siigo"
# reconstruyan la llave real, no una distinta a la que de verdad se envía.
PREFIJO_RESPALDO = "FC"


def _catalogo_por_id(catalogo_taxes: list[dict]) -> dict[str, dict]:
    return {str(t["id"]): t for t in catalogo_taxes}


def _valor_iva_declarado(item: dict) -> float | None:
    """Monto de IVA tal como lo declaró el XML del proveedor para este ítem
    (`detalle_impuestos`, preservado tal cual por `dian_parser`) -- se
    prefiere sobre recalcularlo por porcentaje, para no introducir una
    diferencia de redondeo contra lo que dice la factura del proveedor.
    `None` si el ítem no trae un impuesto de tipo IVA declarado (ítems
    inyectados por política de empresa, o sin IVA)."""
    for impuesto in item.get("impuestos", []):
        tipo = (impuesto.get("tipo") or "").upper()
        if "IVA" in tipo:
            return impuesto.get("valor")
    return None


def _motivos_bloqueo(factura: dict) -> list[str]:
    """Nunca se arma un payload a medias -- si falta algo, se lista el
    motivo para que el usuario lo complete desde el panel de detalle antes
    de reintentar. No se adivina nada."""
    motivos = []
    if not factura.get("tipo_comprobante_id"):
        motivos.append("Falta el tipo de comprobante en la cabecera.")
    if not factura.get("medio_pago_id"):
        motivos.append("Falta el medio de pago en la cabecera.")
    sin_cuenta = [it for it in factura["items"] if not it.get("cuenta_contable")]
    if sin_cuenta:
        plural = "línea" if len(sin_cuenta) == 1 else "líneas"
        motivos.append(f"{len(sin_cuenta)} {plural} sin cuenta contable asignada.")
    return motivos


def construir_payload(factura: dict, catalogo_taxes: list[dict]) -> dict:
    """Devuelve `{"payload": dict | None, "motivos_bloqueo": list[str]}`.
    `payload` es `None` si hay motivos de bloqueo."""
    motivos = _motivos_bloqueo(factura)
    if motivos:
        return {"payload": None, "motivos_bloqueo": motivos}

    catalogo = _catalogo_por_id(catalogo_taxes)
    items_payload = []
    suma_base_mas_iva = 0.0
    suma_retenciones = 0.0

    for item in factura["items"]:
        cantidad_original = float(item["cantidad"])
        precio_original = float(item["valor_unitario"])
        descuento = round(float(item.get("descuento_monto") or 0), 2)

        # Siigo solo acepta 2 decimales en quantity. Con cantidades tipo
        # 1.228 (comunes en datos reales), redondear la cantidad SIN ajustar
        # el precio infla el total (1.23 * precio original ≠ total real de la
        # línea) más allá de la tolerancia de reintento (±5 pesos). Igual que
        # el aplicativo anterior (enviar_siigo_individual.py): el precio se
        # deriva del total real de la línea dividido por la cantidad ya
        # redondeada, para que quantity*price reproduzca el total del XML.
        cantidad = round(cantidad_original, 2)
        total_bruto_linea = cantidad_original * precio_original
        precio = round(total_bruto_linea / cantidad, 4) if cantidad else precio_original
        base_linea = cantidad * precio - descuento

        taxes_payload = []

        if item.get("iva_tax_id"):
            valor_iva = _valor_iva_declarado(item)
            if valor_iva is None:
                tax = catalogo.get(str(item["iva_tax_id"]))
                porcentaje = float(tax["percentage"]) if tax and tax.get("percentage") is not None else 0.0
                valor_iva = base_linea * porcentaje / 100.0
            valor_iva = round(valor_iva, 2)
            taxes_payload.append({"id": int(item["iva_tax_id"]), "value": valor_iva})
            suma_base_mas_iva += valor_iva

        if item.get("retencion_tax_id"):
            # La retención SIEMPRE se calcula -- el XML del proveedor no
            # declara la retención que aplica el comprador, así que no hay
            # un valor "oficial" que reusar como sí pasa con el IVA.
            tax = catalogo.get(str(item["retencion_tax_id"]))
            porcentaje = float(tax["percentage"]) if tax and tax.get("percentage") is not None else 0.0
            valor_retencion = round(base_linea * porcentaje / 100.0, 2)
            taxes_payload.append({"id": int(item["retencion_tax_id"]), "value": valor_retencion})
            suma_retenciones += valor_retencion

        suma_base_mas_iva += base_linea

        item_payload = {
            "type": item.get("tipo_item") or "Account",
            "code": item["cuenta_contable"],
            "description": (item["descripcion"] or "")[:50],
            "quantity": cantidad,
            "price": precio,
            "taxes": taxes_payload,
        }
        if descuento > 0:
            item_payload["discount"] = descuento
        items_payload.append(item_payload)

    # payments[0].value NO es total_pagar_xml tal cual -- Siigo valida ese
    # valor contra su propio cálculo interno (subtotal + IVA - retenciones),
    # y total_pagar_xml nunca viene neteado de retenciones (ver
    # docs/05-esquema-datos/modelo-datos.md). Se reconstruye desde las
    # líneas ya armadas, como hace enviar_siigo_individual.py del app
    # anterior (más principiado que sumar columnas heredadas).
    total_neto = round(suma_base_mas_iva - suma_retenciones, 2)

    payload = {
        "document": {"id": int(factura["tipo_comprobante_id"])},
        "date": factura["fecha_emision"],
        "supplier": {"identification": factura["proveedor_nit"], "branch_office": 0},
        "provider_invoice": {
            # Siigo exige provider_invoice.prefix no vacío ("parameter_required",
            # confirmado real: factura 47357 de FERRECANTOS S.A.S, folio DIAN
            # puramente numérico sin prefijo). Mismo respaldo que el aplicativo
            # anterior del usuario (enviar_siigo.py/enviar_siigo_individual.py,
            # ya validado en 2212 compras reales): "FC" si el XML no trae prefijo.
            "prefix": factura.get("prefijo") or PREFIJO_RESPALDO,
            "number": factura.get("numero_puro") or factura.get("numero_factura") or "",
        },
        "observations": f"Causado automáticamente por AXON. Proveedor: {factura['proveedor_nombre']}",
        "discount_type": "Value",
        "items": items_payload,
        "payments": [{
            "id": int(factura["medio_pago_id"]),
            "value": total_neto,
            "due_date": factura["fecha_emision"],
        }],
    }
    return {"payload": payload, "motivos_bloqueo": []}
