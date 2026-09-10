# handlers/shop_handler.py

import time
import re
from astrbot.api.event import AstrMessageEvent
from astrbot.api import AstrBotConfig, logger
from ..data import DataBase
from ..core import ShopManager, EquipmentManager, PillManager, StorageRingManager
from ..models import Player
from ..config_manager import ConfigManager
from .utils import player_required, extract_command_args

__all__ = ["ShopHandler"]

class ShopHandler:
    """商店处理器"""
    
    ITEM_ACQUIRE_HINTS = {
        'pill': "丹阁刷新、秘境稀有掉落",
        'exp_pill': "丹阁、炼丹系统、历练/秘境奖励",
        'utility_pill': "丹阁稀有、秘境/Boss 掉落",
        'legacy_pill': "百宝阁限量，购买后立即生效",
        'weapon': "器阁、Boss 掉落",
        'armor': "器阁、Boss 掉落",
        'accessory': "器阁、Boss 掉落",
        'main_technique': "百宝阁稀有刷新",
        'technique': "百宝阁刷新购买",
        'material': "历练、秘境、悬赏、灵田收获与百宝阁限量",
    }

    def __init__(self, db: DataBase, config: AstrBotConfig, config_manager: ConfigManager, item_registry=None):
        self.db = db
        self.config = config
        self.config_manager = config_manager
        self.item_registry = item_registry
        self.shop_manager = ShopManager(config, config_manager)
        self.storage_ring_manager = StorageRingManager(db, config_manager)
        self.equipment_manager = EquipmentManager(db, config_manager, self.storage_ring_manager)
        self.pill_manager = PillManager(db, config_manager)
        access_control = self.config.get("ACCESS_CONTROL", {})
        self.shop_manager_ids = {
            str(user_id)
            for user_id in access_control.get("SHOP_MANAGERS", [])
        }

    async def _ensure_pavilion_refreshed(self, pavilion_id: str, item_getter, count: int) -> None:
        """确保阁楼已刷新"""
        last_refresh_time, current_items = await self.db.get_shop_data(pavilion_id)
        if current_items:
            updated = self.shop_manager.ensure_items_have_stock(current_items)
            updated = self.shop_manager.ensure_items_have_market_ids(current_items, pavilion_id) or updated
            if updated:
                await self.db.update_shop_data(pavilion_id, last_refresh_time, current_items)
        refresh_hours = self.config.get("PAVILION_REFRESH_HOURS", 6)
        if not current_items or self.shop_manager.should_refresh_shop(last_refresh_time, refresh_hours):
            new_items = self.shop_manager.generate_pavilion_items(item_getter, count, pavilion_id)
            await self.db.update_shop_data(pavilion_id, int(time.time()), new_items)

    async def handle_pill_pavilion(self, event: AstrMessageEvent):
        """处理丹阁命令 - 展示丹药列表"""
        count = self.config.get("PAVILION_PILL_COUNT", 10)
        await self._ensure_pavilion_refreshed("pill_pavilion", self.shop_manager.get_pills_for_display, count)
        last_refresh, items = await self.db.get_shop_data("pill_pavilion")
        if not items:
            yield event.plain_result("丹阁暂无丹药出售。")
            return
        refresh_hours = self.config.get("PAVILION_REFRESH_HOURS", 6)
        display = self.shop_manager.format_pavilion_display("丹阁", items, refresh_hours, last_refresh)
        yield event.plain_result(display)

    async def handle_weapon_pavilion(self, event: AstrMessageEvent):
        """处理器阁命令 - 展示武器列表"""
        count = self.config.get("PAVILION_WEAPON_COUNT", 10)
        await self._ensure_pavilion_refreshed("weapon_pavilion", self.shop_manager.get_weapons_for_display, count)
        last_refresh, items = await self.db.get_shop_data("weapon_pavilion")
        if not items:
            yield event.plain_result("器阁暂无武器出售。")
            return
        refresh_hours = self.config.get("PAVILION_REFRESH_HOURS", 6)
        display = self.shop_manager.format_pavilion_display("器阁", items, refresh_hours, last_refresh)
        yield event.plain_result(display)

    async def handle_treasure_pavilion(self, event: AstrMessageEvent):
        """处理百宝阁命令 - 展示所有物品"""
        count = self.config.get("PAVILION_TREASURE_COUNT", 15)
        await self._ensure_pavilion_refreshed("treasure_pavilion", self.shop_manager.get_all_items_for_display, count)
        last_refresh, items = await self.db.get_shop_data("treasure_pavilion")
        if not items:
            yield event.plain_result("百宝阁暂无物品出售。")
            return
        refresh_hours = self.config.get("PAVILION_REFRESH_HOURS", 6)
        display = self.shop_manager.format_pavilion_display("百宝阁", items, refresh_hours, last_refresh)
        yield event.plain_result(display)

    async def _find_item_in_pavilions(self, item_name: str):
        """在所有阁楼中查找物品"""
        for pavilion_id in ["pill_pavilion", "weapon_pavilion", "treasure_pavilion"]:
            last_refresh, items = await self.db.get_shop_data(pavilion_id)
            if items:
                updated = self.shop_manager.ensure_items_have_stock(items)
                updated = self.shop_manager.ensure_items_have_market_ids(items, pavilion_id) or updated
                if updated:
                    await self.db.update_shop_data(pavilion_id, last_refresh, items)
                for item in items:
                    same_name = item.get('name') == item_name
                    same_code = str(item.get('market_id', '')).upper() == item_name.strip().upper()
                    if (same_name or same_code) and item.get('stock', 0) > 0:
                        return pavilion_id, item
        return None, None

    @player_required
    async def handle_buy(self, player: Player, event: AstrMessageEvent, item_name: str = ""):
        """处理购买物品命令"""
        if not item_name or item_name.strip() == "":
            yield event.plain_result("请指定要购买的物品名称，例如：购买 青铜剑")
            return

        # 兼容全角空格/数字与“x10”写法
        normalized = item_name.strip().replace("　", " ")
        normalized = normalized.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        quantity = 1
        item_part = normalized

        def parse_qty(text: str):
            text = re.sub(r"\s+", " ", text)
            m = re.match(r"^(.*?)(?:\s+(\d+)|[xX＊*]\s*(\d+))$", text)
            if m:
                part = m.group(1).strip()
                qty_str = m.group(2) or m.group(3)
                return part, max(1, int(qty_str))
            return text.strip(), 1

        item_part, quantity = parse_qty(normalized)

        # 若指令解析只传入物品名（忽略数量），从原始消息重新解析完整参数
        if quantity == 1:
            raw_msg = extract_command_args(event, ("购买", "购买物品"))
            if raw_msg:
                raw_msg = re.sub(r"\s+", " ", raw_msg).strip()
                raw_msg = raw_msg.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
                item_part, quantity = parse_qty(raw_msg)

        item_name = item_part

        pavilion_id, target_item = await self._find_item_in_pavilions(item_name)
        if not target_item:
            yield event.plain_result(f"无效的市场ID或物品名称: {item_name}\n请使用“市场”命令查看商品短码，或等待商店刷新。")
            return

        item_name = target_item['name']

        stock = target_item.get('stock', 0)
        if quantity > stock:
            yield event.plain_result(f"【{item_name}】库存不足，当前库存: {stock}。")
            return

        price = target_item['price']
        total_price = price * quantity
        if player.gold < total_price:
            yield event.plain_result(
                f"灵石不足！\n【{target_item['name']}】价格: {price} 灵石\n"
                f"购买数量: {quantity}\n需要灵石: {total_price}\n你的灵石: {player.gold}"
            )
            return

        item_type = target_item['type']
        result_lines = []

        await self.db.conn.execute("BEGIN IMMEDIATE")
        try:
            player = await self.db.get_player_by_id(event.get_sender_id())
            if player.gold < total_price:
                await self.db.conn.rollback()
                yield event.plain_result(
                    f"灵石不足！\n【{target_item['name']}】价格: {price} 灵石\n"
                    f"购买数量: {quantity}\n需要灵石: {total_price}\n你的灵石: {player.gold}"
                )
                return

            reserved, _, remaining = await self.db.decrement_shop_item_stock(pavilion_id, item_name, quantity, external_transaction=True)
            if not reserved:
                await self.db.conn.rollback()
                yield event.plain_result(f"【{item_name}】已售罄，请等待刷新。")
                return

            if item_type in ['weapon', 'armor', 'main_technique', 'technique', 'accessory']:
                success, msg = await self.storage_ring_manager.store_item(player, target_item['name'], quantity, external_transaction=True)
                if success:
                    type_name = {"weapon": "武器", "armor": "防具", "main_technique": "心法", "technique": "功法", "accessory": "饰品"}.get(item_type, "装备")
                    result_lines.append(f"成功购买{type_name}【{target_item['name']}】x{quantity}，已存入储物戒。")
                else:
                    result_lines.append(f"成功购买【{target_item['name']}】x{quantity}。")
                    result_lines.append(f"⚠️ 存入储物戒失败：{msg}")
            elif item_type in ['pill', 'exp_pill', 'utility_pill']:
                await self.pill_manager.add_pill_to_inventory(player, target_item['name'], count=quantity)
                result_lines.append(f"成功购买【{target_item['name']}】x{quantity}，已添加到背包。")
            elif item_type == 'legacy_pill':
                success, message = await self._apply_legacy_pill_effects(player, target_item, quantity)
                if not success:
                    await self.db.conn.rollback()
                    yield event.plain_result(message)
                    return
                result_lines.append(message)
            elif item_type == 'material':
                success, msg = await self.storage_ring_manager.store_item(player, target_item['name'], quantity, external_transaction=True)
                if success:
                    result_lines.append(f"成功购买材料【{target_item['name']}】x{quantity}，已存入储物戒。")
                else:
                    result_lines.append(f"成功购买材料【{target_item['name']}】x{quantity}。")
                    result_lines.append(f"⚠️ 存入储物戒失败：{msg}")
            elif item_type == '功法':
                success, msg = await self.storage_ring_manager.store_item(player, target_item['name'], quantity, external_transaction=True)
                if success:
                    result_lines.append(f"成功购买功法【{target_item['name']}】x{quantity}，已存入储物戒。")
                else:
                    result_lines.append(f"成功购买功法【{target_item['name']}】x{quantity}。")
                    result_lines.append(f"⚠️ 存入储物戒失败：{msg}")
            else:
                await self.db.conn.rollback()
                yield event.plain_result(f"未知的物品类型：{item_type}")
                return

            player = await self.db.get_player_by_id(event.get_sender_id())
            if not player:
                await self.db.conn.rollback()
                yield event.plain_result("购买失败：玩家数据不存在。")
                return
            player.gold -= total_price
            await self.db.update_player(player)
            await self.db.conn.commit()
            
            result_lines.append(f"花费灵石: {total_price}，剩余: {player.gold}")
            result_lines.append(f"剩余库存: {remaining}" if remaining > 0 else "该物品已售罄！")
            yield event.plain_result("\n".join(result_lines))
        except Exception as e:
            await self.db.conn.rollback()
            logger.error(f"购买异常: {e}")
            raise

    def _get_acquire_hint(self, item_type: str) -> str:
        """根据类型返回获取提示"""
        return self.ITEM_ACQUIRE_HINTS.get(item_type, "商店刷新或活动奖励")

    async def handle_item_info(self, event: AstrMessageEvent, item_name: str = ""):
        """查询物品/丹药的具体效果与获取方式"""
        if not item_name or item_name.strip() == "":
            yield event.plain_result(
                "请指定要查询的物品名称\n"
                "用法：物品信息 <名称>\n"
                "示例：物品信息 筑基丹"
            )
            return

        item = self.shop_manager.find_item_by_name(item_name.strip())
        if not item:
            item_data = self.config_manager.items_data.get(item_name.strip())
            if item_data and item_data.get('type') == '材料' and item_data.get('shop_weight') == 0:
                item = {
                    'id': item_data.get('id', item_name.strip()),
                    'name': item_data['name'],
                    'type': 'material',
                    'price': item_data.get('price', 0),
                    'rank': item_data.get('rank', '凡品'),
                    'data': item_data,
                }
        if not item:
            # 秘境/Boss 掉落等未上架物品：使用统一物品索引兜底
            registry_lines = self._get_registry_item_info(item_name.strip())
            if registry_lines:
                yield event.plain_result("\n".join(registry_lines))
                return
            yield event.plain_result(f"未找到物品【{item_name}】，请检查名称或等待刷新。")
            return

        detail_text = self.shop_manager.get_item_details(item)
        item_data = item.get('data', {})
        if item.get('type') == 'material' and item_data.get('shop_weight') == 0:
            acquire_hint = "世界Boss击杀掉落；可使用 /炼化材料 转换为灵石与修为"
        else:
            acquire_hint = self._get_acquire_hint(item.get('type', ''))

        lines = [
            detail_text,
            f"获取途径：{acquire_hint}",
            "💡 使用 /丹阁、/器阁、/百宝阁 查看当前售卖物品"
        ]
        yield event.plain_result("\n".join(lines))

    def _get_registry_item_info(self, item_name: str) -> list:
        """从统一物品索引获取物品信息（覆盖秘境/Boss 等掉落物品）"""
        registry = getattr(self, "item_registry", None)
        if not registry:
            return []
        if not registry.is_known(item_name):
            return []

        lines = list(registry.get_detail_lines(item_name))
        category = registry.get_category(item_name)

        hints = {
            "材料": "可用于 /炼化材料 或 /炼化图鉴 查看炼化收益；部分材料为炼丹配方原料",
            "装备": "使用 /修仙装备 <名称> 穿戴（饰品暂未开放独立装备栏）",
            "功法": "使用 /修仙装备 <功法名> 学习，最多同时装备3个",
            "丹药": "使用 /服用丹药 <名称> 使用（丹药不占用储物戒）",
        }
        lines.append(f"💡 {hints.get(category, '可在 /储物戒 中查看持有数量')}")
        return lines

    async def _apply_legacy_pill_effects(self, player: Player, item: dict, quantity: int) -> tuple:
        """应用旧系统丹药效果（items.json 中的丹药）

        实际结算统一由 PillManager 处理，保证「购买即服用」与「丹药背包服用」结果一致。
        """
        return await self.pill_manager.apply_legacy_pill_effects(
            player,
            item.get("name", ""),
            item.get("data", {}) or {},
            quantity,
        )
