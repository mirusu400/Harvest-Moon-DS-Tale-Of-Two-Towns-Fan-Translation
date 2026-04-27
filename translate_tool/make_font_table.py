"""
make_font_table.py
한국어 번역에 필요한 글리프를 폰트 PNG에 렌더링하고 테이블 파일을 생성한다.

전략:
  - 히라가나/가타카나/한자 슬롯만 한국어로 교체
  - 특수문자/기호 슬롯은 원본 Jtable 그대로 보존 (재삽입 오류 방지)
  - Ktable = Jtable 기반 + 재활용 슬롯만 한국어로 덮어쓰기

출력:
  work/font_data/font_tiles_edit.png  (업데이트됨)
  Ktable.tbl                           (새 한국어 테이블, UTF-16 LE)

사용:
  python3 translate_tool/make_font_table.py [--font-path /path/to/font.ttf] [--font-size 11]
"""

import argparse
import glob
import json
import os
import re
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent.parent
JSON_DIR = ROOT / "work" / "event_mes_data_json"
FONT_PNG = ROOT / "work" / "font_data" / "font_tiles_edit.png"
TABLE_IN = ROOT / "Jtable.tbl"
TABLE_OUT = ROOT / "Ktable.tbl"

RAW_PATTERN = re.compile(r"\[RAW:[^\]]*\]")

GLYPH_W = 16
GLYPH_H = 16
SHEET_COLS = 16

DEFAULT_FONTS = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def is_kana_or_kanji(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch[0])
    return (
        0x3041 <= cp <= 0x309F  # 히라가나
        or 0x30A0 <= cp <= 0x30FF  # 가타카나
        or 0x4E00 <= cp <= 0x9FFF  # 한자 CJK
    )


def collect_needed_chars() -> set:
    chars = set()
    for fp in sorted(glob.glob(str(JSON_DIR / "*.json"))):
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("entries", []):
            text = entry.get("translation", "")
            cleaned = RAW_PATTERN.sub("", text)
            chars.update(ch for ch in cleaned if ch not in (" ", "\u3000", "\n", "\r", "\t"))
    return chars


def load_table(path: Path) -> dict:
    """Returns code_str → char (str)"""
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"Unsupported encoding: {path}")
    tbl = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        code, char = line.split("=", 1)
        code = code.strip().upper()
        if code:
            tbl[code] = char
    return tbl


def code_to_glyph_index(code_str: str) -> int:
    return struct.unpack_from("<H", bytes.fromhex(code_str))[0]


def glyph_index_to_code(idx: int) -> str:
    return struct.pack("<H", idx).hex().upper()


def render_char(char: str, font: ImageFont.FreeTypeFont) -> np.ndarray:
    img = Image.new("L", (GLYPH_W, GLYPH_H), 0)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), char, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    ox = (GLYPH_W - tw) // 2 - bbox[0]
    oy = (GLYPH_H - th) // 2 - bbox[1]
    draw.text((ox, oy), char, fill=255, font=font)
    arr = np.array(img, dtype=np.float32)
    if arr.max() > 0:
        arr = arr / arr.max() * 5.0
    return np.clip(np.round(arr), 0, 5).astype(np.uint8)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font-path", default=None)
    parser.add_argument("--font-size", type=int, default=13)
    parser.add_argument("--font-index", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="테이블만 출력, PNG 미수정")
    args = parser.parse_args()

    # 폰트 로드
    font_path = args.font_path
    if font_path is None:
        for c in DEFAULT_FONTS:
            if os.path.exists(c):
                font_path = c
                break
    if font_path is None:
        print("ERROR: 한국어 TTF 폰트 없음. --font-path 지정 필요.", file=sys.stderr)
        sys.exit(1)
    print(f"폰트: {font_path}  size={args.font_size}")
    pil_font = ImageFont.truetype(font_path, args.font_size, index=args.font_index)

    # 필요 글자 수집
    needed_chars = collect_needed_chars()
    print(f"번역 필요 글자: {len(needed_chars)}개")

    # 기존 Jtable 로드
    old_tbl = load_table(TABLE_IN)  # code → char
    old_char_to_code = {v: k for k, v in old_tbl.items() if v}

    # 이미 테이블에 있는 글자 (그대로 유지)
    already_mapped = {ch: old_char_to_code[ch] for ch in needed_chars if ch in old_char_to_code}

    # 새로 추가 필요한 글자
    new_chars = sorted(needed_chars - set(already_mapped.keys()), key=ord)

    # 재활용 대상: 한국어에 불필요한 카나/한자 슬롯만
    reusable_codes = sorted(
        [
            code
            for code, char in old_tbl.items()
            if char not in needed_chars and char != "" and is_kana_or_kanji(char)
        ],
        key=code_to_glyph_index,
    )

    print(f"이미 매핑됨: {len(already_mapped)}개")
    print(f"새로 필요: {len(new_chars)}개")
    print(f"재활용 가능 (카나+한자): {len(reusable_codes)}개")

    if len(new_chars) > len(reusable_codes):
        print(f"ERROR: 슬롯 부족 ({len(new_chars)} > {len(reusable_codes)})", file=sys.stderr)
        sys.exit(1)

    # 재활용 슬롯 → 새 한국어 글자 할당
    new_assignments: dict = {}  # code → char
    for char, code in zip(new_chars, reusable_codes):
        new_assignments[code] = char

    # Ktable 빌드:
    #   Jtable 전체를 베이스로 → 재활용 슬롯만 한국어로 교체
    new_tbl = dict(old_tbl)  # 특수문자 포함 모든 원본 엔트리 유지
    for code, char in new_assignments.items():
        new_tbl[code] = char  # 카나/한자 슬롯 덮어쓰기

    sorted_entries = sorted(new_tbl.items(), key=lambda kv: code_to_glyph_index(kv[0]))
    lines = [f"{code}={char}\r\n" for code, char in sorted_entries]
    TABLE_OUT.write_bytes(("\ufeff" + "".join(lines)).encode("utf-16-le"))
    print(f"Ktable 저장: {TABLE_OUT}  ({len(sorted_entries)}개 엔트리)")

    if args.dry_run:
        print("--dry-run: PNG 수정 건너뜀")
        return

    # 보호 대상 글리프 인덱스: Jtable 기준 카나/한자가 아닌 모든 슬롯
    # (숫자, 영어, 특수문자 → 원본 유지)
    protected_glyph_indices = {
        code_to_glyph_index(code)
        for code, char in old_tbl.items()
        if char and not is_kana_or_kanji(char)
    }
    print(f"보호 슬롯 (숫자/영어/특수문자): {len(protected_glyph_indices)}개")

    # 폰트 PNG: 재활용 슬롯만 렌더링, 보호 슬롯은 절대 건드리지 않음
    img = Image.open(FONT_PNG)
    arr = np.array(img)

    rendered = 0
    skipped_protected = 0
    for code, char in new_assignments.items():
        gidx = code_to_glyph_index(code)
        if gidx in protected_glyph_indices:
            skipped_protected += 1
            continue
        x = (gidx % SHEET_COLS) * GLYPH_W
        y = (gidx // SHEET_COLS) * GLYPH_H
        if y + GLYPH_H > arr.shape[0]:
            print(f"  SKIP glyph {gidx} out of bounds: {repr(char)}")
            continue
        arr[y:y + GLYPH_H, x:x + GLYPH_W] = render_char(char, pil_font)
        rendered += 1
    if skipped_protected:
        print(f"  보호 슬롯으로 렌더링 스킵: {skipped_protected}개")

    out_img = Image.fromarray(arr, mode="P")
    out_img.putpalette(img.getpalette())
    out_img.save(FONT_PNG)
    print(f"PNG 저장: {FONT_PNG}  (렌더링 {rendered}개)")
    print()
    print("다음 단계:")
    print("  python3 utils/ttot_font.py import rom/root/font_data.bin work/font_data work/font_data_patched.bin")
    print("  python3 utils/ttot_text_json.py import rom/root/event_mes_data.bin work/event_mes_data_json work/event_mes_data_translated.bin --table Ktable.tbl --pointer-size 2")


if __name__ == "__main__":
    main()
