//! Minimal pure Rust PNG decoder/encoder (8-bit noninterlaced standard PNG), BMP24 and PPM P6.
//! No external libraries or Cargo crates; unsupported PNG variants are rejected explicitly.
use crate::image::{err, err_kind, ErrorKind, Image, SwError, SwResult};
use std::collections::HashMap;
use std::fs;
use std::path::Path;
fn be32(s: &[u8]) -> usize {
    u32::from_be_bytes([s[0], s[1], s[2], s[3]]) as usize
}
fn crc32(bytes: &[u8]) -> u32 {
    let mut c = !0u32;
    for &b in bytes {
        c ^= b as u32;
        for _ in 0..8 {
            c = if c & 1 != 0 {
                (c >> 1) ^ 0xedb88320
            } else {
                c >> 1
            }
        }
    }
    !c
}
fn adler32(bytes: &[u8]) -> u32 {
    let (mut a, mut b) = (1u32, 0u32);
    for chunk in bytes.chunks(5552) {
        for &v in chunk {
            a = (a + v as u32) % 65521;
            b = (b + a) % 65521
        }
    }
    (b << 16) | a
}
struct Bits<'a> {
    src: &'a [u8],
    byte: usize,
    cache: u64,
    n: u32,
}
impl<'a> Bits<'a> {
    fn new(src: &'a [u8]) -> Self {
        Self {
            src,
            byte: 0,
            cache: 0,
            n: 0,
        }
    }
    fn get(&mut self, bits: u32) -> SwResult<usize> {
        if bits > 24 {
            return err("invalid bit count");
        };
        while self.n < bits {
            if self.byte >= self.src.len() {
                return err("truncated deflate stream");
            };
            self.cache |= (self.src[self.byte] as u64) << self.n;
            self.byte += 1;
            self.n += 8;
        }
        let mask = (1u64 << bits) - 1;
        let val = (self.cache & mask) as usize;
        self.cache >>= bits;
        self.n -= bits;
        Ok(val)
    }
    fn byte_align(&mut self) {
        let rem = self.n % 8;
        if rem > 0 {
            self.cache >>= rem;
            self.n -= rem
        }
    }
}
struct Huffman {
    tables: Vec<HashMap<usize, usize>>,
    max: u32,
}
impl Huffman {
    fn from_lengths(lens: &[u8]) -> SwResult<Self> {
        let mut counts = [0usize; 16];
        for &l in lens {
            if l > 15 {
                return err("invalid Huffman length");
            };
            if l > 0 {
                counts[l as usize] += 1
            }
        }
        let mut next = [0usize; 16];
        let mut code = 0usize;
        for i in 1..=15 {
            code = (code + counts[i - 1]) << 1;
            next[i] = code;
        }
        let mut tables = Vec::new();
        for _ in 0..16 {
            tables.push(HashMap::new());
        }
        let mut max = 0;
        for (sym, &len) in lens.iter().enumerate() {
            if len == 0 {
                continue;
            }
            let l = len as usize;
            let mut x = next[l];
            next[l] += 1;
            let mut reverse = 0;
            for _ in 0..l {
                reverse = (reverse << 1) | (x & 1);
                x >>= 1;
            }
            tables[l].insert(reverse, sym);
            max = max.max(l as u32)
        }
        if max == 0 {
            return err("empty Huffman tree");
        };
        Ok(Self { tables, max })
    }
    fn read(&self, bits: &mut Bits<'_>) -> SwResult<usize> {
        let mut code = 0;
        for len in 1..=self.max {
            code |= bits.get(1)? << (len - 1);
            if let Some(&sym) = self.tables[len as usize].get(&code) {
                return Ok(sym);
            }
        }
        err("invalid Huffman code")
    }
}
const LB: [usize; 29] = [
    3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131,
    163, 195, 227, 258,
];
const LE: [u32; 29] = [
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0,
];
const DB: [usize; 30] = [
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537,
    2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577,
];
const DE: [u32; 30] = [
    0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13,
    13,
];
fn inflate_zlib(data: &[u8], max_len: usize) -> SwResult<Vec<u8>> {
    if data.len() < 6 {
        return err("invalid zlib stream");
    }
    if data[0] & 15 != 8 || ((data[0] as u16) * 256 + data[1] as u16) % 31 != 0 {
        return err("invalid zlib header");
    }
    if data[1] & 0x20 != 0 {
        return err_kind(ErrorKind::Unsupported, "zlib dictionary unsupported");
    }
    let mut bits = Bits::new(&data[2..data.len() - 4]);
    let mut out = Vec::new();
    loop {
        let last = bits.get(1)? == 1;
        let kind = bits.get(2)?;
        if kind == 0 {
            bits.byte_align();
            let len = bits.get(16)?;
            let inv = bits.get(16)?;
            if len ^ inv != 65535 {
                return err("invalid deflate stored block");
            };
            for _ in 0..len {
                out.push(bits.get(8)? as u8);
                if out.len() > max_len {
                    return Err(SwError::limit("decompression size limit exceeded"));
                }
            }
        } else if kind == 1 || kind == 2 {
            let (lit, dist) = if kind == 1 {
                let mut lengths = vec![0u8; 288];
                lengths[..144].fill(8);
                lengths[144..256].fill(9);
                lengths[256..280].fill(7);
                lengths[280..288].fill(8);
                (
                    Huffman::from_lengths(&lengths)?,
                    Huffman::from_lengths(&[5u8; 32])?,
                )
            } else {
                let hlit = bits.get(5)? + 257;
                let hdist = bits.get(5)? + 1;
                let hclen = bits.get(4)? + 4;
                let order = [
                    16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15,
                ];
                let mut codelen = [0u8; 19];
                for i in 0..hclen {
                    codelen[order[i]] = bits.get(3)? as u8
                }
                let codes = Huffman::from_lengths(&codelen)?;
                let mut values = Vec::<u8>::new();
                while values.len() < hlit + hdist {
                    let s = codes.read(&mut bits)?;
                    match s {
                        0..=15 => values.push(s as u8),
                        16 => {
                            let prev = *values
                                .last()
                                .ok_or(SwError::input("repeat before initial code"))?;
                            let n = bits.get(2)? + 3;
                            values.extend(std::iter::repeat(prev).take(n))
                        }
                        17 => {
                            let n = bits.get(3)? + 3;
                            values.extend(std::iter::repeat(0).take(n))
                        }
                        18 => {
                            let n = bits.get(7)? + 11;
                            values.extend(std::iter::repeat(0).take(n))
                        }
                        _ => return err("invalid code-length symbol"),
                    };
                    if values.len() > hlit + hdist {
                        return err("too many code lengths");
                    }
                }
                (
                    Huffman::from_lengths(&values[..hlit])?,
                    Huffman::from_lengths(&values[hlit..])?,
                )
            };
            loop {
                let s = lit.read(&mut bits)?;
                if s < 256 {
                    out.push(s as u8)
                } else if s == 256 {
                    break;
                } else if (257..=285).contains(&s) {
                    let idx = s - 257;
                    let len = LB[idx] + bits.get(LE[idx])?;
                    let d = dist.read(&mut bits)?;
                    if d >= 30 {
                        return err("invalid deflate distance code");
                    };
                    let distance = DB[d] + bits.get(DE[d])?;
                    if distance == 0 || distance > out.len() {
                        return err("invalid deflate backreference");
                    };
                    for _ in 0..len {
                        let c = out[out.len() - distance];
                        out.push(c)
                    }
                } else {
                    return err("invalid deflate symbol");
                }
                if out.len() > max_len {
                    return Err(SwError::limit("decompression size limit exceeded"));
                }
            }
        } else {
            return err("reserved deflate block type");
        }
        if last {
            break;
        }
    }
    let adler = be32(&data[data.len() - 4..]);
    if adler32(&out) as usize != adler {
        return err("zlib checksum mismatch");
    };
    Ok(out)
}
fn zlib_store(data: &[u8]) -> Vec<u8> {
    let mut out = vec![0x78, 0x01];
    let blocks = data.chunks(65535).collect::<Vec<_>>();
    for (i, chunk) in blocks.iter().enumerate() {
        out.push(if i + 1 == blocks.len() { 1 } else { 0 });
        let len = chunk.len() as u16;
        out.extend_from_slice(&len.to_le_bytes());
        out.extend_from_slice(&(!len).to_le_bytes());
        out.extend_from_slice(chunk)
    }
    out.extend_from_slice(&adler32(data).to_be_bytes());
    out
}
fn png_chunk(dst: &mut Vec<u8>, label: &[u8; 4], data: &[u8]) {
    dst.extend_from_slice(&(data.len() as u32).to_be_bytes());
    dst.extend_from_slice(label);
    dst.extend_from_slice(data);
    let start = dst.len() - data.len() - 4;
    let checksum = crc32(&dst[start..]);
    dst.extend_from_slice(&checksum.to_be_bytes());
}
pub fn encode_png(img: &Image) -> Vec<u8> {
    let mut out = vec![137, 80, 78, 71, 13, 10, 26, 10];
    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&(img.width as u32).to_be_bytes());
    ihdr.extend_from_slice(&(img.height as u32).to_be_bytes());
    ihdr.extend_from_slice(&[8, 2, 0, 0, 0]);
    png_chunk(&mut out, b"IHDR", &ihdr);
    let mut raw = Vec::with_capacity(img.rgb.len() + img.height);
    for y in 0..img.height {
        raw.push(0);
        raw.extend_from_slice(&img.rgb[y * img.width * 3..(y + 1) * img.width * 3]);
    }
    png_chunk(&mut out, b"IDAT", &zlib_store(&raw));
    png_chunk(&mut out, b"IEND", &[]);
    out
}
fn decode_png(bytes: &[u8]) -> SwResult<Image> {
    if bytes.len() < 33 || &bytes[..8] != b"\x89PNG\r\n\x1a\n" {
        return err("invalid PNG signature");
    }
    let (mut off, mut w, mut h, mut ct, mut depth, mut interlace) = (8, 0, 0, 0, 0, 0);
    let mut idat = Vec::new();
    let mut palette = Vec::new();
    let mut transparent = Vec::new();
    let mut had_ihdr = false;
    while off + 12 <= bytes.len() {
        let len = be32(&bytes[off..off + 4]);
        if off
            .checked_add(12 + len)
            .map_or(true, |end| end > bytes.len())
        {
            return err("bad PNG chunk");
        };
        let t = &bytes[off + 4..off + 8];
        let data = &bytes[off + 8..off + 8 + len];
        let check = be32(&bytes[off + 8 + len..off + 12 + len]);
        if crc32(&bytes[off + 4..off + 8 + len]) != check as u32 {
            return err("PNG CRC mismatch");
        }
        match t {
            b"IHDR" => {
                if len != 13 {
                    return err("invalid PNG IHDR");
                };
                w = be32(&data[0..4]);
                h = be32(&data[4..8]);
                depth = data[8];
                ct = data[9];
                interlace = data[12];
                if data[10] != 0 || data[11] != 0 {
                    return err_kind(
                        ErrorKind::Unsupported,
                        "unsupported PNG compression/filter method",
                    );
                };
                had_ihdr = true
            }
            b"IDAT" => idat.extend_from_slice(data),
            b"PLTE" => palette.extend_from_slice(data),
            b"tRNS" => transparent.extend_from_slice(data),
            b"IEND" => break,
            _ => {}
        }
        off += 12 + len;
    }
    if !had_ihdr || w == 0 || h == 0 || w > 20000 || h > 200000 || depth != 8 || interlace != 0 {
        return err_kind(
            ErrorKind::Unsupported,
            "unsupported PNG: requires 8-bit non-interlaced",
        );
    }
    let bpp = match ct {
        0 => 1,
        2 => 3,
        3 => 1,
        4 => 2,
        6 => 4,
        _ => return err_kind(ErrorKind::Unsupported, "unsupported PNG color type"),
    };
    let stride = w
        .checked_mul(bpp)
        .ok_or_else(|| SwError::limit("PNG row overflow"))?;
    let expect = (stride + 1)
        .checked_mul(h)
        .ok_or_else(|| SwError::limit("PNG size overflow"))?;
    if expect > 900_000_000 {
        return Err(SwError::limit("PNG too large"));
    };
    let raw = inflate_zlib(&idat, expect)?;
    if raw.len() != expect {
        return err("PNG decompressed size mismatch");
    }
    let mut decoded = vec![0u8; stride * h];
    for y in 0..h {
        let filt = raw[y * (stride + 1)];
        for x in 0..stride {
            let current = raw[y * (stride + 1) + 1 + x];
            let left = if x >= bpp {
                decoded[y * stride + x - bpp]
            } else {
                0
            };
            let above = if y > 0 {
                decoded[(y - 1) * stride + x]
            } else {
                0
            };
            let upper_left = if y > 0 && x >= bpp {
                decoded[(y - 1) * stride + x - bpp]
            } else {
                0
            };
            let val = match filt {
                0 => current,
                1 => current.wrapping_add(left),
                2 => current.wrapping_add(above),
                3 => current.wrapping_add(((left as u16 + above as u16) / 2) as u8),
                4 => {
                    let p = left as i32 + above as i32 - upper_left as i32;
                    let a = (p - left as i32).abs();
                    let b = (p - above as i32).abs();
                    let c = (p - upper_left as i32).abs();
                    current.wrapping_add(if a <= b && a <= c {
                        left
                    } else if b <= c {
                        above
                    } else {
                        upper_left
                    })
                }
                _ => return err_kind(ErrorKind::Unsupported, "unsupported PNG row filter"),
            };
            decoded[y * stride + x] = val
        }
    }
    let mut rgb = Vec::with_capacity(w * h * 3);
    for px in decoded.chunks_exact(bpp) {
        let (r, g, b, a) = match ct {
            0 => (px[0], px[0], px[0], 255u8),
            2 => (px[0], px[1], px[2], 255),
            3 => {
                let k = px[0] as usize;
                if (k + 1) * 3 > palette.len() {
                    return err("invalid PNG palette index");
                };
                (
                    palette[k * 3],
                    palette[k * 3 + 1],
                    palette[k * 3 + 2],
                    *transparent.get(k).unwrap_or(&255),
                )
            }
            4 => (px[0], px[0], px[0], px[1]),
            6 => (px[0], px[1], px[2], px[3]),
            _ => unreachable!(),
        };
        for v in [r, g, b] {
            rgb.push(((v as u16 * a as u16 + 255 * (255 - a as u16)) / 255) as u8)
        }
    }
    Image::new(w, h, rgb)
}
fn ppm_tokens(input: &[u8]) -> SwResult<(Vec<String>, usize)> {
    let mut tokens = Vec::new();
    let mut i = 0;
    while tokens.len() < 4 {
        while i < input.len() && input[i].is_ascii_whitespace() {
            i += 1
        }
        if i < input.len() && input[i] == b'#' {
            while i < input.len() && input[i] != b'\n' {
                i += 1
            }
            continue;
        }
        if i >= input.len() {
            return err("truncated PPM header");
        };
        let start = i;
        while i < input.len() && !input[i].is_ascii_whitespace() {
            i += 1
        }
        tokens.push(String::from_utf8_lossy(&input[start..i]).into_owned());
    }
    if i >= input.len() || !input[i].is_ascii_whitespace() {
        return err("invalid PPM separator");
    };
    Ok((tokens, i + 1))
}
fn decode_ppm(src: &[u8]) -> SwResult<Image> {
    let (t, start) = ppm_tokens(src)?;
    if t[0] != "P6" || t[3] != "255" {
        return err_kind(ErrorKind::Unsupported, "only PPM P6 maxval 255 supported");
    };
    let w = t[1]
        .parse::<usize>()
        .map_err(|_| SwError::input("invalid PPM width"))?;
    let h = t[2]
        .parse::<usize>()
        .map_err(|_| SwError::input("invalid PPM height"))?;
    Image::new(w, h, src[start..].to_vec())
}
fn encode_ppm(img: &Image) -> Vec<u8> {
    let mut out = format!("P6\n{} {}\n255\n", img.width, img.height).into_bytes();
    out.extend_from_slice(&img.rgb);
    out
}
fn decode_bmp(data: &[u8]) -> SwResult<Image> {
    if data.len() < 54 || &data[..2] != b"BM" {
        return err("invalid BMP");
    };
    let offset = u32::from_le_bytes(data[10..14].try_into().unwrap()) as usize;
    let w = i32::from_le_bytes(data[18..22].try_into().unwrap());
    let h = i32::from_le_bytes(data[22..26].try_into().unwrap());
    let bpp = u16::from_le_bytes(data[28..30].try_into().unwrap());
    let compression = u32::from_le_bytes(data[30..34].try_into().unwrap());
    if w <= 0 || h == 0 || bpp != 24 || compression != 0 {
        return err("only uncompressed BMP24 supported");
    };
    let ww = w as usize;
    let hh = h.unsigned_abs() as usize;
    if ww > 20000 || hh > 200000 {
        return err("BMP too large");
    };
    let pitch = (ww * 3).div_ceil(4) * 4;
    if offset + pitch * hh > data.len() {
        return err("truncated BMP");
    };
    let mut im = Image::solid(ww, hh, [0, 0, 0])?;
    for y in 0..hh {
        let source_y = if h > 0 { hh - 1 - y } else { y };
        for x in 0..ww {
            let p = offset + source_y * pitch + x * 3;
            im.set(x, y, [data[p + 2], data[p + 1], data[p]])
        }
    }
    Ok(im)
}
fn encode_bmp(img: &Image) -> Vec<u8> {
    let pitch = (img.width * 3).div_ceil(4) * 4;
    let size = 54 + pitch * img.height;
    let mut buf = vec![0u8; size];
    buf[0..2].copy_from_slice(b"BM");
    buf[2..6].copy_from_slice(&(size as u32).to_le_bytes());
    buf[10..14].copy_from_slice(&54u32.to_le_bytes());
    buf[14..18].copy_from_slice(&40u32.to_le_bytes());
    buf[18..22].copy_from_slice(&(img.width as u32).to_le_bytes());
    buf[22..26].copy_from_slice(&(img.height as u32).to_le_bytes());
    buf[26..28].copy_from_slice(&1u16.to_le_bytes());
    buf[28..30].copy_from_slice(&24u16.to_le_bytes());
    for y in 0..img.height {
        for x in 0..img.width {
            let color = img.pixel(x, y);
            let p = 54 + (img.height - 1 - y) * pitch + x * 3;
            buf[p..p + 3].copy_from_slice(&[color[2], color[1], color[0]])
        }
    }
    buf
}
fn io_failure(action: &str, path: &Path, error: std::io::Error) -> SwError {
    let reason = match error.kind() {
        std::io::ErrorKind::NotFound => "file or directory not found".to_string(),
        std::io::ErrorKind::PermissionDenied => "permission denied".to_string(),
        std::io::ErrorKind::AlreadyExists => "already exists".to_string(),
        std::io::ErrorKind::InvalidInput | std::io::ErrorKind::InvalidData => {
            "invalid path or file name".to_string()
        }
        _ => error.to_string(),
    };
    SwError::new(
        ErrorKind::Io,
        format!("{action} '{}': {reason}", path.display()),
    )
}
pub fn read_image(path: impl AsRef<Path>) -> SwResult<Image> {
    let path = path.as_ref();
    let bytes = fs::read(path).map_err(|error| io_failure("cannot read image", path, error))?;
    let decoded = if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        decode_png(&bytes)
    } else if bytes.starts_with(b"P6") {
        decode_ppm(&bytes)
    } else if bytes.starts_with(b"BM") {
        decode_bmp(&bytes)
    } else {
        err_kind(
            ErrorKind::Unsupported,
            "unsupported image format; use PNG/BMP/PPM or decoded raw RGB",
        )
    };
    decoded.map_err(|error| {
        SwError::new(
            error.kind,
            format!(
                "cannot decode image '{}': {}",
                path.display(),
                error.message
            ),
        )
    })
}
pub fn write_image(path: impl AsRef<Path>, img: &Image) -> SwResult<()> {
    let path = path.as_ref();
    let ext = path
        .extension()
        .and_then(|x| x.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let b = match ext.as_str() {
        "png" => encode_png(img),
        "ppm" => encode_ppm(img),
        "bmp" => encode_bmp(img),
        _ => {
            return err_kind(
                ErrorKind::Unsupported,
                "output extension must be png, ppm or bmp",
            )
        }
    };
    fs::write(path, b).map_err(|error| io_failure("cannot write image", path, error))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn png_roundtrip() {
        let mut img = Image::solid(17, 41, [2, 4, 250]).unwrap();
        img.set(2, 7, [255, 0, 99]);
        img.set(16, 40, [0, 128, 42]);
        let dec = decode_png(&encode_png(&img)).unwrap();
        assert_eq!(img, dec)
    }
    #[test]
    fn ppm_bmp_roundtrip() {
        let a = Image::solid(11, 9, [22, 155, 9]).unwrap();
        assert_eq!(a, decode_ppm(&encode_ppm(&a)).unwrap());
        assert_eq!(a, decode_bmp(&encode_bmp(&a)).unwrap())
    }
    #[test]
    fn missing_image_reports_path_and_io_kind() {
        let path = "definitely-missing-scrollweave-image.png";
        let error = read_image(path).unwrap_err();
        assert_eq!(error.kind, ErrorKind::Io);
        assert!(error.message.contains(path), "{}", error.message);
        assert!(error.message.contains("not found"), "{}", error.message);
    }
    #[test]
    fn unsupported_output_extension_is_reported() {
        let image = Image::solid(2, 2, [0, 0, 0]).unwrap();
        let error = write_image("frame.webp", &image).unwrap_err();
        assert_eq!(error.kind, ErrorKind::Unsupported);
        assert!(error.message.contains("png, ppm or bmp"));
    }
}
