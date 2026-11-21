# Cambios

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
