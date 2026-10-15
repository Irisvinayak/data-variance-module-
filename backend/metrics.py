# metrics.py - small in-process metrics, rendered in the Prometheus text format
# (code review L-17). No external dependency and nothing is sent anywhere: the numbers are
# held in this process and served by GET /metrics when DV_METRICS_ENABLED=true (404 otherwise).
#
# What is measured (all labels are low-cardinality constants):
#   dv_http_requests_total{method,route,status}   every request, by MATCHED route template
#   dv_http_request_seconds{route}                request latency histogram
#   dv_http_rate_limited_total                    requests refused by the rate limiter
#   dv_db_queries_total{outcome}                  ok | error | busy | unavailable | not_configured
#   dv_db_query_seconds                           query latency histogram (acquire + run)
#   dv_db_result_truncated_total                  results cut at DV_DB_MAX_ROWS
#   dv_llm_calls_total{caller,outcome}            caller: intent | sql; outcome: ok | failed
#   dv_llm_call_seconds{caller}                   LLM call latency histogram
#   dv_nl_resolutions_total{mode}                 llm | fallback (see /nlresolve resolution_mode)
#   dv_llm_truncated_total{caller}                replies cut off by the token cap (done_reason=length)
#
# What is deliberately NOT a label or a value: login ids, tenant ids, questions, SQL, table
# or return names, file paths, error text. Route labels are the route TEMPLATE
# ("/variance/compute"), never the request URL.

from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager

_BUCKETS = (0.005, 0.025, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)

_lock = threading.Lock()
_counters: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
_histograms: dict[str, dict[tuple, list]] = defaultdict(dict)       # name -> labels -> [bucket counts..., sum, count]

HELP = {
    "dv_http_requests_total": "HTTP requests by method, matched route template and status.",
    "dv_http_request_seconds": "HTTP request latency in seconds.",
    "dv_http_rate_limited_total": "Requests refused by the per-login/global rate limiter.",
    "dv_db_queries_total": "Oracle queries by outcome.",
    "dv_db_query_seconds": "Oracle query latency in seconds (connection acquisition included).",
    "dv_db_result_truncated_total": "Requests whose query result was cut off at DV_DB_MAX_ROWS (counted once per request).",
    "dv_llm_calls_total": "LLM calls by caller and outcome.",
    "dv_llm_call_seconds": "LLM call latency in seconds.",
    "dv_nl_resolutions_total": "Natural-language resolutions by how they were decided.",
    "dv_llm_truncated_total": "LLM replies that stopped at the token cap (done_reason=length), by caller.",
}


def inc(name: str, amount: float = 1.0, **labels: str) -> None:
    key = tuple(sorted((k, str(v)) for k, v in labels.items()))
    with _lock:
        _counters[name][key] += amount


def observe(name: str, seconds: float, **labels: str) -> None:
    key = tuple(sorted((k, str(v)) for k, v in labels.items()))
    with _lock:
        row = _histograms[name].get(key)
        if row is None:
            row = _histograms[name][key] = [0] * len(_BUCKETS) + [0.0, 0]
        for i, bound in enumerate(_BUCKETS):
            if seconds <= bound:
                row[i] += 1
        row[-2] += seconds
        row[-1] += 1


@contextmanager
def timer(name: str, **labels: str):
    """Observe the wall time of the block, also when it raises."""
    start = time.perf_counter()
    try:
        yield
    finally:
        observe(name, time.perf_counter() - start, **labels)


def reset() -> None:
    with _lock:
        _counters.clear()
        _histograms.clear()


def snapshot() -> dict:
    """A plain copy, for tests: {"counters": {name: {labels: value}}, "histograms": {name: {labels: (count, sum)}}}."""
    with _lock:
        return {
            "counters": {n: {k: v for k, v in d.items()} for n, d in _counters.items()},
            "histograms": {n: {k: (row[-1], row[-2]) for k, row in d.items()} for n, d in _histograms.items()},
        }


def _labels(key: tuple, extra: tuple = ()) -> str:
    items = list(key) + list(extra)
    if not items:
        return ""
    escaped = ",".join(
        '{}="{}"'.format(k, str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")) for k, v in items
    )
    return "{" + escaped + "}"


def render_prometheus() -> str:
    lines: list[str] = []
    with _lock:
        for name in sorted(_counters):
            lines += [f"# HELP {name} {HELP.get(name, name)}", f"# TYPE {name} counter"]
            for key, value in sorted(_counters[name].items()):
                lines.append(f"{name}{_labels(key)} {value:g}")
        for name in sorted(_histograms):
            lines += [f"# HELP {name} {HELP.get(name, name)}", f"# TYPE {name} histogram"]
            for key, row in sorted(_histograms[name].items()):
                for bound, count in zip(_BUCKETS, row, strict=False):
                    lines.append(f"{name}_bucket{_labels(key, (('le', f'{bound:g}'),))} {count}")
                lines.append(f"{name}_bucket{_labels(key, (('le', '+Inf'),))} {row[-1]}")
                lines.append(f"{name}_sum{_labels(key)} {row[-2]:.6f}")
                lines.append(f"{name}_count{_labels(key)} {row[-1]}")
    return "\n".join(lines) + ("\n" if lines else "")
