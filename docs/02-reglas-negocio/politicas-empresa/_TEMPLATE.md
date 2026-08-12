# Política de empresa: <nombre de la empresa>

- **NIT:** <nit>
- **Slug:** <slug-usado-en-config-y-comandos>
- **Estado:** activa | en pruebas | desactivada
- **Archivo de configuración correspondiente:** `config/empresas/<nit>.json`

## Qué se observó

<Describe el comportamiento real, con un ejemplo concreto de una factura si es
posible. No generalices sin evidencia — si solo lo viste una vez, dilo.>

## Por qué pasa

<La razón de negocio, no técnica. Ej: "la contadora prefiere no discriminar el IVA
porque..." — si no sabes el por qué, escribe "sin confirmar con la contadora" en vez
de inventar una explicación.>

## Cómo debe manejarlo el sistema

<Reglas exactas, sin ambigüedad, como para que alguien que no conoce el caso pueda
implementarlo leyendo solo esto.>

## A qué facturas aplica

- [ ] Todas las compras de esta empresa
- [ ] Solo ciertas categorías (especificar)
- [ ] Solo mientras no se confirme lo contrario con la contadora

## Ejemplo real (factura de referencia)

<Adjunta o referencia el XML/número de factura que sirvió de base. Ese mismo caso
debería terminar como caso de prueba en `tests/casos-reales/<nit>/`.>
