"""
茜特菈莉·好感度系统 AstrBot 插件
自包含记忆系统（Embedding + 向量检索 + 图谱），不依赖外部插件。
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger

from .core.affinity_manager import AffinityManager, AffinityStage, STAGE_NAMES
from .core.memory_engine import BuiltInMemoryEngine
from .core.context_builder import ContextBuilder
from .core.time_schedule import (
    get_current_window, get_window_name, get_time_context,
    get_special_date_context, get_special_response,
    get_weather_response, CITLALI_SCHEDULE,
)

logger = logging.getLogger("citlali_affinity")


@register(
    "citlali_affinity",
    "CitlaliDev",
    "茜特菈莉好感度系统 - 自包含记忆引擎(Embedding+向量检索+图谱)与关系追踪",
    "3.0.0",
    "https://github.com/citlali-dev/astrbot_plugin_citlali_affinity",
)
class CitlaliAffinityPlugin(Star):
    """茜特菈莉好感度系统插件"""

    def __init__(self, context: Context, config: dict[str, Any]):
        super().__init__(context)
        self.ctx = context
        self.config = config

        # 数据目录
        self.data_dir = str(StarTools.get_data_dir("citlali_affinity"))

        # 核心组件
        self.affinity_mgr = AffinityManager(self.data_dir)
        self.memory_engine = BuiltInMemoryEngine(self.data_dir)
        self.context_builder = ContextBuilder(self.affinity_mgr)

        # 配置
        self.affinity_enabled = config.get("affinity_enabled", True)
        self.memory_enabled = config.get("memory_enabled", True)
        self.inject_context = config.get("inject_context", True)
        self.decay_enabled = config.get("decay_enabled", True)
        self.upgrade_notify = config.get("upgrade_notify", True)
        self.embedding_provider_id = config.get("embedding_provider_id", "")
        self.llm_provider_id = config.get("llm_provider_id", "")
        self.auto_summarize_rounds = config.get("auto_summarize_rounds", 10)
        self.conversation_buffer: dict[str, list] = {}  # session_id -> messages

        self._last_decay = 0
        self._initialized = False

        # 注册 WebUI
        self._register_pages()

        # 延迟初始化记忆引擎
        asyncio.create_task(self._init_memory())

        logger.info("茜特菈莉好感度系统 v3.0 已加载")

    async def _init_memory(self):
        """延迟初始化记忆引擎"""
        try:
            # 等待 AstrBot 的 Provider 就绪
            await asyncio.sleep(2)

            # 尝试获取 Embedding Provider
            embedding_pid = self.embedding_provider_id
            llm_pid = self.llm_provider_id

            # 如果没配置，尝试自动发现
            if not embedding_pid:
                embedding_pid = self._find_provider("embedding")
            if not llm_pid:
                llm_pid = self._find_provider("chat")

            await self.memory_engine.initialize(
                context=self.ctx,
                embedding_provider_id=embedding_pid,
                llm_provider_id=llm_pid,
            )
            self._initialized = True
            logger.info(f"记忆引擎初始化完成 (embedding={embedding_pid}, llm={llm_pid})")
        except Exception as e:
            logger.warning(f"记忆引擎初始化失败: {e}")

    def _find_provider(self, provider_type: str) -> str:
        """自动查找 Provider"""
        try:
            if hasattr(self.ctx, 'get_all_providers'):
                providers = self.ctx.get_all_providers()
                for p in providers:
                    p_type = getattr(p, 'type', '') or ''
                    p_id = getattr(p, 'id', '') or getattr(p, 'provider_id', '') or ''
                    if provider_type in str(p_type).lower() or provider_type in str(p_id).lower():
                        return p_id
        except Exception:
            pass
        return ""

    def _register_pages(self):
        """注册 WebUI Pages"""
        if not hasattr(self.ctx, "register_web_api"):
            return
        try:
            from .pages.pages_api import register_pages
            register_pages(self)
            logger.info("WebUI Pages 已注册")
        except Exception as e:
            logger.warning(f"WebUI 注册失败: {e}")

    # ==================== 中文指令 ====================

    @filter.command("帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助"""
        yield event.plain_result(
            "✦ 茜特菈莉·指令列表 ✦\n"
            "━━━━━━━━━━━━━━\n"
            "  /好感度    查看你和奶奶的关系\n"
            "  /签到      每日签到增加好感\n"
            "  /日程      查看奶奶当前在干嘛\n"
            "  /回忆 <词>  让奶奶回忆某件事\n"
            "  /记住 <话>  让奶奶记住你说的话\n"
            "  /排行      好感度排行榜\n"
            "  /叫我 <名>  让奶奶记住你的名字\n"
            "  /占卜      让奶奶给你占卜\n"
            "  /小说      让奶奶推荐小说\n"
            "  /喝酒      陪奶奶喝一杯\n"
            "  /记忆 <子>  记忆系统管理\n"
            "  /xt状态    系统运行状态\n"
            "━━━━━━━━━━━━━━"
        )

    @filter.command("好感度")
    async def cmd_affinity(self, event: AstrMessageEvent):
        """查看好感度"""
        user_id = event.get_sender_id()
        user = self.affinity_mgr.get_user(user_id)
        stage = AffinityStage(user.get("stage", 0))
        affinity = user.get("affinity", 0)
        stage_name = STAGE_NAMES[stage]
        total = user.get("total_messages", 0)
        nickname = user.get("nickname", "")

        from .core.affinity_manager import STAGE_THRESHOLDS
        stages = sorted(AffinityStage)
        idx = stages.index(stage)
        if idx < len(stages) - 1:
            next_t = STAGE_THRESHOLDS[stages[idx + 1]]
            curr_t = STAGE_THRESHOLDS[stage]
            prog = (affinity - curr_t) / (next_t - curr_t) * 100
            bar = self._bar(prog)
            prog_text = f"{bar} {prog:.0f}%"
        else:
            prog_text = "已满 ❤"

        resp = {
            AffinityStage.STRANGER: "哼，找我什么事？奶奶我很忙的。",
            AffinityStage.ACQUAINTANCE: "哦，是你啊。有什么事？",
            AffinityStage.FRIEND: "来了？坐吧。要不要喝一杯？",
            AffinityStage.CLOSE_FRIEND: "你来了啊。（放下书，嘴角翘了一下）今天想聊什么？",
            AffinityStage.CONFIDANT: "……你来了。（声音变柔）我刚好看到一个有意思的段落。",
            AffinityStage.TRAVELER: "哼，你怎么又来了？（放下书，眼睛在发光）……带酒了吗？",
        }

        name_text = f"  昵称: {nickname}\n" if nickname else ""
        yield event.plain_result(
            f"✦ 茜特菈莉·好感度 ✦\n━━━━━━━━━━━━━━\n"
            f"  关系: {stage_name}\n"
            f"{name_text}"
            f"  好感: {affinity}\n"
            f"  进度: {prog_text}\n  对话: {total} 次\n"
            f"━━━━━━━━━━━━━━\n{resp.get(stage, '')}"
        )

    @filter.command("签到")
    async def cmd_checkin(self, event: AstrMessageEvent):
        """每日签到"""
        user_id = event.get_sender_id()
        user = self.affinity_mgr.get_user(user_id)
        now = time.time()
        last = user.get("last_checkin", 0)

        # 24小时冷却
        if now - last < 86400:
            remaining = int((86400 - (now - last)) / 3600)
            yield event.plain_result(
                f"（瞥了你一眼）你今天已经来过了。{remaining}小时后再来。"
            )
            return

        # 签到奖励
        import random
        bonus = random.randint(15, 30)
        user["last_checkin"] = now
        delta, upgraded = self.affinity_mgr.add_affinity(user_id, "daily_chat", amount=bonus)

        stage = self.affinity_mgr.get_stage(user_id)
        checkin_responses = {
            AffinityStage.STRANGER: "哼……算你勤快。",
            AffinityStage.ACQUAINTANCE: "哦，又来了？坐吧。",
            AffinityStage.FRIEND: "来了？今天奶奶我心情不错，多给你加点分。",
            AffinityStage.CLOSE_FRIEND: "你来了啊。（嘴角翘了一下）我刚好泡了茶。",
            AffinityStage.CONFIDANT: "……你来了。（放下书）我等你半天了。",
            AffinityStage.TRAVELER: "哼，你怎么才来？（把酒杯推过来）坐，陪我喝。",
        }

        upgrade_text = "\n🎉 关系提升了！" if upgraded else ""
        yield event.plain_result(
            f"{checkin_responses.get(stage, '……嗯。')}\n"
            f"好感度 +{delta}（当前: {self.affinity_mgr.get_user(user_id)['affinity']}）{upgrade_text}"
        )

    @filter.command("日程")
    async def cmd_schedule(self, event: AstrMessageEvent):
        """查看当前日程状态"""
        window = get_current_window()
        window_name = get_window_name(window)
        schedule = CITLALI_SCHEDULE.get(window, {})
        now = datetime.now()

        lines = [
            f"✦ 茜特菈莉·日程 ✦",
            f"━━━━━━━━━━━━━━",
            f"  时间: {now.strftime('%H:%M')}",
            f"  时段: {window_name}",
            f"  活动: {schedule.get('activity', '-')}",
            f"  心情: {schedule.get('mood', '-')}",
            f"  位置: {schedule.get('location', '-')}",
            f"  语态: {schedule.get('voice_style', '-')}",
            f"━━━━━━━━━━━━━━",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("回忆")
    async def cmd_recall(self, event: AstrMessageEvent):
        """主动回忆"""
        args = event.message_str.strip().split(maxsplit=1)
        query = args[1] if len(args) > 1 else ""
        if not query:
            yield event.plain_result("你想让我回忆什么？说个关键词。")
            return

        session_id = event.unified_msg_origin
        results = await self.memory_engine.recall(query, session_id=session_id, k=5)

        if results:
            lines = ["（闭眼，刻纹微光）……嗯，我想起来了。\n"]
            for i, r in enumerate(results, 1):
                content = r.get("content", "") or r.get("summary", "")
                if content:
                    lines.append(f"  {i}. {content[:150]}")
            yield event.plain_result("\n".join(lines))
        else:
            yield event.plain_result("（闭眼了一会儿）……什么都想不起来。你确定发生过？")

    @filter.command("记住")
    async def cmd_memorize(self, event: AstrMessageEvent):
        """主动记忆"""
        args = event.message_str.strip().split(maxsplit=1)
        content = args[1] if len(args) > 1 else ""
        if not content:
            yield event.plain_result("你想让我记住什么？")
            return

        user_id = event.get_sender_id()
        if self.affinity_enabled:
            self.affinity_mgr.add_affinity(user_id, "care_about_her")

        ok = await self.memory_engine.memorize(
            content=f"旅行者说: {content}",
            session_id=event.unified_msg_origin,
            importance=0.8,
        )
        yield event.plain_result(
            "（认真地点了点头）……好吧，奶奶我记住了。" if ok
            else "……好吧，我尽量记着。（记忆引擎未就绪）"
        )

    @filter.command("排行")
    async def cmd_leaderboard(self, event: AstrMessageEvent):
        """好感度排行"""
        board = self.affinity_mgr.get_leaderboard(10)
        if not board:
            yield event.plain_result("还没有人跟奶奶我打过交道呢。")
            return
        lines = ["✦ 好感度排行 ✦\n━━━━━━━━━━━━━━"]
        for i, item in enumerate(board, 1):
            name = item["nickname"] or item["user_id"][:8]
            lines.append(f"  {i}. {name} - {item['stage']} ({item['affinity']})")
        lines.append("━━━━━━━━━━━━━━")
        yield event.plain_result("\n".join(lines))

    @filter.command("叫我")
    async def cmd_setname(self, event: AstrMessageEvent):
        """设置昵称"""
        args = event.message_str.strip().split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result("用法: /叫我 <你的名字>")
            return
        self.affinity_mgr.set_nickname(event.get_sender_id(), args[1].strip())
        yield event.plain_result(f"……{args[1].strip()}？嗯，奶奶我记住了。")

    @filter.command("占卜")
    async def cmd_divination(self, event: AstrMessageEvent):
        """占卜"""
        import random
        user_id = event.get_sender_id()
        if self.affinity_enabled:
            self.affinity_mgr.add_affinity(user_id, "topic_divination")

        fortunes = [
            "（闭眼，刻纹微光）……我看到了。诸恶曜必不会伤害你，诸吉星必环绕你。近日会有好事发生。",
            "（凝视星象）……星象显示，你最近身边有小人。不过别担心，奶奶我会帮你盯着。",
            "（触碰额间刻纹）……你的命运线很亮。但要注意，不要在深夜做重大决定。",
            "（闭眼片刻）……嗯，你最近丢了什么东西吧？别急，三天后会自己冒出来的。",
            "（认真地看着星图）……你面前有三条路。走中间那条——虽然最慢，但最稳。",
            "（刻纹微亮）……你的「盟友」很强。放心往前走，有奶奶我在后面看着。",
            "（叹气）……今天的星象不太好。你出门右转注意脚下，别摔着。",
        ]
        yield event.plain_result(random.choice(fortunes))

    @filter.command("小说")
    async def cmd_novel(self, event: AstrMessageEvent):
        """小说推荐"""
        import random
        user_id = event.get_sender_id()
        if self.affinity_enabled:
            self.affinity_mgr.add_affinity(user_id, "topic_novel")

        recommendations = [
            "（眼睛亮起来）哦，你感兴趣啊！《蜃楼战记》是稻妻八重堂的活化石级老系列，讲的是苇之原岛国上猫头道士「东之山君」的故事。篇幅超长，换过三次作者，最终也没揭开真相……你一定要看！",
            "（从书堆里翻出一本）这本《奥西兹小姐事件簿》！枫丹风格的推理小说，神里绫华也是忠实读者。我有番外篇，要看吗？",
            "（突然兴奋）《再这样下去要成为败犬女主了！》——八重堂今年最热销的！我读到高潮时直接喊出来了。你知道什么书最好看吗？就是那种你明知道女主最后会赢，但中间被虐得死去活来的！",
            "（宝贝地拿出两本）《转生成为雷电将军，然后天下无敌》和《拜托了我的狐仙宫司》——这两本是绝版珍藏，我两本都要，一本都不能少。",
            "（皱眉）《沉秋拾剑录》……书是好书。但签售会不来纳塔——这事儿我记一辈子。",
        ]
        yield event.plain_result(random.choice(recommendations))

    @filter.command("喝酒")
    async def cmd_drink(self, event: AstrMessageEvent):
        """陪奶奶喝酒"""
        import random
        user_id = event.get_sender_id()
        window = get_current_window()

        if self.affinity_enabled:
            self.affinity_mgr.add_affinity(user_id, "topic_wine")

        # 不同时段不同反应
        if window == "morning":
            yield event.plain_result(
                "（瞪大眼睛）大早上就喝酒？！……虽然奶奶我昨晚的酒还没醒，但也不至于这么早吧。"
            )
        elif window == "noon":
            yield event.plain_result(
                "中午好。其实我还是很节制的，起床到中午不会喝酒……至于熬夜就是另一码事了。你要来点吗？"
            )
        else:
            drinks = [
                "（举起酒杯）来，陪奶奶我喝一杯。璃月的酒，不错。岁月献给小酒杯——嗝。",
                "（微醺）你知道吗……这瓶酒我存了好久了。今天心情不错，开了吧。",
                "（晃了晃酒瓶）还剩半瓶。你要来点吗？……别告诉别人奶奶我喝这么多。",
                "（脸颊微红）嗝……你别看我这样，奶奶我酒量很好的。……大概。",
                "（把酒杯推过来）坐。今晚的月色不错，适合喝酒。",
            ]
            yield event.plain_result(random.choice(drinks))

    @filter.command("记忆")
    async def cmd_memory(self, event: AstrMessageEvent):
        """记忆系统管理"""
        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result(
                "✦ 记忆系统 ✦\n"
                "━━━━━━━━━━━━━━\n"
                "  /记忆 状态    系统状态\n"
                "  /记忆 图谱    图谱概览\n"
                "  /记忆 搜索 <词> 搜索记忆\n"
                "  /记忆 最近    最近记忆节点\n"
                "━━━━━━━━━━━━━━"
            )
            return
        sub = args[1]
        if sub == "状态":
            s = await self.memory_engine.get_stats()
            yield event.plain_result(
                f"记忆引擎: {'✓' if self._initialized else '✗'}\n"
                f"记忆总数: {s.get('total_memories', '-')}\n"
                f"图谱节点: {s.get('graph_nodes', '-')}\n"
                f"图谱边数: {s.get('graph_edges', '-')}"
            )
        elif sub == "图谱":
            g = await self.memory_engine.get_graph_overview()
            if g and g.get("nodes"):
                lines = ["图谱节点:"]
                for n in g["nodes"][:10]:
                    lines.append(f"  [{n.get('type','')}] {n.get('label','')}")
                lines.append("\n图谱关系:")
                for e in g.get("edges", [])[:10]:
                    lines.append(f"  {e.get('source','')} → {e.get('target','')} ({e.get('relation','')})")
                yield event.plain_result("\n".join(lines))
            else:
                yield event.plain_result("图谱数据为空。")
        elif sub == "搜索" and len(args) > 2:
            q = " ".join(args[2:])
            r = await self.memory_engine.recall(q, session_id=event.unified_msg_origin, k=10)
            if r:
                lines = [f"找到 {len(r)} 条记忆:"]
                for i, m in enumerate(r, 1):
                    c = m.get("content", "") or m.get("summary", "")
                    lines.append(f"  [{i}] {c[:100]}")
                yield event.plain_result("\n".join(lines))
            else:
                yield event.plain_result("没找到。")
        elif sub == "最近":
            r = await self.memory_engine.recent_memories(10)
            if r:
                lines = ["最近记忆:"]
                for i, m in enumerate(r, 1):
                    c = m.get("content", "") or m.get("summary", "")
                    t = m.get("create_time", 0)
                    ts = time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "-"
                    lines.append(f"  [{i}] [{ts}] {c[:80]}")
                yield event.plain_result("\n".join(lines))
            else:
                yield event.plain_result("暂无记忆。")

    @filter.command("xt状态")
    async def cmd_status(self, event: AstrMessageEvent):
        """系统状态"""
        stats = self.affinity_mgr.get_stats()
        mem_stats = await self.memory_engine.get_stats() if self._initialized else {}
        window = get_current_window()
        window_name = get_window_name(window)
        schedule = CITLALI_SCHEDULE.get(window, {})
        lines = [
            "✦ 茜特菈莉·系统状态 ✦", "━━━━━━━━━━━━━━",
            f"  好感度系统: {'✓' if self.affinity_enabled else '✗'}",
            f"  记忆引擎:   {'✓' if self._initialized else '✗'}",
            f"  上下文注入: {'✓' if self.inject_context else '✗'}",
            f"  当前时段:   {window_name} ({schedule.get('activity', '-')})",
            f"  总用户:     {stats['total_users']}",
            f"  总对话:     {stats['total_messages']}",
            f"  记忆总数:   {mem_stats.get('total_memories', '-')}",
            f"  图谱节点:   {mem_stats.get('graph_nodes', '-')}",
            f"  图谱边数:   {mem_stats.get('graph_edges', '-')}",
            "━━━━━━━━━━━━━━", "  关系分布:",
        ]
        for sn, cnt in stats["stage_counts"].items():
            if cnt > 0:
                lines.append(f"    {sn}: {cnt}")
        yield event.plain_result("\n".join(lines))

    # ==================== LLM 钩子 ====================

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, request):
        if not self.inject_context:
            return

        user_id = event.get_sender_id()
        message = event.message_str
        session_id = event.unified_msg_origin

        # 每日衰减
        if self.decay_enabled:
            now = time.time()
            if now - self._last_decay > 3600:
                self.affinity_mgr.decay_daily()
                self._last_decay = now

        # 好感度触发
        if self.affinity_enabled:
            triggers = self.context_builder.detect_affinity_trigger(message)
            upgraded = False
            new_stage = None
            old_stage = self.affinity_mgr.get_stage(user_id)

            for trigger in triggers:
                _, up = self.affinity_mgr.add_affinity(user_id, trigger)
                if up:
                    upgraded = True
                    new_stage = self.affinity_mgr.get_stage(user_id)

            if upgraded and self.upgrade_notify and new_stage:
                msg = self.context_builder.build_upgrade_message(old_stage, new_stage)
                request.contexts.append({
                    "role": "system",
                    "content": f"[关系升级:{STAGE_NAMES[new_stage]}] {msg}"
                })

        # 对话缓冲（用于自动总结）
        if self.memory_enabled and self._initialized:
            if session_id not in self.conversation_buffer:
                self.conversation_buffer[session_id] = []
            self.conversation_buffer[session_id].append({"role": "user", "content": message})

            # 达到轮次阈值时自动总结
            if len(self.conversation_buffer[session_id]) >= self.auto_summarize_rounds * 2:
                msgs = self.conversation_buffer[session_id]
                self.conversation_buffer[session_id] = []
                asyncio.create_task(self._auto_summarize(session_id, msgs))

            # 记忆召回
            raw = await self.memory_engine.recall(message, session_id=session_id, k=3)
            memories = [r.get("content", "") for r in raw if r.get("content")]

            # 构建上下文
            ctx = self.context_builder.build_context(user_id, memories)

            # 添加时段上下文
            time_ctx = get_time_context()
            if time_ctx:
                ctx = time_ctx + "\n\n" + ctx

            # 检查特殊日期
            special = get_special_date_context()
            if special:
                ctx = special + "\n" + ctx

            request.contexts.append({"role": "system", "content": ctx})

    async def _auto_summarize(self, session_id: str, messages: list):
        """自动总结对话并写入记忆"""
        try:
            summary = await self.memory_engine.summarize_conversation(messages)
            if summary:
                await self.memory_engine.memorize(
                    content=summary,
                    session_id=session_id,
                    importance=0.6,
                )
                logger.info(f"自动总结记忆: {summary[:50]}...")
        except Exception as e:
            logger.warning(f"自动总结失败: {e}")

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """LLM 回复后，将回复加入对话缓冲"""
        if not self.memory_enabled:
            return
        session_id = event.unified_msg_origin
        if session_id in self.conversation_buffer:
            content = resp.completion_text if hasattr(resp, 'completion_text') else str(resp)
            self.conversation_buffer[session_id].append({"role": "assistant", "content": content})

    def _bar(self, pct: float, len: int = 10) -> str:
        f = int(pct / 100 * len)
        return "█" * f + "░" * (len - f)
