"""Monster editor with independent client and SQLite saves."""

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QAbstractTableModel, QByteArray, QEvent, QModelIndex, QSize, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QTabWidget, QTableView,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .equipment_models import ModelAssets
from .install import save_model_table
from .mon_view import MonPreview, configure_table, dialog_buttons
from .monster_data import (
    CLIENT_FIELDS, CLIENT_BY_KEY, DB_FIELDS, DB_BY_KEY, DROPS, ELEMENTS, TABLE, U16, MonsterCatalog, drop_rate_max,
    SYNC_BY_SQL, SYNC_FIELDS, plan_client_import, read_mobs, read_monster_db, save_mobs,
)
from .skill_sheet import SkillSizedDialog

DISPLAY = Qt.ItemDataRole.DisplayRole
CLIENT, SQLITE = 0, 1
STATES = ("Sleep", "Stun", "Silence", "Darkness", "Pause", "Slow", "Dying", "Death", "Poison", "Illness",
          "Delusion", "Doom", "Fear", "Dull", "Bad Luck")


SQL_GREEN = "#20713d"
SQL_RED = "#a82d32"
SOURCE_LEGEND = (f'<span style="color:{SQL_GREEN}">SQLite</span> · '
                 f'<span style="color:{SQL_RED}">SQLite differs from client</span> · Monster.SData')


def comparison(client, sql_key, value):
    client_key = SYNC_BY_SQL.get(sql_key)
    if client_key in client:
        before = client[client_key]
        if value != before:
            return SQL_RED, f"Differs from Monster.SData ({before})"
        return SQL_GREEN, "Matches Monster.SData"
    return SQL_GREEN, "SQLite value"


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
        self.field_status.setTextFormat(Qt.TextFormat.PlainText)


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
        if server is not None:
            form.addWidget(self.immunities())
        elif not window.studio.legacy:
            form.addWidget(QLabel("Server combat stats, immunities and loot require a GX SQLite row for this MobID."))
        loot = self.loots()
        form.addWidget(loot)
        loot.setVisible(not window.studio.legacy)
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
        sources.addWidget(QLabel(SOURCE_LEGEND if server is not None else "Client · Monster.SData"), 1)
        values = QPushButton("More Monster Fields…")
        values.setAutoDefault(False)
        values.clicked.connect(self.open_sources)
        sources.addWidget(values)
        layout.addLayout(sources)
        layout.addWidget(self.field_status)
        dialog_buttons(self, layout)
        self.source_dialog = self.build_source_dialog()
        for rules in self.controls:
            for field, widget in rules:
                if field.key in SYNC_FIELDS or field.key in SYNC_BY_SQL:
                    signal = widget.currentIndexChanged if isinstance(widget, QComboBox) else widget.textChanged
                    signal.connect(self.update_source_indicators)
        self.update_source_indicators()
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
        if values is None and source == SQLITE:
            info += "  A GX SQLite database with a row for this MobID is required."
        elif values is not None and not enabled:
            info += f"  {text_source} is read-only."
        self.field_info[widget] = info
        widget.setToolTip(info)
        widget.setAccessibleDescription(info)
        widget.installEventFilter(self)
        return widget

    @staticmethod
    def control_value(field, widget, previous):
        try:
            if isinstance(widget, QComboBox):
                value = widget.currentData()
            elif isinstance(widget, QCheckBox):
                value = int(widget.isChecked())
            else:
                value = widget.text()
            return field.parse(value)
        except ValueError:
            return previous

    def update_source_indicators(self, *_):
        client = dict(self.sources[CLIENT] or {})
        for field, widget in self.controls[CLIENT]:
            if field.key in SYNC_FIELDS:
                client[field.key] = self.control_value(field, widget, client[field.key])
        for field, widget in self.controls[SQLITE]:
            value = self.control_value(field, widget, self.sources[SQLITE][field.key])
            color, outcome = comparison(client, field.key, value)
            widget.setStyleSheet(f"color: {color};" if value is not None else "")
            description = f"{self.field_info[widget]} · {outcome}"
            widget.setToolTip(description)
            widget.setAccessibleDescription(description)
            if widget.hasFocus():
                self.field_status.setText(description)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and watched in self.field_info:
            self.field_status.setText(watched.accessibleDescription())
            if getattr(self, "source_dialog", None):
                self.detail_status.setText(watched.accessibleDescription())
        return super().eventFilter(watched, event)

    def preferred(self, key, **options):
        sql_key = SYNC_FIELDS.get(key, key)
        server = self.sources[SQLITE] or {}
        if server.get(sql_key) is not None or self.sources[CLIENT] is None or key not in CLIENT_BY_KEY:
            return self.control(SQLITE, sql_key, **options)
        return self.control(CLIENT, key, **options)

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
        self.pair(grid, 0, 0, "Name", self.preferred("Name"), 2)
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
                if self.window.studio.legacy and key not in CLIENT_BY_KEY:
                    continue
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
        if not self.window.studio.legacy:
            self.pair(grid, 4, 0, "Vision", self.control(SQLITE, "ChaseRange"), 2)
        self.pair(grid, 5, 0, "Quest Item", self.preferred("QuestItemID"), 2)
        return widget

    def attacks(self):
        offline = self.sources[SQLITE] is None
        widget, grid = self.group("Client attack settings" if offline else "Attacks")
        if offline:
            for column, title in enumerate(("Animation", "Client attack metadata"), 1):
                grid.addWidget(QLabel(title), 1, column)
            for slot in range(1, 4):
                grid.addWidget(QLabel(str(slot)), slot + 1, 0)
                grid.addWidget(self.control(CLIENT, f"AttackAnimation{slot}"), slot + 1, 1)
                grid.addWidget(self.control(CLIENT, f"AttackType{slot}"), slot + 1, 2)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(2, 1)
            return widget
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
                if key != "Attackspecial3":
                    grid.addWidget(self.control(SQLITE, key, width=width), slot + 1, column)
        self.pair(grid, 5, 1, DB_BY_KEY["Attackspecial3"].label, self.control(SQLITE, "Attackspecial3"), 3)
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
        dialog.setWindowTitle("More Monster Fields")
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        self.detail_status = QLabel("Select a field to see its editable range.")
        self.detail_status.setWordWrap(True)
        entries = [("Client — Monster.SData", CLIENT_FIELDS)]
        if not self.window.studio.legacy:
            entries.append((f"SQLite — {TABLE}", DB_FIELDS))
        self.detail_controls = []
        for source, (title, fields) in enumerate(entries):
            scroll = QScrollArea()
            content = QWidget()
            form = QFormLayout(content)
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            tabs.addTab(scroll, title)
            if self.sources[source] is None:
                message = ("This MobID has no client record." if source == CLIENT
                           else "Connect a GX SQLite database containing this MobID to view server fields.")
                form.addRow(QLabel(message))
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
        if role not in (DISPLAY, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.ForegroundRole):
            return None
        sql_key = SYNC_FIELDS.get(key, key)
        color = None
        if key == "MobID":
            value, detail = mob_id, "MobID"
        elif key != "Day" and server.get(sql_key) is not None:
            value = server[sql_key]
            color, outcome = comparison(client, sql_key, value)
            detail = f"SQLite · {outcome}"
        else:
            value, detail = client.get(key, ""), "Monster.SData"
        if role == Qt.ItemDataRole.ForegroundRole:
            return QBrush(QColor(color)) if color else None
        if key == "Attrib" and isinstance(value, int) and 0 <= value < len(ELEMENTS):
            value = ELEMENTS[value]
        return value if role == DISPLAY else f"{value} · {detail}" if value != "" else None


class MonsterImportDialog(QDialog):
    def __init__(self, window, rows):
        super().__init__(window)
        self.setWindowTitle("Import SQLite Values")
        self.resize(650, 425)
        self.window, self.rows = window, rows
        layout = QVBoxLayout(self)
        options = QHBoxLayout()
        layout.addLayout(options)
        self.scope = QComboBox()
        self.scope.addItem("All matching MobIDs", None)
        if window.selected() in rows:
            self.scope.addItem(f"Selected MobID {window.selected()}", window.selected())
        options.addWidget(self.scope)
        self.names = QCheckBox("Include localized names")
        options.addWidget(self.names)
        options.addStretch()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.preview = QPlainTextEdit(readOnly=True)
        layout.addWidget(self.preview, 1)
        layout.addWidget(QLabel("Import stages edits to Monster.SData. Save Client writes the file with a backup."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_button = buttons.addButton("Stage Import", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.scope.currentIndexChanged.connect(self.refresh)
        self.names.toggled.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        selection = self.scope.currentData()
        self.changes, skipped = plan_client_import(
            self.window.catalog, self.window.client_edits, self.rows,
            sorted(self.rows) if selection is None else (selection,), include_names=self.names.isChecked(),
        )
        messages = [f"{len(self.changes):,} field changes across {len({row[0] for row in self.changes}):,} MobIDs."]
        if skipped:
            messages.append(f"{len(skipped):,} source values do not fit the client field and will be skipped.")
        if self.window.sql_edits:
            messages.append("Import uses saved database values. Pending SQLite edits are excluded.")
        self.summary.setText(" ".join(messages))
        lines = [f"{mob_id} · {CLIENT_BY_KEY[key].label}   {before} → {after}"
                 for mob_id, key, before, after in self.changes[:1000]]
        if len(self.changes) > 1000:
            lines.append(f"… and {len(self.changes) - 1000:,} more changes.")
        lines.extend(f"Skipped {mob_id} · {label}: {value!r} · allowed {limits}"
                     for mob_id, label, value, limits in skipped[:100])
        if len(skipped) > 100:
            lines.append(f"… and {len(skipped) - 100:,} more skipped values.")
        self.preview.setPlainText("\n".join(lines))
        self.import_button.setEnabled(bool(self.changes))


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
        menu.addAction("Select SQLite…", studio.choose_database).setVisible(not studio.legacy)
        self.save_sql_action.setVisible(not studio.legacy)
        self.database_action.setVisible(not studio.legacy)
        menu.addSeparator()
        menu.addAction("&Close", "Ctrl+W", self.close)
        edit = self.menuBar().addMenu("&Edit")
        self.edit_action = edit.addAction("Edit Monster…", "Return", self.edit)
        tools = self.menuBar().addMenu("&Tools")
        self.import_action = tools.addAction("Import SQLite Values…", self.import_database)
        tools.menuAction().setVisible(not studio.legacy)
        view = self.menuBar().addMenu("&Views")
        self.show_empty = view.addAction("Show Empty Monsters")
        self.show_empty.setCheckable(True)
        self.show_empty.setChecked(self.settings.value("monster/showEmpty", False, type=bool))
        self.show_empty.toggled.connect(lambda checked: self.change_view("showEmpty", checked))
        view.addSeparator()
        self.groups = {}
        for name, columns in COLUMN_GROUPS.items():
            if studio.legacy and not any(key in CLIENT_BY_KEY for _, key in columns):
                continue
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
        self.sql_label.setVisible(not studio.legacy)
        self.legend = QLabel(SOURCE_LEGEND)
        layout.addWidget(self.legend)
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

    def sqlite_loaded(self):
        connection = self.studio.connection
        return self.database is not None and connection.connected and connection.path == self.database

    def can_write_sqlite(self):
        return self.sqlite_loaded() and self.studio.connection.editable

    def selected(self):
        index = self.proxy.mapToSource(self.table.currentIndex())
        return self.model.rows[index.row()][0] if index.isValid() else None

    def client_row(self, mob_id):
        if self.catalog is not None and mob_id < len(self.catalog.records):
            return self.catalog.row(mob_id) | self.client_edits.get(mob_id, {})
        return None

    def refresh(self, selected=None):
        count = len(self.catalog.records) if self.catalog is not None else 0
        active_rows = self.rows if self.sqlite_loaded() else {}
        rows = []
        for mob_id in sorted(set(range(count)) | active_rows.keys()):
            client = self.client_row(mob_id) or {}
            server = active_rows[mob_id] | self.sql_edits.get(mob_id, {}) if mob_id in active_rows else {}
            if self.show_empty.isChecked() or max(client.get("Level") or 0, server.get("Level") or 0):
                rows.append((mob_id, client, server))
        group_columns = tuple(column for name, action in self.groups.items() if action.isChecked()
                              for column in COLUMN_GROUPS[name])
        columns = (*BASE_COLUMNS, *(group_columns or DEFAULT_COLUMNS))
        if self.studio.legacy:
            columns = tuple(column for column in columns if column[1] in CLIENT_BY_KEY or column[1] == "MobID")
        self.model.replace(tuple(rows), columns)
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
        self.import_action.setEnabled(not busy and self.catalog is not None and self.catalog.writable and self.sqlite_loaded())
        self.legend.setVisible(self.sqlite_loaded())
        self.setWindowTitle("Monster.SData" + (" *" if self.changed() else ""))
        self.client_label.setText(f"Client · {self.source}" if self.source else "Client · No monster file open")
        state = ("Connected" if self.can_write_sqlite() else "Read-only") if self.sqlite_loaded() else "Disconnected"
        self.sql_label.setText(f"SQLite · {TABLE} · {state} · {self.database}" if self.database else
                               f"SQLite · {self.database_status or self.studio.connection.status}")

    def edit(self, *_):
        mob_id = self.selected()
        if mob_id is None:
            return
        client = self.client_row(mob_id)
        stored = self.rows.get(mob_id) if self.sqlite_loaded() else None
        server = (stored | self.sql_edits.get(mob_id, {})) if stored is not None else None
        drops = None if stored is None or self.drops is None else self.drops.get(mob_id, {}) | {
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

    def import_database(self):
        if not self.import_action.isEnabled():
            return
        path = self.database

        def loaded(rows):
            if not self.sqlite_loaded() or self.database != path:
                return
            form = MonsterImportDialog(self, rows)
            if form.exec() != QDialog.DialogCode.Accepted or not self.sqlite_loaded() or self.database != path:
                return
            for mob_id, key, _, value in form.changes:
                changes = self.client_edits.setdefault(mob_id, {})
                if value == self.catalog.row(mob_id)[key]:
                    changes.pop(key, None)
                else:
                    changes[key] = value
                if not changes:
                    self.client_edits.pop(mob_id)
            if not self.sql_edits:
                self.rows = rows
            self.refresh(self.selected())

        self.studio.run_task("Reading saved SQLite monster values…", lambda: read_mobs(path), loaded)

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
