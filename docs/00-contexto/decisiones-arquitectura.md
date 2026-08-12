# Decisiones de arquitectura

Formato: cada decisión dice qué se decidió, por qué, y qué se descartó. Si en el futuro
alguien (humano o Claude) quiere cambiar una de estas, que primero lea el "por qué" y el
"se descartó" antes de proponer volver atrás.

## Extracción de XML sin IA

**Decisión:** parsear el XML de la DIAN es código puro, determinístico, cero tokens.
**Por qué:** es una tarea mecánica; usar IA para esto sería lento, caro e impredecible
sin necesidad.
**Se descartó:** usar un LLM para "leer" el XML completo — no aporta nada que un parser
UBL no haga mejor y más barato.

## Motor de reglas antes que Claude

**Decisión:** todo pasa por reglas de código primero. Claude solo ve lo que las reglas
no resolvieron. Se guarda `resuelto_por` en cada factura.
**Por qué:** control de costos, trazabilidad, y previsibilidad — si algo falla, se puede
rastrear si fue una regla mal escrita o un caso genuinamente ambiguo.

## Dos niveles de reglas: empresa y proveedor (no son lo mismo)

**Decisión:** existen **políticas por empresa cliente** (aplican a todas las compras de
esa empresa sin importar el proveedor) y **perfiles por proveedor emisor** (aplican a
un NIT de proveedor específico, sin importar qué empresa le compra). Ver
`docs/02-reglas-negocio/README.md`.
**Por qué:** surgió del caso real de una empresa donde la contadora, por convención
propia, no discrimina el IVA y lo causa como línea adicional del gasto — esto no
depende del proveedor, depende de cómo esa empresa específica quiere ver sus libros.
Tratarlo como "regla de proveedor" habría sido incorrecto: se habría tenido que
duplicar la misma regla para cada NIT de proveedor de esa empresa, en vez de
declararla una sola vez a nivel de empresa.
**Se descartó:** un solo nivel de reglas por NIT de proveedor (el diseño original) —
no alcanza para políticas contables que son decisión de la empresa cliente, no del
proveedor.

## Perfiles de reglas en archivos de configuración, no en código

**Decisión:** `config/empresas/*.json` y `config/proveedores/*.json`, separados del
código fuente.
**Por qué:** corregir una regla de una empresa no debe poder romper la de otra, y no
debería requerir tocar `src/`.

## Archivo `.md` de conexión por empresa, separado del JSON de credenciales

**Decisión:** cada empresa tiene dos archivos en `config/empresas/`: el `.json` de
siempre (credenciales reales de Siigo + políticas, gitignored) y un `.md` nuevo
(`<nit>.md`) con datos generales y de conexión **no secretos** — razón social,
contacto contable, Partner-Id, ambiente, qué políticas están activas. El `.md` nunca
lleva `usuario` ni `access_key`, solo referencia a dónde viven.
**Por qué:** el `.json` es difícil de leer/actualizar a mano para alguien no técnico
(un contador, o el propio usuario) y mezclar ahí datos generales de la empresa junto a
secretos invita a que alguien copie el archivo completo como "ejemplo" y filtre una
credencial. Separar en dos archivos permite que el `.md` se comparta o revise sin
riesgo, y deja el `.json` como lo único que de verdad necesita máxima protección.
**Se descartó:** meter las credenciales reales dentro del `.md` — contradice la regla
4 de `CLAUDE.md` (nunca credenciales en `.md` versionado) y elimina la ventaja de
poder mostrar/compartir este archivo sin cuidado especial.

## Base de datos: SQLite hoy, migrable a Postgres

**Decisión:** una base SQLite por empresa (`data/empresas/<nit>.db`), con capa de
abstracción para no acoplar el código a SQLite.
**Por qué:** para 5 empresas locales es más que suficiente, y de hecho da aislamiento
físico real entre empresas (ver `docs/06-multiempresa-saas/aislamiento-datos.md`).
**Pendiente:** decidir el punto exacto de migración a Postgres cuando se pase a SaaS.

## Backend separado del frontend desde el día uno

**Decisión:** lógica de negocio en una API, no mezclada con la interfaz.
**Por qué:** permite correr local hoy y en un servidor web mañana sin reescribir nada
de negocio, solo la capa de presentación.

## Impuestos por línea, no columnas fijas por tipo de impuesto

**Decisión:** en vez de columnas como `xml_total_iva`, `xml_total_retencion`,
`xml_otros_impuestos` (diseño anterior), usar una tabla `detalle_impuestos` con una
fila por impuesto aplicado a cada línea (`tipo`, `base`, `porcentaje`, `valor`,
`fuente`).
**Por qué:** la Fase 0 mostró IVA desglosado en hasta 12 líneas por factura, Impuesto
al Consumo sumándose además del IVA, y retenciones que no se descuentan del total. Un
esquema de columnas fijas no escala a esa variabilidad sin alterar la tabla cada vez
que aparece una combinación nueva.
**Se descartó:** el esquema de columnas fijas del prototipo anterior.
