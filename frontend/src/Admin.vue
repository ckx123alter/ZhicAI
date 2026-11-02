<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {csrfHeaders,signOut,handleAuthFailure} from './auth'
type Product={id:string;name:string;risk:number;open:boolean}
type Action={action:string;target:string;detail:string;created_at:string}
const products=ref<Product[]>([]),actions=ref<Action[]>([]),counts=ref({products:0,open_products:0,tasks:0})
const error=ref(''),busy=ref(''),resetting=ref(false),notice=ref('')
const system=ref({auth_sessions:0,pending_previews:0,recent_task_failures:0})
const securityAudit=ref<{kind:string;at:string}[]>([])
async function load(){
 try{const response=await fetch('/api/admin/overview');handleAuthFailure(response);if(!response.ok)throw new Error('无法读取管理员数据')
  const data=await response.json();products.value=data.products;actions.value=data.actions;counts.value=data.counts;system.value=data.system;securityAudit.value=data.security_audit}
 catch(e){error.value=e instanceof Error?e.message:'读取失败'}
}
async function toggle(product:Product){
 busy.value=product.id;error.value=''
 try{const response=await fetch('/api/admin/products/'+encodeURIComponent(product.id)+'/availability',
  {method:'PUT',headers:{'Content-Type':'application/json',...csrfHeaders()},body:JSON.stringify({open:!product.open})})
  handleAuthFailure(response);if(!response.ok)throw new Error('更新产品状态失败')
  await load()
 }catch(e){error.value=e instanceof Error?e.message:'更新失败'}finally{busy.value=''}
}
async function resetDemo(){
 if(resetting.value)return
 resetting.value=true;error.value='';notice.value=''
 try{const response=await fetch('/api/admin/reset',{method:'POST',headers:csrfHeaders()})
  handleAuthFailure(response);if(!response.ok)throw new Error('重置演示数据失败')
  notice.value='演示数据已重置'
  await load()
 }catch(e){error.value=e instanceof Error?e.message:'重置失败'}finally{resetting.value=false}
}
onMounted(load)
</script>
<template>
 <main class="admin-page"><header><div><small>知财 · 本地演示</small><h1>管理员</h1></div><button @click="signOut">退出登录</button></header>
  <p v-if="error" class="admin-error" role="alert">{{error}}</p>
  <p v-if="notice" class="admin-notice" role="status">{{notice}}</p>
  <section class="stats"><article><b>{{counts.products}}</b><span>理财产品</span></article><article><b>{{counts.open_products}}</b><span>开放申购</span></article><article><b>{{counts.tasks}}</b><span>定时任务</span></article></section>
  <section><h2>系统运行情况</h2><div class="stats"><article><b>{{system.auth_sessions}}</b><span>有效登录会话</span></article><article><b>{{system.pending_previews}}</b><span>待确认预览</span></article><article><b>{{system.recent_task_failures}}</b><span>任务失败记录</span></article></div></section>
  <section><h2>演示数据</h2><p>重置会清空账户快照、预览、任务、审计与对话，仅限独立演示环境。</p><button :disabled="resetting" @click="resetDemo">{{resetting?'正在重置…':'重置演示数据'}}</button></section>
  <section><h2>产品状态</h2><p>管理员可调整演示产品的申购状态。账户转账和申赎由账户用户本人确认。</p>
   <div v-for="product in products" :key="product.id" class="row"><div><strong>{{product.name}}</strong><small>R{{product.risk}} · {{product.id}}</small></div><button :disabled="busy===product.id" @click="toggle(product)">{{product.open?'暂停申购':'开放申购'}}</button></div>
  </section><section><h2>管理员操作记录</h2><p v-if="!actions.length">暂无记录</p>
   <div v-for="action in actions" :key="action.created_at+action.target" class="row"><div><strong>{{action.target}}</strong><small>{{action.created_at}} · {{action.detail}}</small></div></div>
  </section>
  <section><h2>脱敏安全审计</h2><p>仅显示工具类别和时间，不展示账户流水或聊天内容。</p><p v-if="!securityAudit.length">暂无记录</p><div v-for="(item,index) in securityAudit" :key="item.at+index" class="row"><div><strong>{{item.kind}}</strong><small>{{item.at}}</small></div></div></section>
 </main>
</template>
<style scoped>
.admin-page{max-width:850px;margin:auto;padding:30px 20px 70px;color:#183850;font-family:Arial,"Microsoft YaHei",sans-serif}
header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #dde4ea;padding-bottom:20px}
small{color:#768899;font-size:12px}h1{margin:6px 0;font-size:26px}h2{font-size:18px;margin:35px 0 12px}
button{background:#e9f0f6;border:0;border-radius:7px;color:#173953;padding:9px 14px;cursor:pointer}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:26px}.stats article{background:#f3f7fa;padding:20px;border-radius:10px;display:flex;flex-direction:column;gap:7px}.stats b{font-size:24px}
p{color:#66788c;font-size:13px}.row{display:flex;justify-content:space-between;align-items:center;padding:13px 0;border-bottom:1px solid #e4e9ed;gap:16px}.row div{display:flex;flex-direction:column;gap:5px}.admin-error{color:#b34b36}
@media(max-width:500px){.stats{grid-template-columns:repeat(3,1fr)}.stats article{padding:12px}.row{align-items:flex-start}.row button{white-space:nowrap}}
</style>
