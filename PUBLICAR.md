# Rodalies Reclama — publicación gratuita

## Qué es
Una aplicación web progresiva (PWA): se puede abrir como web pública y, desde Chrome en Android, instalar en la pantalla de inicio. No es un APK nativo. No requiere cuentas y no usa servidor de usuarios.

## Publicación gratuita con GitHub Pages
1. Crea una cuenta en GitHub si no tienes una.
2. Crea un repositorio público nuevo, por ejemplo `rodalies-reclama`.
3. Sube **todos** los archivos de esta carpeta a la raíz del repositorio: `index.html`, `manifest.webmanifest`, `sw.js`, `icon.svg` y los documentos.
4. En el repositorio, abre **Settings → Pages**.
5. En **Build and deployment**, elige **Deploy from a branch**, selecciona `main` y `/ (root)`, y guarda.
6. Espera a que GitHub Pages publique la web. La dirección tendrá una forma parecida a `https://TU-USUARIO.github.io/rodalies-reclama/`.
7. Abre esa dirección en Chrome para Android. Menú ⋮ → **Instalar aplicación** o **Añadir a pantalla de inicio**.
8. Prueba el alta de un viaje, adjuntos, generación de reclamación, exportación CSV y copia JSON.

No compartas la dirección del repositorio con claves ni información privada. No subas archivos de usuarios ni copias de seguridad reales al repositorio.

## Cómo funciona la privacidad
Los viajes se guardan en `localStorage` del navegador del dispositivo. La web estática no tiene una base de datos común. Cada usuario y navegador tiene su propio almacenamiento. Si el usuario borra datos del navegador o cambia de móvil, los datos no se transfieren automáticamente. Para transferirlos, usa la copia JSON y guárdala en un lugar privado.

## Limitaciones importantes
- El service worker y la instalación PWA requieren HTTPS; GitHub Pages lo proporciona.
- La pestaña «Datos oficiales» intenta consultar el feed JSON de avisos de Renfe. La petición puede fallar si el navegador bloquea CORS; en ese caso se ofrece el enlace oficial. El feed abarca Cercanías de toda España y el filtro por palabras no identifica automáticamente todas las incidencias de Rodalies. La hora del feed y el inicio del periodo del aviso no deben presentarse como hora original de publicación si Renfe no la proporciona.\n- La versión actual aún no interpreta automáticamente los horarios teóricos GTFS: para evitar asignar horarios equivocados, el usuario debe consultar la web oficial e introducir la hora programada.
- La aplicación no envía reclamaciones ni puede confirmar recepción. Genera el texto y abre el canal oficial para que el usuario lo envíe.
- Los adjuntos se guardan en el navegador con límites variables. La versión limita cada archivo a 1 MB para reducir fallos de almacenamiento.
- La instalación desde Google Play requiere una fase aparte (por ejemplo, empaquetar la PWA con TWA, verificar requisitos actuales de Play Console, preparar ficha e iconos y posiblemente pagar la tarifa de desarrollador). Esta entrega no incluye un APK firmado ni una publicación en Play Store.
- Antes de anunciarla públicamente, prueba la app en varios móviles y navegadores, revisa el texto de privacidad y verifica los enlaces oficiales.

## Coste
Alojamiento estático de GitHub Pages: gratuito en repositorios públicos. Dominio propio: opcional y de pago. Google Play: no necesario para lanzar primero la web instalable.
