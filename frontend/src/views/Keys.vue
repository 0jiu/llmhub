<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Back, Delete, Plus } from '@element-plus/icons-vue'
import client, { errMsg } from '../api/client'

const router = useRouter()

const keys = ref([])
const loading = ref(false)

async function loadKeys() {
  loading.value = true
  try {
    const { data } = await client.get('/keys')
    keys.value = data
  } finally {
    loading.value = false
  }
}

// ---------- 签发 ----------
const createVisible = ref(false)
const creating = ref(false)
const form = ref({ name: '', rpm_limit: 10 })
/** 签发响应中的明文 Key——服务端只存哈希，仅此一次展示机会 */
const issuedKey = ref('')

async function createKey() {
  if (!form.value.name.trim()) { ElMessage.warning('请填写应用名称'); return }
  creating.value = true
  try {
    const { data } = await client.post('/keys', form.value)
    issuedKey.value = data.key          // 明文只在这一次响应里
    await loadKeys()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    creating.value = false
  }
}

function closeIssueDialog() {
  issuedKey.value = ''
  createVisible.value = false
  form.value = { name: '', rpm_limit: 10 }
}

async function copyKey() {
  try {
    await navigator.clipboard.writeText(issuedKey.value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('复制失败，请手动选中复制')
  }
}

async function revokeKey(row) {
  await ElMessageBox.confirm(
    `吊销 Key「${row.name}」？使用该 Key 的应用将立即 401。`, '提示', { type: 'warning' })
  await client.delete(`/keys/${row.id}`)
  await loadKeys()
  ElMessage.success('已吊销')
}

onMounted(loadKeys)
</script>

<template>
  <el-container style="min-height:100vh; background:#f5f7fa;">
    <el-header height="56px" class="page-header">
      <el-button :icon="Back" text @click="router.push({ name: 'chat' })">返回聊天</el-button>
      <div class="brand-logo-sm">L</div>
      <span class="page-title">API Key 管理</span>
      <span class="page-sub">服务端只存 SHA256 哈希，明文签发后不可再查看</span>
      <el-button type="primary" size="small" class="grad-btn" style="margin-left:auto;" :icon="Plus" @click="createVisible = true">签发新 Key</el-button>
    </el-header>

    <el-main style="max-width:900px; margin:0 auto; width:100%;">
      <el-table :data="keys" v-loading="loading" style="width:100%; background:#fff;">
        <el-table-column prop="name" label="应用名称" min-width="140" />
        <el-table-column prop="key_masked" label="Key" min-width="180">
          <template #default="{ row }">
            <code style="font-size:12px; background:#f5f7fa; padding:2px 8px; border-radius:4px;">{{ row.key_masked }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="rpm_limit" label="限流（次/分）" width="110" />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === '启用' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="110">
          <template #default="{ row }">{{ row.created_at.slice(0, 10) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button v-if="row.status === '启用'" type="danger" text size="small"
                       @click="revokeKey(row)">吊销</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-main>

    <!-- 签发对话框 -->
    <el-dialog v-model="createVisible" :title="issuedKey ? 'Key 签发成功' : '签发新 Key'"
               width="460px" :close-on-click-modal="false">
      <template v-if="!issuedKey">
        <el-form label-width="110px">
          <el-form-item label="应用名称">
            <el-input v-model="form.name" maxlength="64" placeholder="便于辨识 Key 用在哪，如：作业爬虫" />
          </el-form-item>
          <el-form-item label="限流（次/分）">
            <el-slider v-model="form.rpm_limit" :min="1" :max="60" show-input />
          </el-form-item>
        </el-form>
      </template>
      <template v-else>
        <el-alert type="warning" :closable="false" show-icon
                  title="请立即保存此 Key"
                  description="服务端只存哈希，关闭本窗口后无法再次查看完整 Key。" style="margin-bottom:14px;" />
        <div class="issued-key-row">
          <code class="issued-key">{{ issuedKey }}</code>
          <el-button type="primary" size="small" @click="copyKey">复制</el-button>
        </div>
      </template>
      <template #footer>
        <template v-if="!issuedKey">
          <el-button @click="closeIssueDialog">取消</el-button>
          <el-button type="primary" :loading="creating" @click="createKey">签发</el-button>
        </template>
        <el-button v-else type="primary" @click="closeIssueDialog">我已保存，关闭</el-button>
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
.issued-key-row {
  display:flex; gap:10px; align-items:center;
}
.issued-key {
  flex:1; font-size:12.5px; background:#f5f7fa; padding:8px 10px;
  border-radius:6px; word-break:break-all; user-select:all;
}
</style>
