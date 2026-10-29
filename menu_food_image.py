# -*- coding: utf-8 -*-
# 식당 메뉴 일러스트 이미지 일괄 생성 (SVG 그린 후 Playwright 캡쳐, 640x480 / 메뉴 썸네일 4:3)
# 사용법 : python3 menu_food_image.py  -> capture/menu/food/menu_<IDX>.png
import os
from playwright.sync_api import sync_playwright

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture", "menu", "food")
W, H = 640, 480


def rnd(seed):
    """간단 난수 (실행마다 같은 그림이 나오도록 고정 시드)"""
    while True:
        seed = (seed * 1103515245 + 12345) % 2147483648
        yield seed


def grains(cx, cy, rx, ry, cnt, colors, seed=7, size=(9, 4.5)):
    """타원 영역 안에 밥알/알갱이 흩뿌리기"""
    g = rnd(seed); out = []
    for _ in range(cnt):
        x = cx - rx + next(g) % (2 * rx); y = cy - ry + next(g) % (2 * ry)
        if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 > 1:
            continue
        c = colors[next(g) % len(colors)]
        out.append(f'<ellipse cx="{x}" cy="{y}" rx="{size[0]}" ry="{size[1]}" transform="rotate({next(g)%180} {x} {y})" fill="{c}" stroke="#00000018"/>')
    return "".join(out)


def bowl(fill_top, extra="", band="#4a7fb0"):
    """국/밥 그릇 + 윗면 내용물"""
    return f'''<ellipse cx="320" cy="420" rx="185" ry="20" fill="#000" opacity=".1"/>
<path d="M140 250 L500 250 Q492 375 380 400 L260 400 Q148 375 140 250 Z" fill="#f4f6f9" stroke="#c4cad3" stroke-width="2"/>
<path d="M168 310 Q320 334 472 310" fill="none" stroke="{band}" stroke-width="5" opacity=".7"/>
<rect x="262" y="398" width="116" height="14" rx="5" fill="#d3d8df"/>
<ellipse cx="320" cy="250" rx="181" ry="36" fill="#eef1f5" stroke="#c4cad3" stroke-width="2"/>
<ellipse cx="320" cy="252" rx="166" ry="28" fill="{fill_top}"/>{extra}'''


def plate(extra, color="#ffffff"):
    """납작 접시"""
    return f'''<ellipse cx="320" cy="400" rx="230" ry="26" fill="#000" opacity=".1"/>
<ellipse cx="320" cy="300" rx="240" ry="105" fill="{color}" stroke="#c9ced6" stroke-width="2"/>
<ellipse cx="320" cy="300" rx="190" ry="80" fill="none" stroke="#e3e6ea" stroke-width="2"/>{extra}'''


def cup(drink, extra=""):
    """유리컵 음료"""
    return f'''<ellipse cx="320" cy="425" rx="120" ry="16" fill="#000" opacity=".1"/>
<path d="M215 120 L425 120 L400 420 L240 420 Z" fill="#ffffff" opacity=".55" stroke="#b9c2cc" stroke-width="3"/>
<path d="M222 170 L418 170 L398 412 L242 412 Z" fill="{drink}"/>
<ellipse cx="320" cy="170" rx="98" ry="12" fill="{drink}" stroke="#ffffff66" stroke-width="3"/>
<path d="M240 140 L258 400" stroke="#ffffff" stroke-width="8" opacity=".5" stroke-linecap="round"/>{extra}'''


def steam(y=110):
    return "".join(f'<path d="M{x} {y} q-14 -22 0 -44 q14 -22 0 -44" fill="none" stroke="#c9bba3" stroke-width="5" stroke-linecap="round" opacity=".55"/>' for x in (270, 320, 370))


def draw(kind):
    """메뉴 종류별 그림"""
    if kind == "rice":
        return steam() + bowl("#ffffff", grains(320, 232, 150, 40, 260, ["#ffffff"], size=(8, 4)) + '<path d="M165 250 Q320 120 475 250 Z" fill="#fff" opacity=".0"/>')
    if kind == "friedrice":
        return steam() + bowl("#f2d18b", grains(320, 250, 160, 26, 380, ["#f6dc9a", "#f0c46a", "#e0a43a", "#7ab648", "#e2574c", "#f7e7b4"], size=(7, 4)))
    if kind == "porridge":
        return steam() + bowl("#f6efd9", '<path d="M220 245 q40 -18 80 0 q40 18 80 0" fill="none" stroke="#f2c14e" stroke-width="10" stroke-linecap="round"/><circle cx="300" cy="252" r="6" fill="#7ab648"/><circle cx="350" cy="244" r="5" fill="#7ab648"/>')
    if kind == "gamjatang":
        return steam() + bowl("#c4472b", '<ellipse cx="270" cy="244" rx="40" ry="20" fill="#8a4a2f"/><ellipse cx="365" cy="250" rx="34" ry="17" fill="#e9c46a"/><ellipse cx="320" cy="262" rx="30" ry="12" fill="#5c8a3a"/><circle cx="240" cy="262" r="4" fill="#fff8"/><circle cx="400" cy="240" r="4" fill="#fff8"/>', band="#b0413e")
    if kind == "gomtang":
        return steam() + bowl("#efe6d6", '<ellipse cx="280" cy="248" rx="38" ry="12" fill="#b88a6a"/><ellipse cx="355" cy="256" rx="34" ry="10" fill="#a87a5a"/><g fill="#5fa04a"><circle cx="310" cy="238" r="5"/><circle cx="325" cy="244" r="4"/><circle cx="300" cy="262" r="4"/><circle cx="380" cy="242" r="5"/></g>')
    if kind == "mandu":
        return steam() + bowl("#e8d9b5", '<g fill="#fbf6ea" stroke="#d8ccb2" stroke-width="2"><path d="M220 260 q35 -45 70 0 z"/><path d="M300 250 q35 -45 70 0 z"/><path d="M360 266 q30 -38 60 0 z"/></g><path d="M240 262 q60 8 150 -4" stroke="#f2c14e" stroke-width="7" fill="none"/><circle cx="300" cy="270" r="4" fill="#5fa04a"/>')
    if kind == "ramen":
        noodle = "".join(f'<path d="M{190+i*16} 250 q8 -10 16 0 q8 10 16 0" fill="none" stroke="#f2d27a" stroke-width="5"/>' for i in range(16))
        return steam() + bowl("#d9542c", noodle + '<ellipse cx="380" cy="246" rx="30" ry="18" fill="#fff"/><ellipse cx="380" cy="246" rx="13" ry="9" fill="#f2b632"/><g fill="#5fa04a"><circle cx="270" cy="244" r="6"/><circle cx="290" cy="258" r="5"/></g>', band="#b0413e")
    if kind == "rolledomelet":
        rolls = "".join(f'<g transform="translate({190+i*55} 270)"><rect x="0" y="-40" width="48" height="70" rx="10" fill="#f6c94a" stroke="#d9a62a" stroke-width="2"/><path d="M10 -25 q14 10 0 25 q-8 10 6 22" fill="none" stroke="#fce79a" stroke-width="5"/><circle cx="34" cy="-8" r="3" fill="#e2574c"/><circle cx="20" cy="12" r="3" fill="#5fa04a"/></g>' for i in range(5))
        return plate(rolls)
    if kind == "friedegg":
        return plate('<path d="M200 300 q-20 -70 80 -80 q70 -40 130 10 q70 10 40 80 q-20 60 -120 50 q-110 10 -130 -60 z" fill="#ffffff" stroke="#e5e5e5" stroke-width="2"/><circle cx="320" cy="290" r="44" fill="#f7b32b"/><ellipse cx="305" cy="275" rx="14" ry="9" fill="#fde29a"/>')
    if kind == "steamedegg":
        return steam(130) + '''<ellipse cx="320" cy="420" rx="170" ry="18" fill="#000" opacity=".1"/>
<path d="M170 240 L470 240 L450 400 L190 400 Z" fill="#4b3a32"/><ellipse cx="320" cy="240" rx="150" ry="34" fill="#5a463c"/>
<ellipse cx="320" cy="238" rx="132" ry="26" fill="#f6d36b"/><path d="M220 215 q100 -50 200 0" fill="#f9e08f"/>
<g fill="#5fa04a"><circle cx="290" cy="225" r="6"/><circle cx="330" cy="215" r="5"/><circle cx="360" cy="232" r="6"/></g>'''
    if kind == "spam":
        sl = "".join(f'<g transform="rotate(-8 {200+i*70} 290)"><rect x="{170+i*70}" y="230" width="60" height="110" rx="6" fill="#e48a7a" stroke="#c4614f" stroke-width="2"/><path d="M{175+i*70} 250 l50 0 M{175+i*70} 300 l50 0" stroke="#b3503f" stroke-width="4" opacity=".6"/></g>' for i in range(4))
        return plate(sl)
    if kind == "samgyeop":
        sl = "".join(f'<g transform="rotate(-6 320 {240+i*40})"><rect x="190" y="{220+i*38}" width="260" height="30" rx="8" fill="#f3d5c4"/><rect x="190" y="{228+i*38}" width="260" height="10" fill="#c9785a"/><rect x="190" y="{242+i*38}" width="260" height="6" fill="#a8583c"/></g>' for i in range(3))
        return plate(sl)
    if kind == "tunacan":
        return '''<ellipse cx="320" cy="400" rx="170" ry="22" fill="#000" opacity=".1"/>
<rect x="170" y="220" width="300" height="170" rx="20" fill="#b9c4ce"/><rect x="170" y="260" width="300" height="90" fill="#1f6fb2"/>
<text x="320" y="320" text-anchor="middle" font-size="44" font-weight="bold" fill="#fff" font-family="sans-serif">TUNA</text>
<ellipse cx="320" cy="220" rx="150" ry="40" fill="#d6dde3" stroke="#9aa6b2" stroke-width="3"/>
<ellipse cx="320" cy="220" rx="122" ry="28" fill="#e9c9a6"/>''' + grains(320, 220, 110, 22, 120, ["#e2b88e", "#f0d5b5", "#d4a47a"], size=(7, 4))
    if kind == "tunariceball":
        tri = "".join(f'<g transform="translate({x} 300)"><path d="M-75 60 Q-80 70 -60 70 L60 70 Q80 70 75 60 L10 -60 Q0 -75 -10 -60 Z" fill="#ffffff" stroke="#e0dbd0" stroke-width="2"/>{grains(0, 20, 50, 40, 90, ["#ffffff", "#f4f0e8"], seed=x, size=(6, 3))}<rect x="-45" y="30" width="90" height="42" fill="#1d2a1f"/></g>' for x in (225, 415))
        return plate(tri)
    if kind == "spaghetti":
        noodle = "".join(f'<path d="M{200+i*12} {300 + (i%3)*6} q30 -40 60 0 t60 0" fill="none" stroke="#f4d58d" stroke-width="5"/>' for i in range(14))
        return plate(noodle + '<ellipse cx="320" cy="285" rx="80" ry="34" fill="#d1402f" opacity=".9"/><g fill="#7a3b1f"><circle cx="290" cy="280" r="14"/><circle cx="345" cy="290" r="14"/></g><g fill="#5fa04a"><circle cx="320" cy="270" r="6"/><circle cx="335" cy="262" r="5"/></g>')
    if kind == "kimbap":
        sl = ""
        for i, (x, y) in enumerate([(220, 270), (300, 250), (380, 270), (260, 330), (345, 330), (425, 320)]):
            sl += f'<g transform="translate({x} {y})"><circle r="38" fill="#1d2a1f"/><circle r="32" fill="#ffffff"/>{grains(0, 0, 30, 30, 40, ["#ffffff", "#efe9dc"], seed=i+3, size=(4, 2))}<rect x="-6" y="-14" width="12" height="10" fill="#f2b632"/><rect x="-16" y="-2" width="10" height="10" fill="#e8743b"/><rect x="6" y="-2" width="10" height="10" fill="#5fa04a"/><circle cx="0" cy="12" r="7" fill="#c9a27a"/></g>'
        return plate(sl)
    if kind == "cookie":
        ck = ""
        for i, (x, y) in enumerate([(230, 290), (310, 260), (390, 290), (270, 335), (360, 335)]):
            ck += f'<g transform="translate({x} {y})"><circle r="36" fill="#d9a35f" stroke="#b97f3c" stroke-width="2"/>' + "".join(f'<circle cx="{dx}" cy="{dy}" r="5" fill="#5a3825"/>' for dx, dy in ((-12, -10), (10, -14), (14, 8), (-8, 12), (0, 0))) + '</g>'
        return plate(ck)
    if kind == "misu":
        return cup("#d8c4a0", '<rect x="330" y="60" width="16" height="200" rx="8" fill="#e57a44" transform="rotate(12 338 160)"/>')
    if kind == "milk":
        return cup("#fbfbf7")
    if kind == "greentea":
        return steam(120) + '''<ellipse cx="320" cy="420" rx="150" ry="16" fill="#000" opacity=".1"/>
<path d="M190 210 L450 210 Q445 380 320 405 Q195 380 190 210 Z" fill="#f3f5f2" stroke="#b9c2cc" stroke-width="3"/>
<ellipse cx="320" cy="210" rx="130" ry="26" fill="#eef1f5" stroke="#b9c2cc" stroke-width="3"/><ellipse cx="320" cy="214" rx="114" ry="18" fill="#9cbf5a"/>
<path d="M450 250 q60 10 40 70 q-15 30 -50 25" fill="none" stroke="#b9c2cc" stroke-width="12"/>'''
    if kind == "lemonade":
        return cup("#f7e463", '<circle cx="410" cy="150" r="48" fill="#f7d53a" stroke="#e8b923" stroke-width="5"/><g stroke="#fff6b8" stroke-width="3">' + "".join(f'<line x1="410" y1="150" x2="{410+40*c}" y2="{150+40*s}"/>' for c, s in ((1, 0), (0.5, 0.87), (-0.5, 0.87), (-1, 0), (-0.5, -0.87), (0.5, -0.87))) + '</g><g fill="#ffffff" opacity=".8"><rect x="260" y="250" width="40" height="40" rx="6"/><rect x="320" y="300" width="40" height="40" rx="6"/></g>')
    return plate("")


# 메뉴 IDX : (그림 종류, 메뉴명)
MENUS = {
    58: ("rolledomelet", "계란말이"), 59: ("cookie", "미니쿠키"), 60: ("friedegg", "계란후라이"),
    61: ("steamedegg", "계란찜"), 62: ("spam", "스팸구이"), 63: ("samgyeop", "데패삼겹살 구이"),
    64: ("rice", "밥"), 65: ("tunacan", "참치(캔)"), 70: ("tunariceball", "참치주먹밥"),
    72: ("tunariceball", "참치주먹밥"), 73: ("spaghetti", "스파게티"), 95: ("ramen", "라면"),
    100: ("kimbap", "참치김밥"), 69: ("friedrice", "볶음밥"), 71: ("tunariceball", "참치주먹밥"),
    78: ("porridge", "계란죽"), 67: ("gamjatang", "감자탕"), 79: ("gomtang", "소머리 곰탕"),
    80: ("mandu", "만두국"), 66: ("misu", "미숫가루"), 68: ("milk", "우유"),
    74: ("greentea", "녹차"), 75: ("lemonade", "레몬에이드"),
}


def page_html(kind, name):
    return f'''<html><body style="margin:0;"><svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg">
<defs><radialGradient id="bg" cx="50%" cy="40%" r="75%"><stop offset="0%" stop-color="#f8ecd4"/><stop offset="100%" stop-color="#e4c99e"/></radialGradient></defs>
<rect width="100%" height="100%" fill="url(#bg)"/>{draw(kind)}
<rect x="0" y="438" width="{W}" height="42" fill="#00000055"/>
<text x="{W//2}" y="469" text-anchor="middle" font-size="26" font-weight="bold" fill="#fff" font-family="'Noto Sans CJK KR','Nanum Gothic',sans-serif">{name}</text>
</svg></body></html>'''


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for idx, (kind, name) in MENUS.items():
            page.set_content(page_html(kind, name))
            page.screenshot(path=os.path.join(OUT_DIR, f"menu_{idx}.png"))
        browser.close()
    print("생성 완료 :", len(MENUS), "건 ->", OUT_DIR)


if __name__ == "__main__":
    main()
