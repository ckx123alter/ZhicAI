<script setup lang="ts">
import { computed,onMounted,reactive,ref,watch } from 'vue'
import { ReceiptText,ContactRound,ChevronRight,ChevronLeft,Plus,Pencil,Search,Landmark } from 'lucide-vue-next'
import { useBank,type Payee,type PayeeForm,type RecentPayee } from './store'
const props=defineProps<{view:string;initialName?:string;requestKey?:number;selectAfterSave?:boolean}>()
const emit=defineEmits<{select:[payee:Payee];navigate:[path:string];notify:[text:string];cancel:[];add:[name:string];bankTransfer:[];bankPrepared:[recipient:PayeeForm,amount:number];bankSelectSaved:[amount:number|null]}>()
const bank=useBank(),loading=ref(true),error=ref(''),search=ref(''),formOpen=ref(false),editingId=ref(''),saving=ref(false),confirmRemove=ref(false)
const form=reactive<PayeeForm>({nickname:'',holder_name:'',bank_name:'',account_number:''})
const bankForm=reactive<PayeeForm>({nickname:'',holder_name:'',bank_name:'',account_number:''}),bankAmount=ref('')
const isHome=computed(()=>props.view==='/transfer'),isRecords=computed(()=>props.view==='/transfer/records'),isBank=computed(()=>props.view==='/transfer/bank')
const visiblePayees=computed(()=>{const query=search.value.trim().toLowerCase();return bank.payees.filter(p=>!query||[p.nickname,p.holder_name,p.bank_name,p.account_last4].some(value=>value.toLowerCase().includes(query)))})
const money=(value:number)=>new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}).format(value)
const date=(value:string)=>{const parsed=new Date(value.endsWith('Z')||/[+-]\d\d:\d\d$/.test(value)?value:value.replace(' ','T')+'Z');return Number.isNaN(parsed.getTime())?value:new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(parsed)}
async function load(){loading.value=true;error.value='';try{await bank.refreshTransferData()}catch(e){error.value=e instanceof Error?e.message:'加载失败，请重试'}finally{loading.value=false}}
function add(name=''){editingId.value='';Object.assign(form,{nickname:name,holder_name:'',bank_name:'',account_number:'',version:undefined});formOpen.value=true;confirmRemove.value=false;error.value=''}
function cancel(){formOpen.value=false;confirmRemove.value=false;error.value='';emit('cancel')}
async function edit(payee:Payee){try{const saved=await bank.payeeDetail(payee.id);Object.assign(form,{nickname:saved.nickname,holder_name:saved.holder_name,bank_name:saved.bank_name,account_number:'',version:saved.version});editingId.value=payee.id;formOpen.value=true;confirmRemove.value=false;error.value=''}catch(e){error.value=e instanceof Error?e.message:'无法读取收款人'}}
async function save(){if(saving.value)return;saving.value=true;error.value='';try{const payee=await bank.savePayee({...form},editingId.value||undefined);formOpen.value=false;emit('notify',editingId.value?'收款人已更新':'收款人已保存');if(props.selectAfterSave)emit('select',payee)}catch(e){error.value=e instanceof Error?e.message:'保存失败，请重试'}finally{saving.value=false}}
async function prepareBankTransfer(){
 const value=Number(bankAmount.value)
 if(!/^\d+(?:\.\d{1,2})?$/.test(bankAmount.value)||!Number.isFinite(value)||value<=0){error.value='请输入大于 0 且最多两位小数的金额';return}
 error.value=''
 emit('bankPrepared',{...bankForm},value)
}
async function remove(){if(saving.value)return;saving.value=true;try{await bank.removePayee(editingId.value);formOpen.value=false;confirmRemove.value=false;emit('notify','收款人已移除')}catch(e){error.value=e instanceof Error?e.message:'移除失败，请重试'}finally{saving.value=false}}
function recent(item:RecentPayee){if(item.payee){emit('select',item.payee)}else{emit('add',item.display_name)}}
watch(()=>props.view,()=>{formOpen.value=false;error.value='';search.value=props.requestKey?'':props.initialName||'';if(props.view==='/transfer/bank'){Object.assign(bankForm,{nickname:'',holder_name:'',bank_name:'',account_number:''});bankAmount.value=''}if(props.requestKey)add(props.initialName||'')})
watch(()=>props.initialName,name=>{if(!props.requestKey)search.value=name||''})
watch(()=>props.requestKey,key=>{if(key)add(props.initialName||'')},{immediate:true})
onMounted(load)
</script>

<template>
 <section class="transfer-page">
  <div class="page-title transfer-title"><div class="detail-title"><button v-if="!isHome||formOpen" class="detail-back" :aria-label="formOpen?'返回常用收款人':'返回转账'" @click="formOpen?cancel():emit('navigate','/transfer')"><ChevronLeft :size="20"/></button><h2>{{formOpen?(editingId?'编辑收款人':'新增常用收款人'):isHome?'转账':isBank?'银行账号转账':isRecords?'转账记录':'常用收款人'}}</h2></div></div>
  <p v-if="error" class="transfer-error" role="alert">{{error}} <button v-if="!formOpen" @click="load">重试</button></p>
  <form v-if="formOpen" class="payee-form" @submit.prevent="save">
   <div v-if="editingId" class="payee-form-caption payee-form-caption--warning"><Landmark :size="21"/><span>修改开户信息后，相关定时转账会暂停，需要重新授权。</span></div>
   <label>收款人姓名<input v-model="form.holder_name" name="holder_name" placeholder="请输入收款人姓名" maxlength="40" autocomplete="off" required/></label>
   <label>收款银行<input v-model="form.bank_name" name="bank_name" placeholder="请输入收款银行" minlength="2" maxlength="60" autocomplete="off" required/></label>
   <label>收款账号<input v-model="form.account_number" name="account_number" :placeholder="editingId?'留空则保留原账号（尾号 '+bank.payees.find(p=>p.id===editingId)?.account_last4+'）':'请输入收款账号'" inputmode="numeric" maxlength="40" autocomplete="off" :required="!editingId"/></label>
   <label>备注名<span class="field-optional">选填</span><input v-model="form.nickname" name="nickname" placeholder="请输入备注名" maxlength="30" autocomplete="off"/></label>
   <button class="transfer-primary" type="submit" :disabled="saving">{{saving?'正在保存…':'保存'}}</button>
   <template v-if="editingId"><p v-if="confirmRemove" class="payee-remove-hint">移除后，向这个账户转账需要重新添加。</p><button class="payee-remove" type="button" :disabled="saving" @click="confirmRemove?remove():confirmRemove=true">{{confirmRemove?'确认移除':'移除收款人'}}</button><button v-if="confirmRemove" class="payee-remove-cancel" type="button" @click="confirmRemove=false">取消</button></template>
  </form>
  <template v-else-if="isHome">
   <div class="transfer-shortcuts"><button @click="emit('bankTransfer')"><span class="shortcut-icon"><Landmark :size="25" :stroke-width="1.6"/></span><span>银行账号转账</span></button><button @click="emit('navigate','/transfer/records')"><span class="shortcut-icon"><ReceiptText :size="25" :stroke-width="1.6"/></span><span>转账记录</span></button><button @click="emit('navigate','/transfer/payees')"><span class="shortcut-icon"><ContactRound :size="25" :stroke-width="1.6"/></span><span>常用收款人</span></button></div>
   <div class="section-heading recent-heading"><h3>最近转账对象</h3></div>
   <p v-if="loading" class="transfer-loading">正在读取最近转账…</p>
   <div v-else-if="!bank.recentPayees.length" class="transfer-empty"><p>还没有最近转账对象</p></div>
   <div v-else class="recent-payee-list"><button v-for="item in bank.recentPayees" :key="item.payee_id||item.display_name" class="recent-payee-row" @click="recent(item)"><span class="payee-avatar">{{item.display_name.slice(0,1)}}</span><span class="payee-row-copy"><strong>{{item.display_name}}</strong><small>{{item.payee?'常用联系人':'非常用联系人'}}</small><small v-if="item.account_changed" class="account-changed">收款资料已更新</small></span><span class="recent-amount"><strong>¥{{money(item.amount)}}</strong><small>{{date(item.occurred_at)}}</small></span><ChevronRight :size="15"/></button></div>
  </template>
  <template v-else-if="isBank">
   <form class="payee-form bank-transfer-form" @submit.prevent="prepareBankTransfer">
    <label>收款人姓名<input v-model="bankForm.holder_name" name="bank_holder_name" placeholder="请输入收款人姓名" maxlength="40" autocomplete="off" required/></label>
    <label>收款银行<input v-model="bankForm.bank_name" name="bank_bank_name" placeholder="请输入收款银行" minlength="2" maxlength="60" autocomplete="off" required/></label>
    <label>收款账号<input v-model="bankForm.account_number" name="bank_account_number" placeholder="请输入收款账号" inputmode="numeric" maxlength="40" autocomplete="off" required/></label>
    <label>金额（元）<input v-model="bankAmount" name="bank_amount" type="number" min="0.01" step="0.01" inputmode="decimal" placeholder="请输入金额（元）" required/></label>
    <button class="transfer-primary" type="submit">确认</button>
    <button class="transfer-text-link" type="button" @click="emit('bankSelectSaved',Number(bankAmount)>0?Number(bankAmount):null)">选择常用收款人</button>
   </form>
  </template>
  <template v-else-if="isRecords">
   <p v-if="loading" class="transfer-loading">正在读取转账记录…</p>
   <div v-else-if="!bank.transferRecords.length" class="transfer-empty"><span class="transfer-empty-icon"><ReceiptText :size="30" :stroke-width="1.3"/></span><p>还没有转账记录</p></div>
   <article v-for="record in bank.transferRecords" :key="record.id" class="transfer-record"><div class="record-main"><strong>{{record.display_name}}</strong><b>−¥{{money(record.amount)}}</b></div><p>{{record.bank_name?record.bank_name+' · '+record.account_masked:'历史记录 · 未保存账户信息'}}</p><div class="record-foot"><time>{{date(record.occurred_at)}}</time><span>{{record.source==='task'?'定时转账':'转账成功'}}</span></div></article>
  </template>
  <template v-else>
   <div class="payee-add-prompt"><button class="payee-add-action" type="button" @click="add()"><span class="payee-add-icon"><Plus :size="19"/></span><span>新增常用收款人</span></button></div>
   <label class="payee-search"><Search :size="16"/><input v-model="search" placeholder="搜索姓名、备注名或银行账号" aria-label="搜索收款人"/></label>
   <p v-if="loading" class="transfer-loading">正在读取收款人…</p>
   <p v-else-if="bank.payees.length&&!visiblePayees.length" class="transfer-no-match">没有找到收款人，换个姓名或尾号试试。</p>
   <div v-for="payee in visiblePayees" :key="payee.id" class="saved-payee-row"><button class="payee-select" @click="emit('select',payee)"><span class="payee-avatar">{{payee.display_name.slice(0,1)}}</span><span class="payee-row-copy"><strong>{{payee.display_name}}<span v-if="payee.nickname" class="payee-holder">{{payee.holder_name}}</span></strong><small>{{payee.bank_name}} · {{payee.account_masked}}</small></span></button><button class="payee-edit" :aria-label="'编辑'+payee.display_name" @click="edit(payee)"><Pencil :size="16"/></button></div>
  </template>
 </section>
</template>

<style scoped>
.payee-add-prompt{padding:4px 0 15px;margin-bottom:12px;border-bottom:1px solid var(--line)}
.payee-add-action{display:flex;align-items:center;justify-content:flex-start;gap:12px;width:100%;text-align:left;color:var(--ink);font-size:14px;font-weight:500}
.payee-add-icon{display:flex;align-items:center;justify-content:center;flex:0 0 34px;width:34px;height:34px;border:1px solid var(--line);border-radius:50%;color:var(--blue)}
.transfer-page input:focus{outline:none;outline-offset:0;box-shadow:none;border-radius:0}
.transfer-title{margin-bottom:18px}.transfer-title .detail-title{display:flex;align-items:center;gap:9px}.transfer-add{width:34px;height:34px;color:var(--blue);border:1px solid var(--line);border-radius:50%}.transfer-shortcuts{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;border-bottom:1px solid var(--line);padding:4px 0 18px}.transfer-shortcuts>button{flex-direction:column;gap:11px;padding:13px 3px;font-size:12px;color:var(--ink)}.shortcut-icon{display:flex;width:58px;height:58px;align-items:center;justify-content:center;border-radius:19px;background:#edf3fa;color:var(--blue)}.transfer-shortcuts>button:first-child .shortcut-icon{background:#fff1e6;color:#d98236}.transfer-shortcuts>button:last-child .shortcut-icon{background:#eef5f1;color:#59816e}.recent-heading{margin:19px 0 5px}.recent-heading h3{font-size:14px;font-weight:600}.recent-payee-row{width:100%;padding:10px 0;border-bottom:1px solid var(--line);justify-content:flex-start;gap:11px;text-align:left}.payee-avatar{width:41px;height:41px;flex:0 0 41px;display:flex;align-items:center;justify-content:center;border-radius:50%;background:#edf2f7;color:#557294;font-size:15px}.payee-row-copy{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px}.payee-row-copy strong{font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.payee-row-copy small{font-size:10px;color:var(--muted);line-height:1.5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.payee-holder{font-size:10px;color:var(--muted);margin-left:8px;font-weight:400}.recent-amount{display:flex;flex-direction:column;align-items:flex-end;gap:7px;flex-shrink:0}.recent-amount strong{font-size:12px;font-weight:500;font-variant-numeric:tabular-nums}.recent-amount small{font-size:9px;color:var(--muted)}.recent-payee-row>svg{color:#a7afb6;flex-shrink:0}.transfer-empty{text-align:center;padding:42px 10px 30px;color:var(--ink)}.transfer-empty-icon{display:flex;width:64px;height:64px;align-items:center;justify-content:center;border-radius:50%;background:#f6f8fa;color:#8d9fb3;margin:0 auto 19px}.transfer-empty p{font-size:13px;line-height:1.8}.transfer-empty small{display:block;font-size:11px;color:var(--muted);line-height:1.9;margin-top:7px}.transfer-text-link{font-size:12px;color:var(--blue);margin-top:17px;padding:7px 9px}.transfer-loading,.transfer-no-match{padding:28px 0;font-size:12px;color:var(--muted)}.transfer-error{padding:11px 13px;margin-bottom:14px;border-radius:9px;background:#fcf2e8;color:#9c6030;font-size:12px}.transfer-error button{color:inherit;text-decoration:underline}.saved-payee-row{display:flex;align-items:center;border-bottom:1px solid var(--line);gap:8px}.payee-select{flex:1;min-width:0;justify-content:flex-start;text-align:left;padding:10px 0;gap:12px}.payee-edit{width:34px;height:38px;color:#8b98a8;flex-shrink:0}.payee-search{display:flex;align-items:center;gap:9px;padding:11px 12px;border-radius:10px;background:#f4f6f8;margin:3px 0 8px;color:#8b96a3}.payee-search input{width:100%;min-width:0;border:0;background:transparent;font-size:11px;outline:none}.transfer-record{padding:10px 0;border-bottom:1px solid var(--line)}.record-main{display:flex;justify-content:space-between;gap:14px;font-size:13px}.record-main strong,.record-main b{font-weight:500}.transfer-record>p{font-size:10px;color:var(--muted);margin:8px 0 10px}.record-foot{display:flex;justify-content:space-between;gap:10px;font-size:10px;color:var(--muted)}.record-foot>span{color:#6e8a78}.payee-form-caption{display:flex;align-items:center;gap:10px;color:#7c8998;background:#f5f8fb;border-radius:10px;padding:14px 12px;font-size:11px;margin-bottom:21px}.payee-form>label{display:block;margin:0 0 19px;font-size:12px;color:#586a7e}.payee-form input{display:block;width:100%;margin-top:10px;border:0;border-bottom:1px solid var(--line);border-radius:0;padding:4px 0 13px;font-size:13px;line-height:1.6;background:transparent}.payee-form input::placeholder{color:#abb3bc}.field-optional{margin-left:7px;color:#a2abb5;font-size:10px}.transfer-primary{display:flex;width:100%;background:var(--blue);color:#fff;min-height:43px;border-radius:9px;margin-top:29px;font-size:13px}.payee-remove{display:flex;margin:19px auto 0;font-size:12px;color:#a46d62;padding:8px 16px}.payee-remove-hint{font-size:11px;margin-top:19px;color:#8c736e;text-align:center}.payee-remove-cancel{display:flex;margin:7px auto;font-size:12px;color:var(--muted)}.payee-row-copy .account-changed{color:#a77547}
@media(max-width:380px){.recent-payee-row{gap:8px}.payee-avatar{width:36px;height:36px;flex-basis:36px}.recent-amount strong{font-size:11px}.payee-row-copy strong{font-size:12px}}
.bank-transfer-form>.transfer-text-link{display:flex;margin:14px auto 0}.bank-transfer-form>.transfer-primary{margin-top:16px}
.payee-form-caption--warning{background:#fff3e2;color:#94632f}
</style>
