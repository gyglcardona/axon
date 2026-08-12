# AXON — Agente contable DIAN → Siigo

Guía rápida para humanos. Si vas a trabajar con Claude Code en este proyecto, el
archivo que de verdad importa es `CLAUDE.md` — este README es solo el punto de entrada.

## Empezar

1. Lee `docs/00-contexto/resumen-proyecto.md` — qué es esto y por qué existe.
2. Lee `docs/00-contexto/decisiones-arquitectura.md` — qué ya se decidió y por qué,
   para no volver a discutirlo desde cero.
3. Si vas a agregar una empresa nueva o un proveedor con comportamiento raro, ve
   directo a `docs/02-reglas-negocio/README.md`.

## Cómo se lee `docs/`

Las carpetas están numeradas en el orden en que tiene sentido leerlas la primera vez.
No es obligatorio, pero si no sabes por dónde empezar, sigue el número.

| Carpeta | Contenido |
|---|---|
| `00-contexto` | Qué es el proyecto, decisiones ya tomadas |
| `01-hallazgos-fase0` | Patrones reales encontrados en los XML de muestra |
| `02-reglas-negocio` | Cómo se configuran políticas por empresa y perfiles por proveedor |
| `03-ingesta-dian` | Validación de completitud (listado DIAN vs. archivos entregados) |
| `04-integracion-siigo` | Autenticación, endpoints, mapeo de payload |
| `05-esquema-datos` | Modelo de base de datos |
| `06-multiempresa-saas` | Aislamiento de datos entre empresas, camino a SaaS |
| `07-operacion-claude-code` | Cómo pedirle a Claude que ejecute el proceso de una empresa |
| `08-decisiones-pendientes` | Preguntas abiertas, todavía sin resolver |

## Config vs. docs — no son lo mismo

- `docs/**/*.md` es para que un humano (o Claude) entienda el **por qué**.
- `config/**/*.json` es lo que el **código realmente lee** en tiempo de ejecución.

Cada regla de negocio nueva se documenta en un `.md` y se traduce a un `.json`. El `.md`
explica el caso real y por qué existe la regla; el `.json` es la fuente de verdad que
ejecuta el sistema. Si solo tocas uno de los dos, la regla queda a medias.
