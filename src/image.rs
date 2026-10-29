use std::fmt::{self, Display};

/// Stable error categories for programmatic handling by Rust callers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    /// Invalid caller input or argument combination.
    Input,
    /// Requested format/operation is not supported.
    Unsupported,
    /// A documented size/dimension/count limit was exceeded.
    Limit,
    /// Filesystem or I/O failure.
    Io,
    /// Internal invariant or panic converted at the FFI boundary.
    Internal,
    /// Uncategorized error retained for compatibility.
    Other,
}

/// Error with a category and human-readable message.
#[derive(Debug, Clone)]
pub struct SwError {
    /// Machine-readable category.
    pub kind: ErrorKind,
    /// Human-readable detail; this is what Python receives.
    pub message: String,
}
impl SwError {
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }
    pub fn input(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Input, message)
    }
    pub fn unsupported(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Unsupported, message)
    }
    pub fn limit(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Limit, message)
    }
    pub fn internal(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Internal, message)
    }
}
impl Display for SwError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}
impl std::error::Error for SwError {}
impl From<std::io::Error> for SwError {
    fn from(e: std::io::Error) -> Self {
        Self::new(ErrorKind::Io, e.to_string())
    }
}
pub type SwResult<T> = Result<T, SwError>;
pub fn err<T>(s: &str) -> SwResult<T> {
    Err(SwError::input(s))
}
pub fn err_kind<T>(kind: ErrorKind, s: &str) -> SwResult<T> {
    Err(SwError::new(kind, s))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Image {
    pub width: usize,
    pub height: usize,
    pub rgb: Vec<u8>,
}
impl Image {
    pub fn new(width: usize, height: usize, rgb: Vec<u8>) -> SwResult<Self> {
        let n = width
            .checked_mul(height)
            .and_then(|v| v.checked_mul(3))
            .ok_or_else(|| SwError::limit("image dimensions overflow"))?;
        if width == 0 || height == 0 || n > 900_000_000 || rgb.len() != n {
            return err("invalid image dimensions/buffer length");
        }
        Ok(Self { width, height, rgb })
    }
    pub fn solid(w: usize, h: usize, color: [u8; 3]) -> SwResult<Self> {
        let n = w
            .checked_mul(h)
            .and_then(|v| v.checked_mul(3))
            .ok_or_else(|| SwError::limit("image dimensions overflow"))?;
        if n > 900_000_000 || n == 0 {
            return err("invalid canvas size");
        }
        let mut v = vec![0; n];
        for p in v.chunks_exact_mut(3) {
            p.copy_from_slice(&color)
        }
        Self::new(w, h, v)
    }
    #[inline]
    pub fn pixel(&self, x: usize, y: usize) -> [u8; 3] {
        let p = (y * self.width + x) * 3;
        [self.rgb[p], self.rgb[p + 1], self.rgb[p + 2]]
    }
    #[inline]
    pub fn set(&mut self, x: usize, y: usize, c: [u8; 3]) {
        let p = (y * self.width + x) * 3;
        self.rgb[p..p + 3].copy_from_slice(&c)
    }
    pub fn crop(&self, x: usize, y: usize, w: usize, h: usize) -> SwResult<Self> {
        if x.checked_add(w).map_or(true, |a| a > self.width)
            || y.checked_add(h).map_or(true, |a| a > self.height)
        {
            return err("crop out of bounds");
        }
        let mut v = Vec::with_capacity(w * h * 3);
        for yy in y..y + h {
            let offset = (yy * self.width + x) * 3;
            v.extend_from_slice(&self.rgb[offset..offset + w * 3]);
        }
        Self::new(w, h, v)
    }
    pub fn blit(&mut self, src: &Image, x: usize, y: usize) -> SwResult<()> {
        if x + src.width > self.width || y + src.height > self.height {
            return err("blit out of bounds");
        }
        for yy in 0..src.height {
            let dst = ((y + yy) * self.width + x) * 3;
            let s = yy * src.width * 3;
            self.rgb[dst..dst + src.width * 3].copy_from_slice(&src.rgb[s..s + src.width * 3]);
        }
        Ok(())
    }
}
