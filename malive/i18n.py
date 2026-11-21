"""Idioma de los textos de MaLiVe: español si el sistema está en español,
inglés en cualquier otro caso (la misma regla que el asistente de instalación).

Uso: T("Texto en español", "English text"). Los registros (malive.log) no se
traducen: son para diagnóstico.
"""

from __future__ import annotations

import os

from . import session

_LANG: str | None = None
_VARS = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")


def _from_env(env) -> str | None:
    for var in _VARS:
        value = (env.get(var) or "").strip()
        if var == "LANGUAGE":
            value = value.split(":", 1)[0]
        if value and value not in ("C", "POSIX") and not value.startswith("C."):
            return "es" if value.lower().startswith("es") else "en"
    return None


def lang() -> str:
    global _LANG
    if _LANG is None:
        forced = os.environ.get("MALIVE_LANG", "").lower()
        if forced in ("es", "en"):
            _LANG = forced
        else:
            # Desde el menú o el temporizador puede faltar LANG: se mira el de Plasma/GNOME
            _LANG = _from_env(os.environ) or _from_env(session.desktop_shell_env() or {}) or "en"
    return _LANG


def reset() -> None:
    """Para los tests: volver a detectar el idioma."""
    global _LANG
    _LANG = None


def T(es: str, en: str) -> str:
    return es if lang() == "es" else en
