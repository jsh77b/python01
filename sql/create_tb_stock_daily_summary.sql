-- ============================================================
-- 일별 코스피/코스닥 지수 + 사용자별 수익 스냅샷 테이블
-- 카페24 MySQL 서버는 InnoDB 미지원 -> MyISAM으로 자동 전환됨 (실제 확인됨)
-- ============================================================

CREATE TABLE IF NOT EXISTS TB_STOCK_DAILY_SUMMARY (
    SEQ                INT           NOT NULL AUTO_INCREMENT COMMENT '일련번호',
    SUMMARY_DATE       DATE          NOT NULL               COMMENT '기준일자',
    REG_ID             VARCHAR(50)   NOT NULL               COMMENT '사용자ID (TB_API_USER_SET.REG_ID)',
    KOSPI_PRICE        DECIMAL(12,2)          DEFAULT NULL  COMMENT '코스피 지수',
    KOSPI_CHANGE_VAL   DECIMAL(10,2)          DEFAULT NULL  COMMENT '코스피 전일대비',
    KOSPI_CHANGE_RATE  DECIMAL(10,6)          DEFAULT NULL  COMMENT '코스피 등락률 (0.015 = 1.5%)',
    KOSDAQ_PRICE       DECIMAL(12,2)          DEFAULT NULL  COMMENT '코스닥 지수',
    KOSDAQ_CHANGE_VAL  DECIMAL(10,2)          DEFAULT NULL  COMMENT '코스닥 전일대비',
    KOSDAQ_CHANGE_RATE DECIMAL(10,6)          DEFAULT NULL  COMMENT '코스닥 등락률 (0.015 = 1.5%)',
    TOT_INS_AMT        BIGINT                 DEFAULT NULL  COMMENT '총매입금액(원, TB_API_USER_SET.TOT_INS_AMT)',
    PRSM_DPST_ASET_AMT BIGINT                 DEFAULT NULL  COMMENT '추정예탁자산=평가금액(원, TB_API_USER_SET.PRSM_DPST_ASET_AMT)',
    PROFIT_AMT         BIGINT                 DEFAULT NULL  COMMENT '평가손익금액(원) = PRSM_DPST_ASET_AMT - TOT_INS_AMT',
    PROFIT_RATE        DECIMAL(10,4)          DEFAULT NULL  COMMENT '수익률(%, 1.5 = 1.5%)',
    REG_DT             TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '등록일시',
    PRIMARY KEY (SEQ),
    UNIQUE INDEX UQ_SUMMARY_DATE_USER (SUMMARY_DATE, REG_ID)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COMMENT='일별 코스피/코스닥 지수 및 사용자별 수익 스냅샷';
