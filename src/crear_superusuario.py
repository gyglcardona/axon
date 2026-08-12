"""
Paso manual de arranque -- se corre UNA sola vez para crear el primer
superusuario del sistema. De ahí en adelante, la gestión de usuarios
(invitar contadores, auxiliares, empresas) se hace desde la interfaz de
AXON -- este script es el único lugar donde se asigna una contraseña
directamente, porque en este momento no existe todavía nadie que pueda
enviar una invitación por correo (ver src/auth.py, src/correo.py).

Uso: python src/crear_superusuario.py correo@ejemplo.com
"""

from __future__ import annotations

import getpass
import sys

import auth_store
from werkzeug.security import generate_password_hash


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python src/crear_superusuario.py correo@ejemplo.com")

    email = sys.argv[1].strip().lower()
    conn = auth_store.conectar()
    try:
        if auth_store.obtener_usuario_por_email(conn, email) is not None:
            raise SystemExit(f"Ya existe un usuario con el correo '{email}'.")

        password = getpass.getpass("Contraseña para el superusuario: ")
        confirmacion = getpass.getpass("Confirma la contraseña: ")
        if password != confirmacion:
            raise SystemExit("Las contraseñas no coinciden.")
        if len(password) < 8:
            raise SystemExit("La contraseña debe tener al menos 8 caracteres.")

        usuario_id = auth_store.crear_usuario(
            conn, email=email, rol="superusuario", puede_crear_usuarios=True,
            password_hash=generate_password_hash(password),
        )
        print(f"Superusuario creado: {email} (id={usuario_id}). Ya puedes iniciar sesión en AXON.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
