import {useEffect,useState} from 'react'
import {api,setApiToken} from '../api'
type Status={pending_records:number,syncing:boolean,dirty:boolean,laptop_agent_online:boolean,bootstrap_complete:boolean,bootstrap_blocked?:boolean,last_successful_sync?:string,backend_unavailable?:boolean,auth_required?:boolean}
export default function SyncIndicator(){
 const [status,setStatus]=useState<Status>()
 function refresh(){api<Status>('/sync/status').then(setStatus).catch((error:Error)=>setStatus({pending_records:0,syncing:false,dirty:false,laptop_agent_online:false,bootstrap_complete:false,backend_unavailable:true,auth_required:error.message.includes('key required')||error.message.includes('API_ACCESS_TOKEN')}))}
 useEffect(()=>{refresh();const timer=window.setInterval(refresh,5000);return()=>window.clearInterval(timer)},[])
 async function trigger(){
  if(!status)return
  if(status.auth_required){
   const value=window.prompt('Enter the API access key configured on your laptop backend. It is stored only in this browser.');
   if(value===null)return
   setApiToken(value);refresh();return
  }
  if(status.backend_unavailable){refresh();return}
  if(!status.bootstrap_complete){refresh();return}
  setStatus(current=>current?{...current,syncing:true}:current);await api('/sync/flush',{method:'POST'}).catch(()=>null);refresh()
 }
 if(!status)return <div className="sync-indicator checking"><i/> Checking database…</div>
 const tone=status.backend_unavailable?'offline':!status.bootstrap_complete?'setup':!status.laptop_agent_online?'offline':status.syncing?'syncing':status.dirty||status.pending_records?'pending':'connected'
 const label=status.auth_required?'Connect sync service':status.backend_unavailable?'Render API unreachable':!status.bootstrap_complete?status.bootstrap_blocked?'Laptop setup needs review':'Start laptop agent first':status.syncing?'Sending snapshot to laptop…':status.pending_records?status.laptop_agent_online?`${status.pending_records} flush queued`:`Laptop offline · ${status.pending_records} queued`:status.dirty?'Flush to laptop':status.laptop_agent_online?'Laptop connected · synced':'Laptop connector offline'
 return <button className={`sync-indicator ${tone}`} onClick={trigger} title={status.auth_required?'Enter the Render API access key':status.backend_unavailable?'Check the Netlify API URL and Render service':!status.bootstrap_complete?'Start the laptop connector before adding site data':status.dirty?'Send current app data to laptop PostgreSQL':status.last_successful_sync?`Last laptop commit: ${new Date(status.last_successful_sync).toLocaleString()}`:'Click to queue a snapshot for laptop PostgreSQL'}><i/>{label}</button>
}
