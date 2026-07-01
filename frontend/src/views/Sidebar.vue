<template>
  <div class="sidebar-container">
    <!-- 头部 -->
    <div class="header">企业微信</div>

    <!-- 群名称 -->
    <div class="group-name" v-if="groupName">{{ groupName }}</div>

    <!-- 加载状态 -->
    <div v-if="loading" class="loading-box">
      <div class="loading-spinner"></div>
      <span>{{ loadingText }}</span>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-box">
      <div class="error-message">{{ error }}</div>
      <button class="retry-btn" @click="initSidebar">重试</button>
    </div>

    <!-- 未匹配到门店 -->
    <div v-else-if="!storesData || storesData.length === 0" class="empty-box">
      <div>{{ emptyText }}</div>
    </div>

    <!-- 门店数据（支持多门店） -->
    <template v-else>
      <!-- 警告提示（部分门店未找到） -->
      <div v-if="warningText" class="warning-box">
        <span class="warning-icon">⚠</span>
        <span>{{ warningText }}</span>
      </div>

      <!-- 循环显示每个门店 -->
      <div v-for="(store, index) in storesData" :key="store.poi_id || index" class="store-info-box">
        <div class="section-title">门店业绩{{ storesData.length > 1 ? ` (${index + 1}/${storesData.length})` : '' }}</div>
        <div class="info-list">
          <div class="info-item">
            <span class="info-label">店铺名称:</span>
            <span class="info-value">{{ store.poi_name }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">电脑清灰:</span>
            <span class="info-value">
              <span v-if="store.computer_cleaning_opened" class="status-tag success computer-cleaning-tag">已开通</span>
              <span v-else class="status-tag danger computer-cleaning-tag">未开通</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">门店电话:</span>
            <span class="info-value">{{ formatStorePhone(store.store_phone) }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">店铺ID:</span>
            <span class="info-value">{{ store.poi_id }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">店铺评分:</span>
            <span class="info-value highlight-blue">{{ formatPoiScore(store.poi_score) }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">数据范围:</span>
            <span class="info-value">{{ getDataRangeText(store) }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">上翻收益:</span>
            <span class="info-value highlight-orange">{{ formatMoney(store.gmv_yuan) }} 元</span>
          </div>
          <div class="info-item">
            <span class="info-label">直播时长:</span>
            <span class="info-value highlight-blue">{{ store.live_duration_formatted || '0天0小时0分钟' }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">视频数量:</span>
            <span class="info-value">
              <span class="highlight-green">{{ store.video_cnt_1d ?? 0 }}</span>
              <span v-if="(store.video_cnt_1d ?? 0) >= 15" class="status-tag success">已达标</span>
              <span v-else class="status-tag warning">未达标</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">核销金额:</span>
            <span class="info-value">
              <span class="highlight-orange">{{ store.verify_amount_realtime || '¥0.00' }}</span>
              <span class="date-tag">(实时)</span>
              <span class="metric-separator">;</span>
              <span class="highlight-orange">{{ store.verify_amount || '¥0.00' }}</span>
              <span class="date-tag">(近30天)</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">核销券数:</span>
            <span class="info-value">
              <span class="highlight-green">{{ store.verify_cert_cnt_realtime ?? 0 }}</span>
              <span class="date-tag">(实时)</span>
              <span class="metric-separator">;</span>
              <span class="highlight-green">{{ store.verify_cert_cnt ?? 0 }}</span>
              <span class="date-tag">(近30天)</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">已核销订单:</span>
            <span class="info-value">
              <span class="date-tag fanke-range-tag">({{ store.douyin_order_date_range || '近30天' }}数据)</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">电池核销数:</span>
            <span class="info-value">
              <span class="highlight-green">{{ store.douyin_battery_order_count ?? 0 }}</span>
              <span class="date-tag">(含苹果安卓)</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">电芯核销数:</span>
            <span class="info-value">
              <span class="highlight-green">{{ store.douyin_cell_order_count ?? 0 }}</span>
              <span class="date-tag">(含苹果安卓)</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">凡科订单:</span>
            <span class="info-value">
              <span class="date-tag fanke-range-tag">({{ store.fanke_order_date_range || '近30天' }}数据)</span>
            </span>
          </div>
          <!-- 凡科金额只读取凡科订单汇总字段，避免误展示抖音来客已核销金额。 -->
          <div class="info-item">
            <span class="info-label">电池总金额:</span>
            <span class="info-value">
              <span v-if="store.fanke_phone_matched" class="highlight-orange">¥{{ formatFankeMoney(store.fanke_battery_amount) }}</span>
              <span v-if="store.fanke_phone_matched" class="date-tag">(含苹果安卓)</span>
              <span v-else class="fanke-mismatch">电话不一致</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">电芯总金额:</span>
            <span class="info-value">
              <span v-if="store.fanke_phone_matched" class="highlight-orange">¥{{ formatFankeMoney(store.fanke_cell_amount) }}</span>
              <span v-if="store.fanke_phone_matched" class="date-tag">(含苹果安卓)</span>
              <span v-else class="fanke-mismatch">电话不一致</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">电池订单数:</span>
            <span class="info-value">
              <span v-if="store.fanke_phone_matched" class="highlight-green">{{ store.fanke_battery_order_count ?? 0 }}</span>
              <span v-if="store.fanke_phone_matched" class="date-tag">(含苹果安卓)</span>
              <span v-else class="fanke-mismatch">电话不一致</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">电芯订单数:</span>
            <span class="info-value">
              <span v-if="store.fanke_phone_matched" class="highlight-green">{{ store.fanke_cell_order_count ?? 0 }}</span>
              <span v-if="store.fanke_phone_matched" class="date-tag">(含苹果安卓)</span>
              <span v-else class="fanke-mismatch">电话不一致</span>
            </span>
          </div>
          <div class="info-tip" v-if="index === storesData.length - 1">
            <span class="tip-text">提示: </span>
            <span class="tip-content highlight-red">受最新抖音来客规则限制，平台最新数据的更新时间会晚于每日的 11 点20分钟</span>
          </div>
        </div>
      </div>

      <!-- 版本号 -->
      <div class="version-info">极修匠 v1.9.0</div>
    </template>

    <!-- 调试信息（开发环境显示） -->
    <div v-if="debugMode" class="debug-panel">
      <div class="debug-title">调试信息</div>
      <div class="debug-item">群聊ID: {{ chatId || '未获取' }}</div>
      <div class="debug-item">群名称: {{ groupName || '未获取' }}</div>
      <div class="debug-item">环境: {{ isWeCom ? '企业微信' : '浏览器' }}</div>
      <div class="debug-item">门店数: {{ storesData ? storesData.length : 0 }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { wecomApi } from '../api'
import {
  isWeComEnvironment,
  initWeComSDK,
  initAgentConfig,
  getCurExternalChat,
  getWeComContext
} from '../utils/wecom'

// 状态
const loading = ref(true)
const loadingText = ref('初始化中...')
const error = ref('')
const emptyText = ref('未匹配到门店')
const warningText = ref('')

// 数据
const chatId = ref('')
const groupName = ref('')
const storesData = ref([])  // 改为数组，支持多门店

// 环境检测
const isWeCom = ref(false)
const debugMode = ref(false)

// 计算数据范围文本（改为函数，支持多门店）
function getDataRangeText(store) {
  if (!store) return ''
  const { data_month, data_start_day, data_end_day } = store
  if (data_month && data_start_day && data_end_day) {
    // 提取月份数字
    const month = data_month.split('-')[1] || data_month
    return `${parseInt(month)}月, ${data_start_day}~${data_end_day}号`
  }
  return data_month || ''
}

// 格式化金额
function formatMoney(value) {
  if (!value) return '0.00'
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 2
  })
}

// 格式化凡科订单金额，保证侧边栏固定展示两位小数。
function formatFankeMoney(value) {
  if (value === null || value === undefined || value === '') return '0.00'
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) return '0.00'
  return numericValue.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  })
}

function formatPoiScore(value) {
  if (value === null || value === undefined || value === '') return '-'
  const numericValue = Number(value)
  return Number.isFinite(numericValue) ? numericValue.toFixed(2) : '-'
}

function formatStorePhone(value) {
  if (value === null || value === undefined || value === '') return '无'
  return String(value)
}

// 格式化抖音订单时间，侧边栏只展示月日和时分。
function formatDouyinTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${month}-${day} ${hour}:${minute}`
}

// 缩短商品名称，防止最近订单列表在窄屏里挤压布局。
function shortenOrderName(value) {
  const text = String(value || '').trim()
  if (!text) return '未命名商品'
  return text.length > 32 ? `${text.slice(0, 32)}...` : text
}

// 初始化侧边栏
async function initSidebar() {
  loading.value = true
  error.value = ''
  warningText.value = ''

  try {
    isWeCom.value = isWeComEnvironment()

    if (isWeCom.value) {
      // 企业微信环境
      loadingText.value = '初始化企业微信SDK...'

      try {
        // 初始化SDK
        await initWeComSDK()
        await initAgentConfig()

        // 获取群聊ID
        loadingText.value = '获取群聊信息...'
        chatId.value = await getCurExternalChat()

        // 获取群聊详情
        if (chatId.value) {
          const groupRes = await wecomApi.getGroupInfo(chatId.value)
          if (groupRes && groupRes.name) {
            groupName.value = groupRes.name || ''
          }
        }
      } catch (sdkError) {
        console.warn('SDK初始化失败，尝试使用ww.getContext:', sdkError)

        // 尝试使用ww.getContext获取上下文
        try {
          const context = await getWeComContext()
          if (context && context.entry === 'group_chat_tools') {
            chatId.value = context.chatId || ''
            // 获取群聊详情
            if (chatId.value) {
              const groupRes = await wecomApi.getGroupInfo(chatId.value)
              if (groupRes && groupRes.group_chat) {
                groupName.value = groupRes.group_chat.name || ''
              }
            }
          }
        } catch (contextError) {
          console.error('getContext失败:', contextError)
        }
      }
    } else {
      // 非企业微信环境（测试用）
      debugMode.value = true

      // 从URL参数获取测试数据
      const urlParams = new URLSearchParams(window.location.search)
      groupName.value = urlParams.get('group_name') || ''

      if (!groupName.value) {
        emptyText.value = '请在企业微信中打开'
        loading.value = false
        return
      }
    }

    // 根据群名匹配门店
    if (groupName.value) {
      loadingText.value = '匹配门店数据...'
      const matchRes = await wecomApi.matchStore(groupName.value)

      if (matchRes.code === 0) {
        // 优先使用stores数组，向后兼容data字段
        if (matchRes.stores && matchRes.stores.length > 0) {
          storesData.value = matchRes.stores
        } else if (matchRes.data) {
          storesData.value = [matchRes.data]
        }

        // 显示警告（部分门店未找到）
        if (matchRes.warning) {
          warningText.value = matchRes.warning
        }
      } else {
        emptyText.value = matchRes.message || '未匹配到门店'
      }
    } else {
      emptyText.value = '未获取到群名称'
    }

  } catch (err) {
    console.error('初始化失败:', err)
    error.value = err.message || '初始化失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  initSidebar()
})
</script>

<style scoped>
.sidebar-container {
  min-height: 100vh;
  background: #f0f0f0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.header {
  background: #fff;
  color: #1890ff;
  text-align: center;
  padding: 12px;
  font-size: 16px;
  border-bottom: 1px solid #e8e8e8;
}

.group-name {
  background: #fff;
  padding: 12px 16px;
  font-size: 15px;
  color: #1890ff;
  border-bottom: 1px solid #e8e8e8;
}

.warning-box {
  background: #fffbe6;
  margin: 12px;
  padding: 10px 16px;
  border: 1px solid #ffe58f;
  border-radius: 4px;
  font-size: 13px;
  color: #d48806;
}

.warning-icon {
  margin-right: 6px;
}

.store-info-box {
  background: #fff;
  margin: 12px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
}

.section-title {
  background: #fafafa;
  padding: 10px 16px;
  font-size: 14px;
  font-weight: 500;
  color: #333;
  border-bottom: 1px solid #e8e8e8;
}

.info-list {
  padding: 12px 16px;
}

.info-item {
  display: flex;
  padding: 8px 0;
  font-size: 14px;
  line-height: 1.5;
  border-bottom: 1px dashed #f0f0f0;
}

.info-item:last-of-type {
  border-bottom: none;
}

.info-label {
  color: #666;
  min-width: 80px;
  flex-shrink: 0;
}

.info-value {
  color: #333;
  word-break: break-all;
}

.highlight-orange {
  color: #fa8c16;
  font-weight: 500;
}

.highlight-blue {
  color: #1890ff;
  font-weight: 500;
}

.highlight-green {
  color: #52c41a;
  font-weight: 500;
}

.highlight-red {
  color: #f5222d;
}

.fanke-mismatch {
  color: #f5222d;
  font-weight: 500;
}

.recent-order-list {
  margin: 2px 0 8px 80px;
  padding: 6px 0 2px;
  border-bottom: 1px dashed #f0f0f0;
}

.recent-order-item {
  padding: 4px 0;
}

.recent-order-title {
  color: #333;
  font-size: 13px;
  line-height: 1.4;
  word-break: break-all;
}

.recent-order-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  margin-top: 2px;
  color: #999;
  font-size: 12px;
  line-height: 1.4;
}

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  margin-left: 8px;
  font-size: 12px;
  border-radius: 4px;
}

.status-tag.success {
  background: #f6ffed;
  color: #52c41a;
  border: 1px solid #b7eb8f;
}

.status-tag.warning {
  background: #fff7e6;
  color: #fa8c16;
  border: 1px solid #ffd591;
}

.status-tag.danger {
  background: #fff1f0;
  color: #f5222d;
  border: 1px solid #ffa39e;
}

.computer-cleaning-tag {
  margin-left: 0;
}

.date-tag {
  color: #999;
  font-size: 12px;
  margin-left: 6px;
}

.fanke-range-tag {
  margin-left: 0;
}

.metric-separator {
  color: #bbb;
  margin: 0 6px;
}

.info-tip {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #f0f0f0;
  font-size: 13px;
  line-height: 1.6;
}

.tip-text {
  color: #666;
}

.tip-content {
  color: #f5222d;
}

.version-info {
  text-align: center;
  padding: 20px 16px;
  color: #999;
  font-size: 12px;
}

/* 加载、错误、空状态 */
.loading-box,
.error-box,
.empty-box {
  background: #fff;
  margin: 12px;
  padding: 40px 20px;
  text-align: center;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
}

.loading-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid #f3f3f3;
  border-top: 2px solid #1890ff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin: 0 auto 12px;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.error-message {
  color: #f5222d;
  margin-bottom: 12px;
}

.retry-btn {
  padding: 6px 16px;
  background: #1890ff;
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

/* 调试面板 */
.debug-panel {
  background: #fffbe6;
  margin: 12px;
  padding: 12px;
  border: 1px solid #ffe58f;
  border-radius: 4px;
  font-size: 12px;
}

.debug-title {
  font-weight: 500;
  margin-bottom: 8px;
  color: #d48806;
}

.debug-item {
  color: #666;
  margin-bottom: 4px;
}
</style>
