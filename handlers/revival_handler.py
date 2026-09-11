# handlers/revival_handler.py
"""复活系统处理器（批次1：死亡机制重构）

负责元神状态的查询、自然复活、还魂丹复活与超时强制复活。
所有时间 / 比例参数均来自配置（config/death_config.json），方便后期调整平衡。

指令入口见 main.py：
- 「元神状态」 -> check_soul_state
- 「自然复活」 -> natural_revival
"""

import time
from typing import Optional, Tuple

from astrbot.api import logger

from ..data.data_manager import DataBase
from ..models import Player

__all__ = ["RevivalHandler"]

# 与 config/death_config.json 保持一致（内置默认值）
DEFAULT_DEATH_CONFIG = {
    "soul_revival_hours": 24,
    "soul_exp_loss_rate": 0.05,
    "rebirth_exp_keep_rate": 0.7,
    "soul_exp_decay_per_hour": 0.01,
}

# 超过复活时间的该倍数仍未复活 -> 修为完全流失，强制复活
FORCE_REVIVE_MULTIPLIER = 4

SEP = "━━━━━━━━━━━━━━━"


class RevivalHandler:
    """复活系统处理器"""

    def __init__(self, db: DataBase, config: Optional[dict] = None, config_manager=None):
        self.db = db
        self.config = config or {}
        self.config_manager = config_manager

        death_config = self._get_merged_config()
        self.revival_hours = self._to_float(
            death_config.get("soul_revival_hours"), DEFAULT_DEATH_CONFIG["soul_revival_hours"]
        )
        self.exp_loss_rate = self._to_float(
            death_config.get("soul_exp_loss_rate"), DEFAULT_DEATH_CONFIG["soul_exp_loss_rate"]
        )
        self.rebirth_exp_keep_rate = self._to_float(
            death_config.get("rebirth_exp_keep_rate"), DEFAULT_DEATH_CONFIG["rebirth_exp_keep_rate"]
        )
        self.decay_per_hour = self._to_float(
            death_config.get("soul_exp_decay_per_hour"), DEFAULT_DEATH_CONFIG["soul_exp_decay_per_hour"]
        )

    # ===== 配置 =====

    @staticmethod
    def _to_float(value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _get_merged_config(self) -> dict:
        """合并配置：ConfigManager（文件）> 插件配置 > 内置默认值"""
        merged = DEFAULT_DEATH_CONFIG.copy()

        # 从插件配置中读取
        if self.config:
            merged.update(self.config.get("death_config", {}))

        # 从 ConfigManager 中读取（优先级最高）
        if self.config_manager:
            try:
                death_cfg = self.config_manager.get("death_config", {})
                if death_cfg:
                    merged.update(death_cfg)
            except Exception as e:
                logger.warning(f"读取 death_config 失败: {e}")

        return merged

    def _resolve_death_config(self) -> dict:
        """合并配置：ConfigManager（文件）> 插件配置 > 内置默认值"""
        return self._get_merged_config()

    def refresh_config(self):
        """重新读取死亡配置（配置热更新时调用）"""
        death_config = self._get_merged_config()
        self.revival_hours = self._to_float(
            death_config.get("soul_revival_hours"), DEFAULT_DEATH_CONFIG["soul_revival_hours"]
        )
        self.exp_loss_rate = self._to_float(
            death_config.get("soul_exp_loss_rate"), DEFAULT_DEATH_CONFIG["soul_exp_loss_rate"]
        )
        self.rebirth_exp_keep_rate = self._to_float(
            death_config.get("rebirth_exp_keep_rate"), DEFAULT_DEATH_CONFIG["rebirth_exp_keep_rate"]
        )
        self.decay_per_hour = self._to_float(
            death_config.get("soul_exp_decay_per_hour"), DEFAULT_DEATH_CONFIG["soul_exp_decay_per_hour"]
        )

    # ===== 时间显示 =====

    @staticmethod
    def format_duration(hours: float) -> str:
        """将小时数格式化为「X天Y小时Z分钟」"""
        total_minutes = max(0, int(round(hours * 60)))
        days, remain = divmod(total_minutes, 60 * 24)
        hour_part, minute_part = divmod(remain, 60)

        parts = []
        if days:
            parts.append(f"{days}天")
        if hour_part:
            parts.append(f"{hour_part}小时")
        if minute_part or not parts:
            parts.append(f"{minute_part}分钟")
        return "".join(parts)

    # ===== 元神状态 =====

    def get_soul_progress(self, player: Player) -> dict:
        """计算元神状态的进度信息"""
        now = int(time.time())
        death_time = int(player.soul_death_time or 0)
        elapsed_seconds = max(0, now - death_time) if death_time else 0
        elapsed_hours = elapsed_seconds / 3600

        remaining_hours = max(0.0, self.revival_hours - elapsed_hours)
        max_wait_hours = self.revival_hours * FORCE_REVIVE_MULTIPLIER

        # 修为流失比例（每小时流失，超过100%按100%计）
        decay_percent = min(100.0, elapsed_hours * self.decay_per_hour * 100)

        return {
            "elapsed_hours": elapsed_hours,
            "remaining_hours": remaining_hours,
            "elapsed_text": self.format_duration(elapsed_hours),
            "remaining_text": self.format_duration(remaining_hours),
            "decay_percent": decay_percent,
            "max_wait_hours": max_wait_hours,
            "can_natural_revival": elapsed_hours >= self.revival_hours,
            "force_revive": elapsed_hours >= max_wait_hours,
        }

    def check_soul_state(self, player: Player) -> str:
        """查看元神状态详情"""
        if not player.is_soul_state:
            return (
                "🧘 你并未处于元神状态\n"
                f"{SEP}\n"
                "只有金丹期及以上修士突破失败肉身崩解后，才会以元神形态留存人间。"
            )

        progress = self.get_soul_progress(player)

        lines = [
            f"{SEP} 元神状态 {SEP}",
            f"👻 {(player.user_name or player.user_id)}　境界保留",
            f"{SEP}",
            f"⏰ 已流逝：{progress['elapsed_text']}",
            f"💫 每小时流失：{self.decay_per_hour:.1%} 修为（累计流失 {progress['decay_percent']:.1f}%）",
            f"🕐 自然复活剩余：{progress['remaining_text']}",
            f"⏳ 最长可支撑：{self.format_duration(progress['max_wait_hours'])}（超时修为归零强制复活）",
            f"{SEP}",
            "💡 复活方式：",
            f"1. 发送「自然复活」（需等待 {self.format_duration(self.revival_hours)}，永久损失 {self.exp_loss_rate:.0%} 修为）",
            "2. 使用还魂丹（立即复活，无修为损失，由炼丹师炼制）",
            "3. 请求宗门/师父救援（后续开放）",
            f"{SEP}",
            "⚠️ 元神状态下无法修炼与战斗，请尽快复活！",
        ]
        return "\n".join(lines)

    # ===== 复活 =====

    async def revive_soul(
        self,
        player: Player,
        loss_rate: float = None,
        reason: str = "natural",
    ) -> Tuple[bool, str]:
        """执行复活结算（内部统一出口）

        Args:
            player: 玩家对象
            loss_rate: 永久损失的修为比例，默认取配置「soul_exp_loss_rate」
            reason: 复活来源（natural=自然复活 / pill=还魂丹 / force=超时强制）

        Returns:
            (是否复活成功, 消息)
        """
        if not player.is_soul_state:
            return False, "你并未处于元神状态"

        if loss_rate is None:
            loss_rate = self.exp_loss_rate
        loss_rate = max(0.0, min(1.0, self._to_float(loss_rate, self.exp_loss_rate)))

        exp_before = int(player.soul_exp_before_death or player.experience or 0)
        if reason == "force":
            restored_exp = 0
        else:
            restored_exp = int(exp_before * (1 - loss_rate))

        player.is_soul_state = False
        player.experience = max(0, restored_exp)
        player.state = "空闲"
        player.soul_death_time = 0
        player.soul_exp_before_death = 0
        await self.db.update_player(player)

        logger.info(
            f"[复活系统] 玩家 {player.user_id} 复活（来源：{reason}），"
            f"修为 {exp_before} -> {player.experience}（损失 {loss_rate:.1%}）"
        )

        if reason == "force":
            return True, (
                "💀 元神消散时间过长...\n"
                f"{SEP}\n"
                "你的修为已完全流失，勉强重塑肉身复活\n"
                "⚠️ 请尽快重新修炼！"
            )

        if reason == "pill":
            return True, (
                "✨ 还魂丹入腹，元神归位！\n"
                f"{SEP}\n"
                f"🌟 {player.user_name or player.user_id} 完美复活，修为无损\n"
                f"💫 当前修为：{player.experience}\n"
                f"{SEP}\n"
                "💡 元神状态已解除，可以继续修炼与战斗了"
            )

        return True, (
            "✨ 元神重聚，肉身重塑！\n"
            f"{SEP}\n"
            f"🌟 {player.user_name or player.user_id} 复活了\n"
            f"📉 永久损失 {loss_rate:.0%} 修为\n"
            f"💫 当前修为：{player.experience}\n"
            f"{SEP}\n"
            "💡 元神状态已解除，可以继续修炼与战斗了"
        )

    async def natural_revival(self, player: Player) -> str:
        """自然复活（等待配置时间后可用）"""
        if not player.is_soul_state:
            return "🧘 你并未处于元神状态，无需复活"

        progress = self.get_soul_progress(player)
        if not progress["can_natural_revival"]:
            return (
                "⏰ 自然复活尚需等待\n"
                f"{SEP}\n"
                f"需等待：{self.format_duration(self.revival_hours)}\n"
                f"已流逝：{progress['elapsed_text']}\n"
                f"剩余：{progress['remaining_text']}\n"
                f"{SEP}\n"
                "💡 也可以使用还魂丹立即复活"
            )

        _, msg = await self.revive_soul(player, loss_rate=self.exp_loss_rate, reason="natural")
        return msg

    async def revive_by_pill(self, player: Player) -> Tuple[bool, str]:
        """还魂丹复活（完美复活，无修为损失）

        由炼丹系统（批次2）在玩家服用还魂丹时调用。
        """
        return await self.revive_soul(player, loss_rate=0.0, reason="pill")

    async def check_and_auto_revive(self, player: Player) -> Tuple[bool, str]:
        """检查是否需要强制复活（超时未复活则修为归零并自动复活）

        用于玩家每次操作前自动检测，避免玩家长期卡在元神状态。

        Returns:
            (是否已自动复活, 消息)
        """
        if not player.is_soul_state:
            return False, ""

        progress = self.get_soul_progress(player)
        if not progress["force_revive"]:
            return False, ""

        await self.revive_soul(player, loss_rate=1.0, reason="force")
        return True, (
            "💀 元神消散时间过长...\n"
            f"{SEP}\n"
            "你的修为已完全流失，勉强复活\n"
            "⚠️ 请尽快重新修炼！"
        )

    # ===== 还魂丹（批次2：炼丹系统升级） =====

    def get_pill_count(self, player: Player, pill_name: str) -> int:
        """获取丹药背包中的丹药数量"""
        try:
            inventory = player.get_pills_inventory()
        except Exception:
            return 0
        try:
            return int(inventory.get(pill_name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _remove_pill(self, player: Player, pill_name: str, count: int = 1) -> bool:
        """从丹药背包移除丹药"""
        inventory = player.get_pills_inventory()
        owned = int(inventory.get(pill_name, 0) or 0)
        if owned < count:
            return False
        if owned - count <= 0:
            inventory.pop(pill_name, None)
        else:
            inventory[pill_name] = owned - count
        player.set_pills_inventory(inventory)
        return True

    async def use_revival_pill(self, player: Player, pill_name: str = "还魂丹") -> Tuple[bool, str]:
        """服用还魂丹复活（元神状态专属，完美复活且修为无损）

        Args:
            player: 玩家对象
            pill_name: 丹药名称（默认还魂丹，方便后续扩展其它复活丹药）

        Returns:
            (是否复活成功, 消息)
        """
        if not player.is_soul_state:
            return False, (
                "🧘 你并未处于元神状态，无需使用还魂丹\n"
                f"{SEP}\n"
                "💡 还魂丹请在元神状态（金丹期以上突破死亡）下使用"
            )

        if self.get_pill_count(player, pill_name) <= 0:
            return False, (
                f"⚠️ 你的丹药背包中没有【{pill_name}】！\n"
                f"{SEP}\n"
                f"💡 {pill_name}获取途径：\n"
                "  1. 自行炼制（5星稀有配方，需先学习丹方）\n"
                "  2. 委托炼丹师炼制（批次3开放）\n"
                "  3. 参与世界事件获得（批次4开放）"
            )

        # 消耗还魂丹后再复活，避免复活失败导致丹药异常
        if not self._remove_pill(player, pill_name, 1):
            return False, f"⚠️ 服用【{pill_name}】失败，请稍后重试"

        ok, msg = await self.revive_by_pill(player)
        if not ok:
            # 复活失败（例如状态已被其它逻辑解除）则退还丹药
            inventory = player.get_pills_inventory()
            inventory[pill_name] = int(inventory.get(pill_name, 0) or 0) + 1
            player.set_pills_inventory(inventory)
            await self.db.update_player(player)
            return False, msg

        return True, (
            f"✨ 【{pill_name}】药力涌现！\n"
            f"{SEP}\n"
            f"🌟 元神归位，肉身重塑\n"
            f"💫 {player.user_name or player.user_id} 完美复活\n"
            "📊 修为无损！\n"
            f"{SEP}\n"
            f"💫 当前修为：{player.experience}"
        )
