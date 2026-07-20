# LINE 股票學習導師 — 部署說明

把「看書的想法」變成「可驗證的假設」的 LINE bot。只做投資教育與研究紀錄，**不報明牌、不給買賣建議**。

## 花費：LINE 這塊 0 元
- 接收訊息、**回覆訊息（Reply API）永久免費、無上限** ← 這個 bot 幾乎只用這條
- 主動推播 Push 免費方案每月 200 則，這個 bot 用不到
- Gemini 用 `gemini-2.5-flash-lite`，免費額度足夠自用
- 試算表、Render Free：免費

## 架構
```
LINE ──webhook──> Render(Flask, app.py) ──> Gemini(分類/教學)
                                        └──> Google 試算表(Apps Script) 存 lessons/hypotheses/state
```

## 部署步驟
1. **LINE**：建立官方帳號 → LINE Developers 開 Messaging API channel
   - 拿 `Channel access token`、`Channel secret`
   - Webhook URL 填 `https://你的render網址/callback`，開啟 Webhook、關閉自動回覆
2. **Gemini**：Google AI Studio 申請免費 API key
3. **試算表**：新開一個 Google 試算表 → 擴充功能 → Apps Script
   - 貼上 `sheet_apps_script.gs`，把 `TOKEN` 改成自訂密碼
   - 部署成「網頁應用程式」（執行身分：我；存取：所有人），複製 URL
4. **Render**：New → Web Service，連這個 repo（或上傳 line_bot 資料夾）
   - Build：`pip install -r requirements.txt`
   - Start：`gunicorn app:app`
   - Environment：照 `.env.example` 填 5 組變數（`SHEET_TOKEN` 要跟第3步一致）
5. 加自己好友，傳「選單」測試。

## 指令
| 你打 | bot 做什麼 |
|---|---|
| `想法：連續3個月營收YoY>20%站上季線比較會漲` | 分類 A/B/C/D，可回測就轉成 `HYP-000x` 假設存起來 |
| `筆記：今天看毛利率那章，我理解是…` | 整理成學習筆記存起來 |
| `整理本週` | 產出 weekly_review + 一段可直接丟 GPT/Codex 的複核 prompt |
| `學一課` | 每次教一個投資觀念 |
| 直接發問 | 一般股票教育問答（只教觀念、不報明牌） |

## 每週驗證流程
1. 平日用 bot 累積想法 → 存進試算表 `hypotheses` 分頁
2. 週日打「整理本週」，把可回測的 `rule_json` 貼進 `../hypotheses/` 資料夾（一個假設一個 .json）
3. 叫 Codex 讀 `../驗證引擎/` 的 `cache_*.json` + `causal.db` 跑回測（見上層 `README_給CODEX.md`）
