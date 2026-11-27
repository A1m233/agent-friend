//! Opt-in synthetic measurement through the production collector and ZIP writer.
use super::*;
use std::{io::BufWriter, time::Instant};

mod baseline {
    use super::nonce;
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/../../docs/requirements/184-diagnostic-bundle-governance/artifacts/design-probes/baseline.rs"));
}
fn nonce() -> u128 {
    rand::random()
}

#[test]
#[ignore = "synthetic 128 MiB measurement; scripts/diagnostics-smoke --native"]
fn measure_log_collection_and_archive() {
    let root = std::env::temp_dir().join(format!("af-export-perf-{:032x}", rand::random::<u128>()));
    fs::create_dir_all(&root).unwrap();
    let path = root.join("runtime-bridge.log");
    let line = b"2026-11-28 INFO synthetic poll response normal and credential=test-secret-value\n";
    {
        let mut writer = BufWriter::new(File::create(&path).unwrap());
        let rows = (128 * 1024 * 1024) / line.len();
        for _ in 0..rows {
            writer.write_all(line).unwrap();
        }
        writer.flush().unwrap();
    }
    let mut observations = Vec::new();
    let start = Instant::now();
    let baseline =
        baseline::StagedLog::collect(&path, &["test-secret-value".into()], None).unwrap();
    let baseline_ms = start.elapsed().as_secs_f64() * 1000.0;
    let baseline_output_bytes = baseline.included_bytes;
    drop(baseline);
    for all in [false, true, true, false] {
        let control = job::Control::detached();
        control.stage("logs").unwrap();
        let start = Instant::now();
        let logs = collect_logs_selected(&root, &["test-secret-value".into()], None, all, &control);
        assert!(logs.failures.is_empty());
        let collection_ms = start.elapsed().as_secs_f64() * 1000.0;
        let bytes: u64 = logs.items.iter().map(|item| item.read_bytes).sum();
        if !all {
            assert!(bytes <= selection::FAMILY_BYTES + 1);
        }
        let staged_bytes: usize = logs.items.iter().map(|item| item.included_bytes).sum();
        if all {
            assert_eq!(staged_bytes, baseline_output_bytes);
        }
        control.stage("archive").unwrap();
        let start = Instant::now();
        let zip = write_archive_controlled(&root, "measurement", logs.entries, &control).unwrap();
        let archive_ms = start.elapsed().as_secs_f64() * 1000.0;
        let zip_bytes = zip.metadata().unwrap().len();
        let mut archive = zip::ZipArchive::new(File::open(&zip).unwrap()).unwrap();
        let mut file = archive.by_index(0).unwrap();
        let mut sample = [0_u8; 512];
        let count = file.read(&mut sample).unwrap();
        assert!(!String::from_utf8_lossy(&sample[..count]).contains("test-secret-value"));
        drop(file);
        drop(archive);
        observations.push(json!({"allRetained":all,"sourceBytes":path.metadata().unwrap().len(),"readBytes":bytes,"stagedBytes":staged_bytes,"zipBytes":zip_bytes,"collectMs":collection_ms,"archiveMs":archive_ms}));
        fs::remove_file(zip).unwrap();
    }
    fs::remove_file(path).unwrap();
    fs::remove_dir(paths::diagnostic_archive_work_dir(&root)).unwrap();
    fs::remove_dir(root).unwrap();
    println!(
        "AF_DIAGNOSTIC_PERF {}",
        json!({"platform":std::env::consts::OS,"buildProfile":if cfg!(debug_assertions) {"debug"} else {"release"},"synthetic":true,"baselineCollectMs":baseline_ms,"baselineOutputBytes":baseline_output_bytes,"observations":observations})
    );
}
