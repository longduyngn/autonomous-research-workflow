"""Bounded text views; never materialize an entire log."""
import re


def tail_log(path, n_lines=50):
    count = min(200, max(1, int(n_lines)))
    chunks, size = [], 0
    with path.open('rb') as stream:
        position = stream.seek(0, 2)
        while position and size < 262144:
            length = min(position, 8192)
            position -= length
            stream.seek(position)
            chunk = stream.read(length)
            chunks.append(chunk)
            size += length
            if sum(c.count(b'\n') for c in chunks) > count:
                break
    lines = b''.join(reversed(chunks)).decode('utf-8', errors='replace').splitlines()[-count:]
    return {'lines': [line[:1000] for line in lines], 'truncated': bool(position),
            'bytes_read': size}


def search_log(path, pattern, max_matches=20):
    regex = re.compile(pattern)
    maximum = min(100, max(1, int(max_matches)))
    matches, scanned = [], 0
    with path.open(encoding='utf-8', errors='replace') as stream:
        while True:
            line = stream.readline(65536)
            if not line:
                break
            scanned += 1
            hit = regex.search(line)
            # Drain long lines without retaining them; match each bounded chunk.
            display = line[:1000].rstrip('\r\n')
            while not line.endswith('\n'):
                line = stream.readline(65536)
                if not line:
                    break
                if not hit:
                    hit = regex.search(line)
                    if hit:
                        display = line[:1000].rstrip('\r\n')
            if hit:
                matches.append({'line_no': scanned, 'text': display})
                if len(matches) >= maximum:
                    return {'matches': matches, 'truncated': True}
    return {'matches': matches, 'truncated': False}
