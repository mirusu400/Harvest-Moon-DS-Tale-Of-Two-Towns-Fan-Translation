#!/usr/bin/env python3
import argparse
import json
import math
from pathlib import Path
from typing import List, Sequence, Tuple

from ttot_common import HavContainer, decode_palette

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Pillow is required: pip install pillow") from exc


def decode_tiles_4bpp(blob: bytes) -> List[List[int]]:
    tiles = []
    for offset in range(0, len(blob), 32):
        tile_blob = blob[offset : offset + 32]
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
            raise ValueError("Each tile must contain 64 pixels")
        for i in range(0, 64, 2):
            out.append((tile[i] & 0x0F) | ((tile[i + 1] & 0x0F) << 4))
    return bytes(out)


def grayscale_palette() -> List[int]:
    pal = []
    for i in range(16):
        value = round(i * 255 / 15)
        pal.extend((value, value, value))
    while len(pal) < 256 * 3:
        pal.extend((0, 0, 0))
    return pal


def render_glyphsheet(tiles: Sequence[Sequence[int]], columns: int, cell_tiles_x: int, cell_tiles_y: int) -> Tuple[Image.Image, int]:
    columns = max(1, columns)
    tiles_per_glyph = max(1, cell_tiles_x * cell_tiles_y)
    glyph_count = math.ceil(len(tiles) / tiles_per_glyph)
    rows = math.ceil(max(1, glyph_count) / columns)
    image = Image.new("P", (columns * cell_tiles_x * 8, rows * cell_tiles_y * 8))
    image.putpalette(grayscale_palette())
    pixels = image.load()
    for glyph_index in range(glyph_count):
        gx = (glyph_index % columns) * cell_tiles_x * 8
        gy = (glyph_index // columns) * cell_tiles_y * 8
        for ty in range(cell_tiles_y):
            for tx in range(cell_tiles_x):
                tile_index = glyph_index * tiles_per_glyph + ty * cell_tiles_x + tx
                if tile_index >= len(tiles):
                    continue
                tile = tiles[tile_index]
                ox = gx + tx * 8
                oy = gy + ty * 8
                for py in range(8):
                    for px in range(8):
                        pixels[ox + px, oy + py] = tile[py * 8 + px]
    return image, glyph_count


def export_font(args: argparse.Namespace) -> None:
    container = HavContainer.load(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    tile_blob = container.entries[args.tiles_index].data
    tiles = decode_tiles_4bpp(tile_blob)
    image, glyph_count = render_glyphsheet(tiles, args.columns, args.cell_tiles_x, args.cell_tiles_y)
    image.save(out_dir / "font_tiles_edit.png")

    manifest = {
        "source_file": Path(args.input).name,
        "tiles_index": args.tiles_index,
        "palette_index": args.palette_index,
        "meta_index": args.meta_index,
        "tile_count": len(tiles),
        "glyph_count": glyph_count,
        "columns": args.columns,
        "cell_tiles_x": args.cell_tiles_x,
        "cell_tiles_y": args.cell_tiles_y,
        "image_width": image.width,
        "image_height": image.height,
    }
    (out_dir / "font_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.export_palette_preview:
        palette_colors = decode_palette(container.entries[args.palette_index].data)
        preview = Image.new("P", (16 * 8, max(1, math.ceil(len(palette_colors) / 16)) * 8))
        flat_palette = []
        for color in palette_colors[:256]:
            flat_palette.extend(color[:3])
        while len(flat_palette) < 256 * 3:
            flat_palette.extend((0, 0, 0))
        preview.putpalette(flat_palette)
        pixels = preview.load()
        for idx in range(len(palette_colors)):
            ox = (idx % 16) * 8
            oy = (idx // 16) * 8
            for y in range(8):
                for x in range(8):
                    pixels[ox + x, oy + y] = idx
        preview.save(out_dir / "font_palette_preview.png")


def nearest_gray_index(rgb: Tuple[int, int, int]) -> int:
    gray = round((rgb[0] + rgb[1] + rgb[2]) / 3)
    return max(0, min(15, round(gray * 15 / 255)))


def import_font(args: argparse.Namespace) -> None:
    manifest = json.loads(Path(args.input).joinpath("font_manifest.json").read_text(encoding="utf-8"))
    container = HavContainer.load(args.source)
    image = Image.open(Path(args.input) / "font_tiles_edit.png")
    expected_size = (manifest["image_width"], manifest["image_height"])
    if image.size != expected_size:
        raise ValueError(f"Font PNG size mismatch: got {image.size}, expected {expected_size}")

    rgb = image.convert("RGB")
    pixels = rgb.load()
    tiles = []
    tiles_per_glyph = int(manifest["cell_tiles_x"]) * int(manifest["cell_tiles_y"])
    for glyph_index in range(int(manifest["glyph_count"])):
        gx = (glyph_index % int(manifest["columns"])) * int(manifest["cell_tiles_x"]) * 8
        gy = (glyph_index // int(manifest["columns"])) * int(manifest["cell_tiles_y"]) * 8
        for ty in range(int(manifest["cell_tiles_y"])):
            for tx in range(int(manifest["cell_tiles_x"])):
                tile_index = glyph_index * tiles_per_glyph + ty * int(manifest["cell_tiles_x"]) + tx
                if tile_index >= int(manifest["tile_count"]):
                    continue
                tile = []
                ox = gx + tx * 8
                oy = gy + ty * 8
                for py in range(8):
                    for px in range(8):
                        tile.append(nearest_gray_index(pixels[ox + px, oy + py]))
                tiles.append(tile)

    blobs = [entry.data for entry in container.entries]
    blobs[int(manifest["tiles_index"])] = encode_tiles_4bpp(tiles)
    container.save(args.output, blobs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Harvest Moon DS: Tale of Two Towns font 4bpp editor helper")
    sub = parser.add_subparsers(dest="command", required=True)

    export_cmd = sub.add_parser("export", help="Export font tiles as an editable 4bpp grayscale PNG")
    export_cmd.add_argument("input", help="Input font_data.bin")
    export_cmd.add_argument("output", help="Output directory")
    export_cmd.add_argument("--tiles-index", type=int, default=6, help="Tile chunk index")
    export_cmd.add_argument("--palette-index", type=int, default=0, help="Palette chunk index")
    export_cmd.add_argument("--meta-index", type=int, default=5, help="Metadata chunk index")
    export_cmd.add_argument("--columns", type=int, default=16, help="Glyph columns in output sheet")
    export_cmd.add_argument("--cell-tiles-x", type=int, default=2, help="Tiles per glyph horizontally")
    export_cmd.add_argument("--cell-tiles-y", type=int, default=2, help="Tiles per glyph vertically")
    export_cmd.add_argument("--export-palette-preview", action="store_true", help="Also export original palette preview")
    export_cmd.set_defaults(func=export_font)

    import_cmd = sub.add_parser("import", help="Import edited font tiles back into font_data.bin")
    import_cmd.add_argument("source", help="Original font_data.bin")
    import_cmd.add_argument("input", help="Directory created by export")
    import_cmd.add_argument("output", help="Output font_data.bin")
    import_cmd.set_defaults(func=import_font)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
