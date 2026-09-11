# managers/alchemy_manager.py
"""炼丹系统管理器（批次2：丹药星级 / 稀有配方学习 / 境界与称号加成）

核心规则：
- 丹药分 1-5 星，星级定义在 ``config/alchemy_recipes.json``；商店默认只售 1-2 星丹药
- 5 星与标记 ``learnable`` 的配方需要先学习（可通过世界事件 / 宗门功法库等途径获得）
- 成功率 = 基础成功率 + 境界加成（金丹+10% / 化神+20% / 合体+30%）+ 炼丹师称号加成（+15%），上限 95%
- 炼制失败返还 50% 材料，小概率产出「废丹」
"""

import random
from typing import Tuple, List, Dict, Optional, TYPE_CHECKING

from ..data.data_manager import DataBase
from ..models import Player
from ..models_extended import UserStatus

if TYPE_CHECKING:
    from ..config_manager import ConfigManager
    from ..core import StorageRingManager


class AlchemyManager:
    """炼丹系统管理器"""

    # 炼丹师称号（批次3 委托炼丹系统会围绕该称号展开）
    ALCHEMIST_TITLE = "炼丹师"
    STAR_MAX = 5

    # 炼丹核心参数的兜底默认值（配置缺失时使用，与 config/alchemy_config.json 保持一致）
    DEFAULT_SETTINGS: Dict = {
        "level_success_bonus": {"13": 0.10, "19": 0.20, "25": 0.30},
        "alchemist_bonus": 0.15,
        "alchemist_extra_pill_chance": 0.1,
        "material_refund_rate": 0.5,
        "waste_pill_chance": 0.05,
        "max_success_rate": 0.95,
        "max_shop_pill_star": 2,
        "waste_pill_name": "废丹",
    }

    # 材料获取途径配置（集中管理，避免到处硬编码）
    MATERIAL_SOURCES = {
        "灵草": [
            {"name": "历练系统", "cmd": "/历练 短途", "desc": "30分钟，掉落1-3个"},
            {"name": "灵田系统", "cmd": "/种植 灵草", "desc": "1小时成熟后收获"},
        ],
        "精铁": [
            {"name": "历练系统", "cmd": "/历练 短途", "desc": "30分钟，低概率掉落"},
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "可直接购买"},
        ],
        "灵石碎片": [
            {"name": "历练系统", "cmd": "/历练 短途", "desc": "30分钟，掉落2-5个"},
        ],
        "嗜血草": [
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架，售价昂贵"},
            {"name": "世界事件", "cmd": "/世界事件", "desc": "中阶事件掉落（批次4开放）"},
        ],
        "妖兽内丹": [
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架"},
            {"name": "世界事件", "cmd": "/世界事件", "desc": "中阶事件掉落（批次4开放）"},
        ],
        "金刚石": [
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架"},
            {"name": "世界事件", "cmd": "/世界事件", "desc": "中阶事件掉落（批次4开放）"},
        ],
        "悟道草": [
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架，售价昂贵"},
            {"name": "世界事件", "cmd": "/世界事件", "desc": "高阶事件掉落（批次4开放）"},
        ],
        "菩提叶": [
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架"},
            {"name": "世界事件", "cmd": "/世界事件", "desc": "高阶事件掉落（批次4开放）"},
        ],
        "护心草": [
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架，售价昂贵"},
            {"name": "世界事件", "cmd": "/世界事件", "desc": "高阶事件掉落（批次4开放）"},
        ],
        "神木精华": [
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架"},
            {"name": "世界事件", "cmd": "/世界事件", "desc": "高阶事件掉落（批次4开放）"},
        ],
        "九转仙草": [
            {"name": "世界事件", "cmd": "/世界事件", "desc": "史诗事件稀有掉落（批次4开放）"},
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架，售价极贵"},
        ],
        "太古龙骨": [
            {"name": "世界事件", "cmd": "/世界事件", "desc": "史诗事件稀有掉落（批次4开放）"},
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架，售价极贵"},
        ],
        "灵髓精华": [
            {"name": "世界事件", "cmd": "/世界事件", "desc": "高阶/史诗事件掉落（批次4开放）"},
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架"},
        ],
        "月华精粹": [
            {"name": "世界事件", "cmd": "/世界事件", "desc": "高阶/史诗事件掉落（批次4开放）"},
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "试炼期临时上架"},
        ],
        "default": [
            {"name": "历练系统", "cmd": "/历练", "desc": "通过历练获取"},
            {"name": "灵田系统", "cmd": "/种植", "desc": "种植灵草类材料"},
            {"name": "百宝阁", "cmd": "/百宝阁", "desc": "部分材料可直接购买"},
            {"name": "世界事件", "cmd": "/世界事件", "desc": "高阶材料主要产出（批次4开放）"},
        ],
    }

    def __init__(
        self,
        db: DataBase,
        config_manager: "ConfigManager" = None,
        storage_ring_manager: "StorageRingManager" = None,
    ):
        self.db = db
        self.config_manager = config_manager
        self.storage_ring_manager = storage_ring_manager

        # 炼丹核心参数（兼容嵌套 / 扁平两种配置写法）
        if config_manager and hasattr(config_manager, "get_alchemy_config"):
            self.config = config_manager.get_alchemy_config()
        elif config_manager and isinstance(config_manager.alchemy_config, dict):
            self.config = dict(config_manager.alchemy_config.get("alchemy_config") or {})
        else:
            self.config = {}

        raw_recipes = {}
        if config_manager and getattr(config_manager, "alchemy_recipes", None):
            raw_recipes = config_manager.alchemy_recipes

        self.recipes: Dict[int, Dict] = {}
        for recipe in raw_recipes.values():
            if isinstance(recipe, dict) and recipe.get("id"):
                recipe_id = int(recipe["id"])
                self.recipes[recipe_id] = self._normalize_recipe(recipe_id, recipe)

    # ===== 配置读取 =====

    def get_setting(self, key: str, default=None):
        """读取炼丹核心参数（文件配置 > 内置默认值）"""
        if isinstance(self.config, dict) and self.config.get(key) is not None:
            return self.config[key]
        if key in self.DEFAULT_SETTINGS:
            return self.DEFAULT_SETTINGS[key]
        return default

    def refresh_config(self):
        """配置热更新后重新读取炼丹参数"""
        if self.config_manager and hasattr(self.config_manager, "get_alchemy_config"):
            self.config = self.config_manager.get_alchemy_config()

    # ===== 配方 =====

    def _normalize_recipe(self, recipe_id: int, recipe: Dict) -> Dict:
        """标准化配方字段，兼容不同格式的配置"""
        name = recipe.get("name", f"丹药{recipe_id}")

        desc = recipe.get("desc", None)
        if not desc and self.config_manager:
            pill_config = self._get_pill_config_by_name(name)
            if pill_config:
                desc = self._generate_pill_desc(pill_config)
        if not desc:
            desc = "丹药效果"

        star = self._to_star(recipe.get("star"))
        materials = dict(recipe.get("materials", recipe.get("cost", {})) or {})

        return {
            "id": recipe.get("id", recipe_id),
            "name": name,
            "star": star,
            "level_required": recipe.get("level_required", recipe.get("level", 0)),
            "materials": materials,
            "success_rate": recipe.get("success_rate", recipe.get("success", 50)),
            "desc": desc,
            "learnable": bool(recipe.get("learnable", False)),
            "effect_type": recipe.get("effect_type", ""),
            "buff_duration": recipe.get("buff_duration", 0),
            "buff_effect": dict(recipe.get("buff_effect") or {}),
            "exp_gain": recipe.get("exp_gain", 0),
        }

    @staticmethod
    def _to_star(value) -> int:
        """星级规整为 1-5 的整数"""
        try:
            star = int(value)
        except (TypeError, ValueError):
            return 1
        return min(max(star, 1), AlchemyManager.STAR_MAX)

    @staticmethod
    def star_text(star: int) -> str:
        """星级展示文本"""
        star = min(max(int(star or 1), 1), AlchemyManager.STAR_MAX)
        return "⭐" * star

    def get_recipe(self, recipe_id) -> Optional[Dict]:
        """按配方ID获取配方"""
        try:
            recipe_id = int(recipe_id)
        except (TypeError, ValueError):
            return None
        return self.recipes.get(recipe_id)

    def get_recipe_star(self, recipe_id) -> int:
        """获取配方星级"""
        recipe = self.get_recipe(recipe_id)
        return recipe.get("star", 1) if recipe else 1

    def get_pill_star(self, pill_name: str) -> int:
        """获取丹药星级（配方优先，其次丹药配置，兜底 1 星）"""
        if self.config_manager and hasattr(self.config_manager, "get_pill_star"):
            return self.config_manager.get_pill_star(pill_name)
        for recipe in self.recipes.values():
            if recipe.get("name") == pill_name:
                return recipe.get("star", 1)
        return 1

    def is_learnable_recipe(self, recipe_id) -> bool:
        """检查配方是否需要学习后才能炼制（标记 learnable 或 5 星配方）"""
        recipe = self.get_recipe(recipe_id)
        if not recipe:
            return False
        return bool(recipe.get("learnable")) or recipe.get("star", 1) >= self.STAR_MAX

    def get_recipes_by_star(self, star: int) -> List[Dict]:
        """获取指定星级的配方列表"""
        try:
            star = int(star)
        except (TypeError, ValueError):
            return []
        return [r for r in self.sorted_recipes() if r.get("star", 1) == star]

    def sorted_recipes(self) -> List[Dict]:
        """按星级、配方ID排序后的配方列表"""
        return sorted(self.recipes.values(), key=lambda r: (r.get("star", 1), int(r.get("id", 0))))

    def get_max_shop_pill_star(self) -> int:
        """商店最高可售丹药星级"""
        if self.config_manager and hasattr(self.config_manager, "get_max_shop_pill_star"):
            return self.config_manager.get_max_shop_pill_star()
        return int(self.get_setting("max_shop_pill_star", 2) or 2)

    # ===== 丹药描述 =====

    def _generate_pill_desc(self, pill_config: Dict) -> str:
        """根据丹药配置生成描述"""
        rank = pill_config.get("rank", "")

        if pill_config.get("exp_gain"):
            return f"增加{pill_config['exp_gain']}修为（{rank}修为丹）"

        if pill_config.get("breakthrough_bonus"):
            bonus = int(pill_config["breakthrough_bonus"] * 100)
            return f"提升{bonus}%突破成功率（{rank}破境丹）"

        if pill_config.get("description"):
            return pill_config["description"]

        effect = pill_config.get("effect", {})
        if effect:
            effects = []
            if effect.get("add_hp"):
                effects.append(f"恢复{effect['add_hp']}气血")
            if effect.get("add_experience"):
                effects.append(f"增加{effect['add_experience']}修为")
            if effect.get("add_breakthrough_bonus"):
                bonus = int(effect["add_breakthrough_bonus"] * 100)
                effects.append(f"提升{bonus}%突破率")
            if effects:
                return f"{'，'.join(effects)}（{rank}）"

        return f"{rank}丹药"

    def _get_pill_config_by_name(self, name: str) -> Optional[Dict]:
        """根据丹药名称从配置中获取丹药信息"""
        if not self.config_manager:
            return None

        for source in ("exp_pills_data", "utility_pills_data", "pills_data"):
            store = getattr(self.config_manager, source, None)
            if isinstance(store, dict) and name in store:
                return store[name]

        items = getattr(self.config_manager, "items_data", None)
        if isinstance(items, dict):
            item = items.get(name)
            if item and item.get("type") == "丹药":
                return item

        return None

    def effect_text(self, recipe: Dict) -> str:
        """配方效果展示文本（优先使用丹药实际配置描述）"""
        pill_config = self._get_pill_config_by_name(recipe.get("name", ""))
        if pill_config and pill_config.get("description"):
            return pill_config["description"]
        return recipe.get("desc", "丹药效果")

    # ===== 材料来源 =====

    def _get_material_sources_text(self, material_name: str, compact: bool = False) -> str:
        """获取材料来源说明文本"""
        sources = self.MATERIAL_SOURCES.get(material_name, self.MATERIAL_SOURCES["default"])

        if compact:
            source_names = "、".join([s["name"] for s in sources])
            return f"📍 获取途径：{source_names}"

        lines = [f"📦 {material_name} - 获取途径", "━━━━━━━━━━━━━━━"]
        for source in sources:
            lines.append(f"🔸 {source['name']}：{source['cmd']}（{source['desc']}）")
        return "\n".join(lines)

    def _get_materials_tips(self, materials: Dict[str, int]) -> str:
        """生成材料获取提示（用于材料不足时）"""
        tips = ["\n💡 材料获取提示："]
        mentioned_sources = set()

        for material_name in materials:
            if material_name == "灵石":
                continue
            sources = self.MATERIAL_SOURCES.get(material_name, self.MATERIAL_SOURCES["default"])
            for source in sources:
                source_key = f"{source['name']}:{source['cmd']}"
                if source_key not in mentioned_sources:
                    tips.append(f"  · {source['name']}：{source['cmd']}")
                    mentioned_sources.add(source_key)

        return "\n".join(tips) if len(tips) > 1 else ""

    async def query_material_source(self, material_name: str) -> Tuple[bool, str]:
        """查询材料获取途径（材料溯源功能）"""
        if material_name == "灵石":
            return True, (
                "💰 灵石 - 获取途径\n"
                "━━━━━━━━━━━━━━━\n"
                "🔸 历练系统：完成历练获得灵石奖励\n"
                "🔸 世界事件：参与事件结算奖励（批次4开放）\n"
                "🔸 出售物品：把不需要的材料卖给NPC"
            )

        return True, self._get_material_sources_text(material_name, compact=False)

    # ===== 境界 / 称号加成 =====

    def level_name(self, level_index: int, player: Player = None) -> str:
        """境界名称（优先按玩家修炼类型取，其次灵修）"""
        if not self.config_manager:
            return f"境界{level_index}"

        level_data = None
        if player is not None and hasattr(self.config_manager, "get_level_data"):
            level_data = self.config_manager.get_level_data(player.cultivation_type)
        if not level_data:
            level_data = getattr(self.config_manager, "level_data", None)

        if level_data and 0 <= int(level_index) < len(level_data):
            return level_data[int(level_index)].get("level_name", f"境界{level_index}")
        return f"境界{level_index}"

    def get_level_bonus(self, level_index: int) -> float:
        """境界成功率加成（取满足条件的最高档）"""
        bonus_map = self.get_setting("level_success_bonus", {}) or {}
        if not isinstance(bonus_map, dict):
            return 0.0

        bonus = 0.0
        for threshold, value in bonus_map.items():
            try:
                threshold_value = int(threshold)
                bonus_value = float(value)
            except (TypeError, ValueError):
                continue
            if level_index >= threshold_value:
                bonus = max(bonus, bonus_value)
        return bonus

    async def has_alchemist_title(self, player: Player) -> bool:
        """是否拥有炼丹师称号"""
        if not player:
            return False
        try:
            return await self.db.has_title(player.user_id, self.ALCHEMIST_TITLE)
        except Exception:
            return False

    async def get_alchemist_bonus(self, player: Player) -> float:
        """炼丹师称号成功率加成（无称号则为 0）"""
        if await self.has_alchemist_title(player):
            return float(self.get_setting("alchemist_bonus", 0.15) or 0)
        return 0.0

    async def compute_success_rate(self, player: Player, recipe: Dict) -> Tuple[float, Dict]:
        """计算实际成功率

        Returns:
            (最终成功率 0~1, 明细 {"base": float, "level_bonus": float, "alchemist_bonus": float})
        """
        base = max(0.0, float(recipe.get("success_rate", 50) or 0) / 100.0)
        level_bonus = self.get_level_bonus(player.level_index)
        alchemist_bonus = await self.get_alchemist_bonus(player)
        max_rate = float(self.get_setting("max_success_rate", 0.95) or 0.95)

        final = min(max_rate, base + level_bonus + alchemist_bonus)
        return final, {
            "base": base,
            "level_bonus": level_bonus,
            "alchemist_bonus": alchemist_bonus,
        }

    def format_rate_breakdown(self, detail: Dict, final_rate: float) -> str:
        """成功率明细文本"""
        parts = [f"基础{int(detail.get('base', 0) * 100)}%"]
        if detail.get("level_bonus"):
            parts.append(f"境界+{int(detail['level_bonus'] * 100)}%")
        if detail.get("alchemist_bonus"):
            parts.append(f"炼丹师+{int(detail['alchemist_bonus'] * 100)}%")
        return f"{int(final_rate * 100)}%（{'，'.join(parts)}）"

    # ===== 材料检查与消耗 =====

    def _material_counts(self, player: Player, materials: Dict[str, int]) -> Dict[str, int]:
        """玩家当前持有的材料数量（灵石取玩家身上的灵石）"""
        owned = {}
        for name in materials:
            if name == "灵石":
                owned[name] = int(player.gold or 0)
            elif self.storage_ring_manager:
                owned[name] = int(self.storage_ring_manager.get_item_count(player, name) or 0)
            else:
                owned[name] = 0
        return owned

    async def can_craft_pill(self, player: Player, recipe_id) -> Tuple[bool, str]:
        """检查是否可以炼制（境界 / 配方学习 / 材料）"""
        # 0. 元神状态（批次1）：元神期间无法炼丹，但可用还魂丹复活
        if getattr(player, "is_soul_state", False):
            return False, (
                "⚠️ 元神状态无法炼丹，请先复活！\n"
                "💡 发送「元神状态」查看元神详情\n"
                "💡 背包中有还魂丹时，发送「使用还魂丹」可立即完美复活"
            )

        recipe = self.get_recipe(recipe_id)
        if not recipe:
            return False, "❌ 配方不存在！"

        # 1. 境界要求
        required_level = int(recipe.get("level_required", 0) or 0)
        if player.level_index < required_level:
            level_name = self.level_name(required_level, player)
            return False, f"❌ 炼制【{recipe['name']}】需要达到【{level_name}】以上境界！"

        # 2. 稀有配方需要先学习
        if self.is_learnable_recipe(recipe_id):
            learned = False
            try:
                learned = await self.db.has_learned_recipe(player.user_id, int(recipe["id"]))
            except Exception:
                learned = False
            if not learned:
                return False, (
                    f"❌ 你尚未学习【{recipe['name']}】的配方\n"
                    "━━━━━━━━━━━━━━━\n"
                    "💡 稀有配方获取途径：\n"
                    "  1. 世界事件奖励（批次4开放）\n"
                    "  2. 宗门功法库 / 炼丹师传授（后续开放）\n"
                    "  3. 委托炼丹师炼制成品"
                )

        # 3. 材料检查
        materials = recipe.get("materials", {})
        owned = self._material_counts(player, materials)
        missing = []
        for name, count in materials.items():
            if owned.get(name, 0) < int(count):
                missing.append(f"{name}（需要{count}，拥有{owned.get(name, 0)}）")

        if missing:
            tips = self._get_materials_tips(materials)
            return False, "❌ 材料不足！\n" + "\n".join(f"  · {m}" for m in missing) + tips

        return True, ""

    async def _consume_ring_materials(self, player: Player, materials: Dict[str, int]) -> Tuple[bool, str, List[str]]:
        """消耗储物戒中的材料（灵石不在此处处理）

        Returns:
            (是否成功, 错误消息, 实际消耗的材料文本列表)
        """
        consumed: List[str] = []
        consumed_pairs: List[Tuple[str, int]] = []
        if not self.storage_ring_manager:
            return True, "", consumed

        for name, count in materials.items():
            if name == "灵石":
                continue
            count = int(count)
            ok, msg = await self.storage_ring_manager.retrieve_item(player, name, count)
            if not ok:
                # 极端情况（并发导致材料不足）：回滚已消耗的部分
                for back_name, back_count in consumed_pairs:
                    await self.storage_ring_manager.store_item(player, back_name, back_count, silent=True)
                return False, f"❌ 消耗材料失败：{msg}", []
            consumed_pairs.append((name, count))
            consumed.append(f"{name}×{count}")
        return True, "", consumed

    # ===== 配方列表 / 详情 =====

    async def get_available_recipes(self, user_id: str) -> Tuple[bool, str]:
        """丹药配方列表（含星级、学习状态与当前实际成功率）"""
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！"

        if not self.recipes:
            return False, "❌ 暂无可用配方！"

        max_star = self.get_max_shop_pill_star()
        lines = [
            "🔥 丹药配方图鉴",
            "━━━━━━━━━━━━━━━",
            f"📖 共 {len(self.recipes)} 个配方｜商店仅售 {max_star} 星及以下丹药",
            "",
        ]

        for recipe in self.sorted_recipes():
            recipe_id = int(recipe["id"])
            star = recipe.get("star", 1)
            materials_str = "，".join(f"{k}×{v}" for k, v in recipe.get("materials", {}).items())

            if self.is_learnable_recipe(recipe_id):
                learned = await self.db.has_learned_recipe(player.user_id, recipe_id)
                status = "✅ 已习得" if learned else "🔒 未习得"
            else:
                status = "🔓 无需学习"

            level_ok = player.level_index >= int(recipe.get("level_required", 0) or 0)
            level_text = self.level_name(recipe.get("level_required", 0), player)
            if not level_ok:
                rate_text = f"境界不足（需{level_text}）"
            else:
                final_rate, detail = await self.compute_success_rate(player, recipe)
                rate_text = self.format_rate_breakdown(detail, final_rate)

            lines.append(f"【{recipe['name']}】{self.star_text(star)} (ID:{recipe_id}) {status}")
            lines.append(f"  需求境界：{level_text}")
            lines.append(f"  材料：{materials_str or '无'}")
            lines.append(f"  成功率：{rate_text}")
            lines.append(f"  效果：{self.effect_text(recipe)}")
            lines.append("")

        lines.append("💡 使用「炼丹 <配方ID>」开始炼制")
        lines.append("💡 使用「配方详情 <配方ID>」查看单个配方详情")
        lines.append("💡 使用「材料查询 <材料名>」查看材料获取途径")
        return True, "\n".join(lines).strip()

    async def show_recipe_details(self, recipe_id, user_id: str = "") -> str:
        """显示配方详情"""
        recipe = self.get_recipe(recipe_id)
        if not recipe:
            return (
                "❌ 配方不存在\n"
                f"💡 发送「丹药配方」查看全部配方ID（当前共 {len(self.recipes)} 个）"
            )

        recipe_id = int(recipe["id"])
        star = recipe.get("star", 1)

        player = None
        if user_id:
            player = await self.db.get_player_by_id(user_id)

        lines = [
            "📜 配方详情",
            "━━━━━━━━━━━━━━━",
            f"🧪 {recipe['name']} {self.star_text(star)}（{star}星）",
            f"🆔 配方ID：{recipe_id}",
            "━━━━━━━━━━━━━━━",
            f"📝 效果：{self.effect_text(recipe)}",
            f"⚗️ 基础成功率：{recipe.get('success_rate', 50)}%",
            f"📊 需要境界：{self.level_name(recipe.get('level_required', 0), player)}",
        ]

        # 星级与商店规则
        max_star = self.get_max_shop_pill_star()
        pill_config = self._get_pill_config_by_name(recipe.get("name", "")) or {}
        try:
            in_shop = float(pill_config.get("price", 0) or 0) > 0 and float(pill_config.get("shop_weight", 0) or 0) > 0
        except (TypeError, ValueError):
            in_shop = False

        if star > max_star:
            lines.append(f"🏪 商店可售：❌ {star}星丹药不在商店出售，只能自行炼制")
        elif in_shop:
            lines.append("🏪 商店可售：✅ 可在丹阁直接购买")
        else:
            lines.append("🏪 商店可售：❌ 该丹药未在商店出售，需自行炼制")

        # 配方学习状态
        if self.is_learnable_recipe(recipe_id):
            if player:
                learned = await self.db.has_learned_recipe(player.user_id, recipe_id)
                lines.append(f"📖 配方状态：{'✅ 已习得' if learned else '🔒 未习得（需先学习才能炼制）'}")
            else:
                lines.append("📖 配方状态：🔒 稀有配方，需要先学习")
        else:
            lines.append("📖 配方状态：🔓 无需学习")

        # 材料
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("🧪 所需材料：")
        materials = recipe.get("materials", {})
        if materials:
            owned = self._material_counts(player, materials) if player else {}
            for name, count in materials.items():
                if player:
                    have = owned.get(name, 0)
                    mark = "✅" if have >= int(count) else "❌"
                    lines.append(f"  {mark} {name} ×{int(count)}（拥有 {have}）")
                else:
                    lines.append(f"  · {name} ×{int(count)}")
        else:
            lines.append("  · 无")

        # 当前成功率
        if player:
            lines.append("━━━━━━━━━━━━━━━")
            if player.level_index >= int(recipe.get("level_required", 0) or 0):
                final_rate, detail = await self.compute_success_rate(player, recipe)
                lines.append(f"🎯 你的实际成功率：{self.format_rate_breakdown(detail, final_rate)}")
            else:
                lines.append("🎯 你的实际成功率：境界不足，无法炼制")

        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"💡 发送「炼丹 {recipe_id}」开始炼制")

        # 材料获取提示
        non_gold = [m for m in materials if m != "灵石"]
        if non_gold:
            lines.append(self._get_materials_tips(materials).strip())

        return "\n".join([line for line in lines if line != ""]).strip()

    # ===== 炼丹 =====

    async def craft_pill(
        self,
        user_id: str,
        pill_id: int
    ) -> Tuple[bool, str, Optional[Dict]]:
        """炼制丹药

        Returns:
            (是否执行成功, 消息, 结果数据)
        """
        # 1. 检查用户
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！", None

        # 2. 检查用户状态（状态互斥）
        user_cd = await self.db.ext.get_user_cd(user_id)
        if user_cd and user_cd.type != UserStatus.IDLE:
            current_status = UserStatus.get_name(user_cd.type)
            return False, f"❌ 你当前正{current_status}，无法炼丹！", None

        # 3. 检查配方 / 境界 / 学习 / 材料
        recipe = self.get_recipe(pill_id)
        if not recipe:
            return False, "❌ 无效的丹药ID！发送「丹药配方」查看可用配方", None

        can_craft, error_msg = await self.can_craft_pill(player, pill_id)
        if not can_craft:
            return False, error_msg, None

        recipe_id = int(recipe["id"])
        pill_name = recipe["name"]
        materials = recipe.get("materials", {})
        gold_cost = int(materials.get("灵石", 0) or 0)

        # 4. 消耗储物戒材料（灵石在结算时统一扣除）
        ok, consume_msg, consumed_materials = await self._consume_ring_materials(player, materials)
        if not ok:
            return False, consume_msg, None

        # 5. 计算成功率
        final_rate, detail = await self.compute_success_rate(player, recipe)
        is_success = random.random() < final_rate

        cost_lines = []
        if gold_cost > 0:
            cost_lines.append(f"灵石 -{gold_cost}")
        cost_lines.extend(consumed_materials)
        cost_str = "、".join(cost_lines) if cost_lines else "无"

        rate_text = self.format_rate_breakdown(detail, final_rate)
        star_text = self.star_text(recipe.get("star", 1))

        if is_success:
            # 6-A. 炼制成功：丹药存入丹药背包（可额外产出）
            extra_pill = False
            if detail.get("alchemist_bonus", 0) > 0:
                extra_chance = float(self.get_setting("alchemist_extra_pill_chance", 0.1) or 0)
                extra_pill = random.random() < extra_chance

            fresh = await self.db.get_player_by_id(user_id)
            if not fresh:
                return False, "❌ 炼丹结算失败，请稍后重试", None

            fresh.gold = max(0, int(fresh.gold or 0) - gold_cost)
            inventory = fresh.get_pills_inventory()
            gain = 2 if extra_pill else 1
            inventory[pill_name] = int(inventory.get(pill_name, 0) or 0) + gain
            fresh.set_pills_inventory(inventory)
            await self.db.update_player(fresh)

            msg_lines = [
                "✨ 炼丹成功！",
                "━━━━━━━━━━━━━━━",
                f"🎉 获得【{pill_name}】{star_text} ×{gain}",
                f"📊 成功率：{rate_text}",
                f"🧪 消耗：{cost_str}",
                "━━━━━━━━━━━━━━━",
                f"💡 使用「服用丹药 {pill_name}」可服用此丹药",
                "💡 使用「丹药背包」查看全部丹药",
            ]
            if extra_pill:
                msg_lines.insert(3, "🌟 炼丹师熟练度触发，额外产出 1 枚！")

            if recipe.get("effect_type") == "revival":
                msg_lines.append("💀 元神状态下发送「使用还魂丹」可完美复活（修为无损）")

            return True, "\n".join(msg_lines), {
                "success": True,
                "recipe_id": recipe_id,
                "pill_name": pill_name,
                "pill_star": recipe.get("star", 1),
                "pill_count": gain,
                "bonus_pill": extra_pill,
                "gold_cost": gold_cost,
                "materials_consumed": consumed_materials,
                "success_rate": round(final_rate, 4),
            }

        # 6-B. 炼制失败：按比例返还材料，小概率产出废丹
        refund_rate = float(self.get_setting("material_refund_rate", 0.5) or 0)
        refund_rate = min(max(refund_rate, 0.0), 1.0)
        refund_lines: List[str] = []
        gold_refund = 0

        if self.storage_ring_manager:
            for name, count in materials.items():
                if name == "灵石":
                    continue
                refund_count = int(int(count) * refund_rate)
                if refund_count <= 0:
                    continue
                ok_store, _ = await self.storage_ring_manager.store_item(player, name, refund_count, silent=True)
                if ok_store:
                    refund_lines.append(f"{name}×{refund_count}")

        if gold_cost > 0:
            gold_refund = int(gold_cost * refund_rate)

        # 废丹（小概率）
        waste_pill_name = str(self.get_setting("waste_pill_name", "废丹") or "废丹")
        waste_chance = float(self.get_setting("waste_pill_chance", 0.05) or 0)
        waste_added = False
        if random.random() < waste_chance:
            if self.storage_ring_manager:
                ok_store, _ = await self.storage_ring_manager.store_item(player, waste_pill_name, 1, silent=True)
                waste_added = ok_store

        fresh = await self.db.get_player_by_id(user_id)
        if not fresh:
            return False, "❌ 炼丹结算失败，请稍后重试", None

        fresh.gold = max(0, int(fresh.gold or 0) - gold_cost + gold_refund)
        await self.db.update_player(fresh)

        if gold_refund > 0:
            refund_lines.insert(0, f"灵石×{gold_refund}")

        msg_lines = [
            "💥 炼丹失败！",
            "━━━━━━━━━━━━━━━",
            f"炼制【{pill_name}】失败，丹炉炸裂...",
            f"📉 材料消耗：{int((1 - refund_rate) * 100)}%（返还部分向下取整）",
            f"📊 成功率：{rate_text}",
            f"🧪 投入：{cost_str}",
        ]
        if refund_lines:
            msg_lines.append(f"♻️ 回收：{'、'.join(refund_lines)}")
        if waste_added:
            msg_lines.append(f"🗑️ 意外产出【{waste_pill_name}】×1（可卖给NPC回收少量灵石）")

        msg_lines.append("再接再厉！")

        return True, "\n".join(msg_lines), {
            "success": False,
            "recipe_id": recipe_id,
            "pill_name": pill_name,
            "gold_cost": gold_cost,
            "gold_refund": gold_refund,
            "materials_consumed": consumed_materials,
            "materials_refund": refund_lines,
            "waste_pill": waste_added,
            "success_rate": round(final_rate, 4),
        }
