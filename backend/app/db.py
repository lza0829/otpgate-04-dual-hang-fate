from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('主路','备路')),
  weight INTEGER NOT NULL DEFAULT 100,
  status TEXT NOT NULL DEFAULT '启用' CHECK(status IN ('启用','熔断中','停用')),
  concurrency_limit INTEGER NOT NULL DEFAULT 20,
  fail_threshold INTEGER NOT NULL DEFAULT 3,
  consecutive_fails INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS route_policy (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  mode TEXT NOT NULL DEFAULT '权重' CHECK(mode IN ('权重','强制主路','强制备路')),
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serial TEXT NOT NULL UNIQUE,
  phone_mask TEXT NOT NULL,
  scene TEXT NOT NULL DEFAULT '登录',
  status TEXT NOT NULL DEFAULT '待投递'
    CHECK(status IN ('待投递','投递中','已送达','已失败','已切备','双挂拒投')),
  channel_id INTEGER,
  failover_channel_id INTEGER,
  note TEXT NOT NULL DEFAULT '',
  refuse_reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  sent_at TEXT,
  finished_at TEXT,
  FOREIGN KEY(channel_id) REFERENCES channels(id),
  FOREIGN KEY(failover_channel_id) REFERENCES channels(id)
);
CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  send_id INTEGER NOT NULL,
  channel_id INTEGER NOT NULL,
  result TEXT NOT NULL CHECK(result IN ('成功','失败','拒投')),
  vendor_code TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(send_id) REFERENCES sends(id),
  FOREIGN KEY(channel_id) REFERENCES channels(id)
);
CREATE TABLE IF NOT EXISTS break_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('熔断','恢复','切备','双挂硬拒')),
  send_id INTEGER,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(channel_id) REFERENCES channels(id),
  FOREIGN KEY(send_id) REFERENCES sends(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
"""

# 终态机：双挂拒投与已送达/已失败同为终态，任何写口不得再改写
TERMINAL_STATUSES = ("已送达", "已失败", "双挂拒投")
DISPATCHABLE_STATUSES = ("待投递", "已切备")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def db(exclusive: bool = False):
    """写口统一走 exclusive=True：BEGIN IMMEDIATE 立即拿写锁，
    渠规翻转、通道启停与投递争用在 SQLite 层串行收口，杜绝双结局。"""
    conn = connect()
    try:
        if exclusive:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _needs_migration(conn: sqlite3.Connection) -> bool:
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sends'"
    ).fetchone()
    if table is None:
        return False
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sends)").fetchall()}
    if "refuse_reason" not in cols or "双挂拒投" not in (table["sql"] or ""):
        return True
    receipt_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='receipts'"
    ).fetchone()["sql"]
    if "拒投" not in receipt_sql:
        return True
    break_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='break_events'"
    ).fetchone()["sql"]
    if "双挂硬拒" not in break_sql:
        return True
    return False


def _migrate(conn: sqlite3.Connection) -> None:
    # foreign_keys 不能在事务内切换，先提交再关
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ALTER TABLE sends RENAME TO sends_old")
    conn.execute(
        """
        CREATE TABLE sends (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          serial TEXT NOT NULL UNIQUE,
          phone_mask TEXT NOT NULL,
          scene TEXT NOT NULL DEFAULT '登录',
          status TEXT NOT NULL DEFAULT '待投递'
            CHECK(status IN ('待投递','投递中','已送达','已失败','已切备','双挂拒投')),
          channel_id INTEGER,
          failover_channel_id INTEGER,
          note TEXT NOT NULL DEFAULT '',
          refuse_reason TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          sent_at TEXT,
          finished_at TEXT,
          FOREIGN KEY(channel_id) REFERENCES channels(id),
          FOREIGN KEY(failover_channel_id) REFERENCES channels(id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO sends(
          id, serial, phone_mask, scene, status, channel_id, failover_channel_id,
          note, refuse_reason, created_at, sent_at, finished_at
        )
        SELECT id, serial, phone_mask, scene, status, channel_id, failover_channel_id,
               note, '', created_at, sent_at, finished_at FROM sends_old
        """
    )
    conn.execute("DROP TABLE sends_old")

    conn.execute("ALTER TABLE receipts RENAME TO receipts_old")
    conn.execute(
        """
        CREATE TABLE receipts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          send_id INTEGER NOT NULL,
          channel_id INTEGER NOT NULL,
          result TEXT NOT NULL CHECK(result IN ('成功','失败','拒投')),
          vendor_code TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          FOREIGN KEY(send_id) REFERENCES sends(id),
          FOREIGN KEY(channel_id) REFERENCES channels(id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO receipts(id, send_id, channel_id, result, vendor_code, created_at)
        SELECT id, send_id, channel_id, result, vendor_code, created_at FROM receipts_old
        """
    )
    conn.execute("DROP TABLE receipts_old")

    conn.execute("ALTER TABLE break_events RENAME TO break_events_old")
    conn.execute(
        """
        CREATE TABLE break_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          channel_id INTEGER NOT NULL,
          action TEXT NOT NULL CHECK(action IN ('熔断','恢复','切备','双挂硬拒')),
          send_id INTEGER,
          created_at TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          FOREIGN KEY(channel_id) REFERENCES channels(id),
          FOREIGN KEY(send_id) REFERENCES sends(id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO break_events(id, channel_id, action, send_id, created_at, note)
        SELECT id, channel_id, action, send_id, created_at, note FROM break_events_old
        """
    )
    conn.execute("DROP TABLE break_events_old")

    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError(f"迁移后外键校验失败：{bad}")
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()


def init_db() -> None:
    conn = connect()
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sends'"
        ).fetchone()
        if exists is None:
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
            return
        if _needs_migration(conn):
            _migrate(conn)
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
