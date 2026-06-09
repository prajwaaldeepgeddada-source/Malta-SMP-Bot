"""
utils/ai_service.py
───────────────────
Centralized AI service for Malta SMP Bot.

Provider: GitHub Models — OpenAI GPT-4o
Endpoint: https://models.inference.ai.azure.com/chat/completions
Auth    : Bearer token via GITHUB_TOKEN environment variable
Model   : Configured via GITHUB_MODEL (default: openai/gpt-4o)

GitHub Models uses the OpenAI-compatible chat completions format,
so the request/response structure is identical to the OpenAI API.

Handles:
  - API calls with retry + exponential back-off
  - Per-guild rate limiting (sliding window)
  - TTL-based response cache (keyed by prompt hash)
  - Fallback responses when the API is unavailable
  - Structured logging for every AI interaction
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Optional

import aiohttp

log = logging.getLogger("MaltaSMP.AI")

# ── Provider configuration ────────────────────────────────────────────────────
# GitHub Models endpoint (Azure-backed OpenAI-compatible API)
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
GITHUB_MODEL: str = os.getenv("GITHUB_MODEL", "openai/gpt-4o")
GITHUB_MODELS_URL = "https://models.inference.ai.azure.com/chat/completions"

# ── Retry settings ────────────────────────────────────────────────────────────
MAX_RETRIES = 3
BASE_RETRY_DELAY = 1.5   # seconds — doubles each attempt

# ── Cache settings ────────────────────────────────────────────────────────────
CACHE_TTL = 300          # 5 minutes — reuse identical prompt results
MAX_CACHE_SIZE = 500     # maximum number of cached entries

# ── Rate limiting (per guild) ─────────────────────────────────────────────────
# Allow 20 requests per 60 seconds per guild
RATE_LIMIT_CALLS = 20
RATE_LIMIT_WINDOW = 60   # seconds


# ── Rate limiter ──────────────────────────────────────────────────────────────

class _RateLimiter:
    """Simple sliding-window rate limiter keyed by guild_id."""

    def __init__(self, max_calls: int, window: float):
        self.max_calls = max_calls
        self.window = window
        # guild_id -> list of call timestamps
        self._calls: dict[int, list[float]] = {}

    def is_allowed(self, guild_id: int) -> bool:
        now = time.monotonic()
        calls = self._calls.setdefault(guild_id, [])
        # Evict entries outside the window
        self._calls[guild_id] = [t for t in calls if now - t < self.window]
        if len(self._calls[guild_id]) >= self.max_calls:
            return False
        self._calls[guild_id].append(now)
        return True

    def seconds_until_reset(self, guild_id: int) -> float:
        calls = self._calls.get(guild_id, [])
        if not calls:
            return 0.0
        oldest = min(calls)
        return max(0.0, self.window - (time.monotonic() - oldest))


# ── Response cache ────────────────────────────────────────────────────────────

class _ResponseCache:
    """TTL-based in-memory cache for AI responses."""

    def __init__(self, ttl: int, max_size: int):
        self.ttl = ttl
        self.max_size = max_size
        self._store: dict[str, tuple[str, float]] = {}  # key -> (value, expiry)

    def _key(self, prompt: str, system: str) -> str:
        raw = f"{system}|||{prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, prompt: str, system: str) -> Optional[str]:
        key = self._key(prompt, system)
        entry = self._store.get(key)
        if entry and time.monotonic() < entry[1]:
            return entry[0]
        if entry:
            del self._store[key]
        return None

    def set(self, prompt: str, system: str, value: str):
        if len(self._store) >= self.max_size:
            # Evict the oldest entry by expiry time
            oldest_key = min(self._store.items(), key=lambda x: x[1][1])[0]
            del self._store[oldest_key]
        key = self._key(prompt, system)
        self._store[key] = (value, time.monotonic() + self.ttl)

    @property
    def size(self) -> int:
        return len(self._store)


# ── Module-level singletons ───────────────────────────────────────────────────
_rate_limiter = _RateLimiter(RATE_LIMIT_CALLS, RATE_LIMIT_WINDOW)
_cache = _ResponseCache(CACHE_TTL, MAX_CACHE_SIZE)

# Shared aiohttp session — created lazily, reused across all requests
_session: Optional[aiohttp.ClientSession] = None


def _get_session() -> aiohttp.ClientSession:
    """
    Return the shared aiohttp session, creating it if needed.

    GitHub Models requires only a standard Bearer token Authorization header.
    The Content-Type header is also required for JSON payloads.
    """
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        )
    return _session


async def close_session():
    """Call on bot shutdown to cleanly close the shared HTTP session."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


# ── Fallback responses ────────────────────────────────────────────────────────
_FALLBACK_CHAT = (
    "I'm having a bit of trouble connecting right now 🙁  "
    "Please try again in a moment, or ask a staff member for help!"
)

_FALLBACK_MODERATION = None   # None = skip AI review, use local rules only


# ── Core completion function ──────────────────────────────────────────────────

async def chat_completion(
    messages: list[dict],
    *,
    system: str = "",
    guild_id: int = 0,
    max_tokens: int = 512,
    temperature: float = 0.7,
    use_cache: bool = True,
) -> str:
    """
    Send a chat completion request to GitHub Models (GPT-4o).

    Parameters
    ----------
    messages    : list of {"role": ..., "content": ...} dicts
    system      : system prompt string (prepended as a system message)
    guild_id    : used for per-guild rate limiting (0 = no rate limit check)
    max_tokens  : maximum tokens in the response
    temperature : sampling temperature (0.0 = deterministic, 1.0 = creative)
    use_cache   : whether to check and populate the response cache

    Returns the model's reply text, or a user-friendly fallback string on failure.
    """
    if not GITHUB_TOKEN:
        log.warning("GITHUB_TOKEN not set — returning fallback response")
        return _FALLBACK_CHAT

    # Per-guild rate limit check
    if guild_id and not _rate_limiter.is_allowed(guild_id):
        wait = _rate_limiter.seconds_until_reset(guild_id)
        log.info(f"Rate limited guild {guild_id} — resets in {wait:.1f}s")
        return f"I'm being used a lot right now! Please wait {int(wait)+1} seconds before chatting again. 🕐"

    # Cache check — only meaningful for single-turn lookups, not conversations
    last_user_msg = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    if use_cache and last_user_msg:
        cached = _cache.get(last_user_msg, system)
        if cached:
            log.debug(f"Cache hit for guild {guild_id}")
            return cached

    # Build the full messages list — system prompt first if provided
    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    # GitHub Models uses the OpenAI chat completions payload format exactly
    payload = {
        "model": GITHUB_MODEL,
        "messages": full_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    session = _get_session()
    delay = BASE_RETRY_DELAY

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.post(GITHUB_MODELS_URL, json=payload) as resp:

                # Rate limited by GitHub — back off and retry
                if resp.status == 429:
                    log.warning(
                        f"GitHub Models 429 (rate limited) on attempt {attempt}/{MAX_RETRIES}"
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    return _FALLBACK_CHAT

                # Server-side error — retry with back-off
                if resp.status >= 500:
                    log.warning(
                        f"GitHub Models {resp.status} on attempt {attempt}/{MAX_RETRIES}"
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    return _FALLBACK_CHAT

                # Unexpected non-200 (e.g. 401 bad token, 400 bad request)
                if resp.status != 200:
                    body = await resp.text()
                    log.error(
                        f"GitHub Models unexpected status {resp.status}: {body[:300]}"
                    )
                    return _FALLBACK_CHAT

                # Success — parse the OpenAI-compatible response
                data = await resp.json()
                reply = data["choices"][0]["message"]["content"].strip()

                # Populate cache if appropriate
                if use_cache and last_user_msg:
                    _cache.set(last_user_msg, system, reply)

                log.info(
                    f"AI response OK: guild={guild_id} model={GITHUB_MODEL} "
                    f"tokens={data.get('usage', {}).get('total_tokens', '?')}"
                )
                return reply

        except asyncio.TimeoutError:
            log.warning(f"GitHub Models timeout on attempt {attempt}/{MAX_RETRIES}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(delay)
                delay *= 2

        except aiohttp.ClientError as exc:
            log.error(f"GitHub Models network error: {exc}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(delay)
                delay *= 2

    return _FALLBACK_CHAT


# ── Moderation analysis ───────────────────────────────────────────────────────

async def moderation_analysis(
    text: str,
    *,
    guild_id: int = 0,
) -> Optional[dict]:
    """
    Ask GPT-4o to analyse a message for harmful content.

    Returns a dict:
        {
            "flagged": True,
            "category": "harassment",
            "severity": "high",      # low | medium | high
            "action": "timeout",     # warn | delete | timeout | escalate
            "reason": "..."
        }
    or None if the API is unavailable (callers should fall back to local rules).
    """
    system = (
        "You are a content moderation assistant for a Minecraft Discord server called Malta SMP. "
        "Analyse the provided message for: toxicity, harassment, hate speech, threats, spam, "
        "advertising, scam attempts, NSFW content, or mass-mention abuse. "
        "Respond ONLY with a JSON object — no prose, no markdown fences. "
        "Keys: flagged (bool), category (string), severity (low|medium|high), "
        "action (warn|delete|timeout|escalate), reason (string ≤100 chars). "
        "If the message is safe, set flagged=false and omit other keys."
    )

    messages = [{"role": "user", "content": f"Message to analyse:\n{text[:1500]}"}]

    # Moderation calls are never cached — each message is unique
    raw = await chat_completion(
        messages,
        system=system,
        guild_id=guild_id,
        max_tokens=200,
        temperature=0.0,
        use_cache=False,
    )

    if raw == _FALLBACK_CHAT:
        return _FALLBACK_MODERATION  # None — caller falls back to local heuristics

    # Strip possible markdown code fences GPT-4o sometimes adds
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()

    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        log.warning(f"AI moderation returned non-JSON: {raw[:200]}")
        return None


# ── Phishing analysis ─────────────────────────────────────────────────────────

async def phishing_analysis(
    text: str,
    urls: list[str],
    *,
    guild_id: int = 0,
) -> Optional[dict]:
    """
    Analyse message text and extracted URLs for phishing / scam content.

    Returns a dict:
        {
            "malicious": bool,
            "type": str,
            "confidence": "low" | "medium" | "high",
            "reason": str
        }
    or None if the API is unavailable.
    """
    system = (
        "You are a security analyst for a Minecraft Discord server. "
        "Determine if the provided message or URLs are a phishing or scam attempt "
        "(Nitro scam, fake giveaway, crypto scam, token grabber, fake Steam/Minecraft login, "
        "URL shortener abuse, fake login page). "
        "Respond ONLY with a JSON object — no prose, no markdown fences. "
        "Keys: malicious (bool), type (string), confidence (low|medium|high), "
        "reason (string ≤100 chars). If safe, set malicious=false."
    )

    content = f"Message:\n{text[:800]}\n\nURLs found:\n" + "\n".join(urls[:10])
    messages = [{"role": "user", "content": content}]

    raw = await chat_completion(
        messages,
        system=system,
        guild_id=guild_id,
        max_tokens=200,
        temperature=0.0,
        use_cache=False,
    )

    if raw == _FALLBACK_CHAT:
        return None

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.lstrip("```json").lstrip("```").rstrip("```").strip()

    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        log.warning(f"Phishing AI returned non-JSON: {raw[:200]}")
        return None


# ── Cache statistics ──────────────────────────────────────────────────────────

def cache_stats() -> dict:
    """Return current cache and provider statistics for /ai stats."""
    return {
        "cache_size": _cache.size,
        "cache_max": MAX_CACHE_SIZE,
        "cache_ttl": CACHE_TTL,
        "rate_limit_calls": RATE_LIMIT_CALLS,
        "rate_limit_window": RATE_LIMIT_WINDOW,
        "model": GITHUB_MODEL,          # shown in /ai stats
        "provider": "GitHub Models",
    }
