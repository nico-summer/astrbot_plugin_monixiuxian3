# handlers/admin_handlers.py
"""管理员指令处理器（批次5：管理员功能与AI文案集成）

管理员私聊bot可以：
- 创建世界事件并指定群号、难度、奖励倍率
- 查看事件模板列表
- 查看当前所有活动事件
- 强制结束某个事件
"""

from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

if TYPE_CHECKING:
    from ..managers.world_event_manager import WorldEventManager

__all__ = ["AdminHandlers"]


class AdminHandlers:
    """管理员指令处理器"""

    def __init__(self, world_event_manager: "WorldEventManager"):
        self.world_event_mgr = world_event_manager

    async def handle_create_event(self, event: AstrMessageEvent):
        """创建世界事件 <难度> <群号> [奖励倍率]"""
        # 只允许私聊
        if not event.message_obj.is_private:
            yield event.plain_result("⚠️ 该指令仅限私聊使用")
            return

        user_id = event.get_sender_id()
        if not self.world_event_mgr.is_admin(user_id):
            yield event.plain_result("⚠️ 你没有管理员权限")
            return

        args = event.message_str.strip().split()
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

        success, msg, event_id = await self.world_event_mgr.create_admin_event(
            admin_id=user_id,
            tier=tier,
            group_id=group_id,
            reward_multiplier=reward_multiplier,
        )

        yield event.plain_result(msg)

        # 如果创建成功，提示已广播
        if success and event_id:
            # 实际广播由定时任务或main.py中的逻辑处理
            pass

    async def handle_list_templates(self, event: AstrMessageEvent):
        """世界事件模板"""
        if not event.message_obj.is_private:
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
        if not event.message_obj.is_private:
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
        if not event.message_obj.is_private:
            yield event.plain_result("⚠️ 该指令仅限私聊使用")
            return

        user_id = event.get_sender_id()
        if not self.world_event_mgr.is_admin(user_id):
            yield event.plain_result("⚠️ 你没有管理员权限")
            return

        args = event.message_str.strip().split()
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
