#!/usr/bin/env python3
"""
================================================================================
PHP 프로젝트(cafe24_jsh77b)가 쓰던 Node(web.js) 소켓 서버 로직을
그대로 옮긴 방(room) 기반 릴레이 서버.

Node web.js의 io.on("connection", ...) 블록과 동일한 이벤트 계약을 구현한다.
PHP 쪽에서 이 서버를 쓰는 곳(모두 동일한 connection/join/user/in/out 계약 사용):
  - www/hoone/php/chat/room_view.php          (프리채팅)
  - www/hoone/php/admin/etc/log_pop.php       (관리자 SQL 로그 팝업 뷰어)
  - www/hoone/inc/bottom.inc.php              (관리자 페이지마다 로그를 log_pop으로 전송)
  - www/hoone/php/order/rest/rest_moniter.php (상점 주문 모니터)
  - www/hoone/php/order/guest/guest_cart.php, guest_moniter.php (손님 주문/모니터)
  - www/hoone/php/user/login.php              (로그인 접속 알림)

[실행 방법]
  python3 php_socket_relay.py
  (다른 터미널에서) ./bin/cloudflared tunnel --url http://localhost:8765

[이벤트 계약 (Node web.js 와 동일)]
  클라이언트 연결 시    -> 서버가 "connection" {type:"connected"} 발신
  클라이언트 "connection"(type=join) 또는 "join" 발신 -> 서버가 소켓을 (gubn+room) 방에 join,
                                                      해당 방에 "system"(접속) 브로드캐스트
  클라이언트 "user" 발신     -> 해당 방에 "message" 브로드캐스트
  클라이언트 "in" 발신       -> 해당 방에 "system"(입장) 브로드캐스트
  클라이언트 "out" 발신      -> 해당 방에 "system"(퇴장) 브로드캐스트
  클라이언트 연결 종료(disconnect) -> 해당 방에 "system"(접속끊김) 브로드캐스트
================================================================================
"""

import eventlet
import socketio
from datetime import datetime

HOST = "0.0.0.0"
PORT = 8765

sio = socketio.Server(async_mode="eventlet", cors_allowed_origins="*")
app = socketio.WSGIApp(sio)

# sid -> {name, room, user_id, gubn} (Node web.js의 socketIds/socketNames/... 병렬배열을 dict 하나로 대체)
connections = {}


def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}")


def room_key(gubn, room):
    return f"{gubn}{room}"


@sio.event
def connect(sid, environ):
    log(f"[소켓서버] 연결: sid={sid}")
    sio.emit("connection", {"type": "connected", "url": environ.get("PATH_INFO", "")}, to=sid)


def handle_join(sid, data):
    gubn = data.get("gubn", "")
    room = data.get("room", "")
    name = data.get("name", "")
    user_id = data.get("user_id", "")
    key = room_key(gubn, room)

    sio.enter_room(sid, key)
    sio.emit("system", {"message": "채팅방에 오신 것을 환영합니다.", "type": "alert"}, to=sid)

    connections[sid] = {"name": name, "room": room, "user_id": user_id, "gubn": gubn}

    sio.emit("system", {
        "message": f"{name}님이 접속되었습니다.",
        "type": "txt",
        "gubn": "connection",
        "socket_id": sid,
        "name": name,
        "room": room,
        "user_id": user_id,
    }, room=key)

    log(f"[소켓서버] join: sid={sid} room={key} name={name}")


# 기존 클라이언트 호환을 위해 connection(type=join)/join 둘 다 처리 (Node web.js와 동일)
@sio.on("connection")
def on_connection(sid, data):
    if data.get("type") == "join":
        handle_join(sid, data)


@sio.on("join")
def on_join(sid, data):
    handle_join(sid, {
        "type": "join",
        "gubn": data.get("gubn", ""),
        "room": data.get("room", ""),
        "name": data.get("name", ""),
        "user_id": data.get("user_id", ""),
    })


# 채팅방 대화 메시지 전송
@sio.on("user")
def on_user(sid, data):
    gubn = data.get("gubn", "")
    room = data.get("room", "")
    key = room_key(gubn, room)

    sio.emit("message", {
        "message": data.get("message", ""),
        "name": data.get("name", ""),
        "room": room,
        "div": data.get("div", ""),
        "user_id": data.get("user_id", ""),
    }, room=key)


# 채팅방 입장 신호
@sio.on("in")
def on_in(sid, data):
    gubn = data.get("gubn", "")
    room = data.get("room", "")
    key = room_key(gubn, room)
    name = data.get("name", "")

    sio.emit("system", {
        "message": f"{name}님이 입장 하셨습니다.",
        "type": "txt",
        "gubn": "in",
        "name": name,
        "room": room,
        "user_id": data.get("user_id", ""),
    }, room=key)


# 채팅방 퇴장 신호
@sio.on("out")
def on_out(sid, data):
    gubn = data.get("gubn", "")
    room = data.get("room", "")
    key = room_key(gubn, room)
    name = data.get("name", "")

    sio.emit("system", {
        "message": f"{name}님이 퇴장하셨습니다.",
        "type": "txt",
        "gubn": "out",
        "name": name,
        "room": room,
        "user_id": data.get("user_id", ""),
    }, room=key)


@sio.event
def disconnect(sid):
    info = connections.pop(sid, None)
    if info is None:
        log(f"[소켓서버] 연결종료: sid={sid} (등록된 방 정보 없음)")
        return

    key = room_key(info["gubn"], info["room"])
    sio.emit("system", {
        "message": f"{info['name']}님이 접속이 끊겼습니다.",
        "type": "txt",
        "gubn": "disconnect",
        "socket_id": sid,
        "name": info["name"],
        "room": info["room"],
        "user_id": info["user_id"],
    }, room=key)

    log(f"[소켓서버] 연결종료: sid={sid} room={key} name={info['name']}")


if __name__ == "__main__":
    log(f"PHP 소켓 릴레이 서버 시작 - http://{HOST}:{PORT}")
    eventlet.wsgi.server(eventlet.listen((HOST, PORT)), app, log_output=False)
