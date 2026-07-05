#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="${UV:-uv}"
RAW_DATA="${SCRIPT_DIR}/SHARED/RAW_DATA"

ARCHIVE_EXTENSIONS='001|002|003|004|005|7z|arj|bz2|cab|cpio|deb|dmg|gz|iso|lha|lzh|rar|rpm|tar|tbz2|tgz|txz|xz|z|zip'
BINARY_EXTENSIONS='avif|bin|bmp|dat|db|dll|docm|exe|gif|heic|heif|ico|jpeg|jpg|mdb|mp3|mp4|ods|odp|ppt|sqlite|svg|tif|tiff|wav|webp|wma|wmv|xls|xlsb|xlsm|xlsx|xlt|xltx'

usage() {
  cat <<'EOF'
Usage: analyze.sh [ROOT_DIR]

Count text and binary files under ROOT_DIR (default: RAW_DATA).

Columns: PDF, DOCX, DOC, PPTX, TXT, MD, OTHER
OTHER includes archives and binary files only.

EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

TARGET="${1:-$RAW_DATA}"

if [[ ! -d "$TARGET" ]]; then
  echo "Directory not found: $TARGET" >&2
  exit 1
fi

is_archive_name() {
  local base="$1"
  [[ "$base" =~ \.zip\.[0-9]+$ ]] || [[ "$base" =~ \.part[0-9]+\.rar$ ]]
}

is_binary_mime() {
  local mime="$1"

  [[ "$mime" == application/octet-stream ]] \
    || [[ "$mime" == image/* ]] \
    || [[ "$mime" == audio/* ]] \
    || [[ "$mime" == video/* ]] \
    || [[ "$mime" == application/zip* ]] \
    || [[ "$mime" == application/x-rar* ]] \
    || [[ "$mime" == application/x-7z* ]] \
    || [[ "$mime" == application/gzip* ]] \
    || [[ "$mime" == application/x-tar* ]] \
    || [[ "$mime" == application/vnd.ms-* ]] \
    || [[ "$mime" == application/vnd.openxmlformats-officedocument.spreadsheetml.* ]] \
    || [[ "$mime" == application/vnd.openxmlformats-officedocument.presentationml.presentation ]]
}

classify_file() {
  local file="$1"
  local base ext mime

  base="$(basename "$file")"
  ext="${file##*.}"
  ext="${ext,,}"

  if is_archive_name "$base"; then
    printf 'other\n'
    return
  fi

  if [[ "$base" =~ ^\.~lock\. ]] || [[ "$base" == *'#' ]]; then
    printf 'other\n'
    return
  fi

  if [[ "$base" == "$ext" ]]; then
    mime="$(file --brief --mime-type "$file" 2>/dev/null || true)"
    if [[ "$mime" == text/* ]]; then
      printf 'unlisted\n'
    else
      printf 'other\n'
    fi
    return
  fi

  case "$ext" in
    pdf) printf 'pdf\n' ;;
    docx) printf 'docx\n' ;;
    doc) printf 'doc\n' ;;
    pptx) printf 'pptx\n' ;;
    txt) printf 'txt\n' ;;
    md|markdown) printf 'md\n' ;;
    *)
      if [[ "$ext" =~ ^($ARCHIVE_EXTENSIONS)$ ]]; then
        printf 'other\n'
      elif [[ "$ext" =~ ^($BINARY_EXTENSIONS)$ ]]; then
        printf 'other\n'
      else
        mime="$(file --brief --mime-type "$file" 2>/dev/null || true)"
        if [[ "$mime" =~ ^(application/zip|application/x-rar|application/x-7z-compressed|application/gzip|application/x-tar) ]]; then
          printf 'other\n'
        elif is_binary_mime "$mime"; then
          printf 'other\n'
        elif [[ "$mime" == text/* ]]; then
          printf 'unlisted\n'
        else
          printf 'other\n'
        fi
      fi
      ;;
  esac
}

bucket_for_folder() {
  local dir="$1"
  local root="$2"

  if [[ "$dir" == "$root" ]]; then
    printf '%s\n' "$root"
    return
  fi

  local rel="${dir#"$root"/}"
  local top="${rel%%/*}"

  printf '%s/%s\n' "$root" "$top"
}

print_report() {
  local target="$1"
  local tsv_file="$2"
  local unlisted="$3"

  "${UV}" run python - "$target" "$tsv_file" "$unlisted" <<'PY'
import sys
from pathlib import Path

target = sys.argv[1]
unlisted = int(sys.argv[3])
rows = []
for line in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines():
    if line.strip():
        rows.append(line.split("\t"))

if not rows:
    sys.exit(0)

headers = ["Folder", "PDF", "DOCX", "DOC", "PPTX", "TXT", "MD", "OTHER", "TOTAL"]
data_rows = rows[:-1]
total_row = rows[-1]

tl, tr, bl, br = "\u250c", "\u2510", "\u2514", "\u2518"
h, v = "\u2500", "\u2502"
tm, bm, ml, mr, mm = "\u252c", "\u2534", "\u251c", "\u2524", "\u253c"

widths = [len(header) for header in headers]
for row in rows:
    for i, cell in enumerate(row):
        widths[i] = max(widths[i], len(cell))

def border(left, mid, right, fill):
    parts = [fill * (w + 2) for w in widths]
    print(left + mid.join(parts) + right)

def render_row(cells):
    rendered = []
    for i, cell in enumerate(cells):
        if i == 0:
            rendered.append(cell.ljust(widths[i]))
        else:
            rendered.append(cell.rjust(widths[i]))
    print(v + " " + (" " + v + " ").join(rendered) + " " + v)

try:
    display_target = str(Path(target).relative_to(Path.cwd()))
except ValueError:
    display_target = target

print()
print(f"  File analysis: {display_target}")
print()

border(tl, tm, tr, h)
render_row(headers)
border(ml, mm, mr, h)

for row in data_rows:
    render_row(row)

border(ml, mm, mr, h)
render_row(total_row)
border(bl, bm, br, h)

pdf, docx, doc, pptx, txt, md, other, grand = map(int, total_row[1:])
text_total = pdf + docx + doc + pptx + txt + md

print()
print("  Summary")
print(f"    Files in table: {grand:,}")
print(f"    Text-like:      {text_total:,}  (pdf {pdf:,}, docx {docx:,}, doc {doc:,}, pptx {pptx:,}, txt {txt:,}, md {md:,})")
print(f"    Other:          {other:,}  (archives and binary files)")
if unlisted:
    print(f"    Unlisted text:  {unlisted:,}  (text files outside the columns above)")
if grand:
    print(f"    Text share:     {text_total / grand * 100:.1f}%")
print()
PY
}

declare -A COUNT_PDF COUNT_DOCX COUNT_DOC COUNT_PPTX COUNT_TXT COUNT_MD COUNT_OTHER
unlisted_total=0

while IFS= read -r -d '' file; do
  dir="$(dirname "$file")"
  bucket="$(bucket_for_folder "$dir" "$TARGET")"
  kind="$(classify_file "$file")"

  case "$kind" in
    pdf) COUNT_PDF["$bucket"]=$((${COUNT_PDF["$bucket"]:-0} + 1)) ;;
    docx) COUNT_DOCX["$bucket"]=$((${COUNT_DOCX["$bucket"]:-0} + 1)) ;;
    doc) COUNT_DOC["$bucket"]=$((${COUNT_DOC["$bucket"]:-0} + 1)) ;;
    pptx) COUNT_PPTX["$bucket"]=$((${COUNT_PPTX["$bucket"]:-0} + 1)) ;;
    txt) COUNT_TXT["$bucket"]=$((${COUNT_TXT["$bucket"]:-0} + 1)) ;;
    md) COUNT_MD["$bucket"]=$((${COUNT_MD["$bucket"]:-0} + 1)) ;;
    other) COUNT_OTHER["$bucket"]=$((${COUNT_OTHER["$bucket"]:-0} + 1)) ;;
    unlisted) unlisted_total=$((unlisted_total + 1)) ;;
  esac
done < <(find "$TARGET" -type f -print0)

all_buckets=()
for bucket in \
  "${!COUNT_PDF[@]}" "${!COUNT_DOCX[@]}" "${!COUNT_DOC[@]}" \
  "${!COUNT_PPTX[@]}" "${!COUNT_TXT[@]}" "${!COUNT_MD[@]}" "${!COUNT_OTHER[@]}"; do
  if [[ -n "$bucket" ]]; then
    all_buckets+=("$bucket")
  fi
done

if [[ ${#all_buckets[@]} -eq 0 && "$unlisted_total" -eq 0 ]]; then
  echo "No files found in: $TARGET"
  exit 0
fi

mapfile -t all_buckets < <(printf '%s\n' "${all_buckets[@]}" | sort -u)

total_pdf=0
total_docx=0
total_doc=0
total_pptx=0
total_txt=0
total_md=0
total_other=0

tsv_file="$(mktemp)"
trap 'rm -f "$tsv_file"' EXIT

for bucket in "${all_buckets[@]}"; do
  pdf=${COUNT_PDF["$bucket"]:-0}
  docx=${COUNT_DOCX["$bucket"]:-0}
  doc=${COUNT_DOC["$bucket"]:-0}
  pptx=${COUNT_PPTX["$bucket"]:-0}
  txt=${COUNT_TXT["$bucket"]:-0}
  md=${COUNT_MD["$bucket"]:-0}
  other=${COUNT_OTHER["$bucket"]:-0}
  total=$((pdf + docx + doc + pptx + txt + md + other))

  total_pdf=$((total_pdf + pdf))
  total_docx=$((total_docx + docx))
  total_doc=$((total_doc + doc))
  total_pptx=$((total_pptx + pptx))
  total_txt=$((total_txt + txt))
  total_md=$((total_md + md))
  total_other=$((total_other + other))

  rel_bucket="${bucket#"$TARGET"/}"
  if [[ "$rel_bucket" == "$bucket" ]]; then
    rel_bucket="."
  fi

  printf '%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n' \
    "$rel_bucket" "$pdf" "$docx" "$doc" "$pptx" "$txt" "$md" "$other" "$total" >>"$tsv_file"
done

grand_total=$((total_pdf + total_docx + total_doc + total_pptx + total_txt + total_md + total_other))
printf 'TOTAL\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n' \
  "$total_pdf" "$total_docx" "$total_doc" "$total_pptx" "$total_txt" "$total_md" "$total_other" "$grand_total" >>"$tsv_file"

print_report "$TARGET" "$tsv_file" "$unlisted_total"
