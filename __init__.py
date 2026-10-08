# LRCLIB Lyrics - Picard 3 plugin
#
# Port to the Picard 3 plugin API (v3) of "LRCLIB Lyrics" by Glicole
# (https://github.com/izaz4141/picard-lrclib), itself based on Dylancyclone's
# plugin. Licensed under the MIT license (see LICENSE).

from __future__ import annotations

from functools import partial
import json
import os
from urllib.parse import urlencode
from urllib.request import (
    Request,
    urlopen,
)

from PyQt6 import (
    QtCore,
    QtGui,
    QtWidgets,
)

from picard.plugin3.api import (
    Album,
    BaseAction,
    File,
    OptionsPage,
    PluginApi,
    Track,
)


PLUGIN_NAME = "LRCLIB Lyrics"

LRCLIB_GET_URL = "https://lrclib.net/api/get"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
USER_AGENT = "picard-plugin-lrclib (https://github.com/izaz4141/picard-lrclib)"

PLUGIN_OPTIONS = {
    "get_on_load": False,
    "get_on_save": False,
    "auto_overwrite": False,
    "save_lrc_file": True,
    "ignore_instrumental": False,
    "plain_as_txt": False,
}

AUDIO_EXTENSIONS = {
    "aac", "ac3", "aif", "aifc", "aiff", "ape", "asf", "dff", "dsf", "eac3",
    "flac", "kar", "m2a", "m4a", "mp3", "mpc", "ofr", "ofs", "oga", "ogg",
    "oggflac", "opus", "spx", "tak", "tta", "wav", "webm", "wma", "wv",
}

# Files currently being re-saved by the "fetch on save" feature, to avoid loops.
files_processing: set[str] = set()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def setting(api: PluginApi, key: str):
    value = api.plugin_config[key]
    return PLUGIN_OPTIONS[key] if value is None else bool(value)


def format_duration(seconds) -> str:
    hours, remainder = divmod(int(seconds or 0), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02}:{secs:02}"
    return f"{minutes}:{secs:02}"


def truncate_text(text: str, max_lines=5, max_chars_per_line=46) -> str:
    lines: list[str] = []
    for i, line in enumerate(text.splitlines()):
        if i >= max_lines:
            lines[-1] = lines[-1].rstrip() + " …"
            break
        if len(line) > max_chars_per_line:
            line = line[: max_chars_per_line - 1].rstrip() + "…"
        lines.append(line)
    return "\n".join(lines)


def parse_duration(time_str: str) -> int:
    parts = time_str.strip().split(":")
    if not parts or not all(p.isdigit() for p in parts):
        raise ValueError(f"Invalid time format: {time_str}")
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total


def metadata_length_seconds(metadata) -> int | None:
    """Return the length in seconds from a Metadata object, or None."""
    raw = metadata.get("~length")
    if raw:
        try:
            return parse_duration(str(raw))
        except ValueError:
            pass
    length_ms = getattr(metadata, "length", 0)
    if length_ms:
        return int(round(length_ms / 1000))
    return None


def get_track_duration(api: PluginApi, track: Track) -> int | None:
    length = metadata_length_seconds(track.metadata)
    if length is None and track.files:
        api.logger.warning(
            'Length not found for track "%s", falling back to file length', track.metadata["title"]
        )
        length = metadata_length_seconds(track.files[0].metadata)
    return length


def main_window(api: PluginApi):
    window = getattr(api.tagger, "window", None)
    if isinstance(window, QtWidgets.QWidget):
        return window
    return QtWidgets.QApplication.activeWindow()


def confirm_replace(parent, title: str, description: str) -> bool:
    try:
        reply = QtWidgets.QMessageBox.question(
            parent,
            title,
            description,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        return reply == QtWidgets.QMessageBox.StandardButton.Yes
    except Exception:
        return False


def fetch_json_blocking(api: PluginApi, url: str, params: dict):
    """Synchronous request, only used for re-searching inside the search dialog."""
    full_url = f"{url}?{urlencode(params)}"
    try:
        req = Request(full_url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                api.logger.error("HTTP error %s for %s", resp.status, full_url)
                return []
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        api.logger.error("Request to %s failed: %s", url, e)
        return []


# --------------------------------------------------------------------------
# Manual search dialog
# --------------------------------------------------------------------------


def show_search_table(api: PluginApi, query: str, response):
    parent = main_window(api)
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Buscar letras en LRCLIB")
    dialog.resize(700, 400)

    layout = QtWidgets.QVBoxLayout(dialog)

    search_layout = QtWidgets.QHBoxLayout()
    search_input = QtWidgets.QLineEdit()
    search_input.setText(query)
    search_input.setPlaceholderText("Escribe tu búsqueda...")
    search_button = QtWidgets.QPushButton("Buscar")
    search_layout.addWidget(search_input)
    search_layout.addWidget(search_button)
    layout.addLayout(search_layout)

    table = QtWidgets.QTableWidget(dialog)
    table.setColumnCount(6)
    table.setHorizontalHeaderLabels(["#", "Canción", "Artista", "Duración", "Álbum", "Sincronizada"])
    table.verticalHeader().setVisible(False)
    hheader = table.horizontalHeader()
    hheader.setDefaultAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignVCenter)
    resize = QtWidgets.QHeaderView.ResizeMode
    hheader.setSectionResizeMode(0, resize.ResizeToContents)
    hheader.setSectionResizeMode(1, resize.Stretch)
    hheader.setSectionResizeMode(2, resize.Interactive)
    hheader.setSectionResizeMode(3, resize.ResizeToContents)
    hheader.setSectionResizeMode(4, resize.Interactive)
    hheader.setSectionResizeMode(5, resize.ResizeToContents)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    layout.addWidget(table)

    button_box = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
    )
    layout.addWidget(button_box)

    results = list(response or [])
    center = QtCore.Qt.AlignmentFlag.AlignCenter

    def populate_table(items):
        table.setSortingEnabled(False)
        table.setRowCount(len(items))
        for row, item in enumerate(items):
            num_item = QtWidgets.QTableWidgetItem()
            num_item.setTextAlignment(center)
            num_item.setData(QtCore.Qt.ItemDataRole.EditRole, row + 1)
            # Keep the index into `results` so sorting doesn't break selection.
            num_item.setData(QtCore.Qt.ItemDataRole.UserRole, row)
            table.setItem(row, 0, num_item)

            has_synced = bool(item.get("syncedLyrics"))
            values = [
                item.get("trackName") or "?",
                item.get("artistName") or "?",
                format_duration(item.get("duration") or 0),
                item.get("albumName") or "?",
                "Sí" if has_synced else "No",
            ]
            for col, val in enumerate(values, start=1):
                cell = QtWidgets.QTableWidgetItem(str(val))
                if col in (3, 5):
                    cell.setTextAlignment(center)
                if col == 5:
                    cell.setForeground(QtGui.QColor("#2ecc71" if has_synced else "#e74c3c"))
                table.setItem(row, col, cell)
        table.setSortingEnabled(True)

    populate_table(results)

    def on_search_clicked():
        nonlocal results
        text = search_input.text().strip()
        if not text:
            return
        new_results = fetch_json_blocking(api, LRCLIB_SEARCH_URL, {"q": text})
        results = new_results if isinstance(new_results, list) else []
        populate_table(results)
        api.logger.debug("Search refreshed: %d results", len(results))

    search_button.clicked.connect(on_search_clicked)
    search_input.returnPressed.connect(on_search_clicked)
    table.doubleClicked.connect(lambda index: index.isValid() and dialog.accept())
    button_box.accepted.connect(dialog.accept)
    button_box.rejected.connect(dialog.reject)
    table.setFocus()

    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None
    row = table.currentRow()
    if row < 0:
        return None
    index = table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)
    if index is None or not (0 <= index < len(results)):
        return None
    return results[index]


# --------------------------------------------------------------------------
# Fetching and saving lyrics
# --------------------------------------------------------------------------


def fetch_lyrics(api: PluginApi, method: str, metadata, linked_files: list[File], length: int | None = None):
    title = metadata["title"]
    if method == "search":
        url = LRCLIB_SEARCH_URL
        queryargs = {"q": title}
    else:
        url = LRCLIB_GET_URL
        queryargs = {
            "track_name": title,
            "artist_name": metadata["artist"],
            "album_name": metadata["album"],
        }
        if length:
            queryargs["duration"] = str(length)

    api.logger.debug("%s %s?%s", "SEARCH" if method == "search" else "GET", url, urlencode(queryargs))
    api.web_service.get_url(
        url=url,
        handler=partial(process_response, api, method, metadata, linked_files),
        parse_response_type="json",
        priority=True,
        important=False,
        unencoded_queryargs=queryargs,
        headers={"User-Agent": USER_AGENT},
    )


def _release_save_guard(method: str, linked_files: list[File]):
    if method == "get_on_save":
        for file in linked_files:
            files_processing.discard(file.filename)


def process_response(api: PluginApi, method: str, metadata, linked_files: list[File], response, reply, error):
    title = metadata["title"]
    artist = metadata["artist"]

    not_found = error or not response or (isinstance(response, dict) and not response.get("id"))
    if not_found:
        api.logger.warning('Lyrics NOT found for track "%s" by %s', title, artist)
        _release_save_guard(method, linked_files)
        return

    changed_files: list[File] = []
    try:
        if method == "search":
            response = show_search_table(api, title, response)
            if response is None:
                return

        is_instrumental = (
            response.get("instrumental", False)
            or "(Instrumental)" in (response.get("trackName") or "")
            or "[au: instrumental]" in (response.get("plainLyrics") or "")
        )
        if is_instrumental and setting(api, "ignore_instrumental") and method != "search":
            api.logger.info('Skipping instrumental track "%s"', title)
            return

        if response.get("syncedLyrics"):
            lyrics = response.get("syncedLyrics")
            is_plain = False
        else:
            lyrics = response.get("plainLyrics")
            is_plain = True
        if not isinstance(lyrics, str) or not lyrics.strip():
            api.logger.warning('Empty lyrics for track "%s" by %s', title, artist)
            return

        save_lrc = setting(api, "save_lrc_file")
        auto_overwrite = setting(api, "auto_overwrite")
        ext = ".txt" if (is_plain and setting(api, "plain_as_txt")) else ".lrc"

        for file in linked_files:
            file_lyrics = lyrics
            full_path = file.filename
            if not full_path:
                continue
            base_path = os.path.splitext(full_path)[0]
            file_lrc = base_path + ext

            has_metadata_lyrics = bool(file.metadata.get("lyrics"))
            has_lrc_file = os.path.exists(file_lrc)

            if has_metadata_lyrics and not has_lrc_file and save_lrc and method != "search":
                # Keep existing tag lyrics, just create the missing sidecar file.
                file_lyrics = file.metadata.get("lyrics")
            elif has_lrc_file and not has_metadata_lyrics and method != "search":
                # Keep existing sidecar file, copy it into the tag.
                with open(file_lrc, "r", encoding="utf-8") as f:
                    file_lyrics = f.read()
            elif (
                ((has_metadata_lyrics and has_lrc_file) or (has_metadata_lyrics and not save_lrc))
                and not auto_overwrite
                and method not in ("get_on_load", "get_on_save")
            ):
                desc = '¿Reemplazar la letra de "{}"?\n\n{}'.format(
                    file.metadata.get("title") or os.path.basename(full_path),
                    truncate_text(lyrics, 5, 42),
                )
                if not confirm_replace(main_window(api), "Reemplazar letra", desc):
                    continue

            file.metadata["lyrics"] = file_lyrics
            changed_files.append(file)

            if save_lrc:
                for old_ext in (".txt", ".lrc"):
                    old_file = base_path + old_ext
                    if os.path.exists(old_file):
                        try:
                            os.remove(old_file)
                        except OSError as e:
                            api.logger.error("Failed to delete %s: %s", old_file, e)
                try:
                    with open(file_lrc, "w", encoding="utf-8") as f:
                        f.write(file_lyrics)
                except OSError as e:
                    api.logger.error("Failed to write %s: %s", file_lrc, e)
                    QtWidgets.QMessageBox.critical(
                        main_window(api),
                        "No se pudo guardar el archivo de letra",
                        f"No se pudo guardar:\n\n{file_lrc}\n\nError: {e}",
                    )

        api.logger.info('Lyrics loaded for track "%s" by %s', title, artist)

    except Exception as e:
        api.logger.error('Lyrics NOT loaded for track "%s" by %s: %s', title, artist, e, exc_info=True)

    finally:
        for file in changed_files:
            try:
                file.update()
            except Exception:
                pass
        if method == "get_on_save":
            for file in linked_files:
                if file in changed_files:
                    # The guard stays set so the post-save hook ignores this second save.
                    file.save()
                else:
                    files_processing.discard(file.filename)


# --------------------------------------------------------------------------
# Processors (automatic fetching)
# --------------------------------------------------------------------------


def on_file_added_to_track(api: PluginApi, track: Track, file: File):
    if not setting(api, "get_on_load"):
        return
    try:
        if not track.files:
            return
        length = get_track_duration(api, track)
        fetch_lyrics(api, "get_on_load", track.metadata, list(track.files), length)
    except Exception as e:
        api.logger.error("Error in get_on_load: %s", e)


def on_file_saved(api: PluginApi, file: File):
    if not setting(api, "get_on_save"):
        return
    if file.filename in files_processing:
        # This is the save we triggered ourselves after adding lyrics.
        files_processing.discard(file.filename)
        return
    try:
        files_processing.add(file.filename)
        length = metadata_length_seconds(file.metadata)
        fetch_lyrics(api, "get_on_save", file.metadata, [file], length)
    except Exception as e:
        api.logger.error("Error in get_on_save: %s", e)
        files_processing.discard(file.filename)


# --------------------------------------------------------------------------
# Context menu actions
# --------------------------------------------------------------------------


def _tracks_from_objects(objs):
    for item in objs:
        if isinstance(item, Track):
            yield item
        elif isinstance(item, Album):
            yield from item.tracks


class LrclibGetLyrics(BaseAction):
    TITLE = "Obtener letras automáticamente (LRCLIB)"

    def callback(self, objs):
        for track in _tracks_from_objects(objs):
            try:
                if not track.files:
                    continue
                length = get_track_duration(self.api, track)
                fetch_lyrics(self.api, "get", track.metadata, list(track.files), length)
            except Exception as e:
                self.api.logger.error("Error fetching lyrics: %s", e)


class LrclibSearchLyrics(BaseAction):
    TITLE = "Buscar letras manualmente (LRCLIB)…"

    def callback(self, objs):
        for track in _tracks_from_objects(objs):
            try:
                if not track.files:
                    continue
                fetch_lyrics(self.api, "search", track.metadata, list(track.files))
            except Exception as e:
                self.api.logger.error("Error searching lyrics: %s", e)


# --------------------------------------------------------------------------
# Options page
# --------------------------------------------------------------------------


class LrclibOptionsPage(OptionsPage):
    NAME = "lrclib_lyrics"
    TITLE = "LRCLIB Lyrics"
    PARENT = "plugins"

    def __init__(self, parent=None):
        super().__init__(parent)
        box = QtWidgets.QVBoxLayout(self)

        self.checkboxes: dict[str, QtWidgets.QCheckBox] = {}
        labels = {
            "get_on_load": "Buscar letras al cargar las pistas (hace más lenta la carga)",
            "get_on_save": "Buscar letras al guardar los archivos",
            "auto_overwrite": "Reemplazar letras existentes sin preguntar",
            "save_lrc_file": "Guardar archivo .lrc junto a cada canción",
            "ignore_instrumental": "Ignorar pistas instrumentales",
            "plain_as_txt": "Guardar letras sin sincronizar como .txt",
        }
        for key, label in labels.items():
            checkbox = QtWidgets.QCheckBox(label, self)
            box.addWidget(checkbox)
            self.checkboxes[key] = checkbox

        box.addSpacing(20)
        cleanup_label = QtWidgets.QLabel("Herramientas de limpieza:", self)
        cleanup_label.setStyleSheet("font-weight: bold;")
        box.addWidget(cleanup_label)

        cleanup_button = QtWidgets.QPushButton("Borrar archivos .lrc huérfanos…", self)
        cleanup_button.setToolTip("Busca en una carpeta archivos .lrc que ya no tienen su canción y los borra")
        cleanup_button.clicked.connect(self.clean_orphaned_lrc_files)
        box.addWidget(cleanup_button)

        box.addStretch(1)
        description = QtWidgets.QLabel(
            "Letras de LRCLIB (https://lrclib.net), base de datos colaborativa.\n"
            "Solo para uso personal; no se permite el uso comercial.",
            self,
        )
        description.setWordWrap(True)
        box.addWidget(description)

    def load(self):
        for key, checkbox in self.checkboxes.items():
            checkbox.setChecked(setting(self.api, key))

    def save(self):
        for key, checkbox in self.checkboxes.items():
            self.api.plugin_config[key] = checkbox.isChecked()

    def clean_orphaned_lrc_files(self):
        root_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Elige la carpeta de tu música",
            "",
            QtWidgets.QFileDialog.Option.ShowDirsOnly | QtWidgets.QFileDialog.Option.DontResolveSymlinks,
        )
        if not root_dir:
            return
        removed = clean_directory(self.api, root_dir)
        if removed:
            message = f"Se borraron {removed} archivo(s) .lrc huérfano(s)."
        else:
            message = "No se encontraron archivos .lrc huérfanos."
        QtWidgets.QMessageBox.information(self, "Limpieza terminada", message)


def clean_directory(api: PluginApi, root_dir: str) -> int:
    if not os.path.isdir(root_dir):
        return 0
    removed = 0
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        stems = {
            os.path.splitext(f)[0]
            for f in filenames
            if os.path.splitext(f)[1][1:].lower() in AUDIO_EXTENSIONS
        }
        for name in filenames:
            stem, ext = os.path.splitext(name)
            if ext.lower() != ".lrc" or stem in stems:
                continue
            path = os.path.join(dirpath, name)
            try:
                os.remove(path)
                removed += 1
                api.logger.debug("Deleted orphaned file: %s", path)
            except OSError as e:
                api.logger.error("Failed to delete %s: %s", path, e)
    return removed


# --------------------------------------------------------------------------
# Plugin entry point
# --------------------------------------------------------------------------


def enable(api: PluginApi):
    for key, default in PLUGIN_OPTIONS.items():
        api.plugin_config.register_option(key, default)

    api.register_file_post_addition_to_track_processor(on_file_added_to_track)
    api.register_file_post_save_processor(on_file_saved)

    api.register_album_action(LrclibGetLyrics)
    api.register_track_action(LrclibGetLyrics)
    api.register_album_action(LrclibSearchLyrics)
    api.register_track_action(LrclibSearchLyrics)

    api.register_options_page(LrclibOptionsPage)
