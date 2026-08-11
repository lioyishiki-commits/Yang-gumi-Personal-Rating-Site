from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import daily_art
import database as db
import scoring
import seasonal_service as seasonal
import share_assets


ROOT = Path(__file__).resolve().parent
EXPORT_DIR = ROOT / "data" / "remote_share_site"


APP_CSS = r'''
:root{--bg:#101113;--panel:#191b1f;--raised:#22252a;--line:#34373d;--text:#f2f2f3;--muted:#999da6;--pink:#ef6c98;--cyan:#61cee9;--gold:#e7bd64}*{box-sizing:border-box}html{background:var(--bg);color:var(--text);font-family:"Microsoft YaHei UI","Noto Sans SC",sans-serif}body{margin:0;min-width:320px}.topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:22px;min-height:66px;padding:10px clamp(16px,3vw,44px);background:#0b0c0eeb;border-bottom:1px solid #292b30;backdrop-filter:blur(14px)}.brand{display:flex;flex-direction:column;min-width:176px}.brand b{font-size:18px}.brand b i{color:var(--pink);font-style:normal}.brand small{color:#747983;font-size:9px}.nav{display:flex;gap:4px;overflow-x:auto}.nav button,.cmd,.pager button{border:1px solid transparent;background:transparent;color:#b4b7bf;height:38px;padding:0 13px;cursor:pointer;font-weight:700}.nav button.active{color:#fff;border-color:#4a303b;background:#2b1b22}.nav button:hover,.cmd:hover,.pager button:hover:not(:disabled){color:#fff;border-color:#474a51}.live{margin-left:auto;color:#9fa3ac;font-size:12px;white-space:nowrap}.live i{display:inline-block;width:7px;height:7px;border-radius:50%;background:#55d58b;margin-right:7px}.app{max-width:1540px;margin:auto;padding:28px clamp(14px,3vw,42px) 72px}.page-head{display:flex;align-items:end;justify-content:space-between;margin-bottom:20px}.page-head p{margin:0 0 5px;color:var(--pink);font-size:11px;font-weight:800}.page-head h1{font-size:30px;margin:0}.page-head span{color:var(--muted);font-size:13px}.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border-block:1px solid var(--line);margin-bottom:26px}.stat{padding:18px 20px;border-right:1px solid var(--line)}.stat:last-child{border:0}.stat small{display:block;color:var(--muted)}.stat b{display:block;font-size:25px;margin-top:3px}.home-split{display:grid;grid-template-columns:minmax(270px,.78fr) minmax(0,1.22fr);gap:20px;margin-bottom:26px}.profile{padding:22px 0}.profile h2{font-size:25px;margin:5px 0}.profile p{color:var(--muted);max-width:48ch}.art-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.art-grid figure{margin:0;aspect-ratio:2/3;background:#050506;overflow:hidden;border:1px solid #303238}.art-grid img{width:100%;height:100%;object-fit:cover;display:block}.section-head{display:flex;justify-content:space-between;align-items:center;margin:26px 0 12px}.section-head h2{font-size:19px;margin:0}.section-head span{color:var(--muted);font-size:12px}.season-strip{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;overflow:hidden}.season-item{position:relative;min-width:0;aspect-ratio:2/3;background:#17191d;overflow:hidden;border:1px solid #30333a}.season-item img{width:100%;height:100%;object-fit:cover;display:block}.season-item div{position:absolute;inset:auto 0 0;padding:24px 9px 8px;background:#111d;color:#fff;font-size:12px;font-weight:700}.controls{display:grid;grid-template-columns:2fr repeat(3,1fr);gap:10px;margin-bottom:16px}.controls label{display:grid;gap:5px;color:var(--muted);font-size:11px}.controls input,.controls select{height:42px;padding:0 11px;border:1px solid var(--line);background:#121316;color:var(--text);outline:none}.controls input:focus,.controls select:focus{border-color:var(--pink)}.resultbar{display:flex;align-items:center;justify-content:space-between;margin:14px 0;color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.card{display:grid;grid-template-columns:minmax(108px,37%) minmax(0,1fr);min-height:244px;background:var(--panel);border:1px solid var(--line);overflow:hidden;cursor:pointer;text-align:left;color:inherit;padding:0}.card:hover{border-color:#5c4650;background:#1d1f23}.cover{position:relative;background:#090a0b;min-height:100%}.cover img{display:block;width:100%;height:100%;object-fit:cover}.fallback{height:100%;display:grid;place-items:center;color:#555a64;font-size:34px;font-weight:900}.badge{position:absolute;left:7px;top:7px;padding:3px 7px;background:#111d;border:1px solid #ffffff24;font-size:10px}.card-body{padding:14px;min-width:0}.title{font-size:17px;font-weight:800;line-height:1.35}.original{height:20px;color:#858994;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.scores{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:12px 0}.score{padding:7px;background:#121418}.score small{display:block;color:#777c86;font-size:9px}.score b{font-size:15px}.score.mine b{color:#ff8aac}.score.bgm b{color:var(--cyan)}.meta,.review{color:#9ca0aa;font-size:11px}.review{margin-top:9px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.tags{display:flex;gap:5px;flex-wrap:wrap;margin-top:9px}.tag{font-size:10px;border:1px solid #3a3d44;color:#b6bac3;padding:2px 6px}.pager{display:flex;align-items:center;justify-content:center;gap:10px;margin:22px 0}.pager span{min-width:130px;text-align:center;color:var(--muted);font-size:12px}.pager button:disabled{opacity:.3;cursor:not-allowed}.rank-list{display:grid;gap:7px}.rank-row{display:grid;grid-template-columns:55px 72px minmax(0,1fr) 110px 110px;align-items:center;gap:12px;border-bottom:1px solid var(--line);padding:9px 4px;cursor:pointer}.rank-row:hover{background:#17191d}.rank-row>img{width:58px;height:78px;object-fit:cover}.rank-no{font-size:20px;color:#777}.rank-score{font-size:21px;font-weight:900;color:var(--pink)}.tag-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.tag-card{padding:16px;border:1px solid var(--line);background:var(--panel);color:inherit;text-align:left;cursor:pointer}.tag-card:hover{border-color:var(--pink)}.tag-card b{display:block;font-size:17px}.tag-card span{color:var(--muted);font-size:12px}.score-table{width:100%;border-collapse:collapse}.score-table th,.score-table td{padding:12px;border-bottom:1px solid var(--line);text-align:left}.score-table th{color:var(--muted);font-size:11px}.modal[hidden]{display:none}.modal{position:fixed;inset:0;z-index:40;background:#000b;display:grid;place-items:center;padding:20px}.dialog{width:min(980px,96vw);max-height:92vh;overflow:auto;background:#16181b;border:1px solid #4a4d54;display:grid;grid-template-columns:minmax(220px,32%) minmax(0,1fr);position:relative}.dialog-cover{min-height:520px;background:#090a0b}.dialog-cover img{width:100%;height:100%;object-fit:cover;display:block}.dialog-body{padding:30px}.dialog-body h2{font-size:28px;margin:4px 0}.dialog-body>small{color:var(--muted)}.dialog-body p{color:#b5b8c0}.close{position:absolute;right:12px;top:12px;width:38px;height:38px;border:1px solid #555;background:#111d;color:#fff;font-size:22px;cursor:pointer}.empty{padding:70px;text-align:center;color:#747983}.fatal{margin:60px auto;max-width:720px;padding:30px;border:1px solid #713647;background:#27171d}.skeleton{min-height:70vh;background:#15171a}@media(max-width:1080px){.grid{grid-template-columns:repeat(2,1fr)}.season-strip{grid-template-columns:repeat(4,minmax(150px,1fr));overflow-x:auto}.tag-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:760px){.topbar{align-items:flex-start;flex-wrap:wrap;gap:7px}.brand{min-width:140px}.nav{order:3;width:100%}.live{margin-left:auto}.app{padding-top:18px}.stats{grid-template-columns:repeat(2,1fr)}.stat{border-bottom:1px solid var(--line)}.home-split{grid-template-columns:1fr}.controls{grid-template-columns:1fr 1fr}.controls label:first-child{grid-column:1/-1}.grid{grid-template-columns:1fr}.rank-row{grid-template-columns:42px 58px minmax(0,1fr) 76px}.rank-row .public{display:none}.tag-grid{grid-template-columns:repeat(2,1fr)}.dialog{grid-template-columns:1fr}.dialog-cover{min-height:0;aspect-ratio:3/2}.dialog-cover img{object-position:center 25%}.season-strip{display:flex}.season-item{min-width:145px}.page-head span{display:none}}@media(max-width:430px){.art-grid{gap:5px}.card{grid-template-columns:112px minmax(0,1fr)}.scores{grid-template-columns:1fr 1fr}.score.diff{display:none}.tag-grid{grid-template-columns:1fr 1fr}.controls{grid-template-columns:1fr}.controls label:first-child{grid-column:auto}}
'''


APP_JS = r'''
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let data=window.__YANGGUMI_DATA__, page="首页", listPage=1, pageSize=12, filters={q:"",type:"",status:"",sort:"updated"};
const revisionKey=value=>JSON.stringify(value??null);
if(location.search)history.replaceState(null,"",location.pathname+location.hash);
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const num=v=>v===null||v===undefined||v===""?null:Number(v), fmt=v=>num(v)===null?"—":num(v).toFixed(2), fmtMine=w=>num(w.score_total)===null?"—":num(w.score_total).toFixed(w.score_mode==="manual"?1:2);
const image=w=>w.cover_url||w.bangumi_image_url||"";
const picture=(src,alt="",eager=false)=>src?`<img loading="${eager?'eager':'lazy'}" decoding="async" src="${esc(src)}" alt="${esc(alt)}">`:"";
function warmImages(urls,delay=0){let schedule=()=>{let warm=()=>[...new Set(urls.filter(Boolean))].forEach(src=>{let img=new Image();img.decoding="async";img.src=src});(window.requestIdleCallback||((fn)=>setTimeout(fn,500)))(warm,{timeout:2500})};if(delay)setTimeout(schedule,delay);else schedule()}
function warmAfterVisible(urls){let started=Date.now(),check=()=>{let visible=[...document.images].filter(img=>{let rect=img.getBoundingClientRect();return rect.top<innerHeight&&rect.bottom>0});if(!visible.length||visible.every(img=>img.complete&&img.naturalWidth>1)||Date.now()-started>15000){warmImages(urls);return}setTimeout(check,200)};check()}
function head(kicker,title,aside=""){return `<header class="page-head"><div><p>${esc(kicker)}</p><h1>${esc(title)}</h1></div><span>${esc(aside)}</span></header>`}
function stat(label,value){return `<div class="stat"><small>${esc(label)}</small><b>${esc(value)}</b></div>`}
function stats(){let scores=data.works.map(w=>num(w.score_total)).filter(v=>v!==null);return `<section class="stats">${stat("收藏",data.works.length)}${stat("已看",data.works.filter(w=>w.status==="已看").length)}${stat("在看",data.works.filter(w=>["在看","重看中"].includes(w.status)).length)}${stat("我的均分",scores.length?(scores.reduce((a,b)=>a+b,0)/scores.length).toFixed(2):"—")}${stat("同步",data.export_meta.exported_at.slice(11,19))}</section>`}
function scoreCells(w){return `<div class="scores"><div class="score mine"><small>MY</small><b>${fmtMine(w)}</b></div><div class="score bgm"><small>BGM</small><b>${fmt(w.bangumi_score)}</b></div><div class="score diff"><small>DIFF</small><b>${num(w.score_diff)===null?"—":(num(w.score_diff)>=0?"+":"")+num(w.score_diff).toFixed(2)}</b></div></div>`}
function card(w,i=0){let src=image(w),tags=(w.bangumi_tags||[]).slice(0,4).map(t=>`<span class="tag">${esc(t)}</span>`).join("");let pic=src?`${picture(src,w.title,i<4)}<span class="badge">${esc(w.status||"未分类")}</span>`:`<div class="fallback">YG</div>`;return `<button class="card" data-work="${w.id}"><div class="cover">${pic}</div><div class="card-body"><div class="title">${esc(w.title||"未命名")}</div><div class="original">${esc(w.original_title||"")}</div>${scoreCells(w)}<div class="meta">${esc(w.type||"")} · ${esc(w.subtype||"")} · ${esc(w.year||"年份未知")}</div>${w.short_review?`<div class="review">${esc(w.short_review)}</div>`:""}<div class="tags">${tags}</div></div></button>`}
function filteredWorks(){let q=filters.q.trim().toLocaleLowerCase();let rows=data.works.filter(w=>(!filters.type||w.type===filters.type)&&(!filters.status||w.status===filters.status)&&(!q||[w.title,w.original_title,...(w.bangumi_tags||[])].join(" ").toLocaleLowerCase().includes(q)));let key=filters.sort;rows.sort((a,b)=>key==="score"?(num(b.score_total)??-1)-(num(a.score_total)??-1):key==="bangumi"?(num(b.bangumi_score)??-1)-(num(a.bangumi_score)??-1):key==="year"?(num(b.year)??0)-(num(a.year)??0):key==="title"?String(a.title).localeCompare(String(b.title),"zh-CN"):String(b.updated_at||"").localeCompare(String(a.updated_at||"")));return rows}
function options(field){return [...new Set(data.works.map(w=>w[field]).filter(Boolean))].sort((a,b)=>String(a).localeCompare(String(b),"zh-CN")).map(v=>`<option ${filters[field]===v?"selected":""}>${esc(v)}</option>`).join("")}
function controls(){return `<section class="controls"><label>搜索<input id="q" value="${esc(filters.q)}" placeholder="中文名、原名或标签"></label><label>类型<select id="type"><option value="">全部</option>${options("type")}</select></label><label>状态<select id="status"><option value="">全部</option>${options("status")}</select></label><label>排序<select id="sort"><option value="updated">最近更新</option><option value="score">我的评分</option><option value="bangumi">Bangumi</option><option value="year">年份</option><option value="title">标题</option></select></label></section>`}
function pager(total){let pages=Math.max(1,Math.ceil(total/pageSize));listPage=Math.min(Math.max(1,listPage),pages);return `<nav class="pager"><button data-move="-1" ${listPage<=1?"disabled":""} title="上一页">←</button><span>第 ${listPage} / ${pages} 页 · 共 ${total}</span><button data-move="1" ${listPage>=pages?"disabled":""} title="下一页">→</button></nav>`}
function warmNext(rows){let start=listPage*pageSize;warmAfterVisible(rows.slice(start,start+pageSize).map(image))}
function renderHome(){let arts=data.daily_art.filter(x=>x.type==="portrait").slice(0,3),season=data.seasonal.slice(0,6),recent=[...data.works].sort((a,b)=>String(b.updated_at||"").localeCompare(String(a.updated_at||""))).slice(0,6);return `${head("YANG-GUMI / HOME","我的私人档案",data.export_meta.exported_at)}${stats()}<section class="home-split"><div class="profile"><p>PERSONAL ACGN ARCHIVE</p><h2>此刻的观看与余韵</h2><p>${data.works.length} 部作品构成的本地档案。</p></div><div class="art-grid">${arts.map(a=>`<figure>${picture(a.src,"",true)}</figure>`).join("")}</div></section><div class="section-head"><h2>本季新番</h2><span>${esc(data.season_label)}</span></div><section class="season-strip">${season.map(s=>`<article class="season-item">${picture(s.image,s.title)}<div>${esc(s.title)}</div></article>`).join("")}</section><div class="section-head"><h2>最近更新</h2><span>${recent.length} 部</span></div><section class="grid">${recent.map(card).join("")}</section>`}
function renderLibrary(){let rows=filteredWorks(),start=(listPage-1)*pageSize,shown=rows.slice(start,start+pageSize);setTimeout(()=>warmNext(rows),0);return `${head("ARCHIVE","条目库",`${rows.length} / ${data.works.length}`)}${controls()}<div class="resultbar"><b>${rows.length} 部作品</b><span>每页 ${pageSize}</span></div>${pager(rows.length)}<section class="grid">${shown.map(card).join("")}</section>${pager(rows.length)}`}
function rankingRows(rows){return `<section class="rank-list">${rows.map((w,i)=>`<article class="rank-row" data-work="${w.id}"><b class="rank-no">#${String((listPage-1)*pageSize+i+1).padStart(2,"0")}</b>${image(w)?picture(image(w),w.title):`<div></div>`}<div><b>${esc(w.title)}</b><div class="original">${esc(w.original_title||"")}</div></div><b class="rank-score">${fmtMine(w)}</b><b class="public">BGM ${fmt(w.bangumi_score)}</b></article>`).join("")}</section>`}
function renderRank(){let rows=[...data.works].filter(w=>num(w.score_total)!==null).sort((a,b)=>num(b.score_total)-num(a.score_total)),start=(listPage-1)*pageSize;setTimeout(()=>warmNext(rows),0);return `${head("RANKING","排行榜",`${rows.length} 部已评分`)}${pager(rows.length)}${rankingRows(rows.slice(start,start+pageSize))}${pager(rows.length)}`}
function renderCompare(){let rows=[...data.works].filter(w=>num(w.score_diff)!==null).sort((a,b)=>Math.abs(num(b.score_diff))-Math.abs(num(a.score_diff))),start=(listPage-1)*pageSize,diffs=rows.map(w=>num(w.score_diff));return `${head("MY SCORE / BANGUMI","评分对比",`${rows.length} 部可比较`)}<section class="stats">${stat("我高于 Bangumi",diffs.filter(x=>x>.5).length)}${stat("我低于 Bangumi",diffs.filter(x=>x<-.5).length)}${stat("基本一致",diffs.filter(x=>Math.abs(x)<=.5).length)}${stat("平均差",diffs.length?(diffs.reduce((a,b)=>a+b,0)/diffs.length).toFixed(2):"—")}${stat("最大差",diffs.length?Math.max(...diffs.map(Math.abs)).toFixed(2):"—")}</section>${pager(rows.length)}<section class="grid">${rows.slice(start,start+pageSize).map(card).join("")}</section>${pager(rows.length)}`}
function tagRows(){let map=new Map();data.works.forEach(w=>(w.bangumi_tags||[]).forEach(t=>{let row=map.get(t)||{name:t,works:[],sum:0,count:0};row.works.push(w);if(num(w.score_total)!==null){row.sum+=num(w.score_total);row.count++}map.set(t,row)}));return [...map.values()].sort((a,b)=>b.works.length-a.works.length)}
function renderTags(){let rows=tagRows();return `${head("TAG ARCHIVE","标签筛选",`${rows.length} 个标签`)}<section class="tag-grid">${rows.map(t=>`<button class="tag-card" data-tag="${esc(t.name)}"><b>#${esc(t.name)}</b><span>${t.works.length} 部 · MY ${t.count?(t.sum/t.count).toFixed(2):"—"}</span></button>`).join("")}</section>`}
function renderScoring(){let fields=data.score_labels,rows=Object.entries(fields).map(([key,label])=>{let vals=data.works.map(w=>num(w[key])).filter(v=>v!==null);return `<tr><td>${esc(label)}</td><td>${vals.length}</td><td>${vals.length?(vals.reduce((a,b)=>a+b,0)/vals.length).toFixed(2):"—"}</td></tr>`}).join("");return `${head("SCORING SYSTEM","评分设置","只读")}<table class="score-table"><thead><tr><th>维度</th><th>已评分</th><th>平均值</th></tr></thead><tbody>${rows}</tbody></table>`}
function render(){try{let html=page==="首页"?renderHome():page==="条目库"?renderLibrary():page==="排行榜"?renderRank():page==="评分对比"?renderCompare():page==="标签筛选"?renderTags():renderScoring();$("#app").innerHTML=html;$$('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));bindPage();if(page==="首页")warmAfterVisible(filteredWorks().slice(0,pageSize).map(image))}catch(e){$("#app").innerHTML=`<div class="fatal">页面载入失败：${esc(e.message)}</div>`}}
function bindPage(){let q=$("#q"),type=$("#type"),status=$("#status"),sort=$("#sort");if(q)q.oninput=e=>{filters.q=e.target.value;listPage=1;render()};if(type){type.value=filters.type;type.onchange=e=>{filters.type=e.target.value;listPage=1;render()}}if(status){status.value=filters.status;status.onchange=e=>{filters.status=e.target.value;listPage=1;render()}}if(sort){sort.value=filters.sort;sort.onchange=e=>{filters.sort=e.target.value;listPage=1;render()}}}
function openDetail(id){let w=data.works.find(x=>String(x.id)===String(id));if(!w)return;let src=image(w),tags=(w.bangumi_tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join("");$("#dialog").innerHTML=`<button class="close" aria-label="关闭">×</button><div class="dialog-cover">${src?picture(src,w.title,true):`<div class="fallback">YG</div>`}</div><div class="dialog-body"><small>${esc(w.original_title||"")}</small><h2>${esc(w.title)}</h2>${scoreCells(w)}<p>${esc(w.bangumi_summary||"")}</p>${w.short_review?`<h3>短评</h3><p>${esc(w.short_review)}</p>`:""}<div class="tags">${tags}</div></div>`;$("#modal").hidden=false;document.body.style.overflow="hidden"}
document.addEventListener("click",e=>{let nav=e.target.closest("[data-page]");if(nav){page=nav.dataset.page;listPage=1;render();scrollTo(0,0);return}let move=e.target.closest("[data-move]");if(move&&!move.disabled){listPage+=Number(move.dataset.move);render();scrollTo(0,0);return}let work=e.target.closest("[data-work]");if(work){openDetail(work.dataset.work);return}let tag=e.target.closest("[data-tag]");if(tag){filters.q=tag.dataset.tag;page="条目库";listPage=1;render();scrollTo(0,0);return}if(e.target.closest(".close")||e.target.id==="modal"){$("#modal").hidden=true;document.body.style.overflow=""}});
setInterval(async()=>{if(document.hidden)return;try{let r=await fetch("/revision.json",{cache:"no-store"});if(!r.ok)return;let v=await r.json();if(revisionKey(v.revision)!==revisionKey(data.revision)){let s=await fetch("/snapshot.json",{cache:"no-store"});if(!s.ok)return;data=await s.json();render()}}catch(e){}},15000);
render();
'''


INDEX_TEMPLATE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><meta name="referrer" content="no-referrer"><title>Yang-gumi · 只读分享</title>__PRELOADS__<style>__CSS__</style></head><body><header class="topbar"><div class="brand"><b>YANG<i>·</i>GUMI</b><small>PERSONAL ACGN ARCHIVE</small></div><nav class="nav" aria-label="页面"><button data-page="首页">首页</button><button data-page="条目库">条目库</button><button data-page="排行榜">排行榜</button><button data-page="评分对比">评分对比</button><button data-page="标签筛选">标签筛选</button><button data-page="评分设置">评分设置</button></nav><div class="live"><i></i>实时只读</div></header><main id="app" class="app skeleton"></main><div id="modal" class="modal" hidden><article id="dialog" class="dialog"></article></div><script>window.__YANGGUMI_DATA__=__DATA__;</script><script>__JS__</script></body></html>'''


def _public_payload() -> dict[str, Any]:
    payload = json.loads(db.export_json(public=True).decode("utf-8"))
    private_rows = {int(work["id"]): work for work in db.list_works()}
    for work in payload.get("works", []):
        private = private_rows.get(int(work["id"]))
        if private:
            work["cover_url"] = share_assets.work_cover_url(private)

    manifest = daily_art.load_manifest()
    payload["daily_art"] = [
        {
            "src": f"/app/static/{item['asset']}",
            "type": item.get("type"),
            "key": item.get("key"),
            "focus": item.get("focus"),
        }
        for item in manifest.get("items", [])
        if item.get("asset")
    ]

    current = seasonal.current_season()
    seasonal_rows = db.list_seasonal_anime(
        current["year"], current["season_code"], include_unconfirmed=False
    )
    visible_season = []
    for item in seasonal_rows:
        if not seasonal.is_homepage_seasonal_anime(item):
            continue
        source = seasonal.seasonal_poster_static_url(
            current["year"], current["season_code"], int(item["bangumi_id"])
        ) or item.get("image_url") or ""
        visible_season.append({
            "id": item.get("bangumi_id"),
            "title": item.get("title") or item.get("original_title") or "未命名动画",
            "image": share_assets.seasonal_poster_url(
                source, key=f"current-{current['year']}-{current['season_code']}-{item['bangumi_id']}"
            ),
            "score": item.get("bangumi_score"),
        })
    visible_season.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    payload["seasonal"] = visible_season
    payload["season_label"] = f"{current['year']} · {current['month_label']}"
    config = scoring.load_score_config()
    payload["score_labels"] = {
        field: str(config.get(group, {}).get("labels", {}).get(field) or field)
        for group in ("body", "feeling", "era")
        for field in config.get(group, {}).get("weights", {})
    }
    visible_fields = {
        "id", "title", "original_title", "type", "subtype", "status", "year",
        "score_total", "score_mode", "bangumi_score", "short_review", "bangumi_image_url",
        "bangumi_summary", "updated_at", "score_diff", "cover_url", "bangumi_tags",
        *payload["score_labels"].keys(),
    }
    payload["works"] = [
        {key: value for key, value in work.items() if key in visible_fields}
        for work in payload.get("works", [])
    ]
    payload["revision"] = share_assets.source_revision()
    payload["export_meta"]["exported_at"] = datetime.now().isoformat(timespec="seconds")
    return payload


def build_public_site(destination: Path = EXPORT_DIR) -> Path:
    """Build an atomic, privacy-safe browser-side share snapshot."""
    destination = Path(destination).resolve()
    temporary = destination.with_name(destination.name + ".building")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True, exist_ok=True)
    payload = _public_payload()
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    critical_images = [
        item["src"] for item in payload.get("daily_art", [])
        if item.get("type") == "portrait" and item.get("src")
    ][:3]
    preloads = "".join(
        f'<link rel="preload" as="image" href="{source}" fetchpriority="high">'
        for source in critical_images
    )
    html = (
        INDEX_TEMPLATE.replace("__PRELOADS__", preloads).replace("__CSS__", APP_CSS)
        .replace("__DATA__", serialized).replace("__JS__", APP_JS)
    )
    (temporary / "index.html").write_text(html, encoding="utf-8", newline="\n")
    (temporary / "snapshot.json").write_text(serialized, encoding="utf-8", newline="\n")
    backup = destination.with_name(destination.name + ".previous")
    shutil.rmtree(backup, ignore_errors=True)
    if destination.exists():
        destination.replace(backup)
    temporary.replace(destination)
    shutil.rmtree(backup, ignore_errors=True)
    return destination


if __name__ == "__main__":
    print(build_public_site())
