# managers/world_event_manager.py
"""世界事件系统管理器（批次4：替代历练系统）

多人协作的随机事件：定时任务（或批次5 的管理员）在群内发布事件 → 玩家报名 →
报名截止自动开战 → 结束后统一结算死亡与奖励。

设计要点：
- 事件按群独立：每个群一份报名名单与结算结果，同名事件可在多个群同时开启
- 阵亡会触发批次1 的死亡机制（低阶「劫后重生」/ 金丹以上「元神状态」）
- 幸存者拿全额奖励，阵亡者按 death_penalty_reward_rate（默认 20%）结算
- 奖励含稀有炼丹材料（九转仙草 / 太古龙骨 / 灵髓精华 / 月华精粹），用于炼制还魂丹
"""

import json
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from astrbot.api import logger

from ..data.data_manager import DataBase
from ..models import Player

__all__ = ["WorldEventManager"]

SEP = "━━━━━━━━━━━━━━━"


# 指令名（handlers / main.py / 管理器展示共用，避免多处硬编码漂移）
CMD_WORLD_EVENT = "世界事件"
CMD_JOIN_WORLD_EVENT = "加入世界事件"
CMD_LEAVE_WORLD_EVENT = "退出世界事件"
CMD_WORLD_EVENT_RECORD = "世界事件战绩"

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

    def __init__(self, db: DataBase, config_manager=None, storage_ring_manager=None, death_manager=None):
        self.db = db
        self.config_manager = config_manager
        self.storage = storage_ring_manager
        self.death_manager = death_manager

    # ==================== 配置 ====================

    def _config(self) -> dict:
        """获取世界事件核心参数（配置缺失时回退默认值）"""
        getter = getattr(self.config_manager, "get_world_event_config", None)
        if callable(getter):
            try:
                config = getter() or {}
                if isinstance(config, dict) and config:
                    return config
            except Exception as e:
                logger.warning(f"[世界事件] 读取配置失败，使用默认值: {e}")
        return {
            "auto_generate": True,
            "auto_interval_minutes": [120, 240],
            "max_participants": 10,
            "min_participants": 1,
            "signup_duration_seconds": 300,
            "death_penalty_reward_rate": 0.2,
            "auto_tier_weights": {"low_tier": 40, "mid_tier": 30, "high_tier": 20, "epic_tier": 10},
            "admin_users": [],
            "broadcast_groups": [],
        }

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
        return f"境界{index}"

    # ==================== 装备掉落配置 ====================

    @classmethod
    def get_equipment_index(cls) -> Dict[str, dict]:
        """事件掉落装备名 -> 完整装备配置（结果缓存）"""
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
        }
        event["data"] = WorldEventManager.parse_event_data(event["event_data"])
        return event

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
        return True, (
            f"✅ 报名成功！你已加入【{event_data.get('name', '世界事件')}】\n"
            f"{SEP}\n"
            f"👥 当前报名人数：{current_count}/{max_participants}\n"
            f"💀 预估死亡率：{int(self._to_float(event_data.get('base_death_rate'), 0) * 100)}%\n"
            f"⏰ 报名截止后将自动开战，请留意群消息"
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

        logger.info(
            f"[世界事件] 事件【{name}】(event_id={event_id}) 开战，"
            f"参与 {len(participants)} 人，持续 {duration_minutes} 分钟"
        )

        msg = (
            f"⚔️ 世界事件开战！\n"
            f"{SEP}\n"
            f"🌟 【{name}】\n"
            f"👥 参战道友：{len(participants)} 人\n"
            f"⏰ 预计 {duration_minutes} 分钟后结算\n"
            f"💀 预估死亡率：{int(self._to_float(event_data.get('base_death_rate'), 0) * 100)}%\n"
            f"{SEP}\n"
            f"🙏 祝各位道友旗开得胜，平安归来"
        )
        return True, msg, {"participants": len(participants), "duration_minutes": duration_minutes}

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
            "fallen": [],
            "rewards": {},
            "not_stored": [],
        }
        if not participants:
            return True, f"🌫️ 世界事件【{name}】无人参与，已悄然结束", results

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
            died = random.random() < death_rate
            reward_rate = 1.0

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

            rewards = self._calculate_rewards(event_data, reward_multiplier * reward_rate)
            stored, failed = await self._grant_rewards(user_id, rewards)
            rewards["stored"] = stored
            results["rewards"][str(user_id)] = rewards
            if failed:
                results["not_stored"].extend(failed)

            # 生还者推进悬赏进度（世界事件标签，见 config/bounty_templates.json）
            if not died and bounty_tag:
                await self._add_bounty_progress(user_id, bounty_tag, 1)

        logger.info(
            f"[世界事件] 事件【{name}】(event_id={event_id}) 结算完成："
            f"生还 {len(results['survivors'])} / 阵亡 {len(results['deaths'])} / "
            f"陨落 {len(results['fallen'])}"
        )

        return True, self.format_result_message(results), results

    def _calc_death_rate(self, player: Player, base_death_rate: float, min_level: int, max_level: int) -> float:
        """计算玩家本次事件的死亡率（境界越低越危险）"""
        death_rate = base_death_rate
        level_index = self._to_int(player.level_index, 0)
        if level_index < min_level:
            death_rate += 0.2
        elif level_index < (min_level + max_level) // 2:
            death_rate += 0.1
        return max(0.0, min(1.0, death_rate))

    async def _apply_death(self, player: Player):
        """触发批次1 的死亡机制（未注入 DeathManager 时按规则兜底）"""
        if self.death_manager is not None:
            return await self.death_manager.apply_death(player)

        # 兜底：DeathManager 未注入时按相同规则处理，避免事件结算失败
        from ..core.death_manager import DeathManager

        fallback = DeathManager(self.db, self.config_manager)
        return await fallback.apply_death(player)

    def _calculate_rewards(self, event_data: dict, multiplier: float) -> dict:
        """按事件模板随机计算奖励（multiplier 已包含阵亡惩罚比例）"""
        template_rewards = event_data.get("rewards") or {}
        multiplier = max(0.0, multiplier)
        rewards = {
            "spirit_stone": 0,
            "exp": 0,
            "items": [],
            "recipes": [],
            "titles": [],
        }

        stone_range = template_rewards.get("spirit_stone")
        if isinstance(stone_range, (list, tuple)) and len(stone_range) >= 2:
            low, high = self._to_int(stone_range[0], 0), self._to_int(stone_range[1], 0)
            if high < low:
                low, high = high, low
            rewards["spirit_stone"] = int(random.randint(low, high) * multiplier)

        exp_range = template_rewards.get("exp")
        if isinstance(exp_range, (list, tuple)) and len(exp_range) >= 2:
            low, high = self._to_int(exp_range[0], 0), self._to_int(exp_range[1], 0)
            if high < low:
                low, high = high, low
            rewards["exp"] = int(random.randint(low, high) * multiplier)

        for material in template_rewards.get("materials", []) or []:
            name = (material or {}).get("name")
            if name and random.random() < self._to_float(material.get("rate"), 0.0):
                rewards["items"].append(name)

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

    async def _grant_rewards(self, user_id: str, rewards: dict) -> Tuple[List[str], List[str]]:
        """发放奖励（灵石 / 修为 / 物品 / 配方 / 称号）

        Returns:
            (成功入库的物品清单, 储物戒空间不足而丢失的物品清单)
        """
        stored: List[str] = []
        failed: List[str] = []

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

        created: List[str] = []
        for group_id in targets:
            ok, _, _ = await self.create_event(group_id=group_id, template=template)
            if ok:
                created.append(group_id)

        if not created:
            return False, "⚠️ 所有目标群均已有进行中的世界事件，本次跳过", []

        name = template.get("name", "世界事件")
        return True, (
            f"🌟 天地异动，世界事件【{name}】开启！\n"
            f"{SEP}\n"
            f"📍 目标群：{len(created)} 个\n"
            f"⏰ 报名时间有限，速速前往参与"
        ), created

    # ==================== 展示 ====================

    def format_event_broadcast(self, event: dict, participants: int = 0) -> str:
        """事件报名广播文案"""
        event_data = self.parse_event_data(event.get("event_data"))
        max_participants = max(1, self._to_int(self._config().get("max_participants"), 10))
        remaining = max(0, self._to_int(event.get("signup_end_time"), 0) - int(time.time()))
        admin_tag = "🎯 【管理员特别活动】\n" if event_data.get("admin_created") else ""

        return (
            f"{SEP} 世界事件 {SEP}\n"
            f"{admin_tag}"
            f"🌟 【{event_data.get('name', '世界事件')}】\n"
            f"📜 {event_data.get('description', '')}\n"
            f"{SEP}\n"
            f"⚔️ 难度：{event_data.get('tier', '未知')}\n"
            f"📊 推荐境界：{self._get_level_name(self._to_int(event_data.get('min_level'), 0))}"
            f" - {self._get_level_name(self._to_int(event_data.get('max_level'), 0))}\n"
            f"💀 预估死亡率：{int(self._to_float(event_data.get('base_death_rate'), 0) * 100)}%\n"
            f"🎁 奖励倍率：{self._to_float(event_data.get('reward_multiplier'), 1.0)}x\n"
            f"⏰ 报名剩余：{self.format_duration(remaining)}\n"
            f"{SEP}\n"
            f"发送「{CMD_JOIN_WORLD_EVENT}」参与战斗！\n"
            f"当前报名人数：{participants}/{max_participants}\n"
            f"{SEP}"
        )

    def format_event_progress(self, event: dict) -> str:
        """事件进行中的状态文案"""
        event_data = self.parse_event_data(event.get("event_data"))
        return (
            f"⚔️ 【{event_data.get('name', '世界事件')}】进行中\n"
            f"{SEP}\n"
            f"⏰ 剩余时间：{self.format_duration(self._remaining_seconds(event))}\n"
            f"💀 预估死亡率：{int(self._to_float(event_data.get('base_death_rate'), 0) * 100)}%\n"
            f"{SEP}\n"
            f"🎁 奖励结算后公布"
        )

    def format_result_message(self, results: dict) -> str:
        """事件结算广播文案"""
        lines = [
            f"{SEP} 事件结算 {SEP}",
            f"🌟 【{results.get('name', '世界事件')}】",
            f"👥 参战道友：{results.get('participants', 0)} 人",
            SEP,
        ]

        survivors = results.get("survivors") or []
        deaths = results.get("deaths") or []
        fallen = results.get("fallen") or []

        if survivors:
            names = "、".join(item["name"] for item in survivors)
            lines.append(f"✅ 生还（全额奖励）：{names}")
        if deaths:
            names = "、".join(
                f"{item['name']}（{'元神' if item.get('soul') else '劫后重生'}）" for item in deaths
            )
            lines.append(f"💀 阵亡（{int(self._to_float(self._config().get('death_penalty_reward_rate'), 0.2) * 100)}% 奖励）：{names}")
        if fallen:
            names = "、".join(item["name"] for item in fallen)
            lines.append(f"🪦 彻底陨落：{names}")
        if not (survivors or deaths or fallen):
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

    def describe_event(self, event: dict, participants: int = 0, joined: bool = False) -> str:
        """「世界事件」指令的详情面板"""
        if not event:
            return (
                "🌫️ 当前没有世界事件\n"
                f"{SEP}\n"
                "💡 事件会自动生成并广播到群，发送「世界事件帮助」查看玩法"
            )

        max_participants = max(1, self._to_int(self._config().get("max_participants"), 10))
        lines = [
            f"🌟 世界事件：【{event_data.get('name', '世界事件')}】",
            SEP,
            f"📜 {event_data.get('description', '')}",
            SEP,
            f"⚔️ 难度：{event_data.get('tier', '未知')}",
            f"📊 推荐境界：{self._get_level_name(self._to_int(event_data.get('min_level'), 0))}"
            f" - {self._get_level_name(self._to_int(event_data.get('max_level'), 0))}",
            f"💀 预估死亡率：{int(self._to_float(event_data.get('base_death_rate'), 0) * 100)}%",
            f"🎁 奖励倍率：{self._to_float(event_data.get('reward_multiplier'), 1.0)}x",
            f"👥 报名人数：{participants}/{max_participants}",
        ]

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
            lines.append(SEP)
            if joined:
                lines.append("⚔️ 你正在战斗中，结束后自动结算死亡与奖励")
            else:
                lines.append("⚔️ 战斗进行中，本次未参战")

        return "\n".join(lines)
