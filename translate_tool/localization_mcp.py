# localization_mcp.py
from fastmcp import FastMCP
import json
import re
import os

mcp = FastMCP("GameLocalization")
code_map = {}

TM_FILE = "master_translation_memory.json"
translation_memory = {}
script_dir = os.path.dirname(os.path.abspath(__file__))
base_path = os.path.join(script_dir, "..", "work", "event_mes_data_json")
if os.path.exists(TM_FILE):
    with open(TM_FILE, "r", encoding="utf-8") as f:
        translation_memory = json.load(f)
    print(f"✅ 번역 메모리 로드 완료: {len(translation_memory)}개")

SPECIAL_TAGS = {
    "[RAW:2A23]": "\n\n",  # 문단 개행 (길이가 긴 것부터 먼저 처리해야 꼬이지 않음)
    "[RAW:2823]": "\n",  # 줄 개행
}


def get_pure_text(text):
    if not text:
        return ""
    return re.sub(r"\[.*?\]", "", text).strip()


# [중요 버그 수정] 여러 파일의 name(예: "1", "2")이 겹쳐서 태그가 박살나는 것을 방지하기 위해 file_path를 키값에 추가
def mask_text(text, file_path, entry_id):
    masked = text

    # 1. 특수 개행 태그를 먼저 실제 개행 문자(\n)로 치환합니다.
    for tag, replacement in SPECIAL_TAGS.items():
        masked = masked.replace(tag, replacement)

    # 2. 나머지 [RAW:...] 등의 일반 태그들을 정규식으로 찾아 <0>, <1> 등으로 마스킹합니다.
    codes = re.findall(r"\[.*?\]", masked)
    key = f"{file_path}_{entry_id}"
    code_map[key] = codes

    for i, code in enumerate(codes):
        masked = masked.replace(code, f"<{i}>", 1)

    return masked


def unmask_text(masked_text, file_path, entry_id):
    key = f"{file_path}_{entry_id}"
    unmasked = masked_text

    # 1. <0>, <1> 로 마스킹된 일반 태그들을 먼저 복구합니다.
    if key in code_map:
        codes = code_map[key]
        for i, code in enumerate(codes):
            unmasked = unmasked.replace(f"<{i}>", code)

    # 2. 실제 개행 문자(\n)를 다시 게임의 원래 제어 코드로 복구합니다.
    # [주의] 반드시 \n\n 을 \n 보다 먼저 치환해야 버그가 발생하지 않습니다!
    unmasked = unmasked.replace("\n\n", "[RAW:2A23]")
    unmasked = unmasked.replace("\n", "[RAW:2823]")

    return unmasked


@mcp.tool()
def get_next_translation_data() -> str:
    """
    번역이 안 된 첫 번째 파일을 찾고,
    해당 파일의 모든 미번역 항목을 마스킹하여 반환합니다.
    """
    folder_path = base_path
    if not os.path.exists(folder_path):
        return "폴더를 찾을 수 없습니다."

    files = sorted([f for f in os.listdir(folder_path) if f.endswith(".json")])
    target_file = None
    target_data = None

    for filename in files:
        if "manifest" in filename:
            continue
        file_path = os.path.join(folder_path, filename)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for entry in data.get("entries", []):
                if not entry.get("translation") and entry.get("message", "").strip():
                    target_file = filename
                    target_data = data
                    break

            if target_file:
                break
        except Exception as e:
            print(f"⚠️ {filename} 파싱 실패: {e}")
            continue

    if not target_file:
        return "🎉 모든 파일의 번역이 완료되었습니다!"

    target_file_path = os.path.join(folder_path, target_file)
    slim_entries = []

    for entry in target_data.get("entries", []):
        if entry.get("translation"):
            continue
        original_msg = entry.get("message", "")
        if not original_msg.strip():
            continue

        slim_entry = {
            "name": entry["name"],
            "message": mask_text(original_msg, target_file_path, entry["name"]),
        }

        pure_jp = get_pure_text(original_msg)
        hints = {}
        for translation_ori_jp, translation_kr in translation_memory.items():
            if translation_ori_jp in pure_jp:
                hints[translation_ori_jp] = translation_kr
        if hints:
            slim_entry["tm_hint"] = hints

        slim_entries.append(slim_entry)

    result = {
        "file": target_file,
        "total_entries": len(slim_entries),
        "entries": slim_entries,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def save_translated_json(original_file_name: str, translated_json_str: str) -> str:
    """클로드의 번역 결과물을 원본 파일에 안전하게 끼워넣습니다."""
    original_file_path = os.path.join(base_path, original_file_name)
    with open(original_file_path, "r", encoding="utf-8") as f:
        original_data = json.load(f)

    try:
        translated_data = json.loads(translated_json_str)
        claude_entries = (
            translated_data.get("entries", translated_data)
            if isinstance(translated_data, dict)
            else translated_data
        )
    except Exception as e:
        return f"데이터 파싱 에러: {e}"

    translation_map = {}
    for item in claude_entries:
        if isinstance(item, dict) and "name" in item and "translation" in item:
            translation_map[item["name"]] = item["translation"]

    for entry in original_data.get("entries", []):
        name_id = entry["name"]
        if name_id in translation_map:
            entry["translation"] = unmask_text(
                translation_map[name_id], original_file_path, name_id
            )

    with open(original_file_path, "w", encoding="utf-8") as f:
        json.dump(original_data, f, ensure_ascii=False, indent=2)

    for name_id in translation_map:
        key = f"{original_file_path}_{name_id}"
        code_map.pop(key, None)
    return f"원본 손실 없이 부분 병합(Merge) 저장되었습니다: {original_file_path}"


if __name__ == "__main__":
    mcp.run()
