# Model Watch Report - Example Output

Example output expected from the `--watch-report` command.
Real data depends on the history in the local database.

```
┌──────────────────────────────────────────────┐
│              Model Watch Report              │
└──────────────────────────────────────────────┘

Inventory: 52 active, 3 removed, 7 changes (7d)

Recently Removed:
  • pixtral-12b-2409 (last seen: 2026-05-15T10:30:00)
  • mistral-tiny (last seen: 2026-04-20T08:15:00)
  • open-mistral-7b (last seen: 2026-03-01T12:00:00)

Recent Changes:
┌─────────────────────────────┬──────────────────┬──────────────────────────────┐
│ Model                       │ Field            │ Old → New                    │
├─────────────────────────────┼──────────────────┼──────────────────────────────┤
│ mistral-small-2506          │ token_limit_min  │ 500000 → 2250000             │
│ ministral-3b-2505           │ context_length   │ 32768 → 131072               │
│ codestral-latest            │ category         │ GENERAL → CODING             │
│ mistral-large-2506          │ reasoning        │ False → True                 │
└─────────────────────────────┴──────────────────┴──────────────────────────────┘

Potential New Headers Detected:
  • x-mistral-billing-tier
  • x-mistral-request-id
```

## Interpretation

- **Removed models**: models no longer present in the `/v1/models` response. They are marked `status=removed` in the inventory.
- **Changes**: differences detected between scans. Each change is recorded in the `model_changes` table.
- **New headers**: HTTP headers not in the known set. Useful for identifying new API features introduced by Mistral.

## How to use

```bash
# First populate the inventory
python mistral_monitor/monitor.py --test-all

# Then check for anomalies
python mistral_monitor/monitor.py --watch-report

# After a few days, re-run test-all and watch-report to see the deltas
python mistral_monitor/monitor.py --test-all
python mistral_monitor/monitor.py --watch-report
```
