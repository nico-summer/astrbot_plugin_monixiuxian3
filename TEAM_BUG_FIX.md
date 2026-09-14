# 组队系统漏洞修复报告

## 问题概述

根据 2026-09-14 的聊天记录，组队系统存在严重的状态不一致问题，导致队伍卡死在"探索中"状态，无法继续使用。

## 问题分析

### 根本原因

**指令混淆漏洞**：单人秘境的"完成探索"指令没有检查玩家是否在组队探索中，导致组队成员可以绕过队长权限直接结算。

### 问题复现路径

1. 队长使用 `/组队探索 <秘境ID>` 发起组队探索
2. 系统设置所有队员的个人状态为 `EXPLORING`，并在 `extra_data` 中标记 `is_team_exploration: True`
3. 系统设置队伍状态为 `exploring`
4. **漏洞触发**：队员使用 `/完成探索`（单人指令）而不是等待队长使用 `/完成组队`
5. `finish_exploration()` 函数没有检查 `is_team_exploration` 标记，直接结算并清除个人状态
6. 队员获得奖励，个人 CD 被清除
7. 队长后来也使用 `/完成探索`，同样获得奖励并清除个人状态
8. **结果**：两人都结算了，但队伍状态仍然是 `exploring`，队伍彻底卡死

### 影响范围

1. **重复奖励**：每个队员都能单独结算，获得多次奖励
2. **队伍卡死**：队伍状态与成员状态不一致，无法进行新的探索
3. **状态混乱**：显示"还需要 27 分钟"等错误信息

## 已实施的修复

### 修复 1：阻止组队成员使用单人指令

**文件**：`managers/rift_manager.py`  
**位置**：`finish_exploration()` 函数，第 785-788 行

```python
# 2.5. 检查是否为组队探索（组队探索必须由队长使用"完成组队"指令）
extra_data = user_cd.get_extra_data() if hasattr(user_cd, 'get_extra_data') else {}
is_team_exploration = extra_data.get("is_team_exploration", False)
if is_team_exploration:
    return False, "❌ 你正在进行组队探索！请由队长使用 /完成组队 指令来结算奖励。", None
```

**效果**：
- 阻止组队成员使用 `/完成探索` 指令
- 引导用户使用正确的 `/完成组队` 指令
- 防止状态不一致和重复奖励

## 数据修复工具

### 修复卡住的队伍

**文件**：`fix_stuck_teams.py`

**功能**：
- 自动检测状态为"探索中"但所有成员都是空闲状态的队伍
- 将这些卡住的队伍重置为"待命中"状态
- 清除关联的秘境ID

**使用方法**：

```bash
cd /Users/charmdeer/Desktop/jobCode/my-code/astrbot_plugin_monixiuxian2
python fix_stuck_teams.py
```

**注意事项**：
- 在运行前请先备份数据库
- 确认数据库路径正确（默认为 `./data/xiuxian.db`）
- 建议在机器人停机时运行

## 测试验证

### 测试场景 1：组队探索正常流程

1. 队长创建队伍并邀请队员
2. 队长使用 `/组队探索 <秘境ID>` 发起探索
3. 等待探索完成
4. 队长使用 `/完成组队` 结算奖励
5. ✅ 预期结果：所有队员获得奖励，队伍状态恢复为"待命中"

### 测试场景 2：队员尝试使用错误指令（修复后）

1. 队长创建队伍并发起组队探索
2. 队员尝试使用 `/完成探索`（单人指令）
3. ✅ 预期结果：系统拒绝，提示"你正在进行组队探索！请由队长使用 /完成组队 指令来结算奖励。"

### 测试场景 3：卡住的队伍修复

1. 运行 `fix_stuck_teams.py`
2. ✅ 预期结果：卡住的队伍状态被重置为"待命中"
3. 验证：队长可以正常使用 `/队伍信息` 和 `/组队探索`

## 代码改进建议

### 短期改进（高优先级）

1. **状态同步检查**：在 `finish_team_exploration()` 中添加额外的状态验证
2. **错误恢复机制**：当检测到状态不一致时自动修复
3. **日志记录**：记录所有组队探索的开始和结束，便于追踪问题

### 长期改进（中优先级）

1. **事务一致性**：使用数据库事务确保队伍状态和成员状态的原子性更新
2. **状态机设计**：实现严格的状态转换规则，防止非法状态转换
3. **权限检查增强**：所有组队相关操作都应检查调用者的权限和队伍状态

### 代码示例：状态同步检查

```python
async def finish_team_exploration(self, team_id: int, leader_id: str, team_manager):
    """完成组队探索（增强版）"""
    
    # 现有检查...
    
    # 新增：验证所有队员的状态一致性
    inconsistent_members = []
    for member_id in members:
        user_cd = await self.db.ext.get_user_cd(member_id)
        if not user_cd or user_cd.type != UserStatus.EXPLORING:
            inconsistent_members.append(member_id)
        else:
            extra_data = user_cd.get_extra_data() if hasattr(user_cd, 'get_extra_data') else {}
            if not extra_data.get("is_team_exploration") or extra_data.get("team_id") != team_id:
                inconsistent_members.append(member_id)
    
    if inconsistent_members:
        # 记录警告并尝试自动修复
        logger.warning(f"Team {team_id} has inconsistent member states: {inconsistent_members}")
        # 可以选择：1) 拒绝结算并提示联系管理员 2) 自动修复状态
```

## 历史记录

- **2026-09-14**：问题首次报告（用户：原原十六神、挽剑）
- **2026-09-14**：问题分析完成，识别为指令混淆漏洞
- **2026-09-14**：实施修复，添加组队探索检查
- **2026-09-14**：创建数据修复工具 `fix_stuck_teams.py`

## 相关文件

- `managers/rift_manager.py` - 秘境管理器（已修复）
- `managers/team_manager.py` - 队伍管理器
- `handlers/team_handlers.py` - 组队指令处理器
- `handlers/rift_handlers.py` - 秘境指令处理器
- `fix_stuck_teams.py` - 数据修复工具（新增）

## 联系方式

如有问题或需要进一步协助，请联系开发团队。
