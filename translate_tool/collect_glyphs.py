"""
collect_glyphs.py
번역 텍스트에서 실제 렌더링되는 글리프(문자) 수집 → 폰트 제작용
출력: glyphs.txt (정렬된 유니크 문자 목록)
"""

import json
import glob
import os
import re

JSON_DIR = os.path.join(os.path.dirname(__file__), "../work/event_mes_data_json")
OUT_FILE = os.path.join(os.path.dirname(__file__), "../work/glyphs.txt")

# [RAW:...] 컨트롤 코드 제거 패턴
RAW_PATTERN = re.compile(r"\[RAW:[^\]]*\]")


def extract_glyphs_from_text(text: str) -> set:
    # 컨트롤 코드 제거
    cleaned = RAW_PATTERN.sub("", text)
    # 공백 제외한 모든 문자 수집 (전각 공백 U+3000도 제외)
    return {ch for ch in cleaned if ch not in (" ", "\u3000", "\n", "\r", "\t")}


def main():
    files = sorted(glob.glob(os.path.join(JSON_DIR, "*.json")))
    all_glyphs: set = set()

    for fp in files:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get("entries", [])
        for entry in entries:
            translation = entry.get("translation", "")
            if translation:
                all_glyphs |= extract_glyphs_from_text(translation)

    # 코드포인트 순 정렬
    sorted_glyphs = sorted(all_glyphs, key=ord)

    # 카테고리별 분류 출력용
    import unicodedata

    def cat(ch):
        try:
            name = unicodedata.name(ch, "")
        except Exception:
            name = ""
        cp = ord(ch)
        if 0xAC00 <= cp <= 0xD7A3:
            return "korean_syllable"
        if 0x3130 <= cp <= 0x318F:
            return "korean_jamo"
        if 0xFF01 <= cp <= 0xFF60 or 0xFFE0 <= cp <= 0xFFE6:
            return "fullwidth"
        if 0x3000 <= cp <= 0x303F:
            return "cjk_symbol"
        if 0x4E00 <= cp <= 0x9FFF:
            return "cjk_unified"
        if cp < 0x80:
            return "ascii"
        return "other"

    groups: dict = {}
    for ch in sorted_glyphs:
        c = cat(ch)
        groups.setdefault(c, []).append(ch)

    lines = []
    lines.append(f"# Total unique glyphs: {len(sorted_glyphs)}\n")

    order = ["korean_syllable", "korean_jamo", "fullwidth", "cjk_symbol", "cjk_unified", "ascii", "other"]
    for key in order:
        if key not in groups:
            continue
        chars = groups[key]
        lines.append(f"\n## {key} ({len(chars)})\n")
        # 한 줄에 32자씩
        for i in range(0, len(chars), 32):
            lines.append("".join(chars[i:i+32]) + "\n")

    # 맨 아래 전체 한 줄 (폰트 툴 붙여넣기용)
    lines.append("\n## all_in_one\n")
    lines.append("".join(sorted_glyphs) + "\n")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"총 {len(sorted_glyphs)}개 글리프 → {OUT_FILE}")
    for key in order:
        if key in groups:
            print(f"  {key}: {len(groups[key])}")


if __name__ == "__main__":
    main()
