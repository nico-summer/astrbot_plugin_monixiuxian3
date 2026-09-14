# handlers/admin_handlers.py
"""管理员指令处理器（批次5：管理员功能与AI文案集成）

管理员私聊bot可以：
- 创建世界事件并指定群号、难度、奖励倍率
- 查看事件模板列表
- 查看当前所有活动事件
- 强制结束某个事件
"""

from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

if TYPE_CHECKING:
    from ..managers.world_event_manager import WorldEventManager

__all__ = ["AdminHandlers"]


class AdminHandlers:
    """管理员指令处理器（仅私聊）

    通过 ``broadcaster`` 回调把新建事件广播到目标群（由 main.py 注入），
    因为处理器本身只负责生成回复内容，不直接操作平台会话。
    """

    def __init__(self, world_event_manager: "WorldEventManager", broadcaster=None):
        self.world_event_mgr = world_event_manager
        self.broadcaster = broadcaster

    @staticmethod
    def _is_private(event: AstrMessageEvent) -> bool:
        """判断是否私聊（兼容不同 AstrBot 版本的适配器实现）"""
        group_id = None
        getter = getattr(event, "get_group_id", None)
        if callable(getter):
            try:
                group_id = getter()
            except Exception:
                group_id = None
        if group_id:
            return False

        message_obj = getattr(event, "message_obj", None)
        is_private = getattr(message_obj, "is_private", None)
        if isinstance(is_private, bool):
            return is_private

        # 取不到群号时视为私聊
        return True

    @staticmethod
    def _command_args(event: AstrMessageEvent) -> list:
        """按空格切分指令参数（首项为指令名）"""
        text = str(getattr(event, "message_str", "") or "").strip()
        return text.split()


    async def handle_create_event(self, event: AstrMessageEvent):
        """创建世界事件 <难度> <群号> [奖励倍率]"""
        # 只允许私聊
        if not self._is_private(event):
            yield event.plain_result("⚠️ 该指令仅限私聊使用")
            return

        user_id = event.get_sender_id()
        if not self.world_event_mgr.is_admin(user_id):
            yield event.plain_result("⚠️ 你没有管理员权限")
            return

        args = self._command_args(event)
        if len(args) < 3:
            yield event.plain_result(
                "用法：创建世界事件 <难度> <群号> [奖励倍率]\n"
                "━━━━━━━━━━━━━━━\n"
                "难度：低阶 / 中阶 / 高阶 / 史诗\n"
                "群号：目标群的群号\n"
                "奖励倍率：可选，默认2.0\n"
                "━━━━━━━━━━━━━━━\n"
                "例如：创建世界事件 高阶 123456789 3.0\n"
                "💡 发送「世界事件模板」查看所有事件模板"
            )
            return

        tier_input = args[1]
        group_id = str(args[2])

        # 难度名称映射
        tier_map = {
            "低阶": "low",
            "中阶": "mid",
            "高阶": "high",
            "史诗": "epic",
            "low": "low",
            "mid": "mid",
            "high": "high",
            "epic": "epic",
        }
        tier = tier_map.get(tier_input)
        if not tier:
            yield event.plain_result(
                f"❌ 无效的难度：{tier_input}\n"
                "可选：低阶 / 中阶 / 高阶 / 史诗"
            )
            return

        # 奖励倍率（可选）
        reward_multiplier = 2.0
        if len(args) >= 4:
            try:
                reward_multiplier = float(args[3])
                if reward_multiplier < 0.1 or reward_multiplier > 10.0:
                    yield event.plain_result("❌ 奖励倍率必须在 0.1 ~ 10.0 之间")
                    return
            except ValueError:
                yield event.plain_result("❌ 奖励倍率必须是数字")
                return

        success, success_msg, event_id = await self.world_event_mgr.create_admin_event(
            admin_id=user_id,
            tier=tier,
            group_id=group_id,
            reward_multiplier=reward_multiplier,
        )

        if not success:
            yield event.plain_result(success_msg)  # success_msg 此时是错误消息
            return

        # 成功：直接使用返回的成功消息
        yield event.plain_result(success_msg)

        # 创建成功后立即广播到目标群（含 AI 开场文案，AI 未开启时用固定模板）
        if success and event_id:
            if not self.broadcaster:
                logger.warning(f"[世界事件] broadcaster 未配置，无法广播事件到群 {group_id}")
                yield event.plain_result("⚠️ 事件已创建，但 broadcaster 未配置，请手动通知群成员")
            else:
                try:
                    # 确保 event_id 是整数
                    event_id_int = int(event_id) if not isinstance(event_id, int) else event_id
                    logger.info(f"[世界事件] 准备广播事件 ID={event_id_int} 到群 {group_id}")

                    created_event = await self.world_event_mgr.get_event(event_id_int)
                    if created_event:
                        event_data = created_event.get("data") or {}
                        intro = await self.world_event_mgr.generate_intro_text(event_data, 0)
                        text = self.world_event_mgr.format_event_broadcast(created_event, 0, intro)

                        logger.info(f"[世界事件] 开始调用 broadcaster，目标群 {group_id}")
                        await self.broadcaster(str(group_id), text)
                        logger.info(f"[世界事件] 管理员事件已广播到群 {group_id}")
                        yield event.plain_result(f"✅ 事件已成功广播到群 {group_id}")
                    else:
                        logger.warning(f"[世界事件] 未能查询到刚创建的事件 ID={event_id_int}")
                        yield event.plain_result(f"⚠️ 事件已创建，但查询失败，无法广播")
                except Exception as e:
                    logger.error(f"[世界事件] 管理员事件广播失败: {e}", exc_info=True)
                    yield event.plain_result(f"⚠️ 事件已创建，但广播失败：{e}")

    async def handle_list_templates(self, event: AstrMessageEvent):
        """世界事件模板"""
        if not self._is_private(event):
            yield event.plain_result("⚠️ 该指令仅限私聊使用")
            return

        user_id = event.get_sender_id()
        if not self.world_event_mgr.is_admin(user_id):
            yield event.plain_result("⚠️ 你没有管理员权限")
            return

        msg = await self.world_event_mgr.list_event_templates()
        yield event.plain_result(msg)

    async def handle_active_events(self, event: AstrMessageEvent):
        """查看世界事件（管理员查看所有活动事件）"""
        if not self._is_private(event):
            yield event.plain_result("⚠️ 该指令仅限私聊使用")
            return

        user_id = event.get_sender_id()
        if not self.world_event_mgr.is_admin(user_id):
            yield event.plain_result("⚠️ 你没有管理员权限")
            return

        msg = await self.world_event_mgr.get_active_events()
        yield event.plain_result(msg)

    async def handle_force_end_event(self, event: AstrMessageEvent):
        """结束世界事件 <事件ID>"""
        if not self._is_private(event):
            yield event.plain_result("⚠️ 该指令仅限私聊使用")
            return

        user_id = event.get_sender_id()
        if not self.world_event_mgr.is_admin(user_id):
            yield event.plain_result("⚠️ 你没有管理员权限")
            return

        args = self._command_args(event)
        if len(args) < 2:
            yield event.plain_result(
                "用法：结束世界事件 <事件ID>\n"
                "━━━━━━━━━━━━━━━\n"
                "例如：结束世界事件 42\n"
                "💡 发送「查看世界事件」查看所有活动事件及其ID"
            )
            return

        try:
            event_id = int(args[1])
        except ValueError:
            yield event.plain_result("❌ 事件ID必须是数字")
            return

        success, msg = await self.world_event_mgr.force_end_event(user_id, event_id)
        yield event.plain_result(msg)
