# handlers/rift_handlers.py
from astrbot.api.event import AstrMessageEvent
from ..managers.rift_manager import RiftManager
from ..data.data_manager import DataBase
from .utils import soul_state_block_message

class RiftHandlers:
    def __init__(self, db: DataBase, rift_mgr: RiftManager):
        self.db = db
        self.rift_mgr = rift_mgr

    async def handle_rift_list(self, event: AstrMessageEvent):
        """秘境列表"""
        user_id = event.get_sender_id()
        player = await self.db.get_player_by_id(user_id)
        success, msg = await self.rift_mgr.list_rifts(player)
        yield event.plain_result(msg)

    async def handle_rift_explore(self, event: AstrMessageEvent, rift_id: int):
        """探索秘境"""
        user_id = event.get_sender_id()
        if not rift_id:
            yield event.plain_result("❌ 请输入秘境ID")
            return

        player = await self.db.get_player_by_id(user_id)
        if player and player.is_soul_state:
            yield event.plain_result(soul_state_block_message("进行秘境探索"))
            return
        success, msg = await self.rift_mgr.enter_rift(user_id, int(rift_id))
        yield event.plain_result(msg)
        
    async def handle_rift_complete(self, event: AstrMessageEvent):
        """完成探索"""
        user_id = event.get_sender_id()
        success, msg, _ = await self.rift_mgr.finish_exploration(user_id)
        yield event.plain_result(msg)
    
    async def handle_rift_exit(self, event: AstrMessageEvent):
        """退出秘境"""
        user_id = event.get_sender_id()
        success, msg = await self.rift_mgr.exit_rift(user_id)
        yield event.plain_result(msg)

    async def handle_sect_rift(self, event: AstrMessageEvent, level: int):
        """进入宗门秘境"""
        user_id = event.get_sender_id()

        player = await self.db.get_player_by_id(user_id)
        if player and player.is_soul_state:
            yield event.plain_result(soul_state_block_message("进行秘境探索"))
            return
        success, msg = await self.rift_mgr.enter_sect_rift(user_id, level)
        yield event.plain_result(msg)
