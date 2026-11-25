"""Read-only browser for EP8 client tables."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QTableView, QVBoxLayout

from .mon_view import configure_table


class BinaryTableModel(QAbstractTableModel):
    def __init__(self, table, parent):
        super().__init__(parent)
        self.table = table

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.table.count

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.table.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.DisplayRole:
            return self.table.value(index.row(), index.column())
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.table.headers[section]
        return super().headerData(section, orientation, role)


class BinaryTableDialog(QDialog):
    def __init__(self, filename, table, parent):
        super().__init__(parent)
        self.setWindowTitle(filename)
        self.resize(810, 475)
        layout = QVBoxLayout(self)
        model = BinaryTableModel(table, self)
        proxy = QSortFilterProxyModel(self)
        proxy.setSourceModel(model)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        search = QHBoxLayout()
        layout.addLayout(search)
        column = QComboBox()
        column.addItems(table.headers)
        column.currentIndexChanged.connect(proxy.setFilterKeyColumn)
        search.addWidget(column)
        query = QLineEdit(placeholderText="Find in column", clearButtonEnabled=True)
        query.textChanged.connect(proxy.setFilterFixedString)
        search.addWidget(query, 1)
        view = QTableView()
        configure_table(view)
        view.setModel(proxy)
        view.setSortingEnabled(True)
        view.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        layout.addWidget(view, 1)
        footer = QHBoxLayout()
        footer.addWidget(QLabel(f"{table.count:,} rows · EP8 · Read-only"), 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
