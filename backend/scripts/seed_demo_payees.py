"""Add the three fictional recipients for the local demo account once."""

from contextlib import closing

from backend.app.repositories import legacy as db
from backend.app.services.payees import seed_demo_payees


def main() -> None:
    with closing(db.connect_db()) as connection, connection:
        added = seed_demo_payees(connection)
    print("新增常用收款人：" + ("、".join(added) if added else "无（已存在）"))


if __name__ == "__main__":
    main()
