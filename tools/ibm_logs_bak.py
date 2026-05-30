"""
IBM Cloud Logs (Coralogix-based) query tool.

This is the PRIMARY investigation tool — aggregated logs persist across pod
restarts, deployments, and scale-downs, unlike `kubectl logs` which only sees
currently-running pods. Root-cause evidence usually lives here.

Auth model:
  - IBM_CLOUD_API_KEY (env) is exchanged for a short-lived IAM bearer token.
  - The bearer token is used for the Logs query API.
  - Credentials never come from config.yaml (that file is committed to git).

Endpoint:
  Set IBM_LOGS_ENDPOINT to the *API* endpoint for your instance, e.g.:
    export IBM_LOGS_ENDPOINT=https://<guid>.api.us-south.logs.cloud.ibm.com

  If you accidentally set the ingress endpoint (.ingress. host), the code
  converts it automatically. The ingress host (for log shipping) and the API
  host (for queries) share the same GUID and region but differ in the subdomain.

Log record structure (from the actual ingestion format):
  - applicationName  : Kubernetes namespace (e.g. "si")
  - subsystemName    : component / container name (e.g. "DataCollector")
  - text.message     : the human-readable log line
  - timestamp        : epoch milliseconds

Lucene field reference for queries:
  applicationName:"si"                         -- namespace scope
  subsystemName:"DataCollector"                -- component scope
  text.message:"UNKNOWN_TOPIC_OR_PARTITION"    -- message keyword
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import requests

_IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"

# IBM Cloud Logs query API path (DataPrime endpoint; supports Lucene syntax via
# the QUERY_SYNTAX_LUCENE metadata flag).
_QUERY_PATH = "/v1/query"

# Simple in-process cache so we don't exchange the key on every query.
_token_cache: dict[str, object] = {"token": None, "expires_at": 0.0}


def _get_iam_token() -> str:
    """Exchange the IBM Cloud API key for a short-lived IAM bearer token."""
    now = time.time()
    if _token_cache["token"] and now < float(_token_cache["expires_at"]) - 60:
        return str(_token_cache["token"])

    api_key = os.environ.get("IBM_CLOUD_API_KEY")
    if not api_key:
        raise RuntimeError(
            "IBM_CLOUD_API_KEY is not set. export it before running the agent."
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
    Return the IBM Cloud Logs query API base URL.

    Reads IBM_LOGS_ENDPOINT. If the user set the *ingress* endpoint by mistake
    (subdomain contains '.ingress.'), we convert it to the API endpoint by
    replacing '.ingress.' with '.api.' — both share the same GUID and region.
    """
    raw = os.environ.get("IBM_LOGS_ENDPOINT", "").rstrip("/")
    if not raw:
        return ""
    # Auto-correct ingress → api subdomain
    return raw.replace(".ingress.", ".api.")


def _iso8601(ts_sec: float) -> str:
    return datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


def _extract_text(entry: dict) -> str:
    """
    Extract the human-readable log line from a query result entry.

    The IBM Cloud Logs record structure (as seen in the ingestion format):
      - top-level "text" is a dict that contains "message"
      - applicationName / subsystemName / timestamp are top-level siblings

    In the query response the full record arrives as a JSON string in the
    "userdata" field. We parse it and pull text.message first, then fall back
    through common alternatives.
    """
    userdata_raw = entry.get("user_data", "{}")
    try:
        ud = json.loads(userdata_raw) if isinstance(userdata_raw, str) else userdata_raw
    except (json.JSONDecodeError, TypeError):
        return str(userdata_raw)

    # Priority order for the actual message text
    # 1. text.message  (Coralogix structured log from the si namespace)
    text_obj = ud.get("text")
    if isinstance(text_obj, dict):
        msg = text_obj.get("message") or text_obj.get("msg") or text_obj.get("log")
        if msg:
            return str(msg)
        # Fall back to compact JSON of the text object
        return json.dumps(text_obj, separators=(",", ":"))
    if isinstance(text_obj, str) and text_obj:
        return text_obj

    # 2. Top-level message / log / msg
    for field in ("message", "msg", "log", "textPayload", "MESSAGE"):
        if field in ud:
            return str(ud[field])

    # 3. Entire userdata as compact JSON
    return json.dumps(ud, separators=(",", ":"))


def _parse_streaming_response(resp: requests.Response, limit: int) -> list[tuple[str, str]]:
    """
    Parse an NDJSON (or SSE) streaming response from the IBM Cloud Logs API.

    Each line is one of:
      {"result":{"results":[{...}]}}
      {"queryStats":{...}}            -- progress/stats, ignored
      {"error":{...}}                 -- API error

    SSE prefix ("data: ") is stripped if present.
    Returns a list of (timestamp_str, text) pairs.
    """
    entries: list[tuple[str, str]] = []
    for raw in resp.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        # Strip Server-Sent Events prefix if present
        if line.startswith("data:"):
            line = line[5:].lstrip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if "error" in obj:
            raise ValueError(f"API error: {obj['error']}")

        for result_entry in obj.get("result", {}).get("results", []):
            # metadata is a list of {key, value} dicts
            meta = {
                m["key"]: m["value"]
                for m in result_entry.get("metadata", [])
                if "key" in m and "value" in m
            }
            ts = meta.get("timestamp", "")
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
) -> str:
    """
    Query IBM Cloud Logs using Lucene syntax and return matching log lines.

    Args:
        query: Lucene keyword/expression (e.g. "UNKNOWN_TOPIC_OR_PARTITION").
               Searched across all text fields; wrap in quotes for exact phrase.
        namespace: maps to applicationName in IBM Cloud Logs (e.g. "si")
        app: optional subsystemName to scope to (e.g. "DataCollector")
        since_minutes: look-back window
        limit: max log lines to return

    Returns:
        Newline-joined log lines, oldest-first, or an explanatory error string.
    """
    endpoint = _resolve_endpoint()
    if not endpoint:
        return (
            "[config needed] IBM_LOGS_ENDPOINT not set.\n"
            "Set it to your IBM Cloud Logs API endpoint:\n"
            "  export IBM_LOGS_ENDPOINT=https://<guid>.api.us-south.logs.cloud.ibm.com\n"
            "The GUID and region appear in: IBM Cloud console → your Logs instance → Endpoints."
        )

    try:
        token = _get_iam_token()
    except RuntimeError as e:
        return f"[auth error] {e}"
    except requests.HTTPError as e:
        return f"[IAM token error] HTTP {e.response.status_code}: {e.response.text[:200]}"
    except requests.RequestException as e:
        return f"[IAM network error] {e}"

    now_ts = time.time()
    start_ts = now_ts - since_minutes * 60

    # Lucene query: scope by applicationName (= namespace), optionally by
    # subsystemName (= app/component), then the caller's keyword expression.
    scope_parts = [f'coralogix.applicationname:"{namespace}"']
    if app:
        scope_parts.append(f'coralogix.subsystemname:"{app}"')
    full_query = " AND ".join(scope_parts) + f" AND ({query})"


    payload = {
        "query": full_query,
        "metadata": {
            "startDate": _iso8601(start_ts),
            "endDate": _iso8601(now_ts),
            "syntax": "lucene",
            "limit": limit,
        },
    }

    try:
        resp = requests.post(
            f"{endpoint}{_QUERY_PATH}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=90,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        snippet = ""
        try:
            snippet = f" — {e.response.text[:400]}"
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
        entries = _parse_streaming_response(resp, limit)
    except ValueError as e:
        return f"[logs api error] {e}"
    except Exception as e:
        return f"[logs parse error] {e}"
    
    print("STATUS:", resp.status_code)
    print("HEADERS:", dict(resp.headers))
    print("TEXT:", resp.text[:2000])

    if not entries:
        return (
            f"[no matching logs] query='{query}' applicationName={namespace} "
            f"window={since_minutes}m\n"
            f"Tried: {endpoint}{_QUERY_PATH}"
        )

    # Sort oldest-first by timestamp string (ISO8601 lexicographic sort works)
    entries.sort(key=lambda x: x[0])
    return "\n".join(f"{ts}  {text}" for ts, text in entries[:limit])
