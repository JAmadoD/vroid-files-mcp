"""Read and patch VRoid Studio 2.x .vroid files without VRoid.

A .vroid file is a zip: meta.json, v1model/meta.json, v1model/data.bin (protobuf, no public schema) and a
thumbnail. data.bin keeps every texture as a list of named layers, each layer a PNG. This module reads those
layers and can write a COPY of the file with one layer's PNG replaced. It never changes the source file.

Layout found by reading files made in VRoid 2.14:
  4.3.2.5 texture entry: 1 id (40 hex), 6 {1 width, 2 height, 3 {1 layer...}, 4 ?}
  layer:                 1 guid (32 hex), 2 name ("Layer", "Default Image"), 7 {2 PNG}
  5.*                    the recipe; parameter keys such as "Level1Clothing_MainImage" point at texture ids.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .pbwire import enc_varint, fields, is_message, read_varint

DATA = "v1model/data.bin"
PNG_SIG = b"\x89PNG\r\n\x1a\n"
HEX = set("0123456789abcdef")
# Optional: comma-separated name parts that may never be written, e.g. VROID_PROTECTED_NAMES=MY_BASE_MODEL
PROTECTED_NAMES = [n.strip().upper() for n in os.environ.get("VROID_PROTECTED_NAMES", "").split(",") if n.strip()]


def _check_protected(dst):
    for n in PROTECTED_NAMES:
        if n in dst.name.upper():
            raise ValueError(f"refusing to write a protected file (name contains {n})")


def _is_hex_id(t: str, n: int) -> bool:
    return len(t) == n and set(t) <= HEX


@dataclass
class Layer:
    guid: str
    name: str
    png_size: int
    chain: list = field(repr=False, default_factory=list)  # (field_start, value_start, value_end) outer -> PNG

    def png(self, data: bytes) -> bytes:
        s, e = self.chain[-1][1], self.chain[-1][2]
        return data[s:e]


@dataclass
class Texture:
    id: str
    width: int
    height: int
    layers: list
    used_as: list  # recipe parameter names that point at this texture id


def load(path) -> tuple[dict, bytes]:
    with zipfile.ZipFile(path) as z:
        members = {i.filename: z.read(i.filename) for i in z.infolist()}
    if DATA not in members:
        raise ValueError(f"{path} has no {DATA}; not a VRoid 2.x file")
    return members, members[DATA]


def _str(data, s, e):
    try:
        t = data[s:e].decode("utf-8")
        return t if t.isprintable() else None
    except UnicodeDecodeError:
        return None


def _children(data, s, e):
    return list(fields(data, s, e))


def _find_textures(data):
    """Every message shaped like a texture entry, anywhere in the file."""
    out = []

    def walk(s, e, chain, depth):
        kids = _children(data, s, e)
        by = {}
        for fn, wt, v, span in kids:
            by.setdefault(fn, []).append((wt, v, span))
        # texture entry: 1 = 40-hex id, 6 = message with width/height/layers
        if 1 in by and 6 in by and by[1][0][0] == 2:
            tid = _str(data, by[1][0][2][1], by[1][0][2][2])
            s6, e6 = by[6][0][2][1], by[6][0][2][2]
            if tid and _is_hex_id(tid, 40) and by[6][0][0] == 2 and is_message(data, s6, e6):
                try:
                    tex = _parse_texture(data, tid, by[6][0][2], chain)
                except (ValueError, IndexError):
                    tex = None
                if tex:
                    out.append(tex)
                    return
        for fn, wt, v, (fs, vs, ve) in kids:
            if wt == 2 and depth < 14 and ve - vs > 16 and data[vs:vs + 8] != PNG_SIG and is_message(data, vs, ve):
                walk(vs, ve, chain + [(fs, vs, ve)], depth + 1)

    walk(0, len(data), [], 0)
    return out


def _parse_texture(data, tid, span6, chain):
    fs6, s6, e6 = span6
    w = h = 0
    layers = []
    for fn, wt, v, (fs, vs, ve) in fields(data, s6, e6):
        if fn == 1 and wt == 0:
            w = v
        elif fn == 2 and wt == 0:
            h = v
        elif fn == 3 and wt == 2:
            for lf, lwt, _, (lfs, lvs, lve) in fields(data, vs, ve):
                if lf != 1 or lwt != 2:
                    continue
                guid = name = None
                png_chain = None
                for f2, wt2, _, (fs2, vs2, ve2) in fields(data, lvs, lve):
                    if f2 == 1 and wt2 == 2:
                        guid = _str(data, vs2, ve2)
                    elif f2 == 2 and wt2 == 2:
                        name = _str(data, vs2, ve2)
                    elif f2 == 7 and wt2 == 2:
                        for f3, wt3, _, (fs3, vs3, ve3) in fields(data, vs2, ve2):
                            if f3 == 2 and wt3 == 2 and data[vs3:vs3 + 8] == PNG_SIG:
                                png_chain = chain + [(fs6, s6, e6), (fs, vs, ve), (lfs, lvs, lve),
                                                     (fs2, vs2, ve2), (fs3, vs3, ve3)]
                layers.append(Layer(guid or "", name or "", (png_chain[-1][2] - png_chain[-1][1]) if png_chain else 0,
                                    png_chain or []))
    if not w or not h:
        return None
    return Texture(tid, w, h, layers, [])


def _recipe_refs(data):
    """Map texture id -> recipe parameter names (e.g. Level1Clothing_MainImage)."""
    refs = {}

    def walk(s, e, depth):
        kids = _children(data, s, e)
        name = None
        ids = []
        for fn, wt, v, (fs, vs, ve) in kids:
            if wt != 2:
                continue
            t = _str(data, vs, ve)
            if fn == 1 and t and not _is_hex_id(t, 40) and len(t) < 120:
                name = t
            elif data[vs:vs + 8] != PNG_SIG and ve - vs < 4096 and is_message(data, vs, ve):
                for g in _flat_ids(data, vs, ve, 4):
                    ids.append(g)
        if name and ids:
            for g in ids:
                refs.setdefault(g, set()).add(name)
        for fn, wt, v, (fs, vs, ve) in kids:
            if wt == 2 and depth < 14 and ve - vs > 16 and data[vs:vs + 8] != PNG_SIG and is_message(data, vs, ve):
                walk(vs, ve, depth + 1)

    walk(0, len(data), 0)
    return {k: sorted(v) for k, v in refs.items()}


def _flat_ids(data, s, e, depth):
    for fn, wt, v, (fs, vs, ve) in fields(data, s, e):
        if wt != 2:
            continue
        t = _str(data, vs, ve)
        if t and _is_hex_id(t, 40):
            yield t
        elif depth > 0 and data[vs:vs + 8] != PNG_SIG and is_message(data, vs, ve):
            yield from _flat_ids(data, vs, ve, depth - 1)


def textures(data) -> list:
    texs = _find_textures(data)
    refs = _recipe_refs(data)
    for t in texs:
        t.used_as = refs.get(t.id, [])
    return texs


def info(path) -> dict:
    members, data = load(path)
    meta = json.loads(members.get("meta.json", b"{}") or b"{}")
    texs = textures(data)
    return {
        "file": str(path), "bytes": os.path.getsize(path),
        "vroid_version": f"{meta.get('updateVroidVersionMajor')}.{meta.get('updateVroidVersionMinor')}."
                         f"{meta.get('updateVroidVersionPatch')}",
        "members": {k: len(v) for k, v in members.items()},
        "textures": len(texs), "layers": sum(len(t.layers) for t in texs),
    }


def list_textures(path, compare_to=None) -> list:
    _, data = load(path)
    other = set()
    if compare_to:
        _, od = load(compare_to)
        other = {hashlib.sha1(l.png(od)).hexdigest() for t in textures(od) for l in t.layers if l.chain}
    rows = []
    for t in textures(data):
        for l in t.layers:
            row = {"texture_id": t.id, "size": [t.width, t.height], "used_as": t.used_as,
                   "layer_guid": l.guid, "layer_name": l.name, "png_bytes": l.png_size}
            if compare_to:
                row["changed_vs_compare"] = bool(l.chain) and hashlib.sha1(l.png(data)).hexdigest() not in other
            rows.append(row)
    return rows


def _find_layer(data, layer_guid):
    hits = [(t, l) for t in textures(data) for l in t.layers if l.guid == layer_guid]
    if not hits:
        raise KeyError(f"layer {layer_guid} not found")
    if len(hits) > 1:
        raise KeyError(f"layer {layer_guid} appears {len(hits)} times")
    return hits[0]


def _check_png_out(out_png, overwrite=False) -> Path:
    """Exports only ever write .png files, and never replace an existing file unless overwrite is set."""
    out_png = Path(out_png)
    if out_png.suffix.lower() != ".png":
        raise ValueError(f"export target must end in .png: {out_png}")
    if out_png.exists() and not overwrite:
        raise FileExistsError(f"{out_png} exists; pick a new name or set overwrite")
    return out_png


def export_layer(path, layer_guid, out_png, overwrite=False) -> dict:
    _, data = load(path)
    t, l = _find_layer(data, layer_guid)
    if not l.chain:
        raise ValueError("that layer has no image (empty layer)")
    out_png = _check_png_out(out_png, overwrite)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.write_bytes(l.png(data))
    return {"png": str(out_png), "bytes": l.png_size, "texture_id": t.id, "used_as": t.used_as}


def export_all(path, out_dir, compare_to=None, overwrite=False) -> list:
    _, data = load(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    keep = None
    if compare_to:
        keep = {r["layer_guid"] for r in list_textures(path, compare_to) if r.get("changed_vs_compare")}
    done = []
    for t in textures(data):
        tag = "".join(c if c.isalnum() or c in "-_." else "_" for c in (t.used_as[0] if t.used_as else "unused"))[:60]
        for l in t.layers:
            if not l.chain or (keep is not None and l.guid not in keep):
                continue
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in l.name)
            p = _check_png_out(out_dir / f"{tag}__{safe}__{l.guid[:8]}.png", overwrite)
            p.write_bytes(l.png(data))
            done.append({"png": str(p), "layer_guid": l.guid, "texture_id": t.id, "used_as": t.used_as})
    return done


def _replace(data: bytes, chain: list, new_value: bytes) -> bytes:
    """Replace the innermost length-delimited value in `chain` and fix every enclosing length."""
    value = new_value
    for k in range(len(chain) - 1, -1, -1):
        fs, vs, ve = chain[k]
        _, ks = read_varint(data, fs)  # end of the key varint
        rebuilt = data[fs:ks] + enc_varint(len(value)) + value
        if k == 0:
            return data[:fs] + rebuilt + data[ve:]
        pfs, pvs, pve = chain[k - 1]
        value = data[pvs:fs] + rebuilt + data[ve:pve]
    raise AssertionError("empty chain")


def replace_layer(src, dst, layer_guid, png_path) -> dict:
    """Write dst = src with one layer's PNG replaced. dst must be new; the PNG must match the texture size."""
    src, dst = Path(src).resolve(), Path(dst).resolve()
    if dst == src:
        raise ValueError("dst must be a different file from src (the source is never changed)")
    if dst.exists():
        raise FileExistsError(f"{dst} exists; pick a new name")
    if dst.suffix.lower() != ".vroid":
        raise ValueError("dst must end in .vroid")
    _check_protected(dst)
    png = Path(png_path).read_bytes()
    if png[:8] != PNG_SIG:
        raise ValueError("not a PNG file")
    from PIL import Image  # size check only
    w, h = Image.open(io.BytesIO(png)).size
    members, data = load(src)
    t, l = _find_layer(data, layer_guid)
    if not l.chain:
        raise ValueError("that layer has no image to replace")
    if (w, h) != (t.width, t.height):
        raise ValueError(f"PNG is {w}x{h}, texture is {t.width}x{t.height}")
    new_data = _replace(data, l.chain, png)
    # verify: re-parse and read the layer back
    t2, l2 = _find_layer(new_data, layer_guid)
    if l2.png(new_data) != png:
        raise AssertionError("verification failed: layer does not read back")
    if len(textures(new_data)) != len(textures(data)):
        raise AssertionError("verification failed: texture count changed")
    members[DATA] = new_data
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".vroid.tmp")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info_ in zin.infolist():
            zout.writestr(info_, members[info_.filename])
    tmp.replace(dst)
    return {"written": str(dst), "bytes": dst.stat().st_size, "texture_id": t.id, "used_as": t.used_as,
            "layer_guid": layer_guid, "old_png_bytes": l.png_size, "new_png_bytes": len(png)}


# ---------------------------------------------------------------- item colours
# Recipe block (e.g. 5.5.9): repeated field 2 = parameter {1 name, 4 {1 value_id}, 6 {1 texture_id}}.
# Value table entry: {1 value_id, 2 '', 3 {1 r, 2 g, 3 b, 4 a} as fixed32 floats}.


COLOR_SUFFIXES = ("MaterialColor", "MaterialShadeColor", "MaterialEmissionColor", "MaterialOutlineColor",
                  "OverlayColor")


def _recipe_blocks(data):
    """Yield (block_span, params) for every message whose children are named parameters."""
    out = []

    def walk(s, e, depth):
        params = []
        for fn, wt, v, (fs, vs, ve) in fields(data, s, e):
            if fn == 2 and wt == 2 and data[vs:vs + 8] != PNG_SIG and is_message(data, vs, ve):
                p = {"span": (vs, ve)}
                try:
                    for f2, w2, _, (a, c, d) in fields(data, vs, ve):
                        if f2 == 1 and w2 == 2:
                            p["name"] = _str(data, c, d)
                        elif f2 in (4, 5, 6) and w2 == 2 and is_message(data, c, d):
                            for f3, w3, _, (a3, c3, d3) in fields(data, c, d):
                                if f3 == 1 and w3 == 2:
                                    p[{4: "ref", 5: "slider", 6: "texture"}[f2]] = _str(data, c3, d3)
                except (ValueError, IndexError):
                    continue
                if p.get("name"):
                    params.append(p)
        if len(params) >= 3 and any((p.get("name") or "").endswith("_MainImage") for p in params):
            out.append(((s, e), params))
        for fn, wt, v, (fs, vs, ve) in fields(data, s, e):
            if wt == 2 and depth < 10 and ve - vs > 64 and data[vs:vs + 8] != PNG_SIG and is_message(data, vs, ve):
                walk(vs, ve, depth + 1)

    walk(0, len(data), 0)
    return out


def _color_values(data):
    """value_id -> (offset of the 16 float bytes) for every colour entry in the value table."""
    vals = {}

    def walk(s, e, depth):
        kids = list(fields(data, s, e))
        vid = None
        col = None
        for fn, wt, v, (fs, vs, ve) in kids:
            if fn == 1 and wt == 2:
                t = _str(data, vs, ve)
                if t and _is_hex_id(t, 40):
                    vid = t
            elif fn == 3 and wt == 2 and ve - vs == 20:
                b = data[vs:ve]
                if b[0] == 0x0d and b[5] == 0x15 and b[10] == 0x1d and b[15] == 0x25:
                    col = vs
        if vid and col is not None:
            vals[vid] = col
            return
        for fn, wt, v, (fs, vs, ve) in kids:
            if wt == 2 and depth < 10 and ve - vs > 16 and data[vs:vs + 8] != PNG_SIG and is_message(data, vs, ve):
                walk(vs, ve, depth + 1)

    walk(0, len(data), 0)
    return vals


def _read_rgba(data, off):
    return tuple(struct.unpack_from("<f", data, off + 1 + 5 * k)[0] for k in range(4))


def item_colors(path, texture_id) -> dict:
    """Colour parameters of the item that uses `texture_id` as its main image."""
    _, data = load(path)
    return {k: v["rgba"] for k, v in _item_color_slots(data, texture_id).items()}


def _item_color_slots(data, texture_id):
    vals = _color_values(data)
    for (s, e), params in _recipe_blocks(data):
        if any(p.get("texture") == texture_id and p["name"].endswith("_MainImage") for p in params):
            slots = {}
            for p in params:
                if p["name"].endswith(COLOR_SUFFIXES) and p.get("ref") in vals:
                    off = vals[p["ref"]]
                    slots[p["name"]] = {"offset": off, "rgba": [round(x, 4) for x in _read_rgba(data, off)]}
            return slots
    raise KeyError(f"no item uses texture {texture_id} as its main image")


def _hex_to_rgba(h):
    h = h.lstrip("#")
    if len(h) not in (6, 8):
        raise ValueError("colour must be #RRGGBB or #RRGGBBAA")
    vals = [int(h[i:i + 2], 16) / 255 for i in range(0, len(h), 2)]
    return vals + [1.0] * (4 - len(vals))


def _check_dst(src, dst):
    src, dst = Path(src).resolve(), Path(dst).resolve()
    if dst == src:
        raise ValueError("dst must be a different file from src (the source is never changed)")
    if dst.exists():
        raise FileExistsError(f"{dst} exists; pick a new name")
    if dst.suffix.lower() != ".vroid":
        raise ValueError("dst must end in .vroid")
    _check_protected(dst)
    return src, dst


def _write_copy(src, dst, members, new_data):
    members[DATA] = new_data
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".vroid.tmp")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info_ in zin.infolist():
            zout.writestr(info_, members[info_.filename])
    tmp.replace(dst)


def set_item_colors(src, dst, texture_id, colors: dict) -> dict:
    """Write dst = src with colour parameters changed, e.g. {"MaterialShadeColor": "#CAB9C2"}.
    Keys may be full names (Level1Clothing_MaterialShadeColor) or just the suffix."""
    src, dst = _check_dst(src, dst)
    members, data = load(src)
    slots = _item_color_slots(data, texture_id)
    buf = bytearray(data)
    changed = {}
    for key, hexv in colors.items():
        names = [n for n in slots if n == key or n.endswith("_" + key)]
        if len(names) != 1:
            raise KeyError(f"{key!r} matches {names or 'nothing'}; available: {sorted(slots)}")
        off = slots[names[0]]["offset"]
        for k, val in enumerate(_hex_to_rgba(hexv)):
            struct.pack_into("<f", buf, off + 1 + 5 * k, val)
        changed[names[0]] = {"old": slots[names[0]]["rgba"], "new": [round(x, 4) for x in _read_rgba(buf, off)]}
    new_data = bytes(buf)
    if len(textures(new_data)) != len(textures(data)):
        raise AssertionError("verification failed")
    _write_copy(src, dst, members, new_data)
    return {"written": str(dst), "changed": changed}


# ---------------------------------------------------------------- garment sliders
# Slider parameter: {1 "BlendShape_BlendShape.N00_007_01_Tops.AllVolume", 5 {1 value_id, ...}}.
# Slider value entry: {1 value_id, 2 '', 3 float (fixed32)}. The value is the number shown in VRoid's panel.

def _slider_values(data):
    vals = {}

    def walk(s, e, depth):
        kids = list(fields(data, s, e))
        vid = None
        off = None
        for fn, wt, v, (fs, vs, ve) in kids:
            if fn == 1 and wt == 2:
                t = _str(data, vs, ve)
                if t and _is_hex_id(t, 40):
                    vid = t
            elif fn == 3 and wt == 5:
                off = vs
        if vid and off is not None and len(kids) <= 3:
            vals[vid] = off
            return
        for fn, wt, v, (fs, vs, ve) in kids:
            if wt == 2 and depth < 10 and ve - vs > 16 and data[vs:vs + 8] != PNG_SIG and is_message(data, vs, ve):
                walk(vs, ve, depth + 1)

    walk(0, len(data), 0)
    return vals


def _short(name):
    return name.rsplit(".", 1)[-1]


def _item_slider_slots(data, texture_id):
    vals = _slider_values(data)
    for (s, e), params in _recipe_blocks(data):
        if any(p.get("texture") == texture_id and p["name"].endswith("_MainImage") for p in params):
            out = {}
            for p in params:
                if p.get("slider") in vals:
                    off = vals[p["slider"]]
                    out[_short(p["name"])] = {"name": p["name"], "offset": off,
                                              "value": round(struct.unpack_from("<f", data, off)[0], 4)}
            return out
    raise KeyError(f"no item uses texture {texture_id} as its main image")


def item_sliders(path, texture_id) -> dict:
    """Shape sliders of the garment whose main image is texture_id, as {short_name: value}."""
    _, data = load(path)
    return {k: v["value"] for k, v in _item_slider_slots(data, texture_id).items()}


def set_item_sliders(src, dst, texture_id, values: dict) -> dict:
    """Write dst = src with garment sliders changed, e.g. {"AllVolume": 10}. Only clothing items are
    reachable here (the lookup goes through a garment texture), so body, face and hair stay locked."""
    src, dst = _check_dst(src, dst)
    members, data = load(src)
    slots = _item_slider_slots(data, texture_id)
    buf = bytearray(data)
    changed = {}
    for key, val in values.items():
        hit = slots.get(key) or next((v for v in slots.values() if v["name"] == key), None)
        if not hit:
            raise KeyError(f"{key!r} not found; available: {sorted(slots)}")
        val = float(val)
        if not -1000.0 <= val <= 1000.0:
            raise ValueError(f"{key}={val} is outside -1000..1000")
        struct.pack_into("<f", buf, hit["offset"], val)
        changed[key] = {"old": hit["value"], "new": val}
    new_data = bytes(buf)
    if len(textures(new_data)) != len(textures(data)):
        raise AssertionError("verification failed")
    _write_copy(src, dst, members, new_data)
    return {"written": str(dst), "changed": changed}
