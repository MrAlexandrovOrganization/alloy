"""Readiness must reject quiet/missing/stale data after aggregate queries."""

import io
import gzip
import json
import os
from pathlib import Path
import runpy
import unittest
import urllib.parse
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/smoke.py"


class DeliverySmokeTests(unittest.TestCase):
    def run_smoke(self, *, logs=1, containers=1, fresh=True, failed_collector=False, jobs=4, compressed=False):
        requests = []

        def request(request, timeout):
            url = request.full_url
            requests.append(url)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["query"][0]
            if query.startswith("count(count by(job)"):
                result = [{"metric": {}, "value": [10000, str(jobs)]}]
            elif query.startswith("min(timestamp"):
                result = [{"metric": {}, "value": [10000, "10000" if fresh else "9999"]}]
            elif query.startswith("count(container_memory"):
                result = [{"metric": {}, "value": [10000, str(containers)]}]
            elif query.startswith("sum(count_over_time"):
                result = [{"metric": {}, "value": [10000, str(logs)]}]
            elif query.startswith("timestamp(up{service="):
                result = [{"metric": {}, "value": [10000, "10000"]}]
            elif query.startswith("count(node_scrape_collector_success"):
                result = [{"metric": {}, "value": [10000, "1"]}] if failed_collector else []
            else:
                raise AssertionError("Unexpected unbounded probe query")
            body = json.dumps({"data": {"result": result}}).encode()
            response = io.BytesIO(gzip.compress(body) if compressed else body)
            response.headers = {"Content-Encoding": "gzip"} if compressed else {}
            return response

        environment = {
            "ALLOY_HOST": "fixture",
            "PROMETHEUS_QUERY_URL": "http://prometheus.invalid",
            "LOKI_QUERY_URL": "http://loki.invalid",
            "HOST_NETWORK_METRICS_ADDRESS": "100.64.0.99:19100",
            "READINESS_TCP_MSS": "0",
        }
        with patch.dict(os.environ, environment, clear=True), patch("urllib.request.urlopen", side_effect=request), \
                patch("time.time", return_value=10000), patch("time.sleep"), patch("builtins.print"):
            self.namespace = runpy.run_path(str(SCRIPT), run_name="__main__")
        return requests

    def test_fresh_metrics_and_logs_are_verified_without_fetching_log_bodies(self):
        requests = self.run_smoke()
        self.assertEqual(len(requests), 6)
        self.assertFalse(any("query_range" in url for url in requests))

    def test_standard_gzip_responses_preserve_readiness_semantics(self):
        self.assertEqual(len(self.run_smoke(compressed=True)), 6)

    def test_zero_log_count_does_not_pass_as_a_nonempty_response(self):
        with self.assertRaises(SystemExit):
            self.run_smoke(logs=0)

    def test_zero_named_container_count_is_not_readiness(self):
        with self.assertRaises(SystemExit):
            self.run_smoke(containers=0)

    def test_samples_before_activation_do_not_confirm_new_delivery(self):
        with self.assertRaises(SystemExit):
            self.run_smoke(fresh=False)

    def test_failed_network_collector_blocks_readiness(self):
        with self.assertRaises(SystemExit):
            self.run_smoke(failed_collector=True)

    def test_a_missing_core_job_cannot_pass_as_a_positive_job_count(self):
        with self.assertRaises(SystemExit):
            self.run_smoke(jobs=3)

    def test_opt_in_mss_is_set_before_connect_and_socket_is_closed(self):
        self.run_smoke()
        response = io.BytesIO(b'{"data":{"result":[]}}')
        response.headers = {}
        response.status = 200
        with patch.dict(os.environ, {"READINESS_TCP_MSS": "1000"}, clear=True), \
                patch("socket.socket") as socket_factory, patch("http.client.HTTPConnection") as connection:
            connection.return_value.getresponse.return_value = response
            result = self.namespace["query"]("http://100.64.0.3:9090/api/v1/query", "up")
            sock = socket_factory.return_value.__enter__.return_value
            operations = [call[0] for call in sock.mock_calls]
            self.assertLess(operations.index("setsockopt"), operations.index("connect"))
            self.assertEqual(sock.setsockopt.call_args.args[-1], 1000)
            connection.return_value.close.assert_called_once()
            self.assertEqual(result, [])

    def test_mss_opt_in_refuses_public_or_tls_endpoints_before_connecting(self):
        self.run_smoke()
        with patch.dict(os.environ, {"READINESS_TCP_MSS": "1000"}, clear=True), patch("socket.socket") as socket_factory:
            for endpoint in ("http://203.0.113.1:9090", "https://100.64.0.3:9090", "http://user:secret@100.64.0.3:9090"):
                with self.assertRaises(ValueError):
                    self.namespace["query"](endpoint, "up")
            socket_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
