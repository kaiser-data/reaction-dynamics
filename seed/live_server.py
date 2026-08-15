"""Live projector dashboard. Reads the listener's event log and shows reactions
arriving in real time.

    .venv/bin/python seed/live_server.py          # then open http://localhost:8765

It only READS seed/live_events.jsonl. It never touches the listener, never posts,
never writes. Safe to start and stop mid-demo.

Stdlib only -- no Flask, no websockets. The page polls /events.json every 1.5s,
which is faster than any human can react and cannot desync.
"""

import http.server
import json
import os
import socketserver
import statistics
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import store  # noqa: E402

LOG = os.path.join(HERE, "live_events.jsonl")
PORT = int(os.environ.get("PORT", "8765"))

MIN_REACTIONS = 4  # same gate as shapes.py -- below this, arrival order is noise


def read_events():
    """Read from the capture store.

    An empty store yields an empty page ("Waiting for the first reaction..."),
    which is correct: it means nothing has been captured, not that something
    went wrong. Run `store.migrate_jsonl` once to bring a legacy JSONL in.
    """
    conn = store.connect(os.getenv("CAPTURE_DB"))
    store.init(conn)
    out = []
    for r in conn.execute(
            "SELECT kind, user_id, user, raw FROM events "
            "ORDER BY event_ts").fetchall():
        e = json.loads(r["raw"])
        e["kind"] = r["kind"]
        # A raw Slack id when the name is unresolved -- visibly an id, never
        # a fabricated name.
        e["user"] = r["user"] or r["user_id"]
        out.append(e)
    for m in conn.execute("SELECT * FROM messages").fetchall():
        out.append({"kind": "message", "ts": m["ts"], "channel": m["channel"],
                    "user": m["user"] or m["user_id"], "text": m["text"],
                    "ts_iso": m["ts_iso"]})
    conn.close()
    return out


def coverage():
    """What the tool knows about its own blind spots."""
    conn = store.connect(os.getenv("CAPTURE_DB"))
    store.init(conn)
    gaps = [{"since": g["started_at"], "reason": g["reason"]}
            for g in store.open_gaps(conn)]
    dark = round(store.total_dark_seconds(conn) / 60, 1)
    conn.close()
    return gaps, dark


def classify(spans):
    """Cheap live shape read. The real classifier is shapes.py (KS test); this is
    the projector version, and it says 'forming' until it has enough arrivals."""
    n = len(spans)
    if n < MIN_REACTIONS:
        return "forming", f"{MIN_REACTIONS - n} more"
    gaps = [b - a for a, b in zip(spans, spans[1:])]
    if not gaps:
        return "forming", ""
    mean = sum(gaps) / len(gaps)
    sd = statistics.pstdev(gaps)
    burst = (sd - mean) / (sd + mean) if (sd + mean) else 0
    total = spans[-1] - spans[0]
    if total <= 0:
        return "cascade", "same instant"
    first_half = sum(1 for s in spans if s - spans[0] <= total * 0.35)
    if first_half / n >= 0.6:
        return "cascade", "copied whoever went first"
    if spans[1] - spans[0] > total * 0.5:
        return "stall-burst", "silence, then everyone"
    if burst < 0.1:
        return "trickle", "decided independently"
    return "mixed", ""


def build():
    ev = read_events()
    msgs = {e["ts"]: e for e in ev if e.get("kind") == "message"}
    rx = [e for e in ev if e.get("kind") == "reaction_added"]
    rx.sort(key=lambda e: float(e.get("event_ts") or 0))

    by_msg = defaultdict(list)
    for r in rx:
        by_msg[r.get("message_ts")].append(r)

    cards = []
    for mts, rs in by_msg.items():
        m = msgs.get(mts, {})
        times = [float(r["event_ts"]) for r in rs]
        shape, hint = classify(times)
        first = rs[0]
        cards.append({
            "text": (m.get("text") or "(message not in this capture window)")[:150],
            "author": m.get("user", "?"),
            "n": len(rs),
            "shape": shape,
            "hint": hint,
            "first_reactor": first.get("user"),
            "span_s": round(times[-1] - times[0], 1) if len(times) > 1 else 0,
            "emoji": [r.get("emoji") for r in rs],
            "people": sorted({r.get("user") for r in rs}),
            "last": max(times),
        })
    cards.sort(key=lambda c: -c["last"])

    feed = [{
        "user": r.get("user"), "emoji": r.get("emoji"),
        "at": (r.get("ts_iso") or "")[11:19],
        "latency_s": round(float(r["event_ts"]) - float(r.get("message_ts") or r["event_ts"]), 1),
    } for r in rx[::-1][:14]]

    # Hero = the most recently POSTED message. That is the one on the projector,
    # whether or not anyone has reacted to it yet.
    hero = None
    if msgs:
        hts = max(msgs, key=lambda t: float(t))
        hm = msgs[hts]
        hrs = sorted(by_msg.get(hts, []), key=lambda r: float(r["event_ts"]))
        times = [float(r["event_ts"]) for r in hrs]
        shape, hint = classify(times)
        base = float(hts)
        hero = {
            "text": (hm.get("text") or "")[:400],
            "n": len(hrs),
            "need": max(0, MIN_REACTIONS - len(hrs)),
            "shape": shape,
            "hint": hint,
            "span_s": round(times[-1] - times[0], 1) if len(times) > 1 else 0,
            "people": len({r.get("user") for r in hrs}),
            "arrivals": [{
                "user": r.get("user"),
                "emoji": r.get("emoji"),
                "after": round(float(r["event_ts"]) - base, 1),
                "gap": (round(float(r["event_ts"]) - times[i - 1], 1) if i else 0.0),
            } for i, r in enumerate(hrs)],
        }

    people = Counter(r.get("user") for r in rx)
    gaps, dark_minutes = coverage()
    return {
        "served_at": time.time(),
        "open_gaps": gaps,
        "dark_minutes": dark_minutes,
        "hero": hero,
        "reactions": len(rx),
        "people": len(people),
        "messages": len(msgs),
        "leaderboard": people.most_common(6),
        "emoji": Counter(r.get("emoji") for r in rx).most_common(8),
        "cards": cards[:6],
        "feed": feed,
        "shaped": sum(1 for c in cards if c["shape"] not in ("forming", "mixed")),
    }


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reaction Dynamics — LIVE</title><style>
:root{--bg:#0b0d12;--panel:#141821;--line:#2a3040;--ink:#f8f5ed;--muted:#a4abba;
--dim:#687185;--cyan:#54d8ff;--green:#56dda7;--yellow:#ffca55;--red:#ff4f7d;--orange:#ff6b35;
--mono:"SFMono-Regular",Menlo,Consolas,monospace;
--display:"Avenir Next Condensed","Arial Narrow",sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:16px/1.5 "Avenir Next","Helvetica Neue",system-ui,sans-serif;
padding:clamp(16px,2.4vw,34px);-webkit-font-smoothing:antialiased}
.top{display:flex;align-items:baseline;gap:18px;flex-wrap:wrap;margin-bottom:20px}
h1{font-family:var(--display);text-transform:uppercase;font-size:clamp(26px,3.4vw,46px);letter-spacing:-.02em}
.dot{width:11px;height:11px;border-radius:50%;background:var(--green);
box-shadow:0 0 14px var(--green);animation:p 1.6s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.25}}
.live{font:700 11px var(--mono);letter-spacing:.2em;color:var(--green);display:flex;align-items:center;gap:9px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:18px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.tile b{display:block;font-family:var(--display);font-size:clamp(34px,5vw,64px);line-height:1}
.tile span{font:700 10px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--dim)}
.grid{display:grid;grid-template-columns:1.35fr .65fr;gap:16px}
@media(max-width:900px){.grid,.tiles{grid-template-columns:1fr 1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:12px}
.card.hot{border-color:var(--green);box-shadow:0 0 0 1px var(--green),0 0 30px rgba(86,221,167,.16)}
.msg{font-size:15px;color:var(--muted);margin-bottom:10px}
.row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.shape{font:800 13px var(--mono);letter-spacing:.12em;text-transform:uppercase;padding:4px 10px;
border-radius:999px;border:1px solid currentColor}
.cascade{color:var(--cyan)}.trickle{color:var(--green)}
.stall-burst{color:var(--yellow)}.forming{color:var(--dim)}.mixed{color:var(--muted)}
.cnt{font-family:var(--display);font-size:30px;line-height:1}
.who{font:600 12px var(--mono);color:var(--dim)}
.emo{font-size:22px;letter-spacing:3px}
h2{font:700 11px var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--dim);margin-bottom:12px}
.f{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid var(--line);font-size:14px}
.f:last-child{border:0}.f .n{color:var(--ink);font-weight:600}.f .t{font:500 11px var(--mono);color:var(--dim)}
.new{animation:in .5s ease}@keyframes in{from{background:rgba(86,221,167,.18)}to{background:transparent}}
.empty{color:var(--dim);font-size:14px;padding:14px 0}
.banner:empty,.gaps:empty{display:none}
.banner{background:var(--red);color:#fff;font:800 13px var(--mono);
letter-spacing:.08em;padding:11px 15px;border-radius:10px;margin-bottom:14px}
.gaps{background:#2a2214;color:var(--yellow);font:700 12px var(--mono);
padding:9px 14px;border-radius:10px;margin-bottom:14px}
/* a stale page must not keep pulsing a green LIVE dot */
body.stale .dot{background:var(--red);box-shadow:none;animation:none}
body.stale .live{color:var(--red)}
/* hero: the message currently on the projector */
.hero{border-color:var(--orange);margin-bottom:18px}
.hero .htext{font-size:clamp(15px,1.6vw,21px);color:var(--ink);margin:6px 0 16px;line-height:1.4}
.hero .hbig{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
.hero .hn{font-family:var(--display);font-size:clamp(52px,8vw,104px);line-height:.9}
.hero .need{font:800 14px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--orange)}
.tiles2{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:10px;margin-top:16px}
.ar{background:#0f1218;border:1px solid var(--line);border-left:3px solid var(--green);
border-radius:10px;padding:11px 13px;animation:in .6s ease}
.ar .e{font-size:26px;line-height:1}
.ar .u{font:700 12px var(--mono);color:var(--ink);margin-top:7px;overflow:hidden;text-overflow:ellipsis}
.ar .g{font:500 11px var(--mono);color:var(--dim);margin-top:3px}
.ar.first{border-left-color:var(--yellow)}
.ar.first .g{color:var(--yellow)}
</style></head><body>
<div class="top"><h1>Reaction Dynamics</h1>
<div class="live"><span class="dot"></span>LIVE · #emojie-lab</div></div>
<div id="banner" class="banner"></div>
<div id="gaps" class="gaps"></div>
<div class="tiles">
 <div class="tile"><b id="r">0</b><span>reactions</span></div>
 <div class="tile"><b id="p">0</b><span>people</span></div>
 <div class="tile"><b id="s">0</b><span>shapes formed</span></div>
 <div class="tile"><b id="m">0</b><span>messages</span></div>
</div>
<div class="card hero" id="hero"></div>
<div class="grid">
 <div><h2>Messages · live arrival shape</h2><div id="cards"></div></div>
 <div><div class="card"><h2>Reactions as they land</h2><div id="feed"></div></div>
      <div class="card"><h2>Who is reacting</h2><div id="lb"></div></div></div>
</div>
<script>
var seen={};
function esc(s){return String(s==null?"":s).replace(/[&<>]/g,function(c){
 return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c]})}
// A page that cannot reach the server must say so. Leaving the last good
// numbers under a pulsing green LIVE dot is the failure this replaces.
var lastGood=null;
function stale(isStale,servedAt){
 if(!isStale){lastGood=servedAt;document.body.classList.remove('stale');
  banner.textContent='';return}
 document.body.classList.add('stale');
 var age=lastGood?Math.round(Date.now()/1000-lastGood):null;
 banner.textContent = age===null
  ? 'NOT CONNECTED - this page has never reached the capture server.'
  : 'STALE - no update for '+age+'s. These numbers are frozen, not live.';
}
function gapline(d){
 if(d.open_gaps&&d.open_gaps.length){
  gaps.textContent='CAPTURE GAP OPEN ('+d.open_gaps[0].reason
   +') - reactions are being missed right now.';
 }else if(d.dark_minutes>0){
  gaps.textContent=d.dark_minutes+' min not captured on record.';
 }else{gaps.textContent=''}
}
function tick(){
 fetch('/events.json?t='+Date.now()).then(function(r){return r.json()}).then(function(d){
  r.textContent=d.reactions; p.textContent=d.people;
  s.textContent=d.shaped;   m.textContent=d.messages;
  var h=d.hero;
  hero.innerHTML = !h ? '' :
   '<h2>On the projector right now</h2>'
   +'<div class="htext">'+esc(h.text)+'</div>'
   +'<div class="hbig"><span class="hn">'+h.n+'</span>'
   +'<span class="shape '+h.shape+'">'+h.shape+(h.hint?' · '+esc(h.hint):'')+'</span>'
   +(h.need>0 ? '<span class="need">'+h.need+' more to form a shape</span>'
              : '<span class="need" style="color:var(--green)">shape locked · '
                +h.people+' people · '+h.span_s+'s span</span>')
   +'</div>'
   +(h.arrivals.length
      ? '<div class="tiles2">'+h.arrivals.map(function(a,i){
          return '<div class="ar'+(i===0?' first':'')+'">'
           +'<div class="e">:'+esc(a.emoji)+':</div>'
           +'<div class="u">'+esc(a.user)+'</div>'
           +'<div class="g">'+(i===0?'MOVED FIRST · +'+a.after+'s':'+'+a.gap+'s after')+'</div>'
           +'</div>'}).join('')+'</div>'
      : '<div class="empty">Nobody has reacted yet. Be the one who moves first.</div>');
  cards.innerHTML = d.cards.length ? d.cards.map(function(c){
   var hot = c.shape!=='forming';
   return '<div class="card'+(hot?' hot':'')+'">'
    +'<div class="msg">'+esc(c.text)+'</div>'
    +'<div class="row"><span class="cnt">'+c.n+'</span>'
    +'<span class="shape '+c.shape+'">'+c.shape+(c.hint?' · '+esc(c.hint):'')+'</span>'
    +'<span class="emo">'+c.emoji.map(function(e){return ':'+e+':'}).join(' ')+'</span></div>'
    +'<div class="who" style="margin-top:9px">first: '+esc(c.first_reactor)
    +' · span '+c.span_s+'s · '+c.people.length+' people</div></div>'
  }).join('') : '<div class="empty">Waiting for the first reaction…</div>';
  feed.innerHTML = d.feed.length ? d.feed.map(function(f){
   var k=f.user+f.at+f.emoji, isNew=!seen[k]; seen[k]=1;
   return '<div class="f'+(isNew?' new':'')+'"><span class="n">'+esc(f.user)+' :'+esc(f.emoji)+':</span>'
    +'<span class="t">'+f.at+' · +'+f.latency_s+'s</span></div>'
  }).join('') : '<div class="empty">—</div>';
  lb.innerHTML = d.leaderboard.map(function(x){
   return '<div class="f"><span class="n">'+esc(x[0])+'</span><span class="t">'+x[1]+'</span></div>'}).join('');
  stale(false,d.served_at);
  gapline(d);
 }).catch(function(){stale(true,null)});
}
tick(); setInterval(tick,1500);
</script></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # keep the projector terminal clean

    def do_GET(self):
        if self.path.startswith("/events.json"):
            body = json.dumps(build()).encode()
            ctype = "application/json"
        else:
            body = PAGE.encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    if not os.path.exists(LOG):
        print(f"!! {LOG} not found -- is the listener running?", file=sys.stderr)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), H) as srv:
        print(f"  LIVE dashboard  ->  http://localhost:{PORT}")
        print("  reads live_events.jsonl only. Does not touch the listener. Ctrl-C to stop.")
        srv.serve_forever()
