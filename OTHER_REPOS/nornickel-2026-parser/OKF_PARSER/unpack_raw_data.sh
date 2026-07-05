#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW_DATA="${SCRIPT_DIR}/SHARED/RAW_DATA"
TOOLS_DIR="${SCRIPT_DIR}/tools"
UNRAR="${TOOLS_DIR}/unrar"
UNRAR_URL="https://www.rarlab.com/rar/rarlinux-x64-723.tar.gz"

if [[ ! -d "$RAW_DATA" ]]; then
  echo "RAW_DATA not found: $RAW_DATA" >&2
  exit 1
fi

if ! command -v 7z >/dev/null 2>&1; then
  echo "7z is required (p7zip-full)." >&2
  exit 1
fi

ensure_unrar() {
  if [[ -x "$UNRAR" ]]; then
    return 0
  fi

  if command -v unrar >/dev/null 2>&1; then
    UNRAR="$(command -v unrar)"
    return 0
  fi

  echo "unrar not found; downloading to $TOOLS_DIR ..."
  mkdir -p "$TOOLS_DIR"
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' RETURN

  if ! command -v wget >/dev/null 2>&1; then
    echo "Install unrar: sudo apt install unrar" >&2
    exit 1
  fi

  wget -q "$UNRAR_URL" -O "$tmp_dir/rarlinux.tar.gz"
  tar -xzf "$tmp_dir/rarlinux.tar.gz" -C "$tmp_dir"
  cp "$tmp_dir/rar/unrar" "$UNRAR"
  chmod +x "$UNRAR"
  trap - RETURN
  rm -rf "$tmp_dir"
}

should_skip_archive() {
  local base="$1"

  if [[ "$base" =~ \.part[2-9][0-9]*\.rar$ ]] || [[ "$base" =~ \.part[0-9]{2,}\.rar$ ]]; then
    return 0
  fi

  if [[ "$base" =~ \.zip\.[0-9]+$ ]] && [[ ! "$base" =~ \.zip\.001$ ]]; then
    return 0
  fi

  return 1
}

archive_kind() {
  local archive="$1"
  local base ext

  base="$(basename "$archive")"
  ext="${archive##*.}"
  ext="${ext,,}"

  if [[ "$base" =~ \.zip\.[0-9]+$ ]] || [[ "$ext" == "zip" ]]; then
    printf 'zip\n'
  elif [[ "$ext" == "rar" ]]; then
    printf 'rar\n'
  elif [[ "$ext" == "7z" ]]; then
    printf '7z\n'
  else
    return 1
  fi
}

list_rar_members() {
  local archive="$1"

  ensure_unrar
  "$UNRAR" lb "$archive" 2>/dev/null
}

list_7z_members() {
  local archive="$1"

  7z l -slt "$archive" 2>/dev/null | awk -F' = ' -v arch="$archive" '
    /^Path = / { path=$2; isdir=-1 }
    /^Folder = \+/ { isdir=1 }
    /^Folder = -/ { isdir=0 }
    /^Size = / && path != "" && path != arch && isdir == 0 { print path; path="" }
  '
}

members_present() {
  local archive="$1"
  local dir="$2"
  local kind member found=0

  kind="$(archive_kind "$archive")"

  while IFS= read -r member; do
    [[ -z "$member" ]] && continue
    found=1
    if [[ ! -e "$dir/$member" ]]; then
      return 1
    fi
  done < <(
    case "$kind" in
      rar) list_rar_members "$archive" ;;
      zip|7z) list_7z_members "$archive" ;;
    esac
  )

  [[ "$found" -eq 1 ]]
}

extract_rar() {
  local archive="$1"
  local dir="$2"

  ensure_unrar
  ( cd "$dir" && "$UNRAR" x -o- -y "$(basename "$archive")" ./ ) >/dev/null
}

extract_zip_or_7z() {
  local archive="$1"
  local dir="$2"

  7z x -aos -y -o"$dir" "$archive" >/dev/null
}

extract_archive() {
  local archive="$1"
  local dir kind

  dir="$(dirname "$archive")"

  if members_present "$archive" "$dir"; then
    echo "Skip (already extracted): $archive"
    return 0
  fi

  kind="$(archive_kind "$archive")"
  echo "Extracting: $archive"

  case "$kind" in
    rar) extract_rar "$archive" "$dir" ;;
    zip|7z) extract_zip_or_7z "$archive" "$dir" ;;
    *) echo "Unsupported archive: $archive" >&2; return 1 ;;
  esac
}

find_archives() {
  find "$RAW_DATA" -type f \( \
    -iname '*.zip' -o \
    -iname '*.zip.001' -o \
    -iname '*.rar' -o \
    -iname '*.7z' \
  \) -print0
}

pass=0
while true; do
  pass=$((pass + 1))
  extracted=0

  while IFS= read -r -d '' archive; do
    if should_skip_archive "$(basename "$archive")"; then
      continue
    fi

    if members_present "$archive" "$(dirname "$archive")"; then
      continue
    fi

    extract_archive "$archive"
    extracted=1
  done < <(find_archives)

  if [[ "$extracted" -eq 0 ]]; then
    break
  fi

  if [[ "$pass" -ge 20 ]]; then
    echo "Stopped after $pass passes (nested archives limit)." >&2
    exit 1
  fi
done

echo "Done."
