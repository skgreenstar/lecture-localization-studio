import React,{useEffect,useRef,useState} from 'react';
import{createRoot}from'react-dom/client';
import{Clapperboard,Languages,Upload,Search,CheckCircle2,RefreshCw,Play,FolderOpen,AudioLines,Save,ChevronRight}from'lucide-react';
import './style.css';
const API='http://127.0.0.1:8000';
type Translation={id:string;text:string;version:number;approved:number};
type Segment={id:string;start:number;end:number;source:string;translation:Translation|null;audio_id:string|null};
type Project={id:string;title:string;duration:number};
async function request<T>(path:string,init?:RequestInit):Promise<T>{const r=await fetch(API+path,init);if(!r.ok)throw Error((await r.text()).slice(0,300));return r.json();}
function stamp(n:number){return [Math.floor(n/3600),Math.floor(n%3600/60),Math.floor(n%60)].map(x=>String(x).padStart(2,'0')).join(':')}
function App(){
 const[projects,setProjects]=useState<Project[]>([]),[project,setProject]=useState<Project|null>(null);
 const[segments,setSegments]=useState<Segment[]>([]),[active,setActive]=useState<string|null>(null);
 const[lang,setLang]=useState('en'),[search,setSearch]=useState(''),[draft,setDraft]=useState(''),[message,setMessage]=useState('');
 const[busy,setBusy]=useState(false),[job,setJob]=useState(''),[now,setNow]=useState(0);
 const video=useRef<HTMLVideoElement>(null),upload=useRef<HTMLInputElement>(null);
 const current=segments.find(s=>s.id===active)||null;
 const approved=segments.filter(s=>s.translation?.approved).length;
 async function refreshProjects(){setProjects(await request<Project[]>('/api/projects'))}
 async function refreshSegments(p=project,l=lang){if(!p)return;const list=await request<Segment[]>(`/api/projects/${p.id}/segments?language=${l}`);setSegments(list);setActive(old=>list.some(s=>s.id===old)?old:list[0]?.id||null)}
 useEffect(()=>{refreshProjects().catch(e=>setMessage(e.message))},[]);
 useEffect(()=>{refreshSegments().catch(e=>setMessage(e.message))},[project?.id,lang]);
 useEffect(()=>{setDraft(current?.translation?.text||'')},[active,current?.translation?.id]);
 async function run(task:()=>Promise<void>){setBusy(true);setMessage('');try{await task()}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}
 async function onUpload(file:File){await run(async()=>{const fd=new FormData();fd.append('file',file);const p=await request<Project>('/api/projects',{method:'POST',body:fd});await refreshProjects();setProject(p);setMessage('영상이 등록되었습니다. 전사문을 추가하거나 데모 데이터를 불러오세요.')})}
 async function save(){if(!current||!draft.trim())return;await run(async()=>{await request(`/api/segments/${current.id}/translations/${lang}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:draft,expected_version:current.translation?.version||0})});await refreshSegments();setMessage('새 번역 버전이 저장되었습니다. 승인 후 TTS를 요청하세요.')})}
 async function approve(){if(!current?.translation)return;await run(async()=>{await request(`/api/translations/${current.translation!.id}/approve`,{method:'POST'});await refreshSegments();setMessage('번역을 승인했습니다.')})}
 async function tts(){if(!current?.translation)return;await run(async()=>{const r=await request<{job_id:string}>(`/api/translations/${current.translation!.id}/tts`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice:'Ryan'})});setJob(r.job_id);setMessage('TTS 작업이 등록되었습니다. 현재 MVP에서는 실제 MLX 워커 연결이 필요합니다.')})}
 async function demo(){if(!project)return;await run(async()=>{await request(`/api/projects/${project.id}/demo`,{method:'POST'});await refreshSegments();setMessage('예제 문장 3개가 추가되었습니다.')})}
 function choose(s:Segment){setActive(s.id);if(video.current)video.current.currentTime=s.start}
 const filtered=segments.filter(s=>(s.source+' '+(s.translation?.text||'')).toLowerCase().includes(search.toLowerCase()));
 return <div className="shell">
  <aside className="sidebar"><div className="brand"><div className="brandIcon"><Languages size={23}/></div><div><strong>Lingua Studio</strong><small>Lecture Localization</small></div></div>
   <div className="sideLabel">WORKSPACE</div><div className="sideLink selected"><Clapperboard size={18}/> Studio</div><div className="sideLink"><FolderOpen size={18}/> Projects</div>
   <div className="sideLabel space">YOUR PROJECTS</div>{projects.map(p=><button key={p.id} className={'projectLink '+(p.id===project?.id?'on':'')} onClick={()=>setProject(p)}><span className="dot"/><span>{p.title}</span></button>)}
   <button className="newProject" onClick={()=>upload.current?.click()}><Upload size={16}/> Upload video</button><input ref={upload} type="file" accept="video/*" hidden onChange={e=>{const f=e.target.files?.[0];if(f)onUpload(f)}}/>
   <div className="sideFooter">LOCAL FIRST <span>●</span><small>Apple Silicon ready</small></div>
  </aside>
  <main className="main"><header className="top"><div><div className="eyebrow">WORKSPACE / STUDIO</div><h1>Translation Studio</h1><p>Refine every word. Deliver every meaning.</p></div><div className="headerRight"><span className="localTag">● Local workspace</span><button className="primary" onClick={()=>upload.current?.click()}><Upload size={16}/> New video</button></div></header>
   {message&&<div className="notice" role="status">{message}<button onClick={()=>setMessage('')}>×</button></div>}
   {!project?<div className="empty"><div className="emptyIcon"><Clapperboard size={38}/></div><h2>Your localization workspace</h2><p>Upload a lecture video to review, edit and approve translations sentence by sentence.</p><button className="primary" onClick={()=>upload.current?.click()}><Upload size={16}/> Upload your first video</button>{projects.length>0&&<p>Or select an existing project from the sidebar.</p>}</div>:
   <><section className="overview"><div><div className="eyebrow">CURRENT PROJECT</div><h2>{project.title}</h2><p>Source: Korean <ChevronRight size={14}/> Target: {lang==='en'?'English':lang==='ja'?'Japanese':'Chinese'}</p></div><div className="metrics"><div><small>Duration</small><strong>{stamp(project.duration)}</strong></div><div><small>Segments</small><strong>{segments.length}</strong></div><div><small>Approved</small><strong>{approved}<span> / {segments.length}</span></strong></div></div></section>
   <section className="workspace"><div className="leftPane"><div className="panel videoPanel"><div className="panelTitle"><span><Play size={16}/> Original video</span><span className="muted">SOURCE · KO</span></div><video ref={video} controls preload="metadata" src={API+`/api/projects/${project.id}/video`} onTimeUpdate={e=>setNow(e.currentTarget.currentTime)}/><div className="videoBottom"><span>▶ {stamp(now)}</span><span>Original footage</span></div></div>
   <div className="panel segmentsPanel"><div className="panelTitle"><span>Transcript & translations</span><span className="muted">{segments.length} segments</span></div><div className="search"><Search size={16}/><input placeholder="Search source or translation..." value={search} onChange={e=>setSearch(e.target.value)}/></div>
    <div className="segments">{filtered.length===0?<div className="noSegments">No segments yet. Import your transcript or load demo segments.</div>:filtered.map(s=><button key={s.id} className={'segment '+(active===s.id?'active':'')} onClick={()=>choose(s)}><div className="segmentTop"><span>{stamp(s.start)} – {stamp(s.end)}</span>{s.translation?.approved?<span className="approved"><CheckCircle2 size={13}/> Approved</span>:<span className="review">Needs review</span>}</div><strong>{s.source}</strong><p>{s.translation?.text||'Translation not added yet'}</p></button>)}</div>
    <div className="panelFoot"><button className="subtle" onClick={demo} disabled={busy}>+ Add demo transcript segments</button></div></div></div>
   <div className="rightPane"><div className="panel editor"><div className="panelTitle"><span><Languages size={16}/> Translation editor</span><select value={lang} onChange={e=>setLang(e.target.value)}><option value="en">English</option><option value="ja">Japanese</option><option value="zh">Chinese</option></select></div>
    {current?<div className="editorBody"><div className="segmentMeta">SEGMENT {segments.indexOf(current)+1} OF {segments.length} <span>{stamp(current.start)} – {stamp(current.end)}</span></div><label>KOREAN SOURCE</label><div className="sourceText">{current.source}</div><label>TRANSLATION · EDITABLE</label><textarea value={draft} onChange={e=>setDraft(e.target.value)} rows={6} placeholder="Enter your translation..."/><div className="version">Version {current.translation?.version||0} · {current.translation?.approved?'Approved':'Draft / review needed'}</div>
    <button className="primary wide" disabled={busy||!draft.trim()||draft===current.translation?.text} onClick={save}><Save size={16}/> Save translation</button><button className="outline wide" disabled={busy||!current.translation||!!current.translation.approved||draft!==current.translation.text} onClick={approve}><CheckCircle2 size={16}/> Approve translation</button>
    <div className="divider"/><div className="ttsTitle"><AudioLines size={18}/> Voice generation</div><p className="hint">Regenerate only the selected, approved segment. Existing translations remain unchanged.</p><button className="ttsButton wide" disabled={busy||!current.translation?.approved} onClick={tts}><RefreshCw size={16}/> Regenerate selected TTS</button>{current.audio_id&&<audio controls src={API+`/api/audio/${current.audio_id}`}/>}
    {job&&<div className="job">TTS job: {job.slice(0,12)}… · Worker integration pending</div>}</div>:<div className="noSegments">Select a segment to start reviewing.</div>}</div>
    <div className="tip"><strong>Accuracy first</strong><p>Review the Korean transcript before approving translations. Keep technical terms consistent across your lecture.</p></div></div></section></>}
   <footer>Lecture Localization Studio · Local-first MVP · No cloud inference required for editing</footer>
  </main>
 </div>
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
