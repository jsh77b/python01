-- TB_BASEBALL_MATCH : 숫자야구게임 방/매치 정보
-- 용도: 포트폴리오 > 숫자야구게임 의 방 목록(로비) 겸 매치 결과 저장
--
-- 주의) 정답(비밀번호) 숫자 컬럼은 의도적으로 두지 않는다.
--       정답은 소켓서버(php_socket_relay.py) 메모리에만 보관하고
--       게임 종료 시 baseball_end 이벤트로만 공개한다.
--       DB에 남기면 다른 화면/쿼리로 유출될 경로가 생긴다.
--
-- 주의) ROOM_ID 는 UNIQUE 로 잡는다. MyISAM은 트랜잭션이 없어
--       "조회 후 INSERT" 사이에 끼어들 수 있는데, UNIQUE 제약이 있으면
--       중복 INSERT가 DB 레벨에서 실패해 방 중복 생성이 막힌다.

CREATE TABLE TB_BASEBALL_MATCH (
  SEQ            INT           NOT NULL AUTO_INCREMENT      COMMENT 'SEQ',
  ROOM_ID        VARCHAR(50)   NOT NULL                     COMMENT '방 ID (소켓 room 키)',
  ROOM_NM        VARCHAR(100)  NOT NULL DEFAULT ''          COMMENT '방 제목',
  P1_ID          VARCHAR(50)   NOT NULL                     COMMENT '방장(플레이어1) ID',
  P1_NM          VARCHAR(100)                               COMMENT '방장 이름',
  P2_ID          VARCHAR(50)                                COMMENT '참가자(플레이어2) ID',
  P2_NM          VARCHAR(100)                               COMMENT '참가자 이름',
  DIGIT_CNT      INT           NOT NULL DEFAULT 3           COMMENT '자릿수 (3~9)',
  TURN_LIMIT_SEC INT           NOT NULL DEFAULT 0           COMMENT '턴 제한시간(초). 0=무제한, 최대 300',
  STATUS         VARCHAR(20)   NOT NULL DEFAULT 'AD_330_10' COMMENT '상태 (TB_COMM_CD GRP_CD=AD_330)',
  WINNER_ID      VARCHAR(50)                                COMMENT '승자 ID (무승부/취소는 NULL)',
  END_TYPE       VARCHAR(20)                                COMMENT '종료사유 (TB_COMM_CD GRP_CD=AD_340)',
  TURN_CNT       INT           NOT NULL DEFAULT 0           COMMENT '총 진행 턴수',
  STA_DT         DATETIME                                   COMMENT '게임 시작일시',
  END_DT         DATETIME                                   COMMENT '게임 종료일시',
  DEL_YN         CHAR(1)       NOT NULL DEFAULT 'N'         COMMENT '삭제여부',
  REG_ID         VARCHAR(50)                                COMMENT '등록자',
  REG_DT         TIMESTAMP     DEFAULT CURRENT_TIMESTAMP    COMMENT '등록일',
  UPD_ID         VARCHAR(50)                                COMMENT '수정자',
  UPD_DT         DATETIME                                   COMMENT '수정일',
  PRIMARY KEY (SEQ),
  UNIQUE KEY UK_BASEBALL_MATCH_01 (ROOM_ID),
  KEY IDX_BASEBALL_MATCH_01 (STATUS),
  KEY IDX_BASEBALL_MATCH_02 (P1_ID),
  KEY IDX_BASEBALL_MATCH_03 (P2_ID)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;
