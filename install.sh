#!/usr/bin/env bash
# Instalador de MaLiVe — asistente con ventanas en la terminal.
#
#   ./install.sh          (o doble clic → "Ejecutar"): abre una terminal con el asistente
#   ./install.sh --yes    sin preguntas (instalaciones desatendidas)
#
# Se ejecuta como tu usuario; solo pide la contraseña para instalar el
# software del sistema que falte.

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
  msg="No he podido abrir una terminal para instalar MaLiVe.
Abre una terminal en esta carpeta y escribe:  ./install.sh"
  if graphical && command -v kdialog >/dev/null; then kdialog --title "MaLiVe" --error "$msg"
  elif graphical && command -v zenity >/dev/null; then zenity --error --title "MaLiVe" --text "$msg"
  elif command -v notify-send >/dev/null; then notify-send "MaLiVe" "$msg"
  else printf '%s\n' "$msg" >&2; fi
  exit 1
fi

# ==========================================================================
# 1. Ventanas en la terminal (estilo whiptail, sin dependencias)
# ==========================================================================

case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
  *UTF-8*|*utf8*|*UTF8*|*utf-8*) ;;
  *) export LC_ALL=C.UTF-8 ;;
esac

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
  at 1 2; printf '%s' "${E}[1;37;44mInstalación de MaLiVe ${VERSION}${C_BG}"
  at "$ROWS" 2; printf '%s' "←/→ o Tab: elegir   Intro: aceptar   Esc: cancelar"
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
    frame "Contraseña" $(( ${#lines[@]} + 6 ))
    for i in "${!lines[@]}"; do text_line $((i + 2)) "${lines[$i]}"; done
    local frow=$(( ${#lines[@]} + 3 ))
    local fw=$(( WW > 40 ? 40 : WW ))
    at $((WY + frow)) $((WX + 2)); printf '%s' "${C_WIN}Contraseña: "
    local dots; dots="$(printf '%*s' "${#pw}" '' | sed 's/ /•/g')"
    printf '%s' "$C_FIELD"; pad "$dots" $(( fw - 12 )); printf '%s' "$C_WIN"
    [ -n "$err" ] && text_line $(( frow + 1 )) "$err" "$C_ERR"
    draw_buttons $(( frow + 3 )) "$sel" "Aceptar" "Cancelar"
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
  frame "Instalando" $(( ${#STEP_LABELS[@]} + 4 ))
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
    | sed 's/^[0-9:]* //; s/^[[:space:]]*//' | grep -v -e '^$' -e '^==' | tail -n 1
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
  dialog "$1" "$2"$'\n\n'"Registro de la instalación: $LOG" "Cerrar"
  exit 1
}

cancelled() {
  log "cancelado por el usuario"
  dialog "Instalación cancelada" "No se ha instalado ni cambiado nada."$'\n\n'"Puedes volver a ejecutar el instalador cuando quieras." "Cerrar"
  exit 1
}

# ==========================================================================
# 2. Comprobaciones
# ==========================================================================

log "MaLiVe $VERSION, usuario $(id -un), escritorio ${XDG_CURRENT_DESKTOP:-?}, TERM=${TERM:-?}"

if [ "$(id -u)" -eq 0 ]; then
  fatal "Ejecución como administrador" "No ejecutes el instalador con sudo ni como root: MaLiVe se instala en tu carpeta personal.

Ciérralo y vuelve a abrirlo con tu usuario normal. Te pedirá la contraseña solo cuando haga falta."
fi

if ! command -v pacman >/dev/null; then
  fatal "Distribución no compatible" "Por ahora el instalador de MaLiVe solo funciona en distribuciones basadas en Arch Linux (CachyOS, Arch, EndeavourOS, Manjaro…).

Software que necesita MaLiVe: umu-launcher, icoutils, msitools, libnotify, desktop-file-utils, xdg-user-dirs y zenity o kdialog."
fi

PKGS=() WHY=()
need() { pacman -Qq "$1" >/dev/null 2>&1 || { PKGS+=("$1"); WHY+=("$2"); }; }
command -v python3 >/dev/null || need python "lenguaje en el que está hecho MaLiVe"
need umu-launcher       "ejecuta los programas de Windows"
need icoutils           "iconos de los programas en el menú"
need msitools           "lee los instaladores .msi"
need libnotify          "avisos en el escritorio"
need desktop-file-utils "añade los programas al menú"
need xdg-user-dirs      "localiza Documentos y Descargas"
if ! pacman -Qq zenity >/dev/null 2>&1 && ! pacman -Qq kdialog >/dev/null 2>&1; then
  if [[ "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]]; then need kdialog "ventanas de aviso y progreso"
  else need zenity "ventanas de aviso y progreso"; fi
fi
log "faltan: ${PKGS[*]:-nada}"

if [ "${#PKGS[@]}" -gt 0 ]; then
  unavailable=()
  for p in "${PKGS[@]}"; do pacman -Si "$p" >/dev/null 2>&1 || unavailable+=("$p"); done
  if [ "${#unavailable[@]}" -gt 0 ]; then
    if [[ " ${unavailable[*]} " == *" umu-launcher "* ]]; then
      fatal "Falta un repositorio" "El programa umu-launcher está en el repositorio «multilib», que en tu sistema está desactivado.

Para activarlo, abre /etc/pacman.conf como administrador y quita el # de estas dos líneas:

    [multilib]
    Include = /etc/pacman.d/mirrorlist

Después actualiza el sistema (sudo pacman -Syu) y vuelve a ejecutar el instalador."
    fi
    fatal "Software no encontrado" "No encuentro en los repositorios: ${unavailable[*]}

Puede que la lista de paquetes esté desactualizada. Actualiza el sistema (sudo pacman -Syu) y vuelve a ejecutar el instalador."
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
      what="MaLiVe ${VERSION} ya está instalado. Este asistente lo reinstalará."
    else
      what="Este asistente actualizará MaLiVe de la versión ${INSTALLED} a la ${VERSION}."
    fi
    dialog "Bienvenido" "${what}

Tus programas instalados, sus datos y GE-Proton se conservan." "Siguiente" "Cancelar"
  else
  dialog "Bienvenido" "Este asistente instalará MaLiVe ${VERSION} en tu equipo.

Con MaLiVe podrás abrir programas de Windows (.exe y .msi) con un doble clic, igual que en Windows: se instalan, aparecen en el menú de aplicaciones con su icono y se abren desde ahí." \
    "Siguiente" "Cancelar"
  fi
  [ "$CHOICE" -eq 0 ] || cancelled

  if compgen -G "$DATA/proton/GE-Proton*/proton" >/dev/null; then
    dl="GE-Proton ya está descargado: no hace falta descargar nada grande."
  else
    dl="También se descargarán GE-Proton y el entorno de Steam (alrededor de 1 GB)."
  fi
  if [ -n "$INSTALLED" ]; then action="actualización"; else action="instalación"; fi
  if [ "${#PKGS[@]}" -gt 0 ]; then
    list=""
    for i in "${!PKGS[@]}"; do list+="  • ${PKGS[$i]}: ${WHY[$i]}"$'\n'; done
    body="Para funcionar, MaLiVe necesita el siguiente software de los repositorios oficiales de tu sistema:

${list}
${dl}

¿Deseas continuar con la ${action}?"
  else
    body="Todo el software que necesita MaLiVe ya está instalado.

${dl}

¿Deseas continuar con la ${action}?"
  fi
  dialog "Software necesario" "$body" "Continuar" "Cancelar"
  [ "$CHOICE" -eq 0 ] || cancelled
fi

# --- contraseña (solo si hay que instalar paquetes) ---
ELEVATE=() PASSWORD=""
if [ "${#PKGS[@]}" -gt 0 ]; then
  if command -v sudo >/dev/null; then
    ELEVATE=(sudo)
    if ! sudo -n true 2>/dev/null; then
      if [ "$ASSUME_YES" = 1 ]; then
        sudo -v || { say "No se pudo obtener permiso de administrador." >&2; exit 1; }
      else
        tries=0 err=""
        while :; do
          password "Para instalar el software necesario hace falta la contraseña de administrador (la misma con la que inicias sesión)." "$err" || cancelled
          if printf '%s\n' "$PASSWORD" | sudo -S -p '' -v 2>/dev/null; then break; fi
          tries=$((tries + 1))
          [ "$tries" -ge 3 ] && fatal "Contraseña" "No se ha podido validar la contraseña.

Comprueba que es la correcta y que tu usuario tiene permisos de administrador."
          err="Contraseña incorrecta. Inténtalo de nuevo."
        done
      fi
    fi
  elif command -v pkexec >/dev/null; then
    ELEVATE=(pkexec)   # pide la contraseña en una ventana del sistema
  else
    fatal "Sin permisos" "No encuentro sudo ni pkexec para instalar el software necesario."
  fi
fi

# ==========================================================================
# 4. Instalación
# ==========================================================================

step_packages() {
  echo "Instalando ${PKGS[*]}…"
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
    || { echo "Hace falta Python 3.10 o superior"; return 1; }
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
  echo "MaLiVe copiado en $LIB"
}

step_integrate() { "$BIN/malive" integrate && "$BIN/malive" refresh; }
step_setup()     { "$BIN/malive" setup; }

STEP_LABELS=()
[ "${#PKGS[@]}" -gt 0 ] && STEP_LABELS+=("Instalar el software necesario")
STEP_LABELS+=("Copiar MaLiVe" "Asociar los archivos .exe, .msi y .lnk" "Descargar GE-Proton y preparar el entorno")
STEP_FUNCS=()
[ "${#PKGS[@]}" -gt 0 ] && STEP_FUNCS+=(step_packages)
STEP_FUNCS+=(step_copy step_integrate step_setup)
for i in "${!STEP_LABELS[@]}"; do STEP_STATE[$i]=pending; done
LAST=$(( ${#STEP_FUNCS[@]} - 1 ))

if [ "$ASSUME_YES" = 1 ]; then
  for i in "${!STEP_FUNCS[@]}"; do
    say "→ ${STEP_LABELS[$i]}"
    "${STEP_FUNCS[$i]}" 2>&1 | tee -a "$LOG" || { [ "$i" -eq "$LAST" ] || { say "Error. Registro: $LOG" >&2; exit 1; }; }
  done
  say "MaLiVe instalado."
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

Si no se pudo descargar algún paquete, actualiza el sistema (sudo pacman -Syu) y vuelve a ejecutar el instalador."
    fatal "Error en la instalación" "Ha fallado el paso «${STEP_LABELS[$i]}».${hint}"
  fi
done
PASSWORD=""
draw_progress 0 "Terminado."
sleep 0.6

if [ "$SETUP_FAILED" = 1 ]; then
  dialog "Instalación terminada" "MaLiVe se ha instalado, pero no se ha podido preparar GE-Proton (¿sin conexión a Internet?).

No pasa nada: se preparará automáticamente la primera vez que abras un programa de Windows.

Registro: $LOG" "Finalizar"
elif [ -n "$INSTALLED" ] && [ "$INSTALLED" != "$VERSION" ]; then
  dialog "Actualización terminada" "MaLiVe se ha actualizado a la versión ${VERSION}.

Tus programas siguen en el menú de aplicaciones. Ya puedes borrar la carpeta del instalador." "Finalizar"
else
  dialog "Instalación terminada" "MaLiVe se ha instalado correctamente.

Para usarlo, haz doble clic en cualquier archivo .exe o .msi desde tu gestor de archivos. Los programas que instales aparecerán en el menú de aplicaciones." "Finalizar"
fi
exit 0
