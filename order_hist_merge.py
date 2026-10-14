#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
감시목록 주문이력(TB_API_ORDER_HIST) 종목당 1ROW 통합 스크립트 (수동 실행용)

목적
  - 계정별 감시목록 종목의 미청산 주문이력(완료/미마감) 여러 ROW 를
    키움 실잔고(kt00004) 수량/평단가 기준 1ROW 로 최신화한다.
  - 2026-10-14 etf@email.com 대상 최초 수행 (256건 -> 16건)

처리 순서
  1. 키움 계좌평가현황(kt00004) 조회 -> 종목별 보유수량/평단가
  2. DB 미청산 주문이력과 대조 결과 출력 (기본: 여기까지만 = 미리보기)
  3. --exec 지정 시
     - 대상 원본 ROW 를 백업테이블 TB_API_ORDER_HIST_BAK_YYYYMMDD 에 저장
     - 종목당 통합 1ROW INSERT (BUY_PRICE=BASIC_PRICE=키움평단가, BUY_CNT=키움수량,
       ORDER_STAT=AD_240_11, CLOSE_YN=N, REG_DT=해당종목 마지막 매수일)
     - 기존 ROW 는 CLOSE_YN=Y, DEL_YN=Y, ORDER_STAT=AD_240_13(취소), CLOSE_DT=NULL
       (이력은 남기되 수익통계/최근매도 재매수차단 판단에서 제외되도록)
       RESULT_MSG 앞에 '[1ROW통합->IDX 신규IDX]' 추가

스킵 조건
  - 키움 잔고 없음(0주) / 미체결(AD_240_10) 주문 존재 / 이미 1ROW 이고 수량·평단 일치

실행방법
  STOCK_DB_PASS=xxx python3 order_hist_merge.py etf@email.com          # 미리보기
  STOCK_DB_PASS=xxx python3 order_hist_merge.py etf@email.com --exec   # 실제 반영

복구
  백업테이블 IDX 기준으로 원본 ROW 상태 복원 후 통합 ROW(RESULT_MSG '주문이력 1ROW 통합%') 삭제
================================================================================
"""

import sys
from datetime import datetime

import requests

import avg_price_batch as b


def fetch_holdings(user):
    """계좌평가현황(kt00004) 조회 -> {종목코드: (보유수량, 평단가)}"""
    holdings, cont_yn, next_key = {}, "N", ""
    for _ in range(b.MAX_PAGE):
        response = requests.post(
            b.KIWOOM_HOST + b.ACCOUNT_ENDPOINT,
            json={"qry_tp": "0", "dmst_stex_tp": "KRX"},
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"{user['TOKEN_TYPE']} {user['TOKEN']}",
                "cont-yn": cont_yn, "next-key": next_key, "api-id": "kt00004",
            },
            timeout=30,
        )
        data = response.json() or {}
        if str(data.get("return_code")) != "0":
            raise Exception(f"계좌평가현황 조회 실패: {data.get('return_msg')}")
        for row in data.get("stk_acnt_evlt_prst") or []:
            holdings[b.normalize_stock_code(row.get("stk_cd"))] = (int(row.get("rmnd_qty") or 0), int(row.get("avg_prc") or 0))
        cont_yn = response.headers.get("cont-yn", "N")
        next_key = response.headers.get("next-key", "")
        if cont_yn != "Y" or not next_key:
            break
    return holdings


# 미청산 주문이력 조건 / 감시목록 종목 조건
COND = "REG_ID=%s AND ORDER_STAT='AD_240_11' AND CLOSE_YN='N' AND DEL_YN='N'"
MON = ("JONGMOG_CD IN (SELECT JONGMOG_CD FROM TB_API_JONGMOG_MONITOR_YN "
       "WHERE REG_ID=%s AND DEL_YN='N' AND JONGMOG_CD!='')")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("사용법: python3 order_hist_merge.py <REG_ID> [--exec]")
        sys.exit(1)
    reg_id = args[0]
    do_exec = "--exec" in sys.argv
    today = datetime.now().strftime("%Y%m%d")
    bak = f"TB_API_ORDER_HIST_BAK_{today}"

    conn = b.get_connection()
    cur = conn.cursor()

    users = [u for u in b.get_active_users(conn) if u["REG_ID"] == reg_id]
    if not users:
        print(f"키움 사용자 설정 없음: {reg_id}")
        sys.exit(1)
    user = users[0]
    if user["REFRESH_YN"] == "Y":
        user = b.refresh_token(conn, user)
    holdings = fetch_holdings(user)

    # 종목별 미청산 주문이력 조회
    cur.execute(f"SELECT * FROM TB_API_ORDER_HIST WHERE {COND} AND {MON} ORDER BY JONGMOG_CD, REG_DT DESC, IDX DESC", (reg_id, reg_id))
    groups = {}
    for r in cur.fetchall():
        groups.setdefault(r["JONGMOG_CD"], []).append(r)

    targets = []
    print(f"[{reg_id}] {'실행' if do_exec else '미리보기'} - 대상종목 {len(groups)}개")
    for code, rows in groups.items():
        db_qty = sum(r["BUY_CNT"] for r in rows)
        api_qty, api_avg = holdings.get(code, (0, 0))
        cur.execute("SELECT COUNT(1) n FROM TB_API_ORDER_HIST WHERE REG_ID=%s AND JONGMOG_CD=%s AND ORDER_STAT='AD_240_10' AND DEL_YN='N'", (reg_id, code))
        pending = cur.fetchone()["n"]

        if api_qty <= 0:
            note = "SKIP 키움잔고없음"
        elif pending:
            note = "SKIP 미체결존재"
        elif len(rows) == 1 and rows[0]["BUY_CNT"] == api_qty and rows[0]["BUY_PRICE"] == api_avg:
            note = "SKIP 이미정리됨"
        else:
            note = "대상" + ("" if db_qty == api_qty else " (수량차이)")
            targets.append((code, rows, api_qty, api_avg))
        print(f"  {code} DB {len(rows)}건 {db_qty}주 | 키움 {api_qty}주 평단 {api_avg} | {note}")

    if not do_exec:
        print(f"미리보기 종료 - 통합대상 {len(targets)}종목 (반영하려면 --exec)")
        return
    if not targets:
        print("통합 대상 없음")
        return

    # 1. 백업
    cur.execute(f"CREATE TABLE IF NOT EXISTS {bak} LIKE TB_API_ORDER_HIST")
    all_idx = [r["IDX"] for _, rows, _, _ in targets for r in rows]
    ph = ",".join(["%s"] * len(all_idx))
    cur.execute(f"INSERT IGNORE INTO {bak} SELECT * FROM TB_API_ORDER_HIST WHERE IDX IN ({ph})", all_idx)
    conn.commit()
    print(f"백업 {cur.rowcount}건 -> {bak}")

    total = 0
    for code, rows, qty, avg in targets:
        last = rows[0]
        idxs = [r["IDX"] for r in rows]
        msg = f"주문이력 1ROW 통합 {today} (원본 {len(rows)}건, 키움 잔고 기준)"
        # 2. 통합 1ROW 등록 (ORDER_NO 가 비어있으면 종목상세 화면에서 매도대상으로 안 보여서 MERGE_YYYYMMDD 지정)
        cur.execute(
            """INSERT INTO TB_API_ORDER_HIST (JONGMOG_CD, BASIC_PRICE, BUY_PRICE, BUY_CNT, BUY_POINT, BUY_ORDER_CNT,
                   SELL_BASIC_PRICE, SELL_PRICE, SELL_CNT, SELL_POINT, SELL_ORDER_CNT, ORDER_STAT, COMMISSION, TAX,
                   ACCNO, ORDER_NO, RESULT_MSG, ORDER_STATUS, CLOSE_YN, CLOSE_DT, DEL_YN, REG_DT, REG_ID, UPD_DT)
               VALUES (%s,%s,%s,%s,0,0, 0,0,0,0,0,'AD_240_11',%s,%s, %s,%s,%s,'','N',NULL,'N',%s,%s,NOW())""",
            (code, avg, avg, qty, last["COMMISSION"], last["TAX"], last["ACCNO"], f"MERGE_{today}", msg, last["REG_DT"], reg_id),
        )
        new_idx = cur.lastrowid
        # 3. 기존 ROW 취소/삭제 처리 (이력 유지, 수익통계·재매수 판단 제외)
        ph = ",".join(["%s"] * len(idxs))
        cur.execute(
            f"""UPDATE TB_API_ORDER_HIST SET CLOSE_YN='Y', DEL_YN='Y', ORDER_STAT='AD_240_13', CLOSE_DT=NULL, UPD_DT=NOW(),
                   RESULT_MSG=LEFT(CONCAT('[1ROW통합->IDX {new_idx}] ', RESULT_MSG), 2000) WHERE IDX IN ({ph})""",
            idxs,
        )
        conn.commit()
        total += cur.rowcount
        print(f"  {code} {len(rows)}건 -> IDX {new_idx} {qty}주 평단 {avg}")
    print(f"완료 - 통합 {len(targets)}종목, 기존행 처리 {total}건")


if __name__ == "__main__":
    main()
