import {useEffect,useState} from 'react'
import {api,setApiToken} from '../api'
type Status={laptop_connected:boolean,last_successful_backup?:string|null,last_external_backup_at?:string|null,pending_changes:number,last_error?:string|null,backup_requested:boolean,external_backup_configured:boolean,backend_unavailable?:boolean,auth_required?:boolean}
export default function SyncIndicator(){
 const [status,setStatus]=useState<Status>()
 function refresh(){api<Status>('/backup/status').then(setStatus).catch((error:Error)=>setStatus({laptop_connected:false,pending_changes:0,backup_requested:false,external_backup_configured:false,backend_unavailable:true,auth_required:error.message.includes('key required')||error.message.includes('API_ACCESS_TOKEN')}))}
 useEffect(()=>{refresh();const timer=window.setInterval(refresh,5000);return()=>window.clearInterval(timer)},[])
 async function trigger(){
  if(!status)return
  if(status.auth_required){
   const value=window.prompt('Enter the API access key configured on Render. It is stored only in this browser.');
   if(value===null)return
   setApiToken(value);refresh();return
  }
  if(status.backend_unavailable){refresh();return}
  await api('/backup/request',{method:'POST'}).catch(()=>null);refresh()
 }
 if(!status)return <div className="sync-indicator checking"><i/> Checking backup…</div>
 const tone=status.backend_unavailable||!status.laptop_connected?'offline':status.backup_requested||status.pending_changes?'pending':'connected'
 const last=status.last_successful_backup?`Last successful backup: ${new Date(status.last_successful_backup).toLocaleString()}`:'No laptop backup completed yet.'
 const external=status.external_backup_configured?(status.last_external_backup_at?`S3 upload: ${new Date(status.last_external_backup_at).toLocaleString()}`:'S3 configured · no upload confirmed yet'):'External backup not configured'
 const label=status.auth_required?'Connect backup':status.backend_unavailable?'Production API unreachable':!status.laptop_connected?`Laptop offline${status.pending_changes?` · ${status.pending_changes} pending`:''}`:status.backup_requested?'Backup requested':status.pending_changes?`${status.pending_changes} changes to back up`:'Laptop backup connected'
 const title=[last,external,status.last_error||''].filter(Boolean).join(' ')
 return <button className={`sync-indicator ${tone}`} onClick={trigger} title={title}><i/><span className="sync-copy"><b>{label}</b><small>{last} · {external}</small></span><span className="sync-action">Backup to Laptop</span></button>
}
