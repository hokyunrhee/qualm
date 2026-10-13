"""Seal the held-out split: hash it, then encrypt it so nothing reads it by accident.

    python3 -I seal.py ASSEMBLED_DIR FIXLOOP_DIR OUT_DATA_DIR --label v1

Packs heldout.jsonl.gz, heldout_reserve.jsonl.gz and the held-out fix-loop items (their injected
files and patches) into one tar with fixed metadata, records the SHA-256 of that tar and of each
member in OUT_DATA_DIR/heldout.<label>.sha256.json, and writes the tar encrypted with AES-256
(openssl, PBKDF2) to OUT_DATA_DIR/heldout.<label>.tar.enc. The passphrase is in
OUT_DATA_DIR/heldout.key: the encryption keeps the split from being read by accident (a grep, a
curious agent); the hash is what proves it never changed. Open it only for the final evaluation:

    openssl enc -d -aes-256-cbc -pbkdf2 -pass file:heldout.key -in heldout.v1.tar.enc | tar -x
"""
import argparse, hashlib, io, json, os, secrets, subprocess, tarfile, time


def add(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    tar.addfile(info, io.BytesIO(data))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('assembled')
    ap.add_argument('fixloop_items')
    ap.add_argument('out')
    ap.add_argument('--label', default='v1')
    a = ap.parse_args()
    members = {}
    for name in ('heldout.jsonl.gz', 'heldout_reserve.jsonl.gz'):
        p = os.path.join(a.assembled, name)
        if os.path.exists(p):
            members[name] = open(p, 'rb').read()
    fx = json.load(open(a.fixloop_items))
    held = [it for it in fx['items'] if it['split'] == 'heldout']
    members['fixloop_heldout_items.json'] = json.dumps({'items': held}, sort_keys=True, indent=1).encode()
    for it in held:
        d = it['dir']
        for f in ('inject.patch', 'head.txt'):
            members['fixloop/%s/%s' % (it['fix_id'], f)] = open(os.path.join(d, f), 'rb').read()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name in sorted(members):
            add(tar, name, members[name])
    data = buf.getvalue()
    os.makedirs(a.out, exist_ok=True)
    key_path = os.path.join(a.out, 'heldout.key')
    if not os.path.exists(key_path):
        open(key_path, 'w').write(secrets.token_hex(32) + '\n')
    enc = os.path.join(a.out, 'heldout.%s.tar.enc' % a.label)
    subprocess.run(['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-salt', '-pass', 'file:' + key_path, '-out', enc],
                   input=data, check=True)
    record = {'label': a.label, 'sealed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'tar_sha256': hashlib.sha256(data).hexdigest(),
              'members': {n: hashlib.sha256(b).hexdigest() for n, b in sorted(members.items())},
              'fixloop_heldout_items': len(held)}
    json.dump(record, open(os.path.join(a.out, 'heldout.%s.sha256.json' % a.label), 'w'), indent=1)
    print(json.dumps({k: record[k] for k in ('label', 'tar_sha256', 'fixloop_heldout_items')}))


if __name__ == '__main__':
    main()
