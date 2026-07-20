# -*- coding: utf-8 -*-
"""每週一鍵匯出：從 Google 試算表拉出筆記+假設，
生成 hypotheses/*.json（給 Codex 回測）與 週複核/*.md（給 GPT 審閱）。"""
import os
import re
import sys
import json
import datetime
import requests
from dotenv import load_dotenv

try:                                    # 讓中文/emoji 在 Windows 主控台不會崩
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))     # ...\line_bot
ROOT = os.path.dirname(BASE)                          # ...\股票導師系統
HYP_DIR = os.path.join(ROOT, "hypotheses")
REV_DIR = os.path.join(ROOT, "週複核")
load_dotenv(os.path.join(BASE, ".env"))

URL = os.environ["SHEET_WEBAPP_URL"]
TOKEN = os.environ["SHEET_TOKEN"]

PROMPT = """---
📋 給 GPT / Codex 的複核指令：
你是投資教育複核員兼量化研究助理。檢查本檔的學習筆記與假設：
1. 找出錯誤/過度簡化/危險推論，並改寫成更精準版本
2. 標出哪些只是觀念不能回測、哪些可轉成明確回測規則
3. 檢查每個 rule_json 是否偷看未來（look-ahead / 倖存者偏誤）
4. 補上缺的欄位；產生修正版 hypotheses JSON
5. 可回測的用「驗證引擎」的 cache_*.json + causal.db 寫程式回測並跑
限制：不報明牌、不給買賣建議、不憑空想像結果；結果須由程式讀歷史資料算出；資料不足要講明。"""


def safe(name):
    return re.sub(r"[^0-9A-Za-z_\-]", "", str(name)) or "HYP"


def main():
    os.makedirs(HYP_DIR, exist_ok=True)
    os.makedirs(REV_DIR, exist_ok=True)
    try:
        r = requests.post(URL, json={"action": "export", "token": TOKEN,
                                     "since": "0000-00-00"}, timeout=60).json()
    except Exception as e:
        print("❌ 連不到試算表：", e)
        return
    if not r.get("ok"):
        print("❌ 匯出失敗：", r)
        print("   （如果是 unknown action，代表 Apps Script 還沒更新到含 export 的版本）")
        return

    lessons = r.get("lessons", [])
    hyps = r.get("hypotheses", [])

    # 1) 每個假設寫成一個 JSON 檔（給 Codex 回測）
    n_json = 0
    for h in hyps:
        rj = h.get("rule_json", "")
        hid = safe(h.get("hypothesis_id", ""))
        if not rj:
            continue
        try:
            obj = json.loads(rj)
        except Exception:
            obj = {"id": hid, "rule_json_raw": rj}
        with open(os.path.join(HYP_DIR, f"{hid}.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        n_json += 1

    # 2) 本週複核 markdown
    today = datetime.date.today()
    monday = (today - datetime.timedelta(days=today.weekday())).isoformat()
    wk_l = [x for x in lessons if str(x.get("created_at", ""))[:10] >= monday]
    wk_h = [x for x in hyps if str(x.get("created_at", ""))[:10] >= monday]

    lines = [f"# 每週股票學習複核（{monday} ~ {today.isoformat()}）", ""]
    lines.append(f"## 本週學習筆記（{len(wk_l)} 筆）")
    for x in wk_l:
        note = (x.get("corrected_note") or x.get("ai_explanation")
                or x.get("user_understanding", ""))
        lines.append(f"- 【{x.get('source_type','')}】{x.get('title','')}：{str(note)[:150]}")
    lines.append("")
    lines.append(f"## 本週候選假設（{len(wk_h)} 筆；全部 {len(hyps)} 筆已輸出到 hypotheses/）")
    for x in wk_h:
        lines.append(f"### {x.get('hypothesis_id','')}（{x.get('category','')}）")
        lines.append(f"- 假設：{x.get('hypothesis','')}")
        lines.append(f"- rule_json：{x.get('rule_json','')}")
    lines.append("")
    lines.append(PROMPT)

    md_path = os.path.join(REV_DIR, f"weekly_review_{today.isoformat()}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ 已輸出 {n_json} 個假設 JSON → hypotheses\\")
    print(f"✅ 本週複核 markdown → 週複核\\weekly_review_{today.isoformat()}.md")
    print()
    print("── 接下來怎麼給 ──")
    print("【Codex】在『股票導師系統』資料夾開 Codex，貼這句：")
    print("   讀 README_給CODEX.md，用 驗證引擎/ 的 cache_*.json 回測 hypotheses/ 的")
    print("   假設，輸出勝率/報酬/最大回撤/交易次數，並檢查有沒有偷看未來資料。")
    print("【GPT】把 週複核\\weekly_review_*.md 這個檔上傳到 ChatGPT（裡面已含複核指令）。")


if __name__ == "__main__":
    main()
