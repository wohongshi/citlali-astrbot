"""
好感度管理器
追踪每个用户与茜特菈莉的关系阶段和好感度数值。
数据持久化到 JSON 文件。
"""
import json
import time
import os
from enum import IntEnum
from typing import Optional


class AffinityStage(IntEnum):
    """关系阶段"""
    STRANGER = 0       # 陌生人
    ACQUAINTANCE = 1   # 熟人
    FRIEND = 2         # 朋友
    CLOSE_FRIEND = 3   # 好友
    CONFIDANT = 4      # 知己
    TRAVELER = 5       # 旅行者（最高）


STAGE_NAMES = {
    AffinityStage.STRANGER: "陌生人",
    AffinityStage.ACQUAINTANCE: "熟人",
    AffinityStage.FRIEND: "朋友",
    AffinityStage.CLOSE_FRIEND: "好友",
    AffinityStage.CONFIDANT: "知己",
    AffinityStage.TRAVELER: "旅行者",
}

STAGE_THRESHOLDS = {
    AffinityStage.STRANGER: 0,
    AffinityStage.ACQUAINTANCE: 50,
    AffinityStage.FRIEND: 150,
    AffinityStage.CLOSE_FRIEND: 400,
    AffinityStage.CONFIDANT: 800,
    AffinityStage.TRAVELER: 1500,
}

# 好感度获取规则
AFFINITY_RULES = {
    "daily_chat": (5, 15),         # 每日对话 5-15
    "topic_novel": (10, 20),       # 聊小说
    "topic_wine": (8, 15),         # 聊酒
    "topic_divination": (10, 18),  # 占卜
    "call_grandma": (15, 25),      # 叫"奶奶"
    "long_conversation": (12, 20), # 长对话(>10轮)
    "care_about_her": (20, 30),    # 关心她
    "see_through_her": (25, 35),   # 看穿她
    "daily_decay": (-3, -1),       # 每日衰减(不说话)
}


class AffinityManager:
    """好感度管理器"""

    def __init__(self, data_dir: str):
        self.data_file = os.path.join(data_dir, "citlali_affinity.json")
        self.data: dict = {}
        self._load()

    def _load(self):
        """加载数据"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.data = {}

    def _save(self):
        """保存数据"""
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def _ensure_user(self, user_id: str) -> dict:
        """确保用户数据存在"""
        if user_id not in self.data:
            self.data[user_id] = {
                "affinity": 0,
                "stage": AffinityStage.STRANGER,
                "first_seen": time.time(),
                "last_active": time.time(),
                "total_messages": 0,
                "daily_messages": 0,
                "last_daily_reset": 0,
                "last_checkin": 0,
                "milestones": [],
                "notes": "",
                "nickname": "",
            }
        return self.data[user_id]

    def get_user(self, user_id: str) -> dict:
        """获取用户数据"""
        return self._ensure_user(user_id)

    def get_all_users(self) -> dict:
        """获取所有用户数据"""
        return self.data

    def get_stage(self, user_id: str) -> AffinityStage:
        """获取用户当前关系阶段"""
        user = self._ensure_user(user_id)
        return AffinityStage(user.get("stage", 0))

    def get_stage_name(self, user_id: str) -> str:
        """获取关系阶段名称"""
        return STAGE_NAMES[self.get_stage(user_id)]

    def add_affinity(self, user_id: str, reason: str, amount: Optional[int] = None) -> tuple[int, bool]:
        """
        增加好感度
        返回: (实际增加量, 是否升级)
        """
        import random
        user = self._ensure_user(user_id)
        user["last_active"] = time.time()
        user["total_messages"] += 1
        user["daily_messages"] += 1

        # 计算增加量
        if amount is not None:
            delta = amount
        elif reason in AFFINITY_RULES:
            lo, hi = AFFINITY_RULES[reason]
            delta = random.randint(lo, hi)
        else:
            delta = random.randint(3, 10)

        # 每日消息上限
        if user["daily_messages"] > 30:
            delta = max(1, delta // 3)

        old_affinity = user["affinity"]
        user["affinity"] = max(0, old_affinity + delta)
        old_stage = AffinityStage(user["stage"])

        # 检查是否升级
        new_stage = old_stage
        for stage in sorted(AffinityStage, reverse=True):
            if user["affinity"] >= STAGE_THRESHOLDS[stage]:
                new_stage = stage
                break

        upgraded = new_stage > old_stage
        if upgraded:
            user["stage"] = new_stage
            user["milestones"].append({
                "type": "stage_up",
                "from": old_stage,
                "to": new_stage,
                "time": time.time(),
                "affinity": user["affinity"],
            })

        self._save()
        return delta, upgraded

    def decay_daily(self) -> int:
        """每日衰减，返回处理的用户数"""
        now = time.time()
        count = 0
        for user_id, user in self.data.items():
            # 每天只衰减一次
            if now - user.get("last_daily_reset", 0) < 86400:
                continue
            user["last_daily_reset"] = now
            user["daily_messages"] = 0

            # 超过3天不活跃开始衰减
            inactive_days = (now - user["last_active"]) / 86400
            if inactive_days > 3:
                import random
                lo, hi = AFFINITY_RULES["daily_decay"]
                decay = random.randint(lo, hi)
                user["affinity"] = max(0, user["affinity"] + decay)

                # 检查是否降级
                current_stage = AffinityStage(user["stage"])
                for stage in sorted(AffinityStage, reverse=True):
                    if user["affinity"] >= STAGE_THRESHOLDS[stage]:
                        if stage < current_stage:
                            user["stage"] = stage
                            user["milestones"].append({
                                "type": "stage_down",
                                "from": current_stage,
                                "to": stage,
                                "time": now,
                            })
                        break
                count += 1

        if count > 0:
            self._save()
        return count

    def set_nickname(self, user_id: str, nickname: str):
        """设置用户昵称"""
        user = self._ensure_user(user_id)
        user["nickname"] = nickname
        self._save()

    def add_note(self, user_id: str, note: str):
        """添加备注"""
        user = self._ensure_user(user_id)
        if user["notes"]:
            user["notes"] += "\n" + note
        else:
            user["notes"] = note
        self._save()

    def get_leaderboard(self, limit: int = 20) -> list:
        """获取好感度排行"""
        sorted_users = sorted(
            self.data.items(),
            key=lambda x: x[1].get("affinity", 0),
            reverse=True
        )[:limit]
        result = []
        for user_id, user in sorted_users:
            result.append({
                "user_id": user_id,
                "nickname": user.get("nickname", ""),
                "affinity": user.get("affinity", 0),
                "stage": STAGE_NAMES.get(AffinityStage(user.get("stage", 0)), "未知"),
                "total_messages": user.get("total_messages", 0),
                "last_active": user.get("last_active", 0),
            })
        return result

    def get_stats(self) -> dict:
        """获取统计信息"""
        total_users = len(self.data)
        stage_counts = {}
        for s in AffinityStage:
            stage_counts[STAGE_NAMES[s]] = 0
        total_messages = 0
        for user in self.data.values():
            stage = AffinityStage(user.get("stage", 0))
            stage_counts[STAGE_NAMES[stage]] += 1
            total_messages += user.get("total_messages", 0)
        return {
            "total_users": total_users,
            "stage_counts": stage_counts,
            "total_messages": total_messages,
        }

    def reset_user(self, user_id: str):
        """重置用户数据"""
        if user_id in self.data:
            del self.data[user_id]
            self._save()
