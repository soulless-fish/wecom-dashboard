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
        <div class="section-title section-title-with-action">
          <span>门店业绩{{ storesData.length > 1 ? ` (${index + 1}/${storesData.length})` : '' }}</span>
          <div class="section-actions">
            <button
              v-if="showJlyqButton(store, index)"
              class="jlyq-action-btn"
              type="button"
              @click="openJlyqPage(store)"
            >
              巨量引擎
            </button>
          </div>
        </div>
        <div class="info-list">
          <!-- 门店快捷入口集中展示在店铺名称上方，巨量引擎入口保持在标题栏。 -->
          <div class="store-quick-actions" aria-label="门店快捷入口">
            <button
              class="douyin-account-action-btn"
              type="button"
              @click="openDouyinAccountPage(store)"
            >
              抖音号
            </button>
            <button
              class="group-purchase-action-btn"
              type="button"
              @click="openGroupPurchasePage(store)"
            >
              团购链接
            </button>
          </div>
          <div class="info-item">
            <span class="info-label">店铺名称:</span>
            <span class="info-value store-name-value">
              <span class="store-name-text">{{ store.poi_name }}</span>
              <button
                class="copy-field-btn"
                type="button"
                :aria-label="`复制店铺名称${store.poi_name || ''}`"
                :disabled="!hasCopyValue(store.poi_name)"
                @click="copyStoreField(store.poi_name, `name-${index}`)"
              >
                {{ copiedField === `name-${index}` ? '已复制' : '复制' }}
              </button>
              <span
                v-if="store.business_status_text"
                :class="['business-status-text', getBusinessStatusClass(store)]"
              >
                {{ store.business_status_text }}
              </span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">门店电话:</span>
            <span class="info-value info-value-with-action">
              <span>{{ formatStorePhone(store.store_phone) }}</span>
              <button
                class="copy-field-btn"
                type="button"
                :aria-label="`复制门店电话${formatStorePhone(store.store_phone)}`"
                :disabled="!hasCopyValue(store.store_phone)"
                @click="copyStoreField(store.store_phone, `phone-${index}`)"
              >
                {{ copiedField === `phone-${index}` ? '已复制' : '复制' }}
              </button>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">店铺ID:</span>
            <span class="info-value info-value-with-action">
              <span>{{ store.poi_id }}</span>
              <button
                class="copy-field-btn"
                type="button"
                :aria-label="`复制店铺ID${store.poi_id || ''}`"
                :disabled="!hasCopyValue(store.poi_id)"
                @click="copyStoreField(store.poi_id, `id-${index}`)"
              >
                {{ copiedField === `id-${index}` ? '已复制' : '复制' }}
              </button>
            </span>
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
          <!-- 全国平均核销是全门店基准，和当前门店数据分开显示。 -->
          <div class="info-item">
            <span class="info-label">全国平均核销:</span>
            <span class="info-value">
              <span v-if="verifyAverageLoading" class="date-tag">加载中</span>
              <template v-else-if="verifyAverageData.current_month">
                <span class="highlight-orange">{{ getVerifyAverageDisplay('current_month') }}</span>
                <span class="date-tag">(当月)</span>
                <span class="metric-separator">;</span>
                <span class="highlight-orange">{{ getVerifyAverageDisplay('last_thirty_days') }}</span>
                <span class="date-tag">(近30天)</span>
              </template>
              <span v-else class="date-tag">{{ verifyAverageMessage || '暂未获取' }}</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">核销金额:</span>
            <span class="info-value">
              <span class="highlight-orange">{{ store.verify_amount_realtime || '¥0.00' }}</span>
              <span class="date-tag">(当月)</span>
              <span class="metric-separator">;</span>
              <span class="highlight-orange">{{ store.verify_amount || '¥0.00' }}</span>
              <span class="date-tag">(近30天)</span>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">核销券数:</span>
            <span class="info-value">
              <span class="highlight-green">{{ store.verify_cert_cnt_realtime ?? 0 }}</span>
              <span class="date-tag">(当月)</span>
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
      <div class="version-info">极修匠 v1.10.0</div>
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
import { useRouter } from 'vue-router'
import { jlyqApi, storePerformanceApi, wecomApi } from '../api'
import {
  isWeComEnvironment,
  initWeComSDK,
  initAgentConfig,
  getCurExternalChat,
  getWeComContext
} from '../utils/wecom'

// 状态
const router = useRouter()
const loading = ref(true)
const loadingText = ref('初始化中...')
const error = ref('')
const emptyText = ref('未匹配到门店')
const warningText = ref('')

// 数据
const chatId = ref('')
const groupName = ref('')
const storesData = ref([])  // 改为数组，支持多门店
const jlyqPermissionMap = ref({})
const verifyAverageData = ref({})
const verifyAverageLoading = ref(false)
const verifyAverageMessage = ref('')
const copiedField = ref('')

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

// 读取后端已经按两位小数格式化的平均核销金额。
function getVerifyAverageDisplay(periodKey) {
  return verifyAverageData.value?.[periodKey]?.average_verify_amount_display || '¥0.00'
}

// 判断复制按钮是否有实际文本，避免把“无”或空值写入剪贴板。
function hasCopyValue(value) {
  return String(value ?? '').trim() !== ''
}

// 复制门店基础字段，并只记录当前门店当前字段的短暂反馈状态。
async function copyStoreField(value, fieldKey) {
  const text = String(value ?? '').trim()
  if (!text) return

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    copiedField.value = fieldKey
    window.setTimeout(() => {
      if (copiedField.value === fieldKey) copiedField.value = ''
    }, 1500)
  } catch (err) {
    console.warn('复制门店字段失败:', err)
  }
}

// 基准值获取失败时保留门店主数据，不让整页进入错误状态。
async function loadVerifyAverage() {
  verifyAverageLoading.value = true
  verifyAverageData.value = {}
  verifyAverageMessage.value = ''
  try {
    const response = await storePerformanceApi.getVerifyAverage()
    if (response?.code === 0 && response.data) {
      verifyAverageData.value = response.data
    }
  } catch (err) {
    verifyAverageMessage.value = err?.response?.data?.detail || '暂未获取'
    console.warn('平均核销金额获取失败:', verifyAverageMessage.value)
  } finally {
    verifyAverageLoading.value = false
  }
}

function formatStorePhone(value) {
  if (value === null || value === undefined || value === '') return '无'
  return String(value)
}

function getBusinessStatusClass(store) {
  // 营业状态样式优先使用后端分类，缺失时按中文文案兜底判断。
  const statusClass = String(store?.business_status_class || '').trim()
  if (statusClass) return statusClass
  const text = String(store?.business_status_text || '')
  if (text.includes('正常营业')) return 'normal'
  if (text.includes('暂停营业')) return 'paused'
  if (text.includes('即将开业')) return 'upcoming'
  return ''
}

function formatDouyinPoiAccount(store) {
  // 经营抖音号来自来客抖音号管理接口，只展示子机构经营号。
  const accountName = String(store?.douyin_poi_account_name || '').trim()
  return accountName || '暂无'
}

function shortenDouyinPoiAccountStatus(value) {
  // 状态里可能带审核失败原因，侧边栏保留前 28 个字符，避免窄屏撑开。
  const text = String(value || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.length > 28 ? `${text.slice(0, 28)}...` : text
}

function getDouyinPoiAccountStatusClass(value) {
  // 已绑定展示为成功，其余状态统一用醒目的待处理样式。
  const text = String(value || '')
  return text.includes('已绑定') ? 'success' : 'warning'
}

function getStoreKey(store, index = 0) {
  // 用门店ID优先生成前端权限缓存键，缺失时再用门店名称兜底。
  return String(store?.poi_id || store?.poi_name || index)
}

function showJlyqButton(store, index) {
  // 只有权限表命中的外部群或门店才显示巨量引擎入口。
  const permission = jlyqPermissionMap.value[getStoreKey(store, index)]
  return Boolean(permission && permission.authorized)
}

async function loadJlyqPermissions() {
  // 门店数据加载完成后查询巨量权限，失败时只隐藏入口，不影响原门店业绩展示。
  const entries = await Promise.all((storesData.value || []).map(async (store, index) => {
    try {
      const res = await jlyqApi.getSidebarData({
        group_name: groupName.value,
        poi_name: store.poi_name || ''
      })
      const data = res?.data || {}
      return [
        getStoreKey(store, index),
        {
          authorized: Boolean(data.authorized),
          accountCount: Array.isArray(data.accounts) ? data.accounts.length : 0
        }
      ]
    } catch (err) {
      console.warn('巨量引擎权限查询失败:', err)
      return [getStoreKey(store, index), { authorized: false, accountCount: 0 }]
    }
  }))
  jlyqPermissionMap.value = Object.fromEntries(entries)
}

function openJlyqPage(store) {
  // 跳转详情页时带上群ID、群名和门店名，发送填写卡片时继续使用当前外部群上下文。
  router.push({
    path: '/jlyq',
    query: {
      chat_id: chatId.value || '',
      group_name: groupName.value || '',
      poi_name: store?.poi_name || ''
    }
  })
}

function openGroupPurchasePage(store) {
  // 团购链接每个门店都可查看，按门店ID读取命中的商品列表。
  router.push({
    path: '/group-purchase',
    query: {
      poi_id: store?.poi_id || '',
      poi_name: store?.poi_name || '',
      group_name: groupName.value || ''
    }
  })
}

function openDouyinAccountPage(store) {
  // 抖音号详情页按门店ID读取子机构经营号，并按门店名称匹配职人号。
  router.push({
    path: '/douyin-accounts',
    query: {
      poi_id: store?.poi_id || '',
      poi_name: store?.poi_name || '',
      group_name: groupName.value || ''
    }
  })
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
  verifyAverageData.value = {}
  verifyAverageMessage.value = ''

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
      // 门店匹配和全门店平均值并行请求，避免新增指标额外拉长侧边栏等待时间。
      const [matchRes] = await Promise.all([
        wecomApi.matchStore(groupName.value),
        loadVerifyAverage()
      ])

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

        if (storesData.value.length > 0) {
          await loadJlyqPermissions()
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

.section-title-with-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.section-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
}

.jlyq-action-btn {
  min-width: 82px;
  min-height: 30px;
  padding: 0 12px;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, #2f8cff 0%, #0f62fe 52%, #0647c9 100%);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  line-height: 30px;
  text-align: center;
  cursor: pointer;
  box-shadow: 0 8px 16px rgba(15, 98, 254, 0.24), inset 0 1px 0 rgba(255, 255, 255, 0.22);
  transition: transform 0.16s ease, box-shadow 0.16s ease, filter 0.16s ease;
  white-space: nowrap;
}

.group-purchase-action-btn {
  min-width: 86px;
  min-height: 30px;
  padding: 0 13px;
  border: none;
  border-radius: 999px;
  background: linear-gradient(135deg, #ff4f9a 0%, #e11d72 48%, #b9145c 100%);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  line-height: 30px;
  text-align: center;
  cursor: pointer;
  box-shadow: 0 8px 16px rgba(225, 29, 114, 0.26), inset 0 1px 0 rgba(255, 255, 255, 0.24);
  transition: transform 0.16s ease, box-shadow 0.16s ease, filter 0.16s ease, background 0.16s ease;
  white-space: nowrap;
}

.group-purchase-action-btn:hover {
  background: linear-gradient(135deg, #ff66aa 0%, #f02b83 48%, #c51666 100%);
  filter: saturate(1.08) brightness(1.03);
}

.group-purchase-action-btn:active {
  transform: translateY(2px) scale(0.97);
  box-shadow: 0 4px 10px rgba(225, 29, 114, 0.22), inset 0 2px 5px rgba(103, 10, 49, 0.28);
  filter: saturate(1.12);
}

.douyin-account-action-btn {
  min-width: 86px;
  min-height: 30px;
  padding: 0 13px;
  border: none;
  border-radius: 999px;
  background: linear-gradient(135deg, #b7f34b 0%, #22c55e 48%, #06b6d4 100%);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  line-height: 30px;
  text-align: center;
  cursor: pointer;
  box-shadow: 0 8px 16px rgba(34, 197, 94, 0.24), inset 0 1px 0 rgba(255, 255, 255, 0.24);
  transition: transform 0.16s ease, box-shadow 0.16s ease, filter 0.16s ease, background 0.16s ease;
  white-space: nowrap;
}

.douyin-account-action-btn:hover {
  background: linear-gradient(135deg, #c7ff61 0%, #2ddf73 48%, #0fc9df 100%);
  filter: saturate(1.08) brightness(1.03);
}

.douyin-account-action-btn:active {
  transform: translateY(2px) scale(0.97);
  box-shadow: 0 4px 10px rgba(34, 197, 94, 0.22), inset 0 2px 5px rgba(10, 105, 70, 0.26);
  filter: saturate(1.12);
}

.jlyq-action-btn:active {
  transform: translateY(2px) scale(0.98);
  box-shadow: 0 4px 10px rgba(15, 98, 254, 0.22), inset 0 2px 4px rgba(0, 36, 120, 0.24);
  filter: saturate(1.06);
}

.info-list {
  padding: 12px 16px;
}

.store-quick-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 10px;
  margin-bottom: 2px;
  border-bottom: 1px dashed #f0f0f0;
}

.store-quick-actions .douyin-account-action-btn,
.store-quick-actions .group-purchase-action-btn {
  min-width: 0;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 28px;
  box-shadow: none;
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

.store-name-value {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0 8px;
}

.store-name-text {
  word-break: break-all;
}

.info-value-with-action {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

/* 基础信息复制按钮保持紧凑，避免挤压侧边栏数据。 */
.copy-field-btn {
  padding: 1px 7px;
  border: 1px solid #91caff;
  border-radius: 3px;
  background: #e6f4ff;
  color: #1677ff;
  font-size: 11px;
  line-height: 18px;
  cursor: pointer;
  white-space: nowrap;
}

.copy-field-btn:hover:not(:disabled) {
  background: #bae0ff;
}

.copy-field-btn:active:not(:disabled) {
  transform: translateY(1px);
}

.copy-field-btn:disabled {
  border-color: #d9d9d9;
  background: #f5f5f5;
  color: #bfbfbf;
  cursor: not-allowed;
}

.business-status-text {
  font-weight: 700;
  white-space: nowrap;
}

.business-status-text.normal {
  color: #22a35a;
}

.business-status-text.paused {
  color: #f5222d;
}

.business-status-text.upcoming {
  color: #d48806;
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
