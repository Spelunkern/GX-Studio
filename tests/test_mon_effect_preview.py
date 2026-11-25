from pathlib import Path
from struct import pack
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image

from gx_items.effect_preview import EffectMaterial, MonPreviewRenderer, read_eft
from gx_items.equipment_models import ModelAssets, ModelChoice, ModelFormatError, Surface
from gx_items.model_render import draw_triangles, PreparedSurface


def name(text):
    data = text.encode("utf-8")
    return pack("<i", len(data)) + data


def effect_library():
    return (
        b"EFT" + pack("<2i", 0, 1) + name("spark.dds") + pack("<i", 1)
        + name("Spark") + pack("<10i", 0, 0, 0, 1, 5, 0, 4, 1, -1, 0)
        + pack("<8f", .1, 20, 1.5, 10, .5, .2, 0, 0)
        + bytes(60 + 24 + 20)
        + pack("<i5f", 1, 1, 1, 1, 1, 0) + pack("<i", 0) + pack("<i3f", 1, .4, .4, 0)
        + bytes(16) + pack("<2i", 1, 0)
        + pack("<i", 1) + name("Attack") + pack("<2if", 1, 0, .125)
    )


class MonEffectPreviewTest(unittest.TestCase):
    def test_eft_timing_and_sequence(self):
        data = effect_library()
        library = read_eft(data)
        emitter = library.emitters[0]
        self.assertEqual((emitter.rate, emitter.life, emitter.source, emitter.destination), (15, 1, 4, 5))
        self.assertAlmostEqual(emitter.duration, .2)
        self.assertEqual(library.sequences, (((0, .125),),))
        with self.assertRaises(ModelFormatError):
            read_eft(data[:-1])

    def test_bone_and_action_placements_share_texture_and_keep_their_anchors(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Effect/DDS").mkdir(parents=True)
            Image.new("RGBA", (4, 4), (100, 50, 255, 255)).save(root / "Effect/DDS/Spark.DDS")
            (root / "Effect/monster.eft").write_bytes(effect_library())
            (root / "Effect/Attack.eft").write_bytes(effect_library())
            (root / "ANI").mkdir()
            bone = np.eye(4, dtype="<f4")
            bone[3, :3] = (1, 2, 3)
            (root / "ANI/idle.ani").write_bytes(pack("<IIHi", 0, 0, 1, -1) + bone.tobytes() + bytes(8))
            model = ModelChoice("NPC", (Surface("mesh", "", "skin", pose="ANI/idle.ani"),))
            renderer = MonPreviewRenderer(ModelAssets(root), root)
            placements, bone_count, _, problems = renderer.scene(model, ((0, 0), (-1, 0), (1, 0)), "attack.EFT")
            self.assertEqual(bone_count, 1)
            self.assertEqual(len(placements), 2)
            self.assertEqual(problems, ("Bone 1 is out of range for this model.",))
            np.testing.assert_array_equal(placements[0].matrix, bone)
            np.testing.assert_array_equal(placements[1].matrix, np.diag((-1, 1, -1, 1)))
            self.assertEqual(placements[1].delay, .125)
            self.assertIs(placements[0].textures[0], placements[1].textures[0])

    def test_particles_are_blended_and_occluded_without_writing_depth(self):
        pixels = np.zeros((8, 8, 3), dtype=np.uint8)
        depth = np.zeros((8, 8), dtype=np.float32)
        depth[2, 2] = 2
        surface = PreparedSurface(
            Surface("", "", "rigid", two_sided=True), np.empty((0, 3)), np.empty((0, 3)),
            np.zeros((3, 2)), np.array(((0, 1, 2),)), np.array([[[200, 100, 50, 255]]], dtype=np.uint8),
        )
        material = EffectMaterial(np.array((1, 1, 1, .5)), 4, 5, True)
        draw_triangles(pixels, depth, np.array(((1, 1), (7, 1), (1, 7))), np.ones(3), None, surface, material=material)
        np.testing.assert_array_equal(pixels[2, 2], (0, 0, 0))
        np.testing.assert_array_equal(pixels[3, 2], (100, 50, 25))
        self.assertEqual(depth[3, 2], 0)
