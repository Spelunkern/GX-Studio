from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit
from PySide6.QtWidgets import QMainWindow, QMessageBox, QPushButton, QTableView, QTableWidget, QTableWidgetItem
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .catalog import Scalar
from .consumable_fields import CONSUMABLES
from .equipment_fields import HIDDEN_EQUIPMENT_FIELDS, choices_for, editable_fields
from .equipment_models import ModelAssets, has_equipment_durability
from .gx_connection import DatabaseStatus, SQL_FIELDS, parse_sql_value
from .item_controls import DebuffCombo, ForgeCostCombo, NameColorCombo, SpellGroupCombo
from .lapis_fields import LAPIS_DEBUFF_FAMILIES, LAPIS_FAMILIES
from .npc_skills import NPCSkillAssets
from .pickers import ItemEntry, ItemPicker
from .quest_catalog import QuestReferences, quest_references
from .quest_view import quest_panel
from .workspace import TABLE

Row = dict[str, Scalar | None]
COLUMNS = ("ItemID", "Type", "TypeID", "ItemName", "Reqlevel", "Grade", "Buy", "Sell")
DISPLAY = Qt.ItemDataRole.DisplayRole
DebuffAssets = tuple[NPCSkillAssets, Callable[[int], QPixmap] | None]


class SQLiteItems(QAbstractTableModel):
    def __init__(self, rows: dict[int, Row], parent: QWidget):
        super().__init__(parent)
        self.rows = rows
        self.ids = sorted(rows)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.ids)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=DISPLAY):
        if role == DISPLAY and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index, role=DISPLAY):
        if role != DISPLAY or not index.isValid():
            return None
        item_id = self.ids[index.row()]
        column = COLUMNS[index.column()]
        if column == "ItemID":
            return item_id
        if column == "Type":
            return item_id // 1000
        if column == "TypeID":
            return item_id % 1000
        return self.rows[item_id][column]


class SQLiteItemDialog(QDialog):
    def __init__(
        self, parent: QWidget, item_id: int, record: Row, editable: bool,
        pick_material: Callable[[QWidget, int], int | None] | None = None,
        debuff_assets: DebuffAssets | None = None,
        references: QuestReferences | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{TABLE} · {item_id}")
        self.resize(510, 545)
        self.family = item_id // 1000
        self.record = record
        self.changes: dict[str, Scalar] = {}
        self.enchant_id: QLineEdit | None = None
        self.spell_group: SpellGroupCombo | None = None
        self.cost: QComboBox | None = None
        self.name_color: NameColorCombo | None = None
        self.debuff: DebuffCombo | None = None
        allowed = editable_fields(self.family)
        self.fields = tuple(
            (column, field) for column, field in SQL_FIELDS.items()
            if (allowed is None or field.name in allowed)
            and (field.name not in HIDDEN_EQUIPMENT_FIELDS or not has_equipment_durability(self.family))
        )
        self.table = QTableWidget(len(self.fields), 2)
        self.table.setHorizontalHeaderLabels(("Column", "SQLite value"))
        self.table.setColumnWidth(0, 175)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        for row, (column, _) in enumerate(self.fields):
            enchant = allowed is not None and column == "Reqluc"
            lapis_cost = column == "ReqIg" and self.family in LAPIS_FAMILIES
            consumable_group = column == "ReqIg" and self.family in CONSUMABLES and allowed is None
            lapis_debuff = column == "Special" and self.family in LAPIS_DEBUFF_FAMILIES
            label = {"ReqDex": "Name color (ReqDex)"}.get(column, column)
            if enchant:
                label = "Enchant ID"
            elif lapis_cost:
                label = "Forge cost (ReqIg)"
            elif consumable_group:
                label = "Spell group (ReqIg)"
            elif lapis_debuff:
                label = "Debuff (Special)"
            name = QTableWidgetItem(label)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, name)
            original = record[column]
            value = QTableWidgetItem("" if original is None else str(original))
            if not editable:
                value.setFlags(value.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, value)
            if column == "ReqDex" and (original is None or isinstance(original, int)):
                self.name_color = NameColorCombo(original)
                self.name_color.setEnabled(editable)
                self.table.setRowHeight(row, self.name_color.sizeHint().height() + 2)
                self.table.setCellWidget(row, 1, self.name_color)
            elif lapis_debuff and (original is None or isinstance(original, int)):
                assets, icon = debuff_assets or (None, None)
                self.debuff = DebuffCombo(original, assets, icon)
                self.debuff.setEnabled(editable)
                cell = QWidget()
                selection = QHBoxLayout(cell)
                selection.setContentsMargins(0, 0, 0, 0)
                selection.addWidget(self.debuff, 1)
                selection.addWidget(self.debuff.preview)
                self.table.setRowHeight(row, cell.sizeHint().height() + 2)
                self.table.setCellWidget(row, 1, cell)
            elif (lapis_cost or consumable_group) and (original is None or isinstance(original, int)):
                if lapis_cost:
                    combo = self.cost = ForgeCostCombo(self.family, original)
                else:
                    combo = self.spell_group = SpellGroupCombo(original)
                combo.setEnabled(editable)
                self.table.setRowHeight(row, combo.sizeHint().height() + 2)
                self.table.setCellWidget(row, 1, combo)
            elif enchant and editable and pick_material:
                self.enchant_id = QLineEdit(value.text())
                browse = QPushButton("Browse…")
                browse.clicked.connect(lambda: self.browse_enchant(pick_material))
                cell = QWidget()
                selection = QHBoxLayout(cell)
                selection.setContentsMargins(0, 0, 0, 0)
                selection.addWidget(self.enchant_id, 1)
                selection.addWidget(browse)
                self.table.setRowHeight(row, cell.sizeHint().height() + 2)
                self.table.setCellWidget(row, 1, cell)
        if editable:
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(self.accept)
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        quest_view = quest_panel(references)
        if quest_view is not None:
            layout.addWidget(quest_view)
        self.setFixedSize(self.size())
        layout.addWidget(buttons)

    def browse_enchant(self, pick_material: Callable[[QWidget, int], int | None]):
        if self.enchant_id is None:
            return
        try:
            current = int(self.enchant_id.text())
        except ValueError:
            current = 0
        selected = pick_material(self, current)
        if selected is not None:
            self.enchant_id.setText(str(selected))

    def accept(self):
        changes: dict[str, Scalar] = {}
        for row, (column, field) in enumerate(self.fields):
            value = self.enchant_id.text() if column == "Reqluc" and self.enchant_id else self.table.item(row, 1).text()
            combo = {"ReqIg": self.cost or self.spell_group, "ReqDex": self.name_color}.get(column)
            if combo is not None:
                data = combo.currentData()
                value = "" if data is None else str(data)
            elif column == "Special" and self.debuff is not None:
                value = self.debuff.value()
            original = self.record[column]
            if value == ("" if original is None else str(original)):
                continue
            try:
                parsed = parse_sql_value(field, value)
                options = choices_for(self.family, field.name)
                if options and parsed not in dict(options):
                    raise ValueError("Valid values: " + ", ".join(str(code) for code, _ in options))
                if self.family == 42 and column == "Range" and not 0 <= parsed <= 255:
                    raise ValueError("A mount model index must be between 0 and 255.")
            except ValueError as error:
                QMessageBox.warning(self, "Invalid SQLite Value", f"{column}: {error}")
                self.table.setCurrentCell(row, 1)
                return
            if parsed != original:
                changes[column] = parsed
        self.changes = changes
        super().accept()


class SQLiteWindow(QMainWindow):
    def __init__(
        self, parent: QWidget, refresh: Callable[[], None],
        save: Callable[[Path, int, Row, dict[str, Scalar]], None],
        client_descriptions: Callable[[], dict[int, str]],
        client_debuff_assets: Callable[[], DebuffAssets | None] | None = None,
        client_assets: Callable[[], ModelAssets | None] | None = None,
        server_quest: Callable[[], Path | None] | None = None,
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(f"{TABLE} · SQLite")
        self.resize(810, 510)
        self.save = save
        self.client_descriptions = client_descriptions
        self.client_debuff_assets = client_debuff_assets
        self.client_assets = client_assets
        self.server_quest = server_quest
        self.connection = DatabaseStatus(None, "Disconnected", "")
        self.model: SQLiteItems | None = None
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.path = QLabel()
        self.path.setTextFormat(Qt.TextFormat.PlainText)
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path.setWordWrap(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find item by ID or name")
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(20)
        self.view.setShowGrid(False)
        self.view.setSortingEnabled(True)
        self.view.doubleClicked.connect(lambda _: self.edit_selected())
        self.menuBar().addMenu("&File").addAction("&Refresh", "F5", refresh)
        self.menuBar().addMenu("&Edit").addAction("Open item…", "Return", self.edit_selected)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 0)
        layout.addWidget(self.path)
        layout.addWidget(self.search)
        layout.addWidget(self.view)
        self.setCentralWidget(widget)

    def load(self, connection: DatabaseStatus, rows: dict[int, Row], *, activate: bool = True):
        previous = self.model
        self.connection = connection
        self.path.setText(f"{connection.status} · {connection.path}")
        self.model = SQLiteItems(rows, self)
        self.proxy.setSourceModel(self.model)
        for column, width in enumerate((75, 68, 76, 220, 75, 60, 95, 95)):
            if COLUMNS[column] in ("Type", "TypeID"):
                width = max(width, self.view.horizontalHeader().sectionSizeHint(column) + 8)
            self.view.setColumnWidth(column, width)
        self.view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        if previous is not None:
            previous.deleteLater()
        self.statusBar().showMessage(f"{len(rows):,} items")
        if activate:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def edit_selected(self):
        source = self.proxy.mapToSource(self.view.currentIndex())
        path = self.connection.path
        if self.model is None or path is None or not source.isValid():
            return
        item_id = self.model.ids[source.row()]
        row = self.model.rows[item_id]
        assets = self.client_debuff_assets() if item_id // 1000 in LAPIS_DEBUFF_FAMILIES and self.client_debuff_assets else None
        client = self.client_assets() if self.client_assets else None
        server_quest = self.server_quest() if self.server_quest else None
        references = quest_references(item_id, client, path, server=True, server_quest=server_quest)
        dialog = SQLiteItemDialog(self, item_id, row, self.connection.editable, self.pick_material, assets, references)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.changes:
            return
        columns = ", ".join(dialog.changes)
        answer = QMessageBox.question(
            self, "Save directly to SQLite",
            f"Update item {item_id} in:\n{path}\n\n"
            f"Columns: {columns}\n\nGX World loads item definitions when it starts.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            self.save(path, item_id, row.copy(), dialog.changes)

    def pick_material(self, parent: QWidget, current: int) -> int | None:
        if self.model is None:
            return None
        descriptions = self.client_descriptions()
        entries = [
            ItemEntry(item_id, name, descriptions.get(item_id, ""))
            for item_id, row in self.model.rows.items() if (name := str(row["ItemName"] or "").strip())
        ]
        dialog = ItemPicker(parent, entries, current, materials_only=True)
        dialog.exec()
        return dialog.selected_id

    def apply_changes(self, item_id: int, changes: dict[str, Scalar]):
        if self.model is None or item_id not in self.model.rows:
            return
        self.model.rows[item_id].update(changes)
        row = self.model.ids.index(item_id)
        self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, len(COLUMNS) - 1))
        self.statusBar().showMessage(f"Item {item_id} saved to SQLite", 5000)
