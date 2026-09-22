# managers/world_event_manager.py
"""世界事件系统管理器（批次4：替代历练系统）

多人协作的随机事件：定时任务（或批次5 的管理员）在群内发布事件 → 玩家报名 →
报名截止自动开战 → 结束后统一结算死亡与奖励。

设计要点：
- 事件按群独立：每个群一份报名名单与结算结果，同名事件可在多个群同时开启
- 阵亡会触发批次1 的死亡机制（低阶「劫后重生」/ 金丹以上「元神状态」）
- v3.9.1 起死亡率为「境界压制」模型：境界越高死亡风险越低，
  高出事件推荐上限一个大境界后**完全免死**（v3.10.0 小说化模型：最终死亡率 = 基准 × 境界优势 × 战力优势），
  不会再出现炼虚期修士被低阶妖兽围城骰死的情况
- v3.9.1 起持有「回生丹」的玩家阵亡时优先被丹药抵消（与突破走火入魔同逻辑）
- 幸存者拿全额奖励，阵亡者按 death_penalty_reward_rate（v3.11.0 默认 50%）结算
- 奖励含稀有炼丹材料（九转仙草 / 太古龙骨 / 灵髓精华 / 月华精粹），用于炼制还魂丹
- v3.11.0 奖励重构（玩家反馈「顶着死亡风险只掉俩魔核」）：
  1. **数值随境界成长**：修为/灵石以模板的 ``reward_ref_level`` 为基准，玩家每高
     1 个小境界 ×``reward_level_growth``（默认 1.35，上限 ``reward_max_multiplier``）。
     此前是固定区间，导致高境界玩家打事件只拿同境界秘境的 1/3~1/40。
  2. **材料必掉保底**：每场至少掉 ``reward_guaranteed_material``（默认 1）件材料，
     材料支持 ``count`` 数量区间（如 灵髓精华 ×2~3），还魂丹材料成型速度提升约 2 倍。
  3. **阵亡代价下调**：阵亡奖励保留比例默认 20% → 50%，且事件阵亡的修为损失按
     ``death_exp_loss_scale``（默认 0.5）打折，避免「为了复活道具进副本，结果先把
     修为赔光」。
- v3.10.0 起战斗过程可见：报名截止开战后，战斗按时长拆成 3~5 个节点逐段推进，
  每个节点独立掷骰并实时播报战况。每节点死亡率取 ``1 - (1 - p) ** (1 / N)``，
  因此**整场死亡率与旧版完全一致**，只是把一次骰子摊成了可见的若干次。
  具体战况计算见 ``managers/world_event_battle.py``（纯计算，不碰数据库）。
"""

import asyncio
import json
import random
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from astrbot.api import logger

from ..data.data_manager import DataBase
from ..data.default_configs import DEFAULT_WORLD_EVENT_CONFIG
from ..models import Player
from . import world_event_battle as battle

__all__ = ["WorldEventManager"]

SEP = "━━━━━━━━━━━━━━━"


# AstrBot 插件配置（_conf_schema.json）中的世界事件配置分组与键名映射
PLUGIN_CONFIG_GROUP = "WORLD_EVENT"
PLUGIN_KEY_MAP = {
    "AUTO_GENERATE": "auto_generate",
    "AUTO_INTERVAL_MINUTES": "auto_interval_minutes",
    "SIGNUP_DURATION_SECONDS": "signup_duration_seconds",
    "MIN_PARTICIPANTS": "min_participants",
    "MAX_PARTICIPANTS": "max_participants",
    "DEATH_PENALTY_REWARD_RATE": "death_penalty_reward_rate",
    # v3.10.0 小说化风险模型（旧的 4 个 DEATH_* 参数已废弃，见 _conf_schema）
    "DEATH_MINOR_DECAY": "death_minor_decay",
    "DEATH_ZERO_THRESHOLD": "death_zero_threshold",
    # v3.10.0 修复：以下两个键此前未登记映射，面板上改动不会生效（只有改
    # config/world_events.json 才生效）
    "ENABLE_COMBAT_POWER_REDUCTION": "enable_combat_power_reduction",
    "COMBAT_POWER_MAX_REDUCTION": "combat_power_max_reduction",
    "COMBAT_POWER_DECAY": "combat_power_decay",
    # v3.10.0 新增：可见战斗过程
    "BATTLE_PROCESS_ENABLED": "battle_process_enabled",
    "BATTLE_NODE_COUNT": "battle_node_count",
    "BATTLE_BROADCAST_MIN_PARTICIPANTS": "battle_broadcast_min_participants",
    # v3.11.0 奖励重构：奖励随境界成长 + 阵亡修为损失折扣
    "REWARD_LEVEL_SCALING": "reward_level_scaling",
    "REWARD_LEVEL_GROWTH": "reward_level_growth",
    "REWARD_MAX_MULTIPLIER": "reward_max_multiplier",
    "DEATH_EXP_LOSS_SCALE": "death_exp_loss_scale",
    "BROADCAST_GROUPS": "broadcast_groups",
    "ADMIN_USERS": "admin_users",
}


def _extract_plugin_world_event_config(plugin_config, defaults: dict) -> dict:
    """从 AstrBot 插件配置面板提取世界事件配置（仅覆盖面板上暴露的项）

    面板键名为大写（见 _conf_schema.json 的 WORLD_EVENT 分组），
    内部键名为 snake_case；未在面板配置的项保持配置文件里的取值。
    """
    if plugin_config is None or not hasattr(plugin_config, "get"):
        return {}

    group = None
    try:
        group = plugin_config.get(PLUGIN_CONFIG_GROUP, None)
    except Exception:
        group = None
    if not isinstance(group, dict):
        return {}

    values: dict = {}
    for key, value in group.items():
        if value is None:
            continue
        internal = PLUGIN_KEY_MAP.get(key, key)
        if internal in defaults:
            values[internal] = value
    return values


# 指令名（handlers / main.py / 管理器展示共用，避免多处硬编码漂移）
CMD_WORLD_EVENT = "世界事件"
CMD_JOIN_WORLD_EVENT = "加入世界事件"
CMD_LEAVE_WORLD_EVENT = "退出世界事件"
CMD_WORLD_EVENT_RECORD = "世界事件战绩"
CMD_WORLD_EVENT_REWARDS = "我的世界事件奖励"  # 查看个人奖励
CMD_WORLD_EVENT_BATTLE = "世界事件战报"  # 新增：查看个人视角的战斗过程回放

# 事件状态
STATUS_SIGNUP = "signup"             # 报名中
STATUS_IN_PROGRESS = "in_progress"   # 战斗中
STATUS_COMPLETED = "completed"       # 已结算
STATUS_CANCELLED = "cancelled"       # 已取消（人数不足 / 管理员取消）
ACTIVE_STATUSES = (STATUS_SIGNUP, STATUS_IN_PROGRESS)

# 参与者结算结果
RESULT_SURVIVOR = "survivor"  # 生还
RESULT_DEAD = "dead"          # 阵亡（元神状态 / 劫后重生）
RESULT_FALLEN = "fallen"      # 彻底陨落（角色已删除）

# 掉落装备的属性模板（按难度分组，power 为该难度装备的基准数值）
EQUIPMENT_STATS = {
    "low_tier": {"level_index": 0, "rank": "凡品", "power": 16},
    "mid_tier": {"level_index": 13, "rank": "天品", "power": 130},
    "high_tier": {"level_index": 19, "rank": "皇品", "power": 360},
    "epic_tier": {"level_index": 28, "rank": "帝品", "power": 1000},
}

# 掉落装备 type -> 装备系统类型
EQUIPMENT_TYPES = {"武器": "weapon", "防具": "armor", "饰品": "accessory"}

# 未接入 AI 时的回合氛围文案（按回合号取用，确定性输出便于复现排查）
BATTLE_FLAVOUR = (
    "灵气激荡，杀声震野。",
    "血光冲天，战线反复易手。",
    "双方你来我往，法宝光华照亮半边天。",
    "烟尘蔽日，喊杀声不绝于耳。",
    "罡风横扫，战阵几度崩散又重整。",
)

# 有伤亡时的收束句（无 AI 时使用）
BATTLE_FLAVOUR_CASUALTY = "战况骤紧，有人再也站不起来了。"

# 大境界顺序（境界压制 / 风险系数展示共用，避免多处硬编码漂移）
REALM_ORDER = (
    "炼气期", "筑基期", "金丹期", "元婴期", "化神期", "炼虚期", "合体期",
    "大乘期", "渡劫期", "地仙", "天仙", "大罗金仙", "混元大罗金仙",
)

# 结果标签（结算文案 / 个人战报共用）
RESULT_LABELS = {
    RESULT_SURVIVOR: "✅ 生还",
    RESULT_DEAD: "💀 阵亡",
    RESULT_FALLEN: "🪦 陨落",
}

# 主法伤的武器关键词
MAGIC_WEAPON_KEYWORDS = ("法杖", "琴", "符", "笔", "幡", "笛", "钟", "镜", "珠", "法宝")

# 武器类别推断（仅用于展示）
WEAPON_CATEGORY_SUFFIXES = (
    ("阔刀", "阔刀"), ("重剑", "剑"), ("长枪", "枪"), ("神兵", "剑"),
    ("长剑", "剑"), ("剑", "剑"), ("刀", "刀"), ("刃", "刀"), ("枪", "枪"),
    ("棍", "棍"), ("戟", "戟"), ("杖", "杖"), ("琴", "琴"), ("符", "符箓"),
    ("鼎", "鼎"), ("笔", "笔"), ("匕", "匕首"), ("斧", "斧"), ("弓", "弓"),
)

# 掉落装备名 -> 生成配置 的缓存
_EQUIPMENT_INDEX: Optional[Dict[str, dict]] = None


class WorldEventManager:
    """世界事件管理器"""

    # 事件模板配置（config/world_events.json），供 ItemRegistry / 装备系统解析
    CONFIG_FILE = Path(__file__).resolve().parents[1] / "config" / "world_events.json"

    def __init__(
        self,
        db: DataBase,
        config_manager=None,
        storage_ring_manager=None,
        death_manager=None,
        plugin_config=None,
        equipment_manager=None,
    ):
        self.db = db
        self.config_manager = config_manager
        self.storage = storage_ring_manager
        self.death_manager = death_manager
        self.equipment_manager = equipment_manager  # 装备管理器（用于战力计算）
        # AstrBot 插件配置面板（DEATH_CONFIG 同级的 WORLD_EVENT 分组）
        self._ai_generator = None  # AI 文案生成器（懒加载）
        # 回合氛围文案缓存：键为「模板 + 回合 + 伤亡档位」，与群数无关，
        # 保证 P1 接入 AI 后调用量不随群数线性膨胀（v3.10.0）
        self._battle_text_cache: Dict[tuple, str] = {}
        self.plugin_config = plugin_config

    # ==================== 配置 ====================

    def _config(self) -> dict:
        """获取世界事件核心参数

        优先级：内置默认值 < 配置文件（config/world_events.json） < AstrBot 插件配置面板。
        """
        merged = dict(DEFAULT_WORLD_EVENT_CONFIG)

        getter = getattr(self.config_manager, "get_world_event_config", None)
        if callable(getter):
            try:
                file_config = getter() or {}
                if isinstance(file_config, dict) and file_config:
                    merged.update({k: v for k, v in file_config.items() if v is not None})
            except Exception as e:
                logger.warning(f"[世界事件] 读取配置失败，使用默认值: {e}")

        merged.update(_extract_plugin_world_event_config(self.plugin_config, merged))
        return merged

    def _death_rate_display(self, event_data: dict) -> str:
        """死亡率展示文案（基础值 + 境界压制说明，避免玩家误以为人人同概率）"""
        base = int(self._to_float(event_data.get("base_death_rate"), 0.0) * 100)
        # 新规则：高出一个大境界（9级）完全免死
        return f"{base}%（基准·境界越高越低，高出推荐上限一个大境界免死）"

    # ==================== 可见战斗过程（v3.10.0） ====================

    def _battle_process_enabled(self) -> bool:
        """总开关：是否启用可见战斗过程

        关掉后整场只掷一次骰、不产生战斗状态，行为与 v3.9.x 完全一致。
        """
        return bool(self._config().get("battle_process_enabled", True))

    def _battle_broadcast_enabled(self, participant_count: int) -> bool:
        """是否把回合战况播报到群

        人数不足 ``battle_broadcast_min_participants`` 时**只记录不播报**——
        单个玩家的回合播报只会变成对全群的刷屏。这类事件的战斗过程照常记录，
        玩家仍可用「世界事件战报」查看自己的逐回合回放。
        """
        try:
            need = int(self._config().get("battle_broadcast_min_participants") or 2)
        except (TypeError, ValueError):
            need = 2
        try:
            count = int(participant_count or 0)
        except (TypeError, ValueError):
            count = 0
        return count >= max(1, need)

    def _battle_node_plan(self, duration_minutes: int) -> int:
        """战斗节点数（配置 battle_node_count=0 时按时长自动规划）"""
        config = self._config()
        try:
            override = int(config.get("battle_node_count") or 0)
        except (TypeError, ValueError):
            override = 0
        return battle.node_count(duration_minutes, override)

    async def build_battle_participants(self, event_id, event_data: dict) -> List[dict]:
        """构建参战快照（战力 / 整场死亡率），供战斗过程计算使用

        死亡率按「整场」口径计算，节点死亡率由 battle 模块用
        ``1 - (1 - p) ** (1 / N)`` 折算，保证总死亡率与旧版一致。
        """
        participants: List[dict] = []
        min_level = self._to_int((event_data or {}).get("min_level"), 0)
        max_level = self._to_int((event_data or {}).get("max_level"), 0)
        base_death_rate = self._to_float((event_data or {}).get("base_death_rate"), 0.0)

        for user_id in await self.get_participants(event_id):
            player = await self.db.get_player_by_id(user_id)
            if not player:
                continue
            level_index = self._to_int(getattr(player, "level_index", 0), 0)
            death_detail = self._calc_death_rate_detail(player, base_death_rate, min_level, max_level)
            participants.append({
                "user_id": str(user_id),
                "name": player.user_name or f"道友{str(user_id)[:6]}",
                "level_index": level_index,
                "power": self._player_battle_power(player, level_index),
                "death_rate": death_detail["final"],
            })
        return participants

    def _player_battle_power(self, player: Player, level_index: int) -> float:
        """玩家在事件中的输出能力（与战力减免共用同一套属性口径）"""
        combat_power, expected_base = self._player_attr_combat_power(player, level_index)
        # 保底：低战力玩家也能贡献输出，避免「输出占比 0%」这种尴尬播报
        return float(max(combat_power, expected_base * 0.35))

    def describe_risk_breakdown(self, player: Player, event_data: dict) -> str:
        """个人「风险系数」透明化文案（v3.10.0）

        让玩家看得见自己为什么安全 / 危险：境界优势减了多少、战力（装备+丹药）
        又减了多少。既降低阵亡时的挫败感，也让「穿装备、磕药有用」变得可感知。
        """
        min_level = self._to_int((event_data or {}).get("min_level"), 0)
        max_level = self._to_int((event_data or {}).get("max_level"), 0)
        base_death_rate = self._to_float((event_data or {}).get("base_death_rate"), 0.0)
        detail = self._calc_death_rate_detail(player, base_death_rate, min_level, max_level)

        if detail["immune"]:
            return (
                f"💀 你的风险系数：0%（事件基准 {detail['base'] * 100:.0f}%）\n"
                f"   • {detail['immune_reason']}"
            )

        lines = [
            f"💀 你的风险系数：{detail['final'] * 100:.1f}%（事件基准 {detail['base'] * 100:.0f}%）"
        ]

        realm_multiplier = detail["realm_multiplier"]
        if abs(realm_multiplier - 1.0) >= 0.005:
            lines.append(
                f"   • 境界优势：×{realm_multiplier:.2f}（{detail['realm_note']}）"
            )
        else:
            lines.append(f"   • 境界优势：无（{detail['realm_note'] or '无推荐区间'}）")

        reduction = detail["combat_reduction"]
        if reduction > 0.001:
            lines.append(f"   • 战力优势：×{detail['power_multiplier']:.2f}（减免 {reduction * 100:.0f}%，装备 + 丹药 buff）")
        else:
            lines.append("   • 战力优势：无（战力未超过同境界期望值）")

        return "\n".join(lines)

    def _combat_power_reduction_from_ratio(self, combat_ratio: float, max_reduction: float, decay: float = 1.0) -> float:
        """战力倍率 -> 死亡率减免比例（连续曲线，唯一口径）

        ``减伤 = 1 - 1 / (1 + decay × (战力倍率 - 1))``，
        再以 ``max_reduction`` 封顶。含义直观：

        - 战力 = 同境界期望值（倍率 1.0）→ 无减免
        - 战力 2 倍 → 减免 50%（风险减半）
        - 战力 4 倍 → 减免 75%（风险降到 1/4）

        v3.10.0 之前是 1.5x/2x/3x/4x 的硬阶梯且封顶 30%，导致「堆了一身神装
        死亡率几乎不变」，与小说体感不符。
        """
        try:
            ratio = float(combat_ratio)
        except (TypeError, ValueError):
            ratio = 1.0
        try:
            cap = max(0.0, min(0.95, float(max_reduction)))
        except (TypeError, ValueError):
            cap = 0.90
        try:
            k = max(0.0, float(decay))
        except (TypeError, ValueError):
            k = 1.0

        if ratio <= 1.0:
            # 战力低于同境界期望值：不给减免（风险由境界段体现）
            return 0.0
        if k <= 0.0:
            return cap
        continuous = 1.0 - 1.0 / (1.0 + k * (ratio - 1.0))
        return max(0.0, min(cap, continuous))

    def _templates(self) -> Dict[str, List[dict]]:
        """获取事件模板（按难度分组）"""
        getter = getattr(self.config_manager, "get_world_event_templates", None)
        if callable(getter):
            try:
                templates = getter() or {}
                if isinstance(templates, dict):
                    return templates
            except Exception as e:
                logger.warning(f"[世界事件] 读取事件模板失败: {e}")
        return {}

    @classmethod
    def _to_int(cls, value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    # ==================== 模板查询 ====================

    def get_all_templates(self) -> List[Tuple[str, dict]]:
        """全部事件模板：(难度分组, 模板) 列表"""
        result: List[Tuple[str, dict]] = []
        for tier_key, tier_templates in (self._templates() or {}).items():
            for template in tier_templates or []:
                if isinstance(template, dict) and template.get("name"):
                    result.append((tier_key, template))
        return result

    def get_templates_by_tier(self, tier_key: str) -> List[dict]:
        """指定难度分组的事件模板"""
        templates = (self._templates() or {}).get(tier_key) or []
        return [t for t in templates if isinstance(t, dict) and t.get("name")]

    def get_random_template(self, tier_key: str = None) -> Optional[dict]:
        """随机获取一个事件模板（可按难度分组指定）"""
        if tier_key:
            candidates = self.get_templates_by_tier(tier_key)
            return self._weighted_choice(candidates)

        all_templates = [tpl for _, tpl in self.get_all_templates()]
        return self._weighted_choice(all_templates)

    def find_template(self, template_id) -> Optional[dict]:
        """按模板ID查找事件模板"""
        if template_id is None or template_id == "":
            return None
        finder = getattr(self.config_manager, "find_world_event_template", None)
        if callable(finder):
            try:
                template = finder(template_id)
                if template:
                    return template
            except Exception:
                pass
        for _, template in self.get_all_templates():
            if str(template.get("id")) == str(template_id):
                return template
        return None

    def get_template_tier_key(self, template: dict) -> str:
        """获取模板所属难度分组名"""
        if not template:
            return ""
        template_id = template.get("id")
        for tier_key, tier_templates in (self._templates() or {}).items():
            for item in tier_templates or []:
                if item is template or str(item.get("id")) == str(template_id):
                    return tier_key
        return ""

    def pick_tier_key(self) -> str:
        """按权重随机挑选一个难度分组（自动生成事件时使用）"""
        weights = self._config().get("auto_tier_weights") or {}
        candidates: List[str] = []
        for tier_key in (self._templates() or {}).keys():
            if self.get_templates_by_tier(tier_key):
                candidates.append(tier_key)
        if not candidates:
            return ""

        weighted: List[str] = []
        for tier_key in candidates:
            weight = self._to_int(weights.get(tier_key), 0)
            weighted.extend([tier_key] * max(0, weight))
        if not weighted:
            return random.choice(candidates)
        return random.choice(weighted)

    @staticmethod
    def _weighted_choice(templates: List[dict]) -> Optional[dict]:
        """按 weight 字段加权随机（未配置权重时等概率）"""
        if not templates:
            return None
        total = 0
        for template in templates:
            total += max(1, int(template.get("weight", 1) or 1))
        roll = random.randint(1, total)
        upto = 0
        for template in templates:
            upto += max(1, int(template.get("weight", 1) or 1))
            if roll <= upto:
                return template
        return templates[-1]

    def _get_level_name(self, level_index: int) -> str:
        """获取境界名称（用于展示推荐境界）"""
        level_data = getattr(self.config_manager, "level_data", None) or []
        try:
            index = int(level_index)
        except (TypeError, ValueError):
            index = 0
        if 0 <= index < len(level_data):
            return level_data[index].get("level_name", f"境界{index}")
        if index >= len(level_data) and level_data:
            # 模板的 max_level 允许写成「上限开区间」（如史诗 36 = 境界表末尾之后再无更高档），
            # 展示时钳到最后一档，避免面板里出现「境界36」这种生造名词
            return level_data[-1].get("level_name", f"境界{index}")
        return f"境界{index}"

    # ==================== 装备掉落配置 ====================

    @classmethod
    def get_equipment_index(cls) -> Dict[str, dict]:
        """事件掉落装备名 -> 完整装备配置（结果缓存）"""
        global _EQUIPMENT_INDEX
        if _EQUIPMENT_INDEX is not None:
            return _EQUIPMENT_INDEX

        index: Dict[str, dict] = {}
        try:
            with open(cls.CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            logger.warning(f"[世界事件] 读取掉落装备配置失败: {e}")
            _EQUIPMENT_INDEX = index
            return index

        templates = (config or {}).get("event_templates") or {}
        for tier_key, tier_templates in templates.items():
            stats = EQUIPMENT_STATS.get(tier_key) or EQUIPMENT_STATS["low_tier"]
            for template in tier_templates or []:
                rewards = (template or {}).get("rewards") or {}
                for entry in rewards.get("equipments", []):
                    name = (entry or {}).get("name")
                    if not name or name in index:
                        continue
                    built = cls._build_equipment_config(name, entry, stats)
                    if built:
                        index[name] = built

        _EQUIPMENT_INDEX = index
        return index

    @classmethod
    def get_equipment_config(cls, item_name: str) -> Optional[dict]:
        """获取事件掉落装备的完整配置（供装备系统解析穿戴）"""
        if not item_name:
            return None
        return cls.get_equipment_index().get(item_name)

    @classmethod
    def _build_equipment_config(cls, name: str, entry: dict, stats: dict) -> Optional[dict]:
        """根据难度模板生成掉落装备配置"""
        entry_type = (entry or {}).get("type", "")
        if entry_type not in EQUIPMENT_TYPES:
            return None

        power = int(stats.get("power", 16))
        rank = entry.get("quality") or stats.get("rank", "凡品")

        config = {
            "id": f"world_event_equip_{name}",
            "name": name,
            "type": EQUIPMENT_TYPES[entry_type],
            "rank": rank,
            "required_level_index": int(stats.get("level_index", 0)),
            "description": f"世界事件掉落的{entry_type}，来历非凡。",
            "price": 0,
            "shop_weight": 0,
        }

        if entry_type == "武器":
            is_magic = any(keyword in name for keyword in MAGIC_WEAPON_KEYWORDS)
            sub_power = int(power * 0.6)
            config.update({
                "weapon_category": cls._guess_weapon_category(name),
                "magic_damage": power if is_magic else sub_power,
                "physical_damage": sub_power if is_magic else power,
                "mental_power": int(power * 0.3),
                "physical_defense": max(1, int(power * 0.1)),
            })
        elif entry_type == "防具":
            config.update({
                "physical_defense": int(power * 0.6),
                "magic_defense": int(power * 0.6),
                "mental_power": int(power * 0.2),
            })
        else:  # 饰品
            config.update({
                "mental_power": int(power * 0.6),
                "magic_damage": int(power * 0.2),
                "magic_defense": int(power * 0.2),
            })

        return config

    @staticmethod
    def _guess_weapon_category(name: str) -> str:
        """从装备名推断武器类别（仅用于展示）"""
        for suffix, category in WEAPON_CATEGORY_SUFFIXES:
            if name.endswith(suffix) or suffix in name:
                return category
        return "武器"

    # ==================== 事件读写 ====================

    @staticmethod
    def _row_to_event(row) -> Optional[dict]:
        if not row:
            return None
        event = {
            "event_id": row["event_id"],
            "group_id": str(row["group_id"]),
            "event_data": row["event_data"],
            "status": row["status"],
            "create_time": row["create_time"],
            "signup_end_time": row["signup_end_time"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "complete_time": row["complete_time"],
            "battle_node": WorldEventManager._row_field(row, "battle_node", 0),
            "battle_state": WorldEventManager._row_field(row, "battle_state", ""),
        }
        event["data"] = WorldEventManager.parse_event_data(event["event_data"])
        return event

    @staticmethod
    def _row_field(row, key: str, default):
        """安全读取 sqlite3.Row 字段（旧库未迁移时字段不存在，回退默认值）"""
        try:
            keys = row.keys()
        except Exception:
            return default
        if key not in keys:
            return default
        try:
            value = row[key]
        except Exception:
            return default
        return default if value is None else value

    @staticmethod
    def parse_event_data(event_data) -> dict:
        """解析事件 JSON 数据（容错）"""
        if isinstance(event_data, dict):
            return event_data
        try:
            parsed = json.loads(event_data or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    async def create_event(
        self,
        group_id: str,
        template: dict = None,
        tier_key: str = None,
        reward_multiplier: float = 1.0,
        admin_created: bool = False,
        intro: str = "",
    ) -> Tuple[bool, str, int]:
        """创建世界事件并进入报名阶段

        Returns:
            (是否成功, 消息, event_id)
        """
        group_id = str(group_id or "").strip()
        if not group_id:
            return False, "❌ 缺少目标群，无法开启世界事件", 0

        # 同一群同时只允许一场世界事件
        current = await self.get_current_event(group_id)
        if current:
            return False, "⚠️ 本群已有进行中的世界事件，请等待结束后再开启", 0

        template = template or self.get_random_template(tier_key)
        if not template:
            return False, "❌ 未找到可用的事件模板，请检查 config/world_events.json", 0

        tier_key = tier_key or self.get_template_tier_key(template)
        config = self._config()
        signup_duration = max(30, self._to_int(config.get("signup_duration_seconds"), 300))

        event_data = {
            "template_id": template.get("id"),
            "name": template.get("name", "未知事件"),
            "tier": template.get("tier", "低阶"),
            "tier_key": tier_key,
            "description": template.get("description", ""),
            "min_level": self._to_int(template.get("min_level"), 0),
            "max_level": self._to_int(template.get("max_level"), 0),
            "base_death_rate": self._to_float(template.get("base_death_rate"), 0.0),
            "duration_minutes": max(1, self._to_int(template.get("duration_minutes"), 30)),
            "rewards": template.get("rewards") or {},
            "bounty_tag": template.get("bounty_tag") or "",
            "reward_multiplier": round(max(0.1, self._to_float(reward_multiplier, 1.0)), 2),
            "admin_created": bool(admin_created),
            # 创建时生成的开场文案随快照落库，开战时直接复用（省掉每群一次 AI 调用）
            "intro": str(intro or ""),
        }

        now = int(time.time())
        try:
            cursor = await self.db.conn.execute(
                """
                INSERT INTO world_events
                (group_id, event_data, status, create_time, signup_end_time)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    json.dumps(event_data, ensure_ascii=False),
                    STATUS_SIGNUP,
                    now,
                    now + signup_duration,
                ),
            )
            event_id = cursor.lastrowid
            await self.db.conn.commit()
        except Exception as e:
            logger.error(f"[世界事件] 创建事件失败: {e}")
            return False, f"❌ 创建世界事件失败：{e}", 0

        logger.info(
            f"[世界事件] 群 {group_id} 开启事件【{event_data['name']}】"
            f"（{event_data['tier']}，报名 {signup_duration} 秒，event_id={event_id}）"
        )
        return True, event_data["name"], int(event_id or 0)

    async def get_event(self, event_id) -> Optional[dict]:
        """按ID获取事件"""
        try:
            async with self.db.conn.execute(
                "SELECT * FROM world_events WHERE event_id = ?",
                (int(event_id),),
            ) as cursor:
                row = await cursor.fetchone()
            return self._row_to_event(row)
        except Exception as e:
            logger.warning(f"[世界事件] 查询事件失败: {e}")
            return None

    async def get_current_event(self, group_id: str) -> Optional[dict]:
        """获取群内进行中的世界事件（报名中 / 战斗中）"""
        if not group_id:
            return None
        try:
            async with self.db.conn.execute(
                """
                SELECT * FROM world_events
                WHERE group_id = ? AND status IN (?, ?)
                ORDER BY event_id DESC LIMIT 1
                """,
                (str(group_id), STATUS_SIGNUP, STATUS_IN_PROGRESS),
            ) as cursor:
                row = await cursor.fetchone()
            return self._row_to_event(row)
        except Exception as e:
            logger.warning(f"[世界事件] 查询群事件失败: {e}")
            return None

    async def get_events_by_status(self, status: str, limit: int = 50) -> List[dict]:
        """按状态获取事件列表"""
        try:
            async with self.db.conn.execute(
                "SELECT * FROM world_events WHERE status = ? ORDER BY event_id ASC LIMIT ?",
                (status, int(limit)),
            ) as cursor:
                rows = await cursor.fetchall()
            return [self._row_to_event(row) for row in rows if row]
        except Exception as e:
            logger.warning(f"[世界事件] 查询事件列表失败: {e}")
            return []

    async def get_participants(self, event_id) -> List[str]:
        """获取事件参与者ID列表（按报名先后）"""
        try:
            async with self.db.conn.execute(
                "SELECT user_id FROM event_participants WHERE event_id = ? ORDER BY join_time ASC",
                (int(event_id),),
            ) as cursor:
                rows = await cursor.fetchall()
            return [str(row["user_id"]) for row in rows]
        except Exception as e:
            logger.warning(f"[世界事件] 查询参与者失败: {e}")
            return []

    async def is_participant(self, event_id, user_id: str) -> bool:
        """检查玩家是否已报名该事件"""
        try:
            async with self.db.conn.execute(
                "SELECT 1 FROM event_participants WHERE event_id = ? AND user_id = ? LIMIT 1",
                (int(event_id), str(user_id)),
            ) as cursor:
                return await cursor.fetchone() is not None
        except Exception:
            return False

    async def _set_participant_result(self, event_id, user_id: str, result: str):
        """记录参与者结算结果"""
        try:
            await self.db.conn.execute(
                "UPDATE event_participants SET result = ? WHERE event_id = ? AND user_id = ?",
                (result, int(event_id), str(user_id)),
            )
            await self.db.conn.commit()
        except Exception as e:
            logger.warning(f"[世界事件] 记录参与者结果失败: {e}")

    async def _save_participant_rewards(self, event_id, user_id: str, rewards: dict):
        """保存参与者的个人奖励明细（新增：用于后续查询）"""
        try:
            rewards_json = json.dumps(rewards, ensure_ascii=False)
            await self.db.conn.execute(
                "UPDATE event_participants SET rewards = ? WHERE event_id = ? AND user_id = ?",
                (rewards_json, int(event_id), str(user_id)),
            )
            await self.db.conn.commit()
        except Exception as e:
            logger.warning(f"[世界事件] 保存参与者奖励失败: {e}")

    async def _update_status(self, event_id, status: str, **fields):
        """更新事件状态（可附加字段）"""
        columns = ["status = ?"]
        params: List = [status]
        for column, value in fields.items():
            columns.append(f"{column} = ?")
            params.append(value)
        params.append(int(event_id))
        try:
            cursor = await self.db.conn.execute(
                f"UPDATE world_events SET {', '.join(columns)} WHERE event_id = ?",
                tuple(params),
            )
            await self.db.conn.commit()
            return cursor.rowcount
        except Exception as e:
            logger.error(f"[世界事件] 更新事件状态失败: {e}")
            return 0

    # ==================== 战斗状态（可见战斗过程） ====================

    @staticmethod
    def _load_battle_state(event: dict) -> Optional[dict]:
        """从事件行反序列化战斗状态；非战斗过程事件返回 None（走旧版结算路径）"""
        raw = (event or {}).get("battle_state")
        if not raw:
            return None
        try:
            state = json.loads(raw)
        except Exception:
            return None
        if not isinstance(state, dict) or not state.get("players"):
            return None
        return state

    async def _save_battle_state(self, event_id, node: int, state: dict) -> bool:
        """写入战斗状态（无并发保护，仅用于开战初始化与回合落地后回存）"""
        try:
            await self.db.conn.execute(
                "UPDATE world_events SET battle_node = ?, battle_state = ? WHERE event_id = ?",
                (int(node), json.dumps(state, ensure_ascii=False), int(event_id)),
            )
            await self.db.conn.commit()
            return True
        except Exception as e:
            logger.error(f"[世界事件] 保存战斗状态失败: {e}")
            return False

    async def _claim_battle_node(self, event_id, expected_node: int, new_node: int, state: dict) -> bool:
        """CAS 推进战斗回合：只有抢到「expected -> new」的调用者负责播报

        与结算的 ``status`` 原子占位同理，避免重复 tick / 重启重放导致同一个
        回合被播报两次。

        顺序说明：先占位再落地死亡（见 ``_advance_battle_node``）。若进程在
        占位后、死亡落地前崩掉，该回合的阵亡不会生效、状态里仍是存活——
        宁可少死也不会重复结算，符合"结算不可重复"的优先级。
        """
        try:
            cursor = await self.db.conn.execute(
                """
                UPDATE world_events SET battle_node = ?, battle_state = ?
                WHERE event_id = ? AND battle_node = ?
                """,
                (
                    int(new_node),
                    json.dumps(state, ensure_ascii=False),
                    int(event_id),
                    int(expected_node),
                ),
            )
            await self.db.conn.commit()
            return bool(cursor.rowcount)
        except Exception as e:
            logger.error(f"[世界事件] 推进战斗回合失败: {e}")
            return False

    async def _resolve_pending_deaths(self, event_id, record: dict) -> Tuple[List[dict], List[dict], List[dict]]:
        """落地本回合的阵亡：回生丹抵消 -> DeathManager -> 参与者结果写库

        Returns:
            (阵亡, 回生丹免死, 彻底陨落)
        """
        dead: List[dict] = []
        saved: List[dict] = []
        fallen: List[dict] = []

        for item in record.get("pending_deaths") or []:
            user_id = str(item.get("user_id") or "")
            if not user_id:
                continue
            player = await self.db.get_player_by_id(user_id)
            if not player:
                continue
            name = player.user_name or f"道友{user_id[:6]}"

            if await self._try_resurrect_pill(player):
                # 回生丹抵消本次阵亡（属性减半但活下来），按生还结算
                saved.append({"user_id": user_id, "name": name})
                await self._set_participant_result(event_id, user_id, RESULT_SURVIVOR)
                continue

            outcome = await self._apply_death(player)
            if outcome is not None and getattr(outcome, "is_permanent", False):
                # 彻底陨落：角色数据已删除，不再发放奖励
                fallen.append({"user_id": user_id, "name": name})
                await self._set_participant_result(event_id, user_id, RESULT_FALLEN)
                continue

            dead.append({
                "user_id": user_id,
                "name": name,
                "soul": bool(getattr(outcome, "is_soul", False)),
            })
            await self._set_participant_result(event_id, user_id, RESULT_DEAD)

        return dead, saved, fallen

    async def _advance_battle_node(self, event: dict, state: dict) -> Tuple[bool, str]:
        """推进一个战斗回合：掷骰 -> CAS 占位 -> 死亡落地 -> 生成播报

        Args:
            event: 事件行（含 event_id / data）
            state: 已反序列化的战斗状态，**就地修改**

        Returns:
            (是否推进成功, 群内播报文案；抢占失败时文案为空)
        """
        event_id = event["event_id"]
        event_data = event.get("data") or {}
        expected = int(state.get("resolved") or 0)

        # 1) 纯计算：掷骰、战意、敌方首领血量（不改变任何玩家数据）
        record = battle.resolve_node(event_data, state)

        # 2) CAS 占位 + 落库（此时尚未落地死亡，崩溃也不会重复结算）
        if not await self._claim_battle_node(event_id, expected, expected + 1, state):
            logger.info(
                f"[世界事件] 回合 {expected + 1} 已被其他调度推进，跳过播报（event_id={event_id}）"
            )
            return False, ""

        # 3) 死亡落地
        dead, saved, fallen = await self._resolve_pending_deaths(event_id, record)
        battle.apply_node_outcome(state, record, dead=dead, saved=saved, fallen=fallen)
        await self._save_battle_state(event_id, expected + 1, state)

        casualties = len(dead) + len(saved) + len(fallen)
        ai_text = await self._generate_battle_text(event_data, state, record, casualties)
        return True, battle.describe_node(event_data, state, record, ai_text)

    # ==================== 报名 ====================

    async def join_event(self, player: Player, event_id) -> Tuple[bool, str]:
        """加入世界事件（报名阶段）"""
        event = await self.get_event(event_id)
        if not event:
            return False, "❌ 事件不存在"

        if event["status"] != STATUS_SIGNUP:
            return False, "⏰ 该事件报名已结束"

        now = int(time.time())
        if event.get("signup_end_time") and now >= int(event["signup_end_time"]):
            return False, "⏰ 该事件报名已截止"

        event_data = event["data"]
        if getattr(player, "is_soul_state", False):
            return False, "⚠️ 元神状态无法参与世界事件，请先复活"

        level_index = self._to_int(player.level_index, 0)
        min_level = self._to_int(event_data.get("min_level"), 0)
        if level_index < min_level:
            return False, (
                f"❌ 境界不足：该事件最低需要【{self._get_level_name(min_level)}】\n"
                f"💡 你当前处于【{self._get_level_name(level_index)}】"
            )

        if await self.is_participant(event_id, player.user_id):
            return False, "⚠️ 你已经报名了本次世界事件"

        participants = await self.get_participants(event_id)
        max_participants = max(1, self._to_int(self._config().get("max_participants"), 10))
        if len(participants) >= max_participants:
            return False, f"⚠️ 报名人数已满（{max_participants}人）"

        try:
            cursor = await self.db.conn.execute(
                "INSERT OR IGNORE INTO event_participants (event_id, user_id, join_time) VALUES (?, ?, ?)",
                (int(event_id), str(player.user_id), now),
            )
            await self.db.conn.commit()
        except Exception as e:
            logger.error(f"[世界事件] 报名失败: {e}")
            return False, f"❌ 报名失败：{e}"

        if not cursor.rowcount:
            return False, "⚠️ 你已经报名了本次世界事件"

        current_count = len(participants) + 1
        preview = self.describe_reward_preview(player, event_data)
        return True, (
            f"✅ 报名成功！你已加入【{event_data.get('name', '世界事件')}】\n"
            f"{SEP}\n"
            f"👥 当前报名人数：{current_count}/{max_participants}\n"
            f"{self.describe_risk_breakdown(player, event_data)}\n"
            + (f"{SEP}\n{preview}\n" if preview else "")
            + "⏰ 报名截止后将自动开战，请留意群消息"
        )

    async def leave_event(self, player: Player, event_id) -> Tuple[bool, str]:
        """在报名阶段退出世界事件"""
        event = await self.get_event(event_id)
        if not event:
            return False, "❌ 事件不存在"

        if event["status"] != STATUS_SIGNUP:
            return False, "⚠️ 事件已开战，无法退出"

        if not await self.is_participant(event_id, player.user_id):
            return False, "⚠️ 你尚未报名本次世界事件"

        try:
            await self.db.conn.execute(
                "DELETE FROM event_participants WHERE event_id = ? AND user_id = ?",
                (int(event_id), str(player.user_id)),
            )
            await self.db.conn.commit()
        except Exception as e:
            return False, f"❌ 退出失败：{e}"

        return True, "✅ 已退出本次世界事件"

    async def cancel_event(self, event_id, reason: str = "") -> Tuple[bool, str]:
        """取消事件（人数不足 / 管理员操作）"""
        event = await self.get_event(event_id)
        if not event:
            return False, "❌ 事件不存在"

        if event["status"] not in ACTIVE_STATUSES:
            return False, "⚠️ 该事件已结束"

        await self._update_status(event_id, STATUS_CANCELLED, complete_time=int(time.time()))
        name = event["data"].get("name", "世界事件")
        suffix = f"\n💡 {reason}" if reason else ""
        return True, f"⚠️ 世界事件【{name}】已取消{suffix}"

    # ==================== 开战 / 结算 ====================

    async def start_event(self, event_id) -> Tuple[bool, str, dict]:
        """报名截止后开战（返回广播消息）"""
        event = await self.get_event(event_id)
        if not event:
            return False, "❌ 事件不存在", {}

        if event["status"] != STATUS_SIGNUP:
            return False, "⚠️ 事件状态异常", {}

        event_data = event["data"]
        name = event_data.get("name", "世界事件")
        participants = await self.get_participants(event_id)
        min_participants = max(1, self._to_int(self._config().get("min_participants"), 1))

        if len(participants) < min_participants:
            await self._update_status(event_id, STATUS_CANCELLED, complete_time=int(time.time()))
            return False, (
                f"⚠️ 世界事件【{name}】报名人数不足"
                f"（{len(participants)}/{min_participants}），已自动取消"
            ), {"cancelled": True}

        duration_minutes = max(1, self._to_int(event_data.get("duration_minutes"), 30))
        now = int(time.time())

        # 条件更新：避免重复开战（并发 / 重启后重放）
        try:
            cursor = await self.db.conn.execute(
                """
                UPDATE world_events
                SET status = ?, start_time = ?, end_time = ?
                WHERE event_id = ? AND status = ?
                """,
                (STATUS_IN_PROGRESS, now, now + duration_minutes * 60, int(event_id), STATUS_SIGNUP),
            )
            await self.db.conn.commit()
        except Exception as e:
            logger.error(f"[世界事件] 开战失败: {e}")
            return False, f"❌ 开战失败：{e}", {}

        if not cursor.rowcount:
            return False, "⚠️ 事件状态异常", {}

        # 可见战斗过程（v3.10.0）：按时长规划回合并落库初始状态。
        # 未启用时 battle_state 保持为空 -> 结算走旧版「整场一次掷骰」的路径。
        nodes = self._battle_node_plan(duration_minutes)
        staged = False
        broadcast = False
        if self._battle_process_enabled():
            snapshot = await self.build_battle_participants(event_id, event_data)
            if snapshot:
                state = battle.init_state(
                    event_data, snapshot, nodes, now, now + duration_minutes * 60
                )
                # 播报门槛只影响「发不发群消息」，战斗过程一律记录，
                # 否则单人事件将完全拿不到逐回合回放
                broadcast = self._battle_broadcast_enabled(len(snapshot))
                state["broadcast"] = broadcast
                await self._save_battle_state(event_id, 0, state)
                staged = True

        logger.info(
            f"[世界事件] 事件【{name}】(event_id={event_id}) 开战，"
            f"参与 {len(participants)} 人，持续 {duration_minutes} 分钟"
            f"{'，可见战斗过程 ' + str(nodes) + ' 回合' + ('（本场不播报）' if staged and not broadcast else '') if staged else '，整场一次掷骰结算'}"
        )

        # 开场文案：优先复用创建时的文案，省掉一次 AI 调用（v3.10.0 优化）。
        # 创建时参与人数为 0，开战时才知道实际人数，因此仅在人数文案不同时重生成。
        intro = event_data.get("intro") or await self.generate_intro_text(event_data, len(participants))
        lines = [
            "⚔️ 世界事件开战！",
            SEP,
            f"🌟 【{name}】",
        ]
        if intro:
            lines.append(intro)
        lines.extend([
            f"👥 参战道友：{len(participants)} 人",
            f"⏰ 预计 {duration_minutes} 分钟后结算",
        ])
        if staged:
            lines.append(
                f"🗺️ 此战分 {nodes} 个回合推进，战况将实时播报"
                if broadcast else
                f"🗺️ 此战分 {nodes} 个回合推进（人数较少，不刷屏播报，可用「{CMD_WORLD_EVENT_BATTLE}」回看）"
            )
        lines.append(f"💀 预估死亡率：{self._death_rate_display(event_data)}")
        lines.append(SEP)
        lines.append("🙏 祝各位道友旗开得胜，平安归来")
        msg = "\n".join(lines)
        return True, msg, {
            "participants": len(participants),
            "duration_minutes": duration_minutes,
            "staged": staged,
            "broadcast": broadcast,
            "nodes": nodes if staged else 0,
        }

    async def complete_event(self, event_id) -> Tuple[bool, str, dict]:
        """事件结算：计算死亡与奖励（返回广播消息）"""
        event = await self.get_event(event_id)
        if not event:
            return False, "❌ 事件不存在", {}

        if event["status"] != STATUS_IN_PROGRESS:
            return False, "⚠️ 事件状态异常", {}

        event_data = event["data"]
        name = event_data.get("name", "世界事件")

        # 先原子占位：只有抢到「in_progress -> completed」的调用者负责结算，
        # 避免重启 / 重复 tick 导致奖励重复发放
        now = int(time.time())
        try:
            cursor = await self.db.conn.execute(
                "UPDATE world_events SET status = ?, complete_time = ? WHERE event_id = ? AND status = ?",
                (STATUS_COMPLETED, now, int(event_id), STATUS_IN_PROGRESS),
            )
            await self.db.conn.commit()
        except Exception as e:
            logger.error(f"[世界事件] 结算占位失败: {e}")
            return False, f"❌ 事件结算失败：{e}", {}

        if not cursor.rowcount:
            return False, "⚠️ 该事件已被结算", {}

        participants = await self.get_participants(event_id)
        results = {
            "event_id": event["event_id"],
            "name": name,
            "tier": event_data.get("tier", ""),
            "participants": len(participants),
            "survivors": [],
            "deaths": [],
            "saved": [],
            "fallen": [],
            "rewards": {},
            "not_stored": [],
        }
        if not participants:
            return True, f"🌫️ 世界事件【{name}】无人参与，已悄然结束", results

        state = self._load_battle_state(event)
        if state:
            # 可见战斗过程：补算剩余回合后按战斗状态汇总（v3.10.0）
            await self._settle_staged_event(event, state, results)
        else:
            # 旧版路径：整场一次掷骰（战斗过程未启用，或事件创建于旧版本）
            await self._settle_legacy_event(event, participants, results)

        logger.info(
            f"[世界事件] 事件【{name}】(event_id={event_id}) 结算完成："
            f"生还 {len(results['survivors'])} / 回生丹免死 {len(results['saved'])} / "
            f"阵亡 {len(results['deaths'])} / 陨落 {len(results['fallen'])}"
            + (f"，历经 {results.get('stages')} 回合" if results.get("stages") else "")
        )

        # 总结文案：AI 生成（未开启 AI 时使用固定模板）
        summary = await self.generate_summary_text(results)
        return True, self.format_result_message(results, summary), results

    @staticmethod
    def _clamp_rate(value, default: float = 0.0) -> float:
        """把概率限制在 0~1（非法值回退默认值）"""
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return default

    def _get_major_realm(self, level_index: int) -> str:
        """获取大境界名称（例如：炼气期、筑基期、金丹期）"""
        level_data = getattr(self.config_manager, "level_data", None) or []
        try:
            index = int(level_index)
        except (TypeError, ValueError):
            return ""
        if 0 <= index < len(level_data):
            level_name = level_data[index].get("level_name", "")
            # 提取大境界名称（去掉"初期/中期/后期/一层/二层"等）
            for realm in ["炼气期", "筑基期", "金丹期", "元婴期", "化神期", "炼虚期", "合体期", "大乘期", "渡劫期"]:
                if realm in level_name:
                    return realm
            # 仙人境界直接返回
            if "仙" in level_name:
                return level_name
        return ""

    def _calc_death_rate_detail(
        self, player: Player, base_death_rate: float, min_level: int, max_level: int
    ) -> dict:
        """死亡率明细（v3.10.0 小说化模型）

        风险链：``最终 = 基准 × 境界优势 × 战力优势``，三项相乘、连续变化，
        不再是分档阶梯；低于 ``death_zero_threshold`` 直接归零（完全免死，不掷骰）。

        - **大境界压制**（v3.9.4 规则保留）：玩家大境界高于事件推荐**上限**的大境界
          → 完全免死。锚点仍取 ``max_level`` 而非 ``min_level``：若锚在门槛，
          炼虚期打「化神~合体」事件会直接免死，而该档奖励是皇品/帝品级，
          等于白送奖励，会破坏平衡。
        - **小境界优势**：每高出推荐**门槛**（``min_level``）1 级，风险乘
          ``death_minor_decay``（默认 0.72）。旧版把优势摊在整条推荐区间上，
          跨度 8 级时领先 1 级只值 6.25%，与小说体感严重不符。
        - **战力优势**：见 ``_calc_combat_power_reduction``，连续曲线。

        Returns:
            dict: base / final / realm_multiplier / realm_note /
                  combat_reduction / power_multiplier / immune / immune_reason
        """
        base = max(0.0, self._to_float(base_death_rate, 0.0))
        detail = {
            "base": base,
            "final": 0.0,
            "realm_multiplier": 1.0,
            "realm_note": "",
            "combat_reduction": 0.0,
            "power_multiplier": 1.0,
            "immune": False,
            "immune_reason": "",
        }
        if base <= 0.0:
            detail["immune"] = True
            detail["immune_reason"] = "该事件无生命风险"
            return detail

        config = self._config()
        level_index = self._to_int(getattr(player, "level_index", 0), 0)
        min_level = self._to_int(min_level, 0)
        max_level = self._to_int(max_level, 0)

        # 1) 大境界压制：高出一个大境界完全免死（v3.9.4 规则）
        if max_level > 0:
            player_realm = self._get_major_realm(level_index)
            max_realm = self._get_major_realm(max_level)
            player_rank = REALM_ORDER.index(player_realm) if player_realm in REALM_ORDER else -1
            max_rank = REALM_ORDER.index(max_realm) if max_realm in REALM_ORDER else -1
            if player_rank > max_rank and player_rank >= 0 and max_rank >= 0:
                detail["immune"] = True
                detail["realm_multiplier"] = 0.0
                detail["immune_reason"] = f"{player_realm}碾压{max_realm}，完全免死"
                return detail

        # 2) 小境界优势：以推荐门槛为满风险锚点
        minor_decay = self._to_float(
            config.get("death_minor_decay"),
            DEFAULT_WORLD_EVENT_CONFIG["death_minor_decay"],
        )
        minor_decay = min(0.99, max(0.05, minor_decay))

        gap = level_index - min_level
        if gap >= 0:
            realm_multiplier = minor_decay ** gap
            detail["realm_note"] = (
                f"高出推荐门槛 {gap} 级，每级 ×{minor_decay:g}" if gap else "恰在推荐门槛"
            )
        else:
            # 兜底：join_event 已拦截低于门槛的报名，正常不会走到这里
            under_mult = max(1.0, self._to_float(config.get("death_under_level_mult"), 1.35))
            under_cap = max(1.0, self._to_float(config.get("death_under_level_cap"), 2.5))
            realm_multiplier = min(under_cap, under_mult ** (-gap))
            detail["realm_note"] = f"低于推荐门槛 {-gap} 级，风险 ×{realm_multiplier:.1f}"
        detail["realm_multiplier"] = realm_multiplier

        # 3) 战力优势：连续曲线，战力越强越不容易死
        combat_reduction = self._calc_combat_power_reduction(player, level_index)
        detail["combat_reduction"] = combat_reduction
        detail["power_multiplier"] = max(0.0, 1.0 - combat_reduction)

        final = base * realm_multiplier * detail["power_multiplier"]

        zero_threshold = max(0.0, self._to_float(config.get("death_zero_threshold"), 0.015))
        if final < zero_threshold:
            detail["immune"] = True
            detail["immune_reason"] = f"风险已低于 {zero_threshold * 100:.1f}%，视同完全免死"
            return detail

        detail["final"] = self._clamp_rate(final)
        return detail

    def _calc_death_rate(self, player: Player, base_death_rate: float, min_level: int, max_level: int) -> float:
        """计算玩家本次事件的整场死亡率（实现见 ``_calc_death_rate_detail``）

        返回 0 表示无论骰子如何都不会阵亡。
        """
        return self._calc_death_rate_detail(
            player, base_death_rate, min_level, max_level
        )["final"]

    def _player_attr_combat_power(self, player: Player, level_index: int) -> Tuple[float, float]:
        """玩家的属性战力与同境界期望基准

        战力减免（``_calc_combat_power_reduction``）与事件输出能力
        （``_player_battle_power``）共用同一套口径，避免两处公式各自漂移。

        Returns:
            (combat_power, expected_base)；属性获取失败时回退为境界期望值
        """
        expected_base = 100 + max(0, self._to_int(level_index, 0)) * 50
        try:
            equipped_items = self._get_player_equipped_items(player)
            pill_multipliers = self._get_player_pill_multipliers(player)
            total_attrs = player.get_total_attributes(equipped_items, pill_multipliers)
        except Exception as e:
            logger.warning(f"[世界事件] 计算战力加成失败: {e}")
            return float(expected_base), float(expected_base)

        # 综合战力：物攻、法攻、物防、法防、精神力的加权平均
        total_attack = total_attrs.get("physical_damage", 0) + total_attrs.get("magic_damage", 0)
        total_defense = total_attrs.get("physical_defense", 0) + total_attrs.get("magic_defense", 0)
        mental = total_attrs.get("mental_power", 0)
        combat_power = total_attack * 0.4 + total_defense * 0.4 + mental * 0.2
        return float(combat_power), float(expected_base)

    def _calc_combat_power_reduction(self, player: Player, level_index: int) -> float:
        """根据玩家战力计算死亡率减免（装备、丹药buff 都有效）

        战力越高死亡率越低，且是**连续**的：战力达到同境界期望值的 2 倍时
        减免 50%，4 倍时减免 75%，上限由 ``combat_power_max_reduction`` 控制
        （v3.10.0 起默认 0.90，此前为 0.30）。
        """
        config = self._config()
        # 可配置：是否启用战力影响（默认启用）
        if not config.get("enable_combat_power_reduction", True):
            return 0.0

        combat_power, expected_base = self._player_attr_combat_power(player, level_index)
        combat_ratio = combat_power / max(expected_base, 1)

        max_reduction = self._to_float(config.get("combat_power_max_reduction"), 0.90)
        decay = self._to_float(config.get("combat_power_decay"), 1.0)
        return self._combat_power_reduction_from_ratio(combat_ratio, max_reduction, decay)

    def _get_player_equipped_items(self, player: Player) -> List:
        """获取玩家装备的物品列表（用于战力计算）"""
        from ..models import Item

        equipped = []
        equipment_manager = getattr(self, "equipment_manager", None)
        if not equipment_manager:
            # 未注入装备管理器，返回空列表
            return equipped

        try:
            # 获取武器
            if player.weapon:
                weapon = equipment_manager.get_equipment_by_name(player.weapon)
                if weapon:
                    equipped.append(weapon)

            # 获取防具
            if player.armor:
                armor = equipment_manager.get_equipment_by_name(player.armor)
                if armor:
                    equipped.append(armor)

            # 获取主修心法
            if player.main_technique:
                technique = equipment_manager.get_equipment_by_name(player.main_technique)
                if technique:
                    equipped.append(technique)

            # 获取功法列表
            for tech_name in player.get_techniques_list():
                tech = equipment_manager.get_equipment_by_name(tech_name)
                if tech:
                    equipped.append(tech)
        except Exception as e:
            logger.warning(f"[世界事件] 获取装备列表失败: {e}")

        return equipped

    def _get_player_pill_multipliers(self, player: Player) -> dict:
        """获取玩家当前丹药buff倍率（用于战力计算）"""
        try:
            from ..core.pill_manager import PillManager

            pill_manager = PillManager(self.db, self.config_manager)
            return pill_manager.get_active_multipliers(player)
        except Exception as e:
            logger.warning(f"[世界事件] 获取丹药buff失败: {e}")
            return {}

    async def _apply_death(self, player: Player):
        """触发批次1 的死亡机制（未注入 DeathManager 时按规则兜底）

        v3.11.0：世界事件阵亡按 ``death_exp_loss_scale`` 打折修为损失。境界与状态
        判定（劫后重生 / 元神 / 彻底陨落）完全沿用常规规则，只是把「死亡代价」调轻，
        让「进来刷复活道具材料」这件事不再天然亏修为。
        """
        scale = self._to_float(
            self._config().get("death_exp_loss_scale"),
            DEFAULT_WORLD_EVENT_CONFIG["death_exp_loss_scale"],
        )
        scale = max(0.0, min(1.0, scale))

        if self.death_manager is not None:
            return await self.death_manager.apply_death(player, exp_loss_scale=scale)

        # 兜底：DeathManager 未注入时按相同规则处理，避免事件结算失败
        from ..core.death_manager import DeathManager

        fallback = DeathManager(self.db, self.config_manager)
        return await fallback.apply_death(player, exp_loss_scale=scale)

    async def _try_resurrect_pill(self, player: Player) -> bool:
        """阵亡瞬间检查「回生丹」是否抵消本次死亡

        与突破走火入魔（``core/breakthrough_manager.py``）共用同一套丹药逻辑：
        回生丹消耗后属性减半，但玩家保住性命。
        """
        if not getattr(player, "has_resurrection_pill", False):
            return False

        try:
            from ..core.pill_manager import PillManager

            pill_manager = PillManager(self.db, self.config_manager)
            resurrected = await pill_manager.handle_resurrection(player)
        except Exception as e:
            logger.error(f"[世界事件] 回生丹结算失败: {e}")
            return False

        if resurrected:
            logger.info(f"[世界事件] 玩家 {player.user_id} 触发回生丹，抵消本次阵亡")
        return bool(resurrected)

    # ==================== 奖励重构（v3.11.0） ====================

    def _reward_ref_level(self, event_data: dict) -> int:
        """奖励基准境界：模板优先取 ``reward_ref_level``，缺省取推荐区间中点"""
        explicit = (event_data or {}).get("reward_ref_level")
        if explicit is not None:
            try:
                return int(explicit)
            except (TypeError, ValueError):
                pass
        min_level = self._to_int((event_data or {}).get("min_level"), 0)
        max_level = self._to_int((event_data or {}).get("max_level"), min_level)
        return (min_level + max_level) // 2

    def _reward_scale_for_player(self, player: Player, event_data: dict) -> float:
        """境界成长倍率：玩家每高出基准境界 1 级，修为/灵石奖励乘一个系数

        事件模板里的修为/灵石区间是「基准境界处」的数值，高境界玩家不再拿同一份
        固定奖励（这正是「合体期打事件只得同境界秘境 1/30」的根因）。
        低于基准境界不减益（保底 1.0），并受 ``reward_max_multiplier`` 封顶。
        """
        config = self._config()
        if not config.get("reward_level_scaling", True):
            return 1.0

        level_index = self._to_int(getattr(player, "level_index", 0), 0)
        gap = level_index - self._reward_ref_level(event_data)
        if gap <= 0:
            return 1.0

        # 成长系数：模板级 reward_level_growth 优先，缺省用全局配置
        # （各难度区间的秘境曲线斜率不同，模板级覆盖能让收益贴合同境界秘境）
        raw_growth = (event_data or {}).get("reward_level_growth")
        if raw_growth is None:
            raw_growth = config.get("reward_level_growth")
        growth = self._to_float(
            raw_growth,
            DEFAULT_WORLD_EVENT_CONFIG["reward_level_growth"],
        )
        growth = max(1.0, min(3.0, growth))
        cap = self._to_float(
            config.get("reward_max_multiplier"),
            DEFAULT_WORLD_EVENT_CONFIG["reward_max_multiplier"],
        )
        cap = max(1.0, cap)
        return min(cap, growth ** gap)

    @classmethod
    def _reward_range(cls, template_rewards: dict, key: str) -> Tuple[int, int]:
        """读取模板里的区间字段（自动纠正颠倒的上下限）"""
        value = (template_rewards or {}).get(key)
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return 0, 0
        low, high = cls._to_int(value[0], 0), cls._to_int(value[1], 0)
        if high < low:
            low, high = high, low
        return max(0, low), max(0, high)

    @classmethod
    def _material_count(cls, material: dict) -> int:
        """单次掉落的材料数量：支持 int 或 [min, max] 区间，非法值回退 1"""
        raw = (material or {}).get("count", 1)
        if isinstance(raw, (list, tuple)):
            if len(raw) < 2:
                return 1
            low, high = cls._to_int(raw[0], 1), cls._to_int(raw[1], 1)
            low, high = max(1, min(low, high)), max(1, max(low, high))
            return random.randint(low, high)
        return max(1, cls._to_int(raw, 1))

    @staticmethod
    def _format_amount(value) -> str:
        """把大数字打成「万 / 亿」，方便在事件面板里一眼看清收益量级"""
        try:
            amount = int(value)
        except (TypeError, ValueError):
            return "0"
        if abs(amount) >= 100_000_000:
            return f"{amount / 100_000_000:.2f}".rstrip("0").rstrip(".") + "亿"
        if abs(amount) >= 10_000:
            return f"{amount / 10_000:.1f}".rstrip("0").rstrip(".") + "万"
        return f"{amount:,}"

    def describe_reward_preview(self, player: Player, event_data: dict) -> str:
        """个人「预估收益」文案（含生还 / 期望两档，v3.11.0）

        玩家反馈的核心是「算不清值不值得去」。这里把生还收益与折算风险后的期望收益
        直接摆出来，让「值不值」变成一次心算就能判断的事。
        """
        template_rewards = (event_data or {}).get("rewards") or {}
        scale = self._reward_scale_for_player(player, event_data)
        exp_low, exp_high = self._reward_range(template_rewards, "exp")
        stone_low, stone_high = self._reward_range(template_rewards, "spirit_stone")
        base_multiplier = self._to_float((event_data or {}).get("reward_multiplier"), 1.0)

        if not (exp_high or stone_high):
            return ""

        retain = self._to_float(
            self._config().get("death_penalty_reward_rate"),
            DEFAULT_WORLD_EVENT_CONFIG["death_penalty_reward_rate"],
        )
        retain = max(0.0, min(1.0, retain))
        detail = self._calc_death_rate_detail(
            player,
            self._to_float((event_data or {}).get("base_death_rate"), 0.0),
            self._to_int((event_data or {}).get("min_level"), 0),
            self._to_int((event_data or {}).get("max_level"), 0),
        )
        # 期望系数 = 生还概率 × 1 + 阵亡概率 × 阵亡保留比例
        death_rate = 0.0 if detail["immune"] else detail["final"]
        expected_factor = (1.0 - death_rate) + death_rate * retain

        factor = scale * base_multiplier
        lines = []
        if exp_high:
            lines.append(
                f"✨ 修为 {self._format_amount(exp_low * factor)}-{self._format_amount(exp_high * factor)}"
            )
        if stone_high:
            lines.append(
                f"💰 灵石 {self._format_amount(stone_low * factor)}-{self._format_amount(stone_high * factor)}"
            )
        out = ["🎁 生还收益（按你的境界）：" + " · ".join(lines)]
        out.append(
            f"📈 折算风险后期望：约 {int(expected_factor * 100)}%"
            f"（阵亡保留 {int(retain * 100)}%）"
        )
        if scale > 1.0:
            out.append(f"   • 境界加成 ×{scale:.2f}（基准 {self._get_level_name(self._reward_ref_level(event_data))}）")
        guaranteed = self._to_int(
            self._config().get("reward_guaranteed_material"),
            DEFAULT_WORLD_EVENT_CONFIG["reward_guaranteed_material"],
        )
        if guaranteed > 0:
            out.append(f"   • 材料保底：至少 {guaranteed} 件（另有概率额外掉落）")
        return "\n".join(out)

    def _calculate_rewards(self, event_data: dict, multiplier: float, player: Optional[Player] = None) -> dict:
        """按事件模板随机计算奖励

        Args:
            multiplier: 阵亡惩罚 / 管理员奖励倍率（作用于修为与灵石）
            player: 参战玩家；传入后修为/灵石按境界成长、材料走保底与数量区间
                    （v3.11.0）。不传则退化为旧版固定区间行为。
        """
        template_rewards = event_data.get("rewards") or {}
        multiplier = max(0.0, multiplier)
        rewards = {
            "spirit_stone": 0,
            "exp": 0,
            "items": [],
            "recipes": [],
            "titles": [],
        }

        # v3.11.0：境界成长倍率（模板区间是「基准境界」处的数值）
        level_scale = self._reward_scale_for_player(player, event_data) if player else 1.0
        scale = max(0.0, multiplier) * level_scale

        stone_low, stone_high = self._reward_range(template_rewards, "spirit_stone")
        if stone_high:
            rewards["spirit_stone"] = int(random.randint(stone_low, stone_high) * scale)

        exp_low, exp_high = self._reward_range(template_rewards, "exp")
        if exp_high:
            rewards["exp"] = int(random.randint(exp_low, exp_high) * scale)

        # 材料：按 rate 掷骰，命中后按 count 区间给数量；记录件数用于保底补足
        material_pool: List[str] = []
        material_drops = 0
        for material in template_rewards.get("materials", []) or []:
            name = (material or {}).get("name")
            if not name:
                continue
            material_pool.append(name)
            if random.random() < self._to_float(material.get("rate"), 0.0):
                count = self._material_count(material)
                rewards["items"].extend([name] * count)
                material_drops += count

        guaranteed = max(0, self._to_int(
            self._config().get("reward_guaranteed_material"),
            DEFAULT_WORLD_EVENT_CONFIG["reward_guaranteed_material"],
        ))
        while material_drops < guaranteed and material_pool:
            name = random.choice(material_pool)
            rewards["items"].append(name)
            material_drops += 1

        for equipment in template_rewards.get("equipments", []) or []:
            name = (equipment or {}).get("name")
            if name and random.random() < self._to_float(equipment.get("rate"), 0.0):
                rewards["items"].append(name)

        for recipe in template_rewards.get("recipes", []) or []:
            recipe_id = self._to_int((recipe or {}).get("recipe_id"), 0)
            if recipe_id and random.random() < self._to_float(recipe.get("rate"), 0.0):
                rewards["recipes"].append(recipe_id)

        for special in template_rewards.get("special", []) or []:
            if str((special or {}).get("type", "")).lower() != "title":
                continue
            title = special.get("name")
            if title and random.random() < self._to_float(special.get("rate"), 0.0):
                rewards["titles"].append(title)

        return rewards

    async def _grant_rewards(
        self, user_id: str, rewards: dict, player: Optional[Player] = None
    ) -> Tuple[List[str], List[str]]:
        """发放奖励（灵石 / 修为 / 物品 / 配方 / 称号）

        Args:
            player: 已加载的玩家对象；不传则内部按 user_id 查询（避免重复查库）

        Returns:
            (成功入库的物品清单, 储物戒空间不足而丢失的物品清单)
        """
        stored: List[str] = []
        failed: List[str] = []

        if player is None:
            player = await self.db.get_player_by_id(user_id)
        if not player:
            return stored, failed

        stone = self._to_int(rewards.get("spirit_stone"), 0)
        exp = self._to_int(rewards.get("exp"), 0)
        if stone or exp:
            player.gold += stone
            player.experience += exp
            await self.db.update_player(player)
            # 师徒系统：徒弟参战获得收益时，师父获得传道奖励
            try:
                from .mentorship_manager import MentorshipManager

                mentorship_mgr = MentorshipManager(self.db, self.config_manager)
                await mentorship_mgr.grant_mentor_reward(player, exp, stone)
            except Exception as e:
                logger.warning(f"[世界事件] 传道奖励结算失败: {e}")

        # 配方：直接学习（还魂丹等 5 星配方必须学习后才能炼制）
        for recipe_id in rewards.get("recipes", []):
            try:
                await self.db.learn_recipe(user_id, int(recipe_id))
            except Exception as e:
                logger.warning(f"[世界事件] 学习配方 {recipe_id} 失败: {e}")

        for title in rewards.get("titles", []):
            try:
                await self.db.grant_title(user_id, title)
            except Exception as e:
                logger.warning(f"[世界事件] 授予称号 {title} 失败: {e}")

        for item_name in rewards.get("items", []):
            if self.storage is None:
                failed.append(item_name)
                continue
            try:
                ok, _ = await self.storage.store_item(player, item_name, 1, silent=True)
            except Exception as e:
                logger.warning(f"[世界事件] 发放物品 {item_name} 失败: {e}")
                ok = False
            if ok:
                stored.append(item_name)
            else:
                failed.append(item_name)

        return stored, failed

    async def _add_bounty_progress(self, user_id: str, tag: str, count: int = 1):
        """推进悬赏进度（需要时由 main.py 注入 bounty_manager）"""
        bounty_manager = getattr(self, "bounty_manager", None)
        if bounty_manager is None:
            return
        try:
            player = await self.db.get_player_by_id(user_id)
            if not player:
                return
            await bounty_manager.add_bounty_progress(player, tag, count)
        except Exception as e:
            logger.warning(f"[世界事件] 悬赏进度更新失败: {e}")


    # ==================== AI 文案（可选，批次5） ====================

    # 插件配置面板中的 AI 分组与键名映射
    AI_PLUGIN_GROUP = "AI_GENERATOR"
    AI_PLUGIN_KEY_MAP = {
        "ENABLE_AI_BATTLE": "enable_ai_battle",
        "ENABLE_AI_INTRO": "enable_ai_intro",
        "ENABLE_AI_SUMMARY": "enable_ai_summary",
        "AI_API_KEY": "ai_api_key",
        "AI_MODEL": "ai_model",
        "AI_BASE_URL": "ai_base_url",
        "FIXED_FALLBACK": "fixed_fallback",
    }

    def _ai_config(self) -> dict:
        """AI 文案配置：config/world_events.json 的 ai_config < AstrBot 插件配置面板 AI_GENERATOR"""
        config = dict(self._config().get("ai_config") or {})

        group = self._plugin_option(self.AI_PLUGIN_GROUP)
        if isinstance(group, dict):
            for key, value in group.items():
                if value is None:
                    continue
                internal = self.AI_PLUGIN_KEY_MAP.get(key, key)
                config[internal] = value
        return config

    def _get_ai_generator(self):
        """懒加载 AI 生成器（配置变更后重载插件即可生效）"""
        if self._ai_generator is None:
            try:
                from ..utils.ai_generator import AIGenerator

                self._ai_generator = AIGenerator(self._ai_config(), self.plugin_config)
            except Exception as e:
                logger.warning(f"[世界事件] AI 生成器初始化失败，将使用固定文案: {e}")
                self._ai_generator = False  # 标记为不可用，避免反复重试
        return self._ai_generator or None

    async def generate_intro_text(self, event_data: dict, participants: int = 0) -> str:
        """生成事件开场文案（AI 关闭 / 失败时回退固定模板，最终返回空串表示不加文案）"""
        generator = self._get_ai_generator()
        if generator is None:
            return ""
        try:
            return await asyncio.to_thread(
                generator.generate_event_intro,
                event_data.get("name", "世界事件"),
                event_data.get("tier", ""),
                participants,
                self._get_level_name(self._to_int(event_data.get("min_level"), 0)),
            ) or ""
        except Exception as e:
            logger.warning(f"[世界事件] AI 开场文案生成失败: {e}")
            return ""

    async def generate_summary_text(self, results: dict) -> str:
        """生成事件总结文案（AI 关闭 / 失败时回退固定模板）"""
        generator = self._get_ai_generator()
        if generator is None:
            return ""

        survivors = results.get("survivors") or []
        deaths = results.get("deaths") or []
        saved = results.get("saved") or []
        fallen = results.get("fallen") or []

        participants = [item.get("name", "") for item in (survivors + saved + deaths + fallen)]

        drop_counter: Dict[str, int] = {}
        for rewards in (results.get("rewards") or {}).values():
            for item in rewards.get("items", []):
                drop_counter[item] = drop_counter.get(item, 0) + 1
        rewards_summary = "、".join(f"{name}×{count}" for name, count in drop_counter.items())

        try:
            return await asyncio.to_thread(
                generator.generate_event_summary,
                results.get("name", "世界事件"),
                participants,
                deaths,
                survivors + saved,  # 回生丹幸存者一并按生还者提供给文案生成
                rewards_summary,
            ) or ""
        except Exception as e:
            logger.warning(f"[世界事件] AI 总结文案生成失败: {e}")
            return ""

    # ==================== 结算辅助 ====================

    async def _grant_event_rewards(
        self, event_id, user_id: str, event_data: dict, multiplier: float, results: dict
    ) -> None:
        """发放奖励并回写个人明细（可见战斗过程与旧版结算共用）"""
        player = await self.db.get_player_by_id(user_id)
        rewards = self._calculate_rewards(event_data, multiplier, player)
        stored, failed = await self._grant_rewards(user_id, rewards, player)
        rewards["stored"] = stored
        results["rewards"][str(user_id)] = rewards
        if failed:
            results["not_stored"].extend(failed)
        # 保存个人奖励明细到数据库，用于「我的世界事件奖励」查询
        await self._save_participant_rewards(event_id, user_id, rewards)

    async def _settle_legacy_event(self, event: dict, participants: List[str], results: dict) -> None:
        """旧版结算：整场一次掷骰

        保留此路径有两个作用：``battle_process_enabled=false`` 时行为与 v3.9.x
        完全一致；插件从旧版本升级时，已经在进行中的事件也能正常结算。
        """
        event_id = event["event_id"]
        event_data = event["data"]
        config = self._config()
        base_death_rate = self._to_float(event_data.get("base_death_rate"), 0.0)
        reward_multiplier = self._to_float(event_data.get("reward_multiplier"), 1.0)
        death_reward_rate = self._to_float(config.get("death_penalty_reward_rate"), 0.2)
        death_reward_rate = max(0.0, min(1.0, death_reward_rate))
        min_level = self._to_int(event_data.get("min_level"), 0)
        max_level = self._to_int(event_data.get("max_level"), 0)
        bounty_tag = str(event_data.get("bounty_tag") or "")

        for user_id in participants:
            player = await self.db.get_player_by_id(user_id)
            if not player:
                continue

            player_name = player.user_name or f"道友{str(user_id)[:6]}"
            death_rate = self._calc_death_rate(player, base_death_rate, min_level, max_level)
            # death_rate 为 0 表示已被境界压制完全免死，连骰子都不掷
            died = death_rate > 0 and random.random() < death_rate
            reward_rate = 1.0

            if died and await self._try_resurrect_pill(player):
                # 回生丹抵消本次阵亡（属性减半但活下来），按生还结算
                died = False
                results["saved"].append({"user_id": str(user_id), "name": player_name})

            if died:
                outcome = await self._apply_death(player)
                if outcome is not None and getattr(outcome, "is_permanent", False):
                    # 彻底陨落：角色数据已删除，不再发放奖励
                    results["fallen"].append({"user_id": str(user_id), "name": player_name})
                    await self._set_participant_result(event_id, user_id, RESULT_FALLEN)
                    continue
                results["deaths"].append({
                    "user_id": str(user_id),
                    "name": player_name,
                    "soul": bool(getattr(outcome, "is_soul", False)),
                })
                await self._set_participant_result(event_id, user_id, RESULT_DEAD)
                reward_rate = death_reward_rate
            else:
                results["survivors"].append({"user_id": str(user_id), "name": player_name})
                await self._set_participant_result(event_id, user_id, RESULT_SURVIVOR)

            await self._grant_event_rewards(
                event_id, user_id, event_data, reward_multiplier * reward_rate, results
            )

            # 生还者推进悬赏进度（世界事件标签，见 config/bounty_templates.json）
            if not died and bounty_tag:
                await self._add_bounty_progress(user_id, bounty_tag, 1)

    async def _settle_staged_event(self, event: dict, state: dict, results: dict) -> None:
        """可见战斗过程结算：补算剩余回合 -> 按战斗状态汇总 -> 发奖

        生死结果一律取自战斗过程中已落地的记录，不在这里重新掷骰，避免
        「过程里活着、结算时突然死了」这种自相矛盾。
        """
        event_id = event["event_id"]
        event_data = event["data"]
        config = self._config()
        reward_multiplier = self._to_float(event_data.get("reward_multiplier"), 1.0)
        death_reward_rate = self._to_float(config.get("death_penalty_reward_rate"), 0.2)
        death_reward_rate = max(0.0, min(1.0, death_reward_rate))
        bounty_tag = str(event_data.get("bounty_tag") or "")

        # 补算剩余回合：插件停机跨过整场战斗时，把没掷完的骰子一次补完，
        # 保证总死亡率守恒、且最终生死与战斗过程一致。
        nodes = int(state.get("nodes") or 1)
        while int(state.get("resolved") or 0) < nodes:
            record = battle.resolve_node(event_data, state)
            dead, saved, fallen = await self._resolve_pending_deaths(event_id, record)
            battle.apply_node_outcome(state, record, dead=dead, saved=saved, fallen=fallen)
        await self._save_battle_state(event_id, int(state.get("resolved") or 0), state)

        summary_state = battle.summarize_state(state)
        results["stages"] = summary_state["nodes"]
        results["boss_outcome"] = summary_state["boss_outcome"]
        results["mvp"] = summary_state.get("mvp")
        results["mvp_share"] = summary_state.get("mvp_share", 0.0)

        results["deaths"] = list(state.get("deaths") or [])
        results["saved"] = list(state.get("saved") or [])
        results["fallen"] = list(state.get("fallen") or [])
        dead_ids = {str(item.get("user_id")) for item in results["deaths"]}
        fallen_ids = {str(item.get("user_id")) for item in results["fallen"]}

        for user_id, player_state in (state.get("players") or {}).items():
            user_id = str(user_id)
            if user_id in fallen_ids:
                continue  # 彻底陨落：角色数据已删除，不再发放奖励
            name = str((player_state or {}).get("name") or f"道友{user_id[:6]}")
            died = user_id in dead_ids
            if died:
                reward_rate = death_reward_rate
            else:
                reward_rate = 1.0
                results["survivors"].append({"user_id": user_id, "name": name})
                # 阵亡者由 _resolve_pending_deaths 写库，生还者必须在这里补写，
                # 否则「世界事件战绩 / 战报 / 奖励」都会显示结果为「未知」
                await self._set_participant_result(event_id, user_id, RESULT_SURVIVOR)

            await self._grant_event_rewards(
                event_id, user_id, event_data, reward_multiplier * reward_rate, results
            )
            if not died and bounty_tag:
                await self._add_bounty_progress(user_id, bounty_tag, 1)

    # ==================== AI 战况叙事（P1） ====================

    def _fixed_battle_flavour(self, record: dict, casualties: int) -> str:
        """未接入 AI 时的回合氛围句（确定性输出，便于复现排查）"""
        if casualties > 0:
            return BATTLE_FLAVOUR_CASUALTY
        index = max(1, int(record.get("node") or 1)) - 1
        return BATTLE_FLAVOUR[index % len(BATTLE_FLAVOUR)]

    async def _generate_battle_text(
        self, event_data: dict, state: dict, record: dict, casualties: int
    ) -> str:
        """生成回合金氛围文案（AI 关闭 / 失败时回退固定文案池）

        缓存键为「模板 + 回合 + 伤亡档位」，**与群数无关**：一次自动生成会在多个
        群开同名事件，节点文案按模板生成一次即可全群复用，因此接入 AI 后的
        调用量不随群数线性膨胀（甚至低于改造前的 1 + 2N 次）。
        """
        key = (
            str(event_data.get("template_id") or event_data.get("name") or ""),
            int(record.get("node") or 0),
            int(record.get("total") or 0),
            0 if casualties <= 0 else (1 if casualties <= 2 else 2),
        )
        cached = self._battle_text_cache.get(key)
        if cached:
            return cached

        text = ""
        generator = self._get_ai_generator()
        if generator is not None:
            try:
                text = await asyncio.to_thread(
                    generator.generate_battle_stage,
                    event_data.get("name", "世界事件"),
                    event_data.get("tier", ""),
                    int(record.get("node") or 1),
                    int(record.get("total") or 1),
                    int(round(float(record.get("boss_hp", 1.0)) * 100)),
                    casualties,
                ) or ""
            except Exception as e:
                logger.warning(f"[世界事件] AI 战况文案生成失败: {e}")
                text = ""

        if text:
            self._battle_text_cache[key] = text
            return text
        return self._fixed_battle_flavour(record, casualties)

    # ==================== 定时推进 / 自动生成 ====================

    async def process_pending_events(self) -> List[Tuple[str, str]]:
        """推进所有事件（报名截止开战 / 战斗结束结算）

        Returns:
            需要广播的 (group_id, message) 列表
        """
        messages: List[Tuple[str, str]] = []
        now = int(time.time())

        for event in await self.get_events_by_status(STATUS_SIGNUP):
            signup_end = self._to_int(event.get("signup_end_time"), 0)
            if signup_end and now < signup_end:
                continue
            ok, msg, _ = await self.start_event(event["event_id"])
            if msg:
                messages.append((event["group_id"], msg))

        for event in await self.get_events_by_status(STATUS_IN_PROGRESS):
            end_time = self._to_int(event.get("end_time"), 0)
            state = self._load_battle_state(event)

            # 可见战斗过程：到点推进一个回合并播报战况。
            # 战斗已过结束时间时不再逐段播报，剩余回合由 complete_event 一次性
            # 补算——插件停机跨过整场战斗时，玩家不该被"补播"一串过场消息。
            if state and end_time and now < end_time:
                nodes = int(state.get("nodes") or 1)
                while int(state.get("resolved") or 0) < nodes:
                    index = int(state.get("resolved") or 0) + 1
                    deadline = battle.node_deadline(
                        self._to_int(event.get("start_time"), 0), end_time, nodes, index
                    )
                    if not deadline or now < deadline:
                        break
                    ok, node_msg = await self._advance_battle_node(event, state)
                    if not ok:
                        break
                    # 战斗过程照常记录；人数不足时不把回合消息发到群里
                    if node_msg and state.get("broadcast", True):
                        messages.append((event["group_id"], node_msg))

            if not end_time or now < end_time:
                continue
            ok, msg, _ = await self.complete_event(event["event_id"])
            if msg:
                messages.append((event["group_id"], msg))

        return messages

    async def auto_create_events(self, groups: List[str]) -> Tuple[bool, str, List[str]]:
        """在所有目标群开启同一主题的世界事件

        Returns:
            (是否成功, 消息, 已开启事件的群列表)
        """
        targets = [str(g).strip() for g in (groups or []) if str(g).strip()]
        if not targets:
            return False, "⚠️ 未配置事件群（broadcast_groups 与白名单群均为空）", []

        template = self.get_random_template(self.pick_tier_key())
        if not template:
            return False, "⚠️ 事件模板为空，请检查 config/world_events.json", []

        name = template.get("name", "世界事件")
        # 开场文案在循环外只生成一次，随事件快照落库；开战时直接复用，
        # 避免此前「每群开战各调用一次 AI」的 N 倍浪费（v3.10.0 优化）
        intro = await self.generate_intro_text(
            {
                "name": name,
                "tier": template.get("tier", ""),
                "min_level": template.get("min_level", 0),
            },
            0,
        )

        created: List[str] = []
        for group_id in targets:
            ok, _, _ = await self.create_event(group_id=group_id, template=template, intro=intro)
            if ok:
                created.append(group_id)

        if not created:
            return False, "⚠️ 所有群均已有进行中的世界事件，本次跳过", []

        lines = [
            f"🌟 天地异动，世界事件【{name}】开启！",
            SEP,
            intro or f"📜 {template.get('description', '')}",
            SEP,
            f"⚔️ 难度：{template.get('tier', '未知')}"
            f"（推荐 {self._get_level_name(self._to_int(template.get('min_level'), 0))}+）",
            f"⏰ 报名时间有限，发送「{CMD_JOIN_WORLD_EVENT}」速速前往参与",
        ]
        return True, "\n".join(lines), created

    # ==================== 展示 ====================

    def format_event_broadcast(self, event: dict, participants: int = 0, intro: str = "") -> str:
        """事件报名广播文案（``intro`` 为可选的 AI 开场描述）"""
        event_data = self.parse_event_data(event.get("event_data"))
        max_participants = max(1, self._to_int(self._config().get("max_participants"), 10))
        remaining = max(0, self._to_int(event.get("signup_end_time"), 0) - int(time.time()))
        admin_tag = "🎯 【管理员特别活动】\n" if event_data.get("admin_created") else ""

        lines = [
            f"{SEP} 世界事件 {SEP}",
            admin_tag.rstrip("\n") if admin_tag else None,
            f"🌟 【{event_data.get('name', '世界事件')}】",
            intro or f"📜 {event_data.get('description', '')}",
            SEP,
            f"⚔️ 难度：{event_data.get('tier', '未知')}",
            f"📊 推荐境界：{self._get_level_name(self._to_int(event_data.get('min_level'), 0))}"
            f" - {self._get_level_name(self._to_int(event_data.get('max_level'), 0))}",
            f"💀 预估死亡率：{self._death_rate_display(event_data)}",
            f"🎁 奖励倍率：{self._to_float(event_data.get('reward_multiplier'), 1.0)}x",
            f"⏰ 报名剩余：{self.format_duration(remaining)}",
            SEP,
            f"发送「{CMD_JOIN_WORLD_EVENT}」参与战斗！",
            f"当前报名人数：{participants}/{max_participants}",
            SEP,
        ]
        return "\n".join(line for line in lines if line)

    def format_event_progress(self, event: dict) -> str:
        """事件进行中的状态文案"""
        event_data = self.parse_event_data(event.get("event_data"))
        return (
            f"⚔️ 【{event_data.get('name', '世界事件')}】进行中\n"
            f"{SEP}\n"
            f"⏰ 剩余时间：{self.format_duration(self._remaining_seconds(event))}\n"
            f"💀 预估死亡率：{self._death_rate_display(event_data)}\n"
            f"{SEP}\n"
            f"🎁 奖励结算后公布"
        )

    def format_result_message(self, results: dict, summary: str = "") -> str:
        """事件结算广播文案"""
        lines = [
            f"{SEP} 事件结算 {SEP}",
            f"🌟 【{results.get('name', '世界事件')}】",
            f"👥 参战道友：{results.get('participants', 0)} 人",
        ]
        if results.get("stages"):
            lines.append(
                f"🗺️ 历经 {int(results['stages'])} 回合鏖战，"
                f"{results.get('boss_outcome', '战事已了')}"
            )
        mvp = results.get("mvp") or None
        if mvp and mvp.get("name") and int(results.get("participants") or 0) > 1:
            share = int(round(float(results.get("mvp_share") or 0) * 100))
            lines.append(f"⚡ 本战主力：{mvp['name']}（输出占 {share}%）")
        lines.append(SEP)

        survivors = results.get("survivors") or []
        deaths = results.get("deaths") or []
        saved = results.get("saved") or []
        fallen = results.get("fallen") or []

        if summary:
            lines.append(summary)
            lines.append(SEP)
        if survivors:
            names = "、".join(item["name"] for item in survivors)
            lines.append(f"✅ 生还（全额奖励）：{names}")
        if deaths:
            names = "、".join(
                f"{item['name']}（{'元神' if item.get('soul') else '劫后重生'}）" for item in deaths
            )
            lines.append(f"💀 阵亡（{int(self._to_float(self._config().get('death_penalty_reward_rate'), 0.2) * 100)}% 奖励）：{names}")
        if saved:
            names = "、".join(item["name"] for item in saved)
            lines.append(f"🛡️ 回生丹显灵·免于一死（全额奖励）：{names}")
        if fallen:
            names = "、".join(item["name"] for item in fallen)
            lines.append(f"🪦 彻底陨落：{names}")
        if not (survivors or saved or deaths or fallen):
            lines.append("🌫️ 无人参与本次事件")

        # 统计本次掉落的稀有物品
        drop_counter: Dict[str, int] = {}
        for rewards in (results.get("rewards") or {}).values():
            for item in rewards.get("items", []):
                drop_counter[item] = drop_counter.get(item, 0) + 1

        if drop_counter:
            lines.append(SEP)
            lines.append("🎁 稀有掉落：" + "、".join(
                f"{name}×{count}" if count > 1 else name for name, count in drop_counter.items()
            ))
        if any((r.get("recipes") for r in (results.get("rewards") or {}).values())):
            lines.append("📜 有道友参悟了稀有丹方（请到「丹药配方」查看）")
        if any((r.get("titles") for r in (results.get("rewards") or {}).values())):
            lines.append("🎖️ 有道友获得稀有称号（请到「我的信息」查看）")

        not_stored = results.get("not_stored") or []
        if not_stored:
            unique = list(dict.fromkeys(not_stored))
            lines.append(f"⚠️ 储物戒已满，未能入库：{'、'.join(unique)}")

        lines.append(SEP)
        lines.append(f"💡 「{CMD_WORLD_EVENT_BATTLE}」看逐回合回放 · 「{CMD_WORLD_EVENT_REWARDS}」看奖励明细")
        return "\n".join(lines)

    def format_player_history(self, history: List[dict]) -> str:
        """玩家历史战绩文案"""
        if not history:
            return (
                "📜 你还没有参加过世界事件\n"
                f"{SEP}\n"
                "💡 群内出现世界事件时发送「加入世界事件」即可参与"
            )

        result_labels = {
            RESULT_SURVIVOR: "✅ 生还",
            RESULT_DEAD: "💀 阵亡",
            RESULT_FALLEN: "🪦 陨落",
        }
        lines = ["📜 世界事件战绩（最近）", SEP]
        for record in history:
            event_data = self.parse_event_data(record.get("event_data"))
            status = result_labels.get(record.get("result"), "❔ 未结算")
            date_text = time.strftime("%m-%d %H:%M", time.localtime(self._to_int(record.get("join_time"), 0)))
            lines.append(
                f"{status} 【{event_data.get('name', '世界事件')}】"
                f"（{event_data.get('tier', '未知')}）{date_text}"
            )
        lines.append(SEP)
        return "\n".join(lines)

    async def format_player_rewards(self, user_id: str, event_id: int = None) -> str:
        """查看玩家在最近世界事件中获得的个人奖励明细（新增功能）"""
        try:
            if event_id is not None:
                # 查询指定事件的奖励
                async with self.db.conn.execute(
                    """
                    SELECT e.event_data, p.rewards, p.result
                    FROM event_participants p
                    JOIN world_events e ON e.event_id = p.event_id
                    WHERE p.user_id = ? AND p.event_id = ?
                    """,
                    (str(user_id), int(event_id)),
                ) as cursor:
                    row = await cursor.fetchone()
            else:
                # 查询最近一次完成的事件的奖励
                async with self.db.conn.execute(
                    """
                    SELECT e.event_data, p.rewards, p.result
                    FROM event_participants p
                    JOIN world_events e ON e.event_id = p.event_id
                    WHERE p.user_id = ? AND e.status = ? AND p.rewards IS NOT NULL
                    ORDER BY e.complete_time DESC LIMIT 1
                    """,
                    (str(user_id), STATUS_COMPLETED),
                ) as cursor:
                    row = await cursor.fetchone()

            if not row:
                return (
                    "📭 暂无世界事件奖励记录\n"
                    f"{SEP}\n"
                    "💡 参加世界事件并完成结算后可查看个人奖励明细"
                )

            event_data = self.parse_event_data(row["event_data"])
            rewards_json = row["rewards"]
            result = row["result"]

            if not rewards_json:
                return (
                    f"🌟 【{event_data.get('name', '世界事件')}】\n"
                    f"{SEP}\n"
                    "⚠️ 该事件暂无奖励记录"
                )

            rewards = json.loads(rewards_json)
            result_labels = RESULT_LABELS

            lines = [
                "🎁 世界事件个人奖励",
                SEP,
                f"🌟 【{event_data.get('name', '世界事件')}】",
                f"📊 结算结果：{result_labels.get(result, '❔ 未知')}",
                SEP,
            ]

            # 灵石和修为
            stone = self._to_int(rewards.get("spirit_stone"), 0)
            exp = self._to_int(rewards.get("exp"), 0)
            if stone > 0:
                lines.append(f"💰 灵石：+{stone:,}")
            if exp > 0:
                lines.append(f"✨ 修为：+{exp:,}")

            # 物品掉落（v3.11.0：材料支持数量区间，按名称聚合展示「名称×N」）
            items = rewards.get("items", []) or []
            item_counter = Counter(items)
            if item_counter:
                lines.append(SEP)
                lines.append("📦 物品掉落：")
                for item, count in item_counter.items():
                    lines.append(f"  • {item}×{count}" if count > 1 else f"  • {item}")

            # 配方
            recipes = rewards.get("recipes", [])
            if recipes:
                lines.append(SEP)
                lines.append("📜 丹方：")
                for recipe_id in recipes:
                    lines.append(f"  • 配方 #{recipe_id}")

            # 称号
            titles = rewards.get("titles", [])
            if titles:
                lines.append(SEP)
                lines.append("🎖️ 称号：")
                for title in titles:
                    lines.append(f"  • {title}")

            # 未入库物品（按名称做数量差集，避免同名物品「一存一漏」时误报）
            stored = rewards.get("stored", []) or []
            failed_counter = item_counter - Counter(stored)
            if failed_counter:
                lines.append(SEP)
                lines.append("⚠️ 储物戒已满，未能入库：")
                for item, count in failed_counter.items():
                    lines.append(f"  • {item}×{count}" if count > 1 else f"  • {item}")

            lines.append(SEP)
            if not (stone or exp or items or recipes or titles):
                lines.append("💬 本次事件未获得奖励")
            else:
                retained = self._to_float(
                    self._config().get("death_penalty_reward_rate"),
                    DEFAULT_WORLD_EVENT_CONFIG["death_penalty_reward_rate"],
                )
                if result == RESULT_DEAD:
                    lines.append(f"💡 本次阵亡，修为/灵石按 {int(retained * 100)}% 结算（掉落不受影响）")
                lines.append(f"💡 收益随境界成长：高出事件基准境界后，修为/灵石按倍率提升")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"[世界事件] 查询玩家奖励失败: {e}")
            return f"❌ 查询奖励失败：{e}"

    async def format_battle_report(self, user_id: str) -> str:
        """个人视角战报：最近一场世界事件的逐回合回放（v3.10.0）

        群内回合播报是「公共视角」，这里补上「我的视角」——每个玩家都能看到
        自己每一回合做了什么、战意剩多少、在哪一回合倒下的。
        """
        try:
            async with self.db.conn.execute(
                """
                SELECT e.event_id, e.event_data, e.battle_state, e.complete_time,
                       p.result
                FROM event_participants p
                JOIN world_events e ON e.event_id = p.event_id
                WHERE p.user_id = ? AND e.status = ?
                ORDER BY e.event_id DESC LIMIT 1
                """,
                (str(user_id), STATUS_COMPLETED),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                return (
                    "📭 暂无世界事件战报\n"
                    f"{SEP}\n"
                    "💡 参加过世界事件并完成结算后可查看逐回合回放"
                )

            event_data = self.parse_event_data(row["event_data"])
            name = event_data.get("name", "世界事件")
            result = row["result"]

            lines = [
                "📜 世界事件战报 · 我的视角",
                SEP,
                f"🌟 【{name}】（{event_data.get('tier', '未知')}）",
            ]

            state = self._load_battle_state({"battle_state": row["battle_state"]})
            if not state:
                # 未启用可见战斗过程（或旧版本结算的事件）
                lines.append(f"📊 结算结果：{RESULT_LABELS.get(result, '❔ 未知')}")
                lines.append(SEP)
                lines.append("💡 该场事件未启用可见战斗过程（插件配置 → 世界事件配置 → 启用可见战斗过程）")
                return "\n".join(lines)

            summary = battle.summarize_state(state)
            lines.append(
                f"🗺️ 共 {summary['nodes']} 回合 · {summary['boss_outcome']}"
            )
            lines.append(SEP)

            player_state = (state.get("players") or {}).get(str(user_id)) or {}
            for note in player_state.get("notes") or []:
                lines.append(f"【第{note.get('node')}回合】{note.get('text', '')}")

            if not player_state.get("notes"):
                lines.append("🌫️ 没有你的回合记录")

            lines.append(SEP)
            lines.append(f"📊 结算结果：{RESULT_LABELS.get(result, '❔ 未知')}")

            mvp = summary.get("mvp") or None
            if mvp and mvp.get("name") and len(state.get("players") or {}) > 1:
                share = int(round(float(summary.get("mvp_share") or 0) * 100))
                lines.append(f"⚡ 本战主力：{mvp['name']}（输出占 {share}%）")

            deaths = summary.get("deaths") or []
            if deaths:
                names = "、".join(str(item.get("name") or "") for item in deaths)
                lines.append(f"💀 此战阵亡：{names}")

            # 风险系数透明化：让玩家知道自己为什么安全 / 危险
            player = await self.db.get_player_by_id(user_id)
            if player:
                lines.append(SEP)
                lines.append(self.describe_risk_breakdown(player, event_data))

            lines.append(SEP)
            lines.append(f"💡 「{CMD_WORLD_EVENT_REWARDS}」查看奖励明细 · 「{CMD_WORLD_EVENT_RECORD}」查看更多战绩")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"[世界事件] 查询个人战报失败: {e}")
            return f"❌ 查询战报失败：{e}"

    async def get_player_history(self, user_id: str, limit: int = 8) -> List[dict]:
        """获取玩家最近的世界事件记录"""
        try:
            async with self.db.conn.execute(
                """
                SELECT p.join_time, p.result, e.event_data
                FROM event_participants p
                JOIN world_events e ON e.event_id = p.event_id
                WHERE p.user_id = ?
                ORDER BY p.event_id DESC LIMIT ?
                """,
                (str(user_id), int(limit)),
            ) as cursor:
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.warning(f"[世界事件] 查询玩家战绩失败: {e}")
            return []

    # ==================== 工具 ====================

    @staticmethod
    def format_duration(seconds: int) -> str:
        """秒数 -> 中文时长"""
        total = max(0, int(seconds))
        minutes, sec = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}小时{minutes}分钟"
        if minutes:
            return f"{minutes}分{sec}秒"
        return f"{sec}秒"

    @staticmethod
    def _remaining_seconds(event: dict) -> int:
        """事件剩余秒数（根据当前状态取报名截止或结束时间）"""
        now = int(time.time())
        target = event.get("signup_end_time") if event.get("status") == STATUS_SIGNUP else event.get("end_time")
        try:
            return max(0, int(target) - now)
        except (TypeError, ValueError):
            return 0

    def describe_event(
        self, event: dict, participants: int = 0, joined: bool = False, player: Optional[Player] = None
    ) -> str:
        """「世界事件」指令的详情面板（v3.11.0 起带个人预估收益）"""
        if not event:
            return (
                "🌫️ 当前没有世界事件\n"
                f"{SEP}\n"
                "💡 事件会自动生成并广播到群，发送「世界事件帮助」查看玩法"
            )

        event_data = event["data"]
        max_participants = max(1, self._to_int(self._config().get("max_participants"), 10))
        lines = [
            f"🌟 世界事件：【{event_data.get('name', '世界事件')}】",
            SEP,
            f"📜 {event_data.get('description', '')}",
            SEP,
            f"⚔️ 难度：{event_data.get('tier', '未知')}",
            f"📊 推荐境界：{self._get_level_name(self._to_int(event_data.get('min_level'), 0))}"
            f" - {self._get_level_name(self._to_int(event_data.get('max_level'), 0))}",
            f"💀 预估死亡率：{self._death_rate_display(event_data)}",
            f"🎁 奖励倍率：{self._to_float(event_data.get('reward_multiplier'), 1.0)}x",
            f"👥 报名人数：{participants}/{max_participants}",
        ]

        if player is not None:
            preview = self.describe_reward_preview(player, event_data)
            if preview:
                lines.append(SEP)
                lines.append(preview)

        if event.get("status") == STATUS_SIGNUP:
            lines.append(f"⏰ 报名剩余：{self.format_duration(self._remaining_seconds(event))}")
            lines.append(SEP)
            if joined:
                lines.append("✅ 你已报名，战斗结束后自动结算")
                lines.append(f"💡 反悔可发送「{CMD_LEAVE_WORLD_EVENT}」")
            else:
                lines.append(f"发送「{CMD_JOIN_WORLD_EVENT}」参与战斗")
        else:
            lines.append(f"⏰ 战斗剩余：{self.format_duration(self._remaining_seconds(event))}")
            state = self._load_battle_state(event)
            if state:
                resolved = int(state.get("resolved") or 0)
                nodes = int(state.get("nodes") or 1)
                boss_hp = int(round(float(state.get("boss_hp", 1.0)) * 100))
                lines.append(
                    f"🗺️ 战况进度：第 {resolved}/{nodes} 回合"
                    f" · {battle.boss_label(event_data)}气血 {boss_hp}%"
                )
            lines.append(SEP)
            if joined:
                lines.append("⚔️ 你正在战斗中，结束后自动结算死亡与奖励")
            else:
                lines.append("⚔️ 战斗进行中，本次未参战")

        return "\n".join(lines)

    # ==================== 管理员功能（批次5） ====================

    # ==================== 管理员鉴权 ====================

    def _plugin_option(self, key: str):
        """读取 AstrBot 插件配置的顶层键（兼容旧版写法）"""
        if self.plugin_config is None or not hasattr(self.plugin_config, "get"):
            return None
        try:
            return self.plugin_config.get(key, None)
        except Exception:
            return None

    @staticmethod
    def _as_str_list(value) -> List[str]:
        """把配置值规整为字符串列表"""
        if value is None:
            return []
        if isinstance(value, (str, int)):
            value = [value]
        if not isinstance(value, (list, tuple, set)):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def get_admin_users(self) -> List[str]:
        """获取世界事件管理员列表

        优先级：插件配置面板 WORLD_EVENT.ADMIN_USERS
                > 插件配置顶层 world_event_admin_users（旧写法）
                > config/world_events.json 的 admin_users
        """
        admins = self._as_str_list(self._config().get("admin_users"))
        if admins:
            return admins
        return self._as_str_list(self._plugin_option("world_event_admin_users"))

    def get_broadcast_groups(self) -> List[str]:
        """获取事件广播/目标群列表（同上优先级）"""
        groups = self._as_str_list(self._config().get("broadcast_groups"))
        if groups:
            return groups
        return self._as_str_list(self._plugin_option("world_event_broadcast_groups"))

    def is_admin(self, user_id: str) -> bool:
        """检查用户是否为世界事件管理员"""
        return str(user_id) in self.get_admin_users()

    async def create_admin_event(
        self,
        admin_id: str,
        tier: str,
        group_id: str,
        custom_name: str = None,
        custom_death_rate: float = None,
        reward_multiplier: float = 2.0,
    ) -> Tuple[bool, str, Optional[int]]:
        """管理员创建世界事件

        Args:
            admin_id: 管理员用户ID
            tier: 难度（low/mid/high/epic）
            group_id: 目标群号
            custom_name: 自定义事件名称（可选）
            custom_death_rate: 自定义死亡率（可选，0.0-1.0）
            reward_multiplier: 奖励倍率（默认2.0）

        Returns:
            (是否成功, 消息, 事件ID)
        """
        if not self.is_admin(admin_id):
            return False, "⚠️ 你没有管理员权限", None

        # 允许的群号：插件配置面板 > 插件顶层旧键 > config/world_events.json
        allowed_groups = self.get_broadcast_groups()
        if allowed_groups and str(group_id) not in allowed_groups:
            return False, f"❌ 群号 {group_id} 不在允许列表中", None

        tier_map = {
            "low": "low_tier",
            "mid": "mid_tier",
            "high": "high_tier",
            "epic": "epic_tier",
        }
        tier_key = tier_map.get(tier)
        if not tier_key:
            return False, f"❌ 无效的难度：{tier}（可选：low/mid/high/epic）", None

        template = self.get_random_template(tier_key)
        if not template:
            tier_name = {"low": "低阶", "mid": "中阶", "high": "高阶", "epic": "史诗"}.get(tier, tier)
            return False, f"❌ 没有找到{tier_name}难度的事件模板", None

        template = dict(template)
        if custom_name:
            template["name"] = str(custom_name)
        if custom_death_rate is not None:
            try:
                rate = float(custom_death_rate)
                if 0.0 <= rate <= 1.0:
                    template["base_death_rate"] = rate
            except (TypeError, ValueError):
                pass

        event_success, event_name, event_id = await self.create_event(
            template=template,
            group_id=str(group_id),
            reward_multiplier=float(reward_multiplier),
            admin_created=True,
        )

        if not event_success or not event_id:
            return False, "❌ 创建事件失败，请稍后重试", None

        tier_name = template.get("tier", "未知")
        lines = [
            "✅ 世界事件创建成功！",
            SEP,
            f"📋 事件ID：{event_id}",
            f"🌟 {template['name']}",
            f"⚔️ 难度：{tier_name}",
            f"💀 死亡率：{int(template['base_death_rate'] * 100)}%",
            f"🎁 奖励倍率：{reward_multiplier}x",
            f"📢 目标群：{group_id}",
            SEP,
            "事件已创建，将在报名期结束后自动开始",
            "💡 发送「查看世界事件」查看所有活动事件",
        ]
        return True, "\n".join(lines), event_id

    async def list_event_templates(self) -> str:
        """列出所有事件模板（管理员）"""
        templates = self._templates()
        if not templates:
            return "❌ 没有可用的事件模板"

        tier_names = {
            "low_tier": "低阶",
            "mid_tier": "中阶",
            "high_tier": "高阶",
            "epic_tier": "史诗",
        }

        lines = ["📜 世界事件模板列表", SEP]
        idx = 1
        for tier_key in ["low_tier", "mid_tier", "high_tier", "epic_tier"]:
            tier_list = templates.get(tier_key, [])
            if not tier_list:
                continue

            tier_name = tier_names.get(tier_key, tier_key)
            lines.append(f"\n【{tier_name}】")
            for template in tier_list:
                name = template.get("name", "未命名")
                death_rate = int((template.get("base_death_rate", 0) or 0) * 100)
                min_level = self._get_level_name(template.get("min_level", 0))
                lines.append(f"{idx}. {name}（死亡率{death_rate}%，推荐{min_level}+）")
                idx += 1

        lines.append(f"\n{SEP}")
        lines.append("💡 使用方法：创建世界事件 <难度> <群号> [奖励倍率]")
        lines.append("💡 难度：低阶/中阶/高阶/史诗")
        lines.append("💡 例如：创建世界事件 高阶 123456789 3.0")
        return "\n".join(lines)

    async def get_active_events(self) -> str:
        """查看所有进行中的事件（管理员）"""
        try:
            events = await self.db.list_world_events(
                status_filter=["signup", "in_progress"],
                limit=20,
            )
        except Exception as e:
            logger.error(f"[世界事件] 查询活动事件失败: {e}")
            return "❌ 查询失败，请稍后重试"

        if not events:
            return f"📭 当前没有进行中的事件\n{SEP}\n💡 发送「世界事件模板」查看可创建的事件"

        lines = ["📊 活动事件列表", SEP]
        for event in events:
            event_id = event.get("event_id")
            group_id = event.get("group_id", "未知")
            status = event.get("status", "unknown")
            event_data = event.get("event_data", {})

            if isinstance(event_data, str):
                try:
                    event_data = json.loads(event_data)
                except Exception:
                    event_data = {}

            name = event_data.get("name", "未命名事件")
            tier = event_data.get("tier", "未知")
            status_text = {"signup": "报名中", "in_progress": "进行中"}.get(status, status)
            admin_tag = "🎯 " if event_data.get("admin_created") else ""

            lines.append(f"\n#{event_id} {admin_tag}{name}")
            lines.append(f"  群号：{group_id}｜难度：{tier}｜状态：{status_text}")

        lines.append(f"\n{SEP}")
        lines.append("💡 发送「结束世界事件 <事件ID>」可强制结束事件")
        return "\n".join(lines)

    async def force_end_event(self, admin_id: str, event_id: int) -> Tuple[bool, str]:
        """管理员强制结束事件"""
        if not self.is_admin(admin_id):
            return False, "⚠️ 你没有管理员权限"

        try:
            event_id = int(event_id)
        except (TypeError, ValueError):
            return False, "❌ 事件ID必须是数字"

        event = await self.db.get_world_event(event_id)
        if not event:
            return False, f"❌ 事件 #{event_id} 不存在"

        status = event.get("status")
        if status not in ["signup", "in_progress"]:
            status_text = {"completed": "已完成", "cancelled": "已取消"}.get(status, status)
            return False, f"❌ 事件 #{event_id} 已{status_text}，无法强制结束"

        try:
            await self.db.cancel_world_event(event_id)
        except Exception as e:
            logger.error(f"[世界事件] 强制结束事件失败: {e}")
            return False, f"❌ 结束事件失败：{e}"

        event_data = event.get("event_data", {})
        if isinstance(event_data, str):
            try:
                event_data = json.loads(event_data)
            except Exception:
                event_data = {}

        name = event_data.get("name", "未命名事件")
        return True, (
            f"✅ 事件已强制结束\n"
            f"{SEP}\n"
            f"📋 事件ID：{event_id}\n"
            f"🌟 {name}\n"
            f"📢 群号：{event.get('group_id', '未知')}\n"
            f"{SEP}\n"
            "事件已取消，参与者无需结算"
        )
