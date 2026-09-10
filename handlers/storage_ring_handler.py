# handlers/storage_ring_handler.py

import re

from astrbot.api.event import AstrMessageEvent
from astrbot.api.all import At, Plain
from ..data import DataBase
from ..core import StorageRingManager
from ..config_manager import ConfigManager
from ..models import Player
from .utils import player_required, extract_command_args

CMD_STORAGE_RING = "储物戒"
CMD_STORE_ITEM = "存入"
CMD_RETRIEVE_ITEM = "取出"
CMD_UPGRADE_RING = "更换储物戒"
CMD_DISCARD_ITEM = "丢弃"
CMD_GIFT_ITEM = "赠予"
CMD_ACCEPT_GIFT = "接收"
CMD_REJECT_GIFT = "拒绝"
CMD_STORE_ALL = "存入所有"
CMD_RETRIEVE_ALL = "取出所有"
CMD_SEARCH_ITEM = "搜索物品"
CMD_VIEW_CATEGORY = "查看分类"
CMD_REFINE_MATERIAL = "炼化材料"
CMD_REFINE_CATALOG = "炼化图鉴"
CMD_REFINE_ALL = "一键炼化"
CMD_REFINE_ALL_ALT = "炼化全部"

MATERIAL_REFINING_VALUES = {
    "灵兽内丹": (500, 2000),
    "妖兽精血": (350, 1400),
    "玄铁": (300, 1200),
    "星辰石": (900, 3600),
    "天材地宝": (2500, 10000),
    "功法残页": (1800, 7200),
    "混沌精华": (8000, 32000),
    "神兽之骨": (12000, 48000),
    "远古秘籍": (18000, 72000),
    "仙器碎片": (30000, 120000),
}


def _to_item_count(value) -> int:
    """解析储物戒物品数量，兼容 int 与 {count, bound} 两种存储格式。"""
    if isinstance(value, dict):
        value = value.get("count", 0)
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return count if count > 0 else 0


# 物品分类定义
ITEM_CATEGORIES = {
    "材料": ["灵草", "精铁", "玄铁", "星辰石", "灵石碎片", "灵兽毛皮", "灵兽内丹",
             "妖兽精血", "功法残页", "秘境精华", "天材地宝", "混沌精华", "神兽之骨",
             "远古秘籍", "仙器碎片",
             # 秘境材料（Lv.1-8）
             "玄铁", "灵兽骨", "精铁", "星辰石", "灵兽内丹", "玄冰晶", "天材地宝",
             "紫金神铁", "龙鳞", "仙灵草", "传承玉简", "混沌石",
             "血魂石", "星核碎片", "魔晶", "元婴精华", "不朽骨",
             "虚空结晶", "仙器碎片", "空间石", "法则碎片", "时光沙",
             "混沌精华", "神铁", "万界石", "道韵结晶", "大道碎片",
             "仙晶", "天劫雷晶", "仙灵根", "涅槃石", "仙道本源",
             "大罗金精", "混元道石", "天道碎片", "圣灵之心", "鸿蒙紫气"],
    "装备": ["武器", "防具", "法器"],
    "功法": ["心法", "技能"],
    "其他": []
}

__all__ = ["StorageRingHandler"]


class StorageRingHandler:
    """储物戒系统处理器"""

    def __init__(self, db: DataBase, config_manager: ConfigManager, item_registry=None):
        self.db = db
        self.config_manager = config_manager
        self.item_registry = item_registry
        self.storage_ring_manager = StorageRingManager(db, config_manager)

    @player_required
    async def handle_storage_ring(self, player: Player, event: AstrMessageEvent):
        """显示储物戒信息"""
        display_name = event.get_sender_name()

        # 获取储物戒信息
        ring_info = self.storage_ring_manager.get_storage_ring_info(player)

        lines = [
            f"=== {display_name} 的储物戒 ===\n",
            f"【{ring_info['name']}】（{ring_info['rank']}）\n",
            f"{ring_info['description']}\n",
            f"\n容量：{ring_info['used']}/{ring_info['capacity']}格\n",
            f"━━━━━━━━━━━━━━━\n",
        ]

        # 按分类显示存储的物品
        items = ring_info['items']
        if items:
            categorized = self._categorize_items(items)
            for category, cat_items in categorized.items():
                if cat_items:
                    lines.append(f"【{category}】\n")
                    for item_name, count in cat_items:
                        label = self._item_label(item_name)
                        if count > 1:
                            lines.append(f"  · {item_name}×{count}{label}\n")
                        else:
                            lines.append(f"  · {item_name}{label}\n")
        else:
            lines.append("【存储物品】空\n")

        # 空间警告
        warning = self.storage_ring_manager.get_space_warning(player)
        if warning:
            lines.append(f"\n{warning}\n")

        lines.append(f"\n{'=' * 28}\n")
        lines.append(f"存入：{CMD_STORE_ITEM} 物品名 [数量]\n")
        lines.append(f"取出：{CMD_RETRIEVE_ITEM} 物品名 [数量]\n")
        lines.append(f"搜索：{CMD_SEARCH_ITEM} 关键词\n")
        lines.append(f"炼化：{CMD_REFINE_MATERIAL} 材料名 [数量/全部]\n")
        lines.append(f"一键炼化：{CMD_REFINE_ALL}（炼化全部可炼化材料）\n")
        lines.append(f"物品信息：物品信息 物品名（查看类型/品质/用途）\n")
        lines.append(f"升级：{CMD_UPGRADE_RING} 储物戒名")

        yield event.plain_result("".join(lines))

    @player_required
    async def handle_refine_catalog(self, player: Player, event: AstrMessageEvent):
        """显示Boss材料的明确炼化用途。"""
        lines = ["=== 材料炼化图鉴 ===\n", "以下Boss材料均可稳定炼化为灵石与修为。\n"]
        for item_name, (gold, experience) in MATERIAL_REFINING_VALUES.items():
            lines.append(f"【{item_name}】×1 → 灵石{gold:,} + 修为{experience:,}\n")

        # 统计当前储物戒中可直接炼化的材料
        items = player.get_storage_ring_items()
        owned_lines = []
        total_gold = 0
        total_exp = 0
        for item_name, (gold_each, exp_each) in MATERIAL_REFINING_VALUES.items():
            count = _to_item_count(items.get(item_name))
            if count <= 0:
                continue
            gold_gain = gold_each * count
            exp_gain = exp_each * count
            total_gold += gold_gain
            total_exp += exp_gain
            owned_lines.append(f"  · {item_name}×{count} → 灵石{gold_gain:,} + 修为{exp_gain:,}\n")

        if owned_lines:
            lines.append("\n📦 你可炼化的材料：\n")
            lines.extend(owned_lines)
            lines.append(f"合计可获：灵石{total_gold:,} + 修为{total_exp:,}\n")
            lines.append(f"💡 使用 {CMD_REFINE_ALL} 一次性全部炼化\n")
        else:
            lines.append("\n📦 储物戒中暂时没有可炼化材料\n")

        lines.append(f"\n用法：{CMD_REFINE_MATERIAL} 材料名 数量\n")
        lines.append(f"示例：{CMD_REFINE_MATERIAL} 仙器碎片 1；{CMD_REFINE_MATERIAL} 功法残页 全部\n")
        lines.append(f"一键炼化：{CMD_REFINE_ALL}（无需参数，炼化所有可炼化材料）")
        yield event.plain_result("".join(lines))

    @player_required
    async def handle_refine_material(
        self, player: Player, event: AstrMessageEvent, args: str, quantity: str = ""
    ):
        """炼化Boss材料，发放灵石和修为。"""
        # AstrBot 按空格绑定形参，多余 token（数量/全部）会被丢弃，这里从原始消息重新解析
        raw_args = extract_command_args(event, CMD_REFINE_MATERIAL).strip()
        if not raw_args:
            raw_args = " ".join(part.strip() for part in (args, quantity) if part and part.strip())

        if not raw_args:
            yield event.plain_result(
                f"用法：{CMD_REFINE_MATERIAL} 材料名 [数量/全部]\n"
                f"使用 {CMD_REFINE_CATALOG} 查看每种材料的炼化价值"
            )
            return

        parts = raw_args.rsplit(" ", 1)
        item_name = parts[0].strip()
        quantity_text = parts[1].strip() if len(parts) == 2 else "1"
        if item_name not in MATERIAL_REFINING_VALUES:
            yield event.plain_result(f"【{item_name}】不是可炼化材料，请使用 {CMD_REFINE_CATALOG} 查看图鉴")
            return

        available = self.storage_ring_manager.get_item_count(player, item_name)
        if quantity_text in {"全部", "all", "ALL"}:
            count = available
        elif quantity_text.isdigit():
            count = int(quantity_text)
        else:
            yield event.plain_result("数量必须是正整数或“全部”")
            return

        if count <= 0:
            yield event.plain_result(f"储物戒中没有【{item_name}】可炼化")
            return
        if count > available:
            yield event.plain_result(f"【{item_name}】数量不足，当前仅有 {available} 个")
            return

        gold_each, experience_each = MATERIAL_REFINING_VALUES[item_name]
        gold_gain = gold_each * count
        experience_gain = experience_each * count
        success, current_count = await self.db.ext.refine_storage_material(
            player.user_id,
            item_name,
            count,
            gold_gain,
            experience_gain,
        )
        if not success:
            yield event.plain_result(f"炼化失败：【{item_name}】数量不足，当前仅有 {current_count} 个")
            return
        yield event.plain_result(
            f"🔥 炼化成功！\n【{item_name}】×{count}\n"
            f"获得灵石：+{gold_gain:,}\n获得修为：+{experience_gain:,}"
        )

    @player_required
    async def handle_refine_all(self, player: Player, event: AstrMessageEvent):
        """一键炼化：把储物戒中所有可炼化材料一次性炼化。"""
        async for r in self._refine_all(player, event):
            yield r

    @player_required
    async def handle_refine_all_alias(self, player: Player, event: AstrMessageEvent):
        """一键炼化（别名词条：炼化全部）。"""
        async for r in self._refine_all(player, event):
            yield r

    async def _refine_all(self, player: Player, event: AstrMessageEvent):
        """执行一键炼化并输出明细（不加装饰器，便于多个指令名复用）。"""
        success, results, total_gold, total_exp = await self.db.ext.refine_storage_materials_batch(
            player.user_id,
            MATERIAL_REFINING_VALUES,
        )

        if not success or not results:
            yield event.plain_result(
                "❌ 储物戒中没有可炼化的材料\n"
                f"使用 {CMD_REFINE_CATALOG} 查看可炼化材料与炼化价值"
            )
            return

        lines = ["🔥 一键炼化完成！\n", "━━━━━━━━━━━━━━━\n"]
        total_count = 0
        for item_name, count, gold_gain, exp_gain in results:
            total_count += count
            lines.append(f"【{item_name}】×{count} → 灵石+{gold_gain:,} 修为+{exp_gain:,}\n")

        lines.append("━━━━━━━━━━━━━━━\n")
        lines.append(f"共炼化：{total_count} 个材料（{len(results)} 种）\n")
        lines.append(f"获得灵石：+{total_gold:,}\n")
        lines.append(f"获得修为：+{total_exp:,}")
        yield event.plain_result("".join(lines))

    @player_required
    async def handle_store_item(
        self, player: Player, event: AstrMessageEvent, args: str, quantity: str = ""
    ):
        """存入物品到储物戒 - 已禁用手动存入"""
        yield event.plain_result(
            "📦 储物戒说明：\n"
            "物品会在以下情况自动存入储物戒：\n"
            "  · 商店购买物品\n"
            "  · 历练/秘境获得物品\n"
            "  · Boss击杀掉落\n"
            "  · 悬赏任务奖励\n"
            "  · 卸下装备\n"
            "\n⚠️ 不支持手动存入物品"
        )

    @player_required
    async def handle_retrieve_item(
        self, player: Player, event: AstrMessageEvent, args: str, quantity: str = ""
    ):
        """从储物戒取出物品"""
        args = self._join_args(event, CMD_RETRIEVE_ITEM, args, quantity)
        if not args:
            yield event.plain_result(
                f"请指定要取出的物品\n"
                f"用法：{CMD_RETRIEVE_ITEM} 物品名 [数量]\n"
                f"示例：{CMD_RETRIEVE_ITEM} 精铁 5"
            )
            return

        parts = args.rsplit(" ", 1)

        # 解析物品名和数量
        if len(parts) == 2 and parts[1].isdigit():
            item_name = parts[0]
            count = int(parts[1])
        else:
            item_name = args
            count = 1

        if count <= 0:
            yield event.plain_result("数量必须大于0")
            return

        # 取出物品
        success, message = await self.storage_ring_manager.retrieve_item(player, item_name, count)

        if success:
            yield event.plain_result(f"✅ {message}")
        else:
            yield event.plain_result(f"❌ {message}")

    @player_required
    async def handle_discard_item(
        self, player: Player, event: AstrMessageEvent, args: str, quantity: str = ""
    ):
        """丢弃储物戒中的物品"""
        args = self._join_args(event, CMD_DISCARD_ITEM, args, quantity)
        if not args:
            yield event.plain_result(
                f"请指定要丢弃的物品\n"
                f"用法：{CMD_DISCARD_ITEM} 物品名 [数量]\n"
                f"示例：{CMD_DISCARD_ITEM} 精铁 5\n"
                f"⚠️ 丢弃的物品将永久销毁！"
            )
            return

        parts = args.rsplit(" ", 1)

        # 解析物品名和数量
        if len(parts) == 2 and parts[1].isdigit():
            item_name = parts[0]
            count = int(parts[1])
        else:
            item_name = args
            count = 1

        if count <= 0:
            yield event.plain_result("数量必须大于0")
            return

        # 丢弃物品
        success, message = await self.storage_ring_manager.discard_item(player, item_name, count)

        if success:
            yield event.plain_result(f"🗑️ {message}")
        else:
            yield event.plain_result(f"❌ {message}")

    @player_required
    async def handle_gift_item(self, player: Player, event: AstrMessageEvent, args: str):
        """赠予物品给其他玩家"""
        target_id = None
        item_name = None
        count = 1

        # 从消息链中提取 At 组件和 Plain 文本
        text_parts = []
        message_obj = getattr(event, "message_obj", None)
        message_chain = getattr(message_obj, "message", []) or []
        
        for comp in message_chain:
            if isinstance(comp, At):
                # 兼容多种At属性名
                if target_id is None:
                    for attr_name in ("qq", "target", "uin", "user_id"):
                        target_value = getattr(comp, attr_name, None)
                        if target_value is not None and str(target_value).strip():
                            target_id = str(target_value).strip()
                            break
            elif isinstance(comp, Plain):
                text_parts.append(str(getattr(comp, "text", "") or ""))

        plain_text = "".join(text_parts).strip()
        text_content = (args or plain_text or "").strip()

        text_content = re.sub(
            rf"^[^\w\s]*\s*{re.escape(CMD_GIFT_ITEM)}",
            "",
            text_content,
            count=1,
        ).strip()

        if target_id and text_content:
            text_content = re.sub(
                r"^(?:@[^\s]+|\[CQ:at,[^\]]+\]|<at[^>]*>)\s*",
                "",
                text_content,
                count=1,
                flags=re.IGNORECASE,
            ).strip()
        
        # 如果没有从At组件获取到target_id，尝试从文本解析纯数字QQ号
        if not target_id and text_content:
            parts = text_content.split(None, 1)
            if len(parts) >= 1:
                potential_id = parts[0].lstrip('@')
                if potential_id.isdigit() and len(potential_id) >= 5:
                    target_id = potential_id
                    text_content = parts[1].strip() if len(parts) > 1 else ""

        # 解析物品名和数量
        if text_content:
            parts = text_content.rsplit(None, 1)
            if len(parts) == 2 and parts[1].isdigit():
                item_name = parts[0].strip()
                count = int(parts[1])
            else:
                item_name = text_content.strip()

        # 验证必要参数
        if not target_id:
            yield event.plain_result(
                f"请指定赠予对象\n"
                f"用法：{CMD_GIFT_ITEM} @某人 物品名 [数量]\n"
                f"或：{CMD_GIFT_ITEM} QQ号 物品名 [数量]\n"
                f"示例：{CMD_GIFT_ITEM} 123456789 精铁 5"
            )
            return

        if not item_name:
            yield event.plain_result("请指定要赠予的物品名称")
            return

        if count <= 0:
            yield event.plain_result("数量必须大于0")
            return

        # 检查物品是否在储物戒中
        if not self.storage_ring_manager.has_item(player, item_name, count):
            current = self.storage_ring_manager.get_item_count(player, item_name)
            if current == 0:
                yield event.plain_result(f"储物戒中没有【{item_name}】")
            else:
                yield event.plain_result(f"储物戒中【{item_name}】数量不足（当前：{current}个）")
            return

        target_player = await self.db.get_player_by_id(target_id)
        if not target_player:
            yield event.plain_result(f"目标玩家（QQ:{target_id}）尚未开始修仙")
            return

        if target_id == str(player.user_id):
            yield event.plain_result("不能赠予物品给自己")
            return

        # 先从储物戒中取出物品
        success, _ = await self.storage_ring_manager.retrieve_item(player, item_name, count)
        if not success:
            yield event.plain_result("赠予失败：无法取出物品")
            return

        # 存储待处理的赠予请求到数据库
        sender_name = event.get_sender_name()
        try:
            await self.db.ext.create_pending_gift(
                receiver_id=target_id,
                sender_id=player.user_id,
                sender_name=sender_name,
                item_name=item_name,
                count=count,
                expires_hours=24  # 24小时后过期
            )
        except Exception:
            await self.storage_ring_manager.store_item(player, item_name, count, silent=True)
            yield event.plain_result("赠予失败：无法创建赠予请求，物品已返还")
            return

        yield event.plain_result(
            f"📦 赠予请求已发送！\n"
            f"【{item_name}】x{count} → @{target_id}\n"
            f"等待对方确认...（24小时内有效）\n"
            f"对方可使用 {CMD_ACCEPT_GIFT} 接收或 {CMD_REJECT_GIFT} 拒绝"
        )

    @player_required
    async def handle_accept_gift(self, player: Player, event: AstrMessageEvent):
        """接收赠予的物品"""
        user_id = player.user_id

        # 从数据库获取待处理的赠予请求
        gift = await self.db.ext.get_pending_gift(user_id)
        if not gift:
            yield event.plain_result("你没有待接收的赠予物品")
            return

        item_name = gift["item_name"]
        count = gift["count"]
        sender_name = gift["sender_name"]
        gift_id = gift["id"]

        # 尝试存入接收者的储物戒
        success, message = await self.storage_ring_manager.store_item(player, item_name, count)

        if success:
            # 删除数据库中的赠予请求
            await self.db.ext.delete_pending_gift(gift_id)
            yield event.plain_result(
                f"✅ 已接收来自【{sender_name}】的赠予！\n"
                f"获得：【{item_name}】x{count}"
            )
        else:
            # 存入失败，物品返还给发送者
            sender_id = gift["sender_id"]
            sender_player = await self.db.get_player_by_id(sender_id)
            if sender_player:
                await self.storage_ring_manager.store_item(sender_player, item_name, count, silent=True)

            # 删除数据库中的赠予请求
            await self.db.ext.delete_pending_gift(gift_id)
            yield event.plain_result(
                f"❌ 接收失败：{message}\n"
                f"物品已返还给【{sender_name}】"
            )

    @player_required
    async def handle_reject_gift(self, player: Player, event: AstrMessageEvent):
        """拒绝赠予的物品"""
        user_id = player.user_id

        # 从数据库获取待处理的赠予请求
        gift = await self.db.ext.get_pending_gift(user_id)
        if not gift:
            yield event.plain_result("你没有待处理的赠予请求")
            return

        item_name = gift["item_name"]
        count = gift["count"]
        sender_id = gift["sender_id"]
        sender_name = gift["sender_name"]
        gift_id = gift["id"]

        # 物品返还给发送者
        sender_player = await self.db.get_player_by_id(sender_id)
        if sender_player:
            await self.storage_ring_manager.store_item(sender_player, item_name, count, silent=True)

        # 删除数据库中的赠予请求
        await self.db.ext.delete_pending_gift(gift_id)
        yield event.plain_result(
            f"已拒绝来自【{sender_name}】的赠予\n"
            f"【{item_name}】x{count} 已返还"
        )

    @player_required
    async def handle_upgrade_ring(self, player: Player, event: AstrMessageEvent, ring_name: str):
        """升级/更换储物戒"""
        if not ring_name or ring_name.strip() == "":
            # 显示可用的储物戒列表
            rings = self.storage_ring_manager.get_all_storage_rings()
            current_capacity = self.storage_ring_manager.get_ring_capacity(player.storage_ring)

            lines = [
                f"=== 储物戒列表 ===\n",
                f"当前：【{player.storage_ring}】({current_capacity}格)\n",
                f"━━━━━━━━━━━━━━━\n",
            ]

            for ring in rings:
                # 标记当前装备
                if ring["name"] == player.storage_ring:
                    marker = "✓ "
                elif ring["capacity"] <= current_capacity:
                    marker = "✗ "  # 容量不高于当前的
                else:
                    marker = "  "

                level_name = self.storage_ring_manager._format_required_level(ring["required_level_index"])
                lines.append(
                    f"{marker}【{ring['name']}】({ring['rank']})\n"
                    f"    容量：{ring['capacity']}格 | 需求：{level_name}\n"
                )

            lines.append(f"\n用法：{CMD_UPGRADE_RING} 储物戒名")
            lines.append("\n注：储物戒只能升级，不能卸下")

            yield event.plain_result("".join(lines))
            return

        ring_name = ring_name.strip()

        # 检查是否为储物戒类型
        ring_config = self.storage_ring_manager.get_storage_ring_config(ring_name)
        if not ring_config:
            yield event.plain_result(f"未找到储物戒：{ring_name}")
            return

        # 升级储物戒
        success, message = await self.storage_ring_manager.upgrade_ring(player, ring_name)

        if success:
            yield event.plain_result(f"✅ {message}")
        else:
            yield event.plain_result(f"❌ {message}")

    @staticmethod
    def _join_args(event, command_name: str, *parts: str) -> str:
        """拼出指令后的完整参数文本（兼容 AstrBot 只绑定首个 token 的行为）"""
        raw = extract_command_args(event, command_name).strip()
        if raw:
            return raw
        return " ".join(part.strip() for part in parts if part and part.strip())

    def _item_label(self, item_name: str) -> str:
        """物品名后缀信息，例如（武器·凡品）、（可炼化⚗️）"""
        tags = []

        if self.item_registry:
            brief = self.item_registry.get_brief(item_name)
            # 与所在栏目名重复的信息不再展示（如【材料】下的“材料”）
            if brief and brief != self.item_registry.get_category(item_name):
                tags.append(brief)

        if item_name in MATERIAL_REFINING_VALUES:
            tags.append("可炼化⚗️")

        return f"（{'·'.join(tags)}）" if tags else ""

    def _resolve_category(self, item_name: str) -> str:
        """判断物品所属分类：统一索引 -> 关键词/配置 -> 后缀推断 -> 其他"""
        registry = self.item_registry
        if registry and registry.is_known(item_name):
            return registry.get_category(item_name)

        legacy = self._legacy_category(item_name)
        if legacy != "其他":
            return legacy

        if registry:
            guessed = registry.get_category(item_name)
            if guessed != "其他":
                return guessed
        return "其他"

    def _legacy_category(self, item_name: str) -> str:
        """旧的关键词 + items.json 类型判定（作为索引兜底）"""
        for category, keywords in ITEM_CATEGORIES.items():
            if category == "其他":
                continue
            for keyword in keywords:
                if keyword in item_name or item_name in keyword:
                    return category

        item_config = self.config_manager.items_data.get(item_name, {})
        item_type = item_config.get("type", "")

        if item_type in ["weapon", "武器", "armor", "防具", "法器", "饰品"]:
            return "装备"
        if item_type in ["technique", "功法", "main_technique"]:
            return "功法"
        if item_type in ["material", "材料"]:
            return "材料"
        return "其他"

    def _categorize_items(self, items: dict) -> dict:
        """将物品按分类整理"""
        result = {cat: [] for cat in ITEM_CATEGORIES.keys()}
        for item_name, count in items.items():
            category = self._resolve_category(item_name)
            result.setdefault(category, []).append((item_name, count))

        # 移除空分类
        return {k: v for k, v in result.items() if v}

    @player_required
    async def handle_search_item(self, player: Player, event: AstrMessageEvent, keyword: str):
        """搜索储物戒中的物品"""
        if not keyword or keyword.strip() == "":
            yield event.plain_result(
                f"请指定搜索关键词\n"
                f"用法：{CMD_SEARCH_ITEM} 关键词\n"
                f"示例：{CMD_SEARCH_ITEM} 灵草"
            )
            return

        keyword = keyword.strip().lower()
        items = player.get_storage_ring_items()
        
        # 模糊搜索
        matched = []
        for item_name, count in items.items():
            if keyword in item_name.lower():
                matched.append((item_name, count))
        
        if not matched:
            yield event.plain_result(f"未找到包含「{keyword}」的物品")
            return
        
        lines = [f"=== 搜索结果：{keyword} ===\n"]
        for item_name, count in matched:
            label = self._item_label(item_name)
            lines.append(f"  · {item_name}×{count}{label}\n")
        lines.append(f"\n共找到 {len(matched)} 种物品")
        
        yield event.plain_result("".join(lines))

    @player_required
    async def handle_store_all(self, player: Player, event: AstrMessageEvent, category: str = None):
        """批量存入物品（预留接口，实际物品来源需要其他系统配合）"""
        yield event.plain_result(
            f"📦 批量存入功能说明：\n"
            f"当前物品会在以下情况自动存入储物戒：\n"
            f"  · 商店购买物品\n"
            f"  · 历练/秘境获得物品\n"
            f"  · Boss击杀掉落\n"
            f"  · 悬赏任务奖励\n"
            f"  · 卸下装备\n"
            f"\n所有物品获取后会自动存入储物戒"
        )

    @player_required
    async def handle_retrieve_all(self, player: Player, event: AstrMessageEvent, category: str = None):
        """批量取出指定分类的物品"""
        if not category or category.strip() == "":
            yield event.plain_result(
                f"请指定要取出的分类\n"
                f"用法：{CMD_RETRIEVE_ALL} 分类名\n"
                f"可用分类：材料、装备、功法、其他\n"
                f"示例：{CMD_RETRIEVE_ALL} 材料"
            )
            return
        
        category = category.strip()
        if category not in ITEM_CATEGORIES:
            yield event.plain_result(f"未知分类：{category}\n可用分类：材料、装备、功法、其他")
            return
        
        items = player.get_storage_ring_items()
        categorized = self._categorize_items(items)
        cat_items = categorized.get(category, [])
        
        if not cat_items:
            yield event.plain_result(f"储物戒中没有【{category}】类物品")
            return
        
        # 取出所有该分类的物品
        retrieved = []
        failed = []
        for item_name, count in cat_items:
            success, msg = await self.storage_ring_manager.retrieve_item(player, item_name, count)
            if success:
                retrieved.append(f"{item_name}×{count}")
            else:
                failed.append(f"{item_name}：{msg}")
        
        lines = [f"=== 批量取出【{category}】 ===\n"]
        if retrieved:
            lines.append(f"✅ 已取出：\n")
            for item in retrieved:
                lines.append(f"  · {item}\n")
        if failed:
            lines.append(f"\n❌ 失败：\n")
            for item in failed:
                lines.append(f"  · {item}\n")
        
        yield event.plain_result("".join(lines))
