//! Stable C ABI for the stdlib-only Python ctypes client.
//! All heap allocations crossing the boundary are owned and freed by Rust.
use scrollweave_core::image::{SwError, SwResult};
use scrollweave_core::{
    compose, read_image, stitch_basic, write_image, BasicOptions, ComposeOptions, Image, Layout,
};
use std::ffi::{c_char, c_void, CStr};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::ptr;

#[repr(C)]
pub struct SwRgbRef {
    pub width: u32,
    pub height: u32,
    pub data: *const u8,
    pub len: usize,
}
#[repr(C)]
pub struct SwRgbOwned {
    pub width: u32,
    pub height: u32,
    pub data: *mut u8,
    pub len: usize,
}
impl Default for SwRgbOwned {
    fn default() -> Self {
        Self {
            width: 0,
            height: 0,
            data: ptr::null_mut(),
            len: 0,
        }
    }
}
#[repr(C)]
pub struct SwStitchConfig {
    pub level: u8,
    pub auto_trim: u8,
    pub diagnostics: u8,
    pub reserved: u8, // level is reserved; basic uses robust matching
    pub viewport_top: i32,
    pub viewport_bottom: i32,
    pub known_positions: *const i32, // [x0,y0,x1,y1,...] or NULL
    pub min_overlap: f64,
    pub min_match_score: f64,
}
#[repr(C)]
#[derive(Default)]
pub struct SwStats {
    pub viewport_top: usize,
    pub viewport_bottom: usize,
    pub graph_edges: usize,
    pub graph_rejected: usize,
    pub graph_residual: f64,
    pub independent_support_ratio: f64,
    pub unknown_ratio: f64,
}
#[repr(C)]
pub struct SwStitchResponse {
    pub image: SwRgbOwned,
    pub coverage: SwRgbOwned,
    pub confidence: SwRgbOwned,
    pub provenance: SwRgbOwned,
    pub screen_mask: SwRgbOwned,
    pub positions: *mut i32, // has 2 * positions_len elements; i32::MIN = unplaced
    pub positions_len: usize,
    pub stats: SwStats,
}

impl Default for SwStitchResponse {
    fn default() -> Self {
        Self {
            image: SwRgbOwned::default(),
            coverage: SwRgbOwned::default(),
            confidence: SwRgbOwned::default(),
            provenance: SwRgbOwned::default(),
            screen_mask: SwRgbOwned::default(),
            positions: ptr::null_mut(),
            positions_len: 0,
            stats: SwStats::default(),
        }
    }
}

fn owned(im: Image) -> SwRgbOwned {
    let width = im.width as u32;
    let height = im.height as u32;
    let mut data = im.rgb.into_boxed_slice();
    let len = data.len();
    let ptr = data.as_mut_ptr();
    std::mem::forget(data);
    SwRgbOwned {
        width,
        height,
        data: ptr,
        len,
    }
}
unsafe fn free_owned(im: &mut SwRgbOwned) {
    if !im.data.is_null() {
        let raw = ptr::slice_from_raw_parts_mut(im.data, im.len);
        drop(Box::from_raw(raw));
    }
    *im = SwRgbOwned::default();
}
fn panic_to_error(panic: Box<dyn std::any::Any + Send>) -> String {
    if let Some(s) = panic.downcast_ref::<&str>() {
        format!("Rust panic: {s}")
    } else if let Some(s) = panic.downcast_ref::<String>() {
        format!("Rust panic: {s}")
    } else {
        "Rust panic inside ScrollWeave".into()
    }
}
unsafe fn write_error(buffer: *mut c_char, cap: usize, msg: &str) {
    if buffer.is_null() || cap == 0 {
        return;
    }
    let b = msg.as_bytes();
    let count = b.len().min(cap - 1);
    ptr::copy_nonoverlapping(b.as_ptr(), buffer.cast::<u8>(), count);
    *buffer.add(count) = 0;
}
fn run<F>(buf: *mut c_char, cap: usize, f: F) -> i32
where
    F: FnOnce() -> SwResult<()>,
{
    let result = catch_unwind(AssertUnwindSafe(f));
    let problem = match result {
        Ok(Ok(())) => return 0,
        Ok(Err(e)) => e.to_string(),
        Err(panic) => panic_to_error(panic),
    };
    unsafe { write_error(buf, cap, &problem) };
    -1
}
unsafe fn path_arg<'a>(path: *const c_char) -> SwResult<&'a Path> {
    if path.is_null() {
        return Err(SwError::input("path pointer is NULL"));
    }
    let s = CStr::from_ptr(path)
        .to_str()
        .map_err(|_| SwError::input("path must be valid UTF-8"))?;
    Ok(Path::new(s))
}
unsafe fn ref_image(value: &SwRgbRef) -> SwResult<Image> {
    let size = (value.width as usize)
        .checked_mul(value.height as usize)
        .and_then(|v| v.checked_mul(3))
        .ok_or_else(|| SwError::limit("RGB dimension overflow"))?;
    if size == 0 || size > 900_000_000 || size != value.len || value.data.is_null() {
        return Err(SwError::input(
            "invalid RGB image pointer, dimensions or length",
        ));
    }
    Image::new(
        value.width as usize,
        value.height as usize,
        std::slice::from_raw_parts(value.data, value.len).to_vec(),
    )
}
unsafe fn input_images(ptr: *const SwRgbRef, n: usize) -> SwResult<Vec<Image>> {
    if ptr.is_null() || n == 0 || n > 128 {
        return Err(SwError::limit("expected 1..128 images"));
    }
    std::slice::from_raw_parts(ptr, n)
        .iter()
        .map(|im| ref_image(im))
        .collect()
}

#[no_mangle]
pub extern "C" fn sw_api_version() -> u32 {
    4
}

#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_free_image(im: *mut SwRgbOwned) {
    if !im.is_null() {
        free_owned(&mut *im)
    }
}
#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_free_stitch_response(res: *mut SwStitchResponse) {
    if res.is_null() {
        return;
    }
    let r = &mut *res;
    for im in [
        &mut r.image,
        &mut r.coverage,
        &mut r.confidence,
        &mut r.provenance,
        &mut r.screen_mask,
    ] {
        free_owned(im)
    }
    if !r.positions.is_null() {
        let b = ptr::slice_from_raw_parts_mut(r.positions, r.positions_len.saturating_mul(2));
        drop(Box::from_raw(b));
    }
    r.positions = ptr::null_mut();
    r.positions_len = 0;
}
#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_read_image(
    path: *const c_char,
    out: *mut SwRgbOwned,
    err: *mut c_char,
    cap: usize,
) -> i32 {
    run(err, cap, || {
        if out.is_null() {
            return Err(SwError::input("output pointer is NULL"));
        }
        let im = read_image(path_arg(path)?)?;
        *out = owned(im);
        Ok(())
    })
}
#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_write_image(
    im: *const SwRgbRef,
    path: *const c_char,
    err: *mut c_char,
    cap: usize,
) -> i32 {
    run(err, cap, || {
        if im.is_null() {
            return Err(SwError::input("image pointer is NULL"));
        }
        let value = ref_image(&*im)?;
        write_image(path_arg(path)?, &value)
    })
}
#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_compose_rgbs(
    inputs: *const SwRgbRef,
    n: usize,
    layout: u32,
    columns: usize,
    gap: usize,
    r: u8,
    g: u8,
    b: u8,
    out: *mut SwRgbOwned,
    err: *mut c_char,
    cap: usize,
) -> i32 {
    run(err, cap, || {
        if out.is_null() {
            return Err(SwError::input("output pointer is NULL"));
        }
        let images = input_images(inputs, n)?;
        let lay = match layout {
            0 => Layout::Vertical,
            1 => Layout::Horizontal,
            2 => Layout::Grid { columns },
            3 => Layout::Overlay,
            _ => return Err(SwError::unsupported("invalid layout")),
        };
        let result = compose(
            &images,
            lay,
            ComposeOptions {
                gap,
                background: [r, g, b],
            },
        )?;
        *out = owned(result);
        Ok(())
    })
}
#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_stitch_rgbs(
    inputs: *const SwRgbRef,
    n: usize,
    config: *const SwStitchConfig,
    out: *mut SwStitchResponse,
    err: *mut c_char,
    cap: usize,
) -> i32 {
    run(err, cap, || {
        if out.is_null() || config.is_null() {
            return Err(SwError::input("output/config pointer is NULL"));
        }
        let images = input_images(inputs, n)?;
        let cfg = &*config;
        let mut options = BasicOptions {
            auto_trim: cfg.auto_trim != 0,
            diagnostics: cfg.diagnostics != 0,
            min_overlap: cfg.min_overlap,
            min_match_score: cfg.min_match_score,
            ..BasicOptions::default()
        };
        if cfg.viewport_top >= 0 || cfg.viewport_bottom >= 0 {
            if cfg.viewport_top < 0 || cfg.viewport_bottom < 0 {
                return Err(SwError::input("viewport bounds must both be set"));
            }
            options.viewport = Some((cfg.viewport_top as usize, cfg.viewport_bottom as usize));
        }
        if !cfg.known_positions.is_null() {
            let pairs = std::slice::from_raw_parts(cfg.known_positions, n * 2);
            options.known_positions = Some(pairs.chunks_exact(2).map(|p| (p[0], p[1])).collect());
        }
        let result = stitch_basic(&images, &options)?;
        let mut positions = Vec::<i32>::with_capacity(n * 2);
        for p in result.positions.iter() {
            let (x, y) = p.unwrap_or((i32::MIN, i32::MIN));
            positions.push(x);
            positions.push(y)
        }
        let positions_len = n;
        let mut boxed_positions = positions.into_boxed_slice();
        let positions_ptr = boxed_positions.as_mut_ptr();
        std::mem::forget(boxed_positions);
        let (mut coverage, mut confidence, mut provenance, mut screen_mask) = (
            SwRgbOwned::default(),
            SwRgbOwned::default(),
            SwRgbOwned::default(),
            SwRgbOwned::default(),
        );
        let mut independent_support_ratio = 0.0;
        let mut unknown_ratio = 0.0;
        if let Some(d) = result.diagnostics {
            coverage = owned(d.coverage);
            confidence = owned(d.confidence);
            provenance = owned(d.provenance);
            screen_mask = owned(d.screen_mask);
            independent_support_ratio = d.independent_support_ratio;
            unknown_ratio = d.unknown_ratio;
        }
        *out = SwStitchResponse {
            image: owned(result.image),
            coverage,
            confidence,
            provenance,
            screen_mask,
            positions: positions_ptr,
            positions_len,
            stats: SwStats {
                viewport_top: result.viewport.0,
                viewport_bottom: result.viewport.1,
                graph_edges: result.graph_edges,
                graph_rejected: result.graph_rejected,
                graph_residual: result.graph_residual,
                independent_support_ratio,
                unknown_ratio,
            },
        };
        Ok(())
    })
}

// Layered reconstruction and planning ABI. The original C ABI functions
// remain available unchanged for image load/save, compose and basic stitching.
use scrollweave_core::layered::{self, LayeredOptions, Segmentation};
use scrollweave_core::planner::{self, Goal, PlannerInput, PlannerOptions};

#[repr(C)]
pub struct SwMaskRef {
    pub data: *const u8,
    pub len: usize,
}

#[repr(C)]
pub struct SwLayeredConfig {
    pub positions: *const i32,
    pub viewport_top: i32,
    pub viewport_bottom: i32,
    pub appearance: u8,
    pub segmentation: u8,
    pub include_transients: u8,
    pub calibrate_prior: u8,
    pub appearance_threshold: f32,
    pub texture_strength: f32,
    pub recurrent_mask: SwMaskRef,
    pub flat_mask: SwMaskRef,
    pub training_indices: *const u32,
    pub training_count: usize,
}

#[repr(C)]
pub struct SwSliceI32 {
    pub data: *mut i32,
    pub len: usize,
}

impl Default for SwSliceI32 {
    fn default() -> Self {
        Self {
            data: ptr::null_mut(),
            len: 0,
        }
    }
}

#[repr(C)]
pub struct SwSliceU16 {
    pub data: *mut u16,
    pub len: usize,
}

impl Default for SwSliceU16 {
    fn default() -> Self {
        Self {
            data: ptr::null_mut(),
            len: 0,
        }
    }
}

#[repr(C)]
pub struct SwSliceU8 {
    pub data: *mut u8,
    pub len: usize,
}

impl Default for SwSliceU8 {
    fn default() -> Self {
        Self {
            data: ptr::null_mut(),
            len: 0,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct SwSuggestedView {
    pub scroll_position: i32,
    pub estimated_gain: f64,
    pub target_pixels: usize,
    pub predicted_clean_fraction: f64,
    pub goal: u32,
}

#[repr(C)]
pub struct SwSliceViews {
    pub data: *mut SwSuggestedView,
    pub len: usize,
}

impl Default for SwSliceViews {
    fn default() -> Self {
        Self {
            data: ptr::null_mut(),
            len: 0,
        }
    }
}

#[repr(C)]
pub struct SwLayeredResponse {
    pub image: SwRgbOwned,
    pub coverage: SwRgbOwned,
    pub confidence: SwRgbOwned,
    pub provenance: SwRgbOwned,
    pub screen_mask: SwRgbOwned,
    pub source_ids: SwSliceI32,
    pub independent_support: SwSliceU16,
    pub independent_views: SwSliceU16,
    pub conflict: SwSliceU8,
    pub unverified: SwSliceU8,
    pub invalid_source: SwSliceU8,
    pub ui_mask: SwSliceU8,
    pub ui_probability: SwSliceU8,
    pub rejected_prior_rows: SwSliceU8,
    pub viewport_top: usize,
    pub viewport_bottom: usize,
    pub geometry_mode: u32,
    /// Opaque `PlannerInput`; free with `sw_layered_free_plan`.
    pub handle: *mut c_void,
}

impl Default for SwLayeredResponse {
    fn default() -> Self {
        Self {
            image: SwRgbOwned::default(),
            coverage: SwRgbOwned::default(),
            confidence: SwRgbOwned::default(),
            provenance: SwRgbOwned::default(),
            screen_mask: SwRgbOwned::default(),
            source_ids: SwSliceI32::default(),
            independent_support: SwSliceU16::default(),
            independent_views: SwSliceU16::default(),
            conflict: SwSliceU8::default(),
            unverified: SwSliceU8::default(),
            invalid_source: SwSliceU8::default(),
            ui_mask: SwSliceU8::default(),
            ui_probability: SwSliceU8::default(),
            rejected_prior_rows: SwSliceU8::default(),
            viewport_top: 0,
            viewport_bottom: 0,
            geometry_mode: 0,
            handle: ptr::null_mut(),
        }
    }
}

#[repr(C)]
pub struct SwPlanConfig {
    pub goal: u32,
    pub count: usize,
    pub step: usize,
    pub minimum_separation: i32,
    pub candidates: *const i32,
    pub candidate_count: usize,
    pub document_height: usize,
}

fn own_i32(values: Vec<i32>) -> SwSliceI32 {
    let mut boxed = values.into_boxed_slice();
    let len = boxed.len();
    let data = boxed.as_mut_ptr();
    std::mem::forget(boxed);
    SwSliceI32 { data, len }
}

fn own_u16(values: Vec<u16>) -> SwSliceU16 {
    let mut boxed = values.into_boxed_slice();
    let len = boxed.len();
    let data = boxed.as_mut_ptr();
    std::mem::forget(boxed);
    SwSliceU16 { data, len }
}

fn own_u8(values: Vec<u8>) -> SwSliceU8 {
    let mut boxed = values.into_boxed_slice();
    let len = boxed.len();
    let data = boxed.as_mut_ptr();
    std::mem::forget(boxed);
    SwSliceU8 { data, len }
}

fn own_views(values: Vec<SwSuggestedView>) -> SwSliceViews {
    let mut boxed = values.into_boxed_slice();
    let len = boxed.len();
    let data = boxed.as_mut_ptr();
    std::mem::forget(boxed);
    SwSliceViews { data, len }
}

unsafe fn free_i32(value: &mut SwSliceI32) {
    if !value.data.is_null() {
        drop(Box::from_raw(ptr::slice_from_raw_parts_mut(
            value.data, value.len,
        )));
    }
    *value = SwSliceI32::default();
}

unsafe fn free_u16(value: &mut SwSliceU16) {
    if !value.data.is_null() {
        drop(Box::from_raw(ptr::slice_from_raw_parts_mut(
            value.data, value.len,
        )));
    }
    *value = SwSliceU16::default();
}

unsafe fn free_u8(value: &mut SwSliceU8) {
    if !value.data.is_null() {
        drop(Box::from_raw(ptr::slice_from_raw_parts_mut(
            value.data, value.len,
        )));
    }
    *value = SwSliceU8::default();
}

unsafe fn free_views(value: &mut SwSliceViews) {
    if !value.data.is_null() {
        drop(Box::from_raw(ptr::slice_from_raw_parts_mut(
            value.data, value.len,
        )));
    }
    *value = SwSliceViews::default();
}

#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_layered_free_response(response: *mut SwLayeredResponse) {
    if response.is_null() {
        return;
    }
    let response = &mut *response;
    for image in [
        &mut response.image,
        &mut response.coverage,
        &mut response.confidence,
        &mut response.provenance,
        &mut response.screen_mask,
    ] {
        free_owned(image);
    }
    free_i32(&mut response.source_ids);
    free_u16(&mut response.independent_support);
    free_u16(&mut response.independent_views);
    for array in [
        &mut response.conflict,
        &mut response.unverified,
        &mut response.invalid_source,
        &mut response.ui_mask,
        &mut response.ui_probability,
        &mut response.rejected_prior_rows,
    ] {
        free_u8(array);
    }
}

#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_layered_free_plan(handle: *mut c_void) {
    if !handle.is_null() {
        drop(Box::from_raw(handle as *mut PlannerInput));
    }
}

#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_layered_free_views(views: *mut SwSliceViews) {
    if !views.is_null() {
        free_views(&mut *views);
    }
}

unsafe fn mask_arg(value: &SwMaskRef, expected: usize) -> SwResult<Option<Vec<u8>>> {
    if value.data.is_null() {
        if value.len != 0 {
            return Err(SwError::input("null UI mask with nonzero length"));
        }
        return Ok(None);
    }
    if value.len != expected {
        return Err(SwError::input(
            "UI mask size must be frame_count*height*width",
        ));
    }
    Ok(Some(
        std::slice::from_raw_parts(value.data, value.len).to_vec(),
    ))
}

#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_layered_reconstruct_rgbs(
    inputs: *const SwRgbRef,
    n: usize,
    config: *const SwLayeredConfig,
    out: *mut SwLayeredResponse,
    error: *mut c_char,
    cap: usize,
) -> i32 {
    run(error, cap, || {
        if config.is_null() || out.is_null() {
            return Err(SwError::input("null config/response pointer"));
        }
        let images = input_images(inputs, n)?;
        let config = &*config;
        if config.positions.is_null() {
            return Err(SwError::input(
                "layered reconstruction requires caller-supplied scroll positions",
            ));
        }
        let positions = std::slice::from_raw_parts(config.positions, n).to_vec();
        let viewport = if config.viewport_top < 0 && config.viewport_bottom < 0 {
            None
        } else {
            if config.viewport_top < 0 || config.viewport_bottom < 0 {
                return Err(SwError::input("viewport must have both bounds"));
            }
            Some((
                config.viewport_top as usize,
                config.viewport_bottom as usize,
            ))
        };
        let segmentation = match config.segmentation {
            0 => Segmentation::Temporal,
            1 => Segmentation::GraphCut,
            _ => return Err(SwError::input("segmentation must be temporal or graphcut")),
        };
        let length = n
            .checked_mul(images[0].width)
            .and_then(|value| value.checked_mul(images[0].height))
            .ok_or_else(|| SwError::limit("mask size overflow"))?;
        if config.training_count > n {
            return Err(SwError::limit("too many training indices"));
        }
        let training = if config.training_indices.is_null() {
            None
        } else {
            Some(
                std::slice::from_raw_parts(config.training_indices, config.training_count)
                    .iter()
                    .map(|&value| value as usize)
                    .collect::<Vec<_>>(),
            )
        };
        let options = LayeredOptions {
            viewport,
            training_indices: training,
            appearance: config.appearance != 0,
            appearance_threshold: config.appearance_threshold,
            texture_strength: config.texture_strength,
            include_transients: config.include_transients != 0,
            segmentation,
            calibrate_prior: config.calibrate_prior != 0,
            recurrent_mask: mask_arg(&config.recurrent_mask, length)?,
            flat_mask: mask_arg(&config.flat_mask, length)?,
        };
        let layered = layered::reconstruct(&images, &positions, &options)?;
        let handle = Box::into_raw(Box::new(layered.planner_input())) as *mut c_void;
        let mode = match layered.geometry_mode {
            scrollweave_core::geometry::GeometryMode::StableBorders => 1,
            scrollweave_core::geometry::GeometryMode::ActivityEnvelope => 2,
            scrollweave_core::geometry::GeometryMode::InsufficientMotion => 0,
        };
        *out = SwLayeredResponse {
            image: owned(layered.image),
            coverage: owned(layered.coverage),
            confidence: owned(layered.confidence),
            provenance: owned(layered.provenance),
            screen_mask: owned(layered.screen_mask),
            source_ids: own_i32(layered.source_ids),
            independent_support: own_u16(layered.evidence.support),
            independent_views: own_u16(layered.evidence.views),
            conflict: own_u8(layered.evidence.conflict),
            unverified: own_u8(layered.evidence.unverified),
            invalid_source: own_u8(layered.evidence.invalid),
            ui_mask: own_u8(layered.estimated_ui),
            ui_probability: own_u8(layered.ui_probability),
            rejected_prior_rows: own_u8(layered.rejected_prior_rows),
            viewport_top: layered.viewport.0,
            viewport_bottom: layered.viewport.1,
            geometry_mode: mode,
            handle,
        };
        Ok(())
    })
}

#[no_mangle]
/// # Safety
/// The caller must pass valid pointers with the documented ownership/lifetime.
pub unsafe extern "C" fn sw_layered_plan(
    handle: *mut c_void,
    config: *const SwPlanConfig,
    out: *mut SwSliceViews,
    error: *mut c_char,
    cap: usize,
) -> i32 {
    run(error, cap, || {
        if handle.is_null() || config.is_null() || out.is_null() {
            return Err(SwError::input("null plan handle/config/output pointer"));
        }
        let config = &*config;
        let goal = match config.goal {
            0 => Goal::Verification,
            1 => Goal::Repair,
            _ => {
                return Err(SwError::input(
                    "planning goal must be verification or repair",
                ))
            }
        };
        if config.candidate_count > 8192 {
            return Err(SwError::limit("candidate count too large"));
        }
        let candidates = if config.candidates.is_null() {
            None
        } else {
            Some(std::slice::from_raw_parts(config.candidates, config.candidate_count).to_vec())
        };
        let options = PlannerOptions {
            goal,
            count: config.count.min(32),
            step: config.step.max(1),
            minimum_separation: config.minimum_separation.max(0),
            candidates,
            document_height: if config.document_height == 0 {
                None
            } else {
                Some(config.document_height)
            },
        };
        let input = &*(handle as *const PlannerInput);
        let views = planner::plan(input, &options)
            .into_iter()
            .map(|view| SwSuggestedView {
                scroll_position: view.scroll_position,
                estimated_gain: view.estimated_gain,
                target_pixels: view.target_pixels,
                predicted_clean_fraction: view.predicted_clean_fraction,
                goal: if goal == Goal::Repair { 1 } else { 0 },
            })
            .collect();
        *out = own_views(views);
        Ok(())
    })
}
