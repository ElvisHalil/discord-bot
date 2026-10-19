"""
Abstract base contract for Voice-Channel AI Co-Host STT and TTS aggregators (`src/skills/voice/aggregators/base.py`).
"""

from abc import ABC


class BaseVoiceAggregator(ABC):
    """Base contract for Speech-to-Text (STT) and Text-to-Speech (TTS) providers."""
