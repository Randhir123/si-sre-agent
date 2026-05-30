"""
IBM Cloud Logs query tool.

Uses IBM Cloud Logs / Coralogix query API.

Environment variables:
  IBM_CLOUD_API_KEY   IBM Cloud API key
  IBM_LOGS_ENDPOINT   IBM Cloud Logs API endpoint, for example:
                      https://<guid>.api.us-south.logs.cloud.ibm.com

Example:

  python -c "
  from dotenv import load_dotenv; load_dotenv()
  from tools.ibm_logs import query_logs

  print(query_logs(
      'UNKNOWN_TOPIC_OR_PARTITION',
      namespace='si',
      app='multi-system-processor',
      since_minutes=120,
      limit=10,
      debug=True
  ))
  "
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

_IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
_QUERY_PATH = "/v1/query"

_token_cache: dict[str, object] = {
    "token": None,
    "expires_at": 0.0,
}


def _get_iam_token() -> str:
    """Exchange IBM Cloud API key for a short-lived IAM bearer token."""
    now = time.time()

    if _token_cache["token"] and now < float(_token_cache["expires_at"]) - 60:
        return str(_token_cache["token"])

    api_key = os.environ.get("IBM_CLOUD_API_KEY")
    if not api_key:
        raise RuntimeError(
            "IBM_CLOUD_API_KEY is not set. Export it before running this tool."
        )

    resp = requests.post(
        _IAM_TOKEN_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={
            "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
            "apikey": api_key,
        },
        timeout=30,
    )
    resp.raise_for_status()

    body = resp.json()
    _token_cache["token"] = body["access_token"]
    _token_cache["expires_at"] = now + int(body.get("expires_in", 3600))

    return str(_token_cache["token"])


def _resolve_endpoint() -> str:
    """
    Return IBM Cloud Logs API endpoint.

    Converts ingress endpoint to api endpoint if needed.
    """
    raw = os.environ.get("IBM_LOGS_ENDPOINT", "").rstrip("/")
    if not raw:
        return ""

    return raw.replace(".ingress.", ".api.")


def _iso8601(ts_sec: float) -> str:
    return datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


def _escape_dp_string(value: str) -> str:
    """
    Escape a string for use inside a DataPrime single-quoted string.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _build_dataprime_query(
    query: str,
    namespace: str,
    app: str | None,
    limit: int,
) -> str:
    """
    Build a DataPrime query.

    Important:
      - Labels use $l.*
      - Use wildfind for plain text search.
      - Pass query as plain text, not Lucene syntax.

    Example generated query:

      source logs
      | filter $l.applicationname == 'si'
      | filter $l.subsystemname == 'multi-system-processor'
      | wildfind 'UNKNOWN_TOPIC_OR_PARTITION'
      | limit 10
    """
    lines = [
        "source logs",
        f"| filter $l.applicationname == '{_escape_dp_string(namespace)}'",
    ]

    if app:
        lines.append(f"| filter $l.subsystemname == '{_escape_dp_string(app)}'")

    lines.append(f"| wildfind '{_escape_dp_string(query)}'")
    lines.append(f"| limit {int(limit)}")

    return "\n".join(lines)


def _read_userdata(entry: dict[str, Any]) -> Any:
    """
    Query response entries may use different user-data field names.
    """
    userdata_raw = (
        entry.get("user_data")
        or entry.get("userdata")
        or entry.get("userData")
        or entry.get("data")
        or "{}"
    )

    if isinstance(userdata_raw, str):
        try:
            return json.loads(userdata_raw)
        except json.JSONDecodeError:
            return userdata_raw

    return userdata_raw


def _extract_text(entry: dict[str, Any]) -> str:
    """
    Extract a human-readable log line from one result entry.
    """
    ud = _read_userdata(entry)

    if isinstance(ud, str):
        return ud

    if not isinstance(ud, dict):
        return str(ud)

    text_obj = ud.get("text")

    if isinstance(text_obj, dict):
        for field in ("message", "msg", "log"):
            if text_obj.get(field):
                return str(text_obj[field])
        return json.dumps(text_obj, separators=(",", ":"))

    if isinstance(text_obj, str) and text_obj:
        return text_obj

    for field in (
        "message",
        "msg",
        "log",
        "textPayload",
        "MESSAGE",
        "short_message",
    ):
        if field in ud and ud[field]:
            return str(ud[field])

    return json.dumps(ud, separators=(",", ":"))


def _extract_timestamp(entry: dict[str, Any]) -> str:
    """
    Extract timestamp from metadata or user data.
    """
    metadata = entry.get("metadata", [])

    if isinstance(metadata, list):
        meta = {
            m.get("key"): m.get("value")
            for m in metadata
            if isinstance(m, dict) and "key" in m and "value" in m
        }

        for key in ("timestamp", "Timestamp", "time"):
            if meta.get(key):
                return str(meta[key])

    ud = _read_userdata(entry)
    if isinstance(ud, dict):
        ts = ud.get("timestamp") or ud.get("@timestamp") or ud.get("time")
        if ts:
            return str(ts)

    return ""


def _parse_streaming_response(
    resp: requests.Response,
    limit: int,
    debug: bool = False,
) -> list[tuple[str, str]]:
    """
    Parse IBM Cloud Logs text/event-stream response.

    Possible lines:
      : success
      data: {"query_id": {...}}
      data: {"warning": {...}}
      data: {"result": {"results": [...]}}
      data: {"error": {...}}
    """
    entries: list[tuple[str, str]] = []

    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue

        line = raw.strip()

        if line.startswith(":"):
            if debug:
                print("[logs sse]", line)
            continue

        if line.startswith("data:"):
            line = line[5:].lstrip()

        if not line:
            continue

        if debug:
            print("[logs raw]", line[:1000])

        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            if debug:
                print("[logs non-json]", line[:1000])
            continue

        if "error" in obj:
            raise ValueError(f"API error: {obj['error']}")

        if "warning" in obj:
            if debug:
                print("[logs warning]", obj["warning"])
            continue

        result = obj.get("result")
        if not isinstance(result, dict):
            continue

        results = result.get("results", [])
        if not isinstance(results, list):
            continue

        for result_entry in results:
            if not isinstance(result_entry, dict):
                continue

            ts = _extract_timestamp(result_entry)
            text = _extract_text(result_entry)

            entries.append((ts, text))

            if len(entries) >= limit:
                return entries

    return entries


def query_logs(
    query: str,
    namespace: str,
    app: str | None = None,
    since_minutes: int = 60,
    limit: int = 200,
    debug: bool = False,
) -> str:
    """
    Query IBM Cloud Logs and return matching log lines.

    Args:
        query:
            Plain text to search for.

            Good:
              UNKNOWN_TOPIC_OR_PARTITION
              SSLHandshakeException
              handshake_failure

            Avoid:
              text.message:"UNKNOWN_TOPIC_OR_PARTITION"

        namespace:
            Kubernetes namespace / Cloud Logs application name.
            Example:
              si

        app:
            Optional subsystem/component/container.
            Example:
              multi-system-processor
              metric-analyser

        since_minutes:
            Look-back window.

        limit:
            Maximum log lines to return.

        debug:
            Print generated query and raw response lines.
    """
    endpoint = _resolve_endpoint()
    if not endpoint:
        return (
            "[config needed] IBM_LOGS_ENDPOINT not set.\n"
            "Set it to your IBM Cloud Logs API endpoint:\n"
            "  export IBM_LOGS_ENDPOINT=https://<guid>.api.us-south.logs.cloud.ibm.com"
        )

    try:
        token = _get_iam_token()
    except RuntimeError as e:
        return f"[auth error] {e}"
    except requests.HTTPError as e:
        return f"[IAM token error] HTTP {e.response.status_code}: {e.response.text[:400]}"
    except requests.RequestException as e:
        return f"[IAM network error] {e}"

    now_ts = time.time()
    start_ts = now_ts - since_minutes * 60

    full_query = _build_dataprime_query(
        query=query,
        namespace=namespace,
        app=app,
        limit=limit,
    )

    payload = {
        "query": full_query,
        "metadata": {
            "startDate": _iso8601(start_ts),
            "endDate": _iso8601(now_ts),
            "syntax": "dataprime",
            "limit": limit,
            "tier": "frequent_search",
        },
    }

    if debug:
        print("ENDPOINT:", f"{endpoint}{_QUERY_PATH}")
        print("QUERY:")
        print(full_query)
        print("START:", payload["metadata"]["startDate"])
        print("END:", payload["metadata"]["endDate"])
        print("PAYLOAD:")
        print(json.dumps(payload, indent=2))

    try:
        resp = requests.post(
            f"{endpoint}{_QUERY_PATH}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json=payload,
            timeout=90,
            stream=True,
        )

        if debug:
            print("STATUS:", resp.status_code)
            print("HEADERS:", dict(resp.headers))

        resp.raise_for_status()

    except requests.HTTPError as e:
        snippet = ""
        try:
            snippet = f" — {e.response.text[:1200]}"
        except Exception:
            pass

        return (
            f"[logs query error] HTTP {e.response.status_code}{snippet}\n"
            f"endpoint: {endpoint}{_QUERY_PATH}\n"
            f"query: {full_query}"
        )

    except requests.RequestException as e:
        return f"[logs network error] {e}"

    try:
        entries = _parse_streaming_response(resp, limit=limit, debug=debug)
    except ValueError as e:
        return f"[logs api error] {e}"
    except Exception as e:
        return f"[logs parse error] {type(e).__name__}: {e}"

    if not entries:
        app_part = f" subsystem={app}" if app else ""
        return (
            f"[no matching logs] query='{query}' namespace={namespace}{app_part} "
            f"window={since_minutes}m\n"
            f"Tried: {endpoint}{_QUERY_PATH}\n"
            f"DataPrime query:\n{full_query}"
        )

    entries.sort(key=lambda x: x[0])

    lines: list[str] = []
    for ts, text in entries[:limit]:
        if ts:
            lines.append(f"{ts}  {text}")
        else:
            lines.append(text)

    return "\n".join(lines)


if __name__ == "__main__":
    print(
        query_logs(
            query="UNKNOWN_TOPIC_OR_PARTITION",
            namespace="si",
            app="multi-system-processor",
            since_minutes=120,
            limit=10,
            debug=True,
        )
    )