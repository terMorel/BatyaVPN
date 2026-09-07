#!/usr/bin/env bash
set -euo pipefail

file_metadata() {
  local path="$1"
  if [[ -e "$path" ]]; then
    printf '%s\n' "$(stat -c '%A %U:%G %s bytes %y' "$path")  $path"
    if [[ -f "$path" ]]; then
      sha256sum "$path"
    fi
  else
    printf 'MISSING  %s\n' "$path"
  fi
}

service_state() {
  local unit="$1"
  printf '%-40s enabled=%-10s active=%s\n' \
    "$unit" \
    "$(systemctl is-enabled "$unit" 2>/dev/null || true)" \
    "$(systemctl is-active "$unit" 2>/dev/null || true)"
}

printf 'HyBoard/Hysteria recovery inventory\n'
printf 'Generated (UTC): %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'Hostname: %s\n' "$(hostname)"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  printf 'OS: %s\n' "${PRETTY_NAME:-unknown}"
fi
printf 'Kernel: %s\n' "$(uname -srmo)"
printf '\nVersions\n'
printf 'Hysteria: %s\n' "$(hysteria version 2>&1 | head -n 1 || true)"
printf 'Python: %s\n' "$(python3 --version 2>&1 || true)"
printf 'Nginx: %s\n' "$(nginx -v 2>&1 || true)"
printf 'Age: %s\n' "$(age --version 2>&1 | head -n 1 || true)"
if command -v dpkg-query >/dev/null 2>&1; then
  printf 'Packages:\n'
  dpkg-query -W -f='  ${Package} ${Version}\n' age certbot nginx python3 python3-venv 2>/dev/null || true
fi

printf '\nService state\n'
for unit in \
  hysteria-server.service \
  hysteria-data-plane-check.timer \
  hyboard.service \
  hyboard-helper.service \
  hyboard-expire.timer \
  hyboard-monitor.timer \
  snap.certbot.renew.timer \
  nginx.service; do
  service_state "$unit"
done

printf '\nRelevant listeners\n'
ss -H -lntup 2>/dev/null | grep -E '(:22|:80|:443|:9999|:28474)([[:space:]]|$)' || true

printf '\nFirewall and network tuning (read-only)\n'
if command -v ufw >/dev/null 2>&1; then
  ufw status verbose 2>/dev/null || true
fi
if command -v nft >/dev/null 2>&1; then
  nft list ruleset 2>/dev/null || true
fi
sysctl net.core.rmem_max net.core.wmem_max net.core.netdev_max_backlog 2>/dev/null || true

printf '\nCore file metadata and checksums\n'
for path in \
  /etc/hysteria/config.yaml \
  /etc/hysteria/users.json \
  /usr/local/sbin/hy-access \
  /usr/local/sbin/hysteria-data-plane-check \
  /usr/local/sbin/hyboard-probe-report \
  /etc/hyboard/hyboard.env \
  /var/lib/hyboard/hyboard.db \
  /etc/systemd/system/hysteria-server.service \
  /etc/systemd/system/hysteria-data-plane-check.service \
  /etc/systemd/system/hysteria-data-plane-check.timer \
  /etc/systemd/system/hyboard.service \
  /etc/nginx/sites-available/hyboard \
  /etc/nginx/hyboard-client-ca.crt; do
  file_metadata "$path"
done

printf '\nNon-secret configuration facts\n'
if grep -Eq '^[[:space:]]*trafficStats:' /etc/hysteria/config.yaml 2>/dev/null; then
  printf 'Hysteria Traffic Stats API: configured\n'
else
  printf 'Hysteria Traffic Stats API: not configured\n'
fi
if grep -Eq '^HYBOARD_TRAFFIC_STATS_ENABLED=1$' /etc/hyboard/hyboard.env 2>/dev/null; then
  printf 'HyBoard Traffic Stats collection: enabled\n'
else
  printf 'HyBoard Traffic Stats collection: disabled or unknown\n'
fi
if [[ -r /var/lib/hysteria-healthcheck/status.json ]]; then
  python3 -c 'import json; d=json.load(open("/var/lib/hysteria-healthcheck/status.json")); print("Hysteria authenticated data plane: healthy" if d.get("healthy") else "Hysteria authenticated data plane: failed")' 2>/dev/null || true
else
  printf 'Hysteria authenticated data plane: not configured\n'
fi

python3 - <<'PY' 2>/dev/null || true
import json
import sqlite3
from pathlib import Path

users_path = Path("/etc/hysteria/users.json")
if users_path.exists():
    data = json.loads(users_path.read_text(encoding="utf-8"))
    print(f"Hysteria credential entries: {len(data) if isinstance(data, dict) else 'unknown'}")

db_path = Path("/var/lib/hyboard/hyboard.db")
if db_path.exists():
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        for table in ("users_meta", "traffic_samples", "external_probes"):
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.DatabaseError:
                continue
            print(f"HyBoard {table} rows: {count}")
PY

printf '\nRepository\n'
# BatyaVPN is canonical; the previous checkout path remains detectable on servers
# that have not renamed their local directory yet.
for repo in /opt/hyboard /root/BatyaVPN /root/FuckingWhitelists; do
  if git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf '%s: %s\n' "$repo" "$(git -C "$repo" rev-parse HEAD)"
  fi
done
