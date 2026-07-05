#!/usr/bin/env python3
"""
================================================================================
Daum Finance 국내 증시 데이터 크롤러
사이트: https://finance.daum.net/domestic

[수집 항목]
  - 코스피 / 코스닥 지수 (현재가, 전일대비, 등락률, 거래량, 거래대금)
  - 상승종목 TOP N  (KOSPI / KOSDAQ)
  - 하락종목 TOP N  (KOSPI / KOSDAQ)
  - 외국인순매수 TOP N
  - 외국인순매도 TOP N
  - 기관순매수   TOP N
  - 기관순매도   TOP N

[실제 사용 API]
  지수   : GET /api/domestic/trend/market/indexes
  상승/하락: GET /api/trend/price_performance
              ?market=KOSPI&changeType=RISE&page=1&perPage=10
  외국인  : GET /api/trend/investor_purchase
              ?market=KOSPI&investorType=FOREIGN&limit=10
             → 응답: { "data": { "BUY": [...], "SELL": [...] } }
  기관   : 동일, investorType=INSTITUTION

[DB 테이블]
  TB_MARKET_INDEX  : 코스피/코스닥 지수
  TB_STOCK_RANKING : 종목별 순위

[테이블 생성]
  mysql -u jsh77b1 -p jsh77b1 < create_tb_stock_monitor.sql

[실행 방법]
  python3 stock_monitor.py                       # 1회 수집 (TOP 10)
  python3 stock_monitor.py --top 20              # 1회 수집 (TOP 20)
  python3 stock_monitor.py --loop                # 10분 간격 자동 수집
  python3 stock_monitor.py --loop --interval 30  # 30분 간격
================================================================================
"""

import os
import sys
import glob
import time
import datetime
import requests
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# pymysql 로드 (없으면 /tmp에 자동 다운로드)
try:
    import pymysql
except ModuleNotFoundError:
    import urllib.request, io, tarfile
    _pkg = '/tmp/PyMySQL-1.1.0'
    if not os.path.exists(_pkg):
        _url = 'https://files.pythonhosted.org/packages/source/P/PyMySQL/PyMySQL-1.1.0.tar.gz'
        with urllib.request.urlopen(_url, timeout=15) as _r:
            with tarfile.open(fileobj=io.BytesIO(_r.read()), mode='r:gz') as _t:
                _t.extractall('/tmp/')
    sys.path.insert(0, _pkg)
    import pymysql


# ── 메일 설정 ─────────────────────────────────────────────────────────────────
SMTP_HOST  = "smtp.cafe24.com"
SMTP_PORT  = 465
SENDER     = "jsh77b@jsh77b1.cafe24.com"
MAIL_PASS  = os.getenv("STOCK_MAIL_PASS", "")
RECEIVER   = "ack1000hu@gmail.com"

# 리포트 발송 시각 (HH:MM)
REPORT_TIMES = {"10:00", "12:00", "14:00", "15:20"}

# 주문이력 평가손익 리포트 대상 유저
ORDER_HIST_REPORT_USER = "jsh77b@naver.com"


# ── DB 설정 ───────────────────────────────────────────────────────────────────
DB_HOST = "jsh77b1.cafe24app.com"
DB_USER = "jsh77b1"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b1"
DB_PORT = 3306


# ── 로그 설정 ─────────────────────────────────────────────────────────────────
LOG_DIR       = "/workspace/python01/log"
LOG_PREFIX    = "stock_"
LOG_KEEP_DAYS = 3


# ── 시장 운영 조건 ────────────────────────────────────────────────────────────
# KRX 공식 휴장일 — 음력 기반 공휴일(설날·추석·부처님오신날)은 매년 갱신 필요
MARKET_HOLIDAYS = {
    # 2025
    "2025-01-01",  # 신정
    "2025-01-28",  # 설날 연휴
    "2025-01-29",  # 설날
    "2025-01-30",  # 설날 연휴
    "2025-05-05",  # 어린이날·부처님오신날
    "2025-05-06",  # 대체휴일
    "2025-06-06",  # 현충일
    "2025-08-15",  # 광복절
    "2025-10-03",  # 개천절
    "2025-10-06",  # 추석
    "2025-10-07",  # 추석 연휴
    "2025-10-08",  # 추석 대체휴일
    "2025-10-09",  # 한글날
    "2025-12-25",  # 크리스마스
    "2025-12-31",  # 연말 휴장
    # 2026
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


def is_market_open() -> bool:
    """평일 09:00~15:30 이고 공휴일이 아닌 경우에만 True를 반환한다."""
    now = datetime.datetime.now()
    if now.weekday() >= 5:  # 토(5)·일(6)
        return False
    if now.strftime("%Y-%m-%d") in MARKET_HOLIDAYS:
        return False
    t = now.time()
    return datetime.time(9, 0) <= t <= datetime.time(15, 30, 59)


# ── API 설정 ──────────────────────────────────────────────────────────────────
BASE_URL = "https://finance.daum.net"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer":         "https://finance.daum.net/domestic",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ── 로그 정리 ─────────────────────────────────────────────────────────────────
def cleanup_logs():
    """LOG_KEEP_DAYS일보다 오래된 stock_YYYY-MM-DD.log 파일을 삭제한다."""
    cutoff = datetime.date.today() - datetime.timedelta(days=LOG_KEEP_DAYS)
    pattern = os.path.join(LOG_DIR, f"{LOG_PREFIX}????-??-??.log")
    for path in glob.glob(pattern):
        fname = os.path.basename(path)
        date_str = fname[len(LOG_PREFIX):-4]  # "YYYY-MM-DD"
        try:
            file_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        if file_date < cutoff:
            os.remove(path)
            print(f"  [로그] 삭제: {fname}")


# ── DB 연결 ───────────────────────────────────────────────────────────────────
def _db_connect():
    return pymysql.connect(
        host=DB_HOST
        , user=DB_USER
        , password=DB_PASS
        , database=DB_NAME
        , port=DB_PORT
        , charset='utf8'
        , connect_timeout=10
    )


# ── DB 저장 ───────────────────────────────────────────────────────────────────
def save_market_index(market_type: str, data: dict):
    """지수 데이터를 TB_MARKET_INDEX에 INSERT한다."""
    price     = data.get("tradePrice")
    chg_price = data.get("changePrice")
    # changeRate 없으므로 직접 계산: changePrice / prevClose
    if price and chg_price:
        prev  = float(price) - float(chg_price)
        c_rate = float(chg_price) / prev if prev != 0 else 0
    else:
        c_rate = None

    try:
        conn = _db_connect()
        cur  = conn.cursor()
        cur.execute("DELETE FROM TB_MARKET_INDEX WHERE MARKET_TYPE = %s", (market_type,))
        cur.execute("""
            INSERT INTO TB_MARKET_INDEX
            (MARKET_TYPE, CURRENT_PRICE, CHANGE_VAL, CHANGE_RATE,
             VOLUME, TRADE_VALUE, MARKET_CAP, REG_DT)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """, (
            market_type,
            price,
            chg_price,
            c_rate,
            data.get("accTradeVolume"),
            data.get("accTradePrice"),
            None,   # indexes API에 marketCap 없음
        ))
        conn.commit()
        conn.close()
        print("    → DB DELETE → INSERT 완료")
    except Exception as e:
        print(f"    → [DB 오류] {e}")


def _build_ranking_rows(category: str, market: str, stocks: list) -> list:
    """종목 리스트를 DB INSERT용 튜플 리스트로 변환한다."""
    rows = []
    for s in stocks:
        rows.append((
            category,
            market,
            s.get("rank"),
            s.get("symbolCode") or s.get("code"),
            s.get("name"),
            s.get("tradePrice"),
            s.get("changePrice"),
            s.get("changeRate"),
            s.get("accTradeVolume"),
            s.get("accTradePrice"),
            s.get("straightPurchasePrice"),
        ))
    return rows


INSERT_RANKING_SQL = """
    INSERT INTO {table}
    (CATEGORY, MARKET, RANK_NO, STOCK_CODE, STOCK_NAME,
     CURRENT_PRICE, CHANGE_VAL, CHANGE_RATE,
     VOLUME, TRADE_VALUE, NET_VALUE, REG_DT)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
"""


def save_stock_ranking(category: str, market: str, stocks: list, save_hist: bool = False):
    """종목 순위 데이터를 TB_STOCK_RANKING에 저장한다. save_hist=True이면 이력도 저장한다."""
    if not stocks:
        return
    stocks = stocks[:10]
    rows   = _build_ranking_rows(category, market, stocks)
    try:
        conn = _db_connect()
        cur  = conn.cursor()
        cur.execute(
            "DELETE FROM TB_STOCK_RANKING WHERE CATEGORY = %s AND MARKET = %s",
            (category, market)
        )
        cur.executemany(INSERT_RANKING_SQL.format(table="TB_STOCK_RANKING"), rows)
        if save_hist:
            cur.executemany(INSERT_RANKING_SQL.format(table="TB_STOCK_RANKING_HIST"), rows)
        conn.commit()
        conn.close()
        label = "최신 갱신 + 이력 저장" if save_hist else "최신 갱신"
        print(f"    → DB {label} 완료 ({len(stocks)}건)")
    except Exception as e:
        print(f"    → [DB 오류] {e}")


# ── API 호출 ──────────────────────────────────────────────────────────────────
def fetch_indexes() -> dict:
    """
    KOSPI / KOSDAQ 지수를 조회한다.
    GET /api/domestic/trend/market/indexes
    응답: { "KOSPI": [{tradePrice, changePrice, accTradeVolume, ...}], "KOSDAQ": [...] }
    """
    try:
        r = requests.get(
            BASE_URL + "/api/domestic/trend/market/indexes",
            headers=HEADERS, timeout=10
        )
        if r.status_code == 200:
            return r.json()
        print(f"    → HTTP {r.status_code}")
        return {}
    except Exception as e:
        print(f"    → 요청 실패: {e}")
        return {}


def fetch_price_performance(market: str, change_type: str, per_page: int = 10) -> list:
    """
    상승/하락 종목 순위를 조회한다.
    GET /api/trend/price_performance
        ?market=KOSPI&changeType=RISE&page=1&perPage=10
    응답: { "code": 200, "data": [{rank, name, symbolCode, tradePrice, changeRate, ...}] }
    """
    try:
        r = requests.get(
            BASE_URL + "/api/trend/price_performance",
            headers=HEADERS,
            params={
                "market":     market,
                "changeType": change_type,
                "page":       1,
                "perPage":    per_page,
            },
            timeout=10
        )
        if r.status_code == 200:
            return r.json().get("data", [])
        print(f"    → HTTP {r.status_code}")
        return []
    except Exception as e:
        print(f"    → 요청 실패: {e}")
        return []


def fetch_investor_purchase(market: str, investor_type: str, limit: int = 10) -> dict:
    """
    외국인 / 기관 순매수·순매도 종목을 조회한다.
    GET /api/trend/investor_purchase
        ?market=KOSPI&investorType=FOREIGN&limit=10
    응답: { "data": { "BUY": [{rank, name, straightPurchasePrice, ...}], "SELL": [...] } }
    """
    try:
        r = requests.get(
            BASE_URL + "/api/trend/investor_purchase",
            headers=HEADERS,
            params={
                "market":       market,
                "investorType": investor_type,
                "limit":        limit,
            },
            timeout=10
        )
        if r.status_code == 200:
            return r.json().get("data", {})
        print(f"    → HTTP {r.status_code}")
        return {}
    except Exception as e:
        print(f"    → 요청 실패: {e}")
        return {}


# ── 콘솔 출력 ─────────────────────────────────────────────────────────────────
def _pct(rate) -> str:
    if rate is None:
        return "  -.--%"
    v = float(rate) * 100
    return f"{v:+.2f}%"


def print_index(data: dict):
    price     = float(data.get("tradePrice")   or 0)
    chg_price = float(data.get("changePrice")  or 0)
    volume    = int(data.get("accTradeVolume") or 0)
    t_value   = float(data.get("accTradePrice") or 0)
    prev      = price - chg_price
    rate      = (chg_price / prev * 100) if prev != 0 else 0

    sign = "+" if chg_price >= 0 else ""
    print(f"    현재가  : {price:>12,.2f}")
    print(f"    전일대비: {sign}{chg_price:>10,.2f}  ({sign}{rate:.2f}%)")
    print(f"    거래량  : {volume:>15,} 천주")
    # indexes API의 accTradePrice 단위는 백만원
    print(f"    거래대금: {int(t_value) // 100:>10,} 억원")


def print_stocks(stocks: list, show_net: bool = False):
    for s in stocks:
        name  = s.get("name", "")
        price = int(s.get("tradePrice") or 0)
        rate  = _pct(s.get("changeRate"))
        net   = int(s.get("straightPurchasePrice") or 0)
        rank  = s.get("rank", "-")

        if show_net:
            print(f"    {rank:>2}. {name:<14}  {price:>9,}원  {rate}  순매수: {net // 100_000_000:>6,}억원")
        else:
            print(f"    {rank:>2}. {name:<14}  {price:>9,}원  {rate}")


# ── 리포트 ────────────────────────────────────────────────────────────────────
def query_common_stocks(category_a: str, category_b: str) -> list:
    """공통 종목의 10시/12시/14시 현재가를 피벗으로 조회한다."""
    try:
        conn = _db_connect()
        cur  = conn.cursor()
        cur.execute("""
            SELECT a.RANK_NO, a.STOCK_NAME,
                   MAX(CASE WHEN HOUR(a.REG_DT) = 10 THEN a.CURRENT_PRICE END) AS p10,
                   MAX(CASE WHEN HOUR(a.REG_DT) = 12 THEN a.CURRENT_PRICE END) AS p12,
                   MAX(CASE WHEN HOUR(a.REG_DT) = 14 THEN a.CURRENT_PRICE END) AS p14
            FROM TB_STOCK_RANKING_HIST a
            WHERE a.CATEGORY = %s
              AND DATE(a.REG_DT) = CURDATE()
              AND a.STOCK_CODE IN (
                  SELECT b.STOCK_CODE FROM TB_STOCK_RANKING_HIST b
                  WHERE b.CATEGORY = %s
                    AND DATE(b.REG_DT) = CURDATE()
                    AND HOUR(b.REG_DT) = HOUR(a.REG_DT)
              )
            GROUP BY a.STOCK_CODE, a.RANK_NO, a.STOCK_NAME
            ORDER BY MAX(ABS(a.NET_VALUE)) DESC
        """, (category_a, category_b))
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"  [리포트 DB 오류] {e}")
        return []


def query_market_index():
    """TB_MARKET_INDEX 에서 코스피/코스닥 지수를 조회한다."""
    try:
        conn = _db_connect()
        cur  = conn.cursor()
        cur.execute("""
            SELECT MARKET_TYPE, CURRENT_PRICE, CHANGE_VAL, CHANGE_RATE, REG_DT
            FROM   TB_MARKET_INDEX
            ORDER  BY FIELD(MARKET_TYPE, 'KOSPI', 'KOSDAQ')
        """)
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"  [지수 DB 오류] {e}")
        return []


def query_user_set_list():
    """TB_API_USER_SET 에서 전체 유저의 재무정보를 조회한다."""
    try:
        conn = _db_connect()
        cur  = conn.cursor()
        cur.execute("""
            SELECT REG_ID, TOT_INS_AMT, PRSM_DPST_ASET_AMT
            FROM   TB_API_USER_SET
            WHERE  DEL_YN = 'N'
            ORDER  BY REG_ID
        """)
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"  [유저정보 DB 오류] {e}")
        return []


def query_order_hist_profit(reg_id: str) -> list:
    """특정 유저의 미마감 주문이력을 종목별로 집계해 평가손익/손익율 계산용 원본을 조회한다."""
    try:
        conn = _db_connect()
        cur  = conn.cursor()
        cur.execute("""
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
        """, (reg_id,))
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"  [주문이력 DB 오류] {e}")
        return []


def send_report_email():
    """공통 매수·매도 리포트를 메일로 발송한다."""
    now_str  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    buy_rows        = query_common_stocks("FOR_BUY",  "INS_BUY")
    sell_rows       = query_common_stocks("FOR_SELL", "INS_SELL")
    user_rows       = query_user_set_list()
    index_rows      = query_market_index()
    order_hist_rows = query_order_hist_profit(ORDER_HIST_REPORT_USER)

    def fmt_price(val):
        return f"{int(val):,}" if val else "-"

    def make_order_hist_table(rows):
        if not rows:
            return "<p>&#xC8FC;&#xBB38;&#xC774;&#xB825; &#xC5C6;&#xC74C;</p>"  # 주문이력 없음
        html  = "<h3 style='color:#8e44ad;text-align:left;'>&#xC8FC;&#xBB38;&#xC774;&#xB825; &#xD3C9;&#xAC00;&#xC190;&#xC775;</h3>"
        html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:16px;width:480px;'>"
        html += ("<tr style='background:#f2f2f2;'>"
                 "<th style='text-align:center;width:180px;'>&#xC885;&#xBAA9;&#xBA85;</th>"
                 "<th style='text-align:center;width:150px;'>&#xD3C9;&#xAC00;&#xC190;&#xC775;</th>"
                 "<th style='text-align:center;width:100px;'>&#xC190;&#xC775;&#xC728;</th>"
                 "</tr>")
        for name, _code, buy_amt, buy_cnt, cur_price in rows:
            buy_amt_num   = float(buy_amt or 0)
            eval_amt_num  = float(cur_price or 0) * float(buy_cnt or 0)
            eval_profit   = eval_amt_num - buy_amt_num
            rate          = (eval_profit / buy_amt_num * 100) if buy_amt_num != 0 else 0
            color         = "#e74c3c" if eval_profit >= 0 else "#2980b9"
            html += (f"<tr>"
                     f"<td style='text-align:left;max-width:180px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px;'>{name or '-'}</td>"
                     f"<td style='text-align:right;color:{color};'>{fmt_price(eval_profit)}</td>"
                     f"<td style='text-align:right;color:{color};'>{rate:.2f}%</td>"
                     f"</tr>")
        html += "</table>"
        return html

    def make_table(rows, label):
        if not rows:
            return f"<p>{label} &#xC5C6;&#xC74C;</p>"
        color = "#27ae60" if "BUY" in label else "#e74c3c"
        html  = f"<h3 style='color:{color};text-align:left;'>{label}</h3>"
        html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:16px;width:480px;'>"
        html += ("<tr style='background:#f2f2f2;'>"
                 "<th style='text-align:center;width:50px;'>&#xC21C;&#xC704;</th>"
                 "<th style='text-align:center;width:160px;'>&#xC885;&#xBAA9;&#xBA85;</th>"
                 "<th style='text-align:center;width:150px;'>&#xD604;&#xC7AC;&#xAC00;</th>"
                 "<th style='text-align:center;width:80px;'>&#xC2DC;&#xAC04;</th>"
                 "</tr>")
        for rank, name, p10, p12, p14 in rows:
            times  = [("10", p10), ("12", p12), ("14", p14)]
            filled = [(t, p) for t, p in times if p]
            span   = len(filled) if filled else 1
            first  = True
            for t_label, price in (filled if filled else [("10", None)]):
                if first:
                    html += (f"<tr>"
                             f"<td rowspan='{span}' style='text-align:center;vertical-align:middle;'>{rank}</td>"
                             f"<td rowspan='{span}' style='text-align:left;max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;vertical-align:middle;'>{name}</td>"
                             f"<td style='text-align:right;'>{fmt_price(price)}</td>"
                             f"<td style='text-align:center;'>{t_label}</td>"
                             f"</tr>")
                    first = False
                else:
                    html += (f"<tr>"
                             f"<td style='text-align:right;'>{fmt_price(price)}</td>"
                             f"<td style='text-align:center;'>{t_label}</td>"
                             f"</tr>")
        html += "</table>"
        return html

    buy_label  = "&#xACF5;&#xD1B5; &#xC21C;&#xB9E4;&#xC218; &#xC885;&#xBAA9;"
    sell_label = "&#xACF5;&#xD1B5; &#xC21C;&#xB9E4;&#xB3C4; &#xC885;&#xBAA9;"

    def make_user_table(rows):
        if not rows:
            return "<p>&#xC720;&#xC800; &#xC815;&#xBCF4; &#xC5C6;&#xC74C;</p>"
        html  = "<h3 style='color:#2980b9;text-align:left;'>&#xC720;&#xC800;&#xBCC4; &#xC7AC;&#xBB34;&#xD604;&#xD669;</h3>"
        html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:16px;width:480px;'>"
        html += ("<tr style='background:#f2f2f2;'>"
                 "<th style='text-align:center;width:200px;'>&#xC720;&#xC800;ID</th>"
                 "<th style='text-align:center;width:150px;'>&#xC218;&#xC775;&#xAE08;&#xC561;</th>"
                 "<th style='text-align:center;width:130px;'>&#xC218;&#xC775;&#xC728;</th>"
                 "</tr>")
        for reg_id, ins_amt, prsm_amt in rows:
            ins_num   = float(ins_amt  or 0)
            prsm_num  = float(prsm_amt or 0)
            profit    = prsm_num - ins_num
            rate      = (profit / ins_num * 100) if ins_num != 0 else 0
            color     = "#e74c3c" if profit >= 0 else "#2980b9"
            html += (f"<tr>"
                     f"<td style='text-align:center;'>{reg_id or '-'}</td>"
                     f"<td style='text-align:right;color:{color};'>{fmt_price(profit)}</td>"
                     f"<td style='text-align:right;color:{color};'>{rate:.2f}%</td>"
                     f"</tr>")
        html += "</table>"
        return html

    def make_index_table(rows):
        if not rows:
            return "<p>&#xC2DC;&#xC7A5; &#xC9C0;&#xC218; &#xC5C6;&#xC74C;</p>"
        reg_dt_str = str(rows[0][4]) if rows and rows[0][4] else ""
        html  = f"<h3 style='color:#555;text-align:left;'>&#xC2DC;&#xC7A5; &#xC9C0;&#xC218; <span style='font-size:13px;color:#999;font-weight:normal;'>{reg_dt_str}</span></h3>"
        html += "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-size:16px;width:480px;'>"
        html += ("<tr style='background:#f2f2f2;'>"
                 "<th style='text-align:center;width:120px;'>&#xC9C0;&#xC218;&#xBA85;</th>"
                 "<th style='text-align:center;width:140px;'>&#xD604;&#xC7AC;&#xAC00;</th>"
                 "<th style='text-align:center;width:140px;'>&#xC804;&#xC77C;&#xB300;&#xBE44;</th>"
                 "<th style='text-align:center;width:80px;'>&#xB4F1;&#xB77D;&#xC728;</th>"
                 "</tr>")
        for market_type, cur_price, change_val, change_rate, _ in rows:
            chg = float(change_val or 0)
            color = "#e74c3c" if chg > 0 else "#2980b9" if chg < 0 else ""
            sign  = "+" if chg > 0 else ""
            rate  = float(change_rate or 0) * 100
            html += (f"<tr>"
                     f"<td style='text-align:center;'>{market_type}</td>"
                     f"<td style='text-align:right;color:{color};'>{fmt_price(cur_price)}</td>"
                     f"<td style='text-align:right;color:{color};'>{sign}{fmt_price(change_val)}</td>"
                     f"<td style='text-align:right;color:{color};'>{sign}{rate:.2f}%</td>"
                     f"</tr>")
        html += "</table>"
        return html

    body  = "<html><head><meta charset='utf-8'></head><body style='font-family:sans-serif;'>"
    body += f"<h2 style='text-align:left;'>&#xC99D;&#xC2DC; &#xB9AC;&#xD3EC;&#xD2B8; &mdash; {now_str}</h2>"
    body += make_index_table(index_rows)
    body += "<br>"
    body += make_user_table(user_rows)
    body += "<br>"
    body += make_order_hist_table(order_hist_rows)
    body += "<br>"
    body += make_table(buy_rows,  buy_label)
    body += "<br>"
    body += make_table(sell_rows, sell_label)
    body += "<p style='text-align:left;color:gray;font-size:12px;'>HOONE.NET &#xC790;&#xB3D9; &#xB9AC;&#xD3EC;&#xD2B8;</p>"
    body += "</body></html>"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Stock Report] {now_str}"
    msg["From"]    = SENDER
    msg["To"]      = RECEIVER
    msg.attach(MIMEText(body, "html", "utf-8"))

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        ctx.set_ciphers('ALL:@SECLEVEL=0')
        ctx.options |= 0x4
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as smtp:
            smtp.login(SENDER, MAIL_PASS)
            smtp.sendmail(SENDER, RECEIVER, msg.as_string())
        print(f"  [리포트] 메일 발송 완료 → {RECEIVER}")
    except Exception as e:
        print(f"  [리포트] 메일 발송 실패: {e}")


# ── 메인 실행 ─────────────────────────────────────────────────────────────────
def run_once(save_hist: bool = False):
    cleanup_logs()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  Daum Finance 국내 증시 데이터 수집{'  [이력 저장]' if save_hist else ''}")
    print(f"  수집 시각: {now}")
    print(f"{'='*60}")

    # 1. 지수 (KOSPI / KOSDAQ)
    idx_data = fetch_indexes()
    for market in ["KOSPI", "KOSDAQ"]:
        print(f"\n  [{market} 지수]")
        rows = idx_data.get(market, [])
        if rows:
            today_data = rows[0]
            print_index(today_data)
            save_market_index(market, today_data)
        else:
            print("    → 데이터 없음")
    time.sleep(0.5)

    # 2. 상승 / 하락 종목 (KOSPI + KOSDAQ 각각)
    for market in ["KOSPI", "KOSDAQ"]:
        for change_type, kor in [("RISE", "상승"), ("FALL", "하락")]:
            print(f"\n  [{market} {kor}종목]")
            stocks = fetch_price_performance(market, change_type, 10)
            if stocks:
                print_stocks(stocks)
                save_stock_ranking(change_type, market, stocks, save_hist)
            else:
                print("    → 데이터 없음")
            time.sleep(0.3)

    # 3. 외국인 / 기관 순매수·순매도 (KOSPI 기준)
    for investor_type, type_label in [("FOREIGN", "외국인"), ("INSTITUTION", "기관")]:
        print(f"\n  [{type_label} 순매수·순매도 (KOSPI)]")
        inv_data = fetch_investor_purchase("KOSPI", investor_type, limit=10)
        for side, side_label, cat in [
            ("BUY",  "순매수", f"{investor_type[:3]}_BUY"),
            ("SELL", "순매도", f"{investor_type[:3]}_SELL"),
        ]:
            stocks = inv_data.get(side, [])
            print(f"  ┌ {type_label} {side_label}")
            if stocks:
                print_stocks(stocks, show_net=True)
                save_stock_ranking(cat, "KOSPI", stocks, save_hist)
            else:
                print("    → 데이터 없음")
        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  수집 완료")
    print(f"{'='*60}\n")


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Daum Finance 국내 증시 크롤러",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python3 stock_monitor.py                       # 1회 수집 (TOP 10)
  python3 stock_monitor.py --top 20              # 1회 수집 (TOP 20)
  python3 stock_monitor.py --loop                # 10분 간격 자동 수집
  python3 stock_monitor.py --loop --interval 30  # 30분 간격 자동 수집
        """
    )
    parser.add_argument("--loop",     action="store_true", help="주기적 수집 모드")
    parser.add_argument("--interval", type=int, default=10, help="수집 간격(분), 기본값 10분")
    parser.add_argument("--top",      type=int, default=10, help="각 카테고리 수집 건수, 기본값 10")
    args = parser.parse_args()

    if args.loop:
        print(f"자동 수집 시작 — {args.interval}분 간격, 평일 09:00~15:30 (Ctrl+C 로 종료)")
        sent_times = set()  # 당일 발송 완료된 시각 기록
        last_date  = datetime.date.today()
        while True:
            try:
                now      = datetime.datetime.now()
                hhmm     = now.strftime("%H:%M")
                today    = now.date()

                # 날짜 바뀌면 발송 기록 초기화
                if today != last_date:
                    sent_times.clear()
                    last_date = today

                # 리포트 발송 + 이력 저장 (REPORT_TIMES 해당 시각, 중복 방지)
                is_report_time = hhmm in REPORT_TIMES and hhmm not in sent_times
                if is_report_time:
                    print(f"[{now:%Y-%m-%d %H:%M}] 리포트 발송 + 이력 저장 시작")
                    sent_times.add(hhmm)

                if is_market_open():
                    run_once(save_hist=is_report_time)
                    if is_report_time:
                        send_report_email()
                    next_time = now + datetime.timedelta(minutes=args.interval)
                    print(f"다음 수집: {args.interval}분 후 ({next_time:%H:%M})")
                    time.sleep(args.interval * 60)
                else:
                    print(f"[{now:%Y-%m-%d %H:%M}] 장 운영 시간 외 — 1분 후 재확인")
                    time.sleep(60)
            except KeyboardInterrupt:
                print("\n수집 종료.")
                sys.exit(0)
    else:
        if not is_market_open():
            now = datetime.datetime.now()
            print(f"[{now:%Y-%m-%d %H:%M}] 장 운영 시간 외 (평일 09:00~15:30만 동작) — 종료")
            sys.exit(0)

        now   = datetime.datetime.now()
        hhmm  = now.strftime("%H:%M")
        today = now.strftime("%Y-%m-%d")

        # 리포트 시각 여부 확인 + 플래그 파일로 중복 방지
        flag_file     = f"/tmp/stock_report_{today}_{hhmm.replace(':', '')}.sent"
        is_report_time = hhmm in REPORT_TIMES and not os.path.exists(flag_file)

        run_once(save_hist=is_report_time)

        if is_report_time:
            send_report_email()
            open(flag_file, "w").close()  # 발송 완료 플래그 생성
            print(f"  [리포트] 플래그 파일 생성: {flag_file}")
