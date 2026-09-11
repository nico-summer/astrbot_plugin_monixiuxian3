# core/pill_manager.py

import random
import time
from typing import Dict, List, Optional, Tuple
from astrbot.api import logger

from ..models import Player
from ..data import DataBase
from ..config_manager import ConfigManager


class PillManager:
    """丹药管理器 - 处理丹药效果、属性加成和限制机制"""

    def __init__(self, db: DataBase, config_manager: ConfigManager):
        self.db = db
        self.config_manager = config_manager

    def _ensure_non_negative_attributes(self, player: Player):
        """保证属性不为负，并同步能量上限约束"""
        attrs = [
            "lifespan",
            "experience",
            "physical_damage",
            "magic_damage",
            "physical_defense",
            "magic_defense",
            "mental_power",
            "spiritual_qi",
            "max_spiritual_qi",
            "blood_qi",
            "max_blood_qi",
        ]
        for attr in attrs:
            value = getattr(player, attr, 0)
            if value < 0:
                setattr(player, attr, 0)

        # 保证当前能量不超过上限
        if player.spiritual_qi > player.max_spiritual_qi:
            player.spiritual_qi = player.max_spiritual_qi
        if player.blood_qi > player.max_blood_qi:
            player.blood_qi = player.max_blood_qi

    def get_pill_by_name(self, pill_name: str) -> Optional[dict]:
        """根据名称获取丹药配置

        Args:
            pill_name: 丹药名称

        Returns:
            丹药配置字典，如果找不到返回None
        """
        # 尝试从破境丹中查找
        pill = self.config_manager.pills_data.get(pill_name)
        if pill:
            return pill

        # 尝试从修为丹中查找
        pill = self.config_manager.exp_pills_data.get(pill_name)
        if pill:
            return pill

        # 尝试从功能丹中查找
        pill = self.config_manager.utility_pills_data.get(pill_name)
        if pill:
            return pill

        # 兼容 items.json 中的旧版丹药（秘境/Boss 掉落会直接进入丹药背包）
        item_config = self.config_manager.items_data.get(pill_name)
        if item_config and item_config.get("type") == "丹药" and item_config.get("effect"):
            return item_config

        return None

    @staticmethod
    def format_effect_values(data: dict) -> List[str]:
        """按实际参与结算的字段生成效果说明。"""
        lines = []
        if data.get("subtype") == "exp":
            lines.append(f"修为 {data.get('exp_gain', 0):+d}")
        gain_labels = {
            "physical_damage_gain": "物伤", "magic_damage_gain": "法伤",
            "physical_defense_gain": "物防", "magic_defense_gain": "法防",
            "mental_power_gain": "精神力", "lifespan_gain": "寿命",
            "max_spiritual_qi_gain": "最大灵气", "max_blood_qi_gain": "最大气血",
        }
        for key, label in gain_labels.items():
            if data.get(key):
                lines.append(f"{label} {data[key]:+d}")
        multiplier_labels = {
            "cultivation_multiplier": "修炼速度",
            "physical_damage_multiplier": "物伤倍率",
            "magic_damage_multiplier": "法伤倍率",
            "physical_defense_multiplier": "物防倍率",
            "magic_defense_multiplier": "法防倍率",
            "breakthrough_bonus": "突破成功率",
            # 批次2：高阶丹药附加效果
            "crit_rate": "暴击率",
            "defense_multiplier": "防御倍率",
            "technique_exp_multiplier": "功法经验",
        }
        for key, label in multiplier_labels.items():
            if data.get(key):
                lines.append(f"{label} {data[key]:+.0%}")
        periodic_labels = {
            "lifespan_cost_per_minute": ("每分钟寿命", -1),
            "lifespan_regen_per_minute": ("每分钟寿命", 1),
            "spiritual_qi_regen_per_minute": ("每分钟灵气", 1),
            "blood_qi_regen_per_minute": ("每分钟气血", 1),
            "blood_qi_cost_per_minute": ("每分钟气血", -1),
        }
        for key, (label, sign) in periodic_labels.items():
            if data.get(key):
                lines.append(f"{label} {sign * data[key]:+d}")
        if data.get("subtype") == "resurrection":
            lines.append("抵消一次死亡，复活后属性减半")
        if data.get("blocks_next_debuff"):
            lines.append("抵消下一次负面效果")
        if "spiritual_qi_restore" in data:
            value = data["spiritual_qi_restore"]
            lines.append("灵气/气血恢复至满" if value == -1 else f"灵气/气血恢复 +{value}")
        if "blood_qi_restore" in data:
            value = data["blood_qi_restore"]
            lines.append("气血恢复至满" if value == -1 else f"气血恢复 +{value}")
        if data.get("resets_permanent_pills"):
            lines.append(f"重置永久丹药属性，并返还本丹药价格的 {data.get('reset_refund_ratio', 0.5):.0%}")
        if data.get("death_protection_multiplier"):
            lines.append(f"突破死亡概率降低 {1 - data['death_protection_multiplier']:.0%}")
        if data.get("base_attribute_limit_increase"):
            lines.append(f"永久属性丹药上限提高 {data['base_attribute_limit_increase']:.0%}")
        if data.get("is_random"):
            lines.append("随机一项攻防倍率 +500%，其余攻防倍率 -90%")
        if data.get("no_drop_on_death"):
            lines.append("PVP死亡不掉落装备")
        duration = data.get("duration_minutes", 0)
        if duration:
            lines.append(f"持续 {duration} 分钟")
        return lines

    def get_permanent_buff_lines(self, player: Player) -> List[str]:
        permanent_gains = player.get_permanent_pill_gains()
        if not permanent_gains:
            return []
        totals = {}
        sources = {}
        for gain in permanent_gains.values():
            if not isinstance(gain, dict):
                continue
            for key, value in gain.items():
                if key == "_sources" and isinstance(value, dict):
                    for name, count in value.items():
                        sources[name] = sources.get(name, 0) + count
                elif isinstance(value, (int, float)):
                    totals[key] = totals.get(key, 0) + value
        lines = ["来源：" + ("、".join(f"{name}×{count}" for name, count in sources.items()) if sources else "旧存档未记录具体丹药名称")]
        labels = {
            "physical_damage": "物伤", "magic_damage": "法伤",
            "physical_defense": "物防", "magic_defense": "法防",
            "mental_power": "精神力", "lifespan": "寿命",
            "max_spiritual_qi": "最大灵气", "max_blood_qi": "最大气血",
        }
        for key, label in labels.items():
            if totals.get(key):
                lines.append(f"{label} {totals[key]:+g}")
        if totals.get("cultivation_multiplier"):
            lines.append(f"修炼速度 {totals['cultivation_multiplier']:+.0%}")
        if totals.get("base_attribute_limit_increase"):
            lines.append(f"永久属性丹药上限 {totals['base_attribute_limit_increase']:+.0%}")
        death_multiplier = 1.0
        for gain in permanent_gains.values():
            if isinstance(gain, dict):
                death_multiplier *= gain.get("death_protection_multiplier", 1.0)
        if death_multiplier < 1.0:
            lines.append(f"突破死亡概率降低 {(1 - death_multiplier):.0%}")
        return lines

    # 永久丹药增益键 -> (玩家属性键, 展示名)
    PERMANENT_GAIN_ATTRS = {
        "physical_damage_gain": ("physical_damage", "物伤"),
        "magic_damage_gain": ("magic_damage", "法伤"),
        "physical_defense_gain": ("physical_defense", "物防"),
        "magic_defense_gain": ("magic_defense", "法防"),
        "mental_power_gain": ("mental_power", "精神力"),
        "lifespan_gain": ("lifespan", "寿命"),
        "max_spiritual_qi_gain": ("max_spiritual_qi", "最大灵气"),
        "max_blood_qi_gain": ("max_blood_qi", "最大气血"),
    }

    # 恢复类丹药的展示名
    ENERGY_LABELS = {"spiritual_qi": "灵气", "blood_qi": "气血"}

    def _get_limit_bonus(self, permanent_gains: dict) -> float:
        """永久属性丹药上限提升比例（洗髓丹/易筋丹等叠加）"""
        return sum(
            gain_data.get("base_attribute_limit_increase", 0)
            for gain_data in permanent_gains.values()
            if isinstance(gain_data, dict)
        )

    def get_permanent_pill_limit_lines(self, player: Player) -> List[str]:
        """当前境界永久属性丹药的上限进度（已获得/上限）"""
        permanent_gains = player.get_permanent_pill_gains()
        if not permanent_gains:
            return []

        level_gains = permanent_gains.get(f"level_{player.level_index}") or {}
        if not isinstance(level_gains, dict):
            return []

        base_attrs = self._get_base_attributes_for_level(player, player.level_index)
        limit_bonus = self._get_limit_bonus(permanent_gains)

        lines = []
        for _, (attr_key, attr_name) in self.PERMANENT_GAIN_ATTRS.items():
            current = level_gains.get(attr_key, 0)
            if not current:
                continue
            limit = base_attrs.get(attr_key, 100) * (0.3 + limit_bonus)
            ratio = current / limit if limit else 0
            lines.append(f"{attr_name} {current:g}/{limit:.0f}（{ratio:.0%}）")
        return lines

    def get_permanent_pill_limit(
        self, player: Player, pill_name: str, pill_data: dict
    ) -> Tuple[Optional[int], str]:
        """永久属性丹药还能服用几颗

        Returns:
            (剩余可用数量, 受限属性名)；None 表示该丹药不受属性上限限制
        """
        permanent_gains = player.get_permanent_pill_gains()
        level_gains = permanent_gains.get(f"level_{player.level_index}") or {}
        if not isinstance(level_gains, dict):
            level_gains = {}

        base_attrs = self._get_base_attributes_for_level(player, player.level_index)
        limit_bonus = self._get_limit_bonus(permanent_gains)

        remaining = None
        limit_attr = ""
        for gain_key, (attr_key, attr_name) in self.PERMANENT_GAIN_ATTRS.items():
            gain = pill_data.get(gain_key, 0)
            if not gain or gain <= 0:
                continue
            limit = base_attrs.get(attr_key, 100) * (0.3 + limit_bonus)
            current = level_gains.get(attr_key, 0)
            can_use = int(max(0, limit - current) // gain)
            if remaining is None or can_use < remaining:
                remaining = can_use
                limit_attr = attr_name
        return remaining, limit_attr

    def _has_active_effect(self, player: Player, pill_name: str) -> bool:
        """同名临时效果是否仍在生效"""
        current_time = int(time.time())
        for effect in player.get_active_pill_effects():
            if effect.get("pill_name") != pill_name:
                continue
            expiry_time = effect.get("expiry_time", 0)
            if not expiry_time or expiry_time > current_time:
                return True
        return False

    def _get_restore_info(self, player: Player, pill_data: dict) -> Optional[Tuple[str, int, int]]:
        """获取恢复类丹药的信息：(能量类型, 单颗恢复量, 缺口)"""
        keys = ("spiritual_qi_restore", "blood_qi_restore")
        for key in keys:
            if key not in pill_data:
                continue
            if key == "blood_qi_restore":
                energy_key, label = "blood_qi", "气血"
            elif player.cultivation_type == "体修":
                # 体修使用灵气恢复作为气血恢复（与使用逻辑保持一致）
                energy_key, label = "blood_qi", "气血"
            else:
                energy_key, label = "spiritual_qi", "灵气"

            current = getattr(player, energy_key, 0) or 0
            maximum = getattr(player, f"max_{energy_key}", 0) or 0
            missing = max(0, maximum - current)
            return label, pill_data[key], missing
        return None

    def get_consumption_limit(
        self, player: Player, pill_name: str, pill_data: dict
    ) -> Tuple[int, str]:
        """计算该丹药当前最多可服用数量及限制原因

        Returns:
            (可服用数量, 限制原因说明)
        """
        inventory = player.get_pills_inventory()
        owned = int(inventory.get(pill_name, 0) or 0)
        if owned <= 0:
            return 0, "背包中没有"

        if player.level_index < pill_data.get("required_level_index", 0):
            return 0, "境界不足"

        subtype = pill_data.get("subtype", "")
        effect_type = pill_data.get("effect_type", "instant")

        # 回生丹：效果不叠加
        if subtype == "resurrection":
            if player.has_resurrection_pill:
                return 0, "回生效果已生效"
            return 1, "效果不叠加，仅服1个"

        # 重置类丹药：多次服用没有收益
        if pill_data.get("resets_permanent_pills"):
            return 1, "重置类丹药仅服1个"

        # 定魂丹：护盾不叠加
        if pill_data.get("blocks_next_debuff"):
            if player.has_debuff_shield:
                return 0, "定魂护盾已生效"
            return 1, "护盾类丹药仅服1个"

        # 临时效果丹药：同名效果不叠加
        if effect_type == "temporary":
            if self._has_active_effect(player, pill_name):
                return 0, "同名效果未过期"
            return 1, "临时效果不叠加，仅服1个"

        # 永久属性丹药：受当前境界 30% 属性上限限制
        if effect_type == "permanent":
            remaining, limit_attr = self.get_permanent_pill_limit(player, pill_name, pill_data)
            if remaining is None:
                return owned, ""
            if remaining <= 0:
                return 0, f"{limit_attr}已达上限" if limit_attr else "属性已达上限"
            if remaining < owned:
                return remaining, f"受{limit_attr}上限限制"
            return owned, ""

        # 旧版 items.json 丹药：仅对“纯恢复气血/灵气”做缺口限制
        legacy_effects = pill_data.get("effect") or {}
        if pill_data.get("type") == "丹药" and legacy_effects:
            positive_keys = {
                key for key, value in legacy_effects.items()
                if isinstance(value, (int, float)) and value > 0
            }
            # 突破增益类为限时效果（1小时），一键服用时不重复叠加
            if "add_breakthrough_bonus" in legacy_effects:
                if self._has_active_effect(player, pill_name):
                    return 0, "同名效果未过期"
                return 1, "限时增益丹药，仅服1个"

            add_hp = legacy_effects.get("add_hp", 0)
            if add_hp > 0 and positive_keys == {"add_hp"}:
                label, current, maximum = self._get_energy_status(player)
                missing = max(0, maximum - current)
                if missing <= 0:
                    return 0, f"{label}已满"
                needed = -(-missing // add_hp)
                if needed < owned:
                    return needed, f"{label}缺口 {missing}，最多再服 {needed} 个"
            return owned, ""

        # 恢复类丹药：按缺口计算，避免溢出浪费
        restore_info = self._get_restore_info(player, pill_data)
        if restore_info and subtype == "instant_restore":
            label, restore, missing = restore_info
            if missing <= 0:
                return 0, f"{label}已满"
            if restore == -1:
                return 1, f"该丹药可直接补满{label}，仅服1个"
            needed = -(-missing // restore)  # 向上取整
            if needed < owned:
                return needed, f"{label}缺口 {missing}，最多再服 {needed} 个"
            return owned, ""

        # 其他丹药（修为丹等）不限量
        return owned, ""

    def get_pill_remaining_lines(self, player: Player, max_lines: int = 8) -> List[str]:
        """背包中丹药的可服用情况（还能吃几个 / 为什么不能吃）"""
        inventory = player.get_pills_inventory()
        lines: List[str] = []
        hidden = 0

        for pill_name, owned in sorted(inventory.items(), key=lambda kv: -int(kv[1] or 0)):
            owned = int(owned or 0)
            if owned <= 0:
                continue

            pill_data = self.get_pill_by_name(pill_name)
            if not pill_data:
                continue

            count, reason = self.get_consumption_limit(player, pill_name, pill_data)
            if count <= 0:
                lines.append(f"{pill_name}×{owned} → 暂不可服用（{reason}）")
            elif count >= owned:
                lines.append(f"{pill_name}×{owned} → 可服用 {owned} 个")
            else:
                suffix = f"（{reason}）" if reason else ""
                lines.append(f"{pill_name}×{owned} → 最多再服 {count} 个{suffix}")

        if max_lines and len(lines) > max_lines:
            hidden = len(lines) - max_lines
            lines = lines[:max_lines]
            lines.append(f"...另有 {hidden} 种丹药")

        return lines

    async def use_all_pills(self, player: Player) -> Tuple[bool, str]:
        """一键服用：按各类丹药的叠加与上限规则服用背包中的丹药"""
        await self.update_temporary_effects(player)

        inventory = dict(player.get_pills_inventory())
        used_lines: List[str] = []
        skipped_lines: List[str] = []
        total_used = 0

        for pill_name in sorted(inventory, key=lambda name: -int(inventory.get(name, 0) or 0)):
            owned = int(inventory.get(pill_name, 0) or 0)
            if owned <= 0:
                continue

            pill_data = self.get_pill_by_name(pill_name)
            if not pill_data:
                skipped_lines.append(f"{pill_name}（配置不存在）")
                continue

            if pill_data.get("manual_only"):
                skipped_lines.append(f"{pill_name}×{owned}（需手动使用）")
                continue

            count, reason = self.get_consumption_limit(player, pill_name, pill_data)
            if count <= 0:
                skipped_lines.append(f"{pill_name}×{owned}（{reason}）")
                continue

            try:
                success, message = await self.use_pill(player, pill_name, count)
            except Exception as exc:  # 单颗丹药异常不影响其它丹药
                logger.error(f"一键服用【{pill_name}】失败: {exc}")
                skipped_lines.append(f"{pill_name}×{owned}（服用异常）")
                continue

            if not success:
                skipped_lines.append(f"{pill_name}×{owned}（{message.lstrip('❌ ')}）")
                continue

            remaining = int(player.get_pills_inventory().get(pill_name, 0) or 0)
            used = owned - remaining
            total_used += used
            if used <= 0:
                skipped_lines.append(f"{pill_name}×{owned}（无可用增益）")
                continue

            if used < owned:
                suffix = f"（{reason}）" if reason else ""
                used_lines.append(f"{pill_name}×{used}{suffix}，背包剩余{remaining}")
            else:
                used_lines.append(f"{pill_name}×{used}")

        if total_used <= 0:
            detail = "\n".join(f"  • {line}" for line in skipped_lines)
            return False, (
                "❌ 没有可服用的丹药\n"
                + (f"━━━━━━━━━━━━━━━\n{detail}" if detail else "")
            ).strip()

        msg_parts = ["✨ 一键服用完成！", "━━━━━━━━━━━━━━━", "✅ 已服用："]
        msg_parts.extend(f"  • {line}" for line in used_lines)
        if skipped_lines:
            msg_parts.append("⚠️ 未能服用：")
            msg_parts.extend(f"  • {line}" for line in skipped_lines)
        msg_parts.append("━━━━━━━━━━━━━━━")
        msg_parts.append(f"共服用 {total_used} 个丹药")
        msg_parts.append("💡 使用「我的信息」可查看丹药上限进度")

        return True, "\n".join(msg_parts)

    def get_temporary_buff_lines(self, player: Player) -> List[str]:
        current_time = int(time.time())
        lines = []
        for effect in player.get_active_pill_effects():
            expiry_time = effect.get("expiry_time", 0)
            if expiry_time and expiry_time <= current_time:
                continue
            remaining = max(0, expiry_time - current_time)
            time_text = f"剩余{remaining // 3600}小时{remaining % 3600 // 60}分钟" if remaining >= 3600 else f"剩余{remaining // 60}分钟"
            values = [value for value in self.format_effect_values(effect) if not value.startswith("持续 ")]
            lines.append(f"{effect.get('pill_name', '未知效果')}：{'、'.join(values) or '特殊效果'}（{time_text}）")
        return lines

    async def update_temporary_effects(self, player: Player):
        """更新临时丹药效果，移除过期效果

        Args:
            player: 玩家对象
        """
        effects = player.get_active_pill_effects()
        current_time = int(time.time())
        updated_effects = []
        has_changes = False

        for effect in effects:
            if self._apply_periodic_effects(player, effect, current_time):
                has_changes = True

            expiry_time = effect.get("expiry_time", 0)
            if expiry_time <= 0 or current_time < expiry_time:
                updated_effects.append(effect)
            else:
                has_changes = True
                logger.info(f"玩家 {player.user_id} 的丹药效果 {effect.get('pill_name')} 已过期")

        if has_changes or len(updated_effects) != len(effects):
            player.set_active_pill_effects(updated_effects)
            await self.db.update_player(player)

    async def use_pill(
        self,
        player: Player,
        pill_name: str,
        count: int = 1
    ) -> Tuple[bool, str]:
        """使用丹药

        Args:
            player: 玩家对象
            pill_name: 丹药名称
            count: 使用数量（默认1）

        Returns:
            (是否成功, 消息)
        """
        # 检查数量
        if count <= 0:
            return False, "使用数量必须大于0！"

        # 检查背包是否有该丹药
        inventory = player.get_pills_inventory()
        if pill_name not in inventory or inventory[pill_name] <= 0:
            return False, f"你的背包中没有【{pill_name}】！"

        # 检查背包数量是否足够
        if inventory[pill_name] < count:
            return False, f"背包中【{pill_name}】数量不足！（拥有：{inventory[pill_name]}个，需要：{count}个）"

        # 获取丹药配置
        pill_data = self.get_pill_by_name(pill_name)
        if not pill_data:
            return False, f"丹药【{pill_name}】配置不存在！"

        # 特殊丹药（如还魂丹）必须手动使用，避免一键服用误消耗
        if pill_data.get("manual_only"):
            hint = "使用还魂丹" if pill_name == "还魂丹" else "手动使用"
            return False, f"【{pill_name}】需要手动使用（如：{hint}）"

        # 检查境界需求
        required_level = pill_data.get("required_level_index", 0)
        if player.level_index < required_level:
            # 根据玩家修炼类型获取对应境界名称
            level_data = self.config_manager.get_level_data(player.cultivation_type)
            level_name = f"境界{required_level}"
            if 0 <= required_level < len(level_data):
                level_name = level_data[required_level]["level_name"]
            return False, (
                f"境界不足！使用【{pill_name}】需要达到【{level_name}】"
            )

        # 旧版 items.json 丹药：使用 effect 字段结算
        if pill_data.get("type") == "丹药" and pill_data.get("effect"):
            return await self._use_legacy_item_pill(player, pill_name, pill_data, count)

        # 根据丹药类型处理
        effect_type = pill_data.get("effect_type", "instant")
        subtype = pill_data.get("subtype", "")

        if subtype == "exp":
            # 修为丹 - 支持批量
            return await self._use_exp_pill(player, pill_name, pill_data, count)
        elif subtype == "resurrection":
            # 回生丹 - 不支持批量（效果不叠加）
            if count > 1:
                return False, "回生丹效果不叠加，一次只能服用1个！"
            return await self._use_resurrection_pill(player, pill_name, pill_data)
        elif effect_type == "temporary":
            # 临时效果丹药 - 不支持批量（效果不叠加）
            if count > 1:
                return False, "临时效果丹药不支持批量服用（效果不叠加）！"
            return await self._use_temporary_pill(player, pill_name, pill_data)
        elif effect_type == "permanent":
            # 永久属性丹药 - 支持批量
            return await self._use_permanent_pill(player, pill_name, pill_data, count)
        elif effect_type == "instant":
            # 瞬间效果丹药 - 支持批量
            return await self._use_instant_pill(player, pill_name, pill_data, count)
        else:
            return False, f"未知的丹药类型：{effect_type}"

    def _get_energy_status(self, player: Player) -> Tuple[str, int, int]:
        """当前主要能量：(名称, 当前值, 上限)"""
        if player.cultivation_type == "体修":
            return "气血", player.blood_qi or 0, player.max_blood_qi or 0
        return "灵气", player.spiritual_qi or 0, player.max_spiritual_qi or 0

    async def _use_legacy_item_pill(
        self, player: Player, pill_name: str, pill_data: dict, count: int = 1
    ) -> Tuple[bool, str]:
        """服用 items.json 旧版丹药（丹药背包中的秘境/Boss 掉落丹药）"""
        success, message = await self.apply_legacy_pill_effects(player, pill_name, pill_data, count)
        if not success:
            return False, message

        inventory = player.get_pills_inventory()
        inventory[pill_name] -= count
        if inventory[pill_name] <= 0:
            del inventory[pill_name]
        player.set_pills_inventory(inventory)

        await self.db.update_player(player)
        return True, f"✨ {message}"

    async def apply_legacy_pill_effects(
        self, player: Player, pill_name: str, pill_data: dict, quantity: int = 1
    ) -> Tuple[bool, str]:
        """应用 items.json 旧版丹药效果（购买时与服用时共用同一套结算）"""
        effects = pill_data.get("effect") or {}
        if not effects:
            return False, f"丹药【{pill_name}】无效果配置。"

        effect_msgs = []

        for _ in range(max(1, quantity)):
            # 恢复/扣除气血（体修为气血，灵修为灵气）
            if "add_hp" in effects:
                hp_change = effects["add_hp"]
                if player.cultivation_type == "体修":
                    if player.max_blood_qi <= 0:
                        player.max_blood_qi = max(100, 50 + player.level_index * 20)
                    old_blood = player.blood_qi
                    player.blood_qi = max(0, min(player.max_blood_qi, player.blood_qi + hp_change))
                    if hp_change > 0:
                        actual_gain = player.blood_qi - old_blood
                        effect_msgs.append(f"气血+{actual_gain}" if actual_gain else "气血已满")
                    else:
                        effect_msgs.append(f"气血{hp_change}")
                else:
                    old_qi = player.spiritual_qi
                    player.spiritual_qi = max(0, min(player.max_spiritual_qi, player.spiritual_qi + hp_change))
                    if hp_change > 0:
                        actual_gain = player.spiritual_qi - old_qi
                        effect_msgs.append(f"灵气+{actual_gain}" if actual_gain else "灵气已满")
                    else:
                        effect_msgs.append(f"灵气{hp_change}")

            # 增加修为
            if "add_experience" in effects:
                exp_gain = effects["add_experience"]
                player.experience += exp_gain
                effect_msgs.append(f"修为+{exp_gain}")

            # 增加最大气血/灵气上限
            if "add_max_hp" in effects:
                max_hp_gain = effects["add_max_hp"]
                if player.cultivation_type == "体修":
                    player.max_blood_qi += max_hp_gain
                    effect_msgs.append(f"最大气血+{max_hp_gain}")
                else:
                    player.max_spiritual_qi += max_hp_gain
                    effect_msgs.append(f"最大灵气+{max_hp_gain}")

            # 增加灵力（映射到法伤）
            if "add_spiritual_power" in effects:
                sp_gain = effects["add_spiritual_power"]
                player.magic_damage += sp_gain
                effect_msgs.append(f"法伤+{sp_gain}")

            # 增加精神力
            if "add_mental_power" in effects:
                mp_gain = effects["add_mental_power"]
                player.mental_power += mp_gain
                effect_msgs.append(f"精神力+{mp_gain}")

            # 增加攻击力（映射到物伤）
            if "add_attack" in effects:
                atk_gain = effects["add_attack"]
                player.physical_damage += atk_gain
                effect_msgs.append(f"物伤+{atk_gain}" if atk_gain > 0 else f"物伤{atk_gain}")

            # 增加防御力（映射到物防）
            if "add_defense" in effects:
                def_gain = effects["add_defense"]
                player.physical_defense += def_gain
                effect_msgs.append(f"物防+{def_gain}" if def_gain > 0 else f"物防{def_gain}")

            # 增加/扣除灵石
            if "add_gold" in effects:
                gold_change = effects["add_gold"]
                player.gold += gold_change
                effect_msgs.append(f"灵石+{gold_change}" if gold_change > 0 else f"灵石{gold_change}")

            # 突破成功率加成（临时效果，持续1小时，可叠加）
            if "add_breakthrough_bonus" in effects:
                bonus = effects["add_breakthrough_bonus"]
                current_effects = player.get_active_pill_effects()
                current_effects.append({
                    "pill_name": pill_name,
                    "subtype": "breakthrough_boost",
                    "breakthrough_bonus": bonus,
                    "expiry_time": int(time.time()) + 3600,
                })
                player.set_active_pill_effects(current_effects)
                effect_msgs.append(f"突破成功率+{int(bonus * 100)}%(1小时)")

        # 确保属性不为负
        self._ensure_non_negative_attributes(player)
        player.physical_damage = max(0, player.physical_damage)
        player.magic_damage = max(0, player.magic_damage)
        player.physical_defense = max(0, player.physical_defense)
        player.magic_defense = max(0, player.magic_defense)
        player.mental_power = max(0, player.mental_power)
        player.spiritual_qi = min(player.spiritual_qi, player.max_spiritual_qi)
        player.blood_qi = min(player.blood_qi, player.max_blood_qi)

        await self.db.update_player(player)

        unique_effects = list(dict.fromkeys(effect_msgs))
        effects_str = "、".join(unique_effects[:5])
        if len(unique_effects) > 5:
            effects_str += "..."

        qty_str = f"x{quantity}" if quantity > 1 else ""
        return True, f"服用【{pill_name}】{qty_str}成功！效果：{effects_str}"

    async def _use_exp_pill(self, player: Player, pill_name: str, pill_data: dict, count: int = 1) -> Tuple[bool, str]:
        """使用修为丹（支持批量）"""
        exp_gain_per_pill = pill_data.get("exp_gain", 0)
        total_exp_gain = exp_gain_per_pill * count

        player.experience += total_exp_gain

        # 扣除丹药
        inventory = player.get_pills_inventory()
        inventory[pill_name] -= count
        if inventory[pill_name] <= 0:
            del inventory[pill_name]
        player.set_pills_inventory(inventory)

        await self.db.update_player(player)

        if count > 1:
            return True, (
                f"✨ 服用【{pill_name}】×{count} 成功！\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📈 单个修为：{exp_gain_per_pill}\n"
                f"📈 总计修为：{total_exp_gain:,}\n"
                f"💫 当前修为：{player.experience:,}\n"
                f"━━━━━━━━━━━━━━━"
            )
        else:
            return True, (
                f"✨ 服用【{pill_name}】成功！\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📈 获得修为：{total_exp_gain}\n"
                f"💫 当前修为：{player.experience}\n"
                f"━━━━━━━━━━━━━━━"
            )

    async def _use_resurrection_pill(self, player: Player, pill_name: str, pill_data: dict) -> Tuple[bool, str]:
        """使用回生丹"""
        if player.has_resurrection_pill:
            return False, "你已经拥有回生丹效果，无需重复使用！"

        player.has_resurrection_pill = True

        # 扣除丹药
        inventory = player.get_pills_inventory()
        inventory[pill_name] -= 1
        if inventory[pill_name] <= 0:
            del inventory[pill_name]
        player.set_pills_inventory(inventory)

        await self.db.update_player(player)

        return True, (
            f"✨ 服用【{pill_name}】成功！\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🛡️ 你获得了起死回生的能力\n"
            f"下次死亡时将自动复活\n"
            f"（复活后所有属性减半）\n"
            f"━━━━━━━━━━━━━━━"
        )

    async def _use_temporary_pill(self, player: Player, pill_name: str, pill_data: dict) -> Tuple[bool, str]:
        """使用临时效果丹药"""
        duration_minutes = pill_data.get("duration_minutes", 60)
        current_time = int(time.time())
        expiry_time = current_time + duration_minutes * 60

        # 创建效果记录
        effect = {
            "pill_name": pill_name,
            "pill_id": pill_data.get("id", ""),
            "subtype": pill_data.get("subtype", ""),
            "start_time": current_time,
            "expiry_time": expiry_time,
            "duration_minutes": duration_minutes,
            "last_tick_time": current_time,
        }

        if pill_data.get("is_random"):
            random_key = random.choice([
                "physical_damage_multiplier", "magic_damage_multiplier",
                "physical_defense_multiplier", "magic_defense_multiplier",
            ])
            for key in [
                "physical_damage_multiplier", "magic_damage_multiplier",
                "physical_defense_multiplier", "magic_defense_multiplier",
            ]:
                effect[key] = 5.0 if key == random_key else -0.9

        # 添加具体效果数据
        effect_keys = [
            "cultivation_multiplier", "physical_damage_multiplier", "magic_damage_multiplier",
            "physical_defense_multiplier", "magic_defense_multiplier",
            "lifespan_cost_per_minute", "lifespan_regen_per_minute",
            "spiritual_qi_regen_per_minute", "blood_qi_regen_per_minute", "blood_qi_cost_per_minute",
            "breakthrough_bonus",
            # 批次2：高阶丹药附加效果（暴击 / 防御倍率 / 功法经验 / 死亡保护）
            "crit_rate", "defense_multiplier", "technique_exp_multiplier", "no_drop_on_death",
        ]
        for key in effect_keys:
            if key in pill_data:
                effect[key] = pill_data[key]

        # 添加到活跃效果
        effects = player.get_active_pill_effects()
        effects.append(effect)
        player.set_active_pill_effects(effects)

        # 扣除丹药
        inventory = player.get_pills_inventory()
        inventory[pill_name] -= 1
        if inventory[pill_name] <= 0:
            del inventory[pill_name]
        player.set_pills_inventory(inventory)

        await self.db.update_player(player)

        # 使用实际写入玩家状态的效果生成提示，随机丹药会展示本次抽取结果
        effect_desc = [value for value in self.format_effect_values(effect) if not value.startswith("持续 ")]
        effects_str = "、".join(effect_desc) if effect_desc else "特殊效果"

        return True, (
            f"✨ 服用【{pill_name}】成功！\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⏱️ 持续时间：{duration_minutes}分钟\n"
            f"🎯 效果：{effects_str}\n"
            f"━━━━━━━━━━━━━━━"
        )

    async def _use_permanent_pill(self, player: Player, pill_name: str, pill_data: dict, count: int = 1) -> Tuple[bool, str]:
        """使用永久属性丹药（支持批量）"""
        # 检查境界限制（30%上限）
        permanent_gains = player.get_permanent_pill_gains()
        level_key = f"level_{player.level_index}"

        if level_key not in permanent_gains:
            permanent_gains[level_key] = {
                "physical_damage": 0,
                "magic_damage": 0,
                "physical_defense": 0,
                "magic_defense": 0,
                "mental_power": 0,
                "lifespan": 0,
                "max_spiritual_qi": 0,
                "max_blood_qi": 0,
            }

        # 计算基础属性（当前境界突破时获得的属性）
        base_attrs = self._get_base_attributes_for_level(player, player.level_index)

        # 检查各项属性是否已达上限
        attr_mapping = {
            "physical_damage_gain": ("physical_damage", "物伤"),
            "magic_damage_gain": ("magic_damage", "法伤"),
            "physical_defense_gain": ("physical_defense", "物防"),
            "magic_defense_gain": ("magic_defense", "法防"),
            "mental_power_gain": ("mental_power", "精神力"),
            "lifespan_gain": ("lifespan", "寿命"),
            "max_spiritual_qi_gain": ("max_spiritual_qi", "最大灵气"),
            "max_blood_qi_gain": ("max_blood_qi", "最大气血"),
        }

        gains_applied = {}
        gains_blocked = {}
        pills_used = 0

        # 循环批量使用，直到用完或达到上限
        for i in range(count):
            has_any_gain = False

            for gain_key, (attr_key, attr_name) in attr_mapping.items():
                if gain_key not in pill_data:
                    continue

                gain = pill_data[gain_key]
                if gain == 0:
                    continue

                # 只有正向增益才受30%限制
                if gain > 0:
                    current_gain = permanent_gains[level_key].get(attr_key, 0)
                    base_value = base_attrs.get(attr_key, 100)  # 默认基础值100
                    limit_bonus = sum(
                        gain_data.get("base_attribute_limit_increase", 0)
                        for gain_data in permanent_gains.values()
                        if isinstance(gain_data, dict)
                    )
                    limit = base_value * (0.3 + limit_bonus)

                    if current_gain >= limit:
                        if attr_name not in gains_blocked:
                            gains_blocked[attr_name] = f"已达上限({limit:.0f})"
                        continue

                    # 计算实际可以增加的值
                    actual_gain = min(gain, limit - current_gain)
                    if actual_gain > 0:
                        has_any_gain = True
                        if actual_gain < gain and attr_name not in gains_blocked:
                            gains_blocked[attr_name] = f"部分受限"

                        # 应用增益
                        permanent_gains[level_key][attr_key] += actual_gain
                        setattr(player, attr_key, getattr(player, attr_key) + int(actual_gain))
                        gains_applied[attr_name] = gains_applied.get(attr_name, 0) + int(actual_gain)
                else:
                    # 负向效果直接应用
                    has_any_gain = True
                    permanent_gains[level_key][attr_key] += gain
                    setattr(player, attr_key, getattr(player, attr_key) + int(gain))
                    gains_applied[attr_name] = gains_applied.get(attr_name, 0) + int(gain)

            # 处理修炼倍率（永久）
            if "cultivation_multiplier" in pill_data:
                has_any_gain = True
                cult_mult = pill_data["cultivation_multiplier"]
                if "cultivation_multiplier" not in permanent_gains[level_key]:
                    permanent_gains[level_key]["cultivation_multiplier"] = 0
                permanent_gains[level_key]["cultivation_multiplier"] += cult_mult
                if "修炼速度" not in gains_applied:
                    gains_applied["修炼速度"] = 0
                gains_applied["修炼速度"] += cult_mult

            if "base_attribute_limit_increase" in pill_data:
                has_any_gain = True
                limit_increase = pill_data["base_attribute_limit_increase"]
                permanent_gains[level_key]["base_attribute_limit_increase"] = (
                    permanent_gains[level_key].get("base_attribute_limit_increase", 0) + limit_increase
                )
                if "永久属性丹药上限" not in gains_applied:
                    gains_applied["永久属性丹药上限"] = 0
                gains_applied["永久属性丹药上限"] += limit_increase

            # 处理突破死亡概率降低
            if "death_protection_multiplier" in pill_data:
                has_any_gain = True
                death_mult = pill_data["death_protection_multiplier"]
                if "death_protection_multiplier" not in permanent_gains[level_key]:
                    permanent_gains[level_key]["death_protection_multiplier"] = 1.0
                permanent_gains[level_key]["death_protection_multiplier"] *= death_mult
                gains_applied["突破死亡概率"] = f"降低{(1 - permanent_gains[level_key]['death_protection_multiplier']) * 100:.0f}%"

            if not has_any_gain:
                # 所有属性都达到上限，停止使用
                break

            pills_used += 1

        if pills_used == 0:
            return False, "该丹药的所有属性增益都已达到上限，无法使用！"

        sources = permanent_gains[level_key].setdefault("_sources", {})
        sources[pill_name] = sources.get(pill_name, 0) + pills_used

        # 修正属性下限与能量上限
        self._ensure_non_negative_attributes(player)

        # 更新玩家数据
        player.set_permanent_pill_gains(permanent_gains)

        # 扣除丹药
        inventory = player.get_pills_inventory()
        inventory[pill_name] -= pills_used
        if inventory[pill_name] <= 0:
            del inventory[pill_name]
        player.set_pills_inventory(inventory)

        await self.db.update_player(player)

        # 构建消息
        msg_parts = []
        if pills_used > 1:
            msg_parts.append(f"✨ 服用【{pill_name}】×{pills_used} 成功！")
        else:
            msg_parts.append(f"✨ 服用【{pill_name}】成功！")

        msg_parts.append("━━━━━━━━━━━━━━━")
        msg_parts.append("💪 永久增益：")

        for attr_name, value in gains_applied.items():
            if attr_name == "修炼速度":
                msg_parts.append(f"  {attr_name} {value:+.0%}")
            elif attr_name == "永久属性丹药上限":
                msg_parts.append(f"  {attr_name} {value:+.0%}")
            elif isinstance(value, int):
                msg_parts.append(f"  {attr_name} +{value}")
            else:
                msg_parts.append(f"  {attr_name} {value}")

        if gains_blocked:
            msg_parts.append("\n⚠️ 受限提示：")
            for attr_name, reason in gains_blocked.items():
                msg_parts.append(f"  {attr_name} {reason}")

        if pills_used < count:
            msg_parts.append(f"\nℹ️ 使用了 {pills_used}/{count} 个（剩余已达上限）")

        msg_parts.append("━━━━━━━━━━━━━━━")
        limit_bonus = sum(
            gain_data.get("base_attribute_limit_increase", 0)
            for gain_data in permanent_gains.values()
            if isinstance(gain_data, dict)
        )
        msg_parts.append(f"注：当前境界永久基础属性增益上限为基础值的 {30 + limit_bonus * 100:.0f}%")

        return True, "\n".join(msg_parts)

    async def _use_instant_pill(self, player: Player, pill_name: str, pill_data: dict, count: int = 1) -> Tuple[bool, str]:
        """使用瞬间效果丹药（支持批量）"""
        msg_parts = []
        if count > 1:
            msg_parts.append(f"✨ 服用【{pill_name}】×{count} 成功！")
        else:
            msg_parts.append(f"✨ 服用【{pill_name}】成功！")
        msg_parts.append("━━━━━━━━━━━━━━━")

        # 恢复能量（灵气/气血） - 批量累加
        energy_restore = None
        energy_label = "灵气"
        current_energy = player.spiritual_qi
        max_energy = player.max_spiritual_qi

        # 体修优先使用专属气血恢复键；若无则复用灵气恢复作为气血恢复
        if player.cultivation_type == "体修" and "blood_qi_restore" in pill_data:
            energy_restore = pill_data["blood_qi_restore"]
            energy_label = "气血"
            current_energy = player.blood_qi
            max_energy = player.max_blood_qi
        elif "spiritual_qi_restore" in pill_data:
            energy_restore = pill_data["spiritual_qi_restore"]
            if player.cultivation_type == "体修":
                energy_label = "气血"
                current_energy = player.blood_qi
                max_energy = player.max_blood_qi

        if energy_restore is not None:
            if energy_restore == -1:
                # 恢复至满（无论多少个都是满）
                old_energy = current_energy
                current_energy = max_energy
                actual_restore = max_energy - old_energy
            else:
                # 批量恢复
                old_energy = current_energy
                total_restore = energy_restore * count
                current_energy = min(current_energy + total_restore, max_energy)
                actual_restore = current_energy - old_energy

            if energy_label == "气血":
                player.blood_qi = current_energy
                if count > 1:
                    msg_parts.append(f"🌟 恢复气血：+{actual_restore}（单个{energy_restore}×{count}）")
                else:
                    msg_parts.append(f"🌟 恢复气血：+{actual_restore}")
                msg_parts.append(f"🩸 当前气血：{player.blood_qi}/{player.max_blood_qi}")
            else:
                player.spiritual_qi = current_energy
                if count > 1:
                    msg_parts.append(f"🌟 恢复灵气：+{actual_restore}（单个{energy_restore}×{count}）")
                else:
                    msg_parts.append(f"🌟 恢复灵气：+{actual_restore}")
                msg_parts.append(f"💫 当前灵气：{player.spiritual_qi}/{player.max_spiritual_qi}")

        # 重置永久丹药增益（只执行一次，不受数量影响）
        if pill_data.get("resets_permanent_pills"):
            reset_applied = self._reset_permanent_pill_effects(player)
            if reset_applied:
                msg_parts.append("🔄 已重置所有永久属性丹药增益")
                refund_ratio = pill_data.get("reset_refund_ratio", 0.5)
                refund = int(pill_data.get("price", 0) * refund_ratio)
                if refund > 0:
                    player.gold += refund
                    msg_parts.append(f"💰 返还灵石：{refund}")
            else:
                msg_parts.append("ℹ️ 当前没有可重置的永久增益")

        # 定魂丹 - 下一次负面效果免疫（只执行一次，不受数量影响）
        if pill_data.get("blocks_next_debuff"):
            if player.has_debuff_shield:
                msg_parts.append("🛡️ 定魂护盾已存在，无需重复使用")
            else:
                player.has_debuff_shield = True
                msg_parts.append("🛡️ 获得定魂护盾：下一次负面效果将被抵消")

        # 扣除丹药
        inventory = player.get_pills_inventory()
        inventory[pill_name] -= count
        if inventory[pill_name] <= 0:
            del inventory[pill_name]
        player.set_pills_inventory(inventory)

        await self.db.update_player(player)

        msg_parts.append("━━━━━━━━━━━━━━━")
        return True, "\n".join(msg_parts)

    def _get_base_attributes_for_level(self, player: Player, level_index: int) -> dict:
        """获取当前境界的基础属性（用于计算30%上限）

        Args:
            player: 玩家对象，用于确定修炼类型
            level_index: 境界索引

        Returns:
            基础属性字典
        """
        level_data = self.config_manager.get_level_data(player.cultivation_type)
        # 兜底：如果数据为空，使用灵修配置避免索引错误
        if not level_data:
            level_data = self.config_manager.level_data

        # 越界保护
        if level_data:
            level_index = min(level_index, len(level_data) - 1)
            level_config = level_data[level_index]
        else:
            level_config = {}

        return {
            "physical_damage": level_config.get("breakthrough_physical_damage_gain", 10),
            "magic_damage": level_config.get("breakthrough_magic_damage_gain", 10),
            "physical_defense": level_config.get("breakthrough_physical_defense_gain", 5),
            "magic_defense": level_config.get("breakthrough_magic_defense_gain", 5),
            "mental_power": level_config.get("breakthrough_mental_power_gain", 100),
            "lifespan": level_config.get("breakthrough_lifespan_gain", 100),
            "max_spiritual_qi": level_config.get("breakthrough_spiritual_qi_gain", 100),
            "max_blood_qi": level_config.get("breakthrough_blood_qi_gain", 100),
        }

    async def handle_resurrection(self, player: Player) -> bool:
        """处理玩家死亡时的回生丹效果

        Args:
            player: 玩家对象

        Returns:
            是否成功复活
        """
        if not player.has_resurrection_pill:
            return False

        logger.info(f"玩家 {player.user_id} 触发回生丹效果")

        # 消耗回生丹效果
        player.has_resurrection_pill = False

        # 所有属性减半
        player.lifespan = player.lifespan // 2
        player.experience = player.experience // 2
        player.physical_damage = player.physical_damage // 2
        player.magic_damage = player.magic_damage // 2
        player.physical_defense = player.physical_defense // 2
        player.magic_defense = player.magic_defense // 2
        player.mental_power = player.mental_power // 2
        player.max_spiritual_qi = player.max_spiritual_qi // 2
        player.spiritual_qi = player.max_spiritual_qi // 2
        player.max_blood_qi = player.max_blood_qi // 2
        player.blood_qi = player.max_blood_qi // 2

        self._ensure_non_negative_attributes(player)

        await self.db.update_player(player)
        return True

    def calculate_pill_attribute_effects(self, player: Player) -> dict:
        """计算丹药对属性的影响（乘法加成）

        Args:
            player: 玩家对象

        Returns:
            属性乘法倍率字典
        """
        effects = player.get_active_pill_effects()
        current_time = int(time.time())
        multipliers = {
            "physical_damage": 1.0,
            "magic_damage": 1.0,
            "physical_defense": 1.0,
            "magic_defense": 1.0,
            "cultivation_speed": 1.0,
        }

        # 累加临时效果
        for effect in effects:
            expiry_time = effect.get("expiry_time", 0)
            if expiry_time > 0 and current_time >= expiry_time:
                continue
            if "physical_damage_multiplier" in effect:
                multipliers["physical_damage"] += effect["physical_damage_multiplier"]
            if "magic_damage_multiplier" in effect:
                multipliers["magic_damage"] += effect["magic_damage_multiplier"]
            if "physical_defense_multiplier" in effect:
                multipliers["physical_defense"] += effect["physical_defense_multiplier"]
            if "magic_defense_multiplier" in effect:
                multipliers["magic_defense"] += effect["magic_defense_multiplier"]
            if "cultivation_multiplier" in effect:
                multipliers["cultivation_speed"] += effect["cultivation_multiplier"]

        # 累加永久效果
        permanent_gains = player.get_permanent_pill_gains()
        # 永久增益跨境界持续生效；level_key 仅用于兼容旧数据及属性上限记录
        for level_gains in permanent_gains.values():
            if isinstance(level_gains, dict) and "cultivation_multiplier" in level_gains:
                multipliers["cultivation_speed"] += level_gains["cultivation_multiplier"]

        # 确保倍率不为负
        for key in multipliers:
            multipliers[key] = max(0.0, multipliers[key])

        return multipliers

    def get_breakthrough_modifiers(self, player: Player) -> dict:
        """获取突破时的临时与永久加成信息"""
        effects = player.get_active_pill_effects()
        current_time = int(time.time())
        temp_bonus = 0.0
        has_temp_effects = False

        for effect in effects:
            expiry_time = effect.get("expiry_time", 0)
            if expiry_time > 0 and current_time >= expiry_time:
                continue

            subtype = effect.get("subtype", "")
            if subtype in {"breakthrough_boost", "breakthrough_debuff"}:
                temp_bonus += effect.get("breakthrough_bonus", 0)
                has_temp_effects = True

        permanent_multiplier = 1.0
        permanent_gains = player.get_permanent_pill_gains()
        for level_gain in permanent_gains.values():
            permanent_multiplier *= level_gain.get("death_protection_multiplier", 1.0)

        return {
            "temp_bonus": temp_bonus,
            "has_temp_effects": has_temp_effects,
            "permanent_death_multiplier": max(0.0, min(1.0, permanent_multiplier)),
        }

    async def consume_breakthrough_effects(self, player: Player):
        """突破完成后移除相关临时丹药效果"""
        effects = player.get_active_pill_effects()
        remaining_effects = [
            effect for effect in effects
            if effect.get("subtype", "") not in {"breakthrough_boost", "breakthrough_debuff"}
        ]

        if len(remaining_effects) != len(effects):
            player.set_active_pill_effects(remaining_effects)
            await self.db.update_player(player)

    async def add_pill_to_inventory(self, player: Player, pill_name: str, count: int = 1):
        """添加丹药到背包

        Args:
            player: 玩家对象
            pill_name: 丹药名称
            count: 数量
        """
        inventory = player.get_pills_inventory()
        if pill_name in inventory:
            inventory[pill_name] += count
        else:
            inventory[pill_name] = count
        player.set_pills_inventory(inventory)
        await self.db.update_player(player)

    def get_pill_inventory_display(self, player: Player) -> str:
        """获取丹药背包显示文本

        Args:
            player: 玩家对象

        Returns:
            丹药背包的格式化文本
        """
        inventory = player.get_pills_inventory()
        if not inventory:
            return "你的丹药背包是空的！"

        lines = ["--- 丹药背包 ---"]
        for pill_name, count in inventory.items():
            pill_data = self.get_pill_by_name(pill_name)
            if pill_data:
                rank = pill_data.get("rank", "未知")
                lines.append(f"[{rank}] {pill_name} × {count}")
            else:
                lines.append(f"{pill_name} × {count}")

        lines.append("-" * 20)
        return "\n".join(lines)

    def _apply_periodic_effects(self, player: Player, effect: dict, current_time: int) -> bool:
        """根据时间自动结算持续恢复/扣减"""
        expiry_time = effect.get("expiry_time", 0)
        tick_limit = min(current_time, expiry_time) if expiry_time > 0 else current_time
        last_tick = effect.get("last_tick_time", effect.get("start_time", current_time))

        if tick_limit <= last_tick:
            return False

        elapsed_seconds = tick_limit - last_tick
        minutes = elapsed_seconds // 60
        if minutes <= 0:
            return False

        effect["last_tick_time"] = last_tick + minutes * 60
        changed = False

        if "lifespan_cost_per_minute" in effect:
            total_cost = effect["lifespan_cost_per_minute"] * minutes
            player.lifespan = max(0, player.lifespan - total_cost)
            changed = True

        if "lifespan_regen_per_minute" in effect:
            total_regen = effect["lifespan_regen_per_minute"] * minutes
            player.lifespan += total_regen
            changed = True

        if "spiritual_qi_regen_per_minute" in effect:
            total_qi = effect["spiritual_qi_regen_per_minute"] * minutes
            player.spiritual_qi = min(player.max_spiritual_qi, player.spiritual_qi + total_qi)
            changed = True

        if "blood_qi_regen_per_minute" in effect:
            total_blood = effect["blood_qi_regen_per_minute"] * minutes
            player.blood_qi = min(player.max_blood_qi, player.blood_qi + total_blood)
            changed = True

        if "blood_qi_cost_per_minute" in effect:
            total_cost = effect["blood_qi_cost_per_minute"] * minutes
            player.blood_qi = max(0, player.blood_qi - total_cost)
            changed = True

        if changed:
            self._ensure_non_negative_attributes(player)

        return changed

    def _reset_permanent_pill_effects(self, player: Player) -> bool:
        """清空永久丹药增益并回退属性"""
        permanent_gains = player.get_permanent_pill_gains()
        if not permanent_gains:
            return False

        attr_keys = [
            "physical_damage",
            "magic_damage",
            "physical_defense",
            "magic_defense",
            "mental_power",
            "lifespan",
            "max_spiritual_qi",
            "max_blood_qi",
        ]

        changed = False
        for gain in permanent_gains.values():
            for attr_key in attr_keys:
                value = gain.get(attr_key, 0)
                if value:
                    delta = int(value)
                    setattr(player, attr_key, getattr(player, attr_key) - delta)
                    changed = True

            if "cultivation_multiplier" in gain:
                changed = changed or bool(gain["cultivation_multiplier"])
                gain["cultivation_multiplier"] = 0
            if "death_protection_multiplier" in gain:
                changed = changed or gain["death_protection_multiplier"] != 1.0
                gain["death_protection_multiplier"] = 1.0
            if "base_attribute_limit_increase" in gain:
                changed = changed or bool(gain["base_attribute_limit_increase"])
                gain["base_attribute_limit_increase"] = 0

        player.set_permanent_pill_gains({})
        return changed
