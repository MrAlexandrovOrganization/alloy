#!/bin/sh
set -eu

refuse() {
	printf '%s\n' "$*" >&2
	exit 1
}

case "${1:-}" in
	install|migrate) ;;
	*) refuse 'Usage: install-hooks.sh install|migrate' ;;
esac

if git config --get-all core.hooksPath >/dev/null; then
	refuse 'Refusing to install: core.hooksPath is configured. Resolve it manually first.'
fi
hook=$(git rev-parse --git-path hooks/pre-commit)
backup="$hook.alloy-legacy"
# The framework would silently chain this hook even on a fresh installation.
if [ -e "$hook.legacy" ] || [ -L "$hook.legacy" ]; then
	refuse "Refusing to install: framework legacy hook exists: $hook.legacy"
fi
if [ "$1" = migrate ]; then
	# Pin the original bytes, not the potentially edited tracked legacy file.
	[ -f "$hook" ] && [ ! -L "$hook" ] || refuse 'No regular legacy hook to migrate.'
	[ "$(git hash-object --no-filters "$hook")" = 1932a720ab1b7b241536d6b35571b16e11a47295 ] ||
		refuse 'Refusing to migrate an unknown hook.'
	[ ! -e "$backup" ] && [ ! -L "$backup" ] || refuse "Backup already exists: $backup"
elif [ -e "$hook" ] || [ -L "$hook" ]; then
	refuse 'Refusing to replace an existing hook. For the original Alloy hook, use make migrate-hooks.'
fi

# PRE_COMMIT is a command override, e.g. an absolute path or "uv tool run pre-commit".
${PRE_COMMIT:-pre-commit} --version
if [ "$1" = migrate ]; then
	mv "$hook" "$backup"
	# Restore the original hook if installation fails or is interrupted.
	trap 'status=$?; if [ "$status" -ne 0 ]; then mv "$backup" "$hook"; fi' EXIT
	trap 'exit 1' HUP INT TERM
fi
${PRE_COMMIT:-pre-commit} install --hook-type pre-commit
