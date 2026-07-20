# -*- coding: utf-8 -*-
"""建立/上傳 LINE 圖文選單並設為所有人預設。先跑 make_menu.py 產生 richmenu.png 再跑這支。"""
import os, requests
from dotenv import load_dotenv

BASE = "C:/Users/User/Desktop/股票導師系統/line_bot"
load_dotenv(f"{BASE}/.env")

TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
H = {"Authorization": f"Bearer {TOKEN}"}
IMG = f"{BASE}/richmenu.png"

W, Ht = 2500, 1686
cw = [833, 833, 834]
x = [0, 833, 1666]
rh = 843


def area(cx, cy, w, h, text):
    return {"bounds": {"x": cx, "y": cy, "width": w, "height": h},
            "action": {"type": "message", "text": text}}


# 順序要跟 make_menu.py 的 cells 一致
cells = [
    ("想法", 0), ("筆記", 1), ("學一課", 2),
    ("整理本週", 0), ("操作手冊", 1), ("選單", 2),
]
areas = [area(x[col], (i // 3) * rh, cw[col], rh, text)
         for i, (text, col) in enumerate(cells)]

body = {
    "size": {"width": W, "height": Ht},
    "selected": True,
    "name": "stock-tutor-menu-v1",
    "chatBarText": "功能選單 ▾",
    "areas": areas,
}

# 1) 刪掉舊選單（避免累積）
old = requests.get("https://api.line.me/v2/bot/richmenu/list", headers=H).json()
for m in old.get("richmenus", []):
    requests.delete(f"https://api.line.me/v2/bot/richmenu/{m['richMenuId']}", headers=H)
    print("刪除舊選單", m["richMenuId"])

# 2) 建立選單
r = requests.post("https://api.line.me/v2/bot/richmenu",
                  headers={**H, "Content-Type": "application/json"}, json=body)
print("create:", r.status_code, r.text)
rid = r.json()["richMenuId"]

# 3) 上傳圖片
with open(IMG, "rb") as f:
    r2 = requests.post(f"https://api-data.line.me/v2/bot/richmenu/{rid}/content",
                       headers={**H, "Content-Type": "image/png"}, data=f.read())
print("upload:", r2.status_code, r2.text or "(ok)")

# 4) 設為所有人預設
r3 = requests.post(f"https://api.line.me/v2/bot/user/all/richmenu/{rid}", headers=H)
print("setdefault:", r3.status_code, r3.text or "(ok)")
print("DONE richMenuId =", rid)
