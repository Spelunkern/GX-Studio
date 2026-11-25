from pathlib import Path
from struct import pack
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from gx_items.asset_models import ASSET_EXTENSIONS, AssetRenderer, effect_components, effect_scenes
from gx_items.asset_view import read_document
from gx_items.effect_preview import EffectLibrary, IDENTITY, MAX_PARTICLES, Placement
from gx_items.equipment_models import ModelAssets
from gx_items.model_render import read_animation
from gx_items.world_assets import DUNGEON_VERTEX, WORLD_VERTEX, read_dg, read_vani


def name(value):
    data = value.encode("utf-8") + b"\0"
    return pack("<i", len(data)) + data


class AssetViewsTest(unittest.TestCase):
    def test_full_effect_keeps_every_authored_instance_and_isolates_repeated_emitters(self):
        emitters = tuple(SimpleNamespace(name=f"Component {index}") for index in range(3))
        records = tuple((index % 2, index / 60) for index in range(48))
        library = EffectLibrary((), (), emitters, (records,), ("Cast",))
        with patch("gx_items.asset_models.read_eft", return_value=library):
            variants = effect_scenes(Path("skill.EFT"), None, b"")
            self.assertEqual(variants[0][0], "Sequence 0 · Cast")
            self.assertEqual(variants[1][0], "Unassigned Emitters")
            scene = variants[0][1]
            components = effect_components(scene)
            self.assertIs(components[0][1], scene)
            self.assertEqual(components[2][1].eft[1], records[1::2])
            renderer = AssetRenderer(ModelAssets(Path(".")))
            template = Placement(emitters[0], (), None, None, IDENTITY, 0)
            with patch.object(renderer, "placement", return_value=template) as placement:
                instances, errors = renderer.placements(scene)
                self.assertFalse(errors)
                self.assertEqual([i.delay for i in instances], [delay for _, delay in records])
                self.assertEqual(placement.call_count, 2)
        with patch("gx_items.asset_models.read_eft", return_value=EffectLibrary((), (), emitters, ())):
            label, scene = effect_scenes(Path("skill.EFT"), None, b"")[0]
            self.assertEqual(label, "All Emitters")
            self.assertEqual(scene.eft[1], ((0, 0), (1, 0), (2, 0)))

    def test_composite_uses_every_active_layer_in_full_effect_mode(self):
        renderer = AssetRenderer(ModelAssets(Path(".")))
        canvas = np.zeros((2, 2, 3), dtype=np.uint8)
        depth = np.zeros((2, 2), dtype=np.float32)
        particle = (0, np.zeros((3, 2), dtype=np.float32), None, SimpleNamespace(camera_facing=False), None)
        with patch("gx_items.effect_preview.draw_triangles") as draw:
            renderer.compose(canvas, depth, [iter((particle,)) for _ in range(40)], include_each=True)
            self.assertEqual(draw.call_count, 40)
            draw.reset_mock()
            renderer.compose(canvas, depth, [iter((particle,)) for _ in range(40)])
            self.assertEqual(draw.call_count, MAX_PARTICLES)

    def test_dg_texture_is_fixed_width_and_lightmap_uv_is_retained(self):
        vertices = np.zeros(3, dtype=DUNGEON_VERTEX)
        vertices["position"] = ((-1, 0, 0), (1, 0, 0), (0, 1, 0))
        vertices["uv2"] = ((.25, .5), (.75, .5), (.5, 1))
        raw = bytes(24) + pack("<i", 1) + b"pared.dds".ljust(256, b"\0") + pack("<2i", 1, 1)
        raw += bytes(60) + pack("<5i", 1, 0, 1, 0, 3) + vertices.tobytes() + pack("<i3H", 1, 0, 1, 2)
        raw += bytes(36)
        part, = read_dg(raw)
        self.assertEqual((part.texture, part.lightmap), ("pared.dds", 0))
        np.testing.assert_array_equal(part.vertices[0]["uv2"], vertices["uv2"])
        with self.assertRaises(ValueError):
            read_dg(raw[:-4])

    def test_vani_exposes_complete_frames_and_authored_timing(self):
        vertices = np.zeros((2, 3), dtype=WORLD_VERTEX)
        vertices["position"][1, :, 1] = 2
        raw = bytes(40) + pack("<3i", 1, 2, 66) + name("obj.dds") + pack("<i3H", 1, 0, 1, 2)
        raw += pack("<i", 3) + vertices.tobytes() + bytes(28)
        parts, interval = read_vani(raw)
        self.assertEqual(interval, 66)
        np.testing.assert_array_equal(parts[0].vertices, vertices)

    def test_ani_frame_sampling_animates_the_hierarchy(self):
        root = np.eye(4, dtype="<f4")
        child = root.copy()
        child[3, 1] = 1
        data = pack("<IIHi", 0, 30, 2, -1) + root.tobytes()
        data += pack("<3i3fi3f", 0, 2, 0, 0, 0, 0, 30, 2, 0, 0)
        data += pack("<i", 0) + child.tobytes() + bytes(8)
        clip = read_animation(data)
        np.testing.assert_allclose(clip.pose(15)[1, 3, :3], (1, 1, 0))

    def test_read_only_documents_keep_alpha_and_exclude_map_entries(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "transparente.png"
            Image.new("RGBA", (3, 2), (120, 40, 180, 70)).save(path)
            raw = path.read_bytes()
            mode, data = read_document(path, None)
            self.assertEqual(mode, "image")
            self.assertEqual(data.pixels[:4], bytes((120, 40, 180, 70)))
            self.assertEqual(path.read_bytes(), raw)
        self.assertFalse({".wld", ".svmap"} & ASSET_EXTENSIONS)
