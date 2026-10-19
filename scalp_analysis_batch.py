#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
단타 분석 배치 (1분 cron, 요청 있을 때만 실행)

목적
  - 감시목록 > 단타분석 화면에서 [분석요청] 하면 TB_SCALP_SET.REQ_STAT = AD_320_10(준비)
  - 이 배치가 요청 건을 AD_320_11(시작)로 선점 후 감시목록 전 종목 5분봉(ka10080)을 조회해
    종목별 단타 지표 + 사용자 조건(시가-N%/원 매수, +M%/원 익절, 15:15 정리) 백테스트 결과를
    TB_SCALP_RESULT 에 저장하고 AD_320_12(완료) / AD_320_14(실패) 처리

지표
  - DAY_RANGE   : 평균 일중변동폭%  (고가-저가)/저가
  - MIN5_RANGE  : 10시 이후 5분봉 평균 변동폭%
  - TRADE_AMT   : 일평균 거래대금(억)
  - PERIOD_RATE : 분석기간 첫날 시가 -> 마지막 종가 등락률%
  - 백테스트    : 09:30~14:30 사이 저가가 매수가 이하면 그 가격에 매수, 이후 고가가 익절가 이상이면 익절, 아니면 15:15 종가 정리
                  UNIT_GUBN=P(%) : 매수가 = 시가 x (1 - BUY_DROP_PER%),  익절가 = 매수가 x (1 + TAKE_PROFIT_PER%)
                  UNIT_GUBN=W(원) : 매수가 = 시가 - BUY_DROP_AMT원,       익절가 = 매수가 + TAKE_PROFIT_AMT원
                  수량 = BUY_AMT(매수금액) / 매수가, 손익금액(PROFIT_AMT) = 수량 x 차익 - 비용
                  비용 : ETF 0.03% (매매세 없음) / 주식 0.23% (수수료+매매세)

스윙 (2026.10.19 추가, ANAL_GUBN=S : 일봉(ka10081) 기준, 분석구분별로 설정/결과 따로 저장)
  - 보유 종목이 없는 날 저가가 매수가(시가 기준 하락) 이하면 매수
  - 다음 거래일부터 최대 HOLD_DAYS 거래일 동안 고가가 익절가 이상이면 익절 (시가가 이미 익절가 이상이면 시가)
  - 최대보유일까지 미도달이면 그날 종가 정리 (CUT_CNT), 분석 마지막까지 보유중인 건은 제외
  - HOLD_AVG : 평균보유일 / MAX_LOSS : 보유중 최저가 기준 최대물림%

실행방법
  python3 scalp_analysis_batch.py                 # 요청 건 처리
  python3 scalp_analysis_batch.py <REG_ID> [D|S]  # 요청 없이 해당 사용자 즉시 분석 (테스트용, 구분 생략시 전체)
================================================================================
"""

import sys
import time
from collections import defaultdict
from datetime import datetime

import requests

import avg_price_batch as b
import batch_status

BATCH_NM = "scalp_analysis_batch.py"

CHART_ENDPOINT = "/api/dostk/chart"
ETF_PREFIX = ("KODEX", "TIGER", "RISE", "HANARO", "ACE", "SOL", "KBSTAR", "PLUS", "KOSEF", "ARIRANG", "TIMEFOLIO", "WON", "1Q")
FEE_ETF = 0.03
FEE_STOCK = 0.23
CHART_PAGE = 2  # 1페이지 약 900봉(약 11거래일) -> 최대 약 22거래일


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def fetch_min5(user, stock_code, pages):
    """키움 5분봉(ka10080) 조회 -> [(일자, 시각, 시가, 고가, 저가, 종가, 거래량)]"""
    rows, cont_yn, next_key = [], "N", ""
    for _ in range(pages):
        data, response = {}, None
        for _retry in range(3):
            response = requests.post(
                b.KIWOOM_HOST + CHART_ENDPOINT,
                json={"stk_cd": stock_code, "tic_scope": "5", "upd_stkpc_tp": "1"},
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "authorization": f"{user['TOKEN_TYPE']} {user['TOKEN']}",
                    "cont-yn": cont_yn, "next-key": next_key, "api-id": "ka10080",
                },
                timeout=30,
            )
            data = response.json() or {}
            if str(data.get("return_code")) == "0":
                break
            time.sleep(1.5)  # 호출 제한 시 잠시 대기 후 재시도
        for x in data.get("stk_min_pole_chart_qry") or []:
            t = str(x.get("cntr_tm") or "")
            num = lambda k: abs(int(x.get(k) or 0))
            rows.append((t[:8], t[8:12], num("open_pric"), num("high_pric"), num("low_pric"), num("cur_prc"), int(x.get("trde_qty") or 0)))
        cont_yn = response.headers.get("cont-yn", "N")
        next_key = response.headers.get("next-key", "")
        time.sleep(0.4)
        if cont_yn != "Y" or not next_key:
            break
    return rows


def fetch_day(user, stock_code):
    """키움 일봉(ka10081) 조회 (1회 약 600거래일) -> [(일자, 시가, 고가, 저가, 종가, 거래대금(백만))] 일자 오름차순"""
    data = {}
    for _retry in range(3):
        response = requests.post(
            b.KIWOOM_HOST + CHART_ENDPOINT,
            json={"stk_cd": stock_code, "base_dt": datetime.now().strftime("%Y%m%d"), "upd_stkpc_tp": "1"},
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"{user['TOKEN_TYPE']} {user['TOKEN']}",
                "api-id": "ka10081",
            },
            timeout=30,
        )
        data = response.json() or {}
        if str(data.get("return_code")) == "0":
            break
        time.sleep(1.5)  # 호출 제한 시 잠시 대기 후 재시도
    rows = []
    for x in data.get("stk_dt_pole_chart_qry") or []:
        num = lambda k: abs(int(x.get(k) or 0))
        rows.append((str(x.get("dt") or ""), num("open_pric"), num("high_pric"), num("low_pric"), num("cur_prc"), num("trde_prica")))
    time.sleep(0.4)
    return sorted(r for r in rows if r[3] > 0)


def analyze(rows, days, unit, drop_val, profit_val, buy_amt, fee):
    """5분봉으로 단타 지표 + 백테스트 계산 (분석 불가 시 None)"""
    by_day = defaultdict(list)
    for d, t, o, h, lo, c, v in rows:
        if "0900" <= t <= "1515" and lo > 0:
            by_day[d].append((t, o, h, lo, c, v))
    day_list = sorted(by_day)[-days:]
    if len(day_list) < 5:
        return None

    day_range, trade_amt, mid_range, results, amounts = [], [], [], [], []
    for d in day_list:
        bars = sorted(by_day[d])
        high = max(x[2] for x in bars)
        low = min(x[3] for x in bars)
        day_range.append((high - low) / low * 100)
        trade_amt.append(sum(x[4] * x[5] for x in bars))
        mid_range += [(x[2] - x[3]) / x[3] * 100 for x in bars if x[0] >= "1000"]

        # 백테스트 : 시가 대비 drop_val(%/원) 하락 시 매수 -> profit_val(%/원) 상승 시 익절, 미도달 시 마지막 봉 종가 정리
        buy_price = bars[0][1] * (1 - drop_val / 100) if unit == "P" else bars[0][1] - drop_val
        qty = int(buy_amt // buy_price) if buy_price > 0 else 0
        if qty <= 0:
            continue
        sell_price = None
        for i, x in enumerate(bars):
            if "0930" <= x[0] < "1430" and x[3] <= buy_price:
                # 매수 이후 봉에서 익절가 도달 시 익절가, 아니면 마지막 봉 종가
                target = buy_price * (1 + profit_val / 100) if unit == "P" else buy_price + profit_val
                sell_price = target if any(y[2] >= target for y in bars[i + 1:]) else bars[-1][4]
                break
        if sell_price is not None:
            rate = (sell_price / buy_price - 1) * 100 - fee
            results.append(rate)
            amounts.append(qty * buy_price * rate / 100)

    first = sorted(by_day[day_list[0]])[0]
    last_bars = sorted(by_day[day_list[-1]])
    return {
        "DAY_CNT": len(day_list),
        "DAY_RANGE": round(sum(day_range) / len(day_range), 2),
        "MIN5_RANGE": round(sum(mid_range) / max(len(mid_range), 1), 3),
        "TRADE_AMT": round(sum(trade_amt) / len(trade_amt) / 1e8, 1),
        "PERIOD_RATE": round(last_bars[-1][4] / first[1] * 100 - 100, 2),
        "TRADE_CNT": len(results),
        "WIN_CNT": sum(1 for r in results if r > 0),
        "PROFIT_SUM": round(sum(results), 2),
        "PROFIT_AMT": int(round(sum(amounts))),
        "LAST_DT": day_list[-1],
        "LAST_OPEN": last_bars[0][1],
        "LAST_CLOSE": last_bars[-1][4],
    }


def analyze_swing(rows, days, unit, drop_val, profit_val, buy_amt, fee, hold_days):
    """일봉으로 스윙 백테스트 (분석 불가 시 None)"""
    if len(rows) < 20:
        return None
    start = max(0, len(rows) - days)
    bars = rows[start:]

    results, amounts, holds, losses, cut_cnt = [], [], [], [], 0
    i = start
    while i < len(rows):
        o, lo = rows[i][1], rows[i][3]
        buy_price = o * (1 - drop_val / 100) if unit == "P" else o - drop_val
        qty = int(buy_amt // buy_price) if buy_price > 0 else 0
        if qty <= 0 or lo > buy_price:
            i += 1
            continue
        # 매수일 다음 거래일부터 최대보유일까지 익절 확인, 보유중 최저가로 최대물림 계산
        target = buy_price * (1 + profit_val / 100) if unit == "P" else buy_price + profit_val
        min_low, sell_price, j = lo, None, i
        for j in range(i + 1, min(i + hold_days, len(rows) - 1) + 1):
            min_low = min(min_low, rows[j][3])
            if rows[j][2] >= target:
                sell_price = max(target, rows[j][1])
                break
        if sell_price is None:
            if i + hold_days > len(rows) - 1:
                break  # 분석 마지막까지 보유중 -> 제외
            sell_price = rows[j][4]
            cut_cnt += 1
        rate = (sell_price / buy_price - 1) * 100 - fee
        results.append(rate)
        amounts.append(qty * buy_price * rate / 100)
        holds.append(j - i)
        losses.append((min_low / buy_price - 1) * 100)
        i = j + 1

    return {
        "DAY_CNT": len(bars),
        "DAY_RANGE": round(sum((x[2] - x[3]) / x[3] * 100 for x in bars) / len(bars), 2),
        "MIN5_RANGE": 0,
        "TRADE_AMT": round(sum(x[5] for x in bars) / len(bars) / 100, 1),
        "PERIOD_RATE": round(bars[-1][4] / bars[0][1] * 100 - 100, 2) if bars[0][1] else 0,
        "TRADE_CNT": len(results),
        "WIN_CNT": sum(1 for r in results if r > 0),
        "PROFIT_SUM": round(sum(results), 2),
        "PROFIT_AMT": int(round(sum(amounts))),
        "HOLD_AVG": round(sum(holds) / len(holds), 1) if holds else 0,
        "MAX_LOSS": round(min(losses), 2) if losses else 0,
        "CUT_CNT": cut_cnt,
        "LAST_DT": bars[-1][0],
        "LAST_OPEN": bars[-1][1],
        "LAST_CLOSE": bars[-1][4],
    }


def run_user(conn, user, setting):
    """사용자 감시목록 전 종목 분석 후 결과 저장 -> (분석종목수, 제외종목수)"""
    cur = conn.cursor()
    reg_id = setting["REG_ID"]
    gubn = "S" if setting["ANAL_GUBN"] == "S" else "D"
    # 분석기간 : 단타 5~22일(5분봉 한계), 스윙 20~120일
    days = max(20, min(int(setting["ANAL_DAYS"]), 120)) if gubn == "S" else max(5, min(int(setting["ANAL_DAYS"]), 22))
    hold_days = max(1, min(int(setting["HOLD_DAYS"]), 30))
    unit = "W" if setting["UNIT_GUBN"] == "W" else "P"
    drop_val = float(setting["BUY_DROP_AMT"] if unit == "W" else setting["BUY_DROP_PER"])
    profit_val = float(setting["TAKE_PROFIT_AMT"] if unit == "W" else setting["TAKE_PROFIT_PER"])
    buy_amt = int(setting["BUY_AMT"])

    cur.execute(
        "SELECT M.JONGMOG_CD, IFNULL(J.JONGMOG_NM, '') JONGMOG_NM FROM TB_API_JONGMOG_MONITOR_YN M"
        " LEFT JOIN TB_API_JONGMOG J ON J.JONGMOG_CD = M.JONGMOG_CD"
        " WHERE M.REG_ID = %s AND M.DEL_YN = 'N' AND M.JONGMOG_CD != ''",
        (reg_id,),
    )
    stocks = cur.fetchall()
    pages = 1 if days <= 10 else CHART_PAGE

    results, skip = [], 0
    for s in stocks:
        code, name = s["JONGMOG_CD"], s["JONGMOG_NM"]
        fee = FEE_ETF if name.upper().startswith(ETF_PREFIX) else FEE_STOCK
        try:
            if gubn == "S":
                r = analyze_swing(fetch_day(user, code), days, unit, drop_val, profit_val, buy_amt, fee, hold_days)
            else:
                r = analyze(fetch_min5(user, code, pages), days, unit, drop_val, profit_val, buy_amt, fee)
        except Exception as e:
            log(f"  {code} {name} 조회 실패: {e}")
            r = None
        if r is None:
            skip += 1
            continue
        r.update({"JONGMOG_CD": code, "JONGMOG_NM": name, "FEE_RATE": fee})
        results.append(r)

    # 사용자 기존 결과(같은 분석구분) 삭제 후 재등록
    cur.execute("DELETE FROM TB_SCALP_RESULT WHERE REG_ID = %s AND ANAL_GUBN = %s", (reg_id, gubn))
    for r in results:
        cur.execute(
            "INSERT INTO TB_SCALP_RESULT (REG_ID, ANAL_GUBN, JONGMOG_CD, JONGMOG_NM, DAY_CNT, DAY_RANGE, MIN5_RANGE, TRADE_AMT, PERIOD_RATE,"
            " TRADE_CNT, WIN_CNT, PROFIT_SUM, PROFIT_AMT, FEE_RATE, HOLD_AVG, MAX_LOSS, CUT_CNT, LAST_DT, LAST_OPEN, LAST_CLOSE, REG_DT)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
            (reg_id, gubn, r["JONGMOG_CD"], r["JONGMOG_NM"], r["DAY_CNT"], r["DAY_RANGE"], r["MIN5_RANGE"], r["TRADE_AMT"],
             r["PERIOD_RATE"], r["TRADE_CNT"], r["WIN_CNT"], r["PROFIT_SUM"], r["PROFIT_AMT"], r["FEE_RATE"],
             r.get("HOLD_AVG", 0), r.get("MAX_LOSS", 0), r.get("CUT_CNT", 0), r["LAST_DT"], r["LAST_OPEN"], r["LAST_CLOSE"]),
        )
    conn.commit()
    return len(results), skip


def main():
    conn = b.get_connection()
    cur = conn.cursor()
    force_id = sys.argv[1] if len(sys.argv) > 1 else ""
    force_gubn = sys.argv[2] if len(sys.argv) > 2 else ""

    if force_id:
        cur.execute("SELECT * FROM TB_SCALP_SET WHERE REG_ID = %s AND (%s = '' OR ANAL_GUBN = %s)", (force_id, force_gubn, force_gubn))
    else:
        # 준비 건 + 시작 후 10분 넘게 멈춘 건(배치 중단 등) 재처리
        cur.execute("SELECT * FROM TB_SCALP_SET WHERE REQ_STAT = 'AD_320_10'"
                    " OR (REQ_STAT = 'AD_320_11' AND UPD_DT < NOW() - INTERVAL 10 MINUTE) ORDER BY REQ_DT")
    targets = cur.fetchall()
    if not targets:
        return

    # 배치관리(TB_BATCH_MASTER) 상태는 요청 건이 있을 때만 시작/완료 보고 (1분마다 빈 실행은 보고 안 함)
    batch_status.mark_start(BATCH_NM)
    fail_cnt = 0

    users = {u["REG_ID"]: u for u in b.get_active_users(conn)}
    for setting in targets:
        reg_id, gubn = setting["REG_ID"], setting["ANAL_GUBN"]
        gubn_nm = "스윙" if gubn == "S" else "단타"
        # 요청 선점 (중복 실행 방지)
        cur.execute("UPDATE TB_SCALP_SET SET REQ_STAT = 'AD_320_11', UPD_DT = NOW() WHERE REG_ID = %s AND ANAL_GUBN = %s"
                    + ("" if force_id else " AND REQ_STAT = %s AND UPD_DT = %s"), (reg_id, gubn) if force_id else (reg_id, gubn, setting["REQ_STAT"], setting["UPD_DT"]))
        conn.commit()
        if cur.rowcount != 1 and not force_id:
            continue

        if setting["UNIT_GUBN"] == "W":
            cond = f"시가-{setting['BUY_DROP_AMT']}원 매수 +{setting['TAKE_PROFIT_AMT']}원 익절"
        else:
            cond = f"시가-{setting['BUY_DROP_PER']}% 매수 +{setting['TAKE_PROFIT_PER']}% 익절"
        if gubn == "S":
            cond += f" 최대보유 {setting['HOLD_DAYS']}일"
        log(f"{gubn_nm}분석 시작 {reg_id} 기간 {setting['ANAL_DAYS']}일 {cond} 매수금액 {setting['BUY_AMT']}원")
        try:
            user = users.get(reg_id)
            if user is None:
                raise RuntimeError("키움 사용자 설정 없음")
            if user["REFRESH_YN"] == "Y":
                user = b.refresh_token(conn, user)
            cnt, skip = run_user(conn, user, setting)
            msg = f"분석완료 {cnt}종목" + (f" (데이터부족 제외 {skip}종목)" if skip else "")
            cur.execute("UPDATE TB_SCALP_SET SET REQ_STAT = 'AD_320_12', DONE_DT = NOW(), RESULT_MSG = %s, UPD_DT = NOW() WHERE REG_ID = %s AND ANAL_GUBN = %s",
                        (msg, reg_id, gubn))
            log(f"{gubn_nm}분석 완료 {reg_id} {msg}")
        except Exception as e:
            cur.execute("UPDATE TB_SCALP_SET SET REQ_STAT = 'AD_320_14', DONE_DT = NOW(), RESULT_MSG = %s, UPD_DT = NOW() WHERE REG_ID = %s AND ANAL_GUBN = %s",
                        (f"분석실패 {str(e)[:400]}", reg_id, gubn))
            log(f"{gubn_nm}분석 실패 {reg_id}: {e}")
            fail_cnt += 1
        conn.commit()
    if fail_cnt:
        batch_status.mark_failed(BATCH_NM, f"분석실패 {fail_cnt}건")
    else:
        batch_status.mark_done(BATCH_NM)


if __name__ == "__main__":
    # 배치관리 화면에서 미사용이면 실행 안 함
    if not batch_status.is_enabled(BATCH_NM):
        batch_status.log_skip(BATCH_NM)
        sys.exit(0)
    main()
