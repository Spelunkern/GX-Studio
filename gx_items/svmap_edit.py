"""Validate and stage edits to authored server-map records."""

from dataclasses import dataclass, replace
from math import isfinite
import re
from struct import Struct, error as StructError

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .mon_view import MonPreview, configure_table
from .safe_zones import SafeZone
from .svmap import BOUNDS, MONSTER, NAMED, PATROL, POINT, PORTAL, MonsterArea, NPC, SpawnArea

POINT_FIELDS = ("X", "Y", "Z")
BOX_FIELDS = ("X1", "Y1", "Z1", "X2", "Y2", "Z2")


@dataclass(frozen=True, slots=True)
class RecordSpec:
    attribute: str
    columns: tuple[str, ...]
    codec: Struct
    nested_columns: tuple[str, ...] = ()
    nested_codec: Struct | None = None
    record_type: type | None = None
    nested_title: str = ""


SPECS = {
    "Ladders": RecordSpec("ladders", POINT_FIELDS, POINT),
    "Monster Areas": RecordSpec("monsters", BOX_FIELDS, BOUNDS, ("MobID", "Count"), MONSTER,
                                MonsterArea, "Monsters in area"),
    "Named Areas": RecordSpec("named", (*BOX_FIELDS, "Name mode", "Parameter"), NAMED),
    "NPC": RecordSpec("npcs", ("Type", "Type ID"), Struct("<ii"), (*POINT_FIELDS, "Orientation (rad)"), PATROL,
                      NPC, "Positions in patrol order"),
    "Portals": RecordSpec("portals", (*POINT_FIELDS, "Faction / portal ID", "Minimum level", "Maximum level",
                                      "Target map", "Target X", "Target Y", "Target Z"), PORTAL),
    "Spawns": RecordSpec("spawns", ("Faction", "Parameter"), Struct("<ii"), BOX_FIELDS, BOUNDS,
                         SpawnArea, "Spawn boxes"),
}


def blank(codec):
    return codec.unpack(bytes(codec.size))


def parse_fields(codec, labels, fields):
    values = []
    codes = (code for count, code in re.findall(r"(\d*)([fiIHB])", codec.format)
             for _ in range(int(count or "1")))
    for label, code, entry in zip(labels, codes, fields, strict=True):
        try:
            value = float(entry.text()) if code == "f" else int(entry.text())
            if isinstance(value, float) and not isfinite(value):
                raise ValueError
            Struct("<" + code).pack(value)
        except (ValueError, OverflowError, StructError) as error:
            raise ValueError(f"Enter a valid value for {label}.") from error
        values.append(value)
    # Normalize floats to the precision stored in the file.
    return codec.unpack(codec.pack(*values))


def input_text(value):
    return format(value, ".9g") if isinstance(value, float) else str(value)


class MobPreview(MonPreview):
    def __init__(self, context, parent):
        super().__init__(188, 156, parent)
        self.monsters, (assets, self.models) = context
        self.set_assets(assets, "npc", assets.root.parent if assets else None)

    def select_mob(self, mob_id):
        row = self.monsters.get(mob_id)
        model = row.get("Model") if row else None
        record = self.models[model] if isinstance(model, int) and 0 <= model < len(self.models) else None
        self.set_record(record, "monster.MON", reset_angle=True)
        if not self.choice:
            self.missing.setText("Monster model unavailable" if mob_id is not None else "")


class MobSelector(QWidget):
    def __init__(self, mob_field, context, parent):
        super().__init__(parent)
        monsters, _ = context
        layout = QVBoxLayout(self)
        search = QComboBox()
        search.setEditable(True)
        search.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for mob_id, row in sorted(monsters.items()):
            if row["Name"] and row.get("Level"):
                search.addItem(f"{mob_id} · {row['Name']}", mob_id)
        search.setCurrentIndex(-1)
        search.lineEdit().setPlaceholderText("Search by monster name or ID")
        completer = QCompleter(search.model(), search)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        search.setCompleter(completer)
        layout.addWidget(search)
        preview = MobPreview(context, self)
        preview.setObjectName("selectedMonsterPreview")
        parent.finished.connect(preview.stop)
        layout.addWidget(preview)

        def select_mob():
            text = mob_field.text().strip()
            preview.select_mob(int(text) if text.isascii() and text.isdecimal() and len(text) <= 10 else None)

        def adopt(index):
            if index >= 0 and (mob_id := search.itemData(index)) is not None:
                mob_field.setText(str(mob_id))

        search.activated.connect(adopt)
        completer.activated[str].connect(lambda text: adopt(search.findText(text)))
        mob_field.textChanged.connect(select_mob)
        select_mob()


class MapRecordDialog(QDialog):
    def __init__(self, title, labels, codec, values, parent, *, spec=None, nested=(), mob_context=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.labels, self.codec = labels, codec
        self.nested = list(nested)
        self.spec = spec
        self.mob_context = mob_context
        self.fields = []
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        for label, value in zip(labels, values):
            entry = QLineEdit(input_text(value))
            self.fields.append(entry)
            form.addRow(label, entry)
        if codec is MONSTER and mob_context is not None:
            layout.addWidget(MobSelector(self.fields[0], mob_context, self))
        if spec and spec.nested_codec:
            layout.addWidget(QLabel(spec.nested_title))
            self.table = QTableWidget()
            configure_table(self.table)
            self.table.setColumnCount(len(spec.nested_columns))
            self.table.setHorizontalHeaderLabels(spec.nested_columns)
            self.table.cellDoubleClicked.connect(lambda *_: self.update_nested("edit"))
            layout.addWidget(self.table)
            actions = QHBoxLayout()
            for title, operation in (("Add", "add"), ("Edit", "edit"), ("Remove", "remove")):
                button = QPushButton(title)
                button.setAutoDefault(False)
                button.clicked.connect(lambda _, operation=operation: self.update_nested(operation))
                actions.addWidget(button)
            actions.addStretch(1)
            layout.addLayout(actions)
            self.refresh_nested()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(440, self.sizeHint().height())

    def refresh_nested(self, row=0):
        self.table.setRowCount(len(self.nested))
        for r, values in enumerate(self.nested):
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(input_text(value)))
        self.table.resizeColumnsToContents()
        if self.nested:
            self.table.selectRow(min(row, len(self.nested) - 1))

    def update_nested(self, operation):
        row = self.table.currentRow()
        if operation != "add" and row < 0:
            return
        if operation == "remove":
            self.nested.pop(row)
        else:
            codec = self.spec.nested_codec
            values = blank(codec) if operation == "add" else self.nested[row]
            dialog = MapRecordDialog("Map record", self.spec.nested_columns, codec, values, self,
                                     mob_context=self.mob_context)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if operation == "add":
                row = len(self.nested)
                self.nested.append(dialog.values)
            else:
                self.nested[row] = dialog.values
        self.refresh_nested(row)

    def accept(self):
        try:
            self.values = parse_fields(self.codec, self.labels, self.fields)
            if self.spec is SPECS["NPC"] and not (0 <= self.values[0] <= 255 and 0 <= self.values[1] <= 65535):
                raise ValueError("NPC type must fit in 0–255 and the type ID in 0–65,535.")
            if self.spec is SPECS["NPC"] and not self.nested:
                raise ValueError("Add at least one position for the NPC.")
            if self.spec is SPECS["Portals"] and ((self.values[5] and self.values[4] > self.values[5])
                                                   or self.values[6] > 65535):
                raise ValueError("Check the level range and target map ID.")
        except ValueError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        super().accept()


def edit_map_record(data, title, index, operation, parent, *, drawn=None, mob_context=None):
    spec = SPECS[title]
    entries = list(getattr(data, spec.attribute))
    if operation == "remove":
        entries.pop(index)
    else:
        entry = entries[index] if operation == "edit" else None
        if entry is None:
            values, nested = blank(spec.codec), ()
        elif spec.record_type is MonsterArea:
            values, nested = entry.bounds, entry.spawns
        elif spec.record_type is NPC:
            values, nested = (entry.kind, entry.identifier), entry.patrol
        elif spec.record_type is SpawnArea:
            values, nested = (entry.faction, entry.auxiliary), entry.boxes
        else:
            values, nested = entry, ()
        if drawn is not None:
            x1, z1, x2, z2 = drawn
            values = (x1, values[1], z1, x2, values[4], z2)
        dialog = MapRecordDialog(title, spec.columns, spec.codec, values, parent, spec=spec, nested=nested,
                                 mob_context=mob_context)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return data
        values = dialog.values
        if spec.record_type is MonsterArea:
            values = MonsterArea(values, tuple(dialog.nested))
        elif spec.record_type:
            values = spec.record_type(*values, tuple(dialog.nested))
        if operation == "add":
            entries.append(values)
        else:
            entries[index] = values
    return replace(data, **{spec.attribute: tuple(entries)})


class SafeZoneDialog(QDialog):
    def __init__(self, zone, parent):
        super().__init__(parent)
        self.setWindowTitle("Safe Zone")
        self.zone = zone
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.name = QLineEdit(zone.name)
        self.faction = QComboBox()
        self.faction.addItems(("Light", "Fury", "Both"))
        self.faction.setCurrentText(zone.faction)
        form.addRow("Name", self.name)
        form.addRow("Protected faction", self.faction)
        self.fields = []
        for index, point in enumerate(zone.points, 1):
            row = QHBoxLayout()
            for axis, value in zip(("X", "Z"), point):
                row.addWidget(QLabel(axis))
                field = QLineEdit(input_text(value))
                self.fields.append(field)
                row.addWidget(field)
            form.addRow(f"Vertex {index}", row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            values = parse_fields(Struct("<8f"), ("X", "Z") * 4, self.fields)
            points = tuple(zip(values[::2], values[1::2]))
            self.zone = SafeZone(self.name.text(), self.zone.map_id, self.faction.currentText(), points).validated()
        except ValueError as error:
            QMessageBox.warning(self, "Safe Zone", str(error))
            return
        super().accept()
