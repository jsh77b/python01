# -*- coding: utf-8 -*-
# 메뉴 이미지 - 공기밥 일러스트 생성 (SVG 그린 후 Playwright 캡쳐, 메뉴 썸네일 비율 4:3)
# 사용법 : python3 menu_rice_image.py
import os
from playwright.sync_api import sync_playwright

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture", "menu")
WIDTH, HEIGHT = 640, 480


def rice_grains():
    """밥알 (타원 여러 개를 밥 윗면에 흩뿌림)"""
    grains = []
    seed = 7
    for i in range(320):
        seed = (seed * 1103515245 + 12345) % 2147483648
        x = 175 + (seed % 290)
        seed = (seed * 1103515245 + 12345) % 2147483648
        y = 150 + (seed % 125)
        seed = (seed * 1103515245 + 12345) % 2147483648
        rot = seed % 180
        # 밥 윗면(둥근 언덕) 안쪽만
        dx = (x - 320) / 150.0
        top = 270 - 115 * (1 - dx * dx)
        if y < top + 6:
            continue
        grains.append(f'<ellipse cx="{x}" cy="{y}" rx="9" ry="4.5" transform="rotate({rot} {x} {y})" '
                      f'fill="#ffffff" stroke="#d8cfbd" stroke-width="1.2"/>')
    return "\n".join(grains)


def page_html():
    return f"""<html><body style="margin:0;">
<svg width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <radialGradient id="bg" cx="50%" cy="40%" r="70%">
      <stop offset="0%" stop-color="#f6e6c8"/><stop offset="100%" stop-color="#e2c79c"/>
    </radialGradient>
    <linearGradient id="bowl" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ffffff"/><stop offset="100%" stop-color="#d9dde3"/>
    </linearGradient>
    <radialGradient id="rice" cx="50%" cy="30%" r="70%">
      <stop offset="0%" stop-color="#ffffff"/><stop offset="100%" stop-color="#f3efe6"/>
    </radialGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#bg)"/>
  <!-- 그림자 -->
  <ellipse cx="320" cy="420" rx="185" ry="22" fill="#000" opacity="0.10"/>
  <!-- 밥 (둥근 언덕) -->
  <path d="M150 270 Q320 100 490 270 Z" fill="url(#rice)" stroke="#ddd3c0" stroke-width="2"/>
  {rice_grains()}
  <!-- 밥그릇 -->
  <path d="M140 262 L500 262 Q492 380 380 405 L260 405 Q148 380 140 262 Z" fill="url(#bowl)" stroke="#c4cad3" stroke-width="2"/>
  <ellipse cx="320" cy="263" rx="181" ry="14" fill="#eef1f5" stroke="#c4cad3" stroke-width="2"/>
  <path d="M150 268 Q320 236 490 268 Q320 284 150 268 Z" fill="url(#rice)"/>
  <!-- 그릇 무늬 띠 -->
  <path d="M168 318 Q320 340 472 318" fill="none" stroke="#4a7fb0" stroke-width="5" opacity="0.75"/>
  <path d="M182 336 Q320 358 458 336" fill="none" stroke="#4a7fb0" stroke-width="2" opacity="0.5"/>
  <!-- 굽 -->
  <rect x="262" y="403" width="116" height="14" rx="5" fill="#d3d8df" stroke="#c4cad3" stroke-width="2"/>
  <!-- 김 -->
  <path d="M270 120 q-14 -22 0 -44 q14 -22 0 -44" fill="none" stroke="#c9bba3" stroke-width="5" stroke-linecap="round" opacity="0.6"/>
  <path d="M320 108 q-14 -22 0 -44 q14 -22 0 -44" fill="none" stroke="#c9bba3" stroke-width="5" stroke-linecap="round" opacity="0.6"/>
  <path d="M370 120 q-14 -22 0 -44 q14 -22 0 -44" fill="none" stroke="#c9bba3" stroke-width="5" stroke-linecap="round" opacity="0.6"/>
</svg></body></html>"""


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "menu_rice.png")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.set_content(page_html())
        page.screenshot(path=out)
        browser.close()
    print("생성 완료 :", out)


if __name__ == "__main__":
    main()
