# 프로젝트: python01

## 환경 정보

### 카페24 서버 환경
- Node.js: 12 ~ 14
- MySQL: 5.x
- 배포: git push (SSH, 쉘 명령 불가)
- **옵셔널 체이닝(`?.`), 널 병합(`??`) 등 Node 14 초과 문법 사용 금지**

### MySQL
- InnoDB 엔진 미지원 → **MyISAM 사용**
- CREATE TABLE 시 `ENGINE=MyISAM DEFAULT CHARSET=utf8` 사용
- 트랜잭션 미지원 (conn.commit() 무시됨)
- 버전: MySQL 5.x (카페24 서버)

### Python
- 경로: /workspace/python01

## 주요 파일

- `stock_monitor.py` — 주식 모니터링 배치
- `camping_monitor.py` — 캠핑 모니터링 배치
- `create_tb_stock_monitor.sql` — 주식 모니터 테이블 DDL
- `create_tb_camping_monitor.sql` — 캠핑 모니터 테이블 DDL
- `create_tb_stock_ranking_hist.sql` — 주식 랭킹 이력 테이블 DDL

## 공통코드 (TB_COMM_CD)

| GRP_CD  | 용도              | CD         | CD_NM |
|---------|-------------------|------------|-------|
| AD_320  | CLI AUTO 상태     | AD_320_10  | 준비  |
| AD_320  | CLI AUTO 상태     | AD_320_11  | 시작  |
| AD_320  | CLI AUTO 상태     | AD_320_12  | 완료  |
| AD_320  | CLI AUTO 상태     | AD_320_13  | 취소  |
| AD_320  | CLI AUTO 상태     | AD_320_14  | 실패  |

- React에서 질문 등록 시 → `준비`
- 배치 처리 시작 시 → `시작`
- 처리 완료 시 → `완료` / `실패`

## SQL 작성 규칙

- ENGINE=InnoDB 사용 금지 → ENGINE=MyISAM 사용
- CHARSET=utf8 고정
- mod_time은 Python 배치에서 NOW()로 직접 업데이트 (구버전 MySQL 호환)

- /workspace/python01/CLAUDE.md 파일 내용을 출력해줘 완료 (2026-06-20)
- 개인의뢰 개발프로젝트 사이트를 정리할수 있을까? 완료 (2026-06-21)
- https://freenuri.co.kr/m/index.html
해당사이트 접속가능한가? 완료 (2026-06-21)
- https://freenuri.co.kr/m/content/employ_list.html? 완료 (2026-06-21)
- 재능넷 사이트의 특징을 설명해줘 완료 (2026-06-21)
- https://ojnara.com/project
해당사이트 내용 정리해줘 완료 (2026-06-21)
- 내일 날씨에 대해서 알려줘? 완료 (2026-06-21)
- Tb_api_minitor_yn 테이블의 목록정보 보여줘 완료 (2026-06-22)
- 현재 폴더에 있는 파일목록 알려줘 완료 (2026-06-23)
- camping_monitor.py 파일 cron 1분 배치에 추가해줘 완료 (2026-06-23)
- camping_monitor.py 파일 cron 1분 배치에서 빼줘 완료 (2026-06-24)
- 현재 폴더의 파일목록을 표시 해줘

 완료 (2026-06-26)
- 파일목록 완료 (2026-07-03)