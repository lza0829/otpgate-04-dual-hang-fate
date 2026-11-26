"""双挂命运同一对照：渠规项、双挂判定、硬投标（恒无）、投递入口、验码单终态、
路况桶、轧差双挂桶、事件痕、启停灯探针同刷；单路熔断切备另立。"""
from concurrent.futures import ThreadPoolExecutor

from conftest import channel_ids, create_send, latest_send, set_legacy_switch, set_status


def _assert_double_down_facts(client, sid, serial, source_hint=None):
    """一次把所有面的同一成败核齐。"""
    # 验码单终态
    send = next(s for s in client.get("/api/投递单").json() if s["编号"] == sid)
    assert send["状态"] == "双挂拒投"
    assert send["拒投原因"]
    # 永不进投递中/已送达
    assert send["状态"] not in ("投递中", "已送达", "待投递", "已切备")

    # 回执：每个通道一张拒投回执，没有任何成功/在途痕迹
    receipts = [r for r in client.get("/api/回执").json() if r["投递单号"] == serial]
    assert receipts, "双挂拒投必须留下可核拒投回执"
    assert all(r["结果"] == "拒投" for r in receipts)
    assert len(receipts) == 2

    # 熔断事件：必有双挂硬拒动作（熔断路径还会同时留一条熔断事件，属正常）
    events = [e for e in client.get("/api/熔断事件").json() if e["投递单"] == sid]
    assert events, "双挂硬拒必须留下可核事件"
    assert any(e["动作"] == "双挂硬拒" for e in events)
    assert all(e["动作"] in ("双挂硬拒", "熔断", "切备") for e in events)

    # 事件痕
    audits = client.get("/api/审计").json()
    assert any(a["动作"] == "双挂硬拒" and a["关联"] == sid for a in audits)

    # 路况态势桶
    ov = client.get("/api/总览").json()
    assert ov["分布"]["双挂拒投"] >= 1
    assert ov["分布"]["已送达"] == 0
    assert ov["双挂判定"]["双挂"] is True
    assert ov["双挂判定"]["可投递"] is False
    assert ov["双挂判定"]["硬投标"] is None
    assert any(r["编号"] == sid for r in ov["双挂拒投单"])

    # 轧差双挂桶自洽
    rec = client.get("/api/对账").json()
    assert rec["双挂拒投"] >= 1
    assert rec["双挂回执差"] == 0
    assert rec["双挂事件差"] == 0
    assert rec["送达与回执差"] == 0

    # 读侧探针可重放，结论与写口一致
    probe = client.get(f"/api/投递单/{sid}/探针").json()
    assert probe["可投递"] is False
    assert probe["判定"]["双挂"] is True
    assert probe["判定"]["硬投标"] is None


def test_double_down_dispatch_matrix(client):
    """主备皆熔断、一熔一停、皆停用三种矩阵 + 渠规开闭组合，全部硬拒。"""
    main_id, backup_id = channel_ids(client)
    matrix = [
        ("熔断中", "熔断中"),
        ("熔断中", "停用"),
        ("停用", "熔断中"),
        ("停用", "停用"),
    ]
    for switch in ("是", "否"):
        set_legacy_switch(client, switch)
        for ms, bs in matrix:
            # 先恢复再摆矩阵
            client.post(f"/api/通道/{main_id}/恢复")
            client.post(f"/api/通道/{backup_id}/恢复")
            set_status(client, main_id, ms)
            set_status(client, backup_id, bs)

            r = create_send(client)
            # 创建入口即硬拒
            assert r.status_code == 409, r.text
            payload = r.json()["detail"]
            assert payload["结果"] == "双挂拒投"
            assert payload["硬投标"] is None
            assert payload["终态"] == "双挂拒投"
            sid = next(
                s["编号"]
                for s in client.get("/api/投递单").json()
                if s["投递单号"] == payload["投递单号"]
            )
            _assert_double_down_facts(client, sid, payload["投递单号"])

            # 终态后再点投递：被终态机拦住，不会产生第二结局
            again = client.post(f"/api/投递单/{sid}/投递")
            assert again.status_code == 400
            # 写回执也被拦
            rcpt = client.post(f"/api/投递单/{sid}/回执", json={"结果": "成功"})
            assert rcpt.status_code == 400
            # 事实没有被污染
            _assert_double_down_facts(client, sid, payload["投递单号"])


def test_dispatch_entry_double_down_after_pending_created(client):
    """先在可用时建待投递单，再双挂，投递写口必须整笔硬拒而非半截投递中。"""
    main_id, backup_id = channel_ids(client)
    r = create_send(client)
    assert r.status_code == 200, r.text
    sid = r.json()["编号"]
    serial = r.json()["投递单号"]

    set_status(client, main_id, "熔断中")
    set_status(client, backup_id, "停用")

    probe = client.get(f"/api/投递单/{sid}/探针").json()
    assert probe["可投递"] is False
    assert probe["判定"]["双挂"] is True

    d = client.post(f"/api/投递单/{sid}/投递")
    assert d.status_code == 409
    assert d.json()["detail"]["结果"] == "双挂拒投"
    _assert_double_down_facts(client, sid, serial)

    # 半截脏投核对：没有投递中残留、没有成功回执
    send = next(s for s in client.get("/api/投递单").json() if s["编号"] == sid)
    assert send["状态"] == "双挂拒投"


def test_half_dirty_no_traceless_delivery(client):
    """硬拒路径上任何失败都整笔回滚：不得出现双挂已投出却无硬投可核标。"""
    main_id, backup_id = channel_ids(client)
    r = create_send(client)
    sid = r.json()["编号"]
    serial = r.json()["投递单号"]
    set_status(client, main_id, "停用")
    set_status(client, backup_id, "停用")

    d = client.post(f"/api/投递单/{sid}/投递")
    assert d.status_code == 409

    # 全库不变量扫描：双挂单既无投递中/送达状态，也必有等量拒投回执与硬拒事件
    sends = client.get("/api/投递单").json()
    dd = [s for s in sends if s["状态"] == "双挂拒投"]
    for s in dd:
        assert s["状态"] != "投递中"
        rcpts = [x for x in client.get("/api/回执").json() if x["投递单号"] == s["投递单号"]]
        assert rcpts and all(x["结果"] == "拒投" for x in rcpts)
    rec = client.get("/api/对账").json()
    assert rec["双挂回执差"] == 0
    assert rec["双挂事件差"] == 0
    # 已送达与成功回执必须始终对得上，不存在无痕迹送达
    assert rec["送达与回执差"] == 0


def test_policy_flip_vs_dispatch_contention(client):
    """渠规翻转/启停灯与投递按钮并发：写锁串行收口，只有一个结局。"""
    main_id, backup_id = channel_ids(client)
    r = create_send(client)
    sid = r.json()["编号"]

    def flip():
        client.put("/api/选路", json={"模式": "强制主路"})
        set_status(client, main_id, "熔断中")
        set_status(client, backup_id, "熔断中")

    def dispatch():
        return client.post(f"/api/投递单/{sid}/投递")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(dispatch) for _ in range(7)] + [pool.submit(flip)]
        results = [f.result() for f in futs if f is not futs[-1]]

    final = next(s for s in client.get("/api/投递单").json() if s["编号"] == sid)
    # 终态唯一：要么投递中（翻转落锁前胜出），要么双挂拒投（翻转先胜出）
    assert final["状态"] in ("投递中", "双挂拒投")
    if final["状态"] == "双挂拒投":
        # 硬拒结局必须全套留痕，且至少一笔投递请求被 409 硬拒
        _assert_double_down_facts(client, sid, final["投递单号"])
        assert any(x.status_code == 409 for x in results)
    else:
        # 投递中结局：绝无拒投痕迹混入
        receipts = [x for x in client.get("/api/回执").json() if x["投递单号"] == final["投递单号"]]
        assert all(x["结果"] != "拒投" for x in receipts)
    # 同一验码单并发投递，最多一笔 200 成功，其余争用/硬拒，无双活
    codes = sorted(x.status_code for x in results)
    assert codes.count(200) <= 1


def test_concurrent_double_dispatch_single_outcome(client):
    """同一待投递单并发点投递：条件 UPDATE 保证最多一笔进入投递中。"""
    main_id, _ = channel_ids(client)
    r = create_send(client)
    sid = r.json()["编号"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = [f.result().status_code for f in [
            pool.submit(lambda: client.post(f"/api/投递单/{sid}/投递")) for _ in range(8)
        ]]
    assert codes.count(200) == 1
    final = next(s for s in client.get("/api/投递单").json() if s["编号"] == sid)
    assert final["状态"] == "投递中"


def _trip_primary(client, until_terminal_sid=None):
    """主路阈值为 3：连开 3 张单子投递后记失败，把主路打到熔断。
    若 until_terminal_sid 的状态先变终态则提前返回。"""
    for _ in range(3):
        r = create_send(client)
        if r.status_code == 409:
            return  # 已双挂，创建入口硬拒
        sid = r.json()["编号"]
        client.post(f"/api/投递单/{sid}/投递")
        client.post(f"/api/投递单/{sid}/失败", json={"备注": "x"})
        if until_terminal_sid is not None:
            cur = next(s for s in client.get("/api/投递单").json() if s["编号"] == until_terminal_sid)
            if cur["状态"] in ("已切备", "双挂拒投"):
                return


def test_single_channel_break_failover_is_separate(client):
    """单路熔断切备另立：主路熔断、备路启用时走切备，不触发双挂。"""
    main_id, backup_id = channel_ids(client)
    # 在主路上累积失败到阈值；第三张单子触发熔断并切至启用中的备路
    _trip_primary(client)
    channels = client.get("/api/通道").json()
    main = next(c for c in channels if c["编号"] == main_id)
    backup = next(c for c in channels if c["编号"] == backup_id)
    assert main["状态"] == "熔断中"
    assert backup["状态"] == "启用"
    # 最近一张触发熔断的单子应已切备
    switched = [
        s for s in client.get("/api/投递单").json() if s["状态"] == "已切备"
    ]
    assert switched, "主路熔断且备路可用时必须切备"
    rec = client.get("/api/对账").json()
    assert rec["切备次数"] >= 1
    assert rec["双挂拒投"] == 0
    ov = client.get("/api/总览").json()
    assert ov["双挂判定"]["双挂"] is False


def test_breaker_path_double_down_same_fate(client):
    """熔断失败路径遇到双挂，与投递写口同一硬拒终态、同一套留痕。"""
    main_id, backup_id = channel_ids(client)
    # 先备路停用，再让主路累积失败到熔断 => 触发熔断的那笔无备可切，双挂硬拒
    set_status(client, backup_id, "停用")
    serials = []
    last = None
    for _ in range(3):
        r = create_send(client)
        assert r.status_code == 200
        sid = r.json()["编号"]
        serials.append(r.json()["投递单号"])
        client.post(f"/api/投递单/{sid}/投递")
        last = client.post(f"/api/投递单/{sid}/失败", json={"备注": "x"})
    final_serial = serials[-1]
    final = next(s for s in client.get("/api/投递单").json() if s["投递单号"] == final_serial)
    assert final["状态"] == "双挂拒投"
    assert last is not None and last.status_code == 409
    _assert_double_down_facts(client, final["编号"], final_serial)


def test_healthy_path_unaffected(client):
    """正常可用时投递、成功回执全链路不受双挂逻辑影响。"""
    r = create_send(client)
    sid = r.json()["编号"]
    d = client.post(f"/api/投递单/{sid}/投递")
    assert d.status_code == 200
    rc = client.post(f"/api/投递单/{sid}/回执", json={"结果": "成功", "厂商码": "OK"})
    assert rc.status_code == 200
    final = next(s for s in client.get("/api/投递单").json() if s["编号"] == sid)
    assert final["状态"] == "已送达"
    ov = client.get("/api/总览").json()
    assert ov["分布"]["双挂拒投"] == 0
    assert ov["双挂判定"]["双挂"] is False


def test_assigned_channel_down_reroutes_not_rejected(client):
    """原定通道停用但备路启用（非双挂）：投递前改道，属切备另立命运，不得硬拒。"""
    main_id, backup_id = channel_ids(client)
    r = create_send(client)
    assert r.status_code == 200
    sid = r.json()["编号"]
    # 仅停用主路，备路保持启用
    set_status(client, main_id, "停用")
    probe = client.get(f"/api/投递单/{sid}/探针").json()
    assert probe["判定"]["双挂"] is False
    assert probe["可投递"] is True
    d = client.post(f"/api/投递单/{sid}/投递")
    assert d.status_code == 200, d.text
    final = next(s for s in client.get("/api/投递单").json() if s["编号"] == sid)
    assert final["状态"] == "投递中"
    backup_name = next(c["名称"] for c in client.get("/api/通道").json() if c["编号"] == backup_id)
    assert final["通道"] == backup_name
    # 没有任何拒投痕迹
    assert all(x["结果"] != "拒投" for x in client.get("/api/回执").json())
    rec = client.get("/api/对账").json()
    assert rec["双挂拒投"] == 0


def test_probe_replay_matches_write_verdict(client):
    """读侧双挂时能不能投可重放：探针与写口同源。"""
    main_id, backup_id = channel_ids(client)
    r = create_send(client)
    sid = r.json()["编号"]
    before = client.get(f"/api/投递单/{sid}/探针").json()
    assert before["可投递"] is True
    set_status(client, main_id, "熔断中")
    set_status(client, backup_id, "熔断中")
    after = client.get("/api/投递探针").json()
    assert after["判定"]["双挂"] is True
    one = client.get(f"/api/投递单/{sid}/探针").json()
    assert one["可投递"] is False
    # 重放多次结论稳定
    again = client.get(f"/api/投递单/{sid}/探针").json()
    assert again["可投递"] == one["可投递"]
    # 写口兑现与探针预言一致
    d = client.post(f"/api/投递单/{sid}/投递")
    assert d.status_code == 409
