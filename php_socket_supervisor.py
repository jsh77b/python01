#!/usr/bin/env python3
"""
================================================================================
php_socket_relay.py(소켓 릴레이 서버) + cloudflared(Quick Tunnel)를 상시 감시하며
살아있게 유지하고, 터널 URL이 바뀔 때마다 DB(TB_COMM_CD)에 자동 반영하는 상주 프로세스.

PHP 쪽(common.inc.php)이 TB_SYSTEM(DATA_CODE=30) -> TB_COMM_CD(GRP_CD=AD_210,
COM_CD=AD_210_13).COM_NM 값을 소켓서버 URL로 읽어가므로, 이 프로세스가 계속
떠있기만 하면 관리자 화면에서 수동으로 URL을 입력/저장할 필요가 없어진다.

systemd user service(php-socket-relay.service)로 등록해서 상시 실행한다.

[동작]
  루프(HEALTH_CHECK_SEC 마다):
    1) php_socket_relay.py 가 죽어있으면 재시작
    2) cloudflared 가 죽어있으면 재시작 (재시작하면 새 URL 발급됨)
    3) cloudflared 로그에서 현재 URL을 파싱, 마지막으로 DB에 반영한 URL과 다르면 UPDATE
  SIGTERM/SIGINT 수신 시 자식 프로세스까지 함께 종료 후 정상 종료
================================================================================
"""

import os
import re
import signal
import subprocess
import sys
import time

import pymysql

BASE_DIR         = "/workspace/python01"
CLOUDFLARED      = f"{BASE_DIR}/bin/cloudflared"
RELAY_SCRIPT     = f"{BASE_DIR}/php_socket_relay.py"
LOG_DIR          = f"{BASE_DIR}/log"
PORT             = 8765
HEALTH_CHECK_SEC = 10
URL_WAIT_SEC     = 30

DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"

URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

relay_proc = None
tunnel_proc = None
tunnel_log_path = f"{LOG_DIR}/cloudflared.out"
last_pushed_url = None
running = True


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def start_relay():
    global relay_proc
    log_file = open(f"{LOG_DIR}/php_socket_relay.out", "a")
    relay_proc = subprocess.Popen(
        ["python3", "-u", RELAY_SCRIPT],
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    log(f"php_socket_relay.py 시작 (pid={relay_proc.pid})")


def start_tunnel():
    global tunnel_proc
    # 재시작마다 URL이 바뀌므로 로그를 새로 시작해서 이전 URL과 헷갈리지 않게 한다.
    log_file = open(tunnel_log_path, "w")
    tunnel_proc = subprocess.Popen(
        [CLOUDFLARED, "tunnel", "--url", f"http://localhost:{PORT}"],
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    log(f"cloudflared tunnel 시작 (pid={tunnel_proc.pid})")


def parse_current_url():
    if not os.path.exists(tunnel_log_path):
        return None
    with open(tunnel_log_path, encoding="utf-8", errors="ignore") as f:
        content = f.read()
    matches = URL_PATTERN.findall(content)
    return matches[-1] if matches else None


def update_db_url(url):
    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE TB_COMM_CD SET COM_NM=%s, UPD_ID='socket_relay_auto', UPD_DT=NOW() "
                "WHERE GRP_CD='AD_210' AND COM_CD='AD_210_13'",
                (url,),
            )
            affected = cur.rowcount
        conn.commit()
        return affected
    finally:
        conn.close()


def handle_signal(signum, frame):
    global running
    log(f"종료 신호 수신 (signal={signum}) - 자식 프로세스 정리 중...")
    running = False


def cleanup():
    for name, proc in (("relay", relay_proc), ("tunnel", tunnel_proc)):
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            log(f"{name} 프로세스 종료 (pid={proc.pid})")


def main():
    global last_pushed_url

    if not DB_PASS:
        log("경고: STOCK_DB_PASS 환경변수가 없습니다. URL이 바뀌어도 DB 반영이 안 됩니다.")

    os.makedirs(LOG_DIR, exist_ok=True)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    start_relay()
    start_tunnel()

    log("터널 URL 파싱 대기중...")
    for _ in range(URL_WAIT_SEC):
        time.sleep(1)
        url = parse_current_url()
        if url:
            break
    else:
        url = None

    if url and DB_PASS:
        affected = update_db_url(url)
        last_pushed_url = url
        log(f"터널 URL 확인 및 DB 반영: {url} (영향받은 행: {affected})")
    elif url:
        last_pushed_url = url
        log(f"터널 URL 확인(DB 미반영, STOCK_DB_PASS 없음): {url}")
    else:
        log("터널 URL을 초기 확인하지 못했습니다. 계속 감시합니다.")

    while running:
        time.sleep(HEALTH_CHECK_SEC)

        if relay_proc.poll() is not None:
            log(f"php_socket_relay.py 죽음(exit={relay_proc.returncode}) - 재시작")
            start_relay()

        if tunnel_proc.poll() is not None:
            log(f"cloudflared 죽음(exit={tunnel_proc.returncode}) - 재시작")
            start_tunnel()
            last_pushed_url = None  # 재시작하면 URL이 바뀌므로 강제로 재확인/재반영

        current_url = parse_current_url()
        if current_url and current_url != last_pushed_url:
            log(f"터널 URL 변경 감지: {last_pushed_url} -> {current_url}")
            if DB_PASS:
                try:
                    affected = update_db_url(current_url)
                    log(f"DB(TB_COMM_CD) 반영 완료 (영향받은 행: {affected})")
                except Exception as e:
                    log(f"DB 반영 실패: {e}")
                    continue
            last_pushed_url = current_url

    cleanup()
    log("정상 종료")


if __name__ == "__main__":
    main()
