import unittest
from typing import Any, Dict
from unittest.mock import MagicMock, PropertyMock, call, patch

from charm import (
    JS_SCRIPT,
    K6_PATH,
    K6_SVC,
    K6_SVC_PATH,
    OPENSEARCH_RELATION,
    SEND_RW_RELATION,
    K6Operator,
)
from jinja2 import Environment, FileSystemLoader
from ops.testing import Harness

from .templates import K6_JS_CONTENT, K6_SVC_CONTENT


def _mock_render(src_template_file: str, dst_filepath: str, values: Dict[str, Any]):
    templates_dir = "templates/"
    template_env = Environment(loader=FileSystemLoader(templates_dir))
    template = template_env.get_template(src_template_file)
    content = template.render(values)
    if src_template_file == f"{K6_SVC}.service.j2":
        assert content == K6_SVC_CONTENT
    else:
        assert content == K6_JS_CONTENT
    return content


class TestK6Operator(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(K6Operator)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.harness.set_leader(is_leader=True)
        self.charm = self.harness.charm
        self.prom_rw_id = self.harness.add_relation(SEND_RW_RELATION, "prometheus")
        self.harness.add_relation_unit(self.prom_rw_id, "prometheus/0")
        self.harness.update_relation_data(
            self.prom_rw_id,
            "prometheus/0",
            {"remote_write": '{ "url": "http://prometheus:9090/api/v1/write" }'},
        )
        self.os_id = self.harness.add_relation(OPENSEARCH_RELATION, "opensearch")
        self.harness.add_relation_unit(self.os_id, "opensearch/0")
        self.harness.update_relation_data(
            self.os_id,
            "opensearch/0",
            {
                "username": "test",
                "password": "test",
                "host": "localhost",
                "port": "9200",
            },
        )

    @patch("charm.service_restart")
    @patch("charm.daemon_reload")
    @patch("charm.K6Operator._k6_config", new_callable=PropertyMock)
    @patch("charm._render")
    def test_run_action(self, mock_render, mock_db_config, _, __):
        """Test the run action."""
        event = MagicMock()
        event.params = {
            "clients": 10,
            "duration": 0,
            "shards": 1,
            "replicas": 0,
            "cleanup": False,
            "message_size": 100,
            "test_indices": "test1,test2",
        }
        mock_db_config.return_value = {
            "user": "test",
            "password": "test",
            "host": "localhost",
            "port": "9200",
        }
        mock_render.side_effect = _mock_render
        self.charm.on_run_action(event)
        render_calls = [
            call(
                f"{JS_SCRIPT}.j2",
                K6_PATH + JS_SCRIPT,
                {
                    "username": "test",
                    "password": "test",
                    "url": "https://localhost:9200",
                    "cleanup": False,
                    "message_size": 100,
                    "indices_parameters": [
                        {
                            "index": "test1",
                            "shards": 1,
                            "replicas": 0,
                        },
                        {
                            "index": "test2",
                            "shards": 1,
                            "replicas": 0,
                        },
                    ],
                },
            ),
            call(
                f"{K6_SVC}.service.j2",
                K6_SVC_PATH,
                {
                    "xk6_path": K6_PATH,
                    "vus": 10,
                    "duration": 0,
                    "js_script": JS_SCRIPT,
                    "prometheus_rw_server_url": "http://prometheus:9090/api/v1/write",
                    "prometheus_rw_push_interval": "5s",
                },
            ),
        ]
        mock_render.assert_has_calls(render_calls, any_order=True)
