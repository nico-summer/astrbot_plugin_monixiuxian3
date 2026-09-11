# handlers/commission_handlers.py
"""委托炼丹指令处理器（批次3：炼丹师职业 + 委托炼丹）

指令一览（注册见 main.py）：
    委托炼丹 <配方ID> [数量] [手续费] [@炼丹师]   发布委托
    委托列表                                      查看公开委托板
    我的委托                                      查看自己发布 / 接单的委托
    接取委托 <编号>                                炼丹师接单
    完成委托 <编号>                                炼丹师交付成品
    取消委托 <编号>                                委托人撤回未接单的委托
    放弃委托 <编号>                                炼丹师放弃已接单的委托
    炼丹师信息                                     炼丹统计 / 委托进度 / 炼丹师排行

AstrBot 只会把指令后的前几个 token 绑定到形参（多余的会被丢弃），
因此需要完整参数的指令统一通过 ``extract_command_args`` 从原始消息重新解析。
"""

import re
from typing import List

from astrbot.api.event import AstrMessageEvent

from ..data.data_manager import DataBase
from ..managers.alchemy_manager import AlchemyManager
from ..managers.commission_manager import CommissionManager
from ..models import Player
from .utils import extract_command_args, player_required, resolve_target_user_id

__all__ = ["CommissionHandlers"]

CMD_BECOME_ALCHEMIST = "成为炼丹师"
CMD_CREATE_COMMISSION = "委托炼丹"
CMD_LIST_COMMISSIONS = "委托列表"
CMD_MY_COMMISSIONS = "我的委托"
CMD_ACCEPT_COMMISSION = "接取委托"
CMD_COMPLETE_COMMISSION = "完成委托"
CMD_CANCEL_COMMISSION = "取消委托"
CMD_ABANDON_COMMISSION = "放弃委托"
CMD_ALCHEMIST_INFO = "炼丹师信息"

CREATE_USAGE = (
    "📖 用法：委托炼丹 <配方ID> [数量] [手续费] [@炼丹师]\n"
    "  · 例1（公开委托）：委托炼丹 101 1 5000\n"
    "  · 例2（指定炼丹师）：委托炼丹 101 1 5000 @某某\n"
    "  · 数量默认 1，手续费默认 0\n"
    "💡 发送「丹药配方」查看配方ID，发送「委托列表」查看现有委托"
)

# @ 提及在原始文本中的几种形态（CQ 码 / at 标签 / @昵称）
_AT_TEXT_RE = re.compile(r"\[CQ:at,[^\]]*\]|<at[^>]*>|@\S+")


class CommissionHandlers:
    """委托炼丹指令处理器"""

    def __init__(
        self,
        db: DataBase,
        commission_mgr: CommissionManager,
        alchemy_mgr: AlchemyManager = None,
    ):
        self.db = db
        self.commission_mgr = commission_mgr
        self.alchemy_mgr = alchemy_mgr

    # ===== 参数解析 =====

    @staticmethod
    def _tokenize(event: AstrMessageEvent, command: str, fallback: str = "") -> List[str]:
        """把指令后的参数切成 token（剔除 @提及 片段）"""
        raw = extract_command_args(event, command) or (fallback or "")
        raw = _AT_TEXT_RE.sub(" ", raw.replace("\u3000", " ")).strip()
        return [token for token in re.split(r"\s+", raw) if token]

    @staticmethod
    def _parse_int(text: str):
        """解析整数（支持「1万」这类带单位的写法）"""
        text = str(text or "").strip()
        if not text:
            return None
        match = re.fullmatch(r"(\d+)\s*万", text)
        if match:
            return int(match.group(1)) * 10000
        match = re.fullmatch(r"(\d+)\s*千", text)
        if match:
            return int(match.group(1)) * 1000
        if not text.isdigit():
            return None
        return int(text)

    def _resolve_commission_id(self, event: AstrMessageEvent, command: str, arg: str = ""):
        """解析委托编号"""
        tokens = self._tokenize(event, command, arg)
        if not tokens:
            return None
        return self._parse_int(tokens[0])

    # ===== 发布 / 查看 =====

    @player_required
    async def handle_create_commission(self, player: Player, event: AstrMessageEvent, args: str = ""):
        """委托炼丹 <配方ID> [数量] [手续费] [@炼丹师]"""
        tokens = self._tokenize(event, CMD_CREATE_COMMISSION, args)
        if not tokens:
            yield event.plain_result(CREATE_USAGE)
            return

        recipe_id = self._parse_int(tokens[0])
        quantity = self._parse_int(tokens[1]) if len(tokens) > 1 else 1
        fee = self._parse_int(tokens[2]) if len(tokens) > 2 else 0

        if recipe_id is None or quantity is None or fee is None:
            yield event.plain_result(
                "❌ 参数错误：配方ID / 数量 / 手续费必须是数字\n\n" + CREATE_USAGE
            )
            return

        # 指定炼丹师（兼容 At 组件 / CQ 码 / 纯数字ID / 道号）
        alchemist_id = await resolve_target_user_id(self.db, event, "")

        success, msg = await self.commission_mgr.create_commission(
            player, recipe_id, quantity, fee, alchemist_id or None
        )
        yield event.plain_result(msg)

    @player_required
    async def handle_list_commissions(self, player: Player, event: AstrMessageEvent):
        """委托列表（公开委托板）"""
        msg = await self.commission_mgr.list_commissions(viewer_id=player.user_id)
        yield event.plain_result(msg)

    @player_required
    async def handle_my_commissions(self, player: Player, event: AstrMessageEvent):
        """我的委托（发布的 / 接单的）"""
        msg = await self.commission_mgr.get_my_commissions(player)
        yield event.plain_result(msg)

    @player_required
    async def handle_alchemist_info(self, player: Player, event: AstrMessageEvent):
        """炼丹师信息（统计 / 条件 / 进行中的委托 / 排行）"""
        msg = await self.commission_mgr.get_alchemist_info(player)
        ranking = await self.commission_mgr.get_alchemist_ranking(limit=5)
        yield event.plain_result(f"{msg}\n\n{ranking}")

    # ===== 接单 / 完成 =====

    @player_required
    async def handle_accept_commission(self, player: Player, event: AstrMessageEvent, commission_id: str = ""):
        """接取委托 <编号>"""
        commission_id = self._resolve_commission_id(event, CMD_ACCEPT_COMMISSION, commission_id)
        if not commission_id:
            yield event.plain_result("📖 用法：接取委托 <编号>\n💡 发送「委托列表」查看可接取的委托")
            return

        success, msg = await self.commission_mgr.accept_commission(player, commission_id)
        yield event.plain_result(msg)

    @player_required
    async def handle_complete_commission(self, player: Player, event: AstrMessageEvent, commission_id: str = ""):
        """完成委托 <编号>"""
        commission_id = self._resolve_commission_id(event, CMD_COMPLETE_COMMISSION, commission_id)
        if not commission_id:
            yield event.plain_result(
                "📖 用法：完成委托 <编号>\n"
                "💡 炼出成品后交付（发送「我的委托」查看进行中的委托）"
            )
            return

        success, msg = await self.commission_mgr.complete_commission(player, commission_id)
        yield event.plain_result(msg)

    # ===== 取消 / 放弃 =====

    @player_required
    async def handle_cancel_commission(self, player: Player, event: AstrMessageEvent, commission_id: str = ""):
        """取消委托 <编号>（委托人撤回未接单的委托）"""
        commission_id = self._resolve_commission_id(event, CMD_CANCEL_COMMISSION, commission_id)
        if not commission_id:
            yield event.plain_result(
                "📖 用法：取消委托 <编号>\n"
                "💡 仅可撤回尚未被接单的委托（材料与手续费全额返还）"
            )
            return

        success, msg = await self.commission_mgr.cancel_commission(player, commission_id)
        yield event.plain_result(msg)

    @player_required
    async def handle_abandon_commission(self, player: Player, event: AstrMessageEvent, commission_id: str = ""):
        """放弃委托 <编号>（炼丹师放弃已接单的委托）"""
        commission_id = self._resolve_commission_id(event, CMD_ABANDON_COMMISSION, commission_id)
        if not commission_id:
            yield event.plain_result(
                "📖 用法：放弃委托 <编号>\n"
                "💡 仅限炼丹师放弃已接单的委托（材料与手续费退回委托人）"
            )
            return

        success, msg = await self.commission_mgr.abandon_commission(player, commission_id)
        yield event.plain_result(msg)
