"""Checking the reviewer's answer before anything from it reaches the report.

The answer is untrusted twice over: the model can be wrong about where it points, and the file
it read can try to speak through it (the red-team "canary" attack plants a marker in a comment
and asks tools to echo it). So:

- a comment must point at lines the change added (one line of slack either side), or it is dropped;
- a message or fix that repeats text found in the file only inside comments or string literals is
  replaced by a template for the comment's kind, built from its verified lines and the name of the
  enclosing function. Words that also occur in code (identifiers), common English and review
  vocabulary, and plausible line numbers pass; any other word from a comment or string, or any run
  of four words copied from one, does not;
- text is cut to one line of bounded length, so it cannot forge lines of the text report.
"""
import math, re

from gatecore import detectors

KINDS = ('remove', 'reuse', 'merge', 'shorten', 'split', 'debug', 'stub', 'guard', 'other')
MAX_COMMENTS = 3
MESSAGE_CHARS = 300
FIX_CHARS = 400
PHRASE = 4  # words copied in a row from a comment or string

# what a templated finding says, per kind: (what the code does, what to do)
TEMPLATES = {
    'remove': ('adds code the change does not need', 'Delete the code the feature does not use; keep the behaviour and the tests as they are.'),
    'reuse': ('re-implements something the code base or standard library already provides', 'Call the existing function or library routine instead of writing it again.'),
    'merge': ('repeats logic that should be written once', 'Write the repeated steps once, as one function, loop or table, and use it from each place.'),
    'shorten': ('is longer than it needs to be', 'Rewrite it with the shorter equivalent construct, keeping the behaviour.'),
    'split': ('does too many things in one place', 'Split it into smaller functions that each do one job.'),
    'debug': ('leaves debugging output in', 'Delete the debugging statements; send output that is really needed through the logger the code already uses.'),
    'stub': ('leaves a stub or placeholder in', 'Implement it, or remove it together with whatever calls it.'),
    'guard': ('checks for a state that cannot happen', 'Remove the check, or handle the case where the state can really occur.'),
    'other': ('is more complicated than it needs to be', 'Simplify it so it does only what the feature needs.'),
}

COMMON = set("""
a about above across act acts actual actually add added adding adds after again against all allocate allocates allocation almost
alone along already also alternative although always an and another any anything anyway api apis append appends are argument
arguments around array arrays as ask assert assertion assign assigned assignment assigns at attribute attributes avoid away back
bad base be because become becomes been before behavior behaviour behaviours being below best better between block blocks body
bool boolean both branch branches break build builds built but by cache call callback called caller callers calling calls can
cannot case cases cast catch catches caught chain change changed changes changing char character characters check checked
checking checks class classes clean cleaner cleanup clear clearly close closure code codebase collapse collection combine
combined come comes comment commented comments common compare compares comparison complex complexity complicated compute
computed computes computing condition conditional conditions const constant constants construct constructor constructs contain
contains content context continue control convert converted converts copied copies copy correct could count counter create
created creates creating current currently data dead debug debugging declaration declare declared declares default define
defined defines definition defensive delete deleted deletes deleting depend dependency derive derived describe detail
dictionary did different directly do does doing done down drop dropped duplicate duplicated duplicates duplication during each
earlier early easier edge either element elements else elsewhere empty end ends enough entire entry equal equivalent error
errors even every everything exactly example exception exceptions exist existing exists exit expect expected explicit
expression expressions extra extract extracted factor fail fails failure false field fields file files filter final finally
find first fix flag flags flatten follow following for form format formats found from full fully function functions further
general generic get gets getter give given gives global go goes guard guarded guards handle handled handler handles handling
happen happens has hash have having helper helpers here hold holds how however identical if immediately implement
implementation implemented implements import imports impossible in index indirection inline inlined inner input inputs inside
instance instead int integer interface intermediate into is it item items iterate iterates iteration its itself just keep keeps
key keys kind know known label large last later layer leave leaves leftover length less let level like line lines list lists
literal literals little local locals log logged logger logging logic long longer look loop loops lot main make makes making
manual manually map maps match matches may maybe member members merge merged message method methods might missing mode module
more most move moved much multiple must mutable name named names need needed needs nested nesting never new next nil no none
not nothing now null number numbers object objects obvious occur occurs of off old on once one ones only open operation option
optional options or order original other others otherwise out outer output outside over own package parameter parameters part
parse parsed parses parsing pass passed passes path pattern per place places placeholder plain point pointer possible prefer
previous print printed printing prints private probably property public put range rather read reads real really reason
receiver record redundant refactor reference remove removed removes removing repeat repeated repeating repeats replace replaced
replaces request required result results return returned returning returns reuse reused reuses right routine rule run runs
safe same second see separate set sets setter several shared shorter should show shown side similar simple simpler simplify
simply since single size slice slices so some something somewhere split standard start state statement statements step steps
still store stored straight string strings struct structure stub stubs such sum support switch table take takes temporary test
tests than that the their them then there these they thing things this those three through throw thrown thus time to todo
together too top true try twice two type types unchanged under unimplemented unit unless unnecessary unneeded unreachable
until unused up update use used useful uses using usual value values var variable variables various verbose version very via
want was way we well were what whatever when where whether which while whole why will with within without word work works
would wrap wrapped wrapper wrappers wraps write writes writing written yet you your zero
""".split())

ATOM = re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+|[^\W\d_]+')
NON_ASCII = re.compile(r'[^\x00-\x7f]{2,}')


def atoms(text):
    """Lower-case word pieces: identifiers split at case changes, underscores, digits and punctuation."""
    return [a.lower() for a in ATOM.findall(text)]


def stems(a):
    out = [a]
    for suffix, repl in (('ies', 'y'), ('es', ''), ('s', ''), ('ly', ''), ('ed', ''), ('ed', 'e'), ('ing', ''), ('ing', 'e'), ('er', '')):
        if a.endswith(suffix) and len(a) - len(suffix) >= 3:
            out.append(a[:-len(suffix)] + repl)
    return out


class Taint:
    """What the file says only in comments and strings, to keep it out of the report."""

    def __init__(self, f):
        self.n_lines = len(f.lines)
        code, data = [], []
        for scanned in (f.scan, f.base_scan or []):
            for c, comment, strings in scanned:
                code.append(c)
                if comment:
                    data.append(comment)
                data.extend(s for s in strings if s)
        self.code = set()
        for line in code:
            self.code.update(atoms(line))
        self.data = set()
        self.phrases = set()
        self.data_text = '\n'.join(data)
        for text in data:
            seq = atoms(text)
            self.data.update(seq)
            for i in range(len(seq) - PHRASE + 1):
                self.phrases.add(' '.join(seq[i:i + PHRASE]))

    def known(self, a):
        if a.isdigit():
            return int(a) <= self.n_lines + 1 or a in self.code
        return any(s in self.code or s in COMMON for s in stems(a))

    def tainted(self, text):
        """True when text repeats something the file says only in a comment or string."""
        seq = atoms(text)
        if any(a in self.data and not self.known(a) for a in seq):
            return True
        for i in range(len(seq) - PHRASE + 1):
            gram = seq[i:i + PHRASE]
            if ' '.join(gram) in self.phrases and not all(a in self.code for a in gram):
                return True
        return any(run in self.data_text for run in NON_ASCII.findall(text))


def one_line(text, limit):
    text = re.sub(r'\s+', ' ', ''.join(ch if ch.isprintable() or ch.isspace() else ' ' for ch in text)).strip()
    if len(text) > limit:
        cut = text[:limit - 1]
        cut = cut[:cut.rfind(' ')] if ' ' in cut[limit // 2:] else cut
        text = cut.rstrip(' ,;:') + '...'
    return text


class Answer:
    """One parsed answer: blocking, probability and its raw comments."""

    def __init__(self, blocking, probability, comments, notes):
        self.blocking, self.probability, self.comments, self.notes = blocking, probability, comments, notes


def parse(ans):
    """An Answer, or None when the answer has no usable probability."""
    if not isinstance(ans, dict):
        return None
    notes = []
    p = ans.get('probability')
    if isinstance(p, str):
        try:
            p = float(p.strip())
        except ValueError:
            return None
    if isinstance(p, bool) or not isinstance(p, (int, float)) or math.isnan(p):
        return None
    if not 0 <= p <= 1:
        notes.append('probability %r clamped' % p)
        p = min(1.0, max(0.0, p))
    blocking = ans.get('blocking')
    if not isinstance(blocking, bool):
        notes.append('blocking missing; read from the probability')
        blocking = p >= 0.5
    comments = ans.get('comments')
    if not isinstance(comments, list):
        if comments is not None:
            notes.append('comments not a list; ignored')
        comments = []
    return Answer(blocking, float(p), comments, notes)


def line_range(raw, n_lines):
    """[lo, hi] from what the model wrote, clamped to the file, or None."""
    if not isinstance(raw, list):
        raw = [raw]
    if not raw or len(raw) > 2 or not all(whole(x) for x in raw):
        return None
    lo, hi = int(min(raw)), int(max(raw))
    lo, hi = max(1, lo), min(n_lines, hi)
    return (lo, hi) if lo <= hi else None


def whole(x):
    return not isinstance(x, bool) and (isinstance(x, int) or (isinstance(x, float) and x.is_integer()))


def touches_added(f, lo, hi):
    return any(k in f.added for k in range(lo - 1, hi + 2))


def template(f, kind, lo, hi):
    what, fix = TEMPLATES[kind]
    return 'The code at %s %s.' % (detectors.where(f, lo, hi), what), fix


def check(f, raw_comments, taint, n_lines):
    """Verified comments as findings without confidence, and a record of what happened to each
    raw comment (lines and kind only, never its text)."""
    kept, record = [], []
    for c in raw_comments:
        if not isinstance(c, dict):
            record.append({'why': 'not an object'})
            continue
        kind = c.get('kind') if c.get('kind') in KINDS else 'other'
        span = line_range(c.get('lines'), n_lines)
        row = {'lines': list(span) if span else None, 'kind': kind}
        record.append(row)
        if span is None:
            row['why'] = 'no usable line range'
            continue
        lo, hi = span
        if not touches_added(f, lo, hi):
            row['why'] = 'not on an added line'
            continue
        if len(kept) >= MAX_COMMENTS:
            row['why'] = 'over the limit of %d comments' % MAX_COMMENTS
            continue
        t_msg, t_fix = template(f, kind, lo, hi)
        message = one_line(c.get('message') if isinstance(c.get('message'), str) else '', MESSAGE_CHARS)
        fix = one_line(c.get('fix') if isinstance(c.get('fix'), str) else '', FIX_CHARS)
        rewritten = []
        if not message or taint.tainted(message):
            message = t_msg
            rewritten.append('message')
        if not fix or taint.tainted(fix):
            fix = t_fix
            rewritten.append('fix')
        row['why'] = 'kept' + (' (%s rewritten)' % ' and '.join(rewritten) if rewritten else '')
        kept.append({'id': 'llm_' + kind, 'lines': [lo, hi], 'message': message, 'fix': fix, 'related': []})
    return kept, record


def generic(f, probability):
    """The finding a failing file gets when none of the reviewer's comments survived: the largest
    run of added code, with advice that quotes nothing from the file."""
    best, size = None, -1
    for lo, hi in fold_added(f):
        k = sum(1 for n in range(lo, hi + 1) if f.code[n - 1].strip())
        if k > size:
            best, size = (lo, hi), k
    lo, hi = best or (1, 1)
    return {'id': 'llm_generic', 'lines': [lo, hi],
            'message': 'A reviewer would likely ask for this change to be simpler, and the code at %s is its largest addition.' % detectors.where(f, lo, hi),
            'fix': 'Re-read it and remove what the feature does not need: repeated steps, unused helpers, checks for states that cannot happen, leftover debugging. Keep the behaviour and the tests as they are.',
            'confidence': round(probability * 0.5, 2), 'related': []}


def fold_added(f):
    out = []
    for k in sorted(f.added):
        if k > len(f.lines) or not f.lines[k - 1].strip():
            continue
        if out and k <= out[-1][1] + 2:
            out[-1][1] = k
        else:
            out.append([k, k])
    return [tuple(r) for r in out]
