"""
AI文案生成器

支持 OpenAI 格式的通用 API 调用，兼容：
- OpenAI 官方
- DeepSeek (https://api.deepseek.com)
- 硅基流动 (https://api.siliconflow.cn/v1)
- OpenRouter (https://openrouter.ai/api/v1)
- 其他兼容 OpenAI API 格式的服务

配置优先级：
1. AstrBot 配置系统（通过 config_manager）
2. 插件配置文件（config/world_events.json）
3. 内置默认值

AI调用失败时自动降级为固定模板。
"""

from astrbot.api import logger
from typing import Optional


# 尝试导入 requests，如果失败则禁用AI功能
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.warning("requests库未安装，AI文案生成功能将被禁用。安装方法: pip install requests")


class AIGenerator:
    """AI文案生成器

    支持 OpenAI 格式的通用 API，失败时降级为固定模板。
    """

    def __init__(self, config: dict, plugin_config=None):
        """
        初始化AI生成器

        Args:
            config: ai_config配置字典（来自 world_events.json）
            config_manager: ConfigManager实例（从AstrBot配置读取，优先级更高）
                如果提供，会尝试从 AstrBot 配置中读取：
                - world_event_ai_enabled: 是否启用AI
                - world_event_ai_api_key: API密钥
                - world_event_ai_model: 模型名称
                - world_event_ai_base_url: API地址
        """
        self.config = config or {}
        self.plugin_config = plugin_config

        # 合并配置：优先使用 AstrBot 配置
        merged_config = self._get_merged_config()

        self.enable_intro = merged_config.get("enable_ai_intro", False)
        self.enable_summary = merged_config.get("enable_ai_summary", False)
        self.api_key = merged_config.get("ai_api_key", "")
        self.model = merged_config.get("ai_model", "gpt-3.5-turbo")
        self.base_url = merged_config.get("ai_base_url", "")
        self.fixed_fallback = merged_config.get("fixed_fallback", True)

        # 检查依赖
        if not REQUESTS_AVAILABLE:
            self.enable_intro = False
            self.enable_summary = False
            logger.warning("AI功能已禁用：requests库未安装")

        # 检查配置
        if (self.enable_intro or self.enable_summary):
            if not self.api_key:
                logger.warning("AI功能已启用但未配置API Key，将使用固定模板")
            if not self.base_url:
                logger.warning("AI功能已启用但未配置base_url，将使用固定模板")

    # 插件配置面板分组与键名映射（_conf_schema.json 的 AI_GENERATOR 分组）
    PLUGIN_CONFIG_GROUP = "AI_GENERATOR"
    PLUGIN_KEY_MAP = {
        "ENABLE_AI_INTRO": "enable_ai_intro",
        "ENABLE_AI_SUMMARY": "enable_ai_summary",
        "AI_API_KEY": "ai_api_key",
        "AI_MODEL": "ai_model",
        "AI_BASE_URL": "ai_base_url",
        "FIXED_FALLBACK": "fixed_fallback",
        "AI_PROVIDER": "ai_provider",
    }

    def _get_merged_config(self) -> dict:
        """合并配置：默认值 < world_events.json 的 ai_config < AstrBot 插件配置面板"""
        merged = {
            "enable_ai_intro": False,
            "enable_ai_summary": False,
            "ai_provider": "openai",
            "ai_api_key": "",
            "ai_model": "gpt-3.5-turbo",
            "ai_base_url": "",
            "fixed_fallback": True,
        }

        # 文件配置（config/world_events.json 的 ai_config）
        if self.config:
            merged.update({k: v for k, v in self.config.items() if v is not None})

        # AstrBot 插件配置面板（优先级最高）
        plugin_config = self.plugin_config
        if plugin_config is not None and hasattr(plugin_config, "get"):
            group = None
            try:
                group = plugin_config.get(self.PLUGIN_CONFIG_GROUP, None)
            except Exception as e:
                logger.warning(f"读取 AstrBot AI 配置失败，使用文件配置: {e}")
                group = None
            if isinstance(group, dict):
                for key, value in group.items():
                    if value is None:
                        continue
                    internal = self.PLUGIN_KEY_MAP.get(key, key)
                    if internal in merged:
                        merged[internal] = value

            # 兼容旧写法：顶层 world_event_ai_* 键
            legacy_pairs = (
                ("world_event_ai_enabled", ("enable_ai_intro", "enable_ai_summary")),
                ("world_event_ai_api_key", ("ai_api_key",)),
                ("world_event_ai_model", ("ai_model",)),
                ("world_event_ai_base_url", ("ai_base_url",)),
            )
            for legacy_key, targets in legacy_pairs:
                try:
                    legacy_value = plugin_config.get(legacy_key, None)
                except Exception:
                    legacy_value = None
                if legacy_value is None:
                    continue
                for target in targets:
                    merged[target] = bool(legacy_value) if isinstance(legacy_value, bool) else legacy_value

        return merged

    def generate_event_intro(self, event_name: str, tier: str, participant_count: int,
                           recommended_level: str) -> str:
        """
        生成世界事件开场描述

        Args:
            event_name: 事件名称
            tier: 难度等级
            participant_count: 参与人数
            recommended_level: 推荐境界

        Returns:
            str: 开场描述文案
        """
        if not self.enable_intro:
            return self._fixed_intro(event_name, tier)

        prompt = f"""你是一个修仙小说作家。请为世界事件生成开场描述。

事件信息：
- 事件名称：{event_name}
- 难度：{tier}
- 参与人数：{participant_count}人
- 推荐境界：{recommended_level}

要求：
1. 修仙小说风格，营造紧张氛围
2. 100字左右
3. 不要出现具体玩家名称
4. 突出事件的危险性和机遇性

直接输出描述文案，不要其他内容："""

        # 调用OpenAI格式API（增加 max_tokens 避免截断）
        text = self._call_openai_api(prompt, max_tokens=500)

        # 降级处理
        if text is None and self.fixed_fallback:
            return self._fixed_intro(event_name, tier)

        return text or f"【{event_name}】降临！"

    def generate_event_summary(self, event_name: str, participants: list,
                              deaths: list, survivors: list,
                              rewards_summary: str = "") -> str:
        """
        生成世界事件战斗总结

        Args:
            event_name: 事件名称
            participants: 所有参与者名称列表
            deaths: 阵亡者名称列表
            survivors: 幸存者名称列表
            rewards_summary: 本次稀有掉落摘要（可选，v3.9.1 修复：此前调用方传了
                该参数但函数未接收，导致 AI 总结每次都抛错降级为固定模板）

        Returns:
            str: 战斗总结文案
        """
        if not self.enable_summary:
            return self._fixed_summary(event_name, deaths, survivors)

        reward_line = f"\n- 稀有掉落：{rewards_summary}" if rewards_summary else ""
        prompt = f"""你是一个修仙小说作家。请为世界事件生成战斗总结。

事件信息：
- 事件名称：{event_name}
- 参与人数：{len(participants)}人
- 阵亡人数：{len(deaths)}人
- 幸存人数：{len(survivors)}人{reward_line}

要求：
1. 修仙小说风格，含阵亡与幸存者的故事
2. 150-200字
3. 不要具体列举玩家名字（会在后面单独列出）
4. 突出战斗的惨烈和幸存者的不易
5. 有稀有掉落时可自然带过一句

直接输出总结文案，不要其他内容："""

        # 调用OpenAI格式API（增加 max_tokens 避免截断）
        text = self._call_openai_api(prompt, max_tokens=600)

        # 降级处理
        if text is None and self.fixed_fallback:
            return self._fixed_summary(event_name, deaths, survivors)

        return text or f"【{event_name}】结束！"

    def _call_openai_api(self, prompt: str, max_tokens: int = 200) -> Optional[str]:
        """
        调用 OpenAI 格式的通用 API

        Args:
            prompt: 提示词
            max_tokens: 最大token数

        Returns:
            Optional[str]: 生成的文本，失败返回None
        """
        if not REQUESTS_AVAILABLE:
            return None

        if not self.api_key or not self.base_url:
            return None

        try:
            # 构建请求
            url = f"{self.base_url.rstrip('/')}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "model": self.model,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": max_tokens,
                "temperature": 0.8
            }

            # 发送请求
            response = requests.post(url, json=data, headers=headers, timeout=30)
            response.raise_for_status()

            # 解析响应
            result = response.json()

            # 添加调试日志
            logger.debug(f"AI API 原始响应: {result}")

            # 获取生成的内容
            message = result["choices"][0]["message"]
            text = message.get("content", "").strip()

            if not text:
                logger.warning(f"AI生成返回空内容，原始响应: {result}")

            logger.info(f"AI生成成功 (模型: {self.model}, 长度: {len(text)}字)")
            return text

        except requests.exceptions.Timeout:
            logger.warning("AI API调用超时")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"AI API调用失败: {e}")
            return None
        except (KeyError, IndexError) as e:
            logger.warning(f"AI响应解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"AI调用异常: {e}")
            return None

    @staticmethod
    def _fixed_intro(event_name: str, tier: str) -> str:
        """固定模板：开场描述"""
        tier_desc = {
            "低阶": "灵气波动传来，似有异宝出世！",
            "中阶": "天地变色，妖魔现世，危机四伏！",
            "高阶": "天劫降临，魔气滔天，生死一线间！",
            "史诗": "混沌撕裂，上古凶兽苏醒，末日将至！"
        }

        desc = tier_desc.get(tier, "天地异象！")
        return f"【{event_name}】\n\n{desc}\n诸位道友速速前来，此番机缘与凶险并存，切莫大意！"

    @staticmethod
    def _fixed_summary(event_name: str, deaths: list, survivors: list) -> str:
        """固定模板：战斗总结"""
        if not deaths:
            return f"【{event_name}】已平息！诸位道友齐心协力，力克强敌，无人陨落，实乃大幸！"
        elif not survivors:
            return f"【{event_name}】惨烈收场！诸位道友浴血奋战，虽全军覆没，但其英勇事迹当传颂后世！"
        else:
            death_rate = len(deaths) / (len(deaths) + len(survivors)) * 100
            if death_rate < 30:
                tone = "虽有道友陨落，但大部分修士成功脱险"
            elif death_rate < 60:
                tone = "战况惨烈，伤亡惨重"
            else:
                tone = "几近团灭，仅有少数幸存者逃出生天"

            return f"【{event_name}】终于落幕！此战{tone}。愿逝者安息，生者珍惜。"
