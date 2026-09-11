# managers/sect_manager.py
"""
宗门系统管理器 - 处理宗门创建、管理、捐献、任务等逻辑
参照NoneBot2插件的xiuxian_sect实现
"""

import random
import sqlite3
import time
from datetime import datetime
from typing import Tuple, List, Optional, Dict
from astrbot.api import logger
from ..data.data_manager import DataBase
from ..data.migration import repair_sect_daily_tasks_table
from ..models_extended import Sect, UserStatus
from ..models import Player

SECT_NAME_MIN_LENGTH = 2
SECT_NAME_MAX_LENGTH = 12
SECT_NAME_FORBIDDEN = ["管理员", "系统", "官方", "GM", "admin"]


class SectManager:
    """宗门系统管理器"""
    
    # 宗门职位定义
    POSITIONS = {
        0: "宗主",
        1: "长老",
        2: "亲传弟子",
        3: "内门弟子",
        4: "外门弟子"
    }
    
    # 宗门职位权限
    POSITION_PERMISSIONS = {
        0: ["manage_all", "kick", "position_change", "build", "search_skill"],
        1: ["kick_outer", "build"],
        2: ["learn_skill"],
        3: ["learn_skill"],
        4: []  # 外门弟子无特殊权限
    }
    
    def __init__(self, db: DataBase, config_manager=None):
        self.db = db
        self.config = config_manager.sect_config if config_manager else {}
    
    def _validate_sect_name(self, name: str) -> Tuple[bool, str]:
        """验证宗门名称"""
        if len(name) < SECT_NAME_MIN_LENGTH or len(name) > SECT_NAME_MAX_LENGTH:
            return False, f"❌ 宗门名称长度需在{SECT_NAME_MIN_LENGTH}-{SECT_NAME_MAX_LENGTH}字之间！"
        for forbidden in SECT_NAME_FORBIDDEN:
            if forbidden.lower() in name.lower():
                return False, f"❌ 宗门名称包含禁用词汇！"
        return True, ""
    
    async def create_sect(
        self,
        user_id: str,
        sect_name: str,
        required_stone: int = None,
        required_level: int = None
    ) -> Tuple[bool, str]:
        """
        创建宗门
        
        Args:
            user_id: 用户ID
            sect_name: 宗门名称
            required_stone: 需求灵石（默认为配置值或10000）
            required_level: 需求境界等级（默认为配置值或3）
            
        Returns:
            (成功标志, 消息)
        """
        # 加载配置
        if required_stone is None:
            required_stone = self.config.get("create_cost", 10000)
        if required_level is None:
            required_level = self.config.get("create_level_required", 3)
        # 1. 检查用户是否存在
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！"
        
        # 2. 检查是否已有宗门
        if player.sect_id != 0:
            return False, "❌ 你已经加入了宗门，无法创建新宗门！"
        
        # 3. 检查境界
        if player.level_index < required_level:
            return False, f"❌ 创建宗门需要达到境界等级 {required_level}！"
        
        # 4. 检查灵石
        if player.gold < required_stone:
            return False, f"❌ 创建宗门需要 {required_stone} 灵石！"
        
        # 验证宗门名称
        valid, error = self._validate_sect_name(sect_name)
        if not valid:
            return False, error
        
        # 5. 检查宗门名称是否重复
        existing_sect = await self.db.ext.get_sect_by_name(sect_name)
        if existing_sect:
            return False, f"❌ 宗门名称『{sect_name}』已被使用！"
        
        # 6. 扣除灵石
        player.gold -= required_stone
        await self.db.update_player(player)
        
        # 7. 创建宗门
        new_sect = Sect(
            sect_id=0,  # 自动生成
            sect_name=sect_name,
            sect_owner=user_id,
            sect_scale=100,  # 初始建设度
            sect_used_stone=0,
            sect_fairyland=0,
            sect_materials=100,  # 初始资材
            mainbuff="0",
            secbuff="0",
            elixir_room_level=0
        )
        
        sect_id = await self.db.ext.create_sect(new_sect)
        
        # 8. 更新玩家宗门信息（设为宗主）
        await self.db.ext.update_player_sect_info(user_id, sect_id, 0)
        
        # 9. 初始化用户buff信息（如果没有）
        buff_info = await self.db.ext.get_buff_info(user_id)
        if not buff_info:
            await self.db.ext.create_buff_info(user_id)
        
        return True, f"✨ 恭喜！你成功创建了宗门『{sect_name}』，成为一代宗主！"
    
    async def join_sect(self, user_id: str, sect_name: str) -> Tuple[bool, str]:
        """
        加入宗门
        
        Args:
            user_id: 用户ID
            sect_name: 宗门名称
            
        Returns:
            (成功标志, 消息)
        """
        # 1. 检查用户
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！"
        
        if player.sect_id != 0:
            return False, "❌ 你已经加入了宗门！请先退出当前宗门。"
        
        # 2. 查找宗门
        sect = await self.db.ext.get_sect_by_name(sect_name)
        if not sect:
            return False, f"❌ 未找到宗门『{sect_name}』！"
        
        # 3. 加入宗门（默认为外门弟子）
        await self.db.ext.update_player_sect_info(user_id, sect.sect_id, 4)
        
        # 4. 初始化buff信息
        buff_info = await self.db.ext.get_buff_info(user_id)
        if not buff_info:
            await self.db.ext.create_buff_info(user_id)
        
        return True, f"✨ 你成功加入了宗门『{sect_name}』，成为外门弟子！"
    
    async def leave_sect(self, user_id: str) -> Tuple[bool, str]:
        """
        退出宗门
        
        Args:
            user_id: 用户ID
            
        Returns:
            (成功标志, 消息)
        """
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！"
        
        if player.sect_id == 0:
            return False, "❌ 你还未加入任何宗门！"
        
        # 检查是否为宗主
        sect = await self.db.ext.get_sect_by_id(player.sect_id)
        if sect and sect.sect_owner == user_id:
            return False, "❌ 宗主无法直接退出宗门！请先传位或解散宗门。"
        
        sect_name = sect.sect_name if sect else "未知宗门"
        
        # 清除宗门信息
        await self.db.ext.update_player_sect_info(user_id, 0, 4)
        player.sect_contribution = 0
        await self.db.update_player(player)
        
        return True, f"✨ 你已退出宗门『{sect_name}』！"
    
    async def donate_to_sect(
        self,
        user_id: str,
        stone_amount: int
    ) -> Tuple[bool, str]:
        """
        宗门捐献（1灵石 = 10建设度）
        
        Args:
            user_id: 用户ID
            stone_amount: 捐献灵石数量
            
        Returns:
            (成功标志, 消息)
        """
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！"
        
        if player.sect_id == 0:
            return False, "❌ 你还未加入宗门！"
        
        if stone_amount <= 0:
            return False, "❌ 捐献数量必须大于0！"
        
        if player.gold < stone_amount:
            return False, f"❌ 你的灵石不足！当前拥有 {player.gold} 灵石。"
        
        # 扣除灵石
        player.gold -= stone_amount
        
        # 增加宗门贡献度（1灵石 = 1贡献）
        player.sect_contribution += stone_amount
        await self.db.update_player(player)
        
        # 增加宗门建设度和灵石（1灵石 = 10建设度）
        await self.db.ext.donate_to_sect(player.sect_id, stone_amount)

        scale_gained = stone_amount * 10

        # 检查并完成每日任务（捐献>=1000灵石）
        if stone_amount >= 1000:
            success, contribution = await self.complete_daily_task(user_id, "donate_stone")
            if success:
                return True, f"✨ 捐献成功！消耗 {stone_amount} 灵石，宗门获得 {scale_gained} 建设度！\n你的宗门贡献度：{player.sect_contribution}\n\n🎉 完成宗门每日任务「捐献灵石」，额外获得 {contribution} 贡献度！"

        return True, f"✨ 捐献成功！消耗 {stone_amount} 灵石，宗门获得 {scale_gained} 建设度！\n你的宗门贡献度：{player.sect_contribution}"
    
    async def get_sect_info(self, user_id: str) -> Tuple[bool, str, Optional[Dict]]:
        """
        获取宗门信息
        
        Args:
            user_id: 用户ID
            
        Returns:
            (成功标志, 消息, 宗门数据)
        """
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！", None
        
        if player.sect_id == 0:
            return False, "❌ 你还未加入宗门！", None
        
        sect = await self.db.ext.get_sect_by_id(player.sect_id)
        if not sect:
            return False, "❌ 宗门信息异常！", None
        
        # 获取宗主信息
        owner = await self.db.get_player_by_id(sect.sect_owner)
        owner_name = owner.user_name if owner and owner.user_name else sect.sect_owner
        
        # 获取成员数量
        members = await self.db.ext.get_sect_members(sect.sect_id)
        member_count = len(members)
        
        # 构建信息
        position_name = self.POSITIONS.get(player.sect_position, "未知")
        
        info_msg = f"""
🏛️ 宗门信息
━━━━━━━━━━━━━━━

宗门名称：{sect.sect_name}
宗主：{owner_name}
建设度：{sect.sect_scale}
宗门灵石：{sect.sect_used_stone}
宗门资材：{sect.sect_materials}
丹房等级：{sect.elixir_room_level}
成员数量：{member_count}人

你的职位：{position_name}
你的贡献：{player.sect_contribution}
        """.strip()
        
        sect_data = {
            "sect": sect,
            "player_position": player.sect_position,
            "player_contribution": player.sect_contribution,
            "member_count": member_count
        }
        
        return True, info_msg, sect_data
    
    async def list_all_sects(self) -> Tuple[bool, str]:
        """
        获取所有宗门列表
        
        Returns:
            (成功标志, 消息)
        """
        sects = await self.db.ext.get_all_sects()
        
        if not sects:
            return False, "❌ 当前还没有任何宗门！"
        
        msg = "🏛️ 宗门列表\n"
        msg += "━━━━━━━━━━━━━━━\n"
        
        for idx, sect in enumerate(sects[:10], 1):  # 只显示前10个
            owner = await self.db.get_player_by_id(sect.sect_owner)
            owner_name = owner.user_name if owner and owner.user_name else "未知"
            members = await self.db.ext.get_sect_members(sect.sect_id)
            
            msg += f"{idx}. 【{sect.sect_name}】\n"
            msg += f"   宗主：{owner_name}\n"
            msg += f"   建设度：{sect.sect_scale} | 成员：{len(members)}人\n\n"
        
        return True, msg
    
    async def change_position(
        self,
        operator_id: str,
        target_id: str,
        new_position: int
    ) -> Tuple[bool, str]:
        """
        变更宗门职位
        
        Args:
            operator_id: 操作者ID（必须是宗主）
            target_id: 目标用户ID
            new_position: 新职位（0-4）
            
        Returns:
            (成功标志, 消息)
        """
        # 检查操作者
        operator = await self.db.get_player_by_id(operator_id)
        if not operator or operator.sect_id == 0:
            return False, "❌ 你还未加入宗门！"
        
        if operator.sect_position != 0:
            return False, "❌ 只有宗主才能变更职位！"
        
        # 检查目标用户
        target = await self.db.get_player_by_id(target_id)
        if not target:
            return False, "❌ 目标用户不存在！"
        
        if target.sect_id != operator.sect_id:
            return False, "❌ 目标用户不在你的宗门！"
        
        if target_id == operator_id:
            return False, "❌ 无法变更自己的职位！"
        
        if new_position not in self.POSITIONS:
            return False, "❌ 无效的职位！职位范围：0（宗主）- 4（外门弟子）"
        
        if new_position == 0:
            return False, "❌ 无法直接任命宗主！请使用传位功能。"
        
        # 变更职位
        await self.db.ext.update_player_sect_info(target_id, target.sect_id, new_position)
        
        target_name = target.user_name if target.user_name else target_id
        position_name = self.POSITIONS[new_position]
        
        return True, f"✨ 已将 {target_name} 的职位变更为：{position_name}"
    
    async def transfer_ownership(
        self,
        current_owner_id: str,
        new_owner_id: str
    ) -> Tuple[bool, str]:
        """
        宗主传位
        
        Args:
            current_owner_id: 当前宗主ID
            new_owner_id: 新宗主ID
            
        Returns:
            (成功标志, 消息)
        """
        # 检查当前宗主
        current_owner = await self.db.get_player_by_id(current_owner_id)
        if not current_owner or current_owner.sect_id == 0:
            return False, "❌ 你还未加入宗门！"
        
        sect = await self.db.ext.get_sect_by_id(current_owner.sect_id)
        if not sect or sect.sect_owner != current_owner_id:
            return False, "❌ 你不是宗主！"
        
        # 检查新宗主
        new_owner = await self.db.get_player_by_id(new_owner_id)
        if not new_owner:
            return False, "❌ 目标用户不存在！"
        
        if new_owner.sect_id != current_owner.sect_id:
            return False, "❌ 目标用户不在你的宗门！"
        
        if new_owner_id == current_owner_id:
            return False, "❌ 无法传位给自己！"
        
        # 执行传位
        sect.sect_owner = new_owner_id
        await self.db.ext.update_sect(sect)
        
        # 更新职位：新宗主->宗主，旧宗主->长老
        await self.db.ext.update_player_sect_info(new_owner_id, sect.sect_id, 0)
        await self.db.ext.update_player_sect_info(current_owner_id, sect.sect_id, 1)
        
        new_owner_name = new_owner.user_name if new_owner.user_name else new_owner_id
        
        return True, f"✨ 宗主之位已传给 {new_owner_name}！你现在是长老。"
    
    async def kick_member(
        self,
        operator_id: str,
        target_id: str
    ) -> Tuple[bool, str]:
        """
        踢出宗门成员
        
        Args:
            operator_id: 操作者ID
            target_id: 目标用户ID
            
        Returns:
            (成功标志, 消息)
        """
        # 检查操作者权限
        operator = await self.db.get_player_by_id(operator_id)
        if not operator or operator.sect_id == 0:
            return False, "❌ 你还未加入宗门！"
        
        # 宗主和长老可以踢人
        if operator.sect_position not in [0, 1]:
            return False, "❌ 只有宗主和长老才能踢出成员！"
        
        # 检查目标
        target = await self.db.get_player_by_id(target_id)
        if not target:
            return False, "❌ 目标用户不存在！"
        
        if target.sect_id != operator.sect_id:
            return False, "❌ 目标用户不在你的宗门！"
        
        if target_id == operator_id:
            return False, "❌ 无法踢出自己！"
        
        # 长老只能踢外门弟子
        if operator.sect_position == 1 and target.sect_position <= 3:
            return False, "❌ 长老只能踢出外门弟子！"
        
        # 无法踢出宗主
        if target.sect_position == 0:
            return False, "❌ 无法踢出宗主！"
        
        # 踢出
        target_name = target.user_name if target.user_name else target_id
        await self.db.ext.update_player_sect_info(target_id, 0, 4)
        target.sect_contribution = 0
        await self.db.update_player(target)
        
        return True, f"✨ 已将 {target_name} 踢出宗门！"

    async def perform_sect_task(self, user_id: str) -> Tuple[bool, str]:
        """
        执行宗门任务
        
        Args:
            user_id: 用户ID
            
        Returns:
            (成功标志, 消息)
        """
        player = await self.db.get_player_by_id(user_id)
        if not player or player.sect_id == 0:
            return False, "❌ 你还未加入宗门！"
            
        # 检查CD (使用宗门任务CD类型，假设为4)
        user_cd = await self.db.ext.get_user_cd(user_id)
        if not user_cd:
            await self.db.ext.create_user_cd(user_id)
            user_cd = await self.db.ext.get_user_cd(user_id)
            
        current_time = int(time.time())
        # 假设 CD 记录在 type=4, scheduled_time 为下次可用时间
        # 这里重用 set_user_busy 逻辑，但任务通常是瞬时的，只设冷却
        if user_cd.type == UserStatus.SECT_TASK and current_time < user_cd.scheduled_time:
            remaining = user_cd.scheduled_time - current_time
            return False, f"❌ 宗门任务冷却中！还需 {remaining//60} 分钟。"

        # 执行任务
        contribution_gain = random.randint(10, 30)
        stone_gain = contribution_gain * 10
        
        player.sect_contribution += contribution_gain
        await self.db.update_player(player)
        
        # 宗门增加资源
        await self.db.ext.donate_to_sect(player.sect_id, 0) # 只更新建设度? donate_to_sect update both.
        # 手动更新宗门资源
        sect = await self.db.ext.get_sect_by_id(player.sect_id)
        if sect:
            sect.sect_materials += stone_gain
            await self.db.ext.update_sect(sect)

        # 设置1小时冷却
        await self.db.ext.set_user_busy(user_id, 4, current_time + 3600)
        
        return True, f"✨ 完成宗门任务！\n获得贡献：{contribution_gain}\n宗门资材：+{stone_gain}"

    async def handle_owner_death(self, sect_id: int, dead_owner_id: str) -> Tuple[bool, str]:
        """处理宗主死亡，自动传位或解散宗门"""
        members = await self.db.ext.get_sect_members(sect_id)
        # 过滤掉死亡的宗主
        remaining = [m for m in members if m.user_id != dead_owner_id]

        if not remaining:
            # 无其他成员，解散宗门
            await self.db.ext.delete_sect(sect_id)
            return True, "宗门已解散"

        # 按职位和贡献排序，选择新宗主
        remaining.sort(key=lambda m: (m.sect_position, -m.sect_contribution))
        new_owner = remaining[0]

        # 更新宗门宗主
        sect = await self.db.ext.get_sect_by_id(sect_id)
        if sect:
            sect.sect_owner = new_owner.user_id
            await self.db.ext.update_sect(sect)
            await self.db.ext.update_player_sect_info(new_owner.user_id, sect_id, 0)

        return True, f"宗主之位已传给{new_owner.user_name or new_owner.user_id}"

    def get_cultivation_bonus(self, sect_scale: int) -> float:
        """
        根据宗门建设度获取修炼加成

        Args:
            sect_scale: 宗门建设度

        Returns:
            修炼加成百分比（如 0.05 表示 5%）
        """
        if sect_scale >= 100001:
            return 0.30  # 超级宗门 +30%
        elif sect_scale >= 50001:
            return 0.20  # 顶级宗门 +20%
        elif sect_scale >= 20001:
            return 0.15  # 高级宗门 +15%
        elif sect_scale >= 5001:
            return 0.10  # 中级宗门 +10%
        elif sect_scale >= 1:
            return 0.05  # 初级宗门 +5%
        else:
            return 0.0   # 无宗门

    async def donate_technique(self, user_id: str, technique_name: str) -> Tuple[bool, str]:
        """
        捐献功法到宗门

        Args:
            user_id: 用户ID
            technique_name: 功法名称

        Returns:
            (成功标志, 消息)
        """
        player = await self.db.get_player_by_id(user_id)
        if not player or player.sect_id == 0:
            return False, "❌ 你还未加入宗门！"

        # 检查职位权限（只有宗主和长老可以捐献）
        if player.sect_position not in [0, 1]:
            return False, "❌ 只有宗主和长老才能捐献功法！"

        # 检查是否拥有该功法
        techniques = player.get_techniques_list()
        if technique_name not in techniques and player.main_technique != technique_name:
            return False, f"❌ 你没有功法【{technique_name}】！"

        # 检查功法是否已在功法库中
        cursor = await self.db.conn.execute(
            "SELECT id FROM sect_technique_library WHERE sect_id = ? AND technique_name = ?",
            (player.sect_id, technique_name)
        )
        existing = await cursor.fetchone()
        if existing:
            return False, f"❌ 功法【{technique_name}】已在宗门功法库中！"

        # 添加到功法库
        await self.db.conn.execute(
            "INSERT INTO sect_technique_library (sect_id, technique_name, donator_id, donate_time, borrow_count) VALUES (?, ?, ?, ?, 0)",
            (player.sect_id, technique_name, user_id, int(time.time()))
        )
        await self.db.conn.commit()

        # 奖励贡献度
        contribution_reward = 100
        player.sect_contribution += contribution_reward
        await self.db.update_player(player)

        return True, f"✨ 成功捐献功法【{technique_name}】到宗门！\n获得宗门贡献度：+{contribution_reward}\n💡 功法不会从你身上消失，其他成员可以借用。"

    async def borrow_technique(self, user_id: str, technique_name: str) -> Tuple[bool, str]:
        """
        借用宗门功法

        Args:
            user_id: 用户ID
            technique_name: 功法名称

        Returns:
            (成功标志, 消息)
        """
        player = await self.db.get_player_by_id(user_id)
        if not player or player.sect_id == 0:
            return False, "❌ 你还未加入宗门！"

        # 检查功法库中是否有该功法
        cursor = await self.db.conn.execute(
            "SELECT id FROM sect_technique_library WHERE sect_id = ? AND technique_name = ?",
            (player.sect_id, technique_name)
        )
        library_record = await cursor.fetchone()
        if not library_record:
            return False, f"❌ 宗门功法库中没有功法【{technique_name}】！"

        # 检查是否已拥有该功法
        techniques = player.get_techniques_list()
        if technique_name in techniques or player.main_technique == technique_name:
            return False, f"❌ 你已经拥有功法【{technique_name}】！"

        # 检查当前借用数量（最多2个）
        cursor = await self.db.conn.execute(
            "SELECT COUNT(*) FROM sect_technique_borrow WHERE user_id = ? AND is_active = 1",
            (user_id,)
        )
        current_borrow_count = (await cursor.fetchone())[0]
        if current_borrow_count >= 2:
            return False, "❌ 你已借用2个功法，无法继续借用！请等待归还后再借。"

        # 检查是否已借用该功法
        cursor = await self.db.conn.execute(
            "SELECT id FROM sect_technique_borrow WHERE user_id = ? AND technique_name = ? AND is_active = 1",
            (user_id, technique_name)
        )
        if await cursor.fetchone():
            return False, f"❌ 你已借用功法【{technique_name}】！"

        # 创建借用记录（7天期限）
        borrow_time = int(time.time())
        return_time = borrow_time + 7 * 24 * 3600  # 7天后
        await self.db.conn.execute(
            "INSERT INTO sect_technique_borrow (sect_id, user_id, technique_name, borrow_time, return_time, is_active) VALUES (?, ?, ?, ?, ?, 1)",
            (player.sect_id, user_id, technique_name, borrow_time, return_time)
        )

        # 更新借用次数
        await self.db.conn.execute(
            "UPDATE sect_technique_library SET borrow_count = borrow_count + 1 WHERE sect_id = ? AND technique_name = ?",
            (player.sect_id, technique_name)
        )

        # 将功法添加到玩家身上（自动装备）
        if not player.main_technique:
            player.main_technique = technique_name
        else:
            techniques.append(technique_name)
            player.set_techniques_list(techniques)

        await self.db.conn.commit()
        await self.db.update_player(player)

        from datetime import datetime
        return_date = datetime.fromtimestamp(return_time).strftime("%Y-%m-%d %H:%M")

        return True, f"✨ 成功借用功法【{technique_name}】！\n⏰ 归还时间：{return_date}\n💡 到期后功法将自动归还。"

    async def get_technique_library(self, user_id: str) -> Tuple[bool, str]:
        """
        获取宗门功法库列表

        Args:
            user_id: 用户ID

        Returns:
            (成功标志, 消息)
        """
        player = await self.db.get_player_by_id(user_id)
        if not player or player.sect_id == 0:
            return False, "❌ 你还未加入宗门！"

        # 获取功法库列表
        cursor = await self.db.conn.execute(
            "SELECT technique_name, donator_id, borrow_count FROM sect_technique_library WHERE sect_id = ?",
            (player.sect_id,)
        )
        techniques = await cursor.fetchall()

        if not techniques:
            return False, "❌ 宗门功法库为空！\n💡 宗主和长老可使用「捐献功法 <功法名>」捐献功法。"

        msg = "📜 宗门功法库\n"
        msg += "━━━━━━━━━━━━━━━\n"

        for idx, (tech_name, donator_id, borrow_count) in enumerate(techniques, 1):
            donator = await self.db.get_player_by_id(donator_id)
            donator_name = donator.user_name if donator and donator.user_name else donator_id
            msg += f"{idx}. 【{tech_name}】\n"
            msg += f"   捐献者：{donator_name}\n"
            msg += f"   借用次数：{borrow_count}\n\n"

        msg += "💡 使用「借用功法 <功法名>」借用功法（7天期限）"

        return True, msg

    async def check_borrowed_techniques(self, user_id: str) -> Tuple[bool, List[str]]:
        """
        检查并归还过期的借用功法

        Args:
            user_id: 用户ID

        Returns:
            (有过期功法, 归还的功法列表)
        """
        current_time = int(time.time())

        # 查找过期且活跃的借用记录
        cursor = await self.db.conn.execute(
            "SELECT id, technique_name FROM sect_technique_borrow WHERE user_id = ? AND return_time <= ? AND is_active = 1",
            (user_id, current_time)
        )
        expired_borrows = await cursor.fetchall()

        if not expired_borrows:
            return False, []

        returned_techniques = []
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, []

        techniques = player.get_techniques_list()

        for borrow_id, technique_name in expired_borrows:
            # 从玩家身上移除功法
            if player.main_technique == technique_name:
                player.main_technique = ""
            elif technique_name in techniques:
                techniques.remove(technique_name)

            # 标记借用记录为已归还
            await self.db.conn.execute(
                "UPDATE sect_technique_borrow SET is_active = 0 WHERE id = ?",
                (borrow_id,)
            )

            returned_techniques.append(technique_name)

        if returned_techniques:
            player.set_techniques_list(techniques)
            await self.db.update_player(player)
            await self.db.conn.commit()

        return True, returned_techniques

    async def get_daily_tasks(self, user_id: str) -> Tuple[bool, str, Dict]:
        """
        获取宗门每日任务

        Args:
            user_id: 用户ID

        Returns:
            (成功标志, 消息, 任务数据)
        """
        player = await self.db.get_player_by_id(user_id)
        if not player or player.sect_id == 0:
            return False, "❌ 你还未加入宗门！", {}

        today = datetime.now().strftime("%Y-%m-%d")

        # 获取今日任务记录
        cursor = await self.db.conn.execute(
            "SELECT donated_stone, completed_adventure, harvested_farm, completed_rift FROM sect_daily_tasks WHERE user_id = ? AND task_date = ?",
            (user_id, today)
        )
        row = await cursor.fetchone()

        if row:
            donated_stone, completed_adventure, harvested_farm, completed_rift = row
        else:
            # 创建今日记录（(user_id, task_date) 复合主键，每天一行）
            await self._ensure_daily_task_row(user_id, today)
            donated_stone = completed_adventure = harvested_farm = completed_rift = 0

        # 定义任务列表
        all_tasks = {
            "donate_stone": {"name": "捐献灵石", "desc": "捐献1000灵石", "reward": 50, "completed": bool(donated_stone)},
            "adventure": {"name": "完成历练", "desc": "完成任意历练", "reward": 30, "completed": bool(completed_adventure)},
            "farm_harvest": {"name": "种植灵草", "desc": "收获灵田", "reward": 20, "completed": bool(harvested_farm)},
            "rift_explore": {"name": "探索秘境", "desc": "完成秘境探索", "reward": 40, "completed": bool(completed_rift)},
        }

        msg = "📋 宗门每日任务\n"
        msg += "━━━━━━━━━━━━━━━\n"

        total_contribution = 0
        for task_id, task_info in all_tasks.items():
            status = "✅" if task_info["completed"] else "⭕"
            msg += f"{status} {task_info['name']}\n"
            msg += f"   要求：{task_info['desc']}\n"
            msg += f"   奖励：+{task_info['reward']} 贡献度\n\n"
            if task_info["completed"]:
                total_contribution += task_info["reward"]

        msg += f"今日已获得贡献度：{total_contribution}\n"
        msg += "💡 任务自动检测完成"

        return True, msg, all_tasks

    async def complete_daily_task(self, user_id: str, task_type: str) -> Tuple[bool, int]:
        """
        完成宗门每日任务

        Args:
            user_id: 用户ID
            task_type: 任务类型（donate_stone/adventure/farm_harvest/rift_explore）

        Returns:
            (成功标志, 获得的贡献度)
        """
        player = await self.db.get_player_by_id(user_id)
        if not player or player.sect_id == 0:
            return False, 0

        today = datetime.now().strftime("%Y-%m-%d")

        # 映射任务类型到列名
        task_column_map = {
            "donate_stone": "donated_stone",
            "adventure": "completed_adventure",
            "farm_harvest": "harvested_farm",
            "rift_explore": "completed_rift",
        }

        column_name = task_column_map.get(task_type)
        if not column_name:
            return False, 0

        # 确保今日任务记录存在（(user_id, task_date) 复合主键，每天一行）
        await self._ensure_daily_task_row(user_id, today)

        # 原子标记完成：仅当该任务今日尚未完成时更新成功，避免重复发放贡献度
        try:
            cursor = await self.db.conn.execute(
                f"UPDATE sect_daily_tasks SET {column_name} = 1 WHERE user_id = ? AND task_date = ? AND {column_name} = 0",
                (user_id, today)
            )
            updated = cursor.rowcount
            await self.db.conn.commit()
        except Exception:
            await self.db.conn.rollback()
            raise

        if not updated:
            # 今日该任务已完成
            return False, 0

        # 奖励贡献度
        task_rewards = {
            "donate_stone": 50,
            "adventure": 30,
            "farm_harvest": 20,
            "rift_explore": 40,
        }

        contribution = task_rewards.get(task_type, 0)
        if contribution > 0:
            player.sect_contribution += contribution
            await self.db.update_player(player)

        return True, contribution

    async def _ensure_daily_task_row(self, user_id: str, today: str):
        """
        确保今日宗门任务记录存在

        表结构为 (user_id, task_date) 复合主键，同一玩家每天一行；
        若检测到历史遗留的 user_id 单主键结构，会自动重建修复，
        避免出现 "UNIQUE constraint failed: sect_daily_tasks.user_id"。

        Args:
            user_id: 用户ID
            today: 日期字符串（YYYY-MM-DD）
        """
        sql = (
            "INSERT INTO sect_daily_tasks "
            "(user_id, task_date, donated_stone, completed_adventure, harvested_farm, completed_rift) "
            "VALUES (?, ?, 0, 0, 0, 0) "
            "ON CONFLICT(user_id, task_date) DO NOTHING"
        )
        try:
            await self.db.conn.execute(sql, (user_id, today))
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as e:
            # 旧版单主键表结构：ON CONFLICT 无法匹配 / 主键冲突
            # 先回滚失败语句留下的隐式事务，再重建表结构后重试
            logger.warning(f"[宗门每日任务] 表结构异常，正在自动修复: {e}")
            await self.db.conn.rollback()
            try:
                await repair_sect_daily_tasks_table(self.db.conn)
                await self.db.conn.execute(sql, (user_id, today))
            except Exception:
                await self.db.conn.rollback()
                raise
        except Exception:
            await self.db.conn.rollback()
            raise

        await self.db.conn.commit()
