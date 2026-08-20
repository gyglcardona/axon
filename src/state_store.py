"""
Persistencia por empresa (SQLite hoy, migrable a Postgres -- ver
docs/00-contexto/decisiones-arquitectura.md). Una base por empresa
(`data/empresas/<nit>.db`) da aislamiento físico real entre empresas (ver
docs/06-multiempresa-saas/aislamiento-datos.md).

Esquema completo en docs/05-esquema-datos/modelo-datos.md -- este módulo es la
única pieza de código que debe conocer el detalle de las tablas.

No se guarda el XML crudo aquí: `archivo_origen` + `cufe` alcanzan para volver
al archivo original en data/entrada-dian/ si hace falta reprocesar.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dian_parser import FacturaDian
from motor_reglas import ResultadoClasificacion
from zip_handler import DocumentoDuplicado, DocumentoConError, DocumentoNoFactura

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS compras (
    id INTEGER PRIMARY KEY,
    cufe TEXT NOT NULL UNIQUE,
    numero_factura TEXT,
    prefijo TEXT,
    numero_puro TEXT,
    fecha_emision TEXT,
    proveedor_nit TEXT,
    proveedor_nombre TEXT,
    proveedor_correo TEXT,
    proveedor_direccion TEXT,
    subtotal_xml REAL,
    subtotal_fuente TEXT,
    total_pagar_xml REAL,
    resuelto_por TEXT NOT NULL,
    estado_siigo TEXT NOT NULL DEFAULT 'pendiente',
    siigo_id TEXT,
    archivo_origen TEXT,
    notas TEXT,
    creado_en TEXT NOT NULL,
    tipo_comprobante_id TEXT,
    medio_pago_id TEXT,
    siigo_error TEXT
);

CREATE TABLE IF NOT EXISTS detalle_compras (
    id INTEGER PRIMARY KEY,
    compra_id INTEGER NOT NULL REFERENCES compras(id),
    orden INTEGER NOT NULL,
    descripcion TEXT,
    cantidad REAL,
    valor_unitario REAL,
    cuenta_contable TEXT,
    tipo_item TEXT,
    origen TEXT,
    iva_tax_id TEXT,
    retencion_tax_id TEXT,
    descuento_monto REAL
);

CREATE TABLE IF NOT EXISTS detalle_impuestos (
    id INTEGER PRIMARY KEY,
    detalle_compra_id INTEGER NOT NULL REFERENCES detalle_compras(id),
    tipo TEXT,
    porcentaje REAL,
    valor REAL
);

CREATE TABLE IF NOT EXISTS documentos_descartados (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    archivo_origen TEXT NOT NULL,
    cufe TEXT,
    motivo TEXT NOT NULL,
    detectado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_cuentas (
    id INTEGER PRIMARY KEY,
    codigo TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    categoria TEXT,
    clase TEXT,
    relacion_con TEXT,
    maneja_vencimientos TEXT,
    diferencia_fiscal TEXT,
    activo TEXT,
    nivel_agrupacion TEXT
);

CREATE TABLE IF NOT EXISTS catalogos_siigo (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    id_siigo TEXT,
    datos_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalogos_siigo_tipo ON catalogos_siigo(tipo);

CREATE TABLE IF NOT EXISTS proveedores_siigo (
    nit TEXT PRIMARY KEY,
    nombre TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compras_siigo (
    id INTEGER PRIMARY KEY,
    siigo_id TEXT NOT NULL UNIQUE,
    numero INTEGER,
    fecha TEXT,
    proveedor_nit TEXT,
    proveedor_nombre TEXT,
    factura_proveedor TEXT,
    total REAL,
    subtotal REAL,
    datos_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_compras_siigo_fecha ON compras_siigo(fecha);

CREATE TABLE IF NOT EXISTS sugerencias_aprendidas (
    id INTEGER PRIMARY KEY,
    campo TEXT NOT NULL,
    proveedor_nit TEXT NOT NULL,
    item_descripcion TEXT NOT NULL DEFAULT '',
    valor TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    UNIQUE(campo, proveedor_nit, item_descripcion)
);

CREATE TABLE IF NOT EXISTS terceros_contai (
    nit TEXT PRIMARY KEY,
    datos_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_cuentas_contai (
    codigo TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    tipo_cuenta TEXT,
    recibe_movimiento TEXT,
    centro_costo TEXT,
    ajustes TEXT,
    porcentaje_base REAL,
    tipo_plazo TEXT,
    activo TEXT
);

CREATE TABLE IF NOT EXISTS comprobantes_contai (
    codigo TEXT PRIMARY KEY,
    datos_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movimientos_contai_historico (
    id INTEGER PRIMARY KEY,
    proveedor_nit TEXT,
    documento TEXT,
    cuenta TEXT NOT NULL,
    tipo INTEGER NOT NULL,
    valor REAL,
    fecha TEXT
);
CREATE INDEX IF NOT EXISTS idx_movimientos_contai_proveedor ON movimientos_contai_historico(proveedor_nit);
"""


def _migrar(conn: sqlite3.Connection) -> None:
    """Agrega columnas nuevas a bases ya creadas antes de que existieran --
    `CREATE TABLE IF NOT EXISTS` en `_ESQUEMA` no las agrega a una tabla que
    ya existe. Idempotente (cada ALTER solo corre si falta la columna) y no
    destructivo (las filas existentes quedan con NULL en la columna nueva)."""
    columnas_compras = {fila[1] for fila in conn.execute("PRAGMA table_info(compras)")}
    if "tipo_comprobante_id" not in columnas_compras:
        conn.execute("ALTER TABLE compras ADD COLUMN tipo_comprobante_id TEXT")
    if "medio_pago_id" not in columnas_compras:
        conn.execute("ALTER TABLE compras ADD COLUMN medio_pago_id TEXT")
    if "siigo_error" not in columnas_compras:
        conn.execute("ALTER TABLE compras ADD COLUMN siigo_error TEXT")
    if "estado_contai" not in columnas_compras:
        conn.execute("ALTER TABLE compras ADD COLUMN estado_contai TEXT NOT NULL DEFAULT 'pendiente'")
    if "modo_pago_contai" not in columnas_compras:
        # NULL = usa el modo_pago_default de config_contai (ver contai_export.py) --
        # a diferencia de tipo_comprobante_id/medio_pago_id, este campo es
        # deliberadamente "contado" | "credito" | NULL por factura, nunca se
        # aprende por proveedor (dos facturas del mismo proveedor pueden
        # pagarse distinto, ver orquestador.actualizar_factura).
        conn.execute("ALTER TABLE compras ADD COLUMN modo_pago_contai TEXT")

    columnas_detalle = {fila[1] for fila in conn.execute("PRAGMA table_info(detalle_compras)")}
    if "iva_tax_id" not in columnas_detalle:
        conn.execute("ALTER TABLE detalle_compras ADD COLUMN iva_tax_id TEXT")
    if "retencion_tax_id" not in columnas_detalle:
        conn.execute("ALTER TABLE detalle_compras ADD COLUMN retencion_tax_id TEXT")
    if "descuento_monto" not in columnas_detalle:
        conn.execute("ALTER TABLE detalle_compras ADD COLUMN descuento_monto REAL")
    conn.commit()


def conectar(nit_empresa: str, base_dir: Path = Path("data/empresas")) -> sqlite3.Connection:
    base_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(base_dir / f"{nit_empresa}.db")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_ESQUEMA)
    _migrar(conn)
    return conn


def ya_existe_cufe(conn: sqlite3.Connection, cufe: str) -> bool:
    """Evita reimportar una factura ya guardada en corridas anteriores (distinto
    de la deduplicación de zip_handler, que es dentro de una misma corrida)."""
    fila = conn.execute("SELECT 1 FROM compras WHERE cufe = ?", (cufe,)).fetchone()
    return fila is not None


def guardar_resultado(conn: sqlite3.Connection, resultado: ResultadoClasificacion, archivo_origen: Path) -> int:
    """Guarda una factura ya clasificada. Devuelve el id de `compras`.

    Falla con IntegrityError si el CUFE ya existe -- llamar a `ya_existe_cufe`
    antes si se quiere evitarlo con un mensaje más claro."""
    factura: FacturaDian = resultado.factura
    cur = conn.execute(
        """
        INSERT INTO compras (
            cufe, numero_factura, prefijo, numero_puro, fecha_emision,
            proveedor_nit, proveedor_nombre, proveedor_correo, proveedor_direccion,
            subtotal_xml, subtotal_fuente, total_pagar_xml,
            resuelto_por, archivo_origen, notas, creado_en,
            tipo_comprobante_id, medio_pago_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            factura.cufe, factura.numero_factura, factura.prefijo, factura.numero_puro,
            factura.fecha_emision, factura.proveedor_nit, factura.proveedor_nombre,
            factura.proveedor_correo, factura.proveedor_direccion,
            factura.subtotal_xml, factura.subtotal_fuente, factura.total_pagar_xml,
            resultado.resuelto_por, str(archivo_origen), json.dumps(resultado.notas, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
            resultado.tipo_comprobante_id, resultado.medio_pago_id,
        ),
    )
    compra_id = cur.lastrowid

    for orden, item in enumerate(resultado.items):
        cur_item = conn.execute(
            """
            INSERT INTO detalle_compras (
                compra_id, orden, descripcion, cantidad, valor_unitario,
                cuenta_contable, tipo_item, origen, iva_tax_id, retencion_tax_id, descuento_monto
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                compra_id, orden, item.descripcion, item.cantidad, item.valor_unitario,
                item.cuenta_contable, item.tipo_item, item.origen, item.iva_tax_id, item.retencion_tax_id,
                item.descuento_monto,
            ),
        )
        detalle_id = cur_item.lastrowid
        for impuesto in item.impuestos:
            conn.execute(
                "INSERT INTO detalle_impuestos (detalle_compra_id, tipo, porcentaje, valor) VALUES (?, ?, ?, ?)",
                (detalle_id, impuesto["tipo"], impuesto["porcentaje"], impuesto["valor"]),
            )

    conn.commit()
    return compra_id


def guardar_plan_cuentas(conn: sqlite3.Connection, cuentas: list[dict]) -> int:
    """Reemplaza el plan de cuentas completo de la empresa (DELETE + INSERT) --
    cada importación de Excel es la fuente de verdad más reciente, no un
    incremento sobre la anterior (las cuentas se activan/desactivan en Siigo
    con el tiempo). Devuelve cuántas cuentas quedaron guardadas."""
    conn.execute("DELETE FROM plan_cuentas")
    for c in cuentas:
        conn.execute(
            """
            INSERT INTO plan_cuentas (
                codigo, nombre, categoria, clase, relacion_con,
                maneja_vencimientos, diferencia_fiscal, activo, nivel_agrupacion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                c["codigo"], c["nombre"], c["categoria"], c["clase"], c["relacion_con"],
                c["maneja_vencimientos"], c["diferencia_fiscal"], c["activo"], c["nivel_agrupacion"],
            ),
        )
    conn.commit()
    return len(cuentas)


def listar_plan_cuentas(conn: sqlite3.Connection, solo_transaccionales: bool = False) -> list[dict]:
    query = "SELECT codigo, nombre, categoria, clase, nivel_agrupacion FROM plan_cuentas"
    if solo_transaccionales:
        query += " WHERE nivel_agrupacion = 'Transaccional'"
    query += " ORDER BY codigo"
    cur = conn.execute(query)
    columnas = [d[0] for d in cur.description]
    return [dict(zip(columnas, fila)) for fila in cur.fetchall()]


def guardar_catalogo_siigo(conn: sqlite3.Connection, tipo: str, items: list[dict]) -> int:
    """Reemplaza el catálogo de ese tipo completo (DELETE + INSERT) -- cada
    'Actualizar datos' trae el estado actual real de Siigo, no un incremento."""
    conn.execute("DELETE FROM catalogos_siigo WHERE tipo = ?", (tipo,))
    for item in items:
        conn.execute(
            "INSERT INTO catalogos_siigo (tipo, id_siigo, datos_json) VALUES (?, ?, ?)",
            (tipo, str(item.get("id", "")), json.dumps(item, ensure_ascii=False)),
        )
    conn.commit()
    return len(items)


def listar_catalogo_siigo(conn: sqlite3.Connection, tipo: str) -> list[dict]:
    filas = conn.execute(
        "SELECT datos_json FROM catalogos_siigo WHERE tipo = ? ORDER BY id", (tipo,)
    ).fetchall()
    return [json.loads(f[0]) for f in filas]


def obtener_nombre_proveedor_siigo(conn: sqlite3.Connection, nit: str) -> str | None:
    fila = conn.execute("SELECT nombre FROM proveedores_siigo WHERE nit = ?", (nit,)).fetchone()
    return fila[0] if fila else None


def guardar_nombre_proveedor_siigo(conn: sqlite3.Connection, nit: str, nombre: str) -> None:
    conn.execute(
        "INSERT INTO proveedores_siigo (nit, nombre) VALUES (?, ?) "
        "ON CONFLICT(nit) DO UPDATE SET nombre = excluded.nombre",
        (nit, nombre),
    )
    conn.commit()


def guardar_compras_siigo(conn: sqlite3.Connection, compras: list[dict], reemplazar_todo: bool = True) -> int:
    """Guarda compras ya causadas en Siigo en el caché local.

    Con `reemplazar_todo=True` (descarga sin rango de fechas, "trae todo")
    borra el caché completo antes de insertar -- ese caso sí representa el
    estado real completo, igual que plan_cuentas y catalogos_siigo. Con
    `reemplazar_todo=False` (descarga acotada a un rango) hace upsert por
    `siigo_id` para no perder compras de otros rangos ya descargados antes."""
    if reemplazar_todo:
        conn.execute("DELETE FROM compras_siigo")
    for c in compras:
        conn.execute(
            """
            INSERT INTO compras_siigo (
                siigo_id, numero, fecha, proveedor_nit, proveedor_nombre,
                factura_proveedor, total, subtotal, datos_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(siigo_id) DO UPDATE SET
                numero = excluded.numero,
                fecha = excluded.fecha,
                proveedor_nit = excluded.proveedor_nit,
                proveedor_nombre = excluded.proveedor_nombre,
                factura_proveedor = excluded.factura_proveedor,
                total = excluded.total,
                subtotal = excluded.subtotal,
                datos_json = excluded.datos_json
            """,
            (
                c["siigo_id"], c["numero"], c["fecha"], c["proveedor_nit"], c["proveedor_nombre"],
                c["factura_proveedor"], c["total"], c["subtotal"], json.dumps(c, ensure_ascii=False),
            ),
        )
    conn.commit()
    return len(compras)


def listar_compras_siigo(
    conn: sqlite3.Connection, desde: str | None = None, hasta: str | None = None, texto: str | None = None,
) -> list[dict]:
    """Lee del caché local -- nunca llama a Siigo (ver src/orquestador.py:descargar_compras_siigo
    para lo que sí trae datos frescos)."""
    condiciones = []
    parametros: list[str] = []
    if desde:
        condiciones.append("fecha >= ?")
        parametros.append(desde)
    if hasta:
        condiciones.append("fecha <= ?")
        parametros.append(hasta)
    if texto:
        condiciones.append("(proveedor_nombre LIKE ? OR proveedor_nit LIKE ? OR factura_proveedor LIKE ?)")
        comodin = f"%{texto}%"
        parametros.extend([comodin, comodin, comodin])

    query = "SELECT datos_json FROM compras_siigo"
    if condiciones:
        query += " WHERE " + " AND ".join(condiciones)
    query += " ORDER BY fecha DESC, numero DESC"

    filas = conn.execute(query, parametros).fetchall()
    return [json.loads(f[0]) for f in filas]


_CAMPOS_COMPRA_EDITABLES = {"tipo_comprobante_id", "medio_pago_id", "modo_pago_contai"}
_CAMPOS_DETALLE_EDITABLES = {"cuenta_contable", "iva_tax_id", "retencion_tax_id"}


def valores_distintos_cabecera(conn: sqlite3.Connection, campo: str) -> list[str]:
    """Valores distintos NO nulos de `compras.<campo>` en TODA la empresa
    (esta conexión ya está scoped a una sola empresa, ver `conectar`) --
    usado por motor_sugerencias para detectar si un campo de cabecera (tipo
    de comprobante, medio de pago) es prácticamente constante dentro de una
    empresa (caso real confirmado: Hielo Super-Cool usa siempre el mismo
    tipo de comprobante y medio de pago en el 100% de sus compras ya
    causadas), aunque el valor cambie de una empresa a otra."""
    if campo not in _CAMPOS_COMPRA_EDITABLES:
        raise ValueError(f"Campo no editable en compras: {campo}")
    filas = conn.execute(f"SELECT DISTINCT {campo} FROM compras WHERE {campo} IS NOT NULL").fetchall()
    return [str(f[0]) for f in filas]


def completar_cabecera_faltante(conn: sqlite3.Connection, campo: str, valor: str) -> int:
    """Rellena `compras.<campo>` con `valor` en todas las filas donde todavía
    está vacío -- nunca pisa un valor ya presente (aprendido, sugerido o
    puesto a mano), solo completa lo que falta."""
    if campo not in _CAMPOS_COMPRA_EDITABLES:
        raise ValueError(f"Campo no editable en compras: {campo}")
    cursor = conn.execute(f"UPDATE compras SET {campo} = ? WHERE {campo} IS NULL", (valor,))
    conn.commit()
    return cursor.rowcount


def actualizar_compra_campos(conn: sqlite3.Connection, cufe: str, campos: dict) -> None:
    """Actualiza campos de cabecera de una factura ya importada (tipo de
    comprobante, medio de pago) -- panel de detalle editable."""
    invalidos = set(campos) - _CAMPOS_COMPRA_EDITABLES
    if invalidos:
        raise ValueError(f"Campos no editables en compras: {', '.join(sorted(invalidos))}")
    if not campos:
        return
    set_clause = ", ".join(f"{c} = ?" for c in campos)
    conn.execute(f"UPDATE compras SET {set_clause} WHERE cufe = ?", (*campos.values(), cufe))
    conn.commit()


def registrar_resultado_envio_siigo(
    conn: sqlite3.Connection, cufe: str, estado_siigo: str, siigo_id: str | None = None, siigo_error: str | None = None,
) -> None:
    """Guarda el resultado de un intento de envío real a Siigo -- separado de
    `actualizar_compra_campos` (que es para ediciones del usuario desde el
    panel de detalle) porque `estado_siigo`/`siigo_id`/`siigo_error` los fija
    el propio proceso de envío, nunca un PATCH del usuario. En éxito se
    limpia `siigo_error` de un intento fallido anterior, si lo había."""
    conn.execute(
        "UPDATE compras SET estado_siigo = ?, siigo_id = ?, siigo_error = ? WHERE cufe = ?",
        (estado_siigo, siigo_id, siigo_error, cufe),
    )
    conn.commit()


def registrar_exportacion_contai(conn: sqlite3.Connection, cufe: str, estado_contai: str) -> None:
    """Guarda el resultado de una exportación a Contai -- mismo motivo que
    `registrar_resultado_envio_siigo` para no mezclarlo con
    `actualizar_compra_campos` (eso es para ediciones del usuario)."""
    conn.execute("UPDATE compras SET estado_contai = ? WHERE cufe = ?", (estado_contai, cufe))
    conn.commit()


def actualizar_detalle_campos(conn: sqlite3.Connection, detalle_id: int, campos: dict) -> None:
    """Actualiza campos de una línea (cuenta contable, IVA, retefuente) --
    panel de detalle editable."""
    invalidos = set(campos) - _CAMPOS_DETALLE_EDITABLES
    if invalidos:
        raise ValueError(f"Campos no editables en detalle_compras: {', '.join(sorted(invalidos))}")
    if not campos:
        return
    set_clause = ", ".join(f"{c} = ?" for c in campos)
    conn.execute(f"UPDATE detalle_compras SET {set_clause} WHERE id = ?", (*campos.values(), detalle_id))
    conn.commit()


def obtener_detalle(conn: sqlite3.Connection, detalle_id: int) -> dict | None:
    """Una línea con el proveedor/cufe de su factura -- para validar que un
    ítem pertenece a la factura/empresa correcta antes de editarlo, y para
    saber por qué proveedor+descripción aprender la preferencia."""
    fila = conn.execute(
        """
        SELECT dc.id, dc.compra_id, c.cufe, c.proveedor_nit, dc.descripcion,
               dc.cuenta_contable, dc.iva_tax_id, dc.retencion_tax_id
        FROM detalle_compras dc
        JOIN compras c ON c.id = dc.compra_id
        WHERE dc.id = ?
        """,
        (detalle_id,),
    ).fetchone()
    if not fila:
        return None
    columnas = [
        "id", "compra_id", "cufe", "proveedor_nit", "descripcion",
        "cuenta_contable", "iva_tax_id", "retencion_tax_id",
    ]
    return dict(zip(columnas, fila))


def listar_detalle_por_compra(conn: sqlite3.Connection, compra_id: int) -> list[dict]:
    """Todas las líneas de una factura (mismo `compra_id`) -- para "replicar
    a todas las líneas" desde el panel de detalle."""
    filas = conn.execute(
        """
        SELECT dc.id, dc.compra_id, c.cufe, c.proveedor_nit, dc.descripcion,
               dc.cuenta_contable, dc.iva_tax_id, dc.retencion_tax_id
        FROM detalle_compras dc
        JOIN compras c ON c.id = dc.compra_id
        WHERE dc.compra_id = ?
        ORDER BY dc.orden
        """,
        (compra_id,),
    ).fetchall()
    columnas = [
        "id", "compra_id", "cufe", "proveedor_nit", "descripcion",
        "cuenta_contable", "iva_tax_id", "retencion_tax_id",
    ]
    return [dict(zip(columnas, f)) for f in filas]


def listar_proveedores_distintos(conn: sqlite3.Connection) -> list[dict]:
    """NIT + nombre de cada proveedor que ya le facturó a esta empresa --
    usado para saber a qué perfiles de proveedor (config/proveedores/) tiene
    sentido darle visibilidad en 'Reglas por empresa': solo a los que esta
    empresa ya conoce (aparecen en su propia Bandeja de revisión), nunca a
    todos los proveedores del sistema."""
    filas = conn.execute(
        """
        SELECT proveedor_nit, MAX(proveedor_nombre) AS proveedor_nombre
        FROM compras
        WHERE proveedor_nit IS NOT NULL AND proveedor_nit != ''
        GROUP BY proveedor_nit
        ORDER BY proveedor_nombre
        """,
    ).fetchall()
    return [{"nit": f[0], "nombre": f[1]} for f in filas]


def listar_items_por_proveedor_y_rango(
    conn: sqlite3.Connection, proveedor_nit: str, desde: str, hasta: str,
) -> list[dict]:
    """Ítems (de cualquier factura) de un proveedor dentro de un rango de
    fechas -- candidatos para "recalcular sin reimportar" (ver
    orquestador.buscar_candidatos_recalculo): tomar una corrección manual y
    ofrecerla también para otras facturas ya importadas del mismo
    proveedor, sin esperar a la próxima importación."""
    filas = conn.execute(
        """
        SELECT dc.id, dc.compra_id, c.cufe, c.numero_factura, c.fecha_emision, c.proveedor_nit,
               dc.descripcion, dc.cuenta_contable, dc.iva_tax_id, dc.retencion_tax_id
        FROM detalle_compras dc
        JOIN compras c ON c.id = dc.compra_id
        WHERE c.proveedor_nit = ? AND c.fecha_emision >= ? AND c.fecha_emision <= ?
        ORDER BY c.fecha_emision, dc.orden
        """,
        (proveedor_nit, desde, hasta),
    ).fetchall()
    columnas = [
        "id", "compra_id", "cufe", "numero_factura", "fecha_emision", "proveedor_nit",
        "descripcion", "cuenta_contable", "iva_tax_id", "retencion_tax_id",
    ]
    return [dict(zip(columnas, f)) for f in filas]


def eliminar_compras(conn: sqlite3.Connection, cufes: list[str]) -> int:
    """Borra permanentemente facturas ya importadas (cabecera + líneas +
    impuestos) -- corrige una importación por error. No hay deshacer, por
    eso el frontend debe confirmar antes de llamar esto. Los FKs de
    `detalle_compras`/`detalle_impuestos` no tienen `ON DELETE CASCADE`
    declarado, así que se borra en orden explícito hijo -> padre."""
    if not cufes:
        return 0
    marcadores = ",".join("?" * len(cufes))
    ids_compra = [f[0] for f in conn.execute(f"SELECT id FROM compras WHERE cufe IN ({marcadores})", cufes).fetchall()]
    if not ids_compra:
        return 0
    marcadores_compra = ",".join("?" * len(ids_compra))
    ids_detalle = [
        f[0] for f in conn.execute(
            f"SELECT id FROM detalle_compras WHERE compra_id IN ({marcadores_compra})", ids_compra
        ).fetchall()
    ]
    if ids_detalle:
        marcadores_detalle = ",".join("?" * len(ids_detalle))
        conn.execute(f"DELETE FROM detalle_impuestos WHERE detalle_compra_id IN ({marcadores_detalle})", ids_detalle)
    conn.execute(f"DELETE FROM detalle_compras WHERE compra_id IN ({marcadores_compra})", ids_compra)
    conn.execute(f"DELETE FROM compras WHERE id IN ({marcadores_compra})", ids_compra)
    conn.commit()
    return len(ids_compra)


def guardar_preferencia_aprendida(
    conn: sqlite3.Connection, campo: str, proveedor_nit: str, item_descripcion: str | None, valor: str,
) -> None:
    """Recuerda la última elección manual del usuario para ese
    proveedor(+ítem) -- usada por `motor_sugerencias` como la sugerencia de
    mayor prioridad en la próxima importación. `item_descripcion` en `None`
    (campos de cabecera: tipo de comprobante, medio de pago) se normaliza a
    `''` -- SQLite no considera iguales dos `NULL` para efectos de `UNIQUE`,
    así que dejarlo en `NULL` rompería el upsert (cada llamada insertaría
    una fila nueva en vez de actualizar la existente)."""
    conn.execute(
        """
        INSERT INTO sugerencias_aprendidas (campo, proveedor_nit, item_descripcion, valor, actualizado_en)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(campo, proveedor_nit, item_descripcion) DO UPDATE SET
            valor = excluded.valor, actualizado_en = excluded.actualizado_en
        """,
        (campo, proveedor_nit, item_descripcion or "", valor, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def obtener_preferencia_aprendida(
    conn: sqlite3.Connection, campo: str, proveedor_nit: str, item_descripcion: str | None,
) -> str | None:
    fila = conn.execute(
        "SELECT valor FROM sugerencias_aprendidas WHERE campo = ? AND proveedor_nit = ? AND item_descripcion = ?",
        (campo, proveedor_nit, item_descripcion or ""),
    ).fetchone()
    return fila[0] if fila else None


def compras_siigo_por_proveedor(conn: sqlite3.Connection, proveedor_nit: str, limite: int = 200) -> list[dict]:
    """Compras ya causadas en Siigo (caché local) de un proveedor exacto, de
    la más reciente a la más antigua -- fuente de histórico para
    `motor_sugerencias`. Distinto de `listar_compras_siigo` (que hace LIKE
    de texto libre para la pantalla "Compras en Siigo"): acá el NIT debe
    matchear exacto, no como substring."""
    filas = conn.execute(
        "SELECT datos_json FROM compras_siigo WHERE proveedor_nit = ? ORDER BY fecha DESC, numero DESC LIMIT ?",
        (proveedor_nit, limite),
    ).fetchall()
    return [json.loads(f[0]) for f in filas]


def existe_compra_siigo(conn: sqlite3.Connection, proveedor_nit: str, factura_proveedor: str) -> bool:
    """¿Ya existe en Siigo (según el caché local de compras causadas) una
    compra de este proveedor con este número de factura? -- protección
    antidúplicados del envío: causar dos veces la misma factura del
    proveedor es un error contable real, no una molestia cosmética.
    `factura_proveedor` es prefijo+número concatenados, tal como lo guarda
    `orquestador._mapear_compra_siigo` (ej. 'FEFL5159763')."""
    if not factura_proveedor:
        return False
    fila = conn.execute(
        "SELECT 1 FROM compras_siigo WHERE proveedor_nit = ? AND factura_proveedor = ? LIMIT 1",
        (proveedor_nit, factura_proveedor),
    ).fetchone()
    return fila is not None


def obtener_compra_siigo(conn: sqlite3.Connection, proveedor_nit: str, factura_proveedor: str) -> dict | None:
    """La compra tal como quedó causada en Siigo (del caché local), para el
    botón 'Ver en Siigo' del panel de detalle -- misma llave de cruce que
    `existe_compra_siigo`. `None` si no está en el caché (puede que
    simplemente no se haya descargado ese periodo todavía)."""
    if not factura_proveedor:
        return None
    fila = conn.execute(
        "SELECT datos_json FROM compras_siigo WHERE proveedor_nit = ? AND factura_proveedor = ? LIMIT 1",
        (proveedor_nit, factura_proveedor),
    ).fetchone()
    return json.loads(fila[0]) if fila else None


def guardar_terceros_contai(conn: sqlite3.Connection, terceros: list[dict]) -> int:
    """Reemplaza el caché completo del maestro de terceros de Contai (DELETE
    + INSERT) -- cada importación de Excel es el estado real más reciente
    de Contai, no un incremento. `terceros` es una lista de dicts con las
    16 columnas del archivo real (NIT, Tipo, Nombre, ...); se guardan tal
    cual en `datos_json` para poder reconstruir una fila idéntica si hace
    falta. Filas sin NIT numérico (fila plantilla del archivo real) deben
    descartarse antes de llamar a esta función, no acá. Dos filas del Excel
    real pueden colapsar al mismo NIT tras limpiar caracteres no numéricos
    (caso real: "18576831" y "E18576831", cédula de extranjería con/sin
    prefijo) -- se usa REPLACE para que la última fila del archivo gane en
    vez de reventar con UNIQUE constraint."""
    conn.execute("DELETE FROM terceros_contai")
    for t in terceros:
        conn.execute(
            "INSERT OR REPLACE INTO terceros_contai (nit, datos_json) VALUES (?, ?)",
            (t["NIT"], json.dumps(t, ensure_ascii=False)),
        )
    conn.commit()
    return len({t["NIT"] for t in terceros})


def existe_tercero_contai(conn: sqlite3.Connection, nit: str) -> bool:
    """¿Ya está este NIT en el maestro de terceros de Contai (caché local)?
    -- decide si hace falta generar una fila nueva en el plano de terceros
    al exportar."""
    if not nit:
        return False
    fila = conn.execute("SELECT 1 FROM terceros_contai WHERE nit = ? LIMIT 1", (nit,)).fetchone()
    return fila is not None


def contar_terceros_contai(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM terceros_contai").fetchone()[0]


def guardar_plan_cuentas_contai(conn: sqlite3.Connection, cuentas: list[dict]) -> int:
    """Reemplaza el plan de cuentas de Contai completo (DELETE + INSERT) --
    tabla propia, separada de `plan_cuentas` (la de Siigo): en Contai la
    información llega directamente como asiento contable (no pasa por un
    módulo de compras que arma el asiento, como sí hace Siigo), así que las
    cuentas traen banderas propias (si reciben movimiento, si requieren
    centro de costo) que no tienen equivalente en el catálogo de Siigo --
    se conservan tal cual del Excel real en vez de forzarlas a ese formato."""
    conn.execute("DELETE FROM plan_cuentas_contai")
    for c in cuentas:
        conn.execute(
            """
            INSERT INTO plan_cuentas_contai (
                codigo, nombre, tipo_cuenta, recibe_movimiento, centro_costo,
                ajustes, porcentaje_base, tipo_plazo, activo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                c["codigo"], c["nombre"], c["tipo_cuenta"], c["recibe_movimiento"], c["centro_costo"],
                c["ajustes"], c["porcentaje_base"], c["tipo_plazo"], c["activo"],
            ),
        )
    conn.commit()
    return len(cuentas)


def listar_plan_cuentas_contai(conn: sqlite3.Connection, solo_transaccionales: bool = False) -> list[dict]:
    query = "SELECT codigo, nombre, tipo_cuenta, recibe_movimiento, centro_costo, ajustes, porcentaje_base, tipo_plazo, activo FROM plan_cuentas_contai"
    if solo_transaccionales:
        query += " WHERE recibe_movimiento = 'S'"
    query += " ORDER BY codigo"
    cur = conn.execute(query)
    columnas = [d[0] for d in cur.description]
    return [dict(zip(columnas, fila)) for fila in cur.fetchall()]


def guardar_comprobantes_contai(conn: sqlite3.Connection, comprobantes: list[dict]) -> int:
    """Reemplaza el catálogo de comprobantes de Contai completo (DELETE +
    INSERT) -- mismo espíritu que `guardar_terceros_contai`, se guarda la
    fila cruda en `datos_json` para poder mostrarla tal cual."""
    conn.execute("DELETE FROM comprobantes_contai")
    for c in comprobantes:
        conn.execute(
            "INSERT OR REPLACE INTO comprobantes_contai (codigo, datos_json) VALUES (?, ?)",
            (c["Comprobante"], json.dumps(c, ensure_ascii=False)),
        )
    conn.commit()
    return len({c["Comprobante"] for c in comprobantes})


def listar_comprobantes_contai(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("SELECT datos_json FROM comprobantes_contai ORDER BY codigo")
    return [json.loads(fila[0]) for fila in cur.fetchall()]


def guardar_movimientos_contai_historico(conn: sqlite3.Connection, lineas: list[dict]) -> dict:
    """Agrega líneas nuevas al histórico de movimientos de Contai SIN borrar
    lo que ya había -- contai_movimientos.xlsx se exporta por rango de
    fechas (ej. un mes a la vez), así que importar febrero no debe borrar
    lo que ya se importó de enero. Deduplica por número de documento
    (factura): si el documento ya existe en el histórico, sus líneas se
    saltan completas (evita duplicar todo un asiento si el usuario
    reimporta por error el mismo mes); un documento nuevo se agrega
    completo. Documentos sin número (None) no se pueden deduplicar por esa
    vía -- se insertan siempre."""
    existentes = {
        fila[0] for fila in conn.execute(
            "SELECT DISTINCT documento FROM movimientos_contai_historico WHERE documento IS NOT NULL"
        )
    }
    documentos_nuevos: set[str] = set()
    insertadas = 0
    omitidas = 0
    for linea in lineas:
        doc = linea["documento"]
        if doc is not None and doc in existentes:
            omitidas += 1
            continue
        conn.execute(
            "INSERT INTO movimientos_contai_historico (proveedor_nit, documento, cuenta, tipo, valor, fecha) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (linea["proveedor_nit"], linea["documento"], linea["cuenta"], linea["tipo"], linea["valor"], linea["fecha"]),
        )
        insertadas += 1
        if doc is not None:
            documentos_nuevos.add(doc)
    conn.commit()
    return {"lineas_insertadas": insertadas, "lineas_omitidas": omitidas, "documentos_nuevos": len(documentos_nuevos)}


def contar_movimientos_contai_historico(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM movimientos_contai_historico").fetchone()[0]
    proveedores = conn.execute("SELECT COUNT(DISTINCT proveedor_nit) FROM movimientos_contai_historico").fetchone()[0]
    return {"total_lineas": total, "proveedores_distintos": proveedores}


def listar_movimientos_contai_historico(conn: sqlite3.Connection) -> list[dict]:
    """Todas las líneas del histórico de movimientos Contai ya importado --
    se usa para poder CONSULTAR (no solo importar) qué cuenta se usó en una
    factura o proveedor puntual, ver orquestador.listar_movimientos_contai
    (que agrupa estas líneas por documento)."""
    cur = conn.execute(
        "SELECT proveedor_nit, documento, cuenta, tipo, valor, fecha "
        "FROM movimientos_contai_historico ORDER BY documento, id"
    )
    columnas = [d[0] for d in cur.description]
    return [dict(zip(columnas, fila)) for fila in cur.fetchall()]


def mapa_nombres_terceros_contai(conn: sqlite3.Connection) -> dict[str, str]:
    """NIT -> Nombre a partir del maestro de terceros Contai ya importado
    (datos_json trae las columnas crudas del Excel tal cual, ver
    contai_export.COLUMNAS_TERCERO) -- para mostrar el nombre del proveedor
    junto a sus movimientos, dato que la tabla histórica no trae."""
    filas = conn.execute("SELECT nit, datos_json FROM terceros_contai").fetchall()
    mapa: dict[str, str] = {}
    for nit, datos_json in filas:
        try:
            nombre = json.loads(datos_json).get("Nombre")
        except (json.JSONDecodeError, AttributeError):
            nombre = None
        if nombre:
            mapa[nit] = str(nombre).strip()
    return mapa


def sugerir_cuenta_historial_contai(conn: sqlite3.Connection, proveedor_nit: str, cuentas_iva_conocidas: set[str]) -> str | None:
    """Cuenta más frecuente entre las líneas de DÉBITO (Tipo=1) de este
    proveedor en el histórico de Contai, excluyendo las cuentas que hoy
    están configuradas como cuenta de IVA de alguna tarifa (el histórico no
    distingue línea de gasto vs línea de IVA -- ambas son débito -- así que
    hay que descartar las de IVA a mano para no sugerir esa por error).
    `None` si no hay histórico para ese proveedor o si, tras descartar IVA,
    no queda ninguna cuenta."""
    if not proveedor_nit:
        return None
    filas = conn.execute(
        "SELECT cuenta, COUNT(*) AS n FROM movimientos_contai_historico "
        "WHERE proveedor_nit = ? AND tipo = 1 GROUP BY cuenta ORDER BY n DESC",
        (proveedor_nit,),
    ).fetchall()
    for cuenta, _n in filas:
        if cuenta not in cuentas_iva_conocidas:
            return cuenta
    return None


def registrar_descartado(
    conn: sqlite3.Connection, descarte: DocumentoDuplicado | DocumentoConError | DocumentoNoFactura
) -> None:
    if isinstance(descarte, DocumentoDuplicado):
        tipo = "duplicado"
        cufe = descarte.cufe
        motivo = f"Ya se había importado como {descarte.origen_primera_aparicion} en esta misma corrida"
    elif isinstance(descarte, DocumentoNoFactura):
        tipo = "no_es_factura"
        cufe = None
        motivo = f"El documento raíz es '{descarte.tipo}', no 'Invoice' -- no es una factura de compra"
    else:
        tipo = "error"
        cufe = None
        motivo = descarte.motivo

    ya_registrado = conn.execute(
        "SELECT 1 FROM documentos_descartados WHERE tipo = ? AND archivo_origen = ? AND motivo = ?",
        (tipo, str(descarte.origen), motivo),
    ).fetchone()
    if ya_registrado:
        # Reimportar la misma carpeta no debe inflar el log de auditoría con
        # el mismo descarte repetido -- solo `compras` necesita ser el filtro
        # estricto (evitar doble causación); esto es solo trazabilidad.
        return

    conn.execute(
        """
        INSERT INTO documentos_descartados (tipo, archivo_origen, cufe, motivo, detectado_en)
        VALUES (?, ?, ?, ?, ?)
        """,
        (tipo, str(descarte.origen), cufe, motivo, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
