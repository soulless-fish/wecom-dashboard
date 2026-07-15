<template>
  <div class="group-purchase-page">
    <div class="topbar">
      <button class="back-btn" type="button" @click="goBack">返回</button>
      <div class="topbar-text">
        <div class="page-title">团购链接</div>
        <div class="page-subtitle">{{ poiName || groupName || '门店团购链接' }}</div>
      </div>
    </div>

    <div v-if="loading" class="state-box">
      <div class="loading-spinner"></div>
      <span>加载团购链接...</span>
    </div>

    <div v-else-if="error" class="state-box error-text">
      <div>{{ error }}</div>
      <button class="retry-btn" type="button" @click="loadData">重试</button>
    </div>

    <template v-else>
      <section class="summary-band">
        <div class="summary-main">
          <span class="summary-label">开通状态</span>
          <strong :class="['summary-status', data.opened ? 'opened' : 'closed']">
            {{ data.status || (data.opened ? '已开通' : '未开通') }}
          </strong>
        </div>
        <div class="summary-meta">
          <span>命中 {{ productCount }} 个团购链接</span>
          <span v-if="dataUpdateText">数据 {{ dataUpdateText }}</span>
        </div>
      </section>

      <section class="links-section">
        <div class="section-heading">已开通团购链接</div>
        <div v-if="products.length === 0" class="empty-links">
          当前门店没有开通本次指定商品范围内的团购链接
        </div>
        <article v-for="product in products" :key="product.product_id || product.product_name" class="link-item">
          <div class="link-title">{{ product.product_name || '未命名团购商品' }}</div>
          <div class="link-meta">
            <span v-if="product.product_id">商品ID：{{ product.product_id }}</span>
          </div>
        </article>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { douyinGroupPurchaseApi } from '../api'

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const error = ref('')
const data = ref({})

const poiId = computed(() => String(route.query.poi_id || ''))
const poiName = computed(() => String(route.query.poi_name || ''))
const groupName = computed(() => String(route.query.group_name || ''))
const products = computed(() => Array.isArray(data.value.products) ? data.value.products : [])
const productCount = computed(() => Number(data.value.product_count || products.value.length || 0))
const dataUpdateText = computed(() => String(data.value.store_data_update_text || data.value.last_sync_at || ''))

function goBack() {
  router.back()
}

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const res = await douyinGroupPurchaseApi.getStoreLinks({ poi_id: poiId.value })
    data.value = res?.data || {}
  } catch (err) {
    console.error('加载团购链接失败:', err)
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
.group-purchase-page {
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
  border: 1px solid #f3b1cc;
  border-radius: 999px;
  background: #fff;
  color: #cf1768;
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
  border: 1px solid #ead7df;
  border-radius: 8px;
  background: #fff;
  color: #667085;
  text-align: center;
}

.loading-spinner {
  width: 24px;
  height: 24px;
  margin: 0 auto 12px;
  border: 2px solid #f5d6e4;
  border-top-color: #e11d72;
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
  background: linear-gradient(135deg, #ff4f9a 0%, #e11d72 48%, #b9145c 100%);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
}

.summary-band,
.links-section {
  margin-bottom: 12px;
  padding: 14px;
  border: 1px solid #ead7df;
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
  font-size: 18px;
  line-height: 1.3;
}

.summary-status.opened {
  color: #c1155d;
}

.summary-status.closed {
  color: #667085;
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

.empty-links {
  padding: 18px 0;
  color: #667085;
  font-size: 14px;
  text-align: center;
}

.link-item {
  padding: 12px 0;
  border-bottom: 1px dashed #f0d6e1;
}

.link-item:last-child {
  border-bottom: none;
}

.link-title {
  color: #101828;
  font-size: 14px;
  font-weight: 800;
  line-height: 1.5;
  word-break: break-word;
}

.link-meta {
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
