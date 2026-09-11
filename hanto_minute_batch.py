#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
한투(hanto) API 1분 배치. web.js의 runBatchJob에 있던
  callOrderApi("HANTO", "http://jsh77b.cafe24.com/hoone/php/hantoApi/hanto_rest_api.php")
를 그대로 파이썬으로 이관. Node은 이 URL을 1분마다 GET 호출만 하고 실제 로직(토큰갱신/
시세수집/09시 일배치 등)은 전부 PHP(hanto_rest_api.php, hanto_chkExecHour.php)에서
처리하므로, 이 배치도 동일하게 URL 호출만 한다.

[실행방법 / cron 등록 예]
  python3 hanto_minute_batch.py
  # 1분마다 (Node의 1분 배치와 동일 주기)
  # */1 * * * * /usr/bin/python3 /workspace/python01/hanto_minute_batch.py >> /workspace/python01/log/hanto_minute_$(date +\\%Y-\\%m-\\%d).log 2>&1
================================================================================
"""

import sys
from datetime import datetime

import requests

HANTO_API_URL = "http://jsh77b.cafe24.com/hoone/php/hantoApi/hanto_rest_api.php"


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def main():
    try:
        response = requests.get(HANTO_API_URL, timeout=30)
        log(f"HANTO 호출 완료 status={response.status_code}")
    except Exception as e:
        log(f"HANTO 호출 실패: {e}")
        sys.exit(1)


# 2026.09.11 배치관리(TB_BATCH_MASTER) 연동 - 사용여부 체크 + 상태 보고
import batch_status

BATCH_NM = "hanto_minute_batch.py"


if __name__ == "__main__":
    if not batch_status.is_enabled(BATCH_NM):
        batch_status.log_skip(BATCH_NM)
        sys.exit(0)

    batch_status.mark_start(BATCH_NM)
    try:
        main()
        batch_status.mark_done(BATCH_NM)
    except Exception as e:
        batch_status.mark_failed(BATCH_NM, str(e))
        raise
