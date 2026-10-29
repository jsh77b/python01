#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
숫자야구게임 - 소켓 게임로직

php_socket_relay.py 에서 import 해서 사용한다.
기존 이벤트(connection/join/user/in/out)는 전혀 건드리지 않고
baseball_* 이벤트만 추가로 등록한다.

[왜 게임 상태를 메모리에 두는가]
릴레이는 eventlet 단일 프로세스라 소켓 이벤트가 순차 처리된다.
따라서 진행 상태(정답/턴/이력)를 메모리에 두면 락이 필요 없고,
카페24 MySQL이 InnoDB(트랜잭션)를 지원하지 않는 제약도 우회할 수 있다.
정답 숫자는 DB에 저장하지 않고 메모리에만 두며, 게임 종료 시에만 공개한다.

[DB 접속]
매번 새로 접속하면 75ms가 들어 이벤트 루프를 오래 막는다.
접속을 재사용하면 쿼리 5~15ms 수준이라 무시할 만하다. (_db 참조)

[방 상태 (rooms[room_id])]
  match_seq, room_id, digit_cnt, turn_limit_sec
  p1_id, p1_nm, p2_id, p2_nm
  status      : AD_330_11(숫자설정중) / AD_330_12(진행중)
  answers     : {user_id: "427"}   ← 절대 클라이언트로 내보내지 않는다
  turn_id     : 현재 차례 user_id
  turn_no     : 1부터
  history     : [{turn_no, guess_id, guess_no, strike, ball, out_yn, timeout_yn}]
  sids        : {user_id: sid}
  grace       : {user_id: greenthread}  ← 재접속 유예 타이머
  timer       : greenthread, timer_turn: int
  setup_timer : greenthread
  ended       : bool  ← 종료 중복 실행 방지
================================================================================
"""

import hashlib
import os
from datetime import datetime

import eventlet
import pymysql

# ── DB 설정 ────────────────────────────────────────────────────────────────────
DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"

# ── 공통코드 ───────────────────────────────────────────────────────────────────
ST_WAIT = "AD_330_10"    # 대기
ST_SETUP = "AD_330_11"   # 숫자설정중
ST_PLAY = "AD_330_12"    # 진행중
ST_END = "AD_330_13"     # 종료
ST_CANCEL = "AD_330_14"  # 취소

END_ANSWER = "AD_340_10"    # 정답
END_SURRENDER = "AD_340_11" # 항복
END_LEAVE = "AD_340_12"     # 상대이탈
END_MAXTURN = "AD_340_13"   # 턴한도초과
END_DRAW = "AD_340_14"      # 무승부

# ── 게임 규칙 ──────────────────────────────────────────────────────────────────
GRACE_SEC = 30        # 재접속 유예 (F5/앱전환이 이탈로 처리되는 것 방지)
SETUP_LIMIT_SEC = 120 # 비밀번호 설정 제한시간
MAX_TURN = 100        # 무한 게임 방지 상한
GUBN = "baseball"

_sio = None
_conn = None
_secret = None

rooms = {}    # room_id -> 방 상태
sid_map = {}  # sid -> (room_id, user_id)


def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("[%s] [야구] %s" % (now, msg), flush=True)


def room_key(room_id):
    return GUBN + str(room_id)


# ── DB ────────────────────────────────────────────────────────────────────────
def _db():
    """접속을 재사용한다. 끊겼으면 ping(reconnect=True) 이 다시 붙인다."""
    global _conn
    if _conn is None:
        _conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
            charset="utf8", connect_timeout=10, autocommit=True,
        )
    else:
        try:
            _conn.ping(reconnect=True)
        except Exception:
            _conn = pymysql.connect(
                host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
                charset="utf8", connect_timeout=10, autocommit=True,
            )
    return _conn


def db_query(sql, params=None, fetch="none"):
    try:
        cur = _db().cursor()
        cur.execute(sql, params or ())
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return cur.rowcount
    except Exception as e:
        log("DB 오류: %s | sql=%s" % (e, sql[:80]))
        return None


# ── 서버 시작 시 복구 (설계 9.1) ───────────────────────────────────────────────
def recover_on_start():
    """
    정답이 메모리에만 있으므로, 재기동되면 진행중 게임은 재개가 불가능하다.
    그냥 두면 DB에 '진행중' 행이 남아 1인1판 제한 때문에 두 사람 다
    새 방을 못 만드는 상태로 잠긴다. 그래서 미종료 방을 일괄 취소한다.
    (전적에는 반영하지 않는다 - 무효 처리)
    """
    if not DB_PASS:
        log("STOCK_DB_PASS 없음 - 복구/게임기능 비활성")
        return

    cnt = db_query(
        "UPDATE TB_BASEBALL_MATCH SET STATUS=%s, END_TYPE=%s, END_DT=NOW(), UPD_DT=NOW() "
        "WHERE DEL_YN='N' AND STATUS IN (%s,%s,%s)",
        (ST_CANCEL, END_LEAVE, ST_WAIT, ST_SETUP, ST_PLAY),
    )
    log("시작 복구: 미종료 방 %s건 취소 처리" % (cnt if cnt is not None else "?"))


def _load_secret():
    global _secret
    row = db_query(
        "SELECT COM_NM FROM TB_COMM_CD WHERE GRP_CD='AD_350' AND COM_CD='AD_350_10' AND USE_YN='Y'",
        fetch="one",
    )
    _secret = row[0] if row else None
    if not _secret:
        log("경고: 토큰 시크릿(AD_350_10)이 없습니다. 방 입장이 모두 거부됩니다.")


def _check_token(user_id, room_id, token):
    """
    PHP 화면이 만든 토큰과 같은 규칙으로 재계산해 대조한다.
    시크릿이 없으면 '열어두지 않고' 거부한다 (실패시 통과는 보안구멍).
    경계 문제를 피하려고 직전 1시간 토큰까지 허용한다.
    """
    if not _secret:
        return False

    now = datetime.now()
    prev = now.timestamp() - 3600
    stamps = [now.strftime("%Y%m%d%H"),
              datetime.fromtimestamp(prev).strftime("%Y%m%d%H")]

    for st in stamps:
        raw = "%s|%s|%s|%s" % (user_id, room_id, st, _secret)
        if hashlib.sha1(raw.encode("utf-8")).hexdigest() == token:
            return True
    return False


# ── 게임 규칙 ──────────────────────────────────────────────────────────────────
def judge(answer, guess):
    """스트라이크 = 숫자·자리 모두 일치 / 볼 = 숫자는 있으나 자리가 다름"""
    strike = sum(1 for i, c in enumerate(guess) if answer[i] == c)
    ball = sum(1 for c in guess if c in answer) - strike
    return strike, ball


def valid_number(number, digit_cnt):
    """0~9 만 사용 (0으로 시작 허용), 자릿수 일치, 중복 없음"""
    if not number or len(number) != digit_cnt:
        return False
    if any(c not in "0123456789" for c in number):
        return False
    return len(set(number)) == digit_cnt


def opponent_of(room, user_id):
    return room["p2_id"] if room["p1_id"] == user_id else room["p1_id"]


# ── 상태 전송 ──────────────────────────────────────────────────────────────────
def emit_state(room, to_sid=None):
    """방 상태 스냅샷. 정답(answers)은 절대 포함하지 않는다."""
    left_sec = 0
    if room["status"] == ST_PLAY and room["turn_limit_sec"] > 0:
        elapsed = (datetime.now() - room["turn_started"]).total_seconds()
        left_sec = max(0, int(room["turn_limit_sec"] - elapsed))

    payload = {
        "room": room["room_id"],
        "status": room["status"],
        "digit_cnt": room["digit_cnt"],
        "turn_limit_sec": room["turn_limit_sec"],
        "players": [
            {"user_id": room["p1_id"], "name": room["p1_nm"],
             "ready": room["p1_id"] in room["answers"]},
            {"user_id": room["p2_id"], "name": room["p2_nm"],
             "ready": room["p2_id"] in room["answers"]},
        ],
        "turn_id": room.get("turn_id", ""),
        "turn_no": room.get("turn_no", 0),
        "history": room["history"],
        "left_sec": left_sec,
    }

    if to_sid:
        _sio.emit("baseball_state", payload, to=to_sid)
    else:
        _sio.emit("baseball_state", payload, room=room_key(room["room_id"]))


def emit_error(sid, code, message):
    _sio.emit("baseball_error", {"code": code, "message": message}, to=sid)


# ── 타이머 ────────────────────────────────────────────────────────────────────
def cancel_timer(room, key):
    t = room.get(key)
    if t is not None:
        try:
            t.cancel()
        except Exception:
            pass
        room[key] = None


def start_turn_timer(room):
    """턴 제한시간이 0이면 타이머를 걸지 않는다(무제한)."""
    cancel_timer(room, "timer")
    room["turn_started"] = datetime.now()

    if room["turn_limit_sec"] <= 0:
        return

    room["timer_turn"] = room["turn_no"]
    room["timer"] = eventlet.spawn_after(
        room["turn_limit_sec"], on_turn_timeout, room["room_id"], room["turn_no"])


def on_turn_timeout(room_id, turn_no):
    """
    지나간 턴의 타이머가 뒤늦게 깨어나도 무시되도록 turn_no 를 대조한다.
    시간초과는 패배가 아니라 '턴 넘김'이다.
    """
    room = rooms.get(room_id)
    if room is None or room["ended"] or room["status"] != ST_PLAY:
        return
    if room["turn_no"] != turn_no:
        return

    timeout_id = room["turn_id"]
    log("턴 시간초과 room=%s turn=%d user=%s" % (room_id, turn_no, timeout_id))

    entry = {"turn_no": turn_no, "guess_id": timeout_id, "guess_no": "",
             "strike": 0, "ball": 0, "out_yn": "N", "timeout_yn": "Y"}
    room["history"].append(entry)

    db_query(
        "INSERT INTO TB_BASEBALL_GUESS (MATCH_SEQ,TURN_NO,GUESS_ID,GUESS_NO,"
        "STRIKE_CNT,BALL_CNT,OUT_YN,TIMEOUT_YN) VALUES (%s,%s,%s,'',0,0,'N','Y')",
        (room["match_seq"], turn_no, timeout_id),
    )

    next_id = opponent_of(room, timeout_id)
    _sio.emit("baseball_turn_timeout",
              {"turn_no": turn_no, "timeout_id": timeout_id, "next_turn_id": next_id},
              room=room_key(room_id))

    advance_turn(room, next_id)


def advance_turn(room, next_id):
    """턴을 넘기고 상한을 확인한다."""
    room["turn_no"] += 1
    room["turn_id"] = next_id

    if room["turn_no"] > MAX_TURN:
        # 양쪽이 계속 시간초과만 해도 게임이 끝나도록 하는 안전장치
        end_game(room, None, END_MAXTURN)
        return

    start_turn_timer(room)
    emit_state(room)


def on_setup_timeout(room_id):
    """숫자 설정 단계에서 한쪽이 설정하지 않고 사라진 경우 방을 취소한다."""
    room = rooms.get(room_id)
    if room is None or room["ended"] or room["status"] != ST_SETUP:
        return

    log("숫자설정 시간초과 - 방 취소 room=%s" % room_id)
    room["ended"] = True
    cancel_all_timers(room)

    db_query(
        "UPDATE TB_BASEBALL_MATCH SET STATUS=%s, END_TYPE=%s, END_DT=NOW(), UPD_DT=NOW() "
        "WHERE ROOM_ID=%s AND STATUS!=%s",
        (ST_CANCEL, END_LEAVE, room_id, ST_END),
    )
    _sio.emit("baseball_end",
              {"winner_id": "", "end_type": END_LEAVE, "turn_cnt": 0, "answers": {},
               "message": "상대가 숫자를 설정하지 않아 방이 취소되었습니다."},
              room=room_key(room_id))
    rooms.pop(room_id, None)


def cancel_all_timers(room):
    cancel_timer(room, "timer")
    cancel_timer(room, "setup_timer")
    for uid in list(room.get("grace", {}).keys()):
        g = room["grace"].pop(uid, None)
        if g is not None:
            try:
                g.cancel()
            except Exception:
                pass


# ── 게임 종료 ──────────────────────────────────────────────────────────────────
def end_game(room, winner_id, end_type):
    """
    종료 처리는 1회만 (동시 항복, 이탈+타임아웃 겹침 등으로 중복 호출될 수 있음).
    중복되면 전적이 두 번 올라간다.
    """
    if room["ended"]:
        return
    room["ended"] = True
    cancel_all_timers(room)

    room_id = room["room_id"]
    log("게임종료 room=%s winner=%s type=%s turn=%d"
        % (room_id, winner_id, end_type, room["turn_no"]))

    db_query(
        "UPDATE TB_BASEBALL_MATCH SET STATUS=%s, WINNER_ID=%s, END_TYPE=%s, "
        "TURN_CNT=%s, END_DT=NOW(), UPD_DT=NOW() WHERE ROOM_ID=%s AND STATUS!=%s",
        (ST_END, winner_id, end_type, room["turn_no"], room_id, ST_END),
    )

    # 전적 갱신 - MyISAM은 트랜잭션이 없으므로 반드시 단일문으로 처리
    for uid, unm in ((room["p1_id"], room["p1_nm"]), (room["p2_id"], room["p2_nm"])):
        if not uid:
            continue
        if winner_id is None:
            win, lose, draw = 0, 0, 1
        elif uid == winner_id:
            win, lose, draw = 1, 0, 0
        else:
            win, lose, draw = 0, 1, 0
        best = room["turn_no"] if (win == 1 and end_type == END_ANSWER) else None

        db_query(
            "INSERT INTO TB_BASEBALL_RANK "
            "(USER_ID,USER_NM,PLAY_CNT,WIN_CNT,LOSE_CNT,DRAW_CNT,BEST_TURN,UPD_DT) "
            "VALUES (%s,%s,1,%s,%s,%s,%s,NOW()) "
            "ON DUPLICATE KEY UPDATE "
            "USER_NM=VALUES(USER_NM), PLAY_CNT=PLAY_CNT+1, "
            "WIN_CNT=WIN_CNT+VALUES(WIN_CNT), LOSE_CNT=LOSE_CNT+VALUES(LOSE_CNT), "
            "DRAW_CNT=DRAW_CNT+VALUES(DRAW_CNT), "
            "BEST_TURN=IF(VALUES(BEST_TURN) IS NULL, BEST_TURN, "
            "  IF(BEST_TURN IS NULL, VALUES(BEST_TURN), LEAST(BEST_TURN, VALUES(BEST_TURN)))), "
            "UPD_DT=NOW()",
            (uid, unm, win, lose, draw, best),
        )

    # 이 시점에 비로소 양쪽 정답을 공개한다
    _sio.emit("baseball_end", {
        "winner_id": winner_id or "",
        "end_type": end_type,
        "turn_cnt": room["turn_no"],
        "answers": dict(room["answers"]),
    }, room=room_key(room_id))

    # 게임 상태는 해제하되 소켓 방은 유지한다 (종료 후에도 채팅 계속)
    rooms.pop(room_id, None)


# ── 방 적재 ───────────────────────────────────────────────────────────────────
def load_room(room_id):
    row = db_query(
        "SELECT SEQ,ROOM_ID,P1_ID,P1_NM,P2_ID,P2_NM,DIGIT_CNT,TURN_LIMIT_SEC,STATUS "
        "FROM TB_BASEBALL_MATCH WHERE ROOM_ID=%s AND DEL_YN='N'",
        (room_id,), fetch="one",
    )
    if row is None:
        return None
    return {
        "match_seq": row[0], "room_id": row[1],
        "p1_id": row[2], "p1_nm": row[3] or "",
        "p2_id": row[4] or "", "p2_nm": row[5] or "",
        "digit_cnt": int(row[6]), "turn_limit_sec": int(row[7]),
        "status": row[8],
        "answers": {}, "turn_id": "", "turn_no": 0, "history": [],
        "sids": {}, "grace": {}, "timer": None, "timer_turn": 0,
        "setup_timer": None, "ended": False,
        "turn_started": datetime.now(),
    }


# ── 이벤트 핸들러 ──────────────────────────────────────────────────────────────
def on_join(sid, data):
    room_id = str(data.get("room", ""))
    user_id = str(data.get("user_id", ""))
    name = str(data.get("name", ""))
    token = str(data.get("token", ""))

    if not room_id or not user_id:
        emit_error(sid, "PARAM", "잘못된 요청입니다.")
        return

    if not _check_token(user_id, room_id, token):
        log("토큰 불일치 room=%s user=%s" % (room_id, user_id))
        emit_error(sid, "AUTH", "인증에 실패했습니다. 화면을 새로고침해주세요.")
        return

    db_row = load_room(room_id)
    if db_row is None:
        emit_error(sid, "NOROOM", "존재하지 않는 방입니다.")
        return

    if user_id not in (db_row["p1_id"], db_row["p2_id"]):
        emit_error(sid, "NOTPLAYER", "이 방의 참가자가 아닙니다.")
        return

    # 소켓 방 입장은 항상 허용한다 (종료된 방도 채팅은 계속 가능)
    _sio.enter_room(sid, room_key(room_id))
    sid_map[sid] = (room_id, user_id)

    if db_row["status"] in (ST_END, ST_CANCEL):
        _sio.emit("baseball_state", {
            "room": room_id, "status": db_row["status"],
            "digit_cnt": db_row["digit_cnt"], "turn_limit_sec": db_row["turn_limit_sec"],
            "players": [
                {"user_id": db_row["p1_id"], "name": db_row["p1_nm"], "ready": True},
                {"user_id": db_row["p2_id"], "name": db_row["p2_nm"], "ready": True},
            ],
            "turn_id": "", "turn_no": 0, "history": [], "left_sec": 0,
        }, to=sid)
        return

    room = rooms.get(room_id)
    if room is None:
        room = db_row
        rooms[room_id] = room
        # 아직 숫자설정 단계면 설정 제한시간을 건다
        if room["status"] == ST_SETUP:
            room["setup_timer"] = eventlet.spawn_after(
                SETUP_LIMIT_SEC, on_setup_timeout, room_id)
    elif room["status"] == ST_WAIT and db_row["status"] == ST_SETUP:
        # 방장 입장 후 상대가 PHP(baseball_room_enter.php)로 참가하면 DB만 바뀐다.
        # 메모리 방이 대기중으로 남아 숫자설정이 무시되므로 DB 기준으로 동기화한다.
        room["p2_id"] = db_row["p2_id"]
        room["p2_nm"] = db_row["p2_nm"]
        room["status"] = ST_SETUP
        room["setup_timer"] = eventlet.spawn_after(
            SETUP_LIMIT_SEC, on_setup_timeout, room_id)
        log("상대 참가 동기화 room=%s p2=%s" % (room_id, room["p2_id"]))

    # 재접속 유예 취소 (F5/앱전환 복귀)
    g = room["grace"].pop(user_id, None)
    if g is not None:
        try:
            g.cancel()
        except Exception:
            pass
        log("재접속 - 이탈처리 취소 room=%s user=%s" % (room_id, user_id))

    # 같은 사용자가 두 탭으로 들어오면 이전 sid 는 끊는다
    old_sid = room["sids"].get(user_id)
    if old_sid and old_sid != sid:
        sid_map.pop(old_sid, None)
        try:
            _sio.emit("baseball_error",
                      {"code": "DUP", "message": "다른 창에서 접속하여 연결이 종료되었습니다."},
                      to=old_sid)
            _sio.leave_room(old_sid, room_key(room_id))
        except Exception:
            pass

    room["sids"][user_id] = sid
    if user_id == room["p1_id"]:
        room["p1_nm"] = name or room["p1_nm"]
    else:
        room["p2_nm"] = name or room["p2_nm"]

    log("입장 room=%s user=%s status=%s" % (room_id, user_id, room["status"]))
    emit_state(room)

    # 새로고침/재접속 시 내 숫자 복원 (본인에게만)
    if user_id in room["answers"]:
        _sio.emit("baseball_my_number",
                  {"room": room_id, "number": room["answers"][user_id]}, to=sid)


def on_set_number(sid, data):
    room_id = str(data.get("room", ""))
    user_id = str(data.get("user_id", ""))
    number = str(data.get("number", ""))

    room = rooms.get(room_id)
    if room is None or room["ended"]:
        emit_error(sid, "NOROOM", "진행중인 방이 아닙니다.")
        return
    if user_id not in (room["p1_id"], room["p2_id"]):
        return
    if room["status"] != ST_SETUP:
        emit_error(sid, "STATE", "지금은 숫자를 설정할 수 없습니다.")
        return
    if user_id in room["answers"]:
        emit_error(sid, "DUP", "이미 숫자를 설정했습니다.")
        return
    if not valid_number(number, room["digit_cnt"]):
        emit_error(sid, "INVALID",
                   "%d자리 숫자를 0~9 중 중복 없이 입력해주세요." % room["digit_cnt"])
        return

    room["answers"][user_id] = number
    log("숫자설정 완료 room=%s user=%s" % (room_id, user_id))

    # 내 숫자는 본인에게만 보낸다 (본인 화면 표시용)
    _sio.emit("baseball_my_number", {"room": room_id, "number": number}, to=sid)

    # 양쪽 다 설정되면 게임 시작 (방장 선공)
    if room["p1_id"] in room["answers"] and room["p2_id"] in room["answers"]:
        cancel_timer(room, "setup_timer")
        room["status"] = ST_PLAY
        room["turn_id"] = room["p1_id"]
        room["turn_no"] = 1
        db_query(
            "UPDATE TB_BASEBALL_MATCH SET STATUS=%s, STA_DT=NOW(), UPD_DT=NOW() "
            "WHERE ROOM_ID=%s", (ST_PLAY, room_id))
        log("게임시작 room=%s 선공=%s" % (room_id, room["turn_id"]))
        start_turn_timer(room)

    emit_state(room)


def on_guess(sid, data):
    room_id = str(data.get("room", ""))
    user_id = str(data.get("user_id", ""))
    number = str(data.get("number", ""))

    room = rooms.get(room_id)
    if room is None or room["ended"]:
        emit_error(sid, "NOROOM", "진행중인 방이 아닙니다.")
        return
    if room["status"] != ST_PLAY:
        emit_error(sid, "STATE", "아직 게임이 시작되지 않았습니다.")
        return

    # 화면 비활성화는 UX일 뿐이므로 서버에서 반드시 턴을 검사한다
    if room["turn_id"] != user_id:
        emit_error(sid, "TURN", "상대 차례입니다.")
        return
    if not valid_number(number, room["digit_cnt"]):
        emit_error(sid, "INVALID",
                   "%d자리 숫자를 0~9 중 중복 없이 입력해주세요." % room["digit_cnt"])
        return

    # 같은 숫자 재제출은 턴을 소모하지 않는다
    for h in room["history"]:
        if h["guess_id"] == user_id and h["guess_no"] == number:
            emit_error(sid, "REPEAT", "이미 제출한 숫자입니다.")
            return

    answer = room["answers"].get(opponent_of(room, user_id), "")
    if not answer:
        emit_error(sid, "STATE", "상대 숫자가 아직 설정되지 않았습니다.")
        return

    strike, ball = judge(answer, number)
    out_yn = "Y" if (strike == 0 and ball == 0) else "N"
    turn_no = room["turn_no"]

    entry = {"turn_no": turn_no, "guess_id": user_id, "guess_no": number,
             "strike": strike, "ball": ball, "out_yn": out_yn, "timeout_yn": "N"}
    room["history"].append(entry)

    db_query(
        "INSERT INTO TB_BASEBALL_GUESS (MATCH_SEQ,TURN_NO,GUESS_ID,GUESS_NO,"
        "STRIKE_CNT,BALL_CNT,OUT_YN,TIMEOUT_YN) VALUES (%s,%s,%s,%s,%s,%s,%s,'N')",
        (room["match_seq"], turn_no, user_id, number, strike, ball, out_yn),
    )

    _sio.emit("baseball_result", entry, room=room_key(room_id))

    if strike == room["digit_cnt"]:
        end_game(room, user_id, END_ANSWER)
        return

    advance_turn(room, opponent_of(room, user_id))


def on_surrender(sid, data):
    room_id = str(data.get("room", ""))
    user_id = str(data.get("user_id", ""))

    room = rooms.get(room_id)
    if room is None or room["ended"]:
        return
    if user_id not in (room["p1_id"], room["p2_id"]):
        return

    log("항복 room=%s user=%s" % (room_id, user_id))
    end_game(room, opponent_of(room, user_id), END_SURRENDER)


def on_leave(sid, data):
    room_id = str(data.get("room", ""))
    user_id = str(data.get("user_id", ""))

    room = rooms.get(room_id)
    sid_map.pop(sid, None)

    if room is None or room["ended"]:
        return
    if user_id not in (room["p1_id"], room["p2_id"]):
        return

    log("이탈 room=%s user=%s" % (room_id, user_id))
    end_game(room, opponent_of(room, user_id), END_LEAVE)


def on_disconnect(sid):
    """
    새로고침(F5)과 모바일 앱전환도 disconnect 로 들어온다.
    즉시 이탈 처리하면 F5 한 번에 게임이 끝나므로 GRACE_SEC 동안 기다린다.
    """
    info = sid_map.pop(sid, None)
    if info is None:
        return

    room_id, user_id = info
    room = rooms.get(room_id)
    if room is None or room["ended"]:
        return

    # 이미 다른 sid 로 재접속한 상태면 무시
    if room["sids"].get(user_id) not in (None, sid):
        return

    log("연결끊김 - %d초 재접속 대기 room=%s user=%s" % (GRACE_SEC, room_id, user_id))
    room["grace"][user_id] = eventlet.spawn_after(
        GRACE_SEC, on_grace_expired, room_id, user_id)


def on_grace_expired(room_id, user_id):
    room = rooms.get(room_id)
    if room is None or room["ended"]:
        return
    room["grace"].pop(user_id, None)

    log("재접속 없음 - 이탈 처리 room=%s user=%s" % (room_id, user_id))

    if room["status"] == ST_SETUP:
        on_setup_timeout(room_id)
    else:
        end_game(room, opponent_of(room, user_id), END_LEAVE)


# ── 등록 ──────────────────────────────────────────────────────────────────────
def register(sio):
    """php_socket_relay.py 에서 호출한다. 기존 이벤트는 건드리지 않는다."""
    global _sio
    _sio = sio

    sio.on("baseball_join", on_join)
    sio.on("baseball_set_number", on_set_number)
    sio.on("baseball_guess", on_guess)
    sio.on("baseball_surrender", on_surrender)
    sio.on("baseball_leave", on_leave)

    if DB_PASS:
        _load_secret()

    log("baseball_* 이벤트 등록 완료")
