"""LLM calls in this study go through files a harness answers with fresh-context subagents.

`ask(dir, model, system, prompt, schema)` returns the parsed answer when `dir/responses/<key>.json`
exists, and otherwise writes `dir/requests/<key>.json` and returns None; the caller collects all
the requests it needs in one pass, and the run exits 2 with `needs llm`. A shipped version
replaces this with one Messages API call per request (ANTHROPIC_API_KEY).

Token counts are estimates (UTF-8 bytes / 3.5) because no tokenizer is available offline.
"""
import hashlib, json, os

PRICES = {'claude-haiku-5-5': (0.10, 0.50), 'claude-sonnet-5-5': (2.00, 10.00), 'claude-opus-5-5': (4.00, 20.00)}


def est_tokens(text):
    return int(len(text.encode('utf-8')) / 3.5) + 1


class LLM:
    def __init__(self, directory):
        self.dir = directory
        self.pending = 0
        self.requests = self.input_tokens = self.output_tokens = 0
        self.cost = 0.0

    def ask(self, model, system, prompt, schema):
        req = {'model': model, 'system': system, 'prompt': prompt, 'schema': schema}
        key = hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()[:32]
        req['key'] = key
        resp_path = os.path.join(self.dir, 'responses', key + '.json')
        tin = est_tokens(system) + est_tokens(prompt) + est_tokens(json.dumps(schema))
        if os.path.exists(resp_path):
            try:
                with open(resp_path) as fh:
                    ans = json.load(fh)
            except (OSError, ValueError):
                ans = None
            if isinstance(ans, dict):
                tout = est_tokens(json.dumps(ans))
                pin, pout = PRICES.get(model, PRICES['claude-sonnet-5-5'])
                self.requests += 1
                self.input_tokens += tin
                self.output_tokens += tout
                self.cost += (tin * pin + tout * pout) / 1e6
                return ans
        os.makedirs(os.path.join(self.dir, 'requests'), exist_ok=True)
        with open(os.path.join(self.dir, 'requests', key + '.json'), 'w') as fh:
            json.dump(req, fh)
        self.pending += 1
        return None
