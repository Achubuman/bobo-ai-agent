# -*- coding: utf-8 -*-
"""波波菜單 OCR 服務（FastAPI）。

架構：HTML 前端 → 本服務 /parse → Gemini/Claude（套 menu_ocr_prompt.md）→ 回 JSON。
重點：API key 放後端環境變數、不暴露在前端；後端代呼叫 → 解掉瀏覽器 CORS。

執行：
    cd ocr_service
    pip install -r requirements.txt
    cp .env.example .env   # 填入 GEMINI_API_KEY / ANTHROPIC_API_KEY
    uvicorn app:app --reload
    瀏覽器開 http://localhost:8000/
"""
import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import providers

load_dotenv()

app = FastAPI(title="波波菜單 OCR 服務")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

FRONTEND = Path(__file__).resolve().parent / "frontend.html"

# 離線示範用：預先驗證過的測試菜單（result_*.json + menu_*.圖）。
# 用途：面試現場若沒網路或 API key 失效，仍可用真實結果渲染前端畫面。
# 只要 uvicorn 有起來即可，完全不需呼叫 Gemini/Claude。
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "test_menus"
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
if SAMPLES_DIR.exists():
    app.mount("/samples_files", StaticFiles(directory=str(SAMPLES_DIR)), name="samples_files")


class ParseReq(BaseModel):
    provider: str = "gemini"          # gemini | claude
    model: Optional[str] = None        # 留空用該家預設
    image_b64: str
    mime: str = "image/jpeg"


@app.get("/", response_class=HTMLResponse)
def home():
    return FRONTEND.read_text("utf-8")


@app.get("/health")
def health():
    return {
        "ok": True,
        "providers": list(providers.CALLERS),
        "gemini_key": bool(os.environ.get("GEMINI_API_KEY")),
        "claude_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


@app.post("/parse")
def parse(req: ParseReq):
    try:
        return providers.parse_menu(req.provider, req.image_b64, req.mime, req.model)
    except Exception as e:  # noqa: BLE001 — demo 服務，回傳錯誤給前端顯示
        return {"ok": False, "error": providers.redact(str(e)),
                "provider": req.provider, "model": req.model}


@app.post("/parse_stream")
def parse_stream(req: ParseReq):
    """串流版：邊收模型輸出邊回前端（SSE），讓畫面漸進顯示品項。
    每則事件為 {"text": "..."}；錯誤回 {"error": "..."}（已遮蔽 key）。"""
    def gen():
        try:
            for chunk in providers.stream_menu(req.provider, req.image_b64, req.mime, req.model):
                yield "data: " + json.dumps({"text": chunk}, ensure_ascii=False) + "\n\n"
        except Exception as e:  # noqa: BLE001
            yield "data: " + json.dumps({"error": providers.redact(str(e))}, ensure_ascii=False) + "\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


# 開團原型（位於上層 公開作品集/），讓「用此菜單開團」一條龍在後端版也能跳轉。
_PARENT = Path(__file__).resolve().parent.parent


@app.get("/bobo-admin-panel-prototype.html", response_class=HTMLResponse)
def admin_panel():
    return (_PARENT / "bobo-admin-panel-prototype.html").read_text("utf-8")


@app.get("/bobo-group-chat-prototype.html", response_class=HTMLResponse)
def group_chat():
    return (_PARENT / "bobo-group-chat-prototype.html").read_text("utf-8")


@app.get("/menu_ocr_demo.html", response_class=HTMLResponse)
def single_file_demo():
    return (_PARENT / "menu_ocr_demo.html").read_text("utf-8")


@app.get("/samples")
def samples():
    """列出可離線示範的測試菜單：每筆配對 result JSON 與菜單圖片。

    回傳 {ok, samples:[{id, label, items, confidence, json_name, img_name}]}。
    前端用 /samples_files/<name> 取檔（檔名含中文，前端需 encodeURIComponent）。
    """
    if not SAMPLES_DIR.exists():
        return {"ok": True, "samples": []}
    out = []
    for rj in sorted(SAMPLES_DIR.glob("result_*.json")):
        stem = rj.stem[len("result_"):]            # 例：TC-04-01_小木屋鬆餅
        label = stem.split("_", 1)[1] if "_" in stem else stem
        img_name = next(
            (f"menu_{stem}{ext}" for ext in _IMG_EXTS
             if (SAMPLES_DIR / f"menu_{stem}{ext}").exists()),
            None,
        )
        items, conf = None, None
        try:
            data = json.loads(rj.read_text("utf-8"))
            summary = next((o for o in data if isinstance(o, dict)
                            and o.get("type") == "scan_summary"), None)
            if summary:
                conf = summary.get("overall_confidence")
            items = sum(1 for o in data if isinstance(o, dict)
                        and o.get("name") and o.get("type") != "scan_summary")
        except Exception:  # noqa: BLE001 — 壞檔不擋住其它樣本
            pass
        out.append({
            "id": stem, "label": label, "items": items, "confidence": conf,
            "json_name": rj.name, "img_name": img_name,
        })
    return {"ok": True, "samples": out}
