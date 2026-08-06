"""
WebUI Pages API
使用 AstrBot 官方 register_web_api 注册 API 端点。
提供好感度管理、记忆图谱可视化、配置管理等完整 WebUI。
"""
import json
import time
from typing import Any

from ..core.time_schedule import get_current_window, get_window_name, CITLALI_SCHEDULE


def register_pages(plugin):
    """注册所有 Pages API"""
    reg = plugin.ctx.register_web_api

    PREFIX = "/citlali_affinity/page"

    # 总览
    reg(f"{PREFIX}/overview", api_overview(plugin), ["GET"], "Citlali overview")
    # 用户列表
    reg(f"{PREFIX}/users", api_users(plugin), ["GET"], "Citlali users")
    # 用户详情
    reg(f"{PREFIX}/user/detail", api_user_detail(plugin), ["GET"], "Citlali user detail")
    # 调整好感度
    reg(f"{PREFIX}/user/adjust", api_user_adjust(plugin), ["POST"], "Citlali adjust affinity")
    # 重置用户
    reg(f"{PREFIX}/user/reset", api_user_reset(plugin), ["POST"], "Citlali reset user")
    # 添加备注
    reg(f"{PREFIX}/user/note", api_user_note(plugin), ["POST"], "Citlali add note")
    # 记忆搜索
    reg(f"{PREFIX}/memories/search", api_memory_search(plugin), ["GET"], "Citlali memory search")
    # 记忆统计
    reg(f"{PREFIX}/memories/stats", api_memory_stats(plugin), ["GET"], "Citlali memory stats")
    # 图谱概览
    reg(f"{PREFIX}/graph/overview", api_graph_overview(plugin), ["GET"], "Citlali graph overview")
    # 最近记忆节点
    reg(f"{PREFIX}/memories/recent", api_recent_memories(plugin), ["GET"], "Citlali recent memories")
    # WebUI 页面
    reg(f"{PREFIX}", api_dashboard_page(plugin), ["GET"], "Citlali dashboard page")


# ==================== API Handlers ====================

def api_overview(plugin):
    async def handler():
        stats = plugin.affinity_mgr.get_stats()
        mem_ok = plugin._initialized
        mem_stats = await plugin.memory_engine.get_stats() if mem_ok else {}
        graph_ok = mem_ok and mem_stats.get('graph_nodes', 0) > 0
        window = get_current_window()
        schedule = CITLALI_SCHEDULE.get(window, {})
        return {
            "stats": stats,
            "memory_available": mem_ok,
            "graph_available": graph_ok,
            "memory_stats": mem_stats,
            "schedule": {
                "window": window,
                "window_name": get_window_name(window),
                "activity": schedule.get("activity", ""),
                "mood": schedule.get("mood", ""),
                "location": schedule.get("location", ""),
                "energy": schedule.get("energy", ""),
            },
            "config": {
                "affinity_enabled": plugin.affinity_enabled,
                "memory_enabled": plugin.memory_enabled,
                "inject_context": plugin.inject_context,
                "decay_enabled": plugin.decay_enabled,
                "upgrade_notify": plugin.upgrade_notify,
            }
        }
    return handler


def api_users(plugin):
    async def handler():
        board = plugin.affinity_mgr.get_leaderboard(200)
        return {"users": board}
    return handler


def api_user_detail(plugin):
    async def handler():
        from astrbot.api import logger
        # 从 request 获取参数
        try:
            from quart import request
            user_id = request.args.get("user_id", "")
        except Exception:
            user_id = ""

        if not user_id:
            return {"error": "missing user_id"}

        from .core.affinity_manager import AffinityStage, STAGE_NAMES, STAGE_THRESHOLDS
        user = plugin.affinity_mgr.get_user(user_id)
        stage = AffinityStage(user.get("stage", 0))
        user["stage_name"] = STAGE_NAMES[stage]
        user["current_threshold"] = STAGE_THRESHOLDS[stage]
        stages = sorted(AffinityStage)
        idx = stages.index(stage)
        if idx < len(stages) - 1:
            user["next_threshold"] = STAGE_THRESHOLDS[stages[idx + 1]]
        else:
            user["next_threshold"] = None
        return user
    return handler


def api_user_adjust(plugin):
    async def handler():
        try:
            from quart import request
            body = await request.get_json()
            user_id = body.get("user_id", "")
            amount = body.get("amount", 0)
            reason = body.get("reason", "manual")
        except Exception:
            return {"error": "invalid request"}

        if not user_id:
            return {"error": "missing user_id"}

        delta, upgraded = plugin.affinity_mgr.add_affinity(user_id, reason, amount=amount)
        return {"delta": delta, "upgraded": upgraded}
    return handler


def api_user_reset(plugin):
    async def handler():
        try:
            from quart import request
            body = await request.get_json()
            user_id = body.get("user_id", "")
        except Exception:
            return {"error": "invalid request"}

        if not user_id:
            return {"error": "missing user_id"}

        plugin.affinity_mgr.reset_user(user_id)
        return {"ok": True}
    return handler


def api_user_note(plugin):
    async def handler():
        try:
            from quart import request
            body = await request.get_json()
            user_id = body.get("user_id", "")
            note = body.get("note", "")
        except Exception:
            return {"error": "invalid request"}

        if not user_id or not note:
            return {"error": "missing params"}

        plugin.affinity_mgr.add_note(user_id, note)
        return {"ok": True}
    return handler


def api_memory_search(plugin):
    async def handler():
        try:
            from quart import request
            query = request.args.get("q", "")
            session_id = request.args.get("session_id", "")
        except Exception:
            query = ""

        if not query:
            return {"memories": []}

        results = await plugin.memory_engine.recall(query, session_id=session_id, k=10)
        memories = []
        for r in results:
            memories.append({
                "id": r.get("id"),
                "content": r.get("content", ""),
                "summary": r.get("summary", ""),
                "importance": r.get("importance", 0),
                "create_time": r.get("create_time", 0),
                "score": r.get("score", 0),
            })
        return {"memories": memories}
    return handler


def api_memory_stats(plugin):
    async def handler():
        stats = await plugin.memory_engine.get_stats()
        return stats
    return handler


def api_graph_overview(plugin):
    async def handler():
        try:
            from quart import request
            limit = int(request.args.get("limit", 48))
        except Exception:
            limit = 48

        graph = await plugin.memory_engine.get_graph_overview(limit_nodes=limit)
        if graph:
            return graph
        return {"nodes": [], "edges": []}
    return handler


def api_recent_memories(plugin):
    async def handler():
        try:
            from quart import request
            limit = int(request.args.get("limit", 20))
        except Exception:
            limit = 20

        memories = await plugin.memory_engine.recent_memories(limit=limit)
        return {"memories": memories}
    return handler


def api_dashboard_page(plugin):
    async def handler():
        from quart import Response
        html = _get_dashboard_html()
        return Response(html, content_type="text/html; charset=utf-8")
    return handler


# ==================== Dashboard HTML ====================

def _get_dashboard_html() -> str:
    return r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>茜特菈莉·好感度系统</title>
<style>
:root{--bg:#0f0f1a;--card:#1a1a2e;--card2:#16213e;--accent:#e94560;--accent2:#ff6b81;--green:#4ecca3;--yellow:#f5c542;--purple:#a855f7;--blue:#3b82f6;--text:#e8e8e8;--text2:#8888aa;--border:rgba(255,255,255,0.06)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.app{display:flex;min-height:100vh}
.sidebar{width:240px;background:var(--card);border-right:1px solid var(--border);padding:20px 0;flex-shrink:0}
.sidebar .logo{text-align:center;padding:20px;border-bottom:1px solid var(--border);margin-bottom:12px}
.sidebar .logo h2{font-size:18px;color:var(--accent)}
.sidebar .logo p{font-size:12px;color:var(--text2);margin-top:4px}
.nav-item{display:flex;align-items:center;gap:10px;padding:12px 24px;cursor:pointer;color:var(--text2);transition:all .2s;font-size:14px}
.nav-item:hover,.nav-item.active{color:var(--text);background:rgba(233,69,96,0.08);border-right:3px solid var(--accent)}
.main{flex:1;padding:24px;overflow-y:auto;max-height:100vh}
.page{display:none}.page.active{display:block}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px}
.stat-card{background:var(--card);border-radius:12px;padding:20px;border:1px solid var(--border)}
.stat-card .icon{font-size:28px;margin-bottom:8px}
.stat-card .value{font-size:32px;font-weight:700;color:var(--accent)}
.stat-card .label{font-size:13px;color:var(--text2);margin-top:4px}
.section{background:var(--card);border-radius:12px;padding:20px;margin-bottom:20px;border:1px solid var(--border)}
.section h3{font-size:16px;margin-bottom:16px;color:var(--text);display:flex;align-items:center;gap:8px}
table{width:100%;border-collapse:collapse}
th,td{padding:10px 14px;text-align:left;border-bottom:1px solid var(--border);font-size:13px}
th{color:var(--text2);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:0.5px}
tr:hover{background:rgba(255,255,255,0.02)}
.badge{display:inline-block;padding:3px 10px;border-radius:10px;font-size:11px;font-weight:600}
.badge-0{background:rgba(255,255,255,0.08);color:var(--text2)}
.badge-1{background:rgba(59,130,246,0.15);color:var(--blue)}
.badge-2{background:rgba(78,204,163,0.15);color:var(--green)}
.badge-3{background:rgba(168,85,247,0.15);color:var(--purple)}
.badge-4{background:rgba(245,197,66,0.15);color:var(--yellow)}
.badge-5{background:rgba(233,69,96,0.15);color:var(--accent)}
.progress{width:120px;height:6px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden;display:inline-block;vertical-align:middle}
.progress .fill{height:100%;border-radius:3px;background:var(--accent);transition:width .3s}
.btn{padding:6px 14px;border-radius:6px;border:none;cursor:pointer;font-size:12px;font-weight:600;transition:all .2s}
.btn-primary{background:var(--accent);color:#fff}.btn-primary:hover{background:var(--accent2)}
.btn-sm{padding:4px 10px;font-size:11px}
.btn-ghost{background:transparent;color:var(--text2);border:1px solid var(--border)}.btn-ghost:hover{color:var(--text);border-color:var(--text2)}
input[type="text"],input[type="number"],textarea{background:rgba(255,255,255,0.04);border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:6px;font-size:13px;width:100%}
input:focus,textarea:focus{outline:none;border-color:var(--accent)}
.search-bar{display:flex;gap:10px;margin-bottom:16px}
.search-bar input{flex:1}
.graph-container{width:100%;height:500px;background:var(--bg);border-radius:8px;position:relative;overflow:hidden}
.graph-node{position:absolute;padding:6px 12px;border-radius:8px;font-size:11px;cursor:pointer;transition:all .3s;white-space:nowrap}
.graph-edge{position:absolute;pointer-events:none}
.modal-bg{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);display:none;z-index:100;align-items:center;justify-content:center}
.modal-bg.show{display:flex}
.modal{background:var(--card);border-radius:16px;padding:24px;width:90%;max-width:500px;max-height:80vh;overflow-y:auto}
.modal h3{margin-bottom:16px;font-size:18px}
.mem-card{background:var(--card2);border-radius:8px;padding:12px;margin-bottom:8px;font-size:13px;line-height:1.6}
.mem-card .meta{font-size:11px;color:var(--text2);margin-top:6px}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;margin-right:4px}
.tag-important{background:rgba(233,69,96,0.15);color:var(--accent)}
.tag-entity{background:rgba(59,130,246,0.15);color:var(--blue)}
.empty{text-align:center;padding:40px;color:var(--text2)}
#graphCanvas{width:100%;height:100%}
</style>
</head>
<body>
<div class="app">
  <!-- 侧边栏 -->
  <div class="sidebar">
    <div class="logo">
      <h2>✦ 茜特菈莉</h2>
      <p>好感度 · 记忆 · 图谱</p>
    </div>
    <div class="nav-item active" data-page="overview" onclick="nav('overview')">📊 总览</div>
    <div class="nav-item" data-page="users" onclick="nav('users')">👥 用户管理</div>
    <div class="nav-item" data-page="memory" onclick="nav('memory')">🧠 记忆检索</div>
    <div class="nav-item" data-page="graph" onclick="nav('graph')">🕸️ 记忆图谱</div>
    <div class="nav-item" data-page="config" onclick="nav('config')">⚙️ 配置</div>
  </div>

  <!-- 主内容 -->
  <div class="main">
    <!-- 总览页 -->
    <div class="page active" id="page-overview">
      <div class="stats-grid">
        <div class="stat-card"><div class="icon">👥</div><div class="value" id="s-users">-</div><div class="label">总用户</div></div>
        <div class="stat-card"><div class="icon">💬</div><div class="value" id="s-msgs">-</div><div class="label">总对话</div></div>
        <div class="stat-card"><div class="icon">🧠</div><div class="value" id="s-mem">-</div><div class="label">记忆总数</div></div>
        <div class="stat-card"><div class="icon">🕸️</div><div class="value" id="s-graph">-</div><div class="label">图谱节点</div></div>
        <div class="stat-card"><div class="icon">🌙</div><div class="value" id="s-window" style="font-size:20px">-</div><div class="label" id="s-activity">当前时段</div></div>
        <div class="stat-card"><div class="icon">❤️</div><div class="value" id="s-traveler">-</div><div class="label">旅行者</div></div>
        <div class="stat-card"><div class="icon">🔗</div><div class="value" id="s-lm">-</div><div class="label">记忆引擎</div></div>
      </div>
      <div class="section">
        <h3>📈 关系分布</h3>
        <div id="stage-chart"></div>
      </div>
      <div class="section">
        <h3>🕐 最近活跃用户</h3>
        <table><thead><tr><th>用户</th><th>关系</th><th>好感度</th><th>最后活跃</th></tr></thead><tbody id="recent-users"></tbody></table>
      </div>
    </div>

    <!-- 用户管理页 -->
    <div class="page" id="page-users">
      <div class="section">
        <h3>👥 用户管理</h3>
        <div class="search-bar">
          <input type="text" id="userSearch" placeholder="搜索用户昵称、ID、关系阶段..." oninput="filterUsers()">
        </div>
        <table>
          <thead><tr><th>#</th><th>用户</th><th>关系</th><th>好感度</th><th>进度</th><th>对话</th><th>活跃</th><th>操作</th></tr></thead>
          <tbody id="userTable"></tbody>
        </table>
      </div>
    </div>

    <!-- 记忆检索页 -->
    <div class="page" id="page-memory">
      <div class="section">
        <h3>🧠 记忆检索</h3>
        <div class="search-bar">
          <input type="text" id="memQuery" placeholder="搜索记忆内容（如：小说、酒、旅行者、维奇琳）...">
          <button class="btn btn-primary" onclick="searchMemory()">搜索</button>
        </div>
        <div id="memResults"></div>
      </div>
      <div class="section">
        <h3>🕐 最近记忆节点</h3>
        <div id="recentMemories"></div>
      </div>
    </div>

    <!-- 图谱页 -->
    <div class="page" id="page-graph">
      <div class="section">
        <h3>🕸️ 记忆图谱 <button class="btn btn-ghost btn-sm" onclick="loadGraph()" style="margin-left:auto">刷新</button></h3>
        <div class="graph-container">
          <canvas id="graphCanvas"></canvas>
        </div>
        <div style="margin-top:12px;font-size:12px;color:var(--text2)">
          <span class="tag tag-entity">● 实体节点</span>
          <span class="tag tag-important">● 重要记忆</span>
          拖拽节点可移动 | 滚轮缩放 | 点击查看详情
        </div>
      </div>
      <div class="section">
        <h3>📊 图谱统计</h3>
        <div id="graphStats"></div>
      </div>
    </div>

    <!-- 配置页 -->
    <div class="page" id="page-config">
      <div class="section">
        <h3>⚙️ 插件配置</h3>
        <div id="configView"></div>
        <p style="margin-top:16px;font-size:12px;color:var(--text2)">
          配置修改请在 AstrBot WebUI 的「插件 → citlali_affinity → 配置」中进行。
        </p>
      </div>
      <div class="section">
        <h3>ℹ️ 系统信息</h3>
        <div id="sysInfo"></div>
      </div>
    </div>
  </div>
</div>

<!-- 用户详情弹窗 -->
<div class="modal-bg" id="userModal">
  <div class="modal">
    <h3 id="modalTitle">用户详情</h3>
    <div id="modalBody"></div>
    <div style="margin-top:16px;text-align:right;gap:8px;display:flex;justify-content:flex-end">
      <button class="btn btn-ghost" onclick="closeModal()">关闭</button>
    </div>
  </div>
</div>

<script>
const API = '/openclaw/pages/citlali_affinity/page';
let allUsers = [];

// 导航
function nav(page) {
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelector(`.nav-item[data-page="${page}"]`).classList.add('active');
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  if (page === 'graph') loadGraph();
  if (page === 'memory') loadRecentMemories();
}

// 加载总览
async function loadOverview() {
  try {
    const r = await fetch(API + '/overview');
    const d = await r.json();
    document.getElementById('s-users').textContent = d.stats.total_users;
    document.getElementById('s-msgs').textContent = d.stats.total_messages;
    document.getElementById('s-mem').textContent = d.memory_stats?.total_memories ?? '-';
    document.getElementById('s-graph').textContent = d.memory_stats?.graph_nodes ?? '-';
    document.getElementById('s-traveler').textContent = d.stats.stage_counts?.['旅行者'] || 0;
    document.getElementById('s-lm').textContent = d.memory_available ? '✓ 已连接' : '✗ 未连接';
    document.getElementById('s-lm').style.color = d.memory_available ? 'var(--green)' : 'var(--accent)';

    // 日程
    if (d.schedule) {
      document.getElementById('s-window').textContent = d.schedule.window_name || '-';
      document.getElementById('s-activity').textContent = d.schedule.activity || '-';
    }

    // 关系分布
    const chart = document.getElementById('stage-chart');
    const stages = d.stats.stage_counts || {};
    const max = Math.max(...Object.values(stages), 1);
    chart.innerHTML = Object.entries(stages).map(([name, count]) =>
      `<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
        <span style="width:60px;font-size:13px;color:var(--text2)">${name}</span>
        <div style="flex:1;height:20px;background:rgba(255,255,255,0.04);border-radius:4px;overflow:hidden">
          <div style="height:100%;width:${count/max*100}%;background:var(--accent);border-radius:4px;transition:width .5s"></div>
        </div>
        <span style="width:30px;text-align:right;font-size:13px">${count}</span>
      </div>`
    ).join('');

    // 配置
    const cfg = d.config;
    document.getElementById('configView').innerHTML = Object.entries(cfg).map(([k,v]) =>
      `<div style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--border)">
        <span>${k}</span>
        <span style="color:${v?'var(--green)':'var(--text2)'}">${v?'✓ 开启':'✗ 关闭'}</span>
      </div>`
    ).join('');

    document.getElementById('sysInfo').innerHTML =
      `<div style="font-size:13px;line-height:2">
        <div>记忆系统: ${d.memory_available ? '✓ 已连接' : '✗ 未连接'}</div>
        <div>图谱系统: ${d.graph_available ? '✓ 可用' : '✗ 不可用'}</div>
        <div>记忆总数: ${d.memory_stats?.total_memories ?? '-'}</div>
        <div>图谱节点: ${d.memory_stats?.graph_nodes ?? '-'}</div>
        <div>图谱边数: ${d.memory_stats?.graph_edges ?? '-'}</div>
        <div>原子数量: ${d.memory_stats?.atom_count ?? '-'}</div>
      </div>`;

  } catch(e) { console.error('overview error:', e); }
}

// 加载用户
async function loadUsers() {
  try {
    const r = await fetch(API + '/users');
    const d = await r.json();
    allUsers = d.users || [];
    renderUsers(allUsers);
    // 最近活跃
    const recent = [...allUsers].sort((a,b) => (b.last_active||0) - (a.last_active||0)).slice(0,5);
    document.getElementById('recent-users').innerHTML = recent.map(u =>
      `<tr><td>${u.nickname||u.user_id.slice(0,10)}</td><td><span class="badge badge-${['陌生人','熟人','朋友','好友','知己','旅行者'].indexOf(u.stage)}">${u.stage}</span></td><td>${u.affinity}</td><td>${u.last_active?timeAgo(u.last_active*1000):'-'}</td></tr>`
    ).join('') || '<tr><td colspan="4" class="empty">暂无数据</td></tr>';
  } catch(e) { console.error(e); }
}

function renderUsers(users) {
  const tbody = document.getElementById('userTable');
  if (!users.length) { tbody.innerHTML = '<tr><td colspan="8" class="empty">暂无用户</td></tr>'; return; }
  tbody.innerHTML = users.map((u,i) => {
    const sn = ['陌生人','熟人','朋友','好友','知己','旅行者'].indexOf(u.stage);
    return `<tr>
      <td>${i+1}</td>
      <td>${u.nickname||u.user_id.slice(0,10)}</td>
      <td><span class="badge badge-${sn}">${u.stage}</span></td>
      <td><strong>${u.affinity}</strong></td>
      <td><div class="progress"><div class="fill" style="width:${Math.min(100,u.affinity/15)}%"></div></div></td>
      <td>${u.total_messages}</td>
      <td style="font-size:11px;color:var(--text2)">${u.last_active?timeAgo(u.last_active*1000):'-'}</td>
      <td><button class="btn btn-primary btn-sm" onclick="openUser('${u.user_id}')">详情</button></td>
    </tr>`;
  }).join('');
}

function filterUsers() {
  const q = document.getElementById('userSearch').value.toLowerCase();
  renderUsers(allUsers.filter(u => (u.nickname||'').toLowerCase().includes(q) || u.user_id.toLowerCase().includes(q) || u.stage.includes(q)));
}

// 用户详情
async function openUser(uid) {
  try {
    const r = await fetch(API + '/user/detail?user_id=' + encodeURIComponent(uid));
    const u = await r.json();
    const prog = u.next_threshold ? ((u.affinity - u.current_threshold)/(u.next_threshold-u.current_threshold)*100).toFixed(0) : 100;
    const ms = (u.milestones||[]).slice(-8).reverse();
    document.getElementById('modalTitle').textContent = u.nickname || uid;
    document.getElementById('modalBody').innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px">
        <div><div style="font-size:12px;color:var(--text2)">好感度</div><div style="font-size:28px;font-weight:700;color:var(--accent)">${u.affinity}</div></div>
        <div><div style="font-size:12px;color:var(--text2)">关系阶段</div><div><span class="badge badge-${u.stage}">${u.stage_name}</span></div></div>
        <div><div style="font-size:12px;color:var(--text2)">进度</div><div style="font-size:18px">${prog}%</div></div>
        <div><div style="font-size:12px;color:var(--text2)">对话次数</div><div style="font-size:18px">${u.total_messages}</div></div>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:12px;color:var(--text2);margin-bottom:6px">手动调整</div>
        <div style="display:flex;gap:8px">
          <input type="number" id="adjVal" value="10" style="width:80px">
          <button class="btn btn-primary btn-sm" onclick="adjustAffinity('${uid}',1)">增加</button>
          <button class="btn btn-ghost btn-sm" onclick="adjustAffinity('${uid}',-1)">减少</button>
        </div>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:12px;color:var(--text2);margin-bottom:6px">里程碑</div>
        ${ms.length ? ms.map(m => `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px">
          ${m.type==='stage_up'?'🎉':'⬇️'} ${['陌生人','熟人','朋友','好友','知己','旅行者'][m.from]}→${['陌生人','熟人','朋友','好友','知己','旅行者'][m.to]}
          <span style="float:right;color:var(--text2)">${new Date(m.time*1000).toLocaleDateString()}</span>
        </div>`).join('') : '<div class="empty" style="padding:10px">暂无里程碑</div>'}
      </div>
      <div>
        <div style="font-size:12px;color:var(--text2);margin-bottom:6px">备注</div>
        <textarea id="noteText" rows="3" style="width:100%">${u.notes||''}</textarea>
        <button class="btn btn-ghost btn-sm" style="margin-top:6px" onclick="saveNote('${uid}')">保存备注</button>
      </div>
    `;
    document.getElementById('userModal').classList.add('show');
  } catch(e) { console.error(e); }
}

async function adjustAffinity(uid, sign) {
  const val = parseInt(document.getElementById('adjVal').value) || 0;
  if (!val) return;
  await fetch(API + '/user/adjust', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid,amount:val*sign,reason:'manual'})});
  openUser(uid); loadUsers(); loadOverview();
}

async function saveNote(uid) {
  const note = document.getElementById('noteText').value;
  await fetch(API + '/user/note', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid,note})});
  alert('已保存');
}

function closeModal() { document.getElementById('userModal').classList.remove('show'); }

// 记忆搜索
async function searchMemory() {
  const q = document.getElementById('memQuery').value.trim();
  if (!q) return;
  try {
    const r = await fetch(API + '/memories/search?q=' + encodeURIComponent(q));
    const d = await r.json();
    const div = document.getElementById('memResults');
    if (d.memories?.length) {
      div.innerHTML = d.memories.map(m =>
        `<div class="mem-card">
          <div>${(m.content||m.summary||'').substring(0,200)}</div>
          <div class="meta">重要性: ${(m.importance||0).toFixed(2)} | 得分: ${(m.score||0).toFixed(3)} | ${m.create_time ? new Date(m.create_time*1000).toLocaleString() : '-'}</div>
        </div>`
      ).join('');
    } else {
      div.innerHTML = '<div class="empty">未找到相关记忆</div>';
    }
  } catch(e) { console.error(e); }
}

async function loadRecentMemories() {
  try {
    const r = await fetch(API + '/memories/recent?limit=15');
    const d = await r.json();
    const div = document.getElementById('recentMemories');
    if (d.memories?.length) {
      div.innerHTML = d.memories.map(m =>
        `<div class="mem-card">
          <div>${(m.content||m.summary||'').substring(0,150)}</div>
          <div class="meta">${m.create_time ? new Date(m.create_time*1000).toLocaleString() : '-'}</div>
        </div>`
      ).join('');
    } else {
      div.innerHTML = '<div class="empty">暂无记忆</div>';
    }
  } catch(e) { console.error(e); }
}

// 图谱
let graphData = {nodes:[], edges:[]};
async function loadGraph() {
  try {
    const r = await fetch(API + '/graph/overview?limit=48');
    const d = await r.json();
    graphData = d;
    drawGraph();
    document.getElementById('graphStats').innerHTML =
      `<div style="font-size:13px">节点: ${d.nodes?.length||0} | 关系: ${d.edges?.length||0}</div>`;
  } catch(e) { console.error(e); }
}

function drawGraph() {
  const canvas = document.getElementById('graphCanvas');
  const ctx = canvas.getContext('2d');
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width;
  canvas.height = rect.height;
  const W = canvas.width, H = canvas.height;

  ctx.clearRect(0,0,W,H);

  const nodes = graphData.nodes || [];
  const edges = graphData.edges || [];

  if (!nodes.length) {
    ctx.fillStyle = '#555';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('暂无图谱数据', W/2, H/2);
    return;
  }

  // 简单力导向布局
  const nodeMap = {};
  nodes.forEach((n,i) => {
    const angle = (i / nodes.length) * Math.PI * 2;
    const r = Math.min(W,H) * 0.3;
    n.x = n.x || W/2 + Math.cos(angle) * r + (Math.random()-0.5)*40;
    n.y = n.y || H/2 + Math.sin(angle) * r + (Math.random()-0.5)*40;
    n.vx = 0; n.vy = 0;
    nodeMap[n.id || n.label] = n;
  });

  // 简单迭代布局
  for (let iter = 0; iter < 50; iter++) {
    // 排斥力
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i+1; j < nodes.length; j++) {
        let dx = nodes[i].x - nodes[j].x;
        let dy = nodes[i].y - nodes[j].y;
        let dist = Math.sqrt(dx*dx+dy*dy) || 1;
        let force = 500 / (dist * dist);
        nodes[i].vx += dx/dist * force;
        nodes[i].vy += dy/dist * force;
        nodes[j].vx -= dx/dist * force;
        nodes[j].vy -= dy/dist * force;
      }
    }
    // 引力
    edges.forEach(e => {
      const s = nodeMap[e.source], t = nodeMap[e.target];
      if (!s || !t) return;
      let dx = t.x - s.x, dy = t.y - s.y;
      let dist = Math.sqrt(dx*dx+dy*dy) || 1;
      let force = (dist - 100) * 0.01;
      s.vx += dx/dist * force;
      s.vy += dy/dist * force;
      t.vx -= dx/dist * force;
      t.vy -= dy/dist * force;
    });
    // 向心力
    nodes.forEach(n => {
      n.vx += (W/2 - n.x) * 0.001;
      n.vy += (H/2 - n.y) * 0.001;
      n.vx *= 0.8; n.vy *= 0.8;
      n.x += n.vx; n.y += n.vy;
      n.x = Math.max(50, Math.min(W-50, n.x));
      n.y = Math.max(50, Math.min(H-50, n.y));
    });
  }

  // 画边
  ctx.strokeStyle = 'rgba(255,255,255,0.1)';
  ctx.lineWidth = 1;
  edges.forEach(e => {
    const s = nodeMap[e.source], t = nodeMap[e.target];
    if (!s || !t) return;
    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(t.x, t.y);
    ctx.stroke();
    // 关系标签
    if (e.relation) {
      ctx.fillStyle = 'rgba(255,255,255,0.3)';
      ctx.font = '9px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(e.relation, (s.x+t.x)/2, (s.y+t.y)/2);
    }
  });

  // 画节点
  const colors = {entity:'#3b82f6',memory:'#e94560',person:'#4ecca3',event:'#f5c542',concept:'#a855f7'};
  nodes.forEach(n => {
    const c = colors[n.type] || colors.entity;
    ctx.beginPath();
    ctx.arc(n.x, n.y, 6, 0, Math.PI*2);
    ctx.fillStyle = c;
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.2)';
    ctx.lineWidth = 1;
    ctx.stroke();
    // 标签
    ctx.fillStyle = '#ddd';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText((n.label||'').substring(0,12), n.x, n.y - 10);
  });
}

function timeAgo(ts) {
  const d = Date.now() - ts;
  if (d < 60000) return '刚刚';
  if (d < 3600000) return Math.floor(d/60000)+'分钟前';
  if (d < 86400000) return Math.floor(d/3600000)+'小时前';
  return Math.floor(d/86400000)+'天前';
}

// 初始化
loadOverview();
loadUsers();
</script>
</body>
</html>"""
