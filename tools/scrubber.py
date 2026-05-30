"""
Credential scrubber.

The agent prints its reasoning trace and a final report. Both could, in
principle, echo back a token that appeared in a tool's error message or an
environment dump. This module strips anything token-shaped before any text
is printed or written to a file.

Defense-in-depth: the agent is also told never to query secrets, but we do
not rely on that — we scrub unconditionally.
"""
from __future__ import annotations

import re

# Patterns that look like secrets. Order matters: more specific first.
_PATTERNS = [
    # IBM Cloud IAM tokens / bearer tokens (JWT-ish: three base64 segments)
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "[REDACTED_JWT]"),
    # Bearer headers
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    # IBM Cloud API keys are typically 44 chars, mixed case + digits, no spaces.
    # Match assignments like apikey=... / api_key: ... / IBM_CLOUD_API_KEY=...
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    # Grafana service-account tokens start with 'glsa_'
    (re.compile(r"\bglsa_[A-Za-z0-9_]+"), "[REDACTED_GRAFANA_TOKEN]"),
    # Generic token= / password= / secret= assignments
    (re.compile(r"(?i)(token\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(password\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(secret\s*[=:]\s*)\S+"), r"\1[REDACTED]"),
]


def scrub(text: str) -> str:
    """Return text with any token-shaped substrings redacted."""
    if not text:
        return text
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def scrub_env_values(text: str, var_names: tuple[str, ...]) -> str:
    """
    Extra safety: redact the *literal current values* of known secret env vars
    if they happen to appear anywhere in text.
    """
    import os

    out = text
    for name in var_names:
        val = os.environ.get(name)
        if val and len(val) >= 8:  # don't redact trivially short values
            out = out.replace(val, f"[REDACTED:{name}]")
    return out


# Env vars whose literal values must never appear in output.
SECRET_ENV_VARS = ("IBM_CLOUD_API_KEY", "GRAFANA_TOKEN", "IBM_MONITORING_TOKEN")


def safe_output(text: str) -> str:
    """Full scrub pipeline: pattern-based + literal-env-value based."""
    return scrub_env_values(scrub(text), SECRET_ENV_VARS)
