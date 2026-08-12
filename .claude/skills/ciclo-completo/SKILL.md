---
name: ciclo-completo
description: Corre el ciclo completo de causación de una empresa para un mes puntual -- buscar carpeta local, sincronizar Drive si aplica, importar, validar completitud, informar qué falta, y solo entonces (con confirmación explícita) enviar a Siigo.
argument-hint: [empresa] [mes, ej. 2026-07 o "julio 2026"]
---

# Ciclo completo de causación

Ejecuta, en orden, el ciclo completo de una empresa para un mes puntual. Cada
paso puede detenerse si hay algo ambiguo o si falta información -- nunca se
adivina un NIT, una empresa, ni se envía nada a Siigo sin confirmación
explícita del usuario (CLAUDE.md, reglas 3 y "cómo resolver la empresa X").

Este skill orquesta funciones que ya existen en `src/orquestador.py` -- no
duplica lógica, solo encadena los pasos y decide qué mostrarle al usuario en
cada uno. Usa `python -c "..."` con `sys.path.insert(0, 'src')` para
invocarlas (mismo patrón usado en toda la sesión de este proyecto), no
inventes comandos de CLI que no existen (`main.py` hoy solo implementa
`importar`).

## Paso 0 -- Resolver empresa y período

- Empresa: hacer match del nombre dado contra
  `config/empresas/registro.json` (slug → nit → nombre). Si el nombre no
  hace match claro con exactamente una empresa, **preguntar** cuál es
  (`AskUserQuestion`), nunca asumir.
- Período: normalizar a `yyyy/mm` (ej. "julio 2026" → `2026/07`).

## Paso 1 -- Buscar en la carpeta local

```python
from pathlib import Path
carpeta = Path(f"data/entrada-dian/{slug}/{carpeta_relativa}")
print(carpeta.is_dir(), len(list(carpeta.glob("*.zip"))) if carpeta.is_dir() else 0)
```

Reportar cuántos ZIP hay ya localmente para ese período, antes de tocar nada más.

## Paso 2 -- Sincronizar desde Drive (solo si la empresa tiene carpeta configurada)

```python
import sys; sys.path.insert(0, "src")
import orquestador
conexion = orquestador.obtener_conexion_drive(slug)
if conexion["configurado"]:
    resumen_drive = orquestador.importar_desde_drive(slug)
```

`importar_desde_drive` sincroniza y clasifica TODO lo nuevo de la carpeta de
Drive de esa empresa (no solo el mes pedido -- es idempotente y barato
repetirlo, ver `docs/03-ingesta-dian/importar-desde-drive.md`), así que si
la empresa tiene Drive configurado este paso ya cubre también el Paso 3 para
lo que venga de ahí. Si `conexion["configurado"]` es `False`, informar que
esta empresa no tiene Drive conectado y seguir solo con lo local.

## Paso 3 -- Importar lo que haya localmente (si no vino todo por Drive)

```python
resumen_importar = orquestador.ejecutar_importar(slug, carpeta_relativa)
```

Reportar: nuevas, ya existentes, duplicadas, no-facturas, con error. Si
`con_error > 0`, mostrar el detalle -- no seguir como si nada.

## Paso 4 -- Validar completitud (si hay un listado DIAN en esa carpeta)

```python
listados = orquestador.listar_archivos_listado(slug, carpeta_relativa)
if listados:
    resultado = orquestador.validar_completitud(slug, carpeta_relativa, listados[0], desde, hasta)
```

`desde`/`hasta` son el rango de fechas del mes pedido (ej. `2026-07-01` /
`2026-07-31`). Reportar `faltantes` y `sobrantes_en_bandeja` si los hay. Si
no hay ningún `.xlsx` de listado en la carpeta, decirlo explícitamente y
seguir (no es un error bloqueante, pero el usuario debe saber que no se
validó completitud).

## Paso 5 -- Informar qué falta antes de poder enviar

Para las facturas del período (`orquestador.listar_facturas(slug)` filtrado
por fecha), usar `orquestador.previsualizar_envio_siigo(slug, cufes)` sobre
todas -- **no envía nada**, solo calcula. Agrupar y reportar:

- Cuántas son `enviable=True` (con su total sumado).
- Cuántas están bloqueadas y por qué (`motivos_bloqueo`): cuenta contable
  faltante, tipo de comprobante faltante, medio de pago faltante -- agrupar
  por proveedor para que sea accionable, no solo una lista plana de
  facturas.
- Notas relevantes que dejó el motor de reglas (OTROS IMPUESTOS, ajustes,
  advertencias del parser) -- un resumen, no el texto completo de cada una.

## Paso 6 -- Confirmar antes de enviar a Siigo

**Nunca enviar automáticamente.** Mostrar el resumen del Paso 5 y usar
`AskUserQuestion` con el número exacto de facturas y el total exacto en
pesos antes de llamar a `orquestador.confirmar_envio_siigo`. Si el usuario
no confirma en este mismo turno, el ciclo termina en el Paso 5 -- reportar
el estado y quedar a la espera, sin reintentar solo.

Después de enviar, si el lote es grande (más de ~30 facturas), correr
`orquestador.descargar_compras_siigo` y comparar contra lo local (total,
cuenta contable, sin residuales de impuestos en ítems inyectados) antes de
decir que el ciclo terminó limpio -- mismo patrón de verificación usado en
todo este proyecto, no dar por bueno un envío masivo sin comparar.
