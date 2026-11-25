"""Read-only windows for GX meshes, textures, animations and media."""

from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from PIL import Image

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from .asset_models import (
    ASSET_EXTENSIONS, FONT_EXTENSIONS, IMAGE_EXTENSIONS, MODEL_EXTENSIONS, cloth_texture_paths,
    effect_components, open_model,
)
from .catalog import decode_text
from .client_model_data import EMBLEM_FILTER, EmblemCatalog, emblem_texture_directory
from .equipment_models import ModelAssets, basename, dds_name
from .equipment_preview import AnimatedPreview

ASSET_FILTER = ("GX assets (" + " ".join(f"*{ext} *{ext.upper()}" for ext in sorted(ASSET_EXTENSIONS))
                + f" {EMBLEM_FILTER})")


@dataclass(frozen=True, slots=True)
class ImageData:
    pixels: bytes
    width: int
    height: int
    description: str


def read_image(source):
    with Image.open(source) as image:
        width, height = image.size
        if width * height > 32_000_000:
            raise ValueError("The image exceeds 32 million pixels.")
        label = f"{width:,} × {height:,}  ·  {image.format}  ·  {image.mode}"
        return ImageData(image.convert("RGBA").tobytes(), width, height, label)


def read_document(source, workspace):
    ext = source.suffix.casefold()
    if folder := emblem_texture_directory(source):
        assets = ModelAssets(source.parent)
        return "textures", tuple(
            (f"{index} · {name}", assets.resolve(f"{folder}/{dds_name(name)}"))
            for index, row in enumerate(EmblemCatalog.open(source).records, 1)
            if (name := decode_text(row))
        )
    if ext in MODEL_EXTENSIONS:
        return "model", open_model(source, workspace)
    if ext in IMAGE_EXTENSIONS:
        return "image", read_image(source)
    if ext == ".ctl":
        return "textures", cloth_texture_paths(source, workspace)
    if ext in FONT_EXTENSIONS:
        return "font", source
    if ext == ".ogg":
        if not source.is_file():
            raise FileNotFoundError(source.name)
        return "audio", source
    raise ValueError("This asset format has no viewer.")


class AssetPreview(AnimatedPreview):
    def __init__(self, parent):
        super().__init__(580, 440, parent)
        self.kind = "armor"
        self.playing = True
        self.paused_at = 0
        self.zoom = 1
        self.pane.enable_resizing()
        self.pane.viewport_changed.connect(self.queue)
        layout = self.layout()
        layout.setAlignment(self.pane, Qt.AlignmentFlag(0))
        layout.setStretchFactor(self.pane, 1)
        layout.takeAt(layout.count() - 1)

    def show_scene(self, scene, kind):
        self.stop()
        self.choice = scene
        self.kind = kind
        self.model_revision += 1
        self.turn = 0
        self.playing = True
        self.paused_at = 0
        self.restart_effects()

    def set_playing(self, playing):
        now = monotonic()
        if playing:
            self.started = now - self.paused_at
        else:
            self.paused_at = now - self.started
        self.playing = playing
        self.queue()

    def effect_options(self):
        return self.playing, self.paused_at, self.zoom

    def render_size(self):
        size = self.pane.dimensions
        maximum = 560 if self.kind == "dungeon" else 680 if self.kind == "world" else 860
        if self.choice.eft and len(self.choice.eft[1]) > 32:
            maximum = min(maximum, 460)
        if max(size.width(), size.height()) > maximum:
            size = size.scaled(maximum, maximum, Qt.AspectRatioMode.KeepAspectRatio)
        return size.width(), size.height()


class ImageCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.pixmap = QPixmap()
        self.size_to_draw = QSize()

    def paintEvent(self, event):
        if self.pixmap.isNull():
            return
        painter = QPainter(self)
        width, height = self.size_to_draw.width(), self.size_to_draw.height()
        exposed = event.rect()
        step = 12
        first_x, first_y = exposed.left() // step, exposed.top() // step
        end_x = min(exposed.right(), width - 1) // step
        end_y = min(exposed.bottom(), height - 1) // step
        colors = (QColor("#dedede"), QColor("#fafafa"))
        for y in range(first_y, end_y + 1):
            for x in range(first_x, end_x + 1):
                painter.fillRect(x * step, y * step, min(step, width - x * step),
                                 min(step, height - y * step), colors[(x + y) % 2])
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(0, 0, width, height, self.pixmap)


class ImageView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.canvas = ImageCanvas()
        self.setWidget(self.canvas)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom = None

    def set_image(self, data):
        self.canvas.pixmap = QPixmap()
        if data is not None:
            image = QImage(data.pixels, data.width, data.height, data.width * 4, QImage.Format.Format_RGBA8888)
            self.canvas.pixmap = QPixmap.fromImage(image)
        self.fit_image()

    def set_zoom(self, zoom):
        self.zoom = zoom
        self.fit_image()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_image()

    def fit_image(self):
        size = self.canvas.pixmap.size()
        if not size.isEmpty():
            if self.zoom is None:
                room = self.viewport().size() - QSize(16, 16)
                if room.isValid() and (size.width() > room.width() or size.height() > room.height()):
                    size = size.scaled(room, Qt.AspectRatioMode.KeepAspectRatio)
            else:
                size = size * self.zoom
        self.canvas.size_to_draw = size
        self.canvas.resize(size)
        self.canvas.update()


def duration_label(milliseconds):
    seconds = max(0, milliseconds) // 1000
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


class AudioView(QWidget):
    def __init__(self, settings):
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        super().__init__()
        self.settings = settings
        self.output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self.play_button = QPushButton("Play")
        self.stop_button = QPushButton("Stop")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setEnabled(False)
        self.clock = QLabel("0:00 / 0:00")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(settings.value("asset/volume", 70, type=int))
        self.output.setVolume(self.volume.value() / 100)
        self.volume.setAccessibleName("Volume")
        self.seek.setAccessibleName("Playback Position")
        self.issue = QLabel()
        self.issue.setWordWrap(True)
        self.issue.setTextFormat(Qt.TextFormat.PlainText)

        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(self.seek)
        transport = QHBoxLayout()
        for child in (self.play_button, self.stop_button, self.clock):
            transport.addWidget(child)
        transport.addStretch(1)
        transport.addWidget(QLabel("Volume"))
        transport.addWidget(self.volume, 1)
        layout.addLayout(transport)
        layout.addWidget(self.issue)
        layout.addStretch()

        self.play_button.clicked.connect(self.toggle)
        self.stop_button.clicked.connect(self.player.stop)
        self.seek.sliderReleased.connect(lambda: self.player.setPosition(self.seek.value()))
        self.seek.actionTriggered.connect(self.seek_action)
        self.volume.valueChanged.connect(self.set_volume)
        self.player.playbackStateChanged.connect(self.state)
        self.player.positionChanged.connect(self.update_clock)
        self.player.durationChanged.connect(self.set_duration)
        self.player.seekableChanged.connect(self.seek.setEnabled)
        self.player.errorOccurred.connect(lambda _, message: self.issue.setText(message))

    def load(self, source):
        self.issue.clear()
        self.player.setSource(QUrl.fromLocalFile(str(source)))

    def clear(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.issue.clear()

    def toggle(self):
        if self.player.isPlaying():
            self.player.pause()
        else:
            self.player.play()

    def state(self, *_):
        self.play_button.setText("Pause" if self.player.isPlaying() else "Play")

    def seek_action(self, _):
        if not self.seek.isSliderDown():
            self.player.setPosition(self.seek.sliderPosition())

    def set_duration(self, duration):
        self.seek.setRange(0, duration)
        self.update_clock(self.player.position())

    def update_clock(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.clock.setText(f"{duration_label(position)} / {duration_label(self.player.duration())}")

    def set_volume(self, value):
        self.output.setVolume(value / 100)
        self.settings.setValue("asset/volume", value)


class AssetWindow(QMainWindow):
    def __init__(self, studio):
        super().__init__(studio, Qt.WindowType.Window)
        self.setWindowTitle("Asset Viewer")
        self.resize(740, 545)
        self.settings = studio.settings
        self.source = None
        self.variants = ()
        self.component_choices = ()
        self.mode = ""
        self.font_id = -1
        self.audio = None
        self.menuBar().addMenu("&File").addAction("&Close", "Ctrl+W", self.close)
        body = QWidget()
        layout = QVBoxLayout(body)
        controls = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.setAccessibleName("Preview Selection")
        self.selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.selector.currentIndexChanged.connect(self.select_variant)
        controls.addWidget(self.selector, 1)
        self.components = QComboBox()
        self.components.setAccessibleName("Effect Component")
        self.components.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.components.currentIndexChanged.connect(self.select_component)
        controls.addWidget(self.components, 1)
        self.animation = QPushButton("Pause", checkable=True, checked=True)
        self.animation.toggled.connect(self.toggle_animation)
        controls.addWidget(self.animation)
        self.zoom = QComboBox()
        self.zoom.setAccessibleName("Zoom")
        self.zoom.addItem("Fit", None)
        for scale in (.25, .5, 1, 2, 4):
            self.zoom.addItem(f"{scale * 100:.0f}%", scale)
        self.zoom.currentIndexChanged.connect(self.set_zoom)
        controls.addWidget(self.zoom)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.model = AssetPreview(self)
        self.images = ImageView()
        self.font_sample = QLabel(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ\nabcdefghijklmnopqrstuvwxyz\n0123456789  ÁÉÍÓÚ  Ññ  ¿¡  €\n"
            "The quick brown fox jumps over the lazy dog."
        )
        self.font_sample.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.font_sample.setWordWrap(True)
        for widget in (self.model, self.images, self.font_sample):
            self.pages.addWidget(widget)
        self.setCentralWidget(body)

    def load(self, source, document):
        self.clear()
        self.source = source
        self.mode, data = document
        self.setWindowTitle(source.name + " · Asset Viewer")
        self.zoom.setVisible(self.mode in ("image", "textures", "model"))
        with QSignalBlocker(self.zoom):
            self.zoom.setCurrentIndex(0)
        self.set_zoom()
        if self.mode == "model":
            self.pages.setCurrentWidget(self.model)
            self.model.renderer, self.variants = data.renderer, data.variants
        elif self.mode == "textures":
            self.pages.setCurrentWidget(self.images)
            self.variants = data
        elif self.mode == "image":
            self.pages.setCurrentWidget(self.images)
            self.images.set_image(data)
            self.statusBar().showMessage(data.description)
        elif self.mode == "font":
            self.pages.setCurrentWidget(self.font_sample)
            self.font_id = QFontDatabase.addApplicationFont(str(source))
            families = QFontDatabase.applicationFontFamilies(self.font_id)
            if families:
                self.font_sample.setFont(QFont(families[0], 21))
                self.statusBar().showMessage(" · ".join(families))
            else:
                self.statusBar().showMessage("The font could not be opened.")
        elif self.mode == "audio":
            if self.audio is None:
                try:
                    self.audio = AudioView(self.settings)
                except ImportError:
                    self.statusBar().showMessage("The Qt multimedia module is unavailable.")
                else:
                    self.pages.addWidget(self.audio)
            if self.audio is not None:
                self.pages.setCurrentWidget(self.audio)
                self.audio.load(source)
                self.statusBar().showMessage("Ogg audio")
        with QSignalBlocker(self.selector):
            self.selector.clear()
            self.selector.addItems([name for name, _ in self.variants])
            index = 0
            if self.mode == "model" and source.suffix.casefold() == ".seff":
                index = next((i for i, (_, scene) in enumerate(self.variants)
                              if any(layer.supported() and data.renderer.assets.resolve(f"Effect/DDS/{basename(layer.texture)}")
                                     for _, layer in scene.seff)), 0)
                self.selector.setCurrentIndex(index)
        self.selector.setVisible(len(self.variants) > 1)
        if self.variants:
            self.select_variant(index)

    def select_variant(self, index):
        if not 0 <= index < len(self.variants):
            return
        _, selected = self.variants[index]
        if self.mode == "model":
            self.component_choices = effect_components(selected)
            with QSignalBlocker(self.components):
                self.components.clear()
                self.components.addItems([label for label, _ in self.component_choices])
            self.components.setVisible(bool(self.component_choices))
            self.show_model_scene(selected)
        elif self.mode == "textures":
            try:
                image = read_image(Path(selected)) if selected else None
                self.statusBar().showMessage(image.description if image else "Referenced texture is unavailable.")
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                image = None
                self.statusBar().showMessage(str(error))
            self.images.set_image(image)

    def select_component(self, index):
        if 0 <= index < len(self.component_choices):
            self.show_model_scene(self.component_choices[index][1])

    def show_model_scene(self, scene):
        ext = self.source.suffix.casefold()
        kind = {".3do": "weapon", ".pc": "cape", ".dg": "dungeon", ".wtr": "water",
                ".smod": "world", ".vani": "world", ".mani": "world"}.get(ext, "armor")
        parents = {part.casefold() for part in self.source.parts}
        if ext == ".3dc" and parents & {"monster", "npc", "vehicle"}:
            kind = "mount" if "vehicle" in parents else "npc"
        self.model.show_scene(scene, kind)
        with QSignalBlocker(self.animation):
            self.animation.setChecked(True)
            self.animation.setText("Pause")
        self.animation.setVisible(scene.animated)
        self.statusBar().showMessage(scene.information)

    def toggle_animation(self, playing):
        self.animation.setText("Pause" if playing else "Play")
        self.model.set_playing(playing)

    def set_zoom(self, *_):
        zoom = self.zoom.currentData()
        if self.mode == "model":
            self.model.zoom = zoom or 1
            self.model.queue()
        else:
            self.images.set_zoom(zoom)

    def clear(self):
        self.source, self.mode, self.variants = None, "", ()
        self.component_choices = ()
        self.components.hide()
        self.animation.hide()
        self.model.stop()
        self.model.choice = self.model.renderer = None
        self.model.pane.clear()
        self.images.set_image(None)
        if self.audio is not None:
            self.audio.clear()
        if self.font_id >= 0:
            QFontDatabase.removeApplicationFont(self.font_id)
            self.font_sample.setFont(QFont())
            self.font_id = -1
        self.statusBar().clearMessage()

    def closeEvent(self, event):
        self.settings.setValue("windows/assetSize", self.size())
        self.clear()
        super().closeEvent(event)
