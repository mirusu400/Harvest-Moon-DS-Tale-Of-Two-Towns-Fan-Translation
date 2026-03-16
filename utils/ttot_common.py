import json
import math
import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


CONTROL_TOKEN_RE = re.compile(r"\[RAW:([0-9A-Fa-f\- ]+)\]")


def read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def read_pointer(data: bytes, offset: int, pointer_size: int) -> int:
    if pointer_size == 2:
        return read_u16(data, offset)
    if pointer_size == 4:
        return read_u32(data, offset)
    raise ValueError(f"Unsupported pointer size: {pointer_size}")


def align(value: int, multiple: int) -> int:
    remainder = value % multiple
    return value if remainder == 0 else value + (multiple - remainder)


@dataclass
class ContainerEntry:
    index: int
    offset: int
    size: int
    data: bytes


class HavContainer:
    def __init__(self, path: Path, entries: Sequence[ContainerEntry]):
        self.path = Path(path)
        self.entries = list(entries)

    @classmethod
    def load(cls, path: os.PathLike) -> "HavContainer":
        path = Path(path)
        blob = path.read_bytes()
        if len(blob) < 4:
            raise ValueError(f"Container too small: {path}")
        count = read_u32(blob, 0)
        table_size = 4 + count * 8
        if table_size > len(blob):
            raise ValueError(f"Broken entry table: {path}")
        entries: List[ContainerEntry] = []
        for index in range(count):
            off = read_u32(blob, 4 + index * 8)
            size = read_u32(blob, 8 + index * 8)
            start = table_size + off
            end = start + size
            if end > len(blob):
                raise ValueError(f"Entry {index} out of range in {path}")
            entries.append(ContainerEntry(index=index, offset=off, size=size, data=blob[start:end]))
        return cls(path, entries)

    def save(self, path: os.PathLike, entry_blobs: Sequence[bytes]) -> None:
        path = Path(path)
        if len(entry_blobs) != len(self.entries):
            raise ValueError("Entry count mismatch while repacking container")
        pointer = 0
        header = bytearray()
        header += struct.pack("<I", len(entry_blobs))
        payload = bytearray()
        for blob in entry_blobs:
            header += struct.pack("<II", pointer, len(blob))
            payload += blob
            padding = align(len(blob), 4) - len(blob)
            if padding:
                payload += b"\x00" * padding
            pointer += len(blob) + padding
        path.write_bytes(bytes(header + payload))

    def export_raw(self, out_dir: os.PathLike) -> None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "source_file": self.path.name,
            "entry_count": len(self.entries),
            "entries": [],
        }
        for entry in self.entries:
            name = f"{self.path.stem}_{entry.index:04d}.hav"
            (out_dir / name).write_bytes(entry.data)
            manifest["entries"].append(
                {
                    "index": entry.index,
                    "name": name,
                    "size": entry.size,
                }
            )
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class TableCodec:
    def __init__(self, table_path: os.PathLike):
        self.table_path = Path(table_path)
        self.code_to_text: Dict[str, str] = {}
        self.text_to_code: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        raw = self.table_path.read_bytes()
        text: Optional[str] = None
        for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "cp932"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError(f"Unsupported table encoding: {self.table_path}")

        for line in text.splitlines():
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().upper()
            if not key:
                continue
            self.code_to_text[key] = value
            if value not in self.text_to_code:
                self.text_to_code[value] = key

    def decode_unit(self, code: str) -> Optional[str]:
        return self.code_to_text.get(code.upper())

    def encode_char(self, char: str) -> Optional[str]:
        return self.text_to_code.get(char)


def is_probable_script_chunk_with_size(blob: bytes, pointer_size: int) -> bool:
    if len(blob) < 2:
        return False
    if blob == b"\x0F\x27\x00\x00":
        return True
    if pointer_size not in (2, 4):
        return False
    if len(blob) < pointer_size * 2:
        return False
    first = read_pointer(blob, 0, pointer_size)
    if first == 0 or first % pointer_size != 0 or first > len(blob):
        return False
    count = first // pointer_size
    if count == 0 or count * pointer_size > len(blob):
        return False
    pointers = [read_pointer(blob, i * pointer_size, pointer_size) for i in range(count)]
    if pointers[0] != first:
        return False
    prev = first
    for ptr in pointers:
        if ptr < prev or ptr > len(blob):
            return False
        prev = ptr
    return True


def detect_script_pointer_size(blob: bytes, preferred: Optional[int] = None) -> Optional[int]:
    if blob == b"\x0F\x27\x00\x00":
        return preferred if preferred in (2, 4) else 2
    if preferred in (2, 4):
        return preferred if is_probable_script_chunk_with_size(blob, preferred) else None
    for pointer_size in (2, 4):
        if is_probable_script_chunk_with_size(blob, pointer_size):
            return pointer_size
    return None


def is_probable_script_chunk(blob: bytes, preferred: Optional[int] = None) -> bool:
    return detect_script_pointer_size(blob, preferred) is not None


def decode_script(blob: bytes, table: TableCodec, pointer_size: Optional[int] = None) -> Dict[str, object]:
    actual_pointer_size = detect_script_pointer_size(blob, pointer_size)
    if actual_pointer_size is None:
        raise ValueError("Chunk does not look like a text script")
    if blob == b"\x0F\x27\x00\x00":
        return {
            "pointer_size": actual_pointer_size,
            "null_chunk": True,
            "entries": [],
        }
    first = read_pointer(blob, 0, actual_pointer_size)
    count = first // actual_pointer_size
    pointers = [read_pointer(blob, i * actual_pointer_size, actual_pointer_size) for i in range(count)]
    entries = []
    for index, start in enumerate(pointers):
        end = pointers[index + 1] if index + 1 < count else len(blob)
        body = blob[start:end]
        units = [body[i : i + 2] for i in range(0, len(body), 2) if len(body[i : i + 2]) == 2]
        chunks: List[str] = []
        pending_raw: List[str] = []
        controls: List[str] = []
        for unit in units:
            code = unit.hex().upper()
            decoded = table.decode_unit(code)
            if decoded is None:
                pending_raw.append(code)
                continue
            if pending_raw:
                token = "[RAW:" + "-".join(pending_raw) + "]"
                chunks.append(token)
                controls.append(token)
                pending_raw = []
            chunks.append(decoded)
        if pending_raw:
            token = "[RAW:" + "-".join(pending_raw) + "]"
            chunks.append(token)
            controls.append(token)
        message = "".join(chunks)
        entries.append(
            {
                "index": index,
                "name": str(index),
                "message": message,
                "original": message,
                "translation": "",
                "controls": controls,
            }
        )
    return {
        "pointer_size": actual_pointer_size,
        "null_chunk": False,
        "entries": entries,
    }


def encode_text(text: str, table: TableCodec) -> bytes:
    output = bytearray()
    pos = 0
    while pos < len(text):
        match = CONTROL_TOKEN_RE.match(text, pos)
        if match:
            raw = re.sub(r"[^0-9A-Fa-f]", "", match.group(1))
            if len(raw) % 4 != 0:
                raise ValueError(f"Control token length must be 2-byte aligned: {match.group(0)}")
            output += bytes.fromhex(raw)
            pos = match.end()
            continue
        code = table.encode_char(text[pos])
        if code is None:
            raise ValueError(f"Character not found in table: {text[pos]!r}")
        output += bytes.fromhex(code)
        pos += 1
    return bytes(output)


def extract_control_tokens(text: str) -> List[str]:
    return [match.group(0) for match in CONTROL_TOKEN_RE.finditer(text)]


def build_script_blob(
    entries: Sequence[Dict[str, object]],
    table: TableCodec,
    strict_controls: bool,
    pointer_size: int = 2,
) -> bytes:
    if pointer_size not in (2, 4):
        raise ValueError(f"Unsupported pointer size: {pointer_size}")
    encoded_entries: List[bytes] = []
    pointers: List[int] = []
    pointer = len(entries) * pointer_size
    for entry in entries:
        original = str(entry.get("original", ""))
        translation = str(entry.get("translation", "") or original)
        if strict_controls:
            source_controls = extract_control_tokens(original)
            target_controls = extract_control_tokens(translation)
            if source_controls != target_controls:
                raise ValueError(
                    f"Control token mismatch in entry {entry.get('name', entry.get('index', '?'))}: "
                    f"{source_controls} != {target_controls}"
                )
        encoded = encode_text(translation, table)
        pointers.append(pointer)
        encoded_entries.append(encoded)
        pointer += len(encoded)
    blob = bytearray()
    for ptr in pointers:
        blob += struct.pack("<H", ptr) if pointer_size == 2 else struct.pack("<I", ptr)
    for encoded in encoded_entries:
        blob += encoded
    return bytes(blob)


def bgr555_to_rgba(color: int) -> Tuple[int, int, int, int]:
    r = (color & 0x1F) * 255 // 31
    g = ((color >> 5) & 0x1F) * 255 // 31
    b = ((color >> 10) & 0x1F) * 255 // 31
    a = 0 if color == 0 else 255
    return (r, g, b, a)


def rgba_to_bgr555(r: int, g: int, b: int) -> int:
    r5 = round(r * 31 / 255) & 0x1F
    g5 = round(g * 31 / 255) & 0x1F
    b5 = round(b * 31 / 255) & 0x1F
    return r5 | (g5 << 5) | (b5 << 10)


def decode_palette(blob: bytes) -> List[Tuple[int, int, int, int]]:
    if len(blob) % 2 != 0:
        raise ValueError("Palette size must be even")
    colors = []
    for i in range(0, len(blob), 2):
        value = struct.unpack_from("<H", blob, i)[0]
        colors.append(bgr555_to_rgba(value))
    return colors


def encode_palette(colors: Sequence[Tuple[int, int, int, int]]) -> bytes:
    out = bytearray()
    for r, g, b, _a in colors:
        out += struct.pack("<H", rgba_to_bgr555(r, g, b))
    return bytes(out)


def guess_map_dimensions(entry_count: int) -> Tuple[int, int]:
    common = [(32, 32), (64, 32), (32, 64), (64, 64), (32, 16), (16, 32)]
    for width, height in common:
        if width * height == entry_count:
            return width, height
    root = int(math.sqrt(entry_count))
    if root * root == entry_count:
        return root, root
    return entry_count, 1
