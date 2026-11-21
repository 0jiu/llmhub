<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { errMsg } from '../api/client'

const router = useRouter()
const auth = useAuthStore()
const mode = ref('login') // login | register
const loading = ref(false)
const form = reactive({ username: '', password: '' })

async function submit() {
  if (!form.username || !form.password) return ElMessage.warning('请填写用户名和密码')
  loading.value = true
  try {
    if (mode.value === 'login') {
      await auth.login(form.username, form.password)
      ElMessage.success('登录成功')
      router.push({ name: 'chat' })
    } else {
      await auth.register(form.username, form.password)
      ElMessage.success('注册成功，自动登录中')
      await auth.login(form.username, form.password)
      router.push({ name: 'chat' })
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <!-- 装饰光斑 -->
    <div class="blob b1"></div>
    <div class="blob b2"></div>
    <div class="blob b3"></div>
    <el-card class="login-card" shadow="never">
      <div class="logo">L</div>
      <h2 class="title">LLM<span class="grad-text">Hub</span></h2>
      <p class="subtitle">轻量级大模型应用开发平台</p>
      <el-tabs v-model="mode" stretch>
        <el-tab-pane label="登录" name="login" />
        <el-tab-pane label="注册" name="register" />
      </el-tabs>
      <el-form @keyup.enter="submit">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" placeholder="密码" size="large" show-password />
        </el-form-item>
        <el-button type="primary" size="large" class="submit-btn" :loading="loading" @click="submit">
          {{ mode === 'login' ? '登 录' : '注 册 并 登 录' }}
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  height: 100%; position: relative; overflow: hidden;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(160deg, #eef2ff 0%, #f6f7fb 55%, #f3efff 100%);
}
/* 漂浮光斑：纯 CSS 装饰，模糊+低透明度营造氛围 */
.blob { position: absolute; border-radius: 50%; filter: blur(70px); opacity: .5; }
.b1 { width: 340px; height: 340px; background: #7c9cff; top: -80px; left: -60px; animation: drift 14s ease-in-out infinite; }
.b2 { width: 300px; height: 300px; background: #c3a4ff; bottom: -60px; right: -40px; animation: drift 18s ease-in-out infinite reverse; }
.b3 { width: 220px; height: 220px; background: #8fd4ff; bottom: 120px; left: 12%; animation: drift 22s ease-in-out infinite; }
@keyframes drift {
  0%, 100% { transform: translate(0, 0); }
  50% { transform: translate(24px, -20px); }
}
.login-card {
  width: 392px; z-index: 1;
  border-radius: 20px !important; border: 1px solid rgba(255,255,255,.7) !important;
  background: rgba(255, 255, 255, .82) !important;
  backdrop-filter: blur(14px);
  box-shadow: 0 20px 50px rgba(60, 80, 180, .16) !important;
  padding: 10px 6px;
}
.logo {
  width: 52px; height: 52px; border-radius: 14px; margin: 4px auto 12px;
  background: linear-gradient(135deg, #4e6ef2, #7c5cff);
  color: #fff; font-weight: 800; font-size: 24px;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 6px 18px rgba(94, 108, 246, .4);
}
.title { text-align: center; letter-spacing: 1px; font-size: 22px; }
.grad-text {
  background: linear-gradient(135deg, #4e6ef2, #a44ff0);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.subtitle { text-align: center; color: #8a94a8; font-size: 13px; margin: 6px 0 10px; }
.submit-btn {
  width: 100%;
  background: linear-gradient(135deg, #4e6ef2, #7c5cff) !important;
  border: none !important; border-radius: 10px !important;
  letter-spacing: 3px;
}
</style>
