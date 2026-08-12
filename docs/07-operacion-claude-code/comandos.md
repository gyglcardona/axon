# Cómo pedirle a Claude que ejecute el proceso de una empresa

Objetivo: que cuando digas algo como "corre la causación de Hielo Super-Cool" o
"valida la completitud de la empresa X con este listado", Claude sepa exactamente qué
comando ejecutar sin que tengas que explicarle el proceso cada vez — incluso si en el
futuro agregas más empresas.

## Por qué esto funciona sin volver a explicar nada

1. `CLAUDE.md` en la raíz ya documenta la tabla de comandos disponibles.
2. `config/empresas/registro.json` es el índice único de "nombre que usa el usuario" →
   `slug` → `nit`. Cuando agregues una empresa nueva, solo agregas una fila ahí — no
   hay que enseñarle nada nuevo a Claude, porque el patrón ya está documentado y el
   comando es el mismo para cualquier empresa, solo cambia el `--empresa <slug>`.
3. Si dices el nombre de una empresa y no hace match claro en el registro, Claude debe
   preguntar cuál es, nunca adivinar el NIT.

## Ejemplo de flujo esperado

> Tú: "Corre la causación de Hielo Super-Cool con el ZIP que dejé en la carpeta de
> julio."
>
> Claude: busca `hielo-super-cool` en `config/empresas/registro.json`, confirma el
> `slug`, y ejecuta en orden: `validar-completitud` → si pasa, `importar` → `clasificar`.
> Se detiene antes de `enviar-siigo` y te muestra el resumen (cuántas por reglas,
> cuántas necesitan tu criterio) para que decides tú si envías el lote.

## Qué NO debe hacer Claude sin pedírselo explícitamente

- Enviar facturas a la API real de Siigo sin que tú confirmes el lote y el total.
- Agregar una empresa nueva al registro sin que tú des el NIT real (nunca inventarlo).
- Modificar `config/empresas/<nit>.json` de una empresa distinta a la que le pediste
  que tocara.

## Al agregar una empresa nueva

Con que el usuario dé el NIT, nombre y credenciales de Siigo (usuario, access_key,
clave de portal, partner_id) en el chat, esto queda cubierto sin volver a preguntar:

1. Crea `config/empresas/<nit>.json` (usa `config/empresas/_template.json`) con las
   credenciales reales.
2. Crea `config/empresas/<nit>.md` (usa `config/empresas/_template.md`) con los datos
   generales y de conexión no secretos (representante legal, Partner-Id, ambiente).
   Nunca escribir ahí `usuario`/`access_key`/`clave_portal_siigo`.
3. Agrégala a `config/empresas/registro.json` (slug, nit, nombre, ruta al `.json`).
4. Crea `data/entrada-dian/<slug>/<yyyy>/<mm>/` para el mes en curso (ver
   `docs/03-ingesta-dian/carpetas-entrada.md`).
5. Si tiene alguna política contable propia (como el caso del IVA no discriminado),
   documéntala en `docs/02-reglas-negocio/politicas-empresa/` siguiendo la plantilla —
   esto normalmente no se sabe el primer día, se agrega cuando se confirme con la
   contadora.
6. Después de esto, los comandos de `CLAUDE.md` ya funcionan para la empresa nueva sin
   más cambios.

Lo único que Claude nunca debe hacer solo: inventar un NIT o un slug si el usuario no
lo da explícitamente, o agregar una política contable sin que el usuario la confirme.
