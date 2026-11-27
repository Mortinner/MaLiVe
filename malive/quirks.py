"""Ajustes conocidos para programas concretos, aplicados al lanzarlos.

Cada programa de Windows que se cuelga o falla bajo Proton por algo con
arreglo conocido se apunta aquí, para que funcione desde el primer doble clic
sin que el usuario tenga que investigar. Los ajustes propios de cada app
(`malive dll`, `malive env`) mandan sobre estos.
"""

from __future__ import annotations

# nombre del .exe (minúsculas) -> {"dll": {dll: modo}, "env": {VAR: valor}}
QUIRKS: dict[str, dict[str, dict[str, str]]] = {
    # Format Factory 5.x (Qt5): con la SAPI de Wine se queda en bucle al
    # arrancar, sin ventana. Sin ella abre y convierte (probado 2026-11-27).
    "formatfactory.exe": {"dll": {"sapi": "d"}},
}


def for_exe(exe_name: str) -> dict[str, dict[str, str]]:
    return QUIRKS.get(exe_name.lower(), {})


def overrides_string(dlls: dict[str, str]) -> str:
    """{'sapi': 'd', 'd3d9': 'n,b'} -> 'sapi=d;d3d9=n,b' (formato de WINEDLLOVERRIDES)."""
    return ";".join(f"{k}={v}" for k, v in dlls.items())
