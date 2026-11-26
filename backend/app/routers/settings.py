from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import db, rows_to_list
from app.services.common import audit
from app.services.dispatch import DEPRECATED_NOTE, DEPRECATED_SETTING

router = APIRouter(tags=["设置"])


class SettingsBody(BaseModel):
    项: dict[str, str]


@router.get("/设置")
def get_settings():
    with db() as conn:
        rows = rows_to_list(conn.execute("SELECT key, value FROM settings").fetchall())
    values = {r["key"]: r["value"] for r in rows}
    # 即使库里还留着这个键，也显式声明它不参与任何判定
    if DEPRECATED_SETTING not in values:
        values[DEPRECATED_SETTING] = "否"
    return {
        "项": values,
        "废止项": {DEPRECATED_SETTING: DEPRECATED_NOTE},
    }


@router.put("/设置")
def put_settings(body: SettingsBody):
    with db(exclusive=True) as conn:
        for k, v in body.项.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, v),
            )
        note = ",".join(body.项.keys())
        if DEPRECATED_SETTING in body.项:
            note += f"（{DEPRECATED_SETTING}已废止，不参与双挂判定）"
        audit(conn, "设置", None, "批量更新", note)
    return {"结果": "已更新", "废止项": [DEPRECATED_SETTING]}
