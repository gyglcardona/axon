# Resumen del proyecto: Agente contable DIAN → Siigo

> Este documento resume todo lo hablado en el chat anterior. Súbelo como archivo de conocimiento en tu Project de Claude para que cualquier conversación nueva dentro de ese Project tenga el contexto completo sin que tengas que reexplicarlo.

## Contexto del usuario

- Implementador de software, conocimientos básicos de programación (no desarrollador de carrera).
- Le presta un servicio a una contadora: causa los gastos de **5 empresas** en **Siigo**.
- Volumen actual: **500–600 facturas mensuales** entre las 5 empresas.
- Nuevo en Claude — está aprendiendo a usar la herramienta mientras avanza en este proyecto.

## El problema a resolver

Reemplazar una aplicación propia actual (hecha en Python, con ayuda de Gemini, copiando y pegando código) que:
- Lee ZIP de la DIAN, guarda en SQLite, hace *matching* de conceptos contra compras ya existentes en Siigo para sugerir cuenta contable ("simulador de aprendizaje": aprende de compras ya contabilizadas en Siigo, no de reglas fijas).
- **No es confiable**: hay variables que maneja mal, obligando a revisar documento por documento antes de enviar a Siigo.
- Tiene una interfaz lenta e ineficiente (entra a cada documento, guarda, vuelve atrás — sin vista de lote).
- El proceso de desarrollo anterior se estancaba: cada corrección nueva rompía silenciosamente algo que ya funcionaba, sin forma de detectarlo a tiempo.

## Objetivo

Construir un sistema nuevo, desde cero, con **control total**, que:
1. Lea ZIP/XML de la DIAN de las 5 empresas (multiempresa desde el diseño).
2. Cause los gastos automáticamente en Siigo vía API.
3. Escale a más empresas y, eventualmente, a un modelo SaaS (dar acceso a otros usuarios) sin rehacer la arquitectura base.

## Decisiones de arquitectura ya tomadas

- **Extracción de datos del XML = 0 tokens de IA.** Es una tarea determinística (código puro). Claude/IA solo entra para *interpretar* casos ambiguos.
- **Filtrado explícito antes de usar IA**: un motor de reglas en código decide (`if/else`, no "magia") qué facturas se resuelven solas y cuáles necesitan criterio de Claude. Se guarda en BD de qué forma se resolvió cada una (`resuelto_por = "reglas"` o `"claude"`) para trazabilidad y control de costos.
- **Perfiles de reglas por proveedor emisor (NIT)**, no por proveedor tecnológico — ahí es donde realmente se repiten (o no) los problemas mes a mes. Guardados como archivos de configuración separados del código, así corregir uno no rompe otros.
- **Backend (API) separado del frontend** desde el día uno — permite que el mismo sistema corra local en el PC hoy, y en un servidor en internet (SaaS) más adelante, sin reescribir lógica de negocio.
- **Multiempresa desde el inicio** (`company_id` en todo), aunque hoy solo se use localmente.
- **Base de datos con capa de abstracción**: SQLite ahora, migrable a Postgres después sin reescritura mayor.
- **Interfaz de revisión en modo lote** (tipo hoja de cálculo, edición inline, aprobación en bloque) en vez de documento por documento — resuelve la lentitud de la app actual.
- **Control de versiones (Git) + pruebas automáticas** desde el primer día: cada XML raro ya resuelto se guarda como caso de prueba; cualquier cambio nuevo corre automáticamente contra todos los casos guardados, para detectar si algo se rompió antes de que sea un problema en producción.
- **Fase de aprobación humana al inicio** (revisar antes de enviar a Siigo); solo se automatiza el envío sin revisión cuando la tasa de acierto sea alta y estable por varios meses. No se prometió automatización 100% desde el día uno — no sería responsable con datos contables de terceros.

## Hallazgos reales de la Fase 0 (análisis de 9 XML de muestra + plan de cuentas de Hielo Super-Cool S.A.S.)

Todos los XML de muestra vienen como `Invoice` directo (no envueltos en `AttachedDocument`).

| Patrón encontrado | Detalle | Implicación |
|---|---|---|
| **Retención separada, no descontada del total** | Confirmado con un proveedor: `WithholdingTaxTotal` (CREE) viene informado, pero `LegalMonetaryTotal/PayableAmount` **no la resta** | El sistema debe sumar/restar retenciones manualmente según el caso — nunca asumir que el total del XML ya viene neto |
| **`TaxExclusiveAmount` en cero, con dato real en `LineExtensionAmount`** | 2 proveedores específicos con esta inconsistencia | Usar `LineExtensionAmount` como respaldo cuando `TaxExclusiveAmount = 0` |
| **IVA desglosado en múltiples líneas `TaxTotal`** (una por ítem) | Hasta 12 líneas de IVA en una sola factura | Sumar todas las líneas, no tomar solo la primera |
| **Impuesto al Consumo (IC) además de IVA** | Un proveedor (KOPPS) maneja ambos como impuestos que sí afectan el total | Mapear como tipo de impuesto adicional, no confundir con IVA |
| **Plan de cuentas Siigo**: columna "Nivel agrupación" | Solo las filas marcadas **"Transaccional"** son cuentas usables para contabilizar; el resto son agrupadoras | El motor de sugerencia de cuentas debe filtrar solo por transaccionales |

**Pendiente:** correr este mismo análisis sobre el histórico completo (4 meses × 5 empresas) para tener el mapa completo de patrones antes de tocar Siigo.

## Sobre DIAN y Siigo (aclaraciones ya investigadas)

- **No existe API pública de descarga masiva de la DIAN con token** para contribuyentes normales — la descarga automatizada fue restringida (CAPTCHA). El flujo de ZIP/XML manual en carpeta es el camino viable hoy. El "listado" descargable manualmente del portal DIAN sirve como chequeo de completitud (¿me falta alguna factura?), no reemplaza el XML para el detalle de impuestos.
- **Siigo API**: autenticación vía `/auth` con `username` + `access_key`, header `Partner-Id` en cada request. Endpoints relevantes ya identificados: `/v1/purchase-support-documents` (documentos soporte/compras), `/v1/account-groups` (a confirmar si corresponde exactamente al plan de cuentas completo o es más específico de inventario — pendiente de validar contra el Excel).

## Presupuesto estimado (tokens de IA)

Con 500–600 facturas/mes, filtrando con el motor de reglas para que solo ~150-200 pasen por Claude:
- Modelo económico (Haiku): ~$0.50–$2 USD/mes
- Modelo intermedio (Sonnet): ~$2–$6 USD/mes
- Presupuesto inicial recomendado: **$20-25 USD en créditos de la Claude Platform (Console)**, suficiente para varios meses de pruebas.

*(Precios de referencia — siempre verificar el [pricing oficial](https://platform.claude.com/docs/en/about-claude/pricing) porque cambia.)*

## Prototipo de código ya construido (fase inicial, para retomar en Claude Code)

Estructura ya armada y probada con un XML sintético:
```
dian-agent/
  main.py                 # orquestador
  config/companies.example.json
  src/
    zip_handler.py         # descomprime ZIP, localiza XML
    xml_parser.py           # parsea UBL, detecta proveedor tecnológico (ajustar con hallazgos Fase 0)
    state_store.py          # SQLite: evita duplicados, guarda estado
    siigo_client.py         # auth + envío a Siigo (plantilla, falta probar contra API real)
    json_builder.py         # mapeo a payload Siigo (plantilla, falta mapear con catálogos reales)
  README.md
```

## Próximos pasos (en orden)

1. Instalar **Claude Desktop** (gratis) e iniciar sesión con la misma cuenta — confirmar que el proyecto/chat se puede retomar ahí.
2. Activar **Claude Code** (requiere plan Pro, ~$20 USD/mes) cuando se vaya a ejecutar contra archivos reales y la API de Siigo.
3. Correr el script de inventario (Fase 0) sobre el histórico completo de 4 meses de las 5 empresas.
4. Ajustar `xml_parser.py` y los perfiles por proveedor con los hallazgos completos.
5. Confirmar si `/v1/account-groups` de Siigo corresponde al plan de cuentas completo; si sí, dejar de subirlo manual.
6. Construir el motor de reglas (usando también el histórico de compras ya en Siigo, como hace la app actual, pero con trazabilidad de origen y filtrando por cuentas "Transaccionales").
7. Construir la interfaz de revisión en modo lote.
8. Conectar envío real a Siigo con control de estado (evitar duplicados, registrar rechazos).
9. Una vez estable, evaluar automatización sin revisión previa, y más adelante el salto a SaaS.

## Notas prácticas

- Antes de compartir el código con terceros (ej. el implementador de software con quien colabora en otro proyecto separado), revisar que no queden credenciales reales (API key de Siigo, tokens) escritas directamente — deben ir en archivo de configuración aparte que no se comparte.
- El código y las reglas viven como archivos en el computador, no "dentro" de una cuenta de Claude — son portables entre cuentas y equipos sin complicación.
