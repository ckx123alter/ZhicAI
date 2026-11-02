<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ArrowUp, ArrowUpRight, ChevronRight, ChevronLeft, ChevronDown, Plus, X, Home, ChartPie, Layers, UserRound, ArrowDownLeft, CalendarDays, CalendarClock, Check, CircleHelp, RotateCcw, Signal, Wifi, BatteryFull, Wallet, Send, ShieldCheck, Landmark } from 'lucide-vue-next'
import { useBank, CHAT_FALLBACK_REPLY, todayISO, monthlyEquivalent, taskOccurrences, addHoldingLot, reduceHoldingLots, fixedCategories, type Payee, type MessageAction, type TaskType, type TaskFrequency } from './store'
import { categories, bills, evidence, products, suggestions, steps, type Product } from './data'
import { demoConfig } from './demoConfig'
import Chart from './Chart.vue'
import Mascot from './Mascot.vue'
import TransferHub from './TransferHub.vue'
import TransferIcon from './TransferIcon.vue'
import {signOut} from './auth'
const bank=useBank(), router=useRouter(), route=useRoute()
const reduceMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches
const availableMonths=[...new Set(bills.map(bill=>bill.date.slice(0,7)))].sort((a,b)=>b.localeCompare(a))
const page=computed(()=>route.path),navPage=computed(()=>page.value.startsWith('/transfer')?'/transfer':page.value==='/transactions'?'/bills':page.value==='/holdings'?'/me':page.value.startsWith('/wealth/product/')||page.value==='/risk-assessment'?'/wealth':page.value); const input=ref(''),busy=ref(false),chatOpen=ref(false),billBubbleDismissed=ref(true),toast=ref(''),toastTone=ref<'error'|'warning'|'success'|'info'>('info'),buyBufferAcknowledged=ref(false),selectedCategory=ref('全部'),month=ref(availableMonths[0]||todayISO().slice(0,7)),wealthMode=ref('products'),riskFilter=ref(1),exactRiskFilter=ref<number|null>(null),onlyOpen=ref(true),backendProductOrder=ref<string[]>([]),activeStep=ref(-1),thinking=ref(false),flying=ref(false),dock=ref(false)
const modal=ref(''),modalTransitioning=ref(false),resetting=ref(false),selectedProduct=ref<Product>(products[0]),selectedHoldingId=ref('D'),amount=ref<number|string|null>(null),day=ref(0),authorized=ref(false),taskType=ref<TaskType>('investment'),taskName=ref('申购理财'),taskProductId=ref('all'),taskFrequency=ref<TaskFrequency>('monthly'),taskDay=ref<number|null>(null),taskPayee=ref(''),riskLevel=ref(1),riskAnswers=ref<number[]>(Array(10).fill(3)),assessmentRisk=computed(()=>Math.max(1,Math.min(5,Math.round(riskAnswers.value.reduce((sum,n)=>sum+n,0)/riskAnswers.value.length)))),currentRisk=ref(Number(bank.data.riskLevel)||2),evidenceMode=ref<'recurring'|'frequent'>('recurring')
const selectedPayeeId=ref<string>(),selectedPayeeVersion=ref<number>(),paymentSourceMessage=ref<string>()
const oneTimeRecipient=ref<{holder_name:string;bank_name?:string|null;account_number:string}>()
const selectedRecipient=computed(()=>bank.payees.find(p=>p.id===selectedPayeeId.value))
const pendingPayeeAction=ref<MessageAction>(),pendingPayeeSource=ref<string>(),payeeInitialName=ref(''),payeeRequestKey=ref(0)
function clearPayeeRequest(){pendingPayeeAction.value=undefined;pendingPayeeSource.value=undefined;payeeInitialName.value='';payeeRequestKey.value=0}
async function startPayeeAddition(name=''){clearPayeeRequest();if(name)pendingPayeeAction.value={label:'转账',path:'/transfer',payee:name};payeeInitialName.value=name;payeeRequestKey.value=bank.payees.some(p=>p.display_name===name||p.holder_name===name)?0:Date.now();chatOpen.value=false;await router.push('/transfer/payees')}
async function chooseRecipient(){
 oneTimeRecipient.value=undefined
 pendingPayeeAction.value={label:'选择收款人',path:'/transfer/payees',amount:Number(amount.value)>0?Number(amount.value):undefined,...(modal.value==='task'?{taskType:taskType.value,taskName:taskName.value,frequency:taskFrequency.value,day:taskDay.value||undefined}:{} )}
 pendingPayeeSource.value=paymentSourceMessage.value;payeeRequestKey.value=0;chatOpen.value=false;await router.push('/transfer/payees')
}
async function startBankTransfer(){
 clearPayeeRequest();oneTimeRecipient.value=undefined;chatOpen.value=false
 await router.push('/transfer/bank')
}
async function bankTransferPrepared(recipient:{holder_name:string;bank_name:string;account_number:string},bankAmount:number){
 oneTimeRecipient.value={holder_name:recipient.holder_name,bank_name:recipient.bank_name,account_number:recipient.account_number}
 selectedPayeeId.value=undefined;selectedPayeeVersion.value=undefined
 clearPayeeRequest();await router.push('/transfer')
 showModal('transfer');amount.value=bankAmount
}
async function bankTransferSelectSaved(bankAmount:number|null){
 oneTimeRecipient.value=undefined
 pendingPayeeAction.value={label:'银行账号转账',path:'/transfer/payees',amount:bankAmount??undefined}
 pendingPayeeSource.value=undefined;payeeInitialName.value='';payeeRequestKey.value=0
 await router.push('/transfer/payees')
}
async function selectRecipient(payee:Payee){
 try{await bank.refreshTransferData()}catch(e){notify(e instanceof Error?e.message:'无法读取收款人');return}
 const current=bank.payees.find(p=>p.id===payee.id)
 if(!current||current.version!==payee.version){notify('收款资料已更新，请重新选择');return}
 const draft=pendingPayeeAction.value,source=pendingPayeeSource.value
 oneTimeRecipient.value=undefined
 clearPayeeRequest()
 if(draft?.taskType==='fixed_expense'){
  await router.push('/me');openTask('fixed_expense',draft.amount,draft.taskName,draft.day,draft.frequency,current.display_name)
 }else{
  await router.push('/transfer');showModal('transfer');amount.value=draft?.amount??null;taskPayee.value=current.display_name
 }
 selectedPayeeId.value=current.id;selectedPayeeVersion.value=current.version;paymentSourceMessage.value=source;bank.setOperationSource(source)
}
const eligibleTaskProducts=computed(()=>products.filter(product=>product.open&&product.risk<=currentRisk.value)),selectedTaskProduct=computed(()=>products.find(product=>product.id===taskProductId.value)||null)
const riskQuestions=[
 {title:'投资出现短期亏损时，你通常会怎么做？',options:[{text:'尽快赎回，避免继续亏损',score:1},{text:'先观察，再决定是否调整',score:3},{text:'继续持有，必要时考虑加仓',score:5}]},
 {title:'你能接受投资本金出现多大波动？',options:[{text:'基本不能接受亏损',score:1},{text:'可以接受小幅波动',score:3},{text:'可以接受较大波动',score:5}]},
 {title:'这笔资金预计多久不会用到？',options:[{text:'3个月以内',score:1},{text:'3个月至1年',score:3},{text:'1年以上',score:5}]},
 {title:'你目前的收入和工作状态如何？',options:[{text:'收入不稳定或近期可能变化',score:1},{text:'收入基本稳定',score:3},{text:'收入稳定且有长期保障',score:5}]},
 {title:'除日常开支外，你有多少应急备用资金？',options:[{text:'不足3个月开支',score:1},{text:'约3至6个月开支',score:3},{text:'超过6个月开支',score:5}]},
 {title:'计划投入的金额占可投资资产多少？',options:[{text:'占比较高，亏损会明显影响生活',score:1},{text:'占比适中，对生活影响有限',score:3},{text:'占比较低，即使亏损也能承受',score:5}]},
 {title:'你过去的投资经历是？',options:[{text:'很少接触理财或主要买存款',score:1},{text:'买过银行理财或债券类产品',score:3},{text:'熟悉多类投资产品及其风险',score:5}]},
 {title:'如果市场持续下跌一段时间，你会？',options:[{text:'担心本金损失并倾向赎回',score:1},{text:'根据资金安排考虑是否持有',score:3},{text:'能接受波动并按计划持有',score:5}]},
 {title:'你对这笔资金的主要安排是什么？',options:[{text:'近期有明确用途，优先保证本金和流动性',score:1},{text:'中期备用，希望收益与稳健兼顾',score:3},{text:'长期投资，愿意承担波动争取更高收益',score:5}]},
 {title:'你认为理财收益与风险的关系应该是？',options:[{text:'更看重本金安全，收益低一些也可以',score:1},{text:'希望风险和收益大致平衡',score:3},{text:'接受较高风险以争取更高收益',score:5}]}
]
const riskAnswerPresets:Record<number,number[]>={1:Array(10).fill(1),2:[1,1,1,1,1,3,3,3,3,3],3:Array(10).fill(3),4:[3,3,3,3,3,5,5,5,5,5],5:Array(10).fill(5)}
watch(()=>bank.data.riskLevel,level=>currentRisk.value=level)
watch(currentRisk,level=>{riskAnswers.value=[...(riskAnswerPresets[level]||riskAnswerPresets[2])]},{immediate:true})
watch([taskType,taskName,taskProductId,taskFrequency,taskDay,taskPayee,riskLevel,amount,selectedPayeeId,selectedPayeeVersion],()=>{if(modal.value==='task')authorized.value=false})
watch([amount,selectedProduct,modal,selectedPayeeId,selectedPayeeVersion],()=>{buyBufferAcknowledged.value=false})
watch(taskProductId,id=>{if(modal.value==='task'&&taskType.value==='investment')riskLevel.value=products.find(product=>product.id===id)?.risk||1})
const greeting=ref(false), completed=ref(false), landing=ref(false), surprised=ref(false), riskRejected=ref(false), wealthRiskBubbleDismissed=ref(true), surpriseVersion=ref(0)
let greetingTimer: ReturnType<typeof setTimeout>, completeTimer: ReturnType<typeof setTimeout>, landingTimer: ReturnType<typeof setTimeout>, surpriseTimer: ReturnType<typeof setTimeout>, riskRejectTimer: ReturnType<typeof setTimeout>
const riskRejectText=computed(()=>`该产品风险等级为 R${selectedProduct.value.risk}，超出你的 C${currentRisk.value} ${capacityLabel(currentRisk.value)}可投资范围，无法申购。`)
const mascotState=computed(()=>flying.value?'travel':thinking.value?'thinking':greeting.value?'greeting':completed.value?'success':input.value.trim()?'listening':'idle')
// 右下角是全局入口，不跟随账户预警改变表情；眉毛只在当前页面的角色状态中出现。
const dockMascotState=computed(()=>riskRejected.value?'riskReject':surprised.value?'surprised':landing.value?'landing':mascotState.value)
const dockBubble=ref<HTMLElement|null>(null),dockBubbleSpace=ref(0)
const billBubbleIndex=ref(0)
const billSourceMessageId=ref<string|number>(),wealthSourceMessageId=ref<string|number>()
const wealthBubbleDismissed=ref(true),wealthBubbleIndex=ref(0)
watch(page,path=>{
 if(!path.startsWith('/transfer'))clearPayeeRequest()
 billBubbleDismissed.value=true
 wealthBubbleDismissed.value=true
 wealthRiskBubbleDismissed.value=true
 billBubbleIndex.value=0
 wealthBubbleIndex.value=0
 dockBubbleSpace.value=0
 if(modal.value){modal.value='';authorized.value=false;modalTransitioning.value=false}
},{flush:'sync'})
watch(dockBubble,(element,_,onCleanup)=>{
 if(!element){dockBubbleSpace.value=0;return}
 const position=()=>{if(dockBubble.value!==element)return;const viewport=document.querySelector<HTMLElement>('.page-scroll');if(!viewport)return;dockBubbleSpace.value=Math.max(85,Math.ceil(viewport.getBoundingClientRect().bottom-element.getBoundingClientRect().top+14));nextTick(()=>viewport.scrollTo({top:viewport.scrollHeight,behavior:'auto'}))}
 const observer=new ResizeObserver(position);observer.observe(element);const frame=requestAnimationFrame(position);onCleanup(()=>{observer.disconnect();cancelAnimationFrame(frame)})
})
function closeDockBubble(close:()=>void){close();dockBubbleSpace.value=0;nextTick(()=>{const viewport=document.querySelector<HTMLElement>('.page-scroll');viewport?.scrollTo({top:viewport.scrollHeight,behavior:'instant'})})}
function closeBillBubble(){closeDockBubble(()=>billBubbleDismissed.value=true)}
function closeWealthBubble(){closeDockBubble(()=>wealthBubbleDismissed.value=true)}
function nextWealthBubble(){if(wealthBubbleIndex.value>=wealthRecommendationSegments.value.length-1)return;wealthBubbleIndex.value++;bank.message('agent',wealthRecommendationSegments.value[wealthBubbleIndex.value],undefined,wealthSourceMessageId.value)}
function nextBillBubble(){if(billBubbleIndex.value>=billSegments.value.length-1)return;billBubbleIndex.value++;bank.message('agent',billSegments.value[billBubbleIndex.value],undefined,billSourceMessageId.value)}
function greetMascot(){clearTimeout(greetingTimer);greeting.value=true;greetingTimer=setTimeout(()=>greeting.value=false,800)}
function completeMascot(){clearTimeout(completeTimer);completed.value=true;completeTimer=setTimeout(()=>completed.value=false,1500)}
function playLanding(){clearTimeout(landingTimer);landing.value=true;landingTimer=setTimeout(()=>landing.value=false,1150)}
function playSurprise(){if(reduceMotion)return;clearTimeout(surpriseTimer);surprised.value=true;surpriseVersion.value++;surpriseTimer=setTimeout(()=>surprised.value=false,750)}
function playRiskReject(){clearTimeout(riskRejectTimer);riskRejected.value=true;wealthRiskBubbleDismissed.value=false;bank.message('agent',riskRejectText.value,{label:'重新评估',path:'/risk-assessment',icon:'up-right'},`risk:${selectedProduct.value.id}`);riskRejectTimer=setTimeout(()=>riskRejected.value=false,1000)}
const money=(v:number)=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(v)
const holdingProfit=(holding:{profit?:number})=>Number(holding.profit)||0
const riskLabel=(risk:number)=>risk===1?'低风险':risk===2?'中低风险':risk===3?'中风险':risk===4?'中高风险':'高风险'
const capacityLabel=(level:number)=>level===1?'谨慎型':level===2?'稳健型':level===3?'平衡型':level===4?'进取型':'激进型'
function notify(t:string,tone?:'error'|'warning'|'success'|'info'){if(/已完成|已创建|已计入|已设置|已更新/.test(t))completeMascot();toast.value=t;toastTone.value=tone||(/已完成|已创建|已计入|已设置|已更新|已授权/.test(t)?'success':/不足|不匹配|超出|无法|不能|无效|失败|错误|请先|请填写|请选择|不在|没有|暂不可|未执行|过期|停用|缺失|不可|请检查|起购金额|目前不|低于/.test(t)?'error':'info');setTimeout(()=>{if(toast.value===t)toast.value=''},3200)}
watch(()=>bank.accountWarning,failed=>{if(failed)notify('账户数据暂未保存，请检查知财服务后重试','warning')})
watch(()=>bank.conversationWarning,failed=>{if(failed)notify('对话暂未保存，请检查知财服务后重试','warning')})
function showModal(name:string){bank.setOperationSource();paymentSourceMessage.value=undefined;authorized.value=false;modal.value=name}
function openTask(type:TaskType='investment',presetAmount?:number,name=type==='investment'?'申购理财':type==='redemption'?'赎回理财':'其他',preferredDay?:number,frequency:TaskFrequency='monthly',payee=''){selectedPayeeId.value=undefined;selectedPayeeVersion.value=undefined;taskType.value=type;taskName.value=type==='fixed_expense'?(name==='房租'?'租金':fixedCategories.includes(name)?name:'其他'):type==='redemption'?'赎回理财':'申购理财';taskProductId.value=type==='investment'?(eligibleTaskProducts.value[0]?.id||''): 'all';taskPayee.value=payee;riskLevel.value=1;taskFrequency.value=frequency;amount.value=presetAmount??null;taskDay.value=preferredDay??new Date().getDate();showModal('task')}
let taskCheckTimer:ReturnType<typeof setInterval>|undefined
function checkDueTasks(){void bank.reload().catch(()=>{})}
function onVisible(){if(document.visibilityState==='visible')checkDueTasks()}
onMounted(()=>{window.addEventListener('popstate',cancelFlightOnHistory);document.addEventListener('visibilitychange',onVisible);checkDueTasks();taskCheckTimer=setInterval(checkDueTasks,60_000)})
onUnmounted(()=>{window.removeEventListener('popstate',cancelFlightOnHistory);document.removeEventListener('visibilitychange',onVisible);if(taskCheckTimer)clearInterval(taskCheckTimer)})
const nav=[{path:'/',label:'首页',icon:Home},{path:'/bills',label:'账单',icon:ChartPie},{path:'/wealth',label:'理财',icon:Layers},{path:'/transfer',label:'转账',icon:TransferIcon},{path:'/me',label:'我的',icon:UserRound}]
const recurringEvidence=computed(()=>evidence.recurring.filter(item=>item.date.slice(0,7)<=month.value)),recurringAmount=computed(()=>recurringEvidence.value.length?recurringEvidence.value.reduce((sum,item)=>sum+item.amount,0)/recurringEvidence.value.length:0),recurringDay=computed(()=>recurringEvidence.value.length?Number(recurringEvidence.value[recurringEvidence.value.length-1].date.slice(-2)):null)
const serverBillSegments=ref<string[]>([]),serverWealthSegments=ref<string[]>([]),serverRecommendedIds=ref<string[]>([])
const recommendedProducts=computed(()=>serverRecommendedIds.value.map(id=>products.find(p=>p.id===id)).filter((p):p is Product=>Boolean(p)))
const wealthCueProductIds=computed(()=>recommendedProducts.value.map(p=>p.id))
const wealthRecommendationSegments=computed(()=>serverWealthSegments.value)

const flightElement=ref<HTMLElement>(), flightTarget=ref(''), flightPhase=ref('')
let flightVersion=0, flightAnimation:Animation|undefined
const afterFrame=()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()))
function stopFlight(){flightVersion++;flightAnimation?.cancel();clearTimeout(landingTimer);clearTimeout(surpriseTimer);landing.value=false;surprised.value=false;flying.value=false;flightTarget.value='';flightPhase.value=''}
async function navigate(path:string,agent=false,sourceMessageId?:string|number){
 if(!agent){stopFlight();if(path==='/transfer'&&page.value.startsWith('/transfer/')&&pendingPayeeAction.value)clearPayeeRequest();if(path==='/wealth')wealthMode.value='products';if(path===page.value)return;if(path==='/'&&!reduceMotion){playSurprise();await new Promise(resolve=>setTimeout(resolve,320))}await router.push(path);if(path!=='/')playSurprise();return}
 clearTimeout(surpriseTimer);surprised.value=false
 if(reduceMotion){await router.push(path);return}
 if(flying.value)stopFlight()
 clearTimeout(landingTimer);landing.value=false
 await nextTick()
 const avatars=Array.from(document.querySelectorAll<HTMLElement>('.message.agent .message-avatar'))
 const source=(sourceMessageId===undefined?undefined:avatars.find(avatar=>avatar.closest('.message')?.getAttribute('data-message-id')===String(sourceMessageId)))||avatars.at(-1)||document.querySelector<HTMLElement>('.floating-character')
 const phone=document.querySelector<HTMLElement>('.phone')
 const destination=document.querySelector<HTMLElement>('.floating-character')
 if(!source||!phone||!destination){await router.push(path);return}
 source.scrollIntoView({behavior:'instant',block:'nearest'});await afterFrame()
 const box=phone.getBoundingClientRect(), from=source.getBoundingClientRect(), to=destination.getBoundingClientRect()
 const point=(r:DOMRect)=>({x:r.left-box.left+r.width/2,y:r.top-box.top+r.height/2})
 const start=point(from), end=point(to)
 const control={x:(start.x+end.x)/2+30,y:Math.min(start.y,end.y)-110}
 const ease=(t:number)=>t*t*(3-2*t)
 // Sample one continuous arc; scale up gradually, then match the resting dock exactly.
 const frames:Keyframe[]=Array.from({length:25},(_,i)=>{
  const t=i/24,u=1-t
  const x=u*u*start.x+2*u*t*control.x+t*t*end.x
  const y=u*u*start.y+2*u*t*control.y+t*t*end.y
  const scale=(initial:number,final:number)=>t<.35?initial+(1.25-initial)*ease(t/.35):1.25+(final-1.25)*ease((t-.35)/.65)
  return {offset:t,opacity:Math.min(1,t*12),transform:`translate3d(${x-32}px,${y-33.5}px,0) scale(${scale(from.width/64,to.width/64)},${scale(from.height/67,to.height/67)}) rotate(${Math.sin(t*Math.PI)*-5}deg)`}
 })
 const version=++flightVersion
 flying.value=true;flightTarget.value=path;flightPhase.value='to-page';await nextTick()
 let arrived=false
 try{
  flightAnimation=flightElement.value!.animate(frames,{duration:840,easing:'cubic-bezier(.3,0,.25,1)',fill:'forwards'})
  const animationFinished=flightAnimation.finished
  void animationFinished.catch(()=>{})
  // Start the page entrance while the same mascot is still moving above it.
  await new Promise(resolve=>setTimeout(resolve,280));if(version!==flightVersion)return
  await router.push(path);await nextTick();await afterFrame()
  flightPhase.value='landing'
  await animationFinished
  arrived=true
 }catch(error){if(version===flightVersion){console.warn('角色转场已回退',error);await router.push(path)}}
 finally{if(version===flightVersion){flying.value=false;flightTarget.value='';flightPhase.value='';flightAnimation?.cancel();if(arrived)playLanding()}}
}
function cancelFlightOnHistory(){if(flying.value)stopFlight()}
async function openMessageAction(action:MessageAction,messageId:string|number){
 if(busy.value||flying.value)return
 if(action.modal==='payee'){
  pendingPayeeAction.value=action;pendingPayeeSource.value=String(messageId);payeeInitialName.value=action.payee||'';payeeRequestKey.value=Date.now();chatOpen.value=false;await router.push('/transfer/payees');return
 }
 if(action.payeeId){
  try{await bank.refreshTransferData()}catch(e){notify(e instanceof Error?e.message:'无法读取收款人');return}
  const payee=bank.payees.find(p=>p.id===action.payeeId)
  if(!payee||payee.version!==action.payeeVersion){pendingPayeeAction.value=action;pendingPayeeSource.value=String(messageId);payeeRequestKey.value=0;chatOpen.value=false;await router.push('/transfer/payees');notify('收款资料已更新，请重新选择');return}
 }
 if(action.mode==='products')wealthMode.value='products'
 if(action.mode==='products'){const requested=action.risk||currentRisk.value;riskFilter.value=requested<=2?requested:5;exactRiskFilter.value=requested>=3?requested:null}
 if(action.month)month.value=action.month
 if(action.bubbleSegments&&action.path==='/bills')serverBillSegments.value=action.bubbleSegments
 if(action.bubbleSegments&&action.path==='/wealth')serverWealthSegments.value=action.bubbleSegments
 if(action.resultIds)serverRecommendedIds.value=action.resultIds
 if(action.amount!==undefined)amount.value=action.amount
 if(action.day!==undefined)day.value=action.day
 if(action.modal){const routeChanges=page.value!==action.path;modalTransitioning.value=routeChanges;chatOpen.value=false;if(routeChanges)await router.push(action.path);await nextTick()}
 else if(chatOpen.value){chatOpen.value=false;stopFlight();await router.push(action.path)}
 else await navigate(action.path,true,messageId)
 if(page.value!==action.path)return
 if(action.path==='/bills'){billSourceMessageId.value=messageId;billBubbleIndex.value=0;billBubbleDismissed.value=false;billSegments.value[0]&&bank.message('agent',billSegments.value[0],undefined,messageId)}
 if(action.path==='/wealth'&&action.mode==='products'&&!action.modal){wealthSourceMessageId.value=messageId;wealthBubbleIndex.value=0;wealthBubbleDismissed.value=false;wealthRecommendationSegments.value[0]&&bank.message('agent',wealthRecommendationSegments.value[0],undefined,messageId)}
 if(action.modal){
  if(action.productId)selectedProduct.value=products.find(p=>p.id===action.productId)||products[0]
  if(action.payee)taskPayee.value=action.payee
  if(action.modal==='task'){openTask(action.taskType||'investment',action.amount,action.taskName,action.day,action.frequency,action.payee);if(action.productId)taskProductId.value=action.productId}
  else showModal(action.modal)
  selectedPayeeId.value=action.payeeId;selectedPayeeVersion.value=action.payeeVersion;oneTimeRecipient.value=action.manualRecipient?{...action.manualRecipient}:undefined;paymentSourceMessage.value=String(messageId);bank.setOperationSource(String(messageId))
 }
}
function messageActions(message:{role:'user'|'agent';action?:MessageAction;actions?:MessageAction[]}){return message.actions??(message.action?[message.action]:[])}
function messageFlashTarget(message:{role:'user'|'agent';text:string}):'bills'|'wealth'|null{if(message.role!=='agent')return null;if(message.text.includes('点击闪烁可以查看消费明细'))return 'bills';if(message.text.includes('点击闪烁可以查看产品详情'))return 'wealth';return null}
function openMessageFlashTarget(message:{role:'user'|'agent';text:string}){const target=messageFlashTarget(message);chatOpen.value=false;if(target==='bills'){selectedCategory.value='全部';void navigate('/transactions')}else if(target==='wealth'){const product=recommendedProducts.value[0];if(product)void openProduct(product);else void navigate('/wealth')}}
const isProductDetail=computed(()=>page.value.startsWith('/wealth/product/')),productDetailId=computed(()=>String(route.params.productId||'')),detailProduct=computed(()=>products.find(p=>p.id===productDetailId.value)||products[0])
const activeHoldings=computed(()=>bank.data.holdings.filter(h=>h.principal>0)),selectedHolding=computed(()=>activeHoldings.value.find(h=>h.productId===selectedHoldingId.value)||activeHoldings.value[0]),selectedHoldingProduct=computed(()=>products.find(p=>p.id===selectedHolding.value?.productId)||products[0])
const filteredProducts=computed(()=>{const rank=new Map(backendProductOrder.value.map((id,index)=>[id,index]));return products.filter(p=>{const inRiskGroup=exactRiskFilter.value!==null?p.risk===exactRiskFilter.value:riskFilter.value===1?p.risk===1:riskFilter.value===2?p.risk===2:p.risk>=3;return inRiskGroup&&(!onlyOpen.value||p.open)}).sort((a,b)=>(rank.get(a.id)??Number.MAX_SAFE_INTEGER)-(rank.get(b.id)??Number.MAX_SAFE_INTEGER))})
let productRankingRequest=0
async function refreshProductRanking(){
 const request=++productRankingRequest
 const riskMin=exactRiskFilter.value??(riskFilter.value===1?1:riskFilter.value===2?2:3)
 const riskMax=exactRiskFilter.value??(riskFilter.value===1?1:riskFilter.value===2?2:5)
 const query=new URLSearchParams({risk_min:String(riskMin),risk_max:String(riskMax),only_open:String(onlyOpen.value),limit:'50'})
 try{const response=await fetch(`/api/products/recommend?${query}`);if(!response.ok)throw new Error(`产品推荐接口返回 ${response.status}`);const ranked=await response.json() as Product[];if(request===productRankingRequest)backendProductOrder.value=ranked.map(product=>product.id)}catch(error){if(request===productRankingRequest)console.warn('无法获取后端理财推荐排序',error)}
}
watch([riskFilter,exactRiskFilter,onlyOpen],()=>{void refreshProductRanking()},{immediate:true})
function scrollConversation(inSheet:boolean,behavior:ScrollBehavior='auto'){nextTick(()=>{const container=document.querySelector<HTMLElement>(inSheet?'.chat-history':'.page-scroll');container?.scrollTo({top:container.scrollHeight,behavior})})}
watch(page,path=>{if(path==='/')scrollConversation(false)})
watch(()=>bank.data.messages.length,()=>{if(page.value==='/')scrollConversation(false,'smooth');if(chatOpen.value)scrollConversation(true,'smooth')})
watch(chatOpen,open=>{if(open)setTimeout(()=>scrollConversation(true),120)})
const monthBills=computed(()=>bills.filter(bill=>bill.date.startsWith(month.value))),monthAvailable=computed(()=>monthBills.value.length>0),visibleCategories=computed(()=>categories.map(category=>{const entries=monthBills.value.filter(bill=>bill.category===category.name);return {...category,count:entries.length,amount:entries.reduce((sum,bill)=>sum+bill.amount,0)}})),monthTotal=computed(()=>visibleCategories.value.reduce((sum,category)=>sum+category.amount,0)),categoryChartLabel=computed(()=>`消费分类：${visibleCategories.value.filter(category=>category.amount>0).map(category=>`${category.name}${monthTotal.value?Math.round(category.amount/monthTotal.value*100):0}%`).join('，')}`),frequentEvidence=computed(()=>monthBills.value.filter(bill=>bill.merchant==='瑞幸咖啡'&&bill.amount<=demoConfig.ledger.smallAmountThreshold)),frequentTotal=computed(()=>frequentEvidence.value.reduce((sum,bill)=>sum+bill.amount,0)),evidenceTransactions=computed(()=>evidenceMode.value==='recurring'?recurringEvidence.value:frequentEvidence.value),billSegments=computed(()=>serverBillSegments.value)
const categoryBills=computed(()=>monthBills.value.filter(b=>(selectedCategory.value==='全部'||b.category===selectedCategory.value)).sort((a,b)=>b.date.localeCompare(a.date)))
const pieOption=computed(()=>({animationDuration:650,tooltip:{trigger:'item',confine:true,formatter:'{b}：¥{c} ({d}%)',extraCssText:'max-width:150px;white-space:normal;overflow-wrap:anywhere;'},series:[{type:'pie',radius:['66%','88%'],center:['50%','50%'],label:{show:false},itemStyle:{borderColor:'#fff',borderWidth:4},data:visibleCategories.value.map(c=>({name:c.name,value:c.amount,itemStyle:{color:c.color}})),emphasis:{scaleSize:4}}]}))
const salaryDay=computed(()=>bank.forecast.findIndex((point,index)=>index>0&&point.income>0))
const cashflowChartPoints=computed(()=>bank.forecast)
const lineOption=computed(()=>({animationDuration:650,grid:{left:40,right:20,top:25,bottom:24},tooltip:{trigger:'axis',confine:true,position:(point:number[],_params:any,_dom:any,_rect:any,size:any)=>{const [width,height]=size.contentSize,[viewWidth,viewHeight]=size.viewSize;let x=point[0]+12,y=point[1]-height-12;if(x+width>viewWidth-8)x=point[0]-width-12;if(y<8)y=point[1]+12;return[Math.max(8,Math.min(x,viewWidth-width-8)),Math.max(8,Math.min(y,viewHeight-height-8))]},formatter:(p:any)=>{const e=cashflowChartPoints.value[p[0]?.dataIndex];if(!e)return'';const events=[e.income?`工资到账 +¥${money(e.income)}`:'',...e.fixedExpensesToday.map(item=>`${item.name} -¥${money(item.amount)}`)].filter(Boolean);return`${e.day===0?'今天':`${e.day}日后`}<br/>余额 ¥${money(e.balance)}${events.length?`<br/>${events.join('<br/>')}`:''}`}},xAxis:{type:'category',data:cashflowChartPoints.value.map(e=>e.day===0?'今天':`${e.day}日后`),axisLine:{show:false},axisTick:{show:false},axisLabel:{color:'#8a94a8',fontSize:10,formatter:(value:string,index:number)=>index===salaryDay.value?`${bank.salaryDay}日`:index%4===0?value:''}},yAxis:{type:'value',axisLabel:{color:'#8a94a8',fontSize:10,formatter:(v:number)=>v>=1000?`${v/1000}k`:v},splitLine:{lineStyle:{color:'#edf0f5',type:'dashed'}}},series:[{type:'line',data:cashflowChartPoints.value.map(e=>e.balance),showSymbol:false,symbolSize:4,lineStyle:{color:'#315edb',width:2.5},itemStyle:{color:'#315edb'},areaStyle:{color:'#eaf0fe',opacity:.6},markPoint:{symbol:'circle',symbolSize:9,itemStyle:{color:'#bb845b'},label:{show:true,formatter:'工资到账',position:'top',fontSize:9,color:'#8d674d'},data:[{coord:[`${salaryDay.value}日后`,cashflowChartPoints.value[salaryDay.value]?.balance]}]},markLine:{symbol:'none',label:{formatter:`警戒线 ¥${money(bank.reserve)}`,position:'insideEndTop',color:'#b46d42'},lineStyle:{color:'#c68b66',type:'dashed'},data:[{yAxis:bank.reserve}]}}]}))
function selectCategory(name:string){selectedCategory.value=name;navigate('/transactions')}
async function send(text=input.value,fromSheet=false){
 if(!text.trim()||busy.value)return
 busy.value=true;thinking.value=true;input.value=''
 if(!fromSheet){chatOpen.value=false;if(page.value!=='/')await router.push('/')}
 try{await bank.chat(text,()=>{thinking.value=false});scrollConversation(fromSheet,'smooth')}
 catch(error){console.warn('知财对话显示失败',error);bank.message('agent',CHAT_FALLBACK_REPLY)}
 finally{thinking.value=false;busy.value=false}
}
const operationBusy=ref(false),pendingPreview=ref(''),pendingSignature=ref(''),strangerAcknowledged=ref(false)
const challengePassword=ref(''),pendingChallenge=ref<{command:any;previewId:string;signature:string;ack:boolean}|null>(null)
watch([amount,selectedProduct,modal,taskType,taskName,taskProductId,taskFrequency,taskDay,taskPayee,selectedPayeeId,selectedPayeeVersion,oneTimeRecipient],()=>{if(modal.value==='challenge')return;pendingPreview.value='';pendingSignature.value='';strangerAcknowledged.value=false;pendingChallenge.value=null;challengePassword.value=''})
async function execute(){
 if(operationBusy.value)return
 const v=Number(amount.value)
 const command:any=modal.value==='task'?{kind:'task',amount:v,task_type:taskType.value,name:taskName.value,product_id:taskType.value==='fixed_expense'?null:taskProductId.value,payee:taskPayee.value||null,frequency:taskFrequency.value,execution_day:taskDay.value}
 :{kind:modal.value==='buy'?'purchase':modal.value==='transfer'?'transfer':'redemption',amount:v,product_id:modal.value==='transfer'?null:selectedProduct.value.id,payee:modal.value==='transfer'?taskPayee.value:null}
 if(modal.value==='transfer'&&oneTimeRecipient.value){command.payee=oneTimeRecipient.value.holder_name;command.manual_recipient=oneTimeRecipient.value}
 else if(modal.value==='transfer'||modal.value==='task'&&taskType.value==='fixed_expense'){if(!selectedRecipient.value)return notify('请选择常用收款人');command.payee_id=selectedPayeeId.value;command.payee_version=selectedPayeeVersion.value}
 if(modal.value==='task'&&!authorized.value)return notify('请先确认授权')
 if(modal.value==='transfer'&&oneTimeRecipient.value&&!strangerAcknowledged.value){strangerAcknowledged.value=true;return}
 operationBusy.value=true
 try{
  const signature=JSON.stringify(command),ack=buyBufferAcknowledged.value&&pendingSignature.value===signature
  const result=await bank.operation(command,ack,ack?pendingPreview.value:undefined)
  if(result.requires_buffer_confirmation){pendingPreview.value=result.preview_id;pendingSignature.value=signature;buyBufferAcknowledged.value=true;return}
  if(result.requires_challenge){pendingChallenge.value={command,previewId:result.preview_id,signature,ack};challengePassword.value='';modal.value='challenge';return}
  notify(result.text);modal.value='';oneTimeRecipient.value=undefined
 }catch(error){buyBufferAcknowledged.value=false;notify(error instanceof Error?error.message:'操作失败','error')}
 finally{operationBusy.value=false}
}
async function submitChallenge(){
 const pending=pendingChallenge.value
 if(!pending||operationBusy.value)return
 operationBusy.value=true
 try{
  const token=await bank.challenge(pending.previewId,challengePassword.value)
  const result=await bank.operation(pending.command,pending.ack,pending.previewId,token.challenge_token)
  notify(result.text);modal.value='';oneTimeRecipient.value=undefined;pendingChallenge.value=null;challengePassword.value=''
 }catch(error){notify(error instanceof Error?error.message:'操作失败','error')}
 finally{operationBusy.value=false}
}
async function cancelTask(id:string){try{const result=await bank.operation({kind:'cancel_task',task_id:id});notify(result.text)}catch(error){notify(String(error),'error')}}
function demo(i:number){if(i===5){riskFilter.value=5;exactRiskFilter.value=3;wealthMode.value='products'}if(page.value==='/'){void send(suggestions[i]);return}chatOpen.value=true;void nextTick(()=>send(suggestions[i],true))}
async function openProduct(p:Product){selectedProduct.value=p;await navigate(`/wealth/product/${p.id}`)}
async function openHoldings(productId:string){selectedHoldingId.value=productId;await navigate('/holdings')}
function redeemHolding(p:Product){const holding=bank.data.holdings.find(h=>h.productId===p.id);if(!holding||holding.principal<=0)return notify('当前没有该产品的持仓');if(!p.redeemable)return notify('该产品暂不可赎回');selectedProduct.value=p;amount.value=holding.principal;showModal('redeem')}
function buy(p:Product){selectedProduct.value=p;if(p.risk>currentRisk.value){playRiskReject();return}amount.value=Math.min(1000,Math.max(0,bank.data.balance-bank.reserve));showModal('buy')}
async function confirmRiskAssessment(){try{await bank.operation({kind:'risk',answers:riskAnswers.value});currentRisk.value=bank.data.riskLevel;await navigate('/wealth')}catch(error){notify(String(error),'error')}}
async function reset(){if(resetting.value)return;resetting.value=true;let clearFailed=false;try{await bank.reset()}catch{clearFailed=true}await router.push('/');activeStep.value=-1;modal.value='';chatOpen.value=false;billBubbleIndex.value=0;billBubbleDismissed.value=true;wealthBubbleIndex.value=0;wealthBubbleDismissed.value=true;wealthRiskBubbleDismissed.value=true;dockBubbleSpace.value=0;resetting.value=false;if(clearFailed)notify('对话数据清除失败，请重试','error');else toast.value=''}
watch(modal,v=>{if(v)nextTick(()=>document.querySelector<HTMLElement>('.sheet input, .sheet button')?.focus());else modalTransitioning.value=false})
</script>

<template>
<div class="workspace">
 <aside class="demo-panel"><div class="wordmark"><span class="brand-symbol">知</span><span class="brand-word">财</span><span class="small-label">演示流程</span></div><div class="panel-main"><h1>资金管理与<br>决策智能体</h1><div class="demo-steps"><button v-for="(s,i) in steps" :key="s" :class="{selected:activeStep===i}" :disabled="busy" @click="demo(i)"><span class="step-number">0{{i+1}}</span><span>{{s}}</span><ArrowUpRight :size="15" /></button></div></div><button class="reset" :disabled="busy||flying||resetting" @click="reset"><RotateCcw :size="14"/>重置演示</button></aside>
 <div class="phone"><div class="status-bar"><span>9:41</span><span class="status-right" aria-label="蜂窝网络、Wi-Fi、电池"><Signal :size="15" :stroke-width="2.2"/><Wifi :size="16" :stroke-width="2.2"/><BatteryFull :size="19" :stroke-width="2"/></span></div><header class="app-header"><div class="mini-brand"><span class="brand-symbol">知</span><span class="brand-word">财</span></div></header>
 <main class="page-scroll" :key="page" :style="dockBubble?{paddingBottom:dockBubbleSpace+'px',scrollBehavior:'smooth'}:undefined">
 <section v-if="page==='/'" class="home-page">
  <template v-if="!bank.data.messages.length"><div class="home-intro"><button class="hero-mascot" aria-label="和知财打招呼" @click="greetMascot"><Mascot :state="mascotState"/><span class="mascot-shadow"/></button><h2>告诉知财，你想做什么？</h2></div></template>
  <template v-else><div class="messages" @click="scrollConversation(false,'smooth')"><div v-for="m in bank.data.messages" :key="m.id" :data-message-id="m.id" :class="['message',m.role]"><Mascot v-if="m.role==='agent'" class="message-avatar" compact/><div class="message-content"><p v-if="messageFlashTarget(m)" class="message-flash-line">点击<button type="button" class="bubble-flash-word" @click.stop="openMessageFlashTarget(m)">闪烁</button>可以查看{{messageFlashTarget(m)==='bills'?'消费明细':'产品详情'}}。</p><p v-else>{{m.text}}</p><div v-if="m.role==='agent'&&messageActions(m).length" class="message-actions"><button v-for="action in messageActions(m)" :key="`${action.label}-${action.path}`" class="message-action" :disabled="busy||flying" @click="openMessageAction(action,m.id)">{{action.label}}<ArrowUpRight v-if="action.icon==='up-right'" :size="15"/><ChevronRight v-else :size="15"/></button></div></div></div><div v-if="thinking&&!flying" class="chat-thinking"><Mascot class="message-avatar" state="thinking"/><span class="chat-thinking-text">知财正在思考…</span></div><div class="conversation-end"/></div></template>
 </section>
 <section v-else-if="page==='/bills'" class="bills-page"><div class="page-title"><h2>账单分析</h2><label class="period-wrap"><select class="period" v-model="month" aria-label="选择账单月份"><option v-for="billMonth in availableMonths" :key="billMonth" :value="billMonth">{{Number(billMonth.slice(5))}}月</option></select><ChevronDown :size="13"/></label></div><div class="bill-summary"><span class="muted">本月消费</span><button class="bill-detail-link" aria-label="查看消费明细" @click="selectedCategory='全部';navigate('/transactions')"><span class="big-number"><small>¥</small>{{money(monthTotal)}}<span>.00</span></span><ChevronRight :size="17" :class="{'detail-cue':billBubbleIndex===billSegments.length-1&&!billBubbleDismissed}"/></button></div><p v-if="!monthAvailable" class="empty-month">该月份暂无账单数据</p><div v-else class="category-card"><div class="donut"><Chart :option="pieOption" :label="categoryChartLabel"/><div class="donut-label"><small>消费构成</small></div></div><div class="legend"><button v-for="c in visibleCategories" :key="c.name" @click="selectCategory(c.name)"><i :style="{background:c.color}"/><span>{{c.name}}</span><b>{{money(c.amount)}}<small class="amount-decimal">.00</small></b><small>{{monthTotal?Math.round(c.amount/monthTotal*100):0}}%</small></button></div></div><button v-if="monthAvailable&&recurringEvidence.length" class="finding" @click="evidenceMode='recurring';showModal('evidence')"><div class="finding-icon"><CalendarClock :size="19"/></div><div><strong>视频会员 - 疑似周期扣款</strong><p>连续 {{recurringEvidence.length}} 个月{{recurringDay?'，每月 '+recurringDay+' 日':''}}扣款 ¥{{money(recurringAmount)}}</p></div><ChevronRight :size="17"/></button><button v-if="monthAvailable&&frequentEvidence.length" class="finding" @click="evidenceMode='frequent';showModal('evidence')"><div class="finding-icon"><Wallet :size="19"/></div><div><strong>咖啡 - 小额高频</strong><p>{{frequentEvidence.length}} 笔小额消费，合计 ¥{{money(frequentTotal)}}</p></div><ChevronRight :size="17"/></button></section>
 <section v-else-if="page==='/transactions'" class="transactions-page"><div class="page-title"><div class="detail-title"><button class="detail-back" aria-label="返回账单分析" @click="navigate('/bills')"><ChevronLeft :size="20"/></button><h2>消费明细</h2></div><label class="period-wrap"><select class="period" v-model="selectedCategory" aria-label="消费分类"><option>全部</option><option v-for="c in visibleCategories" :key="c.name">{{c.name}}</option></select><ChevronDown :size="13"/></label></div><p v-if="!categoryBills.length" class="empty-month">该月份暂无消费明细</p><div v-else class="transactions"><div v-for="b in categoryBills" :key="b.id" class="transaction"><div><strong>{{b.merchant}}</strong><p>{{b.date.slice(5)}} - {{b.category}}</p></div><b>−{{money(b.amount)}}</b></div></div></section>
 <section v-else-if="page==='/risk-assessment'" class="risk-assessment-page">
  <div class="page-title"><div class="detail-title"><button class="detail-back" aria-label="返回理财产品" @click="navigate('/wealth')"><ChevronLeft :size="20"/></button><h2>风险承受能力评估</h2></div></div>
  <div class="risk-assessment-options"><section v-for="(question,index) in riskQuestions" :key="question.title" class="risk-question"><p>{{index+1}}. {{question.title}}</p><button v-for="(option,optionIndex) in question.options" :key="option.text" type="button" :class="{selected:riskAnswers[index]===option.score}" @click="riskAnswers[index]=option.score"><strong>{{String.fromCharCode(65+optionIndex)}}.</strong><span class="risk-option-text">{{option.text}}</span><Check v-if="riskAnswers[index]===option.score" :size="15"/></button></section><p>评估结果：<strong>C{{assessmentRisk}} {{capacityLabel(assessmentRisk)}} - 可购买 R{{assessmentRisk}} 及以下</strong></p></div>
  <t-button class="primary risk-assessment-submit" theme="primary" block @click="confirmRiskAssessment">确认评估</t-button>
 </section>
 <section v-else-if="isProductDetail" class="product-detail-page">
  <div class="page-title"><div class="detail-title"><button class="detail-back" aria-label="返回理财产品" @click="navigate('/wealth')"><ChevronLeft :size="20"/></button><h2>产品详情</h2></div></div>
  <div class="product-detail-summary"><h3>{{detailProduct.name}}</h3><strong>{{detailProduct.rate.toFixed(2)}}<small>%</small></strong><p>成立以来年化</p></div>
  <div class="product-detail-grid"><div><span>风险等级</span><b>R{{detailProduct.risk}}{{riskLabel(detailProduct.risk)}}</b></div><div><span>起购金额</span><b>¥{{detailProduct.min}}</b></div><div><span>赎回方式</span><b>{{detailProduct.days?'持有 '+detailProduct.days+' 天':'每日可赎回'}}</b></div><div><span>申购状态</span><b>{{detailProduct.open?'开放中':'暂停申购'}}</b></div></div>
  <t-button class="primary" theme="primary" block @click="buy(detailProduct)">申购</t-button>
 </section>
 <section v-else-if="page==='/wealth'" class="wealth-page">
  <div class="page-title"><h2>理财产品</h2></div>
  <div class="risk-capacity-row"><span class="risk-capacity-label">风险承受能力级别：<strong>C{{currentRisk}} {{capacityLabel(currentRisk)}}</strong></span><button class="risk-reassess" type="button" @click="navigate('/risk-assessment')">重新评估 <ArrowUpRight :size="14"/></button></div>
  <template v-if="wealthMode==='products'">
   <div class="wealth-filter-row"><div class="filters"><t-button :theme="riskFilter===1?'primary':'default'" :variant="riskFilter===1?'base':'text'" size="small" @click="exactRiskFilter=null;riskFilter=1">R1 低风险</t-button><t-button :theme="riskFilter===2?'primary':'default'" :variant="riskFilter===2?'base':'text'" size="small" @click="exactRiskFilter=null;riskFilter=2">R2 中低风险</t-button><t-button :theme="riskFilter===5?'primary':'default'" :variant="riskFilter===5?'base':'text'" size="small" @click="exactRiskFilter=null;riskFilter=5">R3 中风险及以上</t-button></div><t-checkbox v-model="onlyOpen" class="filter-check" label="仅看可申购"/></div>
   <article v-for="p in filteredProducts" :key="p.id" :class="['product','product-link',{'wealth-product-cue':wealthBubbleIndex===1&&!wealthBubbleDismissed&&(wealthCueProductIds.includes(p.id))}]" tabindex="0" role="button" @click="openProduct(p)" @keydown.enter="openProduct(p)"><h3 class="product-title"><span>{{p.name}}</span><small v-if="p.advantage" class="product-advantage">{{p.advantage}}</small><small v-if="!p.open" class="product-closed">暂停申购</small></h3><div class="product-data"><div><strong>{{p.rate.toFixed(2)}}<small>%</small></strong><p>成立以来年化</p></div><div><div class="product-data-top"><b>{{p.days?p.days+' 天可赎回':'每日可赎回'}}</b></div><p>{{'R'+p.risk+riskLabel(p.risk)+' | ¥'+p.min+' 起购'}}</p></div></div></article>
  </template>
 </section>
 <section v-else-if="page==='/holdings'" class="holding-detail-page">
  <div class="page-title"><div class="detail-title"><button class="detail-back" aria-label="返回我的账户" @click="navigate('/me')"><ChevronLeft :size="20"/></button><h2>持仓详情</h2></div></div>
  <template v-if="selectedHolding">
   <div class="product-detail-summary"><h3>{{selectedHoldingProduct.name}}</h3><strong>¥{{money(selectedHolding.principal+holdingProfit(selectedHolding))}}</strong><p>持仓市值</p></div>
   <div class="product-detail-grid"><div><span>持仓本金</span><b>¥{{money(selectedHolding.principal)}}</b></div><div><span>累计收益</span><b>¥{{holdingProfit(selectedHolding).toFixed(2)}}</b></div><div><span>风险等级</span><b>R{{selectedHoldingProduct.risk}}{{riskLabel(selectedHoldingProduct.risk)}}</b></div><div><span>赎回状态</span><b>{{selectedHoldingProduct.redeemable?'可赎回':'暂不可赎回'}}</b></div></div>
   <div class="holding-detail-actions"><t-button class="redeem-button" theme="default" variant="outline" :disabled="!selectedHoldingProduct.redeemable" @click="redeemHolding(selectedHoldingProduct)">{{ selectedHoldingProduct.redeemable ? '赎回' : '暂不可赎回' }}</t-button><t-button class="primary" theme="primary" @click="buy(selectedHoldingProduct)">追加购买</t-button></div>
  </template>
  <div v-else class="empty-state">暂无理财持仓</div>
 </section>
 <TransferHub v-else-if="page.startsWith('/transfer')" :view="page" :initial-name="payeeInitialName" :request-key="payeeRequestKey" :select-after-save="Boolean(pendingPayeeAction)" @select="selectRecipient" @navigate="navigate" @notify="notify" @cancel="clearPayeeRequest" @add="startPayeeAddition" @bank-transfer="startBankTransfer" @bank-prepared="bankTransferPrepared" @bank-select-saved="bankTransferSelectSaved"/>
 <section v-else class="me-page">
  <div class="page-title"><h2>我的账户</h2><button type="button" @click="signOut">退出登录</button></div>
  <div class="account-summary"><div class="account-metric"><span class="muted">可用余额</span><div class="big-number"><small>¥</small>{{money(bank.data.balance)}}</div></div><div class="account-metric"><span class="muted">理财持仓</span><div class="big-number"><small>¥</small>{{money(bank.data.holdings.reduce((sum,h)=>sum+h.principal+holdingProfit(h),0))}}</div></div></div>
  <div class="section-heading"><h3>未来 {{bank.forecast.length-1}} 天现金流预测</h3></div>
  <div class="cashflow"><Chart :option="lineOption" :label="`未来${bank.forecast.length-1}天余额预测，点击节点可查看事件与余额`"/></div>
   <div v-if="bank.gap>0" class="warning"><h3>未来现金流可能低于预留余额</h3><p>按当前收入、日常消费和已确认固定开支估算，缺口约 ¥{{money(bank.gap)}}。</p></div>
  <div class="section-heading holdings-heading"><h3>我的持仓</h3></div>
  <article v-for="h in bank.data.holdings.filter(h=>h.principal>0)" :key="h.productId" class="holding"><div class="holding-top"><strong>{{products.find(p=>p.id===h.productId)?.name}}</strong><button class="holding-value-link" type="button" :aria-label="`查看${products.find(p=>p.id===h.productId)?.name}持仓详情`" @click="openHoldings(h.productId)">¥{{money(h.principal+holdingProfit(h))}}<ChevronRight :size="16"/></button></div><div class="holding-bottom"><p>{{products.find(p=>p.id===h.productId)?.redeemable?'可赎回':'暂不可赎回'}}</p><span>总收益 <b>¥{{holdingProfit(h).toFixed(2)}}</b></span></div></article>
  <div class="section-heading task-list-heading"><h3>定时任务</h3><button class="text-button task-add-button" @click="openTask('fixed_expense')"><span class="task-add-icon"><Plus :size="14"/></span>新增</button></div>
  <div v-if="!bank.data.tasks.length" class="empty-state">暂无定时任务</div>
  <article v-for="(t,i) in bank.data.tasks" :key="t.id" :class="['task-card',{'first-task-card':i===0}]"><div class="section-heading"><h3>{{t.name}} - {{t.type==='redemption'&&t.productId==='all'?'全部可赎回持仓 - 上限 ¥'+money(t.amount):'¥'+money(t.amount)}}</h3><span class="task-status">{{t.frequency==='weekly'&&t.status!=='已取消'?'已停用':!t.authorized&&t.status!=='已取消'?'待授权':t.type==='fixed_expense'&&!t.payeeId&&t.status!=='已取消'?'待补账户':t.status}}</span></div><p v-if="t.type==='fixed_expense'&&!t.payeeId&&t.status!=='已取消'" class="task-recipient-note">请先保存收款账户，再取消这项旧任务并重新授权。</p><div class="task-meta-row"><p>{{t.frequency==='weekly'?'旧每周任务':t.frequency==='semiannual'?'每半年':t.frequency==='quarterly'?'每季度':'每月'}}{{t.executionDay?' '+t.executionDay+' 日':''}}{{t.type==='investment'?' - 产品风险 R'+t.risk:t.type==='redemption'&&t.productId!=='all'?' - '+(products.find(p=>p.id===t.productId)?.name||'所选产品'):t.payee?' - 收款方 '+t.payee:''}}</p><div class="task-actions"><button v-if="t.status!=='已取消'" @click="cancelTask(t.id)"><X :size="13"/>取消任务</button></div></div></article>
 </section>
 </main>
 <form v-if="page==='/'" class="composer" @submit.prevent="send()"><t-input v-model="input" placeholder="请输入你的问题…" aria-label="与知财聊天" :disabled="busy" borderless/><t-button theme="primary" type="submit" :disabled="busy||!input.trim()" aria-label="发送"><ArrowUp :size="21"/></t-button></form>
 <div class="agent-dock">
 <div v-if="page==='/bills'&&!flying&&!billBubbleDismissed" ref="dockBubble" class="bill-summary-bubble" role="status"><button class="bubble-close" aria-label="关闭账单总结" @click="closeBillBubble"><X :size="16"/></button><p v-if="billBubbleIndex<billSegments.length-1">{{billSegments[billBubbleIndex].slice(0,-2)}}<span class="bubble-tail">{{billSegments[billBubbleIndex].slice(-2)}}<button class="bubble-next" aria-label="下一段" @click="nextBillBubble"><ChevronRight :size="20"/></button></span></p><p v-else>点击<button type="button" class="bubble-flash-word" aria-label="查看消费明细" @click.stop="selectedCategory='全部';navigate('/transactions')">闪烁</button>可以查看消费明细。</p></div>
 <div v-if="page==='/wealth'&&!flying&&!wealthBubbleDismissed" ref="dockBubble" class="bill-summary-bubble wealth-summary-bubble" role="status"><button class="bubble-close" aria-label="关闭理财推荐" @click="closeWealthBubble"><X :size="16"/></button><p v-if="wealthBubbleIndex<wealthRecommendationSegments.length-1">{{wealthRecommendationSegments[wealthBubbleIndex]}}<button class="bubble-next wealth-bubble-next" aria-label="下一段" @click="nextWealthBubble"><ChevronRight :size="20"/></button></p><p v-else>点击<button type="button" class="bubble-flash-word" aria-label="查看产品详情" @click.stop="recommendedProducts[0]&&openProduct(recommendedProducts[0])">闪烁</button>可以查看产品详情。</p></div>
 <div v-if="isProductDetail&&detailProduct.risk>currentRisk&&!wealthRiskBubbleDismissed" ref="dockBubble" class="bill-summary-bubble risk-reject-bubble" role="alert"><button class="bubble-close" aria-label="关闭风险提示" @click="closeDockBubble(()=>wealthRiskBubbleDismissed=true)"><X :size="16"/></button><p>{{riskRejectText}}<button class="risk-reassess-inline" type="button" @click="navigate('/risk-assessment')">重新评估 <ArrowUpRight :size="14"/></button></p></div>
  <button class="floating-agent" :class="{'dock-hidden':flying||page==='/'||modalTransitioning}" :aria-hidden="flying||page==='/'||modalTransitioning" :disabled="flying||page==='/'||modalTransitioning" @click="greetMascot();chatOpen=true" aria-label="打开知财聊天"><Mascot :key="surpriseVersion" class="floating-character" :state="dockMascotState"/></button>
 </div>
 <div v-if="flying" ref="flightElement" class="agent-flight" :data-phase="flightPhase" :data-target="flightTarget" aria-hidden="true"><Mascot state="travel"/></div>
 <t-tab-bar class="bottom-nav" :value="navPage" :fixed="false" :bordered="true" :split="false" aria-label="主导航" @change="(value:string|number)=>navigate(String(value))"><t-tab-bar-item v-for="n in nav" :key="n.path" :value="n.path" :data-page="n.path"><template #icon><component :is="n.icon" :size="21" :stroke-width="navPage===n.path?2:1.6"/></template>{{n.label}}</t-tab-bar-item></t-tab-bar>
 <div v-if="toast" class="toast" :class="[`toast--${toastTone}`,{'toast-modal':!!modal}]" :role="toastTone==='error'||toastTone==='warning'?'alert':'status'">{{toast}}</div>
 <t-popup :visible="chatOpen" placement="bottom" attach=".phone" destroy-on-close @visible-change="(visible:boolean)=>{if(!visible)chatOpen=false}"><section class="sheet chat-sheet" role="dialog" aria-modal="true" aria-label="知财聊天">
  <div class="section-heading"><button class="icon-button" @click="chatOpen=false" aria-label="关闭聊天"><X :size="19"/></button></div>
  <div class="chat-history"><div v-if="!bank.data.messages.length" class="chat-empty"><div class="chat-empty-character"><Mascot class="chat-empty-mascot"/><span class="mascot-shadow"/></div><h2>告诉知财，你想做什么？</h2></div><div v-else class="messages"><div v-for="m in bank.data.messages" :key="m.id" :data-message-id="m.id" :class="['message',m.role]"><Mascot v-if="m.role==='agent'" class="message-avatar" compact/><div class="message-content"><p v-if="messageFlashTarget(m)" class="message-flash-line">点击<button type="button" class="bubble-flash-word" @click.stop="openMessageFlashTarget(m)">闪烁</button>可以查看{{messageFlashTarget(m)==='bills'?'消费明细':'产品详情'}}。</p><p v-else>{{m.text}}</p><div v-if="m.role==='agent'&&messageActions(m).length" class="message-actions"><button v-for="action in messageActions(m)" :key="`${action.label}-${action.path}`" class="message-action" :disabled="busy||flying" @click="openMessageAction(action,m.id)">{{action.label}}<ArrowUpRight v-if="action.icon==='up-right'" :size="15"/><ChevronRight v-else :size="15"/></button></div></div></div><div v-if="thinking&&!flying" class="chat-thinking"><Mascot class="message-avatar" state="thinking"/><span class="chat-thinking-text">知财正在思考…</span></div><div class="conversation-end"/></div></div>
  <form class="composer" @submit.prevent="send(input,true)"><t-input v-model="input" placeholder="请输入你的问题…" aria-label="聊天输入" :disabled="busy" borderless/><t-button theme="primary" type="submit" :disabled="busy||!input.trim()" aria-label="发送"><ArrowUp :size="21"/></t-button></form>
 </section></t-popup>
 <t-popup :visible="!!modal" placement="bottom" attach=".phone" destroy-on-close @visible-change="(visible:boolean)=>{if(!visible)modal=''}"><section v-if="modal" class="sheet modal-sheet" :class="{'task-sheet':modal==='task'}" role="dialog" aria-modal="true" :aria-label="modal" @keydown.esc="modal=''"><div :key="modal" class="sheet-mascot-window" aria-hidden="true"><Mascot class="sheet-mascot"/></div><div class="modal-sheet-content"><button class="sheet-close icon-button" @click="modal=''" aria-label="关闭"><X :size="19"/></button>
   <template v-if="modal==='task'"><h2>授权定时任务</h2><label>用途<select v-model="taskType" @change="taskName=taskType==='fixed_expense'?'其他':taskType==='redemption'?'赎回理财':'申购理财';taskProductId=taskType==='investment'?(eligibleTaskProducts[0]?.id||''):'all';riskLevel=1;authorized=false"><option value="fixed_expense">固定开支</option><option value="investment">申购理财</option><option value="redemption">赎回理财</option></select></label><label>{{taskType==='investment'?'产品':'类型'}}<select v-if="taskType==='fixed_expense'" v-model="taskName"><option v-for="category in fixedCategories" :key="category" :value="category">{{category}}</option></select><select v-else-if="taskType==='investment'" v-model="taskProductId"><option v-if="!eligibleTaskProducts.length" value="">暂无可申购产品</option><option v-for="product in eligibleTaskProducts" :key="product.id" :value="product.id">{{product.name}} - R{{product.risk}} - 起购 ¥{{money(product.min)}}</option></select><select v-else v-model="taskProductId"><option value="all">全部</option><option v-for="h in bank.data.holdings.filter(h=>h.principal>0)" :key="h.productId" :value="h.productId">{{products.find(p=>p.id===h.productId)?.name||h.productId}}</option></select></label><label>执行周期<select v-model="taskFrequency"><option value="monthly">每月</option><option value="quarterly">每季度</option><option value="semiannual">每半年</option></select></label><label>执行日<select v-model.number="taskDay"><option v-for="date in 31" :key="date" :value="date">每期 {{date}} 日</option></select></label><label>金额（元）<t-input type="number" v-model="amount" borderless/></label><template v-if="taskType==='investment'"><t-checkbox v-model="authorized" class="check-row check-row--small" label="授权以上操作，可随时取消。"/></template><template v-else-if="taskType==='fixed_expense'"><label>收款方<button class="recipient-choice" type="button" @click="chooseRecipient"><span>{{selectedRecipient?.display_name||'选择常用收款人'}}<small v-if="selectedRecipient">{{selectedRecipient.bank_name}} · {{selectedRecipient.account_masked}}</small></span><ChevronRight :size="16"/></button></label><t-checkbox v-model="authorized" class="check-row check-row--small" label="授权以上操作，可随时取消。"/></template><t-checkbox v-else v-model="authorized" class="check-row check-row--small" label="授权以上操作，可随时取消。"/><t-button class="primary" theme="primary" block @click="execute">确认授权</t-button></template>
 <template v-else-if="modal==='transfer'">
  <h2>确认转账</h2>
  <div v-if="oneTimeRecipient" class="transfer-recipient-summary">
   <span class="recipient-bank-icon"><Landmark :size="22"/></span>
   <div><strong>收款人姓名 {{oneTimeRecipient.holder_name}}</strong><p v-if="oneTimeRecipient.bank_name">{{oneTimeRecipient.bank_name}}</p><small class="recipient-full-account">收款账号 {{oneTimeRecipient.account_number}}</small></div>
   <button type="button" class="text-button" @click="modal='';navigate('/transfer/bank')">更换</button>
  </div>
  <div v-else-if="selectedRecipient" class="transfer-recipient-summary"><span class="recipient-bank-icon"><Landmark :size="22"/></span><div><strong>{{selectedRecipient.display_name}}</strong><p>{{selectedRecipient.holder_name}} · {{selectedRecipient.bank_name}}</p><small>{{selectedRecipient.account_masked}}</small></div><button type="button" class="text-button" @click="chooseRecipient">更换</button></div>
  <button v-else class="recipient-choice" type="button" @click="chooseRecipient">选择常用收款人<ChevronRight :size="16"/></button>
  <label>金额（元）<t-input type="number" v-model="amount" borderless/></label>
  <p v-if="strangerAcknowledged" class="recipient-first-transfer" role="alert">该账号不是常用收款人，请谨慎核对信息。</p>
  <p v-if="buyBufferAcknowledged" class="cash-buffer-warning">预计余额低于警戒线，请再次确认。</p>
  <t-button theme="primary" block :disabled="operationBusy||(!selectedRecipient&&!oneTimeRecipient)||!(Number(amount)>0)" @click="execute">确认</t-button>
 </template>
 <template v-else-if="modal==='buy'"><h2>确认申购</h2><div class="confirmation-product"><h3>{{selectedProduct.name}}</h3><p>R{{selectedProduct.risk}} - {{selectedProduct.open?'开放中':'暂停申购'}} - {{selectedProduct.days?'最低持有 '+selectedProduct.days+' 天':'每日可赎回'}}</p></div><label>申购金额（元）<t-input type="number" v-model="amount" borderless/></label><p v-if="buyBufferAcknowledged" class="cash-buffer-warning" role="alert">预计最低余额将低于 ¥{{money(bank.reserve)}} 警戒线。若仍要申购，再次点击“确认申购”即可继续。</p><t-button class="primary" theme="primary" block @click="execute">确认申购</t-button></template>
 <template v-else-if="modal==='redeem'"><h2>确认赎回</h2><div class="confirmation-product"><h3>{{selectedProduct.name}}</h3><p>可赎回 ¥{{money(bank.data.holdings.find(h=>h.productId===selectedProduct.id)?.principal||0)}}</p></div><label>赎回金额（元）<t-input type="number" v-model="amount" borderless/></label><p class="terms">确认后更新持仓和余额。</p><t-button class="primary" theme="primary" block @click="execute">确认赎回</t-button></template>
 <template v-else-if="modal==='capacity'"><h2>月度投入上限怎么算？</h2><div class="calculation"><p>月平均工资 <b>¥{{money(bank.salary)}}</b></p><p>月均日常消费 <b>−¥{{money(bank.expenses)}}</b></p><p>用户确认的固定开支 <b>−¥{{money(bank.fixedMonthly)}}</b></p><p>预留余额 <b>−¥{{money(bank.reserve)}}</b></p><p class="blue">月度投入上限 <b>¥{{money(bank.cap)}}</b></p></div><p class="footnote">固定开支经用户确认后计入，不与日常消费重复扣除。单次申购还需检查当前余额与未来最低余额。</p></template>
 <template v-else-if="modal==='evidence'"><h2>{{evidenceMode==='recurring'?'疑似周期扣款':'小额高频消费'}}</h2><p class="sheet-description">{{evidenceMode==='recurring'?`${recurringEvidence.length} 笔记录${recurringDay?`，每月 ${recurringDay} 日`:''}，平均 ¥${money(recurringAmount)}`:`单笔低于 ¥${money(demoConfig.ledger.smallAmountThreshold)}，共 ${frequentEvidence.length} 笔`}}</p><div class="transaction" v-for="e in evidenceTransactions" :key="e.date"><div><strong>{{e.merchant}}</strong><p>{{e.date}}</p></div><b>−{{money(e.amount)}}</b></div></template>
 <template v-else-if="modal==='challenge'"><h2>身份验证</h2><p class="sheet-description">该操作需要再次验证身份，请输入登录密码后继续。</p><label>登录密码<t-input type="password" v-model="challengePassword" borderless/></label><t-button class="primary" theme="primary" block :disabled="operationBusy||!challengePassword" @click="submitChallenge">确认验证</t-button></template>
 </div></section></t-popup>
 </div><div class="mobile-reset"><button :disabled="busy||flying||resetting" @click="reset">重置</button></div>
</div>
</template>
































