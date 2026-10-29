# -*- coding: utf-8 -*-
# 사이트 화면 캡쳐 (Playwright headless chromium)
# 사용법 : python3 site_capture.py <URL> [저장파일명] [pc|mobile]
import sys
import os
from datetime import datetime
from playwright.sync_api import sync_playwright

CAPTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture")

# 화면 크기 (PC / 모바일)
VIEWPORTS = {
    "pc":     {"width": 1400, "height": 900},
    "mobile": {"width": 390,  "height": 844},
}


def capture(url, file_name=None, device="pc"):
    if not file_name:
        file_name = "capture_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    path = os.path.join(CAPTURE_DIR, file_name)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORTS.get(device, VIEWPORTS["pc"]))
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.screenshot(path=path, full_page=True)
        browser.close()
    return path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법 : python3 site_capture.py <URL> [저장파일명] [pc|mobile]")
        sys.exit(1)
    url    = sys.argv[1]
    name   = sys.argv[2] if len(sys.argv) > 2 else None
    device = sys.argv[3] if len(sys.argv) > 3 else "pc"
    print("캡쳐 완료 : " + capture(url, name, device))
