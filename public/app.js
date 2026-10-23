'use strict';
const $ = (id) => document.getElementById(id);
const labels = {scheduled:'重置预告', announced:'已公布', completed:'宣布完成', cancelled:'已取消', unconfirmed:'待确认'};
const scopeLabels = {global:'全局', paid:'付费用户', 'plus-pro-business':'Plus / Pro / Business', unknown:'适用范围未明确'};
const typeLabel = (e) => e.type === 'credit' ? '重置卡' : '额度重置';
let model = null, filter = 'all', limit = 8, toastTimer = null, loading = false;
const dateFormat = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
const fmt = (v) => v && Number.isFinite(Date.parse(v)) ? dateFormat.format(new Date(v)) : '未明确';
const escape = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const timeValue = (e) => e.window_end || e.expected_at || e.window_start;
const safeLink = (v) => { try {const u = new URL(v); return u.protocol === 'https:' && ['x.com','twitter.com','www.starshoreai.com'].includes(u.hostname) ? u.href : null;} catch {return null;} };
function title(e) {
  if(e.title) return e.title;
  if (isExpired(e)) return e.type === 'credit' ? '重置卡旧预告，等待后续公告。' : '这条预告已到期，等待后续公告。';
  if (e.type === 'credit') return e.state === 'completed' ? '新的重置卡，已宣布发放。' : '有一张重置卡，正在路上。';
  return ({scheduled:'新一轮额度重置，已有公开预告。',announced:'一条新的额度重置消息。',completed:'一次新的开始，已宣布重置。',cancelled:'这条重置预告，已被取消。',unconfirmed:'捕获到重置信号，等待确认。'})[e.state] || '一条新的公开重置动态。';
}
function isExpired(e) {return ['scheduled','announced'].includes(e.state) && timeValue(e) && Date.parse(timeValue(e)) <= Date.now();}
function statusLabel(e) {return isExpired(e) ? '预告已到期' : labels[e.state] || '待确认';}
function description(e) {
  if(e.summary) return e.summary;
  if (e.type === 'credit') return `${scopeLabels[e.scope] || scopeLabels.unknown} · 重置卡需在账号中自行查看与使用。`;
  if (e.state === 'completed') return `${scopeLabels[e.scope] || scopeLabels.unknown} · 公开消息宣布完成，个人额度请自行核对。`;
  if (isExpired(e)) return '预计时间已过，不能据此认定已完成。请核对后续公告。';
  if (timeValue(e)) return `${e.time_kind === 'deadline' ? '预计截止' : '预计时间'} ${fmt(timeValue(e))} · 北京时间，具体以原帖为准。`;
  return `${scopeLabels[e.scope] || scopeLabels.unknown} · 时间尚未明确，请以公开原帖为准。`;
}
function notify(text) {clearTimeout(toastTimer);$('toast').textContent=text;$('toast').classList.remove('hidden');toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),3000);}
function freshness() {
  if (!model) return false;
  const age=Date.now()-Date.parse(model.source_checked_at);
  return model.sync_ok && model.source_healthy && Number.isFinite(age) && age>=-60000 && age<=(model.fresh_seconds||1800)*1000;
}
function upcoming() {
  const e=model?.summary?.upcoming;
  return freshness() && e && Date.parse(e.target_at)>Date.now() ? e : null;
}
function renderHero() {
  if (!model) return;
  const e=model.summary.latest, active=upcoming(), fresh=freshness();
  $('connection').className=`connection ${fresh?'healthy':'stale'}`;
  $('connection').innerHTML=`<i></i>${fresh?'信号同步正常':'数据待更新'}`;
  $('record-count').textContent=model.events.length;
  $('last-reset').textContent=model.summary.latest_completed ? fmt(model.summary.latest_completed.published_at) : '暂无记录';
  $('source-state').textContent=fresh?'正常同步':'等待更新';
  $('source-checked').textContent=model.source_checked_at ? `${fmt(model.source_checked_at)} 采集 · 北京时间` : '尚未取得有效数据';
  $('sync-time').textContent=model.last_sync?`${fmt(model.last_sync)} 同步`:'等待首次同步';
  $('hero-badge').innerHTML=`<i></i>${fresh?(e?escape(statusLabel(e)):'等待公开消息'):'历史资料 · 待更新'}`;
  $('hero-title').textContent=!fresh?'信号稍作停留。':active?'新的开始，已有预告。':e?.state==='completed'?'新的开始，已宣布。':e&&isExpired(e)?'时间已到，等待确认。':'等待下一条好消息。';
  $('hero-description').textContent=e?description(e):'正在等待可核对的公开消息。';
  $('countdown-label').textContent=active?(active.time_kind==='deadline'?'距离预计截止时间':'距离公开预计时间'):'当前进展';
  $('deadline').textContent=active?`${active.time_kind==='deadline'?'预计截止':'预计时间'} ${fmt(active.target_at)} · 北京时间`:'公告发布时间与个人额度到账时间可能不同。';
  $('calendar-button').disabled=!active;
  $('subscribe-calendar').disabled=!active;
  $('calendar-note').textContent=active?'单次日程，预计时间前 15 分钟提醒。时间更改需重新导入。':'当前没有有效的未来预告，暂不能生成日历提醒。';
  $('hero-detail').disabled=!e;
  $('hero-detail').textContent=e?.state==='completed'?'查看完成消息 ↗':'查看消息详情 ↗';
  $('error-banner').classList.toggle('hidden',fresh);
  if(!fresh)$('error-banner').textContent='RSS 镜像暂不可用或资料较久未更新。以下保留已采集原帖，暂不判断当前是否重置。';
  renderCountdown();
}
function renderCountdown() {
  if(!model)return;
  const active=upcoming();
  if(!active){
    $('countdown').className='countdown plain';
    $('countdown').textContent=!freshness()?'等待新鲜消息':model.summary.latest?.state==='completed'?'查看自己的额度':model.summary.latest&&isExpired(model.summary.latest)?'等待完成公告':'暂未有明确排期';
    // A deadline passing must also disable the download without waiting for the next fetch.
    $('calendar-button').disabled=true;$('subscribe-calendar').disabled=true;
    return;
  }
  const total=Math.max(0,Math.floor((Date.parse(active.target_at)-Date.now())/1000));
  const parts=[Math.floor(total/3600),Math.floor(total/60)%60,total%60];
  $('countdown').className='countdown';
  $('countdown').innerHTML=parts.map((n,i)=>`<span>${String(n).padStart(2,'0')}<small>${['小时','分钟','秒'][i]}</small></span>`).join('<em>:</em>');
}
function renderFeed() {
  if(!model)return;
  const query=$('search').value.trim().toLowerCase();
  const events=model.events.filter(e=>(filter==='all'||(filter==='credit'?e.type==='credit':e.state===filter&&e.type!=='credit'))&&`${title(e)} ${description(e)} ${statusLabel(e)} ${e.published_at||''} ${fmt(e.published_at)} ${e.event_id}`.toLowerCase().includes(query));
  $('feed-count').textContent=`${events.length} 条公开消息 · 北京时间`;
  $('feed').innerHTML=events.length ? events.slice(0,limit).map(e=>{
    const cls=e.type==='credit'?'credit':e.state==='completed'?'completed':isExpired(e)||e.state==='cancelled'?'muted':'';
    const link=safeLink(e.source_urls?.[0]);
    return `<article class="event-row"><div class="event-symbol ${cls}" aria-hidden="true">${e.type==='credit'?'▦':e.state==='completed'?'✓':'↗'}</div><div class="event-main"><div class="event-meta"><span class="badge ${cls}">${escape(statusLabel(e))}</span><span>${typeLabel(e)}</span><span>·</span><span>${e.confidence==='candidate'?'待核验原帖':'Tibo 公开消息'}</span></div><button class="event-title" data-event="${escape(e.event_id)}">${escape(title(e))}</button><p class="event-description">${escape(description(e))}</p><div class="event-links"><button data-event="${escape(e.event_id)}">查看详情 ↗</button>${link?`<a href="${escape(link)}" target="_blank" rel="noopener noreferrer">核对原帖 ↗</a>`:''}</div></div><time class="event-time" datetime="${escape(e.published_at||'')}">${escape(fmt(e.published_at))}</time></article>`;
  }).join(''):'<div class="empty-state">没有匹配的动态。试试其他分类或关键词。</div>';
  $('load-more').classList.toggle('hidden',events.length<=limit);
  $('load-more').textContent=`查看更多动态（还剩 ${Math.max(0,events.length-limit)} 条）↓`;
}
function showDetail(id) {
  const event=model?.events.find(e=>e.event_id===id);
  if(!event){notify('这条消息暂未收录');return;}
  const sources=(event.source_urls||[]).map(safeLink).filter(Boolean);
  const fields=[['消息类型',typeLabel(event)],['公开状态',statusLabel(event)],['公告发布时间',`${fmt(event.published_at)} · 北京时间`],['适用范围',scopeLabels[event.scope]||scopeLabels.unknown],['预计时间',timeValue(event)?`${fmt(timeValue(event))} · 北京时间`:'原帖未明确'],['范围核验',event.scope_verified?'来源标记为已核验':'来源未核验范围']];
  const evidence=(event.evidence||[]).map(p=>`<section class="post-evidence"><div><a href="${escape(safeLink(p.url)||'#')}" target="_blank" rel="noopener noreferrer">@thsottiaux ${p.is_reply?'· 回复':'· 原帖'} ↗</a><time>${escape(fmt(p.created_at))}</time></div><blockquote class="post-text">${escape(p.text)}</blockquote>${p.quoted_text?`<details><summary>原帖引用的内容（其他作者）</summary><p class="post-text">${escape(p.quoted_text)}</p></details>`:''}</section>`).join('');
  $('detail-content').innerHTML=`<span class="badge">${escape(statusLabel(event))}</span><h2 id="detail-title">${escape(title(event))}</h2><p>${escape(description(event))}</p><dl class="detail-grid">${fields.map(([k,v])=>`<div><dt>${k}</dt><dd>${escape(v)}</dd></div>`).join('')}</dl>${evidence?`<h3>原帖证据</h3>${evidence}`:''}<div class="detail-sources">${sources.map((url,i)=>`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${i===0?'查看公开原帖':'查看补充原帖'} ↗</a>`).join('')}</div><p class="detail-note">经公开 RSS 镜像采集。${event.confidence==='candidate'?'当前仅为关键词筛选，尚未完成上下文核验。':'摘要请结合原帖核对。'}公开预告不保证个人到账；预计时间已过也不代表重置已完成。${event.original_timezone==='PST'?' 原文 PST 按 UTC−8 换算；日历日期需结合作者当地发布时间。':''}</p><button class="button" id="copy-event">复制消息链接 ↗</button>`;
  $('copy-event').onclick=()=>copyText(`${location.origin}/#event/${encodeURIComponent(id)}`);
  if(!$('detail-dialog').open)$('detail-dialog').showModal();
}
function handleHash(){
  if(location.hash.startsWith('#event/')){if(model)showDetail(decodeURIComponent(location.hash.slice(7)));return;}
  if($('detail-dialog').open)$('detail-dialog').close();
  const history=location.hash==='#history';
  document.querySelectorAll('a.nav-link').forEach(a=>a.classList.toggle('active',a.getAttribute('href')===(history?'#history':'#overview')));
  $('page-label').textContent=history?'历史动态':'总览';
}
async function copyText(text){
  try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);}else{const input=document.createElement('textarea');input.value=text;input.setAttribute('readonly','');input.style.position='fixed';input.style.opacity='0';document.body.append(input);input.select();const ok=document.execCommand('copy');input.remove();if(!ok)throw new Error();}notify('已复制');}
  catch{notify('请选中地址后手动复制');}
}
function showSubscribe(){ $('rss-url').value=location.origin+'/rss.xml';$('subscribe-dialog').showModal(); }
async function load(){
  if(loading)return;loading=true;$('refresh').disabled=true;
  try{const response=await fetch('/api/events',{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!response.ok)throw new Error();const data=await response.json();if(!Array.isArray(data.events)||!data.summary)throw new Error();model=data;renderHero();renderFeed();renderCollection();if(!archiveLoaded)loadPosts(false);if(location.hash.startsWith('#event/'))handleHash();}
  catch{if(model){model.sync_ok=false;renderHero();}else{$('error-banner').classList.remove('hidden');$('error-banner').textContent='暂时无法连接观察站，请稍后刷新。';$('hero-title').textContent='信号暂时未连接。';$('hero-description').textContent='服务恢复后将继续展示公开动态。';$('feed').innerHTML='<div class="empty-state">暂时无法读取消息，点击右上角刷新重试。</div>'; $('connection').innerHTML='<i></i>连接暂不可用';}}
  finally{loading=false;$('refresh').disabled=false;}
}
$('today').textContent=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'long',day:'numeric',weekday:'short'}).format(new Date());
$('refresh').onclick=()=>{load();loadPosts(false);};
for(const id of ['side-subscribe','top-subscribe'])$(id).onclick=showSubscribe;
$('calendar-button').onclick=$('subscribe-calendar').onclick=()=>{if(upcoming())location.href='/reminder.ics';else notify('当前没有有效的未来预告');};
$('hero-detail').onclick=()=>{if(model?.summary.latest)location.hash='event/'+encodeURIComponent(model.summary.latest.event_id);};
$('about-button').onclick=()=>$('about-dialog').showModal();
$('copy-rss').onclick=()=>copyText($('rss-url').value);
$('feed').onclick=(event)=>{const button=event.target.closest('[data-event]');if(button)location.hash='event/'+encodeURIComponent(button.dataset.event);};
$('search').oninput=()=>{limit=8;renderFeed();};
document.querySelectorAll('.tab').forEach(button=>button.onclick=()=>{filter=button.dataset.filter;limit=8;document.querySelectorAll('.tab').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});renderFeed();});
$('load-more').onclick=()=>{limit+=8;renderFeed();};
document.querySelectorAll('dialog').forEach(dialog=>{dialog.querySelector('.close-dialog').onclick=()=>dialog.close();dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});});
$('detail-dialog').addEventListener('close',()=>{if(location.hash.startsWith('#event/')){history.replaceState(null,'',location.pathname+'#history');handleHash();}});
window.addEventListener('hashchange',handleHash);
window.addEventListener('focus',load);
setInterval(()=>{renderCountdown();if(model&&!freshness()&&!$('error-banner').classList.contains('hidden'))return;if(model&&!freshness())renderHero();},1000);
setInterval(()=>{if(!document.hidden)load();},60000);
handleHash();load();

let archiveLoaded=false, archiveLoading=false, archiveNext=0, archiveItems=[], archiveQuery='';
function renderCollection(){
  const c=model?.collection;if(!c)return;
  const done=c.windows.filter(x=>x.status==='source_exhausted').length;
  const partial=c.windows.filter(x=>x.status==='partial').length;
  $('archive-status').textContent=`已收录 ${c.posts} 条原帖及回复 · 已遍历 ${done}/${c.windows.length} 个历史时间段${partial?` · ${partial} 段待重试`:''}`;
  $('archive-note').textContent=`目标范围：2026-04-23 至今。${c.coverage_note} ${model.ai?.enabled?'AI 正在整理额度相关消息。':'AI 分析尚未启用；上方信号待结合上下文核验。'}`;
}
async function loadPosts(append){
  if(archiveLoading)return;archiveLoading=true;
  const q=$('archive-search').value.trim();
  try{
    const offset=append?archiveNext:0;
    const r=await fetch(`/api/posts?q=${encodeURIComponent(q)}&offset=${offset||0}&limit=20`,{signal:AbortSignal.timeout(15000)});
    if(!r.ok)throw new Error();const d=await r.json();
    archiveItems=append&&q===archiveQuery?archiveItems.concat(d.posts):d.posts;
    archiveQuery=q;archiveNext=d.next_offset;archiveLoaded=true;
    $('archive-posts').innerHTML=archiveItems.map(p=>`<article class="archive-post"><div class="event-meta"><span>@thsottiaux · ${p.is_reply?'回复':'原帖'}</span><time>${escape(fmt(p.created_at))}</time></div><p class="post-text">${escape(p.text)}</p><a href="${escape(safeLink(p.url)||'#')}" target="_blank" rel="noopener noreferrer">核对 X 原帖 ↗</a></article>`).join('')||'<div class="empty-state">暂无匹配原帖；历史采集仍会继续。</div>';
    $('archive-more').classList.toggle('hidden',archiveNext===null);
  }catch{$('archive-posts').innerHTML='<div class="empty-state">暂时无法读取原帖，请刷新重试。</div>';}
  finally{archiveLoading=false;}
}
$('archive-more').onclick=()=>loadPosts(true);
$('archive-search-form').onsubmit=e=>{e.preventDefault();loadPosts(false);};
