use crate::align::Match;
#[derive(Clone, Debug)]
pub struct Edge {
    pub a: usize,
    pub b: usize,
    pub dx: i32,
    pub dy: i32,
    pub confidence: f64,
    pub score: f64,
}
impl Edge {
    pub fn new(a: usize, b: usize, m: &Match) -> Self {
        Self {
            a,
            b,
            dx: m.dx,
            dy: m.dy,
            confidence: m.confidence,
            score: m.score,
        }
    }
}
#[derive(Clone, Debug)]
pub struct PoseGraph {
    pub positions: Vec<Option<(i32, i32)>>,
    pub accepted: Vec<Edge>,
    pub rejected: Vec<Edge>,
    pub components: usize,
    pub residual: f64,
}
struct Dsu {
    p: Vec<usize>,
}
impl Dsu {
    fn new(n: usize) -> Self {
        Self {
            p: (0..n).collect(),
        }
    }
    fn root(&mut self, a: usize) -> usize {
        let x = self.p[a];
        if x == a {
            a
        } else {
            let r = self.root(x);
            self.p[a] = r;
            r
        }
    }
    fn join(&mut self, a: usize, b: usize) -> bool {
        let x = self.root(a);
        let y = self.root(b);
        if x == y {
            false
        } else {
            self.p[x] = y;
            true
        }
    }
}
fn component_count(n: usize, edges: &[Edge]) -> usize {
    let mut ds = Dsu::new(n);
    for e in edges {
        ds.join(e.a, e.b);
    }
    let mut roots = std::collections::HashSet::new();
    for i in 0..n {
        roots.insert(ds.root(i));
    }
    roots.len()
}
fn weighted_median(mut pairs: Vec<(i32, f64)>) -> i32 {
    if pairs.is_empty() {
        return 0;
    }
    pairs.sort_by_key(|v| v.0);
    let total: f64 = pairs.iter().map(|v| v.1).sum();
    let mut a = 0.;
    for (p, w) in pairs {
        a += w;
        if a >= total / 2. {
            return p;
        }
    }
    0
}
/// A connected core is initialized from verified edges; redundant edges then vote
/// on positions by robust weighted medians. A single inconsistent cycle cannot
/// silently drag all downstream nodes as in naive chain accumulation.
pub fn solve(n: usize, mut edges: Vec<Edge>) -> PoseGraph {
    edges.sort_by(|a, b| b.confidence.total_cmp(&a.confidence));
    let mut dsu = Dsu::new(n);
    let mut tree = Vec::new();
    for e in &edges {
        if e.a < n && e.b < n && dsu.join(e.a, e.b) {
            tree.push(e.clone())
        }
    }
    let mut adj = vec![Vec::<(usize, i32, i32)>::new(); n];
    for e in &tree {
        adj[e.a].push((e.b, e.dx, e.dy));
        adj[e.b].push((e.a, -e.dx, -e.dy));
    }
    let mut positions = vec![None; n];
    for start in 0..n {
        if positions[start].is_some() {
            continue;
        }
        positions[start] = Some((0, 0));
        let mut q = vec![start];
        while let Some(a) = q.pop() {
            let (x, y) = positions[a].unwrap();
            for &(b, dx, dy) in &adj[a] {
                if positions[b].is_none() {
                    positions[b] = Some((x + dx, y + dy));
                    q.push(b)
                }
            }
        }
    }
    let mut good = Vec::new();
    let mut bad = Vec::new();
    for e in edges {
        let a = positions[e.a].unwrap();
        let b = positions[e.b].unwrap();
        let residual = (b.0 - a.0 - e.dx).abs().max((b.1 - a.1 - e.dy).abs());
        if residual <= 9 || tree.iter().any(|t| t.a == e.a && t.b == e.b) {
            good.push(e)
        } else {
            bad.push(e)
        }
    }
    let components = component_count(n, &good);
    // Only optimize edges that are consistent with seed; low-confidence outliers are excluded.
    for _ in 0..10 {
        let old = positions.clone();
        for (node, position) in positions.iter_mut().enumerate().take(n) {
            let mut xs = Vec::new();
            let mut ys = Vec::new();
            if node == 0 {
                continue;
            }
            for e in &good {
                let (neighbor, dx, dy) = if e.a == node {
                    (e.b, -e.dx, -e.dy)
                } else if e.b == node {
                    (e.a, e.dx, e.dy)
                } else {
                    continue;
                };
                if let Some((x, y)) = old[neighbor] {
                    let weight = e.confidence.max(0.001);
                    xs.push((x + dx, weight));
                    ys.push((y + dy, weight))
                }
            }
            if !xs.is_empty() {
                *position = Some((weighted_median(xs), weighted_median(ys)))
            }
        }
    }
    let mut sum = 0.;
    let mut nerr = 0;
    for e in &good {
        let a = positions[e.a].unwrap();
        let b = positions[e.b].unwrap();
        sum += ((b.0 - a.0 - e.dx).abs() + (b.1 - a.1 - e.dy).abs()) as f64;
        nerr += 1
    }
    PoseGraph {
        positions,
        accepted: good,
        rejected: bad,
        components,
        residual: sum / (2 * nerr.max(1)) as f64,
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn graph_cycles() {
        let m = |dx, dy| Match {
            dx,
            dy,
            score: 0.99,
            overlap: 0.5,
            confidence: 0.99,
        };
        let mut v = vec![
            Edge::new(0, 1, &m(0, 90)),
            Edge::new(1, 2, &m(0, 110)),
            Edge::new(0, 2, &m(0, 200)),
        ];
        v.push(Edge::new(
            0,
            2,
            &Match {
                dx: 0,
                dy: 700,
                score: 0.5,
                overlap: 0.3,
                confidence: 0.1,
            },
        ));
        let g = solve(3, v);
        assert_eq!(g.positions[2].unwrap().1, 200);
        assert!(!g.rejected.is_empty())
    }
}
