<template>
  <div class="page-container">
    <!-- 头部信息 -->
    <div class="header-info">
      <div class="header-title">极修匠门店数据看板</div>
      <div class="header-subtitle">数据月份: {{ currentMonth }}</div>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="loading">
      <div class="loading-spinner"></div>
      <span>加载中...</span>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error">
      <div class="error-icon">!</div>
      <div class="error-message">{{ error }}</div>
      <button class="retry-btn" @click="loadData">重试</button>
    </div>

    <!-- 数据内容 -->
    <template v-else>
      <!-- 汇总统计 -->
      <div class="stats-panel">
        <div class="stat-card primary">
          <div class="stat-value">{{ formatMoney(summary.total_gmv_yuan) }}</div>
          <div class="stat-label">总上翻收益(元)</div>
        </div>
        <div class="stat-card info">
          <div class="stat-value">{{ summary.total_live_duration_formatted || '0' }}</div>
          <div class="stat-label">总直播时长</div>
        </div>
        <div class="stat-card success">
          <div class="stat-value">{{ summary.total_video_cnt_1d || 0 }}</div>
          <div class="stat-label">新发布关联视频数</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ summary.total_stores || 0 }}</div>
          <div class="stat-label">门店数量</div>
        </div>
      </div>

      <!-- 门店列表 -->
      <div class="card">
        <div class="card-title">门店业绩排行</div>
        <div v-if="stores.length === 0" class="empty">
          <div class="empty-icon">-</div>
          <div>暂无数据</div>
        </div>
        <div v-else>
          <div v-for="(store, index) in stores" :key="index" class="store-row">
            <div class="store-rank">{{ index + 1 }}</div>
            <div class="store-info">
              <div class="store-name-text">{{ store.poi_name }}</div>
              <div class="store-detail">
                <span class="detail-item gmv">收益: ¥{{ formatMoney(store.gmv_yuan) }}</span>
                <span class="detail-item">直播: {{ store.live_duration_formatted }}</span>
                <span class="detail-item">关联视频: {{ store.video_cnt_1d || 0 }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 定时任务状态 -->
      <div class="card">
        <div class="card-title">数据同步状态</div>
        <div class="data-item">
          <span class="data-label">定时任务</span>
          <span :class="['tag', schedulerStatus.running ? 'tag-success' : 'tag-warning']">
            {{ schedulerStatus.running ? '运行中' : '未运行' }}
          </span>
        </div>
        <div v-if="schedulerStatus.next_run" class="data-item">
          <span class="data-label">下次执行</span>
          <span class="data-value">{{ schedulerStatus.next_run }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { storePerformanceApi } from '../api'

// 数据
const loading = ref(true)
const error = ref('')
const summary = ref({})
const stores = ref([])
const schedulerStatus = ref({})

// 当前月份
const currentMonth = computed(() => {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
})

// 格式化金额
function formatMoney(value) {
  if (!value) return '0.00'
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })
}

// 加载数据
async function loadData() {
  loading.value = true
  error.value = ''

  try {
    // 并行请求
    const [summaryRes, storesRes, statusRes] = await Promise.all([
      storePerformanceApi.getSummary(),
      storePerformanceApi.getStores({ page: 1, page_size: 20 }),
      storePerformanceApi.getSchedulerStatus()
    ])

    if (summaryRes.code === 0) {
      summary.value = summaryRes.data
    }

    if (storesRes.code === 0) {
      stores.value = storesRes.data.stores || []
    }

    if (statusRes.code === 0) {
      schedulerStatus.value = statusRes.data
    }
  } catch (err) {
    console.error('加载数据失败:', err)
    error.value = err.message || '加载数据失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.store-row {
  display: flex;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid #f0f0f0;
}

.store-row:last-child {
  border-bottom: none;
}

.store-rank {
  width: 28px;
  height: 28px;
  background: #f5f5f5;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  color: #999;
  margin-right: 12px;
  flex-shrink: 0;
}

.store-row:nth-child(1) .store-rank {
  background: linear-gradient(135deg, #ffd700, #ffb700);
  color: #fff;
}

.store-row:nth-child(2) .store-rank {
  background: linear-gradient(135deg, #c0c0c0, #a0a0a0);
  color: #fff;
}

.store-row:nth-child(3) .store-rank {
  background: linear-gradient(135deg, #cd7f32, #b8860b);
  color: #fff;
}

.store-info {
  flex: 1;
  min-width: 0;
}

.store-name-text {
  font-size: 14px;
  font-weight: 500;
  color: #333;
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.store-detail {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.detail-item {
  font-size: 12px;
  color: #999;
}

.detail-item.gmv {
  color: #ff6b35;
  font-weight: 500;
}
</style>
