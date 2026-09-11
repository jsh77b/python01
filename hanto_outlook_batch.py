#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
한투(해외/미국장) 등락분석 신호종목을 CLI AUTO에 등록하는 배치.
trend_outlook_mail_batch.py(키움 30일 등락분석)와 동일한 패턴이며, 다음 차이가 있다.

- 미국장 특성상 하루 기준을 22시~다음날 8시로 계산 (hanto_updown_analysis.php와 동일)
- 코스피/코스닥 지수 비교는 하지 않는다 (미국 종목이라 국내 지수와 비교 의미 없음)
- 메일 발송은 하지 않는다 (CLI AUTO 등록만)

실제 AI 답변 생성은 cli_auto_batch.py(1분 cron)가 처리하고,
결과는 hanto_updown_analysis.php의 "전망보기" 버튼으로 노출된다.

[실행방법 / cron 등록 예]
  STOCK_DB_PASS=xxx python3 hanto_outlook_batch.py
  # 매일 08:10 (한투 22시~08시 세션 마감 직후)
  # 10 8 * * * /usr/bin/python3 /workspace/python01/hanto_outlook_batch.py >> /workspace/python01/log/hanto_outlook_$(date +\\%Y-\\%m-\\%d).log 2>&1
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

HOONE_USER_ID = "jsh77b@naver.com"
CLI_AUTO_PROJECT = "chat"
CLI_AUTO_REG_ID = "hantoOutlookBatch"

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


def is_reversal_signal(row):
    return row["rate30"] < row["rate10"] < row["rate5"] < row["rate3"] and row["rate3"] > 0


def is_decline_signal(row):
    return row["rate30"] > row["rate10"] > row["rate5"] > row["rate3"] and row["rate3"] < 0


# hanto_updown_analysis.php::fn_analyzeHantoTrend / class.hanto.php::getHantoUpdownAnalysisDayList 포팅
def analyze_hanto_trend(conn, hoone_user_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT D.JONGMOG_CD, J.JONGMOG_NM, D.REG_DATE, D.CUR_PRICE_AVG
            FROM (
                SELECT JONGMOG_CD
                     , DATE_FORMAT(DATE_SUB(REG_DT, INTERVAL 22 HOUR), '%%Y-%%m-%%d') AS REG_DATE
                     , ROUND(AVG(ABS(CUR_PRICE)),2) AS CUR_PRICE_AVG
                FROM TB_HANTO_CUR_PRICE
                WHERE TRDE_QTY > 0
                GROUP BY JONGMOG_CD, DATE_FORMAT(DATE_SUB(REG_DT, INTERVAL 22 HOUR), '%%Y-%%m-%%d')
            ) D
            JOIN TB_HANTO_JONGMOG J ON D.JONGMOG_CD = J.JONGMOG_CD
            JOIN TB_HANTO_MONITOR_SET M ON D.JONGMOG_CD = M.JONGMOG_CD
            WHERE M.DEL_YN = 'N'
              AND (%(uid)s IS NULL OR M.REG_ID = %(uid)s)
            ORDER BY D.JONGMOG_CD, D.REG_DATE ASC
            """,
            {"uid": hoone_user_id},
        )
        rows = cur.fetchall()

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

    down_list = [r for r in results if r["downCnt"] > 0]
    up_list = [r for r in results if r["upCnt"] > 0]

    return {"total": len(results), "down": down_list, "up": up_list, "all": results}


# stockOutlookService.js::collectSignalCandidates 포팅
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
                continue
            candidate_map[row["cd"]] = {"row": row, "signalType": signal_type}

    return list(candidate_map.values())


# stockOutlookService.js::buildOutlookPrompt 포팅 (코스피/코스닥 비교 부분만 제외)
def build_outlook_prompt(row, signal_type):
    signal_nm = SIGNAL_LABELS.get(signal_type, signal_type)
    lines = []

    lines.append(f'아래는 미국 상장 종목 "{row["nm"]}"({row["cd"]})의 최근 30 거래일(한국시간 22시~다음날 8시를 하루 기준) 등락 데이터다.')
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
    lines.append("")
    lines.append("이 종목을 사거나 팔라고 추천하지 말고, 위 등락률 숫자 패턴만 놓고 다음 두 가지를 3~4문장으로 답해줘.")
    lines.append("1) 이 흐름이 단기 되돌림에 가까운지, 추세 전환 초입에 가까운지 숫자 근거로 해석")
    lines.append("2) 이 신호가 이어질지 무효화될지 판단하려면 앞으로 어떤 지표(예: rate3의 부호 유지 여부 등)를 지켜봐야 하는지")
    lines.append("뉴스나 재무 정보는 모른다는 전제로, 순수 등락률 패턴 해석에만 집중해줘.")
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


def register_signal_stock_outlooks(conn, trend_result, reg_date):
    targets = collect_signal_candidates(trend_result)

    registered = 0
    for t in targets:
        try:
            prompt = build_outlook_prompt(t["row"], t["signalType"])
            cli_auto_seq = insert_cli_auto(conn, prompt)
            insert_stock_outlook(conn, t["row"]["cd"], t["signalType"], cli_auto_seq, reg_date)
            conn.commit()
            registered += 1
        except Exception as e:
            conn.rollback()
            log(f"등록 실패: {t['row'].get('cd')} - {e}")

    return {"total": len(targets), "registered": registered}


def main():
    if not DB_PASS:
        log("STOCK_DB_PASS 환경변수가 없습니다. 종료합니다.")
        sys.exit(1)

    today = date.today()

    # 미국장도 주말은 휴장이라 이중 체크 (공휴일 캘린더까지는 반영 안 함)
    if today.weekday() >= 5:
        log(f"주말({today.strftime('%Y-%m-%d')})이라 실행하지 않습니다.")
        sys.exit(0)

    conn = get_connection()
    try:
        result = analyze_hanto_trend(conn, HOONE_USER_ID)
        log(f"한투 등락분석 완료: 분석종목 {result['total']}건")

        outlook_result = register_signal_stock_outlooks(conn, result, today)
        log(f"신호종목 CLI AUTO 등록: {outlook_result['registered']}/{outlook_result['total']}")
    finally:
        conn.close()


# 2026.09.11 배치관리(TB_BATCH_MASTER) 연동 - 사용여부 체크 + 상태 보고
import batch_status

BATCH_NM = "hanto_outlook_batch.py"


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
