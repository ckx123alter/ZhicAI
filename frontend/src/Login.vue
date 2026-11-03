<script setup lang="ts">
import {ref} from 'vue'
// 演示账户预填，方便评审/访客直接登录查看演示
const username=ref('user'),password=ref('user12345'),busy=ref(false),error=ref('')
async function login(){
 if(busy.value)return
 busy.value=true;error.value=''
 try{
  const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({username:username.value,password:password.value})})
  if(!response.ok){
   const result=await response.json().catch(()=>({detail:'登录失败'}))
   throw new Error(result.detail||'登录失败')
  }
  location.reload()
 }catch(e){error.value=e instanceof Error?e.message:'登录失败';busy.value=false}
}
</script>
<template>
 <main class="auth-page">
  <form class="auth-card" @submit.prevent="login">
   <div class="auth-intro">
    <div class="wordmark"><span class="brand-symbol">知</span><span class="brand-word">财</span></div>
    <h1>登录</h1><p>使用测试账户登录</p>
   </div>
   <label>账号<input v-model.trim="username" autocomplete="username" required/></label>
   <label>密码<input v-model="password" type="password" autocomplete="current-password" required/></label>
   <p v-if="error" class="auth-error" role="alert">{{error}}</p>
   <button type="submit" :disabled="busy">{{busy?'正在登录…':'登录'}}</button>
  </form>
 </main>
</template>
<style scoped>
.auth-page{min-height:100vh;display:grid;place-items:center;background:#edf2f7;padding:20px;color:#173953;font-family:Arial,"Microsoft YaHei",sans-serif}
.auth-card{background:#fff;width:min(100%,380px);padding:32px;border-radius:18px;box-shadow:0 14px 42px #17395318}
.auth-intro{text-align:center}
.auth-card .wordmark{justify-content:center;font-family:"Noto Sans SC","Microsoft YaHei",sans-serif;color:#172744}
h1{font-size:23px;margin:20px 0 7px}p{font-size:13px;color:#66788c;margin:0 0 24px}
label{display:block;margin:17px 0;font-size:13px}input{display:block;width:100%;margin-top:8px;padding:12px;border:1px solid #cad5df;border-radius:8px;font:inherit}
button{width:100%;padding:13px;border:0;border-radius:9px;background:#173953;color:#fff;font-size:14px;cursor:pointer}
button:disabled{opacity:.65}.auth-error{color:#b34b36;margin:10px 0}
</style>
