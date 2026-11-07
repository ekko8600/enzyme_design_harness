"""Minimal structural checks, not scientific or graphical correctness."""
import math
import shlex
import struct
import xml.etree.ElementTree as ET
import zlib


def image(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        pos=8;header=False;data=False;end=False
        while pos+12<=len(raw):
            size=struct.unpack('>I',raw[pos:pos+4])[0];tag=raw[pos+4:pos+8];finish=pos+12+size
            if finish>len(raw):raise ValueError('PNG_TRUNCATED')
            payload=raw[pos+8:pos+8+size]
            if zlib.crc32(tag+payload)&0xffffffff!=struct.unpack('>I',raw[pos+8+size:finish])[0]:raise ValueError('PNG_CRC_INVALID')
            if tag==b'IHDR':
                if pos!=8 or size!=13 or min(struct.unpack('>II',payload[:8]))<1:raise ValueError('PNG_HEADER_INVALID')
                header=True
            if tag==b'IDAT':data=True
            if tag==b'IEND':end=True;break
            pos=finish
        if not (header and data and end):raise ValueError('PNG_INCOMPLETE')
    elif raw.startswith(b'\xff\xd8\xff'):
        if not raw.endswith(b'\xff\xd9'):raise ValueError('JPEG_INCOMPLETE')
    elif raw.startswith((b'GIF87a',b'GIF89a')):
        if len(raw)<14 or raw[-1:]!=b';':raise ValueError('GIF_INCOMPLETE')
    elif b'<svg' in raw[:1000]:
        if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:raise ValueError('SVG_ENTITY_FORBIDDEN')
        if ET.fromstring(raw).tag.split('}')[-1]!='svg':raise ValueError('SVG_ROOT_INVALID')
    else:raise ValueError('IMAGE_SIGNATURE_INVALID')


def cif(raw):
    text=raw.decode('utf-8');lexer=shlex.shlex(text,posix=True);lexer.whitespace_split=True;lexer.commenters='#';tokens=list(lexer)
    if not any(t.startswith('data_') for t in tokens):raise ValueError('CIF_DATA_BLOCK_MISSING')
    for i,t in enumerate(tokens):
        if t!='loop_':continue
        tags=[];j=i+1
        while j<len(tokens) and tokens[j].startswith('_'):tags.append(tokens[j]);j+=1
        required=['_atom_site.Cartn_x','_atom_site.Cartn_y','_atom_site.Cartn_z']
        if not all(name in tags for name in required):continue
        coords=[tags.index(n) for n in required];rows=0
        while j<len(tokens) and not tokens[j].startswith(('_','data_','loop_','stop_')):
            row=tokens[j:j+len(tags)]
            if len(row)!=len(tags):raise ValueError('CIF_ATOM_ROW_INCOMPLETE')
            if not all(math.isfinite(float(row[c])) for c in coords):raise ValueError('CIF_COORDINATES_NONFINITE')
            rows+=1;j+=len(tags)
        if rows:return
    raise ValueError('CIF_COORDINATE_LOOP_MISSING')
