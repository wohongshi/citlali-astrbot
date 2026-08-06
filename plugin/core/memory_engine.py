"""
BuiltInMemoryEngine — 自包含内置记忆系统
使用 SQLite 存储 + numpy 向量检索 + BM25 文本检索，通过 AstrBot Embedding Provider 向量化。
"""
import asyncio
import json
import logging
import math
import os
import struct
import time
import uuid
from collections import Counter
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("citlali.memory_engine")

# ---------------------------------------------------------------------------
# BM25 参数
# ---------------------------------------------------------------------------
BM25_K1 = 1.5
BM25_B = 0.75


def _tokenize_chinese(text: str) -> list[str]:
    """中文字符 bigram 分词 + 英文单词切分，合并为 token 列表。"""
    tokens: list[str] = []
    buf: list[str] = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf":
            # CJK 字符
            if buf:
                tokens.append("".join(buf))
                buf = []
            tokens.append(ch)
        elif ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                tokens.append("".join(buf))
                buf = []
    if buf:
        tokens.append("".join(buf))

    # 生成 bigram（对单个 CJK 字符也做 bigram）
    bigrams: list[str] = []
    for i in range(len(tokens)):
        bigrams.append(tokens[i])
        if i + 1 < len(tokens):
            bigrams.append(tokens[i] + tokens[i + 1])
    return bigrams


def _embed_to_bytes(vec: list[float]) -> bytes:
    """float32 list → BLOB"""
    return struct.pack(f"{len(vec)}f", *vec)


def _bytes_to_embed(data: bytes) -> np.ndarray:
    """BLOB → numpy float32 array"""
    n = len(data) // 4
    return np.frombuffer(data, dtype=np.float32, count=n)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度"""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# SQLite 建表 SQL
# ---------------------------------------------------------------------------
_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    summary     TEXT DEFAULT '',
    embedding   BLOB,
    importance  REAL DEFAULT 0.5,
    session_id  TEXT DEFAULT '',
    create_time REAL NOT NULL,
    last_access REAL NOT NULL,
    access_count INTEGER DEFAULT 0,
    status      INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS atoms (
    id          TEXT PRIMARY KEY,
    memory_id   TEXT NOT NULL,
    content     TEXT NOT NULL,
    type        TEXT DEFAULT 'fact',
    ttl         REAL DEFAULT -1,
    importance  REAL DEFAULT 0.5,
    create_time REAL NOT NULL,
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);

CREATE TABLE IF NOT EXISTS graph_nodes (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    type        TEXT DEFAULT 'entity',
    properties  TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    relation    TEXT NOT NULL,
    properties  TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mem_session ON memories(session_id);
CREATE INDEX IF NOT EXISTS idx_mem_status  ON memories(status);
CREATE INDEX IF NOT EXISTS idx_mem_time    ON memories(create_time);
CREATE INDEX IF NOT EXISTS idx_atoms_mid   ON atoms(memory_id);
CREATE INDEX IF NOT EXISTS idx_edges_src   ON graph_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_tgt   ON graph_edges(target_id);
"""


class BuiltInMemoryEngine:
    """完全自包含的内置记忆系统，不依赖任何外部插件。"""

    def __init__(self, data_dir: str = "", context=None):
        self.ctx = context
        self._data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "memory"
        )
        self._db_path = os.path.join(self._data_dir, "memory.db")
        self._db: Any = None  # sqlite3.Connection (同步，用 run_in_executor 包装)

        self._embedding_provider = None
        self._llm_provider = None
        self._embedding_dim = 0

        # 内存向量缓存 (id → np.ndarray)，用于快速检索
        self._vector_cache: dict[str, np.ndarray] = {}
        self._cache_dirty = True

        # BM25 文档频率缓存
        self._bm25_docs: list[tuple[str, list[str]]] = []  # (id, tokens)
        self._bm25_df: Counter = Counter()
        self._bm25_avgdl: float = 0.0
        self._bm25_dirty = True

        self._initialized = False

    # -----------------------------------------------------------------------
    # 初始化
    # -----------------------------------------------------------------------
    async def initialize(
        self,
        embedding_provider_id: str = "",
        llm_provider_id: str = "",
        context=None,
    ) -> bool:
        """
        初始化记忆引擎：创建数据库、加载 embedding provider。

        Args:
            embedding_provider_id: AstrBot embedding provider ID，为空则自动查找
            llm_provider_id: AstrBot LLM provider ID（预留）
            context: AstrBot Context（可选，若 __init__ 未传入则在此补充）

        Returns:
            是否初始化成功
        """
        if context is not None:
            self.ctx = context
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            await self._init_db()
            await self._load_embedding_provider(embedding_provider_id)
            await self._load_llm_provider(llm_provider_id)
            self._initialized = True
            logger.info(
                f"BuiltInMemoryEngine 已初始化 | db={self._db_path} | "
                f"embedding={'✓' if self._embedding_provider else '✗'} | dim={self._embedding_dim}"
            )
            return True
        except Exception as e:
            logger.error(f"BuiltInMemoryEngine 初始化失败: {e}", exc_info=True)
            return False

    async def _init_db(self):
        """创建 / 打开 SQLite 数据库。"""
        import sqlite3

        def _create():
            conn = sqlite3.connect(self._db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_CREATE_TABLES)
            conn.commit()
            return conn

        loop = asyncio.get_event_loop()
        self._db = await loop.run_in_executor(None, _create)

    async def _load_embedding_provider(self, provider_id: str = ""):
        """通过 AstrBot Context 获取 Embedding Provider。"""
        try:
            provider = None
            if provider_id:
                # 方式 1：通过 ID 直接获取
                if hasattr(self.ctx, "get_provider_by_id"):
                    provider = self.ctx.get_provider_by_id(provider_id)

            if not provider:
                # 方式 2：遍历所有 provider，找支持 embedding 的
                providers = []
                if hasattr(self.ctx, "get_all_providers"):
                    providers = self.ctx.get_all_providers()
                elif hasattr(self.ctx, "providers"):
                    providers = self.ctx.providers or []
                    if isinstance(providers, dict):
                        providers = list(providers.values())

                for p in providers:
                    # AstrBot Embedding Provider 通常有 embed / get_embedding 方法
                    if hasattr(p, "get_embedding") or hasattr(p, "embed"):
                        provider = p
                        break
                    # 有些 provider 通过 meta 标识类型
                    meta = getattr(p, "meta", {}) or {}
                    if meta.get("type", "").lower() in ("embedding", "embed"):
                        provider = p
                        break

            if provider:
                self._embedding_provider = provider
                # 探测维度
                try:
                    test_vec = await self._do_embed("test")
                    self._embedding_dim = len(test_vec)
                except Exception:
                    self._embedding_dim = 0
                logger.info(f"Embedding provider 已绑定: {getattr(provider, 'id', '?')}")
            else:
                logger.warning("未找到可用的 Embedding Provider，向量检索将不可用")

        except Exception as e:
            logger.warning(f"加载 Embedding Provider 失败: {e}")

    async def _load_llm_provider(self, provider_id: str = ""):
        """通过 AstrBot Context 获取 LLM Provider（用于自动总结等）。"""
        if not self.ctx:
            return
        try:
            provider = None
            if provider_id:
                if hasattr(self.ctx, "get_provider_by_id"):
                    provider = self.ctx.get_provider_by_id(provider_id)

            if not provider:
                providers = []
                if hasattr(self.ctx, "get_all_providers"):
                    providers = self.ctx.get_all_providers()
                elif hasattr(self.ctx, "providers"):
                    providers = self.ctx.providers or []
                    if isinstance(providers, dict):
                        providers = list(providers.values())

                for p in providers:
                    if hasattr(p, "text_chat") or hasattr(p, "chat"):
                        provider = p
                        break
                    meta = getattr(p, "meta", {}) or {}
                    if meta.get("type", "").lower() in ("chat", "llm"):
                        provider = p
                        break

            if provider:
                self._llm_provider = provider
                logger.info(f"LLM provider 已绑定: {getattr(provider, 'id', '?')}")
        except Exception as e:
            logger.warning(f"加载 LLM Provider 失败: {e}")

    async def _do_embed(self, text: str) -> list[float]:
        """调用 embedding provider 获取向量。"""
        if not self._embedding_provider:
            raise RuntimeError("Embedding provider 未初始化")

        provider = self._embedding_provider
        if hasattr(provider, "get_embedding"):
            result = await provider.get_embedding(text)
        elif hasattr(provider, "embed"):
            result = await provider.embed(text)
        else:
            raise RuntimeError("Embedding provider 无可用的 embed 方法")

        # 兼容不同返回格式
        if isinstance(result, list):
            return [float(x) for x in result]
        if isinstance(result, dict):
            # 可能是 {"embedding": [...], ...}
            vec = result.get("embedding") or result.get("data") or result.get("vector")
            if vec:
                return [float(x) for x in vec]
        # 如果返回对象有 .embedding 属性
        if hasattr(result, "embedding"):
            return [float(x) for x in result.embedding]
        raise RuntimeError(f"无法解析 embedding 返回值: {type(result)}")

    def is_available(self) -> bool:
        """记忆系统是否可用。"""
        return self._initialized and self._db is not None

    # -----------------------------------------------------------------------
    # 记忆写入
    # -----------------------------------------------------------------------
    async def memorize(
        self,
        content: str,
        session_id: str = "",
        importance: float = 0.7,
        summary: str = "",
        metadata: dict | None = None,
    ) -> bool:
        """
        写入一条记忆。

        Args:
            content: 记忆内容
            session_id: 会话 ID
            importance: 重要性 (0-1)
            summary: 摘要（可选）
            metadata: 额外元数据（可选）

        Returns:
            是否成功
        """
        if not self.is_available():
            return False

        memory_id = uuid.uuid4().hex[:16]
        now = time.time()

        # 尝试生成 embedding
        embedding_blob: bytes | None = None
        if self._embedding_provider:
            try:
                vec = await self._do_embed(content)
                embedding_blob = _embed_to_bytes(vec)
                self._vector_cache[memory_id] = np.array(vec, dtype=np.float32)
            except Exception as e:
                logger.warning(f"生成 embedding 失败，仅做文本存储: {e}")

        def _insert():
            self._db.execute(
                """INSERT INTO memories
                   (id, content, summary, embedding, importance, session_id,
                    create_time, last_access, access_count, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1)""",
                (memory_id, content, summary, embedding_blob,
                 importance, session_id, now, now),
            )
            # 提取 atoms（简单实现：句子级别）
            atoms = self._extract_atoms(content)
            for atom_content, atom_type in atoms:
                atom_id = uuid.uuid4().hex[:12]
                self._db.execute(
                    """INSERT INTO atoms
                       (id, memory_id, content, type, ttl, importance, create_time)
                       VALUES (?, ?, ?, ?, -1, ?, ?)""",
                    (atom_id, memory_id, atom_content, atom_type, importance * 0.8, now),
                )
            self._db.commit()

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _insert)
            self._bm25_dirty = True
            logger.info(f"记忆已写入 #{memory_id}: {content[:60]}...")
            return True
        except Exception as e:
            logger.error(f"记忆写入失败: {e}", exc_info=True)
            return False

    def _extract_atoms(self, content: str) -> list[tuple[str, str]]:
        """从内容中提取 atoms（知识碎片）。"""
        atoms: list[tuple[str, str]] = []
        # 按句号、感叹号、问号、分号拆分
        delimiters = "。！？；\n"
        buf = ""
        for ch in content:
            buf += ch
            if ch in delimiters:
                s = buf.strip()
                if len(s) >= 4:
                    atoms.append((s, "fact"))
                buf = ""
        if buf.strip() and len(buf.strip()) >= 4:
            atoms.append((buf.strip(), "fact"))
        return atoms

    # -----------------------------------------------------------------------
    # 记忆检索 — BM25 + 向量混合
    # -----------------------------------------------------------------------
    async def recall(
        self,
        query: str,
        session_id: str = "",
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        混合检索记忆（BM25 + 向量余弦相似度）。

        Args:
            query: 查询文本
            session_id: 会话 ID（用于作用域过滤）
            k: 返回数量

        Returns:
            记忆列表，按综合得分排序
        """
        if not self.is_available():
            return []

        # 1. BM25 得分
        bm25_scores = await self._bm25_search(query, session_id, k=k * 3)

        # 2. 向量得分
        vec_scores: dict[str, float] = {}
        if self._embedding_provider and self._vector_cache:
            try:
                q_vec = await self._do_embed(query)
                q_arr = np.array(q_vec, dtype=np.float32)
                vec_scores = self._vector_search(q_arr, session_id, k=k * 3)
            except Exception as e:
                logger.warning(f"向量检索失败: {e}")

        # 3. 归一化 + 合并
        all_ids = set(bm25_scores.keys()) | set(vec_scores.keys())
        if not all_ids:
            return []

        bm25_max = max(bm25_scores.values()) if bm25_scores else 1.0
        vec_max = max(vec_scores.values()) if vec_scores else 1.0
        if bm25_max == 0:
            bm25_max = 1.0
        if vec_max == 0:
            vec_max = 1.0

        combined: dict[str, float] = {}
        for mid in all_ids:
            b = bm25_scores.get(mid, 0.0) / bm25_max
            v = vec_scores.get(mid, 0.0) / vec_max
            # 有向量时 40% BM25 + 60% 向量；无向量时纯 BM25
            if vec_scores:
                combined[mid] = 0.4 * b + 0.6 * v
            else:
                combined[mid] = b

        # 取 top-k
        top_ids = sorted(combined, key=lambda x: combined[x], reverse=True)[:k]

        # 4. 从数据库读取完整记录
        results = await self._fetch_memories_by_ids(top_ids)
        # 附加分数、更新访问
        now = time.time()
        for r in results:
            r["score"] = combined.get(r["id"], 0.0)

        # 异步更新 last_access（不阻塞返回）
        if top_ids:
            asyncio.create_task(self._touch_memories(top_ids))

        return results

    async def _bm25_search(
        self, query: str, session_id: str, k: int
    ) -> dict[str, float]:
        """BM25 文本检索，返回 {memory_id: score}。"""
        await self._ensure_bm25_cache()

        q_tokens = _tokenize_chinese(query)
        if not q_tokens:
            return {}

        N = len(self._bm25_docs)
        if N == 0:
            return {}

        avgdl = self._bm25_avgdl if self._bm25_avgdl > 0 else 1.0

        # 按 session 过滤
        valid_ids: set[str] | None = None
        if session_id:
            valid_ids = await self._get_session_memory_ids(session_id)

        scores: dict[str, float] = {}
        for doc_id, doc_tokens in self._bm25_docs:
            if valid_ids is not None and doc_id not in valid_ids:
                continue
            dl = len(doc_tokens)
            tf_map = Counter(doc_tokens)
            score = 0.0
            for qt in q_tokens:
                tf = tf_map.get(qt, 0)
                df = self._bm25_df.get(qt, 0)
                if df == 0 or tf == 0:
                    continue
                idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)
                tf_norm = (tf * (BM25_K1 + 1)) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl))
                score += idf * tf_norm
            if score > 0:
                scores[doc_id] = score

        return scores

    async def _ensure_bm25_cache(self):
        """确保 BM25 缓存已构建。"""
        if not self._bm25_dirty:
            return

        def _load():
            cursor = self._db.execute(
                "SELECT id, content FROM memories WHERE status=1"
            )
            rows = cursor.fetchall()
            docs = []
            df: Counter = Counter()
            total_len = 0
            for mid, content in rows:
                tokens = _tokenize_chinese(content)
                docs.append((mid, tokens))
                total_len += len(tokens)
                unique_tokens = set(tokens)
                for t in unique_tokens:
                    df[t] += 1
            return docs, df, total_len / max(len(docs), 1)

        loop = asyncio.get_event_loop()
        self._bm25_docs, self._bm25_df, self._bm25_avgdl = await loop.run_in_executor(None, _load)
        self._bm25_dirty = False

    def _vector_search(
        self, q_vec: np.ndarray, session_id: str, k: int
    ) -> dict[str, float]:
        """向量余弦相似度检索，返回 {memory_id: score}。"""
        if not self._vector_cache:
            self._load_vector_cache()

        candidates = self._vector_cache
        if session_id:
            # 过滤（需要从 DB 查 session，这里用全量再过滤）
            pass

        scores: dict[str, float] = {}
        for mid, vec in candidates.items():
            sim = _cosine_similarity(q_vec, vec)
            if sim > 0:
                scores[mid] = sim

        return scores

    def _load_vector_cache(self):
        """从数据库加载所有 embedding 到内存。"""
        try:
            cursor = self._db.execute(
                "SELECT id, embedding FROM memories WHERE status=1 AND embedding IS NOT NULL"
            )
            for mid, blob in cursor.fetchall():
                if blob:
                    self._vector_cache[mid] = _bytes_to_embed(blob)
        except Exception as e:
            logger.warning(f"加载向量缓存失败: {e}")

    async def _get_session_memory_ids(self, session_id: str) -> set[str]:
        """获取某个 session 下所有 memory id。"""
        def _query():
            cursor = self._db.execute(
                "SELECT id FROM memories WHERE session_id=? AND status=1",
                (session_id,),
            )
            return {row[0] for row in cursor.fetchall()}

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _query)

    async def _fetch_memories_by_ids(self, ids: list[str]) -> list[dict[str, Any]]:
        """从数据库按 ID 列表批量读取记忆。"""
        if not ids:
            return []

        def _query():
            placeholders = ",".join("?" for _ in ids)
            cursor = self._db.execute(
                f"""SELECT id, content, summary, importance, session_id,
                           create_time, last_access, access_count
                    FROM memories WHERE id IN ({placeholders}) AND status=1""",
                ids,
            )
            rows = cursor.fetchall()
            id_map = {r[0]: r for r in rows}
            # 保持排序
            result = []
            for mid in ids:
                row = id_map.get(mid)
                if row:
                    result.append({
                        "id": row[0],
                        "content": row[1],
                        "summary": row[2] or "",
                        "importance": row[3],
                        "session_id": row[4],
                        "create_time": row[5],
                        "last_access": row[6],
                        "access_count": row[7],
                    })
            return result

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _query)

    async def _touch_memories(self, ids: list[str]):
        """更新记忆的访问时间和次数。"""
        def _update():
            now = time.time()
            for mid in ids:
                self._db.execute(
                    """UPDATE memories SET last_access=?, access_count=access_count+1
                       WHERE id=?""",
                    (now, mid),
                )
            self._db.commit()

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _update)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # 最近记忆
    # -----------------------------------------------------------------------
    async def recent_memories(self, limit: int = 10) -> list[dict]:
        """获取最近写入的记忆。"""
        if not self.is_available():
            return []

        def _query():
            cursor = self._db.execute(
                """SELECT id, content, summary, importance, session_id, create_time
                   FROM memories WHERE status=1
                   ORDER BY create_time DESC LIMIT ?""",
                (limit,),
            )
            return [
                {
                    "id": r[0],
                    "content": r[1],
                    "summary": r[2] or "",
                    "importance": r[3],
                    "session_id": r[4],
                    "create_time": r[5],
                }
                for r in cursor.fetchall()
            ]

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _query)

    # -----------------------------------------------------------------------
    # 图谱
    # -----------------------------------------------------------------------
    async def add_graph_node(
        self,
        label: str,
        node_type: str = "entity",
        properties: dict | None = None,
    ) -> str:
        """添加图谱节点，返回节点 ID。"""
        node_id = uuid.uuid4().hex[:12]

        def _insert():
            self._db.execute(
                "INSERT INTO graph_nodes (id, label, type, properties) VALUES (?, ?, ?, ?)",
                (node_id, label, node_type, json.dumps(properties or {}, ensure_ascii=False)),
            )
            self._db.commit()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _insert)
        return node_id

    async def add_graph_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        properties: dict | None = None,
    ) -> str:
        """添加图谱边，返回边 ID。"""
        edge_id = uuid.uuid4().hex[:12]

        def _insert():
            self._db.execute(
                "INSERT INTO graph_edges (id, source_id, target_id, relation, properties) VALUES (?, ?, ?, ?, ?)",
                (edge_id, source_id, target_id, relation, json.dumps(properties or {}, ensure_ascii=False)),
            )
            self._db.commit()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _insert)
        return edge_id

    async def get_graph_overview(self, limit_nodes: int = 100) -> dict:
        """
        获取图谱概览。

        Returns:
            {"nodes": [...], "edges": [...]}
        """
        if not self.is_available():
            return {"nodes": [], "edges": []}

        def _query():
            nodes = []
            cursor = self._db.execute(
                "SELECT id, label, type, properties FROM graph_nodes LIMIT ?",
                (limit_nodes,),
            )
            for row in cursor.fetchall():
                nodes.append({
                    "id": row[0],
                    "label": row[1],
                    "type": row[2],
                    "properties": json.loads(row[3]) if row[3] else {},
                })

            edges = []
            cursor = self._db.execute(
                "SELECT id, source_id, target_id, relation, properties FROM graph_edges LIMIT 200"
            )
            for row in cursor.fetchall():
                edges.append({
                    "id": row[0],
                    "source": row[1],
                    "target": row[2],
                    "relation": row[3],
                    "properties": json.loads(row[4]) if row[4] else {},
                })

            return {"nodes": nodes, "edges": edges}

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _query)

    async def has_graph(self) -> bool:
        """图谱是否有数据。"""
        if not self.is_available():
            return False

        def _check():
            cursor = self._db.execute("SELECT COUNT(*) FROM graph_nodes")
            return cursor.fetchone()[0] > 0

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _check)

    # -----------------------------------------------------------------------
    # 统计
    # -----------------------------------------------------------------------
    async def get_stats(self) -> dict:
        """获取记忆系统统计信息。"""
        if not self.is_available():
            return {}

        def _query():
            mem_count = self._db.execute(
                "SELECT COUNT(*) FROM memories WHERE status=1"
            ).fetchone()[0]

            atom_count = self._db.execute(
                "SELECT COUNT(*) FROM atoms"
            ).fetchone()[0]

            node_count = self._db.execute(
                "SELECT COUNT(*) FROM graph_nodes"
            ).fetchone()[0]

            edge_count = self._db.execute(
                "SELECT COUNT(*) FROM graph_edges"
            ).fetchone()[0]

            return {
                "total_memories": mem_count,
                "atom_count": atom_count,
                "graph_nodes": node_count,
                "graph_edges": edge_count,
            }

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _query)

    # -----------------------------------------------------------------------
    # 辅助 / 清理
    # -----------------------------------------------------------------------
    async def forget(self, memory_id: str) -> bool:
        """软删除一条记忆。"""
        if not self.is_available():
            return False

        def _delete():
            self._db.execute(
                "UPDATE memories SET status=0 WHERE id=?", (memory_id,)
            )
            self._db.commit()

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _delete)
            self._vector_cache.pop(memory_id, None)
            self._bm25_dirty = True
            return True
        except Exception:
            return False

    async def search_atoms(self, keyword: str, limit: int = 10) -> list[dict]:
        """按关键词搜索 atoms。"""
        if not self.is_available():
            return []

        def _query():
            cursor = self._db.execute(
                """SELECT a.id, a.memory_id, a.content, a.type, a.importance, a.create_time
                   FROM atoms a
                   JOIN memories m ON a.memory_id = m.id
                   WHERE m.status=1 AND a.content LIKE ?
                   ORDER BY a.importance DESC LIMIT ?""",
                (f"%{keyword}%", limit),
            )
            return [
                {
                    "id": r[0],
                    "memory_id": r[1],
                    "content": r[2],
                    "type": r[3],
                    "importance": r[4],
                    "create_time": r[5],
                }
                for r in cursor.fetchall()
            ]

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _query)

    async def summarize_conversation(self, messages: list[dict]) -> str:
        """
        通过 LLM 总结对话内容。需要 LLM provider。

        Args:
            messages: 对话列表 [{"role": "user"/"assistant", "content": "..."}]

        Returns:
            总结文本，失败返回空字符串
        """
        if not self._llm_provider:
            parts = []
            for m in messages[-6:]:
                role = m.get("role", "user")
                content = m.get("content", "")
                if content:
                    label = "用户" if role == "user" else "助手"
                    parts.append(f"{label}: {content[:200]}")
            return ("对话摘要: " + " | ".join(parts)) if parts else ""

        try:
            conversation_text = "\n".join(
                f"{'用户' if m.get('role') == 'user' else '助手'}: {m.get('content', '')}"
                for m in messages
                if m.get("content")
            )
            prompt = (
                "请用一两句话总结以下对话的关键信息，用于长期记忆存储。"
                "只输出总结，不要多余内容。\n\n"
                f"{conversation_text[-2000:]}"
            )

            provider = self._llm_provider
            if hasattr(provider, "text_chat"):
                resp = await provider.text_chat(prompt=prompt)
                if hasattr(resp, "completion_text"):
                    return resp.completion_text.strip()
                return str(resp).strip()
            elif hasattr(provider, "chat"):
                resp = await provider.chat(prompt)
                return str(resp).strip()

        except Exception as e:
            logger.warning(f"LLM 总结对话失败: {e}")

        return ""

    async def close(self):
        """关闭数据库连接。"""
        if self._db:
            def _close():
                self._db.close()

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _close)
            self._db = None
        self._initialized = False
