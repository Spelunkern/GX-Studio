from dataclasses import replace
from math import isfinite
from pathlib import Path
import re
from struct import error as StructError

from PySide6.QtCore import QAbstractTableModel, QByteArray, QMimeData, QModelIndex, QSize, Qt, QSortFilterProxyModel
from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtGui import QAction, QDrag, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit
from PySide6.QtWidgets import QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTabBar, QTableView, QTreeWidget, QTreeWidgetItem
from PySide6.QtWidgets import QToolButton, QVBoxLayout, QWidget

from .catalog import Catalog
from .equipment_models import ModelAssets
from .equipment_preview import PreviewPane, RenderJob
from .fields import FIELDS_BY_NAME
from .install import replace_file
from .item_view import ItemIcons, find_child
from .merchant_stock import CLASSES as MERCHANT_CLASSES
from .merchant_stock import EMPTY_PRODUCT, PROFESSIONS, MerchantItem, item_matches, item_types
from .model_render import PreviewRenderer
from .npcquest_data import EMPTY_LINKS, FLOAT, GATE_FIELDS, JOBS, NPC, NPC_FIELDS, QUEST_FIELDS, U8, Gate, Quest
from .npcquest_data import empty_fields, text_bytes
from .pickers import IconPicker
from .seed import envelope_label, episode_label
from .skill_sheet import SkillSizedDialog

MERCHANTS, GATES, NPCS, QUESTS = range(4)
PAGES = ("Merchants", "Gate-Keepers", "Standard-NPCs", "Quests")
DISPLAY = Qt.ItemDataRole.DisplayRole
NPC_TYPES = ("Unknown", "Merchant", "Gate-Keeper", "Blacksmith", "PvP-Manager", "Gambling House", "Warehouse",
             "Normal NPC", "Guard", "Animal", "Apprentice", "Guild-Master", "Dead NPC", "Combat Commander")
STANDARD_FAMILIES = {
    9: "Animal", 10: "Apprentice", 3: "Blacksmith", 13: "Combat Commander", 12: "Dead NPC",
    5: "Gambling House", 8: "Guard", 11: "Guild Master", 7: "Normal NPC", 4: "PvP Manager", 6: "Warehouse",
}
NPC_FACTIONS = ("Both", "Light", "Dark")
QUEST_FACTIONS = ("Human", "Elf", "Light", "Deatheater", "Vail", "Fury", "All factions")
LABELS = {
    "NPCType": "Service type", "NPCTypeID": "Service ID", "MoveDistance": "Patrol distance", "MoveSpeed": "Patrol interval",
    "EmbeddedID": "Embedded ID", "PrevQuest": "Previous quest ID", "StartType": "Obtained from",
    "EndType": "Objective type", "ResultType": "Reward mode", "StartNPCType": "Start NPC group",
    "EndNPCType": "End NPC group", "StartNPCID": "Start NPC row", "EndNPCID": "End NPC row",
    "MinMinutes": "Minimum minutes", "MaxMinutes": "Maximum minutes", "TickStart": "Start interval (s)",
    "TickKeep": "Time limit (s)", "TickReceive": "Repeatable", "UserSelect": "Reward choices",
    "NeedItemID": "Required item slot", "NeedMobID": "Required mob slot", "NeedTime": "Required time (s)",
    "AttackFighter": "Fighter", "DefenseFighter": "Defender", "PatrolRogue": "Ranger", "ShooterRogue": "Archer",
    "AttackMage": "Mage", "DefenseMage": "Priest",
}
START_TYPES = ("Conditional quest in a chain", "Standard quest given by a NPC", "Quest given by object",
               "Hidden quest activated by a drop", "Letter-quest activated by click")
END_TYPES = ("Final", "Farm", "PvE", "PvP")
REWARD_CHOICES = (
    "Give the reward matching the criteria", "Give the first listed choice",
    "Give 1 reward among the 2 listed choices", "Give 1 reward among the 3 listed choices",
)
OPTIONS = {
    "StartType": START_TYPES,
    "EndType": END_TYPES,
    "ResultType": ("Conditional or player choice", "First reward"),
    "Mode": ("Easy or higher", "Normal or higher", "Difficult or higher", "Ultimate"),
}


def label(name):
    if name.startswith("Party") and name[5:] in LABELS:
        return "Party " + LABELS[name[5:]]
    return LABELS.get(name, re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name))


def option_text(options, value):
    return options[value] if 0 <= value < len(options) else str(value)


def buttons(dialog, layout):
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(dialog.editable)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)
    return box


def configure_list(table):
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setShowGrid(False)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(20)


def size_shop_columns(table):
    for column, width in enumerate((40, 40, 120, 40, 40)):
        table.setColumnWidth(column, width)


class ShopList(QTableView):
    stock_dropped = Signal()
    mime_type = "application/x-gx-editor-merchant-stock"

    def __init__(self):
        super().__init__()
        self.stock_source = None
        configure_list(self)
        self.setIconSize(QSize(16, 16))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def startDrag(self, actions):
        if self.selectionModel().hasSelection():
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(self.mime_type, b"stock")
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event):
        if self.stock_source is not None and event.source() is self.stock_source and event.mimeData().hasFormat(self.mime_type):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if self.stock_source is not None and event.source() is self.stock_source and event.mimeData().hasFormat(self.mime_type):
            event.acceptProposedAction()
            self.stock_dropped.emit()


class ShopStockFilter(QSortFilterProxyModel):
    def __init__(self, parent):
        super().__init__(parent)
        self.query = ""
        self.item_key = None

    def find_items(self, text):
        self.query = text.strip().casefold()
        family, separator, type_id = self.query.partition("/")
        self.item_key = (int(family), int(type_id)) if (
            separator and family.isascii() and family.isdecimal() and len(family) <= 3
            and type_id.isascii() and type_id.isdecimal() and len(type_id) <= 3
        ) else None
        self.invalidate()

    def filterAcceptsRow(self, row, parent):
        if not self.query:
            return True
        key, values = self.sourceModel().rows[row]
        if self.item_key is not None:
            return key == self.item_key
        family, type_id = key
        return (self.query in values[2].casefold() or self.query in str(family * 1000 + type_id)
                or self.query in f"{family}/{type_id}")


class QuestForm(SkillSizedDialog):
    def __init__(self, editor, title, key):
        super().__init__(editor, editor.settings, key)
        self.setWindowTitle(title)
        self.editor = editor
        self.editable = editor.catalog.writable

    def values(self, schema, source, columns=2):
        grid = QGridLayout()
        fields = {}
        rows = max(1, (len(schema) + columns - 1) // columns)
        for index, (name, codec) in enumerate(schema):
            control = self.number(name, source[name], OPTIONS.get(name))
            heading = QLabel(label(name) + ":")
            heading.setToolTip(name)
            row, column = index % rows, 2 * (index // rows)
            grid.addWidget(heading, row, column)
            grid.addWidget(control, row, column + 1)
            fields[name] = (control, codec)
        return grid, fields

    def number(self, name, value, options=None):
        if options is None:
            control = self.line(str(value))
        else:
            control = QComboBox()
            for code, title in (options.items() if isinstance(options, dict) else enumerate(options)):
                control.addItem(title, code)
            if control.findData(value) < 0:
                control.addItem(f"{value}", value)
            control.setCurrentIndex(control.findData(value))
            control.setEnabled(self.editable)
        control.setAccessibleName(label(name))
        control.setToolTip(name)
        return control

    @staticmethod
    def read_values(fields, original):
        values = original.copy()
        for name, (control, codec) in fields.items():
            if isinstance(control, QComboBox):
                values[name] = control.currentData()
                continue
            if isinstance(control, QCheckBox):
                values[name] = (original[name] or 1) if control.isChecked() else 0
                continue
            text = control.text().strip()
            if text == str(original[name]):
                continue
            try:
                number = float(text) if codec is FLOAT else int(text)
                if codec is FLOAT and not isfinite(number):
                    raise ValueError
                values[name] = codec.unpack(codec.pack(number))[0]
            except (ValueError, OverflowError, StructError) as error:
                raise ValueError(f"{label(name)} has an invalid value for its native field.") from error
        return values

    def line(self, text):
        widget = QLineEdit(text)
        widget.setReadOnly(not self.editable)
        return widget

    def paragraph(self, text):
        widget = QPlainTextEdit(text)
        widget.setReadOnly(not self.editable)
        return widget, widget.toPlainText()

    @staticmethod
    def read_text(widget, original, shown=None):
        current = widget.text() if shown is None else widget.toPlainText()
        if shown is not None and current == shown:
            return original
        text_bytes(current, b"")
        return current

    def scroll(self, layout):
        panel = QWidget()
        panel.setLayout(layout)
        scroll = QScrollArea()
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        return scroll

    def link_fields(self, links):
        form = QFormLayout()
        controls = [self.line(", ".join(map(str, ids))) for ids in links]
        for title, control in zip(("Start quest IDs:", "End quest IDs:"), controls):
            control.setPlaceholderText("Quest IDs separated by commas")
            form.addRow(title, control)
        return form, controls

    def read_links(self, controls, original):
        values = []
        for control, before in zip(controls, original):
            line = control.text().strip()
            if line == ", ".join(map(str, before)):
                values.append(before)
                continue
            ids = tuple(int(token) for token in line.replace(",", " ").split())
            if any(quest_id not in self.editor.draft.quests and quest_id not in before for quest_id in ids):
                raise ValueError("Quest links must reference IDs listed in Quests.")
            values.append(ids)
        return tuple(values)


class NPCDialog(QuestForm):
    def __init__(self, editor, key, npc):
        group = key[0] if key is not None else 0
        title = ("Merchant Editor" if group == 1 else "Gate-Keeper" if group == 2 else "Standard NPC")
        size_key = {1: "npcquest-merchant", 2: "npcquest-gatekeeper"}.get(group, "npcquest-standard")
        super().__init__(editor, title, size_key)
        self.npc = npc
        self.record_key = key
        self.group = group
        self.fields = {}
        self.gates = []
        self.gate_coordinates = {}
        self.render_job = None
        self.render_generation = 0
        self.turn = 0.0
        self.assets = editor.model_assets
        try:
            self.models = self.assets.npc_choices() if self.assets else {}
        except (OSError, ValueError):
            self.models = {}
        self.renderer = PreviewRenderer(self.assets) if self.models else None
        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        layout.addLayout(body)
        profile = QVBoxLayout()
        body.addLayout(profile, 1)
        header = QHBoxLayout()
        profile.addLayout(header)
        self.portraits = self.assets.npc_portraits() if self.assets else {}
        self.avatar = QToolButton()
        self.avatar.setFixedSize(80, 62)
        self.avatar.setIconSize(QSize(64, 64))
        self.avatar.setAccessibleName("NPC portrait")
        self.avatar.setEnabled(self.editable)
        self.avatar.clicked.connect(self.choose_portrait)
        self.name = self.line(npc.name)
        factions = self.field("Faction", NPC_FACTIONS)
        model_names = {key: entry.name for key, entry in self.models.items()}
        for portrait in self.portraits:
            model_names.setdefault(portrait, str(portrait))
        model = self.field("Model", model_names or None)
        self.expand = QPushButton(">>", self)
        self.expand.setEnabled(bool(self.models))
        self.expand.clicked.connect(self.toggle_preview)
        if isinstance(model, QComboBox):
            model.setMaxVisibleItems(16)
            model.currentIndexChanged.connect(self.model_changed)
        self.welcome, self.shown = self.paragraph(npc.welcome)
        if group == 1:
            self.expand.hide()
            self.build_merchant(layout, body, profile, header, factions)
        else:
            self.build_npc_profile(layout, profile, header, factions, model)
            if group == 2:
                self.build_destinations(profile)
            profile.addStretch()
        self.preview = PreviewPane(200, {1: 450, 2: 338}.get(group, 268), self)
        self.preview.turned.connect(self.rotate_model)
        self.preview.hide()
        body.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)

        native = QAction("NPC Details…", self)
        native.triggered.connect(self.show_native_details)
        self.addAction(native)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        if group == 1 and self.models:
            self.preview_action = QAction("Show 3D Preview", self, checkable=True)
            self.preview_action.triggered.connect(self.toggle_preview)
            self.addAction(self.preview_action)
        link_layout, self.links = self.link_fields(npc.links)
        self.native_dialog = QDialog(self)
        self.native_dialog.setWindowTitle("NPC Details")
        native_layout = QVBoxLayout(self.native_dialog)
        if group == 1:
            model_form = QFormLayout()
            model_form.addRow("Model's name:", model)
            native_layout.addLayout(model_form)
        schema = tuple((name, codec) for name, codec in NPC_FIELDS if name not in self.fields)
        grid, extra = self.values(schema, npc.fields)
        self.fields.update(extra)
        native_layout.addLayout(grid)
        native_layout.addLayout(link_layout)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.native_dialog.accept)
        native_layout.addWidget(close)
        margins = layout.contentsMargins()
        self.setMinimumHeight(self.preview.height() + margins.top() + margins.bottom())
        box = buttons(self, layout if group == 1 else profile)
        ok = box.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Ok")
        ok.setDefault(True)
        default_size = {1: QSize(710, 524), 2: QSize(460, 374)}.get(group, QSize(450, 304))
        self.restore_size(default_size)
        self.model_changed()
        self.finished.connect(self.stop_render)

    def build_npc_profile(self, layout, profile, header, factions, model):
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(10)
        profile.setSpacing(3)
        identity = QGridLayout()
        identity.setVerticalSpacing(5)
        identity.setHorizontalSpacing(0)
        identity.setColumnMinimumWidth(0, 90)
        identity.setColumnMinimumWidth(1, 40)
        identity.setColumnStretch(2, 1)
        identity.setColumnMinimumWidth(3, 17)
        header.addLayout(identity)
        self.avatar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        identity.addWidget(self.avatar, 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        identity.addWidget(QLabel("Name:"), 0, 1)
        identity.addWidget(self.name, 0, 2, 1, 3)
        identity.addWidget(QLabel("Faction:"), 1, 1)
        factions.setMinimumContentsLength(5)
        if self.group == 2:
            identity.addWidget(factions, 1, 2, Qt.AlignmentFlag.AlignLeft)
        else:
            family_row = QHBoxLayout()
            family_row.addWidget(factions)
            family_row.addWidget(QLabel("Family:"))
            family = self.field("NPCType", {0: "", **STANDARD_FAMILIES} if self.group == 0 else STANDARD_FAMILIES)
            family.setFixedSize(123, 22)
            family.setEnabled(self.editable and self.group == 0)
            family_row.addWidget(family)
            family_row.addStretch()
            identity.addLayout(family_row, 1, 2, 1, 3)
        caption = QLabel("Model's name:  ")
        identity.addWidget(caption, 2, 0, 1, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if isinstance(model, QComboBox):
            model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            model.setMinimumContentsLength(16)
        identity.addWidget(model, 2, 2)
        self.expand.setFixedWidth(40)
        identity.addWidget(self.expand, 2, 4)
        for control in (self.name, factions, model, self.expand):
            control.setFixedHeight(22)
        profile.addSpacing(2)
        profile.addWidget(QLabel("Welcome Msg:"))
        self.welcome.setFixedHeight(54 if self.group == 2 else 108)
        profile.addWidget(self.welcome)

    def build_destinations(self, profile):
        profile.addSpacing(9)
        heading = QLabel("Destinations:")
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        profile.addWidget(heading)

        destinations = QGridLayout()
        destinations.setHorizontalSpacing(6)
        destinations.setVerticalSpacing(4)
        destinations.setColumnStretch(0, 1)
        for column, title in enumerate(("Name", "Map ID", "Pos. X", "Pos. Y", "Pos. Z", "Cost")):
            destinations.addWidget(QLabel(title), 0, column)
        for slot, gate in enumerate(self.npc.gates, 1):
            name = self.line(gate.label)
            name.setMinimumWidth(100)
            name.setFixedHeight(22)
            name.setAccessibleName(f"Destination {slot} name")
            destinations.addWidget(name, slot, 0)
            fields = {}
            for column, (field, codec) in enumerate(GATE_FIELDS, 1):
                value = gate.fields[field]
                control = self.number(field, value)
                control.setFixedWidth((40, 55, 55, 55, 55)[column - 1])
                control.setFixedHeight(22)
                if codec is FLOAT:
                    places = 1 if value >= 1000 else 2 if value >= 100 else 3 if value >= 1 else 4
                    display = f"{value:.{places}f}"
                    control.setText(display)
                    control.setToolTip(str(value))
                    self.gate_coordinates[control] = display
                destinations.addWidget(control, slot, column)
                fields[field] = (control, codec)
            self.gates.append((name, fields))
        profile.addLayout(destinations)

    def field(self, name, options=None):
        schema = dict((*NPC_FIELDS, ("MerchantType", U8)))
        control = self.number(name, self.npc.fields[name], options)
        self.fields[name] = (control, schema[name])
        return control

    def show_native_details(self):
        self.native_dialog.exec()

    def build_merchant(self, layout, body, profile, header, factions):
        layout.setContentsMargins(20, 18, 20, 16)
        body.setSpacing(5)
        body.setStretch(0, 33)
        profile.setSpacing(4)
        self.avatar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        identity = QGridLayout()
        identity.setHorizontalSpacing(0)
        identity.setVerticalSpacing(4)
        identity.setColumnMinimumWidth(0, 100)
        identity.setColumnMinimumWidth(1, 40)
        identity.setColumnStretch(2, 1)
        header.addLayout(identity)
        identity.addWidget(self.avatar, 0, 0, 3, 1, Qt.AlignmentFlag.AlignTop)
        profession = self.field("MerchantType", PROFESSIONS)
        factions.setFixedWidth(80)
        for row, (caption, control) in enumerate((("Name:", self.name), ("Type:", profession), ("Sell to:", factions))):
            control.setFixedHeight(22)
            identity.addWidget(QLabel(caption), row, 1)
            identity.addWidget(control, row, 2)
        profile.addWidget(QLabel("Welcome Msg:"))
        self.welcome.setFixedHeight(66)
        profile.addWidget(self.welcome)
        stock = QVBoxLayout()
        stock.setSpacing(4)
        self.build_shop(profile, stock)

        transfer = QVBoxLayout()
        transfer.addStretch(2)
        add = QPushButton("<<")
        add.setFixedSize(40, 24)
        add.setAccessibleName("Add selected items")
        add.setEnabled(self.editable)
        add.clicked.connect(self.add_product)
        transfer.addWidget(add)
        transfer.addStretch(1)
        body.addLayout(transfer)
        body.addLayout(stock, 29)
        profession.currentIndexChanged.connect(self.update_merchant_filters)
        factions.currentIndexChanged.connect(self.update_merchant_filters)
        self.update_merchant_filters()

    def build_shop(self, profile, stock):
        self.products = list(self.npc.products)
        self.items = self.editor.shop_items()
        self.shop_icons = ItemIcons(self.assets.root) if self.assets else None
        self.sold_model = QuestList(self, icon_for=self.shop_item_icon)
        self.stock_model = QuestList(self, draggable=True, icon_for=self.shop_item_icon)
        self.sold, self.stock = ShopList(), ShopList()
        self.stock_proxy = ShopStockFilter(self)
        self.stock_proxy.setSourceModel(self.stock_model)
        self.sold.setModel(self.sold_model)
        self.stock.setModel(self.stock_proxy)
        self.stock.setDragEnabled(self.editable)
        self.sold.stock_source = self.stock
        self.sold.setAcceptDrops(self.editable)
        self.sold.viewport().setAcceptDrops(self.editable)
        self.sold.stock_dropped.connect(lambda: self.add_product(unique=True))
        self.sold.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        for title, action in (("Remove", self.remove_product), ("Move Up", lambda: self.move_product(-1)),
                              ("Move Down", lambda: self.move_product(1)), ("Add by ID…", self.product_by_id)):
            entry = QAction(title, self.sold)
            entry.setEnabled(self.editable)
            entry.triggered.connect(action)
            self.sold.addAction(entry)
            if title == "Remove":
                QShortcut(QKeySequence.StandardKey.Delete, self.sold, activated=action)
        find = QAction("Find Item…", self.stock)
        find.triggered.connect(self.focus_shop_search)
        self.stock.addAction(find)
        self.stock.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)

        profile.addWidget(self.shop_heading("Sold Items:"))
        profile.addWidget(self.sold, 1)
        self.sold_description = self.shop_description()
        profile.addWidget(self.sold_description)
        stock.addWidget(self.shop_heading("Available Items"))
        self.stock_type = QComboBox()
        self.stock_class = QComboBox()
        filters = QGridLayout()
        filters.setHorizontalSpacing(4)
        filters.setVerticalSpacing(7)
        filters.setColumnMinimumWidth(0, 36)
        for row, (caption, control) in enumerate((("Type", self.stock_type), ("Class", self.stock_class))):
            control.setFixedWidth(200)
            control.setFixedHeight(23)
            filters.addWidget(QLabel(caption), row, 0)
            filters.addWidget(control, row, 1, Qt.AlignmentFlag.AlignLeft)
        filters.setColumnStretch(2, 1)
        stock.addSpacing(4)
        stock.addLayout(filters)
        stock.addSpacing(4)
        stock.addWidget(self.stock, 1)
        self.stock_search = QLineEdit()
        self.stock_search.setPlaceholderText("Find by name or item ID")
        self.stock_search.setAccessibleName("Find available items")
        self.stock_search.setClearButtonEnabled(True)
        self.stock_search.textChanged.connect(self.stock_proxy.find_items)
        stock.addWidget(self.stock_search)
        self.stock_description = self.shop_description()
        stock.addWidget(self.stock_description)
        for table in (self.sold, self.stock):
            table.selectionModel().currentChanged.connect(lambda current, _, table=table: self.describe_shop_item(table, current))
            table.doubleClicked.connect(lambda index, table=table: self.open_shop_item(table, index))
        self.stock_type.currentIndexChanged.connect(self.update_stock)
        self.stock_class.currentIndexChanged.connect(self.update_stock)
        self.update_products()

    @staticmethod
    def shop_heading(title):
        heading = QLabel(title)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        return heading

    @staticmethod
    def shop_description():
        info = QLabel()
        info.setTextFormat(Qt.TextFormat.PlainText)
        info.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        info.setWordWrap(True)
        info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        info.setFixedHeight(25)
        return info

    def focus_shop_search(self):
        self.stock_search.setFocus()
        self.stock_search.selectAll()

    def shop_item_icon(self, key):
        if self.shop_icons is None or (item := self.items.get(key)) is None:
            return None
        pixmap = self.shop_icons.pixmap(key[0], item.icon)
        return QIcon(pixmap) if not pixmap.isNull() else None

    def update_merchant_filters(self, *_):
        profession = self.fields["MerchantType"][0].currentData()
        faction = self.fields["Faction"][0].currentData()
        self.stock_type.blockSignals(True)
        self.stock_type.clear()
        for kind, title in item_types(profession, faction):
            self.stock_type.addItem(title, kind)
        self.stock_type.blockSignals(False)
        self.stock_class.blockSignals(True)
        self.stock_class.clear()
        self.stock_class.addItem("All", None)
        if profession in (1, 2):
            for slots, title in enumerate(MERCHANT_CLASSES):
                self.stock_class.addItem(title, slots)
        self.stock_class.blockSignals(False)
        self.update_stock()

    def update_stock(self, *_):
        profession = self.fields["MerchantType"][0].currentData()
        faction = self.fields["Faction"][0].currentData()
        category = self.stock_type.currentData()
        item_class = self.stock_class.currentData()
        rows = [(key, (*key, item.name, item.level or "—")) for key, item in self.items.items()
                if item_matches(profession, faction, category, item_class, key[0], item)]
        self.stock_model.reset(("Type", "ID", "Name", "Level"), rows, centered_columns=(1, 3))
        size_shop_columns(self.stock)

    def shop_item_key(self, table, index):
        if table is self.stock:
            index = self.stock_proxy.mapToSource(index)
            return self.stock_model.rows[index.row()][0] if index.isValid() else None
        return self.products[self.sold_slots[index.row()]] if index.isValid() else None

    def describe_shop_item(self, table, index):
        target = self.stock_description if table is self.stock else self.sold_description
        item = self.items.get(self.shop_item_key(table, index))
        description = item.description if item else ""
        if table is self.stock and item and item.slots:
            description += f", Slot(s): {item.slots}"
        target.setText(description)
        target.setToolTip(description)

    def open_shop_item(self, table, index):
        key = self.shop_item_key(table, index)
        if key in self.items:
            self.editor.edit_shop_item(key, self, self.refresh_shop_items)

    def refresh_shop_items(self):
        selected = self.sold.currentIndex().row()
        self.items = self.editor.shop_items()
        self.update_stock()
        self.update_products(selected if selected >= 0 else None)

    def update_products(self, selected=None):
        self.sold_slots = [slot for slot, key in enumerate(self.products) if key != EMPTY_PRODUCT]
        rows = []
        for slot in self.sold_slots:
            key = self.products[slot]
            item = self.items.get(key)
            rows.append((key, (*key, item.name, item.level or "—", item.slots or "—") if item else
                         (*key, "Item unavailable", "", "")))
        self.sold_model.reset(("Type", "ID", "Name", "Level", "Slot"), rows, centered_columns=(1, 3, 4))
        size_shop_columns(self.sold)
        if selected is not None and rows:
            self.sold.selectRow(min(selected, len(rows) - 1))

    def add_product(self, *, unique=False):
        if not self.editable:
            return
        additions = [self.shop_item_key(self.stock, index) for index in
                     sorted(self.stock.selectionModel().selectedRows(), key=QModelIndex.row)]
        if unique:
            additions = [key for key in additions if key not in self.products]
        if additions:
            self.insert_products(additions)

    def insert_products(self, additions):
        # Insert before the native trailing empty slots and keep their count.
        insert_at = self.sold_slots[-1] + 1 if self.sold_slots else 0
        self.products[insert_at:insert_at] = additions
        self.update_products(len(self.sold_slots))

    def product_by_id(self):
        value, ok = QInputDialog.getInt(self, "Add Sold Item", "Item ID", 1001, 1001, 255255)
        if not ok:
            return
        family, item = divmod(value, 1000)
        if not 1 <= item <= 255:
            QMessageBox.warning(self, self.windowTitle(), "Item TypeID must be between 1 and 255.")
            return
        self.insert_products([(family, item)])

    def remove_product(self):
        if not self.editable:
            return
        rows = sorted(index.row() for index in self.sold.selectionModel().selectedRows())
        for row in reversed(rows):
            self.products.pop(self.sold_slots[row])
        if rows:
            self.update_products(rows[0])

    def move_product(self, delta):
        row = self.sold.currentIndex().row()
        destination = row + delta
        if self.editable and row >= 0 and 0 <= destination < len(self.sold_slots):
            source_slot, dest_slot = self.sold_slots[row], self.sold_slots[destination]
            self.products[source_slot], self.products[dest_slot] = self.products[dest_slot], self.products[source_slot]
            self.update_products(destination)

    def toggle_preview(self):
        visible = self.preview.isHidden()
        size = self.size()
        self.preview.setVisible(visible)
        self.expand.setText("<<" if visible else ">>")
        if hasattr(self, "preview_action"):
            self.preview_action.setChecked(visible)
        self.layout().activate()
        margin = 205 if self.group == 1 else 210
        self.resize(size.width() + (margin if visible else -margin), size.height())
        self.render_model()

    def model_changed(self, *_):
        model = self.fields["Model"][0]
        model_id = model.currentData() if isinstance(model, QComboBox) else None
        portrait = self.portraits.get(model_id)
        self.avatar.setIcon(QIcon(str(portrait)) if portrait else QIcon())
        self.avatar.setText("▼")
        self.avatar.setEnabled(self.editable and bool(self.portraits))
        self.render_model()

    def choose_portrait(self):
        model = self.fields["Model"][0]
        icons = [(key, QPixmap(str(path))) for key, path in sorted(self.portraits.items())]
        icons = [(key, pixmap) for key, pixmap in icons if not pixmap.isNull()]
        if not icons:
            return
        picker = IconPicker(self.avatar, model.currentData(), icons, 10, title="NPC portraits", include_empty=False)
        picker.iconSelected.connect(lambda key: model.setCurrentIndex(model.findData(key)))
        picker.show()

    def rotate_model(self, delta):
        self.turn = (self.turn + delta * .65) % 360
        self.render_model()

    def stop_render(self, *_):
        if self.render_job is not None:
            self.render_job.cancelled.set()
            self.render_job = None

    def render_model(self, *_):
        self.stop_render()
        self.render_generation += 1
        pane = self.preview
        if self.renderer is None or pane.isHidden():
            return
        choice = self.models.get(self.fields["Model"][0].currentData())
        requests = [(choice, pane.dimensions.width(), pane.dimensions.height())]
        self.render_job = RenderJob(self.renderer, "npc", requests, self.turn, self.render_generation)
        self.render_job.signals.finished.connect(self.rendered)
        QThreadPool.globalInstance().start(self.render_job)

    def rendered(self, generation, results):
        if generation == self.render_generation:
            self.preview.display(results[0])

    def done(self, result):
        if not self.preview.isHidden():
            self.toggle_preview()
        super().done(result)

    def accept(self):
        try:
            values = self.read_values(self.fields, self.npc.fields)
            key = self.record_key
            if key is None:
                family = values["NPCType"]
                if family not in STANDARD_FAMILIES:
                    raise ValueError("Choose a family for the NPC.")
                row = self.editor.next_npc_row(family)
                if row > 65535:
                    raise ValueError("This NPC family has reached its row limit.")
                key = family, row
                values["NPCTypeID"] = row
            gates = []
            for gate, (name, inputs) in zip(self.npc.gates, self.gates):
                edited = {field: (control, codec) for field, (control, codec) in inputs.items()
                          if control.text().strip() != self.gate_coordinates.get(control)}
                gates.append(Gate(self.read_values(edited, gate.fields), self.read_text(name, gate.label)))
            self.result_data = NPC(
                values, self.read_text(self.name, self.npc.name),
                self.read_text(self.welcome, self.npc.welcome, self.shown),
                tuple(self.products) if hasattr(self, "products") else self.npc.products,
                tuple(gates),
                self.read_links(self.links, self.npc.links),
            )
            self.result_key = key
        except ValueError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        super().accept()


class QuestDialog(QuestForm):
    def __init__(self, editor, row, quest):
        super().__init__(editor, "Quest Details", "npcquest-quest-details")
        self.quest = quest
        self.fields = {}
        self.codecs = dict(QUEST_FIELDS)
        self.tall = self.screen().availableGeometry().height() > 900
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 18, 12)
        layout.setSpacing(7)
        content = QVBoxLayout()
        content.setContentsMargins(8, 0, 8, 0)
        content.setSpacing(3)
        content.addLayout(self.overview(row))
        content.addSpacing(7)
        self.build_dialogue(content)
        content.addSpacing(8)
        self.build_rewards(content)
        layout.addWidget(self.scroll(content), 1)

        self.additional_fields = self.build_additional_fields()
        extra = QAction("Additional Fields…", self)
        extra.triggered.connect(self.additional_fields.exec)
        self.addAction(extra)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        QShortcut(QKeySequence("Ctrl+Shift+A"), self, activated=self.additional_fields.exec)
        box = buttons(self, layout)
        ok = box.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Ok")
        ok.setDefault(True)
        self.restore_size(QSize(820, 835 if self.tall else 745))

    def overview(self, row):
        layout = QHBoxLayout()
        layout.setSpacing(20)
        left = QVBoxLayout()
        right = QVBoxLayout()
        left.setSpacing(2)
        right.setSpacing(2)
        layout.addLayout(left, 53)
        layout.addLayout(right, 47)

        self.name = self.line(self.quest.name)
        self.name.setFixedHeight(20)
        self.name.setAccessibleName("Name of Quest")
        name_label = QLabel("Name of Quest:")
        name_label.setFixedWidth(95)
        name_label.setBuddy(self.name)
        bold = name_label.font()
        bold.setBold(True)
        name_label.setFont(bold)
        left.addLayout(self.control_row(name_label, self.name))

        kind_label = QLabel("Type of quest:")
        kind_label.setFixedWidth(95)
        self.objective = self.field("EndType", END_TYPES, width=55)
        kind_label.setBuddy(self.objective)
        obtained = self.field("StartType", START_TYPES)
        obtained.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        obtained.setMinimumWidth(170)
        left.addLayout(self.control_row(kind_label, self.objective, "Obtained:", obtained))
        left.addSpacing(7)

        objectives = QHBoxLayout()
        objectives.setSpacing(8)
        for title, prefix in (("Received items", "Start"), ("Items to farm", "Farm")):
            rows = [tuple(f"{prefix}Item{slot}{suffix}" for suffix in ("Type", "ID", "Count")) for slot in (1, 2, 3)]
            objectives.addLayout(self.objectives_grid(title, ("Type", "TypeID", "Count"), rows))
        monsters = self.objectives_grid("Mobs to fight", ("Type", "Count"),
                                        (("Mob1ID", "Mob1Count"), ("Mob2ID", "Mob2Count")), numbered=False)
        pvp_label = QLabel("PvP kills")
        monsters.addWidget(pvp_label, 4, 0)
        monsters.addWidget(self.field("PVPKills"), 4, 1)
        objectives.addLayout(monsters)
        left.addLayout(objectives)
        self.objective.currentIndexChanged.connect(self.enable_objectives)
        self.enable_objectives()
        left.addStretch()

        quest_id = QLabel(f"Quest ID:    {row}")
        quest_id.setFont(bold)
        quest_id.setFixedHeight(20)
        right.addWidget(quest_id)
        right.addLayout(self.control_row("Quest for:", self.field("Faction", QUEST_FACTIONS, width=104),
                                         "in mode:", self.field("Mode", OPTIONS["Mode"], width=126)))
        classes = QGridLayout()
        classes.setHorizontalSpacing(5)
        classes.setVerticalSpacing(0)
        for index, name in enumerate(JOBS):
            classes.addWidget(self.flag(name, label(name)), index // 3, index % 3)
        classes_row = self.control_row("Classes:")
        classes_row.addLayout(classes, 1)
        right.addLayout(classes_row)

        delay = self.field("MaxMinutes", width=48)
        delay.setToolTip("Maximum quest duration in minutes (MaxMinutes)")
        right.addLayout(self.control_row("Level:", self.field("MinLevel", width=40), "–",
                                         self.field("MaxLevel", width=40), "Delay:", delay, "(0: no limit)"))
        right.addLayout(self.control_row("Given by:", self.field("StartNPCType", width=37),
                                         self.field("StartNPCID", width=45), "Received by:",
                                         self.field("EndNPCType", width=37), self.field("EndNPCID", width=45)))
        refs = [self.field(name, width=45) for name in ("PrevQuest", "HG", "VG")]
        for field, name in zip(refs, ("PrevQuest", "HG", "VG")):
            field.setToolTip(f"Alternative prerequisite quest ID ({name})")
        repeat = self.flag("TickReceive", "Repeatable")
        repeat.setToolTip("Allow this quest to be accepted again after completion (TickReceive)")
        right.addLayout(self.control_row("Requires:", *refs, repeat))
        right.addStretch()
        return layout

    def build_dialogue(self, content):
        self.texts = [self.paragraph(value) for value in self.quest.texts]
        sections = (
            (5, "Welcome Msg:", 18, 18),
            (4, "Initial Description: (displayed when quest is given, also ‘Quest Info’ in quest window)", 69, 95),
            (0, "Summary: (‘Quest Mission’ in quest window)", 32, 43),
            (6, "Reminder instructions: (displayed by the NPC if the quest is incomplete)", 18, 18),
            (7, "Alternate Response (displayed when the quest is done or inactive):", 18, 30),
            (1, "Completion Msg: (displayed when the quest is validated with all requirements fulfilled)", 30, 56),
            (2, "Alternate Completion Msg 2:", 30, 43),
            (3, "Alternate Completion Msg 3:", 30, 43),
        )
        for index, title, compact, tall in sections:
            widget, _ = self.texts[index]
            widget.setAccessibleName(title)
            widget.document().setDocumentMargin(2)
            widget.setFixedHeight(tall if self.tall else compact)
            if index in (5, 6):
                widget.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
                widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            caption = QLabel(title)
            caption.setFixedHeight(14)
            caption.setBuddy(widget)
            caption.setStyleSheet("color: #606060")
            group = QVBoxLayout()
            group.setSpacing(0)
            group.addWidget(caption)
            group.addWidget(widget)
            content.addLayout(group)

    def build_rewards(self, content):
        rewards = QGridLayout()
        rewards.setHorizontalSpacing(5)
        rewards.setVerticalSpacing(3)
        headings = ("", "Mob Counter", "Item Counter", "Gold", "XP", "Type", "TypeID", "Count", "Unlock")
        title = QLabel("Rewards")
        bold = title.font()
        bold.setBold(True)
        title.setFont(bold)
        rewards.addWidget(title, 0, 0, 1, len(headings))
        for column, heading in enumerate(headings[1:], 1):
            rewards.addWidget(QLabel(heading), 1, column)

        for slot in (1, 2, 3):
            prefix = f"Reward{slot}"
            row = slot + 1
            rewards.addWidget(QLabel(f"{slot}:"), row, 0)
            for column, (target, choices) in enumerate((("Mob", ("—", "1", "2")), ("Item", ("—", "1", "2", "3"))), 1):
                selector = self.field(prefix + f"Need{target}ID", choices, width=42)
                selector.setToolTip(f"Select the {target.lower()} objective slot for reward {slot}")
                count = self.field(prefix + f"Need{target}Count", width=40)
                selector.currentIndexChanged.connect(
                    lambda _, control=count, combo=selector: self.toggle_counter(combo, control)
                )
                count.setEnabled(self.editable and bool(selector.currentData()))
                rewards.addLayout(self.control_row(selector, count), row, column)
            item_type = self.field(prefix + "ItemType", width=44)
            item_id = self.field(prefix + "ItemTypeID", width=48)
            item_count = self.line("")
            item_count.setReadOnly(True)
            item_count.setFixedSize(44, 20)
            item_count.setToolTip("GX awards one item per reward variant")
            item_count.setAccessibleName(f"Reward {slot} item count")

            def update_count(widget=item_count, kind=item_type, ident=item_id):
                try:
                    present = 1 <= int(kind.text()) <= 255 and 1 <= int(ident.text()) <= 255
                except ValueError:
                    present = False
                widget.setText("1" if present else "0")

            item_type.textChanged.connect(lambda _, update=update_count: update())
            item_id.textChanged.connect(lambda _, update=update_count: update())
            update_count()
            for column, widget in enumerate((self.field(prefix + "Gold", width=84), self.field(prefix + "XP", width=84),
                                             item_type, item_id, item_count, self.field(prefix + "NextQuest", width=54)), 3):
                rewards.addWidget(widget, row, column, Qt.AlignmentFlag.AlignLeft)

        rewards.setColumnStretch(len(headings), 1)
        content.addLayout(rewards)
        if self.quest.fields["ResultType"] in (0, 1):
            default = self.flag("ResultType", "Give default reward")
        else:
            default = self.field("ResultType", OPTIONS["ResultType"])
        self.reward_choices = self.field("UserSelect", REWARD_CHOICES, width=280)
        self.reward_choices.setEnabled(self.editable and self.quest.fields["ResultType"] != 1)
        if isinstance(default, QCheckBox):
            default.toggled.connect(self.toggle_default_reward)
        else:
            default.currentIndexChanged.connect(lambda _: self.toggle_default_reward(default.currentData() == 1))
        reward_mode = self.control_row(default, "or", self.reward_choices)
        reward_mode.addStretch()
        content.addLayout(reward_mode)

    def build_additional_fields(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Additional Quest Fields")
        dialog.resize(615, 385)
        layout = QVBoxLayout(dialog)
        remaining = [(name, codec) for name, codec in QUEST_FIELDS if name not in self.fields]
        grid, fields = self.values(remaining, self.quest.fields)
        self.fields.update(fields)
        body = QVBoxLayout()
        body.addLayout(grid)
        layout.addWidget(self.scroll(body))
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.accept)
        layout.addWidget(close)
        return dialog

    def enable_objectives(self, *_):
        kind = self.objective.currentData()
        for name, (widget, _) in self.fields.items():
            if name.startswith("FarmItem"):
                widget.setEnabled(self.editable and kind == 1)
            elif name.startswith(("Mob1", "Mob2")):
                widget.setEnabled(self.editable and kind == 2)
            elif name == "PVPKills":
                widget.setEnabled(self.editable and kind == 3)

    def toggle_default_reward(self, enabled):
        if enabled:
            self.reward_choices.setCurrentIndex(self.reward_choices.findData(0))
        self.reward_choices.setEnabled(self.editable and not enabled)

    def toggle_counter(self, selector, count):
        active = bool(selector.currentData())
        if not active:
            count.setText("0")
        count.setEnabled(self.editable and active)

    def field(self, name, options=None, *, width=0):
        widget = self.number(name, self.quest.fields[name], options)
        widget.setFixedHeight(20)
        if width:
            widget.setFixedWidth(width)
        else:
            widget.setMinimumWidth(34)
        self.fields[name] = (widget, self.codecs[name])
        return widget

    def flag(self, name, title):
        control = QCheckBox(title)
        control.setFixedHeight(18)
        control.setChecked(bool(self.quest.fields[name]))
        control.setEnabled(self.editable)
        control.setToolTip(name)
        self.fields[name] = (control, self.codecs[name])
        return control

    @staticmethod
    def control_row(*controls):
        row = QHBoxLayout()
        row.setSpacing(5)
        for control in controls:
            if isinstance(control, str):
                control = QLabel(control)
                control.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            row.addWidget(control)
        return row

    def objectives_grid(self, title, headings, rows, *, numbered=True):
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(3)
        header = QLabel(title)
        bold = header.font()
        bold.setBold(True)
        header.setFont(bold)
        first = int(numbered)
        grid.addWidget(header, 0, first, 1, len(headings))
        for column, heading in enumerate(headings, first):
            grid.addWidget(QLabel(heading), 1, column)
        for row, names in enumerate(rows, 2):
            if numbered:
                grid.addWidget(QLabel(str(row - 1)), row, 0)
            for column, name in enumerate(names, first):
                grid.addWidget(self.field(name), row, column)
        return grid

    def accept(self):
        try:
            self.result_data = Quest(self.read_values(self.fields, self.quest.fields),
                                     self.read_text(self.name, self.quest.name),
                                     tuple(self.read_text(widget, text, shown) for text, (widget, shown)
                                           in zip(self.quest.texts, self.texts)))
        except ValueError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        super().accept()


class ItemLinksDialog(QuestForm):
    def __init__(self, editor, key):
        super().__init__(editor, "Item Quest Links", "npcquest-links")
        self.key = key
        self.original = editor.draft.item_links.get(key, EMPTY_LINKS)
        layout = QVBoxLayout(self)
        if key is None:
            fields = QFormLayout()
            self.item_id = QSpinBox()
            self.item_id.setRange(1001, 255255)
            fields.addRow("Item ID:", self.item_id)
            layout.addLayout(fields)
        else:
            layout.addWidget(QLabel(f"Item ID  {key[0] * 1000 + key[1]}"))
        links, self.controls = self.link_fields(self.original)
        layout.addLayout(links)
        buttons(self, layout)
        self.restore_size(QSize(480, 155))

    def accept(self):
        try:
            if self.key is None:
                key = divmod(self.item_id.value(), 1000)
                if not 1 <= key[0] <= 255 or not 1 <= key[1] <= 255:
                    raise ValueError("Item type and ID must each be between 1 and 255.")
                if key in self.editor.draft.item_links:
                    raise ValueError("This item already has links. Open its row to edit them.")
            else:
                key = self.key
            self.result_data = key, self.read_links(self.controls, self.original)
        except ValueError as error:
            QMessageBox.warning(self, self.windowTitle(), str(error))
            return
        super().accept()


class QuestList(QAbstractTableModel):
    def __init__(self, parent, *, draggable=False, icon_for=None):
        super().__init__(parent)
        self.headers = ()
        self.rows = []
        self.centered_columns = None
        self.draggable = draggable
        self.icon_for = icon_for

    def reset(self, headers, rows, *, centered_columns=None):
        self.beginResetModel()
        self.headers, self.rows = headers, rows
        self.centered_columns = centered_columns
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def flags(self, index):
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsDragEnabled if self.draggable and index.isValid() else flags

    def headerData(self, section, orientation, role=DISPLAY):
        if orientation == Qt.Orientation.Horizontal:
            if role == DISPLAY:
                return self.headers[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return self.alignment(section)

    def data(self, index, role=DISPLAY):
        if index.isValid():
            if role == DISPLAY:
                return self.rows[index.row()][1][index.column()]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return self.alignment(index.column())
            if role == Qt.ItemDataRole.DecorationRole and index.column() == 2 and self.icon_for:
                return self.icon_for(self.rows[index.row()][0])
            if role == Qt.ItemDataRole.ToolTipRole and self.icon_for:
                text = self.rows[index.row()][1][index.column()]
                if index.column() == 2:
                    return text
                if index.column() >= 3 and text == "—":
                    return "No level requirement" if index.column() == 3 else "No sockets"

    def alignment(self, column):
        if self.centered_columns is not None:
            horizontal = Qt.AlignmentFlag.AlignHCenter if column in self.centered_columns else Qt.AlignmentFlag.AlignLeft
            return (horizontal | Qt.AlignmentFlag.AlignVCenter).value


class NPCQuestWindow(QMainWindow):
    def __init__(self, studio):
        super().__init__(studio, Qt.WindowType.Window)
        self.studio = studio
        self.settings = studio.settings
        self.source = self.server_path = None
        self.catalog = self.server = self.draft = None
        self.model_assets = None
        self.item_stock = None
        self.server_error = ""
        self.rules_differ = False
        self.resize(810, 475)

        menu = self.menuBar().addMenu("&File")
        menu.addAction("&Close", "Ctrl+W", self.close)
        self.save = menu.addAction("&Save", "Ctrl+S", studio.save_npcquest)
        self.install = menu.addAction("&Install Rules to Server…", studio.install_npcquest)
        self.install.setVisible(not studio.legacy)
        self.export = menu.addAction("Export Client Copy…", studio.export_npcquest)
        self.export_quest_sentences = menu.addAction("Export Sentences of a Quest…", self.export_sentences)
        self.reload = menu.addAction("&Reload…", studio.reload_npcquest)
        edit = self.menuBar().addMenu("&Edit")
        self.edit = edit.addAction("Edit Entry…", "Return", self.edit_selected)
        self.add = edit.addAction("New Entry…", "Ctrl+N", self.add_entry)
        self.remove = edit.addAction("Delete Entry…", "Delete", self.remove_entry)
        edit.addSeparator()
        self.item_links = edit.addAction("Item Links…", self.open_item_links)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(5, 4, 5, 0)
        self.client_info = QLabel()
        self.server_info = QLabel()
        for info in (self.client_info, self.server_info):
            info.setTextFormat(Qt.TextFormat.PlainText)
        self.choose_server = QPushButton("Select Server File…")
        self.choose_server.clicked.connect(studio.choose_npcquest_server)

        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        for page in PAGES:
            self.tabs.addTab(page)
        self.tabs.setCurrentIndex(MERCHANTS)
        self.tabs.currentChanged.connect(self.change_page)
        layout.addWidget(self.tabs)
        self.model = QuestList(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        configure_list(self.table)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.doubleClicked.connect(lambda _: self.edit_selected())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.quest_table_menu)
        self.table.selectionModel().currentChanged.connect(self.update_actions)
        layout.addWidget(self.table)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find by name or ID")
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.search.textChanged.connect(self.update_actions)
        self.search.hide()
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.show_search)
        layout.addWidget(self.search)
        sources = QHBoxLayout()
        sources.addWidget(self.client_info)
        sources.addWidget(self.server_info)
        sources.addStretch()
        sources.addWidget(self.choose_server)
        self.choose_server.setVisible(not studio.legacy)
        self.server_info.setVisible(not studio.legacy)
        layout.addLayout(sources)
        self.setCentralWidget(central)
        self.refresh()

    def load(self, source, client, server_path, server, server_error=""):
        new_source = source != self.source
        selection = self.selected() if not new_source else None
        if new_source:
            root = source.parent.parent if source.parent.name.casefold() == "npc" else self.studio.client_data
            self.model_assets = ModelAssets(root) if root else None
            self.item_stock = None
        self.source, self.catalog = source, client
        self.server_path, self.server = server_path, server
        self.server_error = server_error
        self.rules_differ = server is not None and client.data.shared() != server.data.shared()
        self.draft = client.data.draft()
        if new_source:
            self.reset_opening_tab()
        self.refresh(selection)

    def clear(self):
        self.source = self.server_path = self.catalog = self.server = self.draft = None
        self.server_error = ""
        self.rules_differ = False
        self.model_assets = self.item_stock = None
        self.refresh()
        self.close()

    def link_server(self, path, server, error=""):
        self.server_path, self.server = path, server
        self.server_error = error
        self.rules_differ = server is not None and self.catalog.data.shared() != server.data.shared()
        self.update_actions()

    def changed(self):
        return self.catalog is not None and self.draft != self.catalog.data

    def change_page(self, _):
        self.search.setVisible(bool(self.search.text()))
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.refresh()
        self.table.scrollToTop()

    def reset_opening_tab(self):
        self.search.clear()
        self.search.hide()
        self.tabs.setCurrentIndex(MERCHANTS)

    def show_search(self):
        self.search.show()
        self.search.setFocus()
        self.search.selectAll()

    def shop_item_source(self):
        root = self.model_assets.root if self.model_assets else self.studio.client_data
        return self.studio.find_client_catalog(root)

    def shop_items(self):
        if (source := self.shop_item_source()) is None:
            return {}
        model = self.studio.item_window.model
        from_model = model is not None and model.workspace.source == source
        if from_model:
            items, value = model.items, model.value
        else:
            if self.item_stock is not None:
                return self.item_stock
            try:
                catalog = Catalog.open(source)
            except (OSError, ValueError):
                return {}
            items = catalog.items.values()

            def value(item, field):
                return catalog.value(item, FIELDS_BY_NAME[field])

        stock = {}
        for item in items:
            stock[item.family, item.type_id] = MerchantItem(
                str(value(item, "Name")), value(item, "MinLevel"), str(value(item, "Description")), value(item, "Slot"),
                value(item, "Country"), value(item, "Range") | (value(item, "AttackTime") << 8), value(item, "Special"),
                any(value(item, field) for field in ("RecoveryHP", "RecoveryMP", "RecoverySP")),
                value(item, "Icon"),
            )
        if not from_model:
            self.item_stock = stock
        return stock

    def edit_shop_item(self, key, parent, on_edit):
        source = self.shop_item_source()
        studio = self.studio
        if source is None or studio.task is not None:
            return

        def edit(workspace):
            if parent.isVisible() and (item := workspace.catalog.items.get(key[0] * 1000 + key[1])) is not None:
                if studio.item_window.edit_catalog_item(item, parent=parent):
                    self.item_stock = None
                    on_edit()

        if studio.workspace is not None and studio.workspace.source == source:
            edit(studio.workspace)
        elif studio.confirm_discard(items_only=True):
            studio.load(source, show_items=False, on_loaded=edit)

    def selected(self):
        index = self.proxy.mapToSource(self.table.currentIndex())
        return self.model.rows[index.row()][0] if index.isValid() else None

    def refresh(self, key=None):
        rows = []
        page = self.tabs.currentIndex()
        if page == QUESTS:
            headers = ("ID", "Name", "Lvl min", "Lvl max", "Type", "Mode", "Reward", "Default", "Repeat")
            widths = (50, 150, 55, 55, 55, 50, 50, 50, 50)
            if self.draft:
                for quest_row, quest in self.draft.quests.items():
                    fields = quest.fields
                    rows.append((quest_row, (quest_row, quest.name, fields["MinLevel"], fields["MaxLevel"],
                                       option_text(END_TYPES, fields["EndType"]), fields["StartType"],
                                       fields["UserSelect"], fields["ResultType"], int(bool(fields["TickReceive"])))))
        elif page == MERCHANTS:
            headers = ("TypeID", "Name", "Cntr", "Shape", "Profession", "# Items")
            widths = (50, 150, 45, 45, 150, 70)
            if self.draft:
                rows = [(key, (npc.fields["NPCTypeID"], npc.name, npc.fields["Faction"], npc.fields["Model"],
                               PROFESSIONS.get(npc.fields["MerchantType"], str(npc.fields["MerchantType"])),
                               sum(product != EMPTY_PRODUCT for product in npc.products)))
                        for key, npc in self.draft.npcs.items() if key[0] == 1]
        elif page == GATES:
            headers = ("TypeID", "Name", "Cntr", "Shape", "Dest 1", "Dest 2", "Dest 3")
            widths = (50, 150, 45, 45, 120, 120, 120)
            if self.draft:
                rows = [(key, (npc.fields["NPCTypeID"], npc.name, npc.fields["Faction"], npc.fields["Model"],
                               *(gate.label for gate in npc.gates))) for key, npc in self.draft.npcs.items() if key[0] == 2]
        else:
            headers = ("Type", "Type name", "UID", "Name", "Cntr", "Shape", "#Qst In", "#Qst Out", "Qst In", "Qst Out")
            widths = (50, 80, 50, 150, 45, 45, 50, 50, 50, 50)
            if self.draft:
                rows = [(key, (npc.fields["NPCType"], option_text(NPC_TYPES, npc.fields["NPCType"]),
                               npc.fields["NPCTypeID"], npc.name,
                               npc.fields["Faction"], npc.fields["Model"], *map(len, npc.links),
                               *(",".join(map(str, ids)) for ids in npc.links)))
                        for key, npc in self.draft.npcs.items() if key[0] >= 3]
        centered = {
            MERCHANTS: (2, 3, 5), GATES: (2, 3), NPCS: (1, 2, 4, 5, 6, 7),
            QUESTS: (2, 3, 4, 5, 6, 7, 8),
        }[page]
        self.model.reset(headers, rows, centered_columns=centered)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table.horizontalHeader().setHighlightSections(page != QUESTS)
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)
        if key is not None:
            for row, (wanted, _) in enumerate(rows):
                if wanted == key and (index := self.proxy.mapFromSource(self.model.index(row, 0))).isValid():
                    self.table.selectRow(index.row())
                    break
        self.update_actions()

    def update_actions(self, *_):
        loaded = self.catalog is not None and self.studio.task is None
        editable = loaded and self.catalog.writable
        selected = self.selected() is not None
        both_writable = self.server is None or self.server.writable
        self.save.setEnabled(editable and both_writable and self.changed())
        self.install.setEnabled(editable and self.server is not None and self.server.writable
                                and (self.rules_differ or self.changed()))
        self.export.setEnabled(loaded and getattr(self.catalog, "legacy_episode", None) != "8")
        self.export_quest_sentences.setEnabled(loaded and selected and self.tabs.currentIndex() == QUESTS)
        self.reload.setEnabled(loaded)
        self.choose_server.setText("Server Data Settings…" if self.source == self.studio.client_quests else "Select Server File…")
        self.choose_server.setEnabled(loaded)
        self.item_links.setEnabled(loaded)
        self.edit.setEnabled(loaded and selected)
        self.add.setEnabled(editable)
        self.remove.setEnabled(editable and selected)
        self.setWindowTitle(f"NpcQuest.SData{' *' if self.changed() else ''}")
        self.client_info.setText(f"Client · {self.encryption(self.catalog)}" if self.catalog else "")
        self.client_info.setToolTip(str(self.source) if self.source else "")
        if self.server:
            rules = "Different rules" if self.rules_differ else "Rules in sync"
            server_label = f"Server · {self.encryption(self.server)} · {episode_label(self.server)} · {rules}"
        else:
            server_label = "Server file unavailable" if self.server_error else "Server file not linked"
        self.server_info.setText(server_label if self.catalog else "")
        self.server_info.setToolTip(self.server_error or (str(self.server_path) if self.server_path else ""))
        note = " · Read-only preview" if getattr(self.catalog, "legacy_preview", False) else ""
        note += getattr(self.catalog, "legacy_text_status", "")
        self.statusBar().showMessage(f"{self.proxy.rowCount():,} entries{note}")

    @staticmethod
    def encryption(catalog):
        status = envelope_label(catalog.raw, len(catalog.raw))
        return status if catalog.writable else f"{status} (read-only)"

    def open_item_links(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Item Quest Links")
        dialog.resize(665, 410)
        layout = QVBoxLayout(dialog)
        listing = QTreeWidget()
        listing.setHeaderLabels(("Item ID", "Type", "TypeID", "Start quests", "End quests"))
        listing.setRootIsDecorated(False)
        listing.setUniformRowHeights(True)
        for column, width in enumerate((80, 60, 65, 200)):
            listing.setColumnWidth(column, width)
        layout.addWidget(listing, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(dialog.accept)
        add = box.addButton("New Link…", QDialogButtonBox.ButtonRole.ActionRole)
        edit = box.addButton("Edit…", QDialogButtonBox.ButtonRole.ActionRole)
        remove = box.addButton("Delete", QDialogButtonBox.ButtonRole.ActionRole)
        add.setEnabled(self.catalog.writable)
        layout.addWidget(box)

        def refresh():
            listing.clear()
            for key, links in sorted(self.draft.item_links.items()):
                values = (str(key[0] * 1000 + key[1]), *map(str, key), *(", ".join(map(str, ids)) for ids in links))
                item = QTreeWidgetItem(listing, values)
                item.setData(0, Qt.ItemDataRole.UserRole, key)

        def edit_links(key):
            form = ItemLinksDialog(self, tuple(key) if key is not None else None)
            if form.exec() == QDialog.DialogCode.Accepted:
                key, links = form.result_data
                if links == EMPTY_LINKS:
                    self.draft.item_links.pop(tuple(key), None)
                else:
                    self.draft.item_links[tuple(key)] = links
                refresh()
                self.update_actions()
            form.deleteLater()

        def remove_links():
            item = listing.currentItem()
            if item is not None:
                del self.draft.item_links[tuple(item.data(0, Qt.ItemDataRole.UserRole))]
                refresh()
                self.update_actions()

        def selection(current, _):
            edit.setEnabled(current is not None)
            remove.setEnabled(current is not None and self.catalog.writable)

        add.clicked.connect(lambda: edit_links(None))
        edit.clicked.connect(lambda: edit_links(listing.currentItem().data(0, Qt.ItemDataRole.UserRole)))
        remove.clicked.connect(remove_links)
        listing.currentItemChanged.connect(selection)
        listing.itemDoubleClicked.connect(lambda item, _: edit_links(item.data(0, Qt.ItemDataRole.UserRole)))
        selection(None, None)
        refresh()
        dialog.exec()
        dialog.deleteLater()

    def edit_selected(self):
        key = self.selected()
        if key is not None:
            self.edit_entry(key)

    def quest_table_menu(self, point):
        if self.tabs.currentIndex() != QUESTS or self.studio.task is not None or self.catalog is None:
            return
        index = self.table.indexAt(point)
        if index.isValid():
            self.table.selectRow(index.row())
        menu = QMenu(self)
        menu.addAction(self.edit)
        menu.addAction(self.add)
        menu.addAction(self.remove)
        menu.addSeparator()
        menu.addAction(self.export_quest_sentences)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def export_sentences(self):
        row = self.selected()
        if row is None or not self.export_quest_sentences.isEnabled():
            return
        quest = self.draft.quests[row]
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Quest Sentences", str(self.source.with_name(f"Quest-{row}.txt")), "Text files (*.txt)",
        )
        if not filename:
            return
        destination = Path(filename).resolve()
        if any(path == destination or (destination.exists() and path.exists() and path.samefile(destination))
               for path in (self.source, self.server_path) if path is not None):
            QMessageBox.warning(self, "Export Quest Sentences", "Select a separate text file for the export.")
            return
        raw = ("\n".join((quest.name, *quest.texts)) + "\n").encode("utf-8")
        self.studio.run_task("Exporting quest sentences…", lambda: replace_file(destination, raw, allow_create=True),
                             lambda _: QMessageBox.information(self, "Export Quest Sentences", f"Exported to\n{destination}"))

    def edit_entry(self, key, new=None):
        page = self.tabs.currentIndex()
        if page == QUESTS:
            dialog = QuestDialog(self, key, new or self.draft.quests[key])
        else:
            dialog = NPCDialog(self, key, new or self.draft.npcs[key])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if page == QUESTS:
                self.draft.quests[key] = dialog.result_data
            else:
                key = dialog.result_key
                self.draft.npcs[key] = dialog.result_data
            self.refresh(key)
        dialog.deleteLater()

    def add_entry(self):
        page = self.tabs.currentIndex()
        if page == QUESTS:
            key = len(self.draft.quests) + 1
            if key <= 65535:
                self.edit_entry(key, Quest(empty_fields(QUEST_FIELDS) | {"EmbeddedID": key}))
        elif page == NPCS:
            self.edit_entry(None, NPC(empty_fields(NPC_FIELDS)))
        else:
            group = page + 1
            row = self.next_npc_row(group)
            if row <= 65535:
                fields = empty_fields(NPC_FIELDS) | {"NPCType": group, "NPCTypeID": row}
                if group == 1:
                    fields["MerchantType"] = 0
                gates = tuple(Gate() for _ in range(3)) if group == 2 else ()
                self.edit_entry((group, row), NPC(fields, gates=gates))

    def next_npc_row(self, group):
        return 1 + max((row for kind, row in self.draft.npcs if kind == group), default=0)

    def remove_entry(self):
        key = self.selected()
        if key is None:
            return
        page = self.tabs.currentIndex()
        if page == QUESTS:
            records = self.draft.quests
            linked = (any(key in start or key in end for start, end in self.draft.item_links.values()) or
                      any(key in npc.links[0] or key in npc.links[1] for npc in self.draft.npcs.values()) or
                      any(q.fields["PrevQuest"] == key or any(q.fields[f"Reward{i}NextQuest"] == key for i in (1, 2, 3))
                          for row, q in records.items() if row != key))
            allowed = key == max(records) and not linked
        else:
            records = self.draft.npcs
            linked = any((quest.fields[f"{prefix}NPCType"], quest.fields[f"{prefix}NPCID"]) == key
                         for quest in self.draft.quests.values() for prefix in ("Start", "End"))
            allowed = key == max(k for k in records if k[0] == key[0]) and not linked
        if not allowed:
            QMessageBox.warning(self, "Delete Entry", "Only the last unreferenced row can be removed. GX uses row numbers as IDs.")
            return
        answer = QMessageBox.question(self, "Delete Entry", "Remove the selected entry from the draft?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            del records[key]
            self.refresh()

    def closeEvent(self, event):
        if self.isVisible():
            self.settings.setValue("npcQuest/geometry", self.saveGeometry())
            self.settings.sync()
        self.reset_opening_tab()
        super().closeEvent(event)

    def restore_window(self):
        geometry = self.settings.value("npcQuest/geometry", QByteArray(), type=QByteArray)
        return not geometry.isEmpty() and self.restoreGeometry(geometry)
