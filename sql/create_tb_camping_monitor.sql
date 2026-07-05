-- ============================================================
-- TB_CAMPING_MONITOR 테이블 생성 스크립트
-- ============================================================


-- ────────────────────────────────────────────────────────────
-- [MySQL / MariaDB] (구버전 호환 - MySQL 5.0+ 지원)
-- ────────────────────────────────────────────────────────────
CREATE TABLE TB_CAMPING_MONITOR (
    IDX     INT          NOT NULL AUTO_INCREMENT,
    KEY1    VARCHAR(100) NULL,
    KEY2    VARCHAR(100) NULL,
    KEY3    VARCHAR(100) NULL,
    KEY4    VARCHAR(100) NULL,
    KEY5    VARCHAR(100) NULL,
    CONF_YN CHAR(1)      NOT NULL DEFAULT 'N',
    DEL_YN  CHAR(1)      NOT NULL DEFAULT 'N',
    REG_DT  DATETIME     NOT NULL,
    UPD_DT  DATETIME     NOT NULL,
    PRIMARY KEY (IDX)
);

-- INSERT 시 REG_DT, UPD_DT 자동 설정
DELIMITER $$
CREATE TRIGGER TRG_CAMPING_MONITOR_BI
BEFORE INSERT ON TB_CAMPING_MONITOR
FOR EACH ROW
BEGIN
    SET NEW.REG_DT = NOW();
    SET NEW.UPD_DT = NOW();
END$$

-- UPDATE 시 UPD_DT 자동 갱신
CREATE TRIGGER TRG_CAMPING_MONITOR_BU
BEFORE UPDATE ON TB_CAMPING_MONITOR
FOR EACH ROW
BEGIN
    SET NEW.UPD_DT = NOW();
END$$
DELIMITER ;


-- ────────────────────────────────────────────────────────────
-- [Oracle]
-- ────────────────────────────────────────────────────────────
CREATE SEQUENCE SEQ_CAMPING_MONITOR_IDX
    START WITH 1
    INCREMENT BY 1
    NOCACHE
    NOCYCLE;

CREATE TABLE TB_CAMPING_MONITOR (
    IDX     NUMBER        NOT NULL,
    KEY1    VARCHAR2(100),
    KEY2    VARCHAR2(100),
    KEY3    VARCHAR2(100),
    KEY4    VARCHAR2(100),
    KEY5    VARCHAR2(100),
    CONF_YN CHAR(1)       DEFAULT 'N' NOT NULL,
    DEL_YN  CHAR(1)       DEFAULT 'N' NOT NULL,
    REG_DT  DATE          DEFAULT SYSDATE NOT NULL,
    UPD_DT  DATE          DEFAULT SYSDATE NOT NULL,
    CONSTRAINT PK_CAMPING_MONITOR PRIMARY KEY (IDX)
);

-- Oracle 자동증가: 트리거로 시퀀스 적용
CREATE OR REPLACE TRIGGER TRG_CAMPING_MONITOR_IDX
BEFORE INSERT ON TB_CAMPING_MONITOR
FOR EACH ROW
BEGIN
    IF :NEW.IDX IS NULL THEN
        SELECT SEQ_CAMPING_MONITOR_IDX.NEXTVAL INTO :NEW.IDX FROM DUAL;
    END IF;
END;
/
