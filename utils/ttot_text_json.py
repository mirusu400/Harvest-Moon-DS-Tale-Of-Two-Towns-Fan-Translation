#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from ttot_common import HavContainer, TableCodec, build_script_blob, decode_script, is_probable_script_chunk


def parse_pointer_size(value: str):
    if value == "auto":
        return None
    return int(value)


def export_container(args: argparse.Namespace) -> None:
    table = TableCodec(args.table)
    container = HavContainer.load(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "source_file": Path(args.input).name,
        "table_file": str(Path(args.table).name),
        "entry_count": len(container.entries),
        "chunks": [],
    }

    for entry in container.entries:
        chunk_name = f"{Path(args.input).stem}_{entry.index:04d}.json"
        json_path = out_dir / chunk_name
        pointer_size = parse_pointer_size(args.pointer_size)
        if is_probable_script_chunk(entry.data, pointer_size):
            decoded = decode_script(entry.data, table, pointer_size)
            payload = {
                "source_file": chunk_name.replace(".json", ".hav"),
                "container_index": entry.index,
                "entry_count": len(decoded["entries"]),
                "pointer_size": decoded["pointer_size"],
                "null_chunk": decoded["null_chunk"],
                "entries": decoded["entries"],
            }
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest["chunks"].append(
                {
                    "index": entry.index,
                    "kind": "script",
                    "json_file": chunk_name,
                    "original_size": entry.size,
                }
            )
        else:
            raw_name = f"{Path(args.input).stem}_{entry.index:04d}.hav"
            (out_dir / raw_name).write_bytes(entry.data)
            manifest["chunks"].append(
                {
                    "index": entry.index,
                    "kind": "raw",
                    "raw_file": raw_name,
                    "original_size": entry.size,
                }
            )

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def import_container(args: argparse.Namespace) -> None:
    table = TableCodec(args.table)
    manifest = json.loads(Path(args.input).joinpath("manifest.json").read_text(encoding="utf-8"))
    source_container = HavContainer.load(args.source)
    forced_pointer_size = parse_pointer_size(args.pointer_size)

    blobs = []
    for chunk in manifest["chunks"]:
        if chunk["kind"] == "raw":
            blob = Path(args.input).joinpath(chunk["raw_file"]).read_bytes()
        else:
            payload = json.loads(Path(args.input).joinpath(chunk["json_file"]).read_text(encoding="utf-8"))
            if payload.get("null_chunk"):
                blob = b"\x0F\x27\x00\x00"
            else:
                pointer_size = forced_pointer_size or int(payload.get("pointer_size", 2))
                blob = build_script_blob(
                    payload["entries"],
                    table,
                    strict_controls=not args.no_strict_controls,
                    pointer_size=pointer_size,
                )
        blobs.append(blob)

    source_container.save(args.output, blobs)


def validate_json(args: argparse.Namespace) -> None:
    table = TableCodec(args.table)
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if payload.get("null_chunk"):
        print("null chunk")
        return
    forced_pointer_size = parse_pointer_size(args.pointer_size)
    pointer_size = forced_pointer_size or int(payload.get("pointer_size", 2))
    blob = build_script_blob(
        payload["entries"],
        table,
        strict_controls=not args.no_strict_controls,
        pointer_size=pointer_size,
    )
    print(f"ok: {len(payload['entries'])} entries, {len(blob)} bytes, pointer_size={pointer_size}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Harvest Moon DS: Tale of Two Towns text JSON extractor/importer",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export_cmd = sub.add_parser("export", help="Export a text container to translation JSON files")
    export_cmd.add_argument("input", help="Input container, e.g. rom/root/mes_data.bin")
    export_cmd.add_argument("output", help="Output directory")
    export_cmd.add_argument("--table", default="Jtable.tbl", help="Table file")
    export_cmd.add_argument("--pointer-size", choices=("auto", "2", "4"), default="auto", help="Force script pointer size")
    export_cmd.set_defaults(func=export_container)

    import_cmd = sub.add_parser("import", help="Import translated JSON files back into a container")
    import_cmd.add_argument("source", help="Original source container, used for header shape")
    import_cmd.add_argument("input", help="Directory created by export")
    import_cmd.add_argument("output", help="Output container path")
    import_cmd.add_argument("--table", default="Jtable.tbl", help="Table file")
    import_cmd.add_argument("--pointer-size", choices=("auto", "2", "4"), default="auto", help="Force script pointer size")
    import_cmd.add_argument("--no-strict-controls", action="store_true", help="Allow control token changes")
    import_cmd.set_defaults(func=import_container)

    validate_cmd = sub.add_parser("validate", help="Validate a single exported JSON file")
    validate_cmd.add_argument("input", help="JSON file to validate")
    validate_cmd.add_argument("--table", default="Jtable.tbl", help="Table file")
    validate_cmd.add_argument("--pointer-size", choices=("auto", "2", "4"), default="auto", help="Force script pointer size")
    validate_cmd.add_argument("--no-strict-controls", action="store_true", help="Allow control token changes")
    validate_cmd.set_defaults(func=validate_json)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
