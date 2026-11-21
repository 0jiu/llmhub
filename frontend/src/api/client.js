import axios from 'axios'
import router from '../router'

/**
 * API 基址按环境自适应：
 * - dev：'/api' 交给 Vite 代理转发到 8000（绕开 CORS）
 * - 生产：空串——前后端同源（后端直接托管 dist），直接请求相对路径
 */
export const API_BASE = import.meta.env.DEV ? '/api' : ''

/** axios 实例：自动携带 JWT */
const client = axios.create({ baseURL: API_BASE, timeout: 120000 })

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('llmhub_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

client.interceptors.response.use(
  (resp) => resp,
  (err) => {
    // 401：登录过期 → 清态跳登录页
    if (err.response?.status === 401 && router.currentRoute.value.name !== 'login') {
      localStorage.removeItem('llmhub_token')
      router.push({ name: 'login' })
    }
    return Promise.reject(err)
  },
)

/** 后端 detail 可能是字符串也可能是 422 校验数组，统一取可读文本 */
export function errMsg(err) {
  const d = err.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x) => `${x.loc?.join('.')}: ${x.msg}`).join('; ')
  return err.message || '请求失败'
}

/**
 * Agent 流式问答（SSE）。axios 基于 XMLHttpRequest，拿不到增量数据，
 * 所以这里用原生 fetch + ReadableStream 逐块读取，手动解析 SSE 帧。
 *
 * SSE 帧格式（HTTP 纯文本流）：
 *   event: delta\n
 *   data: {"text": "你好"}\n
 *   \n            ← 空行表示一帧结束
 * 网络分片不一定按帧边界到达，所以用 buf 缓冲、凑满完整帧再解析。
 *
 * handlers: { meta, delta, tool, final, error } 各事件回调
 */
export async function streamAgentChat(payload, handlers, signal) {
  const token = localStorage.getItem('llmhub_token')
  const resp = await fetch(`${API_BASE}/agent/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
    signal,
  })
  if (resp.status === 401 && router.currentRoute.value.name !== 'login') {
    localStorage.removeItem('llmhub_token')
    router.push({ name: 'login' })
    throw new Error('登录已过期，请重新登录')
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try { const j = await resp.json(); if (j.detail) detail = j.detail } catch { /* 保留默认 */ }
    throw new Error(detail)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      let data = ''
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      if (!data) continue
      try { handlers[event]?.(JSON.parse(data)) } catch { /* 忽略坏帧 */ }
    }
  }
}

export default client
