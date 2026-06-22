"""
database.py — SQLite persistence layer for Mistral Intelligence Monitor v2.
Auto-creates usage_history.db with schema, handles insert & query.

Tables:
  usage_events           — per-request telemetry (legacy, kept for compat)
  model_capabilities     — discovered capabilities (legacy, kept for compat)
  models_inventory       — model catalog with auto-classification (v2)
  rate_limit_snapshots   — historical rate-limit samples (v2)
  model_changes          — differential tracking of model metadata changes (v2)
  raw_headers            — full response headers for API forensics (v2)
"""

from __future__ import annotations

import json as _json
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("mistral_monitor.db")

DB_PATH = Path(__file__).resolve().parent / "usage_history.db"

SCHEMA_DDL = """
-- legacy tables (v1, kept for backward compat)
CREATE TABLE IF NOT EXISTS usage_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       DATETIME NOT NULL,
    model           TEXT NOT NULL,
    req_limit_minute        INTEGER,
    req_remaining_minute    INTEGER,
    token_limit_minute      INTEGER,
    token_remaining_minute  INTEGER,
    query_token_cost        INTEGER,
    latency_ms      INTEGER,
    success         BOOLEAN NOT NULL DEFAULT 1,
    extra_json      TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_events_model     ON usage_events(model);
CREATE INDEX IF NOT EXISTS idx_usage_events_timestamp ON usage_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_events_success   ON usage_events(success);

CREATE TABLE IF NOT EXISTS model_capabilities (
    model_id        TEXT PRIMARY KEY,
    name            TEXT,
    aliases_json    TEXT,
    max_context_length INTEGER,
    default_model_temperature REAL,
    type            TEXT,
    completion_chat            BOOLEAN DEFAULT 0,
    function_calling           BOOLEAN DEFAULT 0,
    reasoning                  BOOLEAN DEFAULT 0,
    completion_fim             BOOLEAN DEFAULT 0,
    fine_tuning                BOOLEAN DEFAULT 0,
    vision                     BOOLEAN DEFAULT 0,
    ocr                        BOOLEAN DEFAULT 0,
    classification             BOOLEAN DEFAULT 0,
    moderation                 BOOLEAN DEFAULT 0,
    audio                      BOOLEAN DEFAULT 0,
    audio_transcription        BOOLEAN DEFAULT 0,
    audio_transcription_realtime BOOLEAN DEFAULT 0,
    audio_speech               BOOLEAN DEFAULT 0,
    last_seen       DATETIME
);

-- ─── v2 tables ────────────────────────────────────────────────────────────

-- Phase 1: Model Inventory
CREATE TABLE IF NOT EXISTS models_inventory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name      TEXT UNIQUE NOT NULL,
    first_seen      DATETIME NOT NULL,
    last_seen       DATETIME NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    category        TEXT NOT NULL DEFAULT 'UNKNOWN',
    context_length  INTEGER,
    reasoning       BOOLEAN DEFAULT 0,
    vision          BOOLEAN DEFAULT 0,
    audio           BOOLEAN DEFAULT 0,
    ocr             BOOLEAN DEFAULT 0,
    tool_calling    BOOLEAN DEFAULT 0,
    fine_tuning     BOOLEAN DEFAULT 0,
    req_limit_min   INTEGER,
    token_limit_min INTEGER,
    notes           TEXT,
    -- v3: family normalization
    family_name     TEXT,
    version         TEXT,
    is_latest       BOOLEAN DEFAULT 0,
    -- v3: capability fingerprint
    capability_fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS idx_inventory_category  ON models_inventory(category);
CREATE INDEX IF NOT EXISTS idx_inventory_status    ON models_inventory(status);
-- idx_inventory_family created in _migrate_v3 after column exists

-- Phase 2: Rate Limit History
CREATE TABLE IF NOT EXISTS rate_limit_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       DATETIME NOT NULL,
    model_name      TEXT NOT NULL,
    req_limit       INTEGER,
    req_remaining   INTEGER,
    token_limit     INTEGER,
    token_remaining INTEGER,
    query_cost      INTEGER,
    latency_ms      INTEGER,
    success         BOOLEAN DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_snapshots_model     ON rate_limit_snapshots(model_name);
CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON rate_limit_snapshots(timestamp);

-- Phase 5: Differential Tracking
CREATE TABLE IF NOT EXISTS model_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   DATETIME NOT NULL,
    model_name  TEXT NOT NULL,
    field_name  TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT
);
CREATE INDEX IF NOT EXISTS idx_changes_model ON model_changes(model_name);
CREATE INDEX IF NOT EXISTS idx_changes_time  ON model_changes(timestamp);

-- Phase 8: API Forensics
CREATE TABLE IF NOT EXISTS raw_headers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   DATETIME NOT NULL,
    model_name  TEXT NOT NULL,
    headers_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_headers_time ON raw_headers(timestamp);
"""


def _connect() -> sqlite3.Connection:
    """Open (or create) the usage database and ensure schema is current."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_DDL)

    # ── v3 migration: add columns if missing (safe idempotent ALTER) ──
    _migrate_v3(conn)

    conn.commit()
    return conn


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """Add v3 columns to models_inventory if they don't exist (idempotent)."""
    v3_cols = {
        "family_name": "TEXT",
        "version": "TEXT",
        "is_latest": "BOOLEAN DEFAULT 0",
        "capability_fingerprint": "TEXT",
    }
    existing = {row[1] for row in conn.execute("PRAGMA table_info(models_inventory)")}
    for col_name, col_type in v3_cols.items():
        if col_name not in existing:
            conn.execute(f"ALTER TABLE models_inventory ADD COLUMN {col_name} {col_type}")
            logger.info("Migration v3: added column %s to models_inventory", col_name)

    # Create family index now that columns are guaranteed to exist
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inventory_family ON models_inventory(family_name)")


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Model Inventory
# ═══════════════════════════════════════════════════════════════════════════════

def upsert_inventory(
    model_name: str,
    *,
    category: str = "UNKNOWN",
    context_length: Optional[int] = None,
    reasoning: bool = False,
    vision: bool = False,
    audio: bool = False,
    ocr: bool = False,
    tool_calling: bool = False,
    fine_tuning: bool = False,
    req_limit_min: Optional[int] = None,
    token_limit_min: Optional[int] = None,
    notes: Optional[str] = None,
    # v3 fields
    family_name: Optional[str] = None,
    version: Optional[str] = None,
    is_latest: bool = False,
    capability_fingerprint: Optional[str] = None,
) -> None:
    """Insert or update a model in the inventory. Auto-detects first_seen vs last_seen."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id, first_seen, category, context_length, reasoning, vision, audio, ocr,"
            " tool_calling, fine_tuning, req_limit_min, token_limit_min "
            "FROM models_inventory WHERE model_name=?",
            (model_name,),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE models_inventory SET last_seen=?, category=?,
                   context_length=?, reasoning=?, vision=?, audio=?, ocr=?,
                   tool_calling=?, fine_tuning=?, req_limit_min=?, token_limit_min=?,
                   family_name=COALESCE(?, family_name),
                   version=COALESCE(?, version),
                   is_latest=?,
                   capability_fingerprint=COALESCE(?, capability_fingerprint),
                   notes=COALESCE(?, notes)
                   WHERE model_name=?""",
                (
                    now, category,
                    _coalesce_int(context_length, existing[3]),
                    reasoning or bool(existing[4]),
                    vision or bool(existing[5]),
                    audio or bool(existing[6]),
                    ocr or bool(existing[7]),
                    tool_calling or bool(existing[8]),
                    fine_tuning or bool(existing[9]),
                    _coalesce_int(req_limit_min, existing[10]),
                    _coalesce_int(token_limit_min, existing[11]),
                    family_name, version, is_latest,
                    capability_fingerprint,
                    notes,
                    model_name,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO models_inventory
                   (model_name, first_seen, last_seen, category,
                    context_length, reasoning, vision, audio, ocr,
                    tool_calling, fine_tuning, req_limit_min, token_limit_min,
                    family_name, version, is_latest, capability_fingerprint, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    model_name, now, now, category,
                    context_length, reasoning, vision, audio, ocr,
                    tool_calling, fine_tuning, req_limit_min, token_limit_min,
                    family_name, version, is_latest, capability_fingerprint, notes,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def query_inventory(category: Optional[str] = None, status: str = "active") -> list[dict]:
    """Return models from inventory, optionally filtered by category."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        wheres = ["status=?"]
        params = [status]
        if category:
            wheres.append("category=?")
            params.append(category)
        rows = conn.execute(
            f"SELECT * FROM models_inventory WHERE {' AND '.join(wheres)} ORDER BY category, model_name",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_inventory_snapshot() -> dict[str, dict]:
    """Return {model_name: {field: value}} for all active models."""
    rows = query_inventory()
    return {r["model_name"]: r for r in rows}


def mark_model_removed(model_name: str) -> None:
    """Mark a model as removed (was in inventory but no longer in API response)."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE models_inventory SET status='removed' WHERE model_name=?",
            (model_name,),
        )
        conn.commit()
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Rate Limit Snapshots
# ═══════════════════════════════════════════════════════════════════════════════

def insert_rate_limit_snapshot(
    model_name: str,
    *,
    req_limit: Optional[int] = None,
    req_remaining: Optional[int] = None,
    token_limit: Optional[int] = None,
    token_remaining: Optional[int] = None,
    query_cost: Optional[int] = None,
    latency_ms: Optional[int] = None,
    success: bool = True,
) -> int:
    """Insert a rate-limit snapshot. Returns row id."""
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO rate_limit_snapshots
               (timestamp, model_name, req_limit, req_remaining,
                token_limit, token_remaining, query_cost, latency_ms, success)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                model_name,
                req_limit, req_remaining,
                token_limit, token_remaining,
                query_cost, latency_ms,
                1 if success else 0,
            ),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def query_snapshots(
    model_name: Optional[str] = None,
    since_days: Optional[int] = None,
    limit: int = 100,
) -> list[dict]:
    """Retrieve rate-limit snapshots with optional filters."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        wheres = []
        params: list = []
        if model_name:
            wheres.append("model_name = ?")
            params.append(model_name)
        if since_days:
            wheres.append("timestamp >= datetime('now', ?)")
            params.append(f"-{since_days} days")
        where_clause = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        rows = conn.execute(
            f"SELECT * FROM rate_limit_snapshots {where_clause} ORDER BY timestamp DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def latest_limits_per_model() -> list[dict]:
    """Return the most recent snapshot for each active model."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT s.* FROM rate_limit_snapshots s
               JOIN (SELECT model_name, MAX(timestamp) AS maxt FROM rate_limit_snapshots GROUP BY model_name) sub
               ON s.model_name = sub.model_name AND s.timestamp = sub.maxt
               ORDER BY s.token_limit DESC NULLS LAST""",
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 5 — Differential Tracking
# ═══════════════════════════════════════════════════════════════════════════════

def record_change(model_name: str, field_name: str, old_value: Optional[str], new_value: Optional[str]) -> None:
    """Record a metadata change if old != new."""
    if old_value == new_value:
        return
    if old_value is None and new_value is None:
        return
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO model_changes (timestamp, model_name, field_name, old_value, new_value)
               VALUES (?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                model_name,
                field_name,
                str(old_value) if old_value is not None else None,
                str(new_value) if new_value is not None else None,
            ),
        )
        conn.commit()
        logger.info("CHANGE: %s.%s: %s → %s", model_name, field_name, old_value, new_value)
    finally:
        conn.close()


def query_changes(since_days: int = 7) -> list[dict]:
    """Return recent model changes."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM model_changes WHERE timestamp >= datetime('now', ?) ORDER BY timestamp DESC",
            (f"-{since_days} days",),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 8 — API Forensics
# ═══════════════════════════════════════════════════════════════════════════════

def save_raw_headers(model_name: str, headers: dict) -> None:
    """Save complete response headers as JSON for forensic analysis."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO raw_headers (timestamp, model_name, headers_json) VALUES (?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                model_name,
                _json.dumps({k: v for k, v in headers.items()}, ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def query_raw_headers(since_days: int = 30) -> list[dict]:
    """Return recent raw headers for analysis."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM raw_headers WHERE timestamp >= datetime('now', ?) ORDER BY timestamp DESC LIMIT 200",
            (f"-{since_days} days",),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["headers_parsed"] = _json.loads(d["headers_json"])
            except (json.JSONDecodeError, TypeError):
                d["headers_parsed"] = {}
            results.append(d)
        return results
    finally:
        conn.close()


def discover_new_headers() -> list[str]:
    """Find header keys not in the known set — potential new Mistral headers."""
    known = {
        "x-ratelimit-limit-req-minute", "x-ratelimit-remaining-req-minute",
        "x-ratelimit-limit-tokens-minute", "x-ratelimit-remaining-tokens-minute",
        "x-ratelimit-tokens-query-cost", "retry-after",
        "content-type", "content-length", "date", "server", "connection",
        "access-control-allow-origin", "access-control-allow-methods",
        "access-control-allow-headers", "strict-transport-security",
        "x-request-id", "x-kong-proxy-latency", "x-kong-upstream-latency",
        "via", "cf-cache-status", "cf-ray", "set-cookie",
    }
    raw = query_raw_headers(since_days=30)
    all_keys: set[str] = set()
    for r in raw:
        parsed = r.get("headers_parsed", {})
        all_keys.update(k.lower() for k in parsed)
    return sorted(all_keys - known)


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 9 — Anomaly Detection helpers
# ═══════════════════════════════════════════════════════════════════════════════

def detect_anomalies(api_model_names: set[str]) -> dict:
    """Compare inventory vs current API response. Return added/removed/changed models."""
    inventory = get_inventory_snapshot()
    inv_names = set(inventory.keys())

    added = api_model_names - inv_names
    removed = inv_names - api_model_names

    changed = []
    for name in inv_names & api_model_names:
        # The actual comparison happens in monitor.py during discovery
        # Here we just flag models that had recent changes
        recent = query_changes(since_days=1)
        if any(c["model_name"] == name for c in recent):
            changed.append(name)

    return {
        "added": sorted(added),
        "removed": sorted(removed),
        "changed": sorted(changed),
        "total_active": len(api_model_names),
        "total_known": len(inv_names),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# v3 — Family Analytics & Duplicate Detection
# ═══════════════════════════════════════════════════════════════════════════════

def query_families() -> list[dict]:
    """Return aggregated family report: one row per family_name."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT family_name,
                      COUNT(*) AS version_count,
                      MAX(CASE WHEN is_latest THEN model_name END) AS latest_model,
                      MAX(context_length) AS max_context,
                      GROUP_CONCAT(DISTINCT capability_fingerprint) AS fingerprints,
                      MAX(category) AS category,
                      MAX(token_limit_min) AS max_tok_min,
                      MAX(req_limit_min) AS max_req_min
               FROM models_inventory
               WHERE status='active' AND family_name IS NOT NULL AND family_name != ''
               GROUP BY family_name
               ORDER BY version_count DESC, family_name""",
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_duplicates() -> list[dict]:
    """Detect alias/duplicate models by comparing fingerprints and family groupings."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT a.model_name AS canonical,
                      b.model_name AS alias,
                      a.capability_fingerprint AS fingerprint,
                      CASE WHEN a.is_latest THEN 'latest' ELSE b.version END AS alias_status
               FROM models_inventory a
               JOIN models_inventory b
                 ON a.family_name = b.family_name
                AND a.model_name != b.model_name
                AND a.capability_fingerprint = b.capability_fingerprint
               WHERE a.status='active' AND b.status='active'
                 AND a.family_name IS NOT NULL
                 AND (a.is_latest OR b.is_latest OR a.model_name < b.model_name)
               GROUP BY a.model_name, b.model_name
               ORDER BY a.family_name, a.is_latest DESC, a.model_name""",
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_family_stats() -> list[dict]:
    """Per-family aggregate statistics for infrastructure analytics."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT i.family_name,
                      COUNT(DISTINCT i.model_name) AS version_count,
                      MAX(i.token_limit_min) AS max_token_limit,
                      MAX(i.req_limit_min) AS max_req_limit,
                      MAX(i.context_length) AS max_context,
                      GROUP_CONCAT(DISTINCT i.capability_fingerprint) AS fingerprints,
                      MAX(i.category) AS category,
                      COUNT(DISTINCT s.id) AS snapshot_count,
                      COALESCE(AVG(s.latency_ms), 0) AS avg_latency
               FROM models_inventory i
               LEFT JOIN rate_limit_snapshots s ON i.model_name = s.model_name AND s.success=1
               WHERE i.status='active' AND i.family_name IS NOT NULL AND i.family_name != ''
               GROUP BY i.family_name
               ORDER BY max_token_limit DESC NULLS LAST""",
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Legacy functions (kept for backward compat with v1 monitor.py calls)
# ═══════════════════════════════════════════════════════════════════════════════

def insert_event(
    model: str,
    *,
    req_limit_minute: Optional[int] = None,
    req_remaining_minute: Optional[int] = None,
    token_limit_minute: Optional[int] = None,
    token_remaining_minute: Optional[int] = None,
    query_token_cost: Optional[int] = None,
    latency_ms: Optional[int] = None,
    success: bool = True,
    extra: Optional[str] = None,
) -> int:
    """Insert a usage event row (legacy table). Also snapshots to rate_limit_snapshots."""
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO usage_events
               (timestamp, model, req_limit_minute, req_remaining_minute,
                token_limit_minute, token_remaining_minute,
                query_token_cost, latency_ms, success, extra_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                model,
                req_limit_minute,
                req_remaining_minute,
                token_limit_minute,
                token_remaining_minute,
                query_token_cost,
                latency_ms,
                1 if success else 0,
                extra,
            ),
        )
        conn.commit()
        row_id = cur.lastrowid or 0
    finally:
        conn.close()

    # Also write to v2 rate_limit_snapshots (dual-write for smooth migration)
    insert_rate_limit_snapshot(
        model_name=model,
        req_limit=req_limit_minute,
        req_remaining=req_remaining_minute,
        token_limit=token_limit_minute,
        token_remaining=token_remaining_minute,
        query_cost=query_token_cost,
        latency_ms=latency_ms,
        success=success,
    )

    return row_id


def upsert_model_capability(model_id: str, capabilities: dict) -> None:
    """Insert or update a model's capability record (legacy)."""
    conn = _connect()
    try:
        caps = {
            "completion_chat": 1 if capabilities.get("completion_chat") else 0,
            "function_calling": 1 if capabilities.get("function_calling") else 0,
            "reasoning": 1 if capabilities.get("reasoning") else 0,
            "completion_fim": 1 if capabilities.get("completion_fim") else 0,
            "fine_tuning": 1 if capabilities.get("fine_tuning") else 0,
            "vision": 1 if capabilities.get("vision") else 0,
            "ocr": 1 if capabilities.get("ocr") else 0,
            "classification": 1 if capabilities.get("classification") else 0,
            "moderation": 1 if capabilities.get("moderation") else 0,
            "audio": 1 if capabilities.get("audio") else 0,
            "audio_transcription": 1 if capabilities.get("audio_transcription") else 0,
            "audio_transcription_realtime": 1 if capabilities.get("audio_transcription_realtime") else 0,
            "audio_speech": 1 if capabilities.get("audio_speech") else 0,
        }
        conn.execute(
            """INSERT OR REPLACE INTO model_capabilities
               (model_id, name, aliases_json, max_context_length,
                default_model_temperature, type,
                completion_chat, function_calling, reasoning,
                completion_fim, fine_tuning, vision, ocr,
                classification, moderation, audio,
                audio_transcription, audio_transcription_realtime,
                audio_speech, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                model_id,
                capabilities.get("name", model_id),
                capabilities.get("aliases_json"),
                capabilities.get("max_context_length"),
                capabilities.get("default_model_temperature"),
                capabilities.get("type"),
                caps["completion_chat"],
                caps["function_calling"],
                caps["reasoning"],
                caps["completion_fim"],
                caps["fine_tuning"],
                caps["vision"],
                caps["ocr"],
                caps["classification"],
                caps["moderation"],
                caps["audio"],
                caps["audio_transcription"],
                caps["audio_transcription_realtime"],
                caps["audio_speech"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def query_events(
    model: Optional[str] = None,
    since_days: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Retrieve usage events with optional filters (legacy)."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        wheres = []
        params: list = []
        if model:
            wheres.append("model = ?")
            params.append(model)
        if since_days:
            wheres.append("timestamp >= datetime('now', ?)")
            params.append(f"-{since_days} days")
        where_clause = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        rows = conn.execute(
            f"SELECT * FROM usage_events {where_clause} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_model_capabilities() -> list[dict]:
    """Return all known model capabilities (legacy)."""
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM model_capabilities ORDER BY model_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── internal helpers ────────────────────────────────────────────────────────

def _coalesce_int(new_val: Optional[int], old_val) -> Optional[int]:
    """Return new_val if not None, else old_val cast to int or None."""
    if new_val is not None:
        return new_val
    if old_val is not None:
        try:
            return int(old_val)
        except (ValueError, TypeError):
            return None
    return None
