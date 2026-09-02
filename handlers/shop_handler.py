# handlers/shop_handler.py

import time
import re
from astrbot.api.event import AstrMessageEvent
from astrbot.api import AstrBotConfig, logger
from ..data import DataBase
from ..core import ShopManager, EquipmentManager, PillManager, StorageRingManager
from ..models import Player
from ..config_manager import ConfigManager
from .utils import player_required

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
        'technique': "百宝阁、Boss 掉落",
        'material': "历练、秘境、悬赏、灵田收获与百宝阁限量",
    }

    def __init__(self, db: DataBase, config: AstrBotConfig, config_manager: ConfigManager):
        self.db = db
        self.config = config
        self.config_manager = config_manager
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
            if updated:
                await self.db.update_shop_data(pavilion_id, last_refresh_time, current_items)
        refresh_hours = self.config.get("PAVILION_REFRESH_HOURS", 6)
        if not current_items or self.shop_manager.should_refresh_shop(last_refresh_time, refresh_hours):
            new_items = self.shop_manager.generate_pavilion_items(item_getter, count)
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
            _, items = await self.db.get_shop_data(pavilion_id)
            if items:
                for item in items:
                    if item['name'] == item_name and item.get('stock', 0) > 0:
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

        # 若指令解析只传入物品名（忽略数量），尝试从原始消息再解析一次
        if quantity == 1:
            try:
                raw_msg = event.get_message_str().strip()
                if raw_msg.startswith("购买"):
                    raw_msg = raw_msg[len("购买"):].strip()
                raw_msg = raw_msg.replace("　", " ")
                raw_msg = raw_msg.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
                item_part, quantity = parse_qty(raw_msg)
            except Exception:
                pass

        item_name = item_part

        pavilion_id, target_item = await self._find_item_in_pavilions(item_name)
        if not target_item:
            yield event.plain_result(f"没有找到【{item_name}】，请检查物品名称或等待刷新。")
            return

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
            yield event.plain_result(f"未找到物品【{item_name}】，请检查名称或等待刷新。")
            return

        detail_text = self.shop_manager.get_item_details(item)
        acquire_hint = self._get_acquire_hint(item.get('type', ''))

        lines = [
            detail_text,
            f"获取途径：{acquire_hint}",
            "💡 使用 /丹阁、/器阁、/百宝阁 查看当前售卖物品"
        ]
        yield event.plain_result("\n".join(lines))

    async def _apply_legacy_pill_effects(self, player: Player, item: dict, quantity: int) -> tuple:
        """应用旧系统丹药效果（items.json中的丹药）

        Args:
            player: 玩家对象
            item: 物品配置字典
            quantity: 购买数量

        Returns:
            (是否成功, 消息)
        """
        effects = item.get('data', {}).get('effect', {})
        if not effects:
            return False, f"丹药【{item['name']}】无效果配置。"

        effect_msgs = []
        pill_name = item['name']

        # 处理各种效果（乘以数量）
        for _ in range(quantity):
            # 恢复/扣除气血
            if 'add_hp' in effects:
                hp_change = effects['add_hp']
                if player.cultivation_type == "体修":
                    old_blood = player.blood_qi
                    player.blood_qi = max(0, min(player.max_blood_qi, player.blood_qi + hp_change))
                    if hp_change > 0:
                        effect_msgs.append(f"气血+{player.blood_qi - old_blood}")
                    else:
                        effect_msgs.append(f"气血{hp_change}")
                else:
                    old_qi = player.spiritual_qi
                    player.spiritual_qi = max(0, min(player.max_spiritual_qi, player.spiritual_qi + hp_change))
                    if hp_change > 0:
                        effect_msgs.append(f"灵气+{player.spiritual_qi - old_qi}")
                    else:
                        effect_msgs.append(f"灵气{hp_change}")

            # 增加修为
            if 'add_experience' in effects:
                exp_gain = effects['add_experience']
                player.experience += exp_gain
                effect_msgs.append(f"修为+{exp_gain}")

            # 增加最大气血/灵气上限
            if 'add_max_hp' in effects:
                max_hp_gain = effects['add_max_hp']
                if player.cultivation_type == "体修":
                    player.max_blood_qi += max_hp_gain
                    effect_msgs.append(f"最大气血+{max_hp_gain}")
                else:
                    player.max_spiritual_qi += max_hp_gain
                    effect_msgs.append(f"最大灵气+{max_hp_gain}")

            # 增加灵力（映射到法伤）
            if 'add_spiritual_power' in effects:
                sp_gain = effects['add_spiritual_power']
                player.magic_damage += sp_gain
                effect_msgs.append(f"法伤+{sp_gain}")

            # 增加精神力
            if 'add_mental_power' in effects:
                mp_gain = effects['add_mental_power']
                player.mental_power += mp_gain
                effect_msgs.append(f"精神力+{mp_gain}")

            # 增加攻击力（映射到物伤）
            if 'add_attack' in effects:
                atk_gain = effects['add_attack']
                player.physical_damage += atk_gain
                if atk_gain > 0:
                    effect_msgs.append(f"物伤+{atk_gain}")
                else:
                    effect_msgs.append(f"物伤{atk_gain}")

            # 增加防御力（映射到物防）
            if 'add_defense' in effects:
                def_gain = effects['add_defense']
                player.physical_defense += def_gain
                if def_gain > 0:
                    effect_msgs.append(f"物防+{def_gain}")
                else:
                    effect_msgs.append(f"物防{def_gain}")

            # 增加/扣除灵石
            if 'add_gold' in effects:
                gold_change = effects['add_gold']
                player.gold += gold_change
                if gold_change > 0:
                    effect_msgs.append(f"灵石+{gold_change}")
                else:
                    effect_msgs.append(f"灵石{gold_change}")

            # 处理突破成功率加成（添加为临时效果，持续1小时）
            if 'add_breakthrough_bonus' in effects:
                bonus = effects['add_breakthrough_bonus']
                import time
                current_effects = player.get_active_pill_effects()
                new_effect = {
                    "pill_name": pill_name,
                    "subtype": "breakthrough_boost",
                    "breakthrough_bonus": bonus,
                    "expiry_time": int(time.time()) + 3600,  # 1小时有效期
                }
                current_effects.append(new_effect)
                player.set_active_pill_effects(current_effects)
                if bonus > 0:
                    effect_msgs.append(f"突破成功率+{int(bonus*100)}%(1小时)")
                else:
                    effect_msgs.append(f"突破成功率{int(bonus*100)}%(1小时)")

        # 确保属性不为负
        player.physical_damage = max(0, player.physical_damage)
        player.magic_damage = max(0, player.magic_damage)
        player.physical_defense = max(0, player.physical_defense)
        player.magic_defense = max(0, player.magic_defense)
        player.mental_power = max(0, player.mental_power)
        player.spiritual_qi = min(player.spiritual_qi, player.max_spiritual_qi)
        player.blood_qi = min(player.blood_qi, player.max_blood_qi)

        await self.db.update_player(player)

        # 去重效果消息
        unique_effects = list(dict.fromkeys(effect_msgs))
        effects_str = "、".join(unique_effects[:5])  # 最多显示5个效果
        if len(unique_effects) > 5:
            effects_str += "..."

        qty_str = f"x{quantity}" if quantity > 1 else ""
        return True, f"服用【{pill_name}】{qty_str}成功！效果：{effects_str}"
