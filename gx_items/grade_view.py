from collections import defaultdict

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtGui import QIcon

from .catalog import Item
from .fields import BONUS_WORDS
from .item_view import ItemModel, ItemsFilter

HEADERS = ("Item", "Idnt", "Cntr", "Lvl", "Slot", "Info")
COUNTRIES = ("Hum", "Elf", "Light", "Dth", "Vail", "Dark", "All")
BONUSES = tuple((label.title() if len(label) > 2 else label, key)
                for label, key, _ in BONUS_WORDS[3:] + BONUS_WORDS[:3])
DISPLAY_FIELDS = {0: "Name", 3: "MinLevel", 4: "Slot"}
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
ROOT, FOLDER, FIRST_ITEM = range(3)


class GradeModel(QAbstractItemModel):
    def __init__(self, icons: tuple[QIcon, ...], parent):
        super().__init__(parent)
        self.items: ItemModel | None = None
        self.icons = icons
        self.grades: list[int] = []
        self.groups: dict[int, list[Item]] = {}

    def rebuild(self, items: ItemModel, proxy: ItemsFilter):
        groups = defaultdict(list)
        for item in items.items:
            grade = int(items.value(item, "Grade"))
            if not grade or (not proxy.show_empty and (
                (item.family <= 21 and not items.value(item, "MinLevel"))
                or str(items.value(item, "Name")).startswith("?")
            )) or not proxy.matches_search(item):
                continue
            groups[grade].append(item)
        self.beginResetModel()
        self.items = items
        self.groups = dict(groups)
        self.grades = sorted(groups)
        self.endResetModel()

    def clear(self):
        self.beginResetModel()
        self.items = None
        self.groups.clear()
        self.grades.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return int(self.items is not None)
        if parent.column() != 0:
            return 0
        if parent.internalId() == ROOT:
            return len(self.grades)
        if parent.internalId() == FOLDER:
            return len(self.groups[self.grades[parent.row()]])
        return 0

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            kind = ROOT
        elif parent.internalId() == ROOT:
            kind = FOLDER
        else:
            kind = FIRST_ITEM + parent.row()
        return self.createIndex(row, column, kind)

    def parent(self, index):
        if not index.isValid() or index.internalId() == ROOT:
            return QModelIndex()
        if index.internalId() == FOLDER:
            return self.createIndex(0, 0, ROOT)
        return self.createIndex(index.internalId() - FIRST_ITEM, 0, FOLDER)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return HEADERS[section]
            if role == Qt.ItemDataRole.TextAlignmentRole and 1 <= section <= 4:
                return RIGHT
        return None

    def item(self, index: QModelIndex) -> Item | None:
        if index.isValid() and index.internalId() >= FIRST_ITEM:
            grade = self.grades[index.internalId() - FIRST_ITEM]
            return self.groups[grade][index.row()]
        return None

    def index_for_item(self, item: Item) -> QModelIndex:
        if self.items is not None:
            grade = int(self.items.value(item, "Grade"))
            if grade in self.groups and item in self.groups[grade]:
                folder = self.index(self.grades.index(grade), 0, self.index(0, 0))
                return self.index(self.groups[grade].index(item), 0, folder)
        return QModelIndex()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.items is None:
            return None
        column = index.column()
        kind = index.internalId()
        if kind < FIRST_ITEM:
            if column != 0:
                return None
            if role == Qt.ItemDataRole.DisplayRole:
                return "Grades" if kind == ROOT else f"- {self.grades[index.row()]} -"
            if role == Qt.ItemDataRole.DecorationRole:
                return self.icons[kind]
            return None

        if role == Qt.ItemDataRole.TextAlignmentRole and 1 <= column <= 4:
            return RIGHT
        if role == Qt.ItemDataRole.DecorationRole and column == 0:
            item = self.item(index)
            pixmap = self.items.icons.pixmap(item.family, int(self.items.value(item, "Icon")))
            return self.icons[2] if pixmap.isNull() else QIcon(pixmap)
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        item = self.item(index)
        if column == 1:
            return f"{item.family}-{item.type_id}"
        if column == 2:
            country = int(self.items.value(item, "Country"))
            return "All" if item.family == 30 else COUNTRIES[country] if country < len(COUNTRIES) else "--"
        if column == 5:
            return ", ".join(f"{label}:{value}" for label, key in BONUSES if (value := self.items.value(item, key)))
        return str(self.items.value(item, DISPLAY_FIELDS[column]))
