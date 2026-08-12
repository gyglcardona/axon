# Bugs confirmados en el parser anterior (xml_processor.py)

Confirmados leyendo el código real del prototipo anterior, no solo inferidos de los
XML de muestra. Sirven como especificación negativa: el parser nuevo debe hacer
explícitamente lo contrario de cada uno de estos.

## 1. IVA multilínea: solo lee el primer `TaxTotal`, descarta el resto

```python
tax_total_node = root.find('./cac:TaxTotal', ns)   # .find() = singular
total_iva_xml = float(tax_total_node.find('cbc:TaxAmount', ns).text)
```

Si la factura trae varios nodos `cac:TaxTotal` a nivel raíz (el patrón de "IVA en
hasta 12 líneas" de Fase 0), esto lee solo el primero y descarta el resto **sin
ningún error ni log** — el IVA queda subcalculado en silencio.

**Corrección:** `root.findall('./cac:TaxTotal', ns)` y sumar `cbc:TaxAmount` de
todos los nodos.

## 2. Impuestos por línea: mismo problema, a nivel de ítem

```python
tax_node = line.find('.//cac:TaxTotal/cac:TaxSubtotal', ns)   # .find() = singular
iva_p = float(tax_node.find('.//cbc:Percent', ns).text) if tax_node is not None else 0.0
```

Si una línea de factura trae más de un `TaxSubtotal` (por ejemplo IVA + Impoconsumo
en la misma línea), solo se lee el primero.

**Corrección:** `line.findall('.//cac:TaxTotal/cac:TaxSubtotal', ns)`, sumar y
clasificar cada uno por su `TaxScheme`.

## 3. Riesgo de doble conteo en "otros impuestos"

```python
for sub_tax in root.findall('.//cac:TaxTotal/cac:TaxSubtotal', ns):
```

El `.//` busca en *todo* el documento, incluyendo los `TaxSubtotal` que ya están
dentro de cada `InvoiceLine`. Si el total de cabecera ya agrega los impuestos de las
líneas (comportamiento típico de UBL), este bucle los cuenta dos veces: una como
parte del total de cabecera, otra al recorrer cada línea.

**Corrección:** separar explícitamente impuestos a nivel de documento (bajo
`LegalMonetaryTotal`/`TaxTotal` raíz) de impuestos a nivel de línea, y no sumarlos
como si fueran la misma fuente.

## 4. `TaxExclusiveAmount = 0` nunca se contempla

El parser anterior no lee `TaxExclusiveAmount` en ningún punto — solo usa
`PayableAmount`. El hallazgo de Fase 0 (2 proveedores con `TaxExclusiveAmount` en
cero y el dato real en `LineExtensionAmount`) nunca llegó a incorporarse al código.

**Corrección:** el parser nuevo debe leer `TaxExclusiveAmount`, y si es cero, sumar
`LineExtensionAmount` de todas las líneas como respaldo.

## 5. La retención se guarda, pero nunca se resta correctamente después

El parser guarda `total_rete_xml` como columna aparte, correcto. El problema (ya
identificado en `causacion_view.py`, ver `docs/00-contexto/decisiones-arquitectura.md`)
es que el "ajuste sugerido" corriente abajo solo resta la retención y no contempla
impuestos adicionales (IC) que *suman* al total. Este bug no está en el parser sino en
el cálculo posterior — se corrige junto con el motor de reglas nuevo, no en el parser.
