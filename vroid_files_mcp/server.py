"""VRoid file MCP server (stdio).

Works on VRoid Studio 2.x .vroid FILES, not on the running app: VRoid has no API, so this server reads
and writes the file format directly. Every write goes to a NEW .vroid file; sources are never changed.
Open the written copy in VRoid (Open on the model selection screen) to see the result.

Run:  vroid-files-mcp   (after pip install), or python -m vroid_files_mcp, or python server.py from the repo

Optional environment variables:
  VROID_ALLOWED_ROOTS    folders the server may read and write, separated by ';' (default: any absolute path)
  VROID_PROTECTED_NAMES  comma-separated name parts that are never written, e.g. MY_BASE_MODEL
  VROID_LOCK_IDENTITY=1  face, body, hair and skin parameters need unlock_identity=true
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import __version__, recipe
from . import vroidfile as vf

ALLOWED_ROOTS = [r.strip() for r in os.environ.get("VROID_ALLOWED_ROOTS", "").split(";") if r.strip()]

server = MCPServer(
    name="vroid-files",
    version=__version__,
    instructions=(
        "Read and patch VRoid Studio 2.x .vroid files. Writes always create a new .vroid next to or away from "
        "the source; the source is never changed and protected file names are never written. "
        "Typical loop: vroid_list_textures (compare_to the base file to see what an outfit added), "
        "vroid_export_layer, edit the PNG at the same size, vroid_replace_layer into a new file, "
        "vroid_set_item_colors for material colours, vroid_list_params and vroid_set_params for every slider, colour, "
        "flag and choice VRoid saves (face, body, hair, each garment), then open the new file in VRoid to check. "
        "When the identity lock is on, face, body, hair and skin parameters need unlock_identity=true. "
        "All paths must be absolute."
    ),
)


def _p(path: str, must_exist: bool = True) -> Path:
    p = Path(path)
    if not p.is_absolute():
        raise ValueError(f"use an absolute path: {path}")
    p = p.resolve()
    if ALLOWED_ROOTS and not any(p.is_relative_to(Path(r).resolve()) for r in ALLOWED_ROOTS):
        raise ValueError(f"path is outside VROID_ALLOWED_ROOTS: {p}")
    if must_exist and not p.exists():
        raise FileNotFoundError(str(p))
    return p


@server.tool()
def vroid_info(path: str) -> dict:
    """Summary of a .vroid file: VRoid version, zip members, texture and layer counts."""
    return vf.info(_p(path))


@server.tool()
def vroid_list_textures(path: str, compare_to: str | None = None) -> list:
    """Every texture layer: texture_id, size, used_as (recipe slot names), layer_guid, layer_name, png_bytes.
    With compare_to (e.g. the base .vroid), each row also says whether its image is new or changed."""
    return vf.list_textures(_p(path), _p(compare_to) if compare_to else None)


@server.tool()
def vroid_export_layer(path: str, layer_guid: str, out_png: str, overwrite: bool = False) -> dict:
    """Save one layer's PNG (exact bytes) to out_png, which must end in .png.
    An existing file is never replaced unless overwrite is true."""
    return vf.export_layer(_p(path), layer_guid, _p(out_png, must_exist=False), overwrite)


@server.tool()
def vroid_export_textures(path: str, out_dir: str, compare_to: str | None = None, overwrite: bool = False) -> list:
    """Save every layer PNG into out_dir (only new or changed ones when compare_to is given).
    Existing files are never replaced unless overwrite is true."""
    return vf.export_all(_p(path), _p(out_dir, must_exist=False), _p(compare_to) if compare_to else None, overwrite)


@server.tool()
def vroid_replace_layer(src: str, dst: str, layer_guid: str, png_path: str) -> dict:
    """Write a NEW file dst = src with one layer's image replaced by png_path (same width and height).
    Verifies the result by re-reading it. dst must not exist."""
    return vf.replace_layer(_p(src), _p(dst, must_exist=False), layer_guid, _p(png_path))


@server.tool()
def vroid_item_colors(path: str, texture_id: str) -> dict:
    """Material colours (RGBA 0..1) of the clothing item whose main image is texture_id:
    MaterialColor, MaterialShadeColor, MaterialOutlineColor, OverlayColor (and Emission when present)."""
    return vf.item_colors(_p(path), texture_id)


@server.tool()
def vroid_set_item_colors(src: str, dst: str, texture_id: str, colors: dict) -> dict:
    """Write a NEW file dst = src with item colours changed. colors maps a slot to a hex colour, e.g.
    {"MaterialShadeColor": "#CAB9C2", "MaterialOutlineColor": "#46172080"}. dst must not exist."""
    return vf.set_item_colors(_p(src), _p(dst, must_exist=False), texture_id, colors)


@server.tool()
def vroid_list_params(path: str, contains: str | None = None, kind: str | None = None,
                      group: str | None = None) -> list:
    """Every recipe parameter VRoid saves, with its current value: sliders (the number shown in VRoid's panel),
    numbers, colours (RGBA 0..1), flags, choices (text), images and templates. Filter with contains (part of a
    name or group), kind (slider, number, color, flag, choice, image, template) or group (e.g. "Tops#2": the
    second template in the Tops stack). locked=true marks face, body, hair and skin parameters while the identity lock is on."""
    return recipe.list_params(_p(path), contains, kind, group)


@server.tool()
def vroid_set_params(src: str, dst: str, changes: list, unlock_identity: bool = False) -> dict:
    """Write a NEW file dst = src with parameters changed. changes: [{"name": "ArmShort", "group": "Tops#2",
    "value": 100}]; name may be the full name or its last part, group is needed when the name is not unique.
    Values: sliders and numbers a number, flags true/false, colours "#RRGGBB" or "#RRGGBBAA", choices text.
    With the identity lock on, face, body, hair and skin parameters need unlock_identity=true.
    The result is verified (same parameters, same value entries, same textures) before it is written."""
    return recipe.set_params(_p(src), _p(dst, must_exist=False), changes, unlock_identity)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
