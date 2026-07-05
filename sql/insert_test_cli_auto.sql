-- TB_CLI_AUTO 테스트 데이터 INSERT
-- 실행: mysql -h jsh77b1.cafe24app.com -u jsh77b1 -p jsh77b1 < insert_test_cli_auto.sql

INSERT INTO TB_CLI_AUTO (PROJECT, QUESTION, STATUS, REG_ID)
VALUES
    ('python01',       '/workspace/python01/CLAUDE.md 파일 내용을 출력해줘', 'AD_320_10', 'test'),
    ('cafe24_jsh77b1', '/workspace/cafe24_jsh77b1/CLAUDE.md 파일 내용을 출력해줘', 'AD_320_10', 'test');

SELECT SEQ, PROJECT, LEFT(QUESTION, 50) AS QUESTION, STATUS, REG_DATE
FROM TB_CLI_AUTO
ORDER BY SEQ DESC
LIMIT 5;
