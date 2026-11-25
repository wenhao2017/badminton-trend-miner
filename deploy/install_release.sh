#!/usr/bin/env bash
set -euo pipefail
umask 027

if [[ $EUID -ne 0 || $# -ne 1 ]]; then
    echo "Usage: run as root with a trusted local source.tar.gz" >&2
    exit 2
fi

archive=$(realpath "$1")
base=/opt/badminton-trend-miner
config=/etc/badminton-trend-miner
state=/var/lib/badminton-trend-miner
backup=/var/backups/badminton-trend-miner
service=badminton-trend-miner

exec 9>/run/lock/badminton-trend-miner-deploy.lock
flock -n 9
install -d -m 0755 "$base" "$base/releases"
install -d -m 0700 "$config"
if ! id badminton >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$state" --shell /usr/sbin/nologin badminton
fi
install -d -m 0750 -o badminton -g badminton "$state" "$state/data" "$state/models" "$backup"

release=$(mktemp -d "$base/releases/$(date -u +%Y%m%dT%H%M%SZ).XXXXXX")
tar -xzf "$archive" -C "$release" --no-same-owner --no-same-permissions
chmod 0755 "$release"
chmod -R a+rX,go-w "$release"
if [[ ! -f "$config/service.env" ]]; then
    (
        umask 077
        cat "$release/deploy/production.env.example"
        printf 'WEB_AUTH_PASSWORD=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
        printf 'WEB_SESSION_SECRET=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    ) > "$config/service.env"
    chmod 0600 "$config/service.env"
fi

shared_venv="$base/.venv"
previous=$(readlink -f "$base/current" || true)
if [[ ! -x "$shared_venv/bin/python" ]]; then
    if [[ -n "$previous" && -d "$previous/.venv" && ! -L "$previous/.venv" ]]; then
        mv "$previous/.venv" "$shared_venv"
        ln -s "$shared_venv" "$previous/.venv"
    else
        if ! python3 -m venv "$shared_venv"; then
            python3 -m venv --without-pip "$shared_venv"
            bootstrap=$(mktemp)
            trap 'rm -f "$bootstrap"' EXIT
            curl --proto '=https' -fsSL https://bootstrap.pypa.io/get-pip.py -o "$bootstrap"
            "$shared_venv/bin/python" "$bootstrap" --no-cache-dir
        fi
    fi
fi

req_hash=$(sha256sum "$release/requirements.txt" | cut -d ' ' -f 1)
installed_hash=$(cat "$shared_venv/.requirements.sha256" 2>/dev/null || true)
if [[ "$req_hash" != "$installed_hash" ]]; then
    constraints=()
    if [[ -f "$config/runtime-constraints.txt" ]]; then
        constraints=(-c "$config/runtime-constraints.txt")
    fi
    "$shared_venv/bin/python" -m pip install --no-cache-dir --only-binary=:all: --disable-pip-version-check \
        "${constraints[@]}" -r "$release/requirements.txt"
    "$shared_venv/bin/python" -m pip check
    printf '%s\n' "$req_hash" > "$shared_venv/.requirements.sha256"
    chmod -R a+rX,go-w "$shared_venv"
fi

ln -s "$shared_venv" "$release/.venv"
"$release/.venv/bin/python" -m pip freeze > "$release/runtime-versions.txt"
sha256sum "$archive" | cut -d ' ' -f 1 > "$release/source.sha256"

activate() {
    local next="$base/.current.$$"
    ln -s "$1" "$next"
    mv -Tf "$next" "$base/current"
}

for unit in "$release"/deploy/systemd/*; do
    install -m 0644 "$unit" /etc/systemd/system/
done
activate "$release"
systemctl daemon-reload
systemd-analyze verify "$service.service"

if ! { systemctl restart "$service.service" &&
       curl --retry 30 --retry-delay 1 --retry-connrefused -fsS http://127.0.0.1:8010/health &&
       systemctl is-active --quiet "$service.service"; }; then
    journalctl -u "$service.service" -n 30 --no-pager >&2
    if [[ -n "$previous" && -d "$previous" ]]; then
        activate "$previous"
        systemctl restart "$service.service" || true
    else
        systemctl stop "$service.service" || true
    fi
    exit 1
fi

systemctl enable "$service.service"
(cd "$base/releases" && ls -1dt 20* | tail -n +3 | xargs -r rm -rf)
printf '\nActive release: %s\n' "$release"
