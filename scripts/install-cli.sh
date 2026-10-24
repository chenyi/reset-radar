#!/bin/sh
set -eu
version='@VERSION@'
source_mode=auto
agent=''
json=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) version="$2"; shift 2 ;;
    --download-source) source_mode="$2"; shift 2 ;;
    --agent) [ "$2" = codex ] || { echo 'Only codex is supported' >&2; exit 2; }; agent=codex; shift 2 ;;
    --json) json=--json; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$version" in ''|*[!0-9A-Za-z.-]*) echo 'Invalid version' >&2; exit 2 ;; esac
case "$source_mode" in auto|site|github) ;; *) echo 'Invalid download source' >&2; exit 2 ;; esac
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) target=aarch64-apple-darwin ;;
  Linux-x86_64) target=x86_64-unknown-linux-musl ;;
  *) echo 'Supported: macOS ARM64 and Linux x86_64' >&2; exit 2 ;;
esac
site="${RESET_RADAR_DOWNLOAD_BASE:-https://radar.shadowearth.cn/downloads}"
github="${RESET_RADAR_GITHUB_DOWNLOAD_BASE:-https://github.com/chenyi/reset-radar/releases/download}"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT HUP INT TERM
download() {
  name="$1"
  if [ "$source_mode" != github ]; then
    if curl -fsSL --connect-timeout 10 --max-time 120 "$site/cli-v$version/$name" -o "$stage/$name"; then return; fi
    [ "$source_mode" = auto ] || return 1
  fi
  curl -fsSL --connect-timeout 10 --max-time 120 "$github/cli-v$version/$name" -o "$stage/$name"
}
archive="reset-radar-$target.tar.gz"
download SHA256SUMS
download "$archive"
expected=$(awk -v name="$archive" '$2 == name {print $1}' "$stage/SHA256SUMS")
if command -v sha256sum >/dev/null 2>&1; then actual=$(sha256sum "$stage/$archive" | awk '{print $1}');
else actual=$(shasum -a 256 "$stage/$archive" | awk '{print $1}'); fi
[ -n "$expected" ] && [ "$expected" = "$actual" ] || { echo 'Checksum mismatch' >&2; exit 6; }
tar -xzf "$stage/$archive" -C "$stage" reset-radar
chmod +x "$stage/reset-radar"
if [ -n "$agent" ]; then
  "$stage/reset-radar" install --agent "$agent" --download-source "$source_mode" ${json:+"$json"}
else
  "$stage/reset-radar" install --download-source "$source_mode" ${json:+"$json"}
fi
