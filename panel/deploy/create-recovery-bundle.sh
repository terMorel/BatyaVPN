#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/recovery-common.sh
. "$SCRIPT_DIR/recovery-common.sh"

usage() {
  cat <<'EOF'
Usage:
  sudo bash deploy/create-recovery-bundle.sh \
    --recipient AGE_RECIPIENT \
    --output /root/hyboard-recovery/recovery-YYYYMMDD.age \
    [--extra-path /absolute/path]

The output is encrypted before it leaves the private temporary directory.
The age private identity must remain off the server.
EOF
}

recipient=""
output=""
declare -a extra_paths=()

while (($#)); do
  case "$1" in
    --recipient) recipient="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    --extra-path) extra_paths+=("${2:-}"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) recovery_fail "unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || recovery_fail "run this script as root"
[[ -n "$recipient" ]] || recovery_fail "--recipient is required"
[[ "$output" == /* && "$output" == *.age ]] || recovery_fail "--output must be an absolute .age path"
[[ ! -e "$output" ]] || recovery_fail "refusing to overwrite: $output"

for command in age tar sha256sum python3 mktemp realpath hysteria; do
  recovery_require_command "$command"
done

output_parent="$(realpath -m -- "$(dirname -- "$output")")"
[[ "$output_parent" != "/" ]] || recovery_fail "refusing to write a recovery bundle directly under /"
install -d -m 0700 "$output_parent"

for path in "${extra_paths[@]}"; do
  [[ "$path" == /* ]] || recovery_fail "--extra-path must be absolute: $path"
  case "$path" in
    /|/etc|/root|/usr|/var|*/../*|*/..) recovery_fail "extra path is too broad or unsafe: $path" ;;
  esac
  [[ -e "$path" || -L "$path" ]] || recovery_fail "extra path does not exist: $path"
done

umask 077
work_dir="$(mktemp -d /tmp/hyboard-recovery.XXXXXXXX)"
stage_dir="$work_dir/stage"
plain_archive="$work_dir/recovery.tar.gz"
output_complete=0
cleanup() {
  if [[ $output_complete -ne 1 && -f "$output" ]]; then
    rm -f -- "$output"
  fi
  case "$work_dir" in
    /tmp/hyboard-recovery.*) rm -rf -- "$work_dir" ;;
  esac
}
trap cleanup EXIT INT TERM
install -d -m 0700 "$stage_dir/meta" "$stage_dir/rootfs"

copy_path() {
  local source="$1"
  local target
  [[ -e "$source" || -L "$source" ]] || return 0
  target="$stage_dir/rootfs$source"
  install -d -m 0700 "$(dirname -- "$target")"
  cp -a -- "$source" "$target"
}

for required in \
  /etc/hysteria/config.yaml \
  /etc/hysteria/users.json \
  /usr/local/sbin/hy-access \
  /etc/hyboard/hyboard.env \
  /var/lib/hyboard/hyboard.db; do
  [[ -f "$required" ]] || recovery_fail "required source file is missing: $required"
done

hysteria_binary="$(readlink -f -- "$(command -v hysteria)")"
case "$hysteria_binary" in
  /usr/bin/hysteria|/usr/local/bin/hysteria) ;;
  *) recovery_fail "unsupported Hysteria binary location: $hysteria_binary" ;;
esac

# Core data and the management plane. Everything remains inside the encrypted archive.
for path in \
  /etc/hysteria \
  "$hysteria_binary" \
  /root/hysteria-access \
  /usr/local/sbin/hy-access \
  /etc/systemd/system/hysteria-server.service \
  /usr/local/sbin/hysteria-data-plane-check \
  /usr/local/sbin/hyboard-probe-report \
  /root/windows-hysteria-probe.yaml \
  /etc/default/hysteria-healthcheck \
  /etc/systemd/system/hysteria-data-plane-check.service \
  /etc/systemd/system/hysteria-data-plane-check.timer \
  /etc/hyboard \
  /usr/local/libexec/hyboard-helper \
  /usr/local/libexec/hyboard-helperd \
  /etc/systemd/system/hyboard.service \
  /etc/systemd/system/hyboard-helper.service \
  /etc/systemd/system/hyboard-expire.service \
  /etc/systemd/system/hyboard-expire.timer \
  /etc/systemd/system/hyboard-monitor.service \
  /etc/systemd/system/hyboard-monitor.timer \
  /etc/nginx \
  /etc/letsencrypt \
  /etc/ssh/sshd_config \
  /etc/ssh/sshd_config.d \
  /root/.ssh/authorized_keys \
  /etc/ufw \
  /etc/sysctl.conf \
  /etc/sysctl.d; do
  copy_path "$path"
done

# SQLite online backup gives a consistent database even while HyBoard is running.
install -d -m 0700 "$stage_dir/rootfs/var/lib/hyboard"
python3 -c 'import sqlite3,sys; src=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro", uri=True); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()' \
  /var/lib/hyboard/hyboard.db \
  "$stage_dir/rootfs/var/lib/hyboard/hyboard.db"
chmod 0600 "$stage_dir/rootfs/var/lib/hyboard/hyboard.db"

# Client PKI directories are optional, but are essential if the existing .p12 identity
# must survive a total server loss. They are never copied outside the encrypted bundle.
while IFS= read -r -d '' pki_dir; do
  copy_path "$pki_dir"
done < <(find /root -mindepth 1 -maxdepth 1 -type d -name 'hyboard-client-pki-*' -print0 2>/dev/null)

for path in "${extra_paths[@]}"; do
  copy_path "$path"
done

printf '1\n' > "$stage_dir/meta/bundle-version"
date -u +%Y-%m-%dT%H:%M:%SZ > "$stage_dir/meta/created-at-utc"
printf '%s\n' "$recipient" > "$stage_dir/meta/age-recipient"
printf '%s\n' "$hysteria_binary" > "$stage_dir/meta/hysteria-binary-path"
"$SCRIPT_DIR/recovery-inventory.sh" > "$stage_dir/meta/inventory.txt"
find "$stage_dir/rootfs" -mindepth 1 -printf '%P\n' | LC_ALL=C sort > "$stage_dir/meta/file-list.txt"
find "$stage_dir/rootfs" -type l -printf '%P -> %l\n' | LC_ALL=C sort > "$stage_dir/meta/symlinks.txt"

manifest_tmp="$stage_dir/manifest.tmp"
(
  cd "$stage_dir"
  find meta rootfs -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
) > "$manifest_tmp"
mv "$manifest_tmp" "$stage_dir/meta/manifest.sha256"

tar -C "$stage_dir" -czf "$plain_archive" meta rootfs
age --recipient "$recipient" --output "$output" "$plain_archive"
chmod 0600 "$output"
output_complete=1

printf 'Encrypted recovery bundle created: %s\n' "$output"
sha256sum "$output"
printf 'Copy the .age file off the server and verify it before treating it as a backup.\n'
