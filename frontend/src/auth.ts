export type Identity={username:string;role:'account_user'|'admin';csrf_token:string}
let csrfToken=''
export async function currentIdentity():Promise<Identity|null>{
 const response=await fetch('/api/auth/me')
 if(response.status===401)return null
 if(!response.ok)throw new Error('无法读取登录状态')
 const identity=await response.json() as Identity
 csrfToken=identity.csrf_token
 return identity
}
export function csrfHeaders():Record<string,string>{return csrfToken?{'X-CSRF-Token':csrfToken}:{}}
export function handleAuthFailure(response:Response):void{
 if(response.status===401){csrfToken='';window.location.reload();throw new Error('登录已过期，请重新登录')}
}
export async function signOut(){
 await fetch('/api/auth/logout',{method:'POST',headers:csrfHeaders()})
 location.reload()
}
