from dataclasses import replace
from struct import pack
import unittest

from gx_items.equipment_models import armor_choice, armor_pose, table_rows
from gx_items.mlt_catalog import MltCatalog, MltRecord


def names(*values):
    return pack("<I", len(values)) + b"".join(pack("<I", len(value)) + value for value in values)


class MltCatalogTest(unittest.TestCase):
    def test_preserves_signature_names_holes_and_opaque_bytes(self):
        original_rows = pack("<9i", -1, -1, 0, 1, 0, 1, 0, 42, -7)
        for signature in (b"ML2", b"MLT"):
            raw = signature + names(b"a.3DC", b"a.3DC") + names(b"ca\xf1a.dds\0", b"unused.dds")
            raw += pack("<I", 3) + original_rows + b"extra"
            catalog = MltCatalog(raw)
            self.assertEqual(catalog.encode(catalog.meshes, catalog.textures, catalog.records), raw)
            self.assertEqual(catalog.records[0], MltRecord())
            self.assertEqual(table_rows(raw, "armor"), (None, ("a.3DC", "caña.dds", False, -1), ("a.3DC", "", False, -1)))
            staged = (catalog.records[0], replace(catalog.records[1], alpha=0), catalog.records[2])
            saved = MltCatalog(catalog.encode(catalog.meshes, catalog.textures, staged))
            self.assertEqual((saved.signature, saved.meshes, saved.textures, saved.records, saved.trailing),
                             (signature, catalog.meshes, catalog.textures, staged, b"extra"))

    def test_rejects_non_native_signature_and_truncated_records(self):
        with self.assertRaises(ValueError):
            MltCatalog(b"MLX" + bytes(12))
        with self.assertRaises(ValueError):
            MltCatalog(b"ML2" + pack("<3i", 0, 0, 2) + bytes(12))

    def test_armor_preview_uses_native_texture_and_vile_pose(self):
        choice = armor_choice("3DC\\hands.3dc", "hands.tga", True, "", armor_pose("vimr"))
        surface, = choice.surfaces
        self.assertEqual((surface.mesh, surface.texture, surface.pose),
                         ("3DC/hands.3dc", "DDS/hands.dds", "ANI/vimr_000_nomal.ANI"))
        self.assertTrue(surface.cutout)
