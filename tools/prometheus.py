"""
Prometheus client. Read-only by nature (query API only).
"""
from __future__ import annotations

import requests


class Prometheus:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def query(self, promql: str) -> dict:
        """Instant query."""
        resp = requests.get(
            f"{self.base_url}/api/v1/query",
            params={"query": promql},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def query_range(self, promql: str, start: str, end: str, step: str = "60s") -> dict:
        """Range query over a time window."""
        resp = requests.get(
            f"{self.base_url}/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": step},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()


def summarize_result(raw: dict, max_series: int = 25) -> str:
    """
    Turn a Prometheus JSON response into a compact text table the LLM can read.
    Avoids dumping huge JSON into the context window.
    """
    if raw.get("status") != "success":
        return f"[query error] {raw.get('error', 'unknown error')}"

    result = raw.get("data", {}).get("result", [])
    if not result:
        return "[no data] query returned zero series"

    lines = []
    for series in result[:max_series]:
        metric = series.get("metric", {})
        # Build a readable label set, dropping noisy internal labels
        label_str = ", ".join(
            f"{k}={v}"
            for k, v in metric.items()
            if k not in ("__name__", "job", "instance")
        )
        value = series.get("value", series.get("values", ["", "?"]))
        val = value[1] if isinstance(value, list) else value
        try:
            val = f"{float(val):.1f}"
        except (ValueError, TypeError):
            pass
        lines.append(f"  {label_str or '(no labels)'} => {val}")

    header = f"{len(result)} series (showing up to {max_series}):"
    return header + "\n" + "\n".join(lines)
