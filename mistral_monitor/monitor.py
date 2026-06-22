#!/usr/bin/env python3
"""
monitor.py — Mistral Realtime Usage & Capability Monitor
=========================================================
Real-time rate-limit monitoring, SQLite usage history persistence,
model capability discovery, aggregate statistics and trend analysis.

Dependencies: requests, rich, sqlite3 (stdlib).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.layout import Layout
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn

# ─── Internal modules ────────────────────────────────────────────────────────

from database import (
    insert_event, query_events,
    upsert_model_capability, query_model_capabilities,
    upsert_inventory, query_inventory, get_inventory_snapshot,
    mark_model_removed, record_change, query_changes,
    insert_rate_limit_snapshot, latest_limits_per_model,
    save_raw_headers, discover_new_headers, detect_anomalies,
    query_families, query_duplicates, query_family_stats,
)
from stats import (
    global_stats, stats_for_model, export_events, trend_data,
    limits_report_data, per_model_stats_v2, per_category_stats,
    export_inventory, export_limits,
)
from classifier import (
    classify_model, CATEGORY_DESCRIPTIONS, CATEGORY_COLORS,
    parse_family, capability_fingerprint,
)

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mistral_monitor")

console = Console()

# ─── Config ──────────────────────────────────────────────────────────────────

MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
DEFAULT_MODEL = "mistral-medium-latest"
TEST_MESSAGE = "Is Paris in France?"
MAX_RETRIES = 3

# ─── .env loader ─────────────────────────────────────────────────────────────

def _load_dotenv(path: str | None = None) -> None:
    env_path = Path(path) if path else Path(__file__).parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = val

_load_dotenv()

# ─── API helpers ─────────────────────────────────────────────────────────────

def _api_key() -> str:
    key = os.getenv("MISTRAL_API_KEY")
    if not key:
        console.print(Panel(
            "[bold red]MISTRAL_API_KEY not found![/bold red]\n"
            "Set the environment variable o crea un file .env.",
            title="Error",
            border_style="red",
        ))
        sys.exit(1)
    return key


def _get_endpoint(model_id: str, is_probe: bool = False) -> tuple[str, dict]:
    m = model_id.lower()
    msg = "OK" if is_probe else TEST_MESSAGE
    if "embed" in m:
        return f"{MISTRAL_BASE_URL}/embeddings", {"model": model_id, "input": [msg]}
    if "moderation" in m:
        return f"{MISTRAL_BASE_URL}/moderations", {"model": model_id, "input": msg}
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": msg}],
        "max_tokens": 1 if is_probe else 256,
    }
    if not is_probe:
        payload["temperature"] = 0.2
    return f"{MISTRAL_BASE_URL}/chat/completions", payload


# ─── Rate-limit parser (robust, nullable) ────────────────────────────────────

def _safe_int(h: dict, key: str) -> Optional[int]:
    val = h.get(key)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        log.debug("Cannot parse %s=%r as int", key, val)
        return None


def _parse_rate_limits(headers: dict) -> dict:
    """Extract known rate-limit headers. All nullable."""
    hlow = {k.lower(): v for k, v in headers.items()}
    for k, v in hlow.items():
        log.debug("header: %s = %s", k, v)
    return {
        "req_limit":          _safe_int(hlow, "x-ratelimit-limit-req-minute"),
        "req_remaining":      _safe_int(hlow, "x-ratelimit-remaining-req-minute"),
        "token_limit":        _safe_int(hlow, "x-ratelimit-limit-tokens-minute"),
        "token_remaining":    _safe_int(hlow, "x-ratelimit-remaining-tokens-minute"),
        "query_token_cost":   _safe_int(hlow, "x-ratelimit-tokens-query-cost"),
    }


def _ratio_str(used: Optional[int], limit: Optional[int]) -> str:
    if used is None or limit is None or limit == 0:
        return "—"
    pct = (used / limit) * 100
    color = "[bold red]" if pct >= 90 else "[bold yellow]" if pct >= 70 else "[green]"
    return f"{color}{pct:.1f}%[/]"


# ─── Capability discovery ────────────────────────────────────────────────────

CAPABILITY_FIELDS = [
    "completion_chat",
    "function_calling",
    "reasoning",
    "completion_fim",
    "fine_tuning",
    "vision",
    "ocr",
    "classification",
    "moderation",
    "audio",
    "audio_transcription",
    "audio_transcription_realtime",
    "audio_speech",
]


def discover_models(api_key: str) -> list[dict]:
    """Fetch /v1/models, extract capabilities, persist to DB, return enriched list.
    Phase 1+3+5: builds inventory, classifies, detects changes."""
    log.info("GET %s/models", MISTRAL_BASE_URL)
    resp = requests.get(
        f"{MISTRAL_BASE_URL}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    if resp.status_code != 200:
        console.print(f"[bold red]HTTP {resp.status_code}[/bold red] — {resp.text[:300]}")
        sys.exit(1)

    raw = resp.json().get("data", [])
    enriched = []

    # Get existing inventory for differential tracking
    old_inv = get_inventory_snapshot()
    api_model_names = {m.get("id", "?") for m in raw}

    for m in raw:
        caps = m.get("capabilities", {})
        if isinstance(caps, dict):
            cap_bools = {k: bool(caps.get(k)) for k in CAPABILITY_FIELDS}
        else:
            cap_bools = {k: False for k in CAPABILITY_FIELDS}

        model_id = m.get("id", "?")
        entry = {
            "id": model_id,
            "name": m.get("name", m.get("id", "?")),
            "aliases": m.get("aliases", []),
            "max_context_length": m.get("max_context_length"),
            "default_model_temperature": m.get("default_model_temperature"),
            "type": m.get("type", ""),
            "capabilities": caps,
        }
        enriched.append(entry)

        # Phase 3: classify
        category = classify_model(model_id, caps)

        # v3: family normalization & capability fingerprint
        family_name, version, is_latest = parse_family(model_id)
        fp = capability_fingerprint(caps)

        # Phase 1: upsert inventory (with v3 fields)
        upsert_inventory(
            model_name=model_id,
            category=category,
            context_length=m.get("max_context_length"),
            reasoning=cap_bools.get("reasoning", False),
            vision=cap_bools.get("vision", False),
            audio=cap_bools.get("audio", False) or cap_bools.get("audio_transcription", False),
            ocr=cap_bools.get("ocr", False),
            tool_calling=cap_bools.get("function_calling", False),
            fine_tuning=cap_bools.get("fine_tuning", False),
            family_name=family_name,
            version=version,
            is_latest=is_latest,
            capability_fingerprint=fp,
        )

        # Phase 5: differential tracking
        old = old_inv.get(model_id, {})
        for field, new_val in [
            ("category", category),
            ("context_length", str(m.get("max_context_length", ""))),
            ("reasoning", str(cap_bools.get("reasoning", False))),
            ("vision", str(cap_bools.get("vision", False))),
        ]:
            old_val = str(old.get(field, "")) if old else ""
            if old_val and old_val != new_val:
                record_change(model_id, field, old_val, new_val)

        # Legacy: persist capabilities
        upsert_model_capability(
            entry["id"],
            {
                "name": entry["name"],
                "aliases_json": json.dumps(entry["aliases"], ensure_ascii=False),
                "max_context_length": entry["max_context_length"],
                "default_model_temperature": entry["default_model_temperature"],
                "type": entry["type"],
                **cap_bools,
            },
        )

    # Phase 9: mark removed models
    for old_name in set(old_inv.keys()) - api_model_names:
        if old_inv.get(old_name, {}).get("status") == "active":
            mark_model_removed(old_name)
            record_change(old_name, "status", "active", "removed")
            console.print(f"  [bold red]REMOVED:[/bold red] {old_name}")

    log.info("Discovered %d models", len(enriched))
    return enriched


def print_model_table(models: list[dict]):
    """Rich table: model id, name, type, context, cap summary."""
    table = Table(title="Mistral Model Catalog", box=box.ROUNDED, border_style="cyan", show_lines=True)
    table.add_column("Model ID", style="bold white", min_width=28)
    table.add_column("Type", style="blue")
    table.add_column("Context", justify="right")
    table.add_column("Temp", justify="right")
    table.add_column("Chat", justify="center")
    table.add_column("Vision", justify="center")
    table.add_column("FC", justify="center")
    table.add_column("Reasoning", justify="center")
    table.add_column("OCR", justify="center")
    table.add_column("Audio", justify="center")

    for m in models:
        caps = m.get("capabilities", {})
        ctx = str(m.get("max_context_length") or "—")
        temp = f"{m.get('default_model_temperature', 0):.1f}" if m.get("default_model_temperature") is not None else "—"
        table.add_row(
            m["id"],
            m.get("type", "—"),
            ctx,
            temp,
            "✓" if caps.get("completion_chat") else "",
            "✓" if caps.get("vision") else "",
            "✓" if caps.get("function_calling") else "",
            "✓" if caps.get("reasoning") else "",
            "✓" if caps.get("ocr") else "",
            "✓" if caps.get("audio") or caps.get("audio_transcription") or caps.get("audio_speech") else "",
        )
    console.print(table)


# ─── Rate-limit display ──────────────────────────────────────────────────────

def print_rate_limits(rl: dict, title: str = "Rate Limits (per-minute)"):
    """Rich panel with rate-limit gauges."""
    table = Table(title=title, box=box.SIMPLE, border_style="cyan")
    table.add_column("Metric", style="bold white", min_width=18)
    table.add_column("Limit", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("Used %", justify="right", min_width=12)

    for label, lk, rk in [
        ("Requests/min", "req_limit", "req_remaining"),
        ("Tokens/min", "token_limit", "token_remaining"),
    ]:
        lim = rl.get(lk)
        rem = rl.get(rk)
        used = (lim - rem) if (lim is not None and rem is not None) else None
        limit_str = str(lim) if lim is not None else "—"
        rem_str = str(rem) if rem is not None else "—"
        ratio = _ratio_str(used, lim)
        table.add_row(label, limit_str, rem_str, ratio)

    cost = rl.get("query_token_cost")
    if cost is not None:
        table.add_row("Query cost (tok)", str(cost), "", "")

    console.print(table)


# ─── Statistics display ──────────────────────────────────────────────────────

def print_stats(window: str = "all"):
    gs = global_stats(window)
    if gs.total_events == 0:
        console.print("[yellow]No data in history.[/yellow]")
        return

    console.print(Panel.fit(
        f"[bold]Events:[/bold] {gs.total_events}  |  [bold]Models:[/bold] {gs.total_models}  |  [bold]Window:[/bold] {window}",
        title="Global Stats",
        border_style="green",
    ))

    for ms in gs.per_model:
        table = Table(title=f"{ms.model}", box=box.SIMPLE, border_style="blue")
        table.add_column("Metric", style="bold white")
        table.add_column("Value", justify="right")
        table.add_row("Requests", str(ms.request_count))
        table.add_row("Success / Fail", f"[green]{ms.success_count}[/green] / [red]{ms.failure_count}[/red]")
        table.add_row("Total tokens obs", f"{ms.total_tokens_observed:,}")
        table.add_row("Avg tokens/req", f"{ms.avg_tokens_per_request:.1f}")
        table.add_row("Avg query cost (tok)", f"{ms.avg_query_cost:.1f}")
        table.add_row("Avg latency", f"{ms.avg_latency_ms:.0f} ms")
        table.add_row("Min / Max latency", f"{ms.min_latency_ms} / {ms.max_latency_ms} ms")
        console.print(table)
        console.print()


def print_history(limit: int = 20):
    events = query_events(limit=limit)
    if not events:
        console.print("[yellow]No events in history.[/yellow]")
        return

    table = Table(title=f"Last {len(events)} Requests", box=box.SIMPLE, border_style="cyan")
    table.add_column("Time", style="dim")
    table.add_column("Model", style="bold")
    table.add_column("Success", justify="center")
    table.add_column("Latency", justify="right")
    table.add_column("Tok cost", justify="right")
    table.add_column("Req/min", justify="right")

    for e in events:
        ts = e.get("timestamp", "")[-8:] if e.get("timestamp") else "?"
        table.add_row(
            ts,
            e.get("model", "?"),
            "[green]✓[/green]" if e.get("success") else "[red]✗[/red]",
            f"{e.get('latency_ms')} ms" if e.get("latency_ms") else "—",
            str(e.get("query_token_cost") or "—"),
            f"{e.get('req_remaining_minute')}/{e.get('req_limit_minute')}" if e.get("req_limit_minute") else "—",
        )
    console.print(table)


def print_trends():
    td = trend_data()
    if not td["daily"]:
        console.print("[yellow]No trend data available.[/yellow]")
        return

    console.print(Panel("[bold]Daily Trends (data ready for charting)[/bold]", border_style="magenta"))

    table = Table(box=box.SIMPLE, border_style="cyan")
    table.add_column("Date", style="bold white")
    table.add_column("Requests", justify="right")
    table.add_column("Total Tokens", justify="right")
    table.add_column("Avg Latency", justify="right")

    for d in td["daily"][:30]:  # last 30 days
        table.add_row(
            d["date"] or "?",
            str(d["requests"]),
            f"{d['total_tokens']:,}",
            f"{d['avg_latency_ms']:.0f} ms",
        )
    console.print(table)

    console.print("\n[bold]Per-model summary:[/bold]")
    for m in td["per_model"]:
        console.print(f"  [bold]{m['model']}[/bold]: {m['requests']} req, {m['total_tokens']:,} tok, {m['avg_latency_ms']:.0f} ms avg")


# ─── Phase 4 — Limits Report ────────────────────────────────────────────────

def print_limits_report():
    """Display limits report sorted by token/min descending."""
    data = limits_report_data()
    if not data:
        console.print("[yellow]No limit data available. Run --test-all first.[/yellow]")
        return

    table = Table(title="Mistral Rate Limits Report (per-minute)", box=box.ROUNDED, border_style="cyan", show_lines=True)
    table.add_column("Model", style="bold white", min_width=26)
    table.add_column("Category", style="dim")
    table.add_column("Tok/min", justify="right")
    table.add_column("Req/min", justify="right")
    table.add_column("Context", justify="right")
    table.add_column("Caps")

    for r in data:
        cat_color = CATEGORY_COLORS.get(r.category, "white")
        tok_str = f"{r.token_limit:,}" if r.token_limit > 0 else "—"
        req_str = f"{r.req_limit:,}" if r.req_limit > 0 else "—"
        table.add_row(
            r.model_name,
            f"[{cat_color}]{r.category}[/{cat_color}]",
            tok_str,
            req_str,
            f"{r.context_length:,}" if r.context_length else "—",
            r.capabilities,
        )
    console.print(table)

    # Category summary
    cat_data = per_category_stats()
    if cat_data:
        console.print()
        ctable = Table(title="Per-Category Averages", box=box.SIMPLE, border_style="green")
        ctable.add_column("Category", style="bold")
        ctable.add_column("Models", justify="right")
        ctable.add_column("Avg Tok/min", justify="right")
        ctable.add_column("Avg Req/min", justify="right")
        for c in cat_data:
            cat_color = CATEGORY_COLORS.get(c.category, "white")
            ctable.add_row(
                f"[{cat_color}]{c.category}[/{cat_color}]",
                str(c.model_count),
                f"{c.avg_token_limit:,.0f}",
                f"{c.avg_req_limit:,.0f}",
            )
        console.print(ctable)


# ─── Phase 9 — Anomaly Detection ─────────────────────────────────────────────

def print_watch_report():
    """Model Watch Report: added/removed/changed models."""
    console.print(Panel("[bold white]Model Watch Report[/bold white]", border_style="magenta"))

    changes = query_changes(since_days=7)
    inventory = query_inventory()

    active = [m for m in inventory if m.get("status") == "active"]
    removed = [m for m in inventory if m.get("status") == "removed"]

    # Summary
    console.print(f"\n[bold]Inventory:[/bold] {len(active)} active, {len(removed)} removed, {len(changes)} changes (7d)")

    if removed:
        console.print("\n[bold red]Recently Removed:[/bold red]")
        for m in removed[:10]:
            console.print(f"  • {m['model_name']} (last seen: {m.get('last_seen', '?')})")

    if changes:
        console.print("\n[bold yellow]Recent Changes:[/bold yellow]")
        ctable = Table(box=box.SIMPLE, border_style="yellow")
        ctable.add_column("Model", style="bold")
        ctable.add_column("Field")
        ctable.add_column("Old → New")
        for c in changes[:30]:
            ctable.add_row(
                c["model_name"],
                c["field_name"],
                f"[red]{c.get('old_value','?')}[/red] → [green]{c.get('new_value','?')}[/green]",
            )
        console.print(ctable)

    # New headers discovery (Phase 8 forensic analysis)
    new_hdrs = discover_new_headers()
    if new_hdrs:
        console.print(f"\n[bold cyan]Potential New Headers Detected:[/bold cyan]")
        for h in new_hdrs:
            console.print(f"  • {h}")


# ─── Phase 6 — v2 Enhanced Stats ─────────────────────────────────────────────

def print_per_model_stats():
    """Show enhanced per-model statistics."""
    data = per_model_stats_v2()
    if not data:
        console.print("[yellow]No statistical data available.[/yellow]")
        return

    table = Table(title="Per-Model Statistics", box=box.ROUNDED, border_style="cyan", show_lines=True)
    table.add_column("Model", style="bold white", min_width=26)
    table.add_column("Tests", justify="right")
    table.add_column("Avg Lat", justify="right")
    table.add_column("Min/Max Lat", justify="right")
    table.add_column("Avg Cost", justify="right")
    table.add_column("Total Cost", justify="right")

    for d in data:
        table.add_row(
            d["model_name"],
            str(d["test_count"]),
            f"{d['avg_latency']:.0f} ms",
            f"{d['min_latency']:.0f}/{d['max_latency']:.0f} ms",
            f"{d['avg_query_cost']:.0f}",
            f"{d['total_query_cost']:,}",
        )
    console.print(table)


# ─── Inference engine ────────────────────────────────────────────────────────

def run_inference(model: str, api_key: str) -> dict:
    url, payload = _get_endpoint(model)
    log.info("POST %s — model=%s", url, model)

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.perf_counter()
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=60,
            )
        except requests.RequestException as e:
            log.error("Request failed: %s", e)
            console.print(f"[bold red]Richiesta fallita: {e}[/bold red]")
            sys.exit(1)

        latency_ms = int((time.perf_counter() - start) * 1000)
        rl = _parse_rate_limits(dict(resp.headers))

        # Phase 8: save raw headers for forensics
        save_raw_headers(model, dict(resp.headers))

        if resp.status_code == 200:
            log.info("Inference OK — latency=%dms", latency_ms)
            data = resp.json()
            data["_meta"] = {"latency_ms": latency_ms, "rate_limits": rl}
            return data

        if resp.status_code == 429:
            wait_s = float(resp.headers.get("retry-after", 2 ** attempt))
            log.warning("Rate-limit (429). Waiting %.0fs...", wait_s)
            time.sleep(wait_s)
            continue

        log.error("HTTP %d — %s", resp.status_code, resp.text[:400])
        console.print(f"[bold red]HTTP {resp.status_code}[/bold red]")
        # record failed event
        insert_event(
            model=model,
            req_limit_minute=rl.get("req_limit"),
            req_remaining_minute=rl.get("req_remaining"),
            token_limit_minute=rl.get("token_limit"),
            token_remaining_minute=rl.get("token_remaining"),
            query_token_cost=rl.get("query_token_cost"),
            latency_ms=latency_ms,
            success=False,
        )
        sys.exit(1)

    console.print("[bold red]Tentativi esauriti.[/bold red]")
    sys.exit(1)


def show_result(result: dict, model: str):
    meta = result.get("_meta", {})
    latency_ms = meta.get("latency_ms", 0)
    rl = meta.get("rate_limits", {})

    # Persist
    insert_event(
        model=model,
        req_limit_minute=rl.get("req_limit"),
        req_remaining_minute=rl.get("req_remaining"),
        token_limit_minute=rl.get("token_limit"),
        token_remaining_minute=rl.get("token_remaining"),
        query_token_cost=rl.get("query_token_cost"),
        latency_ms=latency_ms,
        success=True,
    )

    # Display rate limits
    print_rate_limits(rl)

    # Display response
    data_items = result.get("data", [])
    if data_items and isinstance(data_items, list) and len(data_items) > 0 and "embedding" in data_items[0]:
        emb = data_items[0]["embedding"]
        console.print(Panel(f"Dimensione: {len(emb)}\nPrimi 5: {emb[:5]}...", title="Embedding", border_style="blue"))
    elif "results" in result:
        console.print(Panel(json.dumps(result["results"], indent=2, ensure_ascii=False), title="Moderazione", border_style="magenta"))
    else:
        for choice in result.get("choices", []):
            content = choice.get("message", {}).get("content", "")
            console.print(Panel(content, title="Risposta Chat", border_style="green"))

    console.print(f"\n[dim]Latency: {latency_ms} ms[/dim]")


# ─── --test-all ──────────────────────────────────────────────────────────────

def test_all(api_key: str, models: list[dict]):
    skip = {"ocr", "transcribe", "voxtral"}
    candidates = [m for m in models if not any(k in m.get("id", "").lower() for k in skip)]
    candidates.sort(key=lambda x: x.get("id", ""))

    console.print(f"\n[bold]Probing {len(candidates)} models... (inventory + limits)[/bold]\n")

    # Phase 1: build inventory from model list
    for m in models:
        mid = m.get("id", "?")
        caps = m.get("capabilities", {})
        category = classify_model(mid, caps)
        family_name, version, is_latest = parse_family(mid)
        fp = capability_fingerprint(caps)
        upsert_inventory(
            model_name=mid,
            category=category,
            context_length=m.get("max_context_length"),
            reasoning=bool(caps.get("reasoning")),
            vision=bool(caps.get("vision")),
            audio=bool(caps.get("audio") or caps.get("audio_transcription")),
            ocr=bool(caps.get("ocr")),
            tool_calling=bool(caps.get("function_calling")),
            fine_tuning=bool(caps.get("fine_tuning")),
            family_name=family_name,
            version=version,
            is_latest=is_latest,
            capability_fingerprint=fp,
        )

    table = Table(title="Per-minute quota per model", box=box.ROUNDED, border_style="cyan")
    table.add_column("#", justify="right")
    table.add_column("Model", style="bold")
    table.add_column("Tok limit", justify="right")
    table.add_column("Tok used", justify="right")
    table.add_column("Used %", justify="right")
    table.add_column("Status")

    for idx, m in enumerate(candidates, 1):
        mid = m.get("id", "?")
        url, payload = _get_endpoint(mid, is_probe=True)

        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=30,
            )
        except requests.RequestException:
            table.add_row(str(idx), mid, "—", "—", "—", "[red]net err[/red]")
            continue

        rl = _parse_rate_limits(dict(r.headers))
        lim = rl.get("token_limit")
        rem = rl.get("token_remaining")
        used = (lim - rem) if (lim is not None and rem is not None) else None
        pct_str = _ratio_str(used, lim)

        # Phase 1: update inventory with observed limits
        upsert_inventory(
            model_name=mid,
            req_limit_min=rl.get("req_limit"),
            token_limit_min=rl.get("token_limit"),
        )

        status = "[green]OK[/green]" if r.status_code == 200 else f"[red]{r.status_code}[/red]"
        table.add_row(str(idx), mid, str(lim or "—"), str(used or "—"), pct_str, status)

        insert_event(
            model=mid,
            req_limit_minute=rl.get("req_limit"),
            req_remaining_minute=rl.get("req_remaining"),
            token_limit_minute=rl.get("token_limit"),
            token_remaining_minute=rl.get("token_remaining"),
            query_token_cost=rl.get("query_token_cost"),
            latency_ms=0,
            success=(r.status_code == 200),
        )

        if r.status_code == 429:
            time.sleep(float(r.headers.get("retry-after", 5)))
        else:
            time.sleep(0.5)

    console.print(table)


# ─── v3 — Family Analytics ─────────────────────────────────────────────────

def print_duplicates():
    """Detect and display alias/duplicate models."""
    dups = query_duplicates()
    if not dups:
        console.print("[green]No duplicates detected among active models.[/green]")
        return

    table = Table(title="Duplicate & Alias Detection", box=box.ROUNDED, border_style="yellow", show_lines=True)
    table.add_column("Canonical", style="bold green", min_width=26)
    table.add_column("Alias / Duplicate", style="dim", min_width=26)
    table.add_column("Fingerprint", style="cyan")
    table.add_column("Status")

    for d in dups:
        canonical = d.get("canonical", "?")
        alias = d.get("alias", "?")
        fp = d.get("fingerprint", "—")
        status = d.get("alias_status", "—")
        table.add_row(canonical, alias, fp, status)
    console.print(table)
    console.print(f"[dim]{len(dups)} alias/duplicate pairs found.[/dim]")


def print_families():
    """Display aggregated family report."""
    families = query_families()
    if not families:
        console.print("[yellow]No families found. Run --models or --test-all first.[/yellow]")
        return

    table = Table(title="Model Family Report", box=box.ROUNDED, border_style="cyan", show_lines=True)
    table.add_column("Family", style="bold white", min_width=22)
    table.add_column("Versions", justify="right")
    table.add_column("Latest", style="green", min_width=22)
    table.add_column("Max Context", justify="right")
    table.add_column("Fingerprints")
    table.add_column("Category")
    table.add_column("Max Tok/min", justify="right")

    for f in families:
        cat_color = CATEGORY_COLORS.get(f.get("category", "UNKNOWN"), "white")
        max_tok = f.get("max_tok_min") or 0
        table.add_row(
            f["family_name"] or "—",
            str(f["version_count"]),
            f.get("latest_model") or "—",
            f"{f['max_context']:,}" if f.get("max_context") else "—",
            f.get("fingerprints") or "—",
            f"[{cat_color}]{f.get('category','UNKNOWN')}[/{cat_color}]",
            f"{max_tok:,}" if max_tok else "—",
        )
    console.print(table)
    console.print(f"[dim]{len(families)} famiglie.[/dim]")


def print_stats_families():
    """Per-family aggregate statistics (Phase 8)."""
    data = query_family_stats()
    if not data:
        console.print("[yellow]No family data available.[/yellow]")
        return

    table = Table(title="Family Infrastructure Analytics", box=box.ROUNDED, border_style="green", show_lines=True)
    table.add_column("Family", style="bold white", min_width=22)
    table.add_column("Versions", justify="right")
    table.add_column("Max Tok/min", justify="right")
    table.add_column("Max Req/min", justify="right")
    table.add_column("Max Context", justify="right")
    table.add_column("Fingerprints")
    table.add_column("Snapshots", justify="right")
    table.add_column("Avg Latency", justify="right")

    for d in data:
        table.add_row(
            d["family_name"] or "—",
            str(d["version_count"]),
            f"{d['max_token_limit']:,}" if d.get("max_token_limit") else "—",
            f"{d['max_req_limit']:,}" if d.get("max_req_limit") else "—",
            f"{d['max_context']:,}" if d.get("max_context") else "—",
            d.get("fingerprints") or "—",
            str(d.get("snapshot_count", 0)),
            f"{d['avg_latency']:.0f} ms" if d.get("avg_latency") else "—",
        )
    console.print(table)


# ─── Export ──────────────────────────────────────────────────────────────────

def cmd_export(fmt: str, window: str):
    data = export_events(format=fmt, window=window)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = "csv" if fmt == "csv" else "json"
    fname = f"mistral_export_{ts}.{ext}"
    Path(fname).write_text(data, encoding="utf-8")
    console.print(f"[green]Exported:[/green] {fname} ({len(data):,} bytes)")


def cmd_export_models(fmt: str):
    data = export_inventory(format=fmt)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = "csv" if fmt == "csv" else "json"
    fname = f"mistral_models_{ts}.{ext}"
    Path(fname).write_text(data, encoding="utf-8")
    console.print(f"[green]Exported:[/green] {fname} ({len(data):,} bytes)")


def cmd_export_limits(fmt: str):
    data = export_limits(format=fmt)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = "csv" if fmt == "csv" else "json"
    fname = f"mistral_limits_{ts}.{ext}"
    Path(fname).write_text(data, encoding="utf-8")
    console.print(f"[green]Exported:[/green] {fname} ({len(data):,} bytes)")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Mistral Intelligence Monitor v2 — Telemetry & Model Watch",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python monitor.py                              # single inference + rate-limit display
  python monitor.py --model mistral-large-latest # choose model
  python monitor.py --models                     # model catalog with capabilities + inventory
  python monitor.py --test-all                   # probe all models, build inventory + limits
  python monitor.py --limits-report              # rate limits sorted by tok/min
  python monitor.py --stats                      # aggregate stats (all history)
  python monitor.py --stats --window 30d         # last 30 days stats
  python monitor.py --per-model-stats            # v2 enhanced per-model stats
  python monitor.py --history 50                 # last 50 requests
  python monitor.py --trends                     # daily trend data
  python monitor.py --watch-report               # model watch: added/removed/changed
  python monitor.py --duplicates                 # detect alias/duplicate models
  python monitor.py --families                   # model family report
  python monitor.py --stats-families             # per-family infrastructure analytics
  python monitor.py --export csv                 # export usage history
  python monitor.py --export-models json         # export model inventory
  python monitor.py --export-limits csv          # export limits report
  python monitor.py -v                           # debug logging
        """,
    )
    p.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Model to test")
    p.add_argument("--test-all", action="store_true", help="Probe all models, build inventory + limits")
    p.add_argument("--models", action="store_true", help="Show model catalog with capabilities + build inventory")
    p.add_argument("--limits-report", action="store_true", help="Show rate limits report sorted by tok/min")
    p.add_argument("--stats", action="store_true", help="Show aggregate statistics")
    p.add_argument("--per-model-stats", action="store_true", help="Show v2 enhanced per-model statistics")
    p.add_argument("--window", type=str, default="all", choices=["today", "7d", "30d", "all"], help="Time window for stats")
    p.add_argument("--history", type=int, nargs="?", const=20, metavar="N", help="Show last N requests (default 20)")
    p.add_argument("--trends", action="store_true", help="Show daily trend data")
    p.add_argument("--watch-report", action="store_true", help="Model Watch Report: added/removed/changed models")
    p.add_argument("--duplicates", action="store_true", help="Detect alias/duplicate models")
    p.add_argument("--families", action="store_true", help="Show model family report")
    p.add_argument("--stats-families", action="store_true", help="Show per-family infrastructure analytics")
    p.add_argument("--export", type=str, choices=["csv", "json"], metavar="FMT", help="Export usage history")
    p.add_argument("--export-models", type=str, choices=["csv", "json"], metavar="FMT", help="Export model inventory")
    p.add_argument("--export-limits", type=str, choices=["csv", "json"], metavar="FMT", help="Export limits report")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    api_key = _api_key()

    # Purely read-only / informational commands
    if args.models:
        models = discover_models(api_key)
        print_model_table(models)
        return

    if args.stats:
        print_stats(window=args.window)
        return

    if args.per_model_stats:
        print_per_model_stats()
        return

    if args.history is not None:
        print_history(limit=args.history)
        return

    if args.trends:
        print_trends()
        return

    if args.limits_report:
        print_limits_report()
        return

    if args.watch_report:
        print_watch_report()
        return

    if args.duplicates:
        print_duplicates()
        return

    if args.families:
        print_families()
        return

    if args.stats_families:
        print_stats_families()
        return

    if args.export:
        cmd_export(fmt=args.export, window=args.window)
        return

    if args.export_models:
        cmd_export_models(fmt=args.export_models)
        return

    if args.export_limits:
        cmd_export_limits(fmt=args.export_limits)
        return

    if args.test_all:
        resp = requests.get(
            f"{MISTRAL_BASE_URL}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        if resp.status_code != 200:
            console.print(f"[bold red]HTTP {resp.status_code}[/bold red]")
            sys.exit(1)
        test_all(api_key, resp.json().get("data", []))
        return

    # Default: single inference
    console.print(Panel.fit("[bold white]Mistral Usage Monitor[/bold white]", border_style="cyan"))
    result = run_inference(args.model, api_key)
    show_result(result, args.model)


if __name__ == "__main__":
    main()
