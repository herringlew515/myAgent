// 浏览器逻辑单元测试：模拟 DOM/网络事件，不替代真机布局验收。
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.value = ''; this.hidden = false;
    this.disabled = false; this.textContent = ''; this.listeners = {}; this.parent = null;
    this.scrollHeight = 1000; this.scrollTop = 1000; this.clientHeight = 500;
    this.classList = {add(){}, remove(){}, toggle(){}};
    this.style = {setProperty(){}};
  }
  append(...elements) { elements.forEach(e => { e.parent = this; this.children.push(e); }); }
  prepend(element) { element.parent = this; this.children.unshift(element); }
  replaceChildren(...elements) { this.children.forEach(e => e.parent = null); this.children = []; this.append(...elements); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(e => e !== this); this.parent = null; }
  get isConnected() { return !!this.parent; }
  addEventListener(event, callback) { this.listeners[event] = callback; }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  querySelector() { return null; }
  focus() {}
  click() { return this.onclick?.(); }
  getBoundingClientRect() { return {top:0}; }
}

async function settle() { await new Promise(resolve => setImmediate(resolve)); }
function setup(saved = null) {
  const elements = new Map();
  const aside = new Element(), body = new Element();
  const storage = () => { const values = new Map(); return {getItem:k=>values.get(k) || null, setItem:(k,v)=>values.set(k,v), removeItem:k=>values.delete(k)}; };
  const sessionStorage = storage(), localStorage = storage();
  if (saved) localStorage.setItem('ai-group-session', JSON.stringify(saved));
  class Socket {
    static OPEN = 1;
    static all = [];
    constructor(url) { this.url = url; this.readyState = 1; this.sent = []; Socket.all.push(this); }
    send(raw) { this.sent.push(JSON.parse(raw)); }
    close() { this.onclose?.({code:1000}); }
    event(value) { this.onmessage({data:JSON.stringify(value)}); }
  }
  const rooms = [{id:1,name:'群一'}, {id:2,name:'群二'}];
  const context = vm.createContext({
    document: {
      getElementById(id) { if (!elements.has(id)) elements.set(id,new Element()); return elements.get(id); },
      createElement: tag => new Element(tag), querySelector: () => aside, body, documentElement: new Element(),
    },
    window: {innerHeight:800, addEventListener(){}},
    location: {protocol:'http:',host:'localhost:8000'}, sessionStorage, localStorage,
    WebSocket: Socket, URL: {createObjectURL:()=> 'blob:test', revokeObjectURL(){}},
    matchMedia: () => ({matches:false}), requestAnimationFrame:()=>1, cancelAnimationFrame(){},
    setTimeout:()=>1, clearTimeout(){}, console,
    fetch: async (path, options) => {
      let data;
      if (path === '/api/sessions') data = {id:'me',nickname:'小王',token:'test-token'};
      else if (path === '/api/rooms' && options.method === 'POST') data = rooms.find(r=>r.name === JSON.parse(options.body).name);
      else if (path === '/api/rooms') data = rooms;
      else throw Error(`Unexpected API ${path}`);
      return {ok:true,json:async()=>data};
    },
  });
  vm.runInContext(fs.readFileSync('static/app.js','utf8'), context);
  const el = id => context.document.getElementById(id);
  return {context,el,Socket,localStorage,sessionStorage,run:code=>vm.runInContext(code,context)};
}
const message = (id, content, extra={}) => ({id,room_id:1,content,sender_id:'other',nickname:'小李',role:'user',created_at:'2026-09-17T10:00:00Z',...extra});
async function joined() {
  const app = setup(); app.el('nickname').value = '小王'; app.el('room-name').value = '群一';
  await app.run('join()');
  const socket = app.Socket.all.at(-1);
  socket.onopen(); socket.event({type:'history',messages:[]});
  return {...app,socket};
}

test('引用消息生成正确发送包，取消引用不影响其他消息', async () => {
  const app = await joined();
  app.socket.event({type:'message',message:message(1,'原始消息')});
  const entry = app.el('messages').children[0];
  entry.children[0].children.at(-1).onclick();
  assert.equal(app.el('quote-preview').hidden,false);
  assert.match(app.el('quote-text').textContent,/原始消息/);
  app.el('message').value = '回复内容';
  app.el('message-form').listeners.submit({preventDefault(){}});
  assert.deepEqual(app.socket.sent.at(-1),{content:'回复内容',reply_to:1,image_id:null});
  assert.equal(app.el('quote-preview').hidden,true);
  app.socket.event({type:'message',message:message(2,'回复内容',{reply_to:1,quote:{nickname:'小李',content:'原始消息'}})});
  assert.equal(app.el('messages').children[1].children[1].textContent,'小李：原始消息');
});

test('切群保留身份，旧连接事件不能污染新群', async () => {
  const app = await joined();
  await app.run('join("群二")');
  const current = app.Socket.all.at(-1); current.onopen();
  current.event({type:'history',messages:[]});
  app.socket.event({type:'message',message:message(99,'旧群迟到消息')});
  assert.equal(app.el('messages').children.length,0);
  assert.equal(current.sent[0].token,'test-token');
  assert.equal(JSON.parse(app.localStorage.getItem('ai-group-session')).roomName,'群二');
});

test('关闭标签页后可从持久身份恢复群列表和上次群名', async () => {
  const app = setup({id:'me',nickname:'小王',token:'persisted-token',roomName:'群二'});
  await settle();
  assert.equal(app.Socket.all.at(-1).url,'ws://localhost:8000/ws/rooms/2');
  assert.equal(app.el('room-select').children.length,2);
  assert.equal(app.el('nickname').value,'小王');
});

test('切群时未完成的图片上传不会附加到新群', async () => {
  const app = await joined();
  let finish;
  const originalFetch = app.context.fetch;
  app.context.fetch = (url,options) => url.endsWith('/images')
    ? new Promise(resolve => { finish = resolve; }) : originalFetch(url,options);
  app.el('image-file').files = [{size:100}];
  const upload = app.el('image-file').listeners.change();
  await app.run('join("群二")');
  finish({ok:true,json:async()=>({id:'a'.repeat(32)})}); await upload;
  assert.equal(app.run('attachment'),null);
  assert.equal(app.el('image-preview').hidden,true);
});
