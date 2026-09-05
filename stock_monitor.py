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
REPORT_TIMES = {"09:30", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "15:30"}

# 서킷브레이커 단계별 발동 기준 (전일대비 %, 단계) — 큰 낙폭부터 확인
CIRCUIT_BREAKER_LEVELS = [(-20.0, 3), (-15.0, 2), (-8.0, 1)]


# ── DB 설정 ───────────────────────────────────────────────────────────────────
DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("STOCK_DB_PASS", "")
DB_NAME = "jsh77b"
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


# ── 알림 메일 ──────────────────────────────────────────────────────────────────
# 지정시각 증시 리포트 메일은 cafe24_jsh77b1/services/stockReportMail.js 로 이전됨
def send_alert_email(subject: str, message: str):
    """서킷브레이커 등 긴급 상황을 알리는 단순 텍스트 메일을 발송한다."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER
    msg["To"]      = RECEIVER
    msg.attach(MIMEText(message, "plain", "utf-8"))

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        ctx.set_ciphers('ALL:@SECLEVEL=0')
        ctx.options |= 0x4
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as smtp:
            smtp.login(SENDER, MAIL_PASS)
            smtp.sendmail(SENDER, RECEIVER, msg.as_string())
        print(f"  [알림] 메일 발송 완료 → {RECEIVER} ({subject})")
    except Exception as e:
        print(f"  [알림] 메일 발송 실패: {e}")


def check_circuit_breaker(market: str, rate: float):
    """
    서킷브레이커 발동 여부를 확인하고, 당일 처음 감지된 단계면 메일로 알린다.
    10분 주기 수집이라 정확한 발동 순간이 아닌 '기준 충족 감지' 시점 기준이다.
    """
    today = datetime.date.today().isoformat()

    for threshold, level in CIRCUIT_BREAKER_LEVELS:
        if rate > threshold:
            continue

        flag_file = f"/tmp/circuit_breaker_{market}_{level}_{today}.flag"
        if os.path.exists(flag_file):
            break  # 이미 오늘 해당 단계 알림 발송함

        subject = f"[서킷브레이커 발동] {market} {level}단계 감지 ({rate:+.2f}%)"
        message = (
            f"{market} 지수가 전일 대비 {rate:+.2f}% 하락하여 "
            f"서킷브레이커 {level}단계 기준({threshold:.0f}%)을 충족한 것으로 감지되었습니다.\n\n"
            f"※ 10분 주기 감시 기준 감지이며, 정확한 공식 발동/해제 시각은 KRX 공지를 참고하세요."
        )
        send_alert_email(subject, message)
        open(flag_file, "w").close()
        break  # 가장 높은(심한) 단계 하나만 알림


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

            price = float(today_data.get("tradePrice")  or 0)
            chg   = float(today_data.get("changePrice") or 0)
            prev  = price - chg
            rate  = (chg / prev * 100) if prev != 0 else 0
            check_circuit_breaker(market, rate)
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

                # 이력 저장 시각 여부 (REPORT_TIMES 해당 시각, 중복 방지) — 리포트 메일 발송은 Node로 이전됨
                is_report_time = hhmm in REPORT_TIMES and hhmm not in sent_times
                if is_report_time:
                    print(f"[{now:%Y-%m-%d %H:%M}] 이력 저장 시작")
                    sent_times.add(hhmm)

                if is_market_open():
                    run_once(save_hist=is_report_time)
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

        # 이력 저장 시각 여부 확인 + 플래그 파일로 중복 방지 (리포트 메일 발송은 Node로 이전됨)
        flag_file     = f"/tmp/stock_report_{today}_{hhmm.replace(':', '')}.sent"
        is_report_time = hhmm in REPORT_TIMES and not os.path.exists(flag_file)

        run_once(save_hist=is_report_time)

        if is_report_time:
            open(flag_file, "w").close()  # 이력 저장 완료 플래그 생성
            print(f"  [이력] 플래그 파일 생성: {flag_file}")
