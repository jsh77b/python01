-- 2026.10.29 손님 주문 신용카드 결제(KG이니시스) 결제정보
-- 결제요청(READY) -> 승인처리중(PROC) -> 결제완료(PAID) / 실패(FAIL) / 망취소(NETCANCEL) / 망취소실패(NETCANCEL_FAIL)
CREATE TABLE IF NOT EXISTS TBO_ORDER_PAY (
    PAY_IDX       INT          NOT NULL AUTO_INCREMENT COMMENT '결제 IDX',
    OID           VARCHAR(40)  NOT NULL                COMMENT '이니시스 주문번호(가맹점)',
    REST_IDX      INT          NOT NULL                COMMENT '상점 IDX',
    USER_ID       VARCHAR(100) NOT NULL                COMMENT '주문자 ID',
    PRICE         INT          NOT NULL DEFAULT 0      COMMENT '결제요청 금액(장바구니 합계)',
    GOOD_NAME     VARCHAR(100)          DEFAULT NULL   COMMENT '상품명',
    DEVICE        VARCHAR(10)           DEFAULT NULL   COMMENT 'PC / MOBILE',
    STATUS        VARCHAR(20)  NOT NULL DEFAULT 'READY' COMMENT 'READY/PROC/PAID/FAIL/NETCANCEL/NETCANCEL_FAIL',
    TID           VARCHAR(50)           DEFAULT NULL   COMMENT '이니시스 거래번호',
    PAY_METHOD    VARCHAR(20)           DEFAULT NULL   COMMENT '결제수단',
    APPL_NUM      VARCHAR(20)           DEFAULT NULL   COMMENT '승인번호',
    APPL_DT       VARCHAR(20)           DEFAULT NULL   COMMENT '승인일시',
    CARD_NUM      VARCHAR(30)           DEFAULT NULL   COMMENT '카드번호(마스킹)',
    CARD_CODE     VARCHAR(10)           DEFAULT NULL   COMMENT '카드사코드',
    EASY_PAY_CD   VARCHAR(10)           DEFAULT NULL   COMMENT '간편(앱)결제구분 (PC CARD_SrcCode / 모바일 P_SRC_CODE)',
    RESULT_CODE   VARCHAR(10)           DEFAULT NULL   COMMENT '결과코드',
    RESULT_MSG    VARCHAR(500)          DEFAULT NULL   COMMENT '결과메시지',
    ORDER_IDX     VARCHAR(30)           DEFAULT NULL   COMMENT '생성된 주문 IDX',
    ORD_NO        VARCHAR(30)           DEFAULT NULL   COMMENT '생성된 주문번호',
    REG_DT        DATETIME              DEFAULT NULL   COMMENT '등록일시',
    UPD_DT        DATETIME              DEFAULT NULL   COMMENT '수정일시',
    PRIMARY KEY (PAY_IDX),
    UNIQUE KEY UK_TBO_ORDER_PAY_OID (OID),
    KEY IDX_TBO_ORDER_PAY_REST (REST_IDX, USER_ID)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COMMENT='손님 주문 결제정보(이니시스)';

-- 2026.10.29 간편결제구분 컬럼 추가 (기존 테이블)
-- ALTER TABLE TBO_ORDER_PAY ADD COLUMN EASY_PAY_CD VARCHAR(10) DEFAULT NULL COMMENT '간편(앱)결제구분 (PC CARD_SrcCode / 모바일 P_SRC_CODE)' AFTER CARD_CODE;
