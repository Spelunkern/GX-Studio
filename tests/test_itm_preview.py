from dataclasses import replace
from struct import pack
import unittest

import numpy as np

from gx_items.effect_preview import EffectMaterial, draw_billboard
from gx_items.itm_catalog import BINDINGS_SIZE, DEFAULT_EXTENDED, ItmCatalog
from gx_items.weapon_preview import enchant_record, read_seff


def name(raw):
    return pack("<I", len(raw)) + raw


class ItmPreviewTest(unittest.TestCase):
    def test_it2_round_trip_preserves_bindings_and_native_material_flag(self):
        bindings = bytes(range(256)) * (BINDINGS_SIZE // 256)
        row = pack("<6i", 0, 0, 1, -8, 1, -7) + DEFAULT_EXTENDED + bindings
        data = b"IT2" + pack("<I", 1) + name(b"modelo.3do") + pack("<I", 1) + name(b"ca\xf1a.dds") + pack("<I", 1) + row
        catalog = ItmCatalog(data)
        original = catalog.records[0]
        self.assertTrue(original.cutout)
        self.assertEqual(original.bindings, bindings)
        self.assertEqual(catalog.encode(catalog.meshes, catalog.textures, catalog.records), data)
        edited = replace(original, glow=3)
        output = catalog.encode(catalog.meshes, catalog.textures, (edited,))
        loaded = ItmCatalog(output)
        self.assertEqual(loaded.records, (edited,))
        self.assertEqual(loaded.textures, (b"ca\xf1a.dds",))
        self.assertEqual(output[:-BINDINGS_SIZE - 32], data[:-BINDINGS_SIZE - 32])
        with self.assertRaises(ValueError):
            ItmCatalog(data[:-1])

    def test_enchant_selects_by_normalized_family_and_tier(self):
        self.assertIsNone(enchant_record(1, 4))
        self.assertIsNone(enchant_record(1, 50))
        self.assertIsNone(enchant_record(19, 10))
        self.assertIsNone(enchant_record(34, 10))
        self.assertEqual(enchant_record(1, 5), 1)
        self.assertEqual(enchant_record(57, 5), 49)
        self.assertEqual(enchant_record(50, 10), enchant_record(5, 49))
        self.assertEqual(enchant_record(65, 10), 90)

    def test_seff_versions_keep_utf16_texture_and_emitter_fields(self):
        texture = "éclair.dds\0".encode("utf-16-le")
        layer = pack("<IfIIIfI", 20, .5, 0, 3, 500, .4, len(texture) // 2) + texture
        layer += pack("<3B5fB3f", 10, 20, 200, 0, 1, 2, 5, .1, 1, .5, .1, .2)
        for version in (5, 6, 7):
            raw = pack("<i", version) + bytes(12) + pack("<3i", 1, 49, 1) + layer
            if version > 5:
                raw += pack("<i", -999)
            emitter, = read_seff(raw)[49]
            self.assertEqual((emitter.rate, emitter.shape, emitter.texture, emitter.origin, emitter.color),
                             (20, 3, "éclair.dds", (0, 1, 2), (10, 20, 200)))
            self.assertTrue(emitter.supported())
            with self.assertRaises(ValueError):
                read_seff(raw[:-1])

    def test_billboard_respects_existing_depth_and_does_not_write_depth(self):
        pixels = np.zeros((8, 8, 3), dtype=np.uint8)
        depth = np.zeros((8, 8), dtype=np.float32)
        depth[2, 2] = 2
        projected = np.array(((1, 7), (7, 7), (7, 1), (1, 1)), dtype=np.float32)
        texture = np.array([[[200, 100, 50, 255]]], dtype=np.uint8)
        material = EffectMaterial(np.array((1, 1, 1, .5)), 4, 1, True, camera_facing=True)
        draw_billboard(pixels, depth, projected, 1, material, texture)
        np.testing.assert_array_equal(pixels[2, 2], (0, 0, 0))
        np.testing.assert_array_equal(pixels[3, 2], (100, 50, 25))
        self.assertEqual(depth[3, 2], 0)
