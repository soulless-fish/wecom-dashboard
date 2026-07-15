import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'

// 路由配置
const routes = [
  {
    path: '/',
    name: 'Sidebar',
    component: () => import('./views/Sidebar.vue')
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('./views/Home.vue')
  },
  {
    path: '/jlyq',
    name: 'JlyqLocalPromotion',
    component: () => import('./views/JlyqLocalPromotion.vue')
  },
  {
    path: '/jlyq-form',
    name: 'JlyqMerchantForm',
    component: () => import('./views/JlyqMerchantForm.vue')
  },
  {
    path: '/group-purchase',
    name: 'GroupPurchaseLinks',
    component: () => import('./views/GroupPurchaseLinks.vue')
  },
  {
    path: '/douyin-accounts',
    name: 'DouyinAccounts',
    component: () => import('./views/DouyinAccounts.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

const app = createApp(App)
app.use(router)
app.mount('#app')
