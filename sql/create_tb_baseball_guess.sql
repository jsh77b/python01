-- TB_BASEBALL_GUESS : 숫자야구게임 추측 이력
-- 용도: 턴마다 제출된 추측 숫자와 스트라이크/볼 판정 결과를 적재
--
-- STRIKE_CNT / BALL_CNT 는 소켓서버가 계산해서 넣는다 (클라이언트 입력값 아님).
--   스트라이크 = 숫자도 맞고 자리도 맞은 개수
--   볼        = 숫자는 있으나 자리가 틀린 개수
--
-- 아웃(0S0B)과 시간초과는 둘 다 0/0 이라 숫자만으로 구분되지 않으므로
-- OUT_YN / TIMEOUT_YN 플래그로 구분한다.
--   OUT_YN='Y'     : 추측은 했으나 하나도 안 맞음
--   TIMEOUT_YN='Y' : 추측 없이 시간초과로 턴만 넘어감 (GUESS_NO='')

CREATE TABLE TB_BASEBALL_GUESS (
  SEQ            INT           NOT NULL AUTO_INCREMENT   COMMENT 'SEQ',
  MATCH_SEQ      INT           NOT NULL                  COMMENT 'TB_BASEBALL_MATCH.SEQ',
  TURN_NO        INT           NOT NULL                  COMMENT '턴 번호 (1부터)',
  GUESS_ID       VARCHAR(50)   NOT NULL                  COMMENT '추측한 플레이어 ID',
  GUESS_NO       VARCHAR(20)   NOT NULL DEFAULT ''       COMMENT '추측 숫자 (최대 9자리)',
  STRIKE_CNT     INT           NOT NULL DEFAULT 0        COMMENT '스트라이크',
  BALL_CNT       INT           NOT NULL DEFAULT 0        COMMENT '볼',
  OUT_YN         CHAR(1)       NOT NULL DEFAULT 'N'      COMMENT '아웃여부 (0S0B)',
  TIMEOUT_YN     CHAR(1)       NOT NULL DEFAULT 'N'      COMMENT '시간초과로 넘어간 턴 여부',
  REG_DT         TIMESTAMP     DEFAULT CURRENT_TIMESTAMP COMMENT '등록일',
  PRIMARY KEY (SEQ),
  KEY IDX_BASEBALL_GUESS_01 (MATCH_SEQ, TURN_NO)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;
