from collections.abc import Callable
from dataclasses import dataclass
import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, QSize, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QHeaderView
from PySide6.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QStyle, QStyledItemDelegate
from PySide6.QtWidgets import QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

DISPLAY = Qt.ItemDataRole.DisplayRole
ITEM_ID = Qt.ItemDataRole.UserRole
# Families accepted by GX World's isConsumableType.
ENCHANT_MATERIAL_FAMILIES = frozenset((25, 44, 100, 101, 102))


@dataclass(frozen=True, slots=True)
class ItemEntry:
    item_id: int
    name: str
    description: str = ""
    icon: int = 0

    @property
    def plain_description(self) -> str:
        return re.sub(r"\{/?c\d*\}", "", self.description.replace(r"\n", "\n")).strip()


class ItemTable(QAbstractTableModel):
    def __init__(self, entries: list[ItemEntry], icon_for: Callable[[int], QPixmap] | None,
                 parent: QWidget, *, materials_only: bool = False):
        super().__init__(parent)
        if materials_only:
            entries = [entry for entry in entries if entry.item_id // 1000 in ENCHANT_MATERIAL_FAMILIES
                       and entry.name and not entry.name.startswith("?")]
        self.entries = sorted(entries, key=lambda entry: entry.item_id)
        self.icon_for = icon_for

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 2

    def headerData(self, section, orientation, role=DISPLAY):
        if role == DISPLAY and orientation == Qt.Orientation.Horizontal:
            return ("Item ID", "Name")[section]
        return None

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        row = self.entries[index.row()]
        if role == DISPLAY:
            return row.item_id if index.column() == 0 else row.name
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 1 and self.icon_for:
            pixmap = self.icon_for(row.item_id)
            return QIcon(pixmap) if not pixmap.isNull() else None
        return None


class ItemPicker(QDialog):
    def __init__(
        self, parent: QWidget, entries: list[ItemEntry], current: int,
        icon_for: Callable[[int], QPixmap] | None = None, *, materials_only: bool = False,
    ):
        super().__init__(parent)
        self.item_label = "material" if materials_only else "item"
        self.setWindowTitle("Select Enchant Material" if materials_only else "Select Item")
        self.resize(555, 460)
        self.selected_id: int | None = None
        self.model = ItemTable(entries, icon_for, self, materials_only=materials_only)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search = QLineEdit()
        self.search.setPlaceholderText(f"Search {'consumables' if materials_only else 'items'} by name or ID")
        self.search.textChanged.connect(self.filter)
        self.search.returnPressed.connect(self.accept)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(20, 20))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(23)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 80)
        self.table.doubleClicked.connect(lambda _: self.accept())
        self.table.selectionModel().currentChanged.connect(self.update_detail)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.description = QPlainTextEdit()
        self.description.setReadOnly(True)
        self.description.setFixedHeight(85)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.select_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.select_button.setText("Select")
        clear = buttons.addButton(f"Clear {self.item_label.title()}", QDialogButtonBox.ButtonRole.ResetRole)
        clear.setEnabled(current != 0)
        clear.clicked.connect(self.clear_item)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.table)
        layout.addWidget(self.title)
        layout.addWidget(self.description)
        layout.addWidget(buttons)
        row = next((i for i, entry in enumerate(self.model.entries) if entry.item_id == current), None)
        if row is not None:
            self.table.selectRow(row)
            self.table.scrollTo(self.proxy.index(row, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
        self.update_detail()
        self.search.setFocus()

    def selected(self) -> ItemEntry | None:
        source = self.proxy.mapToSource(self.table.currentIndex())
        return self.model.entries[source.row()] if source.isValid() else None

    def filter(self, text: str):
        query = text.strip()
        self.proxy.setFilterFixedString(query)
        if self.proxy.rowCount():
            row = 0
            if query.isascii() and query.isdecimal():
                match = self.model.match(self.model.index(0, 0), DISPLAY, query, 1, Qt.MatchFlag.MatchFixedString)
                if match and (index := self.proxy.mapFromSource(match[0])).isValid():
                    row = index.row()
            self.table.selectRow(row)
            self.table.scrollTo(self.proxy.index(row, 0))
        self.update_detail()

    def update_detail(self, *_):
        row = self.selected()
        self.select_button.setEnabled(row is not None)
        message = f"Choose {'a material' if self.item_label == 'material' else 'an item'}" if self.proxy.rowCount() else "No matches"
        self.title.setText(f"{row.name} · Item ID {row.item_id}" if row else message)
        self.description.setPlainText((row.plain_description or "No description available.") if row else "")

    def clear_item(self):
        self.selected_id = 0
        super().accept()

    def accept(self):
        row = self.selected()
        if row is not None:
            self.selected_id = row.item_id
            super().accept()


def is_blank_icon(pixmap: QPixmap) -> bool:
    """Use shStudio's diagonal and center samples to omit empty atlas slots."""
    image = pixmap.toImage()
    if image.width() != 32 or image.height() != 32:
        image = image.scaled(32, 32)

    def rgb(x: int, y: int) -> tuple[int, int, int]:
        red, green, blue, alpha = image.pixelColor(x, y).getRgb()
        return (red, green, blue) if alpha else (0, 0, 0)

    for i in range(3, 29):
        if max(*rgb(i, i), *rgb(i, 31 - i)) > 96:
            return False
    center = [rgb(x, y) for x in range(12, 21) for y in range(12, 21)]
    return max(map(max, center)) <= 96 and all(sum(channel) // len(center) < 64 for channel in zip(*center))


class IconTileDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        number = index.data(ITEM_ID)
        if number is None:
            return
        rect = option.rect
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        painter.save()
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(painter, rect)
        elif number == 0:
            center = rect.center()
            painter.setPen(QColor("#9f988b"))
            painter.drawLine(center + QPoint(-4, -4), center + QPoint(4, 4))
            painter.drawLine(center + QPoint(-4, 4), center + QPoint(4, -4))
        state = option.state
        if state & (QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Selected):
            color = "#cb3939" if state & QStyle.StateFlag.State_MouseOver else "#3974b2"
            painter.setPen(QColor(color))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.restore()


class IconPicker(QWidget):
    iconSelected = Signal(int)
    CELL = 32

    def __init__(
        self, anchor: QWidget, current: int, icons: list[tuple[int, QPixmap]], columns: int, *,
        title: str = "Inventory icons", include_empty: bool = True,
    ):
        super().__init__(anchor, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        self.setAccessibleName(title)
        self.setObjectName("inventoryIconPopup")
        self.setStyleSheet("""
            QWidget#inventoryIconPopup { background: #a89f91; }
            QTableWidget#inventoryIconGrid { background: #f8f6f1; border: 0; }
        """)

        tiles = [*icons, (0, QPixmap())] if include_empty else icons
        space = anchor.screen().availableGeometry()
        scrollbar_width = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        columns = min(columns, max(1, (space.width() - scrollbar_width - 2) // self.CELL))
        rows = (len(tiles) + columns - 1) // columns
        self.table = QTableWidget(rows, columns)
        self.table.setObjectName("inventoryIconGrid")
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setShowGrid(False)
        self.table.setMouseTracking(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setItemDelegate(IconTileDelegate(self.table))
        for header in (self.table.horizontalHeader(), self.table.verticalHeader()):
            header.hide()
            header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            header.setMinimumSectionSize(self.CELL)
            header.setDefaultSectionSize(self.CELL)
        for cell, (number, pixmap) in enumerate(tiles):
            item = QTableWidgetItem(QIcon(pixmap), "")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(ITEM_ID, number)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"Icon {number}" if number or not include_empty else "No inventory icon")
            self.table.setItem(*divmod(cell, columns), item)
            if number == current and (number or not include_empty):
                self.table.setCurrentItem(item)
        self.table.itemClicked.connect(self.select_icon)
        self.table.itemActivated.connect(self.select_icon)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self.table)
        below = anchor.mapToGlobal(QPoint(0, anchor.height()))
        above = anchor.mapToGlobal(QPoint()).y()
        below_space = space.bottom() - below.y() + 1
        above_space = above - space.top()
        opens_below = rows * self.CELL + 2 <= below_space or below_space >= above_space
        visible_rows = min(rows, max(1, ((below_space if opens_below else above_space) - 2) // self.CELL))
        width = columns * self.CELL + 2 + (scrollbar_width if visible_rows < rows else 0)
        height = visible_rows * self.CELL + 2
        self.setFixedSize(width, height)
        x = min(max(below.x(), space.left()), space.right() - width + 1)
        y = below.y() if opens_below else above - height
        self.move(x, max(space.top(), y))
        self.setFocusProxy(self.table)

    def showEvent(self, event):
        super().showEvent(event)
        if (current := self.table.currentItem()) is not None:
            self.table.scrollToItem(current, QAbstractItemView.ScrollHint.EnsureVisible)
        self.table.setFocus()

    def select_icon(self, item: QTableWidgetItem):
        if self.isVisible():
            self.iconSelected.emit(item.data(ITEM_ID))
            self.close()
