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

[사용여부 캐시]
DB 접속이 끊기면(예: 공인 IP 변경으로 카페24 외부접속 차단) 사용여부를 못 읽어서
"미사용"으로 꺼둔 배치까지 전부 실행되는 문제가 있었다. 그래서 조회에 성공할 때마다
결과를 로컬 캐시파일에 저장해두고, 조회 실패 시에는 그 캐시값을 사용한다.

  - 조회 성공  -> DB값 사용 + 캐시 갱신
  - 조회 실패  -> 캐시값 사용 (캐시 없으면 기존처럼 True)
  - 캐시 만료  -> CACHE_TTL_SEC 를 넘긴 캐시는 신뢰하지 않고 True

DB 장애 때 전 배치가 멈추는 것(fail-closed)도 위험하므로, 캐시가 없거나 너무 오래된
경우에만 기존 동작(무시하고 실행)으로 되돌아가는 절충 방식이다.
================================================================================
"""

import json
import os
import tempfile
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

# 사용여부 캐시 파일 (DB 조회 실패 시 폴백용)
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".batch_use_yn_cache.json")

# 캐시 유효기간(초) - 3일. 이보다 오래된 캐시는 신뢰하지 않는다.
CACHE_TTL_SEC = 3 * 24 * 60 * 60


def _log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [batch_status] {msg}", flush=True)


def _get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10,
    )


def _read_cache():
    """사용여부 캐시 파일 전체를 읽는다. 없거나 깨졌으면 빈 dict."""
    try:
        with open(CACHE_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _write_cache(batch_nm, enabled):
    """조회 성공한 사용여부를 캐시에 기록한다.
    여러 배치가 동시에 쓰므로 임시파일 생성 후 rename 으로 원자적으로 교체한다."""
    try:
        data = _read_cache()
        data[batch_nm] = {"enabled": bool(enabled), "ts": int(datetime.now().timestamp())}

        cache_dir = os.path.dirname(CACHE_PATH)
        fd, tmp_path = tempfile.mkstemp(dir=cache_dir, prefix=".batch_use_yn_cache.")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, ensure_ascii=False)
            os.rename(tmp_path, CACHE_PATH)
        except Exception:
            # 임시파일이 남지 않도록 정리
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    except Exception as e:
        _log(f"사용여부 캐시 기록 실패(무시) batch_nm={batch_nm}: {e}")


def _get_cached(batch_nm):
    """캐시된 사용여부를 돌려준다. 없거나 만료됐으면 None."""
    entry = _read_cache().get(batch_nm)
    if not isinstance(entry, dict) or "enabled" not in entry:
        return None

    age = int(datetime.now().timestamp()) - int(entry.get("ts", 0))
    if age > CACHE_TTL_SEC:
        return None
    return bool(entry["enabled"]), age


def is_enabled(batch_nm):
    """TB_BATCH_MASTER에 등록된 배치의 사용여부를 확인한다.
    미등록 배치는 항상 True(실행)로 처리한다.
    DB 조회에 실패하면 마지막으로 조회에 성공했던 캐시값을 사용한다."""
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
            enabled = row[0] != USE_YN_OFF
            # 조회 성공 -> 캐시 갱신
            _write_cache(batch_nm, enabled)
            return enabled
        finally:
            conn.close()
    except Exception as e:
        # 조회 실패 -> 캐시값으로 폴백 (캐시 없으면 기존대로 실행)
        cached = _get_cached(batch_nm)
        if cached is None:
            _log(f"사용여부 조회 실패(캐시 없음, 무시하고 실행) batch_nm={batch_nm}: {e}")
            return True

        enabled, age = cached
        _log(f"사용여부 조회 실패(캐시값 사용: {'사용' if enabled else '미사용'}, "
             f"{age // 60}분 전) batch_nm={batch_nm}: {e}")
        return enabled


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
