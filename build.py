#!/usr/bin/env python3
"""Builds index.html: SPEC.md embedded verbatim, rendered in the browser by marked."""
import html, pathlib
root = pathlib.Path(__file__).parent
md = (root / "SPEC.md").read_text()
page = r'''<title>TC Compiler Reference</title>
<meta name="description" content="TC: TornadoVM's Graal-free compiler. IR, intrinsics, pipeline, passes, ABI, diagnostics and sources.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root{--bg:#FAFAF8;--surface:#FFFFFF;--ink:#1C2229;--muted:#5C6570;--line:#DDE1E3;--code:#F0F2F1;--accent:#0E6A70;--accent-soft:#E2F0F0;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121619;--surface:#171C20;--ink:#E1E6EA;--muted:#9AA4AD;--line:#29313A;--code:#0E1215;--accent:#6CC6CB;--accent-soft:#16302F;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121619;--surface:#171C20;--ink:#E1E6EA;--muted:#9AA4AD;--line:#29313A;--code:#0E1215;--accent:#6CC6CB;--accent-soft:#16302F;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font:15.5px/1.62 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;padding:0 20px}
.wrap{max-width:1240px;margin:0 auto;padding-block:32px 72px;display:grid;grid-template-columns:260px minmax(0,1fr);gap:48px}
nav{position:sticky;top:calc(env(safe-area-inset-top,0px) + 16px);align-self:start;max-height:calc(100vh - 32px);overflow:auto;font-size:13.5px;padding-right:8px}
nav .brand{font:600 15px "Source Serif 4",Georgia,serif;color:var(--ink);margin-bottom:10px;display:block}
nav ol{list-style:none;margin:0;padding:0}
nav li{margin:2px 0}
nav li.l3{padding-left:14px;font-size:12.5px}
nav a{color:var(--muted);text-decoration:none;display:block;padding:2px 6px;border-radius:4px}
nav a:hover,nav a:focus-visible{color:var(--ink);background:var(--accent-soft);outline:none}
main{min-width:0;max-width:86ch}
main h1{font:600 34px/1.15 "Source Serif 4",Georgia,serif;margin:0 0 10px;text-wrap:balance}
main h2{font:600 24px/1.25 "Source Serif 4",Georgia,serif;margin:44px 0 10px;padding-top:12px;border-top:1px solid var(--line);text-wrap:balance}
main h3{font:600 17.5px/1.3 "IBM Plex Sans",sans-serif;margin:28px 0 6px}
main p,main li{max-width:78ch}
main a{color:var(--accent)}
main hr{display:none}
code{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:.86em;background:var(--code);padding:1px 4px;border-radius:4px}
pre{background:var(--code);border:1px solid var(--line);border-radius:6px;padding:12px 14px;overflow-x:auto;font:12.8px/1.55 "JetBrains Mono",ui-monospace,monospace}
pre code{background:none;padding:0;font-size:inherit}
.table{overflow-x:auto;margin:12px 0;border:1px solid var(--line);border-radius:6px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:13.8px}
th,td{text-align:left;vertical-align:top;padding:7px 10px;border-bottom:1px solid var(--line)}
th{font-weight:600;font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);background:var(--code)}
tr:last-child td{border-bottom:none}
td:first-child{white-space:nowrap}
.lede{color:var(--muted)}
#fallback{white-space:pre-wrap}
@media (max-width:860px){.wrap{grid-template-columns:1fr;gap:12px}nav{position:static;max-height:none;border-bottom:1px solid var(--line);padding-bottom:12px}nav li.l3{display:none}}
</style>
<div class="wrap">
<nav aria-label="Contents"><span class="brand">TC Reference</span><ol id="toc"></ol></nav>
<main id="doc"><pre id="fallback"></pre></main>
</div>
<script type="text/plain" id="src">''' + md.replace("</", "<\\/") + r'''</script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<script>
(function(){
  var src=document.getElementById('src').textContent.replace(/<\\\//g,'</');
  var doc=document.getElementById('doc');
  if(!window.marked){document.getElementById('fallback').textContent=src;return;}
  doc.innerHTML=marked.parse(src,{gfm:true});
  doc.querySelectorAll('table').forEach(function(t){var w=document.createElement('div');w.className='table';t.parentNode.insertBefore(w,t);w.appendChild(t);});
  var p=doc.querySelector('h1 + p'); if(p) p.className='lede';
  var toc=document.getElementById('toc'), used={};
  doc.querySelectorAll('h2,h3').forEach(function(h){
    var id=h.textContent.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');
    while(used[id]) id+='-'; used[id]=1; h.id=id;
    var li=document.createElement('li'); li.className=h.tagName==='H3'?'l3':'l2';
    var a=document.createElement('a'); a.href='#'+id; a.textContent=h.textContent; li.appendChild(a); toc.appendChild(li);
  });
  if(location.hash){var t=document.getElementById(location.hash.slice(1)); if(t) t.scrollIntoView();}
})();
</script>
'''
(root / "index.html").write_text(page)
print("index.html", len(page))
