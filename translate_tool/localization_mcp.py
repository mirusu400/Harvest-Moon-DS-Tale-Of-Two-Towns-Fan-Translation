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

SPECIAL_TAGS = {
    "[RAW:2A23]": "\n\n",  # 문단 개행 (길이가 긴 것부터 먼저 처리해야 꼬이지 않음)
    "[RAW:2823]": "\n",  # 줄 개행
}

# 개행 태그 목록 (검증 시 사용)
NEWLINE_RAW_TAGS = {"[RAW:2A23]", "[RAW:2823]"}

MAX_CHARS_PER_LINE = 14
BATCH_SIZE = 50


def get_pure_text(text):
    if not text:
        return ""
    return re.sub(r"\[.*?\]", "", text).strip()


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

    if key in code_map:
        codes = code_map[key]
        for i, code in enumerate(codes):
            unmasked = unmasked.replace(f"<{i}>", code)

    # 실제 개행 문자(\n)를 다시 게임의 원래 제어 코드로 복구합니다.
    unmasked = unmasked.replace("\\n\\n", "[RAW:2A23]")
    unmasked = unmasked.replace("\\n", "[RAW:2823]")
    unmasked = unmasked.replace("\n\n", "[RAW:2A23]")
    unmasked = unmasked.replace("\n", "[RAW:2823]")

    return unmasked


def _count_newlines_in_original(message: str) -> dict:
    return {
        "paragraph": message.count("[RAW:2A23]"),
        "line": message.count("[RAW:2823]"),
    }


def _count_newlines_in_translation(translated: str) -> dict:
    return {
        "paragraph": translated.count("[RAW:2A23]"),
        "line": translated.count("[RAW:2823]"),
    }


def _get_line_lengths(translated: str) -> list:
    temp = translated.replace("[RAW:2A23]", "\n").replace("[RAW:2823]", "\n")
    lines = temp.split("\n")
    result = []
    for line in lines:
        pure = re.sub(r"\[.*?\]", "", line)
        result.append(len(pure))
    return result


def _extract_non_newline_tags(message: str) -> list:
    all_tags = re.findall(r"\[.*?\]", message)
    return [t for t in all_tags if t not in NEWLINE_RAW_TAGS]


def validate_translation(
    original_message: str,
    translated_unmasked: str,
    entry_name: str,
) -> list:
    errors = []

    # 1. 불법 태그 검사
    illegal_tags = re.findall(r"<br\s*/?>", translated_unmasked, re.IGNORECASE)
    if illegal_tags:
        errors.append(f"[{entry_name}] 금지된 태그 발견: {illegal_tags}")

    # 2. 비개행 제어코드 태그 무결성 검사
    orig_tags = _extract_non_newline_tags(original_message)
    trans_tags = _extract_non_newline_tags(translated_unmasked)
    if orig_tags != trans_tags:
        errors.append(
            f"[{entry_name}] 제어코드 불일치 — 원본: {orig_tags}, 번역: {trans_tags}"
        )

    # 3. 개행 수 검사
    orig_nl = _count_newlines_in_original(original_message)
    trans_nl = _count_newlines_in_translation(translated_unmasked)
    if orig_nl != trans_nl:
        errors.append(
            f"[{entry_name}] 개행 수 불일치 — "
            f"원본(줄:{orig_nl['line']}, 문단:{orig_nl['paragraph']}), "
            f"번역(줄:{trans_nl['line']}, 문단:{trans_nl['paragraph']})"
        )

    # 4. 줄당 글자 수 검사
    line_lengths = _get_line_lengths(translated_unmasked)
    for idx, length in enumerate(line_lengths):
        if length > MAX_CHARS_PER_LINE:
            errors.append(
                f"[{entry_name}] {idx+1}번째 줄 글자 수 초과: {length}자 (제한: {MAX_CHARS_PER_LINE}자)"
            )

    return errors


@mcp.tool()
def get_next_translation_data() -> str:
    """
    번역이 안 된 첫 번째 파일을 찾고,
    해당 파일의 미번역 항목을 최대 50개씩 배치로 반환합니다.
    한 파일 내에 미번역이 50개 이상이면 여러 번 호출하여 처리합니다.
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

    # 미번역 엔트리 전체 수집
    all_untranslated = []
    for entry in target_data.get("entries", []):
        if entry.get("translation"):
            continue
        original_msg = entry.get("message", "")
        if not original_msg.strip():
            continue
        all_untranslated.append(entry)

    # 배치 슬라이싱
    batch = all_untranslated[:BATCH_SIZE]
    remaining = len(all_untranslated) - len(batch)

    slim_entries = []
    for entry in batch:
        slim_entry = {
            "name": entry["name"],
            "message": mask_text(
                entry.get("message", ""), target_file_path, entry["name"]
            ),
        }

        pure_jp = get_pure_text(entry.get("message", ""))
        hints = {}
        for translation_ori_jp, translation_kr in translation_memory.items():
            if translation_ori_jp in pure_jp:
                hints[translation_ori_jp] = translation_kr
        if hints:
            slim_entry["tm_hint"] = hints

        slim_entries.append(slim_entry)

    result = {
        "file": target_file,
        "batch_size": len(slim_entries),
        "remaining_in_file": remaining,
        "entries": slim_entries,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def save_translated_json(original_file_name: str, translated_json_str: str) -> str:
    """
    번역 결과를 원본 파일에 안전하게 머지합니다.
    검증 실패 엔트리는 머지하지 않고 스킵하며, 실패 내역을 리포트합니다.
    """
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

    original_entry_map = {}
    for entry in original_data.get("entries", []):
        original_entry_map[entry["name"]] = entry

    translation_map = {}
    for item in claude_entries:
        if isinstance(item, dict) and "name" in item and "translation" in item:
            translation_map[item["name"]] = item["translation"]

    merged_count = 0
    skipped_entries = []
    all_errors = []

    for name_id, raw_translation in translation_map.items():
        orig_entry = original_entry_map.get(name_id)
        if not orig_entry:
            skipped_entries.append(name_id)
            all_errors.append(f"[{name_id}] 원본에 해당 name이 없음 — 스킵")
            continue

        original_message = orig_entry.get("message", "")
        unmasked = unmask_text(raw_translation, original_file_path, name_id)

        errors = validate_translation(original_message, unmasked, name_id)

        if errors:
            skipped_entries.append(name_id)
            all_errors.extend(errors)
            key = f"{original_file_path}_{name_id}"
            code_map.pop(key, None)
            continue

        orig_entry["translation"] = unmasked
        merged_count += 1

        key = f"{original_file_path}_{name_id}"
        code_map.pop(key, None)

    with open(original_file_path, "w", encoding="utf-8") as f:
        json.dump(original_data, f, ensure_ascii=False, indent=2)

    report_lines = [
        f"✅ 머지 완료: {merged_count}건",
        f"❌ 검증 실패 스킵: {len(skipped_entries)}건",
    ]

    if all_errors:
        report_lines.append("")
        report_lines.append("=== 검증 실패 상세 ===")
        for err in all_errors:
            report_lines.append(f"  • {err}")
        report_lines.append("")
        report_lines.append(
            "⚠️ 스킵된 엔트리는 미번역 상태로 남아있으므로 "
            "다음 get_next_translation_data 호출 시 다시 포함됩니다."
        )

    report_lines.append(f"\n저장 경로: {original_file_path}")
    return "\n".join(report_lines)


if __name__ == "__main__":
    mcp.run()
