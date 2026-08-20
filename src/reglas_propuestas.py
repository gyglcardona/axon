"""
Reglas y dudas contables que un superusuario o contador propone por
empresa, para que alguien (hoy: Claude, en una sesión de Claude Code, nunca
de forma automática) las revise más tarde y decida si hace falta un cambio
en el motor de reglas. No reemplaza `config/politicas-empresa/` ni
`config/proveedores/` -- esos son las reglas ya confirmadas que el motor
ejecuta; esto es la bandeja de entrada de reglas *propuestas*, antes de
que alguien las valide.

Persisten en `data/reglas-propuestas/<nit>.json`, un archivo por empresa en
la misma carpeta (gitignored) donde ya vive el resto del estado operativo
(ver `data/empresas/<nit>.db`) -- no es config versionada a mano, es
contenido que un usuario escribe desde la interfaz.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

ESTADOS_VALIDOS = ("pendiente", "respondida", "aplicada", "no_viable")


def _ruta(nit: str, base_dir: Path) -> Path:
    return base_dir / f"{nit}.json"


def _leer(nit: str, base_dir: Path) -> list[dict]:
    ruta = _ruta(nit, base_dir)
    if not ruta.exists():
        return []
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)["reglas"]


def _escribir(nit: str, reglas: list[dict], base_dir: Path) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    with open(_ruta(nit, base_dir), "w", encoding="utf-8") as f:
        json.dump({"reglas": reglas}, f, ensure_ascii=False, indent=2)


def listar(nit: str, base_dir: Path = Path("data/reglas-propuestas")) -> list[dict]:
    return _leer(nit, base_dir)


def crear(nit: str, texto: str, creado_por: str, base_dir: Path = Path("data/reglas-propuestas")) -> dict:
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("La regla no puede estar vacía.")
    if len(texto) > 4000:
        raise ValueError("La regla es demasiado larga (máximo 4000 caracteres).")

    reglas = _leer(nit, base_dir)
    nueva = {
        "id": max((r["id"] for r in reglas), default=0) + 1,
        "texto": texto,
        "estado": "pendiente",
        "creado_por": creado_por,
        "creado_en": datetime.datetime.now().isoformat(timespec="seconds"),
        "respuesta": None,
        "respondida_por": None,
        "respondida_en": None,
        "aplicada_en": None,
    }
    reglas.append(nueva)
    _escribir(nit, reglas, base_dir)
    return nueva


def cambiar_estado(
    nit: str, regla_id: int, estado: str, respuesta: str | None, actor_email: str,
    base_dir: Path = Path("data/reglas-propuestas"),
) -> dict:
    """Usado cuando alguien (hoy: Claude, guiado por quien la creó) ya
    revisó la regla -- deja la respuesta visible para el usuario que la
    propuso y, si `estado` es "aplicada", registra cuándo. `respuesta` puede
    venir vacía si solo se está corrigiendo el estado (ej. reabrir una regla
    marcada "no_viable" por error)."""
    if estado not in ESTADOS_VALIDOS:
        raise ValueError(f"Estado inválido: '{estado}'. Debe ser uno de {', '.join(ESTADOS_VALIDOS)}.")

    reglas = _leer(nit, base_dir)
    for regla in reglas:
        if regla["id"] == regla_id:
            ahora = datetime.datetime.now().isoformat(timespec="seconds")
            regla["estado"] = estado
            if respuesta is not None:
                regla["respuesta"] = respuesta
                regla["respondida_por"] = actor_email
                regla["respondida_en"] = ahora
            if estado == "aplicada":
                regla["aplicada_en"] = ahora
            _escribir(nit, reglas, base_dir)
            return regla
    raise ValueError(f"No existe la regla {regla_id} para esta empresa.")


def eliminar(nit: str, regla_id: int, base_dir: Path = Path("data/reglas-propuestas")) -> None:
    """Solo mientras sigue "pendiente" -- una vez alguien la respondió o
    aplicó, borrarla perdería el rastro de qué se revisó; la corrección de
    una regla ya tocada se hace proponiendo una regla nueva, no borrando la
    vieja (ver frontend 'Reglas por empresa')."""
    reglas = _leer(nit, base_dir)
    for regla in reglas:
        if regla["id"] == regla_id:
            if regla["estado"] != "pendiente":
                raise ValueError(
                    "Solo se puede borrar una regla mientras sigue \"pendiente\" -- "
                    "esta ya fue revisada, propone una regla nueva si hace falta corregirla."
                )
            reglas.remove(regla)
            _escribir(nit, reglas, base_dir)
            return
    raise ValueError(f"No existe la regla {regla_id} para esta empresa.")
