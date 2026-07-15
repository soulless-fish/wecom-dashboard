<template>
  <div class="jlyq-page">
    <div class="jlyq-topbar">
      <button class="back-btn" type="button" @click="goBack">返回</button>
      <div class="topbar-text">
        <div class="page-title">巨量引擎</div>
        <div class="page-subtitle">{{ groupName || poiName || '本地推数据' }}</div>
      </div>
    </div>

    <div v-if="loading" class="state-box">
      <div class="loading-spinner"></div>
      <span>{{ loadingText }}</span>
    </div>

    <div v-else-if="error" class="state-box error-text">
      <div>{{ error }}</div>
      <button class="copy-btn retry-action" type="button" @click="loadData">重试</button>
    </div>

    <div v-else-if="!authorized" class="state-box">
      <div>当前外部群未配置巨量引擎权限</div>
    </div>

    <div v-else-if="accounts.length === 0" class="state-box">
      <div>当前权限表未配置可展示的巨量账号</div>
    </div>

    <template v-else>
      <div class="account-switcher">
        <button
          v-for="account in accounts"
          :key="account.binding_key"
          :class="['account-chip', { active: selectedKey === account.binding_key }]"
          type="button"
          @click="selectedKey = account.binding_key"
        >
          <span class="account-name">{{ account.account_name }}</span>
          <span v-if="!account.has_data" class="sync-tag">未同步</span>
        </button>
      </div>

      <section v-if="selectedAccount" class="template-section">
        <div class="template-title">{{ selectedAccount.account_name }}</div>
        <div v-if="!selectedAccount.has_data" class="sync-warning">当前账号暂无同步数据</div>
        <pre class="template-text">{{ selectedAccount.template_text }}</pre>
        <button
          class="copy-btn"
          type="button"
          :disabled="!selectedAccount.has_data"
          @click="copyTemplate"
        >
          {{ selectedAccount.has_data ? copyButtonText : '未同步' }}
        </button>
      </section>

      <section v-if="selectedAccount" class="collection-section">
        <div class="collection-heading">
          <div>
            <div class="collection-title">商家每日数据填写</div>
            <div class="collection-subtitle">商家提交后，三项月累计会自动更新到上方模板</div>
          </div>
          <span v-if="selectedAccount.collection?.form" class="collection-status">
            {{ collectionStatusText(selectedAccount.collection.form.status) }}
          </span>
        </div>

        <div class="collection-summary">
          <div>
            <span>月加微信累计</span>
            <strong>{{ selectedAccount.collection?.month?.wechat_count || 0 }}</strong>
          </div>
          <div>
            <span>月累计回收量</span>
            <strong>{{ selectedAccount.collection?.month?.recycle_count || 0 }}</strong>
          </div>
          <div>
            <span>月累计销售量</span>
            <strong>{{ selectedAccount.collection?.month?.sales_count || 0 }}</strong>
          </div>
        </div>

        <div
          v-if="selectedAccount.collection?.form?.permission_notice"
          class="permission-notice"
        >
          {{ selectedAccount.collection.form.permission_notice }}
        </div>

        <div class="collection-actions">
          <button
            class="document-btn"
            type="button"
            :disabled="sendingDocument"
            @click="createAndSendDocument"
          >
            {{ sendingDocument ? '正在创建并发送...' : '发送填写文档' }}
          </button>
          <button
            class="sync-btn"
            type="button"
            :disabled="syncingForms"
            @click="syncCollectionData"
          >
            {{ syncingForms ? '同步中...' : '同步填写数据' }}
          </button>
        </div>

        <div v-if="collectionMessage" class="collection-message">{{ collectionMessage }}</div>
        <div v-if="collectionError" class="collection-message collection-error">{{ collectionError }}</div>
      </section>

      <section v-if="selectedAccount" class="supplement-section">
        <div class="supplement-item">
          <span>账户余额（日）：</span>
          <strong>{{ selectedAccount.supplemental.day_balance_yuan }}</strong>
        </div>
        <div class="supplement-item">
          <span>账户余额（月）：</span>
          <strong>{{ selectedAccount.supplemental.month_balance_yuan }}</strong>
        </div>
        <div class="supplement-item">
          <span>转化成本（日）：</span>
          <strong>{{ selectedAccount.supplemental.day_conversion_cost_yuan }}</strong>
        </div>
        <div class="supplement-item">
          <span>转化成本（月）：</span>
          <strong>{{ selectedAccount.supplemental.month_conversion_cost_yuan }}</strong>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { jlyqApi } from '../api'
import {
  initAgentConfig,
  initWeComSDK,
  isWeComEnvironment,
  sendChatNewsMessage
} from '../utils/wecom'

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const loadingText = ref('加载巨量引擎数据...')
const error = ref('')
const authorized = ref(false)
const accounts = ref([])
const selectedKey = ref('')
const copyButtonText = ref('复制内容')
const copyTimer = ref(null)
const sendingDocument = ref(false)
const syncingForms = ref(false)
const collectionMessage = ref('')
const collectionError = ref('')
const refreshTimer = ref(null)

const chatId = computed(() => String(route.query.chat_id || ''))
const groupName = computed(() => String(route.query.group_name || ''))
const poiName = computed(() => String(route.query.poi_name || ''))
const selectedAccount = computed(() => {
  return accounts.value.find(item => item.binding_key === selectedKey.value) || accounts.value[0] || null
})

function goBack() {
  router.back()
}

function resetCopyTextLater() {
  // 复制成功后短暂显示状态，再恢复为可再次点击的按钮文案。
  if (copyTimer.value) {
    window.clearTimeout(copyTimer.value)
  }
  copyTimer.value = window.setTimeout(() => {
    copyButtonText.value = '复制内容'
    copyTimer.value = null
  }, 1600)
}

function copyByTextarea(text) {
  // 兼容部分企业微信内置浏览器没有 navigator.clipboard 的情况。
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', 'readonly')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  document.body.removeChild(textarea)
}

async function copyTemplate() {
  const text = selectedAccount.value?.template_text || ''
  if (!text) return
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      copyByTextarea(text)
    }
    copyButtonText.value = '已复制'
    resetCopyTextLater()
  } catch (err) {
    console.warn('复制失败，尝试降级复制:', err)
    copyByTextarea(text)
    copyButtonText.value = '已复制'
    resetCopyTextLater()
  }
}

function collectionStatusText(status) {
  // 把后端状态转换成商务能直接理解的短提示。
  const statusMap = {
    created: '已创建',
    sent: '已发送，待填写',
    submitted: '已填写并更新',
    sync_error: '同步异常'
  }
  return statusMap[String(status || '')] || '待发送'
}

async function sendDocumentCard(formData) {
  // 路由跳转后通常仍保留企业微信配置；首次发送失败时重新初始化后再尝试一次。
  const payload = {
    link: formData.share_url,
    title: formData.form_title || '极修匠经营数据填写',
    desc: '请填写加微信量、手机回收量、手机销售量和本月抖音引流利润',
    imgUrl: formData.card_image_url
  }
  try {
    await sendChatNewsMessage(payload)
  } catch (firstError) {
    console.warn('首次发送填写卡片失败，重新初始化企业微信配置:', firstError)
    await initWeComSDK()
    await initAgentConfig()
    await sendChatNewsMessage(payload)
  }
}

async function createAndSendDocument() {
  const account = selectedAccount.value
  if (!account || sendingDocument.value) return
  sendingDocument.value = true
  collectionMessage.value = ''
  collectionError.value = ''
  let createdForm = null
  try {
    const response = await jlyqApi.createCollectionForm({
      chat_id: chatId.value,
      group_name: groupName.value,
      poi_name: poiName.value,
      binding_key: account.binding_key,
      account_id: account.account_id || '',
      account_name: account.account_name || ''
    })
    createdForm = response?.data || null
    if (!createdForm?.share_url) {
      throw new Error('填写文档未返回可发送链接')
    }

    if (isWeComEnvironment()) {
      await sendDocumentCard(createdForm)
      await jlyqApi.markCollectionFormSent(createdForm.id)
      collectionMessage.value = createdForm.channel === 'wecom_form'
        ? '企业微信收集表已发送到当前外部群'
        : '填写卡片已发送到当前外部群，商家提交后会自动更新累计'
    } else {
      copyByTextarea(createdForm.share_url)
      collectionMessage.value = '非企业微信环境无法直接发送，填写链接已复制'
    }
    await loadData()
  } catch (err) {
    console.error('创建或发送填写文档失败:', err)
    if (createdForm?.share_url) {
      copyByTextarea(createdForm.share_url)
      collectionError.value = '卡片发送失败，填写链接已复制，可返回群聊后粘贴发送'
    } else {
      collectionError.value = err?.response?.data?.detail || err.message || '创建填写文档失败'
    }
  } finally {
    sendingDocument.value = false
  }
}

async function syncCollectionData() {
  const account = selectedAccount.value
  if (!account || syncingForms.value) return
  syncingForms.value = true
  collectionMessage.value = ''
  collectionError.value = ''
  try {
    const response = await jlyqApi.syncCollectionForms([account.binding_key])
    const data = response?.data || {}
    await loadData()
    collectionMessage.value = data.checked > 0
      ? `同步完成，检查${data.checked}份文档，更新${data.updated}份`
      : '安全网页填写数据会实时更新，当前没有待同步的官方收集表'
  } catch (err) {
    console.error('同步填写数据失败:', err)
    collectionError.value = err?.response?.data?.detail || err.message || '同步填写数据失败'
  } finally {
    syncingForms.value = false
  }
}

async function fetchData(quiet = false) {
  if (!quiet) {
    loading.value = true
    error.value = ''
  }
  try {
    const res = await jlyqApi.getSidebarData({
      group_name: groupName.value,
      poi_name: poiName.value
    })
    const data = res?.data || {}
    authorized.value = Boolean(data.authorized)
    accounts.value = Array.isArray(data.accounts) ? data.accounts : []
    const previousKey = selectedKey.value
    selectedKey.value = accounts.value.some(item => item.binding_key === previousKey)
      ? previousKey
      : (accounts.value[0]?.binding_key || '')
  } catch (err) {
    console.error('加载巨量引擎数据失败:', err)
    if (!quiet) {
      error.value = err.message || '加载失败'
    }
  } finally {
    if (!quiet) {
      loading.value = false
    }
  }
}

async function loadData() {
  return fetchData(false)
}

watch(selectedKey, () => {
  collectionMessage.value = ''
  collectionError.value = ''
})

onMounted(() => {
  loadData()
  // 页面停留期间定期刷新，商家提交后商务无需手动重开页面即可看到累计更新。
  refreshTimer.value = window.setInterval(() => {
    fetchData(true)
  }, 60000)
})

onBeforeUnmount(() => {
  if (refreshTimer.value) {
    window.clearInterval(refreshTimer.value)
    refreshTimer.value = null
  }
})
</script>

<style scoped>
.jlyq-page {
  min-height: 100vh;
  padding: 12px;
  background: #f4f7fb;
  color: #172033;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.jlyq-topbar {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 54px;
  padding: 8px 0 14px;
}

.back-btn {
  min-width: 58px;
  height: 32px;
  border: 1px solid #c8d7ee;
  border-radius: 8px;
  background: #fff;
  color: #2563eb;
  font-size: 13px;
  font-weight: 600;
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
  font-weight: 700;
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
  border: 1px solid #dbe6f5;
  border-radius: 8px;
  background: #fff;
  color: #667085;
  text-align: center;
}

.loading-spinner {
  width: 24px;
  height: 24px;
  margin: 0 auto 12px;
  border: 2px solid #dbe6f5;
  border-top-color: #1d74f5;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.error-text {
  color: #d92d20;
}

.retry-action {
  margin-top: 14px;
}

.account-switcher {
  display: flex;
  gap: 8px;
  padding: 2px 0 10px;
  overflow-x: auto;
}

.account-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  max-width: 240px;
  padding: 0 12px;
  border: 1px solid #c8d7ee;
  border-radius: 8px;
  background: #fff;
  color: #1d2939;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: transform 0.16s ease, border-color 0.16s ease, background 0.16s ease;
}

.account-chip.active {
  border-color: #1d74f5;
  background: #e8f1ff;
  color: #0f62fe;
}

.account-chip:active {
  transform: translateY(1px) scale(0.99);
}

.account-name {
  overflow: hidden;
  text-overflow: ellipsis;
}

.sync-tag {
  flex: 0 0 auto;
  padding: 1px 5px;
  border-radius: 5px;
  background: #fff3cd;
  color: #946200;
  font-size: 11px;
  line-height: 18px;
}

.sync-warning {
  margin-bottom: 10px;
  padding: 8px 10px;
  border: 1px solid #fedf89;
  border-radius: 8px;
  background: #fffbeb;
  color: #b54708;
  font-size: 13px;
  line-height: 1.5;
}

.template-section,
.collection-section,
.supplement-section {
  border: 1px solid #dbe6f5;
  border-radius: 8px;
  background: #fff;
}

.template-section {
  padding: 14px;
}

.collection-section {
  margin-top: 12px;
  padding: 14px;
}

.collection-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.collection-title {
  color: #172033;
  font-size: 14px;
  font-weight: 800;
  line-height: 1.5;
}

.collection-subtitle {
  margin-top: 2px;
  color: #667085;
  font-size: 12px;
  line-height: 1.5;
}

.collection-status {
  flex: 0 0 auto;
  padding: 3px 7px;
  border-radius: 6px;
  background: #e8f1ff;
  color: #0f62fe;
  font-size: 11px;
  font-weight: 700;
  line-height: 18px;
}

.collection-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
  margin-top: 12px;
}

.collection-summary div {
  min-width: 0;
  padding: 9px 7px;
  border-radius: 8px;
  background: #f6f8fb;
  text-align: center;
}

.collection-summary span,
.collection-summary strong {
  display: block;
}

.collection-summary span {
  min-height: 32px;
  color: #667085;
  font-size: 10px;
  line-height: 1.4;
}

.collection-summary strong {
  margin-top: 3px;
  color: #111827;
  font-size: 17px;
}

.permission-notice {
  margin-top: 10px;
  padding: 8px 9px;
  border: 1px solid #fedf89;
  border-radius: 8px;
  background: #fffbeb;
  color: #9a6700;
  font-size: 11px;
  line-height: 1.6;
}

.collection-actions {
  display: grid;
  grid-template-columns: 1.25fr 1fr;
  gap: 8px;
  margin-top: 12px;
}

.document-btn,
.sync-btn {
  min-height: 38px;
  padding: 0 10px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 750;
  cursor: pointer;
}

.document-btn {
  border: none;
  background: linear-gradient(135deg, #2f8cff, #0f62fe 58%, #0647c9);
  color: #fff;
  box-shadow: 0 7px 14px rgba(15, 98, 254, 0.2);
}

.sync-btn {
  border: 1px solid #b9d4ff;
  background: #f1f6ff;
  color: #0f62fe;
}

.document-btn:disabled,
.sync-btn:disabled {
  cursor: wait;
  opacity: 0.6;
}

.collection-message {
  margin-top: 9px;
  color: #18794e;
  font-size: 12px;
  line-height: 1.6;
}

.collection-error {
  color: #d92d20;
}

.template-title {
  margin-bottom: 10px;
  color: #0f62fe;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.4;
  word-break: break-all;
}

.template-text {
  margin: 0;
  color: #111827;
  font-family: inherit;
  font-size: 15px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}

.copy-btn {
  min-width: 92px;
  min-height: 34px;
  margin-top: 14px;
  padding: 0 16px;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, #2f8cff 0%, #0f62fe 54%, #0647c9 100%);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  line-height: 34px;
  cursor: pointer;
  box-shadow: 0 8px 16px rgba(15, 98, 254, 0.24), inset 0 1px 0 rgba(255, 255, 255, 0.22);
  transition: transform 0.16s ease, box-shadow 0.16s ease, filter 0.16s ease;
}

.copy-btn:active {
  transform: translateY(2px) scale(0.98);
  box-shadow: 0 4px 10px rgba(15, 98, 254, 0.22), inset 0 2px 4px rgba(0, 36, 120, 0.24);
  filter: saturate(1.06);
}

.copy-btn:disabled {
  cursor: not-allowed;
  background: #98a2b3;
  box-shadow: none;
  transform: none;
}

.supplement-section {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0;
  margin-top: 12px;
  padding: 8px 14px;
}

.supplement-item {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px dashed #edf2f7;
  color: #475467;
  font-size: 14px;
  line-height: 1.5;
}

.supplement-item:last-child {
  border-bottom: none;
}

.supplement-item strong {
  color: #111827;
  font-weight: 700;
  white-space: nowrap;
}
</style>
