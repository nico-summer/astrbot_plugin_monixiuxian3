# core/death_manager.py
"""死亡 / 陨落统一处理（批次1 死亡机制，批次4 世界事件复用）

规则（参数可在两处调整）：
1. ``config/death_config.json``（随插件分发的默认配置文件）
2. AstrBot 插件配置面板「死亡与复活配置」（``_conf_schema.json`` 的 DEATH_CONFIG 分组）

优先级：内置默认值 < 配置文件 < 插件配置面板。

- 练气期-筑基期（境界 < 13）：首次死亡触发「劫后重生」——境界倒退 1 级、
  保留部分修为、状态清空；第二次死亡彻底陨落（删除角色及关联数据）
- 金丹期及以上（境界 >= 13）：死亡进入【元神状态】，不会删号，等待复活

批次1 的「突破走火入魔」、批次4 的「世界事件阵亡」与复活系统共用这里的配置解析，
避免多套死亡参数各自漂移。
"""

import time
from dataclasses import dataclass
from typing import Optional

from astrbot.api import logger

from ..config_manager import ConfigManager
from ..data import DataBase
from ..models import Player

__all__ = [
    "DeathManager",
    "DeathOutcome",
    "SOUL_STATE_LEVEL_INDEX",
    "DEFAULT_DEATH_CONFIG",
    "resolve_death_config",
]

# 金丹期及以上（level_index >= 13）死亡进入元神状态
SOUL_STATE_LEVEL_INDEX = 13

# 内核默认值（与 config/death_config.json、_conf_schema.json 保持一致）
DEFAULT_DEATH_CONFIG = {
    "soul_revival_hours": 24,  # 元神复活等待时间（小时），-1表示永久等待
    "soul_exp_loss_rate": 0.05,  # 死亡时立即损失的修为比例（0.05=损失5%）
    "rebirth_exp_keep_rate": 0.7,  # 劫后重生保留修为比例（0.7=保留70%）
}

# AstrBot 插件配置（_conf_schema.json）中的死亡配置分组名
PLUGIN_CONFIG_GROUP = "DEATH_CONFIG"
# 插件配置面板键名 -> 内部配置键名（面板沿用插件配置的大写风格）
PLUGIN_KEY_MAP = {
    "SOUL_REVIVAL_HOURS": "soul_revival_hours",
    "SOUL_EXP_LOSS_RATE": "soul_exp_loss_rate",
    "REBIRTH_EXP_KEEP_RATE": "rebirth_exp_keep_rate",
}
# 兼容旧写法：顶层直接放 death_config
LEGACY_PLUGIN_CONFIG_KEY = "death_config"


def _extract_plugin_death_config(plugin_config) -> dict:
    """从 AstrBot 插件配置对象中提取死亡配置（兼容多种存放方式）

    支持：
    1. 插件配置面板分组 ``DEATH_CONFIG``（推荐，见 _conf_schema.json，键名为大写）
    2. 顶层 ``death_config``（旧写法，键名为 snake_case）
    3. 配置项直接写在插件配置顶层（大写或 snake_case 均可）
    """
    if plugin_config is None or not hasattr(plugin_config, "get"):
        return {}

    values: dict = {}

    legacy = None
    try:
        legacy = plugin_config.get(LEGACY_PLUGIN_CONFIG_KEY, None)
    except Exception:
        legacy = None
    if isinstance(legacy, dict):
        for key, value in legacy.items():
            internal = PLUGIN_KEY_MAP.get(key, key)
            if internal in DEFAULT_DEATH_CONFIG and value is not None:
                values[internal] = value

    group = None
    try:
        group = plugin_config.get(PLUGIN_CONFIG_GROUP, None)
    except Exception:
        group = None
    if not isinstance(group, dict):
        # 兼容直接把配置项写在插件配置顶层的旧配置
        group = plugin_config

    for key, value in (group or {}).items():
        if value is None:
            continue
        internal = PLUGIN_KEY_MAP.get(key, key)
        if internal in DEFAULT_DEATH_CONFIG:
            values[internal] = value

    return values


def resolve_death_config(config_manager=None, plugin_config=None) -> dict:
    """合并死亡系统配置，返回扁平字典（优先级从低到高）

    1. 内置默认值 ``DEFAULT_DEATH_CONFIG``
    2. ``config/death_config.json``
    3. AstrBot 插件配置面板 ``DEATH_CONFIG``（后台可视化调整，优先级最高）

    突破死亡、世界事件阵亡、复活系统都通过本函数读取配置，
    保证「插件配置面板 / 配置文件」两条路径行为一致。
    """
    resolved = dict(DEFAULT_DEATH_CONFIG)

    getter = getattr(config_manager, "get_death_config", None)
    if callable(getter):
        try:
            file_config = getter() or {}
            if isinstance(file_config, dict):
                resolved.update({k: v for k, v in file_config.items() if v is not None})
        except Exception as e:
            logger.warning(f"读取死亡系统配置失败，使用默认值: {e}")

    plugin_values = _extract_plugin_death_config(plugin_config)
    if plugin_values:
        resolved.update(plugin_values)

    return resolved


@dataclass
class DeathOutcome:
    """一次死亡结算的结果"""

    kind: str  # rebirth（劫后重生）/ permanent（彻底陨落）/ soul（元神状态）
    old_level_index: int = 0
    new_level_index: int = 0
    keep_rate: float = 1.0
    revival_hours: float = DEFAULT_DEATH_CONFIG["soul_revival_hours"]
    exp_loss_rate: float = DEFAULT_DEATH_CONFIG["soul_exp_loss_rate"]

    @property
    def is_rebirth(self) -> bool:
        """是否触发了劫后重生"""
        return self.kind == "rebirth"

    @property
    def is_soul(self) -> bool:
        """是否进入元神状态"""
        return self.kind == "soul"

    @property
    def is_permanent(self) -> bool:
        """是否彻底陨落（角色数据已删除）"""
        return self.kind == "permanent"

    @property
    def level_changed(self) -> bool:
        """境界是否发生变化"""
        return self.old_level_index != self.new_level_index


class DeathManager:
    """死亡结算管理器"""

    def __init__(
        self,
        db: DataBase,
        config_manager: Optional[ConfigManager] = None,
        config: dict = None,
    ):
        self.db = db
        self.config_manager = config_manager
        self.config = config or {}

    # ===== 配置 =====

    def get_death_config(self) -> dict:
        """获取死亡系统配置（内置默认值 < death_config.json < AstrBot 插件配置）"""
        return resolve_death_config(self.config_manager, self.config)

    @staticmethod
    def _to_float(value, default: float) -> float:
        """安全转换为 float，非法值回退默认值"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp_rate(value: float) -> float:
        """把比例限制在 0~1"""
        return max(0.0, min(1.0, value))

    # ===== 死亡结算 =====

    async def apply_death(self, player: Player) -> DeathOutcome:
        """按境界对玩家执行死亡结算

        注意：彻底陨落（``kind == "permanent"``）时玩家数据已被删除，
        调用方（突破 / 世界事件）必须停止后续对该玩家的属性写入。

        Args:
            player: 当前玩家对象（会被就地修改并写回数据库）

        Returns:
            DeathOutcome 结算结果
        """
        death_config = self.get_death_config()
        keep_rate = self._clamp_rate(
            self._to_float(
                death_config.get("rebirth_exp_keep_rate"),
                DEFAULT_DEATH_CONFIG["rebirth_exp_keep_rate"],
            )
        )
        revival_hours = self._to_float(
            death_config.get("soul_revival_hours"),
            DEFAULT_DEATH_CONFIG["soul_revival_hours"],
        )
        exp_loss_rate = self._clamp_rate(
            self._to_float(
                death_config.get("soul_exp_loss_rate"),
                DEFAULT_DEATH_CONFIG["soul_exp_loss_rate"],
            )
        )

        if player.level_index < SOUL_STATE_LEVEL_INDEX:
            if not player.used_rebirth:
                # 首次死亡：劫后重生
                old_level_index = player.level_index
                player.level_index = max(0, player.level_index - 1)
                player.experience = int(player.experience * keep_rate)
                player.used_rebirth = True
                player.state = "空闲"
                player.cultivation_start_time = 0
                await self.db.update_player(player)

                logger.info(
                    f"玩家 {player.user_id} 触发劫后重生：境界 {old_level_index} -> "
                    f"{player.level_index}，保留修为 {player.experience}"
                )
                return DeathOutcome(
                    kind="rebirth",
                    old_level_index=old_level_index,
                    new_level_index=player.level_index,
                    keep_rate=keep_rate,
                    revival_hours=revival_hours,
                    exp_loss_rate=exp_loss_rate,
                )

            # 第二次死亡：彻底陨落
            level_index = player.level_index
            await self.db.delete_player_cascade(player.user_id)
            logger.info(f"玩家 {player.user_id} 彻底陨落（已使用过劫后重生），角色数据已删除")
            return DeathOutcome(
                kind="permanent",
                old_level_index=level_index,
                new_level_index=level_index,
                keep_rate=keep_rate,
                revival_hours=revival_hours,
                exp_loss_rate=exp_loss_rate,
            )

        # 金丹期及以上：肉身崩解，进入元神状态，死亡时立即扣除修为
        level_index = player.level_index
        exp_before_death = player.experience
        exp_lost = int(exp_before_death * exp_loss_rate)
        player.experience = max(0, exp_before_death - exp_lost)

        player.is_soul_state = True
        player.soul_death_time = int(time.time())
        player.soul_exp_before_death = exp_before_death  # 记录死亡前修为（用于显示）
        player.state = "元神"
        player.cultivation_start_time = 0
        await self.db.update_player(player)

        logger.info(
            f"玩家 {player.user_id} 进入元神状态（境界 {level_index}），"
            f"死亡损失 {exp_loss_rate*100:.1f}% 修为（{exp_lost}），"
            f"剩余修为 {player.experience}，"
            f"将在 {revival_hours:.0f} 小时后自动复活"
        )
        return DeathOutcome(
            kind="soul",
            old_level_index=level_index,
            new_level_index=level_index,
            keep_rate=keep_rate,
            revival_hours=revival_hours,
            exp_loss_rate=exp_loss_rate,
        )

    # ===== 自动复活检查 =====

    async def check_auto_revival(self, player: Player) -> bool:
        """检查玩家是否到达自动复活时间，如果是则自动复活

        Args:
            player: 玩家对象

        Returns:
            bool: 是否执行了自动复活
        """
        if not player.is_soul_state:
            return False

        death_config = self.get_death_config()
        revival_hours = self._to_float(
            death_config.get("soul_revival_hours"),
            DEFAULT_DEATH_CONFIG["soul_revival_hours"],
        )

        # -1表示永久等待，需要手动复活（使用还魂丹）
        if revival_hours < 0:
            return False

        now = int(time.time())
        death_time = player.soul_death_time or now
        elapsed_hours = (now - death_time) / 3600.0

        # 时间未到，继续等待
        if elapsed_hours < revival_hours:
            return False

        # 时间已到，自动复活
        await self.auto_revive_player(player)
        return True

    async def auto_revive_player(self, player: Player):
        """自动复活玩家（时间到达后）"""
        if not player.is_soul_state:
            return

        # 复活：清除元神状态，恢复到正常状态
        player.is_soul_state = False
        player.soul_death_time = 0
        player.soul_exp_before_death = 0
        player.state = "空闲"
        player.cultivation_start_time = 0

        await self.db.update_player(player)

        logger.info(
            f"玩家 {player.user_id} 自动复活（元神状态结束），"
            f"当前修为 {player.experience}"
        )
