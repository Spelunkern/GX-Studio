from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel
from PySide6.QtWidgets import QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .pickers import IconPicker
from .skill_catalog import SKILL_FIELDS_BY_NAME, SkillEdits, SkillKey
from .skill_sheet import SkillSizedDialog

NPC_TARGETS = {
    0: "Default", 1: "Field Trap", 2: "Caster", 3: "Selected character", 6: "Characters near caster",
    7: "Characters near target", 10: "Type 10", 11: "Type 11",
}
SUCCESS_MODES = {0: "Default", 1: "Percentage", 2: "Luck", 3: "Wisdom"}
DAMAGE_COLUMNS = (("Damage", "Damage"), ("Add Dmg", "AddDamage"), ("Time Dmg", "TimeDamage"),
                  ("Heal", "Heal"), ("Time Heal", "TimeHeal"))


class MobSkillDialog(SkillSizedDialog):
    def __init__(self, editor, key: SkillKey):
        super().__init__(editor, editor.settings, "npc")
        self.editor = editor
        self.editable = editor.catalog.writable
        self.active = key
        self.edits: SkillEdits = {}
        self.controls = {}
        self.initial = {}
        self.setWindowTitle("Mob Skill Editor")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 10)
        layout.setSpacing(5)
        form = QGridLayout()
        form.setColumnStretch(2, 1)
        self.icon = QPushButton()
        self.icon.setFixedSize(39, 39)
        self.icon.setIconSize(QSize(32, 32))
        self.icon.setAccessibleName("Select NPC skill icon")
        self.icon.setEnabled(self.editable)
        self.icon.setAutoDefault(False)
        self.icon.clicked.connect(self.pick_icon)
        form.addWidget(self.icon, 0, 0, 2, 1)
        for row, (label, name) in enumerate((("Name:", "Name"), ("Spell effect:", "TypeDetail"))):
            form.addWidget(QLabel(label), row, 1)
            form.addWidget(self.control(name), row, 2)
        identifiers = QHBoxLayout()
        identifiers.addWidget(QLabel(f"ID  {key[0]}"))
        identifiers.addSpacing(8)
        self.rank = QComboBox()
        for _, rank in editor.groups[key[0]]:
            self.rank.addItem(str(rank), rank)
        self.rank.setCurrentIndex(self.rank.findData(key[1]))
        self.rank.setEnabled(self.rank.count() > 1)
        self.rank.currentIndexChanged.connect(self.change_rank)
        self.add_labeled(identifiers, "Rank:", self.rank)
        identifiers.addStretch()
        form.addLayout(identifiers, 2, 1, 1, 2)
        layout.addLayout(form)

        layout.addWidget(QLabel("Description:"))
        description = QPlainTextEdit()
        description.setMinimumHeight(66)
        description.setReadOnly(not self.editable)
        self.controls["Description"] = description
        layout.addWidget(description, 1)

        modes = QGridLayout()
        modes.setColumnStretch(1, 1)
        modes.setColumnStretch(3, 1)
        for row, entries in enumerate((
            (("Attack Type:", "TypeAttack"), ("Target(s):", "TargetType")),
            (("Effect:", "TypeEffect"), ("State:", "StateType")),
            (("Element:", "AttrType"), ("Chance:", "SuccessType")),
            (("Damage Type:", "DamageType"), ("Visual ID:", "VisualCode")),
        )):
            for column, (label, name) in enumerate(entries):
                control = self.control(name)
                modes.addWidget(QLabel(label), row, column * 2)
                if name == "SuccessType":
                    chance = QHBoxLayout()
                    chance.addWidget(control, 1)
                    chance.addWidget(self.control("SuccessValue", width=38))
                    modes.addLayout(chance, row, column * 2 + 1)
                else:
                    modes.addWidget(control, row, column * 2 + 1)
        layout.addLayout(modes)

        for entries in (
            (("Spell Delay:", "ReadyTime"), ("Reset Time:", "ResetTime"), ("Keep Time:", "KeepTime")),
            (("Attack Range:", "AttackRange"), ("Apply Range:", "ApplyRange"), ("Multi Attack:", "MultiAttack")),
            (("Time Dmg Type:", "TimeDamageType"), ("Duration Mode:", "FixRange")),
        ):
            fields = QHBoxLayout()
            for label, name in entries:
                self.add_labeled(fields, label, self.control(name, width=40 if name != "FixRange" else 140))
            fields.addStretch()
            layout.addLayout(fields)

        self.form_row = QWidget()
        transforms = QHBoxLayout(self.form_row)
        transforms.setContentsMargins(0, 0, 0, 0)
        self.add_labeled(transforms, "Form:", self.control("ChangeType"))
        self.add_labeled(transforms, "Monster ID:", self.control("ChangeLevel"))
        transforms.addStretch()
        layout.addWidget(self.form_row)

        damage = QGridLayout()
        damage.addWidget(QLabel("Damage / Heal:"), 0, 0)
        for row, pool in enumerate(("HP", "SP", "MP"), 1):
            damage.addWidget(QLabel(pool + ":"), row, 0, alignment=Qt.AlignmentFlag.AlignRight)
        for col, (label, prefix) in enumerate(DAMAGE_COLUMNS, 1):
            damage.addWidget(QLabel(label), 0, col, alignment=Qt.AlignmentFlag.AlignHCenter)
            for row, pool in enumerate(("HP", "SP", "MP"), 1):
                damage.addWidget(self.control(prefix + pool), row, col, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(damage)

        layout.addWidget(QLabel("Abilities:"))
        abilities = QGridLayout()
        for index in range(1, 4):
            field_type = self.control(f"AbilityType{index}")
            amount = self.control(f"AbilityValue{index}")
            abilities.addWidget(QLabel(f"{index}."), index - 1, 0)
            abilities.addWidget(field_type, index - 1, 1)
            abilities.addWidget(amount, index - 1, 2)
        abilities.setColumnStretch(3, 1)
        layout.addLayout(abilities)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.editable)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load_rank()
        self.restore_size(QSize(540, self.minimumSizeHint().height() + 24))

    def control(self, name, *, width=0):
        choices = {"TargetType": NPC_TARGETS, "SuccessType": SUCCESS_MODES}.get(name)
        control = self.make_field(name, self.editor.value(self.active, name), width=width, choices=choices)
        self.controls[name] = control
        if name in ("TypeDetail", "SuccessType", "TypeEffect", "ChangeType"):
            control.currentIndexChanged.connect(self.update_modes)
        return control

    def load_rank(self):
        draft = self.edits.get(self.active, {})
        for name, control in self.controls.items():
            value = draft.get(name, self.editor.value(self.active, name))
            if isinstance(control, QComboBox):
                if (index := control.findData(value)) < 0:
                    if name.startswith("AbilityType"):
                        control.setPlaceholderText(f"Legacy ({value})")
                        control.setProperty("legacyValue", value)
                    else:
                        control.addItem(str(value), value)
                        index = control.count() - 1
                control.setCurrentIndex(index)
            elif isinstance(control, QPlainTextEdit):
                control.setPlainText(str(value))
            elif SKILL_FIELDS_BY_NAME[name].size:
                control.setValue(value)
            else:
                control.setText(str(value))
            self.initial[name] = self.control_value(control)
        self.icon_value = draft.get("Icon", self.editor.value(self.active, "Icon"))
        self.set_icon(self.icon_value)
        self.update_modes()

    @staticmethod
    def control_value(control):
        if isinstance(control, QPlainTextEdit):
            return control.toPlainText()
        return SkillSizedDialog.control_value(control)

    def update_modes(self, *_):
        if "ChangeLevel" not in self.controls:
            return
        success = self.controls["SuccessType"].currentData()
        self.controls["SuccessValue"].setEnabled(self.editable and bool(success))
        family = self.controls["TypeDetail"].currentData()
        self.form_row.setVisible(family == 90)
        effect = self.controls["TypeEffect"].currentData()
        form = self.controls["ChangeType"].currentData()
        self.controls["ChangeLevel"].setEnabled(self.editable and (effect == 3 and form == 101 or
                                                                  effect == 4 and form in (10, 11, 12)))

    def set_icon(self, value):
        self.icon_value = value
        self.icon.setIcon(QIcon(self.editor.icons.pixmap(value)))

    def pick_icon(self):
        icons = self.editor.available_icons()
        if icons:
            picker = IconPicker(self.icon, self.icon_value, icons, 16)
            picker.iconSelected.connect(self.set_icon)
            picker.show()

    def collect_rank(self) -> bool:
        patch = self.edits.get(self.active, {}).copy()
        try:
            for name, control in self.controls.items():
                value = self.control_value(control)
                if value != self.initial[name]:
                    value = SKILL_FIELDS_BY_NAME[name].parse(str(value))
                    if value == self.editor.value(self.active, name):
                        patch.pop(name, None)
                    else:
                        patch[name] = value
        except ValueError as error:
            QMessageBox.warning(self, "Mob Skill Editor", str(error))
            return False
        if self.icon_value != self.editor.value(self.active, "Icon"):
            patch["Icon"] = self.icon_value
        else:
            patch.pop("Icon", None)
        if patch:
            self.edits[self.active] = patch
        else:
            self.edits.pop(self.active, None)
        return True

    def change_rank(self):
        if self.collect_rank():
            self.active = self.active[0], self.rank.currentData()
            self.load_rank()
        else:
            self.rank.blockSignals(True)
            self.rank.setCurrentIndex(self.rank.findData(self.active[1]))
            self.rank.blockSignals(False)

    def accept(self):
        if self.editable and self.collect_rank():
            super().accept()
