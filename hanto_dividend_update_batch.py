#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Node(routes/kiwoomApis.js::requestHantoDividendInfo / hantoDividendInfo/register,
frontend/src/pages/kiwoom/KiwoomJongmogViewPage.jsx::loadHantoDividendInfo)에
있던 "종목상세 화면에서 한투 API로 배당정보 조회 후 TB_API_JONGMOG에 반영" 로직을
파이썬 배치로 이관. 화면에서 종목 하나씩 열어봐야만 갱신되던 것을, 감시목록
전체(jsh77b@naver.com + etf@email.com) 종목에 대해 하루 1회 일괄 갱신한다.

- 배당정보는 한투(한국투자증권) Open API(국내주식)로 조회한다. 계정은 감시목록
  주인(jsh77b/etf)과 무관하게 항상 한투 고정 계정(jsh77b@naver.com, TB_HANTO_USER_SET)의
  APPKEY/토큰을 사용한다. (Node와 동일 - 한투 계좌 하나로 키움 종목의 배당정보만 조회)
- 최근 1년간 배당내역 합계를 DIVIDENDS_AMT로, 연간 지급횟수로 배당주기(CYCLE)를
  추정해서 TB_API_JONGMOG에 반영한다. (Node의 getDividendCycle과 동일 기준)

[실행방법 / cron 등록 예]
  STOCK_DB_PASS=xxx python3 hanto_dividend_update_batch.py
  # 매일 08:20, 크론 등록
  # 20 8 * * * /usr/bin/python3 /workspace/python01/hanto_dividend_update_batch.py >> /workspace/python01/log/hanto_dividend_update_$(date +\\%Y-\\%m-\\%d).log 2>&1
================================================================================
"""

import os
import sys
import time
from datetime import datetime, timedelta

import pymysql
import requests

DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"

HANTO_HOST = "https://openapi.koreainvestment.com:9443"
HANTO_TOKEN_ENDPOINT = "/oauth2/tokenP"
HANTO_DIVIDEND_ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-account-balance"
HANTO_FIXED_USER_ID = "jsh77b@naver.com"

MONITOR_USER_IDS = ("jsh77b@naver.com", "etf@email.com")
REQUEST_DELAY_SEC = 0.25  # 한투 API 호출 간격 (rate limit 대비)


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10, cursorclass=pymysql.cursors.DictCursor,
    )


def has_value(v):
    return v is not None and v != ""


def to_number(value):
    if not has_value(value):
        return 0
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0


def get_dividend_cycle(row_count):
    if row_count >= 12:
        return "월배당"
    if row_count >= 4:
        return "분기배당"
    if row_count >= 2:
        return "반기배당"
    return "연배당"


def get_hanto_user_info(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ACCNO, COMMISSION, TAX, APPKEY, SECRETKEY, TOKEN, TOKEN_ST_DT, TOKEN_ED_DT, TOKEN_TYPE, "
            "CASE WHEN NOW() > TOKEN_ED_DT THEN 'Y' ELSE 'N' END AS REFRESH_YN "
            "FROM TB_HANTO_USER_SET WHERE REG_ID = %s AND DEL_YN = 'N'",
            (HANTO_FIXED_USER_ID,),
        )
        return cur.fetchone()


def refresh_hanto_token(conn, user_info):
    resp = requests.post(
        HANTO_HOST + HANTO_TOKEN_ENDPOINT,
        json={"grant_type": "client_credentials", "appkey": user_info["APPKEY"], "appsecret": user_info["SECRETKEY"]},
        headers={"content-type": "application/json;charset=UTF-8"},
        timeout=30,
    )
    data = resp.json() if resp.content else {}
    token = data.get("access_token") or ""
    token_type = data.get("token_type") or "Bearer"
    expires_in = int(data.get("expires_in") or 0)
    expires_dt = datetime.now() + timedelta(seconds=expires_in if expires_in > 0 else 86400)

    if not token:
        raise RuntimeError("한투 토큰 갱신에 실패했습니다.")

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE TB_HANTO_USER_SET SET TOKEN_TYPE=%s, TOKEN=%s, TOKEN_ST_DT=NOW(), "
            "TOKEN_ED_DT=%s, UPD_DT=NOW() WHERE REG_ID=%s",
            (token_type, token, expires_dt.strftime("%Y-%m-%d %H:%M:%S"), HANTO_FIXED_USER_ID),
        )
    conn.commit()

    user_info = dict(user_info)
    user_info["TOKEN"] = token
    user_info["TOKEN_TYPE"] = token_type
    user_info["REFRESH_YN"] = "N"
    return user_info


def ensure_hanto_user_info(conn):
    user_info = get_hanto_user_info(conn)
    if not user_info:
        raise RuntimeError("한투 사용자 설정을 찾을 수 없습니다.")
    if not has_value(user_info.get("APPKEY")) or not has_value(user_info.get("SECRETKEY")):
        raise RuntimeError("한투 APPKEY 또는 SECRETKEY가 없습니다.")

    if user_info.get("REFRESH_YN") == "Y" or not has_value(user_info.get("TOKEN")) or not has_value(user_info.get("TOKEN_TYPE")):
        return refresh_hanto_token(conn, user_info)

    return user_info


def request_hanto_dividend_info(user_info, jongmog_cd, jongmog_nm):
    today = datetime.now()
    one_year_ago = today.replace(year=today.year - 1)

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{user_info['TOKEN_TYPE']} {user_info['TOKEN']}",
        "appkey": user_info["APPKEY"],
        "appsecret": user_info["SECRETKEY"],
        "tr_id": "HHKDB669102C0",
        "tr_cont": "N",
        "custtype": "P",
    }
    params = {
        "CTS": " ",
        "GB1": "0",
        "F_DT": one_year_ago.strftime("%Y%m%d"),
        "T_DT": today.strftime("%Y%m%d"),
        "SHT_CD": jongmog_cd,
        "HIGH_GB": " ",
    }
    resp = requests.get(HANTO_HOST + HANTO_DIVIDEND_ENDPOINT, headers=headers, params=params, timeout=30)
    data = resp.json() if resp.content else {}
    output_rows = data.get("output1") if isinstance(data.get("output1"), list) else []

    if not output_rows:
        return None

    first_row = output_rows[0]
    total_amount = sum(to_number(row.get("per_sto_divi_amt")) for row in output_rows)

    return {
        "record_date": first_row.get("record_date") or "",
        "jongmog_cd": jongmog_cd,
        "jongmog_nm": jongmog_nm or "",
        "dividend_amt": total_amount,
        "divi_rate": first_row.get("divi_rate") or "",
        "divi_pay_dt": first_row.get("divi_pay_dt") or "",
        "stk_kind": first_row.get("stk_kind") or "",
        "cycle": get_dividend_cycle(len(output_rows)),
        "row_count": len(output_rows),
    }


def get_monitor_stock_list(conn):
    with conn.cursor() as cur:
        placeholders = ",".join(["%s"] * len(MONITOR_USER_IDS))
        cur.execute(
            f"SELECT DISTINCT M.JONGMOG_CD, "
            f"(SELECT MAX(X.JONGMOG_NM) FROM TB_API_JONGMOG X WHERE X.JONGMOG_CD = M.JONGMOG_CD) AS JONGMOG_NM, "
            f"(SELECT X.DIVIDENDS_AMT FROM TB_API_JONGMOG X WHERE X.JONGMOG_CD = M.JONGMOG_CD) AS DIVIDENDS_AMT "
            f"FROM TB_API_JONGMOG_MONITOR_YN M "
            f"WHERE M.DEL_YN = 'N' AND M.REG_ID IN ({placeholders}) "
            f"ORDER BY M.JONGMOG_CD",
            MONITOR_USER_IDS,
        )
        return cur.fetchall()


def update_dividend_amt(conn, jongmog_cd, dividend_amt, cycle):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE TB_API_JONGMOG SET DIVIDENDS_AMT=%s, CYCLE=%s, UPD_DT=NOW() WHERE JONGMOG_CD=%s",
            (dividend_amt, cycle, jongmog_cd),
        )
        affected = cur.rowcount
    conn.commit()
    return affected


def main():
    conn = get_connection()
    try:
        user_info = ensure_hanto_user_info(conn)
    except Exception as e:
        log(f"한투 사용자 정보 준비 실패: {e}")
        conn.close()
        sys.exit(1)

    stocks = get_monitor_stock_list(conn)
    log(f"감시목록 대상 종목 수: {len(stocks)} (REG_ID: {', '.join(MONITOR_USER_IDS)})")

    updated_count = 0
    skip_count = 0
    error_count = 0

    for row in stocks:
        jongmog_cd = row["JONGMOG_CD"]
        jongmog_nm = row["JONGMOG_NM"]
        stored_dividend_amt = to_number(row["DIVIDENDS_AMT"])

        try:
            info = request_hanto_dividend_info(user_info, jongmog_cd, jongmog_nm)

            if not info or info["dividend_amt"] <= 0:
                skip_count += 1
                continue

            if info["dividend_amt"] == stored_dividend_amt:
                skip_count += 1
                continue

            update_dividend_amt(conn, jongmog_cd, info["dividend_amt"], info["cycle"])
            updated_count += 1
            log(f"배당정보 갱신: {jongmog_cd} {jongmog_nm} - {stored_dividend_amt} -> {info['dividend_amt']} ({info['cycle']})")
        except Exception as e:
            error_count += 1
            log(f"배당정보 조회 실패: {jongmog_cd} {jongmog_nm} - {e}")

        time.sleep(REQUEST_DELAY_SEC)

    log(f"배치 완료 updated={updated_count} skip={skip_count} error={error_count}")
    conn.close()


# 2026.09.11 배치관리(TB_BATCH_MASTER) 연동 - 사용여부 체크 + 상태 보고
import batch_status

BATCH_NM = "hanto_dividend_update_batch.py"


if __name__ == "__main__":
    if not batch_status.is_enabled(BATCH_NM):
        batch_status.log_skip(BATCH_NM)
        sys.exit(0)

    batch_status.mark_start(BATCH_NM)
    try:
        main()
    except SystemExit as e:
        if e.code:
            batch_status.mark_failed(BATCH_NM, f"sys.exit({e.code})")
        else:
            batch_status.mark_done(BATCH_NM)
        raise
    except Exception as e:
        batch_status.mark_failed(BATCH_NM, str(e))
        raise
    else:
        batch_status.mark_done(BATCH_NM)
