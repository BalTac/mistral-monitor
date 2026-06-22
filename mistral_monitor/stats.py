"""
stats.py — Statistics engine & trend analysis for Mistral Usage Monitor.
Pure data layer; no visualization. Consumes usage_events from database.py.
"""

from __future__ import annotations

import sqlite3
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("mistral_monitor.stats")

DB_PATH = Path(__file__).resolve().parent / "usage_history.db"

# ─── Data containers ─────────────────────────────────────────────────────────

@dataclass
class CategoryStats:
    category: str
    model_count: int = 0
    avg_token_limit: float = 0.0
    avg_req_limit: float = 0.0
    total_snapshots: int = 0


@dataclass
class LimitRecord:
    model_name: str
    category: str
    token_limit: int
    req_limit: int
    context_length: int = 0
    capabilities: str = ""


@dataclass
class ModelStats:
    model: str
    request_count: int = 0
    total_tokens_observed: int = 0
    avg_tokens_per_request: float = 0.0
    avg_query_cost: float = 0.0
    avg_latency_ms: float = 0.0
    min_latency_ms: int = 0
    max_latency_ms: int = 0
    success_count: int = 0
    failure_count: int = 0


@dataclass
class DailyTrend:
    date: str
    request_count: int = 0
    total_tokens: int = 0
    avg_latency_ms: float = 0.0


@dataclass
class GlobalStats:
    total_events: int = 0
    total_models: int = 0
    per_model: list[ModelStats] = field(default_factory=list)
    daily_trends: list[DailyTrend] = field(default_factory=list)


# ─── Query helpers ───────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        return None  # type: ignore[return-value]
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _window_clause(window: str) -> str:
    """Translate human window to SQL date filter."""
    if window == "today":
        return "DATE(timestamp) = DATE('now')"
    if window == "7d":
        return "timestamp >= datetime('now', '-7 days')"
    if window == "30d":
        return "timestamp >= datetime('now', '-30 days')"
    if window == "all":
        return "1=1"
    return "1=1"


def stats_for_model(model: str, window: str = "all") -> Optional[ModelStats]:
    """Aggregated stats for a single model within a time window."""
    conn = _connect()
    if conn is None:
        return None
    try:
        wc = _window_clause(window)
        row = conn.execute(
            f"""SELECT
                  COUNT(*)                    AS request_count,
                  COALESCE(SUM(query_token_cost), 0) AS total_tokens_observed,
                  COALESCE(AVG(query_token_cost), 0)  AS avg_query_cost,
                  COALESCE(AVG(latency_ms), 0)        AS avg_latency_ms,
                  COALESCE(MIN(latency_ms), 0)        AS min_latency_ms,
                  COALESCE(MAX(latency_ms), 0)        AS max_latency_ms,
                  SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS success_count,
                  SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failure_count
               FROM usage_events
               WHERE model=? AND {wc}""",
            (model,),
        ).fetchone()

        if row is None or row["request_count"] == 0:
            return None

        total_tokens = row["total_tokens_observed"]
        count = row["request_count"]
        return ModelStats(
            model=model,
            request_count=count,
            total_tokens_observed=total_tokens,
            avg_tokens_per_request=total_tokens / count if count else 0,
            avg_query_cost=round(row["avg_query_cost"], 1),
            avg_latency_ms=round(row["avg_latency_ms"], 1),
            min_latency_ms=row["min_latency_ms"],
            max_latency_ms=row["max_latency_ms"],
            success_count=row["success_count"],
            failure_count=row["failure_count"],
        )
    finally:
        conn.close()


def global_stats(window: str = "all") -> GlobalStats:
    """Aggregated statistics across all models."""
    conn = _connect()
    if conn is None:
        return GlobalStats()

    try:
        wc = _window_clause(window)

        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM usage_events WHERE {wc}"
        ).fetchone()["n"]

        model_count = conn.execute(
            f"SELECT COUNT(DISTINCT model) AS n FROM usage_events WHERE {wc}"
        ).fetchone()["n"]

        # per-model
        model_rows = conn.execute(
            f"""SELECT model,
                       COUNT(*) AS request_count,
                       COALESCE(SUM(query_token_cost), 0) AS total_tokens,
                       COALESCE(AVG(query_token_cost), 0)  AS avg_query_cost,
                       COALESCE(AVG(latency_ms), 0)        AS avg_latency_ms,
                       COALESCE(MIN(latency_ms), 0)        AS min_latency_ms,
                       COALESCE(MAX(latency_ms), 0)        AS max_latency_ms,
                       SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS success_count,
                       SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failure_count
                FROM usage_events
                WHERE {wc}
                GROUP BY model
                ORDER BY request_count DESC""",
        ).fetchall()

        per_model = []
        for r in model_rows:
            total_tokens = r["total_tokens"]
            count = r["request_count"]
            per_model.append(
                ModelStats(
                    model=r["model"],
                    request_count=count,
                    total_tokens_observed=total_tokens,
                    avg_tokens_per_request=total_tokens / count if count else 0,
                    avg_query_cost=round(r["avg_query_cost"], 1),
                    avg_latency_ms=round(r["avg_latency_ms"], 1),
                    min_latency_ms=r["min_latency_ms"],
                    max_latency_ms=r["max_latency_ms"],
                    success_count=r["success_count"],
                    failure_count=r["failure_count"],
                )
            )

        # daily trends
        trend_rows = conn.execute(
            f"""SELECT DATE(timestamp) AS day,
                       COUNT(*) AS request_count,
                       COALESCE(SUM(query_token_cost), 0) AS total_tokens,
                       COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
                FROM usage_events
                WHERE {wc}
                GROUP BY day
                ORDER BY day DESC
                LIMIT 90""",
        ).fetchall()

        daily_trends = [
            DailyTrend(
                date=r["day"],
                request_count=r["request_count"],
                total_tokens=r["total_tokens"],
                avg_latency_ms=round(r["avg_latency_ms"], 1),
            )
            for r in trend_rows
        ]

        return GlobalStats(
            total_events=total,
            total_models=model_count,
            per_model=per_model,
            daily_trends=daily_trends,
        )
    finally:
        conn.close()


def export_events(format: str = "csv", window: str = "all") -> str:
    """Export usage events as CSV or JSON string."""
    conn = _connect()
    if conn is None:
        return ""

    try:
        wc = _window_clause(window)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM usage_events WHERE {wc} ORDER BY timestamp DESC"
        ).fetchall()
        data = [dict(r) for r in rows]

        if format == "json":
            import json
            return json.dumps(data, indent=2, ensure_ascii=False, default=str)

        # CSV
        if not data:
            return ""
        headers = list(data[0].keys())
        lines = [",".join(headers)]
        for d in data:
            lines.append(",".join(str(d.get(h, "")) for h in headers))
        return "\n".join(lines)
    finally:
        conn.close()


def trend_data() -> dict:
    """Return trend data ready for future charting (daily aggregates)."""
    stats = global_stats("all")
    return {
        "daily": [
            {
                "date": t.date,
                "requests": t.request_count,
                "total_tokens": t.total_tokens,
                "avg_latency_ms": t.avg_latency_ms,
            }
            for t in stats.daily_trends
        ],
        "per_model": [
            {
                "model": m.model,
                "requests": m.request_count,
                "total_tokens": m.total_tokens_observed,
                "avg_latency_ms": m.avg_latency_ms,
            }
            for m in stats.per_model
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4 — Limits Report
# ═══════════════════════════════════════════════════════════════════════════════

def limits_report_data() -> list[LimitRecord]:
    """Build limits report joining latest snapshots with inventory categories."""
    conn = _connect()
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT s.model_name, s.token_limit, s.req_limit,
                      COALESCE(i.category, 'UNKNOWN') AS category,
                      COALESCE(i.context_length, 0) AS context_length,
                      COALESCE(i.reasoning, 0) AS reasoning,
                      COALESCE(i.vision, 0) AS vision,
                      COALESCE(i.tool_calling, 0) AS tool_calling
               FROM rate_limit_snapshots s
               LEFT JOIN models_inventory i ON s.model_name = i.model_name
               WHERE s.token_limit IS NOT NULL
               AND s.id IN (SELECT MAX(id) FROM rate_limit_snapshots GROUP BY model_name)
               ORDER BY s.token_limit DESC NULLS LAST""",
        ).fetchall()

        results = []
        for r in rows:
            caps = []
            if r["reasoning"]: caps.append("R")
            if r["vision"]: caps.append("V")
            if r["tool_calling"]: caps.append("T")
            results.append(LimitRecord(
                model_name=r["model_name"],
                category=r["category"],
                token_limit=r["token_limit"] or 0,
                req_limit=r["req_limit"] or 0,
                context_length=r["context_length"] or 0,
                capabilities=",".join(caps) if caps else "—",
            ))
        return results
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 6 — v2 Enhanced Statistics
# ═══════════════════════════════════════════════════════════════════════════════

def per_model_stats_v2(model_name: Optional[str] = None) -> list[dict]:
    """Enhanced per-model stats from rate_limit_snapshots.
    Returns: [{model, test_count, avg_latency, min_latency, max_latency, avg_query_cost, total_query_cost}]
    """
    conn = _connect()
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        wheres = "WHERE success=1"
        params: list = []
        if model_name:
            wheres += " AND model_name=?"
            params.append(model_name)
        rows = conn.execute(
            f"""SELECT model_name,
                       COUNT(*) AS test_count,
                       COALESCE(AVG(latency_ms), 0) AS avg_latency,
                       COALESCE(MIN(latency_ms), 0) AS min_latency,
                       COALESCE(MAX(latency_ms), 0) AS max_latency,
                       COALESCE(AVG(query_cost), 0) AS avg_query_cost,
                       COALESCE(SUM(query_cost), 0) AS total_query_cost
                FROM rate_limit_snapshots {wheres}
                GROUP BY model_name
                ORDER BY test_count DESC""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def per_category_stats() -> list[CategoryStats]:
    """Aggregate stats by category from inventory + snapshots."""
    conn = _connect()
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT COALESCE(i.category, 'UNKNOWN') AS category,
                      COUNT(DISTINCT s.model_name) AS model_count,
                      COALESCE(AVG(s.token_limit), 0) AS avg_token_limit,
                      COALESCE(AVG(s.req_limit), 0) AS avg_req_limit,
                      COUNT(*) AS total_snapshots
               FROM rate_limit_snapshots s
               LEFT JOIN models_inventory i ON s.model_name = i.model_name
               WHERE s.success=1 AND s.token_limit IS NOT NULL
               GROUP BY category
               ORDER BY avg_token_limit DESC""",
        ).fetchall()
        return [
            CategoryStats(
                category=r["category"],
                model_count=r["model_count"],
                avg_token_limit=round(r["avg_token_limit"], 0),
                avg_req_limit=round(r["avg_req_limit"], 0),
                total_snapshots=r["total_snapshots"],
            )
            for r in rows
        ]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 7 — Export helpers
# ═══════════════════════════════════════════════════════════════════════════════

def export_inventory(format: str = "json") -> str:
    """Export models_inventory as CSV or JSON string."""
    conn = _connect()
    if conn is None:
        return ""
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM models_inventory ORDER BY category, model_name").fetchall()
        data = [dict(r) for r in rows]
        if format == "json":
            import json
            return json.dumps(data, indent=2, ensure_ascii=False, default=str)
        if not data:
            return ""
        headers = list(data[0].keys())
        lines = [",".join(headers)]
        for d in data:
            lines.append(",".join(str(d.get(h, "")) for h in headers))
        return "\n".join(lines)
    finally:
        conn.close()


def export_limits(format: str = "json") -> str:
    """Export latest limits per model as CSV or JSON string."""
    data = limits_report_data()
    if format == "json":
        import json
        return json.dumps(
            [{"model": r.model_name, "category": r.category, "token_limit": r.token_limit, "req_limit": r.req_limit, "context": r.context_length, "capabilities": r.capabilities} for r in data],
            indent=2,
            ensure_ascii=False,
        )
    if not data:
        return ""
    headers = ["model", "category", "token_limit", "req_limit", "context", "capabilities"]
    lines = [",".join(headers)]
    for r in data:
        lines.append(f"{r.model_name},{r.category},{r.token_limit},{r.req_limit},{r.context_length},{r.capabilities}")
    return "\n".join(lines)
