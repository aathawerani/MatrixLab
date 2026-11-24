"""Tests for the topology renderer.  Run:  python3 -m unittest discover -s tests -v"""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import render_environment as R  # noqa: E402

REFERENCE = yaml.safe_load((REPO / "environments/gcp/reference/topology.yml").read_text())


def render_topology(topo):
    """Render a topology dict into a temp dir; return (resolution, warnings, out_dir)."""
    tmp = Path(tempfile.mkdtemp())
    env = tmp / "env"
    env.mkdir()
    (env / "topology.yml").write_text(yaml.safe_dump(topo))
    return R.render(env, tmp / "out")



def render_role_template(relative_path, host_vars):
    """Render an Ansible Jinja template with the small filter set used here."""
    env = Environment(
        loader=FileSystemLoader(REPO / "configuration" / "roles"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["to_json"] = lambda value: json.dumps(value)
    env.filters["combine"] = lambda value, other: {**value, **other}
    env.filters["to_nice_json"] = lambda value: json.dumps(value, indent=2)
    env.filters["to_nice_yaml"] = lambda value, indent=2: yaml.safe_dump(
        value, sort_keys=False, indent=indent, default_flow_style=False
    ).rstrip()
    return env.get_template(relative_path).render(**host_vars)


def hv(res, hostname):
    return res["host_vars"][hostname]


def flows(res):
    return {(r["from"], r["to"], r["port"]) for r in res["rules"]}


class ReferenceReproducesObservedEnvironment(unittest.TestCase):
    """The GCP reference topology must reproduce the observed reference environment."""

    @classmethod
    def setUpClass(cls):
        cls.res, cls.warnings, _ = render_topology(REFERENCE)

    def test_known_firewall_paths(self):
        f = flows(self.res)
        self.assertIn(("vm01", "vm02", 5432), f)   # VM01 -> VM02 TCP5432
        self.assertIn(("vm02", "vm01", 443), f)    # VM02 -> VM01 TCP443
        self.assertNotIn(("vm03", "vm01", 443), f)  # not required

    def test_postgresql_listeners_and_gitlab_hba(self):
        pg = hv(self.res, "matrix-dev-vm-02")["postgresql"]
        self.assertEqual(pg["listen_addresses"], ["localhost", "10.20.0.3"])
        self.assertIn({"database": "gitlabhq_production", "user": "gitlab", "address": "10.20.0.2/32",
                       "type": "hostssl", "client_tls": "required",
                       "method": "scram-sha-256", "comment": "gitlab"}, pg["hba"])
        self.assertEqual(pg["settings"]["max_locks_per_transaction"], 256)
        self.assertEqual(pg["settings"]["max_connections"], 100)
        self.assertEqual(pg["settings"]["shared_buffers"], "128MB")
        self.assertEqual(pg["settings"]["password_encryption"], "scram-sha-256")

    def test_postgresql_hba_tls_matches_observed_application_policy(self):
        hba = hv(self.res, "matrix-dev-vm-02")["postgresql"]["hba"]
        by_db = {entry["database"]: entry for entry in hba}
        self.assertEqual(by_db["gitlabhq_production"]["type"], "hostssl")
        self.assertEqual(by_db["gitlabhq_production"]["client_tls"], "required")
        for db in ("openproject", "nextcloud", "mattermost", "sonarqube"):
            self.assertEqual(by_db[db]["type"], "host")
            self.assertEqual(by_db[db]["client_tls"], "unknown")

    def test_all_provider_examples_use_private_gitlab_https(self):
        for rel in (
            "environments/gcp/development/topology.yml",
            "environments/aws/development/topology.yml",
            "environments/azure/development/topology.yml",
        ):
            topo = yaml.safe_load((REPO / rel).read_text())
            self.assertTrue(topo["app_settings"]["gitlab"]["external_url"].startswith("https://"), rel)
            self.assertEqual(topo["app_settings"]["gitlab"]["tls"]["listener_port"], 443)
            self.assertEqual(topo["app_settings"]["gitlab_runner"]["tls_ca_path"],
                             "/etc/gitlab-runner/certs/gitlab.matrix-dev.internal.crt")
            rendered, _, _ = render_topology(topo)
            gitlab_host = topo["nodes"][rendered["placement"]["gitlab"]]["hostname"]
            runner_host = topo["nodes"][rendered["placement"]["gitlab_runner"]]["hostname"]
            self.assertIn("https", hv(rendered, gitlab_host)["matrix_binds"]["gitlab"])
            self.assertIn("gitlab_runner", hv(rendered, runner_host)["matrix_applications"])

    def test_gitlab_private_https_target_and_runner_dependency(self):
        gitlab = hv(self.res, "matrix-dev-vm-01")
        self.assertEqual(gitlab["matrix_settings"]["gitlab"]["external_url"],
                         "https://gitlab.matrix-dev.internal")
        self.assertEqual(gitlab["matrix_binds"]["gitlab"], {
            "https": {"port": 443, "addresses": ["127.0.0.1", "10.20.0.2"]}
        })
        self.assertTrue(gitlab["matrix_settings"]["gitlab"]["tls"]["runner_trust_required"])
        self.assertIn(("vm02", "vm01", 443), flows(self.res))

    def test_observed_compose_components_and_unverified_redis_are_preserved(self):
        vm03 = hv(self.res, "matrix-dev-vm-03")
        components = vm03["matrix_components"]
        self.assertEqual(components["openproject"]["hocuspocus"]["version"], "17.9.6")
        self.assertEqual(components["openproject"]["cache"]["image"], "memcached")
        self.assertEqual(components["nextcloud"]["redis"]["version"], "7.4-alpine")
        vm02 = hv(self.res, "matrix-dev-vm-02")
        self.assertEqual(vm02["matrix_observed_auxiliary_services"][0]["name"], "gitlab-redis")
        self.assertIn("unverified", vm02["matrix_observed_auxiliary_services"][0]["consumer_status"])
        self.assertEqual(vm02["matrix_applications"], ["postgresql", "gitlab_runner"])

    def test_vm03_apps_bind_loopback_only(self):
        binds = hv(self.res, "matrix-dev-vm-03")["matrix_binds"]
        for app in ("openproject", "nextcloud", "mattermost", "sonarqube", "caddy"):
            for b in binds[app].values():
                self.assertEqual(b["addresses"], ["127.0.0.1"], app)

    def test_caddy_public_routes_to_loopback_upstreams(self):
        routes = {r["hostname"]: r["upstream"] for r in hv(self.res, "matrix-dev-vm-03")["caddy"]["routes"]}
        self.assertEqual(routes["projects.alitech.example"], "127.0.0.1:8080")
        self.assertEqual(routes["cloud.alitech.example"], "127.0.0.1:8081")
        self.assertEqual(routes["chat.alitech.example"], "127.0.0.1:8065")
        caddy = hv(self.res, "matrix-dev-vm-03")["caddy"]
        self.assertEqual(caddy["auto_https"], "disable_redirects")
        self.assertEqual(caddy["bind"], "10.20.0.4")
        self.assertEqual(caddy["tls"], "acme")
        self.assertTrue(caddy["redirect_http"])
        hsts = {r["app"]: r["hsts"] for r in caddy["routes"]}
        self.assertEqual(hsts, {"openproject": False, "nextcloud": True, "mattermost": True})

    def test_sonarqube_advisory_is_a_warning_not_an_error(self):
        self.assertTrue(any(w.startswith("sonarqube shares vm03") for w in self.warnings))


class ConfigurationPreviews(unittest.TestCase):
    """Generated configuration fragments must reflect the observed reference state."""

    @classmethod
    def setUpClass(cls):
        cls.res, _, _ = render_topology(REFERENCE)
        cls.vm01 = hv(cls.res, "matrix-dev-vm-01")
        cls.vm02 = hv(cls.res, "matrix-dev-vm-02")
        cls.vm03 = hv(cls.res, "matrix-dev-vm-03")

    def test_postgresql_hba_template_uses_each_observed_rule_type(self):
        rendered = render_role_template(
            "postgresql/templates/pg_hba.matrix.conf.j2", self.vm02
        )
        lines = [line for line in rendered.splitlines() if line and not line.startswith("#")]
        self.assertTrue(any(line.startswith("hostssl") and "gitlabhq_production" in line for line in lines))
        for database in ("openproject", "nextcloud", "mattermost", "sonarqube"):
            self.assertTrue(any(line.startswith("host    ") and database in line for line in lines), database)

    def test_postgresql_template_renders_observed_settings(self):
        rendered = render_role_template("postgresql/templates/matrix.conf.j2", self.vm02)
        for expected in (
            "listen_addresses = 'localhost,10.20.0.3'",
            "ssl = on",
            "ssl_min_protocol_version = 'TLSv1.2'",
            "max_connections = 100",
            "max_locks_per_transaction = 256",
            "shared_buffers = '128MB'",
            "password_encryption = 'scram-sha-256'",
        ):
            self.assertIn(expected, rendered)

    def test_gitlab_template_renders_private_https_without_embedding_secret(self):
        rendered = render_role_template("gitlab/templates/gitlab.matrix.rb.j2", self.vm01)
        self.assertIn('external_url "https://gitlab.matrix-dev.internal"', rendered)
        self.assertIn("nginx['listen_port'] = 443", rendered)
        self.assertIn("nginx['listen_https'] = true", rendered)
        self.assertIn("127.0.0.1", rendered)
        self.assertIn("10.20.0.2", rendered)
        self.assertNotIn("nginx['listen_port'] = 80", rendered)
        self.assertIn("/etc/gitlab/ssl/gitlab.matrix-dev.internal.crt", rendered)
        self.assertIn("/etc/gitlab/ssl/gitlab.matrix-dev.internal.key", rendered)
        self.assertIn("matrix_postgresql_role_passwords.gitlab", rendered)
        self.assertNotIn("db_password'] = File.read", rendered)
        self.assertNotIn("password_here", rendered)

    def test_runner_preview_uses_host_certificate_path_and_preserves_registration(self):
        rendered = render_role_template("gitlab_runner/templates/tls-trust-manifest.yml.j2", self.vm02)
        self.assertIn("/etc/gitlab-runner/certs/gitlab.matrix-dev.internal.crt", rendered)
        self.assertIn("https://gitlab.matrix-dev.internal", rendered)
        self.assertIn("registration:", rendered)
        self.assertIn("not performed", rendered)
        self.assertIn("config_toml", rendered)

    def test_caddy_template_renders_public_sites_with_redirects_and_acme(self):
        rendered = render_role_template("caddy/templates/Caddyfile.j2", self.vm03)
        self.assertIn("auto_https disable_redirects", rendered)
        self.assertEqual(rendered.count("bind 10.20.0.4"), 6)
        self.assertNotIn("bind 127.0.0.1", rendered)
        self.assertNotIn("tls ", rendered)
        self.assertEqual(rendered.count("redir https://{host}{uri} permanent"), 3)
        for host, upstream in (("projects", "8080"), ("cloud", "8081"), ("chat", "8065")):
            self.assertIn(f"http://{host}.alitech.example {{", rendered)
            self.assertIn(f"https://{host}.alitech.example {{", rendered)
            self.assertIn(f"reverse_proxy 127.0.0.1:{upstream}", rendered)
        self.assertEqual(rendered.count('header Strict-Transport-Security "max-age=15552000"'), 2)

    def test_openproject_manifest_contains_observed_services_and_external_db(self):
        rendered = render_role_template("openproject/templates/deployment-manifest.yml.j2", self.vm03)
        doc = yaml.safe_load(rendered)
        self.assertEqual(doc["database"]["host"], "10.20.0.3")
        self.assertEqual(doc["database"]["database"], "openproject")
        services = doc["deployment"]["observed_services"]
        self.assertEqual(services["hocuspocus"]["image"], "openproject/hocuspocus:17.9.6")
        self.assertEqual(services["proxy"]["host_bind"], "127.0.0.1:8080")
        self.assertIn("not a complete runnable Compose file", doc["deployment"]["preview_note"])

    def test_nextcloud_manifest_contains_observed_storage_and_external_db(self):
        rendered = render_role_template("nextcloud/templates/deployment-manifest.yml.j2", self.vm03)
        doc = yaml.safe_load(rendered)
        self.assertEqual(doc["database"]["host"], "10.20.0.3")
        self.assertEqual(doc["database"]["database"], "nextcloud")
        services = doc["deployment"]["observed_services"]
        self.assertIn("/srv/alitech/services/nextcloud/data:/var/www/data", services["app"]["bind_mounts"])
        self.assertEqual(services["redis"]["image"], "redis:7.4-alpine")

    def test_openproject_compose_uses_external_postgres_and_separate_hocuspocus_env(self):
        context = dict(self.vm03)
        context.update({
            "matrix_openproject_runtime_env": {
                "DATABASE_URL": "postgres://openproject:secret@10.20.0.3:5432/openproject",
                "SECRET_KEY_BASE": "secret-key",
                "OPENPROJECT_HOST__NAME": "projects.example.invalid",
                "OPENPROJECT_HTTPS": "true",
                "OPENPROJECT_CACHE__MEMCACHE__SERVER": "cache:11211",
                "OPENPROJECT_RAILS__CACHE__STORE": "memcache",
                "OPENPROJECT_COLLABORATIVE__EDITING__HOCUSPOCUS__URL": "ws://hocuspocus:4000",
                "OPENPROJECT_COLLABORATIVE__EDITING__HOCUSPOCUS__SECRET": "hocus-secret",
            },
            "matrix_openproject_hocuspocus_env": {"HOCUSPOCUS_SECRET": "hocus-secret"},
            "matrix_openproject_proxy_image": "matrix/openproject-proxy:managed",
            "matrix_openproject_proxy_base_image": "caddy@sha256:" + "a" * 64,
            "matrix_openproject_proxy_app_host": "web",
        })
        rendered = render_role_template("openproject/templates/compose.yaml.j2", context)
        doc = yaml.safe_load(rendered)
        self.assertEqual(set(doc["services"]), {"web", "worker", "cron", "seeder", "hocuspocus", "proxy", "cache"})
        self.assertEqual(doc["services"]["proxy"]["ports"], ["127.0.0.1:8080:80"])
        self.assertNotIn("ports", doc["services"]["hocuspocus"])
        self.assertNotIn("DATABASE_URL", doc["services"]["hocuspocus"]["environment"])
        self.assertEqual(doc["networks"]["backend"], {})
        self.assertIn("@sha256:", doc["services"]["web"]["image"])
        self.assertIn("@sha256:", doc["services"]["cache"]["image"])
        self.assertEqual(doc["services"]["proxy"]["build"]["args"]["APP_HOST"], "web")
        self.assertEqual(doc["services"]["proxy"]["depends_on"], ["web", "hocuspocus"])
        self.assertIn("hocuspocus:1234", (REPO / "configuration/roles/openproject/files/proxy/Caddyfile.template").read_text())
        self.assertNotIn("db", doc["services"])

    def test_nextcloud_compose_uses_derived_database_endpoint_and_loopback_bind(self):
        context = dict(self.vm03)
        context.update({
            "matrix_nextcloud_image": "nextcloud:35.0.2-apache",
            "matrix_nextcloud_redis_image": "redis:7.4-alpine",
            "matrix_nextcloud_html_dir": "/srv/alitech/services/nextcloud/html",
            "matrix_nextcloud_data_dir": "/srv/alitech/services/nextcloud/data",
            "matrix_nextcloud_runtime_env": {
                "NEXTCLOUD_ADMIN_USER": "admin",
                "NEXTCLOUD_ADMIN_PASSWORD": "secret",
                "NEXTCLOUD_TRUSTED_DOMAINS": "cloud.example.invalid",
                "POSTGRES_PASSWORD": "db-secret",
            },
        })
        rendered = render_role_template("nextcloud/templates/compose.yaml.j2", context)
        doc = yaml.safe_load(rendered)
        app = doc["services"]["app"]
        self.assertEqual(app["ports"], ["127.0.0.1:8081:80"])
        self.assertEqual(app["environment"]["POSTGRES_HOST"], "10.20.0.3:5432")
        self.assertNotIn("POSTGRES_PORT", app["environment"])
        self.assertEqual(app["environment"]["POSTGRES_DB"], "nextcloud")
        self.assertEqual(app["environment"]["REDIS_HOST"], "redis")
        self.assertNotIn("ports", doc["services"]["redis"])
        self.assertEqual(app["volumes"], [
            "/srv/alitech/services/nextcloud/html:/var/www/html",
            "/srv/alitech/services/nextcloud/data:/var/www/data",
        ])

    def test_mattermost_fragment_is_valid_json_and_omits_datasource(self):
        rendered = render_role_template("mattermost/templates/config.matrix.fragment.json.j2", self.vm03)
        doc = json.loads(rendered)
        self.assertEqual(doc["ServiceSettings"]["SiteURL"], "https://chat.alitech.example")
        self.assertEqual(doc["ServiceSettings"]["ListenAddress"], "127.0.0.1:8065")
        self.assertEqual(doc["SqlSettings"]["DriverName"], "postgres")
        self.assertEqual(doc["SqlSettings"]["MaxOpenConns"], 100)
        self.assertNotIn("DataSource", doc["SqlSettings"])
        self.assertEqual(doc["FileSettings"]["Directory"], "/opt/mattermost/data/")

    def test_postgresql_hba_include_fragment_points_to_generated_rules(self):
        rendered = render_role_template("postgresql/templates/pg_hba.include.conf.j2", self.vm02)
        self.assertIn("include_if_exists '/etc/postgresql/17/main/pg_hba.matrix.conf'", rendered)
        self.assertIn("not automatically activated", rendered)

    def test_compose_roles_are_fail_closed_and_secrets_are_not_logged(self):
        for role in ("openproject", "nextcloud"):
            tasks = yaml.safe_load((REPO / f"configuration/roles/{role}/tasks/main.yml").read_text())
            self.assertIn("matrix_allow_changes | default(false) | bool", str(tasks[0]))
            secret_tasks = [task for task in tasks if task.get("no_log") is True]
            self.assertTrue(secret_tasks, role)
        openproject = (REPO / "configuration/roles/openproject/tasks/main.yml").read_text()
        self.assertIn("matrix_openproject_proxy_base_image", openproject)
        self.assertIn("matrix_openproject_hocuspocus_env", openproject)
        self.assertTrue((REPO / "configuration/roles/openproject/files/proxy/Dockerfile").is_file())
        self.assertTrue((REPO / "configuration/roles/openproject/files/proxy/Caddyfile.template").is_file())
        self.assertNotIn("autoheal", (REPO / "configuration/roles/openproject/templates/compose.yaml.j2").read_text())

    def test_preview_playbook_covers_all_observed_application_roles(self):
        preview = yaml.safe_load((REPO / "configuration/playbooks/preview.yml").read_text())
        task = preview[0]["tasks"][1]
        self.assertIn("gitlab_runner", task["loop"])
        self.assertIn("openproject", task["loop"])
        self.assertIn("nextcloud", task["loop"])
        self.assertIn("mattermost", task["loop"])

    def test_sonarqube_compose_does_not_assume_unknown_client_tls(self):
        rendered = render_role_template("sonarqube/templates/compose.yaml.j2", self.vm03)
        doc = yaml.safe_load(rendered)
        url = doc["services"]["sonarqube"]["environment"]["SONAR_JDBC_URL"]
        self.assertEqual(url, "jdbc:postgresql://10.20.0.3:5432/sonarqube")
        self.assertIn("SONAR_JDBC_PASSWORD", doc["services"]["sonarqube"]["environment"])
        self.assertNotIn("env_file", doc["services"]["sonarqube"])
        self.assertIn("127.0.0.1:9000:9000", doc["services"]["sonarqube"]["ports"])


class MovingApplications(unittest.TestCase):

    def test_move_sonarqube_to_fourth_vm(self):
        topo = copy.deepcopy(REFERENCE)
        topo["nodes"]["vm03"]["applications"].remove("sonarqube")
        topo["nodes"]["vm04"] = {"hostname": "matrix-dev-vm-04", "private_ip": "10.20.0.5",
                                 "applications": ["sonarqube"]}
        res, warnings, _ = render_topology(topo)
        before, _, _ = render_topology(REFERENCE)

        self.assertEqual(res["placement"]["sonarqube"], "vm04")
        self.assertIn(("vm04", "vm02", 5432), flows(res))
        self.assertEqual(flows(res) - flows(before), {("vm04", "vm02", 5432)})
        hba = hv(res, "matrix-dev-vm-02")["postgresql"]["hba"]
        self.assertIn("10.20.0.5/32", [h["address"] for h in hba if h["database"] == "sonarqube"])
        self.assertEqual(hv(res, "matrix-dev-vm-04")["matrix_sysctls"]["vm.max_map_count"], 524288)
        self.assertEqual(hv(res, "matrix-dev-vm-03")["matrix_sysctls"], {})
        self.assertFalse(any(w.startswith("sonarqube shares") for w in warnings))
        # GitLab's host is unaffected
        self.assertEqual(hv(res, "matrix-dev-vm-01"), hv(before, "matrix-dev-vm-01"))

    def test_colocated_database_uses_loopback_and_needs_no_flow(self):
        topo = copy.deepcopy(REFERENCE)
        topo["nodes"]["vm01"]["applications"] = ["gitlab", "postgresql"]
        topo["nodes"]["vm02"]["applications"] = ["gitlab_runner"]
        res, _, _ = render_topology(topo)
        ep = hv(res, "matrix-dev-vm-01")["matrix_endpoints"]["postgresql"]["postgres"]
        self.assertEqual((ep["host"], ep["same_node"]), ("127.0.0.1", True))
        hba = {h["database"]: h["address"] for h in hv(res, "matrix-dev-vm-01")["postgresql"]["hba"]}
        self.assertEqual(hba["gitlabhq_production"], "127.0.0.1/32")
        self.assertNotIn(("vm01", "vm02", 5432), flows(res))

    def test_remote_upstreams_bind_private_ip(self):
        topo = copy.deepcopy(REFERENCE)
        topo["nodes"]["vm03"]["applications"] = ["openproject", "nextcloud", "mattermost", "sonarqube"]
        topo["nodes"]["vm04"] = {"hostname": "edge", "private_ip": "10.20.0.5", "applications": ["caddy"]}
        res, _, _ = render_topology(topo)
        self.assertEqual(hv(res, "matrix-dev-vm-03")["matrix_binds"]["nextcloud"]["http"]["addresses"],
                         ["127.0.0.1", "10.20.0.4"])
        self.assertEqual(hv(res, "matrix-dev-vm-03")["matrix_binds"]["sonarqube"]["http"]["addresses"],
                         ["127.0.0.1"])  # still private: nobody proxies it
        self.assertIn(("vm04", "vm03", 8081), flows(res))


class Rejections(unittest.TestCase):

    def assertRejected(self, topo, fragment):
        with self.assertRaises(R.TopologyError) as ctx:
            render_topology(topo)
        self.assertIn(fragment, str(ctx.exception))

    def test_app_placed_twice(self):
        t = copy.deepcopy(REFERENCE)
        t["nodes"]["vm01"]["applications"].append("mattermost")
        self.assertRejected(t, "mattermost is placed on both")

    def test_unknown_app(self):
        t = copy.deepcopy(REFERENCE)
        t["nodes"]["vm01"]["applications"].append("zammad")
        self.assertRejected(t, "unknown application 'zammad'")

    def test_missing_dependency(self):
        t = copy.deepcopy(REFERENCE)
        t["nodes"]["vm02"]["applications"] = ["gitlab_runner"]
        self.assertRejected(t, "requires postgresql, which is not placed")

    def test_private_app_cannot_get_a_route(self):
        t = copy.deepcopy(REFERENCE)
        t["routes"]["sonar.alitech.example"] = "sonarqube"
        self.assertRejected(t, "sonarqube is private and may not be proxied")

    def test_port_collision(self):
        t = copy.deepcopy(REFERENCE)
        t["nodes"]["vm01"]["applications"] = ["gitlab", "caddy"]
        t["nodes"]["vm03"]["applications"].remove("caddy")
        self.assertRejected(t, "port 443 wanted by")

    def test_ip_outside_cidr_and_reserved(self):
        t = copy.deepcopy(REFERENCE)
        t["network"]["cidr"] = "10.20.0.0/24"
        t["nodes"]["vm01"]["private_ip"] = "10.99.0.2"
        t["nodes"]["vm02"]["private_ip"] = "10.20.0.1"
        with self.assertRaises(R.TopologyError) as ctx:
            render_topology(t)
        self.assertIn("outside 10.20.0.0/24", str(ctx.exception))
        self.assertIn("reserved by gcp", str(ctx.exception))

    def test_managed_env_needs_known_size(self):
        t = yaml.safe_load((REPO / "environments/gcp/development/topology.yml").read_text())
        t["nodes"]["vm04"]["size"] = "huge"
        self.assertRejected(t, "size 'huge' has no gcp mapping")

    def test_invalid_postgresql_hba_type_is_rejected(self):
        t = copy.deepcopy(REFERENCE)
        catalog = yaml.safe_load((REPO / "applications/catalog.yml").read_text())
        catalog["applications"]["gitlab"]["requires"][0]["hba_type"] = "hostnossl"
        with self.assertRaises(R.TopologyError) as ctx:
            R.validate(t, catalog, yaml.safe_load((REPO / "environments/sizing.yml").read_text()))
        self.assertIn("invalid hba_type", str(ctx.exception))


class PlaybookOrder(unittest.TestCase):
    def test_site_play_order_respects_dependencies(self):
        plays = yaml.safe_load((REPO / "configuration/playbooks/site.yml").read_text())
        hosts = [p["hosts"] for p in plays if p["hosts"] != "all"]
        catalog = yaml.safe_load((REPO / "applications/catalog.yml").read_text())["applications"]
        order = R.deploy_order({a: "x" for a in catalog}, catalog,
                               {"r": a for a in catalog if catalog[a].get("exposure") != "private"
                                and "http" in (catalog[a].get("ports") or {})})
        for app in hosts:
            for dep in [r["app"] for r in catalog[app].get("requires") or []]:
                self.assertLess(hosts.index(dep), hosts.index(app), f"{dep} must run before {app}")
        self.assertEqual(hosts[-1], "caddy")
        self.assertEqual(set(hosts), set(order))


if __name__ == "__main__":
    unittest.main()


class Stage3RoleStructure(unittest.TestCase):
    def test_postgresql_role_has_explicit_approval_gate(self):
        import yaml
        tasks = yaml.safe_load((REPO / "configuration/roles/postgresql/tasks/main.yml").read_text())
        self.assertIn("matrix_allow_changes | default(false) | bool", str(tasks[0]))

    def test_postgresql_role_installs_pinned_package_and_active_hba_include(self):
        import yaml
        tasks = yaml.safe_load((REPO / "configuration/roles/postgresql/tasks/main.yml").read_text())
        serialized = str(tasks)
        self.assertIn("matrix_postgresql_package_version", serialized)
        self.assertIn("include_if_exists", serialized)
        self.assertIn("pg_hba.matrix.conf", serialized)

    def test_database_creation_is_opt_in_and_passwords_are_hidden(self):
        import yaml
        tasks = yaml.safe_load((REPO / "configuration/roles/postgresql/tasks/main.yml").read_text())
        serialized = str(tasks)
        self.assertIn("matrix_manage_postgresql_databases | bool", serialized)
        self.assertIn("no_log", serialized)
        self.assertIn("matrix_postgresql_role_passwords", serialized)

    def test_common_role_does_not_upgrade_all_packages(self):
        import yaml
        tasks = yaml.safe_load((REPO / "configuration/roles/common/tasks/main.yml").read_text())
        serialized = str(tasks)
        self.assertNotIn("upgrade:", serialized)
        self.assertIn("update_cache", serialized)

    def test_site_playbook_has_global_approval_gate(self):
        import yaml
        plays = yaml.safe_load((REPO / "configuration/playbooks/site.yml").read_text())
        self.assertIn("pre_tasks", plays[0])
        self.assertIn("matrix_allow_changes", str(plays[0]["pre_tasks"]))


class Stage3PostgreSQLDefaults(unittest.TestCase):
    def test_cluster_unit_uses_debian_systemd_name(self):
        import yaml
        defaults = yaml.safe_load((REPO / "configuration/roles/postgresql/defaults/main.yml").read_text())
        self.assertEqual(defaults["matrix_postgresql_cluster"], "17-main")


class Stage3GitLabRoles(unittest.TestCase):
    def test_gitlab_role_requires_explicit_approval_and_private_secrets(self):
        tasks = yaml.safe_load((REPO / "configuration/roles/gitlab/tasks/main.yml").read_text())
        serialized = str(tasks)
        self.assertIn("matrix_allow_changes", serialized)
        self.assertIn("matrix_gitlab_db_password", serialized)
        self.assertIn("matrix_gitlab_tls_certificate", serialized)
        self.assertIn("matrix_gitlab_tls_private_key", serialized)
        self.assertIn("matrix_gitlab_tls_ca_certificate", serialized)
        self.assertIn("no_log", serialized)

    def test_gitlab_preserves_main_config_and_uses_managed_fragment(self):
        tasks = yaml.safe_load((REPO / "configuration/roles/gitlab/tasks/main.yml").read_text())
        serialized = str(tasks)
        self.assertIn("from_file", serialized)
        self.assertIn("gitlab.matrix.rb", serialized)
        self.assertIn("gitlab-ctl reconfigure", str(
            yaml.safe_load((REPO / "configuration/roles/gitlab/handlers/main.yml").read_text())
        ))

    def test_gitlab_runner_installs_ca_without_registering_runner(self):
        tasks = yaml.safe_load((REPO / "configuration/roles/gitlab_runner/tasks/main.yml").read_text())
        serialized = str(tasks)
        self.assertIn("matrix_gitlab_tls_ca_certificate", serialized)
        self.assertIn("matrix_gitlab_runner_ca_dir", serialized)
        source = (REPO / "configuration/roles/gitlab_runner/tasks/main.yml").read_text()
        self.assertIn("gitlab-runner register", source)
        self.assertNotIn("ansible.builtin.command: gitlab-runner register", serialized)

    def test_gitlab_runner_does_not_replace_runner_config(self):
        tasks = yaml.safe_load((REPO / "configuration/roles/gitlab_runner/tasks/main.yml").read_text())
        serialized = str(tasks)
        self.assertIn("matrix_gitlab_runner_config", serialized)
        self.assertNotIn("ansible.builtin.template", serialized)
        # The only copy task installs the CA certificate, not config.toml.
        for task in tasks:
            copy_args = task.get("ansible.builtin.copy", {})
            self.assertNotEqual(copy_args.get("dest"), "/etc/gitlab-runner/config.toml")

    def test_gitlab_templates_keep_database_password_out_of_preview(self):
        resolution, _, _ = render_topology(REFERENCE)
        rendered = render_role_template(
            "gitlab/templates/gitlab.matrix.rb.j2",
            hv(resolution, "matrix-dev-vm-01")
        )
        self.assertIn("matrix_postgresql_role_passwords.gitlab", rendered)
        self.assertNotIn("db_password'] =", rendered)


class Stage3GitLabSecretRendering(unittest.TestCase):
    def test_gitlab_secret_is_rendered_only_when_explicitly_supplied(self):
        resolution, _, _ = render_topology(REFERENCE)
        host = copy.deepcopy(hv(resolution, "matrix-dev-vm-01"))
        host["matrix_gitlab_db_password"] = 'dummy-quote-"-value'
        rendered = render_role_template("gitlab/templates/gitlab.matrix.rb.j2", host)
        self.assertIn('gitlab_rails[\'db_password\'] = "dummy-quote-\\\"-value"', rendered)

    def test_runner_settings_are_available_on_runner_host(self):
        resolution, _, _ = render_topology(REFERENCE)
        host = hv(resolution, "matrix-dev-vm-02")
        self.assertEqual(host["matrix_settings"]["gitlab_runner"]["hostname"], "gitlab.matrix-dev.internal")
        self.assertEqual(host["matrix_settings"]["gitlab_runner"]["url"], "https://gitlab.matrix-dev.internal")


class Stage3SharedDatabaseSecret(unittest.TestCase):
    def test_gitlab_database_password_uses_postgresql_role_password_mapping(self):
        defaults = yaml.safe_load((REPO / "configuration/roles/gitlab/defaults/main.yml").read_text())
        self.assertIn("matrix_postgresql_role_passwords.gitlab", defaults["matrix_gitlab_db_password"])
        shared = yaml.safe_load((REPO / "configuration/playbooks/group_vars/all.yml").read_text())
        self.assertEqual(shared["matrix_postgresql_role_passwords"], {})
        self.assertFalse(shared["matrix_manage_postgresql_databases"])


class Stage3SonarQubeRole(unittest.TestCase):
    def test_sonarqube_role_is_guarded_and_pins_observed_image(self):
        tasks = yaml.safe_load((REPO / "configuration/roles/sonarqube/tasks/main.yml").read_text())
        source = (REPO / "configuration/roles/sonarqube/tasks/main.yml").read_text()
        self.assertIn("matrix_allow_changes", str(tasks))
        self.assertIn("matrix_image_digests.sonarqube", source)
        self.assertIn("no_log", source)

    def test_sonarqube_compose_uses_external_database_and_four_volumes(self):
        template = (REPO / "configuration/roles/sonarqube/templates/compose.yaml.j2").read_text()
        self.assertIn("matrix_database_connections.sonarqube.host", template)
        self.assertIn("sonarqube_data:/opt/sonarqube/data", template)
        self.assertIn("sonarqube_extensions:/opt/sonarqube/extensions", template)
        self.assertIn("sonarqube_logs:/opt/sonarqube/logs", template)
        self.assertIn("sonarqube_temp:/opt/sonarqube/temp", template)
        self.assertNotIn("ports: [", template)

    def test_sonarqube_password_is_not_in_preview_manifest(self):
        manifest = (REPO / "configuration/roles/sonarqube/templates/deployment-manifest.yml.j2").read_text()
        self.assertNotIn("SONAR_JDBC_PASSWORD", manifest)


class Stage3CaddyRole(unittest.TestCase):
    def test_caddy_role_is_approval_gated_and_package_pinned(self):
        tasks = (REPO / "configuration/roles/caddy/tasks/main.yml").read_text()
        defaults = (REPO / "configuration/roles/caddy/defaults/main.yml").read_text()
        self.assertIn("matrix_allow_changes", tasks)
        self.assertIn("matrix_caddy_package_version", defaults)
        self.assertIn("dl.cloudsmith.io/public/caddy/stable", defaults)

    def test_caddy_template_uses_generated_routes_and_internal_tls(self):
        template = (REPO / "configuration/roles/caddy/templates/Caddyfile.j2").read_text()
        self.assertIn("caddy.routes", template)
        self.assertIn("tls internal", template)
        self.assertIn("caddy.bind", template)
        self.assertIn("reverse_proxy", template)


class Stage3MattermostRole(unittest.TestCase):
    def test_mattermost_role_is_guarded_and_installs_exact_package(self):
        tasks = (REPO / "configuration/roles/mattermost/tasks/main.yml").read_text()
        self.assertIn("matrix_allow_changes", tasks)
        self.assertIn("matrix_versions.mattermost }}-0", tasks)
        self.assertIn("policy_rc_d: 101", tasks)

    def test_mattermost_requires_complete_protected_config(self):
        tasks = (REPO / "configuration/roles/mattermost/tasks/main.yml").read_text()
        self.assertIn("matrix_mattermost_config.SqlSettings.DataSource", tasks)
        self.assertIn("no_log: true", tasks)
        self.assertIn("0600", tasks)

    def test_mattermost_preview_manifest_contains_no_database_secret(self):
        manifest = (REPO / "configuration/roles/mattermost/templates/deployment-manifest.yml.j2").read_text()
        self.assertNotIn("DataSource", manifest)
        self.assertNotIn("password", manifest.lower())


class PublicDomainPlaceholder(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("MATRIX_PUBLIC_DOMAIN", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["MATRIX_PUBLIC_DOMAIN"] = self._saved
        else:
            os.environ.pop("MATRIX_PUBLIC_DOMAIN", None)

    def test_committed_topology_uses_placeholder_domain_only(self):
        source = (REPO / "environments/gcp/reference/topology.yml").read_text()
        self.assertIn("public_domain: alitech.example", source)
        self.assertIn("projects.{public_domain}", source)

    def test_environment_variable_overrides_domain(self):
        os.environ["MATRIX_PUBLIC_DOMAIN"] = "example.org"
        res, _, _ = render_topology(copy.deepcopy(REFERENCE))
        hosts = {r["hostname"] for r in hv(res, "matrix-dev-vm-03")["caddy"]["routes"]}
        self.assertEqual(hosts, {"projects.example.org", "cloud.example.org", "chat.example.org"})
        self.assertEqual(hv(res, "matrix-dev-vm-03")["matrix_settings"]["mattermost"]["site_url"],
                         "https://chat.example.org")

    def test_placeholder_without_domain_is_rejected(self):
        topo = copy.deepcopy(REFERENCE)
        topo.pop("public_domain")
        with self.assertRaises(R.TopologyError):
            render_topology(topo)

    def test_acme_on_loopback_is_rejected(self):
        topo = copy.deepcopy(REFERENCE)
        topo["caddy"]["bind"] = "127.0.0.1"
        with self.assertRaises(R.TopologyError) as ctx:
            render_topology(topo)
        self.assertIn("loopback-only", str(ctx.exception))

    def test_hsts_app_without_route_is_rejected(self):
        topo = copy.deepcopy(REFERENCE)
        topo["caddy"]["hsts_apps"].append("sonarqube")
        with self.assertRaises(R.TopologyError) as ctx:
            render_topology(topo)
        self.assertIn("caddy.hsts_apps: sonarqube has no route", str(ctx.exception))


class GitLabPrivateHttpsRecord(unittest.TestCase):
    def test_runner_registration_values_are_recorded(self):
        reg = REFERENCE["app_settings"]["gitlab_runner"]["registration"]
        self.assertEqual(reg["executor"], "docker")
        self.assertEqual(reg["concurrent"], 1)
        self.assertEqual((reg["docker_cpus"], reg["docker_memory"]), ("2", "4g"))

    def test_ca_script_is_name_constrained_and_never_prints_the_key(self):
        script = (REPO / "scripts/private-ca/issue-gitlab-certificate.sh").read_text()
        self.assertIn("nameConstraints = critical", script)
        self.assertIn("-days 825", script)
        self.assertNotRegex(script, r"cat [^|\n]*ca\.key")

    def test_runbook_warns_against_printing_runner_token(self):
        runbook = (REPO / "documentation/runbooks/gitlab-private-https.md").read_text()
        self.assertIn("Do not run `gitlab-runner list`", runbook)


class GcpProjectOverride(unittest.TestCase):
    def test_project_placeholder_can_be_overridden_at_render_time(self):
        saved = os.environ.get("MATRIX_GCP_PROJECT")
        os.environ["MATRIX_GCP_PROJECT"] = "real-project-123"
        try:
            args = R.connection_vars(REFERENCE)["ansible_ssh_common_args"]
        finally:
            if saved is None:
                os.environ.pop("MATRIX_GCP_PROJECT", None)
            else:
                os.environ["MATRIX_GCP_PROJECT"] = saved
        self.assertIn("--project=real-project-123", args)
        self.assertNotIn("example-gcp-project", args)


class PublicRepositoryHygiene(unittest.TestCase):
    """This repository is public: no real project IDs, domains, schedules or internal notes."""

    FORBIDDEN = ["matrix-agent-oracle", "aimarks", "PKT", "INFRASTRUCTURE_HANDOFF", "handoff"]

    def test_no_internal_identifiers_in_tracked_text(self):
        hits = []
        for path in REPO.rglob("*"):
            if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if path.suffix not in {".md", ".yml", ".yaml", ".py", ".tf", ".j2", ".sh", ".zsh", ".rb", ".toml", ".cfg", ""}:
                continue
            if path.name == "test_render.py":
                continue
            text = path.read_text(errors="ignore").lower()
            hits += [f"{path.relative_to(REPO)}: {w}" for w in self.FORBIDDEN if w.lower() in text]
        self.assertEqual(hits, [])
