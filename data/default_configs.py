# data/default_configs.py

SECT_CONFIG = {
    "create_cost": 10000,
    "create_level_required": 3, # 筑基
    "positions": {
        "0": {"name": "宗主", "permission": 10},
        "1": {"name": "长老", "permission": 8},
        "2": {"name": "亲传弟子", "permission": 5},
        "3": {"name": "内门弟子", "permission": 2},
        "4": {"name": "外门弟子", "permission": 1}
    },
    "scale_ratio": 10, # 1灵石 = 10建设度
}

BOSS_CONFIG = {
    "spawn_interval": 3600,
    "levels": [
        {"name": "练气", "level_index": 0, "hp_mult": 1.0, "atk_mult": 1.0, "reward_mult": 1.0},
        {"name": "筑基", "level_index": 3, "hp_mult": 1.5, "atk_mult": 1.2, "reward_mult": 1.5},
        {"name": "金丹", "level_index": 6, "hp_mult": 2.0, "atk_mult": 1.5, "reward_mult": 2.0},
        {"name": "元婴", "level_index": 9, "hp_mult": 2.5, "atk_mult": 1.8, "reward_mult": 2.5},
        {"name": "化神", "level_index": 12, "hp_mult": 3.0, "atk_mult": 2.0, "reward_mult": 3.0},
        {"name": "炼虚", "level_index": 15, "hp_mult": 4.0, "atk_mult": 2.5, "reward_mult": 4.0},
        {"name": "合体", "level_index": 18, "hp_mult": 5.0, "atk_mult": 3.0, "reward_mult": 5.0},
        {"name": "大乘", "level_index": 21, "hp_mult": 6.0, "atk_mult": 3.5, "reward_mult": 6.0},
    ]
}

RIFT_CONFIG = {
    "default_duration": 1800, # 30分钟
    "rifts": [
        {"id": 1, "name": "青云秘境", "level": 2, "exp_range": [100, 500], "gold_range": [50, 200]},
        {"id": 2, "name": "幽冥鬼域", "level": 5, "exp_range": [500, 2000], "gold_range": [200, 800]},
        {"id": 3, "name": "太古遗迹", "level": 10, "exp_range": [5000, 10000], "gold_range": [1000, 5000]},
    ]
}

ALCHEMY_CONFIG = {
    # 炼丹系统核心参数（批次2：炼丹系统升级）
    # 配方与丹药星级统一定义在 config/alchemy_recipes.json
    "alchemy_config": {
        "level_success_bonus": {
            "13": 0.10,
            "19": 0.20,
            "25": 0.30,
        },
        "alchemist_bonus": 0.15,
        "alchemist_extra_pill_chance": 0.1,
        "material_refund_rate": 0.5,
        "waste_pill_chance": 0.05,
        "max_success_rate": 0.95,
        "max_shop_pill_star": 2,
        "waste_pill_name": "废丹",
        # 炼丹师称号（批次3：委托炼丹）
        "alchemist_level_required": 13,      # 成为炼丹师所需境界（金丹期初期）
        "alchemist_success_required": 10,    # 成为炼丹师所需成功炼制次数
        "alchemist_cost": 10000,             # 成为炼丹师所需灵石
        "alchemist_rare_recipes": [101],     # 成为炼丹师自动解锁的稀有配方（还魂丹）
        # 委托炼丹（批次3：炼丹师职业 + 委托炼丹）
        "commission_enabled": True,          # 委托炼丹总开关
        "commission_max_quantity": 99,       # 单笔委托最大炼制数量
        "commission_max_fee": 1000000,       # 单笔委托手续费上限
        "commission_max_pending": 5,         # 每人同时挂出的未接单委托上限
        "commission_max_active": 3,          # 炼丹师同时进行中的委托上限
    }
}

# 死亡系统配置（批次1：死亡机制重构）
# 与 config/death_config.json、handlers/revival_handler.py 保持一致
DEFAULT_DEATH_CONFIG = {
    "soul_revival_hours": 24,
    "soul_exp_loss_rate": 0.05,
    "rebirth_exp_keep_rate": 0.7,
    "soul_exp_decay_per_hour": 0.01,
}

DEATH_CONFIG = {
    "death_config": dict(DEFAULT_DEATH_CONFIG),
}
