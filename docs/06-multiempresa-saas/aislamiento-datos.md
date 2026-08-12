# Aislamiento de datos entre empresas

Objetivo: cuando esto sea un SaaS con varias empresas registradas, una empresa jamás
debe poder ver, ni por error de código, los datos de otra.

## Hoy (local, SQLite por empresa)

Cada empresa tiene su propio archivo `data/empresas/<nit>.db`. Esto ya da **aislamiento
físico real**: no existe una consulta que pueda cruzar datos de dos empresas por
accidente, porque ni siquiera es la misma conexión de base de datos. Es una ventaja del
diseño actual, no solo deuda técnica — vale la pena conservar esta propiedad al migrar.

## Al migrar a Postgres (SaaS)

Dos caminos, con trade-offs distintos:

**A. Un esquema por empresa dentro de la misma base Postgres**
Mantiene el mismo nivel de aislamiento fuerte que hoy — cada esquema es, en la
práctica, una base separada. Más simple de razonar y de auditar. Se vuelve más pesado
de administrar cuando el número de empresas crece a cientos (cada esquema es una
migración más que correr).

**B. Filas compartidas con `company_id` + Row-Level Security (RLS) de Postgres**
Todas las empresas en las mismas tablas, pero Postgres bloquea a nivel de motor de
base de datos cualquier fila que no pertenezca a la empresa de la sesión activa — no
solo a nivel de código de la aplicación. Así, aunque una consulta tenga un bug y se le
olvide filtrar por `company_id`, la base de datos igual no devuelve filas ajenas.
Escala mejor a muchas empresas, pero es más complejo de configurar y probar bien.

**Recomendación:** empezar por A (esquema por empresa) porque es la evolución más
directa de lo que ya existe hoy y da seguridad por defecto sin esfuerzo extra. Migrar a
B solo si el número de empresas activas lo justifica.

## Reglas no negociables, sea cual sea el camino elegido

1. Toda sesión de usuario autenticado lleva la lista de `company_id` a los que tiene
   acceso. Nunca se confía en un `company_id` que venga del frontend sin validarlo
   contra esa lista, en cada request.
2. Ninguna consulta a la base de datos se escribe sin `company_id` explícito en el
   `WHERE`, incluso si "en teoría" la conexión ya está limitada a una empresa —
   defensa en profundidad.
3. **Pruebas de fuga de datos obligatorias**: como parte de la batería de pruebas
   automáticas (ver `CLAUDE.md`), debe existir al menos un test que, autenticado como
   usuario de la empresa A, intente leer datos de la empresa B y confirme que siempre
   falla. Este test corre en cada cambio, igual que los casos de XML guardados.

## Registro de empresas: resuelto 2026-08

`POST /api/auth/registrar-empresa` (público, sin sesión — ver
`orquestador.registrar_empresa_nueva`) es la puerta de entrada real:

- Genera su propio NIT (lo manda quien se registra, saneado a solo-dígitos — es la
  única defensa contra path traversal, porque termina siendo el nombre de archivo de
  `config/empresas/<nit>.json` y `data/empresas/<nit>.db`) como llave de todo.
- Materializa `data/empresas/<nit>.db` en el mismo momento del registro (no como
  paso manual ni la primera vez que importa algo).
- No crea `config/empresas/<nit>.json` (credenciales Siigo, políticas) — ese archivo
  se sigue creando solo, perezosamente, la primera vez que la propia empresa guarda
  algo desde "Configuración". Hasta entonces no hay ninguna credencial que reusar ni
  modo demo que tocar: el motor de reglas simplemente opera con perfil de proveedor /
  regla genérica.
- El usuario creado siempre tiene rol `"empresa"` — nunca viene del cliente, así que
  autorregistrarse jamás puede crear un superusuario o contador (ver
  `tests/test_registro_empresa.py`, `tests/test_backend_auth.py`).
- Queda sin contraseña hasta que confirma el correo (mismo token de un solo uso que
  una invitación normal, ver `docs/08-decisiones-pendientes/`) — nadie puede iniciar
  sesión con una empresa que nadie verificó.
- Freno contra abuso: máximo 3 intentos por correo por hora
  (`auth_store.contar_intentos_registro_empresa_recientes`), para que no sirva de
  vector para bombardear un correo ajeno de invitaciones repitiendo el registro con
  NITs distintos.

**Riesgo residual, aceptado conscientemente:** cualquiera puede escribir el NIT y la
razón social de una empresa real que no le pertenece y "ocuparla" en el registro
(namesquatting). El impacto es bajo — no expone ningún dato (la empresa nace vacía,
sin credenciales ni facturas) y el peor caso es que el dueño real, al intentar
registrarse después, tenga que pedirle a un superusuario que le transfiera el acceso
en vez de registrarse solo. No se resolvió con verificación de NIT contra un registro
oficial (DIAN/RUES no tiene una API pública confiable para esto) ni con aprobación
manual de un superusuario por cada alta (mataría el punto de tener autorregistro).
Si esto se vuelve un problema real, la mitigación natural es notificarle al
superusuario cada alta nueva para que pueda intervenir después, no antes.
