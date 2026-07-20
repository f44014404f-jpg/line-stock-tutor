# -*- coding: utf-8 -*-
"""畫 LINE 圖文選單圖：彩色字卡風。改按鈕就改 cells，重跑後再跑 setup_richmenu.py。"""
from PIL import Image, ImageDraw, ImageFont

BASE = "C:/Users/User/Desktop/股票導師系統/line_bot"
W, H = 2500, 1686
PAD, GAP = 44, 32
BG = (15, 23, 42)

CN = "C:/Windows/Fonts/msjhbd.ttc"          # 微軟正黑體 粗
EMO = "C:/Windows/Fonts/seguiemj.ttf"        # 彩色 emoji
f_big = ImageFont.truetype(CN, 140)
f_sub = ImageFont.truetype(CN, 50)
f_emo = ImageFont.truetype(EMO, 140)

# (emoji, 大標, 副標, 卡片色) —— 按鈕點下去會送出「大標」那個字給 bot
cells = [
    ("💡", "想法", "轉可回測假設", (13, 148, 136)),
    ("🧠", "記起來", "問答存成筆記", (37, 99, 235)),
    ("📘", "學一課", "教一個觀念",   (234, 88, 12)),
    ("🗓", "整理本週", "產週複核",   (124, 58, 237)),
    ("📖", "操作手冊", "完整使用說明", (71, 85, 105)),
    ("🧭", "選單", "功能總覽",       (219, 39, 119)),
]

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
cols, rows = 3, 2
cw = (W - 2 * PAD - (cols - 1) * GAP) / cols
ch = (H - 2 * PAD - (rows - 1) * GAP) / rows


def ctext(cx, y, text, font, fill, emoji=False):
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    if emoji:
        d.text((cx - (r - l) / 2, y), text, font=font, embedded_color=True)
    else:
        d.text((cx - (r - l) / 2, y), text, font=font, fill=fill)


for i, (emo, title, sub, col) in enumerate(cells):
    c, r = i % cols, i // cols
    x0 = PAD + c * (cw + GAP)
    y0 = PAD + r * (ch + GAP)
    cx = x0 + cw / 2
    d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], radius=48, fill=col)
    ctext(cx, y0 + ch * 0.14, emo, f_emo, None, emoji=True)
    ctext(cx, y0 + ch * 0.46, title, f_big, (255, 255, 255))
    ctext(cx, y0 + ch * 0.78, sub, f_sub, (240, 244, 255))

img.save(f"{BASE}/richmenu.png", "PNG")
print("saved richmenu.png", img.size)
