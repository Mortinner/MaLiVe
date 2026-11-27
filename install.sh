#!/usr/bin/env bash
# MaLiVe installer — terminal setup wizard (Spanish or English, following the system language).
#
#   ./install.sh          (or double-click → "Run"): opens a terminal with the wizard
#   ./install.sh --yes    no questions (unattended install)
#
# Runs as your user; it only asks for your password to install missing
# system packages.

set -uo pipefail

SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
SRC="$(dirname "$SCRIPT")"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/malive/__init__.py" 2>/dev/null)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/malive"
LIB="$DATA/lib"
BIN="$HOME/.local/bin"
LOGDIR="${XDG_STATE_HOME:-$HOME/.local/state}/malive"
LOG="$LOGDIR/install.log"
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,9p' "$SCRIPT" | sed 's/^# \{0,1\}//'; exit 0 ;;
  esac
done

# Idioma de los textos: español si el sistema está en español; inglés en otro caso.
# (MALIVE_LANG=es|en lo fuerza.)
_lang="${LANGUAGE:-}"; _lang="${_lang%%:*}"
[ -n "$_lang" ] || _lang="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
case "${MALIVE_LANG:-$_lang}" in es*|ES*) UI_LANG=es ;; *) UI_LANG=en ;; esac
T() { if [ "$UI_LANG" = es ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }

mkdir -p "$LOGDIR"
[ -z "${MALIVE_RELAUNCHED:-}" ] && : > "$LOG"
log() { printf '%s %s\n' "$(date +%T)" "$*" >> "$LOG"; }

# ==========================================================================
# 0. Asegurar una terminal (doble clic → "Ejecutar" no abre ninguna)
# ==========================================================================

graphical() { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }

relaunch_in_terminal() {
  local inner=(env MALIVE_RELAUNCHED=1 bash "$SCRIPT" "$@")
  local desk="${XDG_CURRENT_DESKTOP:-}" cands=()
  case "$desk" in
    *KDE*)   cands=(konsole) ;;
    *GNOME*) cands=(ptyxis kgx gnome-terminal) ;;
    *XFCE*)  cands=(xfce4-terminal) ;;
    *MATE*)  cands=(mate-terminal) ;;
  esac
  cands+=(xdg-terminal-exec konsole ptyxis kgx gnome-terminal xfce4-terminal mate-terminal
          alacritty kitty foot wezterm xterm)
  local t cmd pid
  for t in "${cands[@]}"; do
    command -v "$t" >/dev/null || continue
    case "$t" in
      konsole)                  cmd=(konsole --hide-menubar -e "${inner[@]}") ;;
      gnome-terminal)           cmd=(gnome-terminal --geometry=96x32 -- "${inner[@]}") ;;
      ptyxis|kgx)               cmd=("$t" -- "${inner[@]}") ;;
      xfce4-terminal|mate-terminal) cmd=("$t" --geometry=96x32 -x "${inner[@]}") ;;
      wezterm)                  cmd=(wezterm start -- "${inner[@]}") ;;
      kitty|foot|xdg-terminal-exec) cmd=("$t" "${inner[@]}") ;;
      *)                        cmd=("$t" -e "${inner[@]}") ;;
    esac
    log "abriendo terminal: ${cmd[*]}"
    setsid "${cmd[@]}" >/dev/null 2>&1 < /dev/null &
    pid=$!
    sleep 1.5
    # Sigue abierta, o el lanzador terminó bien tras abrir la ventana
    if kill -0 "$pid" 2>/dev/null || wait "$pid"; then
      return 0
    fi
  done
  return 1
}

if [ "$ASSUME_YES" = 0 ] && { [ ! -t 0 ] || [ ! -t 1 ]; }; then
  if [ -z "${MALIVE_RELAUNCHED:-}" ] && graphical && relaunch_in_terminal "$@"; then
    exit 0
  fi
  msg="$(T "No he podido abrir una terminal para instalar MaLiVe.
Abre una terminal en esta carpeta y escribe:  ./install.sh" \
"Could not open a terminal to install MaLiVe.
Open a terminal in this folder and type:  ./install.sh")"
  if graphical && command -v kdialog >/dev/null; then kdialog --title "MaLiVe" --error "$msg"
  elif graphical && command -v zenity >/dev/null; then zenity --error --title "MaLiVe" --text "$msg"
  elif command -v notify-send >/dev/null; then notify-send "MaLiVe" "$msg"
  else printf '%s\n' "$msg" >&2; fi
  exit 1
fi

# ==========================================================================
# 1. Ventanas en la terminal (estilo whiptail, sin dependencias)
# ==========================================================================

# Los anchos de las ventanas se calculan en caracteres: si el idioma del
# sistema no es UTF-8 o no está generado (bash contaría bytes), usamos C.UTF-8.
# El idioma de los textos (UI_LANG) ya se decidió antes y no cambia.
_probe="…"
if [ "${#_probe}" -ne 1 ]; then export LC_ALL=C.UTF-8; fi

E=$'\e'
C_BG="${E}[0;37;44m"          # fondo azul
C_WIN="${E}[0;30;47m"         # ventana gris claro
C_TITLE="${E}[1;31;47m"       # título rojo
C_BTN="${E}[0;30;47m"
C_BTN_SEL="${E}[1;37;41m"     # botón elegido: blanco sobre rojo
C_FIELD="${E}[0;37;44m"
C_OK="${E}[0;32;47m"
C_ERR="${E}[1;31;47m"
C_DIM="${E}[0;90;47m"
C_SHADOW="${E}[0;40m"
RST="${E}[0m"

TUI_ON=0
COLS=80 ROWS=24

tui_start() {
  [ "$TUI_ON" = 1 ] && return
  TUI_ON=1
  printf '%s' "${E}[?1049h${E}[?25l"   # pantalla alternativa, sin cursor
  stty -echo 2>/dev/null
}

tui_stop() {
  [ "$TUI_ON" = 1 ] || return
  TUI_ON=0
  printf '%s' "${RST}${E}[2J${E}[H${E}[?25h${E}[?1049l"
  stty echo 2>/dev/null
}
trap 'tui_stop' EXIT
trap 'tui_stop; exit 130' INT TERM

at() { printf '%s' "${E}[$1;$2H"; }

size() {
  local s
  s="$(stty size 2>/dev/null)" || s="24 80"
  ROWS="${s% *}"; COLS="${s#* }"
  [ "$ROWS" -ge 10 ] 2>/dev/null || ROWS=24
  [ "$COLS" -ge 40 ] 2>/dev/null || COLS=80
}

pad() { # texto ancho → texto relleno con espacios hasta "ancho" caracteres
  local s="$1" w="$2"
  if [ "${#s}" -gt "$w" ]; then s="${s:0:$((w-1))}…"; fi
  printf '%s%*s' "$s" $((w - ${#s})) ''
}

# Ajusta texto a un ancho (respeta párrafos y la sangría de las viñetas).
# Resultado en el array WRAP.
wrap() {
  local -
  set -f   # que "[multilib]" o "*" no se expandan como comodines
  local text="$1" width="$2" para line word indent cont
  WRAP=()
  while IFS= read -r para || [ -n "$para" ]; do
    if [ -z "$para" ]; then WRAP+=(""); continue; fi
    indent="${para%%[! ]*}"
    cont="$indent"
    [[ "${para#"$indent"}" == "• "* ]] && cont="$indent  "
    line="$indent"
    for word in ${para#"$indent"}; do
      if [ "$line" = "$indent" ] || [ "$line" = "$cont" ]; then
        line+="$word"
      elif [ $(( ${#line} + 1 + ${#word} )) -le "$width" ]; then
        line+=" $word"
      else
        WRAP+=("$line"); line="$cont$word"
      fi
    done
    WRAP+=("$line")
  done <<< "$text"
}

draw_background() {
  size
  local r blank
  blank="$(printf '%*s' "$COLS" '')"
  printf '%s' "$C_BG"
  for ((r = 1; r <= ROWS; r++)); do at "$r" 1; printf '%s' "$blank"; done
  at 1 2; printf '%s' "${E}[1;37;44m$(T "Instalación de MaLiVe" "MaLiVe Setup") ${VERSION}${C_BG}"
  at "$ROWS" 2; printf '%s' "$(T "←/→ o Tab: elegir   Intro: aceptar   Esc: cancelar" "←/→ or Tab: select   Enter: accept   Esc: cancel")"
}

# Dibuja el marco de una ventana. Deja en WX, WY (esquina), WW (ancho
# interior) y WH (alto interior) la geometría.
frame() {
  local title="$1" inner_h="$2" w
  w=$(( COLS - 8 )); [ "$w" -gt 74 ] && w=74
  WW=$(( w - 4 ))
  local h=$(( inner_h + 2 ))
  WX=$(( (COLS - w) / 2 + 1 )); WY=$(( (ROWS - h) / 2 + 1 )); [ "$WY" -lt 2 ] && WY=2
  local hline r
  hline="$(printf '%*s' $((w - 2)) '' | sed 's/ /─/g')"
  printf '%s' "$C_WIN"
  at "$WY" "$WX"; printf '┌%s┐' "$hline"
  for ((r = 1; r <= inner_h; r++)); do
    at $((WY + r)) "$WX"; printf '│%*s│' $((w - 2)) ''
  done
  at $((WY + h - 1)) "$WX"; printf '└%s┘' "$hline"
  # sombra
  printf '%s' "$C_SHADOW"
  for ((r = 1; r <= h; r++)); do at $((WY + r)) $((WX + w)); printf '  '; done
  at $((WY + h)) $((WX + 2)); printf '%*s' "$w" ''
  # título
  at "$WY" $((WX + (w - ${#title} - 2) / 2)); printf '%s' "${C_TITLE} ${title} ${C_WIN}"
}

text_line() { # fila(relativa) texto [color]
  at $((WY + $1)) $((WX + 2)); printf '%s' "${3:-$C_WIN}"; pad "$2" "$WW"; printf '%s' "$C_WIN"
}

draw_buttons() { # fila(relativa) seleccionado botones...
  local row="$1" sel="$2"; shift 2
  local labels=("$@") total=0 i x
  for i in "${!labels[@]}"; do total=$(( total + ${#labels[$i]} + 4 + 3 )); done
  x=$(( WX + 2 + (WW - total + 3) / 2 ))
  for i in "${!labels[@]}"; do
    at $((WY + row)) "$x"
    if [ "$i" -eq "$sel" ]; then printf '%s' "$C_BTN_SEL"; else printf '%s' "$C_BTN"; fi
    printf '< %s >' "${labels[$i]}"
    printf '%s' "$C_WIN"
    x=$(( x + ${#labels[$i]} + 4 + 3 ))
  done
}

read_key() { # → KEY: left right enter esc tab bs u otro carácter
  local k rest
  IFS= read -rsn1 k || { KEY=esc; return; }
  case "$k" in
    $'\e')
      rest=""
      IFS= read -rsn2 -t 0.05 rest
      case "$rest" in
        '[D') KEY=left ;; '[C') KEY=right ;; '[A') KEY=up ;; '[B') KEY=down ;;
        '[Z') KEY=left ;; '') KEY=esc ;; *) KEY=other ;;
      esac ;;
    '') KEY=enter ;;
    $'\t') KEY=tab ;;
    $'\x7f'|$'\b') KEY=bs ;;
    *) KEY="$k" ;;
  esac
}

# dialog "Título" "Texto" "Botón1" ["Botón2"...]  → CHOICE (índice). Esc = último botón.
dialog() {
  tui_start
  local title="$1" text="$2"; shift 2
  local buttons=("$@") sel=0
  local n=${#buttons[@]}
  while :; do
    draw_background
    local w=$(( COLS - 8 )); [ "$w" -gt 74 ] && w=74
    wrap "$text" $(( w - 4 ))
    local lines=("${WRAP[@]}") maxh=$(( ROWS - 8 ))
    [ "${#lines[@]}" -gt "$maxh" ] && lines=("${lines[@]:0:$maxh}")
    frame "$title" $(( ${#lines[@]} + 3 ))
    local i
    for i in "${!lines[@]}"; do text_line $((i + 2)) "${lines[$i]}"; done
    draw_buttons $(( ${#lines[@]} + 3 )) "$sel" "${buttons[@]}"
    read_key
    case "$KEY" in
      left|up) sel=$(( (sel - 1 + n) % n )) ;;
      right|down|tab) sel=$(( (sel + 1) % n )) ;;
      enter) CHOICE=$sel; return ;;
      esc) CHOICE=$(( n - 1 )); return ;;
    esac
  done
}

# password "Texto" → PASSWORD; devuelve 1 si se cancela
password() {
  tui_start
  local text="$1" pw="" sel=0 err="${2:-}"
  while :; do
    draw_background
    local w=$(( COLS - 8 )); [ "$w" -gt 74 ] && w=74
    wrap "$text" $(( w - 4 ))
    local lines=("${WRAP[@]}") i
    frame "$(T "Contraseña" "Password")" $(( ${#lines[@]} + 6 ))
    for i in "${!lines[@]}"; do text_line $((i + 2)) "${lines[$i]}"; done
    local frow=$(( ${#lines[@]} + 3 ))
    local fw=$(( WW > 40 ? 40 : WW ))
    local label; label="$(T "Contraseña: " "Password:   ")"
    at $((WY + frow)) $((WX + 2)); printf '%s' "${C_WIN}${label}"
    local dots; dots="$(printf '%*s' "${#pw}" '' | sed 's/ /•/g')"
    printf '%s' "$C_FIELD"; pad "$dots" $(( fw - 12 )); printf '%s' "$C_WIN"
    [ -n "$err" ] && text_line $(( frow + 1 )) "$err" "$C_ERR"
    draw_buttons $(( frow + 3 )) "$sel" "$(T "Aceptar" "OK")" "$(T "Cancelar" "Cancel")"
    read_key
    case "$KEY" in
      enter) if [ "$sel" -eq 1 ]; then return 1; fi; PASSWORD="$pw"; return 0 ;;
      esc) return 1 ;;
      tab|left|right|up|down) sel=$(( 1 - sel )) ;;
      bs) pw="${pw%?}"; err="" ;;
      other) ;;
      *) pw+="$KEY"; err="" ;;
    esac
  done
}

# Ventana de progreso: pasos con marcas y la última línea del registro.
STEP_LABELS=() STEP_STATE=()   # pending running ok fail warn
SPIN=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
draw_progress() { # tick estado_texto
  local tick="$1" status="$2" i mark color
  draw_background
  frame "$(T "Instalando" "Installing")" $(( ${#STEP_LABELS[@]} + 4 ))
  for i in "${!STEP_LABELS[@]}"; do
    case "${STEP_STATE[$i]}" in
      ok)      mark="✔"; color="$C_OK" ;;
      fail)    mark="✘"; color="$C_ERR" ;;
      warn)    mark="!"; color="$C_ERR" ;;
      running) mark="${SPIN[$(( tick % ${#SPIN[@]} ))]}"; color="${E}[1;30;47m" ;;
      *)       mark="·"; color="$C_DIM" ;;
    esac
    text_line $((i + 2)) " $mark  ${STEP_LABELS[$i]}" "$color"
  done
  text_line $(( ${#STEP_LABELS[@]} + 2 )) ""
  text_line $(( ${#STEP_LABELS[@]} + 3 )) "  $status" "$C_DIM"
}

last_log_line() {
  tail -c 400 "$LOG" 2>/dev/null | tr '\r' '\n' | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' \
    | sed 's/^[0-9:]* //; s/^[[:space:]]*//' | grep -e '^» ' -e '%' | tail -n 1 | sed 's/^» //'
}

# run_step índice función → 0 si va bien
run_step() {
  local idx="$1" fn="$2" tick=0 rc
  STEP_STATE[$idx]=running
  log "== ${STEP_LABELS[$idx]}"
  ( "$fn" ) >> "$LOG" 2>&1 < /dev/null &
  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    draw_progress "$tick" "$(last_log_line)"
    tick=$((tick + 1))
    sleep 0.15
  done
  wait "$pid"; rc=$?
  log "== código $rc"
  return "$rc"
}

# Modo sin ventanas (--yes): mensajes en texto
say() { printf '%s\n' "$*"; }

fatal() { # título texto
  log "ERROR: $1: $2"
  if [ "$ASSUME_YES" = 1 ]; then say "ERROR: $2" >&2; exit 1; fi
  dialog "$1" "$2"$'\n\n'"$(T "Registro de la instalación" "Installation log"): $LOG" "$(T "Cerrar" "Close")"
  exit 1
}

cancelled() {
  log "cancelado por el usuario"
  dialog "$(T "Instalación cancelada" "Setup cancelled")" "$(T "No se ha instalado ni cambiado nada.

Puedes volver a ejecutar el instalador cuando quieras." "Nothing has been installed or changed.

You can run the installer again whenever you like.")" "$(T "Cerrar" "Close")"
  exit 1
}

# ==========================================================================
# 2. Comprobaciones
# ==========================================================================

log "MaLiVe $VERSION, usuario $(id -un), escritorio ${XDG_CURRENT_DESKTOP:-?}, TERM=${TERM:-?}"

if [ "$(id -u)" -eq 0 ]; then
  fatal "$(T "Ejecución como administrador" "Running as administrator")" "$(T "No ejecutes el instalador con sudo ni como root: MaLiVe se instala en tu carpeta personal.

Ciérralo y vuelve a abrirlo con tu usuario normal. Te pedirá la contraseña solo cuando haga falta." \
"Don't run the installer with sudo or as root: MaLiVe installs into your home folder.

Close it and open it again as your normal user. It will ask for your password only when needed.")"
fi

if ! command -v pacman >/dev/null; then
  fatal "$(T "Distribución no compatible" "Unsupported distribution")" "$(T "Por ahora el instalador de MaLiVe solo funciona en distribuciones basadas en Arch Linux (CachyOS, Arch, EndeavourOS, Manjaro…).

Software que necesita MaLiVe: umu-launcher, icoutils, msitools, libnotify, desktop-file-utils, xdg-user-dirs y zenity o kdialog." \
"For now the MaLiVe installer only works on Arch-based distributions (CachyOS, Arch, EndeavourOS, Manjaro…).

Software MaLiVe needs: umu-launcher, icoutils, msitools, libnotify, desktop-file-utils, xdg-user-dirs and zenity or kdialog.")"
fi

PKGS=() WHY=()
need() { pacman -Qq "$1" >/dev/null 2>&1 || { PKGS+=("$1"); WHY+=("$2"); }; }
command -v python3 >/dev/null || need python "$(T "lenguaje en el que está hecho MaLiVe" "the language MaLiVe is written in")"
need umu-launcher       "$(T "ejecuta los programas de Windows" "runs the Windows programs")"
need icoutils           "$(T "iconos de los programas en el menú" "program icons in the menu")"
need msitools           "$(T "lee los instaladores .msi" "reads .msi installers")"
need libnotify          "$(T "avisos en el escritorio" "desktop notifications")"
need desktop-file-utils "$(T "añade los programas al menú" "adds programs to the menu")"
need xdg-user-dirs      "$(T "localiza Documentos y Descargas" "finds your Documents and Downloads")"
if ! pacman -Qq zenity >/dev/null 2>&1 && ! pacman -Qq kdialog >/dev/null 2>&1; then
  if [[ "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]]; then need kdialog "$(T "ventanas de aviso y progreso" "message and progress windows")"
  else need zenity "$(T "ventanas de aviso y progreso" "message and progress windows")"; fi
fi
log "faltan: ${PKGS[*]:-nada}"

if [ "${#PKGS[@]}" -gt 0 ]; then
  unavailable=()
  for p in "${PKGS[@]}"; do pacman -Si "$p" >/dev/null 2>&1 || unavailable+=("$p"); done
  if [ "${#unavailable[@]}" -gt 0 ]; then
    if [[ " ${unavailable[*]} " == *" umu-launcher "* ]]; then
      fatal "$(T "Falta un repositorio" "Missing repository")" "$(T "El programa umu-launcher está en el repositorio «multilib», que en tu sistema está desactivado.

Para activarlo, abre /etc/pacman.conf como administrador y quita el # de estas dos líneas:" \
"umu-launcher lives in the «multilib» repository, which is disabled on your system.

To enable it, open /etc/pacman.conf as administrator and remove the # from these two lines:")

    [multilib]
    Include = /etc/pacman.d/mirrorlist

$(T "Después actualiza el sistema (sudo pacman -Syu) y vuelve a ejecutar el instalador." \
"Then update the system (sudo pacman -Syu) and run the installer again.")"
    fi
    fatal "$(T "Software no encontrado" "Software not found")" "$(T "No encuentro en los repositorios: ${unavailable[*]}

Puede que la lista de paquetes esté desactualizada. Actualiza el sistema (sudo pacman -Syu) y vuelve a ejecutar el instalador." \
"Not found in the repositories: ${unavailable[*]}

The package list may be out of date. Update the system (sudo pacman -Syu) and run the installer again.")"
  fi
fi

# ==========================================================================
# 3. Asistente
# ==========================================================================

INSTALLED="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$LIB/malive/__init__.py" 2>/dev/null)"
log "versión instalada: ${INSTALLED:-ninguna}"

if [ "$ASSUME_YES" = 0 ]; then
  if [ -n "$INSTALLED" ]; then
    if [ "$INSTALLED" = "$VERSION" ]; then
      what="$(T "MaLiVe ${VERSION} ya está instalado. Este asistente lo reinstalará." \
                 "MaLiVe ${VERSION} is already installed. This wizard will reinstall it.")"
    else
      what="$(T "Este asistente actualizará MaLiVe de la versión ${INSTALLED} a la ${VERSION}." \
                 "This wizard will update MaLiVe from version ${INSTALLED} to ${VERSION}.")"
    fi
    dialog "$(T "Bienvenido" "Welcome")" "${what}

$(T "Tus programas instalados, sus datos y GE-Proton se conservan." "Your installed programs, their data and GE-Proton are kept.")" \
      "$(T "Siguiente" "Next")" "$(T "Cancelar" "Cancel")"
  else
  dialog "$(T "Bienvenido" "Welcome")" "$(T "Este asistente instalará MaLiVe ${VERSION} en tu equipo.

Con MaLiVe podrás abrir programas de Windows (.exe y .msi) con un doble clic, igual que en Windows: se instalan, aparecen en el menú de aplicaciones con su icono y se abren desde ahí." \
"This wizard will install MaLiVe ${VERSION} on your computer.

With MaLiVe you can open Windows programs (.exe and .msi) with a double click, just like on Windows: they get installed, show up in your app menu with their icon and open from there.")" \
    "$(T "Siguiente" "Next")" "$(T "Cancelar" "Cancel")"
  fi
  [ "$CHOICE" -eq 0 ] || cancelled

  if compgen -G "$DATA/proton/GE-Proton*/proton" >/dev/null; then
    dl="$(T "GE-Proton ya está descargado: no hace falta descargar nada grande." \
             "GE-Proton is already downloaded: nothing big to download.")"
  else
    dl="$(T "También se descargarán GE-Proton y el entorno de Steam (alrededor de 1 GB)." \
             "GE-Proton and the Steam runtime will also be downloaded (about 1 GB).")"
  fi
  if [ -n "$INSTALLED" ]; then
    ask="$(T "¿Deseas continuar con la actualización?" "Do you want to continue with the update?")"
  else
    ask="$(T "¿Deseas continuar con la instalación?" "Do you want to continue with the installation?")"
  fi
  if [ "${#PKGS[@]}" -gt 0 ]; then
    list=""
    for i in "${!PKGS[@]}"; do list+="  • ${PKGS[$i]}: ${WHY[$i]}"$'\n'; done
    body="$(T "Para funcionar, MaLiVe necesita el siguiente software de los repositorios oficiales de tu sistema:" \
               "To work, MaLiVe needs the following software from your system's official repositories:")

${list}
${dl}

${ask}"
  else
    body="$(T "Todo el software que necesita MaLiVe ya está instalado." "All the software MaLiVe needs is already installed.")

${dl}

${ask}"
  fi
  dialog "$(T "Software necesario" "Required software")" "$body" "$(T "Continuar" "Continue")" "$(T "Cancelar" "Cancel")"
  [ "$CHOICE" -eq 0 ] || cancelled
fi

# --- contraseña (solo si hay que instalar paquetes) ---
ELEVATE=() PASSWORD=""
if [ "${#PKGS[@]}" -gt 0 ]; then
  if command -v sudo >/dev/null; then
    ELEVATE=(sudo)
    if ! sudo -n true 2>/dev/null; then
      if [ "$ASSUME_YES" = 1 ]; then
        sudo -v || { say "$(T "No se pudo obtener permiso de administrador." "Could not get administrator permission.")" >&2; exit 1; }
      else
        tries=0 err=""
        while :; do
          password "$(T "Para instalar el software necesario hace falta la contraseña de administrador (la misma con la que inicias sesión)." \
                         "Installing the required software needs the administrator password (the one you log in with).")" "$err" || cancelled
          if printf '%s\n' "$PASSWORD" | sudo -S -p '' -v 2>/dev/null; then break; fi
          tries=$((tries + 1))
          [ "$tries" -ge 3 ] && fatal "$(T "Contraseña" "Password")" "$(T "No se ha podido validar la contraseña.

Comprueba que es la correcta y que tu usuario tiene permisos de administrador." \
"The password could not be verified.

Check that it is correct and that your user has administrator rights.")"
          err="$(T "Contraseña incorrecta. Inténtalo de nuevo." "Wrong password. Please try again.")"
        done
      fi
    fi
  elif command -v pkexec >/dev/null; then
    ELEVATE=(pkexec)   # pide la contraseña en una ventana del sistema
  else
    fatal "$(T "Sin permisos" "No permissions")" "$(T "No encuentro sudo ni pkexec para instalar el software necesario." "Neither sudo nor pkexec was found to install the required software.")"
  fi
fi

# ==========================================================================
# 4. Instalación
# ==========================================================================

step_packages() {
  echo "» $(T "Instalando" "Installing") ${PKGS[*]}…"
  if [ "${ELEVATE[0]}" = sudo ] && [ -n "$PASSWORD" ]; then
    printf '%s\n' "$PASSWORD" | sudo -S -p '' pacman -S --needed --noconfirm "${PKGS[@]}"
  else
    "${ELEVATE[@]}" pacman -S --needed --noconfirm "${PKGS[@]}"
  fi
}

step_copy() {
  local py
  py="$(command -v python3)"; [ -x /usr/bin/python3 ] && py=/usr/bin/python3
  "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || { T "Hace falta Python 3.10 o superior" "Python 3.10 or newer is required"; echo; return 1; }
  mkdir -p "$LIB" "$BIN" || return 1
  rm -rf "$LIB/malive.new"
  cp -r "$SRC/malive" "$LIB/malive.new" || return 1
  find "$LIB/malive.new" -name '__pycache__' -prune -exec rm -rf {} +
  rm -rf "$LIB/malive" && mv "$LIB/malive.new" "$LIB/malive" || return 1
  local pair name mod
  for pair in "malive:cli" "malive-run:runner"; do
    name="${pair%%:*}"; mod="${pair##*:}"
    printf '#!%s\nimport sys\nsys.path.insert(0, "%s")\nfrom malive.%s import main\nsys.exit(main())\n' \
      "$py" "$LIB" "$mod" > "$BIN/$name" && chmod +x "$BIN/$name" || return 1
  done
  echo "» $(T "MaLiVe copiado en" "MaLiVe copied to") $LIB"
}

step_integrate() {
  echo "» $(T "Asociando archivos y actualizando el menú…" "Associating files and updating the menu…")"
  "$BIN/malive" integrate && "$BIN/malive" refresh
}
step_setup() {
  echo "» $(T "Preparando GE-Proton y el entorno de Steam (unos minutos la primera vez)…" \
               "Preparing GE-Proton and the Steam runtime (a few minutes the first time)…")"
  "$BIN/malive" setup
}

STEP_LABELS=()
[ "${#PKGS[@]}" -gt 0 ] && STEP_LABELS+=("$(T "Instalar el software necesario" "Install the required software")")
STEP_LABELS+=("$(T "Copiar MaLiVe" "Copy MaLiVe")"
              "$(T "Asociar los archivos .exe, .msi y .lnk" "Associate .exe, .msi and .lnk files")"
              "$(T "Descargar GE-Proton y preparar el entorno" "Download GE-Proton and prepare the runtime")")
STEP_FUNCS=()
[ "${#PKGS[@]}" -gt 0 ] && STEP_FUNCS+=(step_packages)
STEP_FUNCS+=(step_copy step_integrate step_setup)
for i in "${!STEP_LABELS[@]}"; do STEP_STATE[$i]=pending; done
LAST=$(( ${#STEP_FUNCS[@]} - 1 ))

if [ "$ASSUME_YES" = 1 ]; then
  for i in "${!STEP_FUNCS[@]}"; do
    say "→ ${STEP_LABELS[$i]}"
    "${STEP_FUNCS[$i]}" 2>&1 | tee -a "$LOG" || { [ "$i" -eq "$LAST" ] || { say "Error. Log: $LOG" >&2; exit 1; }; }
  done
  say "$(T "MaLiVe instalado." "MaLiVe installed.")"
  exit 0
fi

tui_start
SETUP_FAILED=0
for i in "${!STEP_FUNCS[@]}"; do
  if run_step "$i" "${STEP_FUNCS[$i]}"; then
    STEP_STATE[$i]=ok
  elif [ "$i" -eq "$LAST" ]; then
    STEP_STATE[$i]=warn; SETUP_FAILED=1     # no es grave: se reintenta en el primer uso
  else
    STEP_STATE[$i]=fail
    draw_progress 0 ""
    sleep 1
    hint=""
    [ "${STEP_FUNCS[$i]}" = step_packages ] && hint="

$(T "Si no se pudo descargar algún paquete, actualiza el sistema (sudo pacman -Syu) y vuelve a ejecutar el instalador." \
     "If a package could not be downloaded, update the system (sudo pacman -Syu) and run the installer again.")"
    fatal "$(T "Error en la instalación" "Installation error")" "$(T "Ha fallado el paso «${STEP_LABELS[$i]}»." "The step «${STEP_LABELS[$i]}» failed.")${hint}"
  fi
done
PASSWORD=""
# Lo que MaLiVe ha detectado y usará para todos los programas
HW_INFO="$("$BIN/malive" hardware --short 2>/dev/null | sed 's/^/  • /')"
[ -n "$HW_INFO" ] && HW_INFO="

$(T "Tu equipo (todos los programas lo usarán):" "Your computer (every program will use it):")
$HW_INFO"
draw_progress 0 "$(T "Terminado." "Done.")"
sleep 0.6

FINISH="$(T "Finalizar" "Finish")"
if [ "$SETUP_FAILED" = 1 ]; then
  dialog "$(T "Instalación terminada" "Setup complete")" "$(T "MaLiVe se ha instalado, pero no se ha podido preparar GE-Proton (¿sin conexión a Internet?).

No pasa nada: se preparará automáticamente la primera vez que abras un programa de Windows." \
"MaLiVe has been installed, but GE-Proton could not be prepared (no Internet connection?).

No problem: it will be prepared automatically the first time you open a Windows program.")

$(T "Registro" "Log"): $LOG" "$FINISH"
elif [ -n "$INSTALLED" ] && [ "$INSTALLED" != "$VERSION" ]; then
  dialog "$(T "Actualización terminada" "Update complete")" "$(T "MaLiVe se ha actualizado a la versión ${VERSION}.

Tus programas siguen en el menú de aplicaciones. Ya puedes borrar la carpeta del instalador." \
"MaLiVe has been updated to version ${VERSION}.

Your programs are still in the app menu. You can now delete the installer folder.")${HW_INFO}" "$FINISH"
else
  dialog "$(T "Instalación terminada" "Setup complete")" "$(T "MaLiVe se ha instalado correctamente.

Para usarlo, haz doble clic en cualquier archivo .exe o .msi desde tu gestor de archivos. Los programas que instales aparecerán en el menú de aplicaciones." \
"MaLiVe has been installed successfully.

To use it, double-click any .exe or .msi file in your file manager. The programs you install will appear in your app menu.")${HW_INFO}" "$FINISH"
fi
exit 0
