"""The whole VRoid recipe: every face, body, hair, skin and clothing setting that VRoid saves.

Parameters (top-level field 5, one message per part): {1 name, K {1 value_id | literal}}, K = kind:
  3 choice (literal text, e.g. "armShort"), 4 colour, 5 slider, 6 image, 7 flag, 8 number, 9 template group.

Values sit in each part's record (top-level field 4 ... ) as entries {1 value_id, 2 '', 3 value}:
  record field 2 = colour entries   (3 = {1 r, 2 g, 3 b, 4 a} fixed32 floats)
  record field 3 = number entries   (3 = fixed32 float; sliders use the number shown in VRoid's panel)
  record field 9 = flag entries     (3 = varint)
Protobuf leaves out zero values, so a slider at 0 is an entry with no field 3, and a colour may miss
channels that are 0. Every write rebuilds the whole entry and fixes every enclosing length.
"""
from __future__ import annotations

import os
import re
import struct

from pbwire import enc_varint, fields, is_message
from vroidfile import (PNG_SIG, _check_dst, _hex_to_rgba, _is_hex_id, _replace, _str, _write_copy, load,
                       textures)

KINDS = {3: "choice", 4: "color", 5: "slider", 6: "image", 7: "flag", 8: "number", 9: "template"}
ENTRY_TYPES = {2: "color", 3: "float", 9: "flag"}

# Optional identity lock: with VROID_LOCK_IDENTITY=1, face, body, hair and skin parameters can only be changed
# with unlock_identity=True, so a character's look is not changed by accident while editing outfits.
LOCK_IDENTITY = os.environ.get("VROID_LOCK_IDENTITY", "0") == "1"
IDENTITY_PREFIXES = ("Level1BodySkin", "Level1FaceSkin", "Level1BaseHair", "Level1Eyebrow", "Level1Mouth",
                     "Level1MouthLine", "Level1Lip", "Level1Nose", "Level1NoseLighting", "Level2", "Level3",
                     "pixiv/VRoid/Body", "pixiv/VRoid/Skin", "pixiv/VRoid/FixedPreset")
_CAT = re.compile(r"N00_\d+_\d+_([A-Za-z]+)")


def _kids(data, s, e):
    try:
        return list(fields(data, s, e))
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------- values

def _decode_entry(data, etype, kids):
    for fn, wt, v, (fs, vs, ve) in kids:
        if fn != 3:
            continue
        if etype == "float" and wt == 5:
            return round(struct.unpack_from("<f", data, vs)[0], 5)
        if etype == "flag" and wt == 0:
            return v
        if etype == "color" and wt == 2:
            rgba = [0.0, 0.0, 0.0, 0.0]
            for f2, w2, v2, (a, c, d) in fields(data, vs, ve):
                if 1 <= f2 <= 4 and w2 == 5:
                    rgba[f2 - 1] = round(struct.unpack_from("<f", data, c)[0], 4)
            return rgba
    return {"float": 0.0, "flag": 0, "color": [0.0, 0.0, 0.0, 0.0]}[etype]


def value_table(data):
    """value_id -> list of {"type", "value", "entry": (value_start, value_end), "has_empty_2"}."""
    vals = {}

    def entry_kids(vs, ve):
        sub = _kids(data, vs, ve)
        if not sub or sub[0][0] != 1 or sub[0][1] != 2:
            return None
        vid = _str(data, sub[0][3][1], sub[0][3][2])
        if not vid or not _is_hex_id(vid, 40):
            return None
        for f, w, v, (a, c, d) in sub[1:]:
            if f not in (2, 3) or (f == 2 and (w != 2 or d != c)):
                return None
        return vid, sub

    def walk(s, e, depth):
        kids = _kids(data, s, e)
        if kids is None:
            return
        found = []
        for fn, wt, v, (fs, vs, ve) in kids:
            if fn in ENTRY_TYPES and wt == 2:
                hit = entry_kids(vs, ve)
                if hit:
                    found.append((fn, vs, ve, hit))
        if len(found) >= 2:  # a record: several value entries side by side
            for fn, vs, ve, (vid, sub) in found:
                etype = ENTRY_TYPES[fn]
                vals.setdefault(vid, []).append({
                    "type": etype, "value": _decode_entry(data, etype, sub), "entry": (vs, ve),
                    "has_empty_2": any(f == 2 for f, *_ in sub)})
        for fn, wt, v, (fs, vs, ve) in kids:
            if wt == 2 and depth < 10 and ve - vs > 60 and data[vs:vs + 8] != PNG_SIG and is_message(data, vs, ve):
                walk(vs, ve, depth + 1)

    walk(0, len(data), 0)
    return vals


def _encode_entry(etype, vid, value, has_empty_2):
    out = bytes([0x0A, 40]) + vid.encode("ascii") + (bytes([0x12, 0x00]) if has_empty_2 else b"")
    if etype == "float":
        out += bytes([0x1D]) + struct.pack("<f", float(value))
    elif etype == "flag":
        out += bytes([0x18]) + enc_varint(int(bool(value)) if isinstance(value, bool) else int(value))
    elif etype == "color":
        col = b"".join(bytes([0x0D + 8 * k]) + struct.pack("<f", x) for k, x in enumerate(_hex_to_rgba(str(value))))
        out += bytes([0x1A, len(col)]) + col
    return out


def _chain_to(data, start, end):
    """Spans from the top level down to the message whose value is exactly data[start:end]."""
    chain = []
    s, e = 0, len(data)
    while True:
        nxt = None
        for fn, wt, v, (fs, vs, ve) in fields(data, s, e):
            if wt == 2 and vs <= start and end <= ve:
                nxt = (fs, vs, ve)
                break
        if not nxt:
            raise AssertionError("value entry not found in the file")
        chain.append(nxt)
        if (nxt[1], nxt[2]) == (start, end):
            return chain
        s, e = nxt[1], nxt[2]


def _same(etype, a, b):
    if etype == "color":
        return all(abs(x - y) < 1e-3 for x, y in zip(a, [round(v, 4) for v in _hex_to_rgba(str(b))]))
    if etype == "flag":
        return int(a) == (int(bool(b)) if isinstance(b, bool) else int(b))
    return abs(float(a) - float(b)) < 1e-4


def _set_value(data, vid, value):
    """Rewrite every entry of vid (a value can be stored in more than one record)."""
    for _ in range(8):
        entries = value_table(data).get(vid)
        if not entries:
            raise ValueError(f"value {vid} has no entry")
        todo = [en for en in entries if not _same(en["type"], en["value"], value)]
        if not todo:
            return data
        en = todo[0]
        chain = _chain_to(data, *en["entry"])
        data = _replace(data, chain, _encode_entry(en["type"], vid, value, en["has_empty_2"]))
    raise AssertionError(f"value {vid} did not settle")


# ---------------------------------------------------------------- parameters

def blocks(data):
    """Recipe blocks: [{"category", "params": [{"name", "kind", "ref", "literal"}]}] in file order."""
    out = []

    def walk(s, e, chain, depth):
        found = []
        for fn, wt, v, (fs, vs, ve) in _kids(data, s, e) or []:
            if wt != 2 or data[vs:vs + 8] == PNG_SIG or ve - vs < 4 or not is_message(data, vs, ve):
                continue
            sub = _kids(data, vs, ve)
            if sub is None:
                continue
            name = next((_str(data, c, d) for f2, w2, _, (a, c, d) in sub if f2 == 1 and w2 == 2), None)
            kinds = [(f2, (a, c, d)) for f2, w2, _, (a, c, d) in sub if f2 in KINDS and w2 == 2]
            if name and kinds and not _is_hex_id(name, 40):
                k, kspan = kinds[0]
                ref = literal = None
                if is_message(data, kspan[1], kspan[2]):
                    for f3, w3, _, (a3, c3, d3) in fields(data, kspan[1], kspan[2]):
                        if f3 == 1 and w3 == 2:
                            t = _str(data, c3, d3)
                            if t and _is_hex_id(t, 40):
                                ref = t
                            elif t is not None:
                                literal = {"value": t, "chain": chain + [(fs, vs, ve), kspan, (a3, c3, d3)]}
                found.append({"name": name, "kind": KINDS[k], "ref": ref, "literal": literal})
            if depth < 10 and ve - vs > 16:
                walk(vs, ve, chain + [(fs, vs, ve)], depth + 1)
        if found:
            cats = [m.group(1) for p in found for m in [_CAT.search(p["name"])] if m]
            out.append({"category": cats[0] if cats else None, "params": found})

    for fn, wt, v, (fs, vs, ve) in fields(data):  # top-level field 5: one recipe message per part
        if fn == 5 and wt == 2:
            walk(vs, ve, [(fs, vs, ve)], 0)
    return out


def _label(block):
    if block["category"]:
        return block["category"]
    first = block["params"][0]["name"]
    return first.split("_", 1)[0].split("/")[-1].split(".")[0]


def indexed(data):
    """[(group, param)] with stable group names like Tops#1, Tops#2, Level2Mouth#1."""
    counts = {}
    out = []
    for block in blocks(data):
        lab = _label(block)
        counts[lab] = counts.get(lab, 0) + 1
        for p in block["params"]:
            out.append((f"{lab}#{counts[lab]}", p))
    return out


def is_identity(name):
    return name.startswith(IDENTITY_PREFIXES)


def locked(name):
    return LOCK_IDENTITY and is_identity(name)


def list_params(path, contains=None, kind=None, group=None) -> list:
    """Every recipe parameter with its current value. Defaults (not stored) read as 0."""
    _, data = load(path)
    vals = value_table(data)
    rows = []
    for g, p in indexed(data):
        if contains and contains.lower() not in p["name"].lower() and contains.lower() not in g.lower():
            continue
        if kind and p["kind"] != kind:
            continue
        if group and g != group:
            continue
        en = vals.get(p["ref"]) if p["ref"] else None
        value = en[0]["value"] if en else (p["literal"]["value"] if p["literal"] else None)
        rows.append({"group": g, "name": p["name"], "kind": p["kind"], "value": value,
                     "ref": p["ref"] if p["kind"] in ("image", "template") else None,
                     "editable": bool(en) or bool(p["literal"]), "locked": locked(p["name"])})
    return rows


def _find(idx, name, group):
    hits = [(g, p) for g, p in idx if (p["name"] == name or p["name"].endswith("." + name)
                                       or p["name"].endswith("_" + name)) and (group is None or g == group)]
    if len(hits) != 1:
        raise KeyError(f"{name!r} (group {group}) matches {len(hits)} parameters: {[g for g, _ in hits][:10]}")
    return hits[0]


def set_params(src, dst, changes, unlock_identity=False) -> dict:
    """changes: [{"name": "AllVolume", "group": "Tops#2" (optional if unique), "value": 10}].
    Sliders and numbers take a float, flags true/false, colours "#RRGGBB" or "#RRGGBBAA", choices text."""
    src, dst = _check_dst(src, dst)
    members, data = load(src)
    original_params = {(g, p["name"]) for g, p in indexed(data)}
    original_values = set(value_table(data))
    done = []
    for ch in changes:
        idx = indexed(data)
        g, p = _find(idx, ch["name"], ch.get("group"))
        if locked(p["name"]) and not unlock_identity:
            raise PermissionError(f"{p['name']} is part of the locked identity (face, body, hair, skin); "
                                  "pass unlock_identity=true to change it")
        new = ch["value"]
        vals = value_table(data)
        if p["ref"] and p["ref"] in vals:
            old = vals[p["ref"]][0]["value"]
            data = _set_value(data, p["ref"], new)
        elif p["literal"]:
            old = p["literal"]["value"]
            data = _replace(data, p["literal"]["chain"], str(new).encode("utf-8"))
        else:
            raise ValueError(f"{p['name']} ({p['kind']}) has no value entry this tool can change")
        done.append({"group": g, "name": p["name"], "old": old, "new": new})
    if {(g, p["name"]) for g, p in indexed(data)} != original_params:
        raise AssertionError("verification failed: recipe changed shape")
    if set(value_table(data)) != original_values:
        raise AssertionError("verification failed: value table changed shape")
    if len(textures(data)) != len(textures(load(src)[1])):
        raise AssertionError("verification failed: textures changed")
    _write_copy(src, dst, members, data)
    return {"written": str(dst), "changed": done}
