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

# A piped installer cannot change its parent shell; persist PATH for new shells.
configure_path_file() {
  config="$1"
  marker='# >>> reset-radar PATH >>>'
  if [ -f "$config" ] && grep -Fqx "$marker" "$config"; then return; fi
  if [ -e "$config" ] || [ -L "$config" ]; then
    [ -f "$config" ] || { echo "Cannot update shell config: $config" >&2; return 1; }
    backup=$(mktemp "$config.reset-radar-backup.XXXXXX")
    if ! cp -p "$config" "$backup"; then rm -f "$backup"; return 1; fi
    printf 'Shell config backup: %s\n' "$backup" >&2
  else
    mkdir -p "$(dirname "$config")"
  fi
  cat >> "$config" <<'PATH_CONFIG'

# >>> reset-radar PATH >>>
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
# <<< reset-radar PATH <<<
PATH_CONFIG
  printf 'PATH configured: %s\n' "$config" >&2
}

path_configured=true
shell_name=$(basename "${SHELL:-sh}")
case "$shell_name" in
  zsh)
    configure_path_file "${ZDOTDIR:-$HOME}/.zprofile"
    configure_path_file "${ZDOTDIR:-$HOME}/.zshrc"
    ;;
  bash)
    if [ -f "$HOME/.bash_profile" ]; then login_config="$HOME/.bash_profile"
    elif [ -f "$HOME/.bash_login" ]; then login_config="$HOME/.bash_login"
    else login_config="$HOME/.profile"; fi
    configure_path_file "$login_config"
    configure_path_file "$HOME/.bashrc"
    ;;
  sh|dash) configure_path_file "$HOME/.profile" ;;
  *) path_configured=false; printf 'Automatic PATH setup is unavailable for %s; use the full binary path below.\n' "$shell_name" >&2 ;;
esac
printf '\nInstallation complete.\n' >&2
if [ "$path_configured" = true ]; then printf 'New terminals can run reset-radar directly.\n' >&2; fi
printf 'For this existing terminal (bash/zsh/sh), run: export PATH="$HOME/.local/bin:$PATH"\n' >&2
printf 'Use immediately: "%s/.local/bin/reset-radar" status --json\n' "$HOME" >&2
if [ -n "$agent" ]; then printf 'Codex Skill installed. Start a new Codex chat to use it.\n' >&2; fi
