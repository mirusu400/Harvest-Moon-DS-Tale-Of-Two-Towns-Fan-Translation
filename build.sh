#!/usr/bin/env bash
# build.sh — 한국어 패치 빌드 & ROM 재패킹
# 사용법: ./build.sh <원본.nds> [출력.nds]
#
# 사전 준비:
#   brew install -q gcc make
#   git clone https://github.com/devkitPro/ndstool && cd ndstool && ./autogen.sh && ./configure && make && sudo make install

set -e

ORIG_NDS="${1}"
OUT_NDS="${2:-output_kr.nds}"

if [ -z "$ORIG_NDS" ]; then
  echo "사용법: $0 <원본.nds> [출력.nds]"
  echo ""
  echo "ndstool 설치 방법:"
  echo "  git clone https://github.com/devkitPro/ndstool"
  echo "  cd ndstool && ./autogen.sh && ./configure && make && sudo make install"
  exit 1
fi

if ! command -v ndstool &>/dev/null; then
  echo "ERROR: ndstool 없음."
  echo "  git clone https://github.com/devkitPro/ndstool"
  echo "  cd ndstool && ./autogen.sh && ./configure && make && sudo make install"
  exit 1
fi

echo "=== 1. 폰트/테이블 생성 ==="
python3 translate_tool/make_font_table.py

echo ""
echo "=== 2. 폰트 바이너리 패치 ==="
python3 utils/ttot_font.py import \
  rom/root/font_data.bin \
  work/font_data \
  work/font_data_patched.bin

echo ""
echo "=== 3. 이벤트 텍스트 삽입 ==="
python3 utils/ttot_text_json.py import \
  rom/root/event_mes_data.bin \
  work/event_mes_data_json \
  work/event_mes_data_translated.bin \
  --table Ktable.tbl \
  --pointer-size 2

echo ""
echo "=== 4. 패치 파일 → rom/root 복사 ==="
cp work/font_data_patched.bin       rom/root/font_data.bin
cp work/event_mes_data_translated.bin rom/root/event_mes_data.bin

echo ""
echo "=== 5. ROM 재패킹 ==="
# 원본 ROM에서 ARM9/ARM7 바이너리 추출 후 재조립
TMPDIR=$(mktemp -d)
ndstool -x "$ORIG_NDS" \
  -9 "$TMPDIR/arm9.bin" \
  -7 "$TMPDIR/arm7.bin" \
  -y "$TMPDIR/arm7i.bin" \
  -Y "$TMPDIR/arm9i.bin" \
  -t "$TMPDIR/banner.bin" \
  -h "$TMPDIR/header.bin" \
  2>/dev/null || true

ndstool -c "$OUT_NDS" \
  -9 "$TMPDIR/arm9.bin" \
  -7 "$TMPDIR/arm7.bin" \
  -y "$TMPDIR/arm7i.bin" \
  -Y "$TMPDIR/arm9i.bin" \
  -t "$TMPDIR/banner.bin" \
  -h "$TMPDIR/header.bin" \
  -d rom/root

rm -rf "$TMPDIR"

echo ""
echo "=== 완료 ==="
echo "출력: $OUT_NDS ($(du -h "$OUT_NDS" | cut -f1))"
echo ""
echo "실기기 테스트:"
echo "  - R4/DSTT 등 플래시카트 SD에 $OUT_NDS 복사"
echo "  - 또는 MelonDS/DeSmuME 에뮬레이터로 먼저 확인 추천"
