# vroid-files MCP server

This MCP server reads and patches VRoid Studio 2.x `.vroid` files directly. VRoid has no API or plugin system, so the server works on the files, not on the running app. Edit with the server, then open the new file in VRoid to see the result.

- Every write creates a new `.vroid` file. The source is never changed.
- Each write is checked before it is saved: same parameters, same value entries, same textures.

## Tools

| Tool | What it does |
|---|---|
| `vroid_info` | Shows the VRoid version, the zip members and the texture and layer counts. |
| `vroid_list_textures` | Lists every texture layer: texture_id, size, used_as (recipe slot), layer_guid, layer_name, png_bytes. With `compare_to`, each row also says whether the layer is new or changed. |
| `vroid_export_layer` | Saves one layer's PNG, byte for byte, to a `.png` path. Will not replace an existing file unless `overwrite` is true. |
| `vroid_export_textures` | Saves all layer PNGs, or only the new or changed ones when given `compare_to`. Will not replace existing files unless `overwrite` is true. |
| `vroid_replace_layer` | Writes a new file with one layer's PNG replaced (same size), then re-reads it to verify. |
| `vroid_item_colors` | Reads a clothing item's material colours (Color, Shade, Outline, Overlay, Emission). |
| `vroid_set_item_colors` | Writes a new file with item colours changed, for example `{"MaterialShadeColor": "#CAB9C2"}`. |
| `vroid_list_params` | Lists every saved setting (several hundred on a dressed model): sliders, numbers, colours, flags, choices, images and templates, with current values. Filter by `contains`, `kind` or `group` (for example `Tops#2`, the second template in the Tops stack). |
| `vroid_set_params` | Writes a new file with any of those settings changed, for example `[{"group": "Tops#2", "name": "ArmShort", "value": 100}]`. |

## Status

Unofficial and not affiliated with VRoid or pixiv. The file format has no public spec; it was worked out from files saved by VRoid Studio 2.14 (see File format below). Other 2.x versions will probably work but are not tested. **Keep a backup of your models.** The server never changes a source file, but VRoid may refuse a file it does not like.

## Setup

You need Python 3.10 or newer.

### Quickest: run it with uv

With [uv](https://docs.astral.sh/uv/) installed, your MCP client can start the server straight from GitHub, with nothing to clone:

```json
{
  "mcpServers": {
    "vroid-files": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/JAmadoD/vroid-files-mcp@v0.3.0", "vroid-files-mcp"],
      "env": { "VROID_ALLOWED_ROOTS": "C:\\Users\\you\\Documents\\VRoid" }
    }
  }
}
```

### Or: clone and install

Windows:

```
git clone https://github.com/JAmadoD/vroid-files-mcp
cd vroid-files-mcp
python -m venv .venv
.venv\Scripts\python -m pip install .
```

Mac and Linux:

```
git clone https://github.com/JAmadoD/vroid-files-mcp
cd vroid-files-mcp
python3 -m venv .venv
.venv/bin/python -m pip install .
```

This installs a `vroid-files-mcp` command into the venv (`.venv\Scripts\vroid-files-mcp.exe` on Windows, `.venv/bin/vroid-files-mcp` on Mac and Linux). Running `python server.py` from the checkout also works.

### Register it with your MCP client

The server speaks MCP over stdio. Use the full path to the command from the step above.

**Claude Code** (from a terminal):

```
claude mcp add vroid-files -e VROID_ALLOWED_ROOTS=C:\Users\you\Documents\VRoid -- C:\path\to\vroid-files-mcp\.venv\Scripts\vroid-files-mcp.exe
```

**Claude Desktop, Cursor, VS Code and other clients** take the same JSON shape in their MCP settings file (for Claude Desktop that is `claude_desktop_config.json`; for a project it is often `.mcp.json`):

```json
{
  "mcpServers": {
    "vroid-files": {
      "command": "C:\\path\\to\\vroid-files-mcp\\.venv\\Scripts\\vroid-files-mcp.exe",
      "env": { "VROID_ALLOWED_ROOTS": "C:\\Users\\you\\Documents\\VRoid" }
    }
  }
}
```

### Settings (environment variables)

Set `VROID_ALLOWED_ROOTS` first. The server refuses every call until it is set, and it never reads or writes outside those folders.

| Variable | Effect |
|---|---|
| `VROID_ALLOWED_ROOTS` | Required. Folders the server may read and write, separated by `;`. |
| `VROID_PROTECTED_NAMES` | Comma-separated name parts that are never written, for example `MY_BASE_MODEL`. |
| `VROID_LOCK_IDENTITY` | Set to `1` to lock face, body, hair and skin parameters. Changing them then needs `unlock_identity: true`. Useful when only outfits should change. |

### Safety rules built in

- Every write creates a new `.vroid` file. The source is never changed, and an existing file is never replaced.
- Exports only write `.png` files and never replace an existing file unless `overwrite` is true. Even then, only files inside `VROID_ALLOWED_ROOTS` can be replaced.
- Each write is re-read and checked before it is kept.
- Files that unpack to more than 1 GB are refused.

Found a security problem? See [SECURITY.md](SECURITY.md).

## Typical loop

1. Put the garment on in VRoid and save the file.
2. Run `vroid_list_textures` with `compare_to` set to the model without the garment. This shows the new garment's layers.
3. Export a layer and edit the PNG at the same size.
4. Replace the layer into a new file.
5. Change colours or sliders with `vroid_set_item_colors` or `vroid_set_params`.
6. Open the new file in VRoid: model selection screen, then Open.

## File format (found on VRoid 2.14 files)

- **Zip contents:** `meta.json`, `v1model/meta.json`, `v1model/data.bin` (protobuf, no public schema) and `thumbnails/thumbnail.jpg`.
- **Texture entry:** `{1 id (40 hex), 6 {1 width, 2 height, 3 {1 layer...}}}`.
- **Layer:** `{1 guid, 2 name, 7 {2 PNG}}`.
- **Texture IDs:** not hashes of the image, so a PNG can be swapped and the ID kept.
- **Recipe parameters:** top-level field 5, one message per part. Each parameter is `{1 name, K {1 value_id or literal}}`, where K is 3 choice, 4 colour, 5 slider, 6 image, 7 flag, 8 number or 9 template.
- **Values:** stored in each part's record as entries `{1 value_id, 2 '', 3 value}`. Record field 2 holds colours (`{1 r, 2 g, 3 b, 4 a}` floats), field 3 numbers (sliders use the number shown in VRoid), field 9 flags (varint).
- **Zero values are left out.** A slider at 0 is an entry with no field 3. Writes rebuild the whole entry in place. Appending a second entry with the same id makes VRoid refuse the file.
- **Not mapped yet:** item thumbnails, adding new layers, and adding or removing garments (pick those in VRoid, then tune them here).

## Verified in VRoid 2.14

- A recoloured clothing layer and a changed shade colour open and show correctly.
- A slider stored as a default (jacket Sleeve (Shorter), 0 to 100) loads, shows 100 in the panel and shortens the sleeve.

## Tests

The tests use your own files, read-only, and skip when they are not set:

```
set VROID_TEST_BASE=C:\path\to\model.vroid
set VROID_TEST_OUTFIT=C:\path\to\model_with_jacket.vroid
.venv\Scripts\python -m unittest test_vroidfile -v
```

On Mac and Linux, use `export VROID_TEST_BASE=...` and `.venv/bin/python`.

The outfit file should wear a top made of at least two templates (for example shirt and jacket). `VROID_TEST_TMP` sets the folder for temporary output.

## Command-line helper

`client.py` calls the server the same way an MCP client does, which is handy for scripts and quick checks. Put the tool arguments in a JSON file:

```
python client.py list
python client.py vroid_info @args.json
```

where `args.json` holds, for example, `{"path": "C:\\models\\my_model.vroid"}`.

## License

Free and source available under the AmadOpenSource License 1.0 (see [LICENSE](LICENSE)). In short: you may use it for anything, including in your business and to make money, and anything you make with it is yours. You may not sell the software itself, charge others to use or host it, or turn it into a paid or closed product. Free changed versions are welcome if they keep this license, publish their full source and credit "Based on vroid-files-mcp by Joshua Amado".

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
