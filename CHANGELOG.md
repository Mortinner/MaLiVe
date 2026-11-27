# Cambios

## 0.1.17
- Los ajustes conocidos de un programa se aplican también a su instalador.
  Si al terminar se marca «Ejecutar Format Factory», el programa lo abre el
  instalador (no MaLiVe) y heredaba su entorno sin el ajuste: se quedaba en
  bucle sin ventana.

## 0.1.16
- **Format Factory arranca.** Con el motor de voz de Wine (SAPI) se quedaba
  en bucle al abrir, sin ventana. MaLiVe lleva ahora una lista de ajustes
  conocidos por programa y, para Format Factory, desactiva `sapi.dll`.
- **Ajustes por programa:** `malive dll APP sapi=d` (DLL desactivadas o
  cambiadas) y `malive env APP VAR=VALOR` (variables al lanzar, p. ej.
  `PROTON_ENABLE_NVAPI=1`). Se guardan en la app y se aplican siempre, también
  desde el menú; mandan sobre los ajustes conocidos.

## 0.1.15
- **Discos de datos y NAS como unidades de Windows.** Con el aislamiento, los
  programas solo veían C:, Documentos (D:) y Descargas (E:); un NAS o un
  segundo disco no aparecían en sus diálogos de abrir y guardar. Ahora MaLiVe
  da letra fija (M:, N:…) a los montajes de datos del equipo: NAS por
  CIFS/NFS (también con autofs), otros discos y USB en `/run/media`. Todo lo
  del sistema queda fuera. Las letras se recuerdan para que no cambien al
  conectar otro disco. Se puede desactivar con `malive set host-drives off`.
- Un fichero abierto desde uno de esos discos llega al programa por su unidad
  fija (`N:\Multimedia\…`) en vez de como `Y:`.

## 0.1.14
- **Complementos dentro de un programa.** Los instaladores de códecs,
  plugins o bibliotecas (LAV Filters, K-Lite, Visual C++, .NET, DirectX…) ya
  no se instalan como programa nuevo, sino dentro del programa que los
  necesita. Si no hay duda (un solo candidato abierto, o un único
  reproductor para los códecs) va directo; si hay varios, MaLiVe pregunta
  con el último usado marcado. Con un instalador desconocido no adivina:
  solo pregunta si hay algún programa de MaLiVe abierto.
- Nueva opción en **Abrir con** de los `.exe`/`.msi`: «MaLiVe: instalar
  dentro de un programa…», y `malive-run --into APP fichero.exe`.
- MaLiVe recuerda cuándo se usó cada programa por última vez.

## 0.1.13
- **Ficheros en un NAS o en otro disco.** Al abrir un fichero que está fuera
  de la carpeta personal (un NAS montado en `/mnt`, un disco en
  `/run/media`…), el programa recibía su ruta (`Y:\…`) pero no podía leerlo:
  el contenedor en el que corre Proton no veía esa carpeta. Ahora MaLiVe se
  la expone (`PRESSURE_VESSEL_FILESYSTEMS_RW`).
- **Sección del menú según el programa.** Ya no va todo a Utilidades: los
  reproductores de vídeo o audio van a Multimedia, los visores de imágenes a
  Gráficos, los compresores a Utilidades › Archivado y los editores de texto a
  Utilidades › Editor de texto, según los tipos de fichero que abre cada
  programa. Los accesos existentes se recolocan al actualizar.

## 0.1.12
- **«Abrir con».** Los programas instalados con MaLiVe aceptan ficheros: salen
  en «Abrir con» del gestor de archivos y se les puede arrastrar un fichero al
  icono. MaLiVe traduce la ruta de Linux a la de Windows
  (`~/Documentos/nota.txt` → `D:\nota.txt`); si el fichero está fuera de las
  carpetas compartidas, su carpeta se monta como `Y:`.
- Los tipos de fichero de cada programa se leen del registro de Windows de su
  entorno (las asociaciones que hizo su instalador) y se traducen a tipos de
  Linux: así el programa aparece directamente en la lista corta de «Abrir
  con» para esos ficheros.

## 0.1.11
- Arreglado de verdad (causa comprobada a mano en Plasma 6): crear o borrar
  un acceso y reconstruir el menú en el mismo instante no hace que Plasma
  lo redibuje, ni siquiera desde una terminal; repetirlo unos segundos
  después, sí. MaLiVe reconstruye al momento y programa dos
  reconstrucciones más, a los 5 y a los 30 segundos, con un temporizador
  de systemd que sobrevive al cierre de MaLiVe.

## 0.1.10
- Arreglado (tercer intento, esta vez con la causa confirmada en el código):
  los programas nuevos no aparecían en las categorías ni en «Todas las
  aplicaciones» de Plasma. A MaLiVe, lanzado desde Dolphin o desde el menú,
  le faltaba `XDG_MENU_PREFIX=plasma-`, así que `kbuildsycoca6` construía el
  menú con el fichero genérico en vez de `plasma-applications.menu`. Ahora
  reconstruye con el entorno completo de Plasma, igual que desde una
  terminal de la sesión.
- El registro anota el idioma, `XDG_MENU_PREFIX` y el escritorio usados.

## 0.1.9
- **MaLiVe en inglés.** Los diálogos, avisos, errores y comandos de MaLiVe
  salen en español si el sistema está en español y en inglés en cualquier
  otro caso, como ya hacía el asistente de instalación. Si MaLiVe se abre
  desde el menú o el temporizador sin el idioma de la sesión, lo toma del
  propio Plasma o GNOME. `MALIVE_LANG=es|en` lo fuerza.
- Los accesos del menú llevan sus textos en los dos idiomas (`Name[es]=…`).
- Las actualizaciones de MaLiVe se buscan **cada 3 horas** en vez de una vez
  al día. GE-Proton se sigue comprobando una vez por semana.

## 0.1.8
- Arreglado de verdad el menú de Plasma que no se actualizaba al instalar o
  desinstalar. Cuando MaLiVe se abre desde el menú (p. ej. «Desinstalar») o
  desde el temporizador, puede no recibir el idioma de la sesión, y la base
  de datos de programas que reconstruía era otra (`ksycoca6_en-POSIX_…`) en
  vez de la que lee Plasma (`ksycoca6_es_…`). Ahora usa el idioma y las
  carpetas del propio Plasma (o GNOME Shell) que está en marcha.
- El registro anota con qué idioma se reconstruyó el menú.

## 0.1.7
- Arreglado: en Plasma, los programas recién instalados no aparecían en
  «Todas las aplicaciones» (sí en el buscador) y los desinstalados seguían
  en la lista. MaLiVe ahora reconstruye por completo la base de datos de
  programas del escritorio (`kbuildsycoca6 --noincremental`), que es lo que
  hace que el menú se redibuje.

## 0.1.6
- Los programas que se abren solos al terminar la instalación («Ejecutar
  Notepad++») ya no retrasan su acceso: MaLiVe vigila el menú Inicio de
  Windows mientras dura la instalación y crea cada acceso en cuanto aparece.
- Arreglado: Notepad++ (y otros) no se abría desde el menú por un carácter
  nulo en los argumentos de su acceso directo («embedded null byte»). También
  se corrige en las apps ya instaladas.
- El registro anota cómo se ha refrescado el menú del escritorio, para
  diagnosticar los casos en que Plasma no muestra un programa nuevo.
- El asistente de instalación habla español o inglés según el idioma del
  sistema (inglés para cualquier idioma que no sea español).
- Si el idioma del sistema no está bien configurado, las ventanas del
  asistente ya no se descuadran.
- Capturas del asistente en el README (inglés y español).

## 0.1.5
- **Actualizaciones automáticas.** MaLiVe busca cada día una versión nueva en
  GitHub, la descarga, comprueba su SHA-256, la prueba y la instala sola; si
  algo falla, vuelve a la anterior. Después avisa con una notificación. Si la
  versión nueva necesita instalar software del sistema, avisa y ofrece abrir
  el asistente. Se desactiva con `malive set auto-update off`.
- Nuevos comandos: `malive update` (actualizar ahora) y `malive update --check`.
- El temporizador semanal de GE-Proton pasa a ser uno diario (`malive-auto`)
  que se encarga de MaLiVe y, una vez por semana, de GE-Proton.
- Las notificaciones ya no se pierden cuando MaLiVe termina justo después.
- Licencia GPL-3.0.
- README en inglés (`README.md`) y en español (`README.es.md`).

## 0.1.4
- El menú de Plasma se actualiza al instalar o desinstalar (antes podían
  quedar entradas de programas ya desinstalados).
- Los nombres de los programas se guardan sin versión ni arquitectura
  («PuTTY release 0.85 (64-bit)» → «PuTTY»), así que instalar una versión
  nueva reutiliza el mismo entorno.
- El asistente detecta si es una actualización («de la 0.1.3 a la 0.1.4»).
- El zip se extrae en una carpeta con la versión (`malive-0.1.4`).

## 0.1.3
- «Desinstalar» en el clic derecho de cada programa en el menú.
- `malive refresh` regenera los accesos del menú.

## 0.1.2
- Asistente de instalación con ventanas en la terminal; si se abre con doble
  clic sin terminal, se abre solo en una.

## 0.1.1
- El instalador detecta e instala el software que falta (con permiso).
- `malive setup` deja GE-Proton y el runtime preparados.

## 0.1.0
- Primera versión: doble clic en .exe, .msi y .lnk; un entorno por
  programa con GE-Proton fijado; accesos en el menú con icono; aislamiento
  de la unidad Z:.
