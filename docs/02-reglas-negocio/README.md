# Reglas de negocio: dos niveles, no confundirlos

Hay dos tipos de reglas y se aplican en este orden:

1. **Política de empresa** (`politicas-empresa/`) — aplica a **todas** las compras de
   una empresa cliente, sin importar el proveedor. Es una decisión contable de la
   empresa/contadora sobre *cómo quiere ver sus libros*.
2. **Perfil de proveedor** (`perfiles-proveedor/`) — aplica a **un NIT de proveedor
   específico**, sin importar cuál de las 5 empresas le compró. Es sobre *cómo ese
   proveedor emite sus facturas* (errores de formato, impuestos raros, etc.).

Si ambas aplican a la misma factura, la política de empresa tiene prioridad porque es
una decisión explícita de cómo contabilizar, mientras que el perfil de proveedor
normalmente solo corrige cómo *interpretar* el dato del XML.

## Cómo se documenta cada regla nueva

Cada regla nueva son **dos archivos**, nunca uno solo:

1. `docs/02-reglas-negocio/<nivel>/<nit>-<slug>.md` — explica el caso real en palabras:
   qué se observó, por qué pasa, cómo se debe manejar. Para humanos y para que Claude
   entienda el contexto antes de tocar código.
2. `config/<empresas|proveedores>/<nit>.json` — la traducción de esa regla a algo que
   el motor de reglas ejecuta literalmente. Sin ambigüedad.

Nomenclatura de archivos: `<nit-sin-puntos-ni-guion-de-verificacion>-<nombre-en-kebab-case>.md`.
Ejemplo: `900412558-hielo-super-cool.md`. El NIT primero para que la carpeta quede
ordenada alfabéticamente por empresa/proveedor, no por nombre.

## Plantillas

Usa `_TEMPLATE.md` de la carpeta correspondiente como punto de partida — no empieces
un archivo de regla desde cero, para que todas queden con la misma estructura y no se
te olvide documentar algo importante (como el ejemplo real, que es lo que hace que la
regla sea entendible seis meses después).

## Ejemplos ya incluidos

- `politicas-empresa/EJEMPLO-800111222-iva-no-discriminado.md` — el caso que describiste:
  una empresa donde la contadora no discrimina el IVA, se causa como línea adicional.
- `perfiles-proveedor/EJEMPLO-kopps-impoconsumo.md` — el caso real de Fase 0, proveedor
  KOPPS con Impuesto al Consumo.

Duplica esos archivos, cámbiales el NIT y el nombre, y ajusta el contenido a tu caso
real. El NIT de ejemplo `800111222` es ficticio — reemplázalo por el NIT real de la
empresa antes de usarlo en producción.
