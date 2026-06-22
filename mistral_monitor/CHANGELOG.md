# CHANGELOG — Mistral Intelligence Monitor

## v3.0.0 (2026-06-06)

### Added
- **Family normalization** (`parse_family`): automatic extraction of `family_name`, `version`, `is_latest` from model IDs
- **Capability fingerprint** (`capability_fingerprint`): compact signature like `CHAT+TOOLS+VISION`, `AUDIO+STT+TTS`
- `--duplicates` command: detects alias/duplicate models based on family + fingerprint match
- `--families` command: aggregated family report (versions, latest, context, fingerprints, category, max tok/min)
- `--stats-families` command: per-family infrastructure analytics (token limits, latency, snapshot count)
- `query_families()`, `query_duplicates()`, `query_family_stats()` in database.py
- v3 columns in `models_inventory`: `family_name`, `version`, `is_latest`, `capability_fingerprint`
- Safe `ALTER TABLE` migration (idempotent, runs on every connect if columns missing)
- `FAMILY_ANALYTICS_REPORT.md`: example output document

### Changed
- `upsert_inventory()`: now accepts and persists v3 fields (family_name, version, is_latest, fingerprint)
- `discover_models()`: populates family normalization and fingerprints during model discovery
- `test_all()`: same v3 field population during probing
- CLI: 17 commands (was 14)
- `classifier.py`: now also exports `parse_family` and `capability_fingerprint`

## v2.0.0 (2026-06-06)

### Added
- **Phase 1** — `models_inventory` table: persistent model catalog with auto-classification
- **Phase 2** — `rate_limit_snapshots` table: historical rate-limit samples
- **Phase 3** — `classifier.py`: auto-classification engine (9 categories: EMBEDDING, CODING, GENERAL, REASONING, MULTIMODAL, AUDIO, MODERATION, AGENTIC, UNKNOWN)
- **Phase 4** — `--limits-report`: rate limits sorted by tok/min with per-category averages
- **Phase 5** — `model_changes` table: differential tracking of capability/context/limit changes
- **Phase 6** — `--per-model-stats`: enhanced v2 statistics per model (test count, latency, query cost)
- **Phase 7** — `--export-models csv|json`, `--export-limits csv|json`: inventory and limits export
- **Phase 8** — `raw_headers` table: full HTTP response headers saved for API forensics
- **Phase 9** — `--watch-report`: anomaly detection (added/removed/changed models)
- `discover_new_headers()`: auto-detect unknown headers in API responses
- `detect_anomalies()`: compare inventory snapshot vs current API state
- `print_model_table`: now includes category colors from classifier

### Changed
- `discover_models()`: now builds inventory, classifies models, records changes (Phases 1+3+5)
- `test_all()`: now updates inventory with observed rate limits
- `run_inference()`: now saves raw headers to forensics table (Phase 8)
- `insert_event()`: dual-writes to `rate_limit_snapshots` for smooth migration
- Database: 6 tables total (was 2), all migrations automatic
- CLI: 14 commands (was 9)

### Fixed
- Removed unused rich imports (Layout, Live, Progress, Text) to avoid noise
- Backward compatibility: all v1 functions preserved, tables not dropped

## v1.0.0 (2026-06-06)

### Added
- Initial release: `monitor.py`, `database.py`, `stats.py`
- Single inference with rate-limit display
- `--test-all` model probing
- `--models` capability discovery
- `--stats`, `--history`, `--trends`, `--export`
- SQLite persistence (`usage_events`, `model_capabilities`)
- Robust nullable header parsing
- Structured logging (INFO/DEBUG/ERROR)
