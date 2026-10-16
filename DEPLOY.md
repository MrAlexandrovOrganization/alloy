# SSH delivery of observability

GitHub-hosted Actions validate each repository and deploy the same commit SHA.
Prometheus, Loki and Grafana target only WSL; Alloy uses a six-host matrix.
The public SSH gateway is Hostkey Russia (`193.187.93.245`). The other targets
use native SSH port 22 over Headscale: server `.1`, WSL `.3`, private-test `.4`,
Finland `.6`, ASUS `.11` in `100.64.0.0/24`. WSL's LAN port 2222 is not used by Actions.

## Trust and bootstrap

Each repository has its own Ed25519 deploy key in the `DEPLOY_SSH_KEY` GitHub
secret. Private keys never go to the servers, repository or logs. Pinned public
SSH host keys are stored in repository variable `DEPLOY_KNOWN_HOSTS`, with
`obs-<inventory-name>` host aliases. Host keys were obtained through the existing
trusted administrative SSH connections; do not replace pinning with an unverified
`ssh-keyscan` or disable host verification.

Russia has a dedicated `observability-deploy` SSH gateway account. Each central
key permits forwarding only to WSL SSH; Alloy's key permits only the six
inventory SSH destinations. The SSH daemon allows only local TCP forwarding for
this account and prohibits remote listeners and password authentication; its
forced command denies shell sessions. On targets (including maxim on Russia),
each key has a forced receiver with its repository fixed in `authorized_keys`
and all forwarding disabled. PTY, agent forwarding and user rc files are disabled.

The root-owned `/usr/local/lib/observability-deploy/receiver.py` and
`/etc/observability-deploy.json` enforce the host/project policy. Their canonical
source is this Alloy repository because Alloy is installed on every target.
Central repositories carry independent small SSH clients; their checks/builds
do not require a neighboring Alloy checkout.

Administrative bootstrap is explicit: `make install-deploy-receiver` runs the
root-only installer with JSON stdin containing `config` (`host`, `projects`,
`sudo_make`), `keys` (repository to public key) and base64 `receiver` source.
Install only locally/CI-checked source through an existing administrative SSH
connection; on WSL root administration uses `wsl.exe -d Ubuntu -u root`.
The installer preserves unknown authorized keys and saves a private backup under
`~maxim/.local/state/observability-deploy/authorized_keys.before-*`.
Changes to the root-owned receiver require another administrative bootstrap;
ordinary project deployment does not overwrite it.

The installation's private local recovery keys are under
`~/.local/state/observability-ssh-cd/` on the administrative Mac. Never publish
that directory. To rotate, generate a new repository key, replace only its
`observability-cd-<project>-20261015` authorized-key entries, update the matching
GitHub secret and verify access before removing the former key.

## Deployment contract

`make ci-deploy` needs `DEPLOY_TARGET`, `DEPLOY_SHA`, `DEPLOY_SSH_KEY`, and
`DEPLOY_KNOWN_HOSTS`. `DEPLOY_OPERATION=probe` verifies access without fetching
Git or changing a service. The client writes credentials into a private temporary
directory and deletes it on exit. It does not forward an SSH agent.

The receiver accepts only `probe <40-character SHA>` or `deploy <SHA>`. Deployment
locks each host/project, checks the repository root, `main` branch and absence
of tracked local changes, fetches from the fixed public project repository,
rejects a workflow if remote main no longer equals its checked SHA, and permits
only a fast-forward update. It then calls `make deploy` (through root Make only
on Hostkey Russia/Finland), keeping local `.env` and named volumes.

`make deploy` recreates the service so single-file configuration bind mounts
cannot keep a stale inode, then runs readiness/delivery checks. Alloy first
checks central Prometheus/Loki readiness: a receiver outage must not cause a
collector restart and loss of the unsent queue. On WSL set Alloy's private
`PROMETHEUS_QUERY_URL=http://127.0.0.1:9090` and `LOKI_QUERY_URL` for host-side
checks; runtime remote_write still uses the Docker alias.

Only successful activation and checks write a receipt with current/previous SHA
to `~maxim/.local/state/observability-deploy/<project>.json`. A failed deployment
does not update that receipt. No automatic data/schema rollback is attempted.
Investigate failed checks and deploy a corrective commit or Git revert; original
volumes and historical migration backups remain intact.

## Actions behavior and recovery

`DEPLOY_ENABLED=true` enables deployment after validation on push to main or a
manual dispatch of main. PR validation never receives deploy credentials.
Jobs have host-specific concurrency with cancellation disabled. Alloy's matrix
does not cancel other hosts when one fails. An offline WSL prevents its SSH job;
unavailable central receivers also prevent Alloy activation without stopping it.
Re-run failed jobs when connectivity is restored. If main has moved, run the
latest workflow instead of an obsolete SHA.

Runtime failures remain visible as failed Actions jobs; a green config check
alone does not imply successful delivery. Inspect the receipt, running service
and actual ingestion. GitHub Actions has no automatic catch-up trigger merely
because WSL was switched back on.

To disable delivery, set the repository's `DEPLOY_ENABLED=false`. Existing old
`VM_*` secrets are unused by these workflows. Revoking a deploy key requires
removing only its marked authorized-key entries on Russia and its targets.
