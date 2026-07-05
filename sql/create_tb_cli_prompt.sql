-- TB_CLI_PROMPT 테이블 DDL
-- 용도: CLI AUTO 질문 시 체크 선택하여 프롬프트를 합쳐서 사용

CREATE TABLE TB_CLI_PROMPT (
  PROMPT_ID    INT           NOT NULL AUTO_INCREMENT  COMMENT '프롬프트 ID',
  PROJECT      VARCHAR(100)  NOT NULL DEFAULT ''      COMMENT '프로젝트명 (TB_CLI_AUTO.PROJECT 참조)',
  PROMPT_NM    VARCHAR(100)  NOT NULL                 COMMENT '체크박스 표시명',
  PROMPT_TEXT  TEXT          NOT NULL                 COMMENT '프롬프트 내용',
  POSITION     CHAR(6)       NOT NULL DEFAULT 'BEFORE' COMMENT '질문 위치 (BEFORE/AFTER)',
  SORT_ORD     INT           NOT NULL DEFAULT 0       COMMENT '합칠 때 순서',
  USE_YN       CHAR(1)       NOT NULL DEFAULT 'Y'     COMMENT '사용여부',
  REG_ID       VARCHAR(50)                            COMMENT '등록자',
  REG_DATE     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP COMMENT '등록일시',
  UPD_ID       VARCHAR(50)                            COMMENT '수정자',
  UPD_DATE     DATETIME                               COMMENT '수정일시',
  PRIMARY KEY (PROMPT_ID)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;
