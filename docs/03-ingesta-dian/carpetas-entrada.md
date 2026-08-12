# Dónde colocar los ZIP y listados que entrega cada empresa

## Estructura

```
data/entrada-dian/<slug-empresa>/<yyyy>/<mm>/
```

Ejemplo real: el ZIP de julio 2026 de Hielo Super-Cool va en
`data/entrada-dian/hielo-super-cool/2026/07/`. El listado de la DIAN del mismo periodo
(si se descarga) va en esa misma carpeta.

## Por qué estos nombres

- **`slug`, no NIT.** El `slug` (ej. `hielo-super-cool`) ya es el identificador que se
  usa para invocar comandos (`--empresa <slug>`) y el que aparece en
  `config/empresas/registro.json`, que es la única fuente de verdad para resolver
  nombre → NIT. No hace falta repetir el NIT en la ruta — sería más largo de escribir
  y no aporta nada que el registro no resuelva ya. (Distinto es el caso de
  `docs/02-reglas-negocio/politicas-empresa/`, donde el archivo lleva `NIT-slug`
  porque conviene ordenar por NIT en esa carpeta; aquí no aplica la misma razón.)
- **`yyyy/mm` en vez de solo `mm`.** Así el cambio de año (`2026` → `2027`) no rompe
  nada ni obliga a mover carpetas viejas — cada año es simplemente una carpeta nueva.
  Con cero a la izquierda en el mes (`07`, no `7`) para que ordene bien alfabéticamente.
- **Vive bajo `data/`, no `config/` ni `docs/`.** Es dato crudo de entrada (facturas
  con información fiscal de terceros), igual que las bases SQLite
  (`data/empresas/<nit>.db`). `data/` ya está en `.gitignore` — nunca se sube a git.

## Al agregar una empresa nueva

Crear la carpeta `data/entrada-dian/<slug-nuevo>/<yyyy>/<mm>/` en el mismo momento en
que se agrega la empresa a `config/empresas/registro.json` (ver
`docs/07-operacion-claude-code/comandos.md`).
