import { defineStore } from 'pinia'
import client from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('llmhub_token') || '',
    username: localStorage.getItem('llmhub_username') || '',
  }),
  actions: {
    async login(username, password) {
      const { data } = await client.post('/auth/login', { username, password })
      this.token = data.access_token
      this.username = username
      localStorage.setItem('llmhub_token', this.token)
      localStorage.setItem('llmhub_username', username)
    },
    async register(username, password) {
      await client.post('/auth/register', { username, password })
    },
    logout() {
      this.token = ''
      this.username = ''
      localStorage.removeItem('llmhub_token')
      localStorage.removeItem('llmhub_username')
    },
  },
})
