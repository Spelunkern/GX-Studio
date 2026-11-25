from threading import Event
from time import monotonic

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage, QIntValidator, QMouseEvent, QPixmap
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .equipment_models import ARMOR_TYPES, GLOW_NAMES, HUMAN, WEAPON_TYPES, Body, ModelAssets, ModelChoice, eligible_bodies
from .model_render import PreviewRenderer


def model_value(combo: QComboBox) -> str:
    value = combo.currentData()
    return combo.currentText() if combo.isEditable() or value is None else str(value)


class PreviewPane(QLabel):
    turned = Signal(float)
    dragging_changed = Signal(bool)
    viewport_changed = Signal()

    def __init__(self, width: int, height: int, parent: QWidget):
        super().__init__(parent)
        self.dimensions = QSize(width, height)
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.drag_x: float | None = None
        self.adaptive = False

    def enable_resizing(self):
        self.adaptive = True
        self.setMinimumSize(200, 150)
        self.setMaximumSize(16777215, 16777215)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.adaptive and event.size() != self.dimensions:
            self.dimensions = event.size()
            self.viewport_changed.emit()

    def display(self, result: bytes | str, resolution: tuple[int, int] | None = None):
        if isinstance(result, str):
            self.setText(result)
            return
        width, height = resolution or (self.dimensions.width(), self.dimensions.height())
        image = QImage(result, width, height, width * 3, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image)
        if pixmap.size() != self.dimensions:
            pixmap = pixmap.scaled(self.dimensions, Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(pixmap)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_x = event.position().x()
            self.dragging_changed.emit(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.drag_x is not None:
            position = event.position().x()
            self.turned.emit(position - self.drag_x)
            self.drag_x = position
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_x is not None:
            self.drag_x = None
            self.dragging_changed.emit(False)
        super().mouseReleaseEvent(event)


class RenderSignals(QObject):
    finished = Signal(int, object)


class RenderJob(QRunnable):
    def __init__(self, renderer, kind, requests, turn, generation):
        super().__init__()
        self.renderer = renderer
        self.kind = kind
        self.requests = requests
        self.turn = turn
        self.generation = generation
        self.cancelled = Event()
        self.signals = RenderSignals()

    def run(self):
        results = []
        for choice, width, height in self.requests:
            if self.cancelled.is_set():
                return
            if choice is None:
                results.append("No 3D model for this variant")
                continue
            try:
                result = self.render_frame(choice, width, height)
            except (OSError, ValueError, ArithmeticError) as error:
                results.append(str(error))
            else:
                results.append(result)
        if not self.cancelled.is_set():
            self.signals.finished.emit(self.generation, results)

    def render_frame(self, choice, width, height):
        return self.renderer.render(choice, self.kind, width, height, self.turn).tobytes()


class AnimatedRenderJob(RenderJob):
    def __init__(self, render, kind, choice, width, height, turn, generation, options, elapsed):
        super().__init__(render, kind, [(choice, width, height)], turn, generation)
        self.options, self.elapsed = options, elapsed

    def render_frame(self, choice, width, height):
        return self.renderer(choice, self.kind, width, height, self.turn, *self.options, self.elapsed)


class AnimatedPreview(QWidget):
    def __init__(self, width, height, parent):
        super().__init__(parent)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self.render)
        self.renderer = None
        self.job = None
        self.choice = None
        self.generation = 0
        self.model_revision = 0
        self.dragging = False
        self.paused = False
        self.turn = 0.0
        self.started = monotonic()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.pane = PreviewPane(width, height, self)
        self.pane.turned.connect(self.rotate)
        self.pane.dragging_changed.connect(self.set_dragging)
        layout.addWidget(self.pane, 0, Qt.AlignmentFlag.AlignCenter)
        self.effect_status = QLabel()
        self.effect_status.setWordWrap(True)
        self.effect_status.setTextFormat(Qt.TextFormat.PlainText)
        self.effect_status.hide()
        layout.addWidget(self.effect_status)
        layout.addStretch()

    def show_effect_problems(self, problems):
        self.effect_status.setText(problems[0] if problems else "")
        self.effect_status.setToolTip("\n".join(problems))
        self.effect_status.setVisible(bool(problems))

    def restart_effects(self, *_):
        self.started = monotonic()
        self.show_effect_problems(())
        self.queue()

    def rotate(self, delta):
        self.turn = (self.turn + delta * .65) % 360
        self.queue()

    def set_dragging(self, dragging):
        self.dragging = dragging
        if not dragging:
            self.queue()

    def queue(self):
        self.generation += 1
        if self.choice is None:
            self.timer.stop()
            self.pane.display("No 3D model available")
        elif not self.paused and self.isVisible() and self.job is None:
            self.timer.start(0)

    def render(self):
        if self.paused or self.job is not None or self.choice is None or self.renderer is None or not self.isVisible():
            return
        width, height = self.render_size()
        if self.dragging:
            width, height = width // 2, height // 2
        self.job_revision = self.model_revision
        options = self.effect_options()
        if options is None:
            self.job = RenderJob(self.renderer, self.kind, [(self.choice, width, height)], self.turn, self.generation)
        else:
            self.job = AnimatedRenderJob(self.renderer.render_effects, self.kind, self.choice, width, height, self.turn,
                                         self.generation, options, monotonic() - self.started)
        self.job.signals.finished.connect(self.rendered)
        QThreadPool.globalInstance().start(self.job)

    def effect_options(self):
        return None

    def render_size(self):
        return self.pane.dimensions.width(), self.pane.dimensions.height()

    def rendered(self, generation, results):
        job = self.job
        if job is None or job.generation != generation:
            return
        self.job = None
        latest = generation == self.generation
        result, problems, animated = results[0] if isinstance(results[0], tuple) else (results[0], (), False)
        # Present completed drag frames even if another mouse position is waiting.
        if self.job_revision == self.model_revision and (latest or self.dragging and isinstance(result, bytes)):
            self.pane.display(result, job.requests[0][1:])
            self.show_effect_problems(problems)
        if self.choice is None or not self.isVisible():
            return
        if not latest:
            self.timer.start(0)
        elif animated:
            self.timer.start(65)

    def stop(self):
        self.timer.stop()
        self.generation += 1
        if self.job is not None:
            self.job.cancelled.set()
            self.job = None

    def set_paused(self, paused):
        self.paused = paused
        if paused:
            self.stop()
        else:
            self.queue()

    def showEvent(self, event):
        super().showEvent(event)
        self.queue()

    def hideEvent(self, event):
        self.stop()
        self.dragging = False
        self.pane.drag_x = None
        super().hideEvent(event)


class EquipmentPreview(QWidget):
    def __init__(self, parent: QWidget, assets: ModelAssets, family: int, kind: str, combo: QComboBox):
        super().__init__(parent)
        self.assets = assets
        self.renderer = PreviewRenderer(assets)
        self.family = family
        self.kind = kind
        self.render_kind = "shield" if kind == "weapon" and WEAPON_TYPES[family] == 19 else kind
        self.combo = combo
        self.tables: tuple[dict[int, ModelChoice], ...] = ()
        self.turn = 0.0
        self.generation = 0
        self.pending_frame = False
        self.job: RenderJob | None = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(8)
        self.timer.timeout.connect(self.render)
        self.body = QComboBox()
        self.body.setMaximumWidth(155)
        self.sex = QComboBox()
        self.sex.addItems(("Male", "Female"))
        self.fit = QComboBox()
        self.fit.addItems([str(i) for i in range(1, 9)])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        options = QFormLayout()
        if kind in ("armor", "cape"):
            options.addRow("Body:", self.body)
        elif kind == "mount":
            options.addRow("Race:", self.body)
        if kind == "cape":
            options.addRow("Body model:", self.sex)
            options.addRow("Chest fit:", self.fit)
        if options.rowCount():
            layout.addLayout(options)
        self.panes = []
        compact_armor = kind == "armor" and ARMOR_TYPES[family] != "helmet"
        width = 218 if compact_armor else 256
        height = (166 if compact_armor else 190) if kind == "armor" else 360
        for text in ("Male", "Female") if kind == "armor" else ("",):
            if text:
                layout.addWidget(QLabel(text))
            pane = PreviewPane(width, height, self)
            pane.turned.connect(self.rotate)
            layout.addWidget(pane)
            self.panes.append(pane)
        layout.addStretch()
        self.body.currentIndexChanged.connect(self.reload)
        self.sex.currentIndexChanged.connect(self.reload)
        self.fit.currentIndexChanged.connect(self.reload)
        self.combo.currentIndexChanged.connect(self.queue_render)
        self.combo.editTextChanged.connect(self.queue_render)

    def set_character(self, country: int, classes: tuple[int, ...]):
        selected = self.body.currentData()
        self.body.blockSignals(True)
        self.body.clear()
        bodies = eligible_bodies(self.family, country, classes) if self.kind in ("armor", "cape", "mount") else (HUMAN,)
        seen = set()
        for body in bodies:
            if self.kind == "mount" and body.race in seen:
                continue
            self.body.addItem(body.race if self.kind == "mount" else body.label, body)
            seen.add(body.race)
        index = next((i for i in range(self.body.count()) if self.body.itemData(i) == selected), 0)
        self.body.setCurrentIndex(index)
        self.body.blockSignals(False)
        self.reload()

    def reload(self, *_):
        old = model_value(self.combo)
        body: Body = self.body.currentData() or HUMAN
        fit = self.fit.currentIndex() + 1
        women = (False, True) if self.kind == "armor" else (self.sex.currentIndex() == 1,)
        tables = []
        error = ""
        for female in women:
            try:
                table = self.assets.choices(self.family, body, female, fit)
            except (OSError, ValueError) as issue:
                error = str(issue)
                table = {}
            tables.append(table)
        self.tables = tuple(tables)
        choices = {}
        # Prefer the original male table's name when a model ID exists for both sexes.
        for table in reversed(self.tables):
            choices.update(table)
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.setEditable(not choices)
        if choices:
            for index, choice in sorted(choices.items()):
                glow = f" ({GLOW_NAMES[choice.glow]})" if 0 <= choice.glow < len(GLOW_NAMES) else ""
                self.combo.addItem(f"{index} - {choice.name}{glow}", index)
                files = "\n".join((choice.table, *(spec.mesh for spec in choice.surfaces)))
                self.combo.setItemData(self.combo.count() - 1, files, Qt.ItemDataRole.ToolTipRole)
            try:
                index = int(old)
            except ValueError:
                index = next(iter(choices))
            row = self.combo.findData(index)
            if row < 0:
                self.combo.insertItem(0, f"{index} - Model unavailable", index)
                row = 0
            self.combo.setCurrentIndex(row)
        else:
            self.combo.lineEdit().setValidator(QIntValidator(0, 255, self.combo.lineEdit()))
            self.combo.setEditText(old)
        self.combo.blockSignals(False)
        self.setToolTip(error)
        self.queue_render()

    def rotate(self, delta: float):
        self.turn = (self.turn + delta * .65) % 360
        self.request_frame()

    def queue_render(self, *_):
        self.generation += 1
        self.request_frame()

    def request_frame(self):
        try:
            index = int(model_value(self.combo))
        except ValueError:
            index = -1
        if not any(index in table for table in self.tables):
            self.timer.stop()
            self.pending_frame = False
            for pane in self.panes:
                pane.display("No 3D model available")
        elif self.job is not None:
            self.pending_frame = True
        elif not self.timer.isActive():
            self.timer.start()

    def render(self):
        if self.job is not None:
            return
        try:
            index = int(model_value(self.combo))
        except ValueError:
            return
        requests = [
            (table.get(index), pane.dimensions.width(), pane.dimensions.height())
            for pane, table in zip(self.panes, self.tables)
        ]
        self.job = RenderJob(self.renderer, self.render_kind, requests, self.turn, self.generation)
        self.job.signals.finished.connect(self.rendered)
        QThreadPool.globalInstance().start(self.job)

    @Slot(int, object)
    def rendered(self, generation, results):
        self.job = None
        if generation == self.generation:
            for pane, result in zip(self.panes, results):
                pane.display(result)
        if self.pending_frame:
            self.pending_frame = False
            self.render()

    def stop(self, *_):
        self.timer.stop()
        self.generation += 1
        self.pending_frame = False
        if self.job is not None:
            self.job.cancelled.set()
