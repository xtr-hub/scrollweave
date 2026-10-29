//! Verification and expected-repair planning. Planning reads only a compact
//! snapshot of an existing layered result; it never touches input images.
use crate::evidence::Evidence;
use crate::layered::LayeredResult;
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Goal {
    Verification,
    Repair,
}

#[derive(Clone, Debug)]
pub struct ViewSuggestion {
    pub scroll_position: i32,
    pub estimated_gain: f64,
    pub target_pixels: usize,
    pub predicted_clean_fraction: f64,
}

#[derive(Clone, Debug)]
pub struct PlannerOptions {
    pub goal: Goal,
    pub count: usize,
    pub step: usize,
    pub minimum_separation: i32,
    pub candidates: Option<Vec<i32>>,
    pub document_height: Option<usize>,
}

impl Default for PlannerOptions {
    fn default() -> Self {
        Self {
            goal: Goal::Repair,
            count: 3,
            step: 32,
            minimum_separation: 48,
            candidates: None,
            document_height: None,
        }
    }
}

/// The minimal state required to plan views without retaining frame images.
#[derive(Clone, Debug)]
pub struct PlannerInput {
    pub width: usize,
    pub height: usize,
    pub viewport: (usize, usize),
    pub positions: Vec<i32>,
    pub scroll_offset: i32,
    pub screen_mask_width: usize,
    pub screen_mask_height: usize,
    pub screen_mask_rgb: Vec<u8>,
    pub estimated_ui: Vec<u8>,
    pub evidence: Evidence,
    pub source_ids: Vec<i32>,
}

impl From<&LayeredResult> for PlannerInput {
    fn from(result: &LayeredResult) -> Self {
        Self {
            width: result.image.width,
            height: result.image.height,
            viewport: result.viewport,
            positions: result.positions.clone(),
            scroll_offset: result.scroll_offset,
            screen_mask_width: result.screen_mask.width,
            screen_mask_height: result.screen_mask.height,
            screen_mask_rgb: result.screen_mask.rgb.clone(),
            estimated_ui: result.estimated_ui.clone(),
            evidence: result.evidence.clone(),
            source_ids: result.source_ids.clone(),
        }
    }
}

impl LayeredResult {
    /// Snapshot the planning state without cloning output image pixels.
    pub fn planner_input(&self) -> PlannerInput {
        PlannerInput::from(self)
    }
}

fn screen_risk(input: &PlannerInput) -> Vec<f32> {
    let width = input.width;
    let (top, bottom) = input.viewport;
    let height = bottom - top;
    let mut groups: BTreeMap<i32, Vec<usize>> = BTreeMap::new();
    for (index, &position) in input.positions.iter().enumerate() {
        groups.entry(position).or_default().push(index);
    }
    let mask_area = input.screen_mask_width * input.screen_mask_height;
    let mut risk = vec![0f32; height * width];
    for y in 0..height {
        for x in 0..width {
            let screen_index = (y + top) * width + x;
            let ui = input.screen_mask_rgb[screen_index * 3] as f32 / 255.;
            let mut votes = 0f32;
            for members in groups.values() {
                let group = members
                    .iter()
                    .map(|&index| input.estimated_ui[index * mask_area + screen_index] as f32)
                    .sum::<f32>()
                    / members.len() as f32;
                votes += group;
            }
            let mean = if groups.is_empty() {
                0.
            } else {
                votes / groups.len() as f32
            };
            risk[y * width + x] = ui.max(mean).min(1.);
        }
    }
    risk
}

pub fn plan(input: &PlannerInput, options: &PlannerOptions) -> Vec<ViewSuggestion> {
    let width = input.width;
    let document_height = input.height;
    let view_height = input.viewport.1 - input.viewport.0;
    let height = options.document_height.unwrap_or(document_height);
    if height < document_height
        || height < view_height
        || height.saturating_mul(width) > 180_000_000
        || options.count == 0
        || options.step == 0
    {
        return vec![];
    }

    let mut field = vec![
        if options.goal == Goal::Verification {
            1.5f32
        } else {
            0.
        };
        width * height
    ];

    for (index, value) in field.iter_mut().enumerate().take(width * document_height) {
        let evidence = &input.evidence;
        *value = if options.goal == Goal::Verification {
            let base = evidence.unverified[index] as f32 + evidence.conflict[index] as f32 * 0.5;
            if evidence.unverified[index] > 0 {
                base
            } else {
                base * 0.2
            }
        } else {
            let mut risk = 0.08
                + 0.52 * evidence.unverified[index] as f32
                + 0.23 * evidence.conflict[index] as f32;
            if evidence.views[index] == 0 {
                risk += 0.07;
            }
            if evidence.views[index] > 0 && evidence.support[index] == 0 {
                risk += 0.11;
            }
            if evidence.invalid[index] != 0 {
                risk = 0.9;
            }
            let source = input.source_ids[index];
            if source >= 0 {
                let position = input.positions[source as usize] - input.scroll_offset;
                let screen_y = (index / width) as i64 - position as i64 + input.viewport.0 as i64;
                if screen_y >= input.viewport.0 as i64 && screen_y < input.viewport.1 as i64 {
                    let source_index =
                        source as usize * input.screen_mask_width * input.screen_mask_height
                            + screen_y as usize * width
                            + index % width;
                    if input.estimated_ui[source_index] != 0 {
                        risk += 0.50;
                    }
                    let rail = input.screen_mask_rgb
                        [(screen_y as usize * width + index % width) * 3]
                        as f32
                        / 255.;
                    risk += 0.20 * rail;
                    let margin = (screen_y as usize - input.viewport.0)
                        .min(input.viewport.1 - 1 - screen_y as usize);
                    risk += 0.12 * (1. - margin as f32 / 35.).max(0.);
                }
            }
            risk.min(1.7)
        };
    }

    let clean = screen_risk(input);
    let mut candidates = if let Some(ref values) = options.candidates {
        values
            .iter()
            .map(|&value| value - input.scroll_offset)
            .collect::<Vec<i32>>()
    } else {
        let mut generated = (0..=height - view_height)
            .step_by(options.step)
            .map(|value| value as i32)
            .collect::<Vec<_>>();
        generated.push((height - view_height) as i32);
        generated
    };
    candidates.sort_unstable();
    candidates.dedup();
    candidates.retain(|&candidate| candidate >= 0 && candidate as usize <= height - view_height);

    let positions = input
        .positions
        .iter()
        .map(|&position| position - input.scroll_offset)
        .collect::<Vec<i32>>();
    let mut suggestions = Vec::new();
    for _ in 0..options.count {
        let mut best: Option<(f64, i32, usize)> = None;
        for &candidate in &candidates {
            if positions
                .iter()
                .any(|&position| (position - candidate).abs() < options.minimum_separation)
            {
                continue;
            }
            if suggestions.iter().any(|suggestion: &ViewSuggestion| {
                (suggestion.scroll_position - input.scroll_offset - candidate).abs()
                    < options.minimum_separation
            }) {
                continue;
            }
            let mut gain = 0f64;
            let mut target = 0;
            for y in 0..view_height {
                for x in 0..width {
                    let value = field[(candidate as usize + y) * width + x];
                    let clean_fraction = 1. - clean[y * width + x];
                    gain += value as f64 * clean_fraction.max(0.) as f64;
                    if value
                        > (if options.goal == Goal::Repair {
                            0.45
                        } else {
                            0.
                        })
                    {
                        target += 1;
                    }
                }
            }
            let better = match best.as_ref() {
                None => true,
                Some(current) => gain > current.0,
            };
            if better {
                best = Some((gain, candidate, target));
            }
        }
        let Some((gain, candidate, target)) = best else {
            break;
        };
        if gain <= 0. {
            break;
        }
        let clean_fraction = 1. - clean.iter().sum::<f32>() / clean.len() as f32;
        suggestions.push(ViewSuggestion {
            scroll_position: candidate + input.scroll_offset,
            estimated_gain: gain,
            target_pixels: target,
            predicted_clean_fraction: clean_fraction as f64,
        });
        let factor = if options.goal == Goal::Verification {
            0.85
        } else {
            0.75
        };
        for y in 0..view_height {
            for x in 0..width {
                let index = (candidate as usize + y) * width + x;
                field[index] *= 1. - factor * (1. - clean[y * width + x]);
            }
        }
    }
    suggestions
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_repair() {
        let options = PlannerOptions::default();
        assert_eq!(options.goal, Goal::Repair);
    }
}
