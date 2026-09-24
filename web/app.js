'use strict';
const $ = s => document.querySelector(s);
const node = (tag, cls, text) => { const e = document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e; };
const button = (text, fn, cls='text') => { const b=node('button',cls,text);b.type='button';b.onclick=fn;return b; };
const state = {view:'today', records:[], overview:null, generation:0, navigation:0, brief:null, editing:null, defer:null};
const TK='brain_token';
let accessToken='';
try{accessToken=normalizeToken(sessionStorage.getItem(TK)||'');}catch{/* Login also works when browser storage is blocked. */}
let authGeneration=0, connecting=false;
class AuthenticationError extends Error {}
class SupersededRequest extends Error {}
function showConnection(){const dialog=$('#connection');if(!dialog.open)dialog.showModal();}
function normalizeToken(value){return value.trim().replace(/^Authorization:\s*/i,'').replace(/^Bearer\s+/i,'').trim();}
let noticeTimer;
function notice(text,error=false){clearTimeout(noticeTimer);$('#notice').textContent=text;$('#notice').className=error?'error':'';if(!error)noticeTimer=setTimeout(()=>$('#notice').textContent='',7000);}
async function api(path,body,options={}){
  const token=options.token??accessToken,generation=authGeneration;
  if(!token){showConnection();throw new AuthenticationError('Connect with your access token to load the workspace.');}
  const headers=token?{Authorization:'Bearer '+token}:{};
  if(body!==undefined)headers['Content-Type']='application/json';
  let r;
  try{r=await fetch(path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});}
  catch{if(generation!==authGeneration)throw new SupersededRequest('A newer connection replaced this request.');throw new Error('Could not reach the brain service. Your entry has been kept; try connecting again.');}
  const data=await r.json().catch(()=>({}));
  if(generation!==authGeneration)throw new SupersededRequest('A newer connection replaced this request.');
  if(r.status===401){
    if(!options.connecting){accessToken='';authGeneration++;try{sessionStorage.removeItem(TK);}catch{}showConnection();}
    throw new AuthenticationError('Access token rejected. Use the token from your Obsidian Brain plugin settings.');
  }
  if(!r.ok)throw new Error(data.detail||data.error||'The service could not complete this request.');
  return data;
}
const fmtDate = value => value?new Date(value.slice(0,10)+'T12:00:00').toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}):'Unconfirmed';
const projectName = id => state.records.find(r=>r.id===id)?.title;
function badge(text,kind=''){return node('span','badge '+kind,text);}
function empty(title,detail,action){const box=$('#empty').content.firstElementChild.cloneNode(true);box.querySelector('h2').textContent=title;box.querySelector('p').textContent=detail;if(action)box.appendChild(action);return box;}
function heading(title,detail){const e=node('div','page-heading'),left=node('div');left.append(node('h1','',title),node('p','',detail));e.append(left);return e;}
function section(title,count){const e=node('div','section-heading');e.append(node('h2','',title));if(count!==undefined)e.append(node('span','',String(count)));return e;}
function humanKind(r){return ({commitment:'Commitment',decision:'Decision',project:'Project',client:'Client',person:'Person',issue:'Issue'})[r.kind]||r.kind;}
function evidenceBlock(r){const box=node('div');const e=r.evidence?.[0];if(e){box.append(node('blockquote','evidence',e.quote));box.append(button(e.note_path+' · '+fmtDate(e.event_date),()=>openSource({record:r.id})));}else box.append(node('p','muted','Recorded directly in this workspace.'));return box;}
function footer(r,review=false){
  const row=node('div','card-footer'),left=node('div'),right=node('div');left.append(button('View evidence',()=>openSource({record:r.id})));
  if(review){if(r.review==='pending')right.append(button('Dismiss',()=>mutate(r,'reject')));right.append(button(r.source_changed?'Review changes':'Review & accept',()=>editRecord(r),'primary'));}
  else{right.append(button('Edit',()=>editRecord(r)));if(!['done','cancelled','archived'].includes(r.status)){right.append(button('Acknowledge',()=>mutate(r,'acknowledge')),button('Defer',()=>defer(r)));if(['commitment','decision','issue'].includes(r.kind))right.append(button('Resolve',()=>editRecord(r,'resolve')));}}
  row.append(left,right);return row;
}
function recordCard(r,attention=false){
  const card=node('article',attention?'attention-card'+(r.priority>=90?' urgent':''):'record-card');const body=node('div','card-body');
  const meta=node('div','card-meta');meta.append(badge(humanKind(r)),badge(r.review,r.review));if(r.source_changed)meta.append(badge('Evidence changed','danger'));
  const p=projectName(r.project);if(p)meta.append(button(p,()=>showBrief(r.project)));body.append(meta,node('h3','',r.title));
  if(r.reasons){const list=node('ul','reasons');r.reasons.forEach(x=>list.append(node('li','',x)));body.append(list);}
  const facts=node('div','fact-row');facts.append(node('span','',r.owner||'Owner unconfirmed'),node('span','',r.due?'Due '+fmtDate(r.due):'No accepted due date'),node('span','',r.status));body.append(facts);
  if(r.note)body.append(node('p','reasons',r.note));
  card.append(body,footer(r,r.review==='pending'));return card;
}
async function refresh(){
  const generation=++state.generation;
  try{const [overview,records]=await Promise.all([api('/ui/api/workspace'),api('/ui/api/records')]);if(generation!==state.generation)return;state.overview=overview;state.records=records.records;$('#attention-count').textContent=overview.counts.attention||'';$('#review-count').textContent=overview.counts.pending||'';render();}
  catch(error){if(generation!==state.generation||error instanceof SupersededRequest)return;const login=error instanceof AuthenticationError;$('#content').replaceChildren(empty(login?'Connect to your workspace':'Workspace not available',error.message,button(login?'Connect':'Retry',login?showConnection:refresh,'secondary')));$('#content').setAttribute('aria-busy','false');}
}
function go(view){state.navigation++;state.brief=null;state.view=view;location.hash=view;document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===view);b.setAttribute('aria-current',b.dataset.view===view?'page':'false');});render();}
function render(){
  if(!state.overview)return;
  const content=$('#content');content.replaceChildren();content.setAttribute('aria-busy','false');
  if(state.brief){showBrief(state.brief);return;}
  if(state.view==='today')renderToday(content);
  else if(state.view==='work')renderWork(content);
  else if(['projects','clients','people'].includes(state.view))renderEntities(content);
  else if(state.view==='review')renderReview(content);
  else if(state.view==='search')renderSearch(content);
  else if(state.view==='decisions')renderDecisions(content);
  else go('today');
}
function renderToday(content){
  const d=state.overview,c=d.coverage;content.append(heading('Your attention, today','Accepted commitments, open decisions, and the next step.'));
  const stats=node('div','stats');[['Needs attention',d.counts.attention],['Active commitments',d.counts.commitments],['Tracked projects',d.counts.projects],['Awaiting review',d.counts.pending]].forEach(([label,value])=>{const e=node('div','stat');e.append(node('strong','',String(value)),node('span','',label));stats.append(e);});content.append(stats);
  if(c.state==='not_scanned'){content.append(empty('Start with the information you already have','Scan your notes to build a review list. Original notes stay intact. Confirm the projects and commitments you want to track.',button('Scan sources',syncSources,'primary')));}
  const last=c.latest_source_date;const age=last?(Date.now()-Date.parse(last+'T12:00:00'))/86400000:null;
  if(c.state==='partial'||age>14){content.append(node('div','coverage-banner',c.state==='partial'?`${c.skipped.length} sources could not be read. This review has incomplete coverage.`:`Latest declared source date: ${fmtDate(last)}. Current project status may need confirmation.`));}
  const cols=node('div','columns'),main=node('div'),side=node('aside','side-column');main.append(section('Needs your attention',d.items.length));
  if(!d.items.length)main.append(empty('No accepted items need attention',d.counts.pending?'There are unreviewed observations. Review them before treating this as a complete picture.':'Add a commitment or confirm your project status as work changes.',button(d.counts.pending?'Open review':'Add a commitment',()=>d.counts.pending?go('review'):editRecord(null),'secondary')));
  else d.items.forEach(r=>main.append(recordCard(r,true)));
  const review=node('div','aside-card');review.append(node('p','eyebrow','REVIEW INBOX'),node('h3','',`${d.counts.pending} observations to review`),node('p','', 'Confirm owners, dates, and what is still current. Historical notes never become accepted work automatically.'),button('Review observations →',()=>go('review')));side.append(review);
  const coverage=node('div','aside-card');coverage.append(node('p','eyebrow','INFORMATION COVERAGE'),node('h3','', 'Know what was checked'));const dl=node('dl');[['Sources scanned',c.sources],['Latest source date',fmtDate(c.latest_source_date)],['Undated sources',c.undated_sources||0],['Unreadable sources',c.skipped?.length||0],['Last scan',c.scanned_at?fmtDate(c.scanned_at):'Not yet']].forEach(([k,v])=>dl.append(node('dt','',k),node('dd','',String(v))));coverage.append(dl);if(c.processing&&Object.keys(c.processing).length)coverage.append(node('p','small',`Meeting sections: ${c.processing.succeeded||0} processed, ${(c.processing.failed||0)+(c.processing.pending||0)} need processing.`));side.append(coverage);
  cols.append(main,side);content.append(cols);
}
function renderEntities(content){
  const kind={projects:'project',clients:'client',people:'person'}[state.view],title={project:'Projects',client:'Clients',person:'People'}[kind];
  content.append(heading(title,kind==='project'?'Delivery, decisions, and follow-through in one place.':kind==='client'?'Prepare for the next conversation.':'Responsibilities, commitments, and support needed.'));
  const records=state.records.filter(r=>r.kind===kind&&r.review==='accepted');const grid=node('div','entity-grid');
  if(!records.length){content.append(empty('No '+title.toLowerCase()+' confirmed yet','Create a record or confirm an imported observation in Review.',button('Add '+kind,()=>editRecord(null,'accept',kind),'primary')));return;}
  records.forEach(r=>{const card=node('article','record-card'),body=node('div','card-body');const count=state.records.filter(x=>x.project===r.id&&x.review==='accepted'&&!['done','cancelled'].includes(x.status)).length;body.append(badge(r.status,'accepted'),node('h2','',r.title));body.append(node('p','muted',r.note||'Current status has not been described.'));const facts=node('div','fact-row');facts.append(node('span','',r.owner||'Owner unconfirmed'),node('span','',kind==='project'?count+' open records':'Confirmed '+fmtDate(r.confirmed_at)));body.append(facts,button('Open brief →',()=>showBrief(r.id)));card.append(body);grid.append(card);});content.append(grid);
}
function renderWork(content){
  content.append(heading('All work','Every accepted commitment and issue, including deferred and completed work.'));
  const toolbar=node('div','toolbar'),filter=node('input'),status=node('select');
  filter.placeholder='Filter by title, owner, client, or project';filter.setAttribute('aria-label','Filter accepted work');status.setAttribute('aria-label','Work status');
  [['active','Open work'],['all','All statuses'],['done','Done / cancelled']].forEach(([v,t])=>{const o=node('option','',t);o.value=v;status.append(o);});
  toolbar.append(filter,status);const results=node('div');content.append(toolbar,results);
  const draw=()=>{results.replaceChildren();const q=filter.value.toLowerCase();const rows=state.records.filter(r=>r.review==='accepted'&&['commitment','issue'].includes(r.kind)&&(status.value==='all'||(status.value==='done')===['done','cancelled','archived'].includes(r.status))&&[r.title,r.owner,r.client,projectName(r.project)].some(v=>(v||'').toLowerCase().includes(q)));results.append(section('Accepted records',rows.length));if(!rows.length)results.append(empty('No matching work','Create a record or accept an observation in Review.'));rows.forEach(r=>results.append(recordCard(r)));};filter.oninput=draw;status.onchange=draw;draw();
}
function renderReview(content){
  content.append(heading('Review what changed','Check the evidence, confirm the details, and keep only what is useful.'));
  const toolbar=node('div','toolbar'),filter=node('input');filter.placeholder='Filter by title, source, or project';filter.setAttribute('aria-label','Filter review items');const kind=node('select');kind.setAttribute('aria-label','Record type');[['','All types'],['project','Projects'],['commitment','Commitments'],['decision','Decisions'],['issue','Issues']].forEach(([v,t])=>{const o=node('option','',t);o.value=v;kind.append(o);});toolbar.append(filter,kind);content.append(toolbar);const results=node('div');content.append(results);
  const draw=()=>{results.replaceChildren();const q=filter.value.toLowerCase();const rows=state.records.filter(r=>(r.review==='pending'||(r.review==='accepted'&&r.source_changed))&&(!kind.value||r.kind===kind.value)&&[r.title,r.evidence?.[0]?.note_path,projectName(r.project)].some(x=>(x||'').toLowerCase().includes(q))).sort((a,b)=>(b.evidence?.[0]?.event_date||'').localeCompare(a.evidence?.[0]?.event_date||''));if(!rows.length){results.append(empty('Nothing awaiting review','Scan sources when notes change. Dismissed and accepted records stay remembered.'));return;}results.append(section('Pending observations',rows.length));rows.slice(0,100).forEach(r=>{const card=node('article','record-card'),body=node('div','card-body'),meta=node('div','card-meta');meta.append(badge(humanKind(r)),badge(r.status),node('span','',r.evidence?.[0]?.event_date?'Source date '+fmtDate(r.evidence[0].event_date):'Source date unknown'));if(projectName(r.project))meta.append(node('span','',projectName(r.project)));body.append(meta,node('h3','',r.title),evidenceBlock(r));if(r.source_changed)body.append(node('p','error','Source changed since this observation. Refresh the evidence before accepting.'));card.append(body,footer(r,true));results.append(card);});if(rows.length>100)results.append(node('p','muted','Showing the 100 most recent observations. Filter by project or type to narrow the review.'));};filter.oninput=draw;kind.onchange=draw;draw();
}
function renderDecisions(content){content.append(heading('Decisions & history','The accepted position, its rationale, and the original evidence.'));const rows=state.records.filter(r=>r.kind==='decision'&&r.review==='accepted');if(!rows.length)content.append(empty('No decisions recorded yet','Record a decision directly or extract proposed decisions from a source note.',button('Record a decision',()=>editRecord(null,'accept','decision'),'primary')));else rows.forEach(r=>{const card=recordCard(r);const body=card.querySelector('.card-body');body.append(evidenceBlock(r));if(r.history.length)body.append(node('p','history',r.history.slice(-3).map(h=>`${fmtDate(h.at)} · ${h.action}${h.note?' · '+h.note:''}`).join('\n')));content.append(card);});}
async function showBrief(id){
  state.brief=id;const generation=++state.navigation;
  try{const data=await api('/ui/api/brief?id='+encodeURIComponent(id));if(generation!==state.navigation)return;const content=$('#content');content.replaceChildren();const r=data.entity;content.append(button('← Back to '+({project:'projects',client:'clients',person:'people'}[r.kind]||'workspace'),()=>{state.brief=null;render();}),heading(r.title,r.note||'Current status has not been described.'));const toolbar=node('div','toolbar');toolbar.append(badge(r.status,'accepted'),node('span','small','Last confirmed '+fmtDate(r.confirmed_at)),button('Edit details',()=>editRecord(r),'secondary'),button('View source',()=>openSource({record:r.id}),'secondary'));content.append(toolbar);const cols=node('div','columns'),main=node('div'),side=node('aside','side-column');main.append(section('Accepted work & decisions',data.accepted.length));if(!data.accepted.length)main.append(empty('No accepted work linked yet','Choose this project when reviewing or creating a commitment.'));data.accepted.forEach(x=>main.append(recordCard(x)));const box=node('div','aside-card');box.append(node('h3','',data.pending.length+' observations to review'),node('p','', 'Pending observations remain separate from the current project position.'),button('Open review',()=>go('review')));side.append(box);const evidence=node('div','aside-card');evidence.append(node('h3','', 'Project evidence'),evidenceBlock(r));side.append(evidence);cols.append(main,side);content.append(cols);}catch(e){notice(e.message,true);}
}
function renderSearch(content){
  content.append(heading('Sources & search','Find original notes, exact project references, and technical context.'));
  const processing=node('div');content.append(processing);
  api('/ui/api/extraction').then(data=>{const jobs=data.jobs||[];if(!jobs.length)return;processing.append(section('Recent meeting processing',jobs.length));jobs.slice(0,5).forEach(job=>{const row=node('div','search-result');row.append(button(job.note_path,()=>openSource({path:job.note_path})),badge(job.state,['failed','partial','interrupted'].includes(job.state)?'danger':''));row.append(node('p','small',job.detail||`${job.succeeded||0} / ${job.sections} sections processed`));processing.append(row);});}).catch(error=>processing.append(node('p','error',error.message)));
  const form=node('form','toolbar'),input=node('input');input.type='search';input.placeholder='Project number, client, method, or decision…';input.setAttribute('aria-label','Search source notes');const mode=node('select');mode.setAttribute('aria-label','Search mode');[['lexical','Exact words'],['semantic','Similar meaning']].forEach(([v,t])=>{const o=node('option','',t);o.value=v;mode.append(o);});const submit=node('button','primary','Search');submit.type='submit';form.append(input,mode,submit);content.append(form);const results=node('div');content.append(results);let request=0;
  async function list(){const n=++request;try{const d=await api('/ui/api/sources');if(n!==request)return;draw(d.sources);}catch(e){results.replaceChildren(empty('Sources unavailable',e.message));}}
  function draw(rows){results.replaceChildren();results.append(section('Sources',rows.length));if(!rows.length)results.append(empty('No matching sources','Try fewer words or scan sources if you have added notes.'));rows.forEach(r=>{const card=node('article','search-result'),meta=node('div','card-meta');meta.append(badge(r.source_type||'unknown'),badge(r.review_status||'unreviewed',(r.review_status==='superseded'||r.review_status==='contested')?'danger':'pending'),node('span','',fmtDate(r.event_date)));card.append(meta,button(r.title||r.note_path,()=>openSource({path:r.note_path})),node('p','small',r.note_path));if(r.text)card.append(node('p','',r.text));if(r.superseded_by)card.append(node('p','error','Superseded by '+r.superseded_by));results.append(card);});}
  form.onsubmit=async e=>{e.preventDefault();const n=++request;if(!input.value.trim()){list();return;}submit.disabled=true;results.replaceChildren(node('p','muted','Searching…'));try{const url=mode.value==='semantic'?'/ui/api/search?q=':'/ui/api/lookup?q=';const d=await api(url+encodeURIComponent(input.value));if(n===request)draw(d.results);}catch(error){if(n===request)results.replaceChildren(empty('Search could not be completed',error.message));}finally{submit.disabled=false;}};list();
}
async function openSource(params){
  const dialog=$('#source-panel');if(!dialog.open)dialog.showModal();$('#source-title').textContent='Loading evidence…';$('#source-meta').replaceChildren();$('#source-body').replaceChildren();const generation=String(Date.now())+Math.random();dialog.dataset.request=generation;
  try{const data=await api('/ui/api/source?'+new URLSearchParams(params));if(dialog.dataset.request!==generation)return;$('#source-title').textContent=data.title||data.evidence?.note_path||'Recorded evidence';const meta=$('#source-meta');if(data.changed||data.availability==='missing')meta.append(node('p','coverage-banner',data.availability==='missing'?'The original source is no longer available. The cited passage is preserved below.':'This source changed since the record was created. Review its current content before accepting changes.'));
    if(data.evidence)meta.append(node('blockquote','evidence',data.evidence.quote));
    if(data.availability==='manual'){meta.append(node('p','muted','This record was entered and confirmed directly in the workspace.'));return;}
    if(data.content){meta.append(node('p','small',`${data.note_path} · ${fmtDate(data.event_date)} · ${data.source_type} · ${data.review_status}`));const extract=button('Extract proposed changes',async()=>{extract.disabled=true;extract.textContent='Processing sections…';try{const job=await api('/ui/api/extract',{note_path:data.note_path});notice('Processing started. You can keep using the workspace.');watchExtraction(job.id, extract);}catch(e){notice(e.message,true);}finally{extract.disabled=false;extract.textContent='Extract proposed changes';}},'secondary');meta.append(extract);const lines=node('div','source-lines');data.content.split('\n').forEach((text,i)=>{const match=data.evidence&&i+1>=data.evidence.line_start&&i+1<=data.evidence.line_end&&!data.changed;const row=node('div','source-line'+(match?' highlight':''));row.append(node('span','line-number',String(i+1)),node('span','line-text',text||' '));lines.append(row);});$('#source-body').append(lines);}}
  catch(e){if(dialog.dataset.request!==generation)return;$('#source-title').textContent='Evidence unavailable';$('#source-body').append(node('p','error',e.message));}
}
const watchedJobs=new Set();
async function watchExtraction(id, control){
  if(watchedJobs.has(id))return;watchedJobs.add(id);
  async function poll(){try{const job=await api('/ui/api/extraction?id='+encodeURIComponent(id));if(['queued','running'].includes(job.state)){if(control?.isConnected)control.textContent=`Processing ${job.progress?.succeeded||0} / ${job.sections} sections`;setTimeout(poll,2000);return;}watchedJobs.delete(id);if(control?.isConnected)control.textContent='Extract proposed changes';notice(job.detail||`${job.created||0} proposals added. ${job.failed||0} sections need another attempt.`,job.state!=='ready');await refresh();}catch(error){watchedJobs.delete(id);notice(error.message,true);}}
  await poll();
}
async function resumeExtractions(){try{const data=await api('/ui/api/extraction');for(const job of data.jobs||[])if(['queued','running'].includes(job.state))watchExtraction(job.id);}catch{/* Connection errors are already surfaced by workspace loading. */}}
async function mutate(r,action,fields){try{await api('/ui/api/review',{id:r.id,version:r.version,action,fields});notice(action==='reject'?'Observation dismissed.':action==='acknowledge'?'Acknowledged until tomorrow. The commitment remains open.':'Record updated.');await refresh();}catch(e){notice(e.message,true);}}
async function editRecord(r,action,kind='commitment'){
  if(r?.source_changed&&!['accept_changes','update','resolve'].includes(action)){const dialog=$('#source-panel');await openSource({record:r.id});const b=button(r.latest_evidence?'Review current evidence':'Update the preserved record',()=>{dialog.close();editRecord(r,r.latest_evidence?'accept_changes':'update');},'primary');$('#source-meta').append(b);if(r.review==='accepted')$('#source-meta').append(button('Keep the accepted position',async()=>{dialog.close();await mutate(r,'keep_current');},'secondary'));return;}
  state.editing={record:r,action:action||(r?.review==='accepted'?'update':'accept')};const f=$('#record-form');f.reset();$('#record-error').textContent='';$('#editor-title').textContent=!r?'New record':state.editing.action==='resolve'?'Record resolution':r.review==='pending'?'Review observation':'Update record';$('#kind-label').classList.toggle('hidden',Boolean(r));f.elements.kind.value=r?.kind||kind;
  const project=f.elements.project;project.replaceChildren(node('option','','No project assigned'));project.firstChild.value='';state.records.filter(x=>x.kind==='project'&&x.review!=='rejected').forEach(x=>{const o=node('option','',x.title+(x.review==='pending'?' (unconfirmed)':''));o.value=x.id;project.append(o);});
  for(const k of ['title','project','client','owner','due','status','note'])f.elements[k].value=r?.[k]|| (k==='status'?(['project','client','person'].includes(kind)?'active':'open'):'');if(state.editing.action==='resolve')f.elements.status.value='done';if(state.editing.action==='accept_changes'&&r.observed_status)f.elements.status.value=r.observed_status;
  $('#editor-evidence').replaceChildren();if(r)$('#editor-evidence').append(evidenceBlock(state.editing.action==='accept_changes'?{...r,evidence:[r.latest_evidence]}:r));$('#save-record').textContent=!r?'Save record':state.editing.action==='resolve'?'Confirm resolution':r.review==='pending'?'Accept record':'Save changes';$('#editor').showModal();
}
$('#record-form').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget,r=state.editing.record;const fields=Object.fromEntries(['project','client','owner','due','status','note'].map(k=>[k,f.elements[k].value||null]));fields.note=fields.note||'';if(r)fields.title=f.elements.title.value.trim();const b=$('#save-record');b.disabled=true;$('#record-error').textContent='';try{await api(r?'/ui/api/review':'/ui/api/records',r?{id:r.id,version:r.version,action:state.editing.action,fields}:{kind:f.elements.kind.value,title:f.elements.title.value.trim(),fields});$('#editor').close();notice('Record saved.');await refresh();}catch(error){$('#record-error').textContent=error.message;}finally{b.disabled=false;}};
function defer(r){state.defer=r;$('#defer-error').textContent='';$('#defer-form').reset();$('#defer-dialog').showModal();}
$('#defer-form').onsubmit=async e=>{e.preventDefault();const b=e.currentTarget.querySelector('button.primary');b.disabled=true;try{await api('/ui/api/review',{id:state.defer.id,version:state.defer.version,action:'defer',fields:{until:e.currentTarget.elements.until.value}});$('#defer-dialog').close();notice('Reminder deferred.');await refresh();}catch(error){$('#defer-error').textContent=error.message;}finally{b.disabled=false;}};
async function syncSources(){const b=$('#sync');b.disabled=true;b.textContent='Scanning…';try{const d=await api('/ui/api/sync',{});notice(`${d.sources} sources scanned. ${d.created} new observations await review.`,d.state==='partial');await refresh();}catch(e){notice(e.message,true);}finally{b.disabled=false;b.textContent='↻ Scan sources';}}
$('#sync').onclick=syncSources;$('#new').onclick=()=>editRecord(null);$('#account').onclick=showConnection;
$('#connect-form').onsubmit=async e=>{
  e.preventDefault();if(connecting)return;
  const token=normalizeToken($('#token').value),submit=e.currentTarget.querySelector('button[type="submit"]');
  $('#connect-error').textContent='';
  if(!token||/[^\x21-\x7e]/.test(token)){$('#connect-error').textContent='Paste the complete access token or its Authorization: Bearer header.';return;}
  connecting=true;submit.disabled=true;submit.textContent='Connecting…';authGeneration++;state.generation++;state.navigation++;
  try{
    await api('/ui/api/workspace',undefined,{token,connecting:true});
    accessToken=token;
    let remembered=true;try{sessionStorage.setItem(TK,token);}catch{remembered=false;}
    $('#connection').close();$('#token').value='';
    await refresh();resumeExtractions();
    if(!remembered)notice('Connected for this page. Browser storage is unavailable, so reconnect after reloading.');
  }catch(error){$('#connect-error').textContent=error.message;$('#token').focus();}
  finally{connecting=false;submit.disabled=false;submit.textContent='Connect';}
};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>go(b.dataset.view));
$('#today-date').textContent=new Date().toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric',year:'numeric'});
window.addEventListener('hashchange',()=>{const view=location.hash.slice(1)||'today';if(view!==state.view)go(view);});
if(location.hash)state.view=location.hash.slice(1);document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view));
async function startWorkspace(){
  // Validate a saved token once before loading data in parallel. Anonymous page
  // visits must not generate the cluster of 401s that triggers proxy bans.
  if(accessToken){
    try{await api('/ui/api/workspace');}
    catch(error){if(error instanceof SupersededRequest)return;$('#content').replaceChildren(empty(error instanceof AuthenticationError?'Connect to your workspace':'Workspace not available',error.message,button('Connect',showConnection,'secondary')));$('#content').setAttribute('aria-busy','false');return;}
  }
  await refresh();if(accessToken)resumeExtractions();
}
startWorkspace();
