<template>
  <main class="form-page">
    <section class="form-card">
      <div class="brand-row">
        <div class="brand-mark">极</div>
        <div>
          <div class="brand-title">极修匠经营数据填写</div>
          <div class="brand-subtitle">填写结果会自动更新巨量引擎月累计</div>
        </div>
      </div>

      <div v-if="loading" class="state-box">正在加载填写表...</div>
      <div v-else-if="error" class="state-box error-text">
        <div>{{ error }}</div>
        <button class="secondary-btn" type="button" @click="loadForm">重新加载</button>
      </div>

      <template v-else-if="formInfo">
        <div class="form-heading">
          <h1>{{ formInfo.form_title }}</h1>
          <div class="context-line">数据日期：{{ formInfo.data_date }}</div>
          <div class="context-line">账号：{{ formInfo.account_name || formInfo.store_name }}</div>
        </div>

        <div class="description-box">{{ formInfo.description }}</div>

        <form @submit.prevent="submitForm">
          <label class="field-item">
            <span>{{ questionAt(0, '③加微信量：') }}</span>
            <input v-model="values.wechat_count" type="number" min="0" step="1" inputmode="numeric" required>
          </label>

          <label class="field-item">
            <span>{{ questionAt(1, '④手机回收量：') }}</span>
            <input v-model="values.recycle_count" type="number" min="0" step="1" inputmode="numeric" required>
          </label>

          <label class="field-item">
            <span>{{ questionAt(2, '⑤手机销售量：') }}</span>
            <input v-model="values.sales_count" type="number" min="0" step="1" inputmode="numeric" required>
          </label>

          <label class="field-item">
            <span>{{ questionAt(3, '本月通过抖音引流的总利润（不用减投流本金）：') }}</span>
            <input v-model="values.profit_yuan" type="number" min="0" step="0.01" inputmode="decimal" required>
          </label>

          <div v-if="formInfo.submitted" class="submitted-tip">
            当天数据已提交；再次保存会覆盖当天记录，不会重复累计。
          </div>

          <button class="submit-btn" type="submit" :disabled="submitting">
            {{ submitting ? '正在保存...' : (formInfo.submitted ? '更新当天数据' : '提交数据') }}
          </button>
        </form>

        <div v-if="successMessage" class="success-box">
          <div class="success-title">{{ successMessage }}</div>
          <div class="summary-grid">
            <div><span>月加微信累计</span><strong>{{ formInfo.month.wechat_count }}</strong></div>
            <div><span>月累计回收量</span><strong>{{ formInfo.month.recycle_count }}</strong></div>
            <div><span>月累计销售量</span><strong>{{ formInfo.month.sales_count }}</strong></div>
            <div><span>本月引流利润</span><strong>{{ formInfo.month.profit_yuan || '0.00' }}</strong></div>
          </div>
        </div>
      </template>
    </section>
  </main>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { jlyqApi } from '../api'

const route = useRoute()
const loading = ref(true)
const submitting = ref(false)
const error = ref('')
const successMessage = ref('')
const formInfo = ref(null)
const values = reactive({
  wechat_count: '',
  recycle_count: '',
  sales_count: '',
  profit_yuan: ''
})

const publicToken = computed(() => String(route.query.token || '').trim())

function questionAt(index, fallback) {
  return formInfo.value?.questions?.[index] || fallback
}

function applyFormData(data) {
  formInfo.value = data
  const current = data?.values || {}
  values.wechat_count = current.wechat_count ?? ''
  values.recycle_count = current.recycle_count ?? ''
  values.sales_count = current.sales_count ?? ''
  values.profit_yuan = current.profit_yuan ?? ''
}

async function loadForm() {
  loading.value = true
  error.value = ''
  successMessage.value = ''
  if (!publicToken.value) {
    error.value = '填写链接缺少安全令牌'
    loading.value = false
    return
  }
  try {
    const response = await jlyqApi.getPublicCollectionForm(publicToken.value)
    applyFormData(response?.data || {})
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || '填写链接无效或已失效'
  } finally {
    loading.value = false
  }
}

async function submitForm() {
  submitting.value = true
  error.value = ''
  successMessage.value = ''
  try {
    const response = await jlyqApi.submitPublicCollectionForm(publicToken.value, {
      wechat_count: values.wechat_count,
      recycle_count: values.recycle_count,
      sales_count: values.sales_count,
      profit_yuan: values.profit_yuan
    })
    applyFormData(response?.data || {})
    successMessage.value = '保存成功，月累计已经自动更新'
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message || '保存失败，请检查填写内容'
  } finally {
    submitting.value = false
  }
}

onMounted(loadForm)
</script>

<style scoped>
.form-page {
  min-height: 100vh;
  padding: 18px 12px 36px;
  background: linear-gradient(180deg, #eaf2ff 0%, #f5f7fb 240px, #f5f7fb 100%);
  color: #172033;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.form-card {
  width: min(100%, 620px);
  margin: 0 auto;
  padding: 18px;
  border: 1px solid #dbe6f5;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 14px 36px rgba(31, 75, 140, 0.1);
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 16px;
  border-bottom: 1px solid #edf2f7;
}

.brand-mark {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  border-radius: 12px;
  background: linear-gradient(135deg, #2f8cff, #0f62fe 60%, #0647c9);
  color: #fff;
  font-size: 22px;
  font-weight: 800;
}

.brand-title {
  font-size: 17px;
  font-weight: 800;
}

.brand-subtitle,
.context-line {
  color: #667085;
  font-size: 12px;
  line-height: 1.6;
}

.form-heading {
  padding: 18px 0 12px;
}

.form-heading h1 {
  margin: 0 0 8px;
  font-size: 20px;
  line-height: 1.45;
}

.description-box {
  margin-bottom: 16px;
  padding: 11px 12px;
  border: 1px solid #b9d4ff;
  border-radius: 10px;
  background: #f1f6ff;
  color: #285a9f;
  font-size: 13px;
  line-height: 1.7;
}

.field-item {
  display: block;
  margin-bottom: 16px;
}

.field-item span {
  display: block;
  margin-bottom: 7px;
  color: #344054;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.5;
}

.field-item input {
  box-sizing: border-box;
  width: 100%;
  height: 46px;
  padding: 0 12px;
  border: 1px solid #cdd9e8;
  border-radius: 9px;
  background: #fff;
  color: #111827;
  font-size: 17px;
  outline: none;
}

.field-item input:focus {
  border-color: #267ef0;
  box-shadow: 0 0 0 3px rgba(38, 126, 240, 0.12);
}

.submitted-tip {
  margin: 2px 0 14px;
  padding: 9px 10px;
  border-radius: 8px;
  background: #fff8e6;
  color: #8a5a00;
  font-size: 12px;
  line-height: 1.6;
}

.submit-btn,
.secondary-btn {
  border: none;
  border-radius: 9px;
  font-weight: 700;
  cursor: pointer;
}

.submit-btn {
  width: 100%;
  min-height: 46px;
  background: linear-gradient(135deg, #2f8cff, #0f62fe 60%, #0647c9);
  color: #fff;
  font-size: 15px;
  box-shadow: 0 8px 18px rgba(15, 98, 254, 0.22);
}

.submit-btn:disabled {
  cursor: wait;
  opacity: 0.65;
}

.secondary-btn {
  min-height: 36px;
  margin-top: 14px;
  padding: 0 16px;
  background: #e8f1ff;
  color: #0f62fe;
}

.state-box {
  padding: 46px 10px;
  color: #667085;
  text-align: center;
}

.error-text {
  color: #d92d20;
}

.success-box {
  margin-top: 18px;
  padding: 14px;
  border: 1px solid #a6e3c0;
  border-radius: 10px;
  background: #effaf3;
}

.success-title {
  margin-bottom: 10px;
  color: #18794e;
  font-size: 14px;
  font-weight: 800;
}

.summary-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.summary-grid div {
  padding: 9px;
  border-radius: 8px;
  background: #fff;
}

.summary-grid span,
.summary-grid strong {
  display: block;
}

.summary-grid span {
  color: #667085;
  font-size: 11px;
}

.summary-grid strong {
  margin-top: 3px;
  color: #111827;
  font-size: 17px;
}
</style>
