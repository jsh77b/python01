-- TB_BATCH_MASTER 수동 즉시실행(실행요청) 컬럼 추가
-- 용도: 관리자 > 배치관리 화면의 "즉시실행" 버튼이 RUN_REQ_YN='Y'로 표시하면
--       WSL 의 batch_runner.py 가 1분마다 폴링해서 해당 배치를 실행한다.

ALTER TABLE TB_BATCH_MASTER
  ADD COLUMN RUN_REQ_YN CHAR(1)     NOT NULL DEFAULT 'N' COMMENT '실행요청여부 (Y:즉시실행 대기)' AFTER USE_YN,
  ADD COLUMN RUN_REQ_DT DATETIME                          COMMENT '실행요청일시'                  AFTER RUN_REQ_YN,
  ADD COLUMN RUN_REQ_ID VARCHAR(50)                       COMMENT '실행요청자'                    AFTER RUN_REQ_DT;
