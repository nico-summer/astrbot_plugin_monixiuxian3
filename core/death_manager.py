# core/death_manager.py
"""死亡 / 陨落统一处理（批次1 死亡机制，批次4 世界事件复用）

规则（参数见 config/death_config.json）：
- 练气期-筑基期（境界 < 13）：首次死亡触发「劫后重生」——境界倒退 1 级、
  保留部分修为、状态清空；第二次死亡彻底陨落（删除角色及关联数据）
- 金丹期及以上（境界 >= 13）：死亡进入【元神状态】，不会删号，等待复活

批次1 的「突破走火入魔」与批次4 的「世界事件阵亡」共用这里的逻辑，
避免两套死亡规则各自漂移。
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
]

# 金丹期及以上（level_index >= 13）死亡进入元神状态
SOUL_STATE_LEVEL_INDEX = 13

# 内核默认值（与 config/death_config.json 保持一致）
DEFAULT_DEATH_CONFIG = {
    "soul_revival_hours": 24,
    "soul_exp_loss_rate": 0.05,
    "rebirth_exp_keep_rate": 0.7,
    "soul_exp_decay_per_hour": 0.01,
}


@dataclass
class DeathOutcome:
    """一次死亡结算的结果"""

    kind: str  # rebirth（劫后重生）/ permanent（彻底陨落）/ soul（元神状态）
    old_level_index: int = 0
    new_level_index: int = 0
    keep_rate: float = 1.0
    revival_hours: float = DEFAULT_DEATH_CONFIG["soul_revival_hours"]
    exp_loss_rate: float = DEFAULT_DEATH_CONFIG["soul_exp_loss_rate"]
    decay_per_hour: float = DEFAULT_DEATH_CONFIG["soul_exp_decay_per_hour"]

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
        exp_loss_rate = self._to_float(
            death_config.get("soul_exp_loss_rate"),
            DEFAULT_DEATH_CONFIG["soul_exp_loss_rate"],
        )
        decay_per_hour = self._to_float(
            death_config.get("soul_exp_decay_per_hour"),
            DEFAULT_DEATH_CONFIG["soul_exp_decay_per_hour"],
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
                    decay_per_hour=decay_per_hour,
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
                decay_per_hour=decay_per_hour,
            )

        # 金丹期及以上：肉身崩解，进入元神状态
        level_index = player.level_index
        player.is_soul_state = True
        player.soul_death_time = int(time.time())
        player.soul_exp_before_death = player.experience  # 保存死亡前的完整修为
        player.experience = int(player.experience * 0.5)  # 元神期间展示修为=死亡前50%
        player.state = "元神"
        player.cultivation_start_time = 0
        await self.db.update_player(player)

        logger.info(
            f"玩家 {player.user_id} 进入元神状态（境界 {level_index}），"
            f"自然复活需 {revival_hours:.0f} 小时"
        )
        return DeathOutcome(
            kind="soul",
            old_level_index=level_index,
            new_level_index=level_index,
            keep_rate=keep_rate,
            revival_hours=revival_hours,
            exp_loss_rate=exp_loss_rate,
            decay_per_hour=decay_per_hour,
        )
