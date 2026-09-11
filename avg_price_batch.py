#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
전체 감시종목 대상 키움 주문평단가(HOUR 칼럼) 일괄 갱신 배치.

기존에는 jongmog_view2.php(PHP)/KiwoomJongmogViewPage.jsx(Node) 접속 시에만
종목 1개씩 온디맨드로 계좌평가현황(kt00004)을 조회해 평단가를 갱신했는데,
아무도 화면을 안 보면 갱신이 안 되는 문제가 있어 하루 2회(08:00, 13:00) 전체
계정 x 전체 종목을 일괄 갱신하는 배치로 추가한다.

로직은 jongmog_view2.php에 추가했던 자동갱신과 동일:
  - 미청산 주문이력(ORDER_STAT='AD_240_11', CLOSE_YN='N', DEL_YN='N')이 있는 종목만
    실제 키움 계좌평가현황(kt00004)의 평단가로 HOUR 갱신
  - 미청산 주문이력이 없는 종목은 HOUR를 NULL로 강제 초기화

[실행방법 / cron 등록 예]
  STOCK_DB_PASS=xxx python3 avg_price_batch.py
  # 1일 2회(08:00, 13:00)
  # 0 8,13 * * * /usr/bin/python3 /workspace/python01/avg_price_batch.py >> /workspace/python01/log/avg_price_$(date +\\%Y-\\%m-\\%d).log 2>&1
================================================================================
"""

import os
import re
import sys
from datetime import datetime

import pymysql
import requests

DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"

KIWOOM_HOST = "https://api.kiwoom.com"
TOKEN_ENDPOINT = "/oauth2/token"
ACCOUNT_ENDPOINT = "/api/dostk/acnt"
MAX_PAGE = 10


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10, cursorclass=pymysql.cursors.DictCursor,
    )


# scripts/common.js::normalizeStockCode 와 동일
def normalize_stock_code(value):
    text = re.sub(r"[^0-9A-Za-z]", "", re.sub(r"^A", "", str(value or ""), flags=re.IGNORECASE))
    if text.isdigit() and len(text) < 6:
        return text.zfill(6)
    return text


# scripts/common.js::formatKiwoomDateTime 와 동일 (14자리 YYYYMMDDHHmmss -> 'YYYY-MM-DD HH:mm:ss')
def format_kiwoom_datetime(value):
    text = str(value or "")
    if re.fullmatch(r"\d{14}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}:{text[12:14]}"
    return text


def get_active_users(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT REG_ID, ACCNO, APPKEY, SECRETKEY, TOKEN, TOKEN_TYPE,
                   CASE WHEN NOW() > TOKEN_ED_DT THEN 'Y' ELSE 'N' END AS REFRESH_YN
            FROM   TB_API_USER_SET
            WHERE  DEL_YN = 'N'
            """
        )
        return cur.fetchall()


def refresh_token(conn, user):
    response = requests.post(
        KIWOOM_HOST + TOKEN_ENDPOINT,
        json={"grant_type": "client_credentials", "appkey": user["APPKEY"], "secretkey": user["SECRETKEY"]},
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=30,
    )
    data = response.json() or {}
    token_type = data.get("token_type")
    token = data.get("token")
    expires_dt = data.get("expires_dt")

    if not (token_type and token and expires_dt):
        raise Exception(f"토큰 갱신 응답 이상: {data}")

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE TB_API_USER_SET SET TOKEN_TYPE=%s, TOKEN=%s, TOKEN_ST_DT=NOW(), "
            "TOKEN_ED_DT=%s, UPD_DT=NOW() WHERE REG_ID=%s",
            (token_type, token, format_kiwoom_datetime(expires_dt), user["REG_ID"]),
        )
    conn.commit()

    user["TOKEN_TYPE"] = token_type
    user["TOKEN"] = token
    return user


def fetch_account_holdings(user):
    """계좌평가현황(kt00004) 페이지네이션 조회 -> {종목코드: 평단가} 맵"""
    holdings = {}
    cont_yn = "N"
    next_key = ""

    for _ in range(MAX_PAGE):
        response = requests.post(
            KIWOOM_HOST + ACCOUNT_ENDPOINT,
            json={"qry_tp": "0", "dmst_stex_tp": "KRX"},
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"{user['TOKEN_TYPE']} {user['TOKEN']}",
                "cont-yn": cont_yn,
                "next-key": next_key,
                "api-id": "kt00004",
            },
            timeout=30,
        )
        data = response.json() or {}
        rows = data.get("stk_acnt_evlt_prst") or []

        for row in rows:
            code = normalize_stock_code(row.get("stk_cd"))
            avg_price = int(row.get("avg_prc") or 0)
            if code and avg_price > 0:
                holdings[code] = avg_price

        cont_yn = response.headers.get("cont-yn", "N")
        next_key = response.headers.get("next-key", "")
        if cont_yn != "Y" or not next_key:
            break

    return holdings


def get_open_order_codes(conn, reg_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT JONGMOG_CD FROM TB_API_ORDER_HIST "
            "WHERE REG_ID=%s AND CLOSE_YN='N' AND DEL_YN='N' AND ORDER_STAT='AD_240_11'",
            (reg_id,),
        )
        return {r["JONGMOG_CD"] for r in cur.fetchall()}


def get_monitor_rows(conn, reg_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT JONGMOG_CD, HOUR FROM TB_API_JONGMOG_MONITOR_YN WHERE REG_ID=%s AND DEL_YN='N'",
            (reg_id,),
        )
        return cur.fetchall()


def update_avg_price(conn, jongmog_cd, avg_price, reg_id):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE TB_API_JONGMOG_MONITOR_YN SET HOUR=%s, UPD_DT=NOW() WHERE JONGMOG_CD=%s AND REG_ID=%s",
            (avg_price, jongmog_cd, reg_id),
        )
    conn.commit()


def process_user(conn, user):
    reg_id = user["REG_ID"]

    if user["REFRESH_YN"] == "Y":
        try:
            user = refresh_token(conn, user)
        except Exception as e:
            log(f"[{reg_id}] 토큰 갱신 실패, 이번 사이클 건너뜀: {e}")
            return

    try:
        holdings = fetch_account_holdings(user)
    except Exception as e:
        log(f"[{reg_id}] 계좌평가현황 조회 실패: {e}")
        return

    open_codes = get_open_order_codes(conn, reg_id)
    monitor_rows = get_monitor_rows(conn, reg_id)

    updated = 0
    cleared = 0
    skipped_legacy = 0
    for row in monitor_rows:
        code = row["JONGMOG_CD"]
        raw_hour = row["HOUR"]

        # HOUR 칼럼은 과거 "매도 마감시간" 공통코드(예: AD_250_10)로 쓰이던 값이 일부 종목에
        # 아직 남아있다(신규 평단가 기능 적용 전 종목). 숫자가 아니면 그 레거시 값으로 보고,
        # 미청산 주문이 없는 경우에는 건드리지 않는다(실수로 지우면 복구 불가).
        is_numeric = raw_hour not in (None, "") and re.fullmatch(r"-?\d+", str(raw_hour)) is not None
        cur_hour = int(raw_hour) if is_numeric else 0

        if code in open_codes:
            new_avg = holdings.get(code)
            if new_avg and new_avg > 0 and (not is_numeric or new_avg != cur_hour):
                update_avg_price(conn, code, new_avg, reg_id)
                updated += 1
        else:
            if not is_numeric:
                skipped_legacy += 1
            elif cur_hour != 0:
                update_avg_price(conn, code, None, reg_id)
                cleared += 1

    log(f"[{reg_id}] 평단가 갱신 {updated}건, 초기화 {cleared}건, 레거시값 보존 {skipped_legacy}건 "
        f"(모니터종목 {len(monitor_rows)}건, 실보유 {len(holdings)}건)")


def main():
    if not DB_PASS:
        log("STOCK_DB_PASS 환경변수가 없습니다. 종료합니다.")
        sys.exit(1)

    conn = get_connection()
    try:
        users = get_active_users(conn)
        log(f"대상 계정 {len(users)}건")
        for user in users:
            process_user(conn, user)
    finally:
        conn.close()


# 2026.09.11 배치관리(TB_BATCH_MASTER) 연동 - 사용여부 체크 + 상태 보고
import batch_status

BATCH_NM = "avg_price_batch.py"


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
