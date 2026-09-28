from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from jinja2 import Environment


REPO_ROOT = Path(__file__).resolve().parents[1]


class FrontendMetricsTests(unittest.TestCase):
    def test_node_frontend_contract_suite(self) -> None:
        result = subprocess.run(
            ["node", "--test", "tests/frontend/test_metrics_history.js"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Frontend tests failed:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("# tests 18", result.stdout)
        self.assertIn("# pass 18", result.stdout)

    def test_feature_flag_is_rendered_as_a_safe_boolean(self) -> None:
        source = (REPO_ROOT / "templates" / "dashboard_prtg.html").read_text(
            encoding="utf-8"
        )
        environment = Environment(autoescape=True)
        environment.globals["url_for"] = lambda endpoint, **values: (
            f"/{values.get('filename', endpoint)}"
        )
        template = environment.from_string(source)

        disabled = template.render(
            metrics_ui_enabled=False,
            usuario="synthetic-user",
            rol="lector",
        )
        enabled = template.render(
            metrics_ui_enabled=True,
            usuario="synthetic-user",
            rol="lector",
        )

        self.assertIn('data-metrics-ui-enabled="false"', disabled)
        self.assertIn('data-metrics-ui-enabled="true"', enabled)
        self.assertNotIn("COREVIEW_METRICS_UI_ENABLED", enabled)

    def test_metrics_frontend_uses_canonical_channels_and_safe_dom_apis(self) -> None:
        module = (REPO_ROOT / "static" / "metrics-history.js").read_text(
            encoding="utf-8"
        )
        dashboard = (REPO_ROOT / "static" / "dashboard.js").read_text(
            encoding="utf-8"
        )

        for canonical_key in (
            "latency_ms",
            "cpu_usage_pct",
            "memory_used_bytes",
            "traffic_in_bps",
            "traffic_out_bps",
            "rx_power_dbm",
            "tx_power_dbm",
        ):
            self.assertIn(canonical_key, module)

        for source_name in (
            "Ping Time",
            "CPU Load",
            "Traffic In",
            "Traffic Out",
            "RX Power",
            "TX Power",
        ):
            self.assertNotIn(source_name, module)

        self.assertNotIn("innerHTML", module)
        self.assertIn("document.createElement('canvas')", dashboard)
        self.assertIn("metricsHistoryClient?.cancel()", dashboard)
        self.assertIn("historyChart.destroy()", dashboard)

        legacy_loader = dashboard.index("async function fetchAndRenderLegacyHistory")
        fallback_badge = dashboard.index("setHistoryMode(", legacy_loader)
        legacy_request = dashboard.index("try {", legacy_loader)
        self.assertLess(
            fallback_badge,
            legacy_request,
            "legacy/fallback mode must be visible even if its request fails",
        )


if __name__ == "__main__":
    unittest.main()
