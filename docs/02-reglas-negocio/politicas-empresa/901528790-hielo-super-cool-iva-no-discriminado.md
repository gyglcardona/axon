# Política de empresa: HIELO SUPER-COOL S.A.S.

- **NIT:** 901528790-6
- **Slug:** hielo-super-cool
- **Estado:** confirmado por la contadora (2026-07) -- ver "Cuenta contable" abajo
- **Archivo de configuración correspondiente:** `config/empresas/901528790.json`

## Qué se observó

Aunque las facturas de compra de esta empresa sí traen IVA discriminado en el XML de
la DIAN, la contadora no lo causa como impuesto en Siigo. En su lugar, la factura se
causa "sin IVA" y el valor que correspondería al IVA se agrega como una línea (ítem)
adicional del gasto, con su propia descripción, en vez de ir en el bloque de impuestos
de la línea original.

Este comportamiento se repite en **todas** las compras de esta empresa,
independientemente de quién sea el proveedor.

## Por qué pasa

Sin confirmar con la contadora. No asumir el motivo — solo implementar el
comportamiento observado hasta que se confirme.

## Cómo debe manejarlo el sistema

1. Al procesar cualquier factura de esta empresa, si el XML trae una línea con IVA
   (`TaxTotal` con `TaxScheme = IVA`), el sistema no debe mapear ese IVA al bloque
   `taxes` del payload de Siigo.
2. En su lugar, se agrega un ítem adicional a la factura en Siigo:
   - Descripción: `"IVA"` (`comportamiento.descripcion_item`).
   - Tipo: `Account` (gasto).
   - **Cuenta contable (confirmado 2026-07):** la MISMA cuenta contable que la(s)
     línea(s) de gasto real de ese mismo documento (origen del XML) -- ej. si la
     línea de gasto quedó en `61201801`, el ítem de IVA también va a `61201801`; si
     quedó en `51453001`, el IVA va a `51453001`. **Nunca una cuenta fija genérica**
     -- el antiguo candidato `229999` quedó descartado, no se usa más.
     `motor_reglas` deja esta cuenta en blanco (`None`) a propósito; la completa
     `orquestador._aplicar_sugerencias` una vez que las líneas de gasto ya
     tienen su propia cuenta resuelta, y solo si todas las líneas de gasto del
     documento comparten una única cuenta -- si un documento llegara a mezclar
     líneas con cuentas distintas, el ítem de IVA queda sin cuenta (no se
     adivina) y hay que resolverlo a mano.
   - Cantidad: 1
   - Valor: el monto exacto del IVA que traía el XML original.
   - Sin impuestos asociados a este ítem (ni IVA ni retención) -- si el
     histórico llegara a sugerir uno, se ignora a propósito, igual que con el
     ítem "OTROS IMPUESTOS" de otras empresas.
3. El total de la factura en Siigo debe seguir cuadrando con el total real pagado.

## A qué facturas aplica

- [x] Todas las compras de esta empresa

## Ejemplo real (factura de referencia)

Pendiente: adjuntar el XML real de una factura de Hielo Super-Cool donde se vea el IVA
discriminado en el XML de la DIAN, para usarlo como caso de prueba en
`tests/casos-reales/901528790/`.
