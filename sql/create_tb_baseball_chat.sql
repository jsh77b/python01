-- TB_BASEBALL_CHAT : 숫자야구게임 게임중 채팅 이력
-- 용도: 게임 화면/결과 화면의 채팅 내용 저장 (새로고침 시 복원용)
--
-- 소켓은 프리채팅과 동일한 user -> message 이벤트를 재사용한다.
-- (릴레이 방 키가 gubn+room 이라 gubn='baseball' 이면 프리채팅과 자동 분리됨)
--
-- 저장만 TB_CHAT_USER_TALK 대신 별도 테이블로 분리한 이유:
--   TB_CHAT_USER_TALK 는 ROOM_IDX 로 TB_CHAT_ROOM_MASTER(프리채팅 방 목록)를 참조한다.
--   게임마다 프리채팅 방 마스터 행을 만들면 프리채팅 방 목록이 게임방으로 오염된다.

CREATE TABLE TB_BASEBALL_CHAT (
  SEQ            INT           NOT NULL AUTO_INCREMENT   COMMENT 'SEQ',
  MATCH_SEQ      INT           NOT NULL                  COMMENT 'TB_BASEBALL_MATCH.SEQ',
  ROOM_ID        VARCHAR(50)   NOT NULL                  COMMENT '방 ID',
  USER_ID        VARCHAR(50)   NOT NULL                  COMMENT '발신자 ID',
  USER_NM        VARCHAR(100)                            COMMENT '발신자 이름',
  CONTENT        VARCHAR(800)  NOT NULL                  COMMENT '메시지 내용',
  REG_DT         TIMESTAMP     DEFAULT CURRENT_TIMESTAMP COMMENT '등록일',
  PRIMARY KEY (SEQ),
  KEY IDX_BASEBALL_CHAT_01 (MATCH_SEQ, SEQ),
  KEY IDX_BASEBALL_CHAT_02 (ROOM_ID)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;
