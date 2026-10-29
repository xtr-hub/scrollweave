//! Optional binary UI/document Potts graph cut, downsampled screen coordinates.
//! Explicitly experimental: this segmentation did not beat the temporal path in research.
use crate::image::Image;
use std::collections::VecDeque;
#[derive(Clone, Copy)]
struct Edge {
    to: usize,
    rev: usize,
    cap: i32,
}
struct Dinic {
    adj: Vec<Vec<Edge>>,
    level: Vec<i32>,
    progress: Vec<usize>,
}
impl Dinic {
    fn new(n: usize) -> Self {
        Self {
            adj: vec![vec![]; n],
            level: vec![-1; n],
            progress: vec![0; n],
        }
    }
    fn edge(&mut self, a: usize, b: usize, cap: i32) {
        let ri = self.adj[b].len();
        let ai = self.adj[a].len();
        self.adj[a].push(Edge {
            to: b,
            rev: ri,
            cap,
        });
        self.adj[b].push(Edge {
            to: a,
            rev: ai,
            cap: 0,
        });
    }
    fn levels(&mut self, s: usize, t: usize) -> bool {
        self.level.fill(-1);
        self.level[s] = 0;
        let mut queue = VecDeque::new();
        queue.push_back(s);
        while let Some(v) = queue.pop_front() {
            for e in &self.adj[v] {
                if e.cap > 0 && self.level[e.to] < 0 {
                    self.level[e.to] = self.level[v] + 1;
                    queue.push_back(e.to)
                }
            }
        }
        self.level[t] >= 0
    }
    fn push(&mut self, v: usize, t: usize, amount: i32) -> i32 {
        if v == t {
            return amount;
        }
        while self.progress[v] < self.adj[v].len() {
            let j = self.progress[v];
            let e = self.adj[v][j];
            if e.cap > 0 && self.level[e.to] == self.level[v] + 1 {
                let sent = self.push(e.to, t, amount.min(e.cap));
                if sent > 0 {
                    self.adj[v][j].cap -= sent;
                    self.adj[e.to][e.rev].cap += sent;
                    return sent;
                }
            }
            self.progress[v] += 1;
        }
        0
    }
    fn run(&mut self, s: usize, t: usize) {
        while self.levels(s, t) {
            self.progress.fill(0);
            loop {
                if self.push(s, t, i32::MAX / 4) == 0 {
                    break;
                }
            }
        }
    }
    fn reachable(&self, s: usize) -> Vec<bool> {
        let mut found = vec![false; self.adj.len()];
        let mut queue = VecDeque::new();
        found[s] = true;
        queue.push_back(s);
        while let Some(u) = queue.pop_front() {
            for e in &self.adj[u] {
                if e.cap > 0 && !found[e.to] {
                    found[e.to] = true;
                    queue.push_back(e.to)
                }
            }
        }
        found
    }
}
/// `rate`: suspicious observations / testable observations at each screen pixel.
/// `count`: number of independently testable evidence samples, not duplicated votes.
pub fn segment(rate: &[f32], count: &[u32], image: &Image, step: usize) -> Vec<u8> {
    let (w, h) = (image.width, image.height);
    let step = step.max(1);
    let sw = w.div_ceil(step);
    let sh = h.div_ceil(step);
    let total = sw * sh;
    let s = total;
    let t = total + 1;
    let mut graph = Dinic::new(total + 2);
    for sy in 0..sh {
        for sx in 0..sw {
            let node = sy * sw + sx;
            let x = (sx * step).min(w - 1);
            let y = (sy * step).min(h - 1);
            let x0 = x.saturating_sub(2);
            let y0 = y.saturating_sub(2);
            let x1 = (x + 3).min(w);
            let y1 = (y + 3).min(h);
            let mut rr = 0f32;
            let mut support = 0f32;
            let mut n = 0f32;
            for yy in y0..y1 {
                for xx in x0..x1 {
                    let k = yy * w + xx;
                    rr += rate[k];
                    support += count[k] as f32;
                    n += 1.
                }
            }
            let rr = rr / n;
            let sup = support / n;
            let signal = if sup >= 1.75 {
                (rr - 0.47) * 120.
            } else {
                -84.
            };
            let du = (-signal).max(0.) as i32;
            let dd = signal.max(0.) as i32;
            // Source-reachable label corresponds to UI hypothesis.
            graph.edge(s, node, dd);
            graph.edge(node, t, du);
            if sx > 0 {
                let a = image.pixel(x, y);
                let b = image.pixel(x - step.min(x), y);
                let d = (0..3).map(|c| a[c].abs_diff(b[c]) as f32).sum::<f32>() / 3.;
                let weight = (20. * (-d / 22.).exp()).round().max(1.) as i32;
                graph.edge(node, node - 1, weight);
                graph.edge(node - 1, node, weight)
            }
            if sy > 0 {
                let a = image.pixel(x, y);
                let b = image.pixel(x, y - step.min(y));
                let d = (0..3).map(|c| a[c].abs_diff(b[c]) as f32).sum::<f32>() / 3.;
                let weight = (20. * (-d / 22.).exp()).round().max(1.) as i32;
                graph.edge(node, node - sw, weight);
                graph.edge(node - sw, node, weight)
            }
        }
    }
    graph.run(s, t);
    let reachable = graph.reachable(s);
    let mut mask = vec![0u8; w * h];
    for y in 0..h {
        for x in 0..w {
            mask[y * w + x] = u8::from(reachable[(y / step) * sw + x / step])
        }
    }
    mask
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn zero_evidence_doc() {
        let im = Image::solid(32, 32, [3, 4, 5]).unwrap();
        let m = segment(&vec![0.; 1024], &vec![0; 1024], &im, 3);
        assert!(m.iter().all(|&x| x == 0));
    }
}
