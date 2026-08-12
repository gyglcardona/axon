# Modelo de datos

Una base SQLite por empresa: `data/empresas/<nit>.db` (ver
`docs/06-multiempresa-saas/aislamiento-datos.md`). Implementado en `src/state_store.py`.

## `compras` — cabecera de cada factura

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | autoincremental |
| `cufe` | TEXT UNIQUE NOT NULL | llave real de deduplicación (ver `src/zip_handler.py`); nunca el nombre de archivo |
| `numero_factura`, `prefijo`, `numero_puro` | TEXT | tal como los calcula `dian_parser.parsear_factura` |
| `fecha_emision` | TEXT | ISO tal como viene del XML |
| `proveedor_nit`, `proveedor_nombre`, `proveedor_correo`, `proveedor_direccion` | TEXT | |
| `subtotal_xml`, `subtotal_fuente` | REAL, TEXT | `subtotal_fuente` documenta si vino de `TaxExclusiveAmount` o del respaldo `LineExtensionAmount` |
| `total_pagar_xml` | REAL | tal cual el XML, nunca neteado de retenciones (ver decisión de arquitectura) |
| `resuelto_por` | TEXT | `'reglas' \| 'claude' \| 'historico' \| 'manual'` -- `'historico'` es de `motor_sugerencias` (sugerencia por histórico de Siigo o preferencia aprendida, nunca una regla de negocio confirmada) |
| `estado_siigo` | TEXT | `'pendiente' \| 'enviado' \| 'error'`, default `'pendiente'` |
| `siigo_id` | TEXT NULL | id que asigna Siigo tras el envío exitoso |
| `archivo_origen` | TEXT | ruta al ZIP o XML de origen en `data/entrada-dian/...` — permite re-parsear el XML original en vez de duplicar su contenido en la BD |
| `notas` | TEXT | lista de advertencias/notas del parser y del motor de reglas, serializada como JSON |
| `creado_en` | TEXT | timestamp ISO de cuándo se importó |
| `tipo_comprobante_id`, `medio_pago_id` | TEXT NULL | id del catálogo Siigo (`catalogos_siigo`, tipo `document_types`/`payment_types`) elegido para la cabecera -- editable desde el panel de detalle, sugerido por `motor_sugerencias.sugerir_cabecera` al importar |

No se guarda el XML crudo en la base: el archivo ya vive en `data/entrada-dian/`, y
`archivo_origen` + `cufe` alcanzan para volver a él si hace falta reprocesar.

## `detalle_compras` — ítems tal como se enviarían a Siigo

Uno por cada `ItemSiigo` que produce `motor_reglas.clasificar_factura` (línea del XML,
o ítem inyectado por una política de empresa como el IVA no discriminado).

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `compra_id` | INTEGER FK → `compras.id` | |
| `orden` | INTEGER | posición del ítem, para reconstruir el payload en el mismo orden |
| `descripcion`, `cantidad`, `valor_unitario` | TEXT/REAL | |
| `cuenta_contable` | TEXT NULL | `NULL` mientras nadie (regla, histórico, Claude, humano) la haya asignado -- editable desde el panel de detalle, seleccionable solo entre cuentas `Transaccional` de `plan_cuentas` |
| `tipo_item` | TEXT | hoy siempre `"Account"` (ver `ItemSiigo.tipo_item`) |
| `origen` | TEXT | `'xml' \| 'politica_empresa'` |
| `iva_tax_id`, `retencion_tax_id` | TEXT NULL | id del catálogo `taxes` (`catalogos_siigo`) elegido para IVA/retefuente de esta línea. `NULL` en retención es un estado válido a propósito (ver `motor_sugerencias.py`: no todo catálogo real tiene una tarifa "Retefuente 0%", a diferencia de IVA) -- al enviar a Siigo ese bloque simplemente se omite. Editable desde el panel de detalle; sugerido por `motor_sugerencias.sugerir_item` al importar. |

## `detalle_impuestos` — impuestos por ítem de `detalle_compras`

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `detalle_compra_id` | INTEGER FK → `detalle_compras.id` | |
| `tipo`, `porcentaje`, `valor` | TEXT/REAL | tal como los deja `ItemSiigo.impuestos` |

## `sugerencias_aprendidas` — corrección manual del usuario, recordada para la próxima importación

Implementado en `src/motor_sugerencias.py` (`aprender`/`sugerir_item`/`sugerir_cabecera`). Cuando el
usuario cambia a mano la cuenta/IVA/retefuente de una línea o el tipo de comprobante/medio de pago de
la cabecera, queda guardado aquí por proveedor (y por ítem, cuando aplica) para que la próxima factura
de ese mismo proveedor llegue con esa misma sugerencia -- tiene prioridad sobre el histórico de
`compras_siigo`.

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `campo` | TEXT | `'cuenta_contable' \| 'tipo_comprobante_id' \| 'medio_pago_id' \| 'iva_tax_id' \| 'retencion_tax_id'` |
| `proveedor_nit` | TEXT | |
| `item_descripcion` | TEXT NOT NULL DEFAULT `''` | descripción normalizada (`strip().upper()`) del ítem, para los campos de línea; cadena vacía para los campos de cabecera (nunca `NULL` -- SQLite no considera iguales dos `NULL` en un `UNIQUE`, así que `NULL` rompería el upsert) |
| `valor` | TEXT | el id/código elegido |
| `actualizado_en` | TEXT | timestamp ISO de la última corrección |

`UNIQUE(campo, proveedor_nit, item_descripcion)` -- cada corrección nueva reemplaza la anterior para esa
combinación, no acumula historial de cambios.

## `documentos_descartados` — trazabilidad de lo que `zip_handler` NO importó

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `tipo` | TEXT | `'duplicado' \| 'no_es_factura' \| 'error'` |
| `archivo_origen` | TEXT | |
| `cufe` | TEXT NULL | `NULL` si el motivo es "sin CUFE" o "no es factura" |
| `motivo` | TEXT | por qué se descartó (duplicado de cuál archivo, tipo de documento real, XML mal formado, etc.) |
| `detectado_en` | TEXT | timestamp ISO |

`'no_es_factura'` cubre documentos que la DIAN entrega en el mismo formato de
ZIP/carpeta pero que no son facturas de compra -- ej. `ApplicationResponse`
(acuses de recibo). Se detectan por el tag raíz del XML (`dian_parser.tipo_documento`)
antes de intentar parsearlos como factura (ver `src/zip_handler.py`).

Guardar esto (y no solo imprimirlo en consola) es lo que permite que una corrida de
`importar` quede auditable después: cuántos duplicados hubo, cuáles, y por qué.

## `validaciones_completitud` — historial del validador contra el listado DIAN

Pendiente de implementar junto con el comando `validar-completitud` (ver
`docs/03-ingesta-dian/validador-completitud.md`) — no bloquea lo demás.

## `plan_cuentas` — importado desde el Excel exportado de Siigo Nube

**Confirmado 2026-07-21: no hay endpoint de Siigo para esto** (`/v1/account-groups` es
otra cosa -- grupos de inventario, ver `docs/04-integracion-siigo/autenticacion-y-endpoints.md`).
Se importa desde el Excel que exporta la empresa desde Siigo Nube, vía el menú "Plan de
cuentas" de la interfaz web (`src/orquestador.py:importar_plan_cuentas`).

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `codigo` | TEXT UNIQUE NOT NULL | |
| `nombre` | TEXT NOT NULL | |
| `categoria`, `clase`, `relacion_con`, `maneja_vencimientos`, `diferencia_fiscal`, `activo` | TEXT | tal como vienen del Excel |
| `nivel_agrupacion` | TEXT | solo `'Transaccional'` es usable para causar (ver hallazgo Fase 0); el resto son agrupadoras |

Formato del Excel confirmado en
`docs/05-esquema-datos/plan-cuentas-hielo-super-cool.md`: 6 filas de metadatos, encabezados
en la fila 7, 9 columnas en orden fijo. Cada importación **reemplaza completo** el plan de
cuentas anterior de esa empresa (no es incremental) -- las cuentas se activan/desactivan en
Siigo con el tiempo, así que la última exportación es la fuente de verdad.
