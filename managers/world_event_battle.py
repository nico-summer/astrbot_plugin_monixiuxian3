# managers/world_event_battle.py
"""世界事件「可见战斗过程」计算核心

对应版本 v3.10.0：把世界事件从「报名 →（黑盒）→ 结果」改造为
「报名 → 可见战斗过程 → 结果」。

设计约定（重要，改动前务必先读）：

1. **纯计算，不碰数据库、不碰玩家数据。** 本模块只负责推进战况并产出结构化
   战报；死亡落地（回生丹抵消 / DeathManager / 参与者结果写库）由
   ``WorldEventManager`` 负责，避免一个模块同时改状态与改玩家数据。

2. **总死亡率严格守恒。** 把单次死亡率 ``p`` 拆成 ``N`` 个节点时，每节点死亡率取
   ``p_node = 1 - (1 - p) ** (1 / N)``。N 个节点独立掷骰后，玩家整场存活的概率
   仍为 ``1 - p``。因此 v3.9.1 / v3.9.2 建立的「境界压制 + 战力减免」平衡无需重调，
   只是把一次骰子摊成了可见的若干次。

3. **「战意」是事件内的展示量，不是玩家真实 HP/MP。** 世界事件不会读写玩家的
   气血/真元。战意由每节点的「生存余量」推导：roll 越贴近死亡线，战意掉得越多。
   于是战意见底 ⇔ 离阵亡一步之遥，与后续节点是否阵亡在叙事上自洽，不会出现
   「战意 90% 然后突然暴毙」这种玩家一眼看穿的假过程。

4. **事实归代码，氛围归 AI。** 所有人名、数值、伤亡均由本模块按真实战况写入
   文案；AI 只输出不含人名数值的氛围句。这样 AI 无论怎么幻觉都不会伪造战果。
"""

import random
from typing import Dict, List, Optional

__all__ = [
    "SEP",
    "BOSS_LABELS",
    "node_count",
    "per_node_death_rate",
    "node_deadline",
    "init_state",
    "resolve_node",
    "apply_node_outcome",
    "boss_outcome",
    "boss_label",
    "describe_node",
    "summarize_state",
]

SEP = "━━━━━━━━━━━━━━━"

# 节点数按时长自动决定：<=45 分钟 3 回合、<=60 分钟 4 回合、更长 5 回合
NODE_PLAN = ((45, 3), (60, 4))
NODE_PLAN_LONG = 5
MAX_NODES = 8

# 敌方首领的称呼（按事件难度取用，纯氛围）
BOSS_LABELS = {
    "低阶": "妖兽首领",
    "中阶": "魔道首领",
    "高阶": "上古凶物",
    "史诗": "天魔化身",
}

# 战意低于该值视为「被重创」，会写进播报与个人战报
VIGOR_DOWN_THRESHOLD = 0.35

# 「异变」判定：每节点死亡率低于该值的玩家仍然阵亡时，说明他本该极其安全，
# 于是从成因池里抽一条离谱但可信的死因写进播报与个人战报。
# 这是「我很安全却死了」的唯一解释来源，避免玩家认定是系统随机坑人。
DRAMATIC_RISK_THRESHOLD = 0.05
DRAMATIC_CAUSES = (
    "为护住同门，硬接敌首全力一击",
    "被三名敌手合围，力竭被围",
    "为掩护道友撤退，独自殿后断敌",
    "误入敌阵深处，被重重围困",
    "敌首自爆本源，硬生生将你卷入其中",
)


# ==================== 节点规划 ====================


def node_count(duration_minutes: int, override: int = 0) -> int:
    """战斗节点数：override > 0 时直接用配置值，否则按时长自动规划"""
    try:
        fixed = int(override or 0)
    except (TypeError, ValueError):
        fixed = 0
    if fixed > 0:
        return max(1, min(MAX_NODES, fixed))

    try:
        duration = max(1, int(duration_minutes or 0))
    except (TypeError, ValueError):
        duration = 30
    for limit, count in NODE_PLAN:
        if duration <= limit:
            return count
    return NODE_PLAN_LONG


def per_node_death_rate(total_rate: float, nodes: int) -> float:
    """把整场死亡率拆成每节点死亡率，保证 N 次独立掷骰后总死亡率不变

    ``1 - (1 - p_node) ** N == total_rate``
    """
    try:
        rate = float(total_rate or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    rate = max(0.0, min(1.0, rate))
    try:
        total = max(1, int(nodes or 1))
    except (TypeError, ValueError):
        total = 1
    if rate <= 0.0:
        return 0.0
    if rate >= 1.0:
        return 1.0
    return 1.0 - (1.0 - rate) ** (1.0 / total)


def node_deadline(start_time: int, end_time: int, nodes: int, index: int) -> int:
    """第 index 个节点（从 1 开始）的结算时间戳

    节点均匀分布在战斗时长内：第 k 个节点落在 ``start + 时长 * k / (N + 1)``，
    因此开战到首节点、以及相邻节点之间的最大信息空档为 ``时长 / (N + 1)``。
    """
    try:
        start = int(start_time or 0)
        end = int(end_time or 0)
    except (TypeError, ValueError):
        return 0
    duration = max(0, end - start)
    try:
        total = max(1, int(nodes or 1))
        idx = max(1, int(index))
    except (TypeError, ValueError):
        return start
    return start + int(duration * idx / (total + 1))


# ==================== 初始化 ====================


def init_state(
    event_data: dict,
    participants: List[dict],
    nodes: int,
    start_time: int,
    end_time: int,
) -> dict:
    """构建战斗初始状态

    Args:
        event_data: 事件快照（取 name / tier_key 等展示字段）
        participants: 报名快照，每项需含
            ``user_id`` / ``name`` / ``level_index`` / ``power`` / ``death_rate``
            （``death_rate`` 为**整场**死亡率，已含境界压制与战力减免；
             为 0 表示完全免死）
        nodes: 节点总数
        start_time / end_time: 战斗起止时间戳
    """
    players: Dict[str, dict] = {}
    for item in participants or []:
        user_id = str((item or {}).get("user_id") or "").strip()
        if not user_id:
            continue
        death_rate = float((item or {}).get("death_rate") or 0.0)
        try:
            power = float((item or {}).get("power") or 0.0)
        except (TypeError, ValueError):
            power = 0.0
        players[user_id] = {
            "user_id": user_id,
            "name": str((item or {}).get("name") or "无名修士"),
            "level_index": int((item or {}).get("level_index") or 0),
            "power": max(1.0, power),
            "death_rate": death_rate,
            "p_node": per_node_death_rate(death_rate, nodes),
            "immune": death_rate <= 0.0,
            "alive": True,
            "removed": False,
            "vigor": 1.0,
            "dealt": 0.0,
            "down_node": None,
            "exit_node": None,
            "notes": [],
        }

    total_power = sum(player["power"] for player in players.values()) or 1.0

    return {
        "version": 1,
        "nodes": max(1, int(nodes or 1)),
        "resolved": 0,
        "start_time": int(start_time or 0),
        "end_time": int(end_time or 0),
        "boss_hp": 1.0,
        # 击溃敌方首领所需的总输出：以全队战力为基准并随机浮动，
        # 使「斩杀 / 重伤遁走 / 全身而退」三种收尾都可能出现
        "boss_required": max(1.0, total_power * max(1, int(nodes or 1)) * random.uniform(0.85, 1.35)),
        "players": players,
        "history": [],
        "deaths": [],
        "saved": [],
        "fallen": [],
    }


# ==================== 推进 ====================


def resolve_node(event_data: dict, state: dict) -> dict:
    """推进一个节点（就地修改 state），返回本节点的原始记录

    返回记录里的 ``deaths`` / ``saved`` / ``fallen`` 还是空的，需由管理器完成
    死亡落地后调用 :func:`apply_node_outcome` 回填——本模块不决定生死归属。

    个人视角的 ``notes[].text`` 只写「你……」，不带「第N回合」前缀，
    回合号由展示层（``format_battle_report``）统一加，避免重复输出。
    """
    state["resolved"] = int(state.get("resolved") or 0) + 1
    node = state["resolved"]
    nodes = int(state.get("nodes") or 1)
    players: Dict[str, dict] = state.get("players") or {}

    rolling = [p for p in players.values() if p.get("alive") and not p.get("removed")]

    # 先算输出（本回合倒下的人也打完了这一击）
    node_dealt: Dict[str, float] = {}
    for player in rolling:
        dealt = float(player.get("power") or 0.0) * random.uniform(0.85, 1.15)
        node_dealt[player["user_id"]] = dealt
        player["dealt"] = round(float(player.get("dealt") or 0.0) + dealt, 4)

    # 再掷生死：每节点死亡率已保证 N 次独立掷骰后总死亡率守恒
    pending_deaths: List[str] = []
    downed: List[str] = []
    for player in rolling:
        uid = player["user_id"]
        p_node = float(player.get("p_node") or 0.0)
        roll = random.random()

        if p_node > 0.0 and roll < p_node:
            player["alive"] = False
            player["down_node"] = node
            # 本该极安全却仍阵亡 -> 判为「异变」，必须给出一个离谱但可信的死因
            dramatic = p_node < DRAMATIC_RISK_THRESHOLD
            pending_deaths.append({
                "user_id": uid,
                "dramatic": dramatic,
                "cause": random.choice(DRAMATIC_CAUSES) if dramatic else "",
            })
            continue

        # 生存余量：roll 越贴近死亡线（p_node），余量越小 -> 战意掉得越多
        if p_node >= 1.0:
            margin = 0.0
        else:
            margin = (roll - p_node) / max(1e-9, 1.0 - p_node)
        margin = max(0.0, min(1.0, margin))
        loss = 0.06 + 0.42 * (1.0 - margin)
        player["vigor"] = round(max(0.05, float(player.get("vigor", 1.0)) - loss), 4)
        if player["vigor"] <= VIGOR_DOWN_THRESHOLD:
            downed.append(uid)

    # 敌方首领血量推进
    group_dealt = sum(node_dealt.values())
    required = max(1.0, float(state.get("boss_required") or 1.0))
    if group_dealt > 0:
        state["boss_hp"] = round(max(0.0, float(state.get("boss_hp", 1.0)) - group_dealt / required), 4)

    # 本回合主力
    top_id, top_dealt = "", 0.0
    for uid, dealt in node_dealt.items():
        if dealt > top_dealt:
            top_id, top_dealt = uid, dealt
    top_share = (top_dealt / group_dealt) if group_dealt > 0 else 0.0

    record = {
        "node": node,
        "total": nodes,
        "boss_hp": float(state.get("boss_hp", 1.0)),
        "top_user_id": top_id,
        "top_name": str((players.get(top_id) or {}).get("name") or ""),
        "top_share": round(top_share, 4),
        "pending_deaths": pending_deaths,
        "downs": downed,
        "deaths": [],
        "saved": [],
        "fallen": [],
        "participants": len(players),
        "alive_count": len(rolling),
    }
    state.setdefault("history", []).append(record)
    return record


def apply_node_outcome(
    state: dict,
    record: dict,
    dead: Optional[List[dict]] = None,
    saved: Optional[List[dict]] = None,
    fallen: Optional[List[dict]] = None,
) -> None:
    """把死亡落地结果回填进节点记录与个人视角

    Args:
        dead: 阵亡（元神 / 劫后重生），每项 ``{"user_id", "name", "soul"}``
        saved: 回生丹抵消免死，每项 ``{"user_id", "name"}``
        fallen: 彻底陨落（角色已删除），每项 ``{"user_id", "name"}``
    """
    dead = list(dead or [])
    saved = list(saved or [])
    fallen = list(fallen or [])

    cause_map = {}
    for item in record.get("pending_deaths") or []:
        cause_map[str(item.get("user_id"))] = str(item.get("cause") or "")

    record["deaths"] = [
        {
            "user_id": str(item.get("user_id")),
            "name": item.get("name", ""),
            "soul": bool(item.get("soul")),
            "cause": cause_map.get(str(item.get("user_id")), ""),
        }
        for item in dead
    ]
    record["saved"] = [{"user_id": str(item.get("user_id")), "name": item.get("name", "")} for item in saved]
    record["fallen"] = [{"user_id": str(item.get("user_id")), "name": item.get("name", "")} for item in fallen]

    state.setdefault("deaths", []).extend(record["deaths"])
    state.setdefault("saved", []).extend(record["saved"])
    state.setdefault("fallen", []).extend(record["fallen"])

    dead_ids = {item["user_id"] for item in record["deaths"]}
    saved_ids = {item["user_id"] for item in record["saved"]}
    fallen_ids = {item["user_id"] for item in record["fallen"]}
    down_ids = set(record.get("downs") or [])
    node = int(record.get("node") or 0)

    for player in (state.get("players") or {}).values():
        uid = player["user_id"]
        notes = player.setdefault("notes", [])
        vigor_pct = int(round(float(player.get("vigor", 1.0)) * 100))

        if uid in fallen_ids:
            player["removed"] = True
            player["exit_node"] = node
            notes.append({"node": node, "kind": "fallen", "text": "你道基尽碎，就此陨落"})
            continue
        if uid in dead_ids:
            player["exit_node"] = node
            cause = cause_map.get(uid)
            if cause:
                notes.append({"node": node, "kind": "death_dramatic", "text": f"你{cause}，就此倒下"})
            else:
                notes.append({"node": node, "kind": "death", "text": "你力战不支，倒在了敌手阵前"})
            continue
        if uid in saved_ids:
            notes.append({
                "node": node,
                "kind": "saved",
                "text": "你气血将尽之际，怀中回生丹崩碎，硬生生续住了性命",
            })
            continue
        if not player.get("alive"):
            # 早前节点已阵亡，本回合按元神观战处理
            notes.append({"node": node, "kind": "watch", "text": "你以元神之姿旁观战局"})
            continue
        if uid in down_ids:
            notes.append({
                "node": node,
                "kind": "down",
                "text": f"你被敌手重创，战意仅剩 {vigor_pct}%",
            })
            continue
        # 场上只剩一人时"本轮主力"没有信息量（人人都是主力），不写进个人视角
        if uid == record.get("top_user_id") and int(record.get("alive_count") or 0) > 1:
            notes.append({
                "node": node,
                "kind": "top",
                "text": f"你连斩数名敌手，为本轮主力（战意 {vigor_pct}%）",
            })
        else:
            notes.append({
                "node": node,
                "kind": "fight",
                "text": f"你与敌手缠斗，战意 {vigor_pct}%",
            })


# ==================== 展示 ====================


def boss_label(event_data: dict) -> str:
    """敌方首领称呼（纯氛围，不含具体数值）"""
    tier = str((event_data or {}).get("tier") or "")
    return BOSS_LABELS.get(tier, "敌方首领")


def boss_outcome(state: dict) -> str:
    """战斗收尾：敌方首领的结局"""
    try:
        hp = float((state or {}).get("boss_hp", 1.0))
    except (TypeError, ValueError):
        hp = 1.0
    if hp <= 0.0:
        return "敌方首领被当场斩杀"
    if hp <= 0.3:
        return "敌方首领重伤遁走"
    if hp <= 0.7:
        return "敌方首领带伤退去"
    return "敌方首领从容退走，此战未竟全功"


def describe_node(event_data: dict, state: dict, record: dict, ai_text: str = "") -> str:
    """单节点群内播报（事实由代码写入，``ai_text`` 仅作氛围）"""
    name = (event_data or {}).get("name", "世界事件")
    node = int(record.get("node") or 1)
    total = int(record.get("total") or 1)
    label = boss_label(event_data)

    lines = [f"⚔️ 【{name}】战况 · 第 {node}/{total} 回合", SEP]

    if ai_text:
        lines.append(ai_text)

    boss_hp = float(record.get("boss_hp", 1.0))
    if boss_hp <= 0.0:
        lines.append(f"💥 {label}已伏诛，余众溃散")
    else:
        lines.append(f"📉 {label}气血：{int(round(boss_hp * 100))}%")

    top_name = record.get("top_name")
    # 场上只剩一人时"输出占 100%"没有信息量，直接不报
    if top_name and int(record.get("alive_count") or 0) > 1:
        lines.append(f"⚡ 本轮主力：{top_name}（输出占 {int(round(float(record.get('top_share') or 0) * 100))}%）")

    players = (state or {}).get("players") or {}
    names = lambda ids: "、".join(  # noqa: E731
        str((players.get(uid) or {}).get("name") or "无名修士") for uid in ids
    )

    downs = record.get("downs") or []
    if downs:
        lines.append(f"🩸 {names(downs)} 被重创，战意不足三成")

    saved = record.get("saved") or []
    if saved:
        lines.append(f"🛡️ {names([item['user_id'] for item in saved])} 回生丹护体，死里逃生")

    deaths = record.get("deaths") or []
    if len(deaths) <= 2:
        for item in deaths:
            tag = "元神状态" if item.get("soul") else "劫后重生"
            cause = item.get("cause")
            if cause:
                lines.append(f"💀 {item['name']}（{tag}）· {cause}")
            else:
                lines.append(f"💀 {item['name']}（{tag}）力战不支")
    elif deaths:
        names = "、".join(str(item.get("name") or "无名修士") for item in deaths)
        lines.append(f"💀 {names} 等 {len(deaths)} 人力战不支")

    fallen = record.get("fallen") or []
    if fallen:
        lines.append(f"🪦 {names([item['user_id'] for item in fallen])} 道基尽碎，就此陨落")

    notable = bool(top_name or downs or saved or deaths or fallen)
    if not notable:
        lines.append("🌫️ 双方鏖战不休，战线暂未松动")

    return "\n".join(line for line in lines if line)


def summarize_state(state: dict) -> dict:
    """汇总战斗状态（供结算文案与个人战报复用）"""
    players = (state or {}).get("players") or {}
    ranked = sorted(players.values(), key=lambda p: float(p.get("dealt") or 0.0), reverse=True)
    total_dealt = sum(float(p.get("dealt") or 0.0) for p in players.values())
    return {
        "nodes": int((state or {}).get("nodes") or 1),
        "resolved": int((state or {}).get("resolved") or 0),
        "boss_hp": float((state or {}).get("boss_hp", 1.0)),
        "boss_outcome": boss_outcome(state or {}),
        "deaths": (state or {}).get("deaths") or [],
        "saved": (state or {}).get("saved") or [],
        "fallen": (state or {}).get("fallen") or [],
        "mvp": (ranked[0] if ranked else None),
        "mvp_dealt": float(ranked[0].get("dealt") or 0.0) if ranked else 0.0,
        "mvp_share": (float(ranked[0].get("dealt") or 0.0) / total_dealt) if ranked and total_dealt > 0 else 0.0,
    }
