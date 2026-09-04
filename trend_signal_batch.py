#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
키움 TREND 화면(jongmog_trend.php, class.kiwoom.php::getTrendList)의 실시간
5분봉 연속 상승/하락 신호(TREND_COUNT >= TREND_COUNT_THRESHOLD)를 장중 스냅샷으로
캡처해서 CLI AUTO에 등록한다. 실제 답변 생성은 cli_auto_batch.py(1분 cron)가 처리한다.

Node의 stockOutlookService.js(30일 등락분석 신호종목 -> CLI AUTO 등록)와 동일한 패턴을
파이썬으로 옮긴 것 - 시세/재무 등 외부데이터는 쓰지 않고 TREND 화면의 패턴 데이터만 사용한다.

PROJECT는 "chat"으로 등록한다 - 배치 완료 시 PROJECT 작업 디렉토리의 CLAUDE.md에
완료 이력이 자동 append되는데, /workspace/chat 에는 CLAUDE.md가 없어 오염을 피할 수 있다.

[실행방법 / cron 등록 예]
  STOCK_DB_PASS=xxx python3 trend_signal_batch.py
  # 장중 2회(12시/14시), 주말/공휴일 제외: crontab에 아래처럼 등록
  # 0 12,14 * * 1-5 STOCK_DB_PASS=xxx /usr/bin/python3 /workspace/python01/trend_signal_batch.py >> /workspace/python01/log/trend_signal_$(date +\\%Y-\\%m-\\%d).log 2>&1
================================================================================
"""

import os
import sys
from datetime import date, datetime

import pymysql

DB_HOST = "jsh77b1.cafe24app.com"
DB_USER = "jsh77b1"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b1"

CLI_AUTO_PROJECT = "chat"
CLI_AUTO_REG_ID = "trendSignalBatch"
TREND_COUNT_THRESHOLD = 6  # 연속 5분봉 상승/하락 횟수 기준 (자동매매 "6연속 후 반전" 기준과 동일)

SIGNAL_TYPE_UP = "TREND_UP"
SIGNAL_TYPE_DN = "TREND_DN"

# KRX 공식 휴장일 — stock_monitor.py의 MARKET_HOLIDAYS와 동일 목록(음력 공휴일은 매년 갱신 필요, 두 파일 함께 갱신)
MARKET_HOLIDAYS = {
    "2026-01-01",  # 신정
    "2026-02-16",  # 설날 연휴
    "2026-02-17",  # 설날
    "2026-02-18",  # 설날 연휴
    "2026-03-02",  # 삼일절 대체휴일 (3/1 일요일)
    "2026-05-05",  # 어린이날
    "2026-05-25",  # 부처님오신날 대체 (5/24 일요일)
    "2026-08-17",  # 광복절 대체 (8/15 토요일)
    "2026-09-23",  # 추석 연휴
    "2026-09-24",  # 추석 연휴
    "2026-09-25",  # 추석
    "2026-10-09",  # 한글날
    "2026-12-25",  # 크리스마스
    "2026-12-31",  # 연말 휴장
}


def is_market_day(today):
    """평일(월~금)이고 공휴일이 아닌 경우에만 True"""
    if today.weekday() >= 5:  # 토(5)·일(6)
        return False
    return today.strftime("%Y-%m-%d") not in MARKET_HOLIDAYS


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10, cursorclass=pymysql.cursors.DictCursor,
    )


# class.kiwoom.php::getTrendList() 와 동일한 SQL (MySQL 세션변수 기반 연속횟수 계산, 5.x 전용 구문)
TREND_LIST_SQL = """
SELECT AA.JONGMOG_CD
     , (SELECT MAX(X.JONGMOG_NM) FROM TB_API_JONGMOG X WHERE X.JONGMOG_CD = AA.JONGMOG_CD) AS JONGMOG_NM
     , AA.REG_DT
     , AA.CUR_PRICE
     , PER.CUR_PER
     , AA.TREND
     , MAX(AA.TREND_COUNT) AS TREND_COUNT
     , (SELECT IFNULL(BUY_CNT,0) FROM TB_API_ORDER_HIST X WHERE X.JONGMOG_CD = AA.JONGMOG_CD AND X.CLOSE_YN = 'N' AND X.ORDER_STAT = 'AD_240_11' ORDER BY X.BUY_PRICE LIMIT 0,1) AS ORDER_CNT_LAST
     , (SELECT IFNULL(MIN(BUY_PRICE),0) FROM TB_API_ORDER_HIST X WHERE X.JONGMOG_CD = AA.JONGMOG_CD AND X.CLOSE_YN = 'N' AND X.ORDER_STAT = 'AD_240_11') AS ORDER_AMT_LAST
     , (SELECT IFNULL(SUM(BUY_CNT),0) FROM TB_API_ORDER_HIST X WHERE X.JONGMOG_CD = AA.JONGMOG_CD AND X.CLOSE_YN = 'N' AND X.ORDER_STAT = 'AD_240_11') AS ORDER_CNT_SUM
     , PER.CUR_PER_MIN
     , PER.CUR_PER_MAX
     , MAX(AA.TRDE_QTY) AS TRDE_QTY_TODAY
     , AA.DIFF_QTY
     , AA.QTY_STATUS
FROM (
    SELECT JONGMOG_CD
         , REG_DT
         , CUR_PRICE
         , PREV_PRICE
         , TREND
         , @cnt := IF(@prev_trend = TREND AND @prev_code = JONGMOG_CD, @cnt + 1, 1) AS TREND_COUNT
         , @prev_trend := TREND
         , @prev_code := JONGMOG_CD
         , TRDE_QTY
         , DIFF_QTY
         , QTY_STATUS
    FROM (
        SELECT JONGMOG_CD
             , REG_DT
             , CUR_PRICE
             , @prev AS PREV_PRICE
             , CASE WHEN @prev IS NULL THEN 'ZERO'
                    WHEN CUR_PRICE > @prev THEN 'UP'
                    WHEN CUR_PRICE < @prev THEN 'DOWN'
                    ELSE 'ZERO'
               END AS TREND
             , @prev := CUR_PRICE
             , TRDE_QTY
             , TRDE_QTY - @prev_qty AS DIFF_QTY
             , CASE WHEN TRDE_QTY - @prev_qty - @prev_diff_qty > 0 THEN 'UP'
                    WHEN TRDE_QTY - @prev_qty - @prev_diff_qty < 0 THEN 'DOWN'
                    WHEN TRDE_QTY - @prev_qty < 0 THEN 'START'
               END AS QTY_STATUS
             , @prev_diff_qty := TRDE_QTY - @prev_qty
             , @prev_qty := TRDE_QTY
        FROM   TB_API_CUR_PRICE
             , (SELECT @prev := NULL, @prev_qty := 0, @prev_diff_qty := 0) p
        WHERE 1=1
        AND   REG_DT >= DATE(DATE_ADD(%(stdt)s, INTERVAL - 0 DAY))
        AND   REG_DT < DATE(DATE_ADD(%(stdt)s, INTERVAL 1 DAY))
        AND   MINUTE(REG_DT) %% 5 = 0
        AND   TRDE_QTY > 0
        ORDER BY JONGMOG_CD
             , REG_DT
    ) A
    CROSS JOIN (SELECT @cnt := 0, @prev_trend := '', @prev_code := '') vars
    ORDER  BY A.JONGMOG_CD
         , A.REG_DT
) AA
LEFT JOIN (
    SELECT FLOOR(100 * ((A.MAX-A.MIN) - (A.MAX - A.CUR_MIN)) / (A.MAX-A.MIN)) AS CUR_PER_MIN
         , FLOOR(100 * ((A.MAX-A.MIN) - (A.MAX - A.CUR_MAX)) / (A.MAX-A.MIN)) AS CUR_PER_MAX
         , FLOOR(100 * ((A.MAX-A.MIN) - (A.MAX - A.CUR)) / (A.MAX-A.MIN))     AS CUR_PER
         , A.JONGMOG_CD
    FROM (
        SELECT MIN(A.CUR_PRICE_MIN) AS MIN
             , MAX(A.CUR_PRICE_MIN) AS MAX
             , B.CUR_PRICE_MIN  AS CUR_MIN
             , B.CUR_PRICE_MAX  AS CUR_MAX
             , B.CUR_PRICE      AS CUR
             , B.JONGMOG_CD     AS JONGMOG_CD
        FROM   TB_API_CUR_PRICE_DAY A
        INNER  JOIN TB_API_JONGMOG B
        ON     A.JONGMOG_CD = B.JONGMOG_CD
        WHERE  1=1
        AND    A.REG_DT >= DATE_ADD(NOW(), INTERVAL-365 DAY)
        GROUP  BY B.CUR_PRICE_MIN, B.CUR_PRICE_MAX, B.CUR_PRICE, B.JONGMOG_CD
    ) A
) PER
ON     PER.JONGMOG_CD = AA.JONGMOG_CD
WHERE  1=1
AND    AA.TREND IN ('UP', 'DOWN')
AND    AA.TREND_COUNT >= %(cnt)s
GROUP  BY AA.JONGMOG_CD
     , AA.TREND
"""


def fetch_trend_signals(conn, stdt):
    with conn.cursor() as cur:
        cur.execute(TREND_LIST_SQL, {"stdt": stdt, "cnt": TREND_COUNT_THRESHOLD})
        return cur.fetchall()


# 전일 총거래량(TRDE_QTY_MAX = 그날 마지막 누적거래량) 조회 - 오늘 누적거래량과 비교해서
# 거래량 변화가 "평소 대비" 큰지 판단하는 근거로 사용
def fetch_prev_day_volume_map(conn, today_dash):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(REG_DATE) AS PREV_DATE FROM TB_API_CUR_PRICE_DAY WHERE REG_DATE < %(today)s",
            {"today": today_dash},
        )
        row = cur.fetchone()
        prev_date = row["PREV_DATE"] if row else None
        if not prev_date:
            return {}

        cur.execute(
            "SELECT JONGMOG_CD, MAX(TRDE_QTY_MAX) AS PREV_TRDE_QTY_MAX "
            "FROM TB_API_CUR_PRICE_DAY WHERE REG_DATE = %(prev_date)s GROUP BY JONGMOG_CD",
            {"prev_date": prev_date},
        )
        return {r["JONGMOG_CD"]: r["PREV_TRDE_QTY_MAX"] for r in cur.fetchall()}


def build_prompt(row):
    trend_nm = "상승" if row["TREND"] == "UP" else "하락"
    qty_status_nm = {"UP": "증가", "DOWN": "감소", "START": "거래시작"}.get(row["QTY_STATUS"], str(row["QTY_STATUS"]))

    lines = []
    lines.append(f'아래는 국내 주식 종목 "{row["JONGMOG_NM"]}"({row["JONGMOG_CD"]})의 오늘 장중 실시간 가격 패턴 스냅샷이다.')
    lines.append("")
    lines.append("[실시간 TREND 패턴 - 5분봉 기준]")
    lines.append(f"- 연속 {trend_nm} 횟수: {int(row['TREND_COUNT'])}회 (5분 간격 기준)")
    lines.append(f"- 현재가: {row['CUR_PRICE']:,}")
    if row.get("CUR_PER") is not None:
        lines.append(f"- 당일 최저~최고가 구간 내 현재가 위치: {row['CUR_PER']}% (0%=당일최저, 100%=당일최고)")
    lines.append(f"- 직전 대비 거래량 변화: {int(row['DIFF_QTY']):,}주 ({qty_status_nm})")
    if row.get("TRDE_QTY_TODAY") is not None:
        lines.append(f"- 오늘 누적 거래량: {int(row['TRDE_QTY_TODAY']):,}주")
    prev_vol = row.get("PREV_TRDE_QTY_MAX")
    if prev_vol:
        pct = round(100 * row["TRDE_QTY_TODAY"] / prev_vol)
        lines.append(f"- 전일 총 거래량 대비: {pct}% ({int(prev_vol):,}주)")
    if row.get("ORDER_CNT_SUM") and row["ORDER_CNT_SUM"] > 0:
        lines.append(f"- 보유 중인 매수 주문: {row['ORDER_CNT_SUM']}건, 최근 매수가 {row['ORDER_AMT_LAST']:,}")
    lines.append("")
    lines.append("이 종목을 사거나 팔라고 추천하지 말고, 위 실시간 패턴 숫자만 놓고 다음을 3~4문장으로 답해줘.")
    lines.append("1) 이 연속 흐름이 장중 일시적 쏠림에 가까운지, 추세성 움직임에 가까운지 숫자 근거로 해석")
    lines.append("2) 당일 최저~최고 구간 내 현재 위치를 감안했을 때 추가로 같은 방향 여력이 있어 보이는지")
    lines.append("3) 거래량 변화가 이 가격 움직임을 뒷받침하는지(거래량 증가 동반 여부)")
    lines.append("뉴스나 재무 정보는 모른다는 전제로, 순수 가격/거래량 패턴 해석에만 집중해줘.")
    lines.append("답변은 결과 텍스트만 출력하고, 인사말이나 작업 설명은 붙이지 마.")
    lines.append("문장이 끝날 때마다(마침표 뒤) 줄바꿈을 넣어서 한 줄에 한 문장씩 보이게 작성해줘.")

    return "\n".join(lines)


def insert_cli_auto(conn, question):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO TB_CLI_AUTO (PROJECT, SRC_TYPE, TITLE, QUESTION, STATUS, REG_ID, REG_DATE) "
            "VALUES (%s, 'BATCH', '', %s, 'AD_320_10', %s, NOW())",
            (CLI_AUTO_PROJECT, question, CLI_AUTO_REG_ID),
        )
        return cur.lastrowid


def insert_stock_outlook(conn, jongmog_cd, signal_type, cli_auto_seq, reg_date):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO TB_STOCK_OUTLOOK (JONGMOG_CD, SIGNAL_TYPE, CLI_AUTO_SEQ, REG_DATE) "
            "VALUES (%s, %s, %s, %s)",
            (jongmog_cd, signal_type, cli_auto_seq, reg_date),
        )


def main():
    if not DB_PASS:
        log("STOCK_DB_PASS 환경변수가 없습니다. 종료합니다.")
        sys.exit(1)

    today = date.today()

    if not is_market_day(today):
        log(f"휴장일({today.strftime('%Y-%m-%d')}, 주말 또는 공휴일)이라 실행하지 않습니다.")
        sys.exit(0)

    stdt = today.strftime("%Y%m%d")
    today_dash = today.strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        rows = fetch_trend_signals(conn, stdt)
        log(f"TREND 신호종목 조회 완료: {len(rows)}건 (기준 TREND_COUNT >= {TREND_COUNT_THRESHOLD})")

        prev_volume_map = fetch_prev_day_volume_map(conn, today_dash)

        registered = 0
        for row in rows:
            row["PREV_TRDE_QTY_MAX"] = prev_volume_map.get(row["JONGMOG_CD"])
            try:
                prompt = build_prompt(row)
                cli_auto_seq = insert_cli_auto(conn, prompt)
                signal_type = SIGNAL_TYPE_UP if row["TREND"] == "UP" else SIGNAL_TYPE_DN
                insert_stock_outlook(conn, row["JONGMOG_CD"], signal_type, cli_auto_seq, today)
                conn.commit()
                registered += 1
                log(f"  등록: {row['JONGMOG_NM']}({row['JONGMOG_CD']}) {row['TREND']} {row['TREND_COUNT']}회 -> CLI_AUTO SEQ={cli_auto_seq}")
            except Exception as e:
                conn.rollback()
                log(f"  등록 실패: {row.get('JONGMOG_CD')} - {e}")

        log(f"완료: {registered}/{len(rows)}건 등록")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
