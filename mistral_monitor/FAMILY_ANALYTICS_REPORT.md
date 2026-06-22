# Family Analytics Report - Example Output

Example output expected from v3 commands.
Real data depends on the history in the local database.

## --duplicates

```
┌───────────────────────────────────────────────────────────────────────┐
│                     Duplicate & Alias Detection                       │
├─────────────────────────┬─────────────────────────┬──────────────────┤
│ mistral-medium-latest   │ mistral-medium-2508     │ CHAT+TOOLS       │
│ mistral-large-latest    │ mistral-large-2512      │ CHAT+TOOLS+VISION│
│ devstral-latest         │ devstral-2512           │ CHAT+TOOLS+FIM   │
│ ministral-3b-latest     │ ministral-3b-2505       │ CHAT             │
└─────────────────────────┴─────────────────────────┴──────────────────┘
4 alias/duplicate pairs found.
```

## --families

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           Model Family Report                                 │
├───────────────┬──────────┬──────────────────────┬─────────────┬──────────────┤
│ Family        │ Versions │ Latest               │ Max Context │ Fingerprints │
├───────────────┼──────────┼──────────────────────┼─────────────┼──────────────┤
│ mistral-medium│ 7        │ mistral-medium-latest│ 262144      │ CHAT+TOOLS   │
│ mistral-large │ 5        │ mistral-large-latest │ 524288      │ CHAT+TOOLS   │
│ devstral      │ 4        │ devstral-latest      │ 262144      │ CHAT+TOOLS   │
│ pixtral-large │ 3        │ pixtral-large-latest │ 262144      │ CHAT+TOOLS   │
│ codestral     │ 3        │ codestral-latest     │ 262144      │ CHAT+TOOLS   │
│ ministral-3b  │ 2        │ ministral-3b-latest  │ 131072      │ CHAT         │
│ mistral-embed │ 1        │ mistral-embed        │ 8192        │ EMBEDDING    │
└───────────────┴──────────┴──────────────────────┴─────────────┴──────────────┘
7 famiglie.
```

## --stats-families

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                      Family Infrastructure Analytics                             │
├───────────────┬──────────┬──────────────┬──────────────┬─────────────┬──────────┤
│ Family        │ Versions │ Max Tok/min  │ Max Req/min  │ Max Context │ Avg Lat  │
├───────────────┼──────────┼──────────────┼──────────────┼─────────────┼──────────┤
│ mistral-small │ 2        │ 2,250,000    │ 30           │ 262144      │ 450 ms   │
│ ministral-3b  │ 2        │ 1,300,000    │ 30           │ 131072      │ 320 ms   │
│ devstral      │ 4        │ 1,000,000    │ 30           │ 262144      │ 580 ms   │
│ mistral-large │ 5        │ 250,000      │ 30           │ 524288      │ 890 ms   │
│ mistral-medium│ 7        │ 25,000       │ 30           │ 262144      │ 720 ms   │
└───────────────┴──────────┴──────────────┴──────────────┴─────────────┴──────────┘
```

## Family Normalization Logic

```
parse_family("mistral-medium-2508")   → ("mistral-medium", "2508", False)
parse_family("mistral-medium-latest") → ("mistral-medium", "latest", True)
parse_family("devstral-2512")         → ("devstral", "2512", False)
parse_family("ministral-3b-2505")     → ("ministral-3b", "2505", False)
parse_family("mistral-embed")         → ("mistral-embed", "", False)
```

## Capability Fingerprint Logic

```
capability_fingerprint({completion_chat: true, function_calling: true, vision: true})
  → "CHAT+TOOLS+VISION"

capability_fingerprint({completion_chat: true, reasoning: true})
  → "CHAT+TOOLS+REASONING"

capability_fingerprint({audio: true, audio_speech: true, audio_transcription: true})
  → "AUDIO+STT+TTS"

capability_fingerprint({ocr: true, vision: true})
  → "OCR+VISION"

capability_fingerprint({embeddings: true})
  → "EMBEDDING"
```

## How to use

```bash
# Populate inventory with family data
python mistral_monitor/monitor.py --test-all
# oppure
python mistral_monitor/monitor.py --models

# View duplicates
python mistral_monitor/monitor.py --duplicates

# Family report
python mistral_monitor/monitor.py --families

# Per-family analytics
python mistral_monitor/monitor.py --stats-families
```
