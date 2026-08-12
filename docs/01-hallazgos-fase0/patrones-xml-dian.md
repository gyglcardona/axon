# Patrones reales encontrados en XML de la DIAN (Fase 0)

Basado en 9 XML de muestra + plan de cuentas de Hielo Super-Cool S.A.S. Todos vienen
como `Invoice` directo (no envueltos en `AttachedDocument`).

| Patrón | Detalle | Implicación para el código |
|---|---|---|
| Retención separada, no descontada del total | `WithholdingTaxTotal` (CREE) viene informado pero `LegalMonetaryTotal/PayableAmount` no la resta | Nunca asumir que el total del XML ya viene neto; sumar/restar según el caso |
| `TaxExclusiveAmount` en cero | 2 proveedores con esta inconsistencia, dato real está en `LineExtensionAmount` | Usar `LineExtensionAmount` como respaldo cuando `TaxExclusiveAmount = 0` |
| IVA desglosado en múltiples `TaxTotal` | Hasta 12 líneas de IVA en una sola factura | Sumar todas las líneas, no tomar solo la primera |
| Impuesto al Consumo (IC) además de IVA | Proveedor KOPPS | Mapear como tipo de impuesto adicional, no confundir con IVA — ver perfil de proveedor correspondiente |
| Plan de cuentas Siigo: columna "Nivel agrupación" | Solo filas "Transaccional" son cuentas usables | El motor de sugerencia de cuentas filtra solo por transaccionales |

**Pendiente:** correr este mismo análisis sobre el histórico completo (4 meses × 5
empresas) antes de tocar la integración real con Siigo. Cada patrón nuevo que aparezca
se documenta acá y se traduce en una entrada de `config/proveedores/` o
`config/empresas/` según corresponda.
