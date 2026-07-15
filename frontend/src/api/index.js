import axios from 'axios'

// 创建axios实例
const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 响应拦截器
api.interceptors.response.use(
  response => response.data,
  error => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

// 门店业绩相关API
export const storePerformanceApi = {
  // 获取门店列表
  getStores(params = {}) {
    return api.get('/store-performance/stores', { params })
  },

  // 根据门店名获取业绩
  getStoreByName(poiName, dataMonth) {
    const params = dataMonth ? { data_month: dataMonth } : {}
    return api.get(`/store-performance/store/${encodeURIComponent(poiName)}`, { params })
  },

  // 获取月度汇总
  getSummary(dataMonth) {
    const params = dataMonth ? { data_month: dataMonth } : {}
    return api.get('/store-performance/summary', { params })
  },

  // 获取定时任务状态
  getSchedulerStatus() {
    return api.get('/store-performance/scheduler/status')
  },

  // 获取生意经当月和近30天平均核销金额
  getVerifyAverage() {
    return api.get('/store-performance/verify-average')
  }
}

// 企业微信相关API
export const wecomApi = {
  // 获取用户信息
  getUserInfo(code) {
    return api.get('/wecom/user/info', { params: { code } })
  },

  // 根据群名匹配门店
  matchStore(groupName) {
    return api.post('/wecom/match-store', { group_name: groupName })
  },

  // 获取群聊信息
  getGroupInfo(chatId) {
    return api.get('/wecom/group/info', { params: { chat_id: chatId } })
  }
}

// JS-SDK配置API
export const jssdkApi = {
  // 获取企业配置
  getConfig(url) {
    return api.post('/jssdk/config', { url })
  },

  // 获取应用配置
  getAgentConfig(url) {
    return api.get('/jssdk/agent-config', { params: { url } })
  }
}

// 来客数据API
export const lifeDataApi = {
  // 获取直播数据
  getLiveData(roomType = 'ALL') {
    return api.get('/life-data/live', { params: { room_type: roomType } })
  },

  // 获取门店数据
  getPoiData(startDate, endDate) {
    const params = {}
    if (startDate) params.start_date = startDate
    if (endDate) params.end_date = endDate
    return api.get('/life-data/poi', { params })
  },

  // 获取汇总数据
  getSummary(startDate, endDate) {
    const params = {}
    if (startDate) params.start_date = startDate
    if (endDate) params.end_date = endDate
    return api.get('/life-data/summary', { params })
  }
}

// 抖音官方订单同步状态API
export const douyinOfficialOrderApi = {
  // 查询订单明细本地同步状态
  getStatus() {
    return api.get('/douyin/official-orders/status')
  },

  // 手动触发订单同步，主要用于部署后验证和后台维护
  syncOrders(params = {}) {
    return api.post('/douyin/official-orders/sync', null, { params })
  }
}

// 抖音团购链接API
export const douyinGroupPurchaseApi = {
  // 按门店ID获取命中的团购链接商品
  getStoreLinks(params = {}) {
    return api.get('/douyin/group-purchase/store-links', { params })
  }
}

// 抖音号详情API
export const douyinAccountApi = {
  // 按门店ID和门店名称获取抖音号详情
  getStoreAccounts(params = {}) {
    return api.get('/douyin/poi-account-bindings/store-accounts', { params })
  }
}

// 巨量引擎本地推API
export const jlyqApi = {
  // 获取侧边栏巨量引擎展示数据
  getSidebarData(params = {}) {
    return api.get('/jlyq/local-promotion/sidebar-data', { params })
  },

  // 手动触发巨量本地推同步
  syncLocalPromotion() {
    return api.post('/jlyq/local-promotion/sync')
  },

  // 查询巨量本地推同步状态
  getLocalPromotionStatus() {
    return api.get('/jlyq/local-promotion/status')
  },

  // 创建当前巨量账号的商家每日填写表
  createCollectionForm(data = {}) {
    return api.post('/jlyq/local-promotion/collection-form', data)
  },

  // 企业微信卡片发送成功后记录发送时间
  markCollectionFormSent(recordId) {
    return api.post(`/jlyq/local-promotion/collection-form/${recordId}/sent`, { sent: true })
  },

  // 立即同步企业微信官方收集表答案
  syncCollectionForms(bindingKeys = []) {
    return api.post('/jlyq/local-promotion/collection-form/sync', { binding_keys: bindingKeys })
  },

  // 读取权限不足时自动生成的安全网页填写表
  getPublicCollectionForm(publicToken) {
    return api.get(`/jlyq/local-promotion/collection-form/public/${encodeURIComponent(publicToken)}`)
  },

  // 保存商家填写值，重复提交会覆盖当天记录
  submitPublicCollectionForm(publicToken, data = {}) {
    return api.post(
      `/jlyq/local-promotion/collection-form/public/${encodeURIComponent(publicToken)}`,
      data
    )
  }
}

export default api
