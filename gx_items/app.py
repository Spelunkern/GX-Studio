from argparse import ArgumentParser
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path
from sqlite3 import Error as SQLiteError
from tempfile import TemporaryDirectory, mkdtemp

from PySide6.QtCore import QObject, QPoint, QRunnable, QSettings, QSize, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QCloseEvent, QFont, QFontDatabase, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox
from PySide6.QtWidgets import QPlainTextEdit, QStackedWidget, QStyle, QStyleFactory, QTabBar, QTableView, QTreeView, QTreeWidget
from PySide6.QtWidgets import QTreeWidgetItem, QVBoxLayout, QWidget

from .asset_view import ASSET_EXTENSIONS, ASSET_FILTER, AssetWindow, read_document
from .binary_sdata import BinarySData, binary_schema
from .binary_sdata_view import BinaryTableDialog
from .cash_catalog import CashCatalog, validate_products
from .cash_view import CashWindow
from .catalog import MAX_BYTES, Catalog, Edits, Item, read_file
from .client_model_view import AUX_FILTER, ClientModelWindow, client_model_type
from .equipment_models import ModelAssets, is_vehicle_mon
from .fields import FIELDS_BY_NAME
from .grade_view import GradeModel
from .gx_connection import DatabaseStatus, connect_gx, detect_server_data, read_catalog, server_data_folder, update_item
from .item_view import LOCKED_PAGES, PAGES, ItemDialog, ItemIcons, ItemModel, ItemsFilter, find_child
from .install import ITEMS, NPC_SKILLS, PRODUCTS, SKILLS, install_catalog, item_rows, product_rows, replace_file
from .itm_catalog import ItmCatalog
from .itm_view import ItmWindow
from .mlt_catalog import MltCatalog
from .mlt_view import MltWindow
from .lapis_fields import LAPIS_DEBUFF_FAMILIES
from .legacy_archive import LegacySource, new_output_folder
from .legacy_conversion import (
    CONVERSION_EPISODES, TARGET_CATALOGS, attach_ep8_quest_translation, convert_catalog, inspect_catalog, inspect_source,
)
from .mon_catalog import MonCatalog
from .mon_view import MonWindow
from .monster_data import MonsterCatalog, read_monster_db
from .monster_view import MonsterWindow
from .npc_skills import NPCSkillAssets
from .npcquest_data import NPCQuestCatalog
from .npcquest_save import save_quest_catalog, validate_targets
from .npcquest_view import NPCQuestWindow
from .pickers import ItemEntry
from .seed import episode_label, file_envelope
from .skill_catalog import NPCSkillCatalog, SKILL_FIELDS_BY_NAME, SkillCatalog
from .skill_view import SkillWindow
from .sqlite_view import DebuffAssets, SQLiteWindow
from .studio_settings import CLIENT_ROOT_KEY, DATABASE_KEY, LEGACY_SOURCE_KEY, SERVER_FOLDER_KEY
from .studio_settings import open_settings, remember_path, studio_directory, workspace_path
from .workspace import TABLE, Workspace, read_database
from .workspace_view import WorkspaceDialog

USER_ROLE = Qt.ItemDataRole.UserRole
PATH_ROLE = USER_ROLE + 1
POPULATED_ROLE = USER_ROLE + 2
QUEST_SERVER_KEY = "npcQuest/serverPath"
STUDIO_BETA = "GX Studio v0.1 Beta - .wld and some server formats are not covered yet"
SDATA_EDITORS = {
    "item.sdata": "items", "cash.sdata": "cash", "skill.sdata": "skills",
    "npcskill.sdata": "npc_skills", "npcquest.sdata": "npc_quest", "monster.sdata": "monsters",
}
KNOWN_SDATA = {*SDATA_EDITORS, "guildhouse.sdata"}
MODEL_EDITORS = {".mon": "mon", ".itm": "itm", ".it2": "itm", ".mlt": "mlt"}
CLIENT_SHORTCUTS = (
    ("Character", "Skill.SData"), ("Item", "Item.SData"), ("Item", "DualLayerClothes.SData"),
    ("Item", "SetItem.SData"), ("Monster", "Monster.mon"), ("Monster", "Monster.SData"),
    ("Monster", "NpcSkill.SData"), ("Npc", "Npc.mon"), ("Npc", "NpcQuest.SData"),
)
MON_TABLES = (
    ("Monster", "Monster.mon"), ("Npc", "Npc.mon"),
    *(("Vehicle", f"Vehicle_{race}_01.MON") for race in ("Hu", "El", "De", "Vi")),
)
RESOURCES = Path(__file__).with_name("resources")
APP_ICON = RESOURCES / "workspace.ico"


def file_metadata(path: Path) -> tuple[str, str]:
    try:
        info = path.stat()
    except OSError:
        return "", ""
    return f"{info.st_size / 1_000_000:.2f} MB", datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")


def set_light_theme(app: QApplication):
    app.setStyle("Fusion")
    app.setFont(QFont("Tahoma" if "Tahoma" in QFontDatabase.families() else "Sans Serif", 9))
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#eeece7",
        QPalette.ColorRole.WindowText: "#272522",
        QPalette.ColorRole.Base: "#f8f6f1",
        QPalette.ColorRole.AlternateBase: "#f1efe9",
        QPalette.ColorRole.Text: "#272522",
        QPalette.ColorRole.Button: "#f0eee9",
        QPalette.ColorRole.ButtonText: "#272522",
        QPalette.ColorRole.ToolTipBase: "#faf7e9",
        QPalette.ColorRole.ToolTipText: "#272522",
        QPalette.ColorRole.Highlight: "#3974b2",
        QPalette.ColorRole.HighlightedText: "#fcfaf5",
        QPalette.ColorRole.Mid: "#c8c4ba",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#827f78"))
    app.setPalette(palette)
    app.setStyleSheet("""
        QMenuBar { background: #eeece7; border-bottom: 1px solid #c9c6be; }
        QMenuBar::item { padding: 3px 8px; }
        QMenuBar::item:selected { background: #d9e5f0; color: #202020; }
        QHeaderView::section { background: #eeece7; padding: 2px 5px;
            border: 0; border-right: 1px solid #d3d0c8; border-bottom: 1px solid #c4c0b7; }
        QTableView, QTreeWidget { background: #f8f6f1; border: 1px solid #bdbab2; }
        QTabBar::tab { padding: 3px 7px; margin-right: 1px; background: #e5e2da;
            border: 1px solid #bab6ad; }
        QTabBar::tab:selected { background: #f8f6f1; border-bottom-color: #f8f6f1; }
        QTabBar::tab:disabled { background: #e6e3de; }
        QStatusBar { background: #eeece7; }
        QLineEdit, QPlainTextEdit { selection-background-color: #3974b2; }
    """)


class TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, operation: Callable[[], object]):
        super().__init__()
        self.operation = operation
        self.signals = TaskSignals()

    def run(self):
        try:
            result = self.operation()
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.done.emit(result)


class ItemWindow(QMainWindow):
    def __init__(self, parent: "Window"):
        super().__init__(parent, Qt.WindowType.Window)
        self.studio = parent
        self.resize(700, 480)
        self.workspace: Workspace | None = None
        self.model: ItemModel | None = None
        self.proxy: ItemsFilter | None = None
        self.edits: Edits = {}

        file = self.menuBar().addMenu("&File")
        file.addAction("&Close", "Ctrl+W", self.close)
        self.save = file.addAction("&Save", "Ctrl+S", parent.review)
        self.install = file.addAction("&Install…", parent.install_items)
        self.install.setVisible(not parent.legacy)
        self.reload = file.addAction("&Reload…", parent.reload_items)
        edit = self.menuBar().addMenu("&Edit")
        self.edit_item = edit.addAction("Edit item…", "Return", self.open_selected)
        self.reset = edit.addAction("Revert item", self.revert_selected)
        edit.addSeparator()
        edit.addAction("&Find…", "Ctrl+F", self.show_search)
        edit.addAction("Find &Next", "F3", self.next_match)
        tools = self.menuBar().addMenu("&Tools")
        tools.addAction("Link SQLite for Export…", parent.choose_export_database).setVisible(not parent.legacy)
        self.show_empty = tools.addAction("Show empty items")
        self.show_empty.setCheckable(True)
        self.show_empty.toggled.connect(self.refilter)

        self.tabs = QTabBar()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setExpanding(False)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        for title, *_ in PAGES:
            index = self.tabs.addTab(title)
            if title in LOCKED_PAGES:
                self.tabs.setTabEnabled(index, False)
        self.tabs.currentChanged.connect(self.change_page)

        self.table = QTableView()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(16, 16))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.horizontalHeader().setMinimumSectionSize(34)
        self.table.doubleClicked.connect(lambda _: self.open_selected())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.table_context)

        self.grades = QTreeView()
        grade_style = QStyleFactory.create("Windows")
        if grade_style is not None:
            grade_style.setParent(self.grades)
            self.grades.setStyle(grade_style)
        self.grades.setUniformRowHeights(True)
        self.grades.setIndentation(16)
        self.grades.setIconSize(QSize(16, 16))
        self.grades.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.grades.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        strip = QPixmap(str(RESOURCES / "grades.png"))
        icons = tuple(QIcon(strip.copy(index * 16, 0, 16, 16)) for index in range(3))
        self.grade_model = GradeModel(icons, self.grades)
        self.grades.setModel(self.grade_model)
        for col, width in enumerate((250, 54, 45, 40, 42, 100)):
            self.grades.setColumnWidth(col, width)
        self.grades.doubleClicked.connect(lambda *_: self.open_selected())
        self.grades.selectionModel().currentChanged.connect(self.update_status)
        self.views = QStackedWidget()
        self.views.addWidget(self.table)
        self.views.addWidget(self.grades)

        self.search_box = QWidget()
        search_layout = QHBoxLayout(self.search_box)
        search_layout.setContentsMargins(4, 2, 4, 2)
        close_search = QLabel('<a href="close" style="color:#383632;text-decoration:none">×</a>')
        close_search.linkActivated.connect(self.hide_search)
        self.search = QLineEdit()
        self.search.setMaximumWidth(265)
        self.search.setPlaceholderText("Name, ID or Type/ID")
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(70)
        self.search_timer.timeout.connect(self.refilter)
        self.search.textChanged.connect(lambda _: self.search_timer.start())
        self.search.returnPressed.connect(self.next_match)
        search_layout.addWidget(close_search)
        search_layout.addWidget(QLabel("Find:"))
        search_layout.addWidget(self.search)
        search_layout.addStretch()
        self.search_box.hide()

        contents = QWidget()
        layout = QVBoxLayout(contents)
        layout.setContentsMargins(3, 3, 3, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs)
        layout.addWidget(self.views)
        layout.addWidget(self.search_box)
        self.setCentralWidget(contents)
        self.statusBar()

    def load_workspace(self, workspace: Workspace):
        previous = self.model
        previous_proxy = self.proxy
        self.grade_model.clear()
        self.workspace = workspace
        self.edits = {}
        self.model = ItemModel(workspace, self.edits, ItemIcons(self.studio.data_root), self)
        self.proxy = ItemsFilter(self.model, self)
        self.table.setModel(self.proxy)
        self.table.selectionModel().currentChanged.connect(self.update_status)
        if previous_proxy is not None:
            previous_proxy.deleteLater()
        if previous is not None:
            previous.deleteLater()
        self.change_page(self.tabs.currentIndex())
        self.update_status()

    def clear_workspace(self):
        previous, previous_proxy = self.model, self.proxy
        self.workspace = self.model = self.proxy = None
        self.edits.clear()
        self.search_timer.stop()
        self.grade_model.clear()
        self.table.setModel(None)
        if previous_proxy is not None:
            previous_proxy.deleteLater()
        if previous is not None:
            previous.deleteLater()
        self.hide()

    def show_search(self):
        self.search_box.show()
        self.search.setFocus()
        self.search.selectAll()

    def hide_search(self):
        self.search.clear()
        self.refilter()
        self.search_box.hide()
        self.table.setFocus()

    def next_match(self):
        if self.search_timer.isActive():
            self.refilter()
        if self.proxy is None or self.tabs.currentIndex() == len(PAGES) - 1:
            return
        count = self.proxy.rowCount()
        if count:
            row = (self.table.currentIndex().row() + 1) % count
            self.table.selectRow(row)
            self.table.scrollTo(self.proxy.index(row, 0))

    def table_context(self, point):
        index = self.table.indexAt(point)
        if not index.isValid():
            return
        self.table.selectRow(index.row())
        menu = QMenu(self)
        menu.addAction(self.edit_item)
        menu.addAction(self.reset)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def change_page(self, page: int):
        if self.proxy is None or self.model is None or not self.tabs.isTabEnabled(page):
            return
        _, _, columns, _ = PAGES[page]
        self.views.setCurrentIndex(0 if columns else 1)
        if columns:
            self.proxy.page = page
            self.apply_filter_text()
            self.model.set_columns(columns)
            self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
            header = self.table.horizontalHeader()
            for col, field in enumerate(columns):
                width = {"Type": 68, "TypeID": 76, "Name": 200, "Faction": 80, "Country": 60}.get(field, 56)
                if "LapisLevel" in columns:
                    width = {"Name": 155, "Icon": 45, "LapisLevel": 48, "LapisCost": 150}.get(field, width)
                if field in ("Type", "TypeID"):
                    width = max(width, header.sectionSizeHint(col) + 8)
                self.table.setColumnWidth(col, width)
            self.finish_filter()
        else:
            self.refilter()

    def apply_filter_text(self):
        self.search_timer.stop()
        self.proxy.search = self.search.text().strip().casefold()
        self.proxy.show_empty = self.show_empty.isChecked()

    def refilter(self, *_):
        if self.proxy is None:
            return
        self.apply_filter_text()
        if self.views.currentIndex() == 0:
            self.proxy.invalidateFilter()
        self.finish_filter()

    def finish_filter(self):
        if self.tabs.currentIndex() == len(PAGES) - 1:
            self.fill_grades()
        elif self.proxy.rowCount() and not self.table.currentIndex().isValid():
            self.table.selectRow(0)
        self.update_status()

    def fill_grades(self):
        assert self.model is not None and self.proxy is not None
        grades = self.grade_model
        old_root = grades.index(0, 0)
        root_open = not old_root.isValid() or self.grades.isExpanded(old_root)
        expanded = {
            grade for row, grade in enumerate(grades.grades)
            if self.grades.isExpanded(grades.index(row, 0, old_root))
        }
        selected = grades.item(self.grades.currentIndex())
        grades.rebuild(self.model, self.proxy)
        root = grades.index(0, 0)
        self.grades.setExpanded(root, root_open or bool(self.proxy.search))
        for row, grade in enumerate(grades.grades):
            if grade in expanded or self.proxy.search:
                self.grades.expand(grades.index(row, 0, root))
        if selected is not None:
            item = self.model.workspace.catalog.items.get(selected.item_id)
            if item is not None and (index := grades.index_for_item(item)).isValid():
                self.grades.expand(index.parent())
                self.grades.setCurrentIndex(index)

    def selected_item(self) -> Item | None:
        if self.proxy is None or self.model is None:
            return None
        if self.views.currentIndex() == 1:
            return self.grade_model.item(self.grades.currentIndex())
        index = self.proxy.mapToSource(self.table.currentIndex())
        return self.model.items[index.row()] if index.isValid() else None

    def update_status(self, *_):
        if self.workspace is None or self.proxy is None:
            return
        changed = sum(map(len, self.edits.values()))
        item = self.selected_item()
        self.setWindowTitle(f"{self.workspace.source.name}{' *' if changed else ''}")
        self.save.setEnabled(bool(changed) and self.studio.task is None)
        self.install.setEnabled(self.studio.task is None and
                                self.studio.install_database() is not None)
        self.reload.setEnabled(self.studio.task is None)
        self.edit_item.setEnabled(item is not None)
        self.reset.setEnabled(item is not None and item.item_id in self.edits)
        if self.views.currentIndex() == 1:
            summary = f"{len(self.grade_model.grades):,} grades"
        else:
            summary = f"{self.proxy.rowCount():,} items"
        if item is not None:
            summary += f"    Type {item.family}    TypeID {item.type_id}"
        if changed:
            summary += f"    {changed} pending changes"
        if getattr(self.workspace.catalog, "legacy_preview", False):
            summary += "    EP5.4 preview · read-only"
        elif getattr(self.workspace.catalog, "archive_preview", False):
            summary += "    Archive preview · read-only"
        elif not self.workspace.catalog.writable:
            summary += "    Encrypted (read-only)"
        self.statusBar().showMessage(summary)

    def open_selected(self):
        item = self.selected_item()
        if item is not None:
            self.edit_catalog_item(item)

    def edit_catalog_item(self, item, *, parent=None):
        if self.model is None or self.workspace is None or self.studio.task is not None:
            return False
        dialog = ItemDialog(parent if parent is not None else self, self.model, item, self.studio.connection.path)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        result = dialog.result_edits
        dialog.deleteLater()
        if accepted and result:
            patch = self.edits.setdefault(item.item_id, {})
            for name, value in result.items():
                if value == self.workspace.catalog.value(item, FIELDS_BY_NAME[name]):
                    patch.pop(name, None)
                else:
                    patch[name] = value
            if not patch:
                self.edits.pop(item.item_id)
            self.model.refresh(item)
            if self.views.currentIndex() == 1:
                self.fill_grades()
            self.update_status()
            return True
        return False

    def revert_selected(self):
        item = self.selected_item()
        if item is not None and self.model is not None:
            self.edits.pop(item.item_id, None)
            self.model.refresh(item)
            if self.views.currentIndex() == 1:
                self.fill_grades()
            self.update_status()


class Window(QMainWindow):
    def __init__(self, database: Path | None = None, *, settings: QSettings | None = None, legacy=False):
        super().__init__()
        self.legacy = legacy
        self.legacy_source = self.legacy_temp = self.legacy_episode = None
        self.legacy_layouts = {}
        self.setWindowTitle("GX Studio — Legacy Data" if legacy else "GX Studio")
        self.resize(731, 350)
        self.setMinimumWidth(570)
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.workspace: Workspace | None = None
        self.data_root = Path()
        self.studio_dir = studio_directory()
        self.settings = settings if settings is not None else open_settings(self.studio_dir)
        self.last_legacy_source = workspace_path(self.settings, LEGACY_SOURCE_KEY, self.studio_dir) if legacy else None
        self.client_data = None if legacy else workspace_path(self.settings, CLIENT_ROOT_KEY, self.studio_dir)
        self.server_folder = None if legacy else workspace_path(self.settings, SERVER_FOLDER_KEY, self.studio_dir)
        self.server_data = server_data_folder(self.server_folder) if self.server_folder else None
        self.quest_link_path = None
        self.client_catalog = self.find_client_catalog(self.client_data)
        self.client_cash = self.find_client_cash(self.client_data)
        self.client_skills = self.find_client_skills(self.client_data)
        self.client_npc_skills = self.find_client_skills(self.client_data, npc=True)
        self.client_quests = self.find_client_npcquest(self.client_data)
        self.task: Task | None = None
        self.connection_task: Task | None = None
        self.on_success: Callable[[object], None] | None = None
        self.manual_database = None if legacy else database.resolve() if database else workspace_path(
            self.settings, DATABASE_KEY, self.studio_dir,
        )
        self.connection_request = None
        self.connection = DatabaseStatus(None, "Client only" if legacy else "Detecting…", "" if legacy else "Looking for GX Server")
        self.item_window = ItemWindow(self)
        self.cash_window = CashWindow(self)
        self.skill_window = SkillWindow(self)
        self.npc_skill_window = SkillWindow(self, npc=True)
        self.quest_window = NPCQuestWindow(self)
        self.mon_window = MonWindow(self)
        self.itm_window = ItmWindow(self)
        self.mlt_window = MltWindow(self)
        self.client_model_window = ClientModelWindow(self)
        self.monster_window = MonsterWindow(self)
        self.asset_window = AssetWindow(self)
        self.model_tables = {
            "mon": (self.mon_window, MonCatalog),
            "itm": (self.itm_window, ItmCatalog),
            "mlt": (self.mlt_window, MltCatalog),
        }
        self.sqlite_window = SQLiteWindow(
            self, self.show_sqlite, self.save_sqlite, self.client_descriptions, self.client_debuff_assets,
            self.client_assets, self.workspace_server_quest,
        )
        self.sized_windows = (
            ("windows/workspaceSize", self), ("windows/itemsSize", self.item_window),
            ("windows/cashSize", self.cash_window), ("windows/skillSize", self.skill_window),
            ("windows/npcSkillSize", self.npc_skill_window),
            ("windows/npcQuestSize", self.quest_window),
            ("windows/monSize", self.mon_window),
            ("windows/itmSize", self.itm_window),
            ("windows/mltSize", self.mlt_window),
            ("windows/clientModelSize", self.client_model_window),
            ("windows/monsterSize", self.monster_window),
            ("windows/assetSize", self.asset_window),
            ("windows/sqliteSize", self.sqlite_window),
        )
        for key, window in self.sized_windows:
            window.setWindowIcon(self.windowIcon())
            saved_size = self.settings.value(key, window.size(), type=QSize)
            if not saved_size.isEmpty():
                window.resize(saved_size)

        file = self.menuBar().addMenu("&File")
        self.open_workspace = file.addAction(
            "Open Client &Folder…" if legacy else "&Open Workspace…", "Ctrl+O", self.choose_workspace,
        )
        self.workspace_settings = file.addAction("Workspace &Settings…", lambda: self.configure_workspace())
        self.open_source = file.addAction("Open Item.SData…", "Ctrl+Shift+O", self.choose_source)
        self.open_cash_source = file.addAction("Open Cash.SData…", self.choose_cash_source)
        self.open_skill_source = file.addAction("Open Skill.SData…", self.choose_skill_source)
        self.open_npc_skill_source = file.addAction("Open NpcSkill.SData…", lambda: self.choose_skill_source(npc=True))
        self.open_quest_source = file.addAction("Open NpcQuest.SData…", self.choose_npcquest_source)
        self.open_mon_source = file.addAction("Open MON…", self.choose_mon_source)
        self.open_itm_source = file.addAction("Open ITM…", self.choose_itm_source)
        self.open_mlt_source = file.addAction("Open MLT…", self.choose_mlt_source)
        self.open_client_model_source = file.addAction("Open ALT, Cloak or Emblem Table…", self.choose_client_model)
        self.open_monster_source = file.addAction("Open Monster.SData…", self.choose_monsters)
        self.open_asset_source = file.addAction("Open Asset…", self.choose_asset_source)
        self.open_server_map_source = file.addAction("Open Server Map…", self.choose_server_map)
        self.open_server_map_source.setVisible(not legacy)
        self.close_workspace = file.addAction(
            "Close &Client Data" if legacy else "Close Client &Workspace", "Ctrl+W", self.close_current_workspace,
        )
        file.addSeparator()
        file.addAction("E&xit", "Ctrl+Q", self.close)
        edit = self.menuBar().addMenu("&Edit")
        self.open_editor = edit.addAction("Open Item.SData", self.open_client_catalog)
        self.open_cash_editor = edit.addAction("Open Cash.SData", self.open_client_cash)
        self.open_skill_editor = edit.addAction("Open Skill.SData", self.open_client_skills)
        self.open_npc_skill_editor = edit.addAction("Open NpcSkill.SData", lambda: self.open_client_skills(npc=True))
        self.open_quest_editor = edit.addAction("Open NpcQuest.SData", self.open_client_npcquest)
        views = self.menuBar().addMenu("&Views")
        self.open_database = views.addAction("Select SQLite…", self.choose_database)
        self.detect_database = views.addAction("Detect GX Database", "F5", self.detect_automatically)
        self.edit_database = views.addAction("Open SQLite Items", self.show_sqlite)
        if legacy:
            self.workspace_settings.setVisible(False)
            self.reopen_legacy_action = self.make_legacy_action("Open Last Client Data", self.reopen_legacy)
            file.insertAction(self.open_source, self.reopen_legacy_action)
            file.insertAction(self.open_source, self.make_legacy_action("Open Client Table…", self.choose_binary_sdata))
            views.menuAction().setVisible(False)
            file.insertAction(self.open_source, self.make_legacy_action("Open Client Archive…", self.choose_legacy_archive))
            self.extract_legacy_action = self.make_legacy_action("Extract Client Data…", self.extract_legacy)
            file.insertAction(self.open_source, self.extract_legacy_action)
            self.convert_legacy_action = self.make_legacy_action("Convert Catalogs to EP5.4…", self.convert_legacy)
            file.insertAction(self.open_source, self.convert_legacy_action)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(("Filename", "Size", "Date", "Status", "Encryption", "Format"))
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.header().setStretchLastSection(False)
        for col, width in enumerate((200, 72, 114, 91, 128, 102)):
            self.tree.setColumnWidth(col, width)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(self.open_node)
        self.sdata_formats = {}
        for editor in self.sdata_editors():
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.RichText)
            label.hide()
            editor.statusBar().addPermanentWidget(label)
            self.sdata_formats[editor] = label
        self.tree.itemExpanded.connect(self.expand_data_folder)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.client_menu)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tree)
        self.beta_notice = QLabel(STUDIO_BETA)
        self.beta_notice.setVisible(not legacy)
        self.beta_notice.setWordWrap(True)
        self.beta_notice.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(self.beta_notice)
        self.setCentralWidget(central)
        self.setAcceptDrops(True)
        self.populate_tree()
        self.update_actions()
        self.connection_timer = QTimer(self)
        self.connection_timer.setInterval(8000)
        self.connection_timer.timeout.connect(self.refresh_connection)
        if not legacy:
            self.connection_timer.start()
            QTimer.singleShot(0, self.refresh_connection)

    def populate_tree(self):
        self.tree.clear()
        self.catalog_nodes = {}
        self.standalone_nodes = []
        self.browser_catalogs = {}
        style = self.style()
        self.client_node = QTreeWidgetItem(self.tree, ("Client",))
        self.client_node.setData(0, USER_ROLE, "client")
        self.client_node.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self.shortcuts_node = QTreeWidgetItem(self.client_node, ("Shortcuts",))
        self.shortcuts_node.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_DirLinkIcon))
        self.all_files_node = QTreeWidgetItem(self.client_node, ("All Files",))
        self.all_files_node.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.server_node = QTreeWidgetItem(self.tree, ("Server Data",))
        self.server_node.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.server_node.setData(0, USER_ROLE, "server")
        self.update_server_node()
        self.database_node = QTreeWidgetItem(self.tree, ("SQLite",))
        self.database_node.setIcon(0, style.standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon))
        self.database_node.setData(0, USER_ROLE, "sqlite")
        self.update_database_node()
        self.server_node.setHidden(self.legacy)
        self.database_node.setHidden(self.legacy)
        if self.client_data is not None:
            self.client_node.setToolTip(0, str(self.client_data))
            available = self.client_data.is_dir()
            self.client_node.setText(3, "Selected" if available else "Unavailable")
            self.client_node.setExpanded(True)
            if available:
                self.set_folder(self.all_files_node, self.client_data)
                shortcuts = [self.client_cash]
                if self.legacy:
                    shortcuts.extend(self.client_data / path for path in self.legacy_layouts if binary_schema(path.name))
                shortcuts.extend(find_child(find_child(self.client_data, folder), name)
                                 for folder, name in CLIENT_SHORTCUTS)
                for path in shortcuts:
                    if path is not None and path.is_file():
                        node = self.make_catalog_node(self.shortcuts_node, path)
                        self.catalog_nodes[path.resolve()] = node
        else:
            self.client_node.setText(3, "Select folder")
        self.shortcuts_node.setExpanded(True)
        self.update_catalog_nodes()

    def set_folder(self, node, path):
        node.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        node.setData(0, USER_ROLE, "folder")
        node.setData(0, PATH_ROLE, str(path))
        node.setToolTip(0, str(path))
        node.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)

    def expand_data_folder(self, node):
        if node.data(0, USER_ROLE) != "folder" or node.data(0, POPULATED_ROLE):
            return
        path = Path(node.data(0, PATH_ROLE))
        try:
            entries = [(entry.is_dir(), entry) for entry in path.iterdir()]
        except OSError as error:
            node.setText(3, "Unavailable")
            node.setToolTip(3, str(error))
            return
        node.setData(0, POPULATED_ROLE, True)
        ancestor = node
        ancestors = set()
        while ancestor is not None and (value := ancestor.data(0, PATH_ROLE)):
            ancestors.add(Path(value).resolve())
            ancestor = ancestor.parent()
        def file_order(item):
            is_dir, entry = item
            stem = entry.stem
            number = (int(stem) if entry.suffix.casefold() == ".svmap" and stem.isascii()
                      and stem.isdecimal() else -1)
            return not is_dir, number < 0, number, entry.name.casefold()

        for is_dir, entry in sorted(entries, key=file_order):
            if is_dir:
                child = QTreeWidgetItem(node, (entry.name,))
                if entry.resolve() not in ancestors:
                    self.set_folder(child, entry)
                else:
                    child.setText(3, "Folder link")
            else:
                child = self.make_catalog_node(node, entry)
                if child.data(0, USER_ROLE) not in ("file", "asset"):
                    resolved = entry.resolve()
                    self.browser_catalogs.setdefault(resolved, []).append(child)
                    if resolved in self.opened_catalogs().values():
                        child.setText(3, "Loaded")
        node.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless)

    def make_catalog_node(self, parent, path, status="", *, kind=None):
        extension = path.suffix.casefold()
        kind = kind or MODEL_EDITORS.get(extension) or SDATA_EDITORS.get(path.name.casefold())
        if kind is None and self.legacy and binary_schema(path.name):
            kind = "ep8_table"
        if kind is None and client_model_type(path) is not None:
            kind = "client_model"
        if kind is None and not self.legacy and extension == ".svmap":
            kind = "asset"
        kind = kind or ("asset" if extension in ASSET_EXTENSIONS else "file")
        size, modified = file_metadata(path)
        node = QTreeWidgetItem(parent, (path.name, size, modified, status))
        node.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.update_encryption(node, path)
        node.setToolTip(0, str(path))
        node.setData(0, USER_ROLE, kind)
        node.setData(0, PATH_ROLE, str(path))
        if kind == "ep8_table":
            node.setText(5, "EP8")
        if kind in SDATA_EDITORS.values():
            self.update_sdata_format(node, self.loaded_sdata().get(path.resolve()))
        return node

    def update_encryption(self, node, path):
        if path.suffix.casefold() == ".sdata":
            value = file_envelope(path, known=path.name.casefold() in KNOWN_SDATA or
                                  (self.legacy and binary_schema(path.name) is not None))
            node.setText(4, value)
            node.setToolTip(4, value)

    def sdata_editors(self):
        return (self.item_window, self.cash_window, self.skill_window, self.npc_skill_window,
                self.quest_window, self.monster_window)

    def loaded_sdata(self):
        return {path: catalog for path, catalog in (
            (self.workspace.source, self.workspace.catalog) if self.workspace else (None, None),
            *((editor.source, editor.catalog) for editor in self.sdata_editors()[1:]),
        ) if path is not None and catalog is not None}

    def catalog_format(self, catalog, path):
        if not self.legacy:
            return episode_label(catalog)
        relative = path.relative_to(self.client_data) if self.client_data and path.is_relative_to(self.client_data) else None
        episode = (getattr(catalog, "legacy_episode", None) or self.legacy_layouts.get(relative)
                   or (self.legacy_episode if relative in self.legacy_layouts else None))
        if episode == "5.4":
            return episode_label(catalog, canonical=True)
        if episode:
            return f"EP{episode}"
        return "Shared format" if catalog is not None else ""

    def update_sdata_format(self, node, catalog):
        path = Path(node.data(0, PATH_ROLE)).resolve()
        text = self.catalog_format(catalog, path)
        rich = "<span" in text
        node.setText(5, "" if rich else text)
        node.setToolTip(5, "EP5.4 (GX)" if rich else text)
        if not rich:
            if self.tree.itemWidget(node, 5) is not None:
                self.tree.removeItemWidget(node, 5)
            return
        label = self.tree.itemWidget(node, 5)
        if label is None:
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setAccessibleName("EP5.4 (GX)")
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.tree.setItemWidget(node, 5, label)
        label.setText(text)

    def opened_catalogs(self):
        return {
            "items": self.workspace.source if self.workspace else None, "cash": self.cash_window.source,
            "skills": self.skill_window.source, "npc_skills": self.npc_skill_window.source,
            "npc_quest": self.quest_window.source,
            "monsters": self.monster_window.source, "client_model": self.client_model_window.source,
            **{kind: editor.source for kind, (editor, _) in self.model_tables.items()},
        }

    def update_catalog_nodes(self):
        opened = self.opened_catalogs()
        loaded = self.loaded_sdata()
        for editor, label in self.sdata_formats.items():
            catalog = self.workspace.catalog if editor is self.item_window and self.workspace else getattr(editor, "catalog", None)
            path = self.workspace.source if editor is self.item_window and self.workspace else getattr(editor, "source", None)
            label.setText(self.catalog_format(catalog, path) if path else "")
            label.setVisible(catalog is not None)
        if self.legacy and self.legacy_source is None:
            self.convert_legacy_action.setEnabled(any(path.name.casefold() in TARGET_CATALOGS
                                                       and getattr(catalog, "legacy_episode", None) != "8"
                                                       for path, catalog in loaded.items()))
        for node in self.standalone_nodes:
            self.shortcuts_node.removeChild(node)
        self.standalone_nodes.clear()
        for path in self.catalog_nodes.keys() | self.browser_catalogs.keys():
            for node in [self.catalog_nodes.get(path), *self.browser_catalogs.get(path, ())]:
                if node is None:
                    continue
                kind = node.data(0, USER_ROLE)
                node.setText(3, "Loaded" if opened.get(kind) == path else "")
                for column, value in enumerate(file_metadata(path), 1):
                    node.setText(column, value)
                self.update_encryption(node, path)
                if kind in SDATA_EDITORS.values():
                    self.update_sdata_format(node, loaded.get(path))
        for kind, path in opened.items():
            if path is not None and path not in self.catalog_nodes:
                if kind in ("itm", "mlt", "client_model") or is_vehicle_mon(path):
                    continue
                self.client_node.setExpanded(True)
                self.standalone_nodes.append(self.make_catalog_node(self.shortcuts_node, path, "Loaded", kind=kind))

    def client_menu(self, position):
        node = self.tree.itemAt(position)
        menu = QMenu(self)
        if node is self.client_node:
            menu.addAction(self.open_workspace)
            if self.client_data is not None:
                menu.addAction(self.close_workspace)
        elif node is self.server_node:
            menu.addAction("Select Server Data…", lambda: self.configure_workspace("server"))
        elif node is self.database_node:
            menu.addAction(self.open_database)
            menu.addAction(self.detect_database)
        else:
            return
        if not self.legacy:
            menu.addAction(self.workspace_settings)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def update_server_node(self):
        node = self.server_node
        folder = self.server_data or self.server_folder
        status = ("Selected" if self.server_folder else "Detected") if self.server_data else (
            "Unavailable" if self.server_folder else "Select folder"
        )
        node.setText(3, status)
        detail = str(folder) if folder else "Select a GX Server folder in Workspace Settings."
        node.setToolTip(0, detail)
        node.setToolTip(3, detail)
        expanded = node.isExpanded() or node.childCount() == 0
        node.takeChildren()
        if (source := self.workspace_server_quest()) is not None and source.is_file():
            self.make_catalog_node(node, source, kind="server_quest")
        if self.server_data and (maps := find_child(self.server_data, "maps")).is_dir():
            self.set_folder(QTreeWidgetItem(node, (maps.name,)), maps)
        node.setExpanded(expanded)

    def update_database_node(self):
        node = self.database_node
        connection = self.connection
        node.setText(3, connection.status)
        color = "#2a6639" if connection.editable else "#74552b"
        node.setForeground(3, QColor(color))
        tooltip = "\n".join(filter(None, (str(connection.path) if connection.path else "", connection.detail)))
        node.setToolTip(0, tooltip)
        node.setToolTip(3, tooltip)
        expanded = node.isExpanded() or not node.childCount()
        node.takeChildren()
        if connection.path:
            size, modified = file_metadata(connection.path)
            filename = QTreeWidgetItem(node, (connection.path.name, size, modified, ""))
            filename.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
            filename.setData(0, USER_ROLE, "sqlite")
            filename.setToolTip(0, tooltip)
            filename.setToolTip(3, tooltip)
            if connection.connected:
                table = QTreeWidgetItem(filename, (TABLE, "", "", "Editable" if connection.editable else "Read-only"))
                table.setData(0, USER_ROLE, "sqlite")
                filename.setExpanded(True)
            node.setExpanded(expanded)

    def open_node(self, item, _):
        kind = item.data(0, USER_ROLE)
        if kind == "items":
            self.open_catalog(Path(item.data(0, PATH_ROLE)))
        elif kind == "cash":
            self.open_cash(Path(item.data(0, PATH_ROLE)))
        elif kind == "skills":
            self.open_skills(Path(item.data(0, PATH_ROLE)))
        elif kind == "npc_skills":
            self.open_skills(Path(item.data(0, PATH_ROLE)), npc=True)
        elif kind == "npc_quest":
            self.open_npcquest(Path(item.data(0, PATH_ROLE)))
        elif kind == "monsters":
            self.open_monsters(Path(item.data(0, PATH_ROLE)))
        elif kind == "ep8_table":
            self.open_binary_sdata(Path(item.data(0, PATH_ROLE)))
        elif kind == "client_model":
            self.open_client_model(Path(item.data(0, PATH_ROLE)))
        elif kind in self.model_tables:
            self.open_model_table(Path(item.data(0, PATH_ROLE)), kind=kind)
        elif kind == "asset":
            self.open_asset(Path(item.data(0, PATH_ROLE)))
        elif kind == "client":
            self.choose_workspace()
        elif kind == "server":
            self.configure_workspace("server")
        elif kind == "server_quest":
            if self.client_quests is not None:
                self.open_npcquest(self.client_quests)
            else:
                QMessageBox.information(self, "Server NpcQuest.SData",
                                        "Select Client Data with NpcQuest.SData to edit and synchronize the server rules.")
        elif kind == "sqlite":
            if self.connection.connected:
                self.show_sqlite()
            else:
                self.choose_database()

    def show_catalog(self):
        if self.workspace is None or self.task is not None:
            return
        if not self.item_window.isVisible():
            self.item_window.move(self.frameGeometry().topLeft() + QPoint(60, 50))
        self.item_window.showNormal()
        self.item_window.raise_()
        self.item_window.activateWindow()

    def open_data_path(self, path, database=None):
        if self.legacy:
            database = None
            if path.is_dir() or path.suffix.casefold() == ".sah":
                self.open_legacy_client(path)
                return
            if binary_schema(path.name):
                self.open_binary_sdata(path)
                return
        filename = path.name.casefold()
        if filename == "cash.sdata":
            self.open_cash(path, database)
        elif filename == "skill.sdata":
            self.open_skills(path, database)
        elif filename == "npcskill.sdata":
            self.open_skills(path, database, npc=True)
        elif filename == "npcquest.sdata":
            self.open_npcquest(path)
        elif filename == "monster.sdata":
            self.open_monsters(path)
        elif client_model_type(path) is not None:
            self.open_client_model(path)
        elif path.suffix.casefold() in MODEL_EDITORS:
            self.open_model_table(path)
        elif path.suffix.casefold() in ASSET_EXTENSIONS or (not self.legacy and path.suffix.casefold() == ".svmap"):
            self.open_asset(path)
        else:
            self.load(path, database)

    def open_catalog(self, source: Path):
        if self.task is not None:
            return
        if self.workspace is not None and self.workspace.source == source.resolve():
            self.show_catalog()
        elif not self.item_window.edits or self.confirm_discard(items_only=True):
            self.load(source)

    def open_client_catalog(self):
        if self.workspace is not None:
            self.show_catalog()
        elif self.client_catalog is not None:
            self.open_catalog(self.client_catalog)

    def open_client_cash(self):
        if source := self.cash_window.source or self.client_cash:
            self.open_cash(source)

    def open_client_skills(self, *, npc: bool = False):
        editor = self.npc_skill_window if npc else self.skill_window
        if source := editor.source or (self.client_npc_skills if npc else self.client_skills):
            self.open_skills(source, npc=npc)

    def open_client_npcquest(self):
        if source := self.quest_window.source or self.client_quests:
            self.open_npcquest(source)

    def update_actions(self):
        busy = self.task is not None
        self.open_source.setEnabled(not busy)
        self.open_cash_source.setEnabled(not busy)
        self.open_skill_source.setEnabled(not busy)
        self.open_npc_skill_source.setEnabled(not busy)
        self.open_quest_source.setEnabled(not busy)
        self.open_mon_source.setEnabled(not busy)
        self.open_itm_source.setEnabled(not busy)
        self.open_mlt_source.setEnabled(not busy)
        self.open_client_model_source.setEnabled(not busy)
        self.open_monster_source.setEnabled(not busy)
        self.open_asset_source.setEnabled(not busy)
        self.open_server_map_source.setEnabled(not busy)
        self.open_workspace.setEnabled(not busy)
        self.workspace_settings.setEnabled(not busy)
        self.close_workspace.setEnabled(not busy and (self.workspace is not None or self.cash_window.source is not None
                                                       or self.skill_window.source is not None
                                                       or self.npc_skill_window.source is not None
                                                       or self.quest_window.source is not None
                                                       or self.mon_window.source is not None
                                                       or self.itm_window.source is not None
                                                       or self.mlt_window.source is not None
                                                       or self.client_model_window.source is not None
                                                       or self.monster_window.source is not None
                                                       or self.client_data is not None))
        self.open_editor.setEnabled(not busy and (self.workspace is not None or self.client_catalog is not None))
        self.open_cash_editor.setEnabled(not busy and (self.cash_window.source is not None or self.client_cash is not None))
        self.open_skill_editor.setEnabled(not busy and (self.skill_window.source is not None or self.client_skills is not None))
        self.open_npc_skill_editor.setEnabled(not busy and (self.npc_skill_window.source is not None or self.client_npc_skills is not None))
        self.open_quest_editor.setEnabled(not busy and (self.quest_window.source is not None or self.client_quests is not None))
        self.open_database.setEnabled(not busy and self.connection_task is None)
        self.detect_database.setEnabled(not busy and self.connection_task is None)
        self.edit_database.setEnabled(not busy and self.connection.connected)
        if self.legacy:
            self.reopen_legacy_action.setEnabled(not busy and self.last_legacy_source is not None
                                                 and self.last_legacy_source.exists())
            self.extract_legacy_action.setEnabled(not busy and self.legacy_source is not None)
            if self.legacy_source is not None:
                convertible = any(path.name.casefold() in TARGET_CATALOGS and episode != "8"
                                  for path, episode in self.legacy_layouts.items())
            else:
                convertible = any(path.name.casefold() in TARGET_CATALOGS
                                  and getattr(catalog, "legacy_episode", None) != "8"
                                  for path, catalog in self.loaded_sdata().items())
            self.convert_legacy_action.setEnabled(not busy and convertible)
        self.item_window.setEnabled(not busy)
        self.cash_window.setEnabled(not busy)
        self.skill_window.setEnabled(not busy)
        self.npc_skill_window.setEnabled(not busy)
        self.quest_window.setEnabled(not busy)
        self.mon_window.setEnabled(not busy)
        self.itm_window.setEnabled(not busy)
        self.mlt_window.setEnabled(not busy)
        self.client_model_window.setEnabled(not busy)
        self.monster_window.setEnabled(not busy)
        self.sqlite_window.setEnabled(not busy)
        self.asset_window.setEnabled(not busy)
        self.asset_window.update_map_status()
        self.item_window.update_status()
        self.cash_window.update_actions()
        self.skill_window.update_actions()
        self.npc_skill_window.update_actions()
        self.quest_window.update_actions()
        self.mon_window.update_actions()
        self.itm_window.update_actions()
        self.mlt_window.update_actions()
        self.client_model_window.update_actions()
        self.monster_window.update_actions()
        if not busy and self.legacy_source:
            episode = f"EP{self.legacy_episode} · " if self.legacy_episode else ""
            self.statusBar().showMessage(f"{episode}{self.legacy_source.path}")
        elif not busy:
            directory = self.client_data or (self.data_root if self.workspace is not None else None)
            self.statusBar().showMessage(str(directory) if directory else "Ready")

    def confirm_discard(self, *, items_only: bool = False) -> bool:
        if not self.item_window.edits and (items_only or not any(
            editor.changed() for editor in (
                self.cash_window, self.skill_window, self.npc_skill_window, self.quest_window,
                self.mon_window, self.itm_window, self.mlt_window, self.client_model_window, self.monster_window,
                self.asset_window,
            )
        )):
            return True
        return QMessageBox.question(
            self, "Unsaved Changes", "Discard changes to Item.SData?" if items_only else "Discard the current changes?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Discard

    def make_legacy_action(self, text, callback):
        action = QAction(text, self)
        action.triggered.connect(callback)
        return action

    def choose_legacy_archive(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Open Client Archive", str(self.legacy_open_directory()), "SAH (*.sah *.SAH)",
        )
        if file:
            self.open_legacy_client(Path(file))

    def open_legacy_client(self, path):
        if self.task is not None or not self.confirm_discard():
            return

        def read():
            source = LegacySource.open(path)
            if source.archive is None:
                try:
                    directory = self.resolve_client_data(source.path)
                except ValueError:
                    if not any(binary_schema(entry.path.name) for entry in source.entries):
                        raise
                else:
                    if directory != source.path:
                        source = LegacySource.open(directory)
            layouts, episode = inspect_source(source)
            temporary = None
            directory = source.path
            if source.archive:
                temporary = TemporaryDirectory(prefix="gx-client-")
                try:
                    directory = Path(temporary.name) / "data"
                    source.extract(directory)
                except Exception:
                    temporary.cleanup()
                    raise
            return source, directory, temporary, layouts, episode

        def loaded(result):
            source, directory, temporary, layouts, episode = result
            old_temp = self.legacy_temp
            self.legacy_source, self.legacy_temp = source, temporary
            self.legacy_layouts, self.legacy_episode = layouts, episode
            self.set_client_data(directory)
            self.save_legacy_source(path)
            self.update_actions()
            if old_temp is not None:
                old_temp.cleanup()

        self.run_task("Opening client data…", read, loaded)

    def legacy_open_directory(self):
        if self.last_legacy_source is not None:
            path = self.last_legacy_source
            if path.is_dir():
                return path
            if path.parent.is_dir():
                return path.parent
        return Path.home()

    def remember_legacy_source(self, path):
        if not self.legacy or (self.client_data and path.is_relative_to(self.client_data)):
            return
        self.save_legacy_source(path)

    def save_legacy_source(self, path):
        self.last_legacy_source = path.resolve()
        remember_path(self.settings, LEGACY_SOURCE_KEY, self.last_legacy_source, self.studio_dir)
        self.save_workspace_settings()
        self.reopen_legacy_action.setEnabled(self.task is None and self.last_legacy_source.exists())

    def reopen_legacy(self):
        if self.last_legacy_source is not None:
            self.open_data_path(self.last_legacy_source)

    def choose_binary_sdata(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open EP8 Client Table", str(self.legacy_open_directory()), "SData (*.sdata *.SData *.SDATA)",
        )
        if path:
            self.open_binary_sdata(Path(path))

    def open_binary_sdata(self, source):
        if self.task is not None:
            return
        source = source.resolve()

        def loaded(table):
            self.remember_legacy_source(source)
            self.update_actions()
            dialog = BinaryTableDialog(source.name, table, self)
            dialog.exec()
            dialog.deleteLater()

        self.run_task(f"Loading {source.name}…", lambda: BinarySData.open(source), loaded)

    def read_client_catalog(self, path, parser):
        if not self.legacy:
            return parser.open(path)
        name = next(name for name, catalog_type in TARGET_CATALOGS.items() if parser is catalog_type)
        catalog, episode = inspect_catalog(name, read_file(path))
        catalog.legacy_episode = episode
        if episode == "8" and parser is NPCQuestCatalog:
            translations = sorted((candidate for candidate in path.parent.iterdir()
                                   if candidate.name.casefold().startswith("npcquesttrans")
                                   and candidate.suffix.casefold() == ".sdata"),
                                  key=lambda candidate: (candidate.name.casefold() != "npcquesttrans_usa.sdata",
                                                         candidate.name.casefold()))
            for translation in translations:
                try:
                    attach_ep8_quest_translation(catalog, translation.name, read_file(translation))
                except (OSError, ValueError):
                    continue
                break
            else:
                catalog.legacy_text_status = " · No matching translation loaded"
        if self.legacy_temp and path.resolve().is_relative_to(Path(self.legacy_temp.name)):
            catalog.writable = False
            catalog.archive_preview = True
        return catalog

    def choose_legacy_output(self, title, name):
        path, _ = QFileDialog.getSaveFileName(self, title, str(Path.home() / name))
        return Path(path) if path else None

    def extract_legacy(self):
        if self.task is not None or self.legacy_source is None:
            return
        destination = self.choose_legacy_output("Choose a New Extraction Folder", "legacy-data")
        if destination:
            source = self.legacy_source
            working_directory = self.client_data
            self.run_task("Extracting client data…",
                          lambda: (LegacySource.open(working_directory) if source.archive else source).extract(destination),
                          lambda count: self.statusBar().showMessage(f"Extracted {count:,} files to {destination}"))

    def convert_legacy(self):
        if self.task is not None:
            return
        source = self.legacy_source
        detected_episode = self.legacy_episode
        if source is not None:
            inputs = [(entry.path, lambda entry=entry: source.read_catalog(entry, MAX_BYTES))
                      for entry in source.entries if entry.path.name.casefold() in TARGET_CATALOGS]
        else:
            inputs = [(Path(path.name), lambda path=path: read_file(path)) for path in self.loaded_sdata()
                      if path.name.casefold() in TARGET_CATALOGS]

        def prepare():
            results = []
            for path, read in inputs:
                try:
                    raw = read()
                    _, episode = inspect_catalog(path.name, raw)
                    if episode == "8":
                        raise ValueError("EP8 conversion to EP5.4 is unavailable.")
                    episode = episode if episode in CONVERSION_EPISODES else detected_episode
                    if episode not in CONVERSION_EPISODES:
                        episode = "4.5"  # A validated shared layout needs only decryption.
                    data, losses = convert_catalog(path.name, raw, episode)
                except (OSError, ValueError) as error:
                    results.append((path, None, str(error)))
                else:
                    detail = "\n".join(f"  {count:,} {label}" for label, count in losses.items())
                    results.append((path, data, detail))
            return results

        self.run_task("Checking catalogs for EP5.4 conversion…", prepare, self.review_legacy_conversion)

    def review_legacy_conversion(self, results):
        converted = [(path, data) for path, data, _ in results if data is not None]
        box = QMessageBox(self)
        box.setWindowTitle("Convert to EP5.4 (GX)")
        box.setText(f"{len(converted)} of {len(results)} catalogs can be written in GX EP5.4 format.")
        box.setDetailedText("\n\n".join(f"{path}\n{detail or '  Ready for EP5.4'}" for path, _, detail in results))
        if not converted:
            box.setIcon(QMessageBox.Icon.Warning)
            box.exec()
            return
        if any(detail for _, _, detail in results):
            box.setInformativeText("Show Details lists values that will change and catalogs that could not be converted.")
        save = box.addButton("Choose Output Folder…", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() != save:
            return
        destination = self.choose_legacy_output("Choose a New EP5.4 Output Folder", "gx-ep5.4")
        if destination:
            source = self.legacy_source

            def write():
                if source is not None:
                    source.validate_destination(destination)
                with new_output_folder(destination) as staging:
                    for relative, data in converted:
                        output = staging / relative
                        output.parent.mkdir(parents=True, exist_ok=True)
                        with output.open("xb") as file:
                            file.write(data)

            self.run_task("Writing EP5.4 catalogs…", write,
                          lambda _: self.statusBar().showMessage(f"Saved {len(converted)} catalogs to {destination}"))

    def configure_workspace(self, focus="client"):
        if self.task is None:
            WorkspaceDialog(self, focus).exec()

    def resolve_client_data(self, directory: Path) -> Path:
        directory = directory.resolve(strict=True)
        for candidate in (directory, find_child(directory, "data"), find_child(find_child(directory, "Client"), "data")):
            if candidate.is_dir() and (
                any(find(candidate) is not None for find in (
                    self.find_client_catalog, self.find_client_cash, self.find_client_skills,
                    self.find_client_npcquest, self.find_client_monsters,
                )) or self.find_client_skills(candidate, npc=True) is not None
                or self.find_client_models(candidate) or self.has_client_model_tables(candidate)
            ):
                return candidate.resolve()
        client = "Shaiya" if self.legacy else "Shaiya GX"
        raise ValueError(f"Select a {client} client install folder or its data folder with supported client files.")

    def choose_workspace(self):
        if self.task is not None:
            return
        initial = (self.legacy_open_directory() if self.legacy else
                   self.client_data if self.client_data and self.client_data.is_dir() else Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select Client Data Folder", str(initial))
        if not path:
            return
        if self.legacy:
            self.open_legacy_client(Path(path))
            return
        try:
            directory = self.resolve_client_data(Path(path))
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "Open Client Data" if self.legacy else "Open Workspace", str(error))
            return
        if directory == self.client_data or self.confirm_discard():
            self.set_client_data(directory)

    def apply_workspace(self, client, server, database) -> bool:
        if self.task is not None or (client != self.client_data and not self.confirm_discard()):
            return False
        changed = (server, database) != (self.server_folder, self.manual_database)
        self.server_folder, self.manual_database = server, database
        remember_path(self.settings, SERVER_FOLDER_KEY, server, self.studio_dir)
        remember_path(self.settings, DATABASE_KEY, database, self.studio_dir)
        if changed:
            self.server_data = server_data_folder(server) if server else None
            self.connection = DatabaseStatus(None, "Detecting…", "Looking for GX SQLite")
            self.sqlite_window.hide()
        if client != self.client_data:
            self.set_client_data(client)
        else:
            self.save_workspace_settings()
            if changed:
                self.update_server_node()
                self.update_database_node()
                self.update_actions()
        if changed:
            self.refresh_connection()
        return True

    @staticmethod
    def find_client_catalog(directory: Path | None) -> Path | None:
        if directory is None:
            return None
        try:
            source = find_child(find_child(directory, "Item"), "Item.SData")
            return source.resolve() if source.is_file() else None
        except OSError:
            return None

    @staticmethod
    def find_client_cash(directory: Path | None) -> Path | None:
        if directory is None:
            return None
        try:
            for folder in (find_child(directory, "Item"), directory):
                source = find_child(folder, "Cash.SData")
                if source.is_file():
                    return source.resolve()
        except OSError:
            pass
        return None

    @staticmethod
    def find_client_skills(directory: Path | None, *, npc: bool = False) -> Path | None:
        if directory is None:
            return None
        try:
            catalog = NPCSkillCatalog if npc else SkillCatalog
            source = find_child(find_child(directory, "Monster" if npc else "Character"), catalog.filename)
            return source.resolve() if source.is_file() else None
        except OSError:
            return None

    @staticmethod
    def find_client_npcquest(directory: Path | None) -> Path | None:
        if directory is None:
            return None
        source = find_child(find_child(directory, "Npc"), NPCQuestCatalog.filename)
        return source.resolve() if source.is_file() else None

    @staticmethod
    def find_client_monsters(directory: Path):
        source = find_child(find_child(directory, "Monster"), "Monster.SData")
        return source.resolve() if source.is_file() else None

    @staticmethod
    def find_client_models(directory: Path | None):
        if directory is None:
            return ()
        return tuple(path for folder, name in MON_TABLES
                     if (path := find_child(find_child(directory, folder), name)).is_file())

    @staticmethod
    def has_client_model_tables(directory: Path):
        items, characters = (find_child(directory, folder) for folder in ("Item", "Character"))
        if items.is_dir() and any(path.suffix.casefold() in (".itm", ".it2") and path.is_file() for path in items.iterdir()):
            return True
        if characters.is_dir() and any(
            path.suffix.casefold() in (".mlt", ".alt") and path.is_file()
            for race in characters.iterdir() if race.is_dir()
            for path in race.iterdir()
        ):
            return True
        return find_child(directory, "cloak").is_dir()

    def set_client_data(self, directory: Path | None):
        if directory != self.client_data or directory is None:
            self.item_window.clear_workspace()
            self.cash_window.clear()
            self.skill_window.clear()
            self.npc_skill_window.clear()
            self.quest_window.clear()
            self.mon_window.clear()
            self.itm_window.clear()
            self.mlt_window.clear()
            self.client_model_window.clear()
            self.monster_window.clear()
            self.asset_window.clear()
            self.asset_window.close()
            self.workspace = None
        self.client_data = directory
        self.client_catalog = self.find_client_catalog(directory)
        self.client_cash = self.find_client_cash(directory)
        self.client_skills = self.find_client_skills(directory)
        self.client_npc_skills = self.find_client_skills(directory, npc=True)
        self.client_quests = self.find_client_npcquest(directory)
        if not self.legacy:
            remember_path(self.settings, CLIENT_ROOT_KEY, directory, self.studio_dir)
            self.save_workspace_settings()
        self.populate_tree()
        self.update_actions()

    def save_workspace_settings(self):
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            QMessageBox.warning(self, "Workspace Settings", f"Could not save the workspace settings in\n{self.settings.fileName()}")

    def choose_source(self):
        initial = str(self.legacy_open_directory()) if self.legacy else str(self.client_data or "")
        path, _ = QFileDialog.getOpenFileName(self, "Open Item.SData", initial, "SData (*.sdata *.SData);;All Files (*)")
        if path:
            self.open_catalog(Path(path))

    def choose_cash_source(self):
        initial = str(self.legacy_open_directory()) if self.legacy else str(self.client_data or "")
        path, _ = QFileDialog.getOpenFileName(self, "Open Cash.SData", initial, "SData (*.sdata *.SData)")
        if path:
            self.open_cash(Path(path))

    def choose_skill_source(self, *, npc: bool = False):
        initial = str(self.legacy_open_directory()) if self.legacy else str(self.client_data or "")
        filename = NPCSkillCatalog.filename if npc else SkillCatalog.filename
        path, _ = QFileDialog.getOpenFileName(self, f"Open {filename}", initial, "SData (*.sdata *.SData)")
        if path:
            self.open_skills(Path(path), npc=npc)

    def choose_npcquest_source(self):
        initial = str(self.legacy_open_directory()) if self.legacy else str(self.client_data or "")
        path, _ = QFileDialog.getOpenFileName(self, "Open Client NpcQuest.SData", initial, "SData (*.sdata *.SData)")
        if path:
            self.open_npcquest(Path(path))

    def choose_mon_source(self):
        initial = str(self.legacy_open_directory()) if self.legacy else str(self.client_data or "")
        path, _ = QFileDialog.getOpenFileName(self, "Open MON Model Table", initial, "MON files (*.mon *.MON)")
        if path:
            self.open_mon(Path(path))

    def open_mon(self, source: Path, *, reload=False):
        self.open_model_table(source, reload=reload, kind="mon")

    def choose_itm_source(self):
        initial = str(find_child(self.client_data, "Item")) if self.client_data else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open ITM Model Table", initial, ItmWindow.file_filter)
        if path:
            self.open_itm(Path(path))

    def open_itm(self, source: Path, *, reload=False):
        self.open_model_table(source, reload=reload, kind="itm")

    def choose_mlt_source(self):
        initial = str(find_child(self.client_data, "Character")) if self.client_data else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open MLT Model Table", initial, MltWindow.file_filter)
        if path:
            self.open_mlt(Path(path))

    def open_mlt(self, source: Path, *, reload=False):
        self.open_model_table(source, reload=reload, kind="mlt")

    def choose_client_model(self):
        initial = self.client_model_window.source
        initial = str(initial.parent if initial else self.client_data or "")
        path, _ = QFileDialog.getOpenFileName(self, "Open Animation or Cloak Table", initial, AUX_FILTER)
        if path:
            self.open_client_model(Path(path))

    def open_client_model(self, source: Path, *, reload=False):
        if self.task is not None:
            return
        source = source.resolve()
        catalog_type = client_model_type(source)
        if catalog_type is None:
            QMessageBox.warning(self, "Open Client Table", "Select an ALT, PC, CTL or cloak emblem table.")
            return
        editor = self.client_model_window
        if source == editor.source and not reload:
            self.show_editor(editor)
            return
        if editor.changed() and QMessageBox.question(
            editor, "Unsaved Changes", "Discard the staged client table edits?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Discard:
            return
        selected = max(0, editor.table.currentRow()) if source == editor.source else 0

        def loaded(catalog):
            editor.load(source, catalog, selected)
            self.update_catalog_nodes()
            self.show_editor(editor)

        self.run_task(f"Loading {source.name}…", lambda: catalog_type.open(source), loaded)

    def choose_monsters(self):
        initial = str(find_child(self.client_data, "Monster")) if self.client_data else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open Monster.SData", initial, "SData (*.sdata *.SData)")
        if path:
            self.open_monsters(Path(path))

    def open_monsters(self, source: Path, *, reload=False):
        if self.task is not None:
            return
        source = source.resolve()
        editor = self.monster_window
        same = source == editor.source
        if same and not reload:
            self.show_editor(editor)
            return
        if (bool(editor.client_edits) if same else editor.changed()) and QMessageBox.question(
            editor, "Unsaved Changes", "Discard staged client edits?" if same else "Discard the staged monster edits?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Discard:
            return
        database = self.connection.path if self.connection.connected else None
        workspace = self.client_data
        selected = editor.selected()

        def read():
            catalog = self.read_client_catalog(source, MonsterCatalog)
            model_path = find_child(source.parent, "monster.MON")
            if not model_path.is_file() and workspace is not None:
                model_path = find_child(find_child(workspace, "Monster"), "monster.MON")
            models = ()
            if model_path.is_file():
                try:
                    models = MonCatalog.open(model_path).records
                except (OSError, ValueError):
                    pass
            references = self.monster_references(model_path.parent, workspace or model_path.parent.parent)
            rows, drops, status = {}, None, ""
            if database is not None and not same:
                try:
                    rows, drops = read_monster_db(database)
                except (OSError, ValueError, SQLiteError) as error:
                    status = str(error)
            return catalog, model_path.parent, models, rows, drops, status, references

        def loaded(result):
            catalog, folder, models, rows, drops, status, references = result
            if same:
                editor.load_client(source, catalog, folder, models, references)
                editor.refresh(selected)
            else:
                editor.load(source, catalog, folder, models, database if not status else None, rows, drops, status, references)
            self.remember_legacy_source(source)
            self.update_catalog_nodes()
            self.show_editor(editor)
            if editor.database is None and not editor.database_status:
                editor.reload_sqlite()

        self.run_task("Loading Monster.SData…", read, loaded)

    def monster_references(self, folder, workspace):
        skill_names, grades = {}, {}
        try:
            skills = self.read_client_catalog(find_child(folder, "NpcSkill.SData"), NPCSkillCatalog)
            for identifier in sorted(skills.records):
                skill_names.setdefault(identifier[0], str(skills.value(identifier, SKILL_FIELDS_BY_NAME["Name"])))
        except (OSError, ValueError):
            pass
        try:
            items_source = self.find_client_catalog(workspace)
            if items_source is not None:
                catalog = self.read_client_catalog(items_source, Catalog)
                for item_id, item in catalog.items.items():
                    grade = catalog.value(item, FIELDS_BY_NAME["Grade"])
                    if grade:
                        grades.setdefault(grade, []).append((item_id, str(catalog.value(item, FIELDS_BY_NAME["Name"]))))
        except (OSError, ValueError):
            pass
        return skill_names, grades

    def choose_asset_source(self):
        initial = str(self.asset_window.source.parent if self.asset_window.source else self.client_data or "")
        path, _ = QFileDialog.getOpenFileName(self, "Open Client Asset" if self.legacy else "Open GX Asset", initial, ASSET_FILTER)
        if path:
            self.open_asset(Path(path))

    def choose_server_map(self):
        if self.task is not None or self.legacy:
            return
        initial = find_child(self.server_data, "maps") if self.server_data else Path.home()
        path, _ = QFileDialog.getOpenFileName(self, "Open Server Map", str(initial), "Server Map (*.svmap *.SVMAP)")
        if path:
            self.open_asset(Path(path))

    def open_asset(self, source: Path):
        if self.task is not None:
            return
        source = source.resolve()
        viewer = self.asset_window
        if viewer.source == source and viewer.mode == "server_map":
            self.show_editor(viewer)
            return
        if not viewer.confirm_discard():
            return
        workspace = self.client_data

        def loaded(document):
            viewer.load(source, document)
            self.show_editor(viewer)

        server_data = self.server_data
        self.run_task("Opening asset…", lambda: read_document(source, workspace, server_data), loaded)

    def open_model_table(self, source: Path, *, reload=False, kind=None):
        if self.task is not None:
            return
        kind = kind or MODEL_EDITORS.get(source.suffix.casefold())
        source = source.resolve()
        if kind not in self.model_tables or (kind == "mlt" and source.suffix.casefold() != ".mlt"):
            QMessageBox.warning(self, "Open Model Table", "Select a MON, ITM, IT2 or MLT file.")
            return
        editor, catalog_type = self.model_tables[kind]
        filename = kind.upper()
        if source == editor.source and not reload:
            self.show_editor(editor)
            return
        if editor.changed() and QMessageBox.question(
            editor, "Unsaved Changes", f"Discard the current {filename} edits?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Discard:
            return

        def loaded(catalog):
            editor.load(source, catalog)
            self.update_catalog_nodes()
            self.show_editor(editor)

        self.run_task(f"Loading {filename}…", lambda: catalog_type.open(source), loaded)

    def open_npcquest(self, source: Path, *, reload=False):
        if self.task is not None:
            return
        editor = self.quest_window
        source = source.resolve()
        if editor.source == source and not reload:
            self.show_editor(editor)
            return
        if editor.changed() and QMessageBox.question(
            editor, "Unsaved Changes", "Discard changes to NpcQuest.SData?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Discard:
            return
        target = self.quest_server_target(source)

        def read():
            client = self.read_client_catalog(source, NPCQuestCatalog)
            return client, self.read_quest_server(source, target)

        def loaded(result):
            client, (server_path, server, failure) = result
            self.quest_link_path = target
            editor.load(source, client, server_path, server, failure)
            self.remember_legacy_source(source.resolve())
            self.update_catalog_nodes()
            self.show_editor(editor)

        self.run_task("Loading NpcQuest.SData…", read, loaded)

    @staticmethod
    def quest_server_key(source: Path) -> str:
        root = source.parent.parent if source.parent.name.casefold() == "npc" else source.parent
        return f"{QUEST_SERVER_KEY}/{root.as_posix()}"

    def quest_server_target(self, source: Path) -> Path | None:
        if self.legacy:
            return None
        if source == self.client_quests and (self.server_folder or self.server_data):
            return self.workspace_server_quest()
        return workspace_path(self.settings, self.quest_server_key(source), self.studio_dir)

    def workspace_server_quest(self) -> Path | None:
        return self.server_data / "game" / NPCQuestCatalog.filename if self.server_data else None

    @staticmethod
    def read_quest_server(source: Path, target: Path | None):
        if target is None:
            return None, None, ""
        try:
            validate_targets([source, target])
            return target.resolve(), NPCQuestCatalog.open(target), ""
        except (OSError, ValueError) as error:
            return None, None, f"{target}\n{error}"

    def refresh_quest_link(self):
        editor = self.quest_window
        if self.task is not None or editor.source is None:
            return
        source = editor.source
        target = self.quest_server_target(source)
        if target == self.quest_link_path:
            return
        if target is None:
            self.quest_link_path = None
            editor.link_server(None, None)
            return

        def linked(result):
            self.quest_link_path = target
            editor.link_server(*result)

        self.run_task("Reading server NpcQuest.SData…", lambda: self.read_quest_server(source, target), linked)

    def choose_npcquest_server(self):
        editor = self.quest_window
        if self.task is not None or editor.source is None:
            return
        if editor.source == self.client_quests:
            self.configure_workspace("server")
            return
        candidate = editor.server_path or self.quest_server_target(editor.source)
        path, _ = QFileDialog.getOpenFileName(
            editor, "Select GX World NpcQuest.SData",
            str(candidate.parent if candidate else editor.source.parent), "SData (*.sdata *.SData)",
        )
        if not path:
            return
        source, server_path = editor.source, Path(path).resolve()

        def read():
            validate_targets([source, server_path])
            return NPCQuestCatalog.open(server_path)

        def linked(catalog):
            remember_path(self.settings, self.quest_server_key(source), server_path, self.studio_dir)
            self.save_workspace_settings()
            self.quest_link_path = server_path
            editor.link_server(server_path, catalog)

        self.run_task("Reading server NpcQuest.SData…", read, linked)

    def reload_npcquest(self):
        if self.quest_window.source is not None:
            self.open_npcquest(self.quest_window.source, reload=True)

    def save_npcquest(self):
        self.write_npcquest(full=False)

    def install_npcquest(self):
        self.write_npcquest(full=True)

    def write_npcquest(self, *, full):
        editor = self.quest_window
        if (self.task is not None or editor.source is None or not (editor.install if full else editor.save).isEnabled()):
            return
        source, catalog, draft = editor.source, editor.catalog, editor.draft.draft()
        if self.quest_server_target(source) != self.quest_link_path:
            self.refresh_quest_link()
            return
        server_path, server = editor.server_path, editor.server
        title = "Install NpcQuest Rules" if full else "Save NpcQuest"
        text = "Save the pending client edits and install every NPC, quest rule and item link on the server?" if full else (
            "Save the client edits and apply the changed gameplay rules to the linked server file?" if server else
            "Save changes to the client file?" if self.legacy else
            "Save changes to the client file only? Select a server file to synchronize gameplay changes."
        )
        confirmation = QMessageBox(QMessageBox.Icon.Question, title, "", parent=editor)
        confirmation.setTextFormat(Qt.TextFormat.PlainText)
        confirmation.setText(text)
        details = f"Client · {file_envelope(source)}\n{source}"
        if server_path:
            details += (f"\n\nServer · {file_envelope(server_path)}\n{server_path}\n\n"
                        "Each file keeps its encryption format. The server keeps its existing dialogue.")
        confirmation.setInformativeText(f"{details}\n\nDated backups\n{self.backup_folder()}")
        confirmation.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
        confirmation.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if full:
            confirmation.button(QMessageBox.StandardButton.Save).setText("Install")
        if confirmation.exec() != QMessageBox.StandardButton.Save:
            return
        backups = self.backup_folder()

        def write():
            return save_quest_catalog(source, catalog, draft, backups, server_path, server, full=full)

        def saved(result):
            backup, client, server_result = result
            editor.load(source, client, server_path, server_result)
            self.update_server_node()
            self.update_catalog_nodes()
            restart = "\n\nRestart GX World to load the updated server rules." if server_result and server_result.raw != server.raw else ""
            QMessageBox.information(editor, title, f"Saved.\n\nBackups\n{backup}{restart}")

        self.run_task("Writing NpcQuest files…", write, saved)

    def export_npcquest(self):
        editor = self.quest_window
        if self.task is not None or editor.source is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            editor, "Export Client NpcQuest Copy", str(editor.source.with_name("NpcQuest-copy.SData")), "SData (*.sdata *.SData)",
        )
        if not filename:
            return
        destination, catalog, draft = Path(filename).resolve(), editor.catalog, editor.draft.draft()
        if any(destination == path or (destination.exists() and destination.samefile(path))
               for path in (editor.source, editor.server_path) if path is not None):
            QMessageBox.warning(editor, "Export NpcQuest", "Choose a new path for the export. Use Save to update the linked files.")
            return
        self.run_task("Exporting NpcQuest.SData…", lambda: replace_file(destination, catalog.encode(draft), allow_create=True),
                      lambda _: QMessageBox.information(editor, "Export NpcQuest", f"Exported to\n{destination}"))

    def open_cash(self, source: Path, database: Path | None = None, *, reload: bool = False):
        if self.task is not None:
            return
        source = source.resolve()
        if self.cash_window.source == source and not reload:
            self.show_cash()
            return
        if self.cash_window.changed() and QMessageBox.question(
            self.cash_window, "Unsaved Changes", "Discard changes to Cash.SData?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Discard:
            return

        def loaded(catalog):
            data = source.parent.parent if source.parent.name.casefold() == "item" else source.parent
            if not find_child(data, "interface").is_dir() and self.client_data:
                data = self.client_data
            self.cash_window.load(source, catalog, data, database)
            self.remember_legacy_source(source.resolve())
            self.update_catalog_nodes()
            self.show_cash()

        self.run_task("Loading Cash.SData…", lambda: self.read_client_catalog(source, CashCatalog), loaded)

    def show_cash(self):
        self.show_editor(self.cash_window)

    def show_editor(self, editor):
        has_geometry = isinstance(editor, (SkillWindow, NPCQuestWindow, MonWindow, ItmWindow, MltWindow,
                                           ClientModelWindow, MonsterWindow))
        if not editor.isVisible():
            if not has_geometry or not editor.restore_window():
                editor.move(self.frameGeometry().topLeft() + QPoint(60, 50))
        if has_geometry and not editor.isMinimized():
            editor.show()
        else:
            editor.showNormal()
        editor.raise_()
        editor.activateWindow()

    def open_skills(self, source: Path, database: Path | None = None, *, reload: bool = False, npc: bool = False):
        if self.task is not None:
            return
        editor = self.npc_skill_window if npc else self.skill_window
        other = SkillCatalog.filename if npc else NPCSkillCatalog.filename
        if source.name.casefold() == other.casefold():
            QMessageBox.warning(self, "Open Skill Catalog", f"Use Open {other} for this catalog.")
            return
        source = source.resolve()
        if editor.source == source and not reload:
            self.show_editor(editor)
            return
        if editor.changed() and QMessageBox.question(
            editor, "Unsaved Changes", f"Discard changes to {editor.filename}?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Discard:
            return

        def loaded(catalog):
            data = source.parent.parent if source.parent.name.casefold() == editor.folder.casefold() else source.parent
            if not find_child(data, "interface").is_dir() and self.client_data:
                data = self.client_data
            editor.load(source, catalog, data, database)
            self.remember_legacy_source(source.resolve())
            self.update_catalog_nodes()
            self.show_editor(editor)

        self.run_task(f"Loading {editor.filename}…", lambda: self.read_client_catalog(source, editor.catalog_type), loaded)

    def reload_skills(self, editor: SkillWindow | None = None):
        editor = editor or self.skill_window
        if editor.source is not None:
            self.open_skills(editor.source, editor.database_path, reload=True, npc=editor.npc)

    def reload_cash(self):
        if self.cash_window.source is not None:
            self.open_cash(self.cash_window.source, self.cash_window.database_path, reload=True)

    def reload_items(self):
        if self.workspace is not None and self.task is None and (
            not self.item_window.edits or self.confirm_discard(items_only=True)
        ):
            self.load(self.workspace.source, self.workspace.database_path, keep_data=True)

    def choose_database(self):
        if self.task is not None or self.connection_task is not None:
            return
        directory = str(self.connection.path.parent) if self.connection.path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select GX SQLite", directory, "SQLite (*.sqlite *.sqlite3 *.db);;All Files (*)",
        )
        if path:
            self.apply_workspace(self.client_data, self.server_folder, Path(path).resolve())

    def detect_automatically(self):
        if self.manual_database is not None:
            self.apply_workspace(self.client_data, self.server_folder, None)
        else:
            self.refresh_connection()

    def refresh_connection(self):
        if self.connection_task is not None:
            return
        manual, server = self.manual_database, self.server_folder
        self.connection_request = manual, server

        def detect():
            status = connect_gx(manual, server)
            folder = server_data_folder(server) if server else detect_server_data(status.path if status.connected else None)
            return status, folder

        self.connection_task = Task(detect)
        self.connection_task.signals.done.connect(self.connection_finished)
        self.connection_task.signals.failed.connect(self.connection_failed)
        self.update_actions()
        QThreadPool.globalInstance().start(self.connection_task)

    @Slot(object)
    def connection_finished(self, payload):
        request = self.connection_request
        self.connection_task = None
        self.connection_request = None
        if request != (self.manual_database, self.server_folder):
            self.refresh_connection()
            return
        result, folder = payload
        if folder != self.server_data:
            self.server_data = folder
            self.update_server_node()
        changed = self.connection != result
        if changed:
            previous_path = self.connection.path
            self.connection = result
            self.update_database_node()
            if self.sqlite_window.isVisible():
                if previous_path != result.path or not result.connected:
                    self.sqlite_window.hide()
                else:
                    self.sqlite_window.connection = result
                    self.sqlite_window.path.setText(f"{result.status} · {result.path}")
        self.update_actions()
        editor = self.monster_window
        if changed and editor.source is not None:
            editor.refresh(editor.selected())
            if (editor.isVisible() and result.connected and not editor.sqlite_loaded()
                    and not editor.sql_edits and not editor.drop_edits):
                editor.reload_sqlite()
        self.refresh_quest_link()

    @Slot(str)
    def connection_failed(self, message: str):
        self.connection_finished((DatabaseStatus(None, "Unavailable", message), self.server_data))

    def client_descriptions(self) -> dict[int, str]:
        model = self.item_window.model
        if model is not None:
            return {item.item_id: str(model.value(item, "Description")) for item in model.items}
        if self.client_catalog is None:
            return {}
        try:
            catalog = Catalog.open(self.client_catalog)
        except (OSError, ValueError):
            return {}
        field = FIELDS_BY_NAME["Description"]
        return {item.item_id: str(catalog.value(item, field)) for item in catalog.items.values()}

    def cash_item_entries(self) -> dict[int, ItemEntry]:
        model = self.item_window.model
        cash_assets = self.cash_window.icons
        item_source = self.find_client_catalog(cash_assets.data) if cash_assets is not None else self.client_catalog
        if model is not None and model.workspace.source == item_source:
            items, value = model.items, model.value
        else:
            catalog = None
            if item_source is not None:
                try:
                    catalog = Catalog.open(item_source)
                except (OSError, ValueError):
                    pass
            if catalog is None:
                return self.cash_database_item_entries()
            items = catalog.items.values()

            def value(item, name):
                return catalog.value(item, FIELDS_BY_NAME[name])

        return {
            item.item_id: ItemEntry(item.item_id, str(value(item, "Name")).strip() or "Unnamed item",
                                    str(value(item, "Description")), int(value(item, "Icon")))
            for item in items
        }

    def cash_database_item_entries(self) -> dict[int, ItemEntry]:
        path = self.cash_window.database_path or (self.connection.path if self.connection.connected else None)
        if path is None:
            return {}
        try:
            with closing(read_database(path)) as database:
                rows = database.execute(
                    f'SELECT "ItemID", "ItemName" FROM "{TABLE}" '
                    'WHERE "Type" BETWEEN 1 AND 255 AND "TypeID" BETWEEN 1 AND 255 '
                    'AND "ItemID" = "Type" * 1000 + "TypeID"',
                )
                return {item_id: ItemEntry(item_id, str(name or "").strip() or "Unnamed item") for item_id, name in rows}
        except (OSError, SQLiteError):
            return {}

    def client_assets(self) -> ModelAssets | None:
        model = self.item_window.model
        if model is not None:
            return model.assets
        return ModelAssets(self.client_data) if self.client_data is not None else None

    def client_debuff_assets(self) -> DebuffAssets | None:
        model = self.item_window.model
        if model is not None:
            return model.npc_skills, model.debuff_item_icon
        if self.client_data is None:
            return None
        skills = NPCSkillAssets(ModelAssets(self.client_data))
        if self.client_catalog is None:
            return skills, None
        try:
            catalog = Catalog.open(self.client_catalog)
        except (OSError, ValueError):
            return skills, None
        lapis = [item for item in catalog.items.values() if item.family in LAPIS_DEBUFF_FAMILIES]
        icons = ItemIcons(self.client_data)

        def debuff_icon(code: int) -> QPixmap:
            return icons.debuff_icon(lapis, lambda item, field: catalog.value(item, FIELDS_BY_NAME[field]), code)

        return skills, debuff_icon

    def show_sqlite(self, *, activate: bool = True):
        connection = self.connection
        if not connection.connected or connection.path is None or self.task is not None:
            return
        path = connection.path

        def loaded(rows):
            if self.connection.connected and self.connection.path == path:
                self.sqlite_window.load(self.connection, rows, activate=activate)

        self.run_task("Reading SQLite…", lambda: read_catalog(path), loaded)

    def save_sqlite(self, path, item_id, before, changes):
        if self.task is not None or not self.connection.editable or self.connection.path != path:
            QMessageBox.warning(self.sqlite_window, "SQLite", "The database connection changed.")
            return

        def saved(backup):
            self.sqlite_window.apply_changes(item_id, changes)
            self.sqlite_window.statusBar().showMessage(f"SQLite saved · Backup {backup}")
            if self.workspace and self.workspace.database_path == path and not self.item_window.edits:
                row = self.workspace.sql_rows.get(item_id)
                if row is not None:
                    row.update(changes)

        backups = self.backup_folder()
        self.run_task("Saving to SQLite…", lambda: update_item(path, item_id, before, changes, backups=backups), saved)

    def choose_export_database(self):
        if self.workspace is None or self.task is not None:
            return
        directory = str(self.connection.path) if self.connection.connected else str(self.workspace.source.parent)
        path, _ = QFileDialog.getOpenFileName(
            self.item_window, "Link SQLite for Export", directory,
            "SQLite (*.sqlite *.sqlite3 *.db);;All Files (*)",
        )
        if path and self.confirm_discard(items_only=True):
            self.load(self.workspace.source, Path(path), show_items=self.item_window.isVisible())

    def load(self, source: Path, database: Path | None = None, *, show_items: bool = True, keep_data: bool = False,
             on_loaded: Callable[[Workspace], None] | None = None):
        def loaded(workspace):
            self.workspace = workspace
            if not keep_data:
                source_root = workspace.source.parent.parent
                has_models = find_child(source_root, "Character").is_dir() or find_child(
                    find_child(source_root, "Item"), "3DO"
                ).is_dir()
                self.data_root = source_root if has_models or self.client_data is None else self.client_data
            self.item_window.load_workspace(workspace)
            self.remember_legacy_source(source.resolve())
            self.update_catalog_nodes()
            if show_items:
                self.show_catalog()
            if on_loaded is not None:
                on_loaded(workspace)

        self.run_task("Loading Item.SData…", lambda: Workspace(source, database, read_catalog=lambda path: self.read_client_catalog(path, Catalog)), loaded)

    def run_task(self, message, operation, on_success):
        self.task = Task(operation)
        self.on_success = on_success
        self.task.signals.done.connect(self.task_done)
        self.task.signals.failed.connect(self.task_failed)
        self.update_actions()
        self.statusBar().showMessage(message)
        self.item_window.statusBar().showMessage(message)
        self.cash_window.statusBar().showMessage(message)
        self.skill_window.statusBar().showMessage(message)
        self.npc_skill_window.statusBar().showMessage(message)
        self.quest_window.statusBar().showMessage(message)
        self.mon_window.statusBar().showMessage(message)
        self.itm_window.statusBar().showMessage(message)
        self.mlt_window.statusBar().showMessage(message)
        self.client_model_window.statusBar().showMessage(message)
        self.monster_window.statusBar().showMessage(message)
        QThreadPool.globalInstance().start(self.task)

    @Slot(object)
    def task_done(self, value):
        action = self.on_success
        self.task = None
        self.on_success = None
        self.update_actions()
        if action:
            action(value)

    @Slot(str)
    def task_failed(self, message):
        self.task = None
        self.on_success = None
        self.update_actions()
        parent = next((window for _, window in self.sized_windows if window.isActiveWindow()), self)
        QMessageBox.warning(parent, "GX Studio", message)

    def install_database(self) -> Path | None:
        return self.connection.path if self.connection.editable else None

    def backup_folder(self) -> Path:
        return self.studio_dir / "backups"

    def confirm_install(self, parent, source, database, table, count, pending_edits):
        box = QMessageBox(QMessageBox.Icon.Warning, "Install complete catalog", "", parent=parent)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f"Install all {count:,} records from {source.name} into {table.name}?")
        edits = " Pending edits will also be saved to the client file." if pending_edits else ""
        box.setInformativeText(
            "Rows missing from this catalog will be removed from the table." + edits +
            f"\n\nClient file\n{source}\n\nSQLite database\n{database}\n\nDated backups\n{self.backup_folder()}"
        )
        install = box.addButton("Install", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is install

    def install_items(self):
        workspace = self.workspace
        if workspace is None or self.task is not None:
            return
        database = self.install_database()
        if database is None:
            return
        edits = {item_id: patch.copy() for item_id, patch in self.item_window.edits.items()}
        if self.confirm_install(self.item_window, workspace.source, database, ITEMS, len(workspace.catalog.items), bool(edits)):
            self.write_items(workspace, database, edits, full=True)

    def write_items(self, workspace: Workspace, database: Path, edits: Edits, *, full: bool):
        backups = self.backup_folder()

        def write():
            before = workspace.catalog
            after = Catalog(before.encode(edits)) if edits else before
            backup = install_catalog(workspace.source, before.raw, after.raw, database, backups, ITEMS,
                                     item_rows(after), None if full else item_rows(before))
            return backup, Workspace(workspace.source, workspace.database_path)

        def done(result):
            backup, updated = result
            self.workspace = updated
            if edits:
                self.item_window.load_workspace(updated)
            else:
                self.item_window.workspace = updated
                self.item_window.model.workspace = updated
            self.write_complete(self.item_window, database, ITEMS, backup, full)

        self.run_task("Installing Item.SData…" if full else "Saving Item.SData and SQLite…", write, done)

    def install_cash(self):
        cash = self.cash_window
        if cash.catalog is None or self.task is not None:
            return
        database = self.install_database()
        if database is None:
            return
        if self.confirm_install(cash, cash.source, database, PRODUCTS, len(cash.rows), cash.changed()):
            self.write_cash(database, full=True)

    def write_cash(self, database: Path, *, full: bool):
        cash = self.cash_window
        source, before, rows, data, linked = cash.source, cash.catalog, cash.rows.copy(), cash.icons.data, cash.database_path
        backups = self.backup_folder()

        def write():
            after = CashCatalog(before.encode(rows))
            if full:
                validate_products(after.products, for_database=True)
            backup = install_catalog(source, before.raw, after.raw, database, backups, PRODUCTS, product_rows(after.products),
                                     None if full else product_rows(before.products))
            return backup, after

        def done(result):
            backup, catalog = result
            cash.load(source, catalog, data, linked)
            self.write_complete(cash, database, PRODUCTS, backup, full)

        self.run_task("Installing Cash.SData…" if full else "Saving Cash.SData and SQLite…", write, done)

    def install_skills(self, editor: SkillWindow | None = None):
        editor = editor or self.skill_window
        if editor.catalog is None or self.task is not None:
            return
        database = self.install_database()
        table = NPC_SKILLS if editor.npc else SKILLS
        if database is not None and self.confirm_install(
            editor, editor.source, database, table, len(editor.catalog.records), editor.changed(),
        ):
            self.write_skills(database, full=True, editor=editor)

    def write_skills(self, database: Path, *, full: bool, editor: SkillWindow | None = None):
        editor = editor or self.skill_window
        table = NPC_SKILLS if editor.npc else SKILLS
        source, before, linked = editor.source, editor.catalog, editor.database_path
        edits = {key: patch.copy() for key, patch in editor.edits.items()}
        data = editor.icons.assets.root
        backups = self.backup_folder()

        def write():
            after = editor.catalog_type(before.encode(edits)) if edits else before
            backup = install_catalog(source, before.raw, after.raw, database, backups, table, after.sql_rows(),
                                     None if full else before.sql_rows())
            return backup, after

        def done(result):
            backup, after = result
            editor.load(source, after, data, linked)
            model = self.item_window.model
            if editor.npc and model is not None and self.find_client_skills(model.assets.root, npc=True) == source:
                model.npc_skills = NPCSkillAssets(model.assets)
            self.write_complete(editor, database, table, backup, full)

        filename = editor.filename
        self.run_task(f"Installing {filename}…" if full else f"Saving {filename} and SQLite…", write, done)

    def write_complete(self, parent, database, table, backup, full):
        self.update_catalog_nodes()
        title = "Install" if full else "Save"
        QMessageBox.information(parent, title, f"Verified {table.name} in:\n{database}\n\n"
                                f"Verified backups:\n{backup}\n\nRestart the GX client and GX World to load the changes.")
        if table == ITEMS and self.sqlite_window.isVisible() and self.connection.path == database:
            self.show_sqlite(activate=False)

    def review(self):
        workspace = self.workspace
        if workspace is None or self.task is not None:
            return
        edits = {item_id: patch.copy() for item_id, patch in self.item_window.edits.items()}
        database = self.install_database()
        try:
            changes = workspace.changes(edits, include_sql=database == workspace.database_path)
        except ValueError as error:
            QMessageBox.warning(self.item_window, "Save", str(error))
            return
        if not changes:
            return
        dialog = QDialog(self.item_window)
        dialog.setWindowTitle("Save Item.SData")
        dialog.resize(700, 415)
        layout = QVBoxLayout(dialog)
        label = QLabel(f"Save the listed changes to Item.SData and {database}." if database else
                       "Export a new copy of Item.SData with the listed changes.")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        lines = []
        for change in changes:
            lines.append(f"{change.item.item_id}  {change.field}: {change.before!r} → {change.after!r}")
            if change.column:
                lines.append(f"    SQLite  {change.column}: {change.sql_before!r} → {change.sql_after!r}")
        preview = QPlainTextEdit("\n".join(lines))
        preview.setReadOnly(True)
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        if database is not None:
            buttons.addButton("Save", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(lambda: dialog.done(2))
        buttons.addButton("Export Copy…", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        result = dialog.exec()
        if result == 2:
            self.write_items(workspace, database, edits, full=False)
            return
        if result != QDialog.DialogCode.Accepted:
            return
        folder = QFileDialog.getExistingDirectory(self.item_window, "Choose Export Folder")
        if folder:
            self.run_task("Saving…", lambda: workspace.export(Path(folder), edits), self.export_done)

    def review_cash(self):
        cash = self.cash_window
        if cash.catalog is None or self.task is not None or not cash.changed():
            return
        database = self.install_database()
        box = QMessageBox(QMessageBox.Icon.Question, "Save Cash.SData", "Save the edited cash products?", parent=cash)
        box.setTextFormat(Qt.TextFormat.PlainText)
        save = None
        if database:
            box.setInformativeText(f"Save changed fields to:\n{cash.source}\n\n{database}\n\nBackups: {self.backup_folder()}")
            save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        export = box.addButton("Export Copy…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if save is not None and box.clickedButton() is save:
            self.write_cash(database, full=False)
        elif box.clickedButton() is export:
            folder = QFileDialog.getExistingDirectory(cash, "Choose Export Folder")
            if folder:
                catalog, rows, data = cash.catalog, cash.rows.copy(), cash.icons.data

                def write():
                    encoded = catalog.encode(rows)
                    directory = Path(mkdtemp(prefix="GX-Cash-", dir=folder))
                    path = directory / "Cash.SData"
                    path.write_bytes(encoded)
                    return path, CashCatalog(encoded)

                def done(result):
                    path, exported = result
                    cash.load(path, exported, data)
                    self.update_catalog_nodes()
                    QMessageBox.information(cash, "Save", f"Verified export:\n{path}")

                self.run_task("Exporting Cash.SData…", write, done)

    def review_skills(self, editor: SkillWindow | None = None):
        editor = editor or self.skill_window
        if not editor.changed() or self.task is not None:
            return
        database = self.install_database()
        filename = editor.filename
        dialog = QDialog(editor)
        dialog.setWindowTitle(f"Save {filename}")
        dialog.resize(650, 390)
        layout = QVBoxLayout(dialog)
        label = QLabel(f"Save the listed changes to {filename} and {database}." if database else
                       f"Export a new copy of {filename} with the listed changes.")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        lines = []
        for (skill_id, rank), changes in sorted(editor.edits.items()):
            for name, value in changes.items():
                field = SKILL_FIELDS_BY_NAME[name]
                before = editor.catalog.value((skill_id, rank), field)
                suffix = f" · SQLite {field.column}" if database and field.column else ""
                lines.append(f"{skill_id}/{rank}  {field.label}: {before!r} → {value!r}{suffix}")
        preview = QPlainTextEdit("\n".join(lines))
        preview.setReadOnly(True)
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        if database is not None:
            buttons.addButton("Save", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(lambda: dialog.done(2))
        buttons.addButton("Export Copy…", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        result = dialog.exec()
        if result == 2:
            self.write_skills(database, full=False, editor=editor)
        elif result == QDialog.DialogCode.Accepted:
            folder = QFileDialog.getExistingDirectory(editor, "Choose Export Folder")
            if not folder:
                return
            catalog, data = editor.catalog, editor.icons.assets.root
            edits = {key: patch.copy() for key, patch in editor.edits.items()}

            def write():
                after = editor.catalog_type(catalog.encode(edits))
                prefix = "GX-NpcSkills-" if editor.npc else "GX-Skills-"
                directory = Path(mkdtemp(prefix=prefix, dir=folder)) / "data" / editor.folder
                directory.mkdir(parents=True)
                path = directory / filename
                path.write_bytes(after.raw)
                return path, after

            def done(result):
                path, after = result
                editor.load(path, after, data, None)
                self.update_catalog_nodes()
                QMessageBox.information(editor, "Save", f"Verified export:\n{path}")

            self.run_task(f"Exporting {filename}…", write, done)

    def export_done(self, destination: Path):
        QMessageBox.information(self.item_window, "Save", f"Verified export:\n{destination}")
        database = destination / "rapture.sqlite"
        self.load(destination / "data" / "Item" / "Item.SData",
                  database if database.is_file() else None, keep_data=True)

    def close_current_workspace(self):
        if self.task is not None or not self.confirm_discard():
            return
        self.legacy_source = self.legacy_episode = None
        self.legacy_layouts = {}
        self.set_client_data(None)
        if self.legacy_temp is not None:
            self.legacy_temp.cleanup()
            self.legacy_temp = None

    def closeEvent(self, event: QCloseEvent):
        if self.task is not None or not self.confirm_discard():
            event.ignore()
        else:
            for key, window in self.sized_windows:
                size = window.normalGeometry().size() if window.isMaximized() or window.isFullScreen() else window.size()
                if not size.isEmpty():
                    self.settings.setValue(key, size)
            self.settings.sync()
            self.connection_timer.stop()
            self.item_window.close()
            self.cash_window.close()
            self.skill_window.close()
            self.npc_skill_window.close()
            self.quest_window.close()
            self.mon_window.close()
            self.itm_window.close()
            self.mlt_window.close()
            self.client_model_window.close()
            self.monster_window.close()
            self.asset_window.clear()
            self.asset_window.close()
            self.sqlite_window.close()
            if self.legacy_temp is not None:
                self.legacy_temp.cleanup()
                self.legacy_temp = None
            event.accept()


def choose_work_mode():
    dialog = QMessageBox()
    dialog.setWindowTitle("GX Studio")
    dialog.setText("Which data do you want to work with?")
    gx = dialog.addButton("GX", QMessageBox.ButtonRole.AcceptRole)
    legacy = dialog.addButton("Legacy Data", QMessageBox.ButtonRole.ActionRole)
    dialog.addButton(QMessageBox.StandardButton.Cancel)
    dialog.setDefaultButton(gx)
    dialog.exec()
    return {gx: "gx", legacy: "legacy"}.get(dialog.clickedButton())


def main() -> int:
    parser = ArgumentParser(description="GX Studio data editor and asset viewer")
    parser.add_argument("catalog", type=Path, nargs="?")
    parser.add_argument("--sqlite", type=Path)
    parser.add_argument("--mode", choices=("gx", "legacy"))
    parser.add_argument("--check-startup", action="store_true", help="Open the workspace briefly to verify a build.")
    arguments = parser.parse_args()
    app = QApplication([])
    app.setApplicationName("gx-data-editor")
    app.setApplicationDisplayName("GX Studio")
    app.setDesktopFileName("gx-data-editor")
    app.setWindowIcon(QIcon(str(APP_ICON)))
    set_light_theme(app)
    mode = arguments.mode or ("gx" if arguments.check_startup else choose_work_mode())
    if mode is None:
        return 0
    window = Window(arguments.sqlite, legacy=mode == "legacy")
    window.show()
    if arguments.check_startup:
        QTimer.singleShot(300, app.exit)
    if arguments.catalog:
        window.open_data_path(arguments.catalog, arguments.sqlite)
    result = app.exec()
    QThreadPool.globalInstance().waitForDone()
    return result
