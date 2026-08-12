"""
Identidad y permisos: usuarios, qué empresas puede ver cada uno, y tokens de
un solo uso para invitación/recuperación de contraseña.

Vive en `data/sistema.db`, **separada** de `data/empresas/<nit>.db` -- excepción
deliberada a "todo por NIT" (regla 5 de CLAUDE.md): un usuario puede pertenecer
a varias empresas, así que no puede vivir dentro de la base de una sola. Esta
base nunca contiene datos de negocio (facturas, cuentas, etc.), solo identidad
y el mapa "qué NIT puede ver cada usuario" -- el aislamiento real de datos de
negocio lo sigue dando `data/empresas/<nit>.db` como siempre.

Este módulo es deliberadamente "tonto": guarda y lee filas tal cual, nunca
decide QUIÉN puede ver QUÉ más allá de lo que ya está en `usuario_empresas`
(ej. no sabe que "superusuario" ve todo -- esa regla vive en `src/auth.py`,
que sí conoce el negocio)."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROLES_VALIDOS = ("superusuario", "contador", "empresa")
TIPOS_TOKEN_VALIDOS = ("invitacion", "recuperacion")
VIGENCIA_TOKEN_HORAS = 48

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT,
    rol TEXT NOT NULL CHECK(rol IN ('superusuario','contador','empresa')),
    puede_crear_usuarios INTEGER NOT NULL DEFAULT 0,
    creado_por_usuario_id INTEGER REFERENCES usuarios(id),
    activo INTEGER NOT NULL DEFAULT 1,
    creado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usuario_empresas (
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    nit TEXT NOT NULL,
    PRIMARY KEY (usuario_id, nit)
);

CREATE TABLE IF NOT EXISTS tokens_acceso (
    token TEXT PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    tipo TEXT NOT NULL CHECK(tipo IN ('invitacion','recuperacion')),
    expira_en TEXT NOT NULL,
    usado INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS intentos_registro_empresa (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    creado_en TEXT NOT NULL
);
"""


def conectar(base_dir: Path = Path("data")) -> sqlite3.Connection:
    base_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(base_dir / "sistema.db")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_ESQUEMA)
    return conn


def _fila_a_usuario(fila) -> dict:
    return {
        "id": fila[0], "email": fila[1], "password_hash": fila[2], "rol": fila[3],
        "puede_crear_usuarios": bool(fila[4]), "creado_por_usuario_id": fila[5],
        "activo": bool(fila[6]), "creado_en": fila[7],
    }


_COLUMNAS_USUARIO = "id, email, password_hash, rol, puede_crear_usuarios, creado_por_usuario_id, activo, creado_en"


def crear_usuario(
    conn: sqlite3.Connection, email: str, rol: str,
    puede_crear_usuarios: bool = False, creado_por_usuario_id: int | None = None,
    password_hash: str | None = None,
) -> int:
    """Crea un usuario -- sin `password_hash` (el caso normal: se invita por
    correo y el usuario la fija él mismo con un token, ver `crear_token`).
    Solo el script de arranque (`crear_superusuario.py`) pasa `password_hash`
    directo, porque en ese momento no hay nadie que envíe la invitación."""
    if rol not in ROLES_VALIDOS:
        raise ValueError(f"Rol inválido: '{rol}'. Válidos: {', '.join(ROLES_VALIDOS)}.")
    email = email.strip().lower()
    if obtener_usuario_por_email(conn, email) is not None:
        raise ValueError(f"Ya existe un usuario con el correo '{email}'.")

    cur = conn.execute(
        """
        INSERT INTO usuarios (email, password_hash, rol, puede_crear_usuarios, creado_por_usuario_id, activo, creado_en)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """,
        (email, password_hash, rol, int(puede_crear_usuarios), creado_por_usuario_id,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def obtener_usuario_por_email(conn: sqlite3.Connection, email: str) -> dict | None:
    fila = conn.execute(
        f"SELECT {_COLUMNAS_USUARIO} FROM usuarios WHERE email = ?", (email.strip().lower(),),
    ).fetchone()
    return _fila_a_usuario(fila) if fila else None


def obtener_usuario_por_id(conn: sqlite3.Connection, usuario_id: int) -> dict | None:
    fila = conn.execute(
        f"SELECT {_COLUMNAS_USUARIO} FROM usuarios WHERE id = ?", (usuario_id,),
    ).fetchone()
    return _fila_a_usuario(fila) if fila else None


def listar_usuarios(conn: sqlite3.Connection) -> list[dict]:
    filas = conn.execute(f"SELECT {_COLUMNAS_USUARIO} FROM usuarios ORDER BY email").fetchall()
    return [_fila_a_usuario(f) for f in filas]


def listar_usuarios_creados_por(conn: sqlite3.Connection, creador_id: int) -> list[dict]:
    filas = conn.execute(
        f"SELECT {_COLUMNAS_USUARIO} FROM usuarios WHERE creado_por_usuario_id = ? ORDER BY email", (creador_id,),
    ).fetchall()
    return [_fila_a_usuario(f) for f in filas]


def reemplazar_empresas_de_usuario(conn: sqlite3.Connection, usuario_id: int, nits: list[str]) -> None:
    """Reemplaza el conjunto completo de empresas de este usuario (DELETE +
    INSERT) -- más simple de razonar desde la interfaz (un checklist de
    empresas) que agregar/quitar una por una."""
    conn.execute("DELETE FROM usuario_empresas WHERE usuario_id = ?", (usuario_id,))
    for nit in nits:
        conn.execute("INSERT INTO usuario_empresas (usuario_id, nit) VALUES (?, ?)", (usuario_id, nit))
    conn.commit()


def cambiar_activo(conn: sqlite3.Connection, usuario_id: int, activo: bool) -> None:
    conn.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (int(activo), usuario_id))
    conn.commit()


def cambiar_rol(conn: sqlite3.Connection, usuario_id: int, rol: str, puede_crear_usuarios: bool | None = None) -> None:
    """`puede_crear_usuarios=None` deja ese campo tal cual estaba -- lo
    decide `auth.cambiar_rol_usuario` (ej. se apaga solo al pasar a rol
    "empresa"). Este módulo no valida reglas de negocio, solo el valor del
    rol contra `ROLES_VALIDOS` (igual que `crear_usuario`)."""
    if rol not in ROLES_VALIDOS:
        raise ValueError(f"Rol inválido: '{rol}'. Válidos: {', '.join(ROLES_VALIDOS)}.")
    if puede_crear_usuarios is None:
        conn.execute("UPDATE usuarios SET rol = ? WHERE id = ?", (rol, usuario_id))
    else:
        conn.execute(
            "UPDATE usuarios SET rol = ?, puede_crear_usuarios = ? WHERE id = ?",
            (rol, int(puede_crear_usuarios), usuario_id),
        )
    conn.commit()


def contar_superusuarios_activos(conn: sqlite3.Connection) -> int:
    fila = conn.execute("SELECT COUNT(*) FROM usuarios WHERE rol = 'superusuario' AND activo = 1").fetchone()
    return fila[0]


def eliminar_usuario(conn: sqlite3.Connection, usuario_id: int) -> None:
    """Borra el usuario y sus propias filas dependientes (tokens, empresas
    asociadas). Si otro usuario lo tiene como `creado_por_usuario_id`, la
    restricción de llave foránea bloquea el borrado -- se revierte con
    `rollback` y se deja que el error suba (`auth.eliminar_usuario` lo
    convierte en un mensaje claro en vez de dejarlo como un
    `sqlite3.IntegrityError` crudo)."""
    try:
        conn.execute("DELETE FROM tokens_acceso WHERE usuario_id = ?", (usuario_id,))
        conn.execute("DELETE FROM usuario_empresas WHERE usuario_id = ?", (usuario_id,))
        conn.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise


def quitar_nit_de_todos_los_usuarios(conn: sqlite3.Connection, nit: str) -> None:
    """Limpieza de huérfanos cuando se elimina una empresa por completo --
    ver `orquestador.eliminar_empresa`. Sin esto, un usuario seguiría
    "viendo" un NIT que ya no existe en ningún lado."""
    conn.execute("DELETE FROM usuario_empresas WHERE nit = ?", (nit,))
    conn.commit()


def listar_nits_de_usuario(conn: sqlite3.Connection, usuario_id: int) -> list[str]:
    """Los NIT que este usuario tiene asociados en `usuario_empresas` --
    literal, sin ninguna regla de rol (un superusuario no tiene filas acá,
    su acceso a todo es una regla de negocio de `src/auth.py`, no de este
    módulo)."""
    filas = conn.execute(
        "SELECT nit FROM usuario_empresas WHERE usuario_id = ? ORDER BY nit", (usuario_id,),
    ).fetchall()
    return [fila[0] for fila in filas]


def asociar_empresa_a_usuario(conn: sqlite3.Connection, usuario_id: int, nit: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO usuario_empresas (usuario_id, nit) VALUES (?, ?)", (usuario_id, nit),
    )
    conn.commit()


def quitar_empresa_de_usuario(conn: sqlite3.Connection, usuario_id: int, nit: str) -> None:
    conn.execute("DELETE FROM usuario_empresas WHERE usuario_id = ? AND nit = ?", (usuario_id, nit))
    conn.commit()


def crear_token(conn: sqlite3.Connection, usuario_id: int, tipo: str, vigencia_horas: int = VIGENCIA_TOKEN_HORAS) -> str:
    if tipo not in TIPOS_TOKEN_VALIDOS:
        raise ValueError(f"Tipo de token inválido: '{tipo}'. Válidos: {', '.join(TIPOS_TOKEN_VALIDOS)}.")
    token = secrets.token_urlsafe(32)
    expira_en = (datetime.now(timezone.utc) + timedelta(hours=vigencia_horas)).isoformat()
    conn.execute(
        "INSERT INTO tokens_acceso (token, usuario_id, tipo, expira_en, usado) VALUES (?, ?, ?, ?, 0)",
        (token, usuario_id, tipo, expira_en),
    )
    conn.commit()
    return token


def obtener_token_valido(conn: sqlite3.Connection, token: str) -> dict | None:
    """`None` si el token no existe, ya se usó, o venció -- nunca distingue
    el motivo exacto en la respuesta (no le da pistas a quien intente
    adivinar tokens de otro usuario)."""
    fila = conn.execute(
        "SELECT token, usuario_id, tipo, expira_en, usado FROM tokens_acceso WHERE token = ?", (token,),
    ).fetchone()
    if fila is None:
        return None
    _, usuario_id, tipo, expira_en, usado = fila
    if usado:
        return None
    if datetime.now(timezone.utc) > datetime.fromisoformat(expira_en):
        return None
    return {"token": token, "usuario_id": usuario_id, "tipo": tipo, "expira_en": expira_en}


def marcar_token_usado(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("UPDATE tokens_acceso SET usado = 1 WHERE token = ?", (token,))
    conn.commit()


def fijar_password(conn: sqlite3.Connection, usuario_id: int, password_hash: str) -> None:
    conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (password_hash, usuario_id))
    conn.commit()


def registrar_intento_registro_empresa(conn: sqlite3.Connection, email: str) -> None:
    """Deja constancia de un intento de autorregistro de empresa (exitoso o
    no) para poder limitar cuántos puede hacer el mismo correo por hora --
    ver `contar_intentos_registro_empresa_recientes`. Es el único endpoint
    público que crea datos persistentes (archivos + filas) sin sesión, así
    que necesita su propio freno contra abuso/spam de correos."""
    conn.execute(
        "INSERT INTO intentos_registro_empresa (email, creado_en) VALUES (?, ?)",
        (email.strip().lower(), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def contar_intentos_registro_empresa_recientes(conn: sqlite3.Connection, email: str, horas: int = 1) -> int:
    desde = (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()
    fila = conn.execute(
        "SELECT COUNT(*) FROM intentos_registro_empresa WHERE email = ? AND creado_en >= ?",
        (email.strip().lower(), desde),
    ).fetchone()
    return fila[0]
