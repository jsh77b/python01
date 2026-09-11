-- TB_BATCH_MASTER 테이블 DDL
-- 용도: 관리자 > 배치관리 화면에서 파이썬 배치 목록/상태/사용여부를 관리

CREATE TABLE TB_BATCH_MASTER (
  SEQ            INT           NOT NULL AUTO_INCREMENT      COMMENT 'SEQ',
  BATCH_NM       VARCHAR(200)  NOT NULL                     COMMENT '배치 파일명 (예: stock_monitor.py)',
  CYCLE_NM       VARCHAR(100)  NOT NULL DEFAULT ''           COMMENT '주기 (한글, 예: 10분마다)',
  CRON_INFO      VARCHAR(100)  NOT NULL DEFAULT ''           COMMENT 'CRON 원본 값 (예: */10 * * * *)',
  STATUS         VARCHAR(20)   NOT NULL DEFAULT 'AD_320_10'  COMMENT '상태 (TB_COMM_CD GRP_CD=AD_320: 준비/시작/완료/취소/실패)',
  STATUS_UPD_DT  DATETIME                                    COMMENT '상태수정일',
  USE_YN         VARCHAR(20)   NOT NULL DEFAULT 'AD_110_10'  COMMENT '사용여부 (TB_COMM_CD GRP_CD=AD_110: 사용/미사용)',
  DEL_YN         CHAR(1)       NOT NULL DEFAULT 'N'          COMMENT '삭제여부',
  REG_ID         VARCHAR(50)                                 COMMENT '등록자',
  REG_DT         TIMESTAMP     DEFAULT CURRENT_TIMESTAMP       COMMENT '등록일',
  UPD_ID         VARCHAR(50)                                 COMMENT '수정자',
  UPD_DT         DATETIME                                    COMMENT '수정일',
  PRIMARY KEY (SEQ)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;
