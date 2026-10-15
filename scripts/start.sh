#!/bin/sh
set -eu

# Docker Desktop's dockerd has a different mount namespace from Ubuntu.
# Read its image metadata through procfs inside this collector's namespace.
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
    # Cross-mount-namespace bind mounts lose the procfs root semantics.
    # Detach only our private Ubuntu binds and preserve the magic procfs links.
    umount -l /rootfs
    rmdir /rootfs
    ln -s "$runtime_root" /rootfs
    umount -l /var/lib/docker
    rmdir /var/lib/docker
    ln -s "$runtime_root/var/lib/docker" /var/lib/docker
fi

exec /bin/alloy "$@"
