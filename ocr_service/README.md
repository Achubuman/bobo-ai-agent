# 波波菜單 OCR 服務（FastAPI + Gemini/Claude）

後端架構版 demo：前端不持有 API key、後端代呼叫 → **藏 key + 解 CORS**。
同一份 `../menu_ocr_prompt.md` 餵給 Gemini 或 Claude（前端可切換）。

## 架構
```
HTML 前端 ──POST /parse──▶ FastAPI ──▶ Gemini Vision / Claude（套 menu_ocr_prompt.md）──▶ JSON
   ▲                                                                                      │
   └──────────────────────── 渲染（store_defaults 繼承 / 團主策展 / 套餐加購 / 時價）◀──────┘
```

## 執行
```bash
cd ocr_service
pip install -r requirements.txt
cp .env.example .env        # 填入 GEMINI_API_KEY（免費）和/或 ANTHROPIC_API_KEY
uvicorn app:app --reload
# 瀏覽器開 http://localhost:8000/
```
> 注意：要透過 `http://localhost:8000/` 開（由後端服務前端），不要直接雙擊 frontend.html（那樣 /parse 會找不到後端）。

## API
- `GET /` → 前端頁面
- `GET /health` → 檢查服務與 key 是否就緒
- `POST /parse` body：`{"provider":"gemini|claude","model":null,"image_b64":"...","mime":"image/jpeg"}`
  回傳：`{ok, provider, model, elapsed_ms, items, chars, parsed:[...]}`（失敗回 `{ok:false, error}`）

## 模型
預設 `gemini-3.5-flash`、`claude-sonnet-4-6`（2026/6，會變動）。前端可在欄位改成你帳號可用的版本。

## 跟評測的關係
這是「真實架構版」demo。要比較模型準確率，仍用 `../eval/score.py`（離線比對 ground truth）。
免費的 Gemini 適合 demo；正式產品若要準確率優先，再依 `../eval` 的數據決定模型或做模型路由。
