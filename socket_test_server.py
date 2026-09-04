#!/usr/bin/env python3
"""
================================================================================
소켓서버 테스트 — python-socketio 서버를 로컬에 띄우고
Cloudflare Tunnel로 외부에 HTTPS URL로 노출하기 위한 테스트용 서버.

[실행 방법]
  1) python3 socket_test_server.py
  2) (다른 터미널에서) ./bin/cloudflared tunnel --url http://localhost:8765
     -> https://xxxx.trycloudflare.com 형태의 임시 HTTPS URL이 발급됨

[테스트 방법]
  브라우저 콘솔 또는 python-socketio 클라이언트에서 위 URL로 접속 후
  "ping" 이벤트를 보내면 "pong" 이벤트로 응답한다.
================================================================================
"""

import eventlet
import socketio
from datetime import datetime

HOST = "0.0.0.0"
PORT = 8765


def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")


sio = socketio.Server(async_mode="eventlet", cors_allowed_origins="*")
app = socketio.WSGIApp(sio)


@sio.event
def connect(sid, environ):
    log(f"[소켓서버] 클라이언트 연결: {sid}")


@sio.event
def disconnect(sid):
    log(f"[소켓서버] 클라이언트 연결 종료: {sid}")


@sio.on("ping")
def on_ping(sid, data):
    log(f"[소켓서버] ping 수신 (sid={sid}): {data}")
    sio.emit("pong", {
        "received": data,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, to=sid)


if __name__ == "__main__":
    log(f"소켓서버 테스트 시작 - http://{HOST}:{PORT}")
    eventlet.wsgi.server(eventlet.listen((HOST, PORT)), app, log_output=False)
