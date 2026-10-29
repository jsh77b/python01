# -*- coding: utf-8 -*-
# 1000원 ~ 5000원 메뉴 가격 이미지 생성 (지폐 그림 + 가격, HTML 그린 후 Playwright 캡쳐)
# 2026.10.29 500x500 정사각형 -> 640x480(4:3) 변경 (메뉴 썸네일 60x45 칸에서 잘리거나 눌리지 않도록)
# 사용법 : python3 menu_price_image.py
import os
from playwright.sync_api import sync_playwright

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture", "menu")
WIDTH   = 640
HEIGHT  = 480
X_OFF   = (WIDTH - 500) // 2   # 기존 500 기준 배치를 가로 가운데로 이동
Y_OFF   = -12                  # 세로 480 에 맞춰 살짝 위로

# 지폐 색상 (천원 : 파랑 계열, 오천원 : 주황 계열)
BILL_COLOR = {
    1000: ("#cfe3f5", "#4a7fb0", "#2c5a85"),
    5000: ("#f9dcc4", "#d0773a", "#9a4a17"),
}


def bill_html(unit, rotate, left, top):
    """지폐 한 장 (단순 도안, 실제 지폐 아님)"""
    bg, line, txt = BILL_COLOR[unit]
    return f"""
    <div class="bill" style="background:{bg}; border-color:{line}; color:{txt};
         transform:rotate({rotate}deg); left:{left}px; top:{top}px;">
      <div class="inner" style="border-color:{line};">
        <span class="corner tl">{unit:,}</span>
        <span class="circle" style="border-color:{line};">₩</span>
        <span class="unit">{unit:,}원</span>
        <span class="corner br">{unit:,}</span>
      </div>
    </div>"""


def page_html(price):
    # 2000~4000원은 천원 지폐를 부채꼴로 겹쳐서, 1000/5000원은 한 장
    if price in BILL_COLOR:
        bills = [(price, -6, 85, 80)]
    else:
        cnt   = price // 1000
        mid   = (cnt - 1) / 2.0
        bills = [(1000, (i - mid) * 9, 70 + (i - mid) * 16, 70 + abs(i - mid) * 5)
                 for i in range(cnt)]
    bills_html = "".join(bill_html(u, r, l + X_OFF, t + Y_OFF) for u, r, l, t in bills)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
    body {{ margin:0; }}
    .card {{ width:{WIDTH}px; height:{HEIGHT}px; position:relative; overflow:hidden;
            background:radial-gradient(circle at 50% 40%, #fffdf5 0%, #fff3d6 100%);
            font-family:'NanumGothic', sans-serif; }}
    .bill {{ position:absolute; width:330px; height:165px; border:3px solid; border-radius:10px;
            box-shadow:0 6px 14px rgba(0,0,0,.18); transform-origin:50% 120%; }}
    .inner {{ position:absolute; inset:9px; border:2px dashed; border-radius:6px; }}
    .circle {{ position:absolute; left:20px; top:30px; width:80px; height:80px; border:3px solid;
              border-radius:50%; font-size:46px; font-weight:bold; text-align:center; line-height:80px; }}
    .unit {{ position:absolute; right:22px; top:52px; font-size:40px; font-weight:bold; }}
    .corner {{ position:absolute; font-size:18px; font-weight:bold; }}
    .tl {{ left:10px; top:6px; }} .br {{ right:10px; bottom:6px; }}
    .label {{ position:absolute; left:0; right:0; bottom:42px; text-align:center; }}
    .price {{ font-size:78px; font-weight:bold; color:#c0392b; letter-spacing:-2px;
             text-shadow:3px 3px 0 #fff, 5px 5px 0 rgba(0,0,0,.12); }}
    .menu {{ display:inline-block; margin-top:6px; padding:6px 26px; border-radius:30px;
            background:#2c3e50; color:#fff; font-size:28px; font-weight:bold; }}
    </style></head><body><div class="card">
      {bills_html}
      <div class="label"><div class="price">{price:,}원</div><div class="menu">{price:,}원 메뉴</div></div>
    </div></body></html>"""


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        for price in range(1000, 5001, 1000):
            page.set_content(page_html(price))
            path = os.path.join(OUT_DIR, f"menu_{price}.png")
            page.locator(".card").screenshot(path=path)
            print("생성 : " + path)
        browser.close()


if __name__ == "__main__":
    main()
