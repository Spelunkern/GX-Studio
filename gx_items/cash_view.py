from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRegularExpression, QSize, Qt
from PySide6.QtGui import QActionGroup, QIcon, QPalette, QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QGridLayout
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QListView, QMainWindow, QMenu, QMessageBox
from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QStyle
from PySide6.QtWidgets import QStyledItemDelegate, QTabBar, QTableView, QVBoxLayout, QWidget

from .cash_catalog import BAGS, EMPTY_BUNDLE, SLOTS, CashCatalog, Product, validate_products
from .item_view import ItemIcons
from .pickers import IconPicker, ItemEntry, ItemPicker, is_blank_icon

DISPLAY = Qt.ItemDataRole.DisplayRole
PRODUCT_ROLE = Qt.ItemDataRole.UserRole
MODES = ("Tiles", "Details", "List", "Small Icons", "Large Icons")
MODE_KEY = "cash/viewMode"
VISIBLE_BAGS = range(1, len(BAGS))
MALL_ATLASES = {0: "icon_somo2_dds.png", 1: "icon_somo3_dds.png"}


class CashModel(QAbstractTableModel):
    HEADERS = ("Name", "Cost", "Code", "Description")

    def __init__(self, parent):
        super().__init__(parent)
        self.rows: list[tuple[int, Product]] = []
        self.icons = None

    def set_products(self, rows: dict[int, Product], bag: int, icons):
        self.beginResetModel()
        self.icons = icons
        self.rows = [(key, product) for key, product in rows.items() if product.bag == bag]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal and role == DISPLAY:
            return self.HEADERS[section]
        return None

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        product = self.rows[index.row()][1]
        column = index.column()
        if role == DISPLAY:
            return (product.name, product.cost, product.code, product.description)[column]
        if role == Qt.ItemDataRole.DecorationRole and column == 0 and self.icons:
            return QIcon(self.icons.product(product))
        if role == Qt.ItemDataRole.TextAlignmentRole and column == 1:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.ToolTipRole:
            return product.description
        if role == PRODUCT_ROLE:
            return product
        return None


class MallIcons(ItemIcons):
    def __init__(self, data: Path):
        super().__init__(data)
        self.mall = lru_cache(maxsize=512)(self.mall)

    def mall(self, icon: int) -> QPixmap:
        atlas, index = divmod(icon - 1, 1000)
        if atlas not in MALL_ATLASES or index >= 256:
            return QPixmap()
        sheet = self.sheet(MALL_ATLASES[atlas])
        if sheet.isNull():
            return sheet
        width, height = sheet.width() // 16, sheet.height() // 16
        return sheet.copy(index % 16 * width, index // 16 * height, width, height)

    def product(self, product: Product) -> QPixmap:
        if product.icon:
            return self.mall(product.icon)
        return next((self.pixmap(*divmod(item_id, 1000)) for item_id, _ in product.bundle if item_id), QPixmap())


class CashListDelegate(QStyledItemDelegate):
    def __init__(self, tiles: bool, parent):
        super().__init__(parent)
        self.tiles = tiles

    def sizeHint(self, option, index):
        return (QSize(320, max(52, option.fontMetrics.height() * 3 + 7)) if self.tiles else
                QSize(164, max(23, option.fontMetrics.height() + 6)))

    def paint(self, painter, option, index):
        icon = index.data(Qt.ItemDataRole.DecorationRole) or QIcon()
        product = index.data(PRODUCT_ROLE)
        bounds = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setClipRect(bounds)
        if selected:
            painter.fillRect(bounds, option.palette.brush(QPalette.ColorRole.Highlight))
        painter.setFont(option.font)
        painter.setPen(option.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text))
        icon_size = 32 if self.tiles else 16
        icon.paint(painter, bounds.left() + 4, bounds.top() + (bounds.height() - icon_size) // 2, icon_size, icon_size)
        lines = ((f"{product.name} - {product.cost} Pts", product.code, " ".join(product.description.split()))
                 if self.tiles else (product.name,))
        metrics = option.fontMetrics
        line_height = metrics.height()
        left = bounds.left() + icon_size + 10
        baseline = bounds.top() + (bounds.height() - line_height * len(lines)) // 2 + metrics.ascent()
        for line, label in enumerate(lines):
            text = metrics.elidedText(label, Qt.TextElideMode.ElideRight, max(0, bounds.right() - left - 4))
            painter.drawText(left, baseline + line * line_height, text)
        painter.restore()


class CashProductDialog(QDialog):
    def __init__(self, parent, product: Product, products: tuple[Product, ...], icons: MallIcons,
                 items: dict[int, ItemEntry], *, editable: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Cash Product")
        self.original, self.other_products = product, products
        self.items = items
        self.product = product
        self.icons, self.icon_id = icons, product.icon
        self.icon_button = QPushButton()
        self.icon_button.setFixedSize(38, 38)
        self.icon_button.setIconSize(QSize(32, 32))
        self.icon_button.setAccessibleName("Product icon")
        self.icon_button.clicked.connect(self.pick_icon)
        self.set_icon(product.icon)

        self.name = QLineEdit(product.name)
        self.name.setMaxLength(max(50, len(product.name)))
        self.code = QComboBox()
        self.code.setEditable(True)
        self.code.addItems(sorted(product.code for product in products))
        self.code.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.code.lineEdit().setMaxLength(max(20, len(product.code)))
        self.code.setEditText(product.code)
        self.description = QPlainTextEdit(product.description)
        self.description.setFixedHeight(75)
        self.bag = QComboBox()
        for bag in VISIBLE_BAGS:
            self.bag.addItem(BAGS[bag], bag)
        self.bag.setCurrentIndex(self.bag.findData(product.bag))
        self.cost = QLineEdit(str(product.cost))
        self.cost.setValidator(QRegularExpressionValidator(QRegularExpression("[0-9]{1,10}"), self.cost))
        self.cost.setFixedWidth(82)
        legacy_cost = QLineEdit("0")
        legacy_cost.setFixedWidth(55)
        legacy_cost.setEnabled(False)

        identifiers = QGridLayout()
        identifiers.addWidget(self.icon_button, 0, 0, 2, 1)
        identifiers.addWidget(QLabel("Product Name:"), 0, 1)
        identifiers.addWidget(self.name, 0, 2)
        identifiers.addWidget(QLabel("Product Code:"), 1, 1)
        identifiers.addWidget(self.code, 1, 2)
        identifiers.setColumnStretch(2, 1)
        category = QHBoxLayout()
        category.addWidget(QLabel("Bag:"))
        category.addWidget(self.bag, 1)
        category.addWidget(QLabel("Cost:"))
        category.addWidget(self.cost)
        category.addWidget(QLabel("Cost(2):"))
        category.addWidget(legacy_cost)
        columns = QHBoxLayout()
        columns.setSpacing(12)
        self.bundle = []
        for offset in range(0, SLOTS, 8):
            grid = QGridLayout()
            grid.setHorizontalSpacing(3)
            grid.setVerticalSpacing(3)
            grid.addWidget(QLabel("Item ID"), 0, 1)
            grid.addWidget(QLabel("Count"), 0, 2)
            for row, (item_id, count) in enumerate(product.bundle[offset:offset + 8], 1):
                slot = offset + row
                id_input = QSpinBox()
                id_input.setRange(0, max(255255, item_id))
                id_input.setValue(item_id)
                id_input.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
                id_input.setAlignment(Qt.AlignmentFlag.AlignRight)
                id_input.setAccessibleName(f"Item ID {slot}")
                id_input.setToolTip(self.item_name(item_id))
                id_input.valueChanged.connect(lambda value, field=id_input: field.setToolTip(self.item_name(value)))
                quantity = QSpinBox()
                quantity.setRange(0, 255)
                quantity.setValue(count)
                quantity.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
                quantity.setAlignment(Qt.AlignmentFlag.AlignRight)
                quantity.setAccessibleName(f"Count {slot}")
                self.bundle.append((id_input, quantity))
                grid.addWidget(QLabel(f"# {slot}"), row, 0)
                grid.addWidget(id_input, row, 1)
                grid.addWidget(quantity, row, 2)
            columns.addLayout(grid, 1)

        picker = QHBoxLayout()
        self.slot = QComboBox()
        self.slot.setAccessibleName("Item slot")
        for index in range(SLOTS):
            self.slot.addItem(f"# {index + 1}")
        self.slot.setCurrentIndex(next((index for index, (item_id, _) in enumerate(product.bundle) if not item_id), 0))
        self.slot.setEnabled(editable and bool(items))
        self.browse = QPushButton("Browse Items…")
        self.browse.setAutoDefault(False)
        self.browse.setEnabled(editable and bool(items))
        self.browse.clicked.connect(self.pick_item)
        if not items:
            self.browse.setToolTip("Open a workspace with Item.SData or connect to GX SQLite.")
        picker.addWidget(QLabel("Item slot:"))
        picker.addWidget(self.slot)
        picker.addWidget(self.browse)
        picker.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(editable)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(identifiers)
        layout.addWidget(QLabel("Product Description:"))
        layout.addWidget(self.description)
        layout.addLayout(category)
        layout.addLayout(columns)
        layout.addLayout(picker)
        layout.addWidget(buttons)
        layout.activate()
        self.setFixedSize(self.sizeHint().expandedTo(QSize(420, 0)))

    def set_icon(self, icon: int):
        self.icon_id = icon
        self.icon_button.setIcon(QIcon(self.icons.product(replace(self.original, icon=icon))))

    def pick_icon(self):
        available = [(code, pixmap) for atlas in MALL_ATLASES for code in range(atlas * 1000 + 1, atlas * 1000 + 257)
                     if not (pixmap := self.icons.mall(code)).isNull() and not is_blank_icon(pixmap)]
        if available:
            self.picker = IconPicker(self.icon_button, self.icon_id, available, 16)
            self.picker.iconSelected.connect(self.set_icon)
            self.picker.show()

    def item_name(self, item_id: int) -> str:
        entry = self.items.get(item_id)
        return entry.name if entry is not None else ""

    def pick_item(self):
        id_input, quantity = self.bundle[self.slot.currentIndex()]

        def icon_for(item_id: int) -> QPixmap:
            return self.icons.pixmap(item_id // 1000, self.items[item_id].icon)

        dialog = ItemPicker(self, list(self.items.values()), id_input.value(), icon_for)
        if dialog.exec() == QDialog.DialogCode.Accepted and (selected := dialog.selected_id) is not None:
            id_input.setValue(selected)
            if not selected or not quantity.value():
                quantity.setValue(int(selected != 0))
            id_input.setFocus()
        dialog.deleteLater()

    def accept(self):
        try:
            product = replace(self.original, bag=self.bag.currentData() or self.original.bag, icon=self.icon_id,
                              cost=int(self.cost.text()),
                              bundle=tuple((item_id.value(), count.value()) for item_id, count in self.bundle),
                              name=self.name.text(), code=self.code.currentText(), description=self.description.toPlainText())
            if not product.name.strip() or not product.description.strip():
                raise ValueError("Enter a product name and description.")
            if product.description != self.original.description and len(product.description) > 512:
                raise ValueError("Enter a description of up to 512 characters.")
            validate_products((*self.other_products, product))
        except ValueError as error:
            QMessageBox.warning(self, "Cash Product", str(error))
            return
        self.product = product
        super().accept()


class CashWindow(QMainWindow):
    def __init__(self, studio):
        super().__init__(studio, Qt.WindowType.Window)
        self.studio = studio
        self.resize(700, 480)
        self.source: Path | None = None
        self.database_path: Path | None = None
        self.catalog: CashCatalog | None = None
        self.icons: MallIcons | None = None
        self.rows: dict[int, Product] = {}

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("&Close", "Ctrl+W", self.close)
        self.save = file_menu.addAction("&Save", "Ctrl+S", studio.review_cash)
        self.install = file_menu.addAction("&Install…", studio.install_cash)
        self.reload = file_menu.addAction("&Reload…", studio.reload_cash)
        edit = self.menuBar().addMenu("&Edit")
        self.new_product = edit.addAction("New Product…", "Ctrl+N", self.add_product)
        self.edit_product = edit.addAction("Edit Product…", "Return", self.open_selected)
        self.delete_product = edit.addAction("Delete Product…", "Delete", self.remove_selected)
        views = self.menuBar().addMenu("&Views")
        self.modes = QActionGroup(self)
        self.modes.setExclusive(True)
        saved_mode = studio.settings.value(MODE_KEY, "Small Icons", type=str)
        mode = MODES.index(saved_mode) if saved_mode in MODES else MODES.index("Small Icons")
        for index, name in enumerate(MODES):
            action = views.addAction(name)
            action.setCheckable(True)
            self.modes.addAction(action)
            action.triggered.connect(lambda _, mode=index: self.set_mode(mode, remember=True))

        self.model = CashModel(self)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        for bag in VISIBLE_BAGS:
            index = self.tabs.addTab(BAGS[bag].replace("&", "&&"))
            self.tabs.setTabData(index, bag)
        self.tabs.currentChanged.connect(self.refresh)
        self.list = QListView()
        self.list.setUniformItemSizes(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setWordWrap(False)
        self.default_delegate = self.list.itemDelegate()
        self.tile_delegate = CashListDelegate(True, self.list)
        self.small_delegate = CashListDelegate(False, self.list)
        self.details = QTableView()
        self.details.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.details.setShowGrid(False)
        self.details.verticalHeader().hide()
        self.details.verticalHeader().setDefaultSectionSize(22)
        self.details.horizontalHeader().setStretchLastSection(True)

        self.views = QStackedWidget()
        for view in (self.list, self.details):
            view.setModel(self.model)
            view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda point, view=view: self.context_menu(view, point))
            view.doubleClicked.connect(lambda _: self.open_selected())
            view.selectionModel().currentChanged.connect(self.update_actions)
            self.views.addWidget(view)
        for index, width in enumerate((200, 62, 100, 250)):
            self.details.setColumnWidth(index, width)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs)
        layout.addWidget(self.views)
        self.setCentralWidget(central)
        self.statusBar()
        self.set_mode(mode)

    def load(self, source: Path, catalog: CashCatalog, data: Path, database: Path | None = None):
        self.source, self.catalog, self.database_path = source, catalog, database
        self.icons = MallIcons(data)
        self.rows = dict(enumerate(catalog.products))
        self.refresh()

    def clear(self):
        self.source = self.catalog = self.icons = self.database_path = None
        self.rows = {}
        self.refresh()
        self.hide()

    def changed(self) -> bool:
        return self.catalog is not None and tuple(self.rows.values()) != self.catalog.products

    def set_mode(self, mode: int, *, remember: bool = False):
        current = self.views.currentWidget().currentIndex()
        self.modes.actions()[mode].setChecked(True)
        if remember:
            self.studio.settings.setValue(MODE_KEY, MODES[mode])
            self.studio.settings.sync()
        self.views.setCurrentIndex(1 if mode == 1 else 0)
        if mode != 1:
            tile = mode == 0
            self.list.setViewMode(QListView.ViewMode.IconMode if mode in (0, 4) else QListView.ViewMode.ListMode)
            self.list.setFlow(QListView.Flow.LeftToRight if mode in (0, 3, 4) else QListView.Flow.TopToBottom)
            self.list.setWrapping(mode != 2)
            self.list.setMovement(QListView.Movement.Static)
            self.list.setIconSize(QSize(32, 32) if mode in (0, 4) else QSize(16, 16))
            self.list.setGridSize(QSize(320, self.fontMetrics().height() * 3 + 7) if tile else
                                  QSize(112, 60) if mode == 4 else
                                  QSize(164, max(23, self.fontMetrics().height() + 6)) if mode == 3 else QSize())
            self.list.setItemDelegate(self.tile_delegate if tile else self.small_delegate if mode == 3 else self.default_delegate)
        if current.isValid():
            self.views.currentWidget().setCurrentIndex(current.siblingAtColumn(0))
        self.update_actions()

    def refresh(self, *_):
        current = self.tabs.currentIndex()
        if current >= 0:
            self.model.set_products(self.rows, self.tabs.tabData(current), self.icons)
        name = self.source.name if self.source else "Cash.SData"
        self.setWindowTitle(f"{name}{' *' if self.changed() else ''}")
        format_note = "    Encrypted (read-only)" if self.catalog and not self.catalog.writable else ""
        self.statusBar().showMessage(f"{len(self.model.rows)} products{format_note}")
        self.update_actions()

    def update_actions(self, *_):
        enabled = self.catalog is not None and self.studio.task is None
        writable = enabled and self.catalog.writable
        selected = self.views.currentWidget().currentIndex().isValid()
        self.save.setEnabled(writable and self.changed())
        self.install.setEnabled(enabled and self.studio.install_database() is not None)
        self.reload.setEnabled(enabled)
        self.new_product.setEnabled(writable)
        self.edit_product.setEnabled(enabled and selected)
        self.delete_product.setEnabled(writable and selected)

    def context_menu(self, view, position):
        if (index := view.indexAt(position)).isValid():
            view.setCurrentIndex(index)
        menu = QMenu(view)
        menu.addActions((self.new_product, self.edit_product, self.delete_product))
        menu.exec(view.viewport().mapToGlobal(position))

    def add_product(self):
        if self.catalog is None or not self.catalog.writable:
            return
        product = Product(len(self.rows), self.tabs.tabData(self.tabs.currentIndex()), 0, 0, EMPTY_BUNDLE, "", "", "")
        self.edit(max(self.rows, default=-1) + 1, product)

    def open_selected(self):
        index = self.views.currentWidget().currentIndex()
        if index.isValid() and self.catalog is not None:
            self.edit(*self.model.rows[index.row()])

    def edit(self, key: int, product: Product):
        others = tuple(p for index, p in self.rows.items() if index != key)
        dialog = CashProductDialog(self, product, others, self.icons, self.studio.cash_item_entries(),
                                   editable=self.catalog.writable)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        updated = dialog.product
        dialog.deleteLater()
        if accepted:
            self.rows[key] = updated
            for tab in range(self.tabs.count()):
                if self.tabs.tabData(tab) == updated.bag:
                    self.tabs.setCurrentIndex(tab)
                    break
            self.refresh()
            for row, (index, _) in enumerate(self.model.rows):
                if index == key:
                    self.views.currentWidget().setCurrentIndex(self.model.index(row, 0))
                    break

    def remove_selected(self):
        index = self.views.currentWidget().currentIndex()
        if not index.isValid() or self.catalog is None or not self.catalog.writable:
            return
        key, product = self.model.rows[index.row()]
        if QMessageBox.question(self, "Delete Product", f"Delete {product.name} ({product.code})?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            del self.rows[key]
            self.refresh()
