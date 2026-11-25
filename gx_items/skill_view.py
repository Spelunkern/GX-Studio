from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QByteArray, QModelIndex, QSize, QSortFilterProxyModel, Qt
from PySide6.QtGui import QActionGroup, QIcon
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit
from PySide6.QtWidgets import QMainWindow, QMenu, QTabBar, QTableView, QVBoxLayout, QWidget

from .catalog import Scalar
from .equipment_models import ModelAssets
from .item_view import DARK_CLASSES, LIGHT_CLASSES
from .npc_skill_sheet import MobSkillDialog
from .npc_skills import NPCSkillAssets, skill_icon_location
from .pickers import is_blank_icon
from .skill_catalog import NPCSkillCatalog, SKILL_FIELDS_BY_NAME, SkillCatalog, SkillEdits, SkillKey
from .skill_layout import CLASS_FIELDS, FAMILIES, LIST_FIELDS, NOSS, TOON, rank_title
from .skill_sheet import SkillDialog

DISPLAY = Qt.ItemDataRole.DisplayRole
SORT_VALUE = Qt.ItemDataRole.UserRole


class SkillModel(QAbstractTableModel):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.keys: list[SkillKey] = []
        self.columns = LIST_FIELDS

    def rebuild(self, page: int, class_field: str | None):
        self.beginResetModel()
        self.keys = []
        for ranks in self.editor.groups.values():
            first = ranks[0]
            if self.editor.npc or (bool(self.editor.value(first, "TypeShow")) == (page == TOON) and
                                   (page != TOON or class_field is None or self.editor.value(first, class_field))):
                self.keys.append(first)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.keys)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal and role == DISPLAY:
            return self.columns[section][1]
        return None

    def data(self, index, role=DISPLAY):
        if not index.isValid():
            return None
        key = self.keys[index.row()]
        field = self.columns[index.column()][0]
        if role == Qt.ItemDataRole.DecorationRole and field == "ID":
            return QIcon(self.editor.icons.pixmap(int(self.editor.value(key, "Icon"))))
        if role == Qt.ItemDataRole.ToolTipRole and field in ("Name", "Description"):
            return self.editor.value(key, "Description")
        if role == Qt.ItemDataRole.TextAlignmentRole and field not in ("ID", "Name", "Description"):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role not in (DISPLAY, SORT_VALUE):
            return None
        if field == "ID":
            return key[0]
        value = self.editor.value(key, field)
        if field == "Name":
            return value if self.editor.npc else rank_title(value)
        if field == "TypeDetail" and role == DISPLAY:
            return f"{value} - {FAMILIES.get(value, 'Unknown')}"
        return value


class SkillFilter(QSortFilterProxyModel):
    query = ""

    def set_query(self, text: str):
        self.query = text.casefold().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if not self.query:
            return True
        model = self.sourceModel()
        return self.query in str(model.index(row, 0, parent).data()) or self.query in model.index(row, 1, parent).data().casefold()


class SkillWindow(QMainWindow):
    def __init__(self, studio, *, npc: bool = False):
        super().__init__(studio, Qt.WindowType.Window)
        self.studio = studio
        self.settings = studio.settings
        self.npc = npc
        self.catalog_type = NPCSkillCatalog if npc else SkillCatalog
        self.folder = "Monster" if npc else "Character"
        self.settings_group = "npcSkills" if npc else "skills"
        self.resize(760, 480)
        self.source: Path | None = None
        self.database_path: Path | None = None
        self.catalog: SkillCatalog | None = None
        self.icons = NPCSkillAssets()
        self.icon_choices = None
        self.groups: dict[int, list[SkillKey]] = {}
        self.edits: SkillEdits = {}
        stored_class = self.settings.value(self.setting("class"), "", type=str)
        self.class_field = stored_class if stored_class in CLASS_FIELDS else None

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("&Close", "Ctrl+W", self.close)
        self.save = file_menu.addAction("&Save", "Ctrl+S", lambda: studio.review_skills(self))
        self.install = file_menu.addAction("&Install…", lambda: studio.install_skills(self))
        self.install.setVisible(not studio.legacy)
        self.reload = file_menu.addAction("&Reload…", lambda: studio.reload_skills(self))
        edit = self.menuBar().addMenu("&Edit")
        self.edit_skill = edit.addAction("Edit Skill…", "Return", self.open_selected)
        self.revert = edit.addAction("Revert skill", self.revert_selected)
        self.class_menu = None
        if not npc:
            views = self.menuBar().addMenu("&Views")
            self.class_menu = views.addMenu("Class")
            classes = QActionGroup(self)
            for field, title in ((None, "All Classes"), *zip(CLASS_FIELDS, (
                f"{light} / {dark}" for light, dark in zip(LIGHT_CLASSES, DARK_CLASSES)
            ))):
                action = self.class_menu.addAction(title)
                action.setCheckable(True)
                action.setChecked(field == self.class_field)
                classes.addAction(action)
                action.triggered.connect(lambda _, field=field: self.filter_class(field))

        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        for name in (("Mobs Skills",) if npc else ("Toon Skills", "Noss Skills")):
            self.tabs.addTab(name)
        saved_page = self.settings.value(self.setting("tab"), TOON, type=int)
        self.tabs.setCurrentIndex(NOSS if saved_page == NOSS and not npc else TOON)
        self.tabs.currentChanged.connect(self.change_page)
        self.model = SkillModel(self)
        self.filter = SkillFilter(self)
        self.filter.setSourceModel(self.model)
        self.filter.setSortRole(SORT_VALUE)
        self.table = QTableView()
        self.table.setModel(self.filter)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setIconSize(QSize(16, 16))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.horizontalHeader().setMinimumSectionSize(33)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for column, (_, _, width) in enumerate(LIST_FIELDS):
            self.table.setColumnWidth(column, width)
        state = self.settings.value(self.setting("header"), QByteArray(), type=QByteArray)
        if not state.isEmpty() and header.restoreState(state):
            self.table.sortByColumn(header.sortIndicatorSection(), header.sortIndicatorOrder())
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self.header_menu)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.selectionModel().currentChanged.connect(self.update_actions)
        self.table.doubleClicked.connect(lambda _: self.open_selected())
        self.search = QLineEdit()
        self.search.setMaximumWidth(265)
        self.search.setPlaceholderText("Name or ID")
        self.search.textChanged.connect(self.filter_text)
        edit.addAction("&Find…", "Ctrl+F", self.show_search)
        self.search_box = QWidget()
        find = QHBoxLayout(self.search_box)
        find.setContentsMargins(4, 2, 4, 2)
        close = QLabel('<a href="close" style="color:#383632;text-decoration:none">×</a>')
        close.linkActivated.connect(self.hide_search)
        find.addWidget(close)
        find.addWidget(QLabel("Find:"))
        find.addWidget(self.search)
        find.addStretch()
        self.search_box.hide()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs)
        layout.addWidget(self.table)
        layout.addWidget(self.search_box)
        self.setCentralWidget(central)
        self.statusBar()
        self.change_page()

    @property
    def filename(self):
        return self.catalog_type.filename

    def setting(self, key):
        return f"{self.settings_group}/{key}"

    def load(self, source, catalog: SkillCatalog, data: Path, database: Path | None):
        self.source, self.catalog, self.database_path = source, catalog, database
        self.icons = NPCSkillAssets(ModelAssets(data))
        self.icon_choices = None
        self.edits = {}
        self.groups = {}
        for key in sorted(catalog.records):
            self.groups.setdefault(key[0], []).append(key)
        self.refresh()

    def clear(self):
        self.source = self.catalog = self.database_path = None
        self.icons = NPCSkillAssets()
        self.icon_choices = None
        self.groups.clear()
        self.edits.clear()
        self.refresh()
        self.hide()

    def changed(self) -> bool:
        return bool(self.edits)

    def available_icons(self):
        if self.icon_choices is None:
            self.icon_choices = []
            for sheet in range(66):
                base = sheet * 1000 + 1
                filename, _, _ = skill_icon_location(base)
                if self.icons.assets.resolve(f"interface/icon/{filename}") is None:
                    continue
                for code in range(base, base + 256):
                    pixmap = self.icons.pixmap(code)
                    if not pixmap.isNull() and not is_blank_icon(pixmap):
                        self.icon_choices.append((code, pixmap))
        return self.icon_choices

    def value(self, key: SkillKey, name: str) -> Scalar:
        patch = self.edits.get(key)
        return patch[name] if patch and name in patch else self.catalog.value(key, SKILL_FIELDS_BY_NAME[name])

    def selected(self) -> SkillKey | None:
        current = self.filter.mapToSource(self.table.currentIndex())
        return self.model.keys[current.row()] if current.isValid() else None

    def refresh(self):
        previous = self.selected()
        self.model.rebuild(self.tabs.currentIndex(), self.class_field)
        if previous is not None and previous in self.model.keys:
            index = self.filter.mapFromSource(self.model.index(self.model.keys.index(previous), 0))
            if index.isValid():
                self.table.selectRow(index.row())
        self.update_actions()

    def change_page(self, *_):
        self.refresh()
        self.settings.setValue(self.setting("tab"), self.tabs.currentIndex())

    def show_search(self):
        self.search_box.show()
        self.search.setFocus()
        self.search.selectAll()

    def hide_search(self):
        self.search.clear()
        self.search_box.hide()
        self.table.setFocus()

    def filter_text(self, text):
        self.filter.set_query(text)
        self.update_actions()

    def filter_class(self, field):
        self.class_field = field
        self.settings.setValue(self.setting("class"), field or "")
        self.refresh()

    def header_menu(self, position):
        if self.class_menu is not None and self.tabs.currentIndex() == TOON:
            self.class_menu.exec(self.table.horizontalHeader().mapToGlobal(position))

    def context_menu(self, position):
        index = self.table.indexAt(position)
        if index.isValid():
            self.table.selectRow(index.row())
        menu = QMenu(self)
        menu.addActions((self.edit_skill, self.revert))
        if self.class_menu is not None and self.tabs.currentIndex() == TOON:
            menu.addMenu(self.class_menu)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def update_actions(self, *_):
        enabled = self.catalog is not None and self.studio.task is None
        key = self.selected()
        self.save.setEnabled(enabled and self.catalog.writable and self.changed())
        self.install.setEnabled(enabled and self.studio.install_database() is not None)
        self.reload.setEnabled(enabled)
        self.edit_skill.setEnabled(enabled and key is not None)
        self.revert.setEnabled(enabled and key is not None and any(rank in self.edits for rank in self.groups.get(key[0], ())))
        if self.class_menu is not None:
            self.class_menu.setEnabled(self.tabs.currentIndex() == TOON)
        name = self.source.name if self.source else self.filename
        self.setWindowTitle(f"{name}{' *' if self.changed() else ''}")
        info = f"{self.filter.rowCount():,} skills"
        if self.edits:
            info += f"    {len(self.edits):,} edited ranks"
        if self.catalog and not self.catalog.writable:
            info += "    Read-only"
        self.statusBar().showMessage(info)

    def open_selected(self):
        key = self.selected()
        if key is None or self.studio.task is not None:
            return
        editor = MobSkillDialog if self.npc else SkillDialog
        dialog = editor(self, key)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        edits = dialog.edits
        dialog.deleteLater()
        if not accepted:
            return
        for rank, changes in edits.items():
            patch = self.edits.setdefault(rank, {})
            for name, value in changes.items():
                if value == self.catalog.value(rank, SKILL_FIELDS_BY_NAME[name]):
                    patch.pop(name, None)
                else:
                    patch[name] = value
            if not patch:
                self.edits.pop(rank, None)
        self.refresh()

    def revert_selected(self):
        if (key := self.selected()) is not None:
            for rank in self.groups[key[0]]:
                self.edits.pop(rank, None)
            self.refresh()

    def restore_window(self) -> bool:
        state = self.settings.value(self.setting("geometry"), QByteArray(), type=QByteArray)
        return not state.isEmpty() and self.restoreGeometry(state)

    def closeEvent(self, event):
        if self.isVisible():
            self.settings.setValue(self.setting("geometry"), self.saveGeometry())
        self.settings.setValue(self.setting("header"), self.table.horizontalHeader().saveState())
        self.settings.sync()
        super().closeEvent(event)
