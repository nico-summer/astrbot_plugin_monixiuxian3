# handlers/spirit_farm_handlers.py
"""灵田处理器"""
from astrbot.api.event import AstrMessageEvent
from ..data import DataBase
from ..managers.spirit_farm_manager import SpiritFarmManager
from ..models import Player
from .utils import player_required

__all__ = ["SpiritFarmHandlers"]


class SpiritFarmHandlers:
    """灵田处理器"""
    
    def __init__(self, db: DataBase, farm_mgr: SpiritFarmManager):
        self.db = db
        self.mgr = farm_mgr
    
    @player_required
    async def handle_farm_info(self, player: Player, event: AstrMessageEvent):
        """查看灵田信息"""
        info = await self.mgr.get_farm_info(player.user_id)
        yield event.plain_result(info)
    
    @player_required
    async def handle_create_farm(self, player: Player, event: AstrMessageEvent):
        """开垦灵田"""
        success, msg = await self.mgr.create_farm(player)
        yield event.plain_result(msg)
    
    @player_required
    async def handle_plant(self, player: Player, event: AstrMessageEvent, herb_name: str = "", count_str: str = ""):
        """种植灵草"""
        if not herb_name.strip():
            yield event.plain_result(
                "🌱 可种植的灵草\n"
                "━━━━━━━━━━━━━━━\n"
                "灵草 - 1小时 (修为+500)\n"
                "血灵草 - 2小时 (修为+1500)\n"
                "冰心草 - 4小时 (修为+4000)\n"
                "火焰花 - 8小时 (修为+10000)\n"
                "九叶灵芝 - 24小时 (修为+30000)\n"
                "━━━━━━━━━━━━━━━\n"
                "💡 使用 /种植 <灵草名> [数量]"
            )
            return

        herb_name = herb_name.strip()

        # 检查是否指定数量（批量种植）
        if count_str.strip():
            try:
                count = int(count_str.strip())
                if count <= 0:
                    yield event.plain_result("❌ 数量必须大于0！")
                    return
                if count == 1:
                    # 数量为1时使用单个种植
                    success, msg = await self.mgr.plant_herb(player, herb_name)
                else:
                    # 批量种植
                    success, msg = await self.mgr.batch_plant_herb(player, herb_name, count)
                yield event.plain_result(msg)
            except ValueError:
                yield event.plain_result("❌ 数量必须是正整数！")
        else:
            # 单个种植
            success, msg = await self.mgr.plant_herb(player, herb_name)
            yield event.plain_result(msg)
    
    @player_required
    async def handle_harvest(self, player: Player, event: AstrMessageEvent):
        """收获灵草"""
        success, msg = await self.mgr.harvest(player)
        yield event.plain_result(msg)
    
    @player_required
    async def handle_upgrade_farm(self, player: Player, event: AstrMessageEvent):
        """升级灵田"""
        success, msg = await self.mgr.upgrade_farm(player)
        yield event.plain_result(msg)
