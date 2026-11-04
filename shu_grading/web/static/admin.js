/* Administrator pages share the application's authentication, dialogs and styles. */
const adminState={tab:'overview',offset:0,q:'',status:'',model:'',start:'',end:'',generation:0};
const metric=n=>n===null||n===undefined?'未提供':Number(n).toLocaleString();
const when=n=>n?new Date(n*1000).toLocaleString():'—';
const seconds=(start,end)=>end?Math.max(0,end-start).toFixed(1)+' 秒':'—';
const jobLabel=s=>({pending:'等待处理',processing:'正在处理',succeeded:'已完成',failed:'失败',interrupted:'已中断',unknown:'进程中断／结果未知',retry_scheduled:'已安排重试',lease_expired:'租约失效'})[s]||s;
function adminTable(headers,rows){return `<div class="admin-table-scroll"><table class="admin-table"><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${headers.length}" class="empty">暂无记录</td></tr>`}</tbody></table></div>`;}
function adminPager(total){return `<div class="admin-pager"><span class="muted">共 ${total} 条 · 第 ${Math.floor(adminState.offset/50)+1} 页</span>${action('admin-prev','上一页',true,adminState.offset?'':'disabled')}${action('admin-next','下一页',true,adminState.offset+50<total?'':'disabled')}</div>`;}
async function showAdmin(tab=adminState.tab){
  state.page='admin';adminState.tab=tab;const generation=++adminState.generation;
  $('.account-menu')?.classList.remove('is-open');document.activeElement?.blur();
  shell(`<div class="page-head"><div><div class="eyebrow">管理员控制台</div><h1>服务与阅卷管理</h1><p class="muted">模型用量从追踪功能启用后开始记录，历史未记录用量无法补算。</p></div><div class="actions">${action('admin-refresh','刷新',true)}${action('nav','返回阅卷工作台',true,'data-page="tasks"')}</div></div><div class="resource-tabs">${['overview','traces','accounts'].map((t,i)=>action('admin-tab',['运行概览','阅卷追踪','账户管理'][i],tab!==t,`data-tab="${t}"`)).join('')}</div><div id="admin-content" class="card"><p>正在加载…</p></div>`);
  const target=$('#admin-content');
  try{
    let html;
    if(tab==='overview'){
      const since=new Date();since.setHours(0,0,0,0);const data=await api('/admin/overview?since='+since.getTime()/1000),t=data.totals;
      html=`<div class="admin-metrics">${[['今日模型调用',t.calls],['调用失败',t.failed||0],['输入 token（已报告）',t.input_tokens],['输出 token（已报告）',t.output_tokens],['缓存命中 token',t.cached_tokens],['用量不完整的调用',t.unknown_calls||0]].map(([label,value])=>`<div class="admin-metric"><span>${label}</span><strong>${metric(value)}</strong></div>`).join('')}</div><p class="note">今日按本机时区计算。缓存命中包含在输入统计内，思考 token 不重复计入输出。未提供的用量不能视为零；失败或中断请求也可能已产生费用。</p><h2>阅卷队列</h2><div class="actions">${data.queue.map(q=>`<span class="tag">${jobLabel(q.status)} ${q.count}</span>`).join('')||'暂无作业'}</div><div class="grading-service-status"><strong>阅卷服务</strong><span class="tag ${data.workers.some(w=>w.online)?'good':''}">${data.workers.some(w=>w.online)?'正常':'不可用'}</span><span class="muted">当前在线 ${data.workers.filter(w=>w.online).length} 个处理实例</span></div><h2 class="admin-section">今日模型用量</h2>${adminTable(['模型','调用','输入','输出','缓存命中','用量不完整'],data.models.map(m=>`<tr><td>${esc(m.provider)} / ${esc(m.model)}</td><td>${m.calls}</td><td>${metric(m.input_tokens)}</td><td>${metric(m.output_tokens)}</td><td>${metric(m.cached_tokens)}</td><td>${m.unknown_calls||0}</td></tr>`))}<p class="muted">平均调用耗时：${t.average_seconds==null?'—':t.average_seconds.toFixed(1)+' 秒'} · 账户 ${data.accounts.active||0} / ${data.accounts.total} 个启用 · 更新于 ${when(data.generated_at)}</p>`;
    }else if(tab==='traces'){
      const params=new URLSearchParams({q:adminState.q,status:adminState.status,model:adminState.model,offset:adminState.offset});
      if(adminState.start)params.set('start',new Date(adminState.start+'T00:00:00').getTime()/1000);
      if(adminState.end)params.set('end',new Date(adminState.end+'T23:59:59').getTime()/1000);
      const data=await api('/admin/traces?'+params);
      html=`<form id="admin-trace-filter" class="admin-filter">${field('账户／任务／答卷',`<input name="q" value="${esc(adminState.q)}" placeholder="输入名称搜索">`)}${field('作业状态',`<select name="status"><option value="">全部</option>${['pending','processing','succeeded','failed'].map(s=>`<option value="${s}" ${adminState.status===s?'selected':''}>${jobLabel(s)}</option>`).join('')}</select>`)}${field('模型',`<input name="model" value="${esc(adminState.model)}">`)}${field('开始日期',`<input type="date" name="start" value="${esc(adminState.start)}">`)}${field('结束日期',`<input type="date" name="end" value="${esc(adminState.end)}">`)}<button>筛选</button></form>${adminTable(['时间','账户／任务','答卷','状态','模型','调用','输入','缓存命中','输出','总耗时',''],data.items.map(j=>`<tr><td>${when(j.created)}</td><td>${esc(j.username)}<br>${esc(j.title)}</td><td>${esc(j.label)}</td><td>${jobLabel(j.status)}${j.unknown_calls?'<br><span class="muted">用量不完整</span>':''}</td><td>${esc(j.models||'—')}</td><td>${j.calls}</td><td>${metric(j.input_tokens)}</td><td>${metric(j.cached_tokens)}</td><td>${metric(j.output_tokens)}</td><td>${seconds(j.created,j.finished)}</td><td>${action('admin-trace','详情',true,`data-id="${j.id}"`)}</td></tr>`))}${adminPager(data.total)}`;
    }else{
      const [data,events]=await Promise.all([api('/admin/accounts?'+new URLSearchParams({q:adminState.q,offset:adminState.offset})),api('/admin/events')]);
      adminState.currentUser=data.current_user;
      html=`<div class="page-head"><form id="admin-account-filter" class="actions"><input name="q" aria-label="搜索账户" value="${esc(adminState.q)}" placeholder="搜索账号或备注"><button>搜索</button></form>${action('admin-create','创建教师账户')}</div>${adminTable(['账号','备注','状态','任务／答卷','输入 token','输出 token','缓存命中','操作'],data.items.map(u=>`<tr><td>${esc(u.username)} ${u.is_admin?'<span class="admin-badge">管理员</span>':''}${u.id===data.current_user?'<br><span class="muted">当前账户</span>':''}</td><td>${esc(u.recipient)}</td><td>${u.active?'启用':'停用'}</td><td>${u.tasks} / ${u.submissions}</td><td>${metric(u.input_tokens)}</td><td>${metric(u.output_tokens)}</td><td>${metric(u.cached_tokens)}</td><td><div class="actions">${action('admin-toggle',u.active?'停用':'启用',true,`data-id="${u.id}" data-active="${u.active?'0':'1'}" ${u.id===data.current_user?'disabled':''}`)}${action('admin-reset','重置密码',true,`data-id="${u.id}"`)}</div></td></tr>`))}${adminPager(data.total)}<p class="note">这里只创建教师账户，管理员资格由服务器命令授予。用量为服务商已报告部分，缺失数据未按零估算。当前账户不可停用；管理员忘记密码可通过服务器命令恢复。</p><details><summary>最近账户管理操作</summary>${adminTable(['时间','操作人','对象','操作'],events.map(e=>`<tr><td>${when(e.created)}</td><td>${esc(e.actor)}</td><td>${esc(e.target)}</td><td>${esc(({created:'创建账户',password_reset:'重置密码',enabled:'启用',disabled:'停用',admin_granted:'授予管理员',admin_revoked:'撤销管理员'})[e.action]||e.action)}</td></tr>`))}</details>`;
    }
    if(state.page==='admin'&&generation===adminState.generation&&target.isConnected)target.innerHTML=html;
  }catch(error){if(target.isConnected)target.innerHTML=`<p class="error">${esc(error.message)}</p>`;}
}
async function showTrace(id){
  const data=await api('/admin/traces/'+id),j=data.job;
  dialog(`<h2>${esc(j.title)} · ${esc(j.label)}</h2><p>${esc(j.username)} · 作业${jobLabel(j.status)} · ${when(j.created)}</p>${j.error?`<p class="error">${esc(j.error)}</p>`:''}<p class="note">作业成功表示评分已保存；调用成功仅表示拿到了模型响应。进程中断时，用量和计费可能未知。缓存、思考统计以服务商返回为准。</p>${data.attempts.map(a=>`<section class="trace-attempt"><h3>第 ${a.attempt} 次执行 · ${esc(jobLabel(a.outcome||'processing'))}</h3><p class="muted">${when(a.started)} · ${seconds(a.started,a.finished)} · ${esc(a.worker_id)}</p>${a.error?`<p class="error">${esc(a.error)}</p>`:''}${data.calls.filter(c=>c.attempt_id===a.id).map(c=>`<details class="trace-call"><summary>${c.purpose==='json_repair'?'JSON 修复':'作文评分'} · ${esc(c.model)} · ${jobLabel(c.status)} · ${seconds(c.started,c.finished)}</summary><p>输入 ${metric(c.input_tokens)} · 输出 ${metric(c.output_tokens)} · 缓存命中 ${metric(c.cached_tokens)} · 缓存写入 ${metric(c.cache_write_tokens)} · 思考 ${metric(c.reasoning_tokens)}</p><p class="muted">实际模型：${esc(c.actual_model||'未提供')} · 请求编号：${esc(c.request_id||'未提供')}</p>${c.error?`<p class="error">${esc(c.error_type)}：${esc(c.error)}</p>`:''}<h4>请求参数</h4><pre>${esc(JSON.stringify(c.parameters,null,2))}</pre><h4>输入提示词</h4><pre>${esc(JSON.stringify(c.messages,null,2))}</pre><h4>模型评分正文</h4><pre>${esc(c.response??'未收到／未保存')}</pre><h4>服务商用量字段</h4><pre>${esc(JSON.stringify(c.usage,null,2))}</pre></details>`).join('')||'<p class="muted">本次没有模型调用记录（功能启用前或请求尚未发出）。</p>'}</section>`).join('')}`);
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-action]');if(!button||button.disabled)return;const a=button.dataset.action;
  try{
    if(a==='admin-tab'){adminState.offset=0;adminState.q='';await showAdmin(button.dataset.tab);}
    if(a==='admin-refresh')await showAdmin();
    if(a==='admin-prev'||a==='admin-next'){adminState.offset+=a==='admin-prev'?-50:50;await showAdmin();}
    if(a==='admin-trace')await showTrace(button.dataset.id);
    if(a==='admin-create')dialog(`<h2>创建教师账户</h2><form id="admin-create-form">${field('账号','<input name="username" required minlength="2" maxlength="80" autocomplete="off">')}${field('初始密码','<input name="password" type="password" required minlength="8" maxlength="128" autocomplete="new-password">')}${field('使用人备注','<input name="recipient" maxlength="200">')}<button>创建账户</button></form>`);
    if(a==='admin-reset')dialog(`<h2>重置账户密码</h2><p class="muted">原登录和手机上传入口将失效，请将新密码交给使用人。</p><form id="admin-reset-form" data-id="${button.dataset.id}">${field('新密码','<input name="password" type="password" required minlength="8" maxlength="128" autocomplete="new-password">')}<button>确认重置</button></form>`);
    if(a==='admin-toggle'){const active=button.dataset.active==='1';if(!await notice(active?'确认启用此账户？':'停用后将撤销登录及手机上传入口，确认停用？',{title:'账户状态',confirm:true}))return;await api('/admin/accounts/'+button.dataset.id,'PATCH',{active});await showAdmin('accounts');await notice('账户状态已更新');}
  }catch(error){await notice(error.message);}
});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.id.startsWith('admin-'))return;event.preventDefault();
  const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
  try{
    const body=Object.fromEntries(new FormData(form));
    if(form.id==='admin-trace-filter'||form.id==='admin-account-filter'){Object.assign(adminState,body,{offset:0});await showAdmin();}
    if(form.id==='admin-create-form'||form.id==='admin-reset-form'){
      await api(form.id==='admin-create-form'?'/admin/accounts':'/admin/accounts/'+form.dataset.id,form.id==='admin-create-form'?'POST':'PATCH',body);
      $('#dialog').close();if(form.id==='admin-reset-form'&&form.dataset.id===adminState.currentUser){auth();await notice('密码已重置，请使用新密码重新登录。');}else{await showAdmin('accounts');await notice(form.id==='admin-create-form'?'账户已创建，请将账号和初始密码交给使用人。':'密码已重置，旧登录和手机入口已失效。');}
    }
  }catch(error){await notice(error.message);}finally{buttons.filter(b=>b.isConnected).forEach(b=>b.disabled=false);}
});
