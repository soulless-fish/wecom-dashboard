/**
 * 企业微信JS-SDK工具函数
 */

import { jssdkApi } from '../api'

// 检查是否在企业微信环境
export function isWeComEnvironment() {
  const ua = navigator.userAgent.toLowerCase()
  return ua.includes('wxwork') || ua.includes('micromessenger')
}

// 初始化企业微信JS-SDK
export async function initWeComSDK() {
  if (!isWeComEnvironment()) {
    console.log('非企业微信环境，跳过SDK初始化')
    return false
  }

  try {
    const currentUrl = window.location.href.split('#')[0]

    // 获取配置
    const config = await jssdkApi.getConfig(currentUrl)

    // 配置wx
    return new Promise((resolve, reject) => {
      wx.config({
        beta: true,
        debug: false,
        appId: config.corp_id,
        timestamp: config.timestamp,
        nonceStr: config.nonce_str,
        signature: config.signature,
        jsApiList: [
          'getCurExternalChat',
          'getContext',
          'agentConfig'
        ]
      })

      wx.ready(() => {
        console.log('wx.config ready')
        resolve(true)
      })

      wx.error((err) => {
        console.error('wx.config error:', err)
        reject(err)
      })
    })
  } catch (error) {
    console.error('初始化SDK失败:', error)
    throw error
  }
}

// 初始化应用级别配置
export async function initAgentConfig() {
  if (!isWeComEnvironment()) {
    return false
  }

  try {
    const currentUrl = window.location.href.split('#')[0]
    const config = await jssdkApi.getAgentConfig(currentUrl)

    return new Promise((resolve, reject) => {
      wx.agentConfig({
        corpid: config.corp_id,
        agentid: config.agent_id,
        timestamp: config.timestamp,
        nonceStr: config.nonce_str,
        signature: config.signature,
        jsApiList: [
          'getCurExternalChat',
          'sendChatMessage'
        ],
        success: () => {
          console.log('agentConfig success')
          resolve(true)
        },
        fail: (err) => {
          console.error('agentConfig fail:', err)
          reject(err)
        }
      })
    })
  } catch (error) {
    console.error('agentConfig失败:', error)
    throw error
  }
}

// 获取当前外部群聊ID
export function getCurExternalChat() {
  return new Promise((resolve, reject) => {
    if (!isWeComEnvironment()) {
      reject(new Error('非企业微信环境'))
      return
    }

    wx.invoke('getCurExternalChat', {}, (res) => {
      if (res.err_msg === 'getCurExternalChat:ok') {
        resolve(res.chatId)
      } else {
        reject(new Error(res.err_msg || '获取群聊ID失败'))
      }
    })
  })
}

// 获取应用上下文（使用ww对象）
export function getWeComContext() {
  return new Promise((resolve, reject) => {
    if (typeof ww === 'undefined') {
      reject(new Error('ww对象未定义'))
      return
    }

    ww.getContext({
      success: (res) => {
        console.log('getContext success:', res)
        resolve(res)
      },
      fail: (err) => {
        console.error('getContext fail:', err)
        reject(err)
      }
    })
  })
}

// 发送消息到群聊
export function sendChatMessage(content) {
  return new Promise((resolve, reject) => {
    if (!isWeComEnvironment()) {
      reject(new Error('非企业微信环境'))
      return
    }

    wx.invoke('sendChatMessage', {
      msgtype: 'text',
      text: {
        content: content
      }
    }, (res) => {
      if (res.err_msg === 'sendChatMessage:ok') {
        resolve(true)
      } else {
        reject(new Error(res.err_msg || '发送消息失败'))
      }
    })
  })
}
