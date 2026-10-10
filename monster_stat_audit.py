#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""List every map monster whose stats differ from its archetype.

A map object only needs to write the attributes that differ from its
archetype, so any combat stat present on a monster in a map file with a value
other than the archetype's is a deliberate (or accidental) alteration.  This
script walks the whole map tree, resolves each monster against the archetype
tree, and writes a searchable, sortable HTML table with one row per altered
monster: map name, map path, region, x,y location and the altered stats
(shown as archetype value -> map value).

Monsters carried in another object's inventory (spawn points, containers,
other monsters) are included; their location is that of the container.

Compared stats: level, exp, hp/maxhp, sp/maxsp, grace/maxgrace, dam, wc, ac,
speed, the seven character stats, attacktype, every resist_* value and the
spell path masks.  Names, faces, flags and carried items are not compared.

Defaults: maps and arch checkouts under ~/Documents/cf-devel, output index.html
next to this script.

Usage: monster_stat_audit.py [-o out.html] [-m mapdir] [-a archdir]
                             [--json out.json]
"""

import argparse
import json
import os
import sys

# Directories below the maps root that hold no playable maps.
SKIP_DIRS = {".git", "editor", "python", "Info", "styles", "templates",
             "test", "animations"}

# Numeric stats compared directly.
STATS = ("level", "exp", "hp", "maxhp", "sp", "maxsp", "grace", "maxgrace",
         "dam", "wc", "ac", "speed", "Str", "Dex", "Con", "Int", "Wis",
         "Pow", "Cha", "attacktype", "path_attuned", "path_repelled",
         "path_denied")
STAT_PREFIXES = ("resist_",)


def is_stat(key):
    return key in STATS or key.startswith(STAT_PREFIXES)


# Multi-line blocks, as start -> end keyword.
BLOCKS = {"msg": "endmsg", "lore": "endlore", "maplore": "endmaplore",
          "anim": "mina"}

# Fields kept besides the stats.
KEEP = {"monster", "name", "x", "y", "type"}


def keep(key):
    return key in KEEP or is_stat(key)


def load_archetypes(archdir):
    """Return {archname: {field: value}} (head attributes only)."""
    arches = {}
    for root, dirs, files in os.walk(archdir):
        dirs[:] = [d for d in dirs if d != ".git"]
        for fn in files:
            if fn.endswith(".arc"):
                parse_arc(os.path.join(root, fn), arches)
    return arches


def parse_arc(path, arches):
    cur = None
    depth = 0          # nesting of inventory objects inside cur
    endblock = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip()
            if endblock is not None:
                if line == endblock:
                    endblock = None
                continue
            if line in BLOCKS:
                endblock = BLOCKS[line]
                continue
            if line.startswith("Object "):
                cur = {}
                depth = 0
                arches[line[7:].strip()] = cur
                continue
            if cur is None:
                continue
            if line.startswith("arch "):
                depth += 1
                continue
            if line == "end":
                if depth:
                    depth -= 1
                else:
                    cur = None
                continue
            if depth or not line or line.startswith("#"):
                continue
            key, _, val = line.partition(" ")
            if keep(key):
                cur[key] = val.strip()


def parse_map(path):
    """Return (header, objects) or None.  objects: flat list of dicts, each
    with 'arch', its attributes, and 'parent' (container arch or None)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        if f.readline().rstrip() != "arch map":
            return None
        header = {}
        endblock = None
        for line in f:
            line = line.rstrip()
            if endblock is not None:
                if line == endblock:
                    endblock = None
                continue
            if line in BLOCKS:
                endblock = BLOCKS[line]
                continue
            if line == "end":
                break
            key, _, val = line.partition(" ")
            header[key] = val.strip()

        objects = []
        stack = []
        for line in f:
            line = line.rstrip()
            if endblock is not None:
                if line == endblock:
                    endblock = None
                continue
            if line in BLOCKS:
                endblock = BLOCKS[line]
                continue
            if line.startswith("arch "):
                stack.append({"arch": line[5:].strip(),
                              "_stack": [o["arch"] for o in stack],
                              "_root": stack[0] if stack else None})
                continue
            if line == "end":
                if stack:
                    obj = stack.pop()
                    objects.append(obj)
                continue
            if not stack:
                continue
            key, _, val = line.partition(" ")
            if keep(key):
                stack[-1][key] = val.strip()
    return header, objects


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def differs(a, b):
    na, nb = num(a), num(b)
    if na is not None and nb is not None:
        return abs(na - nb) > 1e-9
    return a != b


def fmt(v):
    n = num(v)
    if n is None:
        return str(v)
    return str(int(n)) if n == int(n) else ("%g" % n)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    base = os.path.expanduser("~/Documents/cf-devel")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-m", "--maps",
                    default=os.path.join(base, "crossfire-crossfire-maps"))
    ap.add_argument("-a", "--arch",
                    default=os.path.join(base, "crossfire-crossfire-arch"))
    ap.add_argument("-o", "--out", default=os.path.join(here, "index.html"))
    ap.add_argument("--json")
    args = ap.parse_args()

    arches = load_archetypes(args.arch)
    print("archetypes: %d" % len(arches), file=sys.stderr)

    # region short name -> long name
    regions = {}
    reg = os.path.join(args.maps, "regions.reg")
    if os.path.exists(reg):
        cur = None
        with open(reg, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip()
                if line.startswith("region "):
                    cur = line[7:].strip()
                elif line.startswith("longname ") and cur:
                    regions[cur] = line[9:].strip()

    rows = []
    nmaps = nmon = 0
    unknown = {}
    for dp, dirs, files in os.walk(args.maps):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for fn in sorted(files):
            full = os.path.join(dp, fn)
            if fn.endswith((".png", ".md", ".html", ".py", ".sh", ".txt")):
                continue
            try:
                res = parse_map(full)
            except OSError:
                continue
            if not res:
                continue
            header, objs = res
            nmaps += 1
            mpath = "/" + os.path.relpath(full, args.maps).replace(os.sep, "/")
            for o in objs:
                arch = arches.get(o["arch"])
                if arch is None:
                    unknown[o["arch"]] = unknown.get(o["arch"], 0) + 1
                    continue
                if (o.get("monster") or arch.get("monster", "0")) in ("0", ""):
                    continue
                nmon += 1
                changes = []
                for k in sorted(o):
                    if k.startswith("_") or not is_stat(k):
                        continue
                    base = arch.get(k, "0")
                    if differs(o[k], base):
                        changes.append([k, fmt(base), fmt(o[k])])
                if not changes:
                    continue
                top = o["_root"] or o
                rows.append({
                    "map": header.get("name", os.path.basename(full)),
                    "path": mpath,
                    "region": header.get("region", ""),
                    "arch": o["arch"],
                    "name": o.get("name") or arch.get("name", o["arch"]),
                    "x": int(num(top.get("x", 0)) or 0),
                    "y": int(num(top.get("y", 0)) or 0),
                    "level": int(num(o.get("level", arch.get("level", 0))) or 0),
                    "in": o["_stack"][-1] if o["_stack"] else "",
                    "changes": changes,
                })

    print("maps: %d  monsters: %d  altered: %d" % (nmaps, nmon, len(rows)),
          file=sys.stderr)
    if unknown:
        top = sorted(unknown.items(), key=lambda kv: -kv[1])[:5]
        print("objects with unknown archetype: %d kinds (e.g. %s)" %
              (len(unknown), ", ".join(k for k, _ in top)), file=sys.stderr)

    data = {"rows": rows, "regions": regions, "maps": nmaps, "monsters": nmon,
            "unknown": len(unknown)}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(data, f)
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.replace("/*__DATA__*/{}", blob))
    print("wrote " + args.out, file=sys.stderr)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crossfire - altered monster stats</title>
<style>
  :root { --bg:#fcfcfb; --surface:#f0efec; --border:#dddcd8; --text:#0b0b0b;
          --text2:#52514e; --muted:#82817d; --zebra:#f6f5f3; --hover:#e2eefb;
          --up:#c0392b; --down:#2a78d6; --accent:#2a78d6; }
  @media (prefers-color-scheme: dark) { :root { --bg:#1a1a19; --surface:#262625;
          --border:#3b3b39; --text:#fff; --text2:#c3c2b7; --muted:#91908a;
          --zebra:#201f1e; --hover:#24344a; --up:#e66767; --down:#5598e7; } }
  * { box-sizing: border-box; }
  body { margin:0; padding:24px; background:var(--bg); color:var(--text);
         font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:var(--text2); font-size:13px; margin:0 0 16px; max-width:80ch; }
  code { font-family:ui-monospace,Menlo,monospace; font-size:.92em; }
  .tiles { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }
  .tile { background:var(--surface); border:1px solid var(--border);
          border-radius:8px; padding:8px 14px; min-width:110px; }
  .tile b { display:block; font-size:22px; }
  .tile span { font-size:11px; color:var(--text2); }
  .bar { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
  input,select { background:var(--bg); color:var(--text); border:1px solid var(--border);
                 border-radius:6px; padding:7px 10px; font:inherit; }
  input[type=search] { flex:1 1 280px; min-width:200px; }
  #count { color:var(--text2); font-size:13px; margin-left:auto; }
  .wrap { overflow-x:auto; border:1px solid var(--border); border-radius:8px; }
  table { border-collapse:collapse; width:100%; }
  th,td { padding:6px 10px; text-align:left; vertical-align:top; border-bottom:1px solid var(--border); }
  th { position:sticky; top:0; background:var(--surface); cursor:pointer; user-select:none;
       white-space:nowrap; font-size:12px; }
  th:hover { color:var(--accent); }
  th .ar { color:var(--accent); }
  tbody tr:nth-child(even) { background:var(--zebra); }
  tbody tr:hover { background:var(--hover); }
  td.num { font-variant-numeric:tabular-nums; white-space:nowrap; }
  td.path { font-family:ui-monospace,Menlo,monospace; font-size:12px; color:var(--text2); word-break:break-all; }
  .m small { color:var(--muted); display:block; font-size:11px; }
  .chg { display:inline-block; margin:1px 4px 1px 0; padding:1px 7px; border-radius:10px;
         background:var(--surface); border:1px solid var(--border); font-size:12px;
         font-variant-numeric:tabular-nums; white-space:nowrap; cursor:pointer; }
  .chg i { font-style:normal; color:var(--muted); }
  .chg.up b { color:var(--up); } .chg.down b { color:var(--down); }
  .pager { display:flex; gap:8px; align-items:center; justify-content:center; margin:12px 0; }
  button { background:var(--surface); color:var(--text); border:1px solid var(--border);
           border-radius:6px; padding:6px 12px; font:inherit; cursor:pointer; }
  button:disabled { opacity:.4; cursor:default; }
  .note { color:var(--muted); font-size:12px; margin-top:12px; max-width:90ch; }
</style></head><body>
<h1>Altered monster stats</h1>
<p class="sub">Monsters placed in maps whose combat stats differ from their archetype.
Each stat shows <code>archetype &rarr; map</code>. Click a column heading to sort, type to
search any column, or click a stat chip to filter by that stat.</p>
<div class="tiles" id="tiles"></div>
<div class="bar">
  <input type="search" id="q" placeholder="Search map, path, region, monster, stat (e.g. hp, resist_fire, /scorn)...">
  <select id="reg"><option value="">All regions</option></select>
  <select id="stat"><option value="">All stats</option></select>
  <span id="count"></span>
</div>
<div class="wrap"><table>
<thead><tr id="head"></tr></thead><tbody id="body"></tbody></table></div>
<div class="pager"><button id="prev">&laquo; Prev</button><span id="pg"></span><button id="next">Next &raquo;</button>
  <select id="ps"><option>100</option><option>250</option><option>500</option><option>1000</option></select></div>
<p class="note">Coordinates are the square the monster (or its container, for inventory monsters such as
spawn-point contents) occupies. Red = raised above the archetype, blue = lowered. Region is the map
header's <code>region</code>; blank means none set.</p>
<script>
const D = /*__DATA__*/{};
const COLS = [
  ["map","Map name"],["path","Map path"],["region","Region"],
  ["monster","Monster"],["arch","Archetype"],["level","Level"],["xy","x,y"],["nchg","Altered stats"]];
const rows = D.rows.map(r => {
  r.monster = r.name; r.nchg = r.changes.length;
  r.hay = [r.map, r.path, r.region, D.regions[r.region]||"", r.arch, r.name,
           r.in, r.x+","+r.y, r.changes.map(c=>c[0]+" "+c[1]+" "+c[2]).join(" ")]
          .join("\n").toLowerCase();
  return r;
});
const $ = id => document.getElementById(id);
let sortKey="map", sortDir=1, page=0, view=rows;

function esc(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function fill(sel, vals){ vals.forEach(v=>{const o=document.createElement("option");o.value=v;o.textContent=v;sel.appendChild(o);}); }
fill($("reg"), [...new Set(rows.map(r=>r.region))].sort().map(v=>v));
$("reg").querySelectorAll("option").forEach(o=>{ if(!o.value) return; o.textContent=o.value+(D.regions[o.value]?" - "+D.regions[o.value]:""); });
const statCount = {};
rows.forEach(r=>r.changes.forEach(c=>statCount[c[0]]=(statCount[c[0]]||0)+1));
Object.keys(statCount).sort().forEach(s=>{const o=document.createElement("option");o.value=s;o.textContent=s+" ("+statCount[s]+")";$("stat").appendChild(o);});

$("tiles").innerHTML = [
  [D.rows.length,"altered monsters"],
  [new Set(rows.map(r=>r.path)).size,"maps affected"],
  [D.monsters,"monsters scanned"],[D.maps,"maps scanned"],
  [new Set(rows.map(r=>r.arch)).size,"distinct archetypes"]
].map(t=>'<div class="tile"><b>'+t[0].toLocaleString()+'</b><span>'+t[1]+'</span></div>').join("");

function head(){
  $("head").innerHTML = COLS.map(([k,l])=>'<th data-k="'+k+'">'+l+(k===sortKey?' <span class="ar">'+(sortDir>0?"▲":"▼")+'</span>':'')+'</th>').join("");
  $("head").querySelectorAll("th").forEach(th=>th.onclick=()=>{
    const k=th.dataset.k; if(k===sortKey) sortDir=-sortDir; else {sortKey=k; sortDir=1;}
    page=0; apply(); });
}
function cmp(a,b){
  let x,y;
  if(sortKey==="xy"){ return (a.x-b.x||a.y-b.y)*sortDir; }
  if(sortKey==="nchg"){ return (a.nchg-b.nchg)*sortDir; }
  if(sortKey==="level"){ return (a.level-b.level)*sortDir; }
  x=String(a[sortKey]).toLowerCase(); y=String(b[sortKey]).toLowerCase();
  return (x<y?-1:x>y?1:0)*sortDir;
}
function apply(){
  const terms = $("q").value.toLowerCase().split(/\s+/).filter(Boolean);
  const reg=$("reg").value, st=$("stat").value;
  view = rows.filter(r=>(!reg||r.region===reg) && (!st||r.changes.some(c=>c[0]===st))
                       && terms.every(t=>r.hay.includes(t)));
  view.sort((a,b)=>cmp(a,b)||a.path.localeCompare(b.path)||(a.y-b.y)||(a.x-b.x));
  head(); render(); saveUrl();
}
function saveUrl(){
  const p=new URLSearchParams();
  if($("reg").value) p.set("region",$("reg").value);
  if($("stat").value) p.set("stat",$("stat").value);
  if($("q").value) p.set("q",$("q").value);
  if(sortKey!=="map"||sortDir!==1){ p.set("sort",sortKey); p.set("dir",sortDir>0?"asc":"desc"); }
  const qs=p.toString();
  try{ history.replaceState(null,"",location.pathname+(qs?"?"+qs:"")); }catch(e){}
}
function loadUrl(){
  const p=new URLSearchParams(location.search);
  const has=(sel,v)=>[...sel.options].some(o=>o.value===v);
  if(p.has("region")&&has($("reg"),p.get("region"))) $("reg").value=p.get("region");
  if(p.has("stat")&&has($("stat"),p.get("stat"))) $("stat").value=p.get("stat");
  if(p.has("q")) $("q").value=p.get("q");
  if(COLS.some(c=>c[0]===p.get("sort"))) sortKey=p.get("sort");
  if(p.get("dir")==="desc") sortDir=-1; else if(p.get("dir")==="asc") sortDir=1;
}
function chip(c){
  const b=parseFloat(c[1]), n=parseFloat(c[2]);
  const cls = isNaN(b)||isNaN(n) ? "" : (n>b?"up":"down");
  return '<span class="chg '+cls+'" data-s="'+esc(c[0])+'">'+esc(c[0])+' <i>'+esc(c[1])+' &rarr;</i> <b>'+esc(c[2])+'</b></span>';
}
function render(){
  const ps=+$("ps").value, pages=Math.max(1,Math.ceil(view.length/ps));
  page=Math.min(page,pages-1);
  const slice=view.slice(page*ps,(page+1)*ps);
  $("body").innerHTML = slice.map(r=>
    '<tr><td>'+esc(r.map)+'</td><td class="path">'+esc(r.path)+'</td>'+
    '<td title="'+esc(D.regions[r.region]||"")+'">'+esc(r.region)+'</td>'+
    '<td class="m">'+esc(r.name)+(r.in?'<small>in '+esc(r.in)+'</small>':'')+'</td>'+
    '<td class="path">'+esc(r.arch)+'</td><td class="num">'+r.level+'</td>'+
    '<td class="num">'+r.x+','+r.y+'</td><td>'+r.changes.map(chip).join("")+'</td></tr>').join("");
  $("count").textContent = view.length.toLocaleString()+" of "+rows.length.toLocaleString()+" monsters";
  $("pg").textContent = "Page "+(page+1)+" / "+pages;
  $("prev").disabled = page===0; $("next").disabled = page>=pages-1;
}
$("q").oninput=()=>{page=0;apply();}; $("reg").onchange=$("stat").onchange=()=>{page=0;apply();};
$("ps").onchange=()=>{page=0;render();};
$("prev").onclick=()=>{page--;render();}; $("next").onclick=()=>{page++;render();};
$("body").onclick=e=>{const c=e.target.closest(".chg"); if(c){$("stat").value=c.dataset.s; page=0; apply();}};
loadUrl(); apply();
</script></body></html>
"""

if __name__ == "__main__":
    main()
