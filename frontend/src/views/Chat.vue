<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ChatDotRound, Check, Delete, Plus, SwitchButton } from '@element-plus/icons-vue'
import client, { streamAgentChat } from '../api/client'
import { useAuthStore } from '../stores/auth'
import MarkdownIt from 'markdown-it'

// markdown 渲染：html:false 转义内嵌 HTML（防 XSS），linkify 自动识别链接
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
function renderMd(text) {
  return md.render(text || '')
}

const router = useRouter()
const auth = useAuthStore()

// ---------------- 会话侧栏 ----------------
const conversations = ref([])
const currentConvId = ref(null)
const kbs = ref([])
const selectedKbId = ref(null)

const currentTitle = computed(() => {
  const c = conversations.value.find((x) => x.id === currentConvId.value)
  return c ? c.title : '新对话'
})

async function loadConversations() {
  const { data } = await client.get('/conversations')
  conversations.value = data
}

async function loadKbs() {
  try {
    const { data } = await client.get('/kbs')
    kbs.value = data
  } catch { /* 知识库加载失败不阻塞聊天 */ }
}

async function newConversation() {
  currentConvId.value = null   // 不带 conversation_id 发消息时后端自动建
  messages.value = []
}

async function selectConversation(conv) {
  currentConvId.value = conv.id
  selectedKbId.value = conv.kb_id
  const { data } = await client.get(`/conversations/${conv.id}/messages`)
  messages.value = data.map((m) => ({
    role: m.role, content: m.content, tool_trace: parseTrace(m.tool_trace),
  }))
  await scrollBottom()
}

async function deleteConversation(conv) {
  await ElMessageBox.confirm(`删除会话「${conv.title}」？`, '提示', { type: 'warning' })
  await client.delete(`/conversations/${conv.id}`)
  if (currentConvId.value === conv.id) { currentConvId.value = null; messages.value = [] }
  await loadConversations()
  ElMessage.success('已删除')
}

function parseTrace(t) {
  if (!t) return []
  if (Array.isArray(t)) return t
  try { return JSON.parse(t) } catch { return [] }
}

// ---------------- 聊天 ----------------
const messages = ref([])
const input = ref('')
const sending = ref(false)
const scrollRef = ref(null)

async function scrollBottom() {
  await nextTick()
  if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
}

async function send() {
  const question = input.value.trim()
  if (!question || sending.value) return
  sending.value = true
  input.value = ''
  messages.value.push({ role: 'user', content: question, tool_trace: [] })
  // 占位的 assistant 气泡：流式期间 content 随 delta 事件逐字追加（打字机）
  const placeholder = reactive({ role: 'assistant', content: '', tool_trace: [], loading: true, streaming: false })
  messages.value.push(placeholder)
  await scrollBottom()
  try {
    const payload = {
      question,
      model: selectedModel.value,          // 用户主动选择的主力模型
      // 降级链：主力建连/首包失败时自动切其余模型（glm-4-flash 免费）
      model_chain: modelChain.value,
    }
    if (currentConvId.value) payload.conversation_id = currentConvId.value
    if (selectedKbId.value) payload.kb_id = selectedKbId.value

    await streamAgentChat(payload, {
      // 新会话：拿到 id 后刷新侧栏，后续消息走续聊
      meta: (d) => {
        if (!currentConvId.value) {
          currentConvId.value = d.conversation_id
          loadConversations()
        }
      },
      // 增量文本：追加即打字机效果
      delta: (d) => {
        placeholder.streaming = true
        placeholder.content += d.text
        scrollBottom()
      },
      // 工具刚执行完：轨迹实时上屏
      tool: (d) => {
        placeholder.tool_trace.push({
          tool: d.tool, arguments: d.arguments, ok: d.ok, result_preview: d.result_preview,
        })
        scrollBottom()
      },
      final: () => { /* 收尾统一在 finally 处理 */ },
      error: (d) => {
        placeholder.content += (placeholder.content ? '\n\n' : '') + `⚠️ ${d.detail}`
      },
    })
    placeholder.loading = false
    placeholder.streaming = false
  } catch (e) {
    placeholder.content = placeholder.content
      ? placeholder.content + `\n\n⚠️ 请求失败：${e.message}`
      : `⚠️ 请求失败：${e.message}`
    placeholder.loading = false
    placeholder.streaming = false
  } finally {
    sending.value = false
    await scrollBottom()
  }
}

const welcomeHint = computed(() => messages.value.length === 0)

// ---------------- 模型选择器 ----------------
// 展示元数据（本地维护）；可用列表以 GET /v1/models 返回为准，
// 后端 provider.py 注册新厂商后这里自动出现
const MODEL_META = {
  'deepseek-chat': { label: 'DeepSeek Chat', tag: '主力', desc: '推理强 · 价格低' },
  'glm-4-flash':   { label: 'GLM-4-Flash',  tag: '免费', desc: '智谱 · 完全免费' },
  'qwen-turbo':    { label: 'Qwen-Turbo',   tag: '快速', desc: '阿里 · 便宜快速' },
  'qwen3-8b':      { label: 'Qwen3-8B',     tag: '免费', desc: '硅基流动 · 长期免费' },
  'qwen2.5-7b':    { label: 'Qwen2.5-7B',   tag: '免费', desc: '硅基流动 · 长期免费' },
  'glm-4-9b':      { label: 'GLM-4-9B',     tag: '免费', desc: '硅基流动 · 长期免费' },
}
const models = ref([])               // 后端实际可用的模型 id 列表
const selectedModel = ref('deepseek-chat')

async function loadModels() {
  try {
    const { data } = await client.get('/v1/models')
    // 只保留前端有展示元数据的模型，保持注册表与 UI 同步
    models.value = data.map((m) => m.id).filter((id) => MODEL_META[id])
  } catch { /* 拉取失败不影响聊天，选择器显示默认模型 */ }
}

// 降级链 = [用户选中的主力模型, ...其余模型]——主模型失败自动切备用
const modelChain = computed(() => {
  const rest = models.value.filter((m) => m !== selectedModel.value)
  return [selectedModel.value, ...rest]
})

function currentLabel(id) {
  return MODEL_META[id]?.label || id
}

// 空会话示例卡片：展示平台三类能力，点击即填入输入框
const samples = [
  { icon: '🧮', tag: '工具调用', text: '计算 (1024+768)*3-512 等于多少？再告诉我现在几点了。', hint: '自动调用计算器与时间工具' },
  { icon: '📚', tag: '知识库问答', text: '根据知识库里的资料，总结一下主要内容。', hint: '先选左侧知识库再发送' },
  { icon: '🧠', tag: '多轮记忆', text: '我最喜欢的数字是 88，请记住。', hint: '下一轮问我"乘以 3 等于多少"' },
]

function fillPrompt(text) {
  input.value = text
}
// 提示按钮弹出层：选中一条示例后自动收起
const hintOpen = ref(false)
function pickSample(text) {
  fillPrompt(text)
  hintOpen.value = false
}

onMounted(() => { loadConversations(); loadKbs(); loadModels() })
</script>

<template>
  <el-container style="height: 100%">
    <!-- 侧栏：深色，会话列表 -->
    <el-aside width="264px" class="sidebar">
      <div class="side-top">
        <div class="brand">
          <div class="brand-logo">L</div>
          <div class="brand-name">LLMHub</div>
        </div>
        <el-button class="new-btn" :icon="Plus" @click="newConversation">新建会话</el-button>
        <el-select v-model="selectedKbId" placeholder="🔗 关联知识库（可选）" clearable size="small" class="kb-select">
          <el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" />
        </el-select>
        <div class="nav-row">
          <div class="nav-btn" @click="router.push({ name: 'kb' })">📚 知识库</div>
          <div class="nav-btn" @click="router.push({ name: 'keys' })">🔑 API Key</div>
        </div>
      </div>
      <div class="conv-list">
        <div v-for="c in conversations" :key="c.id"
             class="conv-item" :class="{ active: c.id === currentConvId }"
             @click="selectConversation(c)">
          <el-icon style="margin-right:8px; font-size:14px; opacity:.7;"><ChatDotRound /></el-icon>
          <span class="conv-title" :title="c.title">{{ c.title }}</span>
          <el-icon class="conv-del" @click.stop="deleteConversation(c)"><Delete /></el-icon>
        </div>
        <div v-if="conversations.length === 0" class="conv-empty">还没有会话，发一条消息开始吧</div>
      </div>
      <div class="side-footer">
        <div class="avatar me" style="width:30px; height:30px; font-size:11px;">{{ auth.username.slice(0, 1).toUpperCase() }}</div>
        <span class="side-user">{{ auth.username }}</span>
        <el-button text size="small" class="logout-btn" :icon="SwitchButton"
                   @click="auth.logout(); router.push({ name: 'login' })">退出</el-button>
      </div>
    </el-aside>

    <!-- 主区：顶栏 + 消息流 + 输入。direction 必须显式指定：
         el-container 仅当子元素是 el-header/el-footer 时才自动纵向，
         否则默认横向——消息区和输入区会左右排（宽屏下输入框悬在右上） -->
    <el-container direction="vertical" class="main-area">
      <!-- 背景视频：铺满整个聊天区。video 要 muted + playsinline 才能自动循环播放；
           上层白色半透明遮罩保证消息可读性 -->
      <div class="bg-deco" aria-hidden="true">
        <video class="bg-video" src="/video/bg.mp4" autoplay muted loop playsinline></video>
        <div class="bg-overlay"></div>
      </div>
      <div class="chat-header">
        <span class="chat-title">{{ currentTitle }}</span>
        <!-- 模型选择器：点击徽章弹出下拉，选中谁谁就是主力模型 -->
        <el-dropdown trigger="click" class="model-picker" @command="(id) => (selectedModel = id)">
          <span class="model-badge">
            <span class="dot"></span>{{ currentLabel(selectedModel) }}
            <el-icon class="badge-caret"><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="m in models" :key="m" :command="m">
                <div class="model-option">
                  <div class="model-option-main">
                    <span class="model-option-name">{{ MODEL_META[m].label }}</span>
                    <span class="model-option-tag" :class="'tag-' + m">{{ MODEL_META[m].tag }}</span>
                  </div>
                  <div class="model-option-desc">{{ MODEL_META[m].desc }}</div>
                </div>
                <el-icon v-if="m === selectedModel" class="model-check"><Check /></el-icon>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
      <div ref="scrollRef" class="chat-scroll" :class="{ empty: welcomeHint }">
        <div v-if="welcomeHint" class="welcome">
          <div class="welcome-top">
            <div class="welcome-logo">L</div>
            <div class="welcome-title">你好，我是 <span class="grad-text">LLMHub</span> 助手</div>
            <div class="welcome-sub">这不只是一个聊天框——我可以调用工具、检索你的知识库。</div>
          </div>
          <div class="welcome-bottom">
            <!-- 三张示例卡收进一个提示按钮：点击弹出浮层选择 -->
            <el-popover v-model:visible="hintOpen" trigger="click" placement="top"
                        :width="660" popper-class="sample-popper">
              <template #reference>
                <button class="hint-btn" :class="{ open: hintOpen }">
                  <span class="hint-btn-icon">💡</span>
                  不知道问什么？点这里看 3 个上手示例
                  <span class="hint-caret" :class="{ up: hintOpen }">▾</span>
                </button>
              </template>
              <div class="sample-grid">
                <div v-for="s in samples" :key="s.text" class="sample-card" @click="pickSample(s.text)">
                  <div class="sample-head">
                    <span class="sample-icon">{{ s.icon }}</span>
                    <span class="sample-tag">{{ s.tag }}</span>
                  </div>
                  <div class="sample-text">{{ s.text }}</div>
                  <div class="sample-hint">{{ s.hint }}</div>
                </div>
              </div>
            </el-popover>
            <div class="welcome-note">日常闲聊也可以。选一个知识库后，回答会基于文档内容并标注引用。</div>
          </div>
        </div>
        <div v-for="(m, i) in messages" :key="i" class="msg-row" :class="m.role">
          <div v-if="m.role === 'assistant'" class="avatar ai">AI</div>
          <div class="bubble">
            <span v-if="m.loading" class="thinking"><span class="dot-pulse"></span>思考中…</span>
            <!-- assistant 用 markdown 渲染（html:false，LLM 输出的 HTML 会被转义防 XSS）；
                 user 消息保持纯文本，避免把用户输入当格式解析 -->
            <div v-else-if="m.role === 'assistant'" class="md-body" v-html="renderMd(m.content)"></div>
            <template v-else>{{ m.content }}</template>
            <span v-if="m.streaming" class="cursor">▍</span>
            <!-- 工具调用轨迹（assistant 消息） -->
            <div v-if="m.tool_trace && m.tool_trace.length" class="tool-trace">
              <el-tag v-for="(t, j) in m.tool_trace" :key="j" size="small"
                      :type="t.ok ? 'success' : 'danger'" class="trace-tag" effect="light" round>
                🔧 {{ t.tool }}
                <span v-if="t.arguments" style="opacity:.75">{{ ' ' + t.arguments }}</span>
              </el-tag>
            </div>
          </div>
          <div v-if="m.role === 'user'" class="avatar me">{{ auth.username.slice(0, 1).toUpperCase() }}</div>
        </div>
      </div>
      <div class="input-bar">
        <div class="input-card">
          <el-input v-model="input" type="textarea" :rows="2" resize="none"
                    placeholder="输入问题，Enter 发送（Shift+Enter 换行）"
                    @keydown.enter.exact.prevent="send" />
          <div class="input-foot">
            <el-button type="primary" class="send-btn" :loading="sending" @click="send">发 送</el-button>
          </div>
        </div>
      </div>
    </el-container>
  </el-container>
</template>

<style scoped>
/* ---------------- 深色侧栏 ---------------- */
.sidebar {
  background: linear-gradient(180deg, #1a2032 0%, #141826 100%);
  display: flex; flex-direction: column;
  border-right: none;
}
.side-top { padding: 18px 14px 10px; }
.brand { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.brand-logo {
  width: 34px; height: 34px; border-radius: 10px;
  background: linear-gradient(135deg, #4d6bfe, #6e8cff);
  color: #fff; font-weight: 800; font-size: 17px;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 3px 10px rgba(94, 108, 246, .4);
}
.brand-name { color: #fff; font-weight: 700; font-size: 17px; letter-spacing: .5px; }
.new-btn {
  width: 100%;
  background: linear-gradient(135deg, #4d6bfe, #6e8cff) !important;
  border: none !important; border-radius: 10px !important;
  color: #fff !important; font-weight: 600;
}
.new-btn:hover { opacity: .9; }
.kb-select { width: 100%; margin-top: 12px; }
/* 深色背景下的下拉选择框（兼容 element-plus 新旧两版 DOM 结构） */
.kb-select :deep(.el-select__wrapper),
.kb-select :deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, .08);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, .12) inset !important;
  border-radius: 8px;
}
.kb-select :deep(.el-select__placeholder),
.kb-select :deep(.el-input__inner),
.kb-select :deep(.el-select__selected-item),
.kb-select :deep(.el-input__inner)::placeholder {
  color: #aeb8cc;
}
.kb-select :deep(.el-input__inner) { color: #e6ebf5; }
.kb-select :deep(.el-select__caret) { color: #8a94a8; }
.nav-row { display: flex; gap: 8px; margin-top: 12px; }
.nav-btn {
  flex: 1; text-align: center; padding: 8px 0;
  background: rgba(255, 255, 255, .07); border-radius: 8px;
  color: #c7cfdf; font-size: 13px; cursor: pointer;
  transition: background .15s, color .15s;
}
.nav-btn:hover { background: rgba(255, 255, 255, .14); color: #fff; }
.conv-list { flex: 1; overflow-y: auto; padding: 8px 12px; }
.conv-list::-webkit-scrollbar { width: 5px; }
.conv-list::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, .15); border-radius: 3px; }
/* 删除按钮默认隐藏，hover 会话项时浮现——界面更干净 */
.conv-item .conv-del { opacity: 0; transition: opacity .15s, color .15s; }
.conv-item:hover .conv-del { opacity: 1; }
.conv-del { color: rgba(255,255,255,.35); font-size: 13px; }
.conv-del:hover { color: #ff7875; }
.conv-empty { color: #6d7789; font-size: 12.5px; text-align: center; padding: 28px 10px; line-height: 1.8; }
.side-footer {
  padding: 12px 16px; border-top: 1px solid rgba(255,255,255,.08);
  display: flex; align-items: center; gap: 8px;
}
.side-user { flex: 1; color: #c7cfdf; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.logout-btn { color: #8a94a8 !important; }
.logout-btn:hover { color: #ff7875 !important; }

/* ---------------- 主区 ---------------- */
.main-area {
  position: relative;
  /* DeepSeek 蓝调底色：整体从蓝紫向品牌蓝 #4d6bfe 靠拢 */
  background: linear-gradient(160deg, #eef3ff 0%, #f5f8ff 48%, #eaf1ff 100%);
}
/* 背景视频层：铺满聊天区，object-fit:cover 裁切填满任意宽高比。
   video 需要 muted + playsinline 才能免用户交互自动播放循环。
   容器 pointer-events:none 不挡点击/输入 */
.bg-deco {
  position: absolute; inset: 0; overflow: hidden;
  pointer-events: none; z-index: 0;
}
.bg-video {
  position: absolute; inset: 0;
  width: 100%; height: 100%; object-fit: cover;
  /* 提饱和 + 提对比：抵消微信源片压缩后的灰蒙感，让画面更鲜艳通透 */
  filter: saturate(1.28) contrast(1.08);
}
/* 可读性遮罩：只压顶/底两条带（顶栏、输入区附近），中间大面积留给视频，
   保证视频色彩鲜亮；气泡自身是白色实底，文字可读性不受影响 */
.bg-overlay {
  position: absolute; inset: 0;
  background: linear-gradient(180deg, rgba(255,255,255,.26), rgba(255,255,255,.04) 22%, rgba(255,255,255,.04) 74%, rgba(255,255,255,.3));
}
/* 内容层整体压在装饰层之上 */
.chat-header, .chat-scroll, .input-bar { position: relative; z-index: 1; }

/* 顶栏：深蓝紫玻璃条，压在视频上方与紫色画面/深色侧栏同一调性 */
.chat-header {
  height: 52px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 24px;
  background: rgba(24, 22, 52, .38); backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid rgba(255, 255, 255, .14);
}
.chat-title { font-weight: 600; font-size: 14.5px; color: #fff;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 60%;
  text-shadow: 0 1px 4px rgba(0, 0, 0, .35); }
.model-badge .dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: #34c77b; box-shadow: 0 0 0 3px rgba(52, 199, 123, .18);
  animation: dot-breath 2.4s ease-in-out infinite;
}
@keyframes dot-breath {
  0%, 100% { box-shadow: 0 0 0 3px rgba(52, 199, 123, .18); }
  50% { box-shadow: 0 0 0 5px rgba(52, 199, 123, .10); }
}

/* ---------------- 模型选择器 ---------------- */
.model-picker { cursor: pointer; outline: none; }
.model-badge {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: rgba(255, 255, 255, .88);
  background: rgba(255,255,255,.14); backdrop-filter: blur(6px);
  padding: 4px 12px; border-radius: 12px;
  border: 1px solid rgba(255,255,255,.26);
  box-shadow: 0 1px 4px rgba(0,0,0,.15);
  white-space: nowrap; flex-shrink: 0;
  transition: border-color .15s, box-shadow .15s, color .15s;
}
.model-badge:hover {
  color: #fff; border-color: rgba(255, 255, 255, .6);
  box-shadow: 0 2px 8px rgba(94, 108, 242, .35);
}
.badge-caret { font-size: 11px; color: rgba(255, 255, 255, .6); }

/* ---------------- 欢迎页 ---------------- */
/* 空会话时 chat-scroll 转为纵向 flex，welcome 撑满整列：
   标题区贴顶、示例卡片贴底（紧邻输入框），中部完全留白给背景视频的人物面部 */
.chat-scroll.empty { display: flex; flex-direction: column; }
.welcome { max-width: 720px; width: 100%; margin: 0 auto; text-align: center;
  flex: 1; display: flex; flex-direction: column; justify-content: space-between; }
.welcome-top { margin-top: 1vh; }
.welcome-logo {
  width: 56px; height: 56px; border-radius: 16px; margin: 0 auto 16px;
  background: linear-gradient(135deg, #4d6bfe, #6e8cff);
  color: #fff; font-weight: 800; font-size: 26px;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 8px 24px rgba(77, 107, 254, .35);
  animation: logo-float 3.5s ease-in-out infinite;
}
@keyframes logo-float {
  0%, 100% { transform: translateY(0); box-shadow: 0 8px 24px rgba(77, 107, 254, .35); }
  50% { transform: translateY(-6px); box-shadow: 0 14px 30px rgba(77, 107, 254, .45); }
}
.welcome-title { font-size: 22px; font-weight: 700; color: #1f2430; margin-bottom: 8px;
  text-shadow: 0 1px 8px rgba(255, 255, 255, .9), 0 1px 2px rgba(255, 255, 255, .8); }
.grad-text {
  background: linear-gradient(135deg, #2b4be0, #0f8fd0);
  -webkit-background-clip: text; background-clip: text; color: transparent;
  /* background-clip 文字的专属提亮手法：drop-shadow 不像 text-shadow 会透出色块 */
  filter: drop-shadow(0 1px 3px rgba(255, 255, 255, .9)) drop-shadow(0 0 8px rgba(255, 255, 255, .6));
}
/* 副标题/提示语：白字黑字都容易糊在视频上，改用毛玻璃胶囊衬底，
   文字加深加粗，任何背景帧上都清晰可读 */
.welcome-sub {
  display: inline-block; color: #1f2430; font-size: 14px; font-weight: 500;
  margin-bottom: 22px; padding: 7px 18px; border-radius: 999px;
  background: rgba(255, 255, 255, .68);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  box-shadow: 0 2px 10px rgba(31, 45, 80, .08);
}
.sample-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; text-align: left; }
.sample-card {
  /* --ac 主题色由 nth-child 按卡分配：标签/图标圈/悬浮环全部跟随 */
  --ac: #4d6bfe; --ac-bg: #eef2ff;
  background: #fff; border: 1px solid #eceef5; border-radius: 14px;
  padding: 14px; cursor: pointer; transition: all .18s;
  box-shadow: 0 1px 3px rgba(31,45,80,.05);
  border-top: 3px solid var(--ac);
}
/* 三张卡三个主题：蓝=工具调用、绿=知识库、紫=多轮记忆 */
.sample-card:nth-child(1) { --ac: #4d6bfe; --ac-bg: #eef2ff; }
.sample-card:nth-child(2) { --ac: #0e9f6e; --ac-bg: #e6f7f1; }
.sample-card:nth-child(3) { --ac: #8b5cf6; --ac-bg: #f3efff; }
/* hover：边框变透明 + 主题色渐变环（box-shadow 外环模拟渐变描边）+ 上浮 */
.sample-card:hover {
  border-color: transparent;
  transform: translateY(-3px);
  box-shadow: 0 0 0 1.5px var(--ac), 0 10px 24px rgba(94, 108, 242, .18);
}
/* 图标与标签同一行，卡片更紧凑 */
.sample-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.sample-icon {
  font-size: 17px; width: 32px; height: 32px; border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  background: var(--ac-bg);
}
.sample-tag {
  display: inline-block; font-size: 11px; font-weight: 600;
  color: var(--ac); background: var(--ac-bg);
  padding: 2px 10px; border-radius: 10px;
}
.sample-text { font-size: 13px; line-height: 1.6; color: #2b2f36; }
.sample-hint { font-size: 11.5px; color: #9aa3b5; margin-top: 8px; }
.welcome-note {
  display: inline-block; font-size: 12.5px; font-weight: 500; color: #3d4658;
  margin-top: 14px; padding: 6px 16px; border-radius: 999px;
  background: rgba(255, 255, 255, .62);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  box-shadow: 0 2px 8px rgba(31, 45, 80, .07);
}
/* 提示按钮：三张示例卡的入口。毛玻璃胶囊，与副标题视觉呼应 */
.hint-btn {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 14px; font-weight: 600; color: #1f2430;
  padding: 10px 22px; border-radius: 999px; border: 1px solid rgba(255, 255, 255, .8);
  background: rgba(255, 255, 255, .72);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  box-shadow: 0 4px 16px rgba(31, 45, 80, .12);
  cursor: pointer; transition: all .18s;
}
.hint-btn:hover { transform: translateY(-2px); box-shadow: 0 8px 22px rgba(31, 45, 80, .18); }
.hint-btn.open { border-color: #4d6bfe; color: #2b4be0; }
.hint-btn-icon { font-size: 16px; }
.hint-caret { font-size: 12px; color: #9aa3b5; transition: transform .18s; }
.hint-caret.up { transform: rotate(180deg); }

/* ---------------- 打字机与思考动画 ---------------- */
.cursor {
  display: inline-block;
  color: #4e6ef2;
  animation: blink 1s steps(2, start) infinite;
}
@keyframes blink { to { visibility: hidden; } }
.thinking { color: #9aa3b5; display: inline-flex; align-items: center; gap: 8px; }
.dot-pulse {
  width: 8px; height: 8px; border-radius: 50%; background: #4e6ef2;
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { transform: scale(.7); opacity: .4; }
  50% { transform: scale(1.15); opacity: 1; }
}

/* markdown 渲染体：紧凑排版适配气泡；
   white-space 必须覆盖全局的 pre-wrap——md 渲染出的 <p> 已带间距，
   保留原始换行符会导致空行翻倍 */
.md-body { white-space: normal; }
.md-body :deep(p) { margin: 0 0 6px; line-height: 1.7; }
.md-body :deep(p:last-child) { margin-bottom: 0; }
.md-body :deep(ul), .md-body :deep(ol) { margin: 4px 0 6px; padding-left: 20px; }
.md-body :deep(li) { line-height: 1.7; }
.md-body :deep(code) {
  background: rgba(78, 110, 242, .08); padding: 1px 5px;
  border-radius: 4px; font-size: 12.5px;
}
/* 代码块：深色底 + 左侧渐变描边，与浅色气泡形成对比，一眼识别"这是代码" */
.md-body :deep(pre) {
  background: #1e2433; color: #dbe2f0;
  padding: 12px 14px; border-radius: 10px;
  overflow-x: auto; margin: 8px 0;
  border-left: 3px solid #4d6bfe;
  font-size: 12.5px; line-height: 1.6;
}
.md-body :deep(pre::-webkit-scrollbar) { height: 5px; }
.md-body :deep(pre::-webkit-scrollbar-thumb) { background: rgba(255,255,255,.2); border-radius: 3px; }
.md-body :deep(pre code) { background: none; padding: 0; color: inherit; }
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3) {
  font-size: 14.5px; margin: 8px 0 6px; font-weight: 600;
}
.md-body :deep(blockquote) {
  border-left: 3px solid #b9c6ff; margin: 6px 0; padding: 2px 10px; color: #6d7789;
}
.md-body :deep(table) { border-collapse: collapse; margin: 6px 0; font-size: 12.5px; }
.md-body :deep(th), .md-body :deep(td) { border: 1px solid #e4e7ed; padding: 4px 10px; }
.md-body :deep(a) { color: #4e6ef2; }
</style>

<!-- el-dropdown 菜单 teleport 到 body，scoped 样式够不到，需要全局块。
     用 .el-dropdown__popper 前缀限定作用范围，避免污染全局 -->
<style>
.el-dropdown__popper .model-option { display: inline-block; line-height: 1.4; }
.el-dropdown__popper .model-option-main { display: flex; align-items: center; gap: 6px; }
.el-dropdown__popper .model-option-name { font-size: 13px; font-weight: 600; color: #2b2f36; }
.el-dropdown__popper .model-option-tag { font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 8px; }
.el-dropdown__popper .tag-deepseek-chat { color: #4e6ef2; background: #eef2ff; }
.el-dropdown__popper .tag-glm-4-flash { color: #1a9e5c; background: #e8f7ef; }
.el-dropdown__popper .tag-qwen-turbo { color: #c2620b; background: #fdf3e7; }
.el-dropdown__popper .model-option-desc { font-size: 11px; color: #9aa3b5; margin-top: 1px; }
.el-dropdown__popper .model-check { color: #4e6ef2; margin-left: 10px; }
.el-dropdown__popper .el-dropdown-menu__item.is-disabled { display: none; }
</style>
