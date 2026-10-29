-- 숫자야구게임 공통코드(AD_330/AD_340) + 메뉴(651900) 등록
-- TB_COMM_CD 는 기존 AD_320(배치상태) 패턴을 그대로 따른다.

-- ── AD_330 : 게임 상태 ──────────────────────────────────────────────
INSERT INTO TB_COMM_CD (GRP_CD, GRP_NM, COM_CD, COM_NM, PRJ_CD, PRJ_NM, DISP_ORD, USE_YN, REG_ID, REG_DT, UPD_ID, UPD_DT) VALUES
 ('AD_330', '숫자야구 게임상태', 'AD_330_10', '대기',       'AD', '관리자', 1, 'Y', 'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW()),
 ('AD_330', '숫자야구 게임상태', 'AD_330_11', '숫자설정중', 'AD', '관리자', 2, 'Y', 'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW()),
 ('AD_330', '숫자야구 게임상태', 'AD_330_12', '진행중',     'AD', '관리자', 3, 'Y', 'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW()),
 ('AD_330', '숫자야구 게임상태', 'AD_330_13', '종료',       'AD', '관리자', 4, 'Y', 'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW()),
 ('AD_330', '숫자야구 게임상태', 'AD_330_14', '취소',       'AD', '관리자', 5, 'Y', 'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW());

-- ── AD_340 : 종료 사유 ──────────────────────────────────────────────
INSERT INTO TB_COMM_CD (GRP_CD, GRP_NM, COM_CD, COM_NM, PRJ_CD, PRJ_NM, DISP_ORD, USE_YN, REG_ID, REG_DT, UPD_ID, UPD_DT) VALUES
 ('AD_340', '숫자야구 종료사유', 'AD_340_10', '정답',       'AD', '관리자', 1, 'Y', 'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW()),
 ('AD_340', '숫자야구 종료사유', 'AD_340_11', '항복',       'AD', '관리자', 2, 'Y', 'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW()),
 ('AD_340', '숫자야구 종료사유', 'AD_340_12', '상대이탈',   'AD', '관리자', 3, 'Y', 'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW()),
 ('AD_340', '숫자야구 종료사유', 'AD_340_13', '턴한도초과', 'AD', '관리자', 4, 'Y', 'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW()),
 ('AD_340', '숫자야구 종료사유', 'AD_340_14', '무승부',     'AD', '관리자', 5, 'Y', 'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW());

-- ── 메뉴 : 포트폴리오(650000) > 숫자야구게임 ────────────────────────
-- MENU_GRADE=1 : nav.inc.php 의 $hoone_grade >= $menu_grade 규칙상
--                로그인한 모든 사용자에게 노출된다 (숫자가 낮을수록 공개범위 넓음)
INSERT INTO TB_MENU (MENU_GRP, MENU_DEPTH, MENU_CD, MENU_NM, MENU_LINK,
                     MENU_GRADE, MENU_INFO, MENU_GB, MENU_CLASS, ORD_NO, PHP_YN, MOBILE_YN, DEL_YN,
                     REG_ID, REG_DT, UPD_ID, UPD_DT)
VALUES (650000, 2, 651900, '숫자야구게임', '/hoone/php/baseball/baseball_list.php?',
        1, '', 'MENU', '', 0, 'Y', 'Y', 'N',
        'jsh77b@naver.com', NOW(), 'jsh77b@naver.com', NOW());
