# -*- coding: utf-8 -*-
r"""
launch_claude_rc.py  —  새 Windows 터미널 창에서 WSL 의 claude 를 원격제어(Remote Control) 모드로 실행

목적: 폰이나 claude.ai/code 에서 이 PC 의 claude 세션을 원격 제어(rc = Remote Control)한다.
모바일은 로컬 파일시스템 권한이 없으므로, 실제 작업은 PC 터미널의 claude 가 수행한다.
claude 는 Windows 에 설치되어 있지 않고 WSL(Ubuntu) 안에만 있으므로, 새 창에서
바로 WSL bash 로 들어가 그 안의 claude 를 실행한다 (Windows PowerShell 경유 X).

동작 순서:
1. 새 Windows 터미널 창을 띄운다
2. 그 창 안에서 wsl.exe 로 Ubuntu 배포판에 진입한다
3. /workspace/cafe24_jsh77b1 로 이동 후 claude 를 --remote-control 플래그로 실행한다

주의: 원격제어는 대화형 슬래시 명령 /rc(=/remote-control)로 켜는 기능이라,
claude '/rc' 처럼 시작 프롬프트 인자로 넘기면 자동 실행되지 않는다.
실행 시점에 켜려면 반드시 `claude --remote-control` 플래그를 써야 한다.
(원격제어에는 Claude 구독 로그인이 필요하다.)
──────────────────────────────────────────────────────────────────────────────
■ 실행 환경 (중요)
- 진짜 Python 은 WSL 안에만 있음(3.14). Windows python.exe 는 Store 스텁이라 불가.
- claude 도 WSL(Ubuntu) 안에만 설치되어 있음. Windows 쪽엔 claude 가 없다.
=> 이 스크립트는 WSL 의 python3 로 실행하고, 새 창에서 다시 wsl.exe 로 들어가 claude 를 부른다.

■ 창을 띄우는 방법: wt.exe (Windows Terminal)
- WSL interop 으로 콘솔 앱을 Start-Process / cmd start 로 띄우면 콘솔 창이
  "보이지 않게" 실행된다(MainWindowHandle=0). 검증으로 확인함.
- 반면 wt.exe 는 GUI 앱이라 interop 으로도 실제 창이 보인다. 그래서 wt.exe 사용.
- 기본 '-w new' 로 기존 터미널에 탭이 아니라 "새 창"을 강제로 연다.
──────────────────────────────────────────────────────────────────────────────

실행 방법 (WSL 터미널 또는 Git Bash 에서):
    wsl python3 /workspace/python01/launch_claude_rc.py

옵션:
    --name <이름>    # 원격제어 세션 이름 지정 (기본: 없음)
    --tab            # 새 창이 아니라 기존 Windows Terminal 에 새 탭으로 열기
    --no-keep        # claude 종료 시 창(탭)도 닫기 (기본: bash 유지로 안 닫힘)
"""

import argparse
import shutil
import subprocess
import sys

WSL_DISTRO = "Ubuntu"
WORKSPACE_LINUX = "/workspace/cafe24_jsh77b1"


def build_child_script(session_name: str, keep_open: bool) -> str:
    """WSL bash 안에서 실행될 명령: 프로젝트 폴더로 이동 후 원격제어(Remote Control) claude 실행.

    원격제어는 슬래시 명령(`/rc`, `/remote-control`)을 시작 프롬프트로 넘겨서는
    켜지지 않는다(시작 인자 슬래시 명령은 자동 실행 안 됨). 실행 시점에 켜려면
    반드시 `--remote-control` 플래그를 써야 한다.
    session_name 이 있으면 원격제어 세션 이름으로 함께 전달한다.
    keep_open 이 True 면 claude 종료 후에도 bash 를 이어받아(exec) 창이 안 닫히게 한다.
    """
    rc = "claude --remote-control"
    if session_name:
        rc += f" '{session_name}'"
    script = f"cd '{WORKSPACE_LINUX}' && {rc}"
    if keep_open:
        script += "; exec bash"
    return script


def resolve_wt() -> str:
    """wt.exe 경로 확인. 없으면 안내 후 종료."""
    wt = shutil.which("wt.exe") or shutil.which("wt")
    if not wt:
        sys.exit(
            "[오류] wt.exe(Windows Terminal)를 찾을 수 없습니다.\n"
            "  - Microsoft Store 에서 'Windows Terminal' 을 설치하거나\n"
            "  - 이미 있다면 PATH 에 노출되는지 확인하세요.\n"
            "  (WSL interop 에서는 Start-Process/cmd start 로는 창이 보이지 않아\n"
            "   wt.exe 가 필요합니다.)"
        )
    return wt


def launch(session_name: str, new_window: bool, keep_open: bool) -> None:
    child_script = build_child_script(session_name, keep_open)
    wsl_args = ["wsl.exe", "-d", WSL_DISTRO, "--", "bash", "-lic", child_script]

    wt = resolve_wt()
    wt_args = [wt]
    if new_window:
        wt_args += ["-w", "new"]  # 기존 창에 탭이 아니라 독립된 새 창
    wt_args += wsl_args

    subprocess.Popen(wt_args)

    where = "새 창" if new_window else "새 탭"
    what = f"claude --remote-control '{session_name}'" if session_name else "claude --remote-control"
    print(f"[OK] Windows Terminal {where}에서 {what} 실행 요청 완료")


def main() -> None:
    p = argparse.ArgumentParser(
        description="새 Windows 터미널 창에서 claude 를 원격제어(Remote Control) 모드로 실행"
    )
    p.add_argument(
        "--name",
        default="",
        help="원격제어 세션 이름 (기본: 없음)",
    )
    p.add_argument(
        "--tab",
        action="store_true",
        help="새 창 대신 기존 Windows Terminal 에 새 탭으로 열기",
    )
    p.add_argument(
        "--no-keep",
        action="store_true",
        help="claude 종료 시 창/탭도 닫기 (기본: bash 로 이어받아 유지)",
    )
    args = p.parse_args()

    launch(
        session_name=args.name,
        new_window=not args.tab,
        keep_open=not args.no_keep,
    )


if __name__ == "__main__":
    main()
