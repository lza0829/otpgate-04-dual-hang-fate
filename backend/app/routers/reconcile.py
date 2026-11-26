from __future__ import annotations
from fastapi import APIRouter
from app.db import db

router = APIRouter(tags=["对账"])


@router.get("/对账")
def reconcile():
    with db() as conn:
        sends = conn.execute("SELECT COUNT(*) c FROM sends").fetchone()["c"]
        ok = conn.execute("SELECT COUNT(*) c FROM sends WHERE status='已送达'").fetchone()["c"]
        fail = conn.execute("SELECT COUNT(*) c FROM sends WHERE status='已失败'").fetchone()["c"]
        fo = conn.execute("SELECT COUNT(*) c FROM sends WHERE status='已切备'").fetchone()["c"]
        double_down = conn.execute(
            "SELECT COUNT(*) c FROM sends WHERE status='双挂拒投'"
        ).fetchone()["c"]
        hanging = conn.execute(
            "SELECT COUNT(*) c FROM sends WHERE status IN ('待投递','投递中','已切备')"
        ).fetchone()["c"]
        receipts = conn.execute("SELECT COUNT(*) c FROM receipts").fetchone()["c"]
        receipt_ok = conn.execute("SELECT COUNT(*) c FROM receipts WHERE result='成功'").fetchone()["c"]
        receipt_refuse = conn.execute(
            "SELECT COUNT(*) c FROM receipts WHERE result='拒投'"
        ).fetchone()["c"]
        breaks = conn.execute("SELECT COUNT(*) c FROM break_events WHERE action='切备'").fetchone()["c"]
        double_breaks = conn.execute(
            "SELECT COUNT(*) c FROM break_events WHERE action='双挂硬拒'"
        ).fetchone()["c"]
        # 双挂挂起桶：每张双挂拒投单按通道数留痕，桶数应等于拒投回执数
        dd_sends = conn.execute(
            "SELECT COUNT(*) c FROM sends WHERE status='双挂拒投'"
        ).fetchone()["c"]
        channel_total = conn.execute("SELECT COUNT(*) c FROM channels").fetchone()["c"]
    expected_refuse_receipts = dd_sends * channel_total
    return {
        "投递申请数": sends,
        "已送达": ok,
        "已失败": fail,
        "已切备在途": fo,
        "双挂拒投": double_down,
        "挂起桶": hanging,
        "回执条数": receipts,
        "回执成功": receipt_ok,
        "拒投回执": receipt_refuse,
        "切备次数": breaks,
        "双挂硬拒事件": double_breaks,
        "送达与回执差": ok - receipt_ok,
        # 同刷对照：双挂桶、拒投回执、硬拒事件必须三者自洽，差异恒为 0
        "双挂回执差": receipt_refuse - expected_refuse_receipts,
        "双挂事件差": double_breaks - expected_refuse_receipts,
    }
