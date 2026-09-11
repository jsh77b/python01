#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
배치 공통 헬퍼 - 관리자 > 배치관리 화면(TB_BATCH_MASTER)과 연동

각 배치 스크립트의 진입점에서 아래 두 가지를 처리한다.
  1. 사용여부 체크: 관리자가 화면에서 "미사용"으로 꺼두면 배치 로직을 실행하지 않고 즉시 종료
  2. 상태 보고: 배치 시작/완료/실패 시점마다 TB_BATCH_MASTER.STATUS/STATUS_UPD_DT 갱신

[사용법]
    import batch_status

    BATCH_NM = "stock_monitor.py"

    if not batch_status.is_enabled(BATCH_NM):
        batch_status.log_skip(BATCH_NM)
        sys.exit(0)

    batch_status.mark_start(BATCH_NM)
    try:
        ... 배치 로직 ...
        batch_status.mark_done(BATCH_NM)
    except Exception as e:
        batch_status.mark_failed(BATCH_NM, str(e))
        raise

TB_BATCH_MASTER에 등록되지 않은 배치명으로 호출하면(아직 관리자 화면에 등록 전)
조용히 무시하고 항상 실행(is_enabled=True)한다 - 등록 누락으로 배치가 갑자기
멈추는 사고를 막기 위함.
================================================================================
"""

import os
from datetime import datetime

import pymysql

DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"

STATUS_READY = "AD_320_10"   # 준비
STATUS_START = "AD_320_11"   # 시작
STATUS_DONE = "AD_320_12"    # 완료
STATUS_CANCEL = "AD_320_13"  # 취소
STATUS_FAIL = "AD_320_14"    # 실패

USE_YN_ON = "AD_110_10"    # 사용
USE_YN_OFF = "AD_110_11"   # 미사용


def _log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [batch_status] {msg}", flush=True)


def _get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10,
    )


def is_enabled(batch_nm):
    """TB_BATCH_MASTER에 등록된 배치의 사용여부를 확인한다.
    미등록 배치는 항상 True(실행)로 처리한다."""
    if not DB_PASS:
        return True
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT USE_YN FROM TB_BATCH_MASTER WHERE BATCH_NM=%s AND DEL_YN='N'",
                (batch_nm,),
            )
            row = cur.fetchone()
            if row is None:
                return True
            return row[0] != USE_YN_OFF
        finally:
            conn.close()
    except Exception as e:
        _log(f"사용여부 조회 실패(무시하고 실행) batch_nm={batch_nm}: {e}")
        return True


def log_skip(batch_nm):
    _log(f"미사용 설정으로 스킵 batch_nm={batch_nm}")


def _update_status(batch_nm, status):
    if not DB_PASS:
        return
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE TB_BATCH_MASTER SET STATUS=%s, STATUS_UPD_DT=NOW() "
                "WHERE BATCH_NM=%s AND DEL_YN='N'",
                (status, batch_nm),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        _log(f"상태 갱신 실패 batch_nm={batch_nm} status={status}: {e}")


def mark_start(batch_nm):
    _update_status(batch_nm, STATUS_START)


def mark_done(batch_nm):
    _update_status(batch_nm, STATUS_DONE)


def mark_failed(batch_nm, error_msg=None):
    _update_status(batch_nm, STATUS_FAIL)
    if error_msg:
        _log(f"실패 batch_nm={batch_nm} error={error_msg}")
