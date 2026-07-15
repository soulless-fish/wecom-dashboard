<template>
  <div class="douyin-account-page">
    <div class="topbar">
      <button class="back-btn" type="button" @click="goBack">返回</button>
      <div class="topbar-text">
        <div class="page-title">抖音号</div>
        <div class="page-subtitle">{{ poiName || groupName || '门店抖音号' }}</div>
      </div>
    </div>

    <div v-if="loading" class="state-box">
      <div class="loading-spinner"></div>
      <span>加载抖音号...</span>
    </div>

    <div v-else-if="error" class="state-box error-text">
      <div>{{ error }}</div>
      <button class="retry-btn" type="button" @click="loadData">重试</button>
    </div>

    <template v-else>
      <section class="summary-band">
        <div class="summary-main">
          <span class="summary-label">可展示抖音号</span>
          <strong class="summary-status">{{ accountCount }} 个</strong>
        </div>
        <div class="summary-meta">
          <span>子机构经营号只显示已激活，商家职人号只显示运营中，个人职人号只显示签约成功</span>
          <span v-if="lastSyncAt">数据 {{ lastSyncAt }}</span>
        </div>
      </section>

      <section class="account-section">
        <div class="section-heading">子机构经营号</div>
        <div v-if="subOrgAccounts.length === 0" class="empty-account">当前门店暂无已激活的子机构经营号</div>
        <article v-for="account in subOrgAccounts" :key="accountKey(account)" class="account-item">
          <div class="account-title">{{ account.account_name || '未命名抖音号' }}</div>
          <div class="account-meta">
            <span v-if="account.account_id">抖音号ID：{{ account.account_id }}</span>
            <span>状态：{{ account.status || '未知' }}</span>
            <span v-if="account.poi_name">门店：{{ account.poi_name }}</span>
          </div>
        </article>
      </section>

      <section class="account-section">
        <div class="section-heading">商家职人号</div>
        <div v-if="merchantAccounts.length === 0" class="empty-account">当前门店暂无运营中的商家职人号</div>
        <article v-for="account in merchantAccounts" :key="accountKey(account)" class="account-item">
          <div class="account-title">{{ account.account_name || account.account_id || '未命名抖音号' }}</div>
          <div class="account-meta">
            <span v-if="account.account_id">抖音号：{{ account.account_id }}</span>
            <span v-if="account.employee_info">就职信息：{{ account.employee_info }}</span>
            <span>状态：{{ account.status || '未知' }}</span>
          </div>
        </article>
      </section>

      <section class="account-section">
        <div class="section-heading">个人职人号</div>
        <div v-if="personalAccounts.length === 0" class="empty-account">当前门店暂无签约成功的个人职人号</div>
        <article v-for="account in personalAccounts" :key="accountKey(account)" class="account-item">
          <div class="account-title">{{ account.account_id || account.account_name || '未命名抖音号' }}</div>
          <div class="account-meta">
            <span v-if="account.account_id">抖音号：{{ account.account_id }}</span>
            <span v-if="account.employee_info">就职信息：{{ account.employee_info }}</span>
            <span>状态：{{ account.status || '未知' }}</span>
          </div>
        </article>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { douyinAccountApi } from '../api'

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const error = ref('')
const data = ref({})

const poiId = computed(() => String(route.query.poi_id || ''))
const poiName = computed(() => String(route.query.poi_name || ''))
const groupName = computed(() => String(route.query.group_name || ''))
const subOrgAccounts = computed(() => Array.isArray(data.value.sub_org_accounts) ? data.value.sub_org_accounts : [])
const merchantAccounts = computed(() => Array.isArray(data.value.merchant_craftsman_accounts) ? data.value.merchant_craftsman_accounts : [])
const personalAccounts = computed(() => Array.isArray(data.value.personal_craftsman_accounts) ? data.value.personal_craftsman_accounts : [])
const accountCount = computed(() => Number(data.value.account_count || 0))
const lastSyncAt = computed(() => String(data.value.last_sync_at || ''))

function goBack() {
  // 返回企业微信侧边栏原页面。
  router.back()
}

function accountKey(account) {
  // 使用分类、门店和抖音号拼出稳定键，避免同名账号渲染冲突。
  return [
    account?.account_category || '',
    account?.poi_id || '',
    account?.account_id || '',
    account?.account_name || ''
  ].join('|')
}

async function loadData() {
  // 详情页按门店ID和门店名称读取后端已过滤好的抖音号数据。
  loading.value = true
  error.value = ''
  try {
    const res = await douyinAccountApi.getStoreAccounts({
      poi_id: poiId.value,
      poi_name: poiName.value
    })
    data.value = res?.data || {}
  } catch (err) {
    console.error('加载抖音号失败:', err)
    error.value = err.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.douyin-account-page {
  min-height: 100vh;
  padding: 12px;
  background: #f6f8fb;
  color: #1f2937;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.topbar {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 54px;
  padding: 8px 0 14px;
}

.back-btn {
  min-width: 58px;
  height: 32px;
  border: 1px solid #a7f3d0;
  border-radius: 999px;
  background: #fff;
  color: #059669;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.back-btn:active {
  transform: translateY(1px);
}

.topbar-text {
  min-width: 0;
}

.page-title {
  font-size: 18px;
  font-weight: 800;
  line-height: 1.25;
}

.page-subtitle {
  margin-top: 2px;
  color: #667085;
  font-size: 12px;
  line-height: 1.4;
  word-break: break-all;
}

.state-box {
  min-height: 160px;
  padding: 34px 18px;
  border: 1px solid #d2f7df;
  border-radius: 8px;
  background: #fff;
  color: #667085;
  text-align: center;
}

.loading-spinner {
  width: 24px;
  height: 24px;
  margin: 0 auto 12px;
  border: 2px solid #d2f7df;
  border-top-color: #22c55e;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.error-text {
  color: #b42318;
}

.retry-btn {
  min-width: 86px;
  min-height: 32px;
  margin-top: 14px;
  border: none;
  border-radius: 999px;
  background: linear-gradient(135deg, #b7f34b 0%, #22c55e 48%, #06b6d4 100%);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
}

.summary-band,
.account-section {
  margin-bottom: 12px;
  padding: 14px;
  border: 1px solid #d2f7df;
  border-radius: 8px;
  background: #fff;
}

.summary-main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.summary-label {
  color: #667085;
  font-size: 13px;
}

.summary-status {
  color: #059669;
  font-size: 18px;
  line-height: 1.3;
}

.summary-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin-top: 8px;
  color: #667085;
  font-size: 12px;
  line-height: 1.5;
}

.section-heading {
  margin-bottom: 10px;
  color: #344054;
  font-size: 14px;
  font-weight: 800;
}

.empty-account {
  padding: 18px 0;
  color: #667085;
  font-size: 14px;
  text-align: center;
}

.account-item {
  padding: 12px 0;
  border-bottom: 1px dashed #d8f3df;
}

.account-item:last-child {
  border-bottom: none;
}

.account-title {
  color: #101828;
  font-size: 14px;
  font-weight: 800;
  line-height: 1.5;
  word-break: break-word;
}

.account-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 6px;
  color: #667085;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}
</style>
