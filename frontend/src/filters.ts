export type AreaOption={id:number,name:string,doctor_count:number}
export type DoctorOption={id:number,external_id?:string,name:string,area?:string,hq?:string,clinic_hospital?:string,existing_specialty?:string}
export type FilterOptions={hqs:string[],areas:AreaOption[],categories:string[],specialty_groups:string[],doctor_statuses:string[],qualifications:string[],genders:string[],doctors:DoctorOption[]}
export type VisitFilterOptions={purposes:string[],outcomes:string[],products:{id:number,name:string}[]}

export function queryString(values:Record<string,string|number|boolean|undefined|null>){
 const params=new URLSearchParams()
 Object.entries(values).forEach(([key,value])=>{if(value!==undefined&&value!==null&&value!=='')params.set(key,String(value))})
 return params.toString()
}
