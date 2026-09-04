# Alloy

## Formatting

Formatting requires Docker Compose and a running Docker daemon. The Alloy image
is pinned only in `docker-compose.yml`. Formatting commands resolve it with
`docker compose config --images alloy`, so runtime, local checks, and CI use the
same image. To upgrade Alloy, change `services.alloy.image` in that file.

```sh
make fmt           # Format config.alloy in place
make fmt-check     # Check without modifying config.alloy
make install-hooks # Install the pre-commit hook once per clone
```

The hook checks the staged version of `config.alloy` only when it is added or
modified. It does not modify files or stage changes. On failure, run `make fmt`,
review and stage the desired changes, then retry the commit. When partially
staging a file, use `git add -p config.alloy` to avoid staging unrelated edits.

Installation refuses to overwrite an existing pre-commit hook; integrate
`.githooks/pre-commit` into that hook manually if needed. After changing the
tracked hook, update the installed copy as well. CI always runs `make fmt-check`,
including when a local hook was not installed or was bypassed.
