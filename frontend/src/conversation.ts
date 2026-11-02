// This identifies a conversation in the local demo; account authorization stays server-side.
const key='zhicai-conversation-id'
import {csrfHeaders} from './auth'
let current=''
export function conversationHeaders():Record<string,string>{return current?{'X-Conversation-Id':current}:{}}
export async function initializeConversation(){
 try{current=localStorage.getItem(key)||''}catch{}
 if(current){
  const existing=await fetch('/api/conversation/context',{headers:conversationHeaders()})
  if(existing.ok)return
  if(existing.status!==404)throw new Error('无法读取会话')
 }
 const response=await fetch('/api/conversations',{method:'POST',headers:{'Content-Type':'application/json',...csrfHeaders()},body:JSON.stringify({adopt_legacy:true})})
 if(!response.ok)throw new Error('无法建立会话')
 current=(await response.json()).conversation_id
 try{localStorage.setItem(key,current)}catch{}
}
