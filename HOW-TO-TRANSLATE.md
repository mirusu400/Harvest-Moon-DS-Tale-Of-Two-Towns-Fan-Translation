# Harvest Moon DS: Tale of Two Towns Translation How-To

이 문서는 현재 워크스페이스의 `utils` 도구 사용법을 간단히 정리한 것이다.

## 1. 기본 바이너리 컨테이너 분해/재패킹

대상:
- `rom/root/mes_data.bin`
- `rom/root/event_mes_data.bin`
- `rom/root/font_data.bin`
- `rom/root/console_bg_data.bin`
- `rom/root/console_obj_data.bin`

분해:
```bash
python3 utils/binunpack.py rom/root/mes_data.bin
python3 utils/binunpack.py rom/root/event_mes_data.bin
python3 utils/binunpack.py rom/root/console_bg_data.bin
python3 utils/binunpack.py rom/root/console_obj_data.bin
```

재패킹:
```bash
python3 utils/binpack.py rom/root/mes_data
python3 utils/binpack.py rom/root/event_mes_data
python3 utils/binpack.py rom/root/console_bg_data
python3 utils/binpack.py rom/root/console_obj_data
```

결과물은 같은 위치에 `*.out.bin`으로 생성된다.

## 2. 텍스트 JSON 추출/재삽입

사용 스크립트:
- [utils/ttot_text_json.py](/Users/seongjinkim/lab/Harvest-Moon-DS-Tale-Of-Two-Towns-Fan-Translation/utils/ttot_text_json.py)

특징:
- `mes_data.bin`, `event_mes_data.bin` 컨테이너를 직접 읽는다.
- 스크립트 청크는 JSON으로 추출한다.
- 제어코드/미해독 바이트는 `[RAW:XXXX-YYYY-....]` 형태로 보존한다.
- 번역문은 `translation` 필드에 넣는다.
- 기본적으로 `original`과 `translation`의 제어토큰이 완전히 같아야 한다.

추출:
```bash
python3 utils/ttot_text_json.py export rom/root/mes_data.bin work/mes_data_json --table Jtable.tbl --pointer-size 2
python3 utils/ttot_text_json.py export rom/root/event_mes_data.bin work/event_mes_data_json --table Jtable.tbl --pointer-size 2
```

재삽입:
```bash
python3 utils/ttot_text_json.py import rom/root/mes_data.bin work/mes_data_json work/mes_data_translated.bin --table Jtable.tbl --pointer-size 2
python3 utils/ttot_text_json.py import rom/root/event_mes_data.bin work/event_mes_data_json work/event_mes_data_translated.bin --table Jtable.tbl --pointer-size 2
```

단일 JSON 검증:
```bash
python3 utils/ttot_text_json.py validate work/mes_data_json/mes_data_0000.json --table Jtable.tbl
python3 utils/ttot_text_json.py validate work/event_mes_data_json/event_mes_data_0000.json --table Jtable.tbl --pointer-size auto
```

제어코드 강제 검사를 끄고 싶으면:
```bash
python3 utils/ttot_text_json.py import rom/root/mes_data.bin work/mes_data_json work/mes_data_translated.bin --table Jtable.tbl --no-strict-controls
```

JSON 예시:
```json
{
  "source_file": "mes_data_0000.hav",
  "container_index": 0,
  "entry_count": 3,
  "pointer_size": 2,
  "null_chunk": false,
  "entries": [
    {
      "index": 0,
      "name": "0",
      "message": "원문...[RAW:2135-401F]...",
      "original": "원문...[RAW:2135-401F]...",
      "translation": "",
      "controls": ["[RAW:2135-401F]"]
    }
  ]
}
```

번역 규칙:
- `translation`만 수정한다.
- `[RAW:...]` 토큰은 지우거나 순서를 바꾸지 않는 것이 안전하다.
- 테이블(`Jtable.tbl`)에 없는 글자는 재삽입 시 에러가 난다.

## 3. 그래픽 일괄 추출/일괄 삽입

사용 스크립트:
- [utils/ttot_gfx.py](/Users/seongjinkim/lab/Harvest-Moon-DS-Tale-Of-Two-Towns-Fan-Translation/utils/ttot_gfx.py)
- [utils/ttot_font.py](/Users/seongjinkim/lab/Harvest-Moon-DS-Tale-Of-Two-Towns-Fan-Translation/utils/ttot_font.py)

특징:
- 컨테이너 전체를 다시 패킹하므로 청크 크기 변화 허용
- `inplace` 수정이 아니라 새 `bin` 생성
- 모든 청크를 `raw/`에 추출
- 렌더 가능한 자산은 `assets/*.png`로 추가 추출

### 3-1. `console_bg_data.bin`

일괄 추출:
```bash
python3 utils/ttot_gfx.py batch-export rom/root/console_bg_data.bin work/console_bg --kind bg
```

일괄 삽입:
```bash
python3 utils/ttot_gfx.py batch-import rom/root/console_bg_data.bin work/console_bg work/console_bg_patched.bin --keep-palette
```

### 3-2. `console_obj_data.bin`

일괄 추출:
```bash
python3 utils/ttot_gfx.py batch-export rom/root/console_obj_data.bin work/console_obj --kind obj
python3 utils/ttot_gfx.py batch-export rom/root/console_obj_data.bin work/console_obj --kind obj --map-xlsx console_obj_data_map.xlsx
```

일괄 삽입:
```bash
python3 utils/ttot_gfx.py batch-import rom/root/console_obj_data.bin work/console_obj work/console_obj_patched.bin --keep-palette
```

출력 구조:
```text
work/console_bg/
  batch_manifest.json
  raw/
    manifest.json
    console_bg_data_0000.hav
    ...
  assets/
    bg_0005.png
    bg_0005.json
    ...
```

```text
work/console_obj/
  batch_manifest.json
  raw/
    manifest.json
    console_obj_data_0000.hav
    ...
  assets/
    obj_0000_tiles.png
    obj_0000_preview.png
    obj_0000.json
    obj_0000_cells.json
    ...
```

규칙:
- `raw/` 안의 `.hav`를 바꾸면 그 내용이 그대로 다시 들어간다.
- `assets/*.png`를 바꾸면 해당 타일/맵/팔레트 청크가 다시 생성된다.
- `--keep-palette`를 빼면 PNG 팔레트도 다시 반영한다.
- BG 자동 추출은 휴리스틱 기반이라 일부 청크는 PNG 후보가 안 잡힐 수 있다.
- OBJ는 이제 `*_preview.png`를 수정해도 재삽입 가능하다.
- OBJ는 `*_preview.png`가 있으면 그것을 우선 사용하고, 없으면 `*_tiles.png`를 사용한다.
- OBJ preview 재삽입 시 각 셀은 원래 palette bank 안에서 가장 가까운 색으로 자동 보정된다.
- OBJ의 `*_cells.json`에는 메타 청크 해석 결과가 들어 있다.

추가 도구:
```bash
python3 utils/ttot_gfx.py scan rom/root/console_bg_data.bin
python3 utils/ttot_gfx.py scan rom/root/console_obj_data.bin
python3 utils/ttot_gfx.py export-raw rom/root/console_bg_data.bin work/console_bg_raw
python3 utils/ttot_gfx.py export-raw rom/root/console_obj_data.bin work/console_obj_raw
```

## 4. 작업 순서 추천

텍스트:
1. `ttot_text_json.py export`
2. JSON의 `translation` 채우기
3. `ttot_text_json.py validate`
4. `ttot_text_json.py import`

그래픽:
1. `ttot_gfx.py batch-export`
2. `raw/` 또는 `assets/*.png` 수정
3. `ttot_gfx.py batch-import`

폰트:
1. `ttot_font.py export`
2. `font_tiles_edit.png` 수정
3. `ttot_font.py import`

## 4-1. 폰트 추출/재삽입

폰트 추출:
```bash
python3 utils/ttot_font.py export rom/root/font_data.bin work/font_data
```

원본 팔레트 미리보기까지 추출:
```bash
python3 utils/ttot_font.py export rom/root/font_data.bin work/font_data --export-palette-preview
```

폰트 재삽입:
```bash
python3 utils/ttot_font.py import rom/root/font_data.bin work/font_data work/font_data_patched.bin
```

특징:
- `font_tiles_edit.png`는 `4bpp grayscale` 편집용 PNG다.
- 실제 폰트 타일 청크만 다시 써서 `font_data.bin`을 재조립한다.
- 현재 기본값은 `tiles_index=6`, `palette_index=0`, `meta_index=5`다.

## 5. 주의사항

- 텍스트 재삽입은 테이블 기반이므로 한글용 확장 테이블이 정리되어 있어야 한다.
- `[RAW:...]`는 실제 바이트이므로 번역 중 임의 수정하면 게임이 깨질 수 있다.
- 그래픽 재삽입은 원본과 동일 위치에 덮는 방식이 아니라 컨테이너를 통째로 다시 만든다.
- `font_data.bin` 전용 추출/삽입은 아직 별도 구현되지 않았다.
