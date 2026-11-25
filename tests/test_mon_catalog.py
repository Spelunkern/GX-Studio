from dataclasses import replace
from pathlib import Path
from struct import pack
from tempfile import TemporaryDirectory
import unittest

from gx_items.equipment_models import mon_model_choice
from gx_items.mon_catalog import ABSENT, MonCatalog, save_mon, slot_text, text_bytes


def mon_bytes(signature, attachment=b"hook.EFT"):
    def string(value):
        return pack("<I", len(value)) + value

    animations = [b"LOAD"] * 9
    animations[6] = b"creature.ANI"
    slots = [*animations, *([b"load"] * 4), *([b"LOAD"] * 4)]
    if signature == b"MO4":
        slots.append(attachment)
    return b"".join((
        signature, pack("<I", 1), string(b"Cr\xe9ature\0"), b"\x99",
        *(string(value) for value in slots), pack("<I", 1), string(b"Creature.3DC"),
        string(b"Creature.TGA"), pack("<fIii", 2.25, 1, -7, 56),
    ))


class MonCatalogTests(unittest.TestCase):
    def test_edits_keep_native_slots_and_legacy_text_bytes(self):
        initial = MonCatalog(mon_bytes(b"MO2"))
        record, = initial.records
        self.assertEqual(record.display_name, "Créature")
        self.assertEqual(text_bytes(record.display_name, record.name), b"Cr\xe9ature\0")
        self.assertEqual(slot_text(b"load"), "")
        self.assertEqual(text_bytes("", b"load", slot=True), b"load")
        self.assertEqual(record.effects[-1], ABSENT)
        self.assertEqual(initial.encode(initial.records, b"MO2"), initial.raw)
        changed = replace(record, height_bytes=record.edit_height("2.75"), flags=22)
        saved, = MonCatalog(initial.encode((changed,), b"MO2")).records
        self.assertEqual(saved, changed)
        surface, = mon_model_choice(saved, "Npc", "npc.MON").surfaces
        self.assertEqual((surface.mesh, surface.texture, surface.pose),
                         ("Npc/3DC/Creature.3DC", "Npc/DDS/Creature.dds", "Npc/ANI/creature.ANI"))

    def test_format_conversion_only_changes_the_attachment_slot(self):
        original = MonCatalog(mon_bytes(b"MO4"))
        parsed = MonCatalog(original.encode(original.records, b"MO2"))
        expected = replace(original.records[0], effects=(ABSENT,) * 5)
        self.assertEqual(parsed.records, (expected,))
        restored = MonCatalog(parsed.encode(parsed.records, b"MO4"))
        self.assertEqual(restored.records, (expected,))
        self.assertEqual(restored.signature, b"MO4")

    def test_truncated_and_trailing_bytes_are_rejected(self):
        reference = mon_bytes(b"MO2")
        for raw in (reference[:40], reference[:-1], reference + b"\0"):
            with self.subTest(raw_length=len(raw)), self.assertRaises(ValueError):
                MonCatalog(raw)

    def test_save_verifies_backup_and_rejects_external_changes(self):
        reference = mon_bytes(b"MO2")
        catalog = MonCatalog(reference)
        draft = (replace(catalog.records[0], name="Modelo nuevo".encode("utf-8")),)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "npc.MON"
            source.write_bytes(reference)
            result, backup = save_mon(source, catalog, draft, b"MO4", root / "backups")
            self.assertEqual(backup.read_bytes(), reference)
            self.assertEqual(MonCatalog.open(source).records, draft)
            self.assertEqual(source.read_bytes(), result.raw)
            with self.assertRaisesRegex(ValueError, "changed on disk"):
                save_mon(source, catalog, draft, b"MO4", root / "backups")
            self.assertEqual(source.read_bytes(), result.raw)
