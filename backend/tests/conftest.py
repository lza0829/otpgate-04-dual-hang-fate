import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _fresh_app(db_path: Path):
    os.environ["OTPGATE_DB"] = str(db_path)
    # 配置先重载，后续模块才能拿到新的 DB_PATH
    import app.config
    importlib.reload(app.config)
    import app.db
    importlib.reload(app.db)
    import app.services.common
    importlib.reload(app.services.common)
    import app.services.dispatch
    importlib.reload(app.services.dispatch)
    for mod in (
        "app.routers.audit",
        "app.routers.breaker",
        "app.routers.channels",
        "app.routers.dashboard",
        "app.routers.receipts",
        "app.routers.reconcile",
        "app.routers.route",
        "app.routers.sends",
        "app.routers.settings",
    ):
        importlib.import_module(mod)
        importlib.reload(importlib.import_module(mod))
    import app.seed
    importlib.reload(app.seed)
    import app.main
    importlib.reload(app.main)
    return app.main.app


@pytest.fixture()
def client(tmp_path):
    db_file = tmp_path / "otpgate-test.db"
    app = _fresh_app(db_file)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_file(tmp_path):
    return tmp_path / "otpgate-legacy.db"


def make_legacy_db(path: Path):
    """造一个 v0 旧库（无 refuse_reason、无 双挂拒投/拒投/双挂硬拒 约束）。"""
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE channels (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('主路','备路')),
          weight INTEGER NOT NULL DEFAULT 100,
          status TEXT NOT NULL DEFAULT '启用' CHECK(status IN ('启用','熔断中','停用')),
          concurrency_limit INTEGER NOT NULL DEFAULT 20,
          fail_threshold INTEGER NOT NULL DEFAULT 3,
          consecutive_fails INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE route_policy (
          id INTEGER PRIMARY KEY CHECK(id = 1),
          mode TEXT NOT NULL DEFAULT '权重' CHECK(mode IN ('权重','强制主路','强制备路')),
          updated_at TEXT NOT NULL
        );
        CREATE TABLE sends (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          serial TEXT NOT NULL UNIQUE, phone_mask TEXT NOT NULL,
          scene TEXT NOT NULL DEFAULT '登录',
          status TEXT NOT NULL DEFAULT '待投递'
            CHECK(status IN ('待投递','投递中','已送达','已失败','已切备')),
          channel_id INTEGER, failover_channel_id INTEGER,
          note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
          sent_at TEXT, finished_at TEXT
        );
        CREATE TABLE receipts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, send_id INTEGER NOT NULL,
          channel_id INTEGER NOT NULL,
          result TEXT NOT NULL CHECK(result IN ('成功','失败')),
          vendor_code TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE break_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL,
          action TEXT NOT NULL CHECK(action IN ('熔断','恢复','切备')),
          send_id INTEGER, created_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
          ref_id INTEGER, action TEXT NOT NULL,
          created_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO channels(code,name,role,weight,status) VALUES
          ('A','主','主路',80,'启用'),('B','备','备路',20,'启用');
        INSERT INTO route_policy(id,mode,updated_at) VALUES (1,'权重','t');
        INSERT INTO sends(serial,phone_mask,status,channel_id,created_at)
          VALUES ('验OLD1','138****0001','投递中',1,'t');
        INSERT INTO receipts(send_id,channel_id,result,created_at) VALUES (1,1,'成功','t');
        """
    )
    conn.commit()
    conn.close()


def channel_ids(client):
    rows = client.get("/api/通道").json()
    main = next(r["编号"] for r in rows if r["角色"] == "主路")
    backup = next(r["编号"] for r in rows if r["角色"] == "备路")
    return main, backup


def set_status(client, cid, status):
    r = client.patch(f"/api/通道/{cid}", json={"状态": status})
    assert r.status_code == 200, r.text


def create_send(client, phone="139****6602"):
    return client.post("/api/投递单", json={"手机掩码": phone, "场景": "登录"})


def latest_send(client):
    return client.get("/api/投递单").json()[0]


def set_legacy_switch(client, value):
    # 故意把废止项拨到任意值，验证它对判定毫无影响
    return client.put("/api/设置", json={"项": {"允许双挂硬投": value}})
