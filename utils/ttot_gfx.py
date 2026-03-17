#!/usr/bin/env python3
import argparse
import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from ttot_common import HavContainer, decode_palette, encode_palette, guess_map_dimensions

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Pillow is required: pip install pillow") from exc


def decode_tiles_4bpp(blob: bytes) -> List[List[int]]:
    tiles = []
    for tile_index in range(0, len(blob), 32):
        tile_blob = blob[tile_index : tile_index + 32]
        if len(tile_blob) < 32:
            break
        pixels = []
        for byte in tile_blob:
            pixels.append(byte & 0x0F)
            pixels.append((byte >> 4) & 0x0F)
        tiles.append(pixels)
    return tiles


def encode_tiles_4bpp(tiles: Sequence[Sequence[int]]) -> bytes:
    out = bytearray()
    for tile in tiles:
        if len(tile) != 64:
            raise ValueError("Each 4bpp tile must have 64 pixels")
        for i in range(0, 64, 2):
            out.append((tile[i] & 0x0F) | ((tile[i + 1] & 0x0F) << 4))
    return bytes(out)


def decode_tiles_8bpp(blob: bytes) -> List[List[int]]:
    tiles = []
    for tile_index in range(0, len(blob), 64):
        tile_blob = blob[tile_index : tile_index + 64]
        if len(tile_blob) < 64:
            break
        tiles.append(list(tile_blob))
    return tiles


def encode_tiles_8bpp(tiles: Sequence[Sequence[int]]) -> bytes:
    out = bytearray()
    for tile in tiles:
        if len(tile) != 64:
            raise ValueError("Each 8bpp tile must have 64 pixels")
        out += bytes(tile)
    return bytes(out)


def sheet_dimensions(tile_count: int, max_width_tiles: int = 32) -> Tuple[int, int]:
    width_tiles = min(max_width_tiles, max(1, math.ceil(math.sqrt(tile_count))))
    while tile_count % width_tiles != 0 and width_tiles > 1:
        width_tiles -= 1
    height_tiles = math.ceil(tile_count / width_tiles)
    return width_tiles, height_tiles


def render_tilesheet(tile_blob: bytes, palette_blob: bytes, bpp: int, out_png: Path) -> Dict[str, object]:
    tiles = decode_tiles_4bpp(tile_blob) if bpp == 4 else decode_tiles_8bpp(tile_blob)
    colors = decode_palette(palette_blob)
    width_tiles, height_tiles = sheet_dimensions(max(1, len(tiles)))
    image = Image.new("P", (width_tiles * 8, height_tiles * 8))
    flat_palette: List[int] = []
    for color in colors[:256]:
        flat_palette.extend(color[:3])
    while len(flat_palette) < 256 * 3:
        flat_palette.extend((0, 0, 0))
    image.putpalette(flat_palette)
    pixels = image.load()
    for index, tile in enumerate(tiles):
        tx = (index % width_tiles) * 8
        ty = (index // width_tiles) * 8
        for py in range(8):
            for px in range(8):
                pixels[tx + px, ty + py] = tile[py * 8 + px]
    image.save(out_png)
    return {
        "tile_count": len(tiles),
        "width_tiles": width_tiles,
        "height_tiles": height_tiles,
        "bpp": bpp,
        "palette_colors": len(colors),
    }


XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def excel_col_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref)
    if not match:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + (ord(char) - 64)
    return value - 1


def load_map_xlsx(path: Path) -> List[Dict[str, object]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{XLSX_NS}si"):
                shared_strings.append("".join(text.text or "" for text in item.findall(f".//{XLSX_NS}t")))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall(f"{PKG_REL_NS}Relationship")}
        first_sheet = workbook.find(f"{XLSX_NS}sheets")[0]
        target = "xl/" + rel_map[first_sheet.attrib[XLSX_REL]]
        sheet = ET.fromstring(archive.read(target))
        rows = []
        for row in sheet.find(f"{XLSX_NS}sheetData"):
            cells: Dict[int, str] = {}
            for cell in row.findall(f"{XLSX_NS}c"):
                idx = excel_col_index(cell.attrib.get("r", "A1"))
                value = ""
                kind = cell.attrib.get("t")
                v = cell.find(f"{XLSX_NS}v")
                if kind == "s" and v is not None:
                    value = shared_strings[int(v.text)]
                elif kind == "inlineStr":
                    value = "".join(text.text or "" for text in cell.findall(f".//{XLSX_NS}t"))
                elif v is not None and v.text is not None:
                    value = v.text
                cells[idx] = value
            if not cells:
                continue
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])

    header = rows[0]
    out = []
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        item = {header[i]: row[i] if i < len(row) else "" for i in range(len(header))}
        out.append(item)
    return out


def resolve_map_xlsx(input_path: Path, explicit: str) -> Path:
    if explicit:
        return Path(explicit)
    stem = input_path.stem
    if "console_bg_data" in stem:
        return Path("console_bg_data_map.xlsx")
    if "console_obj_data" in stem:
        return Path("console_obj_data_map.xlsx")
    raise ValueError("No default xlsx map is known for this container; pass --map-xlsx")


def row_type(row: Dict[str, object]) -> str:
    return str(row.get("DATA_TYPE", "")).strip()


def row_desc(row: Dict[str, object]) -> str:
    return str(row.get("DATA_DESCRIPTION", "")).strip()


def row_index(row: Dict[str, object]) -> int:
    return int(str(row.get("DATA_INDEX", "0")).strip())


def render_map(tile_blob: bytes, palette_blob: bytes, map_blob: bytes, bpp: int, out_png: Path) -> Dict[str, object]:
    if bpp == 4:
        tiles = decode_tiles_4bpp(tile_blob)
        palette_size = len(palette_blob) // 2
        palette_sets = max(1, palette_size // 16)
    elif bpp == 8:
        tiles = decode_tiles_8bpp(tile_blob)
        palette_sets = 1
    else:
        raise ValueError("Only 4bpp and 8bpp are supported")

    colors = decode_palette(palette_blob)
    entry_count = len(map_blob) // 2
    width_tiles, height_tiles = guess_map_dimensions(entry_count)
    image = Image.new("P", (width_tiles * 8, height_tiles * 8))

    flat_palette: List[int] = []
    for color in colors[:256]:
        flat_palette.extend(color[:3])
    while len(flat_palette) < 256 * 3:
        flat_palette.extend((0, 0, 0))
    image.putpalette(flat_palette)

    out_pixels = image.load()
    for tile_y in range(height_tiles):
        for tile_x in range(width_tiles):
            offset = (tile_y * width_tiles + tile_x) * 2
            value = int.from_bytes(map_blob[offset : offset + 2], "little")
            tile_index = value & 0x03FF
            hflip = bool(value & 0x0400)
            vflip = bool(value & 0x0800)
            palette_index = (value >> 12) & 0x0F
            if tile_index >= len(tiles):
                continue
            tile = tiles[tile_index]
            for py in range(8):
                sy = 7 - py if vflip else py
                for px in range(8):
                    sx = 7 - px if hflip else px
                    pixel = tile[sy * 8 + sx]
                    if bpp == 4:
                        pixel += (palette_index % palette_sets) * 16
                    out_pixels[tile_x * 8 + px, tile_y * 8 + py] = pixel
    image.save(out_png)
    return {
        "width_tiles": width_tiles,
        "height_tiles": height_tiles,
        "bpp": bpp,
        "tile_count": len(tiles),
        "palette_colors": len(colors),
    }


def split_image_to_tiles(image: Image.Image, bpp: int) -> List[List[int]]:
    width, height = image.size
    if width % 8 != 0 or height % 8 != 0:
        raise ValueError("Image size must be aligned to 8x8 tiles")
    pixels = image.load()
    tiles: List[List[int]] = []
    for tile_y in range(0, height, 8):
        for tile_x in range(0, width, 8):
            tile: List[int] = []
            for py in range(8):
                for px in range(8):
                    value = int(pixels[tile_x + px, tile_y + py])
                    if bpp == 4 and value > 15:
                        raise ValueError("4bpp import expects palette indices 0..15 per tile before map palette offset")
                    tile.append(value)
            tiles.append(tile)
    return tiles


def scan_container(args: argparse.Namespace) -> None:
    container = HavContainer.load(args.input)
    rows = []
    for entry in container.entries:
        hints = []
        if entry.size % 2 == 0 and entry.size in (0x20, 0x40, 0x80, 0x100, 0x180, 0x200):
            hints.append("palette?")
        if entry.size % 32 == 0:
            hints.append("4bpp-tiles?")
        if entry.size % 64 == 0:
            hints.append("8bpp-tiles?")
        if entry.size % 2 == 0 and (entry.size // 2) in (256, 512, 1024, 2048, 4096):
            hints.append("map?")
        rows.append(
            {
                "index": entry.index,
                "size": entry.size,
                "hints": hints,
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def is_valid_bg_combo(map_blob: bytes, tile_blob: bytes, palette_blob: bytes, bpp: int) -> bool:
    if len(map_blob) % 2 != 0:
        return False
    if bpp == 4:
        if len(tile_blob) % 32 != 0 or len(palette_blob) < 32 or len(palette_blob) % 2 != 0:
            return False
        tile_count = len(tile_blob) // 32
    else:
        if len(tile_blob) % 64 != 0 or len(palette_blob) < 512 or len(palette_blob) % 2 != 0:
            return False
        tile_count = len(tile_blob) // 64
    palette_colors = len(palette_blob) // 2
    if tile_count == 0:
        return False
    for offset in range(0, len(map_blob), 2):
        value = int.from_bytes(map_blob[offset : offset + 2], "little")
        tile_index = value & 0x03FF
        palette_index = (value >> 12) & 0x0F
        if tile_index >= tile_count:
            return False
        if bpp == 4 and palette_index * 16 >= palette_colors:
            return False
    return True


def pick_bg_asset_for_map(container: HavContainer, map_index: int, graphic_indices: Sequence[int], palette_indices: Sequence[int]) -> Dict[str, object]:
    best = None
    for graphic_index in graphic_indices:
        tile_blob = container.entries[graphic_index].data
        for palette_index in palette_indices:
            palette_blob = container.entries[palette_index].data
            for bpp in (4, 8):
                if not is_valid_bg_combo(container.entries[map_index].data, tile_blob, palette_blob, bpp):
                    continue
                distance = abs(graphic_index - map_index) * 4 + abs(palette_index - graphic_index)
                score = (
                    distance,
                    0 if palette_index > graphic_index >= map_index else 1,
                    abs(palette_index - map_index),
                )
                candidate = {
                    "name": f"bg_{map_index:04d}",
                    "kind": "bg",
                    "map_index": map_index,
                    "tiles_index": graphic_index,
                    "palette_index": palette_index,
                    "bpp": bpp,
                    "png": f"assets/bg_{map_index:04d}.png",
                    "meta": f"assets/bg_{map_index:04d}.json",
                    "score": score,
                }
                if best is None or candidate["score"] < best["score"]:
                    best = candidate
    if best is None:
        return {}
    best.pop("score", None)
    return best


def detect_bg_assets_from_rows(container: HavContainer, rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    graphics = [row_index(row) for row in rows if row_type(row) == "Graphic"]
    palettes = [row_index(row) for row in rows if row_type(row) == "Palette or Dummy"]
    assets: List[Dict[str, object]] = []
    for row in rows:
        if row_type(row) != "MAP":
            continue
        idx = row_index(row)
        local_graphics = [x for x in graphics if abs(x - idx) <= 24] or graphics
        local_palettes = [x for x in palettes if abs(x - idx) <= 24] or palettes
        asset = pick_bg_asset_for_map(container, idx, local_graphics, local_palettes)
        if asset:
            asset["description"] = row_desc(row)
            assets.append(asset)
    return assets


def parse_obj_meta(meta_blob: bytes) -> Dict[str, object]:
    if len(meta_blob) < 6:
        return {"record_end": 0, "records": []}

    record_end = int.from_bytes(meta_blob[0:2], "little")
    offsets: List[int] = []
    pos = 4
    while pos + 1 < len(meta_blob):
        value = int.from_bytes(meta_blob[pos : pos + 2], "little")
        if offsets and value < offsets[-1]:
            break
        if value >= len(meta_blob):
            break
        offsets.append(value)
        pos += 2
        if len(offsets) > 1024:
            break

    records = []
    for offset in offsets:
        actual = offset + 4
        if actual + 2 > len(meta_blob):
            continue
        count = int.from_bytes(meta_blob[actual : actual + 2], "little")
        if count > 128 or actual + 2 + count * 12 > len(meta_blob):
            continue
        entries = []
        for index in range(count):
            base = actual + 2 + index * 12
            fields = [int.from_bytes(meta_blob[base + i * 2 : base + i * 2 + 2], "little") for i in range(6)]
            signed = [field - 0x10000 if field >= 0x8000 else field for field in fields]
            entries.append(
                {
                    "x": signed[0],
                    "y": signed[1],
                    "tile": fields[2],
                    "attr3": fields[3],
                    "flags": fields[4],
                    "palette": fields[5],
                    "raw": fields,
                }
            )
        records.append(
            {
                "offset": offset,
                "prefix": meta_blob[offset : offset + 4].hex(),
                "entry_count": count,
                "entries": entries,
            }
        )
    return {
        "record_end": record_end,
        "records": records,
    }


def obj_dimensions_from_flags(flags: int) -> Tuple[int, int]:
    size_class = (flags >> 8) & 0xFF
    shape = flags & 0xFF
    square = {0: (8, 8), 1: (16, 16), 2: (32, 32), 3: (64, 64)}
    horizontal = {0: (16, 8), 1: (32, 8), 2: (32, 16), 3: (64, 32)}
    vertical = {0: (8, 16), 1: (8, 32), 2: (16, 32), 3: (32, 64)}
    if shape == 0:
        return square.get(size_class, (8, 8))
    if shape == 1:
        return horizontal.get(size_class, (16, 8))
    if shape == 2:
        return vertical.get(size_class, (8, 16))
    return (8, 8)


def blit_obj_tile_block(target, target_w: int, target_h: int, tiles: Sequence[Sequence[int]], entry: Dict[str, object], palette_base: int) -> None:
    width, height = obj_dimensions_from_flags(int(entry["flags"]))
    hflip = False
    vflip = False
    tiles_x = width // 8
    tiles_y = height // 8
    tile_index = int(entry["tile"])
    sx = int(entry["x"])
    sy = int(entry["y"])
    for ty in range(tiles_y):
        for tx in range(tiles_x):
            source_index = tile_index + ty * tiles_x + tx
            if source_index >= len(tiles):
                continue
            tile = tiles[source_index]
            draw_x = sx + tx * 8
            draw_y = sy + ty * 8
            for py in range(8):
                src_py = 7 - py if vflip else py
                for px in range(8):
                    src_px = 7 - px if hflip else px
                    px_index = tile[src_py * 8 + src_px] + palette_base
                    rx = draw_x + px
                    ry = draw_y + py
                    if 0 <= rx < target_w and 0 <= ry < target_h:
                        target[rx, ry] = px_index


def obj_record_bounds(record: Dict[str, object]) -> Tuple[int, int, int, int]:
    if not record["entries"]:
        return (0, 0, 8, 8)
    min_x = None
    min_y = None
    max_x = None
    max_y = None
    for entry in record["entries"]:
        width, height = obj_dimensions_from_flags(int(entry["flags"]))
        x = int(entry["x"])
        y = int(entry["y"])
        min_x = x if min_x is None else min(min_x, x)
        min_y = y if min_y is None else min(min_y, y)
        max_x = x + width if max_x is None else max(max_x, x + width)
        max_y = y + height if max_y is None else max(max_y, y + height)
    return (min_x or 0, min_y or 0, max_x or 8, max_y or 8)


def render_obj_record(tiles: Sequence[Sequence[int]], record: Dict[str, object], palette_count: int, out_palette_pixels: int = 256) -> Image.Image:
    min_x, min_y, max_x, max_y = obj_record_bounds(record)
    width = max(8, max_x - min_x)
    height = max(8, max_y - min_y)
    image = Image.new("P", (width, height))
    pixels = image.load()
    for entry in record["entries"]:
        palette_index = int(entry["palette"])
        if palette_count > 0:
            palette_index %= palette_count
        palette_base = palette_index * 16
        shifted = dict(entry)
        shifted["x"] = int(entry["x"]) - min_x
        shifted["y"] = int(entry["y"]) - min_y
        blit_obj_tile_block(pixels, width, height, tiles, shifted, palette_base)
    return image


def build_obj_preview_layout(meta: Dict[str, object]) -> Dict[str, object]:
    cells = meta["records"]
    bounds = [obj_record_bounds(record) for record in cells]
    widths = [max(8, max_x - min_x) for (min_x, min_y, max_x, max_y) in bounds]
    heights = [max(8, max_y - min_y) for (min_x, min_y, max_x, max_y) in bounds]
    cell_w = max(widths, default=8) + 4
    cell_h = max(heights, default=8) + 4
    max_preview_width = 1024
    max_cols = max(1, min(6, max_preview_width // max(1, cell_w)))
    cols = max(1, min(max_cols, math.ceil(math.sqrt(max(1, len(cells))))))
    rows = math.ceil(max(1, len(cells)) / cols)
    return {
        "bounds": bounds,
        "widths": widths,
        "heights": heights,
        "cols": cols,
        "rows": rows,
        "cell_w": cell_w,
        "cell_h": cell_h,
    }


def save_palette_to_image(image: Image.Image, colors: Sequence[Tuple[int, int, int, int]]) -> None:
    flat_palette: List[int] = []
    for color in colors[:256]:
        flat_palette.extend(color[:3])
    while len(flat_palette) < 256 * 3:
        flat_palette.extend((0, 0, 0))
    image.putpalette(flat_palette)


def render_obj_preview(
    tile_blob: bytes,
    palette_blob: bytes,
    meta_blob: bytes,
    bpp: int,
    out_png: Path,
    meta_json_path: Path,
    parts_dir: Path,
) -> Dict[str, object]:
    tiles = decode_tiles_4bpp(tile_blob) if bpp == 4 else decode_tiles_8bpp(tile_blob)
    colors = decode_palette(palette_blob)
    meta = parse_obj_meta(meta_blob)
    meta_json_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    cells = meta["records"]
    record_images = [render_obj_record(tiles, record, len(colors) // 16 if bpp == 4 else 1) for record in cells]
    parts_dir.mkdir(parents=True, exist_ok=True)
    for record_index, record_image in enumerate(record_images):
        save_palette_to_image(record_image, colors)
        record_image.save(parts_dir / f"record_{record_index:04d}.png")

    layout = build_obj_preview_layout(meta)
    cols = int(layout["cols"])
    rows = int(layout["rows"])
    cell_w = int(layout["cell_w"])
    cell_h = int(layout["cell_h"])
    image = Image.new("P", (cols * cell_w, rows * cell_h))
    save_palette_to_image(image, colors)
    pixels = image.load()

    for cell_index, record_image in enumerate(record_images):
        origin_x = (cell_index % cols) * cell_w + 2
        origin_y = (cell_index // cols) * cell_h + 2
        src = record_image.load()
        for y in range(record_image.height):
            for x in range(record_image.width):
                pixels[origin_x + x, origin_y + y] = int(src[x, y])

    image.save(out_png)
    return {
        "record_count": len(cells),
        "tile_count": len(tiles),
        "palette_colors": len(colors),
        "bpp": bpp,
        "cells_json": meta_json_path.name,
        "preview_parts_dir": parts_dir.name,
    }


def rebuild_obj_tiles_from_preview(tile_blob: bytes, palette_blob: bytes, meta_blob: bytes, bpp: int, png_path: Path) -> Tuple[bytes, bytes]:
    if bpp != 4:
        raise ValueError("Preview reimport currently supports 4bpp OBJ only")

    colors = decode_palette(palette_blob)
    meta = parse_obj_meta(meta_blob)
    layout = build_obj_preview_layout(meta)

    image = Image.open(png_path)
    rgb_image = image.convert("RGB")
    if image.mode != "P":
        paletted = Image.new("P", image.size)
        palette_data = [c for rgba in colors[:256] for c in rgba[:3]]
        palette_data += [0] * (256 * 3 - len(palette_data))
        paletted.putpalette(palette_data)
        out = paletted.load()
        src = rgb_image.load()
        for y in range(image.height):
            for x in range(image.width):
                out[x, y] = nearest_palette_index(src[x, y], colors)
        image = paletted

    expected_size = (int(layout["cols"]) * int(layout["cell_w"]), int(layout["rows"]) * int(layout["cell_h"]))
    if image.size != expected_size:
        raise ValueError(f"Preview PNG size mismatch: got {image.size}, expected {expected_size}")

    src_rgb = rgb_image.load()
    tiles = decode_tiles_4bpp(tile_blob)
    palette_count = max(1, (len(palette_blob) // 2) // 16)

    for record_index, record in enumerate(meta["records"]):
        min_x, min_y, max_x, max_y = layout["bounds"][record_index]
        origin_x = (record_index % int(layout["cols"])) * int(layout["cell_w"]) + 2
        origin_y = (record_index // int(layout["cols"])) * int(layout["cell_h"]) + 2
        for entry in record["entries"]:
            width, height = obj_dimensions_from_flags(int(entry["flags"]))
            tiles_x = width // 8
            tiles_y = height // 8
            tile_index = int(entry["tile"])
            palette_index = int(entry["palette"]) % palette_count
            palette_base = palette_index * 16
            local_x = int(entry["x"]) - min_x
            local_y = int(entry["y"]) - min_y
            for ty in range(tiles_y):
                for tx in range(tiles_x):
                    out_tile_index = tile_index + ty * tiles_x + tx
                    if out_tile_index >= len(tiles):
                        continue
                    tile_pixels: List[int] = []
                    for py in range(8):
                        for px in range(8):
                            pixel = nearest_palette_index_in_range(
                                src_rgb[origin_x + local_x + tx * 8 + px, origin_y + local_y + ty * 8 + py],
                                colors,
                                palette_base,
                                palette_base + 16,
                            )
                            tile_pixels.append(pixel - palette_base)
                    tiles[out_tile_index] = tile_pixels

    palette_limit = len(palette_blob) // 2
    raw_palette = image.getpalette()[: palette_limit * 3]
    new_palette = palette_blob
    if raw_palette:
        color_list = []
        for i in range(0, len(raw_palette), 3):
            color_list.append((raw_palette[i], raw_palette[i + 1], raw_palette[i + 2], 255))
        while len(color_list) < palette_limit:
            color_list.append((0, 0, 0, 0))
        new_palette = encode_palette(color_list[:palette_limit])

    return encode_tiles_4bpp(tiles), new_palette


def rebuild_obj_tiles_from_preview_parts(
    tile_blob: bytes,
    palette_blob: bytes,
    meta_blob: bytes,
    bpp: int,
    parts_dir: Path,
) -> Tuple[bytes, bytes]:
    if bpp != 4:
        raise ValueError("Preview-part reimport currently supports 4bpp OBJ only")

    colors = decode_palette(palette_blob)
    meta = parse_obj_meta(meta_blob)
    tiles = decode_tiles_4bpp(tile_blob)
    palette_count = max(1, (len(palette_blob) // 2) // 16)

    for record_index, record in enumerate(meta["records"]):
        part_path = parts_dir / f"record_{record_index:04d}.png"
        if not part_path.exists():
            continue
        image = Image.open(part_path)
        expected_width = max(8, obj_record_bounds(record)[2] - obj_record_bounds(record)[0])
        expected_height = max(8, obj_record_bounds(record)[3] - obj_record_bounds(record)[1])
        if image.size != (expected_width, expected_height):
            raise ValueError(
                f"Preview-part size mismatch for record {record_index}: got {image.size}, "
                f"expected {(expected_width, expected_height)}"
            )
        src_rgb = image.convert("RGB").load()
        min_x, min_y, _max_x, _max_y = obj_record_bounds(record)
        for entry in record["entries"]:
            width, height = obj_dimensions_from_flags(int(entry["flags"]))
            tiles_x = width // 8
            tiles_y = height // 8
            tile_index = int(entry["tile"])
            palette_index = int(entry["palette"]) % palette_count
            palette_base = palette_index * 16
            local_x = int(entry["x"]) - min_x
            local_y = int(entry["y"]) - min_y
            for ty in range(tiles_y):
                for tx in range(tiles_x):
                    out_tile_index = tile_index + ty * tiles_x + tx
                    if out_tile_index >= len(tiles):
                        continue
                    tile_pixels: List[int] = []
                    for py in range(8):
                        for px in range(8):
                            pixel = nearest_palette_index_in_range(
                                src_rgb[local_x + tx * 8 + px, local_y + ty * 8 + py],
                                colors,
                                palette_base,
                                palette_base + 16,
                            )
                            tile_pixels.append(pixel - palette_base)
                    tiles[out_tile_index] = tile_pixels

    return encode_tiles_4bpp(tiles), palette_blob


def is_valid_obj_combo(tile_blob: bytes, palette_blob: bytes, meta_blob: bytes) -> bool:
    if len(tile_blob) % 32 != 0:
        return False
    if len(palette_blob) < 32 or len(palette_blob) % 32 != 0:
        return False
    meta = parse_obj_meta(meta_blob)
    if not meta["records"]:
        return False
    palette_count = max(1, (len(palette_blob) // 2) // 16)
    tile_count = len(tile_blob) // 32
    valid_entries = 0
    for record in meta["records"]:
        for entry in record["entries"]:
            width, height = obj_dimensions_from_flags(int(entry["flags"]))
            tiles_used = (width * height) // 64
            if int(entry["tile"]) + tiles_used > tile_count:
                return False
            if int(entry["palette"]) >= palette_count:
                return False
            valid_entries += 1
    return valid_entries > 0


def detect_bg_assets(container: HavContainer) -> List[Dict[str, object]]:
    assets: List[Dict[str, object]] = []
    seen = set()
    for index in range(len(container.entries) - 2):
        map_entry = container.entries[index]
        tile_entry = container.entries[index + 1]
        palette_entry = container.entries[index + 2]
        for bpp in (4, 8):
            if not is_valid_bg_combo(map_entry.data, tile_entry.data, palette_entry.data, bpp):
                continue
            key = (index, index + 1, index + 2, bpp)
            if key in seen:
                continue
            seen.add(key)
            assets.append(
                {
                    "name": f"bg_{index:04d}",
                    "kind": "bg",
                    "map_index": index,
                    "tiles_index": index + 1,
                    "palette_index": index + 2,
                    "bpp": bpp,
                    "png": f"assets/bg_{index:04d}.png",
                    "meta": f"assets/bg_{index:04d}.json",
                }
            )
            break
    return assets


def detect_obj_assets(container: HavContainer) -> List[Dict[str, object]]:
    assets: List[Dict[str, object]] = []
    index = 0
    while index < len(container.entries) - 2:
        tiles = container.entries[index]
        palette = container.entries[index + 1]
        meta = container.entries[index + 2]
        if not is_valid_obj_combo(tiles.data, palette.data, meta.data):
            index += 1
            continue
        assets.append(
            {
                "name": f"obj_{index:04d}",
                "kind": "obj",
                "tiles_index": index,
                "palette_index": index + 1,
                "meta_index": index + 2,
                "bpp": 4,
                "png": f"assets/obj_{index:04d}_tiles.png",
                "preview_png": f"assets/obj_{index:04d}_preview.png",
                "preview_parts_dir": f"assets/obj_{index:04d}_preview_parts",
                "meta": f"assets/obj_{index:04d}.json",
                "cells_json": f"assets/obj_{index:04d}_cells.json",
                "meta_raw": f"raw/{container.path.stem}_{index + 2:04d}.hav",
            }
        )
        index += 3
    return assets


def detect_obj_assets_from_rows(container: HavContainer, rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    items = [(row_index(row), row_type(row), row_desc(row)) for row in rows]
    assets: List[Dict[str, object]] = []
    for pos, (idx, ty, desc) in enumerate(items):
        if ty != "Graphic":
            continue
        tile_entry = container.entries[idx]
        if len(tile_entry.data) % 32 != 0:
            continue
        palette_index = None
        meta_index = None
        for j in range(pos + 1, min(pos + 4, len(items))):
            n_idx, n_ty, _ = items[j]
            if palette_index is None and n_ty == "Palette or Dummy" and len(container.entries[n_idx].data) % 32 == 0:
                palette_index = n_idx
                continue
            if palette_index is not None:
                meta_index = n_idx
                break
        if palette_index is None or meta_index is None:
            continue
        if not is_valid_obj_combo(tile_entry.data, container.entries[palette_index].data, container.entries[meta_index].data):
            continue
        assets.append(
            {
                "name": f"obj_{idx:04d}",
                "kind": "obj",
                "tiles_index": idx,
                "palette_index": palette_index,
                "meta_index": meta_index,
                "bpp": 4,
                "png": f"assets/obj_{idx:04d}_tiles.png",
                "preview_png": f"assets/obj_{idx:04d}_preview.png",
                "preview_parts_dir": f"assets/obj_{idx:04d}_preview_parts",
                "meta": f"assets/obj_{idx:04d}.json",
                "cells_json": f"assets/obj_{idx:04d}_cells.json",
                "meta_raw": f"raw/{container.path.stem}_{meta_index:04d}.hav",
                "description": desc,
            }
        )
    return assets


def choose_batch_kind(args_kind: str, source_name: str) -> str:
    if args_kind != "auto":
        return args_kind
    if "obj" in source_name.lower():
        return "obj"
    return "bg"


def export_raw(args: argparse.Namespace) -> None:
    container = HavContainer.load(args.input)
    container.export_raw(args.output)


def batch_export(args: argparse.Namespace) -> None:
    container = HavContainer.load(args.input)
    out_dir = Path(args.output)
    raw_dir = out_dir / "raw"
    asset_dir = out_dir / "assets"
    raw_dir.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)
    container.export_raw(raw_dir)

    kind = choose_batch_kind(args.kind, Path(args.input).name)
    map_rows = None
    if args.map_xlsx:
        map_rows = load_map_xlsx(Path(args.map_xlsx))
    else:
        default_map = resolve_map_xlsx(Path(args.input), "")
        if default_map.exists():
            map_rows = load_map_xlsx(default_map)

    if kind == "obj":
        assets = detect_obj_assets_from_rows(container, map_rows) if map_rows else detect_obj_assets(container)
    else:
        assets = detect_bg_assets_from_rows(container, map_rows) if map_rows else detect_bg_assets(container)

    for asset in assets:
        png_path = out_dir / asset["png"]
        meta_path = out_dir / asset["meta"]
        if asset["kind"] == "bg":
            meta = render_map(
                container.entries[asset["tiles_index"]].data,
                container.entries[asset["palette_index"]].data,
                container.entries[asset["map_index"]].data,
                asset["bpp"],
                png_path,
            )
        else:
            render_tilesheet(
                container.entries[asset["tiles_index"]].data,
                container.entries[asset["palette_index"]].data,
                asset["bpp"],
                png_path,
            )
            meta = render_obj_preview(
                container.entries[asset["tiles_index"]].data,
                container.entries[asset["palette_index"]].data,
                container.entries[asset["meta_index"]].data,
                asset["bpp"],
                out_dir / asset["preview_png"],
                out_dir / asset["cells_json"],
                out_dir / asset["preview_parts_dir"],
            )
        meta.update(asset)
        meta["source_file"] = Path(args.input).name
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "source_file": Path(args.input).name,
        "kind": kind,
        "entry_count": len(container.entries),
        "assets": assets,
        "map_xlsx": args.map_xlsx or (str(resolve_map_xlsx(Path(args.input), "")) if map_rows else ""),
    }
    (out_dir / "batch_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def export_bg(args: argparse.Namespace) -> None:
    container = HavContainer.load(args.input)
    tile_blob = container.entries[args.tiles].data
    palette_blob = container.entries[args.palette].data
    map_blob = container.entries[args.map].data
    out_png = Path(args.output)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    meta = render_map(tile_blob, palette_blob, map_blob, args.bpp, out_png)
    meta.update(
        {
            "source_file": Path(args.input).name,
            "tiles_index": args.tiles,
            "palette_index": args.palette,
            "map_index": args.map,
        }
    )
    out_png.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def nearest_palette_index(color: Tuple[int, int, int], palette: Sequence[Tuple[int, int, int, int]]) -> int:
    best_index = 0
    best_distance = None
    for index, entry in enumerate(palette):
        dr = color[0] - entry[0]
        dg = color[1] - entry[1]
        db = color[2] - entry[2]
        dist = dr * dr + dg * dg + db * db
        if best_distance is None or dist < best_distance:
            best_distance = dist
            best_index = index
    return best_index


def nearest_palette_index_in_range(
    color: Tuple[int, int, int],
    palette: Sequence[Tuple[int, int, int, int]],
    start: int,
    end: int,
) -> int:
    best_index = start
    best_distance = None
    for index in range(start, min(end, len(palette))):
        entry = palette[index]
        dr = color[0] - entry[0]
        dg = color[1] - entry[1]
        db = color[2] - entry[2]
        dist = dr * dr + dg * dg + db * db
        if best_distance is None or dist < best_distance:
            best_distance = dist
            best_index = index
    return best_index


def rebuild_from_png(tile_blob: bytes, palette_blob: bytes, map_blob: bytes, bpp: int, png_path: Path) -> Tuple[bytes, bytes]:
    colors = decode_palette(palette_blob)
    image = Image.open(png_path)
    if image.mode != "P":
        rgb = image.convert("RGB")
        paletted = Image.new("P", image.size)
        paletted.putpalette([c for rgba in colors[:256] for c in rgba[:3]] + [0] * (256 * 3 - min(len(colors), 256) * 3))
        out = paletted.load()
        src = rgb.load()
        for y in range(image.height):
            for x in range(image.width):
                out[x, y] = nearest_palette_index(src[x, y], colors)
        image = paletted
    width_tiles = image.width // 8
    height_tiles = image.height // 8
    entry_count = len(map_blob) // 2
    expected_w, expected_h = guess_map_dimensions(entry_count)
    if (width_tiles, height_tiles) != (expected_w, expected_h):
        raise ValueError(f"PNG tile size mismatch: got {width_tiles}x{height_tiles}, expected {expected_w}x{expected_h}")

    image_pixels = image.load()
    tile_map: Dict[Tuple[int, ...], int] = {}
    tiles: List[List[int]] = []
    new_map = bytearray()
    for tile_y in range(height_tiles):
        for tile_x in range(width_tiles):
            original_value = int.from_bytes(map_blob[(tile_y * width_tiles + tile_x) * 2 : (tile_y * width_tiles + tile_x) * 2 + 2], "little")
            palette_index = (original_value >> 12) & 0x0F
            tile: List[int] = []
            for py in range(8):
                for px in range(8):
                    pixel = int(image_pixels[tile_x * 8 + px, tile_y * 8 + py])
                    if bpp == 4:
                        base = palette_index * 16
                        if pixel < base or pixel >= base + 16:
                            raise ValueError(
                                f"Pixel uses palette index {pixel} outside map cell palette window {base}..{base + 15} "
                                f"at tile ({tile_x}, {tile_y})"
                            )
                        pixel -= base
                    tile.append(pixel)
            key = tuple(tile)
            tile_index = tile_map.get(key)
            if tile_index is None:
                tile_index = len(tiles)
                tile_map[key] = tile_index
                tiles.append(tile)
            if tile_index > 0x03FF:
                raise ValueError("Tile count exceeds BG map tile index limit (1024)")
            new_value = (original_value & 0xFC00) | tile_index
            new_map += new_value.to_bytes(2, "little")

    if bpp == 4:
        return encode_tiles_4bpp(tiles), bytes(new_map)
    return encode_tiles_8bpp(tiles), bytes(new_map)


def rebuild_tilesheet_from_png(palette_blob: bytes, bpp: int, png_path: Path) -> Tuple[bytes, bytes]:
    colors = decode_palette(palette_blob)
    image = Image.open(png_path)
    if image.mode != "P":
        rgb = image.convert("RGB")
        paletted = Image.new("P", image.size)
        pal = [c for rgba in colors[:256] for c in rgba[:3]]
        pal += [0] * (256 * 3 - len(pal))
        paletted.putpalette(pal)
        out = paletted.load()
        src = rgb.load()
        for y in range(image.height):
            for x in range(image.width):
                out[x, y] = nearest_palette_index(src[x, y], colors)
        image = paletted

    tiles = split_image_to_tiles(image, bpp)
    new_palette = palette_blob
    palette_limit = len(palette_blob) // 2
    raw_palette = image.getpalette()[: palette_limit * 3]
    if raw_palette:
        color_list = []
        for i in range(0, len(raw_palette), 3):
            color_list.append((raw_palette[i], raw_palette[i + 1], raw_palette[i + 2], 255))
        while len(color_list) < palette_limit:
            color_list.append((0, 0, 0, 0))
        new_palette = encode_palette(color_list[:palette_limit])

    if bpp == 4:
        return encode_tiles_4bpp(tiles), new_palette
    return encode_tiles_8bpp(tiles), new_palette


def import_bg(args: argparse.Namespace) -> None:
    container = HavContainer.load(args.source)
    tile_blob = container.entries[args.tiles].data
    palette_blob = container.entries[args.palette].data
    map_blob = container.entries[args.map].data
    new_tiles, new_map = rebuild_from_png(tile_blob, palette_blob, map_blob, args.bpp, Path(args.png))

    blobs = [entry.data for entry in container.entries]
    blobs[args.tiles] = new_tiles
    blobs[args.map] = new_map
    if args.keep_palette:
        blobs[args.palette] = palette_blob
    else:
        image = Image.open(args.png).convert("P")
        raw_palette = image.getpalette()[: (len(palette_blob) // 2) * 3]
        colors = []
        for i in range(0, len(raw_palette), 3):
            colors.append((raw_palette[i], raw_palette[i + 1], raw_palette[i + 2], 255))
        while len(colors) < len(palette_blob) // 2:
            colors.append((0, 0, 0, 0))
        blobs[args.palette] = encode_palette(colors[: len(palette_blob) // 2])
    container.save(args.output, blobs)


def batch_import(args: argparse.Namespace) -> None:
    source = HavContainer.load(args.source)
    work_dir = Path(args.input)
    manifest = json.loads((work_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    raw_manifest = json.loads((work_dir / "raw" / "manifest.json").read_text(encoding="utf-8"))

    blobs = [entry.data for entry in source.entries]
    for item in raw_manifest["entries"]:
        raw_path = work_dir / "raw" / item["name"]
        if raw_path.exists():
            blobs[item["index"]] = raw_path.read_bytes()

    for asset in manifest["assets"]:
        png_path = work_dir / asset["png"]
        if not png_path.exists():
            continue
        if asset["kind"] == "bg":
            new_tiles, new_map = rebuild_from_png(
                blobs[asset["tiles_index"]],
                blobs[asset["palette_index"]],
                blobs[asset["map_index"]],
                asset["bpp"],
                png_path,
            )
            blobs[asset["tiles_index"]] = new_tiles
            blobs[asset["map_index"]] = new_map
            if not args.keep_palette:
                image = Image.open(png_path).convert("P")
                palette_limit = len(blobs[asset["palette_index"]]) // 2
                raw_palette = image.getpalette()[: palette_limit * 3]
                colors = []
                for i in range(0, len(raw_palette), 3):
                    colors.append((raw_palette[i], raw_palette[i + 1], raw_palette[i + 2], 255))
                while len(colors) < palette_limit:
                    colors.append((0, 0, 0, 0))
                blobs[asset["palette_index"]] = encode_palette(colors[:palette_limit])
        else:
            preview_path = work_dir / asset.get("preview_png", "")
            preview_parts_dir = work_dir / asset.get("preview_parts_dir", "")
            if asset.get("preview_parts_dir") and preview_parts_dir.exists():
                new_tiles, new_palette = rebuild_obj_tiles_from_preview_parts(
                    blobs[asset["tiles_index"]],
                    blobs[asset["palette_index"]],
                    blobs[asset["meta_index"]],
                    asset["bpp"],
                    preview_parts_dir,
                )
            elif asset.get("preview_png") and preview_path.exists():
                new_tiles, new_palette = rebuild_obj_tiles_from_preview(
                    blobs[asset["tiles_index"]],
                    blobs[asset["palette_index"]],
                    blobs[asset["meta_index"]],
                    asset["bpp"],
                    preview_path,
                )
            else:
                new_tiles, new_palette = rebuild_tilesheet_from_png(
                    blobs[asset["palette_index"]],
                    asset["bpp"],
                    png_path,
                )
            blobs[asset["tiles_index"]] = new_tiles
            if not args.keep_palette:
                blobs[asset["palette_index"]] = new_palette

    source.save(args.output, blobs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Harvest Moon DS: Tale of Two Towns graphics helper")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_cmd = sub.add_parser("scan", help="List chunk sizes and rough graphic hints")
    scan_cmd.add_argument("input", help="Input container")
    scan_cmd.set_defaults(func=scan_container)

    raw_cmd = sub.add_parser("export-raw", help="Export every chunk as raw .hav")
    raw_cmd.add_argument("input", help="Input container")
    raw_cmd.add_argument("output", help="Output directory")
    raw_cmd.set_defaults(func=export_raw)

    batch_export_cmd = sub.add_parser("batch-export", help="Batch export raw chunks and renderable assets")
    batch_export_cmd.add_argument("input", help="Input container")
    batch_export_cmd.add_argument("output", help="Output working directory")
    batch_export_cmd.add_argument("--kind", choices=("auto", "bg", "obj"), default="auto", help="Container kind")
    batch_export_cmd.add_argument("--map-xlsx", help="Excel map file, e.g. console_bg_data_map.xlsx")
    batch_export_cmd.set_defaults(func=batch_export)

    export_cmd = sub.add_parser("export-bg", help="Render a BG asset to PNG from selected chunks")
    export_cmd.add_argument("input", help="Input container")
    export_cmd.add_argument("output", help="Output PNG path")
    export_cmd.add_argument("--tiles", type=int, required=True, help="Tile chunk index")
    export_cmd.add_argument("--palette", type=int, required=True, help="Palette chunk index")
    export_cmd.add_argument("--map", type=int, required=True, help="Map chunk index")
    export_cmd.add_argument("--bpp", type=int, choices=(4, 8), required=True, help="Tile depth")
    export_cmd.set_defaults(func=export_bg)

    import_cmd = sub.add_parser("import-bg", help="Insert a modified PNG back into the container")
    import_cmd.add_argument("source", help="Original container")
    import_cmd.add_argument("png", help="Edited PNG path")
    import_cmd.add_argument("output", help="Output container")
    import_cmd.add_argument("--tiles", type=int, required=True, help="Tile chunk index")
    import_cmd.add_argument("--palette", type=int, required=True, help="Palette chunk index")
    import_cmd.add_argument("--map", type=int, required=True, help="Map chunk index")
    import_cmd.add_argument("--bpp", type=int, choices=(4, 8), required=True, help="Tile depth")
    import_cmd.add_argument("--keep-palette", action="store_true", help="Keep original palette chunk unchanged")
    import_cmd.set_defaults(func=import_bg)

    batch_import_cmd = sub.add_parser("batch-import", help="Batch import a working directory back into a container")
    batch_import_cmd.add_argument("source", help="Original source container")
    batch_import_cmd.add_argument("input", help="Directory created by batch-export")
    batch_import_cmd.add_argument("output", help="Output container")
    batch_import_cmd.add_argument("--keep-palette", action="store_true", help="Keep palette chunks unchanged")
    batch_import_cmd.set_defaults(func=batch_import)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
