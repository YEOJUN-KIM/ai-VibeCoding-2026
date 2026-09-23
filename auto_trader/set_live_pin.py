"""웹과 분리된 LIVE용 6자리 PIN 설정 명령."""

from getpass import getpass

from .auth import set_live_pin
from .database import initialize


def main() -> None:
    initialize()
    print("LIVE 활성화에 사용할 숫자 6자리 PIN을 설정합니다.")
    pin = getpass("LIVE PIN: ")
    confirmation = getpass("LIVE PIN 확인: ")
    if pin != confirmation:
        raise SystemExit("PIN이 서로 일치하지 않습니다.")
    try:
        set_live_pin(pin)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("LIVE PIN이 안전하게 변경되었습니다. 기존 LIVE 인증은 만료되었습니다.")


if __name__ == "__main__":
    main()
