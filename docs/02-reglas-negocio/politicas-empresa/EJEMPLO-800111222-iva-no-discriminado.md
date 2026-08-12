# Política de empresa: <por confirmar — NIT de ejemplo 800.111.222-2>

- **NIT:** 800111222 (ficticio, reemplazar por el real)
- **Slug:** iva-no-discriminado-ejemplo
- **Estado:** en pruebas — falta confirmar con la contadora los casos límite
- **Archivo de configuración correspondiente:** `config/empresas/800111222.json`

## Qué se observó

Aunque las facturas de compra de esta empresa sí traen IVA discriminado en el XML de
la DIAN, la contadora no lo causa como impuesto en Siigo. En su lugar, la factura se
causa "sin IVA" y el valor que correspondería al IVA se agrega como una línea
(ítem) adicional del gasto, con su propia descripción, en vez de ir en el bloque de
impuestos de la línea original.

Este comportamiento se repite en **todas** las compras de esta empresa,
independientemente de quién sea el proveedor — no es un comportamiento del proveedor,
es cómo esta empresa específica quiere que se contabilice.

## Por qué pasa

Sin confirmar con la contadora. Hipótesis: puede ser una decisión de manejo tributario
propio de la empresa, o una simplificación operativa. **No asumir el motivo — solo
implementar el comportamiento observado hasta que se confirme.**

## Cómo debe manejarlo el sistema

1. Al procesar cualquier factura de esta empresa, si el XML trae una línea con IVA
   (`TaxTotal` con `TaxScheme = IVA`), el sistema **no** debe mapear ese IVA al bloque
   `taxes` del payload de Siigo.
2. En su lugar, se agrega un ítem adicional a la factura en Siigo:
   - Descripción: `"IVA no discriminado (política contable de la empresa)"`
   - Tipo: `Account` (gasto), cuenta contable por definir con la contadora — de momento
     usar la misma cuenta del ítem principal si no hay una cuenta específica asignada.
   - Cantidad: 1
   - Valor: el monto exacto del IVA que traía el XML original.
   - Sin impuestos asociados a este ítem (ya "es" el impuesto, causado como gasto).
3. El total de la factura en Siigo debe seguir cuadrando con el total real pagado —
   este tratamiento no cambia cuánto se pagó, solo cómo se distribuye contablemente.

## A qué facturas aplica

- [x] Todas las compras de esta empresa
- [ ] Solo ciertas categorías
- [ ] Solo mientras no se confirme lo contrario con la contadora ← marcar si aplica

## Ejemplo real (factura de referencia)

Pendiente: adjuntar el XML real de una factura de esta empresa donde se vea el IVA
discriminado en el XML de la DIAN, para usarlo como caso de prueba en
`tests/casos-reales/800111222/`.
