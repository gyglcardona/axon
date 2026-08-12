"""
Motor de reglas: decide qué hacer con cada factura ya parseada, ANTES de que
Claude vea nada (ver CLAUDE.md, "Reglas duras de arquitectura").

Jerarquía (ver docs/02-reglas-negocio/README.md):
  1. Política de empresa  -- aplica a TODAS las compras de esa empresa cliente.
  2. Perfil de proveedor  -- aplica a un NIT de proveedor específico.
  3. Si nada de lo anterior resuelve la línea, queda para Claude o revisión manual.

Cada factura queda marcada con resuelto_por = "reglas" | "claude" | "historico" |
"manual", para trazabilidad y control de costos. "historico" lo agrega
`orquestador.ejecutar_importar` (no este módulo) cuando `motor_sugerencias`
llena todas las cuentas usando el histórico de compras Siigo o una
preferencia aprendida -- es una sugerencia, no una regla de negocio
confirmada, así que nunca se marca como "reglas".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from dian_parser import FacturaDian


@dataclass
class ItemSiigo:
    """Un ítem tal como se enviaría al payload de Siigo -- todavía sin cuenta
    contable si el motor de reglas no pudo resolverla."""
    descripcion: str
    cantidad: float
    valor_unitario: float
    cuenta_contable: str | None
    tipo_item: str = "Account"
    impuestos: list[dict] = field(default_factory=list)
    origen: str = "xml"          # "xml" | "politica_empresa" | "otros_impuestos" (ver más abajo)
    # id (`taxes.id` en catalogos_siigo) del IVA/retefuente elegido para esta
    # línea -- lo llena `motor_sugerencias`, nunca el motor de reglas (que no
    # tiene acceso a la base de datos). `None` = sin código todavía / no
    # aplica (ver docs de la feature: la retención puede quedar sin código a
    # propósito, nunca se inventa uno).
    iva_tax_id: str | None = None
    retencion_tax_id: str | None = None
    # AllowanceCharge de la línea (dian_parser.LineaFactura.descuento_monto)
    # -- si no se propaga hasta el payload de Siigo, el total enviado queda
    # inflado por el monto del descuento (confirmado con datos reales:
    # facturas de julio de Hielo Super-Cool sí traen líneas con descuento).
    descuento_monto: float = 0.0


@dataclass
class ResultadoClasificacion:
    factura: FacturaDian
    items: list[ItemSiigo]
    resuelto_por: str             # "reglas" | "claude" | "historico" | "manual"
    notas: list[str] = field(default_factory=list)
    # id (`document_types.id` / `payment_types.id` en catalogos_siigo) sugerido
    # para la cabecera de la factura -- igual que arriba, lo llena
    # `motor_sugerencias` en orquestador.ejecutar_importar.
    tipo_comprobante_id: str | None = None
    medio_pago_id: str | None = None


def _cargar_config(ruta: Path) -> dict:
    if not ruta.exists():
        return {}
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def cargar_config_empresa(nit_empresa: str, base_dir: Path = Path("config/empresas")) -> dict:
    return _cargar_config(base_dir / f"{nit_empresa}.json")


def cargar_config_proveedor(nit_proveedor: str, base_dir: Path = Path("config/proveedores")) -> dict:
    return _cargar_config(base_dir / f"{nit_proveedor}.json")


def _total_iva_sin_duplicar(factura: FacturaDian) -> float:
    """Suma el IVA de la factura sin el doble conteo confirmado en datos
    reales de Hielo Super-Cool (ej. GL30644, julio 2026): cuando el XML
    declara IVA por línea, el TaxTotal de nivel documento no es un impuesto
    adicional -- es la SUMA de esos mismos valores de línea (confirmado:
    coincide siempre, sin excepción, en las 64 facturas de julio que traen
    IVA por línea). `FacturaDian.total_por_tipo` suma documento + líneas sin
    saber esto, así que NO se usa aquí -- sumarlos daría el doble. Se usa el
    total de líneas cuando existe; el de documento solo cuando ninguna línea
    trae su propio IVA (factura con IVA declarado solo a nivel de documento,
    ver test_lineas_se_parsean_correctamente)."""
    total_lineas = sum(i.valor for linea in factura.lineas for i in linea.impuestos if i.tipo == "IVA")
    if total_lineas > 0:
        return total_lineas
    return sum(i.valor for i in factura.impuestos_documento if i.tipo == "IVA")


def _aplicar_politica_iva_no_discriminado(factura: FacturaDian, politica: dict) -> tuple[list[ItemSiigo], list[str]]:
    """
    Implementa docs/02-reglas-negocio/politicas-empresa/901528790-hielo-super-cool-iva-no-discriminado.md

    En vez de mapear el IVA de cada línea al bloque de impuestos de Siigo, se
    causa como línea (ítem) adicional, sin impuestos asociados.
    """
    notas = []
    comportamiento = politica.get("comportamiento", {})
    descripcion_item = comportamiento.get("descripcion_item", "IVA no discriminado")

    total_iva = _total_iva_sin_duplicar(factura)

    items: list[ItemSiigo] = []
    for linea in factura.lineas:
        # El IVA nunca va aquí -- ya se movió aparte, arriba, como
        # `total_iva`. Cualquier OTRO impuesto de la línea (ej. IC, INC --
        # confirmado real: SKY CORD $70 de IC, COMCEL $673,62 de INC) SÍ se
        # conserva, para que `_extraer_otros_impuestos` (que corre después,
        # en `clasificar_factura`, para ambos caminos) lo agrupe en "OTROS
        # IMPUESTOS" en vez de perderlo silenciosamente -- antes se
        # descartaba TODO el bloque de impuestos de la línea, no solo el IVA.
        impuestos_no_iva = [
            {"tipo": i.tipo, "porcentaje": i.porcentaje, "valor": i.valor}
            for i in linea.impuestos if i.tipo != "IVA"
        ]
        items.append(ItemSiigo(
            descripcion=linea.descripcion,
            cantidad=linea.cantidad,
            valor_unitario=linea.valor_unitario,
            cuenta_contable=None,  # el motor de reglas de proveedor / Claude la completa
            impuestos=impuestos_no_iva,
            origen="xml",
            descuento_monto=linea.descuento_monto,
        ))

    if total_iva > 0:
        items.append(ItemSiigo(
            descripcion=descripcion_item,
            cantidad=1,
            valor_unitario=total_iva,
            # Confirmado por la contadora: esta línea NUNCA lleva una cuenta
            # fija propia -- debe heredar la misma cuenta contable de la(s)
            # línea(s) de gasto (origen="xml") de este mismo documento, no
            # una cuenta "candidata" genérica. Queda en None aquí a
            # propósito; orquestador._aplicar_sugerencias la completa DESPUÉS
            # de resolver la cuenta de las líneas de gasto (y solo si todas
            # comparten una única cuenta -- si el documento mezcla cuentas
            # distintas entre sus líneas, no se adivina cuál usar aquí).
            cuenta_contable=None,
            impuestos=[],
            origen="politica_empresa",
        ))

    return items, notas


# Únicos tipos de impuesto que se quedan pegados al ítem, porque tienen un
# mecanismo propio de resolución de código Siigo más adelante en el pipeline:
# IVA (motor_sugerencias resuelve iva_tax_id) y las retenciones (resuelven
# retencion_tax_id). Cualquier otro tipo -- "ESQUEMA_<código>" (esquema que
# dian_parser no supo nombrar) o un tipo SÍ reconocido pero sin mecanismo de
# código Siigo propio (confirmado real: IC $70 en factura de SKY CORD, INC
# $673,62 en factura de COMCEL) -- se pierde silenciosamente si se deja en
# el bloque de impuestos del ítem, porque nada en el pipeline sabe qué
# código de Siigo ponerle.
_TIPOS_IMPUESTO_CON_CODIGO_PROPIO = {"IVA", "ReteIVA", "ReteFuente", "ReteICA", "Retencion_General"}


def _extraer_otros_impuestos(items: list[ItemSiigo]) -> tuple[list[ItemSiigo], dict[str, float]]:
    """Saca de cada línea los impuestos que no tienen forma de resolverse a
    un código de Siigo (ver _TIPOS_IMPUESTO_CON_CODIGO_PROPIO) y devuelve su
    suma agrupada POR TIPO (ej. {"IC": 3360.0, "IBUA": 26400.0}), para que
    `clasificar_factura` los agregue como un ítem aparte en vez de enviarlos
    silenciosamente perdidos -- no existe un código de impuesto Siigo
    confiable para mapearlos uno por uno, así que se causan como línea
    propia (misma cuenta que el resto del documento, cantidad 1).

    Se agrupa por tipo (no en un solo total) porque Contai SÍ necesita saber
    de qué tipo es cada parte -- ver contai_export.cuentas_impuesto_por_tipo
    (confirmado real: una factura de INDUSTRIA NACIONAL DE GASEOSAS con
    Impuesto al Consumo $3.360 + IBUA $26.400 en la misma factura, que antes
    quedaban indistinguibles en un solo ítem "OTROS IMPUESTOS" de $29.760).
    Siigo no se ve afectado por este cambio: sigue recibiendo el mismo total
    combinado como `valor_unitario` del ítem sintético (ver clasificar_factura),
    `siigo_payload.py` nunca lee estos tipos de `item.impuestos`."""
    por_tipo: dict[str, float] = {}
    for item in items:
        conservados = []
        for impuesto in item.impuestos:
            if impuesto["tipo"] in _TIPOS_IMPUESTO_CON_CODIGO_PROPIO:
                conservados.append(impuesto)
            else:
                tipo = impuesto["tipo"]
                por_tipo[tipo] = por_tipo.get(tipo, 0.0) + impuesto["valor"]
        item.impuestos = conservados
    return items, por_tipo


def clasificar_factura(factura: FacturaDian, nit_empresa: str, config_dir: Path = Path("config")) -> ResultadoClasificacion:
    config_empresa = cargar_config_empresa(nit_empresa, config_dir / "empresas")
    config_proveedor = cargar_config_proveedor(factura.proveedor_nit, config_dir / "proveedores")

    notas: list[str] = list(factura.advertencias)  # las advertencias del parser viajan como notas
    items: list[ItemSiigo]

    politicas = config_empresa.get("politicas", {})
    politica_iva = politicas.get("iva_no_discriminado", {})

    if politica_iva.get("activa"):
        items, notas_politica = _aplicar_politica_iva_no_discriminado(factura, politica_iva)
        notas.extend(notas_politica)
    else:
        # Camino genérico: cada línea del XML se convierte en un ítem, con sus
        # impuestos tal como vienen -- todavía sin cuenta contable asignada.
        #
        # Los impuestos pueden venir declarados a nivel de LÍNEA o a nivel de
        # DOCUMENTO (ver hallazgo Fase 0: a veces cada TaxTotal de cabecera
        # corresponde a un ítem distinto, no está anidado dentro del <InvoiceLine>).
        # Si una línea no trae impuestos propios y la factura tiene una sola
        # línea, es seguro atribuirle los impuestos de documento completos.
        # Con más de una línea sin impuestos propios, NO se adivina el reparto
        # -- se deja explícito en las notas para que Claude o un humano decidan.
        impuestos_doc_no_retencion = [
            i for i in factura.impuestos_documento if i.tipo != "Retencion_General"
        ]
        items = []
        for linea in factura.lineas:
            if linea.impuestos:
                impuestos_item = [
                    {"tipo": i.tipo, "porcentaje": i.porcentaje, "valor": i.valor}
                    for i in linea.impuestos
                ]
            elif len(factura.lineas) == 1 and impuestos_doc_no_retencion:
                impuestos_item = [
                    {"tipo": i.tipo, "porcentaje": i.porcentaje, "valor": i.valor}
                    for i in impuestos_doc_no_retencion
                ]
                notas.append(
                    "Los impuestos venían declarados a nivel de documento, no de "
                    "línea. Como la factura tiene una sola línea, se le atribuyeron "
                    "completos -- seguro en este caso, no generalizar a facturas "
                    "multilínea sin una regla explícita de reparto."
                )
            else:
                impuestos_item = []
                if impuestos_doc_no_retencion:
                    notas.append(
                        "Hay impuestos a nivel de documento pero la factura tiene "
                        "varias líneas sin impuestos propios -- no se adivinó el "
                        "reparto entre líneas. Requiere revisión manual o una regla "
                        "explícita antes de enviar a Siigo."
                    )

            items.append(ItemSiigo(
                descripcion=linea.descripcion,
                cantidad=linea.cantidad,
                valor_unitario=linea.valor_unitario,
                cuenta_contable=None,
                impuestos=impuestos_item,
                origen="xml",
                descuento_monto=linea.descuento_monto,
            ))

    # Impuestos sin código de Siigo propio (IC, IBUA, ICUI, o ESQUEMA_<código>
    # que dian_parser no supo nombrar) se sacan de sus líneas y se agrupan en
    # un ítem "OTROS IMPUESTOS" aparte -- cantidad 1, misma cuenta contable
    # que las demás líneas (la resuelve motor_sugerencias por
    # histórico/aprendizaje, igual que cualquier otro ítem). El ítem SÍ
    # conserva el desglose por tipo en su `impuestos` (ej. [{"tipo": "IC",
    # "valor": 3360.0}, {"tipo": "IBUA", "valor": 26400.0}]) -- antes se
    # perdía (quedaba `impuestos=[]`), lo que hacía imposible para Contai
    # separar cada tipo en su propia cuenta (ver
    # contai_export.cuentas_impuesto_por_tipo). Para Siigo no cambia nada:
    # `valor_unitario` sigue siendo el mismo total combinado, y
    # `siigo_payload.py` nunca lee estos tipos de `item.impuestos`.
    items, otros_impuestos_por_tipo = _extraer_otros_impuestos(items)
    total_otros_impuestos = sum(otros_impuestos_por_tipo.values())
    if total_otros_impuestos > 0:
        items.append(ItemSiigo(
            descripcion="OTROS IMPUESTOS",
            cantidad=1,
            valor_unitario=total_otros_impuestos,
            cuenta_contable=None,
            impuestos=[
                {"tipo": tipo, "porcentaje": 0.0, "valor": valor}
                for tipo, valor in otros_impuestos_por_tipo.items()
            ],
            origen="otros_impuestos",
        ))
        notas.append(
            f"La factura trae impuestos que el parser no reconoce (ver "
            f"dian_parser.TAX_SCHEME_MAP) por ${total_otros_impuestos:,.2f} -- "
            "se agregaron como el ítem 'OTROS IMPUESTOS' aparte, con la misma "
            "cuenta contable que las demás líneas, sin IVA ni retención."
        )

    # Perfil de proveedor: por ahora solo se anota si existe, para que quien
    # continúe esta construcción sepa dónde engancharlo (mapeo de IC, etc.).
    if config_proveedor:
        notas.append(
            f"Existe perfil de proveedor para NIT {factura.proveedor_nit} "
            f"({config_proveedor.get('nombre', 'sin nombre')}) -- "
            f"ver {config_proveedor.get('descripcion_md', 'sin doc asociada')}. "
            "Aplicación automática de este perfil: pendiente de implementar."
        )

    # resuelto_por: "reglas" solo si TODAS las líneas ya tienen cuenta contable
    # asignada; si falta alguna, queda pendiente de Claude o revisión manual.
    faltan_cuentas = any(item.cuenta_contable is None for item in items)
    resuelto_por = "manual" if faltan_cuentas else "reglas"
    if faltan_cuentas:
        notas.append(
            "Quedan líneas sin cuenta contable asignada -- el motor de reglas de "
            "predicción por proveedor todavía no está implementado (era la lógica "
            "de predictor_manager.py en el prototipo anterior). Por ahora todo "
            "queda como 'manual' hasta que se construya esa pieza."
        )

    return ResultadoClasificacion(factura=factura, items=items, resuelto_por=resuelto_por, notas=notas)
