# LRCLIB Lyrics para Picard 3

Complemento para [MusicBrainz Picard 3](https://picard.musicbrainz.org/) que descarga letras de
[LRCLIB](https://lrclib.net) (sincronizadas cuando existen, si no, en texto plano) y las guarda:

- en la etiqueta `lyrics` de cada archivo de audio, y
- opcionalmente en un archivo `.lrc` junto a cada canción (útil para Jellyfin, Plex, Kodi, foobar2000, etc.).

Es una adaptación al nuevo sistema de complementos de Picard 3 del complemento
[LRCLIB Lyrics de Glicole](https://github.com/izaz4141/picard-lrclib) (para Picard 2),
que a su vez se basa en el de Dylancyclone. Licencia MIT.

## Instalación

1. En Picard 3: **Opciones → Opciones… → Complementos → Install Plugin…**
2. Pestaña **URL**, pega: `https://github.com/Coco-ite/picard-plugin-lrclib`
3. Da clic en **Install…** y confirma.
4. Reinicia Picard.

## Uso

- Clic derecho en un álbum o pista → **Obtener letras automáticamente (LRCLIB)**.
- Clic derecho → **Buscar letras manualmente (LRCLIB)…** para elegir de una lista.
- Después, **Guardar** (Ctrl+S) para escribir la letra dentro de los archivos.

Opciones en **Opciones → Complementos → LRCLIB Lyrics**: buscar al cargar o al guardar,
reemplazar sin preguntar, guardar `.lrc`, ignorar instrumentales, guardar letras sin sincronizar
como `.txt`, y borrar archivos `.lrc` huérfanos.

Las letras de LRCLIB son solo para uso personal.
