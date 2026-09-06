import struct, zlib, pathlib

def make_png(w, h, rgb=(40, 120, 200)):
    def chunk(name, data):
        buf = name + data
        return struct.pack('>I', len(data)) + buf + struct.pack('>I', zlib.crc32(buf) & 0xffffffff)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    rows = b''.join(b'\x00' + bytes(rgb) * w for _ in range(h))
    idat = chunk(b'IDAT', zlib.compress(rows))
    iend = chunk(b'IEND', b'')
    return sig + ihdr + idat + iend

base = pathlib.Path(__file__).parent
for size, name in [(16, 'icon16.png'), (48, 'icon48.png'), (128, 'icon128.png')]:
    (base / name).write_bytes(make_png(size, size))
    print(f'Generated {name} ({size}x{size})')
