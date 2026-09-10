#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
etf@email.com 계정의 감시목록(TB_API_JONGMOG_MONITOR_YN) 중 ETF로 보이는 종목의
구성종목(보유 상위 종목/비중)을 CLI AUTO에 하루 1회 등록한다.
실제 웹검색 + 답변 생성은 cli_auto_batch.py(1분 cron)가 처리한다.

trend_signal_batch.py와 동일 패턴 - PROJECT는 "chat"으로 등록하고,
TB_STOCK_OUTLOOK(SIGNAL_TYPE='ETF_COMP')을 통해 종목코드와 CLI_AUTO 답변을 연결한다.
jongmog_monitor_list.php의 "구성" 버튼이 이 SIGNAL_TYPE으로 오늘자 데이터를 조회해서 보여준다.

ETF 판단은 종목명에 국내 ETF 브랜드명이 포함되는지로 필터링한다(휴리스틱).
이미 오늘자로 등록된 종목은 중복 등록하지 않는다.

[실행방법 / cron 등록 예]
  STOCK_DB_PASS=xxx python3 etf_composition_batch.py
  # 매일 1회(장 시작 전), crontab 등록 예:
  # 0 8 * * * STOCK_DB_PASS=xxx /usr/bin/python3 /workspace/python01/etf_composition_batch.py >> /workspace/python01/log/etf_composition_$(date +\\%Y-\\%m-\\%d).log 2>&1
================================================================================
"""

import os
import sys
from datetime import date, datetime

import pymysql

DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"

CLI_AUTO_PROJECT = "chat"
CLI_AUTO_REG_ID = "etfCompositionBatch"
TARGET_USER_ID = "etf@email.com"

SIGNAL_TYPE_COMPOSITION = "ETF_COMP"

# 종목명에 아래 브랜드명이 포함되면 ETF로 판단 (휴리스틱, 신규 브랜드 생기면 추가)
ETF_BRAND_NAMES = [
    "KODEX", "TIGER", "ACE", "KBSTAR", "KOSEF", "SOL", "HANARO",
    "ARIRANG", "PLUS", "RISE", "TIMEFOLIO", "히어로즈", "WOORI", "FOCUS", "마이다스",
]


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10, cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_target_stocks(conn):
    brand_conditions = " OR ".join(["J.JONGMOG_NM LIKE %s"] * len(ETF_BRAND_NAMES))
    sql = f"""
        SELECT M.JONGMOG_CD, J.JONGMOG_NM
        FROM   TB_API_JONGMOG_MONITOR_YN M
        JOIN   TB_API_JONGMOG J ON M.JONGMOG_CD = J.JONGMOG_CD
        WHERE  M.REG_ID = %s
        AND    ({brand_conditions})
        GROUP  BY M.JONGMOG_CD
    """
    params = [TARGET_USER_ID] + [f"%{brand}%" for brand in ETF_BRAND_NAMES]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_already_registered_today(conn, today):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT JONGMOG_CD FROM TB_STOCK_OUTLOOK WHERE SIGNAL_TYPE = %s AND REG_DATE = %s",
            (SIGNAL_TYPE_COMPOSITION, today),
        )
        return {r["JONGMOG_CD"] for r in cur.fetchall()}


def build_prompt(jongmog_nm, jongmog_cd):
    lines = []
    lines.append(f'국내 상장 ETF "{jongmog_nm}"({jongmog_cd})의 구성종목(보유 상위 종목)을 알려줘.')
    lines.append("")
    lines.append("- 실시간 검색이 필요하면 WebSearch 도구로 운용사 공식 페이지나 ETF 정보 사이트를 조회해줘.")
    lines.append("- 상위 10개 종목명과 비중(%)을 아래와 같은 마크다운 표 형식으로만 답변해줘(다른 설명 문구 없이 표만).")
    lines.append("| 순위 | 종목명 | 비중(%) |")
    lines.append("|---|---|---|")
    lines.append("| 1 | 삼성전자 | 28.6 |")
    lines.append("- 매수/매도를 추천하지 말고, 구성종목 정보만 사실대로 전달해줘.")
    lines.append("- 답변은 표만 출력하고, 인사말이나 작업 설명은 붙이지 마.")

    return "\n".join(lines)


def insert_cli_auto(conn, jongmog_nm, question):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO TB_CLI_AUTO (PROJECT, SRC_TYPE, TITLE, QUESTION, STATUS, REG_ID, REG_DATE) "
            "VALUES (%s, 'BATCH', %s, %s, 'AD_320_10', %s, NOW())",
            (CLI_AUTO_PROJECT, f"{jongmog_nm} 구성종목", question, CLI_AUTO_REG_ID),
        )
        return cur.lastrowid


def insert_stock_outlook(conn, jongmog_cd, cli_auto_seq, reg_date):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO TB_STOCK_OUTLOOK (JONGMOG_CD, SIGNAL_TYPE, CLI_AUTO_SEQ, REG_DATE) "
            "VALUES (%s, %s, %s, %s)",
            (jongmog_cd, SIGNAL_TYPE_COMPOSITION, cli_auto_seq, reg_date),
        )


def main():
    if not DB_PASS:
        log("STOCK_DB_PASS 환경변수가 없습니다. 종료합니다.")
        sys.exit(1)

    today = date.today()

    conn = get_connection()
    try:
        stocks = fetch_target_stocks(conn)
        log(f"{TARGET_USER_ID} ETF 감시종목 조회 완료: {len(stocks)}건")

        already_done = fetch_already_registered_today(conn, today)
        if already_done:
            log(f"이미 오늘 등록됨(스킵): {len(already_done)}건")

        registered = 0
        for row in stocks:
            jongmog_cd = row["JONGMOG_CD"]
            jongmog_nm = row["JONGMOG_NM"]

            if jongmog_cd in already_done:
                continue

            try:
                question = build_prompt(jongmog_nm, jongmog_cd)
                cli_auto_seq = insert_cli_auto(conn, jongmog_nm, question)
                insert_stock_outlook(conn, jongmog_cd, cli_auto_seq, today)
                conn.commit()
                registered += 1
                log(f"  등록: {jongmog_nm}({jongmog_cd}) -> CLI_AUTO SEQ={cli_auto_seq}")
            except Exception as e:
                conn.rollback()
                log(f"  등록 실패: {jongmog_cd} - {e}")

        log(f"완료: {registered}/{len(stocks)}건 등록")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
