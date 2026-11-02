"""Interactive first-run setup for the two local demo identities."""
from contextlib import closing
from getpass import getpass

from backend.app.repositories import legacy as db
from backend.app.security.auth import create_auth_schema, provision_user


def main():
    db.initialize_database()
    with closing(db.connect_db()) as c, c:
        create_auth_schema(c)
        if c.execute("SELECT COUNT(*) FROM local_users").fetchone()[0]:
            return
        print("首次使用：请设置账户用户和管理员的登录信息。")
        users = []
        for label, role in (("账户用户", "account_user"), ("管理员", "admin")):
            username = input(f"{label}用户名：").strip()
            password = getpass(f"{label}密码（至少8位）：")
            confirm = getpass("再次输入密码：")
            if password != confirm:
                raise ValueError("两次密码不一致，未创建任何用户")
            users.append((username, password, role))
        if users[0][0] == users[1][0]:
            raise ValueError("两类身份不能使用相同用户名")
        for username, password, role in users:
            provision_user(c, username, password, role)
    print("本地用户已创建。")


if __name__ == "__main__":
    main()
