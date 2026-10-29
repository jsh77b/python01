-- TB_BASEBALL_RANK : 숫자야구게임 전적 집계
-- 용도: 사용자별 승/패/무 누적, 전적 화면(baseball_rank.php) 노출
--
-- 주의) MyISAM은 트랜잭션이 없으므로 갱신은 반드시 아래처럼
--       INSERT ... ON DUPLICATE KEY UPDATE 단일문으로 처리한다.
--       "SELECT 후 UPDATE" 로 쪼개면 동시 종료 시 집계가 어긋난다.
--
--   INSERT INTO TB_BASEBALL_RANK
--          (USER_ID, USER_NM, PLAY_CNT, WIN_CNT, LOSE_CNT, DRAW_CNT, BEST_TURN, UPD_DT)
--   VALUES (%s, %s, 1, %s, %s, %s, %s, NOW())
--   ON DUPLICATE KEY UPDATE
--     USER_NM   = VALUES(USER_NM),
--     PLAY_CNT  = PLAY_CNT + 1,
--     WIN_CNT   = WIN_CNT  + VALUES(WIN_CNT),
--     LOSE_CNT  = LOSE_CNT + VALUES(LOSE_CNT),
--     DRAW_CNT  = DRAW_CNT + VALUES(DRAW_CNT),
--     BEST_TURN = IF(VALUES(BEST_TURN) IS NULL, BEST_TURN,
--                    IF(BEST_TURN IS NULL, VALUES(BEST_TURN),
--                       LEAST(BEST_TURN, VALUES(BEST_TURN)))),
--     UPD_DT    = NOW();

CREATE TABLE TB_BASEBALL_RANK (
  USER_ID        VARCHAR(50)   NOT NULL                  COMMENT '사용자 ID',
  USER_NM        VARCHAR(100)                            COMMENT '사용자 이름',
  PLAY_CNT       INT           NOT NULL DEFAULT 0        COMMENT '총 판수',
  WIN_CNT        INT           NOT NULL DEFAULT 0        COMMENT '승',
  LOSE_CNT       INT           NOT NULL DEFAULT 0        COMMENT '패',
  DRAW_CNT       INT           NOT NULL DEFAULT 0        COMMENT '무',
  BEST_TURN      INT                                     COMMENT '최소 턴 승리 기록',
  REG_DT         TIMESTAMP     DEFAULT CURRENT_TIMESTAMP COMMENT '등록일',
  UPD_DT         DATETIME                                COMMENT '수정일',
  PRIMARY KEY (USER_ID)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;
