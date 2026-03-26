# localization_mcp_parallel.py
"""
병렬 안전 게임 번역 MCP 서버 (파일 단위 락)

- A 에이전트가 0128.json을 잡으면, B는 0129.json으로 감
- A는 0128.json의 배치를 계속 가져오면서 번역
- 0128.json 번역 완료 시 파일 클레임 자동 해제 → 다음 미번역 파일로
- 에이전트 크래시 시 10분 후 클레임 자동 만료
- [RAW:2B23-XXXX-2823-YYYY] 같은 합체 태그도 올바르게 분리/복원 처리
"""

from fastmcp import FastMCP
import json
import re
import os
import time
import uuid
import fcntl
from pathlib import Path

mcp = FastMCP("Harvest-Moon-GameLocalization")

# ──────────────────────────────────────────────
# 경로 설정
# ──────────────────────────────────────────────
script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
base_path = script_dir / ".." / "work" / "event_mes_data_json"
TM_FILE = script_dir / "master_translation_memory.json"
CLAIMS_DIR = script_dir / ".claims"
CLAIMS_DIR.mkdir(exist_ok=True)

INSTANCE_ID = f"{os.getpid()}_{uuid.uuid4().hex[:8]}"

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
FILE_CLAIM_EXPIRE_SECONDS = 600
MAX_CHARS_PER_LINE = 14
BATCH_SIZE = 50

# 개행 제어코드 (긴 것부터 치환해야 꼬이지 않음)
NEWLINE_CODES = {
    "2B23": "\n\n\n",  # 페이지/대화창 넘김
    "2A23": "\n\n",  # 문단 개행
    "2823": "\n",  # 줄 개행
}

# ──────────────────────────────────────────────
# 번역 메모리 로드
# ──────────────────────────────────────────────
translation_memory = {}
if TM_FILE.exists():
    with open(TM_FILE, "r", encoding="utf-8") as f:
        translation_memory = json.load(f)

code_map = {}


# ──────────────────────────────────────────────
# 합체 태그 분리 / 복원
# ──────────────────────────────────────────────
def _split_combined_newline_tags(text: str) -> str:
    """
    합체 태그 안의 개행 코드를 분리하여 단독 태그로 만듦.

    [RAW:2B23-411F-B01F-481F-3621]
      → [RAW:2B23][RAW:411F-B01F-481F-3621]

    [RAW:6C20-2C23-2823-0622]
      → [RAW:6C20-2C23][RAW:2823][RAW:0622]

    [RAW:411F-2A23-B01F]
      → [RAW:411F][RAW:2A23][RAW:B01F]
    """

    def _split_tag(match):
        inner = match.group(1)
        parts = inner.split("-")

        if len(parts) <= 1:
            return match.group(0)

        has_newline = any(p in NEWLINE_CODES for p in parts)
        if not has_newline:
            return match.group(0)

        result = []
        non_newline_buffer = []

        for part in parts:
            if part in NEWLINE_CODES:
                if non_newline_buffer:
                    result.append("[RAW:" + "-".join(non_newline_buffer) + "]")
                    non_newline_buffer = []
                result.append(f"[RAW:{part}]")
            else:
                non_newline_buffer.append(part)

        if non_newline_buffer:
            result.append("[RAW:" + "-".join(non_newline_buffer) + "]")

        return "".join(result)

    return re.sub(r"\[RAW:([0-9A-Fa-f]+(?:-[0-9A-Fa-f]+)+)\]", _split_tag, text)


def _rejoin_adjacent_raw_tags(text: str) -> str:
    """
    인접한 [RAW:X][RAW:Y] 를 [RAW:X-Y] 로 합침.

    원본 데이터에서 텍스트 없이 [RAW:X][RAW:Y]가 연속하는 경우는 없으므로,
    인접한 RAW 태그는 반드시 우리가 분리한 것 → 무조건 재합체.

    [RAW:6C20-2C23][RAW:2823][RAW:0622]
      → [RAW:6C20-2C23-2823-0622]

    3개 이상 연속도 반복 적용으로 처리.
    """
    pattern = re.compile(
        r"\[RAW:([0-9A-Fa-f]+(?:-[0-9A-Fa-f]+)*)\]"
        r"\[RAW:([0-9A-Fa-f]+(?:-[0-9A-Fa-f]+)*)\]"
    )
    changed = True
    while changed:
        new_text = pattern.sub(r"[RAW:\1-\2]", text)
        changed = new_text != text
        text = new_text
    return text


# ──────────────────────────────────────────────
# 파일 단위 클레임 시스템
# ──────────────────────────────────────────────
FILE_CLAIMS_PATH = CLAIMS_DIR / "_file_claims.json"
FILE_CLAIMS_LOCK = CLAIMS_DIR / "_file_claims.lock"


def _locked_claims_op(func):
    with open(FILE_CLAIMS_LOCK, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            return func()
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def _load_file_claims() -> dict:
    if not FILE_CLAIMS_PATH.exists():
        return {}
    try:
        with open(FILE_CLAIMS_PATH, "r", encoding="utf-8") as f:
            claims = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}
    now = time.time()
    return {
        fname: info
        for fname, info in claims.items()
        if now - info.get("claimed_at", 0) < FILE_CLAIM_EXPIRE_SECONDS
    }


def _save_file_claims(claims: dict):
    with open(FILE_CLAIMS_PATH, "w", encoding="utf-8") as f:
        json.dump(claims, f, ensure_ascii=False, indent=2)


def claim_file(filename: str) -> bool:
    def _do():
        claims = _load_file_claims()
        if filename in claims:
            if claims[filename]["instance"] == INSTANCE_ID:
                claims[filename]["claimed_at"] = time.time()
                _save_file_claims(claims)
                return True
            else:
                return False
        claims[filename] = {"instance": INSTANCE_ID, "claimed_at": time.time()}
        _save_file_claims(claims)
        return True

    return _locked_claims_op(_do)


def refresh_claim(filename: str):
    def _do():
        claims = _load_file_claims()
        if filename in claims and claims[filename]["instance"] == INSTANCE_ID:
            claims[filename]["claimed_at"] = time.time()
            _save_file_claims(claims)

    _locked_claims_op(_do)


def release_file_claim(filename: str):
    def _do():
        claims = _load_file_claims()
        if filename in claims and claims[filename]["instance"] == INSTANCE_ID:
            del claims[filename]
            _save_file_claims(claims)

    _locked_claims_op(_do)


def get_claimed_files() -> dict:
    return _locked_claims_op(_load_file_claims)


# ──────────────────────────────────────────────
# 텍스트 처리
# ──────────────────────────────────────────────
def get_pure_text(text):
    if not text:
        return ""
    return re.sub(r"\[.*?\]", "", text).strip()


def mask_text(text, file_path, entry_id):
    masked = text

    # 1) 합체 태그 분리: [RAW:2B23-411F] → [RAW:2B23][RAW:411F]
    masked = _split_combined_newline_tags(masked)

    # 2) 단독 개행 태그를 실제 개행으로 치환 (긴 것부터)
    for code, replacement in NEWLINE_CODES.items():
        masked = masked.replace(f"[RAW:{code}]", replacement)

    # 3) 나머지 태그를 <0>, <1> 등으로 마스킹
    codes = re.findall(r"\[.*?\]", masked)
    key = f"{file_path}_{entry_id}"
    code_map[key] = codes

    for i, code in enumerate(codes):
        masked = masked.replace(code, f"<{i}>", 1)

    return masked


def unmask_text(masked_text, file_path, entry_id):
    key = f"{file_path}_{entry_id}"
    unmasked = masked_text

    # 1) <0>, <1> 등을 원본 제어코드로 복원
    if key in code_map:
        codes = code_map[key]
        for i, code in enumerate(codes):
            unmasked = unmasked.replace(f"<{i}>", code)
    else:
        print(f"⚠️ code_map 키 누락: {key}")

    # 2) 개행 → RAW 태그 복원 (긴 것부터!)
    unmasked = unmasked.replace("\\n\\n\\n", "[RAW:2B23]")
    unmasked = unmasked.replace("\\n\\n", "[RAW:2A23]")
    unmasked = unmasked.replace("\\n", "[RAW:2823]")
    unmasked = unmasked.replace("\n\n\n", "[RAW:2B23]")
    unmasked = unmasked.replace("\n\n", "[RAW:2A23]")
    unmasked = unmasked.replace("\n", "[RAW:2823]")

    # 3) 인접 RAW 태그 재합체: [RAW:2B23][RAW:411F] → [RAW:2B23-411F]
    unmasked = _rejoin_adjacent_raw_tags(unmasked)

    return unmasked


# ──────────────────────────────────────────────
# 검증
# ──────────────────────────────────────────────
def _count_newlines_in_message(message: str) -> dict:
    """합체 태그 안의 개행 코드도 정확히 카운트."""
    counts = {}
    for code in NEWLINE_CODES:
        count = 0
        for tag_match in re.finditer(
            r"\[RAW:([0-9A-Fa-f]+(?:-[0-9A-Fa-f]+)*)\]", message
        ):
            parts = tag_match.group(1).split("-")
            count += parts.count(code)
        counts[code] = count
    return counts


def _get_line_lengths(translated: str) -> list:
    """합체 태그 안의 개행 코드도 줄 나눔으로 처리."""
    temp = translated

    def _replace_newlines_in_tag(match):
        inner = match.group(1)
        parts = inner.split("-")
        result_parts = []
        output = ""
        for part in parts:
            if part in NEWLINE_CODES:
                if result_parts:
                    output += "[RAW:" + "-".join(result_parts) + "]"
                    result_parts = []
                output += "\n"
            else:
                result_parts.append(part)
        if result_parts:
            output += "[RAW:" + "-".join(result_parts) + "]"
        return output

    temp = re.sub(
        r"\[RAW:([0-9A-Fa-f]+(?:-[0-9A-Fa-f]+)*)\]",
        _replace_newlines_in_tag,
        temp,
    )
    lines = temp.split("\n")
    return [len(re.sub(r"\[.*?\]", "", line)) for line in lines]


def _extract_non_newline_tags(message: str) -> list:
    """
    비개행 제어코드만 추출 (합체 태그에서 개행 부분 제거).

    [RAW:2B23-411F-B01F] → [RAW:411F-B01F]
    [RAW:6C20-2C23-2823-0622] → [RAW:6C20-2C23-0622]
    [RAW:2823]               → (제거)
    [RAW:411F-B01F]          → [RAW:411F-B01F]
    """
    all_tags = re.findall(r"\[RAW:[0-9A-Fa-f]+(?:-[0-9A-Fa-f]+)*\]", message)
    result = []

    for tag in all_tags:
        inner = tag[5:-1]
        parts = inner.split("-")
        non_newline_parts = [p for p in parts if p not in NEWLINE_CODES]
        if non_newline_parts:
            result.append("[RAW:" + "-".join(non_newline_parts) + "]")

    other_tags = re.findall(r"\[(?!RAW:)[^\]]*\]", message)
    result.extend(other_tags)

    return result


def validate_translation(original_message, translated_unmasked, entry_name):
    errors = []

    # 0. unmask 실패 감지 (제어코드가 하나도 없으면 높은 확률로 code_map 누락)
    if re.search(r"<\d+>", translated_unmasked):
        errors.append(
            f"[{entry_name}] unmask 실패 — 번역에 <0>, <1> 등이 그대로 남아있음 "
            f"(code_map 키 누락 가능성. MCP 서버가 중간에 재시작되었을 수 있음)"
        )
        return errors  # 이후 검증은 의미 없으므로 바로 리턴

    # 1. HTML 태그 금지
    illegal_tags = re.findall(r"<br\s*/?>", translated_unmasked, re.IGNORECASE)
    if illegal_tags:
        errors.append(f"[{entry_name}] 금지된 태그 발견: {illegal_tags}")

    # 2. 비개행 제어코드 무결성
    orig_tags = _extract_non_newline_tags(original_message)
    trans_tags = _extract_non_newline_tags(translated_unmasked)
    if orig_tags != trans_tags:
        errors.append(
            f"[{entry_name}] 제어코드 불일치 — 원본: {orig_tags}, 번역: {trans_tags}"
        )

    # 3. 개행 수 검사 (합체 태그 포함)
    orig_nl = _count_newlines_in_message(original_message)
    trans_nl = _count_newlines_in_message(translated_unmasked)
    if orig_nl != trans_nl:
        errors.append(
            f"[{entry_name}] 개행 수 불일치 — 원본: {orig_nl}, 번역: {trans_nl}"
        )

    # 4. 줄당 글자 수 검사
    line_lengths = _get_line_lengths(translated_unmasked)
    for idx, length in enumerate(line_lengths):
        if length > MAX_CHARS_PER_LINE:
            errors.append(
                f"[{entry_name}] {idx+1}번째 줄 글자 수 초과: {length}자 (제한: {MAX_CHARS_PER_LINE}자)"
            )

    return errors


# ──────────────────────────────────────────────
# 파일에 미번역 남았는지 확인
# ──────────────────────────────────────────────
def _has_untranslated(file_path: str) -> bool:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("entries", []):
            if not entry.get("translation") and entry.get("message", "").strip():
                return True
    except:
        pass
    return False


# ──────────────────────────────────────────────
# MCP 도구
# ──────────────────────────────────────────────
@mcp.tool()
def get_next_translation_data() -> str:
    """
    번역이 안 된 첫 번째 파일을 찾고,
    해당 파일의 미번역 항목을 최대 50개씩 배치로 반환합니다.

    ★ 파일 단위 락: 다른 에이전트가 잡은 파일은 건너뛰고 다음 파일로 갑니다.
    """
    folder_path = str(base_path)
    if not os.path.exists(folder_path):
        return "폴더를 찾을 수 없습니다."

    files = sorted([f for f in os.listdir(folder_path) if f.endswith(".json")])

    for filename in files:
        if "manifest" in filename:
            continue

        file_path = os.path.join(folder_path, filename)

        if not _has_untranslated(file_path):
            continue

        if not claim_file(filename):
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            release_file_claim(filename)
            print(f"⚠️ {filename} 파싱 실패: {e}")
            continue

        untranslated = []
        for entry in data.get("entries", []):
            if entry.get("translation"):
                continue
            if not entry.get("message", "").strip():
                continue
            untranslated.append(entry)

        if not untranslated:
            release_file_claim(filename)
            continue

        batch = untranslated[:BATCH_SIZE]
        remaining = len(untranslated) - len(batch)

        slim_entries = []
        for entry in batch:
            slim_entry = {
                "name": entry["name"],
                "message": mask_text(
                    entry.get("message", ""), file_path, entry["name"]
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
            "file": filename,
            "batch_size": len(slim_entries),
            "remaining_in_file": remaining,
            "entries": slim_entries,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    return "🎉 모든 파일의 번역이 완료되었습니다!"


@mcp.tool()
def save_translated_json(original_file_name: str, translated_json_str: str) -> str:
    """
    번역 결과를 원본 파일에 안전하게 머지합니다.
    파일 번역이 전부 완료되면 자동으로 클레임 해제 → 다음 파일로.
    """
    original_file_path = os.path.join(str(base_path), original_file_name)

    write_lock_path = CLAIMS_DIR / f"{original_file_name}.write_lock"
    with open(write_lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
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

            file_complete = not _has_untranslated(original_file_path)

        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)

    if file_complete:
        release_file_claim(original_file_name)
    else:
        refresh_claim(original_file_name)

    report_lines = [
        f"✅ 머지 완료: {merged_count}건",
        f"❌ 검증 실패 스킵: {len(skipped_entries)}건",
    ]

    if file_complete:
        report_lines.append(f"🎉 {original_file_name} 번역 완료! 클레임 해제됨.")

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


@mcp.tool()
def get_status() -> str:
    """
    현재 번역 진행 상황과 파일별 클레임 상태를 확인합니다.
    """
    folder_path = str(base_path)
    if not os.path.exists(folder_path):
        return "폴더를 찾을 수 없습니다."

    files = sorted([f for f in os.listdir(folder_path) if f.endswith(".json")])
    total_entries = 0
    translated_entries = 0
    files_with_remaining = 0
    files_complete = 0

    for filename in files:
        if "manifest" in filename:
            continue
        file_path = os.path.join(folder_path, filename)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            has_remaining = False
            for entry in data.get("entries", []):
                if not entry.get("message", "").strip():
                    continue
                total_entries += 1
                if entry.get("translation"):
                    translated_entries += 1
                else:
                    has_remaining = True
            if has_remaining:
                files_with_remaining += 1
            else:
                files_complete += 1
        except:
            continue

    claims = get_claimed_files()
    claim_lines = []
    for fname, info in sorted(claims.items()):
        age = int(time.time() - info["claimed_at"])
        claim_lines.append(f"    📌 {fname} → {info['instance']} ({age}초 전)")

    remaining = total_entries - translated_entries
    pct = (translated_entries / total_entries * 100) if total_entries > 0 else 0

    lines = [
        f"📊 번역 진행 상황",
        f"  전체 엔트리: {total_entries}건",
        f"  번역 완료: {translated_entries}건 ({pct:.1f}%)",
        f"  남은 엔트리: {remaining}건",
        f"  완료 파일: {files_complete}개 / 미완료 파일: {files_with_remaining}개",
        f"",
        f"🔒 현재 파일 클레임 ({len(claims)}개):",
    ]
    if claim_lines:
        lines.extend(claim_lines)
    else:
        lines.append("    (없음)")
    lines.append(f"\n🔑 내 인스턴스: {INSTANCE_ID}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
