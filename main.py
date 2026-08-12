"""
CLI (ver tabla de comandos en CLAUDE.md). Capa delgada sobre src/orquestador.py
-- toda la lógica real vive ahí, compartida con backend/app.py (API web).

Hoy solo implementa `importar`. Los demás comandos de la tabla
(validar-completitud, clasificar, enviar-siigo) todavía no existen -- si se
invocan, este script lo dice explícitamente en vez de fingir que corrió algo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import orquestador  # noqa: E402


def cmd_importar(args: argparse.Namespace) -> None:
    try:
        resumen = orquestador.ejecutar_importar(args.empresa, args.carpeta)
    except (orquestador.EmpresaNoEncontrada, FileNotFoundError) as e:
        raise SystemExit(str(e))

    print(f"Empresa: {resumen['empresa']} ({resumen['nit']})")
    print(f"Carpeta: {resumen['carpeta']}")
    print(f"  Nuevas facturas importadas: {resumen['nuevas']}")
    print(f"  Ya existían en la base (se omitieron): {resumen['ya_existentes']}")
    print(f"  Duplicadas dentro de esta carpeta (zip_handler): {resumen['duplicados']}")
    print(f"  No son facturas de compra (acuses, notas, etc.): {resumen['no_facturas']}")
    print(f"  Con error (sin CUFE / XML mal formado): {resumen['con_error']}")
    if resumen["nuevas"]:
        print("  Se detiene aquí -- falta clasificar/enviar. Nada se envió a Siigo.")


def main() -> None:
    parser = argparse.ArgumentParser(description="AXON -- agente contable DIAN -> Siigo")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_importar = sub.add_parser("importar", help="Descomprime, parsea y guarda facturas de una carpeta.")
    p_importar.add_argument("--empresa", required=True, help="slug de config/empresas/registro.json")
    p_importar.add_argument("--carpeta", required=True, help="ruta relativa dentro de data/entrada-dian/<slug>/, ej. 2026/07")
    p_importar.set_defaults(func=cmd_importar)

    for nombre in ("validar-completitud", "clasificar", "enviar-siigo"):
        p = sub.add_parser(nombre, help="Todavía no implementado.")
        p.set_defaults(func=lambda args, n=nombre: (_ for _ in ()).throw(
            SystemExit(f"El comando '{n}' todavía no está implementado.")
        ))

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
