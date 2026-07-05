-- ============================================================
-- Daum Finance 국내 증시 데이터 저장 테이블
-- MySQL 5.1+ 호환 (DATETIME DEFAULT NOW() 미사용, utf8 사용)
-- ============================================================
-- ※ 테이블이 이미 존재할 경우 MARKET 컬럼 추가
-- ALTER TABLE TB_STOCK_RANKING ADD COLUMN MARKET VARCHAR(10) DEFAULT NULL COMMENT '시장구분 (KOSPI/KOSDAQ)' AFTER CATEGORY;
-- ============================================================

-- 코스피 / 코스닥 지수 정보
CREATE TABLE IF NOT EXISTS TB_MARKET_INDEX (
    SEQ           INT           NOT NULL AUTO_INCREMENT COMMENT '일련번호',
    MARKET_TYPE   VARCHAR(10)   NOT NULL               COMMENT '시장구분 (KOSPI/KOSDAQ)',
    CURRENT_PRICE DECIMAL(12,2)          DEFAULT NULL  COMMENT '현재지수',
    CHANGE_VAL    DECIMAL(10,2)          DEFAULT NULL  COMMENT '전일대비',
    CHANGE_RATE   DECIMAL(10,6)          DEFAULT NULL  COMMENT '등락률 (0.015 = 1.5%)',
    VOLUME        BIGINT                 DEFAULT NULL  COMMENT '누적거래량(주)',
    TRADE_VALUE   BIGINT                 DEFAULT NULL  COMMENT '누적거래대금(원)',
    MARKET_CAP    BIGINT                 DEFAULT NULL  COMMENT '시가총액(억원)',
    REG_DT        TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '등록일시',
    PRIMARY KEY (SEQ),
    INDEX IDX_MARKET_REG (MARKET_TYPE, REG_DT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8 COMMENT='국내 시장지수 정보';


-- 상승/하락/외국인·기관 순매수·순매도 종목 순위
CREATE TABLE IF NOT EXISTS TB_STOCK_RANKING (
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
    INDEX IDX_CATEGORY_REG (CATEGORY, REG_DT)
) ENGINE=InnoDB DEFAULT CHARSET=utf8 COMMENT='종목별 순위 정보 (상승/하락/외국인/기관)';
