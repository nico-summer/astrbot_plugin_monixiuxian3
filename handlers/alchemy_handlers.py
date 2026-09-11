# handlers/alchemy_handlers.py
"""炼丹系统指令处理器（批次2：丹药星级 / 配方详情 / 稀有配方学习状态）"""

from astrbot.api.event import AstrMessageEvent
from ..managers.alchemy_manager import AlchemyManager
from ..data.data_manager import DataBase
from ..models_extended import UserStatus


class AlchemyHandlers:
    def __init__(self, db: DataBase, alchemy_mgr: AlchemyManager):
        self.db = db
        self.alchemy_mgr = alchemy_mgr

    async def handle_recipes(self, event: AstrMessageEvent):
        """丹药配方（含星级、学习状态与当前成功率）"""
        user_id = event.get_sender_id()
        success, msg = await self.alchemy_mgr.get_available_recipes(user_id)
        yield event.plain_result(msg)

    async def handle_recipe_detail(self, event: AstrMessageEvent, recipe_id: int = 0):
        """配方详情 <配方ID>"""
        if not recipe_id:
            yield event.plain_result(
                "❌ 请指定配方ID\n"
                "💡 例如：配方详情 101\n"
                "💡 发送「丹药配方」可查看全部配方ID"
            )
            return

        msg = await self.alchemy_mgr.show_recipe_details(recipe_id, event.get_sender_id())
        yield event.plain_result(msg)

    async def handle_craft(self, event: AstrMessageEvent, pill_id: int):
        """炼丹 <配方ID>"""
        user_id = event.get_sender_id()

        # 检查玩家是否存在
        player = await self.db.get_player_by_id(user_id)
        if not player:
            yield event.plain_result("❌ 你还未踏入修仙之路！")
            return

        # 检查玩家状态
        user_cd = await self.db.ext.get_user_cd(user_id)
        if user_cd and user_cd.type != UserStatus.IDLE:
            current_status = UserStatus.get_name(user_cd.type)
            yield event.plain_result(f"❌ 你当前正{current_status}，无法炼丹！")
            return

        if not pill_id:
            yield event.plain_result("❌ 请输入丹药配方ID\n💡 例如：炼丹 1\n💡 发送「丹药配方」查看全部配方")
            return

        success, msg, _ = await self.alchemy_mgr.craft_pill(user_id, int(pill_id))
        yield event.plain_result(msg)

    async def handle_material_query(self, event: AstrMessageEvent, material_name: str):
        """材料查询 <材料名>（材料溯源功能）"""
        if not material_name:
            yield event.plain_result("❌ 请输入材料名称\n\n💡 例如：材料查询 灵草")
            return

        success, msg = await self.alchemy_mgr.query_material_source(material_name.strip())
        yield event.plain_result(msg)
