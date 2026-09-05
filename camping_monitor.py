#!/usr/bin/env python3
"""
================================================================================
땡큐캠핑 - 노을진캠핑장 예약 가능 여부 감시 스크립트
사이트: https://m.thankqcamping.com

[목적]
  땡큐캠핑 웹사이트의 예약 상세 API를 직접 호출하여,
  노을진캠핑장의 목요일 / 금요일 / 토요일 / 일요일 잔여 자리를 주기적으로 확인한다.

[캠핑장 정보]
  - 이름: 노을진캠핑장 (前 수도권매립지캠핑장)
  - 위치: 인천 서구 정서진로 500 (오류동)
  - 유형: 오토캠핑 / 카라반
  - campSeq(사이트 내부 고유번호): 16706

[사용 API]
  POST https://m.thankqcamping.com/resv/axResCampSite.hbb
  - 캠핑장 상세 페이지(view.hbb)에서 사이트 목록을 불러오는 API
  - 응답: HTML 형식 (JSON 아님)
  - 예약 가능 사이트: <div class="site_div">          (disabled 없음)
  - 예약 완료 사이트: <div class="site_div disabled">  (disabled 있음)
  - 날짜 파라미터 형식: YYYYMMDD (YYYY-MM-DD 형식은 서버 500 오류 발생)

[검증 이력]
  - ax_list_search.hbb의 ableCnt 필드는 날짜와 무관한 고정값(6) → 사용 중단
  - axResCampSite.hbb HTML 파싱으로 교체 (실제 예약 상세 페이지와 동일한 데이터)
  - 실제 확인 결과 토요일은 0자리(마감), ax_list_search는 모든 날짜에 6 반환

[실행 방법]
  # 1회 조회 (기본 4주)
  python3 camping_monitor.py

  # 8주 앞까지 조회
  python3 camping_monitor.py --weeks 8

  # 10분 간격 자동 감시 (Ctrl+C 로 종료)
  python3 camping_monitor.py --loop --interval 10
================================================================================
"""

import re          # HTML 파싱 (정규표현식)
import os
import sys         # 프로세스 종료 (sys.exit)
import ssl
import glob
import time        # 대기 (sleep)
import datetime    # 날짜/시간 계산
import smtplib
import requests    # HTTP 요청 라이브러리 (웹사이트 API 호출)
from email.mime.text import MIMEText

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


# ── DB 설정 ───────────────────────────────────────────────────────────────────

DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("CAMPING_DB_PASS", "")
DB_NAME = "jsh77b"
DB_PORT = 3306


# ── 메일 설정 ──────────────────────────────────────────────────────────────────

SMTP_HOST = "smtp.cafe24.com"
SMTP_PORT = 465
SENDER    = "jsh77b@jsh77b1.cafe24.com"
PASSWORD  = os.getenv("CAMPING_MAIL_PASS", "")
RECEIVER  = "ack1000hu@gmail.com"


# ── 캠핑장 설정 ────────────────────────────────────────────────────────────────

# 땡큐캠핑 내부에서 노을진캠핑장에 부여된 고유 식별번호
CAMP_SEQ = 16706

# 화면 출력용 캠핑장 이름
CAMP_NAME = "노을진캠핑장 (前 수도권매립지캠핑장)"

# 땡큐캠핑 모바일 도메인 (axResCampSite는 m. 서브도메인에서만 응답)
BASE_URL = "https://m.thankqcamping.com"

# 캠핑장 사이트 예약 현황 API 엔드포인트
# - POST 방식, HTML 응답
# - 캠핑장 상세 페이지의 getResCampSite() 함수에서 호출하는 실제 API
# - 날짜 파라미터 형식 주의: YYYYMMDD (하이픈 없음)
API_URL = f"{BASE_URL}/resv/axResCampSite.hbb"

# 사용자가 실제 예약을 진행하는 페이지 URL
BOOKING_URL = f"{BASE_URL}/resv/view.hbb?cseq={CAMP_SEQ}#booking_dt"

# 감시 대상 사이트 유형 (이 목록에 포함된 것만 출력 및 집계)
# 빈 리스트([])로 설정하면 전체 유형을 모두 표시
TARGET_SITES = ["파쇄석사이트", "데크사이트"]


# ── HTTP 요청 헤더 ──────────────────────────────────────────────────────────────
# 브라우저처럼 위장하여 서버의 봇 차단을 우회한다.
# - Referer: 실제 사용자가 상세 페이지에서 사이트 목록을 불러오는 것처럼 설정
# - X-Requested-With: jQuery AJAX 호출임을 서버에 알려 정상 응답을 유도
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": f"{BASE_URL}/resv/view.hbb?cseq={CAMP_SEQ}",  # 상세 페이지에서 온 것처럼
    "X-Requested-With": "XMLHttpRequest",                     # AJAX 요청 표시
    "Content-Type": "application/x-www-form-urlencoded",      # POST body 형식
}


# ── 날짜 / 요일 설정 ────────────────────────────────────────────────────────────

# Python datetime의 weekday() 반환값: 월=0, 화=1, 수=2, 목=3, 금=4, 토=5, 일=6
DAY_NAMES = {3: "목요일", 4: "금요일", 5: "토요일", 6: "일요일"}

# 감시 대상 요일 (목=3, 금=4, 토=5, 일=6)
TARGET_WEEKDAYS = [3, 4, 5, 6]


# ── 로그 설정 ──────────────────────────────────────────────────────────────────

LOG_DIR      = "/workspace/python01/log"
LOG_PREFIX   = "camping_"
LOG_KEEP_DAYS = 3


# ── 함수 정의 ──────────────────────────────────────────────────────────────────

def cleanup_logs():
    """LOG_KEEP_DAYS일보다 오래된 camping_YYYY-MM-DD.log 파일을 삭제한다."""
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


def send_mail(available_dates: list[dict]):
    """예약 가능 날짜가 발견됐을 때 메일로 알린다."""
    lines = ["노을진캠핑장 예약 가능 날짜가 발견되었습니다!\n"]

    print(f"  예약하기: {BOOKING_URL}")
    print(f"\n{'='*60}")

    for r in available_dates:
        spots = r.get("filtered_spots", r["total_spots"])
        lines.append(f" * {r['date']} ({r['day_name']})  잔여 {spots}자리")

        for s in r.get("sites", []):
            if not TARGET_SITES or s["name"] in TARGET_SITES:
                if s['able_spots'] > 0:
                    lines.append(f"    {s['name']}  {s['price']}  잔여 ({s['able_spots']})자리)")

        lines.append("\n")
    
    lines.append(f"\n예약하기: {BOOKING_URL}")
    lines.append("\n")

    body = "\n".join(lines)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[캠핑알림] 노을진캠핑장 예약 가능 {len(available_dates)}건"
    msg["From"]    = SENDER
    msg["To"]      = RECEIVER

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers('ALL:@SECLEVEL=0')
    ctx.options |= 0x4

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as smtp:
            smtp.login(SENDER, PASSWORD)
            smtp.sendmail(SENDER, RECEIVER, msg.as_string())
        print(f"  📧 메일 발송 완료 → {RECEIVER}")
    except Exception as e:
        print(f"  [메일 오류] {e}")


def _db_connect():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS,
        database=DB_NAME, port=DB_PORT, charset='utf8',
        connect_timeout=10
    )


def db_filter_new(available_dates: list[dict]) -> list[dict]:
    """KEY1·KEY2·KEY3 기준으로 이미 DB에 있는 항목을 제외하고 신규 건만 반환한다."""
    if not available_dates:
        return []
    try:
        conn = _db_connect()
        cur  = conn.cursor()
        new_dates = []
        for r in available_dates:
            key1 = r['date'].strftime('%Y-%m-%d')
            key2 = f"({r['day_name']})"
            key3 = f"잔여 {r.get('filtered_spots', r['total_spots'])}자리"
            cur.execute(
                "SELECT COUNT(*) FROM TB_CAMPING_MONITOR WHERE KEY1=%s AND KEY2=%s AND KEY3=%s AND REG_DT >= NOW() - INTERVAL 1 HOUR",
                (key1, key2, key3)
            )
            count = cur.fetchone()[0]
            if count == 0:
                new_dates.append(r)
            else:
                print(f"  [DB] 중복 건너뜀 (1시간 이내 등록) → {key1} {key2} {key3}")
        conn.close()
        return new_dates
    except Exception as e:
        print(f"  [DB 오류] 중복 체크 실패: {e}")
        return available_dates  # DB 오류 시 전체를 신규로 간주


def db_insert_available(available_dates: list[dict]):
    """예약 가능 날짜를 TB_CAMPING_MONITOR에 INSERT한다."""
    if not available_dates:
        return
    try:
        conn = _db_connect()
        cur  = conn.cursor()
        sql  = "INSERT INTO TB_CAMPING_MONITOR (KEY1, KEY2, KEY3, REG_DT, UPD_DT) VALUES (%s, %s, %s, NOW(), NOW())"
        for r in available_dates:
            key1 = r['date'].strftime('%Y-%m-%d')
            key2 = f"({r['day_name']})"
            key3 = f"잔여 {r.get('filtered_spots', r['total_spots'])}자리"
            cur.execute(sql, (key1, key2, key3))
            print(f"  [DB] INSERT 완료 → {key1} {key2} {key3}")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [DB 오류] INSERT 실패: {e}")


def get_next_target_dates(weeks_ahead: int = 2) -> list[datetime.date]:
    """
    오늘 기준으로 아직 체크인 가능한 이번 주 날짜 + weeks_ahead 주간의
    금/토/일 날짜 목록을 반환한다.

    - 월~금: 이번 주(또는 다음 주) 금요일부터 weeks_ahead 주간
    - 토요일 14시 이전: 오늘(토) + 내일(일) + 차주 weeks_ahead 주간 금/토/일
    - 토요일 14시 이후: 내일(일) + 차주 weeks_ahead 주간 금/토/일
    - 일요일 14시 이전: 오늘(일) + 차주 weeks_ahead 주간 금/토/일
    - 일요일 14시 이후: 차주 weeks_ahead 주간 금/토/일만
    """
    today   = datetime.date.today()
    now     = datetime.datetime.now()
    weekday = today.weekday()
    dates   = []

    if weekday == 5:  # 토요일
        if now.hour < 14:
            dates.append(today)                             # 오늘(토)
        dates.append(today + datetime.timedelta(days=1))    # 내일(일)
        next_friday = today + datetime.timedelta(days=6)    # 차주 금요일 # 2026.06.06 수정

        for week in range(weeks_ahead):
            friday = next_friday + datetime.timedelta(weeks=week)
            for offset in range(3): # 2026.06.06 수정
                dates.append(friday + datetime.timedelta(days=offset))

    elif weekday == 6:  # 일요일
        if now.hour < 14:
            dates.append(today)                              # 오늘(일)
        next_friday = today + datetime.timedelta(days=5)    # 차주 금요일
        for week in range(weeks_ahead):
            friday = next_friday + datetime.timedelta(weeks=week)
            for offset in range(3):
                dates.append(friday + datetime.timedelta(days=offset))

    else:  # 월~금
        days_to_friday = (4 - weekday) % 7
        if days_to_friday == 0 and now.hour >= 22:          # 금요일 22시 이후
            days_to_friday = 7
        start_friday = today + datetime.timedelta(days=days_to_friday)
        for week in range(weeks_ahead):
            friday = start_friday + datetime.timedelta(weeks=week)
            for offset in range(3):
                dates.append(friday + datetime.timedelta(days=offset))

    return dates


def check_availability(check_date: datetime.date, res_days: int = 1) -> dict | None:
    """
    axResCampSite API를 호출하여 특정 날짜의 노을진캠핑장 예약 가능 여부를 조회한다.

    [API 동작 방식]
      - POST /resv/axResCampSite.hbb
      - body 파라미터:
          campseq     : 캠핑장 고유번호 (16706)
          res_dt      : 체크인 날짜 (YYYYMMDD 형식, 하이픈 없음)
          res_edt     : 마지막 박(泊) 날짜 = 체크인 + (박수 - 1)  ← 체크아웃 날짜가 아님!
                        예) 금요일 1박: res_dt=금, res_edt=금 (같은 날)
                            금·토 2박: res_dt=금, res_edt=토 (다음 날)
          res_days    : 숙박 일수 (1박 = 1, 2박 = 2)
          site_tp     : 사이트 유형 필터 (빈 문자열 = 전체)
          only_able_yn: 예약 가능만 표시 여부 (빈 문자열 = 전체)

      - 응답: HTML 형식
          예약 가능:  <div class="site_div">           (disabled 없음)
          예약 가능:  <div class="site_div type2">      (disabled 없음)
          예약 완료:  <div class="site_div disabled">
          예약 완료:  <div class="site_div type2 disabled">

    [검증]
      resv.js 의 달력 선택 JS 로직:
        ndt.setDate(ndt.getDate() + s_days - 1);
        frm.res_edt.value = ndt  → 마지막 박 날짜 = check_in + (res_days - 1)

    Args:
        check_date: 체크인 날짜
        res_days  : 숙박 일수 (기본값 1박)

    Returns:
        조회 성공 시 dict, 실패 시 None
        dict 구조:
          - date       : 체크인 날짜 (datetime.date)
          - day_name   : 요일 이름 (예: "금요일")
          - res_days   : 숙박 일수
          - able_cnt   : 예약 가능한 사이트 수 (0이면 예약 불가)
          - booked_cnt : 예약 완료된 사이트 수
          - total_cnt  : 전체 사이트 수
    """
    # 마지막 박 날짜 = 체크인 + (박수 - 1)
    # 1박이면 체크인 당일, 2박이면 체크인 다음날
    last_night = check_date + datetime.timedelta(days=res_days - 1)

    # API 요청 body 파라미터
    # 날짜 형식: YYYYMMDD (하이픈 없음 — YYYY-MM-DD 로 보내면 서버 500 오류 발생)
    payload = {
        "campseq"           : CAMP_SEQ,
        "res_dt"            : check_date.strftime("%Y%m%d"),   # 체크인 날짜 (YYYYMMDD)
        "res_edt"           : last_night.strftime("%Y%m%d"),   # 마지막 박 날짜 (YYYYMMDD)
        "res_days"          : res_days,                        # 숙박 일수
        "site_tp"           : "",                              # 빈 문자열 = 전체 유형
        "only_able_yn"      : "",                              # 예약완료 포함 전체 표시
    }

    try:
        # POST 요청으로 사이트 예약 현황 API 호출
        r = requests.post(API_URL, data=payload, headers=HEADERS, timeout=10)
        r.encoding = "utf-8"

        if r.status_code != 200:
            print(f"  [오류] {check_date} HTTP {r.status_code}")
            return None

        html = r.text

        # site_div 단위로 블록 분리
        # HTML 구조: <div class="site_div ..."> ... </div></div>
        # - 외부 li는 style만 있는 것(<li style="...">) vs 내부 li는 class 있는 것(<li class="item">)
        # - <div class="site_div 로 split하면 사이트 유형별로 정확하게 분리됨
        raw_blocks = html.split('<div class="site_div')
        if len(raw_blocks) < 2:
            return None

        sites = []  # 사이트별 상세 정보 리스트
        for block in raw_blocks[1:]:  # 첫 번째는 사이트 블록 이전 내용이므로 skip
            # 예약 완료 여부: site_div class에 disabled 포함 여부
            # block 첫 줄에 class 속성이 이어짐: e.g. ' disabled" onClick=...'
            first_tag_end = block.find('>')
            class_part    = block[:first_tag_end]
            disabled      = "disabled" in class_part

            name_m  = re.search(r'<p class=["\']na["\'][^>]*>(.*?)</p>', block, re.DOTALL)
            price_m = re.search(r'<p class=["\']pri["\'][^>]*>(.*?)</p>', block, re.DOTALL)

            # q_tip 파싱:
            #   예약완료: <span class="q_tip">예약완료</span>
            #   예약가능: <span class="q_tip og">예약가능 <em>20</em></span>
            #   예약불가: <span class="q_tip red">예약불가</span>
            tip_m   = re.search(r'class="q_tip[^"]*">(.*?)</span>', block, re.DOTALL)
            count_m = re.search(r'<em>(\d+)</em>', block)  # 잔여 자리 수

            raw_price   = price_m.group(1) if price_m else "?"
            clean_price = re.sub(r'<[^>]+>', '', raw_price).strip()

            # 잔여 자리 수: 예약가능이면 <em>N</em>, 완료/불가면 0
            able_spots = int(count_m.group(1)) if (count_m and not disabled) else 0
            # tip 텍스트에서 태그 제거
            tip_text = re.sub(r'<[^>]+>', '', tip_m.group(1)).strip() if tip_m else ("예약가능" if not disabled else "예약완료")

            site_name = name_m.group(1).strip() if name_m else ""

            # 사이트명이 없는 블록은 HTML 이중 렌더링으로 생긴 중복 → 제외
            if not site_name:
                continue
            sites.append({
                "name"              : site_name,
                "price"             : clean_price,
                "status"            : tip_text,
                "available"         : not disabled,
                "able_spots"        : able_spots,  # 실제 잔여 자리 수 (예약가능 시)
            })

        # 전체 집계: 잔여 자리가 있는 유형 수 / 유형별 실제 잔여 합계
        able_cnt        = sum(1 for s in sites if s["available"])
        booked_cnt      = sum(1 for s in sites if not s["available"])
        total_cnt       = len(sites)
        total_spots     = sum(s["able_spots"] for s in sites)  # 전체 잔여 자리 합계

        return {
            "date"              : check_date,
            "day_name"          : DAY_NAMES.get(check_date.weekday(), ""),
            "res_days"          : res_days,     # 숙박 일수
            "able_cnt"          : able_cnt,     # 예약 가능 유형 수
            "booked_cnt"        : booked_cnt,   # 예약 완료 유형 수
            "total_cnt"         : total_cnt,    # 전체 유형 수
            "total_spots"       : total_spots,  # 전체 잔여 자리 합계
            "sites"             : sites,        # 사이트 유형별 상세 목록
        }

    except Exception as e:
        print(f"  [오류] {check_date} 조회 실패: {e}")
        return None


def format_result(result: dict) -> str:
    """
    check_availability() 의 반환값을 콘솔 출력용 문자열로 변환한다.
    사이트 유형별(데크/파쇄석/카라반A·B·C)로 예약 가능 여부를 분류해서 출력한다.
    """
    if result is None:
        return "  → 데이터 없음"

    days        = result["res_days"]                                # 숙박 일수
    date_str    = result["date"].strftime("%Y-%m-%d")               # 체크인 날짜 문자열
    day         = result["day_name"]                                # 요일 이름
    checkout    = result["date"] + datetime.timedelta(days=days)    # 체크아웃 날짜 계산
    all_sites   = result.get("sites", [])                           # 사이트별 상세 정보 리스트

    # TARGET_SITES 필터: 빈 리스트면 전체, 아니면 해당 이름만
    sites = [s for s in all_sites if not TARGET_SITES or s["name"] in TARGET_SITES]

    if not sites:
        return f"  {date_str}({day}) ~ {checkout} [{days}박]: → 해당 사이트 정보 없음"

    # 필터된 사이트 기준으로 집계
    able_cnt    = sum(1 for s in sites if s["available"])   # 예약 가능한 유형 수
    total_cnt   = len(sites)                                # 전체 유형 수  
    total_spots = sum(s["able_spots"] for s in sites)       # 전체 잔여 자리 합계

    if able_cnt > 0:
        header = f"✅ 예약 가능  (잔여 총 {total_spots}자리)"
    else:
        header = f"❌ 예약 불가"

    lines = [f"  {date_str}({day}) ~ {checkout} [{days}박]: {header}"]

    for s in sites:
        if s["available"]:
            mark   = "○"
            detail = f"{s['able_spots']}자리 남음"
        else:
            mark   = "×"
            detail = s["status"]
        lines.append(f"      {mark} {s['name']}  {s['price']}  ({detail})")

    return "\n".join(lines)


def run_once(weeks_ahead: int = 2) -> list[dict]:
    """
    1회 실행: 이번 주~N주 후 금/토/일 예약 현황을 조회하고 결과를 출력한다.
    """
    cleanup_logs()

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"\n{'='*60}")
    print(f"  땡큐캠핑 예약 현황 감시")
    print(f"  캠핑장: {CAMP_NAME}")
    print(f"  조회 시각: {now}")
    print(f"{'='*60}")

    dates = get_next_target_dates(weeks_ahead)
    available_dates = []
    prev_week = None

    for check_date in dates:
        week_num = check_date.isocalendar()[1]

        if prev_week != week_num:
            if prev_week is not None:
                print()

            # 해당 주의 금요일 기준으로 헤더 출력
            week_start = check_date - datetime.timedelta(days=check_date.weekday() - 4)
            print(f"  [{week_start.strftime('%Y년 %m월')} 주말]")
            prev_week = week_num

        # 각 날짜 1박 조회
        result = check_availability(check_date, res_days=1)

        print(format_result(result)) # 콘솔 출력용 문자열로 변환하여 출력

        # TARGET_SITES 기준으로 잔여 자리 계산 후 수집
        if result:
            filtered_spots = sum(
                s["able_spots"] for s in result.get("sites", [])
                if not TARGET_SITES or s["name"] in TARGET_SITES
            )

            if filtered_spots > 0:
                result["filtered_spots"] = filtered_spots
                available_dates.append(result)

        # API 과부하 방지를 위한 짧은 대기
        time.sleep(0.5)

    print(f"\n{'='*60}")

    if available_dates:
        print(f"  🎯 예약 가능 날짜 {len(available_dates)}건 발견!")

        # 예약가능자리
        for r in available_dates:     
            print(f"     → {r['date']} ({r['day_name']}) 잔여 {r.get('filtered_spots', r['total_spots'])}자리")
            for s in r.get("sites", []):
                if not TARGET_SITES or s["name"] in TARGET_SITES:
                    status = "예약 가능" if s["available"] else "예약 불가"
                    print(f"         - {s['name']}  {s['price']}  ({status}, 잔여 {s['able_spots']}자리)")

        print(f"  예약하기: {BOOKING_URL}")
        print(f"\n{'='*60}")

        new_dates = db_filter_new(available_dates)

        if new_dates:
            send_mail(new_dates)
            db_insert_available(new_dates)
        else:
            print("  [알림] 모두 이미 통보된 건 — 메일/DB 등록 생략")
    else:
        print("  현재 예약 가능한 금/토/일 날짜가 없습니다.")
    print(f"{'='*60}\n")

    return available_dates


def run_loop(interval_minutes: int = 10, weeks_ahead: int = 2):
    """
    주기적 감시 모드: interval_minutes 분마다 run_once()를 반복 실행한다.
    새로 예약 가능해진 날짜가 생기면 알림을 출력한다.
    Ctrl+C 로 종료.
    """
    print(f"감시 시작 - {interval_minutes}분 간격으로 체크합니다. (Ctrl+C 로 종료)")

    # 이전 조회에서 예약 가능했던 날짜 집합 — 차집합으로 신규 가능 날짜 감지
    prev_available = set()

    while True:
        try:
            available = run_once(weeks_ahead)
            current_available = {str(r["date"]) for r in available}

            # 이전 조회에 없었는데 이번에 새로 생긴 날짜
            newly_available = current_available - prev_available
            if newly_available:
                print(f"🔔 [알림] 새로 예약 가능해진 날짜: {', '.join(sorted(newly_available))}")
                print(f"   예약하기: {BOOKING_URL}")
                newly_list = [r for r in available if str(r["date"]) in newly_available]
                send_mail(newly_list)

            prev_available = current_available

            next_check = datetime.datetime.now() + datetime.timedelta(minutes=interval_minutes)
            print(f"다음 체크: {interval_minutes}분 후 ({next_check:%H:%M})")
            time.sleep(interval_minutes * 60)

        except KeyboardInterrupt:
            print("\n감시 종료.")
            sys.exit(0)


# ── 진입점 ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="땡큐캠핑 노을진캠핑장 예약 감시",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python3 camping_monitor.py               # 1회 조회 (2주)
  python3 camping_monitor.py --weeks 8     # 1회 조회 (8주)
  python3 camping_monitor.py --loop        # 10분 간격 자동 감시
  python3 camping_monitor.py --loop --interval 5 --weeks 6
        """
    )
    parser.add_argument("--loop", action="store_true",help="주기적 감시 모드 (지정하지 않으면 1회 실행 후 종료)")
    parser.add_argument("--interval", type=int, default=1,help="감시 간격(분), 기본값 1분 (--loop 모드에서만 사용)")
    parser.add_argument("--weeks", type=int, default=2,help="조회 기간(주), 기본값 2주")

    args = parser.parse_args()

    if args.loop:
        run_loop(interval_minutes=args.interval, weeks_ahead=args.weeks)
    else:
        run_once(weeks_ahead=args.weeks)
