# Plan de despliegue en la web + actualizaciones futuras

Objetivo: pasar AXON de "corre en mi PC" a un servidor accesible por internet, con
HTTPS, y con una forma repetible de subir cada actualización sin que el usuario tenga
que reconstruir el proceso a mano cada vez. Este documento es el plan; la ejecución
(aprovisionar el VPS, generar credenciales reales) se hace en sesiones aparte, con
confirmación explícita antes de cada paso que cueste dinero o toque algo irreversible.

## 0. Punto de partida real (lo que hoy falta antes de poder desplegar)

Un repaso honesto del estado actual, porque cambia el plan:

1. **No hay repositorio git todavía** (`git status` → "not a git repository"). Es el
   primer bloqueador real: sin git no hay historial, no hay forma segura de sincronizar
   código local → servidor, y no se puede montar CI/CD. Se resuelve en minutos, pero es
   el paso 1, no un detalle.
2. **El servidor de desarrollo de Flask no es apto para producción**
   (`app.run(debug=True)` en `backend/app.py`). Necesita un servidor WSGI real
   (`gunicorn`) detrás de un proxy (`nginx`) que dé TLS, sirva los estáticos de
   `frontend/` y absorba picos de conexión.
3. **Las conexiones de Google (Drive/Gmail) están en modo "Prueba"** en Google Cloud
   Console (ver `docs/08-decisiones-pendientes/preguntas-abiertas.md`) — cualquier
   cuenta de Google nueva que una empresa quiera conectar hay que agregarla a mano como
   "Usuario de prueba" antes de que el OAuth funcione. Esto **no bloquea** publicar la
   app (las 7 empresas actuales ya funcionan así), pero si el objetivo es que empresas
   nuevas se autorregistren y conecten su propio Drive sin que tú intervengas, hay que
   someter la app a verificación pública de Google en algún momento — es un trámite de
   Google (formulario + a veces una revisión de una semana o más), no algo que se
   resuelva con código.
4. **Los secretos nunca deben viajar por git** — ya está bien resuelto en el código
   (`.gitignore` ya excluye `config/empresas/*.json`, `config/google/`, `data/`,
   `*.db`), pero hay que decidir CÓMO llegan esos archivos reales al servidor la
   primera vez (nunca por commit). Ver sección 3.
5. **SQLite por empresa** (`data/empresas/<nit>.db`) es una decisión ya tomada y
   documentada (`docs/06-multiempresa-saas/aislamiento-datos.md`) — aislamiento físico
   real entre empresas, se mantiene tal cual para el lanzamiento. Ver sección 6 para
   cuándo replantearlo.

## 1. Arquitectura de despliegue recomendada

```
Internet
   │  HTTPS (443)
   ▼
┌─────────────────────────────────────────┐
│  nginx (proxy inverso + TLS Let's Encrypt) │
└─────────────────────────────────────────┘
   │  HTTP interno (127.0.0.1:8000)
   ▼
┌─────────────────────────────────────────┐
│  gunicorn (varios workers) → backend/app.py │
│  systemd la mantiene viva y la reinicia si  │
│  se cae o si el servidor reinicia            │
└─────────────────────────────────────────┘
   │
   ▼
data/  (SQLite por empresa + XML/ZIP originales)
config/  (credenciales por empresa, fuera de git)
```

Por qué este stack y no otro:

- **gunicorn + nginx** es el estándar para Flask en producción — nginx absorbe TLS,
  compresión y archivos estáticos (más rápido que Flask sirviéndolos), gunicorn corre
  varios workers para no bloquear si una petición tarda (ej. una importación grande).
- **systemd** (ya viene en cualquier VPS Linux moderno) reinicia el proceso solo si se
  cae o si el servidor se reinicia — sin esto, un reboot del servidor deja la app caída
  hasta que alguien entre a mano a levantarla.
- **Sin Docker por ahora**: con una sola app Flask + SQLite, Docker agrega una capa de
  complejidad (imágenes, volúmenes para persistir `data/`) sin un beneficio claro a
  este tamaño. Vale la pena reconsiderarlo si más adelante se necesita reproducir el
  entorno en varios servidores a la vez — no es el caso hoy.

## 2. Recomendación de VPS

**Hetzner Cloud, plan CX22 (2 vCPU / 4 GB RAM / 40 GB SSD) — ~€3.79/mes (~USD 4)**

Por qué, en concreto para esta app:

- **La carga real es baja.** `requirements.txt` es liviano (Flask, openpyxl, cliente
  de Google) — no hay nada que procese imágenes, entrene modelos, ni mantenga miles de
  conexiones simultáneas. Es una herramienta de back-office para un puñado de
  contadores/empresas, no una app de consumo masivo. 2 vCPU / 4 GB sobra con margen
  amplio incluso con las 7 empresas actuales multiplicadas varias veces.
- **Mejor relación costo/beneficio del mercado en esta franja.** A specs iguales,
  Hetzner cuesta bastante menos que DigitalOcean, Vultr o Linode (que rondan
  USD 12–24/mes por un CX22 equivalente). Con lo que ahorras al año casi pagas dos
  meses adicionales de servidor.
- **Escalar es literalmente un botón.** Hetzner permite redimensionar el servidor
  (subir a 4 vCPU/8 GB, luego 8/16, etc.) desde el panel, con un reinicio de un par de
  minutos — no hay que reconstruir nada. Es exactamente el tipo de "fácil de escalar"
  que pediste: subes el plan cuando el uso lo justifique, no antes.
- **Snapshots y backups administrados** por un costo adicional pequeño (~20% del precio
  del servidor) — útil como respaldo secundario, aunque el respaldo principal debe ser
  independiente del proveedor (ver sección 5).
- **Región**: Hetzner no tiene datacenter en Sudamérica (sí en EE.UU. — Ashburn,
  Virginia). Para una herramienta de back-office (no tiempo real, se usa para importar
  y causar facturas, no para chatear) una latencia de ~120-150ms desde Colombia es
  completamente imperceptible en el uso normal. Si esto llegara a importar mucho, la
  alternativa con presencia en São Paulo (más cerca de Colombia, ~40-60ms) es
  **DigitalOcean** o **Vultr**, a cambio de un costo mensual mayor.

**Alternativa si prefieres una interfaz más guiada / más tutoriales en español:
DigitalOcean, Droplet Basic 2 GB ($12/mes)** — específicamente si quieres tener
documentación abundante para resolver problemas por tu cuenta sin depender de mí, o si
la latencia desde Colombia termina importando (región São Paulo). Mismo concepto de
"resize" para escalar, solo que a un costo base más alto.

**No recomendado para este caso:** planes "serverless"/PaaS tipo Render, Railway,
Fly.io — son más caros a este tamaño de tráfico constante, y SQLite con archivos en
disco no encaja bien con su modelo de sistema de archivos efímero (perderías
`data/` en cada redeploy salvo que pagues por un volumen persistente aparte, lo que
anula la ventaja de simplicidad que ofrecen).

## 3. Primer despliegue (paso a paso)

1. **`git init` + primer commit + repo privado en GitHub.** Necesario para tener
   historial, para el flujo de actualizaciones (sección 4), y como respaldo del código
   fuera del servidor. Confirmar contigo antes de crear el repo remoto (privado, nunca
   público — el código no tiene secretos, pero sí expone lógica de negocio de varios
   clientes reales).
2. **Aprovisionar el VPS** (Hetzner CX22, Ubuntu 24.04 LTS) — usuario no-root con sudo,
   firewall (`ufw`) abriendo solo 22/80/443, llave SSH (nunca contraseña).
3. **Dominio**: comprar o usar uno que ya tengas, apuntar un registro A al VPS.
   Necesario para HTTPS real y porque **Google OAuth exige una URL de redirect HTTPS
   fija** para las conexiones de Drive/Gmail — sin dominio estable esto no funciona en
   producción. *(Pregunta abierta para ti: ¿ya tienes un dominio pensado, o lo
   compramos como parte de este plan?)*
4. **Clonar el repo en el servidor**, crear entorno virtual, `pip install -r
   requirements.txt`, más `gunicorn`.
5. **Subir los secretos reales manualmente** (nunca por git): `scp` directo y con
   permisos restringidos (`chmod 600`) para `config/empresas/*.json`,
   `config/google/client_secret_web.json`, `config/correo/`, y crear `data/` vacío (o
   restaurar un backup si ya hay empresas operando localmente que se van a migrar).
6. **Archivo de servicio systemd** (`axon.service`) apuntando a gunicorn +
   `backend/app.py`, con `Restart=always`.
7. **nginx** como proxy inverso hacia `127.0.0.1:8000`, + `certbot` para el
   certificado TLS de Let's Encrypt (renovación automática, no requiere intervención
   manual después).
8. **Actualizar el redirect URI de Google Cloud Console** al dominio real
   (`https://tu-dominio.com/api/conexiones-google/oauth/callback` o el que corresponda)
   — hoy seguramente apunta a `localhost`.
9. **Prueba de humo completa**: login, importar una factura de prueba, exportar a
   Siigo/Contai en modo de prueba, confirmar que los correos de invitación llegan.

## 4. Flujo para actualizaciones futuras

Dado que este proyecto se sigue construyendo activamente (contigo y conmigo en estas
sesiones), el flujo de actualización debe ser tan simple que no se salte por pereza,
pero seguro:

**Recomendado: GitHub Actions con despliegue por SSH, disparado al hacer push a
`main`.**

```
Cambio de código (esta sesión, o la que sea)
        │
        ▼
git push a main (o merge de un PR)
        │
        ▼
GitHub Actions: corre `pytest tests/` automáticamente
        │  (si falla, se detiene acá — nunca se despliega código roto)
        ▼
SSH al VPS: git pull + pip install -r requirements.txt + systemctl restart axon
        │
        ▼
Chequeo automático simple (curl al endpoint de salud) confirma que levantó bien
```

Por qué así y no manual:

- **Corre la misma suite de 469 pruebas antes de desplegar** — la misma que ya
  corremos después de cada cambio en estas sesiones. Si algo rompe Siigo o Contai, el
  despliegue automáticamente no ocurre.
- Un solo comando (`git push`) reemplaza el ritual manual de "conectarme por SSH,
  hacer pull, reiniciar el servicio, cruzar los dedos" — que es exactamente el tipo de
  paso que se olvida o se hace mal bajo apuro.
- No agrega infraestructura nueva que mantener (GitHub Actions es gratis para repos
  privados hasta un volumen de minutos que este proyecto no se acerca a gastar).

**Regla dura para cambios de esquema de base de datos**: cualquier migración que toque
`state_store.py`/`auth_store.py` debe seguir siendo aditiva y retrocompatible (columnas
nuevas con default, nunca un `DROP`/`RENAME` directo en producción) — el mismo criterio
que ya se sigue en este proyecto para las migraciones locales, así un despliegue nunca
deja una base a medio migrar.

## 5. Backups (no negociable, es software contable)

**Implementado 2026-08-12.** Backup diario automático de `data/` completo (todas las
`.db` por empresa + `sistema.db` + los XML/ZIP/PDF originales en `entrada-dian/`) y de
`config/` (credenciales y configuración por empresa), hacia **Backblaze B2**
(bucket privado `axonweb-lat-backups`, región EU Central — misma región que el VPS,
Falkenstein — cifrado del lado del servidor activado). Cae dentro del plan gratis de
B2 (10GB) por años al ritmo de crecimiento actual (~78MB en agosto 2026).

- `src/backup_a_b2.py`: arma el paquete (las bases SQLite se copian con la API de
  backup de `sqlite3`, nunca una copia de archivo directa, para no capturar una
  escritura a medias mientras gunicorn sigue atendiendo peticiones), lo sube con
  `rclone` (remoto `b2`, configurado en `~/.config/rclone/rclone.conf` del usuario
  `axon` en el servidor — nunca en este repo, ver regla 4 de `CLAUDE.md`), y poda lo
  vencido.
- Corre por `axon-backup.timer` (systemd, mismo patrón que `axon.service`) todas las
  noches a las 08:00 UTC (~3am hora Colombia). `Persistent=true`: si el VPS estaba
  apagado a esa hora, corre apenas vuelve a encender.
- Retención: 30 diarios (`diario/`) + 12 mensuales, el del día 1 de cada mes
  (`mensual/`) — podada automáticamente en cada corrida, no hace falta cron aparte.
- Verificado de punta a punta el 2026-08-12: backup real subido, descargado de
  vuelta, `PRAGMA integrity_check` en las 7 bases dio `ok`, y el conteo de archivos
  de `entrada-dian` restaurados coincidió exacto con el original (1014 = 1014).
- El snapshot administrado del proveedor (mencionado en la sección 2) es un respaldo
  *adicional*, no el principal — un snapshot vive en la misma cuenta/proveedor que el
  servidor, así que no protege contra un problema de facturación o de cuenta.

## 6. Cuándo replantear la arquitectura (disparadores concretos de escala)

Cierra la pregunta abierta en `docs/08-decisiones-pendientes/preguntas-abiertas.md`
("¿por número de empresas, por volumen de facturas, o por fecha objetivo?"):

| Señal | Qué hacer |
|---|---|
| El VPS usa consistentemente >70% de CPU/RAM en horas pico | Redimensionar el mismo VPS (un clic, sin tocar código) — el primer escalón, no una re-arquitectura |
| >50 empresas activas simultáneas, o alguna empresa con >50.000 facturas/año | Empezar a evaluar migrar a Postgres (esquema por empresa, ver `aislamiento-datos.md` opción A) — ahí SQLite empieza a mostrar límites de escritura concurrente |
| Se necesita alta disponibilidad real (no aceptar ni minutos de caída) | Recién ahí tiene sentido un balanceador + más de un servidor de aplicación — con Postgres ya migrado, porque SQLite en disco local no se reparte entre dos servidores |
| Se somete la app a verificación pública de Google | Solo entonces el autorregistro de empresas con conexión de Google propia queda 100% self-service, sin intervención manual tuya por cada cuenta nueva |

No hay que adelantarse a ninguno de estos — cada uno es una señal real, no una fecha en
el calendario.

## 7. Costo total estimado (lanzamiento)

| Ítem | Costo aproximado |
|---|---|
| VPS Hetzner CX22 | ~USD 4/mes |
| Dominio (.com genérico) | ~USD 10-15/año |
| Backups a Object Storage (Backblaze B2, primeros GB) | ~USD 1-3/mes |
| Certificado TLS (Let's Encrypt) | Gratis |
| GitHub Actions (repo privado) | Gratis a este volumen |
| **Total mensual aproximado** | **~USD 5-8/mes** |

## Próximos pasos para ejecutar esto

1. Confirmar: ¿inicializamos el repo git ahora y lo subimos a un GitHub privado tuyo?
2. Confirmar: ¿ya tienes un dominio, o lo compramos como parte de este plan?
3. Con eso resuelto, aprovisiono el VPS y sigo la lista de la sección 3 paso a paso,
   confirmando contigo antes de cualquier acción que cueste dinero real (creación del
   servidor, compra del dominio).
