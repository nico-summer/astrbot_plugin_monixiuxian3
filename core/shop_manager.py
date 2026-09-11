# core/shop_manager.py

import random
import time
import json
import hashlib
from typing import List, Dict, Optional, Tuple

from astrbot.api import AstrBotConfig, logger
from ..config_manager import ConfigManager
from ..models import Item

# ===== 境界货架分层 =====
# 品质 -> 档位（0~8）。同档位视为同级品质（珍品≈灵品、圣品≈皇品、神品≈仙品）
RANK_TIERS = {
    '凡品': 0, '灵品': 1, '珍品': 1, '地品': 2, '天品': 3,
    '皇品': 4, '圣品': 4, '帝品': 5, '道品': 6,
    '仙品': 7, '神品': 7, '混元先天': 8,
}
TIER_NAMES = ['凡品', '灵品', '地品', '天品', '皇品', '帝品', '道品', '仙品', '混元先天']
# 物品缺少 required_level_index 字段时，按品质兜底一个最低境界
RANK_LEVEL_FALLBACK = {
    '凡品': 0, '灵品': 3, '珍品': 3, '地品': 10, '天品': 13,
    '皇品': 16, '圣品': 16, '帝品': 22, '道品': 25,
    '仙品': 31, '神品': 31, '混元先天': 35,
}
# 顶级品质：永不上架，只能靠 Boss / 秘境获取
DEFAULT_TOP_TIER_RANKS = ['仙品', '神品', '混元先天']
# 阁楼 ID -> 池子获取方法名
PAVILION_GETTERS = {
    'pill_pavilion': 'get_pills_for_display',
    'weapon_pavilion': 'get_weapons_for_display',
    'treasure_pavilion': 'get_all_items_for_display',
}
PAVILION_LABELS = {
    'pill_pavilion': '丹阁',
    'weapon_pavilion': '器阁',
    'treasure_pavilion': '百宝阁',
}

class ShopManager:
    """商店管理器，负责商店物品生成、刷新和购买"""

    def __init__(self, config: AstrBotConfig, config_manager: ConfigManager):
        self.config = config
        self.config_manager = config_manager

    # ===== 配置读取 =====
    def _cfg(self, key: str, default=None):
        """读取插件配置。

        AstrBot 的「核心数值配置」是放在 VALUES 分组下的嵌套结构，
        这里优先从 VALUES 取，再回退到顶层取值和默认值，
        避免直接 config.get("KEY") 拿到 None 而永远走默认值。
        """
        try:
            values = self.config.get("VALUES")
        except Exception:
            values = None
        if isinstance(values, dict) and values.get(key) is not None:
            return values[key]
        try:
            value = self.config.get(key)
        except Exception:
            value = None
        return default if value is None else value

    # ===== 丹药星级限制（批次2：商店只售 1-2 星丹药） =====
    # 商店条目中代表丹药的 type 取值
    PILL_ITEM_TYPES = ('pill', 'exp_pill', 'utility_pill')

    def get_max_shop_pill_star(self) -> int:
        """商店最高可售丹药星级（默认 2 星）"""
        if hasattr(self.config_manager, "get_max_shop_pill_star"):
            try:
                return int(self.config_manager.get_max_shop_pill_star())
            except Exception:
                pass
        try:
            return int(self._cfg("SHOP_MAX_PILL_STAR", 2))
        except (TypeError, ValueError):
            return 2

    def is_pill_item(self, item: Dict) -> bool:
        """判断商店条目是否为丹药"""
        if item.get('type') in self.PILL_ITEM_TYPES:
            return True
        data = item.get('data') or {}
        return data.get('type') == '丹药'

    def get_pill_star_of_item(self, item: Dict) -> int:
        """获取商店条目的丹药星级（非丹药返回 0）"""
        if not self.is_pill_item(item):
            return 0
        name = item.get('name') or (item.get('data') or {}).get('name') or ''
        if hasattr(self.config_manager, "get_pill_star"):
            try:
                return int(self.config_manager.get_pill_star(name))
            except Exception:
                return 1
        return 1

    def is_high_star_pill_item(self, item: Dict) -> bool:
        """3 星及以上丹药不上架（只能通过炼制 / 世界事件获得）"""
        star = self.get_pill_star_of_item(item)
        return star > self.get_max_shop_pill_star()

    # ===== 境界分层（公共货架） =====
    def get_item_tier(self, item: Dict) -> int:
        """物品的品质档位（0~8）"""
        data = item.get('data') or item
        return RANK_TIERS.get(data.get('rank', '凡品'), 0)

    def get_item_required_level(self, item: Dict) -> int:
        """物品的需求境界索引，缺失时按品质兜底"""
        data = item.get('data') or item
        level = data.get('required_level_index')
        if level is None:
            level = RANK_LEVEL_FALLBACK.get(data.get('rank', '凡品'), 0)
        try:
            return int(level)
        except (TypeError, ValueError):
            return 0

    def get_top_tier_ranks(self) -> set:
        """顶级品质名单（不上架）"""
        ranks = self._cfg("SHOP_TOP_TIER_RANKS", DEFAULT_TOP_TIER_RANKS)
        if not isinstance(ranks, (list, tuple, set)) or not ranks:
            ranks = DEFAULT_TOP_TIER_RANKS
        return set(ranks)

    def is_top_tier(self, item: Dict) -> bool:
        data = item.get('data') or item
        return data.get('rank', '凡品') in self.get_top_tier_ranks()

    def get_pavilion_pool(self, pavilion_id: str) -> List[Dict]:
        """获取阁楼的全部可售物品（价格>0 且商店权重大于 0）"""
        getter = getattr(self, PAVILION_GETTERS.get(pavilion_id, ''), None)
        if not getter:
            return []
        pool = []
        for item in getter(0):
            data = item.get('data') or {}
            if item.get('price', 0) <= 0:
                continue
            try:
                weight = float(data.get('shop_weight') or 0)
            except (TypeError, ValueError):
                weight = 0.0
            if weight <= 0:
                continue
            # 批次2：3 星及以上丹药不上架（只能炼制 / 活动获得）
            if self.is_high_star_pill_item(item):
                continue
            pool.append(item)
        return pool

    def get_shelf_tier(self, pavilion_id: str, level_index: int) -> int:
        """玩家在该阁楼能看到的最高品质档位。

        顶级品质不上架，因此高境界玩家会自动下探到次一级品质档。
        """
        pool = [it for it in self.get_pavilion_pool(pavilion_id) if not self.is_top_tier(it)]
        if not pool:
            return 0
        usable = [it for it in pool if self.get_item_required_level(it) <= level_index]
        if not usable:
            usable = pool
        return max(self.get_item_tier(it) for it in usable)

    def get_shelf_id(self, pavilion_id: str, level_index: int) -> str:
        """公共货架 ID：同一境界的玩家共享同一个货架"""
        return f"{pavilion_id}:t{self.get_shelf_tier(pavilion_id, level_index)}"

    def get_shelf_name(self, pavilion_id: str, level_index: int) -> str:
        """货架展示名，例如「器阁·皇品」"""
        tier = self.get_shelf_tier(pavilion_id, level_index)
        tier = min(max(tier, 0), len(TIER_NAMES) - 1)
        return f"{PAVILION_LABELS.get(pavilion_id, pavilion_id)}·{TIER_NAMES[tier]}"

    def _wrap_for_choice(self, items: List[Dict], bonus: float = 1.0) -> List[Dict]:
        """包一层用于加权抽样的结构（权重下限避免稀有物永不出货）"""
        wrapped = []
        for item in items:
            try:
                weight = float((item.get('data') or {}).get('shop_weight') or 0)
            except (TypeError, ValueError):
                weight = 0.0
            extra = float(item.get('bonus', 1.0) or 1.0)
            wrapped.append({'weight': max(weight, 1.0) * bonus * extra, 'src': item.get('src', item)})
        return wrapped

    def _build_shop_items(self, selected: List[Dict], shelf_id: str, stock_overrides: Optional[Dict[str, int]] = None) -> List[Dict]:
        """把抽中的物品包装成货架条目（含折扣/库存/短码）"""
        discount_min = float(self._cfg("SHOP_DISCOUNT_MIN", 0.8))
        discount_max = float(self._cfg("SHOP_DISCOUNT_MAX", 1.2))
        if discount_max < discount_min:
            discount_min, discount_max = discount_max, discount_min

        result = []
        for item in selected:
            discount = random.uniform(discount_min, discount_max)
            try:
                weight = float((item.get('data') or {}).get('shop_weight') or 0)
            except (TypeError, ValueError):
                weight = 0.0
            stock = None
            if stock_overrides:
                stock = stock_overrides.get(item['name'])
            if stock is None:
                stock = self._calculate_stock(int(max(weight, 1.0)))
            result.append({
                'id': item.get('id') or (item.get('data') or {}).get('id', item['name']),
                'name': item['name'], 'type': item['type'], 'rank': item['rank'],
                'original_price': item['price'], 'discount': discount,
                'price': int(item['price'] * discount), 'stock': stock,
                'data': item.get('data', {})
            })
        self.ensure_items_have_market_ids(result, shelf_id)
        return result

    def generate_shelf_items(self, pavilion_id: str, level_index: int, count: int) -> List[Dict]:
        """按玩家境界生成货架。

        规则：
        - 只上架「玩家当前可用」的物品，且品质最高到当前档位
        - 顶级品质（仙品/神品/混元先天）永不上架
        - 其中 1~2 件为契合槽（当前档位），其余从当前档位与低一档补齐
        """
        pool = [it for it in self.get_pavilion_pool(pavilion_id) if not self.is_top_tier(it)]
        if not pool:
            return []

        tier = self.get_shelf_tier(pavilion_id, level_index)
        usable = [it for it in pool if self.get_item_required_level(it) <= level_index] or pool

        fit = [it for it in usable if self.get_item_tier(it) == tier]
        if not fit:
            # 兜底：当前档位没有可用物品时，取池子里品质最高的一档
            tier = max(self.get_item_tier(x) for x in usable)
            fit = [it for it in usable if self.get_item_tier(it) == tier]
        # 补充槽只向下取有限档位，避免低阶凡品刷屏占满货架
        try:
            fill_range = max(1, int(self._cfg("SHOP_FILL_TIER_RANGE", 2)))
        except (TypeError, ValueError):
            fill_range = 2
        lower = [it for it in usable if tier - fill_range <= self.get_item_tier(it) < tier]
        if not lower:
            lower = [it for it in usable if self.get_item_tier(it) < tier]

        try:
            bonus = float(self._cfg("SHOP_FIT_TIER_WEIGHT_BONUS", 3.0))
        except (TypeError, ValueError):
            bonus = 3.0

        selected: List[Dict] = []
        chosen_ids = set()

        try:
            fit_quota = int(self._cfg("SHOP_FIT_ITEM_COUNT", 2))
        except (TypeError, ValueError):
            fit_quota = 2
        fit_count = max(0, min(fit_quota, len(fit), count))
        for chosen in self._weighted_random_choice(self._wrap_for_choice(fit, bonus), fit_count):
            selected.append(chosen['src'])
            chosen_ids.add(id(chosen['src']))

        fit_names = {it['name'] for it in selected}

        remaining = count - len(selected)
        if remaining > 0:
            # 越接近当前档位权重越高：次一档 1.0，次二档 0.4，依次递减
            rest = []
            for item in lower:
                if id(item) in chosen_ids:
                    continue
                gap = tier - self.get_item_tier(item)
                rest.append({'weight': None, 'src': item, 'bonus': 0.4 ** max(0, gap - 1)})
            if not rest:
                for item in fit:
                    if id(item) not in chosen_ids:
                        rest.append({'weight': None, 'src': item, 'bonus': 1.0})
            for chosen in self._weighted_random_choice(self._wrap_for_choice(rest), remaining):
                selected.append(chosen['src'])

        # 契合槽限量 1 件（人人抢手），补充槽 2~3 件（管够）
        stock_overrides = {
            it['name']: (1 if it['name'] in fit_names else random.randint(2, 3))
            for it in selected
        }
        return self._build_shop_items(selected, f"{pavilion_id}:t{tier}", stock_overrides)

    # ===== 刷新消耗 =====
    def get_refresh_cost(self, level_index: int) -> int:
        """消耗型刷新的单次灵石成本，随境界指数递增"""
        try:
            base = float(self._cfg("SHOP_REFRESH_COST_BASE", 50))
            rate = float(self._cfg("SHOP_REFRESH_COST_RATE", 1.30))
        except (TypeError, ValueError):
            base, rate = 50.0, 1.30
        rate = min(max(rate, 1.0), 3.0)
        return max(1, int(base * (rate ** max(0, int(level_index)))))

    def get_auto_refresh_hours(self) -> int:
        """自动刷新间隔（小时），0 表示不自动刷新"""
        try:
            return max(0, int(self._cfg("PAVILION_REFRESH_HOURS", 6)))
        except (TypeError, ValueError):
            return 6

    def get_pavilion_count(self, pavilion_id: str) -> int:
        """各阁楼货架展示数量"""
        key, default = {
            'pill_pavilion': ("PAVILION_PILL_COUNT", 10),
            'weapon_pavilion': ("PAVILION_WEAPON_COUNT", 10),
            'treasure_pavilion': ("PAVILION_TREASURE_COUNT", 15),
        }.get(pavilion_id, ("PAVILION_TREASURE_COUNT", 15))
        try:
            return max(1, int(self._cfg(key, default)))
        except (TypeError, ValueError):
            return default

    def get_daily_refresh_limit(self) -> int:
        try:
            return max(0, int(self._cfg("SHOP_REFRESH_DAILY_LIMIT", 5)))
        except (TypeError, ValueError):
            return 5

    def get_free_refreshes(self) -> int:
        try:
            return max(0, int(self._cfg("SHOP_REFRESH_FREE_TIMES", 1)))
        except (TypeError, ValueError):
            return 1

    def _format_required_level(self, level_index: int) -> str:
        """同时展示灵修/体修的需求境界名称"""
        names = []
        if 0 <= level_index < len(self.config_manager.level_data):
            name = self.config_manager.level_data[level_index].get("level_name", "")
            if name:
                names.append(name)
        if 0 <= level_index < len(self.config_manager.body_level_data):
            name = self.config_manager.body_level_data[level_index].get("level_name", "")
            if name and name not in names:
                names.append(name)
        if not names:
            return "未知境界"
        return " / ".join(names)

    def _get_all_shop_items(self) -> List[Dict]:
        """获取所有可以在商店出售的物品"""
        all_items = []

        # 添加武器
        for weapon in self.config_manager.weapons_data.values():
            if weapon.get('shop_weight', 0) > 0 and weapon.get('price', 0) > 0:
                all_items.append({
                    'id': weapon['id'],
                    'name': weapon['name'],
                    'type': 'weapon',
                    'price': weapon['price'],
                    'weight': weapon['shop_weight'],
                    'rank': weapon.get('rank', '凡品'),
                    'data': weapon
                })

        # 添加物品（防具、心法、功法）
        for item in self.config_manager.items_data.values():
            if item.get('shop_weight', 0) > 0 and item.get('price', 0) > 0:
                all_items.append({
                    'id': item.get('id', item['name']),
                    'name': item['name'],
                    'type': self._map_legacy_item_type(item),
                    'price': item['price'],
                    'weight': item['shop_weight'],
                    'rank': item.get('rank', '凡品'),
                    'data': item
                })

        # 添加破境丹
        for pill in self.config_manager.pills_data.values():
            if pill.get('shop_weight', 0) > 0 and pill.get('price', 0) > 0:
                all_items.append({
                    'id': pill['id'],
                    'name': pill['name'],
                    'type': 'pill',
                    'price': pill['price'],
                    'weight': pill['shop_weight'],
                    'rank': pill.get('rank', '凡品'),
                    'data': pill
                })

        # 添加修为丹
        for pill in self.config_manager.exp_pills_data.values():
            if pill.get('shop_weight', 0) > 0 and pill.get('price', 0) > 0:
                all_items.append({
                    'id': pill['id'],
                    'name': pill['name'],
                    'type': 'exp_pill',
                    'price': pill['price'],
                    'weight': pill['shop_weight'],
                    'rank': pill.get('rank', '凡品'),
                    'data': pill
                })

        # 添加功能丹
        for pill in self.config_manager.utility_pills_data.values():
            if pill.get('shop_weight', 0) > 0 and pill.get('price', 0) > 0:
                all_items.append({
                    'id': pill['id'],
                    'name': pill['name'],
                    'type': 'utility_pill',
                    'price': pill['price'],
                    'weight': pill['shop_weight'],
                    'rank': pill.get('rank', '凡品'),
                    'data': pill
                })

        # 批次2：3 星及以上丹药不上架（只能通过炼制 / 活动获得）
        return [item for item in all_items if not self.is_high_star_pill_item(item)]

    def _weighted_random_choice(self, items: List[Dict], count: int) -> List[Dict]:
        """基于权重的随机选择（不重复）"""
        if len(items) <= count:
            return items.copy()

        selected = []
        available_items = items.copy()

        for _ in range(count):
            if not available_items:
                break

            # 计算总权重
            total_weight = sum(item['weight'] for item in available_items)
            if total_weight == 0:
                # 如果所有权重都是0，则随机选择
                choice = random.choice(available_items)
            else:
                # 基于权重选择
                rand = random.uniform(0, total_weight)
                cumulative = 0
                choice = available_items[0]
                for item in available_items:
                    cumulative += item['weight']
                    if rand <= cumulative:
                        choice = item
                        break

            selected.append(choice)
            available_items.remove(choice)

        return selected

    def _calculate_stock(self, weight: int) -> int:
        """根据权重计算库存数量

        权重越高，物品越常见，库存越多
        权重越低，物品越稀有，库存越少（最少为1）

        Args:
            weight: 物品的商店权重

        Returns:
            库存数量（最小为1）
        """
        # 获取库存计算基数，默认100
        try:
            stock_divisor = int(self._cfg("SHOP_STOCK_DIVISOR", 100))
        except (TypeError, ValueError):
            stock_divisor = 100
        if stock_divisor <= 0:
            stock_divisor = 100

        # 库存 = 权重 / 基数，向上取整，最小为1
        stock = max(1, (weight + stock_divisor - 1) // stock_divisor)

        return stock

    def ensure_items_have_stock(self, shop_items: List[Dict]) -> bool:
        """确保已有商店物品列表包含库存字段（用于兼容旧数据）

        Args:
            shop_items: 商店物品列表

        Returns:
            是否发生了修改
        """
        updated = False
        for item in shop_items:
            stock = item.get('stock')
            if stock is None:
                data = item.get('data', {})
                weight = 0
                if isinstance(data, dict):
                    weight = data.get('shop_weight') or data.get('weight') or 0
                item['stock'] = self._calculate_stock(weight)
                updated = True
            elif stock < 0:
                item['stock'] = 0
                updated = True
        return updated

    def ensure_items_have_market_ids(self, shop_items: List[Dict], pavilion_id: str) -> bool:
        """为商店条目补齐可用于购买的短码（兼容旧库存数据）。"""
        updated = False
        used_codes = set()
        for index, item in enumerate(shop_items):
            code = str(item.get('market_id', '')).strip().upper()
            if code and code not in used_codes:
                if item.get('market_id') != code:
                    updated = True
                item['market_id'] = code
                used_codes.add(code)
                continue
            data = item.get('data', {}) if isinstance(item.get('data', {}), dict) else {}
            source_id = item.get('id') or data.get('id') or item.get('name', '')
            seed = f"{pavilion_id}:{source_id}:{index}"
            suffix = 0
            while True:
                digest = hashlib.sha1(f"{seed}:{suffix}".encode('utf-8')).hexdigest().upper()
                candidate = digest[:5]
                if candidate not in used_codes:
                    break
                suffix += 1
            item['market_id'] = candidate
            used_codes.add(candidate)
            updated = True
        return updated

    def generate_shop_items(self, count: int) -> List[Dict]:
        """生成商店物品列表

        Args:
            count: 要生成的物品数量

        Returns:
            商店物品列表，每个物品包含 id, name, type, price, discount, final_price, stock
        """
        all_items = self._get_all_shop_items()
        if not all_items:
            logger.warning("没有可用的商店物品")
            return []

        # 随机选择物品
        selected_items = self._weighted_random_choice(all_items, count)

        # 获取折扣配置
        discount_min = float(self._cfg("SHOP_DISCOUNT_MIN", 0.8))
        discount_max = float(self._cfg("SHOP_DISCOUNT_MAX", 1.2))
        if discount_max < discount_min:
            discount_min, discount_max = discount_max, discount_min

        # 生成商店物品
        shop_items = []
        for item in selected_items:
            # 随机折扣
            discount = random.uniform(discount_min, discount_max)
            final_price = int(item['price'] * discount)

            # 计算库存（基于权重）
            stock = self._calculate_stock(item['weight'])

            shop_items.append({
                'id': item['id'],
                'name': item['name'],
                'type': item['type'],
                'rank': item['rank'],
                'original_price': item['price'],
                'discount': discount,
                'price': final_price,
                'stock': stock,
                'data': item['data']
            })

        return shop_items

    def should_refresh_shop(self, last_refresh_time: int, refresh_hours: int = None) -> bool:
        """检查是否需要刷新"""
        if refresh_hours is None:
            refresh_hours = self._cfg("PAVILION_REFRESH_HOURS", 6)
        if refresh_hours <= 0:
            return False
        return (int(time.time()) - last_refresh_time) >= (refresh_hours * 3600)

    def generate_pavilion_items(self, item_getter, count: int, pavilion_id: str = 'pavilion') -> List[Dict]:
        """生成阁楼物品列表（全局池，保留给旧调用方）

        ⚠️ 新版商店已改为按境界分层（见 generate_shelf_items），
        本方法仅作为兼容保留。
        """
        base_items = item_getter(count * 2)  # 获取更多以便随机选择
        selected = self._weighted_random_choice(
            [{'weight': (i.get('data') or {}).get('shop_weight', 100), **i} for i in base_items], count
        )
        return self._build_shop_items(selected, pavilion_id)

    def get_pills_for_display(self, count: int) -> List[Dict]:
        """获取丹药列表用于丹阁展示"""
        all_pills = []
        for pill in self.config_manager.pills_data.values():
            if pill.get('price', 0) > 0:
                all_pills.append({'id': pill.get('id', pill['name']), 'name': pill['name'], 'type': 'pill', 'price': pill['price'], 'rank': pill.get('rank', '凡品'), 'data': pill})
        for pill in self.config_manager.exp_pills_data.values():
            if pill.get('price', 0) > 0:
                all_pills.append({'id': pill.get('id', pill['name']), 'name': pill['name'], 'type': 'exp_pill', 'price': pill['price'], 'rank': pill.get('rank', '凡品'), 'data': pill})
        for pill in self.config_manager.utility_pills_data.values():
            if pill.get('price', 0) > 0:
                all_pills.append({'id': pill.get('id', pill['name']), 'name': pill['name'], 'type': 'utility_pill', 'price': pill['price'], 'rank': pill.get('rank', '凡品'), 'data': pill})
        return all_pills

    def get_weapons_for_display(self, count: int) -> List[Dict]:
        """获取武器列表用于器阁展示"""
        all_weapons = []
        for weapon in self.config_manager.weapons_data.values():
            if weapon.get('price', 0) > 0:
                all_weapons.append({'id': weapon.get('id', weapon['name']), 'name': weapon['name'], 'type': 'weapon', 'price': weapon['price'], 'rank': weapon.get('rank', '凡品'), 'data': weapon})
        return all_weapons

    def get_all_items_for_display(self, count: int) -> List[Dict]:
        """获取所有物品用于百宝阁展示"""
        all_items = []
        for weapon in self.config_manager.weapons_data.values():
            if weapon.get('price', 0) > 0:
                all_items.append({'name': weapon['name'], 'type': 'weapon', 'price': weapon['price'], 'rank': weapon.get('rank', '凡品'), 'data': weapon})
        for item in self.config_manager.items_data.values():
            if item.get('price', 0) > 0:
                # 映射旧格式类型到新格式
                item_type = self._map_legacy_item_type(item)
                all_items.append({'id': item.get('id', item['name']), 'name': item['name'], 'type': item_type, 'price': item['price'], 'rank': item.get('rank', '凡品'), 'data': item})
        for pill in self.config_manager.pills_data.values():
            if pill.get('price', 0) > 0:
                all_items.append({'id': pill.get('id', pill['name']), 'name': pill['name'], 'type': 'pill', 'price': pill['price'], 'rank': pill.get('rank', '凡品'), 'data': pill})
        for pill in self.config_manager.exp_pills_data.values():
            if pill.get('price', 0) > 0:
                all_items.append({'id': pill.get('id', pill['name']), 'name': pill['name'], 'type': 'exp_pill', 'price': pill['price'], 'rank': pill.get('rank', '凡品'), 'data': pill})
        for pill in self.config_manager.utility_pills_data.values():
            if pill.get('price', 0) > 0:
                all_items.append({'id': pill.get('id', pill['name']), 'name': pill['name'], 'type': 'utility_pill', 'price': pill['price'], 'rank': pill.get('rank', '凡品'), 'data': pill})
        return all_items

    def _map_legacy_item_type(self, item: dict) -> str:
        """将旧格式物品类型映射到新格式

        Args:
            item: 物品配置字典

        Returns:
            映射后的类型字符串
        """
        original_type = item.get('type', '')
        subtype = item.get('subtype', '')

        # 法器类型映射
        if original_type == '法器':
            if subtype == '武器':
                return 'weapon'
            elif subtype == '防具':
                return 'armor'
            elif subtype == '饰品':
                return 'accessory'
            else:
                return 'weapon'  # 默认为武器

        # 功法类型映射
        if original_type == '功法':
            return 'technique'

        # 丹药类型映射（旧系统丹药）
        if original_type == '丹药':
            return 'legacy_pill'

        # 材料类型映射
        if original_type == '材料':
            return 'material'

        # 其他类型保持不变
        return original_type

    def find_item_by_name(self, name: str) -> Optional[Dict]:
        """根据名称查找物品"""
        for weapon in self.config_manager.weapons_data.values():
            if weapon['name'] == name and weapon.get('price', 0) > 0:
                return {'id': weapon.get('id', weapon['name']), 'name': weapon['name'], 'type': 'weapon', 'price': weapon['price'], 'rank': weapon.get('rank', '凡品'), 'data': weapon}
        for item in self.config_manager.items_data.values():
            if item['name'] == name and item.get('price', 0) > 0:
                # 映射旧格式类型到新格式
                item_type = self._map_legacy_item_type(item)
                return {'id': item.get('id', item['name']), 'name': item['name'], 'type': item_type, 'price': item['price'], 'rank': item.get('rank', '凡品'), 'data': item}
        for pill in self.config_manager.pills_data.values():
            if pill['name'] == name and pill.get('price', 0) > 0:
                return {'id': pill.get('id', pill['name']), 'name': pill['name'], 'type': 'pill', 'price': pill['price'], 'rank': pill.get('rank', '凡品'), 'data': pill}
        for pill in self.config_manager.exp_pills_data.values():
            if pill['name'] == name and pill.get('price', 0) > 0:
                return {'id': pill.get('id', pill['name']), 'name': pill['name'], 'type': 'exp_pill', 'price': pill['price'], 'rank': pill.get('rank', '凡品'), 'data': pill}
        for pill in self.config_manager.utility_pills_data.values():
            if pill['name'] == name and pill.get('price', 0) > 0:
                return {'id': pill.get('id', pill['name']), 'name': pill['name'], 'type': 'utility_pill', 'price': pill['price'], 'rank': pill.get('rank', '凡品'), 'data': pill}
        return None

    def format_pavilion_display(self, pavilion_name: str, items: List[Dict], refresh_hours: int = 6, last_refresh: int = 0, footer_lines: Optional[List[str]] = None) -> str:
        """格式化阁楼展示信息"""
        if not items:
            return f"{pavilion_name}暂无物品出售"

        type_label_map = {
            'weapon': '武器', 'armor': '防具', 'main_technique': '心法', 'technique': '功法',
            'pill': '破境丹', 'exp_pill': '修为丹', 'utility_pill': '功能丹',
            'legacy_pill': '丹药', 'material': '材料', 'accessory': '饰品'
        }

        lines = [f"=== {pavilion_name} ===\n"]
        for i, item in enumerate(items, 1):
            stock = item.get('stock', 0)
            if stock <= 0:
                continue
            type_label = type_label_map.get(item['type'], '物品')
            discount_text = ""
            if item.get('discount', 1.0) < 1.0:
                discount_text = f" [{int((1.0 - item['discount']) * 100)}%折]"
            elif item.get('discount', 1.0) > 1.0:
                discount_text = f" [+{int((item['discount'] - 1.0) * 100)}%]"
            stock_text = f"库存紧张:{stock}" if stock <= 3 else f"库存:{stock}"
            
            # 获取物品效果描述
            effect_desc = self._get_item_effect_short(item)
            effect_line = f"\n   效果: {effect_desc}" if effect_desc else ""
            
            market_id = item.get('market_id', '')
            code_text = f" [{market_id}]" if market_id else ""
            lines.append(f"{i}. [{item['rank']}] {item['name']} ({type_label}){code_text}{discount_text}\n   价格: {item['price']} 灵石 {stock_text}{effect_line}\n")

        if refresh_hours > 0 and last_refresh:
            remaining = (last_refresh + refresh_hours * 3600) - int(time.time())
            if remaining > 0:
                lines.append(f"\n下次刷新: {remaining // 3600}小时{(remaining % 3600) // 60}分钟后")
        lines.append(f"\n提示: 使用 '购买 [物品名/短码] [数量]' 购买物品")
        if footer_lines:
            lines.append("\n" + "\n".join(footer_lines))
        return "".join(lines)

    def _get_item_effect_short(self, item: Dict) -> str:
        """获取物品效果的简短描述"""
        data = item.get('data', {})
        item_type = item.get('type', '')
        effects = []
        
        # 武器/装备属性
        if item_type in ['weapon', 'armor', 'accessory']:
            if data.get('physical_damage', 0) > 0:
                effects.append(f"物伤+{data['physical_damage']}")
            if data.get('magic_damage', 0) > 0:
                effects.append(f"法伤+{data['magic_damage']}")
            if data.get('physical_defense', 0) > 0:
                effects.append(f"物防+{data['physical_defense']}")
            if data.get('magic_defense', 0) > 0:
                effects.append(f"法防+{data['magic_defense']}")
            if data.get('mental_power', 0) > 0:
                effects.append(f"精神力+{data['mental_power']}")
        
        # 功法属性
        elif item_type in ['main_technique', 'technique', '功法']:
            if data.get('exp_multiplier', 0) > 0:
                effects.append(f"修炼效率+{int(data['exp_multiplier']*100)}%")
            if data.get('physical_damage', 0) > 0:
                effects.append(f"物伤+{data['physical_damage']}")
            if data.get('magic_damage', 0) > 0:
                effects.append(f"法伤+{data['magic_damage']}")
            if data.get('physical_defense', 0) > 0:
                effects.append(f"物防+{data['physical_defense']}")
            if data.get('magic_defense', 0) > 0:
                effects.append(f"法防+{data['magic_defense']}")
            if data.get('mental_power', 0) > 0:
                effects.append(f"精神力+{data['mental_power']}")
            if data.get('lifespan', 0) > 0:
                effects.append(f"寿命+{data['lifespan']}")
            if data.get('spiritual_qi', 0) > 0:
                effects.append(f"灵气容量+{data['spiritual_qi']}")
            if data.get('blood_qi', 0) > 0:
                effects.append(f"气血容量+{data['blood_qi']}")
        
        # 丹药效果
        elif item_type in ['pill', 'exp_pill', 'utility_pill', 'legacy_pill']:
            # 尝试从 effect 字段获取
            effect_data = data.get('effect', {})
            if isinstance(effect_data, dict):
                if effect_data.get('add_experience', 0) > 0:
                    effects.append(f"修为+{effect_data['add_experience']}")
                if effect_data.get('add_hp', 0) > 0:
                    effects.append(f"气血+{effect_data['add_hp']}")
                if effect_data.get('add_max_hp', 0) > 0:
                    effects.append(f"气血上限+{effect_data['add_max_hp']}")
                if effect_data.get('add_attack', 0) > 0:
                    effects.append(f"攻击+{effect_data['add_attack']}")
                if effect_data.get('add_defense', 0) > 0:
                    effects.append(f"防御+{effect_data['add_defense']}")
            
            # 破境丹特殊处理
            if data.get('subtype') == 'breakthrough':
                bonus = data.get('breakthrough_bonus', 0)
                if bonus > 0:
                    effects.append(f"突破成功率+{int(bonus*100)}%")
            
            # 修为丹
            exp_gain = data.get('exp_gain', data.get('exp_boost', 0))
            if exp_gain > 0:
                effects.append(f"修为+{exp_gain}")

            direct_effects = [
                ('cultivation_multiplier', '修炼速度', 'percent'),
                ('physical_damage_multiplier', '物伤倍率', 'percent'),
                ('magic_damage_multiplier', '法伤倍率', 'percent'),
                ('physical_defense_multiplier', '物防倍率', 'percent'),
                ('magic_defense_multiplier', '法防倍率', 'percent'),
                ('physical_damage_gain', '物伤', 'number'),
                ('magic_damage_gain', '法伤', 'number'),
                ('physical_defense_gain', '物防', 'number'),
                ('magic_defense_gain', '法防', 'number'),
                ('mental_power_gain', '精神力', 'number'),
                ('lifespan_gain', '寿命', 'number'),
            ]
            for key, label, value_type in direct_effects:
                value = data.get(key, 0)
                if value:
                    formatted = f"{value:+.0%}" if value_type == 'percent' else f"{value:+d}"
                    effects.append(f"{label}{formatted}")
        
        # 材料
        elif item_type == 'material':
            if data.get('description'):
                return data['description'][:20]
        
        # 如果有描述字段，优先使用
        if not effects and data.get('description'):
            desc = data['description']
            return desc[:25] + "..." if len(desc) > 25 else desc
        
        return ", ".join(effects[:3]) if effects else ""

    def get_item_details(self, item_data: Dict) -> str:
        """获取物品详细信息

        Args:
            item_data: 物品数据字典

        Returns:
            物品详细描述
        """
        item_type = item_data.get('type', '')
        data = item_data.get('data', {})

        details = [f"名称: {item_data['name']}"]
        details.append(f"品级: {item_data['rank']}")
        details.append(f"价格: {item_data['price']} 灵石")

        description = data.get('description')
        if description:
            details.append(f"描述: {description}")

        # 武器/防具/饰品属性
        if item_type in ['weapon', 'armor', 'accessory']:
            attrs = []
            if data.get('magic_damage', 0) > 0:
                attrs.append(f"法伤+{data['magic_damage']}")
            if data.get('physical_damage', 0) > 0:
                attrs.append(f"物伤+{data['physical_damage']}")
            if data.get('magic_defense', 0) > 0:
                attrs.append(f"法防+{data['magic_defense']}")
            if data.get('physical_defense', 0) > 0:
                attrs.append(f"物防+{data['physical_defense']}")
            if data.get('mental_power', 0) > 0:
                attrs.append(f"精神力+{data['mental_power']}")
            if data.get('lifespan', 0) > 0:
                attrs.append(f"寿命+{data['lifespan']}")
            if data.get('blood_qi', 0) > 0:
                attrs.append(f"气血+{data['blood_qi']}")
            if attrs:
                details.append(f"属性: {', '.join(attrs)}")
            if 'required_level_index' in data:
                level_name = self._format_required_level(data['required_level_index'])
                details.append(f"需求境界: {level_name}")

        # 心法/功法
        elif item_type in ['main_technique', 'technique']:
            attrs = []
            if data.get('exp_multiplier', 0) > 0:
                attrs.append(f"修炼效率+{data['exp_multiplier']:.1%}")
            if data.get('spiritual_qi', 0) > 0:
                attrs.append(f"灵气+{data['spiritual_qi']}")
            if data.get('magic_damage', 0) > 0:
                attrs.append(f"法伤+{data['magic_damage']}")
            if data.get('physical_damage', 0) > 0:
                attrs.append(f"物伤+{data['physical_damage']}")
            if data.get('magic_defense', 0) > 0:
                attrs.append(f"法防+{data['magic_defense']}")
            if data.get('physical_defense', 0) > 0:
                attrs.append(f"物防+{data['physical_defense']}")
            if data.get('mental_power', 0) > 0:
                attrs.append(f"精神力+{data['mental_power']}")
            if attrs:
                details.append(f"效果: {', '.join(attrs)}")
            if 'required_level_index' in data:
                level_name = self._format_required_level(data['required_level_index'])
                details.append(f"需求境界: {level_name}")

        # 丹药类
        elif item_type in ['pill', 'exp_pill', 'utility_pill', 'legacy_pill']:
            if 'required_level_index' in data and data['required_level_index'] > 0:
                level_name = self._format_required_level(data['required_level_index'])
                details.append(f"需求境界: {level_name}")

            subtype = data.get('subtype', '')
            effect_desc = []

            if item_type == 'pill' and subtype == 'breakthrough':
                bonus = data.get('breakthrough_bonus', 0)
                max_rate = data.get('max_success_rate', 1.0)
                target = data.get('target_level_index')
                if target is not None:
                    level_name = self._format_required_level(target)
                    effect_desc.append(f"目标境界: {level_name}")
                effect_desc.append(f"突破成功率+{int(bonus * 100)}%，最高可达 {int(max_rate * 100)}%")

            elif item_type == 'exp_pill':
                exp_gain = data.get('exp_gain', 0)
                effect_desc.append(f"立即获得修为：+{exp_gain}")

            elif item_type == 'utility_pill':
                effect_type = data.get('effect_type', '')
                if subtype == 'resurrection':
                    effect_desc.append("死亡时自动复活（属性减半）")
                elif effect_type == 'temporary':
                    duration = data.get('duration_minutes', 0)
                    for key, label in [
                        ('cultivation_multiplier', '修炼速度'),
                        ('physical_damage_multiplier', '物伤倍率'),
                        ('magic_damage_multiplier', '法伤倍率'),
                        ('physical_defense_multiplier', '物防倍率'),
                        ('magic_defense_multiplier', '法防倍率'),
                    ]:
                        value = data.get(key, 0)
                        if value:
                            effect_desc.append(f"{label}{value:+.0%}")
                    if data.get('is_random'):
                        effect_desc.append("随机一项攻防倍率+500%，其余-90%")
                    effect_desc.append(f"持续时间：{duration}分钟")
                elif effect_type == 'permanent':
                    gains = []
                    for attr_key, label in [
                        ('physical_damage_gain', '物伤'),
                        ('magic_damage_gain', '法伤'),
                        ('physical_defense_gain', '物防'),
                        ('magic_defense_gain', '法防'),
                        ('mental_power_gain', '精神力'),
                        ('lifespan_gain', '寿命'),
                        ('max_spiritual_qi_gain', '最大灵气'),
                        ('max_blood_qi_gain', '最大气血')
                    ]:
                        value = data.get(attr_key)
                        if value:
                            sign = "+" if value > 0 else ""
                            gains.append(f"{label}{sign}{value}")
                    if gains:
                        effect_desc.append("永久增益：" + "，".join(gains))
                    if data.get('cultivation_multiplier'):
                        effect_desc.append(f"永久修炼速度{data['cultivation_multiplier']:+.0%}")
                    if data.get('death_protection_multiplier'):
                        effect_desc.append(f"突破死亡概率降低{1 - data['death_protection_multiplier']:.0%}")
                    if data.get('base_attribute_limit_increase'):
                        effect_desc.append(f"永久属性丹药上限提高{data['base_attribute_limit_increase']:.0%}")
                if data.get('resets_permanent_pills'):
                    refund_ratio = data.get('reset_refund_ratio', 0)
                    hint = "重置所有永久丹药增益"
                    if refund_ratio:
                        hint += f"，返还售价的{int(refund_ratio*100)}%"
                    effect_desc.append(hint)
                if data.get('blocks_next_debuff'):
                    effect_desc.append("获得定魂护盾，抵消下一次负面状态")

            elif item_type == 'legacy_pill':
                effect_data = data.get('effect', {})
                for key, label in [
                    ('add_hp', '恢复气血'),
                    ('add_experience', '增加修为'),
                    ('add_max_hp', '提升上限'),
                    ('add_attack', '物伤变化'),
                    ('add_defense', '物防变化'),
                    ('add_spiritual_power', '法伤变化'),
                    ('add_mental_power', '精神力变化'),
                    ('add_gold', '灵石变化'),
                ]:
                    value = effect_data.get(key)
                    if value:
                        sign = "+" if value > 0 else ""
                        effect_desc.append(f"{label}{sign}{value}")
                if effect_data.get('add_breakthrough_bonus'):
                    effect_desc.append(f"突破成功率+{int(effect_data['add_breakthrough_bonus']*100)}%（1小时）")

            if effect_desc:
                details.append("效果: " + "；".join(effect_desc))

        return "\n".join(details)
