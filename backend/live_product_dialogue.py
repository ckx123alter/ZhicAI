"""Live DeepSeek regression for named-product and cashflow follow-ups.

Run from the project root: python -m backend.live_product_dialogue
Uses a temporary SQLite database and the real configured provider.
"""
import json
import os
import tempfile
from contextlib import closing
from pathlib import Path


def main() -> int:
    # Set the isolated database before importing modules that read its path.
    with tempfile.TemporaryDirectory(prefix="zhicai-live-product-") as folder:
        os.environ["ZHICAI_DATABASE_PATH"] = str(Path(folder) / "live.sqlite3")

        from fastapi.testclient import TestClient
        from .main import app
        from .app.config import load_settings
        from .app.repositories import legacy as db
        from .app.services.commands import create_command_schema, seed_account
        from .app.security.auth import create_auth_schema, provision_user
        from .tests.payee_seed import seed_test_payees

        settings = load_settings()
        if settings is None or settings.model != "deepseek-v4-pro":
            raise RuntimeError("请先在 backend/.env 配置 deepseek-v4-pro")

        db.initialize_database()
        with closing(db.connect_db()) as c, c:
            create_command_schema(c)
            seed_account(c)
            seed_test_payees(c)
            create_auth_schema(c)
            provision_user(c, "live-check", "local-test-password", "account_user")

        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={
                "username": "live-check", "password": "local-test-password"})
            assert login.status_code == 200, login.text
            client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
            for question in ("我想买理财", "我想买进取精选365天", "我想买进取精选365天"):
                response = client.post("/api/chat", json={"message": question})
                assert response.status_code == 200, response.text
                body = response.json()
                with closing(db.connect_db()) as c:
                    row = c.execute(
                        "SELECT intent_json FROM agent_audit ORDER BY rowid DESC LIMIT 1").fetchone()
                intent = json.loads(row["intent_json"])
                print(f"{question} => {body['text'].splitlines()[0]}")
                if question == "我想买理财":
                    assert body["evidence"].get("missing_fields") == ["产品风险等级"]
                else:
                    assert intent["intent"] == "products", intent
                    assert intent["product_id"] == "O" and intent["risk"] == 3, intent
                    assert body["evidence"].get("product_id") == "O", body["evidence"]
                    assert "哪个风险等级" not in body["text"], body["text"]
                    assert body["evidence"].get("reply_source") == "suitability_rule"
            before_reassessment = client.post("/api/chat", json={"message": "现在可以买吗"})
            assert before_reassessment.status_code == 200, before_reassessment.text
            before = before_reassessment.json()
            print(f"现在可以买吗（C2） => {before['text'].splitlines()[0]}")
            assert before["evidence"].get("reply_source") == "suitability_rule"
            assert before["evidence"].get("product_id") == "O"
            preview = client.post("/api/operations/preview",
                                  json={"kind": "risk", "answers": [3] * 10})
            assert preview.status_code == 200, preview.text
            confirmation = {"confirmed": True, "buffer_acknowledged": False}
            if preview.json().get("requires_challenge"):
                challenge = client.post(
                    f"/api/operations/{preview.json()['preview_id']}/challenge",
                    json={"password": "local-test-password"})
                assert challenge.status_code == 200, challenge.text
                confirmation["challenge_token"] = challenge.json()["challenge_token"]
            reassessment = client.post(
                f"/api/operations/{preview.json()['preview_id']}/confirm",
                json=confirmation)
            assert reassessment.status_code == 200, reassessment.text
            assert reassessment.json()["state"]["riskLevel"] == 3

            followup = client.post("/api/chat", json={"message": "现在可以买吗"})
            assert followup.status_code == 200, followup.text
            body = followup.json()
            print(f"现在可以买吗（C3） => {body['text']}")
            evidence = body["evidence"]
            assert evidence["product_id"] == "O", evidence
            assert evidence["cashflow_conflict"] is True, evidence
            assert evidence["min_purchase"] == 10000, evidence
            assert evidence["recommended_cap"] < 10000, evidence
            assert "起购金额" in body["text"] and "高于" in body["text"], body["text"]
            assert {"查看理财产品", "查看现金流预测"} == {
                action["label"] for action in body["actions"]}, body["actions"]
            purchase = client.post("/api/chat", json={
                "message": "我要申购进取精选365天10000元"})
            assert purchase.status_code == 200, purchase.text
            purchase_body = purchase.json()
            print(f"我要申购进取精选365天10000元 => {purchase_body['text']}")
            assert purchase_body["evidence"].get("product_id") == "O"
            assert purchase_body["evidence"].get("cashflow_conflict") is True
            assert "现金流" in purchase_body["text"]
            assert client.get("/api/account-state").json()["balance"] == 12000
    print("PASS: real DeepSeek multi-turn product dialogue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
