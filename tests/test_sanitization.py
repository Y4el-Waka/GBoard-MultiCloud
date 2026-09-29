from __future__ import annotations

import hashlib
import ipaddress
import re
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
    )
)

TECHNICAL_IP_EXCEPTIONS = {
    ipaddress.ip_address("127.0.0.1"): "Loopback usado por los servicios locales del laboratorio.",
    ipaddress.ip_address("0.0.0.0"): "Direccion de bind del servidor Flask; no identifica infraestructura.",
    ipaddress.ip_address("35.235.240.0"): "Rango oficial de proxies IAP TCP de Google Cloud, limitado a SSH.",
    ipaddress.ip_address("169.254.169.254"): "Endpoint link-local oficial de metadata para Azure IMDS y OCI Instance Metadata.",
}

IPV4_LITERAL = re.compile(
    r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"
)

HIGH_CONFIDENCE_SECRET_PATTERNS = {
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[0-9A-Za-z]{20,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b"),
    "Private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}

FROZEN_FRONTEND_SHA256 = {
    "templates/login.html": "9ee39fe858beb3d2ceb7799d4bdfed076613ee1423aa70c2efecdd334775e696",
    "templates/dashboard_prtg.html": "eb81b590f2054b1c3201bf26ce92dfca0362020cd6fd0ef1ac924637960023c5",
    "static/dashboard.js": "9ad78ad6747f7e8bd7b2017b60807c1d2972c55fb1caa61633a81ed8d6ebbc21",
    "static/dashboard.css": "0b5b9ba1dd5a74bca9e3b2d235e427d844fefaa6c0c937e54344ed3e9f22f774",
    "static/metrics-history.js": "3ac3e169dd8e3f620ba5ea81ff5c5ca35e3d8cdca69b1a6eb27b598474bf0f7c",
    "static/vendor/manifest.json": "b71c7893dbacfe592c4c66813f61d2ab57d508c4186bf13fa4c6e5e7529f69a0",
    "static/img/login-bg.jpg": "31fde8ef75dee78eeef69b4cc644a8d17e8e4cc1166639a0b896df2c32d2a6ab",
}


def git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def tracked_text_files() -> list[Path]:
    relative_names = set(
        git_lines("ls-files", "--cached", "--others", "--exclude-standard")
    )
    files = []

    for relative_name in sorted(relative_names):
        path = REPO_ROOT / relative_name

        try:
            path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError):
            continue

        files.append(path)

    return files


class SanitizationTests(unittest.TestCase):
    def test_runtime_and_secret_files_are_ignored_and_untracked(self) -> None:
        protected_paths = (
            ".env",
            "servers.json",
            "usuarios.db",
            "usuarios.db-wal",
            "estado_sensores_prtg.json",
            "static/uploads/example.mp3",
        )
        tracked = set(git_lines("ls-files"))

        for relative_name in protected_paths:
            with self.subTest(path=relative_name):
                result = subprocess.run(
                    ["git", "check-ignore", "--no-index", "-q", relative_name],
                    cwd=REPO_ROOT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, f"{relative_name} debe estar ignorado")
                self.assertNotIn(relative_name, tracked)

    def test_tracked_text_uses_only_documentation_or_documented_technical_ips(self) -> None:
        violations = []

        for path in tracked_text_files():
            text = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), start=1):
                for match in IPV4_LITERAL.finditer(line):
                    try:
                        address = ipaddress.ip_address(match.group(0))
                    except ValueError:
                        continue

                    if address in TECHNICAL_IP_EXCEPTIONS:
                        continue

                    if not any(address in network for network in DOCUMENTATION_NETWORKS):
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)}:{line_number}: {address}"
                        )

        self.assertEqual(
            violations,
            [],
            "IPv4 fuera de TEST-NET o de las excepciones tecnicas documentadas:\n"
            + "\n".join(violations),
        )

    def test_no_high_confidence_secrets_in_tracked_text(self) -> None:
        findings = []

        for path in tracked_text_files():
            text = path.read_text(encoding="utf-8")
            for label, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS.items():
                if pattern.search(text):
                    findings.append(f"{path.relative_to(REPO_ROOT)}: {label}")

        self.assertEqual(findings, [], "Posibles secretos versionados:\n" + "\n".join(findings))

    def test_frontend_files_match_frozen_sha256(self) -> None:
        actual = {
            relative_name: hashlib.sha256(
                (REPO_ROOT / relative_name).read_bytes()
            ).hexdigest()
            for relative_name in FROZEN_FRONTEND_SHA256
        }
        self.assertEqual(actual, FROZEN_FRONTEND_SHA256)


if __name__ == "__main__":
    unittest.main()
