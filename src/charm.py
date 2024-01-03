#!/usr/bin/env python3
# Copyright 2023 pguimaraes
# See LICENSE file for licensing details.

"""This class manages the sysbench systemd service."""

import logging
import os
import subprocess
from typing import Any, Dict

import ops
from charms.data_platform_libs.v0.data_interfaces import OpenSearchRequires
from charms.operator_libs_linux.v0 import apt
from charms.operator_libs_linux.v1.systemd import (
    daemon_reload,
    service_restart,
    service_running,
    service_stop,
)
from charms.operator_libs_linux.v2 import snap
from charms.prometheus_k8s.v1.prometheus_remote_write import PrometheusRemoteWriteConsumer
from jinja2 import Environment, FileSystemLoader, exceptions
from ops.main import main

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ["info", "debug", "warning", "error", "critical"]


INDEX_NAME = "dummy"

K6_SVC = "k6"
K6_RESOURCE = "xk6"

# For now, create another user later
K6_SVC = "k6"
K6_SVC_PATH = f"/etc/systemd/system/{K6_SVC}.service"

K6_PATH = "/usr/share/xk6_opensearch/"
JS_SCRIPT = "script.js"

OPENSEARCH_RELATION = "opensearch"
SEND_RW_RELATION = "send-remote-write"


def _render(src_template_file: str, dst_filepath: str, values: Dict[str, Any]):
    templates_dir = os.path.join(os.environ.get("CHARM_DIR", ""), "templates")
    template_env = Environment(loader=FileSystemLoader(templates_dir))
    try:
        template = template_env.get_template(src_template_file)
        content = template.render(values)
    except exceptions.TemplateNotFound as e:
        raise e
    # save the file in the destination
    with open(dst_filepath, "w") as f:
        f.write(content)
        os.chmod(dst_filepath, 0o640)


class K6Operator(ops.CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.run_action, self.on_run_action)
        self.framework.observe(self.on.stop_action, self.on_stop_action)

        self.database = OpenSearchRequires(self, OPENSEARCH_RELATION, INDEX_NAME)
        self.framework.observe(
            self.on[OPENSEARCH_RELATION].relation_broken, self._on_relation_broken
        )
        self.remote_write_consumer = PrometheusRemoteWriteConsumer(self)
        self.framework.observe(
            self.remote_write_consumer.on.endpoints_changed,
            self._rw_changed,
        )

    def _rw_changed(self, _):
        """Update the remote write url."""
        pass

    @property
    def _k6_config(self):
        """Returns the database config to use to connect to the MySQL cluster."""
        # identify the database relation
        data = list(self.database.fetch_relation_data().values())[0]

        username, password, endpoints = (
            data.get("username"),
            data.get("password"),
            data.get("endpoints"),
        )
        if None in [username, password, endpoints]:
            return {}

        config = {
            "user": username,
            "password": password,
            "database": INDEX_NAME,
        }
        if endpoints.startswith("file://"):
            config["unix_socket"] = endpoints[7:]
        else:
            host, port = endpoints.split(":")
            config["host"] = host
            config["port"] = port

        return config

    def __del__(self):
        """Set status for the operator and finishes the service."""
        pass
        # self.unit.status = self.status()

    @property
    def is_tls_enabled(self):
        """Return tls status."""
        return False

    @property
    def _unit_ip(self) -> str:
        """Current unit ip."""
        return self.model.get_binding(SEND_RW_RELATION).network.bind_address

    def _on_config_changed(self, _):
        # For now, ignore the configuration
        pass

    def on_stop_action(self, event):
        """Stop benchmark action."""
        if service_running(K6_SVC):
            service_stop(K6_SVC)

    def _on_relation_broken(self, _):
        if service_running(K6_SVC):
            service_stop(K6_SVC)

    def _on_install(self, _):
        """Installs the basic packages and python dependencies.

        No exceptions are captured as we need all the dependencies below to even start running.
        """
        apt.update()
        apt.add_package(["snapd", "python3-jinja2", "unzip"])
        subprocess.check_output(["mkdir", "-p", K6_PATH])

        cache = snap.SnapCache()
        go = cache["go"]

        if not go.present:
            go.ensure(snap.SnapState.Latest, channel="1.20/stable")

    def _install_xk6(self):
        k6_resource = self.model.resources.fetch(K6_RESOURCE)
        try:
            subprocess.check_output(["unzip", "-o", "-j", k6_resource, "-d", K6_PATH])
        except Exception as e:
            raise e
        os.chmod(K6_PATH + K6_SVC, 0o777)

    def on_run_action(self, event):
        """Run benchmark action."""
        if (
            not self.model.get_relation(OPENSEARCH_RELATION)
            or len(self.model.get_relation(OPENSEARCH_RELATION).units) == 0
        ):
            event.fail("No OpenSearch relation found")
            return
        _render(
            f"{K6_SVC}.service.j2",
            K6_SVC_PATH,
            {
                "xk6_path": K6_PATH,
                "vus": event.params.get("clients", 10),
                "duration": event.params.get("duration", 0),
                "js_script": JS_SCRIPT,
                "prometheus_rw_server_url": self.remote_write_consumer.endpoints[0]["url"],
                "prometheus_rw_push_interval": self.config["remote_write_interval"],
            },
        )
        db = self._k6_config
        num_units = len(self.model.get_relation(OPENSEARCH_RELATION).units)
        shards = (
            event.params.get("shards", 1)
            if num_units > event.params.get("shards", 1)
            else num_units
        )
        replicas = event.params.get("replicas", 0)

        if service_running(K6_SVC):
            service_stop(K6_SVC)

        if shards > 0:
            replicas = num_units // shards - 1
        if replicas < 0:
            replicas = 0
        _render(
            f"{JS_SCRIPT}.j2",
            K6_PATH + JS_SCRIPT,
            {
                "username": db["user"],
                "password": db["password"],
                "url": f"https://{db['host']}:{db['port']}",
                "cleanup": event.params.get("cleanup", False),
                "message_size": event.params.get("message_size", 100),
                "indices_parameters": [
                    {
                        "index": n,
                        "shards": shards,
                        "replicas": replicas,
                    }
                    for n in event.params.get("test_indices", "").split(",")
                ],
            },
        )

        self.duration = event.params.get("duration", 0)

        daemon_reload()
        service_restart(K6_SVC)


if __name__ == "__main__":
    main(K6Operator)
