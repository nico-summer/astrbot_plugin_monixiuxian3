# managers/rift_manager.py
"""
秘境系统管理器 - 处理秘境探索、奖励等逻辑
"""

import random
import time
from typing import Tuple, List, Optional, Dict, TYPE_CHECKING
from ..data.data_manager import DataBase
from ..models_extended import Rift, UserStatus
from ..models import Player

if TYPE_CHECKING:
    from ..core import StorageRingManager


class RiftManager:
    """秘境系统管理器"""
    
    # 默认秘境探索时长（秒）
    DEFAULT_DURATION = 1800

    # ========== 秘境掉落系统重构：装备+功法+稀有材料 ==========

    # 秘境材料掉落表（保底掉落，100%触发）
    RIFT_MATERIAL_TABLE = {
        1: [  # 低级秘境 - 基础材料
            {"name": "玄铁", "weight": 40, "min": 1, "max": 3},
            {"name": "灵兽骨", "weight": 30, "min": 1, "max": 2},
            {"name": "精铁", "weight": 20, "min": 2, "max": 4},
            {"name": "功法残页", "weight": 10, "min": 1, "max": 1},
        ],
        2: [  # 中级秘境 - 进阶材料
            {"name": "星辰石", "weight": 35, "min": 2, "max": 5},
            {"name": "灵兽内丹", "weight": 30, "min": 1, "max": 2},
            {"name": "玄冰晶", "weight": 20, "min": 1, "max": 3},
            {"name": "天材地宝", "weight": 15, "min": 1, "max": 1},
        ],
        3: [  # 高级秘境 - 稀有材料
            {"name": "紫金神铁", "weight": 25, "min": 1, "max": 3},
            {"name": "龙鳞", "weight": 20, "min": 1, "max": 1},
            {"name": "仙灵草", "weight": 30, "min": 2, "max": 4},
            {"name": "传承玉简", "weight": 15, "min": 1, "max": 1},
            {"name": "混沌石", "weight": 10, "min": 1, "max": 2},
        ],
    }

    # 秘境装备掉落表（爆率掉落）
    RIFT_EQUIPMENT_TABLE = {
        1: {  # 低级秘境
            "drop_rate": 25,  # 25%爆率
            "items": [
                {"name": "精铁剑", "type": "武器", "weight": 30},
                {"name": "青铜剑", "type": "武器", "weight": 25},
                {"name": "布衣", "type": "防具", "weight": 25},
                {"name": "护心镜", "type": "饰品", "weight": 15},
                {"name": "灵石手镯", "type": "饰品", "weight": 5},
            ]
        },
        2: {  # 中级秘境
            "drop_rate": 30,  # 30%爆率
            "items": [
                {"name": "玄铁重剑", "type": "武器", "weight": 25},
                {"name": "寒冰刃", "type": "武器", "weight": 20},
                {"name": "炼心甲", "type": "防具", "weight": 25},
                {"name": "云纹袍", "type": "防具", "weight": 15},
                {"name": "灵玉佩", "type": "饰品", "weight": 15},
            ]
        },
        3: {  # 高级秘境
            "drop_rate": 40,  # 40%爆率
            "items": [
                {"name": "寒霜仙剑", "type": "武器", "quality": "极品", "weight": 15},
                {"name": "烈焰刀", "type": "武器", "quality": "极品", "weight": 12},
                {"name": "天蚕宝甲", "type": "防具", "quality": "极品", "weight": 15},
                {"name": "龙鳞甲", "type": "防具", "quality": "极品", "weight": 10},
                {"name": "混元戒", "type": "饰品", "quality": "极品", "weight": 8},
            ]
        },
    }

    # 秘境功法掉落表（低爆率）
    RIFT_SKILL_TABLE = {
        1: {  # 低级秘境
            "drop_rate": 10,  # 10%爆率
            "items": [
                {"name": "基础剑诀", "type": "攻击功法", "weight": 40},
                {"name": "凝神诀", "type": "辅助功法", "weight": 35},
                {"name": "护体诀", "type": "防御功法", "weight": 25},
            ]
        },
        2: {  # 中级秘境
            "drop_rate": 15,  # 15%爆率
            "items": [
                {"name": "烈焰掌", "type": "攻击功法", "weight": 30},
                {"name": "金刚诀", "type": "防御功法", "weight": 25},
                {"name": "疾风步", "type": "身法功法", "weight": 25},
                {"name": "御剑术", "type": "特殊功法", "weight": 20},
            ]
        },
        3: {  # 高级秘境
            "drop_rate": 20,  # 20%爆率
            "items": [
                {"name": "九天玄雷诀", "type": "顶级攻击功法", "weight": 20},
                {"name": "太上忘情诀", "type": "顶级心法", "weight": 15},
                {"name": "逍遥游", "type": "顶级身法", "weight": 25},
                {"name": "万剑归宗", "type": "终极剑法", "weight": 10},
            ]
        },
    }

    # 双倍掉落触发概率
    DOUBLE_DROP_CHANCE = 5  # 5%概率触发双倍掉落
    
    # 秘境稀有丹药掉落表（按秘境等级分组，低概率掉落通用增益丹）
    RIFT_PILL_DROP_TABLE = {
        1: [  # 低级秘境 - 3%概率掉落
            {"name": "三品凝神增益丹", "weight": 100, "min": 1, "max": 1},
        ],
        2: [  # 中级秘境 - 5%概率掉落
            {"name": "三品凝神增益丹", "weight": 50, "min": 1, "max": 1},
            {"name": "四品破境增益丹", "weight": 40, "min": 1, "max": 1},
            {"name": "五品渡劫增益丹", "weight": 10, "min": 1, "max": 1},
        ],
        3: [  # 高级秘境 - 10%概率掉落
            {"name": "四品破境增益丹", "weight": 40, "min": 1, "max": 1},
            {"name": "五品渡劫增益丹", "weight": 30, "min": 1, "max": 1},
            {"name": "六品破境增益丹", "weight": 20, "min": 1, "max": 1},
            {"name": "七品化神增益丹", "weight": 10, "min": 1, "max": 1},
        ],
    }
    
    # 秘境丹药掉落概率（百分比）
    RIFT_PILL_DROP_CHANCE = {
        1: 3,   # 低级秘境 3%
        2: 5,   # 中级秘境 5%
        3: 10,  # 高级秘境 10%
    }
    
    def __init__(self, db: DataBase, config_manager=None, storage_ring_manager: "StorageRingManager" = None):
        self.db = db
        self.config_manager = config_manager
        self.storage_ring_manager = storage_ring_manager
        self.config = config_manager.rift_config if config_manager else {}
        self.explore_duration = self.config.get("default_duration", self.DEFAULT_DURATION)
    
    def _get_level_name(self, level_index: int) -> str:
        """获取境界名称"""
        if self.config_manager and hasattr(self.config_manager, 'level_data'):
            if 0 <= level_index < len(self.config_manager.level_data):
                return self.config_manager.level_data[level_index].get("level_name", f"境界{level_index}")
        # 默认境界名称
        level_names = ["炼气期一层", "炼气期二层", "炼气期三层", "炼气期四层", "炼气期五层",
                       "炼气期六层", "炼气期七层", "炼气期八层", "炼气期九层", "炼气期十层",
                       "筑基期初期", "筑基期中期", "筑基期后期", "金丹期初期", "金丹期中期", "金丹期后期"]
        if 0 <= level_index < len(level_names):
            return level_names[level_index]
        return f"境界{level_index}"
    
    async def list_rifts(self) -> Tuple[bool, str]:
        """
        列出所有秘境
        
        Returns:
            (成功标志, 消息)
        """
        rifts = await self.db.ext.get_all_rifts()
        
        if not rifts:
            return False, "❌ 当前没有开放的秘境！"
        
        msg = "🌀 秘境列表\n"
        msg += "━━━━━━━━━━━━━━━\n"
        
        for rift in rifts:
            rewards_dict = rift.get_rewards()
            exp_range = rewards_dict.get("exp", [0, 0])
            gold_range = rewards_dict.get("gold", [0, 0])
            level_name = self._get_level_name(rift.required_level)
            
            msg += f"【{rift.rift_name}】(ID:{rift.rift_id})\n"
            if rift.required_level == 0:
                msg += f"  等级要求：无限制\n"
            else:
                msg += f"  等级要求：{level_name} 及以上\n"
            msg += f"  修为奖励：{exp_range[0]:,}-{exp_range[1]:,}\n"
            msg += f"  灵石奖励：{gold_range[0]:,}-{gold_range[1]:,}\n\n"
        
        msg += "💡 使用 /探索秘境 <ID> 进入（如：/探索秘境 1）"
        
        return True, msg
    
    async def enter_rift(
        self,
        user_id: str,
        rift_id: int
    ) -> Tuple[bool, str]:
        """
        进入秘境
        
        Args:
            user_id: 用户ID
            rift_id: 秘境ID
            
        Returns:
            (成功标志, 消息)
        """
        # 1. 检查用户
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！"
        
        # 2. 检查用户状态
        user_cd = await self.db.ext.get_user_cd(user_id)
        if not user_cd:
            await self.db.ext.create_user_cd(user_id)
            user_cd = await self.db.ext.get_user_cd(user_id)
        
        if user_cd.type != UserStatus.IDLE:
            return False, f"❌ 你当前正{UserStatus.get_name(user_cd.type)}，无法探索秘境！"
        
        # 3. 检查秘境
        rift = await self.db.ext.get_rift_by_id(rift_id)
        if not rift:
            return False, "❌ 秘境不存在！使用 /秘境列表 查看可用秘境"
        
        # 4. 检查境界要求
        if player.level_index < rift.required_level:
            level_name = self._get_level_name(rift.required_level)
            return False, f"❌ 探索【{rift.rift_name}】需要达到【{level_name}】！"
        
        # 5. 设置探索状态，存储秘境ID
        scheduled_time = int(time.time()) + self.explore_duration
        extra_data = {"rift_id": rift_id, "rift_level": rift.rift_level}
        await self.db.ext.set_user_busy(user_id, UserStatus.EXPLORING, scheduled_time, extra_data)
        
        return True, f"✨ 你进入了『{rift.rift_name}』！探索需要 {self.explore_duration//60} 分钟。\n使用 /完成探索 领取奖励"
    
    async def finish_exploration(
        self,
        user_id: str
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        完成秘境探索
        
        Args:
            user_id: 用户ID
            
        Returns:
            (成功标志, 消息, 奖励数据)
        """
        # 1. 检查用户
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！", None
        
        # 2. 检查CD状态
        user_cd = await self.db.ext.get_user_cd(user_id)
        if not user_cd or user_cd.type != UserStatus.EXPLORING:
            return False, "❌ 你当前不在探索秘境！", None
        
        # 3. 检查时间
        current_time = int(time.time())
        if current_time < user_cd.scheduled_time:
            remaining = user_cd.scheduled_time - current_time
            minutes = remaining // 60
            return False, f"❌ 探索尚未完成！还需要 {minutes} 分钟。", None
        
        # 4. 获取秘境信息（从extra_data中读取）
        extra_data = user_cd.get_extra_data() if hasattr(user_cd, 'get_extra_data') else {}
        rift_id = extra_data.get("rift_id", 0)
        rift_level = extra_data.get("rift_level", 1)
        
        # 获取秘境配置
        rift = await self.db.ext.get_rift_by_id(rift_id) if rift_id else None
        rift_name = rift.rift_name if rift else "未知秘境"
        
        # 5. 根据秘境配置计算奖励
        if rift:
            rewards_config = rift.get_rewards()
            exp_range = rewards_config.get("exp", [1000, 5000])
            gold_range = rewards_config.get("gold", [500, 2000])
            exp_reward = random.randint(exp_range[0], exp_range[1])
            gold_reward = random.randint(gold_range[0], gold_range[1])
            rift_level = rift.rift_level
        else:
            # 兼容旧数据，使用默认奖励
            exp_reward = random.randint(1000, 5000)
            gold_reward = random.randint(500, 2000)
        
        # 随机事件
        events = [
            {"desc": "你发现了一处灵泉，修为大增！", "item_chance": 70},
            {"desc": "你在秘境中击败了一只妖兽！", "item_chance": 80},
            {"desc": "你找到了一个隐藏的宝箱！", "item_chance": 100},
            {"desc": "你领悟了一些修炼心得。", "item_chance": 40},
            {"desc": "你在秘境中遇到了前辈留下的传承！", "item_chance": 90}
        ]
        event = random.choice(events)
        
        # 6. 物品掉落（根据秘境等级）
        dropped_items = []
        item_msg = ""
        dropped_items = await self._roll_rift_drops(player, rift_level, event["item_chance"])
        if dropped_items:
            # 分类显示：装备、功法、材料、丹药
            equipment_lines = []
            skill_lines = []
            material_lines = []
            pill_lines = []

            for item_name, count in dropped_items:
                # 判断物品类型
                is_pill = self._is_pill_item(item_name)
                is_equipment = self._is_equipment_item(item_name)
                is_skill = self._is_skill_item(item_name)

                if is_pill:
                    # 存入丹药背包
                    inventory = player.get_pills_inventory()
                    inventory[item_name] = inventory.get(item_name, 0) + count
                    player.set_pills_inventory(inventory)
                    pill_lines.append(f"  🔥 {item_name} x{count}")
                elif is_equipment:
                    # 装备存入储物戒
                    if self.storage_ring_manager:
                        success, _ = await self.storage_ring_manager.store_item(player, item_name, count, silent=True)
                        if success:
                            equipment_lines.append(f"  ⚔️ {item_name} x{count}")
                        else:
                            equipment_lines.append(f"  ⚔️ {item_name} x{count}（储物戒已满，丢失）")
                elif is_skill:
                    # 功法存入储物戒
                    if self.storage_ring_manager:
                        success, _ = await self.storage_ring_manager.store_item(player, item_name, count, silent=True)
                        if success:
                            skill_lines.append(f"  📜 {item_name} x{count}")
                        else:
                            skill_lines.append(f"  📜 {item_name} x{count}（储物戒已满，丢失）")
                else:
                    # 材料存入储物戒
                    if self.storage_ring_manager:
                        success, _ = await self.storage_ring_manager.store_item(player, item_name, count, silent=True)
                        if success:
                            material_lines.append(f"  📦 {item_name} x{count}")
                        else:
                            material_lines.append(f"  📦 {item_name} x{count}（储物戒已满，丢失）")

            # 组装掉落消息
            all_lines = []
            if equipment_lines:
                all_lines.extend(equipment_lines)
            if skill_lines:
                all_lines.extend(skill_lines)
            if material_lines:
                all_lines.extend(material_lines)
            if pill_lines:
                all_lines.extend(pill_lines)

            if all_lines:
                item_msg = "\n\n✨ 获得物品：\n" + "\n".join(all_lines)
        
        # 7. 应用奖励
        player.experience += exp_reward
        player.gold += gold_reward
        await self.db.update_player(player)
        
        # 8. 清除CD
        await self.db.ext.set_user_free(user_id)
        
        msg = f"""
🌀 探索完成 - {rift_name}
━━━━━━━━━━━━━━━

{event["desc"]}

获得修为：+{exp_reward:,}
获得灵石：+{gold_reward:,}{item_msg}
        """.strip()
        
        reward_data = {
            "exp": exp_reward,
            "gold": gold_reward,
            "event": event["desc"],
            "items": dropped_items,
            "rift_name": rift_name
        }
        
        return True, msg, reward_data
    
    async def exit_rift(self, user_id: str) -> Tuple[bool, str]:
        """
        退出秘境（放弃探索）
        
        Args:
            user_id: 用户ID
            
        Returns:
            (成功标志, 消息)
        """
        # 1. 检查用户
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！"
        
        # 2. 检查CD状态
        user_cd = await self.db.ext.get_user_cd(user_id)
        if not user_cd or user_cd.type != UserStatus.EXPLORING:
            return False, "❌ 你当前不在探索秘境！"
        
        # 3. 清除CD状态
        await self.db.ext.set_user_free(user_id)
        
        return True, "✅ 你已退出秘境，本次探索未获得任何奖励。"
    
    def _is_pill_item(self, item_name: str) -> bool:
        """检查物品是否为丹药"""
        if self.config_manager and hasattr(self.config_manager, 'is_pill'):
            return self.config_manager.is_pill(item_name)
        return False

    def _is_equipment_item(self, item_name: str) -> bool:
        """检查物品是否为装备（武器/防具/饰品）"""
        # 遍历所有等级的装备表
        for level_config in self.RIFT_EQUIPMENT_TABLE.values():
            for item in level_config["items"]:
                if item["name"] == item_name:
                    return True
        return False

    def _is_skill_item(self, item_name: str) -> bool:
        """检查物品是否为功法"""
        # 遍历所有等级的功法表
        for level_config in self.RIFT_SKILL_TABLE.values():
            for item in level_config["items"]:
                if item["name"] == item_name:
                    return True
        return False
    
    def _get_rift_level_by_player(self, player: Player) -> int:
        """根据玩家境界确定秘境等级"""
        level_index = player.level_index
        if level_index <= 5:
            return 1  # 低级秘境
        elif level_index <= 12:
            return 2  # 中级秘境
        else:
            return 3  # 高级秘境
    
    async def _roll_rift_drops(self, player: Player, rift_level: int, item_chance: int) -> List[Tuple[str, int]]:
        """
        根据秘境等级随机掉落物品（新系统：材料+装备+功法）

        Args:
            player: 玩家对象
            rift_level: 秘境等级 (1-3)
            item_chance: 基础掉落概率（事件加成）

        Returns:
            掉落物品列表 [(物品名, 数量), ...]
        """
        dropped_items = []

        # 检查双倍掉落
        is_double_drop = random.randint(1, 100) <= self.DOUBLE_DROP_CHANCE
        double_msg = "【双倍掉落】" if is_double_drop else ""

        # ===== 1. 材料掉落（保底，100%触发）=====
        material_table = self.RIFT_MATERIAL_TABLE.get(rift_level, self.RIFT_MATERIAL_TABLE[1])

        # 加权随机选择1-2个材料
        num_materials = 2 if random.randint(1, 100) <= 50 else 1
        for _ in range(num_materials):
            total_weight = sum(item["weight"] for item in material_table)
            roll = random.randint(1, total_weight)

            current_weight = 0
            for item in material_table:
                current_weight += item["weight"]
                if roll <= current_weight:
                    count = random.randint(item["min"], item["max"])
                    if is_double_drop:
                        count *= 2
                    dropped_items.append((item["name"], count))
                    break

        # ===== 2. 装备掉落（爆率触发）=====
        equipment_config = self.RIFT_EQUIPMENT_TABLE.get(rift_level, self.RIFT_EQUIPMENT_TABLE[1])
        equipment_drop_rate = equipment_config["drop_rate"]

        if random.randint(1, 100) <= equipment_drop_rate:
            equipment_items = equipment_config["items"]
            total_weight = sum(item["weight"] for item in equipment_items)
            roll = random.randint(1, total_weight)

            current_weight = 0
            for item in equipment_items:
                current_weight += item["weight"]
                if roll <= current_weight:
                    # 装备数量固定为1
                    dropped_items.append((item["name"], 1))
                    if is_double_drop:
                        # 双倍掉落时再掉一件
                        dropped_items.append((item["name"], 1))
                    break

        # ===== 3. 功法掉落（低爆率）=====
        skill_config = self.RIFT_SKILL_TABLE.get(rift_level, self.RIFT_SKILL_TABLE[1])
        skill_drop_rate = skill_config["drop_rate"]

        if random.randint(1, 100) <= skill_drop_rate:
            skill_items = skill_config["items"]
            total_weight = sum(item["weight"] for item in skill_items)
            roll = random.randint(1, total_weight)

            current_weight = 0
            for item in skill_items:
                current_weight += item["weight"]
                if roll <= current_weight:
                    # 功法数量固定为1
                    dropped_items.append((item["name"], 1))
                    break

        # ===== 4. 稀有丹药掉落（额外奖励，保留原逻辑）=====
        pill_drops = self._roll_pill_drops(rift_level)
        if pill_drops:
            dropped_items.extend(pill_drops)

        return dropped_items
    
    def _roll_pill_drops(self, rift_level: int) -> List[Tuple[str, int]]:
        """
        根据秘境等级随机掉落稀有丹药
        
        Args:
            rift_level: 秘境等级 (1-3)
            
        Returns:
            掉落丹药列表 [(丹药名, 数量), ...]
        """
        dropped_pills = []
        
        # 获取丹药掉落概率
        pill_chance = self.RIFT_PILL_DROP_CHANCE.get(rift_level, 3)
        
        # 检查是否触发丹药掉落
        if random.randint(1, 100) > pill_chance:
            return dropped_pills
        
        # 获取对应等级的丹药掉落表
        pill_table = self.RIFT_PILL_DROP_TABLE.get(rift_level, self.RIFT_PILL_DROP_TABLE[1])
        
        # 加权随机选择丹药
        total_weight = sum(item["weight"] for item in pill_table)
        roll = random.randint(1, total_weight)
        
        current_weight = 0
        for item in pill_table:
            current_weight += item["weight"]
            if roll <= current_weight:
                count = random.randint(item["min"], item["max"])
                dropped_pills.append((item["name"], count))
                break
        
        return dropped_pills
