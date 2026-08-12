# Validador de completitud: listado DIAN vs. archivos entregados

## Formato confirmado (a partir de un listado real)

El listado exportado desde el portal de la DIAN trae estas columnas:

`Tipo de documento, CUFE/CUDE, Folio, Prefijo, Divisa, Forma de Pago, Medio de Pago,
Fecha Emisión, Fecha Recepción, NIT Emisor, Nombre Emisor, NIT Receptor, Nombre
Receptor, IVA, ICA, IC, INC, Timbre, INC Bolsas, IN Carbono, IN Combustibles, IC Datos,
ICL, INPP, IBUA, ICUI, Rete IVA, Rete Renta, Rete ICA, Total, Estado, Grupo`

Confirmado con un archivo real (338 filas, 0 CUFE duplicados):

- **Trae `CUFE/CUDE` siempre presente** — se usa como llave principal de comparación,
  como estaba previsto.
- **Columna `Grupo`** distingue `Emitido` de `Recibido`. Para el validador de compras
  solo interesan las filas `Recibido` (el NIT de la empresa aparece en `NIT Receptor`).
- **El listado ya trae impuestos desglosados por documento**: `IVA`, `ICA`, `IC`,
  `INC`, `Timbre`, retenciones. Esto es más de lo previsto — se puede usar como
  **segundo cruce de validación**, comparando el IVA que reporta la DIAN en el listado
  contra el IVA que el sistema calcula al parsear el XML. Si no coinciden, es señal de
  alerta antes de causar la factura, no solo de que falte el archivo.
- `Tipo de documento` incluye valores que no son facturas de compra (`Nomina
  Individual`, `Application response`) — filtrar por `Tipo de documento = 'Factura
  electrónica'` (o el tipo de documento soporte que aplique) antes de comparar.

## Cómo se valida

1. El usuario descarga manualmente del portal de la DIAN el listado del periodo.
2. El sistema recibe también el ZIP con los XML entregados.
3. Se filtra el listado a `Grupo = 'Recibido'` y `NIT Receptor` = NIT de la empresa
   activa.
4. Se compara contra los XML del ZIP usando `CUFE` como llave — el CUFE está en el
   XML (`cbc:UUID`) y en el listado (`CUFE/CUDE`).
5. Opcional pero recomendado: comparar el `IVA` del listado contra el IVA calculado
   del XML por el parser, como chequeo cruzado adicional.

## Salida del validador

| Categoría | Significado | Acción |
|---|---|---|
| **En listado, sin XML** | Falta el archivo | Bloquea esa factura hasta conseguir el XML; el resto del lote sigue |
| **Con XML, no en listado** | Puede ser normal (otro periodo) o el listado no cubre el rango completo | Alerta, no bloquea |
| **Coincide, pero IVA no cuadra contra el listado** | El parser calculó un IVA distinto al reportado por la DIAN | Alerta — no bloquea, pero se marca para revisión antes de enviar a Siigo |
| **Coincide en todo** | Todo en orden | Sigue el flujo normal |

## Cuándo corre

Antes del motor de reglas, cada vez que se importa una carpeta/ZIP nueva.

## Trazabilidad

Cada corrida queda guardada (empresa, fecha, cuántas coincidieron, cuántas faltaron,
cuántas sobraron, cuántas con IVA en disputa).

## Pendiente

Este formato se confirmó con un listado de otra empresa (NIT 901518066), no de Hielo
Super-Cool ni de las otras 4 empresas del proyecto — falta confirmar que el mismo
formato aplica igual al descargarlo desde las cuentas reales de estas 5 empresas antes
de darlo por definitivo.
