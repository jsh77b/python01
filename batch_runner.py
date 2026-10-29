#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
================================================================================
배치 수동 즉시실행 러너

관리자 > 배치관리 화면(batch_master_list.php)의 "즉시실행" 버튼을 누르면
PHP 가 TB_BATCH_MASTER.RUN_REQ_YN 을 'Y' 로 표시한다.
이 스크립트가 cron 으로 1분마다 돌면서 그 요청건을 찾아 실제 배치를 실행한다.

  [PHP 즉시실행 버튼] -> RUN_REQ_YN='Y'  (공용 DB: jsh77b.cafe24.com)
                              |
                              v
  [WSL batch_runner.py]  1분마다 폴링 -> 배치 실행 -> RUN_REQ_YN='N'
                              |
                              v
  각 배치가 batch_status 로 STATUS 를 갱신 -> 화면에 준비/시작/완료/실패 표시

[실행]
    */1 * * * * /usr/bin/python3 /workspace/python01/batch_runner.py \
                >> /workspace/python01/log/batch_runner_$(date +\%Y-\%m-\%d).log 2>&1

[안전장치]
  - BATCH_NM 은 관리자가 화면에서 직접 입력하는 자유값이므로 반드시 검증한다.
    (파일명 형식 검사 + BATCH_DIR 내 실제 존재 확인 -> 임의 명령 실행 방지)
  - 사용여부가 "미사용"인 배치는 즉시실행 요청이 와도 실행하지 않고 요청만 해제한다.
  - 요청 플래그는 실행 "전"에 먼저 해제한다. (배치가 오래 걸려도 다음 폴링에서 중복 실행되지 않게)
================================================================================
"""

import os
import re
import subprocess
import sys
from datetime import datetime

import pymysql

DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"

# 배치 파일이 모여있는 디렉터리 (이 디렉터리 밖은 실행하지 않는다)
BATCH_DIR = "/workspace/python01"
LOG_DIR = os.path.join(BATCH_DIR, "log")

# 배치 파일명 허용 형식 (영문소문자/숫자/언더스코어 + .py)
BATCH_NM_PATTERN = re.compile(r"^[a-z0-9_]+\.py$")

# 배치 1건당 최대 실행시간(초) - 넘어가면 강제 종료
RUN_TIMEOUT = 600

USE_YN_OFF = "AD_110_11"   # 미사용
STATUS_FAIL = "AD_320_14"  # 실패

# 이 러너 자신은 즉시실행 대상에서 제외한다 (무한 재귀 방지)
SELF_NM = "batch_runner.py"


def _log(msg):
    print("[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def _get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10,
    )


def _clear_run_req(conn, seq):
    """실행요청 플래그 해제"""
    cur = conn.cursor()
    cur.execute(
        "UPDATE TB_BATCH_MASTER SET RUN_REQ_YN='N' WHERE SEQ=%s",
        (seq,),
    )
    conn.commit()


def _mark_failed(conn, seq, batch_nm, reason):
    """실행 불가/실패 시 상태를 '실패'로 남긴다"""
    cur = conn.cursor()
    cur.execute(
        "UPDATE TB_BATCH_MASTER SET STATUS=%s, STATUS_UPD_DT=NOW() WHERE SEQ=%s",
        (STATUS_FAIL, seq),
    )
    conn.commit()
    _log("실행실패 batch_nm=%s: %s" % (batch_nm, reason))


def _run_batch(batch_nm):
    """배치를 실제로 실행하고 결과 로그는 해당 배치의 일별 로그파일에 이어붙인다"""
    batch_path = os.path.join(BATCH_DIR, batch_nm)
    log_name = "%s_%s.log" % (batch_nm[:-3], datetime.now().strftime("%Y-%m-%d"))
    log_path = os.path.join(LOG_DIR, log_name)

    with open(log_path, "a") as lf:
        lf.write("\n[%s] ===== 화면 즉시실행 =====\n"
                 % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        lf.flush()
        proc = subprocess.Popen(
            [sys.executable, batch_path],
            stdout=lf, stderr=subprocess.STDOUT, cwd=BATCH_DIR,
        )
        try:
            rc = proc.wait(timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise Exception("실행시간 초과(%d초)" % RUN_TIMEOUT)

    if rc != 0:
        raise Exception("종료코드 %d (로그: %s)" % (rc, log_name))
    return log_name


def main():
    if not DB_PASS:
        _log("STOCK_DB_PASS 미설정 - 종료")
        return

    try:
        conn = _get_connection()
    except Exception as e:
        _log("DB 접속 실패: %s" % e)
        return

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT SEQ, BATCH_NM, USE_YN FROM TB_BATCH_MASTER "
            "WHERE RUN_REQ_YN='Y' AND DEL_YN='N' ORDER BY RUN_REQ_DT ASC"
        )
        rows = cur.fetchall()

        if not rows:
            return

        _log("즉시실행 요청 %d건" % len(rows))

        for seq, batch_nm, use_yn in rows:
            batch_nm = (batch_nm or "").strip()

            # 중복 실행 방지를 위해 실행 전에 플래그부터 해제
            _clear_run_req(conn, seq)

            # 1) 파일명 형식 검증 (임의 명령 실행 방지)
            if not BATCH_NM_PATTERN.match(batch_nm):
                _mark_failed(conn, seq, batch_nm, "허용되지 않는 배치 파일명")
                continue

            # 2) 러너 자기자신 제외
            if batch_nm == SELF_NM:
                _mark_failed(conn, seq, batch_nm, "러너 자신은 실행할 수 없음")
                continue

            # 3) 실제 파일 존재 확인
            batch_path = os.path.join(BATCH_DIR, batch_nm)
            if not os.path.isfile(batch_path):
                _mark_failed(conn, seq, batch_nm, "배치 파일 없음 (%s)" % batch_path)
                continue

            # 4) 사용여부가 "미사용"이면 실행하지 않는다
            if use_yn == USE_YN_OFF:
                _log("미사용 배치라 즉시실행 무시 batch_nm=%s" % batch_nm)
                continue

            # 5) 실행
            _log("실행시작 batch_nm=%s" % batch_nm)
            try:
                log_name = _run_batch(batch_nm)
                _log("실행완료 batch_nm=%s (로그: %s)" % (batch_nm, log_name))
            except Exception as e:
                _mark_failed(conn, seq, batch_nm, str(e))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
