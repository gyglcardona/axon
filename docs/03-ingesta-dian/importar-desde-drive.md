# Importar desde Google Drive

> **El modelo de autorización descrito aquí abajo (una sola cuenta de Gmail global,
> `python src/autorizar_drive.py`) es el modelo original y sigue funcionando tal cual
> para las empresas que ya lo usan** -- ahora se llama la conexión **"legacy"**. Para
> conectar una cuenta de Google nueva por empresa (o reutilizar una entre varias, modo
> contador) desde la interfaz, sin el script de escritorio, ver
> **`conexion-google.md`** -- ese es el documento vigente para configuración nueva.
> Esta página se conserva por el detalle de "estructura de carpetas libre" más abajo,
> que sigue aplicando igual.

Alternativa a copiar los ZIP a mano en `data/entrada-dian/<slug>/` (ver
`carpetas-entrada.md`): cada empresa cliente comparte su propia carpeta de
Google Drive con la cuenta de Gmail del usuario, y AXON la sincroniza y la
importa con un clic ("Importar desde Google" en la bandeja de revisión).

## La autorización original era UNA sola, no por empresa (ver conexion-google.md para el modelo actual)

Una sola cuenta de Gmail recibe las carpetas compartidas de todas las
empresas -- por eso la autorización contra Google (OAuth) se hacía **una
vez**, no se repetía por cada empresa. Lo único que sí es específico de cada
empresa es el **id de su carpeta** de Drive, que se configura en la pestaña
"Conexión Google" del menú "Configuración" (mismo lugar que "Conexión Siigo").

## Configuración inicial de la conexión legacy (ya hecha, referencia histórica)

1. Entrar a https://console.cloud.google.com/ con la cuenta de Gmail donde
   se reciben las carpetas compartidas, y crear un proyecto nuevo (ej. "AXON
   Drive").
2. "APIs y servicios" → "Biblioteca" → habilitar **Google Drive API**.
3. "Pantalla de consentimiento OAuth" → tipo **Externo** → agregar el propio
   correo como **usuario de prueba** (evita el proceso de verificación
   pública de Google, innecesario para uso personal).
4. "Credenciales" → "Crear credenciales" → **ID de cliente de OAuth** → tipo
   de aplicación **"App de escritorio"**.
5. Descargar el JSON de esas credenciales y guardarlo como
   `config/google/client_secret.json` (la carpeta `config/google/` está en
   `.gitignore`, igual que `config/empresas/*.json`).
6. Correr una vez, desde la raíz del proyecto:
   ```bash
   python src/autorizar_drive.py
   ```
   Abre el navegador, se inicia sesión con esa cuenta de Gmail y se acepta el
   permiso de **solo lectura** sobre Drive. Guarda el token de refresco en
   `config/google/token.json` -- de ahí en adelante se renueva solo, no hay
   que repetir este paso salvo que el token se revoque manualmente desde la
   cuenta de Google.

## Por empresa

1. Pedirle a la empresa que comparta su carpeta de Drive con la cuenta de
   Gmail conectada (permiso de **Lector** basta).
2. Copiar el id de esa carpeta desde la URL de Drive:
   `drive.google.com/drive/folders/<ESTE ID>`.
3. En AXON: pestaña "Conexión Google" de esa empresa → pegar el id →
   Guardar.
4. Botón "Importar desde Google" en la bandeja de revisión.

## La estructura interna de cada carpeta es libre

No se le exige a ninguna empresa ningún convenio de nombres ni de
subcarpetas. AXON recorre la carpeta compartida recursivamente (igual que ya
hace en disco local, ver `carpetas-entrada.md`) y trae cualquier `.zip`/
`.xml` que encuentre a cualquier profundidad -- puede venir organizado por
año/mes (`2026/07/...`), por cualquier otra convención, o simplemente
suelto. La estructura de subcarpetas que tenga en Drive se refleja igual en
`data/entrada-dian/<slug>/` al descargarse.

Cada clic en "Importar desde Drive" sincroniza **todo lo nuevo** -- no hace
falta elegir un mes. Un archivo que ya se descargó antes no se vuelve a
traer (se compara por ruta+nombre contra lo que ya hay en disco); la
deduplicación real de facturas ya procesadas la hace el CUFE, igual que en
cualquier importación local, así que repetir la sincronización es siempre
seguro.
