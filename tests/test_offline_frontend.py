from __future__ import annotations

import base64
import hashlib
import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FRONTEND = (
    "templates/login.html",
    "templates/dashboard_prtg.html",
    "static/dashboard.css",
    "static/dashboard.js",
    "static/metrics-history.js",
)
EXPECTED_PACKAGES = {
    "tailwindcss": ("3.4.17", "MIT"),
    "jquery": ("3.7.1", "MIT"),
    "toastr": ("2.1.4", "MIT"),
    "chart.js": ("4.5.1", "MIT"),
    "chartjs-adapter-date-fns": ("3.0.0", "MIT"),
    "@fortawesome/fontawesome-free": (
        "6.4.0",
        "(CC-BY-4.0 AND OFL-1.1 AND MIT)",
    ),
    "@fontsource/inter": ("5.3.0", "OFL-1.1"),
}
PLAYWRIGHT_DIGEST = "sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27"


class OfflineFrontendTests(unittest.TestCase):
    def test_package_lock_fixes_every_direct_asset_and_integrity(self) -> None:
        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))

        self.assertTrue(package["private"])
        self.assertEqual(package["engines"]["node"], ">=20")
        self.assertEqual(lock["lockfileVersion"], 3)
        self.assertEqual(lock["packages"][""]["engines"]["node"], ">=20")
        for name, (version, _license) in EXPECTED_PACKAGES.items():
            self.assertEqual(package["devDependencies"][name], version)
            locked = lock["packages"][f"node_modules/{name}"]
            self.assertEqual(locked["version"], version)
            self.assertRegex(locked["integrity"], r"^sha512-")

        self.assertEqual(package["devDependencies"]["playwright"], "1.63.0")
        playwright = lock["packages"]["node_modules/playwright"]
        self.assertEqual(playwright["version"], "1.63.0")
        self.assertRegex(playwright["integrity"], r"^sha512-")

    def test_vendor_manifest_covers_assets_with_sha256_and_licenses(self) -> None:
        vendor = REPO_ROOT / "static" / "vendor"
        manifest = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)

        for name, (version, license_name) in EXPECTED_PACKAGES.items():
            self.assertEqual(manifest["packages"][name]["version"], version)
            self.assertEqual(manifest["packages"][name]["license"], license_name)

        observed = {}
        for path in sorted(vendor.rglob("*")):
            if not path.is_file() or path.name == "manifest.json":
                continue
            relative = path.relative_to(vendor).as_posix()
            observed[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(observed, manifest["assets"])
        self.assertEqual(len(list((vendor / "licenses").glob("*.txt"))), 7)

    def test_runtime_references_only_local_versioned_assets(self) -> None:
        external = re.compile(
            r"(?:src|href)=[\"']https?://|@import\s+url\([\"']?https?://",
            re.IGNORECASE,
        )
        for relative in RUNTIME_FRONTEND:
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                self.assertIsNone(external.search(source))

        dashboard = (REPO_ROOT / "templates/dashboard_prtg.html").read_text(
            encoding="utf-8"
        )
        for version in ("3.4.17", "5.3.0", "6.4.0", "2.1.4", "4.5.1"):
            self.assertIn(f"?v={version}", dashboard)

    def test_tailwind_contains_static_and_dynamic_runtime_classes(self) -> None:
        css = (
            REPO_ROOT / "static/vendor/tailwind/tailwind.min.css"
        ).read_text(encoding="utf-8")
        for selector in (
            ".hidden",
            ".flex",
            ".scale-105",
            ".bg-red-50",
            ".text-red-700",
            ".border-red-200",
            ".text-prtg-paused",
            ".dark\\:bg-\\[\\#161B22\\]",
            ".bg-black\\/60",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)

    def test_csp_is_local_only_and_matches_every_inline_block(self) -> None:
        app_source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
        csp_section = app_source.split('resp.headers["Content-Security-Policy"]', 1)[1]
        csp_section = csp_section.split('resp.headers["Cache-Control"]', 1)[0]
        self.assertNotIn("unsafe-inline", csp_section)
        self.assertNotIn("unsafe-eval", csp_section)
        for host in (
            "cdn.tailwindcss.com",
            "cdn.jsdelivr.net",
            "cdnjs.cloudflare.com",
            "fonts.googleapis.com",
            "fonts.gstatic.com",
        ):
            self.assertNotIn(host, csp_section)
        for directive in ("base-uri 'self'", "form-action 'self'", "object-src 'none'"):
            self.assertIn(directive, csp_section)

        for relative in ("templates/login.html", "templates/dashboard_prtg.html"):
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            source = source.replace(
                "{{ url_for('static', filename='img/login-bg.jpg') }}",
                "/static/img/login-bg.jpg",
            )
            for tag in ("script", "style"):
                for match in re.finditer(
                    rf"<{tag}(?: [^>]*)?>(.*?)</{tag}>", source, re.DOTALL
                ):
                    opening_tag = match.group(0).split(">", 1)[0]
                    if tag == "script" and "src=" in opening_tag:
                        continue
                    digest = base64.b64encode(
                        hashlib.sha256(match.group(1).encode("utf-8")).digest()
                    ).decode("ascii")
                    self.assertIn(f"'sha256-{digest}'", csp_section)

    def test_docker_builds_assets_without_node_in_runtime_stage(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("AS frontend-builder", dockerfile)
        self.assertIn("npm ci --ignore-scripts", dockerfile)
        self.assertIn("npm run build:frontend", dockerfile)
        self.assertIn("COPY --from=frontend-builder", dockerfile)
        runtime_stage = dockerfile.split("FROM python:", 1)[1]
        self.assertNotIn("npm ", runtime_stage)
        self.assertNotIn("node_modules", runtime_stage)

    def test_browser_smoke_has_pinned_browser_and_no_external_network(self) -> None:
        runner = (REPO_ROOT / "scripts/run_offline_frontend_smoke.sh").read_text(
            encoding="utf-8"
        )
        browser = (REPO_ROOT / "scripts/offline_frontend_smoke.mjs").read_text(
            encoding="utf-8"
        )

        self.assertIn(f"playwright:v1.63.0-noble@{PLAYWRIGHT_DIGEST}", runner)
        self.assertIn("docker network create --internal", runner)
        self.assertIn("--cap-drop ALL", runner)
        self.assertIn("--security-opt no-new-privileges:true", runner)
        self.assertIn("externalRequests", browser)
        self.assertIn("await route.abort('blockedbyclient')", browser)
        self.assertIn("Chart?.version", browser)
        self.assertIn("content-security-policy", browser)
        self.assertIn("legacy-fallback", browser)

    def test_soak_default_is_one_hour_and_records_runtime_health(self) -> None:
        soak = (REPO_ROOT / "scripts/docker_lab_soak.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('default=3600, help="seconds"', soak)
        self.assertIn('default=30, help="seconds"', soak)
        self.assertIn('"restart_count"', soak)
        self.assertIn('"oom_killed"', soak)
        self.assertIn('"collector_errors"', soak)
        self.assertIn('"p95"', soak)
        self.assertIn('"linear_slope_per_hour"', soak)
        self.assertIn('"snapshot_age_seconds"', soak)


if __name__ == "__main__":
    unittest.main()
