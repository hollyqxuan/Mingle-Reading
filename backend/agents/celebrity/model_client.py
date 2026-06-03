from __future__ import annotations

import json
import socket
from typing import Any
from urllib import error, request


def _normalize_chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions") or normalized.endswith("/v1/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def _extract_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("upstream response contained no choices")
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        if text_parts:
            return "\n".join(text_parts)
    raise ValueError("upstream response contained no readable message content")


def invoke_openai_compatible_messages(
    *,
    api_key: str,
    base_url: str,
    model_name: str,
    messages: list[dict[str, str]],
    temperature: float = 0.4,
    max_tokens: int = 700,
    timeout_seconds: int = 90,
    response_format: dict[str, Any] | None = None,
) -> str:
    endpoint = _normalize_chat_completions_url(base_url)
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }
    if response_format is not None:
        payload["response_format"] = response_format
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    req = request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"network timeout: {exc}") from exc
    except (ConnectionError, OSError) as exc:
        raise RuntimeError(f"remote end closed connection: {exc}") from exc
    payload = json.loads(raw)
    return _extract_content(payload)


def invoke_openai_compatible_chat(
    *,
    api_key: str,
    base_url: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4,
    max_tokens: int = 700,
    timeout_seconds: int = 90,
    response_format: dict[str, Any] | None = None,
) -> str:
    return invoke_openai_compatible_messages(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        response_format=response_format,
    )
