"""Move LLM requests and answers between candidates' --llm-dir folders and the answering workflow.

    python3 -I llm_answers.py stage ROOT STAGE [--shards N]   copy unanswered requests under ROOT to STAGE/<key>.json
                                                             and write STAGE/args_<k>.json for workflows/answer_llm.js
    python3 -I llm_answers.py write STAGE RUN_ID [RUN_ID ...]  write each agent's answer to every responses/<key>.json
                                                             that asked for it
    python3 -I llm_answers.py number STAGE                   also give every staged request a number (STAGE/n/<i>.json),
                                                             so a workflow can be handed a range instead of a list

A request lives at <dir>/requests/<key>.json and its answer at <dir>/responses/<key>.json. The
answering agents see only STAGE/<key>.json: the key is a hash, so nothing in the path (a case id
can name an injected smell) reaches the agent. Identical requests share a key and are answered once.
Each answer comes from a fresh-context subagent that saw only its request file.
"""
import glob, json, os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import journal  # noqa: E402


def pending(root):
    out = []
    for req in sorted(glob.glob(os.path.join(root, '**', 'requests', '*.json'), recursive=True)):
        resp = os.path.join(os.path.dirname(os.path.dirname(req)), 'responses', os.path.basename(req))
        if not os.path.exists(resp):
            out.append(req)
    return out


def stage(root, stage_dir, shards):
    os.makedirs(stage_dir, exist_ok=True)
    index_path = os.path.join(stage_dir, 'index.json')
    index = json.load(open(index_path)) if os.path.exists(index_path) else {}
    new, schemas = [], {}
    for req in pending(root):
        key = os.path.basename(req)[:-5]
        resp = os.path.join(os.path.dirname(os.path.dirname(req)), 'responses', key + '.json')
        if key not in index:
            index[key] = []
            shutil.copyfile(req, os.path.join(stage_dir, key + '.json'))
            new.append(key)
            with open(req) as fh:
                r = json.load(fh)
            schemas.setdefault((json.dumps(r['schema'], sort_keys=True), r['model']), []).append(key)
        if resp not in index[key]:
            index[key].append(resp)
    json.dump(index, open(index_path, 'w'), indent=1)
    k = 0
    for (schema, model), keys in schemas.items():
        tier = 'haiku' if 'haiku' in model else 'sonnet' if 'sonnet' in model else 'opus'
        for s in range(shards):
            part = keys[s::shards]
            if part:
                json.dump({'root': stage_dir, 'files': [x + '.json' for x in part], 'schema': json.loads(schema), 'model': tier, 'effort': 'low'},
                          open(os.path.join(stage_dir, 'args_%d.json' % k), 'w'))
                k += 1
    print('%d new requests staged in %s; %d workflow arg files' % (len(new), stage_dir, k))


def number(stage_dir):
    index = json.load(open(os.path.join(stage_dir, 'index.json')))
    os.makedirs(os.path.join(stage_dir, 'n'), exist_ok=True)
    alias_path = os.path.join(stage_dir, 'aliases.json')
    aliases = json.load(open(alias_path)) if os.path.exists(alias_path) else {}
    have = set(aliases.values())
    i = len(aliases)
    for key in sorted(index):
        if key in have:
            continue
        shutil.copyfile(os.path.join(stage_dir, key + '.json'), os.path.join(stage_dir, 'n', '%d.json' % i))
        aliases[str(i)] = key
        i += 1
    json.dump(aliases, open(alias_path, 'w'), indent=1)
    print('%d numbered requests in %s/n' % (len(aliases), stage_dir))


def write(stage_dir, run_ids):
    index = json.load(open(os.path.join(stage_dir, 'index.json')))
    alias_path = os.path.join(stage_dir, 'aliases.json')
    aliases = json.load(open(alias_path)) if os.path.exists(alias_path) else {}
    n = 0
    for run in run_ids:
        for path, ans in journal.results(run).items():
            key = os.path.basename(str(path))[:-5]
            key = aliases.get(key, key) if '/n/' in str(path) else key
            if not isinstance(ans, dict) or key not in index:
                continue
            for resp in index[key]:
                os.makedirs(os.path.dirname(resp), exist_ok=True)
                with open(resp, 'w') as fh:
                    json.dump(ans, fh)
                n += 1
    print(n, 'answer files written')


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'stage':
        shards = int(sys.argv[sys.argv.index('--shards') + 1]) if '--shards' in sys.argv else 4
        stage(sys.argv[2], sys.argv[3], shards)
    elif cmd == 'write':
        write(sys.argv[2], sys.argv[3:])
    elif cmd == 'number':
        number(sys.argv[2])
