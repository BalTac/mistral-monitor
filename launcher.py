#!/usr/bin/env python3
# =============================================================================
# Mistral Intelligence Monitor  (c) 2026 BalTac  |  v3.0.0  |  MIT License
# =============================================================================
"""
launcher.py -- Interactive Menu + Argument Passthrough

If called with arguments: passes them directly to monitor.py (one-liner mode).
If called without arguments: opens an interactive terminal menu (TUI mode).

Usage:
  python launcher.py                          # interactive menu
  python launcher.py --stats --window 7d      # one-liner
  python launcher.py --models                 # one-liner
  python launcher.py --model mistral-large-latest  # single inference
"""

from __future__ import annotations

__version__ = "3.0.0"
__author__ = "BalTac"
__license__ = "MIT"

import os
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Optional

MONITOR_SCRIPT = Path(__file__).resolve().parent / "mistral_monitor" / "monitor.py"
ENV_FILE = Path(__file__).resolve().parent / ".env"

# ─── ANSI / Rich check ────────────────────────────────────────────────────────

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm, IntPrompt
    from rich.text import Text
    from rich.box import ROUNDED
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    Console = None  # type: ignore


# ─── helpers ──────────────────────────────────────────────────────────────────

def _check_env() -> bool:
    """Check if API key is configured."""
    if os.getenv("MISTRAL_API_KEY"):
        return True
    if ENV_FILE.is_file():
        text = ENV_FILE.read_text(encoding="utf-8").strip()
        if "MISTRAL_API_KEY" in text:
            return True
    return False


def _is_powershell() -> bool:
    return "WINDOWS" in os.environ.get("TERM_PROGRAM", "") or os.name == "nt"


# ─── One-liner mode (passthrough) ─────────────────────────────────────────────

def run_passthrough(args: list[str]) -> int:
    """Forward all arguments directly to monitor.py."""
    return subprocess.run(
        [sys.executable, str(MONITOR_SCRIPT)] + args,
        env=os.environ.copy(),
    ).returncode


# ─── Plain-text fallback menu (no rich) ────────────────────────────────────────

def _plain_menu() -> None:
    """Simple input-driven menu when rich is unavailable."""
    menu = {
        "1":  ("Single Inference",             [""]),
        "2":  ("Single Inference (choose model)", ["--model"]),
        "3":  ("Model Catalog",                ["--models"]),
        "4":  ("Probe All Models",             ["--test-all"]),
        "5":  ("Rate Limits Report",           ["--limits-report"]),
        "6":  ("Model Watch Report",           ["--watch-report"]),
        "7":  ("Aggregate Statistics (all)",   ["--stats"]),
        "8":  ("Aggregate Statistics (7d)",    ["--stats", "--window", "7d"]),
        "9":  ("Aggregate Statistics (30d)",   ["--stats", "--window", "30d"]),
        "10": ("Per-Model Statistics",         ["--per-model-stats"]),
        "11": ("Daily Trends",                 ["--trends"]),
        "12": ("Request History (last 20)",    ["--history", "20"]),
        "13": ("Duplicate Detection",          ["--duplicates"]),
        "14": ("Family Report",                ["--families"]),
        "15": ("Family Stats",                 ["--stats-families"]),
        "16": ("Export Usage (CSV)",           ["--export", "csv"]),
        "17": ("Export Models (JSON)",         ["--export-models", "json"]),
        "18": ("Export Limits (CSV)",          ["--export-limits", "csv"]),
        "0":  ("Exit",                          []),
    }

    while True:
        print("\n" + "=" * 60)
        print("   Mistral Intelligence Monitor  (c) 2026 BalTac  |  v3.0.0  |  MIT")
        print("=" * 60)
        for key, (label, _) in menu.items():
            print(f"  [{key}] {label}")
        print("-" * 60)

        choice = input("\n  Choice → ").strip()
        if choice == "0":
            print("Goodbye.")
            break
        if choice == "2":
            model = input("  Model name (e.g. mistral-large-latest): ").strip()
            if model:
                subprocess.run([sys.executable, str(MONITOR_SCRIPT), "--model", model])
            continue
        if choice == "12":
            n = input("  Number of requests [20]: ").strip()
            n = n or "20"
            subprocess.run([sys.executable, str(MONITOR_SCRIPT), "--history", n])
            continue
        if choice in menu:
            _, args = menu[choice]
            if args:
                subprocess.run([sys.executable, str(MONITOR_SCRIPT)] + args)
        else:
            print(f"  Unknown choice: {choice}")


# ─── Rich TUI menu ─────────────────────────────────────────────────────────────

def _rich_menu() -> None:
    """Rich-powered interactive terminal UI."""
    console = Console()

    def show_banner():
        console.print()
        console.print(
            Panel.fit(
                Text("Mistral Intelligence Monitor", style="bold white"),
                border_style="bright_blue",
                box=ROUNDED,
            )
        )
        console.print(
            Text("(c) 2026 BalTac  |  v3.0.0  |  MIT License", style="dim"),
            justify="left",
        )

    def show_env_warning():
        console.print(
            Panel(
                "[yellow]MISTRAL_API_KEY not set[/yellow]\n"
                "Set the environment variable or create a [bold].env[/bold] file.",
                border_style="yellow",
            )
        )

    def show_brief(path: list[str]):
        """Show a 1-line summary after a command runs."""
        pass  # monitor.py handles its own display

    # ── Menu structure ──
    main_options = [
        ("1",  "[bold cyan](> Quick Start[/bold cyan]"),
        ("2",  "[bold green](i) Stats & History[/bold green]"),
        ("3",  "[bold magenta](o) Analysis & Watch[/bold magenta]"),
        ("4",  "[bold blue]👪 Family Analytics (v3)[/bold blue]"),
        ("5",  "[bold yellow][@] Export[/bold yellow]"),
    ]

    quick_start = [
        ("1", "Single Inference (default model)",  [""]),
        ("2", "Single Inference (choose model)",    None),  # prompts
        ("3", "Model Catalog",                      ["--models"]),
        ("4", "Probe All Models (build inventory)", ["--test-all"]),
        ("0", "← Back", []),
    ]

    stats_options = [
        ("1", "Aggregate Stats (all time)",         ["--stats"]),
        ("2", "Aggregate Stats (today)",            ["--stats", "--window", "today"]),
        ("3", "Aggregate Stats (last 7 days)",      ["--stats", "--window", "7d"]),
        ("4", "Aggregate Stats (last 30 days)",     ["--stats", "--window", "30d"]),
        ("5", "Per-Model Statistics",               ["--per-model-stats"]),
        ("6", "Daily Trends",                       ["--trends"]),
        ("7", "Request History (choose count)",     None),
        ("0", "← Back", []),
    ]

    analysis_options = [
        ("1", "Rate Limits Report",                 ["--limits-report"]),
        ("2", "Model Watch Report (changes)",       ["--watch-report"]),
        ("0", "← Back", []),
    ]

    family_options = [
        ("1", "Duplicate / Alias Detection",       ["--duplicates"]),
        ("2", "Model Family Report",                ["--families"]),
        ("3", "Per-Family Infrastructure Stats",   ["--stats-families"]),
        ("0", "← Back", []),
    ]

    export_options = [
        ("1", "Export Usage History (CSV)",        ["--export", "csv"]),
        ("2", "Export Usage History (JSON)",       ["--export", "json"]),
        ("3", "Export Model Inventory (CSV)",      ["--export-models", "csv"]),
        ("4", "Export Model Inventory (JSON)",     ["--export-models", "json"]),
        ("5", "Export Limits Report (CSV)",        ["--export-limits", "csv"]),
        ("6", "Export Limits Report (JSON)",       ["--export-limits", "json"]),
        ("0", "← Back", []),
    ]

    menus = {
        "1": ("Quick Start", quick_start),
        "2": ("Stats & History", stats_options),
        "3": ("Analysis & Watch", analysis_options),
        "4": ("Family Analytics", family_options),
        "5": ("Export", export_options),
    }

    def run_cmd(args: list[str] | None, prompt_label: str = ""):
        """Run a monitor command. If args is None, prompt the user."""
        if args is None:
            if prompt_label == "history":
                n = IntPrompt.ask("  Number of requests to show", default=20)
                args = ["--history", str(n)]
            elif prompt_label == "model":
                model = Prompt.ask("  Model name", default="mistral-medium-latest")
                args = ["--model", model]
            else:
                console.print("[red]Unknown prompt type[/red]")
                return

        if not args:
            # "--model mistral-medium-latest" is default inference
            args = ["--model", "mistral-medium-latest"]

        console.print()
        console.print(f"  [dim]Running: python monitor.py {' '.join(args)}[/dim]")
        console.print("─" * 60)
        ret = subprocess.run(
            [sys.executable, str(MONITOR_SCRIPT)] + args,
            env=os.environ.copy(),
        )
        if ret.returncode != 0 and ret.returncode != 1:
            console.print(f"\n[yellow]Exit code: {ret.returncode}[/yellow]")
        console.print("─" * 60)

    def show_menu(title: str, options: list, parent: bool = False):
        """Show a sub-menu and return True to stay, False to go back."""
        while True:
            console.print()
            table = Table(title=f"[bold]{title}[/bold]", box=ROUNDED, show_header=False, padding=(0, 2))
            table.add_column("", style="cyan", width=4)
            table.add_column("", style="white")
            for key, label, *_ in options:
                table.add_row(f"[{key}]", label)
            console.print(table)

            choice = Prompt.ask("  Choice", choices=[o[0] for o in options], default="0", show_choices=False)

            if choice == "0":
                return True  # go back (stay in outer loop)

            # find the matching option
            for key, label, args in options:
                if key == choice:
                    if args is None:
                        # Dynamic prompt
                        if "History" in label:
                            n = IntPrompt.ask("  Number of requests", default=20)
                            args = ["--history", str(n)]
                        elif "Model" in label or "model" in label.lower():
                            model = Prompt.ask("  Model name", default="mistral-medium-latest")
                            args = ["--model", model]
                        elif "Inference" in label and key == "2":
                            model = Prompt.ask("  Model name", default="mistral-medium-latest")
                            args = ["--model", model]
                        else:
                            args = [""]
                    if args and args != [""]:
                        run_cmd(args)
                    elif args == [""]:
                        run_cmd(["--model", "mistral-medium-latest"])
                    break
            if not parent:
                return True

    def show_main_menu():
        while True:
            console.print()
            table = Table(title="[bold]Main Menu[/bold]", box=ROUNDED, show_header=False, padding=(0, 2))
            table.add_column("", style="cyan", width=4)
            table.add_column("", style="white")
            for key, label in main_options:
                table.add_row(f"[{key}]", label)
            table.add_section()
            table.add_row("[0]", "[dim]Exit[/dim]")
            console.print(table)

            choice = Prompt.ask("  Choice", choices=["0", "1", "2", "3", "4", "5"], default="0", show_choices=False)

            if choice == "0":
                console.print("\n  [green]Goodbye![/green]")
                break

            name, opts = menus[choice]
            show_menu(name, opts)

    # ── Startup ──
    show_banner()
    if not _check_env():
        show_env_warning()
        if not Confirm.ask("\n  Continue without API key?", default=False):
            return
    show_main_menu()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    # If arguments given → passthrough mode
    if len(sys.argv) > 1:
        # strip script name, pass rest to monitor
        sys.exit(run_passthrough(sys.argv[1:]))

    # No arguments → interactive mode
    if HAS_RICH:
        _rich_menu()
    else:
        _plain_menu()


if __name__ == "__main__":
    main()
