"""Shared Anthropic Claude API client for the lightweight AI-assisted HR
features (resume parsing, interview evaluation, HR helpdesk). Each caller
wraps this in its own try/except and falls back to heuristic rules on
failure, so this raises rather than swallowing errors itself.

utils/ai_assistant.py's employee-chat client is intentionally separate: it
returns a (text, error) tuple, distinguishes HTTP/network/n8n failure modes,
and supports a system prompt — a different contract than the simple
prompt-in/text-out callers here.
"""
import json
import os
import urllib.request

_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-5"


def call_claude(prompt, max_tokens=450, timeout=10):
    """POST `prompt` to the Claude Messages API and return the model's text reply.

    Raises RuntimeError if ANTHROPIC_API_KEY isn't set, or propagates any
    request/parsing exception — callers catch and fall back to heuristics."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    payload = {
        "model": _MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        _API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        data = json.loads(resp.read().decode("utf-8"))
        return data["content"][0]["text"]


def call_claude_vision(text_prompt, images, max_tokens=1024, timeout=30):
    """Like call_claude() but for a single user turn that mixes a text
    instruction with one or more images/PDFs -- used by
    utils/document_verification.py to read scanned KYC documents. Every
    other caller in this module only ever sends plain text; this is
    deliberately a separate function rather than an optional parameter on
    call_claude() so that contract stays simple for callers that don't
    need it.

    images: a list of (media_type, base64_data) tuples, e.g.
    ("image/png", "<base64...>") or ("application/pdf", "<base64...>").
    Content blocks are ordered images-then-text (Anthropic's documented
    recommendation for multi-image prompts) so the model sees every
    document before being asked to reason about them together.

    Raises RuntimeError if ANTHROPIC_API_KEY isn't set, or propagates any
    request/parsing exception -- same contract as call_claude()."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    content = []
    for media_type, b64_data in images:
        block_type = "document" if media_type == "application/pdf" else "image"
        content.append({
            "type": block_type,
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        })
    content.append({"type": "text", "text": text_prompt})

    payload = {
        "model": _MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    req = urllib.request.Request(
        _API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        data = json.loads(resp.read().decode("utf-8"))
        return data["content"][0]["text"]
