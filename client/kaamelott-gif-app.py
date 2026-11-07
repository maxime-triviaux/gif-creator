#!/usr/bin/env python3
"""Application de bureau : cherche une réplique de Kaamelott et affiche les GIF en grille, via l'API.

Usage :
  python3 kaamelott-gif-app.py

Même configuration que kaamelott-gif-cli.py (fichier .env avec KAAMELOTT_API_KEY), qui doit être
dans le même dossier. Nécessite PySide6 : pip install -r requirements-app.txt
"""
import functools
import importlib.util
import sys
import time
import types
from pathlib import Path

from PySide6.QtCore import (QBuffer, QByteArray, QIODevice, QMimeData, QObject, QPointF, QRunnable,
                            QSize, QStandardPaths, QThreadPool, QTimer, QUrl, Qt, Signal)
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QMovie, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

HERE = Path(__file__).resolve().parent
ICON = HERE / "icon.png"  # shown in the Dock / taskbar; replace it with any square PNG
MAX_RESULTS = 12
THUMB_WIDTH = 240   # small and fast to render: the server only runs 2 ffmpeg at a time
THUMB_FPS = 10
THUMB_HEIGHT = 150
GIF_WORKERS = 2     # matches the API's MAX_FFMPEG: more would only queue on the server
FEEDBACK_MS = 1200  # how long the ✓ / ✕ stays on a button


def load_client():
    """Loads kaamelott-gif-cli.py (its name is not importable) to reuse its config, API calls and clipboard."""
    spec = importlib.util.spec_from_file_location("kaamelott_client", HERE / "kaamelott-gif-cli.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client = load_client()


def gif_params(width=480, fps=12):
    """The argument object client.download_gif expects (same fields as the CLI options)."""
    return types.SimpleNamespace(text=None, before=0.2, after=0.4, width=width, fps=fps)


# --- Background work --------------------------------------------------------------------------

class TaskSignals(QObject):
    done = Signal(object, object, object)  # (task, result, error message)


class Task(QRunnable):
    """Runs `fn` in a worker thread, then calls `callback(task, result, error)` in the UI thread."""

    def __init__(self, fn, callback):
        super().__init__()
        self.setAutoDelete(False)  # kept alive by the window until its signal has been delivered
        self.fn = fn
        self.signals = TaskSignals()
        self.signals.done.connect(callback)

    def run(self):
        try:
            result, error = self.fn(), None
        except client.ApiError as e:
            result, error = None, str(e)
        except Exception as e:  # never let a worker die silently
            result, error = None, f"Erreur inattendue : {e}"
        self.signals.done.emit(self, result, error)


def show_gif(label, data, owner):
    """Plays GIF bytes in a QLabel. The buffer and movie are parented to `owner` to stay alive."""
    buffer = QBuffer(owner)
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.ReadOnly)
    movie = QMovie(buffer, QByteArray(b"gif"), owner)
    label.setMovie(movie)
    movie.start()


def cache_dir():
    """Where copied GIFs are written: the clipboard refers to the file, so it must outlive the copy."""
    path = Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation)) / "gifs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def prune_cache_dir(max_age=86400):
    """Deletes the copied GIFs older than a day: pasting them again is unlikely."""
    now = time.time()
    for f in cache_dir().glob("*.gif"):
        try:
            if now - f.stat().st_mtime > max_age:
                f.unlink()
        except OSError:
            pass


def copy_gif(data, name):
    """Copies the GIF to the clipboard, animated (an image copy would keep one frame only).
    The clipboard refers to a file, written to the app's cache."""
    path = cache_dir() / name
    path.write_bytes(data)
    if sys.platform in ("darwin", "linux") and client.copy_to_clipboard(path):
        return
    mime = QMimeData()  # Windows, or Linux without wl-copy / xclip: the file itself
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    QGuiApplication.clipboard().setMimeData(mime)


def save_gif(parent, data, name):
    """Asks where to save the GIF, in Downloads by default. Returns the file name, or None if cancelled."""
    default = Path.home() / "Downloads" / name
    path, _ = QFileDialog.getSaveFileName(parent, "Enregistrer le GIF", str(default), "GIF (*.gif)")
    if not path:
        return None
    Path(path).write_bytes(data)
    return Path(path).name


# --- Icons ------------------------------------------------------------------------------------

ICON_SIZE = 34


@functools.lru_cache(maxsize=None)
def line_icon(kind):
    """White line icons drawn at runtime (no image files): copy, save, done, error, busy."""
    s, ratio = ICON_SIZE, 3  # drawn at 3x so it stays sharp on Retina screens
    pixmap = QPixmap(s * ratio, s * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("white"), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def line(*points):
        p.drawPolyline(QPolygonF([QPointF(x * s, y * s) for x, y in points]))

    if kind == "copy":  # two sheets: the back one partly hidden by the front one
        p.drawRoundedRect(int(0.36 * s), int(0.34 * s), int(0.44 * s), int(0.52 * s), 3, 3)
        line((0.36, 0.66), (0.20, 0.66), (0.20, 0.14), (0.64, 0.14), (0.64, 0.34))
    elif kind == "save":  # arrow down into a tray
        line((0.50, 0.14), (0.50, 0.62))
        line((0.30, 0.44), (0.50, 0.64), (0.70, 0.44))
        line((0.16, 0.62), (0.16, 0.84), (0.84, 0.84), (0.84, 0.62))
    elif kind == "done":
        line((0.20, 0.52), (0.42, 0.74), (0.80, 0.30))
    elif kind == "error":
        line((0.26, 0.26), (0.74, 0.74))
        line((0.74, 0.26), (0.26, 0.74))
    elif kind == "busy":
        p.setBrush(QColor("white"))
        for x in (0.26, 0.50, 0.74):
            p.drawEllipse(QPointF(x * s, 0.5 * s), 2.6, 2.6)
    p.end()
    return QIcon(pixmap)


# --- Widgets ----------------------------------------------------------------------------------

def caption(result):
    return f"Livre {result['livre']} – ép. {result['numero']} · {client.timecode(result['debut'])}"


HOVER_STYLE = """
QPushButton { background: rgba(0, 0, 0, 0.42); border: none; }
QPushButton:hover { background: rgba(0, 0, 0, 0.66); }
QPushButton#copy { border-right: 1px solid rgba(255, 255, 255, 0.28); }
"""


class Tile(QFrame):
    """One search result: the thumbnail GIF and its text. On hover, the GIF is split in two halves:
    copy on the left, save on the right. A click anywhere else on the tile copies too."""
    copy_requested = Signal(object)  # the tile
    save_requested = Signal(object)

    def __init__(self, result):
        super().__init__()
        self.result = result
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Cliquer pour copier le GIF")
        self.setFixedWidth(THUMB_WIDTH + 16)

        self.image = QLabel("Génération…")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)
        self.image.setStyleSheet("background: rgba(127,127,127,0.15); color: gray;")

        self.actions = QWidget(self.image)
        self.actions.setStyleSheet(HOVER_STYLE)
        self.actions.setGeometry(0, 0, THUMB_WIDTH, THUMB_HEIGHT)
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.copy_button = self.half_button("copy", "Copier le GIF")
        self.save_button = self.half_button("save", "Enregistrer le GIF…")
        row.addWidget(self.copy_button)
        row.addWidget(self.save_button)
        self.actions.hide()
        self.copy_button.clicked.connect(lambda: self.copy_requested.emit(self))
        self.save_button.clicked.connect(lambda: self.save_requested.emit(self))

        info = QLabel(caption(result))
        info.setStyleSheet("color: gray; font-size: 11px;")
        text = QLabel(result["texte"])
        text.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.image)
        layout.addWidget(info)
        layout.addWidget(text)
        layout.addStretch()

    @staticmethod
    def half_button(kind, tooltip):
        button = QPushButton()
        button.setObjectName(kind)
        button.setIcon(line_icon(kind))
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setToolTip(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return button

    def set_gif(self, data):
        self.image.setText("")
        show_gif(self.image, data, self)

    def set_error(self, message):
        self.image.setText("GIF indisponible")
        self.image.setToolTip(message)

    def enterEvent(self, event):
        self.actions.show()
        self.actions.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.busy():  # keep the feedback visible until the action ends
            self.actions.hide()
        super().leaveEvent(event)

    def busy(self):
        return not self.copy_button.isEnabled()

    def set_busy(self, button):
        """Shows the action in progress on its half; the other one waits too."""
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.actions.show()
        button.setIcon(line_icon("busy"))

    def done(self, button, outcome):
        """Ends the action: `outcome` is "done" or "error" (shown briefly), or None (cancelled)."""
        self.copy_button.setEnabled(True)
        self.save_button.setEnabled(True)
        if outcome:
            button.setIcon(line_icon(outcome))
            QTimer.singleShot(FEEDBACK_MS, self, self.reset)
        else:
            self.reset()

    def reset(self):
        self.copy_button.setIcon(line_icon("copy"))
        self.save_button.setIcon(line_icon("save"))
        if not self.underMouse():
            self.actions.hide()

    def mouseReleaseEvent(self, event):
        # Releases on the two halves are taken by the buttons: this is a click elsewhere on the tile
        if event.button() == Qt.LeftButton and not self.busy():
            self.copy_requested.emit(self)


class MainWindow(QMainWindow):
    def __init__(self, url, key):
        super().__init__()
        self.url, self.key = url, key
        self.tiles, self.tasks, self.generation = [], set(), 0
        self.gif_pool = QThreadPool(self)
        self.gif_pool.setMaxThreadCount(GIF_WORKERS)
        self.setWindowTitle("Kaamelott GIF")
        self.resize(860, 700)

        self.query = QLineEdit()
        self.query.setPlaceholderText("Cherche une réplique… (ex. c'est pas faux)")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.search)
        button = QPushButton("Chercher")
        button.clicked.connect(self.search)
        self.status = QLabel("Tape une réplique puis Entrée.")
        self.status.setStyleSheet("color: gray;")

        self.grid_widget = QWidget()
        self.grid = QGridLayout()
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.grid.setSpacing(12)
        outer = QVBoxLayout(self.grid_widget)
        outer.addLayout(self.grid)
        outer.addStretch()  # takes the spare height, so a single row of tiles is not stretched
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.grid_widget)

        bar = QHBoxLayout()
        bar.addWidget(self.query, 1)
        bar.addWidget(button)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(bar)
        layout.addWidget(self.status)
        layout.addWidget(self.scroll, 1)
        self.setCentralWidget(central)

    def run(self, task, pool=None):
        self.tasks.add(task)
        (pool or QThreadPool.globalInstance()).start(task)

    def search(self):
        query = self.query.text().strip()
        if not query:
            return
        self.generation += 1  # results and GIFs of an older search are ignored
        generation = self.generation
        self.gif_pool.clear()  # drops the thumbnails still waiting for a worker
        self.status.setText("Recherche…")
        self.run(Task(lambda: client.search(self.url, self.key, query, MAX_RESULTS),
                      lambda task, res, err: self.found(task, generation, res, err)))

    def found(self, task, generation, results, error):
        self.tasks.discard(task)
        if generation != self.generation:
            return
        self.clear_tiles()
        if error:
            self.status.setText(error)
            return
        self.status.setText(f"{len(results)} résultat(s) · survole un GIF pour le copier ou l'enregistrer"
                            if results else "Aucun résultat.")
        for result in results:
            tile = Tile(result)
            tile.copy_requested.connect(self.copy)
            tile.save_requested.connect(self.save)
            self.tiles.append(tile)
            params = gif_params(width=THUMB_WIDTH, fps=THUMB_FPS)
            self.run(Task(lambda r=result: client.download_gif(self.url, self.key, r, params),
                          lambda task, res, err, t=tile: self.thumbnail(task, generation, t, res, err)),
                     self.gif_pool)
        self.layout_tiles()

    def thumbnail(self, task, generation, tile, response, error):
        self.tasks.discard(task)
        if generation != self.generation:
            return  # the tile was removed by a newer search
        if error:
            tile.set_error(error)
        else:
            tile.set_gif(response[0])

    def clear_tiles(self):
        for tile in self.tiles:
            tile.deleteLater()
        self.tiles = []

    def layout_tiles(self):
        columns = max(1, (self.scroll.viewport().width() - 12) // (THUMB_WIDTH + 16 + 12))
        for i, tile in enumerate(self.tiles):
            self.grid.addWidget(tile, i // columns, i % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.layout_tiles()

    # Copy and save act on the full-size GIF (default settings, like the CLI), not the thumbnail

    def fetch_full_gif(self, tile, button, then):
        tile.set_busy(button)
        result = tile.result
        self.run(Task(lambda: client.download_gif(self.url, self.key, result, gif_params()),
                      lambda task, res, err: self.full_gif_ready(task, tile, button, res, err, then)))

    def full_gif_ready(self, task, tile, button, response, error, then):
        self.tasks.discard(task)
        alive = tile in self.tiles  # a newer search may have removed the tile meanwhile
        if error:
            self.status.setText(error)
            if alive:
                tile.done(button, "error")
            return
        data, headers = response
        outcome = then(data, client.file_name(headers, tile.result))
        if alive:
            tile.done(button, outcome)

    def copy(self, tile):
        def copy_data(data, name):
            copy_gif(data, name)
            self.status.setText(f"Copié dans le presse-papier : {name}")
            return "done"
        self.fetch_full_gif(tile, tile.copy_button, copy_data)

    def save(self, tile):
        def save_data(data, name):
            saved = save_gif(self, data, name)
            if saved:
                self.status.setText(f"Enregistré : {saved}")
            return "done" if saved else None
        self.fetch_full_gif(tile, tile.save_button, save_data)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Kaamelott GIF")
    if ICON.is_file():
        app.setWindowIcon(QIcon(str(ICON)))  # on macOS, this also replaces the Python icon in the Dock
    try:
        url, key = client.load_config()
    except SystemExit as e:  # the CLI's config errors exit with a message
        QMessageBox.critical(None, "Kaamelott GIF", str(e.code))
        return 1
    prune_cache_dir()
    window = MainWindow(url, key)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
