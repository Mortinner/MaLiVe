# MaLiVe — Make Linux Versatil

Doble clic en un `.exe` o `.msi` desde el gestor de archivos y se ejecuta o se
instala, como en Windows. Por debajo: **umu-launcher + GE-Proton**, un prefijo
por app y accesos en el menú de aplicaciones con su icono.

Versión **0.1.1** (primera para probar).

## Instalación

1. Descomprime `malive-0.1.1.zip` (clic derecho → Extraer aquí).
2. Entra en la carpeta `malive` y haz doble clic en `install.sh` → **Ejecutar**
   (o en una terminal: `./install.sh`).

Se abre un asistente con ventanas en la terminal (estilo instalador clásico
de Linux; se maneja con ←/→, Intro y Esc): bienvenida, software necesario
("¿Deseas continuar con la instalación?"), contraseña si hace falta instalar
paquetes, progreso y final. Si se cancela, no se toca nada.

- Si se lanza sin terminal (doble clic → Ejecutar), se vuelve a abrir solo en
  la terminal del escritorio (Konsole, Ptyxis/GNOME Console, GNOME Terminal…).
- La contraseña se pide en su propia ventana y se pasa a `sudo`; nunca se
  guarda. Si no hay `sudo`, se usa `pkexec` (ventana del sistema).
- Registro de la instalación: `~/.local/state/malive/install.log`.
- `./install.sh --yes`: sin ventanas ni preguntas (instalación desatendida).
- Necesita una distro basada en Arch (CachyOS, Arch, EndeavourOS, Manjaro) con
  el repositorio `[multilib]` activo (ahí está `umu-launcher`); si no lo está,
  el asistente explica cómo activarlo.
- Para actualizar MaLiVe, vuelve a ejecutar el instalador.

Desinstalar: `./uninstall.sh` (conserva tus apps) o `./uninstall.sh --purge`
(lo borra todo). Los paquetes del sistema no se desinstalan, porque otros
programas pueden usarlos.

## Qué hace al hacer doble clic

| Fichero | Qué pasa |
|---|---|
| Instalador (NSIS, Inno Setup, InstallShield, WiX Burn, 7z SFX…, o con nombre tipo `setup.exe`) | Crea un prefijo para la app (o reutiliza el de la misma app si ya existe), lo instala y crea los `.desktop` a partir de los `.lnk` que deja el instalador |
| `.msi` | Igual, con `msiexec /i … DISABLEADVTSHORTCUTS=1` (para que los accesos apunten al `.exe` real) |
| `.exe` normal fuera de un prefijo | Programa portable: un prefijo por carpeta, que se reutiliza la próxima vez |
| `.exe` o `.lnk` dentro de un prefijo de MaLiVe | Lo ejecuta en ese prefijo |
| Programa de consola | Lo abre con `start /wait /unix …` para que tenga ventana de consola |
| Programa de MS-DOS / 16 bits, `.dll` | Aviso explicando que no se puede |

GE-Proton y el runtime ya se descargan durante la instalación. Al crear el
primer entorno de cada app se ve un diálogo "Preparando…".

Si el instalador no crea accesos directos, MaLiVe busca los `.exe` nuevos y,
si hay varios, pregunta cuáles poner en el menú. Si no se instaló nada
(instalador cancelado) y el prefijo era nuevo, lo borra.

## Decisiones de diseño implementadas

- **Proton fijado por app.** Cada app guarda la versión de GE-Proton con la
  que se creó y siempre arranca con esa. Las versiones nuevas solo se usan para
  apps nuevas; para migrar una app: `malive proton use APP GE-ProtonX-Y`.
  Los Proton viven en `~/.local/share/malive/proton` (ni Steam ni umu los
  borran). Si falta la versión fijada, se vuelve a descargar.
- **Aislamiento (activado por defecto).** Proton recrea `Z: → /` en cada
  arranque si no existe, así que en vez de borrarla MaLiVe la apunta a una
  carpeta vacía. Se comparten Documentos (D:) y Descargas (E:), y la carpeta
  del instalador/portable como Y: si no está ya dentro de esas.
  Desactivar: `malive set isolate off` (global) o `--app ID`.
- **Fin de instalación:** se escanean los accesos al terminar el instalador y
  otra vez cuando ya no queda ningún proceso del prefijo (bootstrappers que se
  relanzan, o la app que el instalador abre al acabar).
- Si el prefijo ya tiene algo en marcha, se lanza con el verbo `run` de Proton
  (en vez de `waitforexitandrun`) para no esperar a que se cierre lo otro.

## Comandos

```
malive list [--size]            apps instaladas
malive remove APP [-y]          borrar app, prefijo y accesos
malive shortcuts APP [--all]    volver a buscar accesos (--all: elegir entre todos los .exe)
malive exec APP winecfg         winecfg, regedit, cmd, un .exe… dentro del prefijo
malive winetricks APP dotnet48  componentes extra
malive proton list|update|use
malive set isolate on|off [--app APP]
malive doctor                   comprobar dependencias e integración
malive setup                    descargar GE-Proton y runtime y probar que Proton arranca
malive integrate | unintegrate
```

## Dónde está cada cosa

| | |
|---|---|
| Prefijos | `~/.local/share/malive/prefixes/<app>/` |
| GE-Proton | `~/.local/share/malive/proton/` |
| Apps registradas | `~/.config/malive/apps/<app>.json` |
| Configuración | `~/.config/malive/config.json` (`isolate`, `shared_folders`, `proton_auto_update`, …) |
| Registro de MaLiVe | `~/.local/state/malive/malive.log` |
| Salida de umu/Proton por app | `~/.local/state/malive/logs/<app>.log` |
| Accesos del menú | `~/.local/share/applications/malive-<app>-<entrada>.desktop` |

## Limitaciones conocidas (0.1)

- **El aislamiento es parcial.** Quitar Z: evita que el programa vea `/` por
  sus unidades, pero Wine aún puede abrir rutas `\\?\unix\…`, y el contenedor
  de umu (pressure-vessel) comparte tu home. Es una mitigación, no un sandbox.
- Sin `binfmt_misc` todavía (ejecutar `.exe` desde la terminal: usa `malive run`).
- Un portable con nombre de instalador (p. ej. `setup-tool.exe` sin firma de
  instalador) se trata como instalador; si no instala nada, su prefijo se borra.
- La categoría del menú es siempre "Utilidades".
- Fuera de alcance: antitrampas de kernel, drivers, DRM agresivo.

## Desarrollo

```fish
python -m venv .venv; and .venv/bin/pip install pytest pylnk3
.venv/bin/python -m pytest -q
```

Los tests usan un `umu-run` falso (`tests/fake_umu_run.py`) que imita la
estructura de prefijo de umu/Proton, y binarios reales de prueba en
`tests/fixtures` (PE con mingw, instalador NSIS con makensis, MSI con wixl;
fuentes incluidas). Para ejecutar desde el repo sin instalar: `bin/malive`,
`bin/malive-run`.
