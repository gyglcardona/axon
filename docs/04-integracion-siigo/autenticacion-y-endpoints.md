# Integración con Siigo API

- **Auth:** `POST /auth` con `username` + `access_key`. Header `Partner-Id` en cada
  request posterior.
- **Endpoints ya identificados:**
  - `POST /v1/purchases` — envío de facturas de compra.
  - `GET /v1/purchase-support-documents` — documentos soporte de compra.
  - `GET /v1/account-groups` — **NO es el plan de cuentas contable** (ver "Plan de
    cuentas" abajo, confirmado 2026-07-21).
  - `GET /v1/document-types?type=FC`, `GET /v1/payment-types?document_type=FC`,
    `GET /v1/taxes`, `GET /v1/journals` — catálogos maestros. Implementados en
    `src/siigo_client.py`, cacheados por empresa vía el menú "Datos maestros Siigo".

## Catálogos maestros: formas de respuesta confirmadas contra la API real

**Confirmado 2026-07-21** contra la cuenta real de Hielo Super-Cool (no solo la
documentación, que resultó inconsistente en varios puntos, ej. headers de `/auth`).
Auth: `POST https://api.siigo.com/auth` con body `{"username", "access_key"}`, sin
headers extra — responde `{"access_token", "expires_in", "token_type", "scope"}`.
Las llamadas siguientes usan `Authorization: Bearer <token>` + `Partner-Id: <valor>`.

| Endpoint | Forma | Campos clave |
|---|---|---|
| `GET /v1/document-types?type=FC` | lista plana | `id, code, name, type, active, reteiva, reteica, ...` |
| `GET /v1/payment-types?document_type=FC` | lista plana | `id, name, type, active, due_date` |
| `GET /v1/journals` | **objeto paginado** `{pagination, results, _links}` -- la lista real está en `results`, no en el nivel raíz | cada item: `id, document:{id}, number, name, date, items, ...` -- el "prefijo" no es un campo propio, se deriva del patrón `PREFIJO-...` en `name` |
| `GET /v1/taxes` | lista plana | `id, name, type, percentage, active` |

## Compras ya causadas (`GET /v1/purchases`): filtros de Siigo no funcionan

**Confirmado 2026-07-21** contra la cuenta real de Hielo Super-Cool (2212 compras
reales, ya creadas por el sistema anterior del usuario, también llamado "AXON").

- Objeto paginado igual que `journals`: `{pagination, results, _links}`.
- Cada resultado YA trae los `items` completos (descripción, cuenta, cantidad,
  precio, impuestos) -- no hace falta un segundo GET por id para el detalle.
- **Los filtros documentados no filtran nada**: se probaron `created_start/end`,
  `date_start/end` (en formato `yyyy-mm-dd` y RFC3339), `customer_identification`,
  `supplier_identification`, `identification` -- todos devuelven HTTP 200 pero con
  el `total_results` completo sin importar el valor. Solo la paginación (`page`,
  `page_size`) funciona de verdad.
- Sí vienen ordenados por consecutivo/fecha descendente (el más reciente
  primero), de forma estable -- por eso `src/orquestador.py:descargar_compras_siigo`
  puede cortar la paginación temprano cuando hace falta, en vez de confiar en un
  filtro de Siigo que no existe.
- El proveedor solo viene como `supplier.identification` (NIT) -- el **nombre**
  no está en la respuesta de compras. Hay que resolverlo aparte contra
  `GET /v1/customers?identification=<nit>` (los proveedores viven ahí como
  "customers" en el modelo de Siigo). Se cachea localmente (tabla
  `proveedores_siigo`) para no repetir la llamada en cada descarga.
- El campo `provider_invoice.{prefix, number}` es el número de factura del
  proveedor tal como se causó (ej. `G7P4` + `558122`), no el consecutivo interno
  de Siigo (`number`).

## Plan de cuentas: no hay endpoint para descargarlo

**Confirmado (2026-07-21):** `GET /v1/account-groups` devuelve *grupos de
inventario* (clasificación de productos/servicios), no el plan de cuentas
contable — lo dice la propia documentación de Siigo ("Grupos de Inventario").
Revisando el índice de la API pública y el SDK oficial de JavaScript
(`SiigoSAS/siigo_sdk_javascript`, que expone `AccountGroupApi`,
`CostCenterApi`, `DocumentTypeApi`, `PaymentTypeApi`, `TaxApi`,
`AccountsPayableApi`, `TestBalanceApi`, etc.), **no existe ningún recurso de
"chart of accounts" / plan de cuentas completo.**

Lo más cercano es `TestBalanceApi` (balance de prueba), que en principio
devolvería cuentas con movimiento — pero no es lo mismo que el plan de
cuentas configurado completo (no incluiría cuentas sin movimiento todavía), y
no se confirmó si está en la documentación pública o es de acceso especial.

**Conclusión práctica:** por ahora, seguir importando el plan de cuentas desde
el Excel manualmente (como ya se hacía) — no hay forma confirmada de
reemplazar ese paso vía API. Si Siigo agrega un endpoint dedicado en el
futuro, revisar esta sección.

Fuentes: [developers.siigo.com/docs/siigoapi/catalog/1-get-account-groups](https://developers.siigo.com/docs/siigoapi/catalog/1-get-account-groups),
[github.com/SiigoSAS/siigo_sdk_javascript](https://github.com/SiigoSAS/siigo_sdk_javascript).
