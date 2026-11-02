export type Product = { id:string; name:string; issuer:string; risk:number; rate:number; days:number; min:number; open:boolean; redeemable:boolean; advantage:string }
export type Bill = { id:string; category:string; merchant:string; date:string; amount:number }
export type Category = { name:string; amount:number; color:string; count:number }
export type ConversationMessage = { id:string|number; role:'user'|'agent'; text:string; action?:Record<string,unknown>; actions?:Record<string,unknown>[] }
export type DemoData = {
  products:Product[]
  bills:Bill[]
  categories:Category[]
  evidence:{recurring:{date:string;merchant:string;amount:number}[]}
  messages:ConversationMessage[]
}

export const products:Product[]=[]
export const bills:Bill[]=[]
export const categories:Category[]=[]
export const evidence:DemoData['evidence']={recurring:[]}
export const conversationMessages:ConversationMessage[]=[]

export function loadDemoData(data:DemoData){
  products.splice(0,products.length,...data.products)
  bills.splice(0,bills.length,...data.bills)
  categories.splice(0,categories.length,...data.categories)
  evidence.recurring.splice(0,evidence.recurring.length,...data.evidence.recurring)
  conversationMessages.splice(0,conversationMessages.length,...(data.messages||[]))
}

export const suggestions=['帮我分析上个月的账单','给妈妈转500元','给高程0000000000001234转2000元','每月5号给房东转3000元房租','我想买R2理财，最多能投多少？','我要购买R3理财','比较我持仓的理财实际年化收益']
export const steps=['看懂账单每一笔消费','智能转账','警示转账给陌生人','授权定时任务','先算现金流，再选理财','拒绝申购超出风险产品','比较理财产品真实收益']

