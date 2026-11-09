"""Bounded HWP 5 text reader. Text only; no macros, embedded objects or rendering."""
import re
import struct
import zlib

END = 0xfffffffe
FREE = 0xffffffff


def u32(data, offset=0):
    return struct.unpack_from('<I', data, offset)[0]


class CompoundFile:
    def __init__(self, data):
        if len(data) < 512 or data[:8] != bytes.fromhex('d0cf11e0a1b11ae1'):
            raise ValueError('HWP 바이너리 문서의 저장 구조가 올바르지 않습니다.')
        self.data = data
        version, shift, mini = struct.unpack_from('<H', data, 26)[0], struct.unpack_from('<H', data, 30)[0], struct.unpack_from('<H', data, 32)[0]
        if (version, shift) not in ((3, 9), (4, 12)) or mini != 6:
            raise ValueError('지원하지 않는 HWP 저장 구조입니다.')
        self.size = 1 << shift
        self.sectors = len(data) // self.size - 1
        fat_ids = [x for x in struct.unpack_from('<109I', data, 76) if x != FREE]
        sector = u32(data, 68)
        visited = set()
        for _ in range(min(u32(data, 72), self.sectors)):
            if sector in visited:
                raise ValueError('문서의 FAT 목록이 순환합니다.')
            visited.add(sector)
            words = struct.unpack('<' + 'I' * (self.size // 4), self.sector(sector))
            fat_ids.extend(x for x in words[:-1] if x != FREE)
            sector = words[-1]
        if u32(data, 44) > self.sectors or len(fat_ids) < u32(data, 44):
            raise ValueError('문서의 FAT 목록이 손상되었습니다.')
        self.fat = []
        for sid in fat_ids[:u32(data, 44)]:
            self.fat.extend(struct.unpack('<' + 'I' * (self.size // 4), self.sector(sid)))
        directory = self.chain(u32(data, 48), self.fat, self.sector, self.size)
        self.entries = []
        for pos in range(0, len(directory) - 127, 128):
            entry = directory[pos:pos+128]
            n = struct.unpack_from('<H', entry, 64)[0]
            name = entry[:max(0, min(n-2, 64))].decode('utf-16le', 'replace')
            length = struct.unpack_from('<Q', entry, 120)[0]
            if version == 3:
                length &= 0xffffffff
            self.entries.append({'name':name, 'type':entry[66], 'left':u32(entry,68), 'right':u32(entry,72),
                                 'child':u32(entry,76), 'start':u32(entry,116), 'length':length})
        if not self.entries or self.entries[0]['type'] != 5:
            raise ValueError('문서 루트가 없습니다.')
        root = self.entries[0]
        self.mini_stream = self.chain(root['start'], self.fat, self.sector, self.size)[:root['length']] if root['length'] else b''
        mini_fat_data = self.chain(u32(data, 60), self.fat, self.sector, self.size) if u32(data, 64) else b''
        self.mini_fat = list(struct.unpack('<'+'I'*(len(mini_fat_data)//4), mini_fat_data))
        self.paths = {}
        self.walk(root['child'], '', set())

    def sector(self, number):
        if not 0 <= number < self.sectors:
            raise ValueError('문서의 섹터 범위를 벗어났습니다.')
        start = (number + 1) * self.size
        return self.data[start:start+self.size]

    def chain(self, start, fat, reader, chunk):
        result, visited = [], set()
        while start not in (END, FREE):
            if start in visited or not 0 <= start < len(fat):
                raise ValueError('문서 스트림이 순환하거나 손상되었습니다.')
            visited.add(start)
            result.append(reader(start))
            start = fat[start]
        return b''.join(result)

    def walk(self, index, parent, visited):
        # Iterative traversal avoids recursion on malformed directory trees.
        pending = [(index, parent)]
        while pending:
            i, base = pending.pop()
            if i == FREE:
                continue
            if i in visited or i >= len(self.entries):
                raise ValueError('문서 디렉터리가 손상되었습니다.')
            visited.add(i)
            e = self.entries[i]
            path = (base + '/' + e['name']).strip('/')
            if e['type'] == 2:
                self.paths[path] = e
            if e['type'] == 1:
                pending.append((e['child'], path))
            pending.extend([(e['left'], base), (e['right'], base)])

    def read(self, path):
        e = self.paths[path]
        if e['length'] > len(self.data):
            raise ValueError('HWP 내부 스트림 길이가 실제 파일 크기를 초과합니다.')
        if e['length'] < 4096:
            def mini_sector(i):
                if i * 64 + 64 > len(self.mini_stream):
                    raise ValueError('문서의 작은 스트림이 손상되었습니다.')
                return self.mini_stream[i*64:(i+1)*64]
            value = self.chain(e['start'], self.mini_fat, mini_sector, 64)
        else:
            value = self.chain(e['start'], self.fat, self.sector, self.size)
        if len(value) < e['length']:
            raise ValueError('문서 스트림이 잘렸습니다.')
        return value[:e['length']]


def paragraph(data):
    if len(data) % 2:
        raise ValueError('HWP 문단이 손상되었습니다.')
    units = struct.unpack('<'+'H'*(len(data)//2), data)
    out, i = [], 0
    extended = {1,2,3,9,11,12,14,15,16,17,18,19,20,21,22,23}
    while i < len(units):
        c = units[i]
        if c in extended:
            out.append('\t' if c == 9 else ' ')
            i += 8
        else:
            out.append(chr(c) if c >= 32 else '\n' if c in (10,13) else ' ')
            i += 1
    return ''.join(out).encode('utf-16le', 'surrogatepass').decode('utf-16le', 'replace').strip()


def extract_hwp(data):
    cfb = CompoundFile(data)
    if 'FileHeader' not in cfb.paths:
        raise ValueError('HWP 5 FileHeader가 없습니다.')
    header = cfb.read('FileHeader')
    if len(header) < 40 or not header.startswith(b'HWP Document File'):
        raise ValueError('HWP 5 형식만 지원합니다.')
    flags = u32(header, 36)
    if flags & (2 | 4):
        raise ValueError('암호·배포용 HWP는 해제하거나 HWPX/PDF로 변환해 입력하세요.')
    sections = sorted([p for p in cfb.paths if re.fullmatch(r'BodyText/Section\d+', p)],
                      key=lambda p:int(re.search(r'\d+$',p)[0]))
    parts, total = [], 0
    for path in sections:
        content = cfb.read(path)
        if flags & 1:
            dec = zlib.decompressobj(-15)
            try:
                import os
                expanded_limit=max(64*1024*1024,int(len(content)*float(os.environ.get('ACL_MAX_EXPANSION_RATIO','1000'))))
                content = dec.decompress(content, expanded_limit+1)
            except zlib.error as exc:
                raise ValueError('HWP 압축 데이터가 손상되었습니다.') from exc
            if len(content) > expanded_limit or not dec.eof:
                raise ValueError('HWP 압축 데이터가 손상되었거나 크기 제한을 초과했습니다.')
        total += len(content)
        offset = 0
        while offset < len(content):
            if offset + 4 > len(content):
                raise ValueError('HWP 레코드가 잘렸습니다.')
            word = u32(content, offset);offset += 4
            tag, size = word & 0x3ff, word >> 20
            if size == 0xfff:
                if offset + 4 > len(content):raise ValueError('HWP 레코드 길이가 없습니다.')
                size = u32(content, offset);offset += 4
            if offset + size > len(content):raise ValueError('HWP 레코드 본문이 잘렸습니다.')
            if tag == 67:
                text = paragraph(content[offset:offset+size])
                if text:parts.append(text)
            offset += size
    text = '\n'.join(parts)
    if len(text.strip()) < 10:
        raise ValueError('HWP에서 충분한 본문을 찾지 못했습니다. 이미지·구형 문서는 HWPX/PDF로 변환해 입력하세요.')
    return text, ['HWP 5의 본문 텍스트만 추출했습니다. 표 배치·수식·이미지는 원문에서 확인하세요.']
