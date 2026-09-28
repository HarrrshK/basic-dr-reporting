import {Activity, CalendarDays, ChartNoAxesCombined, Database, FileBarChart, LayoutDashboard, Map, Package, Stethoscope, Upload, UserRoundPlus, UsersRound} from 'lucide-react'
import {lazy,Suspense} from 'react'
import {NavLink, Route, Routes} from 'react-router-dom'
import SyncIndicator from './components/SyncIndicator'

const Dashboard=lazy(()=>import('./pages/Dashboard')),Doctors=lazy(()=>import('./pages/Doctors')),
 ImportDoctors=lazy(()=>import('./pages/ImportDoctors')),Visits=lazy(()=>import('./pages/Visits')),
 AddVisit=lazy(()=>import('./pages/AddVisit')),Areas=lazy(()=>import('./pages/Areas')),
 Products=lazy(()=>import('./pages/Products')),FollowUps=lazy(()=>import('./pages/FollowUps')),
 Reports=lazy(()=>import('./pages/Reports')),DoctorProfile=lazy(()=>import('./pages/DoctorProfile')),
 Calendar=lazy(()=>import('./pages/Calendar')),BulkVisit=lazy(()=>import('./pages/BulkVisit')),
 Insights=lazy(()=>import('./pages/Insights')),DataManagement=lazy(()=>import('./pages/DataManagement'))

const nav = [
  ['/', 'Dashboard', LayoutDashboard], ['/doctors', 'Doctors', Stethoscope], ['/visits/new', 'Add visit', UserRoundPlus],
  ['/visits/bulk', 'Multiple visits', UsersRound], ['/visits', 'Visit history', CalendarDays], ['/calendar', 'Calendar', CalendarDays], ['/areas', 'Areas', Map], ['/products', 'Products', Package],
  ['/follow-ups', 'Follow-ups', Activity], ['/insights', 'Insights', ChartNoAxesCombined], ['/reports', 'Reports', FileBarChart], ['/import', 'Import doctors', Upload], ['/data', 'Data management', Database],
] as const

export default function App() {
  return <div className="shell"><aside><div className="brand"><span>FR</span><div>Field Reports<small>Personal workspace</small></div></div><nav>{nav.map(([to,label,Icon]) => <NavLink key={to} to={to} end={to === '/'}><Icon size={19}/>{label}</NavLink>)}</nav></aside>
    <main><header><div><b>Field visit reporting</b><small>Doctor master & activity analytics</small></div><div className="header-actions"><SyncIndicator/><NavLink className="button primary" to="/visits/new">+ Add visit</NavLink></div></header><section className="content"><Suspense fallback={<div className="loading">Loading workspace…</div>}><Routes>
      <Route path="/" element={<Dashboard/>}/><Route path="/doctors" element={<Doctors/>}/><Route path="/doctors/:id" element={<DoctorProfile/>}/><Route path="/import" element={<ImportDoctors/>}/>
      <Route path="/visits" element={<Visits/>}/><Route path="/visits/new" element={<AddVisit/>}/><Route path="/visits/bulk" element={<BulkVisit/>}/><Route path="/calendar" element={<Calendar/>}/><Route path="/areas" element={<Areas/>}/>
      <Route path="/products" element={<Products/>}/><Route path="/follow-ups" element={<FollowUps/>}/><Route path="/insights" element={<Insights/>}/><Route path="/reports" element={<Reports/>}/><Route path="/data" element={<DataManagement/>}/>
    </Routes></Suspense></section></main></div>
}
