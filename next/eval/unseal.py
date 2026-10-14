"""Open the sealed held-out split once, and check it is the split that was sealed.

    python3 -I unseal.py KEYFILE OUT_DIR

Decrypts data/heldout.v1.tar.enc (AES-256-CBC, PBKDF2, as collect/seal.py wrote it), checks the
tar's SHA-256 and every member's against data/heldout.v1.sha256.json, and extracts it into OUT_DIR.
Stops without extracting if any hash differs.
"""
import hashlib, io, json, os, subprocess, sys, tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')


def main():
    key, out = sys.argv[1], sys.argv[2]
    rec = json.load(open(os.path.join(DATA, 'heldout.v1.sha256.json')))
    tar = subprocess.run(['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-pass', 'file:' + key, '-in',
                          os.path.join(DATA, 'heldout.v1.tar.enc')], capture_output=True, check=True).stdout
    got = hashlib.sha256(tar).hexdigest()
    if got != rec['tar_sha256']:
        sys.exit('tar hash differs: %s != %s' % (got, rec['tar_sha256']))
    tf = tarfile.open(fileobj=io.BytesIO(tar))
    seen = {}
    for m in tf.getmembers():
        if m.isfile():
            seen[m.name] = hashlib.sha256(tf.extractfile(m).read()).hexdigest()
    bad = [n for n, h in rec['members'].items() if seen.get(n) != h]
    extra = sorted(set(seen) - set(rec['members']))
    if bad or extra:
        sys.exit('member hashes differ: %s; unrecorded members: %s' % (bad[:5], extra[:5]))
    os.makedirs(out, exist_ok=True)
    for m in tf.getmembers():
        if m.name.startswith('/') or '..' in m.name.split('/'):
            sys.exit('unsafe member name %s' % m.name)
    tf.extractall(out)
    print(json.dumps({'tar_sha256': got, 'members_checked': len(rec['members']), 'sealed_at': rec.get('sealed_at'), 'out': out}))


if __name__ == '__main__':
    main()
