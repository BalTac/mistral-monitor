"""
test_mistral.py - Script diagnostico per Mistral AI API
=========================================================
Pre-flight checks (API key, models, rate limits)
before sending a request, supporting Chat, Embeddings and Moderation.

Dependencies: requests + stdlib only. No external dependencies (no rich, no dotenv).
"""

import os
import sys
import time
import json
import argparse
from datetime import datetime
from pathlib import Path

import requests

# ─── Configurazione ──────────────────────────────────────────────────────────

MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
DEFAULT_MODEL = "mistral-medium-latest"
TEST_MESSAGE = "Is Paris in France?"
MAX_RETRIES = 3

MODEL = DEFAULT_MODEL

# ─── ANSI helpers (Windows Terminal / PowerShell compatibile) ────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"
BOLD_RED = "\033[1;31m"
BOLD_GREEN = "\033[1;32m"
BOLD_YELLOW = "\033[1;33m"
BOLD_BLUE = "\033[1;34m"
BOLD_CYAN = "\033[1;36m"
BOLD_WHITE = "\033[1;37m"

# ─── Minimal .env loader ─────────────────────────────────────────────────────

def _load_dotenv(path: str = ".env") -> None:
    """Load KEY=value pairs from a .env file into os.environ (does not overwrite existing)."""
    env_path = Path(path)
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

# ─── Output formatting ───────────────────────────────────────────────────────

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def info(msg: str):
    print(f"{DIM}{ts()}{RESET}  {BOLD_BLUE}INFO {RESET} {msg}")

def ok(msg: str):
    print(f"{DIM}{ts()}{RESET}  {BOLD_GREEN}  OK {RESET} {msg}")

def warn(msg: str):
    print(f"{DIM}{ts()}{RESET}  {BOLD_YELLOW}WARN {RESET} {msg}")

def err(msg: str):
    print(f"{DIM}{ts()}{RESET}  {BOLD_RED}ERROR{RESET} {msg}")

def fatal(msg: str):
    print()
    _print_box(msg, title="FATAL ERROR", border_color=RED)
    sys.exit(1)

def _print_box(text: str, title: str = "", border_color: str = CYAN, width: int = 72):
    """Stampa un box testuale semplice con eventuale titolo."""
    inner_w = width - 4
    if title:
        top = f"{border_color}┌─ {title} ─{'─' * max(0, inner_w - len(title) - 2)}┐{RESET}"
    else:
        top = f"{border_color}┌{'─' * (width - 2)}┐{RESET}"
    bot = f"{border_color}└{'─' * (width - 2)}┘{RESET}"

    print(top)
    for line in text.splitlines():
        print(f"{border_color}│{RESET} {line:<{inner_w}} {border_color}│{RESET}")
    print(bot)

def _print_table(headers: list[str], rows: list[list[str]], title: str = ""):
    """Stampa una tabella formattata con allineamento automatico."""
    all_data = [headers] + rows
    col_widths = [max(len(str(row[i])) for row in all_data) for i in range(len(headers))]
    total_w = sum(col_widths) + 3 * (len(headers) - 1) + 4

    if title:
        print(f"{BOLD_WHITE}{title}{RESET}")

    # Header
    sep_top = "┬".join("─" * (w + 2) for w in col_widths)
    sep_mid = "┼".join("─" * (w + 2) for w in col_widths)
    sep_bot = "┴".join("─" * (w + 2) for w in col_widths)
    print(f"{BOLD_CYAN}┌{sep_top}┐{RESET}")
    header_cells = [f" {h:<{col_widths[i]}} " for i, h in enumerate(headers)]
    print(f"{BOLD_CYAN}│{RESET}{f'{BOLD_CYAN}│{RESET}'.join(header_cells)}{BOLD_CYAN}│{RESET}")
    print(f"{BOLD_CYAN}├{sep_mid}┤{RESET}")

    # Rows
    for row in rows:
        cells = [f" {str(c):<{col_widths[i]}} " for i, c in enumerate(row)]
        print(f"{DIM}│{RESET}{f'{DIM}│{RESET}'.join(cells)}{DIM}│{RESET}")

    print(f"{BOLD_CYAN}└{sep_bot}┘{RESET}")


# ─── Rate-limit parsing ──────────────────────────────────────────────────────

def _parse_rate_limits(headers: dict) -> dict[str, str]:
    mappings = {
        "x-ratelimit-limit-req-minute":       "Limite req/min",
        "x-ratelimit-remaining-req-minute":   "Req rimanenti/min",
        "x-ratelimit-limit-tokens-minute":    "Limite tok/min",
        "x-ratelimit-remaining-tokens-minute":"Tok rimanenti/min",
        "x-ratelimit-limit-req-month":        "Limite req/mese",
        "x-ratelimit-remaining-req-month":    "Req rimanenti/mese",
        "x-ratelimit-limit-tokens-month":     "Limite tok/mese",
        "x-ratelimit-remaining-tokens-month": "Tok rimanenti/mese",
        "retry-after":                        "Retry (s)",
    }
    result = {}
    hlow = {k.lower(): str(v) for k, v in headers.items()}
    for key, label in mappings.items():
        if key in hlow:
            result[label] = hlow[key]
    return result

def _usage_pct(limit_key: str, remain_key: str, rl: dict) -> float | None:
    try:
        lim, rem = rl.get(limit_key), rl.get(remain_key)
        if lim and rem:
            lim_i, rem_i = int(lim), int(rem)
            return ((lim_i - rem_i) / lim_i) * 100 if lim_i > 0 else 0.0
    except (ValueError, TypeError):
        pass
    return None

def show_rate_limits(headers: dict, title: str = "Rate Limits"):
    rl = _parse_rate_limits(headers)
    if not rl:
        warn("Nessun header rate-limit nella risposta.")
        return

    pairs = [
        ("Limite req/min", "Req rimanenti/min"),
        ("Limite tok/min", "Tok rimanenti/min"),
        ("Limite req/mese", "Req rimanenti/mese"),
        ("Limite tok/mese", "Tok rimanenti/mese"),
    ]

    print(f"\n{BOLD_CYAN}── {title} ──{RESET}")
    for label, value in rl.items():
        marker = ""
        color = GREEN
        for lk, rk in pairs:
            if label in (lk, rk):
                pct = _usage_pct(lk, rk, rl)
                if pct is not None:
                    marker = f"  [{pct:5.1f}%]"
                    if pct >= 90:
                        color = BOLD_RED
                    elif pct >= 70:
                        color = BOLD_YELLOW
                if "rimanenti" in label.lower() and value == "0":
                    color = BOLD_RED
                break
        print(f"  {color}{label:<22}{RESET} {value:>10}{DIM}{marker}{RESET}")


# ─── Endpoint dispatcher ─────────────────────────────────────────────────────

def _get_endpoint(model_id: str, is_test_all: bool = False) -> tuple[str, dict]:
    m = model_id.lower()
    msg = "OK" if is_test_all else TEST_MESSAGE

    if "embed" in m:
        return f"{MISTRAL_BASE_URL}/embeddings", {"model": model_id, "input": [msg]}
    if "moderation" in m:
        return f"{MISTRAL_BASE_URL}/moderations", {"model": model_id, "input": msg}
    # chat completions (default)
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": msg}],
        "max_tokens": 1 if is_test_all else 256,
    }
    if not is_test_all:
        payload["temperature"] = 0.2
    return f"{MISTRAL_BASE_URL}/chat/completions", payload


# ─── Core steps ──────────────────────────────────────────────────────────────

def validate_api_key() -> str:
    info("Controllo MISTRAL_API_KEY ...")
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        fatal("MISTRAL_API_KEY not found!\nSet the environment variable d'ambiente o crea un file .env.")
    ok("API Key trovata.")
    return api_key

def check_models(api_key: str) -> list[dict]:
    info(f"GET {MISTRAL_BASE_URL}/models ...")
    resp = requests.get(
        f"{MISTRAL_BASE_URL}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    if resp.status_code != 200:
        fatal(f"HTTP {resp.status_code} — {resp.text[:300]}")
    models = resp.json().get("data", [])
    ok(f"{len(models)} models available.")
    model_ids = [m.get("id", "") for m in models]
    if MODEL in model_ids:
        ok(f"Model '{MODEL}' found.")
    else:
        warn(f"Model '{MODEL}' NOT in the list! Check the name.")
    return models

def run_inference(api_key: str) -> dict:
    url, payload = _get_endpoint(MODEL)
    info(f"POST {url}")
    info(f"Model: {MODEL}")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=60,
            )
            print("\n=== HEADERS ===")
            for k, v in resp.headers.items():
                print(f"{k}: {v}")
            print("================\n")
        except requests.RequestException as e:
            fatal(f"Richiesta fallita: {e}")

        show_rate_limits(dict(resp.headers))

        if resp.status_code == 200:
            ok("Inference completata.")
            data = resp.json()
            hlow = {k.lower(): str(v) for k, v in resp.headers.items()}
            data["_tier"] = {
                "tok_mese_lim": hlow.get("x-ratelimit-limit-tokens-month"),
                "tok_mese_rem": hlow.get("x-ratelimit-remaining-tokens-month"),
            }
            return data

        if resp.status_code == 429:
            wait_s = float(resp.headers.get("retry-after", 2 ** attempt))
            warn(f"Rate-limit (429). Attesa {wait_s:.0f}s...")
            time.sleep(wait_s)
            continue

        fatal(f"HTTP {resp.status_code} — {resp.text[:400]}")

    fatal("Tentativi esauriti.")

def show_result(result: dict):
    print()

    # Embedding
    data_items = result.get("data", [])
    if data_items and isinstance(data_items, list) and "embedding" in data_items[0]:
        emb = data_items[0]["embedding"]
        _print_box(
            f"Vettore di dimensione: {len(emb)}\nPrimi 5 valori: {emb[:5]} ...",
            title="Embedding",
            border_color=BLUE,
        )
        return

    # Moderation
    if "results" in result:
        _print_box(
            json.dumps(result["results"], indent=2, ensure_ascii=False),
            title="Moderazione",
            border_color=MAGENTA,
        )
        return

    # Chat
    for choice in result.get("choices", []):
        content = choice.get("message", {}).get("content", "")
        print(f"{BOLD_GREEN}── Risposta Chat ──{RESET}")
        print(content)
        print(f"{BOLD_GREEN}───────────────────{RESET}")

    # Tier info
    tier = result.get("_tier", {})
    lim = tier.get("tok_mese_lim")
    rem = tier.get("tok_mese_rem")
    if lim and rem:
        lim_i, rem_i = int(lim), int(rem)
        used = lim_i - rem_i
        pct = (used / lim_i) * 100 if lim_i > 0 else 0
        color = BOLD_RED if pct >= 90 else BOLD_YELLOW if pct >= 70 else GREEN
        print(f"\n{BOLD_WHITE}Quota mensile:{RESET} {color}{used:,} / {lim_i:,} token ({pct:.1f}%){RESET}")


# ─── --test-all ──────────────────────────────────────────────────────────────

def test_all(api_key: str, models: list[dict]):
    skip = {"ocr", "transcribe", "voxtral"}
    candidates = [m for m in models if not any(k in m.get("id", "").lower() for k in skip)]
    candidates.sort(key=lambda x: x.get("id", ""))

    info(f"{len(candidates)} testable models. Starting...\n")

    rows = []
    for idx, m in enumerate(candidates, 1):
        mid = m.get("id", "?")
        print(f"  {DIM}[{idx}/{len(candidates)}]{RESET} {mid:<36}", end=" ", flush=True)

        url, payload = _get_endpoint(mid, is_test_all=True)
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=30,
            )
        except requests.RequestException:
            print(f"{RED}network error{RESET}")
            continue

        if r.status_code == 429:
            print(f"{YELLOW}429{RESET}")
            time.sleep(float(r.headers.get("retry-after", 5)))
            continue
        if r.status_code != 200:
            print(f"{RED}HTTP {r.status_code}{RESET}")
            continue

        hlow = {k.lower(): v for k, v in r.headers.items()}
        lim_s = hlow.get("x-ratelimit-limit-tokens-month", "")
        rem_s = hlow.get("x-ratelimit-remaining-tokens-month", "")

        if lim_s and rem_s:
            lim, rem = int(lim_s), int(rem_s)
            pct = ((lim - rem) / lim) * 100 if lim > 0 else 0
            color = BOLD_RED if pct >= 90 else YELLOW if pct >= 70 else GREEN
            rows.append([
                str(idx), mid, f"{lim - rem:,}", f"{lim:,}",
                f"{color}{pct:.1f}%{RESET}", f"{GREEN}OK{RESET}",
            ])
            print(f"{color}{pct:.1f}%{RESET}")
        else:
            rows.append([str(idx), mid, "-", "-", "-", f"{DIM}no quota{RESET}"])
            print(f"{DIM}no quota{RESET}")

        time.sleep(0.5)

    print()
    if rows:
        _print_table(
            ["#", "Model", "Tokens used", "Total tokens", "Usage %", "Status"],
            rows,
            title="Monthly quota per model",
        )


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    _print_box("Mistral API Test", title="", border_color=BOLD_CYAN, width=40)
    api_key = validate_api_key()
    check_models(api_key)
    result = run_inference(api_key)
    show_result(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mistral API diagnostic tool")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Modello da testare")
    parser.add_argument("--test-all", action="store_true", help="Test all available models")
    args = parser.parse_args()

    MODEL = args.model

    if args.test_all:
        api_key = validate_api_key()
        resp = requests.get(
            f"{MISTRAL_BASE_URL}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        if resp.status_code != 200:
            fatal(f"HTTP {resp.status_code}")
        test_all(api_key, resp.json().get("data", []))
    else:
        main()
