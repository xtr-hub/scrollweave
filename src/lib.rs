//! ScrollWeave: no third-party Cargo crates. No image enhancement or invented pixels.
//! Supported: PNG (8-bit, non-interlaced), BMP 24-bit, PPM P6, raw RGB.
//! Stable entry points are re-exported at the crate root; module paths may change
//! before 1.0.
pub mod align;
pub mod codec;
pub mod compose;
pub mod evidence;
pub mod feature;
pub mod geometry;
pub mod graph;
pub mod graphcut;
pub mod image;
pub mod layered;
pub mod planner;
pub mod stitch;
pub mod visibility;

pub use codec::{read_image, write_image};
pub use compose::{compose, ComposeOptions, Layout};
pub use evidence::{audit, Evidence};
pub use geometry::{infer_viewport, GeometryMode};
pub use image::{ErrorKind, Image, SwError, SwResult};
pub use layered::{
    reconstruct as reconstruct_layered, LayeredOptions, LayeredResult, Segmentation,
};
pub use planner::{plan, Goal, PlannerOptions, ViewSuggestion};
pub use stitch::{stitch_basic, BasicOptions, StitchDiagnostics, StitchResult};
