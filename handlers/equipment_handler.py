import re

# handlers/equipment_handler.py

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from ..data import DataBase
from ..core import EquipmentManager, PillManager, StorageRingManager
from ..config_manager import ConfigManager
from ..models import Player
from .utils import player_required, extract_command_args

CMD_SHOW_EQUIPMENT = "我的装备"
# 「装备」与钓鱼等第三方插件指令冲突，本插件统一使用「修仙装备」
CMD_EQUIP_ITEM = "修仙装备"
CMD_EQUIP_ITEM_ALIASES = ("装备物品",)
CMD_UNEQUIP_ITEM = "卸下"
CMD_UNEQUIP_ITEM_ALIASES = ("修仙卸下",)

__all__ = ["EquipmentHandler"]

class EquipmentHandler:
    """装备系统处理器"""

    def __init__(self, db: DataBase, config_manager: ConfigManager):
        self.db = db
        self.config_manager = config_manager
        self.storage_ring_manager = StorageRingManager(db, config_manager)
        self.equipment_manager = EquipmentManager(db, config_manager, self.storage_ring_manager)
        self.pill_manager = PillManager(db, config_manager)

    @player_required
    async def handle_show_equipment(self, player: Player, event: AstrMessageEvent):
        """显示玩家当前装备"""
        display_name = event.get_sender_name()

        # 获取所有已装备物品
        equipped_items = self.equipment_manager.get_equipped_items(
            player,
            self.config_manager.items_data,
            self.config_manager.weapons_data
        )

        await self.pill_manager.update_temporary_effects(player)
        pill_multipliers = self.pill_manager.calculate_pill_attribute_effects(player)

        # 构建装备显示
        equipment_lines = [
            f"=== {display_name} 的装备 ===\n",
            f"【武器】{player.weapon if player.weapon else '未装备'}\n",
            f"【防具】{player.armor if player.armor else '未装备'}\n",
            f"【主修心法】{player.main_technique if player.main_technique else '未装备'}\n",
        ]

        # 功法列表
        techniques_list = player.get_techniques_list()
        equipment_lines.append(f"【功法】({len(techniques_list)}/3)\n")
        if techniques_list:
            for i, tech in enumerate(techniques_list, 1):
                equipment_lines.append(f"  {i}. {tech}\n")
        else:
            equipment_lines.append("  未装备\n")

        # 总属性加成
        if equipped_items:
            equipment_lines.append("\n--- 装备属性加成 ---\n")
            total_attrs = player.get_total_attributes(equipped_items, pill_multipliers)

            # 计算加成值（总属性 - 基础属性）
            magic_damage_bonus = total_attrs["magic_damage"] - player.magic_damage
            physical_damage_bonus = total_attrs["physical_damage"] - player.physical_damage
            magic_defense_bonus = total_attrs["magic_defense"] - player.magic_defense
            physical_defense_bonus = total_attrs["physical_defense"] - player.physical_defense
            mental_power_bonus = total_attrs["mental_power"] - player.mental_power
            max_spiritual_qi_bonus = total_attrs["max_spiritual_qi"] - player.max_spiritual_qi
            max_blood_qi_bonus = total_attrs["max_blood_qi"] - player.max_blood_qi
            lifespan_bonus = total_attrs["lifespan"] - player.lifespan
            exp_multiplier = total_attrs["exp_multiplier"]

            if magic_damage_bonus > 0:
                equipment_lines.append(f"⚔️ 法伤 +{magic_damage_bonus}\n")
            if physical_damage_bonus > 0:
                equipment_lines.append(f"🗡️ 物伤 +{physical_damage_bonus}\n")
            if magic_defense_bonus > 0:
                equipment_lines.append(f"🛡️ 法防 +{magic_defense_bonus}\n")
            if physical_defense_bonus > 0:
                equipment_lines.append(f"🪨 物防 +{physical_defense_bonus}\n")
            if mental_power_bonus > 0:
                equipment_lines.append(f"🧠 精神力 +{mental_power_bonus}\n")
            if max_spiritual_qi_bonus > 0:
                equipment_lines.append(f"✨ 灵气容量 +{max_spiritual_qi_bonus}\n")
            if max_blood_qi_bonus > 0:
                equipment_lines.append(f"🩸 气血容量 +{max_blood_qi_bonus}\n")
            if lifespan_bonus > 0:
                equipment_lines.append(f"⏳ 寿命 +{lifespan_bonus}\n")
            if exp_multiplier > 0:
                equipment_lines.append(f"📈 修为倍率 +{exp_multiplier:.1%}\n")

        equipment_lines.append("=" * 28)

        yield event.plain_result("".join(equipment_lines))

    @player_required
    async def handle_equip_item(self, player: Player, event: AstrMessageEvent, item_name: str):
        """装备物品"""
        item_name = self._normalize_command_argument(
            event, item_name, (CMD_EQUIP_ITEM,) + CMD_EQUIP_ITEM_ALIASES
        )
        if not item_name:
            yield event.plain_result(f"请指定要装备的物品名称\n用法：{CMD_EQUIP_ITEM} 物品名称")
            return

        item = self.equipment_manager.parse_item_from_name(
            item_name,
            self.config_manager.items_data,
            self.config_manager.weapons_data,
        )
        if not item:
            yield event.plain_result(
                f"未找到物品：{item_name}\n"
                f"💡 请确认名称是否正确（可用「储物戒」查看背包、「物品信息 <名称>」查询详情）"
            )
            return

        if item.item_type not in {"weapon", "armor", "main_technique", "technique"}:
            yield event.plain_result(f"【{item_name}】暂不支持装备（饰品暂未开放独立装备栏）")
            return

        # 检查储物戒中是否有该物品
        if not self.storage_ring_manager.has_item(player, item_name, 1):
            yield event.plain_result(
                f"❌ 储物戒中没有【{item_name}】\n"
                f"请先通过购买或获得该装备"
            )
            return

        # 从储物戒取出物品
        success, retrieve_msg = await self.storage_ring_manager.retrieve_item(player, item_name, 1)
        if not success:
            yield event.plain_result(f"❌ 无法从储物戒取出装备：{retrieve_msg}")
            return

        user_id = player.user_id
        player = await self.db.get_player_by_id(user_id)
        if not player:
            logger.error(
                f"装备失败后无法返还物品：user_id={user_id}, item={item_name}, reason=玩家数据不存在"
            )
            yield event.plain_result("❌ 装备失败：玩家数据不存在，物品返还失败，请联系管理员")
            return

        # 装备物品
        success, message = await self.equipment_manager.equip_item(player, item)

        if success:
            # 显示属性加成
            attr_display = item.get_attribute_display()
            result_msg = (
                f"✅ {message}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"属性加成：{attr_display}"
            )
            yield event.plain_result(result_msg)
        else:
            # 装备失败，将物品放回储物戒
            await self.storage_ring_manager.store_item(player, item_name, 1, silent=True)
            yield event.plain_result(f"❌ {message}")

    @staticmethod
    def _normalize_command_argument(event: AstrMessageEvent, argument, commands) -> str:
        """清理命令参数中可能残留的唤醒词/指令名（支持多个候选指令名）。"""
        names = [commands] if isinstance(commands, (str, bytes)) else list(commands)
        value = (argument or "").strip()
        for name in sorted([n for n in names if n], key=len, reverse=True):
            value = re.sub(
                rf"^[^\w\s]*\s*{re.escape(name)}\s*", "", value, count=1, flags=re.IGNORECASE
            ).strip()
        if value:
            return value

        # 形参未拿到内容时，回退到从原始消息中截取指令名之后的完整文本
        return extract_command_args(event, names).strip()

    @player_required
    async def handle_unequip_item(self, player: Player, event: AstrMessageEvent, slot_or_name: str):
        """卸下装备"""
        slot_or_name = self._normalize_command_argument(
            event, slot_or_name, (CMD_UNEQUIP_ITEM,) + CMD_UNEQUIP_ITEM_ALIASES
        )
        if not slot_or_name or slot_or_name.strip() == "":
            yield event.plain_result(
                f"请指定要卸下的装备\n"
                f"用法：{CMD_UNEQUIP_ITEM} 武器/防具/心法/功法名称\n"
                f"（也可使用：{CMD_UNEQUIP_ITEM_ALIASES[0]} 武器/防具/心法/功法名称）"
            )
            return

        slot_or_name = slot_or_name.strip()

        # 获取卸下前的装备名称，用于存入储物戒
        unequipped_item_name = None
        if slot_or_name in ["武器", "weapon"]:
            unequipped_item_name = player.weapon
        elif slot_or_name in ["防具", "armor"]:
            unequipped_item_name = player.armor
        elif slot_or_name in ["主修心法", "心法", "main_technique"]:
            unequipped_item_name = player.main_technique
        else:
            # 检查功法列表
            techniques_list = player.get_techniques_list()
            if slot_or_name in techniques_list:
                unequipped_item_name = slot_or_name

        # 卸下装备
        success, message = await self.equipment_manager.unequip_item(player, slot_or_name)

        if success:
            # 卸下成功后，将装备存入储物戒
            storage_msg = ""
            if unequipped_item_name:
                store_success, store_msg = await self.storage_ring_manager.store_item(
                    player, unequipped_item_name, 1, silent=True
                )
                if store_success:
                    storage_msg = f"\n已存入储物戒"
                else:
                    storage_msg = f"\n⚠️ 存入储物戒失败：{store_msg}"
            
            yield event.plain_result(f"✅ {message}{storage_msg}")
        else:
            yield event.plain_result(f"❌ {message}")
