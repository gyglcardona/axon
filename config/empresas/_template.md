# Conexión — <NOMBRE EMPRESA>

> Datos generales y de conexión de esta empresa. **Nunca escribas aquí `usuario` ni
> `access_key` de Siigo** — esos viven únicamente en `config/empresas/<nit>.json`,
> que está en `.gitignore`. Este archivo es para todo lo demás: quién es la empresa,
> cómo se conecta a Siigo a nivel de referencia, y datos generales que un contador o
> Claude necesitan para operarla sin preguntar cada vez.

## Datos generales

- **Razón social:**
- **NIT:**
- **Slug:** (debe coincidir con el `slug` en `config/empresas/registro.json`)
- **Contacto contable:** (nombre, correo, teléfono)
- **Régimen / responsabilidades tributarias relevantes:**

## Conexión a Siigo

- **Partner-Id:** (no es secreto, identifica la integración; ver
  `docs/04-integracion-siigo/autenticacion-y-endpoints.md`)
- **Usuario y access_key:** → `config/empresas/<nit>.json` (nunca en este archivo)
- **Ambiente:** producción / pruebas
- **Fecha de última rotación de `access_key`:** (para saber cuándo pedir renovarla)

## Políticas contables activas

Qué políticas de `config/empresas/<nit>.json` → `politicas` están activas, con link
al `.md` que las explica en `docs/02-reglas-negocio/politicas-empresa/`.

- [ ] IVA no discriminado — ver `docs/02-reglas-negocio/politicas-empresa/<archivo>.md`

## Notas operativas

Cualquier cosa rara de esta empresa que no está en las reglas de código: horarios de
envío a Siigo, quién aprueba antes de causar, particularidades del plan de cuentas,
etc.
