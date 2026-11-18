// Small native CDP transport for the intake acceptance script; no browser dependencies.
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export async function stopChild(child) {
  if (!child || !child.pid || child.exitCode !== null) return;
  await new Promise(resolve => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); }, 10000);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
    child.kill('SIGTERM');
  });
}

export async function launchChrome() {
  const profile = await mkdtemp(join(tmpdir(), 'intake-chrome-'));
  const child = spawn('/usr/bin/google-chrome', [
    '--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--no-first-run',
    '--no-default-browser-check', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  let client;
  const close = async () => {
    client?.close();
    await stopChild(child);
    await rm(profile, { recursive: true, force: true });
  };
  try {
    const endpoint = await new Promise((resolve, reject) => {
      let output = '';
      const timeout = setTimeout(() => reject(new Error('Chrome readiness timeout')), 15000);
      child.once('error', () => { clearTimeout(timeout); reject(new Error('Chrome launch failed')); });
      child.once('exit', () => { clearTimeout(timeout); reject(new Error('Chrome exited before readiness')); });
      child.stderr.on('data', chunk => {
        output += chunk.toString();
        const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/);
        if (match) { clearTimeout(timeout); resolve(match[1]); }
      });
    });
    client = await CDP.connect(endpoint);
    const { targetId } = await client.send('Target.createTarget', { url: 'about:blank' }, null);
    const { sessionId } = await client.send('Target.attachToTarget', { targetId, flatten: true }, null);
    client.session = sessionId;
    await client.send('Page.enable');
    await client.send('Runtime.enable');
    await client.send('Network.enable');
    return { client, close };
  } catch (error) { await close(); throw error; }
}

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.next = 0;
    this.pending = new Map();
    this.listeners = new Map();
    this.session = null;
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        clearTimeout(pending.timer);
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(`CDP ${pending.method} failed`));
        else pending.resolve(message.result);
      } else if (!message.sessionId || message.sessionId === this.session) {
        for (const listener of this.listeners.get(message.method) ?? []) listener(message.params);
      }
    });
  }
  static async connect(endpoint) {
    const socket = new WebSocket(endpoint);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', () => reject(new Error('CDP connection failed')), { once: true });
    });
    return new CDP(socket);
  }
  send(method, params = {}, sessionId = this.session) {
    const id = ++this.next;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP ${method} timeout`)); }, 20000);
      this.pending.set(id, { resolve, reject, timer, method });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }
  on(method, listener) {
    const listeners = this.listeners.get(method) ?? new Set();
    listeners.add(listener);
    this.listeners.set(method, listeners);
    return () => listeners.delete(listener);
  }
  async evaluate(expression) {
    const result = await this.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error('Browser evaluation failed (values withheld)');
    return result.result.value;
  }
  async wait(expression, timeout = 15000) {
    const result = await this.evaluate(`new Promise(resolve => {
      const check = () => { if (${expression}) { finish(true); } };
      const observer = new MutationObserver(check);
      let timer;
      const finish = value => { observer.disconnect(); clearTimeout(timer); resolve(value); };
      observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
      timer = setTimeout(() => finish(false), ${timeout});
      check();
    })`);
    if (!result) throw new Error('Browser DOM condition timed out');
  }
  async navigate(url) {
    const loaded = new Promise(resolve => {
      const off = this.on('Page.loadEventFired', () => { clearTimeout(timer); off(); resolve(); });
      const timer = setTimeout(() => { off(); resolve(); }, 10000);
    });
    await this.send('Page.navigate', { url });
    await loaded;
  }
  async setFile(selector, path) {
    const { root } = await this.send('DOM.getDocument');
    const { nodeId } = await this.send('DOM.querySelector', { nodeId: root.nodeId, selector });
    if (!nodeId) throw new Error('File input missing');
    await this.send('DOM.setFileInputFiles', { nodeId, files: [path] });
  }
  close() { this.socket.close(); }
}
