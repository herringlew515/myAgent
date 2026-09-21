"use strict";
const $ = (id) => document.getElementById(id);
const key = "ai-group-session";
let session = null, room = null, socket = null, retryTimer = null;
let seen = new Map(), pending = new Set(), quote = null, attachment = null;
let selectedURL = null, uploadVersion = 0, viewVersion = 0, joining = false, ready = false;
let imageURLs = new Set();
try { session = JSON.parse(sessionStorage.getItem(key) || localStorage.getItem(key)); } catch { /* fresh identity */ }

function persist() {
  try {
    if (session) {
      sessionStorage.setItem(key, JSON.stringify(session));
      localStorage.setItem(key, JSON.stringify(session));
    } else {
      sessionStorage.removeItem(key); localStorage.removeItem(key);
    }
  } catch { notice("浏览器禁用了本地存储，关闭页面后可能需要重新进入。"); }
}
function notice(text) { $("notice").textContent = text; }
function connected(value, label) {
  ready = value;
  $("connection").textContent = label;
  $("connection").classList.toggle("online", value);
  $("message").disabled = $("send").disabled = $("attach").disabled = !value;
}
async function api(path, body, method = body === undefined ? "GET" : "POST") {
  const response = await fetch(path, {
    method, headers: {"Content-Type": "application/json", ...(session ? {Authorization: `Bearer ${session.token}`} : {})},
    ...(body !== undefined ? {body: JSON.stringify(body)} : {}),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) { session = null; persist(); }
    throw new Error(typeof data.detail === "string" ? data.detail : "请检查填写的内容。");
  }
  return data;
}
function atBottom() {
  const list = $("messages"); return list.scrollHeight - list.scrollTop - list.clientHeight < 90;
}
function scrollBottom() { $("messages").scrollTop = $("messages").scrollHeight; }
function setQuote(message) {
  quote = message;
  $("quote-preview").hidden = !message;
  $("quote-text").textContent = message ? `引用 ${message.nickname}：${message.content.slice(0,180) || "[图片]"}` : "";
}
function clearImage() {
  uploadVersion += 1; attachment = null;
  if (selectedURL) URL.revokeObjectURL(selectedURL);
  selectedURL = null; $("selected-image").removeAttribute("src");
  $("image-file").value = ""; $("image-preview").hidden = true;
}
function clearView() {
  viewVersion += 1;
  imageURLs.forEach((url) => URL.revokeObjectURL(url)); imageURLs.clear();
  seen.clear(); pending.clear(); $("messages").replaceChildren();
}
async function loadImage(img, message, version) {
  try {
    const response = await fetch(`/api/rooms/${message.room_id}/images/${message.image_id}`, {
      headers: {Authorization: `Bearer ${session.token}`},
    });
    if (!response.ok) throw new Error("image unavailable");
    const blob = await response.blob();
    if (version !== viewVersion || !img.isConnected) return;
    const url = URL.createObjectURL(blob); imageURLs.add(url);
    img.onload = () => { if (atBottom()) scrollBottom(); };
    img.src = url;
  } catch { if (version === viewVersion) img.alt = "图片加载失败，请重新进入群聊重试"; }
}
function renderMessage(message, prepend = false) {
  if (seen.has(message.id)) return;
  seen.set(message.id, message);
  $("messages").querySelector(".empty")?.remove();
  const follow = atBottom();
  const entry = document.createElement("article"); entry.id = `message-${message.id}`;
  entry.className = `message ${message.role}${message.sender_id === session.id ? " mine" : ""}`;
  const meta = document.createElement("div"); meta.className = "meta";
  const name = document.createElement("span"); name.textContent = message.role === "system" ? "系统提示" : message.nickname;
  const time = document.createElement("time"); time.dateTime = message.created_at;
  time.textContent = new Date(message.created_at).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit"});
  meta.append(name, time);
  if (message.role === "assistant") {
    const badge = document.createElement("span"); badge.className = "role-tag"; badge.textContent = "AI 助手"; meta.append(badge);
  }
  const reply = document.createElement("button"); reply.type = "button"; reply.className = "quote-action";
  reply.textContent = "引用"; reply.setAttribute("aria-label", `引用 ${message.nickname} 的消息`);
  reply.onclick = () => { setQuote(message); $("message").focus({preventScroll:true}); };
  meta.append(reply); entry.append(meta);
  if (message.quote) {
    const reference = document.createElement("button"); reference.type = "button"; reference.className = "quoted-message";
    reference.textContent = `${message.quote.nickname}：${message.quote.content || "[图片]"}`;
    reference.onclick = () => {
      const target = $(`message-${message.reply_to}`);
      if (!target) { notice("原消息在更早的记录中，可点击「加载更早消息」。"); return; }
      const list = $("messages");
      list.scrollTop += target.getBoundingClientRect().top - list.getBoundingClientRect().top - 12;
      target.classList.add("highlight"); setTimeout(() => target.classList.remove("highlight"), 1200);
    };
    entry.append(reference);
  }
  if (message.content) {
    const bubble = document.createElement("div"); bubble.className = "bubble"; bubble.textContent = message.content; entry.append(bubble);
  }
  if (message.image_id) {
    const img = document.createElement("img"); img.className = "chat-image"; img.alt = "群聊图片";
    img.loading = "lazy"; entry.append(img);
    img.onclick = () => {
      if (!img.src) return;
      const dialog = document.createElement("dialog"); dialog.className = "image-dialog";
      const full = document.createElement("img"); full.src = img.src; full.alt = "图片预览";
      const close = document.createElement("button"); close.textContent = "关闭";
      close.onclick = () => dialog.close(); dialog.append(full, close);
      dialog.onclose = () => dialog.remove(); document.body.append(dialog); dialog.showModal();
    };
    if (prepend) $("messages").prepend(entry); else $("messages").append(entry);
    loadImage(img, message, viewVersion);
  } else if (prepend) $("messages").prepend(entry); else $("messages").append(entry);
  if (message.reply_to && message.role !== "user") pending.delete(message.reply_to);
  if (!prepend && (follow || message.sender_id === session.id)) scrollBottom();
}
function disconnect() {
  clearTimeout(retryTimer); retryTimer = null;
  const old = socket; socket = null; old?.close();
  connected(false, "未连接");
}
function openSocket() {
  if (!room || !session) return;
  connected(false, "连接中");
  const current = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/rooms/${room.id}`);
  socket = current;
  current.onopen = () => { if (socket === current) current.send(JSON.stringify({token: session.token})); };
  current.onmessage = (event) => {
    if (socket !== current) return;
    const data = JSON.parse(event.data);
    if (data.type === "history") {
      clearView(); data.messages.forEach((message) => renderMessage(message)); scrollBottom();
      $("older").hidden = data.messages.length < 100;
      connected(true, "已连接"); notice("");
    } else if (data.type === "message") {
      renderMessage(data.message);
      if (data.message.role !== "user") notice(pending.size ? "AI 正在处理群内提问…" : "");
    } else if (data.type === "ai_status") {
      if (data.status === "done") pending.delete(data.reply_to); else pending.add(data.reply_to);
      notice(pending.size ? `AI 正在处理群内提问（${pending.size} 条）…` : "");
    } else if (data.type === "error") notice(data.detail);
  };
  current.onclose = (event) => {
    if (socket !== current) return;
    connected(false, "已断开");
    if (event.code === 1008) { notice("会话不可用，请更换身份后重新进入。"); return; }
    notice("连接中断，正在重连。未发送的文字会保留。");
    retryTimer = setTimeout(openSocket, 2000);
  };
  current.onerror = () => current.close();
}
async function refreshRooms() {
  const rooms = await api("/api/rooms");
  $("room-select").replaceChildren();
  rooms.forEach((item) => {
    const option = document.createElement("option"); option.value = item.name; option.textContent = item.name;
    option.selected = item.id === room?.id; $("room-select").append(option);
  });
}
async function join(name = $("room-name").value.trim()) {
  if (joining) return;
  joining = true; $("join-button").disabled = $("room-select").disabled = true;
  try {
    const nickname = $("nickname").value.trim();
    if (!nickname || !name) throw new Error("请填写昵称和群名。");
    if (!session) { session = await api("/api/sessions", {nickname}); persist(); }
    const nextRoom = await api("/api/rooms", {name});
    disconnect(); clearView(); setQuote(null); clearImage(); $("message").value = "";
    room = nextRoom; session.roomName = room.name; persist();
    $("nickname").value = session.nickname; $("room-name").value = room.name;
    $("nickname").disabled = true; $("join-form").hidden = true;
    $("room-controls").hidden = false; document.querySelector("aside").classList.add("joined");
    $("room-title").textContent = room.name; openSocket();
    await refreshRooms();
  } catch (error) { notice(error.message); }
  finally { joining = false; $("join-button").disabled = $("room-select").disabled = false; }
}
$("join-form").addEventListener("submit", (event) => { event.preventDefault(); join(); });
$("room-select").addEventListener("change", () => join($("room-select").value));
$("switch-room").addEventListener("click", () => {
  $("join-form").hidden = !$("join-form").hidden;
  if (!$("join-form").hidden) { $("room-name").value = ""; $("room-name").focus({preventScroll:true}); }
});
$("leave").addEventListener("click", () => {
  if (joining) return;
  disconnect(); clearView(); setQuote(null); clearImage();
  session = null; room = null; persist();
  $("nickname").disabled = false; $("join-form").hidden = false; $("room-controls").hidden = true;
  document.querySelector("aside").classList.remove("joined"); $("message").value = "";
  $("older").hidden = true; $("room-title").textContent = "等你加入这场对话"; notice("");
});
$("older").addEventListener("click", async () => {
  const version = viewVersion; const before = Math.min(...seen.keys()); $("older").disabled = true;
  try {
    const messages = await api(`/api/rooms/${room.id}/messages?before=${before}`);
    if (version !== viewVersion) return;
    const list = $("messages"), oldHeight = list.scrollHeight, oldTop = list.scrollTop;
    [...messages].reverse().forEach((message) => renderMessage(message, true));
    list.scrollTop = oldTop + list.scrollHeight - oldHeight;
    $("older").hidden = messages.length < 100;
  } catch (error) { if (version === viewVersion) notice(error.message); }
  finally { $("older").disabled = false; }
});
$("cancel-quote").onclick = () => setQuote(null);
$("cancel-image").onclick = clearImage;
$("attach").onclick = () => $("image-file").click();
$("image-file").addEventListener("change", async () => {
  const file = $("image-file").files[0]; if (!file || !room) return;
  clearImage();
  if (file.size > 5 * 1024 * 1024) { notice("图片不能超过 5 MB。"); return; }
  const version = uploadVersion, targetRoom = room.id;
  selectedURL = URL.createObjectURL(file); $("selected-image").src = selectedURL;
  $("image-preview").hidden = false; $("image-status").textContent = "上传中…";
  try {
    const response = await fetch(`/api/rooms/${targetRoom}/images`, {
      method:"POST", headers:{Authorization:`Bearer ${session.token}`, "Content-Type":"application/octet-stream"}, body:file,
    });
    const data = await response.json();
    if (version !== uploadVersion) return;
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "图片上传失败。");
    attachment = data.id; $("image-status").textContent = "已准备好，点击发送";
  } catch (error) { if (version === uploadVersion) { clearImage(); notice(error.message); } }
});
$("message-form").addEventListener("submit", (event) => {
  event.preventDefault(); const content = $("message").value.trim();
  if (selectedURL && !attachment) { notice("请等待图片上传完成，或取消图片。"); return; }
  if ((!content && !attachment) || !ready || socket?.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({content, reply_to:quote?.id || null, image_id:attachment}));
  $("message").value = ""; setQuote(null); clearImage(); $("message").focus({preventScroll:true});
});
$("message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !matchMedia("(pointer: coarse)").matches) {
    event.preventDefault(); $("message-form").requestSubmit();
  }
});
// 保留键盘正常弹出和主动缩放，只调整应用容器的可见高度。
let viewportFrame = null;
function fitViewport() {
  cancelAnimationFrame(viewportFrame);
  viewportFrame = requestAnimationFrame(() => {
    const viewport = window.visualViewport;
    if (viewport && viewport.scale !== 1) return;
    const follow = atBottom();
    document.documentElement.style.setProperty("--visible-height", `${viewport?.height || window.innerHeight}px`);
    document.body.classList.toggle("keyboard-open", !!viewport && window.innerHeight - viewport.height > 140);
    if (follow) scrollBottom();
  });
}
window.visualViewport?.addEventListener("resize", fitViewport);
window.addEventListener("resize", fitViewport); fitViewport();
if (session?.token && session?.roomName) {
  $("nickname").value = session.nickname; $("room-name").value = session.roomName; join();
}
