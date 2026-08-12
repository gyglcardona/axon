# Plan de cuentas real: HIELO SUPER-COOL S.A.S.

Confirmado a partir del Excel exportado de Siigo. NIT 901528790-6.

- **905 cuentas en total, 467 marcadas `Nivel agrupación = Transaccional`** — confirma
  el filtro ya usado en el prototipo anterior (`cuentas_view.py`): solo esas 467 son
  usables para contabilizar, el resto son agrupadoras.
- Encabezados reales en fila 7 del Excel (6 filas de metadatos arriba: título, nombre
  de empresa, NIT). Al leer con pandas: `skiprows=6`.
- Columnas: `Código, Nombre, Categoría, Clase, Relación con, Maneja vencimientos,
  Diferencia fiscal, Activo, Nivel agrupación`.
- Distribución de las 467 cuentas transaccionales por `Clase`: 173 Gastos, 120 Activo,
  102 Pasivo, 30 Ingresos, 23 Costos de producción o de operación, 12 Patrimonio, 6
  Costos de venta, 1 Cuentas de orden acreedoras.
- **Cuenta candidata para el ítem de IVA no discriminado**: `229999 — Causación
  automática compras (sistema)`, clase Pasivo. Por el nombre parece pensada para
  ajustes automáticos como este, pero **no usar sin confirmación explícita de la
  contadora** — ver `docs/02-reglas-negocio/politicas-empresa/901528790-hielo-super-cool-iva-no-discriminado.md`.

## Resuelto (2026-07-21)

`/v1/account-groups` de la API de Siigo **no** reemplaza esta carga de Excel -- es el
catálogo de grupos de inventario, un catálogo distinto (ver
`docs/04-integracion-siigo/autenticacion-y-endpoints.md`). No existe ningún endpoint
público de Siigo para descargar el plan de cuentas contable. Se sigue importando por
Excel, ahora vía el menú "Plan de cuentas" de la interfaz web (ver
`docs/05-esquema-datos/modelo-datos.md`).
