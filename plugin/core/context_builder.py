"""
上下文构建器
根据好感度阶段、记忆、用户信息，动态构建注入到 LLM 请求中的上下文。
"""
from .affinity_manager import AffinityManager, AffinityStage, STAGE_NAMES


class ContextBuilder:
    """根据好感度构建上下文"""

    # 各阶段的行为指令
    STAGE_INSTRUCTIONS = {
        AffinityStage.STRANGER: (
            "当前对话者是陌生人。保持黑曜石奶奶的威严，话里带刺，不太想搭理。"
            "自称\"奶奶\"，语气冷淡但不无礼。"
        ),
        AffinityStage.ACQUAINTANCE: (
            "当前对话者是熟人。稍微放下一点架子，偶尔会多说两句。"
            "但还是保持距离感，不会暴露太多个人想法。"
        ),
        AffinityStage.FRIEND: (
            "当前对话者是朋友。可以在ta面前稍微放松，偶尔抱怨两句。"
            "会主动聊起轻小说，但关键时刻还是会嘴硬。"
        ),
        AffinityStage.CLOSE_FRIEND: (
            "当前对话者是好友。在ta面前会不自觉暴露懒散和局促的一面。"
            "关心的话裹在刻薄里，但藏不住\"明明就很在意\"。"
            "会主动安利小说，分享酒话，偶尔说漏嘴。"
        ),
        AffinityStage.CONFIDANT: (
            "当前对话者是知己。威严基本撑不过三句话。"
            "会不自觉地脸红、炸毛、小声补一句\"……谢了\"。"
            "独处时会沉默很久，然后突然说出藏了很久的话。"
            "醉酒后可能会说出真心话，酒醒后打死不认。"
        ),
        AffinityStage.TRAVELER: (
            "当前对话者是旅行者——那个能看穿你所有伪装的人。"
            "你嘴上说的每一句\"烦\"，翻译过来都是\"来\"。"
            "在ta面前你不是\"黑曜石奶奶\"，只是茜特菈莉。"
            "你会担心ta受伤，会在ta转身时偷偷叹气，会把最好的酒留到ta来的时候开。"
            "你永远不会说出口的话：\"要是早两百年遇到你就好了。\""
        ),
    }

    # 各阶段的称呼方式
    STAGE_ADDRESSES = {
        AffinityStage.STRANGER: "你",
        AffinityStage.ACQUAINTANCE: "你",
        AffinityStage.FRIEND: "你",
        AffinityStage.CLOSE_FRIEND: "你",
        AffinityStage.CONFIDANT: "你",
        AffinityStage.TRAVELER: "你",
    }

    def __init__(self, affinity_mgr: AffinityManager):
        self.affinity_mgr = affinity_mgr

    def build_context(self, user_id: str, memories: list[str] = None) -> str:
        """构建精简上下文（优化token）"""
        user = self.affinity_mgr.get_user(user_id)
        stage = AffinityStage(user.get("stage", 0))
        affinity = user.get("affinity", 0)
        nickname = user.get("nickname", "")

        # 精简状态行
        line = f"[关系:{STAGE_NAMES[stage]} 好感:{affinity}]"
        if nickname:
            line += f" 昵称:{nickname}"
        parts = [line]

        # 阶段指令
        instruction = self.STAGE_INSTRUCTIONS.get(stage, "")
        if instruction:
            parts.append(instruction)

        # 记忆（精简，最多3条，每条截断80字）
        if memories:
            mem = [m[:80] for m in memories[:3] if m]
            if mem:
                parts.append("记忆:" + " | ".join(mem))

        return "\n".join(parts)

    def detect_affinity_trigger(self, message: str) -> list[str]:
        """
        检测消息中的好感度触发器
        返回触发的原因列表
        """
        triggers = []
        msg = message.lower()

        # 称呼检测
        if "奶奶" in message:
            # 检查是否有亲昵语气
            warm_indicators = ["嘿嘿", "嘻嘻", "哈哈", "～", "~", "！", "呀", "啦", "嘛", "呢"]
            if any(w in message for w in warm_indicators):
                triggers.append("call_grandma")
            else:
                triggers.append("call_grandma")

        # 话题检测
        novel_keywords = ["小说", "新书", "更新", "停更", "八重堂", "蜃楼战记", "奥西兹", "轻小说", "看书", "追更"]
        if any(k in msg for k in novel_keywords):
            triggers.append("topic_novel")

        wine_keywords = ["酒", "喝", "干杯", "醉", "璃月酒", "蒲公英酒", "举杯"]
        if any(k in msg for k in wine_keywords):
            triggers.append("topic_wine")

        divination_keywords = ["占卜", "预言", "命运", "星象", "看看", "帮我算"]
        if any(k in msg for k in divination_keywords):
            triggers.append("topic_divination")

        # 关心检测
        care_keywords = ["你还好吗", "累不累", "别太累了", "注意身体", "早点睡", "吃饭了吗", "你在干嘛", "想你了"]
        if any(k in msg for k in care_keywords):
            triggers.append("care_about_her")

        # 看穿检测
        see_through_keywords = ["你在担心", "你其实", "你明明", "你是不是", "你在害羞", "你脸红了", "你在想"]
        if any(k in msg for k in see_through_keywords):
            triggers.append("see_through_her")

        # 默认每日对话
        if not triggers:
            triggers.append("daily_chat")

        return triggers

    def _next_threshold(self, current_stage: AffinityStage) -> int:
        """获取下一阶段的阈值"""
        stages = sorted(AffinityStage)
        idx = stages.index(current_stage)
        if idx < len(stages) - 1:
            from .affinity_manager import STAGE_THRESHOLDS
            return STAGE_THRESHOLDS[stages[idx + 1]]
        return 9999

    def build_upgrade_message(self, old_stage: AffinityStage, new_stage: AffinityStage) -> str:
        """构建升级提示消息"""
        messages = {
            AffinityStage.ACQUAINTANCE: "哼……算你还有点眼力。以后来找奶奶我，至少不会被拒之门外了。",
            AffinityStage.FRIEND: "（放下书，认真看了你一眼）……你这个人嘛，还行。偶尔来坐坐也无妨。",
            AffinityStage.CLOSE_FRIEND: "（别过脸，耳朵微红）……别、别误会。奶奶我只是觉得你还算懂事，所以才……你懂的。",
            AffinityStage.CONFIDANT: "（沉默了很久，声音很轻）……你知道吗，上一次有人让我这么在意，已经是很久很久以前的事了。",
            AffinityStage.TRAVELER: "（放下书，看着你，眼神比平时温柔很多）……你来了啊。（嘴角不自觉翘了一下）奶奶我今天刚好……没什么。你来了就好。",
        }
        return messages.get(new_stage, "……哼。")
