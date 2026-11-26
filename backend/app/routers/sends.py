from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list
from app.services import dispatch as gate

router = APIRouter(tags=["投递"])


class SendCreate(BaseModel):
    手机掩码: str = Field(min_length=3)
    场景: str = "登录"
    备注: str = ""


@router.get("/投递探针")
def probe_all():
    """读侧重放：当前双挂判定矩阵 + 每张非终态验码单能不能投。
    与投递写口共用同一个 evaluate，结论可随时重放。"""
    with db() as conn:
        verdict = gate.evaluate(conn)
        rows = conn.execute("SELECT * FROM sends ORDER BY id DESC").fetchall()
        probes = [gate.probe_send(conn, s) for s in rows]
    return {"判定": verdict, "验码单探针": probes}


@router.get("/投递单")
def list_sends():
    with db() as conn:
        rows = rows_to_list(
            conn.execute(
                """
                SELECT s.*, c.name AS channel_name, f.name AS failover_name
                FROM sends s
                LEFT JOIN channels c ON c.id = s.channel_id
                LEFT JOIN channels f ON f.id = s.failover_channel_id
                ORDER BY s.id DESC
                """
            ).fetchall()
        )
    return [
        {
            "编号": r["id"],
            "投递单号": r["serial"],
            "手机掩码": r["phone_mask"],
            "场景": r["scene"],
            "状态": r["status"],
            "通道": r["channel_name"] or "-",
            "切备通道": r["failover_name"] or "-",
            "拒投原因": r["refuse_reason"],
            "创建时间": r["created_at"],
            "备注": r["note"],
        }
        for r in rows
    ]


@router.post("/投递单")
def create_send(body: SendCreate):
    serial = f"验{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    # 写事务立即上锁：与渠规翻转、通道启停、其它投递串行
    with db(exclusive=True) as conn:
        result = gate.create_send(conn, serial, body.手机掩码, body.场景, body.备注)
    # 事务已提交后再转 409：拒投终态与留痕绝不随异常回滚
    if isinstance(result, gate.Reject):
        raise HTTPException(409, result.payload)
    return result


@router.get("/投递单/{sid}/探针")
def probe_one(sid: int):
    with db() as conn:
        s = conn.execute("SELECT * FROM sends WHERE id=?", (sid,)).fetchone()
        result = gate.probe_send(conn, s)
        if not result["存在"]:
            raise HTTPException(404, "投递单不存在")
    return result


@router.post("/投递单/{sid}/投递")
def dispatch_send(sid: int):
    with db(exclusive=True) as conn:
        s = conn.execute("SELECT * FROM sends WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "投递单不存在")
        result = gate.dispatch(conn, s)
    if isinstance(result, gate.Reject):
        raise HTTPException(409, result.payload)
    return result
