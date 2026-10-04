"""회원 권한 바꾸기 (첫 관리자 지정용). 그다음부터는 관리자 화면(/admin/users)에서 바꿀 수 있다.

docker compose exec app python -m scripts.set_permit 아이디 ADMIN
docker compose exec app python -m scripts.set_permit 아이디 USER
"""
import sys

from sqlalchemy import select

from server.db import SessionLocal
from server.models import PERMITS, User


def main(argv):
    if len(argv) != 2 or argv[1].upper() not in PERMITS:
        print(__doc__)
        return 2
    username, permit = argv[0], argv[1].upper()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username, User.save_status == "Y"))
        if not user:
            print(f"사용 중인 회원 중에 아이디 '{username}'이(가) 없습니다.")
            return 1
        user.permit = permit
        db.commit()
        print(f"{user.nickname}({username})의 권한을 {permit}({PERMITS[permit]})(으)로 바꿨습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
