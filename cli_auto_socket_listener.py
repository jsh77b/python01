#!/usr/bin/env python3
"""
================================================================================
CLI AUTO 실시간 리스너 — cafe24 Node 소켓 서버(web.js)에 접속해
질문이 등록되면(cliauto:new 이벤트) cron(1분) 대기 없이 즉시
cli_auto_batch.run_batch()를 실행한다.

기존 cron(*/1 * * * * cli_auto_batch.py)은 안전망으로 그대로 유지한다.
이 리스너가 죽어도 최대 1분 지연으로 처리되며, 두 프로세스가 동시에 돌아도
run_batch() 안의 GET_LOCK('cli_auto_batch')가 중복 실행을 막아준다.

[실행 방법]
  python3 cli_auto_socket_listener.py
================================================================================
"""

import sys
import time
import threading

sys.path.insert(0, "/workspace/python01")
from cli_auto_batch import run_batch, log

try:
    import socketio
except ModuleNotFoundError:
    log("[리스너] python-socketio 모듈이 없습니다. 다음 명령으로 설치해주세요:")
    log("  pip3 install --user --break-system-packages python-socketio websocket-client")
    sys.exit(1)

SOCKET_URL = "http://jsh77b1.cafe24app.com"

sio = socketio.Client(reconnection=True, reconnection_delay=2, reconnection_delay_max=30)

_running_lock = threading.Lock()
_running = False


def _run_batch_async():
    global _running
    with _running_lock:
        if _running:
            log("[리스너] 이미 처리 중 — 이번 이벤트는 건너뜀 (다음 run_batch에서 같이 처리됨)")
            return
        _running = True
    try:
        run_batch()
    except Exception as e:
        log(f"[리스너] run_batch 실행 중 예외: {e}")
    finally:
        with _running_lock:
            _running = False


@sio.event
def connect():
    log("[리스너] 소켓 연결됨")


@sio.event
def disconnect():
    log("[리스너] 소켓 연결 끊김")


@sio.on("cliauto:new")
def on_new(data):
    log(f"[리스너] 새 질문 알림 수신: {data}")
    threading.Thread(target=_run_batch_async, daemon=True).start()


if __name__ == "__main__":
    log("CLI AUTO 실시간 리스너 시작")
    while True:
        try:
            sio.connect(SOCKET_URL, transports=["websocket"])
            sio.wait()
        except Exception as e:
            log(f"[리스너] 연결 오류: {e} — 5초 후 재시도")
            time.sleep(5)
