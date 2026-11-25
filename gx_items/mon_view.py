from dataclasses import replace
from math import isfinite
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QAbstractTableModel, QByteArray, QMimeData, QModelIndex, QSize, QSortFilterProxyModel
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QLabel
from PySide6.QtWidgets import QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox, QTableView, QTableWidget
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QTableWidgetItem, QVBoxLayout, QWidget

from .catalog import decode_text
from .effect_preview import MonPreviewRenderer
from .equipment_models import ModelAssets, basename, is_vehicle_mon, mon_model_choice
from .equipment_preview import AnimatedPreview
from .install import replace_file
from .item_view import find_child
from .mon_catalog import ANIMATIONS, EFFECTS, WAVES, MonCatalog, MonRecord, save_mon, slot_text, text_bytes
from .skill_sheet import SkillSizedDialog

DISPLAY = Qt.ItemDataRole.DisplayRole
NO_EFFECTS = -2
BONE_EFFECTS = -1


def configure_table(table):
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(21)
    table.horizontalHeader().setHighlightSections(False)
    table.setShowGrid(False)


def dialog_buttons(dialog, layout):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)


class MonPreview(AnimatedPreview):
    def __init__(self, width, height, parent):
        super().__init__(width, height, parent)
        self.kind = "npc"
        self.effects_root = None
        self.attachments = ()
        self.actions = ()
        self.effect_mode = QComboBox()
        self.effect_mode.setAccessibleName("Effect Preview")
        self.effect_mode.currentIndexChanged.connect(self.restart_effects)
        self.effect_mode.hide()
        self.layout().insertWidget(1, self.effect_mode)
        self.missing = QLabel()
        self.missing.setWordWrap(True)
        self.missing.setTextFormat(Qt.TextFormat.PlainText)
        self.missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout().insertWidget(3, self.missing)

    def effect_options(self):
        mode = self.effect_mode.currentData()
        attachments = () if mode == NO_EFFECTS else self.attachments
        action = self.actions[mode] if mode is not None and mode >= 0 else ""
        return attachments, action

    def set_assets(self, assets, kind, effects_root=None):
        self.stop()
        self.effects_root = effects_root
        self.renderer = MonPreviewRenderer(assets, effects_root or assets.root) if assets else None
        self.kind = kind

    def set_record(self, record, filename, *, reset_angle=False, action=None):
        self.model_revision += 1
        if reset_angle:
            self.turn = 0
        self.choice = mon_model_choice(record, "", filename) if record is not None else None
        missing = []
        if self.choice and self.renderer:
            assets = self.renderer.assets
            surfaces = []
            for part in self.choice.surfaces:
                if assets.resolve(part.mesh) is None:
                    missing.append(part.mesh)
                else:
                    if part.texture and assets.resolve(part.texture) is None:
                        missing.append(part.texture)
                    surfaces.append(part)
            self.choice = replace(self.choice, surfaces=tuple(surfaces)) if surfaces else None
        self.missing.setText(f"{len(set(missing))} missing mesh or texture files" if missing else "")
        self.missing.setToolTip("\n".join(sorted(set(missing))))
        attachments = record.bone_effects if record is not None else ()
        actions = tuple(slot_text(effect) for effect in record.effects[:len(EFFECTS)]) if record is not None else ()
        old = self.effect_mode.currentData()
        if reset_angle and old != NO_EFFECTS:
            old = BONE_EFFECTS
        if action is not None:
            old = action
        changed = (attachments, actions) != (self.attachments, self.actions)
        self.attachments, self.actions = attachments, actions
        if changed or self.effect_mode.count() == 0:
            self.update_effect_modes(old)
        elif action is not None or reset_angle:
            self.effect_mode.setCurrentIndex(max(0, self.effect_mode.findData(old)))
        self.effect_mode.setVisible(bool(self.choice and (self.attachments or any(self.actions))))
        if changed or reset_angle or action is not None:
            self.started = monotonic()
            self.show_effect_problems(())
        self.queue()

    def update_effect_modes(self, selected):
        with QSignalBlocker(self.effect_mode):
            self.effect_mode.clear()
            self.effect_mode.addItem("Bone Effects", BONE_EFFECTS)
            for index, name in enumerate(self.actions):
                if name:
                    self.effect_mode.addItem(f"Effect Preview: {EFFECTS[index]}", index)
            self.effect_mode.addItem("Effects Off", NO_EFFECTS)
            self.effect_mode.setCurrentIndex(max(0, self.effect_mode.findData(selected)))

class VertexTextureDialog(QDialog):
    def __init__(self, parent, folder, pair):
        super().__init__(parent)
        self.setWindowTitle("MON Vertex-Texture Editor")
        self.folder = folder
        self.before = pair
        self.controls = []
        layout = QVBoxLayout(self)
        groups = (
            ("Vertices file:", "3DC", "GX meshes (*.3dc *.3DC)"),
            ("Texture file:", "DDS", "Textures (*.dds *.DDS *.tga *.TGA)"),
        )
        for raw, (title, directory, filter) in zip(pair, groups):
            layout.addWidget(QLabel(title))
            row = QHBoxLayout()
            field = QLineEdit(decode_text(raw))
            self.controls.append(field)
            row.addWidget(field, 1)
            browse = QPushButton("…")
            browse.setFixedWidth(28)
            browse.clicked.connect(lambda _, f=field, d=directory, suffix=filter: self.browse(f, d, suffix))
            row.addWidget(browse)
            layout.addLayout(row)
        dialog_buttons(self, layout)
        self.resize(270, 146)

    def browse(self, field, directory, filter):
        folder = find_child(self.folder, directory)
        filename, _ = QFileDialog.getOpenFileName(self, "Select Model File", str(folder), filter)
        if filename:
            path = Path(filename)
            if path.parent.resolve() == folder.resolve():
                field.setText(path.name)
            else:
                QMessageBox.warning(self, "Select Model File", f"Select a file from\n{folder}")

    def accept(self):
        try:
            if not all(field.text().strip() for field in self.controls):
                raise ValueError("Enter both a vertices file and a texture file.")
            self.pair = tuple(text_bytes(field.text(), raw) for field, raw in zip(self.controls, self.before))
        except ValueError as error:
            QMessageBox.warning(self, "MON Vertex-Texture Editor", str(error))
            return
        super().accept()


class SignedIndexEditor(QStyledItemDelegate):
    value_changed = Signal(QModelIndex, int)

    def createEditor(self, parent, option, index):
        editor = QSpinBox(parent, minimum=-(1 << 31), maximum=(1 << 31) - 1)
        editor.valueChanged.connect(lambda value: self.value_changed.emit(index, value))
        return editor

    def setEditorData(self, editor, index):
        with QSignalBlocker(editor):
            super().setEditorData(editor, index)


class BoneEffectsDialog(QDialog):
    effects_changed = Signal(object)

    def __init__(self, parent, attachments):
        super().__init__(parent)
        self.setWindowTitle("Bone Effects")
        self.resize(405, 310)
        layout = QVBoxLayout(self)
        self.body = QHBoxLayout()
        layout.addLayout(self.body, 1)
        editor = QVBoxLayout()
        self.body.addLayout(editor, 1)
        editor.addWidget(QLabel("Emitter indices refer to Monster.EFT."))
        self.table = QTableWidget(0, 2)
        configure_table(self.table)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.setHorizontalHeaderLabels(("Bone Index", "Emitter Index"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        delegate = SignedIndexEditor(self.table)
        self.table.setItemDelegate(delegate)
        for bone, emitter in attachments:
            self.append_row(bone, emitter)
        editor.addWidget(self.table, 1)
        delegate.value_changed.connect(self.preview_index)
        delegate.closeEditor.connect(lambda *_: self.effects_changed.emit(self.pairs()))
        self.table.cellChanged.connect(lambda *_: self.effects_changed.emit(self.pairs()))
        actions = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self.add_attachment)
        actions.addWidget(add)
        self.remove = QPushButton("Remove")
        self.remove.setEnabled(False)
        self.remove.clicked.connect(self.remove_attachment)
        self.table.currentCellChanged.connect(lambda row, *_: self.remove.setEnabled(row >= 0))
        actions.addWidget(self.remove)
        actions.addStretch()
        editor.addLayout(actions)
        dialog_buttons(self, layout)

    def append_row(self, bone, emitter):
        index = self.table.rowCount()
        self.table.insertRow(index)
        for column, value in enumerate((bone, emitter)):
            item = QTableWidgetItem()
            item.setData(Qt.ItemDataRole.EditRole, value)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setToolTip("Negative values leave this attachment inactive in GX.")
            self.table.setItem(index, column, item)
        return index

    def add_attachment(self):
        with QSignalBlocker(self.table):
            row = self.append_row(0, 0)
        self.effects_changed.emit(self.pairs())
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))

    def remove_attachment(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.effects_changed.emit(self.pairs())

    def pairs(self):
        return tuple(
            tuple(self.table.item(row, col).data(Qt.ItemDataRole.EditRole) for col in range(2))
            for row in range(self.table.rowCount())
        )

    def preview_index(self, index, value):
        pairs = [list(pair) for pair in self.pairs()]
        pairs[index.row()][index.column()] = value
        self.effects_changed.emit(tuple(tuple(pair) for pair in pairs))

    def accept(self):
        editor = next((spin for spin in self.table.findChildren(QSpinBox) if spin.isVisible()), None)
        if self.table.state() == QAbstractItemView.State.EditingState and editor is not None:
            if not editor.hasAcceptableInput():
                QMessageBox.warning(self, "Bone Effects", "Enter an index between −2,147,483,648 and 2,147,483,647.")
                return
            editor.interpretText()
            self.table.commitData(editor)
        self.attachments = self.pairs()
        super().accept()


class MonEditor(SkillSizedDialog):
    def __init__(self, parent, record):
        show_preview = parent.preview_action.isChecked()
        super().__init__(parent, parent.settings, "mon-editor-preview" if show_preview else "mon-editor")
        self.setWindowTitle("MON Editor")
        self.window = parent
        self.record = record
        self.parts = list(record.parts)
        self.bone_effects = record.bone_effects
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 18, 12)
        self.body = QHBoxLayout()
        layout.addLayout(self.body, 1)
        form = QVBoxLayout()
        self.body.addLayout(form, 1)
        header = QFormLayout()
        self.name = QLineEdit(record.display_name)
        header.addRow("Name:", self.name)
        self.meshes = QTableWidget(0, 4)
        self.meshes.setHorizontalHeaderLabels(("Index", "Vertices", "Textures", "Format"))
        configure_table(self.meshes)
        self.meshes.setMinimumHeight(144)
        for column, width in enumerate((46, 138, 138, 52)):
            self.meshes.setColumnWidth(column, width)
        self.meshes.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.meshes.customContextMenuRequested.connect(self.mesh_menu)
        self.meshes.cellDoubleClicked.connect(lambda row, _: self.edit_part(row))
        QShortcut(QKeySequence.StandardKey.Delete, self.meshes, activated=self.remove_selected_part)
        header.addRow("3D files:", self.meshes)
        form.addLayout(header)

        row = QHBoxLayout()
        row.addWidget(QLabel("Height:"))
        height = record.height
        places = 4 if height < 1 else 3 if height < 100 else 2 if height < 1000 else 1
        self.height_display = f"{height:.{places}f}" if isfinite(height) else str(height)
        self.height_edit = QLineEdit(self.height_display)
        self.height_edit.setFixedWidth(80)
        self.height_edit.setCursorPosition(0)
        self.height_edit.setToolTip(str(height))
        row.addWidget(self.height_edit)
        row.addSpacing(10)
        row.addWidget(QLabel("Unkn:"))
        self.flags = QSpinBox(maximum=255, value=record.flags)
        row.addWidget(self.flags)
        row.addStretch()
        add = QPushButton("Add files")
        add.clicked.connect(self.add_part)
        row.addWidget(add)
        form.addLayout(row)

        mappings = QHBoxLayout()
        animations, wave_effects = QVBoxLayout(), QVBoxLayout()
        animations.setSpacing(4)
        wave_effects.setSpacing(4)
        self.animation_fields = self.add_slots(animations, "Animations:", ANIMATIONS, record.animations)
        self.wave_fields = self.add_slots(wave_effects, "Waves:", WAVES, record.waves)
        self.effect_fields = self.add_slots(wave_effects, "Effects:", EFFECTS, record.effects)
        self.effect_fields[-1].setEnabled(parent.signature == b"MO4")
        self.add_effect_pickers()
        self.bone_button = QPushButton()
        self.update_bone_button()
        self.bone_button.clicked.connect(self.edit_bone_effects)
        wave_effects.addWidget(self.bone_button, 0, Qt.AlignmentFlag.AlignLeft)
        mappings.addLayout(animations)
        mappings.addLayout(wave_effects)
        animations.addStretch()
        form.addLayout(mappings)
        self.preview = MonPreview(270, 396, self)
        self.preview.set_assets(parent.assets, parent.preview.kind, parent.preview.effects_root)
        self.body.addWidget(self.preview)
        self.preview.setVisible(show_preview)
        self.animation_fields[6].editingFinished.connect(self.refresh_preview)
        self.update_parts()
        dialog_buttons(self, layout)
        self.restore_size(QSize(790 if show_preview else 520, 552))

    def add_effect_pickers(self):
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        for index, control in enumerate(self.effect_fields):
            control.editingFinished.connect(lambda slot=index: self.refresh_preview(action=slot))
            action = control.addAction(icon, QLineEdit.ActionPosition.TrailingPosition)
            action.setToolTip("Browse for an EFT file")
            action.triggered.connect(lambda checked=False, slot=index: self.choose_effect(slot))
        self.effect_fields[-1].setToolTip("Attach is available in MO4.")

    def choose_effect(self, index):
        folder = find_child(self.window.preview.effects_root, "Effect")
        filename, _ = QFileDialog.getOpenFileName(self, "Select an EFT File", str(folder), "Effects (*.eft *.EFT)")
        if filename:
            path = Path(filename)
            if not folder.is_dir() or path.parent.resolve() == folder.resolve():
                self.effect_fields[index].setText(path.name)
                self.refresh_preview(action=index)
            else:
                QMessageBox.warning(self, "Select an EFT File", f"Select a file from\n{folder}")

    def update_bone_button(self):
        self.bone_button.setText(f"Bone Effects… ({len(self.bone_effects)})")

    def edit_bone_effects(self):
        dialog = BoneEffectsDialog(self, self.bone_effects)
        show_preview = self.preview.isVisible()
        previous_mode = self.preview.effect_mode.currentData()
        if show_preview:
            dialog.body.addWidget(self.preview)
            dialog.effects_changed.connect(
                lambda pairs: self.refresh_preview(attachments=pairs, action=BONE_EFFECTS),
            )
            self.preview.show()
            dialog.resize(690, 480)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted:
            self.bone_effects = dialog.attachments
            self.update_bone_button()
        if show_preview:
            self.body.addWidget(self.preview)
            self.preview.show()
            self.refresh_preview(action=None if accepted else previous_mode)
        dialog.deleteLater()

    @staticmethod
    def add_slots(layout, title, names, values):
        heading = QLabel(title)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        rows = QFormLayout()
        rows.setVerticalSpacing(3)
        controls = []
        for name, value in zip(names, values):
            field = QLineEdit(slot_text(value))
            field.setMinimumWidth(155)
            controls.append(field)
            rows.addRow(name + ":", field)
        layout.addLayout(rows)
        return controls

    def mesh_version(self, mesh):
        return self.window.assets.skin_version(decode_text(mesh))

    def update_parts(self, selected=None):
        self.meshes.setRowCount(len(self.parts))
        for row, (mesh, texture) in enumerate(self.parts):
            for column, text in enumerate((str(row), decode_text(mesh), decode_text(texture), self.mesh_version(mesh))):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.meshes.setItem(row, column, item)
        if selected is not None and self.parts:
            self.meshes.selectRow(min(selected, len(self.parts) - 1))
        self.refresh_preview()

    def mesh_menu(self, point):
        index = self.meshes.indexAt(point)
        menu = QMenu(self.meshes)
        if index.isValid():
            menu.addAction("Edit files…", lambda: self.edit_part(index.row()))
            menu.addAction("Remove", lambda: self.remove_part(index.row()))
        menu.addAction("Add files…", self.add_part)
        menu.exec(self.meshes.viewport().mapToGlobal(point))

    def add_part(self):
        self.edit_part(len(self.parts))

    def edit_part(self, row):
        pairing = VertexTextureDialog(self, self.window.assets.root, self.parts[row] if row < len(self.parts) else (b"", b""))
        if pairing.exec() == QDialog.DialogCode.Accepted:
            self.parts[row:row + 1] = [pairing.pair]
            self.update_parts(row)
        pairing.deleteLater()

    def remove_part(self, row):
        if QMessageBox.question(self, "Remove 3D Files", "Remove this mesh and texture pair from the model?") == QMessageBox.StandardButton.Yes:
            self.parts.pop(row)
            self.update_parts(row)

    def remove_selected_part(self):
        row = self.meshes.currentRow()
        if row >= 0:
            self.remove_part(row)

    @staticmethod
    def read_slots(fields, values):
        return tuple(text_bytes(field.text(), raw, slot=True) for field, raw in zip(fields, values))

    def refresh_preview(self, *, attachments=None, action=None):
        try:
            animations = self.read_slots(self.animation_fields, self.record.animations)
            effects = self.read_slots(self.effect_fields, self.record.effects)
        except ValueError:
            return
        record = replace(self.record, parts=tuple(self.parts), animations=animations, effects=effects,
                         bone_effects=self.bone_effects if attachments is None else attachments)
        self.preview.set_record(record, self.window.source.name, action=action)

    def accept(self):
        try:
            height = self.height_edit.text().strip()
            height_bytes = self.record.height_bytes if height == self.height_display else self.record.edit_height(height)
            self.result_record = replace(
                self.record, name=text_bytes(self.name.text(), self.record.name), flags=self.flags.value(),
                height_bytes=height_bytes, parts=tuple(self.parts),
                animations=self.read_slots(self.animation_fields, self.record.animations),
                waves=self.read_slots(self.wave_fields, self.record.waves),
                effects=self.read_slots(self.effect_fields, self.record.effects),
                bone_effects=self.bone_effects,
            )
        except ValueError as error:
            QMessageBox.warning(self, "MON Editor", str(error))
            return
        super().accept()


class MonList(QAbstractTableModel):
    HEADERS = ("Index", "Name", "# Vert.", "# Text.", "Height", "Unk1", "# Eff.", "Effects")
    WIDTHS = (40, 150, 70, 70, 70, 50, 70, 100)

    def __init__(self, parent):
        super().__init__(parent)
        self.rows = ()
        self.effect_details = ()

    def reset(self, records):
        rows, details = [], []
        for index, row in enumerate(records):
            bindings = [f"Bone {bone} → emitter {emitter}" for bone, emitter in row.bone_effects]
            files = [(action, effect) for action, raw in zip(EFFECTS, row.effects) if (effect := slot_text(raw))]
            preview = [f"{bone}→{emitter}" for bone, emitter in row.bone_effects] + [name for _, name in files]
            details.append("\n".join([*bindings, *(f"{action} · {name}" for action, name in files)]))
            rows.append((index, row.display_name, len(row.parts), len(row.parts), row.height, row.flags,
                         len(preview), ", ".join(preview)))
        self.beginResetModel()
        self.rows = tuple(rows)
        self.effect_details = tuple(details)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal:
            if role == DISPLAY:
                return self.HEADERS[section]
            if role == Qt.ItemDataRole.TextAlignmentRole and section in (0, 1, 7):
                return (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter).value

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return
        value = self.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.ToolTipRole and index.column() in (6, 7):
            return self.effect_details[index.row()]
        if role == DISPLAY or role == Qt.ItemDataRole.ToolTipRole:
            return f"{value:.4g}" if isinstance(value, float) else value
        if role == Qt.ItemDataRole.UserRole:
            return value
        if role == Qt.ItemDataRole.TextAlignmentRole and 2 <= index.column() <= 6:
            return Qt.AlignmentFlag.AlignCenter.value


class MonWindow(QMainWindow):
    MODEL_MIME = "application/x-gx-editor-mon-record"

    def __init__(self, studio):
        super().__init__(studio, Qt.WindowType.Window)
        self.studio = studio
        self.settings = studio.settings
        self.source = self.catalog = self.assets = None
        self.signature = b"MO2"
        self.records = []
        self.resize(990, 510)
        file = self.menuBar().addMenu("&File")
        self.save_action = file.addAction("&Save", "Ctrl+S", self.save)
        self.export_action = file.addAction("Export Copy…", self.export)
        self.reload_action = file.addAction("&Reload…", self.reload)
        self.format_menu = file.addMenu("MON Format")
        self.formats = {fmt: self.format_menu.addAction(label, lambda checked=False, fmt=fmt: self.set_format(fmt))
                        for fmt, label in ((b"MO2", "MO2"), (b"MO4", "MO4"))}
        for action in self.formats.values():
            action.setCheckable(True)
        file.addSeparator()
        file.addAction("&Close", "Ctrl+W", self.close)
        edit = self.menuBar().addMenu("&Edit")
        self.edit_action = edit.addAction("Edit Model…", "Return", self.edit_record)
        self.add_action = edit.addAction("New Model…", "Ctrl+N", self.add_record)
        self.copy_action = edit.addAction("Copy Model", "Ctrl+C", self.copy_record)
        self.paste_action = edit.addAction("Paste Model…", "Ctrl+V", self.paste_record)
        self.duplicate_action = edit.addAction("Duplicate Model…", self.duplicate)
        self.clear_action = edit.addAction("Clear Model…", self.clear_record)
        self.remove_action = edit.addAction("Delete Last Model…", "Delete", self.remove_record)
        self.up_action = edit.addAction("Move Up…", lambda: self.move_record(-1))
        self.down_action = edit.addAction("Move Down…", lambda: self.move_record(1))
        view = self.menuBar().addMenu("&Views")
        self.preview_action = view.addAction("3D Preview")
        self.preview_action.setCheckable(True)
        self.preview_action.setChecked(self.settings.value("mon/preview", True, type=bool))

        content = QWidget()
        layout = QHBoxLayout(content)
        layout.setContentsMargins(5, 4, 5, 5)
        table_column = QVBoxLayout()
        layout.addLayout(table_column, 1)
        self.table = QTableView()
        configure_table(self.table)
        self.model = MonList(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table.doubleClicked.connect(lambda _: self.edit_record())
        self.table.selectionModel().currentChanged.connect(self.selection_changed)
        for column, width in enumerate(MonList.WIDTHS):
            self.table.setColumnWidth(column, width)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        table_column.addWidget(self.table, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find by name or index")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.search.textChanged.connect(self.update_actions)
        table_column.addWidget(self.search)
        self.preview = MonPreview(315, 396, self)
        layout.addWidget(self.preview)
        self.preview.setVisible(self.preview_action.isChecked())
        self.preview_action.toggled.connect(self.set_preview_visible)
        self.setCentralWidget(content)
        QApplication.clipboard().dataChanged.connect(self.update_actions)
        self.update_actions()

    def load(self, source, catalog):
        same = source == self.source
        selected = self.selected() if same else None
        if not same:
            self.search.clear()
            self.assets = ModelAssets(source.parent)
            vehicle = is_vehicle_mon(source)
            root = source.parent
            if root.name.casefold() in ("monster", "npc", "vehicle"):
                root = root.parent
            elif self.studio.client_data is not None:
                root = self.studio.client_data
            self.preview.set_assets(self.assets, "mount" if vehicle else "npc", root)
        self.source, self.catalog, self.signature = source, catalog, catalog.signature
        self.records = list(catalog.records)
        if selected is None and self.records:
            selected = next((i for i, row in enumerate(self.records) if row.parts), 0)
        self.refresh(selected)

    def clear(self):
        self.close()
        self.source = self.catalog = self.assets = None
        self.records.clear()
        self.preview.set_assets(None, "npc")
        self.refresh()

    def changed(self):
        return self.catalog is not None and (self.signature != self.catalog.signature or tuple(self.records) != self.catalog.records)

    def selected(self):
        index = self.proxy.mapToSource(self.table.currentIndex())
        return index.row() if index.isValid() else None

    def refresh(self, selected=None):
        self.model.reset(self.records)
        if selected is not None and 0 <= selected < len(self.records):
            index = self.proxy.mapFromSource(self.model.index(selected, 0))
            if index.isValid():
                self.table.selectRow(index.row())
        self.selection_changed()

    def selection_changed(self, *_):
        selected = self.selected()
        record = self.records[selected] if selected is not None and selected < len(self.records) else None
        self.preview.set_record(record, self.source.name if self.source else "", reset_angle=True)
        self.update_actions()

    def update_actions(self, *_):
        loaded = self.catalog is not None and self.studio.task is None
        selected = self.selected()
        active = loaded and selected is not None
        self.save_action.setEnabled(loaded and self.changed())
        for action in (self.export_action, self.reload_action, self.add_action, self.format_menu.menuAction()):
            action.setEnabled(loaded)
        for action in (self.edit_action, self.copy_action, self.duplicate_action, self.clear_action):
            action.setEnabled(active)
        clipboard = QApplication.clipboard().mimeData()
        self.paste_action.setEnabled(loaded and clipboard is not None and clipboard.hasFormat(self.MODEL_MIME))
        self.remove_action.setEnabled(active and selected == len(self.records) - 1)
        self.up_action.setEnabled(active and selected > 0)
        self.down_action.setEnabled(active and selected < len(self.records) - 1)
        for fmt, action in self.formats.items():
            action.setChecked(self.signature == fmt)
        if self.source:
            self.setWindowTitle(self.source.name + (" *" if self.changed() else ""))
            self.statusBar().showMessage(f"{self.signature.decode()}   ·   {len(self.records):,} models   ·   {self.source}")
        else:
            self.setWindowTitle("MON Models")
            self.statusBar().clearMessage()

    def context_menu(self, point):
        index = self.table.indexAt(point)
        if index.isValid():
            self.table.selectRow(index.row())
        menu = QMenu(self)
        menu.addActions((self.edit_action, self.add_action, self.copy_action, self.paste_action,
                         self.duplicate_action, self.clear_action, self.remove_action))
        menu.addSeparator()
        menu.addActions((self.up_action, self.down_action, self.preview_action))
        menu.exec(self.table.viewport().mapToGlobal(point))

    def set_preview_visible(self, visible):
        self.preview.setVisible(visible)
        self.settings.setValue("mon/preview", visible)

    def set_format(self, signature):
        self.signature = signature
        self.update_actions()

    def edit_record(self):
        if (selected := self.selected()) is not None:
            self.open_editor(selected, self.records[selected])

    def add_record(self):
        self.open_editor(len(self.records), MonRecord())

    def copy_record(self):
        selected = self.selected()
        if selected is not None:
            mime = QMimeData()
            mime.setData(self.MODEL_MIME, self.catalog.encode((self.records[selected],), b"MO4"))
            QApplication.clipboard().setMimeData(mime)

    def paste_record(self):
        if not self.paste_action.isEnabled():
            return
        payload = QApplication.clipboard().mimeData().data(self.MODEL_MIME)
        try:
            record, = MonCatalog(bytes(payload)).records
        except ValueError as error:
            QMessageBox.warning(self, "Paste Model", str(error))
            return
        self.open_editor(len(self.records), record)

    def duplicate(self):
        if (selected := self.selected()) is not None:
            self.open_editor(len(self.records), self.records[selected])

    def open_editor(self, index, record):
        form = MonEditor(self, record)
        self.preview.set_paused(True)
        try:
            if form.exec() == QDialog.DialogCode.Accepted:
                self.records[index:index + 1] = [form.result_record]
                self.refresh(index)
        finally:
            form.deleteLater()
            self.preview.set_paused(False)

    def confirm(self, title, message):
        return QMessageBox.question(self, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes

    def clear_record(self):
        selected = self.selected()
        if selected is not None and self.confirm("Clear Model", f"Clear model {selected} and keep its index?"):
            self.records[selected] = MonRecord(flags=0)
            self.refresh(selected)

    def remove_record(self):
        selected = self.selected()
        if selected is not None and selected == len(self.records) - 1 and self.confirm("Delete Model", f"Delete the last model, index {selected}?"):
            self.records.pop()
            self.refresh(len(self.records) - 1)

    def move_record(self, delta):
        selected = self.selected()
        if selected is None:
            return
        other = selected + delta
        if 0 <= other < len(self.records) and self.confirm(
                "Move Model", f"Swap indices {selected} and {other}? Other GX files may refer to these indices."):
            self.records[selected], self.records[other] = self.records[other], self.records[selected]
            self.refresh(other)

    def confirm_format(self):
        if self.signature == b"MO2" and any(slot_text(record.effects[-1]) for record in self.records):
            return self.confirm("Save MO2", "MO2 has no Attach effect slot. Continue with the selected format?")
        return True

    def save(self):
        if not self.save_action.isEnabled() or not self.confirm_format():
            return
        box = QMessageBox(QMessageBox.Icon.Question, "Save MON", "Save the staged model edits?", parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        backups = self.studio.backup_folder()
        box.setInformativeText(f"{self.source}\n\nDated backups\n{backups}")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Save:
            return
        source, catalog, records, signature = self.source, self.catalog, tuple(self.records), self.signature

        def saved(result):
            catalog, backup = result
            self.load(source, catalog)
            ModelAssets.npc_choices.cache_clear()
            ModelAssets.choices.cache_clear()
            self.studio.update_catalog_nodes()
            QMessageBox.information(self, "Save MON", f"Saved {source.name}.\n\nBackup\n{backup}")

        self.studio.run_task("Saving MON…", lambda: save_mon(source, catalog, records, signature, backups), saved)

    def export(self):
        if not self.export_action.isEnabled() or not self.confirm_format():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export MON Copy", str(self.source.with_name(self.source.stem + "-copy.MON")), "MON files (*.mon *.MON)",
        )
        if not filename:
            return
        destination = Path(filename).resolve()
        if destination == self.source or (destination.exists() and self.source.exists() and self.source.samefile(destination)):
            QMessageBox.warning(self, "Export MON Copy", "Select a separate file for the copy.")
            return
        catalog, records, signature = self.catalog, tuple(self.records), self.signature
        self.studio.run_task("Exporting MON…",
                             lambda: replace_file(destination, catalog.encode(records, signature), allow_create=True),
                             lambda _: QMessageBox.information(self, "Export MON Copy", f"Exported to\n{destination}"))

    def reload(self):
        if self.source is not None:
            self.studio.open_mon(self.source, reload=True)

    def closeEvent(self, event):
        if self.isVisible():
            self.settings.setValue("mon/geometry", self.saveGeometry())
            self.settings.sync()
        super().closeEvent(event)

    def restore_window(self):
        geometry = self.settings.value("mon/geometry", QByteArray(), type=QByteArray)
        return not geometry.isEmpty() and self.restoreGeometry(geometry)
