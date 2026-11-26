import importlib
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import make_legacy_db

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def test_legacy_db_migrates_and_enforces_new_terminal(tmp_path):
    db_path = tmp_path / "legacy.db"
    make_legacy_db(db_path)

    os.environ["OTPGATE_DB"] = str(db_path)
    import app.config
    importlib.reload(app.config)
    import app.db
    importlib.reload(app.db)
    app.db.init_db()

    import sqlite3
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sends)").fetchall()]
    assert "refuse_reason" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 1
    # 旧数据保留
    assert conn.execute("SELECT serial FROM sends WHERE id=1").fetchone()[0] == "验OLD1"
    # 新终态可写、旧 CHECK 已放宽
    conn.execute(
        "UPDATE sends SET status='双挂拒投', refuse_reason='x' WHERE id=1"
    )
    conn.commit()
    conn.close()

    # 重新整栈启动，接口可用
    import app.services.common
    importlib.reload(app.services.common)
    import app.services.dispatch
    importlib.reload(app.services.dispatch)
    for mod in (
        "app.routers.audit", "app.routers.breaker", "app.routers.channels",
        "app.routers.dashboard", "app.routers.receipts", "app.routers.reconcile",
        "app.routers.route", "app.routers.sends", "app.routers.settings",
    ):
        importlib.reload(importlib.import_module(mod))
    import app.seed
    importlib.reload(app.seed)
    import app.main
    importlib.reload(app.main)
    with TestClient(app.main.app) as client:
        sends = client.get("/api/投递单").json()
        assert sends[0]["状态"] == "双挂拒投"
        # 迁移后的库上双挂判定依旧成立
        ov = client.get("/api/总览").json()
        assert "双挂拒投" in ov["分布"]
