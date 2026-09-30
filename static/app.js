const csrf = document.querySelector('meta[name="csrf-token"]').content;
const state = {bootstrap:null, telemetry:[], cqse:null, hyperfield:null, replay:null, replayTimer:null};

function $(s){return document.querySelector(s)}
function $$(s){return [...document.querySelectorAll(s)]}
function toast(msg,error=false){const t=$("#toast");t.textContent=msg;t.className="toast show"+(error?" error":"");setTimeout(()=>t.className="toast",2800)}
async function api(url,opt={}){
  const headers={"Content-Type":"application/json","X-CSRFToken":csrf,...(opt.headers||{})};
  const r=await fetch(url,{credentials:"same-origin",...opt,headers});
  const text=await r.text();let data={};try{data=JSON.parse(text)}catch{data={error:text||r.statusText}}
  if(!r.ok)throw new Error(data.error||`HTTP ${r.status}`);return data
}
function fmt(v,n=2){return Number.isFinite(+v)?(+v).toFixed(n):"—"}
function resize(canvas){const dpr=window.devicePixelRatio||1;const rect=canvas.getBoundingClientRect();const h=parseInt(canvas.getAttribute("height")||300,10);canvas.width=Math.max(1,Math.floor(rect.width*dpr));canvas.height=Math.floor(h*dpr);canvas.style.height=h+"px";return {ctx:canvas.getContext("2d"),w:canvas.width,h:canvas.height,dpr}}
function grid(ctx,w,h){ctx.strokeStyle="rgba(255,255,255,.055)";ctx.lineWidth=1;for(let x=0;x<w;x+=w/10){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke()}for(let y=0;y<h;y+=h/6){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke()}}
function lineChart(canvas,series){
  const {ctx,w,h}=resize(canvas);ctx.clearRect(0,0,w,h);grid(ctx,w,h);
  const all=series.flatMap(s=>s.values).filter(Number.isFinite);if(!all.length)return;
  let mn=Math.min(...all),mx=Math.max(...all);if(mx-mn<1e-9){mx=mn+1}
  const pad=26*(window.devicePixelRatio||1);
  series.forEach(s=>{ctx.strokeStyle=s.color;ctx.lineWidth=2*(window.devicePixelRatio||1);ctx.beginPath();
    s.values.forEach((v,i)=>{const x=pad+(w-2*pad)*(i/Math.max(1,s.values.length-1));const y=h-pad-(h-2*pad)*((v-mn)/(mx-mn));if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});ctx.stroke()})
}
function bars(canvas,values,palette){
  const {ctx,w,h}=resize(canvas);ctx.clearRect(0,0,w,h);grid(ctx,w,h);const max=Math.max(...values,1e-9);const pad=20*(window.devicePixelRatio||1);
  values.forEach((v,i)=>{const bw=(w-2*pad)/values.length*.72;const gap=(w-2*pad)/values.length;const bh=(h-2*pad)*(v/max);ctx.fillStyle=palette?.[i%palette.length]||`hsl(${i/values.length*300+175} 85% 63%)`;ctx.fillRect(pad+i*gap,h-pad-bh,bw,bh)})
}
function renderTelemetry(rows,a){
  const c=a.current,m=a.metrics;$("#mPing").textContent=fmt(c.ping,1);$("#mJitter").textContent=fmt(c.jitter,1);$("#mDown").textContent=fmt(c.download_speed,1);$("#mUp").textContent=fmt(c.upload_speed,1);
  $("#zPing").textContent=`z ${fmt(m.ping.robust_z,2)}`;$("#zJitter").textContent=`z ${fmt(m.jitter.robust_z,2)}`;$("#zDown").textContent=`z ${fmt(m.download_speed.robust_z,2)}`;$("#zUp").textContent=`z ${fmt(m.upload_speed.robust_z,2)}`;
  $("#mQEntropy").textContent=fmt(a.q16_entropy,3);$("#mSamples").textContent=a.sample_count;$("#mAnomalies").textContent=`${a.anomalies.length} anomalies`;
  const chron=[...rows].reverse();lineChart($("#telemetryCanvas"),[
    {values:chron.map(x=>+x.ping),color:"#41e7ff"},{values:chron.map(x=>+x.jitter),color:"#a875ff"},
    {values:chron.map(x=>+x.download_speed),color:"#54f1a8"},{values:chron.map(x=>+x.upload_speed),color:"#ffcc67"}]);
  bars($("#q16Canvas"),a.q16);$("#q16Readout").textContent=`Σp=${fmt(a.q16.reduce((x,y)=>x+y,0),6)} · entropy=${fmt(a.q16_entropy,4)} bits · dominant |${a.q16.indexOf(Math.max(...a.q16)).toString(2).padStart(4,"0")}>`;
  $("#anomalyList").innerHTML=a.anomalies.length?a.anomalies.map(x=>`<div class="list-item"><strong>${esc(x.metric)}</strong><span>value ${fmt(x.value)} · robust z ${fmt(x.robust_z,3)}</span></div>`).join(""):`<div class="list-item"><strong>No current robust outliers</strong><span>Threshold |z| ≥ 3.5</span></div>`
}
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
async function bootstrap(){
  const b=await api("/api/bootstrap");state.bootstrap=b;
  const t=await api("/api/telemetry?limit=300");state.telemetry=t.rows;renderTelemetry(t.rows,t.analytics);
  $("#memoryStatus").textContent=b.weaviate.ready?`${Object.values(b.weaviate.collections||{}).filter(Boolean).length} collections online`:`Offline · ${b.weaviate.error||"not ready"}`;
  $("#securityMini").innerHTML=Object.entries(b.security).map(([k,v])=>`<div class="list-item"><strong>${esc(k)}</strong><span>${esc(v)}</span></div>`).join("")
}
function renderCQSE(s){
  state.cqse=s;$("#palette").innerHTML=s.palette.map(x=>`<div class="swatch" style="background:${esc(x)}"><span>${esc(x)}</span></div>`).join("");
  heatmap($("#cq96Canvas"),s.chromatic_surface_96,8,12);bars($("#q32Canvas"),s.chromatic_quantum_32,s.palette);labPlot($("#labCanvas"),s.oklab,s.palette);
  $("#cqseRaw").textContent=JSON.stringify({base_source:s.base_source,semantic_entropy:s.semantic_entropy,q32_entropy:s.q32_entropy,rgb:s.rgb,xyz:s.xyz,oklab:s.oklab,oklch:s.oklch,notice:s.experimental_notice},null,2)
}
function heatmap(canvas,values,rows,cols){
  const {ctx,w,h}=resize(canvas);ctx.clearRect(0,0,w,h);const cw=w/cols,ch=h/rows;const max=Math.max(...values.map(Math.abs),1e-9);
  values.forEach((v,i)=>{const n=v/max;const hue=n>=0?190+55*n:315+35*(-n);const light=28+35*Math.abs(n);ctx.fillStyle=`hsl(${hue} 82% ${light}%)`;ctx.fillRect((i%cols)*cw,Math.floor(i/cols)*ch,cw+1,ch+1)})
}
function labPlot(canvas,labs,palette){
  const {ctx,w,h}=resize(canvas);ctx.clearRect(0,0,w,h);grid(ctx,w,h);if(!labs.length)return;
  const a=labs.map(x=>x[1]),b=labs.map(x=>x[2]);const amin=Math.min(...a,-.2),amax=Math.max(...a,.2),bmin=Math.min(...b,-.2),bmax=Math.max(...b,.2);
  labs.forEach((p,i)=>{const x=(p[1]-amin)/(amax-amin)*w*.8+w*.1,y=h-(p[2]-bmin)/(bmax-bmin)*h*.8-h*.1;ctx.fillStyle=palette[i%palette.length];ctx.beginPath();ctx.arc(x,y,11*(window.devicePixelRatio||1),0,Math.PI*2);ctx.fill();ctx.fillStyle="#fff";ctx.font=`${11*(window.devicePixelRatio||1)}px sans-serif`;ctx.fillText(`P${i+1}`,x+14,y)})
}
async function buildCQSE(){
  const r=await api("/api/cqse",{method:"POST",body:JSON.stringify({text:$("#cqseProbe").value,store:$("#cqseStore").checked})});renderCQSE(r.surface);toast("CQSE surface generated")
}
function project3(p,w,h,angle){
  const x=p.lab_a*3.8,y=p.lab_b*3.8,z=(p.lab_l-.5)*2.2;const ca=Math.cos(angle),sa=Math.sin(angle);const rx=x*ca-z*sa,rz=x*sa+z*ca;const scale=1/(2.8+rz*.35);return [w/2+rx*w*.32*scale,h/2-y*h*.38*scale-rz*h*.08*scale]
}
function drawWorldline(index){
  const f=state.hyperfield;if(!f)return;const c=$("#worldlineCanvas"),{ctx,w,h}=resize(c);ctx.clearRect(0,0,w,h);grid(ctx,w,h);const pts=f.points.slice(0,index+1);const angle=.55;
  ctx.lineWidth=2*(window.devicePixelRatio||1);ctx.strokeStyle="rgba(65,231,255,.5)";ctx.beginPath();
  pts.forEach((p,i)=>{const [x,y]=project3(p,w,h,angle);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)});ctx.stroke();
  pts.forEach((p,i)=>{const [x,y]=project3(p,w,h,angle);ctx.fillStyle=`hsl(${190+p.regime_id*47} 88% ${55+Math.min(p.change_score,4)*6}%)`;ctx.beginPath();ctx.arc(x,y,(3+Math.min(p.change_score,4)*1.5)*(window.devicePixelRatio||1),0,Math.PI*2);ctx.fill()})
}
function renderHyperfield(f){
  state.hyperfield=f;state.replay=f.points.length-1;$("#hPoints").textContent=f.point_count;$("#hRegimes").textContent=f.regimes.length;$("#hBoundaries").textContent=f.boundaries.length;$("#hQjs").textContent=fmt(f.points.reduce((a,p)=>a+p.quantum_js,0)/f.points.length,5);
  $("#worldlineSlider").max=Math.max(1,f.points.length-1);$("#worldlineSlider").value=f.points.length-1;drawWorldline(f.points.length-1);
  $("#regimeBody").innerHTML=f.regimes.map(r=>`<tr><td>R${r.regime_id}</td><td>${r.samples}</td><td>${fmt(r.mean_ping)}</td><td>${fmt(r.mean_jitter)}</td><td>${fmt(r.mean_download)}</td><td>${fmt(r.mean_quantum_entropy,4)}</td></tr>`).join("");
  $("#boundaryList").innerHTML=f.boundaries.length?f.boundaries.map(b=>`<div class="list-item"><strong>${esc(b.reason)} · R${b.from}→R${b.to}</strong><span>${esc(b.timestamp)} · change ${fmt(b.change_score,3)}</span></div>`).join(""):`<div class="list-item"><strong>No regime boundary above threshold</strong><span>Worldline remained comparatively stable.</span></div>`
}
async function buildHyperfield(){const f=await api("/api/hyperfield",{method:"POST",body:JSON.stringify({limit:300})});renderHyperfield(f);toast("Temporal hyperfield constructed")}
function replay(){
  if(!state.hyperfield)return;clearInterval(state.replayTimer);state.replay=0;$("#worldlineSlider").value=0;
  state.replayTimer=setInterval(()=>{drawWorldline(state.replay);$("#worldlineSlider").value=state.replay;if(++state.replay>=state.hyperfield.points.length)clearInterval(state.replayTimer)},65)
}
async function searchMemory(){
  const q=$("#memoryQuery").value.trim();if(!q)return;const r=await api("/api/memory/search",{method:"POST",body:JSON.stringify({query:q,limit:16})});
  $("#memoryResults").innerHTML=r.hits.length?r.hits.map(h=>{const p=h.properties||{},summary=p.summary||p.final_text||p.title||JSON.stringify(p);return `<article class="result-card glass"><span class="badge">${esc(h.collection)}</span><h3>score ${fmt(h.score,6)}</h3><p>${esc(String(summary).slice(0,500))}</p><div class="result-meta">${h.sources.map(s=>`<span class="pill">${esc(s.source)} #${s.rank}</span>`).join("")}</div></article>`}).join(""):`<div class="panel glass">No matching memories.</div>`
}
async function loadActions(){const r=await api("/api/actions");$("#actionsBody").innerHTML=r.actions.map(a=>`<tr><td>${esc(a.priority)}</td><td>${esc(a.risk)}</td><td><span class="badge">${esc(a.approval_state)}</span></td><td>${esc(a.title)}</td><td>${esc(a.description)}</td><td><button class="ghost actionState" data-id="${esc(a.action_id)}" data-state="approved">Approve</button> <button class="ghost actionState" data-id="${esc(a.action_id)}" data-state="rejected">Reject</button></td></tr>`).join("");$$(".actionState").forEach(b=>b.onclick=async()=>{await api(`/api/actions/${b.dataset.id}/${b.dataset.state}`,{method:"POST",body:"{}"});loadActions()})}
async function loadSystem(){const s=await api("/api/system");$("#systemRaw").textContent=JSON.stringify(s,null,2);const fields={CPU:`${fmt(s.cpu_percent,1)}%`,Memory:`${fmt(s.memory_percent,1)}%`,Disk:`${fmt(s.disk_percent,1)}%`,UID:s.container.uid,OpenAI:s.openai_configured?"configured":"not configured",Weaviate:s.weaviate_key_configured?"API key configured":"network-scoped"};$("#systemCards").innerHTML=Object.entries(fields).map(([k,v])=>`<div><strong>${esc(k)}</strong><span>${esc(v)}</span></div>`).join("")}

async function loadModels(){
  const m=await api("/api/models");
  $("#modelOpenAI").textContent=m.openai_embeddings.configured?m.openai_embeddings.model:"offline fallback";
  $("#modelFB").textContent=m.contriever.enabled?(m.contriever.dependencies_available?"ready":"dependencies missing"):"disabled";
  $("#modelGemma").textContent=m.gemma.verified?"SHA verified":(m.gemma.present?"SHA mismatch":"not installed");
  return m;
}
async function runAnalysis(){
  $("#analysisText").textContent="Running structured analysis…";
  const r=await api("/api/analyze",{method:"POST",body:JSON.stringify({mode:$("#analysisMode").value,limit:260})});
  $("#analysisModel").textContent=r.result.model||"configured model";
  const u=r.result.usage||{};
  $("#analysisUsage").textContent=`${u.total_tokens??"—"} tokens · ${r.memory_count} memories`;
  $("#analysisText").textContent=r.result.text||"No output text.";
  toast("Structured analysis complete");
}

function bind(){
  $$(".nav-item").forEach(b=>b.onclick=()=>{$$(".nav-item").forEach(x=>x.classList.remove("active"));b.classList.add("active");$$(".view").forEach(x=>x.classList.remove("active"));$(`#view-${b.dataset.view}`).classList.add("active");$("#viewTitle").textContent=b.querySelector("span").textContent;if(b.dataset.view==="actions")loadActions();if(b.dataset.view==="security")loadSystem()});
  $("#refreshBtn").onclick=()=>bootstrap().then(()=>toast("State refreshed")).catch(e=>toast(e.message,true));
  $("#setupMemoryBtn").onclick=()=>api("/api/memory/setup",{method:"POST",body:"{}"}).then(()=>toast("Memory collections ready")).catch(e=>toast(e.message,true));
  $("#buildCqseBtn").onclick=()=>buildCQSE().catch(e=>toast(e.message,true));
  $("#verifyModelsBtn").onclick=()=>loadModels().then(()=>toast("Model layer verified")).catch(e=>toast(e.message,true));
  $("#downloadGemmaBtn").onclick=()=>api("/api/models/gemma/download",{method:"POST",body:"{}"}).then(()=>loadModels()).then(()=>toast("Gemma downloaded and SHA verified")).catch(e=>toast(e.message,true));
  $("#runAnalysisBtn").onclick=()=>runAnalysis().catch(e=>{$("#analysisText").textContent=e.message;toast(e.message,true)});
  $("#buildHyperfieldBtn").onclick=()=>buildHyperfield().catch(e=>toast(e.message,true));
  $("#playWorldlineBtn").onclick=replay;$("#worldlineSlider").oninput=e=>drawWorldline(+e.target.value);
  $("#memorySearchBtn").onclick=()=>searchMemory().catch(e=>toast(e.message,true));
  $$(".datasetSimBtn").forEach(b=>b.onclick=async()=>{const card=b.closest(".dataset-card"),count=+card.querySelector("input").value;try{const r=await api("/api/datasets/simulate",{method:"POST",body:JSON.stringify({dataset_id:b.dataset.dataset,count})});card.querySelector(".dataset-result").textContent=`generated ${r.generated}; Weaviate ${r.weaviate_inserted}`;toast("Dataset simulation complete")}catch(e){toast(e.message,true)}});
  $("#createActionBtn").onclick=async()=>{try{await api("/api/actions",{method:"POST",body:JSON.stringify({title:$("#actionTitle").value,description:$("#actionDescription").value})});$("#actionTitle").value="";$("#actionDescription").value="";loadActions();toast("Action queued")}catch(e){toast(e.message,true)}};
  $("#refreshSystemBtn").onclick=()=>loadSystem().catch(e=>toast(e.message,true));
  window.addEventListener("resize",()=>{if(state.telemetry.length&&state.bootstrap)api("/api/telemetry?limit=300").then(t=>renderTelemetry(t.rows,t.analytics));if(state.cqse)renderCQSE(state.cqse);if(state.hyperfield)drawWorldline(state.replay??state.hyperfield.points.length-1)})
}
setInterval(()=>{$("#clock").textContent=new Date().toISOString().slice(11,19)},1000);
document.addEventListener("DOMContentLoaded",()=>{bind();Promise.all([bootstrap(),loadModels()]).catch(e=>toast(e.message,true))});
