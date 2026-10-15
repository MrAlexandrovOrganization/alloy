#!/bin/sh
set -eu

# Docker Desktop's dockerd has a different mount namespace from Ubuntu.
# Bind its image metadata read-only inside this collector's private namespace.
if [ "${ALLOY_DOCKER_DESKTOP:-false}" = true ]; then
    runtime_root=
    for process in /proc/[0-9]*; do
        [ -f "$process/root/run/desktop/docker.pid" ] || continue
        [ "$(cat "$process/root/run/desktop/docker.pid")" = "${process##*/}" ] || continue
        [ "$(cat "$process/comm" 2>/dev/null || true)" = dockerd ] || continue
        [ -z "$runtime_root" ] || { echo 'Multiple dockerd processes; refusing ambiguous runtime root' >&2; exit 1; }
        runtime_root="$process/root"
    done
    [ -n "$runtime_root" ] && [ -d "$runtime_root/var/lib/docker/image" ] || {
        echo 'Docker Desktop image metadata is unavailable' >&2
        exit 1
    }
    for target in /var/lib/docker /rootfs/var/lib/docker; do
        mount --bind "$runtime_root/var/lib/docker" "$target"
        mount -o remount,bind,ro "$target"
    done
fi

exec /bin/alloy "$@"
