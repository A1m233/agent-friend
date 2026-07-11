use serde::Serialize;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MonitorSnapshot {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub scale_milli: i32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverlayBounds {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DisplayTopologySnapshot {
    pub monitors: Vec<MonitorSnapshot>,
    pub bounds: OverlayBounds,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DisplayTopologyChangedPayload {
    pub reason: String,
    pub generation: u64,
    pub bounds: OverlayBounds,
    pub previous_bounds: Option<OverlayBounds>,
    pub previous_monitors: Option<Vec<MonitorSnapshot>>,
    pub monitors: Vec<MonitorSnapshot>,
}

#[derive(Default)]
pub struct TopologyEpochState {
    last_topology: Option<DisplayTopologySnapshot>,
    generation: u64,
}

pub struct TopologyEpochDecision {
    pub topology_changed: bool,
    pub generation: Option<u64>,
    pub previous_topology: Option<DisplayTopologySnapshot>,
}

impl TopologyEpochState {
    pub fn accept(
        &mut self,
        snapshot: &DisplayTopologySnapshot,
        force: bool,
    ) -> TopologyEpochDecision {
        let previous_topology = self.last_topology.clone();
        let topology_changed = force || self.last_topology.as_ref() != Some(snapshot);
        let generation = if topology_changed {
            self.generation = self.generation.saturating_add(1);
            self.last_topology = Some(snapshot.clone());
            Some(self.generation)
        } else {
            None
        };

        TopologyEpochDecision {
            topology_changed,
            generation,
            previous_topology,
        }
    }
}

pub fn scale_to_milli(scale_factor: f64) -> i32 {
    if !scale_factor.is_finite() || scale_factor <= 0.0 {
        return 0;
    }
    (scale_factor * 1000.0).round() as i32
}

pub fn display_topology_snapshot(
    mut monitors: Vec<MonitorSnapshot>,
) -> Option<DisplayTopologySnapshot> {
    monitors.retain(|monitor| monitor.width > 0 && monitor.height > 0);
    if monitors.is_empty() {
        return None;
    }
    monitors.sort_by_key(|monitor| {
        (
            monitor.x,
            monitor.y,
            monitor.width,
            monitor.height,
            monitor.scale_milli,
        )
    });

    let min_x = monitors.iter().map(|monitor| monitor.x).min().unwrap_or(0);
    let min_y = monitors.iter().map(|monitor| monitor.y).min().unwrap_or(0);
    let max_x = monitors
        .iter()
        .map(|monitor| monitor.x + monitor.width as i32)
        .max()
        .unwrap_or(min_x + 1);
    let max_y = monitors
        .iter()
        .map(|monitor| monitor.y + monitor.height as i32)
        .max()
        .unwrap_or(min_y + 1);

    Some(DisplayTopologySnapshot {
        monitors,
        bounds: OverlayBounds {
            x: min_x,
            y: min_y,
            width: (max_x - min_x).max(1) as u32,
            height: (max_y - min_y).max(1) as u32,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn monitor(x: i32, y: i32, width: u32, height: u32, scale_milli: i32) -> MonitorSnapshot {
        MonitorSnapshot {
            x,
            y,
            width,
            height,
            scale_milli,
        }
    }

    #[test]
    fn computes_side_by_side_union_bounds() {
        let snapshot = display_topology_snapshot(vec![
            monitor(1920, 0, 2560, 1440, 1250),
            monitor(0, 0, 1920, 1080, 1500),
        ])
        .expect("snapshot");

        assert_eq!(
            snapshot.bounds,
            OverlayBounds {
                x: 0,
                y: 0,
                width: 4480,
                height: 1440
            }
        );
        assert_eq!(snapshot.monitors[0].x, 0);
        assert_eq!(snapshot.monitors[1].x, 1920);
    }

    #[test]
    fn includes_negative_monitor_origins() {
        let snapshot = display_topology_snapshot(vec![
            monitor(-2560, 0, 2560, 1440, 1000),
            monitor(0, 360, 1920, 1080, 1000),
        ])
        .expect("snapshot");

        assert_eq!(
            snapshot.bounds,
            OverlayBounds {
                x: -2560,
                y: 0,
                width: 4480,
                height: 1440
            }
        );
    }

    #[test]
    fn scale_factor_changes_are_part_of_the_signature() {
        let base = display_topology_snapshot(vec![monitor(0, 0, 1920, 1080, 1000)]).expect("base");
        let scaled =
            display_topology_snapshot(vec![monitor(0, 0, 1920, 1080, 1250)]).expect("scaled");

        assert_ne!(base, scaled);
        assert_eq!(base.bounds, scaled.bounds);
    }

    #[test]
    fn resolution_changes_update_bounds() {
        let base = display_topology_snapshot(vec![monitor(0, 0, 1920, 1080, 1000)]).expect("base");
        let resized =
            display_topology_snapshot(vec![monitor(0, 0, 2560, 1440, 1000)]).expect("resized");

        assert_ne!(base, resized);
        assert_eq!(resized.bounds.width, 2560);
        assert_eq!(resized.bounds.height, 1440);
    }

    #[test]
    fn filters_unusable_monitors() {
        let snapshot = display_topology_snapshot(vec![
            monitor(0, 0, 0, 1080, 1000),
            monitor(100, 100, 800, 600, 1000),
        ])
        .expect("snapshot");

        assert_eq!(snapshot.monitors.len(), 1);
        assert_eq!(
            snapshot.bounds,
            OverlayBounds {
                x: 100,
                y: 100,
                width: 800,
                height: 600
            }
        );
    }

    #[test]
    fn rejects_empty_topology() {
        assert!(display_topology_snapshot(vec![]).is_none());
        assert!(display_topology_snapshot(vec![monitor(0, 0, 0, 0, 1000)]).is_none());
    }

    #[test]
    fn topology_epoch_advances_only_when_signature_changes() {
        let base = display_topology_snapshot(vec![monitor(0, 0, 1920, 1080, 1000)]).expect("base");
        let same = display_topology_snapshot(vec![monitor(0, 0, 1920, 1080, 1000)]).expect("same");
        let scaled =
            display_topology_snapshot(vec![monitor(0, 0, 1920, 1080, 1250)]).expect("scaled");
        let mut state = TopologyEpochState::default();

        let initial = state.accept(&base, true);
        assert!(initial.topology_changed);
        assert_eq!(initial.generation, Some(1));
        assert!(initial.previous_topology.is_none());

        let duplicate = state.accept(&same, false);
        assert!(!duplicate.topology_changed);
        assert_eq!(duplicate.generation, None);
        assert_eq!(duplicate.previous_topology, Some(base.clone()));

        let changed = state.accept(&scaled, false);
        assert!(changed.topology_changed);
        assert_eq!(changed.generation, Some(2));
        assert_eq!(changed.previous_topology, Some(base));
    }
}
