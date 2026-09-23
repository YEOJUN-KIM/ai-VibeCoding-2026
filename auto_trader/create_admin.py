"""웹과 분리된 단일 관리자 계정 생성 명령."""

from getpass import getpass

from .auth import create_admin
from .database import initialize


def main() -> None:
    initialize()
    print("자동매매 프로그램 관리자 계정을 생성합니다.")
    username = input("관리자 아이디: ").strip()
    password = getpass("비밀번호(12자 이상): ")
    confirmation = getpass("비밀번호 확인: ")
    if password != confirmation:
        raise SystemExit("비밀번호가 서로 일치하지 않습니다.")
    try:
        create_admin(username, password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("관리자 계정이 생성되었습니다.")


if __name__ == "__main__":
    main()
