#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Node(services/kiwoomApi.js::runCurrentPriceBiz)에 있던 키움 자동매매 배치를
파이썬으로 이관. 토큰갱신 / 계좌평가 / 현재가수집 / 주문상태동기화 / 매수매도
판단·실행까지 전체 로직을 그대로 포팅했다. (배치로그 TB_KIWOOM_BATCH_* 기능은
필요없다는 요청에 따라 포팅하지 않음)

*** 안전장치: KIWOOM_BATCH_LIVE 환경변수로 실거래 여부를 제어한다 ***

  - KIWOOM_BATCH_LIVE 미설정(또는 "0") = DRY-RUN(기본값, 검증모드)
    실제 매수/매도/취소 주문을 키움에 보내지 않고, 감시목록 현재가 DB 저장과
    일일정리(오전 배치)도 건너뛴다. (Node가 이미 매분 이 테이블들을 갱신하고
    있어서, 파이썬이 같은 테이블에 동시에 쓰면 Node의 실거래 판단에 쓰이는
    추세 데이터가 흔들릴 수 있기 때문) 그 외 조회성 로직(현재가/계좌평가/체결
    조회, 매수매도 판단 계산, 로그/메일 발송)은 전부 동일하게 실행되므로 Node
    로그와 나란히 비교 검증할 수 있다.
    - 토큰갱신(REFRESH), 계좌평가현황 DB 갱신, 체결정보 기록은 매매 액션이
      아니라 상태 반영/캐시 성격이라 DRY-RUN에서도 그대로 실행한다.
  - KIWOOM_BATCH_LIVE=1 = 운영모드. Node와 동일하게 전부 실거래로 동작한다.
    Node 쪽 호출부(web.js callKiwoomBatchService)를 지운 뒤 이 모드로 전환한다.

[실행방법 / cron 등록 예 - 검증기간]
  STOCK_DB_PASS=xxx STOCK_MAIL_PASS=xxx python3 kiwoom_trading_batch.py
  # 평일 08:00~20:00, 1분마다 (Node의 1분 배치 주기와 동일)
  # * 8-20 * * 1-5 /usr/bin/python3 /workspace/python01/kiwoom_trading_batch.py >> /workspace/python01/log/kiwoom_trading_$(date +\\%Y-\\%m-\\%d).log 2>&1
  # 운영 전환시 위 cron 앞에 KIWOOM_BATCH_LIVE=1 을 추가한다.
================================================================================
"""

import math
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText

import pymysql
import requests

# ------------------------------------------------------------------
# 환경 설정
# ------------------------------------------------------------------
DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
MAIL_SENDER = "ack1000hu@gmail.com"
MAIL_RECEIVER = "ack1000hu@gmail.com"
MAIL_PASS = os.getenv("STOCK_MAIL_PASS", "")

LIVE = os.getenv("KIWOOM_BATCH_LIVE") == "1"           # 실거래 모드 여부
TEST_MODE = os.getenv("KIWOOM_BATCH_TEST_MODE") == "1"  # Node testMode와 동일 (실행시간 체크 무시)
FORCE_RUN = os.getenv("KIWOOM_BATCH_FORCE") == "1"
USER_ID_FILTER = os.getenv("KIWOOM_BATCH_USER_ID", "")

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
DAILY_MAINTENANCE_STATE_FILE = os.path.join(STATE_DIR, "kiwoom_daily_maintenance.state")

KIWOOM_HOST = "https://api.kiwoom.com"
ORDER_ENDPOINT = "/api/dostk/ordr"
ACCOUNT_ENDPOINT = "/api/dostk/acnt"
STOCK_INFO_ENDPOINT = "/api/dostk/stkinfo"
TOKEN_ENDPOINT = "/oauth2/token"
CURRENT_PRICE_CHUNK_SIZE = 50
ADDITIONAL_BUY_COOLDOWN_MINUTES = 30
DAILY_MAX_ADDITIONAL_BUY_COUNT = 2
STOP_LOSS_PROFIT_RATE = -20        # 2026.07.30 로직 정지 - 현재 미사용 (Node와 동일)
DOWNTREND_LOOKBACK_DAYS = 10
DOWNTREND_DECLINE_RATE = -10
RECENT_SELL_BLOCK_DAYS = 2


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------
# scripts/common.js 포팅
# ------------------------------------------------------------------
def has_value(v):
    return v is not None and v != ""


def get_row_value(row, key):
    if not row:
        return None
    if key in row and has_value(row[key]):
        return row[key]
    if key.upper() in row and has_value(row[key.upper()]):
        return row[key.upper()]
    return row.get(key.lower())


def require_value(v, name):
    if not has_value(v):
        raise ValueError(f"{name} 값이 필요합니다.")


def get_input_value(input_dict, names):
    for n in names:
        if has_value(input_dict.get(n)):
            return input_dict.get(n)
    return ""


def get_response_value(data, names):
    if not data:
        return ""
    for n in names:
        if has_value(data.get(n)):
            return data.get(n)
    return ""


def get_first_list(data, names):
    if not data:
        return []
    for n in names:
        v = data.get(n)
        if isinstance(v, list):
            return v
    return []


def normalize_number(value):
    text = str(value if value is not None else "").replace(",", "").replace("+", "").strip()
    if text.startswith("-"):
        text = text[1:]
    return text or "0"


def normalize_signed_number(value):
    text = str(value if value is not None else "").replace(",", "").replace("+", "").strip()
    return text or "0"


def to_number(value):
    try:
        return float(normalize_number(value))
    except ValueError:
        return 0.0


def to_signed_number(value):
    try:
        return float(normalize_signed_number(value))
    except ValueError:
        return 0.0


def normalize_order_no(value):
    text = str(value or "")
    stripped = re.sub(r"^0+", "", text).strip()
    return stripped or text.strip()


def is_same_order_no(left, right):
    return normalize_order_no(left) == normalize_order_no(right)


def normalize_stock_code(value):
    text = str(value or "").strip()
    text = re.sub(r"^[Aa]", "", text)
    text = re.sub(r"[^0-9A-Za-z]", "", text)
    if re.fullmatch(r"\d+", text) and len(text) < 6:
        text = text.zfill(6)
    return text


def get_date_text(dt):
    return dt.strftime("%Y-%m-%d")


def should_run_daily_morning_maintenance(now):
    return now.hour == 8 and 0 <= now.minute <= 1


def split_array(items, chunk_size):
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def format_kiwoom_date_time(value):
    text = str(value or "")
    if re.fullmatch(r"\d{14}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}:{text[12:14]}"
    return text


def get_token_expire_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def is_yes(value):
    return str(value or "").upper() == "Y"


def get_error_message(error):
    return str(error)


# ------------------------------------------------------------------
# DB 연결/실행 헬퍼
# ------------------------------------------------------------------
def get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10, cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_all(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()


def fetch_one(conn, sql, params=None):
    rows = fetch_all(conn, sql, params)
    return rows[0] if rows else None


def execute_write(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
        conn.commit()
        return {"affectedRows": cur.rowcount, "insertId": cur.lastrowid}


def db_write(conn, sql, params, description):
    """실거래 액션과 무관한(정보 캐시성) DB 쓰기 - LIVE 여부와 무관하게 항상 실행"""
    return execute_write(conn, sql, params)


def guarded_db_write(conn, sql, params, description):
    """감시목록 현재가 저장/일일정리 등, Node와 동시 실행시 실거래 판단에 영향을 줄 수
    있는 쓰기 - DRY-RUN에서는 건너뛴다."""
    if not LIVE:
        log(f"[DRY-RUN] DB 쓰기 스킵 - {description}")
        return {"affectedRows": 0, "insertId": None}
    return execute_write(conn, sql, params)


def load_last_daily_maintenance_date():
    try:
        with open(DAILY_MAINTENANCE_STATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def save_last_daily_maintenance_date(date_text):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(DAILY_MAINTENANCE_STATE_FILE, "w", encoding="utf-8") as f:
        f.write(date_text)


# ------------------------------------------------------------------
# queries/kiwoomApiQueries.js 포팅 (사용하는 쿼리만)
# ------------------------------------------------------------------
def query_chk_holiday(base_dt):
    sql = (
        "SELECT COUNT(1) AS HOLIDAY_CNT FROM TB_HOLIDAY WHERE 1=1 "
        "AND HOLIDAY_GBN IN ('AD_180_11','AD_180_12') AND DEL_YN = 'N' "
        "AND CASE WHEN AGAIN_GBN IN ('AD_190_10') "
        "THEN CONCAT(DATE_FORMAT(%s, '%%Y'), '-', DATE_FORMAT(HOLIDAY_DT, '%%m-%%d')) "
        "ELSE DATE_FORMAT(HOLIDAY_DT, '%%Y-%%m-%%d') END = DATE_FORMAT(%s, '%%Y-%%m-%%d')"
    )
    return sql, [base_dt, base_dt]


def query_get_api_user_set_list(user_id):
    sql = (
        "SELECT REG_ID, ACCNO, APPKEY, SECRETKEY, TOKEN_TYPE, TOKEN, TOKEN_ED_DT, BUY_PER "
        "FROM TB_API_USER_SET WHERE 1=1 AND DEL_YN = 'N'"
    )
    params = []
    if has_value(user_id):
        sql += " AND REG_ID = %s"
        params.append(user_id)
    sql += " ORDER BY REG_ID"
    return sql, params


def query_set_accno_info(accno, user_id):
    sql = (
        "SELECT COMMISSION, TAX, APPKEY, SECRETKEY, REG_ID, ACCNO, "
        "CASE WHEN NOW() > TOKEN_ED_DT THEN 'Y' ELSE 'N' END REFRESH_YN, TOKEN, TOKEN_TYPE "
        "FROM TB_API_USER_SET WHERE 1=1"
    )
    params = []
    if has_value(accno):
        sql += " AND ACCNO = %s"
        params.append(accno)
    if has_value(user_id):
        sql += " AND REG_ID = %s"
        params.append(user_id)
    sql += " AND DEL_YN = 'N'"
    return sql, params


def query_set_token_refresh(token_type, token, token_ed_dt, user_id):
    sql = (
        "UPDATE TB_API_USER_SET SET TOKEN_TYPE = %s, TOKEN = %s, TOKEN_ST_DT = NOW(), "
        "TOKEN_ED_DT = DATE_FORMAT(%s, '%%Y-%%m-%%d %%H:%%i:%%s'), UPD_DT = NOW() "
        "WHERE 1=1 AND REG_ID = %s"
    )
    return sql, [token_type, token, token_ed_dt, user_id]


def query_set_accno_amt(d2_entra, tot_pur_amt, prsm_dpst_aset_amt, user_id):
    sql = (
        "UPDATE TB_API_USER_SET SET D2_ENTRA = %s, TOT_PUR_AMT = %s, "
        "PRSM_DPST_ASET_AMT = %s, UPD_DT = NOW() WHERE 1=1 AND REG_ID = %s"
    )
    return sql, [d2_entra, tot_pur_amt, prsm_dpst_aset_amt, user_id]


def query_get_monitor_list():
    sql = (
        "SELECT A.* FROM ("
        "  SELECT AJMY.JONGMOG_CD"
        "       , (SELECT MAX(X.JONGMOG_NM) FROM TB_API_JONGMOG X WHERE X.JONGMOG_CD = AJMY.JONGMOG_CD) AS JONGMOG_NM"
        "       , (SELECT COUNT(1) FROM TB_API_ORDER_HIST WHERE JONGMOG_CD = AJMY.JONGMOG_CD AND ORDER_STAT = 'AD_240_11' AND CLOSE_YN = 'N') AS ORDER_CNT"
        "       , AJMY.ORDER_YN"
        "  FROM TB_API_JONGMOG_MONITOR_YN AJMY"
        "  WHERE 1=1 AND AJMY.DEL_YN = 'N'"
        "  GROUP BY AJMY.JONGMOG_CD"
        ") A ORDER BY A.ORDER_CNT DESC, A.ORDER_YN, A.JONGMOG_CD"
    )
    return sql, []


# 2026.09.11 사용자별 실제 감시종목 집합(REG_ID -> {JONGMOG_CD}) - 현재가는 종목당 한 번만 조회하려고
# monitor_rows를 전체 사용자 통합으로 가져오지만, 매수/매도 판단은 본인이 감시하는 종목만 하도록 별도 조회
def query_get_monitor_codes_by_user():
    sql = "SELECT REG_ID, JONGMOG_CD FROM TB_API_JONGMOG_MONITOR_YN WHERE 1=1 AND DEL_YN = 'N'"
    return sql, []


def query_set_order_list(jongmog_cd, user_id):
    sql = (
        "SELECT AOH.JONGMOG_CD"
        "     , (SELECT ROUND(AVG(X.BUY_PRICE)) FROM TB_API_ORDER_HIST X WHERE 1=1"
        "        AND X.JONGMOG_CD = AOH.JONGMOG_CD AND X.REG_ID = AOH.REG_ID"
        "        AND X.ORDER_STAT = 'AD_240_11' AND X.CLOSE_YN = 'N') AS BUR_PRICE_AVG"
        "     , AOH.BUY_CNT AS BUY_CNT, AOH.IDX, AOH.BUY_CNT * 1 AS SELL_CNT"
        "     , AOH.ORDER_NO, AOH.ORDER_STAT"
        "     , (SELECT IFNULL(ORDER_STATUS,'') FROM TB_API_ORDER_HIST X WHERE X.IDX = AOH.IDX) AS ORDER_STATUS"
        "     , AOH.BUY_PRICE AS BUY_PRICE_MIN"
        " FROM TB_API_ORDER_HIST AOH"
        " WHERE 1=1 AND AOH.JONGMOG_CD = %s AND AOH.REG_ID = %s"
        " AND AOH.CLOSE_YN = 'N' AND AOH.ORDER_STAT IN ('AD_240_11','AD_240_10')"
        " ORDER BY AOH.BUY_PRICE LIMIT 0,1"
    )
    return sql, [jongmog_cd, user_id]


def query_get_recent_buy_order_info(jongmog_cd, user_id, minute_gap):
    sql = (
        "SELECT COUNT(1) AS CNT, MAX(AOH.REG_DT) AS LAST_REG_DT FROM TB_API_ORDER_HIST AOH"
        " WHERE 1=1 AND AOH.JONGMOG_CD = %s AND AOH.REG_ID = %s"
        " AND AOH.ORDER_STAT IN ('AD_240_10','AD_240_11') AND AOH.CLOSE_YN = 'N'"
        " AND AOH.REG_DT >= DATE_SUB(NOW(), INTERVAL %s MINUTE)"
    )
    return sql, [jongmog_cd, user_id, minute_gap]


def query_get_today_buy_order_count(jongmog_cd, user_id):
    sql = (
        "SELECT COUNT(1) AS CNT FROM TB_API_ORDER_HIST AOH WHERE 1=1"
        " AND AOH.JONGMOG_CD = %s AND AOH.REG_ID = %s"
        " AND AOH.ORDER_STAT IN ('AD_240_10','AD_240_11') AND AOH.REG_DT >= DATE(NOW())"
    )
    return sql, [jongmog_cd, user_id]


def query_get_recent_sell_info(jongmog_cd, user_id, day_gap):
    sql = (
        "SELECT COUNT(1) AS CNT, MAX(AOH.CLOSE_DT) AS LAST_CLOSE_DT FROM TB_API_ORDER_HIST AOH"
        " WHERE 1=1 AND AOH.JONGMOG_CD = %s AND AOH.REG_ID = %s"
        " AND AOH.CLOSE_YN = 'Y' AND AOH.CLOSE_DT >= DATE_SUB(DATE(NOW()), INTERVAL %s DAY)"
    )
    return sql, [jongmog_cd, user_id, day_gap]


def query_insert_api_order_hist(jongmog_cd, basic_price, order_price, buy_cnt, user_id,
                                 buy_point_amt, sell_point_amt, accno, commission, tax,
                                 order_no, order_cnt, s_msg):
    sql = (
        "INSERT INTO TB_API_ORDER_HIST ("
        "JONGMOG_CD, BASIC_PRICE, BUY_PRICE, BUY_CNT, SELL_PRICE, SELL_CNT, ORDER_STAT, DEL_YN,"
        " REG_DT, UPD_DT, REG_ID, BUY_POINT, SELL_POINT, ACCNO, COMMISSION, TAX, ORDER_NO,"
        " BUY_ORDER_CNT, RESULT_MSG"
        ") VALUES (%s, %s, %s, %s, 0, 0, 'AD_240_10', 'N', NOW(), NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    return sql, [jongmog_cd, basic_price, order_price, buy_cnt, user_id, buy_point_amt,
                 sell_point_amt, accno, commission, tax, order_no, order_cnt, s_msg]


def query_update_api_order_hist(order_price, buy_cnt, cur_price_max, order_cnt, jongmog_cd,
                                 user_id, order_idx, order_no, s_msg):
    sql = (
        "UPDATE TB_API_ORDER_HIST SET SELL_PRICE = %s, SELL_CNT = %s, UPD_DT = NOW(),"
        " SELL_BASIC_PRICE = %s, SELL_ORDER_CNT = %s, ORDER_STATUS = %s WHERE 1=1 AND IDX = %s"
    )
    return sql, [order_price, buy_cnt, cur_price_max, order_cnt, order_no, order_idx]


def query_update_api_order_hist_stat(jongmog_cd, user_id, order_no, order_stat):
    sql = (
        "UPDATE TB_API_ORDER_HIST SET ORDER_STAT = %s, UPD_DT = NOW() WHERE 1=1"
        " AND JONGMOG_CD = %s AND REG_ID = %s AND ORDER_NO = %s"
    )
    return sql, [order_stat, jongmog_cd, user_id, order_no]


def query_update_api_order_hist_close(jongmog_cd, user_id, order_no, order_status, close_yn):
    sql = (
        "UPDATE TB_API_ORDER_HIST SET CLOSE_YN = %s, CLOSE_DT = NOW(), UPD_DT = NOW(),"
        " ORDER_STATUS = %s WHERE 1=1 AND JONGMOG_CD = %s AND REG_ID = %s AND ORDER_STATUS = %s"
    )
    return sql, [close_yn, order_status, jongmog_cd, user_id, order_no]


def query_update_api_order_hist_sell_cancel(jongmog_cd, user_id, order_no):
    sql = (
        "UPDATE TB_API_ORDER_HIST SET CLOSE_YN = 'N', CLOSE_DT = NULL, UPD_DT = NOW(),"
        " ORDER_STATUS = '' WHERE 1=1 AND JONGMOG_CD = %s AND REG_ID = %s AND ORDER_STATUS = %s"
    )
    return sql, [jongmog_cd, user_id, order_no]


def query_reg_cur_price(jongmog_cd, current_price, trde_qty):
    sql = (
        "INSERT INTO TB_API_CUR_PRICE (JONGMOG_CD, CUR_PRICE, TRDE_QTY, REG_DT)"
        " SELECT %s, %s, %s, NOW() FROM DUAL WHERE NOT EXISTS ("
        "   SELECT 1 FROM ("
        "     SELECT TRDE_QTY FROM TB_API_CUR_PRICE WHERE JONGMOG_CD = %s"
        "     ORDER BY REG_DT DESC LIMIT 0,1"
        "   ) A WHERE IFNULL(A.TRDE_QTY, '') = %s"
        " )"
    )
    return sql, [jongmog_cd, current_price, trde_qty, jongmog_cd, trde_qty]


def query_reg_cur_price_update(jongmog_cd, current_price):
    sql = (
        "UPDATE TB_API_JONGMOG AJ SET CUR_PRICE = %s,"
        " CUR_PRICE_MIN = (SELECT MIN(ABS(CUR_PRICE)) FROM TB_API_CUR_PRICE X WHERE X.JONGMOG_CD = AJ.JONGMOG_CD AND X.REG_DT > DATE(NOW())),"
        " CUR_PRICE_MAX = (SELECT MAX(ABS(CUR_PRICE)) FROM TB_API_CUR_PRICE X WHERE X.JONGMOG_CD = AJ.JONGMOG_CD AND X.REG_DT > DATE(NOW())),"
        " UPD_DT = NOW() WHERE 1=1 AND AJ.JONGMOG_CD = %s"
    )
    return sql, [current_price, jongmog_cd]


def query_get_order_exec_info(jongmog_cd, user_id):
    sql = (
        "SELECT CASE WHEN AJMY.MONITOR_YN IN ('AD_110_10') THEN 'Y' ELSE 'N' END MONITOR_YN"
        "     , CASE WHEN AJMY.ORDER_YN IN ('AD_110_10') THEN 'Y' ELSE 'N' END ORDER_YN"
        "     , AJS.BUY_01_CNT, AJS.BUY_POINT_AMT, AJS.SELL_POINT_AMT, AJS.SELL_PRICE_LIMIT"
        "     , (SELECT X.BUY_CNT FROM TB_API_SET X WHERE X.REG_ID = AJMY.REG_ID) AS BUY_TIME"
        "     , (SELECT X.BUY_PER FROM TB_API_USER_SET X WHERE X.REG_ID = AJMY.REG_ID) AS BUY_PERCENT"
        "     , (SELECT COUNT(1) FROM TB_API_ORDER_HIST X WHERE 1=1"
        "        AND X.JONGMOG_CD = AJMY.JONGMOG_CD AND X.REG_ID = AJMY.REG_ID"
        "        AND X.ORDER_STAT = 'AD_240_11' AND X.CLOSE_YN = 'N') AS ORDER_HIST_CNT"
        " FROM TB_API_JONGMOG_MONITOR_YN AJMY"
        " INNER JOIN TB_API_JONGMOG_SET AJS ON AJMY.JONGMOG_CD = AJS.JONGMOG_CD AND AJMY.REG_ID = AJS.REG_ID"
        " WHERE 1=1 AND AJMY.JONGMOG_CD = %s AND AJMY.REG_ID = %s"
    )
    return sql, [jongmog_cd, user_id]


def query_get_cur_price_up_down1(jongmog_cd):
    sql = (
        "SELECT AA.* FROM ("
        "  SELECT REG_DT, CUR_PRICE, PREV_PRICE, TREND"
        "       , @cnt := IF(@prev_trend = TREND, @cnt + 1, 1) AS TREND_COUNT"
        "       , @prev_trend := TREND AS PREV_TREND"
        "       , CASE WHEN REG_DT > DATE_SUB(NOW(), INTERVAL 30 MINUTE) THEN 'Y' ELSE 'N' END HOUR_YN"
        "       , DIFF_QTY, QTY_STATUS"
        "  FROM ("
        "      SELECT REG_DT, CUR_PRICE, @prev AS PREV_PRICE"
        "           , CASE WHEN @prev IS NULL THEN 'ZERO'"
        "                  WHEN CUR_PRICE > @prev THEN 'UP'"
        "                  WHEN CUR_PRICE < @prev THEN 'DOWN'"
        "                  ELSE 'ZERO' END AS TREND"
        "           , @prev := CUR_PRICE"
        "           , TRDE_QTY - @prev_qty AS DIFF_QTY"
        "           , CASE WHEN TRDE_QTY - @prev_qty - @prev_diff_qty > 0 THEN 'UP'"
        "                  WHEN TRDE_QTY - @prev_qty - @prev_diff_qty < 0 THEN 'DOWN'"
        "                  WHEN TRDE_QTY - @prev_qty < 0 THEN 'START' END AS QTY_STATUS"
        "           , @prev_diff_qty := TRDE_QTY - @prev_qty"
        "           , @prev_qty := TRDE_QTY"
        "      FROM TB_API_CUR_PRICE, (SELECT @prev := NULL, @prev_qty := 0, @prev_diff_qty := 0) p"
        "      WHERE JONGMOG_CD = %s"
        "      AND REG_DT >= DATE(DATE_ADD(NOW(), INTERVAL - 0 DAY))"
        "      AND MINUTE(REG_DT) %% 5 = 0 AND TRDE_QTY > 0"
        "      ORDER BY REG_DT"
        "  ) A"
        "  CROSS JOIN (SELECT @cnt := 0, @prev_trend := '') vars"
        ") AA WHERE 1=1 AND AA.HOUR_YN = 'Y' ORDER BY AA.REG_DT DESC"
    )
    return sql, [jongmog_cd]


def query_get_cur_price_per(jongmog_cd):
    sql = (
        "SELECT FLOOR(100 * ((A.MAX-A.MIN) - (A.MAX - A.CUR)) / (A.MAX-A.MIN)) AS CUR_PER, A.JONGMOG_CD"
        " FROM ("
        "   SELECT MIN(A.CUR_PRICE_MIN) AS MIN, MAX(A.CUR_PRICE_MIN) AS MAX"
        "        , B.CUR_PRICE AS CUR, B.JONGMOG_CD AS JONGMOG_CD"
        "   FROM TB_API_CUR_PRICE_DAY A INNER JOIN TB_API_JONGMOG B ON A.JONGMOG_CD = B.JONGMOG_CD"
        "   WHERE 1=1 AND A.JONGMOG_CD = %s AND A.REG_DT >= DATE_ADD(NOW(), INTERVAL-365 DAY)"
        "   GROUP BY B.CUR_PRICE_MIN, B.JONGMOG_CD"
        " ) A"
    )
    return sql, [jongmog_cd]


def query_get_api_not_che_list(jongmog_cd, user_id):
    sql = (
        "SELECT ORDER_NO, 'BUY_CANCEL' AS SELL_YN FROM TB_API_ORDER_HIST AOH WHERE 1=1"
        " AND AOH.ORDER_STAT = 'AD_240_10' AND AOH.ORDER_NO <> ''"
        " AND AOH.JONGMOG_CD = %s AND AOH.REG_ID = %s"
        " UNION ALL"
        " SELECT ORDER_STATUS, 'SELL_CANCEL' AS SELL_YN FROM TB_API_ORDER_HIST AOH WHERE 1=1"
        " AND AOH.ORDER_STAT = 'AD_240_11' AND AOH.CLOSE_YN = 'N' AND AOH.ORDER_STATUS <> ''"
        " AND AOH.JONGMOG_CD = %s AND AOH.REG_ID = %s"
    )
    return sql, [jongmog_cd, user_id, jongmog_cd, user_id]


def query_get_api_che_check(stock_code, stock_order_number, user_id):
    sql = (
        "SELECT COUNT(1) AS CNT FROM TB_API_CHE AC WHERE 1=1"
        " AND AC.STOCK_CODE = %s AND AC.STOCK_ORDER_NUMBER = %s AND AC.REG_ID = %s"
    )
    return sql, [stock_code, stock_order_number, user_id]


def query_insert_api_che(stock_code, stock_order_number, stock_order_price, stock_order_quantity,
                          stock_not_signed_quantity, stock_signed_quantity, stock_order_status,
                          stock_order_type, user_id):
    sql = (
        "INSERT INTO TB_API_CHE(STOCK_CODE, STOCK_ORDER_NUMBER, STOCK_ORDER_PRICE,"
        " STOCK_ORDER_QUANTITY, STOCK_NOT_SIGNED_QUANTITY, STOCK_SIGNED_QUANTITY,"
        " STOCK_ORDER_STATUS, STOCK_ORDER_TYPE, DEL_YN, REG_DT, REG_ID, UPD_DT, UPD_ID)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'N', NOW(), %s, NOW(), %s)"
    )
    return sql, [stock_code, stock_order_number, stock_order_price, stock_order_quantity,
                 stock_not_signed_quantity, stock_signed_quantity, stock_order_status,
                 stock_order_type, user_id, user_id]


def query_update_api_che(stock_code, stock_order_number, stock_not_signed_quantity,
                          stock_signed_quantity, stock_order_status, user_id):
    sql = (
        "UPDATE TB_API_CHE SET STOCK_NOT_SIGNED_QUANTITY=%s, STOCK_SIGNED_QUANTITY=%s,"
        " STOCK_ORDER_STATUS=%s, UPD_DT=NOW(), UPD_ID=%s"
        " WHERE STOCK_CODE=%s AND STOCK_ORDER_NUMBER=%s AND REG_ID=%s"
    )
    return sql, [stock_not_signed_quantity, stock_signed_quantity, stock_order_status, user_id,
                 stock_code, stock_order_number, user_id]


def query_del_cur_price():
    return "DELETE FROM TB_API_CUR_PRICE WHERE REG_DT < DATE_ADD(NOW(), INTERVAL-14 DAY)", []


def query_ins_cur_price_day():
    sql = (
        "INSERT INTO TB_API_CUR_PRICE_DAY (JONGMOG_CD, REG_DATE, CUR_PRICE_AVG, CUR_PRICE_MIN,"
        " CUR_PRICE_MAX, TRDE_QTY_AVG, TRDE_QTY_MIN, TRDE_QTY_MAX, CNT, REG_DT)"
        " SELECT A.JONGMOG_CD, A.REG_DATE, A.CUR_PRICE_AVG, A.CUR_PRICE_MIN, A.CUR_PRICE_MAX,"
        " A.TRDE_QTY_AVG, A.TRDE_QTY_MIN, A.TRDE_QTY_MAX, A.CNT, NOW()"
        " FROM ("
        "   SELECT JONGMOG_CD, DATE_FORMAT(REG_DT, '%%Y-%%m-%%d') AS REG_DATE"
        "        , ROUND(AVG(ABS(CUR_PRICE))) AS CUR_PRICE_AVG"
        "        , ROUND(MIN(ABS(CUR_PRICE))) AS CUR_PRICE_MIN"
        "        , ROUND(MAX(ABS(CUR_PRICE))) AS CUR_PRICE_MAX"
        "        , ROUND(AVG(ABS(TRDE_QTY))) AS TRDE_QTY_AVG"
        "        , ROUND(MIN(ABS(TRDE_QTY))) AS TRDE_QTY_MIN"
        "        , ROUND(MAX(ABS(TRDE_QTY))) AS TRDE_QTY_MAX"
        "        , COUNT(1) AS CNT"
        "   FROM TB_API_CUR_PRICE WHERE 1=1"
        "   AND REG_DT >= DATE_ADD(NOW(), INTERVAL-1 DAY) AND REG_DT < CURDATE()"
        "   GROUP BY JONGMOG_CD, DATE_FORMAT(REG_DT, '%%Y-%%m-%%d')"
        "   ORDER BY JONGMOG_CD, REG_DT"
        " ) A WHERE 1=1 AND A.TRDE_QTY_AVG > 0 AND A.CNT > 50"
        " AND NOT EXISTS ("
        "     SELECT 1 FROM TB_API_CUR_PRICE_DAY X WHERE X.JONGMOG_CD = A.JONGMOG_CD"
        "     AND DATE_FORMAT(X.REG_DATE, '%%Y-%%m-%%d') = A.REG_DATE"
        " )"
    )
    return sql, []


def query_ins_stock_daily_summary():
    sql = (
        "INSERT INTO TB_STOCK_DAILY_SUMMARY (SUMMARY_DATE, REG_ID, KOSPI_PRICE, KOSPI_CHANGE_VAL,"
        " KOSPI_CHANGE_RATE, KOSDAQ_PRICE, KOSDAQ_CHANGE_VAL, KOSDAQ_CHANGE_RATE, TOT_INS_AMT,"
        " PRSM_DPST_ASET_AMT, PROFIT_AMT, PROFIT_RATE)"
        " SELECT DATE(K.REG_DT), U.REG_ID, K.CURRENT_PRICE, K.CHANGE_VAL, K.CHANGE_RATE,"
        " D.CURRENT_PRICE, D.CHANGE_VAL, D.CHANGE_RATE, U.TOT_INS_AMT, U.PRSM_DPST_ASET_AMT,"
        " (U.PRSM_DPST_ASET_AMT - U.TOT_INS_AMT),"
        " CASE WHEN U.TOT_INS_AMT > 0 THEN ROUND((U.PRSM_DPST_ASET_AMT - U.TOT_INS_AMT) / U.TOT_INS_AMT * 100, 4) ELSE NULL END"
        " FROM TB_API_USER_SET U"
        " JOIN (SELECT REG_DT, CURRENT_PRICE, CHANGE_VAL, CHANGE_RATE FROM TB_MARKET_INDEX WHERE MARKET_TYPE = 'KOSPI') K ON 1=1"
        " JOIN (SELECT CURRENT_PRICE, CHANGE_VAL, CHANGE_RATE FROM TB_MARKET_INDEX WHERE MARKET_TYPE = 'KOSDAQ') D ON 1=1"
        " WHERE U.DEL_YN = 'N'"
        " AND NOT EXISTS ("
        "     SELECT 1 FROM TB_STOCK_DAILY_SUMMARY X WHERE X.SUMMARY_DATE = DATE(K.REG_DT) AND X.REG_ID = U.REG_ID"
        " )"
    )
    return sql, []


def query_upd_order_hist_cancel():
    sql = (
        "UPDATE TB_API_ORDER_HIST SET ORDER_STAT = 'AD_240_12', UPD_DT = NOW() WHERE 1=1"
        " AND DEL_YN = 'N' AND ORDER_STAT = 'AD_240_10' AND REG_DT < CURDATE()"
    )
    return sql, []


def query_upd_order_hist_del_yn():
    sql = (
        "UPDATE TB_API_ORDER_HIST SET DEL_YN = 'Y', UPD_DT = NOW() WHERE 1=1"
        " AND DEL_YN = 'N' AND ORDER_STAT = 'AD_240_12' AND REG_DT < CURDATE()"
    )
    return sql, []


def query_upd_monitor_order_yn(jongmog_cd, order_yn, reg_id):
    sql = (
        "UPDATE TB_API_JONGMOG_MONITOR_YN SET ORDER_YN = %s, UPD_DT = NOW() WHERE 1=1"
        " AND JONGMOG_CD = %s AND REG_ID = %s"
    )
    return sql, [order_yn, jongmog_cd, reg_id]


# ------------------------------------------------------------------
# 메일 발송 (config/mail.js + config/sendmail.py 포팅)
# ------------------------------------------------------------------
def send_mail(subject, text):
    msg = MIMEText(text, "plain", "utf-8")
    msg["From"] = MAIL_SENDER
    msg["To"] = MAIL_RECEIVER
    msg["Subject"] = Header(subject, "utf-8")

    ctx = ssl.create_default_context()

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as smtp:
        smtp.login(MAIL_SENDER, MAIL_PASS)
        smtp.sendmail(MAIL_SENDER, MAIL_RECEIVER, msg.as_string())


def send_order_mail(subject, decision):
    order_price = decision.get("orderPrice") or 0
    text = (
        f"종목명: {decision.get('stockName') or ''}\n"
        f"종목코드: {decision.get('stockCode')}\n"
        f"주문가격: {int(round(to_number(order_price))):,}\n"
        f"주문수량: {decision.get('orderQty')}\n"
        f"주문사유: {decision.get('reason')}" + (" (손절)" if decision.get("stopLossYn") == "Y" else "") + "\n"
    )
    prefix = "" if LIVE else "[TEST] "
    try:
        send_mail(prefix + subject, text)
    except Exception as e:
        log(f"메일 발송 실패: {e}")


# ------------------------------------------------------------------
# 키움 API 호출
# ------------------------------------------------------------------
def kiwoom_post(url, body, headers, timeout=30):
    resp = requests.post(url, json=body, headers=headers, timeout=timeout)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.headers, (data or {})


def call_kiwoom_api_with_user(user_info, endpoint, api_id, body, cont_yn="N", next_key=""):
    token = get_row_value(user_info, "TOKEN")
    token_type = get_row_value(user_info, "TOKEN_TYPE") or "Bearer"
    require_value(token, "토큰")

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{token_type} {token}",
        "cont-yn": cont_yn or "N",
        "next-key": next_key or "",
        "api-id": api_id,
    }
    resp_headers, data = kiwoom_post(KIWOOM_HOST + endpoint, body, headers)
    return {
        "headers": {
            "cont-yn": resp_headers.get("cont-yn"),
            "next-key": resp_headers.get("next-key"),
            "api-id": resp_headers.get("api-id"),
        },
        "data": data,
    }


def get_accno_info(conn, accno, user_id):
    sql, params = query_set_accno_info(accno, user_id)
    rows = fetch_all(conn, sql, params)
    if not rows:
        raise RuntimeError("키움 계좌 기본정보를 찾을 수 없습니다.")
    return rows[0]


def refresh_token(conn, user_id, accno):
    accno_info = get_accno_info(conn, accno, user_id)
    appkey = get_row_value(accno_info, "APPKEY")
    secretkey = get_row_value(accno_info, "SECRETKEY")
    require_value(appkey, "appkey")
    require_value(secretkey, "secretkey")

    resp = requests.post(
        KIWOOM_HOST + TOKEN_ENDPOINT,
        json={"grant_type": "client_credentials", "appkey": appkey, "secretkey": secretkey},
        headers={"Content-Type": "application/json;charset=UTF-8"},
        timeout=30,
    )
    data = resp.json() if resp.content else {}
    token_type = get_response_value(data, ["token_type", "TOKEN_TYPE"])
    token = get_response_value(data, ["token", "TOKEN"])
    expires_dt = get_response_value(data, ["expires_dt", "EXPIRES_DT"])
    require_value(token_type, "토큰타입")
    require_value(token, "토큰")
    require_value(expires_dt, "토큰만료일")

    sql, params = query_set_token_refresh(token_type, token, format_kiwoom_date_time(expires_dt), user_id)
    db_write(conn, sql, params, "토큰 갱신")
    return {"token_type": token_type, "token": token, "expires_dt": expires_dt}


def refresh_user_token_if_needed(conn, user_info):
    reg_id = get_row_value(user_info, "REG_ID")
    token_expire_date = get_token_expire_date(get_row_value(user_info, "TOKEN_ED_DT"))
    refresh_yn = "Y" if (not token_expire_date or datetime.now() >= token_expire_date) else "N"

    if refresh_yn != "Y":
        return user_info

    log(f"토큰 갱신 시작 regId={reg_id}")
    result = refresh_token(conn, reg_id, get_row_value(user_info, "ACCNO"))
    user_info = dict(user_info)
    user_info["TOKEN_TYPE"] = result["token_type"]
    user_info["TOKEN"] = result["token"]
    user_info["TOKEN_ED_DT"] = format_kiwoom_date_time(result["expires_dt"])
    log(f"토큰 갱신 완료 regId={reg_id}")
    return user_info


# ------------------------------------------------------------------
# 계좌평가현황 / 종목별 수익률
# ------------------------------------------------------------------
def update_account_evaluation(conn, user_info):
    reg_id = get_row_value(user_info, "REG_ID")
    api_result = call_kiwoom_api_with_user(user_info, ACCOUNT_ENDPOINT, "kt00004",
                                            {"qry_tp": "0", "dmst_stex_tp": "KRX"})
    data = api_result["data"] or {}
    d2_entra = get_response_value(data, ["d2_entra", "D2_ENTRA"])
    tot_pur_amt = get_response_value(data, ["tot_pur_amt", "TOT_PUR_AMT"])
    prsm_dpst_aset_amt = get_response_value(data, ["prsm_dpst_aset_amt", "PRSM_DPST_ASET_AMT"])
    sql, params = query_set_accno_amt(d2_entra, tot_pur_amt, prsm_dpst_aset_amt, reg_id)
    db_write(conn, sql, params, "계좌평가현황 갱신")
    log(f"계좌평가현황 갱신 완료 regId={reg_id}")
    return data


def get_account_stock_rows(data):
    return get_first_list(data, [
        "stk_acnt_evlt_prst", "stk_acnt_evlt_prst_array",
        "acnt_evlt_remn_indv_tot", "acnt_evlt_remn_indv_tot_array",
        "output", "output1", "list", "items", "data",
    ])


def get_stock_row_code(row):
    return get_response_value(row, ["stk_cd", "stock_code", "JONGMOG_CD", "jongmog_cd", "code", "pdno", "PDNO"])


def map_order_average_price_info(row):
    if not row:
        return None

    buy_amt = to_number(get_response_value(row, ["buy_amt", "pchs_amt", "tot_pur_amt", "purchase_amt", "pur_amt", "매입금액"]))
    eval_profit = to_signed_number(get_response_value(row, ["evltv_prft", "evlt_prft", "eval_profit", "pl_amt", "평가손익"]))
    profit_rate = to_signed_number(get_response_value(row, ["prft_rt", "profit_rate", "evltv_prft_rt", "pl_rt", "수익률"]))

    if profit_rate == 0 and buy_amt > 0:
        profit_rate = round((eval_profit / buy_amt) * 10000) / 100

    return {
        "jongmog_cd": normalize_stock_code(get_stock_row_code(row)),
        "jongmog_nm": get_response_value(row, ["stk_nm", "stock_name", "JONGMOG_NM", "jongmog_nm", "name", "prdt_name", "PRDT_NAME"]),
        "avg_price": normalize_number(get_response_value(row, ["avg_prc", "avg_price", "pchs_avg_pric", "pchs_avg_prc", "pur_pric", "buy_price", "매입평균가", "평균단가"])),
        "buy_amt": buy_amt,
        "hold_qty": normalize_number(get_response_value(row, ["rmnd_qty", "poss_qty", "hldg_qty", "hold_qty", "qty", "보유수량"])),
        "eval_amt": normalize_number(get_response_value(row, ["evlt_amt", "eval_amt", "평가금액"])),
        "eval_profit": eval_profit,
        "profit_rate": profit_rate,
    }


def get_account_stock_profit_map(user_info):
    profit_map = {}
    cont_yn, next_key = "N", ""

    for _ in range(10):
        api_result = call_kiwoom_api_with_user(user_info, ACCOUNT_ENDPOINT, "kt00004",
                                                {"qry_tp": "0", "dmst_stex_tp": "KRX"}, cont_yn, next_key)
        data = api_result["data"] or {}
        for row in get_account_stock_rows(data):
            info = map_order_average_price_info(row)
            if info and info.get("jongmog_cd"):
                profit_map[info["jongmog_cd"]] = info

        cont_yn = api_result["headers"].get("cont-yn") or "N"
        next_key = api_result["headers"].get("next-key") or ""
        if cont_yn != "Y" or not next_key:
            break

    return profit_map


# ------------------------------------------------------------------
# 현재가 멀티조회 / 저장
# ------------------------------------------------------------------
def request_current_price_list(user_info, monitor_rows):
    stock_codes = []
    for row in monitor_rows:
        code = get_row_value(row, "JONGMOG_CD")
        if has_value(code) and code not in stock_codes:
            stock_codes.append(code)

    results = []
    for chunk in split_array(stock_codes, CURRENT_PRICE_CHUNK_SIZE):
        body = {"stk_cd": "|".join(str(c) for c in chunk)}
        api_result = call_kiwoom_api_with_user(user_info, STOCK_INFO_ENDPOINT, "ka10095", body)
        results.append({"request": {"apiId": "ka10095", "body": body}, "response": api_result["data"] or {}})

    log(f"현재가 멀티조회 완료 stockCount={len(stock_codes)} chunkCount={len(results)}")
    return results


def get_current_price_rows_from_response(data):
    rows = get_first_list(data, ["atn_stk_infr", "stk_infr", "output", "list", "items"])
    if rows:
        return rows
    if data and (has_value(data.get("stk_cd")) or has_value(data.get("jongmog_cd"))):
        return [data]
    return []


def get_stock_code_from_price_row(row):
    return get_response_value(row, ["stk_cd", "JONGMOG_CD", "jongmog_cd", "stock_code"])


def get_current_price_from_row(row):
    return normalize_number(get_response_value(row, ["cur_prc", "stk_prpr", "CUR_PRICE", "current_price", "now_prc"]))


def get_trade_qty_from_row(row):
    return normalize_number(get_response_value(row, ["trde_qty", "acc_trde_qty", "TRDE_QTY", "volume", "vol"]))


def get_current_price_by_stock_code(current_price_results):
    price_map = {}
    for item in current_price_results:
        for row in get_current_price_rows_from_response(item["response"]):
            stock_code = get_stock_code_from_price_row(row)
            current_price = get_current_price_from_row(row)
            if has_value(stock_code) and has_value(current_price):
                price_map[stock_code] = current_price
    return price_map


def save_current_price_rows(conn, current_price_results):
    """감시목록 현재가를 TB_API_CUR_PRICE/TB_API_JONGMOG에 반영 - Node의 실거래 추세
    판단과 같은 테이블을 쓰므로 DRY-RUN에서는 건너뛴다."""
    insert_rows, update_rows = 0, 0
    for item in current_price_results:
        for row in get_current_price_rows_from_response(item["response"]):
            stock_code = get_stock_code_from_price_row(row)
            current_price = get_current_price_from_row(row)
            trade_qty = get_trade_qty_from_row(row)

            if not has_value(stock_code) or not has_value(current_price):
                continue

            insert_sql, insert_params = query_reg_cur_price(stock_code, current_price, trade_qty)
            update_sql, update_params = query_reg_cur_price_update(stock_code, current_price)
            insert_result = guarded_db_write(conn, insert_sql, insert_params, f"현재가 저장 {stock_code}")
            update_result = guarded_db_write(conn, update_sql, update_params, f"현재가 반영 {stock_code}")
            insert_rows += insert_result["affectedRows"]
            update_rows += update_result["affectedRows"]

    log(f"현재가 저장 완료 insertRows={insert_rows} updateRows={update_rows}")
    return {"insertRows": insert_rows, "updateRows": update_rows}


# ------------------------------------------------------------------
# 주문 상태 동기화 (체결/미체결 확인 후 취소)
# ------------------------------------------------------------------
def call_che_info(user_info, stock_code):
    return call_kiwoom_api_with_user(user_info, ACCOUNT_ENDPOINT, "ka10076", {
        "stk_cd": str(stock_code), "qry_tp": "1", "sell_tp": "0", "ord_no": "", "stex_tp": "0",
    })


def call_not_che_info(user_info, stock_code):
    return call_kiwoom_api_with_user(user_info, ACCOUNT_ENDPOINT, "ka10075", {
        "all_stk_tp": "1", "trde_tp": "0", "stk_cd": str(stock_code), "stex_tp": "0",
    })


def get_order_rows(data):
    return get_first_list(data, ["cntr", "oso", "output", "list", "items"])


def get_order_no_from_api_row(row):
    return get_response_value(row, ["ord_no", "order_no", "STOCK_ORDER_NUMBER", "stock_order_number"])


def find_api_order_row(rows, order_no):
    for row in rows:
        if is_same_order_no(get_order_no_from_api_row(row), order_no):
            return row
    return None


def get_signed_qty(row):
    return to_number(get_response_value(row, ["cntr_qty", "stk_signed_quantity", "signed_qty", "STOCK_SIGNED_QUANTITY"]))


def get_not_signed_qty(row):
    return to_number(get_response_value(row, ["oso_qty", "stk_not_signed_quantity", "not_signed_qty", "STOCK_NOT_SIGNED_QUANTITY"]))


def get_che_info(row):
    return {
        "stockOrderPrice": normalize_number(get_response_value(row, ["ord_pric", "ord_uv", "stock_order_price", "STOCK_ORDER_PRICE"])),
        "stockOrderQuantity": normalize_number(get_response_value(row, ["ord_qty", "stock_order_quantity", "STOCK_ORDER_QUANTITY"])),
        "stockNotSignedQuantity": str(int(get_not_signed_qty(row))),
        "stockSignedQuantity": str(int(get_signed_qty(row))),
        "stockOrderStatus": get_response_value(row, ["ord_stt", "stock_order_status", "STOCK_ORDER_STATUS"]) or "",
        "stockOrderType": get_response_value(row, ["io_tp_nm", "stock_order_type", "STOCK_ORDER_TYPE"]) or "",
    }


def upsert_che_info(conn, stock_code, order_no, reg_id, api_row):
    che_info = get_che_info(api_row)
    check_sql, check_params = query_get_api_che_check(stock_code, order_no, reg_id)
    check_row = fetch_one(conn, check_sql, check_params)
    exists_count = int(check_row.get("CNT") or 0) if check_row else 0

    if exists_count > 0:
        sql, params = query_update_api_che(stock_code, order_no, che_info["stockNotSignedQuantity"],
                                            che_info["stockSignedQuantity"], che_info["stockOrderStatus"], reg_id)
        db_write(conn, sql, params, "체결정보 수정")
        log(f"체결정보 수정 완료 stockCode={stock_code} orderNo={order_no}")
        return

    sql, params = query_insert_api_che(stock_code, order_no, che_info["stockOrderPrice"],
                                        che_info["stockOrderQuantity"], che_info["stockNotSignedQuantity"],
                                        che_info["stockSignedQuantity"], che_info["stockOrderStatus"],
                                        che_info["stockOrderType"], reg_id)
    db_write(conn, sql, params, "체결정보 등록")
    log(f"체결정보 등록 완료 stockCode={stock_code} orderNo={order_no}")


def update_order_hist_by_che(conn, stock_code, order_no, reg_id, sell_yn):
    if sell_yn == "BUY_CANCEL":
        sql, params = query_update_api_order_hist_stat(stock_code, reg_id, order_no, "AD_240_11")
    else:
        sql, params = query_update_api_order_hist_close(stock_code, reg_id, order_no, order_no, "Y")
    db_write(conn, sql, params, "주문내역 체결 상태 반영")
    log(f"주문내역 체결 상태 반영 완료 stockCode={stock_code} orderNo={order_no} sellYn={sell_yn}")


def guarded_cancel_buy_stock(conn, input_dict):
    if not LIVE:
        log(f"[DRY-RUN] 매수취소 스킵 - {input_dict}")
        return {"response": {"dry_run": True}}
    return cancel_buy_stock(conn, input_dict)


def guarded_cancel_sell_stock(conn, input_dict):
    if not LIVE:
        log(f"[DRY-RUN] 매도취소 스킵 - {input_dict}")
        return {"response": {"dry_run": True}}
    return cancel_sell_stock(conn, input_dict)


def cancel_order_and_update(conn, user_info, stock_code, order_no, sell_yn):
    reg_id = get_row_value(user_info, "REG_ID")
    input_dict = {
        "user_id": reg_id, "accno": get_row_value(user_info, "ACCNO"),
        "jongmogCd": stock_code, "orderNo": order_no, "cancelQty": "0",
    }
    if sell_yn == "BUY_CANCEL":
        result = guarded_cancel_buy_stock(conn, input_dict)
        log(f"매수취소 실행 완료 stockCode={stock_code} orderNo={order_no}")
        return result
    result = guarded_cancel_sell_stock(conn, input_dict)
    log(f"매도취소 실행 완료 stockCode={stock_code} orderNo={order_no}")
    return result


def sync_order_status_for_user_and_stock(conn, user_info, stock_code):
    reg_id = get_row_value(user_info, "REG_ID")
    sql, params = query_get_api_not_che_list(stock_code, reg_id)
    db_not_che_rows = fetch_all(conn, sql, params)
    result = {"stockCode": stock_code, "regId": reg_id, "targetCount": len(db_not_che_rows),
              "cheCount": 0, "skipCount": 0, "cancelCount": 0, "errorCount": 0}

    if not db_not_che_rows:
        return result

    che_rows = get_order_rows(call_che_info(user_info, stock_code)["data"])
    api_not_che_rows = get_order_rows(call_not_che_info(user_info, stock_code)["data"])

    for db_row in db_not_che_rows:
        order_no = get_row_value(db_row, "ORDER_NO")
        sell_yn = get_row_value(db_row, "SELL_YN")
        che_row = find_api_order_row(che_rows, order_no)
        not_che_row = find_api_order_row(api_not_che_rows, order_no)

        try:
            if che_row and get_signed_qty(che_row) > 0:
                upsert_che_info(conn, stock_code, order_no, reg_id, che_row)
                update_order_hist_by_che(conn, stock_code, order_no, reg_id, sell_yn)
                result["cheCount"] += 1
                continue

            if not_che_row and get_not_signed_qty(not_che_row) > 0:
                result["skipCount"] += 1
                continue

            cancel_order_and_update(conn, user_info, stock_code, order_no, sell_yn)
            result["cancelCount"] += 1
        except Exception as e:
            result["errorCount"] += 1
            log(f"주문 상태 동기화 실패 stockCode={stock_code} orderNo={order_no}: {e}")

    return result


def sync_order_status(conn, users, monitor_rows):
    stock_codes = []
    for row in monitor_rows:
        code = get_row_value(row, "JONGMOG_CD")
        if has_value(code) and code not in stock_codes:
            stock_codes.append(code)

    results = []
    for stock_code in stock_codes:
        for user_info in users:
            try:
                results.append(sync_order_status_for_user_and_stock(conn, user_info, stock_code))
            except Exception as e:
                log(f"종목/사용자 주문 상태 동기화 실패 stockCode={stock_code}: {e}")
                results.append({"stockCode": stock_code, "regId": get_row_value(user_info, "REG_ID"),
                                 "targetCount": 0, "cheCount": 0, "skipCount": 0, "cancelCount": 0, "errorCount": 1})
    return results


# ------------------------------------------------------------------
# 일봉 연속등락 맵 (stockAnalysis/trend와 동일 로직)
# ------------------------------------------------------------------
def get_daily_streak_map(conn):
    try:
        rows = fetch_all(conn, (
            "SELECT d.JONGMOG_CD, d.REG_DATE, d.CUR_PRICE_AVG FROM TB_API_CUR_PRICE_DAY d"
            " JOIN TB_API_JONGMOG_MONITOR_YN m ON d.JONGMOG_CD = m.JONGMOG_CD"
            " WHERE m.DEL_YN = 'N' ORDER BY d.JONGMOG_CD, d.REG_DATE ASC"
        ))
    except Exception:
        return {}

    stocks = {}
    for r in rows:
        stocks.setdefault(r["JONGMOG_CD"], []).append(to_number(r["CUR_PRICE_AVG"]))

    streak_map = {}
    for cd, days in stocks.items():
        if len(days) < 2:
            continue

        down_cnt, up_cnt, prev_streak_idx = 0, 0, -1
        for i in range(len(days) - 1, 0, -1):
            diff = days[i] - days[i - 1]
            if down_cnt == 0 and up_cnt == 0:
                if diff < 0:
                    down_cnt += 1
                elif diff > 0:
                    up_cnt += 1
                else:
                    break
            elif down_cnt > 0:
                if diff < 0:
                    down_cnt += 1
                else:
                    prev_streak_idx = i
                    break
            elif up_cnt > 0:
                if diff > 0:
                    up_cnt += 1
                else:
                    prev_streak_idx = i
                    break

        prev_down_cnt, prev_up_cnt = 0, 0
        if prev_streak_idx >= 1:
            for k in range(prev_streak_idx, 0, -1):
                diff2 = days[k] - days[k - 1]
                if prev_down_cnt == 0 and prev_up_cnt == 0:
                    if diff2 < 0:
                        prev_down_cnt += 1
                    elif diff2 > 0:
                        prev_up_cnt += 1
                    else:
                        break
                elif prev_down_cnt > 0:
                    if diff2 < 0:
                        prev_down_cnt += 1
                    else:
                        break
                elif prev_up_cnt > 0:
                    if diff2 > 0:
                        prev_up_cnt += 1
                    else:
                        break

        decline_rate_10d = None
        if len(days) > DOWNTREND_LOOKBACK_DAYS:
            latest_price = days[-1]
            base_price = days[len(days) - 1 - DOWNTREND_LOOKBACK_DAYS]
            if base_price > 0:
                decline_rate_10d = ((latest_price - base_price) / base_price) * 100

        last_price = days[-1]
        ref3 = days[len(days) - 4] if len(days) >= 4 else days[0]
        ref5 = days[len(days) - 6] if len(days) >= 6 else days[0]
        ref10 = days[len(days) - 11] if len(days) >= 11 else days[0]
        window_size = min(30, len(days) - 1)
        ref30 = days[len(days) - 1 - window_size]
        rate3 = (last_price - ref3) / ref3 * 100 if ref3 > 0 else 0
        rate5 = (last_price - ref5) / ref5 * 100 if ref5 > 0 else 0
        rate10 = (last_price - ref10) / ref10 * 100 if ref10 > 0 else 0
        rate30 = (last_price - ref30) / ref30 * 100 if ref30 > 0 else 0
        reversal_signal = rate30 < rate10 < rate5 < rate3 and rate3 > 0

        streak_map[cd] = {
            "downCnt": down_cnt, "upCnt": up_cnt, "prevDownCnt": prev_down_cnt, "prevUpCnt": prev_up_cnt,
            "declineRate10d": decline_rate_10d, "reversalSignal": reversal_signal,
        }

    return streak_map


# ------------------------------------------------------------------
# 매수/매도 판단 컨텍스트 조회 + 판단 (핵심 로직 - Node buildOrderDecision과 동일)
# ------------------------------------------------------------------
def get_single_row(conn, sql, params):
    rows = fetch_all(conn, sql, params)
    return rows[0] if rows else None


def get_order_decision_context(conn, stock_code, reg_id, current_price):
    order_exec_info = get_single_row(conn, *query_get_order_exec_info(stock_code, reg_id))
    cur_price_per_info = get_single_row(conn, *query_get_cur_price_per(stock_code))
    trend_rows = fetch_all(conn, *query_get_cur_price_up_down1(stock_code))
    trend_info = trend_rows[0] if trend_rows else None
    previous_trend_info = trend_rows[1] if len(trend_rows) > 1 else None
    order_rows = fetch_all(conn, *query_set_order_list(stock_code, reg_id))
    recent_buy_order_info = get_single_row(conn, *query_get_recent_buy_order_info(stock_code, reg_id, ADDITIONAL_BUY_COOLDOWN_MINUTES))
    today_buy_order_count_info = get_single_row(conn, *query_get_today_buy_order_count(stock_code, reg_id))
    recent_sell_info = get_single_row(conn, *query_get_recent_sell_info(stock_code, reg_id, RECENT_SELL_BLOCK_DAYS))

    return {
        "stockCode": stock_code, "regId": reg_id, "currentPrice": to_number(current_price),
        "orderExecInfo": order_exec_info, "curPricePerInfo": cur_price_per_info,
        "trendInfo": trend_info, "previousTrendInfo": previous_trend_info,
        "orderRows": order_rows, "recentBuyOrderInfo": recent_buy_order_info,
        "todayBuyOrderCountInfo": today_buy_order_count_info, "recentSellInfo": recent_sell_info,
        "dailyStreakInfo": None, "stockProfitInfo": None,
    }


def build_order_decision(context, test_mode):
    order_info = context.get("orderExecInfo") or {}
    price_per_info = context.get("curPricePerInfo") or {}
    trend_info = context.get("trendInfo") or {}
    order_rows = context.get("orderRows") or []
    order_row = order_rows[0] if order_rows else None
    recent_buy_order_info = context.get("recentBuyOrderInfo") or {}
    previous_trend_info = context.get("previousTrendInfo") or {}
    today_buy_order_count_info = context.get("todayBuyOrderCountInfo") or {}
    recent_sell_info = context.get("recentSellInfo") or {}

    order_yn = get_row_value(order_info, "ORDER_YN")
    buy_point_amt = to_number(get_row_value(order_info, "BUY_POINT_AMT"))
    sell_point_amt = to_number(get_row_value(order_info, "SELL_POINT_AMT"))
    sell_price_limit = to_signed_number(get_row_value(order_info, "SELL_PRICE_LIMIT"))
    buy_percent = to_number(get_row_value(order_info, "BUY_PERCENT"))
    buy_01_cnt = to_number(get_row_value(order_info, "BUY_01_CNT"))

    cur_price_per = to_number(get_row_value(price_per_info, "CUR_PER"))
    trend_cnt = to_number(get_row_value(trend_info, "TREND_COUNT"))
    diff_qty = to_number(get_row_value(trend_info, "DIFF_QTY"))
    trend = get_row_value(trend_info, "TREND")
    hour_yn = get_row_value(trend_info, "HOUR_YN")

    previous_trend = get_row_value(previous_trend_info, "TREND")
    previous_trend_cnt = to_number(get_row_value(previous_trend_info, "TREND_COUNT"))

    recent_buy_count = to_number(get_row_value(recent_buy_order_info, "CNT"))
    recent_buy_last_reg_dt = get_row_value(recent_buy_order_info, "LAST_REG_DT")

    today_buy_order_count = to_number(get_row_value(today_buy_order_count_info, "CNT"))
    today_additional_buy_count = max(today_buy_order_count - 1, 0) if today_buy_order_count > 0 else 0
    recent_sell_count = to_number(get_row_value(recent_sell_info, "CNT"))

    decision = {
        "stockCode": context["stockCode"], "regId": context["regId"], "action": "NONE",
        "stat": "", "reason": "", "orderPrice": context["currentPrice"],
        "orderQty": buy_01_cnt or 1, "orderIdx": None, "orderNo": "", "orderStatus": "",
        "currentPrice": context["currentPrice"], "curPricePer": cur_price_per, "trend": trend,
        "trendCnt": trend_cnt, "previousTrend": previous_trend, "previousTrendCnt": previous_trend_cnt,
        "hourYn": hour_yn, "diffQty": diff_qty, "recentBuyCount": recent_buy_count,
        "recentBuyLastRegDt": recent_buy_last_reg_dt, "todayBuyOrderCount": today_buy_order_count,
        "todayAdditionalBuyCount": today_additional_buy_count,
    }

    # 주문 대상 여부 판단 - 보유중인 주문이력(order_row)이 있으면 ORDER_YN이 미사용이어도 매도/손절 판단은 계속 진행
    if not is_yes(order_yn) and not order_row:
        decision["reason"] = "주문대상 아님"
        return decision

    if test_mode is False:
        if not trend_info or not is_yes(hour_yn):
            decision["reason"] = "최근 추세 데이터 없음"
            return decision

    if order_row:
        sell_cnt = to_number(get_row_value(order_row, "SELL_CNT"))
        buy_cnt = to_number(get_row_value(order_row, "BUY_CNT"))
        order_idx = get_row_value(order_row, "IDX")
        order_no = get_row_value(order_row, "ORDER_NO")
        order_stat = get_row_value(order_row, "ORDER_STAT")
        order_status = get_row_value(order_row, "ORDER_STATUS")
        basic_price_min = to_number(get_row_value(order_row, "BUY_PRICE_MIN"))
        basic_price_plus = basic_price_min + sell_point_amt
        basic_price_minus = basic_price_min + sell_price_limit

        decision["stat"] = "SELL"
        decision["orderIdx"] = order_idx
        decision["orderNo"] = order_no
        decision["orderStatus"] = order_status
        decision["basicPriceMin"] = basic_price_min
        decision["basicPricePlus"] = basic_price_plus
        decision["basicPriceMinus"] = basic_price_minus

        if order_stat == "AD_240_10" or has_value(order_status):
            decision["action"] = "WAIT"
            decision["reason"] = "주문접수 또는 매도주문번호 존재"
            return decision

        if context["currentPrice"] >= basic_price_plus:
            decision["orderQty"] = buy_cnt

            if trend == "DOWN" and trend_cnt >= 4:
                decision["action"] = "SELL_EXEC"
                decision["reason"] = "수익권 4연속 하락 매도"
                return decision

            if context.get("dailyStreakInfo") and context["dailyStreakInfo"]["prevUpCnt"] >= 5 and context["dailyStreakInfo"]["downCnt"] >= 1:
                decision["action"] = "SELL_EXEC"
                decision["reason"] = f"수익권 일봉 상승 {context['dailyStreakInfo']['prevUpCnt']}일 후 하락 전환 매도"
                return decision

            decision["reason"] = "수익권이나 매도 추세 조건 미충족"
            return decision

        if context["currentPrice"] <= basic_price_minus:
            # 2026.07.30 손절매도 로직 정지 (Node와 동일하게 비활성 상태 유지)
            # 2026.07.03 - 주문사용여부 미사용(손절 등 재매수 차단 상태)이면 추가매수 판단은 전부 스킵
            if is_yes(order_yn):
                if recent_buy_count > 0:
                    decision["action"] = "WAIT"
                    decision["reason"] = f"최근 {ADDITIONAL_BUY_COOLDOWN_MINUTES}분 내 매수이력 존재"
                    return decision

                if today_additional_buy_count >= DAILY_MAX_ADDITIONAL_BUY_COUNT:
                    decision["action"] = "WAIT"
                    decision["reason"] = f"당일 추가매수 최대 {DAILY_MAX_ADDITIONAL_BUY_COUNT}회 도달"
                    return decision

                streak = context.get("dailyStreakInfo")
                if streak and has_value(streak.get("declineRate10d")) and streak["declineRate10d"] <= DOWNTREND_DECLINE_RATE:
                    decision["reason"] = f"{DOWNTREND_LOOKBACK_DAYS}일 하락추세 {streak['declineRate10d']:.2f}% - 추가매수 차단"
                    return decision

                if trend == "UP" and trend_cnt >= 1 and previous_trend == "DOWN" and previous_trend_cnt >= 6:
                    if streak and streak["downCnt"] >= 5:
                        decision["reason"] = f"일봉 연속하락 {streak['downCnt']}일 - 추가매수 차단"
                        return decision

                    if streak and streak["prevUpCnt"] >= 5 and streak["downCnt"] >= 1:
                        decision["reason"] = f"일봉 상승 {streak['prevUpCnt']}일 후 하락 전환 - 추가매수 차단"
                        return decision

                    decision["action"] = "BUY_EXEC"
                    decision["reason"] = "손실권 하락 6회 후 상승 반전 추가매수"
                    return decision

                if streak and streak["prevDownCnt"] >= 5 and streak["upCnt"] >= 1:
                    decision["action"] = "BUY_EXEC"
                    decision["reason"] = f"손실권 일봉 하락 {streak['prevDownCnt']}일 후 상승 전환 추가매수"
                    return decision

            decision["reason"] = "손실권이나 추가매수 반전 조건 미충족" if is_yes(order_yn) else "주문사용여부 미사용 - 손절기준 미도달, 추가매수 스킵"
            return decision

        decision["reason"] = "수익/손실 기준가 미도달"
        return decision

    else:
        decision["stat"] = "BUY"

        if recent_sell_count > 0:
            decision["reason"] = f"{RECENT_SELL_BLOCK_DAYS}일 이내 매도 이력 존재 - 최초매수 제외"
            return decision

        # 2026.07.30 반등신호 최초매수 로직 정지 (Node와 동일하게 비활성 상태 유지)

        if cur_price_per >= buy_percent:
            decision["reason"] = "매수 퍼센트 미충족"
            return decision

        streak = context.get("dailyStreakInfo")
        if streak and has_value(streak.get("declineRate10d")) and streak["declineRate10d"] <= DOWNTREND_DECLINE_RATE:
            decision["reason"] = f"{DOWNTREND_LOOKBACK_DAYS}일 하락추세 {streak['declineRate10d']:.2f}% - 매수 차단"
            return decision

        if trend == "UP" and trend_cnt >= 1 and previous_trend == "DOWN" and previous_trend_cnt >= 6:
            if streak and streak["downCnt"] >= 5:
                decision["reason"] = f"일봉 연속하락 {streak['downCnt']}일 - 매수 차단"
                return decision

            if streak and streak["prevUpCnt"] >= 5 and streak["downCnt"] >= 1:
                decision["reason"] = f"일봉 상승 {streak['prevUpCnt']}일 후 하락 전환 - 매수 차단"
                return decision

            decision["action"] = "BUY_EXEC"
            decision["reason"] = "손실권 하락 6회 후 상승 반전 최초 매수"
            decision["orderPrice"] = context["currentPrice"] - buy_point_amt
            return decision

        if streak and streak["prevDownCnt"] >= 5 and streak["upCnt"] >= 1:
            decision["action"] = "BUY_EXEC"
            decision["reason"] = f"손실권 일봉 하락 {streak['prevDownCnt']}일 후 상승 전환 최초 매수"
            decision["orderPrice"] = context["currentPrice"] - buy_point_amt
            return decision

        decision["reason"] = "매수 퍼센트 충족, 손실권 하락 6회 후 상승 반전 미충족"
        return decision


# ------------------------------------------------------------------
# 주문 실행 (실거래 - LIVE 모드에서만 실제 API 호출)
# ------------------------------------------------------------------
def build_stock_order_body(input_dict):
    stock_code = get_input_value(input_dict, ["jongmogCd", "jongmog_cd", "stk_cd", "stockCode"])
    order_qty = get_input_value(input_dict, ["buyCnt", "sellCnt", "ord_qty", "orderQty", "qty"])
    order_price = get_input_value(input_dict, ["orderPrice", "ord_uv", "price"])
    require_value(stock_code, "종목코드")
    require_value(order_qty, "주문수량")
    require_value(order_price, "지정가 주문단가")
    return {
        "dmst_stex_tp": input_dict.get("dmst_stex_tp") or "KRX",
        "stk_cd": str(stock_code), "ord_qty": str(order_qty), "ord_uv": str(order_price),
        "trde_tp": "0", "cond_uv": "",
    }


def build_cancel_order_body(input_dict):
    stock_code = get_input_value(input_dict, ["jongmogCd", "jongmog_cd", "stk_cd", "stockCode"])
    order_no = get_input_value(input_dict, ["orderNo", "order_no", "orig_ord_no"])
    cancel_qty = get_input_value(input_dict, ["cancelQty", "cncl_qty", "qty"]) or "0"
    require_value(stock_code, "종목코드")
    require_value(order_no, "원주문번호")
    return {
        "dmst_stex_tp": input_dict.get("dmst_stex_tp") or "KRX",
        "orig_ord_no": str(order_no), "stk_cd": str(stock_code), "cncl_qty": str(cancel_qty),
    }


def get_order_context(conn, input_dict):
    user_id = input_dict.get("user_id") or input_dict.get("userId")
    accno = input_dict.get("accno")
    require_value(user_id, "user_id")

    accno_info = get_accno_info(conn, accno, user_id)
    token = get_row_value(accno_info, "TOKEN")
    token_type = get_row_value(accno_info, "TOKEN_TYPE") or "Bearer"
    refresh_yn = get_row_value(accno_info, "REFRESH_YN")
    appkey = get_row_value(accno_info, "APPKEY")
    secretkey = get_row_value(accno_info, "SECRETKEY")
    selected_accno = get_row_value(accno_info, "ACCNO")

    require_value(appkey, "appkey")
    require_value(secretkey, "secretkey")
    require_value(selected_accno, "계좌번호")
    require_value(token, "토큰")

    if refresh_yn == "Y":
        raise RuntimeError("토큰 만료 상태입니다. 토큰 갱신 기능 연동 후 주문이 가능합니다.")

    return {"accno": selected_accno, "token": token, "tokenType": token_type}


def call_order_api(conn, input_dict, api_id, body):
    context = get_order_context(conn, input_dict)
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"{context['tokenType']} {context['token']}",
        "cont-yn": "N", "next-key": "", "api-id": api_id,
    }
    _, data = kiwoom_post(KIWOOM_HOST + ORDER_ENDPOINT, body, headers)
    return {"request": {"apiId": api_id, "accno": context["accno"], "body": body}, "response": data}


def buy_stock(conn, input_dict):
    body = build_stock_order_body(input_dict)
    result = call_order_api(conn, input_dict, "kt10000", body)
    user_id = input_dict.get("user_id") or input_dict.get("userId")
    order_no = get_response_value(result["response"], ["ord_no", "order_no", "ORDER_NO"])
    return_msg = get_response_value(result["response"], ["return_msg", "msg", "message", "RETURN_MSG"])
    sql, params = query_insert_api_order_hist(body["stk_cd"], body["ord_uv"], body["ord_uv"], body["ord_qty"],
                                               user_id, "0", "0", result["request"]["accno"], "0.015", "0.15",
                                               order_no, "0", return_msg)
    result["orderHist"] = execute_write(conn, sql, params)
    return result


def sell_stock(conn, input_dict):
    body = build_stock_order_body(input_dict)
    result = call_order_api(conn, input_dict, "kt10001", body)
    user_id = input_dict.get("user_id") or input_dict.get("userId")
    order_idx = get_input_value(input_dict, ["orderIdx", "order_idx", "idx"])
    order_no = get_response_value(result["response"], ["ord_no", "order_no", "ORDER_NO"])
    return_msg = get_response_value(result["response"], ["return_msg", "msg", "message", "RETURN_MSG"])

    if has_value(order_idx):
        sql, params = query_update_api_order_hist(body["ord_uv"], body["ord_qty"], body["ord_uv"], "0",
                                                    body["stk_cd"], user_id, order_idx, order_no, return_msg)
        result["orderHist"] = execute_write(conn, sql, params)

    return result


def cancel_stock(conn, input_dict):
    return call_order_api(conn, input_dict, "kt10003", build_cancel_order_body(input_dict))


def cancel_buy_stock(conn, input_dict):
    result = cancel_stock(conn, input_dict)
    user_id = input_dict.get("user_id") or input_dict.get("userId")
    jongmog_cd = get_input_value(input_dict, ["jongmogCd", "jongmog_cd", "stk_cd", "stockCode"])
    order_no = get_input_value(input_dict, ["orderNo", "order_no", "orig_ord_no"])
    sql, params = query_update_api_order_hist_stat(jongmog_cd, user_id, order_no, "AD_240_12")
    result["orderHist"] = execute_write(conn, sql, params)
    return result


def cancel_sell_stock(conn, input_dict):
    result = cancel_stock(conn, input_dict)
    user_id = input_dict.get("user_id") or input_dict.get("userId")
    jongmog_cd = get_input_value(input_dict, ["jongmogCd", "jongmog_cd", "stk_cd", "stockCode"])
    order_no = get_input_value(input_dict, ["orderNo", "order_no", "orig_ord_no"])
    sql, params = query_update_api_order_hist_sell_cancel(jongmog_cd, user_id, order_no)
    result["orderHist"] = execute_write(conn, sql, params)
    return result


def guarded_buy_stock(conn, input_dict):
    if not LIVE:
        log(f"[DRY-RUN] 매수 주문 스킵 - {input_dict}")
        return {"response": {"dry_run": True}}
    return buy_stock(conn, input_dict)


def guarded_sell_stock(conn, input_dict):
    if not LIVE:
        log(f"[DRY-RUN] 매도 주문 스킵 - {input_dict}")
        return {"response": {"dry_run": True}}
    return sell_stock(conn, input_dict)


def execute_order_decision(conn, user_info, decision):
    reg_id = get_row_value(user_info, "REG_ID")
    input_dict = {
        "user_id": reg_id, "accno": get_row_value(user_info, "ACCNO"),
        "jongmogCd": decision["stockCode"],
        "orderPrice": str(max(1, math.floor(decision.get("orderPrice") or decision["currentPrice"]))),
        "buyCnt": str(max(1, math.floor(decision.get("orderQty") or 1))),
        "sellCnt": str(max(1, math.floor(decision.get("orderQty") or 1))),
        "orderIdx": decision.get("orderIdx"),
    }

    if decision["action"] == "BUY_EXEC":
        result = guarded_buy_stock(conn, input_dict)
        log(f"매수 주문 실행 완료 stockCode={decision['stockCode']} reason={decision['reason']}")
        send_order_mail("매수 주문 실행 완료", decision)
        return result

    if decision["action"] == "SELL_EXEC":
        result = guarded_sell_stock(conn, input_dict)
        log(f"매도 주문 실행 완료 stockCode={decision['stockCode']} reason={decision['reason']}")

        if decision.get("stopLossYn") == "Y":
            sql, params = query_upd_monitor_order_yn(decision["stockCode"], "AD_110_11", reg_id)
            guarded_db_write(conn, sql, params, "손절 매도 후 주문사용여부 미사용 처리")

        send_order_mail("매도 주문 실행 완료", decision)
        return result

    log(f"주문 실행 스킵 stockCode={decision['stockCode']} action={decision['action']} reason={decision['reason']}")
    return None


def run_buy_sell_decision(conn, users, monitor_rows, current_price_maps_by_user, test_mode,
                           user_monitor_code_map=None):
    daily_streak_map = get_daily_streak_map(conn)
    log(f"일봉 연속등락 맵 조회 완료 count={len(daily_streak_map)}")
    user_monitor_code_map = user_monitor_code_map or {}

    results = []
    for user_info in users:
        reg_id = get_row_value(user_info, "REG_ID")
        price_map = current_price_maps_by_user.get(reg_id, {})
        # 2026.09.11 monitor_rows는 현재가 조회 효율을 위해 전체 사용자 통합 목록이라,
        # 매수/매도 판단은 본인이 실제로 감시하는 종목만 대상으로 함
        own_codes = user_monitor_code_map.get(reg_id, set())

        stock_profit_map = {}
        try:
            stock_profit_map = get_account_stock_profit_map(user_info)
        except Exception as e:
            log(f"계좌평가현황 종목별 수익률 맵 조회 실패 regId={reg_id}: {e}")

        for row in monitor_rows:
            stock_code = get_row_value(row, "JONGMOG_CD")
            if stock_code not in own_codes:
                continue

            stock_name = get_row_value(row, "JONGMOG_NM")
            current_price = price_map.get(stock_code)
            item = {"regId": reg_id, "stockCode": stock_code, "decision": None, "orderResult": None, "error": None}

            if not has_value(current_price):
                item["error"] = "현재가 없음"
                results.append(item)
                log(f"매수/매도 판단 스킵 - 현재가 없음 stockCode={stock_code} regId={reg_id}")
                continue

            try:
                context = get_order_decision_context(conn, stock_code, reg_id, current_price)
                context["dailyStreakInfo"] = daily_streak_map.get(stock_code)
                context["stockProfitInfo"] = stock_profit_map.get(stock_code)

                decision = build_order_decision(context, test_mode)
                decision["stockName"] = stock_name
                item["decision"] = decision

                if decision["action"] in ("BUY_EXEC", "SELL_EXEC"):
                    item["orderResult"] = execute_order_decision(conn, user_info, decision)
                else:
                    execute_order_decision(conn, user_info, decision)
            except Exception as e:
                item["error"] = str(e)
                log(f"매수/매도 판단 실패 stockCode={stock_code} regId={reg_id}: {e}")

            results.append(item)

    return results


# ------------------------------------------------------------------
# 오전 일일정리 / 실행시간 체크
# ------------------------------------------------------------------
def run_daily_morning_maintenance(conn):
    now = datetime.now()
    today_text = get_date_text(now)
    run_window_yn = "Y" if should_run_daily_morning_maintenance(now) else "N"
    already_run_yn = "Y" if load_last_daily_maintenance_date() == today_text else "N"

    if run_window_yn != "Y":
        return {"runYn": "N"}
    if already_run_yn == "Y":
        return {"runYn": "N"}

    if not LIVE:
        log(f"[DRY-RUN] 오전 일일정리 스킵 (실거래 모드에서만 실행) today={today_text}")
        return {"runYn": "N"}

    log(f"오전 일일정리 시작 today={today_text}")
    result = {"runYn": "Y"}
    for name, query_fn in (
        ("insCurPriceDayRows", query_ins_cur_price_day),
        ("updOrderHistCancelRows", query_upd_order_hist_cancel),
        ("updOrderHistDelYnRows", query_upd_order_hist_del_yn),
        ("delCurPriceRows", query_del_cur_price),
        ("insStockDailySummaryRows", query_ins_stock_daily_summary),
    ):
        sql, params = query_fn()
        write_result = execute_write(conn, sql, params)
        result[name] = write_result["affectedRows"]

    save_last_daily_maintenance_date(today_text)
    log(f"오전 일일정리 완료 {result}")
    return result


def check_biz_run_time(conn, force_run):
    now = datetime.now()
    today_text = get_date_text(now)
    holiday_row = get_single_row(conn, *query_chk_holiday(today_text))
    holiday_cnt = int(holiday_row.get("HOLIDAY_CNT") or 0) if holiday_row else 0

    # Python weekday(): 월=0 ... 일=6 (JS getDay(): 일=0 ... 토=6) - 주말은 토(5)/일(6)
    weekend_yn = "Y" if now.weekday() in (5, 6) else "N"
    holiday_yn = "Y" if holiday_cnt > 0 else "N"
    time_yn = "Y" if 8 <= now.hour <= 20 else "N"
    run_yn = "Y" if weekend_yn == "N" and holiday_yn == "N" and time_yn == "Y" else "N"

    if force_run:
        run_yn = "Y"

    result = {"today": today_text, "weekendYn": weekend_yn, "holidayYn": holiday_yn, "timeYn": time_yn, "runYn": run_yn}
    log(f"실행시간 체크 완료 {result}")
    return result


def get_api_user_set_rows(conn, user_id):
    sql, params = query_get_api_user_set_list(user_id)
    return fetch_all(conn, sql, params)


def get_monitor_rows(conn):
    sql, params = query_get_monitor_list()
    rows = fetch_all(conn, sql, params)
    log(f"감시목록 조회 완료 count={len(rows)}")
    return rows


# 2026.09.11 사용자별 감시종목 집합 조회 - {REG_ID: {JONGMOG_CD, ...}}
def get_user_monitor_code_map(conn):
    sql, params = query_get_monitor_codes_by_user()
    rows = fetch_all(conn, sql, params)
    code_map = {}
    for row in rows:
        reg_id = get_row_value(row, "REG_ID")
        stock_code = get_row_value(row, "JONGMOG_CD")
        if not has_value(reg_id) or not has_value(stock_code):
            continue
        code_map.setdefault(reg_id, set()).add(stock_code)
    return code_map


# ------------------------------------------------------------------
# 메인 오케스트레이션 (Node runCurrentPriceBiz 포팅)
# ------------------------------------------------------------------
def run_current_price_biz(conn):
    log(f"=== 키움 배치 시작 (LIVE={LIVE}, TEST_MODE={TEST_MODE}) ===")

    run_daily_morning_maintenance(conn)

    if not TEST_MODE:
        run_time = check_biz_run_time(conn, FORCE_RUN)
    else:
        run_time = {"runYn": "Y"}

    if run_time.get("runYn") != "Y":
        log("실행조건 미충족으로 중단")
        return

    users = list(get_api_user_set_rows(conn, USER_ID_FILTER))
    log(f"사용자 설정 목록 조회 완료 userCount={len(users)}")
    monitor_rows = get_monitor_rows(conn)
    user_monitor_code_map = get_user_monitor_code_map(conn)

    user_results = []
    current_price_user_info = None

    for i, user_info in enumerate(users):
        reg_id = get_row_value(user_info, "REG_ID")
        try:
            log(f"REG_ID 처리 시작 regId={reg_id}")
            refreshed = refresh_user_token_if_needed(conn, user_info)
            users[i] = refreshed
            update_account_evaluation(conn, refreshed)
            if current_price_user_info is None:
                current_price_user_info = refreshed
            user_results.append({"regId": reg_id, "success": True})
            log(f"REG_ID 처리 완료 regId={reg_id}")
        except Exception as e:
            user_results.append({"regId": reg_id, "success": False})
            log(f"REG_ID 처리 실패 regId={reg_id}: {e}")

    current_price_maps_by_user = {}
    if current_price_user_info and monitor_rows:
        current_price_results = request_current_price_list(current_price_user_info, monitor_rows)
        save_current_price_rows(conn, current_price_results)
        shared_map = get_current_price_by_stock_code(current_price_results)
        for r in user_results:
            if r["success"]:
                current_price_maps_by_user[r["regId"]] = shared_map

    order_sync_results = sync_order_status(conn, users, monitor_rows)
    sync_totals = {"target": 0, "che": 0, "skip": 0, "cancel": 0, "error": 0}
    for item in order_sync_results:
        sync_totals["target"] += item.get("targetCount", 0)
        sync_totals["che"] += item.get("cheCount", 0)
        sync_totals["skip"] += item.get("skipCount", 0)
        sync_totals["cancel"] += item.get("cancelCount", 0)
        sync_totals["error"] += item.get("errorCount", 0)
    log(f"주문 상태 동기화 완료 {sync_totals}")

    buy_sell_results = run_buy_sell_decision(conn, users, monitor_rows, current_price_maps_by_user, TEST_MODE,
                                              user_monitor_code_map)
    buy_cnt = sum(1 for r in buy_sell_results if r.get("decision") and r["decision"]["action"] == "BUY_EXEC")
    sell_cnt = sum(1 for r in buy_sell_results if r.get("decision") and r["decision"]["action"] == "SELL_EXEC")
    wait_cnt = sum(1 for r in buy_sell_results if r.get("decision") and r["decision"]["action"] == "WAIT")
    error_cnt = sum(1 for r in buy_sell_results if r.get("error"))
    log(f"키움 현재가 업무 실행 완료 buyDecisionCount={buy_cnt} sellDecisionCount={sell_cnt} "
        f"waitDecisionCount={wait_cnt} errorCount={error_cnt}")


def main():
    conn = get_connection()
    try:
        run_current_price_biz(conn)
    except Exception as e:
        log(f"키움 현재가 업무 실행 실패: {e}")
        sys.exit(1)
    finally:
        conn.close()


# 2026.09.11 배치관리(TB_BATCH_MASTER) 연동 - 사용여부 체크 + 상태 보고
import batch_status

BATCH_NM = "kiwoom_trading_batch.py"


if __name__ == "__main__":
    if not batch_status.is_enabled(BATCH_NM):
        batch_status.log_skip(BATCH_NM)
        sys.exit(0)

    batch_status.mark_start(BATCH_NM)
    try:
        main()
        batch_status.mark_done(BATCH_NM)
    except Exception as e:
        batch_status.mark_failed(BATCH_NM, str(e))
        raise
