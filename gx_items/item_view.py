from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSize, QSortFilterProxyModel, Qt
from PySide6.QtGui import QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit, QMessageBox, QPlainTextEdit
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from .catalog import Edits, Item, Scalar
from .consumable_fields import CONSUMABLES
from .equipment_fields import HIDDEN_EQUIPMENT_FIELDS, REQUIREMENTS, choices_for, profile, rows_for
from .equipment_models import ARMOR_TYPES, ModelAssets, has_equipment_durability, model_kind
from .equipment_preview import EquipmentPreview, model_value
from .fields import CLASSES, FIELDS_BY_NAME
from .image_assets import icon_atlas
from .item_controls import DebuffCombo, ForgeCostCombo, NameColorCombo, SpellGroupCombo
from .lapis_fields import LAPIS_DEBUFF_FAMILIES, LAPIS_FAMILIES, LAPISIA, STAT_ROWS, TARGET_ROWS, choices_for_lapis, cost_label
from .npc_skills import NPCSkillAssets
from .pickers import IconPicker, ItemEntry, ItemPicker, is_blank_icon
from .quest_catalog import quest_references
from .quest_view import quest_panel
from .workspace import Workspace

DISPLAY = Qt.ItemDataRole.DisplayRole
FACTIONS = ("Human", "Elf", "Light", "Deatheater", "Vail", "Dark", "All factions")
LIGHT_CLASSES = ("Fighter", "Defender", "Ranger", "Archer", "Mage", "Priest")
DARK_CLASSES = ("Warrior", "Guardian", "Assassin", "Hunter", "Pagan", "Oracle")
ELEMENTS = ("None", "Fire 1", "Water 1", "Earth 1", "Wind 1", "Fire 2", "Water 2", "Earth 2", "Wind 2")
LIGHT_GEAR = frozenset((16, 17, 18, 20, 21, 66, 67, 68, 70, 71, 72, 73, 74, 76, 77))
DARK_GEAR = frozenset((31, 32, 33, 35, 36, 81, 82, 83, 85, 86, 87, 88, 89, 91, 92))
LIGHT_WEAPONS = frozenset((1, 2, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 19, 45, 46, 49, 51,
                           53, 55, 57, 58, 60, 62, 64, 65, 69, 75))
DARK_WEAPONS = frozenset((3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 34, 47, 48, 50, 52, 54,
                          56, 57, 58, 59, 61, 63, 65, 84, 90))
ACCESSORIES = frozenset((22, 23, 37, 40, 96, 97))
BASE = ("Type", "TypeID", "Name")
GEAR = BASE + ("MinLevel", "Faction", "Slot", "Model", "Icon")
WEAPONS = BASE + ("Model", "Icon", "MinLevel", "Faction", "Slot")
LAPIS_COLUMNS = BASE + ("Icon", "LapisLevel", "Str", "Dex", "Rec", "Int", "Wis", "Luc",
                        "RecoveryHP", "RecoverySP", "RecoveryMP", "LapisCost", "Grade")
LOCKED_PAGES = frozenset(("Pets", "Wings", "Costumes"))
HEADINGS = {
    "MinLevel": "Level", "Slot": "Slots", "BuyPrice": "Buy", "SellPrice": "Sell",
    "AttackAdd": "Atk Add", "Attribute": "Attrib", "RecoveryHP": "HP", "RecoverySP": "SP",
    "RecoveryMP": "MP", "ReqVG": "ReqVg", "ReqOG": "ReqOg",
    "MountModel": "Model", "MountSeats": "Seats", "MountCooldown": "Cooldown",
    "LapisLevel": "Level", "LapisCost": "Forge cost",
    **{name: f"Class{i}" for i, (_, name) in enumerate(CLASSES, 1)},
}
SPECIAL_ICON_SHEETS = {
    **dict.fromkeys((25, 38, 41, 42, 43, 44, 94), ("icon_somo_dds.png", 16)),
    **dict.fromkeys((78, 79, 80), ("icon_somo1_dds.png", 16)),
    **dict.fromkeys((100, 101), ("icon_somo2_dds.png", 16)),
    **dict.fromkeys((102, 103), ("icon_somo3_dds.png", 16)),
    **dict.fromkeys((27, 28, 29), ("icon_quest_dds.png", 8)),
    99: ("icon_quest2_dds.png", 8),
    **dict.fromkeys(LAPIS_FAMILIES, ("icon_rapis_dds.png", 8)),
}
PAGES = (
    ("Light Gears", LIGHT_GEAR, GEAR, (0, 1, 2, 6)),
    ("Dark Gears", DARK_GEAR, GEAR, (3, 4, 5, 6)),
    ("Light Weapons", LIGHT_WEAPONS, WEAPONS, (2, 6)),
    ("Dark Weapons", DARK_WEAPONS, WEAPONS, (5, 6)),
    ("Accessories", ACCESSORIES, WEAPONS[:-1] + ("Grade", "Slot"), ()),
    ("Lapis", LAPIS_FAMILIES, LAPIS_COLUMNS, ()),
    ("Capes", frozenset((24, 39)), WEAPONS[:-1] + ("Grow",), ()),
    ("Mounts", frozenset((42,)),
     ("TypeID", "Name", "Faction", "Icon", "MountModel", "MinLevel", "MountSeats", "MountCooldown"), ()),
    ("Consumables", CONSUMABLES, BASE + ("Icon", "Slot", "Faction", "MinLevel", "Range", "Special"), ()),
    ("Pets", frozenset((120,)), WEAPONS[:-1], ()),
    ("Wings", frozenset((121,)), WEAPONS[:-1], ()),
    ("Costumes", frozenset((150,)), WEAPONS[:-1], ()),
    ("Grades", frozenset(), (), ()),
)


def find_child(directory: Path, name: str) -> Path:
    candidate = directory / name
    if candidate.exists() or not directory.is_dir():
        return candidate
    return next((child for child in directory.iterdir() if child.name.casefold() == name.casefold()), candidate)


class ItemIcons:
    def __init__(self, data: Path):
        self.data = data
        self.assets = ModelAssets(data)
        self.sheet = lru_cache(maxsize=64)(self.sheet)
        self.pixmap = lru_cache(maxsize=2048)(self.pixmap)

    def sheet(self, filename: str) -> QPixmap:
        return icon_atlas(self.assets, filename)

    def debuff_icon(self, items: Iterable[Item], value: Callable[[Item, str], Scalar], code: int) -> QPixmap:
        for item in items:
            if value(item, "Special") == code and not str(value(item, "Name")).startswith("?"):
                pixmap = self.pixmap(item.family, int(value(item, "Icon")))
                if not pixmap.isNull():
                    return pixmap
        return QPixmap()

    def pixmap(self, family: int, icon: int) -> QPixmap:
        if not 1 <= icon <= 255:
            return QPixmap()
        index = icon - 1
        if family in SPECIAL_ICON_SHEETS:
            filename, columns = SPECIAL_ICON_SHEETS[family]
            if family in (27, 28, 29, 99) and index >= 100:
                return QPixmap()
        else:
            for first, last, base, divisor, cycle in (
                (45, 48, 1, 1, 4), (49, 56, 5, 2, 8), (57, 59, 9, 1, 3),
                (60, 63, 12, 2, 4), (64, 65, 14, 1, 2),
                (66, 77, 16, 1, 6), (81, 92, 31, 1, 6),
            ):
                if first <= family <= last:
                    family = base + ((family - first) % cycle) // divisor
                    break
            if not 1 <= family <= 41:
                return QPixmap()
            columns = 4
            filename = f"{family:02d}_dds.png"
            if index >= 100:
                index -= 100
                filename = "1" + filename
        if index >= columns * 16:
            return QPixmap()
        sheet = self.sheet(filename)
        if sheet.isNull():
            return sheet
        width, height = sheet.width() // columns, sheet.height() // 16
        return sheet.copy(index % columns * width, index // columns * height, width, height)


class ItemModel(QAbstractTableModel):
    def __init__(self, workspace: Workspace, edits: Edits, icons: ItemIcons, parent: QWidget):
        super().__init__(parent)
        self.workspace = workspace
        self.edits = edits
        self.icons = icons
        self.assets = ModelAssets(icons.data)
        self.npc_skills = NPCSkillAssets(self.assets)
        self.items = list(workspace.catalog.items.values())
        self.lapis_items = [item for item in self.items if item.family in LAPIS_DEBUFF_FAMILIES]
        self.rows = {item.item_id: index for index, item in enumerate(self.items)}
        self.columns = GEAR

    def set_columns(self, columns: tuple[str, ...]):
        self.beginResetModel()
        self.columns = columns
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=DISPLAY):
        if role == DISPLAY and orientation == Qt.Orientation.Horizontal:
            column = self.columns[section]
            return HEADINGS.get(column, column)
        return None

    def value(self, item: Item, name: str) -> Scalar:
        patch = self.edits.get(item.item_id)
        if patch is not None and name in patch:
            return patch[name]
        return self.workspace.catalog.value(item, FIELDS_BY_NAME[name])

    def debuff_item_icon(self, code: int) -> QPixmap:
        return self.icons.debuff_icon(self.lapis_items, self.value, code)

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        column = self.columns[index.column()]
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            icon = int(self.value(item, "Icon"))
            pixmap = self.icons.pixmap(item.family, icon)
            return QIcon(pixmap) if not pixmap.isNull() else None
        if role == Qt.ItemDataRole.ToolTipRole and column == "Name":
            return str(self.value(item, "Description"))
        if role != DISPLAY:
            return None
        if column == "Type":
            return item.family
        if column == "TypeID":
            return item.type_id
        if column == "LapisLevel":
            return self.value(item, "ReqInt") if item.family != LAPISIA else None
        if column == "LapisCost":
            return cost_label(item.family, int(self.value(item, "ReqIg")))
        if column in HIDDEN_EQUIPMENT_FIELDS and has_equipment_durability(item.family):
            return None
        if column == "Faction":
            country = int(self.value(item, "Country"))
            return FACTIONS[country] if country < len(FACTIONS) else str(country)
        column = {"MountModel": "Range", "MountSeats": "AttackTime", "MountCooldown": "ReqIg"}.get(column, column)
        return self.value(item, column)

    def refresh(self, item: Item):
        row = self.rows[item.item_id]
        self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))


class ItemsFilter(QSortFilterProxyModel):
    def __init__(self, model: ItemModel, parent: QWidget):
        super().__init__(parent)
        self.model = model
        self.page = 0
        self.show_empty = False
        self.search = ""
        self.setSourceModel(model)

    def matches_search(self, item: Item) -> bool:
        return (
            not self.search or self.search in str(item.item_id)
            or self.search in (f"{item.family}/{item.type_id}", f"{item.family}-{item.type_id}")
            or self.search in str(self.model.value(item, "Name")).casefold()
        )

    def filterAcceptsRow(self, row, parent):
        item = self.model.items[row]
        _, types, _, factions = PAGES[self.page]
        if types and item.family not in types:
            return False
        if not self.show_empty:
            if factions and (int(self.model.value(item, "Country")) not in factions
                             or int(self.model.value(item, "MinLevel")) == 0):
                return False
            if self.page in (4, 5, 6, 8) and str(self.model.value(item, "Name")).startswith("?"):
                return False
            if self.page in (9, 10, 11) and int(self.model.value(item, "Model")) == 0:
                return False
        return self.matches_search(item)


PRIMARY_ROWS = (
    (("Level", "MinLevel"), ("Slots", "Slot"), ("Faction", "Country")),
    (("Attack", "Attack"), ("up to", "AttackAdd"), ("Elem.", "Attribute")),
    (("Range", "Range"), ("Special", "Special"), ("Speed", "AttackTime")),
    (("STR", "Str"), ("DEX", "Dex"), ("REC", "Rec")),
    (("LUC", "Luc"), ("INT", "Int"), ("WIS", "Wis")),
)
PRICE_ROWS = (
    (("Drop group", "Grade"), ("Stack", "Count")),
    (("Buy", "BuyPrice"), ("Sell", "SellPrice"), ("Restr.", "ReqOG")),
)
HP_ROW = (("HP", "RecoveryHP"), ("SP", "RecoverySP"), ("MP", "RecoveryMP"))


class ItemDialog(QDialog):
    def __init__(self, parent: QWidget, model: ItemModel, item: Item, database: Path | None = None):
        super().__init__(parent)
        self.model, self.item = model, item
        self.field_height = self.fontMetrics().height() + 4
        self.controls: dict[str, QLineEdit | QPlainTextEdit | QComboBox | QCheckBox] = {}
        self.initial: dict[str, str] = {}
        self.result_edits: dict[str, Scalar] = {}
        family = item.family
        self.is_lapis = family in LAPIS_FAMILIES
        hidden = HIDDEN_EQUIPMENT_FIELDS if has_equipment_durability(family) else frozenset()
        self.model_kind = model_kind(family)
        compact_armor = self.model_kind == "armor" and ARMOR_TYPES[family] != "helmet"
        self.model_field = "Range" if family == 42 else "Model"
        self.equipment_profile = profile(family)
        gear = family in LIGHT_GEAR | DARK_GEAR
        weapon = family in LIGHT_WEAPONS | DARK_WEAPONS
        self.attack_as_max = self.equipment_profile == "weapon"
        title = "Shield Editor" if self.equipment_profile == "shield" else "Weapon Editor" if weapon else "Gear Editor" if gear else next(
            (label for types, label in (
                (ACCESSORIES, "Accessory Editor"), (CONSUMABLES, "Goods Editor"),
                ({24, 39}, "Cape Editor"), ({30, 98}, "Lapis Editor"), ({95}, "Lapisia Editor"),
                ({42}, "Mount Editor"), ({120}, "Pet Editor"), ({121}, "Wing Editor"),
                ({150}, "Costume Editor"),
            ) if family in types), "Item Definition",
        )
        self.setWindowTitle("Accessory Editor" if self.equipment_profile == "accessory" else title)
        self.setStyleSheet("""
            QLineEdit, QPlainTextEdit { border: 1px solid #b4afa4; background: #f8f6f1; }
            QLineEdit:focus, QPlainTextEdit:focus { border-color: #638bb2; }
            QLineEdit:disabled, QPlainTextEdit:disabled { background: #eeece7; }
        """)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(7 if compact_armor else 10)
        content = QHBoxLayout()
        content.setSpacing(14)
        layout.addLayout(content)
        form = QWidget()
        form.setMinimumWidth(398 if self.is_lapis else 318 if compact_armor else 330)
        content.addWidget(form, 1, Qt.AlignmentFlag.AlignTop)
        outer = QVBoxLayout(form)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3 if compact_armor else 4)

        if title == "Item Definition":
            keys = QHBoxLayout()
            for label, value in (("Type", family), ("TypeID", item.type_id)):
                keys.addWidget(QLabel(label + ":"))
                field = QLineEdit(str(value))
                field.setReadOnly(True)
                field.setFixedWidth(45)
                field.setAlignment(Qt.AlignmentFlag.AlignRight)
                keys.addWidget(field)
                keys.addSpacing(12)
            keys.addStretch()
            outer.addLayout(keys)
            outer.addLayout(self.labeled_rows((REQUIREMENTS,), compact=True))

        first = QGridLayout()
        first.setHorizontalSpacing(6)
        first.setVerticalSpacing(2)
        first.setColumnMinimumWidth(0, 57)
        first.setColumnStretch(2, 1)
        self.icon = QPushButton()
        self.icon.setFixedSize(54, 36)
        self.icon.setIconSize(QSize(32, 32))
        self.icon.setAccessibleName("Select inventory icon")
        self.icon.setShortcut(QKeySequence(Qt.Key.Key_F4))
        self.icon.clicked.connect(self.pick_icon)
        self.control("Icon").hide()
        first.addWidget(self.icon, 0, 0, 2, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        identity = (("Name", "Name:"),)
        if self.model_kind:
            identity += ((self.model_field, "Model:"),)
        identity += (("ReqDex", "Color:"),)
        for row, (name, label) in enumerate(identity):
            first.addWidget(QLabel(label), row, 1)
            first.addWidget(self.control(name), row, 2)
        outer.addLayout(first)
        outer.addWidget(QLabel("Description:"))
        description = self.control("Description")
        lines = 2 if compact_armor else 3
        description.setFixedHeight(lines * self.fontMetrics().lineSpacing() + 8)
        outer.addWidget(description)

        if self.equipment_profile:
            outer.addSpacing(3)
            outer.addWidget(self.section_label("Can be equipped by:"))
            classes = QGridLayout()
            classes.setHorizontalSpacing(8)
            classes.setVerticalSpacing(1)
            classes.setContentsMargins(20, 0, 0, 0)
            for index, (_, name) in enumerate(CLASSES):
                control = self.control(name, checkbox=LIGHT_CLASSES[index])
                if isinstance(control, QCheckBox):
                    metrics = control.fontMetrics()
                    extra = metrics.horizontalAdvance(DARK_CLASSES[index]) - metrics.horizontalAdvance(LIGHT_CLASSES[index])
                    control.setMinimumWidth(control.sizeHint().width() + max(extra, 0))
                classes.addWidget(control, index % 2, index // 2)
                classes.setColumnMinimumWidth(index // 2, 84)
            classes.setColumnStretch(3, 1)
            outer.addLayout(classes)
            outer.addLayout(self.labeled_rows((REQUIREMENTS,), compact=True))
            self.set_class_labels(int(self.model.value(self.item, "Country")))
            self.controls["Country"].currentIndexChanged.connect(
                lambda: self.set_class_labels(int(self.controls["Country"].currentData()))
            )
            for heading, rows in rows_for(family):
                if heading == "Bonuses":
                    outer.addLayout(self.labeled_rows(rows, compact=True, stat_width=54 if compact_armor else 61))
                    continue
                if heading:
                    outer.addSpacing(2)
                    outer.addWidget(self.section_label(heading + ":"))
                if not heading or self.equipment_profile == "mount" or (compact_armor and heading == "Characteristics"):
                    for row in rows:
                        outer.addLayout(self.labeled_rows((row,), compact=compact_armor))
                else:
                    outer.addLayout(self.labeled_rows(rows, compact=compact_armor))
        elif self.is_lapis:
            self.add_lapis_sections(outer)
        elif family in CONSUMABLES:
            outer.addLayout(self.labeled_rows(((("Used by", "Country"), ("Level", "MinLevel")),), compact=True))
            outer.addWidget(self.section_label("Stats boost:"))
            outer.addLayout(self.labeled_rows((HP_ROW, *PRIMARY_ROWS[3:]), compact=True, stat_width=61))
            quest_fields = (("Quest Item", "Slot"),)
            if "Quality" not in hidden:
                quest_fields += (("Drop Rate", "Quality"),)
            for heading, fields in (
                ("Quest Info:", quest_fields),
                ("Skill Info:", (("Skill ID", "Range"), ("Level", "AttackTime"))),
                ("Special Info:", (("Effect", "Special"), ("Variant", "ReqVG"))),
            ):
                group = QHBoxLayout()
                label = self.section_label(heading)
                label.setMinimumWidth(self.fontMetrics().horizontalAdvance("Special Info:") + 10)
                group.addWidget(label)
                group.addLayout(self.labeled_rows((fields,), compact=True), 1)
                outer.addLayout(group)
            outer.addWidget(self.section_label("Other Info:"))
            outer.addLayout(self.labeled_rows((
                (("Spell group", "ReqIg"), ("Drop group", "Grade"), ("Stack", "Count")),
                PRICE_ROWS[1],
            )))
        else:
            if title != "Item Definition":
                outer.addWidget(self.section_label("Characteristics:"))
                outer.addLayout(self.labeled_rows((REQUIREMENTS,), compact=True))
            outer.addLayout(self.labeled_rows(tuple(
                tuple((label, name) for label, name in row if name not in hidden) for row in PRICE_ROWS
            )))

        references = quest_panel(quest_references(item.item_id, model.assets, model.workspace.database_path or database))
        if references is not None:
            outer.addWidget(references)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.layout().setSpacing(16)
        layout.addWidget(buttons)
        self.controls["Icon"].textChanged.connect(self.refresh_icon)
        self.icon.setEnabled(self.controls["Icon"].isEnabled())
        self.refresh_icon()
        if self.model_kind:
            self.preview = EquipmentPreview(self, model.assets, family, self.model_kind, self.controls[self.model_field])
            self.finished.connect(self.preview.stop)
            content.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
            self.refresh_body()
            for name in ("Country", *(name for _, name in CLASSES)):
                control = self.controls.get(name)
                if isinstance(control, QComboBox):
                    control.currentIndexChanged.connect(self.refresh_body)
                elif isinstance(control, QCheckBox):
                    control.toggled.connect(self.refresh_body)
                elif isinstance(control, QLineEdit):
                    control.editingFinished.connect(self.refresh_body)
        layout.activate()
        self.setFixedSize(layout.sizeHint().expandedTo(layout.minimumSize()))

    @staticmethod
    def section_label(text: str) -> QLabel:
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    def set_class_labels(self, country: int):
        labels = DARK_CLASSES if country in (3, 4, 5) else LIGHT_CLASSES
        for label, (_, name) in zip(labels, CLASSES):
            control = self.controls[name]
            if isinstance(control, QCheckBox):
                control.setText(label)

    def add_lapis_sections(self, outer: QVBoxLayout):
        lapisia = self.item.family == LAPISIA
        outer.addWidget(self.section_label("Can be used on:" if lapisia else "Can be linked to:"))
        targets = QGridLayout()
        targets.setVerticalSpacing(1)
        for row, fields in enumerate(TARGET_ROWS):
            for column, (label, name) in enumerate(fields):
                targets.addWidget(self.control(name, checkbox=label), row, column)
        outer.addLayout(targets)
        if lapisia:
            outer.addWidget(self.section_label("Characteristics:"))
            outer.addLayout(self.labeled_rows(((("Cost", "ReqIg"),),), compact=True))
            misc = PRICE_ROWS[:1]
        else:
            outer.addLayout(self.labeled_rows((
                (("Legacy level", "ReqInt"), ("Uses", "Slot"), ("Cost / Base %", "ReqIg")),
            ), compact=True))
            outer.addWidget(self.section_label("Stats:"))
            outer.addLayout(self.labeled_rows(STAT_ROWS, compact=True, stat_width=58))
            outer.addWidget(self.section_label("Attack:"))
            outer.addLayout(self.labeled_rows((
                (("Base atk +", "Attack"), ("Max atk +", "AttackAdd"), ("Range +", "Range"), ("Atk speed +", "AttackTime")),
            ), compact=True))
            outer.addWidget(self.section_label("Special:"))
            outer.addLayout(self.labeled_rows(((("Move speed +", "Speed"), ("Debuff", "Special")),), compact=True))
            misc = ((*PRICE_ROWS[0], ("Elem.", "Attribute")),)
        outer.addLayout(self.labeled_rows(misc, compact=True))
        outer.addLayout(self.labeled_rows((PRICE_ROWS[1],), compact=True))

    def control(self, name: str, checkbox: str = ""):
        field = FIELDS_BY_NAME[name]
        value = self.model.value(self.item, name)
        options = choices_for_lapis(self.item.family, name) if self.is_lapis else choices_for(self.item.family, name)
        if name in ("Country", "Attribute"):
            options = tuple(enumerate(FACTIONS if name == "Country" else ELEMENTS))
        if name == "ReqOG":
            options = ((0, "No Restriction"), (1, "Account Bound"), (2, "Character Bound"))
        if name == "AttackAdd" and self.attack_as_max:
            value = int(value) + int(self.model.value(self.item, "Attack"))
        if name == "ReqDex":
            control = NameColorCombo(int(value))
        elif name == "ReqIg" and self.is_lapis:
            control = ForgeCostCombo(self.item.family, int(value))
        elif name == "ReqIg" and self.item.family in CONSUMABLES and not self.equipment_profile:
            control = SpellGroupCombo(int(value))
        elif name == "Special" and self.is_lapis:
            control = DebuffCombo(int(value), self.model.npc_skills, self.model.debuff_item_icon)
        elif name == self.model_field and self.model_kind:
            control = QComboBox()
            control.addItem(str(value), value)
            control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            control.setMinimumContentsLength(10)
        elif checkbox and (self.is_lapis or value in (0, 1)):
            control = QCheckBox(checkbox)
            control.setChecked(bool(value))
        elif name == "Description":
            control = QPlainTextEdit(str(value))
        elif options and isinstance(value, int):
            control = QComboBox()
            for code, label in options:
                control.addItem(label, code)
            index = control.findData(value)
            if index < 0:
                control.addItem(f"{value} · Original", value)
                index = control.count() - 1
            control.setCurrentIndex(index)
        else:
            control = QLineEdit(str(value))
            if field.size:
                control.setAlignment(Qt.AlignmentFlag.AlignRight)
                control.setMaxLength(10)
        if (self.equipment_profile or self.is_lapis) and not isinstance(control, QPlainTextEdit):
            control.setMaximumHeight(self.field_height)
        can_edit = self.model.workspace.catalog.writable and (
            self.model.workspace.database_path is None or not field.column
            or self.item.item_id in self.model.workspace.sql_rows
        )
        control.setEnabled(can_edit)
        control.setObjectName(name)
        self.controls[name] = control
        self.initial[name] = self.control_text(name, control)
        return control

    def labeled_rows(self, rows, *, compact=False, stat_width=0):
        layout = QGridLayout()
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(2)
        if compact:
            layout.setColumnStretch(2 * max(map(len, rows)), 1)
        if stat_width:
            for pair in range(max(map(len, rows))):
                layout.setColumnMinimumWidth(pair * 2, self.fontMetrics().horizontalAdvance("REC:") + 6)
                layout.setColumnMinimumWidth(pair * 2 + 1, stat_width)
        for row, entries in enumerate(rows):
            for pair, (label, name) in enumerate(entries):
                layout.addWidget(QLabel(label + ":"), row, pair * 2)
                control = self.control(name)
                if isinstance(control, QLineEdit):
                    control.setFixedWidth(68 if name in ("BuyPrice", "SellPrice") else 55 if name == "ReqLuc" else 40)
                if name == "ReqLuc":
                    browse = QPushButton("Browse…")
                    browse.setMaximumHeight(self.field_height)
                    browse.setEnabled(control.isEnabled())
                    browse.clicked.connect(self.pick_material)
                    selection = QHBoxLayout()
                    selection.setSpacing(4)
                    selection.addWidget(control)
                    selection.addWidget(browse)
                    if not compact:
                        selection.addStretch()
                    layout.addLayout(selection, row, pair * 2 + 1)
                elif isinstance(control, DebuffCombo):
                    selection = QHBoxLayout()
                    selection.setSpacing(4)
                    selection.addWidget(control)
                    selection.addWidget(control.preview)
                    layout.addLayout(selection, row, pair * 2 + 1)
                else:
                    align = Qt.AlignmentFlag.AlignLeft if stat_width else Qt.AlignmentFlag(0)
                    layout.addWidget(control, row, pair * 2 + 1, align)
        return layout

    @staticmethod
    def control_text(name, control) -> str:
        if isinstance(control, QPlainTextEdit):
            return control.toPlainText()
        if isinstance(control, QCheckBox):
            return "1" if control.isChecked() else "0"
        if isinstance(control, DebuffCombo):
            return control.value()
        if isinstance(control, QComboBox):
            return model_value(control)
        return control.text()

    def refresh_body(self, *_):
        names = ("Country", *(name for _, name in CLASSES))
        defaults = [int(self.model.value(self.item, name)) for name in names]
        values = [self.control_text(name, self.controls[name]) if name in self.controls else str(default)
                  for name, default in zip(names, defaults)]
        parsed = [int(value) if value.isascii() and value.isdecimal() and len(value) <= 3 else default
                  for value, default in zip(values, defaults)]
        self.preview.set_character(parsed[0], tuple(parsed[1:]))

    def refresh_icon(self):
        try:
            number = int(self.controls["Icon"].text())
        except ValueError:
            number = 0
        pixmap = self.model.icons.pixmap(self.item.family, number)
        self.icon.setIcon(QIcon(pixmap) if not pixmap.isNull() else QIcon())
        self.icon.setText(f"{number} ▾" if pixmap.isNull() else "▾")

    def pick_icon(self):
        control = self.controls["Icon"]
        current = int(control.text())
        icons = [
            (number, pixmap) for number in range(1, 256)
            if not (pixmap := self.model.icons.pixmap(self.item.family, number)).isNull()
            and (number == current or not is_blank_icon(pixmap))
        ]
        if not icons:
            number, accepted = QInputDialog.getInt(self, "Select Inventory Icon", "Icon ID:", current, 0, 255)
            if accepted:
                control.setText(str(number))
            return
        count = len(icons)
        if self.is_lapis:
            columns = 10 if count > 56 else 8
        elif self.model_kind == "armor":
            columns = 8 if count > 27 else 6
        elif self.model_kind in ("weapon", "cape"):
            columns = 6
        else:
            columns = 16 if count > 81 else 9 if count > 36 else 6
        picker = IconPicker(self.icon, current, icons, columns)
        picker.iconSelected.connect(lambda number: control.setText(str(number)))
        picker.show()

    def pick_material(self):
        model = self.model
        catalog = model.workspace.catalog
        max_id = (1 << (8 * FIELDS_BY_NAME["ReqLuc"].size)) - 1
        entries = [
            ItemEntry(item.item_id, name, str(model.value(item, "Description")))
            for item in model.items if item.item_id <= max_id and (name := str(model.value(item, "Name")).strip())
        ]

        def icon_for(item_id: int) -> QPixmap:
            item = catalog.items.get(item_id)
            return model.icons.pixmap(item.family, int(model.value(item, "Icon"))) if item else QPixmap()

        control = self.controls["ReqLuc"]
        try:
            current = int(control.text())
        except ValueError:
            current = 0
        dialog = ItemPicker(self, entries, current, icon_for, materials_only=True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            control.setText(str(dialog.selected_id))

    def accept(self):
        result: dict[str, Scalar] = {}
        for name, control in self.controls.items():
            if not control.isEnabled():
                continue
            text = self.control_text(name, control)
            if name == "AttackAdd" and self.attack_as_max:
                continue
            if text == self.initial[name]:
                continue
            try:
                result[name] = FIELDS_BY_NAME[name].parse(text)
            except ValueError as error:
                QMessageBox.warning(self, "Invalid value", f"{name}: {error}")
                control.setFocus()
                return
        if self.attack_as_max:
            control = self.controls["AttackAdd"]
            maximum = self.control_text("AttackAdd", control)
            if control.isEnabled() and (maximum != self.initial["AttackAdd"] or "Attack" in result):
                try:
                    if not maximum.isascii() or not maximum.isdecimal():
                        raise ValueError("Enter an integer attack maximum.")
                    minimum = int(result.get("Attack", self.model.value(self.item, "Attack")))
                    variance = FIELDS_BY_NAME["AttackAdd"].parse(str(int(maximum) - minimum))
                except ValueError as error:
                    QMessageBox.warning(self, "Invalid Attack Range", str(error))
                    control.setFocus()
                    return
                result["AttackAdd"] = variance
        self.result_edits = result
        super().accept()
