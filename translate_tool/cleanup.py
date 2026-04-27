import json
import glob
import os
import re
import unicodedata

JSON_DIR = os.path.join(os.path.dirname(__file__), "../work/event_mes_data_json")

def cleanup_file(filepath):
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    entries = data.get("entries")
    if not entries:
        return False

    changed = False
    for entry in entries:
        original = entry.get("original", "")
        translation = entry.get("translation", "")

        if not original or not translation:
            continue

        new_translation = translation

        # 1. 맨마지막 글자를 original 맨마지막 글자와 통일
        if new_translation[-1] != original[-1]:
            new_translation = new_translation[:-1] + original[-1]

        # 2. 반각 띄어쓰기(0x20) → 전각 띄어쓰기(U+3000)
        new_translation = new_translation.replace(" ", "\u3000")

        if new_translation != translation:
            entry["translation"] = new_translation
            changed = True

    if changed:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return changed


def main():
    files = sorted(glob.glob(os.path.join(JSON_DIR, "*.json")))
    total = 0
    for fp in files:
        if cleanup_file(fp):
            total += 1
            print(f"updated: {os.path.basename(fp)}")
    print(f"\ndone. {total}/{len(files)} files updated.")


if __name__ == "__main__":
    main()
