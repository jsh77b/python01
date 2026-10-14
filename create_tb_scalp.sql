-- 2026.10.14 단타 분석 설정 (하락/익절은 UNIT_GUBN 에 따라 % 또는 금액(원), 매수는 금액(원)) / 결과 (감시목록 > 단타분석 화면, scalp_analysis_batch.py)

-- 사용자별 단타 분석 조건 + 분석요청 상태 (REQ_STAT : TB_COMM_CD AD_320 준비/시작/완료/취소/실패)
CREATE TABLE IF NOT EXISTS TB_SCALP_SET (
    REG_ID       VARCHAR(100)  NOT NULL                COMMENT '사용자ID',
    ANAL_DAYS    INT           NOT NULL DEFAULT 20     COMMENT '분석기간(거래일)',
    UNIT_GUBN       CHAR(1)    NOT NULL DEFAULT 'P'    COMMENT '하락/익절 기준 (P:%, W:금액)',
    BUY_DROP_PER    DECIMAL(5,2) NOT NULL DEFAULT 2.00 COMMENT '매수조건 시가대비 하락%',
    TAKE_PROFIT_PER DECIMAL(5,2) NOT NULL DEFAULT 1.20 COMMENT '익절%',
    BUY_DROP_AMT    INT        NOT NULL DEFAULT 1000   COMMENT '매수조건 시가대비 하락금액(원)',
    TAKE_PROFIT_AMT INT        NOT NULL DEFAULT 1000   COMMENT '익절금액(원)',
    BUY_AMT         INT        NOT NULL DEFAULT 500000 COMMENT '매수금액(원, 수량=매수금액/예약가)',
    REQ_STAT     VARCHAR(20)   NOT NULL DEFAULT ''     COMMENT '분석요청상태(AD_320)',
    REQ_DT       DATETIME      NULL                    COMMENT '분석요청일시',
    DONE_DT      DATETIME      NULL                    COMMENT '분석완료일시',
    RESULT_MSG   VARCHAR(500)  NOT NULL DEFAULT ''     COMMENT '결과메세지',
    REG_DT       DATETIME      NULL                    COMMENT '등록일시',
    UPD_DT       DATETIME      NULL                    COMMENT '수정일시',
    PRIMARY KEY (REG_ID)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COMMENT='단타분석설정';

-- 사용자별 종목 단타 분석 결과 (분석할 때마다 사용자 전체 삭제 후 재등록)
CREATE TABLE IF NOT EXISTS TB_SCALP_RESULT (
    IDX          INT           NOT NULL AUTO_INCREMENT COMMENT 'IDX',
    REG_ID       VARCHAR(100)  NOT NULL                COMMENT '사용자ID',
    JONGMOG_CD   VARCHAR(20)   NOT NULL                COMMENT '종목코드',
    JONGMOG_NM   VARCHAR(100)  NOT NULL DEFAULT ''     COMMENT '종목명',
    DAY_CNT      INT           NOT NULL DEFAULT 0      COMMENT '분석거래일수',
    DAY_RANGE    DECIMAL(6,2)  NOT NULL DEFAULT 0      COMMENT '평균 일중변동폭%',
    MIN5_RANGE   DECIMAL(6,3)  NOT NULL DEFAULT 0      COMMENT '10시이후 5분봉 평균변동폭%',
    TRADE_AMT    DECIMAL(12,1) NOT NULL DEFAULT 0      COMMENT '일평균 거래대금(억)',
    PERIOD_RATE  DECIMAL(6,2)  NOT NULL DEFAULT 0      COMMENT '기간 등락률%',
    TRADE_CNT    INT           NOT NULL DEFAULT 0      COMMENT '조건 충족 거래횟수',
    WIN_CNT      INT           NOT NULL DEFAULT 0      COMMENT '수익 거래횟수',
    PROFIT_SUM   DECIMAL(8,2)  NOT NULL DEFAULT 0      COMMENT '누적손익%(비용차감)',
    PROFIT_AMT   INT           NOT NULL DEFAULT 0      COMMENT '누적손익금액(원, 매수금액 기준 비용차감)',
    FEE_RATE     DECIMAL(5,2)  NOT NULL DEFAULT 0      COMMENT '왕복비용%',
    LAST_DT      VARCHAR(8)    NOT NULL DEFAULT ''     COMMENT '마지막 거래일',
    LAST_OPEN    INT           NOT NULL DEFAULT 0      COMMENT '마지막 거래일 시가',
    LAST_CLOSE   INT           NOT NULL DEFAULT 0      COMMENT '마지막 거래일 종가(현재가)',
    REG_DT       DATETIME      NULL                    COMMENT '분석일시',
    PRIMARY KEY (IDX),
    KEY IX_SCALP_RESULT_01 (REG_ID, JONGMOG_CD)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COMMENT='단타분석결과';
