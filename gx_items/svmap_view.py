"""Top-down server-map inspector with table-driven overlays."""

from dataclasses import dataclass, replace
from math import floor, isfinite

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTabWidget, QTableView,
    QVBoxLayout, QWidget,
)

from .mon_view import configure_table
from .safe_zones import SafeZone
from .svmap_edit import SPECS, MobPreview, SafeZoneDialog, edit_map_record

XYZ = ("X", "Y", "Z")
BOX_COLUMNS = ("X1", "Z1", "X2", "Z2")
BOX_DETAILS = ("X1", "Y1", "Z1", "X2", "Y2", "Z2")


@dataclass(frozen=True, slots=True)
class Layer:
    title: str
    color: str
    columns: tuple
    rows: tuple
    shapes: tuple = ()
    details: tuple = ()
    detail_columns: tuple = ()
    detail_title: str = "Details"

    def detail(self, row):
        rows = self.details[row] if 0 <= row < len(self.details) else ()
        return Layer("", "", self.detail_columns, rows)


def box_columns(bounds):
    return tuple(bounds[column] for column in (0, 2, 3, 5))


def map_layers(data, monsters, npcs, safe, names):
    by_bounds = {name.bounds: name for name in names}
    matched = [by_bounds.get(area[:6]) or (names[index] if len(names) == len(data.named) else None)
               for index, area in enumerate(data.named)]
    areas = data.monsters
    by_monster = {}
    for index, area in enumerate(areas):
        for monster, count in area.spawns:
            by_monster.setdefault(monster, []).append((index, count, area.bounds))
    monster_ids = sorted(by_monster)
    npc_rows = []
    for index, npc in enumerate(data.npcs):
        definition = npcs.get((npc.kind, npc.identifier))
        points = len(npc.patrol)
        movement = "Patrol" if points > 1 else "Stationary" if points else "No position"
        npc_rows.append((index, npc.kind, npc.identifier, definition.name if definition else "", points, movement))

    return (
        Layer("Ladders", "#f6a957", ("Indx", *XYZ), tuple((index, *position) for index, position in enumerate(data.ladders)),
              tuple(data.ladders)),
        Layer("Monster Areas", "#e52924", ("Indx", *BOX_COLUMNS),
              tuple((index, *box_columns(area.bounds)) for index, area in enumerate(areas)),
              tuple(area.bounds for area in areas),
              tuple(tuple((mob, count, monsters.get(mob, {}).get("Name", "")) for mob, count in area.spawns)
                    for area in areas), ("MobID", "Count", "Name"), "Monsters in area"),
        Layer("Monsters", "#e52924", ("MobID", "Level", "Elem", "Name"),
              tuple((mob, *(monsters.get(mob, {}).get(key, "") for key in ("Level", "Attrib", "Name")))
                    for mob in monster_ids),
              tuple(tuple(bounds for _, _, bounds in by_monster[mob]) for mob in monster_ids),
              tuple(tuple((index, count, *box_columns(bounds)) for index, count, bounds in by_monster[mob])
                    for mob in monster_ids), ("Area", "Count", *BOX_COLUMNS), "Monster areas"),
        Layer("Named Areas", "#e52924", ("Indx", "Label", "Source file", *BOX_COLUMNS),
              tuple((index, name.label if name and name.label else "—", name.source if name else "—",
                     *box_columns(area)) for index, (area, name) in enumerate(zip(data.named, matched))),
              tuple(area[:6] for area in data.named), tuple((area,) for area in data.named),
              (*BOX_DETAILS, "Name mode", "Parameter")),
        Layer("NPC", "#f9d36a", ("Indx", "Type", "TypeID", "Name", "Pos.", "Movement"),
              tuple(npc_rows), tuple(tuple(p[:3] for p in npc.patrol) for npc in data.npcs),
              tuple(tuple((index, *point) for index, point in enumerate(npc.patrol)) for npc in data.npcs),
              ("Indx", *XYZ, "Orient."), "Position(s)"),
        Layer("Portals", "#87c9ff", ("Indx", *XYZ, "Dest", "L.Min", "L.Max"),
              tuple((index, *p[:3], p[6], p[4], p[5]) for index, p in enumerate(data.portals)),
              tuple(p[:3] for p in data.portals),
              tuple(((p[3], *p[7:]),) for p in data.portals),
              ("Faction / portal ID", "Target X", "Target Y", "Target Z")),
        Layer("Spawns", "#e52924", ("Indx", "Faction", "Boxes", "Parameter"),
              tuple((index, area.faction, len(area.boxes), area.auxiliary) for index, area in enumerate(data.spawns)),
              tuple(area.boxes for area in data.spawns),
              tuple(tuple((index, *box) for index, box in enumerate(area.boxes)) for area in data.spawns),
              ("Indx", *BOX_DETAILS), "Spawn boxes"),
        Layer("Safe Areas", "#8ce5ef", ("Name", "Protected faction"),
              tuple((zone.name, zone.faction) for zone in safe),
              tuple(tuple(value for x, z in zone.points for value in (x, 0, z)) for zone in safe),
              tuple(tuple((index, x, z) for index, (x, z) in enumerate(zone.points, 1)) for zone in safe),
              ("Vertex", "X", "Z")),
    )


class LayerModel(QAbstractTableModel):
    def __init__(self, layer, parent):
        super().__init__(parent)
        self.layer = layer

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.layer.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.layer.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole,
                                        Qt.ItemDataRole.UserRole):
            value = self.layer.rows[index.row()][index.column()]
            return format(value, ".7g") if isinstance(value, float) and role != Qt.ItemDataRole.UserRole else value
        return None

    def headerData(self, column, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.layer.columns[column]
        return super().headerData(column, orientation, role)


def draw_shape(path, shape, radius, size, *, patrol=False):
    if not shape:
        return
    if isinstance(shape[0], tuple):
        for part in shape:
            draw_shape(path, part, radius, size)
        if patrol:
            points = [QPointF(x, size - z) for x, y, z in shape if isfinite(x) and isfinite(z)]
            if len(points) > 1:
                path.moveTo(points[0])
                for point in points[1:]:
                    path.lineTo(point)
        return
    if not all(isfinite(value) for value in shape):
        return
    if len(shape) == 12:
        points = [QPointF(shape[i], size - shape[i + 2]) for i in range(0, 12, 3)]
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        path.closeSubpath()
        return
    x, _, z, *upper = shape
    origin = QPointF(x, size - z)
    if upper:
        rectangle = QRectF(origin, QPointF(upper[0], size - upper[2])).normalized()
        if not rectangle.isEmpty():
            path.addRect(rectangle)
            return
    path.addEllipse(origin, radius, radius)


class MapCanvas(QGraphicsView):
    capture_ended = Signal()

    def __init__(self, coordinates, parent):
        super().__init__(parent)
        self.coordinates = coordinates
        self.map = None
        self.layer = None
        self.layer_path = QPainterPath()
        self.highlight_path = QPainterPath()
        self.color = QColor()
        self.fitted = True
        self.capture = None
        self.anchor = None
        self.drawing = QPainterPath()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#fafafa"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMouseTracking(True)

    def load(self, data, minimap):
        self.map = data
        self.scene().clear()
        if minimap is not None:
            picture = minimap.pixmap()
            item = self.scene().addPixmap(picture)
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            scale = data.size / max(picture.width(), picture.height())
            item.setTransform(QTransform.fromScale(scale, scale))
            item.setPos((data.size - picture.width() * scale) / 2, (data.size - picture.height() * scale) / 2)
        self.setSceneRect(0, 0, data.size, data.size)
        self.fit_map()

    def clear(self):
        self.cancel_capture()
        self.map = self.layer = None
        self.scene().clear()
        self.layer_path = QPainterPath()
        self.highlight_path = QPainterPath()
        self.coordinates.setText("")

    def fit_map(self):
        self.fitted = True
        if self.map is not None:
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit_map()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta or self.map is None:
            super().wheelEvent(event)
            return
        self.fitted = False
        level = self.transform().m11()
        fit = min(self.viewport().width(), self.viewport().height()) / self.map.size
        next_level = max(fit * 0.65, min(max(8, fit * 24), level * (1.2 ** (delta / 120))))
        self.scale(next_level / level, next_level / level)
        event.accept()

    def capture_map(self, mode, callback):
        self.cancel_capture()
        self.capture = mode, callback
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()

    def cancel_capture(self):
        was_active = self.capture is not None
        self.capture = self.anchor = None
        self.drawing = QPainterPath()
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.viewport().unsetCursor()
        self.viewport().update()
        if was_active:
            self.capture_ended.emit()

    def capture_point(self, event):
        point = self.mapToScene(event.position().toPoint())
        x = max(0, min(self.map.size, point.x()))
        z = max(0, min(self.map.size, self.map.size - point.y()))
        return round(x, 2), round(z, 2)

    def capture_bounds(self, event):
        x1, z1 = self.anchor
        x2, z2 = self.capture_point(event)
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            side = min(abs(x2 - x1), abs(z2 - z1))
            x2 = x1 + (side if x2 >= x1 else -side)
            z2 = z1 + (side if z2 >= z1 else -side)
        return min(x1, x2), min(z1, z2), max(x1, x2), max(z1, z2)

    def keyPressEvent(self, event):
        if self.capture and event.key() == Qt.Key.Key_Escape:
            self.cancel_capture()
            self.coordinates.clear()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if self.capture and event.button() == Qt.MouseButton.LeftButton:
            if not self.sceneRect().contains(self.mapToScene(event.position().toPoint())):
                return
            mode, callback = self.capture
            point = self.capture_point(event)
            if mode == "point":
                self.cancel_capture()
                callback(point)
            else:
                self.anchor = point
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.capture and event.button() == Qt.MouseButton.LeftButton and self.anchor is not None:
            bounds = self.capture_bounds(event)
            if bounds[0] < bounds[2] and bounds[1] < bounds[3]:
                _, callback = self.capture
                self.cancel_capture()
                callback(bounds)
            else:
                self.anchor = None
                self.drawing = QPainterPath()
                self.viewport().update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self.capture and self.anchor is not None:
            x1, z1, x2, z2 = self.capture_bounds(event)
            self.drawing = QPainterPath()
            draw_shape(self.drawing, (x1, 0, z1, x2, 0, z2), 1, self.map.size)
            self.viewport().update()
            event.accept()
            return
        if self.map is not None:
            point = self.mapToScene(event.position().toPoint())
            x, z = floor(point.x()), self.map.size - 1 - floor(point.y())
            if 0 <= x < self.map.size and 0 <= z < self.map.size:
                self.coordinates.setText(f"X {x:,}   Z {z:,}")
            else:
                self.coordinates.clear()
        super().mouseMoveEvent(event)

    def show_layer(self, layer, index):
        self.highlight_path = QPainterPath()
        if self.map is not None:
            radius = max(1, self.map.size / 220)
            if self.layer is not layer:
                self.layer = layer
                self.color = QColor(layer.color)
                self.layer_path = QPainterPath()
                for shape in dict.fromkeys(layer.shapes):
                    draw_shape(self.layer_path, shape, radius, self.map.size, patrol=layer.title == "NPC")
            if 0 <= index < len(layer.shapes):
                draw_shape(self.highlight_path, layer.shapes[index], radius * 1.55, self.map.size,
                           patrol=layer.title == "NPC")
        self.viewport().update()

    def drawForeground(self, painter, rectangle):
        for path, color, width in ((self.layer_path, self.color, 1), (self.highlight_path, QColor("#ffffff"), 2.5),
                                   (self.drawing, QColor("#ff332b"), 2)):
            pen = QPen(color)
            pen.setWidthF(width)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawPath(path)


class MapLayerPage(QWidget):
    def __init__(self, layer, hint="", edit=None, draw=None, mob_context=None):
        super().__init__()
        self.layer = layer
        layout = QVBoxLayout(self)
        self.table = QTableView()
        configure_table(self.table)
        self.proxy = QSortFilterProxyModel(self.table)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy.setSourceModel(LayerModel(layer, self.proxy))
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.resizeColumnsToContents()
        if layer.title == "Monsters":
            search = QLineEdit(placeholderText="Find monster by name", clearButtonEnabled=True)
            self.proxy.setFilterKeyColumn(3)
            self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            search.textChanged.connect(self.proxy.setFilterFixedString)
            layout.addWidget(search)
        layout.addWidget(self.table, 2)
        self.details = None
        if layer.detail_columns:
            layout.addWidget(QLabel(layer.detail_title))
            self.details = QTableView()
            configure_table(self.details)
            self.details.setModel(LayerModel(layer.detail(-1), self.details))
            layout.addWidget(self.details, 1)
            self.table.selectionModel().currentChanged.connect(self.show_details)
        if hint:
            label = QLabel(hint)
            label.setWordWrap(True)
            layout.addWidget(label)

        if edit is not None:
            toolbar = QHBoxLayout()
            edit_button = QPushButton("Edit…")
            remove = QPushButton("Remove")
            for button, operation in ((edit_button, "edit"), (QPushButton("Add…"), "add"), (remove, "remove")):
                button.clicked.connect(lambda _, operation=operation: edit(self, operation))
                toolbar.addWidget(button)
            selection_buttons = [edit_button, remove]
            if draw is not None:
                for title, operation in draw:
                    button = QPushButton(title)
                    button.clicked.connect(lambda _, operation=operation: edit(self, operation))
                    toolbar.addWidget(button)
                    if operation != "draw-add":
                        selection_buttons.append(button)
            toolbar.addStretch(1)
            layout.addLayout(toolbar)
            self.table.doubleClicked.connect(lambda _: edit(self, "edit"))

            def update_buttons():
                selected = self.selected_row() >= 0
                for button in selection_buttons:
                    button.setEnabled(selected)

            self.table.selectionModel().currentChanged.connect(lambda *_: update_buttons())
            update_buttons()

        self.mob_preview = None
        if mob_context is not None:
            self.mob_preview = MobPreview(mob_context, self)
            layout.addWidget(self.mob_preview)
            self.table.selectionModel().currentChanged.connect(self.show_mob)
            if self.details is not None:
                self.details.selectionModel().currentChanged.connect(self.show_mob)

    def show_mob(self, *_):
        if self.mob_preview is None:
            return
        row = self.selected_row()
        if self.layer.title == "Monsters":
            mob_id = self.layer.rows[row][0] if row >= 0 else None
        else:
            index = self.details.currentIndex().row()
            rows = self.layer.detail(row).rows
            mob_id = rows[index][0] if 0 <= index < len(rows) else None
        self.mob_preview.select_mob(mob_id)

    def select_source_row(self, row):
        index = self.proxy.mapFromSource(self.proxy.sourceModel().index(row, 0))
        if index.isValid():
            self.table.setCurrentIndex(index)
            self.table.selectRow(index.row())

    def selected_row(self):
        return self.proxy.mapToSource(self.table.currentIndex()).row()

    def show_details(self, *_):
        previous = self.details.model()
        self.details.setModel(LayerModel(self.layer.detail(self.selected_row()), self.details))
        self.details.resizeColumnsToContents()
        previous.deleteLater()
        if self.mob_preview is not None:
            self.details.selectionModel().currentChanged.connect(self.show_mob)
            self.details.selectRow(0)
            self.show_mob()


class ServerMapView(QWidget):
    edited = Signal()
    save_zones_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.empty_layer = Layer("", "", (), ())
        self.original = self.data = None
        self.safe_path = self.safe_config = None
        self.zones = ()
        self.safe_save = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        splitter = QSplitter()
        left = QWidget()
        map_layout = QVBoxLayout(left)
        map_layout.setContentsMargins(0, 0, 0, 0)
        self.coordinates = QLabel()
        self.canvas = MapCanvas(self.coordinates, self)
        map_layout.addWidget(self.canvas, 1)
        tools = QHBoxLayout()
        fit = QPushButton("Fit")
        fit.clicked.connect(self.canvas.fit_map)
        tools.addWidget(fit)
        tools.addStretch(1)
        tools.addWidget(self.coordinates)
        map_layout.addLayout(tools)
        self.minimap_status = QLabel()
        self.canvas.capture_ended.connect(self.minimap_status.clear)
        tools.addWidget(self.minimap_status)
        self.tabs = QTabWidget()
        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.setSizes([520, 435])
        layout.addWidget(splitter, 1)
        self.tabs.currentChanged.connect(self.canvas.cancel_capture)
        self.tabs.currentChanged.connect(self.show_layer)

    def load(self, data, minimap, monsters, npcs, names, mob_assets, safe, map_id):
        self.clear()
        self.data = self.original = data
        self.assets = monsters, npcs, names
        self.mob_context = monsters, mob_assets
        self.safe_path, self.safe_config, self.safe_error = safe
        self.map_id = map_id
        self.zones = self.safe_config.for_map(map_id) if self.safe_config is not None else ()
        self.canvas.load(data, minimap)
        self.minimap_status.setText("" if minimap is not None else "Client minimap unavailable")
        self.build_tabs()

    def changed(self):
        return self.map_changed() or self.safe_changed()

    def map_changed(self):
        return self.data is not None and self.data != self.original

    def safe_changed(self):
        return self.safe_config is not None and self.zones != self.safe_config.for_map(self.map_id)

    def update_safe_save(self, enabled):
        if self.safe_save is not None:
            self.safe_save.setEnabled(enabled)

    def mark_saved(self, data):
        self.data = self.original = data

    def build_tabs(self, selected_tab=0, selected_row=-1):
        self.clear_tabs()
        data = self.data
        monsters, npcs, names = self.assets
        general = QWidget()
        summary = QFormLayout(general)
        summary.setContentsMargins(24, 27, 24, 12)
        for title, value in (
            ("Map Size", data.size), ("Cell Size", data.cell_size), ("Count of ladders", len(data.ladders)),
            ("Count of monster areas", len(data.monsters)), ("Count of named areas", len(data.named)),
            ("Count of NPCs", len(data.npcs)), ("Count of portals", len(data.portals)),
            ("Count of spawn points", sum(len(group.boxes) for group in data.spawns)),
        ):
            summary.addRow(title, QLabel(f"{value:,}"))
        self.tabs.addTab(general, "General")
        for layer in map_layers(data, monsters, npcs, self.zones, names):
            safe = layer.title == "Safe Areas"
            editable = layer.title in SPECS or (safe and self.safe_config is not None)
            draw = None
            if layer.title == "Monster Areas" or (safe and editable):
                draw = (("Draw New", "draw-add"), ("Redraw", "draw-edit"))
            elif layer.title == "Spawns":
                draw = (("Move on Map", "move"),)
            hint = (str(self.safe_path) if self.safe_config is not None else self.safe_error) if safe else ""
            context = self.mob_context if layer.title in ("Monsters", "Monster Areas") else None
            page = MapLayerPage(layer, hint, self.edit_layer if editable else None, draw, context)
            if safe and editable:
                self.safe_save = QPushButton("Save SafeZones.INI")
                self.safe_save.setEnabled(self.safe_changed())
                self.safe_save.clicked.connect(self.save_zones_requested)
                page.layout().addWidget(self.safe_save)
            page.table.selectionModel().currentChanged.connect(self.canvas.cancel_capture)
            page.table.selectionModel().currentChanged.connect(self.show_layer)
            self.tabs.addTab(page, layer.title)
        self.tabs.setCurrentIndex(selected_tab)
        page = self.tabs.currentWidget()
        if selected_row >= 0 and isinstance(page, MapLayerPage):
            page.select_source_row(min(selected_row, len(page.layer.rows) - 1))
        self.show_layer()

    def edit_layer(self, page, operation, drawn=None):
        row = page.selected_row()
        if operation not in ("add", "draw-add") and row < 0:
            return
        if operation.startswith("draw-"):
            self.canvas.capture_map("rectangle",
                                    lambda bounds: self.edit_layer(page, operation.removeprefix("draw-"), bounds))
            self.minimap_status.setText("Drag a rectangle · Shift for square · Esc to cancel")
            return
        if operation == "move":
            self.move_spawn(page, row)
            return
        self.minimap_status.clear()
        if operation == "remove" and QMessageBox.question(
            self, "Remove Map Record", "Remove the selected record?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        if page.layer.title == "Safe Areas":
            self.edit_safe_zone(row, operation, drawn)
            return
        data = edit_map_record(self.data, page.layer.title, row, operation, self, drawn=drawn,
                               mob_context=self.mob_context)
        if data != self.data:
            self.apply_map_edit(data, len(page.layer.rows) if operation == "add" else row)

    def apply_map_edit(self, data, row):
        self.data = data
        self.canvas.map = data
        self.build_tabs(self.tabs.currentIndex(), row)
        self.edited.emit()

    def move_spawn(self, page, row):
        group = self.data.spawns[row]
        index = page.details.currentIndex().row()
        if index < 0 and len(group.boxes) == 1:
            index = 0
        if not 0 <= index < len(group.boxes):
            QMessageBox.information(self, "Move Spawn", "Select a spawn box in the lower list first.")
            return

        def clicked(point):
            self.minimap_status.clear()
            x, z = point
            x1, y1, z1, x2, y2, z2 = group.boxes[index]
            dx, dz = x - (x1 + x2) / 2, z - (z1 + z2) / 2
            if QMessageBox.question(
                self, "Move Spawn", f"Move the selected spawn box to X {x:.2f}, Z {z:.2f}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
            ) != QMessageBox.StandardButton.Yes:
                return
            boxes = list(group.boxes)
            boxes[index] = (x1 + dx, y1, z1 + dz, x2 + dx, y2, z2 + dz)
            groups = list(self.data.spawns)
            groups[row] = replace(group, boxes=tuple(boxes))
            self.apply_map_edit(replace(self.data, spawns=tuple(groups)), row)

        self.canvas.capture_map("point", clicked)
        self.minimap_status.setText("Click the new spawn center · Esc to cancel")

    def edit_safe_zone(self, row, operation, drawn):
        zones = list(self.zones)
        if operation == "remove":
            zones.pop(row)
        else:
            if operation == "add":
                used = {zone.name.upper() for zone in (*self.safe_config.zones, *self.zones)}
                number = 1
                while f"MAP {self.map_id} - ZONE {number}" in used:
                    number += 1
                zone = SafeZone(f"Map {self.map_id} - Zone {number}", self.map_id, "Both", ((0, 0),) * 4)
            else:
                zone = zones[row]
            if drawn is not None:
                x1, z1, x2, z2 = drawn
                zone = SafeZone(zone.name, zone.map_id, zone.faction, ((x1, z1), (x2, z1), (x2, z2), (x1, z2)))
            dialog = SafeZoneDialog(zone, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if operation == "add":
                row = len(zones)
                zones.append(dialog.zone)
            else:
                zones[row] = dialog.zone
        try:
            self.safe_config.encode_map(self.map_id, zones)
        except ValueError as error:
            QMessageBox.warning(self, "Safe Zone", str(error))
            return
        self.zones = tuple(zones)
        self.build_tabs(self.tabs.currentIndex(), row)
        self.edited.emit()

    def show_layer(self, *_):
        page = self.tabs.currentWidget()
        if isinstance(page, MapLayerPage):
            self.canvas.show_layer(page.layer, page.selected_row())
        else:
            self.canvas.show_layer(self.empty_layer, -1)

    def clear(self):
        self.canvas.clear()
        self.data = self.original = self.safe_config = None
        self.zones = ()
        self.clear_tabs()

    def clear_tabs(self):
        self.safe_save = None
        while self.tabs.count():
            page = self.tabs.widget(0)
            self.tabs.removeTab(0)
            if isinstance(page, MapLayerPage) and page.mob_preview is not None:
                page.mob_preview.stop()
            page.deleteLater()
