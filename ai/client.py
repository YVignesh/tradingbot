"""
ai/client.py — Hardened AI Client
===================================
Thin wrapper around Gemini / OpenAI / Anthropic with:
- Exponential backoff retry (3 attempts for 429/503/timeout)
- Native JSON mode where supported
- Thread-safe lazy init
- Call counter and token tracking
- Input sanitization

All AI modules share a single instance via AIOrchestrator.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Optional

from utils import get_logger

_log = get_logger("ai.client")

# Patterns that suggest prompt injection in external text
_INJECTION_PATTERNS = re.compile(
    r"(ignore\s+(all\s+)?previous|system\s*:|assistant\s*:|<\|im_start\|>|"
    r"<\|endoftext\|>|<\|system\|>|\[INST\])",
    re.IGNORECASE,
)


def sanitize_external_text(text: str, max_len: int = 500) -> str:
    """Strip potential prompt injection and control characters from external data."""
    if not text:
        return ""
    clean = text[:max_len]
    clean = "".join(c for c in clean if c.isprintable() or c in ("\n", "\t"))
    clean = _INJECTION_PATTERNS.sub("[FILTERED]", clean)
    return clean.strip()


class AIClient:
    """Unified AI client supporting Gemini, OpenAI, and Anthropic."""

    def __init__(self, config: dict):
        ai_cfg = config.get("ai", {})
        self.provider = str(ai_cfg.get("provider", "gemini")).lower()
        self.model = str(ai_cfg.get("model", "gemini-2.0-flash"))
        self.temperature = float(ai_cfg.get("temperature", 0.2))
        self.timeout = int(ai_cfg.get("timeout_sec", 45))
        self.enabled = bool(ai_cfg.get("enabled", False))

        # Resolve API key: config > env var
        self.api_key = ai_cfg.get("api_key", "")
        if not self.api_key:
            env_map = {
                "gemini": "GEMINI_API_KEY",
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
            }
            self.api_key = os.environ.get(env_map.get(self.provider, ""), "")

        self._client = None
        self._lock = threading.Lock()

        # Usage tracking
        self._call_count = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_latency_ms = 0.0

    def _get_client(self):
        """Thread-safe lazy-init the provider SDK client."""
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is not None:
                return self._client
            if self.provider == "gemini":
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            elif self.provider == "openai":
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key, timeout=self.timeout)
            elif self.provider == "anthropic":
                import anthropic
                self._client = anthropic.Anthropic(
                    api_key=self.api_key,
                    timeout=float(self.timeout),
                )
            else:
                raise ValueError(f"Unknown AI provider: {self.provider}")
            return self._client

    def generate(
        self,
        prompt: str,
        system: str = "",
        temperature: Optional[float] = None,
        json_mode: bool = False,
    ) -> str:
        """
        Send a prompt with exponential backoff retry.

        Args:
            prompt: user message
            system: system instruction
            temperature: override default
            json_mode: request structured JSON output

        Returns:
            Text response from the model.
        """
        if not self.enabled:
            return ""

        temp = temperature if temperature is not None else self.temperature
        max_retries = 3

        for attempt in range(max_retries):
            try:
                start = time.monotonic()
                text = self._call_provider(prompt, system, temp, json_mode)
                elapsed_ms = (time.monotonic() - start) * 1000

                self._call_count += 1
                self._total_latency_ms += elapsed_ms
                _log.debug(
                    "AI call #%d (%s/%s) %.0fms",
                    self._call_count, self.provider, self.model, elapsed_ms,
                )
                return text

            except Exception as exc:
                exc_text = str(exc).lower()
                retryable = any(
                    marker in exc_text
                    for marker in ("429", "rate", "overloaded", "503", "timeout", "timed out", "connection")
                )
                if not retryable or attempt >= max_retries - 1:
                    _log.warning(
                        "AI call failed (%s/%s) after %d attempts: %s",
                        self.provider, self.model, attempt + 1, exc,
                    )
                    raise
                wait = 2 ** (attempt + 1)
                _log.info("AI call retryable error, waiting %ds: %s", wait, exc)
                time.sleep(wait)

        return ""

    def generate_json(
        self,
        prompt: str,
        system: str = "",
        temperature: Optional[float] = None,
    ) -> Optional[dict]:
        """Generate a response and parse as JSON with native JSON mode.
        Returns None on failure (not empty dict) so callers can distinguish."""
        text = self.generate(prompt, system, temperature, json_mode=True)
        if not text:
            _log.warning("AI returned empty response for JSON request")
            return None

        text = text.strip()
        # Strip markdown code fences (fallback for models that ignore json_mode)
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # skip ```json
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            _log.warning("AI response not valid JSON: %.200s...", text)
            return None

    def _call_provider(
        self, prompt: str, system: str, temp: float, json_mode: bool
    ) -> str:
        """Dispatch to the correct provider SDK."""
        client = self._get_client()

        if self.provider == "gemini":
            return self._call_gemini(client, prompt, system, temp, json_mode)
        elif self.provider == "openai":
            return self._call_openai(client, prompt, system, temp, json_mode)
        elif self.provider == "anthropic":
            return self._call_anthropic(client, prompt, system, temp, json_mode)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    def _call_gemini(
        self, client, prompt: str, system: str, temp: float, json_mode: bool
    ) -> str:
        from google.genai import types

        gen_config = types.GenerateContentConfig(
            temperature=temp,
            system_instruction=system if system else None,
        )
        if json_mode:
            gen_config.response_mime_type = "application/json"

        response = client.models.generate_content(
            model=self.model,
            config=gen_config,
            contents=prompt,
        )
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            self._total_input_tokens += response.usage_metadata.prompt_token_count or 0
            self._total_output_tokens += response.usage_metadata.candidates_token_count or 0
        return response.text or ""

    def _call_openai(
        self, client, prompt: str, system: str, temp: float, json_mode: bool
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # Track tokens if available
        if hasattr(response, "usage") and response.usage:
            self._total_input_tokens += response.usage.prompt_tokens or 0
            self._total_output_tokens += response.usage.completion_tokens or 0

        return choice.message.content or ""

    def _call_anthropic(
        self, client, prompt: str, system: str, temp: float, json_mode: bool
    ) -> str:
        # Anthropic doesn't have native json_mode, but we can instruct via prompt
        actual_prompt = prompt
        if json_mode:
            # Always append JSON instruction for Anthropic (no native json_mode)
            actual_prompt += "\n\nRespond with valid JSON only, no other text."

        kwargs = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": temp,
            "messages": [{"role": "user", "content": actual_prompt}],
        }
        if system:
            kwargs["system"] = system

        response = client.messages.create(**kwargs)

        # Track tokens
        if hasattr(response, "usage") and response.usage:
            self._total_input_tokens += response.usage.input_tokens or 0
            self._total_output_tokens += response.usage.output_tokens or 0

        return response.content[0].text or ""

    def usage_stats(self) -> dict:
        """Return usage statistics for logging."""
        return {
            "calls": self._call_count,
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "avg_latency_ms": (
                round(self._total_latency_ms / self._call_count)
                if self._call_count > 0 else 0
            ),
        }
