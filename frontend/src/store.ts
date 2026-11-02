import { defineStore } from 'pinia'
import { computed, onScopeDispose, ref, watch } from 'vue'
import { conversationMessages, products } from './data'
import { demoConfig } from './demoConfig'
import { conversationHeaders } from './conversation'
import {csrfHeaders,handleAuthFailure} from './auth'

export const CHAT_FALLBACK_REPLY='抱歉，知财暂不支持这类请求。'
const CHAT_TURN_TIMEOUT_MS=90_000
const CHAT_TIMEOUT_REPLY='这次处理超过 90 秒，请重试。'

export type Payee = {id:string;nickname:string;holder_name:string;bank_name:string;display_name:string;account_last4:string;account_masked:string;version:number;first_transfer:boolean}
export type TransferRecord = {id:string;payee_id:string|null;payee_version:number|null;display_name:string;holder_name:string|null;bank_name:string|null;account_masked:string|null;amount:number;source:'instant'|'task';occurred_at:string}
export type RecentPayee = TransferRecord & {payee:Payee|null;account_changed:boolean}
export type PayeeForm = {nickname:string;holder_name:string;bank_name:string;account_number:string;version?:number}

export type TaskType = 'fixed_expense' | 'investment' | 'redemption'
export type TaskFrequency = 'monthly' | 'quarterly' | 'semiannual'
export type InvestmentLot = { id?:string; principal:number; originalPrincipal?:number; purchaseDate:string; holdingDays?:number }
export type Holding = { productId:string; principal:number; profit:number; lots?:InvestmentLot[] }
export const fixedCategories:string[]=['租金','生活缴费','贷款分期','教育','医疗','其他']
const normalizeFixedName=(name:unknown)=>name==='房租'?'租金':typeof name==='string'&&fixedCategories.includes(name)?name:'其他'
export type Task = {id:string;type:TaskType;name:string;amount:number;frequency:TaskFrequency|'weekly';authorizedAt:string;executionDay?:number;risk:number;status:'运行中'|'已暂停'|'已取消';productId?:string;allProducts?:boolean;payee?:string;authorized?:boolean;lastRun?:string;lastFailure?:string;payeeId?:string;payeeVersion?:number}
export type MessageAction = {label:string;path:'/bills'|'/wealth'|'/me'|'/risk-assessment'|'/transfer'|'/transfer/payees'|'/transfer/records';mode?:'products'|'performance';icon?:'chevron'|'up-right';modal?:'task'|'redeem'|'buy'|'transfer'|'payee';payeeId?:string;payeeVersion?:number;manualRecipient?:{holder_name:string;bank_name?:string|null;account_number:string};taskType?:TaskType;taskName?:string;frequency?:TaskFrequency;amount?:number;day?:number;payee?:string;productId?:string;risk?:number;month?:string;bubbleSegments?:string[];resultIds?:string[]}
type Message = {id:string|number;role:'user'|'agent';text:string;action?:MessageAction;actions?:MessageAction[];parent_message_id?:string;topic_id?:string;message_kind?:string}

const pad=(n:number)=>String(n).padStart(2,'0')
const dateISO=(d:Date)=>`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`
const parseDate=(iso:string)=>{const [year,month,day]=iso.split('-').map(Number);return new Date(year,month-1,day,12)}
export const todayISO=()=>dateISO(new Date())
export function addMonthsISO(iso:string,count:number){const d=parseDate(iso);const last=new Date(d.getFullYear(),d.getMonth()+count+1,0).getDate();return dateISO(new Date(d.getFullYear(),d.getMonth()+count,Math.min(d.getDate(),last),12))}
export function nextMonthlyDate(day:number){const now=todayISO(),date=parseDate(now);const candidate=dateISO(new Date(date.getFullYear(),date.getMonth(),Math.min(day,new Date(date.getFullYear(),date.getMonth()+1,0).getDate()),12));return candidate>=now?candidate:addMonthsISO(candidate,1)}
const utcDay=(iso:string)=>{const [year,month,day]=iso.split('-').map(Number);return Date.UTC(year,month-1,day)/86400000}
export const monthlyEquivalent=(task:Pick<Task,'amount'|'frequency'>)=>task.amount*(task.frequency==='weekly'?52/12:task.frequency==='quarterly'?1/3:task.frequency==='semiannual'?1/6:1)
const capacityName=(level:number)=>level===1?'谨慎型':level===2?'稳健型':level===3?'平衡型':level===4?'进取型':'激进型'
export function taskOccurrences(task:Task,from=todayISO(),days=demoConfig.cashflow.forecastDays){
 if(task.status!=='运行中'||!task.authorized)return [] as {day:number;date:string}[]
 const authorizationDate=task.authorizedAt||from
 const start=parseDate(authorizationDate)
 const requestedDay=task.executionDay
 const firstCandidate=requestedDay&&task.frequency!=='weekly'?dateISO(new Date(start.getFullYear(),start.getMonth(),Math.min(requestedDay,new Date(start.getFullYear(),start.getMonth()+1,0).getDate()),12)):authorizationDate
 const interval=task.frequency==='quarterly'?3:task.frequency==='semiannual'?6:1
 const firstDate=firstCandidate<authorizationDate?addMonthsISO(firstCandidate,interval):firstCandidate
 const result:{day:number;date:string}[]=[],anchor=parseDate(firstDate)
 for(let index=0;index<600;index++){
  let date:string
  if(task.frequency==='weekly'){const d=new Date(anchor);d.setDate(d.getDate()+index*7);date=dateISO(d)}
  else {const target=new Date(anchor.getFullYear(),anchor.getMonth()+index*(task.frequency==='quarterly'?3:task.frequency==='semiannual'?6:1),1,12);date=dateISO(new Date(target.getFullYear(),target.getMonth(),Math.min(requestedDay||anchor.getDate(),new Date(target.getFullYear(),target.getMonth()+1,0).getDate()),12))}
   if(utcDay(date)-utcDay(from)>days)break
  const day=utcDay(date)-utcDay(from)
  if(day>=0)result.push({day,date})
 }
 return result
}
const seedHoldings=()=>demoConfig.holdings.map(({productId,principal,profit,purchaseDate,holdingDays})=>({productId,principal,profit,lots:[{principal,purchaseDate,holdingDays}]} as Holding))
export function addHoldingLot(holding:Holding,amount:number,purchaseDate=todayISO()){
 if(!holding.lots?.length)holding.lots=[{principal:holding.principal,purchaseDate}]
 holding.lots.push({principal:amount,purchaseDate})
 holding.principal+=amount
}
export function reduceHoldingLots(holding:Holding,amount:number){
 if(!holding.lots?.length)holding.lots=[{principal:holding.principal,purchaseDate:todayISO()}]
 let remaining=amount
 for(const lot of holding.lots){const reduction=Math.min(lot.principal,remaining);lot.principal-=reduction;remaining-=reduction;if(remaining<=0)break}
 holding.lots=holding.lots.filter(lot=>lot.principal>0)
 holding.principal=Math.max(0,holding.principal-amount)
}
export type AccountState = {balance:number;riskLevel:number;monthlySalary:number;salaryDay:number;monthlyExpenses:number;reserveAmount:number;holdings:Holding[];tasks:Task[];receipts:string[]}
export const freshAccountState=():AccountState=>({balance:demoConfig.account.openingBalance,riskLevel:demoConfig.account.riskLevel,monthlySalary:demoConfig.account.monthlySalary,salaryDay:demoConfig.account.salaryDay,monthlyExpenses:demoConfig.account.monthlyExpenses,holdings:seedHoldings(),tasks:[],receipts:[],reserveAmount:demoConfig.account.reserveAmount})
const fresh=()=>({...freshAccountState(),messages:conversationMessages.map(message=>({...message})) as Message[]})
export function readLegacyAccountState():AccountState|null{
 let initial:AccountState|null=null
 try{
  const savedBundle=JSON.parse(localStorage.getItem('zhicai-v1')||'null')
  if([1,2,3].includes(savedBundle?.version)&&savedBundle.data&&typeof savedBundle.data==='object'&&Number.isFinite(savedBundle.data.balance)&&Array.isArray(savedBundle.data.holdings)&&Array.isArray(savedBundle.data.tasks)){
    const {reservePercent,reserveAmount,rent,rentDay,declining,saving,...saved}=savedBundle.data
   const receipts=Array.isArray(saved.receipts)?saved.receipts.map((item:unknown)=>typeof item==='string'?item.replaceAll('·','-'):String(item)):freshAccountState().receipts
   const tasks:Task[]=Array.isArray(saved.tasks)?saved.tasks.map((item:any,index:number)=>{
    if(item.type==='fixed_expense'||item.type==='investment'||item.type==='redemption'){const {startDate,endDate,...savedTask}=item;const fallbackProduct=item.type==='investment'&&!item.productId?products.filter(product=>product.risk<=Math.min(Number(item.risk)||1,Number(saved.riskLevel)||2)&&product.open&&product.min<=Number(item.amount)&&product.days===0).sort((a,b)=>b.rate-a.rate)[0]:undefined;const productId=item.type==='investment'?(item.productId||fallbackProduct?.id):item.productId;const hasInvestmentProduct=item.type!=='investment'||products.some(product=>product.id===productId);const task={...savedTask,productId,authorizedAt:item.authorizedAt||startDate||todayISO(),name:item.type==='fixed_expense'?normalizeFixedName(item.name):item.type==='investment'?(item.name&&item.name!=='买入理财'?item.name:products.find(product=>product.id===productId)?.name||'申购理财'):'赎回理财'};const needsPause=item.frequency==='weekly'||!item.authorized||item.type==='fixed_expense'&&!String(item.payee||'').trim()||item.type==='redemption'&&!item.productId;return item.status!=='已取消'&&!hasInvestmentProduct?{...task,status:'已取消'} as Task:needsPause&&item.status!=='已取消'?{...task,status:'已暂停'} as Task:task as Task}
    const startDate=nextMonthlyDate(Number(item.day)||5)
    return {id:`legacy-investment-${index}`,type:'investment',name:'申购理财',amount:Number(item.amount)||0,frequency:'monthly',authorizedAt:startDate,executionDay:Number(item.day)||undefined,risk:Number(item.risk)||1,status:item.status||'运行中'}
   }):[]
   if(Number(rent)>0){const authorizedAt=nextMonthlyDate(Number(rentDay)||1);tasks.unshift({id:'legacy-rent',type:'fixed_expense',name:'租金',amount:Number(rent),frequency:'monthly',authorizedAt,executionDay:Number(rentDay)||1,risk:1,status:'已暂停'})}
   // F→K only corrected the original demo fixture. Do not rewrite newly saved holdings.
   const seeds=new Map(demoConfig.holdings.map(item=>[item.productId,item]))
   const holdings=Array.isArray(saved.holdings)?saved.holdings.map((item:any)=>{const migrated=savedBundle.version<=2&&item?.productId==='F'?{...item,productId:'K'}:item;const seed=seeds.get(migrated?.productId);const unchangedSeed=seed&&Number(migrated.principal)===seed.principal;const lots=Array.isArray(migrated.lots)?migrated.lots:unchangedSeed?[{principal:seed.principal,purchaseDate:seed.purchaseDate,holdingDays:seed.holdingDays}]:[{principal:Number(migrated.principal)||0,purchaseDate:todayISO()}];return {...migrated,profit:migrated?.profit??0,lots}}):freshAccountState().holdings
   initial={...freshAccountState(),...saved,holdings,tasks,receipts,reserveAmount:demoConfig.account.reserveAmount,monthlySalary:demoConfig.account.monthlySalary,salaryDay:demoConfig.account.salaryDay,monthlyExpenses:demoConfig.account.monthlyExpenses}
  }
 }catch{}
 return initial
}
let backendInitialState:AccountState|null=null
export function setBackendInitialAccountState(state:AccountState){backendInitialState=state}
export const useBank=defineStore('bank',()=>{
 const data=ref({...fresh(),...(backendInitialState||freshAccountState())})
 const accountWarning=ref(false),conversationWarning=ref(false)
 const summary=ref<{points:{day:number;date:string;balance:number;income:number;fixedExpensesToday:{name:string;amount:number}[]}[];cap:number;gap:number;fixedMonthly:number;recurring:number}>({points:[],cap:0,gap:0,fixedMonthly:0,recurring:0})
 let operationSource:string|undefined
 function setOperationSource(id?:string){operationSource=id}
 const payees=ref<Payee[]>([]),transferRecords=ref<TransferRecord[]>([]),recentPayees=ref<RecentPayee[]>([])
 async function request(path:string,body?:unknown,method='POST',signal?:AbortSignal){
  const headers={...conversationHeaders(),...(body===undefined?{}:{'Content-Type':'application/json'}),...(body!==undefined||method==='DELETE'?csrfHeaders():{}),...(path.startsWith('/api/operations/')&&operationSource?{'X-Context-Message-Id':operationSource}:{})}
  const response=await fetch(path,{headers,signal,...(body===undefined?(method==='DELETE'?{method}:{}):{method,body:JSON.stringify(body)})})
  handleAuthFailure(response)
  if(!response.ok){const error=await response.json().catch(()=>({detail:'服务暂不可用'}));throw Object.assign(new Error(typeof error.detail==='string'?error.detail:'请检查操作参数'),{status:response.status})}
  return response.json()
 }
 async function refreshTransferData(){
  const [saved,records,recent]=await Promise.all([request('/api/payees'),request('/api/transfers'),request('/api/transfers/recent')])
  payees.value=saved;transferRecords.value=records;recentPayees.value=recent
 }
 async function savePayee(body:PayeeForm,id?:string):Promise<Payee>{
  const saved=await request(id?`/api/payees/${id}`:'/api/payees',body,id?'PUT':'POST')
  if(id)await reload().catch(()=>{})
  await refreshTransferData().catch(()=>{})
  return saved
 }
  async function payeeDetail(id:string):Promise<Payee>{return request(`/api/payees/${id}`)}
 async function removePayee(id:string){await request(`/api/payees/${id}`,undefined,'DELETE');await reload().catch(()=>{});await refreshTransferData()}
 async function reload(){
  try{const result=await request('/api/finance/overview');data.value={...result.state,messages:data.value.messages};summary.value=result.cashflow;accountWarning.value=false}
  catch(error){accountWarning.value=true;throw error}
 }
 async function chat(text:string,onReply?:()=>void){
  // Show the question before waiting for the model. POST /api/chat owns persistence;
  // the authoritative history below replaces this local item without duplicating it.
  data.value.messages.push({id:`pending-${crypto.randomUUID()}`,role:'user',text})
  const controller=new AbortController()
  const timeout=setTimeout(()=>controller.abort(),CHAT_TURN_TIMEOUT_MS)
  let reply
  try{reply=await request('/api/chat',{message:text},'POST',controller.signal)}
  catch(error){
   console.warn('知财对话请求失败',error)
   const message=controller.signal.aborted||(error as {status?:number}).status===504?CHAT_TIMEOUT_REPLY:CHAT_FALLBACK_REPLY
   data.value.messages.push({id:crypto.randomUUID(),role:'agent',text:message})
   onReply?.()
   return {text:message}
  }
  finally{clearTimeout(timeout)}
  // Display the returned answer before the separate history sync finishes.
  data.value.messages.push({id:reply.id||crypto.randomUUID(),role:'agent',text:reply.text,actions:reply.actions})
  onReply?.()
  try{data.value.messages=await request('/api/conversation/messages')}
  catch(error){
   console.warn('知财对话记录同步失败',error)
  }
  return reply
 }
 function message(role:Message['role'],text:string,action?:MessageAction|MessageAction[],parentId?:string|number){
  if(parentId!==undefined){
   // 同一回答下的同一条页面解读只保留一次，反复点击同一动作不再重复堆积。
   const parent=String(parentId)
   const shown=data.value.messages.some(m=>m.parent_message_id===parent&&m.message_kind==='page_explanation'&&m.role===role&&m.text===text)
   if(shown)return
  }
  const item:Message={id:crypto.randomUUID(),role,text,...(Array.isArray(action)?{actions:action}:{action}),...(parentId===undefined?{}:{parent_message_id:String(parentId),message_kind:'page_explanation'})}
  data.value.messages.push(item)
  if(parentId!==undefined)void request('/api/conversation/messages',item).catch(()=>conversationWarning.value=true)
 }
 let retryPreview:{signature:string;id:string;requiresBufferConfirmation:boolean;requiresChallenge:boolean}|null=null
 async function operation(command:unknown,acknowledged=false,previewId?:string,challengeToken?:string){
  const signature=JSON.stringify(command)
  const existing=previewId||(retryPreview?.signature===signature?retryPreview.id:undefined)
  if(existing&&(!retryPreview||retryPreview.id!==existing||retryPreview.signature!==signature)){
   throw new Error('操作预览已失效，请重新核对')
  }
  const preview=existing?{
   preview_id:existing,
   requires_buffer_confirmation:retryPreview!.requiresBufferConfirmation,
   requires_challenge:retryPreview!.requiresChallenge
  }:await request('/api/operations/preview',command)
  retryPreview={signature,id:preview.preview_id,
   requiresBufferConfirmation:!!preview.requires_buffer_confirmation,
   requiresChallenge:!!preview.requires_challenge}
  if(preview.requires_buffer_confirmation&&!acknowledged)return preview
  if(preview.requires_challenge&&!challengeToken)return {...preview,requires_buffer_confirmation:false}
  let result
  try{result=await request('/api/operations/'+preview.preview_id+'/confirm',{confirmed:true,buffer_acknowledged:acknowledged,challenge_token:challengeToken})}
  catch(error){if([404,409,422].includes((error as any).status))retryPreview=null;throw error}
  retryPreview=null
  // The command is already committed. A refresh error must not invite another debit.
  if(result.state)data.value={...result.state,messages:data.value.messages}
  await reload().catch(()=>{})
  await refreshTransferData().catch(()=>{})
  return result
 }
 async function challenge(previewId:string,password:string){return request('/api/operations/'+previewId+'/challenge',{password})}
 async function reset(){await request('/api/account-state/reset',{state:{}});data.value.messages=[];await reload();await refreshTransferData().catch(()=>{})}
 const salary=computed(()=>data.value.monthlySalary),salaryDay=computed(()=>data.value.salaryDay),reserve=computed(()=>data.value.reserveAmount),expenses=computed(()=>data.value.monthlyExpenses)
 const forecast=computed(()=>summary.value.points),cap=computed(()=>summary.value.cap),gap=computed(()=>summary.value.gap),fixedMonthly=computed(()=>summary.value.fixedMonthly),recurring=computed(()=>summary.value.recurring)
 const totalHoldings=computed(()=>data.value.holdings.reduce((s,h)=>s+h.principal+h.profit,0))
 return {data,salary,salaryDay,reserve,expenses,forecast,cap,gap,fixedMonthly,recurring,totalHoldings,accountWarning,conversationWarning,chat,message,reload,reset,operation,challenge,setOperationSource,payees,transferRecords,recentPayees,refreshTransferData,savePayee,payeeDetail,removePayee}
})
