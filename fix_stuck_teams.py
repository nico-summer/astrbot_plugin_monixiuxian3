#!/usr/bin/env python3
"""
修复卡住的组队状态

这个脚本用于修复因使用"完成探索"导致的队伍状态不一致问题：
- 队员个人状态已清除，但队伍状态仍然是"探索中"
- 将所有卡住的队伍状态重置为"待命中"
"""

import asyncio
import aiosqlite
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def fix_stuck_teams():
    """修复卡住的队伍状态"""

    # 数据库路径（需要根据实际情况调整）
    db_path = "./data/xiuxian.db"

    if not os.path.exists(db_path):
        print(f"❌ 数据库文件不存在: {db_path}")
        print("请确认数据库路径是否正确")
        return

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row

        # 1. 查找状态为"探索中"的队伍
        async with conn.execute(
            "SELECT team_id, leader_id, rift_id FROM teams WHERE status = 'exploring'"
        ) as cursor:
            exploring_teams = await cursor.fetchall()

        if not exploring_teams:
            print("✅ 没有发现卡住的队伍")
            return

        print(f"🔍 发现 {len(exploring_teams)} 个状态为'探索中'的队伍")

        fixed_count = 0
        for team in exploring_teams:
            team_id = team["team_id"]
            leader_id = team["leader_id"]
            rift_id = team["rift_id"]

            # 2. 获取队伍成员
            async with conn.execute(
                "SELECT user_id FROM team_members WHERE team_id = ?",
                (team_id,)
            ) as cursor:
                members = await cursor.fetchall()

            if not members:
                print(f"⚠️  队伍 {team_id} 没有成员，跳过")
                continue

            # 3. 检查成员的CD状态
            all_members_idle = True
            for member in members:
                user_id = member["user_id"]
                async with conn.execute(
                    "SELECT type FROM user_cd WHERE user_id = ?",
                    (user_id,)
                ) as cursor:
                    cd_row = await cursor.fetchone()
                    if cd_row and cd_row["type"] != 0:  # 0 = IDLE
                        all_members_idle = False
                        break

            # 4. 如果所有成员都是空闲状态，说明队伍卡住了
            if all_members_idle:
                print(f"🔧 修复队伍 {team_id}（队长: {leader_id[:8]}...）")

                # 重置队伍状态为"待命中"
                await conn.execute(
                    "UPDATE teams SET status = 'waiting', rift_id = NULL WHERE team_id = ?",
                    (team_id,)
                )
                fixed_count += 1
            else:
                print(f"✓  队伍 {team_id} 仍在正常探索中，保持不变")

        await conn.commit()

        if fixed_count > 0:
            print(f"\n✅ 成功修复 {fixed_count} 个卡住的队伍！")
        else:
            print(f"\n✅ 所有队伍状态正常，无需修复")

if __name__ == "__main__":
    print("=" * 50)
    print("组队状态修复工具")
    print("=" * 50)
    print()

    asyncio.run(fix_stuck_teams())
