from collections.abc import Callable
from html import escape
import re

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QStyle, QStyledItemDelegate
from PySide6.QtWidgets import QStyleOptionComboBox, QStyleOptionViewItem, QStylePainter, QToolTip

from .consumable_fields import REUSE_SECONDS, spell_group_label
from .lapis_fields import NPC_EFFECT_NAMES, choices_for_lapis, cost_label
from .npc_skills import NPCSkillAssets

# GX's ItemNameRGB palette. The index is the low byte of ReqDex.
NAME_COLORS = (
    ("White (Default)", "#ffffff"), ("Light Blue", "#80ffff"), ("Dark Blue", "#0080ff"),
    ("Green", "#00ff40"), ("Yellow", "#ffff00"), ("Orange", "#ff8000"),
    ("Light Purple", "#c8c8ff"), ("Magenta", "#ff00ff"), ("Dark Purple", "#8000ff"),
    ("Light Pink", "#ff80ff"),
)


def draw_color_text(painter, rect, text, color):
    if not text or color is None:
        return
    font = painter.fontMetrics()
    baseline = rect.top() + (rect.height() + font.ascent() - font.descent()) / 2
    glyphs = QPainterPath()
    glyphs.addText(rect.left() + 5, baseline, painter.font(), text)
    outline = QPen(QColor("#000000"), 1.5)
    outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.save()
    painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.strokePath(glyphs, outline)
    painter.fillPath(glyphs, color)
    painter.restore()


class NameColorDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        row = QStyleOptionViewItem(option)
        self.initStyleOption(row, index)
        text, row.text = row.text, ""
        style = row.widget.style() if row.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, row, painter, row.widget)
        draw_color_text(painter, row.rect, text, index.data(Qt.ItemDataRole.ForegroundRole))


class NameColorCombo(QComboBox):
    def __init__(self, current: int | None):
        super().__init__()
        self.setAccessibleName("Name color")
        self.setItemDelegate(NameColorDelegate(self))
        for code, (name, color) in enumerate(NAME_COLORS):
            # GX uses the same color for 8 and 10. Preserve 10 in existing items.
            self.addItem(name, 10 if code == 8 and current == 10 else code)
            self.setItemData(code, QColor(color), Qt.ItemDataRole.ForegroundRole)
        index = self.findData(current) if current is not None else -1
        if index < 0:
            index = self.count()
            self.addItem("Unset" if current is None else f"{current} · Original", current)
            native = current & 0xFF if current is not None else 0
            if native == 10:
                native = 8
            color = NAME_COLORS[native][1] if native < len(NAME_COLORS) else NAME_COLORS[0][1]
            self.setItemData(index, QColor(color), Qt.ItemDataRole.ForegroundRole)
        self.setCurrentIndex(index)

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self,
        )
        draw_color_text(painter, rect, option.currentText, self.currentData(Qt.ItemDataRole.ForegroundRole))


class ForgeCostCombo(QComboBox):
    def __init__(self, family: int, current: int | None):
        super().__init__()
        self.setAccessibleName("Forge cost")
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for code, label in choices_for_lapis(family, "ReqIg"):
            self.addItem(label, code)
        index = self.findData(current) if current is not None else -1
        if index < 0:
            index = self.count()
            label = "Unset" if current is None else f"{cost_label(family, current)} (#{current})"
            self.addItem(label, current)
        self.setCurrentIndex(index)
        # Calculate after populating the longest price, before the form is fixed.
        width = self.sizeHint().width() + 10
        self.setMinimumWidth(width)
        self.view().setMinimumWidth(width)
        self.view().setTextElideMode(Qt.TextElideMode.ElideNone)


class SpellGroupCombo(QComboBox):
    def __init__(self, current: int | None):
        super().__init__()
        self.setAccessibleName("Spell group")
        for group in REUSE_SECONDS:
            self.addItem(spell_group_label(group), group)
        index = self.findData(current) if current is not None else -1
        if index < 0:
            index = self.count()
            self.addItem("Unset" if current is None else spell_group_label(current), current)
        self.setCurrentIndex(index)


class DebuffIcon(QLabel):
    def enterEvent(self, event):
        if self.toolTip():
            QToolTip.showText(self.mapToGlobal(QPoint(self.width() + 4, self.height())), self.toolTip(), self, self.rect())
        super().enterEvent(event)


class DebuffCombo(QComboBox):
    def __init__(
        self, current: int | None, assets: NPCSkillAssets | None = None,
        fallback_icon: Callable[[int], QPixmap] | None = None,
    ):
        super().__init__()
        self.assets = assets or NPCSkillAssets()
        self.fallback_icon = fallback_icon
        self.preview = DebuffIcon()
        self.preview.setFixedSize(26, 26)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAccessibleName("Debuff")
        names = dict(NPC_EFFECT_NAMES)
        for code, skill in self.assets.skills.items():
            if code != 16 and skill.effect in (1, 3):
                old = names.get(code, "")
                rank = old.rpartition(" Lv ")[2]
                names[code] = f"{skill.name} Lv {rank}" if rank.isdecimal() else skill.name
        for code, name in sorted(names.items()):
            self.addItem(f"{code} · {name}", code)
            if code:
                _, tip = self.describe(code)
                self.setItemData(self.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        index = self.findData(current) if current is not None else self.findData(0)
        if index < 0:
            index = self.count()
            self.addItem(f"{current} · Original", current)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setValidator(QIntValidator(0, 255, self))
        self.lineEdit().setPlaceholderText("NPC skill ID")
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(16)
        self.setCurrentIndex(index)
        if current is None:
            self.setEditText("")
        self.currentTextChanged.connect(self.update_preview)
        self.update_preview()

    def value(self) -> str:
        if self.currentText() == self.itemText(self.currentIndex()) and self.currentData() is not None:
            return str(self.currentData())
        return self.currentText().strip()

    def describe(self, code: int) -> tuple[str, str]:
        skill = self.assets.skills.get(code) if code != 16 else None
        title = skill.name if skill else NPC_EFFECT_NAMES.get(code, "NPC skill")
        lines = [f"<b>{escape(title)} · {code}</b>"]
        if skill is not None:
            description = re.sub(r"\{/?c\d*\}", "", skill.description).replace(r"\n", "\n")
            if description:
                lines.append(escape(description).replace("\n", "<br>"))
            if skill.effect == 3 and skill.success_type in (1, 2, 3):
                lines.append(f"Base chance: {skill.success_value}%")
        elif code == 16:
            lines.append("Applies the lapis HP, SP and MP bonuses to recovery.")
        else:
            lines.append("This NPC skill is missing from the client catalog.")
        return title, "<br>".join(lines)

    def update_preview(self, *_):
        text = self.value()
        if not text.isascii() or not text.isdecimal() or len(text) > 3 or not 1 <= int(text) <= 255:
            self.preview.clear()
            self.preview.setAccessibleName("")
            self.set_effect_tooltip("")
            return
        code = int(text)
        title, tip = self.describe(code)
        skill = self.assets.skills.get(code) if code != 16 else None
        pixmap = self.assets.pixmap(skill.icon) if skill else QPixmap()
        if pixmap.isNull() and self.fallback_icon is not None:
            pixmap = self.fallback_icon(code)
        if pixmap.isNull():
            pixmap = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation).pixmap(24, 24)
        self.preview.setPixmap(pixmap.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio))
        self.preview.setAccessibleName(f"{title} · {code}")
        self.set_effect_tooltip(tip)

    def set_effect_tooltip(self, tip: str):
        self.setToolTip(tip)
        self.lineEdit().setToolTip(tip)
        self.preview.setToolTip(tip)
