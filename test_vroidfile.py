"""Tests on your own VRoid files (read-only). Writes go to a temp folder.

Set two files saved by VRoid Studio 2.x, then run the tests:
  VROID_TEST_BASE    a model without the outfit
  VROID_TEST_OUTFIT  the same model wearing a top made of at least two templates (for example shirt + jacket)
  VROID_TEST_TMP     optional folder for temporary output (default: the system temp folder)

  python -m unittest test_vroidfile -v
The tests are skipped when the two files are not set.
"""
import asyncio
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from vroid_files_mcp import recipe  # noqa: E402
from vroid_files_mcp import vroidfile as vf  # noqa: E402

BASE = Path(os.environ.get("VROID_TEST_BASE", "")) if os.environ.get("VROID_TEST_BASE") else None
OUTFIT = Path(os.environ.get("VROID_TEST_OUTFIT", "")) if os.environ.get("VROID_TEST_OUTFIT") else None
TMP_PARENT = os.environ.get("VROID_TEST_TMP") or None


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def short(name):
    return name.rsplit(".", 1)[-1]


@unittest.skipUnless(BASE and OUTFIT and BASE.exists() and OUTFIT.exists(), "set VROID_TEST_BASE and VROID_TEST_OUTFIT")
class VroidFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TMP_PARENT:
            Path(TMP_PARENT).mkdir(parents=True, exist_ok=True)
        cls.tmp = Path(tempfile.mkdtemp(prefix="vroid_mcp_test_", dir=TMP_PARENT))
        cls.src_hash = sha(OUTFIT)
        rows = vf.list_textures(OUTFIT, compare_to=BASE)
        changed = [r for r in rows if r.get("changed_vs_compare")]
        main = [r for r in changed if any("MainImage" in u for u in r["used_as"])]
        assert changed, "the outfit file has no texture layers that differ from the base file"
        def has_shade(r):
            try:
                return "Level1Clothing_MaterialShadeColor" in vf.item_colors(OUTFIT, r["texture_id"])
            except (KeyError, ValueError):
                return False
        pick = next((r for r in main if has_shade(r)), (main or changed)[0])
        cls.layer, cls.tex = pick["layer_guid"], pick["texture_id"]
        # a garment template that has a sleeve slider still at its default (0)
        groups = {}
        for r in recipe.list_params(OUTFIT, kind="slider"):
            groups.setdefault(r["group"], {})[short(r["name"])] = r
        cls.group = next(g for g, p in groups.items()
                         if "ArmShort" in p and p["ArmShort"]["editable"] and p["ArmShort"]["value"] == 0
                         and "AllVolume" in p and p["AllVolume"]["editable"])
        cls.volume = groups[cls.group]["AllVolume"]["value"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        assert sha(OUTFIT) == cls.src_hash, "source file changed"

    def test_info_and_list(self):
        self.assertTrue(vf.info(OUTFIT)["vroid_version"].startswith("2."))
        self.assertFalse(any(r["changed_vs_compare"] for r in vf.list_textures(BASE, compare_to=BASE)))

    def test_identity_rewrite_is_byte_equal(self):
        _, data = vf.load(OUTFIT)
        _, layer = vf._find_layer(data, self.layer)
        self.assertEqual(vf._replace(data, layer.chain, layer.png(data)), data)

    def test_replace_layer_round_trip(self):
        png = self.tmp / "layer.png"
        vf.export_layer(OUTFIT, self.layer, png)
        from PIL import Image
        im = Image.open(png).convert("RGBA")
        im.putpixel((0, 0), (1, 2, 3, 255))
        im.save(png)
        dst = self.tmp / "out.vroid"
        vf.replace_layer(OUTFIT, dst, self.layer, png)
        _, data = vf.load(dst)
        _, layer = vf._find_layer(data, self.layer)
        self.assertEqual(layer.png(data), png.read_bytes())
        with zipfile.ZipFile(dst) as z, zipfile.ZipFile(OUTFIT) as z0:
            self.assertEqual(z.namelist(), z0.namelist())

    def test_replace_layer_refuses(self):
        png = self.tmp / "b.png"
        vf.export_layer(OUTFIT, self.layer, png)
        with self.assertRaises(ValueError):
            vf.replace_layer(OUTFIT, OUTFIT, self.layer, png)
        old = vf.PROTECTED_NAMES
        vf.PROTECTED_NAMES = ["MY_BASE"]
        try:
            with self.assertRaises(ValueError):
                vf.replace_layer(OUTFIT, self.tmp / "my_base_x.vroid", self.layer, png)
        finally:
            vf.PROTECTED_NAMES = old
        existing = self.tmp / "exists.vroid"
        existing.write_bytes(b"x")
        with self.assertRaises(FileExistsError):
            vf.replace_layer(OUTFIT, existing, self.layer, png)
        from PIL import Image
        Image.new("RGBA", (64, 64)).save(self.tmp / "small.png")
        with self.assertRaises(ValueError):
            vf.replace_layer(OUTFIT, self.tmp / "small.vroid", self.layer, self.tmp / "small.png")

    def test_set_item_colors(self):
        dst = self.tmp / "col.vroid"
        r = vf.set_item_colors(OUTFIT, dst, self.tex, {"MaterialShadeColor": "#CAB9C2"})
        self.assertIn("Level1Clothing_MaterialShadeColor", r["changed"])
        got = vf.item_colors(dst, self.tex)["Level1Clothing_MaterialShadeColor"]
        self.assertAlmostEqual(got[0], 0xCA / 255, places=3)
        with self.assertRaises(KeyError):
            vf.set_item_colors(OUTFIT, self.tmp / "bad.vroid", self.tex, {"NoSuchColor": "#000000"})

    def test_list_params(self):
        rows = recipe.list_params(OUTFIT)
        self.assertGreater(len(rows), 100)
        p = {short(r["name"]): r for r in recipe.list_params(OUTFIT, group=self.group)}
        self.assertEqual(p["ArmShort"]["value"], 0.0)  # default: entry without a stored value
        self.assertTrue(p["ArmShort"]["editable"])

    def test_set_default_slider_changes_only_that_value(self):
        dst = self.tmp / "sleeve.vroid"
        new_volume = 10.0 if self.volume != 10.0 else 20.0
        r = recipe.set_params(OUTFIT, dst, [{"group": self.group, "name": "ArmShort", "value": 100},
                                             {"group": self.group, "name": "AllVolume", "value": new_volume}])
        self.assertEqual(len(r["changed"]), 2)
        a = {(x["group"], x["name"]): x["value"] for x in recipe.list_params(OUTFIT)}
        b = {(x["group"], x["name"]): x["value"] for x in recipe.list_params(dst)}
        diff = {short(k[1]): b[k] for k in a if a[k] != b[k]}
        self.assertEqual(diff, {"ArmShort": 100.0, "AllVolume": new_volume})
        dst2 = self.tmp / "sleeve0.vroid"
        recipe.set_params(dst, dst2, [{"group": self.group, "name": "ArmShort", "value": 0}])
        self.assertEqual({short(x["name"]): x["value"] for x in
                          recipe.list_params(dst2, group=self.group)}["ArmShort"], 0.0)

    def test_identity_lock(self):
        old = recipe.LOCK_IDENTITY
        recipe.LOCK_IDENTITY = True
        try:
            face = next(r for r in recipe.list_params(OUTFIT)
                        if r["locked"] and r["editable"] and r["kind"] == "slider")
            with self.assertRaises(PermissionError):
                recipe.set_params(OUTFIT, self.tmp / "face.vroid",
                                  [{"group": face["group"], "name": face["name"], "value": 1}])
        finally:
            recipe.LOCK_IDENTITY = old


class ServerTests(unittest.TestCase):
    """Runs without any model files."""

    def test_server_lists_tools(self):
        from vroid_files_mcp import server
        tools = asyncio.run(server.server.list_tools())
        names = {t.name for t in tools}
        self.assertEqual(names, {"vroid_info", "vroid_list_textures", "vroid_export_layer", "vroid_export_textures",
                                 "vroid_replace_layer", "vroid_item_colors", "vroid_set_item_colors",
                                 "vroid_list_params", "vroid_set_params"})
        from mcp.server.mcpserver.exceptions import ToolError
        here = str(HERE / "server.py")
        with self.assertRaises(ToolError):
            server._p("relative/file.vroid")
        saved = server.ALLOWED_ROOTS
        try:
            server.ALLOWED_ROOTS = []
            with self.assertRaisesRegex(ToolError, "set VROID_ALLOWED_ROOTS"):
                server._p(here)
            server.ALLOWED_ROOTS = [str(HERE / "vroid_files_mcp")]
            with self.assertRaisesRegex(ToolError, "outside VROID_ALLOWED_ROOTS"):
                server._p(here)
            server.ALLOWED_ROOTS = [str(HERE)]
            self.assertEqual(server._p(here), Path(here).resolve())
        finally:
            server.ALLOWED_ROOTS = saved


if __name__ == "__main__":
    unittest.main()
