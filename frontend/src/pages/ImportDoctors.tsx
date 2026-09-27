import {useState} from 'react'
import {api,json} from '../api'

type Row={row:number,status:string,values:Record<string,string>,errors:string[],matches:{id:number,name:string}[]}
type Preview={id:string,filename:string,mapping:Record<string,string>,summary:Record<string,number|string[]>,rows:Row[]}
const fields=['external_id','name','existing_specialty','area','hq','category','mobile','active','specialty_group','doctor_status','qualification','gender','clinic_hospital']

export default function ImportDoctors(){
 const [file,setFile]=useState<File>()
 const [preview,setPreview]=useState<Preview>()
 const [busy,setBusy]=useState(false)
 const [result,setResult]=useState<Record<string,number>>()
 const [error,setError]=useState('')
 const [resolutions,setResolutions]=useState<Record<string,string>>({})
 async function inspect(){
  if(!file)return
  setBusy(true);setError('')
  const form=new FormData();form.append('file',file)
  try{setPreview(await api('/imports/preview',{method:'POST',body:form}))}catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }
 async function confirm(){
  if(!preview)return
  setBusy(true)
  try{setResult(await api(`/imports/${preview.id}/confirm`,json('POST',{mapping:preview.mapping,resolutions})))}catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }
 function map(header:string,field:string){if(preview)setPreview({...preview,mapping:{...preview.mapping,[header]:field}})}
 const headers=preview?[...Object.keys(preview.mapping),...((preview.summary.unmapped_columns as string[])||[])]:[]
 const unresolved=preview?.rows.some(row=>row.status==='ambiguous'&&!resolutions[String(row.row)])
 return <>
  <div className="page-title"><div><h1>Import doctor master</h1><p>Preview, validate, and safely synchronize an Excel workbook.</p></div></div>
  {error&&<div className="error">{error}</div>}
  {!preview&&!result&&<div className="drop"><div className="upload-icon">↑</div><h2>Select an Excel workbook</h2><p>.xlsx and .xlsm files are supported. No visits will be created.</p><input type="file" accept=".xlsx,.xlsm" onChange={event=>setFile(event.target.files?.[0])}/><button className="button primary" disabled={!file||busy} onClick={inspect}>{busy?'Reading…':'Preview import'}</button></div>}
  {preview&&!result&&<>
   <div className="metrics import-metrics">{['total','new','matched','ambiguous','skipped','errors'].map(key=><div className="metric" key={key}><span>{key}</span><strong>{String(preview.summary[key]??0)}</strong></div>)}</div>
   <div className="panel"><h2>Column mapping</h2><div className="mapping-editor">{headers.map(header=><label key={header}><span>{header}</span><select value={preview.mapping[header]||''} onChange={event=>map(header,event.target.value)}><option value="">Preserve as additional data</option>{fields.map(field=><option key={field} value={field}>{field.replaceAll('_',' ')}</option>)}</select></label>)}</div><p className="muted">Unmapped columns are retained on the doctor record. ID-only template rows are shown as skipped.</p></div>
   <div className="table-wrap"><table><thead><tr><th>Row</th><th>Doctor</th><th>Area</th><th>Result</th><th>Details / resolution</th></tr></thead><tbody>{preview.rows.slice(0,250).map(row=><tr key={row.row}><td>{row.row}</td><td>{row.values.name||'—'}</td><td>{row.values.area||'—'}</td><td><span className={`pill ${row.status}`}>{row.status}</span></td><td>{row.status==='ambiguous'?<select value={resolutions[String(row.row)]||''} onChange={event=>setResolutions({...resolutions,[String(row.row)]:event.target.value})}><option value="">Choose action…</option><option value="create">Create new doctor</option>{row.matches.map(match=><option key={match.id} value={match.id}>Update {match.name}</option>)}</select>:row.status==='skipped'?'ID-only template row':row.errors.join(', ')||row.matches.map(match=>match.name).join(', ')||'Ready'}</td></tr>)}</tbody></table></div>
   <div className="actions"><button className="button" onClick={()=>setPreview(undefined)}>Cancel</button><button className="button primary" disabled={busy||unresolved||!Object.values(preview.mapping).includes('name')} onClick={confirm}>{busy?'Importing…':'Confirm import'}</button></div>
  </>}
  {result&&<div className="success"><h2>Import complete</h2><p>{result.new} doctors created and {result.updated} updated. {result.skipped} template rows skipped, {result.ambiguous} remain ambiguous, and {result.errors} had errors.</p></div>}
 </>
}
