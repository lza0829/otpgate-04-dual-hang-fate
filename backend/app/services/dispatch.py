"""双挂判定与投递写口的全站唯一权威。

架构定案（只此一臂，全站同命运）：
  双挂必须硬拒——主路、备路皆不可用（熔断中/停用任意组合）时，投递整笔失败：
  * 验码单不进入「投递中」，直接落终态「双挂拒投」，永不送达；
  * 每个不可用通道各留一张「拒投」回执（可核事实，硬投不存在，故无任何硬投标）；
  * 路况桶、轧差双挂桶、熔断事件（动作=双挂硬拒）、事件痕同事务同刷；
  * 「允许双挂硬投」为已废止遗留项，任何代码路径都不得读取它来改变判定。

读侧（探针 /api/投递探针）与写侧（创建、投递、失败熔断）共用本模块的 evaluate，
因此读侧双挂时能不能投可随时重放，结论必然一致。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.db import DISPATCHABLE_STATUSES, TERMINAL_STATUSES
from app.services.common import audit, now_str

REFUSE_REASON = "主备双挂：无可用通道，整笔硬拒"
DEPRECATED_SETTING = "允许双挂硬投"
DEPRECATED_NOTE = "已废止：双挂一律硬拒，本项不参与判定"


class Reject:
    """双挂硬拒载体（非异常）：服务层在写事务内返回它，
    由路由在事务提交后再转成 HTTP 409，保证拒投事实绝不被回滚。"""

    def __init__(self, payload: dict):
        self.payload = payload


def list_channels(conn) -> list:
    return conn.execute("SELECT * FROM channels ORDER BY role, id").fetchall()


def evaluate(conn) -> dict:
    """双挂判定矩阵的唯一实现。覆盖：主备皆熔断、一熔一停、皆停用及渠规开闭组合
    （渠规开关已废止，开闭组合结论一致——此处刻意不读任何设置项）。"""
    chans = list_channels(conn)
    roles: dict[str, list] = {"主路": [], "备路": []}
    for c in chans:
        roles.setdefault(c["role"], []).append(c)
    primary = roles["主路"]
    backup = roles["备路"]
    primary_up = any(c["status"] == "启用" for c in primary)
    backup_up = any(c["status"] == "启用" for c in backup)
    double_down = bool(chans) and not primary_up and not backup_up

    def bucket(group):
        if not group:
            return "缺失"
        if any(c["status"] == "启用" for c in group):
            return "可用"
        if any(c["status"] == "熔断中" for c in group) and any(
            c["status"] == "停用" for c in group
        ):
            return "半熔半停"
        if any(c["status"] == "熔断中" for c in group):
            return "熔断"
        return "停用"

    return {
        "双挂": double_down,
        "可投递": not double_down,
        "主路况": bucket(primary),
        "备路况": bucket(backup),
        "硬投标": None,  # 硬投一臂已砍，全站永远不产生硬投标
        "依据": "主备皆不可用即硬拒（渠规允许双挂硬投已废止，不参与判定）",
        "通道": [
            {
                "编号": c["id"],
                "名称": c["name"],
                "角色": c["role"],
                "状态": c["status"],
            }
            for c in chans
        ],
    }


def probe_send(conn, send_row) -> dict:
    """读侧重放：某张验码单此刻能不能投、走哪条路、双挂怎么判。"""
    verdict = evaluate(conn)
    if send_row is None:
        return {"存在": False, "可投递": False, "判定": verdict}
    if send_row["status"] in TERMINAL_STATUSES:
        return {
            "存在": True,
            "投递单号": send_row["serial"],
            "状态": send_row["status"],
            "可投递": False,
            "原因": f"终态 {send_row['status']} 不可再投",
            "判定": verdict,
        }
    target = pick_target(conn, send_row)
    if target is None:
        # 原定通道不可用但系统未双挂：投递前按当前渠规改道，不留半截状态
        target = pick_routed_channel(conn)
    if target is None:
        # 能走到这里必然是全局双挂，与 evaluate 同源
        verdict["双挂"] = True
        verdict["可投递"] = False
        return {
            "存在": True,
            "投递单号": send_row["serial"],
            "状态": send_row["status"],
            "可投递": False,
            "目标通道": None,
            "原因": REFUSE_REASON,
            "判定": verdict,
        }
    return {
        "存在": True,
        "投递单号": send_row["serial"],
        "状态": send_row["status"],
        "可投递": True,
        "目标通道": target["name"],
        "判定": verdict,
    }


def pick_routed_channel(conn):
    """创建验码单时按渠规模式选路（仅在启用通道中选）。"""
    policy = conn.execute("SELECT mode FROM route_policy WHERE id=1").fetchone()["mode"]
    if policy == "强制主路":
        return conn.execute(
            "SELECT * FROM channels WHERE role='主路' AND status='启用' LIMIT 1"
        ).fetchone()
    if policy == "强制备路":
        return conn.execute(
            "SELECT * FROM channels WHERE role='备路' AND status='启用' LIMIT 1"
        ).fetchone()
    return conn.execute(
        """
        SELECT * FROM channels WHERE status='启用'
        ORDER BY weight DESC, id ASC LIMIT 1
        """
    ).fetchone()


def pick_target(conn, send_row):
    """投递写口的目标通道：切备单优先备路，否则用原选路通道。
    目标必须启用；无启用通道即双挂。"""
    target_id = send_row["failover_channel_id"] or send_row["channel_id"]
    if target_id is None:
        return None
    ch = conn.execute("SELECT * FROM channels WHERE id=?", (target_id,)).fetchone()
    if ch is None or ch["status"] != "启用":
        return None
    return ch


def _record_double_down(conn, send_id: int, serial: str, source: str) -> dict:
    """双挂硬拒的同事务事实落库：终态 + 拒投回执×每通道 + 熔断事件 + 事件痕。
    全部在调用方的写事务内完成，任一失败整笔回滚，不留半截投递中。"""
    now = now_str()
    conn.execute(
        "UPDATE sends SET status='双挂拒投', refuse_reason=?, finished_at=? WHERE id=?",
        (REFUSE_REASON, now, send_id),
    )
    chans = list_channels(conn)
    for c in chans:
        conn.execute(
            """
            INSERT INTO receipts(send_id, channel_id, result, vendor_code, created_at)
            VALUES (?, ?, '拒投', ?, ?)
            """,
            (send_id, c["id"], f"DOUBLE_DOWN:{c['status']}", now),
        )
        conn.execute(
            """
            INSERT INTO break_events(channel_id, action, send_id, created_at, note)
            VALUES (?, '双挂硬拒', ?, ?, ?)
            """,
            (c["id"], send_id, now, f"{source}｜{c['role']}{c['status']}"),
        )
    audit(conn, "投递单", send_id, "双挂硬拒", f"{serial}｜{source}")
    return {
        "结果": "双挂拒投",
        "投递单号": serial,
        "原因": REFUSE_REASON,
        "拒投回执数": len(chans),
        "终态": "双挂拒投",
        "硬投标": None,
    }


def create_send(conn, serial: str, phone_mask: str, scene: str, note: str) -> dict:
    """创建即定命运：双挂时也落一张可核的双挂拒投单，而非静默丢单。"""
    now = now_str()
    verdict = evaluate(conn)
    if verdict["双挂"]:
        cur = conn.execute(
            """
            INSERT INTO sends(serial, phone_mask, scene, status, note,
                              refuse_reason, created_at, finished_at)
            VALUES (?, ?, ?, '双挂拒投', ?, ?, ?, ?)
            """,
            (serial, phone_mask, scene, note, REFUSE_REASON, now, now),
        )
        sid = cur.lastrowid
        payload = _record_double_down(conn, sid, serial, "投递入口创建")
        # 创建路径的审计已在 _record_double_down 内完成
        return Reject(payload)

    ch = pick_routed_channel(conn)
    if ch is None:
        # 理论不可达：非双挂必有启用通道
        raise HTTPException(500, "选路与双挂判定不一致")
    cur = conn.execute(
        """
        INSERT INTO sends(serial, phone_mask, scene, status, channel_id, note, created_at)
        VALUES (?, ?, ?, '待投递', ?, ?, ?)
        """,
        (serial, phone_mask, scene, ch["id"], note, now),
    )
    sid = cur.lastrowid
    audit(conn, "投递单", sid, "创建", serial)
    return {"编号": sid, "投递单号": serial, "通道": ch["name"]}


def dispatch(conn, send_row) -> dict:
    """投递写口（唯一）。条件 UPDATE 保证同一验码单并发投递只有一笔胜出，
    竞态落败方拿 409，不会出现双结局；双挂整笔硬拒、同事务留痕。"""
    sid = send_row["id"]
    serial = send_row["serial"]
    if send_row["status"] not in DISPATCHABLE_STATUSES:
        raise HTTPException(400, f"当前状态不可投递：{send_row['status']}")

    verdict = evaluate(conn)
    if verdict["双挂"]:
        return Reject(_record_double_down(conn, sid, serial, "投递写口"))

    ch = pick_target(conn, send_row)
    if ch is None:
        # 非双挂：单路不可用属另立的切备命运，投递前按渠规改道启用通道，绝不误硬拒
        ch = pick_routed_channel(conn)
    if ch is None:
        # 防御性兜底：与 evaluate 双挂结论一致才可能到达
        return Reject(_record_double_down(conn, sid, serial, "投递写口兜底"))

    inflight = conn.execute(
        "SELECT COUNT(*) c FROM sends WHERE channel_id=? AND status='投递中'",
        (ch["id"],),
    ).fetchone()["c"]
    if inflight >= ch["concurrency_limit"]:
        raise HTTPException(400, "通道并发已满")

    now = now_str()
    cur = conn.execute(
        """
        UPDATE sends
        SET status='投递中', channel_id=?, sent_at=?
        WHERE id=? AND status IN ('待投递','已切备')
        """,
        (ch["id"], now, sid),
    )
    if cur.rowcount != 1:
        # 并发争用：状态已被另一笔写口改掉，收口为单结局
        raise HTTPException(409, "投递争用：验码单状态已变更，请刷新后重试")
    audit(conn, "投递单", sid, "投递", ch["name"])
    return {"结果": "投递中", "通道": ch["name"], "投递单号": serial}


def double_down_after_break(conn, send_id: int, serial: str, source: str) -> dict:
    """熔断失败路径复用同一硬拒写口，保证两处双挂命运完全相同。"""
    return _record_double_down(conn, send_id, serial, source)
