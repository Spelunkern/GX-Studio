from pathlib import Path

from PySide6.QtCore import QByteArray, QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPushButton, QTableView, QVBoxLayout, QWidget,
)

from .equipment_models import ModelAssets
from .install import replace_file, save_model_table
from .item_view import find_child
from .itm_catalog import name_at
from .mon_catalog import text_bytes
from .mon_view import configure_table

DISPLAY = Qt.ItemDataRole.DisplayRole


def reference(names, before, text):
    if text == name_at(names, before):
        return names, before
    if not text:
        return names, -1
    for index in range(len(names)):
        if text == name_at(names, index):
            return names, index
    return (*names, text_bytes(text, b"")), len(names)


def file_reference_field(form, parent, title, directory, filters, name, changed):
    field = QLineEdit(name)
    field.setMinimumWidth(166)
    field.editingFinished.connect(changed)
    browse = QPushButton("…")
    browse.setAccessibleName(f"Browse {directory.name} files")
    browse.setFixedWidth(28)

    def pick():
        filename, _ = QFileDialog.getOpenFileName(parent, title, str(directory), filters)
        if filename:
            path = Path(filename)
            if path.parent.resolve() != directory.resolve():
                QMessageBox.warning(parent, title, f"Select a file from\n{directory}")
                return
            field.setText(path.name)
            changed()

    browse.clicked.connect(pick)
    row = QHBoxLayout()
    row.addWidget(field)
    row.addWidget(browse)
    form.addRow(title + ":", row)
    return field


class ModelTableWindow(QMainWindow):
    export_extension = None
    def __init__(self, studio):
        super().__init__(studio, Qt.WindowType.Window)
        self.studio, self.settings = studio, studio.settings
        self.source = self.catalog = None
        self.meshes = self.textures = ()
        self.records = []
        self.resize(*self.initial_size)
        file = self.menuBar().addMenu("&File")
        self.save_action = file.addAction("&Save", "Ctrl+S", self.save)
        self.export_action = file.addAction("Export Copy…", self.export)
        self.reload_action = file.addAction("&Reload…", self.reload)
        file.addSeparator()
        file.addAction("&Close", "Ctrl+W", self.close)
        edit = self.menuBar().addMenu("&Edit")
        self.edit_action = edit.addAction("Edit Model…", "Return", self.edit_selected)
        self.add_action = edit.addAction("New Model…", "Ctrl+N", self.add_model)
        self.duplicate_action = edit.addAction("Duplicate Model", self.duplicate)
        self.clear_action = edit.addAction("Clear Model…", self.clear_selected)
        self.remove_action = edit.addAction("Delete Last Model…", "Delete", self.delete_last)
        self.up_action = edit.addAction("Move Up…", lambda: self.move_record(-1))
        self.down_action = edit.addAction("Move Down…", lambda: self.move_record(1))
        view = self.menuBar().addMenu("&Views")
        self.preview_action = view.addAction("3D Preview")
        self.preview_action.setCheckable(True)
        self.preview_action.setChecked(self.settings.value(f"{self.format_name.lower()}/preview", True, type=bool))
        self.preview_action.toggled.connect(self.set_preview_visible)

        content = QWidget()
        layout = QHBoxLayout(content)
        layout.setContentsMargins(5, 4, 5, 5)
        listing = QVBoxLayout()
        layout.addLayout(listing, 1)
        self.table = QTableView()
        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels(self.HEADERS)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        configure_table(self.table)
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        for column, width in enumerate(self.column_widths):
            self.table.setColumnWidth(column, width)
        self.table.selectionModel().currentChanged.connect(self.selection_changed)
        self.table.doubleClicked.connect(lambda _: self.edit_selected())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        listing.addWidget(self.table, 1)
        self.search = QLineEdit(placeholderText="Find a model, mesh or texture", clearButtonEnabled=True)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        listing.addWidget(self.search)
        self.preview_container = QWidget()
        preview_column = QVBoxLayout(self.preview_container)
        preview_column.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.preview_container)
        self.preview = self.make_preview(preview_column)
        preview_column.addWidget(self.preview, 1)
        self.setCentralWidget(content)
        self.set_preview_visible(self.preview_action.isChecked())
        self.update_actions()

    def load(self, source, catalog):
        same = source == self.source
        selected = self.selected() if same else None
        if not same:
            self.search.clear()
        self.source, self.catalog = source, catalog
        self.meshes, self.textures, self.records = catalog.meshes, catalog.textures, list(catalog.records)
        self.preview.stop()
        self.load_preview(source, same)
        if selected is None and self.records:
            selected = next((index for index, record in enumerate(self.records) if record.mesh >= 0), 0)
        self.refresh(selected)

    def clear(self):
        self.close()
        self.source = self.catalog = None
        self.meshes = self.textures = ()
        self.records.clear()
        self.preview.stop()
        self.preview.renderer = None
        self.refresh()

    def changed(self):
        return self.catalog is not None and (self.meshes, self.textures, tuple(self.records)) != (
            self.catalog.meshes, self.catalog.textures, self.catalog.records,
        )

    def selected(self):
        index = self.proxy.mapToSource(self.table.currentIndex())
        return index.row() if index.isValid() else None

    def refresh(self, selected=None):
        self.model.removeRows(0, self.model.rowCount())
        for index, record in enumerate(self.records):
            values = self.row_values(index, record)
            items = []
            for column, value in enumerate(values):
                item = QStandardItem()
                item.setData(value, DISPLAY)
                if column not in self.name_columns:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                items.append(item)
            self.model.appendRow(items)
        if selected is not None and 0 <= selected < len(self.records):
            row = self.proxy.mapFromSource(self.model.index(selected, 0)).row()
            if row >= 0:
                self.table.selectRow(row)
        self.selection_changed()

    def selection_changed(self, *_):
        index = self.selected()
        record = self.records[index] if index is not None and index < len(self.records) else None
        names = (name_at(self.meshes, record.mesh), name_at(self.textures, record.texture)) if record else ("", "")
        self.preview.set_model(names, record, reset_angle=True)
        self.update_actions()

    def update_actions(self):
        loaded = self.catalog is not None and self.studio.task is None
        index = self.selected()
        active = loaded and index is not None
        self.save_action.setEnabled(loaded and self.changed())
        self.export_action.setEnabled(loaded)
        self.reload_action.setEnabled(loaded)
        self.edit_action.setEnabled(active)
        self.add_action.setEnabled(loaded and len(self.records) < 256)
        self.duplicate_action.setEnabled(active and len(self.records) < 256)
        self.clear_action.setEnabled(active)
        self.remove_action.setEnabled(active and index == len(self.records) - 1)
        self.up_action.setEnabled(active and index > 0)
        self.down_action.setEnabled(active and index < len(self.records) - 1)
        self.setWindowTitle(self.source.name + (" *" if self.changed() else "") if self.source else f"{self.format_name} Models")
        if self.source:
            self.statusBar().showMessage(f"{self.catalog.signature.decode()}   ·   {len(self.records):,} models   ·   {self.source}")
        else:
            self.statusBar().clearMessage()

    def context_menu(self, point):
        index = self.table.indexAt(point)
        if index.isValid():
            self.table.selectRow(index.row())
        menu = QMenu(self)
        menu.addActions((self.edit_action, self.add_action, self.duplicate_action, self.clear_action, self.remove_action))
        menu.addSeparator()
        menu.addActions((self.up_action, self.down_action, self.preview_action))
        menu.exec(self.table.viewport().mapToGlobal(point))

    def set_preview_visible(self, visible):
        self.preview_container.setVisible(visible)
        self.settings.setValue(f"{self.format_name.lower()}/preview", visible)

    def open_editor(self, index, record):
        form = self.editor_type(self, index, record)
        self.preview.set_paused(True)
        try:
            if form.exec() == QDialog.DialogCode.Accepted:
                self.meshes, self.textures = form.meshes, form.textures
                self.records[index:index + 1] = [form.result_record]
                self.refresh(index)
        finally:
            form.deleteLater()
            self.preview.set_paused(False)

    def edit_selected(self):
        if (index := self.selected()) is not None:
            self.open_editor(index, self.records[index])

    def new_record(self):
        return self.empty_record()

    def add_model(self):
        if self.add_action.isEnabled():
            self.open_editor(len(self.records), self.new_record())

    def duplicate(self):
        if self.duplicate_action.isEnabled():
            self.open_editor(len(self.records), self.records[self.selected()])

    def confirm(self, title, message):
        return QMessageBox.question(self, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes

    def clear_selected(self):
        if self.clear_action.isEnabled():
            index = self.selected()
            if self.confirm("Clear Model", f"Clear model {index} and keep its index?"):
                self.records[index] = self.empty_record()
                self.refresh(index)

    def delete_last(self):
        if self.remove_action.isEnabled() and self.confirm("Delete Model", f"Delete the last model, index {self.selected()}?"):
            self.records.pop()
            self.refresh(len(self.records) - 1)

    def move_record(self, delta):
        index = self.selected()
        if index is None:
            return
        other = index + delta
        if 0 <= other < len(self.records) and self.confirm(
            "Move Model", f"Swap indices {index} and {other}? Other GX files may refer to these indices.",
        ):
            self.records[index], self.records[other] = self.records[other], self.records[index]
            self.refresh(other)

    def reload(self):
        if self.source:
            self.studio.open_model_table(self.source, reload=True)

    def save(self):
        if not self.save_action.isEnabled():
            return
        backups = self.studio.backup_folder()
        box = QMessageBox(QMessageBox.Icon.Question, f"Save {self.format_name}", "Save the staged model edits?", parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setInformativeText(f"{self.source}\n\nDated backups\n{backups}")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Save:
            return
        source, catalog, meshes, textures, records = self.source, self.catalog, self.meshes, self.textures, tuple(self.records)

        def operation():
            return save_model_table(source, catalog, catalog.encode(meshes, textures, records), backups,
                                    format_name=self.format_name, read=type(catalog).read)

        def saved(result):
            catalog, backup = result
            self.load(source, catalog)
            ModelAssets.choices.cache_clear()
            self.studio.update_catalog_nodes()
            QMessageBox.information(self, f"Save {self.format_name}", f"Saved {source.name}.\n\nBackup\n{backup}")

        self.studio.run_task(f"Saving {self.format_name}…", operation, saved)

    def export(self):
        if not self.export_action.isEnabled():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {self.format_name} Copy", str(self.source.with_stem(self.source.stem + "-copy")),
            self.file_filter,
        )
        if not path:
            return
        destination = Path(path)
        if self.export_extension and not destination.suffix:
            destination = destination.with_suffix(self.source.suffix)
        destination = destination.resolve()
        if self.export_extension and destination.suffix.casefold() != self.export_extension:
            QMessageBox.warning(self, f"Export {self.format_name} Copy",
                                f"Use the {self.export_extension.upper()} file extension.")
            return
        if destination == self.source or (destination.exists() and self.source.exists() and self.source.samefile(destination)):
            QMessageBox.warning(self, f"Export {self.format_name} Copy", "Select a separate file for the copy.")
            return
        catalog, meshes, textures, records = self.catalog, self.meshes, self.textures, tuple(self.records)
        self.studio.run_task(f"Exporting {self.format_name}…",
                             lambda: replace_file(destination, catalog.encode(meshes, textures, records), allow_create=True),
                             lambda _: QMessageBox.information(self, f"Export {self.format_name} Copy", f"Exported to\n{destination}"))

    def closeEvent(self, event):
        if self.isVisible():
            self.settings.setValue(f"{self.format_name.lower()}/geometry", self.saveGeometry())
            self.settings.sync()
        super().closeEvent(event)

    def restore_window(self):
        geometry = self.settings.value(f"{self.format_name.lower()}/geometry", QByteArray(), type=QByteArray)
        return not geometry.isEmpty() and self.restoreGeometry(geometry)
