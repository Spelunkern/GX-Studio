from dataclasses import replace
from math import isfinite
from re import match
from struct import error as StructError, pack

from PySide6.QtCore import QSignalBlocker, QSize
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit
from PySide6.QtWidgets import QMessageBox, QSpinBox, QVBoxLayout, QWidget

from .equipment_models import GLOW_NAMES, WEAPON_TYPES, ModelAssets, ModelChoice, Surface, basename, dds_name
from .equipment_preview import AnimatedPreview
from .item_view import find_child
from .itm_catalog import DEFAULT_EXTENDED, EMPTY_BINDINGS, EXTENDED, ItmRecord, name_at
from .model_table_view import ModelTableWindow, file_reference_field, reference
from .mon_view import dialog_buttons
from .skill_layout import WEAPONS
from .skill_sheet import SkillSizedDialog
from .weapon_preview import WeaponPreviewRenderer, enchant_record, weapon_family

WEAPON_NAMES = {**dict(enumerate(WEAPONS[:15], 1)), 9: "Reverse Dagger", 19: "Shield"}


def table_type(path):
    number = match(r"^([0-9]{1,3})(?=$|[-_. ])", path.stem)
    return int(number[1]) if number and int(number[1]) in WEAPON_TYPES else 0


class ItmPreview(AnimatedPreview):
    def __init__(self, parent, width=270, height=360):
        super().__init__(width, height, parent)
        self.item_type = 0
        self.kind = "weapon"
        self.enchant = QComboBox()
        self.enchant.setAccessibleName("Enchant Preview")
        self.enchant.currentIndexChanged.connect(self.restart_effects)
        self.show_fx = QCheckBox("Show Effects", checked=True)
        self.show_fx.toggled.connect(self.restart_effects)
        self.controls = QWidget()
        options = QFormLayout(self.controls)
        options.setContentsMargins(0, 0, 0, 0)
        options.addRow(self.show_fx)
        options.addRow("Enchant Preview:", self.enchant)
        self.layout().insertWidget(1, self.controls)
        self.controls.hide()

    def set_family(self, item_type, level=None):
        if level is None:
            level = self.enchant.currentData() or 0
        self.item_type = item_type
        self.kind = "shield" if WEAPON_TYPES.get(item_type) == 19 else "weapon"
        with QSignalBlocker(self.enchant):
            self.enchant.clear()
            self.enchant.addItem("+0", 0)
            if weapon_family(item_type):
                for tier in range(5, 11):
                    self.enchant.addItem(f"+{tier}  ·  SEFF #{enchant_record(item_type, tier)}", tier)
            self.enchant.setCurrentIndex(max(0, self.enchant.findData(level)))
        self.controls.setVisible(bool(self.choice and weapon_family(self.item_type)))
        self.restart_effects()

    def set_model(self, names, record, *, reset_angle=False):
        self.model_revision += 1
        if reset_angle:
            self.turn = 0
        mesh, texture = map(basename, names)
        self.choice = (
            ModelChoice(mesh, (Surface(f"3DO/{mesh}", f"DDS/{dds_name(texture)}", "rigid", record.cutout),),
                        record.glow) if record and mesh and texture else None
        )
        self.controls.setVisible(bool(self.choice and weapon_family(self.item_type)))
        self.restart_effects()

    def effect_options(self):
        return self.item_type, self.enchant.currentData() or 0, self.show_fx.isChecked()


class ItmEditor(SkillSizedDialog):
    def __init__(self, parent, index, record):
        super().__init__(parent, parent.settings, "itm-editor")
        self.setWindowTitle(f"ITM Editor — Model {index}")
        self.window = parent
        self.original = record
        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        layout.addLayout(body, 1)
        form_column = QVBoxLayout()
        form = QFormLayout()
        form_column.addLayout(form)
        form_column.addStretch()
        body.addLayout(form_column, 1)

        root = parent.preview.renderer.assets.root
        self.input = [
            file_reference_field(form, self, title, find_child(root, folder), filters, name_at(names, file_index),
                                 self.refresh_preview)
            for title, folder, names, file_index, filters in (
                ("DDS File", "DDS", parent.textures, record.texture, "GX textures (*.dds *.DDS *.tga *.TGA)"),
                ("3DO File", "3DO", parent.meshes, record.mesh, "GX meshes (*.3do *.3DO)"),
            )
        ]

        self.glow = QComboBox()
        for value, title in enumerate(("None", *GLOW_NAMES), -1):
            self.glow.addItem(title, value)
        glow = self.glow.findData(record.glow)
        if glow < 0:
            self.glow.addItem(str(record.glow), record.glow)
            glow = self.glow.count() - 1
        self.glow.setCurrentIndex(glow)
        form.addRow("Glow:", self.glow)
        self.parameter = self.signed(record.parameter)
        self.parameter2 = self.signed(record.parameter2)
        self.parameter2_label = QLabel()
        form.addRow("Parameter:", self.parameter)
        form.addRow(self.parameter2_label, self.parameter2)

        self.extended = QCheckBox("Extended", checked=bool(record.variant))
        form.addRow("Format:", self.extended)
        rgba, rotation, scale, alpha_flag = EXTENDED.unpack(record.extended or DEFAULT_EXTENDED)
        self.color = QLineEdit(f"{rgba:08X}")
        self.rotation = QLineEdit(f"{rotation:.9g}")
        self.scale = QLineEdit(f"{scale:.9g}")
        self.extended_alpha = self.signed(alpha_flag)
        self.original_extended = (self.color.text(), self.rotation.text(), self.scale.text(), alpha_flag)
        self.extras = QWidget()
        extended_form = QFormLayout(self.extras)
        extended_form.setContentsMargins(0, 0, 0, 0)
        extended_form.addRow("Rotation:", self.rotation)
        extended_form.addRow("Scale:", self.scale)
        extended_form.addRow("RGBA:", self.color)
        extended_form.addRow("Alpha Test Flag:", self.extended_alpha)
        form.addRow(self.extras)

        self.preview = ItmPreview(self, 246, 330)
        self.preview.renderer = parent.preview.renderer
        self.preview.set_family(parent.preview.item_type, parent.preview.enchant.currentData() or 0)
        self.preview.show_fx.setChecked(parent.preview.show_fx.isChecked())
        body.addWidget(self.preview)
        self.preview.setVisible(parent.preview_action.isChecked())
        self.extended.toggled.connect(self.update_extended)
        self.glow.currentIndexChanged.connect(self.refresh_preview)
        self.parameter2.valueChanged.connect(self.refresh_preview)
        self.extended_alpha.valueChanged.connect(self.refresh_preview)
        self.update_extended()
        dialog_buttons(self, layout)
        self.restore_size(QSize(630 if parent.preview_action.isChecked() else 340, 428))

    @staticmethod
    def signed(value):
        return QSpinBox(minimum=-(1 << 31), maximum=(1 << 31) - 1, value=value)

    def update_extended(self, *_):
        enabled = self.extended.isChecked()
        self.extras.setEnabled(enabled)
        self.parameter2_label.setText("Parameter 2:" if enabled else "Alpha Test Flag:")
        self.refresh_preview()

    def refresh_preview(self, *_):
        # Geometry uses the native cutout flag. Extended rotation/scale belong
        # to the record and are retained even when GX's weapon draw ignores them.
        flag = self.extended_alpha.value() if self.extended.isChecked() else self.parameter2.value()
        record = replace(self.original, glow=self.glow.currentData(), variant=0, parameter2=flag)
        self.preview.set_model((self.input[1].text(), self.input[0].text()), record)

    def accept(self):
        try:
            self.meshes, mesh = reference(self.window.meshes, self.original.mesh, self.input[1].text())
            self.textures, texture = reference(self.window.textures, self.original.texture, self.input[0].text())
            extended = b""
            if self.extended.isChecked():
                fields = (self.color.text(), self.rotation.text(), self.scale.text(), self.extended_alpha.value())
                if fields == self.original_extended:
                    extended = self.original.extended or DEFAULT_EXTENDED
                else:
                    original = self.original.extended or DEFAULT_EXTENDED
                    rgba = original[:4] if fields[0] == self.original_extended[0] else int(fields[0], 16).to_bytes(4, "little")
                    values = [rgba]
                    for index in (1, 2):
                        if fields[index] == self.original_extended[index]:
                            values.append(original[index * 4:(index + 1) * 4])
                        else:
                            value = float(fields[index])
                            if not isfinite(value):
                                raise ValueError("Rotation and scale must be finite.")
                            values.append(pack("<f", value))
                    values.append(fields[3].to_bytes(4, "little", signed=True))
                    extended = b"".join(values)
            self.result_record = replace(
                self.original, mesh=mesh, texture=texture, glow=self.glow.currentData(),
                parameter=self.parameter.value(), variant=int(self.extended.isChecked()),
                parameter2=self.parameter2.value(), extended=extended,
            )
        except (ValueError, OverflowError, StructError) as error:
            QMessageBox.warning(self, "ITM Editor", str(error))
            return
        super().accept()


class ItmWindow(ModelTableWindow):
    format_name = "ITM"
    file_filter = "Item model tables (*.itm *.ITM *.it2 *.IT2)"
    editor_type = ItmEditor
    initial_size = (1080, 510)
    HEADERS = ("Model", "DDS", "3DO", "Glow", "Parameter", "Alpha Test Flag", "Format", "Parameter 2", "RGBA", "Rot°", "Scale")
    column_widths = (52, 105, 105, 48, 72, 88, 54, 78, 76, 42, 49)
    name_columns = (1, 2)

    def make_preview(self, layout):
        self.family = QComboBox()
        self.family.addItem("Select Weapon Family…", 0)
        for number, name in WEAPON_NAMES.items():
            self.family.addItem(name, number)
        self.family.currentIndexChanged.connect(lambda: self.preview.set_family(self.family.currentData()))
        layout.addWidget(self.family)
        return ItmPreview(self)

    def load_preview(self, source, same):
        item_type = table_type(source)
        self.family.setVisible(not item_type)
        if not item_type and same:
            item_type = self.family.currentData()
        with QSignalBlocker(self.family):
            self.family.setCurrentIndex(max(0, self.family.findData(WEAPON_TYPES.get(item_type, 0))))
        effect_root = source.parent.parent if source.parent.name.casefold() == "item" else source.parent
        item_root = source.parent
        workspace = self.studio.client_data
        if workspace is not None:
            if not find_child(item_root, "3DO").is_dir() and not find_child(item_root, "DDS").is_dir():
                item_root = find_child(workspace, "Item")
            if not find_child(effect_root, "Effect").is_dir():
                effect_root = workspace
        self.preview.renderer = WeaponPreviewRenderer(ModelAssets(item_root), effect_root)
        self.preview.set_family(item_type)

    def row_values(self, index, record):
        rgba, rotation, scale = EXTENDED.unpack(record.extended)[:3] if record.variant else (None, None, None)
        return (
            index, name_at(self.textures, record.texture), name_at(self.meshes, record.mesh), record.glow,
            record.parameter, record.alpha_flag, record.variant, record.parameter2 if record.variant else "",
            f"{rgba:08X}" if rgba is not None else "", f"{rotation:.6g}" if rotation is not None else "",
            f"{scale:.6g}" if scale is not None else "",
        )

    def empty_record(self):
        return ItmRecord(bindings=EMPTY_BINDINGS if self.catalog.signature == b"IT2" else b"")

    def new_record(self):
        record = self.empty_record()
        index = self.selected()
        return replace(record, bindings=self.records[index].bindings) if record.bindings and index is not None else record
