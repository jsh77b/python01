-- ============================================================
-- TB_STOCK_RANKING_HIST : 종목 순위 이력 테이블
-- TB_STOCK_RANKING 과 동일 구조, DELETE 없이 INSERT만 수행
-- ============================================================

CREATE TABLE IF NOT EXISTS TB_STOCK_RANKING_HIST (
    SEQ           INT           NOT NULL AUTO_INCREMENT COMMENT '일련번호',
    CATEGORY      VARCHAR(10)   NOT NULL               COMMENT '구분(RISE/FALL/FOR_BUY/FOR_SELL/INS_BUY/INS_SELL)',
    MARKET        VARCHAR(10)            DEFAULT NULL  COMMENT '시장구분 (KOSPI/KOSDAQ)',
    RANK_NO       INT           NOT NULL               COMMENT '순위',
    STOCK_CODE    VARCHAR(10)            DEFAULT NULL  COMMENT '종목코드',
    STOCK_NAME    VARCHAR(50)            DEFAULT NULL  COMMENT '종목명',
    CURRENT_PRICE DECIMAL(15,0)          DEFAULT NULL  COMMENT '현재가(원)',
    CHANGE_VAL    DECIMAL(15,0)          DEFAULT NULL  COMMENT '전일대비(원)',
    CHANGE_RATE   DECIMAL(10,6)          DEFAULT NULL  COMMENT '등락률 (0.015 = 1.5%)',
    VOLUME        BIGINT                 DEFAULT NULL  COMMENT '누적거래량(주)',
    TRADE_VALUE   BIGINT                 DEFAULT NULL  COMMENT '누적거래대금(원)',
    NET_VALUE     BIGINT                 DEFAULT NULL  COMMENT '순매수금액(원, 외국인/기관 카테고리)',
    REG_DT        TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '등록일시',
    PRIMARY KEY (SEQ),
    INDEX IDX_HIST_CATEGORY_REG  (CATEGORY, REG_DT),
    INDEX IDX_HIST_STOCK_REG     (STOCK_CODE, REG_DT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8 COMMENT='종목별 순위 이력 (수집 시각마다 누적)';
