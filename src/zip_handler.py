"""
Descubre los documentos de factura (XML) entregados en una carpeta de entrada
(data/entrada-dian/<slug>/<yyyy>/<mm>/), sin importar cómo hayan llegado:

- comprimidos en ZIP (un XML + un PDF por ZIP, el caso normal de la DIAN).
- ya descomprimidos, sueltos en la carpeta (XML sin ZIP).
- ya descomprimidos dentro de una subcarpeta (alguien extrajo el ZIP a mano).

Deduplica por CUFE (leído del contenido del XML vía `dian_parser.extraer_cufe`),
nunca por nombre de archivo ni por si el documento venía o no comprimido --
el nombre del archivo puede coincidir con el CUFE en la práctica, pero no es
una garantía, así que no se usa como llave.

Cero tokens de IA -- es descubrimiento de archivos, código puro (ver
docs/00-contexto/decisiones-arquitectura.md, "Extracción de XML sin IA").
"""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from dian_parser import extraer_cufe, tipo_documento

# La DIAN entrega en el mismo formato de ZIP/carpeta otros tipos de documento
# además de facturas de compra (acuses de recibo, notas, etc.). Por ahora solo
# se procesa 'Invoice' -- ver docs/03-ingesta-dian/validador-completitud.md.
TIPOS_FACTURA = {"Invoice"}


@dataclass
class DocumentoEncontrado:
    cufe: str
    xml_bytes: bytes
    origen: Path


@dataclass
class DocumentoDuplicado:
    cufe: str
    origen: Path
    origen_primera_aparicion: Path


@dataclass
class DocumentoConError:
    origen: Path
    motivo: str


@dataclass
class DocumentoNoFactura:
    origen: Path
    tipo: str  # 'ApplicationResponse', 'CreditNote', etc.


@dataclass
class ResultadoDescubrimiento:
    documentos: list[DocumentoEncontrado]
    duplicados: list[DocumentoDuplicado]
    con_error: list[DocumentoConError]
    no_facturas: list[DocumentoNoFactura]


def _candidatos_xml(carpeta: Path) -> tuple[list[tuple[bytes, Path]], list[DocumentoConError]]:
    """Recorre `carpeta` recursivamente y devuelve (contenido_xml, archivo_origen)
    para cada XML encontrado, venga suelto o dentro de un ZIP. Otras extensiones
    (.pdf, etc.) se ignoran -- no son el documento fiscal.

    Un .zip que no abre (dañado, o algo con extensión .zip que en realidad no
    es un ZIP -- caso real desde que existe la subida manual por el
    navegador, ver orquestador.guardar_archivos_subidos) no debe tumbar el
    descubrimiento de TODO lo demás en la carpeta -- se reporta como
    `DocumentoConError` igual que un XML mal formado, en vez de propagar la
    excepción cruda."""
    candidatos: list[tuple[bytes, Path]] = []
    con_error: list[DocumentoConError] = []
    for path in sorted(carpeta.rglob("*")):
        if not path.is_file():
            continue
        sufijo = path.suffix.lower()
        if sufijo == ".zip":
            try:
                with zipfile.ZipFile(path) as z:
                    for nombre in z.namelist():
                        if nombre.lower().endswith(".xml"):
                            candidatos.append((z.read(nombre), path))
            except (zipfile.BadZipFile, OSError) as e:
                con_error.append(DocumentoConError(origen=path, motivo=f"El ZIP está dañado o no es un ZIP válido: {e}"))
        elif sufijo == ".xml":
            candidatos.append((path.read_bytes(), path))
    return candidatos, con_error


def descubrir_documentos(carpeta: Path) -> ResultadoDescubrimiento:
    """Punto de entrada del comando `importar`: encuentra todos los XML de
    `carpeta`, deduplica por CUFE y separa lo que no se pudo identificar."""
    vistos: dict[str, Path] = {}
    documentos: list[DocumentoEncontrado] = []
    duplicados: list[DocumentoDuplicado] = []
    no_facturas: list[DocumentoNoFactura] = []

    candidatos, con_error = _candidatos_xml(carpeta)

    for xml_bytes, origen in candidatos:
        try:
            tipo = tipo_documento(xml_bytes)
        except ET.ParseError as e:
            con_error.append(DocumentoConError(origen=origen, motivo=f"XML mal formado: {e}"))
            continue

        if tipo not in TIPOS_FACTURA:
            no_facturas.append(DocumentoNoFactura(origen=origen, tipo=tipo))
            continue

        cufe = extraer_cufe(xml_bytes)

        if not cufe:
            con_error.append(DocumentoConError(
                origen=origen,
                motivo="No se encontró CUFE (cbc:UUID) en el XML -- no se puede "
                       "identificar ni deduplicar de forma confiable, requiere revisión manual.",
            ))
            continue

        if cufe in vistos:
            duplicados.append(DocumentoDuplicado(
                cufe=cufe, origen=origen, origen_primera_aparicion=vistos[cufe],
            ))
            continue

        vistos[cufe] = origen
        documentos.append(DocumentoEncontrado(cufe=cufe, xml_bytes=xml_bytes, origen=origen))

    return ResultadoDescubrimiento(
        documentos=documentos, duplicados=duplicados, con_error=con_error, no_facturas=no_facturas,
    )
