#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
지정시각(09:30/10:00/11:00/12:00/13:00/14:00/15:00/15:30) 증시 리포트 메일 발송.

원래 python01/stock_monitor.py에 있던 send_report_email()이 2026년경 Node
(cafe24_jsh77b1/services/stockReportMail.js, web.js 10분 배치)로 이전됐던 것을,
Node 기능을 다른 곳으로 이관하는 작업의 일환으로 다시 파이썬(cron)으로 되돌린다.
쿼리는 stockReportQueries.js와 완전히 동일 (원래 이 python 버전에서 그대로 옮겨간 것).

[실행방법 / cron 등록 예]
  STOCK_DB_PASS=xxx STOCK_MAIL_PASS=xxx python3 stock_report_mail_batch.py
  # 정시(09:30,10:00,11:00,12:00,13:00,14:00,15:00,15:30)에만 실제 발송,
  # 그 외 시각(매시 30분)에 걸려도 REPORT_TIMES에 없으면 조용히 종료한다.
  # 0,30 9-15 * * 1-5 /usr/bin/python3 /workspace/python01/stock_report_mail_batch.py >> /workspace/python01/log/stock_report_mail_$(date +\\%Y-\\%m-\\%d).log 2>&1
================================================================================
"""

import os
import ssl
import smtplib
import sys
from datetime import date, datetime
from email.mime.text import MIMEText
from email.header import Header

import pymysql

DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
MAIL_SENDER = "ack1000hu@gmail.com"
MAIL_RECEIVER = "ack1000hu@gmail.com"
MAIL_PASS = os.getenv("STOCK_MAIL_PASS", "")

ORDER_HIST_REPORT_USER = "jsh77b@naver.com"
REPORT_TIMES = {"09:30", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "15:30"}


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10, cursorclass=pymysql.cursors.DictCursor,
    )


def is_trading_day(conn, today):
    """주말 또는 TB_HOLIDAY 공휴일이면 False (stockReportMail.js::isTradingDay와 동일 조건)"""
    if today.weekday() >= 5:
        return False
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS CNT FROM TB_HOLIDAY WHERE HOLIDAY_DT = %s", (today.strftime("%Y-%m-%d"),))
        row = cur.fetchone()
        return not (row and row["CNT"] > 0)


def fmt_price(val):
    return f"{int(val):,}" if val else "-"


# ------------------------------------------------------------------
# queries/stockReportQueries.js 포팅
# ------------------------------------------------------------------
def query_common_stocks(conn, category_a, category_b):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.RANK_NO, a.STOCK_NAME,
                   MAX(CASE WHEN HOUR(a.REG_DT) = 10 THEN a.CURRENT_PRICE END) AS P10,
                   MAX(CASE WHEN HOUR(a.REG_DT) = 12 THEN a.CURRENT_PRICE END) AS P12,
                   MAX(CASE WHEN HOUR(a.REG_DT) = 14 THEN a.CURRENT_PRICE END) AS P14
            FROM   TB_STOCK_RANKING_HIST a
            WHERE  a.CATEGORY = %s
              AND  DATE(a.REG_DT) = CURDATE()
              AND  a.STOCK_CODE IN (
                    SELECT b.STOCK_CODE FROM TB_STOCK_RANKING_HIST b
                    WHERE b.CATEGORY = %s
                      AND DATE(b.REG_DT) = CURDATE()
                      AND HOUR(b.REG_DT) = HOUR(a.REG_DT)
                  )
            GROUP BY a.STOCK_CODE, a.RANK_NO, a.STOCK_NAME
            ORDER BY MAX(ABS(a.NET_VALUE)) DESC
            """,
            (category_a, category_b),
        )
        return cur.fetchall()


def query_market_index(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MARKET_TYPE, CURRENT_PRICE, CHANGE_VAL, CHANGE_RATE, REG_DT
            FROM   TB_MARKET_INDEX
            ORDER  BY FIELD(MARKET_TYPE, 'KOSPI', 'KOSDAQ')
            """
        )
        return cur.fetchall()


def query_user_set_list(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT REG_ID, TOT_INS_AMT, PRSM_DPST_ASET_AMT
            FROM   TB_API_USER_SET
            WHERE  DEL_YN = 'N'
            ORDER  BY REG_ID
            """
        )
        return cur.fetchall()


def query_order_hist_profit(conn, reg_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT j.JONGMOG_NM, o.JONGMOG_CD,
                   SUM(o.BUY_PRICE * o.BUY_CNT) AS BUY_AMT,
                   SUM(o.BUY_CNT)               AS BUY_CNT,
                   j.CUR_PRICE
            FROM   TB_API_ORDER_HIST o
            JOIN   TB_API_JONGMOG j ON j.JONGMOG_CD = o.JONGMOG_CD
            WHERE  o.REG_ID   = %s
              AND  o.DEL_YN   = 'N'
              AND  o.CLOSE_YN = 'N'
            GROUP  BY o.JONGMOG_CD, j.JONGMOG_NM, j.CUR_PRICE
            ORDER  BY BUY_AMT DESC
            """,
            (reg_id,),
        )
        return cur.fetchall()


# ------------------------------------------------------------------
# services/stockReportMail.js 포팅 (HTML 조립)
# ------------------------------------------------------------------
def make_index_table(rows):
    if not rows:
        return "<p>시장 지수 없음</p>"
    reg_dt_str = str(rows[0]["REG_DT"]) if rows[0].get("REG_DT") else ""
    html = f"<h3 style='color:#555;text-align:left;'>시장 지수 <span style='font-size:13px;color:#999;font-weight:normal;'>{reg_dt_str}</span></h3>"
    html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:16px;width:480px;'>"
    html += ("<tr style='background:#f2f2f2;'>"
             "<th style='text-align:center;width:120px;'>지수명</th>"
             "<th style='text-align:center;width:140px;'>현재가</th>"
             "<th style='text-align:center;width:140px;'>전일대비</th>"
             "<th style='text-align:center;width:80px;'>등락률</th></tr>")
    for r in rows:
        chg = float(r["CHANGE_VAL"] or 0)
        color = "#e74c3c" if chg > 0 else ("#2980b9" if chg < 0 else "")
        sign = "+" if chg > 0 else ""
        rate = float(r["CHANGE_RATE"] or 0) * 100
        html += (f"<tr><td style='text-align:center;'>{r['MARKET_TYPE']}</td>"
                 f"<td style='text-align:right;'><font color='{color}'>{fmt_price(r['CURRENT_PRICE'])}</font></td>"
                 f"<td style='text-align:right;'><font color='{color}'>{sign}{fmt_price(r['CHANGE_VAL'])}</font></td>"
                 f"<td style='text-align:right;'><font color='{color}'>{sign}{rate:.2f}%</font></td></tr>")
    html += "</table>"
    return html


def make_user_table(rows):
    if not rows:
        return "<p>유저 정보 없음</p>"
    html = "<h3 style='color:#2980b9;text-align:left;'>유저별 재무현황</h3>"
    html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:16px;width:480px;'>"
    html += ("<tr style='background:#f2f2f2;'>"
             "<th style='text-align:center;width:200px;'>유저ID</th>"
             "<th style='text-align:center;width:150px;'>수익금액</th>"
             "<th style='text-align:center;width:130px;'>수익율</th></tr>")
    for r in rows:
        ins_num = float(r["TOT_INS_AMT"] or 0)
        prsm_num = float(r["PRSM_DPST_ASET_AMT"] or 0)
        profit = prsm_num - ins_num
        rate = (profit / ins_num * 100) if ins_num != 0 else 0
        color = "#e74c3c" if profit >= 0 else "#2980b9"
        html += (f"<tr><td style='text-align:center;'>{r['REG_ID'] or '-'}</td>"
                 f"<td style='text-align:right;'><font color='{color}'>{fmt_price(profit)}</font></td>"
                 f"<td style='text-align:right;'><font color='{color}'>{rate:.2f}%</font></td></tr>")
    html += "</table>"
    return html


def make_order_hist_table(rows):
    if not rows:
        return "<p>주문이력 없음</p>"
    html = "<h3 style='color:#8e44ad;text-align:left;'>주문이력 평가손익</h3>"
    html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:16px;width:480px;'>"
    html += ("<tr style='background:#f2f2f2;'>"
             "<th style='text-align:center;width:180px;'>종목명</th>"
             "<th style='text-align:center;width:150px;'>평가손익</th>"
             "<th style='text-align:center;width:100px;'>손익율</th></tr>")
    for r in rows:
        buy_amt_num = float(r["BUY_AMT"] or 0)
        eval_amt_num = float(r["CUR_PRICE"] or 0) * float(r["BUY_CNT"] or 0)
        eval_profit = eval_amt_num - buy_amt_num
        rate = (eval_profit / buy_amt_num * 100) if buy_amt_num != 0 else 0
        color = "#e74c3c" if eval_profit >= 0 else "#2980b9"
        html += (f"<tr><td style='text-align:left;max-width:180px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px;'>{r['JONGMOG_NM'] or '-'}</td>"
                 f"<td style='text-align:right;'><font color='{color}'>{fmt_price(eval_profit)}</font></td>"
                 f"<td style='text-align:right;'><font color='{color}'>{rate:.2f}%</font></td></tr>")
    html += "</table>"
    return html


def make_stock_table(rows, label):
    if not rows:
        return f"<p>{label} 없음</p>"
    color = "#27ae60" if "매수" in label else "#e74c3c"
    html = f"<h3 style='color:{color};text-align:left;'>{label}</h3>"
    html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:16px;width:480px;'>"
    html += ("<tr style='background:#f2f2f2;'>"
             "<th style='text-align:center;width:50px;'>순위</th>"
             "<th style='text-align:center;width:160px;'>종목명</th>"
             "<th style='text-align:center;width:150px;'>현재가</th>"
             "<th style='text-align:center;width:80px;'>시간</th></tr>")
    for r in rows:
        times = [("10", r.get("P10")), ("12", r.get("P12")), ("14", r.get("P14"))]
        filled = [(t, p) for t, p in times if p]
        display = filled if filled else [("10", None)]
        span = len(display)
        for idx, (t_label, price) in enumerate(display):
            if idx == 0:
                html += (f"<tr><td rowspan='{span}' style='text-align:center;vertical-align:middle;'>{r['RANK_NO']}</td>"
                         f"<td rowspan='{span}' style='text-align:left;max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;vertical-align:middle;'>{r['STOCK_NAME']}</td>"
                         f"<td style='text-align:right;'>{fmt_price(price)}</td>"
                         f"<td style='text-align:center;'>{t_label}</td></tr>")
            else:
                html += (f"<tr><td style='text-align:right;'>{fmt_price(price)}</td>"
                         f"<td style='text-align:center;'>{t_label}</td></tr>")
    html += "</table>"
    return html


def build_report_mail_html(conn):
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")

    index_rows = query_market_index(conn)
    user_rows = query_user_set_list(conn)
    order_hist_rows = query_order_hist_profit(conn, ORDER_HIST_REPORT_USER)
    buy_rows = query_common_stocks(conn, "FOR_BUY", "INS_BUY")
    sell_rows = query_common_stocks(conn, "FOR_SELL", "INS_SELL")

    html = "<html><head><meta charset='utf-8'></head><body style='font-family:sans-serif;'>"
    html += f"<h2 style='text-align:left;'>증시 리포트 — {now_str}</h2>"
    html += make_index_table(index_rows)
    html += "<br>"
    html += make_user_table(user_rows)
    html += "<br>"
    html += make_order_hist_table(order_hist_rows)
    html += "<br>"
    html += make_stock_table(buy_rows, "공통 순매수 종목")
    html += "<br>"
    html += make_stock_table(sell_rows, "공통 순매도 종목")
    html += "<p style='text-align:left;color:gray;font-size:12px;'>HOONE.NET 자동 리포트</p>"
    html += "</body></html>"

    return html, now_str


def send_mail(subject, html):
    msg = MIMEText(html, "html", "utf-8")
    msg["From"] = MAIL_SENDER
    msg["To"] = MAIL_RECEIVER
    msg["Subject"] = Header(subject, "utf-8")

    ctx = ssl.create_default_context()

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as smtp:
        smtp.login(MAIL_SENDER, MAIL_PASS)
        smtp.sendmail(MAIL_SENDER, MAIL_RECEIVER, msg.as_string())


def main():
    if not DB_PASS:
        log("STOCK_DB_PASS 환경변수가 없습니다. 종료합니다.")
        sys.exit(1)
    if not MAIL_PASS:
        log("STOCK_MAIL_PASS 환경변수가 없습니다. 종료합니다.")
        sys.exit(1)

    now = datetime.now()
    hhmm = now.strftime("%H:%M")

    if hhmm not in REPORT_TIMES:
        # cron이 09:00 등 REPORT_TIMES에 없는 정각에도 걸릴 수 있어 조용히 종료
        return

    conn = get_connection()
    try:
        if not is_trading_day(conn, date.today()):
            log(f"휴장일이라 발송하지 않습니다. ({hhmm})")
            return

        html, now_str = build_report_mail_html(conn)
        send_mail(f"[Stock Report] {now_str}", html)
        log(f"발송 완료: {hhmm}")
    except Exception as e:
        log(f"발송 실패: {e}")
    finally:
        conn.close()


# 2026.09.11 배치관리(TB_BATCH_MASTER) 연동 - 사용여부 체크 + 상태 보고
import batch_status

BATCH_NM = "stock_report_mail_batch.py"


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
