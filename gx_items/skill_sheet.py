from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea
from PySide6.QtWidgets import QSpinBox, QTabWidget, QToolButton, QVBoxLayout, QWidget

from .item_view import DARK_CLASSES, LIGHT_CLASSES
from .pickers import IconPicker
from .skill_catalog import SKILL_FIELDS_BY_NAME, SkillEdits, SkillKey
from .skill_layout import CLASS_FIELDS, COMBOS, REFLECT_FIELDS, STAT_FAMILIES, WEAPON_FIELDS, WEAPON_PASSIVES, WEAPONS
from .skill_layout import additional_rank_fields, column_label, rank_columns, rank_title


class SkillSizedDialog(QDialog):
    def __init__(self, parent, settings, key):
        super().__init__(parent)
        self.settings = settings
        self.size_key = f"skills/size/{key}"

    def restore_size(self, default: QSize):
        size = self.settings.value(self.size_key, default, type=QSize)
        room = self.screen().availableGeometry().size() - QSize(24, 50)
        self.resize(size.boundedTo(room).expandedTo(self.minimumSizeHint()))
        self.initial_size = self.size()

    def done(self, result):
        if self.size() != self.initial_size:
            self.settings.setValue(self.size_key, self.size())
            self.settings.sync()
        super().done(result)


    @staticmethod
    def add_labeled(layout, title, control):
        label = QLabel(title)
        label.setBuddy(control)
        layout.addWidget(label)
        layout.addWidget(control)

    def make_field(self, name, value, *, enabled=True, flag=False, width=0, choices=None):
        field = SKILL_FIELDS_BY_NAME[name]
        if flag or name in CLASS_FIELDS or name in WEAPON_FIELDS:
            control = QCheckBox()
            control.setChecked(bool(value))
        elif choices is not None or name in COMBOS:
            control = QComboBox()
            for code, title in (COMBOS[name] if choices is None else choices).items():
                label = f"{code} - {title}" if name == "TypeDetail" else title
                control.addItem(label, code)
            index = control.findData(value)
            if index < 0 and name.startswith("AbilityType"):
                control.setPlaceholderText(f"Legacy ({value})")
                control.setProperty("legacyValue", value)
            elif index < 0:
                control.addItem(str(value), value)
                index = control.count() - 1
            control.setCurrentIndex(index)
            if width:
                control.setFixedWidth(width)
            elif name.startswith("AbilityType"):
                control.setFixedWidth(138)
                control.view().setMinimumWidth(205)
            else:
                control.setMinimumWidth(105)
        elif not field.size:
            control = QLineEdit(value)
        else:
            control = QSpinBox()
            control.setRange(*field.numeric_limits())
            control.setValue(value)
            control.setAlignment(Qt.AlignmentFlag.AlignRight)
            control.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            control.setFixedWidth(width or 54)
        control.setEnabled(self.editable and enabled)
        control.setAccessibleName(field.label)
        if not isinstance(control, QCheckBox):
            control.setFixedHeight(max(20, control.fontMetrics().height() + 4))
        return control

    @staticmethod
    def control_value(control):
        if isinstance(control, QComboBox):
            return control.property("legacyValue") if control.currentIndex() == -1 else control.currentData()
        if isinstance(control, QCheckBox):
            return int(control.isChecked())
        if isinstance(control, QSpinBox):
            return control.value()
        return control.text()


class SkillTextDialog(SkillSizedDialog):
    def __init__(self, parent, rows: SkillEdits, current: SkillKey):
        super().__init__(parent, parent.settings, "text")
        self.setWindowTitle("Skill Names and Descriptions")
        layout = QVBoxLayout(self)
        self.setMinimumSize(490, 330)
        self.ranks = QTabWidget()
        self.inputs = {}
        for key, fields in rows.items():
            page = QWidget()
            form = QVBoxLayout(page)
            label_name = QLabel("Name:")
            name = QLineEdit(str(fields["Name"]))
            name.setAccessibleName(f"Rank {key[1]} name")
            label_name.setBuddy(name)
            label_description = QLabel("Description:")
            description = QPlainTextEdit(str(fields["Description"]))
            description.setAccessibleName(f"Rank {key[1]} description")
            label_description.setBuddy(description)
            form.addWidget(label_name)
            form.addWidget(name)
            form.addWidget(label_description)
            form.addWidget(description, 1)
            self.inputs[key] = (name, description, name.text(), description.toPlainText())
            index = self.ranks.addTab(page, f"Rank {key[1]}")
            if key == current:
                self.ranks.setCurrentIndex(index)
        self.inputs[current][1].setFocus()
        layout.addWidget(self.ranks, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.restore_size(QSize(690, 420))

    def changes(self) -> SkillEdits:
        result = {}
        for rank, (name, description, before_name, before_description) in self.inputs.items():
            values = {field: after for field, before, after in (
                ("Name", before_name, name.text()), ("Description", before_description, description.toPlainText()),
            ) if before != after}
            if values:
                result[rank] = values
        return result


class SkillDialog(SkillSizedDialog):
    def __init__(self, editor, key: SkillKey):
        first = editor.groups[key[0]][0]
        noss = not editor.value(first, "TypeShow")
        super().__init__(editor, editor.studio.settings, "noss" if noss else "toon")
        self.editor = editor
        self.keys = tuple(editor.groups[key[0]])
        self.first = first
        self.editable = editor.catalog.writable
        self.edits: SkillEdits = {}
        self.shared = []
        self.profile_shared = []
        self.per_rank = []
        self.profile_values: SkillEdits = {}
        self.overrides: SkillEdits = {}
        self.profile = None
        self.setWindowTitle("Noss Editor" if noss else "Toon Skill Editor")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(5)
        top = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(4)
        left.addLayout(self.identity())
        left.addWidget(QLabel("Description:"))
        description = QHBoxLayout()
        self.preview = QPlainTextEdit(str(editor.value(first, "Description")))
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(77)
        description.addWidget(self.preview, 1)
        edit = QPushButton("Edit Text…")
        edit.setToolTip("Edit names and descriptions for all ranks")
        edit.setEnabled(self.editable)
        edit.clicked.connect(lambda: self.edit_text(self.first))
        description.addWidget(edit)
        left.addLayout(description)
        left.addLayout(self.checks("Used by:", CLASS_FIELDS, LIGHT_CLASSES, 6))
        faction = QHBoxLayout()
        self.country = self.shared_control("Country")
        self.country.currentIndexChanged.connect(self.update_class_labels)
        self.add_labeled(faction, "Faction:", self.country)
        self.add_labeled(faction, "in mode:", self.make_field("Grow", editor.value(self.first, "Grow"), enabled=False))
        faction.addStretch()
        left.addLayout(faction)
        top.addLayout(left)
        effects = QHBoxLayout()
        self.add_labeled(effects, "Type:", self.shared_control("TypeAttack"))
        self.effect = self.shared_control("TypeEffect")
        self.add_labeled(effects, "Effect:", self.effect)
        effects.addStretch()
        left.addLayout(effects)
        left.addLayout(self.checks("Skill usable with:", WEAPON_FIELDS, WEAPONS, 4))
        top.addStretch()
        layout.addLayout(top)
        self.update_class_labels()

        self.profile_holder = QVBoxLayout()
        self.profile_holder.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.profile_holder, 1)
        self.rebuild_profile()
        self.spell_effect.currentIndexChanged.connect(self.rebuild_profile)
        self.category.currentIndexChanged.connect(self.rebuild_profile)
        self.effect.currentIndexChanged.connect(self.rebuild_profile)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.editable)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.restore_size(QSize(self.preferred_width, self.minimumSizeHint().height()))

    @property
    def family(self) -> int:
        return self.spell_effect.currentData()

    @property
    def noss(self) -> bool:
        return not self.category.currentData()

    def profile_value(self, rank, name):
        stored = self.profile_values.get(rank, {})
        return stored[name] if name in stored else self.editor.value(rank, name)

    def rebuild_profile(self, *_):
        if self.profile is not None:
            self.profile_values = self.profile_draft()
            self.profile_holder.removeWidget(self.profile)
            self.profile.hide()
            self.profile.deleteLater()
        self.profile_shared = []
        self.per_rank = []
        self.profile = QWidget()
        layout = QVBoxLayout(self.profile)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        family = self.family
        if family in WEAPON_PASSIVES:
            layout.addLayout(self.shared_row((("Requires:", "Weapon1"), ("or", "Weapon2")), profile=True))
        if family not in WEAPON_PASSIVES | {2, 4, 5, 6} and not 41 <= family <= 60:
            modes = (("Def. Type:", "DefenceType"),) if family in (85, 86) else ()
            layout.addLayout(self.shared_row((*modes, ("Damage Type:", "DamageType"), ("State Type:", "StateType")),
                                             profile=True))
        if family in STAT_FAMILIES:
            stats = QHBoxLayout()
            for index in range(1, 4):
                name = f"AbilityType{index}"
                value = next((value for rank in self.keys if (value := self.profile_value(rank, name))), 0)
                control = self.shared_control(name, value=value, profile=True)
                self.add_labeled(stats, f"Stat. {index}:", control)
            stats.addStretch()
            if family == 4:
                layout.addWidget(QLabel("Enhanced Abilities:"))
            layout.addLayout(stats)

        columns = rank_columns(family, self.effect.currentData(), self.profile_value(self.first, "StateType"), noss=self.noss)
        extra_fields = additional_rank_fields(columns)
        matrix = self.rank_grid(columns, extra_fields)
        room = self.screen().availableGeometry()
        matrix_size = matrix.sizeHint()
        self.preferred_width = min(matrix_size.width() + 36, 740 if self.noss else 1080, room.width() - 24)
        scroll = QScrollArea()
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(matrix)
        padding = 20 if self.preferred_width < matrix_size.width() + 36 else 4
        scroll.setMinimumHeight(min(matrix_size.height() + padding, max(84, room.height() // 3)))
        layout.addWidget(scroll, 1)
        self.profile_holder.addWidget(self.profile)

    def identity(self):
        form = QGridLayout()
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(4)
        self.icon = QPushButton()
        self.icon.setAccessibleName("Select skill icon")
        self.icon.setFixedSize(39, 39)
        self.icon.setIconSize(QSize(32, 32))
        self.icon_value = int(self.editor.value(self.first, "Icon"))
        self.icon.setIcon(QIcon(self.editor.icons.pixmap(self.icon_value)))
        self.icon.setEnabled(self.editable and self.editor.icons.assets is not None)
        self.icon.setAutoDefault(False)
        self.icon.clicked.connect(self.pick_icon)
        form.addWidget(self.icon, 0, 0, 2, 1)
        self.name = self.shared_control("Name", value=rank_title(self.editor.value(self.first, "Name")))
        self.spell_effect = self.shared_control("TypeDetail")
        for row, title, control in ((0, "Name:", self.name), (1, "Spell effect:", self.spell_effect)):
            label = QLabel(title)
            label.setBuddy(control)
            form.addWidget(label, row, 1)
            form.addWidget(control, row, 2)
        options = QHBoxLayout()
        self.category = self.shared_control("TypeShow")
        self.add_labeled(options, "Category:", self.category)
        self.add_labeled(options, "Animation:", self.shared_control("Animation"))
        options.addStretch()
        form.addLayout(options, 2, 1, 1, 2)
        form.setColumnStretch(2, 1)
        return form

    def shared_control(self, name, *, value=None, profile=False):
        if value is None:
            value = self.profile_value(self.first, name) if profile else self.editor.value(self.first, name)
        control = self.make_field(name, value)
        bindings = self.profile_shared if profile else self.shared
        bindings.append((name, control, self.control_value(control)))
        return control

    def shared_row(self, fields, *, profile=False):
        row = QHBoxLayout()
        row.setSpacing(5)
        for title, name in fields:
            self.add_labeled(row, title, self.shared_control(name, profile=profile))
        row.addStretch()
        return row

    def checks(self, title, fields, labels, width):
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(1)
        grid.addWidget(QLabel(title), 0, 0, 1, width)
        if fields == CLASS_FIELDS:
            self.class_checks = []
        for index, (name, text) in enumerate(zip(fields, labels)):
            control = self.shared_control(name)
            control.setText(text)
            grid.addWidget(control, index // width + 1, index % width)
            if fields == CLASS_FIELDS:
                self.class_checks.append(control)
        return grid

    def update_class_labels(self, *_):
        classes = DARK_CLASSES if self.country.currentData() in (3, 4, 5) else LIGHT_CLASSES
        for control, label in zip(self.class_checks, classes):
            control.setText(label)

    def pick_icon(self):
        icons = self.editor.available_icons()
        if icons:
            picker = IconPicker(self.icon, self.icon_value, icons, 24)
            picker.iconSelected.connect(self.set_icon)
            picker.show()

    def set_icon(self, value: int):
        self.icon_value = value
        self.icon.setIcon(QIcon(self.editor.icons.pixmap(value)))

    def edit_text(self, current: SkillKey):
        draft = self.draft_values()
        rows = {rank: {name: draft[rank].get(name, self.editor.value(rank, name)) for name in ("Name", "Description")}
                for rank in self.keys}
        dialog = SkillTextDialog(self, rows, current)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for rank, changes in dialog.changes().items():
                self.overrides.setdefault(rank, {}).update(changes)
            self.preview.setPlainText(str(self.overrides.get(self.first, {}).get("Description", rows[self.first]["Description"])))
        dialog.deleteLater()

    def rank_input(self, rank, name):
        if name == "Description":
            control = QPushButton("Edit…")
            control.setAutoDefault(False)
            control.setEnabled(self.editable)
            control.clicked.connect(lambda: self.edit_text(rank))
            return control
        if name == "Chance":
            slot = QWidget()
            row = QHBoxLayout(slot)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)
            flag = self.bind_rank(rank, "SuccessType", flag=True)
            number = self.bind_rank(rank, "SuccessValue", width=40)
            number.setEnabled(self.editable and flag.isChecked())
            flag.toggled.connect(lambda checked: number.setEnabled(self.editable and checked))
            row.addWidget(flag)
            row.addWidget(number)
            return slot
        width = {"AttrType": 78, "TargetType": 110 if self.noss else 139, "ReqLevel": 38, "SkillPoint": 35,
                 "ReadyTime": 35, "AttackRange": 39, "ApplyRange": 44, "MultiAttack": 39,
                 "SP": 45, "MP": 45, "ChangeType": 124, "FixRange": 82}.get(name, 0)
        reflection = self.family == 83 and name in REFLECT_FIELDS
        control = self.bind_rank(rank, name, flag=reflection, width=width)
        if reflection:
            control.setAccessibleName(f"Rank {rank[1]} reflects {REFLECT_FIELDS[name].lower()} attacks")
        if name == "ChangeLevel":
            form = next(widget for key, field, widget, _ in self.per_rank if key == rank and field == "ChangeType")

            def update_monster(*_):
                code = form.currentData()
                effect = self.effect.currentData()
                control.setEnabled(self.editable and (effect == 3 and code == 101 or effect == 4 and code in (10, 11, 12)))

            control.setAccessibleName(f"Rank {rank[1]} monster ID")
            form.currentIndexChanged.connect(update_monster)
            update_monster()
        return control

    def bind_rank(self, rank, name, *, flag=False, width=0):
        value = self.profile_value(rank, name)
        if flag and name in REFLECT_FIELDS:
            value = value or self.profile_value(rank, "AbilityType" + name[-1])
        control = self.make_field(name, value, flag=flag, width=width)
        control.setAccessibleName(f"Rank {rank[1]} {SKILL_FIELDS_BY_NAME[name].label}")
        self.per_rank.append((rank, name, control, self.control_value(control)))
        return control

    def rank_grid(self, columns, extra_fields):
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(1, 2, 2, 2)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(2)
        centered = Qt.AlignmentFlag.AlignCenter
        grid.addWidget(QLabel("#"), 1, 0, alignment=centered)
        for row, rank in enumerate(self.keys, 2):
            if extra_fields and self.editable:
                label = QToolButton()
                label.setText(str(rank[1]))
                label.setAutoRaise(True)
                label.setToolTip(f"Edit rank {rank[1]} details")
                label.clicked.connect(lambda _, key=rank: self.edit_rank(key, extra_fields))
            else:
                label = QLabel(str(rank[1]))
            grid.addWidget(label, row, 0, alignment=centered)
        column = 1
        for entry in columns:
            fields = (entry,) if isinstance(entry, str) else entry[1]
            if not isinstance(entry, str):
                grid.addWidget(QLabel(entry[0]), 0, column, 1, len(fields), centered)
            for name in fields:
                if not isinstance(entry, str) and entry[0] == "Reflection":
                    label = REFLECT_FIELDS[name]
                elif not isinstance(entry, str) and len(fields) == 3 and name.endswith(("HP", "SP", "MP")):
                    label = name[-2:]
                else:
                    label = column_label(name)
                grid.addWidget(QLabel(label), 1, column, alignment=centered)
                for row, rank in enumerate(self.keys, 2):
                    grid.addWidget(self.rank_input(rank, name), row, column, alignment=centered)
                column += 1
        return panel

    def edit_rank(self, rank: SkillKey, extra_fields):
        draft = self.draft_values()[rank]
        dialog = SkillSizedDialog(self, self.settings, "rank")
        dialog.setWindowTitle(f"Skill Rank {rank[1]}")
        panel = QWidget()
        forms = QHBoxLayout(panel)
        inputs = {}
        midpoint = (len(extra_fields) + 1) // 2
        for names in (extra_fields[:midpoint], extra_fields[midpoint:]):
            form = QFormLayout()
            for name in names:
                value = draft.get(name, self.editor.value(rank, name))
                control = self.make_field(name, value)
                inputs[name] = control, self.control_value(control)
                form.addRow(SKILL_FIELDS_BY_NAME[name].label + ":", control)
            forms.addLayout(form, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        layout = QVBoxLayout(dialog)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.restore_size(QSize(max(560, panel.sizeHint().width() + 58), min(540, panel.sizeHint().height() + 60)))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            overrides = self.overrides.get(rank, {})
            for name, (control, initial) in inputs.items():
                if (value := self.control_value(control)) != initial:
                    overrides[name] = value
            if overrides:
                self.overrides[rank] = overrides
        dialog.deleteLater()

    def draft_values(self) -> SkillEdits:
        pending = self.profile_draft()
        for name, control, initial in self.shared:
            value = self.control_value(control)
            if value != initial:
                for rank in self.keys:
                    pending[rank][name] = f"{value} Lv{rank[1]}" if name == "Name" and value else value
        if self.icon_value != self.editor.value(self.first, "Icon"):
            for rank in self.keys:
                pending[rank]["Icon"] = self.icon_value
        for rank, changes in self.overrides.items():
            pending[rank].update(changes)
        return pending

    def profile_draft(self) -> SkillEdits:
        pending = {rank: self.profile_values.get(rank, {}).copy() for rank in self.keys}
        for name, control, initial in self.profile_shared:
            value = self.control_value(control)
            if value != initial:
                for rank in self.keys:
                    pending[rank][name] = value
        for rank, name, control, initial in self.per_rank:
            value = self.control_value(control)
            if value != initial:
                pending[rank][name] = value
                if name in REFLECT_FIELDS and isinstance(control, QCheckBox) and not value:
                    pending[rank]["AbilityType" + name[-1]] = 0
        return pending

    def collect(self) -> SkillEdits:
        edits = {}
        for rank, changes in self.draft_values().items():
            patch = {name: SKILL_FIELDS_BY_NAME[name].parse(str(value)) for name, value in changes.items()
                     if value != self.editor.value(rank, name)}
            if "TargetType" in patch or "TypeEffect" in patch:
                target = patch.get("TargetType", self.editor.value(rank, "TargetType"))
                effect = patch.get("TypeEffect", self.editor.value(rank, "TypeEffect"))
                if target == 8 and effect not in (2, 4):
                    raise ValueError(f"Rank {rank[1]}: GX uses the raid target for healing and buff skills.")
            if patch:
                edits[rank] = patch
        return edits

    def accept(self):
        if not self.editable:
            return
        try:
            self.edits = self.collect()
        except ValueError as error:
            QMessageBox.warning(self, "Skill Editor", str(error))
            return
        super().accept()
