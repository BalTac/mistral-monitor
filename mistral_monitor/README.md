# Mistral Intelligence Monitor

Tool di telemetria e analisi per l'ecosistema API Mistral.
Monitors rate limits, classifies models, persists history,
detects anomalies and changes in the model inventory.

## Dependencies

- `requests`
- `rich`
- `sqlite3` (stdlib)

## Comandi

```bash
# ─── Discovery & Inventory ───

# Model catalog + inventory + classification
python mistral_monitor/monitor.py --models

# Probe completo: testa tutti i modelli, registra limit, costruisce inventory
python mistral_monitor/monitor.py --test-all

# Rate limits report (ordinato per tok/min decrescente)
python mistral_monitor/monitor.py --limits-report

# ─── Telemetry ───

# Singola inference con rate-limit display
python mistral_monitor/monitor.py
python mistral_monitor/monitor.py --model mistral-large-latest

# ─── Statistics ───

# Statistiche aggregate (finestre: today, 7d, 30d, all)
python mistral_monitor/monitor.py --stats
python mistral_monitor/monitor.py --stats --window 30d

# v2 Enhanced per-model statistics (latency, query cost, success rate)
python mistral_monitor/monitor.py --per-model-stats

# Ultime N richieste
python mistral_monitor/monitor.py --history 50

# Daily trend data (ready for charting)
python mistral_monitor/monitor.py --trends

# ─── Watch & Forensics ───

# Model Watch Report: added/removed/changed models
python mistral_monitor/monitor.py --watch-report

# ─── Evolution & Families (v3) ───

# Detect alias/duplicate models
python mistral_monitor/monitor.py --duplicates

# Model family report with versions, latest, fingerprints
python mistral_monitor/monitor.py --families

# Per-family infrastructure analytics
python mistral_monitor/monitor.py --stats-families

# ─── Export ───

# Usage history
python mistral_monitor/monitor.py --export csv
python mistral_monitor/monitor.py --export json --window 7d

# Model inventory
python mistral_monitor/monitor.py --export-models csv
python mistral_monitor/monitor.py --export-models json

# Limits report
python mistral_monitor/monitor.py --export-limits csv
python mistral_monitor/monitor.py --export-limits json

# ─── Debug ───

python mistral_monitor/monitor.py -v
```

## Structure

```
mistral_monitor/
├── monitor.py       # CLI principale
├── database.py      # SQLite persistence (6 tabelle, auto-migration)
├── stats.py         # Statistics engine & trend analysis
├── classifier.py    # Model classification + family normalization + fingerprint
├── README.md        # This file
├── CHANGELOG.md     # Changelog
├── MODEL_WATCH_REPORT.md     # Example anomaly output
└── FAMILY_ANALYTICS_REPORT.md # Example family output
```

## Database

File: `mistral_monitor/usage_history.db` (creato automaticamente)

Tabelle:
- `usage_events` — telemetria per-request (legacy v1)
- `model_capabilities` — capabilities scoperte (legacy v1)
- `models_inventory` — catalogo modelli con classificazione (v2)
- `rate_limit_snapshots` — storico rate-limit campionati (v2)
- `model_changes` — differential tracking cambiamenti (v2)
- `raw_headers` — header completi per API forensics (v2)

All migrations are automatic on startup.

## Model Classification

Models are automatically classified into 9 categories:
EMBEDDING, CODING, GENERAL, REASONING, MULTIMODAL, AUDIO, MODERATION, AGENTIC, UNKNOWN.

## Family Normalization (v3)

Each model is automatically decomposed into:
- `family_name` (es. `mistral-medium`)
- `version` (es. `2508`, `latest`)
- `is_latest`
- `capability_fingerprint` (es. `CHAT+TOOLS+VISION`)

Comandi correlati: `--duplicates`, `--families`, `--stats-families`.

## Anomaly Detection

`--watch-report` detects:
- Models removed from the API
- Models added
- Changes in capabilities, context length, rate limits
- New HTTP headers introduced by Mistral

## Note

- Not included in Git versioning (`mistral_monitor/` in `.gitignore`)
- Requires `MISTRAL_API_KEY` in environment variable or `.env` file
- Maintains local history in SQLite for cumulative analysis
- All headers are optional — the script works even if Mistral changes the APIs
