let currentWs = null;
let currentSession = null;
let isWorking = false;

// ── Markdown Config ───────────────────────────────────────────────────────────
if (typeof marked !== 'undefined') {
  const renderer = new marked.Renderer();
  renderer.link = function(opts) {
    const { href, title, text } = opts;
    return `<a href="${href}" title="${title || ''}" target="_blank" rel="noopener noreferrer">${text}</a>`;
  };
  marked.setOptions({ renderer, gfm: true, breaks: true });
}

function renderMessage(text) {
  if (typeof marked !== 'undefined') {
    return marked.parse(text);
  }
  return escHtml(text).replace(/\n/g, '<br>');
}

// ── Session management ────────────────────────────────────────────────────────

function loadSession(sid) {
  if (currentSession === sid && !document.getElementById('messages').innerHTML.trim()) {
      // already there, nothing to do unless page was empty
  }
  currentSession = sid;
  document.getElementById('current-session').value = sid;
  
  // Reset working state UI locally — backend status will re-sync via WS
  setWorking(false);

  // Highlight active session
  document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
  const el = document.getElementById('session-' + sid);
  if (el) el.classList.add('active');

  // Load history
  fetch('/history/' + encodeURIComponent(sid))
    .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
    .then(data => {
      if (currentSession !== sid) return;

      const box = document.getElementById('messages');
      const historyHtml = (data.messages || []).map(m =>
        `<div class="message ${m.role}"><div class="bubble">${renderMessage(m.content)}</div></div>`
      ).join('');
      
      box.innerHTML = historyHtml + '<div id="ws-container"></div>';
      scrollBottom();
      
      // Connect WebSocket for live updates AFTER DOM is ready
      connectWs(sid);
    })
    .catch(err => {
        console.error("History load error:", err);
        // Ensure we still have a WS container even if history fails
        const box = document.getElementById('messages');
        if (!document.getElementById('ws-container')) {
            box.innerHTML += '<div id="ws-container"></div>';
        }
        connectWs(sid);
    });
}

function connectWs(sid) {
  if (currentWs) {
      if (currentWs._sid === sid && currentWs.readyState === WebSocket.OPEN) return;
      currentWs.onclose = null;
      currentWs.close(); 
      currentWs = null; 
  }
  
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${protocol}//${location.host}/ws/${encodeURIComponent(sid)}`);
  ws._sid = sid;

  ws.onmessage = e => {
    let msg = null;
    try { msg = JSON.parse(e.data); } catch {}
    if (!msg) return;

    if (msg.session_id && msg.session_id !== currentSession) return;

    const wsNode = document.getElementById('ws-container');
    if (!wsNode) return;

    if (msg.type === 'working') {
        setWorking(true);
        wsNode.innerHTML = `<div class="spinner-row"><div class="spinner"></div><span class="spinner-text">Agent is working…</span></div>`;
        scrollBottom();
    } else if (msg.type === 'done') {
        setWorking(false);
        wsNode.innerHTML = ''; 
        loadSession(sid); // Reload to show new assistant bubble
    } else if (msg.type === 'error') {
        setWorking(false);
        wsNode.innerHTML = `<span style="color:red">Error: ${msg.error}</span>`;
    }
  };
  
  ws.onclose = () => {
    if (currentSession === sid) {
        setWorking(false); // Clear lock if backend vanishes
        currentWs = null;
    }
  };
  currentWs = ws;
}

// Called by WS fragment script tag when agent finishes
window.__agentDone = function(sid) {
  if (currentSession !== sid) return;
  setWorking(false);
  // Reload fresh history to show the saved assistant message
  loadSession(sid);
};

// ── Send message ──────────────────────────────────────────────────────────────

function sendMessage(e) {
  e.preventDefault();
  const input   = document.getElementById('msg-input');
  const prompt  = input.value.trim();
  if (!prompt || isWorking) return;

  const sid      = document.getElementById('current-session').value;
  const search   = document.getElementById('use-search').checked;
  const provider = document.getElementById('provider-select').value;
  const filePath = document.getElementById('pending-file-path').value;

  // Reconnect if connection dropped while idle
  if (!currentWs || currentWs.readyState !== WebSocket.OPEN) {
      connectWs(sid);
  }

  // Show user bubble immediately (use DOM API to preserve ws-container)
  const msgs = document.getElementById('messages');
  const fileName = document.getElementById('pending-file-name')?.value || '';
  const display = fileName ? `📎 \`${fileName}\` — ${prompt}` : prompt;
  const bubbleDiv = document.createElement('div');
  bubbleDiv.className = 'message user';
  bubbleDiv.innerHTML = `<div class="bubble">${renderMessage(display)}</div>`;
  const wsTarget = document.getElementById('ws-container');
  msgs.insertBefore(bubbleDiv, wsTarget);
  scrollBottom();

  // Build final prompt with file context if attached
  let finalPrompt = prompt;
  if (filePath) {
    const ext = filePath.split('.').pop().toLowerCase();
    const ftype = ext === 'pdf' ? 'PDF' : 'image';
    finalPrompt = `[Uploaded ${ftype}: \`${fileName}\` saved at \`${filePath}\`]\nUse run_skill('pdf_ocr_nvidia') to extract text first, then answer:\n\n${prompt}`;
  }

  const form = new FormData();
  form.append('prompt', finalPrompt);
  form.append('use_search', search);
  form.append('provider_override', provider);

  setWorking(true);
  input.value = '';
  input.style.height = 'auto';   // ← Reset height after manual clear
  clearFile();

  // Ensure WS is connected (async, non-blocking — don't wait for it)
  if (!currentWs || 
      currentWs.readyState === WebSocket.CLOSED || 
      currentWs.readyState === WebSocket.CLOSING) {
      connectWs(sid);
  }

  // POST immediately — WS will be ready long before the agent finishes
  fetch(`/chat/${encodeURIComponent(sid)}`, { method: 'POST', body: form })
    .catch(err => {
        setWorking(false);
        const wsNode = document.getElementById('ws-container');
        if (wsNode) wsNode.innerHTML = 
          `<span style="color:red">Failed to send: ${err.message}</span>`;
    });
}

// ── Stop ──────────────────────────────────────────────────────────────────────

function stopAgent() {
  const sid = document.getElementById('current-session').value;
  fetch(`/stop/${sid}`, { method: 'POST' });
  document.getElementById('stop-btn').textContent = '⏹ Stopping…';
}

// ── New chat ──────────────────────────────────────────────────────────────────

function newChat() {
  const now = new Date();
  const pad = n => String(n).padStart(2,'0');
  const sid = `session_${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  document.getElementById('messages').innerHTML = '<div id="ws-container"></div>';
  document.getElementById('chat-title').textContent = 'Ask me anything';
  document.getElementById('current-session').value = sid;
  currentSession = sid;
  setWorking(false);
  connectWs(sid);

  // Add to sidebar
  const list = document.getElementById('session-list');
  const div  = document.createElement('div');
  div.className = 'session-item active';
  div.id = 'session-' + sid;
  div.onclick = () => loadSession(sid);
  div.innerHTML = `<span class="session-label">New chat</span>
    <button class="btn-icon" onclick="event.stopPropagation();deleteSession('${sid}',this.parentElement)">🗑</button>`;
  document.querySelectorAll('.session-item').forEach(e => e.classList.remove('active'));
  list.prepend(div);
}

// ── File upload ───────────────────────────────────────────────────────────────

function toggleUpload() {
  document.getElementById('upload-popup').classList.toggle('hidden');
}

function handleFileSelect(input) {
  const file = input.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  fetch('/upload', { method: 'POST', body: form })
    .then(r => r.json())
    .then(data => {
      document.getElementById('pending-file-path').value = data.path;
      document.getElementById('pending-file-name').value = data.filename;
      document.getElementById('file-badge-name').textContent = '📎 ' + data.filename;
      document.getElementById('file-badge').classList.remove('hidden');
      toggleUpload();
    });
}

function clearFile() {
  document.getElementById('pending-file-path').value = '';
  document.getElementById('pending-file-name').value = '';
  document.getElementById('file-badge').classList.add('hidden');
  document.getElementById('file-input').value = '';
}

// ── Misc ──────────────────────────────────────────────────────────────────────

function closeBrowser() {
  fetch('/stop/' + document.getElementById('current-session').value, { method: 'POST' });
  fetch('/browser/close', { method: 'POST' }).catch(() => {});
}

function setWorking(v) {
  isWorking = v;
  document.getElementById('stop-row').classList.toggle('hidden', !v);
  document.getElementById('send-btn').disabled = v;
  document.getElementById('msg-input').placeholder = v ? 'Agent is working…' : 'Message…';
  if (v) document.getElementById('stop-btn').textContent = '⏹ Stop';
}

function scrollBottom() {
  requestAnimationFrame(() => {
    const el = document.getElementById('messages');
    el.scrollTop = el.scrollHeight;
  });
}

function escHtml(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function deleteSession(sid, el) {
  if (!confirm('Delete this chat?')) return;
  fetch('/sessions/' + sid, { method: 'DELETE' }).then(() => el.remove());
}

// Keyboard: Enter sends, Shift+Enter = newline (not applicable for input, kept for reference)
document.addEventListener('DOMContentLoaded', () => {
  const inp = document.getElementById('msg-input');

  // existing Enter-to-send handler
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      document.getElementById('chat-form').dispatchEvent(new Event('submit'));
    }
  });

  // NEW: auto-grow textarea as user types
  inp.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 200) + 'px';
  });

  document.getElementById('stop-btn').addEventListener('click', stopAgent);

  // Close upload popup when clicking outside
  document.addEventListener('click', e => {
    const popup = document.getElementById('upload-popup');
    const wrap  = document.querySelector('.attach-wrap');
    if (popup && !popup.classList.contains('hidden') && !wrap.contains(e.target)) {
      popup.classList.add('hidden');
    }
  });
});
