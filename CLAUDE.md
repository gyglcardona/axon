# AXON — Agente contable DIAN → Siigo

Este archivo lo lee Claude Code automáticamente al abrir este proyecto. Contiene lo que
necesitas saber para operar y modificar el sistema sin que el usuario tenga que
reexplicarlo cada vez. Si algo aquí contradice lo que ves en el código, el código manda,
pero avisa antes de asumir que este archivo quedó desactualizado.

## Qué es esto

Sistema que lee facturas electrónicas de la DIAN (XML dentro de ZIP), decide la cuenta
contable de cada línea (con reglas de código primero, con criterio de Claude solo cuando
las reglas no alcanzan) y las causa en Siigo vía API. Sirve hoy a 5 empresas de forma
local; el objetivo de mediano plazo es ofrecerlo como SaaS a otras empresas/contadores.

Contexto completo en `docs/00-contexto/resumen-proyecto.md`. Léelo si es la primera vez
que trabajas en este proyecto en la sesión.

## Reglas duras de arquitectura (no negociables sin discutirlo primero)

1. **Extracción de datos del XML = 0 tokens de IA.** Es determinística, código puro.
   Claude solo entra a interpretar casos ambiguos ya filtrados por el motor de reglas.
2. **Todo pasa primero por el motor de reglas** (`if/else` en código + configuración en
   `config/empresas/` y `config/proveedores/`). Solo lo que las reglas no resuelven pasa
   a Claude. Cada factura queda marcada con `resuelto_por = "reglas" | "claude" | "manual"`.
3. **Nunca enviar nada a la API real de Siigo sin confirmación explícita del usuario**,
   salvo que el usuario haya pedido expresamente automatizar el envío para una empresa
   específica y esa decisión esté registrada en `docs/08-decisiones-pendientes/`.
4. **Credenciales nunca en código ni en `.md` versionado.** Viven en
   `config/empresas/<nit>.json`, que está en `.gitignore`. Si vas a mostrar código de
   ejemplo, usa placeholders (`TU_ACCESS_KEY`), nunca valores reales copiados de otro
   archivo de configuración.
   Cada empresa además tiene `config/empresas/<nit>.md` con datos generales y de
   conexión **no secretos** (razón social, contacto contable, Partner-Id, qué
   políticas están activas). Ese `.md` nunca debe traer `usuario` ni `access_key` —
   solo referencia al JSON donde viven. El `.md` real de cada empresa también está en
   `.gitignore` (igual que el JSON); solo `_template.md` y `EJEMPLO-*.md` se versionan.
5. **`company_id` (o `nit`) en todo.** Ninguna consulta a la base de datos debería poder
   ejecutarse sin saber a qué empresa pertenece. Ver `docs/06-multiempresa-saas/`.
6. **Cada caso raro de XML ya resuelto se guarda como caso de prueba** en
   `tests/casos-reales/<nit-empresa>/`. Antes de cerrar cualquier cambio al parser o al
   motor de reglas, corre `pytest tests/` y confirma que nada existente se rompió.

## Cómo resolver "la empresa X" cuando el usuario te pida ejecutar algo

El usuario va a pedir cosas como "corre la causación de Hielo Super-Cool" sin darte el
NIT. Resuelve el nombre contra `config/empresas/registro.json` (índice maestro de
slug → nit → nombre). Nunca inventes un NIT ni asumas cuál empresa es si el nombre no
hace match claro — pregunta.

## Comandos disponibles

> Esta tabla se actualiza a medida que se construye `src/`. Si el comando no existe
> todavía, dilo explícitamente en vez de simular que lo ejecutaste.

| Comando | Qué hace |
|---|---|
| `python main.py validar-completitud --empresa <slug> --listado <ruta.xlsx> --carpeta <ruta_zip>` | Compara el listado DIAN contra los XML entregados. Ver `docs/03-ingesta-dian/`. |
| `python main.py importar --empresa <slug> --carpeta <yyyy>/<mm>` | Descomprime, parsea XML, guarda en la BD de la empresa. `--carpeta` es relativa a `data/entrada-dian/<slug>/` (ej. `2026/07`), no una ruta completa. |
| `python backend/app.py` | Levanta la API + la interfaz web (`frontend/bandeja-revision.html`) en `http://localhost:5000`. Misma lógica que el CLI, vía `src/orquestador.py`. |
| `python main.py clasificar --empresa <slug>` | Corre el motor de reglas; lo que no resuelve queda pendiente de Claude o de revisión humana. |
| `python main.py enviar-siigo --empresa <slug> --lote <ids>` | Envía a la API real de Siigo. **Pide confirmación con el número de facturas y el total antes de ejecutar.** |
| `pytest tests/` | Corre todos los casos de prueba guardados. |

## Estructura de este repo

```
CLAUDE.md                  este archivo
README.md                  guía humana rápida
docs/                       documentación (ver docs/README implícito: carpetas numeradas = orden de lectura)
config/empresas/            config por empresa cliente (políticas contables + credenciales, NO se versiona el contenido real)
config/proveedores/         config por NIT de proveedor emisor (quirks de facturación)
data/entrada-dian/<slug>/<yyyy>/<mm>/   ZIP y listados que entrega cada empresa por mes (ver docs/03-ingesta-dian/carpetas-entrada.md)
src/                         código (parser, motor de reglas, state_store, orquestador, siigo_client)
backend/app.py               API Flask + sirve frontend/ -- capa delgada sobre src/orquestador.py
frontend/                    interfaz web (bandeja de revisión), consume la API de backend/
main.py                      CLI -- capa delgada sobre src/orquestador.py (misma lógica que backend/app.py)
requirements.txt              dependencias Python (hoy: flask)
tests/casos-reales/          XML reales + resultado esperado, por empresa
```

`main.py` (CLI) y `backend/app.py` (API + interfaz web) nunca duplican lógica de
negocio -- ambos llaman a `src/orquestador.py`. Arrancar la interfaz web:
`python backend/app.py` → `http://localhost:5000`.

## Al modificar el motor de reglas

Antes de cambiar `src/rules_engine` (cuando exista), lee
`docs/02-reglas-negocio/README.md` — ahí está la jerarquía: **política de empresa
primero, luego perfil de proveedor, luego regla genérica**. Son dos niveles distintos y
no son intercambiables.
