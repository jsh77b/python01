#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
================================================================================
숫자야구게임 - 버려진 방 정리 배치

[왜 필요한가]
1인 1판 제한 때문에, 방만 만들어두고 사라지거나 게임 도중 양쪽이 모두 떠난
방이 남아 있으면 그 사용자들은 새 방을 만들지도 참가하지도 못하는 상태로 잠긴다.

소켓서버가 이탈을 감지해 즉시 정리하지만(baseball_game.py 의 재접속 유예 → 이탈 처리),
아래 경우는 소켓이 감지하지 못한다.
  - 방만 만들고 게임화면에 한 번도 들어오지 않은 경우 (소켓 접속 자체가 없음)
  - 턴 제한시간을 무제한(0)으로 두고 양쪽이 잠수한 경우
    (MAX_TURN 상한은 턴이 진행돼야 늘어나므로 영영 도달하지 않는다)

그래서 마지막 활동(UPD_DT)으로부터 STALE_HOURS 가 지난 미종료 방을 취소 처리한다.

[실행]
    */10 * * * * /usr/bin/python3 /workspace/python01/baseball_cleanup_batch.py \
                 >> /workspace/python01/log/baseball_cleanup_$(date +\%Y-\%m-\%d).log 2>&1
================================================================================
"""

import os
import sys
from datetime import datetime

import pymysql

import batch_status

BATCH_NM = "baseball_cleanup_batch.py"

DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"

# 마지막 활동 이후 이 시간이 지난 미종료 방은 취소한다 (설계 9.5)
STALE_HOURS = 6

ST_WAIT = "AD_330_10"    # 대기
ST_SETUP = "AD_330_11"   # 숫자설정중
ST_PLAY = "AD_330_12"    # 진행중
ST_CANCEL = "AD_330_14"  # 취소
END_LEAVE = "AD_340_12"  # 상대이탈


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def main():
    if not DB_PASS:
        log("STOCK_DB_PASS 미설정 - 종료")
        sys.exit(1)

    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10,
    )
    try:
        cur = conn.cursor()

        # 대상 먼저 조회해서 로그에 남긴다 (무엇이 정리됐는지 추적 가능하게)
        cur.execute(
            "SELECT ROOM_ID, ROOM_NM, STATUS, P1_ID, P2_ID, UPD_DT "
            "FROM TB_BASEBALL_MATCH "
            "WHERE DEL_YN='N' AND STATUS IN (%s,%s,%s) "
            "AND IFNULL(UPD_DT, REG_DT) < DATE_SUB(NOW(), INTERVAL %s HOUR)",
            (ST_WAIT, ST_SETUP, ST_PLAY, STALE_HOURS),
        )
        rows = cur.fetchall()

        if not rows:
            log("정리 대상 없음")
            return

        for r in rows:
            log("  취소: room=%s nm=%s status=%s p1=%s p2=%s last=%s"
                % (r[0], r[1], r[2], r[3], r[4], r[5]))

        cur.execute(
            "UPDATE TB_BASEBALL_MATCH "
            "SET STATUS=%s, END_TYPE=%s, END_DT=NOW(), UPD_DT=NOW() "
            "WHERE DEL_YN='N' AND STATUS IN (%s,%s,%s) "
            "AND IFNULL(UPD_DT, REG_DT) < DATE_SUB(NOW(), INTERVAL %s HOUR)",
            (ST_CANCEL, END_LEAVE, ST_WAIT, ST_SETUP, ST_PLAY, STALE_HOURS),
        )
        conn.commit()
        log("배치 완료 - 방 %d건 취소 처리 (기준: 마지막 활동 %d시간 경과)"
            % (cur.rowcount, STALE_HOURS))
    finally:
        conn.close()


if __name__ == "__main__":
    if not batch_status.is_enabled(BATCH_NM):
        batch_status.log_skip(BATCH_NM)
        sys.exit(0)

    batch_status.mark_start(BATCH_NM)
    try:
        main()
    except SystemExit as e:
        if e.code:
            batch_status.mark_failed(BATCH_NM, "sys.exit(%s)" % e.code)
        else:
            batch_status.mark_done(BATCH_NM)
        raise
    except Exception as e:
        batch_status.mark_failed(BATCH_NM, str(e))
        raise
    else:
        batch_status.mark_done(BATCH_NM)
