# Conexión Google (Drive + Gmail), por empresa o compartida

Reemplaza el modelo original de "una sola cuenta de Gmail global" (ver historial en
`importar-desde-drive.md`) por **conexiones de Google reutilizables**: una conexión es
el token de una cuenta de Google (permiso de solo lectura sobre Drive y Gmail) que se
puede **asociar** a una o varias empresas. Así se cubren dos casos con el mismo
mecanismo:

- El contador conecta **una** cuenta y la reutiliza en varias de sus empresas (lo que
  ya se hacía antes, ahora gestionable desde un menú en vez de un script).
- Una empresa conecta **su propia** cuenta de Google, distinta a la del contador.

## La conexión "heredada" sigue funcionando sin tocar nada

Las 5 empresas que ya usaban el modelo anterior (`config/google/client_secret.json` +
`token.json`, creado con `python src/autorizar_drive.py`) siguen funcionando
exactamente igual -- esa autorización se trata como la conexión especial `"legacy"`
("Conexión compartida (actual)" en el selector de "Configuración"). Nadie necesita
volver a dar consentimiento. Esa conexión **solo tiene permiso de Drive** (se creó
antes de que existiera el permiso de Gmail) -- por eso AXON bloquea activar la
importación desde Gmail mientras una empresa siga usándola; hay que conectarle una
cuenta nueva primero.

## Configuración inicial en Google Cloud Console (pasos manuales, una sola vez)

Sobre el mismo proyecto ya usado para Drive:

1. "APIs y servicios" → "Biblioteca" → habilitar también la **Gmail API** (la Drive
   API ya debería estar habilitada).
2. "Pantalla de consentimiento OAuth" → agregar el scope
   `https://www.googleapis.com/auth/gmail.readonly` a los scopes solicitados.
3. "Credenciales" → "Crear credenciales" → **ID de cliente de OAuth** → tipo de
   aplicación **"Aplicación web"** (NO "App de escritorio" -- ese tipo no admite
   registrar una URI de redirección propia, que es justo lo que necesita este flujo).
   Como URI de redirección autorizada, agregar:
   ```
   http://localhost:5000/oauth/google/callback
   ```
4. Descargar el JSON de ese cliente y guardarlo como
   `config/google/client_secret_web.json` (mismo directorio gitignored que
   `client_secret.json` y `token.json`; puede coexistir con el cliente de escritorio
   original, no hace falta borrarlo).
5. Mientras la pantalla de consentimiento siga en modo **Prueba** (Testing, para
   evitar el proceso de verificación pública de Google): cada cuenta de Google nueva
   que vaya a conectar una empresa debe agregarse a mano como **"Usuario de prueba"**
   en esa pantalla -- si no, Google responde "access_denied" al intentar conectar.
   Esto es una limitación real mientras la app no se someta a verificación; hay que
   tenerlo presente al escalar a más empresas/clientes.

## Conectar una cuenta nueva (por empresa, desde la interfaz)

1. Menú "Configuración" de la empresa → pestaña "Conexión Google".
2. Botón "Conectar una cuenta nueva" -- abre una pestaña con el consentimiento de
   Google (Drive + Gmail, un solo permiso).
3. Al aceptar, Google redirige a `http://localhost:5000/oauth/google/callback`, que
   guarda la conexión nueva y la asocia automáticamente a la empresa que la inició.
4. Para **reutilizar** una conexión ya creada en otra empresa (modo contador con
   varias empresas bajo la misma cuenta), elegirla del desplegable "Conexión de Google
   a usar" y hacer clic en "Usar esta conexión" -- no hace falta repetir el
   consentimiento.

## Carpeta de Drive (igual que antes)

Sin cambios respecto al modelo original: se pide el id de la carpeta compartida
(`drive.google.com/drive/folders/<ESTE ID>`) y se pega en el campo "Id de la carpeta
de Drive" de la misma pestaña. AXON recorre esa carpeta recursivamente sin exigir
ninguna convención de nombres ni subcarpetas -- ver el detalle en la sección
correspondiente de `importar-desde-drive.md`.

## Importar desde Gmail (nuevo)

Busca cualquier adjunto `.zip` en bandeja de entrada + spam (configurable) desde una
fecha de corte, sin filtrar por remitente ni asunto -- lo que no sea una factura DIAN
real lo descarta el pipeline de importación normal (igual que ya pasa hoy con
adjuntos sueltos en Drive).

- **Activar**: checkbox "Activar importación desde Gmail" en la pestaña "Conexión
  Google" -- requiere una conexión propia (no la legacy, ver arriba).
- **Buscar en spam**: activado por defecto: DIAN a veces cae en spam según el
  proveedor de correo del emisor.
- **Fecha de corte**: se define **una sola vez** -- una vez guardada, el campo pasa a
  solo lectura y cada sincronización siguiente solo trae correos nuevos desde la
  última vez (`ultima_sincronizacion`, que el propio sistema actualiza).
- Botón **"Importar desde Google"** en la bandeja de revisión: sincroniza Drive (si
  tiene carpeta configurada) y Gmail (si está activo) en una sola corrida.

## Detalles técnicos (para quien toque el código)

- `src/google_conexiones.py`: registro de conexiones (`config/google/conexiones/`),
  flujo OAuth (`google_auth_oauthlib.flow.Flow`, no `InstalledAppFlow` -- esa clase es
  para scripts de escritorio, acá Flask necesita un `redirect_uri` fijo), y
  `obtener_credenciales(conexion_id)` (con soporte transparente para `"legacy"`).
- `src/drive_client.py` y `src/gmail_client.py` reciben `creds` explícito -- no se
  autentican solos, así una empresa puede usar una conexión distinta a otra sin
  estado global oculto.
- `config/empresas/<nit>.json`: `conexion_drive.conexion_id` (qué conexión usa esa
  empresa, `""` = legacy) y `conexion_gmail` (activo, buscar_en_spam, desde_fecha,
  ultima_sincronizacion).
