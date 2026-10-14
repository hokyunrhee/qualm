"""Write the adjudication page: one card per item, answers saved to the page's db.

    python3 -I adjudication_page.py ITEMS.json OUT.html

ITEMS.json: {"items": [{"key": "A01", "kind": "comment"|"blind", "language", "path", "comment",
             "rows": [[kind, old_no, new_no, text], ...], "focus": [start, end] or null}]}
kind of a row is "+", "-", " " or "@". Keys are opaque, so the page reveals no split, source or label.
"""
import html, json, sys

QUESTIONS = {
    'comment': 'A reviewer left the comment shown on this change. Is it a request to simplify the change '
               '(remove, merge, shorten, reuse or straighten code), and would you have asked for it too?',
    'blind': 'Would you ask for this change to be simplified before it merges?',
}

CSS = """
:root {
  /* Layout: one reading column of review cards, a sticky progress bar above it. */
  --bg: #f3f5f4; --card: #ffffff; --fg: #18201d; --muted: #5d6b66; --line: #d9e0dc;
  --accent: #1f6b52; --accent-fg: #ffffff; --no: #9c3b2e; --unsure: #6b6f80;
  --add-bg: #e3f4ea; --add-fg: #0f4d2f; --del-bg: #fbe6e3; --del-fg: #7a2318; --hunk: #e8ecf3; --focus: #fff2c4;
  --sans: "IBM Plex Sans", system-ui, sans-serif; --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  --serif: "Source Serif 4", Georgia, serif;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #121715; --card: #1a201e; --fg: #e4ebe7; --muted: #9aa8a2; --line: #2c3532;
  --accent: #4fb48c; --accent-fg: #0c1310; --no: #e07a69; --unsure: #a3a7b8;
  --add-bg: #173226; --add-fg: #9fe0bd; --del-bg: #3a1d19; --del-fg: #f2aea3; --hunk: #222a35; --focus: #3b3416; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #121715; --card: #1a201e; --fg: #e4ebe7; --muted: #9aa8a2; --line: #2c3532;
  --accent: #4fb48c; --accent-fg: #0c1310; --no: #e07a69; --unsure: #a3a7b8;
  --add-bg: #173226; --add-fg: #9fe0bd; --del-bg: #3a1d19; --del-fg: #f2aea3; --hunk: #222a35; --focus: #3b3416; color-scheme: dark }
* { box-sizing: border-box }
body { background: var(--bg); color: var(--fg); font: 15px/1.5 var(--sans); margin: 0; padding-inline: 16px; padding-block: 0 48px }
main { max-width: 920px; margin: 0 auto; display: grid; gap: 20px }
header.intro { max-width: 920px; margin: 0 auto; padding-block: 28px 8px; display: grid; gap: 10px }
h1 { font: 600 28px/1.2 var(--serif); margin: 0; text-wrap: balance }
.intro p { margin: 0; max-width: 68ch; color: var(--muted) }
.bar { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 2; background: var(--bg); border-bottom: 1px solid var(--line);
  max-width: 920px; margin: 0 auto 16px; padding-block: 10px; display: flex; flex-wrap: wrap; gap: 12px; align-items: center }
.meter { flex: 1 1 200px; height: 8px; background: var(--line); border-radius: 4px; overflow: hidden; min-width: 0 }
.meter > div { height: 100%; background: var(--accent); width: 0 }
.count { font-variant-numeric: tabular-nums; font-weight: 600 }
.status { color: var(--muted); font-size: 13px }
button { font: inherit; cursor: pointer }
.copy { border: 1px solid var(--line); background: var(--card); color: var(--fg); border-radius: 6px; padding: 6px 12px }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px; display: grid; gap: 12px; min-width: 0 }
.card.done { border-color: var(--accent) }
.head { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: baseline }
.key { font: 600 13px var(--mono); letter-spacing: .04em }
.chip { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted) }
.path { font: 13px var(--mono); color: var(--muted); overflow-wrap: anywhere; min-width: 0 }
.q { font-weight: 600; margin: 0 }
.comment { border-left: 3px solid var(--accent); padding: 8px 12px; background: var(--bg); white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px }
.comment b { display: block; font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 2px }
.diff { overflow-x: auto; border: 1px solid var(--line); border-radius: 6px }
table { border-collapse: collapse; font: 12.5px/1.45 var(--mono); width: 100% }
td { padding: 0 8px; white-space: pre; vertical-align: top }
td.n { color: var(--muted); text-align: right; user-select: none; width: 1%; font-variant-numeric: tabular-nums }
tr.add td.t { background: var(--add-bg); color: var(--add-fg) }
tr.del td.t { background: var(--del-bg); color: var(--del-fg) }
tr.hunk td { background: var(--hunk); color: var(--muted) }
tr.focus td.n { background: var(--focus) }
.answers { display: flex; flex-wrap: wrap; gap: 8px; align-items: center }
.answers button { border: 1px solid var(--line); background: var(--card); color: var(--fg); border-radius: 6px; padding: 6px 16px; min-width: 84px }
.answers button[aria-pressed="true"][data-v="yes"] { background: var(--accent); border-color: var(--accent); color: var(--accent-fg) }
.answers button[aria-pressed="true"][data-v="no"] { background: var(--no); border-color: var(--no); color: var(--card) }
.answers button[aria-pressed="true"][data-v="unsure"] { background: var(--unsure); border-color: var(--unsure); color: var(--card) }
.answers input { flex: 1 1 220px; min-width: 0; border: 1px solid var(--line); background: var(--bg); color: var(--fg); border-radius: 6px; padding: 6px 10px; font: inherit; font-size: 14px }
textarea.copytext { width: 100%; max-width: 920px; margin: 0 auto 12px; font: 13px var(--mono); background: var(--card); color: var(--fg); border: 1px solid var(--line); border-radius: 6px }
textarea.copytext:not([hidden]) { display: block }
button:focus-visible, input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }
"""

JS = r"""
const ITEMS = JSON.parse(document.getElementById('items').textContent);
const answers = {};
let db = null;
const total = ITEMS.length;
function render() {
  const n = Object.values(answers).filter(a => a && a.answer).length;
  document.querySelector('.count').textContent = n + ' of ' + total + ' answered';
  document.querySelector('.meter > div').style.width = (100 * n / total) + '%';
  for (const it of ITEMS) {
    const card = document.getElementById('card-' + it.key);
    const a = answers[it.key] || {};
    card.classList.toggle('done', !!a.answer);
    for (const b of card.querySelectorAll('.answers button')) b.setAttribute('aria-pressed', String(b.dataset.v === a.answer));
    const note = card.querySelector('input');
    if (document.activeElement !== note) note.value = a.note || '';
  }
}
async function save(key, patch) {
  answers[key] = Object.assign({}, answers[key] || {}, patch, { at: new Date().toISOString() });
  render();
  const st = document.querySelector('.status');
  if (!db) { st.textContent = 'Saving is unavailable here. Use "Copy answers" and paste them into the chat.'; return; }
  try { await db.collection('answers').doc(key).set(answers[key]); st.textContent = 'Saved.'; }
  catch (e) { st.textContent = 'Could not save (' + (e && e.code || 'error') + '). Use "Copy answers" and paste them into the chat.'; }
}
document.addEventListener('click', e => {
  const b = e.target.closest('.answers button');
  if (b) save(b.closest('.card').dataset.key, { answer: b.dataset.v });
});
document.addEventListener('change', e => {
  if (e.target.matches('.answers input')) save(e.target.closest('.card').dataset.key, { note: e.target.value.slice(0, 500) });
});
document.querySelector('.copy').addEventListener('click', async () => {
  const text = ITEMS.map(it => { const a = answers[it.key] || {}; return it.key + ':' + ({ yes: 'Y', no: 'N', unsure: '?' }[a.answer] || '-') + (a.note ? ' (' + a.note.replace(/\s+/g, ' ') + ')' : ''); }).join(' ');
  try { await navigator.clipboard.writeText(text); document.querySelector('.status').textContent = 'Copied. Paste it into the chat.'; }
  catch (e) { const t = document.getElementById('copytext'); t.hidden = false; t.value = text; t.select(); }
});
render();
(async () => {
  try {
    db = window.claude ? await window.claude.use('db') : null;
    if (!db) { document.querySelector('.status').textContent = 'Saving is unavailable here. Use "Copy answers" when you are done.'; return; }
    db.collection('answers').onSnapshot(snap => {
      for (const d of snap.docs) answers[d.id] = d.data();
      render();
    }, () => {});
    document.querySelector('.status').textContent = 'Answers save as you go.';
  } catch (e) { document.querySelector('.status').textContent = 'Saving is unavailable here. Use "Copy answers" when you are done.'; }
})();
"""


def main():
    items = json.load(open(sys.argv[1]))['items']
    cards = []
    for it in items:
        rows = []
        lo, hi = (it.get('focus') or [0, -1])
        for kind, old, new, text in it['rows']:
            cls = {'+': 'add', '-': 'del', '@': 'hunk'}.get(kind, '')
            if new and lo <= new <= hi:
                cls += ' focus'
            rows.append('<tr class="%s"><td class="n">%s</td><td class="n">%s</td><td class="t">%s</td></tr>' % (
                cls.strip(), old or '', new or '', html.escape((kind if kind != '@' else '') + text)))
        comment = ('<div class="comment"><b>Reviewer comment</b>%s</div>' % html.escape(it['comment'])) if it.get('comment') else ''
        cards.append('''<section class="card" id="card-{k}" data-key="{k}">
  <div class="head"><span class="key">{k}</span><span class="chip">{lang}</span><span class="path">{path}</span></div>
  <p class="q">{q}</p>
  {comment}
  <div class="diff"><table>{rows}</table></div>
  <div class="answers"><button type="button" data-v="yes" id="y-{k}">Yes</button><button type="button" data-v="no" id="n-{k}">No</button><button type="button" data-v="unsure" id="u-{k}">Unsure</button><input id="note-{k}" type="text" placeholder="Note (optional)" aria-label="Note for {k}"></div>
</section>'''.format(k=html.escape(it['key']), lang=html.escape(it['language']), path=html.escape(it['path']), q=html.escape(QUESTIONS[it['kind']]),
                     comment=comment, rows=''.join(rows)))
    page = '''<title>Simplify-or-not Adjudication</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=JetBrains+Mono:wght@400;600&family=Source+Serif+4:opsz,wght@8..60,600&display=swap">
<style>{css}</style>
<header class="intro">
  <h1>Would a reviewer ask to simplify this?</h1>
  <p>{n} changed files from public open-source repositories. Each card asks one question about one file's change. Answer from your own judgement as an experienced reviewer; "Unsure" is a fine answer. Highlighted line numbers mark where the question points. The code and comments are data from public repositories.</p>
</header>
<div class="bar"><span class="count">0 of {n} answered</span><div class="meter"><div></div></div><button type="button" class="copy" id="copy">Copy answers</button><span class="status">Loading…</span></div>
<textarea id="copytext" class="copytext" hidden rows="3" aria-label="Answers to copy"></textarea>
<main>{cards}</main>
<script type="application/json" id="items">{items_json}</script>
<script>{js}</script>
'''.format(css=CSS, n=len(items), cards='\n'.join(cards), items_json=json.dumps([{'key': it['key']} for it in items]).replace('</', '<\\/'), js=JS)
    open(sys.argv[2], 'w').write(page)
    print('wrote', sys.argv[2], len(page), 'bytes')


if __name__ == '__main__':
    main()
