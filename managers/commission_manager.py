# managers/commission_manager.py
"""委托炼丹管理器（批次3：炼丹师职业 + 委托炼丹）

玩家之间交易炼丹服务：委托人出材料与手续费，炼丹师接单炼制并交付成品。

完整流程：
    委托炼丹 <配方ID> <数量> <手续费> [@炼丹师]   委托人发布（材料 + 手续费托管锁定）
    委托列表                                      查看公开委托板
    我的委托                                      查看自己发布 / 接单的委托
    接取委托 <编号>                                炼丹师接单（领取托管材料，进入炼制中）
    完成委托 <编号>                                炼丹师交付成品，领取手续费
    取消委托 <编号>                                委托人取消未接单的委托（全额返还）
    放弃委托 <编号>                                炼丹师放弃已接单的委托（材料与手续费退回委托人）

数据表：alchemy_commissions（建表与迁移见 data/migration.py，读写见 data/data_manager.py）

托管规则（避免玩家之间产生纠纷）：
- 发布委托时：材料（含配方中的「灵石」消耗）与手续费从委托人身上扣除并锁定
- 接单时：材料转交给炼丹师（炼丹师用这批材料炼制），手续费继续托管
- 完成时：炼丹师交付成品（丹药背包扣除），获得手续费
- 取消 / 放弃时：材料与手续费原样退回委托人
"""

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Union

from astrbot.api import logger

from ..data.data_manager import DataBase
from ..models import Player

if TYPE_CHECKING:
    from ..config_manager import ConfigManager
    from ..core import StorageRingManager
    from .alchemy_manager import AlchemyManager

__all__ = ["CommissionManager"]

SEP = "━━━━━━━━━━━━━━━"

# 配方材料中的通用货币键名（灵石不走储物戒，直接结算）
GOLD_KEY = "灵石"

# 委托状态 -> 展示文本
STATUS_TEXT = {
    "pending": "待接单",
    "in_progress": "炼制中",
    "completed": "已完成",
}


class CommissionManager:
    """委托炼丹管理器"""

    def __init__(
        self,
        db: DataBase,
        config_manager: "ConfigManager" = None,
        storage_ring_manager: "StorageRingManager" = None,
        alchemy_manager: "AlchemyManager" = None,
    ):
        self.db = db
        self.config_manager = config_manager
        self.storage_ring_manager = storage_ring_manager
        self.alchemy = alchemy_manager

    # ===== 配置 =====

    def _setting(self, key: str, default=None):
        """读取炼丹配置（复用炼丹管理器的配置读取逻辑）"""
        if self.alchemy is not None and hasattr(self.alchemy, "get_setting"):
            value = self.alchemy.get_setting(key, None)
            if value is not None:
                return value
        return default

    def is_enabled(self) -> bool:
        return bool(self._setting("commission_enabled", True))

    @property
    def max_quantity(self) -> int:
        try:
            return max(1, int(self._setting("commission_max_quantity", 99) or 99))
        except (TypeError, ValueError):
            return 99

    @property
    def max_fee(self) -> int:
        try:
            return max(0, int(self._setting("commission_max_fee", 1000000) or 0))
        except (TypeError, ValueError):
            return 1000000

    @property
    def max_active(self) -> int:
        """炼丹师同时进行中的委托上限"""
        try:
            return max(1, int(self._setting("commission_max_active", 3) or 3))
        except (TypeError, ValueError):
            return 3

    @property
    def max_pending(self) -> int:
        try:
            return max(1, int(self._setting("commission_max_pending", 5) or 5))
        except (TypeError, ValueError):
            return 5

    # ===== 通用工具 =====

    async def _resolve_player(self, target: Union[Player, str, None]) -> Optional[Player]:
        """兼容「玩家对象」与「用户ID」两种入参"""
        if isinstance(target, Player):
            return target
        if not target:
            return None
        try:
            return await self.db.get_player_by_id(str(target))
        except Exception as e:
            logger.warning(f"[委托炼丹] 读取玩家失败: {e}")
            return None

    async def _fresh(self, player: Optional[Player]) -> Optional[Player]:
        """重新读取玩家最新数据

        玩家对象可能在指令执行期间被其它结算改写（储物戒 / 灵石），
        统一使用最新数据可以避免「材料不足」之类的误判。
        """
        if not player:
            return player
        try:
            fresh = await self.db.get_player_by_id(player.user_id)
        except Exception as e:
            logger.warning(f"[委托炼丹] 刷新玩家数据失败: {e}")
            return player
        return fresh or player

    def _get_recipe(self, recipe_id) -> Optional[Dict]:
        if self.alchemy is None:
            return None
        return self.alchemy.get_recipe(recipe_id)

    async def _display_name(self, user_id: str) -> str:
        """玩家展示名（道号优先，其次 ID）"""
        if not user_id:
            return "未知"
        player = await self.db.get_player_by_id(str(user_id))
        if player and getattr(player, "user_name", ""):
            return str(player.user_name)
        return str(user_id)

    def _total_materials(self, recipe: Dict, quantity: int) -> Dict[str, int]:
        """委托所需材料总量（含配方中的灵石消耗）"""
        materials = recipe.get("materials", {}) or {}
        total: Dict[str, int] = {}
        for name, count in materials.items():
            try:
                amount = int(count) * int(quantity)
            except (TypeError, ValueError):
                continue
            if amount > 0:
                total[str(name)] = amount
        return total

    @staticmethod
    def _split_materials(total_materials: Dict[str, int]) -> Tuple[Dict[str, int], int]:
        """拆分材料：储物戒材料 与 灵石消耗"""
        ring_materials = {
            name: amount for name, amount in total_materials.items() if name != GOLD_KEY
        }
        try:
            gold_materials = int(total_materials.get(GOLD_KEY, 0) or 0)
        except (TypeError, ValueError):
            gold_materials = 0
        return ring_materials, gold_materials

    def _material_text(self, materials: Dict[str, int]) -> str:
        if not materials:
            return "无"
        return "、".join(f"{name}×{int(amount)}" for name, amount in materials.items())

    # ===== 储物戒材料收发 =====

    def _check_ring_materials(self, player: Player, materials: Dict[str, int]) -> List[str]:
        """检查储物戒材料是否充足，返回不足项文本列表"""
        if not self.storage_ring_manager or not materials:
            return []

        missing: List[str] = []
        for name, need in materials.items():
            have = int(self.storage_ring_manager.get_item_count(player, name) or 0)
            if have < int(need):
                missing.append(f"  · {name}（需要{int(need)}，拥有{have}）")
        return missing

    async def _take_ring_materials(
        self, player: Player, materials: Dict[str, int]
    ) -> Tuple[bool, str]:
        """从储物戒取出材料（失败时回滚已取出的部分）"""
        if not self.storage_ring_manager or not materials:
            return True, ""

        taken: List[Tuple[str, int]] = []
        for name, count in materials.items():
            count = int(count)
            if count <= 0:
                continue
            ok, msg = await self.storage_ring_manager.retrieve_item(player, name, count)
            if not ok:
                for back_name, back_count in taken:
                    await self.storage_ring_manager.store_item(
                        player, back_name, back_count, silent=True
                    )
                return False, f"❌ 锁定材料失败：{msg}"
            taken.append((name, count))
        return True, ""

    def _check_receive_space(self, player: Player, materials: Dict[str, int]) -> Tuple[bool, str]:
        """检查储物戒是否有足够空位接收材料（每种新材料占 1 格）"""
        if not self.storage_ring_manager or not materials:
            return True, ""

        items = player.get_storage_ring_items()
        new_types = 0
        for name in materials:
            value = items.get(name, 0)
            if isinstance(value, dict):
                value = value.get("count", 0)
            try:
                owned = int(value)
            except (TypeError, ValueError):
                owned = 0
            if owned <= 0:
                new_types += 1

        available = self.storage_ring_manager.get_available_slots(player)
        if new_types > available:
            return False, (
                f"储物戒空间不足：需要 {new_types} 格，仅剩 {available} 格\n"
                "💡 请先清空部分物品后重试"
            )
        return True, ""

    async def _give_ring_materials(
        self, player: Player, materials: Dict[str, int]
    ) -> Tuple[bool, str]:
        """把材料放入储物戒（失败时回滚已放入的部分）"""
        if not self.storage_ring_manager or not materials:
            return True, ""

        stored: List[Tuple[str, int]] = []
        for name, count in materials.items():
            count = int(count)
            if count <= 0:
                continue
            ok, msg = await self.storage_ring_manager.store_item(
                player, name, count, silent=True
            )
            if not ok:
                for back_name, back_count in stored:
                    await self.storage_ring_manager.retrieve_item(player, back_name, back_count)
                return False, f"❌ 放入材料失败：{msg}"
            stored.append((name, count))
        return True, ""

    # ===== 委托发布 =====

    async def create_commission(
        self,
        client: Union[Player, str],
        recipe_id: int,
        quantity: int = 1,
        fee: int = 0,
        alchemist_id: str = None,
    ) -> Tuple[bool, str]:
        """创建委托

        Args:
            client: 委托人（玩家对象或用户ID）
            recipe_id: 配方ID
            quantity: 炼制数量
            fee: 手续费（灵石，托管到委托完成）
            alchemist_id: 指定炼丹师（为 None 时发布到公开委托板）

        Returns:
            (是否成功, 消息)
        """
        if not self.is_enabled():
            return False, "⚠️ 委托炼丹功能暂未开放"

        client_player = await self._fresh(await self._resolve_player(client))
        if not client_player:
            return False, "❌ 你还未踏入修仙之路！"

        # 元神状态（批次1）：需要先复活才能进行委托活动
        if getattr(client_player, "is_soul_state", False):
            return False, (
                "⚠️ 元神状态无法委托炼丹，请先复活！\n"
                "💡 发送「元神状态」查看元神详情\n"
                "💡 背包中有还魂丹时，发送「使用还魂丹」可立即完美复活"
            )

        try:
            recipe_id = int(recipe_id)
            quantity = int(quantity)
            fee = int(fee)
        except (TypeError, ValueError):
            return False, "❌ 参数错误：配方ID / 数量 / 手续费必须是数字"

        recipe = self._get_recipe(recipe_id)
        if not recipe:
            return False, (
                f"❌ 配方不存在（ID：{recipe_id}）\n"
                "💡 发送「丹药配方」查看可用配方ID"
            )

        if quantity <= 0:
            return False, "❌ 数量必须大于 0"
        if quantity > self.max_quantity:
            return False, f"❌ 单笔委托数量不能超过 {self.max_quantity}"
        if fee < 0:
            return False, "❌ 手续费不能为负数"
        if fee > self.max_fee:
            return False, f"❌ 手续费不能超过 {self.max_fee:,} 灵石"

        # 指定炼丹师：校验目标玩家与炼丹师资格
        target_alchemist = None
        if alchemist_id:
            alchemist_id = str(alchemist_id)
            if alchemist_id == client_player.user_id:
                return False, "❌ 不能委托自己炼制丹药"
            target_alchemist = await self.db.get_player_by_id(alchemist_id)
            if not target_alchemist:
                return False, "❌ 指定的炼丹师不存在，请检查 @ 的对象"
            if not await self.alchemy.has_alchemist_title(target_alchemist):
                name = target_alchemist.user_name or alchemist_id
                return False, (
                    f"❌ 【{name}】还不是炼丹师，无法指定接单\n"
                    "💡 也可不指定炼丹师，发布到公开委托板等待接单"
                )

        # 未接单委托数量上限
        pending_count = await self.db.count_commissions(
            client_id=client_player.user_id, status="pending"
        )
        if pending_count >= self.max_pending:
            return False, (
                f"❌ 你已有 {pending_count} 个待接单委托（上限 {self.max_pending} 个）\n"
                "💡 可发送「取消委托 <编号>」撤回部分委托"
            )

        # 托管材料与灵石
        total_materials = self._total_materials(recipe, quantity)
        ring_materials, gold_materials = self._split_materials(total_materials)
        total_gold = gold_materials + fee

        missing = self._check_ring_materials(client_player, ring_materials)
        if missing:
            return False, (
                f"❌ 材料不足，无法发布委托：\n" + "\n".join(missing) + "\n"
                "💡 材料由委托人提供，炼丹师不承担材料消耗\n"
                "💡 发送「材料查询 <材料名>」查看材料获取途径"
            )

        if int(client_player.gold or 0) < total_gold:
            return False, (
                f"❌ 灵石不足，需要 {total_gold:,} 灵石\n"
                f"  · 手续费：{fee:,}\n"
                f"  · 配方炼制消耗：{gold_materials:,}\n"
                f"  · 当前：{int(client_player.gold or 0):,}"
            )

        # 1) 锁定储物戒材料
        ok, msg = await self._take_ring_materials(client_player, ring_materials)
        if not ok:
            return False, msg

        # 2) 锁定灵石（手续费的托管金额一并扣除，完成时支付给炼丹师）
        fresh = await self.db.get_player_by_id(client_player.user_id)
        if not fresh:
            await self._give_ring_materials(client_player, ring_materials)
            return False, "❌ 委托失败，请稍后重试"

        fresh.gold = max(0, int(fresh.gold or 0) - total_gold)
        await self.db.update_player(fresh)

        # 3) 落库；失败则把材料与灵石退回委托人
        commission_id = await self.db.create_commission_record(
            client_id=fresh.user_id,
            recipe_id=recipe_id,
            quantity=quantity,
            fee=fee,
            materials=total_materials,
            alchemist_id=alchemist_id,
        )
        if not commission_id:
            fresh = await self.db.get_player_by_id(client_player.user_id) or fresh
            fresh.gold = int(fresh.gold or 0) + total_gold
            await self.db.update_player(fresh)
            back_ok, back_msg = await self._give_ring_materials(fresh, ring_materials)
            if not back_ok:
                logger.error(
                    f"[委托炼丹] 委托落库失败且材料退回异常（用户 {fresh.user_id}）: {back_msg}"
                )
                return False, f"❌ 创建委托失败：{back_msg}\n💡 请清理储物戒后联系管理员"
            return False, "❌ 创建委托失败，材料与灵石已退回"

        star_text = self.alchemy.star_text(recipe.get("star", 1)) if self.alchemy else ""
        if target_alchemist:
            target_text = f"@{(target_alchemist.user_name or alchemist_id)}（指定炼丹师）"
        else:
            target_text = "公开委托板（任意炼丹师可接单）"

        lines = [
            "✅ 委托发布成功！",
            SEP,
            f"📋 委托编号：{commission_id}",
            f"⚗️ 炼制：{recipe['name']} {star_text} ×{quantity}",
            f"🧺 托管材料：{self._material_text(total_materials)}",
            f"💰 托管手续费：{fee:,} 灵石",
            f"👤 接单对象：{target_text}",
            SEP,
            "材料与灵石已锁定，等待炼丹师接单",
            f"💡 发送「我的委托」查看进度，发送「取消委托 {commission_id}」可撤回",
        ]
        return True, "\n".join(lines)

    # ===== 委托查询 =====

    def _format_commission_line(self, commission: Dict) -> str:
        """单条委托的展示文本"""
        recipe = self._get_recipe(commission.get("recipe_id"))
        pill_name = recipe["name"] if recipe else f"配方{commission.get('recipe_id')}"
        star_text = self.alchemy.star_text(recipe.get("star", 1)) if (recipe and self.alchemy) else ""
        status = STATUS_TEXT.get(str(commission.get("status")), str(commission.get("status")))
        target = "指定炼丹师" if commission.get("alchemist_id") else "公开委托"
        alchemist_name = commission.get("alchemist_name")
        return (
            f"📋 #{commission.get('commission_id')}｜{pill_name} {star_text} ×{commission.get('quantity')}\n"
            f"   委托人：{commission.get('client_name') or commission.get('client_id')}"
            f"｜手续费：{int(commission.get('fee', 0) or 0):,} 灵石\n"
            + (f"   炼丹师：{alchemist_name}｜" if alchemist_name else "   类型：")
            + f"{target}｜状态：{status}"
        )

    async def _decorate(self, commissions: List[Dict]) -> List[Dict]:
        """补充委托人 / 炼丹师道号"""
        for commission in commissions:
            commission["client_name"] = await self._display_name(commission.get("client_id"))
            if commission.get("alchemist_id"):
                commission["alchemist_name"] = await self._display_name(commission.get("alchemist_id"))
        return commissions

    async def list_commissions(
        self,
        status: str = "pending",
        alchemist_id: str = None,
        viewer_id: str = "",
    ) -> str:
        """列出委托（供「委托列表」使用）

        - 传入 alchemist_id：查看指定炼丹师的委托（含指定给他的待接单委托）
        - 否则：查看公开委托板（未指定炼丹师的委托）
        """
        if not self.is_enabled():
            return "⚠️ 委托炼丹功能暂未开放"

        if alchemist_id:
            commissions = await self.db.list_commissions(
                status="pending", public_only=False, limit=50
            )
            commissions = [
                c for c in commissions
                if c.get("alchemist_id") in (None, str(alchemist_id))
            ]
        else:
            commissions = await self.db.list_commissions(
                status="pending", public_only=True, order_by_fee=True, limit=20
            )

        if not commissions:
            return (
                "📭 当前没有待接单的委托\n"
                f"{SEP}\n"
                "💡 发送「委托炼丹 <配方ID> <数量> <手续费>」发布委托\n"
                "💡 成为炼丹师后可接单赚取手续费（发送「成为炼丹师」查看条件）"
            )

        await self._decorate(commissions)

        lines = [
            "📜 委托炼丹 · 待接单",
            SEP,
            f"当前共有 {len(commissions)} 条委托（按手续费从高到低）",
            "",
        ]
        for commission in commissions:
            lines.append(self._format_commission_line(commission))
            lines.append("")

        lines.append(SEP)
        lines.append("💡 炼丹师发送「接取委托 <编号>」接单")
        lines.append("💡 发送「委托炼丹 <配方ID> <数量> <手续费> [@炼丹师]」发布委托")
        return "\n".join(lines).strip()

    async def get_my_commissions(self, user: Union[Player, str]) -> str:
        """查看自己发布 / 接单的委托（供「我的委托」使用）"""
        player = await self._resolve_player(user)
        if not player:
            return "❌ 你还未踏入修仙之路！"

        user_id = player.user_id
        is_alchemist = bool(
            self.alchemy and await self.alchemy.has_alchemist_title(player)
        )

        published = await self.db.list_commissions(client_id=user_id, limit=20)
        accepted = await self.db.list_commissions(alchemist_id=user_id, limit=20)
        await self._decorate(published)
        await self._decorate(accepted)

        lines = ["📋 我的委托", SEP]

        lines.append(f"🧺 我发布的委托（{len(published)} 条）")
        if published:
            for commission in published:
                lines.append("  " + self._format_commission_line(commission).replace("\n", "\n  "))
        else:
            lines.append("  · 暂无")
        lines.append("")

        lines.append(f"⚗️ 我接单的委托（{len(accepted)} 条）")
        if accepted:
            for commission in accepted:
                lines.append("  " + self._format_commission_line(commission).replace("\n", "\n  "))
        else:
            lines.append("  · 暂无（成为炼丹师后可接单）" if not is_alchemist else "  · 暂无")
        lines.append("")

        lines.append(SEP)
        lines.append("💡 「取消委托 <编号>」撤回未接单的委托（全额返还）")
        lines.append("💡 「放弃委托 <编号>」炼丹师放弃已接单的委托（材料退回委托人）")
        return "\n".join(lines)

    # ===== 接单 / 完成 =====

    async def can_accept(self, alchemist: Player, commission: Dict) -> Tuple[bool, str]:
        """判断炼丹师能否接取该委托"""
        if not alchemist or not commission:
            return False, "委托不存在"

        if commission.get("status") != "pending":
            return False, f"委托已{STATUS_TEXT.get(str(commission.get('status')), commission.get('status'))}"

        target = commission.get("alchemist_id")
        if target and str(target) != alchemist.user_id:
            return False, "该委托指定了其他炼丹师"

        if self.alchemy is None:
            return False, "炼丹系统未初始化"

        if not await self.alchemy.has_alchemist_title(alchemist):
            return False, (
                "你还不是炼丹师，无法接单\n"
                "💡 发送「成为炼丹师」查看条件（金丹期 + 成功炼制10次 + 10000灵石）"
            )

        recipe = self._get_recipe(commission.get("recipe_id"))
        if not recipe:
            return False, "配方已失效，请联系委托人取消"

        required_level = int(recipe.get("level_required", 0) or 0)
        if int(alchemist.level_index or 0) < required_level:
            return False, (
                f"你的境界不足，炼制【{recipe['name']}】需要"
                f"【{self.alchemy.level_name(required_level, alchemist)}】"
            )

        recipe_id = int(recipe.get("id", commission.get("recipe_id") or 0))
        if self.alchemy.is_learnable_recipe(recipe_id):
            learned = await self.db.has_learned_recipe(alchemist.user_id, recipe_id)
            if not learned:
                return False, (
                    f"你尚未学习【{recipe['name']}】的配方，无法接单\n"
                    "💡 成为炼丹师会自动解锁还魂丹配方"
                )
        return True, ""

    async def accept_commission(
        self,
        alchemist: Union[Player, str],
        commission_id: int,
    ) -> Tuple[bool, str]:
        """炼丹师接单（领取托管材料）"""
        if not self.is_enabled():
            return False, "⚠️ 委托炼丹功能暂未开放"

        alchemist_player = await self._fresh(await self._resolve_player(alchemist))
        if not alchemist_player:
            return False, "❌ 你还未踏入修仙之路！"

        if getattr(alchemist_player, "is_soul_state", False):
            return False, "⚠️ 元神状态无法接单，请先复活！"

        try:
            commission_id = int(commission_id)
        except (TypeError, ValueError):
            return False, "❌ 委托编号必须是数字"

        commission = await self.db.get_commission(commission_id)
        if not commission:
            return False, "❌ 委托不存在"

        # 0) 限制同时进行中的委托数量，避免炼丹师大量囤单
        in_progress = await self.db.list_commissions(
            status="in_progress", alchemist_id=alchemist_player.user_id, limit=10
        )
        if len(in_progress) >= self.max_active:
            return False, (
                f"❌ 你同时进行的委托不能超过 {self.max_active} 个，请先完成或放弃已有委托"
            )

        can_accept, reason = await self.can_accept(alchemist_player, commission)
        if not can_accept:
            return False, f"❌ {reason}"

        # 1) 储物戒空间检查（接单后材料会转交给炼丹师）
        ring_materials, gold_materials = self._split_materials(commission.get("materials", {}))
        can_receive, space_msg = self._check_receive_space(alchemist_player, ring_materials)
        if not can_receive:
            return False, f"❌ {space_msg}"

        # 2) 原子接单（防并发重复接单）
        claimed = await self.db.claim_commission(commission_id, alchemist_player.user_id)
        if not claimed:
            return False, "❌ 手慢了，该委托已被其他炼丹师接走"

        # 3) 转交托管材料（失败则退回待接单状态）
        ok, msg = await self._give_ring_materials(alchemist_player, ring_materials)
        if not ok:
            # 回滚时保留原本的「指定炼丹师」，公开委托则回到委托板
            await self.db.release_commission(commission_id, commission.get("alchemist_id"))
            return False, f"{msg}\n委托已退回委托板"

        if gold_materials > 0:
            fresh = await self.db.get_player_by_id(alchemist_player.user_id)
            if fresh:
                fresh.gold = int(fresh.gold or 0) + gold_materials
                await self.db.update_player(fresh)

        recipe = self._get_recipe(commission.get("recipe_id"))
        pill_name = recipe["name"] if recipe else f"配方{commission.get('recipe_id')}"
        quantity = int(commission.get("quantity", 1) or 1)
        fee = int(commission.get("fee", 0) or 0)

        lines = [
            "✅ 接单成功！",
            SEP,
            f"📋 委托编号：{commission_id}",
            f"⚗️ 需炼制：{pill_name} ×{quantity}",
            f"🧺 已领取材料：{self._material_text(commission.get('materials', {}))}",
            f"💰 完成可得手续费：{fee:,} 灵石",
            SEP,
            f"💡 发送「炼丹 {commission.get('recipe_id')}」炼制（材料已在你身上）",
            f"💡 炼好后发送「完成委托 {commission_id}」交付成品",
        ]
        return True, "\n".join(lines)

    async def complete_commission(
        self,
        alchemist: Union[Player, str],
        commission_id: int,
    ) -> Tuple[bool, str]:
        """完成委托（交付成品，领取手续费）"""
        if not self.is_enabled():
            return False, "⚠️ 委托炼丹功能暂未开放"

        alchemist_player = await self._fresh(await self._resolve_player(alchemist))
        if not alchemist_player:
            return False, "❌ 你还未踏入修仙之路！"

        try:
            commission_id = int(commission_id)
        except (TypeError, ValueError):
            return False, "❌ 委托编号必须是数字"

        commission = await self.db.get_commission(commission_id)
        if not commission:
            return False, "❌ 委托不存在"

        if str(commission.get("alchemist_id") or "") != alchemist_player.user_id:
            return False, "❌ 这不是你接取的委托"

        if commission.get("status") != "in_progress":
            status_text = STATUS_TEXT.get(str(commission.get("status")), commission.get("status"))
            return False, f"❌ 委托状态异常（{status_text}），无法交付"

        recipe = self._get_recipe(commission.get("recipe_id"))
        if not recipe:
            return False, "❌ 配方已失效，请联系委托人取消"

        pill_name = recipe["name"]
        quantity = int(commission.get("quantity", 1) or 1)
        fee = int(commission.get("fee", 0) or 0)
        client_id = str(commission.get("client_id") or "")

        # 1) 检查成品（丹药背包）
        fresh_alchemist = await self.db.get_player_by_id(alchemist_player.user_id)
        if not fresh_alchemist:
            return False, "❌ 玩家数据不存在，请稍后重试"

        inventory = fresh_alchemist.get_pills_inventory()
        owned = int(inventory.get(pill_name, 0) or 0)
        if owned < quantity:
            return False, (
                f"❌ 成品不足：需要【{pill_name}】×{quantity}（当前：{owned}）\n"
                f"💡 发送「炼丹 {commission.get('recipe_id')}」炼制后再交付"
            )

        client_player = await self.db.get_player_by_id(client_id)
        if not client_player:
            return False, "❌ 委托人不存在，无法交付（请联系管理员处理）"

        # 2) 炼丹师：扣除成品 + 领取手续费（同一行数据一次写入）
        if owned - quantity <= 0:
            inventory.pop(pill_name, None)
        else:
            inventory[pill_name] = owned - quantity
        fresh_alchemist.set_pills_inventory(inventory)
        fresh_alchemist.gold = int(fresh_alchemist.gold or 0) + fee
        await self.db.update_player(fresh_alchemist)

        # 3) 委托人：接收成品（失败则回滚炼丹师一侧）
        client_inventory = client_player.get_pills_inventory()
        client_inventory[pill_name] = int(client_inventory.get(pill_name, 0) or 0) + quantity
        client_player.set_pills_inventory(client_inventory)
        try:
            await self.db.update_player(client_player)
        except Exception as e:
            logger.error(f"[委托炼丹] 交付成品失败，回滚炼丹师数据: {e}")
            rollback = await self.db.get_player_by_id(alchemist_player.user_id)
            if rollback:
                rollback_inventory = rollback.get_pills_inventory()
                rollback_inventory[pill_name] = (
                    int(rollback_inventory.get(pill_name, 0) or 0) + quantity
                )
                rollback.set_pills_inventory(rollback_inventory)
                rollback.gold = max(0, int(rollback.gold or 0) - fee)
                await self.db.update_player(rollback)
            return False, "❌ 交付失败，请稍后重试（成品与手续费已还原）"

        # 4) 标记完成
        completed = await self.db.complete_commission_record(commission_id)
        if not completed:
            logger.warning(f"[委托炼丹] 委托 {commission_id} 状态更新失败（成品已交付）")

        star_text = self.alchemy.star_text(recipe.get("star", 1)) if self.alchemy else ""
        client_name = client_player.user_name or client_id
        return True, "\n".join([
            "✅ 委托完成！",
            SEP,
            f"📋 委托编号：{commission_id}",
            f"⚗️ 交付：{pill_name} {star_text} ×{quantity}",
            f"👤 委托人：{client_name}",
            f"💰 获得手续费：{fee:,} 灵石",
            SEP,
            "成品已交付给委托人",
        ])

    # ===== 取消 / 放弃 =====

    async def cancel_commission(
        self,
        client: Union[Player, str],
        commission_id: int,
    ) -> Tuple[bool, str]:
        """委托人取消未接单的委托（材料与手续费全额返还）"""
        client_player = await self._fresh(await self._resolve_player(client))
        if not client_player:
            return False, "❌ 你还未踏入修仙之路！"

        try:
            commission_id = int(commission_id)
        except (TypeError, ValueError):
            return False, "❌ 委托编号必须是数字"

        commission = await self.db.get_commission(commission_id)
        if not commission:
            return False, "❌ 委托不存在"

        if str(commission.get("client_id")) != client_player.user_id:
            return False, "❌ 这不是你的委托"

        if commission.get("status") != "pending":
            status_text = STATUS_TEXT.get(str(commission.get("status")), commission.get("status"))
            return False, (
                f"❌ 委托已{status_text}，无法取消\n"
                "💡 已接单的委托只能由炼丹师「放弃委托」退回"
            )

        return await self._refund_and_close(commission, reason="取消")

    async def abandon_commission(
        self,
        alchemist: Union[Player, str],
        commission_id: int,
    ) -> Tuple[bool, str]:
        """炼丹师放弃已接单的委托（材料与手续费退回委托人）"""
        alchemist_player = await self._fresh(await self._resolve_player(alchemist))
        if not alchemist_player:
            return False, "❌ 你还未踏入修仙之路！"

        try:
            commission_id = int(commission_id)
        except (TypeError, ValueError):
            return False, "❌ 委托编号必须是数字"

        commission = await self.db.get_commission(commission_id)
        if not commission:
            return False, "❌ 委托不存在"

        if str(commission.get("alchemist_id") or "") != alchemist_player.user_id:
            return False, "❌ 这不是你接取的委托"

        if commission.get("status") != "in_progress":
            status_text = STATUS_TEXT.get(str(commission.get("status")), commission.get("status"))
            return False, f"❌ 委托状态异常（{status_text}），无法放弃"

        # 已接单：材料在炼丹师手上，需要先收回再退回委托人
        ring_materials, gold_materials = self._split_materials(commission.get("materials", {}))

        # 预检查委托人的储物戒空间：空间不足时直接拒绝，避免材料在搬运途中丢失
        client_player = await self.db.get_player_by_id(str(commission.get("client_id") or ""))
        if not client_player:
            return False, "❌ 委托人已不存在，无法放弃委托（请联系管理员处理）"
        can_receive, space_msg = self._check_receive_space(client_player, ring_materials)
        if not can_receive:
            return False, (
                f"❌ 无法放弃委托：委托人的{space_msg}\n"
                "💡 请委托人在线清理储物戒后再试，或先炼制成品后发送「完成委托」"
            )

        missing = self._check_ring_materials(alchemist_player, ring_materials)
        if missing:
            return False, (
                "❌ 你身上的托管材料不足，无法放弃委托：\n"
                + "\n".join(missing)
                + "\n💡 请先凑齐托管材料（或炼制出成品后发送「完成委托」）"
            )

        ok, msg = await self._take_ring_materials(alchemist_player, ring_materials)
        if not ok:
            return False, msg

        if gold_materials > 0:
            fresh = await self.db.get_player_by_id(alchemist_player.user_id)
            if fresh:
                fresh.gold = max(0, int(fresh.gold or 0) - gold_materials)
                await self.db.update_player(fresh)

        # 状态先回到待接单，避免退回失败导致炼丹师白拿材料
        await self.db.release_commission(commission_id)
        commission["alchemist_id"] = None

        refund_ok, refund_msg = await self._refund_and_close(commission, reason="放弃")
        if refund_ok:
            return True, refund_msg.replace(
                "材料与灵石已全额返还委托人",
                "材料与灵石已全额返还委托人\n💡 若委托人仍有委托需求，可重新发布委托",
            )
        return False, refund_msg

    async def _refund_and_close(
        self,
        commission: Dict,
        reason: str,
    ) -> Tuple[bool, str]:
        """把托管材料与手续费退回委托人，并删除委托记录"""
        commission_id = int(commission.get("commission_id"))
        client_id = str(commission.get("client_id") or "")
        fee = int(commission.get("fee", 0) or 0)
        ring_materials, gold_materials = self._split_materials(commission.get("materials", {}))

        client_player = await self._fresh(await self.db.get_player_by_id(client_id))
        if not client_player:
            return False, "❌ 委托人已不存在，无法退回材料（请联系管理员处理）"

        # 储物戒空间检查：空间不足时保持委托不变，避免材料丢失
        can_receive, space_msg = self._check_receive_space(client_player, ring_materials)
        if not can_receive:
            return False, (
                f"❌ 无法退回：委托人的{space_msg}\n"
                "💡 请委托人清理储物戒后重试"
            )

        ok, msg = await self._give_ring_materials(client_player, ring_materials)
        if not ok:
            return False, f"{msg}\n💡 请委托人清理储物戒后重试"

        refund_gold = fee + gold_materials
        if refund_gold > 0:
            fresh_client = await self.db.get_player_by_id(client_id) or client_player
            fresh_client.gold = int(fresh_client.gold or 0) + refund_gold
            await self.db.update_player(fresh_client)

        await self.db.delete_commission(commission_id)

        client_name = getattr(client_player, "user_name", "") or client_id
        return True, "\n".join([
            f"✅ 委托已{reason}",
            SEP,
            f"📋 委托编号：{commission_id}",
            f"👤 委托人：{client_name}",
            f"🧺 退回材料：{self._material_text(commission.get('materials', {}))}",
            f"💰 退回灵石：{refund_gold:,}",
            SEP,
            "材料与灵石已全额返还委托人",
        ])

    # ===== 炼丹师信息 =====

    async def get_alchemist_info(self, player: Player) -> str:
        """炼丹师信息面板（含统计与进行中的委托）"""
        if not player:
            return "❌ 你还未踏入修仙之路！"

        stats = await self.db.get_alchemy_stats(player.user_id)
        is_alchemist = bool(self.alchemy and await self.alchemy.has_alchemist_title(player))
        accepted = await self.db.list_commissions(
            status="in_progress", alchemist_id=player.user_id, limit=10
        )

        lines = [
            "🎖️ 炼丹师信息",
            SEP,
            f"🧪 累计炼制：{int(stats.get('total', 0) or 0)} 次",
            f"✨ 成功炼制：{int(stats.get('success', 0) or 0)} 次",
            f"🎖️ 当前称号：{'炼丹师' if is_alchemist else '未获得'}",
        ]
        if is_alchemist and self.alchemy:
            bonus = int(float(self.alchemy.get_setting("alchemist_bonus", 0.15) or 0) * 100)
            lines.append(f"📈 炼丹加成：+{bonus}%")
        else:
            require_text = (
                self.alchemy.get_alchemist_requirement_text(player)
                if self.alchemy and hasattr(self.alchemy, "get_alchemist_requirement_text")
                else "金丹期 + 成功炼制 10 次 + 10000 灵石"
            )
            lines.append(f"📜 成为炼丹师：{require_text}")

        lines.append(SEP)
        lines.append(f"📋 进行中的委托：{len(accepted)} 个")
        for commission in accepted:
            recipe = self._get_recipe(commission.get("recipe_id"))
            pill_name = recipe["name"] if recipe else f"配方{commission.get('recipe_id')}"
            lines.append(
                f"  · #{commission.get('commission_id')} {pill_name} ×{commission.get('quantity')}"
                f"（手续费 {int(commission.get('fee', 0) or 0):,}）"
            )

        lines.append(SEP)
        lines.append("💡 「委托列表」查看委托板｜「我的委托」查看委托进度")
        return "\n".join(lines)

    async def get_alchemist_ranking(self, limit: int = 5) -> str:
        """炼丹师排行（按成功炼制次数）"""
        rows = await self.db.get_top_alchemists(limit=limit)
        lines = ["🏆 炼丹师排行", SEP]
        if not rows:
            lines.append("暂无数据，快来成为第一位炼丹师吧！")
            return "\n".join(lines)

        medals = ["🥇", "🥈", "🥉"]
        for index, row in enumerate(rows):
            medal = medals[index] if index < len(medals) else f"{index + 1}."
            name = row.get("user_name") or row.get("user_id")
            total = int(row.get("total_attempts", 0) or 0)
            success = int(row.get("success_count", 0) or 0)
            rate = f"{int(success / total * 100)}%" if total > 0 else "--"
            lines.append(f"{medal} {name}｜成功炼制 {success} 次｜成功率 {rate}")
        lines.append(SEP)
        lines.append("💡 发送「成为炼丹师」加入炼丹师行列")
        return "\n".join(lines)
