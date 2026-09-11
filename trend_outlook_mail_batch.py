#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Node(web.js::runTrendMailBatch, routes/stockAnalysis.js::analyzeTrend,
services/stockOutlookService.js)에 있던 "등락분석 일배치"를 파이썬으로 그대로 이관.

1) 감시목록(REG_ID='jsh77b@naver.com') 종목의 최근 30일 등락 패턴을 분석
2) 등락분석 결과를 HTML 메일로 발송 (제목: [등락분석] YYYY-MM-DD)
3) 신호종목(반등/하락/하락후반등/상승후하락/연속상승/연속하락)을 CLI AUTO에 등록
   (실제 AI 답변 생성은 cli_auto_batch.py(1분 cron)가 처리)

조건은 Node와 동일하게: 평일만, TB_HOLIDAY 조회로 공휴일 제외, 하루 1회.
(Node는 1분마다 도는 배치라 08:05~08:06 사이인지 확인 후 날짜로 중복실행을 막았지만,
 cron은 정해진 시각에 1회만 실행되므로 08:05 정각에 등록하고 휴장일만 체크한다.)

[실행방법 / cron 등록 예]
  STOCK_DB_PASS=xxx STOCK_MAIL_PASS=xxx python3 trend_outlook_mail_batch.py
  # 평일 08:05, crontab에 아래처럼 등록
  # 5 8 * * 1-5 /usr/bin/python3 /workspace/python01/trend_outlook_mail_batch.py >> /workspace/python01/log/trend_outlook_mail_$(date +\\%Y-\\%m-\\%d).log 2>&1
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

HOONE_USER_ID = "jsh77b@naver.com"
CLI_AUTO_PROJECT = "chat"
CLI_AUTO_REG_ID = "stockOutlookBatch"

# 신호구분 코드 -> 표시명 (stockOutlookService.js SIGNAL_LABELS와 동일)
SIGNAL_LABELS = {
    "REVERSAL":  "반등신호",
    "DECLINE":   "하락신호",
    "REBOUND":   "하락후반등",
    "REVERSE":   "상승후하락",
    "CONSEC_UP": "연속상승(5일+)",
    "CONSEC_DN": "연속하락(5일+)",
}


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_connection():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8", connect_timeout=10, cursorclass=pymysql.cursors.DictCursor,
    )


def is_holiday(conn, today_dash):
    """TB_HOLIDAY 조회 - Node의 holiday 체크와 동일 조건"""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS CNT FROM TB_HOLIDAY WHERE HOLIDAY_DT = %s", (today_dash,))
        row = cur.fetchone()
        return bool(row and row["CNT"] > 0)


# ------------------------------------------------------------------
# routes/stockAnalysis.js::analyzeTrend 포팅
# ------------------------------------------------------------------
def is_reversal_signal(row):
    return row["rate30"] < row["rate10"] < row["rate5"] < row["rate3"] and row["rate3"] > 0


def is_decline_signal(row):
    return row["rate30"] > row["rate10"] > row["rate5"] > row["rate3"] and row["rate3"] < 0


def analyze_trend(conn, hoone_user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.JONGMOG_CD, j.JONGMOG_NM, d.REG_DATE, d.CUR_PRICE_AVG
            FROM TB_API_CUR_PRICE_DAY d
            JOIN TB_API_JONGMOG j ON d.JONGMOG_CD = j.JONGMOG_CD
            JOIN TB_API_JONGMOG_MONITOR_YN m ON d.JONGMOG_CD = m.JONGMOG_CD
            WHERE m.DEL_YN = 'N'
              AND (%(uid)s IS NULL OR m.REG_ID = %(uid)s)
            ORDER BY d.JONGMOG_CD, d.REG_DATE ASC
            """,
            {"uid": hoone_user_id},
        )
        rows = cur.fetchall()

    # 종목별 그룹핑
    stocks = {}
    for r in rows:
        cd = r["JONGMOG_CD"]
        if cd not in stocks:
            stocks[cd] = {"nm": r["JONGMOG_NM"], "days": []}
        stocks[cd]["days"].append({"date": r["REG_DATE"], "avg": float(r["CUR_PRICE_AVG"])})

    results = []

    for cd, info in stocks.items():
        days = info["days"]
        if len(days) < 2:
            continue

        last = days[-1]
        prev = days[-2]

        # 현재 연속 streak 계산 (최신날부터 역방향)
        down_cnt = 0
        up_cnt = 0
        prev_streak_idx = -1
        i = len(days) - 1
        while i >= 1:
            diff = days[i]["avg"] - days[i - 1]["avg"]
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
            i -= 1

        # 직전 streak 계산
        prev_down_cnt = 0
        prev_up_cnt = 0
        if prev_streak_idx >= 1:
            j = prev_streak_idx
            while j >= 1:
                diff2 = days[j]["avg"] - days[j - 1]["avg"]
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
                j -= 1

        ref3 = days[-4] if len(days) >= 4 else days[0]
        ref5 = days[-6] if len(days) >= 6 else days[0]
        ref10 = days[-11] if len(days) >= 11 else days[0]
        rate1 = round((last["avg"] - prev["avg"]) / prev["avg"] * 100, 2)
        rate3 = round((last["avg"] - ref3["avg"]) / ref3["avg"] * 100, 2)
        rate5 = round((last["avg"] - ref5["avg"]) / ref5["avg"] * 100, 2)
        rate10 = round((last["avg"] - ref10["avg"]) / ref10["avg"] * 100, 2)

        # 최근 30거래일 상승일수/하락일수 및 등락률 집계
        up30_cnt = 0
        down30_cnt = 0
        window_size = min(30, len(days) - 1)
        for w in range(len(days) - 1, len(days) - 1 - window_size, -1):
            w_diff = days[w]["avg"] - days[w - 1]["avg"]
            if w_diff > 0:
                up30_cnt += 1
            elif w_diff < 0:
                down30_cnt += 1
        ref30 = days[len(days) - 1 - window_size]
        rate30 = round((last["avg"] - ref30["avg"]) / ref30["avg"] * 100, 2)

        results.append({
            "cd": cd,
            "nm": info["nm"],
            "lastDate": last["date"],
            "lastPrice": last["avg"],
            "downCnt": down_cnt,
            "upCnt": up_cnt,
            "prevDownCnt": prev_down_cnt,
            "prevUpCnt": prev_up_cnt,
            "rate1": rate1,
            "rate3": rate3,
            "rate5": rate5,
            "rate10": rate10,
            "up30Cnt": up30_cnt,
            "down30Cnt": down30_cnt,
            "rate30": rate30,
        })

    down_list = sorted(
        [r for r in results if r["downCnt"] > 0],
        key=lambda r: (-r["downCnt"], r["rate5"]),
    )
    up_list = sorted(
        [r for r in results if r["upCnt"] > 0],
        key=lambda r: (-r["upCnt"], -r["rate5"]),
    )

    return {"total": len(results), "down": down_list, "up": up_list, "all": results}


# ------------------------------------------------------------------
# routes/stockAnalysis.js::buildTrendMailHtml 포팅
# ------------------------------------------------------------------
def build_trend_mail_html(result, today_text, exchange_rates=None):
    TD_NM = 'align="left"   style="border:1px solid #ccc;padding:4px 8px;font-size:13px;text-align:left;"'
    TD = 'align="center" style="border:1px solid #ccc;padding:4px 8px;font-size:13px;text-align:center;"'
    TH = 'align="center" style="border:1px solid #ccc;padding:4px 8px;font-size:13px;background:#f2f2f2;text-align:center;"'
    TBL = 'width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #ccc;"'

    def short_nm(nm):
        return (nm[:8] + "...") if nm and len(nm) > 8 else nm

    def rate_html(val):
        color = "#d93025" if val > 0 else ("#1a73e8" if val < 0 else "#222")
        s = ("+" if val > 0 else "") + str(val) + "%"
        return f'<span style="color:{color};">{s}</span>'

    def table_html(rows, pattern_fn, empty_msg):
        if not rows:
            return f'<p style="color:#888;font-size:12px;">{empty_msg}</p>'
        body = "".join(
            f"<tr><td {TD_NM}>{short_nm(r['nm'])}</td><td {TD}>{pattern_fn(r)}</td>"
            f"<td {TD}>{rate_html(r['rate1'])}</td><td {TD}>{rate_html(r['rate5'])}</td></tr>"
            for r in rows
        )
        return f"<table {TBL}><tr><th {TH}>종목명</th><th {TH}>패턴</th><th {TH}>1일등락</th><th {TH}>5일등락</th></tr>{body}</table>"

    def reversal_table_html(rows, empty_msg):
        if not rows:
            return f'<p style="color:#888;font-size:12px;">{empty_msg}</p>'
        body = "".join(
            f"<tr><td {TD_NM}>{short_nm(r['nm'])}</td><td {TD}>{rate_html(r['rate30'])}</td>"
            f"<td {TD}>{rate_html(r['rate10'])}</td><td {TD}>{rate_html(r['rate5'])}</td>"
            f"<td {TD}>{rate_html(r['rate3'])}</td></tr>"
            for r in rows
        )
        return (f"<table {TBL}><tr><th {TH}>종목명</th><th {TH}>30일등락</th><th {TH}>10일등락</th>"
                f"<th {TH}>5일등락</th><th {TH}>3일등락</th></tr>{body}</table>")

    country_nm = {"USD": "달러 (USD)", "JPY": "엔화 (JPY)"}

    def exchange_html(rates):
        if not rates:
            return ""
        rows = "".join(
            f"<tr><td {TD_NM}>{country_nm.get(r['country'], r['country'])}</td>"
            f"<td {TD}>{r['rate']:,.2f}원</td>"
            f"<td {TD}>{rate_html(r['change_pct']) if r['change_pct'] is not None else '-'}</td></tr>"
            for r in rates
        )
        return (f'<h3 style="font-size:13px;margin:16px 0 4px;">■ 환율 ({rates[0]["reg_date"]} 기준)</h3>'
                f"<table {TBL}><tr><th {TH}>통화</th><th {TH}>환율</th><th {TH}>전일대비</th></tr>{rows}</table>")

    down_list = [r for r in result["down"] if r["downCnt"] >= 5]
    up_list = [r for r in result["up"] if r["upCnt"] >= 5]
    rebound_list = sorted(
        [r for r in result["up"] if r["prevDownCnt"] >= 5 and r["upCnt"] >= 1],
        key=lambda r: (-r["prevDownCnt"], -r["upCnt"]),
    )
    reverse_list = sorted(
        [r for r in result["down"] if r["prevUpCnt"] >= 5 and r["downCnt"] >= 1],
        key=lambda r: (-r["prevUpCnt"], -r["downCnt"]),
    )
    reversal_signal_list = sorted(
        [r for r in result["all"] if is_reversal_signal(r)],
        key=lambda r: -r["rate3"],
    )

    html = '<div style="font-family:sans-serif;font-size:13px;color:#222;">'
    html += f'<h2 style="font-size:15px;margin:0 0 4px;">[등락분석] {today_text}</h2>'
    html += f'<p style="margin:0 0 12px;">분석 종목: {result["total"]}개</p>'
    html += exchange_html(exchange_rates)

    html += f'<h3 style="font-size:13px;margin:16px 0 4px;">■ 연속하락 ({len(down_list)}개)</h3>'
    html += table_html(
        down_list,
        lambda r: (f"▲{r['prevUpCnt']}→▼{r['downCnt']}" if r["prevUpCnt"] > 0 else f"▼{r['downCnt']}일"),
        "해당 없음",
    )

    html += f'<h3 style="font-size:13px;margin:16px 0 4px;">■ 연속상승 ({len(up_list)}개)</h3>'
    html += table_html(
        up_list,
        lambda r: (f"▼{r['prevDownCnt']}→▲{r['upCnt']}" if r["prevDownCnt"] > 0 else f"▲{r['upCnt']}일"),
        "해당 없음",
    )

    html += f'<h3 style="font-size:13px;margin:16px 0 4px;">■ 하락후반등 ({len(rebound_list)}개)</h3>'
    html += table_html(rebound_list, lambda r: f"▼{r['prevDownCnt']}→▲{r['upCnt']}", "해당 없음")

    html += f'<h3 style="font-size:13px;margin:16px 0 4px;">■ 상승후하락 ({len(reverse_list)}개)</h3>'
    html += table_html(reverse_list, lambda r: f"▲{r['prevUpCnt']}→▼{r['downCnt']}", "해당 없음")

    html += f'<h3 style="font-size:13px;margin:16px 0 4px;">■ 반등신호 ({len(reversal_signal_list)}개)</h3>'
    html += reversal_table_html(reversal_signal_list, "해당 없음")

    html += "</div>"
    return html


def send_mail(subject, html):
    msg = MIMEText(html, "html", "utf-8")
    msg["From"] = MAIL_SENDER
    msg["To"] = MAIL_RECEIVER
    msg["Subject"] = Header(subject, "utf-8")

    ctx = ssl.create_default_context()

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as smtp:
        smtp.login(MAIL_SENDER, MAIL_PASS)
        smtp.sendmail(MAIL_SENDER, MAIL_RECEIVER, msg.as_string())


# ------------------------------------------------------------------
# services/stockOutlookService.js 포팅
# ------------------------------------------------------------------
def calc_rates(values):
    if not values or len(values) < 2:
        return None

    last = values[-1]
    prev = values[-2]
    ref3 = values[-4] if len(values) >= 4 else values[0]
    ref5 = values[-6] if len(values) >= 6 else values[0]
    ref10 = values[-11] if len(values) >= 11 else values[0]
    window_size = min(30, len(values) - 1)
    ref30 = values[len(values) - 1 - window_size]

    return {
        "rate1": round((last - prev) / prev * 100, 2),
        "rate3": round((last - ref3) / ref3 * 100, 2),
        "rate5": round((last - ref5) / ref5 * 100, 2),
        "rate10": round((last - ref10) / ref10 * 100, 2),
        "rate30": round((last - ref30) / ref30 * 100, 2),
    }


def fetch_index_rates(conn, reg_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT KOSPI_PRICE, KOSDAQ_PRICE FROM TB_STOCK_DAILY_SUMMARY "
            "WHERE REG_ID = %s ORDER BY SUMMARY_DATE ASC LIMIT 31",
            (reg_id,),
        )
        rows = cur.fetchall()

    kospi_values = [float(r["KOSPI_PRICE"]) for r in rows]
    kosdaq_values = [float(r["KOSDAQ_PRICE"]) for r in rows]

    return {"kospi": calc_rates(kospi_values), "kosdaq": calc_rates(kosdaq_values)}


def fetch_exchange_rates(conn):
    """국가(통화)별 최신 환율 + 전일 환율 조회 (TB_HANTO_EXCHANGE_LATE_DAY)"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNTRY, REG_DATE, MAX(EXCHANGE_LATE) AS EXCHANGE_LATE "
            "FROM TB_HANTO_EXCHANGE_LATE_DAY "
            "WHERE DEL_YN = 'N' "
            "AND   COUNTRY != '' "
            "AND   EXCHANGE_LATE > 0 "
            "GROUP BY COUNTRY, REG_DATE "
            "ORDER BY COUNTRY, REG_DATE DESC"
        )
        rows = cur.fetchall()

    by_country = {}
    for r in rows:
        by_country.setdefault(r["COUNTRY"], []).append(r)

    result = []
    for country, recs in by_country.items():
        latest = recs[0]
        prev = recs[1] if len(recs) > 1 else None
        rate = float(latest["EXCHANGE_LATE"])
        prev_rate = float(prev["EXCHANGE_LATE"]) if prev else None
        change_pct = round((rate - prev_rate) / prev_rate * 100, 2) if prev_rate else None
        result.append({
            "country": country,
            "reg_date": latest["REG_DATE"],
            "rate": rate,
            "change_pct": change_pct,
        })

    # 보기 좋게 USD, JPY 순으로, 그 외는 이름순
    order = {"USD": 0, "JPY": 1}
    result.sort(key=lambda x: (order.get(x["country"], 99), x["country"]))
    return result


def build_outlook_prompt(row, signal_type, index_info):
    signal_nm = SIGNAL_LABELS.get(signal_type, signal_type)
    lines = []

    lines.append(f'아래는 국내 주식 종목 "{row["nm"]}"({row["cd"]})의 최근 30일 등락 데이터다.')
    lines.append("")
    lines.append("[등락 패턴]")
    lines.append(f"- 1일 등락률: {row['rate1']}%")
    lines.append(f"- 3일 등락률: {row['rate3']}%")
    lines.append(f"- 5일 등락률: {row['rate5']}%")
    lines.append(f"- 10일 등락률: {row['rate10']}%")
    lines.append(f"- 30일 등락률: {row['rate30']}%")
    lines.append(f"- 최근 연속 상승일수: {row['upCnt']}일, 연속 하락일수: {row['downCnt']}일")
    lines.append(f"- 직전 연속 상승일수: {row['prevUpCnt']}일, 직전 연속 하락일수: {row['prevDownCnt']}일")
    lines.append(f"- 30일 중 상승일수: {row['up30Cnt']}일, 하락일수: {row['down30Cnt']}일")
    lines.append(f"- 신호 구분: {signal_nm}")

    has_index = index_info and index_info.get("kospi") and index_info.get("kosdaq")
    if has_index:
        k = index_info["kospi"]
        q = index_info["kosdaq"]
        lines.append("")
        lines.append("[같은 기간 코스피/코스닥 지수 등락률 - 시장 전체 흐름 참고용]")
        lines.append(f"- 코스피: 1일 {k['rate1']}%, 3일 {k['rate3']}%, 5일 {k['rate5']}%, 10일 {k['rate10']}%, 30일 {k['rate30']}%")
        lines.append(f"- 코스닥: 1일 {q['rate1']}%, 3일 {q['rate3']}%, 5일 {q['rate5']}%, 10일 {q['rate10']}%, 30일 {q['rate30']}%")

    lines.append("")
    lines.append("이 종목을 사거나 팔라고 추천하지 말고, 위 등락률 숫자 패턴만 놓고 다음 세 가지를 3~5문장으로 답해줘.")
    lines.append("1) 이 흐름이 단기 되돌림에 가까운지, 추세 전환 초입에 가까운지 숫자 근거로 해석")
    lines.append("2) 이 신호가 이어질지 무효화될지 판단하려면 앞으로 어떤 지표(예: rate3의 부호 유지 여부 등)를 지켜봐야 하는지")
    if has_index:
        lines.append("3) 종목의 등락률을 코스피/코스닥 지수 등락률과 비교했을 때, 시장 전체 흐름 대비 이 종목이 상대적으로 강한지 약한지(상대강도)")
    lines.append("뉴스나 재무 정보는 모른다는 전제로, 순수 등락률 패턴 해석에만 집중해줘.")
    lines.append("답변은 결과 텍스트만 출력하고, 인사말이나 작업 설명은 붙이지 마.")
    lines.append("문장이 끝날 때마다(마침표 뒤) 줄바꿈을 넣어서 한 줄에 한 문장씩 보이게 작성해줘.")

    return "\n".join(lines)


# 30일<10일<5일<3일 순서로 등락률이 개선/악화되는지로 반등/하락 신호를 판단 (stockOutlookService.js와 동일)
def collect_signal_candidates(trend_result):
    all_all = trend_result.get("all") or []
    all_down = trend_result.get("down") or []
    all_up = trend_result.get("up") or []

    reversal_list = [r for r in all_all if is_reversal_signal(r)]
    decline_list = [r for r in all_all if is_decline_signal(r)]
    rebound_list = [r for r in all_up if r["prevDownCnt"] >= 5 and r["upCnt"] >= 1]
    reverse_list = [r for r in all_down if r["prevUpCnt"] >= 5 and r["downCnt"] >= 1]
    consec_up_list = [r for r in all_up if r["upCnt"] >= 5]
    consec_dn_list = [r for r in all_down if r["downCnt"] >= 5]

    priority_groups = [
        (reversal_list, "REVERSAL"),
        (decline_list, "DECLINE"),
        (rebound_list, "REBOUND"),
        (reverse_list, "REVERSE"),
        (consec_up_list, "CONSEC_UP"),
        (consec_dn_list, "CONSEC_DN"),
    ]

    candidate_map = {}
    for group_list, signal_type in priority_groups:
        for row in group_list:
            if row["cd"] in candidate_map:
                continue  # 이미 더 높은 우선순위로 등록됨
            candidate_map[row["cd"]] = {"row": row, "signalType": signal_type}

    return list(candidate_map.values())


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


def register_signal_stock_outlooks(conn, trend_result, reg_date, reg_id):
    targets = collect_signal_candidates(trend_result)

    try:
        index_info = fetch_index_rates(conn, reg_id)
    except Exception as e:
        log(f"[STOCK_OUTLOOK] 지수 등락률 조회 실패: {e}")
        index_info = None

    registered = 0
    for t in targets:
        try:
            prompt = build_outlook_prompt(t["row"], t["signalType"], index_info)
            cli_auto_seq = insert_cli_auto(conn, prompt)
            insert_stock_outlook(conn, t["row"]["cd"], t["signalType"], cli_auto_seq, reg_date)
            conn.commit()
            registered += 1
        except Exception as e:
            conn.rollback()
            log(f"[STOCK_OUTLOOK] 등록 실패: {t['row'].get('cd')} - {e}")

    return {"total": len(targets), "registered": registered}


def main():
    if not DB_PASS:
        log("STOCK_DB_PASS 환경변수가 없습니다. 종료합니다.")
        sys.exit(1)
    if not MAIL_PASS:
        log("STOCK_MAIL_PASS 환경변수가 없습니다. 종료합니다.")
        sys.exit(1)

    today = date.today()

    # 주말 제외 (cron이 평일에만 실행하지만 Node 조건과 동일하게 이중 체크)
    if today.weekday() >= 5:
        log(f"주말({today.strftime('%Y-%m-%d')})이라 실행하지 않습니다.")
        sys.exit(0)

    today_dash = today.strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        # 공휴일 제외 (Node과 동일하게 TB_HOLIDAY 조회)
        if is_holiday(conn, today_dash):
            log(f"공휴일({today_dash})이라 실행하지 않습니다.")
            return

        result = analyze_trend(conn, HOONE_USER_ID)
        log(f"등락분석 완료: 분석종목 {result['total']}건")

        exchange_rates = fetch_exchange_rates(conn)
        log(f"환율정보 조회: {[(r['country'], r['rate']) for r in exchange_rates]}")

        html = build_trend_mail_html(result, today_dash, exchange_rates)
        try:
            send_mail(f"[등락분석] {today_dash}", html)
            log("메일 발송 완료")
        except Exception as e:
            log(f"메일 발송 실패: {e}")

        outlook_result = register_signal_stock_outlooks(conn, result, today, HOONE_USER_ID)
        log(f"신호종목 CLI AUTO 등록: {outlook_result['registered']}/{outlook_result['total']}")
    finally:
        conn.close()


# 2026.09.11 배치관리(TB_BATCH_MASTER) 연동 - 사용여부 체크 + 상태 보고
import batch_status

BATCH_NM = "trend_outlook_mail_batch.py"


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
