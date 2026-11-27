#!/usr/bin/env python3
"""Resolve a topology into everything OpenTofu and Ansible need.

    python3 scripts/render_environment.py environments/gcp/reference

Reads   applications/catalog.yml, environments/sizing.yml,
        <environment dir>/topology.yml
Writes  generated/<provider>/<environment>/
          inventory.yml          Ansible inventory: one group per node AND per application
          host_vars/<host>.yml   per-host resolved endpoints, binds, sysctls, role inputs
          tofu-input.json        nodes + node-to-node firewall flows (no IPs needed for rules)
          connectivity.md        human-readable flow list for firewall review/approval
          placement.md           which app runs where, deploy order, warnings

Nothing here contacts a VM or a cloud API. Errors stop the run; warnings do not.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
NODE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
# Addresses each cloud reserves inside a subnet: (from start, from end)
# hyperv: an internal switch behind host NAT; the host adapter is the gateway (.1)
RESERVED = {"gcp": (2, 2), "aws": (4, 1), "azure": (4, 1), "hyperv": (2, 1)}
PROXY_APP = "caddy"
BUNDLED = "bundled"


def requirements(app: str, apps: dict, settings: dict) -> list[dict]:
    """Dependencies of an application in this topology.

    app_settings.<app>.database: bundled means the application runs its own embedded
    database, so its catalog dependency on the shared PostgreSQL does not apply.
    """
    reqs = apps[app].get("requires") or []
    if (settings.get(app) or {}).get("database") == BUNDLED:
        reqs = [r for r in reqs if r["app"] != "postgresql"]
    return reqs


def needs_docker(app: str, apps: dict) -> bool:
    return apps[app].get("kind") == "compose" or bool(apps[app].get("requires_docker"))


class TopologyError(Exception):
    pass


def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


# --------------------------------------------------------------------------- validation
def validate(topo: dict, catalog: dict, sizing: dict) -> list[str]:
    """Raise TopologyError listing every problem; return warnings."""
    errors: list[str] = []
    warnings: list[str] = []
    apps = catalog["applications"]
    provider = topo.get("provider")
    nodes = topo.get("nodes") or {}

    if provider not in RESERVED:
        errors.append(f"provider must be one of {sorted(RESERVED)}, got {provider!r}")
    if not nodes:
        errors.append("topology defines no nodes")

    managed = bool(topo.get("managed_by_opentofu"))
    if provider == "hyperv" and managed:
        errors.append("provider hyperv is provisioned with Hyper-V PowerShell; "
                      "managed_by_opentofu must be false")
    if (topo.get("access") or {}).get("method") == "local" and len(nodes) != 1:
        errors.append("access.method local runs Ansible on the node itself and needs exactly one node")
    settings = topo.get("app_settings") or {}
    for app, cfg in settings.items():
        db_mode = (cfg or {}).get("database")
        if db_mode is not None and db_mode not in {BUNDLED, "external"}:
            errors.append(f"app_settings.{app}.database must be bundled or external, got {db_mode!r}")
        if db_mode == BUNDLED and not (apps.get(app) or {}).get("supports_bundled_database"):
            errors.append(f"app_settings.{app}.database: {app} has no bundled database")
    cidr = (topo.get("network") or {}).get("cidr")
    net = None
    if cidr:
        try:
            net = ipaddress.ip_network(cidr)
        except ValueError as exc:
            errors.append(f"network.cidr {cidr!r}: {exc}")
    elif managed:
        errors.append("network.cidr is required when managed_by_opentofu is true")

    placement: dict[str, str] = {}
    seen_ips: dict[str, str] = {}
    seen_hostnames: dict[str, str] = {}
    for key, node in nodes.items():
        if not NODE_KEY.match(str(key)):
            errors.append(f"node key {key!r}: use lowercase letters, digits, underscore")
        for field in ("hostname", "private_ip"):
            if not node.get(field):
                errors.append(f"node {key}: {field} is required")
        host = node.get("hostname")
        if host in seen_hostnames:
            errors.append(f"node {key}: hostname {host} already used by {seen_hostnames[host]}")
        seen_hostnames[host] = key

        ip = node.get("private_ip")
        if ip:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                errors.append(f"node {key}: private_ip {ip!r} is not an IP address")
                addr = None
            if addr and net is not None:
                lo, hi = RESERVED.get(provider, (0, 0))
                first = net.network_address + lo
                last = net.broadcast_address - hi
                if addr not in net:
                    errors.append(f"node {key}: {ip} is outside {cidr}")
                elif not first <= addr <= last:
                    errors.append(f"node {key}: {ip} is reserved by {provider} in {cidr}")
            if ip in seen_ips:
                errors.append(f"node {key}: private_ip {ip} already used by {seen_ips[ip]}")
            seen_ips[ip] = key

        if managed:
            size = node.get("size")
            if size not in (sizing.get("providers", {}).get(provider) or {}):
                errors.append(f"node {key}: size {size!r} has no {provider} mapping in sizing.yml")

        node_apps = node.get("applications") or []
        if not node_apps:
            warnings.append(f"node {key} has no applications (it will be provisioned idle)")
        for app in node_apps:
            if app not in apps:
                errors.append(f"node {key}: unknown application {app!r} (not in catalog)")
            elif app in placement:
                errors.append(f"{app} is placed on both {placement[app]} and {key}; "
                              "each application runs exactly once")
            else:
                placement[app] = key

    for app in topo.get("app_settings") or {}:
        if app not in placement:
            errors.append(f"app_settings.{app}: {app} is not placed")

    # dependencies
    for app, node in placement.items():
        for req in requirements(app, apps, settings):
            dep, port = req["app"], req["port"]
            if dep not in placement:
                errors.append(f"{app} (on {node}) requires {dep}, which is not placed")
            elif port not in (apps[dep].get("ports") or {}):
                errors.append(f"catalog: {app} requires {dep}:{port}, but {dep} has no such port")
            hba_type = req.get("hba_type")
            client_tls = req.get("client_tls")
            if hba_type is not None and (dep != "postgresql" or hba_type not in {"host", "hostssl"}):
                errors.append(f"catalog: {app} dependency {dep}:{port} has invalid hba_type {hba_type!r}; "
                              "use host|hostssl only for PostgreSQL dependencies")
            if client_tls is not None and (dep != "postgresql" or client_tls not in {"required", "disabled", "unknown"}):
                errors.append(f"catalog: {app} dependency {dep}:{port} has invalid client_tls {client_tls!r}; "
                              "use required|disabled|unknown only for PostgreSQL dependencies")

    # routes
    routes = topo.get("routes") or {}
    if routes and PROXY_APP not in placement:
        errors.append(f"routes are defined but {PROXY_APP} is not placed")
    for hostname, target in routes.items():
        if target not in placement:
            errors.append(f"route {hostname} -> {target}: {target} is not placed")
        elif apps[target].get("exposure") == "private":
            errors.append(f"route {hostname} -> {target}: {target} is private and may not be proxied")
        elif "http" not in (apps[target].get("ports") or {}):
            errors.append(f"route {hostname} -> {target}: {target} has no 'http' port")
    caddy_cfg = topo.get("caddy") or {}
    for app in caddy_cfg.get("hsts_apps") or []:
        if app not in set(routes.values()):
            errors.append(f"caddy.hsts_apps: {app} has no route")
    if caddy_cfg.get("tls") == "acme" and caddy_cfg.get("bind", "127.0.0.1") == "127.0.0.1":
        errors.append("caddy.tls is acme but caddy.bind is 127.0.0.1; certificates cannot be "
                      "validated on a loopback-only listener")

    # port collisions on one node
    for key, node in nodes.items():
        used: dict[int, str] = {}
        for app in node.get("applications") or []:
            for pname, port in (apps.get(app, {}).get("ports") or {}).items():
                if port in used:
                    errors.append(f"node {key}: port {port} wanted by {used[port]} and {app}:{pname}")
                used[port] = f"{app}:{pname}"

    # sysctl conflicts on one node
    for key, node in nodes.items():
        merged: dict[str, tuple] = {}
        for app in node.get("applications") or []:
            for k, v in (apps.get(app, {}).get("host_sysctls") or {}).items():
                if k in merged and merged[k][0] != v:
                    errors.append(f"node {key}: sysctl {k} = {merged[k][0]} ({merged[k][1]}) vs {v} ({app})")
                merged[k] = (v, app)

    # advisories (warnings only)
    for app, node in placement.items():
        adv = apps[app].get("advisories") or {}
        clash = [a for a in adv.get("avoid_colocation_with", []) if placement.get(a) == node]
        if clash:
            warnings.append(f"{app} shares {node} with {', '.join(clash)}: {adv.get('reason', '').strip()}")

    if errors:
        raise TopologyError("\n".join(f"  - {e}" for e in errors))
    return warnings


# --------------------------------------------------------------------------- resolution
def deploy_order(placement: dict, apps: dict, routes: dict, settings: dict | None = None) -> list[str]:
    """Topological order: dependencies before dependants; Caddy after its upstreams."""
    settings = settings or {}
    deps = {a: {r["app"] for r in requirements(a, apps, settings)} for a in placement}
    if PROXY_APP in deps:
        deps[PROXY_APP] |= set(routes.values())
    order, done, visiting = [], set(), set()

    def visit(a):
        if a in done:
            return
        if a in visiting:
            raise TopologyError(f"  - dependency cycle involving {a}")
        visiting.add(a)
        for d in sorted(deps[a]):
            visit(d)
        visiting.discard(a)
        done.add(a)
        order.append(a)

    for a in sorted(placement):
        visit(a)
    return order


def edges(placement: dict, apps: dict, routes: dict, settings: dict | None = None) -> list[dict]:
    """Every consumer -> provider connection, whether same-node or cross-node."""
    settings = settings or {}
    out = []
    for app in placement:
        for req in requirements(app, apps, settings):
            out.append({"consumer": app, "provider": req["app"], "port_name": req["port"],
                        "hba_type": req.get("hba_type", "host"),
                        "client_tls": req.get("client_tls", "unknown"),
                        "reason": f"{app} -> {req['app']}"})
    for hostname, target in routes.items():
        out.append({"consumer": PROXY_APP, "provider": target, "port_name": "http",
                    "reason": f"{PROXY_APP} route {hostname}"})
    for e in out:
        e["port"] = apps[e["provider"]]["ports"][e["port_name"]]
        e["from"] = placement[e["consumer"]]
        e["to"] = placement[e["provider"]]
    return out


def resolve(topo: dict, catalog: dict, sizing: dict) -> dict:
    apps = catalog["applications"]
    nodes = topo["nodes"]
    routes = topo.get("routes") or {}
    placement = {a: k for k, n in nodes.items() for a in n.get("applications") or []}
    ip = {k: n["private_ip"] for k, n in nodes.items()}
    settings = topo.get("app_settings") or {}
    all_edges = edges(placement, apps, routes, settings)
    order = deploy_order(placement, apps, routes, settings)
    docker_versions = (catalog.get("host_packages") or {}).get("docker") or {}

    host_vars: dict[str, dict] = {}
    for key, node in nodes.items():
        local_apps = node.get("applications") or []
        # endpoints this host's apps consume, seen from this host
        endpoints = {}
        for e in all_edges:
            if e["from"] != key:
                continue
            same = e["to"] == key
            endpoints.setdefault(e["provider"], {})[e["port_name"]] = {
                "host": "127.0.0.1" if same else ip[e["to"]],
                "port": e["port"],
                "same_node": same,
            }
        # bind addresses for each local listener: loopback always, private IP only
        # when some consumer lives on another node
        binds = {}
        for app in local_apps:
            for pname, port in (apps[app].get("ports") or {}).items():
                remote = any(e["to"] == key and e["provider"] == app and e["port_name"] == pname
                             and e["from"] != key for e in all_edges)
                # people (admins, developers) reach some apps directly over the private network
                remote = remote or bool(settings.get(app, {}).get("listen_private"))
                binds.setdefault(app, {})[pname] = {
                    "port": port,
                    "addresses": ["127.0.0.1", ip[key]] if remote else ["127.0.0.1"],
                }
        sysctls = {}
        for app in local_apps:
            sysctls.update(apps[app].get("host_sysctls") or {})

        hv = {
            "matrix_node": key,
            "matrix_private_ip": ip[key],
            "matrix_applications": local_apps,
            "matrix_endpoints": endpoints,
            "matrix_binds": binds,
            "matrix_sysctls": sysctls,
            "matrix_extend_root_lvm": bool(node.get("extend_root_lvm", False)),
            "matrix_docker_required": any(needs_docker(a, apps) for a in local_apps),
            "matrix_docker_versions": docker_versions,
            "matrix_versions": {a: apps[a].get("version") for a in local_apps},
            "matrix_images": {a: apps[a].get("image") for a in local_apps if apps[a].get("image")},
            "matrix_image_digests": {a: apps[a].get("observed_digest") for a in local_apps
                                      if apps[a].get("observed_digest")},
            "matrix_components": {a: apps[a].get("components", {}) for a in local_apps
                                  if apps[a].get("components")},
            "matrix_deployment": {a: apps[a].get("deployment", {}) for a in local_apps
                                  if apps[a].get("deployment")},
            "matrix_database_connections": {
                a: {
                    "database": apps[a]["database"]["name"],
                    "user": apps[a]["database"]["role"],
                    "host": endpoints["postgresql"]["postgres"]["host"],
                    "port": endpoints["postgresql"]["postgres"]["port"],
                    "same_node": endpoints["postgresql"]["postgres"]["same_node"],
                    "client_tls": next(
                        (r.get("client_tls", "unknown") for r in apps[a].get("requires", [])
                         if r.get("app") == "postgresql"), "unknown"
                    ),
                }
                for a in local_apps
                if apps[a].get("database") and "postgresql" in endpoints
            },
            "matrix_observed_auxiliary_services": node.get("observed_auxiliary_services", []),
            # environment-specific, non-secret settings (e.g. GitLab external_url)
            "matrix_settings": {a: (topo.get("app_settings") or {}).get(a, {}) for a in local_apps},
        }

        if "postgresql" in local_apps:
            dbs, hba = [], []
            for e in all_edges:
                if e["provider"] != "postgresql":
                    continue
                db = apps[e["consumer"]].get("database")
                if not db:
                    continue
                if db not in dbs:
                    dbs.append(db)
                src = "127.0.0.1" if e["from"] == key else ip[e["from"]]
                hba.append({"database": db["name"], "user": db["role"], "address": f"{src}/32",
                            "type": e.get("hba_type", "host"),
                            "client_tls": e.get("client_tls", "unknown"),
                            "method": "scram-sha-256", "comment": e["consumer"]})
            listen = binds["postgresql"]["postgres"]["addresses"]
            hv["postgresql"] = {
                "databases": sorted(dbs, key=lambda d: d["name"]),
                "hba": sorted(hba, key=lambda h: h["database"]),
                "listen_addresses": ["localhost" if a == "127.0.0.1" else a for a in listen],
                "settings": apps["postgresql"].get("settings") or {},
            }

        if PROXY_APP in local_apps:
            caddy_cfg = topo.get("caddy") or {}
            hsts_apps = set(caddy_cfg.get("hsts_apps") or [])
            hv["caddy"] = {
                "bind": caddy_cfg.get("bind", "127.0.0.1"),
                "tls": caddy_cfg.get("tls", "internal"),
                "auto_https": caddy_cfg.get("auto_https", "redirect"),
                "redirect_http": bool(caddy_cfg.get("redirect_http", False)),
                "hsts_max_age": int(caddy_cfg.get("hsts_max_age", 15552000)),
                "routes": [
                    {"hostname": h, "app": t,
                     "upstream": f"{endpoints[t]['http']['host']}:{endpoints[t]['http']['port']}",
                     "hsts": t in hsts_apps}
                    for h, t in sorted(routes.items())
                ],
            }
        host_vars[node["hostname"]] = hv

    # firewall flows: cross-node only, deduplicated per (from, to, port)
    flows: dict[tuple, dict] = {}
    for e in all_edges:
        if e["from"] == e["to"]:
            continue
        k = (e["from"], e["to"], e["port"])
        f = flows.setdefault(k, {"name": f"{e['from']}-to-{e['to']}-tcp{e['port']}",
                                 "from": e["from"], "to": e["to"], "port": e["port"],
                                 "protocol": "tcp", "reasons": []})
        f["reasons"].append(e["reason"])
    rules = [flows[k] for k in sorted(flows)]
    for i, r in enumerate(rules):
        r["priority"] = 1000 + i * 10  # stable ordering for Azure NSGs

    machine = (sizing.get("providers") or {}).get(topo["provider"], {})
    tofu_nodes = {
        k: {"hostname": n["hostname"], "private_ip": n["private_ip"],
            "machine_type": machine.get(n.get("size")), "size": n.get("size"),
            "disk_gb": n.get("disk_gb", 50), "applications": n.get("applications") or []}
        for k, n in nodes.items()
    }
    return {"placement": placement, "order": order, "host_vars": host_vars,
            "rules": rules, "tofu_nodes": tofu_nodes}


# --------------------------------------------------------------------------- outputs
def connection_vars(topo: dict) -> dict:
    access = topo.get("access") or {}
    net = topo.get("network") or {}
    if access.get("method") == "gcp_iap":
        return {
            "ansible_ssh_common_args": (
                "-o ProxyCommand=\"gcloud compute start-iap-tunnel %h 22 --listen-on-stdin "
                f"--zone={net.get('zone')} "
                f"--project={os.environ.get('MATRIX_GCP_PROJECT') or net.get('project')}\""
            )
        }
    if access.get("method") == "local":
        # Ansible runs on the node itself (single-node lab); no SSH involved.
        return {"ansible_connection": "local"}
    args = access.get("ssh_common_args")
    return {"ansible_ssh_common_args": args} if args else {}


def write_outputs(topo: dict, catalog: dict, res: dict, warnings: list[str], out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "host_vars").mkdir(parents=True)
    apps = catalog["applications"]
    nodes = topo["nodes"]
    gcp_iap = (topo.get("access") or {}).get("method") == "gcp_iap"

    hosts = {}
    for k, n in nodes.items():
        h = {"matrix_private_ip": n["private_ip"]}
        # IAP tunnels address the instance by name; everything else by private IP
        h["ansible_host"] = n["hostname"] if gcp_iap else n["private_ip"]
        hosts[n["hostname"]] = h
    children = {}
    for k, n in nodes.items():
        children[f"node_{k}"] = {"hosts": {n["hostname"]: None}}
    for app in apps:  # every catalog app gets a group, even if empty, so plays never break
        placed = res["placement"].get(app)
        children[app] = {"hosts": {nodes[placed]["hostname"]: None} if placed else {}}
    inventory = {"all": {
        "vars": {"matrix_environment": topo["environment"], "matrix_provider": topo["provider"],
                 "matrix_placement": {a: nodes[k]["hostname"] for a, k in res["placement"].items()},
                 **connection_vars(topo)},
        "hosts": hosts,
        "children": children,
    }}
    header = "# GENERATED by scripts/render_environment.py — do not edit; edit topology.yml\n"
    (out / "inventory.yml").write_text(header + yaml.safe_dump(inventory, sort_keys=False))
    for host, hv in res["host_vars"].items():
        (out / "host_vars" / f"{host}.yml").write_text(header + yaml.safe_dump(hv, sort_keys=False))

    tofu = {"environment": topo["environment"], "provider": topo["provider"],
            "managed_by_opentofu": bool(topo.get("managed_by_opentofu")),
            "network": topo.get("network") or {}, "nodes": res["tofu_nodes"],
            "rules": [{k: v for k, v in r.items() if k != "reasons"} for r in res["rules"]]}
    (out / "tofu-input.json").write_text(json.dumps(tofu, indent=2) + "\n")

    lines = [f"# Required node-to-node flows — {topo['provider']}/{topo['environment']}", "",
             "Generated from placement and dependencies. Same-node traffic uses 127.0.0.1 and "
             "needs no rule. Administrative access (IAP/SSM/Bastion) is not listed.", "",
             "| From | To | TCP | Why |", "|---|---|---|---|"]
    for r in res["rules"]:
        f, t = nodes[r["from"]], nodes[r["to"]]
        lines.append(f"| {r['from']} `{f['hostname']}` ({f['private_ip']}) | {r['to']} `{t['hostname']}` "
                     f"({t['private_ip']}) | {r['port']} | {'; '.join(r['reasons'])} |")
    if not res["rules"]:
        lines.append("| — | — | — | everything is co-located |")
    (out / "connectivity.md").write_text("\n".join(lines) + "\n")

    p = [f"# Placement — {topo['provider']}/{topo['environment']}", "",
         "| Node | Hostname | Private IP | Applications |", "|---|---|---|---|"]
    for k, n in nodes.items():
        p.append(f"| {k} | {n['hostname']} | {n['private_ip']} | {', '.join(n.get('applications') or []) or '—'} |")
    p += ["", "Deploy order: " + " → ".join(res["order"]), ""]
    unpinned = [a for a in res["placement"] if not apps[a].get("version")]
    if unpinned:
        warnings = warnings + [f"version not yet pinned for: {', '.join(sorted(unpinned))}"]
    p += ["## Warnings", ""] + ([f"- {w}" for w in warnings] or ["- none"])
    (out / "placement.md").write_text("\n".join(p) + "\n")


PUBLIC_DOMAIN_TOKEN = "{public_domain}"


def apply_public_domain(topo: dict) -> dict:
    """Replace {public_domain} in every key and string value of the topology.

    The committed topology uses a placeholder domain; set MATRIX_PUBLIC_DOMAIN to
    render with the real one. The real domain therefore only appears in the
    git-ignored generated/ output.
    """
    domain = os.environ.get("MATRIX_PUBLIC_DOMAIN") or topo.get("public_domain")

    def walk(v):
        if isinstance(v, str):
            if PUBLIC_DOMAIN_TOKEN in v:
                if not domain:
                    raise TopologyError(f"  - {v!r} uses {PUBLIC_DOMAIN_TOKEN} but no public_domain is set "
                                        "(topology public_domain or MATRIX_PUBLIC_DOMAIN)")
                return v.replace(PUBLIC_DOMAIN_TOKEN, domain)
            return v
        if isinstance(v, dict):
            return {walk(k): walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    out = walk(topo)
    if domain:
        out["public_domain"] = domain
    return out


def render(env_dir: Path, out_root: Path | None = None) -> tuple[dict, list[str], Path]:
    env_dir = env_dir.resolve()
    catalog = load_yaml(REPO / "applications" / "catalog.yml")
    sizing = load_yaml(REPO / "environments" / "sizing.yml")
    topo = apply_public_domain(load_yaml(env_dir / "topology.yml"))
    warnings = validate(topo, catalog, sizing)
    res = resolve(topo, catalog, sizing)
    out = (out_root or REPO / "generated") / topo["provider"] / topo["environment"]
    write_outputs(topo, catalog, res, warnings, out)
    return res, warnings, out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("environment", type=Path, help="e.g. environments/gcp/reference")
    ap.add_argument("--out", type=Path, default=None, help="output root (default: generated/)")
    args = ap.parse_args()
    try:
        res, warnings, out = render(args.environment, args.out)
    except TopologyError as exc:
        print(f"Topology is invalid:\n{exc}", file=sys.stderr)
        return 1
    print(f"Rendered {out.relative_to(REPO) if out.is_relative_to(REPO) else out}")
    print("Deploy order: " + " -> ".join(res["order"]))
    print(f"Cross-node flows: {len(res['rules'])}")
    for w in warnings:
        print(f"WARNING: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
