# handlers/utils.py
# 通用工具函数和装饰器

import re
import time
from functools import wraps
from typing import Callable, Coroutine, AsyncGenerator

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger
from ..models import Player
from ..models_extended import UserStatus

# 指令常量
CMD_START_XIUXIAN = "我要修仙"
CMD_PLAYER_INFO = "我的信息"
CMD_START_CULTIVATION = "闭关"
CMD_END_CULTIVATION = "出关"
CMD_CHECK_IN = "签到"
# 死亡机制 / 复活系统指令（批次1）
CMD_SOUL_STATE = "元神状态"
CMD_NATURAL_REVIVAL = "自然复活"

# 忙碌状态下允许执行的命令白名单
BUSY_STATE_ALLOWED_COMMANDS = [
    # 基础信息查看
    CMD_PLAYER_INFO,
    "我的信息",
    CMD_CHECK_IN,
    "签到",
    # 银行相关
    "银行",
    "存灵石",
    "取灵石",
    "领取利息",
    "贷款",
    "还款",
    "银行流水",
    # 背包查看（只读操作）
    "丹药背包",
    "我的丹药",
    "我的装备",
    "储物戒",
    "查看储物戒",
    # 商店浏览（只读操作）
    "丹阁",
    "器阁",
    "百宝阁",
    "物品信息",
    # 排行榜查看
    "排行榜",
    "境界榜",
    "战力榜",
    "灵石榜",
    "宗门榜",
    "存款榜",
    # 帮助信息
    "修仙帮助",
    # 死亡/复活相关（任何状态下都应可用）
    CMD_SOUL_STATE,
    CMD_NATURAL_REVIVAL,
    # 闭关相关
    CMD_END_CULTIVATION,
    "出关",
    # 历练/秘境结算
    "结束历练",
    "结束秘境",
    "结束任务",
]

# ===== 元神状态（批次1：死亡机制重构） =====

# 元神状态下仍可使用的指令（只读查询 / 复活相关）
SOUL_STATE_ALLOWED_COMMANDS = [
    # 基础信息与帮助
    CMD_PLAYER_INFO,
    "我的信息",
    CMD_CHECK_IN,
    "签到",
    "修仙帮助",
    CMD_SOUL_STATE,
    CMD_NATURAL_REVIVAL,
    "使用还魂丹",
    "还魂丹",
    # 背包 / 商店浏览（只读）
    "丹药背包",
    "我的装备",
    "改道号",
    "储物戒",
    "查看储物戒",
    "物品信息",
    "材料查询",
    "丹药信息",
    "炼化图鉴",
    "丹阁",
    "器阁",
    "百宝阁",
    "刷新商店",
    "购买",
    # 银行
    "银行",
    "存灵石",
    "取灵石",
    "领取利息",
    "还款",
    "银行流水",
    # 排行榜 / 只读面板
    "排行榜",
    "境界排行",
    "战力排行",
    "灵石排行",
    "宗门排行",
    "存款排行",
    "贡献排行",
    "宗门列表",
    "我的宗门",
    "师徒信息",
    "我的灵田",
    "我的洞天",
    "灵眼信息",
    "悬赏令",
    "悬赏状态",
    "秘境列表",
    "历练信息",
    "历练状态",
    "突破信息",
    "我的队伍",
    "队伍信息",
    # 结算/退出进行中的活动（避免元神后卡在忙碌状态）
    "出关",
    "完成历练",
    "完成探索",
    "退出秘境",
    "完成组队",
]

# 元神状态下禁止的指令前缀 -> 提示中的动作名
SOUL_STATE_FORBIDDEN_ACTIONS = [
    ("闭关", "修炼"),
    ("突破", "突破"),
    ("开始历练", "历练"),
    ("历练", "历练"),
    ("探索秘境", "进行秘境探索"),
    ("宗门秘境", "进行秘境探索"),
    ("挑战boss", "战斗"),
    ("世界boss", "战斗"),
    ("决斗", "战斗"),
    ("切磋", "战斗"),
    ("传承挑战", "战斗"),
    ("组队探索", "组队探险"),
    ("创建队伍", "组队"),
    ("邀请入队", "组队"),
    ("加入队伍", "组队"),
    ("炼化材料", "炼化材料"),
    ("一键炼化", "炼化材料"),
    ("炼丹", "炼丹"),
    ("种植", "种植"),
    ("收获", "种植"),
    ("开垦灵田", "开垦灵田"),
    ("升级灵田", "升级灵田"),
    ("双修", "双修"),
    ("接受双修", "双修"),
    ("收徒", "收徒"),
    ("拜师", "拜师"),
    ("灌顶", "灌顶"),
    ("接取悬赏", "接取悬赏"),
    ("完成悬赏", "完成悬赏"),
    ("放弃悬赏", "放弃悬赏"),
    ("抢占灵眼", "抢占灵眼"),
    ("灵眼收取", "收取灵眼"),
    ("释放灵眼", "释放灵眼"),
    ("洞天收取", "收取洞天产出"),
    ("购买洞天", "购买洞天"),
    ("升级洞天", "升级洞天"),
    ("转让洞天", "转让洞天"),
    ("宗门捐献", "处理宗门事务"),
    ("宗门建设", "处理宗门事务"),
    ("宗门任务", "处理宗门事务"),
    ("捐献功法", "处理宗门事务"),
    ("借用功法", "处理宗门事务"),
    ("创建宗门", "处理宗门事务"),
    ("加入宗门", "处理宗门事务"),
    ("退出宗门", "处理宗门事务"),
    ("职位变更", "处理宗门事务"),
    ("踢出成员", "处理宗门事务"),
    ("宗主传位", "处理宗门事务"),
    ("突破贷款", "申请贷款"),
    ("赠予", "赠予物品"),
    ("接收", "赠予"),
    ("弃道重修", "弃道重修"),
]

# 由 main.py 注入的死亡系统配置（供自动复活使用）
_soul_state_config: dict = {}


def set_soul_state_config(config: dict):
    """注入死亡系统配置（main.py 初始化时调用）"""
    global _soul_state_config
    _soul_state_config = dict(config) if isinstance(config, dict) else {}


def get_soul_state_config() -> dict:
    """获取已注入的死亡系统配置"""
    return dict(_soul_state_config)


def soul_state_block_message(action: str = "") -> str:
    """生成元神状态禁止操作的统一提示"""
    action = action or "进行此操作"
    return (
        f"⚠️ 元神状态无法{action}，请先复活！\n"
        f"💡 发送「{CMD_SOUL_STATE}」查看元神详情\n"
        f"发送「{CMD_NATURAL_REVIVAL}」等待期满后自然复活"
    )


def _match_command(text: str, cmd: str) -> bool:
    """判断指令文本是否命中指定指令（要求带参数时以空格分隔，避免前缀误伤）"""
    if not cmd:
        return False
    return text == cmd or text.startswith(f"{cmd} ")


def get_soul_state_block_message(message_text: str) -> str:
    """判断该指令在元神状态下是否被禁止

    Returns:
        禁止时返回提示消息；允许时返回空字符串
    """
    text = (message_text or "").strip().lstrip("/").strip()
    if not text:
        return ""

    # 所有「XX帮助」类指令始终可用
    if text.endswith("帮助"):
        return ""

    for cmd in SOUL_STATE_ALLOWED_COMMANDS:
        if _match_command(text, cmd):
            return ""

    for cmd, action in SOUL_STATE_FORBIDDEN_ACTIONS:
        if _match_command(text, cmd):
            return soul_state_block_message(action)

    return soul_state_block_message("进行此操作")


async def _auto_revive_soul_state(db, player: Player) -> tuple:
    """超时未复活的元神玩家自动强制复活

    Returns:
        (是否已自动复活, 消息)
    """
    try:
        from .revival_handler import RevivalHandler
    except Exception:
        return False, ""

    try:
        handler = RevivalHandler(db, config=get_soul_state_config())
        return await handler.check_and_auto_revive(player)
    except Exception as e:
        logger.warning(f"[复活系统] 自动复活检查失败: {e}")
        return False, ""


def player_required(func: Callable[..., Coroutine[any, any, AsyncGenerator[any, None]]]):
    """
    一个装饰器，用于需要玩家登录才能执行的指令。
    它会自动检查玩家是否存在、状态是否空闲（特定指令除外），否则将玩家对象作为参数注入。
    同时检查贷款状态，如有贷款则显示还款提示。
    """
    @wraps(func)
    async def wrapper(self, event: AstrMessageEvent, *args, **kwargs):
        # self 是 Handler 类的实例 (e.g., PlayerHandler)
        player = await self.db.get_player_by_id(event.get_sender_id())

        if not player:
            yield event.plain_result(f"道友尚未踏入仙途，请发送「{CMD_START_XIUXIAN}」开启你的旅程。")
            return

        # 检查贷款状态并处理逾期
        loan_warning = await _check_loan_status(self.db, player)
        if loan_warning:
            if loan_warning.get("is_dead"):
                # 玩家因逾期被追杀，删除数据
                yield event.plain_result(loan_warning["message"])
                return
        
        message_text = event.get_message_str().strip()

        # 元神状态检查（批次1）：超时自动复活 + 禁止修炼/战斗等操作
        if player.is_soul_state:
            revived, revive_msg = await _auto_revive_soul_state(self.db, player)
            if revived:
                # 自动复活后数据库已更新，重新加载玩家对象，避免继续使用过期数据
                player = await self.db.get_player_by_id(player.user_id) or player
                if revive_msg:
                    yield event.plain_result(revive_msg)

            # 重新检查元神状态（可能已被自动复活）
            if player.is_soul_state:
                block_msg = get_soul_state_block_message(message_text)
                if block_msg:
                    yield event.plain_result(block_msg)
                    return

        # 检查 user_cd 表的忙碌状态
        user_cd = await self.db.ext.get_user_cd(player.user_id)
        if user_cd and user_cd.type != UserStatus.IDLE:
            # 玩家处于忙碌状态，检查命令是否在白名单中
            is_allowed = _is_command_allowed(message_text, BUSY_STATE_ALLOWED_COMMANDS)
            
            if not is_allowed:
                status_name = UserStatus.get_name(user_cd.type)
                yield event.plain_result(f"道友当前正在「{status_name}」，无法分心他顾。\n💡 可使用「我的信息」「签到」「银行」等基础指令。")
                return
        
        # 状态检查：如果处于修炼中（闭关），只允许出关、查看信息和签到
        if player.state == "修炼中":
            is_allowed = _is_command_allowed(message_text, BUSY_STATE_ALLOWED_COMMANDS)

            if not is_allowed:
                yield event.plain_result(f"道友当前正在「{player.state}」中，无法分心他顾。\n💡 可使用「出关」「我的信息」「签到」「银行」等基础指令。")
                return

        # 将 player 对象作为第一个参数传递给原始函数
        async for result in func(self, player, event, *args, **kwargs):
            yield result
        
        # 如果有贷款警告，在指令执行完后显示
        if loan_warning and loan_warning.get("warning_message"):
            yield event.plain_result(loan_warning["warning_message"])

    return wrapper


def _is_command_allowed(message_text: str, allowed_commands: list) -> bool:
    """检查命令是否在允许列表中（帮助类指令在忙碌状态下始终可用）"""
    text = (message_text or "").strip().lstrip("/").strip()
    if text.endswith("帮助"):
        return True

    for cmd in allowed_commands:
        if message_text.startswith(cmd):
            return True
    return False


async def _check_loan_status(db, player: Player) -> dict:
    """检查玩家贷款状态
    
    Returns:
        dict: {is_dead, message, warning_message} 或 None
    """
    try:
        loan = await db.ext.get_active_loan(player.user_id)
        if not loan:
            return None
        
        now = int(time.time())
        due_at = loan["due_at"]
        
        # 检查是否已逾期
        if now > due_at:
            # 使用事务保护，防止并发删除
            await db.begin_immediate()
            try:
                # 重新检查贷款状态（可能已被其他请求处理）
                loan = await db.ext.get_active_loan(player.user_id)
                if not loan or loan["status"] != "active":
                    await db.conn.rollback()
                    return None
                
                # 再次检查是否逾期
                if now <= loan["due_at"]:
                    await db.conn.rollback()
                    return None
                
                player_name = player.user_name or f"道友{player.user_id[:6]}"
                
                # 删除玩家（级联删除所有关联数据）
                await db.delete_player_cascade(player.user_id)
                
                # 标记贷款逾期
                await db.ext.mark_loan_overdue(loan["id"])
                
                # 记录流水
                await db.ext.add_bank_transaction(
                    player.user_id, "bank_kill", 0, 0,
                    "逾期未还款，被银行追杀致死", now
                )
                
                await db.conn.commit()
                
                loan_type_name = "突破贷款" if loan["loan_type"] == "breakthrough" else "普通贷款"
                
                return {
                    "is_dead": True,
                    "message": (
                        f"💀 银行追杀令 💀\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"道友【{player_name}】因{loan_type_name}逾期未还\n"
                        f"欠款本金：{loan['principal']:,} 灵石\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"银行派出的追杀者已将你击杀！\n"
                        f"所有修为和装备化为虚无...\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"若想重新修仙，请使用「我要修仙」命令"
                    )
                }
            except Exception:
                await db.conn.rollback()
                raise
        
        # 计算剩余时间
        remaining_seconds = due_at - now
        remaining_days = remaining_seconds // 86400
        remaining_hours = (remaining_seconds % 86400) // 3600
        
        # 计算应还金额
        days_borrowed = max(1, (now - loan["borrowed_at"]) // 86400)
        interest = int(loan["principal"] * loan["interest_rate"] * days_borrowed)
        total_due = loan["principal"] + interest
        
        loan_type_name = "突破贷款" if loan["loan_type"] == "breakthrough" else "普通贷款"
        
        # 根据剩余时间设置警告等级
        if remaining_days <= 0:
            urgency = "🔴 紧急"
            time_str = f"{remaining_hours} 小时"
        elif remaining_days <= 1:
            urgency = "🟠 警告"
            time_str = f"{remaining_days} 天 {remaining_hours} 小时"
        else:
            urgency = "🟡 提醒"
            time_str = f"{remaining_days} 天"
        
        warning_message = (
            f"\n━━━━━━━━━━━━━━━\n"
            f"{urgency}【{loan_type_name}还款提醒】\n"
            f"应还金额：{total_due:,} 灵石\n"
            f"剩余时间：{time_str}\n"
            f"⚠️ 逾期将被银行追杀致死！\n"
            f"请使用 /还款 命令还款"
        )
        
        return {
            "is_dead": False,
            "warning_message": warning_message
        }
        
    except Exception:
        return None


# ==================== @ 目标玩家解析 ====================

# At 组件上可能出现的用户标识字段（不同适配器命名不同）
_AT_ID_ATTRS = ("qq", "target", "uin", "user_id", "openid", "id")

# 原始消息中 @ 的文本形式：CQ 码 / XML 标签
_AT_TEXT_PATTERNS = (
    r"\[CQ:at,[^\]]*?\bqq=(\d+)",
    r"\[CQ:at,[^\]]*?\buser_id=([\w-]+)",
    r"<at[^>]*?\bqq=[\"']?([\w-]+)",
    r"<at[^>]*?\buser_id=[\"']?([\w-]+)",
)


def extract_command_args(event, command_names) -> str:
    """从原始消息中提取指令名之后的完整参数文本。

    AstrBot 的指令参数是按空格逐个绑定到函数形参的，函数没声明的多余 token 会被丢弃
    （例如 ``/服用丹药 凝气丹 10`` 只会把「凝气丹」传进第一个形参，数量「10」被吞掉）。
    需要完整参数的指令（数量、全部、多段参数等）可通过本函数从原始消息重新解析。

    Args:
        event: AstrMessageEvent
        command_names: 指令名（str 或可迭代的多个候选名，如别名）

    Returns:
        指令名之后的参数文本（已去除全角空格），无法解析时返回空字符串
    """
    raw = ""
    getter = getattr(event, "get_message_str", None)
    if callable(getter):
        try:
            raw = getter() or ""
        except Exception:
            raw = ""
    if not raw:
        raw = str(getattr(event, "message_str", "") or "")

    raw = raw.replace("\u3000", " ").strip()
    if not raw:
        return ""

    names = [command_names] if isinstance(command_names, (str, bytes)) else list(command_names)
    for name in sorted(names, key=len, reverse=True):
        if not name:
            continue
        stripped = re.sub(
            rf"^[=/！!，,。.]*\s*{re.escape(str(name))}\s*",
            "",
            raw,
            count=1,
        )
        if stripped != raw:
            return stripped.strip()
    return raw


def _iter_message_components(event):
    """遍历消息链组件（兼容缺失 message_obj 的老版本/适配器）"""
    message_obj = getattr(event, "message_obj", None)
    chain = getattr(message_obj, "message", None)
    if chain is None:
        getter = getattr(event, "get_messages", None)
        if callable(getter):
            try:
                chain = getter()
            except Exception:
                chain = None
    return list(chain or [])


def _is_at_component(component) -> bool:
    if component is None:
        return False
    if type(component).__name__ == "At":
        return True
    return any(hasattr(component, attr) for attr in ("qq", "uin"))


def _iter_raw_texts(event):
    """收集可能包含 CQ 码 / at 标签的原始文本"""
    texts = []
    message_str = getattr(event, "get_message_str", None)
    if callable(message_str):
        try:
            texts.append(message_str() or "")
        except Exception:
            pass
    texts.append(str(getattr(event, "message_str", "") or ""))
    raw = getattr(event, "raw_message", None)
    if raw is not None:
        texts.append(str(raw))
    for component in _iter_message_components(event):
        text = getattr(component, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return [t for t in texts if t]


def extract_at_ids(event) -> list:
    """从消息链与原始消息中提取所有 @ 到的用户ID（去重保序）"""
    ids = []

    def _add(value):
        text = str(value or "").strip().lstrip("@")
        if text and text not in ids:
            ids.append(text)

    for component in _iter_message_components(event):
        if not _is_at_component(component):
            continue
        for attr in _AT_ID_ATTRS:
            value = getattr(component, attr, None)
            if value is None:
                continue
            if str(value).strip():
                _add(value)
                break

    for text in _iter_raw_texts(event):
        for pattern in _AT_TEXT_PATTERNS:
            for match in re.finditer(pattern, text):
                _add(match.group(1))

    return ids


def extract_name_candidate(event, arg: str = "") -> str:
    """从参数或消息文本中提取可能的道号/昵称（去掉@与表情）"""
    candidates = []

    raw_arg = str(arg or "").strip()
    if raw_arg:
        candidates.append(raw_arg.lstrip("@").strip())

    for component in _iter_message_components(event):
        if _is_at_component(component):
            name = getattr(component, "name", None) or getattr(component, "display", None)
            if name:
                candidates.append(str(name).strip())

    message_str = ""
    getter = getattr(event, "get_message_str", None)
    if callable(getter):
        try:
            message_str = getter() or ""
        except Exception:
            message_str = ""
    for match in re.finditer(r"@([^@\s\[\]<>=:]+)", message_str):
        candidates.append(match.group(1).strip())

    for candidate in candidates:
        if not candidate or len(candidate) > 20:
            continue
        if any(ch in candidate for ch in "[]<>=:,\n\t"):
            continue
        if candidate.isdigit():
            continue
        return candidate
    return ""


async def _find_user_id_by_name(db, name: str) -> str:
    """按道号精确/模糊查找玩家ID（结果唯一时才返回）"""
    if not name:
        return ""

    getter = getattr(db, "get_player_by_name", None)
    if callable(getter):
        try:
            player = await getter(name)
        except Exception:
            player = None
        if player:
            return player.user_id

    rows = []
    try:
        async with db.conn.execute(
            "SELECT user_id FROM players WHERE user_name = ? LIMIT 2", (name,)
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            async with db.conn.execute(
                "SELECT user_id FROM players WHERE user_name LIKE ? LIMIT 3",
                (f"%{name}%",),
            ) as cursor:
                rows = await cursor.fetchall()
    except Exception:
        return ""

    if len(rows) == 1:
        row = rows[0]
        try:
            return str(row["user_id"])
        except (TypeError, IndexError, KeyError):
            return str(row[0])
    return ""


async def _player_exists(db, user_id: str) -> bool:
    if not user_id:
        return False
    try:
        return bool(await db.get_player_by_id(str(user_id)))
    except Exception:
        return False


async def resolve_target_user_id(db, event, arg: str = "") -> str:
    """解析指令中的目标玩家ID。

    依次尝试（每一项都会校验玩家是否存在）：
    1. 消息链中的 At 组件（AstrBot 标准做法）
    2. 原始消息中的 CQ 码 / at 标签
    3. 指令参数中的纯数字ID
    4. 道号/昵称（精确 -> 模糊），解决部分适配器 @ 只留下名字的情况

    若 @ 到了数据库中不存在的玩家，则原样返回该ID，由调用方给出提示。

    Args:
        db: DataBase 实例
        event: AstrMessageEvent
        arg: 指令参数字符串

    Returns:
        解析出的用户ID，解析失败返回空字符串
    """
    at_candidates = extract_at_ids(event)
    for candidate in at_candidates:
        if await _player_exists(db, candidate):
            return candidate

    raw_arg = str(arg or "").strip()
    for digits in re.findall(r"\d+", raw_arg):
        if await _player_exists(db, digits):
            return digits

    name = extract_name_candidate(event, raw_arg)
    if name:
        user_id = await _find_user_id_by_name(db, name)
        if user_id:
            return user_id

    return at_candidates[0] if at_candidates else ""
