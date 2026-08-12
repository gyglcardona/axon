# Perfil de proveedor: KOPPS

- **NIT del proveedor:** <completar con el NIT real de la muestra de Fase 0>
- **Slug:** kopps
- **Archivo de configuración correspondiente:** `config/proveedores/<nit-kopps>.json`

## Qué se observó

Este proveedor factura con Impuesto al Consumo (IC) además del IVA. Ambos impuestos
afectan el total de la factura y deben tratarse como conceptos separados — el IC no es
una variante del IVA ni debe sumarse a la misma línea de impuesto.

## Cómo debe manejarlo el sistema

1. Detectar el `TaxScheme` correspondiente a Impoconsumo en el XML (distinto del de IVA).
2. Mapear a un tipo de impuesto adicional en el payload de Siigo, no fusionarlo con el IVA.
3. Sumar ambos impuestos al validar que el total de la factura cuadra con
   `LegalMonetaryTotal/PayableAmount`.

## Empresas afectadas

Cualquiera de las 5 empresas que le compre a este proveedor — el comportamiento es del
proveedor, no depende de quién compra.
