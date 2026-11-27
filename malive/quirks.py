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


# id de la app en MaLiVe -> ajustes para todo lo que se ejecute en su entorno.
# Hace falta porque muchos instaladores abren el programa al terminar
# («Ejecutar X»): ese proceso hereda el entorno del instalador, no el de MaLiVe.
APP_QUIRKS: dict[str, dict[str, dict[str, str]]] = {
    "format-factory": {"dll": {"sapi": "d"}},
}


def for_exe(exe_name: str) -> dict[str, dict[str, str]]:
    return QUIRKS.get(exe_name.lower(), {})


def for_app(app_id: str) -> dict[str, dict[str, str]]:
    return APP_QUIRKS.get(app_id, {})


def merged(app_id: str, exe_name: str) -> dict[str, dict[str, str]]:
    a, e = for_app(app_id), for_exe(exe_name)
    return {"dll": {**a.get("dll", {}), **e.get("dll", {})}, "env": {**a.get("env", {}), **e.get("env", {})}}


def overrides_string(dlls: dict[str, str]) -> str:
    """{'sapi': 'd', 'd3d9': 'n,b'} -> 'sapi=d;d3d9=n,b' (formato de WINEDLLOVERRIDES)."""
    return ";".join(f"{k}={v}" for k, v in dlls.items())
