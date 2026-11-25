from dataclasses import replace

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QMessageBox, QVBoxLayout

from .catalog import decode_text
from .equipment_models import BODIES, ModelAssets, armor_choice, armor_pose, basename
from .equipment_preview import AnimatedPreview
from .item_view import find_child
from .itm_catalog import name_at
from .mlt_catalog import MltRecord
from .model_render import PreviewRenderer
from .model_table_view import ModelTableWindow, file_reference_field, reference
from .mon_view import dialog_buttons
from .skill_sheet import SkillSizedDialog

RACE_BY_PREFIX = {body.archetype(female): body.race for body in BODIES for female in (False, True)}
ALPHA_MODES = ("Visibility (Cutout)", "Glow (No Cutout)")


class MltPreview(AnimatedPreview):
    def __init__(self, parent, width=255, height=345):
        super().__init__(width, height, parent)
        self.kind = "armor"
        self.pose = ""

    def set_model(self, names, record, *, reset_angle=False):
        self.model_revision += 1
        if reset_angle:
            self.turn = 0
        self.choice = armor_choice(*names, record.alpha == 0, "", self.pose) if record else None
        self.queue()


class MltEditor(SkillSizedDialog):
    def __init__(self, parent, index, record):
        show_preview = parent.preview_action.isChecked()
        super().__init__(parent, parent.settings, "mlt-editor-preview" if show_preview else "mlt-editor")
        self.setWindowTitle(f"MLT Editor — Model {index}")
        self.original = record
        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        layout.addLayout(body, 1)
        field_column = QVBoxLayout()
        form = QFormLayout()
        field_column.addLayout(form)
        field_column.addStretch()
        body.addLayout(field_column, 1)

        root = parent.preview.renderer.assets.root
        self.texture = file_reference_field(form, self, "DDS File", find_child(root, "DDS"),
                                            "GX textures (*.dds *.DDS *.tga *.TGA)",
                                            name_at(parent.textures, record.texture), self.refresh_preview)
        self.mesh = file_reference_field(form, self, "3DC File", find_child(root, "3DC"),
                                         "GX meshes (*.3dc *.3DC)", name_at(parent.meshes, record.mesh),
                                         self.refresh_preview)
        self.alpha = QComboBox()
        for value, label in enumerate(ALPHA_MODES):
            self.alpha.addItem(label, value)
        if not 0 <= record.alpha < len(ALPHA_MODES):
            self.alpha.addItem(f"{record.alpha} · No Cutout", record.alpha)
        self.alpha.setCurrentIndex(self.alpha.findData(record.alpha))
        form.addRow("Alpha Channel:", self.alpha)

        self.preview = MltPreview(self, 240, 285)
        self.preview.renderer = parent.preview.renderer
        self.preview.pose = parent.preview.pose
        body.addWidget(self.preview)
        self.preview.setVisible(show_preview)
        self.alpha.currentIndexChanged.connect(self.refresh_preview)
        self.refresh_preview()
        dialog_buttons(self, layout)
        self.restore_size(QSize(595, 337) if show_preview else QSize(345, 168))

    def refresh_preview(self, *_):
        record = replace(self.original, alpha=self.alpha.currentData())
        self.preview.set_model((self.mesh.text(), self.texture.text()), record)

    def accept(self):
        parent = self.parent()
        try:
            self.meshes, mesh = reference(parent.meshes, self.original.mesh, self.mesh.text())
            self.textures, texture = reference(parent.textures, self.original.texture, self.texture.text())
        except ValueError as error:
            QMessageBox.warning(self, "MLT Editor", str(error))
            return
        self.result_record = MltRecord(mesh, texture, self.alpha.currentData())
        super().accept()


class MltWindow(ModelTableWindow):
    format_name = "MLT"
    export_extension = ".mlt"
    file_filter = "MLT files (*.mlt *.MLT)"
    editor_type = MltEditor
    initial_size = (765, 455)
    HEADERS = ("Model", "Alpha", "DDS", "3DC", "Version")
    column_widths = (54, 54, 145, 145, 64)
    name_columns = (2, 3)

    def make_preview(self, layout):
        return MltPreview(self)

    def load_preview(self, source, same):
        prefix = source.stem.split("_", 1)[0].casefold()
        if prefix not in RACE_BY_PREFIX:
            prefix = next((candidate for name in self.meshes
                           if (candidate := basename(decode_text(name))[:4].casefold()) in RACE_BY_PREFIX), "")
        root = source.parent
        workspace = self.studio.client_data
        if not find_child(root, "3DC").is_dir() and workspace is not None and prefix:
            root = find_child(find_child(workspace, "Character"), RACE_BY_PREFIX[prefix])
        self.preview.renderer = PreviewRenderer(ModelAssets(root))
        self.preview.pose = armor_pose(prefix)

    def row_values(self, index, record):
        mesh, texture = name_at(self.meshes, record.mesh), name_at(self.textures, record.texture)
        version = self.preview.renderer.assets.skin_version(mesh)
        return index, record.alpha, texture, mesh, version

    def empty_record(self):
        return MltRecord()
