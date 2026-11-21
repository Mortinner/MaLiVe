#!/usr/bin/env bash
# Instalador de MaLiVe.
#
#   ./install.sh          te dice qué necesita, te pide permiso y lo instala todo
#   ./install.sh --yes    igual, sin preguntar (instalaciones desatendidas)
#
# Se ejecuta como tu usuario (no con sudo): pedirá la contraseña solo para
# instalar los paquetes del sistema que falten.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/malive"
LIB="$DATA/lib"
BIN="$HOME/.local/bin"
ASSUME_YES=0

for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Opción desconocida: $arg" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'; else B= G= Y= R= N=; fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s→ %s%s\n' "$B" "$*" "$N"; }
bye()  { printf '\n%s%s%s\n' "$R" "$*" "$N" >&2; exit 1; }

say "${B}MaLiVe — Make Linux Versatil${N}"
say "Ejecuta programas de Windows con doble clic."

# --------------------------------------------------------------------------
# Comprobaciones previas
# --------------------------------------------------------------------------
if [ "$(id -u)" -eq 0 ]; then
  bye "No ejecutes el instalador con sudo ni como root.
Lánzalo con tu usuario:  ./install.sh
Te pedirá la contraseña solo cuando haga falta instalar algo."
fi

if ! command -v pacman >/dev/null; then
  bye "Por ahora MaLiVe solo sabe instalar sus dependencias en distribuciones
basadas en Arch (CachyOS, Arch, EndeavourOS, Manjaro…).
Necesita: umu-launcher, icoutils, msitools, libnotify, zenity o kdialog,
desktop-file-utils y xdg-user-dirs."
fi

# --------------------------------------------------------------------------
# Qué falta
# --------------------------------------------------------------------------
declare -a PKGS=() WHY=()
need() { # paquete motivo
  pacman -Qq "$1" >/dev/null 2>&1 && return 0
  PKGS+=("$1"); WHY+=("$2")
}

command -v python3 >/dev/null || need python "el lenguaje en el que está escrito MaLiVe"
need umu-launcher       "ejecuta los programas de Windows con Proton, fuera de Steam"
need icoutils           "saca los iconos de los programas para el menú"
need msitools           "lee el nombre de los instaladores .msi"
need libnotify          "avisos en el escritorio («Instalado», «Preparando…»)"
need desktop-file-utils "registra los programas en el menú de aplicaciones"
need xdg-user-dirs      "localiza tus carpetas Documentos y Descargas"
if ! pacman -Qq zenity >/dev/null 2>&1 && ! pacman -Qq kdialog >/dev/null 2>&1; then
  if [[ "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]]; then
    need kdialog "ventanas de diálogo (preguntas, errores, progreso)"
  else
    need zenity "ventanas de diálogo (preguntas, errores, progreso)"
  fi
fi

if ((${#PKGS[@]})); then
  # ¿Están en los repositorios? umu-launcher está en [multilib].
  unavailable=()
  for p in "${PKGS[@]}"; do
    pacman -Si "$p" >/dev/null 2>&1 || unavailable+=("$p")
  done
  if ((${#unavailable[@]})); then
    msg="No encuentro en los repositorios: ${unavailable[*]}"
    if [[ " ${unavailable[*]} " == *" umu-launcher "* ]]; then
      msg+="

umu-launcher está en el repositorio [multilib], que parece desactivado.
Actívalo descomentando estas dos líneas en /etc/pacman.conf:
    [multilib]
    Include = /etc/pacman.d/mirrorlist
y después ejecuta:  sudo pacman -Syu
Luego vuelve a lanzar ./install.sh"
    else
      msg+="

Puede que la base de datos de paquetes esté desactualizada:
ejecuta  sudo pacman -Syu  y vuelve a lanzar ./install.sh"
    fi
    bye "$msg"
  fi
fi

# --------------------------------------------------------------------------
# Pedir autorización
# --------------------------------------------------------------------------
say ""
say "${B}Para funcionar, MaLiVe va a:${N}"
if ((${#PKGS[@]})); then
  say ""
  say "  1. Instalar estos programas de tus repositorios oficiales:"
  for i in "${!PKGS[@]}"; do
    printf '       %s%-19s%s %s\n' "$G" "${PKGS[$i]}" "$N" "${WHY[$i]}"
  done
  say "     (con: sudo pacman -S --needed ${PKGS[*]})"
  say "     ${Y}Te pedirá tu contraseña.${N}"
  n=2
else
  say ""
  say "  ✓ Los programas necesarios ya están instalados."
  n=1
fi
say ""
say "  $n. Descargar GE-Proton y el runtime de Steam (~1 GB) en tu carpeta personal."
say "  $((n+1)). Abrir con MaLiVe los .exe, .msi y .lnk al hacer doble clic."
say "  $((n+2)). Instalarse en ~/.local (sin tocar el sistema)."
say ""

if ((ASSUME_YES)); then
  say "(--yes: autorizado)"
else
  if [ ! -t 0 ]; then
    bye "Necesito tu autorización y no hay una terminal donde preguntarte.
Ejecuta ./install.sh desde una terminal, o ./install.sh --yes para autorizarlo directamente."
  fi
  read -r -p "${B}¿Me autorizas? [s/N]${N} " answer
  case "${answer,,}" in
    s|si|sí|y|yes) ;;
    *) say ""; say "Vale, no he instalado ni cambiado nada. ¡Hasta otra!"; exit 1 ;;
  esac
fi

# --------------------------------------------------------------------------
# 1. Paquetes
# --------------------------------------------------------------------------
if ((${#PKGS[@]})); then
  step "Instalando: ${PKGS[*]}"
  if command -v sudo >/dev/null; then ELEVATE=(sudo)
  elif command -v doas >/dev/null; then ELEVATE=(doas)
  elif command -v pkexec >/dev/null; then ELEVATE=(pkexec)
  else bye "No encuentro sudo, doas ni pkexec para instalar los paquetes."; fi

  if ! "${ELEVATE[@]}" pacman -S --needed --noconfirm "${PKGS[@]}"; then
    bye "La instalación de paquetes ha fallado. No se ha instalado MaLiVe.
Si pacman no pudo descargar algo, actualiza el sistema (sudo pacman -Syu)
y vuelve a lanzar ./install.sh"
  fi
fi

PY="$(command -v python3)"
[ -x /usr/bin/python3 ] && PY=/usr/bin/python3
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || bye "MaLiVe necesita Python 3.10 o superior."

# --------------------------------------------------------------------------
# 2. MaLiVe en ~/.local
# --------------------------------------------------------------------------
step "Instalando MaLiVe en ~/.local"
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
say "  Comandos: $BIN/malive y $BIN/malive-run"

# --------------------------------------------------------------------------
# 3. Doble clic
# --------------------------------------------------------------------------
step "Asociando .exe, .msi y .lnk a MaLiVe"
"$BIN/malive" integrate | sed 's/^/  /'

# --------------------------------------------------------------------------
# 4. GE-Proton y runtime
# --------------------------------------------------------------------------
step "Descargando GE-Proton y preparando el runtime"
SETUP_OK=1
"$BIN/malive" setup || SETUP_OK=0

# --------------------------------------------------------------------------
say ""
if ((SETUP_OK)); then
  say "${G}${B}✓ MaLiVe está listo.${N} Haz doble clic en un .exe o .msi desde tu gestor de archivos."
else
  say "${Y}${B}MaLiVe está instalado, pero la preparación de Proton ha fallado.${N}"
  say "Se reintentará sola en el primer doble clic. Diagnóstico: malive doctor"
fi
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) say ""
     say "Para usar el comando «malive» en la terminal, añade ~/.local/bin al PATH."
     say "En fish:  fish_add_path ~/.local/bin" ;;
esac
