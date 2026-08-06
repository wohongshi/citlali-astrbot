"""
WebUI Pages API
提供好感度管理面板的后端接口。
"""
import time
import json
from aiohttp import web


def setup_pages(plugin, app: web.Application):
    """注册 Pages 路由"""

    @app.get("/api/citlali/overview")
    async def api_overview(request):
        """总览数据"""
        stats = plugin.affinity_mgr.get_stats()
        mem_ok = plugin.memory_engine.is_available()
        return web.json_response({
            "stats": stats,
            "memory_available": mem_ok,
            "config": {
                "affinity_enabled": plugin.affinity_enabled,
                "memory_enabled": plugin.memory_enabled,
                "inject_context": plugin.inject_context,
                "decay_enabled": plugin.decay_enabled,
                "upgrade_notify": plugin.upgrade_notify,
            }
        })

    @app.get("/api/citlali/users")
    async def api_users(request):
        """用户列表"""
        board = plugin.affinity_mgr.get_leaderboard(100)
        return web.json_response({"users": board})

    @app.get("/api/citlali/user/{user_id}")
    async def api_user_detail(request):
        """用户详情"""
        user_id = request.match_info["user_id"]
        user = plugin.affinity_mgr.get_user(user_id)
        from ..core.affinity_manager import AffinityStage, STAGE_NAMES, STAGE_THRESHOLDS
        stage = AffinityStage(user.get("stage", 0))
        user["stage_name"] = STAGE_NAMES[stage]
        user["current_threshold"] = STAGE_THRESHOLDS[stage]
        # 计算下一阶段阈值
        stages = sorted(AffinityStage)
        idx = stages.index(stage)
        if idx < len(stages) - 1:
            user["next_threshold"] = STAGE_THRESHOLDS[stages[idx + 1]]
        else:
            user["next_threshold"] = None
        return web.json_response(user)

    @app.post("/api/citlali/user/{user_id}/affinity")
    async def api_adjust_affinity(request):
        """手动调整好感度"""
        user_id = request.match_info["user_id"]
        body = await request.json()
        amount = body.get("amount", 0)
        reason = body.get("reason", "manual")
        delta, upgraded = plugin.affinity_mgr.add_affinity(user_id, reason, amount=amount)
        return web.json_response({"delta": delta, "upgraded": upgraded})

    @app.post("/api/citlali/user/{user_id}/reset")
    async def api_reset_user(request):
        """重置用户"""
        user_id = request.match_info["user_id"]
        plugin.affinity_mgr.reset_user(user_id)
        return web.json_response({"ok": True})

    @app.post("/api/citlali/user/{user_id}/note")
    async def api_add_note(request):
        """添加备注"""
        user_id = request.match_info["user_id"]
        body = await request.json()
        note = body.get("note", "")
        plugin.affinity_mgr.add_note(user_id, note)
        return web.json_response({"ok": True})

    @app.get("/api/citlali/memories")
    async def api_memories(request):
        """查询记忆"""
        query = request.query.get("q", "")
        user_id = request.query.get("user_id", "")
        if not query:
            return web.json_response({"memories": []})
        memories = await plugin.memory_engine.recall(query, session_id=user_id)
        return web.json_response({"memories": memories})

    @app.get("/api/citlali/dashboard")
    async def api_dashboard_page(request):
        """返回 WebUI 页面"""
        html = _get_dashboard_html()
        return web.Response(text=html, content_type="text/html")


def _get_dashboard_html() -> str:
    """生成 WebUI 页面 HTML"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>茜特菈莉·好感度系统</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
:root {
  --bg: #1a1a2e; --card: #16213e; --accent: #e94560;
  --accent2: #0f3460; --text: #eee; --text2: #aaa;
  --green: #4ecca3; --yellow: #f5c542; --red: #e94560;
  --purple: #a855f7; --blue: #3b82f6;
}
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg); color: var(--text);
  min-height: 100vh; padding: 20px;
}
.container { max-width: 1200px; margin: 0 auto; }
.header {
  text-align: center; padding: 30px 0 20px;
  border-bottom: 1px solid rgba(255,255,255,0.1); margin-bottom: 24px;
}
.header h1 { font-size: 24px; color: var(--accent); }
.header p { color: var(--text2); margin-top: 8px; font-size: 14px; }
.stats-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px; margin-bottom: 24px;
}
.stat-card {
  background: var(--card); border-radius: 12px; padding: 20px;
  text-align: center; border: 1px solid rgba(255,255,255,0.05);
}
.stat-card .value { font-size: 32px; font-weight: 700; color: var(--accent); }
.stat-card .label { font-size: 13px; color: var(--text2); margin-top: 4px; }
.section { margin-bottom: 24px; }
.section h2 {
  font-size: 18px; margin-bottom: 16px; color: var(--text);
  padding-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,0.1);
}
.user-table {
  width: 100%; border-collapse: collapse;
  background: var(--card); border-radius: 12px; overflow: hidden;
}
.user-table th, .user-table td {
  padding: 12px 16px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.05);
}
.user-table th { background: rgba(255,255,255,0.05); color: var(--text2); font-size: 13px; }
.user-table tr:hover { background: rgba(255,255,255,0.03); }
.stage-badge {
  display: inline-block; padding: 3px 10px; border-radius: 12px;
  font-size: 12px; font-weight: 600;
}
.stage-0 { background: rgba(255,255,255,0.1); color: var(--text2); }
.stage-1 { background: rgba(59,130,246,0.2); color: var(--blue); }
.stage-2 { background: rgba(78,204,163,0.2); color: var(--green); }
.stage-3 { background: rgba(168,85,247,0.2); color: var(--purple); }
.stage-4 { background: rgba(245,197,66,0.2); color: var(--yellow); }
.stage-5 { background: rgba(233,69,96,0.2); color: var(--red); }
.progress-bar {
  width: 100%; height: 6px; background: rgba(255,255,255,0.1);
  border-radius: 3px; overflow: hidden;
}
.progress-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }
.affinity-input {
  background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
  color: var(--text); padding: 6px 12px; border-radius: 6px; width: 80px;
}
.btn {
  padding: 6px 16px; border-radius: 6px; border: none; cursor: pointer;
  font-size: 13px; font-weight: 600; transition: all 0.2s;
}
.btn-primary { background: var(--accent); color: white; }
.btn-primary:hover { opacity: 0.8; }
.btn-sm { padding: 4px 10px; font-size: 12px; }
.btn-danger { background: rgba(233,69,96,0.2); color: var(--red); }
.search-box {
  display: flex; gap: 10px; margin-bottom: 16px;
}
.search-input {
  flex: 1; background: var(--card); border: 1px solid rgba(255,255,255,0.1);
  color: var(--text); padding: 10px 16px; border-radius: 8px; font-size: 14px;
}
.tab-bar { display: flex; gap: 8px; margin-bottom: 16px; }
.tab {
  padding: 8px 20px; border-radius: 8px; cursor: pointer; font-size: 14px;
  background: var(--card); color: var(--text2); border: 1px solid transparent;
}
.tab.active { background: var(--accent); color: white; }
.modal-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.6); display: none; z-index: 100;
  align-items: center; justify-content: center;
}
.modal-overlay.show { display: flex; }
.modal {
  background: var(--card); border-radius: 16px; padding: 24px;
  width: 90%; max-width: 500px; max-height: 80vh; overflow-y: auto;
}
.modal h3 { margin-bottom: 16px; }
.form-group { margin-bottom: 12px; }
.form-group label { display: block; font-size: 13px; color: var(--text2); margin-bottom: 4px; }
.form-group input, .form-group textarea {
  width: 100%; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
  color: var(--text); padding: 8px 12px; border-radius: 6px;
}
.form-group textarea { min-height: 60px; resize: vertical; }
.milestone { padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 13px; }
.milestone-time { color: var(--text2); font-size: 12px; }
.empty-state { text-align: center; padding: 40px; color: var(--text2); }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>✦ 茜特菈莉·好感度系统 ✦</h1>
    <p>黑曜石奶奶的记忆与关系管理面板</p>
  </div>

  <!-- 总览 -->
  <div class="stats-grid" id="statsGrid">
    <div class="stat-card"><div class="value" id="statUsers">-</div><div class="label">总用户</div></div>
    <div class="stat-card"><div class="value" id="statMsgs">-</div><div class="label">总对话</div></div>
    <div class="stat-card"><div class="value" id="statMemory">-</div><div class="label">记忆系统</div></div>
    <div class="stat-card"><div class="value" id="statTraveler">-</div><div class="label">旅行者</div></div>
  </div>

  <!-- 标签页 -->
  <div class="tab-bar">
    <div class="tab active" data-tab="users" onclick="switchTab('users')">用户列表</div>
    <div class="tab" data-tab="memory" onclick="switchTab('memory')">记忆搜索</div>
    <div class="tab" data-tab="config" onclick="switchTab('config')">配置</div>
  </div>

  <!-- 用户列表 -->
  <div class="section" id="tab-users">
    <div class="search-box">
      <input class="search-input" id="userSearch" placeholder="搜索用户..." oninput="filterUsers()">
    </div>
    <table class="user-table">
      <thead>
        <tr>
          <th>排名</th><th>用户</th><th>关系</th><th>好感度</th><th>进度</th><th>对话</th><th>最后活跃</th><th>操作</th>
        </tr>
      </thead>
      <tbody id="userTableBody"></tbody>
    </table>
  </div>

  <!-- 记忆搜索 -->
  <div class="section" id="tab-memory" style="display:none">
    <div class="search-box">
      <input class="search-input" id="memoryQuery" placeholder="搜索记忆（如：小说、酒、旅行者）...">
      <button class="btn btn-primary" onclick="searchMemory()">搜索</button>
    </div>
    <div id="memoryResults"></div>
  </div>

  <!-- 配置 -->
  <div class="section" id="tab-config" style="display:none">
    <div style="background:var(--card);border-radius:12px;padding:20px;">
      <h3 style="margin-bottom:16px;">插件配置</h3>
      <div id="configItems"></div>
      <p style="margin-top:16px;font-size:13px;color:var(--text2);">
        配置修改需要在 AstrBot WebUI 的插件配置页面进行。此处仅展示当前状态。
      </p>
    </div>
  </div>
</div>

<!-- 用户详情弹窗 -->
<div class="modal-overlay" id="userModal">
  <div class="modal">
    <h3 id="modalTitle">用户详情</h3>
    <div id="modalBody"></div>
    <div style="margin-top:16px;text-align:right;">
      <button class="btn" style="background:rgba(255,255,255,0.1);color:var(--text);" onclick="closeModal()">关闭</button>
    </div>
  </div>
</div>

<script>
const API = '/openclaw/pages/api/citlali';
let allUsers = [];

async function loadOverview() {
  try {
    const r = await fetch(API + '/overview');
    const d = await r.json();
    document.getElementById('statUsers').textContent = d.stats.total_users;
    document.getElementById('statMsgs').textContent = d.stats.total_messages;
    document.getElementById('statMemory').textContent = d.memory_available ? '✓' : '✗';
    document.getElementById('statTraveler').textContent = d.stats.stage_counts['旅行者'] || 0;
    // 配置
    const cfg = d.config;
    const cfgHtml = Object.entries(cfg).map(([k,v]) =>
      `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
        <span>${k}</span><span style="color:${v?'var(--green)':'var(--red)'}">${v?'✓ 开启':'✗ 关闭'}</span>
      </div>`
    ).join('');
    document.getElementById('configItems').innerHTML = cfgHtml;
  } catch(e) { console.error(e); }
}

async function loadUsers() {
  try {
    const r = await fetch(API + '/users');
    const d = await r.json();
    allUsers = d.users;
    renderUsers(allUsers);
  } catch(e) { console.error(e); }
}

function renderUsers(users) {
  const tbody = document.getElementById('userTableBody');
  if (!users.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-state">暂无用户数据</td></tr>';
    return;
  }
  tbody.innerHTML = users.map((u, i) => {
    const stageNum = ['陌生人','熟人','朋友','好友','知己','旅行者'].indexOf(u.stage);
    const lastActive = u.last_active ? timeAgo(u.last_active * 1000) : '-';
    return `<tr>
      <td>${i+1}</td>
      <td>${u.nickname || u.user_id.slice(0,12)}</td>
      <td><span class="stage-badge stage-${stageNum}">${u.stage}</span></td>
      <td><strong>${u.affinity}</strong></td>
      <td><div class="progress-bar"><div class="progress-fill" style="width:${Math.min(100,u.affinity/15)}%;background:var(--accent)"></div></div></td>
      <td>${u.total_messages}</td>
      <td style="font-size:12px;color:var(--text2)">${lastActive}</td>
      <td><button class="btn btn-sm btn-primary" onclick="openUserDetail('${u.user_id}')">详情</button></td>
    </tr>`;
  }).join('');
}

function filterUsers() {
  const q = document.getElementById('userSearch').value.toLowerCase();
  const filtered = allUsers.filter(u =>
    (u.nickname || '').toLowerCase().includes(q) ||
    u.user_id.toLowerCase().includes(q) ||
    u.stage.includes(q)
  );
  renderUsers(filtered);
}

async function openUserDetail(userId) {
  try {
    const r = await fetch(API + '/user/' + encodeURIComponent(userId));
    const u = await r.json();
    const progress = u.next_threshold ?
      ((u.affinity - u.current_threshold) / (u.next_threshold - u.current_threshold) * 100).toFixed(0) : 100;
    const milestones = (u.milestones || []).slice(-10).reverse();
    const milestonesHtml = milestones.length ? milestones.map(m =>
      `<div class="milestone">
        <span>${m.type === 'stage_up' ? '🎉 升级' : '⬇️ 降级'}: ${stageName(m.from)} → ${stageName(m.to)}</span>
        <span class="milestone-time">${new Date(m.time*1000).toLocaleString()}</span>
      </div>`
    ).join('') : '<div class="empty-state">暂无里程碑</div>';

    document.getElementById('modalTitle').textContent = u.nickname || userId;
    document.getElementById('modalBody').innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">
        <div><span style="color:var(--text2)">好感度</span><br><strong style="font-size:24px;color:var(--accent)">${u.affinity}</strong></div>
        <div><span style="color:var(--text2)">关系</span><br><span class="stage-badge stage-${u.stage}">${u.stage_name}</span></div>
        <div><span style="color:var(--text2)">进度</span><br>${progress}%</div>
        <div><span style="color:var(--text2)">对话次数</span><br>${u.total_messages}</div>
      </div>
      <div style="margin-bottom:16px;">
        <span style="color:var(--text2)">手动调整</span>
        <div style="display:flex;gap:8px;margin-top:8px;">
          <input class="affinity-input" id="affinityDelta" type="number" placeholder="±数值" value="10">
          <button class="btn btn-primary btn-sm" onclick="adjustAffinity('${userId}', 1)">增加</button>
          <button class="btn btn-danger btn-sm" onclick="adjustAffinity('${userId}', -1)">减少</button>
        </div>
      </div>
      <div style="margin-bottom:16px;">
        <span style="color:var(--text2)">里程碑</span>
        ${milestonesHtml}
      </div>
      <div>
        <span style="color:var(--text2)">备注</span>
        <textarea id="noteInput" style="width:100%;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);color:var(--text);padding:8px;border-radius:6px;margin-top:4px;min-height:50px">${u.notes||''}</textarea>
      </div>
    `;
    document.getElementById('userModal').classList.add('show');
  } catch(e) { console.error(e); }
}

async function adjustAffinity(userId, sign) {
  const val = parseInt(document.getElementById('affinityDelta').value) || 0;
  if (!val) return;
  try {
    await fetch(API + '/user/' + encodeURIComponent(userId) + '/affinity', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({amount: val * sign, reason: 'manual'})
    });
    openUserDetail(userId);
    loadUsers();
    loadOverview();
  } catch(e) { console.error(e); }
}

function closeModal() {
  document.getElementById('userModal').classList.remove('show');
}

async function searchMemory() {
  const q = document.getElementById('memoryQuery').value.trim();
  if (!q) return;
  try {
    const r = await fetch(API + '/memories?q=' + encodeURIComponent(q));
    const d = await r.json();
    const div = document.getElementById('memoryResults');
    if (d.memories.length) {
      div.innerHTML = d.memories.map((m, i) =>
        `<div style="background:var(--card);border-radius:8px;padding:12px;margin-bottom:8px;font-size:14px;">${m}</div>`
      ).join('');
    } else {
      div.innerHTML = '<div class="empty-state">未找到相关记忆</div>';
    }
  } catch(e) { console.error(e); }
}

function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.tab[data-tab="${tab}"]`).classList.add('active');
  ['users','memory','config'].forEach(t => {
    document.getElementById('tab-' + t).style.display = t === tab ? 'block' : 'none';
  });
}

function stageName(n) {
  return ['陌生人','熟人','朋友','好友','知己','旅行者'][n] || '未知';
}

function timeAgo(ts) {
  const diff = Date.now() - ts;
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return Math.floor(diff/60000) + '分钟前';
  if (diff < 86400000) return Math.floor(diff/3600000) + '小时前';
  return Math.floor(diff/86400000) + '天前';
}

// 初始化
loadOverview();
loadUsers();
</script>
</body>
</html>"""
