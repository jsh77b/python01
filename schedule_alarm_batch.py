#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
web.js의 5분 배치(runBatchJob5)를 그대로 파이썬으로 이관.
TB_SCHEDULE에서 오늘 일정 중 알람 시각이 [현재-10분, 현재) 구간에 걸리는 건을
찾아 메일로 알린다. Node 기능을 다른 곳으로 이관하는 작업의 일환.

주의: Node 원본 쿼리가 REG_ID/USER_GROUP_IDX 컬럼을 SELECT하지 않아 항상
기본 수신자(MAIL_RECEIVER)로만 발송되던 기존 동작을 그대로 유지한다(동일 조건 이관).

[실행방법 / cron 등록 예]
  STOCK_DB_PASS=xxx STOCK_MAIL_PASS=xxx python3 schedule_alarm_batch.py
  # 5분마다(Node의 setInterval 5분 주기와 동일)
  # */5 * * * * /usr/bin/python3 /workspace/python01/schedule_alarm_batch.py >> /workspace/python01/log/schedule_alarm_$(date +\\%Y-\\%m-\\%d).log 2>&1
================================================================================
"""

import os
import ssl
import smtplib
import sys
from datetime import datetime
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

# web.js runBatchJob5와 동일한 SQL
SCHEDULE_ALARM_SQL = """
SELECT CONCAT(CURDATE(), ' ', TIME_FORMAT(STR_TO_DATE(SCHEDULE_ST_HHMM, '%H%i'),'%H:%i'), ' ', TITLE) AS TITLE
    , CASE WHEN STR_TO_DATE(TIME_FORMAT(SUBTIME(STR_TO_DATE(SCHEDULE_ST_HHMM, '%H%i'), SEC_TO_TIME(ALARM_HHMM * 60)), '%H%i'), '%H%i') >= DATE_FORMAT(DATE_ADD(NOW(), INTERVAL -10 MINUTE), '%H:%i:%s')
        AND STR_TO_DATE(TIME_FORMAT(SUBTIME(STR_TO_DATE(SCHEDULE_ST_HHMM, '%H%i'), SEC_TO_TIME(ALARM_HHMM * 60)), '%H%i'), '%H%i') < DATE_FORMAT(NOW(), '%H:%i:%s')
            THEN '알람'
            ELSE '없음'
      END ALARM
    , SCHEDULE_DT
    , SCHEDULE_ST_HHMM
    , ALARM_HHMM
FROM   TB_SCHEDULE
WHERE  1=1
AND    DEL_YN = 'N'
AND    SCHEDULE_DT >= CURDATE()
AND    SCHEDULE_DT <  CURDATE() + INTERVAL 1 DAY
"""


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10, cursorclass=pymysql.cursors.DictCursor,
    )


def send_mail(subject, text):
    msg = MIMEText(text, "plain", "utf-8")
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

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEDULE_ALARM_SQL)
            rows = cur.fetchall()

        for row in rows:
            if row["ALARM"] != "알람":
                continue
            try:
                title = row["TITLE"]
                send_mail(f"[스케줄 알림] {title}", f"스케줄 알림\n\n{title}")
                log(f"발송 완료: {title}")
            except Exception as e:
                log(f"발송 실패: {row.get('TITLE')} - {e}")
    finally:
        conn.close()


# 2026.09.11 배치관리(TB_BATCH_MASTER) 연동 - 사용여부 체크 + 상태 보고
import batch_status

BATCH_NM = "schedule_alarm_batch.py"


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
