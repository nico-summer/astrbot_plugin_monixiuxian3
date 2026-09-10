# core/item_registry.py
"""统一物品分类与信息索引。

储物戒、物品信息等模块通过本索引获知物品的：
- 分类（材料 / 装备 / 功法 / 丹药 / 其他）
- 补充信息（品质、细分类型、描述、获取来源）

索引来源（按优先级从高到低）：
1. 配置文件：items.json / weapons.json / pills.json / exp_pills.json /
   utility_pills.json / storage_rings.json / alchemy_recipes.json
2. 各系统掉落表：秘境 / 世界Boss / 历练 / 悬赏 / 灵田

这样秘境、Boss 等系统掉落但未登记进配置的物品也能被正确分类与查询。
"""

import json
import re
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from ..config_manager import ConfigManager


CATEGORY_MATERIAL = "材料"
CATEGORY_EQUIPMENT = "装备"
CATEGORY_TECHNIQUE = "功法"
CATEGORY_PILL = "丹药"
CATEGORY_OTHER = "其他"

# 储物戒等界面展示顺序
CATEGORY_ORDER = (
    CATEGORY_MATERIAL,
    CATEGORY_EQUIPMENT,
    CATEGORY_TECHNIQUE,
    CATEGORY_PILL,
    CATEGORY_OTHER,
)

# 配置中 type 字段风格不统一，这里统一归一化
_EQUIPMENT_TYPES = {
    "weapon", "weapons", "武器", "防具", "armor", "armour", "法器",
    "饰品", "装备", "equipment",
}
_TECHNIQUE_TYPES = {
    "technique", "skill", "功法", "心法", "技能", "main_technique", "sub_technique",
}
_MATERIAL_TYPES = {"material", "材料", "材料类", "ore", "herb"}
_PILL_TYPES = {"pill", "丹药", "丹", "pills"}

# 兜底推断用的后缀（仅用于完全未登记的物品）
_EQUIPMENT_SUFFIXES = (
    "剑", "刀", "枪", "矛", "锤", "斧", "弓", "弩", "杖", "戟", "棍",
    "甲", "铠", "袍", "衣", "履", "靴", "冠", "护符", "戒指", "手镯", "项链",
)
_TECHNIQUE_SUFFIXES = ("诀", "术", "经", "典", "篇", "神功", "心法", "大法", "拳", "掌", "步", "游")
_MATERIAL_SUFFIXES = (
    "石", "晶", "铁", "金", "砂", "粉", "髓", "沙", "鳞", "草", "花", "叶",
    "蛋", "骨", "皮", "毛皮", "核", "碎片", "精华", "之息", "息", "液",
)

# 丹药 subtype -> 中文标签
_PILL_SUBTYPES = {
    "breakthrough": "破境丹",
    "exp": "修为丹",
    "resurrection": "复活丹",
    "cultivation_boost": "修炼增益丹",
    "permanent_attribute": "属性丹",
    "combat_boost": "战斗增益丹",
    "defensive_boost": "防护增益丹",
    "debuff": "减益丹",
    "instant_restore": "恢复丹",
    "regeneration": "回复丹",
    "chaos_boost": "混沌增益丹",
    "breakthrough_boost": "破境增益丹",
    "breakthrough_debuff": "破境减益丹",
    "special": "特殊丹药",
    "reset": "重置丹",
    "protection": "护身丹",
}


class ItemRegistry:
    """物品分类与信息索引"""

    def __init__(self, config_manager: Optional["ConfigManager"] = None):
        self.config_manager = config_manager
        self._categories: Dict[str, str] = {}
        self._infos: Dict[str, dict] = {}
        self._build_error: Optional[str] = None
        self.build()

    # ==================== 构建索引 ====================

    def build(self):
        """重建索引（可重复调用）"""
        self._categories.clear()
        self._infos.clear()
        if not self.config_manager:
            return

        self._register_json_items()
        self._register_drop_tables()

    def _register_json_items(self):
        cm = self.config_manager

        # items.json：材料 / 法器 / 功法 / 丹药
        for name, data in (getattr(cm, "items_data", None) or {}).items():
            category = self._normalize_type(data.get("type"))
            if not category:
                continue
            info = self._build_info(
                data,
                category=category,
                source=self._source_from_item(data),
                subtype=data.get("type") if category == CATEGORY_EQUIPMENT else None,
            )
            self._register(name, category, info)

        # weapons.json
        for name, data in (getattr(cm, "weapons_data", None) or {}).items():
            info = self._build_info(
                data,
                category=CATEGORY_EQUIPMENT,
                subtype=data.get("weapon_category") or "武器",
                source="器阁购买、秘境与Boss掉落",
            )
            self._register(name, CATEGORY_EQUIPMENT, info)

        # 三种丹药配置
        for store in (
            getattr(cm, "pills_data", None),
            getattr(cm, "exp_pills_data", None),
            getattr(cm, "utility_pills_data", None),
        ):
            for name, data in (store or {}).items():
                info = self._build_info(
                    data,
                    category=CATEGORY_PILL,
                    subtype=data.get("subtype"),
                    source="丹阁购买、炼丹或活动奖励",
                )
                self._register(name, CATEGORY_PILL, info)

        # 储物戒
        for name, data in (getattr(cm, "storage_rings_data", None) or {}).items():
            info = self._build_info(
                data,
                category=CATEGORY_OTHER,
                subtype="储物戒",
                source="更换储物戒（消耗灵石）",
            )
            self._register(name, CATEGORY_OTHER, info)

        # 炼丹配方产物
        for name, data in (getattr(cm, "alchemy_recipes", None) or {}).items():
            self._register(
                name,
                CATEGORY_PILL,
                {"category": CATEGORY_PILL, "subtype": "炼丹产物", "source": "炼丹"},
            )

    def _register_drop_tables(self):
        """登记各系统掉落表中的物品，避免秘境/Boss 掉落落入【其他】"""
        # 秘境
        try:
            from ..managers.rift_manager import RiftManager

            for level_table in (RiftManager.RIFT_MATERIAL_TABLE or {}).values():
                for entry in level_table or []:
                    self._register_drop_entry(entry, CATEGORY_MATERIAL, "秘境探索掉落")

            for level_config in (RiftManager.RIFT_EQUIPMENT_TABLE or {}).values():
                for entry in (level_config or {}).get("items", []):
                    self._register_drop_entry(entry, CATEGORY_EQUIPMENT, "秘境探索掉落")

            for level_config in (RiftManager.RIFT_SKILL_TABLE or {}).values():
                for entry in (level_config or {}).get("items", []):
                    self._register_drop_entry(entry, CATEGORY_TECHNIQUE, "秘境探索掉落")
        except Exception as exc:  # pragma: no cover - 索引失败不应影响插件启动
            self._build_error = f"秘境掉落表登记失败: {exc}"

        # 世界Boss
        try:
            from ..managers.boss_manager import BossManager

            for tier_table in (BossManager.BOSS_DROP_TABLE or {}).values():
                for entry in tier_table or []:
                    self._register_drop_entry(entry, CATEGORY_MATERIAL, "世界Boss击杀掉落")
        except Exception as exc:  # pragma: no cover
            self._build_error = f"Boss掉落表登记失败: {exc}"

        # 历练
        try:
            from ..managers.adventure_manager import AdventureManager

            config = self._load_json_file(getattr(AdventureManager, "CONFIG_FILE", None))
            drop_tables = (config or {}).get("drop_tables") or AdventureManager.DEFAULT_CONFIG.get("drop_tables", {})
            for tier_table in drop_tables.values():
                for entry in tier_table or []:
                    self._register_drop_entry(entry, CATEGORY_MATERIAL, "历练掉落")
        except Exception as exc:  # pragma: no cover
            self._build_error = f"历练掉落表登记失败: {exc}"

        # 悬赏
        try:
            from ..managers.bounty_manager import BountyManager

            config = self._load_json_file(getattr(BountyManager, "CONFIG_FILE", None))
            item_tables = (config or {}).get("item_tables") or BountyManager.DEFAULT_CONFIG.get("item_tables", {})
            for table in item_tables.values():
                for entry in table or []:
                    self._register_drop_entry(entry, CATEGORY_MATERIAL, "悬赏任务奖励")
        except Exception as exc:  # pragma: no cover
            self._build_error = f"悬赏掉落表登记失败: {exc}"

        # 灵田灵草（收获后可炼化）
        try:
            from ..managers.spirit_farm_manager import SPIRIT_HERBS

            for herb_name in (SPIRIT_HERBS or {}):
                self._register(
                    herb_name,
                    CATEGORY_MATERIAL,
                    {
                        "category": CATEGORY_MATERIAL,
                        "subtype": "灵草",
                        "source": "灵田种植收获",
                    },
                )
        except Exception as exc:  # pragma: no cover
            self._build_error = f"灵田灵草登记失败: {exc}"

    # ==================== 查询接口 ====================

    def get_category(self, item_name: str) -> str:
        """获取物品分类，未知物品返回【其他】"""
        name = str(item_name or "").strip()
        if not name:
            return CATEGORY_OTHER
        if name in self._categories:
            return self._categories[name]
        guessed = self._guess_category(name)
        if guessed:
            self._categories[name] = guessed
        return guessed or CATEGORY_OTHER

    def get_info(self, item_name: str) -> Optional[dict]:
        """获取物品补充信息，未登记返回 None"""
        return self._infos.get(str(item_name or "").strip())

    def is_known(self, item_name: str) -> bool:
        """物品是否已被登记（不含后缀推断）"""
        return str(item_name or "").strip() in self._categories

    def get_brief(self, item_name: str) -> str:
        """返回适合列表展示的简短短语，例如“武器·凡品”“攻击功法”“材料”"""
        name = str(item_name or "").strip()
        category = self.get_category(name)
        info = self.get_info(name) or {}

        if category == CATEGORY_MATERIAL:
            subtype = info.get("subtype")
            return subtype or CATEGORY_MATERIAL
        if category == CATEGORY_EQUIPMENT:
            subtype = info.get("subtype") or "装备"
            rank = info.get("rank")
            return f"{subtype}·{rank}" if rank else subtype
        if category == CATEGORY_TECHNIQUE:
            subtype = info.get("subtype")
            return subtype or CATEGORY_TECHNIQUE
        if category == CATEGORY_PILL:
            subtype = info.get("subtype")
            return _PILL_SUBTYPES.get(str(subtype), subtype or CATEGORY_PILL)
        if not self.is_known(name):
            return "未登记物品"
        return info.get("subtype") or CATEGORY_OTHER

    def get_detail_lines(self, item_name: str) -> List[str]:
        """返回物品的详细信息行（指令 /物品信息 兜底展示）"""
        name = str(item_name or "").strip()
        category = self.get_category(name)
        info = self.get_info(name) or {}

        lines = [f"【{name}】", f"分类：{category}"]

        subtype = info.get("subtype")
        if category == CATEGORY_PILL and subtype:
            subtype = _PILL_SUBTYPES.get(str(subtype), subtype)
        if subtype:
            label = "类型" if category == CATEGORY_EQUIPMENT else "细分"
            lines.append(f"{label}：{subtype}")
        if info.get("rank"):
            lines.append(f"品质：{info['rank']}")
        if info.get("description"):
            lines.append(f"描述：{info['description']}")
        if info.get("price"):
            lines.append(f"参考价格：{info['price']:,} 灵石")
        if info.get("source"):
            lines.append(f"获取途径：{info['source']}")

        if not self.is_known(name):
            lines.append("提示：该物品尚未登记详细信息，可能来自秘境/Boss掉落")

        return lines

    # ==================== 内部工具 ====================

    def _register(self, name, category, info=None):
        """登记物品（先登记的来源优先级更高，不会被后登记的覆盖）"""
        name = str(name or "").strip()
        if not name:
            return
        if category not in CATEGORY_ORDER:
            category = CATEGORY_OTHER

        existing = self._categories.get(name)
        if existing is None:
            self._categories[name] = category
        if info:
            merged = self._infos.setdefault(name, {})
            for key, value in info.items():
                if value is not None and merged.get(key) in (None, ""):
                    merged[key] = value
            merged.setdefault("category", self._categories.get(name, category))

    def _register_drop_entry(self, entry, default_category, source):
        if not isinstance(entry, dict):
            return
        name = entry.get("name")
        if not name:
            return

        entry_type = entry.get("type")
        category = default_category
        subtype = None
        if default_category == CATEGORY_EQUIPMENT:
            subtype = entry_type or "装备"
        elif default_category == CATEGORY_TECHNIQUE:
            subtype = entry_type or CATEGORY_TECHNIQUE
        else:
            # 掉落表里偶尔也会夹杂装备/功法
            if entry_type in _EQUIPMENT_TYPES:
                category = CATEGORY_EQUIPMENT
                subtype = entry_type
            elif entry_type in _TECHNIQUE_TYPES:
                category = CATEGORY_TECHNIQUE
                subtype = entry_type

        info = {
            "category": category,
            "subtype": subtype,
            "rank": entry.get("quality"),
            "source": source,
        }
        self._register(name, category, info)

    @staticmethod
    def _normalize_type(raw_type) -> Optional[str]:
        if not raw_type:
            return None
        value = str(raw_type).strip()
        if value in _EQUIPMENT_TYPES:
            return CATEGORY_EQUIPMENT
        if value in _TECHNIQUE_TYPES:
            return CATEGORY_TECHNIQUE
        if value in _MATERIAL_TYPES:
            return CATEGORY_MATERIAL
        if value in _PILL_TYPES:
            return CATEGORY_PILL
        # 形如“攻击功法”“防御功法”
        if "功法" in value or value.endswith("诀") or value.endswith("术"):
            return CATEGORY_TECHNIQUE
        if value.endswith("丹"):
            return CATEGORY_PILL
        if "武器" in value or "防具" in value or "饰品" in value:
            return CATEGORY_EQUIPMENT
        if "材料" in value:
            return CATEGORY_MATERIAL
        return None

    @staticmethod
    def _guess_category(name: str) -> Optional[str]:
        """完全未登记物品的后缀兜底推断"""
        for suffix in _EQUIPMENT_SUFFIXES:
            if name.endswith(suffix):
                return CATEGORY_EQUIPMENT
        for suffix in _TECHNIQUE_SUFFIXES:
            if name.endswith(suffix):
                return CATEGORY_TECHNIQUE
        for suffix in _MATERIAL_SUFFIXES:
            if name.endswith(suffix):
                return CATEGORY_MATERIAL
        return None

    @staticmethod
    def _build_info(data: dict, category, subtype=None, source=None) -> dict:
        return {
            "category": category,
            "subtype": subtype,
            "rank": data.get("rank"),
            "description": data.get("description") or data.get("desc"),
            "price": data.get("price"),
            "source": source,
        }

    @staticmethod
    def _source_from_item(data: dict) -> str:
        if data.get("shop_weight", 0) in (0, None):
            return "世界Boss击杀掉落"
        return "百宝阁购买、历练与秘境掉落"

    @staticmethod
    def _load_json_file(path) -> Optional[dict]:
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None
