"""Classify collection failures without changing legacy per-target messages."""

import json
from urllib.error import URLError


def error_category(error) -> str:
    if isinstance(error, dict):
        return error.get("category", "OTHER")
    current = error
    seen = set()
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        status = getattr(current, "status_code", None) or getattr(current, "code", None)
        code = getattr(current, "error_code", None)
        if status == 429:
            return "RATE_LIMIT"
        if isinstance(status, int) and 500 <= status < 600:
            return "HTTP_5XX"
        if isinstance(status, int) and 400 <= status < 500:
            return "HTTP_4XX"
        if isinstance(current, TimeoutError) or code == "timeout":
            return "TIMEOUT"
        if isinstance(current, (json.JSONDecodeError, UnicodeDecodeError)) or code == "invalid-response":
            return "INVALID_RESPONSE"
        if isinstance(current, URLError) and isinstance(current.reason, TimeoutError):
            return "TIMEOUT"
        if isinstance(current, URLError) or code == "network":
            return "NETWORK"
        current = current.__cause__ or current.__context__
    if isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
        return "INVALID_RESPONSE"
    # Old persisted collection errors contain only a human-readable message.
    text = str(error).lower()
    if "429" in text or "rate-limit" in text or "요청 한도" in text:
        return "RATE_LIMIT"
    if "timeout" in text or "timed out" in text:
        return "TIMEOUT"
    if "형식" in text or "decode" in text:
        return "INVALID_RESPONSE"
    if any(f"({code})" in text for code in range(500, 600)):
        return "HTTP_5XX"
    if "연결하지 못" in text or "connection" in text or "network" in text:
        return "NETWORK"
    return "OTHER"


def error_detail(error: Exception) -> dict:
    return {"category": error_category(error), "message": str(error),
            "status_code": getattr(error, "status_code", None),
            "error_code": getattr(error, "error_code", None)}
