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
    # Keep mountpoint directories in the image layer intact: replacing them
    # with /proc symlinks prevents runc mounting them on a container restart.
    # Temporary views disappear with this mount namespace, even after SIGKILL.
    umount -l /rootfs
    mount -t tmpfs -o size=1m,mode=755,nodev,nosuid tmpfs /rootfs
    for entry in "$runtime_root"/* "$runtime_root"/.[!.]* "$runtime_root"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        ln -s "$entry" "/rootfs/${entry##*/}"
    done
    ln -s "$runtime_root" /rootfs/.alloy-runtime-root
    # Statfs('/') must describe the runtime filesystem, not the temporary view.
    export ALLOY_ROOTFS_PATH=/rootfs/.alloy-runtime-root
    umount -l /var/lib/docker
    mount -t tmpfs -o size=1m,mode=755,nodev,nosuid tmpfs /var/lib/docker
    for entry in "$runtime_root/var/lib/docker"/* "$runtime_root/var/lib/docker"/.[!.]* "$runtime_root/var/lib/docker"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        ln -s "$entry" "/var/lib/docker/${entry##*/}"
    done
fi

exec /bin/alloy "$@"
