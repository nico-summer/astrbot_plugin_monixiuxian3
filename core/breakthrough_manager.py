# core/breakthrough_manager.py

import random
from typing import Optional, Tuple
from astrbot.api import logger

from ..models import Player
from ..data import DataBase
from ..config_manager import ConfigManager

# 死亡机制（批次1）：level_index >= 13 为金丹期及以上（金丹期初期）
# 低阶修士（练气-筑基）首次死亡触发「劫后重生」，金丹以上死亡进入元神状态
SOUL_STATE_LEVEL_INDEX = 13

# 死亡系统配置默认值（与 config/death_config.json 保持一致）
DEFAULT_DEATH_CONFIG = {
    "soul_revival_hours": 24,
    "soul_exp_loss_rate": 0.05,
    "rebirth_exp_keep_rate": 0.7,
    "soul_exp_decay_per_hour": 0.01,
}


class BreakthroughManager:
    """突破管理器 - 处理境界突破相关逻辑"""

    def __init__(self, db: DataBase, config_manager: ConfigManager, config: dict):
        self.db = db
        self.config_manager = config_manager
        self.config = config

    def _get_death_config(self) -> dict:
        """获取死亡系统配置（配置管理器 -> 插件配置 -> 内置默认值逐级回退）"""
        config = dict(DEFAULT_DEATH_CONFIG)

        getter = getattr(self.config_manager, "get_death_config", None)
        if callable(getter):
            try:
                file_config = getter() or {}
                if isinstance(file_config, dict):
                    config.update({k: v for k, v in file_config.items() if v is not None})
            except Exception as e:
                logger.warning(f"读取死亡系统配置失败，使用默认值: {e}")

        try:
            plugin_config = self.config.get("death_config", {}) if hasattr(self.config, "get") else {}
        except Exception:
            plugin_config = {}
        if isinstance(plugin_config, dict):
            config.update({k: v for k, v in plugin_config.items() if v is not None})

        return config

    def _to_float(self, value, default: float) -> float:
        """安全转换为 float，非法值回退默认值"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def check_breakthrough_requirements(self, player: Player) -> Tuple[bool, str]:
        """检查玩家是否满足突破条件

        Args:
            player: 玩家对象

        Returns:
            (是否满足, 错误消息)
        """
        # 根据修炼类型获取对应的境界数据
        level_data = self.config_manager.get_level_data(player.cultivation_type)

        # 检查是否已经是最高境界
        if player.level_index >= len(level_data) - 1:
            return False, "你已经达到了最高境界，无法继续突破！"

        # 获取下一境界所需修为
        next_level_index = player.level_index + 1
        next_level_data = level_data[next_level_index]
        required_exp = next_level_data.get("exp_needed", 0)

        # 检查修为是否满足
        if player.experience < required_exp:
            current_level = level_data[player.level_index]["level_name"]
            next_level = next_level_data["level_name"]
            return False, (
                f"修为不足！\n"
                f"当前境界：{current_level}\n"
                f"当前修为：{player.experience}\n"
                f"突破至【{next_level}】需要修为：{required_exp}"
            )

        return True, ""

    def calculate_breakthrough_success_rate(
        self,
        player: Player,
        pill_name: Optional[str] = None,
        temp_bonus: float = 0.0
    ) -> Tuple[float, str]:
        """计算突破成功率

        Args:
            player: 玩家对象
            pill_name: 使用的破境丹名称（可选）

        Returns:
            (成功率, 说明信息)
        """
        # 根据修炼类型获取对应的境界数据
        level_data = self.config_manager.get_level_data(player.cultivation_type)

        # 获取基础成功率
        next_level_index = player.level_index + 1
        next_level_data = level_data[next_level_index]
        base_success_rate = next_level_data.get("success_rate", 0.5)

        info_lines = [
            f"基础成功率：{base_success_rate:.1%}"
        ]

        final_rate = base_success_rate + temp_bonus
        max_rate = 1.0  # 默认最大100%

        if temp_bonus:
            info_lines.append(f"临时丹药加成：{temp_bonus:+.1%}")

        # 如果使用了破境丹
        if pill_name:
            pill_data = self.config_manager.pills_data.get(pill_name)
            if pill_data and pill_data.get("subtype") == "breakthrough":
                breakthrough_bonus = pill_data.get("breakthrough_bonus", 0)
                max_rate = pill_data.get("max_success_rate", 1.0)

                # 计算加成后的成功率
                final_rate = min(base_success_rate + temp_bonus + breakthrough_bonus, max_rate)

                info_lines.append(f"破境丹加成：+{breakthrough_bonus:.1%}")
                info_lines.append(f"最大成功率限制：{max_rate:.1%}")
            else:
                logger.warning(f"无效的破境丹：{pill_name}")

        final_rate = max(0.0, min(final_rate, max_rate))
        info_lines.append(f"最终成功率：{final_rate:.1%}")
        info = "\n".join(info_lines)

        return final_rate, info

    async def execute_breakthrough(
        self,
        player: Player,
        pill_name: Optional[str] = None,
        temp_bonus: float = 0.0,
        death_rate_multiplier: float = 1.0
    ) -> Tuple[bool, str, bool]:
        """执行突破

        Args:
            player: 玩家对象
            pill_name: 使用的破境丹名称（可选）

        Returns:
            (是否成功, 消息, 是否死亡)
        """
        # 检查突破条件
        can_breakthrough, error_msg = self.check_breakthrough_requirements(player)
        if not can_breakthrough:
            return False, error_msg, False

        # 计算成功率
        success_rate, rate_info = self.calculate_breakthrough_success_rate(player, pill_name, temp_bonus)

        # 根据修炼类型获取对应的境界数据
        level_data = self.config_manager.get_level_data(player.cultivation_type)

        # 判定突破结果
        random_value = random.random()
        breakthrough_success = random_value < success_rate

        current_level_name = level_data[player.level_index]["level_name"]
        next_level_index = player.level_index + 1
        next_level_data = level_data[next_level_index]
        next_level_name = next_level_data["level_name"]

        if breakthrough_success:
            # 突破成功 - 提升境界并更新属性
            old_level_index = player.level_index
            player.level_index = next_level_index

            # 直接从下一境界配置中读取突破增量，并累加到玩家属性上
            # 这样可以保留玩家初始化时的随机属性值
            lifespan_gain = next_level_data.get("breakthrough_lifespan_gain", 0)
            mental_power_gain = next_level_data.get("breakthrough_mental_power_gain", 0)
            physical_damage_gain = next_level_data.get("breakthrough_physical_damage_gain", 0)
            magic_damage_gain = next_level_data.get("breakthrough_magic_damage_gain", 0)
            physical_defense_gain = next_level_data.get("breakthrough_physical_defense_gain", 0)
            magic_defense_gain = next_level_data.get("breakthrough_magic_defense_gain", 0)

            # 根据修炼类型处理灵气/气血增长
            if player.cultivation_type == "体修":
                # 体修使用气血
                blood_qi_gain = next_level_data.get("breakthrough_blood_qi_gain", 0)
                player.max_blood_qi += blood_qi_gain
                player.blood_qi = player.max_blood_qi  # 恢复满气血
                energy_name = "气血"
                energy_gain = blood_qi_gain
            else:
                # 灵修使用灵气
                spiritual_qi_gain = next_level_data.get("breakthrough_spiritual_qi_gain", 0)
                player.max_spiritual_qi += spiritual_qi_gain
                player.spiritual_qi = player.max_spiritual_qi  # 恢复满灵气
                energy_name = "灵气"
                energy_gain = spiritual_qi_gain

            # 应用属性增长
            player.lifespan += lifespan_gain
            player.physical_damage += physical_damage_gain
            player.magic_damage += magic_damage_gain
            player.physical_defense += physical_defense_gain
            player.magic_defense += magic_defense_gain
            player.mental_power += mental_power_gain

            # 保存到数据库
            await self.db.update_player(player)
            
            # 检查并处理突破贷款自动还款
            loan_msg = await self._handle_breakthrough_loan_repay(player)

            # 根据修炼类型生成不同的成功消息
            if player.cultivation_type == "体修":
                success_msg = (
                    f"✨ 突破成功！✨\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"{rate_info}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"恭喜你从【{current_level_name}】突破至【{next_level_name}】！\n"
                    f"境界提升，肉身更加强横！\n"
                    f"\n【属性增长】\n"
                    f"寿命 +{lifespan_gain}\n"
                    f"最大气血 +{energy_gain}\n"
                    f"物伤 +{physical_damage_gain}\n"
                    f"物防 +{physical_defense_gain}\n"
                    f"法防 +{magic_defense_gain}\n"
                    f"精神力 +{mental_power_gain}\n"
                    f"\n【当前属性】\n"
                    f"寿命：{player.lifespan}\n"
                    f"最大气血：{player.max_blood_qi}\n"
                    f"物伤：{player.physical_damage}\n"
                    f"物防：{player.physical_defense}\n"
                    f"法防：{player.magic_defense}\n"
                    f"精神力：{player.mental_power}"
                )
            else:
                success_msg = (
                    f"✨ 突破成功！✨\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"{rate_info}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"恭喜你从【{current_level_name}】突破至【{next_level_name}】！\n"
                    f"境界提升，实力大增！\n"
                    f"\n【属性增长】\n"
                    f"寿命 +{lifespan_gain}\n"
                    f"最大灵气 +{energy_gain}\n"
                    f"法伤 +{magic_damage_gain}\n"
                    f"物伤 +{physical_damage_gain}\n"
                    f"法防 +{magic_defense_gain}\n"
                    f"物防 +{physical_defense_gain}\n"
                    f"精神力 +{mental_power_gain}\n"
                    f"\n【当前属性】\n"
                    f"寿命：{player.lifespan}\n"
                    f"最大灵气：{player.max_spiritual_qi}\n"
                    f"法伤：{player.magic_damage}\n"
                    f"物伤：{player.physical_damage}\n"
                    f"法防：{player.magic_defense}\n"
                    f"物防：{player.physical_defense}\n"
                    f"精神力：{player.mental_power}"
                )

            logger.info(
                f"玩家 {player.user_id} 突破成功：{current_level_name} -> {next_level_name}"
            )
            
            # 如果有贷款相关消息，追加到成功消息后
            if loan_msg:
                success_msg += f"\n\n{loan_msg}"

            return True, success_msg, False

        else:
            # 突破失败 - 判断是否死亡
            death_probability_range = self.config.get("VALUES", {}).get(
                "BREAKTHROUGH_DEATH_PROBABILITY",
                [0.01, 0.1]  # 默认1%-10%死亡概率
            )

            # 随机一个死亡概率
            death_rate = random.uniform(death_probability_range[0], death_probability_range[1])
            death_rate = max(0.0, min(1.0, death_rate * death_rate_multiplier))
            died = random.random() < death_rate

            if died:
                # 检查是否有回生丹效果
                from .pill_manager import PillManager
                pill_manager = PillManager(self.db, self.config_manager)
                resurrected = await pill_manager.handle_resurrection(player)

                if resurrected:
                    # 回生丹触发，玩家复活
                    resurrection_msg = (
                        f"💀 突破失败，走火入魔！💀\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"{rate_info}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"你在突破【{next_level_name}】时走火入魔...\n"
                        f"\n"
                        f"⚡ 回生丹效果触发！⚡\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"🌟 你涅槃重生了！\n"
                        f"⚠️ 但所有属性降低到之前的一半\n"
                        f"💊 回生丹效果已消耗\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"请继续修炼，重回巅峰！"
                    )

                    logger.info(
                        f"玩家 {player.user_id} 突破失败触发回生丹，成功复活"
                    )

                    # 返回False（突破失败），消息，False（未真正死亡）
                    return False, resurrection_msg, False

                # 死亡机制（批次1）：按境界分级处理
                death_config = self._get_death_config()

                if player.level_index < SOUL_STATE_LEVEL_INDEX:
                    # ===== 练气期-筑基期：首次死亡「劫后重生」，第二次真正陨落 =====
                    if not player.used_rebirth:
                        keep_rate = self._to_float(
                            death_config.get("rebirth_exp_keep_rate"), DEFAULT_DEATH_CONFIG["rebirth_exp_keep_rate"]
                        )
                        keep_rate = max(0.0, min(1.0, keep_rate))

                        old_level_index = player.level_index
                        player.level_index = max(0, player.level_index - 1)
                        player.experience = int(player.experience * keep_rate)
                        player.used_rebirth = True
                        player.state = "空闲"
                        player.cultivation_start_time = 0
                        await self.db.update_player(player)

                        current_level_name = level_data[player.level_index]["level_name"]
                        old_level_name = level_data[old_level_index]["level_name"]
                        level_change_text = (
                            f"境界倒退：{old_level_name} → {current_level_name}"
                            if old_level_index != player.level_index
                            else f"境界保留：{current_level_name}（已是最低境界）"
                        )

                        death_msg = (
                            f"💀 突破失败，走火入魔濒临死亡！\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"{rate_info}\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"🌟 天道垂怜，获得【劫后重生】机会\n"
                            f"📉 {level_change_text}\n"
                            f"💫 保留 {keep_rate:.0%} 修为（当前修为：{player.experience}）\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"⚠️ 此机会仅有一次，下次突破失败将彻底陨落！"
                        )

                        logger.info(
                            f"玩家 {player.user_id} 突破失败触发劫后重生："
                            f"{old_level_name} -> {current_level_name}，"
                            f"保留修为 {player.experience}"
                        )

                        return False, death_msg, False
                    else:
                        # 第二次死亡：真正删号
                        await self.db.delete_player_cascade(player.user_id)

                        death_msg = (
                            f"💀 走火入魔，道基崩塌！💀\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"{rate_info}\n"
                            f"━━━━━━━━━━━━━━━\n"
                            f"你在突破【{next_level_name}】时再次走火入魔，\n"
                            f"天道已不再垂怜，你的神魂彻底消散在天地间...\n"
                            f"🪦 {player.user_name or player.user_id} 陨落了\n"
                            f"所有修为和装备化为虚无\n"
                            f"若想重新修仙，请使用「我要修仙」命令重新开始"
                        )

                        logger.info(
                            f"玩家 {player.user_id} 突破失败并彻底陨落（已使用过劫后重生）："
                            f"{current_level_name} -> {next_level_name}，死亡概率 {death_rate:.2%}"
                        )

                        return False, death_msg, True
                else:
                    # ===== 金丹期以上：肉身崩解，进入元神状态 =====
                    import time

                    revival_hours = self._to_float(
                        death_config.get("soul_revival_hours"), DEFAULT_DEATH_CONFIG["soul_revival_hours"]
                    )
                    exp_loss_rate = self._to_float(
                        death_config.get("soul_exp_loss_rate"), DEFAULT_DEATH_CONFIG["soul_exp_loss_rate"]
                    )
                    decay_per_hour = self._to_float(
                        death_config.get("soul_exp_decay_per_hour"), DEFAULT_DEATH_CONFIG["soul_exp_decay_per_hour"]
                    )

                    player.is_soul_state = True
                    player.soul_death_time = int(time.time())
                    player.soul_exp_before_death = player.experience  # 保存死亡前的完整修为
                    player.experience = int(player.experience * 0.5)  # 元神期间展示修为=死亡前50%，复活按 soul_exp_before_death 结算
                    player.state = "元神"
                    player.cultivation_start_time = 0
                    await self.db.update_player(player)

                    death_msg = (
                        f"💀 突破失败，肉身崩解！\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"{rate_info}\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"✨ 金丹已成，元神不灭\n"
                        f"👻 你已进入【元神状态】（境界保留在{current_level_name}）\n"
                        f"📊 修为锁定 50%，每小时流失 {decay_per_hour:.1%}\n"
                        f"⏰ {revival_hours:.0f} 小时后可自然复活（永久损失 {exp_loss_rate:.0%} 修为）\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"💡 复活方式：\n"
                        f"1. 等待自然复活（发送「自然复活」，损失 {exp_loss_rate:.0%} 修为）\n"
                        f"2. 使用还魂丹（无损失，详见后续炼丹玩法）\n"
                        f"3. 请求宗门/师父救援（后续开放）\n"
                        f"━━━━━━━━━━━━━━━\n"
                        f"发送「元神状态」可查看元神详情"
                    )

                    logger.info(
                        f"玩家 {player.user_id} 突破失败进入元神状态："
                        f"{current_level_name} -> {next_level_name}，死亡概率 {death_rate:.2%}，"
                        f"自然复活需 {revival_hours:.0f} 小时"
                    )

                    return False, death_msg, False

            else:
                # 突破失败但未死亡 - 扣除部分修为
                exp_penalty = int(player.experience * 0.1)  # 扣除10%修为
                player.experience = max(0, player.experience - exp_penalty)

                await self.db.update_player(player)

                fail_msg = (
                    f"❌ 突破失败 ❌\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"{rate_info}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"突破【{next_level_name}】失败，但幸运地保住了性命\n"
                    f"修为受损，损失了 {exp_penalty} 点修为\n"
                    f"当前修为：{player.experience}\n"
                    f"请继续修炼，再接再厉！"
                )

                logger.info(
                    f"玩家 {player.user_id} 突破失败：{current_level_name} -> {next_level_name}，"
                    f"损失修为 {exp_penalty}"
                )

                return False, fail_msg, False
    
    async def _handle_breakthrough_loan_repay(self, player: Player) -> str:
        """处理突破贷款自动还款
        
        Args:
            player: 玩家对象
            
        Returns:
            还款消息（如果有贷款的话）
        """
        try:
            # 检查是否有突破贷款
            loan = await self.db.ext.get_active_loan(player.user_id)
            if not loan or loan["loan_type"] != "breakthrough":
                return ""
            
            # 计算应还金额
            import time
            now = int(time.time())
            days_borrowed = max(1, (now - loan["borrowed_at"]) // 86400)
            interest = int(loan["principal"] * loan["interest_rate"] * days_borrowed)
            total_due = loan["principal"] + interest
            
            # 检查玩家是否有足够灵石
            if player.gold >= total_due:
                # 自动扣款
                player.gold -= total_due
                await self.db.update_player(player)
                
                # 关闭贷款
                await self.db.ext.close_loan(loan["id"])
                
                # 记录流水
                bank_data = await self.db.ext.get_bank_account(player.user_id)
                balance = bank_data["balance"] if bank_data else 0
                await self.db.ext.add_bank_transaction(
                    player.user_id, "auto_repay", -total_due, balance,
                    f"突破成功自动还款：本金{loan['principal']:,}+利息{interest:,}", now
                )
                
                return (
                    f"💰 突破贷款自动还款成功！\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"已还本金：{loan['principal']:,} 灵石\n"
                    f"已还利息：{interest:,} 灵石\n"
                    f"当前持有：{player.gold:,} 灵石"
                )
            else:
                # 灵石不足，提醒玩家
                return (
                    f"⚠️ 你有未还清的突破贷款！\n"
                    f"应还金额：{total_due:,} 灵石\n"
                    f"当前持有：{player.gold:,} 灵石\n"
                    f"请尽快使用 /还款 命令还款"
                )
        except Exception as e:
            logger.warning(f"处理突破贷款自动还款异常: {e}")
            return ""
