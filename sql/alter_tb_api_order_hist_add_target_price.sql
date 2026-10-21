-- 2026.10.21 단타/스윙 화면 매수예약 주문에 분석구분/익절가 저장 (익절가 도달 시 kiwoom_trading_batch.py 자동매도)
-- 기존 주문은 ANAL_GUBN='' / TARGET_PRICE=0 (익절가 매도 대상 아님)
ALTER TABLE TB_API_ORDER_HIST
    ADD ANAL_GUBN    CHAR(1) NOT NULL DEFAULT '' COMMENT '분석구분 (D:단타, S:스윙, 공백:일반)' AFTER RESERVE_ED_DT,
    ADD TARGET_PRICE INT     NOT NULL DEFAULT 0  COMMENT '익절가 (현재가 >= 익절가 이면 자동매도, 0:미사용)' AFTER ANAL_GUBN;
