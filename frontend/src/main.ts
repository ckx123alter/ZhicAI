import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import Login from './Login.vue'
import Admin from './Admin.vue'
import {currentIdentity,csrfHeaders,handleAuthFailure} from './auth'
import { Button, TabBar, TabBarItem, Popup, Input, Checkbox } from 'tdesign-mobile-vue'
import 'tdesign-mobile-vue/es/style/index.css'
import 'tdesign-mobile-vue/es/button/style/index.css'
import 'tdesign-mobile-vue/es/tab-bar/style/index.css'
import 'tdesign-mobile-vue/es/popup/style/index.css'
import 'tdesign-mobile-vue/es/input/style/index.css'
import 'tdesign-mobile-vue/es/checkbox/style/index.css'
import './style.css'
import { loadDemoData, type DemoData } from './data'
import { initializeConversation, conversationHeaders } from './conversation'
import { useBank, freshAccountState, readLegacyAccountState, setBackendInitialAccountState, type AccountState } from './store'
const router = createRouter({history:createWebHashHistory(),routes:['/','/bills','/transactions','/wealth','/wealth/product/:productId','/risk-assessment','/transfer','/transfer/bank','/transfer/payees','/transfer/records','/me','/holdings'].map(path=>({path,component:{template:'<span />'}}))})
const app=createApp(App).use(createPinia()).use(router)
for(const component of [Button,TabBar,TabBarItem,Popup,Input,Checkbox])app.component(component.name!,component)

async function start(){
  const identity=await currentIdentity()
  if(!identity){createApp(Login).mount('#app');return}
  if(identity.role==='admin'){createApp(Admin).mount('#app');return}
  await initializeConversation()
  const response=await fetch('/api/bootstrap',{headers:conversationHeaders()})
  handleAuthFailure(response)
  if(!response.ok)throw new Error(`后端返回 ${response.status}`)
  loadDemoData(await response.json() as DemoData)
  let accountResponse=await fetch('/api/account-state')
  let accountState:AccountState
  if(accountResponse.status===404){
    const state=readLegacyAccountState()||freshAccountState()
    accountResponse=await fetch('/api/account-state/initialize',{method:'POST',headers:{'Content-Type':'application/json',...csrfHeaders()},body:JSON.stringify({state})})
  }
  handleAuthFailure(accountResponse)
  if(!accountResponse.ok)throw new Error(`账户数据接口返回 ${accountResponse.status}`)
  accountState=await accountResponse.json() as AccountState
  setBackendInitialAccountState(accountState)
  await useBank().reload()
  app.mount('#app')
}

void start().catch(error=>{
  console.error('知财后端连接失败',error)
  const root=document.querySelector<HTMLElement>('#app')
  if(root)root.innerHTML='<main style="max-width:480px;margin:15vh auto;padding:24px;color:#243247;font:16px/1.6 Arial,sans-serif"><h2>暂时无法连接知财服务</h2><p>请先运行项目目录中的“启动知财.cmd”，再刷新页面。</p></main>'
})
