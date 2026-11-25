"""Monster editor with independent client and SQLite saves."""

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QAbstractTableModel, QByteArray, QEvent, QModelIndex, QSize, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTabWidget, QTableView,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .equipment_models import ModelAssets
from .install import save_model_table
from .mon_view import MonPreview, configure_table, dialog_buttons
from .monster_data import (
    CLIENT_FIELDS, CLIENT_BY_KEY, DB_FIELDS, DB_BY_KEY, DROPS, ELEMENTS, TABLE, U16, MonsterCatalog, drop_rate_max,
    read_monster_db, save_mobs,
)
from .skill_sheet import SkillSizedDialog

DISPLAY = Qt.ItemDataRole.DisplayRole
CLIENT, SQLITE = 0, 1
STATES = ("Sleep", "Stun", "Silence", "Darkness", "Pause", "Slow", "Dying", "Death", "Poison", "Illness",
          "Delusion", "Doom", "Fear", "Dull", "Bad Luck")


def compact_grid(parent):
    grid = QGridLayout(parent)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(7)
    grid.setVerticalSpacing(4)
    return grid


class DropPanel(QWidget):
    def __init__(self, parent, stored, editable, items):
        super().__init__(parent)
        self.stored = stored
        self.editable = editable and stored is not None
        self.items = items
        self.edits = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.addWidget(QLabel("Drop Bags List  ·  SQLite"))
        row = QHBoxLayout()
        layout.addLayout(row)
        self.bags = QTableWidget(9, 3)
        configure_table(self.bags)
        self.bags.setHorizontalHeaderLabels(("Slot", "Grade", "Rate %"))
        self.bags.verticalHeader().setMinimumSectionSize(19)
        self.bags.verticalHeader().setDefaultSectionSize(19)
        self.bags.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate((33, 44, 58)):
            self.bags.setColumnWidth(col, width)
        self.bags.setMinimumWidth(148)
        self.bags.setFixedHeight(206)
        row.addWidget(self.bags, 1)
        controls = QVBoxLayout()
        row.addLayout(controls)
        form = QFormLayout()
        controls.addLayout(form)
        self.grade = QLineEdit()
        self.rate = QLineEdit()
        self.grade.setMaximumWidth(73)
        self.rate.setMaximumWidth(73)
        form.addRow("Grade", self.grade)
        form.addRow("Rate %", self.rate)
        self.set_button = QPushButton("Set")
        self.set_button.setAutoDefault(False)
        self.set_button.clicked.connect(self.apply_slot)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setAutoDefault(False)
        self.clear_button.clicked.connect(self.clear_slot)
        controls.addWidget(self.set_button)
        controls.addWidget(self.clear_button)
        self.limits = QLabel()
        self.limits.setWordWrap(True)
        controls.addWidget(self.limits)
        controls.addStretch(1)
        row.setAlignment(controls, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(QLabel("Items in Grade"))
        self.grade_items = QTableWidget(0, 2)
        configure_table(self.grade_items)
        self.grade_items.setHorizontalHeaderLabels(("Item ID", "Name"))
        self.grade_items.setColumnWidth(0, 61)
        self.grade_items.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.grade_items, 1)
        self.bags.currentCellChanged.connect(self.select_slot)
        for slot in range(1, 10):
            self.update_slot(slot)
        self.bags.setCurrentCell(0, 0)

    @property
    def slot(self):
        return self.bags.currentRow() + 1

    def entry(self, slot):
        saved = (self.stored or {}).get(slot, ())
        return self.edits.get(slot, saved[0] if saved else (0, 0))

    def rate_text(self, slot, rate):
        return format(Decimal(rate) / (drop_rate_max(slot) // 100), "f")

    def update_slot(self, slot):
        grade, rate = self.entry(slot)
        for col, value in enumerate((str(slot), str(grade), self.rate_text(slot, rate))):
            item = QTableWidgetItem(value)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.bags.setItem(slot - 1, col, item)

    def select_slot(self, *_):
        slot = self.slot
        if not slot:
            return
        grade, rate = self.entry(slot)
        self.grade.setText(str(grade))
        self.rate.setText(self.rate_text(slot, rate))
        duplicate = len((self.stored or {}).get(slot, ())) > 1
        for widget in (self.grade, self.rate, self.set_button, self.clear_button):
            widget.setEnabled(self.editable and not duplicate)
        step = "1%" if slot < 7 else "0.0001%"
        self.limits.setText("Duplicate SQLite rows" if duplicate else f"Grade 0–65,535\n0–100% · steps of {step}")
        items = self.items.get(grade, ())
        self.grade_items.setRowCount(len(items))
        for row, (item_id, name) in enumerate(items):
            self.grade_items.setItem(row, 0, QTableWidgetItem(str(item_id)))
            self.grade_items.setItem(row, 1, QTableWidgetItem(name))

    def set_slot(self, entry):
        slot = self.slot
        if not slot:
            return
        saved = (self.stored or {}).get(slot, ())
        if entry == (saved[0] if saved else (0, 0)):
            self.edits.pop(slot, None)
        else:
            self.edits[slot] = entry
        self.update_slot(slot)
        self.select_slot()

    def apply_slot(self):
        slot = self.slot
        try:
            grade = int(self.grade.text())
            if not 0 <= grade <= U16:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Drop Grade", f"Enter a grade from 0 to {U16:,}.")
            return False
        try:
            chance = Decimal(self.rate.text())
            step = Decimal("1") if slot < 7 else Decimal("0.0001")
            if not chance.is_finite() or not 0 <= chance <= 100 or chance.quantize(step) != chance:
                raise InvalidOperation
        except InvalidOperation:
            QMessageBox.warning(self, "Drop Chance", f"Enter 0–100% in steps of {step}%.")
            return False
        self.set_slot((grade, int(chance * (drop_rate_max(slot) // 100))))
        return True

    def clear_slot(self):
        self.set_slot((0, 0))

    def finish(self):
        slot = self.slot
        if slot and self.set_button.isEnabled():
            grade, rate = self.entry(slot)
            if (self.grade.text(), self.rate.text()) != (str(grade), self.rate_text(slot, rate)):
                return self.apply_slot()
        return True


class MonsterDialog(SkillSizedDialog):
    def __init__(self, window, mob_id, client, server, drops):
        super().__init__(window, window.settings, "monster-editor-reference")
        self.setWindowTitle(f"Monster Editor — MobID {mob_id}")
        self.sources = client, server
        self.controls = ([], [])
        self.field_info = {}
        self.window = window
        self.panel = None
        self.field_status = QLabel("Select a field to see its source and editable range.")
        self.field_status.setWordWrap(True)
        self.setStyleSheet(
            'QLineEdit[mobSource="sqlite"]:enabled, QComboBox[mobSource="sqlite"]:enabled { background-color: #e5eef7; }'
            'QCheckBox[mobSource="sqlite"]:enabled { color: #23517b; }'
        )

        layout = QVBoxLayout(self)
        content = QHBoxLayout()
        layout.addLayout(content, 1)
        fields = QWidget()
        content.addWidget(fields, 1)
        form = QVBoxLayout(fields)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(5)
        header = self.header()
        form.addWidget(header)
        stats = QHBoxLayout()
        stats.addWidget(self.characteristics(), 4)
        stats.addWidget(self.movement(), 3)
        form.addLayout(stats)
        form.addWidget(self.attacks())
        form.addWidget(self.immunities())
        form.addWidget(self.loots())
        form.addStretch(1)

        self.side = QStackedWidget()
        self.side.setFixedWidth(312)
        content.addWidget(self.side)
        page = QWidget()
        preview_layout = QVBoxLayout(page)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview = MonPreview(300, 405, page)
        self.preview.set_assets(window.assets, "npc", window.assets.root.parent if window.assets else None)
        preview_layout.addWidget(self.preview)
        self.side.addWidget(page)
        self.drop_panel = DropPanel(self, drops, window.can_write_sqlite(), window.grade_items)
        self.side.addWidget(self.drop_panel)
        self.side.hide()
        self.preview_button.setEnabled(bool(client and window.models))
        self.drops_button.setEnabled(drops is not None and server is not None)
        self.model_control = next((widget for field, widget in self.controls[CLIENT] if field.key == "Model"), None)
        if isinstance(self.model_control, QComboBox):
            self.model_control.currentIndexChanged.connect(self.refresh_preview)

        sources = QHBoxLayout()
        state = "connected" if window.can_write_sqlite() else "read-only" if window.database else "unavailable"
        sources.addWidget(QLabel(f"Client · Monster.SData    |    Blue fields · SQLite ({state})"), 1)
        values = QPushButton("Source Values…")
        values.setAutoDefault(False)
        values.clicked.connect(self.open_sources)
        sources.addWidget(values)
        layout.addLayout(sources)
        layout.addWidget(self.field_status)
        dialog_buttons(self, layout)
        self.source_dialog = self.build_source_dialog()
        self.restore_size(QSize(705, 580))
        if self.preview_button.isEnabled() and self.settings.value("monster/showPreview", False, type=bool):
            self.toggle_panel(0)

    def control(self, source, key, *, checkbox=False, width=None):
        field = (CLIENT_BY_KEY, DB_BY_KEY)[source][key]
        values = self.sources[source]
        value = values.get(key) if values is not None else None
        names = None
        if source == CLIENT and key == "Model" and self.window.models:
            names = [(i, f"{i} ({model.display_name})") for i, model in enumerate(self.window.models) if model.parts]
        elif source == SQLITE and key.startswith("AttackType") and self.window.skill_names:
            names = [(0, "Melee Attack"), *((i, f"{i} · {name}") for i, name in self.window.skill_names.items())]
        elif field.choices:
            names = list(enumerate(field.choices))

        if checkbox:
            widget = QCheckBox(STATES[int(key.removeprefix("ResistState")) - 1])
            widget.setChecked(bool(value))
        elif names is not None:
            widget = QComboBox()
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            for index, label in names:
                widget.addItem(label, index)
            selected = widget.findData(value)
            if selected < 0:
                widget.addItem("—" if value is None else str(value), value)
                selected = widget.count() - 1
            widget.setCurrentIndex(selected)
            widget.view().setMinimumWidth(210 if key.startswith("AttackType") else 150)
        else:
            widget = QLineEdit("" if value is None else str(value))
        if width:
            widget.setMinimumWidth(width)
            widget.setMaximumWidth(width)
        widget.setFixedHeight(20 if checkbox else 22)
        enabled = (self.window.catalog.writable if source == CLIENT else self.window.can_write_sqlite())
        widget.setEnabled(values is not None and enabled and not field.read_only)
        widget.setAccessibleName(field.label)
        widget.setProperty("mobSource", "client" if source == CLIENT else "sqlite")
        if values is not None:
            self.controls[source].append((field, widget))
        text_source = "Client · Monster.SData" if source == CLIENT else f"SQLite · {TABLE}"
        limit = "Read-only" if field.read_only else field.range_text
        info = f"{field.label} · {text_source} · {field.key} · {limit}"
        if field.note:
            info += f"  {field.note}"
        self.field_info[widget] = info
        widget.setAccessibleDescription(info)
        widget.installEventFilter(self)
        return widget

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and watched in self.field_info:
            self.field_status.setText(self.field_info[watched])
            if getattr(self, "source_dialog", None):
                self.detail_status.setText(self.field_info[watched])
        return super().eventFilter(watched, event)

    def preferred(self, key, **options):
        source = SQLITE if self.sources[SQLITE] is not None or key not in CLIENT_BY_KEY else CLIENT
        return self.control(source, key, **options)

    def group(self, title):
        widget = QWidget()
        grid = compact_grid(widget)
        heading = QLabel(f"<b>{title}</b>")
        grid.addWidget(heading, 0, 0, 1, -1)
        return widget, grid

    @staticmethod
    def pair(grid, row, col, label, control, span=1):
        caption = QLabel(label)
        caption.setBuddy(control)
        grid.addWidget(caption, row, col)
        grid.addWidget(control, row, col + 1, 1, span)

    def header(self):
        widget = QWidget()
        grid = compact_grid(widget)
        self.pair(grid, 0, 0, "Name", self.control(CLIENT if self.sources[CLIENT] is not None else SQLITE,
                                                  "Name" if self.sources[CLIENT] is not None else "MobName"), 2)
        self.pair(grid, 1, 0, "Model", self.control(CLIENT, "Model"))
        self.preview_button = QPushButton(">>")
        self.preview_button.setFixedWidth(35)
        self.preview_button.setAccessibleName("3D Preview")
        self.preview_button.setAutoDefault(False)
        self.preview_button.clicked.connect(lambda: self.toggle_panel(0))
        grid.addWidget(self.preview_button, 1, 2)
        self.pair(grid, 0, 3, "Level", self.preferred("Level", width=73))
        self.pair(grid, 1, 3, "AI", self.preferred("AI", width=73))
        grid.setColumnStretch(1, 1)
        return widget

    def characteristics(self):
        widget, grid = self.group("Characteristics")
        for row, cells in enumerate((
            (("HP", "HP"), ("SP", "SP"), ("MP", "MP")),
            (("Dex", "Dex"), ("Wis", "Wis"), ("Luc", "Luc")),
            (("Def", "Defense"), ("Resist", "Magic"), ("Elem", "Attrib")),
        ), 1):
            for column, (label, key) in enumerate(cells):
                self.pair(grid, row, column * 2, label, self.preferred(key, width=91 if key in ("HP", "SP", "MP") else 68))
        self.pair(grid, 4, 0, "Height", self.control(CLIENT, "Day", width=75))
        self.pair(grid, 4, 2, "Radius", self.preferred("Size", width=68))
        return widget

    def movement(self):
        widget, grid = self.group("Movement")
        grid.addWidget(QLabel("Normal"), 1, 1)
        grid.addWidget(QLabel("Chase"), 1, 2)
        for row, (label, suffix) in enumerate((("Delay", "Time"), ("Step", "Step")), 2):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(self.preferred(f"Normal{suffix}", width=92), row, 1)
            grid.addWidget(self.preferred(f"Chase{suffix}", width=92), row, 2)
        self.pair(grid, 4, 0, "Vision", self.control(SQLITE, "ChaseRange"), 2)
        self.pair(grid, 5, 0, "Quest Item", self.preferred("QuestItemID"), 2)
        return widget

    def attacks(self):
        widget, grid = self.group("Attacks")
        columns = (
            ("Mode", "Attackok", 78), ("Damage", "Attack", 61), ("Addit.", "Attackplus", 57),
            ("Delay", "AttackTime", 91), ("Range", "Attackrange", 48), ("Attack Type", "AttackType", None),
            ("Param.", "Attackspecial", 84), ("Element", "Attackattrib", 65),
        )
        for column, (label, _, _) in enumerate(columns, 1):
            grid.addWidget(QLabel(label), 1, column)
        for slot in range(1, 4):
            grid.addWidget(QLabel(str(slot)), slot + 1, 0)
            for column, (_, prefix, width) in enumerate(columns, 1):
                key = f"{prefix}{slot}"
                if self.sources[SQLITE] is None and prefix == "Attackok":
                    control = self.control(CLIENT, f"AttackAnimation{slot}", width=width)
                elif self.sources[SQLITE] is None and prefix == "AttackType":
                    control = self.control(CLIENT, key)
                else:
                    control = self.control(SQLITE, key, width=width)
                grid.addWidget(control, slot + 1, column)
        grid.setColumnStretch(6, 1)
        return widget

    def immunities(self):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        states, state_grid = self.group("Resistance to debuffs")
        row.addWidget(states, 1)
        for index in range(15):
            control = self.control(SQLITE, f"ResistState{index+1}", checkbox=True)
            state_grid.addWidget(control, index % 5 + 1, index // 5)
        spells, spell_grid = self.group("Resistance to spells")
        row.addWidget(spells, 1, Qt.AlignmentFlag.AlignTop)
        for index in range(6):
            self.pair(spell_grid, index // 2 + 1, (index % 2) * 2, f"#{index+1}",
                      self.control(SQLITE, f"ResistSkill{index+1}", width=61))
        spell_grid.setColumnStretch(4, 1)
        return widget

    def loots(self):
        widget, grid = self.group("Loots")
        for column, (label, key) in enumerate((("XP", "Exp"), ("Gold", "Money1"), ("Random", "Money2"))):
            self.pair(grid, 1, column * 2, label, self.control(SQLITE, key, width=90))
        self.drops_button = QPushButton("Drops >>")
        self.drops_button.setAutoDefault(False)
        self.drops_button.clicked.connect(lambda: self.toggle_panel(1))
        grid.addWidget(self.drops_button, 1, 6)
        grid.setColumnStretch(7, 1)
        return widget

    def build_source_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Monster Source Values")
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        self.detail_status = QLabel("Select a field to see its editable range.")
        self.detail_status.setWordWrap(True)
        entries = (("Client — Monster.SData", CLIENT_FIELDS), (f"SQLite — {TABLE}", DB_FIELDS))
        self.detail_controls = []
        for source, (title, fields) in enumerate(entries):
            scroll = QScrollArea()
            content = QWidget()
            form = QFormLayout(content)
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            tabs.addTab(scroll, title)
            if self.sources[source] is None:
                form.addRow(QLabel("No row available for this MobID."))
                continue
            shown = {field.key for field, _ in self.controls[source]}
            for field in fields:
                if field.key in shown:
                    continue
                control = self.control(source, field.key)
                form.addRow(f"{field.label} ({'read-only' if field.read_only else field.range_text})", control)
                self.detail_controls.append(control)
        layout.addWidget(self.detail_status)
        dialog_buttons(dialog, layout)
        dialog.resize(590, 410)
        return dialog

    def open_sources(self):
        previous = [(widget, widget.currentIndex() if isinstance(widget, QComboBox) else widget.text())
                    for widget in self.detail_controls]
        if self.source_dialog.exec() != QDialog.DialogCode.Accepted:
            for control, value in previous:
                if isinstance(control, QComboBox):
                    control.setCurrentIndex(value)
                else:
                    control.setText(value)

    def toggle_panel(self, index):
        old = self.panel
        width = self.width()
        self.panel = None if old == index else index
        self.side.setVisible(self.panel is not None)
        if self.panel is not None:
            self.side.setCurrentIndex(self.panel)
        if (old is None) != (self.panel is None):
            difference = self.side.width() + self.layout().spacing()
            self.layout().activate()
            self.resize(width + (difference if self.panel is not None else -difference), self.height())
        self.preview_button.setText("<<" if self.panel == 0 else ">>")
        self.drops_button.setText("Drops <<" if self.panel == 1 else "Drops >>")
        if index == 0:
            self.settings.setValue("monster/showPreview", self.panel == 0)
        if self.panel == 0:
            self.refresh_preview()

    def refresh_preview(self, *_):
        if not isinstance(self.model_control, QComboBox):
            return
        index = self.model_control.currentData()
        records = self.window.models
        record = records[index] if index is not None and 0 <= index < len(records) else None
        self.preview.set_record(record, "monster.MON", reset_angle=True)

    def accept(self):
        if not self.drop_panel.finish():
            return
        result = []
        try:
            for fields, previous in zip(self.controls, self.sources):
                edits = {}
                for field, control in fields:
                    if not control.isEnabled():
                        continue
                    before = previous[field.key]
                    if isinstance(control, QCheckBox):
                        if control.isChecked() == bool(before):
                            continue
                        value = int(control.isChecked())
                    elif isinstance(control, QComboBox):
                        value = control.currentData()
                        if value == before:
                            continue
                    else:
                        value = control.text()
                        if value == ("" if before is None else str(before)):
                            continue
                    validated = field.parse(value)
                    if validated != before:
                        edits[field.key] = validated
                result.append(edits)
        except ValueError as error:
            QMessageBox.warning(self, "Monster Editor", str(error))
            return
        self.result_edits = result
        self.result_drops = self.drop_panel.edits
        super().accept()

    def done(self, result):
        width = self.width() - (self.side.width() + self.layout().spacing() if self.panel is not None else 0)
        self.settings.setValue(self.size_key, QSize(max(1, width), self.height()))
        QDialog.done(self, result)


BASE_COLUMNS = (("MobID", "MobID"), ("Name", "Name"), ("Level", "Level"),
                ("Model", "Model"), ("Exp", "Exp"), ("AI", "AI"))
DEFAULT_COLUMNS = (("Height", "Day"), ("Elem", "Attrib"), ("Qst Itm", "QuestItemID"),
                   *((f"Atk Mode {slot}", f"AttackAnimation{slot}") for slot in range(1, 4)))
COLUMN_GROUPS = {
    "Statistics": (
        ("Money1", "Money1"), ("Money2", "Money2"), ("Qst Itm", "QuestItemID"), ("HP", "HP"),
        ("SP", "SP"), ("MP", "MP"), ("Dex", "Dex"), ("Wis", "Wis"), ("Luc", "Luc"),
        ("Height", "Day"), ("Size", "Size"), ("Elem", "Attrib"), ("Defense", "Defense"), ("Magic", "Magic"),
        ("Normal Time", "NormalTime"), ("Normal Step", "NormalStep"), ("Chase Time", "ChaseTime"),
        ("Chase Step", "ChaseStep"), ("Chase Range", "ChaseRange"),
    ),
    "Resistances": (
        *((f"Res.State {i}", f"ResistState{i}") for i in range(1, 16)),
        *((f"Res.Skill {i}", f"ResistSkill{i}") for i in range(1, 7)),
    ),
    "Attacks": tuple((f"Atk {slot} {label}", f"{prefix}{slot}")
                    for slot in range(1, 4) for label, prefix in (
                        ("Type", "AttackType"), ("Time", "AttackTime"), ("Range", "Attackrange"), ("Dmg", "Attack"),
                        ("Plus", "Attackplus"), ("Elem", "Attackattrib"), ("Spec", "Attackspecial"), ("Mode", "Attackok"),
                    )),
}


class MonsterListModel(QAbstractTableModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.rows = ()
        self.columns = (*BASE_COLUMNS, *DEFAULT_COLUMNS)

    def replace(self, rows, columns):
        self.beginResetModel()
        self.rows, self.columns = rows, columns
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal and role == DISPLAY:
            return self.columns[section][0]
        return None

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        mob_id, client, server = self.rows[index.row()]
        key = self.columns[index.column()][1]
        if role == Qt.ItemDataRole.TextAlignmentRole and key != "Name":
            return Qt.AlignmentFlag.AlignCenter
        if role not in (DISPLAY, Qt.ItemDataRole.ToolTipRole):
            return None
        if key == "MobID":
            value, source = mob_id, "MobID"
        elif key == "Name" and key not in client:
            value, source = server.get("MobName", ""), "SQLite"
        elif key in server and key != "Day":
            value, source = server[key], "SQLite"
        else:
            value, source = client.get(key, ""), "Monster.SData"
        if key == "Attrib" and isinstance(value, int) and 0 <= value < len(ELEMENTS):
            value = ELEMENTS[value]
        return value if role == DISPLAY else f"{value} · {source}" if value != "" else None


class MonsterWindow(QMainWindow):
    def __init__(self, studio):
        super().__init__(studio, Qt.WindowType.Window)
        self.studio, self.settings = studio, studio.settings
        self.source = self.catalog = self.assets = self.database = None
        self.models = ()
        self.skill_names, self.grade_items = {}, {}
        self.rows = {}
        self.drops = None
        self.database_status = ""
        self.client_edits, self.sql_edits, self.drop_edits = {}, {}, {}
        self.resize(840, 500)
        menu = self.menuBar().addMenu("&File")
        self.save_client_action = menu.addAction("Save Client Monster.SData…", "Ctrl+S", self.save_client)
        self.save_sql_action = menu.addAction("Save SQLite Mobs…", "Ctrl+Shift+S", self.save_sqlite)
        self.reload_action = menu.addAction("Reload Client…", self.reload_client)
        self.database_action = menu.addAction("Reload SQLite…", self.reload_sqlite)
        menu.addAction("Select SQLite…", studio.choose_database)
        menu.addSeparator()
        menu.addAction("&Close", "Ctrl+W", self.close)
        edit = self.menuBar().addMenu("&Edit")
        self.edit_action = edit.addAction("Edit Monster…", "Return", self.edit)
        view = self.menuBar().addMenu("&Views")
        self.show_empty = view.addAction("Show Empty Monsters")
        self.show_empty.setCheckable(True)
        self.show_empty.setChecked(self.settings.value("monster/showEmpty", False, type=bool))
        self.show_empty.toggled.connect(lambda checked: self.change_view("showEmpty", checked))
        view.addSeparator()
        self.groups = {}
        for name in COLUMN_GROUPS:
            action = view.addAction(name)
            action.setCheckable(True)
            action.setChecked(self.settings.value(f"monster/{name}", False, type=bool))
            action.toggled.connect(lambda checked, name=name: self.change_view(name, checked))
            self.groups[name] = action

        content = QWidget()
        layout = QVBoxLayout(content)
        self.client_label = QLabel()
        self.sql_label = QLabel()
        for label in (self.client_label, self.sql_label):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.model = MonsterListModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.table = QTableView()
        configure_table(self.table)
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        for index, width in enumerate((61, 200, 49, 53, 48, 46, 50, 49, 58, 64, 64, 64)):
            self.table.setColumnWidth(index, width)
        self.table.doubleClicked.connect(self.edit)
        self.table.selectionModel().currentChanged.connect(self.update_actions)
        layout.addWidget(self.table, 1)
        search = QLineEdit(placeholderText="Find a MobID or name", clearButtonEnabled=True)
        search.textChanged.connect(self.proxy.setFilterFixedString)
        layout.addWidget(search)
        layout.addWidget(self.client_label)
        layout.addWidget(self.sql_label)
        self.setCentralWidget(content)
        self.update_actions()

    def load_client(self, source, catalog, folder, models, references):
        self.source, self.catalog, self.models = source, catalog, models
        self.assets = ModelAssets(folder)
        self.skill_names, self.grade_items = references
        self.client_edits.clear()

    def load(self, source, catalog, folder, models, database, rows, drops, status, references):
        self.load_client(source, catalog, folder, models, references)
        self.database, self.rows, self.database_status = database, rows, status
        self.drops = drops
        self.sql_edits.clear()
        self.drop_edits.clear()
        self.refresh()

    def clear(self):
        self.close()
        self.source = self.catalog = self.database = self.assets = None
        self.models, self.rows = (), {}
        self.skill_names, self.grade_items = {}, {}
        self.drops = None
        self.database_status = ""
        self.client_edits.clear()
        self.sql_edits.clear()
        self.drop_edits.clear()
        self.refresh()

    def changed(self):
        return bool(self.client_edits or self.sql_edits or self.drop_edits)

    def can_write_sqlite(self):
        connection = self.studio.connection
        return self.database is not None and connection.path == self.database and connection.editable

    def selected(self):
        index = self.proxy.mapToSource(self.table.currentIndex())
        return self.model.rows[index.row()][0] if index.isValid() else None

    def client_row(self, mob_id):
        if self.catalog is not None and mob_id < len(self.catalog.records):
            return self.catalog.row(mob_id) | self.client_edits.get(mob_id, {})
        return None

    def refresh(self, selected=None):
        count = len(self.catalog.records) if self.catalog is not None else 0
        rows = []
        for mob_id in sorted(set(range(count)) | self.rows.keys()):
            client = self.client_row(mob_id) or {}
            server = self.rows.get(mob_id, {}) | self.sql_edits.get(mob_id, {})
            if self.show_empty.isChecked() or max(client.get("Level") or 0, server.get("Level") or 0):
                rows.append((mob_id, client, server))
        group_columns = tuple(column for name, action in self.groups.items() if action.isChecked()
                              for column in COLUMN_GROUPS[name])
        self.model.replace(tuple(rows), (*BASE_COLUMNS, *(group_columns or DEFAULT_COLUMNS)))
        for row_number, (mob_id, _, _) in enumerate(rows):
            if mob_id == selected:
                index = self.proxy.mapFromSource(self.model.index(row_number, 0))
                if index.isValid():
                    self.table.selectRow(index.row())
                break
        self.update_actions()

    def change_view(self, key, checked):
        self.settings.setValue(f"monster/{key}", checked)
        self.refresh(self.selected())

    def update_actions(self, *_):
        busy = self.studio.task is not None
        self.save_client_action.setEnabled(not busy and bool(self.client_edits))
        self.save_sql_action.setEnabled(not busy and bool(self.sql_edits or self.drop_edits) and self.can_write_sqlite())
        self.reload_action.setEnabled(not busy and self.source is not None)
        self.database_action.setEnabled(not busy and self.source is not None and self.studio.connection.connected)
        self.edit_action.setEnabled(not busy and self.selected() is not None)
        self.setWindowTitle("Monster.SData" + (" *" if self.changed() else ""))
        self.client_label.setText(f"Client · {self.source}" if self.source else "Client · No monster file open")
        state = "Connected" if self.can_write_sqlite() else "Read-only or disconnected"
        self.sql_label.setText(f"SQLite · {TABLE} · {state} · {self.database}" if self.database else
                               f"SQLite · {self.database_status or self.studio.connection.status}")

    def edit(self, *_):
        mob_id = self.selected()
        if mob_id is None:
            return
        client = self.client_row(mob_id)
        stored = self.rows.get(mob_id)
        server = (stored | self.sql_edits.get(mob_id, {})) if stored is not None else None
        drops = None if self.drops is None else self.drops.get(mob_id, {}) | {
            slot: [entry] for slot, entry in self.drop_edits.get(mob_id, {}).items()
        }
        form = MonsterDialog(self, mob_id, client, server, drops)
        if form.exec() == QDialog.DialogCode.Accepted:
            originals = (self.catalog.row(mob_id) if client is not None else None, stored)
            for batch, changes, baseline in zip((self.client_edits, self.sql_edits), form.result_edits, originals):
                if changes:
                    merged = batch.get(mob_id, {}) | changes
                    merged = {key: value for key, value in merged.items() if value != baseline[key]}
                    if merged:
                        batch[mob_id] = merged
                    else:
                        batch.pop(mob_id, None)
            if form.result_drops:
                before = self.drops.get(mob_id, {})
                merged = self.drop_edits.get(mob_id, {}) | form.result_drops
                merged = {slot: value for slot, value in merged.items() if before.get(slot, [(0, 0)]) != [value]}
                if merged:
                    self.drop_edits[mob_id] = merged
                else:
                    self.drop_edits.pop(mob_id, None)
            self.refresh(mob_id)
        form.deleteLater()

    def review(self, title, destination, action):
        box = QMessageBox(QMessageBox.Icon.Question, title, f"Save the staged edits to {destination}?", parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setInformativeText(f"{action}\n\nBackups\n{self.studio.backup_folder()}")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Save

    def save_client(self):
        if not self.save_client_action.isEnabled() or not self.review("Save Client", self.source, "Client Monster.SData"):
            return
        source, catalog, edits, selected = self.source, self.catalog, self.client_edits.copy(), self.selected()
        backups = self.studio.backup_folder()

        def write():
            return save_model_table(source, catalog, catalog.encode(edits), backups,
                                    format_name="Monster", read=MonsterCatalog.read)

        def saved(result):
            self.catalog, backup = result
            self.client_edits.clear()
            self.refresh(selected)
            self.studio.update_catalog_nodes()
            self.statusBar().showMessage(f"Client saved · Backup {backup}")

        self.studio.run_task("Saving Client Monster.SData…", write, saved)

    def save_sqlite(self):
        tables = f"{TABLE}\n{DROPS}" if self.drop_edits else TABLE
        if not self.save_sql_action.isEnabled() or not self.review("Save SQLite", self.database, tables):
            return
        if not self.can_write_sqlite():
            QMessageBox.warning(self, "Save SQLite", "The active GX database changed. Reload SQLite to continue.")
            return
        path, rows, edits, selected = self.database, self.rows, self.sql_edits.copy(), self.selected()
        drops, drop_edits = self.drops, self.drop_edits.copy()
        backups = self.studio.backup_folder()

        def saved(result):
            (self.rows, self.drops), backup = result
            self.sql_edits.clear()
            self.drop_edits.clear()
            self.refresh(selected)
            self.statusBar().showMessage(f"SQLite saved · Backup {backup}")

        def write():
            return save_mobs(path, rows, edits, backups, drop_baseline=drops, drop_edits=drop_edits)

        self.studio.run_task("Saving SQLite Mobs…", write, saved)

    def reload_client(self):
        if self.source:
            self.studio.open_monsters(self.source, reload=True)

    def reload_sqlite(self):
        connection = self.studio.connection
        if not self.database_action.isEnabled():
            return
        if (self.sql_edits or self.drop_edits) and QMessageBox.question(
            self, "Reload SQLite", "Discard staged SQLite edits?", QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Discard:
            return
        path = connection.path
        selected = self.selected()

        def loaded(data):
            self.database = path
            self.rows, self.drops = data
            self.database_status = ""
            self.sql_edits.clear()
            self.drop_edits.clear()
            self.refresh(selected)

        self.studio.run_task("Reading SQLite Mobs…", lambda: read_monster_db(path), loaded)

    def closeEvent(self, event):
        if self.isVisible():
            self.settings.setValue("monster/geometry", self.saveGeometry())
        super().closeEvent(event)

    def restore_window(self):
        geometry = self.settings.value("monster/geometry", QByteArray(), type=QByteArray)
        return not geometry.isEmpty() and self.restoreGeometry(geometry)
