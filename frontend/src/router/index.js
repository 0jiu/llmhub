import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/Login.vue') },
  { path: '/', name: 'chat', component: () => import('../views/Chat.vue') },
  // 统一挂 /console 前缀：避免与后端 API 路由前缀（/keys /kbs /auth…）冲突
  { path: '/console/kb', name: 'kb', component: () => import('../views/Knowledge.vue') },
  { path: '/console/keys', name: 'keys', component: () => import('../views/Keys.vue') },
]

const router = createRouter({ history: createWebHistory(), routes })

// 路由守卫：未登录一律去 /login
router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.name !== 'login' && !auth.token) return { name: 'login' }
  if (to.name === 'login' && auth.token) return { name: 'chat' }
  return true
})

export default router
