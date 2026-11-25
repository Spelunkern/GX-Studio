from pathlib import Path
from struct import pack
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image

from gx_items.equipment_fields import choices_for, editable_fields, rows_for
from gx_items.equipment_models import ModelAssets, ModelFormatError, Surface, table_rows
from gx_items.model_render import BACKGROUND, PreparedSurface, PreviewRenderer, camera_axes, draw_triangles, pose_cloth


def name(text):
    data = text.encode("utf-8") + b"\0"
    return pack("<I", len(data)) + data


class ModelPreviewTest(unittest.TestCase):
    def test_mount_projection_keeps_the_authored_outer_face(self):
        world_up = np.array((0, 1, 0))
        for turn in (0, 90, 180, 270):
            right, up, outward = camera_axes("mount", turn, np.ones(3)).T
            np.testing.assert_allclose(right, np.cross(world_up, -outward) / np.linalg.norm(np.cross(world_up, outward)))
            self.assertLess(float(np.cross(right, up) @ outward), 0)

    def test_fixed_equipment_profiles_use_gx_field_meanings(self):
        weapon, shield, chest, cape, mount = (editable_fields(family) for family in (1, 19, 17, 24, 42))
        self.assertTrue({"Attack", "AttackAdd", "Attribute", "Range"} <= weapon)
        self.assertTrue({"Def", "Resist"}.issubset(shield) and "Attack" not in shield)
        self.assertTrue({"ReqVG", "Attribute"} <= chest)
        self.assertFalse({"ReqVG", "Attribute"} & cape)
        self.assertTrue({"Range", "AttackTime", "Speed", "ReqIg"} <= mount)
        self.assertNotIn("Model", mount)
        for fields in (weapon, shield, chest, cape, mount, editable_fields(22)):
            self.assertFalse({"Count", "Drop", "Quality", "Special"} & fields)
        self.assertIn("ReqLuc", weapon)
        self.assertEqual(tuple(code for code, _ in choices_for(42, "AttackTime")), (1, 2))
        tiers = dict(choices_for(1, "AttackTime"))
        self.assertEqual((tiers[1], tiers[5], tiers[9]), ("Ext. Slow", "Normal", "Ext. Fast"))
        self.assertIn(("Max atk +", "AttackAdd"), rows_for(22)[0][1][0])

    def test_cloak_pose_anchors_collar_and_distributes_offset_into_cloth(self):
        rest = np.array([(x, -y, 0) for y in range(5) for x in range(3)], dtype=np.float32)
        pins = ((0, 0, (0, 0, -.3)), (1, 0, (1, 0, 0)), (2, 0, (2, 0, .3)))
        surface = Surface("cape.3dc", "", "cloth", pins=pins, cloth_grid=(3, 5))
        posed = pose_cloth(rest, surface, np.eye(4, dtype=np.float32)[None, ...])
        np.testing.assert_allclose(posed[:3], [pin[2] for pin in pins])
        self.assertLess(posed[3, 2], 0)
        self.assertLess(abs(posed[-3, 2]), abs(posed[3, 2]))
        np.testing.assert_array_equal(posed[:, :2], rest[:, :2])
        self.assertTrue((rest[:, 2] == 0).all())

    def test_mlt_preserves_indices_with_empty_rows(self):
        table = b"ML2" + pack("<I", 1) + name("Torso.3DC") + pack("<I", 1) + name("Torso.tga")
        table += pack("<7i", 2, -1, -1, 1, 0, 0, 0)
        self.assertEqual(table_rows(table, "armor"), (None, ("Torso.3DC", "Torso.tga", True, -1)))

    def test_weapon_alias_resolves_real_texture_and_triangle(self):
        vertices = (
            (-1, -1, 0, 0, 0, 1, .1, .75),
            (0, 1, 0, 0, 0, 1, .5, .1),
            (1, -1, 0, 0, 0, 1, .9, .75),
        )
        table = b"ITM" + pack("<I", 1) + name("Espada.3DO") + pack("<I", 1) + name("Metal.tga")
        table += pack("<7i", 1, 0, 0, 2, 0, 0, 0)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Item/3DO").mkdir(parents=True)
            (root / "Item/DDS").mkdir()
            (root / "Item/01.ITM").write_bytes(table)
            mesh = name("Metal.tga") + pack("<I", 3) + b"".join(pack("<8f", *vertex) for vertex in vertices)
            (root / "Item/3DO/espada.3do").write_bytes(mesh + pack("<I3H", 1, 0, 1, 2))
            Image.new("RGBA", (4, 4), (210, 28, 20, 255)).save(root / "Item/DDS/metal.dds")
            assets = ModelAssets(root)
            model = assets.choices(45)[0]
            self.assertEqual((model.name, model.glow, model.table), ("Espada.3DO", 2, "Item/01.ITM"))
            renderer = PreviewRenderer(assets)
            pixels = np.asarray(renderer.render(model, "weapon", 100, 120))
            changed = pixels[(pixels != BACKGROUND).any(axis=2)]
            self.assertGreater(len(changed), 300)
            self.assertTrue((changed[:, 0] > changed[:, 1]).all())

    def test_truncated_model_table_is_rejected(self):
        with self.assertRaises(ModelFormatError):
            table_rows(b"IT2" + pack("<i", 3), "weapon")

    def test_cutout_reveals_farther_triangle_in_same_batch(self):
        pixels = np.zeros((8, 8, 3), dtype=np.uint8)
        depth = np.zeros((8, 8), dtype=np.float32)
        projected = np.tile(np.array(((1, 1), (7, 1), (1, 7)), dtype=np.float32), (2, 1))
        texture = np.array((((240, 5, 0, 0), (0, 220, 0, 255)),), dtype=np.uint8)
        surface = PreparedSurface(
            Surface("", "", "skin", cutout=True),
            np.empty((0, 3)), np.empty((0, 3)),
            np.repeat(np.array(((.25, .5), (.75, .5)), dtype=np.float32), 3, axis=0),
            np.array(((0, 1, 2), (3, 4, 5))), texture,
        )
        draw_triangles(pixels, depth, projected, np.repeat((2.0, 1.0), 3), np.ones(6), surface)
        self.assertEqual(tuple(pixels[2, 2]), (0, 220, 0))
        self.assertEqual(depth[2, 2], 1.0)
