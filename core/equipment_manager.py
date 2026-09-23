# core/equipment_manager.py

from typing import Optional, List, Dict, TYPE_CHECKING
from ..models import Player, Item
from ..data import DataBase

if TYPE_CHECKING:
    from ..config_manager import ConfigManager
    from .storage_ring_manager import StorageRingManager

# 单槽位装备定义：item_type -> (Player 字段名, 中文名)
EQUIPMENT_SLOTS: Dict[str, tuple] = {
    "weapon": ("weapon", "武器"),
    "armor": ("armor", "防具"),
    "accessory": ("accessory", "饰品"),
    "main_technique": ("main_technique", "主修心法"),
}

# 「卸下」参数别名 -> 槽位标识（新增装备栏位时只需在这里补一行）
SLOT_ALIASES: Dict[str, str] = {
    "武器": "weapon", "兵器": "weapon", "weapon": "weapon",
    "防具": "armor", "护甲": "armor", "armor": "armor",
    "饰品": "accessory", "首饰": "accessory", "accessory": "accessory",
    "主修心法": "main_technique", "心法": "main_technique", "main_technique": "main_technique",
    "功法": "technique", "technique": "technique",
}


class EquipmentManager:
    """装备管理器 - 处理装备的穿戴、卸下和属性计算"""

    def __init__(self, db: DataBase, config_manager: "ConfigManager" = None, storage_ring_manager: "StorageRingManager" = None):
        self.db = db
        self.config_manager = config_manager
        self.storage_ring_manager = storage_ring_manager

    def parse_item_from_name(self, item_name: str, items_data: dict, weapons_data: dict = None) -> Optional[Item]:
        """从物品名称解析为Item对象

        Args:
            item_name: 物品名称
            items_data: 物品配置数据字典
            weapons_data: 武器配置数据字典（可选）

        Returns:
            Item对象，如果未找到则返回None
        """
        if not item_name or item_name == "":
            return None

        # 先从物品配置中查找
        item_config = items_data.get(item_name)

        # 如果没找到且提供了武器配置，从武器配置中查找
        if not item_config and weapons_data:
            item_config = weapons_data.get(item_name)

        # 秘境等系统掉落但未登记进配置的装备：按掉落表动态生成配置
        if not item_config:
            item_config = self._resolve_drop_equipment_config(item_name)

        # 秘境等系统掉落的功法同样未登记进配置：按功法掉落表动态生成配置
        if not item_config:
            item_config = self._resolve_drop_skill_config(item_name)

        if not item_config:
            return None

        # 处理新旧格式兼容性
        item_type = item_config.get("type", "")
        physical_damage = item_config.get("physical_damage", 0)
        physical_defense = item_config.get("physical_defense", 0)
        magic_damage = item_config.get("magic_damage", 0)
        magic_defense = item_config.get("magic_defense", 0)
        mental_power = item_config.get("mental_power", 0)

        # 旧格式兼容：处理 items.json 中的法器（equip_effects 格式）
        if "equip_effects" in item_config:
            equip_effects = item_config.get("equip_effects") or {}
            # 优先使用 equip_effects 中的属性
            if "magic_damage" in equip_effects:
                magic_damage = equip_effects["magic_damage"]
            if "physical_damage" in equip_effects:
                physical_damage = equip_effects["physical_damage"]
            if "magic_defense" in equip_effects:
                magic_defense = equip_effects["magic_defense"]
            if "physical_defense" in equip_effects:
                physical_defense = equip_effects["physical_defense"]
            if "mental_power" in equip_effects:
                mental_power = equip_effects["mental_power"]
            # 旧格式 attack -> physical_damage（如果没有 physical_damage）
            if "attack" in equip_effects and physical_damage == 0:
                physical_damage = equip_effects["attack"]
            # 旧格式 defense -> physical_defense（如果没有 physical_defense）
            if "defense" in equip_effects and physical_defense == 0:
                physical_defense = equip_effects["defense"]

        # 旧格式兼容：处理类型映射
        # "法器" + subtype="武器" -> "weapon"
        # "法器" + subtype="防具" -> "armor"
        # "法器" + subtype="饰品" -> "accessory"
        if item_type == "法器":
            subtype = item_config.get("subtype", "")
            if subtype == "武器":
                item_type = "weapon"
            elif subtype == "防具":
                item_type = "armor"
            elif subtype == "饰品":
                item_type = "accessory"
        elif item_type == "功法":
            # 旧格式功法 -> technique
            item_type = "technique"

        # 中文细分类型兜底（如“神通功法”“顶级心法”）
        if item_type not in {"weapon", "armor", "accessory", "main_technique", "technique"}:
            if "心法" in item_type:
                item_type = "main_technique"
            elif "功法" in item_type or "诀" in item_type or "术" in item_type:
                item_type = "technique"

        return Item(
            item_id=item_config.get("id", item_name),
            name=item_name,
            item_type=item_type,
            description=item_config.get("description", ""),
            rank=item_config.get("rank", ""),
            required_level_index=item_config.get("required_level_index", 0),
            weapon_category=item_config.get("weapon_category", ""),
            magic_damage=magic_damage,
            physical_damage=physical_damage,
            magic_defense=magic_defense,
            physical_defense=physical_defense,
            mental_power=mental_power,
            exp_multiplier=item_config.get("exp_multiplier", 0.0),
            spiritual_qi=item_config.get("spiritual_qi", 0),
            blood_qi=item_config.get("blood_qi", 0),
            lifespan=item_config.get("lifespan", 0)
        )

    @staticmethod
    def _resolve_drop_equipment_config(item_name: str) -> Optional[dict]:
        """解析秘境 / 世界事件等系统掉落的装备（未登记进 items.json / weapons.json）

        避免「掉落装备只能炼化、无法穿戴」的问题。
        """
        try:
            from ..managers.rift_manager import RiftManager
        except Exception:
            RiftManager = None
        if RiftManager is not None:
            try:
                config = RiftManager.get_equipment_config(item_name)
            except Exception:
                config = None
            if config:
                return config

        try:
            from ..managers.world_event_manager import WorldEventManager
        except Exception:
            return None
        try:
            return WorldEventManager.get_equipment_config(item_name)
        except Exception:
            return None

    @staticmethod
    def _resolve_drop_skill_config(item_name: str) -> Optional[dict]:
        """解析秘境等系统掉落的功法（未登记进 items.json / weapons.json）

        避免「秘境掉落的功法只能炼化、无法装备」的问题。
        """
        try:
            from ..managers.rift_manager import RiftManager
        except Exception:
            return None
        try:
            return RiftManager.get_skill_config(item_name)
        except Exception:
            return None

    def get_equipped_items(self, player: Player, items_data: dict, weapons_data: dict = None) -> List[Item]:
        """获取玩家所有已装备的物品

        Args:
            player: 玩家对象
            items_data: 物品配置数据字典
            weapons_data: 武器配置数据字典（可选）

        Returns:
            已装备物品列表
        """
        equipped = []

        # 武器
        if player.weapon:
            item = self.parse_item_from_name(player.weapon, items_data, weapons_data)
            if item:
                equipped.append(item)

        # 防具
        if player.armor:
            item = self.parse_item_from_name(player.armor, items_data, weapons_data)
            if item:
                equipped.append(item)

        # 饰品
        if player.accessory:
            item = self.parse_item_from_name(player.accessory, items_data, weapons_data)
            if item:
                equipped.append(item)

        # 主修心法
        if player.main_technique:
            item = self.parse_item_from_name(player.main_technique, items_data, weapons_data)
            if item:
                equipped.append(item)

        # 功法列表
        techniques_list = player.get_techniques_list()
        for technique_name in techniques_list:
            item = self.parse_item_from_name(technique_name, items_data, weapons_data)
            if item:
                equipped.append(item)

        return equipped

    def check_equipment_level_requirement(self, player: Player, item: Item) -> tuple[bool, str]:
        """检查玩家是否满足装备的境界要求

        Args:
            player: 玩家对象
            item: 装备物品

        Returns:
            (是否满足, 提示消息)
        """
        if player.level_index < item.required_level_index:
            # 获取需求境界名称
            required_level_name = self._format_required_level(item.required_level_index)
            return False, f"境界不足！装备【{item.name}】（{item.rank}）需要达到【{required_level_name}】以上"
        return True, ""

    def _format_required_level(self, level_index: int) -> str:
        """格式化需求境界名称（同时显示灵修/体修）"""
        if not self.config_manager:
            return f"境界{level_index}"

        names = []
        # 灵修境界名称
        if 0 <= level_index < len(self.config_manager.level_data):
            name = self.config_manager.level_data[level_index].get("level_name", "")
            if name:
                names.append(name)
        # 体修境界名称
        if 0 <= level_index < len(self.config_manager.body_level_data):
            name = self.config_manager.body_level_data[level_index].get("level_name", "")
            if name and name not in names:
                names.append(name)

        if not names:
            return f"境界{level_index}"
        return " / ".join(names)

    async def equip_item(self, player: Player, item: Item) -> tuple[bool, str]:
        """装备物品

        Args:
            player: 玩家对象
            item: 要装备的物品

        Returns:
            (是否成功, 消息)
        """
        # 检查境界要求
        can_equip, error_msg = self.check_equipment_level_requirement(player, item)
        if not can_equip:
            return False, error_msg

        # 功法单独处理（多槽位列表）
        if item.item_type == "technique":
            techniques_list = player.get_techniques_list()

            # 检查是否已装备
            if item.name in techniques_list:
                return False, f"功法【{item.name}】已装备"

            # 检查功法栏是否已满（最多3个）
            if len(techniques_list) >= 3:
                return False, f"功法栏已满（最多3个），请先卸下其他功法"

            # 添加功法
            techniques_list.append(item.name)
            player.set_techniques_list(techniques_list)
            await self.db.update_player(player)
            return True, f"已装备功法【{item.name}】（{item.rank}）（{len(techniques_list)}/3）"

        # 单槽位装备（武器/防具/饰品/主修心法）
        slot_key = EQUIPMENT_SLOTS.get(item.item_type)
        if not slot_key:
            return False, f"未知的装备类型：{item.item_type}"

        field, label = slot_key
        old_item = getattr(player, field) or ""

        if old_item == item.name:
            return False, f"{label}【{item.name}】已装备，无需重复装备"

        if old_item:
            # 关键顺序：先把旧装备收回储物戒，收纳成功才允许替换
            # （否则储物戒满格时旧装备会被直接覆盖，等于凭空消失）
            stored, store_msg = await self._store_to_ring(player, old_item)
            if not stored:
                return False, (
                    f"无法替换{label}：旧装备【{old_item}】{store_msg}\n"
                    f"请先清理储物戒空间后再试（本次不会消耗【{item.name}】）"
                )
            # 收纳动作已写库，重新读取玩家数据，避免后续写入把刚存入的旧装备覆盖掉
            refreshed = await self.db.get_player_by_id(player.user_id)
            if refreshed:
                player = refreshed

        setattr(player, field, item.name)
        await self.db.update_player(player)

        if old_item:
            return True, (
                f"已将【{old_item}】替换为【{item.name}】（{item.rank}）\n"
                f"旧装备【{old_item}】已存入储物戒"
            )
        return True, f"已装备{label}【{item.name}】（{item.rank}）"

    def resolve_equipped(self, player: Player, slot_or_name: str) -> tuple[Optional[str], str]:
        """把「卸下」的参数解析成 (槽位标识, 已装备物品名)

        槽位标识：weapon / armor / accessory / main_technique / technique；
        解析失败返回 (None, "")，槽位为空返回 (槽位标识, "")。

        支持两种写法：
          · 槽位名：武器 / 防具 / 饰品 / 心法 / 功法
          · 装备名：直接写已装备的物品名（戮仙剑阵、星辰坠 …）
        """
        value = (slot_or_name or "").strip()
        if not value:
            return None, ""

        slot = SLOT_ALIASES.get(value)
        if slot == "technique":
            if value in ("功法", "technique"):
                return "technique", ""
            if value in player.get_techniques_list():
                return "technique", value
            return None, ""
        if slot:
            field, _ = EQUIPMENT_SLOTS[slot]
            return slot, (getattr(player, field) or "")

        # 按已装备物品名匹配
        for key, (field, _) in EQUIPMENT_SLOTS.items():
            if (getattr(player, field) or "") == value:
                return key, value
        if value in player.get_techniques_list():
            return "technique", value

        return None, ""

    async def unequip_item(self, player: Player, slot_or_name: str) -> tuple[bool, str, str]:
        """卸下装备

        Args:
            player: 玩家对象
            slot_or_name: 槽位名称（武器/防具/饰品/心法/功法）或已装备的物品名

        Returns:
            (是否成功, 消息, 卸下的物品名)
        """
        slot, item_name = self.resolve_equipped(player, slot_or_name)

        if slot is None:
            return False, (
                f"未找到装备：{slot_or_name}\n"
                f"可用槽位：武器 / 防具 / 饰品 / 心法 / 功法，也可直接输入已装备的物品名"
            ), ""

        label = "功法" if slot == "technique" else EQUIPMENT_SLOTS[slot][1]

        if not item_name:
            if slot == "technique":
                techniques = player.get_techniques_list()
                if techniques:
                    return False, f"请指定要卸下的功法名（当前：{' / '.join(techniques)}）", ""
            return False, f"未装备{label}", ""

        # 关键顺序：先存回储物戒，收纳成功后再清空装备栏，避免卸下即销毁
        stored, store_msg = await self._store_to_ring(player, item_name)
        if not stored:
            return False, (
                f"无法卸下【{item_name}】：{store_msg}\n"
                f"请先清理储物戒空间后再试（本次不会卸下装备）"
            ), ""
        # 收纳动作已写库，重新读取玩家数据，避免后续写入把刚存入的装备覆盖掉
        refreshed = await self.db.get_player_by_id(player.user_id)
        if refreshed:
            player = refreshed

        if slot == "technique":
            techniques_list = player.get_techniques_list()
            if item_name in techniques_list:
                techniques_list.remove(item_name)
                player.set_techniques_list(techniques_list)
        else:
            setattr(player, EQUIPMENT_SLOTS[slot][0], "")

        await self.db.update_player(player)
        return True, f"已卸下{label}【{item_name}】，并放回储物戒", item_name

    async def _store_to_ring(self, player: Player, item_name: str) -> tuple[bool, str]:
        """把一件装备放回储物戒（失败原因原样返回，供上层提示）"""
        if not self.storage_ring_manager:
            return False, "储物戒系统未初始化"
        return await self.storage_ring_manager.store_item(player, item_name, 1, silent=True)
