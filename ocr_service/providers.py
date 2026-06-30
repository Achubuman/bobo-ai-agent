# -*- coding: utf-8 -*-
"""菜單 OCR 模型供應商層（Gemini / Claude 可切）。

- 系統提示詞共用同一份 menu_ocr_prompt.md（model-agnostic）。
- 用原生 HTTP（requests）呼叫，不需各家 SDK，安裝最少。
- API key 從環境變數讀取，不經過前端（後端架構的重點：藏 key + 解 CORS）。
"""
import json
import os
import re
import time
from pathlib import Path

import requests

PROMPT = (Path(__file__).resolve().parent.parent / "menu_ocr_prompt.md").read_text("utf-8")
USER_TEXT = "請解析這張菜單，依約定格式只輸出純 JSON 陣列。"
# 預設 gemini-3.5-flash：辨識準確度較佳（2.5 誤判較多）。
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash", "claude": "claude-sonnet-4-6"}


def redact(text: str) -> str:
    """從錯誤／日誌訊息移除 API key，避免後端把 key 洩漏到前端。

    1) 遮掉 URL query 的 key=...（萬一仍有舊式呼叫）。
    2) 直接比對環境變數中的 key 值並遮掉（防呆，含 Anthropic header 情境）。
    """
    text = re.sub(r"(key=)[A-Za-z0-9._\-]+", r"\1***", text or "")
    for env in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        v = os.environ.get(env, "").strip()
        if v:
            text = text.replace(v, "***")
    return text


# ── JSON 抽取／截斷救援（與前端 extractArray/repairArray 同邏輯）──────────
def _extract_array(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(json)?", "", text).strip().rstrip("`").strip()
    s, e = text.find("["), text.rfind("]")
    if s != -1 and e != -1:
        try:
            return json.loads(text[s:e + 1])
        except json.JSONDecodeError:
            pass
    return _repair_array(text)


def _repair_array(text: str):
    start = text.find("[")
    if start == -1:
        return None
    depth, in_str, esc, last = 0, False, False, -1
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 1 and c == "}":
                last = i
    if last == -1:
        return None
    try:
        return json.loads(text[start:last + 1] + "]")
    except json.JSONDecodeError:
        return None


# ── HTTP 呼叫（含暫時性錯誤自動重試）─────────────────────────
# 免費 tier 的模型常因共用容量塞車回 503/overloaded（暫時性，非請求本身有錯）。
# 對 500/502/503/504 自動退避重試；429（額度用完）不重試，直接讓使用者看到。
_RETRY_STATUS = {500, 502, 503, 504}


def _post_retry(url, *, headers=None, json_body=None, timeout=120, tries=3):
    last = None
    for i in range(tries):
        r = requests.post(url, headers=headers, json=json_body, timeout=timeout)
        if r.status_code not in _RETRY_STATUS:
            return r
        last = r
        if i < tries - 1:
            time.sleep(1.5 * (2 ** i))   # 退避：1.5s、3s
    return last


# ── 各家呼叫 ────────────────────────────────────────────────
def call_gemini(image_b64: str, mime: str, model: str) -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺 GEMINI_API_KEY 環境變數")
    # key 放 header（x-goog-api-key），不塞進 URL → 任何錯誤訊息都不會帶出 key。
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": PROMPT}]},
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": mime, "data": image_b64}},
            {"text": USER_TEXT},
        ]}],
        "generationConfig": {"maxOutputTokens": 8192},
    }
    r = _post_retry(url, headers={"x-goog-api-key": key}, json_body=body, timeout=120)
    r.raise_for_status()
    parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def call_claude(image_b64: str, mime: str, model: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺 ANTHROPIC_API_KEY 環境變數")
    body = {
        "model": model, "max_tokens": 8192, "system": PROMPT,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}},
            {"type": "text", "text": USER_TEXT},
        ]}],
    }
    r = _post_retry(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json_body=body, timeout=120,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []))


CALLERS = {"gemini": call_gemini, "claude": call_claude}


def parse_menu(provider: str, image_b64: str, mime: str = "image/jpeg", model: str = None) -> dict:
    fn = CALLERS.get(provider)
    if not fn:
        raise ValueError(f"未知 provider：{provider}（可用：{', '.join(CALLERS)}）")
    model = model or DEFAULT_MODELS[provider]
    t0 = time.time()
    raw = fn(image_b64, mime, model)
    elapsed_ms = round((time.time() - t0) * 1000)
    parsed = _extract_array(raw)
    return {
        "ok": parsed is not None,
        "provider": provider, "model": model, "elapsed_ms": elapsed_ms,
        "items": _count_items(parsed),
        "chars": len(raw), "parsed": parsed, "raw": raw if parsed is None else None,
    }


# ── 串流呼叫（邊收邊回文字 delta，給前端漸進渲染）────────────
def _stream_gemini(image_b64: str, mime: str, model: str):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺 GEMINI_API_KEY 環境變數")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
    body = {
        "systemInstruction": {"parts": [{"text": PROMPT}]},
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": mime, "data": image_b64}},
            {"text": USER_TEXT},
        ]}],
        "generationConfig": {"maxOutputTokens": 8192},
    }
    with requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=120, stream=True) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                ev = json.loads(payload)
            except json.JSONDecodeError:
                continue
            for cand in ev.get("candidates", []):
                for p in cand.get("content", {}).get("parts", []):
                    if p.get("text"):
                        yield p["text"]


def _stream_claude(image_b64: str, mime: str, model: str):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺 ANTHROPIC_API_KEY 環境變數")
    body = {
        "model": model, "max_tokens": 8192, "stream": True, "system": PROMPT,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": image_b64}},
            {"type": "text", "text": USER_TEXT},
        ]}],
    }
    with requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json=body, timeout=120, stream=True,
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                ev = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                if ev["delta"].get("text"):
                    yield ev["delta"]["text"]


STREAMERS = {"gemini": _stream_gemini, "claude": _stream_claude}


def stream_menu(provider: str, image_b64: str, mime: str = "image/jpeg", model: str = None):
    fn = STREAMERS.get(provider)
    if not fn:
        raise ValueError(f"未知 provider：{provider}（可用：{', '.join(STREAMERS)}）")
    model = model or DEFAULT_MODELS[provider]
    return fn(image_b64, mime, model)


def _count_items(parsed) -> int:
    """攤平計數：支援 v5 分類分組與舊扁平格式。"""
    n = 0
    for o in parsed or []:
        if not isinstance(o, dict) or o.get("type") == "scan_summary":
            continue
        if o.get("type") == "category" and isinstance(o.get("items"), list):
            n += len(o["items"])
        elif o.get("name"):
            n += 1
    return n
