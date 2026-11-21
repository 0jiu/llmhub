<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Delete, Plus, Upload } from '@element-plus/icons-vue'
import client, { errMsg } from '../api/client'

const router = useRouter()

const kbs = ref([])
const activeKbId = ref(null)          // 当前展开的库
const docs = ref([])                  // 当前库的文档列表
const uploading = ref(false)

async function loadKbs() {
  const { data } = await client.get('/kbs')
  kbs.value = data
}

async function loadDocs(kbId) {
  const { data } = await client.get(`/kbs/${kbId}/documents`)
  docs.value = data
}

function selectKb(kb) {
  if (activeKbId.value === kb.id) { activeKbId.value = null; return }
  activeKbId.value = kb.id
  loadDocs(kb.id)
}

// ---------- 新建库 ----------
const createVisible = ref(false)
const newKb = ref({ name: '', description: '' })

async function createKb() {
  if (!newKb.value.name.trim()) { ElMessage.warning('请填写库名'); return }
  await client.post('/kbs', newKb.value)
  createVisible.value = false
  newKb.value = { name: '', description: '' }
  await loadKbs()
  ElMessage.success('知识库已创建')
}

async function deleteKb(kb) {
  await ElMessageBox.confirm(`删除知识库「${kb.name}」？其下所有文档与向量将一并清除。`, '危险操作', { type: 'warning' })
  const { data } = await client.delete(`/kbs/${kb.id}`)
  if (activeKbId.value === kb.id) activeKbId.value = null
  await loadKbs()
  ElMessage.success(`已删除（清理 ${data.documents_removed} 个文档）`)
}

// ---------- 上传文档 ----------
/** 自定义上传：走 axios 实例自动带 JWT，FormData 传文件 */
async function uploadFile(kbId, file) {
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.raw)
    const { data } = await client.post(`/kbs/${kbId}/documents`, fd)
    ElMessage.success(`${data.filename}：切 ${data.chunk_count} 块，向量化 ${data.vector_count} 条`)
    await loadDocs(kbId)
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    uploading.value = false
  }
}

async function deleteDoc(doc) {
  await ElMessageBox.confirm(`删除文档「${doc.filename}」？（${doc.chunk_count} 个分块与向量将同步移除）`, '提示', { type: 'warning' })
  await client.delete(`/kbs/${activeKbId.value}/documents/${doc.id}`)
  await loadDocs(activeKbId.value)
  ElMessage.success('已删除')
}

onMounted(loadKbs)
</script>

<template>
  <el-container style="min-height:100vh; background:#f5f7fa;">
    <el-header height="56px" class="page-header">
      <el-button :icon="Back" text @click="router.push({ name: 'chat' })">返回聊天</el-button>
      <div class="brand-logo-sm">L</div>
      <span class="page-title">知识库管理</span>
      <span class="page-sub">文档解析 → 切分（500/80 滑窗）→ bge 向量化 → FAISS 入库</span>
      <el-button type="primary" size="small" class="grad-btn" style="margin-left:auto;" :icon="Plus" @click="createVisible = true">新建知识库</el-button>
    </el-header>

    <el-main style="max-width:900px; margin:0 auto; width:100%;">
      <el-empty v-if="kbs.length === 0" description="还没有知识库，点击右上角新建" />

      <div v-for="kb in kbs" :key="kb.id" class="kb-card">
        <div class="kb-head" @click="selectKb(kb)">
          <div>
            <div class="kb-name">{{ kb.name }}</div>
            <div class="kb-desc">{{ kb.description || '暂无描述' }} · {{ kb.created_at.slice(0, 10) }}</div>
          </div>
          <el-button type="danger" text size="small" :icon="Delete"
                     @click.stop="deleteKb(kb)">删除</el-button>
        </div>

        <!-- 展开的文档区 -->
        <div v-if="activeKbId === kb.id" class="kb-docs">
          <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
            <el-upload :show-file-list="false" accept=".txt,.md,.pdf"
                       :auto-upload="false" @change="(f) => uploadFile(kb.id, f)">
              <el-button type="primary" size="small" :icon="Upload" :loading="uploading">
                {{ uploading ? '解析并向量化中…' : '上传文档（txt / md / pdf）' }}
              </el-button>
            </el-upload>
            <span style="font-size:12px; color:#909399;">上传即解析入库，耗时随文档大小而定</span>
          </div>

          <el-empty v-if="docs.length === 0" description="该库还没有文档" :image-size="50" />
          <div v-for="d in docs" :key="d.id" class="doc-row">
            <span class="doc-name">📄 {{ d.filename }}</span>
            <span class="doc-meta">{{ d.chunk_count }} 个分块 · {{ d.created_at.slice(0, 10) }}</span>
            <el-button type="danger" text size="small" @click="deleteDoc(d)">删除</el-button>
          </div>
        </div>
      </div>
    </el-main>

    <!-- 新建库对话框 -->
    <el-dialog v-model="createVisible" title="新建知识库" width="420px">
      <el-form label-width="70px">
        <el-form-item label="库名">
          <el-input v-model="newKb.name" maxlength="64" placeholder="如：产品手册" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="newKb.description" maxlength="255" placeholder="用途说明（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="createKb">创建</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<style scoped>
.page-header {
  background: rgba(255,255,255,.8); backdrop-filter: blur(8px);
  border-bottom: 1px solid #eef0f6;
  display:flex; align-items:center; gap:12px;
}
.brand-logo-sm {
  width: 30px; height: 30px; border-radius: 8px;
  background: linear-gradient(135deg, #4e6ef2, #7c5cff);
  color:#fff; font-weight:800; font-size:15px;
  display:flex; align-items:center; justify-content:center;
}
.page-title { font-weight:700; font-size:15.5px; }
.page-sub { font-size:12px; color:#9aa3b5; }
.grad-btn {
  background: linear-gradient(135deg, #4e6ef2, #7c5cff) !important;
  border: none !important; border-radius: 8px !important;
}
.kb-card {
  background:#fff; border:1px solid #eceef5; border-radius:14px;
  margin-bottom:14px; overflow:hidden;
  box-shadow: 0 1px 4px rgba(31,45,80,.06);
  transition: box-shadow .18s;
}
.kb-card:hover { box-shadow: 0 6px 18px rgba(31,45,80,.1); }
.kb-head {
  display:flex; justify-content:space-between; align-items:center;
  padding:14px 16px; cursor:pointer;
}
.kb-head:hover { background:#fafbfd; }
.kb-name { font-weight:500; font-size:14.5px; }
.kb-desc { font-size:12.5px; color:#909399; margin-top:3px; }
.kb-docs { border-top:1px solid #f0f2f5; padding:12px 16px; background:#fafbfd; }
.doc-row {
  display:flex; align-items:center; gap:12px;
  padding:8px 10px; background:#fff; border:1px solid #f0f2f5;
  border-radius:8px; margin-bottom:8px;
}
.doc-name { font-size:13px; flex:1; }
.doc-meta { font-size:12px; color:#909399; }
</style>
