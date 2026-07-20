"""
LINE 股票學習導師
────────────────────────────────────────────────────────
定位：投資「教育 + 研究紀錄」，不是報明牌、不給買賣建議。
流程：看書想法 → LINE 隨手記 → AI 幫你分類/追問/轉成可驗證假設
      → 存 Google 試算表 → 週日匯出 → 丟 GPT/Codex 用驗證引擎回測。

- LINE Messaging API（Reply 模式，永久免費）
- Google Gemini（gemini-2.5-flash-lite，免費額度大）
- Google 試算表（透過 Apps Script Web App 存 lessons / hypotheses / state）

模式（打這些字切換，會被記住）：
    導師 / 聊天   → 一般股票教育問答（預設）
    學一課        → 每次教一個投資觀念 + 出 2 題確認理解

指令（任何模式都能用）：
    想法：<你的想法>     → 分類 A/B/C/D，可回測就轉成假設 JSON 存起來
    筆記：<讀書心得>     → 整理成學習筆記存起來
    整理本週             → 產生 weekly_review，附給 GPT/Codex 的複核 prompt
    /help  /選單  /操作手冊
"""

import os
import re
import json
import time
import datetime

import requests
from flask import Flask, request, abort

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage,
    FlexMessage, FlexContainer,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent

from google import genai
from google.genai import types


# ---------- 金鑰 / 設定 ----------
LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
SHEET_URL = os.environ.get("SHEET_WEBAPP_URL")
SHEET_TOKEN = os.environ.get("SHEET_TOKEN")

app = Flask(__name__)
configuration = Configuration(access_token=LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)
gemini = genai.Client(api_key=GEMINI_KEY)
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

TW = datetime.timezone(datetime.timedelta(hours=8))
tw_today = lambda: datetime.datetime.now(TW).date().isoformat()
tw_now = lambda: datetime.datetime.now(TW).strftime("%Y-%m-%d %H:%M")

DISCLAIMER = "＊這是投資教育與研究紀錄，不是買賣建議；任何策略都要回測與風控。"

# 驗證引擎實際有的資料欄位 —— 給 AI 產生假設時只能用這些，確保 Codex 回測得動
DATA_FIELDS = """
可用資料欄位（驗證引擎 cache_*.json / causal.db 實際有的，只能用這些）：
[價量 日K 2015起] close, open, high, low, volume, ma5, ma20, ma60, ma120, ma240,
                  high_Nd(N日新高), return_Nd(N日報酬), vol_ma20, 量比
[月營收 2019起]   revenue_yoy(%), revenue_mom(%), revenue_yoy_positive_months(連續正成長月數)
[三大法人 2015起] foreign_net(外資淨買賣超張), trust_net(投信淨), foreign_buy_days(連買天數)
[融資融券]        margin(融資餘額), margin_chg(增減), short(融券)
[集保大戶 週]     over400_pct(400張大戶%), over1000_pct(千張大戶%), over400_chg_1w(週變化)
[因果庫]          theme(產業題材), event(事件), supply_chain_role(供應鏈角色),
                  revenue_sensitivity(營收敏感度)
""".strip()


# ---------- 試算表（呼叫 Apps Script）----------
def sheet_call(payload: dict, retries: int = 2):
    if not SHEET_URL or not SHEET_TOKEN:
        return None
    payload["token"] = SHEET_TOKEN
    for attempt in range(retries + 1):
        try:
            return requests.post(SHEET_URL, json=payload, timeout=30).json()
        except Exception as e:
            print(f"sheet_call error (try {attempt + 1}):", e)
    return None


def get_state(user):
    res = sheet_call({"action": "getstate", "user": user})
    if not res or not res.get("ok"):
        return "chat", ""
    return (res.get("mode") or "chat"), (res.get("pending") or "")


def set_state(user, mode, pending=""):
    sheet_call({"action": "setstate", "user": user, "mode": mode, "pending": pending})


def add_lesson(user, row: dict):
    row.update({"action": "add_lesson", "user": user})
    return sheet_call(row)


def add_hypothesis(user, row: dict):
    row.update({"action": "add_hypothesis", "user": user})
    return sheet_call(row)


def list_since(user, kind, since):
    """kind = 'lessons' | 'hypotheses'；回傳本週（含）以後的紀錄。"""
    res = sheet_call({"action": f"list_{kind}", "user": user, "since": since})
    if not res or not res.get("ok"):
        return []
    return res.get("rows", [])


def count_hypotheses(user):
    res = sheet_call({"action": "count_hypotheses", "user": user})
    return (res or {}).get("count", 0)


# ---------- Gemini ----------
def ask_gemini(system_prompt: str, user_text: str, temp=0.6) -> str:
    last = ""
    for attempt in range(3):
        try:
            resp = gemini.models.generate_content(
                model=MODEL, contents=user_text,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt, temperature=temp),
            )
            return (resp.text or "").strip() or "（沒有回傳內容，再試一次）"
        except Exception as e:
            last = str(e)
            transient = any(k in last for k in
                            ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED",
                             "overloaded", "high demand"))
            if transient and attempt < 2:
                time.sleep(1.5)
                continue
            break
    return f"⚠️ 呼叫 Gemini 失敗：{last}"


def extract_json(s: str):
    """從 AI 回覆裡挖出第一個 JSON 物件。"""
    if not s:
        return None
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except Exception:
        return None


SHORT = "回答精簡、適合手機閱讀，最多 8 行；每個重點各自一行、可用「‧」開頭條列，不要一大段擠在一起。"

# 一般股票教育家教
TUTOR = (
    "你是務實的台股投資「教育」導師，對象是認真學習、會看書做功課的散戶。"
    "用繁體中文，把觀念講清楚、給白話例子。"
    "重要界線：只做觀念教學與思路引導，絕不報明牌、不預測特定個股漲跌、不給買賣點。"
    "你看不到任何即時或歷史的股價/營收/籌碼資料。若使用者問特定個股的具體數字"
    "（例如某股營收YoY、目前股價、法人買超張數），要老實說『我看不到即時資料，"
    "這要用你本機的股票系統或 Codex 去查真實數據』，絕對不要自己編造數字。"
    f"若被要求報明牌就婉拒並把問題導回『可驗證的規則』。{SHORT}"
)


# ---------- LINE Flex 卡片（讓回覆排版清楚，不再是一坨字）----------
CAT_ACCENT = {"A": "#16A34A", "B": "#D97706", "C": "#2563EB", "D": "#6B7280"}
CAT_TAG = {"A": "🟢 A ‧ 可直接回測", "B": "🟡 B ‧ 需補條件",
           "C": "🔵 C ‧ 觀念檢查表", "D": "⚪ D ‧ 太模糊"}


def info_card(accent, tag, title, rows, footer=None, alt=None):
    """通用資訊卡：彩色標題 + 若干（小標, 內文）段落；內文空的會略過。"""
    body = []
    for label, value in rows:
        if not value:
            continue
        if label:
            body.append({"type": "text", "text": label, "size": "xs",
                         "weight": "bold", "color": accent, "margin": "md"})
        body.append({"type": "text", "text": str(value), "size": "sm",
                     "wrap": True, "color": "#333333",
                     "margin": "xs" if label else "sm"})
    bubble = {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": accent,
                   "paddingAll": "16px", "spacing": "xs", "contents": [
                       {"type": "text", "text": tag, "size": "xs",
                        "color": "#FFFFFFDD", "wrap": True},
                       {"type": "text", "text": title, "size": "lg", "weight": "bold",
                        "color": "#FFFFFF", "wrap": True}]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "16px",
                 "spacing": "none", "contents": body or [
                     {"type": "text", "text": "—", "size": "sm", "color": "#999999"}]},
    }
    if footer:
        bubble["footer"] = {"type": "box", "layout": "vertical", "paddingAll": "12px",
                            "contents": [{"type": "text", "text": footer, "size": "xxs",
                                          "color": "#AAAAAA", "wrap": True}]}
    return {"flex": bubble, "alt": alt or title}


# ---------- 「學一課」：教一個觀念 + 出題 ----------
LESSON_TOPICS = [
    "月營收 YoY 與股價的關係", "均線多頭排列的意義與陷阱", "成交量與價格的配合",
    "三大法人買賣超怎麼看才不會被騙", "融資融券透露的散戶情緒", "集保大戶持股比例的訊號",
    "毛利率與營益率看什麼", "本益比 / 股價淨值比的適用與誤用", "停損與部位控制為何比選股重要",
    "產業供應鏈與題材輪動", "營收認列與旺淡季的季節性", "突破新高 vs 追高的差別",
]


def teach_lesson(user):
    # 用已上過的課數決定教哪一課，循序不重複
    done = count_hypotheses(user)  # 借用計數當輪替種子即可
    topic = LESSON_TOPICS[done % len(LESSON_TOPICS)]
    sys = (
        f"你是台股投資教育導師。用繁體中文教『{topic}』這個觀念，"
        "結構：①一句話定義 ②為什麼重要 ③一個台股情境例子 ④最常見的誤用/陷阱。"
        f"最後用一行『🤔 想想看：』出 1 個開放式問題讓學生思考（不要給答案）。{SHORT}"
    )
    body = ask_gemini(sys, f"教我：{topic}")
    add_lesson(user, {"source_type": "daily_lesson", "title": topic,
                      "ai_explanation": body[:2000], "user_understanding": "",
                      "created_at": tw_now()})
    return info_card("#0D9488", "📘 今日一課", topic, [("", body)],
                     footer="有想法就打「想法：…」記下來　·　" + DISCLAIMER,
                     alt=f"今日一課：{topic}")


# ---------- 「筆記：」讀書心得整理 ----------
def handle_note(user, body):
    if not body:
        return "用法：筆記：我今天看第3章毛利率，我理解是……\n（我幫你整理成學習筆記存起來）"
    sys = (
        "你是投資讀書筆記整理員。使用者給一段讀書心得，請你："
        "①用 2-4 句整理他的『理解重點』（用他的話，不要照抄整本書）"
        "②指出 1 個他可能還不確定或需要查證的地方。"
        "不要長篇，不要幫他下結論。繁體中文。"
    )
    tidy = ask_gemini(sys, body)
    add_lesson(user, {"source_type": "book_note", "title": body[:30],
                      "ai_explanation": "", "user_understanding": body[:2000],
                      "corrected_note": tidy[:2000], "created_at": tw_now()})
    return info_card("#2563EB", "📝 已存成學習筆記",
                     (body[:22] + "…") if len(body) > 22 else body,
                     [("重點整理", tidy)],
                     footer="若能變成可驗證規則，改打「想法：…」　·　" + DISCLAIMER,
                     alt="學習筆記")


# ---------- 「想法：」核心 —— 分類 A/B/C/D + 轉假設 JSON ----------
CLASSIFY_SYS = f"""你是量化投資研究助理。使用者給一個從書上/觀察得到的投資「想法」。
你的工作是把它分類，並且【只在可回測時】轉成結構化假設，交給程式做歷史回測。

分類定義：
A = 可直接回測（條件明確、用得到下列資料欄位）
B = 需要補條件才可回測（方向對，但缺門檻/期間/定義）
C = 只是好觀念，適合放進檢查表，不適合回測
D = 太模糊，先不要用

{DATA_FIELDS}

【最重要的防污染規則】
- 規則只能用「進場當下就能知道」的資訊，嚴禁使用任何要事後才知道的結果。
- 不可把「你知道後來哪些股票漲了」的特徵偷偷寫進條件（避免倖存者/未來函數偏誤）。
- 若想法本身依賴事後結果（例如「找出會漲的股票」），一律標為 D。
- 不要指名任何個股，只描述『可套用到全市場的規則』。

請「只」輸出一個 JSON（前後不要有多餘文字），格式：
{{
  "category": "A/B/C/D 其中一個",
  "reason": "一句話說明為何這樣分類（繁中）",
  "followup": "若是 B，問使用者需要補的 1 個關鍵條件；其他類填空字串",
  "hypothesis": {{
     "hypothesis": "用一句話寫出可檢驗的假設（繁中）",
     "market": "TW",
     "universe": "listed_and_otc",
     "entry_rules": [ {{"field": "欄位名", "operator": ">/>=/</<=/==", "value": 數字或欄位名, "unit": "percent/張/日 等(可省)"}} ],
     "exit_rules":  [ {{"field": "holding_days", "operator": ">=", "value": 20}} ],
     "risk_rules":  [ {{"field": "stop_loss_pct", "operator": "<=", "value": 12}} ],
     "metrics": ["total_return","avg_return","win_rate","max_drawdown","trade_count"],
     "avoid_lookahead_bias": true
  }}
}}
若 category 是 C 或 D，hypothesis 整個填 null。"""


def handle_idea(user, body):
    if not body:
        return ("用法：想法：書裡說連續3個月營收成長的股票比較會漲，我想驗證……\n"
                "我會幫你分類 A/B/C/D，可回測的就轉成假設存起來。")
    raw = ask_gemini(CLASSIFY_SYS, f"想法：{body}", temp=0.3)
    data = extract_json(raw)
    if not data:
        return f"⚠️ 分類時沒抓到結構，AI 原回覆：\n{raw[:400]}\n\n請換句話再說一次這個想法。"

    cat = (data.get("category") or "D").strip()[:1].upper()
    reason = data.get("reason", "")
    followup = data.get("followup", "")
    hyp = data.get("hypothesis")

    # A / B：存成假設 + 產生 JSON 檔內容
    if cat in ("A", "B") and hyp:
        seq = count_hypotheses(user) + 1
        hid = f"HYP-{seq:04d}"
        hyp["id"] = hid
        rule_json = json.dumps(hyp, ensure_ascii=False)
        add_hypothesis(user, {
            "hypothesis_id": hid, "hypothesis": hyp.get("hypothesis", body[:200]),
            "category": cat, "status": "draft", "rule_json": rule_json,
            "source_idea": body[:500], "created_at": tw_now(),
        })
        rows = [("為什麼這樣分類", reason)]
        if cat == "B" and followup:
            rows.append(("還需要你補充", f"{followup}（補完再打一次「想法：…」會更完整）"))
        rows.append(("狀態", f"已存成 {hid}，週日打「整理本週」可匯出給 Codex 回測"))
        return info_card(CAT_ACCENT[cat], f"{CAT_TAG[cat]}　{hid}",
                         hyp.get("hypothesis", body[:60]), rows,
                         footer=DISCLAIMER, alt=f"{hid}（{cat}類假設）")

    # C / D：只給回饋，不存假設（C 可存成檢查表 lesson）
    if cat == "C":
        add_lesson(user, {"source_type": "checklist", "title": body[:30],
                          "user_understanding": body[:500], "corrected_note": reason,
                          "created_at": tw_now()})
        return info_card(CAT_ACCENT["C"], CAT_TAG["C"], body[:60],
                         [("導師說", reason),
                          ("怎麼用", "這類不適合回測，但選股時可放進檢查表提醒自己")],
                         footer=DISCLAIMER, alt="C類（觀念檢查表）")
    return info_card(CAT_ACCENT["D"], CAT_TAG["D"], body[:60],
                     [("導師說", reason),
                      ("怎麼改才能驗證",
                       "加上『明確門檻＋期間＋資料欄位』，例：連續3個月營收YoY>20%且股價站上季線")],
                     footer=DISCLAIMER, alt="D類（太模糊）")


# ---------- 「整理本週」weekly_review ----------
REVIEW_PROMPT = """——————————
📋 複製下面整段丟給 GPT / Codex：

你是投資教育複核員兼量化研究助理。請檢查我這週的學習筆記與假設 JSON：
1. 找出錯誤、過度簡化、危險推論，並改寫成更精準版本
2. 標出哪些只是觀念不能回測、哪些可轉成明確回測規則
3. 檢查每個 rule_json 是否偷看未來資料（look-ahead / 倖存者偏誤）
4. 補上缺少的資料欄位或門檻
5. 產生修正版 hypotheses JSON
6. 可回測的，請用「驗證引擎」資料夾的 cache_*.json + causal.db 寫回測程式並跑
限制：不報明牌、不給買賣建議、不憑空想像回測結果；結果必須由程式讀歷史資料算出；資料不足要明講缺什麼。"""


def handle_weekly(user):
    # 取本週一以來的紀錄
    today = datetime.datetime.now(TW).date()
    monday = (today - datetime.timedelta(days=today.weekday())).isoformat()
    lessons = list_since(user, "lessons", monday)
    hyps = list_since(user, "hypotheses", monday)

    if not lessons and not hyps:
        return "這週還沒有紀錄。先打「想法：…」或「筆記：…」累積幾筆，週日再來「整理本週」。"

    lines = [f"# 每週股票學習複核（{monday} ~ {today.isoformat()}）", ""]
    lines.append(f"## 本週學習筆記（{len(lessons)} 筆）")
    for r in lessons:
        t = r.get("title", "")
        note = r.get("corrected_note") or r.get("ai_explanation") or r.get("user_understanding", "")
        lines.append(f"- 【{r.get('source_type','')}】{t}：{note[:120]}")
    lines.append("")
    lines.append(f"## 候選回測假設（{len(hyps)} 筆）")
    for r in hyps:
        lines.append(f"### {r.get('hypothesis_id','')}（{r.get('category','')}）")
        lines.append(f"- 假設：{r.get('hypothesis','')}")
        lines.append(f"- rule_json：{r.get('rule_json','')}")
    md = "\n".join(lines)

    # LINE 單則有長度限制，太長就只回摘要 + 提示去試算表看
    head = f"🗓 本週複核：筆記 {len(lessons)} 筆、假設 {len(hyps)} 筆\n"
    full = head + "\n" + md + "\n" + REVIEW_PROMPT
    if len(full) > 4500:
        return (head + "（內容較長，完整 weekly_review 已在試算表 hypotheses/lessons 分頁，"
                "把可回測的 rule_json 貼進『驗證引擎/hypotheses/』資料夾給 Codex 即可）\n\n"
                + REVIEW_PROMPT)
    return full


# ---------- 說明文字 ----------
WELCOME = (
    "👋 歡迎使用「股票學習導師」！\n"
    "我不是報明牌工具，是幫你把『看書的想法』變成『可驗證的假設』的學習夥伴 📚\n\n"
    "🧭 三個主要用法：\n"
    "• 想法：<一句話> → 我分類 A/B/C/D，可回測的就轉成假設存起來\n"
    "• 筆記：<讀書心得> → 幫你整理成學習筆記\n"
    "• 整理本週 → 產出週複核，附一段可直接丟 GPT/Codex 的 prompt\n\n"
    "📘 打「學一課」→ 每次教你一個投資觀念\n"
    "💬 其他時候直接問我股票觀念就好（我只教觀念、不報明牌）\n\n"
    "💡 隨時打「操作手冊」看完整說明。\n" + DISCLAIMER
)

HELP = (
    "🧭 用法：\n"
    "• 想法：<想法> → 分類+轉可回測假設\n"
    "• 筆記：<心得> → 整理成學習筆記\n"
    "• 整理本週 → 週複核 + 給 Codex 的 prompt\n"
    "• 學一課 → 教一個觀念\n"
    "• 直接發問 → 一般股票教育問答\n\n"
    "💡 打「選單」看歡迎說明、「操作手冊」看完整教學。"
)

MANUAL = (
    "📖 操作手冊 ─ 股票學習導師\n"
    "━━━━━━━━━━━━━━\n"
    "這套的核心是：看書想法 → 記下來 → AI 幫你變成可回測的規則 → 週日交給 GPT/Codex 用歷史資料驗證。\n\n"
    "【① 想法：】最重要\n"
    "打「想法：」再接一句你看書得到的點子，例如：\n"
    "　想法：連續3個月營收YoY超過20%的股票，站上季線後比較會續漲\n"
    "我會回你：\n"
    "　🟢A 可直接回測 / 🟡B 需補條件 / 🔵C 檢查表 / ⚪D 太模糊\n"
    "可回測的會自動存成假設（HYP-0001…），並產生 rule_json。\n"
    "→ B 類我會問你缺哪個門檻，補完再打一次會更完整。\n\n"
    "【② 筆記：】\n"
    "打「筆記：」接讀書心得，我幫你整理重點、點出要查證的地方，存進 lessons。\n\n"
    "【③ 整理本週】\n"
    "週日打「整理本週」→ 我把這週的筆記與假設整理成 weekly_review，\n"
    "並附一段可直接複製給 GPT / Codex 的複核 prompt。\n"
    "把可回測的 rule_json 放進『驗證引擎/hypotheses/』，叫 Codex 用 cache 資料回測即可。\n\n"
    "【④ 學一課 / 一般問答】\n"
    "打「學一課」每次教一個觀念；其他時候直接問股票觀念。\n\n"
    "【界線】只做投資教育與研究，不報明牌、不給買賣建議。\n" + DISCLAIMER
)


# ---------- 路由 ----------
def strip_prefix(s, prefixes):
    for p in prefixes:
        if s.startswith(p):
            return s[len(p):].lstrip("：: ").strip()
    return None


def route(text, user):
    s = text.strip()

    # 指令 / 說明
    if s in ("/help", "help", "?", "說明"):
        return HELP
    if s in ("選單", "開始", "menu", "使用說明", "怎麼用"):
        return WELCOME
    if s in ("操作手冊", "手冊", "說明書", "教學手冊", "使用手冊"):
        return MANUAL

    # 想法：/ 筆記：
    idea = strip_prefix(s, ("想法", "點子", "假設"))
    if idea is not None:
        return handle_idea(user, idea)
    note = strip_prefix(s, ("筆記", "讀書筆記", "心得"))
    if note is not None:
        return handle_note(user, note)

    # 整理本週
    if s in ("整理本週", "本週", "週複核", "整理這週", "weekly"):
        return handle_weekly(user)

    # 學一課
    if s in ("學一課", "今天學一課", "教我一課", "上課", "一課"):
        return teach_lesson(user)

    # 模式切換
    if s in ("導師", "聊天", "問答", "一般"):
        set_state(user, "chat", "")
        return "已回到一般教育問答，直接問我股票觀念就好（我只教觀念、不報明牌）。"

    # 預設：一般股票教育問答
    return ask_gemini(TUTOR, s)


# ---------- LINE Webhook ----------
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


def to_messages(result):
    items = result if isinstance(result, list) else [result]
    msgs = []
    for it in items:
        if isinstance(it, dict) and it.get("flex"):
            msgs.append(FlexMessage(alt_text=it.get("alt", "訊息"),
                                    contents=FlexContainer.from_dict(it["flex"])))
        else:
            msgs.append(TextMessage(text=str(it)[:4900]))
    return msgs[:5]


@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event):
    user_id = getattr(event.source, "user_id", "unknown")
    reply = route(event.message.text, user_id)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=event.reply_token,
                                messages=to_messages(reply))
        )


@handler.add(FollowEvent)
def on_follow(event):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=event.reply_token,
                                messages=[TextMessage(text=WELCOME)])
        )


@app.route("/")
def health():
    return "LINE Stock Tutor is running."


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    app.run(host="0.0.0.0", port=port)
