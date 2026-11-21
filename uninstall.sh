#!/usr/bin/env bash
# Desinstala MaLiVe.
#   ./uninstall.sh          quita los comandos y las asociaciones; conserva apps y prefijos
#   ./uninstall.sh --purge  borra TAMBIÉN todas las apps instaladas, prefijos, GE-Proton y configuración
set -euo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}"
BIN="$HOME/.local/bin"

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

if [ -x "$BIN/malive" ]; then
  "$BIN/malive" unintegrate || true
fi

if ((PURGE)); then
  read -r -p "Se borrarán TODAS las apps instaladas con MaLiVe y sus datos. ¿Seguro? [s/N] " ans
  case "$ans" in s|S|si|sí|y|Y) ;; *) echo "Cancelado."; exit 1 ;; esac
  rm -f "$DATA"/applications/malive-*.desktop
  rm -rf "$DATA/malive" "$CONF/malive" "$STATE/malive"
  command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
  echo "MaLiVe y todos sus datos eliminados."
else
  rm -rf "$DATA/malive/lib"
  echo "Comandos eliminados. Tus apps y prefijos siguen en $DATA/malive (usa --purge para borrarlos)."
fi
rm -f "$BIN/malive" "$BIN/malive-run"
