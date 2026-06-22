"""
classifier.py — Model Classification Engine for Mistral Intelligence Monitor.
Auto-classifies models into categories based on name + capabilities heuristics.
"""

from __future__ import annotations

from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# Classification rules (ordered — first match wins)
# ═══════════════════════════════════════════════════════════════════════════════


def classify_model(model_id: str, capabilities: Optional[dict] = None) -> str:
    """
    Classify a Mistral model into one of 9 categories.

    Priority order:
      1. Name-based heuristics (most reliable)
      2. Capability-based fallback
      3. UNKNOWN if nothing matches
    """
    name = model_id.lower()
    caps = capabilities or {}

    # ── Name-based rules (strongest signal) ──

    # Embedding models
    if any(kw in name for kw in ["embed", "mistral-embed"]):
        return "EMBEDDING"

    # Moderation models
    if "moderation" in name:
        return "MODERATION"

    # Audio models
    if any(kw in name for kw in ["audio", "speech", "transcribe", "voxtral", "whisper"]):
        return "AUDIO"

    # OCR models
    if "ocr" in name:
        return "MULTIMODAL"

    # Coding models
    if any(kw in name for kw in ["codestral", "code", "devstral"]):
        return "CODING"

    # Agentic / tool-use models
    if any(kw in name for kw in ["agent", "tool", "function"]):
        return "AGENTIC"

    # Reasoning models
    if any(kw in name for kw in ["reason", "think", "deep", "opus"]):
        return "REASONING"

    # ── Capability-based rules (fallback) ──

    caps_bool = {}
    for k, v in caps.items():
        if isinstance(v, bool):
            caps_bool[k] = v
        elif isinstance(v, dict):
            caps_bool[k] = bool(v)
        elif isinstance(v, (int, float)):
            caps_bool[k] = v > 0
        else:
            caps_bool[k] = bool(v)

    # Multimodal: vision + chat
    if caps_bool.get("vision") and caps_bool.get("completion_chat"):
        return "MULTIMODAL"

    # Audio
    if caps_bool.get("audio") or caps_bool.get("audio_transcription") or caps_bool.get("audio_speech"):
        return "AUDIO"

    # Classification
    if caps_bool.get("classification"):
        return "MODERATION" if caps_bool.get("moderation") else "GENERAL"

    # Function calling → agentic
    if caps_bool.get("function_calling") or caps_bool.get("tool_calling"):
        return "AGENTIC"

    # Reasoning
    if caps_bool.get("reasoning"):
        return "REASONING"

    # Fine-tuning only → GENERAL
    if caps_bool.get("fine_tuning") and not caps_bool.get("completion_chat"):
        return "GENERAL"

    # Completion FIM → CODING
    if caps_bool.get("completion_fim"):
        return "CODING"

    # Chat completion → GENERAL
    if caps_bool.get("completion_chat"):
        return "GENERAL"

    # ── Size-based heuristics for GENERAL models ──
    if any(kw in name for kw in ["small", "medium", "large", "mini", "tiny", "3b", "7b", "8b"]):
        return "GENERAL"

    return "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════════
# Category metadata
# ═══════════════════════════════════════════════════════════════════════════════

CATEGORY_DESCRIPTIONS = {
    "EMBEDDING":    "Vector embedding models",
    "CODING":       "Code generation & completion",
    "GENERAL":      "General-purpose chat & text",
    "REASONING":    "Deep reasoning & chain-of-thought",
    "MULTIMODAL":   "Vision + text models",
    "AUDIO":        "Speech, transcription, audio",
    "MODERATION":   "Content moderation & safety",
    "AGENTIC":      "Tool use, function calling, agents",
    "UNKNOWN":      "Unclassified / new model type",
}

CATEGORY_COLORS = {
    "EMBEDDING":    "dim cyan",
    "CODING":       "bold green",
    "GENERAL":      "white",
    "REASONING":    "bold magenta",
    "MULTIMODAL":   "bold blue",
    "AUDIO":        "bold yellow",
    "MODERATION":   "bold red",
    "AGENTIC":      "bold cyan",
    "UNKNOWN":      "dim",
}


# ═══════════════════════════════════════════════════════════════════════════════
# v3 — Family normalization & capability fingerprint
# ═══════════════════════════════════════════════════════════════════════════════

import re as _re  # noqa: E402

def parse_family(model_id: str) -> tuple[str, str, bool]:
    """Extract family_name, version, is_latest from a model ID.

    Examples:
        mistral-medium-2508  → ('mistral-medium', '2508', False)
        mistral-medium-latest → ('mistral-medium', 'latest', True)
        devstral-2512        → ('devstral', '2512', False)
        ministral-3b-2505    → ('ministral-3b', '2505', False)
        pixtral-large-2511   → ('pixtral-large', '2511', False)
        mistral-embed        → ('mistral-embed', '', False)
    """
    name = model_id.lower().strip()

    # Check for -latest suffix
    if name.endswith("-latest"):
        base = name[: -len("-latest")]
        return (base, "latest", True)

    # Check for date-based version suffix: -YYMM or -YYYYMMDD
    m = _re.search(r"-(2[34]\d{2,6})$", name)
    if m:
        base = name[: m.start()]
        ver = m.group(1)
        return (base, ver, False)

    # Check for numeric version: -v2, -3, etc. at the end
    m = _re.search(r"-v?(\d+)$", name)
    if m:
        base = name[: m.start()]
        ver = m.group(1)
        return (base, ver, False)

    # No version detected — the whole thing is the family
    return (name, "", False)


def capability_fingerprint(capabilities: dict) -> str:
    """Generate a compact capability fingerprint string.

    Examples:
        CHAT+TOOLS
        CHAT+TOOLS+VISION
        CHAT+TOOLS+REASONING
        OCR+VISION
        AUDIO+TTS
        EMBEDDING
        MODERATION
    """
    caps = {}
    for k, v in (capabilities or {}).items():
        if isinstance(v, bool):
            caps[k] = v
        elif isinstance(v, dict):
            caps[k] = bool(v)
        elif isinstance(v, (int, float)):
            caps[k] = v > 0
        else:
            caps[k] = bool(v)

    tags = []

    # Embedding models
    if caps.get("embedding") or caps.get("embeddings"):
        return "EMBEDDING"

    # Moderation
    if caps.get("moderation"):
        tags.append("MODERATION")

    # Audio capabilities
    audio_tags = []
    if caps.get("audio"):
        audio_tags.append("AUDIO")
    if caps.get("audio_transcription") or caps.get("audio_transcription_realtime"):
        audio_tags.append("STT")
    if caps.get("audio_speech"):
        audio_tags.append("TTS")
    if audio_tags:
        return "+".join(audio_tags)

    # Vision / OCR
    if caps.get("ocr"):
        tags.append("OCR")
    if caps.get("vision"):
        tags.append("VISION")

    # Core chat
    if caps.get("completion_chat"):
        tags.insert(0, "CHAT")

    # Tools
    if caps.get("function_calling") or caps.get("tool_calling"):
        tags.append("TOOLS")

    # Reasoning
    if caps.get("reasoning"):
        tags.append("REASONING")

    # FIM / Code
    if caps.get("completion_fim"):
        if "CHAT" in tags:
            tags.append("FIM")
        else:
            tags.append("CODE")

    # Classification
    if caps.get("classification"):
        tags.append("CLASSIFY")

    # Fine-tuning
    if caps.get("fine_tuning") and not caps.get("completion_chat"):
        tags.append("FT")

    if not tags:
        return "UNKNOWN"

    return "+".join(tags)
