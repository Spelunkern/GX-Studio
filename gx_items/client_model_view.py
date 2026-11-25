"""Editors for client animation actions and cloak resources."""

from math import isfinite
from pathlib import Path
from struct import error as StructError, pack

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .asset_models import AssetRenderer, ViewerAssets, animation_scenes, cloak_scenes, ctl_names, read_asset
from .asset_view import AssetPreview, ImageView, read_image
from .catalog import decode_text
from .client_model_data import (
    ACTION, EMBLEM, EMBLEM_FILTER, PIN_OFFSET, AltCatalog, AltRecord, CloakCatalog, EmblemCatalog, TextureCatalog,
    cloak_emblem, cloak_pins, cloak_values, emblem_texture_directory,
)
from .equipment_models import ModelAssets, PC_PIN, PC_ROW, dds_name
from .install import replace_file, save_model_table
from .item_view import find_child
from .mon_catalog import text_bytes
from .mon_view import configure_table, dialog_buttons
from .skill_sheet import SkillSizedDialog

AUX_TYPES = {".alt": AltCatalog, ".pc": CloakCatalog, ".ctl": TextureCatalog}
AUX_FILTER = (
    f"Animation and cloak tables (*.alt *.ALT *.pc *.PC *.ctl *.CTL {EMBLEM_FILTER})"
)


def client_model_type(path):
    return EmblemCatalog if emblem_texture_directory(path) else AUX_TYPES.get(path.suffix.casefold())


def uint_value(text, maximum, field):
    try:
        result = int(text.strip())
    except ValueError:
        result = -1
    if not 0 <= result <= maximum:
        raise ValueError(f"{field} must be between 0 and {maximum:,}.")
    return result


def float_bytes(text):
    value = float(text)
    if not isfinite(value):
        raise ValueError("Enter a finite 32-bit number.")
    return pack("<f", value)


class ClientRecordDialog(SkillSizedDialog):
    def __init__(self, parent, number, record):
        super().__init__(parent, parent.settings, f"{parent.kind}-editor")
        self.catalog, self.window, self.before = parent.catalog, parent, record
        self.setWindowTitle(f"{parent.kind.upper()} Editor — Record {number}")
        layout = QVBoxLayout(self)
        self.layout_fields = QFormLayout()
        layout.addLayout(self.layout_fields)
        if isinstance(record, AltRecord):
            self.name = self.file_field("ANI File", find_child(parent.source.parent, "ANI"), "*.ani *.ANI",
                                        decode_text(record.name))
            mode, next_action, *metadata = record.values()
            self.mode = self.field("Mode (0–255)", mode & 255)
            self.flags = [self.field(f"Parameter byte {i} (0–255)", (mode >> (8 * i)) & 255) for i in range(1, 4)]
            self.next = self.field("Next action (0–4,294,967,295)", next_action)
            self.keys = [self.field(label, f"{value:.9g}") for label, value in
                         zip(("First key", "End key", "Phase key 1", "Phase key 2"), metadata)]
        elif isinstance(self.catalog, TextureCatalog):
            maximum = self.catalog.record_size - 1
            self.name = self.file_field(f"DDS File (max {maximum} UTF-8 bytes)", parent.texture_folder(), "*.dds *.DDS",
                                        decode_text(record))
        else:
            self.build_cloak(layout)
        dialog_buttons(self, layout)
        self.restore_size(QSize(600, 490) if parent.kind == "pc" else QSize(415, 240))

    def field(self, label, value):
        line = QLineEdit(str(value))
        self.layout_fields.addRow(label, line)
        return line

    def file_field(self, label, folder, filters, value):
        line = QLineEdit(value)
        browse = QPushButton("Browse…")

        def pick():
            path, _ = QFileDialog.getOpenFileName(self, label, str(folder), f"Files ({filters})")
            if path:
                path = Path(path)
                if path.parent.resolve() != folder.resolve():
                    QMessageBox.warning(self, label, f"Select a resource in\n{folder}")
                else:
                    line.setText(path.name)

        browse.clicked.connect(pick)
        row = QHBoxLayout()
        row.addWidget(line, 1)
        row.addWidget(browse)
        self.layout_fields.addRow(label, row)
        return line

    def asset_combo(self, label, names, index, folder):
        combo = QComboBox(editable=True)
        values = set(names)
        if folder.is_dir():
            values.update(p.name for p in folder.iterdir() if p.suffix.casefold() == ".3dc" and p.is_file())
        names = tuple(names)
        combo.addItems(sorted(values, key=str.casefold))
        combo.setEditText(names[index] if 0 <= index < len(names) else "")
        self.layout_fields.addRow(label, combo)
        return combo

    def build_cloak(self, layout):
        body, texture, mode, shoulder, columns, rows = cloak_values(self.before)
        parent = self.window
        self.body = self.asset_combo("Cloth 3DC", CloakCatalog.names(parent.bodies), body, parent.source.parent / "3DC")
        self.shoulder = self.asset_combo("Shoulder 3DC", CloakCatalog.names(parent.shoulders), shoulder,
                                         parent.source.parent / "3DC" / "Static")
        self.texture = QComboBox()
        textures = parent.texture_names()
        for index, name in enumerate(textures):
            self.texture.addItem(f"{index} · {name}", index)
        current = self.texture.findData(texture)
        if current < 0:
            self.texture.addItem(f"{texture} · Texture unavailable", texture)
            current = self.texture.count() - 1
        self.texture.setCurrentIndex(current)
        self.layout_fields.addRow("Texture index (CTL)", self.texture)
        self.mode = self.field("Cloak mode (0–1)", mode)
        self.columns = self.field("Grid columns (2–32)", columns + 1)
        self.rows = self.field("Grid rows (2–128)", rows + 1)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        self.pins = QTableWidget(20, 6)
        self.pins.setHorizontalHeaderLabels(("Active", "Vertex", "Bone", "X", "Y", "Z"))
        self.pin_values = cloak_pins(self.before)
        for row, pin in enumerate(self.pin_values):
            for col, value in enumerate(pin):
                if col == 0:
                    item = QTableWidgetItem()
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
                else:
                    item = QTableWidgetItem(f"{value:.9g}" if col >= 3 else str(value))
                self.pins.setItem(row, col, item)
        tabs.addTab(self.pins, "Bone Pins")
        emblem = QWidget()
        form = QFormLayout(emblem)
        self.emblem = []
        for label, value in zip(("Center column", "Center row", "Column half-span", "Row half-span"), cloak_emblem(self.before)):
            field = QLineEdit(str(value))
            self.emblem.append(field)
            form.addRow(label, field)
        tabs.addTab(emblem, "Emblem")

    def alt_record(self):
        name = text_bytes(self.name.text(), self.before.name)
        if not decode_text(name):
            return AltRecord(name)
        original = self.before.parameters or bytes(ACTION.size)
        mode = sum(uint_value(edit.text(), 255, "Mode or parameter byte") << (8 * index)
                   for index, edit in enumerate((self.mode, *self.flags)))
        next_action = uint_value(self.next.text(), 4294967295, "Next action")
        values = [pack("<II", mode, next_action)]
        for index, (edit, old) in enumerate(zip(self.keys, self.before.values()[2:]), 2):
            values.append(original[index * 4:(index + 1) * 4] if edit.text() == f"{old:.9g}" else float_bytes(edit.text()))
        return AltRecord(name, b"".join(values))

    def cloak_record(self):
        parent = self.window
        body, _, before_mode, shoulder, _, _ = cloak_values(self.before)
        self.bodies, body = CloakCatalog.reference(parent.bodies, body, self.body.currentText())
        self.shoulders, shoulder = CloakCatalog.reference(parent.shoulders, shoulder, self.shoulder.currentText())
        mode = before_mode if self.mode.text() == str(before_mode) else uint_value(self.mode.text(), 1, "Cloak mode")
        cols, rows = uint_value(self.columns.text(), 32, "Grid columns"), uint_value(self.rows.text(), 128, "Grid rows")
        if cols < 2 or rows < 2:
            raise ValueError("The grid needs at least two columns and two rows.")
        raw = bytearray(self.before)
        PC_ROW.pack_into(raw, 0, body, self.texture.currentData(), mode, shoulder, cols - 1, rows - 1)
        original = cloak_emblem(self.before)
        emblem = [old if edit.text() == str(old) else uint_value(edit.text(), limit, label)
                  for old, edit, limit, label in zip(original, self.emblem, (cols - 1, rows - 1, cols - 1, rows - 1),
                                                     ("Emblem column", "Emblem row", "Column half-span", "Row half-span"))]
        EMBLEM.pack_into(raw, PC_ROW.size, *emblem)
        for row, before in enumerate(self.pin_values):
            cells = [self.pins.item(row, column) for column in range(6)]
            enabled = cells[0].checkState() == Qt.CheckState.Checked
            active = before[0] if bool(before[0]) == enabled else int(enabled)
            vertex = uint_value(cells[1].text(), cols * rows - 1 if enabled else 4294967295, "Pin vertex")
            bone = uint_value(cells[2].text(), 255 if enabled else 4294967295, "Pin bone")
            start = PIN_OFFSET + row * PC_PIN.size
            raw[start:start + 12] = pack("<3I", active, vertex, bone)
            for index in range(3):
                text = cells[index + 3].text()
                if text != f"{before[index + 3]:.9g}":
                    offset = start + (3 + index) * 4
                    raw[offset:offset + 4] = float_bytes(text)
        return bytes(raw)

    def accept(self):
        try:
            self.result_record = self.alt_record() if self.window.kind == "alt" else (
                self.catalog.name(self.name.text(), self.before) if isinstance(self.catalog, TextureCatalog) else self.cloak_record()
            )
        except (ValueError, StructError, OverflowError) as error:
            QMessageBox.warning(self, "Edit Record", str(error))
            return
        super().accept()


class ClientModelWindow(QMainWindow):
    def __init__(self, studio):
        super().__init__(studio, Qt.WindowType.Window)
        self.studio, self.settings = studio, studio.settings
        self.source = self.catalog = self.assets = None
        self.records = []
        self.bodies = self.shoulders = None
        self.kind = ""
        self.resize(775, 495)
        file = self.menuBar().addMenu("&File")
        self.save_action = file.addAction("&Save", "Ctrl+S", self.save)
        self.export_action = file.addAction("Export Copy…", self.export)
        self.reload_action = file.addAction("Reload…", self.reload)
        file.addAction("&Close", "Ctrl+W", self.close)
        edit = self.menuBar().addMenu("&Edit")
        self.edit_action = edit.addAction("Edit Record…", "Return", self.edit)
        self.add_action = edit.addAction("Append Record…", "Ctrl+N", self.add)
        self.preview_action = edit.addAction("Open Read-only Viewer", self.open_viewer)

        body = QWidget()
        layout = QHBoxLayout(body)
        self.table = QTableWidget()
        configure_table(self.table)
        self.table.cellDoubleClicked.connect(self.edit)
        self.table.currentCellChanged.connect(self.select)
        layout.addWidget(self.table, 1)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.preview = AssetPreview(self)
        self.images = ImageView()
        self.pages.addWidget(self.preview)
        self.pages.addWidget(self.images)
        self.setCentralWidget(body)
        self.update_actions()

    def load(self, source, catalog, selected=0):
        self.source, self.catalog = source, catalog
        self.kind = "emblem" if isinstance(catalog, EmblemCatalog) else source.suffix.casefold()[1:]
        self.records = list(catalog.records)
        self.bodies = getattr(catalog, "bodies", None)
        self.shoulders = getattr(catalog, "shoulders", None)
        self.assets = ViewerAssets(source, self.studio.client_data)
        self.preview.renderer = AssetRenderer(self.assets)
        self.pages.setCurrentWidget(self.images if isinstance(catalog, TextureCatalog) else self.preview)
        self.refresh(selected)

    def texture_folder(self):
        if self.kind == "emblem":
            return find_child(self.source.parent, emblem_texture_directory(self.source))
        return find_child(self.source.parent, self.source.stem.split("_")[-1])

    def texture_names(self):
        prefix = self.source.stem.split("_")[-1][:2]
        path = self.assets.resolve(f"cloak/ClothTexture/CLOTH_TEXTHRE_{prefix}.CTL")
        return ctl_names(read_asset(path)) if path else ()

    def clear(self):
        self.close()
        self.source = self.catalog = self.assets = None
        self.kind = ""
        self.records.clear()
        self.bodies = self.shoulders = None
        self.refresh(0)

    def changed(self):
        return self.catalog is not None and (
            tuple(self.records) != self.catalog.records or self.bodies != getattr(self.catalog, "bodies", None)
            or self.shoulders != getattr(self.catalog, "shoulders", None)
        )

    def encode(self, rows):
        if self.kind == "pc":
            return self.catalog.encode(rows, self.bodies, self.shoulders)
        return self.catalog.encode(rows)

    def refresh(self, selected):
        headers = {"alt": ("Index", "Animation", "Mode", "Next Action"),
                   "pc": ("Index", "Cloth 3DC", "Texture", "Shoulder 3DC", "Grid"), "ctl": ("Index", "DDS"),
                   "emblem": ("Pattern ID", "DDS")}
        self.table.blockSignals(True)
        self.table.clear()
        keys = headers.get(self.kind, ())
        self.table.setColumnCount(len(keys))
        self.table.setHorizontalHeaderLabels(keys)
        self.table.setRowCount(len(self.records))
        bodies = CloakCatalog.names(self.bodies) if self.bodies is not None else ()
        shoulders = CloakCatalog.names(self.shoulders) if self.shoulders is not None else ()
        for index, record in enumerate(self.records):
            if self.kind == "pc":
                body, texture, _, shoulder, columns, rows = cloak_values(record)
                values = (index, bodies[body] if body < len(bodies) else body, texture,
                          shoulders[shoulder] if shoulder < len(shoulders) else shoulder, f"{columns + 1} × {rows + 1}")
            elif self.kind == "alt":
                mode, next_action, *_ = record.values()
                values = (index, decode_text(record.name), mode & 255, next_action)
            else:
                values = (index + (self.kind == "emblem"), decode_text(record))
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        if self.records:
            self.table.setCurrentCell(min(selected, len(self.records) - 1), 0)
        self.table.blockSignals(False)
        self.select()

    def select(self, *_):
        self.update_actions()
        self.preview.stop()
        self.preview.choice = None
        self.preview.pane.clear()
        self.images.set_image(None)
        index = self.table.currentRow()
        if not 0 <= index < len(self.records):
            return
        record = self.records[index]
        try:
            if isinstance(self.catalog, TextureCatalog):
                if self.kind == "emblem":
                    path = ModelAssets(self.texture_folder()).resolve(dds_name(decode_text(record)))
                else:
                    path = self.assets.texture_path(decode_text(record), f"cloak/ClothTexture/{self.source.stem.split('_')[-1]}")
                if path:
                    self.images.set_image(read_image(Path(path)))
                else:
                    self.statusBar().showMessage("Texture unavailable.")
            else:
                raw = self.encode((record,))
                variants = animation_scenes(self.source, raw) if self.kind == "alt" else cloak_scenes(self.source, self.assets, raw)
                if variants:
                    self.preview.show_scene(variants[0][1], "cape" if self.kind == "pc" else "armor")
                else:
                    self.preview.pane.display("No preview for this record")
        except (OSError, ValueError) as error:
            self.statusBar().showMessage(str(error))

    def update_actions(self):
        loaded = self.source is not None and self.studio.task is None
        self.save_action.setEnabled(loaded and self.changed())
        for action in (self.export_action, self.reload_action, self.preview_action):
            action.setEnabled(loaded)
        self.edit_action.setEnabled(loaded and self.table.currentRow() >= 0)
        can_add = self.kind != "pc" or bool(self.records) and len(self.records) < 256
        if self.kind == "emblem":
            can_add = len(self.records) < EmblemCatalog.max_records
        self.add_action.setEnabled(loaded and can_add)
        self.setWindowTitle((self.source.name + (" *" if self.changed() else "")) if self.source else "Animation and Cloak Tables")

    def edit(self, *_):
        index = self.table.currentRow()
        if index >= 0:
            self.open_dialog(index, self.records[index])

    def add(self):
        if not self.add_action.isEnabled():
            return
        if self.kind == "alt":
            record = AltRecord()
        elif isinstance(self.catalog, TextureCatalog):
            record = bytes(self.catalog.record_size)
        else:
            record = self.records[max(0, self.table.currentRow())]
        self.open_dialog(len(self.records), record)

    def open_dialog(self, index, record):
        dialog = ClientRecordDialog(self, index + (self.kind == "emblem"), record)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.records[index:index + 1] = [dialog.result_record]
            if self.kind == "pc":
                self.bodies, self.shoulders = dialog.bodies, dialog.shoulders
            self.refresh(index)
        dialog.deleteLater()

    def save(self):
        if not self.save_action.isEnabled():
            return
        title = f"Save {self.kind.upper()}"
        if QMessageBox.question(self, title, f"Save the client file with a dated backup?\n\n{self.source}") != QMessageBox.StandardButton.Yes:
            return
        index = self.table.currentRow()
        source, catalog, backups = self.source, self.catalog, self.studio.backup_folder()
        raw = self.encode(self.records)
        tag = self.kind.upper()

        def complete(result):
            saved, backup = result
            self.load(source, saved, index)
            ModelAssets.choices.cache_clear()
            self.studio.update_catalog_nodes()
            self.statusBar().showMessage(f"Saved · Backup {backup}")

        self.studio.run_task(f"Saving {tag}…", lambda: save_model_table(source, catalog, raw, backups,
                             format_name=tag, read=type(catalog).read), complete)

    def export(self):
        if not self.export_action.isEnabled():
            return
        default = self.source.with_stem(self.source.stem + "-copy")
        suffix = self.source.suffix
        file, _ = QFileDialog.getSaveFileName(self, "Export Copy", str(default),
                                              f"Files (*{suffix.lower()} *{suffix.upper()})")
        if not file:
            return
        path = Path(file)
        if not path.suffix:
            path = path.with_suffix(self.source.suffix)
        path = path.resolve()
        if path.suffix.casefold() != self.source.suffix.casefold() or path == self.source:
            QMessageBox.warning(self, "Export Copy", "Choose a separate file with the original extension.")
            return
        raw = self.encode(self.records)
        self.studio.run_task("Exporting client table…", lambda: replace_file(path, raw, allow_create=True),
                             lambda _: self.statusBar().showMessage(f"Exported to {path}"))

    def reload(self):
        if self.source:
            self.studio.open_client_model(self.source, reload=True)

    def open_viewer(self):
        if self.source:
            self.studio.open_asset(self.source)

    def closeEvent(self, event):
        if self.isVisible():
            self.settings.setValue("client-model/geometry", self.saveGeometry())
        super().closeEvent(event)

    def restore_window(self):
        geometry = self.settings.value("client-model/geometry", QByteArray(), type=QByteArray)
        return not geometry.isEmpty() and self.restoreGeometry(geometry)
