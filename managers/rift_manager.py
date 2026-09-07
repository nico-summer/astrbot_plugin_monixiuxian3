# managers/rift_manager.py
"""
秘境系统管理器 - 处理秘境探索、奖励等逻辑
"""

import random
import time
from datetime import datetime
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
        4: [  # 元婴期秘境 - 血魔材料
            {"name": "血魂石", "weight": 30, "min": 2, "max": 5},
            {"name": "星核碎片", "weight": 25, "min": 1, "max": 3},
            {"name": "魔晶", "weight": 20, "min": 1, "max": 2},
            {"name": "元婴精华", "weight": 15, "min": 1, "max": 1},
            {"name": "不朽骨", "weight": 10, "min": 1, "max": 1},
        ],
        5: [  # 化神-炼虚期秘境 - 虚空材料
            {"name": "虚空结晶", "weight": 25, "min": 2, "max": 4},
            {"name": "仙器碎片", "weight": 25, "min": 1, "max": 2},
            {"name": "空间石", "weight": 20, "min": 1, "max": 3},
            {"name": "法则碎片", "weight": 20, "min": 1, "max": 2},
            {"name": "时光沙", "weight": 10, "min": 1, "max": 1},
        ],
        6: [  # 合体-大乘期秘境 - 混沌材料
            {"name": "混沌精华", "weight": 30, "min": 2, "max": 5},
            {"name": "神铁", "weight": 25, "min": 1, "max": 3},
            {"name": "万界石", "weight": 20, "min": 1, "max": 2},
            {"name": "道韵结晶", "weight": 15, "min": 1, "max": 2},
            {"name": "大道碎片", "weight": 10, "min": 1, "max": 1},
        ],
        7: [  # 渡劫期+秘境 - 仙界材料
            {"name": "仙晶", "weight": 30, "min": 3, "max": 8},
            {"name": "天劫雷晶", "weight": 25, "min": 2, "max": 5},
            {"name": "仙灵根", "weight": 20, "min": 1, "max": 3},
            {"name": "涅槃石", "weight": 15, "min": 1, "max": 2},
            {"name": "仙道本源", "weight": 10, "min": 1, "max": 1},
        ],
        8: [  # 仙境秘境 - 大罗材料
            {"name": "大罗金精", "weight": 30, "min": 3, "max": 10},
            {"name": "混元道石", "weight": 25, "min": 2, "max": 6},
            {"name": "天道碎片", "weight": 20, "min": 1, "max": 4},
            {"name": "圣灵之心", "weight": 15, "min": 1, "max": 3},
            {"name": "鸿蒙紫气", "weight": 10, "min": 1, "max": 2},
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
        4: {  # 元婴期秘境
            "drop_rate": 45,  # 45%爆率
            "items": [
                {"name": "血魔剑", "type": "武器", "quality": "传说", "weight": 20},
                {"name": "星辰法杖", "type": "武器", "quality": "传说", "weight": 18},
                {"name": "血煞战甲", "type": "防具", "quality": "传说", "weight": 20},
                {"name": "星纹道袍", "type": "防具", "quality": "传说", "weight": 18},
                {"name": "元婴护符", "type": "饰品", "quality": "传说", "weight": 14},
                {"name": "不朽骨链", "type": "饰品", "quality": "传说", "weight": 10},
            ]
        },
        5: {  # 化神-炼虚期秘境
            "drop_rate": 50,  # 50%爆率
            "items": [
                {"name": "虚空神剑", "type": "武器", "quality": "神器", "weight": 18},
                {"name": "仙陨战戟", "type": "武器", "quality": "神器", "weight": 16},
                {"name": "虚空战甲", "type": "防具", "quality": "神器", "weight": 18},
                {"name": "仙陨法袍", "type": "防具", "quality": "神器", "weight": 16},
                {"name": "空间戒指", "type": "饰品", "quality": "神器", "weight": 16},
                {"name": "时光之沙", "type": "饰品", "quality": "神器", "weight": 16},
            ]
        },
        6: {  # 合体-大乘期秘境
            "drop_rate": 55,  # 55%爆率
            "items": [
                {"name": "混沌神剑", "type": "武器", "quality": "至尊", "weight": 16},
                {"name": "万界神兵", "type": "武器", "quality": "至尊", "weight": 15},
                {"name": "混沌战甲", "type": "防具", "quality": "至尊", "weight": 16},
                {"name": "万界道袍", "type": "防具", "quality": "至尊", "weight": 15},
                {"name": "混沌神环", "type": "饰品", "quality": "至尊", "weight": 20},
                {"name": "大道之石", "type": "饰品", "quality": "至尊", "weight": 18},
            ]
        },
        7: {  # 渡劫期+秘境
            "drop_rate": 60,  # 60%爆率
            "items": [
                {"name": "仙剑·诛仙", "type": "武器", "quality": "仙品", "weight": 15},
                {"name": "仙器·开天斧", "type": "武器", "quality": "仙品", "weight": 12},
                {"name": "仙甲·不灭金身", "type": "防具", "quality": "仙品", "weight": 18},
                {"name": "仙袍·万劫不磨", "type": "防具", "quality": "仙品", "weight": 15},
                {"name": "仙环·涅槃重生", "type": "饰品", "quality": "仙品", "weight": 20},
                {"name": "仙玉·大道本源", "type": "饰品", "quality": "仙品", "weight": 20},
            ]
        },
        8: {  # 仙境秘境
            "drop_rate": 65,  # 65%爆率
            "items": [
                {"name": "大罗剑·斩道", "type": "武器", "quality": "大罗", "weight": 16},
                {"name": "混元斧·开天", "type": "武器", "quality": "大罗", "weight": 14},
                {"name": "圣甲·不朽", "type": "防具", "quality": "大罗", "weight": 18},
                {"name": "道袍·鸿蒙", "type": "防具", "quality": "大罗", "weight": 16},
                {"name": "圣环·永恒", "type": "饰品", "quality": "大罗", "weight": 18},
                {"name": "天道印", "type": "饰品", "quality": "大罗", "weight": 18},
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
        4: {  # 元婴期秘境
            "drop_rate": 25,  # 25%爆率
            "items": [
                {"name": "血海魔功", "type": "魔道功法", "weight": 25},
                {"name": "星辰炼体诀", "type": "体修功法", "weight": 25},
                {"name": "元婴变", "type": "神通功法", "weight": 20},
                {"name": "夺命血爪", "type": "杀招", "weight": 15},
                {"name": "星辰坠", "type": "终极术法", "weight": 15},
            ]
        },
        5: {  # 化神-炼虚期秘境
            "drop_rate": 30,  # 30%爆率
            "items": [
                {"name": "虚空大挪移", "type": "空间神通", "weight": 25},
                {"name": "时光倒流术", "type": "时间秘法", "weight": 20},
                {"name": "仙陨九式", "type": "绝世剑法", "weight": 25},
                {"name": "法则感悟", "type": "悟道心法", "weight": 20},
                {"name": "空间撕裂", "type": "终极神通", "weight": 10},
            ]
        },
        6: {  # 合体-大乘期秘境
            "drop_rate": 35,  # 35%爆率
            "items": [
                {"name": "混沌开天诀", "type": "至尊功法", "weight": 20},
                {"name": "万界归一", "type": "终极心法", "weight": 18},
                {"name": "大道无形", "type": "道祖传承", "weight": 22},
                {"name": "混沌神雷", "type": "至尊神通", "weight": 20},
                {"name": "万劫不灭体", "type": "不死之身", "weight": 20},
            ]
        },
        7: {  # 渡劫期+秘境
            "drop_rate": 40,  # 40%爆率
            "items": [
                {"name": "仙诀·九天玄功", "type": "仙界功法", "weight": 25},
                {"name": "仙术·天劫雷罚", "type": "仙界神通", "weight": 22},
                {"name": "仙道·涅槃重生", "type": "不死秘法", "weight": 20},
                {"name": "仙法·破碎虚空", "type": "飞升秘技", "weight": 18},
                {"name": "大道本源经", "type": "终极传承", "weight": 15},
            ]
        },
        8: {  # 仙境秘境
            "drop_rate": 45,  # 45%爆率
            "items": [
                {"name": "大罗金仙诀", "type": "大罗功法", "weight": 25},
                {"name": "混元无极功", "type": "混元心法", "weight": 22},
                {"name": "鸿蒙开天经", "type": "开天秘典", "weight": 20},
                {"name": "天道轮回术", "type": "天道神通", "weight": 18},
                {"name": "圣人传承", "type": "圣级传承", "weight": 15},
            ]
        },
    }

    # 双倍掉落触发概率
    DOUBLE_DROP_CHANCE = 5  # 5%概率触发双倍掉落

    # 每日秘境探索次数限制
    DAILY_RIFT_LIMIT = 5  # 每个秘境每天最多探索5次

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
    
    async def list_rifts(self, player: Optional[Player] = None) -> Tuple[bool, str]:
        """
        列出所有秘境

        Args:
            player: 玩家对象（可选，用于显示剩余次数）

        Returns:
            (成功标志, 消息)
        """
        rifts = await self.db.ext.get_all_rifts()

        if not rifts:
            return False, "❌ 当前没有开放的秘境！"

        # 获取玩家今日探索次数
        daily_count = {}
        if player:
            today = datetime.now().strftime("%Y-%m-%d")
            if player.rift_count_reset_date != today:
                # 日期不同，重置计数
                daily_count = {}
            else:
                daily_count = player.get_rift_daily_count()

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
            msg += f"  灵石奖励：{gold_range[0]:,}-{gold_range[1]:,}\n"

            # 显示剩余次数
            if player:
                used_count = daily_count.get(str(rift.rift_id), 0)
                remaining = self.DAILY_RIFT_LIMIT - used_count
                if remaining > 0:
                    msg += f"  今日剩余：{remaining}/{self.DAILY_RIFT_LIMIT} 次\n\n"
                else:
                    msg += f"  今日剩余：已达上限 ❌\n\n"
            else:
                msg += "\n"

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

        # 3.5. 检查每日次数限制
        today = datetime.now().strftime("%Y-%m-%d")
        daily_count = player.get_rift_daily_count()

        # 如果日期不同，重置计数
        if player.rift_count_reset_date != today:
            daily_count = {}
            player.rift_count_reset_date = today
            player.set_rift_daily_count(daily_count)
            await self.db.update_player(player)

        # 检查该秘境今日探索次数
        rift_id_str = str(rift_id)
        used_count = daily_count.get(rift_id_str, 0)
        if used_count >= self.DAILY_RIFT_LIMIT:
            return False, f"❌【{rift.rift_name}】今日探索次数已达上限（{self.DAILY_RIFT_LIMIT}次）！\n💡 明日 00:00 重置次数。"

        # 4. 检查境界要求（上下限）
        level_diff = player.level_index - rift.required_level

        # 等级过低
        if level_diff < 0:
            level_name = self._get_level_name(rift.required_level)
            return False, f"❌ 探索【{rift.rift_name}】需要达到【{level_name}】！"

        # 等级过高（硬性限制）
        if level_diff > 20:
            return False, f"❌【{rift.rift_name}】等级过低，已无法进入。\n💡 请使用 /秘境列表 查看适合你的秘境。"

        # 等级偏高（警告+掉落惩罚）
        warning_msg = ""
        if level_diff > 10:
            penalty_percent = (20 - level_diff) * 5  # 11级差=45%掉落率
            warning_msg = f"\n⚠️ 警告：秘境等级过低，装备/功法掉落率降低{100-penalty_percent}%！建议选择更高级秘境。"

        # 5. 设置探索状态，存储秘境ID和等级差（用于掉落计算）
        scheduled_time = int(time.time()) + self.explore_duration
        extra_data = {"rift_id": rift_id, "rift_level": rift.rift_level, "level_diff": level_diff}
        await self.db.ext.set_user_busy(user_id, UserStatus.EXPLORING, scheduled_time, extra_data)

        remaining = self.DAILY_RIFT_LIMIT - used_count - 1
        return True, f"✨ 你进入了『{rift.rift_name}』！探索需要 {self.explore_duration//60} 分钟。\n使用 /完成探索 领取奖励\n📋 今日剩余次数：{remaining}/{self.DAILY_RIFT_LIMIT}{warning_msg}"
    
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
        level_diff = extra_data.get("level_diff", 0)  # 获取等级差
        
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
        
        # 6. 物品掉落（根据秘境等级和等级差）
        dropped_items = []
        item_msg = ""
        dropped_items = await self._roll_rift_drops(player, rift_level, event["item_chance"], level_diff)
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

        # 7. 增加每日秘境探索次数计数
        today = datetime.now().strftime("%Y-%m-%d")
        daily_count = player.get_rift_daily_count()

        # 如果日期不同，重置计数（兼容处理）
        if player.rift_count_reset_date != today:
            daily_count = {}
            player.rift_count_reset_date = today

        # 增加该秘境的探索次数
        rift_id_str = str(rift_id)
        daily_count[rift_id_str] = daily_count.get(rift_id_str, 0) + 1
        player.set_rift_daily_count(daily_count)

        # 8. 应用奖励
        player.experience += exp_reward
        player.gold += gold_reward
        await self.db.update_player(player)

        # 9. 清除CD
        await self.db.ext.set_user_free(user_id)

        # 计算剩余次数
        remaining = self.DAILY_RIFT_LIMIT - daily_count[rift_id_str]
        remaining_msg = f"\n📋 今日剩余次数：{remaining}/{self.DAILY_RIFT_LIMIT}" if remaining > 0 else "\n⚠️ 今日探索次数已用尽"

        msg = f"""
🌀 探索完成 - {rift_name}
━━━━━━━━━━━━━━━

{event["desc"]}

获得修为：+{exp_reward:,}
获得灵石：+{gold_reward:,}{item_msg}{remaining_msg}
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
    
    async def _roll_rift_drops(self, player: Player, rift_level: int, item_chance: int, level_diff: int = 0) -> List[Tuple[str, int]]:
        """
        根据秘境等级随机掉落物品（新系统：材料+装备+功法）

        Args:
            player: 玩家对象
            rift_level: 秘境等级 (1-3)
            item_chance: 基础掉落概率（事件加成）
            level_diff: 等级差（玩家等级 - 秘境要求等级）

        Returns:
            掉落物品列表 [(物品名, 数量), ...]
        """
        dropped_items = []

        # 检查双倍掉落
        is_double_drop = random.randint(1, 100) <= self.DOUBLE_DROP_CHANCE

        # 计算等级差掉落惩罚（超过10级开始惩罚）
        drop_penalty = 1.0
        if level_diff > 10:
            # 11级差=90%掉落率，20级差=10%掉落率，线性递减
            drop_penalty = max(0.1, (20 - level_diff) / 10)

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

        # ===== 2. 装备掉落（爆率触发，受等级差惩罚）=====
        equipment_config = self.RIFT_EQUIPMENT_TABLE.get(rift_level, self.RIFT_EQUIPMENT_TABLE[1])
        equipment_drop_rate = equipment_config["drop_rate"] * drop_penalty  # 应用惩罚

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

        # ===== 3. 功法掉落（低爆率，受等级差惩罚）=====
        skill_config = self.RIFT_SKILL_TABLE.get(rift_level, self.RIFT_SKILL_TABLE[1])
        skill_drop_rate = skill_config["drop_rate"] * drop_penalty  # 应用惩罚

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
