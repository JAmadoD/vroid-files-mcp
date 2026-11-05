# vroid-files MCP server

This MCP server reads and patches VRoid Studio 2.x `.vroid` files directly. VRoid has no API or plugin system, so the server works on the files, not on the running app. Edit with the server, then open the new file in VRoid to see the result.

- Every write creates a new `.vroid` file. The source is never changed.
- Each write is checked before it is saved: same parameters, same value entries, same textures.

## Tools

| Tool | What it does |
|---|---|
| `vroid_info` | Shows the VRoid version, the zip members and the texture and layer counts. |
| `vroid_list_textures` | Lists every texture layer: texture_id, size, used_as (recipe slot), layer_guid, layer_name, png_bytes. With `compare_to`, each row also says whether the layer is new or changed. |
| `vroid_export_layer` | Saves one layer's PNG, byte for byte. |
| `vroid_export_textures` | Saves all layer PNGs, or only the new or changed ones when given `compare_to`. |
| `vroid_replace_layer` | Writes a new file with one layer's PNG replaced (same size), then re-reads it to verify. |
| `vroid_item_colors` | Reads a clothing item's material colours (Color, Shade, Outline, Overlay, Emission). |
| `vroid_set_item_colors` | Writes a new file with item colours changed, for example `{"MaterialShadeColor": "#CAB9C2"}`. |
| `vroid_list_params` | Lists every saved setting (several hundred on a dressed model): sliders, numbers, colours, flags, choices, images and templates, with current values. Filter by `contains`, `kind` or `group` (for example `Tops#2`, the second template in the Tops stack). |
| `vroid_set_params` | Writes a new file with any of those settings changed, for example `[{"group": "Tops#2", "name": "ArmShort", "value": 100}]`. |

## Setup

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Register it with your MCP client as a stdio server, for example in a `.mcp.json`:

```json
{
  "mcpServers": {
    "vroid-files": {
      "type": "stdio",
      "command": "C:\\path\\to\\vroid_mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\vroid_mcp\\server.py"]
    }
  }
}
```

### Optional settings (environment variables)

| Variable | Effect |
|---|---|
| `VROID_ALLOWED_ROOTS` | Folders the server may read and write, separated by `;`. Default: any absolute path. |
| `VROID_PROTECTED_NAMES` | Comma-separated name parts that are never written, for example `MY_BASE_MODEL`. |
| `VROID_LOCK_IDENTITY` | Set to `1` to lock face, body, hair and skin parameters. Changing them then needs `unlock_identity: true`. Useful when only outfits should change. |

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

The outfit file should wear a top made of at least two templates (for example shirt and jacket). `VROID_TEST_TMP` sets the folder for temporary output.
