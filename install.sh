#!/usr/bin/env bash
# Instala MaLiVe para el usuario actual (sin root) en ~/.local
#   ./install.sh                 instalar y asociar .exe/.msi/.lnk
#   ./install.sh --no-integrate  instalar sin tocar las asociaciones
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/malive"
LIB="$DATA/lib"
BIN="$HOME/.local/bin"
PY="$(command -v python3)"
[ -x /usr/bin/python3 ] && PY=/usr/bin/python3

INTEGRATE=1
for arg in "$@"; do
  case "$arg" in
    --no-integrate) INTEGRATE=0 ;;
    *) echo "Opción desconocida: $arg" >&2; exit 2 ;;
  esac
done

"$PY" - <<'EOF' || { echo "MaLiVe necesita Python 3.10 o superior" >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
EOF

echo "→ Copiando MaLiVe en $LIB"
mkdir -p "$LIB" "$BIN"
rm -rf "$LIB/malive.new"
cp -r "$SRC/malive" "$LIB/malive.new"
find "$LIB/malive.new" -name '__pycache__' -prune -exec rm -rf {} +
rm -rf "$LIB/malive"
mv "$LIB/malive.new" "$LIB/malive"

for pair in "malive:cli" "malive-run:runner"; do
  name="${pair%%:*}"; mod="${pair##*:}"
  cat > "$BIN/$name" <<EOF
#!$PY
import sys
sys.path.insert(0, "$LIB")
from malive.$mod import main
sys.exit(main())
EOF
  chmod +x "$BIN/$name"
done
echo "→ Comandos instalados: $BIN/malive y $BIN/malive-run"

missing=()
command -v umu-run   >/dev/null || missing+=(umu-launcher)
command -v wrestool  >/dev/null || missing+=(icoutils)
command -v msiinfo   >/dev/null || missing+=(msitools)
command -v notify-send >/dev/null || missing+=(libnotify)
if ! command -v zenity >/dev/null && ! command -v kdialog >/dev/null; then
  if [[ "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]]; then missing+=(kdialog); else missing+=(zenity); fi
fi
if ((${#missing[@]})); then
  echo
  echo "Faltan paquetes recomendados. Instálalos con:"
  echo "  sudo pacman -S --needed ${missing[*]}"
  echo
fi

if ((INTEGRATE)); then
  "$BIN/malive" integrate
fi

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo
     echo "Aviso: $BIN no está en tu PATH. En fish:  fish_add_path ~/.local/bin" ;;
esac

echo
"$BIN/malive" doctor || true
echo
echo "Listo. Haz doble clic en un .exe o .msi desde el gestor de archivos."
