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
        "exp_pill": "丹阁、炼丹系统、世界事件/秘境奖励",
        'utility_pill': "丹阁稀有、秘境/Boss 掉落",
        'legacy_pill': "百宝阁限量，购买后立即生效",
        'weapon': "器阁、Boss 掉落",
        'armor': "器阁、Boss 掉落",
        'accessory': "器阁、Boss 掉落",
        'main_technique': "百宝阁稀有刷新",
        'technique': "百宝阁刷新购买",
        'material': "世界事件、秘境、悬赏、灵田收获与百宝阁限量",
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

    PAVILION_LABELS = {
        "pill_pavilion": "丹阁",
        "weapon_pavilion": "器阁",
        "treasure_pavilion": "百宝阁",
    }
    PAVILION_ALIASES = {
        "丹阁": "pill_pavilion", "丹药": "pill_pavilion", "丹": "pill_pavilion",
        "器阁": "weapon_pavilion", "武器": "weapon_pavilion", "器": "weapon_pavilion",
        "百宝阁": "treasure_pavilion", "宝物": "treasure_pavilion", "百宝": "treasure_pavilion",
    }

    async def _load_pavilion(self, player: Player, pavilion_id: str):
        """读取（必要时生成）玩家所属境界的公共货架

        Returns:
            (shelf_id, last_refresh_time, items)
        """
        shelf_id = self.shop_manager.get_shelf_id(pavilion_id, player.level_index)
        last_refresh_time, items = await self.db.get_shop_data(shelf_id)
        if items:
            updated = self.shop_manager.ensure_items_have_stock(items)
            updated = self.shop_manager.ensure_items_have_market_ids(items, shelf_id) or updated
            if updated:
                await self.db.update_shop_data(shelf_id, last_refresh_time, items)
                await self.db.conn.commit()

        refresh_hours = self.shop_manager.get_auto_refresh_hours()
        if not items or self.shop_manager.should_refresh_shop(last_refresh_time, refresh_hours):
            items = self.shop_manager.generate_shelf_items(
                pavilion_id, player.level_index, self.shop_manager.get_pavilion_count(pavilion_id)
            )
            last_refresh_time = int(time.time())
            await self.db.update_shop_data(shelf_id, last_refresh_time, items)
            await self.db.conn.commit()
        return shelf_id, last_refresh_time, items

    async def _refresh_footer(self, player: Player) -> list:
        """货架页脚：今日剩余刷新次数与下次消耗"""
        today = time.strftime("%Y-%m-%d")
        used = await self.db.get_shop_refresh_count(player.user_id, today)
        limit = self.shop_manager.get_daily_refresh_limit()
        free = self.shop_manager.get_free_refreshes()
        remaining = max(0, limit - used)

        lines = [f"🔄 今日刷新：剩余 {remaining}/{limit} 次"]
        if remaining <= 0:
            lines.append("   今日次数已用完，明日恢复")
        elif used < free:
            lines.append("   本次刷新免费（今日首发）")
        else:
            cost = self.shop_manager.get_refresh_cost(player.level_index)
            lines.append(f"   下次消耗：{cost:,} 灵石")
        lines.append("   刷新商店 <丹阁|器阁|百宝阁> → 立即刷新货架")
        return lines

    async def _render_pavilion(self, player: Player, pavilion_id: str, event: AstrMessageEvent):
        """渲染某个阁楼货架"""
        label = self.PAVILION_LABELS.get(pavilion_id, pavilion_id)
        _, last_refresh, items = await self._load_pavilion(player, pavilion_id)
        if not items:
            yield event.plain_result(f"{label}暂无物品出售。")
            return
        footer = await self._refresh_footer(player)
        display = self.shop_manager.format_pavilion_display(
            self.shop_manager.get_shelf_name(pavilion_id, player.level_index),
            items,
            self.shop_manager.get_auto_refresh_hours(),
            last_refresh,
            footer,
        )
        yield event.plain_result(display)

    @player_required
    async def handle_pill_pavilion(self, player: Player, event: AstrMessageEvent):
        """处理丹阁命令 - 展示丹药列表"""
        async for r in self._render_pavilion(player, "pill_pavilion", event):
            yield r

    @player_required
    async def handle_weapon_pavilion(self, player: Player, event: AstrMessageEvent):
        """处理器阁命令 - 展示武器列表"""
        async for r in self._render_pavilion(player, "weapon_pavilion", event):
            yield r

    @player_required
    async def handle_treasure_pavilion(self, player: Player, event: AstrMessageEvent):
        """处理百宝阁命令 - 展示所有物品"""
        async for r in self._render_pavilion(player, "treasure_pavilion", event):
            yield r

    @player_required
    async def handle_refresh_shop(self, player: Player, event: AstrMessageEvent, pavilion_name: str = ""):
        """消耗灵石刷新当前境界的公共货架（每人每日限次）"""
        target = (pavilion_name or "").strip()
        if not target:
            yield event.plain_result(
                "请指定要刷新的阁楼。\n"
                "用法：刷新商店 <丹阁|器阁|百宝阁>\n"
                "示例：刷新商店 器阁"
            )
            return

        pavilion_id = self.PAVILION_ALIASES.get(target)
        if not pavilion_id:
            yield event.plain_result(f"没有「{target}」这个阁楼。\n用法：刷新商店 <丹阁|器阁|百宝阁>")
            return

        label = self.PAVILION_LABELS[pavilion_id]
        today = time.strftime("%Y-%m-%d")
        limit = self.shop_manager.get_daily_refresh_limit()
        used = await self.db.get_shop_refresh_count(player.user_id, today)
        if used >= limit:
            yield event.plain_result(
                f"今日刷新次数已用完（{used}/{limit}）。\n"
                "明日恢复，期间可等待每 "
                f"{self.shop_manager.get_auto_refresh_hours()} 小时的自动刷新。"
            )
            return

        free = self.shop_manager.get_free_refreshes()
        cost = 0 if used < free else self.shop_manager.get_refresh_cost(player.level_index)
        if cost > 0 and player.gold < cost:
            yield event.plain_result(
                f"灵石不足，刷新{label}需要 {cost:,} 灵石，你当前有 {player.gold:,} 灵石。"
            )
            return

        if cost > 0:
            await self.db.begin_immediate()
            try:
                fresh = await self.db.get_player_by_id(event.get_sender_id())
                if not fresh or fresh.gold < cost:
                    await self.db.conn.rollback()
                    yield event.plain_result("灵石不足，刷新失败。")
                    return
                fresh.gold -= cost
                await self.db.update_player(fresh)
                await self.db.conn.commit()
            except Exception as e:
                await self.db.conn.rollback()
                logger.error(f"刷新商店扣费异常: {e}")
                raise

        shelf_id = self.shop_manager.get_shelf_id(pavilion_id, player.level_index)
        items = self.shop_manager.generate_shelf_items(
            pavilion_id, player.level_index, self.shop_manager.get_pavilion_count(pavilion_id)
        )
        await self.db.update_shop_data(shelf_id, int(time.time()), items)
        await self.db.conn.commit()
        new_used = await self.db.increment_shop_refresh_count(player.user_id, today)

        player = await self.db.get_player_by_id(event.get_sender_id())
        remaining = max(0, limit - new_used)
        header = (
            f"✨ 已刷新【{self.shop_manager.get_shelf_name(pavilion_id, player.level_index)}】\n"
            f"本次消耗：{cost:,} 灵石　剩余灵石：{player.gold:,}\n"
            f"今日剩余刷新次数：{remaining}/{limit}\n"
        )
        yield event.plain_result(header + "\n" + self.shop_manager.format_pavilion_display(
            self.shop_manager.get_shelf_name(pavilion_id, player.level_index),
            items,
            self.shop_manager.get_auto_refresh_hours(),
            int(time.time()),
            await self._refresh_footer(player),
        ))

    async def _find_item_in_pavilions(self, player: Player, item_name: str):
        """在玩家所属境界的所有货架中查找物品"""
        needle = (item_name or "").strip()
        for pavilion_id in ("pill_pavilion", "weapon_pavilion", "treasure_pavilion"):
            shelf_id, _, items = await self._load_pavilion(player, pavilion_id)
            for item in items:
                same_name = item.get('name') == needle
                same_code = str(item.get('market_id', '')).upper() == needle.upper()
                if (same_name or same_code) and item.get('stock', 0) > 0:
                    return shelf_id, item
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

        pavilion_id, target_item = await self._find_item_in_pavilions(player, item_name)
        if not target_item:
            yield event.plain_result(
                f"当前货架上没有「{item_name}」。\n"
                "💡 使用「丹阁」「器阁」「百宝阁」查看你当前境界货架的物品与短码，\n"
                "   或用「刷新商店 <阁楼>」消耗灵石立即换一批货。"
            )
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

        await self.db.begin_immediate()
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
        # 批次2：3 星以上丹药只能炼制获得，还魂丹需手动使用
        if category == "丹药":
            star = 1
            try:
                star = int(self.config_manager.get_pill_star(item_name))
            except Exception:
                star = 1
            max_star = 2
            try:
                max_star = int(self.config_manager.get_max_shop_pill_star())
            except Exception:
                max_star = 2
            if star > max_star:
                hints["丹药"] = f"⚠️ {star}星丹药不上架商店，需自行炼制（丹药不占用储物戒）"
            if item_name == "还魂丹":
                hints["丹药"] = "💀 元神状态发送「使用还魂丹」完美复活（该丹药需手动使用）"

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
