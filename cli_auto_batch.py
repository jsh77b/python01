#!/usr/bin/env python3
"""
================================================================================
CLI AUTO 배치 — DB에 등록된 질문을 Claude CLI로 자동 실행

[흐름]
  TB_CLI_AUTO (status=AD_320_10 준비) 조회
  → status=AD_320_11 시작 변경
  → claude -p 실행 (stdin으로 컨텍스트 전달)
  → 결과 DB 저장 (status=AD_320_12 완료 or AD_320_14 실패)

[실행 방법]
  python3 cli_auto_batch.py          # 1회 실행
  python3 cli_auto_batch.py --loop   # 1분 간격 자동 실행

[CRON 등록]
  * * * * * /usr/bin/python3 /workspace/python01/cli_auto_batch.py
================================================================================
"""

import os
import sys
import glob
import time
import subprocess
from datetime import datetime, date, timedelta

# pymysql 로드 (없으면 /tmp에 자동 다운로드)
try:
    import pymysql
except ModuleNotFoundError:
    import urllib.request, io, tarfile
    _pkg = '/tmp/PyMySQL-1.1.0'
    if not os.path.exists(_pkg):
        _url = 'https://files.pythonhosted.org/packages/source/P/PyMySQL/PyMySQL-1.1.0.tar.gz'
        with urllib.request.urlopen(_url, timeout=15) as _r:
            with tarfile.open(fileobj=io.BytesIO(_r.read()), mode='r:gz') as _t:
                _t.extractall('/tmp/')
    sys.path.insert(0, _pkg)
    import pymysql


# ── DB 설정 ───────────────────────────────────────────────────────────────────
DB_HOST = "jsh77b.cafe24.com"
DB_USER = "jsh77b"
DB_PASS = os.getenv("CLI_AUTO_DB_PASS", "")
DB_NAME = "jsh77b"
DB_PORT = 3306

# ── 공통코드 (TB_COMM_CD GRP_CD='AD_320') ────────────────────────────────────
ST_READY  = 'AD_320_10'  # 준비
ST_START  = 'AD_320_11'  # 시작
ST_DONE   = 'AD_320_12'  # 완료
ST_CANCEL = 'AD_320_13'  # 취소
ST_FAIL   = 'AD_320_14'  # 실패

# ── Claude CLI 경로 ───────────────────────────────────────────────────────────
CLAUDE_BIN = "/home/jsh77b/.npm-global/bin/claude"

# ── 프로젝트별 작업 디렉토리 ──────────────────────────────────────────────────
PROJECT_DIRS = {
    "python01":       "/workspace/python01",
    "cafe24_jsh77b1": "/workspace/cafe24_jsh77b1",
    "chat":          "/workspace/chat",
}

# ── 로그 설정 ─────────────────────────────────────────────────────────────────
LOG_DIR       = "/workspace/python01/log"
LOG_PREFIX    = "cli_auto_"
LOG_KEEP_DAYS = 3


os.makedirs(LOG_DIR, exist_ok=True)


def cleanup_logs():
    cutoff = date.today() - timedelta(days=LOG_KEEP_DAYS)
    pattern = os.path.join(LOG_DIR, f"{LOG_PREFIX}????-??-??.log")
    for path in glob.glob(pattern):
        fname = os.path.basename(path)
        date_str = fname[len(LOG_PREFIX):-4]
        try:
            file_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if file_date < cutoff:
            os.remove(path)
            print(f"  [로그] 삭제: {fname}")


def log(msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}"
    print(line)
    log_file = os.path.join(LOG_DIR, f"cli_auto_{datetime.now().strftime('%Y-%m-%d')}.log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── DB 연결 ───────────────────────────────────────────────────────────────────
def db_connect():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        port=DB_PORT,
        charset='utf8',
        connect_timeout=10,
    )


# ── 이전 작업 이력 조회 (같은 등록자만) ────────────────────────────────────────
def get_history(cursor, project: str, reg_id: str) -> str:
    cursor.execute("""
        SELECT QUESTION
             , LEFT(ANSWER, 200)
        FROM TB_CLI_AUTO
        WHERE PROJECT=%s AND REG_ID=%s AND STATUS=%s
        ORDER BY REG_DATE DESC LIMIT 3
    """, (project, reg_id, ST_DONE))
    rows = cursor.fetchall()
    if not rows:
        return ""
    history = "이전 작업 이력:\n"
    for q, a in rows:
        history += f"Q: {q}\nA: {a}\n\n"
    return history


# ── 상태 업데이트 ─────────────────────────────────────────────────────────────
def update_status(cursor, conn, row_id: int, status: str, answer: str = None):
    cursor.execute("""
        UPDATE TB_CLI_AUTO
        SET STATUS=%s, ANSWER=%s, UPD_ID='batch', UPD_DATE=NOW()
        WHERE SEQ=%s
    """, (status, answer, row_id))
    conn.commit()


# ── CLAUDE.md 이력 추가 ───────────────────────────────────────────────────────
def append_claude_md(project: str, question: str):
    cwd = PROJECT_DIRS.get(project)
    if not cwd:
        return
    claude_md = os.path.join(cwd, "CLAUDE.md")
    if not os.path.exists(claude_md):
        return
    try:
        with open(claude_md, "a", encoding="utf-8") as f:
            f.write(f"\n- {question[:50]} 완료 ({datetime.now().strftime('%Y-%m-%d')})")
    except Exception as e:
        log(f"  [CLAUDE.md 오류] {e}")


# ── Claude 실행 지시문 (고정) ─────────────────────────────────────────────────
CLAUDE_INSTRUCTION = (
    "WebSearch, WebFetch 도구를 사용할 수 있습니다. "
    "날씨·주가·뉴스 등 실시간 정보가 필요한 경우 반드시 "
    "WebSearch 도구로 조회한 뒤 답하세요.\n"
    "git push, 파일 수정 등 모든 작업은 사용자에게 확인을 요청하지 말고 "
    "즉시 실행하세요.\n\n"
)


# ── 메인 배치 ─────────────────────────────────────────────────────────────────
def run_batch():
    cleanup_logs()
    conn = db_connect()
    cursor = conn.cursor()
    try:
        # ── 고착된 ST_START 행 복구 (프로세스 재시작 시) ──────────────────────
        # 배치 시작 시점에 ST_START 상태인 행은 이전 실행이 비정상 종료된 것으로 간주
        # 단, 현재 실행 중인 다른 인스턴스와의 충돌을 피하기 위해 GET_LOCK 사용
        cursor.execute("SELECT GET_LOCK('cli_auto_batch', 0)")
        lock_acquired = cursor.fetchone()[0]
        if not lock_acquired:
            log("다른 인스턴스 실행 중 — 스킵")
            return

        try:
            # 고착 행 ST_FAIL 처리
            cursor.execute("""
                UPDATE TB_CLI_AUTO
                SET STATUS=%s, ANSWER='이전 실행 비정상 종료로 재시도 필요',
                    UPD_ID='batch', UPD_DATE=NOW()
                WHERE STATUS=%s
            """, (ST_FAIL, ST_START))

            # 준비 건 조회
            cursor.execute("""
                SELECT SEQ, PROJECT, QUESTION, REG_ID
                FROM TB_CLI_AUTO
                WHERE STATUS=%s
                ORDER BY REG_DATE ASC
            """, (ST_READY,))
            rows = cursor.fetchall()

            if not rows:
                log("처리할 작업 없음")
                return

            in_progress = set()
            for row_id, project, question, reg_id in rows:
                if project in in_progress:
                    log(f"  [SKIP] id={row_id} project={project} — 이미 진행중인 작업 있음")
                    continue

                cwd = PROJECT_DIRS.get(project)
                if not cwd:
                    update_status(cursor, conn, row_id, ST_FAIL, f"알 수 없는 프로젝트: {project}")
                    log(f"  [실패] id={row_id} 알 수 없는 프로젝트: {project}")
                    continue

                log(f"  [시작] id={row_id} project={project}")
                try:
                    update_status(cursor, conn, row_id, ST_START)
                except Exception as e:
                    log(f"  [실패] id={row_id} ST_START 업데이트 오류: {e}")
                    continue
                in_progress.add(project)

                try:
                    history = get_history(cursor, project, reg_id)
                    context = CLAUDE_INSTRUCTION + history + f"새 작업: {question}"

                    # subprocess 실행 전 DB 재연결 (idle 연결 끊김 방지)
                    conn.ping(reconnect=True)

                    result = subprocess.run(
                        [CLAUDE_BIN, "-p", "--dangerously-skip-permissions"],
                        input=context,
                        cwd=cwd,
                        capture_output=True,
                        text=True,
                        timeout=600,
                    )

                    # subprocess 완료 후 DB 재연결 확인
                    conn.ping(reconnect=True)

                    if result.returncode == 0:
                        answer = result.stdout.strip()
                        if not answer:
                            update_status(cursor, conn, row_id, ST_FAIL, '빈 응답 (stdout 없음)')
                            log(f"  [실패] id={row_id} 빈 응답")
                        else:
                            update_status(cursor, conn, row_id, ST_DONE, answer)
                            append_claude_md(project, question)
                            log(f"  [완료] id={row_id} ({len(answer)}자)")
                    else:
                        err = (result.stderr or result.stdout).strip()[:4000]
                        update_status(cursor, conn, row_id, ST_FAIL, err)
                        log(f"  [실패] id={row_id} returncode={result.returncode}")

                except subprocess.TimeoutExpired as e:
                    if e.process:
                        e.process.kill()
                        e.process.communicate()
                    conn.ping(reconnect=True)
                    update_status(cursor, conn, row_id, ST_FAIL, '타임아웃 (10분 초과)')
                    log(f"  [실패] id={row_id} 타임아웃")
                except Exception as e:
                    try:
                        conn.ping(reconnect=True)
                        update_status(cursor, conn, row_id, ST_FAIL, str(e)[:4000])
                    except Exception as e2:
                        log(f"  [오류] id={row_id} ST_FAIL 업데이트 실패: {e2}")
                    log(f"  [실패] id={row_id} {e}")

        finally:
            cursor.execute("SELECT RELEASE_LOCK('cli_auto_batch')")

    finally:
        cursor.close()
        conn.close()


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CLI AUTO 배치")
    parser.add_argument("--loop", action="store_true", help="1분 간격 자동 실행 (Ctrl+C로 종료)")
    args = parser.parse_args()

    if args.loop:
        log("자동 실행 시작 — 1분 간격 (Ctrl+C로 종료)")
        try:
            while True:
                try:
                    run_batch()
                except Exception as e:
                    log(f"[오류] run_batch 예외: {e}")
                time.sleep(60)
        except KeyboardInterrupt:
            log("종료")
            sys.exit(0)
    else:
        run_batch()
