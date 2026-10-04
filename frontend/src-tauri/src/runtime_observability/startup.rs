//! Coarse startup spans. Durations share one process-local monotonic clock.
use super::RuntimeObserver;
use serde_json::{json, Value};
use std::{sync::atomic::Ordering, sync::Arc, time::Duration};

#[tauri::command]
pub(crate) fn record_startup_milestone(
    window: tauri::WebviewWindow,
    observer: tauri::State<'_, Arc<RuntimeObserver>>,
    milestone: String,
    page_id: String,
    page_elapsed_ms: f64,
) -> Result<(), String> {
    if window.label() != "chat" {
        return Err("startup_milestone_wrong_window".into());
    }
    observer.frontend_milestone(&milestone, &page_id, page_elapsed_ms)
}

pub(crate) struct StartupPhase {
    observer: Arc<RuntimeObserver>,
    phase: &'static str,
    phase_id: u64,
    started: Duration,
    context: Value,
    finished: bool,
}

impl RuntimeObserver {
    fn frontend_milestone(
        &self,
        milestone: &str,
        page_id: &str,
        page_elapsed_ms: f64,
    ) -> Result<(), String> {
        if !matches!(
            milestone,
            "module-loaded"
                | "settings-ready"
                | "root-committed"
                | "models-ready"
                | "restore-ready"
                | "composer-ready"
                | "frontend-ready"
        ) || page_id.len() != 36
            || !page_id
                .bytes()
                .all(|value| value.is_ascii_hexdigit() || value == b'-')
            || !page_elapsed_ms.is_finite()
            || page_elapsed_ms < 0.0
        {
            return Err("startup_milestone_invalid".into());
        }
        let mut seen = self
            .frontend_milestones
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        // A bounded number of reloads; duplicate StrictMode notifications do not multiply logs.
        if seen.len() >= 56 || !seen.insert(format!("{page_id}:{milestone}")) {
            return Ok(());
        }
        self.record(
            "startup",
            "frontend-milestone",
            json!({
                "milestone": milestone, "pageId": page_id, "pageElapsedMs": page_elapsed_ms,
                "elapsedMs": self.started.elapsed().as_secs_f64() * 1000.0,
                "windowLabel": "chat",
            }),
        );
        Ok(())
    }

    pub(crate) fn phase(self: &Arc<Self>, phase: &'static str, context: Value) -> StartupPhase {
        let span = StartupPhase {
            observer: self.clone(),
            phase,
            phase_id: self.next_phase_id.fetch_add(1, Ordering::Relaxed),
            started: self.started.elapsed(),
            context,
            finished: false,
        };
        span.record("phase-started", "running");
        span
    }
}

impl StartupPhase {
    pub(crate) fn finish_with_context(mut self, success: bool, context: Value) {
        self.context = context;
        self.finish(success);
    }

    /// Call only after the observed operation actually returned its result.
    pub(crate) fn finish(mut self, success: bool) {
        self.record("phase-finished", if success { "ok" } else { "error" });
        self.finished = true;
    }

    fn record(&self, kind: &str, status: &str) {
        let elapsed = self.observer.started.elapsed();
        self.observer.record(
            "startup",
            kind,
            json!({
                "phase": self.phase,
                "phaseId": self.phase_id,
                "startElapsedMs": self.started.as_secs_f64() * 1000.0,
                "elapsedMs": elapsed.as_secs_f64() * 1000.0,
                "durationMs": elapsed.saturating_sub(self.started).as_secs_f64() * 1000.0,
                "status": status,
                "context": self.context,
            }),
        );
    }
}

impl Drop for StartupPhase {
    fn drop(&mut self) {
        if !self.finished {
            // Early return / cancellation / unwind is not a successful startup.
            self.record("phase-finished", "incomplete");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frontend_milestones_are_validated_deduplicated_and_bounded() {
        let dir = super::super::tests::test_directory("frontend-startup");
        let observer = RuntimeObserver::start(dir.clone());
        let page = "00000000-0000-0000-0000-000000000000";
        assert!(observer
            .frontend_milestone("private text", page, 1.0)
            .is_err());
        assert!(observer
            .frontend_milestone("module-loaded", "private path", 1.0)
            .is_err());
        assert!(observer
            .frontend_milestone("module-loaded", page, f64::NAN)
            .is_err());
        for _ in 0..2 {
            observer
                .frontend_milestone("module-loaded", page, 2.0)
                .unwrap();
        }
        let records = std::fs::read_to_string(observer.active_path()).unwrap();
        assert_eq!(records.matches("frontend-milestone").count(), 1);
        assert!(!records.contains("private"));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn nested_phases_and_early_returns_keep_distinct_ids_and_statuses() {
        let dir = super::super::tests::test_directory("startup");
        let observer = RuntimeObserver::start(dir.clone());
        let outer = observer.phase("outer", json!({}));
        observer.phase("inner", json!({})).finish(true);
        drop(outer);
        observer.phase("failed", json!({})).finish(false);
        let records: Vec<Value> = std::fs::read_to_string(observer.active_path())
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .filter(|value: &Value| value["source"] == "startup")
            .collect();
        assert_eq!(records.len(), 6);
        assert_eq!(
            records[0]["details"]["phaseId"],
            records[3]["details"]["phaseId"]
        );
        assert_ne!(
            records[0]["details"]["phaseId"],
            records[1]["details"]["phaseId"]
        );
        assert_eq!(records[2]["details"]["status"], "ok");
        assert_eq!(records[3]["details"]["status"], "incomplete");
        assert_eq!(records[5]["details"]["status"], "error");
        for record in &records {
            let details = &record["details"];
            assert!(
                details["elapsedMs"].as_f64().unwrap()
                    >= details["startElapsedMs"].as_f64().unwrap()
            );
            assert!(details["durationMs"].as_f64().unwrap() >= 0.0);
        }
        std::fs::remove_dir_all(dir).unwrap();
    }
}
