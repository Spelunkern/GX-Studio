from dataclasses import dataclass
from functools import cached_property
from struct import unpack_from

from PySide6.QtGui import QPixmap

from . import seed
from .catalog import Cursor, decode_text, read_file
from .equipment_models import ModelAssets

SKILL_BYTES = 116
SKILL_RANKS = 9


@dataclass(frozen=True, slots=True)
class NPCSkill:
    name: str
    description: str
    icon: int
    effect: int
    success_type: int
    success_value: int


def read_npc_skills(raw: bytes) -> dict[int, NPCSkill]:
    plain, _ = seed.decode(raw)
    cursor = Cursor(plain)
    groups = cursor.word()
    if groups > min(65535, (len(plain) - 4) // (SKILL_RANKS * (SKILL_BYTES + 8))):
        raise ValueError("Invalid NPC skill group count.")
    skills = {}
    for skill_id in range(1, groups + 1):
        for _ in range(SKILL_RANKS):
            name, description = cursor.string(), cursor.string()
            record = cursor.take(SKILL_BYTES)
            if skill_id <= 255 and record[0] == 1:
                title = " ".join(decode_text(name).split())
                if title and not title.startswith("?"):
                    skills[skill_id] = NPCSkill(
                        title, decode_text(description).strip(), unpack_from("<H", record, 1)[0],
                        record[21], record[54], record[55],
                    )
    if cursor.position != len(plain):
        raise ValueError("Unexpected bytes after the NPC skill catalog.")
    return skills


def skill_icon_location(icon: int) -> tuple[str, int, int] | None:
    sheet, slot = divmod(icon, 1000)
    if not 1 <= slot <= 256:
        return None
    filename = "icon_skill.png" if sheet == 0 else f"icon_skill{sheet + 1}_dds.png"
    row, col = divmod(slot - 1, 16)
    return filename, row, col


class NPCSkillAssets:
    def __init__(self, assets: ModelAssets | None = None):
        self.assets = assets
        self.sheets: dict[str, QPixmap] = {}

    @cached_property
    def skills(self) -> dict[int, NPCSkill]:
        path = self.assets.resolve("Monster/NpcSkill.SData") if self.assets else None
        try:
            return read_npc_skills(read_file(path)) if path else {}
        except (OSError, ValueError):
            return {}

    def pixmap(self, icon: int) -> QPixmap:
        location = skill_icon_location(icon)
        if location is None or self.assets is None:
            return QPixmap()
        filename, row, col = location
        if filename not in self.sheets:
            path = self.assets.resolve(f"interface/icon/{filename}")
            self.sheets[filename] = QPixmap(str(path)) if path else QPixmap()
        atlas = self.sheets[filename]
        if atlas.isNull():
            return QPixmap()
        width, height = atlas.width() // 16, atlas.height() // 16
        return atlas.copy(col * width, row * height, width, height)
