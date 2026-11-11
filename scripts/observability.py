#!/usr/bin/env python3
"""Assemble pinned service observability packages without sibling checkouts."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import io
import os
import sys
import json
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

from observability_package import (
    ID,
    KINDS,
    MANIFEST,
    SCHEMA,
    atomic,
    digest,
    encoded,
    export,
    package,
    relative,
    validate,
)


def sync(collector, source=None, archive=None):
    collector = Path(collector).resolve()
    if archive:
        with tarfile.open(archive, "r:") as bundle:
            members = bundle.getmembers()
            if len({m.name for m in members}) != len(members):
                raise ValueError("Duplicate archive member")
            if any(not m.isfile() or m.size > 16 * 1024 * 1024 for m in members):
                raise ValueError("Only bounded regular package files are accepted")
            if sum(m.size for m in members) > 64 * 1024 * 1024:
                raise ValueError("Package is too large")
            files = {
                str(relative(m.name)): bundle.extractfile(m).read() for m in members
            }
        lock = json.loads(files.pop("package.json"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, data in files.items():
                atomic(root / name, data)
            doc, checked = validate(root)
        if files != checked or doc["service"] != lock["service"]:
            raise ValueError("Archive differs from its manifest")
        hashes = {name: digest(data) for name, data in files.items()}
        if hashes != lock["files"] or digest(encoded(hashes)) != lock["content_sha256"]:
            raise ValueError("Archive checksum mismatch")
    else:
        lock, files = package(source)
    registry = collector / ".observability/inputs/services.lock.json"
    doc = (
        json.loads(registry.read_text())
        if registry.exists()
        else {"schema": SCHEMA, "services": {}}
    )
    identity = lock["service"]
    if not ID.fullmatch(identity) or not re.fullmatch(
        r"[a-f0-9]{64}", lock["content_sha256"]
    ):
        raise ValueError("Invalid package identity or digest")
    snapshot = collector / ".observability/inputs/packages" / identity
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    # Publish an immutable content directory before changing the lock. This
    # avoids partially rewritten inputs even when a sync is interrupted.
    snapshot.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot / lock["content_sha256"]
    if not snapshot.exists():
        temporary = Path(tempfile.mkdtemp(prefix=".package-", dir=snapshot.parent))
        try:
            for name, data in files.items():
                atomic(temporary / name, data)
            temporary.rename(snapshot)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    doc["services"][identity] = lock
    atomic(registry, encoded(doc))
    return identity


def inputs_root(collector):
    return Path(
        os.environ.get("OBSERVABILITY_INPUTS", str(collector / ".observability/inputs"))
    ).resolve()


def deployment(collector):
    path = inputs_root(collector) / "deployment.json"
    if not path.exists():
        return {
            "schema": SCHEMA,
            "hosts": [],
            "central": [],
            "services": {},
            "inventory": {},
        }
    plan = json.loads(path.read_text())
    hosts = plan.get("hosts", [])
    if (
        plan.get("schema") != SCHEMA
        or not isinstance(hosts, list)
        or len(set(hosts)) != len(hosts)
        or any(not ID.fullmatch(h) for h in hosts)
    ):
        raise ValueError("Invalid private deployment inventory")
    if set(plan.get("central", [])) - set(hosts):
        raise ValueError("Central placement is outside active inventory")
    for value in plan.get("services", {}).values():
        if set(value.get("local", [])) - set(hosts):
            raise ValueError("Local placement is outside active inventory")
    return plan


def public_package(collector):
    doc, files = validate(collector)
    hashes = {name: digest(data) for name, data in files.items()}
    return (
        doc["service"],
        {"files": hashes, "content_sha256": digest(encoded(hashes))},
        doc,
        files,
    )


def read_packages(collector, public=False):
    root = inputs_root(collector)
    registry_path = root / "services.lock.json"
    if public or not registry_path.exists():
        yield public_package(collector)
        return
    registry = json.loads(registry_path.read_text())
    if registry.get("schema") != SCHEMA:
        raise ValueError("Unsupported registry schema")
    for identity, pin in sorted(registry["services"].items()):
        if not ID.fullmatch(identity) or pin["service"] != identity:
            raise ValueError("Invalid package registry identity")
        if not re.fullmatch(r"[a-f0-9]{64}", pin["content_sha256"]):
            raise ValueError("Invalid package digest")
        snapshot = root / "packages" / identity / pin["content_sha256"]
        doc, files = validate(snapshot)
        hashes = {name: digest(body) for name, body in files.items()}
        if hashes != pin["files"] or digest(encoded(hashes)) != pin["content_sha256"]:
            raise ValueError("Package checksum mismatch")
        if doc["service"] != identity:
            raise ValueError("Snapshot identity mismatch")
        yield identity, pin, doc, files


def render(collector, public=False):
    collector = Path(collector).resolve()
    plan = (
        deployment(collector)
        if not public
        else {"hosts": [], "central": [], "services": {}}
    )
    stages = {"parse": [], "normalize": []}
    outputs = {}
    owners = {}
    uids = {}
    adoption = {}
    components = {}
    retire = {}

    def hosts_for(identity, item, default):
        location = item.get("location", default)
        hosts = (
            plan.get("central", [])
            if location == "central"
            else plan.get("services", {}).get(identity, {}).get("local", [])
        )
        if not hosts:
            raise ValueError("Resource lacks a private placement")
        return hosts

    for identity, pin, doc, files in read_packages(collector, public):
        for component in doc["components"]:
            project, service = component.split("/")
            for other, owner in components.items():
                other_project, other_service = other.split("/")
                if project == other_project and (
                    service == other_service or "*" in (service, other_service)
                ):
                    raise ValueError(
                        "Component ownership conflict: "
                        + component
                        + " ("
                        + owner
                        + ", "
                        + identity
                        + ")"
                    )
            components[component] = identity
        for stage in doc.get("logs", []):
            comment = ""
            stages[stage["phase"]].append(
                (
                    stage.get("order", 100),
                    identity,
                    stage["file"],
                    comment + files[stage["file"]].decode().strip() + "\n",
                )
            )
        for item in doc.get("retire", []):
            for host in hosts_for(identity, item, "central"):
                name = f"hosts/{host}/{KINDS[item['kind']]}/{item['destination']}"
                retire.setdefault(name, []).extend(item["sha256"])
        for asset in doc.get("assets", []):
            body = files[asset["file"]]
            if asset["kind"] == "dashboards":
                uid = json.loads(body)["uid"]
                if uid in uids:
                    raise ValueError(
                        "Dashboard UID conflict: "
                        + uid
                        + " ("
                        + uids[uid]
                        + ", "
                        + identity
                        + ")"
                    )
                uids[uid] = identity
            for host in hosts_for(identity, asset, "local"):
                destination = str(relative(asset["destination"]))
                name = f"hosts/{host}/{KINDS[asset['kind']]}/{destination}"
                if name in outputs:
                    raise ValueError("Asset output conflict: " + name)
                outputs[name] = body
                owners[name] = identity
                adoption[name] = asset.get("adopt_sha256", [])
    base = (collector / "observability/base.alloy").read_text()
    for phase in stages:
        marker = "// @service-stages:" + phase + "@"
        if base.count(marker) != 1:
            raise ValueError("Missing or duplicate assembly marker: " + phase)
        chunks = [entry[3] for entry in sorted(stages[phase])]
        indented = "\n\n".join(
            "\n".join(
                "\t" + line if line else "" for line in chunk.rstrip().splitlines()
            )
            for chunk in chunks
        )
        base = base.replace("\t" + marker, indented)
    for host, observed in plan.get("inventory", {}).items():
        if host not in plan["hosts"]:
            raise ValueError("Observed placement is outside active inventory")
        for component in observed:
            matches = [
                c
                for c in components
                if c == component or c == component.split("/")[0] + "/*"
            ]
            if len(matches) != 1:
                raise ValueError("Observed component must have one package owner")
    base = re.sub(r"\n(?:[ \t]*\n){2,}", "\n\n", base)
    outputs["config.alloy"] = (
        "// Generated by make observability-generate. Edit the owning service package.\n"
        + base
    ).encode()
    if set(outputs) & set(retire):
        raise ValueError("Asset cannot be both installed and retired")
    outputs["retire.json"] = encoded(retire)
    outputs["assets.json"] = encoded(
        {
            "schema": SCHEMA,
            "owners": owners,
            "adopt_sha256": adoption,
            "components": components,
        }
    )
    return outputs


def generate(collector, output, check=False, public=False):
    collector, output = Path(collector).resolve(), Path(output).resolve()
    files = render(collector, public)
    for name, data in files.items():
        destination = (
            collector / "config.alloy"
            if name == "config.alloy" and public
            else output / name
        )
        if check:
            if name != "config.alloy":
                continue
            if not destination.exists() or destination.read_bytes() != data:
                raise ValueError(
                    "Generated observability file is stale: " + str(destination)
                )
        else:
            atomic(destination, data)
    if not check:
        # Only prune this generator's output tree, never the shared live registry.
        expected = set(files) - ({"config.alloy"} if public else set())
        if output.exists():
            for p in output.rglob("*"):
                if p.is_file() and p.relative_to(output).as_posix() not in expected:
                    p.unlink()
    return files


def install(collector, output, root, host):
    collector = Path(collector).resolve()
    if (inputs_root(collector) / "deployment.json").exists() and host not in deployment(collector)["hosts"]:
        raise ValueError("Refusing to install for an inactive host")
    if not (inputs_root(collector) / "services.lock.json").exists():
        return 0  # Standalone public template has no private service assets.
    if host not in deployment(collector)["hosts"]:
        raise ValueError("Refusing to install for an inactive host")
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".observability-install.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        return install_locked(Path(collector).resolve(), root, host)


def install_locked(collector, root, host):
    files = render(collector)
    prefix = f"hosts/{host}/"
    selected = {
        name[len(prefix) :]: data
        for name, data in files.items()
        if name.startswith(prefix)
    }
    metadata = json.loads(files["assets.json"])
    ledger = root / "observability-managed.json"
    if ledger.is_symlink():
        raise ValueError("Symlinked managed ledger")
    previous = (
        json.loads(ledger.read_text())
        if ledger.exists()
        else {"schema": SCHEMA, "host": host, "files": {}}
    )
    if previous.get("schema") != SCHEMA or previous.get("host") != host:
        raise ValueError("Managed registry belongs to another host or schema")

    def destination(name):
        path = root / relative(name)
        current = path
        while current != root:
            if current.is_symlink():
                raise ValueError("Refusing a symlinked observability destination")
            current = current.parent
        return path

    # Preflight every file, including removals, before any mutation. Legacy
    # YAML-to-JSON adoption requires an explicitly recorded original hash.
    retired = {
        name[len(prefix) :]: hashes
        for name, hashes in json.loads(files["retire.json"]).items()
        if name.startswith(prefix)
    }
    obsolete = (set(previous["files"]) | set(retired)) - set(selected)
    backup = {}
    backup_owners = {}
    for name in sorted(set(selected) | obsolete):
        path = destination(name)
        old = path.read_bytes() if path.exists() else None
        backup[name] = old
        if old is not None:
            info = path.stat()
            backup_owners[name] = (info.st_uid, info.st_gid)
        accepted = {previous["files"].get(name)}
        accepted.update(retired.get(name, []))
        if name in selected:
            accepted.add(digest(selected[name]))
            accepted.update(metadata["adopt_sha256"].get(prefix + name, []))
        if old is not None and digest(old) not in accepted:
            raise ValueError("Unmanaged or locally edited observability file: " + name)
    old_ledger = ledger.read_bytes() if ledger.exists() else None
    owner = (root.stat().st_uid, root.stat().st_gid)
    try:
        for name, data in selected.items():
            path = root / name
            # Shared bind-mounted assets must remain readable under umask 077.
            parent = path.parent
            missing = []
            while parent != root and not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                parent.mkdir(mode=0o755)
                parent.chmod(0o755)
                os.chown(parent, *owner)
            parent = path.parent
            while parent != root:
                parent.chmod(0o755)
                parent = parent.parent
            atomic(path, data)
            os.chown(path, *owner)
        for name in obsolete:
            (root / name).unlink(missing_ok=True)
        atomic(
            ledger,
            encoded(
                {
                    "schema": SCHEMA,
                    "host": host,
                    "files": {name: digest(data) for name, data in selected.items()},
                }
            ),
        )
    except BaseException:
        for name, data in backup.items():
            if data is None:
                (root / name).unlink(missing_ok=True)
            else:
                atomic(root / name, data)
                os.chown(root / name, *backup_owners[name])
        if old_ledger is None:
            ledger.unlink(missing_ok=True)
        else:
            atomic(ledger, old_ledger)
        raise
    return len(selected)


def receive(collector, data):
    collector = Path(collector).resolve()
    private = collector / ".observability"
    private.mkdir(exist_ok=True, mode=0o700)
    private.chmod(0o700)
    target = private / "inputs"
    if not data:
        if not (target / "services.lock.json").exists():
            raise ValueError("A private input bundle is required for deployment")
        return
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Private input bundle exceeds transport limit")
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        unpacked = stream.read(16 * 1024 * 1024 + 1)
    if len(unpacked) > 16 * 1024 * 1024:
        raise ValueError("Private input bundle exceeds expanded limit")
    temporary = Path(tempfile.mkdtemp(prefix=".inputs-", dir=private))
    try:
        with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as archive:
            members = archive.getmembers()
            if len({m.name for m in members}) != len(members):
                raise ValueError("Duplicate bundle member")
            for member in members:
                path = relative(member.name)
                if not member.isfile() or member.size > 8 * 1024 * 1024:
                    raise ValueError("Invalid private bundle member")
                atomic(temporary / path, archive.extractfile(member).read())
        previous = os.environ.get("OBSERVABILITY_INPUTS")
        os.environ["OBSERVABILITY_INPUTS"] = str(temporary)
        try:
            render(collector)
            for _, pin, _, _ in read_packages(collector):
                if pin.get("source_state") != "commit" or not re.fullmatch(
                    r"[a-f0-9]{40}", pin.get("revision", "")
                ):
                    raise ValueError(
                        "Private bundle must reference committed resources"
                    )
        finally:
            if previous is None:
                os.environ.pop("OBSERVABILITY_INPUTS", None)
            else:
                os.environ["OBSERVABILITY_INPUTS"] = previous
        archived = private / "bundles" / (digest(data) + ".tar.gz")
        atomic(archived, data)
        archived.chmod(0o600)
        if target.exists():
            backup = private / "inputs.previous"
            if backup.exists():
                shutil.rmtree(backup)
            target.rename(backup)
        temporary.rename(target)
        atomic(private / "input.sha256", (digest(data) + "\n").encode())
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def bundle(collector, destination):
    root = inputs_root(Path(collector).resolve())
    render(Path(collector).resolve())
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        paths = [root / "deployment.json", root / "services.lock.json"]
        for _, pin, _, _ in read_packages(Path(collector).resolve()):
            for name in pin["files"]:
                paths.append(
                    root / "packages" / pin["service"] / pin["content_sha256"] / name
                )
        for path in sorted(paths):
            body = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(root).as_posix())
            info.size, info.mode, info.mtime, info.uid, info.gid = (
                len(body),
                0o600,
                0,
                0,
                0,
            )
            archive.addfile(info, io.BytesIO(body))
    atomic(destination, gzip.compress(buffer.getvalue(), mtime=0))
    destination.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "check",
            "export",
            "sync",
            "generate",
            "generated-check",
            "release-check",
            "install",
            "receive",
            "bundle",
        ),
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--collector", type=Path)
    parser.add_argument(
        "--package", type=Path, help="Explicit exported service package for sync"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--monitoring-root", type=Path)
    parser.add_argument("--host")
    parser.add_argument(
        "--public",
        action="store_true",
        help="Use only this repository own public resources",
    )
    args = parser.parse_args()
    if args.command == "check":
        doc, _ = validate(args.root)
        print("Observability package validated: " + doc["service"])
    elif args.command == "export":
        pin = export(args.root, args.output or args.root / ".observability/package.tar")
        print("Exported " + pin["service"] + " " + pin["content_sha256"])
    elif args.command == "sync":
        if not args.collector:
            parser.error("sync requires --collector")
        print("Pinned " + sync(args.collector, args.root, args.package))
    elif args.command == "receive":
        receive(
            args.root,
            b"" if sys.stdin.isatty() else sys.stdin.buffer.read(2 * 1024 * 1024 + 1),
        )
        print("Private deployment inputs verified")
    elif args.command == "bundle":
        bundle(args.root, args.output or args.root / ".observability/inputs.tar.gz")
        print("Private deployment bundle created")
    elif args.command == "release-check":
        if not (inputs_root(args.root.resolve()) / "services.lock.json").exists():
            print("No private deployment inputs in this checkout")
            return
        for identity, pin, _, _ in read_packages(args.root.resolve()):
            if pin.get("source_state") != "commit" or not re.fullmatch(
                r"[a-f0-9]{40}", pin.get("revision", "")
            ):
                raise ValueError(
                    "Release package must reference committed resources: " + identity
                )
        print("All service inputs reference committed resources")
    else:
        output = args.output or args.root / ".generated/observability"
        if args.command == "install":
            if not args.monitoring_root or not args.host:
                parser.error("install requires --monitoring-root and --host")
            print(
                "Installed "
                + str(install(args.root, output, args.monitoring_root, args.host))
                + " managed observability assets"
            )
        else:
            generate(args.root, output, args.command == "generated-check", args.public)
            print(
                "Service observability assembled"
                if args.command == "generate"
                else "Generated observability is current"
            )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        if not sys.stdin.isatty():
            raise SystemExit(
                "Observability operation failed; inspect private inputs locally"
            ) from None
        raise SystemExit(str(error)) from None
